"""Configurações centrais do MVP."""

import importlib.util
import logging
import os
import secrets

if importlib.util.find_spec("dotenv") is not None:
    import dotenv
    dotenv.load_dotenv()

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


_session_secret = os.getenv("SESSION_SECRET", "").strip()
if not _session_secret:
    # Sem SESSION_SECRET fixo em .env, cada reinício do processo invalida as sessões
    # abertas (login expira). Aceitável para piloto/dev; defina SESSION_SECRET em
    # produção para sessões sobreviverem a reinícios/múltiplos workers.
    _session_secret = secrets.token_urlsafe(32)
    logger.warning(
        "SESSION_SECRET não configurado: usando segredo aleatório só desta execução "
        "(sessões de login serão invalidadas a cada reinício do servidor)."
    )


CONFIG = {
    "port": int(os.getenv("PORT", 5000)),
    "camera_index": int(os.getenv("CAMERA_INDEX", 0)),
    "recognition_tolerance": float(os.getenv("RECOGNITION_TOLERANCE", 0.45)),
    "face_images_dir": os.getenv("FACE_IMAGES_DIR", "storage/faces"),
    "frame_process_scale": float(os.getenv("FRAME_PROCESS_SCALE", 0.5)),
    "frame_process_every": int(os.getenv("FRAME_PROCESS_EVERY", 3)),
    "entry_cooldown_seconds": int(
        os.getenv("ENTRY_COOLDOWN_SECONDS", os.getenv("MESSAGE_COOLDOWN", 60))
    ),
    "exit_cooldown_seconds": int(
        os.getenv("EXIT_COOLDOWN_SECONDS", os.getenv("MESSAGE_COOLDOWN", 60))
    ),
    "mock_messages": _env_bool("MOCK_MESSAGES", "true"),
    "use_meta_whatsapp": _env_bool("USE_META_WHATSAPP"),
    "use_evolution_api": _env_bool("USE_EVOLUTION_API"),
    "meta_whatsapp_token": os.getenv("META_WHATSAPP_TOKEN", "").strip(),
    "meta_phone_number_id": os.getenv("META_PHONE_NUMBER_ID", "").strip(),
    "meta_api_version": os.getenv("META_API_VERSION", "v19.0").strip(),
    "default_recipient": os.getenv("DEFAULT_RECIPIENT", "").strip(),
    "attendance_timezone": os.getenv("ATTENDANCE_TIMEZONE", "UTC").strip() or "UTC",
    "webhook_url": os.getenv("WEBHOOK_URL", "").strip(),
    "webhook_secret": os.getenv("WEBHOOK_SECRET", "").strip(),
    "webhook_timeout_seconds": float(os.getenv("WEBHOOK_TIMEOUT_SECONDS", 1.5)),
    "webhook_retry_max": int(os.getenv("WEBHOOK_RETRY_MAX", 2)),
    "admin_bootstrap_token": os.getenv("ADMIN_BOOTSTRAP_TOKEN", "").strip(),
    "postgres_dsn": os.getenv("POSTGRES_DSN", "").strip(),
    # Sessão de login real (cookie httpOnly) para a interface web — distinta da
    # X-School-Key/dispositivo, que continua existindo para integrações e Control iD.
    "session_secret": _session_secret,
    "session_cookie_secure": _env_bool("SESSION_COOKIE_SECURE", "false"),
    "session_cookie_samesite": os.getenv("SESSION_COOKIE_SAMESITE", "Lax").strip(),
    "cors_origins": [
        origin.strip() for origin in os.getenv("CORS_ORIGINS", "http://localhost:8080").split(",")
        if origin.strip()
    ],
}
