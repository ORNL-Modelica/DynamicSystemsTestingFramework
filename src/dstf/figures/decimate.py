"""Largest-Triangle-Three-Buckets (LTTB) decimation for figure rendering.

Used on every figure embed — the report page and the authoring tool share
the budget — while the full-resolution arrays stay on disk in
``comparison_data.json``. Decimation affects what the browser draws, not
pass/fail scoring, stored baselines, or downstream tooling.

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

    Bucket means come from ``np.add.reduceat``, which sums a segment in a
    different order than ``np.mean``; the two differ by round-off. That
    changes a pick only where candidate areas tie up to round-off: collinear
    runs (every area is round-off, the other sample lies on the same line)
    and equal-area candidates on stepped or boolean signals over a uniform
    grid (the tie breaks to the other plateau). Either pick is a valid LTTB
    choice of equal area; the difference is in the drawing, not the data.
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

    # Every bucket boundary at once: bucket i is [starts[i], ends[i]) and
    # the bucket after it — whose centroid the triangle uses — is
    # [ends[i], next_ends[i]). Same floor arithmetic as the loop had.
    #
    # The guard above leaves 3 <= n_out < n_in, so bucket_size > 1 STRICTLY
    # and consecutive entries of ``b`` differ by at least one: no bucket is
    # empty (starts[i] < ends[i]) and every next-bucket holds at least one
    # sample (counts >= 1). ``ends`` needs no clamp either — its largest
    # entry is b[n_out - 2] == floor(n_in - 2) + 1 == n_in - 1, a valid
    # index. ``next_ends`` is the one boundary that does overshoot:
    # b[n_out - 1] == floor(n_in - 2 + bucket_size) + 1 > n_in - 1 whenever
    # bucket_size > 1, which is always, so the clamp to n_in is load-bearing.
    # TestBucketPrecondition sweeps these claims over the shapes lttb sees.
    b = np.floor(np.arange(n_out, dtype=np.float64) * bucket_size).astype(np.int64) + 1
    starts = b[: n_out - 2]
    ends = b[1 : n_out - 1]
    next_ends = np.minimum(b[2:n_out], n_in)
    # Next-bucket means, one reduceat each. reduceat's last segment runs to
    # the end of the array, which is where the last next-bucket ends
    # (next_ends[-1] == n_in, since b[n_out - 1] >= n_in), so the two agree.
    counts = next_ends - ends
    avg_t = np.add.reduceat(t, ends) / counts
    avg_v = np.add.reduceat(v, ends) / counts

    a = 0
    for i in range(n_out - 2):
        bucket_start = int(starts[i])
        bucket_end = int(ends[i])
        ta, va = t[a], v[a]
        seg_t = t[bucket_start:bucket_end]
        seg_v = v[bucket_start:bucket_end]
        area = np.abs((ta - avg_t[i]) * (seg_v - va) - (ta - seg_t) * (avg_v[i] - va))
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


def decimate_band(
    time: NDArray[np.float64],
    lower: NDArray[np.float64],
    upper: NDArray[np.float64],
    n_out: int,
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """Decimate an envelope on ONE shared time axis.

    LTTB picks the kept samples from ``upper``; ``lower`` is then read at
    exactly those times. The kept times are grid nodes, so ``np.interp`` at
    them returns the node values — no approximation, and the two edges can
    never drift onto different x arrays.
    """
    t2, u2 = lttb(
        np.asarray(time, dtype=np.float64), np.asarray(upper, dtype=np.float64), n_out
    )
    l2 = np.interp(
        t2, np.asarray(time, dtype=np.float64), np.asarray(lower, dtype=np.float64)
    )
    return t2, l2, u2
