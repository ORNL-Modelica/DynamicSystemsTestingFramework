"""Tests for the ``scalar`` leaf metric — one number, not a trajectory.

Covers the mode itself (config validation, the closed set of reductions, abs/rel
and bounds forms), the evaluator's stats-path data-source rule, and the
reporter surfaces (schema export, derived field metadata, and the score
display the page renders, sentinels included).
"""

from __future__ import annotations

import numpy as np
import pytest

from dstf.backends.types import TestResult, VariableResult
from dstf.comparison.algorithms import SCALAR_REDUCTIONS
from dstf.comparison.modes import ScalarConfig, ScalarMode, resolve_mode
from dstf.comparison.registry import summarize
from dstf.comparison.sides import Sides
from dstf.comparison.tree_eval import (
    PRIMARY_BASELINE,
    BaselineView,
    ScalarSources,
    evaluate_spec,
)
from dstf.comparison.tree_spec import parse_metric_tree
from dstf.discovery.test_registry import TestModel
from dstf.storage.baseline import StoredVariable
from dstf.storage.baseline_codec import decode_reference, decode_series
from scoring import score

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _series(values: list[float]) -> tuple[np.ndarray, np.ndarray]:
    time = np.linspace(0.0, float(len(values) - 1), len(values))
    return time, np.array(values, dtype=float)


def _mode(**kwargs) -> ScalarMode:
    kwargs.setdefault("quantity", {"reduce": "final"})
    return ScalarMode(ScalarConfig(**kwargs))


def _stored(name: str, values) -> StoredVariable:
    return StoredVariable(index=1, name=name, expression="", values=list(values))


def _baselines(**kwargs) -> dict[str, BaselineView]:
    kwargs.setdefault("ref_vars_by_name", {})
    kwargs.setdefault("shared_ref_time", np.array([]))
    if "diagnostics" in kwargs:
        # Given as a file stores them, read as the codec reads a file.
        kwargs["diagnostics"] = decode_series(
            {"diagnostics": kwargs["diagnostics"]}, PRIMARY_BASELINE
        ).diagnostics
    return {PRIMARY_BASELINE: BaselineView(name=PRIMARY_BASELINE, **kwargs)}


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


class TestScalarConfigValidation:
    def test_quantity_is_required(self):
        with pytest.raises(ValueError, match="needs a 'quantity'"):
            ScalarMode(ScalarConfig())

    def test_empty_string_quantity_rejected(self):
        with pytest.raises(ValueError, match="needs a 'quantity'"):
            ScalarMode(ScalarConfig(quantity=""))

    def test_both_shapes_at_once_rejected(self):
        with pytest.raises(ValueError, match="exactly one of"):
            ScalarMode(ScalarConfig(quantity={"reduce": "max", "expr": "x + 1"}))

    def test_expr_shape_is_not_implemented_yet(self):
        with pytest.raises(ValueError, match="not implemented"):
            ScalarMode(ScalarConfig(quantity={"expr": "max(x)"}))

    def test_unknown_reduction_rejected(self):
        with pytest.raises(ValueError, match="reduce must be one of"):
            ScalarMode(ScalarConfig(quantity={"reduce": "median"}))

    def test_hyphenated_sample_mean_rejected_with_the_closed_set(self):
        with pytest.raises(ValueError) as excinfo:
            ScalarMode(ScalarConfig(quantity={"reduce": "sample-mean"}))
        message = str(excinfo.value)
        assert "reduce must be one of" in message
        assert str(list(SCALAR_REDUCTIONS)) in message

    def test_non_object_non_string_quantity_rejected(self):
        with pytest.raises(ValueError, match="must be a string path or an object"):
            ScalarMode(ScalarConfig(quantity=[1, 2]))

    def test_bounds_must_be_ordered(self):
        with pytest.raises(ValueError, match="must be below"):
            _mode(min_value=2.0, max_value=1.0)

    def test_bad_tolerance_mode_rejected(self):
        with pytest.raises(ValueError, match="tolerance_mode must be"):
            _mode(tolerance_mode="relative")

    def test_resolves_via_factory(self):
        mode = resolve_mode(
            {"mode": "scalar", "quantity": {"reduce": "mean"}, "tolerance_mode": "rel"},
            tolerance=0.05,
        )
        assert isinstance(mode, ScalarMode)
        assert mode.reduction == "mean"
        assert mode.config.tolerance == Sides(0.05, 0.05)
        assert mode.config.tolerance_mode == "rel"

    def test_stats_path_quantity_defaults_to_final_reduction(self):
        # The evaluator hands stats-path leaves 1-sample arrays, so "final"
        # is the reduction that reads them back unchanged.
        assert _mode(quantity="translation.continuous_time_states").reduction == "final"


# ---------------------------------------------------------------------------
# Reductions
# ---------------------------------------------------------------------------


class TestScalarReductions:
    @pytest.mark.parametrize(
        "reduction,expected",
        [
            ("max", 4.0),
            ("min", -1.0),
            ("mean", 1.5),
            ("sample_mean", 1.5),
            ("final", 2.0),
        ],
    )
    def test_reductions(self, reduction, expected):
        t, v = _series([1.0, 4.0, -1.0, 2.0])
        mode = _mode(quantity={"reduce": reduction}, tolerance=0.0)
        vc = mode.compare(t, v, t, v)
        assert vc.actual_final == pytest.approx(expected)
        assert vc.reference_final == pytest.approx(expected)
        assert vc.passed

    def test_mean_is_time_weighted_not_sample_mean(self):
        # Samples clustered early: the sample mean is pulled toward 0, the
        # time-weighted mean is the integral over the span.
        t = np.array([0.0, 0.1, 0.2, 10.0])
        v = np.array([0.0, 0.0, 0.0, 10.0])
        assert float(np.mean(v)) == pytest.approx(2.5)
        expected = (0.5 * 9.8 * 10.0) / 10.0  # trapezoid area / span
        mode = _mode(quantity={"reduce": "mean"}, tolerance=1e-9)
        vc = mode.compare(t, v, t, v)
        assert vc.actual_final == pytest.approx(expected)

    def test_mean_is_grid_independent(self):
        # Same ramp, different output grids — the score must stay 0.
        coarse_t = np.linspace(0.0, 1.0, 3)
        fine_t = np.linspace(0.0, 1.0, 101)
        mode = _mode(quantity={"reduce": "mean"}, tolerance=1e-12)
        vc = mode.compare(coarse_t, coarse_t, fine_t, fine_t)
        assert vc.passed
        assert vc.actual_final == pytest.approx(0.5)

    def test_sample_mean_is_the_arithmetic_mean_on_a_clustered_grid(self):
        # Same clustered series as the time-weighted case above: sample_mean
        # is np.mean, and that is NOT the time-weighted mean.
        t = np.array([0.0, 0.1, 0.2, 10.0])
        v = np.array([0.0, 0.0, 0.0, 10.0])
        vc = _mode(quantity={"reduce": "sample_mean"}, tolerance=1e-12).compare(
            t, v, t, v
        )
        assert vc.actual_final == pytest.approx(float(np.mean(v)))
        assert vc.actual_final == pytest.approx(2.5)
        weighted = _mode(quantity={"reduce": "mean"}, tolerance=1e-12).compare(
            t, v, t, v
        )
        assert vc.actual_final != pytest.approx(weighted.actual_final)

    def test_sample_mean_on_a_uniform_grid(self):
        t = np.linspace(0.0, 1.0, 5)
        v = 2.0 * t
        mode = _mode(quantity={"reduce": "sample_mean"}, tolerance=1e-12)
        assert mode.reduction == "sample_mean"
        vc = mode.compare(t, v, t, v)
        assert vc.actual_final == pytest.approx(float(np.mean(v)))

    @pytest.mark.parametrize(
        "t,act,expected",
        [
            ([2.0, 3.0, 4.0], [0.0, 0.0, 8.0], 2.0),
            ([0.0, 0.25, 1.0], [0.0, 0.0, 2.0], 0.75),
        ],
        ids=["a span starting at 2", "a span shorter than 1"],
    )
    def test_mean_is_the_area_over_the_series_own_span(self, t, act, expected):
        t, v = np.array(t), np.array(act)
        vc = _mode(quantity={"reduce": "mean"}, tolerance=0.0).compare(t, v, t, v)
        assert vc.actual_final == pytest.approx(expected)

    @pytest.mark.parametrize("stamp", [0.0, np.nan, np.inf])
    def test_mean_of_single_sample_is_the_sample_mean(self, stamp):
        t, v = np.array([stamp]), np.array([7.0])
        vc = _mode(quantity={"reduce": "mean"}, tolerance=0.0).compare(t, v, t, v)
        assert vc.passed is True
        assert vc.actual_final == pytest.approx(7.0)

    @pytest.mark.parametrize(
        "t,v,area",
        [
            # y = 2t on [0, 3] → area 9. Non-uniform spacing on purpose.
            ([0.0, 0.5, 2.0, 3.0], [0.0, 1.0, 4.0, 6.0], 9.0),
            ([0.0, 2.0], [1.0, 5.0], 6.0),
        ],
        ids=["non-uniform", "two samples"],
    )
    def test_integral_is_the_trapezoid_rule(self, t, v, area):
        t, v = np.array(t), np.array(v)
        mode = _mode(quantity={"reduce": "integral"}, tolerance=1e-9)
        vc = mode.compare(t, v, t, v)
        assert vc.actual_final == pytest.approx(area)

    def test_integral_of_single_sample_is_zero(self):
        t, v = np.array([0.0]), np.array([5.0])
        mode = _mode(quantity={"reduce": "integral"}, tolerance=0.0)
        vc = mode.compare(t, v, t, v)
        assert vc.actual_final == 0.0

    def test_empty_actual_hard_fails(self):
        t, v = _series([1.0, 2.0])
        empty = np.array([])
        vc = _mode(tolerance=1.0).compare(t, v, empty, empty)
        assert not vc.passed
        assert "no actual samples" in vc.diagnostics["error"]

    def test_empty_reference_hard_fails_in_baseline_form(self):
        t, v = _series([1.0, 2.0])
        empty = np.array([])
        vc = _mode(tolerance=1.0).compare(empty, empty, t, v)
        assert not vc.passed
        assert "no reference samples" in vc.diagnostics["error"]


# ---------------------------------------------------------------------------
# Comparison semantics
# ---------------------------------------------------------------------------


class TestScalarComparison:
    def test_abs_tolerance_pass_and_fail(self):
        ref_t, ref_v = _series([0.0, 10.0])
        act_t, act_v = _series([0.0, 10.05])
        assert _mode(tolerance=0.1).compare(ref_t, ref_v, act_t, act_v).passed
        vc = _mode(tolerance=0.01).compare(ref_t, ref_v, act_t, act_v)
        assert not vc.passed
        assert vc.max_abs_error == pytest.approx(0.05)
        assert vc.nrmse == pytest.approx(0.05)

    def test_rel_tolerance_normalizes_by_reference(self):
        ref_t, ref_v = _series([0.0, 100.0])
        act_t, act_v = _series([0.0, 101.0])
        mode = _mode(tolerance=0.02, tolerance_mode="rel")
        vc = mode.compare(ref_t, ref_v, act_t, act_v)
        assert vc.passed
        assert vc.nrmse == pytest.approx(0.01)
        assert vc.max_abs_error == pytest.approx(1.0)
        tight = _mode(tolerance=0.005, tolerance_mode="rel")
        assert not tight.compare(ref_t, ref_v, act_t, act_v).passed

    def test_rel_falls_back_to_absolute_on_zero_reference(self):
        ref_t, ref_v = _series([1.0, 0.0])
        act_t, act_v = _series([1.0, 0.05])
        mode = _mode(tolerance=0.1, tolerance_mode="rel")
        vc = mode.compare(ref_t, ref_v, act_t, act_v)
        assert vc.passed
        assert vc.nrmse == pytest.approx(0.05)
        # The fallback is recorded so the report can say which criterion
        # actually decided the verdict.
        assert vc.diagnostics["abs_fallback"] is True
        assert (
            not _mode(tolerance=0.01, tolerance_mode="rel")
            .compare(ref_t, ref_v, act_t, act_v)
            .passed
        )

    def test_no_abs_fallback_flag_on_a_normal_relative_comparison(self):
        vc = _mode(tolerance=0.1, tolerance_mode="rel").compare(
            *_series([0.0, 100.0]), *_series([0.0, 101.0])
        )
        assert vc.diagnostics["abs_fallback"] is False

    def test_scalar_never_claims_a_constant_signal(self):
        # is_constant means "the reference SIGNAL has zero range" and the
        # console renders it as "(constant signal)" — a scalar leaf compares
        # no signals, so it must not set the flag even on a zero reference.
        vc = _mode(tolerance=1.0).compare(*_series([1.0, 0.0]), *_series([1.0, 0.0]))
        assert vc.is_constant is False

    def test_bounds_form_is_baseline_free(self):
        mode = _mode(min_value=0.0, max_value=1.0)
        assert mode.is_baseline_free()
        assert not _mode().is_baseline_free()

    def test_bounds_form_scores_without_a_reference(self):
        empty = np.array([])
        act_t, act_v = _series([0.0, 0.5])
        vc = _mode(min_value=0.0, max_value=1.0).compare(empty, empty, act_t, act_v)
        assert vc.passed
        assert vc.actual_final == pytest.approx(0.5)
        assert np.isnan(vc.reference_final)

    def test_bounds_form_score_is_distance_outside(self):
        empty = np.array([])
        act_t, act_v = _series([0.0, 1.25])
        vc = _mode(min_value=0.0, max_value=1.0).compare(empty, empty, act_t, act_v)
        assert not vc.passed
        assert vc.nrmse == pytest.approx(0.25)
        assert vc.max_abs_error == pytest.approx(0.25)

    def test_one_sided_lower_bound(self):
        empty = np.array([])
        act_t, act_v = _series([0.0, -2.0])
        mode = _mode(quantity={"reduce": "min"}, min_value=-1.0)
        vc = mode.compare(empty, empty, act_t, act_v)
        assert not vc.passed
        assert vc.nrmse == pytest.approx(1.0)
        assert mode.compare(empty, empty, *_series([0.0, -0.5])).passed

    def test_one_sided_upper_bound(self):
        empty = np.array([])
        mode = _mode(quantity={"reduce": "max"}, max_value=10.0)
        assert mode.compare(empty, empty, *_series([0.0, 9.0])).passed
        assert not mode.compare(empty, empty, *_series([0.0, 11.0])).passed


# ---------------------------------------------------------------------------
# Evaluator wiring
# ---------------------------------------------------------------------------


class TestScalarLeafEvaluation:
    def test_series_reduction_respects_the_leaf_window(self):
        # Full-series max is 9 on both sides; inside [0, 2] it is 2 vs 2.5.
        time = np.linspace(0.0, 9.0, 10)
        ref = BaselineView(
            name=PRIMARY_BASELINE,
            ref_vars_by_name={"x": _stored("x", time)},
            shared_ref_time=time,
        )
        act = np.array([0.0, 1.0, 2.5, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0])
        results = {"x": VariableResult(index=1, name="x", time=time, values=act)}

        spec = parse_metric_tree(
            {
                "metric": "scalar",
                "variable": "x",
                "quantity": {"reduce": "max"},
                "tolerance": 0.1,
                "window": {"end": 2.0},
            }
        )
        node = evaluate_spec(spec, results, {PRIMARY_BASELINE: ref}, 1e-4)
        assert not node.passed
        assert node.score == pytest.approx(0.5)
        assert node.diagnostics["window"] == {"start": None, "end": 2.0}
        assert node.diagnostics["quantity"] == {"reduce": "max"}

        wide = parse_metric_tree(
            {
                "metric": "scalar",
                "variable": "x",
                "quantity": {"reduce": "max"},
                "tolerance": 0.1,
            }
        )
        assert evaluate_spec(wide, results, {PRIMARY_BASELINE: ref}, 1e-4).passed

    def test_stat_path_from_nested_statistics(self):
        spec = parse_metric_tree(
            {
                "metric": "scalar",
                "quantity": "translation.scalar_unknowns",
                "tolerance": 0,
            }
        )
        stats = {"translation": {"scalar_unknowns": 42}}
        node = evaluate_spec(
            spec,
            {},
            _baselines(statistics=stats),
            1e-4,
            scalars=ScalarSources(statistics=stats),
        )
        assert node.passed
        assert node.score == 0.0
        # No ``variable`` authored — the path is the label.
        assert node.label == "translation.scalar_unknowns"

        drifted = evaluate_spec(
            spec,
            {},
            _baselines(statistics={"translation": {"scalar_unknowns": 43}}),
            1e-4,
            scalars=ScalarSources(statistics=stats),
        )
        assert not drifted.passed
        assert drifted.score == pytest.approx(1.0)

    def test_a_variables_number_reduces_the_runs_diagnostic_live(self):
        # Baseline carries the stored summary; the run carries a trajectory.
        spec = parse_metric_tree(
            {
                "metric": "scalar",
                "variable": "CPUtime",
                "quantity": {"reduce": "final"},
                "tolerance": 0.5,
            }
        )
        baseline = _baselines(
            diagnostics=[{"name": "CPUtime", "final": 1.0, "min": 0.1, "max": 1.0}]
        )
        run = ScalarSources(
            diagnostics=[
                VariableResult(
                    index=1,
                    name="CPUtime",
                    time=np.array([0.0, 1.0, 2.0]),
                    values=np.array([0.1, 0.7, 1.2]),
                )
            ]
        )
        node = evaluate_spec(spec, {}, baseline, 1e-4, scalars=run)
        vc = node.diagnostics["variable"]
        assert vc.actual_final == pytest.approx(1.2)
        assert vc.reference_final == pytest.approx(1.0)
        assert node.passed

    def test_a_listed_name_with_one_sample_is_scored_from_it(self):
        spec = parse_metric_tree(
            {"metric": "scalar", "variable": "CPUtime", "quantity": {"reduce": "max"}}
        )
        baseline = _baselines(
            diagnostics=[{"name": "CPUtime", "final": 7.0, "min": 7.0, "max": 7.0}]
        )
        run = ScalarSources(
            diagnostics=[
                VariableResult(
                    index=1,
                    name="CPUtime",
                    time=np.array([0.0]),
                    values=np.array([7.0]),
                )
            ]
        )
        node = evaluate_spec(spec, {}, baseline, 1e-3, scalars=run)
        assert node.passed
        assert summarize(node.diagnostics["variable"]).criterion == (
            "Scalar 7 vs baseline 7: absolute deviation 0.000e+00 vs tolerance "
            "1.000e-03 → PASS"
        )

    def test_a_boolean_statistic_is_not_a_number(self):
        spec = parse_metric_tree(
            {
                "metric": "scalar",
                "variable": "flag",
                "quantity": "a.flag",
                "min_value": 0,
                "max_value": 2,
            }
        )
        node = evaluate_spec(
            spec, {}, {}, 1e-3, scalars=ScalarSources(statistics={"a": {"flag": True}})
        )
        assert not node.passed
        assert node.diagnostics["variable"].diagnostics["error"] == (
            "quantity 'a.flag' not found in current run"
        )

    @pytest.mark.parametrize("listed", [True, False], ids=["listed", "not listed"])
    def test_a_reduction_over_an_unmigrated_baseline_carries_the_hint(self, listed):
        """A UnitTests variable the baseline still stores under its
        expression: the reason names where the baseline has it, whether or
        not the test also lists the name."""
        t = np.linspace(0.0, 1.0, 5)
        u = VariableResult(2, t, t * 2.0, name="unitTests.x[1]", expression="a.Q")
        spec = parse_metric_tree(
            {
                "metric": "scalar",
                "variable": "unitTests.x[1]",
                "quantity": {"reduce": "final"},
            }
        )
        baseline = {
            PRIMARY_BASELINE: BaselineView(
                PRIMARY_BASELINE,
                {"a.Q": StoredVariable(1, "a.Q", None, list(t * 2.0))},
                t,
            )
        }
        node = evaluate_spec(
            spec,
            {"unitTests.x[1]": u},
            baseline,
            1e-3,
            scalars=ScalarSources(diagnostics=[u] if listed else []),
        )
        assert not node.passed
        assert node.diagnostics["variable"].diagnostics["error"] == (
            "variable 'unitTests.x[1]' not found in the baseline — the baseline "
            "stores it under its expression 'a.Q'; the stored name must be the "
            "result-file name 'unitTests.x[1]'"
        )

    @pytest.mark.parametrize("field_name,expected", [("min", 0.1), ("max", 1.2)])
    def test_a_variables_min_and_max_read_their_fields(self, field_name, expected):
        spec = parse_metric_tree(
            {
                "metric": "scalar",
                "variable": "CPUtime",
                "quantity": {"reduce": field_name},
                "tolerance": 10.0,
            }
        )
        run = ScalarSources(
            diagnostics=[
                VariableResult(
                    index=1,
                    name="CPUtime",
                    time=np.array([0.0, 1.0, 2.0]),
                    values=np.array([0.1, 0.7, 1.2]),
                )
            ]
        )
        node = evaluate_spec(
            spec,
            {},
            _baselines(diagnostics=[{"name": "CPUtime", field_name: expected}]),
            1e-4,
            scalars=run,
        )
        assert node.diagnostics["variable"].actual_final == pytest.approx(expected)

    def test_legacy_full_array_diagnostics_entry_is_summarized(self):
        # Pre-summary baselines stored the whole diagnostic trajectory: the
        # codec summarizes such an entry on read (`_baselines` decodes the
        # file's shape), and the evaluator reads the summary.
        spec = parse_metric_tree(
            {
                "metric": "scalar",
                "variable": "CPUtime",
                "quantity": {"reduce": "max"},
                "tolerance": 0.0,
            }
        )
        run = ScalarSources(
            diagnostics=[
                VariableResult(
                    index=1,
                    name="CPUtime",
                    time=np.array([0.0, 1.0]),
                    values=np.array([0.2, 0.9]),
                )
            ]
        )
        node = evaluate_spec(
            spec,
            {},
            _baselines(
                diagnostics=[
                    {"name": "CPUtime", "time": [0.0, 1.0], "values": [0.1, 0.9]}
                ]
            ),
            1e-4,
            scalars=run,
        )
        assert node.passed
        assert node.diagnostics["variable"].reference_final == pytest.approx(0.9)

    def test_missing_path_on_the_current_side_hard_fails(self):
        spec = parse_metric_tree({"metric": "scalar", "quantity": "timing.nope"})
        node = evaluate_spec(
            spec,
            {},
            _baselines(statistics={"timing": {"nope": 1.0}}),
            1e-4,
            scalars=ScalarSources(),
        )
        assert not node.passed
        # The label is the leaf's variable — the path, since none was
        # authored — and the reason is a diagnostic beside it.
        assert node.label == "timing.nope"
        error = node.diagnostics["variable"].diagnostics["error"]
        assert error == "quantity 'timing.nope' not found in current run"

    def test_authored_variable_labels_the_leaf_and_the_path_is_the_reason(self):
        spec = parse_metric_tree(
            {"metric": "scalar", "variable": "solver cost", "quantity": "timing.nope"}
        )
        node = evaluate_spec(spec, {}, _baselines(), 1e-4, scalars=ScalarSources())
        assert node.label == "solver cost"
        assert (
            "quantity 'timing.nope'"
            in node.diagnostics["variable"].diagnostics["error"]
        )

    def test_missing_path_on_the_baseline_side_hard_fails(self):
        spec = parse_metric_tree({"metric": "scalar", "quantity": "timing.total"})
        node = evaluate_spec(
            spec,
            {},
            _baselines(),
            1e-4,
            scalars=ScalarSources(statistics={"timing": {"total": 2.0}}),
        )
        assert not node.passed
        assert node.label == "timing.total"
        error = node.diagnostics["variable"].diagnostics["error"]
        assert "timing.total" in error
        assert "baseline 'primary'" in error

    def test_stat_leaf_against_a_soft_check(self):
        spec = parse_metric_tree(
            {
                "metric": "scalar",
                "quantity": "timing.total",
                "against": "experiment",
                "tolerance": 0.5,
            }
        )
        baselines = {
            PRIMARY_BASELINE: BaselineView(
                name=PRIMARY_BASELINE,
                ref_vars_by_name={},
                shared_ref_time=np.array([]),
                statistics={},
            ),
            "experiment": BaselineView(
                name="experiment",
                ref_vars_by_name={},
                shared_ref_time=np.array([]),
                statistics={"timing": {"total": 2.0}},
            ),
        }
        run = ScalarSources(statistics={"timing": {"total": 2.25}})
        node = evaluate_spec(spec, {}, baselines, 1e-4, scalars=run)
        assert node.passed
        assert node.diagnostics["against"] == "experiment"

        # The primary baseline has no such path — same leaf, hard fail.
        against_primary = parse_metric_tree(
            {"metric": "scalar", "quantity": "timing.total", "tolerance": 0.5}
        )
        missing = evaluate_spec(against_primary, {}, baselines, 1e-4, scalars=run)
        assert not missing.passed
        assert (
            "baseline 'primary'" in missing.diagnostics["variable"].diagnostics["error"]
        )

    def test_bounds_form_with_no_baseline_at_all(self):
        spec = parse_metric_tree(
            {
                "metric": "scalar",
                "quantity": "timing.total",
                "max_value": 5.0,
            }
        )
        node = evaluate_spec(
            spec,
            {},
            {},
            1e-4,
            scalars=ScalarSources(statistics={"timing": {"total": 2.0}}),
        )
        assert node.passed
        assert node.diagnostics["variable"].actual_final == pytest.approx(2.0)

    def test_window_is_ignored_for_a_stats_path_leaf(self):
        spec = parse_metric_tree(
            {
                "metric": "scalar",
                "quantity": "timing.total",
                "tolerance": 0.0,
                "window": {"start": 1.0, "end": 2.0},
            }
        )
        stats = {"timing": {"total": 2.0}}
        node = evaluate_spec(
            spec,
            {},
            _baselines(statistics=stats),
            1e-4,
            scalars=ScalarSources(statistics=stats),
        )
        assert node.passed
        assert "window" not in node.diagnostics

    def test_compare_test_threads_the_run_scalars(self):
        from dstf.comparison.comparator import compare_test

        test = TestModel(
            model_id="Test.Model",
            source_file=None,
            n_vars=0,
            metric_tree_spec=parse_metric_tree(
                {
                    "metric": "scalar",
                    "quantity": "translation.scalar_unknowns",
                    "tolerance": 0.0,
                }
            ),
        )
        result = TestResult(
            model_id="Test.Model",
            success=True,
            variables=[],
            statistics={"translation": {"scalar_unknowns": 12}},
        )
        reference = {
            "test_id": "0001",
            "variables": [],
            "statistics": {"translation": {"scalar_unknowns": 12}},
        }
        comp = compare_test(test, result, decode_reference(reference))
        assert comp.passed


# ---------------------------------------------------------------------------
# Parse rules
# ---------------------------------------------------------------------------


class TestScalarLeafParsing:
    def test_variable_defaults_to_a_string_quantity(self):
        leaf = parse_metric_tree(
            {"metric": "scalar", "quantity": "translation.continuous_time_states"}
        )
        assert leaf.variable == "translation.continuous_time_states"
        assert leaf.params["quantity"] == "translation.continuous_time_states"

    def test_explicit_variable_is_the_display_label(self):
        leaf = parse_metric_tree(
            {
                "metric": "scalar",
                "variable": "solver cost",
                "quantity": "translation.continuous_time_states",
            }
        )
        assert leaf.variable == "solver cost"

    def test_reduction_quantity_still_requires_a_variable(self):
        from dstf.comparison.tree_spec import MetricSpecError

        with pytest.raises(MetricSpecError, match="variable"):
            parse_metric_tree({"metric": "scalar", "quantity": {"reduce": "max"}})

    def test_string_quantity_does_not_name_data_for_other_metrics(self):
        # Only a metric declaring accepts_quantity may drop ``variable``.
        from dstf.comparison.tree_spec import MetricSpecError

        for metric_name in ("nrmse", "tube", "points", "range", "event-timing"):
            with pytest.raises(MetricSpecError, match="variable"):
                parse_metric_tree({"metric": metric_name, "quantity": "timing.total"})


class TestQuantityGate:
    """Only ``accepts_quantity`` metrics may swap their data source.

    Before the gate the switch keyed off the param alone, so a stray
    ``quantity`` on an nrmse leaf collapsed both sides to one identical
    sample and a 2x-off trajectory scored 0.0 / passed.
    """

    def test_exactly_scalar_among_builtins_accepts_quantity(self):
        from dstf.comparison import registry

        accepting = {d.name for d in registry.metrics() if d.accepts_quantity}
        assert accepting == {"scalar"}

    def test_nrmse_leaf_with_a_quantity_still_scores_its_trajectory(self):
        time = np.linspace(0.0, 1.0, 11)
        ref = BaselineView(
            name=PRIMARY_BASELINE,
            ref_vars_by_name={"x": _stored("x", time)},
            shared_ref_time=time,
            statistics={"timing": {"total": 2.0}},
        )
        results = {"x": VariableResult(index=1, name="x", time=time, values=2.0 * time)}
        spec = parse_metric_tree(
            {
                "metric": "nrmse",
                "variable": "x",
                "quantity": "timing.total",
                "tolerance": 1e-6,
            }
        )
        node = evaluate_spec(
            spec,
            results,
            {PRIMARY_BASELINE: ref},
            1e-4,
            scalars=ScalarSources(statistics={"timing": {"total": 2.0}}),
        )
        # Actual is 2x the reference — the trajectory must be what's scored.
        assert not node.passed
        assert node.score > 0.1

    def test_scalar_leaf_with_path_and_explicit_variable_scores_the_path(self):
        # A real tracked series named by ``variable`` is only the label —
        # the string quantity still decides what gets scored.
        time = np.linspace(0.0, 1.0, 11)
        ref = BaselineView(
            name=PRIMARY_BASELINE,
            ref_vars_by_name={"x": _stored("x", time)},
            shared_ref_time=time,
            statistics={"timing": {"total": 2.0}},
        )
        results = {"x": VariableResult(index=1, name="x", time=time, values=2.0 * time)}
        spec = parse_metric_tree(
            {
                "metric": "scalar",
                "variable": "x",
                "quantity": "timing.total",
                "tolerance": 0.0,
            }
        )
        node = evaluate_spec(
            spec,
            results,
            {PRIMARY_BASELINE: ref},
            1e-4,
            scalars=ScalarSources(statistics={"timing": {"total": 2.0}}),
        )
        assert node.passed
        vc = node.diagnostics["variable"]
        assert vc.actual_final == pytest.approx(2.0)
        assert node.label == "x"

    def test_flat_override_with_a_stats_path_hard_fails(self):
        # The flat variable_overrides path has no run scalars; before this
        # guard ScalarMode reduced the variable's own trajectory instead
        # (x final identical on both sides → PASS while CPUtime moved 1→5).
        from dstf.comparison.comparator import compare_test

        time = np.linspace(0.0, 1.0, 11)
        test = TestModel(
            model_id="Test.Model",
            source_file=None,
            n_vars=1,
            variable_patterns=["x"],
            variable_overrides={
                "x": {
                    "mode": "scalar",
                    "quantity": "translation.continuous_time_states",
                    "tolerance": 0.0,
                }
            },
        )
        result = TestResult(
            model_id="Test.Model",
            success=True,
            variables=[VariableResult(index=1, name="x", time=time, values=time)],
            diagnostics=[
                VariableResult(
                    index=2, name="CPUtime", time=time, values=np.full(11, 5.0)
                )
            ],
        )
        reference = {
            "test_id": "0001",
            "time": time.tolist(),
            "variables": [{"index": 1, "name": "x", "values": time.tolist()}],
            "diagnostics": [{"name": "CPUtime", "final": 1.0, "min": 1.0, "max": 1.0}],
        }
        comp = compare_test(test, result, decode_reference(reference))
        assert not comp.passed
        (vc,) = comp.variables
        assert vc.mode == "scalar"
        assert "metrics" in vc.diagnostics["error"]


# ---------------------------------------------------------------------------
# Reporter surfaces
# ---------------------------------------------------------------------------


class TestScalarReporterSurfaces:
    def test_exported_schema_carries_the_scalar_mode_def(self):
        from dstf.reporting.schema_export import build_schema

        schema = build_schema()
        assert "mode_scalar" in schema["$defs"]
        props = schema["$defs"]["mode_scalar"]["properties"]
        for field_name in ("quantity", "tolerance", "tolerance_mode", "min_value"):
            assert field_name in props
        assert props["tolerance_mode"]["enum"] == ["abs", "rel"]
        assert "scalar" in schema["$defs"]["leaf"]["properties"]["metric"]["enum"]

    def test_mode_schema_derive_scalar_fields(self):
        from dstf.comparison.mode_schema import derive_schema
        from dstf.comparison.registry import metric

        desc = metric("scalar")
        assert desc is not None
        schema = derive_schema(desc.config_cls, mode="scalar")
        names = [f.name for f in schema.fields]
        assert names == [
            "quantity",
            "tolerance",
            "tolerance_mode",
            "min_value",
            "max_value",
        ]
        # A quantity is a JSON object or a path string — neither a scalar
        # nor an enum, so it derives as a passthrough.
        assert schema.fields[0].type == "passthrough"

    def test_score_display_baseline_form(self):
        from dstf.comparison.registry import summarize

        vc = _mode(tolerance=0.1).compare(*_series([0.0, 10.0]), *_series([0.0, 10.05]))
        vc.tolerance_used = Sides(0.1, 0.1)
        summary = summarize(vc)
        score, criterion = summary.display, summary.criterion
        assert score.startswith("|Δ|")
        assert "10.05" in criterion
        assert "PASS" in criterion

    def test_score_display_rel_and_bounds_forms(self):
        from dstf.comparison.registry import summarize

        rel = _mode(tolerance=0.001, tolerance_mode="rel").compare(
            *_series([0.0, 100.0]), *_series([0.0, 101.0])
        )
        summary = summarize(rel)
        score, criterion = summary.display, summary.criterion
        assert score.startswith("rel Δ")
        assert "relative deviation" in criterion
        assert "FAIL" in criterion

        empty = np.array([])
        bounds = _mode(min_value=0.0, max_value=1.0).compare(
            empty, empty, *_series([0.0, 2.0])
        )
        summary = summarize(bounds)
        score, criterion = summary.display, summary.criterion
        assert score == "value 2"
        assert "within [0, 1]" in criterion
        assert "FAIL" in criterion

    def test_score_display_names_the_abs_fallback(self):
        from dstf.comparison.registry import summarize

        vc = _mode(tolerance=0.1, tolerance_mode="rel").compare(
            *_series([1.0, 0.0]), *_series([1.0, 0.05])
        )
        vc.tolerance_used = Sides(0.1, 0.1)
        summary = summarize(vc)
        score, criterion = summary.display, summary.criterion
        assert score.startswith("|Δ|")
        assert "|ref|=0 → absolute" in criterion
        assert "absolute deviation" in criterion

    def test_score_display_of_a_sentinel_shows_the_reason(self):
        from dstf.comparison.registry import summarize

        # Unresolvable quantity → sentinel with nan scalars; the panel must
        # not read "Scalar nan vs baseline nan".
        spec = parse_metric_tree({"metric": "scalar", "quantity": "timing.nope"})
        node = evaluate_spec(spec, {}, _baselines(), 1e-4, scalars=ScalarSources())
        summary = summarize(node.diagnostics["variable"])
        score, criterion = summary.display, summary.criterion
        assert score == "not scored"
        assert "nan" not in criterion
        assert "timing.nope" in criterion

    def test_score_display_of_an_empty_data_failure_shows_the_error(self):
        from dstf.comparison.registry import summarize

        empty = np.array([])
        vc = _mode(tolerance=1.0).compare(*_series([1.0, 2.0]), empty, empty)
        summary = summarize(vc)
        score, criterion = summary.display, summary.criterion
        assert score == "not scored"
        assert "no actual samples" in criterion

    def test_a_non_finite_reduction_is_a_no_data_failure_with_a_reason(self):
        """A NaN among the samples makes the reduction nan, and a scalar
        leaf that cannot state its number must say WHY. Before this the
        summary fell back to the variable's name, which since the sentinel
        rename is never a reason — the panel read "not scored — x"."""
        from dstf.comparison.registry import summarize

        vc = _mode(tolerance=1.0, quantity={"reduce": "max"}).compare(
            *_series([1.0, 2.0]), *_series([1.0, float("nan")])
        )
        vc.name = "x"
        assert vc.passed is False
        error = (vc.diagnostics or {})["error"]
        assert "max" in error and "not finite" in error
        summary = summarize(vc)
        assert summary.display == "not scored"
        assert error in summary.criterion
        assert summary.criterion.strip() != "Scalar criterion not scored — x"

    def test_a_non_finite_reference_reduction_names_the_reference(self):
        from dstf.comparison.registry import summarize

        vc = _mode(tolerance=1.0, quantity={"reduce": "max"}).compare(
            *_series([1.0, float("nan")]), *_series([1.0, 2.0])
        )
        assert vc.passed is False
        error = (vc.diagnostics or {})["error"]
        assert "reference" in error and "not finite" in error
        assert error in summarize(vc).criterion


class TestZeroCountRule:
    """A baseline written before the parser always wrote a list's summaries
    holds only the empty list: its count reads as 0 — and nothing else is
    guessed."""

    SPEC = {
        "metric": "scalar",
        "quantity": "translation.nonlinear_count",
        "tolerance": 0,
        "tolerance_mode": "abs",
    }

    def _eval(self, baseline_stats, run_stats):
        return evaluate_spec(
            parse_metric_tree(self.SPEC),
            {},
            _baselines(statistics=baseline_stats),
            1e-4,
            scalars=ScalarSources(statistics=run_stats),
        )

    def test_an_empty_list_in_the_baseline_reads_as_zero(self):
        node = self._eval(
            {"translation": {"nonlinear": []}},
            {"translation": {"nonlinear": [], "nonlinear_count": 0}},
        )
        assert node.passed

    def test_a_count_that_moved_fails_on_its_value(self):
        node = self._eval(
            {"translation": {"nonlinear": []}},
            {"translation": {"nonlinear": [1, 1, 1], "nonlinear_count": 3}},
        )
        assert not node.passed
        assert "error" not in node.diagnostics["variable"].diagnostics
        assert node.diagnostics["variable"].reference_final == 0.0
        assert node.diagnostics["variable"].actual_final == 3.0

    def test_no_list_in_the_baseline_still_hard_fails(self):
        node = self._eval(
            {"translation": {}},
            {"translation": {"nonlinear_count": 0}},
        )
        assert not node.passed
        error = node.diagnostics["variable"].diagnostics["error"]
        assert "not found in baseline" in error


# ---------------------------------------------------------------------------
# A lower and an upper tolerance
# ---------------------------------------------------------------------------


def _scalar_sides(ref, act, tol, mode="abs"):
    t = np.linspace(0.0, 10.0, 11)
    return score(
        {"mode": "scalar", "quantity": {"reduce": "final"}, "tolerance_mode": mode},
        t,
        np.full_like(t, ref),
        t,
        np.full_like(t, act),
        tolerance=tol,
    )


class TestScalarSides:
    """``d = actual − baseline`` (over ``|baseline|`` in ``rel``) passes iff
    ``−lower <= d <= upper``."""

    def test_rel_four_percent_under(self):
        assert _scalar_sides(100.0, 96.0, Sides(0.05, 0.01), "rel").passed
        assert not _scalar_sides(100.0, 96.0, Sides(0.01, 0.05), "rel").passed
        assert _scalar_sides(100.0, 104.0, Sides(0.01, 0.05), "rel").passed
        assert not _scalar_sides(100.0, 104.0, Sides(0.05, 0.01), "rel").passed

    def test_rel_scales_by_the_baselines_magnitude(self):
        # −104 is BELOW −100: the lower side decides.
        assert _scalar_sides(-100.0, -104.0, Sides(0.05, 0.01), "rel").passed
        assert not _scalar_sides(-100.0, -104.0, Sides(0.01, 0.05), "rel").passed

    def test_abs(self):
        assert _scalar_sides(1.0, 1.375, Sides(0.125, 0.5)).passed
        assert not _scalar_sides(1.0, 0.625, Sides(0.125, 0.5)).passed
        assert _scalar_sides(1.0, 0.625, Sides(0.5, 0.125)).passed

    def test_it_is_inclusive_on_each_side(self):
        assert _scalar_sides(1.0, 1.5, Sides(0.25, 0.5)).passed
        assert _scalar_sides(1.0, 0.75, Sides(0.25, 0.5)).passed
        assert not _scalar_sides(1.0, 1.75, Sides(0.25, 0.5)).passed
        assert not _scalar_sides(1.0, 0.5, Sides(0.25, 0.5)).passed

    def test_a_zero_baseline_falls_back_to_absolute_with_a_pair(self):
        vc = _scalar_sides(0.0, 0.375, Sides(0.125, 0.5), "rel")
        assert vc.passed and vc.diagnostics["abs_fallback"] is True
        assert not _scalar_sides(0.0, -0.375, Sides(0.125, 0.5), "rel").passed

    def test_the_score_stays_a_magnitude(self):
        # What a `weighted` combinator reads does not change meaning.
        vc = _scalar_sides(100.0, 96.0, Sides(0.05, 0.01), "rel")
        assert (vc.nrmse, vc.rmse, vc.max_abs_error) == (0.04, 4.0, 4.0)
        assert vc.diagnostics["reference_scalar"] == 100.0

    @pytest.mark.parametrize("mode", ["abs", "rel"])
    def test_a_number_scores_as_it_did(self, mode):
        import random

        rng = random.Random(7)
        on_edge = 0
        for _ in range(3000):
            ref = rng.choice([round(rng.uniform(-3, 3), 1), rng.uniform(-3, 3), 0.0])
            tol = rng.choice(
                [round(rng.uniform(0, 1), 1), rng.uniform(0, 1), 0.0, -0.1]
            )
            act = rng.choice(
                [
                    ref + tol,
                    ref - tol,
                    ref * (1 + tol),
                    ref * (1 - tol),
                    rng.uniform(-3, 3),
                ]
            )
            new = _scalar_sides(ref, act, tol, mode)
            pair = _scalar_sides(ref, act, {"lower": tol, "upper": tol}, mode)
            # The arithmetic of the commit before a pair existed.
            err = abs(act - ref)
            score = err / abs(ref) if mode == "rel" and abs(ref) != 0.0 else err
            assert new.passed == bool(score <= tol), (ref, act, tol)
            assert (new.nrmse, new.rmse) == (score, err)
            assert (pair.passed, pair.nrmse, pair.rmse) == (new.passed, score, err)
            on_edge += score == tol
        assert on_edge > 300  # the sweep reaches the edge itself

    def test_the_bounds_form_is_untouched_by_a_pair(self):
        vc = _mode(
            tolerance={"lower": 0.1, "upper": 0.2}, min_value=0.0, max_value=1.0
        ).compare(*_series([0.0, 10.0]), *_series([0.0, 0.5]))
        assert vc.passed and vc.diagnostics["min_value"] == 0.0

    def test_through_the_mode_and_the_criterion_sentence(self):
        from dstf.comparison.registry import summarize

        mode = resolve_mode(
            {
                "mode": "scalar",
                "quantity": {"reduce": "final"},
                "tolerance_mode": "rel",
            },
            {"lower": 0.05, "upper": 0.01},
        )
        vc = mode.compare(*_series([0.0, 100.0]), *_series([0.0, 96.0]))
        vc.tolerance_used = mode.tolerance  # as both scoring paths stamp it
        assert vc.passed
        assert summarize(vc).criterion == (
            "Scalar 96 vs baseline 100: relative deviation −4.000e-02 vs "
            "tolerance −5.000e-02 / +1.000e-02 → PASS"
        )
