from __future__ import annotations

import tempfile
import unittest
import sys
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

if importlib.util.find_spec("flask") is None:
    raise unittest.SkipTest("Flask não instalado neste ambiente")
sys.modules.setdefault("cv2", MagicMock())
sys.modules.setdefault("face_recognition", MagicMock())
import main
from database import FaceDatabase


class ApiSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db = main.db
        main.db = FaceDatabase(str(Path(self.tmp.name) / "api.db"))
        main.CONFIG["admin_bootstrap_token"] = "bootstrap-test"
        self.client = main.app.test_client()
        response = self.client.post(
            "/api/schools",
            headers={"X-Bootstrap-Token": "bootstrap-test"},
            json={"name": "Escola A", "slug": "escola-a", "admin_name": "Admin", "admin_email": "a@test"},
        )
        self.school = response.get_json()["school"]
        self.admin_key = response.get_json()["admin"]["api_key"]
        professor = main.db.add_school_member(
            self.school["id"], "Prof", "p@test", "professor", "prof-key"
        )
        self.professor_id = professor["id"]

    def tearDown(self) -> None:
        main.db = self.original_db
        self.tmp.cleanup()

    def test_bootstrap_and_admin_routes_require_valid_credentials_and_role(self) -> None:
        self.assertEqual(self.client.post("/api/schools", json={}).status_code, 401)
        self.assertEqual(self.client.post("/api/classes", json={"name": "A"}).status_code, 401)
        self.assertEqual(
            self.client.post("/api/classes", headers={"X-School-Key": "invalid"}, json={"name": "A"}).status_code,
            401,
        )
        self.assertEqual(
            self.client.post("/api/classes", headers={"X-School-Key": "prof-key"}, json={"name": "A"}).status_code,
            403,
        )
        self.assertEqual(
            self.client.post("/api/classes", headers={"X-School-Key": self.admin_key}, json={"name": "A"}).status_code,
            201,
        )

    def test_professor_can_write_own_note_but_not_administer(self) -> None:
        main.db.add_face("student", "Aluno", "", school_id=self.school["id"])
        response = self.client.post(
            "/api/school/notes", headers={"X-School-Key": "prof-key"},
            json={"face_id": "student", "body": "Evoluiu"},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            self.client.post(
                "/api/dietary-restrictions", headers={"X-School-Key": "prof-key"},
                json={"face_id": "student", "description": "Lactose"},
            ).status_code,
            403,
        )

    def test_control_id_rejects_invalid_and_deduplicates_valid_callback(self) -> None:
        main.db.add_face("student", "Aluno", "", school_id=self.school["id"])
        main.db.create_device(self.school["id"], "gate", "Portaria", "secret")
        payload = {"event_id": "evt-1", "user_id": "student", "event_at": "2026-08-11T12:00:00+00:00"}
        self.assertEqual(self.client.post("/new_user_identified.fcgi", json=payload).status_code, 401)
        headers = {"X-Device-Id": "gate", "X-Device-Secret": "secret"}
        first = self.client.post("/new_user_identified.fcgi", headers=headers, json=payload)
        duplicate = self.client.post("/new_user_identified.fcgi", headers=headers, json=payload)
        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.get_json()["duplicate"])
        self.assertTrue(duplicate.get_json()["duplicate"])

    def test_registration_cannot_take_student_from_another_school(self) -> None:
        other = main.db.create_school("Escola B", "escola-b", "school-b")
        main.db.add_face("shared-id", "Aluno B", "", school_id=other["id"])

        response = self.client.post(
            "/api/register",
            headers={"X-School-Key": self.admin_key},
            data={"id": "shared-id", "name": "Tentativa", "phone": "+5511900000000"},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(main.db.get_face("shared-id")["school_id"], other["id"])

    def test_attendance_book_requires_auth_and_includes_absent_students(self) -> None:
        main.db.add_face("present", "Aluno Presente", "", school_id=self.school["id"])
        main.db.add_face("absent", "Aluno Ausente", "", school_id=self.school["id"])
        classroom = main.db.create_classroom(self.school["id"], "1 A", "2026")
        main.db.enroll_student(self.school["id"], classroom, "present")
        main.db.enroll_student(self.school["id"], classroom, "absent")
        main.db.create_presence_event("present", "entrada", 0.9, "2026-08-13T12:00:00+00:00")

        self.assertEqual(self.client.get("/api/attendance-book").status_code, 401)
        response = self.client.get(
            "/api/attendance-book?date=2026-08-13",
            headers={"X-School-Key": self.admin_key},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["total_students"], 2)
        self.assertEqual({item["status"] for item in payload["items"]}, {"presente", "ausente"})

    def test_video_feed_requires_school_key_via_header_or_query_param(self) -> None:
        self.assertEqual(self.client.get("/video_feed").status_code, 401)
        self.assertEqual(self.client.get("/video_feed?key=invalid").status_code, 401)

        # A rota abre a câmera de verdade quando autorizada; o corpo streaming não
        # importa aqui, só a checagem de credencial feita antes de gerar frames.
        empty_capture = MagicMock()
        empty_capture.read.return_value = (False, None)
        with patch("main.cv2.VideoCapture", return_value=empty_capture):
            self.assertEqual(
                self.client.get("/video_feed", headers={"X-School-Key": "prof-key"}).status_code, 200
            )
            self.assertEqual(
                self.client.get(f"/video_feed?key={self.admin_key}").status_code, 200
            )

    def test_login_establishes_session_that_authenticates_subsequent_requests(self) -> None:
        self.assertEqual(self.client.post("/api/auth/login", json={"api_key": "invalid"}).status_code, 401)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)

        login = self.client.post("/api/auth/login", json={"api_key": "prof-key"})
        self.assertEqual(login.status_code, 200)
        self.assertEqual(login.get_json()["role"], "professor")

        # Sem enviar X-School-Key: a sessão (cookie) já autentica.
        me = self.client.get("/api/auth/me")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.get_json()["role"], "professor")

        events = self.client.get("/api/presence_events")
        self.assertEqual(events.status_code, 200)

        self.assertEqual(self.client.post("/api/auth/logout").status_code, 204)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)
        self.assertEqual(self.client.get("/api/presence_events").status_code, 401)

    def test_session_takes_priority_but_header_still_works_without_login(self) -> None:
        # Fluxo programático/dispositivo continua funcionando sem nenhuma sessão aberta.
        response = self.client.get("/api/presence_events", headers={"X-School-Key": self.admin_key})
        self.assertEqual(response.status_code, 200)

    def test_control_id_callback_publishes_webhook_only_for_new_events(self) -> None:
        main.db.add_face("student", "Aluno", "", school_id=self.school["id"])
        main.db.create_device(self.school["id"], "gate", "Portaria", "secret")
        headers = {"X-Device-Id": "gate", "X-Device-Secret": "secret"}
        payload = {"event_id": "evt-1", "user_id": "student", "event_at": "2026-08-11T12:00:00+00:00"}

        with patch("main.publish_presence_event") as mock_publish:
            mock_publish.return_value = {"ok": True, "status": 200, "info": "ok", "sent_at": "now"}
            first = self.client.post("/new_user_identified.fcgi", headers=headers, json=payload)
            duplicate = self.client.post("/new_user_identified.fcgi", headers=headers, json=payload)

        self.assertEqual(first.status_code, 200)
        self.assertTrue(duplicate.get_json()["duplicate"])
        self.assertEqual(mock_publish.call_count, 1)


if __name__ == "__main__":
    unittest.main()
