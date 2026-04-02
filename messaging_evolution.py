"""Provider de envio via Evolution API."""

from __future__ import annotations

import json
import re
import os
import urllib.error
import urllib.request
from typing import Any


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return ""
    if not digits.startswith("55"):
        digits = "55" + digits
    return digits


def _build_response(success: bool, request_id: str, raw_response: Any) -> tuple[bool, dict[str, Any]]:
    return success, {
        "success": success,
        "provider": "evolution",
        "request_id": request_id,
        "raw_response": raw_response,
    }


def send_via_evolution(phone: str, message: str) -> tuple[bool, dict[str, Any]]:
    base_url = os.getenv("EVOLUTION_BASE_URL", "").strip().rstrip("/")
    api_key = os.getenv("EVOLUTION_API_KEY", "").strip()
    instance = os.getenv("EVOLUTION_INSTANCE", "").strip()

    if not base_url or not api_key or not instance:
        return _build_response(
            False,
            "",
            "Evolution API não configurada. Defina EVOLUTION_BASE_URL, EVOLUTION_API_KEY e EVOLUTION_INSTANCE.",
        )

    number = normalize_phone(phone)
    url = f"{base_url}/message/sendText/{instance}"
    payload = json.dumps({"number": number, "textMessage": {"text": message}}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "apikey": api_key,
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = json.loads(response.read().decode("utf-8") or "{}")
            request_id = str(raw.get("key") or raw.get("id") or "")
            return _build_response(True, request_id, raw)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        try:
            raw = json.loads(body)
        except json.JSONDecodeError:
            raw = body
        request_id = ""
        if isinstance(raw, dict):
            request_id = str(raw.get("key") or raw.get("id") or "")
        return _build_response(False, request_id, raw)
    except urllib.error.URLError as exc:
        return _build_response(False, "", f"Falha de conexão com Evolution: {exc.reason}")
