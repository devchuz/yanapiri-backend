"""Persistencia Supabase con un fallback completo en memoria para la demo local."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from threading import RLock
from uuid import uuid4

from . import config
from ..domain.anthropometry import assess_child

_client = None
_lock = RLock()


class FollowupStorageUnavailableError(RuntimeError):
    """El esquema de seguimiento todavía no fue aplicado en Supabase."""


class MeasurementReviewStorageUnavailableError(RuntimeError):
    """Faltan las columnas que distinguen una medición pendiente de revisión."""


class SupplementStorageUnavailableError(RuntimeError):
    """El esquema SRSI todavía no fue aplicado en Supabase."""


def _empty_memory() -> dict:
    return {
        "caregivers": [],
        "children": [],
        "measurements": [],
        "assessments": [],
        "alerts": [],
        "alert_followup_events": [],
        "appointments": [],
        "child_conditions": [],
        "supplement_plans": [],
        "supplement_intake_events": [],
        "reminder_preferences": [],
        "messages": [],
        "states": {},
        "webhook_events": set(),
    }


_mem = _empty_memory()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    return str(uuid4())


def _date_text(value: date | datetime | str) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def _alert_priority_key(alert: dict) -> tuple[int, float]:
    """Prioriza nivel y distingue alerta clínica de solicitud de verificación."""
    try:
        created = datetime.fromisoformat(str(alert.get("created_at") or "").replace("Z", "+00:00"))
        timestamp = created.timestamp()
    except ValueError:
        timestamp = 0
    clinical = alert.get("alert_type") == "clinical_alert"
    priority = (
        0 if alert.get("nivel") == "rojo" and clinical
        else 1 if alert.get("nivel") == "rojo"
        else 2 if clinical
        else 3
    )
    return (priority, -timestamp)


def _response_data(response, default=None):
    """Extrae ``data`` incluso si PostgREST devuelve None para cero filas."""
    if response is None:
        return default
    data = getattr(response, "data", None)
    return default if data is None else data


def _state_with_expiry(state: dict) -> dict:
    payload = deepcopy(state)
    if payload:
        expires = datetime.now(timezone.utc) + timedelta(
            minutes=config.CONVERSATION_SESSION_MINUTES
        )
        payload["_expires_at"] = expires.isoformat()
    return payload


def _state_is_expired(state: dict, now: datetime | None = None) -> bool:
    expires_at = state.get("_expires_at") if state else None
    if not expires_at:
        return False
    try:
        expires = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
    except ValueError:
        return True
    return expires <= (now or datetime.now(timezone.utc))


def _missing_relationship_column(exc: Exception) -> bool:
    detail = str(exc).lower()
    return "pgrst204" in detail and "relationship" in detail and "caregivers" in detail


def _missing_followup_table(exc: Exception) -> bool:
    detail = str(exc).lower()
    return "alert_followup_events" in detail and any(
        marker in detail
        for marker in ("pgrst205", "42p01", "schema cache", "does not exist", "not found")
    )


def _missing_measurement_review_columns(exc: Exception) -> bool:
    detail = str(exc).lower()
    return "measurements" in detail and any(
        column in detail for column in ("validation_status", "validation_notes")
    ) and any(
        marker in detail
        for marker in ("pgrst204", "42703", "schema cache", "does not exist")
    )


def _missing_measurement_provenance_columns(exc: Exception) -> bool:
    detail = str(exc).lower()
    return "measurements" in detail and any(
        column in detail for column in ("verification_status", "recorded_by", "verified_at")
    ) and any(
        marker in detail
        for marker in ("pgrst204", "42703", "schema cache", "does not exist")
    )


def _missing_alert_type_column(exc: Exception) -> bool:
    detail = str(exc).lower()
    return "alerts" in detail and "alert_type" in detail and any(
        marker in detail
        for marker in ("pgrst204", "42703", "schema cache", "does not exist")
    )


def _legacy_measurement_range_constraints(exc: Exception) -> bool:
    detail = str(exc).lower()
    return any(
        constraint in detail
        for constraint in (
            "measurements_weight_kg_check",
            "measurements_height_cm_check",
            "measurements_muac_mm_check",
        )
    ) and any(marker in detail for marker in ("23514", "check constraint", "violates"))


def _missing_supplement_tables(exc: Exception) -> bool:
    detail = str(exc).lower()
    return any(
        table in detail
        for table in (
            "child_conditions",
            "supplement_plans",
            "supplement_intake_events",
            "supplement_reminder_preferences",
        )
    ) and any(
        marker in detail
        for marker in ("pgrst205", "42p01", "schema cache", "does not exist", "not found")
    )


def _missing_appointments_table(exc: Exception) -> bool:
    detail = str(exc).lower()
    return "appointments" in detail and any(
        marker in detail
        for marker in ("pgrst205", "42p01", "schema cache", "does not exist", "not found")
    )


def _get_client():
    global _client
    if _client is None and config.usando_supabase():
        from supabase import create_client

        _client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_KEY)
    return _client


def usando_supabase() -> bool:
    return _get_client() is not None


def usando_turso() -> bool:
    """Alias temporal para no romper consumidores antiguos durante la migración."""
    return usando_supabase()


def autenticar_profesional(access_token: str) -> dict:
    """Valida un access token de Supabase y exige membresía clínica activa."""
    client = _get_client()
    if not client:
        raise RuntimeError("La API clínica requiere Supabase configurado.")
    token = str(access_token or "").strip()
    if not token:
        raise PermissionError("Falta el token de acceso profesional.")
    try:
        response = client.auth.get_user(token)
        user = getattr(response, "user", None)
        user_id = str(getattr(user, "id", "") or "")
    except Exception as exc:
        raise PermissionError("Token profesional inválido o vencido.") from exc
    if not user_id:
        raise PermissionError("Token profesional inválido o vencido.")
    memberships = (
        client.table("health_center_members")
        .select("user_id,health_center_id,role")
        .eq("user_id", user_id)
        .execute()
        .data
        or []
    )
    if not memberships:
        raise PermissionError("El usuario no está vinculado a un establecimiento de salud.")
    profile = _response_data(
        client.table("professional_profiles")
        .select("user_id,full_name,profession,license_number,verified")
        .eq("user_id", user_id)
        .maybe_single()
        .execute()
    ) or {"user_id": user_id, "verified": False}
    return {"user_id": user_id, "profile": profile, "memberships": memberships}


def verificar_acceso_profesional(user_id: str, child_id: str) -> dict:
    """Devuelve el niño solo si pertenece a un centro del profesional."""
    child = _get_child(child_id)
    if not child:
        raise ValueError("Niña o niño no encontrado.")
    client = _get_client()
    if not client:
        # El fallback de memoria se usa únicamente en pruebas locales.
        return child
    memberships = (
        client.table("health_center_members")
        .select("health_center_id,role")
        .eq("user_id", str(user_id))
        .execute()
        .data
        or []
    )
    allowed = any(
        member.get("role") == "admin"
        or (
            child.get("health_center_id")
            and member.get("health_center_id") == child.get("health_center_id")
        )
        for member in memberships
    )
    if not allowed:
        raise PermissionError("No tienes acceso a este historial infantil.")
    return child


def reset_memory() -> None:
    """Limpia el fallback. Se usa en pruebas; no elimina datos de Supabase."""
    global _mem
    if usando_supabase():
        return
    with _lock:
        _mem = _empty_memory()


def guardar_mensaje(telefono: str, rol: str, contenido: str) -> None:
    row = {
        "id": _uuid(),
        "whatsapp_identity": str(telefono),
        "role": rol,
        "content": contenido,
        "created_at": _now(),
    }
    client = _get_client()
    if client:
        client.table("conversation_messages").insert(row).execute()
        return
    with _lock:
        _mem["messages"].append(row)


def historial_conversacion(telefono: str, limite: int = 20) -> list[dict]:
    client = _get_client()
    if client:
        data = (
            client.table("conversation_messages")
            .select("role,content,created_at")
            .eq("whatsapp_identity", str(telefono))
            .order("created_at", desc=True)
            .limit(limite)
            .execute()
            .data
        )
        return list(reversed(data or []))
    rows = [m for m in _mem["messages"] if m["whatsapp_identity"] == str(telefono)]
    return deepcopy(rows[-limite:])


def estado_conversacion(telefono: str) -> dict:
    client = _get_client()
    if client:
        response = (
            client.table("conversation_states")
            .select("state")
            .eq("whatsapp_identity", str(telefono))
            .maybe_single()
            .execute()
        )
        state = deepcopy((_response_data(response) or {}).get("state") or {})
    else:
        state = deepcopy(_mem["states"].get(str(telefono), {}))
    if _state_is_expired(state):
        limpiar_estado_conversacion(telefono)
        return {"_expired": True}
    return state


def guardar_estado_conversacion(telefono: str, state: dict) -> None:
    stored_state = _state_with_expiry(state)
    client = _get_client()
    if client:
        client.table("conversation_states").upsert(
            {
                "whatsapp_identity": str(telefono),
                "state": stored_state,
                "updated_at": _now(),
            },
            on_conflict="whatsapp_identity",
        ).execute()
        return
    with _lock:
        _mem["states"][str(telefono)] = stored_state


def limpiar_estado_conversacion(telefono: str) -> None:
    guardar_estado_conversacion(telefono, {})


def _caregiver_for_identity(identity: str) -> dict | None:
    client = _get_client()
    if client:
        response = (
            client.table("caregivers")
            .select("*")
            .eq("whatsapp_identity", str(identity))
            .maybe_single()
            .execute()
        )
        return _response_data(response)
    return next((c for c in _mem["caregivers"] if c["whatsapp_identity"] == str(identity)), None)


def _resolve_health_center(reported: str, district: str) -> dict | None:
    """Busca por código RENIPRESS o nombre; no asigna un centro por simple cercanía."""
    if not reported or reported.strip().lower() in {"omitir", "no se", "no sé", "ninguno"}:
        return None
    client = _get_client()
    if not client:
        return None
    centers = (
        client.table("health_centers")
        .select("id,renaes_code,name,district")
        .eq("active", True)
        .execute()
        .data
        or []
    )
    needle = reported.strip().casefold()
    district_key = district.strip().casefold()
    exact = [
        center
        for center in centers
        if needle in {str(center.get("renaes_code") or "").casefold(), center["name"].casefold()}
    ]
    if exact:
        return exact[0]
    matches = [
        center
        for center in centers
        if needle in center["name"].casefold()
        and (not district_key or district_key == str(center.get("district") or "").casefold())
    ]
    return matches[0] if len(matches) == 1 else None


def registrar_nino(
    *,
    whatsapp_identity: str,
    caregiver_name: str,
    child_name: str,
    birth_date: str,
    sex: str,
    district: str,
    caregiver_relationship: str = "cuidador",
    phone_number: str | None = None,
    health_center_id: str | None = None,
    reported_health_center: str | None = None,
) -> dict:
    sex = sex.strip().upper()
    if sex not in {"M", "F"}:
        raise ValueError("El sexo debe ser M o F.")
    caregiver = _caregiver_for_identity(whatsapp_identity)
    client = _get_client()
    if caregiver is None:
        caregiver = {
            "id": _uuid(),
            "whatsapp_identity": str(whatsapp_identity),
            "phone_number": phone_number,
            "full_name": caregiver_name.strip(),
            "relationship": caregiver_relationship.strip().lower(),
            "district": district.strip(),
            "consent_at": _now(),
            "created_at": _now(),
            "updated_at": _now(),
        }
        if client:
            try:
                response = client.table("caregivers").insert(caregiver).execute()
            except Exception as exc:
                if not _missing_relationship_column(exc):
                    raise
                # Compatibilidad temporal hasta ejecutar la migración de
                # caregivers.relationship en el SQL Editor.
                legacy_caregiver = {k: v for k, v in caregiver.items() if k != "relationship"}
                response = client.table("caregivers").insert(legacy_caregiver).execute()
                print("[supabase] falta caregivers.relationship; registro guardado sin relación")
            caregiver = _response_data(response, [])[0]
        else:
            _mem["caregivers"].append(caregiver)
    else:
        updates = {
            "full_name": caregiver_name.strip(),
            "relationship": caregiver_relationship.strip().lower(),
            "district": district.strip(),
            "updated_at": _now(),
        }
        if phone_number:
            updates["phone_number"] = phone_number
        if client:
            try:
                client.table("caregivers").update(updates).eq("id", caregiver["id"]).execute()
            except Exception as exc:
                if not _missing_relationship_column(exc):
                    raise
                legacy_updates = {k: v for k, v in updates.items() if k != "relationship"}
                client.table("caregivers").update(legacy_updates).eq("id", caregiver["id"]).execute()
                print("[supabase] falta caregivers.relationship; relación no persistida")
            caregiver = {**caregiver, **updates}
        else:
            caregiver.update(updates)

    normalized_child_name = " ".join(child_name.split()).casefold()
    normalized_birth_date = _date_text(birth_date)
    if client:
        possible_duplicates = (
            client.table("children")
            .select("*")
            .eq("caregiver_id", caregiver["id"])
            .eq("birth_date", normalized_birth_date)
            .eq("active", True)
            .execute()
            .data
            or []
        )
    else:
        possible_duplicates = [
            child
            for child in _mem["children"]
            if child["caregiver_id"] == caregiver["id"]
            and child["birth_date"] == normalized_birth_date
            and child["active"]
        ]
    existing = next(
        (
            child
            for child in possible_duplicates
            if " ".join(str(child["full_name"]).split()).casefold() == normalized_child_name
        ),
        None,
    )
    if existing:
        return {**deepcopy(existing), "_already_registered": True}

    center = None if health_center_id else _resolve_health_center(reported_health_center or "", district)
    health_center_id = health_center_id or (center or {}).get("id")
    child = {
        "id": _uuid(),
        "caregiver_id": caregiver["id"],
        "health_center_id": health_center_id,
        "full_name": child_name.strip(),
        "birth_date": normalized_birth_date,
        "sex": sex,
        "district": district.strip(),
        "reported_health_center": (reported_health_center or "").strip() or None,
        "active": True,
        "created_at": _now(),
        "updated_at": _now(),
    }
    if client:
        return client.table("children").insert(child).execute().data[0]
    with _lock:
        _mem["children"].append(child)
    return deepcopy(child)


def listar_ninos(whatsapp_identity: str) -> list[dict]:
    caregiver = _caregiver_for_identity(whatsapp_identity)
    if not caregiver:
        return []
    client = _get_client()
    if client:
        return (
            client.table("children")
            .select("*")
            .eq("caregiver_id", caregiver["id"])
            .eq("active", True)
            .order("created_at")
            .execute()
            .data
            or []
        )
    return deepcopy(
        [c for c in _mem["children"] if c["caregiver_id"] == caregiver["id"] and c["active"]]
    )


def nombres_familia(whatsapp_identity: str) -> list[str]:
    """Nombres conocidos que deben redactarse antes de consultar al LLM."""
    caregiver = _caregiver_for_identity(whatsapp_identity)
    names = [str((caregiver or {}).get("full_name") or "").strip()]
    names.extend(str(child.get("full_name") or "").strip() for child in listar_ninos(whatsapp_identity))
    return [name for name in names if name]


def actualizar_establecimiento_nino(
    *, whatsapp_identity: str, child_id: str, reported_health_center: str
) -> dict:
    child = _get_child(child_id, whatsapp_identity)
    if not child:
        raise ValueError("No se encontró a la niña o niño para esta persona cuidadora.")
    reported = reported_health_center.strip()
    if not reported:
        raise ValueError("Escribe el nombre o código RENIPRESS del establecimiento.")
    center = _resolve_health_center(reported, str(child.get("district") or ""))
    updates = {
        "reported_health_center": reported,
        "health_center_id": (center or {}).get("id"),
        "updated_at": _now(),
    }
    client = _get_client()
    if client:
        response = client.table("children").update(updates).eq("id", child_id).execute()
        rows = _response_data(response, [])
        return rows[0] if rows else {**child, **updates}
    child.update(updates)
    return deepcopy(child)


def _get_child(child_id: str, whatsapp_identity: str | None = None) -> dict | None:
    client = _get_client()
    if client:
        query = client.table("children").select("*").eq("id", child_id)
        child = _response_data(query.maybe_single().execute())
    else:
        child = next((c for c in _mem["children"] if c["id"] == child_id), None)
    if not child or not whatsapp_identity:
        return child
    caregiver = _caregiver_for_identity(whatsapp_identity)
    return child if caregiver and child["caregiver_id"] == caregiver["id"] else None


_SUPPLEMENT_TYPES = {"iron", "mnp", "vitamin_a", "zinc", "vitamin_d", "other"}


def registrar_condicion_reportada(
    *,
    whatsapp_identity: str,
    child_id: str,
    condition_name: str,
    diagnosed_by_name: str | None = None,
    diagnosed_at: str | None = None,
    reported_health_center: str | None = None,
    condition_code: str | None = None,
    source_system: str = "caregiver",
    external_record_id: str | None = None,
) -> dict:
    """Registra un antecedente informado; no lo convierte en diagnóstico verificado."""
    child = _get_child(child_id, whatsapp_identity)
    if not child:
        raise ValueError("No se encontró a la niña o niño para esta persona cuidadora.")
    name = " ".join(str(condition_name or "").split())
    if len(name) < 3:
        raise ValueError("Escribe el nombre de la condición indicada por el personal de salud.")
    row = {
        "id": _uuid(),
        "child_id": child_id,
        "condition_code": (condition_code or "").strip() or None,
        "condition_name": name,
        "diagnosed_at": _date_text(diagnosed_at) if diagnosed_at else None,
        "diagnosing_professional_id": None,
        "diagnosed_by_name": (diagnosed_by_name or "").strip() or None,
        "health_center_id": child.get("health_center_id"),
        "reported_health_center": (reported_health_center or "").strip() or None,
        "verification_status": "reported",
        "condition_status": "active",
        "source_system": source_system,
        "external_record_id": (external_record_id or "").strip() or None,
        "reported_by_identity": str(whatsapp_identity),
        "created_at": _now(),
        "updated_at": _now(),
    }
    client = _get_client()
    if client:
        try:
            return client.table("child_conditions").insert(row).execute().data[0]
        except Exception as exc:
            if _missing_supplement_tables(exc):
                raise SupplementStorageUnavailableError from exc
            raise
    with _lock:
        _mem["child_conditions"].append(row)
    return deepcopy(row)


def listar_condiciones(*, whatsapp_identity: str, child_id: str) -> list[dict]:
    if not _get_child(child_id, whatsapp_identity):
        return []
    client = _get_client()
    if client:
        try:
            return (
                client.table("child_conditions")
                .select("*")
                .eq("child_id", child_id)
                .eq("condition_status", "active")
                .order("created_at", desc=True)
                .execute()
                .data
                or []
            )
        except Exception as exc:
            if _missing_supplement_tables(exc):
                raise SupplementStorageUnavailableError from exc
            raise
    return deepcopy(
        [
            row
            for row in _mem["child_conditions"]
            if row["child_id"] == child_id and row["condition_status"] == "active"
        ]
    )


def registrar_plan_suplemento_reportado(
    *,
    whatsapp_identity: str,
    child_id: str,
    supplement_type: str,
    purpose: str = "unknown",
    indicated_by_name: str | None = None,
    reported_health_center: str | None = None,
    condition_id: str | None = None,
) -> dict:
    """Registra una indicación informada por la familia, pendiente de conciliación clínica."""
    child = _get_child(child_id, whatsapp_identity)
    if not child:
        raise ValueError("No se encontró a la niña o niño para esta persona cuidadora.")
    supplement = str(supplement_type).strip().lower()
    if supplement not in _SUPPLEMENT_TYPES:
        raise ValueError("El suplemento indicado no está reconocido.")
    if purpose not in {"preventive", "therapeutic", "unknown"}:
        raise ValueError("La finalidad debe ser preventiva, terapéutica o desconocida.")
    if condition_id and not any(
        item["id"] == condition_id
        for item in listar_condiciones(whatsapp_identity=whatsapp_identity, child_id=child_id)
    ):
        raise ValueError("La condición indicada no pertenece a esta niña o niño.")
    row = {
        "id": _uuid(),
        "child_id": child_id,
        "condition_id": condition_id,
        "supplement_type": supplement,
        "purpose": purpose,
        "start_date": date.today().isoformat(),
        "end_date": None,
        "schedule_text": None,
        "indicating_professional_id": None,
        "indicated_by_name": (indicated_by_name or "").strip() or None,
        "health_center_id": child.get("health_center_id"),
        "reported_health_center": (reported_health_center or "").strip() or None,
        "verification_status": "reported",
        "status": "active",
        "source_system": "caregiver",
        "external_record_id": None,
        "created_at": _now(),
        "updated_at": _now(),
    }
    client = _get_client()
    if client:
        try:
            return client.table("supplement_plans").insert(row).execute().data[0]
        except Exception as exc:
            if _missing_supplement_tables(exc):
                raise SupplementStorageUnavailableError from exc
            raise
    with _lock:
        _mem["supplement_plans"].append(row)
    return deepcopy(row)


def listar_planes_suplemento(
    *, whatsapp_identity: str, child_id: str, active_only: bool = True
) -> list[dict]:
    if not _get_child(child_id, whatsapp_identity):
        return []
    client = _get_client()
    if client:
        try:
            query = client.table("supplement_plans").select("*").eq("child_id", child_id)
            if active_only:
                query = query.eq("status", "active")
            return query.order("created_at", desc=True).execute().data or []
        except Exception as exc:
            if _missing_supplement_tables(exc):
                raise SupplementStorageUnavailableError from exc
            raise
    rows = [row for row in _mem["supplement_plans"] if row["child_id"] == child_id]
    if active_only:
        rows = [row for row in rows if row["status"] == "active"]
    return deepcopy(rows)


def registrar_toma_suplemento(
    *,
    whatsapp_identity: str,
    plan_id: str,
    intake_status: str,
    reason_code: str | None = None,
    notes: str | None = None,
    scheduled_for: str | None = None,
) -> dict:
    status = str(intake_status).strip().lower()
    if status not in {"taken", "not_taken", "pending"}:
        raise ValueError("El estado debe ser tomó, no tomó o pendiente.")
    plans = []
    for child in listar_ninos(whatsapp_identity):
        plans.extend(listar_planes_suplemento(whatsapp_identity=whatsapp_identity, child_id=child["id"]))
    plan = next((item for item in plans if item["id"] == plan_id), None)
    if not plan:
        raise ValueError("No se encontró un plan activo para esta familia.")
    target_date = _date_text(scheduled_for or date.today().isoformat())
    row = {
        "id": _uuid(),
        "plan_id": plan_id,
        "scheduled_for": target_date,
        "intake_status": status,
        "reason_code": (reason_code or "").strip() or None,
        "notes": (notes or "").strip()[:500] or None,
        "reported_by_identity": str(whatsapp_identity),
        "reported_at": _now(),
        "created_at": _now(),
    }
    client = _get_client()
    if client:
        try:
            response = client.table("supplement_intake_events").upsert(
                row, on_conflict="plan_id,scheduled_for"
            ).execute()
            return response.data[0]
        except Exception as exc:
            if _missing_supplement_tables(exc):
                raise SupplementStorageUnavailableError from exc
            raise
    with _lock:
        existing = next(
            (
                item for item in _mem["supplement_intake_events"]
                if item["plan_id"] == plan_id and item["scheduled_for"] == target_date
            ),
            None,
        )
        if existing:
            existing.update({key: value for key, value in row.items() if key != "id"})
            return deepcopy(existing)
        _mem["supplement_intake_events"].append(row)
    return deepcopy(row)


def resumen_adherencia(*, whatsapp_identity: str, child_id: str, days: int = 7) -> dict:
    plans = listar_planes_suplemento(whatsapp_identity=whatsapp_identity, child_id=child_id)
    plan_ids = {plan["id"] for plan in plans}
    since = (date.today() - timedelta(days=max(1, days) - 1)).isoformat()
    client = _get_client()
    if client and plan_ids:
        try:
            rows = (
                client.table("supplement_intake_events")
                .select("*")
                .in_("plan_id", list(plan_ids))
                .gte("scheduled_for", since)
                .execute()
                .data
                or []
            )
        except Exception as exc:
            if _missing_supplement_tables(exc):
                raise SupplementStorageUnavailableError from exc
            raise
    else:
        rows = [
            row for row in _mem["supplement_intake_events"]
            if row["plan_id"] in plan_ids and row["scheduled_for"] >= since
        ]
    counts = {
        status: sum(1 for row in rows if row["intake_status"] == status)
        for status in ("taken", "not_taken", "pending")
    }
    return {"days": days, "plans": deepcopy(plans), "events": deepcopy(rows), **counts}


def configurar_recordatorio_suplemento(
    *, whatsapp_identity: str, plan_id: str, enabled: bool, reminder_time: str = "08:00"
) -> dict:
    plans = []
    for child in listar_ninos(whatsapp_identity):
        plans.extend(listar_planes_suplemento(whatsapp_identity=whatsapp_identity, child_id=child["id"]))
    if not any(plan["id"] == plan_id for plan in plans):
        raise ValueError("No se encontró un plan activo para esta familia.")
    if not __import__("re").fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", reminder_time):
        raise ValueError("La hora debe tener formato HH:MM.")
    row = {
        "id": _uuid(),
        "plan_id": plan_id,
        "whatsapp_identity": str(whatsapp_identity),
        "enabled": bool(enabled),
        "reminder_time": reminder_time,
        "timezone": "America/Lima",
        "consented_at": _now() if enabled else None,
        "updated_at": _now(),
    }
    client = _get_client()
    if client:
        try:
            return client.table("supplement_reminder_preferences").upsert(
                row, on_conflict="plan_id"
            ).execute().data[0]
        except Exception as exc:
            if _missing_supplement_tables(exc):
                raise SupplementStorageUnavailableError from exc
            raise
    with _lock:
        existing = next(
            (item for item in _mem["reminder_preferences"] if item["plan_id"] == plan_id), None
        )
        if existing:
            existing.update({key: value for key, value in row.items() if key != "id"})
            return deepcopy(existing)
        _mem["reminder_preferences"].append(row)
    return deepcopy(row)


def recordatorios_suplemento_pendientes(
    *, reminder_time: str, on_date: str | None = None
) -> list[dict]:
    """Devuelve recordatorios consentidos y aún no respondidos; no envía mensajes."""
    target_date = _date_text(on_date or date.today().isoformat())
    client = _get_client()
    if client:
        try:
            preferences = (
                client.table("supplement_reminder_preferences")
                .select("*")
                .eq("enabled", True)
                .eq("reminder_time", reminder_time)
                .execute()
                .data
                or []
            )
            plan_ids = [item["plan_id"] for item in preferences]
            plans = (
                client.table("supplement_plans")
                .select("*")
                .in_("id", plan_ids)
                .eq("status", "active")
                .execute()
                .data
                or []
                if plan_ids
                else []
            )
            events = (
                client.table("supplement_intake_events")
                .select("plan_id")
                .in_("plan_id", plan_ids)
                .eq("scheduled_for", target_date)
                .execute()
                .data
                or []
                if plan_ids
                else []
            )
        except Exception as exc:
            if _missing_supplement_tables(exc):
                raise SupplementStorageUnavailableError from exc
            raise
    else:
        preferences = [
            item for item in _mem["reminder_preferences"]
            if item["enabled"] and item["reminder_time"] == reminder_time
        ]
        plan_ids = {item["plan_id"] for item in preferences}
        plans = [
            item for item in _mem["supplement_plans"]
            if item["id"] in plan_ids and item["status"] == "active"
        ]
        events = [
            item for item in _mem["supplement_intake_events"]
            if item["plan_id"] in plan_ids and item["scheduled_for"] == target_date
        ]
    plan_by_id = {plan["id"]: plan for plan in plans}
    recorded = {event["plan_id"] for event in events}
    return [
        {**deepcopy(preference), "plan": deepcopy(plan_by_id[preference["plan_id"]])}
        for preference in preferences
        if preference["plan_id"] in plan_by_id and preference["plan_id"] not in recorded
    ]


def registrar_medicion(
    *,
    whatsapp_identity: str | None = None,
    child_id: str,
    measured_at: str,
    weight_kg: float,
    height_cm: float,
    height_mode: str,
    muac_mm: float | None,
    bilateral_edema: bool,
    source: str = "caregiver",
    recorded_by_user_id: str | None = None,
) -> dict:
    source = str(source or "caregiver").strip().lower()
    if source not in {"caregiver", "health_worker"}:
        raise ValueError("La fuente debe ser caregiver o health_worker.")
    if source == "health_worker" and not recorded_by_user_id:
        raise ValueError("Una medición clínica debe identificar al personal que la registró.")
    if source == "health_worker":
        child = verificar_acceso_profesional(str(recorded_by_user_id), child_id)
    else:
        child = _get_child(child_id, whatsapp_identity)
    if not child:
        raise ValueError("No se encontró a la niña o niño para esta persona cuidadora.")
    assessment = assess_child(
        birth_date=child["birth_date"],
        measured_at=measured_at,
        sex=child["sex"],
        weight_kg=weight_kg,
        height_cm=height_cm,
        height_mode=height_mode,
        muac_mm=muac_mm,
        bilateral_edema=bilateral_edema,
    ).to_dict()
    measurement = {
        "id": _uuid(),
        "child_id": child_id,
        "measured_at": _date_text(measured_at),
        "weight_kg": float(weight_kg),
        "height_cm": float(height_cm),
        "height_mode": "length" if height_mode.lower() in {"length", "longitud", "acostado", "acostada"} else "height",
        "muac_mm": None if muac_mm is None else float(muac_mm),
        "bilateral_edema": bool(bilateral_edema),
        "source": source,
        "verification_status": "verified" if source == "health_worker" else "reported",
        "recorded_by": recorded_by_user_id if source == "health_worker" else None,
        "verified_at": _now() if source == "health_worker" else None,
        "validation_status": "valid",
        "validation_notes": None,
        "created_at": _now(),
    }
    result = {
        "id": _uuid(),
        "measurement_id": measurement["id"],
        **assessment,
        "created_at": _now(),
    }
    alert = None
    if result["semaforo"] in {"amarillo", "rojo"}:
        alert = {
            "id": _uuid(),
            "child_id": child_id,
            "measurement_id": measurement["id"],
            "health_center_id": child.get("health_center_id"),
            "nivel": result["semaforo"],
            "alert_type": (
                "clinical_alert" if source == "health_worker" else "verification_request"
            ),
            "estado": "abierta",
            "reason": "; ".join(result["reasons"]),
            "created_at": _now(),
            "updated_at": _now(),
            "resolved_at": None,
        }
    client = _get_client()
    if client:
        try:
            client.table("measurements").insert(measurement).execute()
        except Exception as exc:
            if not (
                _missing_measurement_review_columns(exc)
                or _missing_measurement_provenance_columns(exc)
            ):
                raise
            legacy_measurement = {
                key: value
                for key, value in measurement.items()
                if key not in {
                    "validation_status",
                    "validation_notes",
                    "verification_status",
                    "recorded_by",
                    "verified_at",
                }
            }
            client.table("measurements").insert(legacy_measurement).execute()
        client.table("assessment_results").insert(result).execute()
        if alert:
            try:
                client.table("alerts").insert(alert).execute()
            except Exception as exc:
                if not _missing_alert_type_column(exc):
                    raise
                client.table("alerts").insert(
                    {key: value for key, value in alert.items() if key != "alert_type"}
                ).execute()
    else:
        with _lock:
            _mem["measurements"].append(measurement)
            _mem["assessments"].append(result)
            if alert:
                _mem["alerts"].append(alert)
    return {
        "child": deepcopy(child),
        "measurement": measurement,
        "assessment": result,
        "alert": alert,
    }


def registrar_medicion_para_revision(
    *,
    whatsapp_identity: str | None = None,
    child_id: str,
    measured_at: str,
    weight_kg: float,
    height_cm: float,
    height_mode: str,
    muac_mm: float | None,
    bilateral_edema: bool,
    validation_notes: str,
    source: str = "caregiver",
    recorded_by_user_id: str | None = None,
) -> dict:
    """Conserva entradas confirmadas, pero no interpretables, sin crear alerta."""
    source = str(source or "caregiver").strip().lower()
    if source not in {"caregiver", "health_worker"}:
        raise ValueError("La fuente debe ser caregiver o health_worker.")
    if source == "health_worker" and not recorded_by_user_id:
        raise ValueError("Una medición clínica debe identificar al personal que la registró.")
    if source == "health_worker":
        child = verificar_acceso_profesional(str(recorded_by_user_id), child_id)
    else:
        child = _get_child(child_id, whatsapp_identity)
    if not child:
        raise ValueError("No se encontró a la niña o niño para esta persona cuidadora.")
    weight = float(weight_kg)
    height = float(height_cm)
    muac = None if muac_mm is None else float(muac_mm)
    if not 0.1 <= weight <= 100:
        raise ValueError("Revisa el peso: debe ser mayor que cero.")
    if not 10 <= height <= 250:
        raise ValueError("Revisa la talla: debe ser mayor que cero.")
    if muac is not None and not 10 <= muac <= 1000:
        raise ValueError("Revisa el MUAC: debe ser mayor que cero.")
    mode = str(height_mode).strip().lower()
    if mode in {"length", "longitud", "acostado", "acostada"}:
        mode = "length"
    elif mode in {"height", "talla", "parado", "parada", "de pie"}:
        mode = "height"
    else:
        raise ValueError("Indica si la medición fue acostado/a o parado/a.")

    measurement = {
        "id": _uuid(),
        "child_id": child_id,
        "measured_at": _date_text(measured_at),
        "weight_kg": weight,
        "height_cm": height,
        "height_mode": mode,
        "muac_mm": muac,
        "bilateral_edema": bool(bilateral_edema),
        "source": source,
        "verification_status": "verified" if source == "health_worker" else "reported",
        "recorded_by": recorded_by_user_id if source == "health_worker" else None,
        "verified_at": _now() if source == "health_worker" else None,
        "validation_status": "needs_review",
        "validation_notes": str(validation_notes or "Medición pendiente de confirmar.")[:500],
        "created_at": _now(),
    }
    client = _get_client()
    if client:
        try:
            client.table("measurements").insert(measurement).execute()
        except Exception as exc:
            if (
                _missing_measurement_review_columns(exc)
                or _missing_measurement_provenance_columns(exc)
                or _legacy_measurement_range_constraints(exc)
            ):
                raise MeasurementReviewStorageUnavailableError(
                    "Falta aplicar las migraciones de mediciones en Supabase."
                ) from exc
            raise
    else:
        with _lock:
            _mem["measurements"].append(measurement)
    return {
        "child": deepcopy(child),
        "measurement": measurement,
        "assessment": None,
        "alert": None,
        "needs_review": True,
    }


def consultar_estado(child_ref: str, whatsapp_identity: str | None = None) -> dict | None:
    children = listar_ninos(whatsapp_identity) if whatsapp_identity else []
    child = next(
        (c for c in children if c["id"] == child_ref or c["full_name"].lower() == child_ref.lower()),
        None,
    )
    if child is None and whatsapp_identity is None:
        child = _get_child(child_ref)
    if child is None:
        return None
    client = _get_client()
    if client:
        measurements = (
            client.table("measurements")
            .select("*")
            .eq("child_id", child["id"])
            .order("measured_at", desc=True)
            .execute()
            .data
            or []
        )
        ids = [m["id"] for m in measurements]
        assessments = []
        if ids:
            assessments = (
                client.table("assessment_results").select("*").in_("measurement_id", ids).execute().data
                or []
            )
        alerts = (
            client.table("alerts")
            .select("*")
            .eq("child_id", child["id"])
            .neq("estado", "resuelta")
            .execute()
            .data
            or []
        )
        alert_ids = [alert["id"] for alert in alerts]
        followup_events = []
        if alert_ids:
            try:
                followup_events = (
                    client.table("alert_followup_events")
                    .select("*")
                    .in_("alert_id", alert_ids)
                    .order("occurred_at", desc=True)
                    .execute()
                    .data
                    or []
                )
            except Exception as exc:
                if not _missing_followup_table(exc):
                    raise
                # Compatibilidad mientras el equipo aplica db/schema.sql. La
                # trayectoria y la alerta siguen siendo consultables.
                followup_events = []
    else:
        measurements = [m for m in _mem["measurements"] if m["child_id"] == child["id"]]
        measurements.sort(key=lambda row: row["measured_at"], reverse=True)
        assessments = [a for a in _mem["assessments"] if a["measurement_id"] in {m["id"] for m in measurements}]
        alerts = [a for a in _mem["alerts"] if a["child_id"] == child["id"] and a["estado"] != "resuelta"]
        alert_ids = {alert["id"] for alert in alerts}
        followup_events = [
            event for event in _mem["alert_followup_events"] if event["alert_id"] in alert_ids
        ]
        followup_events.sort(key=lambda row: row["occurred_at"], reverse=True)
    by_measurement = {a["measurement_id"]: a for a in assessments}
    trajectory = [{**m, "assessment": by_measurement.get(m["id"])} for m in measurements]
    verified_trajectory = [
        row for row in trajectory
        if row.get("verification_status") == "verified" or row.get("source") == "health_worker"
    ]
    reported_trajectory = [
        row for row in trajectory
        if row.get("verification_status", "reported") == "reported"
        and row.get("source", "caregiver") == "caregiver"
    ]
    return {
        "child": deepcopy(child),
        "latest": deepcopy(trajectory[0]) if trajectory else None,
        "latest_verified": deepcopy(verified_trajectory[0]) if verified_trajectory else None,
        "latest_reported": deepcopy(reported_trajectory[0]) if reported_trajectory else None,
        "trajectory": deepcopy(trajectory),
        "verified_trajectory": deepcopy(verified_trajectory),
        "reported_trajectory": deepcopy(reported_trajectory),
        "active_alerts": deepcopy(alerts),
        "followup_events": deepcopy(followup_events),
    }


_APPOINTMENT_TYPES = {"growth_control", "nutrition", "vaccination", "pediatrics", "other"}
_APPOINTMENT_STATUSES = {"scheduled", "confirmed", "completed", "missed", "cancelled"}
_APPOINTMENT_TRANSITIONS = {
    "scheduled": {"confirmed", "completed", "missed", "cancelled"},
    "confirmed": {"completed", "missed", "cancelled"},
    "completed": set(),
    "missed": set(),
    "cancelled": set(),
}


def registrar_cita(
    *,
    child_id: str,
    professional_user_id: str,
    scheduled_at: str,
    appointment_type: str = "growth_control",
    notes: str | None = None,
) -> dict:
    """Registra una cita clínica auditada, nunca una cita inferida por el bot."""
    child = verificar_acceso_profesional(professional_user_id, child_id)
    appointment_type = str(appointment_type).strip().lower()
    if appointment_type not in _APPOINTMENT_TYPES:
        raise ValueError("Tipo de cita no permitido.")
    try:
        parsed = datetime.fromisoformat(str(scheduled_at).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("La fecha y hora de la cita no son válidas.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    row = {
        "id": _uuid(),
        "child_id": child_id,
        "health_center_id": child.get("health_center_id"),
        "scheduled_at": parsed.astimezone(timezone.utc).isoformat(),
        "appointment_type": appointment_type,
        "status": "scheduled",
        "notes": str(notes or "").strip()[:500] or None,
        "created_by": str(professional_user_id),
        "created_at": _now(),
        "updated_at": _now(),
    }
    client = _get_client()
    if client:
        try:
            return client.table("appointments").insert(row).execute().data[0]
        except Exception as exc:
            if _missing_appointments_table(exc):
                raise RuntimeError("Falta aplicar la migración de citas en Supabase.") from exc
            raise
    with _lock:
        _mem["appointments"].append(row)
    return deepcopy(row)


def listar_citas(*, child_id: str, professional_user_id: str) -> list[dict]:
    verificar_acceso_profesional(professional_user_id, child_id)
    client = _get_client()
    if client:
        try:
            return (
                client.table("appointments")
                .select("*")
                .eq("child_id", child_id)
                .order("scheduled_at", desc=True)
                .execute()
                .data
                or []
            )
        except Exception as exc:
            if _missing_appointments_table(exc):
                raise RuntimeError("Falta aplicar la migración de citas en Supabase.") from exc
            raise
    rows = [row for row in _mem["appointments"] if row["child_id"] == child_id]
    rows.sort(key=lambda row: row["scheduled_at"], reverse=True)
    return deepcopy(rows)


def actualizar_estado_cita(
    *, appointment_id: str, professional_user_id: str, status: str
) -> dict:
    status = str(status).strip().lower()
    if status not in _APPOINTMENT_STATUSES:
        raise ValueError("Estado de cita no permitido.")
    client = _get_client()
    if client:
        response = client.table("appointments").select("*").eq("id", appointment_id).maybe_single().execute()
        appointment = _response_data(response)
    else:
        appointment = next(
            (row for row in _mem["appointments"] if row["id"] == appointment_id), None
        )
    if not appointment:
        raise ValueError("Cita no encontrada.")
    verificar_acceso_profesional(professional_user_id, appointment["child_id"])
    if status not in _APPOINTMENT_TRANSITIONS.get(appointment["status"], set()):
        raise ValueError(
            f"Transición de cita no permitida: {appointment['status']} → {status}."
        )
    updates = {"status": status, "updated_at": _now()}
    if client:
        return client.table("appointments").update(updates).eq("id", appointment_id).execute().data[0]
    appointment.update(updates)
    return deepcopy(appointment)


_CAREGIVER_FOLLOWUP_EVENTS = {
    "caregiver_acknowledged",
    "establishment_requested",
    "plans_to_attend",
    "attendance_reported",
    "needs_support",
    "recommendation_requested",
}

_FOLLOWUP_BARRIERS = {
    "appointment",
    "distance",
    "transport_cost",
    "schedule",
    "unknown_facility",
    "other",
}


def alertas_activas_familia(whatsapp_identity: str) -> list[dict]:
    """Devuelve alertas activas solo de niñas o niños de esta persona cuidadora."""
    children = listar_ninos(whatsapp_identity)
    child_by_id = {child["id"]: child for child in children}
    if not child_by_id:
        return []
    client = _get_client()
    if client:
        alerts = (
            client.table("alerts")
            .select("*")
            .in_("child_id", list(child_by_id))
            .neq("estado", "resuelta")
            .order("created_at", desc=True)
            .execute()
            .data
            or []
        )
    else:
        alerts = [
            alert
            for alert in _mem["alerts"]
            if alert["child_id"] in child_by_id and alert["estado"] != "resuelta"
        ]
        alerts.sort(key=lambda row: row["created_at"], reverse=True)
    # Una sola opción por niña o niño: primero una roja todavía activa y luego
    # la más reciente. Las demás alertas siguen disponibles para la app clínica.
    selected_by_child: dict[str, dict] = {}
    for alert in sorted(alerts, key=_alert_priority_key):
        selected_by_child.setdefault(alert["child_id"], alert)
    return [
        {**deepcopy(alert), "child": deepcopy(child_by_id[alert["child_id"]])}
        for alert in selected_by_child.values()
    ]


def registrar_evento_seguimiento_cuidador(
    *,
    whatsapp_identity: str,
    alert_id: str,
    event_type: str,
    planned_for: str | None = None,
    barrier_code: str | None = None,
) -> dict:
    """Registra un avance familiar sin permitir cerrar ni reclasificar la alerta."""
    if event_type not in _CAREGIVER_FOLLOWUP_EVENTS:
        raise ValueError("Evento de seguimiento no permitido para la persona cuidadora.")
    if barrier_code is not None and barrier_code not in _FOLLOWUP_BARRIERS:
        raise ValueError("Barrera de acceso inválida.")
    if event_type == "needs_support" and not barrier_code:
        raise ValueError("Selecciona la dificultad para solicitar apoyo.")
    planned_date = None
    if planned_for:
        try:
            planned_date = date.fromisoformat(str(planned_for)[:10])
        except ValueError as exc:
            raise ValueError("La fecha prevista no es válida.") from exc
        if planned_date < date.today():
            raise ValueError("La fecha prevista no puede estar en el pasado.")

    owned_alert = next(
        (alert for alert in alertas_activas_familia(whatsapp_identity) if alert["id"] == alert_id),
        None,
    )
    if not owned_alert:
        raise ValueError("No se encontró una alerta activa para esta familia.")

    event = {
        "id": _uuid(),
        "alert_id": alert_id,
        "actor_type": "caregiver",
        "event_type": event_type,
        "planned_for": planned_date.isoformat() if planned_date else None,
        "barrier_code": barrier_code,
        "notes": None,
        "occurred_at": _now(),
    }
    client = _get_client()
    if client:
        try:
            return client.table("alert_followup_events").insert(event).execute().data[0]
        except Exception as exc:
            if _missing_followup_table(exc):
                raise FollowupStorageUnavailableError(
                    "Falta aplicar db/schema.sql en Supabase."
                ) from exc
            raise
    with _lock:
        _mem["alert_followup_events"].append(event)
    return deepcopy(event)


def eventos_seguimiento_alerta(
    alert_id: str, whatsapp_identity: str | None = None
) -> list[dict]:
    """Consulta la bitácora; si hay identidad, verifica primero la pertenencia."""
    if whatsapp_identity and not any(
        alert["id"] == alert_id for alert in alertas_activas_familia(whatsapp_identity)
    ):
        return []
    client = _get_client()
    if client:
        try:
            return (
                client.table("alert_followup_events")
                .select("*")
                .eq("alert_id", alert_id)
                .order("occurred_at", desc=True)
                .execute()
                .data
                or []
            )
        except Exception as exc:
            if _missing_followup_table(exc):
                return []
            raise
    rows = [event for event in _mem["alert_followup_events"] if event["alert_id"] == alert_id]
    rows.sort(key=lambda row: row["occurred_at"], reverse=True)
    return deepcopy(rows)


def casos_priorizados() -> list[dict]:
    client = _get_client()
    if client:
        return (
            client.table("v_casos_priorizados")
            .select("*")
            .order("priority_order")
            .execute()
            .data
            or []
        )
    rows = []
    for child in _mem["children"]:
        state = consultar_estado(child["id"])
        active_alerts = (state or {}).get("active_alerts") or []
        if active_alerts:
            alert = sorted(active_alerts, key=_alert_priority_key)[0]
            measurement = next(
                (row for row in (state or {}).get("trajectory", []) if row["id"] == alert["measurement_id"]),
                None,
            )
            assessment = (measurement or {}).get("assessment") or {}
            rows.append({
                "child_id": child["id"],
                "child_name": child["full_name"],
                "district": child["district"],
                "semaforo": alert["nivel"],
                "measured_at": (measurement or {}).get("measured_at"),
                "reasons": assessment.get("reasons") or [alert["reason"]],
                "alert_id": alert["id"],
                "alert_status": alert["estado"],
                "alert_type": alert.get("alert_type", "verification_request"),
                "measurement_source": (measurement or {}).get("source", "caregiver"),
                "verification_status": (measurement or {}).get(
                    "verification_status", "reported"
                ),
                "priority_order": (
                    1 if alert["nivel"] == "rojo" and alert.get("alert_type") == "clinical_alert"
                    else 2 if alert["nivel"] == "rojo"
                    else 3 if alert.get("alert_type") == "clinical_alert"
                    else 4
                ),
            })
    return sorted(rows, key=lambda row: (row["priority_order"], row["measured_at"]))


_ALERT_TRANSITIONS = {
    "abierta": {"vista"},
    "vista": {"en_seguimiento"},
    "en_seguimiento": {"resuelta"},
    "resuelta": set(),
}


def actualizar_alerta(alert_id: str, nuevo_estado: str) -> dict:
    if nuevo_estado not in _ALERT_TRANSITIONS:
        raise ValueError("Estado de alerta inválido.")
    client = _get_client()
    if client:
        current = client.table("alerts").select("*").eq("id", alert_id).single().execute().data
    else:
        current = next((a for a in _mem["alerts"] if a["id"] == alert_id), None)
    if not current:
        raise ValueError("Alerta no encontrada.")
    if nuevo_estado not in _ALERT_TRANSITIONS[current["estado"]]:
        raise ValueError(f"Transición no permitida: {current['estado']} → {nuevo_estado}.")
    updates = {"estado": nuevo_estado, "updated_at": _now()}
    if nuevo_estado == "resuelta":
        updates["resolved_at"] = _now()
    if client:
        return client.table("alerts").update(updates).eq("id", alert_id).execute().data[0]
    current.update(updates)
    return deepcopy(current)


def registrar_evento_webhook(event_id: str, event_type: str, payload: dict | None = None) -> bool:
    """Devuelve False si el evento ya había sido recibido."""
    if not event_id:
        return True
    client = _get_client()
    if client:
        try:
            client.table("webhook_events").insert(
                {"event_id": event_id, "event_type": event_type, "payload": payload or {}}
            ).execute()
            return True
        except Exception as exc:
            # La PK hace la deduplicación atómica incluso con varias instancias.
            detail = str(exc).lower()
            if "23505" in detail or "duplicate key" in detail:
                return False
            raise
    with _lock:
        if event_id in _mem["webhook_events"]:
            return False
        _mem["webhook_events"].add(event_id)
        return True
