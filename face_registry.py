"""Cadastro e comparação facial usando uma única foto por aluno."""

from __future__ import annotations

import re
import shutil
import unicodedata
from pathlib import Path

import face_recognition
import numpy as np

from config import CONFIG
from database import FaceDatabase

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[\s-]+", "_", text)


class FaceRegistry:
    def __init__(self, db: FaceDatabase | None = None):
        self.db = db or FaceDatabase()
        self.images_dir = Path(CONFIG["face_images_dir"])
        self.images_dir.mkdir(parents=True, exist_ok=True)

    def register_face(
        self,
        full_name: str,
        phone: str,
        image_path: str,
        face_id: str | None = None,
        email: str = "",
        notes: str = "",
    ) -> dict:
        person_id = face_id or slugify(full_name)
        image_src = Path(image_path)
        if not image_src.exists():
            raise FileNotFoundError(f"Imagem não encontrada: {image_src}")
        if image_src.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ValueError("Formato de imagem não suportado.")

        encoding = extract_face_encoding(image_src)
        stored_path = self.images_dir / f"{person_id}{image_src.suffix.lower()}"
        shutil.copy2(image_src, stored_path)

        existing = self.db.get_face(person_id)
        payload = {
            "full_name": full_name,
            "phone": phone,
            "email": email,
            "notes": notes,
            "photo_path": str(stored_path),
            "encoding": encoding.tolist(),
        }
        if existing:
            self.db.update_face(person_id, **payload)
        else:
            self.db.add_face(face_id=person_id, **payload)
        return self.db.get_face(person_id)

    def known_faces(self) -> list[dict]:
        return [face for face in self.db.list_faces() if face.get("encoding")]

    def match_encoding(self, candidate_encoding: np.ndarray) -> tuple[dict | None, float | None]:
        faces = self.known_faces()
        if not faces:
            return None, None
        encodings = np.array([face["encoding"] for face in faces], dtype=np.float64)
        distances = face_recognition.face_distance(encodings, candidate_encoding)
        best_index = int(np.argmin(distances))
        best_distance = float(distances[best_index])
        threshold = float(CONFIG["recognition_tolerance"])
        if best_distance <= threshold:
            similarity = max(0.0, 1.0 - (best_distance / threshold))
            return faces[best_index], similarity
        return None, best_distance


def extract_face_encoding(image_path: str | Path) -> np.ndarray:
    image = face_recognition.load_image_file(str(image_path))
    locations = face_recognition.face_locations(image, model="hog")
    if not locations:
        raise ValueError("Nenhum rosto detectado na imagem enviada.")
    if len(locations) > 1:
        raise ValueError("A imagem deve conter apenas um rosto.")
    encodings = face_recognition.face_encodings(image, locations)
    if not encodings:
        raise ValueError("Não foi possível gerar o vetor facial da imagem.")
    return encodings[0]
