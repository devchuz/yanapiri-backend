-- Separa mediciones familiares preliminares de mediciones clínicas verificadas
-- y agrega el registro auditable de citas.

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

create index if not exists idx_measurements_child_source_date
  on public.measurements(child_id, verification_status, measured_at desc);
create index if not exists idx_appointments_child_date
  on public.appointments(child_id, scheduled_at desc);

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

alter table public.appointments enable row level security;
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

grant select, insert, update on public.appointments to authenticated;

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

grant select on public.v_casos_priorizados, public.v_app_children_compat to authenticated;
