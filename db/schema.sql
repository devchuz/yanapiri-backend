-- NutriCRED — esquema único del equipo.
-- Ejecutar completo en Supabase SQL Editor. Timestamps en UTC (timestamptz).

create extension if not exists pgcrypto;

create table if not exists public.health_centers (
  id uuid primary key default gen_random_uuid(),
  renaes_code text unique,
  name text not null,
  district text not null,
  province text,
  region text,
  active boolean not null default true,
  created_at timestamptz not null default now()
);

create table if not exists public.health_center_members (
  user_id uuid not null references auth.users(id) on delete cascade,
  health_center_id uuid not null references public.health_centers(id) on delete cascade,
  role text not null default 'health_worker' check (role in ('health_worker','coordinator','admin')),
  created_at timestamptz not null default now(),
  primary key (user_id, health_center_id)
);

create table if not exists public.professional_profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  full_name text not null,
  profession text not null,
  license_number text,
  verified boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.caregivers (
  id uuid primary key default gen_random_uuid(),
  whatsapp_identity text not null unique,
  phone_number text,
  dni text,
  full_name text not null,
  district text not null,
  consent_at timestamptz not null,
  consent_version text not null default '2026-08-v1',
  consent_withdrawn_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Migración idempotente para proyectos creados con una versión anterior.
alter table public.caregivers
  add column if not exists relationship text not null default 'cuidador';

alter table public.caregivers
  add column if not exists consent_version text not null default '2026-08-v1';

alter table public.caregivers
  add column if not exists consent_withdrawn_at timestamptz;

alter table public.caregivers
  add column if not exists dni text;

alter table public.caregivers drop constraint if exists caregivers_dni_format;
alter table public.caregivers
  add constraint caregivers_dni_format check (dni is null or dni ~ '^[0-9]{8}$');

create unique index if not exists caregivers_dni_unique
  on public.caregivers(dni) where dni is not null;

create table if not exists public.children (
  id uuid primary key default gen_random_uuid(),
  caregiver_id uuid not null references public.caregivers(id) on delete cascade,
  health_center_id uuid references public.health_centers(id) on delete set null,
  dni text,
  full_name text not null,
  birth_date date not null,
  sex text not null check (sex in ('M','F')),
  district text not null,
  reported_health_center text,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.children
  add column if not exists dni text;

alter table public.children drop constraint if exists children_dni_format;
alter table public.children
  add constraint children_dni_format check (dni is null or dni ~ '^[0-9]{8}$');

create unique index if not exists children_dni_unique
  on public.children(dni) where dni is not null;

-- Evita dos altas activas idénticas para la misma persona cuidadora. Si una
-- instalación anterior ya tiene duplicados, no elimina información: deja un
-- aviso para que el equipo los revise antes de volver a ejecutar el esquema.
do $$
begin
  if not exists (
    select 1 from pg_indexes
    where schemaname = 'public' and indexname = 'children_active_identity_unique'
  ) then
    if exists (
      select 1
      from public.children
      where active
      group by caregiver_id, birth_date,
        lower(regexp_replace(btrim(full_name), '\s+', ' ', 'g'))
      having count(*) > 1
    ) then
      raise notice 'No se creó children_active_identity_unique: existen duplicados activos para revisar.';
    else
      execute 'create unique index children_active_identity_unique
        on public.children (
          caregiver_id,
          birth_date,
          lower(regexp_replace(btrim(full_name), ''\s+'', '' '', ''g''))
        ) where active';
    end if;
  end if;
end
$$;

create table if not exists public.measurements (
  id uuid primary key default gen_random_uuid(),
  child_id uuid not null references public.children(id) on delete cascade,
  measured_at timestamptz not null,
  weight_kg numeric(5,2) not null check (weight_kg between 0.1 and 100),
  height_cm numeric(5,2) not null check (height_cm between 10 and 250),
  height_mode text not null check (height_mode in ('length','height')),
  muac_mm numeric(5,1) check (muac_mm is null or muac_mm between 10 and 1000),
  bilateral_edema boolean not null default false,
  source text not null default 'caregiver' check (source in ('caregiver','health_worker')),
  verification_status text not null default 'reported'
    check (verification_status in ('reported','verified')),
  recorded_by uuid references auth.users(id) on delete set null,
  verified_at timestamptz,
  validation_status text not null default 'valid' check (validation_status in ('valid','needs_review')),
  validation_notes text check (validation_notes is null or char_length(validation_notes) <= 500),
  created_at timestamptz not null default now(),
  constraint measurements_source_verification_check check (
    (source = 'caregiver' and verification_status = 'reported' and verified_at is null) or
    (source = 'health_worker' and verification_status = 'verified' and verified_at is not null)
  )
);

-- Migración idempotente para bases creadas antes de conservar mediciones
-- improbables como pendientes de revisión.
alter table public.measurements
  add column if not exists validation_status text not null default 'valid'
  check (validation_status in ('valid','needs_review'));
alter table public.measurements
  add column if not exists validation_notes text
  check (validation_notes is null or char_length(validation_notes) <= 500);
alter table public.measurements
  add column if not exists verification_status text not null default 'reported';
alter table public.measurements
  add column if not exists recorded_by uuid references auth.users(id) on delete set null;
alter table public.measurements
  add column if not exists verified_at timestamptz;
update public.measurements
set source = 'caregiver', verification_status = 'reported', recorded_by = null, verified_at = null
where source not in ('caregiver','health_worker');
update public.measurements
set verification_status = 'verified', verified_at = coalesce(verified_at, created_at)
where source = 'health_worker';
alter table public.measurements drop constraint if exists measurements_source_check;
alter table public.measurements
  add constraint measurements_source_check check (source in ('caregiver','health_worker'));
alter table public.measurements drop constraint if exists measurements_verification_status_check;
alter table public.measurements
  add constraint measurements_verification_status_check
  check (verification_status in ('reported','verified'));
alter table public.measurements drop constraint if exists measurements_source_verification_check;
alter table public.measurements
  add constraint measurements_source_verification_check check (
    (source = 'caregiver' and verification_status = 'reported' and verified_at is null) or
    (source = 'health_worker' and verification_status = 'verified' and verified_at is not null)
  );

-- La captura acepta un rango amplio para no perder lo reportado por la familia.
-- Solo el motor antropométrico aplica los límites OMS de interpretación.
alter table public.measurements
  drop constraint if exists measurements_weight_kg_check;
alter table public.measurements
  add constraint measurements_weight_kg_check check (weight_kg between 0.1 and 100);
alter table public.measurements
  drop constraint if exists measurements_height_cm_check;
alter table public.measurements
  add constraint measurements_height_cm_check check (height_cm between 10 and 250);
alter table public.measurements
  drop constraint if exists measurements_muac_mm_check;
alter table public.measurements
  add constraint measurements_muac_mm_check check (muac_mm is null or muac_mm between 10 and 1000);

create table if not exists public.assessment_results (
  id uuid primary key default gen_random_uuid(),
  measurement_id uuid not null unique references public.measurements(id) on delete cascade,
  age_days integer not null check (age_days between 0 and 1856),
  waz numeric(5,2),
  haz numeric(5,2),
  whz numeric(5,2),
  wh_indicator text not null check (wh_indicator in ('peso/longitud','peso/talla')),
  adjusted_height_cm numeric(5,1) not null,
  semaforo text not null check (semaforo in ('verde','amarillo','rojo')),
  reasons jsonb not null default '[]'::jsonb,
  warnings jsonb not null default '[]'::jsonb,
  rule_version text not null,
  created_at timestamptz not null default now()
);

create table if not exists public.alerts (
  id uuid primary key default gen_random_uuid(),
  child_id uuid not null references public.children(id) on delete cascade,
  measurement_id uuid not null references public.measurements(id) on delete cascade,
  health_center_id uuid references public.health_centers(id) on delete set null,
  nivel text not null check (nivel in ('amarillo','rojo')),
  alert_type text not null default 'verification_request'
    check (alert_type in ('verification_request','clinical_alert')),
  estado text not null default 'abierta' check (estado in ('abierta','vista','en_seguimiento','resuelta')),
  reason text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  resolved_at timestamptz
);

alter table public.alerts
  add column if not exists alert_type text not null default 'verification_request';
update public.alerts a
set alert_type = case
  when m.source = 'health_worker' then 'clinical_alert'
  else 'verification_request'
end
from public.measurements m
where m.id = a.measurement_id;
alter table public.alerts drop constraint if exists alerts_alert_type_check;
alter table public.alerts
  add constraint alerts_alert_type_check
  check (alert_type in ('verification_request','clinical_alert'));

create table if not exists public.appointments (
  id uuid primary key default gen_random_uuid(),
  child_id uuid not null references public.children(id) on delete cascade,
  health_center_id uuid references public.health_centers(id) on delete set null,
  scheduled_at timestamptz not null,
  appointment_type text not null default 'growth_control'
    check (appointment_type in ('growth_control','nutrition','vaccination','pediatrics','other')),
  status text not null default 'scheduled'
    check (status in ('scheduled','confirmed','completed','missed','cancelled')),
  notes text check (notes is null or char_length(notes) <= 500),
  created_by uuid not null references auth.users(id) on delete restrict,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Bitácora inmutable de acompañamiento. Los eventos informados por la familia
-- no cambian ni resuelven el estado clínico de la alerta.
create table if not exists public.alert_followup_events (
  id uuid primary key default gen_random_uuid(),
  alert_id uuid not null references public.alerts(id) on delete cascade,
  actor_type text not null check (actor_type in ('caregiver','health_worker','system')),
  event_type text not null check (event_type in (
    'caregiver_acknowledged','establishment_requested','plans_to_attend',
    'attendance_reported','needs_support','recommendation_requested',
    'reminder_sent','health_worker_contacted','clinically_verified'
  )),
  planned_for date,
  barrier_code text check (barrier_code is null or barrier_code in (
    'appointment','distance','transport_cost','schedule','unknown_facility','other'
  )),
  notes text check (notes is null or char_length(notes) <= 500),
  occurred_at timestamptz not null default now(),
  check (event_type <> 'needs_support' or barrier_code is not null)
);

-- Antecedentes nutricionales y planes indicados. Un reporte familiar nunca se
-- considera diagnóstico ni prescripción verificada hasta la conciliación clínica.
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
  verification_status text not null default 'reported'
    check (verification_status in ('reported','verified','rejected')),
  condition_status text not null default 'active'
    check (condition_status in ('active','resolved','inactive')),
  source_system text not null default 'caregiver'
    check (source_system in ('caregiver','clinical_app','import','external')),
  external_record_id text,
  reported_by_identity text,
  verified_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (verification_status <> 'verified' or (
    diagnosing_professional_id is not null and verified_at is not null
  )),
  unique (source_system, external_record_id)
);

create table if not exists public.supplement_plans (
  id uuid primary key default gen_random_uuid(),
  child_id uuid not null references public.children(id) on delete cascade,
  condition_id uuid references public.child_conditions(id) on delete set null,
  supplement_type text not null
    check (supplement_type in ('iron','mnp','vitamin_a','zinc','vitamin_d','other')),
  purpose text not null default 'unknown'
    check (purpose in ('preventive','therapeutic','unknown')),
  start_date date not null default current_date,
  end_date date,
  schedule_text text check (schedule_text is null or char_length(schedule_text) <= 300),
  indicating_professional_id uuid references auth.users(id) on delete set null,
  indicated_by_name text,
  health_center_id uuid references public.health_centers(id) on delete set null,
  reported_health_center text,
  verification_status text not null default 'reported'
    check (verification_status in ('reported','verified','rejected')),
  status text not null default 'active'
    check (status in ('active','paused','completed','stopped')),
  source_system text not null default 'caregiver'
    check (source_system in ('caregiver','clinical_app','import','external')),
  external_record_id text,
  verified_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (end_date is null or end_date >= start_date),
  check (verification_status <> 'verified' or (
    indicating_professional_id is not null and verified_at is not null
  )),
  unique (source_system, external_record_id)
);

create table if not exists public.supplement_intake_events (
  id uuid primary key default gen_random_uuid(),
  plan_id uuid not null references public.supplement_plans(id) on delete cascade,
  scheduled_for date not null,
  intake_status text not null check (intake_status in ('taken','not_taken','pending')),
  reason_code text check (reason_code is null or reason_code in (
    'forgot','out_of_stock','child_refused','discomfort','instructions_unclear','other'
  )),
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

create table if not exists public.conversation_messages (
  id uuid primary key default gen_random_uuid(),
  whatsapp_identity text not null,
  role text not null check (role in ('user','assistant','system')),
  content text not null,
  created_at timestamptz not null default now()
);

create table if not exists public.conversation_states (
  whatsapp_identity text primary key,
  state jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

create table if not exists public.webhook_events (
  event_id text primary key,
  event_type text not null,
  payload jsonb not null default '{}'::jsonb,
  received_at timestamptz not null default now()
);

create table if not exists public.district_recommendations (
  id uuid primary key default gen_random_uuid(),
  district text not null,
  min_age_months integer not null default 0,
  max_age_months integer not null default 59,
  category text not null,
  content text not null,
  source_url text,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  check (min_age_months between 0 and 59 and max_age_months between min_age_months and 59)
);

create index if not exists idx_children_health_center on public.children(health_center_id);
create index if not exists idx_measurements_child_date on public.measurements(child_id, measured_at desc);
create index if not exists idx_measurements_child_source_date
  on public.measurements(child_id, verification_status, measured_at desc);
create index if not exists idx_alerts_center_status on public.alerts(health_center_id, estado, nivel);
create index if not exists idx_appointments_child_date
  on public.appointments(child_id, scheduled_at desc);
create index if not exists idx_followup_alert_date on public.alert_followup_events(alert_id, occurred_at desc);
create index if not exists idx_followup_planned_for on public.alert_followup_events(planned_for)
  where event_type = 'plans_to_attend';
create index if not exists idx_messages_identity_date on public.conversation_messages(whatsapp_identity, created_at desc);
create index if not exists idx_conditions_child_status on public.child_conditions(child_id, condition_status);
create index if not exists idx_supplement_plans_child_status on public.supplement_plans(child_id, status);
create index if not exists idx_supplement_intake_plan_date on public.supplement_intake_events(plan_id, scheduled_for desc);
create index if not exists idx_supplement_reminders_due on public.supplement_reminder_preferences(enabled, reminder_time);

create or replace function public.set_updated_at()
returns trigger language plpgsql set search_path = '' as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists caregivers_set_updated_at on public.caregivers;
create trigger caregivers_set_updated_at before update on public.caregivers
for each row execute function public.set_updated_at();

drop trigger if exists children_set_updated_at on public.children;
create trigger children_set_updated_at before update on public.children
for each row execute function public.set_updated_at();

drop trigger if exists appointments_set_updated_at on public.appointments;
create trigger appointments_set_updated_at before update on public.appointments
for each row execute function public.set_updated_at();

create or replace function public.validate_appointment_transition()
returns trigger language plpgsql set search_path = '' as $$
begin
  if old.status <> new.status and not (
    (old.status = 'scheduled' and new.status in ('confirmed','completed','missed','cancelled')) or
    (old.status = 'confirmed' and new.status in ('completed','missed','cancelled'))
  ) then
    raise exception 'Transición de cita no permitida: % -> %', old.status, new.status;
  end if;
  return new;
end;
$$;

drop trigger if exists appointments_validate_transition on public.appointments;
create trigger appointments_validate_transition before update of status on public.appointments
for each row execute function public.validate_appointment_transition();

create or replace function public.sync_child_health_center_to_alerts()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  if old.health_center_id is distinct from new.health_center_id then
    update public.alerts set health_center_id = new.health_center_id where child_id = new.id;
  end if;
  return new;
end;
$$;

drop trigger if exists children_sync_alert_center on public.children;
create trigger children_sync_alert_center after update of health_center_id on public.children
for each row execute function public.sync_child_health_center_to_alerts();

create or replace function public.validate_alert_transition()
returns trigger language plpgsql set search_path = '' as $$
begin
  if old.estado <> new.estado and not (
    (old.estado = 'abierta' and new.estado = 'vista') or
    (old.estado = 'vista' and new.estado = 'en_seguimiento') or
    (old.estado = 'en_seguimiento' and new.estado = 'resuelta')
  ) then
    raise exception 'Transición de alerta no permitida: % -> %', old.estado, new.estado;
  end if;
  new.updated_at = now();
  if new.estado = 'resuelta' and old.estado <> 'resuelta' then
    new.resolved_at = now();
  end if;
  return new;
end;
$$;

drop trigger if exists alerts_validate_transition on public.alerts;
create trigger alerts_validate_transition before update on public.alerts
for each row execute function public.validate_alert_transition();

drop view if exists public.v_casos_priorizados;
create view public.v_casos_priorizados
with (security_invoker = true) as
select
  c.id as child_id,
  c.full_name as child_name,
  c.birth_date,
  c.sex,
  c.district,
  c.reported_health_center,
  c.health_center_id,
  cg.full_name as caregiver_name,
  cg.phone_number as caregiver_phone,
  m.id as measurement_id,
  m.measured_at,
  m.weight_kg,
  m.height_cm,
  m.muac_mm,
  m.source as measurement_source,
  m.verification_status,
  ar.waz,
  ar.haz,
  ar.whz,
  ar.semaforo,
  ar.reasons,
  a.id as alert_id,
  a.nivel as alert_level,
  a.alert_type,
  a.estado as alert_status,
  fe.event_type as last_followup_event,
  fe.planned_for as followup_planned_for,
  fe.barrier_code as followup_barrier,
  fe.occurred_at as last_followup_at,
  case
    when a.nivel = 'rojo' and a.alert_type = 'clinical_alert' then 1
    when a.nivel = 'rojo' then 2
    when a.alert_type = 'clinical_alert' then 3
    else 4
  end as priority_order
from public.children c
join public.caregivers cg on cg.id = c.caregiver_id
join lateral (
  select * from public.alerts ax
  where ax.child_id = c.id and ax.estado <> 'resuelta'
  order by case
    when ax.nivel = 'rojo' and ax.alert_type = 'clinical_alert' then 1
    when ax.nivel = 'rojo' then 2
    when ax.alert_type = 'clinical_alert' then 3
    else 4
  end, ax.created_at desc limit 1
) a on true
join public.measurements m on m.id = a.measurement_id
join public.assessment_results ar on ar.measurement_id = m.id
left join lateral (
  select * from public.alert_followup_events fx
  where fx.alert_id = a.id order by fx.occurred_at desc limit 1
) fe on true
where c.active = true;

-- Vista temporal para facilitar la migración del frontend desplegado. El id sigue
-- siendo UUID: el cliente no debe convertirlo con parseInt.
drop view if exists public.v_app_children_compat;
create view public.v_app_children_compat
with (security_invoker = true) as
select
  c.id,
  c.birth_date as fecha_nacimiento,
  c.full_name as name,
  c.sex,
  cg.full_name as caregiver,
  case
    when m.validation_status = 'needs_review' then 'needs-review'
    when m.verification_status = 'reported' then 'needs-review'
    when ar.semaforo = 'rojo' then 'urgent'
    when ar.semaforo = 'amarillo' then 'follow-up'
    else 'normal'
  end as status_alerta,
  m.weight_kg as weight,
  m.height_cm as height,
  m.muac_mm as muac,
  m.measured_at as last_measured,
  m.validation_status,
  m.validation_notes,
  m.source as measurement_source,
  m.verification_status,
  (m.verification_status = 'reported') as status_is_preliminary,
  ar.whz as zscore_actual,
  c.district,
  c.district as community
from public.children c
join public.caregivers cg on cg.id = c.caregiver_id
left join lateral (
  select * from public.measurements mx
  where mx.child_id = c.id
  order by (mx.verification_status = 'verified') desc, mx.measured_at desc
  limit 1
) m on true
left join public.assessment_results ar on ar.measurement_id = m.id
where c.active = true;

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
  order by sie.scheduled_for desc, sie.reported_at desc
  limit 1
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

-- RLS: la app cliente usa anon key + una sesión de usuario autenticado.
-- La anon key sola NO concede acceso; el backend usa service_role y no queda expuesto.
alter table public.health_centers enable row level security;
alter table public.health_center_members enable row level security;
alter table public.professional_profiles enable row level security;
alter table public.caregivers enable row level security;
alter table public.children enable row level security;
alter table public.measurements enable row level security;
alter table public.assessment_results enable row level security;
alter table public.alerts enable row level security;
alter table public.alert_followup_events enable row level security;
alter table public.appointments enable row level security;
alter table public.child_conditions enable row level security;
alter table public.supplement_plans enable row level security;
alter table public.supplement_intake_events enable row level security;
alter table public.supplement_reminder_preferences enable row level security;
alter table public.conversation_messages enable row level security;
alter table public.conversation_states enable row level security;
alter table public.webhook_events enable row level security;
alter table public.district_recommendations enable row level security;

drop policy if exists "members see their membership" on public.health_center_members;
create policy "members see their membership" on public.health_center_members
for select to authenticated using (user_id = auth.uid());

drop policy if exists "professionals see profiles" on public.professional_profiles;
create policy "professionals see profiles" on public.professional_profiles
for select to authenticated using (
  user_id = auth.uid() or exists (
    select 1
    from public.health_center_members viewer
    join public.health_center_members subject
      on subject.health_center_id = viewer.health_center_id
    where viewer.user_id = auth.uid() and subject.user_id = user_id
  ) or exists (
    select 1 from public.health_center_members admin
    where admin.user_id = auth.uid() and admin.role = 'admin'
  )
);

drop policy if exists "members see health centers" on public.health_centers;
create policy "members see health centers" on public.health_centers
for select to authenticated using (
  exists (select 1 from public.health_center_members hcm where hcm.user_id = auth.uid() and hcm.health_center_id = id)
);

drop policy if exists "admins see all health centers" on public.health_centers;
create policy "admins see all health centers" on public.health_centers
for select to authenticated using (
  exists (select 1 from public.health_center_members hcm where hcm.user_id = auth.uid() and hcm.role = 'admin')
);

drop policy if exists "members see assigned children" on public.children;
create policy "members see assigned children" on public.children
for select to authenticated using (
  exists (select 1 from public.health_center_members hcm where hcm.user_id = auth.uid() and hcm.health_center_id = health_center_id)
);

drop policy if exists "admins see all children" on public.children;
create policy "admins see all children" on public.children
for select to authenticated using (
  exists (select 1 from public.health_center_members hcm where hcm.user_id = auth.uid() and hcm.role = 'admin')
);

drop policy if exists "admins assign children" on public.children;
create policy "admins assign children" on public.children
for update to authenticated
using (exists (select 1 from public.health_center_members hcm where hcm.user_id = auth.uid() and hcm.role = 'admin'))
with check (exists (select 1 from public.health_center_members hcm where hcm.user_id = auth.uid() and hcm.role = 'admin'));

drop policy if exists "members see caregivers" on public.caregivers;
create policy "members see caregivers" on public.caregivers
for select to authenticated using (
  exists (
    select 1 from public.children c
    join public.health_center_members hcm on hcm.health_center_id = c.health_center_id
    where c.caregiver_id = id and hcm.user_id = auth.uid()
  )
);

drop policy if exists "admins see all caregivers" on public.caregivers;
create policy "admins see all caregivers" on public.caregivers
for select to authenticated using (
  exists (select 1 from public.health_center_members hcm where hcm.user_id = auth.uid() and hcm.role = 'admin')
);

drop policy if exists "members see measurements" on public.measurements;
create policy "members see measurements" on public.measurements
for select to authenticated using (
  exists (
    select 1 from public.children c
    join public.health_center_members hcm on hcm.health_center_id = c.health_center_id
    where c.id = child_id and hcm.user_id = auth.uid()
  )
);

drop policy if exists "admins see all measurements" on public.measurements;
create policy "admins see all measurements" on public.measurements
for select to authenticated using (
  exists (select 1 from public.health_center_members hcm where hcm.user_id = auth.uid() and hcm.role = 'admin')
);

drop policy if exists "members see appointments" on public.appointments;
create policy "members see appointments" on public.appointments
for select to authenticated using (exists (
  select 1 from public.health_center_members hcm
  where hcm.user_id = auth.uid()
    and (hcm.role = 'admin' or hcm.health_center_id = appointments.health_center_id)
));
drop policy if exists "members create appointments" on public.appointments;
create policy "members create appointments" on public.appointments
for insert to authenticated with check (
  created_by = auth.uid() and exists (
    select 1 from public.health_center_members hcm
    where hcm.user_id = auth.uid()
      and (hcm.role = 'admin' or hcm.health_center_id = appointments.health_center_id)
  )
);
drop policy if exists "members update appointments" on public.appointments;
create policy "members update appointments" on public.appointments
for update to authenticated using (exists (
  select 1 from public.health_center_members hcm
  where hcm.user_id = auth.uid()
    and (hcm.role = 'admin' or hcm.health_center_id = appointments.health_center_id)
)) with check (exists (
  select 1 from public.health_center_members hcm
  where hcm.user_id = auth.uid()
    and (hcm.role = 'admin' or hcm.health_center_id = appointments.health_center_id)
));

drop policy if exists "members see assessments" on public.assessment_results;
create policy "members see assessments" on public.assessment_results
for select to authenticated using (
  exists (
    select 1 from public.measurements m
    join public.children c on c.id = m.child_id
    join public.health_center_members hcm on hcm.health_center_id = c.health_center_id
    where m.id = measurement_id and hcm.user_id = auth.uid()
  )
);

drop policy if exists "admins see all assessments" on public.assessment_results;
create policy "admins see all assessments" on public.assessment_results
for select to authenticated using (
  exists (select 1 from public.health_center_members hcm where hcm.user_id = auth.uid() and hcm.role = 'admin')
);

drop policy if exists "members see alerts" on public.alerts;
create policy "members see alerts" on public.alerts
for select to authenticated using (
  exists (select 1 from public.health_center_members hcm where hcm.user_id = auth.uid() and hcm.health_center_id = health_center_id)
);

drop policy if exists "admins see all alerts" on public.alerts;
create policy "admins see all alerts" on public.alerts
for select to authenticated using (
  exists (select 1 from public.health_center_members hcm where hcm.user_id = auth.uid() and hcm.role = 'admin')
);

drop policy if exists "members update alerts" on public.alerts;
create policy "members update alerts" on public.alerts
for update to authenticated
using (exists (select 1 from public.health_center_members hcm where hcm.user_id = auth.uid() and hcm.health_center_id = health_center_id))
with check (exists (select 1 from public.health_center_members hcm where hcm.user_id = auth.uid() and hcm.health_center_id = health_center_id));

drop policy if exists "admins update all alerts" on public.alerts;
create policy "admins update all alerts" on public.alerts
for update to authenticated
using (exists (select 1 from public.health_center_members hcm where hcm.user_id = auth.uid() and hcm.role = 'admin'))
with check (exists (select 1 from public.health_center_members hcm where hcm.user_id = auth.uid() and hcm.role = 'admin'));

drop policy if exists "members see alert followups" on public.alert_followup_events;
create policy "members see alert followups" on public.alert_followup_events
for select to authenticated using (
  exists (
    select 1 from public.alerts a
    join public.health_center_members hcm on hcm.health_center_id = a.health_center_id
    where a.id = alert_id and hcm.user_id = auth.uid()
  )
);

drop policy if exists "admins see all alert followups" on public.alert_followup_events;
create policy "admins see all alert followups" on public.alert_followup_events
for select to authenticated using (
  exists (select 1 from public.health_center_members hcm where hcm.user_id = auth.uid() and hcm.role = 'admin')
);

drop policy if exists "members see child conditions" on public.child_conditions;
create policy "members see child conditions" on public.child_conditions
for select to authenticated using (exists (
  select 1 from public.children c
  join public.health_center_members hcm on hcm.health_center_id = c.health_center_id
  where c.id = child_id and hcm.user_id = auth.uid()
));
drop policy if exists "members manage child conditions" on public.child_conditions;
create policy "members manage child conditions" on public.child_conditions
for update to authenticated using (exists (
  select 1 from public.children c
  join public.health_center_members hcm on hcm.health_center_id = c.health_center_id
  where c.id = child_id and hcm.user_id = auth.uid()
)) with check ((diagnosing_professional_id is null or diagnosing_professional_id = auth.uid()) and exists (
  select 1 from public.children c
  join public.health_center_members hcm on hcm.health_center_id = c.health_center_id
  where c.id = child_id and hcm.user_id = auth.uid()
));
drop policy if exists "members create child conditions" on public.child_conditions;
create policy "members create child conditions" on public.child_conditions
for insert to authenticated with check (
  diagnosing_professional_id = auth.uid() and verification_status = 'verified'
  and verified_at is not null and exists (
    select 1 from public.children c
    join public.health_center_members hcm on hcm.health_center_id = c.health_center_id
    where c.id = child_id and hcm.user_id = auth.uid()
  )
);
drop policy if exists "admins manage all child conditions" on public.child_conditions;
create policy "admins manage all child conditions" on public.child_conditions
for all to authenticated using (exists (
  select 1 from public.health_center_members hcm where hcm.user_id = auth.uid() and hcm.role = 'admin'
)) with check (exists (
  select 1 from public.health_center_members hcm where hcm.user_id = auth.uid() and hcm.role = 'admin'
));

drop policy if exists "members see supplement plans" on public.supplement_plans;
create policy "members see supplement plans" on public.supplement_plans
for select to authenticated using (exists (
  select 1 from public.children c
  join public.health_center_members hcm on hcm.health_center_id = c.health_center_id
  where c.id = child_id and hcm.user_id = auth.uid()
));
drop policy if exists "members manage supplement plans" on public.supplement_plans;
create policy "members manage supplement plans" on public.supplement_plans
for update to authenticated using (exists (
  select 1 from public.children c
  join public.health_center_members hcm on hcm.health_center_id = c.health_center_id
  where c.id = child_id and hcm.user_id = auth.uid()
)) with check ((indicating_professional_id is null or indicating_professional_id = auth.uid()) and exists (
  select 1 from public.children c
  join public.health_center_members hcm on hcm.health_center_id = c.health_center_id
  where c.id = child_id and hcm.user_id = auth.uid()
));
drop policy if exists "members create supplement plans" on public.supplement_plans;
create policy "members create supplement plans" on public.supplement_plans
for insert to authenticated with check (
  indicating_professional_id = auth.uid() and verification_status = 'verified'
  and verified_at is not null and exists (
    select 1 from public.children c
    join public.health_center_members hcm on hcm.health_center_id = c.health_center_id
    where c.id = child_id and hcm.user_id = auth.uid()
  )
);
drop policy if exists "admins manage all supplement plans" on public.supplement_plans;
create policy "admins manage all supplement plans" on public.supplement_plans
for all to authenticated using (exists (
  select 1 from public.health_center_members hcm where hcm.user_id = auth.uid() and hcm.role = 'admin'
)) with check (exists (
  select 1 from public.health_center_members hcm where hcm.user_id = auth.uid() and hcm.role = 'admin'
));

drop policy if exists "members see supplement intake" on public.supplement_intake_events;
create policy "members see supplement intake" on public.supplement_intake_events
for select to authenticated using (exists (
  select 1 from public.supplement_plans sp
  join public.children c on c.id = sp.child_id
  join public.health_center_members hcm on hcm.health_center_id = c.health_center_id
  where sp.id = plan_id and hcm.user_id = auth.uid()
));
drop policy if exists "admins see all supplement intake" on public.supplement_intake_events;
create policy "admins see all supplement intake" on public.supplement_intake_events
for select to authenticated using (exists (
  select 1 from public.health_center_members hcm where hcm.user_id = auth.uid() and hcm.role = 'admin'
));

drop policy if exists "members see supplement reminders" on public.supplement_reminder_preferences;
create policy "members see supplement reminders" on public.supplement_reminder_preferences
for select to authenticated using (exists (
  select 1 from public.supplement_plans sp
  join public.children c on c.id = sp.child_id
  join public.health_center_members hcm on hcm.health_center_id = c.health_center_id
  where sp.id = plan_id and hcm.user_id = auth.uid()
));
drop policy if exists "admins see all supplement reminders" on public.supplement_reminder_preferences;
create policy "admins see all supplement reminders" on public.supplement_reminder_preferences
for select to authenticated using (exists (
  select 1 from public.health_center_members hcm where hcm.user_id = auth.uid() and hcm.role = 'admin'
));

drop policy if exists "authenticated read recommendations" on public.district_recommendations;
create policy "authenticated read recommendations" on public.district_recommendations
for select to authenticated using (active = true);

revoke all on public.conversation_messages, public.conversation_states, public.webhook_events from anon, authenticated;
grant select on public.health_centers, public.health_center_members, public.caregivers, public.children,
  public.measurements, public.assessment_results, public.alerts, public.alert_followup_events,
  public.appointments,
  public.professional_profiles, public.child_conditions, public.supplement_plans,
  public.supplement_intake_events, public.supplement_reminder_preferences,
  public.v_casos_priorizados, public.v_supplement_followup,
  public.v_app_children_compat to authenticated;
grant select on public.district_recommendations to authenticated;
grant update (estado) on public.alerts to authenticated;
grant insert, update on public.appointments to authenticated;
grant update (health_center_id) on public.children to authenticated;
grant insert on public.child_conditions, public.supplement_plans to authenticated;
grant update (verification_status, condition_status, diagnosing_professional_id, health_center_id, verified_at)
  on public.child_conditions to authenticated;
grant update (verification_status, status, purpose, schedule_text, indicating_professional_id, health_center_id, verified_at)
  on public.supplement_plans to authenticated;
