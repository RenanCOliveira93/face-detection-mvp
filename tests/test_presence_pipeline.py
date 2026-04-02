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

    def test_try_reserve_message_dispatch_blocks_second_send_same_day(self) -> None:
        first_ok, first_reason = self.db.try_reserve_message_dispatch(
            face_id="stu-1",
            direction="entrada",
            cooldown_seconds=60,
        )
        second_ok, second_reason = self.db.try_reserve_message_dispatch(
            face_id="stu-1",
            direction="entrada",
            cooldown_seconds=60,
        )

        self.assertTrue(first_ok)
        self.assertEqual(first_reason, "reserved")
        self.assertFalse(second_ok)
        self.assertEqual(second_reason, "cooldown")


if __name__ == "__main__":
    unittest.main()
