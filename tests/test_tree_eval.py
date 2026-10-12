"""Tests for tree_eval: evaluating user-authored MetricTree specs against
simulation + reference data, and the compare_test integration path.
"""

import json
from pathlib import Path

import numpy as np
import pytest

from dstf.backends.types import TestResult, VariableResult
from dstf.comparison.comparator import compare_test
from dstf.comparison.geometry import Band, Window
from dstf.comparison.metric_tree import implicit_and_tree
from dstf.comparison.sides import Sides
from dstf.comparison.tree_eval import (
    PRIMARY_BASELINE,
    BaselineView,
    collect_leaf_variables,
    evaluate_spec,
    leaf_comparisons_by_path,
)
from dstf.comparison.tree_spec import (
    collect_leaf_paths,
    parse_metric_tree,
    synthesize_implicit_tree,
)
from dstf.discovery.test_registry import TestModel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _primary(ref_vars_by_name, shared_ref_time=None) -> dict:
    """Shortcut: wrap a single ``primary`` baseline for the evaluator."""
    return {
        PRIMARY_BASELINE: BaselineView(
            name=PRIMARY_BASELINE,
            ref_vars_by_name=ref_vars_by_name,
            shared_ref_time=shared_ref_time,
        ),
    }


def _result_with(variables: dict[str, tuple[np.ndarray, np.ndarray]]) -> TestResult:
    """Build a TestResult with the given name → (time, values) pairs."""
    return TestResult(
        model_id="Test.Model",
        success=True,
        variables=[
            VariableResult(index=i + 1, name=name, time=t, values=v)
            for i, (name, (t, v)) in enumerate(variables.items())
        ],
    )


def _reference_with(variables: dict[str, tuple[np.ndarray, np.ndarray]]) -> dict:
    """Build a reference dict with the given variables (shared time is omitted
    to exercise per-variable time arrays — matches the Phase 2 FMU path)."""
    ref_vars = []
    for i, (name, (t, v)) in enumerate(variables.items()):
        ref_vars.append(
            {
                "index": i + 1,
                "name": name,
                "time": t.tolist(),
                "values": v.tolist(),
            }
        )
    return {"test_id": "0001", "variables": ref_vars}


def _test_model(metrics_raw: dict) -> TestModel:
    """Build a TestModel with a parsed MetricTree spec."""
    return TestModel(
        model_id="Test.Model",
        source_file=Path(""),
        source_package="Test",
        short_name="Model",
        n_vars=0,
        metric_tree_spec=parse_metric_tree(metrics_raw),
    )


def _linear(offset: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    time = np.linspace(0.0, 1.0, 11)
    return time, time + offset


# ---------------------------------------------------------------------------
# Leaf evaluation via evaluate_spec
# ---------------------------------------------------------------------------


class TestLeafEval:
    def test_nrmse_leaf_passes_within_tolerance(self):
        spec = parse_metric_tree(
            {
                "metric": "nrmse",
                "variable": "x",
                "tolerance": 0.1,
            }
        )
        t, ref_vals = _linear()
        _, act_vals = _linear(offset=0.001)
        tree = evaluate_spec(
            spec,
            var_results_by_name={
                "x": VariableResult(index=1, name="x", time=t, values=act_vals)
            },
            baselines=_primary(
                {
                    "x": {
                        "index": 1,
                        "name": "x",
                        "time": t.tolist(),
                        "values": ref_vals.tolist(),
                    }
                },
                None,
            ),
            base_tolerance=1e-4,
        )
        assert tree.passed
        assert tree.children == []
        assert tree.diagnostics["tolerance"] == 0.1

    def test_nrmse_leaf_fails_outside_tolerance(self):
        spec = parse_metric_tree(
            {
                "metric": "nrmse",
                "variable": "x",
                "tolerance": 1e-6,
            }
        )
        t, ref_vals = _linear()
        _, act_vals = _linear(offset=0.1)
        tree = evaluate_spec(
            spec,
            var_results_by_name={
                "x": VariableResult(index=1, name="x", time=t, values=act_vals)
            },
            baselines=_primary(
                {
                    "x": {
                        "index": 1,
                        "name": "x",
                        "time": t.tolist(),
                        "values": ref_vals.tolist(),
                    }
                },
                None,
            ),
            base_tolerance=1e-4,
        )
        assert not tree.passed

    def test_leaf_uses_base_tolerance_when_omitted(self):
        spec = parse_metric_tree({"metric": "nrmse", "variable": "x"})
        t, ref_vals = _linear()
        _, act_vals = _linear(offset=0.001)
        tree = evaluate_spec(
            spec,
            var_results_by_name={
                "x": VariableResult(index=1, name="x", time=t, values=act_vals)
            },
            baselines=_primary(
                {
                    "x": {
                        "index": 1,
                        "name": "x",
                        "time": t.tolist(),
                        "values": ref_vals.tolist(),
                    }
                },
                None,
            ),
            base_tolerance=0.1,  # Generous; should pass
        )
        assert tree.passed
        assert tree.diagnostics["tolerance"] == 0.1

    def test_leaf_against_named_baseline(self):
        """A leaf with ``against: 'experiment'`` scores against that baseline."""
        spec = parse_metric_tree(
            {
                "metric": "nrmse",
                "variable": "x",
                "against": "experiment",
                "tolerance": 0.2,
            }
        )
        t, ref_vals = _linear()
        _, act_vals = _linear(offset=0.01)
        # Actual is close to "experiment" (offset 0.01) but far from "primary" (offset 0.5)
        _, primary_vals = _linear(offset=0.5)

        tree = evaluate_spec(
            spec,
            var_results_by_name={
                "x": VariableResult(index=1, name="x", time=t, values=act_vals)
            },
            baselines={
                "primary": BaselineView(
                    name="primary",
                    ref_vars_by_name={
                        "x": {
                            "index": 1,
                            "name": "x",
                            "time": t.tolist(),
                            "values": primary_vals.tolist(),
                        }
                    },
                ),
                "experiment": BaselineView(
                    name="experiment",
                    ref_vars_by_name={
                        "x": {
                            "index": 1,
                            "name": "x",
                            "time": t.tolist(),
                            "values": ref_vals.tolist(),
                        }
                    },
                ),
            },
            base_tolerance=1e-4,
        )
        # Scores against "experiment" (offset 0.01, within tolerance 0.2) → pass.
        # If it incorrectly fell through to "primary" (offset 0.5), it would fail.
        assert tree.passed

    def test_leaf_against_unknown_baseline_fails(self):
        """A leaf naming an absent baseline produces a hard-fail leaf result."""
        spec = parse_metric_tree(
            {
                "metric": "nrmse",
                "variable": "x",
                "against": "nonexistent",
            }
        )
        t, ref_vals = _linear()
        tree = evaluate_spec(
            spec,
            var_results_by_name={
                "x": VariableResult(index=1, name="x", time=t, values=ref_vals)
            },
            baselines={"primary": BaselineView(name="primary", ref_vars_by_name={})},
            base_tolerance=1e-4,
        )
        assert not tree.passed
        # The label is the variable (the plot pairs by it); the diagnostic
        # surfaces the broken baseline name for the report.
        assert tree.label == "x"
        assert "nonexistent" in tree.diagnostics["variable"].diagnostics["error"]

    def test_missing_variable_fails_leaf(self):
        spec = parse_metric_tree({"metric": "nrmse", "variable": "missing"})
        tree = evaluate_spec(
            spec,
            var_results_by_name={},
            baselines=_primary({}, None),
            base_tolerance=1e-4,
        )
        assert not tree.passed

    def test_tube_leaf(self):
        spec = parse_metric_tree(
            {
                "metric": "tube",
                "variable": "x",
                "tube_width_mode": "band",
                "tube_abs": 0.05,
            }
        )
        t, ref_vals = _linear()
        _, act_vals = _linear(offset=0.02)  # Well within 0.05 band
        tree = evaluate_spec(
            spec,
            var_results_by_name={
                "x": VariableResult(index=1, name="x", time=t, values=act_vals)
            },
            baselines=_primary(
                {
                    "x": {
                        "index": 1,
                        "name": "x",
                        "time": t.tolist(),
                        "values": ref_vals.tolist(),
                    }
                },
                None,
            ),
            base_tolerance=1e-4,
        )
        assert tree.passed
        assert tree.diagnostics["mode"] == "tube"

    def test_range_leaf_passes_when_signal_within_bounds(self):
        spec = parse_metric_tree(
            {
                "metric": "range",
                "variable": "x",
                "min_value": 0.0,
                "max_value": 2.0,
            }
        )
        t, ref_vals = _linear()  # 0..1
        _, act_vals = _linear(offset=0.5)  # 0.5..1.5
        tree = evaluate_spec(
            spec,
            var_results_by_name={
                "x": VariableResult(index=1, name="x", time=t, values=act_vals)
            },
            baselines=_primary(
                {
                    "x": {
                        "index": 1,
                        "name": "x",
                        "time": t.tolist(),
                        "values": ref_vals.tolist(),
                    }
                },
                None,
            ),
            base_tolerance=1e-4,
        )
        assert tree.passed
        assert tree.diagnostics["mode"] == "range"

    def test_range_leaf_fails_when_signal_exceeds_max(self):
        spec = parse_metric_tree(
            {
                "metric": "range",
                "variable": "x",
                "min_value": 0.0,
                "max_value": 0.5,
            }
        )
        t, ref_vals = _linear()
        _, act_vals = _linear()  # 0..1, exceeds 0.5
        tree = evaluate_spec(
            spec,
            var_results_by_name={
                "x": VariableResult(index=1, name="x", time=t, values=act_vals)
            },
            baselines=_primary(
                {
                    "x": {
                        "index": 1,
                        "name": "x",
                        "time": t.tolist(),
                        "values": ref_vals.tolist(),
                    }
                },
                None,
            ),
            base_tolerance=1e-4,
        )
        assert not tree.passed

    def test_range_leaf_min_only(self):
        """Only a min bound is specified — max is unbounded."""
        spec = parse_metric_tree(
            {
                "metric": "range",
                "variable": "x",
                "min_value": 0.0,
            }
        )
        t, ref_vals = _linear()
        _, act_vals = _linear(offset=100.0)  # Huge, but positive
        tree = evaluate_spec(
            spec,
            var_results_by_name={
                "x": VariableResult(index=1, name="x", time=t, values=act_vals)
            },
            baselines=_primary(
                {
                    "x": {
                        "index": 1,
                        "name": "x",
                        "time": t.tolist(),
                        "values": ref_vals.tolist(),
                    }
                },
                None,
            ),
            base_tolerance=1e-4,
        )
        assert tree.passed

    def test_range_leaf_rejects_min_max_shorthand(self):
        """Bounds are `min_value` / `max_value` — the short names are keys
        nothing reads, refused where the tree is parsed."""
        from dstf.comparison.tree_spec import MetricSpecError

        with pytest.raises(MetricSpecError) as caught:
            parse_metric_tree(
                {"metric": "range", "variable": "x", "min": 0.0, "max": 2.0}
            )
        assert caught.value.message == (
            'range · x: unknown field "min" — did you mean "min_value"?'
        )

    def test_range_leaf_with_no_bound_fails_its_leaf(self):
        """A range that parses and cannot be built hard-fails with the error
        named (D107), rather than raising out of the evaluator and taking
        the whole comparison with it.
        """
        spec = parse_metric_tree({"metric": "range", "variable": "x"})
        t, act_vals = _linear()
        tree = evaluate_spec(
            spec,
            var_results_by_name={
                "x": VariableResult(index=1, name="x", time=t, values=act_vals)
            },
            baselines=_primary({}, None),
            base_tolerance=1e-4,
        )
        assert not tree.passed
        assert tree.label == "x"
        vc = tree.diagnostics["variable"]
        assert "misconfigured" in vc.diagnostics["error"]
        assert "min_value / max_value" in vc.diagnostics["error"]

    def test_default_points_leaf(self):
        spec = parse_metric_tree(
            {
                "metric": "points",
                "variable": "x",
                "tolerance": 0.01,
            }
        )
        t, ref_vals = _linear()
        _, act_vals = _linear(offset=0.005)
        tree = evaluate_spec(
            spec,
            var_results_by_name={
                "x": VariableResult(index=1, name="x", time=t, values=act_vals)
            },
            baselines=_primary(
                {
                    "x": {
                        "index": 1,
                        "name": "x",
                        "time": t.tolist(),
                        "values": ref_vals.tolist(),
                    }
                },
                None,
            ),
            base_tolerance=1e-4,
        )
        assert tree.passed


# ---------------------------------------------------------------------------
# Combinator evaluation
# ---------------------------------------------------------------------------


class TestCombinatorEval:
    def _vars(self):
        """Two vars 'a' and 'b' — a is close, b is far from reference."""
        t, ref = _linear()
        _, a_act = _linear(offset=0.001)
        _, b_act = _linear(offset=0.5)
        return {
            "a_results": {"a": VariableResult(index=1, name="a", time=t, values=a_act)},
            "a_ref": {
                "a": {
                    "index": 1,
                    "name": "a",
                    "time": t.tolist(),
                    "values": ref.tolist(),
                }
            },
            "b_results": {"b": VariableResult(index=2, name="b", time=t, values=b_act)},
            "b_ref": {
                "b": {
                    "index": 2,
                    "name": "b",
                    "time": t.tolist(),
                    "values": ref.tolist(),
                }
            },
        }

    def _eval(self, spec_dict, var_results, ref_vars, base_tolerance=1e-4):
        return evaluate_spec(
            parse_metric_tree(spec_dict),
            var_results_by_name=var_results,
            baselines=_primary(ref_vars, None),
            base_tolerance=base_tolerance,
        )

    def test_and_passes_when_all_pass(self):
        v = self._vars()
        spec = {
            "combinator": "and",
            "children": [
                {"metric": "nrmse", "variable": "a", "tolerance": 0.01},
                {"metric": "nrmse", "variable": "a", "tolerance": 0.02},
            ],
        }
        tree = self._eval(spec, v["a_results"], v["a_ref"])
        assert tree.passed
        assert len(tree.children) == 2

    def test_and_fails_when_any_fails(self):
        v = self._vars()
        # Both reference the same var 'a', but one leaf is too strict
        spec = {
            "combinator": "and",
            "children": [
                {"metric": "nrmse", "variable": "a", "tolerance": 0.01},
                {"metric": "nrmse", "variable": "a", "tolerance": 1e-8},
            ],
        }
        tree = self._eval(spec, v["a_results"], v["a_ref"])
        assert not tree.passed

    def test_or_passes_when_any_child_passes(self):
        v = self._vars()
        # Strict one fails, loose one passes → OR passes
        spec = {
            "combinator": "or",
            "children": [
                {"metric": "nrmse", "variable": "a", "tolerance": 1e-8},
                {"metric": "nrmse", "variable": "a", "tolerance": 0.1},
            ],
        }
        tree = self._eval(spec, v["a_results"], v["a_ref"])
        assert tree.passed

    def test_or_fails_when_all_children_fail(self):
        v = self._vars()
        spec = {
            "combinator": "or",
            "children": [
                {"metric": "nrmse", "variable": "b", "tolerance": 1e-8},
                {"metric": "nrmse", "variable": "b", "tolerance": 1e-6},
            ],
        }
        tree = self._eval(spec, v["b_results"], v["b_ref"])
        assert not tree.passed

    def test_warn_always_passes_parent_even_on_child_fail(self):
        v = self._vars()
        spec = {
            "combinator": "warn",
            "children": [
                {"metric": "nrmse", "variable": "b", "tolerance": 1e-8},
            ],
        }
        tree = self._eval(spec, v["b_results"], v["b_ref"])
        assert tree.passed
        assert tree.diagnostics["warned"] is True

    def test_warn_parent_passes_without_warning_when_child_passes(self):
        v = self._vars()
        spec = {
            "combinator": "warn",
            "children": [
                {"metric": "nrmse", "variable": "a", "tolerance": 0.1},
            ],
        }
        tree = self._eval(spec, v["a_results"], v["a_ref"])
        assert tree.passed
        assert tree.diagnostics["warned"] is False

    def test_k_of_n_passes_when_k_children_pass(self):
        # 3 children: 2 pass, 1 fails; k=2 should pass
        t, ref = _linear()
        _, close = _linear(offset=0.001)
        _, far = _linear(offset=1.0)
        var_results = {
            "a": VariableResult(index=1, name="a", time=t, values=close),
            "b": VariableResult(index=2, name="b", time=t, values=close),
            "c": VariableResult(index=3, name="c", time=t, values=far),
        }
        ref_vars = {
            "a": {"index": 1, "name": "a", "time": t.tolist(), "values": ref.tolist()},
            "b": {"index": 2, "name": "b", "time": t.tolist(), "values": ref.tolist()},
            "c": {"index": 3, "name": "c", "time": t.tolist(), "values": ref.tolist()},
        }
        spec = {
            "combinator": "k-of-n",
            "k": 2,
            "children": [
                {"metric": "nrmse", "variable": "a", "tolerance": 0.01},
                {"metric": "nrmse", "variable": "b", "tolerance": 0.01},
                {"metric": "nrmse", "variable": "c", "tolerance": 0.01},
            ],
        }
        tree = evaluate_spec(
            parse_metric_tree(spec),
            var_results_by_name=var_results,
            baselines=_primary(ref_vars, None),
            base_tolerance=1e-4,
        )
        assert tree.passed
        assert tree.diagnostics["n_passed"] == 2


# ---------------------------------------------------------------------------
# collect_leaf_variables helper
# ---------------------------------------------------------------------------


class TestCollectLeaves:
    def test_collects_all_leaves_in_tree_order(self):
        t, ref = _linear()
        _, close = _linear(offset=0.001)
        var_results = {
            "a": VariableResult(index=1, name="a", time=t, values=close),
            "b": VariableResult(index=2, name="b", time=t, values=close),
        }
        ref_vars = {
            "a": {"index": 1, "name": "a", "time": t.tolist(), "values": ref.tolist()},
            "b": {"index": 2, "name": "b", "time": t.tolist(), "values": ref.tolist()},
        }
        spec = {
            "combinator": "and",
            "children": [
                {"metric": "nrmse", "variable": "a"},
                {
                    "combinator": "or",
                    "children": [
                        {"metric": "nrmse", "variable": "b", "tolerance": 1e-8},
                        {"metric": "nrmse", "variable": "b", "tolerance": 0.1},
                    ],
                },
            ],
        }
        tree = evaluate_spec(
            parse_metric_tree(spec),
            var_results_by_name=var_results,
            baselines=_primary(ref_vars, None),
            base_tolerance=0.1,
        )
        leaves = collect_leaf_variables(tree)
        # a + b(strict) + b(loose) = 3 leaf comparisons
        assert [vc.name for vc in leaves] == ["a", "b", "b"]


# ---------------------------------------------------------------------------
# compare_test integration — spec path replaces implicit path
# ---------------------------------------------------------------------------


class TestCompareTestIntegration:
    def test_spec_path_replaces_implicit_and(self):
        t, ref_vals = _linear()
        _, act_vals = _linear(offset=0.001)
        result = _result_with({"x": (t, act_vals)})
        reference = _reference_with({"x": (t, ref_vals)})

        test = _test_model(
            {
                "combinator": "and",
                "children": [
                    {"metric": "nrmse", "variable": "x", "tolerance": 0.01},
                ],
            }
        )
        comp = compare_test(test, result, reference)
        assert comp.passed
        assert comp.metric_tree is not None
        assert comp.metric_tree.passed

    def test_spec_or_fallback_passes_when_loose_leaf_passes(self):
        """A strict NRMSE fails, but an OR-branch loose NRMSE passes → pass."""
        t, ref_vals = _linear()
        _, act_vals = _linear(offset=0.01)
        result = _result_with({"x": (t, act_vals)})
        reference = _reference_with({"x": (t, ref_vals)})

        test = _test_model(
            {
                "combinator": "or",
                "children": [
                    {"metric": "nrmse", "variable": "x", "tolerance": 1e-8},
                    {"metric": "nrmse", "variable": "x", "tolerance": 0.1},
                ],
            }
        )
        comp = compare_test(test, result, reference)
        assert comp.passed

    def test_spec_path_ignores_variable_overrides(self):
        """When metric_tree_spec is set, legacy variable_overrides is ignored.

        Documented behavior: the tree fully controls scoring on this path.
        """
        t, ref_vals = _linear()
        _, act_vals = _linear(offset=0.01)
        result = _result_with({"x": (t, act_vals)})
        reference = _reference_with({"x": (t, ref_vals)})

        test = _test_model(
            {
                "metric": "nrmse",
                "variable": "x",
                "tolerance": 0.1,
            }
        )
        # Set a super-strict legacy override — should be ignored
        test.variable_overrides = {"x": {"tolerance": 1e-12}}
        comp = compare_test(test, result, reference)
        assert comp.passed  # Tree's 0.1 tolerance wins; override ignored

    def test_named_baseline_needs_a_store(self):
        """Named baselines come only from `store.get_soft_checks` (D66).

        With no store, the reference file supplies `primary` and nothing
        else — an `against: <name>` leaf gets the missing-baseline sentinel,
        even if the ref file still carries a pre-D66 `baselines` map.
        """
        t, primary_vals = _linear()
        _, exp_vals = _linear(offset=0.05)
        _, act_vals = _linear(offset=0.05)  # matches experiment, drifts from primary

        result = _result_with({"x": (t, act_vals)})
        reference = {
            "test_id": "0001",
            "time": t.tolist(),
            "variables": [{"index": 1, "name": "x", "values": primary_vals.tolist()}],
            # Stale key from before the soft_checks split — ignored.
            "baselines": {
                "experiment": {
                    "time": t.tolist(),
                    "variables": [
                        {"index": 1, "name": "x", "values": exp_vals.tolist()}
                    ],
                },
            },
        }

        test = _test_model(
            {
                "metric": "nrmse",
                "variable": "x",
                "against": "experiment",
                "tolerance": 0.01,
            }
        )
        comp = compare_test(test, result, reference)
        assert not comp.passed
        assert comp.metric_tree is not None
        assert comp.metric_tree.label == "x"
        error = comp.metric_tree.diagnostics["variable"].diagnostics["error"]
        assert "experiment" in error and "baseline not found" in error

    def test_tree_leaf_scores_against_a_store_soft_check(self):
        """A store's soft_checks feed the tree's ``against:`` lookup (D66).

        Primary and the soft_check carry different trajectories, and the
        actual matches the soft_check. A leaf scored against primary would
        fail; the ``against: "experiment"`` leaf passes — so this fails if
        the soft_check merge is ever dropped from compare_test.
        """
        from dstf.storage.baseline import Baseline

        t, primary_vals = _linear()
        _, exp_vals = _linear(offset=0.5)
        _, act_vals = _linear(offset=0.5)  # matches experiment, far from primary

        class _StubStore:
            def get_soft_checks(self, model_id):
                assert model_id == "Test.Model"
                return {
                    "experiment": Baseline(
                        name="experiment",
                        time=t.tolist(),
                        variables=[
                            {"index": 1, "name": "x", "values": exp_vals.tolist()}
                        ],
                    )
                }

        result = _result_with({"x": (t, act_vals)})
        reference = {
            "test_id": "0001",
            "time": t.tolist(),
            "variables": [{"index": 1, "name": "x", "values": primary_vals.tolist()}],
        }

        test = _test_model(
            {
                "combinator": "and",
                "children": [
                    {
                        "combinator": "warn",
                        "children": [
                            # Primary drifted — hard-fails, but warn-wrapped.
                            {"metric": "nrmse", "variable": "x", "tolerance": 1e-6},
                        ],
                    },
                    {
                        "metric": "nrmse",
                        "variable": "x",
                        "against": "experiment",
                        "tolerance": 0.01,
                    },
                ],
            }
        )
        comp = compare_test(test, result, reference, store=_StubStore())

        assert comp.passed
        assert comp.metric_tree is not None
        warn_branch, exp_leaf = comp.metric_tree.children
        # The soft_check resolved: not the missing-baseline sentinel.
        assert exp_leaf.passed
        assert exp_leaf.diagnostics["against"] == "experiment"
        assert "not found" not in exp_leaf.label
        # Same variable scored against primary genuinely fails.
        assert not warn_branch.children[0].passed

    def test_spec_variables_list_matches_tree_leaves(self):
        """TestComparison.variables comes from collect_leaf_variables."""
        t, ref_vals = _linear()
        _, act_vals = _linear(offset=0.001)
        result = _result_with(
            {
                "a": (t, act_vals),
                "b": (t, act_vals),
                "c": (t, act_vals),
            }
        )
        reference = _reference_with(
            {
                "a": (t, ref_vals),
                "b": (t, ref_vals),
                "c": (t, ref_vals),
            }
        )
        # Tree only references a and c — not b
        test = _test_model(
            {
                "combinator": "and",
                "children": [
                    {"metric": "nrmse", "variable": "a", "tolerance": 0.01},
                    {"metric": "nrmse", "variable": "c", "tolerance": 0.01},
                ],
            }
        )
        comp = compare_test(test, result, reference)
        assert comp.passed
        assert [v.name for v in comp.variables] == ["a", "c"]


class TestLeafPathStamping:
    def _eval(self, spec_raw: dict):
        t, ref_vals = _linear()
        _, act_vals = _linear(offset=0.001)
        spec = parse_metric_tree(spec_raw)
        return evaluate_spec(
            spec,
            var_results_by_name={
                "x": VariableResult(index=1, name="x", time=t, values=act_vals)
            },
            baselines=_primary(
                {
                    "x": {
                        "index": 1,
                        "name": "x",
                        "time": t.tolist(),
                        "values": ref_vals.tolist(),
                    }
                }
            ),
            base_tolerance=1e-4,
        )

    def test_leaves_carry_their_path_and_prefixed_geometry(self):
        tree = self._eval(
            {
                "combinator": "and",
                "children": [
                    {"metric": "nrmse", "variable": "x", "tolerance": 0.1},
                    {
                        "metric": "tube",
                        "variable": "x",
                        "tube_width_mode": "band",
                        "tube_abs": 0.01,
                    },
                ],
            }
        )
        by_path = leaf_comparisons_by_path(tree)
        assert set(by_path) == {"/metrics/children/0", "/metrics/children/1"}
        assert tree.children[1].diagnostics["leaf_path"] == "/metrics/children/1"
        band = next(
            p for p in by_path["/metrics/children/1"].geometry if isinstance(p, Band)
        )
        assert band.id == "/metrics/children/1/tube/band"

    def test_a_windowed_leaf_gets_a_window_primitive_with_open_ends_allowed(self):
        tree = self._eval(
            {
                "metric": "nrmse",
                "variable": "x",
                "tolerance": 0.1,
                "window": {"start": 0.2},
            }
        )
        vc = leaf_comparisons_by_path(tree)["/metrics"]
        window = next(p for p in vc.geometry if isinstance(p, Window))
        assert window.id == "/metrics/window"
        assert window.start == 0.2 and window.end is None

    def test_an_unwindowed_leaf_has_no_window_primitive(self):
        tree = self._eval({"metric": "nrmse", "variable": "x", "tolerance": 0.1})
        vc = leaf_comparisons_by_path(tree)["/metrics"]
        assert not any(isinstance(p, Window) for p in vc.geometry)

    @pytest.mark.parametrize("bad", ["abc", None])
    def test_a_tolerance_that_is_no_number_fails_its_leaf_not_the_run(self, bad):
        """A leaf's tolerance that is no number is that leaf's config error
        (D107): `float("abc")` raised out of the evaluator and took the
        whole comparison, its other leaves included, with it."""
        tree = self._eval(
            {
                "combinator": "and",
                "children": [
                    {"metric": "nrmse", "variable": "x", "tolerance": bad},
                    {"metric": "nrmse", "variable": "x", "tolerance": 0.1},
                ],
            }
        )
        by_path = leaf_comparisons_by_path(tree)
        vc = by_path["/metrics/children/0"]
        assert not vc.passed and vc.diagnostics["config_error"] is True
        assert (
            "tolerance" in vc.diagnostics["error"]
            and f"got {json.dumps(bad)}" in vc.diagnostics["error"]
        )
        assert vc.tolerance_used == Sides(
            1e-4, 1e-4
        )  # the base: the leaf's own is none
        assert by_path["/metrics/children/1"].passed

    def test_a_config_error_leaf_still_carries_its_path(self):
        tree = self._eval(
            {"metric": "tube", "variable": "x", "tube_width_mode": "nonsense"}
        )
        assert tree.diagnostics["leaf_path"] == "/metrics"
        assert "/metrics" in leaf_comparisons_by_path(tree)

    def test_implicit_tree_paths_agree_with_the_synthesized_spec(self):
        from dstf.comparison.algorithms import _compare_tube

        t, ref_vals = _linear()
        vcs = []
        for i, name in enumerate(["a", "b"]):
            vc = _compare_tube(
                t, ref_vals, t, ref_vals, {"tube_width_mode": "band", "tube_abs": 0.1}
            )
            vc.index, vc.name = i + 1, name
            vcs.append(vc)
        tree = implicit_and_tree(vcs)
        stamped = list(leaf_comparisons_by_path(tree))
        synthesized = collect_leaf_paths(synthesize_implicit_tree(["a", "b"]))
        assert stamped == synthesized == ["/metrics/children/0", "/metrics/children/1"]
        assert vcs[1].geometry[0].id == "/metrics/children/1/tube/band"

    def test_stamping_is_idempotent(self):
        from dstf.comparison.algorithms import _compare_tube
        from dstf.comparison.metric_tree import leaf_from_variable, stamp_leaf_path

        t, ref_vals = _linear()
        vc = _compare_tube(
            t, ref_vals, t, ref_vals, {"tube_width_mode": "band", "tube_abs": 0.1}
        )
        leaf = leaf_from_variable(vc)
        stamp_leaf_path(leaf, "/metrics")
        stamp_leaf_path(leaf, "/metrics")
        assert vc.geometry[0].id == "/metrics/tube/band"


# ---------------------------------------------------------------------------
# Sentinel leaves: the name stays the variable, the reason is a diagnostic
# ---------------------------------------------------------------------------


class TestSentinelLeavesKeepTheirName:
    """Every hard-fail sentinel names the variable it belongs to.

    The figure layer pairs leaves with plots by variable name, so a
    sentinel whose name carried the reason never joined its own plot.
    The reason rides in ``diagnostics["error"]`` instead — where
    ``registry.summarize``, the console and JUnit already read it.
    """

    def test_incompatible_dataset_sentinel(self):
        from dstf.backends.types import DatasetType

        spec = parse_metric_tree({"metric": "nrmse", "variable": "x"})
        t, vals = _linear()
        tree = evaluate_spec(
            spec,
            var_results_by_name={
                "x": VariableResult(index=1, name="x", time=t, values=vals)
            },
            baselines=_primary({"x": {"time": t.tolist(), "values": vals.tolist()}}),
            base_tolerance=1e-4,
            dataset_kind=DatasetType.SPECTRUM,
        )
        assert not tree.passed
        vc = tree.diagnostics["variable"]
        assert vc.name == "x"
        error = vc.diagnostics["error"]
        assert "time-series" in error
        assert "spectrum" in error
        assert "nrmse" in error

    def test_config_error_sentinel(self):
        spec = parse_metric_tree({"metric": "range", "variable": "x"})
        t, vals = _linear()
        tree = evaluate_spec(
            spec,
            var_results_by_name={
                "x": VariableResult(index=1, name="x", time=t, values=vals)
            },
            baselines=_primary({}, None),
            base_tolerance=1e-4,
        )
        assert not tree.passed
        vc = tree.diagnostics["variable"]
        assert vc.name == "x"
        error = vc.diagnostics["error"]
        assert "misconfigured" in error
        assert "min_value / max_value" in error
        # The key the reporter reads to pin the leaf survives.
        assert vc.diagnostics["config_error"] is True

    def test_missing_baseline_sentinel(self):
        spec = parse_metric_tree(
            {"metric": "nrmse", "variable": "x", "against": "nonexistent"}
        )
        t, vals = _linear()
        tree = evaluate_spec(
            spec,
            var_results_by_name={
                "x": VariableResult(index=1, name="x", time=t, values=vals)
            },
            baselines={"primary": BaselineView(name="primary", ref_vars_by_name={})},
            base_tolerance=1e-4,
        )
        assert not tree.passed
        vc = tree.diagnostics["variable"]
        assert vc.name == "x"
        error = vc.diagnostics["error"]
        assert "nonexistent" in error
        assert "baseline not found" in error

    def test_missing_variable_sentinel(self):
        """The variable a leaf names is not in the result at all — the
        fifth sentinel, and the last one that carried no reason."""
        spec = parse_metric_tree({"metric": "nrmse", "variable": "ghost"})
        t, vals = _linear()
        tree = evaluate_spec(
            spec,
            var_results_by_name={
                "x": VariableResult(index=1, name="x", time=t, values=vals)
            },
            baselines=_primary({"x": {"time": t.tolist(), "values": vals.tolist()}}),
            base_tolerance=1e-4,
        )
        assert not tree.passed
        vc = tree.diagnostics["variable"]
        assert vc.name == "ghost"
        error = vc.diagnostics["error"]
        assert "ghost" in error
        assert "not found in the result" in error

    def test_variable_missing_from_the_baseline_sentinel(self):
        """Same sentinel, other call site: the result has the variable and
        the baseline does not, so the reason must say so."""
        spec = parse_metric_tree({"metric": "nrmse", "variable": "x"})
        t, vals = _linear()
        tree = evaluate_spec(
            spec,
            var_results_by_name={
                "x": VariableResult(index=1, name="x", time=t, values=vals)
            },
            baselines=_primary({}),
            base_tolerance=1e-4,
        )
        assert not tree.passed
        vc = tree.diagnostics["variable"]
        assert vc.name == "x"
        error = vc.diagnostics["error"]
        assert "x" in error
        assert "not found in the baseline" in error

    def test_missing_quantity_sentinel(self):
        from dstf.comparison.tree_eval import ScalarSources

        spec = parse_metric_tree(
            {"metric": "scalar", "variable": "solver cost", "quantity": "timing.nope"}
        )
        tree = evaluate_spec(
            spec,
            var_results_by_name={},
            baselines=_primary({}, None),
            base_tolerance=1e-4,
            scalars=ScalarSources(),
        )
        assert not tree.passed
        vc = tree.diagnostics["variable"]
        assert vc.name == "solver cost"
        error = vc.diagnostics["error"]
        assert "timing.nope" in error
        assert "current run" in error


# ---------------------------------------------------------------------------
# A leaf's tolerance is a number or a lower/upper pair
# ---------------------------------------------------------------------------


class TestALeafsToleranceIsSides:
    def _evaluate(self, tolerance, base=1e-4):
        t, ref = _linear()
        children = [
            {"metric": "points", "variable": "x", "tolerance": tolerance},
            {"metric": "nrmse", "variable": "y", "tolerance": 0.5},
        ]
        return evaluate_spec(
            parse_metric_tree({"combinator": "and", "children": children}),
            var_results_by_name={
                n: VariableResult(index=i + 1, name=n, time=t, values=ref)
                for i, n in enumerate(("x", "y"))
            },
            baselines=_primary(
                {
                    n: {"name": n, "time": t.tolist(), "values": ref.tolist()}
                    for n in "xy"
                }
            ),
            base_tolerance=base,
        )

    def test_a_pair_with_a_third_key_is_the_parsers_to_refuse(self):
        """A key nothing reads, inside the pair: said where the spec is
        read (`strict_keys`), never left for the leaf to fail on."""
        from dstf.comparison.tree_spec import MetricSpecError

        with pytest.raises(MetricSpecError, match='tolerance: unknown field "x"'):
            self._evaluate({"lower": 1, "upper": 2, "x": 3})

    def test_the_view_writes_an_equal_pair_as_the_number(self):
        from dstf.comparison.tree_eval import to_view

        tree = self._evaluate({"lower": 0.05, "upper": 0.05})
        assert tree.passed
        x = tree.children[0]
        assert x.diagnostics["variable"].tolerance_used == Sides(0.05, 0.05)
        assert x.diagnostics["tolerance"] == 0.05
        leaf = to_view(tree)["children"][0]
        assert leaf["tolerance"] == 0.05 and isinstance(leaf["tolerance"], float)
        # The same as the number's.
        assert leaf == to_view(self._evaluate(0.05))["children"][0]

    @pytest.mark.parametrize(
        "bad",
        ["abc", "0.01", None, True, {"lower": 1}],
        ids=repr,
    )
    def test_a_malformed_one_fails_its_leaf_and_the_rest_is_scored(self, bad):
        from dstf.comparison.tree_eval import to_view

        tree = self._evaluate(bad, base=0.25)
        x, y = (c.diagnostics["variable"] for c in tree.children)
        assert not tree.passed and y.passed
        assert x.diagnostics["config_error"] is True
        assert x.diagnostics["error"] == (
            "metric 'points' misconfigured: tolerance must be a number or "
            f'{{"lower": …, "upper": …}}, got {json.dumps(bad)}'
        )
        # The base is the one it reports as applied, as both sides.
        assert x.tolerance_used == Sides(0.25, 0.25)
        assert to_view(tree)["children"][0]["tolerance"] == 0.25
