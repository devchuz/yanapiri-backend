"""Motor antropométrico determinista basado en los estándares OMS 2006.

Los CSV LMS de ``seeds/who`` provienen del paquete oficial ``igrowup-spss``.
El motor orienta y prioriza; no emite un diagnóstico médico.
"""

from __future__ import annotations

import csv
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

WHO_MAX_AGE_DAYS = 1856
TWO_YEARS_DAYS = 731
SIX_MONTHS_DAYS = 183


class AnthropometryError(ValueError):
    """Entrada incompleta o fuera del rango cubierto por el estándar."""


class ImplausibleMeasurementError(AnthropometryError):
    """Combinación que debe comprobarse antes de interpretarse o guardarse."""


@dataclass(frozen=True)
class Assessment:
    age_days: int
    waz: float | None
    haz: float | None
    whz: float | None
    wh_indicator: str
    adjusted_height_cm: float
    semaforo: str
    reasons: list[str]
    warnings: list[str]
    rule_version: str = "nutricred-oms2006-v1"

    def to_dict(self) -> dict:
        return asdict(self)


def _data_dir() -> Path:
    return Path(__file__).parents[2] / "seeds" / "who"


@lru_cache(maxsize=4)
def _load_table(name: str) -> dict[tuple[int, float], tuple[float, float, float]]:
    path = _data_dir() / f"{name}.csv"
    if not path.exists():
        raise RuntimeError(f"No se encontró la tabla OMS: {path}")
    result: dict[tuple[int, float], tuple[float, float, float]] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            key_name = next(k for k in row if k not in {"sex", "l", "m", "s"})
            key = float(row[key_name])
            result[(int(row["sex"]), key)] = (
                float(row["l"]),
                float(row["m"]),
                float(row["s"]),
            )
    return result


def _parse_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise AnthropometryError("La fecha debe usar el formato AAAA-MM-DD.") from exc


def age_in_days(birth_date: date | datetime | str, measured_at: date | datetime | str) -> int:
    birth = _parse_date(birth_date)
    measured = _parse_date(measured_at)
    days = (measured - birth).days
    if days < 0:
        raise AnthropometryError("La medición no puede ser anterior al nacimiento.")
    try:
        fifth_birthday = birth.replace(year=birth.year + 5)
    except ValueError:  # 29 de febrero
        fifth_birthday = birth.replace(year=birth.year + 5, day=28)
    if measured >= fifth_birthday or days > WHO_MAX_AGE_DAYS:
        raise AnthropometryError("El motor de este reto solo cubre niñas y niños menores de 5 años.")
    return days


def _who_sex(value: str) -> int:
    normalized = str(value).strip().upper()
    if normalized in {"M", "MASCULINO", "NIÑO", "NINO", "1"}:
        return 1
    if normalized in {"F", "FEMENINO", "NIÑA", "NINA", "2"}:
        return 2
    raise AnthropometryError("El sexo debe ser M o F para seleccionar la tabla OMS.")


def _lms_z(value: float, lms: tuple[float, float, float]) -> float:
    l_value, median, sigma = lms
    if value <= 0:
        raise AnthropometryError("Peso y talla deben ser mayores que cero.")
    if abs(l_value) < 1e-12:
        raw = math.log(value / median) / sigma
    else:
        raw = (((value / median) ** l_value) - 1) / (sigma * l_value)

    # Extensión lineal más allá de ±3 DE, igual al macro oficial igrowup.
    if raw > 3:
        sd3 = median * ((1 + l_value * sigma * 3) ** (1 / l_value))
        sd2 = median * ((1 + l_value * sigma * 2) ** (1 / l_value))
        return 3 + ((value - sd3) / (sd3 - sd2))
    if raw < -3:
        sd3 = median * ((1 + l_value * sigma * -3) ** (1 / l_value))
        sd2 = median * ((1 + l_value * sigma * -2) ** (1 / l_value))
        return -3 - ((sd3 - value) / (sd2 - sd3))
    return raw


def _z_by_exact_key(table: str, sex: int, key: int, value: float) -> float:
    lms = _load_table(table).get((sex, float(key)))
    if lms is None:
        raise AnthropometryError("La medición está fuera del rango de la tabla OMS.")
    return _lms_z(value, lms)


def _z_by_interpolation(table: str, sex: int, key: float, value: float) -> float:
    low = math.floor((key + 1e-9) * 10) / 10
    high = math.ceil((key - 1e-9) * 10) / 10
    records = _load_table(table)
    low_lms = records.get((sex, round(low, 1)))
    high_lms = records.get((sex, round(high, 1)))
    if low_lms is None or high_lms is None:
        raise AnthropometryError("La talla está fuera del rango de peso para talla de la OMS.")
    z_low = _lms_z(value, low_lms)
    if high == low:
        return z_low
    z_high = _lms_z(value, high_lms)
    fraction = (key - low) / (high - low)
    return z_low + (z_high - z_low) * fraction


def _round_z(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


def assess_child(
    *,
    birth_date: date | datetime | str,
    measured_at: date | datetime | str,
    sex: str,
    weight_kg: float,
    height_cm: float,
    height_mode: str,
    muac_mm: float | None = None,
    bilateral_edema: bool = False,
) -> Assessment:
    """Calcula WAZ, HAZ y WLZ/WHZ y asigna el semáforo de priorización."""
    days = age_in_days(birth_date, measured_at)
    who_sex = _who_sex(sex)
    weight = float(weight_kg)
    height = float(height_cm)
    muac = None if muac_mm is None else float(muac_mm)
    if not 1 <= weight <= 40:
        raise AnthropometryError("Revisa el peso: debe estar entre 1 y 40 kg.")
    if not 40 <= height <= 125:
        raise AnthropometryError("Revisa la talla: debe estar entre 40 y 125 cm.")
    if muac is not None and not 70 <= muac <= 250:
        raise AnthropometryError("Revisa el MUAC: debe estar entre 70 y 250 mm.")

    mode = str(height_mode).strip().lower()
    if mode in {"acostado", "acostada", "longitud", "l"}:
        mode = "length"
    elif mode in {"parado", "parada", "talla", "height", "h"}:
        mode = "height"
    if mode not in {"length", "height"}:
        raise AnthropometryError("Indica si la medición fue acostada, acostado o de pie.")

    warnings: list[str] = []
    adjusted_height = height
    use_length = days < TWO_YEARS_DAYS
    if use_length and mode == "height":
        adjusted_height += 0.7
        warnings.append("Se añadieron 0.7 cm porque, antes de los 2 años, la OMS usa longitud con el menor acostado.")
    elif not use_length and mode == "length":
        adjusted_height -= 0.7
        warnings.append("Se restaron 0.7 cm porque, desde los 2 años, la OMS usa talla de pie.")

    waz = _z_by_exact_key("wazlms", who_sex, days, weight)
    haz = _z_by_exact_key("hazlms", who_sex, days, adjusted_height)
    wh_table = "wfllms" if use_length else "wfhlms"
    whz = _z_by_interpolation(wh_table, who_sex, adjusted_height, weight)
    wh_indicator = "peso/longitud" if use_length else "peso/talla"

    implausible = []
    if waz < -6 or waz > 5:
        implausible.append("peso/edad")
    if haz < -6 or haz > 6:
        implausible.append("talla/edad")
    if whz < -5 or whz > 5:
        implausible.append(wh_indicator)
    if implausible:
        raise ImplausibleMeasurementError(
            "La combinación de peso, talla y edad necesita comprobarse ("
            + ", ".join(implausible)
            + "). Revisa si los valores y las unidades fueron ingresados correctamente."
        )

    red: list[str] = []
    yellow: list[str] = []
    if bilateral_edema:
        red.append("edema bilateral declarado")
    if SIX_MONTHS_DAYS <= days < WHO_MAX_AGE_DAYS and muac is not None:
        if muac < 115:
            red.append("MUAC menor de 115 mm")
        elif muac < 125:
            yellow.append("MUAC entre 115 y 124 mm")
    elif muac is not None and days < SIX_MONTHS_DAYS:
        warnings.append("El umbral MUAC 115/125 mm no se aplica antes de los 6 meses.")

    for label, z_value in ((wh_indicator, whz), ("peso/edad", waz), ("talla/edad", haz)):
        if z_value < -3:
            red.append(f"{label} menor de -3 DE")
        elif z_value < -2:
            yellow.append(f"{label} entre -3 y -2 DE")

    semaforo = "rojo" if red else "amarillo" if yellow else "verde"
    reasons = red if red else yellow if yellow else ["indicadores disponibles dentro de los umbrales de alerta"]
    return Assessment(
        age_days=days,
        waz=_round_z(waz),
        haz=_round_z(haz),
        whz=_round_z(whz),
        wh_indicator=wh_indicator,
        adjusted_height_cm=round(adjusted_height, 1),
        semaforo=semaforo,
        reasons=reasons,
        warnings=warnings,
    )
