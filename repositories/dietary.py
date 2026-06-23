"""Dietary restrictions repository — PostgreSQL master."""

from __future__ import annotations

import logging
from typing import Any

from .postgres_client import cursor

logger = logging.getLogger(__name__)


class DietaryRestrictionRepository:
    def __init__(self, school_id: str) -> None:
        self.school_id = school_id

    def list_active(self) -> list[dict[str, Any]]:
        if not self.school_id:
            return []
        try:
            with cursor() as cur:
                if cur is None:
                    return []
                cur.execute(
                    "SELECT * FROM dietary_restrictions "
                    "WHERE school_id = %s AND active = TRUE ORDER BY name",
                    (self.school_id,),
                )
                return cur.fetchall()
        except Exception:  # noqa: BLE001
            logger.exception("list dietary_restrictions failed.")
            return []

    def create(
        self,
        name: str,
        severity: str = "atencao",
        description: str | None = None,
    ) -> dict[str, Any] | None:
        if not self.school_id:
            return None
        try:
            with cursor(commit=True) as cur:
                if cur is None:
                    return None
                cur.execute(
                    """
                    INSERT INTO dietary_restrictions (school_id, name, severity, description)
                    VALUES (%s, %s, %s, %s)
                    RETURNING *
                    """,
                    (self.school_id, name, severity, description),
                )
                return cur.fetchone()
        except Exception:  # noqa: BLE001
            logger.exception("create dietary_restriction failed.")
            return None

    def find_or_create_by_name(
        self,
        name: str,
        severity: str = "atencao",
    ) -> dict[str, Any] | None:
        """Useful for CLI bulk import: resolve free-form names to ids."""
        if not self.school_id:
            return None
        try:
            with cursor() as cur:
                if cur is None:
                    return None
                cur.execute(
                    "SELECT * FROM dietary_restrictions "
                    "WHERE school_id = %s AND name = %s LIMIT 1",
                    (self.school_id, name),
                )
                existing = cur.fetchone()
                if existing:
                    return existing
        except Exception:  # noqa: BLE001
            logger.exception("find_or_create_by_name lookup failed for %s.", name)

        return self.create(name=name, severity=severity)

    def students_present_today_with_restrictions(self) -> list[dict[str, Any]]:
        """Pull the cloud view used by the kitchen dispatcher."""
        if not self.school_id:
            return []
        try:
            with cursor() as cur:
                if cur is None:
                    return []
                cur.execute(
                    "SELECT * FROM students_present_today_with_restrictions "
                    "WHERE school_id = %s",
                    (self.school_id,),
                )
                return cur.fetchall()
        except Exception:  # noqa: BLE001
            logger.exception("students_present_today_with_restrictions failed.")
            return []
