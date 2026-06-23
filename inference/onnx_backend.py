"""ONNX Runtime backend using InsightFace's pre-packaged models.

Default pack is ``buffalo_sc`` (light, CPU-friendly). Override with
``INFERENCE_MODEL_PACK``. Heavier packs like ``buffalo_l`` are appropriate when
running on CUDA / TensorRT.
"""

from __future__ import annotations

import logging
import os
from functools import cached_property
from typing import Sequence

import numpy as np

from .base import FaceResult, InferenceBackend
from .providers import resolve_providers

logger = logging.getLogger(__name__)

DEFAULT_MODEL_PACK = "buffalo_sc"
DEFAULT_DET_SIZE = (640, 640)
EMBEDDING_DIM = 512


class OnnxInferenceBackend(InferenceBackend):
    """RetinaFace detector + ArcFace embedder via InsightFace + ONNX Runtime."""

    def __init__(
        self,
        model_pack: str | None = None,
        providers: Sequence[str] | None = None,
        det_size: tuple[int, int] | None = None,
        det_thresh: float | None = None,
    ) -> None:
        self._model_pack = model_pack or os.getenv(
            "INFERENCE_MODEL_PACK", DEFAULT_MODEL_PACK
        )
        self._providers = list(providers) if providers is not None else resolve_providers()
        self._det_size = det_size or DEFAULT_DET_SIZE
        self._det_thresh = (
            det_thresh
            if det_thresh is not None
            else float(os.getenv("INFERENCE_DET_THRESH", "0.5"))
        )

    @cached_property
    def _app(self):
        try:
            from insightface.app import FaceAnalysis
        except ImportError as exc:  # pragma: no cover — install error path
            raise RuntimeError(
                "insightface is required. Install with `pip install insightface`."
            ) from exc

        logger.info(
            "Loading InsightFace pack=%s providers=%s det_size=%s det_thresh=%.2f",
            self._model_pack,
            self._providers,
            self._det_size,
            self._det_thresh,
        )
        app = FaceAnalysis(name=self._model_pack, providers=self._providers)
        # ctx_id=0 selects GPU when CUDA/TensorRT is active; CPU otherwise.
        app.prepare(ctx_id=0, det_size=self._det_size, det_thresh=self._det_thresh)
        return app

    @property
    def model_version(self) -> str:
        return f"insightface-{self._model_pack}-{EMBEDDING_DIM}"

    @property
    def embedding_dim(self) -> int:
        return EMBEDDING_DIM

    @property
    def providers(self) -> Sequence[str]:
        return tuple(self._providers)

    def extract(self, image_bgr: np.ndarray) -> list[FaceResult]:
        if image_bgr is None or image_bgr.size == 0:
            return []

        faces = self._app.get(image_bgr)
        results: list[FaceResult] = []
        for face in faces:
            bbox = face.bbox.astype(int).tolist()
            normed = getattr(face, "normed_embedding", None)
            if normed is None:
                emb = face.embedding.astype(np.float32)
                norm = np.linalg.norm(emb)
                if norm > 0:
                    emb = emb / norm
            else:
                emb = normed.astype(np.float32)

            results.append(
                FaceResult(
                    bbox=(int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])),
                    embedding=emb,
                    det_score=float(face.det_score),
                )
            )
        return results

    def warmup(self) -> None:
        """Force model load + a dummy inference so the first real frame is fast."""
        dummy = np.zeros((self._det_size[1], self._det_size[0], 3), dtype=np.uint8)
        try:
            self._app.get(dummy)
        except Exception:  # noqa: BLE001 — warmup must never crash the caller
            logger.exception("Inference warmup failed; backend will load lazily.")
