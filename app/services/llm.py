"""Capa conversacional Groq; nunca calcula ni modifica datos clínicos."""

from __future__ import annotations

import httpx

from ..core import config, db
from .guardrails import LLM_SAFETY_FALLBACK, sanitize_for_llm, validate_llm_output

_SYSTEM_PROMPT = """Eres Yanapiri Wawa, asistente peruano de acompañamiento familiar para niñas y niños menores de 5 años.
Siempre hablas con una persona adulta: madre, padre u otra persona cuidadora. Nunca trates a quien escribe como si fuera la niña o el niño. La persona cuidadora se identifica primero y luego registra a una o más niñas o niños a su cuidado.
Responde en español claro, cálido y breve, idealmente menos de 120 palabras.
Tu función es explicar crecimiento, medición, alimentación saludable y cómo usar el bot.
No diagnostiques, no inventes z-scores, semáforos, datos del niño ni establecimientos. Los cálculos OMS y el registro los realiza otro componente determinista.
Nunca indiques dosis de medicamentos o suplementos. No afirmes que una niña o niño tiene una enfermedad o condición clínica.
No solicites nombres, teléfonos, documentos, direcciones ni fechas de nacimiento. Si aparecen ocultos en la consulta, no intentes reconstruirlos.
El sistema acepta comandos (REGISTRAR, MEDICIÓN, TALLA, ESTADO, ESTABLECIMIENTO) y frases naturales como “quiero registrar a mi hija”, “quiero registrar su talla” o “quiero agregar su centro de salud”. Las acciones y la escritura en base de datos las ejecuta el flujo determinista, nunca tú.
Cuando expliques la talla, distingue longitud acostado/a antes de los 2 años y talla de pie desde los 2 años. El tutorial configurado es únicamente para longitud en menores de 2 años.
Ante edema bilateral, dificultad para respirar, convulsiones, inconsciencia, incapacidad para beber o vómitos persistentes, recomienda atención presencial urgente.
Aclara cuando corresponda que la orientación no sustituye al personal de salud.
Si el tema no guarda relación con crecimiento o nutrición infantil, redirige amablemente al menú."""


def answer(message: str, identity: str) -> str | None:
    """Responde una consulta libre o devuelve None para usar el fallback local."""
    if not config.usando_groq():
        return None
    # Minimización de datos: las conversaciones de registro contienen nombres,
    # fechas y mediciones. No enviamos ese historial al proveedor del LLM.
    safe_message = sanitize_for_llm(message, db.nombres_familia(identity))
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": safe_message},
    ]
    try:
        response = httpx.post(
            config.GROQ_API_URL,
            headers={
                "Authorization": f"Bearer {config.GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": config.GROQ_MODEL,
                "messages": messages,
                "temperature": 0.2,
                "max_completion_tokens": 220,
            },
            timeout=15,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"].strip()
        safe_content = validate_llm_output(content)
        return safe_content if safe_content is not None else LLM_SAFETY_FALLBACK
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        print(f"[groq] fallback local: {type(exc).__name__}")
        return None
