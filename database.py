"""SQLite para cadastro dos alunos e histórico de detecções/presença."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

DB_PATH = "database/faces.db"


class FaceDatabase:
    def __init__(self, db_path: str = DB_PATH, attendance_timezone: str = "UTC"):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self.attendance_timezone = attendance_timezone or "UTC"
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        migrations: list[Callable[[sqlite3.Connection], None]] = [
            self._migration_1_base_schema,
            self._migration_2_faces_columns,
            self._migration_3_detections_columns,
            self._migration_4_presence_events,
            self._migration_5_daily_attendance,
            self._migration_5_guardians_contacts,
            self._migration_5_message_dispatch_locks,
            self._migration_5_presence_webhook_audit,
        ]
        with self._connect() as conn:
            current_version = conn.execute("PRAGMA user_version").fetchone()[0]
            for version, migration in enumerate(migrations, start=1):
                if current_version < version:
                    migration(conn)
                    conn.execute(f"PRAGMA user_version = {version}")
            conn.commit()

    @staticmethod
    def _migration_1_base_schema(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS faces (
                id TEXT PRIMARY KEY,
                full_name TEXT NOT NULL,
                phone TEXT NOT NULL,
                email TEXT,
                notes TEXT,
                created_at TEXT NOT NULL,
                active INTEGER DEFAULT 1
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS detections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                face_id TEXT NOT NULL,
                detected_at TEXT NOT NULL,
                message_ok INTEGER DEFAULT 0,
                FOREIGN KEY (face_id) REFERENCES faces(id)
            )
            """
        )

    @staticmethod
    def _migration_2_faces_columns(conn: sqlite3.Connection) -> None:
        existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(faces)").fetchall()}
        for column, ddl in {
            "photo_path": "ALTER TABLE faces ADD COLUMN photo_path TEXT",
            "encoding_json": "ALTER TABLE faces ADD COLUMN encoding_json TEXT",
        }.items():
            if column not in existing_columns:
                conn.execute(ddl)

    @staticmethod
    def _migration_3_detections_columns(conn: sqlite3.Connection) -> None:
        detection_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(detections)").fetchall()
        }
        for column, ddl in {
            "similarity": "ALTER TABLE detections ADD COLUMN similarity REAL",
            "message_info": "ALTER TABLE detections ADD COLUMN message_info TEXT",
        }.items():
            if column not in detection_columns:
                conn.execute(ddl)

    @staticmethod
    def _migration_4_presence_events(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS presence_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                face_id TEXT NOT NULL,
                direction TEXT NOT NULL CHECK(direction IN ('entrada', 'saida')),
                event_at TEXT NOT NULL,
                match_score REAL,
                message_ok INTEGER,
                message_info TEXT,
                message_sent_at TEXT,
                FOREIGN KEY (face_id) REFERENCES faces(id)
            )
            """
        )

    @staticmethod
    def _migration_5_daily_attendance(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                face_id TEXT NOT NULL,
                attendance_date TEXT NOT NULL,
                first_entry_at TEXT,
                last_exit_at TEXT,
                status TEXT NOT NULL DEFAULT 'inconsistente',
                total_transitions INTEGER NOT NULL DEFAULT 0,
                UNIQUE(face_id, attendance_date),
                FOREIGN KEY (face_id) REFERENCES faces(id)
            )
            """
        )

    def _parse_iso_datetime(self, value: str) -> datetime:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def _attendance_date_from_event(self, event_at: str) -> str:
        utc_dt = self._parse_iso_datetime(event_at)
        local_dt = utc_dt.astimezone(ZoneInfo(self.attendance_timezone))
        return local_dt.date().isoformat()
    @staticmethod
    def _migration_5_guardians_contacts(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS guardians (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                email TEXT,
                notes TEXT,
                created_at TEXT NOT NULL,
                active INTEGER DEFAULT 1
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS guardian_phones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guardian_id INTEGER NOT NULL,
                phone_e164 TEXT NOT NULL,
                is_primary INTEGER DEFAULT 1,
                channel TEXT NOT NULL DEFAULT 'whatsapp' CHECK(channel IN ('whatsapp', 'sms')),
                active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL,
                FOREIGN KEY (guardian_id) REFERENCES guardians(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS student_guardians (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                face_id TEXT NOT NULL,
                guardian_id INTEGER NOT NULL,
                relationship_type TEXT NOT NULL DEFAULT 'tutor'
                    CHECK(relationship_type IN ('mãe', 'pai', 'tutor')),
                contact_priority INTEGER NOT NULL DEFAULT 1,
                valid_from TEXT,
                valid_to TEXT,
                active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL,
                FOREIGN KEY (face_id) REFERENCES faces(id),
                FOREIGN KEY (guardian_id) REFERENCES guardians(id)
            )
            """
        )

        now_iso = datetime.now(timezone.utc).isoformat()
        faces = conn.execute(
            """
            SELECT id, full_name, phone
            FROM faces
            WHERE phone IS NOT NULL AND TRIM(phone) != ''
            """
        ).fetchall()
        for face in faces:
            existing = conn.execute(
                """
                SELECT sg.id, sg.guardian_id
                FROM student_guardians sg
                JOIN guardian_phones gp ON gp.guardian_id = sg.guardian_id
                WHERE sg.face_id = ?
                  AND sg.active = 1
                  AND gp.active = 1
                  AND gp.phone_e164 = ?
                LIMIT 1
                """,
                (face["id"], face["phone"]),
            ).fetchone()
            if existing:
                continue

            guardian_name = f"Responsável de {face['full_name']}"
            cursor = conn.execute(
                """
                INSERT INTO guardians (full_name, created_at, active)
                VALUES (?, ?, 1)
                """,
                (guardian_name, now_iso),
            )
            guardian_id = int(cursor.lastrowid)
            conn.execute(
                """
                INSERT INTO guardian_phones (
                    guardian_id, phone_e164, is_primary, channel, active, created_at
                ) VALUES (?, ?, 1, 'whatsapp', 1, ?)
                """,
                (guardian_id, face["phone"], now_iso),
            )
            conn.execute(
                """
                INSERT INTO student_guardians (
                    face_id, guardian_id, relationship_type, contact_priority,
                    valid_from, valid_to, active, created_at
                ) VALUES (?, ?, 'tutor', 1, ?, NULL, 1, ?)
                """,
                (face["id"], guardian_id, now_iso, now_iso),
            )
    @staticmethod
    def _migration_5_message_dispatch_locks(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS message_dispatch_locks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                face_id TEXT NOT NULL,
                direction TEXT NOT NULL CHECK(direction IN ('entrada', 'saida')),
                business_date TEXT NOT NULL,
                last_sent_at TEXT NOT NULL,
                FOREIGN KEY (face_id) REFERENCES faces(id),
                UNIQUE(face_id, direction, business_date)
            )
            """
        )

    @staticmethod
    def _migration_5_presence_webhook_audit(conn: sqlite3.Connection) -> None:
        presence_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(presence_events)").fetchall()
        }
        for column, ddl in {
            "webhook_ok": "ALTER TABLE presence_events ADD COLUMN webhook_ok INTEGER",
            "webhook_status": "ALTER TABLE presence_events ADD COLUMN webhook_status INTEGER",
            "webhook_info": "ALTER TABLE presence_events ADD COLUMN webhook_info TEXT",
            "webhook_sent_at": "ALTER TABLE presence_events ADD COLUMN webhook_sent_at TEXT",
        }.items():
            if column not in presence_columns:
                conn.execute(ddl)

    def add_face(
        self,
        face_id: str,
        full_name: str,
        phone: str,
        email: str = "",
        notes: str = "",
        photo_path: str | None = None,
        encoding: list[float] | None = None,
    ) -> bool:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO faces (
                        id, full_name, phone, email, notes, photo_path,
                        encoding_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        face_id,
                        full_name,
                        phone,
                        email,
                        notes,
                        photo_path,
                        json.dumps(encoding) if encoding is not None else None,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def get_face(self, face_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM faces WHERE id = ? AND active = 1", (face_id,)
            ).fetchone()
        data = self._row_to_face(row)
        if data:
            recipient = self.get_preferred_notification_recipient(face_id, channel="whatsapp")
            data["notification_phone"] = recipient["phone"] if recipient else data.get("phone")
        return data

    def list_faces(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM faces WHERE active = 1 ORDER BY created_at DESC"
            ).fetchall()
        faces = [self._row_to_face(row) for row in rows]
        for face in faces:
            recipient = self.get_preferred_notification_recipient(face["id"], channel="whatsapp")
            face["notification_phone"] = recipient["phone"] if recipient else face.get("phone")
        return faces

    def update_face(self, face_id: str, **kwargs: Any) -> bool:
        allowed = {
            "full_name", "phone", "email", "notes", "photo_path", "active", "encoding"
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        if "encoding" in updates:
            updates["encoding_json"] = json.dumps(updates.pop("encoding"))
        fields = ", ".join(f"{key} = ?" for key in updates)
        values = list(updates.values()) + [face_id]
        with self._connect() as conn:
            conn.execute(f"UPDATE faces SET {fields} WHERE id = ?", values)
            conn.commit()
        return True

    def delete_face(self, face_id: str) -> bool:
        return self.update_face(face_id, active=0)

    def log_detection(
        self,
        face_id: str,
        similarity: float | None = None,
        message_ok: bool = False,
        message_info: str = "",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO detections (face_id, detected_at, similarity, message_ok, message_info)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    face_id,
                    datetime.now(timezone.utc).isoformat(),
                    similarity,
                    int(message_ok),
                    message_info,
                ),
            )
            conn.commit()

    def create_presence_event(
        self,
        face_id: str,
        direction: str,
        match_score: float | None,
        event_at: str | None = None,
    ) -> int:
        event_at_value = event_at or datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO presence_events (face_id, direction, event_at, match_score)
                VALUES (?, ?, ?, ?)
                """,
                (
                    face_id,
                    direction,
                    event_at_value,
                    match_score,
                ),
            )
            self._upsert_daily_attendance(
                conn=conn,
                face_id=face_id,
                direction=direction,
                event_at=event_at_value,
            )
            conn.commit()
            return int(cursor.lastrowid)

    def _upsert_daily_attendance(
        self,
        conn: sqlite3.Connection,
        face_id: str,
        direction: str,
        event_at: str,
    ) -> None:
        attendance_date = self._attendance_date_from_event(event_at)

        row = conn.execute(
            """
            SELECT *
            FROM daily_attendance
            WHERE face_id = ? AND attendance_date = ?
            """,
            (face_id, attendance_date),
        ).fetchone()

        if direction == "saida" and row is None:
            previous_open_row = conn.execute(
                """
                SELECT *
                FROM daily_attendance
                WHERE face_id = ?
                  AND attendance_date < ?
                  AND first_entry_at IS NOT NULL
                  AND last_exit_at IS NULL
                ORDER BY attendance_date DESC
                LIMIT 1
                """,
                (face_id, attendance_date),
            ).fetchone()
            if previous_open_row is not None:
                self._update_daily_attendance_row(
                    conn=conn,
                    row=previous_open_row,
                    direction=direction,
                    event_at=event_at,
                )
                return

        if row is None:
            conn.execute(
                """
                INSERT INTO daily_attendance (
                    face_id, attendance_date, first_entry_at, last_exit_at, status, total_transitions
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    face_id,
                    attendance_date,
                    event_at if direction == "entrada" else None,
                    event_at if direction == "saida" else None,
                    "presente" if direction == "entrada" else "inconsistente",
                    1,
                ),
            )
            return

        self._update_daily_attendance_row(conn=conn, row=row, direction=direction, event_at=event_at)

    def _update_daily_attendance_row(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
        direction: str,
        event_at: str,
    ) -> None:
        face_id = row["face_id"]
        attendance_date = row["attendance_date"]
        first_entry_at = row["first_entry_at"]
        last_exit_at = row["last_exit_at"]
        total_transitions = int(row["total_transitions"]) + 1

        status = row["status"]
        if direction == "entrada":
            if first_entry_at is None:
                first_entry_at = event_at
                status = "presente"
            elif last_exit_at is None:
                status = "inconsistente"
            else:
                if event_at >= last_exit_at:
                    last_exit_at = None
                    status = "presente"
                else:
                    status = "inconsistente"
        else:
            if first_entry_at is None:
                status = "inconsistente"
                last_exit_at = event_at
            elif last_exit_at is None:
                last_exit_at = event_at
                status = "ausente"
            else:
                status = "inconsistente"
                if event_at >= last_exit_at:
                    last_exit_at = event_at

        conn.execute(
            """
            UPDATE daily_attendance
            SET first_entry_at = ?, last_exit_at = ?, status = ?, total_transitions = ?
            WHERE face_id = ? AND attendance_date = ?
            """,
            (first_entry_at, last_exit_at, status, total_transitions, face_id, attendance_date),
        )

    def update_presence_event_message(self, event_id: int, message_ok: bool, message_info: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE presence_events
                SET message_ok = ?, message_info = ?, message_sent_at = ?
                WHERE id = ?
                """,
                (
                    int(message_ok),
                    message_info,
                    datetime.now(timezone.utc).isoformat(),
                    event_id,
                ),
            )
            conn.commit()

    def update_presence_event_webhook(
        self,
        event_id: int,
        webhook_ok: bool,
        webhook_status: int | None,
        webhook_info: str,
        webhook_sent_at: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE presence_events
                SET webhook_ok = ?, webhook_status = ?, webhook_info = ?, webhook_sent_at = ?
                WHERE id = ?
                """,
                (
                    int(webhook_ok),
                    webhook_status,
                    webhook_info,
                    webhook_sent_at or datetime.now(timezone.utc).isoformat(),
                    event_id,
                ),
            )
            conn.commit()

    def get_presence_events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT p.*, f.full_name, f.phone
                FROM presence_events p
                JOIN faces f ON p.face_id = f.id
                ORDER BY p.event_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        events = [dict(row) for row in rows]
        for event in events:
            recipient = self.get_preferred_notification_recipient(
                event["face_id"],
                channel="whatsapp",
            )
            event["phone"] = recipient["phone"] if recipient else event.get("phone")
        return events

    def get_detections(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT d.*, f.full_name FROM detections d
                JOIN faces f ON d.face_id = f.id
                ORDER BY d.detected_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_daily_attendance(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT da.*, f.full_name, f.phone
                FROM daily_attendance da
                JOIN faces f ON da.face_id = f.id
                ORDER BY da.attendance_date DESC, da.face_id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
    def get_preferred_notification_recipient(
        self,
        face_id: str,
        channel: str = "whatsapp",
    ) -> dict[str, Any] | None:
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    sg.guardian_id,
                    g.full_name AS guardian_name,
                    gp.phone_e164,
                    sg.relationship_type,
                    sg.contact_priority
                FROM student_guardians sg
                JOIN guardians g ON g.id = sg.guardian_id
                JOIN guardian_phones gp ON gp.guardian_id = g.id
                WHERE sg.face_id = ?
                  AND sg.active = 1
                  AND g.active = 1
                  AND gp.active = 1
                  AND gp.channel = ?
                  AND (sg.valid_from IS NULL OR sg.valid_from <= ?)
                  AND (sg.valid_to IS NULL OR sg.valid_to >= ?)
                ORDER BY
                    sg.contact_priority ASC,
                    CASE sg.relationship_type
                        WHEN 'mãe' THEN 1
                        WHEN 'pai' THEN 2
                        ELSE 3
                    END ASC,
                    gp.is_primary DESC,
                    sg.id ASC,
                    gp.id ASC
                LIMIT 1
                """,
                (face_id, channel, now_iso, now_iso),
            ).fetchone()
            if row:
                return {
                    "guardian_id": row["guardian_id"],
                    "guardian_name": row["guardian_name"],
                    "phone": row["phone_e164"],
                    "relationship_type": row["relationship_type"],
                    "contact_priority": row["contact_priority"],
                    "channel": channel,
                }

            fallback = conn.execute(
                "SELECT phone FROM faces WHERE id = ? AND active = 1",
                (face_id,),
            ).fetchone()
            if not fallback or not fallback["phone"]:
                return None
            return {
                "guardian_id": None,
                "guardian_name": None,
                "phone": fallback["phone"],
                "relationship_type": None,
                "contact_priority": None,
                "channel": channel,
            }
    def try_reserve_message_dispatch(
        self,
        face_id: str,
        direction: str,
        cooldown_seconds: int,
    ) -> tuple[bool, str]:
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        business_date = now.date().isoformat()

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """
                SELECT last_sent_at
                FROM message_dispatch_locks
                WHERE face_id = ? AND direction = ? AND business_date = ?
                """,
                (face_id, direction, business_date),
            ).fetchone()

            if existing:
                last_sent_at = datetime.fromisoformat(existing["last_sent_at"])
                elapsed = (now - last_sent_at).total_seconds()
                if elapsed < cooldown_seconds:
                    conn.commit()
                    return False, "cooldown"
                conn.commit()
                return False, "already_sent"

            try:
                conn.execute(
                    """
                    INSERT INTO message_dispatch_locks (face_id, direction, business_date, last_sent_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (face_id, direction, business_date, now_iso),
                )
                conn.commit()
                return True, "reserved"
            except sqlite3.IntegrityError:
                conn.commit()
                return False, "duplicate"

    @staticmethod
    def _row_to_face(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        data["has_encoding"] = bool(data.get("encoding_json"))
        if data.get("encoding_json"):
            data["encoding"] = json.loads(data["encoding_json"])
        else:
            data["encoding"] = None
        return data
