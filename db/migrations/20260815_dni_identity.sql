-- DNI para identidad y deduplicación. Las columnas permanecen nullable para
-- migrar registros históricos; el bot las exige en toda alta nueva.

alter table public.caregivers add column if not exists dni text;
alter table public.children add column if not exists dni text;

alter table public.caregivers drop constraint if exists caregivers_dni_format;
alter table public.caregivers
  add constraint caregivers_dni_format check (dni is null or dni ~ '^[0-9]{8}$');

alter table public.children drop constraint if exists children_dni_format;
alter table public.children
  add constraint children_dni_format check (dni is null or dni ~ '^[0-9]{8}$');

create unique index if not exists caregivers_dni_unique
  on public.caregivers(dni) where dni is not null;

create unique index if not exists children_dni_unique
  on public.children(dni) where dni is not null;

comment on column public.caregivers.dni is
  'DNI peruano de 8 dígitos. Dato identificatorio sensible; no exponer en logs ni al LLM.';

comment on column public.children.dni is
  'DNI peruano de 8 dígitos usado para deduplicación. No exponer en mensajes completos.';
