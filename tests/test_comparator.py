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
        """Near-constant signal with tiny deviation => uses raw RMSE."""
        t = np.array([0.0, 1.0, 2.0])
        ref_v = np.array([5.0, 5.0, 5.0])
        act_v = np.array([5.0, 5.0, 5.001])
        vc = _compare_trajectories(t, ref_v, t, act_v, 1e-4)
        assert vc.is_constant == True
        # RMSE of [0, 0, 0.001] = sqrt(0.001^2 / 3) ≈ 5.77e-4
        assert vc.nrmse > 1e-4  # Should fail

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
        ref = {"statistics": {"translation": {"nonlinear": "3"}}}
        result = TestResult(
            model_id="Test", success=True,
            statistics={"translation": {"nonlinear": "5"}},
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
