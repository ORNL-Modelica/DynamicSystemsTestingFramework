"""A criterion's values in effect (the plan's Task 5, K3): the tree view's
leaf carries ``in_effect`` — the test's base tolerance, and where it came
from: what a leaf is scored with while its tolerance field is empty."""

from __future__ import annotations

import json

import pytest

from dstf.authoring.session import Session
from test_session import A, _two_var_project


def _session(tmp_path, *, test_tol=None, metrics=None, ref_tol=None, cfg_tol=None):
    cfg, work = _two_var_project(tmp_path)
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for entry in spec["tests"]:
        if entry["model"] == A:
            if test_tol is not None:
                entry["comparison"] = {"tolerance": test_tol}
            if metrics is not None:
                entry["metrics"] = metrics
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    doc = json.loads(cfg.read_text(encoding="utf-8"))
    if cfg_tol is None:
        doc.pop("tolerance", None)  # the fixture's 0.01: here, 1e-4
    else:
        doc["tolerance"] = cfg_tol
    cfg.write_text(json.dumps(doc), encoding="utf-8")
    if ref_tol is not None:
        for ref in (cfg.parent).rglob("ref_0001.json"):
            data = json.loads(ref.read_text(encoding="utf-8"))
            data["comparison"] = {"tolerance": ref_tol}
            ref.write_text(json.dumps(data), encoding="utf-8")
    s = Session()
    s.open(cfg, work_dir=work)
    return s


def _leaves(view):
    if view.get("kind") == "leaf":
        return [view]
    return [leaf for c in view.get("children") or [] for leaf in _leaves(c)]


def test_a_leaf_without_a_tolerance_shows_the_tests(tmp_path):
    s = _session(tmp_path, test_tol=0.01)
    x, y = _leaves(s.tree_view(A))
    assert x["in_effect"] == {"tolerance": {"value": 0.01, "source": "test"}}


def test_a_leaf_with_its_own_tolerance_carries_the_base_too(tmp_path):
    """The hint names what applies with the field EMPTY: a leaf whose own
    0.5 is cleared in the editor is scored with the base, so the view
    carries the base (not the 0.5 the leaf applies now) for it too."""
    tree = {
        "combinator": "and",
        "children": [
            {"metric": "nrmse", "variable": "x", "tolerance": 0.5},
            {"metric": "nrmse", "variable": "y"},
        ],
    }
    s = _session(tmp_path, test_tol=0.01, metrics=tree)
    x, y = _leaves(s.tree_view(A))
    assert x["in_effect"] == {"tolerance": {"value": 0.01, "source": "test"}}
    assert y["in_effect"]["tolerance"] == {"value": 0.01, "source": "test"}


def test_a_leaf_holding_a_pair_carries_the_base_as_one_holding_a_number(tmp_path):
    """A lower/upper pair is the leaf's own tolerance like a number is: the
    view carries the BASE — one number — for the field left empty, and the
    editor shows no hint while either side holds a value."""
    pair = {"lower": 0.1, "upper": 0.3}
    tree = {
        "combinator": "and",
        "children": [
            {"metric": "points", "variable": "x", "tolerance": pair},
            {"metric": "points", "variable": "y", "tolerance": 0.3},
        ],
    }
    s = _session(tmp_path, test_tol=0.01, metrics=tree)
    x, y = _leaves(s.tree_view(A))
    assert x["params"]["tolerance"] == pair
    assert x["in_effect"] == {"tolerance": {"value": 0.01, "source": "test"}}
    assert x["in_effect"] == y["in_effect"]


@pytest.mark.parametrize(
    ("kw", "value", "source"),
    [
        ({"ref_tol": 0.02}, 0.02, "reference"),
        ({"cfg_tol": 0.03}, 0.03, "project"),
        ({}, 1e-4, "project"),
    ],
)
def test_each_source_is_named(tmp_path, kw, value, source):
    s = _session(tmp_path, **kw)
    x, _ = _leaves(s.tree_view(A))
    assert x["in_effect"]["tolerance"] == {"value": value, "source": source}


def test_a_scalar_with_bounds_and_a_tube_show_no_tolerance(tmp_path):
    tree = {
        "combinator": "and",
        "children": [
            {
                "metric": "scalar",
                "variable": "x",
                "quantity": {"reduce": "max"},
                "min_value": 0,
                "max_value": 5,
            },
            {"metric": "scalar", "variable": "y", "quantity": {"reduce": "max"}},
            {
                "metric": "tube",
                "variable": "x",
                "tube_width_mode": "band",
                "tube_abs": 1.0,
            },
        ],
    }
    s = _session(tmp_path, test_tol=0.01, metrics=tree)
    bounded, scalar, tube = _leaves(s.tree_view(A))
    assert "in_effect" not in bounded and "in_effect" not in tube
    assert scalar["in_effect"]["tolerance"] == {"value": 0.01, "source": "test"}


def test_a_leaf_that_was_not_scored_never_says_zero_in_effect(tmp_path):
    """A leaf that returned before it scored — a variable the result lacks
    — shows the tolerance it WOULD have applied, never the comparison's 0.0
    default. (A leaf against a baseline the test does not have is no longer
    scored at all: the comparison refuses its tree, `broken_roles`.)"""
    tree = {
        "combinator": "and",
        "children": [
            {"metric": "nrmse", "variable": "x"},
            {"metric": "nrmse", "variable": "w_not_in_result"},
        ],
    }
    s = _session(tmp_path, test_tol=0.01, metrics=tree)
    _, missing = _leaves(s.tree_view(A))
    assert missing["in_effect"]["tolerance"] == {"value": 0.01, "source": "test"}


def test_a_tracked_variable_the_baseline_lacks_on_the_flat_path(tmp_path):
    """The flat path's no-baseline comparison says the tolerance too."""
    cfg, work = _two_var_project(tmp_path)
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for entry in spec["tests"]:
        if entry["model"] == A:
            entry["variables"] = ["x", "y", "w"]
            entry["comparison"] = {"tolerance": 0.01}
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    t = [i / 4 for i in range(5)]
    (work / "test_0001" / "result.json").write_text(
        json.dumps(
            {
                "success": True,
                "time": t,
                "variables": {
                    "x": [v * 1.25 for v in t],
                    "y": [2 * v for v in t],
                    "w": t,
                },
            }
        ),
        encoding="utf-8",
    )
    s = Session()
    s.open(cfg, work_dir=work)
    w = next(leaf for leaf in _leaves(s.tree_view(A)) if leaf["variable"] == "w")
    assert w["passed"] is False
    assert w["in_effect"]["tolerance"] == {"value": 0.01, "source": "test"}


def test_the_reports_tree_view_carries_no_values_in_effect(tmp_path):
    """`in_effect` is the tool's (K3): the report's `build_tree_view`, which
    names no source, adds none."""
    from dstf.reporting.context_builder import build_tree_view

    s = _session(tmp_path, test_tol=0.01)
    comp = s.comparisons[A]
    view = build_tree_view(list(comp.variables), s.project.test(A), comp.metric_tree)
    assert all("in_effect" not in leaf for leaf in _leaves(view))


def test_a_stats_path_quantity_on_the_flat_path_says_the_tolerance(tmp_path):
    """The flat path refuses a stats-path quantity (it needs a `metrics`
    tree); its comparison still says the tolerance it would have applied."""
    cfg, work = _two_var_project(tmp_path)
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for entry in spec["tests"]:
        if entry["model"] == A:
            entry["comparison"] = {
                "tolerance": 0.01,
                "variable_overrides": {
                    "x": {"mode": "scalar", "quantity": "CPUtime.final"}
                },
            }
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    s = Session()
    s.open(cfg, work_dir=work)
    x = next(leaf for leaf in _leaves(s.tree_view(A)) if leaf["variable"] == "x")
    assert x["passed"] is False and "stats-path" in x["error"]
    assert x["in_effect"]["tolerance"] == {"value": 0.01, "source": "test"}
