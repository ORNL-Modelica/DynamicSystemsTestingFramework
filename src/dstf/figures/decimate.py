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


def bucket_boundaries(
    n_in: int, n_out: int
) -> tuple[NDArray[np.int64], NDArray[np.int64], NDArray[np.int64]]:
    """Every LTTB bucket boundary at once, for ``3 <= n_out < n_in``.

    Bucket ``i`` is ``[starts[i], ends[i])`` and the bucket after it — whose
    centroid the triangle uses — is ``[ends[i], next_ends[i])``. Same floor
    arithmetic the pre-hoist loop did, one array at a time.

    ``lttb``'s guard leaves ``3 <= n_out < n_in``, so
    ``bucket_size = (n_in - 2) / (n_out - 2)`` is strictly above 1 and
    consecutive entries of ``b`` differ by at least one. Hence no bucket is
    empty (``starts[i] < ends[i]``) and every next-bucket holds at least one
    sample (``next_ends[i] - ends[i] >= 1``) — which is why ``lttb`` carries
    no empty-bucket branch and no ``maximum(counts, 1)``.

    ``ends`` needs no clamp: its largest entry is ``b[n_out - 2]``, and
    ``(n_out - 2) * bucket_size`` is ``n_in - 2`` in exact arithmetic, so the
    entry is ``n_in - 1`` when the float product rounds up to or past
    ``n_in - 2`` and ``n_in - 2`` when it rounds below — never above
    ``n_in - 1``. (Measured over every pair with ``4 <= n_in <= 1200``:
    680,649 at ``n_in - 1``, 36,354 at ``n_in - 2``, none above.)

    ``next_ends`` is the one boundary that does overshoot, and its clamp is
    load-bearing: ``b[n_out - 1] >= n_in`` whenever ``bucket_size > 1``, and
    clamping it to exactly ``n_in`` is what makes ``reduceat``'s last segment
    — which runs to the end of the array — agree with the last next-bucket.

    Lives here, called by both ``lttb`` and the test that sweeps these
    claims, so the guard reads production's arithmetic rather than a copy.
    """
    bucket_size = (n_in - 2) / (n_out - 2)
    b = np.floor(np.arange(n_out, dtype=np.float64) * bucket_size).astype(np.int64) + 1
    return b[: n_out - 2], b[1 : n_out - 1], np.minimum(b[2:n_out], n_in)


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

    out_idx = np.empty(n_out, dtype=np.int64)
    out_idx[0] = 0
    out_idx[-1] = n_in - 1

    starts, ends, next_ends = bucket_boundaries(n_in, n_out)
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
