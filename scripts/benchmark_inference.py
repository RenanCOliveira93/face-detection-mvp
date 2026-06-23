#!/usr/bin/env python3
"""Benchmark the active inference backend on a folder of images.

Usage:
    python scripts/benchmark_inference.py --images storage/faces --iterations 30

Reports the active provider, per-frame latency (mean / p50 / p95), and
FPS. Use it to compare CPU vs CoreML vs CUDA on the same hardware, and to
catch regressions when swapping models.
"""

from __future__ import annotations

import argparse
import logging
import statistics
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from inference import build_backend  # noqa: E402

logger = logging.getLogger("benchmark")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def load_images(folder: Path) -> list[tuple[Path, "cv2.Mat"]]:
    items = []
    for path in sorted(folder.iterdir()):
        if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}:
            continue
        img = cv2.imread(str(path))
        if img is None:
            logger.warning("Skipping unreadable image: %s", path)
            continue
        items.append((path, img))
    return items


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    k = max(0, min(len(sorted_vals) - 1, int(round((p / 100) * (len(sorted_vals) - 1)))))
    return sorted_vals[k]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--images",
        type=Path,
        default=Path("storage/faces"),
        help="Folder with sample images.",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=30,
        help="How many times to loop the dataset.",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=3,
        help="Warmup passes (not counted in stats).",
    )
    args = parser.parse_args()

    backend = build_backend()
    logger.info("Backend: %s", backend.model_version)
    logger.info("Providers: %s", list(backend.providers))
    logger.info("Embedding dim: %d", backend.embedding_dim)

    images = load_images(args.images)
    if not images:
        logger.error("No images found under %s", args.images)
        return 2

    logger.info("Loaded %d images from %s", len(images), args.images)

    # Warmup
    for _ in range(max(0, args.warmup)):
        for _, img in images:
            backend.extract(img)

    latencies_ms: list[float] = []
    face_counts: list[int] = []
    start = time.perf_counter()
    for _ in range(args.iterations):
        for _, img in images:
            t0 = time.perf_counter()
            faces = backend.extract(img)
            latencies_ms.append((time.perf_counter() - t0) * 1000)
            face_counts.append(len(faces))
    elapsed = time.perf_counter() - start

    total_frames = args.iterations * len(images)
    fps = total_frames / elapsed if elapsed > 0 else 0.0
    avg_ms = statistics.fmean(latencies_ms) if latencies_ms else 0.0
    p50_ms = percentile(latencies_ms, 50)
    p95_ms = percentile(latencies_ms, 95)
    avg_faces = statistics.fmean(face_counts) if face_counts else 0.0

    print("\n=== Inference benchmark ===")
    print(f"  backend          : {backend.model_version}")
    print(f"  providers        : {list(backend.providers)}")
    print(f"  images           : {len(images)} ({args.iterations} iterations)")
    print(f"  total frames     : {total_frames}")
    print(f"  wall time        : {elapsed:.2f} s")
    print(f"  fps              : {fps:.2f}")
    print(f"  latency mean     : {avg_ms:.2f} ms")
    print(f"  latency p50      : {p50_ms:.2f} ms")
    print(f"  latency p95      : {p95_ms:.2f} ms")
    print(f"  avg faces/frame  : {avg_faces:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
