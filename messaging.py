"""
Módulo de Mensageria
Suporta:
  1. Meta Cloud API   → WhatsApp Business oficial (sem Docker)
  2. Evolution API    → WhatsApp open-source (Docker local, legado)
  3. Twilio           → SMS (fallback com conta trial)
  4. Mock             → Simula envio para testes sem API
"""

import requests
import re
from config import CONFIG


# ─────────────────────────────────────────────────────────────
# Helper: normalizar número de telefone
# ─────────────────────────────────────────────────────────────
def normalize_phone(phone: str) -> str:
    """Remove caracteres não numéricos e garante código Brasil (+55)."""
    digits = re.sub(r"\D", "", phone)
    if not digits.startswith("55"):
        digits = "55" + digits
    return digits


# ─────────────────────────────────────────────────────────────
# 1. Meta Cloud API (WhatsApp Business oficial)
# ─────────────────────────────────────────────────────────────
def send_via_meta(phone: str, message: str) -> tuple[bool, str]:
    """
    Envia mensagem via WhatsApp Business Cloud API (Meta).
    Sem Docker, sem servidor local — direto na nuvem da Meta.

    Pré-requisitos:
      - App criado em https://developers.facebook.com/apps/
      - Produto WhatsApp adicionado ao App
      - META_WHATSAPP_TOKEN e META_PHONE_NUMBER_ID configurados no .env

    Docs: https://developers.facebook.com/docs/whatsapp/cloud-api/messages
    """
    token           = CONFIG["meta_whatsapp_token"]
    phone_number_id = CONFIG["meta_phone_number_id"]
    api_version     = CONFIG["meta_api_version"]

    if not token or not phone_number_id:
        return False, (
            "Meta Cloud API não configurada. "
            "Defina META_WHATSAPP_TOKEN e META_PHONE_NUMBER_ID no .env"
        )

    number = normalize_phone(phone)

    url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": number,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": message,
        },
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        data = resp.json()
        if resp.status_code in (200, 201):
            msg_id = data.get("messages", [{}])[0].get("id", "?")
            return True, {"message_id": msg_id, "status": "sent"}
        else:
            error = data.get("error", {})
            return False, f"HTTP {resp.status_code} — {error.get('message', resp.text)}"
    except requests.exceptions.ConnectionError:
        return False, "Sem conexão com a API da Meta. Verifique sua internet."
    except Exception as e:
        return False, str(e)


# ─────────────────────────────────────────────────────────────
# 2. Evolution API (WhatsApp Open Source — legado/Docker)
# ─────────────────────────────────────────────────────────────
def send_via_evolution(phone: str, message: str) -> tuple[bool, str]:
    """
    Envia mensagem via Evolution API rodando localmente.
    Docs: https://doc.evolution-api.com

    Pré-requisito: Docker com Evolution API em execução
    """
    base_url = CONFIG["evolution_api_url"].rstrip("/")
    instance  = CONFIG["evolution_instance"]
    api_key   = CONFIG["evolution_api_key"]

    number = normalize_phone(phone)

    url = f"{base_url}/message/sendText/{instance}"
    headers = {
        "Content-Type": "application/json",
        "apikey": api_key
    }
    payload = {
        "number": number,
        "textMessage": {
            "text": message
        },
        "options": {
            "delay": 1000,
            "presence": "composing"
        }
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        if resp.status_code in (200, 201):
            return True, resp.json()
        else:
            return False, f"HTTP {resp.status_code}: {resp.text}"
    except requests.exceptions.ConnectionError:
        return False, "Evolution API não está acessível. Verifique o Docker."
    except Exception as e:
        return False, str(e)


# ─────────────────────────────────────────────────────────────
# 3. Twilio SMS (fallback)
# ─────────────────────────────────────────────────────────────
def send_via_twilio_sms(phone: str, message: str) -> tuple[bool, str]:
    """
    Envia SMS via Twilio como fallback.
    Requer TWILIO_ACCOUNT_SID e TWILIO_AUTH_TOKEN no .env
    """
    try:
        from twilio.rest import Client
    except ImportError:
        return False, "Twilio não instalado. Execute: pip install twilio"

    sid   = CONFIG["twilio_account_sid"]
    token = CONFIG["twilio_auth_token"]
    from_ = CONFIG["twilio_from_number"]

    if not sid or not token:
        return False, "Credenciais Twilio não configuradas no .env"

    number = "+" + normalize_phone(phone)

    try:
        client = Client(sid, token)
        msg = client.messages.create(body=message, from_=from_, to=number)
        return True, msg.sid
    except Exception as e:
        return False, str(e)


# ─────────────────────────────────────────────────────────────
# 4. Mock (para testes sem API)
# ─────────────────────────────────────────────────────────────
def send_mock(phone: str, message: str) -> tuple[bool, str]:
    """Simula envio de mensagem — apenas loga no terminal."""
    number = normalize_phone(phone)
    print(f"\n{'='*50}")
    print(f"  📲  MOCK — Mensagem que seria enviada:")
    print(f"  Para   : +{number}")
    print(f"  Texto  : {message}")
    print(f"{'='*50}\n")
    return True, "mock_ok"


# ─────────────────────────────────────────────────────────────
# Dispatcher principal
# ─────────────────────────────────────────────────────────────
def send_whatsapp_message(phone: str, message: str) -> tuple[bool, str]:
    """
    Envia mensagem usando o canal configurado.
    Ordem de prioridade:
      MOCK_MESSAGES=true      → mock (sem envio real)
      USE_META_WHATSAPP=true  → Meta Cloud API (WhatsApp Business)
      USE_TWILIO=true         → Twilio SMS
      padrão                  → Evolution API (Docker local, legado)
    """
    if CONFIG["mock_messages"]:
        return send_mock(phone, message)

    if CONFIG["use_meta_whatsapp"]:
        ok, info = send_via_meta(phone, message)
        if ok:
            return ok, info
        print(f"[MSG] Meta Cloud API falhou: {info}")
        print("[MSG] Fallback para mock...")
        return send_mock(phone, message)

    if CONFIG["use_twilio"]:
        ok, info = send_via_twilio_sms(phone, message)
        if ok:
            return ok, info
        print(f"[MSG] Twilio falhou ({info}), tentando Evolution API...")

    # Tenta Evolution API (legado)
    ok, info = send_via_evolution(phone, message)
    if not ok:
        print(f"[MSG] Evolution API falhou: {info}")
        print("[MSG] Fallback para mock...")
        return send_mock(phone, message)

    return ok, info
