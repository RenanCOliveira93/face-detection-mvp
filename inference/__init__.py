"""Inference layer abstraction.

The application core depends only on ``InferenceBackend``. Concrete backends
(ONNX Runtime + InsightFace today; TensorRT-only later if useful) plug in via
``build_backend()`` which honors the ``INFERENCE_PROVIDER`` env var and falls
back to the best available execution provider for the host.
"""

from .base import FaceResult, InferenceBackend
from .factory import build_backend
from .providers import (
    DEFAULT_PROVIDER_PRIORITY,
    resolve_providers,
)

__all__ = [
    "DEFAULT_PROVIDER_PRIORITY",
    "FaceResult",
    "InferenceBackend",
    "build_backend",
    "resolve_providers",
]
