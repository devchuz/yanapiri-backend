-- SRSI: condiciones nutricionales, indicaciones de suplementos, tomas reportadas
-- y consentimiento para recordatorios. Ejecutar completo en Supabase SQL Editor.
-- La service_role del bot inserta reportes; RLS limita la lectura y conciliación clínica.

create table if not exists public.professional_profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  full_name text not null,
  profession text not null,
  license_number text,
  verified boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.child_conditions (
  id uuid primary key default gen_random_uuid(),
  child_id uuid not null references public.children(id) on delete cascade,
  condition_code text,
  condition_name text not null check (char_length(condition_name) between 3 and 160),
  diagnosed_at date,
  diagnosing_professional_id uuid references auth.users(id) on delete set null,
  diagnosed_by_name text,
  health_center_id uuid references public.health_centers(id) on delete set null,
  reported_health_center text,
  verification_status text not null default 'reported' check (verification_status in ('reported','verified','rejected')),
  condition_status text not null default 'active' check (condition_status in ('active','resolved','inactive')),
  source_system text not null default 'caregiver' check (source_system in ('caregiver','clinical_app','import','external')),
  external_record_id text,
  reported_by_identity text,
  verified_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (verification_status <> 'verified' or (diagnosing_professional_id is not null and verified_at is not null)),
  unique (source_system, external_record_id)
);

create table if not exists public.supplement_plans (
  id uuid primary key default gen_random_uuid(),
  child_id uuid not null references public.children(id) on delete cascade,
  condition_id uuid references public.child_conditions(id) on delete set null,
  supplement_type text not null check (supplement_type in ('iron','mnp','vitamin_a','zinc','vitamin_d','other')),
  purpose text not null default 'unknown' check (purpose in ('preventive','therapeutic','unknown')),
  start_date date not null default current_date,
  end_date date,
  schedule_text text check (schedule_text is null or char_length(schedule_text) <= 300),
  indicating_professional_id uuid references auth.users(id) on delete set null,
  indicated_by_name text,
  health_center_id uuid references public.health_centers(id) on delete set null,
  reported_health_center text,
  verification_status text not null default 'reported' check (verification_status in ('reported','verified','rejected')),
  status text not null default 'active' check (status in ('active','paused','completed','stopped')),
  source_system text not null default 'caregiver' check (source_system in ('caregiver','clinical_app','import','external')),
  external_record_id text,
  verified_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (end_date is null or end_date >= start_date),
  check (verification_status <> 'verified' or (indicating_professional_id is not null and verified_at is not null)),
  unique (source_system, external_record_id)
);

create table if not exists public.supplement_intake_events (
  id uuid primary key default gen_random_uuid(),
  plan_id uuid not null references public.supplement_plans(id) on delete cascade,
  scheduled_for date not null,
  intake_status text not null check (intake_status in ('taken','not_taken','pending')),
  reason_code text check (reason_code is null or reason_code in ('forgot','out_of_stock','child_refused','discomfort','instructions_unclear','other')),
  notes text check (notes is null or char_length(notes) <= 500),
  reported_by_identity text,
  reported_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  unique (plan_id, scheduled_for)
);

create table if not exists public.supplement_reminder_preferences (
  id uuid primary key default gen_random_uuid(),
  plan_id uuid not null unique references public.supplement_plans(id) on delete cascade,
  whatsapp_identity text not null,
  enabled boolean not null default true,
  reminder_time time not null default '08:00',
  timezone text not null default 'America/Lima',
  consented_at timestamptz,
  updated_at timestamptz not null default now()
);

create index if not exists idx_conditions_child_status on public.child_conditions(child_id, condition_status);
create index if not exists idx_supplement_plans_child_status on public.supplement_plans(child_id, status);
create index if not exists idx_supplement_intake_plan_date on public.supplement_intake_events(plan_id, scheduled_for desc);
create index if not exists idx_supplement_reminders_due on public.supplement_reminder_preferences(enabled, reminder_time);

drop view if exists public.v_supplement_followup;
create view public.v_supplement_followup
with (security_invoker = true) as
select
  sp.id as plan_id,
  c.id as child_id,
  c.full_name as child_name,
  c.health_center_id,
  cg.full_name as caregiver_name,
  cg.phone_number as caregiver_phone,
  sp.supplement_type,
  sp.purpose,
  sp.verification_status,
  sp.status as plan_status,
  sp.indicated_by_name,
  sp.indicating_professional_id,
  sp.start_date,
  latest.scheduled_for as last_intake_date,
  latest.intake_status as last_intake_status,
  latest.reason_code as last_reason_code,
  coalesce(summary.taken_7d, 0) as taken_7d,
  coalesce(summary.not_taken_7d, 0) as not_taken_7d,
  coalesce(summary.pending_7d, 0) as pending_7d
from public.supplement_plans sp
join public.children c on c.id = sp.child_id and c.active = true
join public.caregivers cg on cg.id = c.caregiver_id
left join lateral (
  select sie.scheduled_for, sie.intake_status, sie.reason_code
  from public.supplement_intake_events sie
  where sie.plan_id = sp.id
  order by sie.scheduled_for desc, sie.reported_at desc limit 1
) latest on true
left join lateral (
  select
    count(*) filter (where sie.intake_status = 'taken') as taken_7d,
    count(*) filter (where sie.intake_status = 'not_taken') as not_taken_7d,
    count(*) filter (where sie.intake_status = 'pending') as pending_7d
  from public.supplement_intake_events sie
  where sie.plan_id = sp.id and sie.scheduled_for >= current_date - 6
) summary on true
where sp.status = 'active';

alter table public.professional_profiles enable row level security;
alter table public.child_conditions enable row level security;
alter table public.supplement_plans enable row level security;
alter table public.supplement_intake_events enable row level security;
alter table public.supplement_reminder_preferences enable row level security;

drop policy if exists "professionals see profiles" on public.professional_profiles;
create policy "professionals see profiles" on public.professional_profiles for select to authenticated
using (user_id = auth.uid() or exists (
  select 1 from public.health_center_members viewer
  join public.health_center_members subject on subject.health_center_id = viewer.health_center_id
  where viewer.user_id = auth.uid() and subject.user_id = user_id
) or exists (
  select 1 from public.health_center_members admin
  where admin.user_id = auth.uid() and admin.role = 'admin'
));

drop policy if exists "members see child conditions" on public.child_conditions;
create policy "members see child conditions" on public.child_conditions for select to authenticated using (exists (
  select 1 from public.children c join public.health_center_members hcm on hcm.health_center_id = c.health_center_id
  where c.id = child_id and hcm.user_id = auth.uid()
));
drop policy if exists "members manage child conditions" on public.child_conditions;
create policy "members manage child conditions" on public.child_conditions for update to authenticated using (exists (
  select 1 from public.children c join public.health_center_members hcm on hcm.health_center_id = c.health_center_id
  where c.id = child_id and hcm.user_id = auth.uid()
)) with check ((diagnosing_professional_id is null or diagnosing_professional_id = auth.uid()) and exists (
  select 1 from public.children c join public.health_center_members hcm on hcm.health_center_id = c.health_center_id
  where c.id = child_id and hcm.user_id = auth.uid()
));
drop policy if exists "members create child conditions" on public.child_conditions;
create policy "members create child conditions" on public.child_conditions for insert to authenticated with check (
  diagnosing_professional_id = auth.uid() and verification_status = 'verified' and verified_at is not null
  and exists (select 1 from public.children c join public.health_center_members hcm on hcm.health_center_id = c.health_center_id
    where c.id = child_id and hcm.user_id = auth.uid())
);
drop policy if exists "admins manage all child conditions" on public.child_conditions;
create policy "admins manage all child conditions" on public.child_conditions for all to authenticated
using (exists (select 1 from public.health_center_members hcm where hcm.user_id = auth.uid() and hcm.role = 'admin'))
with check (exists (select 1 from public.health_center_members hcm where hcm.user_id = auth.uid() and hcm.role = 'admin'));

drop policy if exists "members see supplement plans" on public.supplement_plans;
create policy "members see supplement plans" on public.supplement_plans for select to authenticated using (exists (
  select 1 from public.children c join public.health_center_members hcm on hcm.health_center_id = c.health_center_id
  where c.id = child_id and hcm.user_id = auth.uid()
));
drop policy if exists "members manage supplement plans" on public.supplement_plans;
create policy "members manage supplement plans" on public.supplement_plans for update to authenticated using (exists (
  select 1 from public.children c join public.health_center_members hcm on hcm.health_center_id = c.health_center_id
  where c.id = child_id and hcm.user_id = auth.uid()
)) with check ((indicating_professional_id is null or indicating_professional_id = auth.uid()) and exists (
  select 1 from public.children c join public.health_center_members hcm on hcm.health_center_id = c.health_center_id
  where c.id = child_id and hcm.user_id = auth.uid()
));
drop policy if exists "members create supplement plans" on public.supplement_plans;
create policy "members create supplement plans" on public.supplement_plans for insert to authenticated with check (
  indicating_professional_id = auth.uid() and verification_status = 'verified' and verified_at is not null
  and exists (select 1 from public.children c join public.health_center_members hcm on hcm.health_center_id = c.health_center_id
    where c.id = child_id and hcm.user_id = auth.uid())
);
drop policy if exists "admins manage all supplement plans" on public.supplement_plans;
create policy "admins manage all supplement plans" on public.supplement_plans for all to authenticated
using (exists (select 1 from public.health_center_members hcm where hcm.user_id = auth.uid() and hcm.role = 'admin'))
with check (exists (select 1 from public.health_center_members hcm where hcm.user_id = auth.uid() and hcm.role = 'admin'));

drop policy if exists "members see supplement intake" on public.supplement_intake_events;
create policy "members see supplement intake" on public.supplement_intake_events for select to authenticated using (exists (
  select 1 from public.supplement_plans sp join public.children c on c.id = sp.child_id
  join public.health_center_members hcm on hcm.health_center_id = c.health_center_id
  where sp.id = plan_id and hcm.user_id = auth.uid()
));
drop policy if exists "admins see all supplement intake" on public.supplement_intake_events;
create policy "admins see all supplement intake" on public.supplement_intake_events for select to authenticated
using (exists (select 1 from public.health_center_members hcm where hcm.user_id = auth.uid() and hcm.role = 'admin'));
drop policy if exists "members see supplement reminders" on public.supplement_reminder_preferences;
create policy "members see supplement reminders" on public.supplement_reminder_preferences for select to authenticated using (exists (
  select 1 from public.supplement_plans sp join public.children c on c.id = sp.child_id
  join public.health_center_members hcm on hcm.health_center_id = c.health_center_id
  where sp.id = plan_id and hcm.user_id = auth.uid()
));
drop policy if exists "admins see all supplement reminders" on public.supplement_reminder_preferences;
create policy "admins see all supplement reminders" on public.supplement_reminder_preferences for select to authenticated
using (exists (select 1 from public.health_center_members hcm where hcm.user_id = auth.uid() and hcm.role = 'admin'));

grant select on public.professional_profiles, public.child_conditions, public.supplement_plans,
  public.supplement_intake_events, public.supplement_reminder_preferences,
  public.v_supplement_followup to authenticated;
grant insert on public.child_conditions, public.supplement_plans to authenticated;
grant update (verification_status, condition_status, diagnosing_professional_id, health_center_id, verified_at)
  on public.child_conditions to authenticated;
grant update (verification_status, status, purpose, schedule_text, indicating_professional_id, health_center_id, verified_at)
  on public.supplement_plans to authenticated;

notify pgrst, 'reload schema';
