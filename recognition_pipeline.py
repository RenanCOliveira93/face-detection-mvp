"""Pure, camera-independent orchestration for batches of face embeddings."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any


def recognize_batch(
    encodings: Iterable[Any], matcher: Callable[[Any], tuple[dict | None, float | None]]
) -> list[dict]:
    """Match every encoding independently; never truncates a frame to one face."""
    results = []
    for encoding in encodings:
        person, score = matcher(encoding)
        results.append({"known": person is not None, "person": person, "match_score": score})
    return results
