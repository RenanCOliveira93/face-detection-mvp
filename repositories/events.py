"""Presence-event sync (used by the outbox worker) — PostgreSQL master."""

from __future__ import annotations

import logging
from typing import Any

from .postgres_client import cursor

logger = logging.getLogger(__name__)


class PresenceEventRepository:
    def __init__(self, school_id: str, device_id: str | None) -> None:
        self.school_id = school_id
        self.device_id = device_id

    def push(self, event: dict[str, Any]) -> bool:
        """Insert a presence event into the PostgreSQL master.

        Expected keys in ``event``: ``student_id`` (UUID), ``face_id`` (slug),
        ``direction`` (entrada/saida), ``event_at`` (ISO ts), ``match_score``,
        ``message_ok``, ``message_info``, ``message_sent_at``.
        """
        if not self.school_id:
            return False

        params: dict[str, Any] = {
            "school_id": self.school_id,
            "device_id": self.device_id,
            "student_id": event["student_id"],
            "face_id": event.get("face_id"),
            "direction": event["direction"],
            "event_at": event["event_at"],
            "match_score": event.get("match_score"),
            "message_ok": event.get("message_ok"),
            "message_info": event.get("message_info"),
            "message_sent_at": event.get("message_sent_at"),
        }

        try:
            with cursor(commit=True) as cur:
                if cur is None:
                    return False
                cur.execute(
                    """
                    INSERT INTO presence_events (
                        school_id, device_id, student_id, face_id, direction,
                        event_at, match_score, message_ok, message_info, message_sent_at
                    ) VALUES (
                        %(school_id)s, %(device_id)s, %(student_id)s, %(face_id)s,
                        %(direction)s, %(event_at)s, %(match_score)s, %(message_ok)s,
                        %(message_info)s, %(message_sent_at)s
                    )
                    """,
                    params,
                )
                return True
        except Exception:  # noqa: BLE001
            logger.exception("push presence_event failed; will retry via outbox.")
            return False
