"""
Banco de dados SQLite para perfis de rostos
Cada rosto tem: id, nome, telefone, data de cadastro, notas
"""

import sqlite3
import os
from datetime import datetime

DB_PATH = "database/faces.db"

class FaceDatabase:
    def __init__(self, db_path: str = DB_PATH):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS faces (
                    id          TEXT PRIMARY KEY,
                    full_name   TEXT NOT NULL,
                    phone       TEXT NOT NULL,
                    email       TEXT,
                    notes       TEXT,
                    created_at  TEXT NOT NULL,
                    active      INTEGER DEFAULT 1
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS detections (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    face_id     TEXT NOT NULL,
                    detected_at TEXT NOT NULL,
                    message_ok  INTEGER DEFAULT 0,
                    FOREIGN KEY (face_id) REFERENCES faces(id)
                )
            """)
            conn.commit()

    # ── CRUD de Rostos ──────────────────────────────────────────

    def add_face(self, face_id: str, full_name: str, phone: str,
                 email: str = "", notes: str = "") -> bool:
        """Registra um novo rosto no banco de dados."""
        try:
            with self._connect() as conn:
                conn.execute(
                    """INSERT INTO faces (id, full_name, phone, email, notes, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (face_id, full_name, phone, email, notes,
                     datetime.now().isoformat())
                )
                conn.commit()
            return True
        except sqlite3.IntegrityError:
            print(f"[DB] ID '{face_id}' já existe.")
            return False

    def get_face(self, face_id: str) -> dict | None:
        """Busca perfil de rosto pelo ID."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM faces WHERE id = ? AND active = 1", (face_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_faces(self) -> list[dict]:
        """Lista todos os rostos cadastrados."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM faces WHERE active = 1 ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def update_face(self, face_id: str, **kwargs) -> bool:
        """Atualiza campos de um perfil."""
        allowed = {"full_name", "phone", "email", "notes", "active"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        fields = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [face_id]
        with self._connect() as conn:
            conn.execute(f"UPDATE faces SET {fields} WHERE id = ?", values)
            conn.commit()
        return True

    def delete_face(self, face_id: str) -> bool:
        """Desativa (soft delete) um rosto."""
        return self.update_face(face_id, active=0)

    # ── Log de Detecções ────────────────────────────────────────

    def log_detection(self, face_id: str, message_ok: bool = False):
        """Registra uma detecção no histórico."""
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO detections (face_id, detected_at, message_ok)
                   VALUES (?, ?, ?)""",
                (face_id, datetime.now().isoformat(), int(message_ok))
            )
            conn.commit()

    def get_detections(self, face_id: str = None, limit: int = 50) -> list[dict]:
        """Retorna histórico de detecções."""
        with self._connect() as conn:
            if face_id:
                rows = conn.execute(
                    """SELECT d.*, f.full_name FROM detections d
                       JOIN faces f ON d.face_id = f.id
                       WHERE d.face_id = ? ORDER BY d.detected_at DESC LIMIT ?""",
                    (face_id, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT d.*, f.full_name FROM detections d
                       JOIN faces f ON d.face_id = f.id
                       ORDER BY d.detected_at DESC LIMIT ?""",
                    (limit,)
                ).fetchall()
        return [dict(r) for r in rows]
