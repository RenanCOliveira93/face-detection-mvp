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
    # Minimum cosine similarity to accept a match (ArcFace 512-d, L2-normalized).
    # Higher = stricter. Typical: 0.45 permissive, 0.55 recommended, 0.65 strict.
    "recognition_tolerance": float(os.getenv("RECOGNITION_TOLERANCE", 0.55)),
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
    # ── Multi-school + PostgreSQL (cloud master) ─────────────
    # Each edge process is bound to a single school and identifies itself as
    # a single device. Both are UUIDs created by ./scripts/provision_school.sh.
    "school_id": os.getenv("SCHOOL_ID", "").strip(),
    "device_id": os.getenv("DEVICE_ID", "").strip(),
    "device_name": os.getenv("DEVICE_NAME", "edge-device").strip(),
    # Direct connection to the cloud master PostgreSQL (replaces Supabase REST).
    "postgres_host": os.getenv("POSTGRES_HOST", "").strip(),
    "postgres_port": int(os.getenv("POSTGRES_PORT", 5432)),
    "postgres_db": os.getenv("POSTGRES_DB", "").strip(),
    "postgres_user": os.getenv("POSTGRES_USER", "").strip(),
    # Password is intentionally not stripped — it may contain leading/trailing
    # whitespace as part of the secret.
    "postgres_password": os.getenv("POSTGRES_PASSWORD", ""),
    "postgres_sslmode": os.getenv("POSTGRES_SSLMODE", "").strip(),
    "postgres_connect_timeout": int(os.getenv("POSTGRES_CONNECT_TIMEOUT", 5)),
    # Local-only fallback: when true, the edge runs without any cloud sync
    # (no reconciliation, no event outbox, no heartbeat). Useful for offline dev.
    "postgres_disabled": _env_bool("POSTGRES_DISABLED", "false"),
    "outbox_poll_seconds": float(os.getenv("OUTBOX_POLL_SECONDS", 5.0)),
    "heartbeat_seconds": float(os.getenv("HEARTBEAT_SECONDS", 30.0)),
    "reconcile_on_boot": _env_bool("RECONCILE_ON_BOOT", "true"),
    # ── Control iD facial collector ──────────────────────────
    # When enabled, the device itself performs face detection/recognition and
    # POSTs to /new_user_identified.fcgi. The edge then skips the local webcam
    # loop and the in-memory InsightFace model entirely (saves CPU/RAM).
    "use_control_id": _env_bool("USE_CONTROL_ID", "false"),
    "control_id_host": os.getenv("CONTROL_ID_HOST", "").strip(),
    "control_id_user": os.getenv("CONTROL_ID_USER", "").strip(),
    "control_id_password": os.getenv("CONTROL_ID_PASSWORD", ""),
    "control_id_timeout_seconds": float(os.getenv("CONTROL_ID_TIMEOUT_SECONDS", 10.0)),
    # Model tag reported to the cloud when running in Control iD mode (no local
    # InsightFace model is loaded to derive a real one).
    "model_version": os.getenv("MODEL_VERSION", "").strip() or "control_id",
}
