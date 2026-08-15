"""Presentación interactiva de los flujos deterministas en WhatsApp.

El dominio sigue aceptando texto y opciones numéricas. Esta capa solo transforma
la siguiente decisión esperada en botones o listas de Kapso/WhatsApp, por lo que
el endpoint ``/chat`` y el fallback por texto conservan el mismo comportamiento.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

from ..core import db


@dataclass(frozen=True)
class Option:
    id: str
    title: str
    description: str = ""


@dataclass(frozen=True)
class Presentation:
    kind: Literal["buttons", "list"]
    body: str
    options: tuple[Option, ...]
    button_text: str = "Ver opciones"
    section_title: str = "Opciones"
    # Si el cuerpo original supera el límite prudente de un interactivo, se
    # envía primero como texto y luego se muestra este prompt breve.
    send_text_first: bool = False


_SUPPLEMENT_LABELS = {
    "iron": "Hierro",
    "mnp": "Micronutrientes (MNP)",
    "vitamin_a": "Vitamina A",
    "zinc": "Zinc",
    "vitamin_d": "Vitamina D",
    "other": "Otro suplemento",
}


def _short(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _compact_interactive_body(value: str) -> str:
    """Evita duplicar en el cuerpo las opciones que ya muestra WhatsApp."""
    lines = []
    for line in str(value or "").splitlines():
        clean = line.strip()
        if re.match(r"^\d+(?:️⃣|\.)\s*", clean):
            continue
        if clean.lower().startswith(("responde con el número", "responde con una opción")):
            continue
        lines.append(line.rstrip())
    compact = "\n".join(lines)
    compact = re.sub(r"\n{3,}", "\n\n", compact).strip()
    return compact or "Selecciona una opción para continuar."


def _options(items: list[tuple[str, str, str]]) -> tuple[Option, ...]:
    return tuple(
        Option(id=str(option_id), title=_short(title, 24), description=_short(description, 72))
        for option_id, title, description in items
    )


def _buttons(body: str, items: list[tuple[str, str]]) -> Presentation:
    # Los títulos de reply buttons deben mantenerse cortos y claros.
    options = tuple(Option(str(option_id), _short(title, 20)) for option_id, title in items)
    return _presentation("buttons", body, options)


def _list(
    body: str,
    items: list[tuple[str, str, str]],
    *,
    button_text: str = "Ver opciones",
    section_title: str = "Opciones",
) -> Presentation:
    return _presentation(
        "list",
        body,
        _options(items),
        button_text=_short(button_text, 20),
        section_title=_short(section_title, 24),
    )


def _presentation(
    kind: Literal["buttons", "list"],
    body: str,
    options: tuple[Option, ...],
    *,
    button_text: str = "Ver opciones",
    section_title: str = "Opciones",
) -> Presentation:
    # 1 000 deja margen frente al límite de 1 024 caracteres del cuerpo.
    if len(body) <= 1000:
        return Presentation(
            kind,
            _compact_interactive_body(body),
            options,
            button_text,
            section_title,
        )
    prompt = "Selecciona una opción para continuar. También puedes responder escribiendo el número."
    return Presentation(kind, prompt, options, button_text, section_title, send_text_first=True)


def _children_list(body: str, children: list[dict]) -> Presentation | None:
    if not children:
        return None
    rows = [
        (str(index), child.get("full_name") or f"Niña o niño {index}", "Seleccionar este registro")
        for index, child in enumerate(children[:10], 1)
    ]
    return _list(body, rows, button_text="Elegir", section_title="Niñas y niños")


def _yes_no(body: str) -> Presentation:
    return _buttons(body, [("si", "Sí"), ("no", "No")])


def build_presentation(identity: str, answer: str) -> Presentation | None:
    """Devuelve la interfaz adecuada para el siguiente paso de la conversación."""
    state = db.estado_conversacion(identity)
    flow = state.get("flow")
    step = state.get("step")
    data = state.get("data") or {}

    if not flow:
        if "¿Qué deseas hacer hoy?" in answer:
            return _buttons(
                answer,
                [
                    ("medicion", "Registrar medida"),
                    ("estado", "Ver crecimiento"),
                    ("mas opciones", "Más opciones"),
                ],
            )
        if "Tu registro como persona cuidadora está listo" in answer:
            return _buttons(answer, [("registrar", "Registrar niño/a"), ("privacidad", "Privacidad")])
        if "Escribe REGISTRAR para comenzar" in answer:
            return _buttons(answer, [("1", "Registrar ahora"), ("4", "Volver al menú")])
        if "Escribe INICIO para volver" in answer:
            return _buttons(answer, [("inicio", "Volver al inicio")])
        if "📈 *Últimos registros de crecimiento*" in answer:
            return _buttons(
                answer,
                [("medicion", "Registrar medida"), ("mas opciones", "Más opciones"), ("inicio", "Inicio")],
            )
        if "como pendiente de confirmar" in answer:
            return _buttons(answer, [("medicion", "Medir nuevamente"), ("inicio", "Ir al inicio")])
        if "*Medición guardada para" in answer:
            return _buttons(answer, [("estado", "Ver crecimiento"), ("inicio", "Ir al inicio")])
        if answer.startswith("✅ Registré") or answer.startswith("✅ Guardé"):
            return _buttons(answer, [("inicio", "Ir al inicio"), ("mas opciones", "Más opciones")])
        return None

    if flow == "onboarding":
        if step == "intro":
            return _buttons(answer, [("1", "Comenzar"), ("2", "¿Cómo funciona?")])
        if step == "consent":
            return _yes_no(answer)
        if step == "relationship":
            return _buttons(answer, [("1", "Madre"), ("2", "Padre"), ("3", "Otro cuidador")])
        if step == "confirm":
            return _yes_no(answer)

    if flow == "caregiver_child_offer":
        return _yes_no(answer)

    if flow == "more_menu":
        return _list(
            answer,
            [
                ("1", "Registrar niño/a", "Agregar otro registro infantil"),
                ("2", "Alertas", "Acciones de seguimiento"),
                ("3", "Suplementos", "Tomas y recordatorios"),
                ("4", "Establecimiento", "Agregar o cambiar centro"),
                ("5", "Ayuda y privacidad", "Uso de datos y orientación"),
                ("6", "Registro rápido", "Enviar datos en un mensaje"),
            ],
            button_text="Ver opciones",
            section_title="Más opciones",
        )

    if flow == "status" and step == "select_child":
        return _children_list(answer, data.get("children") or [])

    if flow == "registration":
        if step == "caregiver_relationship":
            return _buttons(answer, [("1", "Madre"), ("2", "Padre"), ("3", "Otro cuidador")])
        if step == "sex":
            return _buttons(answer, [("f", "Niña / femenino"), ("m", "Niño / masculino")])
        if step == "district_confirm":
            return _buttons(answer, [("si", "Sí"), ("no", "Otro distrito")])
        if step == "health_center":
            return _buttons(answer, [("no lo se", "No lo sé"), ("cancelar", "Cancelar")])
        if step == "consent":
            return _yes_no(answer)

    if flow == "quick_registration" and step == "consent":
        return _yes_no(answer)

    if flow == "first_measurement_offer":
        return _yes_no(answer)

    if flow == "measurement":
        if step == "select_child":
            return _children_list(answer, data.get("children") or [])
        if step == "height_mode":
            return _buttons(answer, [("acostado", "Acostado/a"), ("parado", "De pie")])
        if step == "muac":
            return _buttons(answer, [("omitir", "No tengo cinta"), ("cancelar", "Cancelar")])
        if step in {"edema", "confirm"}:
            return _yes_no(answer)

    if flow == "health_center_update":
        if step == "select_child":
            return _children_list(answer, data.get("children") or [])
        if step == "value":
            return _buttons(answer, [("no lo se", "No lo sé"), ("cancelar", "Cancelar")])

    if flow == "followup":
        if step == "select_alert":
            alerts = data.get("alerts") or []
            rows = [
                (
                    str(index),
                    (alert.get("child") or {}).get("full_name") or f"Alerta {index}",
                    f"Nivel {str(alert.get('nivel') or '').upper()}",
                )
                for index, alert in enumerate(alerts[:10], 1)
            ]
            return _list(answer, rows, button_text="Elegir alerta", section_title="Alertas")
        if step == "action":
            return _list(
                answer,
                [
                    ("1", "Ver establecimiento", "Centro registrado"),
                    ("2", "Planeo acudir", "Indicar cuándo podría ir"),
                    ("3", "Ya acudimos", "Informar la asistencia"),
                    ("4", "Necesito ayuda", "Registrar una dificultad"),
                    ("5", "Recomendaciones", "Orientación segura en casa"),
                ],
                button_text="Elegir acción",
                section_title="Seguimiento",
            )
        if step == "attendance_plan":
            return _buttons(answer, [("1", "Hoy"), ("2", "Próximos 7 días"), ("3", "Aún no lo sé")])
        if step == "barrier":
            return _list(
                answer,
                [
                    ("1", "No consigo cita", "Dificultad para reservar"),
                    ("2", "Está lejos", "Distancia al establecimiento"),
                    ("3", "Transporte o costo", "Dificultad económica"),
                    ("4", "Horarios", "No coinciden con mi tiempo"),
                    ("5", "No sé dónde ir", "Necesito orientación"),
                    ("6", "Otro motivo", "Otra dificultad"),
                ],
                button_text="Elegir motivo",
                section_title="Dificultad principal",
            )

    if flow == "supplement":
        if step == "select_child":
            return _children_list(answer, data.get("children") or [])
        if step.startswith("select_plan_"):
            plans = data.get("plans") or []
            rows = [
                (
                    str(index),
                    _SUPPLEMENT_LABELS.get(plan.get("supplement_type"), "Suplemento"),
                    "Seleccionar este plan",
                )
                for index, plan in enumerate(plans[:10], 1)
            ]
            return _list(answer, rows, button_text="Elegir", section_title="Suplementos")
        if step == "action":
            return _list(
                answer,
                [
                    ("1", "Condición", "Registrar diagnóstico reportado"),
                    ("2", "Suplemento", "Registrar indicación"),
                    ("3", "Toma de hoy", "Marcar si lo recibió"),
                    ("4", "Últimos 7 días", "Ver resumen de tomas"),
                    ("5", "Recordatorio", "Configurar aviso diario"),
                ],
                button_text="Elegir acción",
                section_title="Suplementación",
            )
        if step == "supplement_type":
            return _list(
                answer,
                [
                    ("1", "Hierro", "Sulfato ferroso u otro"),
                    ("2", "MNP", "Micronutrientes en polvo"),
                    ("3", "Vitamina A", "Indicada por salud"),
                    ("4", "Zinc", "Indicado por salud"),
                    ("5", "Vitamina D", "Indicada por salud"),
                    ("6", "Otro", "Otro suplemento indicado"),
                    ("7", "No toma", "Sin suplemento actualmente"),
                ],
                button_text="Elegir suplemento",
                section_title="Suplementos",
            )
        if step == "supplement_purpose":
            return _buttons(answer, [("1", "Prevención"), ("2", "Tratamiento"), ("3", "No lo sé")])
        if step in {"diagnosed_by", "condition_date", "indicated_by"}:
            return _buttons(answer, [("no lo se", "No lo sé"), ("cancelar", "Cancelar")])
        if step in {"supplement_confirm", "reminder_enabled"}:
            return _yes_no(answer)
        if step == "intake":
            return _buttons(answer, [("si", "Sí, lo tomó"), ("no", "No lo tomó"), ("3", "Aún no")])
        if step == "not_taken_reason":
            return _list(
                answer,
                [
                    ("1", "Lo olvidé", "Olvido de la toma"),
                    ("2", "Se terminó", "No queda suplemento"),
                    ("3", "No quiso tomarlo", "La niña o niño lo rechazó"),
                    ("4", "Tuvo molestia", "Presentó una reacción"),
                    ("5", "No entendí", "Indicación poco clara"),
                    ("6", "Otro motivo", "Otra razón"),
                ],
                button_text="Elegir motivo",
                section_title="Motivo",
            )

    return None


def interactive_payload(presentation: Presentation) -> dict:
    """Convierte una presentación al cuerpo REST esperado por Kapso/Meta."""
    if presentation.kind == "buttons":
        action = {
            "buttons": [
                {
                    "type": "reply",
                    "reply": {"id": option.id, "title": option.title},
                }
                for option in presentation.options[:3]
            ]
        }
    else:
        action = {
            "button": presentation.button_text,
            "sections": [
                {
                    "title": presentation.section_title,
                    "rows": [
                        {
                            "id": option.id,
                            "title": option.title,
                            **({"description": option.description} if option.description else {}),
                        }
                        for option in presentation.options[:10]
                    ],
                }
            ],
        }
    return {
        "type": "interactive",
        "interactive": {
            "type": "button" if presentation.kind == "buttons" else "list",
            "body": {"text": presentation.body},
            "action": action,
        },
    }
