import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import numpy as np
    from database import FaceDatabase
    from face_registry import FaceRegistry
except ImportError as exc:  # pragma: no cover - dependências verificadas na CI completa
    raise unittest.SkipTest(f"Dependência de reconhecimento ausente: {exc}")


class FaceRegistryTests(unittest.TestCase):
    @patch("face_registry.extract_face_encoding", return_value=np.zeros(128))
    def test_registers_existing_storage_image_directly_in_school(self, _extract) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "face.jpg"
            image.write_bytes(b"fake-image")
            db = FaceDatabase(str(root / "faces.db"))
            school = db.create_school("POC", "poc")
            registry = FaceRegistry(db)

            student = registry.register_face(
                "Aluno Mock", "+5511900000001", str(image), "aluno_mock_01",
                school_id=school["id"], store_image=False,
            )

            self.assertEqual(student["school_id"], school["id"])
            self.assertEqual(Path(student["photo_path"]), image.resolve())
            self.assertEqual(image.read_bytes(), b"fake-image")

    @patch("face_registry.extract_face_encoding", return_value=np.zeros(128))
    def test_refuses_to_move_existing_student_to_another_school(self, _extract) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "face.jpg"
            image.write_bytes(b"fake-image")
            db = FaceDatabase(str(root / "faces.db"))
            school_a = db.create_school("A", "a")
            school_b = db.create_school("B", "b")
            db.add_face("student", "Aluno", "", school_id=school_a["id"])
            registry = FaceRegistry(db)

            with self.assertRaisesRegex(ValueError, "outra escola"):
                registry.register_face(
                    "Aluno", "+5511900000001", str(image), "student",
                    school_id=school_b["id"], store_image=False,
                )

            self.assertEqual(db.get_face("student")["school_id"], school_a["id"])


if __name__ == "__main__":
    unittest.main()
