"""Tests for the weighted combinator (4.E)."""

from __future__ import annotations

import pytest

from dstf.comparison.metric_tree import (
    MetricResult,
    WeightedCombinator,
)
from dstf.comparison.tree_spec import (
    MetricSpecError,
    parse_metric_tree,
)


def _leaf(score: float, passed: bool = True, label: str = "x") -> MetricResult:
    return MetricResult(passed=passed, score=score, label=label)


class TestWeightedCombinator:
    def test_passes_when_weighted_sum_under_threshold(self):
        # 0.7 * 0.01 + 0.3 * 0.005 = 0.0085 < 0.01 → pass
        comb = WeightedCombinator(weights=[0.7, 0.3], threshold=0.01)
        result = comb.combine([_leaf(0.01), _leaf(0.005)])
        assert result.passed is True
        assert result.score == pytest.approx(0.0085)
        assert result.diagnostics["direction"] == "less"

    def test_fails_when_weighted_sum_over_threshold(self):
        # 0.7 * 0.02 + 0.3 * 0.02 = 0.02 > 0.01 → fail
        comb = WeightedCombinator(weights=[0.7, 0.3], threshold=0.01)
        result = comb.combine([_leaf(0.02), _leaf(0.02)])
        assert result.passed is False

    def test_direction_greater(self):
        # tube-like: higher score is better. Pass when sum > threshold.
        comb = WeightedCombinator(
            weights=[0.5, 0.5], threshold=0.9, direction="greater"
        )
        # 0.5 * 0.95 + 0.5 * 0.96 = 0.955 > 0.9 → pass
        result = comb.combine([_leaf(0.95), _leaf(0.96)])
        assert result.passed is True

    def test_none_score_fails_with_diagnostic(self):
        comb = WeightedCombinator(weights=[0.5, 0.5], threshold=0.01)
        result = comb.combine([_leaf(0.01), MetricResult(passed=True, score=None)])
        assert result.passed is False
        assert "child has no numeric score" in result.diagnostics["reason"]

    def test_init_validates_direction(self):
        with pytest.raises(ValueError, match="direction must be"):
            WeightedCombinator(weights=[1.0], threshold=0.01, direction="up")

    def test_combine_validates_arity(self):
        comb = WeightedCombinator(weights=[1.0, 1.0], threshold=0.01)
        with pytest.raises(ValueError, match="must match"):
            comb.combine([_leaf(0.01)])  # 1 child, 2 weights


class TestWeightedSpecParsing:
    def test_minimal_weighted_spec(self):
        spec = parse_metric_tree(
            {
                "combinator": "weighted",
                "threshold": 0.01,
                "weights": [0.7, 0.3],
                "children": [
                    {"metric": "nrmse", "variable": "h"},
                    {"metric": "nrmse", "variable": "v"},
                ],
            }
        )
        assert spec.combinator == "weighted"
        assert spec.weights == [0.7, 0.3]
        assert spec.threshold == 0.01
        assert spec.direction == "less"

    def test_explicit_direction(self):
        spec = parse_metric_tree(
            {
                "combinator": "weighted",
                "threshold": 0.9,
                "direction": "greater",
                "weights": [1.0],
                "children": [{"metric": "tube", "variable": "h"}],
            }
        )
        assert spec.direction == "greater"

    def test_missing_weights_raises(self):
        with pytest.raises(MetricSpecError, match="requires a list of weights"):
            parse_metric_tree(
                {
                    "combinator": "weighted",
                    "threshold": 0.01,
                    "children": [{"metric": "nrmse", "variable": "h"}],
                }
            )

    def test_weights_length_mismatch(self):
        with pytest.raises(MetricSpecError, match="length .* must match children"):
            parse_metric_tree(
                {
                    "combinator": "weighted",
                    "threshold": 0.01,
                    "weights": [0.7],  # 1 weight but 2 children
                    "children": [
                        {"metric": "nrmse", "variable": "h"},
                        {"metric": "nrmse", "variable": "v"},
                    ],
                }
            )

    def test_missing_threshold(self):
        with pytest.raises(MetricSpecError, match="requires 'threshold'"):
            parse_metric_tree(
                {
                    "combinator": "weighted",
                    "weights": [1.0],
                    "children": [{"metric": "nrmse", "variable": "h"}],
                }
            )

    def test_invalid_direction(self):
        with pytest.raises(MetricSpecError, match="must be 'less' or 'greater'"):
            parse_metric_tree(
                {
                    "combinator": "weighted",
                    "threshold": 0.01,
                    "direction": "sideways",
                    "weights": [1.0],
                    "children": [{"metric": "nrmse", "variable": "h"}],
                }
            )
