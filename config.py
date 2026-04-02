"""Configurações centrais do MVP."""

import importlib.util
import os

if importlib.util.find_spec("dotenv") is not None:
    import dotenv
    dotenv.load_dotenv()


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


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
    "webhook_url": os.getenv("WEBHOOK_URL", "").strip(),
    "webhook_secret": os.getenv("WEBHOOK_SECRET", "").strip(),
    "webhook_timeout_seconds": float(os.getenv("WEBHOOK_TIMEOUT_SECONDS", 1.5)),
    "webhook_retry_max": int(os.getenv("WEBHOOK_RETRY_MAX", 2)),
}
