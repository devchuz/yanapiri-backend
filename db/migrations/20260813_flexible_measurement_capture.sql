-- Evita perder mediciones reportadas por la familia por una unidad equivocada
-- o por estar fuera del dominio interpretable del motor OMS. Estos límites son
-- de captura, no umbrales clínicos. Los datos no interpretables se guardan con
-- validation_status = 'needs_review' y no generan assessment ni alerta.

alter table public.measurements
  drop constraint if exists measurements_weight_kg_check;
alter table public.measurements
  add constraint measurements_weight_kg_check
  check (weight_kg between 0.1 and 100);

alter table public.measurements
  drop constraint if exists measurements_height_cm_check;
alter table public.measurements
  add constraint measurements_height_cm_check
  check (height_cm between 10 and 250);

alter table public.measurements
  drop constraint if exists measurements_muac_mm_check;
alter table public.measurements
  add constraint measurements_muac_mm_check
  check (muac_mm is null or muac_mm between 10 and 1000);

notify pgrst, 'reload schema';
