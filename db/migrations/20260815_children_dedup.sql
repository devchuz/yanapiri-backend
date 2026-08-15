-- Previene nuevos registros infantiles duplicados por reintentos o webhooks
-- simultáneos. Si ya existen duplicados, no modifica ni elimina información:
-- primero deben revisarse manualmente y luego volver a ejecutar la migración.

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
