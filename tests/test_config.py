from datetime import datetime, timedelta, timezone

import pytest

from app.core import config
from app.core import db


def test_optional_supabase_response_can_be_empty():
    assert db._response_data(None) is None


def test_conversation_state_expiration():
    now = datetime.now(timezone.utc)
    expired = {"flow": "registration", "_expires_at": (now - timedelta(seconds=1)).isoformat()}
    active = {"flow": "registration", "_expires_at": (now + timedelta(seconds=1)).isoformat()}
    assert db._state_is_expired(expired, now)
    assert not db._state_is_expired(active, now)


def test_detects_legacy_relationship_schema_error():
    error = Exception(
        "Error PGRST204: Could not find the 'relationship' column of 'caregivers' in the schema cache"
    )
    assert db._missing_relationship_column(error)


def test_development_allows_local_fallbacks(monkeypatch):
    monkeypatch.setattr(config, "APP_ENV", "development")
    config.validar_entorno()


def test_production_rejects_missing_required_services(monkeypatch):
    monkeypatch.setattr(config, "APP_ENV", "production")
    monkeypatch.setattr(config, "SUPABASE_URL", "")
    monkeypatch.setattr(config, "SUPABASE_SERVICE_KEY", "")
    monkeypatch.setattr(config, "KAPSO_API_KEY", "")
    monkeypatch.setattr(config, "KAPSO_PHONE_NUMBER_ID", "")
    monkeypatch.setattr(config, "KAPSO_WEBHOOK_SECRET", "")

    with pytest.raises(RuntimeError, match="Producción no puede iniciar"):
        config.validar_entorno()
