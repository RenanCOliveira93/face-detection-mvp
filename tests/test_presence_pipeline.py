from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from database import FaceDatabase


class PresencePipelineIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "faces.db")
        self.db = FaceDatabase(self.db_path)
        self.db.add_face(
            face_id="stu-1",
            full_name="Aluno Teste",
            phone="5511999999999",
            encoding=[0.1, 0.2],
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_presence_event_persists_and_tracks_message_audit(self) -> None:
        event_id = self.db.create_presence_event("stu-1", "entrada", 0.12)
        self.db.update_presence_event_message(event_id, True, "ok")

        events = self.db.get_presence_events(limit=5)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["direction"], "entrada")
        self.assertEqual(events[0]["message_ok"], 1)
        self.assertEqual(events[0]["message_info"], "ok")
        self.assertIsNotNone(events[0]["message_sent_at"])

    def test_presence_supports_entry_and_exit_for_same_face(self) -> None:
        first = self.db.create_presence_event("stu-1", "entrada", 0.09)
        second = self.db.create_presence_event("stu-1", "saida", None)
        self.db.update_presence_event_message(first, False, "cooldown")
        self.db.update_presence_event_message(second, True, "ok")

        events = self.db.get_presence_events(limit=10)
        directions = [event["direction"] for event in events]
        self.assertIn("entrada", directions)
        self.assertIn("saida", directions)

    def test_migrations_set_user_version(self) -> None:
        with self.db._connect() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, 5)

    def test_daily_attendance_same_day_entry_and_exit(self) -> None:
        self.db.create_presence_event(
            "stu-1",
            "entrada",
            0.10,
            event_at="2026-04-01T13:00:00+00:00",
        )
        self.db.create_presence_event(
            "stu-1",
            "saida",
            None,
            event_at="2026-04-01T18:00:00+00:00",
        )

        attendance = self.db.get_daily_attendance(limit=10)
        self.assertEqual(len(attendance), 1)
        item = attendance[0]
        self.assertEqual(item["attendance_date"], "2026-04-01")
        self.assertEqual(item["first_entry_at"], "2026-04-01T13:00:00+00:00")
        self.assertEqual(item["last_exit_at"], "2026-04-01T18:00:00+00:00")
        self.assertEqual(item["status"], "ausente")
        self.assertEqual(item["total_transitions"], 2)

    def test_daily_attendance_crosses_midnight_with_timezone_projection(self) -> None:
        db = FaceDatabase(self.db_path, attendance_timezone="America/Sao_Paulo")
        db.create_presence_event(
            "stu-1",
            "entrada",
            0.11,
            event_at="2026-04-02T02:50:00+00:00",
        )
        db.create_presence_event(
            "stu-1",
            "saida",
            None,
            event_at="2026-04-02T03:10:00+00:00",
        )

        attendance = db.get_daily_attendance(limit=10)
        self.assertEqual(len(attendance), 1)
        item = attendance[0]
        self.assertEqual(item["attendance_date"], "2026-04-01")
        self.assertEqual(item["status"], "ausente")
        self.assertEqual(item["total_transitions"], 2)

    def test_daily_attendance_exit_without_entry_and_double_entry_are_inconsistent(self) -> None:
        self.db.create_presence_event(
            "stu-1",
            "saida",
            None,
            event_at="2026-04-01T09:00:00+00:00",
        )
        self.db.create_presence_event(
            "stu-1",
            "entrada",
            0.10,
            event_at="2026-04-02T09:00:00+00:00",
        )
        self.db.create_presence_event(
            "stu-1",
            "entrada",
            0.09,
            event_at="2026-04-02T09:05:00+00:00",
        )

        attendance = self.db.get_daily_attendance(limit=10)
        by_date = {item["attendance_date"]: item for item in attendance}
        self.assertEqual(by_date["2026-04-01"]["status"], "inconsistente")
        self.assertEqual(by_date["2026-04-01"]["total_transitions"], 1)
        self.assertEqual(by_date["2026-04-02"]["status"], "inconsistente")
        self.assertEqual(by_date["2026-04-02"]["total_transitions"], 2)


if __name__ == "__main__":
    unittest.main()
