"""Envio de mensagem para responsáveis via WhatsApp Cloud API ou mock."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

from config import CONFIG


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return ""
    if not digits.startswith("55"):
        digits = "55" + digits
    return digits


def send_mock(phone: str, message: str) -> tuple[bool, Any]:
    number = normalize_phone(phone)
    print(f"[MOCK] +{number}: {message}")
    return True, {"provider": "mock", "to": number}


def send_via_meta(phone: str, message: str) -> tuple[bool, Any]:
    token = CONFIG["meta_whatsapp_token"]
    phone_number_id = CONFIG["meta_phone_number_id"]
    api_version = CONFIG["meta_api_version"]
    if not token or not phone_number_id:
        return False, "Meta Cloud API não configurada. Defina META_WHATSAPP_TOKEN e META_PHONE_NUMBER_ID."

    number = normalize_phone(phone)
    url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"
    payload = json.dumps({
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": number,
        "type": "text",
        "text": {"preview_url": False, "body": message},
    }).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8") or "{}")
            return True, data
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = body
        return False, payload
    except urllib.error.URLError as exc:
        return False, f"Falha de conexão com a Meta: {exc.reason}"


def send_whatsapp_message(phone: str, message: str) -> tuple[bool, Any]:
    if CONFIG["mock_messages"]:
        return send_mock(phone, message)
    if CONFIG["use_meta_whatsapp"]:
        return send_via_meta(phone, message)
    return False, "Nenhum canal de WhatsApp habilitado. Use MOCK_MESSAGES=true ou configure USE_META_WHATSAPP=true."
