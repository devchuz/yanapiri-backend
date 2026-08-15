from app.core import config, db
from app.services import llm


def setup_function():
    db.reset_memory()


def test_groq_answers_free_question(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "Respuesta educativa."}}]}

    def fake_post(url, headers, json, timeout):
        captured.update({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return Response()

    monkeypatch.setattr(config, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(config, "GROQ_MODEL", "llama-3.1-8b-instant")
    monkeypatch.setattr(llm.httpx, "post", fake_post)
    db.guardar_mensaje("family", "user", "¿Qué alimentos ayudan al crecimiento?")

    assert llm.answer("¿Qué alimentos ayudan al crecimiento?", "family") == "Respuesta educativa."
    assert captured["json"]["model"] == "llama-3.1-8b-instant"
    assert "Authorization" in captured["headers"]
    assert captured["json"]["messages"][-1]["role"] == "user"


def test_groq_disabled_uses_local_fallback(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    assert llm.answer("Hola", "family") is None


def test_groq_receives_minimized_current_question_without_stored_history(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "Orientación general segura."}}]}

    def fake_post(url, headers, json, timeout):
        captured["messages"] = json["messages"]
        return Response()

    monkeypatch.setattr(config, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(llm.httpx, "post", fake_post)
    db.registrar_nino(
        whatsapp_identity="private-family",
        caregiver_name="Rosa Quispe",
        child_name="Mateo Quispe",
        birth_date="2025-01-10",
        sex="M",
        district="Lima",
    )
    db.guardar_mensaje("private-family", "user", "Mi DNI es 12345678")

    llm.answer(
        "Mateo Quispe nació el 10/01/2025, ¿qué alimentos son adecuados?",
        "private-family",
    )

    assert len(captured["messages"]) == 2
    sent = captured["messages"][-1]["content"]
    assert "Mateo Quispe" not in sent
    assert "10/01/2025" not in sent
    assert "12345678" not in sent


def test_unsafe_llm_diagnosis_is_replaced_by_safety_fallback(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "Tu niño tiene desnutrición."}}]}

    monkeypatch.setattr(config, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(llm.httpx, "post", lambda *args, **kwargs: Response())
    answer = llm.answer("¿Cómo está?", "family")
    assert "no puedo emitir diagnósticos" in answer
