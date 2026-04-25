"""Tests for tree_spec: parsing user-authored MetricTree JSON into the
typed spec form. No evaluation — just structural + validation behavior.
"""

import pytest

from dstf.comparison.tree_spec import (
    CombinatorSpec,
    LeafSpec,
    MetricSpecError,
    parse_metric_tree,
)


# ---------------------------------------------------------------------------
# Leaf parsing
# ---------------------------------------------------------------------------

class TestLeafSpec:
    def test_minimal_nrmse_leaf(self):
        node = parse_metric_tree({"metric": "nrmse", "variable": "h"})
        assert isinstance(node, LeafSpec)
        assert node.metric == "nrmse"
        assert node.variable == "h"
        assert node.params == {}

    def test_leaf_collects_extra_keys_as_params(self):
        node = parse_metric_tree({
            "metric": "nrmse", "variable": "h", "tolerance": 0.01,
        })
        assert isinstance(node, LeafSpec)
        assert node.params == {"tolerance": 0.01}

    def test_tube_leaf_keeps_tube_specific_params(self):
        node = parse_metric_tree({
            "metric": "tube", "variable": "v",
            "tube_rel": 0.05, "tube_width_mode": "rel",
        })
        assert isinstance(node, LeafSpec)
        assert node.metric == "tube"
        assert node.params == {"tube_rel": 0.05, "tube_width_mode": "rel"}

    def test_final_only_leaf(self):
        node = parse_metric_tree({"metric": "points", "variable": "x"})
        assert isinstance(node, LeafSpec)
        assert node.metric == "points"

    def test_range_leaf_keeps_min_max_params(self):
        node = parse_metric_tree({
            "metric": "range", "variable": "h", "min": 0.0, "max": 1.1,
        })
        assert isinstance(node, LeafSpec)
        assert node.metric == "range"
        assert node.params == {"min": 0.0, "max": 1.1}

    def test_leaf_against_defaults_to_primary(self):
        node = parse_metric_tree({"metric": "nrmse", "variable": "h"})
        assert isinstance(node, LeafSpec)
        assert node.against == "primary"

    def test_leaf_against_explicit_named_baseline(self):
        node = parse_metric_tree({
            "metric": "nrmse", "variable": "h", "against": "experiment",
            "tolerance": 0.05,
        })
        assert isinstance(node, LeafSpec)
        assert node.against == "experiment"
        # against is not folded into params
        assert "against" not in node.params
        assert node.params == {"tolerance": 0.05}

    def test_leaf_against_empty_string_rejected(self):
        with pytest.raises(MetricSpecError, match="against.*non-empty"):
            parse_metric_tree({
                "metric": "nrmse", "variable": "h", "against": "",
            })

    def test_unknown_metric_rejected(self):
        with pytest.raises(MetricSpecError, match="unknown metric 'fft-peak'"):
            parse_metric_tree({"metric": "fft-peak", "variable": "x"})

    def test_missing_variable_rejected(self):
        with pytest.raises(MetricSpecError, match="variable.*required"):
            parse_metric_tree({"metric": "nrmse"})

    def test_empty_variable_rejected(self):
        with pytest.raises(MetricSpecError, match="variable"):
            parse_metric_tree({"metric": "nrmse", "variable": ""})


# ---------------------------------------------------------------------------
# Combinator parsing
# ---------------------------------------------------------------------------

class TestCombinatorSpec:
    def test_and_with_two_leaf_children(self):
        node = parse_metric_tree({
            "combinator": "and",
            "children": [
                {"metric": "nrmse", "variable": "h", "tolerance": 0.01},
                {"metric": "tube",  "variable": "v", "tube_rel": 0.05},
            ],
        })
        assert isinstance(node, CombinatorSpec)
        assert node.combinator == "and"
        assert len(node.children) == 2
        assert all(isinstance(c, LeafSpec) for c in node.children)
        assert node.children[0].variable == "h"

    def test_or_combinator(self):
        node = parse_metric_tree({
            "combinator": "or",
            "children": [
                {"metric": "nrmse", "variable": "x", "tolerance": 0.001},
                {"metric": "nrmse", "variable": "x", "tolerance": 0.05},
            ],
        })
        assert isinstance(node, CombinatorSpec)
        assert node.combinator == "or"

    def test_warn_wraps_single_child(self):
        node = parse_metric_tree({
            "combinator": "warn",
            "children": [{"metric": "nrmse", "variable": "h"}],
        })
        assert isinstance(node, CombinatorSpec)
        assert node.combinator == "warn"
        assert len(node.children) == 1

    def test_warn_with_multiple_children_rejected(self):
        with pytest.raises(MetricSpecError, match="warn.*exactly 1 child"):
            parse_metric_tree({
                "combinator": "warn",
                "children": [
                    {"metric": "nrmse", "variable": "h"},
                    {"metric": "nrmse", "variable": "v"},
                ],
            })

    def test_k_of_n_with_k(self):
        node = parse_metric_tree({
            "combinator": "k-of-n", "k": 2,
            "children": [
                {"metric": "nrmse", "variable": "a"},
                {"metric": "nrmse", "variable": "b"},
                {"metric": "nrmse", "variable": "c"},
            ],
        })
        assert isinstance(node, CombinatorSpec)
        assert node.k == 2

    def test_k_of_n_missing_k_rejected(self):
        with pytest.raises(MetricSpecError, match="'k-of-n'.*requires 'k'"):
            parse_metric_tree({
                "combinator": "k-of-n",
                "children": [{"metric": "nrmse", "variable": "x"}],
            })

    def test_k_of_n_k_exceeding_children_rejected(self):
        with pytest.raises(MetricSpecError, match="k=5 exceeds"):
            parse_metric_tree({
                "combinator": "k-of-n", "k": 5,
                "children": [{"metric": "nrmse", "variable": "x"}],
            })

    def test_k_of_n_negative_k_rejected(self):
        with pytest.raises(MetricSpecError, match="positive integer"):
            parse_metric_tree({
                "combinator": "k-of-n", "k": -1,
                "children": [{"metric": "nrmse", "variable": "x"}],
            })

    def test_unknown_combinator_rejected(self):
        with pytest.raises(MetricSpecError, match="unknown combinator 'xor'"):
            parse_metric_tree({
                "combinator": "xor",
                "children": [{"metric": "nrmse", "variable": "x"}],
            })

    def test_combinator_with_no_children_rejected(self):
        with pytest.raises(MetricSpecError, match="non-empty 'children'"):
            parse_metric_tree({"combinator": "and", "children": []})


# ---------------------------------------------------------------------------
# Nesting + path-bearing errors
# ---------------------------------------------------------------------------

class TestNesting:
    def test_nested_combinators(self):
        node = parse_metric_tree({
            "combinator": "and",
            "children": [
                {"metric": "nrmse", "variable": "h"},
                {
                    "combinator": "or",
                    "children": [
                        {"metric": "tube", "variable": "v", "tube_rel": 0.05},
                        {"metric": "nrmse", "variable": "v", "tolerance": 0.1},
                    ],
                },
            ],
        })
        assert isinstance(node, CombinatorSpec)
        inner = node.children[1]
        assert isinstance(inner, CombinatorSpec)
        assert inner.combinator == "or"
        assert len(inner.children) == 2

    def test_error_path_points_to_offending_child(self):
        with pytest.raises(MetricSpecError, match=r"metrics\.children\[1\]"):
            parse_metric_tree({
                "combinator": "and",
                "children": [
                    {"metric": "nrmse", "variable": "h"},
                    {"metric": "fft", "variable": "v"},  # bad metric here
                ],
            })

    def test_custom_path_prefix_threaded_through(self):
        with pytest.raises(MetricSpecError, match=r"tests\[Foo\]\.metrics\."):
            parse_metric_tree(
                {"metric": "fft", "variable": "x"},
                _path="tests[Foo].metrics",
            )


# ---------------------------------------------------------------------------
# Top-level shape errors
# ---------------------------------------------------------------------------

class TestShape:
    def test_root_must_be_dict(self):
        with pytest.raises(MetricSpecError, match="expected an object"):
            parse_metric_tree([])  # type: ignore[arg-type]

    def test_node_with_both_combinator_and_metric_rejected(self):
        with pytest.raises(MetricSpecError, match="both 'combinator' and 'metric'"):
            parse_metric_tree({
                "combinator": "and",
                "metric": "nrmse",
                "variable": "h",
                "children": [{"metric": "nrmse", "variable": "h"}],
            })

    def test_empty_node_rejected(self):
        with pytest.raises(MetricSpecError, match="must have either"):
            parse_metric_tree({})
