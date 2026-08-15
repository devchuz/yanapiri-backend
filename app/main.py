"""API de NutriAcompaña: FastAPI, cola FIFO por identidad y webhook Kapso v2."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import random
import re
from contextlib import asynccontextmanager
from datetime import date, datetime
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .agent import yachay_agent
from .core import clasificador, config, db
from .domain.anthropometry import AnthropometryError, assess_child

_SEM = asyncio.Semaphore(config.MAX_CONCURRENT_MESSAGES)
_queues: dict[str, asyncio.Queue[str]] = {}
_active_workers: set[str] = set()


@asynccontextmanager
async def lifespan(_: FastAPI):
    config.validar_entorno()
    clasificador.init()
    yachay_agent.init()
    print(
        "[nutriacompana] listo | "
        f"env={config.APP_ENV} modo={yachay_agent.modo()} supabase={db.usando_supabase()} "
        f"gateway={'kapso' if config.usando_kapso() else 'mock'}"
    )
    yield
    _queues.clear()
    _active_workers.clear()


app = FastAPI(
    title="NutriAcompaña API",
    version="0.1.0",
    description="Canal familiar de Yanapiri Wawa para el reto 5 Crecer Mejor.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Webhook-Event", "X-Webhook-Signature"],
)


class ChatIn(BaseModel):
    mensaje: str
    identidad: str = "demo-familia"


class AssessmentIn(BaseModel):
    birth_date: date
    measured_at: date = Field(default_factory=date.today)
    sex: str
    weight_kg: float
    height_cm: float
    height_mode: str
    muac_mm: float | None = None
    bilateral_edema: bool = False


class ClinicalMeasurementIn(BaseModel):
    measured_at: date = Field(default_factory=date.today)
    weight_kg: float
    height_cm: float
    height_mode: str
    muac_mm: float | None = None
    bilateral_edema: bool = False


class AppointmentIn(BaseModel):
    scheduled_at: datetime
    appointment_type: str = "growth_control"
    notes: str | None = Field(default=None, max_length=500)


class AppointmentStatusIn(BaseModel):
    status: str


class ClinicalQuestionIn(BaseModel):
    question: str = Field(min_length=2, max_length=500)


def _clinical_professional(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Usa Authorization: Bearer <token de Supabase>.")
    token = authorization.split(" ", 1)[1].strip()
    try:
        return db.autenticar_profesional(token)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _clinical_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, ValueError):
        message = str(exc)
        status_code = 404 if "no encontr" in message.lower() else 422
        return HTTPException(status_code=status_code, detail=message)
    return HTTPException(status_code=503, detail=str(exc))


def _clinical_history_answer(question: str, history: dict, appointments: list[dict]) -> dict:
    """Responde preguntas frecuentes sin delegar cálculos o diagnósticos al LLM."""
    normalized = " ".join(question.lower().split())
    verified = history.get("verified_trajectory") or []
    reported = history.get("reported_trajectory") or []
    active_alerts = history.get("active_alerts") or []
    if any(word in normalized for word in ("cita", "control", "visita")):
        upcoming = [item for item in appointments if item.get("status") in {"scheduled", "confirmed"}]
        answer = (
            f"Hay {len(upcoming)} cita(s) pendiente(s)."
            if upcoming
            else "No hay citas pendientes registradas."
        )
        scope = "appointments"
    elif any(word in normalized for word in ("alerta", "riesgo", "prioridad")):
        clinical = [item for item in active_alerts if item.get("alert_type") == "clinical_alert"]
        verification = [
            item for item in active_alerts if item.get("alert_type") != "clinical_alert"
        ]
        answer = (
            f"Hay {len(clinical)} alerta(s) clínica(s) y "
            f"{len(verification)} solicitud(es) de verificación activas."
        )
        scope = "alerts"
    elif any(word in normalized for word in ("casa", "cuidador", "familia", "reportada")):
        latest = reported[0] if reported else None
        answer = (
            f"Hay {len(reported)} medición(es) familiares. La más reciente es del "
            f"{latest['measured_at']}: {latest['weight_kg']} kg y {latest['height_cm']} cm."
            if latest
            else "No hay mediciones familiares registradas."
        )
        scope = "reported_measurements"
    elif any(
        word in normalized
        for word in ("verificada", "clinica", "clínica", "personal", "ultima", "última")
    ):
        latest = verified[0] if verified else None
        answer = (
            f"La última medición clínica verificada es del {latest['measured_at']}: "
            f"{latest['weight_kg']} kg y {latest['height_cm']} cm."
            if latest
            else "Aún no existe una medición clínica verificada."
        )
        scope = "verified_measurements"
    else:
        answer = (
            f"Historial disponible: {len(verified)} medición(es) clínica(s), "
            f"{len(reported)} medición(es) familiares, {len(active_alerts)} alerta(s) activa(s) "
            f"y {len(appointments)} cita(s)."
        )
        scope = "summary"
    return {
        "answer": answer,
        "scope": scope,
        "disclaimer": "Resumen informativo del registro; no sustituye el juicio clínico.",
    }


def _recortar(text: str, limite: int = 80) -> str:
    """Texto para logs: una sola línea y acotado.

    Los mensajes traen datos de salud de menores, así que el log no debería
    guardar la conversación completa.
    """
    plano = " ".join(str(text).split())
    return plano if len(plano) <= limite else plano[:limite] + "..."


def _ofuscar(identity: str) -> str:
    """Muestra solo los últimos 4 dígitos del número en los logs."""
    return f"*{identity[-4:]}" if len(identity) > 4 else identity


def _identity(value: str) -> str:
    value = str(value or "").strip()
    if value.endswith("@s.whatsapp.net"):
        value = value.split("@", 1)[0]
    if value.startswith("+") and value[1:].isdigit():
        value = value[1:]
    return value


def _is_bsuid(value: str) -> bool:
    """Reconoce BSUIDs regulares y parent BSUIDs (PE.123 o PE.ENT.123)."""
    return bool(re.fullmatch(r"[A-Z]{2}(?:\.[A-Z]+)*\.\d+", str(value or "")))


async def _send_whatsapp(recipient: str, text: str) -> bool:
    recipient = _identity(recipient)
    # Los teléfonos se envían en ``to``; los BSUID se envían en ``recipient``.
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "type": "text",
        "text": {"body": text},
    }
    if recipient.isdigit():
        payload["to"] = f"+{recipient}"
        destination_kind = "to"
    elif _is_bsuid(recipient):
        payload["recipient"] = recipient
        destination_kind = "recipient"
    else:
        print(f"[kapso] identidad de destino no soportada: {_ofuscar(recipient)}")
        return False
    if not config.usando_kapso():
        preview = _recortar(text) if config.LOG_MESSAGE_CONTENT else "[contenido oculto]"
        print(f"[kapso-mock] {destination_kind}={_ofuscar(recipient)}: {preview}")
        return True
    url = (
        f"{config.KAPSO_API_URL}/meta/whatsapp/v24.0/"
        f"{config.KAPSO_PHONE_NUMBER_ID}/messages"
    )
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            # Kapso puede entregar el webhook antes de terminar de actualizar
            # la ventana de atención. Reintentamos solo ese 422 transitorio.
            for attempt, delay in enumerate((0, 2, 5)):
                if delay:
                    await asyncio.sleep(delay)
                response = await client.post(
                    url,
                    headers={
                        "X-API-Key": config.KAPSO_API_KEY,
                        "X-Idempotency-Key": str(uuid4()),
                    },
                    json=payload,
                )
                try:
                    response.raise_for_status()
                    return True
                except httpx.HTTPStatusError as exc:
                    window_pending = (
                        exc.response.status_code == 422
                        and "outside the 24-hour window" in exc.response.text
                    )
                    if window_pending and attempt < 2:
                        next_delay = (2, 5)[attempt]
                        print(
                            "[kapso] ventana aún no sincronizada para "
                            f"{_ofuscar(recipient)}; reintento en {next_delay}s"
                        )
                        continue
                    # El motivo real viene en el cuerpo, no solo en el código.
                    print(
                        f"[kapso] rechazo {exc.response.status_code} para "
                        f"{_ofuscar(recipient)}: {exc.response.text[:300]}"
                    )
                    return False
        return False
    except httpx.HTTPError as exc:
        print(f"[kapso] no se pudo enviar a {_ofuscar(recipient)}: {exc!r}")
        return False


async def _process(identity: str, text: str) -> None:
    async with _SEM:
        try:
            answer = await asyncio.to_thread(yachay_agent.responder, text, identity)
        except Exception as exc:
            print(f"[bot] error para {_ofuscar(identity)}: {exc!r}")
            answer = "No pude procesar el mensaje. Intenta nuevamente o escribe AYUDA."
        delay = random.uniform(
            config.BOT_REPLY_DELAY_MIN_SECONDS,
            config.BOT_REPLY_DELAY_MAX_SECONDS,
        )
        if delay > 0:
            print(f"[bot] preparando respuesta para {_ofuscar(identity)} ({delay:.1f}s)")
            await asyncio.sleep(delay)
        enviado = await _send_whatsapp(identity, answer)
        estado = "enviado" if enviado else "FALLO el envio"
        preview = _recortar(answer) if config.LOG_MESSAGE_CONTENT else "[contenido oculto]"
        print(f"[bot] {_ofuscar(identity)} <- {estado}: {preview}")


async def _worker(identity: str) -> None:
    queue = _queues[identity]
    try:
        while True:
            try:
                text = await asyncio.wait_for(queue.get(), timeout=60)
            except asyncio.TimeoutError:
                break
            try:
                await _process(identity, text)
            finally:
                queue.task_done()
    finally:
        _active_workers.discard(identity)
        _queues.pop(identity, None)


async def _enqueue(identity: str, text: str) -> bool:
    if identity not in _queues:
        _queues[identity] = asyncio.Queue(maxsize=10)
    queue = _queues[identity]
    if queue.full():
        print(f"[queue] cola llena para {_ofuscar(identity)}")
        return False
    await queue.put(text)
    if identity not in _active_workers:
        _active_workers.add(identity)
        asyncio.create_task(_worker(identity))
    return True


def _valid_signature(raw_body: bytes, signature: str) -> bool:
    if not config.KAPSO_WEBHOOK_SECRET:
        return True
    if not signature:
        return False
    expected = hmac.new(
        config.KAPSO_WEBHOOK_SECRET.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    received = signature.split("=", 1)[-1].strip()
    return hmac.compare_digest(expected, received)


def _extract_kapso(event: dict) -> tuple[str, str, str] | None:
    message = event.get("message") or {}
    kapso = message.get("kapso") or {}
    if kapso.get("direction") == "outbound":
        return None
    conversation = event.get("conversation") or {}
    # Para responder, Kapso/Meta exige priorizar el teléfono recibido en
    # ``message.from``. El BSUID es el fallback cuando Meta oculta el número.
    # Elegir el BSUID teniendo también el teléfono puede dejar la respuesta
    # fuera de la ventana asociada a la conversación telefónica.
    identity = (
        message.get("from")
        or conversation.get("phone_number")
        or message.get("from_user_id")
        or conversation.get("business_scoped_user_id")
    )
    text = ""
    if message.get("type") == "text":
        text = (message.get("text") or {}).get("body", "")
    elif message.get("type") == "interactive":
        interactive = message.get("interactive") or {}
        text = (
            (interactive.get("button_reply") or {}).get("title")
            or (interactive.get("list_reply") or {}).get("title")
            or ""
        )
    elif message.get("type") == "audio":
        text = ((kapso.get("transcript") or {}).get("text") or "")
    identity = _identity(identity)
    if not identity or not (identity.isdigit() or _is_bsuid(identity)):
        return None
    if not str(text).strip():
        return None
    return identity, str(text).strip(), str(message.get("id") or "")


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "service": "nutriacompana",
        "environment": config.APP_ENV,
        "agent": yachay_agent.modo(),
        "llm": "groq" if config.usando_groq() else "disabled",
        "database": "supabase" if db.usando_supabase() else "memory",
        "gateway": "kapso" if config.usando_kapso() else "mock",
        "webhook_signature_configured": bool(config.KAPSO_WEBHOOK_SECRET),
        "active_queues": len(_queues),
    }


@app.post("/chat")
def chat(payload: ChatIn) -> dict:
    return {"respuesta": yachay_agent.responder(payload.mensaje, _identity(payload.identidad))}


@app.post("/assessments/preview")
def preview_assessment(payload: AssessmentIn) -> dict:
    try:
        result = assess_child(**payload.model_dump()).to_dict()
    except AnthropometryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"assessment": result, "disclaimer": "Orientación; no constituye diagnóstico médico."}


@app.get("/clinical/children/{child_id}/history")
def clinical_history(
    child_id: str, professional: dict = Depends(_clinical_professional)
) -> dict:
    try:
        db.verificar_acceso_profesional(professional["user_id"], child_id)
        history = db.consultar_estado(child_id)
        appointments = db.listar_citas(
            child_id=child_id, professional_user_id=professional["user_id"]
        )
    except Exception as exc:
        raise _clinical_http_error(exc) from exc
    return {**(history or {}), "appointments": appointments}


@app.post("/clinical/children/{child_id}/measurements")
def create_clinical_measurement(
    child_id: str,
    payload: ClinicalMeasurementIn,
    professional: dict = Depends(_clinical_professional),
) -> dict:
    inputs = {
        "child_id": child_id,
        "measured_at": payload.measured_at.isoformat(),
        "weight_kg": payload.weight_kg,
        "height_cm": payload.height_cm,
        "height_mode": payload.height_mode,
        "muac_mm": payload.muac_mm,
        "bilateral_edema": payload.bilateral_edema,
        "source": "health_worker",
        "recorded_by_user_id": professional["user_id"],
    }
    try:
        return db.registrar_medicion(**inputs)
    except AnthropometryError as exc:
        try:
            return db.registrar_medicion_para_revision(
                **inputs, validation_notes=str(exc)
            )
        except Exception as storage_exc:
            raise _clinical_http_error(storage_exc) from storage_exc
    except Exception as exc:
        raise _clinical_http_error(exc) from exc


@app.get("/clinical/children/{child_id}/appointments")
def clinical_appointments(
    child_id: str, professional: dict = Depends(_clinical_professional)
) -> dict:
    try:
        rows = db.listar_citas(
            child_id=child_id, professional_user_id=professional["user_id"]
        )
    except Exception as exc:
        raise _clinical_http_error(exc) from exc
    return {"appointments": rows}


@app.post("/clinical/children/{child_id}/appointments")
def create_clinical_appointment(
    child_id: str,
    payload: AppointmentIn,
    professional: dict = Depends(_clinical_professional),
) -> dict:
    try:
        row = db.registrar_cita(
            child_id=child_id,
            professional_user_id=professional["user_id"],
            scheduled_at=payload.scheduled_at.isoformat(),
            appointment_type=payload.appointment_type,
            notes=payload.notes,
        )
    except Exception as exc:
        raise _clinical_http_error(exc) from exc
    return {"appointment": row}


@app.patch("/clinical/appointments/{appointment_id}")
def update_clinical_appointment(
    appointment_id: str,
    payload: AppointmentStatusIn,
    professional: dict = Depends(_clinical_professional),
) -> dict:
    try:
        row = db.actualizar_estado_cita(
            appointment_id=appointment_id,
            professional_user_id=professional["user_id"],
            status=payload.status,
        )
    except Exception as exc:
        raise _clinical_http_error(exc) from exc
    return {"appointment": row}


@app.post("/clinical/children/{child_id}/ask")
def ask_clinical_history(
    child_id: str,
    payload: ClinicalQuestionIn,
    professional: dict = Depends(_clinical_professional),
) -> dict:
    try:
        db.verificar_acceso_profesional(professional["user_id"], child_id)
        history = db.consultar_estado(child_id) or {}
        appointments = db.listar_citas(
            child_id=child_id, professional_user_id=professional["user_id"]
        )
    except Exception as exc:
        raise _clinical_http_error(exc) from exc
    return _clinical_history_answer(payload.question, history, appointments)


@app.post("/webhooks/kapso")
@app.post("/webhook/whatsapp", include_in_schema=False)
async def kapso_webhook(request: Request) -> dict:
    raw = await request.body()
    if not raw:
        return {"ok": True, "accepted": 0}
    if not _valid_signature(raw, request.headers.get("x-webhook-signature", "")):
        raise HTTPException(status_code=401, detail="Firma de webhook inválida")
    event_type = request.headers.get("x-webhook-event", "")
    if event_type and event_type != "whatsapp.message.received":
        return {"ok": True, "accepted": 0}
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="JSON inválido") from exc
    events = body.get("data", []) if body.get("batch") is True else [body]
    accepted = 0
    for event in events:
        extracted = _extract_kapso(event)
        if not extracted:
            message = event.get("message") or {}
            conversation = event.get("conversation") or {}
            tipo = message.get("type") or "?"
            has_bsuid = bool(
                message.get("from_user_id")
                or conversation.get("business_scoped_user_id")
            )
            has_phone = bool(message.get("from") or conversation.get("phone_number"))
            reason = (
                "BSUID invalido o no soportado"
                if has_bsuid and not has_phone
                else "payload no extraible"
            )
            print(f"[webhook] ignorado ({reason}, type={tipo})")
            continue
        identity, text, message_id = extracted
        route_kind = "telefono" if identity.isdigit() else "BSUID"
        preview = _recortar(text) if config.LOG_MESSAGE_CONTENT else "[contenido oculto]"
        print(
            f"[webhook] entra via={route_kind} {_ofuscar(identity)} "
            f"id={message_id or '?'}: {preview}"
        )
        if not db.registrar_evento_webhook(message_id, event_type or "whatsapp.message.received", event):
            print(f"[webhook] duplicado, ya procesado (id={message_id or '?'})")
            continue
        if await _enqueue(identity, text):
            accepted += 1
    # Se responde de inmediato; el worker procesa y contesta fuera del webhook.
    return {"ok": True, "accepted": accepted}
