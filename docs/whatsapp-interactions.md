# Interacciones de WhatsApp

Esta capa reduce mensajes y errores de digitación sin cambiar el flujo clínico.
El bot continúa aceptando respuestas escritas y números si un mensaje interactivo
no está disponible.

## Criterio de uso

| Entrada esperada | Presentación | Motivo |
|---|---|---|
| Dos o tres decisiones cerradas | Botones | La opción queda visible y se responde con un toque |
| Entre cuatro y diez opciones | Lista | Evita mensajes extensos y respeta el máximo de tres botones |
| Nombre, fecha, distrito o establecimiento | Texto libre | El valor no puede anticiparse de forma segura |
| Peso, talla, MUAC u hora | Texto libre | Necesita un valor numérico específico |
| Ubicación actual | Solicitud de ubicación | Se incorporará con la búsqueda real por distancia |

## Casos habilitados

| Flujo | Interacciones |
|---|---|
| Primer contacto | Comenzar, explicación y consentimiento previo |
| Registro del cuidador | Relación, confirmación y registro independiente |
| Menú principal | Tres botones: medición, crecimiento y más opciones |
| Más opciones | Lista compacta con registro, alertas, suplementos, establecimiento y privacidad |
| Registro infantil | Sexo, distrito, omitir establecimiento y confirmación |
| Primera medición | Aceptar o posponer |
| Medición | Elegir niña/niño, posición, omitir MUAC, edema y confirmación |
| Establecimiento | Elegir niña/niño, omitir o cancelar |
| Alertas | Elegir alerta, acción, fecha estimada y barrera de acceso |
| Suplementos | Elegir niña/niño, plan, acción, tipo, finalidad, toma, motivo y recordatorio |

Los IDs enviados por los botones y las listas coinciden con los valores que ya
acepta el flujo determinista (`si`, `no`, `1`, `2`, etc.). El webhook procesa el
ID estable y utiliza el título visible únicamente como compatibilidad.

## Comportamiento de respaldo

- Si Kapso acepta el interactivo, se muestra el botón o la lista.
- Si Kapso lo rechaza, se reenvía el mismo contenido como texto con opciones
  numeradas.
- Si el mensaje es demasiado largo, se envía primero el contenido y después un
  selector corto.
- `/chat` mantiene respuestas de texto para facilitar pruebas y documentación.

## Recorrido de incorporación

1. El contacto nuevo recibe únicamente `Comenzar` y `¿Cómo funciona?`.
2. El aviso de uso de datos se muestra antes de solicitar información personal.
3. Después de aceptar, se solicitan relación, nombre y distrito del cuidador.
4. El cuidador se guarda independientemente, aunque decida registrar al niño después.
5. Una persona que regresa ve solamente `Registrar medida`, `Ver crecimiento` y
   `Más opciones`.

Los cierres muestran una única recomendación breve: mantener los controles si no
hay alerta, confirmar la medición ante una alerta amarilla y buscar valoración
prioritaria ante una alerta roja.

## Pendiente relacionado

La búsqueda de establecimientos todavía debe distinguir dos modos:

1. **Usar distrito:** mostrar establecimientos activos del distrito registrado,
   sin afirmar que son los más cercanos.
2. **Compartir ubicación:** solicitar la ubicación por WhatsApp y ordenar los
   establecimientos activos por distancia usando sus coordenadas RENIPRESS.

Esta segunda etapa requiere importar y normalizar el padrón RENIPRESS antes de
habilitar el botón de ubicación.
