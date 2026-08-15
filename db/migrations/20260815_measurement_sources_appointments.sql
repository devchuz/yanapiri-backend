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

-- Las vistas dependen de estas columnas; ejecutar db/schema.sql después de esta
-- migración actualiza también v_casos_priorizados y v_app_children_compat.
