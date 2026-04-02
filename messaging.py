"""Envio de mensagem para responsáveis via providers de WhatsApp ou mock."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

from config import CONFIG
from messaging_evolution import send_via_evolution


def _build_response(provider: str, success: bool, request_id: str = "", raw_response: Any = None) -> tuple[bool, dict[str, Any]]:
    return success, {
        "success": success,
        "provider": provider,
        "request_id": request_id,
        "raw_response": raw_response,
    }


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return ""
    if not digits.startswith("55"):
        digits = "55" + digits
    return digits


def send_mock(phone: str, message: str) -> tuple[bool, dict[str, Any]]:
    number = normalize_phone(phone)
    print(f"[MOCK] +{number}: {message}")
    return _build_response("mock", True, raw_response={"to": number, "message": message})


def send_via_meta(phone: str, message: str) -> tuple[bool, dict[str, Any]]:
    token = CONFIG["meta_whatsapp_token"]
    phone_number_id = CONFIG["meta_phone_number_id"]
    api_version = CONFIG["meta_api_version"]
    if not token or not phone_number_id:
        return _build_response(
            "meta",
            False,
            raw_response="Meta Cloud API não configurada. Defina META_WHATSAPP_TOKEN e META_PHONE_NUMBER_ID.",
        )

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
            request_id = ""
            if data.get("messages"):
                request_id = str(data["messages"][0].get("id") or "")
            return _build_response("meta", True, request_id=request_id, raw_response=data)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = body
        request_id = ""
        if isinstance(payload, dict):
            request_id = str(payload.get("error", {}).get("fbtrace_id") or "")
        return _build_response("meta", False, request_id=request_id, raw_response=payload)
    except urllib.error.URLError as exc:
        return _build_response("meta", False, raw_response=f"Falha de conexão com a Meta: {exc.reason}")


def send_whatsapp_message(phone: str, message: str) -> tuple[bool, dict[str, Any]]:
    if CONFIG["mock_messages"]:
        return send_mock(phone, message)
    if CONFIG["use_evolution_api"]:
        return send_via_evolution(phone, message)
    if CONFIG["use_meta_whatsapp"]:
        return send_via_meta(phone, message)
    return _build_response(
        "none",
        False,
        raw_response="Nenhum canal de WhatsApp habilitado. Use MOCK_MESSAGES=true, USE_EVOLUTION_API=true ou configure USE_META_WHATSAPP=true.",
    )
