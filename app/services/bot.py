"""Flujo conversacional confiable para el canal WhatsApp."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta

from ..core import clasificador, config, db
from ..domain.anthropometry import (
    AnthropometryError,
    ImplausibleMeasurementError,
    age_in_days,
)
from .llm import answer as llm_answer
from .guardrails import danger_response

CONSENT_VERSION = "2026-08-v1"

WELCOME = (
    "👋 ¡Hola! Soy *NutriCRED*.\n\n"
    "Acompaño a madres, padres y personas cuidadoras en el seguimiento del crecimiento infantil.\n\n"
    "Para comenzar, primero registraremos tus datos como persona cuidadora.\n\n"
    "1️⃣ Comenzar\n2️⃣ ¿Cómo funciona?"
)


def _first_name(value: str) -> str:
    return (str(value or "").strip().split() or ["hola"])[0]


def _welcome(identity: str) -> str:
    caregiver = db.obtener_cuidador(identity)
    if not caregiver:
        _save(identity, "onboarding", "intro", {})
        return WELCOME
    children = db.listar_ninos(identity)
    if not children:
        db.limpiar_estado_conversacion(identity)
        return (
            f"👋 Hola, *{_first_name(caregiver['full_name'])}*. Tu registro como persona "
            "cuidadora está listo.\n\n"
            "El siguiente paso es registrar a la niña o niño que está bajo tu cuidado.\n\n"
            "Escribe REGISTRAR para comenzar o PRIVACIDAD para revisar cómo usamos los datos."
        )
    return (
        f"👋 Hola, *{_first_name(caregiver['full_name'])}*. ¿Qué deseas hacer hoy?\n\n"
        "📏 Escribe MEDICIÓN para registrar peso y talla.\n"
        "📈 Escribe ESTADO para ver los últimos registros.\n"
        "➕ Escribe MÁS OPCIONES para abrir el resto del menú."
    )


def _plain(value: str) -> str:
    text = unicodedata.normalize("NFD", str(value).lower())
    return "".join(char for char in text if unicodedata.category(char) != "Mn").strip()


def _number(value: str) -> float:
    match = re.search(r"[-+]?\d+(?:[.,]\d+)?", value)
    if not match:
        raise ValueError
    return float(match.group().replace(",", "."))


def _weight_kg(value: str) -> float:
    """Normaliza gramos/kilos y algunas omisiones habituales de unidad."""
    number = _number(value)
    text = _plain(value)
    if re.search(r"\b(?:g|gr|grs|gramo|gramos)\b", text):
        number /= 1000
    elif not re.search(r"\b(?:kg|kgs|kilo|kilos|kilogramo|kilogramos)\b", text):
        # En menores de cinco años, una cifra mayor a 100 sin unidad suele
        # haberse escrito en gramos (por ejemplo, 10400 = 10.4 kg).
        if 100 < number <= 100_000:
            number /= 1000
    return round(number, 2)


def _height_cm(value: str) -> float:
    """Normaliza una longitud escrita en metros, centímetros o milímetros."""
    number = _number(value)
    text = _plain(value)
    if re.search(r"\b(?:mm|milimetro|milimetros)\b", text):
        number /= 10
    elif re.search(r"\b(?:cm|centimetro|centimetros)\b", text):
        pass
    elif re.search(r"\b(?:m|metro|metros)\b", text):
        number *= 100
    elif 0 < number < 3:
        number *= 100
    elif 300 < number <= 2500:
        number /= 10
    return round(number, 2)


def _muac_mm(value: str) -> float:
    """Normaliza el perímetro braquial a milímetros."""
    number = _number(value)
    text = _plain(value)
    if re.search(r"\b(?:cm|centimetro|centimetros)\b", text):
        number *= 10
    elif re.search(r"\b(?:m|metro|metros)\b", text):
        number *= 1000
    elif not re.search(r"\b(?:mm|milimetro|milimetros)\b", text):
        # 12.8 sin unidad es una forma común de reportar 12.8 cm.
        if 5 <= number <= 30:
            number *= 10
    return round(number, 1)


def _yes_no(value: str) -> bool | None:
    text = _plain(value)
    words = set(re.findall(r"[a-z]+", text))
    if text in {"si", "s", "1", "yes"} or "si" in words or text in {
        "claro",
        "de acuerdo",
        "confirmo",
        "correcto",
    }:
        return True
    if text in {"no", "n", "2"} or text.startswith("no "):
        return False
    return None


def _sex(value: str) -> str | None:
    text = _plain(value)
    words = set(re.findall(r"[a-z]+", text))
    if text in {"m", "1"} or words.intersection(
        {"masculino", "nino", "varon", "hombre"}
    ):
        return "M"
    if text in {"f", "2"} or words.intersection(
        {"femenino", "nina", "mujer"}
    ):
        return "F"
    return None


_MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}


def _birth_date(value: str) -> str | None:
    """Extrae fechas ISO, numéricas o escritas dentro de una frase."""
    text = _plain(value)
    iso = re.search(r"\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b", text)
    numeric = re.search(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](20\d{2})\b", text)
    written = re.search(
        r"\b(\d{1,2})\s+(?:de\s+)?(" + "|".join(_MONTHS) + r")\s+(?:de(?:l)?\s+)?(20\d{2})\b",
        text,
    )
    try:
        if iso:
            parsed = date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        elif numeric:
            parsed = date(int(numeric.group(3)), int(numeric.group(2)), int(numeric.group(1)))
        elif written:
            parsed = date(int(written.group(3)), _MONTHS[written.group(2)], int(written.group(1)))
        else:
            for fmt in ("%Y%m%d", "%d%m%Y"):
                try:
                    parsed = datetime.strptime(text.strip(), fmt).date()
                    break
                except ValueError:
                    continue
            else:
                return None
    except ValueError:
        return None
    return parsed.isoformat()


def _unknown_or_omit(value: str) -> bool:
    text = _plain(value)
    phrases = {
        "omitir",
        "no se",
        "no lo se",
        "no recuerdo",
        "no me acuerdo",
        "desconozco",
        "ninguno",
        "ninguna",
        "no tiene",
        "aun no",
        "todavia no",
    }
    return text in phrases or any(text.startswith(phrase + " ") for phrase in phrases)


def _caregiver_relationship(value: str) -> str | None:
    text = _plain(value)
    words = set(re.findall(r"[a-z]+", text))
    if text == "1" or words.intersection({"madre", "mama"}):
        return "madre"
    if text == "2" or words.intersection({"padre", "papa"}):
        return "padre"
    if text == "3" or words.intersection(
        {"cuidador", "cuidadora", "otro", "otra", "abuelo", "abuela", "tutor", "tutora"}
    ):
        return "cuidador"
    return None


_QUICK_REGISTRATION_LABELS = {
    "cuidador": "caregiver_name",
    "cuidadora": "caregiver_name",
    "persona cuidadora": "caregiver_name",
    "nombre cuidador": "caregiver_name",
    "nombre cuidadora": "caregiver_name",
    "relacion": "caregiver_relationship",
    "parentesco": "caregiver_relationship",
    "nino": "child_name",
    "nina": "child_name",
    "nombre del nino": "child_name",
    "nombre de la nina": "child_name",
    "nina o nino": "child_name",
    "nino o nina": "child_name",
    "menor": "child_name",
    "nacimiento": "birth_date",
    "fecha de nacimiento": "birth_date",
    "fecha nacimiento": "birth_date",
    "sexo": "sex",
    "distrito": "district",
    "establecimiento": "reported_health_center",
    "centro de salud": "reported_health_center",
}


def _quick_registration_template(caregiver: dict | None = None) -> str:
    caregiver_lines = (
        ""
        if caregiver
        else "Cuidador: Nombre completo\nRelación: madre, padre u otra persona cuidadora\n"
    )
    return (
        "⚡ *Registro rápido*\n\n"
        "Copia este formato, completa los datos y envíalo en un solo mensaje:\n\n"
        f"{caregiver_lines}"
        "Niña o niño: Nombre completo\n"
        "Nacimiento: 18/03/2024\n"
        "Sexo: femenino o masculino\n"
        f"Distrito: {(caregiver or {}).get('district') or 'Ventanilla'}\n"
        "Establecimiento: nombre, código RENIPRESS o NO LO SÉ\n\n"
        "Antes de guardar te mostraré un resumen para confirmar. Escribe CANCELAR para salir."
    )


def _parse_quick_registration(
    message: str, caregiver: dict | None = None
) -> tuple[dict, list[str]]:
    """Extrae un registro etiquetado; nunca persiste datos sin confirmación."""
    data: dict = {}
    for part in re.split(r"[\n;]+", str(message or "")):
        if ":" not in part:
            continue
        label, value = part.split(":", 1)
        key = _QUICK_REGISTRATION_LABELS.get(_plain(label))
        clean_value = value.strip()
        if key and clean_value:
            data[key] = clean_value

    if caregiver:
        data["caregiver_name"] = caregiver["full_name"]
        data["caregiver_relationship"] = caregiver.get("relationship") or "cuidador"
        data.setdefault("district", caregiver["district"])

    errors: list[str] = []
    relationship = _caregiver_relationship(data.get("caregiver_relationship", ""))
    if relationship:
        data["caregiver_relationship"] = relationship
    else:
        errors.append("relación con la niña o niño")

    parsed_birth_date = _birth_date(data.get("birth_date", ""))
    if parsed_birth_date:
        try:
            age_in_days(parsed_birth_date, date.today())
            data["birth_date"] = parsed_birth_date
        except AnthropometryError:
            errors.append("fecha de nacimiento válida para un menor de 5 años")
    else:
        errors.append("fecha de nacimiento")

    parsed_sex = _sex(data.get("sex", ""))
    if parsed_sex:
        data["sex"] = parsed_sex
    else:
        errors.append("sexo registrado")

    required_text = {
        "caregiver_name": "nombre de la persona cuidadora",
        "child_name": "nombre de la niña o niño",
        "district": "distrito",
    }
    for key, label in required_text.items():
        if len(str(data.get(key) or "").strip()) < 2:
            errors.append(label)

    center = str(data.get("reported_health_center") or "").strip()
    data["reported_health_center"] = None if not center or _unknown_or_omit(center) else center
    allowed = {
        "caregiver_name",
        "caregiver_relationship",
        "child_name",
        "birth_date",
        "sex",
        "district",
        "reported_health_center",
    }
    return {key: value for key, value in data.items() if key in allowed}, list(dict.fromkeys(errors))


def _looks_like_quick_registration(message: str) -> bool:
    labels = 0
    for part in re.split(r"[\n;]+", str(message or "")):
        if ":" in part and _plain(part.split(":", 1)[0]) in _QUICK_REGISTRATION_LABELS:
            labels += 1
    return labels >= 5


def _registration_summary(data: dict) -> str:
    center = data.get("reported_health_center") or "pendiente de vincular"
    sex = "Femenino" if data.get("sex") == "F" else "Masculino"
    return (
        "📝 *Revisa los datos antes de registrarlos*\n\n"
        f"Persona cuidadora: {data['caregiver_name']} ({data['caregiver_relationship']})\n"
        f"Niña o niño: {data['child_name']}\n"
        f"Nacimiento: {_display_date(data['birth_date'])}\n"
        f"Sexo registrado: {sex}\n"
        f"Distrito: {data['district']}\n"
        f"Establecimiento: {center}\n\n"
        "¿Autorizas registrar estos datos para el seguimiento de crecimiento? "
        "Responde SÍ o NO."
    )


def _height_candidate(message: str) -> float | None:
    text = _plain(message)
    if not any(term in text for term in ("talla", "altura", "longitud")):
        return None
    try:
        value = _height_cm(message)
    except ValueError:
        return None
    return value if 10 <= value <= 250 else None


def _parse_measurement_bundle(message: str) -> dict:
    """Extrae medidas etiquetadas sin usar el LLM ni guardar automáticamente."""
    text = _plain(message)
    data: dict = {}
    patterns = {
        "weight_kg": (
            r"\bpeso\b\s*(?:es|de|:|=)?\s*([-+]?\d+(?:[.,]\d+)?)\s*"
            r"(kg|kgs|kilo|kilos|kilogramo|kilogramos|g|gr|grs|gramo|gramos)?\b",
            _weight_kg,
        ),
        "height_cm": (
            r"\b(?:talla|altura|longitud)\b\s*(?:es|de|:|=)?\s*([-+]?\d+(?:[.,]\d+)?)\s*"
            r"(mm|milimetros?|cm|centimetros?|m|metros?)?\b",
            _height_cm,
        ),
        "muac_mm": (
            r"\b(?:muac|perimetro(?:\s+del)?\s+brazo)\b\s*(?:es|de|:|=)?\s*"
            r"([-+]?\d+(?:[.,]\d+)?)\s*(mm|milimetros?|cm|centimetros?)?\b",
            _muac_mm,
        ),
    }
    for key, (pattern, normalizer) in patterns.items():
        match = re.search(pattern, text)
        if not match:
            continue
        raw = " ".join(part for part in match.groups() if part)
        try:
            data[key] = normalizer(raw)
        except ValueError:
            continue

    if re.search(r"\bmuac\b[^.;,\n]*(?:omitir|no tengo|sin cinta)", text):
        data["muac_mm"] = None
    if any(word in text for word in ("acostado", "acostada", "longitud")):
        data["height_mode"] = "length"
    elif any(phrase in text for phrase in ("parado", "parada", "de pie")):
        data["height_mode"] = "height"

    if any(term in text for term in ("edema", "hinchazon", "pies hinchados")):
        negative = any(
            re.search(pattern, text)
            for pattern in (
                r"\bsin\s+(?:edema|hinchazon)",
                r"\b(?:edema|hinchazon)\s*(?:bilateral)?\s*(?::|=|es)?\s*no\b",
                r"\bno\s+(?:tiene|presenta|hay|veo|observo)\b[^.;,\n]*(?:edema|hinchazon)",
            )
        )
        data["bilateral_edema"] = not negative

    date_match = re.search(r"\bfecha\b\s*(?::|=)?\s*([^;\n,]+)", text)
    if date_match:
        measured_at = _birth_date(date_match.group(1))
        if measured_at and measured_at <= date.today().isoformat():
            data["measured_at"] = measured_at
    return data


def _measurement_confirmation(data: dict) -> str:
    muac = f"{data['muac_mm']} mm" if data.get("muac_mm") is not None else "omitido"
    return (
        f"📝 *Revisa los datos de {data['child_name']} antes de guardarlos*\n\n"
        f"📅 Fecha: {_display_date(data.get('measured_at') or date.today().isoformat())}\n"
        f"⚖️ Peso: {data['weight_kg']} kg\n"
        f"📏 {'Longitud acostado/a' if data['height_mode'] == 'length' else 'Talla de pie'}: "
        f"{data['height_cm']} cm\n"
        f"💪 Perímetro del brazo: {muac}\n"
        f"🦶 Hinchazón en ambos pies: {'sí' if data['bilateral_edema'] else 'no'}\n\n"
        "Esta medición quedará como *reportada por la persona cuidadora* y deberá "
        "confirmarse en un control de salud.\n\n"
        "¿Los datos coinciden con lo que mediste? Responde SÍ para guardarlos o NO para corregirlos."
    )


def _advance_measurement_bundle(identity: str, data: dict) -> str:
    """Continúa desde el primer dato faltante después de una captura agrupada."""
    if "weight_kg" in data and not 0.1 <= float(data["weight_kg"]) <= 100:
        data.pop("weight_kg", None)
    if "height_cm" in data and not 10 <= float(data["height_cm"]) <= 250:
        data.pop("height_cm", None)
    if data.get("muac_mm") is not None and not 10 <= float(data["muac_mm"]) <= 1000:
        data.pop("muac_mm", None)
    if "weight_kg" not in data:
        _save(identity, "measurement", "weight", data)
        understood = (
            f"Entendí una talla de {data['height_cm']} cm. "
            if data.get("height_cm") is not None
            else ""
        )
        return f"{understood}¿Cuál es su peso en kg? Ejemplo: 10.4"
    if "height_cm" not in data:
        _save(identity, "measurement", "height", data)
        return _height_prompt(data)
    if "height_mode" not in data:
        _save(identity, "measurement", "height_mode", data)
        return "¿La talla o longitud se midió ACOSTADO/A o PARADO/A?"
    if "muac_mm" not in data:
        _save(identity, "measurement", "muac", data)
        return "¿Cuál es el MUAC en milímetros? Ejemplo: 128. Si no tienes cinta MUAC, escribe OMITIR."
    if "bilateral_edema" not in data:
        _save(identity, "measurement", "edema", data)
        return "¿Observas hinchazón en AMBOS pies que deja marca al presionar? Responde SÍ o NO."
    _save(identity, "measurement", "confirm", data)
    return _measurement_confirmation(data)


def _length_tutorial(data: dict) -> str:
    birth_date = data.get("birth_date")
    try:
        under_two = birth_date and age_in_days(birth_date, date.today()) < 731
    except AnthropometryError:
        under_two = False
    if under_two:
        return (
            "\n\n🎥 Tutorial del Instituto Nacional de Salud para medir la "
            "longitud en menores de 2 años:\n"
            f"{config.TUTORIAL_LONGITUD_URL}"
        )
    return ""


def _height_prompt(data: dict) -> str:
    tutorial = _length_tutorial(data)
    return (
        "📏 ¿Cuál es la longitud o talla en centímetros? Ejemplo: 82.5."
        f"{tutorial}\n\nSi la medición fue en casa, procura confirmarla en su próximo control."
    )


def _display_date(value: str) -> str:
    try:
        return date.fromisoformat(str(value)[:10]).strftime("%d/%m/%Y")
    except ValueError:
        return str(value)


def _family_app_link() -> str:
    if not config.SEGUIMIENTO_URL:
        return ""
    return (
        "\n\n📈 *Revisa su trayectoria y acciones pendientes*\n"
        f"{config.SEGUIMIENTO_URL}\n"
        "Por seguridad, no compartas enlaces de acceso ni códigos de verificación."
    )


def _family_result_message(child_name: str, saved: dict) -> str:
    """Resumen corto para familias; el detalle técnico queda en la aplicación."""
    result = saved["assessment"]
    measurement = saved["measurement"]
    level = result["semaforo"]
    position = "longitud acostado/a" if measurement["height_mode"] == "length" else "talla de pie"
    headings = {
        "verde": "🟢 Sin señales de alerta con los datos registrados",
        "amarillo": "🟡 Conviene revisar esta medición",
        "rojo": "🔴 Se recomienda atención prioritaria",
    }
    actions = {
        "verde": "📅 Mantén al día sus controles de crecimiento.",
        "amarillo": "🏥 Confirma esta medición en su establecimiento de salud.",
        "rojo": "🚨 Busca una valoración presencial prioritaria. Si presenta signos de peligro, acude hoy.",
    }
    notes = ""
    if result["age_days"] < 183 and measurement.get("muac_mm") is not None:
        notes = (
            "\nℹ️ Por su edad, el perímetro del brazo se guardó, pero no orientó el resultado."
        )
    return (
        f"✅ *Medición guardada para {child_name}*\n\n"
        f"📅 Fecha: {_display_date(measurement['measured_at'])}\n"
        f"⚖️ Peso: {measurement['weight_kg']} kg\n"
        f"📏 {position.capitalize()}: {measurement['height_cm']} cm\n\n"
        f"*{headings[level]}*\n"
        f"{actions[level]}{notes}\n\n"
        "🏠 orientación preliminar basada en una medición familiar; debe confirmarse por personal de salud."
        f"{_family_app_link()}"
    )


def _save(identity: str, flow: str, step: str, data: dict) -> None:
    db.guardar_estado_conversacion(identity, {"flow": flow, "step": step, "data": data})


def _privacy_message() -> str:
    return (
        "🔐 *Uso de tus datos*\n\n"
        "Guardamos tus datos de contacto y los registros infantiles para dar seguimiento al "
        "crecimiento. El personal autorizado del establecimiento vinculado podrá revisarlos.\n\n"
        "Puedes dejar de usar el bot o solicitar al equipo la revisión de tus datos. "
        "Este servicio orienta y no reemplaza la atención profesional."
    )


def _consent_prompt() -> str:
    return (
        _privacy_message()
        + "\n\n¿Aceptas continuar con el registro como persona cuidadora? Responde SÍ o NO."
    )


def _start_onboarding(identity: str, *, show_intro: bool = True) -> str:
    step = "intro" if show_intro else "consent"
    _save(identity, "onboarding", step, {})
    return WELCOME if show_intro else _consent_prompt()


def _onboarding_step(identity: str, state: dict, message: str) -> str:
    step = state["step"]
    data = state.get("data", {})
    choice = _plain(message)
    if step == "intro":
        if choice in {"1", "comenzar", "empezar", "registrarme"}:
            _save(identity, "onboarding", "consent", data)
            return _consent_prompt()
        if choice in {"2", "como funciona", "informacion", "información"}:
            _save(identity, "onboarding", "consent", data)
            return (
                "🌱 Registrarás a las niñas o niños a tu cuidado y podrás guardar mediciones "
                "hechas en casa. Estas serán preliminares hasta que personal de salud las confirme.\n\n"
                + _consent_prompt()
            )
        return "Elige COMENZAR o ¿CÓMO FUNCIONA? para continuar."
    if step == "consent":
        accepted = _yes_no(message)
        if accepted is None:
            return "Responde SÍ para continuar o NO para salir."
        if not accepted:
            db.limpiar_estado_conversacion(identity)
            return "De acuerdo. No guardé datos personales. Puedes escribir HOLA cuando desees volver."
        data["consent_version"] = CONSENT_VERSION
        _save(identity, "onboarding", "relationship", data)
        return "Paso 1 de 3. ¿Cuál es tu relación con la niña o niño?"
    if step == "relationship":
        relationship = _caregiver_relationship(message)
        if not relationship:
            return "Elige MADRE, PADRE u OTRA PERSONA CUIDADORA."
        data["relationship"] = relationship
        _save(identity, "onboarding", "name", data)
        return "Paso 2 de 3. ¿Cuál es tu nombre completo?"
    if step == "name":
        if len(message.strip()) < 2:
            return "Escribe tu nombre completo."
        data["full_name"] = message.strip()
        _save(identity, "onboarding", "district", data)
        return "Paso 3 de 3. ¿En qué distrito vive tu familia?"
    if step == "district":
        if len(message.strip()) < 2:
            return "Escribe el distrito donde vive tu familia."
        data["district"] = message.strip()
        _save(identity, "onboarding", "confirm", data)
        relationship = {
            "madre": "Madre",
            "padre": "Padre",
            "cuidador": "Otra persona cuidadora",
        }[data["relationship"]]
        return (
            "📝 *Revisa tu registro*\n\n"
            f"Nombre: {data['full_name']}\n"
            f"Relación: {relationship}\n"
            f"Distrito: {data['district']}\n\n"
            "¿Los datos son correctos? Responde SÍ o NO."
        )
    confirmed = _yes_no(message)
    if confirmed is None:
        return "Responde SÍ para guardar o NO para corregir."
    if not confirmed:
        _save(identity, "onboarding", "relationship", {"consent_version": CONSENT_VERSION})
        return "De acuerdo, corrijamos el registro. ¿Cuál es tu relación con la niña o niño?"
    caregiver = db.registrar_cuidador(
        whatsapp_identity=identity,
        full_name=data["full_name"],
        relationship=data["relationship"],
        district=data["district"],
        consent_version=data.get("consent_version", CONSENT_VERSION),
    )
    _save(identity, "caregiver_child_offer", "confirm", {})
    return (
        f"✅ Listo, {_first_name(caregiver['full_name'])}. Tu registro como persona cuidadora está listo.\n\n"
        "¿Deseas registrar ahora a una niña o niño bajo tu cuidado?"
    )


def _caregiver_child_offer_step(identity: str, message: str) -> str:
    decision = _yes_no(message)
    if decision is None:
        return "Responde SÍ para registrar a una niña o niño o NO para hacerlo después."
    if decision:
        return _start_registration(identity)
    db.limpiar_estado_conversacion(identity)
    return (
        "De acuerdo. Cuando quieras continuar, escribe REGISTRAR.\n\n"
        "📅 Recuerda mantener al día los controles de crecimiento infantil."
    )


def _more_options(identity: str) -> str:
    if not db.obtener_cuidador(identity):
        return _start_onboarding(identity, show_intro=False)
    _save(identity, "more_menu", "action", {})
    return (
        "➕ *Más opciones*\n\n"
        "1️⃣ Registrar otra niña o niño\n"
        "2️⃣ Alertas y seguimiento\n"
        "3️⃣ Suplementos\n"
        "4️⃣ Establecimiento de salud\n"
        "5️⃣ Ayuda y privacidad\n"
        "6️⃣ Registro rápido"
    )


def _more_options_step(identity: str, message: str) -> str:
    choice = _plain(message)
    if choice in {"1", "registrar", "registrar nino", "registrar nina"}:
        return _start_registration(identity)
    if choice in {"2", "alertas", "seguimiento"}:
        return _start_followup(identity)
    if choice in {"3", "suplementos", "suplemento"}:
        return _start_supplement(identity)
    if choice in {"4", "establecimiento", "centro de salud"}:
        return _start_health_center_update(identity)
    if choice in {"5", "ayuda", "privacidad"}:
        db.limpiar_estado_conversacion(identity)
        return _privacy_message() + "\n\nEscribe INICIO para volver al menú principal."
    if choice in {"6", "registro rapido", "registrar rapido"}:
        return _start_quick_registration(identity)
    return "Elige una opción de la lista o escribe INICIO para volver."


def _start_registration(identity: str) -> str:
    caregiver = db.obtener_cuidador(identity)
    if not caregiver:
        return _start_onboarding(identity, show_intro=False)
    data = {
        "caregiver_name": caregiver["full_name"],
        "caregiver_relationship": caregiver.get("relationship") or "cuidador",
        "district": caregiver["district"],
    }
    _save(identity, "registration", "child_name", data)
    return "👧🏽 ¿Cuál es el nombre de la niña o niño que deseas registrar?"


def _start_quick_registration(identity: str) -> str:
    caregiver = db.obtener_cuidador(identity)
    if not caregiver:
        return (
            _start_onboarding(identity, show_intro=False)
            + "\n\nDespués podrás usar REGISTRO RÁPIDO para registrar a la niña o niño."
        )
    _save(identity, "quick_registration", "input", {})
    return _quick_registration_template(caregiver)


def _finish_registration(identity: str, data: dict) -> str:
    child = db.registrar_nino(whatsapp_identity=identity, **data)
    _save(
        identity,
        "first_measurement_offer",
        "confirm",
        {
            "child_id": child["id"],
            "child_name": child["full_name"],
            "birth_date": child["birth_date"],
        },
    )
    confirmation = (
        f"✅ {child['full_name']} ya estaba registrado/a; no creé un duplicado."
        if child.get("_already_registered")
        else f"✅ Registré correctamente a {child['full_name']}."
    )
    return (
        f"{confirmation}\n\n"
        "¿Deseas registrar ahora su primera medición de peso y talla?"
    )


def _quick_registration_step(identity: str, state: dict, message: str) -> str:
    if state["step"] == "input":
        caregiver = db.obtener_cuidador(identity)
        data, errors = _parse_quick_registration(message, caregiver)
        if errors:
            missing = "\n".join(f"• {item}" for item in errors)
            return (
                "No pude completar el registro. Revisa estos datos:\n"
                f"{missing}\n\n"
                + _quick_registration_template(caregiver)
            )
        _save(identity, "quick_registration", "consent", data)
        return _registration_summary(data)

    consent = _yes_no(message)
    if consent is None:
        return "Responde SÍ para autorizar el registro o NO para cancelarlo."
    if not consent:
        db.limpiar_estado_conversacion(identity)
        return "Registro cancelado. No guardé los datos."
    return _finish_registration(identity, state.get("data", {}))


def _start_measurement(
    identity: str,
    initial_height_cm: float | None = None,
    initial_message: str | None = None,
) -> str:
    children = db.listar_ninos(identity)
    if not children:
        return (
            "Antes de guardar una talla necesito saber a qué niña o niño corresponde. "
            "Escribe REGISTRAR para registrarlo primero."
        )
    captured = _parse_measurement_bundle(initial_message or "")
    if initial_height_cm is not None:
        captured["height_cm"] = initial_height_cm
    if len(children) == 1:
        data = {
            "child_id": children[0]["id"],
            "child_name": children[0]["full_name"],
            "birth_date": children[0]["birth_date"],
        }
        data.update(captured)
        if captured:
            return _advance_measurement_bundle(identity, data)
        _save(identity, "measurement", "weight", data)
        understood = f" Entendí una talla de {initial_height_cm} cm." if initial_height_cm else ""
        tutorial = _length_tutorial(data) if initial_height_cm else ""
        return (
            f"Mediremos a {children[0]['full_name']}.{understood} Para interpretar su crecimiento "
            f"necesito completar la medición. ¿Cuál es su peso en kg? Ejemplo: 10.4{tutorial}"
        )
    options = "\n".join(f"{i}. {child['full_name']}" for i, child in enumerate(children, 1))
    data = {"children": children, **captured}
    _save(identity, "measurement", "select_child", data)
    return f"¿A cuál de las niñas o niños registrados corresponde la medición?\n{options}"


def _start_measurement_for_child(
    identity: str, child_id: str, child_name: str, birth_date: str | None = None
) -> str:
    _save(
        identity,
        "measurement",
        "weight",
        {"child_id": child_id, "child_name": child_name, "birth_date": birth_date},
    )
    return f"Mediremos a {child_name}. ¿Cuál es su peso en kg? Ejemplo: 10.4"


def _status(identity: str, selected_child: dict | None = None) -> str:
    children = db.listar_ninos(identity)
    if not children:
        return "Aún no hay niñas o niños registrados. Escribe REGISTRAR para comenzar."
    if selected_child is None and len(children) > 1:
        options = "\n".join(
            f"{index}. {child['full_name']}" for index, child in enumerate(children, 1)
        )
        _save(identity, "status", "select_child", {"children": children})
        return f"¿De quién deseas ver el crecimiento?\n{options}"
    if selected_child is not None:
        children = [selected_child]
    labels = {
        "verde": "🟢 sin señales de alerta",
        "amarillo": "🟡 conviene revisar la medición",
        "rojo": "🔴 requiere atención prioritaria",
    }
    sections = []
    for child in children:
        state = db.consultar_estado(child["id"], identity)
        trajectory = (state or {}).get("trajectory") or []
        if not trajectory:
            sections.append(
                f"✅ *{child['full_name']} está registrado/a*\n"
                "Todavía no tiene mediciones guardadas. Escribe MEDICIÓN para registrar la primera."
            )
            continue
        recent_lines = []
        for index, measurement in enumerate(trajectory[:2], 1):
            result = measurement.get("assessment") or {}
            orientation = (
                "⚪ pendiente de confirmar"
                if measurement.get("validation_status") == "needs_review"
                else labels.get(result.get("semaforo"), "pendiente de evaluar")
            )
            recent_lines.append(
                f"{index}. {_display_date(measurement['measured_at'])}\n"
                f"   Peso: {measurement['weight_kg']} kg | Talla: {measurement['height_cm']} cm\n"
                f"   Fuente: {'personal de salud — verificada' if measurement.get('verification_status') == 'verified' else 'cuidador/a — preliminar'}\n"
                f"   Orientación: {orientation}"
            )
        clinical_reference = (state or {}).get("latest_verified")
        reference_note = (
            f"\nReferencia clínica más reciente: {_display_date(clinical_reference['measured_at'])}."
            if clinical_reference
            else "\nAún no hay una medición clínica verificada."
        )
        count_note = (
            f"\nMostrando los 2 más recientes de {len(trajectory)} registros."
            if len(trajectory) > 2
            else f"\nRegistros guardados: {len(trajectory)}."
        )
        sections.append(
            f"✅ *{child['full_name']} está registrado/a*\n"
            + "\n".join(recent_lines)
            + count_note
            + reference_note
        )
    return (
        "📈 *Últimos registros de crecimiento*\n\n"
        + "\n\n".join(sections)
        + "\n\nAquí mostramos como máximo las dos mediciones más recientes. "
        "Consulta la trayectoria completa y sus gráficos en la aplicación. "
        "Esta orientación no reemplaza la evaluación del personal de salud.\n\n"
        "📅 Mantén al día sus controles de crecimiento."
        + _family_app_link()
    )


def _status_step(identity: str, state: dict, message: str) -> str:
    children = (state.get("data") or {}).get("children") or []
    try:
        child = children[int(message.strip()) - 1]
    except (ValueError, IndexError):
        return "Elige el nombre de la niña o niño que deseas revisar."
    db.limpiar_estado_conversacion(identity)
    return _status(identity, child)


def _followup_menu(data: dict) -> str:
    urgent = (
        "\n\nSi observas un signo de peligro, no esperes al seguimiento por WhatsApp: "
        "acude a atención presencial."
        if data.get("alert_level") == "rojo"
        else ""
    )
    closing_label = (
        "confirmar la medición y cerrar la solicitud de verificación"
        if data.get("alert_type") == "verification_request"
        else "verificar y resolver la alerta clínica"
    )
    return (
        f"🧭 *Seguimiento de {data['child_name']}*\n\n"
        "¿Qué deseas hacer?\n"
        "1️⃣ Ver el establecimiento registrado\n"
        "2️⃣ Indicar cuándo podrías acudir\n"
        "3️⃣ Informar que ya acudieron\n"
        "4️⃣ Informar que necesitas ayuda para acudir\n"
        "5️⃣ Ver recomendaciones seguras para casa\n\n"
        "La familia puede informar avances, pero solo el personal de salud puede "
        f"{closing_label}.{urgent}\n\n"
        "Escribe MÁS TARDE para volver al menú sin registrar una acción."
    )


def _followup_data(alert: dict) -> dict:
    child = alert["child"]
    return {
        "alert_id": alert["id"],
        "alert_level": alert["nivel"],
        "alert_type": alert.get("alert_type", "verification_request"),
        "child_id": child["id"],
        "child_name": child["full_name"],
        "reported_health_center": child.get("reported_health_center"),
        "health_center_id": child.get("health_center_id"),
    }


def _start_followup(identity: str) -> str:
    alerts = db.alertas_activas_familia(identity)
    if not alerts:
        return (
            "✅ No tienes alertas activas para seguimiento en este momento.\n\n"
            "Puedes escribir ESTADO para revisar las últimas mediciones."
            + _family_app_link()
        )
    if len(alerts) == 1:
        data = _followup_data(alerts[0])
        _save(identity, "followup", "action", data)
        return _followup_menu(data)
    options = "\n".join(
        f"{index}. {alert['child']['full_name']} — {alert['nivel'].upper()}"
        for index, alert in enumerate(alerts, 1)
    )
    _save(identity, "followup", "select_alert", {"alerts": alerts})
    return f"¿Qué alerta deseas revisar?\n{options}"


def _record_followup(identity: str, data: dict, event_type: str, **extra) -> dict:
    return db.registrar_evento_seguimiento_cuidador(
        whatsapp_identity=identity,
        alert_id=data["alert_id"],
        event_type=event_type,
        **extra,
    )


def _followup_step(identity: str, state: dict, message: str) -> str:
    step = state["step"]
    data = state.get("data", {})
    choice = _plain(message)
    if choice in {"estado", "trayectoria", "resultados"}:
        db.limpiar_estado_conversacion(identity)
        return _status(identity)
    if choice in {"menu", "inicio", "hola", "ayuda"}:
        db.limpiar_estado_conversacion(identity)
        return _welcome(identity)
    if choice in {"despues", "no", "ahora no", "mas tarde", "omitir"}:
        db.limpiar_estado_conversacion(identity)
        return "De acuerdo. Puedes retomarlo escribiendo SEGUIMIENTO.\n\n" + _welcome(identity)
    if choice in {"registrar", "registro", "registrar nino", "registrar nina"}:
        db.limpiar_estado_conversacion(identity)
        return _start_registration(identity)
    if choice in {"medicion", "medir", "nueva medicion", "talla", "altura"}:
        db.limpiar_estado_conversacion(identity)
        return _start_measurement(identity)
    if choice in {"establecimiento", "centro de salud", "cambiar establecimiento"}:
        db.limpiar_estado_conversacion(identity)
        return _start_health_center_update(identity)
    if choice in {"seguimiento", "alerta", "alertas", "revisar seguimiento"}:
        return _followup_menu(data) if step == "action" else _start_followup(identity)
    if step == "select_alert":
        try:
            alert = data["alerts"][int(message.strip()) - 1]
        except (ValueError, IndexError):
            return "Responde con el número que aparece junto al nombre."
        selected = _followup_data(alert)
        _save(identity, "followup", "action", selected)
        return _followup_menu(selected)

    if step == "action":
        if choice in {"1", "establecimiento", "centro de salud"}:
            _record_followup(identity, data, "establishment_requested")
            db.limpiar_estado_conversacion(identity)
            center = data.get("reported_health_center")
            if center:
                return (
                    f"🏥 El establecimiento registrado para {data['child_name']} es: *{center}*.\n\n"
                    "Si necesitas corregirlo, escribe ESTABLECIMIENTO."
                    + _family_app_link()
                )
            return (
                "Todavía no hay un establecimiento registrado. Escribe ESTABLECIMIENTO para "
                "agregar el nombre o código RENIPRESS. Si existe un signo de peligro, acude al "
                "servicio de salud más cercano sin esperar la vinculación en la aplicación."
            )
        if choice in {"2", "voy a ir", "ire", "acudire", "planeo acudir"}:
            _save(identity, "followup", "attendance_plan", data)
            return (
                "¿Cuándo crees que podrías acudir?\n"
                "1️⃣ Hoy\n2️⃣ Durante los próximos 7 días\n3️⃣ Todavía no lo sé"
            )
        if choice in {"3", "ya fui", "ya fuimos", "ya acudi", "ya acudimos"}:
            _record_followup(identity, data, "attendance_reported")
            db.limpiar_estado_conversacion(identity)
            return (
                f"✅ Registré que {data['child_name']} ya acudió al establecimiento.\n\n"
                "La alerta continuará activa hasta que el personal de salud confirme la atención."
                + _family_app_link()
            )
        if choice in {"4", "necesito ayuda", "no pude ir", "no puedo ir"}:
            _save(identity, "followup", "barrier", data)
            return (
                "¿Cuál es la principal dificultad?\n"
                "1️⃣ No consigo cita\n"
                "2️⃣ Está lejos\n"
                "3️⃣ Transporte o costo\n"
                "4️⃣ Horarios\n"
                "5️⃣ No sé dónde atenderme\n"
                "6️⃣ Otra dificultad"
            )
        if choice in {"5", "recomendaciones", "consejos"}:
            _record_followup(identity, data, "recommendation_requested")
            db.limpiar_estado_conversacion(identity)
            priority = (
                "Por tratarse de una alerta roja, prioriza la valoración presencial y no la "
                "sustituyas con cambios de alimentación en casa.\n\n"
                if data.get("alert_level") == "rojo"
                else "Coordina un control para confirmar la medición.\n\n"
            )
            return (
                f"🏠 *Mientras coordinas el control*\n\n{priority}"
                "• Continúa la alimentación habitual adecuada para su edad.\n"
                "• No inicies, suspendas ni cambies dosis de suplementos sin indicación profesional.\n"
                "• Conserva el resultado y lleva sus controles anteriores al establecimiento.\n\n"
                "Estas recomendaciones son generales y no reemplazan la evaluación profesional."
                + _family_app_link()
            )
        return "Responde con una opción del 1 al 5."

    if step == "attendance_plan":
        planned_for = None
        if choice in {"1", "hoy"}:
            planned_for = date.today().isoformat()
        elif choice in {"2", "esta semana", "durante los proximos 7 dias"}:
            planned_for = (date.today() + timedelta(days=7)).isoformat()
        elif choice not in {"3", "no se", "todavia no lo se"}:
            return "Responde 1 para HOY, 2 para los PRÓXIMOS 7 DÍAS o 3 si aún no lo sabes."
        _record_followup(identity, data, "plans_to_attend", planned_for=planned_for)
        db.limpiar_estado_conversacion(identity)
        when = _display_date(planned_for) if planned_for else "fecha todavía por definir"
        return (
            f"✅ Registré el compromiso de seguimiento para {data['child_name']}: {when}.\n\n"
            "Esto no resuelve la alerta; el personal de salud deberá confirmar la atención."
            + _family_app_link()
        )

    if step == "barrier":
        barriers = {
            "1": ("appointment", "no consigues cita"),
            "2": ("distance", "el establecimiento está lejos"),
            "3": ("transport_cost", "tienes dificultad con transporte o costo"),
            "4": ("schedule", "los horarios dificultan acudir"),
            "5": ("unknown_facility", "no sabes dónde atenderte"),
            "6": ("other", "existe otra dificultad"),
        }
        if choice not in barriers:
            return "Responde con una opción del 1 al 6."
        barrier_code, label = barriers[choice]
        _record_followup(identity, data, "needs_support", barrier_code=barrier_code)
        db.limpiar_estado_conversacion(identity)
        return (
            f"🤝 Registré que {label}. El personal autorizado podrá ver esta barrera para "
            "orientar el seguimiento.\n\n"
            "Si hay un signo de peligro, no esperes el contacto por WhatsApp: busca atención presencial."
            + _family_app_link()
        )

    db.limpiar_estado_conversacion(identity)
    return _start_followup(identity)


def _health_center_prompt(child_name: str) -> str:
    return (
        f"🏥 Escribe el nombre o código RENIPRESS del establecimiento donde se controla {child_name}.\n\n"
        "Si todavía no lo sabes, responde NO LO SÉ. Podrás agregarlo después escribiendo ESTABLECIMIENTO."
    )


def _start_health_center_update(identity: str) -> str:
    children = db.listar_ninos(identity)
    if not children:
        return "Primero registra a la niña o niño. Escribe REGISTRAR para comenzar."
    if len(children) == 1:
        child = children[0]
        _save(
            identity,
            "health_center_update",
            "value",
            {"child_id": child["id"], "child_name": child["full_name"]},
        )
        return _health_center_prompt(child["full_name"])
    options = "\n".join(f"{index}. {child['full_name']}" for index, child in enumerate(children, 1))
    _save(identity, "health_center_update", "select_child", {"children": children})
    return f"¿Para cuál niña o niño deseas agregar el establecimiento?\n{options}"


_SUPPLEMENT_LABELS = {
    "iron": "Hierro (sulfato ferroso u otra presentación indicada)",
    "mnp": "Micronutrientes en polvo (MNP)",
    "vitamin_a": "Vitamina A",
    "zinc": "Zinc",
    "vitamin_d": "Vitamina D",
    "other": "Otro suplemento indicado",
}


def _supplement_menu(identity: str, child: dict) -> str:
    data = {"child_id": child["id"], "child_name": child["full_name"]}
    _save(identity, "supplement", "action", data)
    return (
        f"💊 *Seguimiento de suplementos de {child['full_name']}*\n\n"
        "1️⃣ Registrar una condición diagnosticada por personal de salud\n"
        "2️⃣ Registrar un suplemento indicado por personal de salud\n"
        "3️⃣ Marcar la toma de hoy\n"
        "4️⃣ Ver el seguimiento de los últimos 7 días\n"
        "5️⃣ Configurar un recordatorio diario\n\n"
        "La información comunicada por la familia queda como *reportada* hasta que "
        "el personal de salud la verifique. El bot no prescribe ni modifica dosis."
    )


def _start_supplement(identity: str) -> str:
    children = db.listar_ninos(identity)
    if not children:
        return "Primero registra a la niña o niño. Escribe REGISTRAR para comenzar."
    if len(children) == 1:
        return _supplement_menu(identity, children[0])
    options = "\n".join(
        f"{index}. {child['full_name']}" for index, child in enumerate(children, 1)
    )
    _save(identity, "supplement", "select_child", {"children": children})
    return f"¿Para cuál niña o niño deseas revisar la suplementación?\n{options}"


def _supplement_plans_prompt(identity: str, data: dict, next_step: str) -> str:
    plans = db.listar_planes_suplemento(
        whatsapp_identity=identity, child_id=data["child_id"]
    )
    if not plans:
        _save(identity, "supplement", "action", data)
        return (
            "Todavía no hay un suplemento activo registrado. Elige la opción 2 para "
            "registrar una indicación del personal de salud."
        )
    if len(plans) == 1:
        selected = {**data, "plan_id": plans[0]["id"], "plan": plans[0]}
        _save(identity, "supplement", next_step, selected)
        return ""
    options = "\n".join(
        f"{index}. {_SUPPLEMENT_LABELS.get(plan['supplement_type'], plan['supplement_type'])}"
        for index, plan in enumerate(plans, 1)
    )
    _save(identity, "supplement", f"select_plan_{next_step}", {**data, "plans": plans})
    return f"¿Qué suplemento deseas seleccionar?\n{options}"


def _supplement_step(identity: str, state: dict, message: str) -> str:
    step = state["step"]
    data = state.get("data", {})
    choice = _plain(message)
    if step == "select_child":
        try:
            child = data["children"][int(message.strip()) - 1]
        except (ValueError, IndexError):
            return "Responde con el número que aparece junto al nombre."
        return _supplement_menu(identity, child)

    if step.startswith("select_plan_"):
        try:
            plan = data["plans"][int(message.strip()) - 1]
        except (ValueError, IndexError):
            return "Responde con el número que aparece junto al suplemento."
        next_step = step.removeprefix("select_plan_")
        data = {**data, "plan_id": plan["id"], "plan": plan}
        data.pop("plans", None)
        _save(identity, "supplement", next_step, data)
        if next_step == "intake":
            return "¿Hoy recibió el suplemento según la indicación? Responde SÍ, NO o AÚN NO."
        return "¿Deseas recibir un recordatorio diario por WhatsApp? Responde SÍ o NO."

    if step == "action":
        if choice in {"1", "condicion", "diagnostico", "enfermedad"}:
            _save(identity, "supplement", "condition_name", data)
            return (
                "¿Qué condición fue diagnosticada o comunicada por el personal de salud? "
                "Por ejemplo: anemia por deficiencia de hierro."
            )
        if choice in {"2", "suplemento", "registrar suplemento"}:
            _save(identity, "supplement", "supplement_type", data)
            return (
                "¿Qué suplemento le indicaron?\n"
                "1️⃣ Hierro\n2️⃣ Micronutrientes en polvo (MNP)\n3️⃣ Vitamina A\n"
                "4️⃣ Zinc\n5️⃣ Vitamina D\n6️⃣ Otro\n7️⃣ No toma suplementos actualmente"
            )
        if choice in {"3", "toma", "marcar toma", "tomo", "no tomo"}:
            prompt = _supplement_plans_prompt(identity, data, "intake")
            return prompt or "¿Hoy recibió el suplemento según la indicación? Responde SÍ, NO o AÚN NO."
        if choice in {"4", "resumen", "adherencia", "seguimiento"}:
            summary = db.resumen_adherencia(
                whatsapp_identity=identity, child_id=data["child_id"], days=7
            )
            db.limpiar_estado_conversacion(identity)
            if not summary["plans"]:
                return "Todavía no hay un suplemento activo registrado. Escribe SUPLEMENTOS para agregarlo."
            return (
                f"📅 *Seguimiento de {data['child_name']} — últimos 7 días*\n\n"
                f"✅ Registrado como tomó: {summary['taken']}\n"
                f"❌ Registrado como no tomó: {summary['not_taken']}\n"
                f"⏳ Pendiente: {summary['pending']}\n\n"
                "Los días sin registro no se cuentan como incumplimiento. El personal de salud "
                "podrá revisar este resumen en la aplicación."
            )
        if choice in {"5", "recordatorio", "recordatorios"}:
            prompt = _supplement_plans_prompt(identity, data, "reminder_enabled")
            return prompt or "¿Deseas recibir un recordatorio diario por WhatsApp? Responde SÍ o NO."
        return "Responde con una opción del 1 al 5."

    if step == "condition_name":
        if len(message.strip()) < 3:
            return "Escribe el nombre de la condición indicada por el personal de salud."
        data["condition_name"] = message.strip()
        _save(identity, "supplement", "diagnosed_by", data)
        return (
            "¿Quién se lo diagnosticó? Puedes escribir el nombre o profesión del personal "
            "de salud. Si no lo recuerdas, responde NO LO SÉ."
        )
    if step == "diagnosed_by":
        data["diagnosed_by_name"] = None if _unknown_or_omit(message) else message.strip()
        _save(identity, "supplement", "condition_date", data)
        return "¿Cuándo se lo diagnosticaron? Escribe una fecha o responde NO LO SÉ."
    if step == "condition_date":
        if _unknown_or_omit(message):
            data["diagnosed_at"] = None
        else:
            parsed = _birth_date(message)
            if not parsed or parsed > date.today().isoformat():
                return "No pude entender la fecha. Prueba con 18/03/2026 o responde NO LO SÉ."
            data["diagnosed_at"] = parsed
        condition = db.registrar_condicion_reportada(
            whatsapp_identity=identity,
            child_id=data["child_id"],
            condition_name=data["condition_name"],
            diagnosed_by_name=data.get("diagnosed_by_name"),
            diagnosed_at=data.get("diagnosed_at"),
        )
        db.limpiar_estado_conversacion(identity)
        verifier = condition.get("diagnosed_by_name") or "profesional por identificar"
        return (
            f"✅ Guardé la condición reportada para {data['child_name']}.\n\n"
            f"Condición: {condition['condition_name']}\n"
            f"Quién diagnosticó: {verifier}\n"
            "Estado: pendiente de verificación clínica.\n\n"
            "Esto no constituye un nuevo diagnóstico. El personal autorizado podrá conciliarlo "
            "con la historia clínica."
        )

    if step == "supplement_type":
        if choice == "7":
            db.limpiar_estado_conversacion(identity)
            return (
                f"De acuerdo. No registré un plan activo de suplementos para {data['child_name']}. "
                "Si recibe una indicación después, vuelve escribiendo SUPLEMENTOS."
            )
        types = {"1": "iron", "2": "mnp", "3": "vitamin_a", "4": "zinc", "5": "vitamin_d", "6": "other"}
        supplement_type = types.get(choice)
        if not supplement_type:
            return "Responde con una opción del 1 al 7."
        data["supplement_type"] = supplement_type
        _save(identity, "supplement", "supplement_purpose", data)
        return (
            "¿Para qué se lo indicaron?\n"
            "1️⃣ Para prevenir una deficiencia\n"
            "2️⃣ Como parte de un tratamiento\n"
            "3️⃣ No lo sé"
        )
    if step == "supplement_purpose":
        purposes = {"1": "preventive", "2": "therapeutic", "3": "unknown"}
        if choice not in purposes:
            return "Responde con una opción del 1 al 3."
        data["purpose"] = purposes[choice]
        conditions = db.listar_condiciones(
            whatsapp_identity=identity, child_id=data["child_id"]
        )
        data["condition_id"] = (
            conditions[0]["id"]
            if data["purpose"] == "therapeutic" and len(conditions) == 1
            else None
        )
        _save(identity, "supplement", "indicated_by", data)
        return (
            "¿Quién indicó este suplemento? Escribe el nombre o profesión del personal de salud. "
            "Si no lo recuerdas, responde NO LO SÉ."
        )
    if step == "indicated_by":
        data["indicated_by_name"] = None if _unknown_or_omit(message) else message.strip()
        _save(identity, "supplement", "supplement_confirm", data)
        return (
            f"Confirma el registro para {data['child_name']}:\n"
            f"Suplemento: {_SUPPLEMENT_LABELS[data['supplement_type']]}\n"
            f"Indicado por: {data.get('indicated_by_name') or 'pendiente de identificar'}\n\n"
            "¿Deseas guardarlo como información reportada? Responde SÍ o NO."
        )
    if step == "supplement_confirm":
        confirmed = _yes_no(message)
        if confirmed is None:
            return "Responde SÍ para guardar o NO para cancelar."
        if not confirmed:
            db.limpiar_estado_conversacion(identity)
            return "De acuerdo, no guardé el suplemento."
        plan = db.registrar_plan_suplemento_reportado(
            whatsapp_identity=identity,
            child_id=data["child_id"],
            supplement_type=data["supplement_type"],
            purpose=data.get("purpose", "unknown"),
            indicated_by_name=data.get("indicated_by_name"),
            condition_id=data.get("condition_id"),
        )
        db.limpiar_estado_conversacion(identity)
        return (
            f"✅ Guardé {_SUPPLEMENT_LABELS[plan['supplement_type']]} para {data['child_name']} "
            "como indicación reportada.\n\n"
            "El personal de salud deberá verificar el producto, finalidad, frecuencia y dosis. "
            "Escribe TOMA para registrar si lo recibió hoy."
        )

    if step == "intake":
        if choice in {"aun no", "todavia no", "pendiente", "3"}:
            status = "pending"
        else:
            decision = _yes_no(message)
            if decision is None:
                return "Responde SÍ, NO o AÚN NO."
            status = "taken" if decision else "not_taken"
        if status == "not_taken":
            _save(identity, "supplement", "not_taken_reason", data)
            return (
                "¿Qué ocurrió?\n1️⃣ Lo olvidé\n2️⃣ Se terminó el suplemento\n"
                "3️⃣ La niña o niño no quiso tomarlo\n4️⃣ Presentó una molestia\n"
                "5️⃣ No entendí la indicación\n6️⃣ Otro motivo"
            )
        db.registrar_toma_suplemento(
            whatsapp_identity=identity, plan_id=data["plan_id"], intake_status=status
        )
        db.limpiar_estado_conversacion(identity)
        return (
            "✅ Registré la toma de hoy."
            if status == "taken"
            else "⏳ Registré que la toma de hoy aún está pendiente."
        )
    if step == "not_taken_reason":
        reasons = {"1": "forgot", "2": "out_of_stock", "3": "child_refused", "4": "discomfort", "5": "instructions_unclear", "6": "other"}
        if choice not in reasons:
            return "Responde con una opción del 1 al 6."
        db.registrar_toma_suplemento(
            whatsapp_identity=identity,
            plan_id=data["plan_id"],
            intake_status="not_taken",
            reason_code=reasons[choice],
        )
        db.limpiar_estado_conversacion(identity)
        warning = (
            " Si presentó una molestia, consulta al establecimiento antes de cambiar o suspender la indicación."
            if choice == "4"
            else ""
        )
        return f"✅ Registré que hoy no lo tomó y el motivo. El personal de salud podrá revisarlo.{warning}"

    if step == "reminder_enabled":
        enabled = _yes_no(message)
        if enabled is None:
            return "Responde SÍ o NO."
        if not enabled:
            db.configurar_recordatorio_suplemento(
                whatsapp_identity=identity, plan_id=data["plan_id"], enabled=False
            )
            db.limpiar_estado_conversacion(identity)
            return "Desactivé la preferencia de recordatorio para este suplemento."
        _save(identity, "supplement", "reminder_time", data)
        return "¿A qué hora deseas el recordatorio? Escribe una hora como 08:00 o 19:30."
    if step == "reminder_time":
        time_match = re.search(r"\b(?:[01]\d|2[0-3]):[0-5]\d\b", message)
        if not time_match:
            return "Escribe la hora en formato de 24 horas, por ejemplo 08:00 o 19:30."
        preference = db.configurar_recordatorio_suplemento(
            whatsapp_identity=identity,
            plan_id=data["plan_id"],
            enabled=True,
            reminder_time=time_match.group(),
        )
        db.limpiar_estado_conversacion(identity)
        return (
            f"✅ Guardé tu preferencia de recordatorio diario a las {preference['reminder_time']}.\n\n"
            "El envío automático requiere mantener activo el proceso programado del proyecto y "
            "respetar las reglas de plantillas de WhatsApp."
        )

    db.limpiar_estado_conversacion(identity)
    return _start_supplement(identity)


def _health_center_update_step(identity: str, state: dict, message: str) -> str:
    data = state.get("data", {})
    if state["step"] == "select_child":
        try:
            child = data["children"][int(message.strip()) - 1]
        except (ValueError, IndexError):
            return "Responde con el número que aparece junto al nombre."
        _save(
            identity,
            "health_center_update",
            "value",
            {"child_id": child["id"], "child_name": child["full_name"]},
        )
        return _health_center_prompt(child["full_name"])
    if _unknown_or_omit(message):
        db.limpiar_estado_conversacion(identity)
        return (
            "De acuerdo, no hice cambios. Cuando conozcas el establecimiento, "
            "escribe ESTABLECIMIENTO para agregarlo."
        )
    updated = db.actualizar_establecimiento_nino(
        whatsapp_identity=identity,
        child_id=data["child_id"],
        reported_health_center=message.strip(),
    )
    db.limpiar_estado_conversacion(identity)
    if updated.get("health_center_id"):
        return f"✅ Vinculé a {data['child_name']} con el establecimiento indicado."
    return (
        f"✅ Guardé “{message.strip()}” como establecimiento reportado para {data['child_name']}. "
        "Quedará pendiente de vinculación si el nombre no coincide de forma inequívoca."
    )


def _registration_step(identity: str, state: dict, message: str) -> str:
    step = state["step"]
    data = state.get("data", {})
    if _plain(message) in {"registro rapido", "registrar rapido", "registro completo"}:
        return _start_quick_registration(identity)
    if step == "caregiver_relationship":
        relationship = _caregiver_relationship(message)
        if not relationship:
            return "Responde 1 para MADRE, 2 para PADRE o 3 para OTRA PERSONA CUIDADORA."
        data["caregiver_relationship"] = relationship
        _save(identity, "registration", "caregiver_name", data)
        return "Gracias. ¿Cuál es tu nombre completo como persona cuidadora?"
    if step == "caregiver_name":
        if len(message.strip()) < 2:
            return "Escribe el nombre de la persona cuidadora."
        data["caregiver_name"] = message.strip()
        _save(identity, "registration", "child_name", data)
        return "¿Cuál es el nombre de la niña o niño?"
    if step == "child_name":
        if len(message.strip()) < 2:
            return "Escribe el nombre de la niña o niño."
        data["child_name"] = message.strip()
        _save(identity, "registration", "birth_date", data)
        return (
            "📅 ¿Cuál es su fecha de nacimiento? Puedes escribirla de distintas formas:\n"
            "• 2024-03-18\n• 18/03/2024\n• Nació el 18 de marzo de 2024"
        )
    if step == "birth_date":
        parsed_birth_date = _birth_date(message)
        if not parsed_birth_date:
            return (
                "No pude identificar la fecha. Prueba con 18/03/2024, "
                "2024-03-18 o “nació el 18 de marzo de 2024”."
            )
        try:
            age_in_days(parsed_birth_date, date.today())
        except AnthropometryError as exc:
            return f"No pude usar esa fecha: {exc}"
        data["birth_date"] = parsed_birth_date
        _save(identity, "registration", "sex", data)
        return (
            "¿Qué sexo figura en su registro de nacimiento? Puedes responder, por ejemplo: "
            "NIÑA, NIÑO, FEMENINO, MASCULINO, F o M."
        )
    if step == "sex":
        value = _sex(message)
        if not value:
            return "No pude identificarlo. Responde NIÑA/FEMENINO/F o NIÑO/MASCULINO/M."
        data["sex"] = value
        _save(identity, "registration", "district_confirm", data)
        return f"¿La niña o niño vive también en {data['district']}?"
    if step == "district_confirm":
        same_district = _yes_no(message)
        if same_district is True:
            _save(identity, "registration", "health_center", data)
            return _health_center_prompt(data["child_name"])
        if same_district is False or _plain(message) in {"otro", "otro distrito"}:
            _save(identity, "registration", "district", data)
            return "¿En qué distrito vive la niña o niño?"
        # También se acepta escribir directamente un distrito diferente.
        if len(message.strip()) >= 2:
            data["district"] = message.strip()
            _save(identity, "registration", "health_center", data)
            return _health_center_prompt(data["child_name"])
        return "Responde SÍ, NO o escribe directamente el distrito."
    if step == "district":
        if len(message.strip()) < 2:
            return "Escribe el distrito donde vive la niña o niño."
        data["district"] = message.strip()
        _save(identity, "registration", "health_center", data)
        return _health_center_prompt(data["child_name"])
    if step == "health_center":
        data["reported_health_center"] = None if _unknown_or_omit(message) else message.strip()
        _save(identity, "registration", "consent", data)
        return _registration_summary(data)
    consent = _yes_no(message)
    if consent is None:
        return "Responde SÍ para autorizar el registro o NO para cancelarlo."
    if not consent:
        db.limpiar_estado_conversacion(identity)
        return "Registro cancelado. No guardé los datos."
    return _finish_registration(identity, data)


def _first_measurement_offer_step(identity: str, state: dict, message: str) -> str:
    decision = _yes_no(message)
    if decision is None:
        return "Responde SÍ para registrar su primera medición ahora o NO para hacerlo después."
    if decision:
        data = state.get("data", {})
        return _start_measurement_for_child(
            identity, data["child_id"], data["child_name"], data.get("birth_date")
        )
    db.limpiar_estado_conversacion(identity)
    return "De acuerdo. Cuando tengas las medidas, escribe MEDICIÓN.\n\n" + _welcome(identity)


def _measurement_step(identity: str, state: dict, message: str) -> str:
    step = state["step"]
    data = state.get("data", {})
    if step == "select_child":
        try:
            choice = int(message.strip()) - 1
            child = data["children"][choice]
        except (ValueError, IndexError):
            return "Responde con el número que aparece junto al nombre."
        captured = {key: value for key, value in data.items() if key != "children"}
        data = {
            "child_id": child["id"],
            "child_name": child["full_name"],
            "birth_date": child["birth_date"],
        }
        data.update(captured)
        if captured:
            return _advance_measurement_bundle(identity, data)
        selected_height = data.get("height_cm")
        _save(identity, "measurement", "weight", data)
        understood = f" Conservaré la talla de {selected_height} cm." if selected_height else ""
        tutorial = _length_tutorial(data) if selected_height else ""
        return (
            f"Mediremos a {child['full_name']}.{understood} "
            f"¿Cuál es su peso en kg? Ejemplo: 10.4{tutorial}"
        )
    bundled = _parse_measurement_bundle(message)
    if bundled and step != "confirm":
        data.update(bundled)
        return _advance_measurement_bundle(identity, data)
    if step == "weight":
        try:
            value = _weight_kg(message)
            if not 0.1 <= value <= 100:
                raise ValueError
        except ValueError:
            return (
                "No pude entender el peso. Puedes escribir 10.4 kg o 10400 g. "
                "Debe ser un valor mayor que cero."
            )
        data["weight_kg"] = value
        if data.get("height_cm") is not None:
            _save(identity, "measurement", "height_mode", data)
            return "¿La talla o longitud se midió ACOSTADO/A o PARADO/A?"
        _save(identity, "measurement", "height", data)
        return _height_prompt(data)
    if step == "height":
        try:
            value = _height_cm(message)
            if not 10 <= value <= 250:
                raise ValueError
        except ValueError:
            return (
                "No pude entender la talla. Puedes escribir 82 cm, 0.82 m o 820 mm. "
                "Debe ser un valor mayor que cero."
            )
        data["height_cm"] = value
        _save(identity, "measurement", "height_mode", data)
        return "¿La medición se hizo ACOSTADO/A o PARADO/A?"
    if step == "height_mode":
        text = _plain(message)
        if text in {"acostado", "acostada", "longitud"}:
            data["height_mode"] = "length"
        elif text in {"parado", "parada", "talla", "de pie"}:
            data["height_mode"] = "height"
        else:
            return "Responde ACOSTADO/A o PARADO/A según cómo hiciste la medición."
        _save(identity, "measurement", "muac", data)
        return "¿Cuál es el MUAC en milímetros? Ejemplo: 128. Si no tienes cinta MUAC adecuada, escribe OMITIR."
    if step == "muac":
        if _plain(message) in {"omitir", "no tengo", "no"}:
            data["muac_mm"] = None
        else:
            try:
                value = _muac_mm(message)
                if not 10 <= value <= 1000:
                    raise ValueError
            except ValueError:
                return (
                    "No pude entender el perímetro del brazo. Puedes escribir 128 mm "
                    "o 12.8 cm; también puedes escribir OMITIR."
                )
            data["muac_mm"] = value
        _save(identity, "measurement", "edema", data)
        return "¿Observas hinchazón en AMBOS pies que deja marca al presionar suavemente? Responde SÍ o NO."
    if step == "edema":
        value = _yes_no(message)
        if value is None:
            return "Responde SÍ o NO. Si observas edema bilateral, busca valoración presencial hoy."
        data["bilateral_edema"] = value
        _save(identity, "measurement", "confirm", data)
        return _measurement_confirmation(data)
    confirmed = _yes_no(message)
    if confirmed is None:
        return "Responde SÍ para guardar o NO para volver a ingresar la medición."
    if not confirmed:
        base = {"child_id": data["child_id"], "child_name": data["child_name"]}
        _save(identity, "measurement", "weight", base)
        return "De acuerdo, no guardé esa medición. Ingresa nuevamente el peso en kg."
    try:
        saved = db.registrar_medicion(
            whatsapp_identity=identity,
            child_id=data["child_id"],
            measured_at=data.get("measured_at") or date.today().isoformat(),
            weight_kg=data["weight_kg"],
            height_cm=data["height_cm"],
            height_mode=data["height_mode"],
            muac_mm=data["muac_mm"],
            bilateral_edema=data["bilateral_edema"],
        )
    except (ImplausibleMeasurementError, AnthropometryError) as exc:
        try:
            pending = db.registrar_medicion_para_revision(
                whatsapp_identity=identity,
                child_id=data["child_id"],
                measured_at=data.get("measured_at") or date.today().isoformat(),
                weight_kg=data["weight_kg"],
                height_cm=data["height_cm"],
                height_mode=data["height_mode"],
                muac_mm=data["muac_mm"],
                bilateral_edema=data["bilateral_edema"],
                validation_notes=str(exc),
            )
        except db.MeasurementReviewStorageUnavailableError:
            db.limpiar_estado_conversacion(identity)
            return (
                "⚠️ No pude conservar la medición como pendiente porque falta actualizar "
                "la base de datos. Si Supabase ya existía, el equipo debe ejecutar las "
                "migraciones de mediciones de *db/migrations* y volver a intentarlo."
            )
        db.limpiar_estado_conversacion(identity)
        measurement = pending["measurement"]
        urgent = (
            "\n\nComo indicaste hinchazón en ambos pies, busca valoración presencial hoy."
            if data.get("bilateral_edema")
            else ""
        )
        return (
            f"✅ *Guardé la medición de {data['child_name']} como pendiente de confirmar*\n\n"
            f"📅 Fecha: {_display_date(measurement['measured_at'])}\n"
            f"⚖️ Peso reportado: {measurement['weight_kg']} kg\n"
            f"📏 Talla reportada: {measurement['height_cm']} cm\n\n"
            "⚠️ La combinación necesita comprobarse. La guardé para revisión, pero no le asigné "
            "color ni generé una alerta clínica.\n\n"
            "🏥 Repite la medición o confírmala en su establecimiento de salud."
            f"{urgent}{_family_app_link()}"
        )
    except ValueError as exc:
        db.limpiar_estado_conversacion(identity)
        return f"No pude guardar la medición: {exc} Escribe MEDICIÓN para volver a intentarlo."
    result_message = _family_result_message(data["child_name"], saved)
    if saved.get("alert"):
        followup = _followup_data({**saved["alert"], "child": saved["child"]})
        _save(identity, "followup", "action", followup)
        return result_message + "\n\n" + _followup_menu(followup)
    db.limpiar_estado_conversacion(identity)
    return result_message


def respond(message: str, identity: str) -> str:
    message = str(message or "").strip()
    if not message:
        return _welcome(identity)
    # Antes del consentimiento solo se conserva el estado temporal necesario
    # para continuar el diálogo; el historial empieza cuando el cuidador existe.
    persist_history = db.obtener_cuidador(identity) is not None
    if persist_history:
        db.guardar_mensaje(identity, "user", message)
    state = db.estado_conversacion(identity)
    command = _plain(message)
    if command in {"cancelar", "salir"}:
        db.limpiar_estado_conversacion(identity)
        answer = "Operación cancelada.\n\n" + _welcome(identity)
    elif command in {"inicio", "volver", "volver al inicio"}:
        db.limpiar_estado_conversacion(identity)
        answer = _welcome(identity)
    elif command in {"privacidad", "mis datos", "uso de datos"}:
        db.limpiar_estado_conversacion(identity)
        answer = _privacy_message() + "\n\nEscribe INICIO para volver."
    elif command in {"registro rapido", "registrar rapido", "registro completo"}:
        answer = _start_quick_registration(identity)
    elif danger_response(message) and not (
        state.get("flow") == "measurement" and state.get("step") == "edema"
    ):
        answer = danger_response(message) or _welcome(identity)
    elif state.get("flow") == "onboarding":
        answer = _onboarding_step(identity, state, message)
    elif state.get("flow") == "caregiver_child_offer":
        answer = _caregiver_child_offer_step(identity, message)
    elif state.get("flow") == "more_menu":
        answer = _more_options_step(identity, message)
    elif state.get("flow") == "status":
        answer = _status_step(identity, state, message)
    elif state.get("flow") == "registration":
        answer = _registration_step(identity, state, message)
    elif state.get("flow") == "quick_registration":
        answer = _quick_registration_step(identity, state, message)
    elif state.get("flow") == "measurement":
        answer = _measurement_step(identity, state, message)
    elif state.get("flow") == "first_measurement_offer":
        answer = _first_measurement_offer_step(identity, state, message)
    elif state.get("flow") == "health_center_update":
        answer = _health_center_update_step(identity, state, message)
    elif state.get("flow") == "followup":
        try:
            answer = _followup_step(identity, state, message)
        except db.FollowupStorageUnavailableError:
            db.limpiar_estado_conversacion(identity)
            answer = (
                "⚠️ La medición y la alerta sí quedaron guardadas, pero todavía no pude "
                "registrar esta acción de seguimiento porque falta actualizar la base de datos.\n\n"
                "El equipo debe ejecutar *db/schema.sql* en Supabase. Después podrás volver "
                "a intentarlo escribiendo SEGUIMIENTO."
            )
    elif state.get("flow") == "supplement":
        try:
            answer = _supplement_step(identity, state, message)
        except db.SupplementStorageUnavailableError:
            db.limpiar_estado_conversacion(identity)
            answer = (
                "⚠️ El módulo de suplementos todavía no está disponible en la base. "
                "El equipo debe ejecutar *db/migrations/20260813_supplement_tracking.sql* "
                "en Supabase y volver a intentarlo."
            )
    elif command in {"1", "registrar", "registro", "registrar nino", "registrar nina"}:
        answer = _start_registration(identity)
    elif command in {"2", "medicion", "medir", "nueva medicion", "talla", "altura", "registrar talla"}:
        answer = _start_measurement(identity)
    elif command in {"3", "estado", "trayectoria", "resultados"}:
        answer = _status(identity)
    elif command in {"4", "ayuda", "menu", "inicio", "hola"}:
        answer = _welcome(identity)
    elif command in {"mas opciones", "otras opciones", "ver opciones"}:
        answer = _more_options(identity)
    elif command in {
        "5",
        "establecimiento",
        "centro de salud",
        "agregar establecimiento",
        "cambiar establecimiento",
    }:
        answer = _start_health_center_update(identity)
    elif command in {
        "6",
        "seguimiento",
        "alerta",
        "alertas",
        "revisar seguimiento",
        "ya acudi",
        "no pude ir",
    }:
        answer = _start_followup(identity)
    elif command in {
        "7",
        "suplemento",
        "suplementos",
        "suplementacion",
        "srsi",
        "toma",
        "registrar toma",
        "recordatorio suplemento",
    }:
        answer = _start_supplement(identity)
    elif state.get("_expired"):
        answer = (
            "⌛ La sesión anterior se cerró después de 2 horas sin actividad. "
            "Tus registros guardados siguen disponibles; solo descarté el proceso incompleto.\n\n"
            + _welcome(identity)
        )
    elif _looks_like_quick_registration(message):
        answer = _quick_registration_step(
            identity, {"flow": "quick_registration", "step": "input", "data": {}}, message
        )
    else:
        action = clasificador.detectar_accion(message)
        if action == "registration":
            answer = _start_registration(identity)
        elif action == "measurement":
            answer = _start_measurement(
                identity,
                _height_candidate(message),
                initial_message=message,
            )
        elif action == "status":
            answer = _status(identity)
        elif action == "health_center":
            answer = _start_health_center_update(identity)
        elif action == "followup":
            answer = _start_followup(identity)
        else:
            classified = clasificador.clasificar(message)
            faq = classified.get("faq")
            answer = faq["answer"] if faq and classified["confidence"] > 0 else None
            if answer is None:
                answer = llm_answer(message, identity) or _welcome(identity)
    if persist_history or db.obtener_cuidador(identity) is not None:
        db.guardar_mensaje(identity, "assistant", answer)
    return answer
