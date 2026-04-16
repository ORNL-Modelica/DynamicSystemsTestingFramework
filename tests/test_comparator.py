"""Tests for comparison/comparator.py — NRMSE, events, structural warnings."""

import numpy as np
import pytest

from modelica_testing.comparison.comparator import (
    VariableComparison,
    StructuralWarning,
    _find_event_boundaries,
    _split_segments,
    _dedup_time_series,
    _compare_trajectories,
    _compare_final_values,
    _check_structural_changes,
)
from modelica_testing.simulators.base import TestResult


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
        t = np.array([0.0, 1.0, 2.0, 2.0, 2.0, 3.0, 5.0, 5.0, 5.0, 6.0, 8.0, 8.0, 8.0, 9.0, 10.0])
        v = np.array([0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0, 2.0, 2.0, 2.0, 2.0, 3.0, 3.0, 3.0])
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
        ref_v = np.array([37e9, 37e9])               # float64
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
    def test_identical(self):
        vc = _compare_final_values(1.0, 1.0, 1e-4)
        assert vc.passed is True
        assert vc.nrmse == 0.0

    def test_different(self):
        vc = _compare_final_values(1.0, 1.1, 1e-4)
        assert vc.passed is False
        assert vc.nrmse == pytest.approx(0.1)

    def test_zero_reference(self):
        """Near-zero reference uses absolute error."""
        vc = _compare_final_values(0.0, 0.0001, 1e-4)
        assert vc.passed is False

    def test_both_zero(self):
        vc = _compare_final_values(0.0, 0.0, 1e-4)
        assert vc.passed is True
        assert vc.nrmse == 0.0


# ---------------------------------------------------------------------------
# Structural change detection
# ---------------------------------------------------------------------------

class TestStructuralChanges:
    def test_no_changes(self):
        ref = {"statistics": {
            "translation": {"continuous_time_states": 4, "nonlinear": "3, 1"},
            "EventCounter": 42,
        }}
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
            model_id="Test", success=True,
            statistics={"translation": {"continuous_time_states": 6}},
        )
        warnings = _check_structural_changes(ref, result)
        assert len(warnings) == 1
        assert "Continuous" in warnings[0].field

    def test_nonlinear_change(self):
        ref = {"statistics": {"translation": {"nonlinear_count": 3}}}
        result = TestResult(
            model_id="Test", success=True,
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
            model_id="Test", success=True,
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

from modelica_testing.comparison.comparator import (
    compare_test,
    _compare_tube,
    _interpolate_tube_widths,
)
from modelica_testing.discovery.test_registry import TestModel
from modelica_testing.simulators.base import VariableResult
from pathlib import Path


def _make_test(comparison_tolerance=None, variable_overrides=None):
    """Create a minimal TestModel for tolerance tests."""
    return TestModel(
        model_id="Test.Model",
        source_file=Path(""),
        source_package="Test",
        short_name="Model",
        n_vars=1,
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
        """Single control point applies everywhere (legacy abs format)."""
        times = np.array([0.0, 50.0, 100.0])
        points = [{"time": 0.0, "abs": 10.0, "rel": 0.05}]
        upper, lower = _interpolate_tube_widths(times, points)
        # Legacy format: abs field used as symmetric width
        np.testing.assert_array_equal(upper, [10.0, 10.0, 10.0])
        np.testing.assert_array_equal(lower, [10.0, 10.0, 10.0])

    def test_linear_interpolation(self):
        """Linear interpolation between two points (legacy format)."""
        times = np.array([0.0, 50.0, 100.0])
        points = [
            {"time": 0.0, "abs": 10.0},
            {"time": 100.0, "abs": 20.0},
        ]
        upper, lower = _interpolate_tube_widths(times, points, "linear")
        np.testing.assert_allclose(upper, [10.0, 15.0, 20.0])
        np.testing.assert_allclose(lower, [10.0, 15.0, 20.0])

    def test_hold_at_boundaries(self):
        """Values before first and after last point are held."""
        times = np.array([0.0, 50.0, 100.0, 200.0])
        points = [
            {"time": 50.0, "abs": 10.0},
            {"time": 100.0, "abs": 20.0},
        ]
        upper, _ = _interpolate_tube_widths(times, points, "linear")
        assert upper[0] == 10.0  # Before first point: hold
        assert upper[3] == 20.0  # After last point: hold

    def test_constant_interpolation_stepwise(self):
        """Constant mode uses stepwise (hold previous)."""
        times = np.array([0.0, 25.0, 75.0, 100.0])
        points = [
            {"time": 0.0, "abs": 10.0},
            {"time": 50.0, "abs": 20.0},
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
            ref_time, ref_values, ref_time, act_values,
            {"tube_abs": 5.0, "tube_rel": 0.0},
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
            ref_time, ref_values, ref_time, act_values,
            {"tube_abs": 5.0, "tube_rel": 0.0},
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
            ref_time, ref_values, ref_time, act_values,
            {"tube_abs": 0.0, "tube_rel": 0.05},
        )
        assert vc.passed

    def test_max_of_abs_and_rel(self):
        """Tube width is max(abs, rel * |ref|) — prevents zero-width at zero crossing."""
        ref_time = np.array([0.0, 1.0])
        ref_values = np.array([0.0, 100.0])  # Crosses zero
        # At t=0: |ref|=0, rel=0.05 → rel_width=0, abs=5 → width=5
        act_values = np.array([3.0, 104.0])  # Within abs tube at zero

        vc = _compare_tube(
            ref_time, ref_values, ref_time, act_values,
            {"tube_abs": 5.0, "tube_rel": 0.05},
        )
        assert vc.passed

    def test_time_varying_tube(self):
        """Time-varying tube with control points."""
        ref_time = np.array([0.0, 50.0, 100.0])
        ref_values = np.array([10.0, 10.0, 10.0])
        # Tight at start (abs=1), loose at end (abs=10)
        act_values = np.array([10.5, 10.5, 18.0])

        vc = _compare_tube(
            ref_time, ref_values, ref_time, act_values,
            {
                "tube_points": [
                    {"time": 0.0, "abs": 1.0, "rel": 0.0},
                    {"time": 100.0, "abs": 10.0, "rel": 0.0},
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
            ref_time, ref_values, ref_time, act_values,
            {
                "tube_points": [
                    {"time": 0.0, "abs": 1.0, "rel": 0.0},
                    {"time": 100.0, "abs": 10.0, "rel": 0.0},
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
            ref_time, ref_values, ref_time, act_values,
            {"tube_abs": 5.0, "tube_rel": 0.0},
        )
        assert vc.nrmse > 0
        assert vc.rmse > 0

    def test_tube_via_compare_test(self):
        """Tube mode dispatched correctly via compare_test."""
        test = _make_test(variable_overrides={
            "x": {
                "mode": "tube",
                "tube_abs": 0.01,
                "tube_rel": 0.0,
            },
        })
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
            ref_time, ref_values, ref_time, act_values,
            {
                "tube_width_mode": "abs",
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
            ref_time, ref_values, ref_time, act_values,
            {
                "tube_width_mode": "abs",
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
            ref_time, ref_values, ref_time, act_values,
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
            ref_time, ref_values, ref_time, act_values,
            {"tube_width_mode": "rel", "tube_rel": 0.02},
        )
        assert not vc_no_floor.passed

        # With min_width floor of 1.0
        vc_floor = _compare_tube(
            ref_time, ref_values, ref_time, act_values,
            {"tube_width_mode": "rel", "tube_rel": 0.02, "tube_min_width": 1.0},
        )
        assert vc_floor.passed
        assert vc_floor.tube_points_inside == 1.0


# ---------------------------------------------------------------------------
# Comparison mode strategies (modes.py)
# ---------------------------------------------------------------------------

from modelica_testing.comparison.modes import (
    resolve_mode,
    NrmseMode,
    TubeMode,
    FinalOnlyMode,
    NrmseConfig,
    TubeConfig,
    FinalOnlyConfig,
    ComparisonMode,
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

    def test_explicit_final_only(self):
        """mode='final_only' → FinalOnlyMode."""
        mode = resolve_mode({"mode": "final_only"}, tolerance=0.05)
        assert isinstance(mode, FinalOnlyMode)
        assert mode.name == "final_only"
        assert mode.config.tolerance == 0.05

    def test_default_final_only_flag(self):
        """default_final_only=True with no explicit mode → FinalOnlyMode."""
        mode = resolve_mode({}, tolerance=1e-4, default_final_only=True)
        assert isinstance(mode, FinalOnlyMode)

    def test_tube_not_overridden_by_final_only(self):
        """Bug fix: explicit tube mode must NOT be overridden by final_only flag."""
        mode = resolve_mode(
            {"mode": "tube", "tube_abs": 1.0},
            tolerance=1e-4,
            default_final_only=True,
        )
        assert isinstance(mode, TubeMode)
        assert mode.name == "tube"

    def test_explicit_nrmse_not_overridden_by_final_only(self):
        """Explicit mode='nrmse' is respected even when final_only is True."""
        mode = resolve_mode(
            {"mode": "nrmse"},
            tolerance=1e-4,
            default_final_only=True,
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
        points = [{"time": 0.0, "abs": 1.0}]
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
        vc = mode.compare(self.ref_time, self.ref_values,
                          self.ref_time, self.act_values_close)
        assert vc.passed
        assert vc.mode == "nrmse"

    def test_nrmse_mode_fail(self):
        mode = NrmseMode(NrmseConfig(tolerance=1e-4))
        vc = mode.compare(self.ref_time, self.ref_values,
                          self.ref_time, self.act_values_far)
        assert not vc.passed

    def test_tube_mode_pass(self):
        mode = TubeMode(TubeConfig(tube_abs=1.0))
        vc = mode.compare(self.ref_time, self.ref_values,
                          self.ref_time, self.act_values_close)
        assert vc.passed
        assert vc.mode == "tube"
        assert vc.tube_points_inside == 1.0

    def test_tube_mode_fail(self):
        mode = TubeMode(TubeConfig(tube_abs=0.001))
        vc = mode.compare(self.ref_time, self.ref_values,
                          self.ref_time, self.act_values_far)
        assert not vc.passed
        assert vc.tube_points_inside < 1.0

    def test_final_only_mode_pass(self):
        mode = FinalOnlyMode(FinalOnlyConfig(tolerance=0.01))
        vc = mode.compare(self.ref_time, self.ref_values,
                          self.ref_time, self.act_values_close)
        assert vc.passed
        assert vc.mode == "nrmse"  # _compare_final_values doesn't set mode

    def test_final_only_mode_fail(self):
        mode = FinalOnlyMode(FinalOnlyConfig(tolerance=1e-6))
        vc = mode.compare(self.ref_time, self.ref_values,
                          self.ref_time, self.act_values_far)
        assert not vc.passed


class TestCompareTestWithModes:
    """End-to-end: compare_test dispatches via resolve_mode correctly."""

    def test_final_only_flag_does_not_override_tube(self):
        """Bug fix: final_only=True must not override mode='tube'."""
        test = _make_test(variable_overrides={
            "x": {"mode": "tube", "tube_abs": 0.01},
        })
        result, ref = _make_result_and_ref(offset=0.005)
        comp = compare_test(test, result, ref, default_tolerance=1e-4, final_only=True)
        assert comp.passed
        assert comp.variables[0].mode == "tube"

    def test_final_only_flag_applies_when_no_explicit_mode(self):
        """final_only=True applies to variables without explicit mode."""
        test = _make_test()
        result, ref = _make_result_and_ref(offset=0.005)
        # final_only compares only last values: ref=3.0, act=3.005
        # relative error = 0.005/3.0 ≈ 0.00167, tolerance=0.01 → pass
        comp = compare_test(test, result, ref, default_tolerance=0.01, final_only=True)
        assert comp.passed
