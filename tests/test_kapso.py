import asyncio
import hashlib
import hmac

import httpx

from app import main
from app.core import config, db


def test_extracts_text_event():
    event = {
        "message": {
            "id": "wamid.123",
            "type": "text",
            "from": "51999999999",
            "text": {"body": "Hola"},
            "kapso": {"direction": "inbound"},
        },
        "conversation": {},
    }
    assert main._extract_kapso(event) == ("51999999999", "Hola", "wamid.123")


def test_extracts_interactive_reply_id_instead_of_visible_title():
    event = {
        "message": {
            "id": "wamid.button",
            "type": "interactive",
            "from": "51999999999",
            "interactive": {
                "type": "button_reply",
                "button_reply": {"id": "si", "title": "Sí, guardar"},
            },
            "kapso": {"direction": "inbound"},
        },
        "conversation": {},
    }
    assert main._extract_kapso(event) == ("51999999999", "si", "wamid.button")


def test_prefers_phone_when_phone_and_bsuid_are_available():
    event = {
        "message": {
            "id": "wamid.456",
            "type": "text",
            "from": "51999999999",
            "from_user_id": "US.13491208655302741918",
            "text": {"body": "Hola"},
            "kapso": {"direction": "inbound"},
        },
        "conversation": {
            "phone_number": "+51999999999",
            "business_scoped_user_id": "US.13491208655302741918",
        },
    }
    assert main._extract_kapso(event) == ("51999999999", "Hola", "wamid.456")


def test_extracts_bsuid_only_identity():
    event = {
        "message": {
            "id": "wamid.789",
            "type": "text",
            "from_user_id": "US.13491208655302741918",
            "username": "@tester",
            "text": {"body": "Hola"},
            "kapso": {"direction": "inbound"},
        },
        "conversation": {
            "phone_number": None,
            "business_scoped_user_id": "US.13491208655302741918",
        },
    }
    assert main._extract_kapso(event) == (
        "US.13491208655302741918",
        "Hola",
        "wamid.789",
    )


def test_sends_bsuid_in_recipient_field(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, url, headers, json):
            captured.update({"url": url, "headers": headers, "json": json})
            return Response()

    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **_: Client())
    monkeypatch.setattr(config, "KAPSO_API_KEY", "test-key")
    monkeypatch.setattr(config, "KAPSO_PHONE_NUMBER_ID", "123")

    assert asyncio.run(main._send_whatsapp("PE.1054603747275877", "Hola"))
    assert captured["json"]["recipient"] == "PE.1054603747275877"
    assert "to" not in captured["json"]


def test_retries_transient_window_sync_error(monkeypatch):
    calls = []
    request = httpx.Request("POST", "https://api.kapso.ai/messages")
    responses = [
        httpx.Response(
            422,
            request=request,
            json={"error": "Cannot send non-template messages outside the 24-hour window."},
        ),
        httpx.Response(200, request=request, json={"messages": [{"id": "wamid.ok"}]}),
    ]

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, url, headers, json):
            calls.append({"url": url, "headers": headers, "json": json})
            return responses.pop(0)

    async def no_wait(_):
        return None

    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **_: Client())
    monkeypatch.setattr(main.asyncio, "sleep", no_wait)
    monkeypatch.setattr(config, "KAPSO_API_KEY", "test-key")
    monkeypatch.setattr(config, "KAPSO_PHONE_NUMBER_ID", "123")

    assert asyncio.run(main._send_whatsapp("51999999999", "Hola"))
    assert len(calls) == 2
    assert calls[0]["headers"]["X-Idempotency-Key"] != calls[1]["headers"]["X-Idempotency-Key"]


def test_signature_uses_raw_body(monkeypatch):
    secret = "test-secret"
    raw = b'{"message":{"id":"1"}}'
    signature = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    monkeypatch.setattr(config, "KAPSO_WEBHOOK_SECRET", secret)
    assert main._valid_signature(raw, signature)
    assert not main._valid_signature(raw + b" ", signature)


def test_webhook_event_is_idempotent_in_memory():
    db.reset_memory()
    assert db.registrar_evento_webhook("wamid.same", "whatsapp.message.received")
    assert not db.registrar_evento_webhook("wamid.same", "whatsapp.message.received")
