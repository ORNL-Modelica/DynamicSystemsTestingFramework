"""Tests for tree_eval: evaluating user-authored MetricTree specs against
simulation + reference data, and the compare_test integration path.
"""

from pathlib import Path

import numpy as np
import pytest

from modelica_testing.comparison.comparator import compare_test
from modelica_testing.comparison.tree_eval import (
    collect_leaf_variables,
    evaluate_spec,
)
from modelica_testing.comparison.tree_spec import parse_metric_tree
from modelica_testing.discovery.test_registry import TestModel
from modelica_testing.simulators.base import TestResult, VariableResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
        ref_vars.append({
            "index": i + 1,
            "name": name,
            "time": t.tolist(),
            "values": v.tolist(),
        })
    return {"test_id": "0001", "variables": ref_vars}


def _test_model(metrics_raw: dict) -> TestModel:
    """Build a TestModel with a parsed MetricTree spec."""
    return TestModel(
        model_id="Test.Model",
        mo_file=Path(""),
        package_path="Test",
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
        spec = parse_metric_tree({
            "metric": "nrmse", "variable": "x", "tolerance": 0.1,
        })
        t, ref_vals = _linear()
        _, act_vals = _linear(offset=0.001)
        tree = evaluate_spec(
            spec,
            var_results_by_name={"x": VariableResult(index=1, name="x", time=t, values=act_vals)},
            ref_vars_by_name={"x": {"index": 1, "name": "x", "time": t.tolist(), "values": ref_vals.tolist()}},
            shared_ref_time=None,
            base_tolerance=1e-4,
        )
        assert tree.passed
        assert tree.children == []
        assert tree.diagnostics["tolerance"] == 0.1

    def test_nrmse_leaf_fails_outside_tolerance(self):
        spec = parse_metric_tree({
            "metric": "nrmse", "variable": "x", "tolerance": 1e-6,
        })
        t, ref_vals = _linear()
        _, act_vals = _linear(offset=0.1)
        tree = evaluate_spec(
            spec,
            var_results_by_name={"x": VariableResult(index=1, name="x", time=t, values=act_vals)},
            ref_vars_by_name={"x": {"index": 1, "name": "x", "time": t.tolist(), "values": ref_vals.tolist()}},
            shared_ref_time=None,
            base_tolerance=1e-4,
        )
        assert not tree.passed

    def test_leaf_uses_base_tolerance_when_omitted(self):
        spec = parse_metric_tree({"metric": "nrmse", "variable": "x"})
        t, ref_vals = _linear()
        _, act_vals = _linear(offset=0.001)
        tree = evaluate_spec(
            spec,
            var_results_by_name={"x": VariableResult(index=1, name="x", time=t, values=act_vals)},
            ref_vars_by_name={"x": {"index": 1, "name": "x", "time": t.tolist(), "values": ref_vals.tolist()}},
            shared_ref_time=None,
            base_tolerance=0.1,  # Generous; should pass
        )
        assert tree.passed
        assert tree.diagnostics["tolerance"] == 0.1

    def test_missing_variable_fails_leaf(self):
        spec = parse_metric_tree({"metric": "nrmse", "variable": "missing"})
        tree = evaluate_spec(
            spec,
            var_results_by_name={},
            ref_vars_by_name={},
            shared_ref_time=None,
            base_tolerance=1e-4,
        )
        assert not tree.passed

    def test_tube_leaf(self):
        spec = parse_metric_tree({
            "metric": "tube", "variable": "x",
            "tube_width_mode": "band", "tube_abs": 0.05,
        })
        t, ref_vals = _linear()
        _, act_vals = _linear(offset=0.02)  # Well within 0.05 band
        tree = evaluate_spec(
            spec,
            var_results_by_name={"x": VariableResult(index=1, name="x", time=t, values=act_vals)},
            ref_vars_by_name={"x": {"index": 1, "name": "x", "time": t.tolist(), "values": ref_vals.tolist()}},
            shared_ref_time=None,
            base_tolerance=1e-4,
        )
        assert tree.passed
        assert tree.diagnostics["mode"] == "tube"

    def test_final_only_leaf(self):
        spec = parse_metric_tree({
            "metric": "final-only", "variable": "x", "tolerance": 0.01,
        })
        t, ref_vals = _linear()
        _, act_vals = _linear(offset=0.005)
        tree = evaluate_spec(
            spec,
            var_results_by_name={"x": VariableResult(index=1, name="x", time=t, values=act_vals)},
            ref_vars_by_name={"x": {"index": 1, "name": "x", "time": t.tolist(), "values": ref_vals.tolist()}},
            shared_ref_time=None,
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
            "a_ref":     {"a": {"index": 1, "name": "a", "time": t.tolist(), "values": ref.tolist()}},
            "b_results": {"b": VariableResult(index=2, name="b", time=t, values=b_act)},
            "b_ref":     {"b": {"index": 2, "name": "b", "time": t.tolist(), "values": ref.tolist()}},
        }

    def _eval(self, spec_dict, var_results, ref_vars, base_tolerance=1e-4):
        return evaluate_spec(
            parse_metric_tree(spec_dict),
            var_results_by_name=var_results,
            ref_vars_by_name=ref_vars,
            shared_ref_time=None,
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
            "combinator": "k-of-n", "k": 2,
            "children": [
                {"metric": "nrmse", "variable": "a", "tolerance": 0.01},
                {"metric": "nrmse", "variable": "b", "tolerance": 0.01},
                {"metric": "nrmse", "variable": "c", "tolerance": 0.01},
            ],
        }
        tree = evaluate_spec(
            parse_metric_tree(spec),
            var_results_by_name=var_results,
            ref_vars_by_name=ref_vars,
            shared_ref_time=None,
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
            ref_vars_by_name=ref_vars,
            shared_ref_time=None,
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

        test = _test_model({
            "combinator": "and",
            "children": [
                {"metric": "nrmse", "variable": "x", "tolerance": 0.01},
            ],
        })
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

        test = _test_model({
            "combinator": "or",
            "children": [
                {"metric": "nrmse", "variable": "x", "tolerance": 1e-8},
                {"metric": "nrmse", "variable": "x", "tolerance": 0.1},
            ],
        })
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

        test = _test_model({
            "metric": "nrmse", "variable": "x", "tolerance": 0.1,
        })
        # Set a super-strict legacy override — should be ignored
        test.variable_overrides = {"x": {"tolerance": 1e-12}}
        comp = compare_test(test, result, reference)
        assert comp.passed  # Tree's 0.1 tolerance wins; override ignored

    def test_spec_variables_list_matches_tree_leaves(self):
        """TestComparison.variables comes from collect_leaf_variables."""
        t, ref_vals = _linear()
        _, act_vals = _linear(offset=0.001)
        result = _result_with({
            "a": (t, act_vals),
            "b": (t, act_vals),
            "c": (t, act_vals),
        })
        reference = _reference_with({
            "a": (t, ref_vals),
            "b": (t, ref_vals),
            "c": (t, ref_vals),
        })
        # Tree only references a and c — not b
        test = _test_model({
            "combinator": "and",
            "children": [
                {"metric": "nrmse", "variable": "a", "tolerance": 0.01},
                {"metric": "nrmse", "variable": "c", "tolerance": 0.01},
            ],
        })
        comp = compare_test(test, result, reference)
        assert comp.passed
        assert [v.name for v in comp.variables] == ["a", "c"]
