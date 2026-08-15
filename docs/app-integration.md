# Integración con la app clínica Yanapiri Wawa

Revisión del bundle público desplegado el 2026-08-12:

- la URL del backend está fija en `http://127.0.0.1:8000`;
- consume `/auth/login`, `/children`, `/children/{id}/measurements`,
  `/children/{id}/alerts`, `/visits`, `/audit`, `/rules` y rutas de administración;
- convierte el identificador del niño con `parseInt`, aunque el contrato acordado
  usa UUID;
- traduce los casos a `urgent | follow-up | normal`, mientras la base compartida
  usa `rojo | amarillo | verde`;
- cuando ese backend no responde, muestra datos de demostración locales.

## Suplementación infantil

El módulo SRSI expone por RLS las tablas `child_conditions`,
`supplement_plans`, `supplement_intake_events` y la vista
`v_supplement_followup`. La vista devuelve planes activos, estado de
verificación, última toma y conteos de los últimos siete días para el
establecimiento asignado.

La app clínica es responsable de conciliar un reporte con
`professional_profiles.user_id`, completar finalidad/frecuencia y cambiar
`verification_status`. Un valor `reported` nunca debe presentarse como diagnóstico
o prescripción verificada. El cliente no debe calcular dosis ni convertir días sin
registro en incumplimientos.

Por eso la demo web actual todavía no está conectada a esta base. No se cambió el
contrato clínico para imitar datos ficticios ni identificadores enteros.

## Cambio recomendado en el frontend

1. Crear el cliente Supabase con la URL y publishable/anon key del mismo proyecto.
2. Autenticar al profesional con Supabase Auth.
3. Insertar su `auth.uid()` en `health_center_members` mediante una operación
   administrativa.
4. Reemplazar `GET /children` por:

```ts
const { data, error } = await supabase
  .from('v_casos_priorizados')
  .select('*')
  .order('priority_order')
```

5. Mantener `child_id` como `string`; no usar `parseInt`.
6. Para la trayectoria:

```ts
const { data } = await supabase
  .from('measurements')
  .select('*, assessment_results(*)')
  .eq('child_id', childId)
  .order('measured_at', { ascending: false })
```

No mezclar las fuentes al graficar:

- `verification_status = reported`: línea familiar preliminar;
- `verification_status = verified`: referencia clínica;
- `validation_status = needs_review`: dato conservado sin interpretación OMS.

La API de este repositorio expone el contrato recomendado para escrituras
clínicas. Enviar el access token de la sesión Supabase:

```http
Authorization: Bearer <supabase_access_token>
```

```text
GET    /clinical/children/{id}/history
POST   /clinical/children/{id}/measurements
GET    /clinical/children/{id}/appointments
POST   /clinical/children/{id}/appointments
PATCH  /clinical/appointments/{appointment_id}
POST   /clinical/children/{id}/ask
```

`/ask` consulta únicamente el historial autorizado y devuelve un resumen
determinista. No calcula diagnósticos ni sustituye la revisión profesional.

7. Para las alertas, leer `alerts` y actualizar solo `estado` en orden:
   `abierta → vista → en_seguimiento → resuelta`.

RLS aplica el establecimiento asociado al usuario. La anon key puede estar en el
cliente; la service role no.

## Puente temporal

`v_app_children_compat` expone nombres parecidos al mapper actual:
`fecha_nacimiento`, `name`, `caregiver`, `status_alerta`, `weight`, `height`,
`muac`, `last_measured`, `zscore_actual`, `district` y `community`.

La vista convierte únicamente para presentación:

| Base común | Vista temporal |
|---|---|
| `rojo` | `urgent` |
| `amarillo` | `follow-up` |
| `verde` | `normal` |

Los UUID permanecen intactos y las escrituras deben hacerse en las tablas reales.
