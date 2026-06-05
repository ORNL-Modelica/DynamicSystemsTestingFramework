"""Tests for LTTB decimation (Phase 6.0 — performance budget).

Decimation is visual-only: it affects what ``interactive.html`` embeds
for Plotly rendering, not pass/fail scoring or stored baselines. These
tests check the algorithmic contract, not rendering.
"""

from __future__ import annotations

import numpy as np
import pytest

from dstf.reporting.decimate import decimate_pair, lttb


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
