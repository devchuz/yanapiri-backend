"""Barreras deterministas de seguridad clínica, privacidad y salida del LLM."""

from __future__ import annotations

import re


_DANGER_PATTERNS = (
    r"\b(?:no puede|no puede bien|le cuesta)\s+respirar\b",
    r"\bno\s+respira\b",
    r"\brespira\s+con\s+dificultad\b",
    r"\b(?:esta|está)\s+convulsionando\b",
    r"\btiene\s+convulsiones\b",
    r"\b(?:convulsiono|convulsionó)\b",
    r"\b(?:esta|está)\s+inconsciente\b",
    r"\bno\s+(?:despierta|responde)\b",
    r"\bno\s+puede\s+(?:beber|tomar|lactar)\b",
    r"\bvomita\s+(?:todo|persistentemente|sin parar)\b",
    r"\b(?:ambos|los dos)\s+pies\s+(?:estan|están)?\s*hinchados\b",
    r"\b(?:hinchazon|hinchazón)\b.{0,25}\b(?:ambos|los dos)\s+pies\b",
)

_UNSAFE_LLM_OUTPUT = (
    r"\b(?:waz|haz|whz|z[- ]?score)\b",
    r"\b(?:dale|administra|suministra)\b.{0,30}\b\d+(?:[.,]\d+)?\s*(?:mg|ml|gotas?)\b",
    r"\b(?:tu (?:hijo|hija|niño|niña)|el niño|la niña)\s+(?:tiene|padece)\s+"
    r"(?:desnutricion|desnutrición|anemia|obesidad|una enfermedad)\b",
)


def danger_response(message: str) -> str | None:
    """Interrumpe preguntas generales cuando se describen signos de peligro."""
    text = str(message or "").lower()
    # No convertir respuestas negativas del formulario en emergencias. Esto no
    # afecta expresiones como "no puede respirar", que sí son signos de peligro.
    text_for_detection = re.sub(
        r"\b(?:no\s+(?:tiene|presenta|hay|observo|observa)|sin)\b.{0,35}"
        r"\b(?:hinchazon|hinchazón|pies?\s+hinchados?)\b",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    text_for_detection = re.sub(
        r"\b(?:ambos|los dos)\s+pies\s+no\s+(?:estan|están)\s+hinchados\b",
        " ",
        text_for_detection,
        flags=re.IGNORECASE,
    )
    if not any(
        re.search(pattern, text_for_detection, flags=re.IGNORECASE)
        for pattern in _DANGER_PATTERNS
    ):
        return None
    return (
        "🚨 *Busca atención presencial de inmediato.*\n\n"
        "Lo que describes puede ser un signo de peligro. No esperes una respuesta del bot ni "
        "intentes resolverlo solo en casa. Acude al establecimiento de salud o servicio de "
        "emergencia más cercano.\n\n"
        "Esta orientación no constituye un diagnóstico."
    )


def sanitize_for_llm(message: str, known_names: list[str] | None = None) -> str:
    """Minimiza datos personales antes de enviar una consulta a un tercero."""
    text = str(message or "")[:800]
    for name in sorted(known_names or [], key=len, reverse=True):
        clean_name = str(name or "").strip()
        if len(clean_name) >= 2:
            text = re.sub(re.escape(clean_name), "[NOMBRE OMITIDO]", text, flags=re.IGNORECASE)
    text = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "[CORREO OMITIDO]", text)
    text = re.sub(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", "[FECHA OMITIDA]", text)
    text = re.sub(r"\b20\d{2}-\d{1,2}-\d{1,2}\b", "[FECHA OMITIDA]", text)
    text = re.sub(
        r"(?<!\d)(?:\+?\d[\s.-]?){8,15}(?!\d)",
        "[NÚMERO OMITIDO]",
        text,
    )
    return " ".join(text.split())


def validate_llm_output(content: str) -> str | None:
    """Descarta respuestas que invaden decisiones reservadas al motor o al clínico."""
    text = str(content or "").strip()
    if not text:
        return None
    if any(re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL) for pattern in _UNSAFE_LLM_OUTPUT):
        return None
    return text[:1500]


LLM_SAFETY_FALLBACK = (
    "Para proteger a la niña o niño no puedo emitir diagnósticos ni indicar dosis. "
    "Puedo darte orientación general, pero las decisiones clínicas deben ser confirmadas "
    "por personal de salud. Si describes un signo de peligro, busca atención presencial."
)
