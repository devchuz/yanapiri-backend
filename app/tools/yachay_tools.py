"""Tools de dominio reutilizables por el bot y por futuros agentes LangGraph."""

from __future__ import annotations

from ..core import clasificador, db


def registrar_nino(**datos) -> dict:
    return db.registrar_nino(**datos)


def registrar_medicion(**datos) -> dict:
    return db.registrar_medicion(**datos)


def consultar_estado(child_ref: str, whatsapp_identity: str | None = None) -> dict | None:
    return db.consultar_estado(child_ref, whatsapp_identity)


def consultar_alertas_familia(whatsapp_identity: str) -> list[dict]:
    return db.alertas_activas_familia(whatsapp_identity)


def registrar_seguimiento_cuidador(**datos) -> dict:
    """No permite resolver ni reclasificar una alerta desde el agente."""
    return db.registrar_evento_seguimiento_cuidador(**datos)


def orientar_servicios(pregunta: str) -> str:
    result = clasificador.clasificar(pregunta)
    if result.get("faq"):
        return result["faq"]["answer"]
    return (
        "Puedo explicar el semáforo o guiarte para medir peso, talla y MUAC. "
        "Escribe AYUDA para ver las opciones."
    )
