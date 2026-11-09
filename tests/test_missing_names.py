"""An exact name ``variables`` lists that a completed result does not hold
fails the test, and no accept stores a baseline for it (#28): a name typed
``a[1, 1]`` against a result that writes ``a[1,1]`` scored nothing about
that variable and read PASS. A glob that matches nothing is a warning in
the verdict — globs are written to match what there is.

At the comparator, and through the CLI over a real Python-backend project."""

from __future__ import annotations

import json

import numpy as np
import pytest

from conftest import make_test_model
from dstf.backends.types import TestResult, VariableResult
from dstf.comparison.comparator import compare_one, missing_names
from dstf.comparison.tree_spec import parse_metric_tree
from dstf.reporting.status import FAIL, PASS, SIM_FAIL, classify, refusal
from dstf.storage.baseline_codec import decode_reference
from test_base_tolerance import CSV, RAMP, Project, _ran  # noqa: F401

T = np.linspace(0.0, 1.0, 5)
REFERENCE = {
    "test_id": 3,
    "time": list(T),
    "variables": [{"name": "x", "values": list(T)}],
}
EMPTY_GLOB = "variables: pattern 'q*' matches no variable in the result"


def _lacks(names: str) -> str:
    return (
        f"the result does not hold {names} — a name \"variables\" lists must be "
        "in the result; nothing was scored"
    )


def _result(*names: str) -> TestResult:
    return TestResult(
        "Lib.T",
        True,
        variables=[VariableResult(i + 1, T, T, name=n) for i, n in enumerate(names)],
    )


class _Store:
    def __init__(self, reference):
        self.reference = None if reference is None else decode_reference(reference)

    def get_reference(self, model_id):
        return self.reference

    folder = "Stub/linux"

    def get_soft_checks(self, model_id):
        return {}

    def get_companions(self, model_id):
        return {}

    def reference_path(self, model_id):
        return None


TREE = parse_metric_tree(
    {"combinator": "and", "children": [{"metric": "nrmse", "variable": "x"}]}
)


@pytest.mark.parametrize("tree", [None, TREE], ids=["flat", "tree"])
@pytest.mark.parametrize("baseline", [True, False], ids=["baseline", "no baseline"])
def test_an_exact_name_the_result_lacks_fails_the_test_and_says_which(tree, baseline):
    test = make_test_model(
        variable_patterns=["x", "a[1,1]", "x*", "b"], metric_tree_spec=tree
    )
    comp = compare_one(test, _result("x"), _Store(REFERENCE if baseline else None))
    assert classify(comp) == FAIL and comp.passed is False
    assert comp.has_reference is baseline and comp.sim_success is True
    assert refusal(comp) == _lacks("a[1,1], b")
    assert comp.variables == [] and comp.metric_tree is None


def test_a_result_holding_none_of_them_says_so_first():
    test = make_test_model(variable_patterns=["x", "y"])
    assert missing_names(test, _result()) is None
    comp = compare_one(test, _result(), _Store(REFERENCE))
    assert refusal(comp) == (
        "the result holds none of the variables this test tracks (x, y) — "
        "nothing was scored"
    )


def test_a_simulate_only_test_and_a_failed_simulation_are_not_asked():
    marked = make_test_model(simulate_only=True, variable_patterns=["gone"])
    assert classify(compare_one(marked, _result("x"), _Store(REFERENCE))) == PASS
    failed = TestResult("Lib.T", False, error_message="boom")
    tracking = make_test_model(variable_patterns=["gone"])
    assert missing_names(tracking, failed) is None
    assert classify(compare_one(tracking, failed, _Store(REFERENCE))) == SIM_FAIL


@pytest.mark.parametrize("tree", [None, TREE], ids=["flat", "tree"])
def test_a_glob_that_matches_nothing_passes_with_a_warning(tree):
    test = make_test_model(variable_patterns=["x", "q*"], metric_tree_spec=tree)
    comp = compare_one(test, _result("x"), _Store(REFERENCE))
    assert classify(comp) == PASS
    assert [w.text for w in comp.warnings] == [EMPTY_GLOB]


def test_a_glob_that_matches_something_says_nothing():
    test = make_test_model(variable_patterns=["x*"])
    comp = compare_one(test, _result("x"), _Store(REFERENCE))
    assert classify(comp) == PASS and comp.warnings == []


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


def _verdict(project: Project, model_id: str) -> dict:
    verdicts = json.loads(
        (project.config.work_dir / "verdicts.json").read_text(encoding="utf-8")
    )
    (found,) = [t for t in verdicts["tests"] if t["model_id"] == model_id]
    return found


def test_compare_fails_a_test_whose_result_lacks_a_listed_name(project, capsys):
    _track(project, RAMP, "x", "x[1, 1]")
    capsys.readouterr()
    assert project.dstf("compare") == 1
    out = capsys.readouterr().out
    assert _lacks("x[1,1]") in out
    assert _verdict(project, RAMP)["status"] == FAIL
    assert _verdict(project, CSV)["status"] == PASS


def test_accept_skips_it_and_writes_no_baseline(project, capsys):
    _track(project, RAMP, "x", "gone")
    project.reference(RAMP).unlink()
    refs = sorted(p.name for p in project.ref_root.rglob("ref_*.json"))
    capsys.readouterr()
    assert project.dstf("compare", "--accept") == 1
    out = capsys.readouterr().out
    assert f"Skipped 1 test(s):\n  {RAMP}: {_lacks('gone')}" in out
    assert sorted(p.name for p in project.ref_root.rglob("ref_*.json")) == refs
    assert project.reference(RAMP) is None


def test_compare_passes_a_glob_that_matches_nothing_and_warns(project, capsys):
    _track(project, RAMP, "x", "q*")
    capsys.readouterr()
    assert project.dstf("compare") == 0
    out = capsys.readouterr().out
    assert f"    {EMPTY_GLOB}\n" in out
    ramp = _verdict(project, RAMP)
    assert ramp["status"] == PASS and ramp["warnings"] == [EMPTY_GLOB]
