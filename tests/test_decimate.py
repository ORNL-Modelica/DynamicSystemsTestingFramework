"""Tests for LTTB decimation (Phase 6.0 — performance budget).

Decimation is visual-only: it affects what ``interactive.html`` embeds
for Plotly rendering, not pass/fail scoring or stored baselines. These
tests check the algorithmic contract, not rendering.
"""

from __future__ import annotations

import numpy as np
import pytest

from dstf.figures.decimate import decimate_pair, lttb


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

    def test_a_bucket_narrower_than_a_point_still_advances(self):
        # n_out close to n_in makes empty buckets; the reference's
        # `max(a + 1, ...)` rule must be reproduced exactly.
        t = np.arange(20.0)
        v = t**2
        want = _lttb_reference(t, v, 19)
        got = lttb(t, v, 19)
        np.testing.assert_array_equal(got[0], want[0])
