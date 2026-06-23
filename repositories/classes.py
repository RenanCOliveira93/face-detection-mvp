"""Class (turma) repository — PostgreSQL master.

Classes are administrative data; the hot recognition path doesn't need them
cached locally beyond ``faces.class_id`` (snapshotted at reconciliation).
"""

from __future__ import annotations

import logging
from typing import Any

from .postgres_client import cursor

logger = logging.getLogger(__name__)


class ClassRepository:
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
                    "SELECT * FROM classes WHERE school_id = %s AND active = TRUE "
                    "ORDER BY grade, name",
                    (self.school_id,),
                )
                return cur.fetchall()
        except Exception:  # noqa: BLE001
            logger.exception("list_active classes failed.")
            return []

    def get(self, class_id: str) -> dict[str, Any] | None:
        if not self.school_id:
            return None
        try:
            with cursor() as cur:
                if cur is None:
                    return None
                cur.execute(
                    "SELECT * FROM classes WHERE id = %s AND school_id = %s",
                    (class_id, self.school_id),
                )
                return cur.fetchone()
        except Exception:  # noqa: BLE001
            return None

    def create(
        self,
        name: str,
        grade: str,
        year: int,
        shift: str = "manha",
    ) -> dict[str, Any] | None:
        if not self.school_id:
            return None
        try:
            with cursor(commit=True) as cur:
                if cur is None:
                    return None
                cur.execute(
                    """
                    INSERT INTO classes (school_id, name, grade, shift, year)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (self.school_id, name, grade, shift, year),
                )
                return cur.fetchone()
        except Exception:  # noqa: BLE001
            logger.exception("create class failed.")
            return None

    def presence_today(self) -> list[dict[str, Any]]:
        """Aggregate live presence per class from the cloud view."""
        if not self.school_id:
            return []
        try:
            with cursor() as cur:
                if cur is None:
                    return []
                cur.execute(
                    "SELECT * FROM class_presence_today WHERE school_id = %s",
                    (self.school_id,),
                )
                return cur.fetchall()
        except Exception:  # noqa: BLE001
            logger.exception("presence_today failed.")
            return []
