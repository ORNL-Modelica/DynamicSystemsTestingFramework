"""Tests for metric_tree: combinators + implicit-tree construction."""

import pytest

from dstf.comparison.comparator import VariableComparison
from dstf.comparison.metric_tree import (
    AndCombinator,
    KOfNCombinator,
    MetricResult,
    OrCombinator,
    WarnCombinator,
    implicit_and_tree,
    leaf_from_variable,
)


def _vc(name="x", passed=True, nrmse=0.001, mode="nrmse", tube_inside=None):
    """Shortcut for building a test VariableComparison."""
    return VariableComparison(
        index=1,
        name=name,
        passed=passed,
        nrmse=nrmse,
        rmse=nrmse,
        signal_range=1.0,
        max_abs_error=nrmse,
        max_abs_error_time=0.0,
        reference_final=0.0,
        actual_final=0.0,
        tolerance_used=0.01,
        mode=mode,
        tube_points_inside=tube_inside,
    )


# ---------------------------------------------------------------------------
# Leaf adapter
# ---------------------------------------------------------------------------


class TestLeafFromVariable:
    def test_nrmse_leaf_uses_nrmse_score(self):
        vc = _vc(name="pipe.T", passed=True, nrmse=0.002)
        leaf = leaf_from_variable(vc)
        assert leaf.passed
        assert leaf.score == pytest.approx(0.002)
        assert leaf.label == "pipe.T"
        assert leaf.diagnostics["mode"] == "nrmse"
        assert leaf.diagnostics["variable"] is vc
        assert leaf.children == []

    def test_tube_leaf_uses_points_inside_score(self):
        vc = _vc(name="tank.h", passed=True, mode="tube", tube_inside=0.98)
        leaf = leaf_from_variable(vc)
        assert leaf.score == pytest.approx(0.98)
        assert leaf.diagnostics["mode"] == "tube"

    def test_tube_leaf_without_inside_falls_back_to_nrmse(self):
        vc = _vc(mode="tube", tube_inside=None, nrmse=0.5)
        leaf = leaf_from_variable(vc)
        assert leaf.score == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# AND / OR
# ---------------------------------------------------------------------------


class TestAndCombinator:
    def test_all_pass_passes(self):
        children = [
            MetricResult(passed=True, score=0.01, label="a"),
            MetricResult(passed=True, score=0.02, label="b"),
        ]
        r = AndCombinator().combine(children)
        assert r.passed
        assert r.score == 0.01  # min (worst)
        assert r.diagnostics["n_failed"] == 0
        assert len(r.children) == 2

    def test_one_failing_fails(self):
        children = [
            MetricResult(passed=True, score=0.01, label="a"),
            MetricResult(passed=False, score=0.5, label="b"),
        ]
        r = AndCombinator().combine(children)
        assert not r.passed
        assert r.diagnostics["n_failed"] == 1

    def test_empty_is_vacuously_true(self):
        r = AndCombinator().combine([])
        assert r.passed
        assert r.score is None


class TestOrCombinator:
    def test_any_pass_passes(self):
        children = [
            MetricResult(passed=False, score=0.5, label="a"),
            MetricResult(passed=True, score=0.01, label="b"),
        ]
        r = OrCombinator().combine(children)
        assert r.passed
        assert r.diagnostics["n_passed"] == 1

    def test_all_fail_fails(self):
        children = [
            MetricResult(passed=False, score=0.3, label="a"),
            MetricResult(passed=False, score=0.5, label="b"),
        ]
        r = OrCombinator().combine(children)
        assert not r.passed
        assert r.score == 0.5  # max (best)

    def test_empty_is_false(self):
        r = OrCombinator().combine([])
        assert not r.passed


# ---------------------------------------------------------------------------
# K-of-N
# ---------------------------------------------------------------------------


class TestKOfNCombinator:
    def test_meets_threshold_passes(self):
        children = [
            MetricResult(passed=True, score=0.01, label="a"),
            MetricResult(passed=True, score=0.02, label="b"),
            MetricResult(passed=False, score=0.5, label="c"),
        ]
        r = KOfNCombinator(k=2).combine(children)
        assert r.passed
        assert r.diagnostics == {"k": 2, "n": 3, "n_passed": 2}

    def test_below_threshold_fails(self):
        children = [
            MetricResult(passed=True, score=0.01, label="a"),
            MetricResult(passed=False, score=0.5, label="b"),
        ]
        r = KOfNCombinator(k=2).combine(children)
        assert not r.passed

    def test_negative_k_rejected(self):
        with pytest.raises(ValueError):
            KOfNCombinator(k=-1)


# ---------------------------------------------------------------------------
# warn
# ---------------------------------------------------------------------------


class TestWarnCombinator:
    def test_passes_even_when_child_fails(self):
        child = MetricResult(passed=False, score=0.5, label="experiment-rmse")
        r = WarnCombinator().combine([child])
        assert r.passed
        assert r.score is None
        assert r.diagnostics["warned"]

    def test_no_warning_when_child_passes(self):
        child = MetricResult(passed=True, score=0.01, label="experiment-rmse")
        r = WarnCombinator().combine([child])
        assert r.passed
        assert not r.diagnostics["warned"]

    def test_requires_exactly_one_child(self):
        with pytest.raises(ValueError):
            WarnCombinator().combine([])
        with pytest.raises(ValueError):
            WarnCombinator().combine(
                [
                    MetricResult(passed=True, score=None, label="a"),
                    MetricResult(passed=True, score=None, label="b"),
                ]
            )


# ---------------------------------------------------------------------------
# Implicit tree (degenerate flat AND, matches current pass/fail semantics)
# ---------------------------------------------------------------------------


class TestImplicitAndTree:
    def test_all_pass(self):
        vcs = [_vc("a", passed=True, nrmse=0.001), _vc("b", passed=True, nrmse=0.002)]
        r = implicit_and_tree(vcs)
        assert r.passed
        assert r.score == pytest.approx(0.001)  # min of children's nrmse
        assert len(r.children) == 2

    def test_one_fails_tree_fails(self):
        vcs = [_vc("a", passed=True, nrmse=0.001), _vc("b", passed=False, nrmse=0.5)]
        r = implicit_and_tree(vcs)
        assert not r.passed
        assert r.diagnostics["n_failed"] == 1

    def test_empty_is_vacuously_true(self):
        """No variables = no failures. Matches current TestComparison.passed."""
        r = implicit_and_tree([])
        assert r.passed
