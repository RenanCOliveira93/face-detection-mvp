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


if __name__ == "__main__":
    unittest.main()
