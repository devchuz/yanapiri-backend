"""Las pruebas siempre usan memoria; jamás las credenciales reales del equipo."""

import pytest

from app.core import config, db


@pytest.fixture(autouse=True)
def disable_real_supabase(monkeypatch):
    monkeypatch.setattr(config, "SUPABASE_URL", "")
    monkeypatch.setattr(config, "SUPABASE_SERVICE_KEY", "")
    monkeypatch.setattr(db, "_client", None)
    db.reset_memory()
    yield
    monkeypatch.setattr(db, "_client", None)
