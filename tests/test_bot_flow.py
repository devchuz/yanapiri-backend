from datetime import date, timedelta

import pytest

from app.core import db
from app.services.bot import respond
from app.services.guardrails import danger_response


def setup_function():
    db.reset_memory()


def test_welcome_is_warm_and_keeps_numbered_actions():
    welcome = respond("hola", "welcome-test")
    assert "👋" in welcome
    assert "Yanapiri Wawa" in welcome
    assert "1️⃣ Registrar" in welcome
    assert "2️⃣ Registrar una nueva medición" in welcome
    assert "personal de salud" in welcome


def test_registered_family_gets_personalized_welcome():
    identity = "returning-family"
    db.registrar_nino(
        whatsapp_identity=identity,
        caregiver_name="Rosa",
        child_name="Mateo",
        birth_date=(date.today() - timedelta(days=400)).isoformat(),
        sex="M",
        district="Lima",
    )
    welcome = respond("hola", identity)
    assert "Hola de nuevo" in welcome
    assert "Mateo" in welcome
    assert "Registrar a otra" in welcome


def test_registration_and_measurement_flow_in_memory():
    identity = "family-test"
    birth = (date.today() - timedelta(days=365)).isoformat()

    assert "relación" in respond("registrar", identity).lower()
    assert "nombre" in respond("madre", identity).lower()
    respond("María Quispe", identity)
    respond("Lucía", identity)
    respond(birth, identity)
    respond("F", identity)
    prompt = respond("San Juan de Lurigancho", identity)
    assert "establecimiento" in prompt.lower()
    confirmation = respond("omitir", identity)
    assert "autoriza" in confirmation.lower()
    registered = respond("sí", identity)
    assert "registré" in registered.lower()
    assert "primera medición" in registered.lower()

    assert "peso" in respond("sí", identity).lower()
    respond("8.9", identity)
    respond("74.0", identity)
    respond("acostada", identity)
    respond("120", identity)
    summary = respond("no", identity)
    assert "revisa los datos" in summary.lower()
    result = respond("sí", identity)
    assert "medición guardada" in result.lower()
    assert "74.0 cm" in result
    assert "conviene revisar" in result.lower()
    assert "P/E" not in result
    assert " DE" not in result

    state = db.consultar_estado(db.listar_ninos(identity)[0]["id"], identity)
    assert state["latest"]["height_cm"] == 74.0
    assert state["latest"]["weight_kg"] == 8.9
    assert state["latest"]["measured_at"] == date.today().isoformat()
    assert len(state["trajectory"]) == 1
    assert state["latest"]["assessment"]["semaforo"] == "amarillo"
    assert len(state["active_alerts"]) == 1


def test_natural_registration_action_starts_caregiver_flow():
    answer = respond("Quiero registrar a mi hija", "natural-registration")
    assert "persona adulta" in answer
    assert "Madre" in answer


def test_natural_height_action_prefills_height_and_requests_weight():
    identity = "natural-height"
    db.registrar_nino(
        whatsapp_identity=identity,
        caregiver_name="Luis",
        caregiver_relationship="padre",
        child_name="Ana",
        birth_date=(date.today() - timedelta(days=300)).isoformat(),
        sex="F",
        district="Lima",
    )
    answer = respond("Quiero registrar una talla de 76.5 cm", identity)
    assert "76.5 cm" in answer
    assert "peso" in answer.lower()
    height_prompt = respond("8.2", identity)
    assert "ACOSTADO" in height_prompt


def test_height_prompt_for_under_two_includes_ins_tutorial():
    identity = "tutorial-height"
    db.registrar_nino(
        whatsapp_identity=identity,
        caregiver_name="Elena",
        child_name="Sol",
        birth_date=(date.today() - timedelta(days=200)).isoformat(),
        sex="F",
        district="Lima",
    )
    assert "peso" in respond("TALLA", identity).lower()
    prompt = respond("7.5", identity)
    assert "Instituto Nacional de Salud" in prompt
    assert "0C6CUT8XlRc" in prompt


def test_registration_accepts_natural_birth_sex_and_unknown_center():
    identity = "natural-demographics"
    birth = date.today() - timedelta(days=250)
    months = [
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
    ]
    respond("Quiero registrar a mi hija", identity)
    respond("soy su mamá", identity)
    respond("Carla Ruiz", identity)
    respond("Valentina", identity)
    sex_prompt = respond(
        f"Nació el {birth.day} de {months[birth.month - 1]} de {birth.year}", identity
    )
    assert "sexo" in sex_prompt.lower()
    district_prompt = respond("es una niña", identity)
    assert "distrito" in district_prompt.lower()
    respond("Ventanilla", identity)
    confirmation = respond("No lo sé por ahora", identity)
    assert birth.strftime("%d/%m/%Y") in confirmation
    assert "Sexo registrado: Femenino" in confirmation
    assert "pendiente de vincular" in confirmation
    registered = respond("claro que sí", identity)
    assert "Registré" in registered


def test_health_center_can_be_added_later():
    identity = "later-health-center"
    db.registrar_nino(
        whatsapp_identity=identity,
        caregiver_name="Julia",
        child_name="Pedro",
        birth_date=(date.today() - timedelta(days=500)).isoformat(),
        sex="M",
        district="Ventanilla",
    )
    prompt = respond("quiero agregar su centro de salud", identity)
    assert "RENIPRESS" in prompt
    saved = respond("Centro de Salud Mi Perú", identity)
    assert "Guardé" in saved
    child = db.listar_ninos(identity)[0]
    assert child["reported_health_center"] == "Centro de Salud Mi Perú"


def test_implausible_measurement_is_saved_for_review_without_red_alert():
    identity = "implausible-family"
    db.registrar_nino(
        whatsapp_identity=identity,
        caregiver_name="Delia",
        child_name="Aldair",
        birth_date=(date.today() - timedelta(days=87)).isoformat(),
        sex="M",
        district="Ventanilla",
    )
    respond("MEDICIÓN", identity)
    respond("10.4", identity)
    respond("50", identity)
    respond("acostado", identity)
    respond("128", identity)
    confirmation = respond("no", identity)
    assert "Revisa los datos" in confirmation
    result = respond("sí", identity)
    assert "Guardé la medición" in result
    assert "pendiente de confirmar" in result
    assert "no le asigné color" in result
    assert "ROJO" not in result
    child = db.listar_ninos(identity)[0]
    state = db.consultar_estado(child["id"], identity)
    assert len(state["trajectory"]) == 1
    assert state["trajectory"][0]["validation_status"] == "needs_review"
    assert state["trajectory"][0]["assessment"] is None
    assert state["active_alerts"] == []
    status = respond("ESTADO", identity)
    assert "pendiente de confirmar" in status


def test_measurement_accepts_and_normalizes_common_units():
    identity = "flexible-units-family"
    db.registrar_nino(
        whatsapp_identity=identity,
        caregiver_name="Rosa",
        child_name="Mateo",
        birth_date=(date.today() - timedelta(days=365)).isoformat(),
        sex="M",
        district="Lima",
    )
    respond("MEDICIÓN", identity)
    respond("10400 gramos", identity)
    respond("0.82 metros", identity)
    respond("acostado", identity)
    respond("12.8 cm", identity)
    summary = respond("no", identity)
    assert "10.4 kg" in summary
    assert "82.0 cm" in summary
    assert "128.0 mm" in summary
    respond("sí", identity)

    child = db.listar_ninos(identity)[0]
    state = db.consultar_estado(child["id"], identity)
    assert state["latest"]["weight_kg"] == 10.4
    assert state["latest"]["height_cm"] == 82.0
    assert state["latest"]["muac_mm"] == 128.0


def test_outside_oms_input_range_is_preserved_as_pending_review():
    identity = "broad-capture-family"
    db.registrar_nino(
        whatsapp_identity=identity,
        caregiver_name="Rosa",
        child_name="Mateo",
        birth_date=(date.today() - timedelta(days=365)).isoformat(),
        sex="M",
        district="Lima",
    )
    respond("MEDICIÓN", identity)
    respond("45 kg", identity)
    respond("82 cm", identity)
    respond("acostado", identity)
    respond("128 mm", identity)
    respond("no", identity)
    result = respond("sí", identity)

    assert "Guardé la medición" in result
    assert "pendiente de confirmar" in result
    child = db.listar_ninos(identity)[0]
    state = db.consultar_estado(child["id"], identity)
    assert state["latest"]["weight_kg"] == 45.0
    assert state["latest"]["validation_status"] == "needs_review"
    assert state["latest"]["assessment"] is None
    assert state["active_alerts"] == []


def test_measurement_rejects_negative_value_instead_of_dropping_sign():
    identity = "negative-measurement-family"
    db.registrar_nino(
        whatsapp_identity=identity,
        caregiver_name="Rosa",
        child_name="Mateo",
        birth_date=(date.today() - timedelta(days=365)).isoformat(),
        sex="M",
        district="Lima",
    )
    respond("MEDICIÓN", identity)
    answer = respond("-5 kg", identity)
    assert "No pude entender el peso" in answer
    assert db.estado_conversacion(identity)["step"] == "weight"


def _registered_child_for_supplements(identity: str = "supplement-family") -> dict:
    return db.registrar_nino(
        whatsapp_identity=identity,
        caregiver_name="Rosa",
        child_name="Mateo",
        birth_date=(date.today() - timedelta(days=500)).isoformat(),
        sex="M",
        district="Lima",
    )


def test_reported_condition_keeps_diagnosis_unverified_and_auditable():
    identity = "condition-family"
    child = _registered_child_for_supplements(identity)

    assert "Seguimiento de suplementos" in respond("SUPLEMENTOS", identity)
    respond("1", identity)
    respond("Anemia por deficiencia de hierro", identity)
    respond("Nutricionista Ana Pérez", identity)
    result = respond("12/08/2026", identity)

    assert "pendiente de verificación clínica" in result
    conditions = db.listar_condiciones(whatsapp_identity=identity, child_id=child["id"])
    assert len(conditions) == 1
    assert conditions[0]["verification_status"] == "reported"
    assert conditions[0]["diagnosed_by_name"] == "Nutricionista Ana Pérez"
    assert conditions[0]["diagnosing_professional_id"] is None


def test_supplement_plan_intake_reason_summary_and_reminder_flow():
    identity = "supplement-full-flow"
    child = _registered_child_for_supplements(identity)

    respond("SUPLEMENTOS", identity)
    respond("2", identity)
    respond("1", identity)
    respond("3", identity)
    confirmation = respond("Enfermera Julia", identity)
    assert "Hierro" in confirmation
    saved = respond("sí", identity)
    assert "indicación reportada" in saved

    plans = db.listar_planes_suplemento(whatsapp_identity=identity, child_id=child["id"])
    assert len(plans) == 1
    assert plans[0]["supplement_type"] == "iron"
    assert plans[0]["verification_status"] == "reported"
    assert plans[0]["schedule_text"] is None

    respond("TOMA", identity)
    respond("3", identity)
    reasons = respond("no", identity)
    assert "Lo olvidé" in reasons
    result = respond("2", identity)
    assert "motivo" in result
    summary = db.resumen_adherencia(whatsapp_identity=identity, child_id=child["id"])
    assert summary["not_taken"] == 1
    assert summary["events"][0]["reason_code"] == "out_of_stock"

    respond("SUPLEMENTOS", identity)
    summary_message = respond("4", identity)
    assert "Registrado como no tomó: 1" in summary_message

    respond("SUPLEMENTOS", identity)
    respond("5", identity)
    respond("sí", identity)
    reminder = respond("19:30", identity)
    assert "19:30" in reminder
    assert db.recordatorios_suplemento_pendientes(reminder_time="19:30") == []


def test_supplement_pending_reminder_excludes_day_after_intake():
    identity = "supplement-reminder"
    child = _registered_child_for_supplements(identity)
    plan = db.registrar_plan_suplemento_reportado(
        whatsapp_identity=identity,
        child_id=child["id"],
        supplement_type="mnp",
        indicated_by_name="Enfermería",
    )
    db.configurar_recordatorio_suplemento(
        whatsapp_identity=identity, plan_id=plan["id"], enabled=True, reminder_time="08:00"
    )
    pending = db.recordatorios_suplemento_pendientes(reminder_time="08:00")
    assert len(pending) == 1
    assert pending[0]["plan"]["supplement_type"] == "mnp"
    db.registrar_toma_suplemento(
        whatsapp_identity=identity, plan_id=plan["id"], intake_status="taken"
    )
    assert db.recordatorios_suplemento_pendientes(reminder_time="08:00") == []


def test_supplement_can_record_that_child_takes_none_without_creating_plan():
    identity = "no-supplement-family"
    child = _registered_child_for_supplements(identity)
    respond("SUPLEMENTOS", identity)
    respond("2", identity)
    answer = respond("7", identity)
    assert "No registré un plan activo" in answer
    assert db.listar_planes_suplemento(whatsapp_identity=identity, child_id=child["id"]) == []


def test_therapeutic_plan_links_single_reported_condition():
    identity = "therapeutic-supplement-family"
    child = _registered_child_for_supplements(identity)
    condition = db.registrar_condicion_reportada(
        whatsapp_identity=identity,
        child_id=child["id"],
        condition_name="Anemia por deficiencia de hierro",
        diagnosed_by_name="Personal de salud",
    )
    respond("SUPLEMENTOS", identity)
    respond("2", identity)
    respond("1", identity)
    respond("2", identity)
    respond("Enfermería", identity)
    respond("sí", identity)
    plans = db.listar_planes_suplemento(whatsapp_identity=identity, child_id=child["id"])
    assert plans[0]["purpose"] == "therapeutic"
    assert plans[0]["condition_id"] == condition["id"]


def test_status_uses_family_language_without_z_scores():
    identity = "friendly-status"
    db.registrar_nino(
        whatsapp_identity=identity,
        caregiver_name="Rosa",
        child_name="Lucía",
        birth_date=(date.today() - timedelta(days=365)).isoformat(),
        sex="F",
        district="Lima",
    )
    respond("MEDICIÓN", identity)
    respond("8.9", identity)
    respond("74", identity)
    respond("acostada", identity)
    respond("120", identity)
    respond("no", identity)
    respond("sí", identity)
    status = respond("ESTADO", identity)
    assert "Últimos registros" in status
    assert "conviene revisar" in status
    assert "P/E" not in status


def _create_yellow_alert(identity: str, child_name: str = "Mateo") -> dict:
    child = db.registrar_nino(
        whatsapp_identity=identity,
        caregiver_name="Persona cuidadora",
        child_name=child_name,
        birth_date=(date.today() - timedelta(days=365)).isoformat(),
        sex="M",
        district="Ventanilla",
        reported_health_center="Centro de Salud Ventanilla",
    )
    return db.registrar_medicion(
        whatsapp_identity=identity,
        child_id=child["id"],
        measured_at=date.today().isoformat(),
        weight_kg=8.9,
        height_cm=74,
        height_mode="length",
        muac_mm=120,
        bilateral_edema=False,
    )


def test_followup_plan_is_recorded_without_resolving_clinical_alert():
    identity = "followup-plan"
    saved = _create_yellow_alert(identity)

    menu = respond("SEGUIMIENTO", identity)
    assert "solo el personal de salud" in menu
    assert "podrías acudir" in respond("2", identity)
    confirmation = respond("1", identity)
    assert "compromiso de seguimiento" in confirmation

    events = db.eventos_seguimiento_alerta(saved["alert"]["id"], identity)
    assert events[0]["event_type"] == "plans_to_attend"
    assert events[0]["planned_for"] == date.today().isoformat()
    state = db.consultar_estado(saved["child"]["id"], identity)
    assert state["active_alerts"][0]["estado"] == "abierta"


def test_caregiver_can_report_attendance_but_cannot_close_alert():
    identity = "attendance-report"
    saved = _create_yellow_alert(identity, "Lucía")

    respond("SEGUIMIENTO", identity)
    confirmation = respond("3", identity)
    assert "continuará activa" in confirmation
    events = db.eventos_seguimiento_alerta(saved["alert"]["id"], identity)
    assert events[0]["event_type"] == "attendance_reported"
    assert saved["alert"]["estado"] == "abierta"


def test_followup_event_rejects_another_family_alert():
    saved = _create_yellow_alert("owner-family")
    db.registrar_nino(
        whatsapp_identity="other-family",
        caregiver_name="Otra persona",
        child_name="Ana",
        birth_date=(date.today() - timedelta(days=300)).isoformat(),
        sex="F",
        district="Lima",
    )
    with pytest.raises(ValueError, match="alerta activa"):
        db.registrar_evento_seguimiento_cuidador(
            whatsapp_identity="other-family",
            alert_id=saved["alert"]["id"],
            event_type="attendance_reported",
        )


def test_danger_sign_bypasses_llm_and_gives_urgent_instruction():
    answer = respond("Mi niño no puede respirar", "danger-family")
    assert "atención presencial de inmediato" in answer
    assert "diagnóstico" in answer


def test_followup_can_be_postponed_without_trapping_next_command():
    identity = "postpone-followup"
    _create_yellow_alert(identity)
    respond("SEGUIMIENTO", identity)
    menu = respond("DESPUÉS", identity)
    assert "retomarlo" in menu
    assert db.estado_conversacion(identity) == {}
    assert "peso" in respond("MEDICIÓN", identity).lower()


def test_missing_followup_migration_exits_flow_without_losing_alert(monkeypatch):
    identity = "missing-followup-table"
    saved = _create_yellow_alert(identity)
    respond("SEGUIMIENTO", identity)

    def unavailable(**kwargs):
        raise db.FollowupStorageUnavailableError("migration pending")

    monkeypatch.setattr(db, "registrar_evento_seguimiento_cuidador", unavailable)
    answer = respond("3", identity)
    assert "medición y la alerta sí quedaron guardadas" in answer
    assert "db/schema.sql" in answer
    assert db.estado_conversacion(identity) == {}
    state = db.consultar_estado(saved["child"]["id"], identity)
    assert len(state["active_alerts"]) == 1


def test_quick_registration_accepts_all_data_and_confirms_before_saving():
    identity = "quick-registration"
    birth = (date.today() - timedelta(days=420)).strftime("%d/%m/%Y")
    prompt = respond("REGISTRO RÁPIDO", identity)
    assert "Copia este formato" in prompt

    summary = respond(
        "\n".join(
            (
                "Cuidador: Rosa Quispe",
                "Relación: madre",
                "Niña o niño: Mateo Quispe",
                f"Nacimiento: {birth}",
                "Sexo: masculino",
                "Distrito: Ventanilla",
                "Establecimiento: no lo sé",
            )
        ),
        identity,
    )
    assert "Revisa los datos" in summary
    assert "Mateo Quispe" in summary
    assert db.listar_ninos(identity) == []

    saved = respond("SÍ", identity)
    assert "Registré correctamente" in saved
    children = db.listar_ninos(identity)
    assert len(children) == 1
    assert children[0]["full_name"] == "Mateo Quispe"
    assert children[0]["reported_health_center"] is None


def test_status_shows_only_two_most_recent_measurements():
    identity = "two-recent-records"
    child = db.registrar_nino(
        whatsapp_identity=identity,
        caregiver_name="Rosa",
        child_name="Mateo",
        birth_date=(date.today() - timedelta(days=1000)).isoformat(),
        sex="M",
        district="Lima",
    )
    dates = [date.today() - timedelta(days=day) for day in (20, 10, 0)]
    for measured_at, weight in zip(dates, (11.0, 11.5, 12.0)):
        db.registrar_medicion(
            whatsapp_identity=identity,
            child_id=child["id"],
            measured_at=measured_at.isoformat(),
            weight_kg=weight,
            height_cm=90,
            height_mode="height",
            muac_mm=130,
            bilateral_edema=False,
        )

    status = respond("ESTADO", identity)
    assert "Mateo está registrado" in status
    assert "12.0 kg" in status
    assert "11.5 kg" in status
    assert "11.0 kg" not in status
    assert "Mostrando los 2 más recientes de 3 registros" in status
    assert "trayectoria completa" in status


def test_negated_swelling_does_not_trigger_danger_guardrail():
    assert danger_response("No tiene hinchazón en ambos pies") is None
    assert danger_response("Los dos pies no están hinchados") is None
    assert danger_response("Mi niño no puede respirar") is not None


def test_repeated_registration_does_not_duplicate_same_child():
    identity = "duplicate-registration"
    birth = (date.today() - timedelta(days=500)).isoformat()
    first = db.registrar_nino(
        whatsapp_identity=identity,
        caregiver_name="Rosa Quispe",
        child_name="Mateo Quispe",
        birth_date=birth,
        sex="M",
        district="Ventanilla",
    )
    second = db.registrar_nino(
        whatsapp_identity=identity,
        caregiver_name="Rosa Quispe",
        child_name="  mateo   quispe ",
        birth_date=birth,
        sex="M",
        district="Ventanilla",
    )
    assert first["id"] == second["id"]
    assert second["_already_registered"] is True
    assert len(db.listar_ninos(identity)) == 1
