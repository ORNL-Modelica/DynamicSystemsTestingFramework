"""Tests for idea #46 — time-windowed leaves.

Window is a uniform leaf-level property scoping every metric to
``[window_start, window_end]``. Slicing happens in `tree_eval` before
``mode.compare`` so mode configs stay untouched.
"""
from __future__ import annotations

import numpy as np
import pytest

from dstf.comparison.tree_eval import (
    BaselineView,
    evaluate_spec,
    flatten_evaluation,
)
from dstf.comparison.tree_spec import (
    CombinatorSpec,
    LeafSpec,
    MetricSpecError,
    collect_leaf_paths,
    collect_variables,
    leaves_for_variable,
    parse_metric_tree,
    spec_to_view,
    synthesize_implicit_tree,
)
from dstf.simulators import VariableResult


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


class TestLeafPaths:
    """JSON-Pointer paths for window patch round-trip (idea #46 UI surfacing)."""

    def test_single_leaf_root(self):
        tree = parse_metric_tree({"metric": "nrmse", "variable": "x"})
        assert collect_leaf_paths(tree) == ["/metrics"]

    def test_flat_and(self):
        tree = parse_metric_tree({
            "combinator": "and",
            "children": [
                {"metric": "nrmse", "variable": "x"},
                {"metric": "nrmse", "variable": "y"},
            ],
        })
        assert collect_leaf_paths(tree) == [
            "/metrics/children/0",
            "/metrics/children/1",
        ]

    def test_nested(self):
        tree = parse_metric_tree({
            "combinator": "and",
            "children": [
                {"metric": "nrmse", "variable": "x"},
                {"combinator": "or", "children": [
                    {"metric": "nrmse", "variable": "y"},
                    {"metric": "tube", "variable": "z", "tube_rel": 0.02},
                ]},
            ],
        })
        assert collect_leaf_paths(tree) == [
            "/metrics/children/0",
            "/metrics/children/1/children/0",
            "/metrics/children/1/children/1",
        ]

    def test_custom_root(self):
        tree = parse_metric_tree({"metric": "nrmse", "variable": "x"})
        assert collect_leaf_paths(tree, root="/other") == ["/other"]

    def test_order_matches_evaluation(self):
        """Walk order matches collect_leaf_variables so zip aligns."""
        from dstf.comparison.tree_eval import collect_leaf_variables

        t = np.linspace(0.0, 10.0, 101)
        ref = _baseline(t, np.sin(t))
        act = _var(t, np.sin(t))

        tree = parse_metric_tree({
            "combinator": "and",
            "children": [
                {"metric": "nrmse", "variable": "x"},
                {"combinator": "warn", "children": [
                    {"metric": "nrmse", "variable": "x"},
                ]},
            ],
        })
        result = evaluate_spec(tree, {"x": act}, {"primary": ref}, base_tolerance=1e-3)
        paths = collect_leaf_paths(tree)
        leaves = collect_leaf_variables(result)
        assert len(paths) == len(leaves)


class TestCollectVariables:
    """Stage-1 helper feeding the per-variable plot grouping."""

    def test_single_leaf(self):
        tree = parse_metric_tree({"metric": "nrmse", "variable": "h"})
        assert collect_variables(tree) == ["h"]

    def test_distinct_variables_in_order(self):
        tree = parse_metric_tree({
            "combinator": "and",
            "children": [
                {"metric": "nrmse", "variable": "h"},
                {"metric": "nrmse", "variable": "v"},
            ],
        })
        assert collect_variables(tree) == ["h", "v"]

    def test_dedup_preserves_first_occurrence(self):
        tree = parse_metric_tree({
            "combinator": "and",
            "children": [
                {"metric": "nrmse", "variable": "h"},
                {"metric": "range", "variable": "h", "min": -1.0, "max": 1.0},
                {"metric": "nrmse", "variable": "v"},
                {"metric": "tube", "variable": "h", "tube_rel": 0.02},
            ],
        })
        assert collect_variables(tree) == ["h", "v"]


class TestLeavesForVariable:
    def test_filters_to_named_variable_with_paths(self):
        tree = parse_metric_tree({
            "combinator": "and",
            "children": [
                {"metric": "nrmse", "variable": "h"},
                {"metric": "nrmse", "variable": "v"},
                {"metric": "range", "variable": "h", "min": 0.0, "max": 1.0},
            ],
        })
        h_leaves = leaves_for_variable(tree, "h")
        assert [path for _, path in h_leaves] == [
            "/metrics/children/0",
            "/metrics/children/2",
        ]
        assert [leaf.metric for leaf, _ in h_leaves] == ["nrmse", "range"]

    def test_returns_empty_for_unknown_variable(self):
        tree = parse_metric_tree({"metric": "nrmse", "variable": "h"})
        assert leaves_for_variable(tree, "v") == []

    def test_root_leaf_returns_metrics_path(self):
        tree = parse_metric_tree({"metric": "nrmse", "variable": "h"})
        h_leaves = leaves_for_variable(tree, "h")
        assert h_leaves == [(tree, "/metrics")]


class TestSynthesizeImplicitTree:
    """The render-only synthesizer for tests without an authored ``metrics`` block."""

    def test_single_variable_wraps_in_and(self):
        tree = synthesize_implicit_tree(["h"])
        assert isinstance(tree, CombinatorSpec)
        assert tree.combinator == "and"
        assert len(tree.children) == 1
        assert tree.children[0].variable == "h"
        assert tree.children[0].metric == "nrmse"

    def test_multi_variable_wraps_in_and(self):
        tree = synthesize_implicit_tree(["h", "v"])
        assert isinstance(tree, CombinatorSpec)
        assert tree.combinator == "and"
        assert [c.variable for c in tree.children] == ["h", "v"]
        assert all(isinstance(c, LeafSpec) for c in tree.children)

    def test_override_mode_translates_to_metric(self):
        tree = synthesize_implicit_tree(
            ["h"], variable_overrides={"h": {"mode": "tube", "tube_rel": 0.05}},
        )
        leaf = tree.children[0]
        assert leaf.metric == "tube"
        # mode key consumed; other keys flow into params
        assert "mode" not in leaf.params
        assert leaf.params["tube_rel"] == 0.05

    def test_override_range_metric(self):
        tree = synthesize_implicit_tree(
            ["h"], variable_overrides={"h": {"mode": "range", "min": -1.0, "max": 1.0}},
        )
        leaf = tree.children[0]
        assert leaf.metric == "range"
        assert leaf.params == {"min": -1.0, "max": 1.0}

    def test_base_tolerance_filled_when_override_silent(self):
        tree = synthesize_implicit_tree(["h"], base_tolerance=1e-3)
        assert tree.children[0].params == {"tolerance": 1e-3}

    def test_explicit_tolerance_override_wins(self):
        tree = synthesize_implicit_tree(
            ["h"],
            variable_overrides={"h": {"tolerance": 1e-5}},
            base_tolerance=1e-3,
        )
        assert tree.children[0].params == {"tolerance": 1e-5}

    def test_paths_align_with_collect_leaf_paths(self):
        """Synthesized tree's leaf paths match the same shape as an
        equivalent authored AND tree. Stage 2/4 patch-emission depends
        on this."""
        tree = synthesize_implicit_tree(["h", "v", "w"])
        assert collect_leaf_paths(tree) == [
            "/metrics/children/0",
            "/metrics/children/1",
            "/metrics/children/2",
        ]


class TestSpecToView:
    """The serializer feeding Stage-2's recursive UI component."""

    def test_leaf_root(self):
        spec = parse_metric_tree({"metric": "nrmse", "variable": "h", "tolerance": 1e-3})
        view = spec_to_view(spec)
        assert view["kind"] == "leaf"
        assert view["path"] == "/metrics"
        assert view["metric"] == "nrmse"
        assert view["variable"] == "h"
        assert view["params"] == {"tolerance": 1e-3}
        assert view["against"] == "primary"
        assert view["window"] == {}
        assert view["children"] == []

    def test_window_serialized_when_set(self):
        spec = parse_metric_tree({
            "metric": "nrmse", "variable": "h",
            "window": {"start": 1.0, "end": 5.0},
        })
        view = spec_to_view(spec)
        assert view["window"] == {"start": 1.0, "end": 5.0}

    def test_window_only_start(self):
        spec = parse_metric_tree({
            "metric": "nrmse", "variable": "h",
            "window": {"start": 1.0},
        })
        view = spec_to_view(spec)
        assert view["window"] == {"start": 1.0}

    def test_combinator_with_nested_leaves(self):
        spec = parse_metric_tree({
            "combinator": "and",
            "children": [
                {"metric": "nrmse", "variable": "h"},
                {"combinator": "warn", "children": [
                    {"metric": "nrmse", "variable": "h", "against": "experiment"},
                ]},
            ],
        })
        view = spec_to_view(spec)
        assert view["kind"] == "combinator"
        assert view["combinator"] == "and"
        assert view["path"] == "/metrics"
        assert len(view["children"]) == 2
        c0 = view["children"][0]
        assert c0["path"] == "/metrics/children/0"
        c1 = view["children"][1]
        assert c1["kind"] == "combinator"
        assert c1["combinator"] == "warn"
        assert c1["path"] == "/metrics/children/1"
        grandchild = c1["children"][0]
        assert grandchild["kind"] == "leaf"
        assert grandchild["path"] == "/metrics/children/1/children/0"
        assert grandchild["against"] == "experiment"

    def test_k_of_n_fields_emitted(self):
        spec = parse_metric_tree({
            "combinator": "k-of-n", "k": 2,
            "children": [
                {"metric": "nrmse", "variable": "h"},
                {"metric": "nrmse", "variable": "v"},
                {"metric": "nrmse", "variable": "w"},
            ],
        })
        view = spec_to_view(spec)
        assert view["combinator"] == "k-of-n"
        assert view["k"] == 2

    def test_weighted_fields_emitted(self):
        spec = parse_metric_tree({
            "combinator": "weighted",
            "weights": [0.5, 0.5], "threshold": 1e-3,
            "children": [
                {"metric": "nrmse", "variable": "h"},
                {"metric": "nrmse", "variable": "v"},
            ],
        })
        view = spec_to_view(spec)
        assert view["combinator"] == "weighted"
        assert view["weights"] == [0.5, 0.5]
        assert view["threshold"] == 1e-3
        assert view["direction"] == "less"

    def test_evaluation_merged_by_path(self):
        spec = parse_metric_tree({
            "combinator": "and",
            "children": [
                {"metric": "nrmse", "variable": "x", "tolerance": 1e-3},
                {"metric": "nrmse", "variable": "x", "tolerance": 1e-3},
            ],
        })
        t = np.linspace(0.0, 10.0, 101)
        ref = _baseline(t, np.sin(t))
        act = _var(t, np.sin(t))
        result = evaluate_spec(spec, {"x": act}, {"primary": ref}, base_tolerance=1e-3)
        eval_by_path = flatten_evaluation(result)
        view = spec_to_view(spec, evaluation_by_path=eval_by_path)
        assert view["passed"] is True
        assert view["children"][0]["passed"] is True
        assert view["children"][0]["score"] is not None

    def test_no_evaluation_yields_structure_only(self):
        spec = parse_metric_tree({"metric": "nrmse", "variable": "h"})
        view = spec_to_view(spec)
        assert "passed" not in view
        assert "score" not in view


class TestFlattenEvaluation:
    def test_keys_match_leaf_paths(self):
        spec = parse_metric_tree({
            "combinator": "and",
            "children": [
                {"metric": "nrmse", "variable": "x"},
                {"metric": "nrmse", "variable": "x"},
            ],
        })
        t = np.linspace(0.0, 10.0, 101)
        ref = _baseline(t, np.sin(t))
        act = _var(t, np.sin(t))
        result = evaluate_spec(spec, {"x": act}, {"primary": ref}, base_tolerance=1e-3)
        eval_by_path = flatten_evaluation(result)
        # Root + each child
        assert "/metrics" in eval_by_path
        assert "/metrics/children/0" in eval_by_path
        assert "/metrics/children/1" in eval_by_path
        # Diagnostics (mode, against) bubble up
        assert eval_by_path["/metrics/children/0"].get("mode") == "nrmse"

    def test_variable_stashed_comparison_not_in_entry(self):
        """`diagnostics['variable']` carries a non-JSON-safe VariableComparison;
        flatten_evaluation must drop it."""
        spec = parse_metric_tree({"metric": "nrmse", "variable": "x"})
        t = np.linspace(0.0, 10.0, 101)
        ref = _baseline(t, np.sin(t))
        act = _var(t, np.sin(t))
        result = evaluate_spec(spec, {"x": act}, {"primary": ref}, base_tolerance=1e-3)
        eval_by_path = flatten_evaluation(result)
        assert "variable" not in eval_by_path["/metrics"]
