"""
Configurações centrais do MVP
Edite este arquivo ou use variáveis de ambiente (.env)
"""

import os
from dotenv import load_dotenv

load_dotenv()

CONFIG = {
    # ── Servidor ──────────────────────────────
    "port": int(os.getenv("PORT", 5000)),

    # ── Reconhecimento Facial ─────────────────
    # Tolerância: menor = mais rigoroso (0.4–0.6 recomendado)
    "recognition_tolerance": float(os.getenv("RECOGNITION_TOLERANCE", 0.50)),
    "model_path": os.getenv("MODEL_PATH", "models/face_encodings.pkl"),
    "training_images_dir": os.getenv("TRAINING_DIR", "training_images"),

    # ── WhatsApp Business (Meta Cloud API) ────
    # Canal principal — sem Docker, direto na nuvem da Meta
    # Docs: https://developers.facebook.com/docs/whatsapp/cloud-api
    "use_meta_whatsapp": os.getenv("USE_META_WHATSAPP", "false").lower() == "true",
    "meta_whatsapp_token": os.getenv("META_WHATSAPP_TOKEN", "").strip(),
    "meta_phone_number_id": os.getenv("META_PHONE_NUMBER_ID", "").strip(),
    "meta_api_version": os.getenv("META_API_VERSION", "v19.0").strip(),

    # ── Mensageria WhatsApp (Evolution API) ───
    # Evolution API rodando localmente via Docker (legado)
    "evolution_api_url": os.getenv("EVOLUTION_API_URL", "http://localhost:8080"),
    "evolution_api_key": os.getenv("EVOLUTION_API_KEY", "B6D711FCDE4D4FD5936544120E713976"),
    "evolution_instance": os.getenv("EVOLUTION_INSTANCE", "face_mvp"),

    # ── Números de Telefone ───────────────────
    # Formato: código do país + DDD + número (sem + ou espaços)
    "sender_phone": os.getenv("SENDER_PHONE", "5514998338034"),
    "default_recipient": os.getenv("DEFAULT_RECIPIENT", "5514997283283"),

    # ── SMS via Twilio (alternativo ao WhatsApp) ──
    "use_twilio": os.getenv("USE_TWILIO", "false").lower() == "true",
    "twilio_account_sid": os.getenv("TWILIO_ACCOUNT_SID", ""),
    "twilio_auth_token": os.getenv("TWILIO_AUTH_TOKEN", ""),
    "twilio_from_number": os.getenv("TWILIO_FROM", "+15514998338034"),

    # ── Modo de teste (sem envio real) ────────
    "mock_messages": os.getenv("MOCK_MESSAGES", "false").lower() == "true",
}
