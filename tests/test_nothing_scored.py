"""A test that scored NOTHING is not a PASS.

With no tree a test is scored one criterion per tracked variable its
result holds, and the AND of none is true: a result holding none of them —
`variables` patterns that match nothing in it, a model replaced by an
empty class of the same name, which still simulates — read PASS with no
criterion and no word. It is a FAIL that says so, with or without a
baseline, and no accept stores a baseline for it.

At the comparator, and through the CLI over a real Python-backend project."""

from __future__ import annotations

import json

import numpy as np
import pytest

from conftest import make_test_model
from dstf.backends.types import TestResult, VariableResult
from dstf.comparison.comparator import compare_one, compare_test, nothing_scored
from dstf.comparison.tree_spec import parse_metric_tree
from dstf.reporting.status import FAIL, NO_REF, PASS, classify, refusal
from test_base_tolerance import CSV, RAMP, Project, _case, _ran  # noqa: F401

T = np.linspace(0.0, 1.0, 5)
REFERENCE = {
    "test_id": 3,
    "time": list(T),
    "variables": [{"name": "x", "values": list(T)}],
}

NONE_OF = "the result holds none of the variables this test tracks"
NOTHING = " — nothing was scored"


def _result(*names: str) -> TestResult:
    return TestResult(
        "Lib.T",
        True,
        variables=[VariableResult(i + 1, T, T, name=n) for i, n in enumerate(names)],
    )


class _Store:
    def __init__(self, reference):
        self.reference = reference

    def get_reference(self, model_id):
        return self.reference

    def get_soft_checks(self, model_id):
        return {}

    def reference_path(self, model_id):
        return None


ROUTES = {
    "the result holds no variable": (dict(variable_patterns=["x"]), (), "(x)"),
    "the result holds another": (dict(variable_patterns=["x"]), ("y",), "(x)"),
    "a pattern matches nothing": (dict(variable_patterns=["q*"]), ("x",), "(q*)"),
    "its UnitTests variables are gone": (
        dict(source="unit_tests", n_vars=2),
        ("x",),
        "(unitTests.x[1], unitTests.x[2])",
    ),
    "more tracked than are named": (
        dict(variable_patterns=["a", "b", "c", "d", "e", "f"]),
        ("x",),
        "(a, b, c, d and 2 more)",
    ),
}


@pytest.mark.parametrize("route", list(ROUTES))
@pytest.mark.parametrize("baseline", [True, False], ids=["baseline", "no baseline"])
def test_a_result_holding_nothing_the_test_tracks_fails_and_says_so(route, baseline):
    fields, names, tracked = ROUTES[route]
    test = make_test_model(**fields)
    comp = compare_one(test, _result(*names), _Store(REFERENCE if baseline else None))
    assert classify(comp) == FAIL and comp.passed is False
    assert comp.has_reference is baseline and comp.sim_success is True
    # The sentence stands where a failing criterion would be named.
    assert refusal(comp) == f"{NONE_OF} {tracked}{NOTHING}"
    assert comp.variables == [] and comp.metric_tree is None
    # A direct caller of compare_test is told the same.
    direct = compare_test(test, _result(*names), REFERENCE if baseline else {})
    assert (direct.passed, direct.error_message) == (False, comp.error_message)


def test_a_test_that_tracks_nothing_and_has_no_criteria_says_that():
    comp = compare_one(make_test_model(), _result("x"), _Store(REFERENCE))
    assert classify(comp) == FAIL
    assert refusal(comp) == (
        "this test tracks no variable and has no criteria — nothing was scored"
    )


def test_one_tracked_variable_in_the_result_is_scored_as_before():
    test = make_test_model(variable_patterns=["x", "gone"])
    comp = compare_one(test, _result("x"), _Store(REFERENCE))
    assert classify(comp) == PASS and [v.name for v in comp.variables] == ["x"]
    assert nothing_scored(test, _result("x")) is None
    # ...and with no baseline it is NO_REF, as any test is.
    assert classify(compare_one(test, _result("x"), _Store(None))) == NO_REF


def test_a_simulate_only_test_still_passes_by_having_run():
    test = make_test_model(simulate_only=True)
    for reference in (REFERENCE, None):
        comp = compare_one(test, _result(), _Store(reference))
        assert classify(comp) == PASS and comp.simulate_only
    assert nothing_scored(test, _result()) is None


def test_a_tree_is_not_asked_each_leaf_fails_by_itself():
    tree = parse_metric_tree({"metric": "nrmse", "variable": "x"})
    test = make_test_model(variable_patterns=["x"], metric_tree_spec=tree)
    assert nothing_scored(test, _result()) is None
    comp = compare_one(test, _result(), _Store(REFERENCE))
    assert classify(comp) == FAIL and refusal(comp) is None
    (leaf,) = comp.variables
    assert leaf.diagnostics["error"] == "variable 'x' not found in the result"


def test_a_failed_simulation_is_not_asked():
    failed = TestResult("Lib.T", False, error_message="boom")
    assert nothing_scored(make_test_model(variable_patterns=["x"]), failed) is None


# ---------------------------------------------------------------------------
# Through the CLI
# ---------------------------------------------------------------------------


@pytest.fixture
def project(_ran):  # noqa: F811
    _ran.reset()
    yield _ran
    _ran.reset()


def _track(project: Project, model_id: str, *patterns: str) -> None:
    data = json.loads(project.spec.read_text(encoding="utf-8"))
    (entry,) = [e for e in data["tests"] if e["model"] == model_id]
    entry["variables"] = list(patterns)
    entry.pop("comparison", None)
    project.spec.write_text(json.dumps(data, indent=2), encoding="utf-8")


@pytest.mark.parametrize("baseline", [True, False], ids=["baseline", "no baseline"])
def test_compare_fails_a_test_whose_patterns_match_nothing_in_its_result(
    project, tmp_path, capsys, baseline
):
    _track(project, RAMP, "nope*")
    if not baseline:
        project.reference(RAMP).unlink()
    rc, cases = project.junit(tmp_path)
    assert rc == 1
    assert _case(cases, RAMP) == ("failure", f"{NONE_OF} (nope*){NOTHING}")
    assert _case(cases, CSV) == ("pass", None)
    capsys.readouterr()
    assert project.dstf("compare") == 1
    out = capsys.readouterr().out
    assert f"{NONE_OF} (nope*){NOTHING}" in out
    verdicts = json.loads(
        (project.config.work_dir / "verdicts.json").read_text(encoding="utf-8")
    )
    (ramp,) = [t for t in verdicts["tests"] if t["model_id"] == RAMP]
    assert ramp["status"] == FAIL


@pytest.mark.parametrize("command", ["compare", "run"])
def test_accept_stores_no_baseline_of_a_result_that_holds_nothing_tracked(
    project, capsys, command
):
    _track(project, RAMP, "nope*")
    before = project.reference(RAMP).read_bytes()
    capsys.readouterr()
    assert project.dstf(command, "--accept") == 1
    out = capsys.readouterr().out
    assert f"Skipped 1 test(s):\n  {RAMP}: {NONE_OF} (nope*){NOTHING}" in out
    assert project.reference(RAMP).read_bytes() == before
