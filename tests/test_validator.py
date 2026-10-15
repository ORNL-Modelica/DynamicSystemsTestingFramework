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
                    {
                        "metric": "range",
                        "variable": "h",
                        "min_value": -0.01,
                        "max_value": 1.1,
                    },
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
        assert "a tree of warnings alone can never fail" in errors[0].message

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
        assert errors[0].message == (
            "This criterion scores against 'experiment' outside a 'warn', where "
            "only the primary baseline is scored: a soft check sits under a "
            "'warn', and a companion is only drawn."
        )

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
        assert "companions are only drawn" in errors[0].message

    def test_unknown_baseline_under_warn_is_written_and_named(self):
        """A name this folder does not hold is a soft check of another
        folder: no error where the file is written, and named by
        `not_held_here` for the writer to say."""
        from dstf.comparison.validator import not_held_here

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
        lookup = role_lookup_from_names()
        assert validate_tree(tree, lookup) == []
        assert not_held_here(tree, lookup) == ["typo-name"]

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
        assert "a tree of warnings alone can never fail" in errors[0].message

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


class TestWeightedDirectionConsistency:
    """`weighted` sums child scores against ONE threshold with ONE
    direction — mixing lower-is-better (nrmse) with higher-is-better
    (tube fraction) children is meaningless and must be rejected at
    validation time (D101; the registry's score_direction is the
    authority)."""

    def _tree(self, children):
        from dstf.comparison.tree_spec import parse_metric_tree

        return parse_metric_tree(
            {
                "combinator": "weighted",
                "weights": [1.0] * len(children),
                "threshold": 0.5,
                "children": children,
            }
        )

    def test_mixed_directions_rejected(self):
        tree = self._tree(
            [
                {"metric": "nrmse", "variable": "x", "tolerance": 1e-3},
                {"metric": "tube", "variable": "y", "tube_width_mode": "rel"},
            ]
        )
        errors = validate_tree(tree, role_lookup_from_names())
        msgs = [e.message for e in errors]
        assert any("cannot mix lower-is-better" in m for m in msgs)
        assert any("nrmse" in m and "tube" in m for m in msgs)

    def test_uniform_directions_clean(self):
        tree = self._tree(
            [
                {"metric": "nrmse", "variable": "x", "tolerance": 1e-3},
                {"metric": "points", "variable": "y"},
            ]
        )
        errors = validate_tree(tree, role_lookup_from_names())
        assert not any("cannot mix lower-is-better" in e.message for e in errors)

    def test_combinator_children_not_direction_checked(self):
        """A combinator child's aggregate direction is ill-defined; the
        eval layer already fails score-less children (warn) — only leaf
        children participate in the direction check."""
        tree = self._tree(
            [
                {"metric": "nrmse", "variable": "x", "tolerance": 1e-3},
                {
                    "combinator": "and",
                    "children": [
                        {"metric": "tube", "variable": "y", "tube_width_mode": "rel"}
                    ],
                },
            ]
        )
        errors = validate_tree(tree, role_lookup_from_names())
        assert not any("cannot mix lower-is-better" in e.message for e in errors)


def test_the_no_anchor_error_is_a_sentence_and_keeps_its_path():
    """The tool shows ``message`` alone, beside the row it came from; the
    CLI prints ``str()``, which keeps the location."""
    tree = _parse(
        {"combinator": "warn", "children": [{"metric": "nrmse", "variable": "x"}]}
    )
    (err,) = validate_tree(tree, role_lookup_from_names())
    assert err.message == (
        "Every tree needs at least one criterion scored against the primary "
        "baseline outside a 'warn': a tree of warnings alone can never fail."
    )
    assert str(err) == f"metrics: {err.message}"
