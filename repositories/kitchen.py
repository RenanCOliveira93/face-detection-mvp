"""Kitchen recipients + dispatch settings — PostgreSQL master."""

from __future__ import annotations

import logging
from typing import Any

from .postgres_client import cursor

logger = logging.getLogger(__name__)


class KitchenRepository:
    def __init__(self, school_id: str) -> None:
        self.school_id = school_id

    def list_active_recipients(self) -> list[dict[str, Any]]:
        if not self.school_id:
            return []
        try:
            with cursor() as cur:
                if cur is None:
                    return []
                cur.execute(
                    "SELECT * FROM kitchen_recipients "
                    "WHERE school_id = %s AND active = TRUE",
                    (self.school_id,),
                )
                return cur.fetchall()
        except Exception:  # noqa: BLE001
            logger.exception("list_active_recipients failed.")
            return []

    def create_recipient(
        self,
        name: str,
        phone_e164: str,
        channel: str = "whatsapp",
        email: str | None = None,
    ) -> dict[str, Any] | None:
        if not self.school_id:
            return None
        try:
            with cursor(commit=True) as cur:
                if cur is None:
                    return None
                cur.execute(
                    """
                    INSERT INTO kitchen_recipients (school_id, name, phone_e164, channel, email)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (self.school_id, name, phone_e164, channel, email),
                )
                return cur.fetchone()
        except Exception:  # noqa: BLE001
            logger.exception("create_recipient failed.")
            return None

    def get_settings(self) -> dict[str, Any] | None:
        """Returns the kitchen-related fields from school_settings."""
        if not self.school_id:
            return None
        try:
            with cursor() as cur:
                if cur is None:
                    return None
                cur.execute(
                    """
                    SELECT school_id, kitchen_enabled, kitchen_dispatch_time,
                           kitchen_dispatch_shifts, kitchen_message_template,
                           whatsapp_provider
                    FROM school_settings
                    WHERE school_id = %s
                    """,
                    (self.school_id,),
                )
                return cur.fetchone()
        except Exception:  # noqa: BLE001
            logger.exception("get_settings failed.")
            return None

    def get_school(self) -> dict[str, Any] | None:
        """Used to template the school name in the kitchen message."""
        if not self.school_id:
            return None
        try:
            with cursor() as cur:
                if cur is None:
                    return None
                cur.execute(
                    "SELECT id, name, slug, timezone FROM schools WHERE id = %s",
                    (self.school_id,),
                )
                return cur.fetchone()
        except Exception:  # noqa: BLE001
            logger.exception("get_school failed.")
            return None
