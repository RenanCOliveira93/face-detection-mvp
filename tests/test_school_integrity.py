from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from database import FaceDatabase


class SchoolIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "poc.db")
        self.db = FaceDatabase(self.path)
        self.a = self.db.create_school("A", "a", "key-a", "America/Sao_Paulo")
        self.b = self.db.create_school("B", "b", "key-b")
        self.db.add_face("a1", "Aluno A", "", school_id=self.a["id"])
        self.db.add_face("b1", "Aluno B", "", school_id=self.b["id"])

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_new_database_and_migration_are_idempotent(self) -> None:
        FaceDatabase(self.path)
        with self.db._connect() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            trigger = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='reject_cross_school_enrollment'"
            ).fetchone()
        self.assertEqual(version, FaceDatabase.SCHEMA_VERSION)
        self.assertIsNotNone(trigger)

    def test_secrets_are_hashed_and_revocation_works(self) -> None:
        with self.db._connect() as conn:
            stored = conn.execute("SELECT api_key,api_key_hash FROM schools WHERE id=?", (self.a["id"],)).fetchone()
        self.assertNotEqual(stored["api_key"], "key-a")
        self.assertEqual(self.db.get_school_by_api_key("key-a")["id"], self.a["id"])
        member = self.db.add_school_member(self.a["id"], "Admin", "a@example.test", "school_admin", "member-key")
        self.assertEqual(self.db.authenticate_principal("member-key")["role"], "school_admin")
        self.assertTrue(self.db.revoke_member(self.a["id"], member["id"]))
        self.assertIsNone(self.db.authenticate_principal("member-key"))

    def test_database_rejects_cross_school_associations(self) -> None:
        classroom = self.db.create_classroom(self.a["id"], "Turma", "2026")
        with self.assertRaises(ValueError):
            self.db.enroll_student(self.a["id"], classroom, "b1")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.add_dietary_restriction(self.a["id"], "b1", "Alergia")
        member = self.db.add_school_member(self.a["id"], "Prof", "p@example.test", "professor")
        with self.assertRaises(ValueError):
            self.db.add_teacher_note(self.a["id"], member["id"], "Nota", face_id="b1")

    def test_school_queries_never_return_other_tenant_rows(self) -> None:
        self.db.create_presence_event("a1", "entrada", 0.1)
        self.db.create_presence_event("b1", "entrada", 0.1)
        self.assertEqual([f["id"] for f in self.db.list_faces(self.a["id"])], ["a1"])
        self.assertEqual(
            {e["face_id"] for e in self.db.get_presence_events(school_id=self.a["id"])}, {"a1"}
        )
        self.assertEqual(
            {e["face_id"] for e in self.db.get_daily_attendance(school_id=self.a["id"])}, {"a1"}
        )

    def test_device_presence_is_scoped_timezone_aware_and_idempotent(self) -> None:
        self.db.create_device(self.a["id"], "gate-a", "Portaria", "device-secret")
        first = self.db.record_device_presence(
            self.a["id"], "gate-a", "event-1", "a1", "2026-08-11T02:30:00+00:00"
        )
        duplicate = self.db.record_device_presence(
            self.a["id"], "gate-a", "event-1", "a1", "2026-08-11T02:30:01+00:00"
        )
        second = self.db.record_device_presence(
            self.a["id"], "gate-a", "event-2", "a1", "2026-08-11T02:40:00+00:00"
        )
        self.assertEqual(first[1:], ("entrada", False))
        self.assertEqual(duplicate, (first[0], "entrada", True))
        self.assertEqual(second[1], "saida")
        self.assertEqual(len(self.db.pending_outbox()), 2)
        with self.assertRaises(ValueError):
            self.db.record_device_presence(self.a["id"], "gate-a", "event-b", "b1")

    def test_camera_presence_event_enqueues_outbox_when_school_assigned(self) -> None:
        self.db.create_presence_event("a1", "entrada", 0.5)
        self.assertEqual(len(self.db.pending_outbox()), 1)

    def test_erase_student_removes_orphan_guardian_but_keeps_shared_one(self) -> None:
        self.db.add_face("a2", "Aluno A2", "", school_id=self.a["id"])
        with self.db._connect() as conn:
            now_iso = "2026-08-11T12:00:00+00:00"
            cur = conn.execute(
                "INSERT INTO guardians (full_name, created_at, active) VALUES (?, ?, 1)",
                ("Mãe compartilhada", now_iso),
            )
            shared_guardian_id = int(cur.lastrowid)
            conn.execute(
                "INSERT INTO guardian_phones (guardian_id, phone_e164, is_primary, channel, active, created_at) "
                "VALUES (?, ?, 1, 'whatsapp', 1, ?)",
                (shared_guardian_id, "5511888888888", now_iso),
            )
            for face_id in ("a1", "a2"):
                conn.execute(
                    "INSERT INTO student_guardians (face_id, guardian_id, relationship_type, "
                    "contact_priority, valid_from, valid_to, active, created_at) "
                    "VALUES (?, ?, 'mãe', 0, ?, NULL, 1, ?)",
                    (face_id, shared_guardian_id, now_iso, now_iso),
                )
            conn.commit()

        event_id = self.db.create_presence_event("a1", "entrada", 0.9)
        self.db.update_presence_event_message(event_id, True, "Aluno Aluno A chegou na escola.")
        self.db.update_presence_event_webhook(event_id, True, 200, "resposta com dados do aluno")
        self.db.log_detection("a1", similarity=0.9, message_ok=True, message_info="Aluno Aluno A chegou.")

        self.db.erase_student_personal_data(self.a["id"], "a1")

        with self.db._connect() as conn:
            guardian_still_there = conn.execute(
                "SELECT 1 FROM guardians WHERE id=?", (shared_guardian_id,)
            ).fetchone()
            still_linked_to_a2 = conn.execute(
                "SELECT 1 FROM student_guardians WHERE face_id='a2' AND guardian_id=?", (shared_guardian_id,)
            ).fetchone()
            linked_to_a1 = conn.execute(
                "SELECT 1 FROM student_guardians WHERE face_id='a1'"
            ).fetchone()
        self.assertIsNotNone(guardian_still_there, "guardian ainda vinculado a a2 não deve ser removido")
        self.assertIsNotNone(still_linked_to_a2)
        self.assertIsNone(linked_to_a1)

        events = self.db.get_presence_events(school_id=self.a["id"])
        self.assertEqual(events[0]["message_info"], "[dados removidos]")
        self.assertEqual(events[0]["webhook_info"], "[dados removidos]")

        with self.db._connect() as conn:
            face = conn.execute("SELECT full_name, active FROM faces WHERE id='a1'").fetchone()
        self.assertEqual(face["full_name"], "Titular removido")
        self.assertEqual(face["active"], 0)

    def test_erase_student_removes_guardian_left_without_other_links(self) -> None:
        with self.db._connect() as conn:
            now_iso = "2026-08-11T12:00:00+00:00"
            cur = conn.execute(
                "INSERT INTO guardians (full_name, created_at, active) VALUES (?, ?, 1)",
                ("Mãe exclusiva", now_iso),
            )
            guardian_id = int(cur.lastrowid)
            conn.execute(
                "INSERT INTO guardian_phones (guardian_id, phone_e164, is_primary, channel, active, created_at) "
                "VALUES (?, ?, 1, 'whatsapp', 1, ?)",
                (guardian_id, "5511777777777", now_iso),
            )
            conn.execute(
                "INSERT INTO student_guardians (face_id, guardian_id, relationship_type, "
                "contact_priority, valid_from, valid_to, active, created_at) "
                "VALUES ('a1', ?, 'mãe', 0, ?, NULL, 1, ?)",
                (guardian_id, now_iso, now_iso),
            )
            conn.commit()

        self.db.erase_student_personal_data(self.a["id"], "a1")

        with self.db._connect() as conn:
            guardian_row = conn.execute("SELECT 1 FROM guardians WHERE id=?", (guardian_id,)).fetchone()
            phone_row = conn.execute(
                "SELECT 1 FROM guardian_phones WHERE guardian_id=?", (guardian_id,)
            ).fetchone()
        self.assertIsNone(guardian_row)
        self.assertIsNone(phone_row)


if __name__ == "__main__":
    unittest.main()
