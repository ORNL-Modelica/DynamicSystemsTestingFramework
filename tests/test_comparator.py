"""Tests for comparison/comparator.py — NRMSE, events, structural warnings."""

import numpy as np
import pytest

from dstf.backends.types import TestResult
from dstf.comparison.comparator import (
    _check_structural_changes,
    _compare_points,
    _compare_trajectories,
    _dedup_time_series,
    _find_event_boundaries,
    _split_segments,
)

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
        vc = _compare_trajectories(t, v, t, v, 1e-4)
        assert vc.passed is True
        assert vc.nrmse == 0.0
        assert vc.rmse == 0.0

    def test_identical_with_single_event(self):
        """Identical signals with one event => NRMSE = 0."""
        t = np.array([0.0, 0.5, 1.0, 1.0, 1.5, 2.0])
        v = np.array([0.0, 0.5, 0.9, 1.1, 1.5, 2.0])
        vc = _compare_trajectories(t, v, t, v, 1e-4)
        assert vc.passed is True
        assert vc.nrmse == 0.0

    def test_identical_with_multiple_events(self):
        """Identical signals with multiple events => NRMSE = 0."""
        t = np.array([0.0, 0.5, 1.0, 1.0, 1.5, 2.0, 2.0, 2.5, 3.0])
        v = np.array([0.0, 0.5, 0.9, 1.1, 1.5, 1.9, 2.1, 2.5, 3.0])
        vc = _compare_trajectories(t, v, t, v, 1e-4)
        assert vc.passed is True
        assert vc.nrmse == 0.0

    def test_identical_three_events(self):
        """Identical signals with three events (double time points) => NRMSE = 0."""
        t = np.array([0.0, 1.0, 1.0, 2.0, 2.0, 3.0, 3.0, 4.0])
        v = np.array([0.0, 0.9, 1.1, 1.9, 2.1, 2.9, 3.1, 4.0])
        vc = _compare_trajectories(t, v, t, v, 1e-4)
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
        vc = _compare_trajectories(t, v, t, v, 1e-4)
        assert vc.passed is True
        assert vc.nrmse == 0.0

    def test_different_signals(self):
        """Different signals => non-zero NRMSE, should fail."""
        t = np.array([0.0, 0.5, 1.0, 1.5, 2.0])
        ref_v = np.array([0.0, 0.5, 1.0, 1.5, 2.0])
        act_v = np.array([0.0, 0.5, 1.0, 1.5, 2.5])
        vc = _compare_trajectories(t, ref_v, t, act_v, 1e-4)
        assert vc.passed is False
        assert vc.nrmse > 0
        assert vc.max_abs_error == pytest.approx(0.5)
        assert vc.max_abs_error_time == pytest.approx(2.0)

    def test_different_with_event(self):
        """Different signals with event => non-zero NRMSE."""
        t = np.array([0.0, 0.5, 1.0, 1.0, 1.5, 2.0])
        ref_v = np.array([0.0, 0.5, 0.9, 1.1, 1.5, 2.0])
        act_v = np.array([0.0, 0.5, 0.9, 1.2, 1.6, 2.1])
        vc = _compare_trajectories(t, ref_v, t, act_v, 1e-4)
        assert vc.passed is False
        assert vc.nrmse > 0
        assert vc.max_abs_error == pytest.approx(0.1)

    def test_constant_signal(self):
        """Constant signal => is_constant=True, passed=True."""
        t = np.array([0.0, 1.0, 2.0])
        v = np.array([5.0, 5.0, 5.0])
        vc = _compare_trajectories(t, v, t, v, 1e-4)
        assert vc.passed is True
        assert vc.is_constant == True
        assert vc.nrmse == 0.0

    def test_constant_with_small_difference(self):
        """Near-constant signal with tiny deviation => normalized by magnitude."""
        t = np.array([0.0, 1.0, 2.0])
        ref_v = np.array([5.0, 5.0, 5.0])
        act_v = np.array([5.0, 5.0, 5.001])
        vc = _compare_trajectories(t, ref_v, t, act_v, 1e-4)
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
        vc = _compare_trajectories(t, ref_v, t, act_v, 1e-4)
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
        vc = _compare_trajectories(t, ref_v, t, act_v, 1e-4)
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
        vc = _compare_trajectories(ref_t, ref_v, act_t, act_v, 1e-4)
        assert vc.passed is True
        assert vc.nrmse == pytest.approx(0.0, abs=1e-10)

    def test_signal_range(self):
        """Signal range is computed from full reference including jumps."""
        t = np.array([0.0, 1.0, 1.0, 2.0])
        v = np.array([0.0, 0.0, 10.0, 10.0])
        vc = _compare_trajectories(t, v, t, v, 1e-4)
        assert vc.signal_range == pytest.approx(10.0)

    def test_reference_and_actual_finals(self):
        """Final values are correctly reported."""
        t = np.array([0.0, 1.0, 2.0])
        ref_v = np.array([0.0, 1.0, 2.0])
        act_v = np.array([0.0, 1.0, 2.5])
        vc = _compare_trajectories(t, ref_v, t, act_v, 1.0)
        assert vc.reference_final == pytest.approx(2.0)
        assert vc.actual_final == pytest.approx(2.5)

    def test_within_tolerance_passes(self):
        """Signal with small difference within tolerance passes."""
        t = np.linspace(0, 10, 101)
        ref_v = np.sin(t)
        act_v = np.sin(t) + 0.001 * np.random.default_rng(42).standard_normal(len(t))
        vc = _compare_trajectories(t, ref_v, t, act_v, 0.01)
        assert vc.passed is True


# ---------------------------------------------------------------------------
# Final value comparison
# ---------------------------------------------------------------------------


class TestCompareFinalValues:
    """Coverage for `_compare_points` in its empty-points / final-only branch."""

    _t = np.array([0.0, 1.0])

    def test_identical(self):
        vc = _compare_points(
            self._t, np.array([1.0, 1.0]), self._t, np.array([1.0, 1.0]), tolerance=1e-4
        )
        assert vc.passed is True
        assert vc.nrmse == 0.0

    def test_different(self):
        vc = _compare_points(
            self._t, np.array([1.0, 1.0]), self._t, np.array([1.1, 1.1]), tolerance=1e-4
        )
        assert vc.passed is False
        assert vc.nrmse == pytest.approx(0.1)

    def test_zero_reference(self):
        """Near-zero reference still measures absolute delta."""
        vc = _compare_points(
            self._t,
            np.array([0.0, 0.0]),
            self._t,
            np.array([0.0001, 0.0001]),
            tolerance=1e-4,
        )
        assert vc.passed is False

    def test_both_zero(self):
        vc = _compare_points(
            self._t, np.array([0.0, 0.0]), self._t, np.array([0.0, 0.0]), tolerance=1e-4
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
        warnings = _check_structural_changes(ref, result)
        assert len(warnings) == 0

    def test_continuous_states_change(self):
        ref = {"statistics": {"translation": {"continuous_time_states": 4}}}
        result = TestResult(
            model_id="Test",
            success=True,
            statistics={"translation": {"continuous_time_states": 6}},
        )
        warnings = _check_structural_changes(ref, result)
        assert len(warnings) == 1
        assert "Continuous" in warnings[0].field

    def test_nonlinear_change(self):
        ref = {"statistics": {"translation": {"nonlinear_count": 3}}}
        result = TestResult(
            model_id="Test",
            success=True,
            statistics={"translation": {"nonlinear_count": 5}},
        )
        warnings = _check_structural_changes(ref, result)
        assert len(warnings) == 1
        assert "Nonlinear" in warnings[0].field
        assert warnings[0].reference_value == "3"
        assert warnings[0].current_value == "5"

    def test_event_counter_change(self):
        ref = {"statistics": {"EventCounter": 42}}
        result = TestResult(
            model_id="Test",
            success=True,
            statistics={"EventCounter": 50},
        )
        warnings = _check_structural_changes(ref, result)
        assert len(warnings) == 1
        assert "Event" in warnings[0].field

    def test_missing_stats_no_crash(self):
        """Missing statistics on either side should not crash."""
        ref = {"statistics": {}}
        result = TestResult(model_id="Test", success=True, statistics=None)
        warnings = _check_structural_changes(ref, result)
        assert len(warnings) == 0

    def test_no_statistics_key(self):
        ref = {}
        result = TestResult(model_id="Test", success=True, statistics={})
        warnings = _check_structural_changes(ref, result)
        assert len(warnings) == 0


# ---------------------------------------------------------------------------
# Tolerance resolution
# ---------------------------------------------------------------------------

from pathlib import Path

from dstf.backends.types import VariableResult
from dstf.comparison.comparator import (
    _compare_tube,
    _interpolate_tube_widths,
    compare_test,
)
from dstf.discovery.test_registry import TestModel


def _make_test(comparison_tolerance=None, variable_overrides=None):
    """Create a minimal TestModel for tolerance tests.

    Tracked by its spec: the result file names its one variable ``x`` (a
    UnitTests test's would be ``unitTests.x[1]``, whatever the model wrote
    for it)."""
    return TestModel(
        model_id="Test.Model",
        source_file=Path(""),
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
        comp = compare_test(test, result, ref, default_tolerance=1e-4)
        # offset=0.001 on range=2.0 → NRMSE=5e-4, exceeds 1e-4
        assert not comp.passed
        assert comp.variables[0].tolerance_used == 1e-4

    def test_per_test_tolerance_overrides_config(self):
        """Per-test comparison_tolerance overrides config.tolerance."""
        test = _make_test(comparison_tolerance=0.01)
        result, ref = _make_result_and_ref(offset=0.001)
        comp = compare_test(test, result, ref, default_tolerance=1e-4)
        # NRMSE=5e-4, tolerance=0.01 → passes
        assert comp.passed
        assert comp.variables[0].tolerance_used == 0.01

    def test_per_variable_tolerance_overrides_test(self):
        """Per-variable override takes precedence over per-test."""
        test = _make_test(
            comparison_tolerance=1e-6,  # Very tight
            variable_overrides={"x": {"tolerance": 0.01}},  # But x is loose
        )
        result, ref = _make_result_and_ref(offset=0.001)
        comp = compare_test(test, result, ref, default_tolerance=1e-4)
        # Per-variable 0.01 used for x → passes despite tight per-test
        assert comp.passed
        assert comp.variables[0].tolerance_used == 0.01

    def test_reference_tolerance_used_as_fallback(self):
        """Comparison tolerance stored in reference JSON is used as fallback."""
        test = _make_test()  # No comparison_tolerance set
        result, ref = _make_result_and_ref(offset=0.001)
        ref["comparison"] = {"tolerance": 0.01}
        comp = compare_test(test, result, ref, default_tolerance=1e-4)
        # ref comparison tolerance 0.01 used → passes
        assert comp.passed
        assert comp.variables[0].tolerance_used == 0.01

    def test_reference_variable_overrides(self):
        """Per-variable overrides from reference JSON are used."""
        test = _make_test()
        result, ref = _make_result_and_ref(offset=0.001)
        ref["comparison"] = {
            "variable_overrides": {"x": {"tolerance": 0.01}},
        }
        comp = compare_test(test, result, ref, default_tolerance=1e-4)
        assert comp.passed
        assert comp.variables[0].tolerance_used == 0.01

    def test_spec_overrides_reference(self):
        """Spec variable overrides take precedence over reference overrides."""
        test = _make_test(variable_overrides={"x": {"tolerance": 1e-6}})
        result, ref = _make_result_and_ref(offset=0.001)
        ref["comparison"] = {
            "variable_overrides": {"x": {"tolerance": 0.1}},  # Loose in ref
        }
        comp = compare_test(test, result, ref, default_tolerance=1e-4)
        # Spec override (1e-6) takes precedence over reference (0.1)
        assert not comp.passed
        assert comp.variables[0].tolerance_used == 1e-6


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

        vc = _compare_tube(
            ref_time,
            ref_values,
            ref_time,
            act_values,
            {"tube_width_mode": "band", "tube_abs": 5.0},
        )
        assert vc.passed
        assert vc.mode == "tube"
        assert vc.tube_points_inside == 1.0

    def test_constant_tube_fails(self):
        """Signal outside constant tube fails."""
        ref_time = np.array([0.0, 0.5, 1.0])
        ref_values = np.array([100.0, 200.0, 300.0])
        act_values = np.array([100.0, 200.0, 310.0])  # Last point outside ±5

        vc = _compare_tube(
            ref_time,
            ref_values,
            ref_time,
            act_values,
            {"tube_width_mode": "band", "tube_abs": 5.0},
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

        vc = _compare_tube(
            ref_time,
            ref_values,
            ref_time,
            act_values,
            {"tube_width_mode": "rel", "tube_rel": 0.05},
        )
        assert vc.passed

    def test_min_width_floors_rel_at_zero_crossing(self):
        """tube_min_width floors rel-mode width — prevents zero-width at zero crossing."""
        ref_time = np.array([0.0, 1.0])
        ref_values = np.array([0.0, 100.0])  # Crosses zero
        # At t=0: |ref|=0 so rel width = 0, but min_width=5 → effective width=5
        act_values = np.array([3.0, 104.0])  # Within ±5 at zero, ±5% at t=1

        vc = _compare_tube(
            ref_time,
            ref_values,
            ref_time,
            act_values,
            {"tube_width_mode": "rel", "tube_rel": 0.05, "tube_min_width": 5.0},
        )
        assert vc.passed

    def test_time_varying_tube(self):
        """Time-varying tube with control points."""
        ref_time = np.array([0.0, 50.0, 100.0])
        ref_values = np.array([10.0, 10.0, 10.0])
        # Tight at start (abs=1), loose at end (abs=10)
        act_values = np.array([10.5, 10.5, 18.0])

        vc = _compare_tube(
            ref_time,
            ref_values,
            ref_time,
            act_values,
            {
                "tube_width_mode": "band",
                "tube_points": [
                    {"time": 0.0, "upper": 1.0, "lower": 1.0},
                    {"time": 100.0, "upper": 10.0, "lower": 10.0},
                ],
                "tube_interpolation": "linear",
            },
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

        vc = _compare_tube(
            ref_time,
            ref_values,
            ref_time,
            act_values,
            {
                "tube_width_mode": "band",
                "tube_points": [
                    {"time": 0.0, "upper": 1.0, "lower": 1.0},
                    {"time": 100.0, "upper": 10.0, "lower": 10.0},
                ],
            },
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

        vc = _compare_tube(
            ref_time,
            ref_values,
            ref_time,
            act_values,
            {"tube_width_mode": "band", "tube_abs": 5.0},
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
        comp = compare_test(test, result, ref, default_tolerance=1e-4)
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
        vc = _compare_tube(
            ref_time,
            ref_values,
            ref_time,
            act_values,
            {
                "tube_width_mode": "band",
                "tube_points": [
                    {"time": 0.0, "upper": 5.0, "lower": 1.0},
                ],
            },
        )
        assert vc.passed

    def test_asymmetric_tube_fails_below(self):
        """Asymmetric tube catches signal below narrow lower bound."""
        ref_time = np.array([0.0, 1.0])
        ref_values = np.array([100.0, 100.0])
        # Signal is 3 below reference
        act_values = np.array([97.0, 97.0])

        # Upper allows 5, lower allows 1 → fails (3 > 1 below)
        vc = _compare_tube(
            ref_time,
            ref_values,
            ref_time,
            act_values,
            {
                "tube_width_mode": "band",
                "tube_points": [
                    {"time": 0.0, "upper": 5.0, "lower": 1.0},
                ],
            },
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

        vc = _compare_tube(
            ref_time,
            ref_values,
            ref_time,
            act_values,
            {
                "tube_width_mode": "rel",
                "tube_rel": 0.02,
            },
        )
        assert vc.passed

    def test_min_width_floor(self):
        """tube_min_width prevents tube from collapsing at zero crossing."""
        ref_time = np.array([0.0, 1.0])
        ref_values = np.array([0.0, 100.0])  # Crosses zero
        act_values = np.array([0.5, 101.0])

        # Rel mode: at t=0, ref=0 → rel width = 0. Without floor, fails
        vc_no_floor = _compare_tube(
            ref_time,
            ref_values,
            ref_time,
            act_values,
            {"tube_width_mode": "rel", "tube_rel": 0.02},
        )
        assert not vc_no_floor.passed

        # With min_width floor of 1.0
        vc_floor = _compare_tube(
            ref_time,
            ref_values,
            ref_time,
            act_values,
            {"tube_width_mode": "rel", "tube_rel": 0.02, "tube_min_width": 1.0},
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
        assert mode.config.tube_abs == 5.0
        assert mode.config.tube_rel == 0.02
        assert mode.config.tube_width_mode == "band"

    def test_explicit_default_points(self):
        """mode='default_points' → PointsMode."""
        mode = resolve_mode({"mode": "points"}, tolerance=0.05)
        assert isinstance(mode, PointsMode)
        assert mode.name == "points"
        assert mode.config.tolerance == 0.05

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
        assert mode.config.tube_abs == 3.0

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
        assert d["tube_abs"] == 5.0
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
            test, result, ref, default_tolerance=1e-4, default_points=True
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
            test, result, ref, default_tolerance=0.01, default_points=True
        )
        assert comp.passed


# ---------------------------------------------------------------------------
# Baseline-free short-circuit (idea #59 / D83)
# ---------------------------------------------------------------------------


class _NoBaselineStore:
    """Minimal ReferenceStore stand-in: no references on disk."""

    def get_reference(self, _model_id):
        return None

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
        from dstf.comparison.comparator import _compare_points

        ref_t, ref_v, act_t, act_v = self._make_traj()
        result = _compare_points(
            ref_t, ref_v, act_t, act_v, points=None, tolerance=0.01
        )
        # act[-1] = 5.001, ref[-1] = 5.0, delta = 0.001 < 0.01 → PASS.
        assert result.passed
        assert result.diagnostics["delta"] == pytest.approx(0.001)

    def test_single_ref_relative_point_passes(self):
        from dstf.comparison.comparator import _compare_points

        ref_t, ref_v, act_t, act_v = self._make_traj()
        # Point at t=3: target = ref(3) = 3.0; act(3) = 3.001; delta = 0.001.
        # Tolerance 0.01 → PASS.
        result = _compare_points(
            ref_t, ref_v, act_t, act_v, points=[{"time": 3.0}], tolerance=0.01
        )
        assert result.passed
        assert result.diagnostics["scored_points"] == 1
        assert result.diagnostics["worst_delta"] == pytest.approx(0.001)

    def test_single_ref_relative_point_fails(self):
        """Point at t=2 hits the 0.05 act offset → exceeds 0.01 tolerance."""
        from dstf.comparison.comparator import _compare_points

        ref_t, ref_v, act_t, act_v = self._make_traj()
        result = _compare_points(
            ref_t, ref_v, act_t, act_v, points=[{"time": 2.0}], tolerance=0.01
        )
        assert not result.passed
        assert result.diagnostics["worst_delta"] == pytest.approx(0.05, abs=1e-9)

    def test_explicit_value_point_baseline_free(self):
        """Point with explicit ``value`` is baseline-free — passes any
        ref. Use a synthetic empty ref array to prove the scorer never
        reads it for absolute-value points.
        """
        import numpy as np

        from dstf.comparison.comparator import _compare_points

        # Empty ref arrays: an absolute-value point should still score.
        ref_t = np.array([])
        ref_v = np.array([])
        act_t = np.array([0.0, 1.0, 2.0, 3.0])
        act_v = np.array([0.0, 1.0, 2.0, 3.0])
        result = _compare_points(
            ref_t,
            ref_v,
            act_t,
            act_v,
            points=[{"time": 2.0, "value": 2.0, "tolerance": 0.01}],
            tolerance=1.0,
        )
        assert result.passed
        assert result.diagnostics["worst_delta"] == pytest.approx(0.0, abs=1e-9)

    def test_per_point_tolerance_overrides_global(self):
        """A wider per-point tolerance lets a point pass that the
        global tolerance would fail."""
        from dstf.comparison.comparator import _compare_points

        ref_t, ref_v, act_t, act_v = self._make_traj()
        # Point at t=2: delta=0.05. Global tolerance=0.01 (would fail);
        # per-point tolerance=0.1 (passes).
        result = _compare_points(
            ref_t,
            ref_v,
            act_t,
            act_v,
            points=[{"time": 2.0, "tolerance": 0.1}],
            tolerance=0.01,
        )
        assert result.passed

    def test_relative_tolerance_mode(self):
        """tolerance_mode='rel' scales tol by |target|."""
        from dstf.comparison.comparator import _compare_points

        ref_t, ref_v, act_t, act_v = self._make_traj()
        # Point at t=4: target=ref(4)=4.0; act(4)=4.001; delta=0.001.
        # rel-tolerance 0.001 → limit = 0.001 * 4 = 0.004 → PASS.
        # rel-tolerance 0.0001 → limit = 0.0001 * 4 = 0.0004 → FAIL.
        passing = _compare_points(
            ref_t,
            ref_v,
            act_t,
            act_v,
            points=[{"time": 4.0, "tolerance": 0.001, "tolerance_mode": "rel"}],
            tolerance=0.01,
        )
        failing = _compare_points(
            ref_t,
            ref_v,
            act_t,
            act_v,
            points=[{"time": 4.0, "tolerance": 0.0001, "tolerance_mode": "rel"}],
            tolerance=0.01,
        )
        assert passing.passed
        assert not failing.passed

    def test_null_time_resolves_to_trace_end(self):
        """time: null sentinel = the trace's final time (act_time[-1]
        when ref_time is empty, else ref_time[-1])."""
        from dstf.comparison.comparator import _compare_points

        ref_t, ref_v, act_t, act_v = self._make_traj()
        # Point at time=null: target = ref(5) = 5.0; act(5) = 5.001; delta=0.001.
        result = _compare_points(
            ref_t,
            ref_v,
            act_t,
            act_v,
            points=[{"time": None, "tolerance": 0.01}],
            tolerance=0.01,
        )
        assert result.passed
        assert result.diagnostics["worst_delta"] == pytest.approx(0.001)

    def test_multiple_points_all_must_match(self):
        """A leaf with 3 points fails if any one fails."""
        from dstf.comparison.comparator import _compare_points

        ref_t, ref_v, act_t, act_v = self._make_traj()
        # t=1 (delta=0.001 ✓), t=2 (delta=0.05 ✗), t=3 (delta=0.001 ✓).
        # Tolerance 0.01 → overall FAIL.
        result = _compare_points(
            ref_t,
            ref_v,
            act_t,
            act_v,
            points=[{"time": 1.0}, {"time": 2.0}, {"time": 3.0}],
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

        from dstf.comparison.comparator import _compare_points

        # ref doesn't matter — point has explicit value.
        ref_t = np.array([0.0, 5.0])
        ref_v = np.array([0.0, 0.0])
        # act curve crosses 30 around t=2.95.
        act_t = np.array([0.0, 2.5, 2.9, 2.95, 3.0, 3.5, 5.0])
        act_v = np.array([0.0, 20.0, 28.0, 30.0, 32.0, 35.0, 40.0])
        result = _compare_points(
            ref_t,
            ref_v,
            act_t,
            act_v,
            points=[
                {"time": 3.0, "value": 30.0, "tolerance": 0.5, "time_tolerance": 0.2}
            ],
            tolerance=0.01,
        )
        # At t=3.0, act is 32 (delta=2 > 0.5). Strict-time would FAIL.
        # Box from [2.8, 3.2] × [29.5, 30.5]: act hits 30.0 at t=2.95 → PASS.
        assert result.passed
        assert result.diagnostics["worst_delta"] < 0.5

    def test_time_tolerance_fails_when_act_misses_box(self):
        """If the act curve never enters the box, point fails."""
        import numpy as np

        from dstf.comparison.comparator import _compare_points

        ref_t = np.array([0.0, 5.0])
        ref_v = np.array([0.0, 0.0])
        # act stays well above 30 across the whole [2.8, 3.2] window.
        act_t = np.array([0.0, 2.8, 3.0, 3.2, 5.0])
        act_v = np.array([0.0, 35.0, 36.0, 37.0, 40.0])
        result = _compare_points(
            ref_t,
            ref_v,
            act_t,
            act_v,
            points=[
                {"time": 3.0, "value": 30.0, "tolerance": 0.5, "time_tolerance": 0.2}
            ],
            tolerance=0.01,
        )
        assert not result.passed
        # Worst delta within the box is at t=2.8 where act=35, target=30 → delta=5.
        assert result.diagnostics["worst_delta"] >= 5.0

    def test_time_tolerance_zero_degenerates_to_strict_time(self):
        """time_tolerance=0 must behave identically to a single-point check."""
        import numpy as np

        from dstf.comparison.comparator import _compare_points

        ref_t = np.array([0.0, 5.0])
        ref_v = np.array([0.0, 0.0])
        act_t = np.array([0.0, 2.5, 3.0, 5.0])
        act_v = np.array([0.0, 28.0, 32.0, 40.0])
        # act(3.0) = 32, target = 30, delta = 2 > 0.5 → FAIL with strict time.
        with_xtol = _compare_points(
            ref_t,
            ref_v,
            act_t,
            act_v,
            points=[
                {"time": 3.0, "value": 30.0, "tolerance": 0.5, "time_tolerance": 0.0}
            ],
            tolerance=0.01,
        )
        without_xtol = _compare_points(
            ref_t,
            ref_v,
            act_t,
            act_v,
            points=[{"time": 3.0, "value": 30.0, "tolerance": 0.5}],
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

        from dstf.comparison.comparator import _compare_points

        ref_t = np.array([0.0, 5.0])
        ref_v = np.array([0.0, 0.0])
        # Sparse act samples that bracket the target box.
        # Box: [2.5, 3.5] × [29.0, 31.0]. Samples at 2.0 and 4.0 with
        # values 20 and 40 → linear interp puts act(2.5) = 25, act(3.5) = 35.
        # Neither crosses 30 inside the box at a sample, but the
        # interpolated curve passes through (3.0, 30) which IS inside.
        act_t = np.array([0.0, 2.0, 4.0, 5.0])
        act_v = np.array([0.0, 20.0, 40.0, 50.0])
        result = _compare_points(
            ref_t,
            ref_v,
            act_t,
            act_v,
            points=[
                {"time": 3.0, "value": 30.0, "tolerance": 1.0, "time_tolerance": 0.5}
            ],
            tolerance=0.01,
        )
        # Linear interp at t=3 gives act=30, delta=0 → PASS.
        assert result.passed


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
            source_file=Path(""),
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
                return {
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

    def test_range_leaf_with_min_max_shorthand(self):
        """Deleted `min`/`max` shorthand → leaf failure naming the real keys."""
        bad, good = self._two_test_run(
            {"metric": "range", "variable": "x", "min": 0, "max": 2}
        )
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

    def test_tube_leaf_with_symmetric_control_points(self):
        """Raised from mode.compare, not resolve_mode — the second catch."""
        bad, good = self._two_test_run(
            {
                "metric": "tube",
                "variable": "x",
                "tube_width_mode": "band",
                "tube_points": [{"time": 0.0, "abs": 1.0, "rel": 0.0}],
            }
        )
        assert not bad.passed
        assert good.passed
        assert "'upper' and/or 'lower'" in self._error_text(bad)


# =========================================================================
# Folded in from tests/test_review_fixes_verdict.py — regression tests for the
# 2026-07-06 review, Theme 1: verdict integrity (CODE_REVIEW_2026-07-06.md).
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


from dstf.comparison.algorithms import _compare_range
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
        vc = _compare_trajectories(
            EMPTY, EMPTY, np.array([0.0, 1.0]), np.array([5.0, 5.0]), 1e-4
        )
        assert not vc.passed
        assert "error" in vc.diagnostics

    def test_nrmse_empty_actual_fails_without_crashing(self):
        vc = _compare_trajectories(
            np.array([0.0, 1.0]), np.array([1.0, 1.0]), EMPTY, EMPTY, 1e-4
        )
        assert not vc.passed

    def test_tube_empty_reference_fails_without_crashing(self):
        cfg = {"tube_width_mode": "band", "tube_abs": 0.5}
        vc = _compare_tube(
            EMPTY, EMPTY, np.array([0.0, 1.0]), np.array([0.0, 0.0]), cfg
        )
        assert not vc.passed

    def test_tube_empty_actual_fails_without_crashing(self):
        cfg = {"tube_width_mode": "band", "tube_abs": 0.5}
        vc = _compare_tube(
            np.array([0.0, 1.0]), np.array([1.0, 1.0]), EMPTY, EMPTY, cfg
        )
        assert not vc.passed

    def test_range_empty_actual_fails(self):
        vc = _compare_range(EMPTY, EMPTY, 0.0, 1.0)
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
            "variables": [
                {
                    "index": 1,
                    "name": "x",
                    "time": [0.0, 5.0, 10.0],
                    "values": [1.0, 1.0, 1.0],
                }
            ]
        }
        comp = compare_test(test, result, reference)
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
        vc = _compare_points(ref_t, ref_v, act_t, act_v, points=points)
        assert not vc.passed
        assert vc.diagnostics.get("failed_points", 0) >= 1

    def test_empty_actual_with_declared_points_fails(self):
        ref_t = np.linspace(0.0, 100.0, 101)
        vc = _compare_points(ref_t, ref_t, EMPTY, EMPTY, points=[{"time": 20.0}])
        assert not vc.passed

    def test_unclipped_points_still_pass(self):
        ref_t = np.linspace(0.0, 100.0, 101)
        act_t = np.linspace(0.0, 100.0, 201)
        vc = _compare_points(
            ref_t, ref_t, act_t, act_t, points=[{"time": 20.0, "tolerance": 0.5}]
        )
        assert vc.passed

    def test_ref_relative_point_beyond_reference_end_fails(self):
        """np.interp would clamp to ref_final — that target is untrustworthy."""
        ref_t = np.linspace(0.0, 40.0, 41)
        ref_v = ref_t.copy()  # still rising at truncation
        act_t = np.linspace(0.0, 100.0, 101)
        act_v = act_t.copy()
        vc = _compare_points(
            ref_t, ref_v, act_t, act_v, points=[{"time": 90.0, "tolerance": 0.5}]
        )
        assert not vc.passed


# ---------------------------------------------------------------------------
# Tolerance resolution: explicit zero honored
# ---------------------------------------------------------------------------


class TestExplicitZeroTolerance:
    def test_zero_test_tolerance_fails_nonzero_error(self):
        test = _mk_test(comparison_tolerance=0.0)
        result = _mk_result(values=np.ones(11) + 1e-6)
        reference = {
            "variables": [
                {
                    "index": 1,
                    "name": "x",
                    "time": list(np.linspace(0.0, 10.0, 11)),
                    "values": [1.0] * 11,
                }
            ]
        }
        comp = compare_test(test, result, reference)
        assert not comp.passed
        assert comp.variables[0].tolerance_used == 0.0

    def test_zero_reference_tolerance_honored(self):
        test = _mk_test()
        result = _mk_result(values=np.ones(11) + 1e-6)
        reference = {
            "comparison": {"tolerance": 0.0},
            "variables": [
                {
                    "index": 1,
                    "name": "x",
                    "time": list(np.linspace(0.0, 10.0, 11)),
                    "values": [1.0] * 11,
                }
            ],
        }
        comp = compare_test(test, result, reference)
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
        assert _check_structural_changes({}, result) == []


# ---------------------------------------------------------------------------
# Name-based reference pairing
# ---------------------------------------------------------------------------


class TestNameBasedPairing:
    def test_reference_pairs_by_name_after_spec_reorder(self):
        """Baseline stored with [a, b]; 'a' later removed from the spec.
        'b' (now index 1) must score against ref 'b', not ref 'a'."""
        reference = {
            "variables": [
                {"index": 1, "name": "a", "time": [0.0, 1.0], "values": [0.0, 0.0]},
                {"index": 2, "name": "b", "time": [0.0, 1.0], "values": [10.0, 10.0]},
            ]
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
        comp = compare_test(_mk_test(), result, reference)
        assert comp.passed


# ---------------------------------------------------------------------------
# Unknown comparison mode: loud, not silent NRMSE
# ---------------------------------------------------------------------------


class TestUnknownMode:
    def test_resolve_mode_raises_on_unknown_mode(self):
        with pytest.raises(ValueError, match="[Uu]nknown.*mode"):
            resolve_mode({"mode": "Tube"}, 0.01)

    def test_typo_mode_fails_variable_not_silently_nrmse(self):
        test = _mk_test(variable_overrides={"x": {"mode": "Tube", "tube_abs": 5.0}})
        result = _mk_result()
        reference = {
            "variables": [
                {
                    "index": 1,
                    "name": "x",
                    "time": list(np.linspace(0.0, 10.0, 11)),
                    "values": [1.0] * 11,
                }
            ]
        }
        comp = compare_test(test, result, reference)
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

    def test_no_result_is_sim_fail(self):
        from dstf.comparison.comparator import compare_one

        comp = compare_one(_mk_test(), None, _NoRefStore())
        assert comp.sim_success is False
        assert comp.error_message == "No simulation results found"

    def test_failed_result_is_sim_fail_before_the_baseline_check(self):
        from dstf.comparison.comparator import compare_one

        result = TestResult(model_id="MyLib.Test", success=False, error_message="boom")
        comp = compare_one(_mk_test(), result, _NoRefStore())
        assert comp.sim_success is False and comp.error_message == "boom"

    def test_no_baseline_is_no_ref(self):
        from dstf.comparison.comparator import compare_one

        comp = compare_one(_mk_test(), _mk_result(), _NoRefStore())
        assert comp.has_reference is False and comp.passed is True
        assert not comp.evaluated

    def test_compare_all_is_compare_one_per_test(self):
        from dstf.comparison.comparator import compare_all, compare_one

        scored_result, ref = _make_result_and_ref(offset=0.5)  # a real FAIL

        class _Store:
            def get_reference(self, model_id):
                return ref if model_id == "Test.Model" else None

            def get_soft_checks(self, model_id):
                return {}

        tests = [
            _make_test(),
            _mk_test(model_id="MyLib.Test"),
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
        comp = compare_test(_vi_test(), _vi_result(), _vi_reference())
        assert comp.passed
        (vc,) = comp.variables
        assert (vc.name, vc.expression) == (VI_NAME, VI_EXPR)
        assert vc.nrmse == 0.0

    def test_a_tree_leaf_scores_the_name_and_carries_the_expression(self):
        spec = parse_metric_tree({"metric": "nrmse", "variable": VI_NAME})
        comp = compare_test(
            _vi_test(metric_tree_spec=spec), _vi_result(), _vi_reference()
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
            _vi_reference(),
        )
        assert by_name.passed and by_name.variables[0].tolerance_used == 0.5
        by_expression = compare_test(
            _vi_test(variable_overrides={VI_EXPR: loose}),
            _vi_result(off),
            _vi_reference(),
        )
        assert not by_expression.passed
        assert by_expression.variables[0].tolerance_used != 0.5

    def test_an_override_keyed_by_a_plain_expression_does_not_apply(self):
        """The shape a real spec has today: a key that WAS the stored name
        (`pipe.p`) and is now the expression beside `unitTests.x[1]`. It stops
        applying — which is what `dstf migrate-variable-names` rewrites."""
        comp = compare_test(
            _vi_test(
                x_expressions=["pipe.p"],
                variable_overrides={"pipe.p": {"tolerance": 0.5}},
            ),
            _vi_result(_VI_T * 1.05, expression="pipe.p"),
            _vi_reference(expression="pipe.p"),
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
            _vi_reference(expression="a.Q"),
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
            _vi_test(metric_tree_spec=spec), _vi_result(expression=measured), reference
        )
        assert comp.passed and comp.warnings == []

    def test_a_baseline_stored_under_the_expression_is_missing_and_names_the_command(
        self, caplog
    ):
        with caplog.at_level(logging.WARNING):
            comp = compare_test(
                _vi_test(x_expressions=["pipe.T[1]"]),
                _vi_result(expression="pipe.T[1]"),
                _vi_reference(name="pipe.T[1]", with_expression=False),
            )
        assert not comp.passed
        assert any(
            "dstf migrate-variable-names" in record.getMessage()
            for record in caplog.records
        )

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
