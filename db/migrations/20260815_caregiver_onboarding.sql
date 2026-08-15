-- Registro independiente de la persona cuidadora y versión del consentimiento.
-- Idempotente: puede ejecutarse sobre una base ya creada.

alter table public.caregivers
  add column if not exists relationship text not null default 'cuidador';

alter table public.caregivers
  add column if not exists consent_version text not null default '2026-08-v1';

alter table public.caregivers
  add column if not exists consent_withdrawn_at timestamptz;

comment on column public.caregivers.consent_version is
  'Versión del aviso aceptado por la persona cuidadora.';

comment on column public.caregivers.consent_withdrawn_at is
  'Fecha de retiro del consentimiento; no implica borrado automático del registro clínico.';
