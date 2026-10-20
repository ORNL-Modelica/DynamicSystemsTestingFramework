"""Tests for comparison/comparator.py — NRMSE, events, structural warnings."""

import numpy as np
import pytest

from dstf.backends.types import TestResult
from dstf.comparison.algorithms import (
    _dedup_time_series,
    _find_event_boundaries,
    _split_segments,
)
from dstf.comparison.comparator import _check_structural_changes
from dstf.comparison.registry import summarize
from dstf.comparison.sides import Sides
from dstf.storage.baseline_codec import decode_reference
from scoring import score

# ---------------------------------------------------------------------------
# Event boundary detection
# ---------------------------------------------------------------------------


class TestFindEventBoundaries:
    def test_no_events(self):
        t = np.array([0.0, 0.5, 1.0, 1.5, 2.0])
        assert _find_event_boundaries(t) == []

    def test_single_event_double(self):
        t = np.array([0.0, 0.5, 1.0, 1.0, 1.5, 2.0])
        assert _find_event_boundaries(t) == [(3, 3)]

    def test_single_event_triple(self):
        """Dymola sometimes produces 3 duplicate time points per event."""
        t = np.array([0.0, 0.5, 1.0, 1.0, 1.0, 1.5, 2.0])
        assert _find_event_boundaries(t) == [(3, 4)]

    def test_multiple_events(self):
        t = np.array([0.0, 1.0, 1.0, 2.0, 2.0, 3.0])
        assert _find_event_boundaries(t) == [(2, 2), (4, 4)]

    def test_event_at_start(self):
        t = np.array([0.0, 0.0, 1.0, 2.0])
        assert _find_event_boundaries(t) == [(1, 1)]

    def test_event_at_end(self):
        t = np.array([0.0, 1.0, 2.0, 2.0])
        assert _find_event_boundaries(t) == [(3, 3)]

    def test_empty(self):
        t = np.array([1.0])
        assert _find_event_boundaries(t) == []


# ---------------------------------------------------------------------------
# Segment splitting
# ---------------------------------------------------------------------------


class TestSplitSegments:
    def test_no_boundaries(self):
        t = np.array([0.0, 1.0, 2.0])
        v = np.array([0.0, 1.0, 2.0])
        segs = _split_segments(t, v, [])
        assert len(segs) == 1
        np.testing.assert_array_equal(segs[0][0], t)
        np.testing.assert_array_equal(segs[0][1], v)

    def test_single_boundary_double(self):
        t = np.array([0.0, 1.0, 1.0, 2.0])
        v = np.array([0.0, 0.9, 1.1, 2.0])
        segs = _split_segments(t, v, [(2, 2)])
        assert len(segs) == 2
        np.testing.assert_array_equal(segs[0][0], [0.0, 1.0])
        np.testing.assert_array_equal(segs[0][1], [0.0, 0.9])
        np.testing.assert_array_equal(segs[1][0], [1.0, 2.0])
        np.testing.assert_array_equal(segs[1][1], [1.1, 2.0])

    def test_single_boundary_triple(self):
        """Triple time points: middle value is skipped."""
        t = np.array([0.0, 1.0, 1.0, 1.0, 2.0])
        v = np.array([0.0, 0.9, 0.95, 1.1, 2.0])
        segs = _split_segments(t, v, [(2, 3)])
        assert len(segs) == 2
        np.testing.assert_array_equal(segs[0][0], [0.0, 1.0])
        np.testing.assert_array_equal(segs[0][1], [0.0, 0.9])
        np.testing.assert_array_equal(segs[1][0], [1.0, 2.0])
        np.testing.assert_array_equal(segs[1][1], [1.1, 2.0])

    def test_two_boundaries(self):
        t = np.array([0.0, 1.0, 1.0, 2.0, 2.0, 3.0])
        v = np.array([0.0, 0.9, 1.1, 1.9, 2.1, 3.0])
        segs = _split_segments(t, v, [(2, 2), (4, 4)])
        assert len(segs) == 3


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestDedupTimeSeries:
    def test_keep_first(self):
        t = np.array([0.0, 1.0, 1.0, 2.0])
        v = np.array([0.0, 0.9, 1.1, 2.0])
        t_out, v_out = _dedup_time_series(t, v, keep="first")
        np.testing.assert_array_equal(t_out, [0.0, 1.0, 2.0])
        np.testing.assert_array_equal(v_out, [0.0, 0.9, 2.0])

    def test_keep_last(self):
        t = np.array([0.0, 1.0, 1.0, 2.0])
        v = np.array([0.0, 0.9, 1.1, 2.0])
        t_out, v_out = _dedup_time_series(t, v, keep="last")
        np.testing.assert_array_equal(t_out, [0.0, 1.0, 2.0])
        np.testing.assert_array_equal(v_out, [0.0, 1.1, 2.0])

    def test_no_duplicates(self):
        t = np.array([0.0, 1.0, 2.0])
        v = np.array([0.0, 1.0, 2.0])
        t_out, v_out = _dedup_time_series(t, v, keep="first")
        np.testing.assert_array_equal(t_out, t)
        np.testing.assert_array_equal(v_out, v)

    def test_multiple_duplicates(self):
        t = np.array([0.0, 1.0, 1.0, 2.0, 2.0, 3.0])
        v = np.array([0.0, 0.9, 1.1, 1.9, 2.1, 3.0])
        t_out, v_out = _dedup_time_series(t, v, keep="last")
        np.testing.assert_array_equal(t_out, [0.0, 1.0, 2.0, 3.0])
        np.testing.assert_array_equal(v_out, [0.0, 1.1, 2.1, 3.0])


# ---------------------------------------------------------------------------
# Trajectory comparison (NRMSE)
# ---------------------------------------------------------------------------


class TestCompareTrajectories:
    """Tests for the core NRMSE comparison with event handling."""

    def test_identical_smooth(self):
        """Identical signals without events => NRMSE = 0."""
        t = np.array([0.0, 0.5, 1.0, 1.5, 2.0])
        v = np.array([0.0, 0.5, 1.0, 1.5, 2.0])
        vc = score({"mode": "nrmse"}, t, v, t, v, tolerance=1e-4)
        assert vc.passed is True
        assert vc.nrmse == 0.0
        assert vc.rmse == 0.0

    def test_identical_with_single_event(self):
        """Identical signals with one event => NRMSE = 0."""
        t = np.array([0.0, 0.5, 1.0, 1.0, 1.5, 2.0])
        v = np.array([0.0, 0.5, 0.9, 1.1, 1.5, 2.0])
        vc = score({"mode": "nrmse"}, t, v, t, v, tolerance=1e-4)
        assert vc.passed is True
        assert vc.nrmse == 0.0

    def test_identical_with_multiple_events(self):
        """Identical signals with multiple events => NRMSE = 0."""
        t = np.array([0.0, 0.5, 1.0, 1.0, 1.5, 2.0, 2.0, 2.5, 3.0])
        v = np.array([0.0, 0.5, 0.9, 1.1, 1.5, 1.9, 2.1, 2.5, 3.0])
        vc = score({"mode": "nrmse"}, t, v, t, v, tolerance=1e-4)
        assert vc.passed is True
        assert vc.nrmse == 0.0

    def test_identical_three_events(self):
        """Identical signals with three events (double time points) => NRMSE = 0."""
        t = np.array([0.0, 1.0, 1.0, 2.0, 2.0, 3.0, 3.0, 4.0])
        v = np.array([0.0, 0.9, 1.1, 1.9, 2.1, 2.9, 3.1, 4.0])
        vc = score({"mode": "nrmse"}, t, v, t, v, tolerance=1e-4)
        assert vc.passed is True
        assert vc.nrmse == 0.0

    def test_identical_triple_time_points(self):
        """Identical signals with triple time points (Dymola format) => NRMSE = 0."""
        t = np.array(
            [0.0, 1.0, 2.0, 2.0, 2.0, 3.0, 5.0, 5.0, 5.0, 6.0, 8.0, 8.0, 8.0, 9.0, 10.0]
        )
        v = np.array(
            [0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0, 2.0, 2.0, 2.0, 2.0, 3.0, 3.0, 3.0]
        )
        vc = score({"mode": "nrmse"}, t, v, t, v, tolerance=1e-4)
        assert vc.passed is True
        assert vc.nrmse == 0.0

    def test_different_signals(self):
        """Different signals => non-zero NRMSE, should fail."""
        t = np.array([0.0, 0.5, 1.0, 1.5, 2.0])
        ref_v = np.array([0.0, 0.5, 1.0, 1.5, 2.0])
        act_v = np.array([0.0, 0.5, 1.0, 1.5, 2.5])
        vc = score({"mode": "nrmse"}, t, ref_v, t, act_v, tolerance=1e-4)
        assert vc.passed is False
        assert vc.nrmse > 0
        assert vc.max_abs_error == pytest.approx(0.5)
        assert vc.max_abs_error_time == pytest.approx(2.0)

    def test_different_with_event(self):
        """Different signals with event => non-zero NRMSE."""
        t = np.array([0.0, 0.5, 1.0, 1.0, 1.5, 2.0])
        ref_v = np.array([0.0, 0.5, 0.9, 1.1, 1.5, 2.0])
        act_v = np.array([0.0, 0.5, 0.9, 1.2, 1.6, 2.1])
        vc = score({"mode": "nrmse"}, t, ref_v, t, act_v, tolerance=1e-4)
        assert vc.passed is False
        assert vc.nrmse > 0
        assert vc.max_abs_error == pytest.approx(0.1)

    def test_constant_signal(self):
        """Constant signal => is_constant=True, passed=True."""
        t = np.array([0.0, 1.0, 2.0])
        v = np.array([5.0, 5.0, 5.0])
        vc = score({"mode": "nrmse"}, t, v, t, v, tolerance=1e-4)
        assert vc.passed is True
        assert vc.is_constant == True
        assert vc.nrmse == 0.0

    def test_constant_with_small_difference(self):
        """Near-constant signal with tiny deviation => normalized by magnitude."""
        t = np.array([0.0, 1.0, 2.0])
        ref_v = np.array([5.0, 5.0, 5.0])
        act_v = np.array([5.0, 5.0, 5.001])
        vc = score({"mode": "nrmse"}, t, ref_v, t, act_v, tolerance=1e-4)
        assert vc.is_constant == True
        # RMSE ≈ 5.77e-4, magnitude = 5.0, NRMSE ≈ 1.15e-4
        assert vc.nrmse > 1e-4  # Should fail
        assert vc.nrmse == pytest.approx(vc.rmse / 5.0)

    def test_constant_large_magnitude_float32_quantization(self):
        """Large-magnitude constant must not fail from float32 quantization.

        Dymola .mat files use float32. At 37e9, the nearest float32
        representable value differs by 512 — which is a relative error
        of ~1.4e-8, well within tolerance.
        """
        t = np.array([0.0, 1.0])
        ref_v = np.array([37e9, 37e9])  # float64
        act_v = np.array([np.float32(37e9)] * 2).astype(np.float64)  # 36999999488.0
        vc = score({"mode": "nrmse"}, t, ref_v, t, act_v, tolerance=1e-4)
        assert vc.is_constant == True
        assert vc.rmse == pytest.approx(512.0, abs=1.0)
        # Normalized by magnitude: 512 / 37e9 ≈ 1.4e-8
        assert vc.nrmse < 1e-4
        assert vc.passed

    def test_constant_zero_reference(self):
        """Constant zero reference with nonzero actual uses raw RMSE."""
        t = np.array([0.0, 1.0])
        ref_v = np.array([0.0, 0.0])
        act_v = np.array([0.001, 0.001])
        vc = score({"mode": "nrmse"}, t, ref_v, t, act_v, tolerance=1e-4)
        assert vc.is_constant == True
        # Magnitude is 0, so falls back to raw RMSE = 0.001
        assert vc.nrmse == pytest.approx(0.001)
        assert not vc.passed

    def test_different_time_grids(self):
        """Actual has finer time grid, same trajectory."""
        ref_t = np.array([0.0, 0.5, 1.0, 1.0, 1.5, 2.0])
        ref_v = np.array([0.0, 0.5, 1.0, 2.0, 2.5, 3.0])
        act_t = np.array([0.0, 0.25, 0.5, 0.75, 1.0, 1.0, 1.25, 1.5, 1.75, 2.0])
        act_v = np.array([0.0, 0.25, 0.5, 0.75, 1.0, 2.0, 2.25, 2.5, 2.75, 3.0])
        vc = score({"mode": "nrmse"}, ref_t, ref_v, act_t, act_v, tolerance=1e-4)
        assert vc.passed is True
        assert vc.nrmse == pytest.approx(0.0, abs=1e-10)

    def test_signal_range(self):
        """Signal range is computed from full reference including jumps."""
        t = np.array([0.0, 1.0, 1.0, 2.0])
        v = np.array([0.0, 0.0, 10.0, 10.0])
        vc = score({"mode": "nrmse"}, t, v, t, v, tolerance=1e-4)
        assert vc.signal_range == pytest.approx(10.0)

    def test_reference_and_actual_finals(self):
        """Final values are correctly reported."""
        t = np.array([0.0, 1.0, 2.0])
        ref_v = np.array([0.0, 1.0, 2.0])
        act_v = np.array([0.0, 1.0, 2.5])
        vc = score({"mode": "nrmse"}, t, ref_v, t, act_v, tolerance=1.0)
        assert vc.reference_final == pytest.approx(2.0)
        assert vc.actual_final == pytest.approx(2.5)

    def test_within_tolerance_passes(self):
        """Signal with small difference within tolerance passes."""
        t = np.linspace(0, 10, 101)
        ref_v = np.sin(t)
        act_v = np.sin(t) + 0.001 * np.random.default_rng(42).standard_normal(len(t))
        vc = score({"mode": "nrmse"}, t, ref_v, t, act_v, tolerance=0.01)
        assert vc.passed is True

    def test_an_nrmse_exactly_at_the_tolerance_fails(self):
        """A pass is nrmse strictly below the tolerance."""
        # rmse 0.5 over a range of 1: the nrmse is 0.5 exactly.
        t = np.array([0.0, 1.0])
        vc = score(
            {"mode": "nrmse"}, t, np.array([0.0, 1.0]), t, np.array([0.5, 1.5]), 0.5
        )
        assert vc.nrmse == 0.5
        assert vc.passed is False

    def test_the_last_segment_reads_the_actuals_value_after_an_event_the_reference_lacks(
        self,
    ):
        """The actual jumps 6 -> 9 at t=2, an event the reference does not
        have: the reference's t=2 sample is scored against 9, not 6."""
        vc = score(
            {"mode": "nrmse"},
            np.array([0.0, 1.0, 1.0, 2.0, 3.0]),
            np.array([0.0, 1.0, 5.0, 6.0, 7.0]),
            np.array([0.0, 1.0, 1.0, 2.0, 2.0, 3.0]),
            np.array([0.0, 1.0, 5.0, 6.0, 9.0, 7.0]),
            tolerance=0.01,
        )
        # One error of 3 over the five reference samples, over a range of 7.
        assert vc.nrmse == pytest.approx(np.sqrt(9 / 5) / 7)
        assert vc.passed is False

    def test_a_one_sample_reference_is_scored_not_refused(self):
        vc = score(
            {"mode": "nrmse"},
            np.array([0.0]),
            np.array([2.0]),
            np.array([0.0, 1.0]),
            np.array([2.0, 2.0]),
        )
        assert "error" not in (vc.diagnostics or {})
        assert vc.passed is True
        assert vc.nrmse == 0.0
        assert vc.reference_final == 2.0


# ---------------------------------------------------------------------------
# Final value comparison
# ---------------------------------------------------------------------------


class TestCompareFinalValues:
    """The points mode with no points: its final-value-only check."""

    _t = np.array([0.0, 1.0])

    def test_identical(self):
        vc = score(
            {"mode": "points"},
            self._t,
            np.array([1.0, 1.0]),
            self._t,
            np.array([1.0, 1.0]),
            tolerance=1e-4,
        )
        assert vc.passed is True
        assert vc.nrmse == 0.0

    def test_different(self):
        vc = score(
            {"mode": "points"},
            self._t,
            np.array([1.0, 1.0]),
            self._t,
            np.array([1.1, 1.1]),
            tolerance=1e-4,
        )
        assert vc.passed is False
        assert vc.nrmse == pytest.approx(0.1)

    def test_zero_reference(self):
        """Near-zero reference still measures absolute delta."""
        vc = score(
            {"mode": "points"},
            self._t,
            np.array([0.0, 0.0]),
            self._t,
            np.array([0.0001, 0.0001]),
            tolerance=1e-4,
        )
        assert vc.passed is False

    def test_both_zero(self):
        vc = score(
            {"mode": "points"},
            self._t,
            np.array([0.0, 0.0]),
            self._t,
            np.array([0.0, 0.0]),
            tolerance=1e-4,
        )
        assert vc.passed is True
        assert vc.nrmse == 0.0


# ---------------------------------------------------------------------------
# Structural change detection
# ---------------------------------------------------------------------------


class TestStructuralChanges:
    def test_no_changes(self):
        ref = {
            "statistics": {
                "translation": {"continuous_time_states": 4, "nonlinear": "3, 1"},
                "EventCounter": 42,
            }
        }
        result = TestResult(
            model_id="Test",
            success=True,
            statistics={
                "translation": {"continuous_time_states": 4, "nonlinear": "3, 1"},
                "EventCounter": 42,
            },
        )
        warnings = _check_structural_changes(decode_reference(ref), result)
        assert len(warnings) == 0

    def test_continuous_states_change(self):
        ref = {"statistics": {"translation": {"continuous_time_states": 4}}}
        result = TestResult(
            model_id="Test",
            success=True,
            statistics={"translation": {"continuous_time_states": 6}},
        )
        warnings = _check_structural_changes(decode_reference(ref), result)
        assert len(warnings) == 1
        assert "Continuous" in warnings[0].field

    def test_nonlinear_change(self):
        ref = {"statistics": {"translation": {"nonlinear_count": 3}}}
        result = TestResult(
            model_id="Test",
            success=True,
            statistics={"translation": {"nonlinear_count": 5}},
        )
        warnings = _check_structural_changes(decode_reference(ref), result)
        assert len(warnings) == 1
        assert "Nonlinear" in warnings[0].field
        assert warnings[0].reference_value == "3"
        assert warnings[0].current_value == "5"

    def test_event_counter_change(self):
        """The event count is the EventCounter summary's final against the
        run's diagnostic's — not a statistic of that name."""
        ref = {
            "statistics": {"EventCounter": 50},
            "diagnostics": [{"name": "EventCounter", "final": 42.0}],
        }
        result = TestResult(
            model_id="Test",
            success=True,
            diagnostics=[
                VariableResult(
                    index=1,
                    time=np.array([0.0, 1.0]),
                    values=np.array([0.0, 50.0]),
                    name="EventCounter",
                )
            ],
            statistics={"EventCounter": 42},
        )
        warnings = _check_structural_changes(decode_reference(ref), result)
        assert [(w.field, w.reference_value, w.current_value) for w in warnings] == [
            ("Event count", "42.0", "50.0")
        ]

    def test_missing_stats_no_crash(self):
        """Missing statistics on either side should not crash."""
        ref = {"statistics": {}}
        result = TestResult(model_id="Test", success=True, statistics=None)
        warnings = _check_structural_changes(decode_reference(ref), result)
        assert len(warnings) == 0

    def test_no_statistics_key(self):
        ref = {}
        result = TestResult(model_id="Test", success=True, statistics={})
        warnings = _check_structural_changes(decode_reference(ref), result)
        assert len(warnings) == 0


# ---------------------------------------------------------------------------
# Tolerance resolution
# ---------------------------------------------------------------------------

from pathlib import Path

from dstf.backends.types import VariableResult
from dstf.comparison.algorithms import _interpolate_tube_widths
from dstf.comparison.comparator import compare_test
from dstf.discovery.test_registry import TestModel


def _make_test(comparison_tolerance=None, variable_overrides=None):
    """Create a minimal TestModel for tolerance tests.

    Tracked by its spec: the result file names its one variable ``x`` (a
    UnitTests test's would be ``unitTests.x[1]``, whatever the model wrote
    for it)."""
    return TestModel(
        model_id="Test.Model",
        source_file=None,
        source_package="Test",
        short_name="Model",
        n_vars=0,
        source="spec",
        variable_patterns=["x"],
        comparison_tolerance=comparison_tolerance,
        variable_overrides=variable_overrides or {},
    )


def _make_result_and_ref(offset=0.005):
    """Create a result and reference with a known difference."""
    time = np.array([0.0, 0.5, 1.0])
    values = np.array([1.0, 2.0, 3.0])
    shifted = values + offset

    result = TestResult(
        model_id="Test.Model",
        success=True,
        variables=[VariableResult(index=1, name="x", time=time, values=shifted)],
    )
    reference = {
        "test_id": "0001",
        "time": time.tolist(),
        "variables": [{"index": 1, "name": "x", "values": values.tolist()}],
    }
    return result, reference


class TestToleranceResolution:
    def test_global_tolerance_default(self):
        """Config tolerance used when no overrides."""
        test = _make_test()
        result, ref = _make_result_and_ref(offset=0.001)
        comp = compare_test(test, result, decode_reference(ref), default_tolerance=1e-4)
        # offset=0.001 on range=2.0 → NRMSE=5e-4, exceeds 1e-4
        assert not comp.passed
        assert comp.variables[0].tolerance_used == Sides(1e-4, 1e-4)

    def test_per_test_tolerance_overrides_config(self):
        """Per-test comparison_tolerance overrides config.tolerance."""
        test = _make_test(comparison_tolerance=0.01)
        result, ref = _make_result_and_ref(offset=0.001)
        comp = compare_test(test, result, decode_reference(ref), default_tolerance=1e-4)
        # NRMSE=5e-4, tolerance=0.01 → passes
        assert comp.passed
        assert comp.variables[0].tolerance_used == Sides(0.01, 0.01)

    def test_per_variable_tolerance_overrides_test(self):
        """Per-variable override takes precedence over per-test."""
        test = _make_test(
            comparison_tolerance=1e-6,  # Very tight
            variable_overrides={"x": {"tolerance": 0.01}},  # But x is loose
        )
        result, ref = _make_result_and_ref(offset=0.001)
        comp = compare_test(test, result, decode_reference(ref), default_tolerance=1e-4)
        # Per-variable 0.01 used for x → passes despite tight per-test
        assert comp.passed
        assert comp.variables[0].tolerance_used == Sides(0.01, 0.01)

    def test_reference_tolerance_used_as_fallback(self):
        """Comparison tolerance stored in reference JSON is used as fallback."""
        test = _make_test()  # No comparison_tolerance set
        result, ref = _make_result_and_ref(offset=0.001)
        ref["comparison"] = {"tolerance": 0.01}
        comp = compare_test(test, result, decode_reference(ref), default_tolerance=1e-4)
        # ref comparison tolerance 0.01 used → passes
        assert comp.passed
        assert comp.variables[0].tolerance_used == Sides(0.01, 0.01)

    def test_reference_variable_overrides(self):
        """Per-variable overrides from reference JSON are used."""
        test = _make_test()
        result, ref = _make_result_and_ref(offset=0.001)
        ref["comparison"] = {
            "variable_overrides": {"x": {"tolerance": 0.01}},
        }
        comp = compare_test(test, result, decode_reference(ref), default_tolerance=1e-4)
        assert comp.passed
        assert comp.variables[0].tolerance_used == Sides(0.01, 0.01)

    def test_spec_overrides_reference(self):
        """Spec variable overrides take precedence over reference overrides."""
        test = _make_test(variable_overrides={"x": {"tolerance": 1e-6}})
        result, ref = _make_result_and_ref(offset=0.001)
        ref["comparison"] = {
            "variable_overrides": {"x": {"tolerance": 0.1}},  # Loose in ref
        }
        comp = compare_test(test, result, decode_reference(ref), default_tolerance=1e-4)
        # Spec override (1e-6) takes precedence over reference (0.1)
        assert not comp.passed
        assert comp.variables[0].tolerance_used == Sides(1e-6, 1e-6)


# ---------------------------------------------------------------------------
# Tube interpolation
# ---------------------------------------------------------------------------


class TestTubeInterpolation:
    def test_single_point_constant(self):
        """Single control point applies everywhere."""
        times = np.array([0.0, 50.0, 100.0])
        points = [{"time": 0.0, "upper": 10.0, "lower": 10.0}]
        upper, lower = _interpolate_tube_widths(times, points)
        np.testing.assert_array_equal(upper, [10.0, 10.0, 10.0])
        np.testing.assert_array_equal(lower, [10.0, 10.0, 10.0])

    def test_points_without_upper_or_lower_are_rejected(self):
        """`abs` / `rel` control points are not a control-point format."""
        times = np.array([0.0, 50.0, 100.0])
        points = [{"time": 0.0, "abs": 10.0, "rel": 0.05}]
        with pytest.raises(ValueError, match="'upper' and/or 'lower'"):
            _interpolate_tube_widths(times, points)

    def test_linear_interpolation(self):
        """Linear interpolation between two points."""
        times = np.array([0.0, 50.0, 100.0])
        points = [
            {"time": 0.0, "upper": 10.0, "lower": 10.0},
            {"time": 100.0, "upper": 20.0, "lower": 20.0},
        ]
        upper, lower = _interpolate_tube_widths(times, points, "linear")
        np.testing.assert_allclose(upper, [10.0, 15.0, 20.0])
        np.testing.assert_allclose(lower, [10.0, 15.0, 20.0])

    def test_hold_at_boundaries(self):
        """Values before first and after last point are held."""
        times = np.array([0.0, 50.0, 100.0, 200.0])
        points = [
            {"time": 50.0, "upper": 10.0, "lower": 10.0},
            {"time": 100.0, "upper": 20.0, "lower": 20.0},
        ]
        upper, _ = _interpolate_tube_widths(times, points, "linear")
        assert upper[0] == 10.0  # Before first point: hold
        assert upper[3] == 20.0  # After last point: hold

    def test_constant_interpolation_stepwise(self):
        """Constant mode uses stepwise (hold previous)."""
        times = np.array([0.0, 25.0, 75.0, 100.0])
        points = [
            {"time": 0.0, "upper": 10.0, "lower": 10.0},
            {"time": 50.0, "upper": 20.0, "lower": 20.0},
        ]
        upper, _ = _interpolate_tube_widths(times, points, "constant")
        assert upper[0] == 10.0
        assert upper[1] == 10.0
        assert upper[2] == 20.0
        assert upper[3] == 20.0

    def test_asymmetric_upper_lower(self):
        """Asymmetric format with independent upper/lower."""
        times = np.array([0.0, 50.0, 100.0])
        points = [
            {"time": 0.0, "upper": 10.0, "lower": 5.0},
            {"time": 100.0, "upper": 20.0, "lower": 10.0},
        ]
        upper, lower = _interpolate_tube_widths(times, points, "linear")
        np.testing.assert_allclose(upper, [10.0, 15.0, 20.0])
        np.testing.assert_allclose(lower, [5.0, 7.5, 10.0])


# ---------------------------------------------------------------------------
# Tube comparison
# ---------------------------------------------------------------------------


class TestTubeComparison:
    def test_constant_tube_passes(self):
        """Signal inside constant tube passes."""
        ref_time = np.array([0.0, 0.5, 1.0])
        ref_values = np.array([100.0, 200.0, 300.0])
        act_values = np.array([101.0, 199.0, 302.0])  # Within ±5

        vc = score(
            {"mode": "tube", "tube_width_mode": "band", "tube_abs": 5.0},
            ref_time,
            ref_values,
            ref_time,
            act_values,
        )
        assert vc.passed
        assert vc.mode == "tube"
        assert vc.tube_points_inside == 1.0

    def test_constant_tube_fails(self):
        """Signal outside constant tube fails."""
        ref_time = np.array([0.0, 0.5, 1.0])
        ref_values = np.array([100.0, 200.0, 300.0])
        act_values = np.array([100.0, 200.0, 310.0])  # Last point outside ±5

        vc = score(
            {"mode": "tube", "tube_width_mode": "band", "tube_abs": 5.0},
            ref_time,
            ref_values,
            ref_time,
            act_values,
        )
        assert not vc.passed
        assert vc.tube_points_inside < 1.0
        assert vc.tube_worst_violation == pytest.approx(5.0, abs=0.01)
        assert vc.tube_worst_violation_time == 1.0

    def test_relative_tube(self):
        """Relative tube scales with reference magnitude."""
        ref_time = np.array([0.0, 1.0])
        ref_values = np.array([100.0, 1000.0])
        # At t=0: |ref|=100, rel=0.05 → width=5. Offset=4 → inside
        # At t=1: |ref|=1000, rel=0.05 → width=50. Offset=40 → inside
        act_values = np.array([104.0, 1040.0])

        vc = score(
            {"mode": "tube", "tube_width_mode": "rel", "tube_rel": 0.05},
            ref_time,
            ref_values,
            ref_time,
            act_values,
        )
        assert vc.passed

    def test_min_width_floors_rel_at_zero_crossing(self):
        """tube_min_width floors rel-mode width — prevents zero-width at zero crossing."""
        ref_time = np.array([0.0, 1.0])
        ref_values = np.array([0.0, 100.0])  # Crosses zero
        # At t=0: |ref|=0 so rel width = 0, but min_width=5 → effective width=5
        act_values = np.array([3.0, 104.0])  # Within ±5 at zero, ±5% at t=1

        vc = score(
            {
                "mode": "tube",
                "tube_width_mode": "rel",
                "tube_rel": 0.05,
                "tube_min_width": 5.0,
            },
            ref_time,
            ref_values,
            ref_time,
            act_values,
        )
        assert vc.passed

    def test_time_varying_tube(self):
        """Time-varying tube with control points."""
        ref_time = np.array([0.0, 50.0, 100.0])
        ref_values = np.array([10.0, 10.0, 10.0])
        # Tight at start (abs=1), loose at end (abs=10)
        act_values = np.array([10.5, 10.5, 18.0])

        vc = score(
            {
                "mode": "tube",
                "tube_width_mode": "band",
                "tube_points": [
                    {"time": 0.0, "upper": 1.0, "lower": 1.0},
                    {"time": 100.0, "upper": 10.0, "lower": 10.0},
                ],
                "tube_interpolation": "linear",
            },
            ref_time,
            ref_values,
            ref_time,
            act_values,
        )
        # t=0: width=1, error=0.5 → inside
        # t=50: width=5.5, error=0.5 → inside
        # t=100: width=10, error=8 → inside
        assert vc.passed

    def test_time_varying_tube_fails_at_tight_end(self):
        """Fails when signal exceeds narrow part of tube."""
        ref_time = np.array([0.0, 50.0, 100.0])
        ref_values = np.array([10.0, 10.0, 10.0])
        act_values = np.array([12.0, 10.0, 10.0])  # 2.0 offset at tight end

        vc = score(
            {
                "mode": "tube",
                "tube_width_mode": "band",
                "tube_points": [
                    {"time": 0.0, "upper": 1.0, "lower": 1.0},
                    {"time": 100.0, "upper": 10.0, "lower": 10.0},
                ],
            },
            ref_time,
            ref_values,
            ref_time,
            act_values,
        )
        # t=0: width=1, error=2 → outside (violation=1.0)
        assert not vc.passed
        assert vc.tube_worst_violation == pytest.approx(1.0)
        assert vc.tube_worst_violation_time == 0.0

    def test_nrmse_still_computed_in_tube_mode(self):
        """NRMSE is computed even in tube mode for reporting."""
        ref_time = np.array([0.0, 1.0])
        ref_values = np.array([0.0, 10.0])
        act_values = np.array([1.0, 11.0])

        vc = score(
            {"mode": "tube", "tube_width_mode": "band", "tube_abs": 5.0},
            ref_time,
            ref_values,
            ref_time,
            act_values,
        )
        assert vc.nrmse > 0
        assert vc.rmse > 0

    def test_tube_via_compare_test(self):
        """Tube mode dispatched correctly via compare_test."""
        test = _make_test(
            variable_overrides={
                "x": {
                    "mode": "tube",
                    "tube_width_mode": "band",
                    "tube_abs": 0.01,
                },
            }
        )
        result, ref = _make_result_and_ref(offset=0.005)
        comp = compare_test(test, result, decode_reference(ref), default_tolerance=1e-4)
        assert comp.passed
        assert comp.variables[0].mode == "tube"
        assert comp.variables[0].tube_points_inside == 1.0

    def test_asymmetric_tube_passes(self):
        """Asymmetric tube with different upper/lower widths."""
        ref_time = np.array([0.0, 1.0])
        ref_values = np.array([100.0, 100.0])
        # Signal is 3 above reference
        act_values = np.array([103.0, 103.0])

        # Upper allows 5, lower allows 1 → passes (signal is above)
        vc = score(
            {
                "mode": "tube",
                "tube_width_mode": "band",
                "tube_points": [
                    {"time": 0.0, "upper": 5.0, "lower": 1.0},
                ],
            },
            ref_time,
            ref_values,
            ref_time,
            act_values,
        )
        assert vc.passed

    def test_asymmetric_tube_fails_below(self):
        """Asymmetric tube catches signal below narrow lower bound."""
        ref_time = np.array([0.0, 1.0])
        ref_values = np.array([100.0, 100.0])
        # Signal is 3 below reference
        act_values = np.array([97.0, 97.0])

        # Upper allows 5, lower allows 1 → fails (3 > 1 below)
        vc = score(
            {
                "mode": "tube",
                "tube_width_mode": "band",
                "tube_points": [
                    {"time": 0.0, "upper": 5.0, "lower": 1.0},
                ],
            },
            ref_time,
            ref_values,
            ref_time,
            act_values,
        )
        assert not vc.passed

    def test_rel_mode_tube(self):
        """Tube with tube_width_mode='rel' scales by |reference|."""
        ref_time = np.array([0.0, 1.0])
        ref_values = np.array([100.0, 1000.0])
        # 2% relative tube
        # t=0: width = 0.02 * 100 = 2. offset=1 → inside
        # t=1: width = 0.02 * 1000 = 20. offset=10 → inside
        act_values = np.array([101.0, 1010.0])

        vc = score(
            {"mode": "tube", "tube_width_mode": "rel", "tube_rel": 0.02},
            ref_time,
            ref_values,
            ref_time,
            act_values,
        )
        assert vc.passed

    def test_min_width_floor(self):
        """tube_min_width prevents tube from collapsing at zero crossing."""
        ref_time = np.array([0.0, 1.0])
        ref_values = np.array([0.0, 100.0])  # Crosses zero
        act_values = np.array([0.5, 101.0])

        # Rel mode: at t=0, ref=0 → rel width = 0. Without floor, fails
        vc_no_floor = score(
            {"mode": "tube", "tube_width_mode": "rel", "tube_rel": 0.02},
            ref_time,
            ref_values,
            ref_time,
            act_values,
        )
        assert not vc_no_floor.passed

        # With min_width floor of 1.0
        vc_floor = score(
            {
                "mode": "tube",
                "tube_width_mode": "rel",
                "tube_rel": 0.02,
                "tube_min_width": 1.0,
            },
            ref_time,
            ref_values,
            ref_time,
            act_values,
        )
        assert vc_floor.passed
        assert vc_floor.tube_points_inside == 1.0


# ---------------------------------------------------------------------------
# Comparison mode strategies (modes.py)
# ---------------------------------------------------------------------------

from dstf.comparison.modes import (
    NrmseConfig,
    NrmseMode,
    PointsConfig,
    PointsMode,
    TubeConfig,
    TubeMode,
    resolve_mode,
)


class TestResolveMode:
    """Tests for resolve_mode() factory — mode selection logic."""

    def test_default_is_nrmse(self):
        """Empty override dict → NrmseMode."""
        mode = resolve_mode({}, tolerance=1e-4)
        assert isinstance(mode, NrmseMode)
        assert mode.name == "nrmse"
        assert mode.config.tolerance == 1e-4

    def test_explicit_nrmse(self):
        """mode='nrmse' → NrmseMode."""
        mode = resolve_mode({"mode": "nrmse"}, tolerance=0.01)
        assert isinstance(mode, NrmseMode)
        assert mode.config.tolerance == 0.01

    def test_explicit_tube(self):
        """mode='tube' → TubeMode with tube params extracted."""
        override = {
            "mode": "tube",
            "tube_abs": 5.0,
            "tube_rel": 0.02,
            "tube_width_mode": "band",
        }
        mode = resolve_mode(override, tolerance=1e-4)
        assert isinstance(mode, TubeMode)
        assert mode.name == "tube"
        assert mode.config.tube_abs == Sides(5.0, 5.0)
        assert mode.config.tube_rel == Sides(0.02, 0.02)
        assert mode.config.tube_width_mode == "band"

    def test_explicit_default_points(self):
        """mode='default_points' → PointsMode."""
        mode = resolve_mode({"mode": "points"}, tolerance=0.05)
        assert isinstance(mode, PointsMode)
        assert mode.name == "points"
        assert mode.config.tolerance == Sides(0.05, 0.05)

    def test_default_default_points_flag(self):
        """default_points=True with no explicit mode → PointsMode."""
        mode = resolve_mode({}, tolerance=1e-4, default_points=True)
        assert isinstance(mode, PointsMode)

    def test_tube_not_overridden_by_default_points(self):
        """Bug fix: explicit tube mode must NOT be overridden by default_points flag."""
        mode = resolve_mode(
            {"mode": "tube", "tube_abs": 1.0},
            tolerance=1e-4,
            default_points=True,
        )
        assert isinstance(mode, TubeMode)
        assert mode.name == "tube"

    def test_explicit_nrmse_not_overridden_by_default_points(self):
        """Explicit mode='nrmse' is respected even when default_points is True."""
        mode = resolve_mode(
            {"mode": "nrmse"},
            tolerance=1e-4,
            default_points=True,
        )
        assert isinstance(mode, NrmseMode)

    def test_tube_config_ignores_non_tube_keys(self):
        """Non-tube keys in override dict are not passed to TubeConfig."""
        override = {
            "mode": "tube",
            "tube_abs": 3.0,
            "tolerance": 0.5,  # Not a tube config key
            "some_other_key": True,
        }
        mode = resolve_mode(override, tolerance=0.5)
        assert isinstance(mode, TubeMode)
        assert mode.config.tube_abs == Sides(3.0, 3.0)

    def test_tube_with_time_varying_points(self):
        """Tube config with tube_points passes through correctly."""
        points = [
            {"time": 0.0, "upper": 1.0, "lower": 0.5},
            {"time": 100.0, "upper": 5.0, "lower": 2.0},
        ]
        override = {
            "mode": "tube",
            "tube_points": points,
            "tube_interpolation": "constant",
        }
        mode = resolve_mode(override, tolerance=1e-4)
        assert isinstance(mode, TubeMode)
        assert mode.config.tube_points == points
        assert mode.config.tube_interpolation == "constant"


class TestTubeConfigToDict:
    """Tests for TubeConfig.to_dict() round-trip."""

    def test_minimal(self):
        """Default TubeConfig produces empty dict."""
        cfg = TubeConfig()
        assert cfg.to_dict() == {}

    def test_band_mode(self):
        cfg = TubeConfig(tube_width_mode="band", tube_abs=5.0)
        d = cfg.to_dict()
        assert d["tube_width_mode"] == "band"
        assert d["tube_abs"] == Sides(5.0, 5.0)
        assert "tube_rel" not in d  # Zero values omitted

    def test_with_points(self):
        points = [{"time": 0.0, "upper": 1.0, "lower": 1.0}]
        cfg = TubeConfig(tube_points=points, tube_interpolation="constant")
        d = cfg.to_dict()
        assert d["tube_points"] == points
        assert d["tube_interpolation"] == "constant"


class TestModeCompare:
    """Integration: each mode produces correct VariableComparison."""

    ref_time = np.array([0.0, 0.5, 1.0])
    ref_values = np.array([1.0, 2.0, 3.0])
    act_values_close = np.array([1.0001, 2.0001, 3.0001])
    act_values_far = np.array([1.5, 2.5, 3.5])

    def test_nrmse_mode_pass(self):
        mode = NrmseMode(NrmseConfig(tolerance=0.01))
        vc = mode.compare(
            self.ref_time, self.ref_values, self.ref_time, self.act_values_close
        )
        assert vc.passed
        assert vc.mode == "nrmse"

    def test_nrmse_mode_fail(self):
        mode = NrmseMode(NrmseConfig(tolerance=1e-4))
        vc = mode.compare(
            self.ref_time, self.ref_values, self.ref_time, self.act_values_far
        )
        assert not vc.passed

    def test_tube_mode_pass(self):
        mode = TubeMode(TubeConfig(tube_width_mode="band", tube_abs=1.0))
        vc = mode.compare(
            self.ref_time, self.ref_values, self.ref_time, self.act_values_close
        )
        assert vc.passed
        assert vc.mode == "tube"
        assert vc.tube_points_inside == 1.0

    def test_tube_mode_fail(self):
        mode = TubeMode(TubeConfig(tube_width_mode="band", tube_abs=0.001))
        vc = mode.compare(
            self.ref_time, self.ref_values, self.ref_time, self.act_values_far
        )
        assert not vc.passed
        assert vc.tube_points_inside < 1.0

    def test_default_points_mode_pass(self):
        mode = PointsMode(PointsConfig(tolerance=0.01))
        vc = mode.compare(
            self.ref_time, self.ref_values, self.ref_time, self.act_values_close
        )
        assert vc.passed
        assert vc.mode == "points"

    def test_default_points_mode_fail(self):
        mode = PointsMode(PointsConfig(tolerance=1e-6))
        vc = mode.compare(
            self.ref_time, self.ref_values, self.ref_time, self.act_values_far
        )
        assert not vc.passed


class TestCompareTestWithModes:
    """End-to-end: compare_test dispatches via resolve_mode correctly."""

    def test_default_points_flag_does_not_override_tube(self):
        """Bug fix: default_points=True must not override mode='tube'."""
        test = _make_test(
            variable_overrides={
                "x": {"mode": "tube", "tube_width_mode": "band", "tube_abs": 0.01},
            }
        )
        result, ref = _make_result_and_ref(offset=0.005)
        comp = compare_test(
            test,
            result,
            decode_reference(ref),
            default_tolerance=1e-4,
            default_points=True,
        )
        assert comp.passed
        assert comp.variables[0].mode == "tube"

    def test_default_points_flag_applies_when_no_explicit_mode(self):
        """default_points=True applies to variables without explicit mode."""
        test = _make_test()
        result, ref = _make_result_and_ref(offset=0.005)
        # default_points compares only last values: ref=3.0, act=3.005
        # relative error = 0.005/3.0 ≈ 0.00167, tolerance=0.01 → pass
        comp = compare_test(
            test,
            result,
            decode_reference(ref),
            default_tolerance=0.01,
            default_points=True,
        )
        assert comp.passed


# ---------------------------------------------------------------------------
# Baseline-free short-circuit (idea #59 / D83)
# ---------------------------------------------------------------------------


class _NoBaselineStore:
    """Minimal ReferenceStore stand-in: no references on disk."""

    def get_reference(self, _model_id):
        return None

    folder = "Stub/linux"

    def get_soft_checks(self, _model_id):
        return {}

    def get_companions(self, _model_id):
        return {}


def _result_only(values: np.ndarray) -> TestResult:
    """Build a TestResult with a single variable 'x' on [0, 1]."""
    time = np.linspace(0.0, 1.0, len(values))
    return TestResult(
        model_id="Test.Model",
        success=True,
        variables=[VariableResult(index=1, name="x", time=time, values=values)],
    )


class TestBaselineFreeNoRef:
    """When every leaf is baseline-free, compare_all runs the scorer instead
    of collapsing to NO_REF. Verifies idea #59 / D83: range, declared-events,
    and declared-peaks leaves produce real pass/fail from config alone.
    """

    def test_range_only_test_passes_without_baseline(self):
        """Range leaf, signal in bounds, no baseline → PASS (not NO_REF)."""
        from dstf.comparison.comparator import compare_all

        test = _make_test(
            variable_overrides={
                "x": {"mode": "range", "min_value": 0.0, "max_value": 2.0},
            }
        )
        result = _result_only(np.array([0.1, 0.5, 1.0, 1.5, 1.9]))
        comps = compare_all([test], {test.model_id: result}, _NoBaselineStore())

        assert len(comps) == 1
        c = comps[0]
        assert c.passed is True
        # has_reference stays False — the badge still shows "no baseline"
        # context, but the tree ran and produced pass.
        assert c.has_reference is False
        assert c.metric_tree is not None
        assert c.variables, "baseline-free scorer should emit a VariableComparison"
        assert c.variables[0].mode == "range"

    def test_range_only_test_fails_when_bounds_violated(self):
        """Range leaf, signal exceeds max, no baseline → FAIL."""
        from dstf.comparison.comparator import compare_all

        test = _make_test(
            variable_overrides={
                "x": {"mode": "range", "min_value": 0.0, "max_value": 1.0},
            }
        )
        result = _result_only(np.array([0.1, 0.5, 1.0, 1.5, 2.0]))  # exceeds 1.0
        comps = compare_all([test], {test.model_id: result}, _NoBaselineStore())

        assert len(comps) == 1
        assert comps[0].passed is False
        assert comps[0].has_reference is False

    def test_mixed_tree_still_short_circuits_to_no_ref(self):
        """Range + NRMSE leaves → NO_REF (no partial-run support in this fix).

        Only when EVERY leaf is baseline-free do we bypass the guard. A
        single NRMSE leaf drags the whole test into NO_REF — matches the
        plan's "mixed trees keep current NO_REF behavior".
        """
        from dstf.comparison.comparator import compare_all

        test = _make_test(
            variable_overrides={
                "x": {"mode": "range", "min_value": 0.0, "max_value": 2.0},
                "y": {"tolerance": 1e-4},  # default NRMSE — needs a reference
            }
        )
        result = TestResult(
            model_id="Test.Model",
            success=True,
            variables=[
                VariableResult(
                    index=1,
                    name="x",
                    time=np.linspace(0, 1, 5),
                    values=np.array([0.1, 0.5, 1.0, 1.5, 1.9]),
                ),
                VariableResult(
                    index=2,
                    name="y",
                    time=np.linspace(0, 1, 5),
                    values=np.array([0.0, 0.25, 0.5, 0.75, 1.0]),
                ),
            ],
        )
        comps = compare_all([test], {test.model_id: result}, _NoBaselineStore())

        assert len(comps) == 1
        c = comps[0]
        # Legacy NO_REF shape: passed=True, has_reference=False, no tree.
        assert c.has_reference is False
        assert c.metric_tree is None
        assert c.error_message == "No reference baseline stored"

    def test_event_timing_declared_events_runs_without_baseline(self):
        """Event-timing with declared events → runs normally (no baseline)."""
        from dstf.comparison.comparator import compare_all

        # Build a signal with two events at t=0.3 and t=0.7 (duplicate-time
        # samples, Modelica convention).
        t = np.array([0.0, 0.1, 0.2, 0.3, 0.3, 0.5, 0.7, 0.7, 1.0])
        v = np.array([0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 0.0, 0.0])
        test = _make_test(
            variable_overrides={
                "x": {
                    "mode": "event-timing",
                    "events": [
                        {"time": 0.3, "tolerance": 0.01},
                        {"time": 0.7, "tolerance": 0.01},
                    ],
                    "count_must_match": True,
                },
            }
        )
        result = TestResult(
            model_id="Test.Model",
            success=True,
            variables=[VariableResult(index=1, name="x", time=t, values=v)],
        )
        comps = compare_all([test], {test.model_id: result}, _NoBaselineStore())

        assert len(comps) == 1
        c = comps[0]
        assert c.passed is True
        assert c.has_reference is False
        assert c.metric_tree is not None
        assert c.variables and c.variables[0].mode == "event-timing"

    def test_event_timing_auto_detect_still_short_circuits(self):
        """Event-timing WITHOUT declared events is not baseline-free — the
        auto-detect path pairs reference events with actual events, so a
        missing baseline collapses to NO_REF as before.
        """
        from dstf.comparison.comparator import compare_all

        test = _make_test(
            variable_overrides={
                "x": {"mode": "event-timing", "time_tolerance": 0.01},
            }
        )
        result = _result_only(np.array([0.0, 0.1, 0.2, 0.3, 0.4]))
        comps = compare_all([test], {test.model_id: result}, _NoBaselineStore())

        assert len(comps) == 1
        c = comps[0]
        assert c.has_reference is False
        assert c.metric_tree is None
        assert c.error_message == "No reference baseline stored"


class TestPointsDeclaredPath:
    """Declared-points semantics: per-point target, tolerance, and mode."""

    def _make_traj(self):
        # Piecewise trajectory: ref(0)=0, ref(1)=1, ref(2)=2, ref(3)=3,
        # ref(4)=4, ref(5)=5. Linear ramp y = t. Act has small offset
        # around t=2 to test pass/fail at a specific point.
        import numpy as np

        ref_t = np.array([0.0, 1.0, 2.0, 3.0, 4.0, 5.0])
        ref_v = np.array([0.0, 1.0, 2.0, 3.0, 4.0, 5.0])
        act_t = np.array([0.0, 1.0, 2.0, 3.0, 4.0, 5.0])
        act_v = np.array([0.0, 1.001, 2.05, 3.001, 4.001, 5.001])
        return ref_t, ref_v, act_t, act_v

    def test_implicit_final_check_unchanged(self):
        """Empty points list → behaves identically to legacy final-only."""

        ref_t, ref_v, act_t, act_v = self._make_traj()
        result = score({"mode": "points"}, ref_t, ref_v, act_t, act_v, tolerance=0.01)
        # act[-1] = 5.001, ref[-1] = 5.0, delta = 0.001 < 0.01 → PASS.
        assert result.passed
        assert result.diagnostics["delta"] == pytest.approx(0.001)

    def test_single_ref_relative_point_passes(self):
        ref_t, ref_v, act_t, act_v = self._make_traj()
        # Point at t=3: target = ref(3) = 3.0; act(3) = 3.001; delta = 0.001.
        # Tolerance 0.01 → PASS.
        result = score(
            {"mode": "points", "points": [{"time": 3.0}]},
            ref_t,
            ref_v,
            act_t,
            act_v,
            tolerance=0.01,
        )
        assert result.passed
        assert result.diagnostics["scored_points"] == 1
        assert result.diagnostics["worst_delta"] == pytest.approx(0.001)

    def test_single_ref_relative_point_fails(self):
        """Point at t=2 hits the 0.05 act offset → exceeds 0.01 tolerance."""

        ref_t, ref_v, act_t, act_v = self._make_traj()
        result = score(
            {"mode": "points", "points": [{"time": 2.0}]},
            ref_t,
            ref_v,
            act_t,
            act_v,
            tolerance=0.01,
        )
        assert not result.passed
        assert result.diagnostics["worst_delta"] == pytest.approx(0.05, abs=1e-9)

    def test_explicit_value_point_baseline_free(self):
        """Point with explicit ``value`` is baseline-free — passes any
        ref. Use a synthetic empty ref array to prove the scorer never
        reads it for absolute-value points.
        """
        import numpy as np

        # Empty ref arrays: an absolute-value point should still score.
        ref_t = np.array([])
        ref_v = np.array([])
        act_t = np.array([0.0, 1.0, 2.0, 3.0])
        act_v = np.array([0.0, 1.0, 2.0, 3.0])
        result = score(
            {
                "mode": "points",
                "points": [{"time": 2.0, "value": 2.0, "tolerance": 0.01}],
            },
            ref_t,
            ref_v,
            act_t,
            act_v,
            tolerance=1.0,
        )
        assert result.passed
        assert result.diagnostics["worst_delta"] == pytest.approx(0.0, abs=1e-9)

    def test_per_point_tolerance_overrides_global(self):
        """A wider per-point tolerance lets a point pass that the
        global tolerance would fail."""

        ref_t, ref_v, act_t, act_v = self._make_traj()
        # Point at t=2: delta=0.05. Global tolerance=0.01 (would fail);
        # per-point tolerance=0.1 (passes).
        result = score(
            {"mode": "points", "points": [{"time": 2.0, "tolerance": 0.1}]},
            ref_t,
            ref_v,
            act_t,
            act_v,
            tolerance=0.01,
        )
        assert result.passed

    def test_relative_tolerance_mode(self):
        """tolerance_mode='rel' scales tol by |target|."""

        ref_t, ref_v, act_t, act_v = self._make_traj()
        # Point at t=4: target=ref(4)=4.0; act(4)=4.001; delta=0.001.
        # rel-tolerance 0.001 → limit = 0.001 * 4 = 0.004 → PASS.
        # rel-tolerance 0.0001 → limit = 0.0001 * 4 = 0.0004 → FAIL.
        passing = score(
            {
                "mode": "points",
                "points": [{"time": 4.0, "tolerance": 0.001, "tolerance_mode": "rel"}],
            },
            ref_t,
            ref_v,
            act_t,
            act_v,
            tolerance=0.01,
        )
        failing = score(
            {
                "mode": "points",
                "points": [{"time": 4.0, "tolerance": 0.0001, "tolerance_mode": "rel"}],
            },
            ref_t,
            ref_v,
            act_t,
            act_v,
            tolerance=0.01,
        )
        assert passing.passed
        assert not failing.passed

    def test_null_time_resolves_to_trace_end(self):
        """time: null sentinel = the trace's final time (act_time[-1]
        when ref_time is empty, else ref_time[-1])."""

        ref_t, ref_v, act_t, act_v = self._make_traj()
        # Point at time=null: target = ref(5) = 5.0; act(5) = 5.001; delta=0.001.
        result = score(
            {"mode": "points", "points": [{"time": None, "tolerance": 0.01}]},
            ref_t,
            ref_v,
            act_t,
            act_v,
            tolerance=0.01,
        )
        assert result.passed
        assert result.diagnostics["worst_delta"] == pytest.approx(0.001)

    def test_a_time_less_point_without_a_reference_is_checked_at_the_actuals_end(
        self,
    ):
        vc = score(
            {"mode": "points", "points": [{"value": 2, "tolerance": 0.1}]},
            EMPTY,
            EMPTY,
            np.array([0.0, 1.0, 2.0]),
            np.array([0.0, 1.0, 2.0]),
        )
        assert vc.passed is True
        assert vc.max_abs_error == 0.0

    def test_multiple_points_all_must_match(self):
        """A leaf with 3 points fails if any one fails."""

        ref_t, ref_v, act_t, act_v = self._make_traj()
        # t=1 (delta=0.001 ✓), t=2 (delta=0.05 ✗), t=3 (delta=0.001 ✓).
        # Tolerance 0.01 → overall FAIL.
        result = score(
            {"mode": "points", "points": [{"time": 1.0}, {"time": 2.0}, {"time": 3.0}]},
            ref_t,
            ref_v,
            act_t,
            act_v,
            tolerance=0.01,
        )
        assert not result.passed
        assert result.diagnostics["scored_points"] == 3
        assert result.diagnostics["failed_points"] == 1

    def test_time_tolerance_passes_when_act_enters_box(self):
        """Box check: act curve must enter the [time±x_tol] × [target±y_lim]
        rectangle at least once. Construct an act curve that misses the
        target at t=3 exactly but hits it at t=2.95.
        """
        import numpy as np

        # ref doesn't matter — point has explicit value.
        ref_t = np.array([0.0, 5.0])
        ref_v = np.array([0.0, 0.0])
        # act curve crosses 30 around t=2.95.
        act_t = np.array([0.0, 2.5, 2.9, 2.95, 3.0, 3.5, 5.0])
        act_v = np.array([0.0, 20.0, 28.0, 30.0, 32.0, 35.0, 40.0])
        result = score(
            {
                "mode": "points",
                "points": [
                    {
                        "time": 3.0,
                        "value": 30.0,
                        "tolerance": 0.5,
                        "time_tolerance": 0.2,
                    }
                ],
            },
            ref_t,
            ref_v,
            act_t,
            act_v,
            tolerance=0.01,
        )
        # At t=3.0, act is 32 (delta=2 > 0.5). Strict-time would FAIL.
        # Box from [2.8, 3.2] × [29.5, 30.5]: act hits 30.0 at t=2.95 → PASS.
        assert result.passed
        assert result.diagnostics["worst_delta"] < 0.5

    def test_time_tolerance_fails_when_act_misses_box(self):
        """If the act curve never enters the box, point fails."""
        import numpy as np

        ref_t = np.array([0.0, 5.0])
        ref_v = np.array([0.0, 0.0])
        # act stays well above 30 across the whole [2.8, 3.2] window.
        act_t = np.array([0.0, 2.8, 3.0, 3.2, 5.0])
        act_v = np.array([0.0, 35.0, 36.0, 37.0, 40.0])
        result = score(
            {
                "mode": "points",
                "points": [
                    {
                        "time": 3.0,
                        "value": 30.0,
                        "tolerance": 0.5,
                        "time_tolerance": 0.2,
                    }
                ],
            },
            ref_t,
            ref_v,
            act_t,
            act_v,
            tolerance=0.01,
        )
        assert not result.passed
        # Worst delta within the box is at t=2.8 where act=35, target=30 → delta=5.
        assert result.diagnostics["worst_delta"] >= 5.0

    def test_time_tolerance_zero_degenerates_to_strict_time(self):
        """time_tolerance=0 must behave identically to a single-point check."""
        import numpy as np

        ref_t = np.array([0.0, 5.0])
        ref_v = np.array([0.0, 0.0])
        act_t = np.array([0.0, 2.5, 3.0, 5.0])
        act_v = np.array([0.0, 28.0, 32.0, 40.0])
        # act(3.0) = 32, target = 30, delta = 2 > 0.5 → FAIL with strict time.
        with_xtol = score(
            {
                "mode": "points",
                "points": [
                    {
                        "time": 3.0,
                        "value": 30.0,
                        "tolerance": 0.5,
                        "time_tolerance": 0.0,
                    }
                ],
            },
            ref_t,
            ref_v,
            act_t,
            act_v,
            tolerance=0.01,
        )
        without_xtol = score(
            {
                "mode": "points",
                "points": [{"time": 3.0, "value": 30.0, "tolerance": 0.5}],
            },
            ref_t,
            ref_v,
            act_t,
            act_v,
            tolerance=0.01,
        )
        # Both must give the same result.
        assert with_xtol.passed == without_xtol.passed == False
        assert (
            with_xtol.diagnostics["worst_delta"]
            == without_xtol.diagnostics["worst_delta"]
        )

    def test_time_tolerance_box_uses_interpolated_endpoints(self):
        """Box check evaluates the act curve at every sample inside
        [t_lo, t_hi] PLUS at the interpolated endpoints t_lo and t_hi
        — so we don't miss a curve entering between samples."""
        import numpy as np

        ref_t = np.array([0.0, 5.0])
        ref_v = np.array([0.0, 0.0])
        # Sparse act samples that bracket the target box.
        # Box: [2.5, 3.5] × [29.0, 31.0]. Samples at 2.0 and 4.0 with
        # values 20 and 40 → linear interp puts act(2.5) = 25, act(3.5) = 35.
        # Neither crosses 30 inside the box at a sample, but the
        # interpolated curve passes through (3.0, 30) which IS inside.
        act_t = np.array([0.0, 2.0, 4.0, 5.0])
        act_v = np.array([0.0, 20.0, 40.0, 50.0])
        result = score(
            {
                "mode": "points",
                "points": [
                    {
                        "time": 3.0,
                        "value": 30.0,
                        "tolerance": 1.0,
                        "time_tolerance": 0.5,
                    }
                ],
            },
            ref_t,
            ref_v,
            act_t,
            act_v,
            tolerance=0.01,
        )
        # Linear interp at t=3 gives act=30, delta=0 → PASS.
        assert result.passed

    @pytest.mark.parametrize(
        "row,act_t,act_v",
        [
            ({"time": 1, "value": 2, "tolerance": 0.1}, [1], [2]),
            (
                {"time": 1.5, "value": 1, "tolerance": 0.01, "time_tolerance": 0.5},
                [0, 10],
                [0, 10],
            ),
            (
                {"time": 0.5, "value": 1, "tolerance": 0.01, "time_tolerance": 0.5},
                [0, 10],
                [0, 10],
            ),
            (
                {"time": 1, "value": 5, "tolerance": 0.1, "time_tolerance": 1},
                [0, 1, 2],
                [0, 5, 0],
            ),
            (
                {
                    "time": 0,
                    "value": 0,
                    "tolerance": 0.5,
                    "time_tolerance": {"lower": 0, "upper": 0.5},
                },
                [0, 0, 1],
                [0, 10, 10],
            ),
            (
                {"time": 0.5, "value": 10, "tolerance": 0.5, "time_tolerance": 0.5},
                [0, 1, 1, 2],
                [0, 10, 0, 0],
            ),
        ],
        ids=[
            "one-sample actual",
            "box start between samples",
            "box end between samples",
            "a sample inside the box",
            "both samples of an event at the box start",
            "both samples of an event at the box end",
        ],
    )
    def test_the_box_meets_the_target_at_any_of_its_points(self, row, act_t, act_v):
        """The interpolated box ends, and every sample between them (both of
        an event's), are where the curve may meet the target."""
        vc = score(
            {"mode": "points", "points": [row]},
            EMPTY,
            EMPTY,
            np.array(act_t, dtype=float),
            np.array(act_v, dtype=float),
        )
        assert vc.passed is True
        assert vc.max_abs_error == 0.0

    @pytest.mark.parametrize(
        "row,act_t,act_v",
        [
            ({"time": 1, "value": 1}, [0, 2], [0, 2]),
            ({"time": 1, "value": 1}, [0, 2], [2, 0]),
            ({"time": 1, "value": 5}, [0, 1, 1, 2], [0, 0, 10, 10]),
        ],
        ids=["rising", "falling", "at an event"],
    )
    def test_a_curve_that_crosses_the_target_inside_the_box_meets_it(
        self, row, act_t, act_v
    ):
        vc = score(
            {
                "mode": "points",
                "points": [{**row, "tolerance": 0.1, "time_tolerance": 1}],
            },
            EMPTY,
            EMPTY,
            np.array(act_t, dtype=float),
            np.array(act_v, dtype=float),
        )
        assert vc.passed is True
        assert vc.max_abs_error == 0.0


# ---------------------------------------------------------------------------
# Tree-path config errors are labeled leaf failures, not crashes (D107)
# ---------------------------------------------------------------------------


class TestTreeConfigErrorIsALeafFailure:
    """A misconfigured leaf fails that leaf and lets the run finish.

    The flat ``variable_overrides`` path has always caught ValueError from
    ``resolve_mode`` and failed only the offending variable. The tree path
    used to let it escape and abort all of ``compare_all`` with an anonymous
    traceback — one bad spec entry took the whole run down.
    """

    def _tree_test(self, metrics_raw, model_id="Test.Bad"):
        from dstf.comparison.tree_spec import parse_metric_tree

        return TestModel(
            model_id=model_id,
            source_file=None,
            source_package="Test",
            short_name="Model",
            n_vars=1,
            metric_tree_spec=parse_metric_tree(metrics_raw),
        )

    def _two_test_run(self, bad_leaf):
        """One misconfigured test + one healthy test, through compare_all."""
        from dstf.comparison.comparator import compare_all

        class _StoreWithRef:
            """Minimal ReferenceStore stand-in serving one flat primary."""

            def get_reference(self, _model_id):
                return decode_reference(
                    {
                        "test_id": "0001",
                        "time": [0.0, 0.25, 0.5, 0.75, 1.0],
                        "variables": [
                            {
                                "index": 1,
                                "name": "x",
                                "values": [0.1, 0.5, 1.0, 1.5, 1.9],
                            }
                        ],
                    }
                )

            folder = "Stub/linux"

            def get_soft_checks(self, _model_id):
                return {}

            def get_companions(self, _model_id):
                return {}

        bad = self._tree_test(
            {
                "combinator": "and",
                "children": [
                    bad_leaf,
                    {
                        "metric": "range",
                        "variable": "x",
                        "min_value": 0.0,
                        "max_value": 2.0,
                    },
                ],
            },
            model_id="Test.Bad",
        )
        good = self._tree_test(
            {"metric": "range", "variable": "x", "min_value": 0.0, "max_value": 2.0},
            model_id="Test.Good",
        )
        values = np.array([0.1, 0.5, 1.0, 1.5, 1.9])
        results = {
            "Test.Bad": _result_only(values),
            "Test.Good": _result_only(values),
        }
        results["Test.Bad"].model_id = "Test.Bad"
        results["Test.Good"].model_id = "Test.Good"
        # No exception may escape: the whole run must complete.
        comps = compare_all([bad, good], results, _StoreWithRef())
        by_id = {c.model_id: c for c in comps}
        return by_id["Test.Bad"], by_id["Test.Good"]

    def _error_text(self, comp) -> str:
        """The raised message rides on the leaf's VariableComparison."""
        assert comp.metric_tree is not None
        bad_leaf = comp.metric_tree.children[0]
        assert not bad_leaf.passed
        vc = bad_leaf.diagnostics["variable"]
        # The name is the variable's — the reason is a diagnostic, not a
        # label — and it says the metric could not be built.
        assert vc.name == "x"
        error = str(vc.diagnostics.get("error", ""))
        assert "misconfigured" in error
        return error

    def test_range_leaf_with_no_bound(self):
        """A range that declares neither bound → leaf failure naming the keys.
        (The deleted `min` / `max` shorthand, which used to end here too, is
        refused where the tree is parsed: a key nothing reads.)"""
        from dstf.comparison.tree_spec import MetricSpecError

        with pytest.raises(MetricSpecError, match='unknown field "min"; the fields'):
            self._tree_test({"metric": "range", "variable": "x", "min": 0, "max": 2})
        bad, good = self._two_test_run({"metric": "range", "variable": "x"})
        assert not bad.passed
        # The sibling leaf still evaluated, and the other test still ran.
        assert bad.metric_tree.children[1].passed
        assert good.passed
        assert "min_value / max_value" in self._error_text(bad)

    def test_scalar_leaf_without_quantity(self):
        bad, good = self._two_test_run({"metric": "scalar", "variable": "x"})
        assert not bad.passed
        assert good.passed
        assert "quantity" in self._error_text(bad)

    def test_tube_leaf_whose_control_points_give_no_width(self):
        """Raised from mode.compare, not resolve_mode — the second catch.
        (A row of the old symmetric `abs` / `rel` shorthand is refused at
        the parse now; a row with no width at all still parses.)"""
        from dstf.comparison.tree_spec import MetricSpecError

        with pytest.raises(
            MetricSpecError, match='tube_points row 1: unknown field "abs"'
        ):
            self._tree_test(
                {
                    "metric": "tube",
                    "variable": "x",
                    "tube_width_mode": "band",
                    "tube_points": [{"time": 0.0, "abs": 1.0, "rel": 0.0}],
                }
            )
        bad, good = self._two_test_run(
            {
                "metric": "tube",
                "variable": "x",
                "tube_width_mode": "band",
                "tube_points": [{"time": 0.0}],
            }
        )
        assert not bad.passed
        assert good.passed
        assert "'upper' and/or 'lower'" in self._error_text(bad)


# =========================================================================
# Folded in from tests/test_review_fixes_verdict.py — regression tests for the
# 2026-07-06 review, Theme 1: verdict integrity.
#
# Decisions encoded here:
#
# * "Nothing to compare" (empty window slice, empty baseline, empty actual)
#   is a hard FAIL with a diagnostic — never a vacuous pass, never a crash.
# * A declared checkpoint whose time-box lies outside the actual trajectory
#   FAILS that point (a truncated simulation must not pass its tail checks).
# * An explicit tolerance of 0 is honored, not replaced by a default.
# * Reference variables pair to result variables by NAME, falling back to
#   index only when names are unavailable.
# =========================================================================


from dstf.comparison.comparator import _test_is_baseline_free, compare_all


def _mk_test(model_id="MyLib.Test", **kw) -> TestModel:
    return TestModel(
        model_id=model_id,
        source_file=Path("x.mo"),
        source_package="MyLib",
        short_name="Test",
        n_vars=1,
        **kw,
    )


def _mk_result(name="x", time=None, values=None, index=1) -> TestResult:
    time = np.linspace(0.0, 10.0, 11) if time is None else np.asarray(time, float)
    values = np.ones_like(time) if values is None else np.asarray(values, float)
    return TestResult(
        model_id="MyLib.Test",
        success=True,
        variables=[VariableResult(index=index, time=time, values=values, name=name)],
    )


EMPTY = np.array([])


# ---------------------------------------------------------------------------
# Empty data → hard fail (never vacuous pass, never crash)
# ---------------------------------------------------------------------------


class TestEmptyDataFails:
    def test_nrmse_empty_reference_fails(self):
        vc = score(
            {"mode": "nrmse"},
            EMPTY,
            EMPTY,
            np.array([0.0, 1.0]),
            np.array([5.0, 5.0]),
            tolerance=1e-4,
        )
        assert not vc.passed
        assert "error" in vc.diagnostics

    def test_nrmse_empty_actual_fails_without_crashing(self):
        vc = score(
            {"mode": "nrmse"},
            np.array([0.0, 1.0]),
            np.array([1.0, 1.0]),
            EMPTY,
            EMPTY,
            tolerance=1e-4,
        )
        assert not vc.passed

    def test_tube_empty_reference_fails_without_crashing(self):
        cfg = {"tube_width_mode": "band", "tube_abs": 0.5}
        vc = score(
            {"mode": "tube", **cfg},
            EMPTY,
            EMPTY,
            np.array([0.0, 1.0]),
            np.array([0.0, 0.0]),
        )
        assert not vc.passed

    def test_tube_empty_actual_fails_without_crashing(self):
        cfg = {"tube_width_mode": "band", "tube_abs": 0.5}
        vc = score(
            {"mode": "tube", **cfg},
            np.array([0.0, 1.0]),
            np.array([1.0, 1.0]),
            EMPTY,
            EMPTY,
        )
        assert not vc.passed

    def test_range_empty_actual_fails(self):
        vc = score(
            {"mode": "range", "min_value": 0.0, "max_value": 1.0},
            EMPTY,
            np.zeros_like(EMPTY),
            EMPTY,
            EMPTY,
        )
        assert not vc.passed
        assert "error" in vc.diagnostics

    def test_windowed_leaf_excluding_everything_fails(self):
        """A window that slices away every sample must fail the leaf."""
        from dstf.comparison.tree_spec import parse_metric_tree

        spec = parse_metric_tree(
            {
                "metric": "nrmse",
                "variable": "x",
                "window": {"start": 50.0, "end": 60.0},
            }
        )
        test = _mk_test(metric_tree_spec=spec)
        result = _mk_result()
        reference = {
            "time": [0.0, 5.0, 10.0],
            "variables": [
                {
                    "index": 1,
                    "name": "x",
                    "values": [1.0, 1.0, 1.0],
                }
            ],
        }
        comp = compare_test(test, result, decode_reference(reference))
        assert not comp.passed


# ---------------------------------------------------------------------------
# Points mode: clipped checkpoints fail
# ---------------------------------------------------------------------------


class TestPointsClipping:
    def test_fully_clipped_point_fails(self):
        """Checkpoint at t=90 on a sim that died at t=40 must FAIL, not skip."""
        ref_t = np.linspace(0.0, 100.0, 101)
        ref_v = ref_t.copy()
        act_t = np.linspace(0.0, 40.0, 41)
        act_v = act_t.copy()
        points = [
            {"time": 20.0, "tolerance": 0.5},
            {"time": 90.0, "tolerance": 0.5},
        ]
        vc = score({"mode": "points", "points": points}, ref_t, ref_v, act_t, act_v)
        assert not vc.passed
        assert vc.diagnostics.get("failed_points", 0) >= 1

    def test_empty_actual_with_declared_points_fails(self):
        ref_t = np.linspace(0.0, 100.0, 101)
        vc = score(
            {"mode": "points", "points": [{"time": 20.0}]}, ref_t, ref_t, EMPTY, EMPTY
        )
        assert not vc.passed

    def test_unclipped_points_still_pass(self):
        ref_t = np.linspace(0.0, 100.0, 101)
        act_t = np.linspace(0.0, 100.0, 201)
        vc = score(
            {"mode": "points", "points": [{"time": 20.0, "tolerance": 0.5}]},
            ref_t,
            ref_t,
            act_t,
            act_t,
        )
        assert vc.passed

    def test_ref_relative_point_beyond_reference_end_fails(self):
        """np.interp would clamp to ref_final — that target is untrustworthy."""
        ref_t = np.linspace(0.0, 40.0, 41)
        ref_v = ref_t.copy()  # still rising at truncation
        act_t = np.linspace(0.0, 100.0, 101)
        act_v = act_t.copy()
        vc = score(
            {"mode": "points", "points": [{"time": 90.0, "tolerance": 0.5}]},
            ref_t,
            ref_v,
            act_t,
            act_v,
        )
        assert not vc.passed

    @pytest.mark.parametrize(
        "rows,ref,act,details",
        [
            (
                [{"time": 1, "value": 1}, {"time": 1}, {"time": 2}],
                ([], []),
                ([0, 1, 2], [0, 1, 2]),
                (
                    ("checkpoints", "2 of 3 failed, 2 not scored"),
                    ("t=1", "no reference data for ref-relative point"),
                    ("t=2", "no reference data for ref-relative point"),
                ),
            ),
            (
                [{"time": 1, "value": 1}, {"time": 2}, {"time": 3}],
                ([0, 1], [0, 1]),
                ([0, 1, 2, 3], [0, 1, 2, 3]),
                (
                    ("checkpoints", "2 of 3 failed, 2 not scored"),
                    ("t=2", "checkpoint time outside reference range"),
                    ("t=3", "checkpoint time outside reference range"),
                ),
            ),
            (
                [{"time": 1}, {"time": 2}],
                ([0, 1, 2], [0, 1, 2]),
                ([], []),
                (
                    ("checkpoints", "2 of 2 failed, 2 not scored"),
                    ("t=1", "no actual samples (empty trajectory)"),
                    ("t=2", "no actual samples (empty trajectory)"),
                ),
            ),
            (
                [
                    {"time": 5, "value": 1},
                    {"time": 6, "value": 1},
                    {"time": 0.5, "value": 0.5},
                ],
                ([], []),
                ([0, 1], [0, 1]),
                (
                    ("checkpoints", "2 of 3 failed, 2 not scored"),
                    ("t=5", "checkpoint time outside actual trajectory"),
                    ("t=6", "checkpoint time outside actual trajectory"),
                ),
            ),
            (
                [{"value": 2, "tolerance": 0.1}],
                ([], []),
                ([], []),
                (
                    ("checkpoints", "1 of 1 failed, 1 not scored"),
                    ("t=0", "no actual samples (empty trajectory)"),
                ),
            ),
        ],
        ids=[
            "no reference",
            "outside the reference",
            "no actual",
            "beyond the actual",
            "nothing at all",
        ],
    )
    def test_each_row_that_could_not_be_scored_fails_and_is_listed(
        self, rows, ref, act, details
    ):
        vc = score(
            {"mode": "points", "points": rows},
            *(np.array(x, dtype=float) for x in (*ref, *act)),
        )
        assert vc.passed is False
        assert summarize(vc).details == details


# ---------------------------------------------------------------------------
# Tolerance resolution: explicit zero honored
# ---------------------------------------------------------------------------


class TestExplicitZeroTolerance:
    def test_zero_test_tolerance_fails_nonzero_error(self):
        test = _mk_test(comparison_tolerance=0.0, variable_patterns=["x"])
        result = _mk_result(values=np.ones(11) + 1e-6)
        reference = {
            "time": list(np.linspace(0.0, 10.0, 11)),
            "variables": [
                {
                    "index": 1,
                    "name": "x",
                    "values": [1.0] * 11,
                }
            ],
        }
        comp = compare_test(test, result, decode_reference(reference))
        assert not comp.passed
        assert comp.variables[0].tolerance_used == Sides(0.0, 0.0)

    def test_zero_reference_tolerance_honored(self):
        test = _mk_test(variable_patterns=["x"])
        result = _mk_result(values=np.ones(11) + 1e-6)
        reference = {
            "comparison": {"tolerance": 0.0},
            "time": list(np.linspace(0.0, 10.0, 11)),
            "variables": [
                {
                    "index": 1,
                    "name": "x",
                    "values": [1.0] * 11,
                }
            ],
        }
        comp = compare_test(test, result, decode_reference(reference))
        assert not comp.passed


# ---------------------------------------------------------------------------
# Baseline-free classification
# ---------------------------------------------------------------------------


class TestBaselineFreeClassification:
    def test_mixed_test_is_not_baseline_free(self):
        """A tracked variable without an override needs a baseline."""
        test = _mk_test(
            variable_patterns=["a", "b"],
            variable_overrides={
                "a": {"mode": "range", "min_value": -10, "max_value": 10}
            },
        )
        assert _test_is_baseline_free(test, 1e-4) is False

    def test_fully_covered_range_test_is_baseline_free(self):
        test = _mk_test(
            source="spec",
            variable_patterns=["a", "b"],
            variable_overrides={
                "a": {"mode": "range", "min_value": -10, "max_value": 10},
                "b": {"mode": "range", "min_value": 0, "max_value": 1},
            },
        )
        assert _test_is_baseline_free(test, 1e-4) is True


# ---------------------------------------------------------------------------
# Structural warnings: no phantoms when statistics are absent
# ---------------------------------------------------------------------------


class TestStructuralWarnings:
    def test_no_phantom_warnings_without_reference_statistics(self):
        result = TestResult(
            model_id="MyLib.Test",
            success=True,
            statistics={
                "translation": {"continuous_time_states": 5},
                "EventCounter": 3,
            },
        )
        assert _check_structural_changes(decode_reference({}), result) == []


# ---------------------------------------------------------------------------
# Name-based reference pairing
# ---------------------------------------------------------------------------


class TestNameBasedPairing:
    def test_reference_pairs_by_name_after_spec_reorder(self):
        """Baseline stored with [a, b]; 'a' later removed from the spec.
        'b' (now index 1) must score against ref 'b', not ref 'a'."""
        reference = {
            "time": [0.0, 1.0],
            "variables": [
                {"index": 1, "name": "a", "values": [0.0, 0.0]},
                {"index": 2, "name": "b", "values": [10.0, 10.0]},
            ],
        }
        result = TestResult(
            model_id="MyLib.Test",
            success=True,
            variables=[
                VariableResult(
                    index=1,
                    time=np.array([0.0, 1.0]),
                    values=np.array([10.0, 10.0]),
                    name="b",
                )
            ],
        )
        # The test tracks `b`: before a test that scores nothing stopped
        # being a PASS this one tracked no variable of its result, scored
        # nothing and passed whatever `b` was paired with.
        comp = compare_test(
            _mk_test(variable_patterns=["b"]), result, decode_reference(reference)
        )
        assert comp.passed and [v.name for v in comp.variables] == ["b"]
        assert comp.variables[0].nrmse == 0.0


# ---------------------------------------------------------------------------
# Unknown comparison mode: loud, not silent NRMSE
# ---------------------------------------------------------------------------


class TestUnknownMode:
    def test_resolve_mode_raises_on_unknown_mode(self):
        with pytest.raises(ValueError, match="[Uu]nknown.*mode"):
            resolve_mode({"mode": "Tube"}, 0.01)

    def test_typo_mode_fails_variable_not_silently_nrmse(self):
        test = _mk_test(
            variable_patterns=["x"],
            variable_overrides={"x": {"mode": "Tube", "tube_abs": 5.0}},
        )
        result = _mk_result()
        reference = {
            "time": list(np.linspace(0.0, 10.0, 11)),
            "variables": [
                {
                    "index": 1,
                    "name": "x",
                    "values": [1.0] * 11,
                }
            ],
        }
        comp = compare_test(test, result, decode_reference(reference))
        assert not comp.passed


# ---------------------------------------------------------------------------
# 2026-07-07 — sim-failure without a baseline must be SIM_FAIL, not NO_REF
# ---------------------------------------------------------------------------


class _NoRefStore:
    """Minimal ReferenceStore stub: every model has no stored baseline."""

    def get_reference(self, model_id):
        return None


class TestSimFailWithoutBaselineIsSimFail:
    """compare_all's no-baseline short-circuit ran BEFORE any sim-success
    check, so a test that failed to SIMULATE *and* has no baseline was masked
    as NO_REF with passed=True. OM's read_result returns a success=False
    TestResult (not None) for a missing .mat, so it slipped past the
    `result is None` guard. Surfaced across 91 OM/TRANSFORM tests (compile /
    solver failures on models that never got OM baselines)."""

    def test_failed_sim_no_baseline_is_sim_fail(self):
        test = _mk_test(model_id="MyLib.Failed")
        results = {
            "MyLib.Failed": TestResult(
                model_id="MyLib.Failed",
                success=False,
                error_message="Result file not found",
            )
        }
        (c,) = compare_all([test], results, _NoRefStore())
        assert c.sim_success is False  # was True (default) -> NO_REF
        assert c.passed is False  # was True

    def test_successful_sim_no_baseline_still_no_ref(self):
        """Regression guard: a SUCCESSFUL sim with no baseline must stay
        NO_REF (has_reference False, sim_success True) — the fix must not
        turn genuine no-baseline into SIM_FAIL."""
        test = _mk_test(model_id="MyLib.Ok")
        results = {
            "MyLib.Ok": TestResult(
                model_id="MyLib.Ok",
                success=True,
                variables=[
                    VariableResult(
                        index=1,
                        time=np.linspace(0.0, 1.0, 5),
                        values=np.ones(5),
                        name="x",
                    )
                ],
            )
        }
        (c,) = compare_all([test], results, _NoRefStore())
        assert c.sim_success is True
        assert c.has_reference is False

    def test_missing_result_object_still_sim_fail(self):
        """The pre-existing `result is None` path (backends that omit failed
        tests, e.g. Dymola with no .mat) must keep reporting SIM_FAIL."""
        test = _mk_test(model_id="MyLib.Gone")
        (c,) = compare_all([test], {}, _NoRefStore())
        assert c.sim_success is False
        assert c.passed is False


# ---------------------------------------------------------------------------
# compare_all's per-test body is callable for one test
# ---------------------------------------------------------------------------


class TestCompareOne:
    """compare_all's per-test body, callable for one test. `_mk_test`,
    `_mk_result`, `_make_test`, `_make_result_and_ref` and `_NoRefStore` are
    this module's helpers (model ids "MyLib.Test" and "Test.Model")."""

    def test_no_result_is_not_run(self):
        """D145: no result — no manifest entry, or one the last run never
        finished (the reader hands back nothing for it) — is NOT RUN, not
        SIM_FAIL, and not a failure."""
        from dstf.comparison.comparator import compare_one
        from dstf.reporting.status import FAILURES, NOT_RUN, classify

        comp = compare_one(_mk_test(), None, _NoRefStore())
        assert classify(comp) == NOT_RUN
        assert NOT_RUN not in FAILURES
        assert comp.error_message == "No simulation results found"

    def test_failed_result_is_sim_fail_before_the_baseline_check(self):
        from dstf.comparison.comparator import compare_one
        from dstf.reporting.status import SIM_FAIL, classify

        result = TestResult(model_id="MyLib.Test", success=False, error_message="boom")
        comp = compare_one(_mk_test(), result, _NoRefStore())
        assert comp.sim_success is False and comp.error_message == "boom"
        assert classify(comp) == SIM_FAIL

    def test_no_baseline_is_no_ref(self):
        from dstf.comparison.comparator import compare_one

        comp = compare_one(
            _mk_test(variable_patterns=["x"]), _mk_result(), _NoRefStore()
        )
        assert comp.has_reference is False and comp.passed is True
        assert not comp.evaluated

    def test_compare_all_is_compare_one_per_test(self):
        from dstf.comparison.comparator import compare_all, compare_one

        scored_result, ref = _make_result_and_ref(offset=0.5)  # a real FAIL

        class _Store:
            def get_reference(self, model_id):
                return decode_reference(ref) if model_id == "Test.Model" else None

            folder = "Stub/linux"

            def get_soft_checks(self, model_id):
                return {}

        tests = [
            _make_test(),
            _mk_test(model_id="MyLib.Test", variable_patterns=["x"]),
            _mk_test(model_id="MyLib.Gone"),
        ]
        results = {"Test.Model": scored_result, "MyLib.Test": _mk_result()}
        store = _Store()
        fields = ("model_id", "passed", "sim_success", "has_reference", "error_message")

        def key(c):
            return tuple(getattr(c, f) for f in fields)

        assert [key(c) for c in compare_all(tests, results, store)] == [
            key(compare_one(t, results.get(t.model_id), store)) for t in tests
        ]
        assert [key(c)[1:3] for c in compare_all(tests, results, store)] == [
            (False, True),
            (True, True),
            (False, False),
        ]


# ---------------------------------------------------------------------------
# Variable identity: scored, stored and overridden under the result-file name
# ---------------------------------------------------------------------------

import logging

from dstf.comparison.tree_spec import parse_metric_tree
from dstf.reporting.status import PASS, classify

_VI_T = np.linspace(0.0, 1.0, 11)
#: One UnitTests variable: the name its result file gives it, and the text the
#: model wrote for it in ``x={...}`` — a label, never a key.
VI_NAME, VI_EXPR = "unitTests.x[1]", "cat(1, a, b)[1]"
#: What a name written as expression text is told. ``HINT`` is the words the
#: two hints share and no other message has — what "no hint" is read by;
#: ``STORED_HINT`` is a baseline entry's and ``KEY_HINT`` an override key's.
HINT = "result-file name"
STORED_HINT = f"the stored name must be the result-file name {VI_NAME!r}"
KEY_HINT = "a key that names an expression must be the result-file name"


def _vi_test(**kw) -> TestModel:
    """A test whose one variable comes from the UnitTests component."""
    kw.setdefault("x_expressions", [VI_EXPR])
    return TestModel(
        model_id="MyLib.Test",
        source_file=Path("x.mo"),
        source_package="MyLib",
        short_name="Test",
        n_vars=kw.pop("n_vars", 1),
        source=kw.pop("source", "unit_tests"),
        **kw,
    )


def _vi_result(values=None, expression=VI_EXPR, name=VI_NAME) -> TestResult:
    return TestResult(
        model_id="MyLib.Test",
        success=True,
        variables=[
            VariableResult(
                index=1,
                time=_VI_T,
                values=_VI_T if values is None else values,
                name=name,
                expression=expression,
            )
        ],
    )


def _vi_reference(expression=VI_EXPR, name=VI_NAME, with_expression=True) -> dict:
    entry: dict = {"index": 1, "name": name, "values": list(_VI_T)}
    if with_expression:
        entry["expression"] = expression
    return {"test_id": "0001", "time": list(_VI_T), "variables": [entry]}


class TestVariableIdentity:
    def test_the_flat_path_scores_the_name_and_carries_the_expression(self):
        comp = compare_test(_vi_test(), _vi_result(), decode_reference(_vi_reference()))
        assert comp.passed
        (vc,) = comp.variables
        assert (vc.name, vc.expression) == (VI_NAME, VI_EXPR)
        assert vc.nrmse == 0.0

    def test_a_tree_leaf_scores_the_name_and_carries_the_expression(self):
        spec = parse_metric_tree({"metric": "nrmse", "variable": VI_NAME})
        comp = compare_test(
            _vi_test(metric_tree_spec=spec),
            _vi_result(),
            decode_reference(_vi_reference()),
        )
        assert comp.passed
        (vc,) = comp.variables
        assert (vc.name, vc.expression) == (VI_NAME, VI_EXPR)

    def test_an_override_keyed_by_the_name_applies_and_one_by_the_expression_does_not(
        self,
    ):
        """Overrides are keyed by the name the result file gives the variable;
        the expression is a label and opens no door of its own."""
        off = _VI_T * 1.05
        loose = {"tolerance": 0.5}
        by_name = compare_test(
            _vi_test(variable_overrides={VI_NAME: loose}),
            _vi_result(off),
            decode_reference(_vi_reference()),
        )
        assert by_name.passed and by_name.variables[0].tolerance_used == Sides(0.5, 0.5)
        by_expression = compare_test(
            _vi_test(variable_overrides={VI_EXPR: loose}),
            _vi_result(off),
            decode_reference(_vi_reference()),
        )
        assert not by_expression.passed
        assert by_expression.variables[0].tolerance_used != Sides(0.5, 0.5)
        assert by_expression.variables[0].tolerance_used == Sides(1e-4, 1e-4)

    def test_an_override_keyed_by_a_plain_expression_does_not_apply(self):
        """The shape a real spec has today: a key that WAS the stored name
        (`pipe.p`) and is now the expression beside `unitTests.x[1]`. It stops
        applying — the key has to be `unitTests.x[1]`."""
        comp = compare_test(
            _vi_test(
                x_expressions=["pipe.p"],
                variable_overrides={"pipe.p": {"tolerance": 0.5}},
            ),
            _vi_result(_VI_T * 1.05, expression="pipe.p"),
            decode_reference(_vi_reference(expression="pipe.p")),
        )
        assert not comp.passed

    @pytest.mark.parametrize("tree", [False, True])
    def test_an_expression_that_changed_warns_and_the_test_still_passes(self, tree):
        """A UnitTests variable is keyed by its position, so an edit to the
        model's `x={...}` can put another quantity under the same name. The
        baseline's recorded expression says which one it was."""
        spec = (
            parse_metric_tree({"metric": "nrmse", "variable": VI_NAME})
            if tree
            else None
        )
        comp = compare_test(
            _vi_test(metric_tree_spec=spec),
            _vi_result(expression="b.Q"),
            decode_reference(_vi_reference(expression="a.Q")),
        )
        assert comp.passed and classify(comp) == PASS
        (warning,) = comp.warnings
        assert VI_NAME in warning.field
        assert (warning.reference_value, warning.current_value) == ("a.Q", "b.Q")

    @pytest.mark.parametrize("tree", [False, True])
    @pytest.mark.parametrize(
        "stored,measured",
        [("a.Q", "a.Q"), ("", "b.Q"), ("a.Q", ""), (None, "b.Q")],
    )
    def test_no_expression_warning_when_they_agree_or_either_side_is_empty(
        self, tree, stored, measured
    ):
        """Silent for a pattern-tracked variable and for a backend that
        records no expression — both sides must say something to disagree."""
        spec = (
            parse_metric_tree({"metric": "nrmse", "variable": VI_NAME})
            if tree
            else None
        )
        reference = (
            _vi_reference(with_expression=False)
            if stored is None
            else _vi_reference(expression=stored)
        )
        comp = compare_test(
            _vi_test(metric_tree_spec=spec),
            _vi_result(expression=measured),
            decode_reference(reference),
        )
        assert comp.passed and comp.warnings == []

    def test_a_baseline_stored_under_the_expression_is_missing_and_says_its_name(
        self, caplog
    ):
        with caplog.at_level(logging.WARNING):
            comp = compare_test(
                _vi_test(x_expressions=["pipe.T[1]"]),
                _vi_result(expression="pipe.T[1]"),
                decode_reference(
                    _vi_reference(name="pipe.T[1]", with_expression=False)
                ),
            )
        assert not comp.passed
        assert any(STORED_HINT in record.getMessage() for record in caplog.records)

    def test_an_override_that_names_no_variable_warns(self, caplog):
        """A key matching nothing has always been ignored in silence. It is
        the only sign a spec key is stale, so say it once per test."""
        with caplog.at_level(logging.WARNING):
            comp = compare_test(
                _vi_test(variable_overrides={"nowhere.near": {"tolerance": 0.5}}),
                _vi_result(),
                decode_reference(_vi_reference()),
            )
        assert comp.passed
        (record,) = [
            r for r in caplog.records if "variable_overrides" in r.getMessage()
        ]
        assert "nowhere.near" in record.getMessage()
        assert HINT not in record.getMessage()

    def test_an_override_keyed_by_an_expression_warns_and_says_the_name_to_write(
        self, caplog
    ):
        """The shape an un-migrated spec has: the key IS the expression the
        variable now carries beside its name. Say which name it meant and
        that the key must be that name."""
        with caplog.at_level(logging.WARNING):
            compare_test(
                _vi_test(variable_overrides={VI_EXPR: {"tolerance": 0.5}}),
                _vi_result(),
                decode_reference(_vi_reference()),
            )
        (record,) = [
            r for r in caplog.records if "variable_overrides" in r.getMessage()
        ]
        message = record.getMessage()
        assert VI_EXPR in message and VI_NAME in message
        assert KEY_HINT in message

    def test_the_warning_says_which_file_holds_each_key(self, caplog):
        """The two override tiers are merged before anything is scored. Told
        only the key, a user greps the spec for a key the BASELINE holds (or
        the other way round) and finds nothing."""
        reference = _vi_reference()
        reference["comparison"] = {"variable_overrides": {"in.the.ref": {}}}
        with caplog.at_level(logging.WARNING):
            compare_test(
                _vi_test(variable_overrides={"in.the.spec": {}}),
                _vi_result(),
                decode_reference(reference),
            )
        (record,) = [
            r for r in caplog.records if "variable_overrides" in r.getMessage()
        ]
        message = record.getMessage()
        assert "'in.the.spec' in test_spec.json" in message
        assert "'in.the.ref' in the reference JSON" in message

    def test_a_key_in_both_tiers_is_named_for_both(self, caplog):
        reference = _vi_reference()
        reference["comparison"] = {"variable_overrides": {"nowhere": {}}}
        with caplog.at_level(logging.WARNING):
            compare_test(
                _vi_test(variable_overrides={"nowhere": {}}),
                _vi_result(),
                decode_reference(reference),
            )
        (record,) = [
            r for r in caplog.records if "variable_overrides" in r.getMessage()
        ]
        assert (
            "'nowhere' in test_spec.json and the reference JSON" in record.getMessage()
        )

    def test_an_expression_two_variables_share_names_both(self, caplog):
        """``x={a, a}`` writes one expression on two variables. Which one the
        stale key meant is the user's call; naming only the last is a guess
        that reads as an answer."""
        test = _vi_test(n_vars=2, x_expressions=["a", "a"])
        result = TestResult(
            model_id="MyLib.Test",
            success=True,
            variables=[
                VariableResult(
                    index=i,
                    time=_VI_T,
                    values=_VI_T,
                    name=f"unitTests.x[{i}]",
                    expression="a",
                )
                for i in (1, 2)
            ],
        )
        test.variable_overrides = {"a": {"tolerance": 0.5}}
        with caplog.at_level(logging.WARNING):
            compare_test(test, result, decode_reference(_vi_reference()))
        (record,) = [
            r for r in caplog.records if "variable_overrides" in r.getMessage()
        ]
        message = record.getMessage()
        assert "'unitTests.x[1]', 'unitTests.x[2]'" in message
        assert KEY_HINT in message

    def test_an_override_that_names_a_variable_is_silent(self, caplog):
        with caplog.at_level(logging.WARNING):
            comp = compare_test(
                _vi_test(variable_overrides={VI_NAME: {"tolerance": 0.5}}),
                _vi_result(),
                decode_reference(_vi_reference()),
            )
        assert comp.passed and comp.variables[0].tolerance_used == Sides(0.5, 0.5)
        assert not [r for r in caplog.records if "variable_overrides" in r.getMessage()]

    def test_the_baseline_free_check_reads_the_variables_names(self):
        bounds = {"mode": "range", "min_value": 0.0, "max_value": 2.0}
        assert _test_is_baseline_free(
            _vi_test(variable_overrides={VI_NAME: bounds}), 1e-4
        )
        assert not _test_is_baseline_free(
            _vi_test(variable_overrides={VI_EXPR: bounds}), 1e-4
        )

    def test_a_both_test_is_not_baseline_free_on_its_patterns_alone(self):
        """`source == "both"`: the spec's patterns AND the component's
        variables are tracked, so covering only the patterns leaves the
        component's variables scoring nrmse against a baseline."""
        bounds = {"mode": "range", "min_value": 0.0, "max_value": 2.0}
        assert not _test_is_baseline_free(
            _vi_test(
                source="both",
                variable_patterns=["a"],
                variable_overrides={"a": bounds},
            ),
            1e-4,
        )

    def test_the_display_relabelling_is_gone(self):
        """`variable_label` turned a `cat(`/newline name into `x[N]` for
        scoring and display; nothing relabels a variable now."""
        import dstf.comparison.comparator as comparator

        assert not hasattr(comparator, "variable_label")


#: The most common un-migrated shape in the owner's library: a plain name
#: whose ``.mo`` source wrapped the line, stored with that whitespace in it
#: (210 of 877 measured variables). The expression beside the result
#: variable is normalized, so the two texts differ byte for byte.
WRAPPED_STORED = "heatTransfer.alphas[\n        1, 1]"
WRAPPED_EXPR = "heatTransfer.alphas[1, 1]"


class TestTheMigrationHintSeesAnUnNormalizedName:
    """`migration_hint` matches a STORED name against an expression, and the
    two are written by different hands: the store keeps the ``.mo`` file's
    layout, the expression is normalized. Compared raw, the shape that needs
    the hint most got none at all — on either scoring path."""

    def _wrapped_reference(self, stored=WRAPPED_STORED) -> dict:
        """A baseline as it was written BEFORE the identity change: the
        expression text as the name, and no ``expression`` key."""
        return {
            "test_id": "0001",
            "time": list(_VI_T),
            "variables": [{"index": 1, "name": stored, "values": list(_VI_T)}],
        }

    @pytest.mark.parametrize("tree", [False, True])
    @pytest.mark.parametrize("stored", [WRAPPED_STORED, "heatTransfer.alphas[1,1]"])
    def test_a_stored_name_the_source_wrapped_or_packed_gets_the_hint(
        self, caplog, tree, stored
    ):
        spec = (
            parse_metric_tree({"metric": "nrmse", "variable": VI_NAME})
            if tree
            else None
        )
        with caplog.at_level(logging.WARNING):
            comp = compare_test(
                _vi_test(metric_tree_spec=spec, x_expressions=[WRAPPED_EXPR]),
                _vi_result(expression=WRAPPED_EXPR),
                decode_reference(self._wrapped_reference(stored)),
            )
        assert not comp.passed
        assert any(STORED_HINT in r.getMessage() for r in caplog.records)

    @pytest.mark.parametrize("tree", [False, True])
    def test_a_migrated_baseline_holding_the_expression_as_a_real_name_is_not_hinted(
        self, caplog, tree
    ):
        """A `both` test: the spec tracks `pipe.T[1]` and the component's
        `x={pipe.T[1]}` measures the same quantity a second time. The
        baseline legitimately holds `pipe.T[1]` — MIGRATED, so it carries
        the `expression` key — and simply does not hold the new
        `unitTests.x[1]` yet. Re-accepting is the answer; renaming that
        entry would destroy a variable of this test."""
        result = TestResult(
            model_id="MyLib.Test",
            success=True,
            variables=[
                VariableResult(
                    index=1, time=_VI_T, values=_VI_T, name="pipe.T[1]", expression=""
                ),
                VariableResult(
                    index=2,
                    time=_VI_T,
                    values=_VI_T,
                    name=VI_NAME,
                    expression="pipe.T[1]",
                ),
            ],
        )
        reference = {
            "test_id": "0001",
            "time": list(_VI_T),
            "variables": [
                {
                    "index": 1,
                    "name": "pipe.T[1]",
                    "expression": "",
                    "values": list(_VI_T),
                }
            ],
        }
        spec = (
            parse_metric_tree({"metric": "nrmse", "variable": VI_NAME})
            if tree
            else None
        )
        with caplog.at_level(logging.WARNING):
            compare_test(
                _vi_test(
                    metric_tree_spec=spec,
                    source="both",
                    variable_patterns=["pipe.T[1]"],
                    x_expressions=["pipe.T[1]"],
                ),
                result,
                decode_reference(reference),
            )
        assert not any(HINT in r.getMessage() for r in caplog.records)

    def test_the_hint_is_said_once_per_variable_and_baseline_pair(self, caplog):
        """The dedupe key is (baseline, variable), not the variable alone:
        the same variable missing from the PRIMARY and from a soft_check is
        two baselines to rename it in, and one line naming only one of them
        leaves the other un-migrated with nothing said about it."""
        from conftest import series

        class _Store:
            folder = "Stub/linux"

            def get_soft_checks(self, model_id):
                return {
                    "fmpy": series(
                        name="fmpy",
                        time=list(_VI_T),
                        variables=[
                            {
                                "index": 1,
                                "name": WRAPPED_STORED,
                                "values": list(_VI_T),
                            }
                        ],
                    )
                }

        spec = parse_metric_tree(
            {
                "combinator": "and",
                "children": [
                    {"metric": "nrmse", "variable": VI_NAME},
                    {
                        "combinator": "warn",
                        "children": [
                            {
                                "metric": "nrmse",
                                "variable": VI_NAME,
                                "against": "fmpy",
                            }
                        ],
                    },
                ],
            }
        )
        with caplog.at_level(logging.WARNING):
            compare_test(
                _vi_test(metric_tree_spec=spec, x_expressions=[WRAPPED_EXPR]),
                _vi_result(expression=WRAPPED_EXPR),
                decode_reference(self._wrapped_reference()),
                store=_Store(),
            )
        hinted = [r.getMessage() for r in caplog.records if HINT in r.getMessage()]
        assert len(hinted) == 2
        assert sum("'primary'" in m for m in hinted) == 1
        assert sum("'fmpy'" in m for m in hinted) == 1

    def test_the_tree_paths_hint_names_the_model(self, caplog):
        """N tests of one suite printed the identical nameless sentence:
        the reader learned how many baselines are un-migrated and not one
        of the models whose baseline to open. The flat path always said
        it."""
        spec = parse_metric_tree({"metric": "nrmse", "variable": VI_NAME})
        with caplog.at_level(logging.WARNING):
            compare_test(
                _vi_test(metric_tree_spec=spec, x_expressions=[WRAPPED_EXPR]),
                _vi_result(expression=WRAPPED_EXPR),
                decode_reference(self._wrapped_reference()),
            )
        (hinted,) = [r.getMessage() for r in caplog.records if HINT in r.getMessage()]
        assert hinted.startswith("MyLib.Test: ")

    def test_the_tree_path_says_it_once_per_variable_not_once_per_leaf(self, caplog):
        """Five criteria on one un-migrated variable is one thing to fix.
        A TRANSFORM test with five leaves printed the same sentence five
        times, and the REASON is on every leaf either way."""
        spec = parse_metric_tree(
            {
                "combinator": "and",
                "children": [
                    {"metric": "nrmse", "variable": VI_NAME, "tolerance": t / 100}
                    for t in range(1, 6)
                ],
            }
        )
        with caplog.at_level(logging.WARNING):
            comp = compare_test(
                _vi_test(metric_tree_spec=spec, x_expressions=[WRAPPED_EXPR]),
                _vi_result(expression=WRAPPED_EXPR),
                decode_reference(self._wrapped_reference()),
            )
        hinted = [r for r in caplog.records if HINT in r.getMessage()]
        assert len(hinted) == 1
        assert len(comp.variables) == 5
        assert all(HINT in (vc.diagnostics.get("error") or "") for vc in comp.variables)


#: A second UnitTests variable of the same test, with its own expression.
VI_NAME2, VI_EXPR2 = "unitTests.x[2]", "cat(1, a, b)[2]"


def _vi_two_result(expressions=(VI_EXPR, VI_EXPR2)) -> TestResult:
    """Two UnitTests variables, each with its OWN expression."""
    return TestResult(
        model_id="MyLib.Test",
        success=True,
        variables=[
            VariableResult(
                index=i + 1,
                time=_VI_T,
                values=_VI_T * (i + 1),
                name=name,
                expression=expressions[i],
            )
            for i, name in enumerate((VI_NAME, VI_NAME2))
        ],
    )


def _vi_two_reference(order=(0, 1), expressions=(VI_EXPR, VI_EXPR2)) -> dict:
    """The baseline for `_vi_two_result`, its entries in ``order``."""
    entries = [
        {
            "index": i + 1,
            "name": name,
            "expression": expressions[i],
            "values": list(_VI_T * (i + 1)),
        }
        for i, name in enumerate((VI_NAME, VI_NAME2))
    ]
    return {
        "test_id": "0001",
        "time": list(_VI_T),
        "variables": [entries[i] for i in order],
    }


class TestTheExpressionRidesOnEveryComparison:
    """A comparison carries the expression of the variable it names —
    including the ones that never reach a scorer. The failing leaves are
    exactly the ones a person reads, so those are the ones that must not
    fall back to the bare name."""

    def _leaf(self, comp):
        (vc,) = comp.variables
        return vc

    def test_a_leaf_whose_variable_the_baseline_lost_keeps_the_expression(self):
        spec = parse_metric_tree({"metric": "nrmse", "variable": VI_NAME})
        comp = compare_test(
            _vi_test(metric_tree_spec=spec),
            _vi_result(),
            decode_reference({"test_id": "0001", "time": list(_VI_T), "variables": []}),
        )
        vc = self._leaf(comp)
        assert not vc.passed
        assert (vc.name, vc.expression) == (VI_NAME, VI_EXPR)

    def test_a_leaf_naming_a_baseline_that_is_not_there_keeps_the_expression(self):
        spec = parse_metric_tree(
            {"metric": "nrmse", "variable": VI_NAME, "against": "nope"}
        )
        comp = compare_test(
            _vi_test(metric_tree_spec=spec),
            _vi_result(),
            decode_reference(_vi_reference()),
        )
        vc = self._leaf(comp)
        assert not vc.passed
        assert vc.diagnostics["error"] == "no soft check 'nope' is stored"
        assert (vc.name, vc.expression) == (VI_NAME, VI_EXPR)

    def test_a_misconfigured_leaf_keeps_the_expression(self):
        spec = parse_metric_tree(
            {
                "metric": "tube",
                "variable": VI_NAME,
                "tube_width_mode": "bogus",
                "tube_rel": 0.1,
            }
        )
        comp = compare_test(
            _vi_test(metric_tree_spec=spec),
            _vi_result(),
            decode_reference(_vi_reference()),
        )
        vc = self._leaf(comp)
        assert not vc.passed and vc.diagnostics.get("config_error")
        assert (vc.name, vc.expression) == (VI_NAME, VI_EXPR)

    def test_a_leaf_whose_mode_never_resolves_keeps_the_expression(self):
        """The earliest failure a leaf has: `resolve_mode` refuses the
        params before any baseline or trajectory is looked at (a `range`
        leaf declaring neither bound). It returns its own sentinel, which
        must carry the expression like every other one."""
        spec = parse_metric_tree({"metric": "range", "variable": VI_NAME})
        comp = compare_test(
            _vi_test(metric_tree_spec=spec),
            _vi_result(),
            decode_reference(_vi_reference()),
        )
        vc = self._leaf(comp)
        assert not vc.passed and vc.diagnostics.get("config_error")
        assert (vc.name, vc.expression) == (VI_NAME, VI_EXPR)

    def test_a_leaf_whose_metric_refuses_the_dataset_keeps_the_expression(self):
        """The dataset gate returns before the leaf resolves anything at
        all. It is still a sentinel a person reads on the report."""
        from dstf.backends.types import DatasetType
        from dstf.comparison.tree_eval import collect_leaf_variables, evaluate_spec

        spec = parse_metric_tree({"metric": "nrmse", "variable": VI_NAME})
        (var_result,) = _vi_result().variables
        tree = evaluate_spec(
            spec, {VI_NAME: var_result}, {}, 1e-4, DatasetType.SPECTRUM
        )
        (vc,) = collect_leaf_variables(tree)
        assert not tree.passed and "spectrum" in vc.diagnostics["error"]
        assert (vc.name, vc.expression) == (VI_NAME, VI_EXPR)

    def test_a_leaf_whose_variable_the_result_lost_has_no_expression(self):
        """Nothing resolved a result variable, so there is no expression to
        carry — the bare name is all the leaf has."""
        spec = parse_metric_tree({"metric": "nrmse", "variable": "not.there"})
        comp = compare_test(
            _vi_test(metric_tree_spec=spec),
            _vi_result(),
            decode_reference(_vi_reference()),
        )
        vc = self._leaf(comp)
        assert not vc.passed and (vc.name, vc.expression) == ("not.there", "")

    def test_each_flat_comparison_carries_its_own_variables_expression(self):
        """Guards the post-loop stamping: the flat path appends one
        comparison per result variable and pairs them by position, so a
        rotation there puts every expression on the wrong variable."""
        comp = compare_test(
            _vi_test(n_vars=2), _vi_two_result(), decode_reference(_vi_two_reference())
        )
        assert comp.passed
        assert [(vc.name, vc.expression) for vc in comp.variables] == [
            (VI_NAME, VI_EXPR),
            (VI_NAME2, VI_EXPR2),
        ]


class TestTheExpressionCheckPairsByName:
    def test_a_reordered_baseline_is_paired_by_name(self):
        """The stored entries are in the order they were accepted in; a
        re-accept after an `x={...}` edit can reorder them. Pairing the
        check by position then reports every variable as changed."""
        comp = compare_test(
            _vi_test(n_vars=2),
            _vi_two_result(),
            decode_reference(_vi_two_reference(order=(1, 0))),
        )
        assert comp.passed and comp.warnings == []

    def test_a_duplicate_name_is_read_from_the_first_entry(self):
        """Pairing reads the FIRST entry under a name; the check must read
        the same one, or a stale duplicate further down the file reports a
        change the scoring never saw."""
        reference = _vi_reference()
        reference["variables"].append(
            {"index": 2, "name": VI_NAME, "expression": "STALE", "values": list(_VI_T)}
        )
        comp = compare_test(_vi_test(), _vi_result(), decode_reference(reference))
        assert comp.warnings == []

    def test_a_named_baselines_expression_is_checked_once_per_variable(self):
        """`against: fmpy` scores a leaf on another baseline — with its own
        recorded expression, which the primary's check never reads. Two
        leaves on the same variable and baseline still say it once."""
        from conftest import series

        class _Store:
            folder = "Stub/linux"

            def get_soft_checks(self, model_id):
                return {
                    "fmpy": series(
                        name="fmpy",
                        time=list(_VI_T),
                        variables=[
                            {
                                "index": 1,
                                "name": VI_NAME,
                                "expression": "OTHER.Q",
                                "values": list(_VI_T),
                            }
                        ],
                    )
                }

        spec = parse_metric_tree(
            {
                "combinator": "and",
                "children": [
                    {"metric": "nrmse", "variable": VI_NAME},
                    {
                        "combinator": "warn",
                        "children": [
                            {
                                "combinator": "and",
                                "children": [
                                    {
                                        "metric": "nrmse",
                                        "variable": VI_NAME,
                                        "against": "fmpy",
                                    },
                                    {
                                        "metric": "tube",
                                        "variable": VI_NAME,
                                        "against": "fmpy",
                                        "tube_rel": 0.5,
                                    },
                                ],
                            }
                        ],
                    },
                ],
            }
        )
        comp = compare_test(
            _vi_test(metric_tree_spec=spec),
            _vi_result(),
            decode_reference(_vi_reference()),
            store=_Store(),
        )
        assert comp.passed
        assert [
            (w.field, w.reference_value, w.current_value) for w in comp.warnings
        ] == [(f"Expression of {VI_NAME} (against fmpy)", "OTHER.Q", VI_EXPR)]

    def test_a_named_baseline_is_checked_only_on_what_it_is_scored_against(self):
        """A soft_check is imported whole — it holds every variable of the
        run it came from. Nothing compares a variable no leaf scores against
        it, so a difference between those two expressions is not a finding
        about this run: it said `unitTests.x[2]` changed while the only leaf
        naming `fmpy` scored `unitTests.x[1]`."""
        from conftest import series

        class _Store:
            folder = "Stub/linux"

            def get_soft_checks(self, model_id):
                return {
                    "fmpy": series(
                        name="fmpy",
                        time=list(_VI_T),
                        variables=[
                            {
                                "index": i + 1,
                                "name": name,
                                "expression": "OTHER.Q",
                                "values": list(_VI_T * (i + 1)),
                            }
                            for i, name in enumerate((VI_NAME, VI_NAME2))
                        ],
                    )
                }

        spec = parse_metric_tree(
            {
                "combinator": "and",
                "children": [
                    {"metric": "nrmse", "variable": VI_NAME2},
                    {"metric": "nrmse", "variable": VI_NAME, "against": "fmpy"},
                ],
            }
        )
        comp = compare_test(
            _vi_test(n_vars=2, metric_tree_spec=spec),
            _vi_two_result(),
            decode_reference(_vi_two_reference()),
            store=_Store(),
        )
        assert [w.field for w in comp.warnings] == [
            f"Expression of {VI_NAME} (against fmpy)"
        ]

    def test_a_stored_scalar_leaf_registers_no_variable_for_the_check(self):
        """A `scalar` leaf reading a statistics path resolves no variable of
        the result: its `variable` is free text naming the leaf. Registered
        as one, it put `CPUtime` — whose name a result variable happens to
        share here — into the set of quantities scored against `fmpy`, and
        the soft_check's entry for it was then compared with an expression
        nothing had measured against that baseline."""
        from conftest import series

        class _Store:
            folder = "Stub/linux"

            def get_soft_checks(self, model_id):
                return {
                    "fmpy": series(
                        name="fmpy",
                        time=list(_VI_T),
                        variables=[
                            {
                                "index": 1,
                                "name": VI_NAME,
                                "expression": "OTHER.Q",
                                "values": list(_VI_T),
                            }
                        ],
                        statistics={"CPUtime": 1.0},
                    )
                }

        spec = parse_metric_tree(
            {
                "combinator": "and",
                "children": [
                    {"metric": "nrmse", "variable": VI_NAME},
                    {
                        "combinator": "warn",
                        "children": [
                            {
                                "metric": "scalar",
                                "variable": VI_NAME,
                                "quantity": "CPUtime",
                                "against": "fmpy",
                                "tolerance": 10.0,
                            }
                        ],
                    },
                ],
            }
        )
        result = _vi_result()
        result.statistics = {"CPUtime": 1.0}
        comp = compare_test(
            _vi_test(metric_tree_spec=spec),
            result,
            decode_reference(_vi_reference()),
            store=_Store(),
        )
        assert comp.warnings == []


class TestPairingIsByNameOnly:
    def test_a_nameless_result_variable_is_not_paired_by_index(self):
        """No backend builds a nameless variable; one that arrives anyway is
        a bug, not an invitation to score it against whatever entry sits at
        its index. It is none of the test's tracked names, so the flat path
        does not score it at all."""
        result = TestResult(
            model_id="MyLib.Test",
            success=True,
            variables=[VariableResult(index=1, time=_VI_T, values=_VI_T, name="")],
        )
        comp = compare_test(_vi_test(), result, decode_reference(_vi_reference()))
        assert comp.variables == []

    def test_a_baseline_holding_the_name_at_another_index_still_scores(self):
        """Pairing is by name, so a reordered baseline scores as it did
        before the reorder."""
        comp = compare_test(
            _vi_test(n_vars=2),
            _vi_two_result(),
            decode_reference(_vi_two_reference(order=(1, 0))),
        )
        assert comp.passed
        assert [vc.nrmse for vc in comp.variables] == [0.0, 0.0]


class TestTheMigrationHintIsSharedByBothPaths:
    def _un_migrated(self, **kw):
        """A result whose variable is `unitTests.x[1]` against a baseline
        that still stores it under its expression."""
        return compare_test(
            _vi_test(x_expressions=["pipe.T[1]"], **kw),
            _vi_result(expression="pipe.T[1]"),
            decode_reference(_vi_reference(name="pipe.T[1]", with_expression=False)),
        )

    def test_the_tree_path_says_the_name_to_store(self, caplog):
        spec = parse_metric_tree({"metric": "nrmse", "variable": VI_NAME})
        with caplog.at_level(logging.WARNING):
            comp = self._un_migrated(metric_tree_spec=spec)
        assert not comp.passed
        (vc,) = comp.variables
        assert STORED_HINT in vc.diagnostics["error"]
        assert any(STORED_HINT in record.getMessage() for record in caplog.records)

    @pytest.mark.parametrize("tree", [False, True])
    def test_a_baseline_that_never_held_the_variable_gets_no_hint(self, tree, caplog):
        """The hint is for a baseline stored under the expression, not for
        every missing one: a variable new to the baseline is re-accepted,
        not renamed."""
        spec = (
            parse_metric_tree({"metric": "nrmse", "variable": VI_NAME})
            if tree
            else None
        )
        with caplog.at_level(logging.WARNING):
            comp = compare_test(
                _vi_test(metric_tree_spec=spec),
                _vi_result(),
                decode_reference(_vi_reference(name="somewhere.else")),
            )
        assert not comp.passed
        messages = [record.getMessage() for record in caplog.records] + [
            comp.variables[0].diagnostics.get("error", "")
        ]
        assert not any(HINT in message for message in messages)


# ---------------------------------------------------------------------------
# The flat path scores what the test tracks, not whatever the result holds
# ---------------------------------------------------------------------------


def _tracked_result(*names: str) -> TestResult:
    t = np.array([0.0, 0.5, 1.0])
    return TestResult(
        model_id="Test.Model",
        success=True,
        variables=[
            VariableResult(index=i, name=n, time=t, values=t + i)
            for i, n in enumerate(names, start=1)
        ],
    )


def _tracked_reference(*names: str) -> dict:
    t = [0.0, 0.5, 1.0]
    return {
        "test_id": "0001",
        "time": t,
        "variables": [
            {"index": i, "name": n, "values": [v + i for v in t]}
            for i, n in enumerate(names, start=1)
        ],
    }


class TestTheFlatPathScoresWhatTheTestTracks:
    """A result may hold a variable the test does not track — one a
    criterion named before it was discarded (a re-read, a rerun with a
    pending criterion). The flat path scores the tracked ones only: an
    extra with no baseline would fail the test for a variable it never
    asked about."""

    def test_an_untracked_extra_is_not_scored(self):
        test = _make_test()  # tracks ["x"]
        comp = compare_test(
            test,
            _tracked_result("x", "w"),
            decode_reference(_tracked_reference("x")),
            1e-4,
        )
        assert [vc.name for vc in comp.variables] == ["x"]
        assert comp.passed

    def test_a_glob_and_the_unit_tests_names_are_tracked(self):
        test = TestModel(
            model_id="Test.Model",
            source_file=None,
            source_package="Test",
            short_name="Model",
            n_vars=1,
            x_expressions=["a.T"],
            source="both",
            variable_patterns=["pipe.T*"],
        )
        names = ("unitTests.x[1]", "pipe.T[1]", "pipe.T[2]", "w")
        comp = compare_test(
            test,
            _tracked_result(*names),
            decode_reference(_tracked_reference(*names[:3])),
            1e-4,
        )
        assert [vc.name for vc in comp.variables] == list(names[:3])
        assert comp.passed

    def test_a_star_scores_every_variable(self):
        test = _make_test()
        test.variable_patterns = ["*"]
        comp = compare_test(
            test,
            _tracked_result("x", "w"),
            decode_reference(_tracked_reference("x", "w")),
            1e-4,
        )
        assert [vc.name for vc in comp.variables] == ["x", "w"]

    def test_a_glob_tracks_whole_names_only(self):
        """``pipe.T?`` is ``pipe.T1``, not a prefix of ``pipe.T12``."""
        test = TestModel(
            model_id="Test.Model",
            source_file=None,
            source_package="Test",
            short_name="Model",
            n_vars=0,
            source="spec",
            variable_patterns=["pipe.T?"],
        )
        comp = compare_test(
            test,
            _tracked_result("pipe.T1", "pipe.T12"),
            decode_reference(_tracked_reference("pipe.T1")),
            1e-4,
        )
        assert [vc.name for vc in comp.variables] == ["pipe.T1"]
        assert comp.passed


def test_a_tree_warns_only_of_the_tracked_variables_it_does_not_score():
    """ "Tracked but not scored by any criterion" names what the test TRACKS
    and no leaf covers — never a variable the result merely holds (one a
    discarded criterion had re-read, or a rerun with it pending recorded).
    It is a statement about this run, not a change from the baseline: the
    old `1 scored by the tree -> y` read as if `y` were the scored one."""
    test = _make_test()
    test.variable_patterns = ["x", "y"]
    test.metric_tree_spec = parse_metric_tree(
        {"combinator": "and", "children": [{"metric": "nrmse", "variable": "x"}]}
    )
    comp = compare_test(
        test,
        _tracked_result("x", "y", "w"),
        decode_reference(_tracked_reference("x", "y")),
        1e-4,
    )
    (warning,) = comp.warnings
    assert warning.text == "Tracked but not scored by any criterion: y"
    assert not warning.is_change
    assert (warning.reference_value, warning.current_value) == ("", "")


# ---------------------------------------------------------------------------
# A tolerance travels as Sides: a number, or a lower/upper pair
# ---------------------------------------------------------------------------

import dataclasses  # noqa: E402
import json  # noqa: E402

from dstf.comparison.types import VariableComparison  # noqa: E402

_S_T = np.linspace(0.0, 10.0, 101)
_S_REF = np.sin(_S_T)
_S_ACT = _S_REF + 0.01
#: Three events, as duplicated instants.
_S_TE = np.array([0, 1, 2, 2, 3, 4, 5, 5, 6, 7, 8, 8, 9, 10], float)
_S_VE = np.arange(len(_S_TE), dtype=float)
_S_TF = np.linspace(0.0, 20.0, 2001)
_S_SIG = np.sin(2 * np.pi * _S_TF)
#: kind -> (time, reference values, actual values)
_S_DATA = {
    "x": (_S_T, _S_REF, _S_ACT),
    "e": (_S_TE, _S_VE, _S_VE),
    "f": (_S_TF, _S_SIG, _S_SIG),
    # Away from zero, where a relative width is not pinched to nothing.
    "o": (_S_T, _S_REF + 3.0, _S_ACT + 3.0),
}
_S_EVEN = {"lower": 0.05, "upper": 0.05}
_S_UNEVEN = {"lower": 0.01, "upper": 0.05}
_S_MALFORMED = [
    "abc",
    "0.01",
    None,
    True,
    {"lower": 1},
    {"lower": 1, "upper": 2, "x": 3},
    [1],
]


def _sides_compare(
    leaf,
    *,
    flat,
    kind="x",
    comparison_tolerance=None,
    ref_comparison=None,
    extra=None,
    test=None,
):
    """``leaf`` (a raw metrics leaf without its ``variable``) scored on the
    tree path or, with ``flat``, as the same keys in ``variable_overrides``.
    ``extra`` is a second leaf, on ``y``, beside it. ``test`` is a discovered
    :class:`TestModel` scored as discovery left it: ``leaf`` and ``flat``
    are then the spec file's, not this helper's."""
    time, ref, act = _S_DATA[kind]
    names = ["x"] + (["y"] if extra is not None else [])
    result = TestResult(
        model_id="Test.Model",
        success=True,
        variables=[
            VariableResult(index=i + 1, name=n, time=time, values=act)
            for i, n in enumerate(names)
        ],
    )
    reference = {
        "test_id": "0001",
        "time": time.tolist(),
        "variables": [
            {"index": i + 1, "name": n, "values": ref.tolist()}
            for i, n in enumerate(names)
        ],
    }
    if ref_comparison is not None:
        reference["comparison"] = ref_comparison
    if test is not None:
        return compare_test(
            test, result, decode_reference(reference), default_tolerance=1e-4
        )
    test = _make_test(comparison_tolerance=comparison_tolerance)
    test.variable_patterns = names

    def override(raw):
        ov = {k: v for k, v in raw.items() if k != "metric"}
        return {**ov, "mode": raw["metric"]}

    if flat:
        test.variable_overrides = {"x": override(leaf)}
        if extra is not None:
            test.variable_overrides["y"] = override(extra)
    else:
        tree = {**leaf, "variable": "x"}
        if extra is not None:
            tree = {"combinator": "and", "children": [tree, {**extra, "variable": "y"}]}
        test.metric_tree_spec = parse_metric_tree(tree)
    return compare_test(
        test, result, decode_reference(reference), default_tolerance=1e-4
    )


def _leaves(result):
    """The leaf results of a MetricResult tree."""
    if not result.children:
        return [result]
    return [leaf for child in result.children for leaf in _leaves(child)]


def _view_leaves(view):
    """The leaf nodes of a tree view (`to_view`)."""
    if not view.get("children"):
        return [view]
    return [leaf for child in view["children"] for leaf in _view_leaves(child)]


def _sides_fields(vc) -> str:
    """Every field of a comparison but its geometry, as comparable text."""
    d = dataclasses.asdict(vc)
    d.pop("geometry")
    return json.dumps(d, sort_keys=True, default=repr)


def _sides_coords(vc) -> list:
    out = []
    for p in vc.geometry:
        d = {
            f.name: getattr(p, f.name)
            for f in dataclasses.fields(p)  # type: ignore[arg-type]
        }
        out.append(
            {k: v.tolist() if isinstance(v, np.ndarray) else v for k, v in d.items()}
        )
    return out


#: Every LEAF key that takes a pair: (kind, the leaf without it, the key, a number).
_S_LEAF_KEYS = {
    "points tolerance": ("x", {"metric": "points"}, "tolerance", 0.05),
    "scalar tolerance": (
        "x",
        {"metric": "scalar", "quantity": {"reduce": "max"}},
        "tolerance",
        0.05,
    ),
    "tube_abs": ("x", {"metric": "tube", "tube_width_mode": "band"}, "tube_abs", 0.05),
    # Away from zero: a relative width is nothing where the reference is 0.
    "tube_rel": ("o", {"metric": "tube", "tube_width_mode": "rel"}, "tube_rel", 0.05),
    "time_tolerance": ("e", {"metric": "event-timing"}, "time_tolerance", 0.05),
}


class TestATolerancesNumberAndEqualPairAreOne:
    """The identity rule: a spec holding ``v`` and one holding
    ``{"lower": v, "upper": v}`` score the same, on both paths."""

    @pytest.mark.parametrize("flat", [False, True], ids=["tree", "flat"])
    @pytest.mark.parametrize("name", _S_LEAF_KEYS)
    def test_a_leaf_key(self, name, flat):
        kind, leaf, key, v = _S_LEAF_KEYS[name]
        a = _sides_compare({**leaf, key: v}, flat=flat, kind=kind).variables[0]
        b = _sides_compare(
            {**leaf, key: {"lower": v, "upper": v}}, flat=flat, kind=kind
        ).variables[0]
        assert "config_error" not in a.diagnostics, a.diagnostics
        assert _sides_fields(a) == _sides_fields(b)
        assert _sides_coords(a) == _sides_coords(b)
        assert a.passed is b.passed

    @pytest.mark.parametrize("flat", [False, True], ids=["tree", "flat"])
    def test_the_tolerance_applied_is_sides(self, flat):
        vc = _sides_compare(
            {"metric": "points", "tolerance": _S_EVEN}, flat=flat
        ).variables[0]
        assert vc.tolerance_used == Sides(0.05, 0.05)
        vc = _sides_compare({"metric": "points"}, flat=flat).variables[0]
        assert vc.tolerance_used == Sides(1e-4, 1e-4)  # the base, as both sides

    @pytest.mark.parametrize("flat", [False, True], ids=["tree", "flat"])
    def test_a_leafs_unequal_pair_leaves_as_the_pair(self, flat):
        """The leaf's diagnostics and the tree view carry the tolerance
        applied as written: an unequal pair is the pair, never one side."""
        from dstf.comparison.tree_eval import to_view

        comp = _sides_compare({"metric": "points", "tolerance": _S_UNEVEN}, flat=flat)
        (leaf,) = _leaves(comp.metric_tree)
        assert leaf.diagnostics["tolerance"] == _S_UNEVEN
        (node,) = _view_leaves(to_view(comp.metric_tree))
        assert node["tolerance"] == _S_UNEVEN
        # Equal sides are the number.
        comp = _sides_compare({"metric": "points", "tolerance": _S_EVEN}, flat=flat)
        (leaf,) = _leaves(comp.metric_tree)
        assert leaf.diagnostics["tolerance"] == 0.05

    @pytest.mark.parametrize("flat", [False, True], ids=["tree", "flat"])
    def test_nrmse_takes_an_equal_pair(self, flat):
        a = _sides_compare({"metric": "nrmse", "tolerance": 0.5}, flat=flat)
        b = _sides_compare(
            {"metric": "nrmse", "tolerance": {"lower": 0.5, "upper": 0.5}}, flat=flat
        )
        assert _sides_fields(a.variables[0]) == _sides_fields(b.variables[0])
        assert a.passed and b.passed


def test_a_number_handed_to_a_comparison_is_both_sides():
    """A fixture, or a third-party metric, builds one with a float."""
    vc = VariableComparison(
        index=0,
        name="x",
        passed=True,
        nrmse=0.0,
        rmse=0.0,
        signal_range=1.0,
        max_abs_error=0.0,
        max_abs_error_time=0.0,
        reference_final=0.0,
        actual_final=0.0,
        tolerance_used=0.25,
    )
    assert vc.tolerance_used == Sides(0.25, 0.25)
    assert dataclasses.replace(vc, tolerance_used=Sides(0.1, 0.2)).tolerance_used == (
        Sides(0.1, 0.2)
    )


_COMPARE_TIME_ERRORS = [
    (
        {"metric": "tube", "tube_width_mode": "absolute"},
        "tube_width_mode must be 'band', 'rel', or 'abs'; got 'absolute'",
    ),
    (
        {"metric": "tube", "tube_width_mode": "abs"},
        "tube_width_mode='abs' requires tube_points; "
        "constant-tube shorthand supports only 'band' and 'rel'.",
    ),
    ({"metric": "tube"}, "tube_width_mode must be 'band', 'rel', or 'abs'; got None"),
    (
        {
            "metric": "tube",
            "tube_width_mode": "band",
            "tube_points": [{"time": 0.0}],
        },
        "tube_points entries need 'upper' and/or 'lower' widths; got keys ['time']",
    ),
]


@pytest.mark.parametrize(
    ("leaf", "said"), _COMPARE_TIME_ERRORS, ids=["absolute", "abs", "none", "points"]
)
class TestAModesCompareTimeErrorIsAConfigError:
    """A config error a mode can only detect when it scores (a tube's
    unknown width mode, `abs` without control points) is a ``ValueError``
    from ``mode.compare``. On the tree path that was already the leaf's
    config error; on the flat one it went out of ``compare_test`` and took
    the run down."""

    def test_flat_it_is_the_variables_and_the_rest_is_scored(self, leaf, said):
        comp = _sides_compare(
            leaf, flat=True, extra={"metric": "nrmse", "tolerance": 0.5}
        )
        x, y = comp.variables
        assert not x.passed and x.name == "x" and x.index == 1
        assert x.diagnostics == {"error": said, "config_error": True}
        assert y.passed and not comp.passed

    def test_flat_it_has_the_shape_of_a_mode_that_did_not_resolve(self, leaf, said):
        """One sentinel for both: what `resolve_mode`'s ``ValueError``
        always became (a config that cannot be built, a malformed
        tolerance) — named by its metric, as the tree path's is."""

        def shape(vc):
            fields = dataclasses.asdict(vc)
            fields["diagnostics"] = sorted(vc.diagnostics)
            return repr(fields)  # NaN fields: compared as text

        (late,) = _sides_compare(leaf, flat=True).variables
        (early,) = _sides_compare(
            {"metric": "tube", "tube_abs": "wide"}, flat=True
        ).variables
        assert early.diagnostics["config_error"] is True
        assert shape(late) == shape(early)
        assert late.mode == "tube"

    def test_flat_a_mode_nothing_is_registered_under_names_no_metric(self, leaf, said):
        """The one sentinel with no metric: never the "nrmse" default it was
        not scored by, never the string that was written."""
        (unknown,) = _sides_compare({"metric": "no-such-mode"}, flat=True).variables
        (late,) = _sides_compare(leaf, flat=True).variables
        assert unknown.mode == "" and unknown.diagnostics["config_error"] is True
        # NaN fields: compared as text
        assert repr(dataclasses.replace(late, mode="", diagnostics={})) == repr(
            dataclasses.replace(unknown, diagnostics={})
        )

    def test_tree_it_is_the_leafs_as_it_was(self, leaf, said):
        comp = _sides_compare(
            leaf, flat=False, extra={"metric": "nrmse", "tolerance": 0.5}
        )
        x, y = comp.variables
        assert not x.passed and x.mode == "tube"
        assert x.diagnostics["error"] == f"metric 'tube' misconfigured: {said}"
        assert x.diagnostics["config_error"] is True
        assert y.passed and not comp.passed


def test_flat_a_baseline_free_modes_compare_time_error_is_a_config_error():
    """The flat path's OTHER `mode.compare` call: a baseline-free mode on a
    variable the baseline does not hold."""
    time, _, act = _S_DATA["e"]
    result = TestResult(
        model_id="Test.Model",
        success=True,
        variables=[
            VariableResult(index=i + 1, name=n, time=time, values=act)
            for i, n in enumerate(["x", "y"])
        ],
    )
    reference = {
        "test_id": "0001",
        "time": time.tolist(),
        "variables": [{"index": 2, "name": "y", "values": act.tolist()}],
    }
    test = _make_test(
        variable_overrides={"x": {"mode": "event-timing", "events": [{"time": "a"}]}}
    )
    test.variable_patterns = ["x", "y"]
    x, y = compare_test(
        test, result, decode_reference(reference), default_tolerance=0.5
    ).variables
    assert x.diagnostics == {
        "error": "could not convert string to float: 'a'",
        "config_error": True,
    }
    assert not x.passed and x.mode == "event-timing" and y.passed


class TestAMalformedToleranceIsAConfigError:
    @pytest.mark.parametrize("bad", _S_MALFORMED, ids=repr)
    def test_flat_it_is_the_variables_and_the_rest_is_scored(self, bad):
        comp = _sides_compare(
            {"metric": "points", "tolerance": bad},
            flat=True,
            extra={"metric": "nrmse", "tolerance": 0.5},
        )
        x, y = comp.variables
        assert x.diagnostics["config_error"] is True
        assert x.diagnostics["error"] == (
            f'tolerance must be a number or {{"lower": …, "upper": …}}, '
            f"got {json.dumps(bad)}"
        )
        assert y.passed and not comp.passed

    @pytest.mark.parametrize("key", ["tube_abs", "tube_rel", "time_tolerance"])
    @pytest.mark.parametrize("flat", [False, True], ids=["tree", "flat"])
    def test_a_modes_own_key_names_itself(self, key, flat):
        leaf = {
            "tube_abs": {"metric": "tube", "tube_width_mode": "band"},
            "tube_rel": {"metric": "tube", "tube_width_mode": "rel"},
            "time_tolerance": {"metric": "event-timing"},
        }[key]
        kind = "e" if key == "time_tolerance" else "x"
        vc = _sides_compare(
            {**leaf, key: {"lower": 1}}, flat=flat, kind=kind
        ).variables[0]
        assert vc.diagnostics["config_error"] is True
        assert f"{key} must be a number or " in vc.diagnostics["error"]
        assert vc.diagnostics["error"].endswith('got {"lower": 1}')

    @pytest.mark.parametrize(
        ("mode", "key"), [("band", "tube_abs"), ("rel", "tube_rel")]
    )
    def test_the_tube_scorer_names_the_width_it_read(self, mode, key):
        """The scorer reads a raw config too (`TubeMode` hands it Sides):
        each width is refused under its own name."""
        from dstf.comparison.algorithms import _tube_bounds

        with pytest.raises(ValueError, match=f"^{key} must be a number or "):
            _tube_bounds(_S_T, _S_REF, {"tube_width_mode": mode, key: {"lower": 1}})

    @pytest.mark.parametrize(
        "bad",
        [{"lower": 0.1, "upper": 0.2}, "0.1", True, [1], float("nan"), float("inf")],
        ids=repr,
    )
    @pytest.mark.parametrize("flat", [False, True], ids=["tree", "flat"])
    def test_a_tubes_min_width_takes_one_number(self, bad, flat):
        """`tube_min_width` has no sides: a floor under both. Handed a
        pair, a string or a list it was compared with zero as it came, and
        the ``TypeError`` went out of ``compare_test`` and took the run
        down; `true` scored as a floor of 1. Anything but one finite number
        is the leaf's config error, as a width that is no number or pair
        is, and the rest of the test is scored."""
        leaf = {
            "metric": "tube",
            "tube_width_mode": "band",
            "tube_abs": 0.5,
            "tube_min_width": bad,
        }
        comp = _sides_compare(
            leaf, flat=flat, extra={"metric": "nrmse", "tolerance": 0.5}
        )
        x, y = comp.variables
        said = f"tube_min_width must be one number, got {json.dumps(bad)}"
        assert not x.passed and x.diagnostics["config_error"] is True
        assert x.diagnostics["error"] == (
            said if flat else f"metric 'tube' misconfigured: {said}"
        )
        assert y.passed and not comp.passed

    @pytest.mark.parametrize("flat", [False, True], ids=["tree", "flat"])
    @pytest.mark.parametrize("width", [-0.3, None, 0, 0.3])
    def test_a_min_width_that_is_one_number_scores_as_it_did(self, flat, width):
        """A negative one written by hand is no floor at all — every width
        is above it — and scores as the tube without it; `null` is unset."""
        leaf = {"metric": "tube", "tube_width_mode": "band", "tube_abs": 0.005}

        def scored(extra):
            (vc,) = _sides_compare({**leaf, **extra}, flat=flat).variables
            assert "config_error" not in vc.diagnostics, vc.diagnostics
            return vc.passed, vc.tube_points_inside

        # The run is 0.01 above its reference: outside a tube of 0.005.
        assert scored({}) == (False, 0.0)
        floored = width is not None and width > 0
        assert scored({"tube_min_width": width}) == (
            (True, 1.0) if floored else (False, 0.0)
        )

    def test_a_min_width_is_refused_where_the_mode_is_built(self):
        """Where a width that is no number or pair is: a ``ValueError`` out
        of ``resolve_mode``, before anything is scored."""
        from dstf.comparison.modes import resolve_mode

        override = {"mode": "tube", "tube_width_mode": "band", "tube_abs": 0.5}
        pair = {"lower": 0.1, "upper": 0.2}
        with pytest.raises(ValueError) as box:
            resolve_mode({**override, "tube_min_width": pair}, 0.01)
        assert str(box.value) == (
            'tube_min_width must be one number, got {"lower": 0.1, "upper": 0.2}'
        )
        built = resolve_mode({**override, "tube_min_width": 2}, 0.01)
        assert built.config.tube_min_width == 2.0
        assert isinstance(built.config.tube_min_width, float)
        unset = resolve_mode({**override, "tube_min_width": None}, 0.01)
        assert unset.config.tube_min_width == 0.0

    def test_the_tube_scorer_refuses_a_min_width_it_reads_raw(self):
        """The scorer reads a raw config too, and refuses it in the same
        words: no caller of it meets a ``TypeError``."""
        from dstf.comparison.algorithms import _tube_bounds

        raw = {"tube_width_mode": "band", "tube_abs": 0.5, "tube_min_width": "0.1"}
        with pytest.raises(
            ValueError, match='^tube_min_width must be one number, got "0.1"$'
        ):
            _tube_bounds(_S_T, _S_REF, raw)

    @pytest.mark.parametrize("flat", [False, True], ids=["tree", "flat"])
    def test_event_timing_does_not_read_the_leafs_tolerance(self, flat):
        """Its window is ``time_tolerance``; a ``tolerance`` on the leaf is
        the value tolerance of other metrics and widens nothing here."""
        vc = _sides_compare(
            {"metric": "event-timing", "tolerance": 5.0}, flat=flat, kind="e"
        ).variables[0]
        plain = _sides_compare({"metric": "event-timing"}, flat=flat, kind="e")
        assert "config_error" not in vc.diagnostics, vc.diagnostics
        assert vc.diagnostics["time_tolerance"] == 1e-3
        assert _sides_coords(vc) == _sides_coords(plain.variables[0])


class TestATubeWidthOfNullIsAbsent:
    """``tube_abs: null`` / ``tube_rel: null`` say "no width", as they did
    when the width was a number: not a config error."""

    @pytest.mark.parametrize("flat", [False, True], ids=["tree", "flat"])
    @pytest.mark.parametrize(
        ("leaf", "stray"),
        [
            ({"metric": "tube", "tube_width_mode": "rel", "tube_rel": 0.5}, "tube_abs"),
            (
                {"metric": "tube", "tube_width_mode": "band", "tube_abs": 0.5},
                "tube_rel",
            ),
        ],
        ids=["rel", "band"],
    )
    def test_a_stray_null_beside_the_width_in_use(self, leaf, stray, flat):
        with_null = _sides_compare({**leaf, stray: None}, flat=flat).variables[0]
        without = _sides_compare(leaf, flat=flat).variables[0]
        assert "config_error" not in with_null.diagnostics, with_null.diagnostics
        assert _sides_fields(with_null) == _sides_fields(without)
        assert _sides_coords(with_null) == _sides_coords(without)

    @pytest.mark.parametrize("flat", [False, True], ids=["tree", "flat"])
    @pytest.mark.parametrize("key", ["tube_abs", "tube_rel"])
    @pytest.mark.parametrize("falsy", [False, ""], ids=repr)
    def test_only_null_is_no_width_not_any_falsy_value(self, falsy, key, flat):
        """``false`` and ``""`` are no width the scorer can read: a config
        error, as any other non-number is — never dropped as ``null`` is."""
        mode = "band" if key == "tube_abs" else "rel"
        leaf = {"metric": "tube", "tube_width_mode": mode, key: falsy}
        vc = _sides_compare(leaf, flat=flat).variables[0]
        assert vc.diagnostics.get("config_error") is True, vc.diagnostics
        assert f"{key} must be a number" in vc.diagnostics["error"]

    @pytest.mark.parametrize("flat", [False, True], ids=["tree", "flat"])
    @pytest.mark.parametrize("key", ["tube_abs", "tube_rel"])
    def test_a_width_of_zero_is_a_width(self, key, flat):
        mode = "band" if key == "tube_abs" else "rel"
        leaf = {"metric": "tube", "tube_width_mode": mode, key: 0}
        vc = _sides_compare(leaf, flat=flat).variables[0]
        assert "config_error" not in vc.diagnostics, vc.diagnostics

    @pytest.mark.parametrize("flat", [False, True], ids=["tree", "flat"])
    def test_null_for_the_width_in_use_is_no_width(self, flat):
        leaf = {"metric": "tube", "tube_width_mode": "band"}
        null = _sides_compare({**leaf, "tube_abs": None}, flat=flat).variables[0]
        zero = _sides_compare({**leaf, "tube_abs": 0.0}, flat=flat).variables[0]
        assert "config_error" not in null.diagnostics, null.diagnostics
        assert _sides_fields(null) == _sides_fields(zero)


#: Every field that takes a pair (C1): the five leaf keys and the four row keys.
_S_PAIR_FIELDS = {
    **{
        name: (kind, {**leaf, key: _S_UNEVEN})
        for name, (kind, leaf, key, _) in _S_LEAF_KEYS.items()
    },
    "points row tolerance": (
        "x",
        {"metric": "points", "points": [{"time": 2.0, "tolerance": _S_UNEVEN}]},
    ),
    "points row time_tolerance": (
        "x",
        {
            "metric": "points",
            "tolerance": 0.5,
            "points": [{"time": 2.0, "time_tolerance": _S_UNEVEN}],
        },
    ),
    "events row tolerance": (
        "e",
        {
            "metric": "event-timing",
            "count_must_match": False,
            "events": [{"time": 2.0, "tolerance": _S_UNEVEN}],
        },
    ),
    "peaks row tolerance": (
        "f",
        {
            "metric": "dominant-frequency",
            "peaks": [{"freq": 1.0, "tolerance": _S_UNEVEN}],
        },
    ),
}


class TestEveryFieldThatTakesAPairScoresOne:
    """C1's nine fields, on both scoring paths: none is refused, and none is
    scored as one of its sides."""

    @pytest.mark.parametrize("flat", [False, True], ids=["tree", "flat"])
    @pytest.mark.parametrize("name", _S_PAIR_FIELDS)
    def test_an_uneven_pair_scores(self, name, flat):
        kind, leaf = _S_PAIR_FIELDS[name]
        comp = _sides_compare(leaf, flat=flat, kind=kind)
        vc = comp.variables[0]
        assert "error" not in vc.diagnostics, vc.diagnostics
        assert comp.passed, vc.diagnostics

    @pytest.mark.parametrize("flat", [False, True], ids=["tree", "flat"])
    @pytest.mark.parametrize("name", _S_PAIR_FIELDS)
    def test_its_equal_pair_scores_as_the_number(self, name, flat):
        kind, leaf = _S_PAIR_FIELDS[name]
        text = json.dumps(leaf)
        pair = json.loads(text.replace(json.dumps(_S_UNEVEN), json.dumps(_S_EVEN)))
        number = json.loads(text.replace(json.dumps(_S_UNEVEN), "0.05"))
        a = _sides_compare(number, flat=flat, kind=kind).variables[0]
        b = _sides_compare(pair, flat=flat, kind=kind).variables[0]
        assert "error" not in a.diagnostics, a.diagnostics
        # The declared peaks are echoed as authored: the one field that differs.
        for vc in (a, b):
            vc.diagnostics.pop("peaks_declared", None)
        assert _sides_fields(a) == _sides_fields(b)
        assert _sides_coords(a) == _sides_coords(b)

    def test_the_stand_in_guard_is_gone(self):
        from dstf.comparison import modes

        assert not hasattr(modes, "_NOT_SCORED_YET")

    @pytest.mark.parametrize("flat", [False, True], ids=["tree", "flat"])
    def test_nrmse_has_one_ceiling(self, flat):
        vc = _sides_compare(
            {"metric": "nrmse", "tolerance": _S_UNEVEN}, flat=flat
        ).variables[0]
        assert vc.diagnostics["config_error"] is True
        # The tree path leads with "metric 'nrmse' misconfigured: ".
        assert vc.diagnostics["error"].endswith(
            "nrmse has one ceiling: tolerance must be a number"
        )

    @pytest.mark.parametrize("flat", [False, True], ids=["tree", "flat"])
    def test_a_mode_that_reads_no_tolerance_ignores_a_pair_in_it(self, flat):
        comp = _sides_compare(
            {"metric": "range", "min_value": -2.0, "tolerance": _S_UNEVEN}, flat=flat
        )
        assert comp.passed and "config_error" not in comp.variables[0].diagnostics


class TestBaselineFreeCheckTakesAnyTolerance:
    """Both sites parsed a tolerance OUTSIDE their ``try``: a pair raised
    ``TypeError`` and a string ``ValueError`` out of the whole run."""

    @pytest.mark.parametrize(
        "tol,free",
        [
            (_S_UNEVEN, True),
            (_S_EVEN, True),
            (0.1, True),
            ("abc", False),
            (None, False),
        ],
        ids=repr,
    )
    def test_it_returns(self, tol, free):
        # `range` reads no tolerance: a well-formed one never matters to it,
        # and a malformed one means "not baseline-free", as a mode that does
        # not resolve does.
        tree = _make_test()
        tree.metric_tree_spec = parse_metric_tree(
            {"metric": "range", "variable": "x", "min_value": 0, "tolerance": tol}
        )
        flat = _make_test(
            variable_overrides={
                "x": {"mode": "range", "min_value": 0, "tolerance": tol}
            }
        )
        assert _test_is_baseline_free(tree, 1e-4) is free
        assert _test_is_baseline_free(flat, 1e-4) is free


def _said(where: str, value) -> str:
    """The refusal of ``value``, written at ``where``: the value as JSON
    holds it, cut at 60 characters; a dict alone is told where a pair
    belongs."""
    text = json.dumps(value)
    text = text if len(text) <= 60 else text[:60] + "…"
    pair = "; a lower/upper pair belongs on a criterion"
    return (
        f"the base tolerance must be a number{pair if isinstance(value, dict) else ''}"
        f" ({where} is {text})"
    )


class TestABaseToleranceIsANumber:
    """C1: the pair belongs on a criterion; ``comparison.tolerance`` — the
    spec's or the reference JSON's — is one number. Anything else there
    refuses the test: it is never scored at the next tolerance down."""

    SPEC = "test_spec.json comparison.tolerance"
    BASELINE = "the baseline's comparison.tolerance"

    #: What a JSON file can hold there that is no number.
    NOT_NUMBERS = pytest.mark.parametrize(
        "bad",
        [_S_UNEVEN, _S_EVEN, {"lower": 0.01}, "abc", "0.5", [1], True],
        ids=["pair", "even-pair", "one-sided", "string", "digits", "list", "bool"],
    )

    def _refused(self, comp, where, bad):
        assert comp.error_message == _said(where, bad)
        assert not comp.passed
        assert comp.sim_success and comp.variables == []
        assert comp.test_id == "0001"
        assert classify(comp) == "FAIL"

    @staticmethod
    def _discovered(tmp_path, tolerance, flat):
        """The test as ``discover_tests`` reads it from a spec FILE: a
        ``TestModel`` built by hand holds a base no discovery produced."""
        from dstf.config import Config
        from dstf.discovery.test_registry import discover_tests

        entry = {
            "model": "Test.Model",
            "variables": ["x"],
            "comparison": {"tolerance": tolerance},
        }
        if flat:
            entry["comparison"]["variable_overrides"] = {"x": {"mode": "points"}}
        else:
            entry["metrics"] = {"metric": "points", "variable": "x"}
        spec = tmp_path / "test_spec.json"
        spec.write_text(json.dumps({"tests": [entry]}), encoding="utf-8")
        config = Config(source_path=tmp_path, source_type="python", test_spec_file=spec)
        (test,) = discover_tests(config)
        return test

    @pytest.mark.parametrize("flat", [False, True], ids=["tree", "flat"])
    @NOT_NUMBERS
    def test_the_spec_files(self, tmp_path, bad, flat):
        test = self._discovered(tmp_path, bad, flat)
        # The reference JSON holds a number the test would be scored with,
        # were the spec's dropped.
        comp = _sides_compare(
            None, flat=flat, test=test, ref_comparison={"tolerance": 0.5}
        )
        self._refused(comp, self.SPEC, bad)

    @pytest.mark.parametrize("flat", [False, True], ids=["tree", "flat"])
    def test_a_number_in_the_spec_file_is_scored(self, tmp_path, flat):
        test = self._discovered(tmp_path, 0.5, flat)
        assert test.comparison_tolerance == 0.5
        comp = _sides_compare(None, flat=flat, test=test)
        assert comp.passed and comp.error_message is None
        assert comp.variables[0].tolerance_used == Sides(0.5, 0.5)

    def test_a_simulation_number_that_is_none_is_refused_at_parse(self, tmp_path):
        """A simulation value has no compare to be refused at: the parser
        names it, where it used to drop it with a warning."""
        from dstf.config import ConfigError
        from dstf.discovery.spec_parser import parse_test_spec

        spec = tmp_path / "test_spec.json"
        entry = {"model": "Test.Model", "simulation": {"stop_time": {"lower": 1}}}
        spec.write_text(json.dumps({"tests": [entry]}), encoding="utf-8")
        with pytest.raises(ConfigError, match="simulation.stop_time: must be a number"):
            parse_test_spec(spec)

    @pytest.mark.parametrize("flat", [False, True], ids=["tree", "flat"])
    @NOT_NUMBERS
    def test_the_reference_jsons(self, bad, flat):
        comp = _sides_compare(
            {"metric": "points"}, flat=flat, ref_comparison={"tolerance": bad}
        )
        self._refused(comp, self.BASELINE, bad)

    @pytest.mark.parametrize("flat", [False, True], ids=["tree", "flat"])
    @pytest.mark.parametrize(
        "bad", [float("nan"), float("inf"), -float("inf"), 10**400], ids=repr
    )
    def test_a_base_that_is_not_finite_is_refused_from_either(self, bad, flat):
        comp = _sides_compare(
            {"metric": "points"}, flat=flat, ref_comparison={"tolerance": bad}
        )
        self._refused(comp, self.BASELINE, bad)
        comp = _sides_compare({"metric": "points"}, flat=flat, comparison_tolerance=bad)
        self._refused(comp, self.SPEC, bad)

    @pytest.mark.parametrize("flat", [False, True], ids=["tree", "flat"])
    def test_a_null_base_in_the_reference_json_is_unset(self, flat):
        """``"comparison": {"tolerance": null}`` is no base: the key being
        there refuses nothing, and the project's tolerance is in effect."""
        comp = _sides_compare(
            {"metric": "points"}, flat=flat, ref_comparison={"tolerance": None}
        )
        assert comp.error_message is None and comp.variables
        assert comp.tolerance_source == "project" and comp.base_tolerance == 1e-4
        assert comp.variables[0].tolerance_used == Sides(1e-4, 1e-4)

    def test_the_sentence_cuts_a_long_value_at_60_characters(self):
        from dstf.comparison.sides import base_tolerance_refused

        at_60 = "a" * 58  # the two quotes make 60
        assert base_tolerance_refused("X", at_60) == (
            f'the base tolerance must be a number (X is "{at_60}")'
        )
        assert base_tolerance_refused("X", "a" * 59) == (
            f'the base tolerance must be a number (X is "{"a" * 59}…)'
        )

    def test_a_direct_callers_project_tolerance_is_judged_too(self):
        test = _make_test()
        test.variable_patterns = ["x"]
        time, ref, act = _S_DATA["x"]
        result = TestResult(
            model_id="Test.Model",
            success=True,
            variables=[VariableResult(index=1, name="x", time=time, values=act)],
        )
        comp = compare_test(
            test,
            result,
            decode_reference({"test_id": "0001"}),
            default_tolerance=float("nan"),
        )
        self._refused(comp, "the project's tolerance", float("nan"))

    @pytest.mark.parametrize("raw", [0, 0.0, -0.0])
    def test_an_explicit_zero_in_the_spec_file_is_carried(self, tmp_path, raw):
        """Zero is the strictest tolerance, not an unset one: read with a
        truthiness test it fell to the baseline's 0.5."""
        test = self._discovered(tmp_path, raw, flat=False)
        assert test.comparison_tolerance == 0 and test.comparison_tolerance is not None
        comp = _sides_compare(
            None, flat=False, test=test, ref_comparison={"tolerance": 0.5}
        )
        assert comp.variables[0].tolerance_used == Sides(0.0, 0.0)
        assert comp.error_message is None

    def test_a_simulate_only_test_is_refused_as_any_other(self):
        """One decision: the base is judged before the test is anything else."""
        test = _make_test(comparison_tolerance="abc")
        test.simulate_only = True
        result = TestResult(model_id="Test.Model", success=True, variables=[])
        comp = compare_test(test, result, decode_reference({"test_id": "0001"}))
        self._refused(comp, self.SPEC, "abc")
        test.comparison_tolerance = 0.5
        assert compare_test(test, result, decode_reference({"test_id": "0001"})).passed

    def test_a_failed_simulation_and_no_result_keep_their_own_status(self):
        """Nothing ran, so nothing was refused: SIM_FAIL and NOT RUN say what
        happened, whatever the spec holds."""
        from dstf.comparison.comparator import compare_one

        class _Store:
            def get_reference(self, model_id):
                return None

        test = _make_test(comparison_tolerance="abc")
        failed = TestResult(model_id="Test.Model", success=False)
        assert classify(compare_one(test, failed, _Store())) == "SIM_FAIL"
        assert classify(compare_one(test, None, _Store())) == "NOT RUN"

    @pytest.mark.parametrize("kind", ["nrmse", "range"])
    @NOT_NUMBERS
    def test_no_baseline_is_the_refusal_not_no_ref(self, bad, kind):
        """``compare_one`` judges the base BEFORE the no-baseline
        short-circuit: NO_REF tells the person to accept, and a
        baseline-free test passed over the base it could not read."""
        from dstf.comparison.comparator import compare_one

        class _Store:
            def get_reference(self, model_id):
                return None

        test = _make_test(comparison_tolerance=bad)
        test.variable_patterns = ["x"]
        if kind == "range":
            test.variable_overrides = {"x": {"mode": "range", "min_value": -1e9}}
        time, _ref, act = _S_DATA["x"]
        result = TestResult(
            model_id="Test.Model",
            success=True,
            variables=[VariableResult(index=1, name="x", time=time, values=act)],
        )
        comp = compare_one(test, result, _Store())
        assert comp.error_message == _said(self.SPEC, bad)
        assert classify(comp) == "FAIL" and comp.variables == []
        assert comp.has_reference is False and comp.test_id is None
        from dstf.reporting.status import refusal

        assert refusal(comp) == comp.error_message

    def test_a_number_in_either_is_scored_as_before(self):
        comp = _sides_compare(
            {"metric": "points"}, flat=False, ref_comparison={"tolerance": 0.5}
        )
        assert comp.passed and comp.error_message is None
        assert comp.variables[0].tolerance_used == Sides(0.5, 0.5)


# ---------------------------------------------------------------------------
# Points: a lower and an upper tolerance, in value and in time
# ---------------------------------------------------------------------------

import random  # noqa: E402

from dstf.comparison.algorithms import (  # noqa: E402
    _min_delta_in_box,
    _violation_in_box,
)

_P_T = np.linspace(0.0, 10.0, 101)
_P_FLAT = np.full_like(_P_T, 2.0)


def _pts(act, points, tolerance=1e-4, ref=_P_FLAT, time=_P_T):
    return score(
        {"mode": "points", "points": points}, time, ref, time, act, tolerance=tolerance
    )


class TestPointsValueSides:
    """``d = actual − target`` passes iff ``−lower <= d <= upper``."""

    _UNEVEN = {"lower": 0.01, "upper": 0.05}

    @pytest.mark.parametrize(
        "row,reason",
        [
            ({}, "no comparable sample"),
            ({"time_tolerance": 0.2}, "no comparable sample in the time box"),
            # A box open on one side only is a box.
            (
                {"time_tolerance": {"lower": 0, "upper": 0.2}},
                "no comparable sample in the time box",
            ),
            (
                {"time_tolerance": {"lower": 0.2, "upper": 0}},
                "no comparable sample in the time box",
            ),
            ({"time_tolerance": {"lower": 0, "upper": 0}}, "no comparable sample"),
        ],
        ids=["no time box", "time box", "later only", "earlier only", "zero pair"],
    )
    def test_a_box_with_no_comparable_sample_says_whether_it_is_a_box(
        self, row, reason
    ):
        act = _P_FLAT.copy()
        act[40:61] = np.nan  # t in [4, 6]
        vc = _pts(act, [{"time": 5.0, "tolerance": self._UNEVEN, **row}])
        assert vc.diagnostics["point_failures"] == [{"time": 5.0, "reason": reason}]

    @pytest.mark.parametrize(
        "act,criterion",
        [
            (
                [0.0, 0.0, 0.0],
                "Point at t=1: error −1.000e+00 vs tolerance "
                "−5.000e-01 / +2.000e+00 → FAIL",
            ),
            (
                [np.nan] * 3,
                "Point at t=1: error +inf vs tolerance −5.000e-01 / +2.000e+00 → FAIL",
            ),
        ],
        ids=["a miss of exactly one", "no comparable sample"],
    )
    def test_the_sentence_signs_the_reported_points_error(self, act, criterion):
        vc = score(
            {
                "mode": "points",
                "points": [
                    {"time": 1, "value": 1, "tolerance": {"lower": 0.5, "upper": 2}}
                ],
            },
            EMPTY,
            EMPTY,
            np.array([0.0, 1.0, 2.0]),
            np.array(act),
        )
        assert summarize(vc).criterion == criterion

    @pytest.mark.parametrize("row", [{}, {"time_tolerance": 0.2}], ids=repr)
    def test_a_target_that_is_not_a_number_says_so(self, row):
        point = {"time": 5.0, "value": float("nan"), "tolerance": self._UNEVEN}
        vc = _pts(_P_FLAT, [{**point, **row}])
        assert vc.diagnostics["point_failures"] == [
            {"time": 5.0, "reason": "the target is not a number"}
        ]

    def test_above_passes_with_room_above_and_fails_with_the_sides_swapped(self):
        act = _P_FLAT + 0.03
        ok = _pts(act, [{"time": 5.0, "tolerance": {"lower": 0.01, "upper": 0.05}}])
        bad = _pts(act, [{"time": 5.0, "tolerance": {"lower": 0.05, "upper": 0.01}}])
        assert ok.passed and not bad.passed

    def test_below_is_the_mirror(self):
        act = _P_FLAT - 0.03
        ok = _pts(act, [{"time": 5.0, "tolerance": {"lower": 0.05, "upper": 0.01}}])
        bad = _pts(act, [{"time": 5.0, "tolerance": {"lower": 0.01, "upper": 0.05}}])
        assert ok.passed and not bad.passed

    def test_a_row_without_one_takes_the_leafs_pair(self):
        act = _P_FLAT + 0.03
        assert _pts(act, [{"time": 5.0}], tolerance=Sides(0.01, 0.05)).passed
        assert not _pts(act, [{"time": 5.0}], tolerance=Sides(0.05, 0.01)).passed
        # ``null`` is "not set", as an absent key is.
        assert _pts(
            act, [{"time": 5.0, "tolerance": None}], tolerance=Sides(0.01, 0.05)
        ).passed

    def test_a_row_without_one_takes_the_leafs_lower_side_too(self):
        """Below the target it is the leaf's LOWER side that decides: a row
        that took only the upper one, as both, gets each of these wrong."""
        act = _P_FLAT - 0.03
        assert _pts(act, [{"time": 5.0}], tolerance=Sides(0.05, 0.01)).passed
        assert not _pts(act, [{"time": 5.0}], tolerance=Sides(0.01, 0.05)).passed

    def test_a_rows_null_time_tolerance_is_absent(self):
        """``"time_tolerance": null`` is "not set" — no time box — and not
        a malformed pair."""
        act = _P_FLAT + 0.03
        null = _pts(act, [{"time": 5.0, "time_tolerance": None}], tolerance=0.05)
        absent = _pts(act, [{"time": 5.0}], tolerance=0.05)
        assert null.passed and absent.passed
        assert _sides_fields(null) == _sides_fields(absent)
        assert _sides_coords(null) == _sides_coords(absent)
        (box,) = [p for p in null.geometry if p.id == "points/box/0"]
        assert (box.t0, box.t1) == (5.0, 5.0)

    def test_a_box_with_no_comparable_sample_says_so(self):
        """Every sample in the box is NaN: there is no closest value to
        name. Equal sides keep the sentence a number always gave."""
        act = np.full_like(_P_T, np.nan)
        uneven = _pts(act, [{"time": 5.0, "value": 2.0, "tolerance": _S_UNEVEN}])
        assert not uneven.passed
        # No time tolerance: no box to name.
        assert uneven.diagnostics["point_failures"] == [
            {"time": 5.0, "reason": "no comparable sample"}
        ]
        even = _pts(act, [{"time": 5.0, "value": 2.0, "tolerance": 0.05}])
        assert even.diagnostics["point_failures"] == [
            {"time": 5.0, "reason": "delta inf exceeds limit 0.05"}
        ]

    def test_rel_scales_each_side_by_the_targets_magnitude(self):
        ref = np.full_like(_P_T, -10.0)
        row = {"time": 5.0, "tolerance_mode": "rel"}
        wide_up = {**row, "tolerance": {"lower": 0.01, "upper": 0.05}}
        wide_down = {**row, "tolerance": {"lower": 0.05, "upper": 0.01}}
        # 0.3 above −10: +5 % of |−10| is 0.5, +1 % is 0.1.
        assert _pts(ref + 0.3, [wide_up], ref=ref).passed
        assert not _pts(ref + 0.3, [wide_down], ref=ref).passed
        # 0.3 below: a scale applied to one side only passes or fails these.
        assert _pts(ref - 0.3, [wide_down], ref=ref).passed
        assert not _pts(ref - 0.3, [wide_up], ref=ref).passed

    def test_a_curve_above_the_band_at_one_sample_and_below_at_the_next_crossed_it(
        self,
    ):
        time = np.array([0.0, 1.0, 2.0, 3.0])
        act = np.array([5.0, 5.0, -5.0, -5.0])
        row = {
            "time": 1.5,
            "value": 1.0,
            "time_tolerance": 0.5,
            "tolerance": {"lower": 0.1, "upper": 0.3},
        }
        vc = score({"mode": "points", "points": [row]}, time, np.zeros(4), time, act)
        assert vc.passed
        # Without the box in time there is one candidate, far above: no crossing.
        assert not score(
            {"mode": "points", "points": [{**row, "time_tolerance": 0}]},
            time,
            np.zeros(4),
            time,
            act,
        ).passed

    def test_the_reason_names_the_band_for_unequal_sides_only(self):
        vc = _pts(
            _P_FLAT + 1.0, [{"time": 5.0, "tolerance": {"lower": 0.1, "upper": 0.4}}]
        )
        assert vc.diagnostics["point_failures"][0]["reason"] == (
            "closest value 3 is outside [1.9, 2.4]"
        )
        even = _pts(_P_FLAT + 1.0, [{"time": 5.0, "tolerance": 0.25}])
        assert even.diagnostics["point_failures"][0]["reason"] == (
            "delta 1 exceeds limit 0.25"
        )
        pair = _pts(
            _P_FLAT + 1.0, [{"time": 5.0, "tolerance": {"lower": 0.25, "upper": 0.25}}]
        )
        assert pair.diagnostics == even.diagnostics


class TestPointsTimeSides:
    """The time box is ``[t − lower, t + upper]``: ``lower`` is how much
    EARLIER, ``upper`` how much LATER."""

    def test_a_late_crossing_is_caught_and_an_early_one_is_not(self):
        late = np.where(_P_T >= 6.0, 2.0, 0.0)  # reaches the target 1 s late
        early = np.where(_P_T <= 4.0, 2.0, 0.0)  # was there 1 s early, then left
        row = {"time": 5.0, "value": 2.0, "tolerance": 0.01}
        after = {**row, "time_tolerance": {"lower": 0.0, "upper": 1.5}}
        before = {**row, "time_tolerance": {"lower": 1.5, "upper": 0.0}}
        assert _pts(late, [after]).passed
        assert not _pts(early, [after]).passed
        assert _pts(early, [before]).passed
        assert not _pts(late, [before]).passed


class TestPointsFinalCheckSides:
    @pytest.mark.parametrize(
        "offset,tol,ok",
        [
            (0.375, Sides(0.125, 0.5), True),
            (0.375, Sides(0.5, 0.125), False),
            (-0.375, Sides(0.5, 0.125), True),
            (-0.375, Sides(0.125, 0.5), False),
            (0.5, Sides(0.25, 0.5), False),  # strict on the upper side
            (-0.25, Sides(0.25, 0.5), False),  # strict on the lower side
            (0.25, Sides(0.25, 0.5), True),
            (-0.125, Sides(0.25, 0.5), True),
        ],
    )
    def test_it_is_strict_on_each_side(self, offset, tol, ok):
        vc = _pts(_P_FLAT + offset, None, tolerance=tol)
        assert vc.passed is ok
        assert vc.nrmse == abs(offset) and vc.diagnostics["delta"] == abs(offset)
        # Leaves Python as data: the pair, serialisable.
        assert vc.diagnostics["tolerance"] == {"lower": tol.lower, "upper": tol.upper}
        json.dumps(vc.diagnostics)

    def test_equal_sides_are_written_as_the_number(self):
        vc = _pts(_P_FLAT, None, tolerance=Sides(0.25, 0.25))
        assert vc.diagnostics["tolerance"] == 0.25
        assert isinstance(vc.diagnostics["tolerance"], float)

    @pytest.mark.parametrize(
        "ref,act",
        [(([1], [2]), ([0, 1], [2, 2])), (([0, 1], [2, 2]), ([1], [2]))],
        ids=["one-sample reference", "one-sample actual"],
    )
    def test_a_one_sample_trajectory_is_checked(self, ref, act):
        vc = score(
            {"mode": "points"}, *(np.array(x, dtype=float) for x in (*ref, *act))
        )
        assert vc.passed is True
        assert vc.diagnostics["delta"] == 0.0

    def test_an_empty_reference_is_not_scored(self):
        vc = score(
            {"mode": "points"}, EMPTY, EMPTY, np.array([0.0, 1.0]), np.array([2.0, 2.0])
        )
        assert vc.passed is False
        assert (
            summarize(vc).criterion == "points criterion not scored — empty trajectory"
        )


class TestPointsIdentity:
    """A number scores as it did before a pair existed — to the bit."""

    def test_violation_is_zero_exactly_when_delta_is_within_the_limit(self):
        # ``lo <= v <= hi`` with ``lo = target − limit`` is NOT today's
        # ``|v − target| <= limit``: the two differ by a rounding step at the
        # edge. So half the cases put a sample exactly ON an edge, as floating
        # point computes it, and one step either side of it.
        rng = random.Random(20261002)
        checked = on_edge = flipped_by_band_edges = 0
        for k in range(20000):
            m = rng.randint(2, 12)
            at = np.sort(np.array([rng.uniform(0, 10) for _ in range(m)]))
            av = np.array(
                [round(rng.uniform(-3, 3), rng.choice([1, 6])) for _ in range(m)]
            )
            if rng.random() < 0.1:
                av[rng.randrange(m)] = np.nan
            target = round(rng.uniform(-3, 3), rng.choice([1, 6]))
            lim = rng.choice(
                [round(rng.uniform(0, 2), 1), rng.uniform(0, 2), 0.0, -0.5]
            )
            t = rng.uniform(-1, 11)
            xt = rng.choice([0.0, rng.uniform(0, 3)])
            edge = None
            if k % 2:
                i = edge = rng.randrange(m)
                av[i] = rng.choice(
                    [
                        target + lim,
                        target - lim,
                        np.nextafter(target + lim, 9),
                        np.nextafter(target - lim, -9),
                    ]
                )
                t = float(at[i])
                on_edge += 1
            t_lo, t_hi = max(t - xt, float(at[0])), min(t + xt, float(at[-1]))
            if t_hi < t_lo:
                continue
            delta, _ = _min_delta_in_box(at, av, t_lo, t_hi, target)
            violation, _ = _violation_in_box(at, av, t_lo, t_hi, target, lim, lim)
            assert (violation == 0.0) == (delta <= lim), (k, target, lim, delta)
            checked += 1
            # The sweep must reach the cases where judging the sample against
            # the band's edges (``target ± limit``) would have flipped today's
            # verdict.
            if (
                edge is not None
                and target - lim <= av[edge] <= target + lim
                and not delta <= lim
            ):
                flipped_by_band_edges += 1
        assert checked > 15000 and on_edge == 10000
        assert flipped_by_band_edges > 100

    def test_the_edge_case_keeps_todays_verdict(self):
        # |1.1 − 1.0| is 0.10000000000000009 in floating point: today's FAIL,
        # and a PASS if the sample were tested against 1.0 + 0.1 == 1.1.
        time = np.array([0.0, 1.0])
        for tol in (0.1, {"lower": 0.1, "upper": 0.1}):
            vc = score(
                {
                    "mode": "points",
                    "points": [{"time": 1.0, "value": 1.0, "tolerance": tol}],
                },
                time,
                np.ones(2),
                time,
                np.full(2, 1.1),
            )
            assert not vc.passed

    def test_a_negative_side_keeps_failing_as_a_negative_number_does(self):
        # An inverted band holds nothing — a curve that "straddles" it too.
        time = np.array([0.0, 1.0, 2.0])
        act = np.array([5.0, -5.0, 5.0])
        row = {"time": 1.0, "value": 0.0, "time_tolerance": 1.0}
        for tol in (
            -0.5,
            {"lower": -0.5, "upper": -0.5},
            {"lower": -0.5, "upper": 0.1},
        ):
            vc = score(
                {"mode": "points", "points": [{**row, "tolerance": tol}]},
                time,
                np.zeros(3),
                time,
                act,
            )
            assert not vc.passed, tol

    @pytest.mark.parametrize("flat", [False, True], ids=["tree", "flat"])
    def test_a_rows_number_and_its_equal_pair_score_the_same(self, flat):
        def leaf(pair):
            def v(x):
                return {"lower": x, "upper": x} if pair else x

            return {
                "metric": "points",
                "tolerance": v(0.02),
                "points": [
                    {"time": 2.0},
                    {"time": 5.0, "tolerance": v(0.05), "tolerance_mode": "rel"},
                    {
                        "time": 7.3,
                        "value": 1.5,
                        "tolerance": v(0.2),
                        "time_tolerance": v(0.4),
                    },
                    {"time": None, "tolerance": v(0.001)},
                    {"time": 3.0, "time_tolerance": v(0.1)},
                ],
            }

        a = _sides_compare(leaf(False), flat=flat).variables[0]
        b = _sides_compare(leaf(True), flat=flat).variables[0]
        assert "config_error" not in a.diagnostics, a.diagnostics
        assert a.diagnostics["scored_points"] == 5
        assert _sides_fields(a) == _sides_fields(b)
        assert _sides_coords(a) == _sides_coords(b)


class TestPointsThroughBothPaths:
    @pytest.mark.parametrize("flat", [False, True], ids=["tree", "flat"])
    def test_an_uneven_pair_in_each_of_its_fields_scores(self, flat):
        leaf = {
            "metric": "points",
            "tolerance": {"lower": 0.5, "upper": 0.1},
            "points": [
                {"time": 1.0, "value": 0.9, "time_tolerance": {"lower": 0, "upper": 1}},
                {"time": 2.0, "tolerance": {"lower": 0.001, "upper": 0.5}},
            ],
        }
        comp = _sides_compare(leaf, flat=flat)
        vc = comp.variables[0]
        assert "config_error" not in vc.diagnostics, vc.diagnostics
        assert comp.passed and vc.diagnostics["scored_points"] == 2
        assert vc.tolerance_used == Sides(0.5, 0.1)

    @pytest.mark.parametrize("flat", [False, True], ids=["tree", "flat"])
    @pytest.mark.parametrize("key", ["tolerance", "time_tolerance"])
    @pytest.mark.parametrize("bad", [{"lower": 1}, "abc", True], ids=repr)
    def test_a_malformed_row_is_the_leafs_config_error_naming_the_row(
        self, bad, key, flat
    ):
        leaf = {"metric": "points", "points": [{"time": 1.0}, {"time": 2.0, key: bad}]}
        comp = _sides_compare(
            leaf, flat=flat, extra={"metric": "nrmse", "tolerance": 0.5}
        )
        x, y = comp.variables
        assert x.diagnostics["config_error"] is True
        assert x.diagnostics["error"].endswith(
            f'points[1].{key} must be a number or {{"lower": …, "upper": …}}, '
            f"got {json.dumps(bad)}"
        )
        assert y.passed and not comp.passed  # the run goes on


class TestScalarAndTubeThroughBothPaths:
    @pytest.mark.parametrize("flat", [False, True], ids=["tree", "flat"])
    @pytest.mark.parametrize("name", ["scalar tolerance", "tube_abs", "tube_rel"])
    def test_an_uneven_pair_scores(self, name, flat):
        # The actual is 0.01 above the reference everywhere.
        _, leaf, key, _ = _S_LEAF_KEYS[name]
        room_above = {"lower": 0.0001, "upper": 0.5}
        room_below = {"lower": 0.5, "upper": 0.0001}
        ok = _sides_compare({**leaf, key: room_above}, flat=flat, kind="o")
        bad = _sides_compare({**leaf, key: room_below}, flat=flat, kind="o")
        for comp in (ok, bad):
            assert "config_error" not in comp.variables[0].diagnostics
        assert ok.passed and not bad.passed
