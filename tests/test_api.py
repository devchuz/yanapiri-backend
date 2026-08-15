from fastapi.testclient import TestClient
from datetime import date, timedelta

from app.core import db
from app.main import app


def test_health_and_preview_endpoints():
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["service"] == "nutriacompana"
        assert health.json()["environment"] in {"development", "production"}

        preview = client.post(
            "/assessments/preview",
            json={
                "birth_date": "2025-01-01",
                "measured_at": "2026-01-01",
                "sex": "F",
                "weight_kg": 8.9,
                "height_cm": 74.0,
                "height_mode": "length",
                "muac_mm": 120,
                "bilateral_edema": False,
            },
        )
        assert preview.status_code == 200
        assert preview.json()["assessment"]["semaforo"] == "amarillo"


def test_clinical_endpoints_require_bearer_token():
    with TestClient(app) as client:
        response = client.get("/clinical/children/unknown/history")
    assert response.status_code == 401


def test_clinical_history_measurement_appointment_and_question(monkeypatch):
    professional_id = "11111111-1111-1111-1111-111111111111"
    monkeypatch.setattr(
        db,
        "autenticar_profesional",
        lambda token: {
            "user_id": professional_id,
            "profile": {"full_name": "Dra. Demo", "verified": True},
            "memberships": [],
        },
    )
    child = db.registrar_nino(
        whatsapp_identity="clinical-api-family",
        caregiver_name="Rosa",
        child_name="Mateo",
        birth_date=(date.today() - timedelta(days=365)).isoformat(),
        sex="M",
        district="Lima",
    )
    headers = {"Authorization": "Bearer test-token"}
    with TestClient(app) as client:
        measurement = client.post(
            f"/clinical/children/{child['id']}/measurements",
            headers=headers,
            json={
                "weight_kg": 9.1,
                "height_cm": 75,
                "height_mode": "length",
                "muac_mm": 121,
                "bilateral_edema": False,
            },
        )
        assert measurement.status_code == 200
        assert measurement.json()["measurement"]["verification_status"] == "verified"

        appointment = client.post(
            f"/clinical/children/{child['id']}/appointments",
            headers=headers,
            json={
                "scheduled_at": "2026-08-20T15:00:00-05:00",
                "appointment_type": "growth_control",
                "notes": "Control CRED",
            },
        )
        assert appointment.status_code == 200
        appointment_id = appointment.json()["appointment"]["id"]

        question = client.post(
            f"/clinical/children/{child['id']}/ask",
            headers=headers,
            json={"question": "¿Cuál fue la última medición clínica?"},
        )
        assert question.status_code == 200
        assert question.json()["scope"] == "verified_measurements"
        assert "9.1 kg" in question.json()["answer"]

        history = client.get(
            f"/clinical/children/{child['id']}/history", headers=headers
        )
        assert history.status_code == 200
        assert len(history.json()["verified_trajectory"]) == 1
        assert len(history.json()["appointments"]) == 1

        updated = client.patch(
            f"/clinical/appointments/{appointment_id}",
            headers=headers,
            json={"status": "confirmed"},
        )
        assert updated.status_code == 200
        assert updated.json()["appointment"]["status"] == "confirmed"
