"""Clasificador de intención y FAQ con e5 opcional y fallback léxico."""

from __future__ import annotations

import re

from . import config

FAQS = [
    {
        "id": "semaforo",
        "intent": "faq",
        "keywords": ["semaforo", "verde", "amarillo", "rojo", "color", "significa"],
        "question": "¿Qué significan los colores del semáforo?",
        "answer": (
            "🟢 Verde: no se activó un umbral de alerta con los datos disponibles.\n"
            "🟡 Amarillo: conviene confirmar la medición y coordinar control.\n"
            "🔴 Rojo: requiere priorización por el establecimiento de salud. "
            "El semáforo orienta; no reemplaza una evaluación profesional.\n\n"
            "*Ejemplos para entenderlo*\n"
            "🟢 Verde: niña de 2 años, 11.5 kg, 85 cm, MUAC 135 mm y sin edema.\n"
            "🟡 Amarillo: niña de 1 año, 8.9 kg, 74 cm y MUAC 120 mm.\n"
            "🔴 Rojo: niño de 2 años, 12 kg, 86 cm y edema bilateral reportado.\n\n"
            "Son ejemplos educativos calculados con el motor del sistema; cada caso debe "
            "evaluarse con sus propios datos."
        ),
    },
    {
        "id": "medir_peso",
        "intent": "measurement",
        "keywords": ["peso", "balanza", "pesar"],
        "question": "¿Cómo mido el peso?",
        "answer": (
            "Pon la balanza en una superficie firme, verifica que marque cero y retira zapatos y ropa pesada. "
            "Registra el valor en kg con un decimal. Si el resultado sorprende, repite la medición."
        ),
    },
    {
        "id": "medir_talla",
        "intent": "measurement",
        "keywords": ["talla", "altura", "longitud", "acostado", "parado", "medir"],
        "question": "¿Cómo mido la longitud o talla?",
        "answer": (
            "Antes de los 2 años se recomienda longitud acostado/a con infantómetro; desde los 2 años, talla de pie. "
            "Cabeza, tronco y piernas deben quedar alineados. Una cinta de costura no sustituye el equipo validado."
            f"\n\nTutorial del Instituto Nacional de Salud para longitud en menores de 2 años:\n"
            f"{config.TUTORIAL_LONGITUD_URL}"
        ),
    },
    {
        "id": "muac",
        "intent": "measurement",
        "keywords": ["muac", "brazo", "perimetro", "circunferencia", "cinta"],
        "question": "¿Qué es el MUAC?",
        "answer": (
            "Es el perímetro del brazo medio. Para niñas y niños de 6 a 59 meses se usa una cinta MUAC adecuada: "
            "ubica el punto medio entre hombro y codo, rodea sin apretar y registra milímetros."
        ),
    },
    {
        "id": "edema",
        "intent": "measurement",
        "keywords": ["edema", "hinchado", "hinchados", "pies", "presionar"],
        "question": "¿Qué es edema bilateral?",
        "answer": (
            "Es hinchazón en ambos pies que deja una marca al presionar suavemente. "
            "Si lo observas, no esperes al bot: busca valoración presencial el mismo día."
        ),
    },
    {
        "id": "privacidad",
        "intent": "faq",
        "keywords": ["datos", "privacidad", "seguro", "consentimiento"],
        "question": "¿Cómo se usan mis datos?",
        "answer": (
            "Se registran para acompañar la trayectoria de crecimiento y priorizar alertas para el personal autorizado. "
            "No compartas documentos ni información clínica innecesaria por WhatsApp."
        ),
    },
]

_model = None
_embeddings = None


def _clean(text: str) -> str:
    return re.sub(r"[^a-záéíóúñ0-9 ]", " ", str(text).lower())


def detectar_accion(texto: str) -> str | None:
    """Detecta acciones explícitas sin delegar cambios de estado al LLM."""
    text = _clean(texto)
    if any(
        phrase in text
        for phrase in (
            "revisar seguimiento",
            "ver seguimiento",
            "ver alerta",
            "necesito ayuda para acudir",
            "no pude acudir",
            "ya acudimos",
        )
    ):
        return "followup"
    health_center_terms = ("establecimiento", "centro de salud", "posta", "clinica")
    health_center_verbs = ("agregar", "añadir", "cambiar", "actualizar", "registrar", "vincular")
    if any(term in text for term in health_center_terms) and any(
        verb in text for verb in health_center_verbs
    ):
        return "health_center"
    measurement_terms = ("medicion", "medición", "peso", "talla", "altura", "longitud")
    measurement_verbs = ("registrar", "anotar", "guardar", "ingresar", "tomar", "hacer", "medir")
    if any(term in text for term in measurement_terms) and any(
        verb in text for verb in measurement_verbs
    ):
        return "measurement"
    registration_subjects = ("niño", "nino", "niña", "nina", "hijo", "hija", "menor")
    if any(verb in text for verb in ("registrar", "inscribir", "agregar", "añadir")) and any(
        subject in text for subject in registration_subjects
    ):
        return "registration"
    if any(phrase in text for phrase in ("ver crecimiento", "ver resultado", "ver trayectoria", "consultar estado")):
        return "status"
    return None


def init() -> None:
    global _model, _embeddings
    if not config.ENABLE_E5_CLASSIFIER or _model is not None:
        return
    try:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(config.EMBED_MODEL)
        passages = [f"passage: {item['question']} {' '.join(item['keywords'])}" for item in FAQS]
        _embeddings = _model.encode(passages, normalize_embeddings=True)
        print(f"[clasificador] e5 activo: {config.EMBED_MODEL}")
    except Exception as exc:
        _model = None
        _embeddings = None
        print(f"[clasificador] e5 no disponible; se usa fallback léxico: {exc}")


def clasificar(texto: str) -> dict:
    normalized = _clean(texto)
    if any(word in normalized for word in ("registrar niño", "registrar nina", "nuevo niño", "nuevo hijo")):
        return {"intent": "registration", "confidence": 1.0, "faq": None}
    if any(word in normalized.split() for word in ("medicion", "medición", "peso", "talla", "muac")):
        default_intent = "measurement"
    elif any(word in normalized.split() for word in ("estado", "trayectoria", "resultado")):
        return {"intent": "status", "confidence": 0.95, "faq": None}
    else:
        default_intent = "faq"

    lexical = []
    words = set(normalized.split())
    for item in FAQS:
        overlap = len(words.intersection(item["keywords"]))
        lexical.append(overlap / max(1, len(set(item["keywords"]))))

    scores = lexical
    if _model is not None and _embeddings is not None:
        query = _model.encode([f"query: {texto}"], normalize_embeddings=True)[0]
        semantic = _embeddings @ query
        scores = [0.65 * float(semantic[i]) + 0.35 * lexical[i] for i in range(len(FAQS))]
    best = max(range(len(scores)), key=scores.__getitem__)
    faq = FAQS[best] if scores[best] > 0 else None
    return {
        "intent": faq["intent"] if faq else default_intent,
        "confidence": round(float(scores[best]), 3),
        "faq": faq,
    }
