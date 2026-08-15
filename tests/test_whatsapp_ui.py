from app.services.bot import respond
from app.services.whatsapp_ui import build_presentation, interactive_payload


def test_first_contact_uses_two_onboarding_buttons():
    identity = "interactive-menu"
    answer = respond("hola", identity)
    presentation = build_presentation(identity, answer)

    assert presentation is not None
    assert presentation.kind == "buttons"
    assert [option.id for option in presentation.options] == ["1", "2"]


def test_returning_caregiver_gets_three_compact_home_buttons():
    from app.core import db

    identity = "interactive-returning"
    db.registrar_cuidador(
        whatsapp_identity=identity,
        full_name="Rosa",
        relationship="madre",
        district="Lima",
    )
    db.registrar_nino(
        whatsapp_identity=identity,
        caregiver_name="Rosa",
        child_name="Mateo",
        birth_date="2025-08-15",
        sex="M",
        district="Lima",
    )
    answer = respond("hola", identity)
    presentation = build_presentation(identity, answer)
    assert presentation.kind == "buttons"
    assert [option.id for option in presentation.options] == [
        "medicion",
        "estado",
        "mas opciones",
    ]


def test_more_options_uses_list_instead_of_six_visible_buttons():
    from app.core import db

    identity = "interactive-more"
    db.registrar_cuidador(
        whatsapp_identity=identity,
        full_name="Rosa",
        relationship="madre",
        district="Lima",
    )
    answer = respond("MÁS OPCIONES", identity)
    presentation = build_presentation(identity, answer)
    assert presentation.kind == "list"
    assert len(presentation.options) == 6
    assert "1️⃣ Registrar" not in presentation.body


def test_registration_relationship_uses_three_reply_buttons():
    identity = "interactive-registration"
    answer = respond("1", identity)
    assert "Aceptas continuar" in answer
    answer = respond("si", identity)
    presentation = build_presentation(identity, answer)

    assert presentation is not None
    assert presentation.kind == "buttons"
    assert [(option.id, option.title) for option in presentation.options] == [
        ("1", "Madre"),
        ("2", "Padre"),
        ("3", "Otro cuidador"),
    ]


def test_interactive_id_is_accepted_by_existing_deterministic_flow():
    identity = "interactive-choice"
    respond("1", identity)
    respond("si", identity)
    answer = respond("1", identity)

    assert "nombre completo" in answer


def test_interactive_payload_matches_kapso_rest_shape():
    identity = "interactive-payload"
    respond("1", identity)
    answer = respond("si", identity)
    presentation = build_presentation(identity, answer)
    payload = interactive_payload(presentation)

    assert payload["type"] == "interactive"
    assert payload["interactive"]["type"] == "button"
    first = payload["interactive"]["action"]["buttons"][0]
    assert first == {
        "type": "reply",
        "reply": {"id": "1", "title": "Madre"},
    }


def test_free_form_measurement_value_does_not_get_buttons():
    identity = "interactive-weight"
    # El registro directo prepara un flujo de medición cuyo peso debe escribirse.
    from datetime import date, timedelta
    from app.core import db

    db.registrar_nino(
        whatsapp_identity=identity,
        caregiver_name="Rosa",
        child_name="Mateo",
        birth_date=(date.today() - timedelta(days=365)).isoformat(),
        sex="M",
        district="Ventanilla",
    )
    answer = respond("MEDICIÓN", identity)

    assert "peso" in answer.lower()
    assert build_presentation(identity, answer) is None
