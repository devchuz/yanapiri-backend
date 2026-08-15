from datetime import date, timedelta

import pytest

from app.domain.anthropometry import (
    AnthropometryError,
    ImplausibleMeasurementError,
    assess_child,
)


def test_birth_medians_produce_zero_waz_and_haz():
    result = assess_child(
        birth_date="2026-01-01",
        measured_at="2026-01-01",
        sex="M",
        weight_kg=3.3464,
        height_cm=49.8842,
        height_mode="length",
    )
    assert result.waz == 0.0
    assert result.haz == 0.0


def test_muac_120_is_yellow_between_6_and_59_months():
    measured = date.today()
    result = assess_child(
        birth_date=measured - timedelta(days=365),
        measured_at=measured,
        sex="F",
        weight_kg=8.9,
        height_cm=74.0,
        height_mode="length",
        muac_mm=120,
    )
    assert result.semaforo == "amarillo"
    assert "MUAC entre 115 y 124 mm" in result.reasons


def test_bilateral_edema_is_red():
    measured = date.today()
    result = assess_child(
        birth_date=measured - timedelta(days=500),
        measured_at=measured,
        sex="M",
        weight_kg=10,
        height_cm=78,
        height_mode="length",
        bilateral_edema=True,
    )
    assert result.semaforo == "rojo"
    assert "edema bilateral declarado" in result.reasons


def test_demo_examples_cover_each_traffic_light():
    green = assess_child(
        birth_date=date.today() - timedelta(days=730),
        measured_at=date.today(),
        sex="F",
        weight_kg=11.5,
        height_cm=85,
        height_mode="height",
        muac_mm=135,
        bilateral_edema=False,
    )
    yellow = assess_child(
        birth_date=date.today() - timedelta(days=365),
        measured_at=date.today(),
        sex="F",
        weight_kg=8.9,
        height_cm=74,
        height_mode="length",
        muac_mm=120,
        bilateral_edema=False,
    )
    red = assess_child(
        birth_date=date.today() - timedelta(days=730),
        measured_at=date.today(),
        sex="M",
        weight_kg=12,
        height_cm=86,
        height_mode="height",
        muac_mm=130,
        bilateral_edema=True,
    )
    assert (green.semaforo, yellow.semaforo, red.semaforo) == (
        "verde",
        "amarillo",
        "rojo",
    )


def test_standing_measurement_under_two_is_adjusted():
    measured = date.today()
    result = assess_child(
        birth_date=measured - timedelta(days=400),
        measured_at=measured,
        sex="M",
        weight_kg=10,
        height_cm=77,
        height_mode="height",
    )
    assert result.adjusted_height_cm == 77.7
    assert result.warnings


def test_rejects_child_older_than_supported_range():
    with pytest.raises(AnthropometryError):
        assess_child(
            birth_date="2018-01-01",
            measured_at="2026-01-01",
            sex="F",
            weight_kg=20,
            height_cm=110,
            height_mode="height",
        )


def test_rejects_biologically_implausible_combination():
    measured = date.today()
    with pytest.raises(ImplausibleMeasurementError):
        assess_child(
            birth_date=measured - timedelta(days=87),
            measured_at=measured,
            sex="M",
            weight_kg=10.4,
            height_cm=50.0,
            height_mode="length",
            muac_mm=128,
        )
