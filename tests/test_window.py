"""Tests for idea #46 — time-windowed leaves.

Window is a uniform leaf-level property scoping every metric to
``[window_start, window_end]``. Slicing happens in `tree_eval` before
``mode.compare`` so mode configs stay untouched.
"""
from __future__ import annotations

import numpy as np
import pytest

from modelica_testing.comparison.tree_eval import BaselineView, evaluate_spec
from modelica_testing.comparison.tree_spec import MetricSpecError, parse_metric_tree
from modelica_testing.simulators import VariableResult


def _var(time, values, index=1, name="x"):
    return VariableResult(
        index=index, time=np.asarray(time, dtype=float),
        values=np.asarray(values, dtype=float), name=name,
    )


def _baseline(time, values, name="primary"):
    return BaselineView(
        name=name,
        ref_vars_by_name={
            "x": {"index": 1, "name": "x", "values": list(values)},
        },
        shared_ref_time=np.asarray(time, dtype=float),
    )


class TestWindowParsing:
    def test_no_window_default_none(self):
        tree = parse_metric_tree({
            "metric": "nrmse", "variable": "x", "tolerance": 1e-3,
        })
        assert tree.window_start is None
        assert tree.window_end is None

    def test_window_both_ends(self):
        tree = parse_metric_tree({
            "metric": "nrmse", "variable": "x", "tolerance": 1e-3,
            "window": {"start": 1.0, "end": 5.0},
        })
        assert tree.window_start == 1.0
        assert tree.window_end == 5.0

    def test_window_open_start(self):
        tree = parse_metric_tree({
            "metric": "nrmse", "variable": "x",
            "window": {"end": 5.0},
        })
        assert tree.window_start is None
        assert tree.window_end == 5.0

    def test_window_open_end(self):
        tree = parse_metric_tree({
            "metric": "nrmse", "variable": "x",
            "window": {"start": 3.0},
        })
        assert tree.window_start == 3.0
        assert tree.window_end is None

    def test_window_end_before_start_rejected(self):
        with pytest.raises(MetricSpecError, match="must be > start"):
            parse_metric_tree({
                "metric": "nrmse", "variable": "x",
                "window": {"start": 5.0, "end": 3.0},
            })

    def test_window_not_dict_rejected(self):
        with pytest.raises(MetricSpecError, match="expected an object"):
            parse_metric_tree({
                "metric": "nrmse", "variable": "x",
                "window": "10-20",
            })

    def test_window_not_in_params(self):
        """window is hoisted to its own fields, not left in params."""
        tree = parse_metric_tree({
            "metric": "nrmse", "variable": "x", "tolerance": 1e-3,
            "window": {"start": 1.0, "end": 5.0},
        })
        assert "window" not in tree.params


class TestWindowEvaluation:
    """Window slices both sides before mode.compare — mode doesn't see it."""

    def test_no_window_unchanged_score(self):
        """With no window, NRMSE matches what we get without the field at all."""
        t = np.linspace(0.0, 10.0, 101)
        ref = _baseline(t, np.sin(t))
        act = _var(t, np.sin(t))

        tree = parse_metric_tree({
            "metric": "nrmse", "variable": "x", "tolerance": 1e-6,
        })
        result = evaluate_spec(
            tree, {"x": act}, {"primary": ref}, base_tolerance=1e-6,
        )
        assert result.passed is True
        # score is NRMSE; for identical signals it's essentially zero
        assert result.score < 1e-9

    def test_window_isolates_portion(self):
        """NRMSE scored only over [start, end] — mismatch outside the window
        doesn't move the score."""
        t = np.linspace(0.0, 10.0, 101)
        ref = _baseline(t, np.sin(t))
        # Inject a large error OUTSIDE [2, 5] — window NRMSE should ignore it.
        act_vals = np.sin(t).copy()
        act_vals[t > 7.0] += 5.0  # huge error after t=7
        act = _var(t, act_vals)

        tree = parse_metric_tree({
            "metric": "nrmse", "variable": "x", "tolerance": 1e-6,
            "window": {"start": 2.0, "end": 5.0},
        })
        result = evaluate_spec(
            tree, {"x": act}, {"primary": ref}, base_tolerance=1e-6,
        )
        # The [2, 5] slice of act matches ref exactly — score tiny.
        assert result.passed is True
        assert result.score < 1e-9

    def test_window_catches_in_range_mismatch(self):
        """Mismatch INSIDE the window gets caught (the window isn't just
        masking badness — it's a real slice)."""
        t = np.linspace(0.0, 10.0, 101)
        ref = _baseline(t, np.sin(t))
        act_vals = np.sin(t).copy()
        act_vals[(t >= 3.0) & (t <= 4.0)] += 2.0  # big error inside window
        act = _var(t, act_vals)

        tree = parse_metric_tree({
            "metric": "nrmse", "variable": "x", "tolerance": 1e-3,
            "window": {"start": 2.0, "end": 5.0},
        })
        result = evaluate_spec(
            tree, {"x": act}, {"primary": ref}, base_tolerance=1e-3,
        )
        assert result.passed is False
        assert result.score > 0.1

    def test_window_records_diagnostics(self):
        t = np.linspace(0.0, 10.0, 101)
        ref = _baseline(t, np.sin(t))
        act = _var(t, np.sin(t))

        tree = parse_metric_tree({
            "metric": "nrmse", "variable": "x", "tolerance": 1e-6,
            "window": {"start": 2.0, "end": 5.0},
        })
        result = evaluate_spec(
            tree, {"x": act}, {"primary": ref}, base_tolerance=1e-6,
        )
        assert result.diagnostics.get("window") == {"start": 2.0, "end": 5.0}


class TestPiecewiseRegression:
    """Multiple windowed leaves under AND reproduce a piecewise contract."""

    def test_two_windows_composed_under_and(self):
        t = np.linspace(0.0, 10.0, 101)
        ref = _baseline(t, np.sin(t))
        act = _var(t, np.sin(t))

        tree = parse_metric_tree({
            "combinator": "and",
            "children": [
                {"metric": "nrmse", "variable": "x", "tolerance": 1e-6,
                 "window": {"end": 5.0}},
                {"metric": "nrmse", "variable": "x", "tolerance": 1e-6,
                 "window": {"start": 5.0}},
            ],
        })
        result = evaluate_spec(
            tree, {"x": act}, {"primary": ref}, base_tolerance=1e-6,
        )
        assert result.passed is True

    def test_one_window_fails_and_fails(self):
        """Mismatch in the second half fails the second window but not the
        first — AND surfaces the partial failure."""
        t = np.linspace(0.0, 10.0, 101)
        ref = _baseline(t, np.sin(t))
        act_vals = np.sin(t).copy()
        act_vals[t >= 6.0] += 3.0  # big error in second half
        act = _var(t, act_vals)

        tree = parse_metric_tree({
            "combinator": "and",
            "children": [
                {"metric": "nrmse", "variable": "x", "tolerance": 1e-3,
                 "window": {"end": 5.0}},
                {"metric": "nrmse", "variable": "x", "tolerance": 1e-3,
                 "window": {"start": 5.0}},
            ],
        })
        result = evaluate_spec(
            tree, {"x": act}, {"primary": ref}, base_tolerance=1e-3,
        )
        assert result.passed is False
        # First child passes, second fails
        assert result.children[0].passed is True
        assert result.children[1].passed is False
