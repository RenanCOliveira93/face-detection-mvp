"""SQLite para cadastro dos alunos e histórico de detecções."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

DB_PATH = "database/faces.db"


class FaceDatabase:
    def __init__(self, db_path: str = DB_PATH):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS faces (
                    id TEXT PRIMARY KEY,
                    full_name TEXT NOT NULL,
                    phone TEXT NOT NULL,
                    email TEXT,
                    notes TEXT,
                    photo_path TEXT,
                    encoding_json TEXT,
                    created_at TEXT NOT NULL,
                    active INTEGER DEFAULT 1
                )
                """
            )
            existing_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(faces)").fetchall()
            }
            for column, ddl in {
                "photo_path": "ALTER TABLE faces ADD COLUMN photo_path TEXT",
                "encoding_json": "ALTER TABLE faces ADD COLUMN encoding_json TEXT",
            }.items():
                if column not in existing_columns:
                    conn.execute(ddl)

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS detections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    face_id TEXT NOT NULL,
                    detected_at TEXT NOT NULL,
                    similarity REAL,
                    message_ok INTEGER DEFAULT 0,
                    message_info TEXT,
                    FOREIGN KEY (face_id) REFERENCES faces(id)
                )
                """
            )
            detection_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(detections)").fetchall()
            }
            for column, ddl in {
                "similarity": "ALTER TABLE detections ADD COLUMN similarity REAL",
                "message_info": "ALTER TABLE detections ADD COLUMN message_info TEXT",
            }.items():
                if column not in detection_columns:
                    conn.execute(ddl)
            conn.commit()

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
