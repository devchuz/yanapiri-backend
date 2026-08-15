"""Configuración central de NutriAcompaña."""

import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

_ROOT_DIR = Path(__file__).resolve().parents[2]
_BASE_ENV_FILE = _ROOT_DIR / ".env"
_ENV_ALIASES = {
    "dev": "development",
    "development": "development",
    "local": "development",
    "prod": "production",
    "production": "production",
}


def _selected_environment() -> str:
    """Selecciona el entorno sin importar todavía sus credenciales."""
    from_process = os.getenv("APP_ENV", "").strip().lower()
    from_base_file = ""
    if _BASE_ENV_FILE.exists():
        from_base_file = str(dotenv_values(_BASE_ENV_FILE).get("APP_ENV") or "").strip().lower()
    requested = from_process or from_base_file or "development"
    try:
        return _ENV_ALIASES[requested]
    except KeyError as exc:
        allowed = ", ".join(sorted(_ENV_ALIASES))
        raise RuntimeError(f"APP_ENV={requested!r} no es válido. Usa uno de: {allowed}.") from exc


APP_ENV = _selected_environment()
ENV_FILE = _ROOT_DIR / f".env.{APP_ENV}"

# Las variables del sistema siempre ganan porque override=False.
load_dotenv(ENV_FILE, override=False)

# Compatibilidad local con el .env que ya utilizaba el proyecto. Producción
# nunca lo carga para evitar reutilizar credenciales de desarrollo por error.
if APP_ENV == "development":
    load_dotenv(_BASE_ENV_FILE, override=False)

# Supabase: la service role se usa únicamente en este backend.
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

# Kapso / WhatsApp Cloud API.
KAPSO_API_URL = os.getenv("KAPSO_API_URL", "https://api.kapso.ai").rstrip("/")
KAPSO_API_KEY = os.getenv("KAPSO_API_KEY", "")
KAPSO_PHONE_NUMBER_ID = os.getenv("KAPSO_PHONE_NUMBER_ID", "")
KAPSO_WEBHOOK_SECRET = os.getenv("KAPSO_WEBHOOK_SECRET", "")

# Groq se usa únicamente como capa conversacional/FAQ. Los cálculos clínicos,
# validaciones y persistencia permanecen en el flujo determinista.
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_API_URL = os.getenv(
    "GROQ_API_URL", "https://api.groq.com/openai/v1/chat/completions"
)
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")

# Public links included in outbound messages.
SEGUIMIENTO_URL = os.getenv("SEGUIMIENTO_URL", "").rstrip("/")
FORMULARIO_URL = os.getenv("FORMULARIO_URL", "")
TUTORIAL_LONGITUD_URL = os.getenv(
    "TUTORIAL_LONGITUD_URL", "https://www.youtube.com/watch?v=0C6CUT8XlRc"
)

# El clasificador e5 es opcional: nunca debe impedir que arranque el bot.
ENABLE_E5_CLASSIFIER = os.getenv("ENABLE_E5_CLASSIFIER", "false").lower() in {
    "1",
    "true",
    "yes",
}
EMBED_MODEL = os.getenv("EMBED_MODEL", "intfloat/multilingual-e5-small")

# Ajustes operativos.
MAX_CONCURRENT_MESSAGES = int(os.getenv("MAX_CONCURRENT_MESSAGES", "4"))
BOT_REPLY_DELAY_MIN_SECONDS = float(os.getenv("BOT_REPLY_DELAY_MIN_SECONDS", "2"))
BOT_REPLY_DELAY_MAX_SECONDS = float(os.getenv("BOT_REPLY_DELAY_MAX_SECONDS", "4"))
CONVERSATION_SESSION_MINUTES = int(os.getenv("CONVERSATION_SESSION_MINUTES", "120"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_MESSAGE_CONTENT = os.getenv("LOG_MESSAGE_CONTENT", "false").lower() in {
    "1",
    "true",
    "yes",
}


def usando_kapso() -> bool:
    return bool(KAPSO_API_KEY and KAPSO_PHONE_NUMBER_ID)


def usando_supabase() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)


def usando_groq() -> bool:
    return bool(GROQ_API_KEY)


def validar_entorno() -> None:
    """Impide arrancar producción con servicios críticos en fallback local."""
    if BOT_REPLY_DELAY_MIN_SECONDS < 0:
        raise RuntimeError("BOT_REPLY_DELAY_MIN_SECONDS no puede ser negativo.")
    if BOT_REPLY_DELAY_MAX_SECONDS < BOT_REPLY_DELAY_MIN_SECONDS:
        raise RuntimeError(
            "BOT_REPLY_DELAY_MAX_SECONDS debe ser mayor o igual al mínimo."
        )
    if CONVERSATION_SESSION_MINUTES <= 0:
        raise RuntimeError("CONVERSATION_SESSION_MINUTES debe ser mayor que cero.")
    if APP_ENV != "production":
        return
    required = {
        "SUPABASE_URL": SUPABASE_URL,
        "SUPABASE_SERVICE_KEY": SUPABASE_SERVICE_KEY,
        "KAPSO_API_KEY": KAPSO_API_KEY,
        "KAPSO_PHONE_NUMBER_ID": KAPSO_PHONE_NUMBER_ID,
        "KAPSO_WEBHOOK_SECRET": KAPSO_WEBHOOK_SECRET,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(
            "Producción no puede iniciar; faltan variables: " + ", ".join(missing)
        )
