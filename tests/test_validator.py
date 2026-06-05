"""Tests for the D66 baseline-role validator."""

from __future__ import annotations

from dstf.comparison.tree_spec import parse_metric_tree
from dstf.comparison.validator import (
    BaselineRole,
    role_lookup_from_names,
    validate_tree,
)


def _parse(spec: dict):
    return parse_metric_tree(spec)


class TestValidatorRules:
    def test_valid_simple_primary_tree(self):
        tree = _parse(
            {
                "combinator": "and",
                "children": [
                    {"metric": "nrmse", "variable": "x", "tolerance": 1e-3},
                ],
            }
        )
        errors = validate_tree(tree, role_lookup_from_names())
        assert errors == []

    def test_valid_bouncing_ball_shape(self):
        """The ModelicaTestingLib/examples/fmu BouncingBall tree shape."""
        tree = _parse(
            {
                "combinator": "and",
                "children": [
                    {"metric": "nrmse", "variable": "h", "tolerance": 1e-3},
                    {"metric": "nrmse", "variable": "v", "tolerance": 1e-3},
                    {"metric": "range", "variable": "h", "min": -0.01, "max": 1.1},
                    {
                        "combinator": "warn",
                        "children": [
                            {
                                "metric": "nrmse",
                                "variable": "h",
                                "against": "experiment",
                                "tolerance": 0.2,
                            }
                        ],
                    },
                ],
            }
        )
        errors = validate_tree(
            tree,
            role_lookup_from_names(soft_checks=["experiment"]),
        )
        assert errors == []

    def test_tree_without_primary_leaf_is_rejected(self):
        tree = _parse(
            {
                "combinator": "and",
                "children": [
                    {
                        "combinator": "warn",
                        "children": [
                            {
                                "metric": "nrmse",
                                "variable": "h",
                                "against": "experiment",
                                "tolerance": 0.2,
                            }
                        ],
                    },
                ],
            }
        )
        errors = validate_tree(
            tree,
            role_lookup_from_names(soft_checks=["experiment"]),
        )
        assert len(errors) == 1
        assert "regression anchor" in errors[0].message

    def test_soft_check_outside_warn_is_rejected(self):
        tree = _parse(
            {
                "combinator": "and",
                "children": [
                    {"metric": "nrmse", "variable": "x", "tolerance": 1e-3},
                    {
                        "metric": "nrmse",
                        "variable": "x",
                        "against": "experiment",
                        "tolerance": 0.2,
                    },
                ],
            }
        )
        errors = validate_tree(
            tree,
            role_lookup_from_names(soft_checks=["experiment"]),
        )
        assert len(errors) == 1
        assert "soft_check" in errors[0].message
        assert "warn" in errors[0].message

    def test_companion_target_is_rejected(self):
        tree = _parse(
            {
                "combinator": "and",
                "children": [
                    {"metric": "nrmse", "variable": "x", "tolerance": 1e-3},
                    {
                        "combinator": "warn",
                        "children": [
                            {
                                "metric": "nrmse",
                                "variable": "x",
                                "against": "rig-data",
                                "tolerance": 0.2,
                            }
                        ],
                    },
                ],
            }
        )
        errors = validate_tree(
            tree,
            role_lookup_from_names(companions=["rig-data"]),
        )
        assert len(errors) == 1
        assert "companion" in errors[0].message
        assert (
            "plot-only" in errors[0].message or "cannot be scored" in errors[0].message
        )

    def test_unknown_baseline_is_rejected(self):
        tree = _parse(
            {
                "combinator": "and",
                "children": [
                    {"metric": "nrmse", "variable": "x", "tolerance": 1e-3},
                    {
                        "combinator": "warn",
                        "children": [
                            {
                                "metric": "nrmse",
                                "variable": "x",
                                "against": "typo-name",
                                "tolerance": 0.2,
                            }
                        ],
                    },
                ],
            }
        )
        errors = validate_tree(tree, role_lookup_from_names())
        assert len(errors) == 1
        assert "unknown baseline" in errors[0].message
        assert "typo-name" in errors[0].message

    def test_primary_inside_warn_counts_against_tree_level_rule(self):
        """A tree where EVERY primary leaf is warn-wrapped still fails the
        tree-level rule (must have ≥ 1 primary outside warn)."""
        tree = _parse(
            {
                "combinator": "warn",
                "children": [
                    {"metric": "nrmse", "variable": "x", "tolerance": 1e-3},
                ],
            }
        )
        errors = validate_tree(tree, role_lookup_from_names())
        assert len(errors) == 1
        assert "regression anchor" in errors[0].message

    def test_multiple_errors_all_reported(self):
        """Validator reports all errors in one pass (not first-only)."""
        tree = _parse(
            {
                "combinator": "and",
                "children": [
                    {
                        "metric": "nrmse",
                        "variable": "x",
                        "against": "unknown1",
                        "tolerance": 1e-3,
                    },
                    {
                        "metric": "nrmse",
                        "variable": "x",
                        "against": "companion1",
                        "tolerance": 1e-3,
                    },
                ],
            }
        )
        errors = validate_tree(
            tree,
            role_lookup_from_names(companions=["companion1"]),
        )
        # unknown1 + companion1 + missing-primary-anchor = 3 errors
        assert len(errors) == 3

    def test_path_included_in_error(self):
        tree = _parse(
            {
                "combinator": "and",
                "children": [
                    {"metric": "nrmse", "variable": "x", "tolerance": 1e-3},
                    {
                        "metric": "nrmse",
                        "variable": "x",
                        "against": "unknown",
                        "tolerance": 1e-3,
                    },
                ],
            }
        )
        errors = validate_tree(tree, role_lookup_from_names())
        assert any("children[1]" in e.path for e in errors)

    def test_deeply_nested_warn_protects_soft_check(self):
        """A soft_check wrapped in nested warn-in-and-in-warn is still valid."""
        tree = _parse(
            {
                "combinator": "and",
                "children": [
                    {"metric": "nrmse", "variable": "x", "tolerance": 1e-3},
                    {
                        "combinator": "warn",
                        "children": [
                            {
                                "combinator": "and",
                                "children": [
                                    {
                                        "metric": "nrmse",
                                        "variable": "x",
                                        "against": "experiment",
                                        "tolerance": 0.2,
                                    }
                                ],
                            },
                        ],
                    },
                ],
            }
        )
        errors = validate_tree(
            tree,
            role_lookup_from_names(soft_checks=["experiment"]),
        )
        assert errors == []


class TestRoleLookup:
    def test_primary_always_primary(self):
        lookup = role_lookup_from_names()
        assert lookup("primary") is BaselineRole.PRIMARY

    def test_unknown_returns_none(self):
        lookup = role_lookup_from_names()
        assert lookup("bogus") is None

    def test_soft_check_recognized(self):
        lookup = role_lookup_from_names(soft_checks=["experiment"])
        assert lookup("experiment") is BaselineRole.SOFT_CHECK

    def test_companion_recognized(self):
        lookup = role_lookup_from_names(companions=["rig"])
        assert lookup("rig") is BaselineRole.COMPANION
