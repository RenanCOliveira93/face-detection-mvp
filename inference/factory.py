"""Single entrypoint to build the inference backend the rest of the app uses.

Keeps the choice of backend out of the call sites — they just call
``build_backend()`` once at startup.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

from .base import InferenceBackend
from .onnx_backend import OnnxInferenceBackend

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def build_backend() -> InferenceBackend:
    """Return the configured singleton inference backend.

    Today only ``onnx`` is supported. The env switch exists so future TensorRT-
    native or alternative backends can plug in without touching call sites.
    """
    kind = os.getenv("INFERENCE_BACKEND", "onnx").strip().lower()
    if kind not in {"onnx"}:
        logger.warning("Unknown INFERENCE_BACKEND=%r; using onnx.", kind)
        kind = "onnx"

    backend: InferenceBackend = OnnxInferenceBackend()
    logger.info(
        "Inference backend ready: kind=%s model=%s providers=%s dim=%d",
        kind,
        backend.model_version,
        list(backend.providers),
        backend.embedding_dim,
    )
    return backend
