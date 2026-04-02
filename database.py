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
        return self._row_to_face(row)

    def list_faces(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM faces WHERE active = 1 ORDER BY created_at DESC"
            ).fetchall()
        return [self._row_to_face(row) for row in rows]

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
        return [dict(row) for row in rows]

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
