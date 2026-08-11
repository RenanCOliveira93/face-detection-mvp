from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone

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


    def test_presence_event_tracks_webhook_audit(self) -> None:
        event_id = self.db.create_presence_event("stu-1", "entrada", 0.22)
        self.db.update_presence_event_webhook(
            event_id=event_id,
            webhook_ok=True,
            webhook_status=202,
            webhook_info="accepted",
        )

        events = self.db.get_presence_events(limit=5)
        self.assertEqual(events[0]["webhook_ok"], 1)
        self.assertEqual(events[0]["webhook_status"], 202)
        self.assertEqual(events[0]["webhook_info"], "accepted")
        self.assertIsNotNone(events[0]["webhook_sent_at"])

    def test_migrations_set_user_version(self) -> None:
        with self.db._connect() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        self.assertGreaterEqual(version, 10)
        with self.db._connect() as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"schools", "devices", "sync_outbox", "dietary_restrictions"} <= tables)

    def test_school_classroom_count_and_teacher_notes_are_tenant_scoped(self) -> None:
        school = self.db.create_school("Escola Teste", "escola-teste", "school-secret")
        other = self.db.create_school("Outra Escola", "outra-escola", "other-secret")
        member_id = self.db.add_school_member(
            school["id"], "Professora Ana", "ana@example.com", "professor"
        )["id"]
        classroom_id = self.db.create_classroom(school["id"], "5º A", "2026")
        self.assertTrue(self.db.assign_face_to_school("stu-1", school["id"]))
        self.db.enroll_student(school["id"], classroom_id, "stu-1")
        note_id = self.db.add_teacher_note(
            school["id"], member_id, "Participou bem da atividade", face_id="stu-1"
        )

        dashboard = self.db.get_school_dashboard(school["id"])
        other_dashboard = self.db.get_school_dashboard(other["id"])
        self.assertEqual(dashboard["students"], 1)
        self.assertEqual(dashboard["classrooms"][0]["student_count"], 1)
        self.assertEqual(dashboard["notes"][0]["id"], note_id)
        self.assertEqual(other_dashboard["students"], 0)
        self.assertEqual(other_dashboard["notes"], [])

    def test_cross_school_enrollment_is_rejected(self) -> None:
        school = self.db.create_school("Escola A", "escola-a")
        other = self.db.create_school("Escola B", "escola-b")
        classroom_id = self.db.create_classroom(other["id"], "1º B", "2026")
        self.db.assign_face_to_school("stu-1", school["id"])
        with self.assertRaises(ValueError):
            self.db.enroll_student(other["id"], classroom_id, "stu-1")

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
    def test_resolves_recipient_by_relationship_priority(self) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.db._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO guardians (full_name, created_at, active) VALUES (?, ?, 1)",
                ("Mãe do Aluno", now_iso),
            )
            mother_id = int(cursor.lastrowid)
            conn.execute(
                """
                INSERT INTO guardian_phones (guardian_id, phone_e164, is_primary, channel, active, created_at)
                VALUES (?, ?, 1, 'whatsapp', 1, ?)
                """,
                (mother_id, "5511888888888", now_iso),
            )
            conn.execute(
                """
                INSERT INTO student_guardians (
                    face_id, guardian_id, relationship_type, contact_priority,
                    valid_from, valid_to, active, created_at
                ) VALUES (?, ?, 'mãe', 0, ?, NULL, 1, ?)
                """,
                ("stu-1", mother_id, now_iso, now_iso),
            )
            conn.commit()

        recipient = self.db.get_preferred_notification_recipient("stu-1", channel="whatsapp")
        self.assertIsNotNone(recipient)
        self.assertEqual(recipient["phone"], "5511888888888")
        self.assertEqual(recipient["relationship_type"], "mãe")

    def test_fallback_to_faces_phone_when_no_guardian(self) -> None:
        with self.db._connect() as conn:
            conn.execute("DELETE FROM student_guardians WHERE face_id = ?", ("stu-1",))
            conn.commit()

        recipient = self.db.get_preferred_notification_recipient("stu-1", channel="whatsapp")
        self.assertIsNotNone(recipient)
        self.assertEqual(recipient["phone"], "5511999999999")
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
