"""Student + embedding sync with the PostgreSQL master.

The edge holds a full local copy in SQLite (the source of truth for the camera
loop / Control iD lookup). This repo reconciles that copy against the cloud at
boot, and pushes new cadastros up. When cloud is disabled the methods become
no-ops.
"""

from __future__ import annotations

import logging
from typing import Iterable

from .postgres_client import cursor

logger = logging.getLogger(__name__)


class StudentRepository:
    def __init__(self, school_id: str, model_version: str) -> None:
        self.school_id = school_id
        self.model_version = model_version

    # ------------------------------------------------------------------
    # Reads (cloud → edge)
    # ------------------------------------------------------------------
    def fetch_active_with_embeddings(self) -> list[dict]:
        """Return active students for this school with their current embedding.

        Each item: ``{id, face_id, full_name, phone, email, external_id,
        enrollment_number, class_id, class_name, class_grade, class_shift,
        embedding}``. Returns ``[]`` when cloud is disabled or on error — caller
        falls back to whatever is already in the local SQLite cache.
        """
        if not self.school_id:
            return []
        try:
            with cursor() as cur:
                if cur is None:
                    return []
                cur.execute(
                    """
                    SELECT s.id, s.face_id, s.full_name, s.phone, s.email,
                           s.external_id, s.enrollment_number, s.class_id,
                           c.name  AS class_name,
                           c.grade AS class_grade,
                           c.shift AS class_shift,
                           e.embedding AS embedding
                    FROM students s
                    JOIN student_embeddings e
                      ON e.student_id = s.id
                     AND e.is_current = TRUE
                     AND e.model_version = %(model_version)s
                    LEFT JOIN classes c ON c.id = s.class_id
                    WHERE s.school_id = %(school_id)s
                      AND s.status = 'active'
                    """,
                    {"school_id": self.school_id, "model_version": self.model_version},
                )
                return cur.fetchall()
        except Exception:  # noqa: BLE001
            logger.exception("fetch_active_with_embeddings failed; using local cache.")
            return []

    def fetch_active(self) -> list[dict]:
        """Return active students WITHOUT embeddings (Control iD mode).

        In Control iD mode the device performs recognition, so the edge only
        needs the student metadata (name/phone/class) to record events and
        notify guardians.
        """
        if not self.school_id:
            return []
        try:
            with cursor() as cur:
                if cur is None:
                    return []
                cur.execute(
                    """
                    SELECT s.id, s.face_id, s.full_name, s.phone, s.email,
                           s.external_id, s.enrollment_number, s.class_id,
                           c.name  AS class_name,
                           c.grade AS class_grade,
                           c.shift AS class_shift
                    FROM students s
                    LEFT JOIN classes c ON c.id = s.class_id
                    WHERE s.school_id = %(school_id)s
                      AND s.status = 'active'
                    """,
                    {"school_id": self.school_id},
                )
                return cur.fetchall()
        except Exception:  # noqa: BLE001
            logger.exception("fetch_active failed; using local cache.")
            return []

    def find_by_external_id(self, external_id: str) -> dict | None:
        """Look up a single active student by ``external_id`` (Control iD user id)."""
        if not self.school_id or not external_id:
            return None
        try:
            with cursor() as cur:
                if cur is None:
                    return None
                cur.execute(
                    """
                    SELECT s.id, s.face_id, s.full_name, s.phone, s.email,
                           s.external_id, s.enrollment_number, s.class_id,
                           c.name  AS class_name,
                           c.grade AS class_grade,
                           c.shift AS class_shift
                    FROM students s
                    LEFT JOIN classes c ON c.id = s.class_id
                    WHERE s.school_id = %(school_id)s
                      AND s.external_id = %(external_id)s
                      AND s.status = 'active'
                    LIMIT 1
                    """,
                    {"school_id": self.school_id, "external_id": external_id},
                )
                return cur.fetchone()
        except Exception:  # noqa: BLE001
            logger.exception("find_by_external_id failed for %s.", external_id)
            return None

    # ------------------------------------------------------------------
    # Writes (edge → cloud)
    # ------------------------------------------------------------------
    def upsert_student(
        self,
        face_id: str,
        full_name: str,
        phone: str,
        email: str | None = None,
        external_id: str | None = None,
        enrollment_number: str | None = None,
        class_id: str | None = None,
    ) -> str | None:
        """Upsert a student by ``(school_id, face_id)``. Returns the UUID or None."""
        if not self.school_id:
            return None
        try:
            with cursor(commit=True) as cur:
                if cur is None:
                    return None
                cur.execute(
                    """
                    INSERT INTO students (
                        school_id, face_id, full_name, phone, email,
                        external_id, enrollment_number, class_id, status
                    ) VALUES (
                        %(school_id)s, %(face_id)s, %(full_name)s, %(phone)s, %(email)s,
                        %(external_id)s, %(enrollment_number)s, %(class_id)s, 'active'
                    )
                    ON CONFLICT (school_id, face_id) DO UPDATE SET
                        full_name = EXCLUDED.full_name,
                        phone = EXCLUDED.phone,
                        email = COALESCE(EXCLUDED.email, students.email),
                        external_id = COALESCE(EXCLUDED.external_id, students.external_id),
                        enrollment_number = COALESCE(EXCLUDED.enrollment_number, students.enrollment_number),
                        class_id = COALESCE(EXCLUDED.class_id, students.class_id),
                        status = 'active'
                    RETURNING id
                    """,
                    {
                        "school_id": self.school_id,
                        "face_id": face_id,
                        "full_name": full_name,
                        "phone": phone,
                        "email": email,
                        "external_id": external_id,
                        "enrollment_number": enrollment_number,
                        "class_id": class_id,
                    },
                )
                row = cur.fetchone()
                return str(row["id"]) if row else None
        except Exception:  # noqa: BLE001
            logger.exception("upsert_student failed for face_id=%s", face_id)
            return None

    def replace_student_restrictions(
        self,
        student_id: str,
        restriction_ids: list[str],
        notes_by_restriction: dict[str, str] | None = None,
    ) -> bool:
        """Replace the active set of dietary restrictions for one student.

        Pattern: deactivate everything, then upsert the new active set. Keeps
        the table append-only friendly without losing history.
        """
        notes_by_restriction = notes_by_restriction or {}
        try:
            with cursor(commit=True) as cur:
                if cur is None:
                    return False
                cur.execute(
                    "UPDATE student_dietary_restrictions SET active = FALSE "
                    "WHERE student_id = %s",
                    (student_id,),
                )
                for rid in restriction_ids:
                    cur.execute(
                        """
                        INSERT INTO student_dietary_restrictions
                            (student_id, restriction_id, notes, active)
                        VALUES (%(student_id)s, %(restriction_id)s, %(notes)s, TRUE)
                        ON CONFLICT (student_id, restriction_id) DO UPDATE SET
                            notes = EXCLUDED.notes,
                            active = TRUE
                        """,
                        {
                            "student_id": student_id,
                            "restriction_id": rid,
                            "notes": notes_by_restriction.get(rid),
                        },
                    )
                return True
        except Exception:  # noqa: BLE001
            logger.exception(
                "replace_student_restrictions failed for student=%s", student_id
            )
            return False

    def upsert_current_embedding(
        self,
        student_id: str,
        embedding: Iterable[float],
    ) -> bool:
        """Replace the current embedding for ``student_id`` under this model_version.

        Sets ``is_current=false`` on the previous record (history is kept).
        """
        if not self.school_id:
            return False
        emb_list = list(embedding)
        try:
            with cursor(commit=True) as cur:
                if cur is None:
                    return False
                cur.execute(
                    """
                    UPDATE student_embeddings SET is_current = FALSE
                    WHERE student_id = %(student_id)s
                      AND model_version = %(model_version)s
                      AND is_current = TRUE
                    """,
                    {"student_id": student_id, "model_version": self.model_version},
                )
                cur.execute(
                    """
                    INSERT INTO student_embeddings
                        (student_id, school_id, model_version, embedding, is_current)
                    VALUES (%(student_id)s, %(school_id)s, %(model_version)s,
                            %(embedding)s, TRUE)
                    """,
                    {
                        "student_id": student_id,
                        "school_id": self.school_id,
                        "model_version": self.model_version,
                        "embedding": emb_list,
                    },
                )
                return True
        except Exception:  # noqa: BLE001
            logger.exception("upsert_current_embedding failed for student=%s", student_id)
            return False
