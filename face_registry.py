"""Cadastro e comparação facial usando InsightFace (ArcFace 512-d, cosine).

Threshold convention (post-refactor): ``RECOGNITION_TOLERANCE`` is the minimum
cosine similarity required to call a match. Higher = stricter. Typical values:

* 0.45 — permissive (more false positives)
* 0.55 — recommended starting point for ``buffalo_sc`` on CPU
* 0.65 — strict (more false negatives)

This replaces the old dlib L2-distance convention. Embeddings stored under a
different ``model_version`` are filtered out so a model swap never corrupts
matching — they just become invisible until re-cadastro.
"""

from __future__ import annotations

import logging
import re
import shutil
import unicodedata
from pathlib import Path

import cv2
import numpy as np

from config import CONFIG
from database import FaceDatabase
from inference import InferenceBackend, build_backend
from repositories import StudentRepository, is_cloud_enabled

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[\s-]+", "_", text)


class FaceRegistry:
    """Domain facade for registering faces and matching live detections.

    Owns the inference backend and the photo store; persists embeddings via
    ``FaceDatabase``. Match results use cosine similarity (higher = more
    similar) because embeddings are L2-normalized.
    """

    def __init__(
        self,
        db: FaceDatabase | None = None,
        backend: InferenceBackend | None = None,
    ) -> None:
        self.db = db or FaceDatabase()
        self.backend = backend or build_backend()
        self.images_dir = Path(CONFIG["face_images_dir"])
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self._known_cache: list[dict] | None = None
        self._student_repo: StudentRepository | None = None
        if CONFIG.get("school_id"):
            self._student_repo = StudentRepository(
                school_id=CONFIG["school_id"],
                model_version=self.backend.model_version,
            )

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------
    def register_face(
        self,
        full_name: str,
        phone: str,
        image_path: str,
        face_id: str | None = None,
        email: str = "",
        notes: str = "",
        enrollment_number: str | None = None,
        class_id: str | None = None,
        restriction_ids: list[str] | None = None,
    ) -> dict:
        person_id = face_id or slugify(full_name)
        image_src = Path(image_path)
        if not image_src.exists():
            raise FileNotFoundError(f"Imagem não encontrada: {image_src}")
        if image_src.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ValueError("Formato de imagem não suportado.")

        embedding = self._embed_single_face(image_src)

        stored_path = self.images_dir / f"{person_id}{image_src.suffix.lower()}"
        shutil.copy2(image_src, stored_path)

        embedding_list = embedding.tolist()
        payload = {
            "full_name": full_name,
            "phone": phone,
            "email": email,
            "notes": notes,
            "photo_path": str(stored_path),
            "encoding": embedding_list,
        }
        # Add optional class/enrollment locally too so offline rendering knows
        # them without a Supabase round-trip.
        if enrollment_number is not None:
            payload["enrollment_number"] = enrollment_number
        if class_id is not None:
            payload["class_id"] = class_id

        if self.db.get_face(person_id):
            self.db.update_face(person_id, **payload)
        else:
            self.db.add_face(face_id=person_id, **payload)

        self._sync_student_to_cloud(
            face_id=person_id,
            full_name=full_name,
            phone=phone,
            email=email,
            embedding=embedding_list,
            enrollment_number=enrollment_number,
            class_id=class_id,
            restriction_ids=restriction_ids or [],
        )

        self._known_cache = None
        return self.db.get_face(person_id)

    def _sync_student_to_cloud(
        self,
        face_id: str,
        full_name: str,
        phone: str,
        email: str,
        embedding: list[float],
        enrollment_number: str | None = None,
        class_id: str | None = None,
        restriction_ids: list[str] | None = None,
    ) -> None:
        """Push student + current embedding + restrictions to Supabase. No-op when offline."""
        if self._student_repo is None or not is_cloud_enabled():
            return
        try:
            supabase_id = self._student_repo.upsert_student(
                face_id=face_id,
                full_name=full_name,
                phone=phone,
                email=email or None,
                enrollment_number=enrollment_number,
                class_id=class_id,
            )
            if not supabase_id:
                logger.warning(
                    "Student upsert returned no id for face_id=%s; embedding sync skipped.",
                    face_id,
                )
                return
            self.db.set_face_supabase_id(face_id, supabase_id)
            self._student_repo.upsert_current_embedding(supabase_id, embedding)
            if restriction_ids is not None:
                self._student_repo.replace_student_restrictions(
                    student_id=supabase_id,
                    restriction_ids=restriction_ids,
                )
            logger.info(
                "Cadastro sync ok: face_id=%s student_id=%s.", face_id, supabase_id
            )
        except Exception:  # noqa: BLE001
            logger.exception("Cadastro sync to Supabase failed for face_id=%s.", face_id)

    def _embed_single_face(self, image_path: Path) -> np.ndarray:
        image = cv2.imread(str(image_path))
        if image is None:
            raise ValueError("Não foi possível abrir a imagem.")

        results = self.backend.extract(image)
        if not results:
            raise ValueError("Nenhum rosto detectado na imagem enviada.")
        if len(results) > 1:
            raise ValueError("A imagem deve conter apenas um rosto.")
        return results[0].embedding

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------
    def known_faces(self, refresh: bool = False) -> list[dict]:
        """Return cadastrados whose stored embedding matches the current model dim.

        Mismatched embeddings (old dlib 128-d records, for example) are dropped
        from matching so a model swap can't poison results. Re-cadastro brings
        them back.
        """
        if refresh or self._known_cache is None:
            faces = self.db.list_faces()
            expected_dim = self.backend.embedding_dim
            usable: list[dict] = []
            skipped = 0
            for face in faces:
                encoding = face.get("encoding")
                if not encoding:
                    continue
                if len(encoding) != expected_dim:
                    skipped += 1
                    continue
                usable.append(face)
            if skipped:
                logger.warning(
                    "Ignoring %d cadastrados with embedding dim != %d "
                    "(stale model_version=%s). Re-cadastrar para reconhecer.",
                    skipped,
                    expected_dim,
                    self.backend.model_version,
                )
            self._known_cache = usable
        return self._known_cache

    def match_embedding(
        self,
        candidate: np.ndarray,
    ) -> tuple[dict | None, float | None]:
        """Return ``(face, cosine_similarity)`` for the best match, or ``(None, score)``.

        ``score`` is always returned (even for non-matches) so the UI can show
        how close the closest candidate was.
        """
        faces = self.known_faces()
        if not faces:
            return None, None

        matrix = np.array([face["encoding"] for face in faces], dtype=np.float32)
        # candidate is already L2-normalized; matrix rows likewise
        sims = matrix @ candidate.astype(np.float32)
        best_index = int(np.argmax(sims))
        best_sim = float(sims[best_index])
        threshold = float(CONFIG["recognition_tolerance"])
        if best_sim >= threshold:
            return faces[best_index], best_sim
        return None, best_sim

    def invalidate_cache(self) -> None:
        """Drop the cached known-faces list (call after CRUD)."""
        self._known_cache = None
