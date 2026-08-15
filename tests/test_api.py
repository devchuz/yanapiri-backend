from fastapi.testclient import TestClient

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
