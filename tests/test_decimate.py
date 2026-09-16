"""Tests for LTTB decimation (Phase 6.0 — performance budget).

Decimation is visual-only: it affects what ``interactive.html`` embeds
for Plotly rendering, not pass/fail scoring or stored baselines. These
tests check the algorithmic contract, not rendering.
"""

from __future__ import annotations

import numpy as np
import pytest

from dstf.figures.decimate import bucket_boundaries, decimate_pair, lttb


class TestLttbContract:
    def test_empty_input_returns_empty(self):
        t_out, v_out = lttb(np.asarray([]), np.asarray([]), 100)
        assert len(t_out) == 0
        assert len(v_out) == 0

    def test_below_threshold_returns_unchanged(self):
        t = np.linspace(0, 10, 50)
        v = np.sin(t)
        t_out, v_out = lttb(t, v, 100)
        np.testing.assert_array_equal(t_out, t)
        np.testing.assert_array_equal(v_out, v)

    def test_n_out_lt_3_returns_unchanged(self):
        t = np.linspace(0, 10, 100)
        v = np.sin(t)
        t_out, v_out = lttb(t, v, 2)
        np.testing.assert_array_equal(t_out, t)
        np.testing.assert_array_equal(v_out, v)

    def test_output_size_matches_n_out(self):
        t = np.linspace(0, 10, 1000)
        v = np.sin(t)
        t_out, v_out = lttb(t, v, 100)
        assert len(t_out) == 100
        assert len(v_out) == 100

    def test_endpoints_preserved(self):
        t = np.linspace(0, 10, 1000)
        v = np.cos(t)
        t_out, v_out = lttb(t, v, 50)
        assert t_out[0] == t[0]
        assert t_out[-1] == t[-1]
        assert v_out[0] == v[0]
        assert v_out[-1] == v[-1]

    def test_time_monotonic_after_decimation(self):
        t = np.linspace(0, 100, 5000)
        v = np.sin(t) + 0.3 * np.sin(7 * t)
        t_out, _ = lttb(t, v, 200)
        assert np.all(np.diff(t_out) > 0)

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError, match="length mismatch"):
            lttb(np.arange(10), np.arange(5), 3)


class TestLttbVisualFidelity:
    """Sanity checks that LTTB preserves visually important features."""

    def test_preserves_central_peak(self):
        """A sharp gaussian peak in the middle of a flat trace survives decimation."""
        t = np.linspace(0, 10, 2000)
        v = np.exp(-((t - 5) ** 2) / 0.01)  # sharp peak at t=5
        _, v_out = lttb(t, v, 100)
        # Max in decimated output should still be close to 1.0 (original peak).
        # Uniform striding at 20:1 might entirely miss a gaussian with sigma ~0.1.
        assert v_out.max() > 0.5, f"Peak lost: max={v_out.max()}"

    def test_preserves_min_max_range(self):
        """Decimation should keep the overall range within tolerance."""
        rng = np.random.default_rng(42)
        t = np.linspace(0, 10, 5000)
        v = np.sin(t) + 0.1 * rng.standard_normal(5000)
        _, v_out = lttb(t, v, 500)
        # Allow ~5% range compression from losing noise spikes
        orig_range = v.max() - v.min()
        out_range = v_out.max() - v_out.min()
        assert out_range > 0.9 * orig_range


class TestDecimatePair:
    def test_list_roundtrip(self):
        t = list(range(1000))
        v = [i * 0.5 for i in range(1000)]
        t_out, v_out = decimate_pair(t, v, 100)
        assert isinstance(t_out, list)
        assert isinstance(v_out, list)
        assert len(t_out) == 100

    def test_none_passthrough(self):
        t_out, v_out = decimate_pair(None, None, 100)
        assert t_out is None
        assert v_out is None

    def test_empty_list_passthrough(self):
        t_out, v_out = decimate_pair([], [], 100)
        assert t_out == []
        assert v_out == []

    def test_below_threshold_returns_original_list(self):
        t = [0.0, 1.0, 2.0, 3.0]
        v = [10.0, 11.0, 12.0, 13.0]
        t_out, v_out = decimate_pair(t, v, 100)
        assert t_out == t
        assert v_out == v


def _lttb_reference(time, values, n_out):
    """The loop as shipped before the hoist, kept verbatim as the oracle."""
    n_in = len(time)
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


class TestHoistedLoopIsTheSameAlgorithm:
    """Where candidate areas do not tie, the hoisted loop picks the same
    points. Where they tie up to round-off the argmax may break the tie
    differently — ``np.add.reduceat`` and ``np.mean`` sum in a different
    order: on COLLINEAR data (review measured 1.8e-12 differences and 411 of
    1000 flipped picks on ``v = 3t + 1``) and on stepped or boolean signals
    over a uniform grid (``t = linspace(0, 100, 14)``, ``v = arange(14) >= 9``,
    ``n_out = 6`` picks the other plateau). Either pick has the same area.
    The docstring on ``lttb`` says so. ``(51, 13)`` is the shape where the
    last next-bucket holds two points (``(n_out - 2) * bucket_size`` rounds
    below ``n_in - 2``) and the last interior bucket has several candidates:
    a ``next_ends`` clipped to ``n_in - 1`` fails there and nowhere else in
    this list."""

    @pytest.mark.parametrize(
        "n_in,n_out",
        [
            (7, 3),
            (11, 5),
            (20, 19),
            (1000, 7),
            (2001, 1000),
            (50_000, 1000),
            (1003, 1002),
            (51, 13),
        ],
    )
    @pytest.mark.parametrize("seed", [0, 1, 2])
    def test_same_points_as_the_reference_loop_on_non_degenerate_data(
        self, n_in, n_out, seed
    ):
        rng = np.random.default_rng(seed)
        t = np.sort(rng.uniform(0.0, 100.0, n_in))
        v = np.cumsum(rng.standard_normal(n_in))
        want_t, want_v = _lttb_reference(t, v, n_out)
        got_t, got_v = lttb(t, v, n_out)
        np.testing.assert_array_equal(got_t, want_t)
        np.testing.assert_array_equal(got_v, want_v)

    def test_the_tightest_buckets_the_guard_allows(self):
        # n_out just below n_in gives the smallest bucket_size the guard
        # permits (18/17 here) — one candidate in most buckets. The
        # reference's empty-bucket arm stays unreached even here.
        t = np.arange(20.0)
        v = t**2
        want = _lttb_reference(t, v, 19)
        got = lttb(t, v, 19)
        np.testing.assert_array_equal(got[0], want[0])


def _bucket_boundaries(n_in: int, n_out: int):
    """PRODUCTION's boundary arithmetic, not a copy of it.

    A recomputed copy was the first version of this, and it fenced nothing:
    deleting the ``next_ends`` clamp this module calls load-bearing left the
    whole sweep green, because the sweep was asserting properties of the
    copy. The import is the point — these tests must fail when
    ``decimate.bucket_boundaries`` drifts.
    """
    starts, ends, next_ends = bucket_boundaries(n_in, n_out)
    return (n_in - 2) / (n_out - 2), starts, ends, next_ends


def _n_out_sweep(n_in: int) -> list[int]:
    """Every valid ``n_out`` for small ``n_in``, a spread for the big ones."""
    if n_in <= 100:
        return list(range(3, n_in))
    outs = set(range(3, 20))
    outs |= {n_in - 1, n_in - 2, n_in - 3, n_in // 2, n_in // 3, n_in // 7}
    outs |= {int(n_in * f) for f in (0.01, 0.1, 0.25, 0.5, 0.75, 0.9, 0.99)}
    return sorted(x for x in outs if 3 <= x < n_in)


class TestBucketPrecondition:
    """``lttb`` carries no empty-bucket handling, and that is only sound
    because the early guard makes empty buckets unreachable: inside the body
    ``3 <= n_out < n_in``, so ``bucket_size = (n_in - 2) / (n_out - 2) > 1``
    STRICTLY, consecutive bucket boundaries differ by at least one, and every
    bucket and next-bucket holds at least one sample.

    The regime these assertions guard against is a guard that stops excluding
    ``n_out > n_in``: measured on ``v = 3t + 1`` with the guard removed,
    ``(n_in, n_out) = (5, 8)`` gives ``bucket_size == 0.5``, 3 empty buckets,
    3 zero ``counts``, and dies with ``ValueError: attempt to get argmax of
    an empty sequence``; ``(20, 40)`` and ``(100, 300)`` do the same with 20
    and 200 empty buckets. ``n_out == n_in`` is the benign edge — it gives
    ``bucket_size == 1``, one sample per bucket and no decimation to do, so
    only the strict ``bucket_size > 1`` assertion below notices it.
    """

    @pytest.mark.parametrize("n_in", [4, 5, 7, 11, 100, 1000, 4001])
    def test_buckets_are_never_empty_over_the_whole_sweep(self, n_in):
        for n_out in _n_out_sweep(n_in):
            bucket_size, starts, ends, next_ends = _bucket_boundaries(n_in, n_out)
            assert bucket_size > 1.0, f"({n_in}, {n_out}) bucket_size={bucket_size}"
            assert np.all(starts < ends), f"({n_in}, {n_out}) empty bucket"
            assert np.all(next_ends - ends >= 1), f"({n_in}, {n_out}) empty next-bucket"
            # ``ends`` is used unclipped as reduceat's index array, so its top
            # entry must still be a valid index; ``next_ends`` is the boundary
            # that really does overshoot and keeps its clamp.
            assert ends.max() <= n_in - 1, f"({n_in}, {n_out}) end past the array"
            assert next_ends[-1] == n_in, f"({n_in}, {n_out}) last next-bucket clipped"
            # The three slices come off one array, so consecutive buckets are
            # adjacent by construction: bucket i ends exactly where i+1 starts.
            # Any clamp or offset applied to one slice and not its neighbour
            # tears that seam — which is what makes this sweep, and not only
            # the reference oracle, notice a changed boundary.
            np.testing.assert_array_equal(starts[1:], ends[:-1])
            np.testing.assert_array_equal(ends[1:], next_ends[:-1])

    @pytest.mark.parametrize("n_in,n_out", [(5, 8), (20, 40), (100, 300)])
    def test_the_precondition_predicates_have_teeth(self, n_in, n_out):
        """The assertions above are not vacuous: outside the guard
        (``n_out > n_in``) the same predicates go false."""
        bucket_size, starts, ends, next_ends = _bucket_boundaries(n_in, n_out)
        assert bucket_size < 1.0
        assert np.any(starts >= ends)
        assert np.any(next_ends - ends < 1)

    @pytest.mark.parametrize("n_in", [4, 5, 7, 11, 100, 1000, 4001])
    def test_output_contract_over_the_whole_sweep(self, n_in):
        t = np.linspace(0.0, 100.0, n_in)
        v = np.sin(0.3 * t) + 0.2 * np.cos(2.7 * t)
        for n_out in _n_out_sweep(n_in):
            t_out, v_out = lttb(t, v, n_out)
            assert len(t_out) == n_out, f"({n_in}, {n_out}) length"
            assert len(v_out) == n_out, f"({n_in}, {n_out}) length"
            assert t_out[0] == t[0] and v_out[0] == v[0], f"({n_in}, {n_out}) first"
            assert t_out[-1] == t[-1] and v_out[-1] == v[-1], f"({n_in}, {n_out}) last"
            # Strictly increasing, and every kept sample is an input sample
            # (a kept index, not an interpolation) taken in order.
            assert np.all(np.diff(t_out) > 0), f"({n_in}, {n_out}) not increasing"
            idx = np.searchsorted(t, t_out)
            np.testing.assert_array_equal(t[idx], t_out)
            np.testing.assert_array_equal(v[idx], v_out)
            # And they SPAN the input: "the first n_out-1 samples plus the
            # last" satisfies everything above, so pin that each kept index
            # sits in its own bucket — the mid output is near the mid input.
            mid = idx[len(idx) // 2]
            assert abs(mid - (n_in - 1) * (len(idx) // 2) / (n_out - 1)) <= (
                2 * (n_in - 2) / (n_out - 2) + 2
            ), f"({n_in}, {n_out}) buckets not spanning: mid index {mid}"
