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
            self._migration_6_outbox,
            self._migration_7_faces_supabase_id,
            self._migration_8_faces_class_and_enrollment,
            self._migration_9_kitchen_dispatches,
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

    @staticmethod
    def _migration_6_outbox(conn: sqlite3.Connection) -> None:
        """Outbox of pending sync operations to push to Supabase."""
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending', 'sent', 'failed')),
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                next_attempt_at TEXT,
                created_at TEXT NOT NULL,
                sent_at TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS outbox_pending_idx "
            "ON outbox(status, next_attempt_at) WHERE status = 'pending'"
        )

    @staticmethod
    def _migration_7_faces_supabase_id(conn: sqlite3.Connection) -> None:
        """Cache the Supabase UUID of each face so events can reference it."""
        existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(faces)").fetchall()}
        if "supabase_id" not in existing_columns:
            conn.execute("ALTER TABLE faces ADD COLUMN supabase_id TEXT")

    @staticmethod
    def _migration_9_kitchen_dispatches(conn: sqlite3.Connection) -> None:
        """Log of kitchen-message dispatches with a (date, shift) idempotency key."""
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS kitchen_dispatches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                business_date TEXT NOT NULL,
                shift TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                recipients_count INTEGER NOT NULL DEFAULT 0,
                present_count INTEGER NOT NULL DEFAULT 0,
                restrictions_count INTEGER NOT NULL DEFAULT 0,
                message_body TEXT,
                triggered_by TEXT NOT NULL DEFAULT 'schedule'
                    CHECK(triggered_by IN ('schedule', 'manual')),
                UNIQUE(business_date, shift)
            )
            """
        )

    @staticmethod
    def _migration_8_faces_class_and_enrollment(conn: sqlite3.Connection) -> None:
        """Mirror Supabase: cache class_id and enrollment_number locally so the
        edge can tag presence events and tally class attendance offline."""
        existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(faces)").fetchall()}
        for column, ddl in {
            "class_id": "ALTER TABLE faces ADD COLUMN class_id TEXT",
            "enrollment_number": "ALTER TABLE faces ADD COLUMN enrollment_number TEXT",
            "class_name": "ALTER TABLE faces ADD COLUMN class_name TEXT",
            "class_grade": "ALTER TABLE faces ADD COLUMN class_grade TEXT",
            "class_shift": "ALTER TABLE faces ADD COLUMN class_shift TEXT",
        }.items():
            if column not in existing_columns:
                conn.execute(ddl)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS faces_class_id_idx ON faces(class_id)"
        )

    def add_face(
        self,
        face_id: str,
        full_name: str,
        phone: str,
        email: str = "",
        notes: str = "",
        photo_path: str | None = None,
        encoding: list[float] | None = None,
        class_id: str | None = None,
        enrollment_number: str | None = None,
    ) -> bool:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO faces (
                        id, full_name, phone, email, notes, photo_path,
                        encoding_json, created_at, class_id, enrollment_number
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        class_id,
                        enrollment_number,
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
            "full_name", "phone", "email", "notes", "photo_path", "active", "encoding",
            "class_id", "enrollment_number", "class_name", "class_grade", "class_shift",
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
                conn.execute(
                    """
                    UPDATE message_dispatch_locks
                    SET last_sent_at = ?
                    WHERE face_id = ? AND direction = ? AND business_date = ?
                    """,
                    (now_iso, face_id, direction, business_date),
                )
                conn.commit()
                return True, "reserved"

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

    # ------------------------------------------------------------------
    # Outbox: queued sync operations to push to Supabase
    # ------------------------------------------------------------------
    def enqueue_outbox(self, kind: str, payload: dict) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO outbox (kind, payload_json, status, created_at, next_attempt_at)
                VALUES (?, ?, 'pending', ?, ?)
                """,
                (
                    kind,
                    json.dumps(payload),
                    datetime.now(timezone.utc).isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def fetch_pending_outbox(self, limit: int = 50) -> list[dict[str, Any]]:
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, kind, payload_json, attempts, next_attempt_at
                FROM outbox
                WHERE status = 'pending'
                  AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                ORDER BY id ASC
                LIMIT ?
                """,
                (now_iso, limit),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "kind": row["kind"],
                "payload": json.loads(row["payload_json"]),
                "attempts": int(row["attempts"]),
            }
            for row in rows
        ]

    def mark_outbox_sent(self, outbox_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE outbox
                SET status = 'sent', sent_at = ?, last_error = NULL
                WHERE id = ?
                """,
                (datetime.now(timezone.utc).isoformat(), outbox_id),
            )
            conn.commit()

    def mark_outbox_failed(
        self,
        outbox_id: int,
        error: str,
        retry_in_seconds: float | None = None,
    ) -> None:
        from datetime import timedelta

        next_attempt = None
        if retry_in_seconds is not None and retry_in_seconds > 0:
            next_attempt = (
                datetime.now(timezone.utc) + timedelta(seconds=retry_in_seconds)
            ).isoformat()

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE outbox
                SET attempts = attempts + 1,
                    last_error = ?,
                    next_attempt_at = ?
                WHERE id = ?
                """,
                (error[:1000], next_attempt, outbox_id),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Kitchen dispatches (idempotency by (date, shift))
    # ------------------------------------------------------------------
    def try_record_kitchen_dispatch(
        self,
        business_date: str,
        shift: str,
        triggered_by: str,
    ) -> bool:
        """Insert a dispatch row; returns False if one already exists for the
        same business_date + shift (so we don't double-send)."""
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO kitchen_dispatches (
                        business_date, shift, sent_at, triggered_by
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        business_date,
                        shift,
                        datetime.now(timezone.utc).isoformat(),
                        triggered_by,
                    ),
                )
                conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def finalize_kitchen_dispatch(
        self,
        business_date: str,
        shift: str,
        recipients_count: int,
        present_count: int,
        restrictions_count: int,
        message_body: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE kitchen_dispatches
                SET recipients_count = ?, present_count = ?,
                    restrictions_count = ?, message_body = ?
                WHERE business_date = ? AND shift = ?
                """,
                (
                    recipients_count,
                    present_count,
                    restrictions_count,
                    message_body,
                    business_date,
                    shift,
                ),
            )
            conn.commit()

    def list_kitchen_dispatches(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM kitchen_dispatches
                ORDER BY business_date DESC, shift ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Cached Supabase id for a face (used to reference student in events)
    # ------------------------------------------------------------------
    def set_face_supabase_id(self, face_id: str, supabase_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE faces SET supabase_id = ? WHERE id = ?",
                (supabase_id, face_id),
            )
            conn.commit()

    def get_face_supabase_id(self, face_id: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT supabase_id FROM faces WHERE id = ?",
                (face_id,),
            ).fetchone()
        return row["supabase_id"] if row and row["supabase_id"] else None

    def upsert_face_from_remote(
        self,
        face_id: str,
        supabase_id: str,
        full_name: str,
        phone: str | None,
        email: str | None,
        encoding: list[float] | None,
        class_id: str | None = None,
        class_name: str | None = None,
        class_grade: str | None = None,
        class_shift: str | None = None,
        enrollment_number: str | None = None,
    ) -> None:
        """Insert/update a face row from a remote reconciliation pull."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM faces WHERE id = ?", (face_id,)
            ).fetchone()
            payload_encoding = json.dumps(encoding) if encoding else None
            if existing:
                conn.execute(
                    """
                    UPDATE faces
                    SET full_name = ?, phone = ?, email = ?,
                        encoding_json = COALESCE(?, encoding_json),
                        supabase_id = ?, active = 1,
                        class_id = ?, class_name = ?, class_grade = ?, class_shift = ?,
                        enrollment_number = ?
                    WHERE id = ?
                    """,
                    (
                        full_name,
                        phone or "",
                        email or "",
                        payload_encoding,
                        supabase_id,
                        class_id,
                        class_name,
                        class_grade,
                        class_shift,
                        enrollment_number,
                        face_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO faces (
                        id, full_name, phone, email, encoding_json,
                        supabase_id, created_at, active,
                        class_id, class_name, class_grade, class_shift,
                        enrollment_number
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                    """,
                    (
                        face_id,
                        full_name,
                        phone or "",
                        email or "",
                        payload_encoding,
                        supabase_id,
                        now_iso,
                        class_id,
                        class_name,
                        class_grade,
                        class_shift,
                        enrollment_number,
                    ),
                )
            conn.commit()

    def count_present_by_class(self) -> list[dict[str, Any]]:
        """Per-class snapshot of who is currently in school (offline-safe).

        "Present" = today's row has first_entry_at set and (no last_exit_at OR
        last_exit_at < first_entry_at). Output is one row per class with a
        nested ``students`` array — used both by the panel and the kitchen
        dispatcher.
        """
        today = datetime.now(ZoneInfo(self.attendance_timezone)).date().isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    f.class_id, f.class_name, f.class_grade, f.class_shift,
                    f.id AS face_id, f.supabase_id, f.full_name, f.enrollment_number,
                    da.first_entry_at, da.last_exit_at
                FROM faces f
                JOIN daily_attendance da
                  ON da.face_id = f.id AND da.attendance_date = ?
                WHERE f.active = 1
                  AND da.first_entry_at IS NOT NULL
                  AND (da.last_exit_at IS NULL OR da.last_exit_at < da.first_entry_at)
                """,
                (today,),
            ).fetchall()

        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = row["class_id"] or "__unassigned__"
            bucket = grouped.setdefault(
                key,
                {
                    "class_id": row["class_id"],
                    "class_name": row["class_name"],
                    "class_grade": row["class_grade"],
                    "class_shift": row["class_shift"],
                    "students": [],
                },
            )
            bucket["students"].append(
                {
                    "face_id": row["face_id"],
                    "supabase_id": row["supabase_id"],
                    "full_name": row["full_name"],
                    "enrollment_number": row["enrollment_number"],
                    "first_entry_at": row["first_entry_at"],
                }
            )

        result = list(grouped.values())
        for bucket in result:
            bucket["present_count"] = len(bucket["students"])
        result.sort(
            key=lambda b: (
                b.get("class_grade") or "",
                b.get("class_name") or "",
            )
        )
        return result

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
        # supabase_id key is normalized so callers don't need to check for absence
        data.setdefault("supabase_id", None)
        return data
