"""Largest-Triangle-Three-Buckets (LTTB) decimation for trajectory rendering.

Used only on the ``interactive.html`` embed path — the full-resolution
arrays are preserved on disk in ``comparison_data.json``. Decimation
affects what the browser draws, not pass/fail scoring, stored baselines,
or downstream tooling.

LTTB preserves visual peaks/valleys far better than uniform striding
by selecting the point in each bucket that forms the largest triangle
with the previously kept point and the next bucket's centroid.

Reference: Sveinn Steinarsson, "Downsampling Time Series for Visual
Representation" (MSc thesis, 2013).
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray


def lttb(
    time: NDArray[np.float64],
    values: NDArray[np.float64],
    n_out: int,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Decimate ``(time, values)`` to at most ``n_out`` points via LTTB.

    Returns the original arrays unchanged when ``len(time) <= n_out`` or
    when ``n_out < 3`` (the algorithm requires at least first, last, and
    one interior bucket). Empty inputs return empty outputs.
    """
    n_in = len(time)
    if n_in == 0:
        return np.asarray([], dtype=np.float64), np.asarray([], dtype=np.float64)
    if n_in != len(values):
        raise ValueError(f"time and values length mismatch: {n_in} vs {len(values)}")
    if n_out >= n_in or n_out < 3:
        return np.asarray(time, dtype=np.float64), np.asarray(values, dtype=np.float64)

    t = np.asarray(time, dtype=np.float64)
    v = np.asarray(values, dtype=np.float64)

    bucket_size = (n_in - 2) / (n_out - 2)
    out_idx = np.empty(n_out, dtype=np.int64)
    out_idx[0] = 0
    out_idx[-1] = n_in - 1

    a = 0
    for i in range(n_out - 2):
        bucket_start = int(np.floor(i * bucket_size)) + 1
        bucket_end = int(np.floor((i + 1) * bucket_size)) + 1
        bucket_end = min(bucket_end, n_in - 1)
        if bucket_start >= bucket_end:
            chosen = max(a + 1, min(bucket_start, n_in - 2))
            out_idx[i + 1] = chosen
            a = chosen
            continue

        next_start = bucket_end
        next_end = int(np.floor((i + 2) * bucket_size)) + 1
        next_end = min(next_end, n_in)
        if next_end > next_start:
            avg_t = float(np.mean(t[next_start:next_end]))
            avg_v = float(np.mean(v[next_start:next_end]))
        else:
            avg_t, avg_v = t[-1], v[-1]

        ta, va = t[a], v[a]
        seg_t = t[bucket_start:bucket_end]
        seg_v = v[bucket_start:bucket_end]
        area = np.abs((ta - avg_t) * (seg_v - va) - (ta - seg_t) * (avg_v - va))
        chosen = bucket_start + int(np.argmax(area))
        out_idx[i + 1] = chosen
        a = chosen

    return t[out_idx], v[out_idx]


def decimate_pair(
    time: list | NDArray,
    values: list | NDArray,
    n_out: int,
) -> tuple[list, list]:
    """LTTB that accepts/returns plain Python lists (template-context friendly)."""
    if time is None or values is None:
        return time, values
    if not len(time) or not len(values):
        return list(time), list(values)
    t_out, v_out = lttb(
        np.asarray(time, dtype=np.float64), np.asarray(values, dtype=np.float64), n_out
    )
    return t_out.tolist(), v_out.tolist()
