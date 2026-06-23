"""Abstract base for face inference backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Sequence

import numpy as np


@dataclass(frozen=True)
class FaceResult:
    """One detected face with its embedding."""

    bbox: tuple[int, int, int, int]
    """(x1, y1, x2, y2) in pixel coordinates of the source frame."""

    embedding: np.ndarray
    """L2-normalized vector of shape ``(embedding_dim,)`` and dtype float32."""

    det_score: float
    """Detector confidence in ``[0, 1]``."""


class InferenceBackend(ABC):
    """Detect + embed faces for the recognition pipeline.

    Implementations must produce L2-normalized embeddings so that the standard
    similarity metric is the dot product (= cosine similarity).
    """

    @property
    @abstractmethod
    def model_version(self) -> str:
        """Stable identifier for the model pack (e.g. ``insightface-buffalo_sc-512``)."""

    @property
    @abstractmethod
    def embedding_dim(self) -> int:
        """Dimensionality of the embedding vectors this backend emits."""

    @property
    @abstractmethod
    def providers(self) -> Sequence[str]:
        """Active ONNX Runtime execution providers, in priority order."""

    @abstractmethod
    def extract(self, image_bgr: np.ndarray) -> list[FaceResult]:
        """Detect every face in ``image_bgr`` (OpenCV BGR) and return embeddings.

        For cadastro callers should validate that exactly one face was found.
        """

    @staticmethod
    def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        """Cosine similarity assuming both inputs are already L2-normalized."""
        return float(np.dot(a, b))

    @staticmethod
    def cosine_distance(a: np.ndarray, b: np.ndarray) -> float:
        """``1 - cosine_similarity``, useful when callers think in distances."""
        return float(1.0 - np.dot(a, b))
