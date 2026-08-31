"""SQLite para cadastro dos alunos e histórico de detecções/presença."""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import hashlib
from datetime import datetime, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

DB_PATH = os.getenv("DB_PATH", "database/faces.db")


class FaceDatabase:
    SCHEMA_VERSION = 11
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
            self._migration_9_school_management,
            self._migration_10_secure_school_operations,
            self._migration_11_pedagogical_module,
        ]
        if len(migrations) != self.SCHEMA_VERSION:
            raise RuntimeError("SCHEMA_VERSION não corresponde à lista de migrações")
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
            cur = conn.execute(
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
    def _migration_9_school_management(conn: sqlite3.Connection) -> None:
        """Cria a camada multi-escola sem invalidar cadastros legados."""
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schools (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                slug TEXT NOT NULL UNIQUE,
                api_key TEXT NOT NULL UNIQUE,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS school_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                school_id INTEGER NOT NULL,
                full_name TEXT NOT NULL,
                email TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin', 'professor')),
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                UNIQUE(school_id, email),
                FOREIGN KEY (school_id) REFERENCES schools(id)
            );
            CREATE TABLE IF NOT EXISTS classrooms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                school_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                school_year TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                UNIQUE(school_id, name, school_year),
                FOREIGN KEY (school_id) REFERENCES schools(id)
            );
            CREATE TABLE IF NOT EXISTS classroom_students (
                classroom_id INTEGER NOT NULL,
                face_id TEXT NOT NULL,
                enrolled_at TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (classroom_id, face_id),
                FOREIGN KEY (classroom_id) REFERENCES classrooms(id),
                FOREIGN KEY (face_id) REFERENCES faces(id)
            );
            CREATE TABLE IF NOT EXISTS teacher_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                school_id INTEGER NOT NULL,
                member_id INTEGER NOT NULL,
                face_id TEXT,
                classroom_id INTEGER,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL,
                CHECK(face_id IS NOT NULL OR classroom_id IS NOT NULL),
                FOREIGN KEY (school_id) REFERENCES schools(id),
                FOREIGN KEY (member_id) REFERENCES school_members(id),
                FOREIGN KEY (face_id) REFERENCES faces(id),
                FOREIGN KEY (classroom_id) REFERENCES classrooms(id)
            );
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(faces)").fetchall()}
        if "school_id" not in columns:
            conn.execute("ALTER TABLE faces ADD COLUMN school_id INTEGER REFERENCES schools(id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_faces_school ON faces(school_id, active)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_notes_school ON teacher_notes(school_id, created_at)"
        )

    @staticmethod
    def _migration_10_secure_school_operations(conn: sqlite3.Connection) -> None:
        """Add secure principals and local-first operational tables."""
        school_columns = {row[1] for row in conn.execute("PRAGMA table_info(schools)")}
        if "timezone" not in school_columns:
            conn.execute("ALTER TABLE schools ADD COLUMN timezone TEXT NOT NULL DEFAULT 'UTC'")
        if "api_key_hash" not in school_columns:
            conn.execute("ALTER TABLE schools ADD COLUMN api_key_hash TEXT")
        member_columns = {row[1] for row in conn.execute("PRAGMA table_info(school_members)")}
        if "api_key_hash" not in member_columns:
            conn.execute("ALTER TABLE school_members ADD COLUMN api_key_hash TEXT")
        presence_columns = {row[1] for row in conn.execute("PRAGMA table_info(presence_events)")}
        for column, ddl in {
            "school_id": "ALTER TABLE presence_events ADD COLUMN school_id INTEGER REFERENCES schools(id)",
            "device_id": "ALTER TABLE presence_events ADD COLUMN device_id TEXT",
            "idempotency_key": "ALTER TABLE presence_events ADD COLUMN idempotency_key TEXT",
        }.items():
            if column not in presence_columns:
                conn.execute(ddl)
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS dietary_restrictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, school_id INTEGER NOT NULL,
                face_id TEXT NOT NULL, description TEXT NOT NULL, severity TEXT NOT NULL DEFAULT 'atenção',
                active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
                FOREIGN KEY(school_id) REFERENCES schools(id), FOREIGN KEY(face_id) REFERENCES faces(id)
            );
            CREATE TABLE IF NOT EXISTS devices (
                id TEXT PRIMARY KEY, school_id INTEGER NOT NULL, name TEXT NOT NULL,
                secret_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
                FOREIGN KEY(school_id) REFERENCES schools(id)
            );
            CREATE TABLE IF NOT EXISTS device_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT NOT NULL, external_id TEXT NOT NULL,
                face_id TEXT NOT NULL, presence_event_id INTEGER, created_at TEXT NOT NULL,
                UNIQUE(device_id, external_id), FOREIGN KEY(device_id) REFERENCES devices(id)
            );
            CREATE TABLE IF NOT EXISTS kitchen_recipients (
                id INTEGER PRIMARY KEY AUTOINCREMENT, school_id INTEGER NOT NULL,
                name TEXT NOT NULL, phone TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL, FOREIGN KEY(school_id) REFERENCES schools(id)
            );
            CREATE TABLE IF NOT EXISTS kitchen_dispatches (
                id INTEGER PRIMARY KEY AUTOINCREMENT, school_id INTEGER NOT NULL,
                business_date TEXT NOT NULL, payload_json TEXT NOT NULL, status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
                UNIQUE(school_id, business_date), FOREIGN KEY(school_id) REFERENCES schools(id)
            );
            CREATE TABLE IF NOT EXISTS sync_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT, school_id INTEGER NOT NULL,
                event_key TEXT NOT NULL UNIQUE, aggregate_type TEXT NOT NULL, payload_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT, created_at TEXT NOT NULL, processed_at TEXT,
                FOREIGN KEY(school_id) REFERENCES schools(id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_presence_idempotency
              ON presence_events(idempotency_key) WHERE idempotency_key IS NOT NULL;
            CREATE TRIGGER IF NOT EXISTS reject_cross_school_enrollment
            BEFORE INSERT ON classroom_students BEGIN
              SELECT CASE WHEN (SELECT school_id FROM classrooms WHERE id=NEW.classroom_id)
                IS NOT (SELECT school_id FROM faces WHERE id=NEW.face_id)
                THEN RAISE(ABORT, 'cross-school enrollment') END;
            END;
            CREATE TRIGGER IF NOT EXISTS reject_cross_school_restriction
            BEFORE INSERT ON dietary_restrictions BEGIN
              SELECT CASE WHEN NEW.school_id IS NOT (SELECT school_id FROM faces WHERE id=NEW.face_id)
                THEN RAISE(ABORT, 'cross-school restriction') END;
            END;
            CREATE TRIGGER IF NOT EXISTS reject_cross_school_note
            BEFORE INSERT ON teacher_notes BEGIN
              SELECT CASE WHEN NEW.face_id IS NOT NULL AND NEW.school_id IS NOT
                (SELECT school_id FROM faces WHERE id=NEW.face_id)
                THEN RAISE(ABORT, 'cross-school note') END;
              SELECT CASE WHEN NEW.classroom_id IS NOT NULL AND NEW.school_id IS NOT
                (SELECT school_id FROM classrooms WHERE id=NEW.classroom_id)
                THEN RAISE(ABORT, 'cross-school note') END;
            END;
            """
        )

    @staticmethod
    def _migration_11_pedagogical_module(conn: sqlite3.Connection) -> None:
        """Disciplinas, alocação de professor, aulas com chamada confirmada e notas.

        A presença física (device/câmera) continua registrada em `daily_attendance`
        (portaria) e não é alterada por este módulo. `lesson_attendance` é a frequência
        pedagógica: pré-preenchida a partir de `daily_attendance` quando a aula é aberta,
        mas só passa a valer como frequência oficial depois que o professor confirma
        (`lessons.confirmed_at`). As duas tabelas coexistem de propósito — um aluno pode
        ter entrado na escola e faltado a uma aula específica.
        """
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS subjects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                school_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                UNIQUE(school_id, name),
                FOREIGN KEY (school_id) REFERENCES schools(id)
            );
            CREATE TABLE IF NOT EXISTS teacher_assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                school_id INTEGER NOT NULL,
                member_id INTEGER NOT NULL,
                classroom_id INTEGER NOT NULL,
                subject_id INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                ended_at TEXT,
                FOREIGN KEY (school_id) REFERENCES schools(id),
                FOREIGN KEY (member_id) REFERENCES school_members(id),
                FOREIGN KEY (classroom_id) REFERENCES classrooms(id),
                FOREIGN KEY (subject_id) REFERENCES subjects(id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_teacher_assignment_active
              ON teacher_assignments(classroom_id, subject_id) WHERE active = 1;
            CREATE TABLE IF NOT EXISTS lessons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                school_id INTEGER NOT NULL,
                classroom_id INTEGER NOT NULL,
                subject_id INTEGER NOT NULL,
                teacher_member_id INTEGER NOT NULL,
                lesson_date TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                confirmed_at TEXT,
                confirmed_by_member_id INTEGER,
                created_at TEXT NOT NULL,
                UNIQUE(school_id, classroom_id, subject_id, lesson_date),
                FOREIGN KEY (school_id) REFERENCES schools(id),
                FOREIGN KEY (classroom_id) REFERENCES classrooms(id),
                FOREIGN KEY (subject_id) REFERENCES subjects(id),
                FOREIGN KEY (teacher_member_id) REFERENCES school_members(id),
                FOREIGN KEY (confirmed_by_member_id) REFERENCES school_members(id)
            );
            CREATE TABLE IF NOT EXISTS lesson_attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lesson_id INTEGER NOT NULL,
                face_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ausente'
                    CHECK(status IN ('presente', 'ausente', 'justificado')),
                source TEXT NOT NULL DEFAULT 'auto' CHECK(source IN ('auto', 'manual')),
                updated_by_member_id INTEGER,
                updated_at TEXT NOT NULL,
                UNIQUE(lesson_id, face_id),
                FOREIGN KEY (lesson_id) REFERENCES lessons(id),
                FOREIGN KEY (face_id) REFERENCES faces(id),
                FOREIGN KEY (updated_by_member_id) REFERENCES school_members(id)
            );
            CREATE TABLE IF NOT EXISTS assessments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                school_id INTEGER NOT NULL,
                classroom_id INTEGER NOT NULL,
                subject_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                assessment_date TEXT NOT NULL,
                max_score REAL NOT NULL DEFAULT 10,
                created_by_member_id INTEGER,
                created_at TEXT NOT NULL,
                FOREIGN KEY (school_id) REFERENCES schools(id),
                FOREIGN KEY (classroom_id) REFERENCES classrooms(id),
                FOREIGN KEY (subject_id) REFERENCES subjects(id),
                FOREIGN KEY (created_by_member_id) REFERENCES school_members(id)
            );
            CREATE TABLE IF NOT EXISTS grades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                assessment_id INTEGER NOT NULL,
                face_id TEXT NOT NULL,
                score REAL NOT NULL,
                updated_by_member_id INTEGER,
                updated_at TEXT NOT NULL,
                UNIQUE(assessment_id, face_id),
                FOREIGN KEY (assessment_id) REFERENCES assessments(id),
                FOREIGN KEY (face_id) REFERENCES faces(id),
                FOREIGN KEY (updated_by_member_id) REFERENCES school_members(id)
            );
            CREATE INDEX IF NOT EXISTS idx_lessons_school_date ON lessons(school_id, lesson_date);
            CREATE INDEX IF NOT EXISTS idx_teacher_assignments_member ON teacher_assignments(member_id, active);
            """
        )

    @staticmethod
    def hash_secret(secret: str) -> str:
        return hashlib.sha256(secret.encode("utf-8")).hexdigest()

    def create_school(
        self, name: str, slug: str, api_key: str | None = None, timezone_name: str = "UTC"
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        key = api_key or secrets.token_urlsafe(32)
        ZoneInfo(timezone_name)
        key_hash = self.hash_secret(key)
        with self._connect() as conn:
            cursor = conn.execute(
                """INSERT INTO schools
                   (name, slug, api_key, api_key_hash, timezone, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (name.strip(), slug.strip().lower(), key_hash, key_hash, timezone_name, now),
            )
            conn.commit()
            return {
                "id": int(cursor.lastrowid), "name": name.strip(), "slug": slug,
                "timezone": timezone_name, "api_key": key,
            }

    def get_school_by_api_key(self, api_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT id, name, slug, timezone FROM schools
                   WHERE (api_key_hash = ? OR (api_key_hash IS NULL AND api_key = ?)) AND active = 1""",
                (self.hash_secret(api_key), api_key),
            ).fetchone()
        return dict(row) if row else None

    def add_school_member(
        self, school_id: int, full_name: str, email: str, role: str, api_key: str | None = None
    ) -> dict[str, Any]:
        if role not in {"school_admin", "professor"}:
            raise ValueError("Perfil inválido")
        if not full_name.strip() or not email.strip():
            raise ValueError("Nome e e-mail do membro são obrigatórios")
        with self._connect() as conn:
            cursor = conn.execute(
                """INSERT INTO school_members
                   (school_id, full_name, email, role, api_key_hash, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    school_id,
                    full_name.strip(),
                    email.strip().lower(),
                    "admin" if role == "school_admin" else role,
                    self.hash_secret(api_key or (key := secrets.token_urlsafe(32))),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
            return {"id": int(cursor.lastrowid), "api_key": api_key or key, "role": role}

    def authenticate_principal(self, api_key: str) -> dict[str, Any] | None:
        digest = self.hash_secret(api_key)
        with self._connect() as conn:
            row = conn.execute(
                """SELECT m.id AS member_id, m.school_id, m.full_name, m.role, s.timezone
                   FROM school_members m JOIN schools s ON s.id=m.school_id
                   WHERE m.api_key_hash=? AND m.active=1 AND s.active=1""", (digest,)
            ).fetchone()
        result = dict(row) if row else None
        if result and result["role"] == "admin":
            result["role"] = "school_admin"
        return result

    def revoke_member(self, school_id: int, member_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE school_members SET active=0 WHERE id=? AND school_id=?", (member_id, school_id)
            )
            conn.commit()
            return cur.rowcount == 1

    def list_school_members(self, school_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(
                """SELECT id,full_name,email,CASE role WHEN 'admin' THEN 'school_admin' ELSE role END role,
                          active,created_at FROM school_members WHERE school_id=? ORDER BY full_name""",
                (school_id,),
            )]

    def create_classroom(self, school_id: int, name: str, school_year: str = "") -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO classrooms (school_id, name, school_year, created_at) VALUES (?, ?, ?, ?)",
                (school_id, name.strip(), school_year.strip(), datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def list_classrooms(self, school_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(
                """SELECT id, name, school_year FROM classrooms
                   WHERE school_id=? AND active=1 ORDER BY name""",
                (school_id,),
            )]

    def update_classroom(self, school_id: int, classroom_id: int, name: str, school_year: str = "") -> None:
        if not name.strip():
            raise ValueError("Nome da turma é obrigatório")
        with self._connect() as conn:
            try:
                cur = conn.execute(
                    """UPDATE classrooms SET name=?, school_year=?
                       WHERE id=? AND school_id=? AND active=1""",
                    (name.strip(), school_year.strip(), classroom_id, school_id),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Já existe uma turma com esse nome/ano") from exc
            if cur.rowcount == 0:
                raise ValueError("Turma não encontrada nesta escola")
            conn.commit()

    def deactivate_classroom(self, school_id: int, classroom_id: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE classrooms SET active=0 WHERE id=? AND school_id=? AND active=1",
                (classroom_id, school_id),
            )
            if cur.rowcount == 0:
                raise ValueError("Turma não encontrada nesta escola")
            conn.execute(
                "UPDATE teacher_assignments SET active=0, ended_at=? WHERE classroom_id=? AND active=1",
                (now, classroom_id),
            )
            conn.commit()

    def list_classroom_students(self, school_id: int, classroom_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            classroom_ok = conn.execute(
                "SELECT 1 FROM classrooms WHERE id=? AND school_id=? AND active=1", (classroom_id, school_id)
            ).fetchone()
            if not classroom_ok:
                raise ValueError("Turma não encontrada nesta escola")
            return [dict(r) for r in conn.execute(
                """SELECT f.id AS face_id, f.full_name
                   FROM classroom_students cs JOIN faces f ON f.id = cs.face_id
                   WHERE cs.classroom_id=? AND cs.active=1 AND f.active=1
                   ORDER BY f.full_name""",
                (classroom_id,),
            )]

    def add_kitchen_recipient(self, school_id: int, name: str, phone: str) -> int:
        if not name.strip() or not phone.strip():
            raise ValueError("Nome e telefone são obrigatórios")
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO kitchen_recipients(school_id,name,phone,created_at)
                   VALUES(?,?,?,?)""", (school_id, name.strip(), phone.strip(), datetime.now(timezone.utc).isoformat())
            )
            conn.commit()
            return int(cur.lastrowid)

    def prepare_kitchen_dispatch(self, school_id: int, business_date: str | None = None) -> dict[str, Any]:
        dashboard = self.get_school_dashboard(school_id)
        date_value = business_date or dashboard["business_date"]
        payload = {"business_date": date_value, "present_by_class": [
            {"classroom": c["name"], "present": c["present_count"]} for c in dashboard["classrooms"]
        ], "dietary_restrictions": dashboard["dietary_restrictions"]}
        with self._connect() as conn:
            recipients = [dict(r) for r in conn.execute(
                "SELECT id,name,phone FROM kitchen_recipients WHERE school_id=? AND active=1", (school_id,)
            )]
            if not recipients:
                raise ValueError("Nenhum destinatário da cozinha configurado")
            cur = conn.execute(
                """INSERT INTO kitchen_dispatches(school_id,business_date,payload_json,status,created_at)
                   VALUES(?,?,?,'mocked',?) ON CONFLICT(school_id,business_date) DO NOTHING""",
                (school_id, date_value, json.dumps(payload, ensure_ascii=False), datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
        return {"payload": payload, "recipients": recipients, "duplicate": cur.rowcount == 0}

    def finish_kitchen_dispatch(self, school_id: int, business_date: str, success: bool) -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE kitchen_dispatches SET status=?,attempts=attempts+1
                   WHERE school_id=? AND business_date=?""",
                ("sent" if success else "failed", school_id, business_date),
            )
            conn.commit()

    def assign_face_to_school(self, face_id: str, school_id: int) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """UPDATE faces SET school_id = ?
                   WHERE id = ? AND active = 1 AND (school_id IS NULL OR school_id = ?)""",
                (school_id, face_id, school_id),
            )
            conn.commit()
            return cursor.rowcount == 1

    def erase_student_personal_data(self, school_id: int, face_id: str) -> str | None:
        """Anonymize PII/biometrics while preserving non-identifying attendance audit."""
        redacted = "[dados removidos]"
        with self._connect() as conn:
            row = conn.execute(
                "SELECT photo_path FROM faces WHERE id=? AND school_id=?", (face_id, school_id)
            ).fetchone()
            if not row:
                raise ValueError("Aluno não encontrado nesta escola")

            guardian_ids = [
                r["guardian_id"] for r in conn.execute(
                    "SELECT guardian_id FROM student_guardians WHERE face_id=?", (face_id,)
                ).fetchall()
            ]
            conn.execute("DELETE FROM student_guardians WHERE face_id=?", (face_id,))
            for guardian_id in guardian_ids:
                still_linked = conn.execute(
                    "SELECT 1 FROM student_guardians WHERE guardian_id=?", (guardian_id,)
                ).fetchone()
                if not still_linked:
                    conn.execute("DELETE FROM guardian_phones WHERE guardian_id=?", (guardian_id,))
                    conn.execute("DELETE FROM guardians WHERE id=?", (guardian_id,))

            conn.execute("DELETE FROM classroom_students WHERE face_id=?", (face_id,))
            conn.execute("DELETE FROM dietary_restrictions WHERE face_id=?", (face_id,))
            conn.execute("DELETE FROM teacher_notes WHERE face_id=?", (face_id,))

            # Nomes/telefones ficam embutidos no texto de mensagens e retornos de webhook
            # já disparados; o registro (contagem/horário) é preservado, o texto é redigido.
            conn.execute(
                "UPDATE detections SET message_info=? WHERE face_id=? AND message_info IS NOT NULL",
                (redacted, face_id),
            )
            conn.execute(
                """UPDATE presence_events SET
                       message_info = CASE WHEN message_info IS NOT NULL THEN ? ELSE message_info END,
                       webhook_info = CASE WHEN webhook_info IS NOT NULL THEN ? ELSE webhook_info END
                   WHERE face_id=?""",
                (redacted, redacted, face_id),
            )

            conn.execute(
                """UPDATE faces SET full_name='Titular removido',phone='',email='',notes='',
                   photo_path=NULL,encoding_json=NULL,active=0 WHERE id=? AND school_id=?""",
                (face_id, school_id),
            )
            conn.commit()
            return row["photo_path"]

    def enroll_student(self, school_id: int, classroom_id: int, face_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            valid = conn.execute(
                """SELECT 1 FROM classrooms c JOIN faces f ON f.school_id = c.school_id
                   WHERE c.id = ? AND c.school_id = ? AND f.id = ? AND c.active = 1 AND f.active = 1""",
                (classroom_id, school_id, face_id),
            ).fetchone()
            if not valid:
                raise ValueError("Turma ou aluno não pertence à escola")
            conn.execute(
                """INSERT INTO classroom_students (classroom_id, face_id, enrolled_at, active)
                   VALUES (?, ?, ?, 1) ON CONFLICT(classroom_id, face_id)
                   DO UPDATE SET active = 1, enrolled_at = excluded.enrolled_at""",
                (classroom_id, face_id, now),
            )
            conn.commit()

    def add_teacher_note(
        self,
        school_id: int,
        member_id: int,
        body: str,
        face_id: str | None = None,
        classroom_id: int | None = None,
    ) -> int:
        if not body.strip() or (not face_id and not classroom_id):
            raise ValueError("Anotação e aluno ou turma são obrigatórios")
        with self._connect() as conn:
            member = conn.execute(
                "SELECT 1 FROM school_members WHERE id = ? AND school_id = ? AND active = 1",
                (member_id, school_id),
            ).fetchone()
            face_ok = not face_id or conn.execute(
                "SELECT 1 FROM faces WHERE id = ? AND school_id = ? AND active = 1",
                (face_id, school_id),
            ).fetchone()
            class_ok = not classroom_id or conn.execute(
                "SELECT 1 FROM classrooms WHERE id = ? AND school_id = ? AND active = 1",
                (classroom_id, school_id),
            ).fetchone()
            if not member or not face_ok or not class_ok:
                raise ValueError("Membro, aluno ou turma não pertence à escola")
            cursor = conn.execute(
                """INSERT INTO teacher_notes
                   (school_id, member_id, face_id, classroom_id, body, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    school_id,
                    member_id,
                    face_id,
                    classroom_id,
                    body.strip(),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def get_school_dashboard(self, school_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            school = conn.execute("SELECT timezone FROM schools WHERE id=?", (school_id,)).fetchone()
            if not school:
                raise ValueError("Escola não encontrada")
            business_date = datetime.now(ZoneInfo(school["timezone"])).date().isoformat()
            classrooms = [
                dict(row)
                for row in conn.execute(
                    """SELECT c.id, c.name, c.school_year,
                          COUNT(DISTINCT cs.face_id) AS student_count,
                          COUNT(DISTINCT CASE WHEN da.status='presente' THEN cs.face_id END) AS present_count
                   FROM classrooms c LEFT JOIN classroom_students cs
                     ON cs.classroom_id = c.id AND cs.active = 1
                   LEFT JOIN daily_attendance da ON da.face_id=cs.face_id AND da.attendance_date=?
                   WHERE c.school_id = ? AND c.active = 1
                   GROUP BY c.id ORDER BY c.name""",
                    (business_date, school_id),
                ).fetchall()
            ]
            totals = conn.execute(
                """SELECT COUNT(*) AS students,
                   SUM(CASE WHEN da.status = 'presente' THEN 1 ELSE 0 END) AS present_today
                   FROM faces f LEFT JOIN daily_attendance da ON da.face_id = f.id
                     AND da.attendance_date = ?
                   WHERE f.school_id = ? AND f.active = 1""",
                (business_date, school_id),
            ).fetchone()
            notes = [
                dict(row)
                for row in conn.execute(
                    """SELECT n.id, n.body, n.face_id, n.classroom_id, n.created_at,
                          m.full_name AS author_name
                   FROM teacher_notes n JOIN school_members m ON m.id = n.member_id
                   WHERE n.school_id = ? ORDER BY n.created_at DESC LIMIT 50""",
                    (school_id,),
                ).fetchall()
            ]
            restrictions = [dict(r) for r in conn.execute(
                """SELECT dr.id,dr.face_id,f.full_name,dr.description,dr.severity
                   FROM dietary_restrictions dr JOIN faces f ON f.id=dr.face_id
                   WHERE dr.school_id=? AND dr.active=1 ORDER BY f.full_name""", (school_id,)
            )]
            last_update = conn.execute(
                "SELECT MAX(event_at) FROM presence_events WHERE school_id=?", (school_id,)
            ).fetchone()[0]
        students = int(totals["students"] or 0)
        present = int(totals["present_today"] or 0)
        return {
            "business_date": business_date,
            "students": students,
            "present_today": present,
            "absent_today": max(0, students - present),
            "classrooms": classrooms,
            "notes": notes,
            "dietary_restrictions": restrictions,
            "last_updated_at": last_update,
        }

    def add_dietary_restriction(
        self, school_id: int, face_id: str, description: str, severity: str = "atenção"
    ) -> int:
        if not description.strip():
            raise ValueError("Descrição é obrigatória")
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO dietary_restrictions
                   (school_id, face_id, description, severity, created_at) VALUES (?, ?, ?, ?, ?)""",
                (school_id, face_id, description.strip(), severity, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            return int(cur.lastrowid)

    def create_device(self, school_id: int, device_id: str, name: str, secret: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO devices (id, school_id, name, secret_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                (device_id, school_id, name, self.hash_secret(secret), datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()

    def authenticate_device(self, device_id: str, secret: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT d.id, d.school_id, s.timezone FROM devices d JOIN schools s ON s.id=d.school_id
                   WHERE d.id=? AND d.secret_hash=? AND d.active=1 AND s.active=1""",
                (device_id, self.hash_secret(secret)),
            ).fetchone()
        return dict(row) if row else None

    def record_device_presence(
        self, school_id: int, device_id: str, external_id: str, face_id: str,
        event_at: str | None = None,
    ) -> tuple[int, str, bool]:
        now = event_at or datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT presence_event_id FROM device_events WHERE device_id=? AND external_id=?",
                (device_id, external_id),
            ).fetchone()
            if existing:
                event = conn.execute(
                    "SELECT direction FROM presence_events WHERE id=?", (existing["presence_event_id"],)
                ).fetchone()
                return int(existing["presence_event_id"]), event["direction"], True
            face = conn.execute(
                "SELECT 1 FROM faces WHERE id=? AND school_id=? AND active=1", (face_id, school_id)
            ).fetchone()
            if not face:
                raise ValueError("Aluno não pertence à escola do dispositivo")
            local_date = self._parse_iso_datetime(now).astimezone(
                ZoneInfo(conn.execute("SELECT timezone FROM schools WHERE id=?", (school_id,)).fetchone()[0])
            ).date().isoformat()
            # event_at is UTC; filter precisely using Python for non-UTC school timezones.
            candidates = conn.execute(
                """SELECT direction,event_at FROM presence_events WHERE face_id=? AND school_id=?
                   ORDER BY event_at DESC LIMIT 100""", (face_id, school_id)
            ).fetchall()
            last_direction = next((r["direction"] for r in candidates if self._attendance_date_from_zone(r["event_at"], conn, school_id) == local_date), None)
            direction = "saida" if last_direction == "entrada" else "entrada"
            cur = conn.execute(
                """INSERT INTO presence_events
                   (face_id,direction,event_at,school_id,device_id,idempotency_key)
                   VALUES (?,?,?,?,?,?)""", (face_id, direction, now, school_id, device_id, f"device:{device_id}:{external_id}"),
            )
            event_id = int(cur.lastrowid)
            conn.execute(
                "INSERT INTO device_events (device_id,external_id,face_id,presence_event_id,created_at) VALUES (?,?,?,?,?)",
                (device_id, external_id, face_id, event_id, now),
            )
            self._upsert_daily_attendance(conn, face_id, direction, now, local_date)
            self._enqueue_outbox(conn, school_id, f"presence:{event_id}", "presence", {
                "event_id": event_id, "face_id": face_id, "direction": direction, "event_at": now,
            })
            conn.commit()
            return event_id, direction, False

    def _attendance_date_from_zone(self, event_at: str, conn: sqlite3.Connection, school_id: int) -> str:
        zone = conn.execute("SELECT timezone FROM schools WHERE id=?", (school_id,)).fetchone()[0]
        return self._parse_iso_datetime(event_at).astimezone(ZoneInfo(zone)).date().isoformat()

    def _enqueue_outbox(
        self, conn: sqlite3.Connection, school_id: int, key: str, aggregate: str, payload: dict[str, Any]
    ) -> None:
        conn.execute(
            """INSERT OR IGNORE INTO sync_outbox
               (school_id,event_key,aggregate_type,payload_json,created_at) VALUES (?,?,?,?,?)""",
            (school_id, key, aggregate, json.dumps(payload), datetime.now(timezone.utc).isoformat()),
        )

    def pending_outbox(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM sync_outbox WHERE status='pending' ORDER BY id LIMIT ?", (limit,)
            )]

    def mark_outbox(self, item_id: int, success: bool, error: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE sync_outbox SET status=?, attempts=attempts+1,last_error=?,processed_at=?
                   WHERE id=?""",
                ("sent" if success else "pending", error, datetime.now(timezone.utc).isoformat() if success else None, item_id),
            )
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
        school_id: int | None = None,
    ) -> bool:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO faces (
                        id, full_name, phone, email, notes, photo_path,
                        encoding_json, created_at, school_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        school_id,
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

    def list_faces(self, school_id: int | None = None) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM faces WHERE active = 1 AND (? IS NULL OR school_id = ?)
                   ORDER BY created_at DESC""", (school_id, school_id)
            ).fetchall()
        faces = [self._row_to_face(row) for row in rows]
        for face in faces:
            recipient = self.get_preferred_notification_recipient(face["id"], channel="whatsapp")
            face["notification_phone"] = recipient["phone"] if recipient else face.get("phone")
        return faces

    def update_face(self, face_id: str, **kwargs: Any) -> bool:
        allowed = {
            "full_name",
            "phone",
            "email",
            "notes",
            "photo_path",
            "active",
            "encoding",
            "school_id",
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
            face = conn.execute("SELECT school_id FROM faces WHERE id=?", (face_id,)).fetchone()
            school_id = face["school_id"] if face else None
            cursor = conn.execute(
                """
                INSERT INTO presence_events (face_id, direction, event_at, match_score, school_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    face_id,
                    direction,
                    event_at_value,
                    match_score,
                    school_id,
                ),
            )
            self._upsert_daily_attendance(
                conn=conn,
                face_id=face_id,
                direction=direction,
                event_at=event_at_value,
            )
            event_id = int(cursor.lastrowid)
            if school_id is not None:
                self._enqueue_outbox(conn, school_id, f"presence:{event_id}", "presence", {
                    "event_id": event_id, "face_id": face_id, "direction": direction, "event_at": event_at_value,
                })
            conn.commit()
            return event_id

    def _upsert_daily_attendance(
        self,
        conn: sqlite3.Connection,
        face_id: str,
        direction: str,
        event_at: str,
        attendance_date_override: str | None = None,
    ) -> None:
        attendance_date = attendance_date_override or self._attendance_date_from_event(event_at)

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

    def get_presence_events(self, limit: int = 50, school_id: int | None = None) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT p.*, f.full_name, f.phone
                FROM presence_events p
                JOIN faces f ON p.face_id = f.id
                WHERE (? IS NULL OR p.school_id = ?)
                ORDER BY p.event_at DESC
                LIMIT ?
                """,
                (school_id, school_id, limit),
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

    def get_daily_attendance(self, limit: int = 100, school_id: int | None = None) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT da.*, f.full_name, f.phone
                FROM daily_attendance da
                JOIN faces f ON da.face_id = f.id
                WHERE (? IS NULL OR f.school_id = ?)
                ORDER BY da.attendance_date DESC, da.face_id ASC
                LIMIT ?
                """,
                (school_id, school_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_attendance_book(
        self, school_id: int, attendance_date: str | None = None
    ) -> dict[str, Any]:
        """Return the complete active roster, including students without events."""
        with self._connect() as conn:
            school = conn.execute(
                "SELECT timezone FROM schools WHERE id=? AND active=1", (school_id,)
            ).fetchone()
            if not school:
                raise ValueError("Escola não encontrada")
            date_value = attendance_date or datetime.now(
                ZoneInfo(school["timezone"])
            ).date().isoformat()
            try:
                datetime.strptime(date_value, "%Y-%m-%d")
            except ValueError as exc:
                raise ValueError("Data deve usar o formato YYYY-MM-DD") from exc
            rows = [dict(row) for row in conn.execute(
                """
                SELECT f.id AS face_id, f.full_name,
                       c.id AS classroom_id, c.name AS classroom_name,
                       da.first_entry_at, da.last_exit_at,
                       COALESCE(da.status, 'ausente') AS status,
                       COALESCE(da.total_transitions, 0) AS total_transitions
                FROM faces f
                LEFT JOIN classroom_students cs
                  ON cs.face_id=f.id AND cs.active=1
                LEFT JOIN classrooms c
                  ON c.id=cs.classroom_id AND c.school_id=f.school_id AND c.active=1
                LEFT JOIN daily_attendance da
                  ON da.face_id=f.id AND da.attendance_date=?
                WHERE f.school_id=? AND f.active=1
                ORDER BY COALESCE(c.name, ''), f.full_name
                """,
                (date_value, school_id),
            )]
        present = sum(item["status"] == "presente" for item in rows)
        inconsistent = sum(item["status"] == "inconsistente" for item in rows)
        return {
            "date": date_value,
            "timezone": school["timezone"],
            "total_students": len(rows),
            "present": present,
            "absent": sum(item["status"] == "ausente" for item in rows),
            "inconsistent": inconsistent,
            "items": rows,
        }
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

    # --- Módulo pedagógico: disciplinas, alocação, aulas, chamada e notas ---

    def create_subject(self, school_id: int, name: str) -> dict[str, Any]:
        if not name.strip():
            raise ValueError("Nome da disciplina é obrigatório")
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO subjects (school_id, name, created_at) VALUES (?, ?, ?)",
                (school_id, name.strip(), datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            return {"id": int(cur.lastrowid), "school_id": school_id, "name": name.strip()}

    def list_subjects(self, school_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT id, name, active FROM subjects WHERE school_id=? AND active=1 ORDER BY name",
                (school_id,),
            )]

    def update_subject(self, school_id: int, subject_id: int, name: str) -> None:
        if not name.strip():
            raise ValueError("Nome da disciplina é obrigatório")
        with self._connect() as conn:
            try:
                cur = conn.execute(
                    "UPDATE subjects SET name=? WHERE id=? AND school_id=? AND active=1",
                    (name.strip(), subject_id, school_id),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Já existe uma disciplina com esse nome") from exc
            if cur.rowcount == 0:
                raise ValueError("Disciplina não encontrada nesta escola")
            conn.commit()

    def deactivate_subject(self, school_id: int, subject_id: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE subjects SET active=0 WHERE id=? AND school_id=? AND active=1",
                (subject_id, school_id),
            )
            if cur.rowcount == 0:
                raise ValueError("Disciplina não encontrada nesta escola")
            conn.execute(
                "UPDATE teacher_assignments SET active=0, ended_at=? WHERE subject_id=? AND active=1",
                (now, subject_id),
            )
            conn.commit()

    def _validate_pedagogical_scope(
        self, conn: sqlite3.Connection, school_id: int, classroom_id: int, subject_id: int,
        member_id: int | None = None,
    ) -> None:
        classroom_ok = conn.execute(
            "SELECT 1 FROM classrooms WHERE id=? AND school_id=? AND active=1", (classroom_id, school_id)
        ).fetchone()
        subject_ok = conn.execute(
            "SELECT 1 FROM subjects WHERE id=? AND school_id=? AND active=1", (subject_id, school_id)
        ).fetchone()
        if not classroom_ok or not subject_ok:
            raise ValueError("Turma ou disciplina não pertence à escola")
        if member_id is not None:
            member_ok = conn.execute(
                "SELECT 1 FROM school_members WHERE id=? AND school_id=? AND active=1", (member_id, school_id)
            ).fetchone()
            if not member_ok:
                raise ValueError("Professor não pertence à escola")

    def create_teacher_assignment(
        self, school_id: int, member_id: int, classroom_id: int, subject_id: int
    ) -> dict[str, Any]:
        """Vincula um professor a turma+disciplina. Substitui o professor anterior (troca de professor)."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            self._validate_pedagogical_scope(conn, school_id, classroom_id, subject_id, member_id)
            conn.execute(
                """UPDATE teacher_assignments SET active=0, ended_at=?
                   WHERE classroom_id=? AND subject_id=? AND active=1""",
                (now, classroom_id, subject_id),
            )
            cur = conn.execute(
                """INSERT INTO teacher_assignments
                   (school_id, member_id, classroom_id, subject_id, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (school_id, member_id, classroom_id, subject_id, now),
            )
            conn.commit()
            return {
                "id": int(cur.lastrowid), "member_id": member_id,
                "classroom_id": classroom_id, "subject_id": subject_id,
            }

    def list_teacher_assignments(
        self, school_id: int, member_id: int | None = None
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(
                """SELECT ta.id, ta.member_id, m.full_name AS teacher_name,
                          ta.classroom_id, c.name AS classroom_name,
                          ta.subject_id, s.name AS subject_name
                   FROM teacher_assignments ta
                   JOIN school_members m ON m.id = ta.member_id
                   JOIN classrooms c ON c.id = ta.classroom_id
                   JOIN subjects s ON s.id = ta.subject_id
                   WHERE ta.school_id=? AND ta.active=1 AND (? IS NULL OR ta.member_id=?)
                   ORDER BY c.name, s.name""",
                (school_id, member_id, member_id),
            )]

    def _is_teacher_assigned(
        self, conn: sqlite3.Connection, school_id: int, member_id: int, classroom_id: int, subject_id: int
    ) -> bool:
        return bool(conn.execute(
            """SELECT 1 FROM teacher_assignments
               WHERE school_id=? AND member_id=? AND classroom_id=? AND subject_id=? AND active=1""",
            (school_id, member_id, classroom_id, subject_id),
        ).fetchone())

    def is_teacher_assigned(
        self, school_id: int, member_id: int, classroom_id: int, subject_id: int
    ) -> bool:
        with self._connect() as conn:
            return self._is_teacher_assigned(conn, school_id, member_id, classroom_id, subject_id)

    def open_lesson(
        self, school_id: int, classroom_id: int, subject_id: int, teacher_member_id: int,
        lesson_date: str,
    ) -> dict[str, Any]:
        """Abre (ou retorna) a aula do dia, pré-preenchendo a chamada a partir da
        presença física (`daily_attendance`). Não sobrescreve correções já feitas
        pelo professor em uma reabertura."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            self._validate_pedagogical_scope(conn, school_id, classroom_id, subject_id, teacher_member_id)
            existing = conn.execute(
                """SELECT id FROM lessons
                   WHERE school_id=? AND classroom_id=? AND subject_id=? AND lesson_date=?""",
                (school_id, classroom_id, subject_id, lesson_date),
            ).fetchone()
            if existing:
                lesson_id = int(existing["id"])
            else:
                cur = conn.execute(
                    """INSERT INTO lessons
                       (school_id, classroom_id, subject_id, teacher_member_id, lesson_date, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (school_id, classroom_id, subject_id, teacher_member_id, lesson_date, now),
                )
                lesson_id = int(cur.lastrowid)
                students = conn.execute(
                    """SELECT cs.face_id, COALESCE(da.status, 'ausente') AS status
                       FROM classroom_students cs
                       LEFT JOIN daily_attendance da
                         ON da.face_id = cs.face_id AND da.attendance_date = ?
                       WHERE cs.classroom_id = ? AND cs.active = 1""",
                    (lesson_date, classroom_id),
                ).fetchall()
                for student in students:
                    auto_status = student["status"] if student["status"] != "inconsistente" else "presente"
                    conn.execute(
                        """INSERT INTO lesson_attendance
                           (lesson_id, face_id, status, source, updated_at)
                           VALUES (?, ?, ?, 'auto', ?)""",
                        (lesson_id, student["face_id"], auto_status, now),
                    )
            conn.commit()
        return self.get_lesson(school_id, lesson_id)

    def get_lesson(self, school_id: int, lesson_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            lesson = conn.execute(
                """SELECT l.*, c.name AS classroom_name, s.name AS subject_name,
                          m.full_name AS teacher_name
                   FROM lessons l
                   JOIN classrooms c ON c.id = l.classroom_id
                   JOIN subjects s ON s.id = l.subject_id
                   JOIN school_members m ON m.id = l.teacher_member_id
                   WHERE l.id=? AND l.school_id=?""",
                (lesson_id, school_id),
            ).fetchone()
            if not lesson:
                return None
            attendance = [dict(r) for r in conn.execute(
                """SELECT la.face_id, f.full_name, la.status, la.source, la.updated_at
                   FROM lesson_attendance la JOIN faces f ON f.id = la.face_id
                   WHERE la.lesson_id=? ORDER BY f.full_name""",
                (lesson_id,),
            )]
        result = dict(lesson)
        result["attendance"] = attendance
        result["confirmed"] = result["confirmed_at"] is not None
        return result

    def update_lesson_attendance(
        self, school_id: int, lesson_id: int, face_id: str, status: str, member_id: int
    ) -> None:
        if status not in {"presente", "ausente", "justificado"}:
            raise ValueError("Status de presença inválido")
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            lesson = conn.execute(
                "SELECT confirmed_at FROM lessons WHERE id=? AND school_id=?", (lesson_id, school_id)
            ).fetchone()
            if not lesson:
                raise ValueError("Aula não encontrada nesta escola")
            if lesson["confirmed_at"] is not None:
                raise ValueError("Chamada já confirmada; não pode mais ser corrigida")
            cur = conn.execute(
                """UPDATE lesson_attendance SET status=?, source='manual',
                       updated_by_member_id=?, updated_at=?
                   WHERE lesson_id=? AND face_id=?""",
                (status, member_id, now, lesson_id, face_id),
            )
            if cur.rowcount == 0:
                raise ValueError("Aluno não faz parte da chamada desta aula")
            conn.commit()

    def confirm_lesson(self, school_id: int, lesson_id: int, member_id: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                """UPDATE lessons SET confirmed_at=?, confirmed_by_member_id=?
                   WHERE id=? AND school_id=? AND confirmed_at IS NULL""",
                (now, member_id, lesson_id, school_id),
            )
            if cur.rowcount == 0:
                existing = conn.execute(
                    "SELECT 1 FROM lessons WHERE id=? AND school_id=?", (lesson_id, school_id)
                ).fetchone()
                conn.commit()
                if not existing:
                    raise ValueError("Aula não encontrada nesta escola")
                raise ValueError("Chamada já estava confirmada")
            conn.commit()

    def update_lesson_content(self, school_id: int, lesson_id: int, content: str) -> None:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE lessons SET content=? WHERE id=? AND school_id=?",
                (content.strip(), lesson_id, school_id),
            )
            if cur.rowcount == 0:
                raise ValueError("Aula não encontrada nesta escola")
            conn.commit()

    def create_assessment(
        self, school_id: int, classroom_id: int, subject_id: int, title: str,
        assessment_date: str, max_score: float = 10, created_by_member_id: int | None = None,
    ) -> dict[str, Any]:
        if not title.strip():
            raise ValueError("Título da avaliação é obrigatório")
        with self._connect() as conn:
            self._validate_pedagogical_scope(conn, school_id, classroom_id, subject_id)
            cur = conn.execute(
                """INSERT INTO assessments
                   (school_id, classroom_id, subject_id, title, assessment_date, max_score,
                    created_by_member_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    school_id, classroom_id, subject_id, title.strip(), assessment_date,
                    max_score, created_by_member_id, datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
            return {"id": int(cur.lastrowid), "title": title.strip(), "max_score": max_score}

    def list_assessments(
        self, school_id: int, classroom_id: int | None = None, subject_id: int | None = None
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(
                """SELECT a.id, a.title, a.assessment_date, a.max_score,
                          a.classroom_id, c.name AS classroom_name,
                          a.subject_id, s.name AS subject_name
                   FROM assessments a
                   JOIN classrooms c ON c.id = a.classroom_id
                   JOIN subjects s ON s.id = a.subject_id
                   WHERE a.school_id=?
                     AND (? IS NULL OR a.classroom_id=?)
                     AND (? IS NULL OR a.subject_id=?)
                   ORDER BY a.assessment_date DESC""",
                (school_id, classroom_id, classroom_id, subject_id, subject_id),
            )]

    def upsert_grade(
        self, school_id: int, assessment_id: int, face_id: str, score: float, member_id: int | None = None
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            assessment = conn.execute(
                "SELECT max_score FROM assessments WHERE id=? AND school_id=?", (assessment_id, school_id)
            ).fetchone()
            if not assessment:
                raise ValueError("Avaliação não encontrada nesta escola")
            if score < 0 or score > assessment["max_score"]:
                raise ValueError(f"Nota deve estar entre 0 e {assessment['max_score']}")
            face_ok = conn.execute(
                "SELECT 1 FROM faces WHERE id=? AND school_id=?", (face_id, school_id)
            ).fetchone()
            if not face_ok:
                raise ValueError("Aluno não pertence à escola")
            conn.execute(
                """INSERT INTO grades (assessment_id, face_id, score, updated_by_member_id, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(assessment_id, face_id)
                   DO UPDATE SET score=excluded.score, updated_by_member_id=excluded.updated_by_member_id,
                                 updated_at=excluded.updated_at""",
                (assessment_id, face_id, score, member_id, now),
            )
            conn.commit()

    def list_grades(self, school_id: int, assessment_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            assessment_ok = conn.execute(
                "SELECT 1 FROM assessments WHERE id=? AND school_id=?", (assessment_id, school_id)
            ).fetchone()
            if not assessment_ok:
                raise ValueError("Avaliação não encontrada nesta escola")
            return [dict(r) for r in conn.execute(
                """SELECT g.face_id, f.full_name, g.score, g.updated_at
                   FROM grades g JOIN faces f ON f.id = g.face_id
                   WHERE g.assessment_id=? ORDER BY f.full_name""",
                (assessment_id,),
            )]

    def get_report_card(
        self, school_id: int, classroom_id: int, face_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Boletim consolidado: notas de cada aluno agrupadas por disciplina, com média.

        Todo aluno matriculado aparece, mesmo sem nenhuma nota lançada ainda — nesse
        caso a(s) avaliação(ões) da turma aparecem com score=None e a média fica None,
        em vez de simplesmente omitir a disciplina. Se a turma não tem nenhuma
        avaliação cadastrada, o aluno aparece com `subjects: []`.
        """
        with self._connect() as conn:
            classroom_ok = conn.execute(
                "SELECT 1 FROM classrooms WHERE id=? AND school_id=? AND active=1", (classroom_id, school_id)
            ).fetchone()
            if not classroom_ok:
                raise ValueError("Turma não encontrada nesta escola")
            rows = conn.execute(
                """SELECT f.id AS face_id, f.full_name,
                          s.id AS subject_id, s.name AS subject_name,
                          a.id AS assessment_id, a.title, a.assessment_date, a.max_score,
                          g.score
                   FROM classroom_students cs
                   JOIN faces f ON f.id = cs.face_id
                   LEFT JOIN assessments a ON a.classroom_id = cs.classroom_id AND a.school_id = ?
                   LEFT JOIN subjects s ON s.id = a.subject_id
                   LEFT JOIN grades g ON g.assessment_id = a.id AND g.face_id = f.id
                   WHERE cs.classroom_id = ? AND cs.active = 1 AND f.active = 1
                     AND (? IS NULL OR f.id = ?)
                   ORDER BY f.full_name, s.name, a.assessment_date""",
                (school_id, classroom_id, face_id, face_id),
            ).fetchall()

        students: dict[str, dict[str, Any]] = {}
        for row in rows:
            student = students.setdefault(row["face_id"], {
                "face_id": row["face_id"], "full_name": row["full_name"], "subjects": {},
            })
            if row["assessment_id"] is None:
                continue
            subject = student["subjects"].setdefault(row["subject_id"], {
                "subject_id": row["subject_id"], "subject_name": row["subject_name"],
                "assessments": [], "average": None,
            })
            subject["assessments"].append({
                "assessment_id": row["assessment_id"], "title": row["title"],
                "assessment_date": row["assessment_date"], "max_score": row["max_score"],
                "score": row["score"],
            })

        result = []
        for student in students.values():
            subjects = list(student["subjects"].values())
            for subject in subjects:
                scored = [a["score"] for a in subject["assessments"] if a["score"] is not None]
                subject["average"] = round(sum(scored) / len(scored), 2) if scored else None
            result.append({
                "face_id": student["face_id"], "full_name": student["full_name"], "subjects": subjects,
            })
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
        return data
