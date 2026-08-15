-- Conserva mediciones confirmadas por la familia que necesitan repetirse o
-- ser revisadas. No generan assessment_results ni alertas mientras estén
-- pendientes.

alter table public.measurements
  add column if not exists validation_status text not null default 'valid';

alter table public.measurements
  drop constraint if exists measurements_validation_status_check;

alter table public.measurements
  add constraint measurements_validation_status_check
  check (validation_status in ('valid','needs_review'));

alter table public.measurements
  add column if not exists validation_notes text;

alter table public.measurements
  drop constraint if exists measurements_validation_notes_check;

alter table public.measurements
  add constraint measurements_validation_notes_check
  check (validation_notes is null or char_length(validation_notes) <= 500);

-- Recarga inmediata del esquema usado por PostgREST/Supabase API.
notify pgrst, 'reload schema';
