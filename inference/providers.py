"""Resolve which ONNX Runtime execution providers to use on this host.

Priority (descending): TensorRT → CUDA → CoreML → CPU.

Override with ``INFERENCE_PROVIDER`` env (``tensorrt`` / ``cuda`` / ``coreml`` /
``cpu``). If the requested provider is unavailable we log a warning and fall
back to CPU so the system stays usable on dev laptops without GPUs.
"""

from __future__ import annotations

import logging
import os
from typing import Sequence

logger = logging.getLogger(__name__)

DEFAULT_PROVIDER_PRIORITY: tuple[str, ...] = (
    "TensorrtExecutionProvider",
    "CUDAExecutionProvider",
    "CoreMLExecutionProvider",
    "CPUExecutionProvider",
)

_ENV_ALIAS_TO_ORT: dict[str, str] = {
    "tensorrt": "TensorrtExecutionProvider",
    "trt": "TensorrtExecutionProvider",
    "cuda": "CUDAExecutionProvider",
    "gpu": "CUDAExecutionProvider",
    "coreml": "CoreMLExecutionProvider",
    "metal": "CoreMLExecutionProvider",
    "mps": "CoreMLExecutionProvider",
    "cpu": "CPUExecutionProvider",
}


def _available_providers() -> list[str]:
    try:
        import onnxruntime as ort
    except ImportError as exc:  # pragma: no cover — install error path
        raise RuntimeError(
            "onnxruntime is required. Install with `pip install onnxruntime`."
        ) from exc
    return list(ort.get_available_providers())


def resolve_providers(env: str | None = None) -> list[str]:
    """Return the list of providers to pass to ONNX Runtime, in priority order.

    Always includes ``CPUExecutionProvider`` last as a safety fallback.
    """

    available = _available_providers()
    requested = (env or os.getenv("INFERENCE_PROVIDER", "")).strip().lower()

    if requested:
        target = _ENV_ALIAS_TO_ORT.get(requested)
        if target is None:
            logger.warning(
                "Unknown INFERENCE_PROVIDER=%r; falling back to auto-detect.",
                requested,
            )
        elif target in available:
            providers = [target]
            if "CPUExecutionProvider" not in providers:
                providers.append("CPUExecutionProvider")
            logger.info("Inference providers (forced): %s", providers)
            return providers
        else:
            logger.warning(
                "INFERENCE_PROVIDER=%r requested but %s not available on this host; "
                "available=%s. Falling back to auto-detect.",
                requested,
                target,
                available,
            )

    providers = [p for p in DEFAULT_PROVIDER_PRIORITY if p in available]
    if "CPUExecutionProvider" not in providers:
        providers.append("CPUExecutionProvider")
    logger.info("Inference providers (auto): %s", providers)
    return providers


def describe_active_provider(providers: Sequence[str]) -> str:
    """Return the first non-CPU provider, or ``cpu`` if CPU-only."""
    for provider in providers:
        if provider != "CPUExecutionProvider":
            return provider
    return "CPUExecutionProvider"
