# API de NutriCRED

La especificación ejecutable vive en FastAPI y se genera desde el código:

- Swagger UI: `/docs`
- ReDoc: `/redoc`
- OpenAPI JSON: `/openapi.json`
- Estado operativo: `/health`

En desarrollo local la URL base habitual es `http://localhost:7860`.

## Autenticación profesional

Todas las rutas `/clinical/*` requieren el access token de una sesión de
Supabase, no la anon key ni la service role:

```http
Authorization: Bearer <supabase_access_token>
```

El backend valida además que el usuario aparezca en `health_center_members` y
que tenga acceso al `health_center_id` del niño. Un token válido sin membresía
recibe `403`; un token ausente, vencido o inválido recibe `401`.

En Swagger, seleccionar **Authorize**, pegar el access token y ejecutar la ruta.
No escribir `Bearer` dentro del cuadro si Swagger ya muestra ese esquema.

## Endpoints

| Método | Ruta | Autenticación | Descripción |
|---|---|---|---|
| `GET` | `/` | No | Descubrimiento de documentación |
| `GET` | `/health` | No | Estado de API, Supabase, Groq y Kapso |
| `POST` | `/chat` | No | Simulación local del bot familiar |
| `POST` | `/assessments/preview` | No | Cálculo OMS sin persistencia |
| `GET` | `/nutrition/recommendations/demo` | No | Caso alimentario trazable Lima/Tumbes |
| `POST` | `/webhooks/kapso` | Firma HMAC | Entrada oficial de eventos WhatsApp |
| `GET` | `/clinical/children/{child_id}/history` | Bearer | Historial, alertas y citas |
| `POST` | `/clinical/children/{child_id}/measurements` | Bearer | Nueva medición clínica verificada |
| `GET` | `/clinical/children/{child_id}/appointments` | Bearer | Lista de citas |
| `POST` | `/clinical/children/{child_id}/appointments` | Bearer | Programa una cita |
| `PATCH` | `/clinical/appointments/{appointment_id}` | Bearer | Actualiza el estado de una cita |
| `POST` | `/clinical/children/{child_id}/ask` | Bearer | Pregunta determinista sobre el historial |

## Fuentes de medición

Una medición familiar se conserva como:

```json
{
  "source": "caregiver",
  "verification_status": "reported"
}
```

Una medición ingresada por la API clínica se conserva como:

```json
{
  "source": "health_worker",
  "verification_status": "verified",
  "recorded_by": "uuid-del-profesional"
}
```

Las dos series no se promedian, no se sobrescriben y se devuelven separadas como
`reported_trajectory` y `verified_trajectory`.

## Ejemplos

### Recomendación alimentaria demostrativa

```bash
curl http://localhost:7860/nutrition/recommendations/demo
```

El caso usa una persona ficticia de 7 meses, una receta infantil INS/CENAN y
precios mayoristas MIDAGRI de Lima y Tumbes. El costo de la receta queda en
`null` porque las medidas caseras aún no tienen equivalencias documentadas. La
respuesta muestra precios observados, mapeos pendientes, cobertura y la razón
por la que el ranking de costo permanece desactivado.

### Vista previa antropométrica

```bash
curl -X POST http://localhost:7860/assessments/preview \
  -H "Content-Type: application/json" \
  -d '{
    "birth_date": "2025-01-01",
    "measured_at": "2026-01-01",
    "sex": "F",
    "weight_kg": 8.9,
    "height_cm": 74.0,
    "height_mode": "length",
    "muac_mm": 120,
    "bilateral_edema": false
  }'
```

### Registrar una medición clínica

```bash
curl -X POST http://localhost:7860/clinical/children/CHILD_UUID/measurements \
  -H "Authorization: Bearer SUPABASE_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "measured_at": "2026-08-15",
    "weight_kg": 9.1,
    "height_cm": 75.0,
    "height_mode": "length",
    "muac_mm": 121,
    "bilateral_edema": false
  }'
```

### Programar una cita

```bash
curl -X POST http://localhost:7860/clinical/children/CHILD_UUID/appointments \
  -H "Authorization: Bearer SUPABASE_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "scheduled_at": "2026-08-20T15:00:00-05:00",
    "appointment_type": "growth_control",
    "notes": "Control CRED"
  }'
```

Tipos de cita permitidos: `growth_control`, `nutrition`, `vaccination`,
`pediatrics` y `other`.

Estados: `scheduled`, `confirmed`, `completed`, `missed` y `cancelled`. Los
estados terminales no pueden revertirse.

### Preguntar por el historial

```bash
curl -X POST http://localhost:7860/clinical/children/CHILD_UUID/ask \
  -H "Authorization: Bearer SUPABASE_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question":"¿Cuál fue la última medición clínica verificada?"}'
```

La respuesta usa datos almacenados y reglas deterministas. Este endpoint no
solicita al LLM que calcule indicadores, diagnostique ni recomiende tratamientos.

## Webhook de Kapso

Configurar en Kapso:

```text
POST https://TU_DOMINIO/webhooks/kapso
Evento: whatsapp.message.received
```

Encabezados reconocidos:

- `X-Webhook-Event`
- `X-Webhook-Signature`

La API responde inmediatamente con `accepted`; la contestación de WhatsApp se
procesa después en una cola por identidad. Los reintentos con el mismo
`message.id` no vuelven a procesarse.

## Errores comunes

| Código | Significado |
|---|---|
| `400` | JSON de webhook inválido |
| `401` | Token o firma ausente/inválida |
| `403` | Sin acceso al establecimiento |
| `404` | Recurso no encontrado |
| `422` | Datos antropométricos, enum o transición inválida |
| `503` | Supabase ausente, indisponible o migración pendiente |

La documentación no sustituye el contrato SQL. Las tablas y políticas oficiales
se mantienen en `db/schema.sql` y `db/migrations/`.
