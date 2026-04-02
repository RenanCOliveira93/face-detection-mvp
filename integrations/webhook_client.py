"""Cliente para publicação de eventos de presença em webhook externo."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import requests

from config import CONFIG

logger = logging.getLogger(__name__)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_payload(event: dict[str, Any], person: dict[str, Any], source: str) -> dict[str, Any]:
    return {
        "event_id": event.get("id"),
        "face_id": event.get("face_id") or person.get("id"),
        "direction": event.get("direction"),
        "event_at": event.get("event_at"),
        "match_score": event.get("match_score"),
        "full_name": person.get("full_name"),
        "phone": person.get("phone"),
        "source": source,
    }


def _build_signature(secret: str, body: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def publish_presence_event(
    event: dict[str, Any],
    person: dict[str, Any],
    source: str,
) -> dict[str, Any]:
    """Publica evento para backend externo com HMAC + retry.

    Retorna um dicionário para auditoria no banco:
    {
        "ok": bool,
        "status": int | None,
        "info": str,
        "sent_at": str,
    }
    """
    webhook_url = CONFIG.get("webhook_url", "")
    webhook_secret = CONFIG.get("webhook_secret", "")
    timeout = float(CONFIG.get("webhook_timeout_seconds", 1.5))
    retry_max = int(CONFIG.get("webhook_retry_max", 2))

    if not webhook_url:
        return {
            "ok": False,
            "status": None,
            "info": "WEBHOOK_URL não configurada",
            "sent_at": _iso_now(),
        }

    if not webhook_secret:
        return {
            "ok": False,
            "status": None,
            "info": "WEBHOOK_SECRET não configurado",
            "sent_at": _iso_now(),
        }

    payload = _build_payload(event, person, source)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    signature = _build_signature(webhook_secret, body)

    headers = {
        "Content-Type": "application/json",
        "X-Signature": signature,
    }

    attempts = max(1, retry_max + 1)
    last_error: str = "erro desconhecido"
    last_status: int | None = None

    for attempt in range(1, attempts + 1):
        try:
            response = requests.post(
                webhook_url,
                data=body.encode("utf-8"),
                headers=headers,
                timeout=timeout,
            )
            last_status = response.status_code
            if 200 <= response.status_code < 300:
                return {
                    "ok": True,
                    "status": response.status_code,
                    "info": f"Webhook entregue na tentativa {attempt}",
                    "sent_at": _iso_now(),
                }

            last_error = f"HTTP {response.status_code}: {response.text[:300]}"
            logger.warning(
                "webhook_delivery_failed",
                extra={
                    "event_id": event.get("id"),
                    "attempt": attempt,
                    "status_code": response.status_code,
                },
            )
        except requests.RequestException as exc:
            last_error = str(exc)
            logger.warning(
                "webhook_request_exception",
                extra={
                    "event_id": event.get("id"),
                    "attempt": attempt,
                    "error": str(exc),
                },
            )

        if attempt < attempts:
            time.sleep(min(0.2 * attempt, 1.0))

    return {
        "ok": False,
        "status": last_status,
        "info": f"Falha no webhook após {attempts} tentativas: {last_error}",
        "sent_at": _iso_now(),
    }
