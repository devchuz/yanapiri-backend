"""Fachada compatible con el agente anterior.

El registro clínico usa un flujo determinista. Un LLM puede añadirse después para
redacción o FAQ, pero nunca controla cálculos ni el semáforo.
"""

from ..core import config
from ..services.bot import respond


def init() -> None:
    return None


def modo() -> str:
    return "hibrido-groq-oms" if config.usando_groq() else "flujo-determinista-oms"


def responder(mensaje: str, telefono: str) -> str:
    return respond(mensaje, telefono)
