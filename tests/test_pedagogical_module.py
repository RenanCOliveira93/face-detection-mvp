from __future__ import annotations

import tempfile
import unittest
import sys
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

from database import FaceDatabase

if importlib.util.find_spec("flask") is None:
    raise unittest.SkipTest("Flask não instalado neste ambiente")
sys.modules.setdefault("cv2", MagicMock())
sys.modules.setdefault("face_recognition", MagicMock())
import main


class PedagogicalDatabaseTests(unittest.TestCase):
    """Testa o módulo pedagógico direto na camada de dados (sem HTTP)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = FaceDatabase(str(Path(self.tmp.name) / "poc.db"))
        self.school = self.db.create_school("Escola A", "escola-a")
        self.classroom_id = self.db.create_classroom(self.school["id"], "5º A", "2026")
        self.subject = self.db.create_subject(self.school["id"], "Matemática")
        self.teacher = self.db.add_school_member(
            self.school["id"], "Prof. Ana", "ana@example.test", "professor"
        )
        self.db.add_face("stu-1", "Aluno 1", "", school_id=self.school["id"])
        self.db.enroll_student(self.school["id"], self.classroom_id, "stu-1")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_create_subject_and_list(self) -> None:
        subjects = self.db.list_subjects(self.school["id"])
        self.assertEqual([s["name"] for s in subjects], ["Matemática"])

    def test_teacher_assignment_swap_deactivates_previous(self) -> None:
        self.db.create_teacher_assignment(
            self.school["id"], self.teacher["id"], self.classroom_id, self.subject["id"]
        )
        other_teacher = self.db.add_school_member(
            self.school["id"], "Prof. Beto", "beto@example.test", "professor"
        )
        self.db.create_teacher_assignment(
            self.school["id"], other_teacher["id"], self.classroom_id, self.subject["id"]
        )

        self.assertFalse(
            self.db.is_teacher_assigned(self.school["id"], self.teacher["id"], self.classroom_id, self.subject["id"])
        )
        self.assertTrue(
            self.db.is_teacher_assigned(self.school["id"], other_teacher["id"], self.classroom_id, self.subject["id"])
        )
        assignments = self.db.list_teacher_assignments(self.school["id"])
        self.assertEqual(len(assignments), 1)
        self.assertEqual(assignments[0]["member_id"], other_teacher["id"])

    def test_open_lesson_prefills_attendance_from_daily_attendance(self) -> None:
        self.db.create_presence_event("stu-1", "entrada", 0.9, event_at="2026-08-11T12:00:00+00:00")
        lesson = self.db.open_lesson(
            self.school["id"], self.classroom_id, self.subject["id"], self.teacher["id"], "2026-08-11"
        )
        self.assertEqual(len(lesson["attendance"]), 1)
        self.assertEqual(lesson["attendance"][0]["face_id"], "stu-1")
        self.assertEqual(lesson["attendance"][0]["status"], "presente")
        self.assertEqual(lesson["attendance"][0]["source"], "auto")
        self.assertFalse(lesson["confirmed"])

    def test_open_lesson_defaults_to_ausente_without_daily_attendance(self) -> None:
        lesson = self.db.open_lesson(
            self.school["id"], self.classroom_id, self.subject["id"], self.teacher["id"], "2026-08-12"
        )
        self.assertEqual(lesson["attendance"][0]["status"], "ausente")

    def test_reopening_lesson_does_not_duplicate_or_reset_manual_correction(self) -> None:
        first = self.db.open_lesson(
            self.school["id"], self.classroom_id, self.subject["id"], self.teacher["id"], "2026-08-11"
        )
        self.db.update_lesson_attendance(
            self.school["id"], first["id"], "stu-1", "justificado", self.teacher["id"]
        )
        second = self.db.open_lesson(
            self.school["id"], self.classroom_id, self.subject["id"], self.teacher["id"], "2026-08-11"
        )
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(len(second["attendance"]), 1)
        self.assertEqual(second["attendance"][0]["status"], "justificado")
        self.assertEqual(second["attendance"][0]["source"], "manual")

    def test_confirmed_lesson_locks_further_attendance_edits(self) -> None:
        lesson = self.db.open_lesson(
            self.school["id"], self.classroom_id, self.subject["id"], self.teacher["id"], "2026-08-11"
        )
        self.db.confirm_lesson(self.school["id"], lesson["id"], self.teacher["id"])

        with self.assertRaises(ValueError):
            self.db.update_lesson_attendance(
                self.school["id"], lesson["id"], "stu-1", "ausente", self.teacher["id"]
            )
        with self.assertRaises(ValueError):
            self.db.confirm_lesson(self.school["id"], lesson["id"], self.teacher["id"])

        refreshed = self.db.get_lesson(self.school["id"], lesson["id"])
        self.assertTrue(refreshed["confirmed"])
        self.assertEqual(refreshed["confirmed_by_member_id"], self.teacher["id"])

    def test_update_lesson_content(self) -> None:
        lesson = self.db.open_lesson(
            self.school["id"], self.classroom_id, self.subject["id"], self.teacher["id"], "2026-08-11"
        )
        self.db.update_lesson_content(self.school["id"], lesson["id"], "Frações e números decimais")
        refreshed = self.db.get_lesson(self.school["id"], lesson["id"])
        self.assertEqual(refreshed["content"], "Frações e números decimais")

    def test_list_classrooms_and_classroom_students(self) -> None:
        classrooms = self.db.list_classrooms(self.school["id"])
        self.assertEqual([c["name"] for c in classrooms], ["5º A"])

        roster = self.db.list_classroom_students(self.school["id"], self.classroom_id)
        self.assertEqual([s["face_id"] for s in roster], ["stu-1"])

        other_school = self.db.create_school("Escola B", "escola-b")
        with self.assertRaises(ValueError):
            self.db.list_classroom_students(other_school["id"], self.classroom_id)

    def test_grades_enforce_max_score_and_school_scope(self) -> None:
        assessment = self.db.create_assessment(
            self.school["id"], self.classroom_id, self.subject["id"], "Prova 1", "2026-08-20", 10,
            self.teacher["id"],
        )
        self.db.upsert_grade(self.school["id"], assessment["id"], "stu-1", 8.5, self.teacher["id"])
        grades = self.db.list_grades(self.school["id"], assessment["id"])
        self.assertEqual(grades[0]["score"], 8.5)

        # Atualiza a mesma nota (upsert)
        self.db.upsert_grade(self.school["id"], assessment["id"], "stu-1", 9.0, self.teacher["id"])
        grades = self.db.list_grades(self.school["id"], assessment["id"])
        self.assertEqual(grades[0]["score"], 9.0)

        with self.assertRaises(ValueError):
            self.db.upsert_grade(self.school["id"], assessment["id"], "stu-1", 11, self.teacher["id"])

        other_school = self.db.create_school("Escola B", "escola-b")
        with self.assertRaises(ValueError):
            self.db.list_grades(other_school["id"], assessment["id"])

    def test_report_card_averages_across_multiple_assessments_and_includes_ungraded_students(self) -> None:
        self.db.add_face("stu-2", "Aluno 2", "", school_id=self.school["id"])
        self.db.enroll_student(self.school["id"], self.classroom_id, "stu-2")

        history = self.db.create_subject(self.school["id"], "História")
        assessment_1 = self.db.create_assessment(
            self.school["id"], self.classroom_id, self.subject["id"], "Prova 1", "2026-08-01", 10
        )
        assessment_2 = self.db.create_assessment(
            self.school["id"], self.classroom_id, self.subject["id"], "Prova 2", "2026-08-15", 10
        )
        history_assessment = self.db.create_assessment(
            self.school["id"], self.classroom_id, history["id"], "Prova 1", "2026-08-10", 10
        )
        self.db.upsert_grade(self.school["id"], assessment_1["id"], "stu-1", 8.0)
        self.db.upsert_grade(self.school["id"], assessment_2["id"], "stu-1", 6.0)
        self.db.upsert_grade(self.school["id"], history_assessment["id"], "stu-1", 9.0)
        # stu-2 não tem nenhuma nota lançada ainda, mas deve continuar aparecendo no boletim
        # — com as avaliações da turma listadas e nota None (ainda não corrigida).

        report = self.db.get_report_card(self.school["id"], self.classroom_id)
        by_student = {item["face_id"]: item for item in report}

        self.assertEqual(set(by_student), {"stu-1", "stu-2"})

        stu1_subjects = {s["subject_name"]: s for s in by_student["stu-1"]["subjects"]}
        self.assertEqual(stu1_subjects["Matemática"]["average"], 7.0)
        self.assertEqual(len(stu1_subjects["Matemática"]["assessments"]), 2)
        self.assertEqual(stu1_subjects["História"]["average"], 9.0)

        stu2_subjects = {s["subject_name"]: s for s in by_student["stu-2"]["subjects"]}
        self.assertIsNone(stu2_subjects["Matemática"]["average"])
        self.assertIsNone(stu2_subjects["História"]["average"])
        self.assertTrue(all(a["score"] is None for a in stu2_subjects["Matemática"]["assessments"]))

        filtered = self.db.get_report_card(self.school["id"], self.classroom_id, face_id="stu-1")
        self.assertEqual([item["face_id"] for item in filtered], ["stu-1"])

    def test_report_card_rejects_classroom_from_other_school(self) -> None:
        other_school = self.db.create_school("Escola B", "escola-b")
        with self.assertRaises(ValueError):
            self.db.get_report_card(other_school["id"], self.classroom_id)

    def test_update_and_deactivate_subject(self) -> None:
        self.db.update_subject(self.school["id"], self.subject["id"], "Matemática Avançada")
        self.assertEqual(self.db.list_subjects(self.school["id"])[0]["name"], "Matemática Avançada")

        self.db.create_teacher_assignment(
            self.school["id"], self.teacher["id"], self.classroom_id, self.subject["id"]
        )
        self.db.deactivate_subject(self.school["id"], self.subject["id"])
        self.assertEqual(self.db.list_subjects(self.school["id"]), [])
        self.assertFalse(
            self.db.is_teacher_assigned(self.school["id"], self.teacher["id"], self.classroom_id, self.subject["id"])
        )
        with self.assertRaises(ValueError):
            self.db.update_subject(self.school["id"], self.subject["id"], "Outro nome")

    def test_update_and_deactivate_classroom(self) -> None:
        self.db.update_classroom(self.school["id"], self.classroom_id, "5º B", "2027")
        self.assertEqual(self.db.list_classrooms(self.school["id"])[0]["name"], "5º B")

        self.db.create_teacher_assignment(
            self.school["id"], self.teacher["id"], self.classroom_id, self.subject["id"]
        )
        self.db.deactivate_classroom(self.school["id"], self.classroom_id)
        self.assertEqual(self.db.list_classrooms(self.school["id"]), [])
        self.assertFalse(
            self.db.is_teacher_assigned(self.school["id"], self.teacher["id"], self.classroom_id, self.subject["id"])
        )
        with self.assertRaises(ValueError):
            self.db.update_classroom(self.school["id"], self.classroom_id, "Outro nome")


class PedagogicalApiTests(unittest.TestCase):
    """Testa as rotas Flask, incluindo o isolamento professor x turma/disciplina."""

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

        self.assigned_teacher = main.db.add_school_member(
            self.school["id"], "Prof. Ana", "ana@test", "professor", "ana-key"
        )
        self.other_teacher = main.db.add_school_member(
            self.school["id"], "Prof. Beto", "beto@test", "professor", "beto-key"
        )
        self.classroom_id = main.db.create_classroom(self.school["id"], "5º A", "2026")
        self.subject = main.db.create_subject(self.school["id"], "Matemática")
        main.db.create_teacher_assignment(
            self.school["id"], self.assigned_teacher["id"], self.classroom_id, self.subject["id"]
        )
        main.db.add_face("stu-1", "Aluno 1", "", school_id=self.school["id"])
        main.db.enroll_student(self.school["id"], self.classroom_id, "stu-1")

    def tearDown(self) -> None:
        main.db = self.original_db
        self.tmp.cleanup()

    def test_unassigned_teacher_cannot_open_lesson(self) -> None:
        response = self.client.post(
            "/api/lessons",
            headers={"X-School-Key": "beto-key"},
            json={"classroom_id": self.classroom_id, "subject_id": self.subject["id"], "lesson_date": "2026-08-11"},
        )
        self.assertEqual(response.status_code, 403)

    def test_assigned_teacher_can_open_and_confirm_lesson(self) -> None:
        open_response = self.client.post(
            "/api/lessons",
            headers={"X-School-Key": "ana-key"},
            json={"classroom_id": self.classroom_id, "subject_id": self.subject["id"], "lesson_date": "2026-08-11"},
        )
        self.assertEqual(open_response.status_code, 201)
        lesson = open_response.get_json()
        self.assertEqual(len(lesson["attendance"]), 1)

        correction = self.client.put(
            f"/api/lessons/{lesson['id']}/attendance/stu-1",
            headers={"X-School-Key": "ana-key"},
            json={"status": "presente"},
        )
        self.assertEqual(correction.status_code, 200)

        content = self.client.put(
            f"/api/lessons/{lesson['id']}/content",
            headers={"X-School-Key": "ana-key"},
            json={"content": "Introdução a frações"},
        )
        self.assertEqual(content.status_code, 200)

        confirm = self.client.post(
            f"/api/lessons/{lesson['id']}/confirm", headers={"X-School-Key": "ana-key"},
        )
        self.assertEqual(confirm.status_code, 200)

        locked = self.client.put(
            f"/api/lessons/{lesson['id']}/attendance/stu-1",
            headers={"X-School-Key": "ana-key"},
            json={"status": "ausente"},
        )
        self.assertEqual(locked.status_code, 400)

    def test_other_teacher_cannot_view_or_edit_colleague_lesson(self) -> None:
        open_response = self.client.post(
            "/api/lessons",
            headers={"X-School-Key": "ana-key"},
            json={"classroom_id": self.classroom_id, "subject_id": self.subject["id"], "lesson_date": "2026-08-11"},
        )
        lesson_id = open_response.get_json()["id"]

        self.assertEqual(
            self.client.get(f"/api/lessons/{lesson_id}", headers={"X-School-Key": "beto-key"}).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                f"/api/lessons/{lesson_id}/confirm", headers={"X-School-Key": "beto-key"}
            ).status_code,
            403,
        )

    def test_teacher_swap_blocks_previous_teacher_from_new_lessons(self) -> None:
        main.db.create_teacher_assignment(
            self.school["id"], self.other_teacher["id"], self.classroom_id, self.subject["id"]
        )
        response = self.client.post(
            "/api/lessons",
            headers={"X-School-Key": "ana-key"},
            json={"classroom_id": self.classroom_id, "subject_id": self.subject["id"], "lesson_date": "2026-08-12"},
        )
        self.assertEqual(response.status_code, 403)

        response = self.client.post(
            "/api/lessons",
            headers={"X-School-Key": "beto-key"},
            json={"classroom_id": self.classroom_id, "subject_id": self.subject["id"], "lesson_date": "2026-08-12"},
        )
        self.assertEqual(response.status_code, 201)

    def test_assessments_and_grades_flow_with_permission_checks(self) -> None:
        create = self.client.post(
            "/api/assessments",
            headers={"X-School-Key": "ana-key"},
            json={
                "classroom_id": self.classroom_id, "subject_id": self.subject["id"],
                "title": "Prova 1", "assessment_date": "2026-08-20", "max_score": 10,
            },
        )
        self.assertEqual(create.status_code, 201)
        assessment_id = create.get_json()["id"]

        forbidden = self.client.post(
            "/api/assessments",
            headers={"X-School-Key": "beto-key"},
            json={
                "classroom_id": self.classroom_id, "subject_id": self.subject["id"],
                "title": "Prova 2", "assessment_date": "2026-08-21",
            },
        )
        self.assertEqual(forbidden.status_code, 403)

        grade = self.client.post(
            f"/api/assessments/{assessment_id}/grades",
            headers={"X-School-Key": "ana-key"},
            json={"face_id": "stu-1", "score": 9.5},
        )
        self.assertEqual(grade.status_code, 201)

        listing = self.client.get(
            f"/api/assessments/{assessment_id}/grades", headers={"X-School-Key": "ana-key"}
        )
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.get_json()["items"][0]["score"], 9.5)

    def test_professor_can_list_but_not_create_classrooms(self) -> None:
        listing = self.client.get("/api/school/classrooms", headers={"X-School-Key": "ana-key"})
        self.assertEqual(listing.status_code, 200)
        self.assertEqual({c["id"] for c in listing.get_json()["items"]}, {self.classroom_id})

        forbidden = self.client.post(
            "/api/school/classrooms", headers={"X-School-Key": "ana-key"}, json={"name": "6º B"},
        )
        self.assertEqual(forbidden.status_code, 403)

        allowed = self.client.post(
            "/api/school/classrooms", headers={"X-School-Key": self.admin_key}, json={"name": "6º B"},
        )
        self.assertEqual(allowed.status_code, 201)

    def test_professor_can_list_classroom_roster_for_grading(self) -> None:
        response = self.client.get(
            f"/api/school/classrooms/{self.classroom_id}/students", headers={"X-School-Key": "ana-key"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["items"], [{"face_id": "stu-1", "full_name": "Aluno 1"}])

    def test_admin_bypasses_assignment_checks(self) -> None:
        response = self.client.post(
            "/api/lessons",
            headers={"X-School-Key": self.admin_key},
            json={
                "classroom_id": self.classroom_id, "subject_id": self.subject["id"],
                "lesson_date": "2026-08-11", "teacher_member_id": self.other_teacher["id"],
            },
        )
        self.assertEqual(response.status_code, 201)

    def test_report_card_endpoint_requires_classroom_and_respects_school_scope(self) -> None:
        assessment = self.client.post(
            "/api/assessments",
            headers={"X-School-Key": "ana-key"},
            json={
                "classroom_id": self.classroom_id, "subject_id": self.subject["id"],
                "title": "Prova 1", "assessment_date": "2026-08-20", "max_score": 10,
            },
        ).get_json()
        self.client.post(
            f"/api/assessments/{assessment['id']}/grades",
            headers={"X-School-Key": "ana-key"},
            json={"face_id": "stu-1", "score": 7.5},
        )

        missing_param = self.client.get("/api/report-card", headers={"X-School-Key": "ana-key"})
        self.assertEqual(missing_param.status_code, 400)

        response = self.client.get(
            f"/api/report-card?classroom_id={self.classroom_id}", headers={"X-School-Key": "ana-key"},
        )
        self.assertEqual(response.status_code, 200)
        items = response.get_json()["items"]
        self.assertEqual(items[0]["subjects"][0]["average"], 7.5)

    def test_subject_and_classroom_update_and_deactivate_admin_only(self) -> None:
        forbidden = self.client.put(
            f"/api/subjects/{self.subject['id']}", headers={"X-School-Key": "ana-key"}, json={"name": "X"},
        )
        self.assertEqual(forbidden.status_code, 403)

        renamed = self.client.put(
            f"/api/subjects/{self.subject['id']}",
            headers={"X-School-Key": self.admin_key}, json={"name": "Matemática Avançada"},
        )
        self.assertEqual(renamed.status_code, 200)

        deactivated = self.client.delete(
            f"/api/subjects/{self.subject['id']}", headers={"X-School-Key": self.admin_key},
        )
        self.assertEqual(deactivated.status_code, 204)
        listing = self.client.get("/api/subjects", headers={"X-School-Key": self.admin_key})
        self.assertEqual(listing.get_json()["items"], [])

        classroom_forbidden = self.client.put(
            f"/api/school/classrooms/{self.classroom_id}",
            headers={"X-School-Key": "ana-key"}, json={"name": "X"},
        )
        self.assertEqual(classroom_forbidden.status_code, 403)

        classroom_deactivated = self.client.delete(
            f"/api/school/classrooms/{self.classroom_id}", headers={"X-School-Key": self.admin_key},
        )
        self.assertEqual(classroom_deactivated.status_code, 204)
        classrooms = self.client.get("/api/school/classrooms", headers={"X-School-Key": self.admin_key})
        self.assertEqual(classrooms.get_json()["items"], [])


if __name__ == "__main__":
    unittest.main()
