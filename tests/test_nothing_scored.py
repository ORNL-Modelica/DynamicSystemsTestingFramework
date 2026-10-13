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
from dstf.reporting.status import FAIL, NO_REF, PASS, SIM_FAIL, classify, refusal
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
    # The baseline's id: a refused test with one is `[0003]` on the console
    # and `ref_0003` on the dashboard, as any other test.
    assert comp.test_id == (3 if baseline else None)
    # The sentence stands where a failing criterion would be named.
    assert refusal(comp) == f"{NONE_OF} {tracked}{NOTHING}"
    assert comp.variables == [] and comp.metric_tree is None
    # A direct caller of compare_test is told the same.
    direct = compare_test(test, _result(*names), REFERENCE if baseline else {})
    assert (direct.passed, direct.error_message) == (False, comp.error_message)


#: What a test can declare to score, one at a time.
DECLARES = {
    "a pattern": dict(variable_patterns=["x"]),
    "a UnitTests variable": dict(source="unit_tests", n_vars=1),
    "a criterion": dict(
        metric_tree_spec=parse_metric_tree({"metric": "nrmse", "variable": "x"})
    ),
}


@pytest.mark.parametrize("baseline", [True, False], ids=["baseline", "no baseline"])
def test_a_test_that_declares_nothing_to_score_is_simulate_only(baseline):
    """No tracked variable, no UnitTests variable, no criteria — the entry
    `dstf add <model>` writes: it passes by having run, as a test a
    recognizer marked `simulate_only` does. Not the FAIL of a test that
    tracks variables its result does not hold."""
    test = make_test_model()
    assert test.simulate_only is False and test.is_simulate_only is True
    assert nothing_scored(test, _result("x")) is None
    comp = compare_one(test, _result("x"), _Store(REFERENCE if baseline else None))
    assert classify(comp) == PASS and comp.simulate_only and refusal(comp) is None
    assert comp.metric_tree.label == "simulate-only" and comp.variables == []
    # ...iff its simulation completes.
    failed = TestResult("Lib.T", False, error_message="boom")
    assert classify(compare_one(test, failed, _Store(None))) == SIM_FAIL


@pytest.mark.parametrize("what", list(DECLARES))
def test_a_test_that_declares_one_thing_to_score_is_not_simulate_only(what):
    test = make_test_model(**DECLARES[what])
    assert test.is_simulate_only is False
    # A UnitTests component of a test the spec alone declares names nothing.
    assert make_test_model(source="spec", n_vars=1).is_simulate_only is True
    # The recognizer's mark wins over anything declared.
    assert make_test_model(simulate_only=True, **DECLARES[what]).is_simulate_only


def test_one_tracked_variable_in_the_result_is_scored_as_before():
    test = make_test_model(variable_patterns=["x", "gone"])
    comp = compare_one(test, _result("x"), _Store(REFERENCE))
    assert classify(comp) == PASS and [v.name for v in comp.variables] == ["x"]
    assert nothing_scored(test, _result("x")) is None
    # ...and with no baseline it is NO_REF, as any test is.
    assert classify(compare_one(test, _result("x"), _Store(None))) == NO_REF


# ---------------------------------------------------------------------------
# A variable the baseline holds and the run lost
# ---------------------------------------------------------------------------

#: A baseline of two variables; `y` carries an expression.
BOTH = {
    "test_id": 3,
    "time": list(T),
    "variables": [
        {"name": "x", "expression": "", "values": list(T)},
        {"name": "y", "expression": "a.Q", "values": list(T)},
    ],
}


def _lost(name: str) -> str:
    return f"variable {name!r} not found in the result"


#: What a flat test tracks -> (its fields, what the baseline holds, what the
#: run holds, the variables it lost).
LOSSES = {
    "a literal pattern": (dict(variable_patterns=["x", "y"]), "x y", "x", ["y"]),
    "every variable": (dict(variable_patterns=["*"]), "x y", "x", ["y"]),
    "a glob that matched it": (
        dict(variable_patterns=["T[*]"]),
        "T[1] T[2] T[3]",
        "T[2]",
        ["T[1]", "T[3]"],
    ),
    "a UnitTests variable": (
        dict(source="unit_tests", n_vars=2),
        "unitTests.x[1] unitTests.x[2]",
        "unitTests.x[1]",
        ["unitTests.x[2]"],
    ),
}


@pytest.mark.parametrize("what", list(LOSSES))
def test_a_tracked_variable_the_baseline_holds_and_the_run_lost_fails(what):
    """With no tree the run was scored on the variables it still held: `x`
    of {x, y} read PASS, Criteria 1/1, no word — and an accept then wrote
    a baseline of `x` alone. A tree fails that loss; so does the flat
    path, with the tree's sentence, as a criterion that was not scored."""
    fields, held, ran, lost = LOSSES[what]
    reference = {
        "test_id": 3,
        "time": list(T),
        "variables": [{"name": n, "values": list(T)} for n in held.split()],
    }
    test = make_test_model(**fields)
    comp = compare_one(test, _result(*ran.split()), _Store(reference))
    assert classify(comp) == FAIL and refusal(comp) is None
    scored = [v for v in comp.variables if v.passed]
    assert [v.name for v in scored] == ran.split()
    failed = [v for v in comp.variables if not v.passed]
    assert [v.name for v in failed] == lost
    assert [v.diagnostics["error"] for v in failed] == [_lost(n) for n in lost]
    assert comp.metric_tree is not None and comp.metric_tree.passed is False


def test_the_lost_criterion_is_the_one_a_tree_would_report():
    flat = make_test_model(variable_patterns=["x", "y"])
    tree = make_test_model(
        variable_patterns=["x", "y"],
        metric_tree_spec=parse_metric_tree(
            {
                "combinator": "and",
                "children": [
                    {"metric": "nrmse", "variable": "x"},
                    {"metric": "nrmse", "variable": "y"},
                ],
            }
        ),
    )
    by_flat = compare_one(flat, _result("x"), _Store(BOTH))
    by_tree = compare_one(tree, _result("x"), _Store(BOTH))

    def said(comp):
        return [
            (v.name, v.passed, v.mode, v.against, (v.diagnostics or {}).get("error"))
            for v in comp.variables
        ]

    assert said(by_flat) == said(by_tree)
    assert said(by_flat)[1] == ("y", False, "nrmse", "primary", _lost("y"))
    # The label a person reads is the baseline's: the run has none to give.
    assert by_flat.variables[1].expression == "a.Q"
    assert by_flat.variables[1].tolerance_used == by_flat.variables[0].tolerance_used


def test_a_lost_variable_keeps_its_overrides_metric_and_tolerance():
    test = make_test_model(
        variable_patterns=["x", "y"],
        variable_overrides={
            "y": {"mode": "tube", "tube_width_mode": "rel", "tube_rel": 0.1}
        },
        comparison_tolerance=0.05,
    )
    comp = compare_one(test, _result("x"), _Store(BOTH))
    (y,) = [v for v in comp.variables if v.name == "y"]
    assert (y.mode, y.against, y.passed) == ("tube", "primary", False)
    assert y.tolerance_used.lower == y.tolerance_used.upper == 0.05


@pytest.mark.parametrize(
    "fields",
    [
        # A glob that matches nothing anywhere stays a log line.
        dict(variable_patterns=["x", "q*"]),
        # ...and so does a name neither side holds.
        dict(variable_patterns=["x", "gone"]),
        # `y` is in the baseline and no longer tracked: the test does not ask.
        dict(variable_patterns=["x"]),
        dict(source="unit_tests", n_vars=0, variable_patterns=["x"]),
    ],
    ids=["an empty glob", "a name nowhere", "no longer tracked", "no UnitTests"],
)
def test_what_the_test_does_not_track_or_no_side_holds_is_not_a_loss(fields):
    comp = compare_one(make_test_model(**fields), _result("x"), _Store(BOTH))
    assert classify(comp) == PASS and [v.name for v in comp.variables] == ["x"]


def test_a_loss_with_no_baseline_is_no_ref_as_before():
    test = make_test_model(variable_patterns=["x", "y"])
    assert classify(compare_one(test, _result("x"), _Store(None))) == NO_REF


def test_a_warn_over_a_lost_variable_is_an_advisory_failure():
    """A tree that is only a `warn`: the test passes, and the criterion
    that was not scored is counted where advisory failures are."""
    from dstf.reporting.status import advisory_failures

    tree = parse_metric_tree(
        {"combinator": "warn", "children": [{"metric": "nrmse", "variable": "y"}]}
    )
    test = make_test_model(variable_patterns=["y"], metric_tree_spec=tree)
    comp = compare_one(test, _result("x"), _Store(BOTH))
    assert classify(comp) == PASS
    (leaf,) = advisory_failures(comp)
    assert leaf.advisory and leaf.reason == _lost("y")


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


def test_an_entry_with_no_variables_passes_by_having_run(project, tmp_path, capsys):
    """`dstf add <model>` writes `"variables": []`: a simulate-only test.
    It read FAIL `this test tracks no variable and has no criteria` on
    every run."""
    _track(project, RAMP)
    rc, cases = project.junit(tmp_path)
    assert rc == 0 and _case(cases, RAMP) == ("pass", None)
    capsys.readouterr()
    assert project.dstf("compare") == 0
    out = capsys.readouterr().out
    assert "nothing was scored" not in out and "Results: 3/3 passed" in out, out
    verdicts = json.loads(
        (project.config.work_dir / "verdicts.json").read_text(encoding="utf-8")
    )
    (ramp,) = [t for t in verdicts["tests"] if t["model_id"] == RAMP]
    assert ramp["status"] == PASS


@pytest.mark.parametrize("command", ["compare", "run"])
def test_accept_neither_stores_nor_skips_a_test_that_declares_nothing(
    project, capsys, command
):
    _track(project, RAMP)
    before = project.reference(RAMP).read_bytes()
    capsys.readouterr()
    assert project.dstf(command, "--accept") == 0
    out = capsys.readouterr().out
    assert "Skipped" not in out, out
    assert project.reference(RAMP).read_bytes() == before


def _hold_y_too(project: Project) -> None:
    """RAMP tracks `x` and `y`, and its stored baseline holds both: the
    run, which writes `x` alone, has lost `y`."""
    _track(project, RAMP, "x", "y")
    path = project.reference(RAMP)
    data = json.loads(path.read_text(encoding="utf-8"))
    (x,) = data["variables"]
    data["variables"].append({**x, "index": 2, "name": "y"})
    data["n_vars"] = 2
    path.write_text(json.dumps(data), encoding="utf-8")


def test_compare_fails_a_test_whose_run_lost_a_baseline_variable(
    project, tmp_path, capsys
):
    _hold_y_too(project)
    rc, cases = project.junit(tmp_path)
    assert rc == 1
    assert _case(cases, RAMP) == ("failure", "1 criterion failed")
    assert "variable 'y' not found in the result" in (tmp_path / "junit.xml").read_text(
        encoding="utf-8"
    )
    capsys.readouterr()
    assert project.dstf("compare") == 1
    out = capsys.readouterr().out
    assert "Not scored:" in out and "variable 'y' not found in the result" in out, out
    assert "Results: 2/3 passed" in out, out


@pytest.mark.parametrize("command", ["compare", "run"])
def test_accept_says_which_variables_the_new_baseline_no_longer_holds(
    project, capsys, command
):
    """Not refused: accepting is how a variable is dropped on purpose. But
    the baseline was rewritten without it, and nothing said so."""
    _hold_y_too(project)
    name = project.reference(RAMP).name
    capsys.readouterr()
    assert project.dstf(command, "--accept") == 0
    out = capsys.readouterr().out
    assert (
        f"Warning: {name} ({RAMP}) no longer holds y — the accepted result "
        "does not hold it."
    ) in out, out
    assert out.count("no longer holds") == 1  # the two other tests lost nothing
    stored = json.loads(project.reference(RAMP).read_text(encoding="utf-8"))
    assert [v["name"] for v in stored["variables"]] == ["x"]
    # Accepted again: there is nothing more to lose, and nothing is said.
    capsys.readouterr()
    assert project.dstf("compare", "--accept") == 0
    assert "no longer holds" not in capsys.readouterr().out
