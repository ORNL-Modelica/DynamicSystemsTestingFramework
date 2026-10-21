"""Tests for CLI utilities (review filtering, argument parsing)."""

import argparse
import json
import logging
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from dstf.backends import _REGISTRY, Backend, register
from dstf.backends.locate import EXECUTABLE, SEARCH, Found, Located, Rule
from dstf.backends.types import (
    TestResult,
    VariableResult,
)
from dstf.cli import main
from dstf.cli.common import (
    _filter_tests,
    _parse_review_filter,
    _resolve_filter_patterns,
    _should_review,
)
from dstf.cli.main import build_arg_parser
from dstf.cli.review import _interactive_review
from dstf.cli.run_cmds import cmd_compare, cmd_run
from dstf.comparison.types import TestComparison
from dstf.config import Config, detect_os
from dstf.discovery.test_registry import TestModel
from dstf.pipeline import write_id_mapping
from dstf.storage.baseline_codec import decode_reference
from dstf.storage.batch_manifest import BatchManifest
from dstf.storage.reference_store import ReferenceStore


def _comparison(**fields) -> TestComparison:
    """A passing comparison with ``fields`` over it, for filter testing."""
    return TestComparison(**{"model_id": "Test.Model", "passed": True, **fields})


# --- _parse_review_filter ---


def test_parse_filter_all():
    assert _parse_review_filter("all") == {"all"}


def test_parse_filter_single():
    assert _parse_review_filter("failed") == {"failed"}


def test_parse_filter_multiple():
    result = _parse_review_filter("failed,no-baseline,warnings")
    assert result == {"failed", "no-baseline", "warnings"}


def test_parse_filter_all_absorbs_others():
    assert _parse_review_filter("all,failed") == {"all"}


def test_parse_filter_strips_whitespace():
    result = _parse_review_filter(" failed , no-baseline ")
    assert result == {"failed", "no-baseline"}


def test_parse_filter_invalid_raises():
    with pytest.raises(argparse.ArgumentTypeError, match="Invalid review filter"):
        _parse_review_filter("bogus")


def test_parse_filter_partial_invalid_raises():
    with pytest.raises(argparse.ArgumentTypeError, match="bogus"):
        _parse_review_filter("failed,bogus")


# --- _should_review ---


def test_should_review_all_matches_everything():
    comp = _comparison()
    assert _should_review(comp, {"all"}) is True


def test_should_review_failed():
    comp = _comparison(passed=False)
    assert _should_review(comp, {"failed"}) is True


def test_should_review_failed_skips_passing():
    comp = _comparison(passed=True)
    assert _should_review(comp, {"failed"}) is False


def test_should_review_failed_skips_no_baseline():
    """An unevaluated fresh test (production NO_REF shape: passed=True)
    is 'no-baseline', not 'failed'. Failing baseline-free tests DO match
    'failed' — covered in test_review_fixes_verdict.py."""
    comp = _comparison(passed=True, has_reference=False)
    assert _should_review(comp, {"failed"}) is False


def test_should_review_failed_skips_sim_failed():
    comp = _comparison(passed=False, sim_success=False)
    assert _should_review(comp, {"failed"}) is False


def test_should_review_no_baseline():
    comp = _comparison(has_reference=False)
    assert _should_review(comp, {"no-baseline"}) is True


def test_should_review_no_baseline_skips_existing():
    comp = _comparison(has_reference=True)
    assert _should_review(comp, {"no-baseline"}) is False


def test_should_review_warnings():
    comp = _comparison(warnings=["something changed"])
    assert _should_review(comp, {"warnings"}) is True


def test_should_review_warnings_skips_clean():
    comp = _comparison(warnings=[])
    assert _should_review(comp, {"warnings"}) is False


def test_should_review_sim_failed():
    comp = _comparison(sim_success=False)
    assert _should_review(comp, {"sim-failed"}) is True


def test_should_review_sim_failed_skips_success():
    comp = _comparison(sim_success=True)
    assert _should_review(comp, {"sim-failed"}) is False


def test_should_review_passed():
    comp = _comparison(passed=True, warnings=[])
    assert _should_review(comp, {"passed"}) is True


def test_should_review_passed_skips_with_warnings():
    comp = _comparison(passed=True, warnings=["warn"])
    assert _should_review(comp, {"passed"}) is False


def test_should_review_passed_skips_failed():
    comp = _comparison(passed=False)
    assert _should_review(comp, {"passed"}) is False


def test_should_review_combined_filters():
    """Combined filters match if any filter matches."""
    filters = {"failed", "no-baseline"}
    assert _should_review(_comparison(passed=False), filters) is True
    assert _should_review(_comparison(has_reference=False), filters) is True
    assert _should_review(_comparison(passed=True), filters) is False


def test_should_review_sim_failed_with_failed():
    """sim-failed + failed covers both crash and comparison failure."""
    filters = {"sim-failed", "failed"}
    assert _should_review(_comparison(sim_success=False), filters) is True
    assert _should_review(_comparison(passed=False), filters) is True
    assert _should_review(_comparison(passed=True), filters) is False


# --- the review's status word and its categories are the classifier's ---


def _comp_shapes() -> dict[str, TestComparison]:
    """One comparison per shape `compare_one` builds."""
    from dstf.comparison.metric_tree import MetricResult

    def tree(passed):
        return MetricResult(passed=passed, score=0.0, label="range")

    return {
        "not run": TestComparison("L.T", passed=False, sim_success=False, ran=False),
        "sim failed": TestComparison("L.T", passed=False, sim_success=False),
        "no baseline": TestComparison("L.T", passed=True, has_reference=False),
        "pass": TestComparison("L.T", passed=True),
        "pass, simulate-only": TestComparison(
            "L.T", passed=True, has_reference=False, simulate_only=True
        ),
        "pass, baseline-free": TestComparison(
            "L.T", passed=True, has_reference=False, metric_tree=tree(True)
        ),
        "fail": TestComparison("L.T", passed=False),
        "fail, baseline-free": TestComparison(
            "L.T", passed=False, has_reference=False, metric_tree=tree(False)
        ),
        "refused": TestComparison("L.T", passed=False, error_message="the base ..."),
        "refused, no baseline": TestComparison(
            "L.T", passed=False, has_reference=False, error_message="the base ..."
        ),
    }


#: shape -> (the classifier's status, the `-i` / `--rerun` categories that
#: select it, the review's status word).
_REVIEWED_AS = {
    "not run": ("NOT RUN", {"sim-failed"}, "NOT RUN"),
    "sim failed": ("SIM_FAIL", {"sim-failed"}, "SIM_FAIL"),
    "no baseline": ("NO_REF", {"no-baseline", "passed"}, "NO BASELINE"),
    "pass": ("PASS", {"passed"}, "PASS"),
    "pass, simulate-only": ("PASS", {"no-baseline", "passed"}, "PASS"),
    "pass, baseline-free": ("PASS", {"no-baseline", "passed"}, "PASS"),
    "fail": ("FAIL", {"failed"}, "FAIL"),
    # A test the comparison FAILED is "failed" and nothing else, whether or
    # not a baseline is stored: it is not one to accept a baseline for.
    "fail, baseline-free": ("FAIL", {"failed"}, "FAIL"),
    "refused": ("FAIL", {"failed"}, "FAIL"),
    "refused, no baseline": ("FAIL", {"failed"}, "FAIL"),
}


@pytest.mark.parametrize("shape", list(_REVIEWED_AS))
def test_a_category_selects_a_test_by_the_classifiers_status(shape):
    from dstf.reporting.status import classify

    comp = _comp_shapes()[shape]
    status, categories, _ = _REVIEWED_AS[shape]
    assert classify(comp) == status
    selected = {
        c
        for c in ("failed", "no-baseline", "sim-failed", "passed", "warnings")
        if _should_review(comp, {c})
    }
    assert selected == categories
    comp.warnings = ["a structural change"]
    assert _should_review(comp, {"warnings"}) is True
    assert _should_review(comp, {"passed"}) is False


@pytest.mark.parametrize("shape", list(_REVIEWED_AS))
def test_the_review_says_the_classifiers_status(shape, monkeypatch, tmp_path, capsys):
    """A refused test with no baseline read "NO BASELINE" above its error —
    the word was keyed on `has_reference`, not on the one classifier."""
    config, store = _review_env(tmp_path)
    monkeypatch.setattr("builtins.input", lambda prompt="": "s")
    _interactive_review([], {}, [_comp_shapes()[shape]], store, config)
    out = re.sub(r"\x1b\[\d+m", "", capsys.readouterr().out)
    assert f"  Status: {_REVIEWED_AS[shape][2]}\n" in out


def _criterion(ok: bool):
    """One criterion of the variable `x`, passed or not."""
    from dstf.comparison.types import VariableComparison

    return VariableComparison(
        index=1,
        name="x",
        passed=ok,
        nrmse=0.0,
        rmse=0.0,
        signal_range=1.0,
        max_abs_error=0.0,
        max_abs_error_time=0.0,
        reference_final=1.0,
        actual_final=1.0,
    )


@pytest.mark.parametrize(
    ("passed", "line"),
    [
        ((True, True, True), "  Criteria: 3/3 passed\n"),
        ((True, False, True), "  Criteria: 2/3 passed, 1 failed\n"),
    ],
)
def test_the_review_counts_criteria_and_calls_them_that(
    passed, line, monkeypatch, tmp_path, capsys
):
    """`Variables: 2/3 passed` counted the test's CRITERIA: two of them on
    one variable are two, and the Suite's and the dashboard's column for
    the same number is Criteria."""
    comp = TestComparison(
        # three criteria, ONE variable
        "L.T",
        passed=all(passed),
        variables=[_criterion(ok) for ok in passed],
    )
    config, store = _review_env(tmp_path)
    monkeypatch.setattr("builtins.input", lambda prompt="": "s")
    _interactive_review([], {}, [comp], store, config)
    out = re.sub(r"\x1b\[\d+m", "", capsys.readouterr().out)
    assert line in out and "Variables:" not in out, out


def test_every_header_counts_the_tests_under_review(monkeypatch, tmp_path, capsys):
    """`[2/2]`, `[3/5]`: from the second test on the header's total was the
    PREVIOUS test's number of criteria — the two counts shared one name."""
    comps = [
        TestComparison("L.A", passed=True, variables=[_criterion(True)] * 2),
        TestComparison("L.B", passed=True, variables=[_criterion(True)] * 5),
        TestComparison("L.C", passed=True),
    ]
    config, store = _review_env(tmp_path)
    monkeypatch.setattr("builtins.input", lambda prompt="": "s")
    _interactive_review([], {}, comps, store, config)
    out = re.sub(r"\x1b\[\d+m", "", capsys.readouterr().out)
    assert re.findall(r"^\[\d+/\d+\] .*$", out, flags=re.M) == [
        "[1/3] L.A",
        "[2/3] L.B",
        "[3/3] L.C",
    ]


def _scored_by(tree: dict) -> TestComparison:
    """A real comparison of one variable, `x`, off its baseline by 0.005
    (nrmse 2.5e-3), scored by `tree`."""
    from dstf.comparison.comparator import compare_test
    from dstf.comparison.tree_spec import parse_metric_tree

    t = np.array([0.0, 0.5, 1.0])
    values = np.array([1.0, 2.0, 3.0])
    test = _mk_test("L.T", metric_tree_spec=parse_metric_tree(tree))
    result = TestResult(
        model_id=test.model_id,
        success=True,
        variables=[VariableResult(index=1, name="x", time=t, values=values + 0.005)],
    )
    reference = {
        "test_id": "0001",
        "time": t.tolist(),
        "variables": [{"index": 1, "name": "x", "values": values.tolist()}],
    }
    return compare_test(test, result, decode_reference(reference), 1e-4)


#: Leaves of `x` for `_scored_by`: one that passes, one that fails, each of
#: them under a `warn`, and one whose variable the result does not hold.
_PASSES = {"metric": "nrmse", "variable": "x", "tolerance": 10.0}
_FAILS = {"metric": "nrmse", "variable": "x", "tolerance": 1e-9}
_ADVISORY_PASS = {"combinator": "warn", "children": [_PASSES]}
_ADVISORY_FAIL = {"combinator": "warn", "children": [_FAILS]}
_NOT_SCORED = {"metric": "nrmse", "variable": "gone"}


@pytest.mark.parametrize(
    ("combinator", "children", "status", "line"),
    [
        ("and", [_PASSES, _PASSES, _PASSES], "PASS", "3/3 passed"),
        ("and", [_FAILS, _PASSES, _FAILS], "FAIL", "1/3 passed, 2 failed"),
        (
            "and",
            [_FAILS, _ADVISORY_FAIL, _PASSES],
            "FAIL",
            "1/3 passed, 1 failed, 1 advisory failure",
        ),
        (
            "and",
            [_PASSES, _ADVISORY_FAIL, _PASSES],
            "PASS",
            "2/3 passed, 1 advisory failure",
        ),
        (
            "and",
            [_ADVISORY_FAIL, _PASSES, _ADVISORY_FAIL],
            "PASS",
            "1/3 passed, 2 advisory failures",
        ),
        (
            "and",
            [_FAILS, _ADVISORY_FAIL, _ADVISORY_FAIL, _ADVISORY_PASS],
            "FAIL",
            "1/4 passed, 1 failed, 2 advisory failures",
        ),
        # A criterion that was not scored is one that did not pass: the
        # console words it in its block and counts it with no other word.
        ("and", [_PASSES, _NOT_SCORED], "FAIL", "1/2 passed, 1 failed"),
        # A failed branch of an `or` that passes is no failure of the test
        # and none under a `warn`: the count says it, and no clause.
        ("or", [_FAILS, _PASSES], "PASS", "1/2 passed"),
    ],
    ids=[
        "all pass",
        "two fail",
        "one fails, one advisory",
        "passes with an advisory failure",
        "passes with two advisory failures",
        "one fails, two advisory",
        "one not scored",
        "a failed branch of a passing or",
    ],
)
def test_the_review_does_not_count_an_advisory_failure_as_a_failure(
    combinator, children, status, line, monkeypatch, tmp_path, capsys
):
    """`Criteria: 1/3 passed, 2 failed` of a test JUnit calls `1 criterion
    failed, with 1 advisory failure` and the tool `Fails 1 of 3 criteria`:
    the review counted every criterion that did not pass as one that fails
    the test. It reads `failed_criteria`, as the console and JUnit do, and
    its two counts are theirs."""
    from dstf.reporting.status import classify, failed_criteria

    comp = _scored_by({"combinator": combinator, "children": children})
    assert classify(comp) == status
    config, store = _review_env(tmp_path)
    monkeypatch.setattr("builtins.input", lambda prompt="": "s")
    _interactive_review([], {}, [comp], store, config)
    out = re.sub(r"\x1b\[\d+m", "", capsys.readouterr().out)
    assert f"  Criteria: {line}\n" in out, out
    # The two counts, against the one reading both other surfaces print.
    not_passed = failed_criteria(comp)
    n_advisory = sum(c.advisory for c in not_passed)
    failed = re.search(r"(\d+) failed", line)
    advisory = re.search(r"(\d+) advisory failure", line)
    assert (int(advisory.group(1)) if advisory else 0) == n_advisory
    if status == "FAIL":
        assert int(failed.group(1)) == len(not_passed) - n_advisory


# ---------------------------------------------------------------------------
# Parser <-> dispatch-table parity
# ---------------------------------------------------------------------------


def test_argparse_subcommands_match_dispatch_table():
    """Every command of the tree has a handler in ``_COMMANDS`` and every
    handler a command, by the name the command is typed with (``spec add``).
    Catches drift when a command is added to one site and not the other —
    and a group that names an action no parser has."""
    from dstf.cli.main import _COMMANDS

    parser = build_arg_parser()
    assert set(parser.commands) == set(_COMMANDS), (
        f"the command tree and _COMMANDS drifted:\n"
        f"  in the tree but not _COMMANDS: {set(parser.commands) - set(_COMMANDS)}\n"
        f"  in _COMMANDS but not the tree: {set(_COMMANDS) - set(parser.commands)}"
    )
    # The tree as argparse holds it, walked: what `parser.commands` records
    # is what a command line can actually reach.
    reachable: set[str] = set()

    def walk(node: argparse.ArgumentParser, path: str) -> None:
        subs = [a for a in node._actions if isinstance(a, argparse._SubParsersAction)]
        if not subs:
            reachable.add(path)
        for sub in subs:
            for name, child in sub.choices.items():
                walk(child, f"{path} {name}".strip())

    walk(parser, "")
    assert reachable == set(_COMMANDS)


def test_author_offers_only_the_project_flag_it_reads(tmp_path, capsys):
    """The tool edits and saves the spec testing.json names; an override
    here would show one file and save another. The three flags are refused
    by the parser, on either side of the command — so before any Config is
    built: a full resolution can write a default testing.json."""
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "package.mo").write_text("package X\nend X;\n", encoding="utf-8")
    cfg = str(tmp_path / "t.json")
    for flag, value in (
        ("--source-path", str(lib)),
        ("--reference-root", str(tmp_path)),
        ("--test-spec", "/x/spec.json"),
    ):
        for argv in (
            ["--config", cfg, "author", flag, value],
            [flag, value, "author", "--config", cfg],
        ):
            with pytest.raises(SystemExit) as exc:
                main(argv)
            assert exc.value.code == 2
            assert flag in capsys.readouterr().err
    # A Config build with --source-path writes its default testing.json in
    # the library's PARENT; nothing was built.
    assert not list(tmp_path.rglob("testing.json"))


def test_author_without_a_config_says_it_needs_one(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["author"]) == 2
    assert "needs --config" in capsys.readouterr().err


def test_author_without_the_extra_says_how_to_install_it(monkeypatch, capsys, tmp_path):
    """NiceGUI is an optional extra: `dstf author` without it must name the
    install, not raise ImportError at the user."""
    import builtins

    real_import = builtins.__import__

    def no_nicegui(name, *a, **k):
        if name == "nicegui" or name.startswith("nicegui."):
            raise ImportError("No module named 'nicegui'")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_nicegui)
    # Earlier test files import the shell package and its submodules; any one
    # of them left in `sys.modules` would satisfy the re-import from cache and
    # the blocked `nicegui` would never be reached — the test would pass with
    # nothing under test.
    for cached in list(sys.modules):
        if (
            cached == "dstf.authoring.shell"
            or cached.startswith("dstf.authoring.shell.")
            or cached == "dstf.authoring.forms"
        ):
            monkeypatch.delitem(sys.modules, cached, raising=False)
    rc = main(["--config", str(tmp_path / "testing.json"), "author"])
    assert rc == 2
    assert "--extra author" in capsys.readouterr().err


def test_author_without_regex_says_how_to_install_it(monkeypatch, capsys, tmp_path):
    """`regex` is of the extra too, and only a Test page imports it (the
    Variables drawer, per request): a venv synced before it joined the extra
    started, drew the Suite page, and failed every Test page. `run_app` is
    replaced so a check that lets it through records the call instead of
    serving."""
    shell = pytest.importorskip("dstf.authoring.shell")
    served = []
    monkeypatch.setattr(shell, "run_app", lambda *a, **k: served.append(a))
    monkeypatch.setitem(sys.modules, "regex", None)  # `import regex` raises
    rc = main(["--config", str(tmp_path / "testing.json"), "author"])
    assert served == []
    assert rc == 2
    err = capsys.readouterr().err
    assert "--extra author" in err and "regex" in err


def _edit_json(path, change) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize(
    "file,change,says",
    [
        ("testing.json", lambda d: d.update(theme="neon"), "theme"),
        ("testing.json", lambda d: d.update(baseline_points=1), "baseline_points"),
        (
            "test_spec.json",
            lambda d: d["tests"][0].update(simulaton={"stop_time": 3}),
            'test_spec.json: tests[FakeLib.A]: unknown field "simulaton" — did you '
            'mean "simulation"?',
        ),
        ("test_spec.json", lambda d: d.update(Tests=[]), 'unknown field "Tests"'),
    ],
    ids=["theme", "baseline_points", "spec-entry-key", "spec-root-key"],
)
def test_author_with_a_config_or_spec_error_serves_nothing(
    tmp_path, monkeypatch, capsys, file, change, says
):
    """`dstf author` opened the project on a thread AFTER the server was
    up: an error `run` prints as one `Config error: …` line and exits 2 on
    started the server, logged a traceback and showed in the page."""
    pytest.importorskip("nicegui")
    import dstf.authoring.shell.app as app_mod
    from test_session import _project

    served = []
    monkeypatch.setattr(app_mod.ui, "run", lambda *a, **k: served.append(a))
    cfg, work = _project(tmp_path)
    _edit_json(cfg.parent / file, change)
    capsys.readouterr()
    rc = main(["--config", str(cfg), "author", "--no-browser", "--port", "8831"])
    assert rc == 2 and served == []
    (line,) = capsys.readouterr().err.strip().splitlines()
    assert line.startswith("Config error: ") and says in line
    assert "(in memory)" not in line and "Traceback" not in line


def test_author_with_a_project_that_opens_serves(tmp_path, monkeypatch, capsys):
    """The control: the check that stops a bad project lets a good one by."""
    pytest.importorskip("nicegui")
    import dstf.authoring.shell.app as app_mod
    from test_session import _project

    served = []
    monkeypatch.setattr(app_mod.ui, "run", lambda *a, **k: served.append(a))
    monkeypatch.setattr(app_mod.app, "on_startup", lambda fn: None)
    monkeypatch.setattr(app_mod.app, "on_shutdown", lambda fn: None)
    monkeypatch.setattr(app_mod.Shell, "mount", lambda self: None)
    cfg, work = _project(tmp_path)
    rc = main(["--config", str(cfg), "author", "--no-browser", "--port", "8831"])
    assert rc == 0 and len(served) == 1


def test_author_checks_every_package_of_the_extra():
    """The install check imports `_AUTHOR_MODULES`, and nothing else ties
    it to pyproject.toml's ``author`` extra: a package added there and not
    here serves the Suite page and fails wherever it is first imported."""
    tomllib = pytest.importorskip("tomllib")
    import re

    from dstf.cli.author_cmd import _AUTHOR_MODULES

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    extra = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"][
        "optional-dependencies"
    ]["author"]
    names = {
        re.sub(r"[-_.]+", "-", re.match(r"[A-Za-z0-9._-]+", req).group()).lower()
        for req in extra
    }
    assert "nicegui" in names  # the parse read the extra
    assert names <= set(_AUTHOR_MODULES)


# ---------------------------------------------------------------------------
# --tolerance 0 survives _build_config
# ---------------------------------------------------------------------------


def _fake_library(tmp_path):
    """Minimal Modelica library root so Config's package.mo walk resolves
    inside tmp_path instead of climbing out of it."""
    lib = tmp_path / "FakeLib"
    lib.mkdir()
    (lib / "package.mo").write_text("package FakeLib end FakeLib;\n")
    return lib


def test_explicit_zero_tolerance_reaches_config(tmp_path):
    """``--tolerance 0`` is the strictest possible setting, not "unset".
    ``_build_config`` used to gate the flag on truthiness, so 0.0 was
    silently dropped and the default 1e-4 applied — while the comparator
    deliberately honors an explicit 0 (comparator.py is-not-None chain).
    """
    from dstf.cli.common import _build_config
    from dstf.cli.main import build_arg_parser

    lib = _fake_library(tmp_path)
    parser = build_arg_parser()
    args = parser.parse_args(["--source-path", str(lib), "run", "--tolerance", "0"])
    config = _build_config(args)
    assert config.tolerance == 0.0


def test_omitted_tolerance_falls_back_to_default(tmp_path):
    """Guard the other side of the is-not-None change: no flag still
    yields the documented default rather than 0."""
    from dstf.cli.common import _build_config
    from dstf.cli.main import build_arg_parser
    from dstf.config import DEFAULT_COMPARISON_TOLERANCE

    lib = _fake_library(tmp_path)
    parser = build_arg_parser()
    args = parser.parse_args(["--source-path", str(lib), "run"])
    config = _build_config(args)
    assert config.tolerance == DEFAULT_COMPARISON_TOLERANCE


def test_soft_check_add_s_source_does_not_shadow_the_source_path_flag():
    """`soft-check add`'s positional is dest='source', NOT 'source_path' —
    that name is the --source-path option, and the collision made
    _build_config treat the ref JSON as the library root (find_package_dir
    iterdir()s a file → NotADirectoryError; found live while importing the
    demo soft_check, D103)."""
    args = build_arg_parser().parse_args(
        ["soft-check", "add", "Lib.M", "openmodelica", "ref_0001.json"]
    )
    assert args.source == "ref_0001.json"
    # The option must remain unset so _build_config ignores it.
    assert args.source_path is None


# =========================================================================
# Folded in from tests/test_review_fixes_cli.py — regression tests for the
# 2026-07-06 review, final CLI/config/manifest batch.
#
# Decisions encoded here:
#
# * Finding 58 — the ``-i``/``--rerun`` category string is validated at
#   argparse time (exit 2 with the valid-categories message), never after a
#   multi-hour simulation run.
# * Finding 59 — ``--rerun`` with an empty matching set is SUCCESS (exit 0,
#   "Nothing to rerun"), and the merged report is still produced. A generic
#   empty ``--filter`` stays exit 1.
# * Finding 60 — ``export --output`` accepts a string path (coerced to Path).
# * Finding 30 — the ``--accept`` branch exits nonzero when any simulation
#   failed or fewer baselines were stored than attempted (naming the skips),
#   and accepts ONLY the tests simulated in THIS run, never the merged
#   ``--rerun``/``--merge`` manifest scope.
# * Finding 61 — ``--accept`` + ``-i`` is a hard argparse error.
# * Finding 62 — interactive review exits 1 iff any reviewed-or-skipped test
#   remains failing/sim-failed and was not accepted; else 0 (quit included).
# * Finding 63 — ``--filter`` matching uses ``fnmatch.fnmatchcase`` so the
#   same filter selects the same tests on Windows and Linux.
# * Finding 64 — ``--filter`` splits on commas NOT inside ``[]`` character
#   classes, so ``Test[A,B]*`` survives as one pattern.
# * Finding 65 — ``--package`` matches the package itself or children across
#   a dot boundary (no more ``MyLib.Fluid`` (superset of)
#   ``MyLib.FluidExperimental``).
# * Finding 66 — ``batch_manifest.json`` is written atomically; a corrupt
#   manifest is quarantined to ``*.corrupt`` with a warning instead of
#   crashing every subsequent run/compare at startup.
# * Leftover A — ``ConfigError`` exits CLI commands with one clean line and
#   exit code 2, not a traceback.
# =========================================================================


def _mk_test(model_id: str, **kw) -> TestModel:
    return TestModel(
        model_id=model_id,
        source_file=None,
        n_vars=1,
        variable_patterns=["x"],
        source="test_spec",
        **kw,
    )


def _mk_result(
    model_id: str,
    values=None,
    success: bool = True,
) -> TestResult:
    t = np.linspace(0.0, 1.0, 11)
    if not success:
        return TestResult(model_id=model_id, success=False, error_message="boom")
    v = np.asarray(values if values is not None else np.sin(t), dtype=float)
    return TestResult(
        model_id=model_id,
        success=True,
        variables=[VariableResult(index=1, time=t, values=v, name="x")],
    )


#: The backend `run_env` registers: what a test runs with when no
#: simulator is involved.
FAKE = "FakeRun"


class _Behaviour:
    """What the registered backend does, held by the test: the results each
    test reads back, the prior results a rerun or compare scores, and the
    manifest a run returns. A test replaces ``run_tests`` to make the run
    itself do something else."""

    def __init__(self, work_dir, results, prior_results=None, manifest_map=None):
        self.work_dir = Path(work_dir)
        self.results = results
        self.prior_results = prior_results or {}
        self.manifest_map = manifest_map
        self.run_tests = self._run_tests

    def _run_tests(self, tests):
        mm = self.manifest_map or {
            f"test_{i:04d}": {"model_id": t.model_id, "ref_id": None}
            for i, t in enumerate(tests, 1)
        }
        return [BatchManifest(work_dir=self.work_dir, manifest=mm)]


@pytest.fixture
def run_env(tmp_path):
    """A project whose testing.json names a registered fake backend and
    whose tests come from its own test_spec.json: no simulator, no browser,
    a tmp work dir. ``make`` writes the spec and returns the behaviour the
    backend reads."""
    config_dir = tmp_path / "config"
    ref_root = config_dir / "Resources" / "ReferenceResults"
    ref_root.mkdir(parents=True)
    (config_dir / "testing.json").write_text(
        json.dumps({"source_type": "python", "backend": FAKE}), encoding="utf-8"
    )
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    env = SimpleNamespace(config_dir=config_dir, work_dir=work_dir, ref_root=ref_root)

    @register(FAKE)
    class FakeRun(Backend):
        @classmethod
        def locate(cls, settings):
            return Located(
                (Found(EXECUTABLE, sys.executable, Rule(SEARCH, "the test")),)
            )

        def read_result(self, test, test_key, run_result):
            raise AssertionError("read_results is the behaviour's")

        def run_tests(self, tests):
            return env.behaviour.run_tests(tests)

        def read_results(self, manifests, scope_tests, on_result=None):
            return {
                t.model_id: env.behaviour.results[t.model_id]
                for t in scope_tests
                if t.model_id in env.behaviour.results
            }

        def read_last_results(self, tests):
            prior = env.behaviour.prior_results
            return {t.model_id: prior[t.model_id] for t in tests if t.model_id in prior}

    def make(entries, results, prior_results=None, manifest_map=None):
        """``entries``: model ids, each tracking ``x``, or spec entries."""
        spec = [
            e if isinstance(e, dict) else {"model": e, "variables": ["x"]}
            for e in entries
        ]
        (ref_root / "test_spec.json").write_text(
            json.dumps({"tests": spec}), encoding="utf-8"
        )
        env.behaviour = _Behaviour(work_dir, results, prior_results, manifest_map)
        return env.behaviour

    env.make = make
    yield env
    del _REGISTRY[FAKE]


def _run_args(env, *extra) -> argparse.Namespace:
    parser = build_arg_parser()
    return parser.parse_args(
        ["--config", str(env.config_dir), "run", "--work-dir", str(env.work_dir)]
        + list(extra)
    )


def _with_baseline(env, model_id: str) -> None:
    """Accept ``model_id``'s result as its primary baseline: a later run of
    the same result PASSes against it."""
    env.make([model_id], results={model_id: _mk_result(model_id)})
    assert cmd_run(_run_args(env, "--accept")) == 0


def _with_cpu_time(result: TestResult, seconds: float) -> TestResult:
    """``result`` holding a ``CPUtime`` that ends at ``seconds``: the Sim (s)
    a page shows where the run kept no wall time."""
    t = result.variables[0].time
    cpu = np.linspace(0.0, seconds, len(t))
    result.diagnostics = [VariableResult(index=2, time=t, values=cpu, name="CPUtime")]
    return result


def _stored_model_ids(env) -> set[str]:
    ref_dir = env.ref_root / FAKE / detect_os()
    if not ref_dir.exists():
        return set()
    return {
        json.loads(f.read_text(encoding="utf-8"))["model_id"]
        for f in ref_dir.glob("ref_*.json")
    }


# ---------------------------------------------------------------------------
# Finding 58 — review filter validated at parse time (exit 2, fast)
# ---------------------------------------------------------------------------


class TestReviewFilterValidatedAtParseTime:
    def test_interactive_typo_aborts_at_parse_time(self, capsys, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)  # any fallout stays out of the repo
        with pytest.raises(SystemExit) as exc:
            main(["run", "-i", "sim_failed"])
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "Invalid review filter" in err
        assert "sim-failed" in err  # the valid-categories hint

    def test_rerun_typo_aborts_at_parse_time(self, capsys, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main(["run", "--rerun", "bogus"])
        assert exc.value.code == 2
        assert "Invalid review filter" in capsys.readouterr().err

    def test_valid_filters_still_parse(self):
        parser = build_arg_parser()
        args = parser.parse_args(["run", "-i", "failed,warnings"])
        assert args.interactive == "failed,warnings"
        args = parser.parse_args(["run", "-i"])
        assert args.interactive == "all"
        args = parser.parse_args(["run", "--rerun"])
        assert args.rerun == "failed"


# ---------------------------------------------------------------------------
# Finding 61 — --accept and -i are mutually exclusive
# ---------------------------------------------------------------------------


def test_accept_and_interactive_are_mutually_exclusive(capsys, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        main(["run", "-i", "--accept"])
    assert exc.value.code == 2
    assert "not allowed with" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Finding 59 — empty --rerun set is success; empty --filter stays an error
# ---------------------------------------------------------------------------


class TestRerunEmptySetIsSuccess:
    def test_nothing_to_rerun_exits_zero(self, run_env, capsys):
        t1 = "Lib.T1"
        run_env.make([t1], results={}, prior_results={"Lib.T1": _mk_result("Lib.T1")})
        rc = cmd_run(_run_args(run_env, "--rerun", "failed"))
        out = capsys.readouterr().out
        assert rc == 0
        assert "Nothing to rerun" in out
        assert "failed" in out

    def test_empty_rerun_still_produces_merged_report(self, run_env, capsys):
        """Nothing failed, so nothing reruns; `--report` still writes the
        prior run's pages, each with the verdict it was scored with."""
        t1 = "Lib.T1"
        _with_baseline(run_env, t1)
        run_env.make([t1], results={}, prior_results={t1: _mk_result(t1)})
        rc = cmd_run(_run_args(run_env, "--rerun", "failed", "--report"))
        assert rc == 0
        (sidecar,) = (run_env.work_dir / "reports").glob("*/comparison_data.json")
        assert (sidecar.parent / "interactive.html").exists()
        assert json.loads(sidecar.read_text(encoding="utf-8"))["summary"]["status"] == (
            "PASS"
        )

    def test_plain_empty_filter_still_exits_one(self, run_env, capsys):
        t1 = "Lib.T1"
        run_env.make([t1], results={})
        rc = cmd_run(_run_args(run_env, "--filter", "Nope.*"))
        assert rc == 1
        assert "No tests matched the filter." in capsys.readouterr().out


# ---------------------------------------------------------------------------
# A final dashboard without sidecars shows verdicts: `run` / `compare`
# without --report hand the page the comparisons they hold
# ---------------------------------------------------------------------------


class TestTheDashboardGetsTheComparisons:
    """Each page a scoring command writes carries the verdict it scored and
    the run's CPU time, read off the page: no sidecar holds either."""

    @staticmethod
    def _says(page) -> tuple[bool, bool]:
        """Whether ``page`` reads PASS and a Sim (s) of 4.25 (the CPU time)."""
        html = page.read_text(encoding="utf-8")
        return ('data-status="pass"' in html, 'data-sort-sim="4.25"' in html)

    @pytest.mark.parametrize("command", ["run", "compare"])
    def test_the_page_is_rendered_with_the_comparisons(
        self, run_env, tmp_path, command
    ):
        t1 = "Lib.T1"
        _with_baseline(run_env, t1)
        result = _with_cpu_time(_mk_result(t1), 4.25)
        run_env.make([t1], results={t1: result}, prior_results={t1: result})
        out = tmp_path / "page.html"
        args = build_arg_parser().parse_args(
            ["--config", str(run_env.config_dir), command]
            + ["--work-dir", str(run_env.work_dir)]
            + ["--report-format", "html", "--output", str(out)]
        )
        (cmd_run if command == "run" else cmd_compare)(args)
        assert self._says(run_env.work_dir / "dashboard.html") == (True, True)
        assert self._says(out) == (True, True)

    def test_an_empty_rerun_renders_the_prior_comparisons(self, run_env):
        """`--rerun` with nothing to rerun still refreshes the page, with
        the comparisons it scored the prior results by."""
        t1 = "Lib.T1"
        _with_baseline(run_env, t1)
        prior = _with_cpu_time(_mk_result(t1), 4.25)
        run_env.make([t1], results={}, prior_results={t1: prior})
        assert cmd_run(_run_args(run_env, "--rerun", "failed")) == 0
        assert self._says(run_env.work_dir / "dashboard.html") == (True, True)

    def test_a_timed_out_run_reads_the_sim_fail_its_comparison_says(self, run_env):
        """Without --report there is no sidecar: the page's verdict is the
        comparison the command hands it. A test whose run timed out reads
        the SIM_FAIL the console printed, not the TIMED OUT its progress
        recorded."""
        from dstf.backends.progress import ProgressReporter

        t1 = "Lib.T1"
        backend = run_env.make(
            [t1], results={"Lib.T1": _mk_result("Lib.T1", success=False)}
        )

        def run_tests(tests, _run=backend.run_tests):
            progress = ProgressReporter(run_env.work_dir, total=1)
            progress.register("test_0001", "Lib.T1")
            progress.on_finish("test_0001", success=False, elapsed=1.0, timed_out=True)
            progress.finalize()
            return _run(tests)

        backend.run_tests = run_tests
        cmd_run(_run_args(run_env))
        html = (run_env.work_dir / "dashboard.html").read_text(encoding="utf-8")
        assert html.count('data-status="sim-fail"') == 1
        assert 'data-status="timed-out"' not in html

    def test_the_report_page_lists_each_test_with_its_verdict_and_cpu(self, run_env):
        """`run --report` writes the per-test pages, then the dashboard: with
        no status.json (this backend keeps none), the comparisons and
        results the command hands it are what put the test on the page —
        its PASS, and its CPU time as Sim (s)."""
        t1 = "Lib.T1"
        _with_baseline(run_env, t1)
        result = _with_cpu_time(_mk_result(t1), 4.25)
        run_env.make([t1], results={t1: result})
        cmd_run(_run_args(run_env, "--report"))
        assert not (run_env.work_dir / "status.json").exists()
        assert self._says(run_env.work_dir / "dashboard.html") == (True, True)


# ---------------------------------------------------------------------------
# A worst NRMSE of nan (the owner's call): a criterion scored by nrmse whose
# score is NaN makes the test's worst NRMSE nan, which sorts above every number
# ---------------------------------------------------------------------------


class TestAWorstNrmseOfNan:
    """A NaN sample in the run makes an nrmse score NaN. The test's Worst
    NRMSE reads ``nan`` — with no metrics tree, with one, and beside another
    nrmse criterion that scored a number — and sorts as above every number;
    a test whose nrmse criteria all scored numbers reads its worst."""

    T = np.linspace(0.0, 1.0, 11)
    NAN_AT_5 = np.where(np.arange(11) == 5, np.nan, np.sin(T))

    ENTRIES = [
        {"model": "Lib.NanFlat", "variables": ["x"]},
        {
            "model": "Lib.NanTree",
            "variables": ["x"],
            "metrics": {"metric": "nrmse", "variable": "x", "tolerance": 0.05},
        },
        {
            "model": "Lib.NanMixed",
            "variables": ["x", "y"],
            "metrics": {
                "combinator": "and",
                "children": [
                    {"metric": "nrmse", "variable": "x", "tolerance": 0.05},
                    {"metric": "nrmse", "variable": "y", "tolerance": 0.05},
                ],
            },
        },
        {"model": "Lib.Fine", "variables": ["x"]},
    ]

    def _result(self, model_id, x, y=None):
        names = {"x": x} if y is None else {"x": x, "y": y}
        return TestResult(
            model_id=model_id,
            success=True,
            variables=[
                VariableResult(index=i, time=self.T, values=np.asarray(v), name=n)
                for i, (n, v) in enumerate(names.items(), 1)
            ],
        )

    def _rows(self, page) -> dict[str, tuple[str, str]]:
        """Each row's Worst NRMSE text and sort value, by model."""
        html = page.read_text(encoding="utf-8")
        return {
            m.group(1): (m.group(2), m.group(3))
            for m in re.finditer(
                r'data-model="([^"]*)"(?:.|\n)*?data-nrmse="([^"]*)"\s*'
                r'data-sort-nrmse="([^"]*)"',
                html,
            )
        }

    def _scored(self, run_env, *extra):
        sin = np.sin(self.T)
        baseline = {
            e["model"]: self._result(e["model"], sin, sin) for e in self.ENTRIES
        }
        run_env.make(self.ENTRIES, results=baseline)
        assert cmd_run(_run_args(run_env, "--accept")) == 0
        run_env.make(
            self.ENTRIES,
            results={
                "Lib.NanFlat": self._result("Lib.NanFlat", self.NAN_AT_5),
                "Lib.NanTree": self._result("Lib.NanTree", self.NAN_AT_5),
                "Lib.NanMixed": self._result("Lib.NanMixed", self.NAN_AT_5, sin + 1e-3),
                "Lib.Fine": self._result("Lib.Fine", sin + 1e-3),
            },
        )
        cmd_run(_run_args(run_env, *extra))
        return self._rows(run_env.work_dir / "dashboard.html")

    @pytest.mark.parametrize("extra", [(), ("--report",)], ids=["run", "report"])
    def test_the_column_reads_nan_and_sorts_above_every_number(self, run_env, extra):
        rows = self._scored(run_env, *extra)
        nan = ("nan", "Infinity")
        assert {m: rows[m] for m in ("Lib.NanFlat", "Lib.NanTree", "Lib.NanMixed")} == {
            "Lib.NanFlat": nan,
            "Lib.NanTree": nan,
            "Lib.NanMixed": nan,
        }
        text, sort = rows["Lib.Fine"]
        assert text == format(float(sort), ".4e") and float(sort) < float("inf")

    def test_the_report_page_reads_nan(self, run_env):
        self._scored(run_env, "--report")
        stats = {}
        for sidecar in (run_env.work_dir / "reports").glob("*/comparison_data.json"):
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            (worst,) = [k for k in data["key_stats"] if k["label"] == "Worst NRMSE"]
            stats[data["model_id"]] = worst["current"]
        assert stats["Lib.NanMixed"] == "nan"
        assert stats["Lib.NanFlat"] == stats["Lib.NanTree"] == "nan"
        assert stats["Lib.Fine"] != "nan"


# ---------------------------------------------------------------------------
# Every path that ends a run leaves a static page. The backends' finalize
# leaves the page LIVE (the compare phase has not scored anything yet); the
# command that ends the run renders the final one.
# ---------------------------------------------------------------------------

_REFRESH = '<meta http-equiv="refresh"'


class TestEveryRunEndsOnAStaticPage:
    CONSTANT_CSV = "PythonTestingLib.Examples.ConstantCsv"

    def _run(self, tmp_path, monkeypatch, *extra, baselines=True):
        import io

        from conftest import python_example_copy

        config, _ = python_example_copy(tmp_path, baselines=baselines)
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))  # stdin closed
        rc = main(
            ["--config", str(config.config_file), "run"]
            + ["--work-dir", str(config.work_dir), "--filter", self.CONSTANT_CSV]
            + list(extra)
        )
        html = (config.work_dir / "dashboard.html").read_text(encoding="utf-8")
        return rc, html

    def test_run_interactive_leaves_a_static_page_with_verdicts(
        self, tmp_path, monkeypatch
    ):
        _, html = self._run(tmp_path, monkeypatch, "-i")
        assert _REFRESH not in html
        assert 'data-status="pass"' in html
        assert "SIMULATED" not in html.split("<tbody", 1)[1]

    def test_run_accept_leaves_a_static_page(self, tmp_path, monkeypatch):
        rc, html = self._run(tmp_path, monkeypatch, "--accept", baselines=False)
        assert rc == 0
        assert _REFRESH not in html
        # Accepted without scoring: the row says so.
        assert 'data-status="pending"' in html

    def test_an_aborted_run_leaves_a_static_page(self, run_env, capsys):
        t1 = "Lib.T1"
        backend = run_env.make([t1], results={})

        def run_tests(tests):
            raise RuntimeError("all workers failed to start")

        backend.run_tests = run_tests
        assert cmd_run(_run_args(run_env)) == 1
        html = (run_env.work_dir / "dashboard.html").read_text(encoding="utf-8")
        assert _REFRESH not in html

    # Any other way out after the page goes live — Ctrl+C, an error in the
    # compare phase or the accept — still ends on a static page, and the
    # exception still propagates.

    def _page(self, run_env) -> str:
        return (run_env.work_dir / "dashboard.html").read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        "exc", [KeyboardInterrupt(), ValueError("bad metric config")]
    )
    def test_an_exception_in_the_compare_phase_leaves_a_static_page(
        self, run_env, monkeypatch, exc
    ):
        run_env.make(["Lib.T1"], results={"Lib.T1": _mk_result("Lib.T1")})

        def compare_all(*a, **k):
            assert _REFRESH in self._page(run_env)  # live until the compare
            raise exc

        # A patch of DSTF's own step, by exception: its raising is the point.
        monkeypatch.setattr("dstf.comparison.comparator.compare_all", compare_all)
        with pytest.raises(type(exc)):
            cmd_run(_run_args(run_env))
        assert _REFRESH not in self._page(run_env)

    def test_a_failing_final_render_does_not_mask_the_runs_own_exception(
        self, run_env, monkeypatch, capsys
    ):
        """Ctrl+C in the compare phase, then the final render fails too: the
        Ctrl+C is what propagates, and the render's failure is a warning."""
        run_env.make(["Lib.T1"], results={"Lib.T1": _mk_result("Lib.T1")})

        def compare_all(*a, **k):
            raise KeyboardInterrupt

        def render_final(*a, **k):
            raise OSError("disk full")

        # Patches of DSTF's own steps, by exception: their raising is the point.
        monkeypatch.setattr("dstf.comparison.comparator.compare_all", compare_all)
        monkeypatch.setattr(
            "dstf.reporting.dashboard_render.render_final", render_final
        )
        with pytest.raises(KeyboardInterrupt):
            cmd_run(_run_args(run_env))
        assert (
            "could not write the final dashboard: disk full" in capsys.readouterr().err
        )

    @pytest.mark.parametrize("exc", [OSError("disk full"), KeyboardInterrupt()])
    def test_an_exception_from_the_backend_leaves_a_static_page(self, run_env, exc):
        """An OSError after the simulations, or Ctrl+C mid-simulation: the
        backend's page is live when it raises."""
        from dstf.reporting.dashboard_render import render_live

        backend = run_env.make(["Lib.T1"], results={})

        def run_tests(tests):
            render_live(run_env.work_dir)
            raise exc

        backend.run_tests = run_tests
        with pytest.raises(type(exc)):
            cmd_run(_run_args(run_env))
        assert _REFRESH not in self._page(run_env)

    def test_an_accept_that_raises_leaves_a_static_page(self, run_env, monkeypatch):
        run_env.make(["Lib.T1"], results={"Lib.T1": _mk_result("Lib.T1")})

        def accept_run_results(*a, **k):
            raise OSError("reference dir read-only")

        # A patch of DSTF's own step, by exception: its raising is the point.
        monkeypatch.setattr(
            "dstf.storage.accept.accept_run_results", accept_run_results
        )
        with pytest.raises(OSError, match="read-only"):
            cmd_run(_run_args(run_env, "--accept"))
        assert _REFRESH not in self._page(run_env)


# ---------------------------------------------------------------------------
# Finding 30 — --accept exit code + this-run-only scope
# ---------------------------------------------------------------------------


class TestAFinalPageListsEveryScoredTest:
    """Each run rewrites status.json with its own scope: a final page lists
    those rows and every test the command scored (D136's amendment)."""

    CONSTANT_CSV = "PythonTestingLib.Examples.ConstantCsv"
    ALL = {
        "PythonTestingLib.Examples.ConstantCsv",
        "PythonTestingLib.Examples.PointsCheck",
        "PythonTestingLib.Examples.SimpleRamp",
    }

    @pytest.fixture
    def filtered(self, tmp_path, monkeypatch):
        """A full run, then a `--filter` run of one test; returns a runner
        of further commands and the work dir."""
        from conftest import python_example_copy

        config, _ = python_example_copy(tmp_path)
        head = ["--config", str(config.config_file)]
        wd = ["--work-dir", str(config.work_dir)]

        def dstf(cmd: str, *extra: str) -> int:
            return main([*head, cmd, *wd, *extra])

        assert dstf("run") == 0
        assert dstf("run", "--filter", self.CONSTANT_CSV) == 0
        return dstf, config.work_dir

    @staticmethod
    def _rows(page: Path) -> dict[str, str]:
        html = page.read_text(encoding="utf-8")
        body = html.split('<tbody id="results-tbody">', 1)[1].split("</tbody>", 1)[0]
        rows = {}
        for tr in re.findall(r"<tr\b[^>]*>", body):
            attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', tr))
            rows[attrs["data-model"]] = attrs["data-status"]
        return rows

    def test_a_plain_filter_run_lists_its_own_tests(self, filtered):
        _dstf, work = filtered
        assert self._rows(work / "dashboard.html") == {self.CONSTANT_CSV: "pass"}

    def test_compare_lists_every_test_with_its_verdict(self, filtered, tmp_path):
        dstf, work = filtered
        out = work / "report.html"
        assert dstf("compare", "--report-format", "html", "--output", str(out)) == 0
        for page in (out, work / "dashboard.html"):
            assert self._rows(page) == dict.fromkeys(self.ALL, "pass"), page

    def test_a_merged_filter_run_lists_every_test(self, filtered):
        dstf, work = filtered
        assert dstf("run", "--filter", self.CONSTANT_CSV, "--merge") == 0
        assert self._rows(work / "dashboard.html") == dict.fromkeys(self.ALL, "pass")


class TestAcceptExitCodeAndScope:
    def test_partial_accept_exits_nonzero_and_names_skips(self, run_env, capsys):
        t1, t2 = "Lib.T1", "Lib.T2"
        run_env.make(
            [t1, t2],
            results={
                "Lib.T1": _mk_result("Lib.T1"),
                "Lib.T2": _mk_result("Lib.T2", success=False),
            },
        )
        rc = cmd_run(_run_args(run_env, "--accept"))
        out = capsys.readouterr().out
        assert rc != 0
        assert "Accepted 1" in out
        assert "Lib.T2" in out
        assert "simulation failed" in out
        assert _stored_model_ids(run_env) == {"Lib.T1"}

    def test_full_accept_exits_zero(self, run_env, capsys):
        t1 = "Lib.T1"
        run_env.make([t1], results={"Lib.T1": _mk_result("Lib.T1")})
        rc = cmd_run(_run_args(run_env, "--accept"))
        assert rc == 0
        assert _stored_model_ids(run_env) == {"Lib.T1"}

    @pytest.mark.parametrize("command", ["run", "compare"])
    def test_an_accept_names_the_simulate_only_tests_it_stored_nothing_for(
        self, run_env, capsys, command
    ):
        """`Accepted 0 test baselines to …`, exit 0, and no word of the
        test: what `dstf spec add <model>` then `run --accept` printed, and
        `compare --accept` after it. Not a warning and not a skip — the
        exit code is what it was."""
        # The entry `dstf spec add` writes.
        tank = {"model": "Lib.Tank", "variables": []}
        results = {"Lib.Tank": _mk_result("Lib.Tank")}
        run_env.make([tank], results=results, prior_results=results)
        args = build_arg_parser().parse_args(
            ["--config", str(run_env.config_dir), command]
            + ["--work-dir", str(run_env.work_dir), "--accept"]
        )
        rc = (cmd_run if command == "run" else cmd_compare)(args)
        out = capsys.readouterr().out
        assert rc == 0
        assert re.search(
            r"^Accepted 0 test baselines to .*\n"
            r"Simulate-only, nothing to store: Lib\.Tank\n",
            out,
            flags=re.M,
        ), out
        assert "Warning" not in out and "Skipped" not in out
        assert _stored_model_ids(run_env) == set()

    def test_an_accept_of_a_mixed_run_names_them_beside_what_it_stored(
        self, run_env, capsys
    ):
        simulate_only = [f"Lib.S{i}" for i in range(1, 9)]
        run_env.make(
            ["Lib.T1"] + [{"model": m, "variables": []} for m in simulate_only],
            results={m: _mk_result(m) for m in ["Lib.T1", *simulate_only]},
        )
        rc = cmd_run(_run_args(run_env, "--accept"))
        out = capsys.readouterr().out
        assert rc == 0
        assert re.search(r"^Accepted 1 test baselines to ", out, flags=re.M)
        assert (
            "\nSimulate-only, nothing to store: "
            "Lib.S1, Lib.S2, Lib.S3, Lib.S4, Lib.S5, Lib.S6 and 2 more\n"
        ) in out
        assert _stored_model_ids(run_env) == {"Lib.T1"}

    def test_run_accept_refuses_a_test_the_manifest_records_as_pending(
        self, run_env, capsys
    ):
        """A test still stamped pending after the run was in flight when the
        run aborted. cmd_run persists the manifest its backend returns
        (`enrich_ref_ids` saves it), so the fake's manifest_map is what the
        guard reads — and the run path refuses with the message the compare
        path prints."""
        t1 = "Lib.T1"
        run_env.make(
            [t1],
            results={"Lib.T1": _mk_result("Lib.T1")},
            manifest_map={
                "test_0001": {
                    "model_id": "Lib.T1",
                    "ref_id": None,
                    "last_run_status": "pending",
                }
            },
        )
        rc = cmd_run(_run_args(run_env, "--accept"))
        out = capsys.readouterr().out
        assert rc != 0
        assert "Lib.T1" in out and "re-run before accepting" in out
        assert _stored_model_ids(run_env) == set()

    def test_accept_with_merge_stores_only_this_runs_tests(self, run_env, capsys):
        """A merge expands the report scope, but stale on-disk results for
        out-of-scope tests must never become baselines. `--merge` beside
        `--accept` is the parser's error; `--rerun` implies a merge, so the
        accept is held to its own tests all the same."""
        t1, t2 = "Lib.T1", "Lib.T2"
        run_env.make(
            [t1, t2],
            results={
                "Lib.T1": _mk_result("Lib.T1"),
                "Lib.T2": _mk_result("Lib.T2"),  # stale on-disk result
            },
            manifest_map={
                "test_0001": {"model_id": "Lib.T1", "ref_id": None},
                "test_0002": {"model_id": "Lib.T2", "ref_id": None},
            },
        )
        args = _run_args(run_env, "--filter", "Lib.T1", "--accept")
        args.merge = True  # as `--rerun` sets it
        rc = cmd_run(args)
        assert rc == 0
        assert _stored_model_ids(run_env) == {"Lib.T1"}

    def test_accept_skips_poisoned_baseline_and_continues(self, run_env, capsys):
        bad_values = [0.0, 1.0, float("nan")] + [2.0] * 8
        t1, t3 = "Lib.T1", "Lib.T3"
        run_env.make(
            [t1, t3],
            results={
                "Lib.T1": _mk_result("Lib.T1"),
                "Lib.T3": _mk_result("Lib.T3", values=bad_values),
            },
        )
        rc = cmd_run(_run_args(run_env, "--accept"))
        out = capsys.readouterr().out
        assert rc != 0
        assert "Lib.T3" in out
        assert "non-finite" in out
        assert _stored_model_ids(run_env) == {"Lib.T1"}


# ---------------------------------------------------------------------------
# The exit code at the seam. TRANSFORM-UnitTests drives `dstf` by its exit
# code and its JUnit file, so every route is pinned: a test NOT RUN beside
# a PASS exits 0, a FAIL exits 1, and a command in which nothing ran exits 1
# (D145 §5).
# ---------------------------------------------------------------------------

#: Each report route's extra arguments; `{out}` is a file under tmp_path.
_ROUTES = {
    "console": [],
    "junit": ["--report-format", "junit", "--output", "{out}.xml"],
    "html": ["--report-format", "html", "--output", "{out}.html"],
    "report": ["--report"],
    "interactive": ["-i"],
}

_T1, _T2 = "Lib.T1", "Lib.T2"


def _said_nothing_ran(err: str) -> list[str]:
    """The lines of ``err`` that say nothing ran — ONE when nothing did, on
    every route (D145 §5)."""
    return [line for line in err.splitlines() if line.startswith("Nothing ran")]


def _nothing_ran_line(n: int) -> str:
    if not n:  # no test in scope at all: there is nothing to count
        return "Nothing ran: no test in scope."
    return f"Nothing ran: no test in scope has a result ({n} NOT RUN) — exit 1."


#: T1 has a baseline, T2 has none; neither has a result unless one is named.
_SITUATIONS = {
    "not run + pass": ({_T1: None}, 0),
    "not run + fail": ({_T1: np.cos(np.linspace(0.0, 1.0, 11))}, 1),
    "nothing ran": ({}, 1),
}


class TestTheExitCodeOnEveryRoute:
    @pytest.fixture
    def routed(self, run_env, monkeypatch, tmp_path):
        """Store T1's baseline, then return a runner of one command over one
        situation: T2 is NOT RUN in every one."""
        run_env.make([_T1], results={_T1: _mk_result(_T1)})
        assert cmd_run(_run_args(run_env, "--accept")) == 0
        # The review skips whatever it is offered: skipping a FAIL leaves it
        # failing, skipping a PASS leaves nothing failing.
        monkeypatch.setattr("builtins.input", lambda prompt="": "s")

        def dstf(command: str, route: str, situation: str) -> int:
            values, _ = _SITUATIONS[situation]
            results = {m: _mk_result(m, values=v) for m, v in values.items()}
            run_env.make(
                [_T1, _T2],
                results=results,
                prior_results=results,
            )
            extra = [a.format(out=tmp_path / route) for a in _ROUTES[route]]
            args = build_arg_parser().parse_args(
                ["--config", str(run_env.config_dir), command]
                + ["--work-dir", str(run_env.work_dir), *extra]
            )
            return (cmd_run if command == "run" else cmd_compare)(args)

        return dstf

    @pytest.mark.parametrize("situation", list(_SITUATIONS))
    @pytest.mark.parametrize(
        "command,route",
        [("run", r) for r in _ROUTES]
        + [("compare", r) for r in _ROUTES if r != "interactive"],
    )
    def test_the_exit_code(self, routed, capsys, command, route, situation):
        """The exit code, and when nothing ran the one stderr line that says
        why it is 1 — the junit, html and --report routes said nothing."""
        capsys.readouterr()
        assert routed(command, route, situation) == _SITUATIONS[situation][1]
        said = _said_nothing_ran(capsys.readouterr().err)
        assert said == ([_nothing_ran_line(2)] if situation == "nothing ran" else [])


class TestNothingRanOverTheRealLibrary:
    """The same seam over the Python example library, as the owner's suite
    meets it: a compare over a work dir nothing ran in, and a compare after
    a run whose worker pool never started (every test stamped pending)."""

    ROUTES = ("console", "junit", "html", "report")

    @pytest.fixture
    def lib(self, tmp_path, monkeypatch):
        from conftest import python_example_copy

        config, _ = python_example_copy(tmp_path)

        def dstf(command: str, *extra: str) -> int:
            return main(
                ["--config", str(config.config_file), command]
                + ["--work-dir", str(config.work_dir), *extra]
            )

        return dstf, tmp_path

    @staticmethod
    def _route(tmp_path, route: str) -> list[str]:
        return [a.format(out=tmp_path / route) for a in _ROUTES[route]]

    @pytest.mark.parametrize("route", ROUTES)
    def test_a_compare_over_a_work_dir_nothing_ran_in(self, lib, route, capsys):
        dstf, tmp_path = lib
        assert dstf("compare", *self._route(tmp_path, route)) == 1
        # The example library's three tests, none with a result.
        assert _said_nothing_ran(capsys.readouterr().err) == [_nothing_ran_line(3)]

    def test_a_compare_after_a_pool_that_never_started(self, lib, monkeypatch):
        from dstf.backends.python.backend import PythonBackend

        dstf, tmp_path = lib

        def never_started(self, tests):
            # What a persistent pool does when no worker starts: the tests
            # are stamped pending, then the run raises.
            self._prepare_run(tests).progress.finalize()
            raise RuntimeError("All workers failed to start")

        with monkeypatch.context() as m:
            m.setattr(PythonBackend, "run_tests", never_started)
            assert dstf("run") == 1
        for route in self.ROUTES:
            assert dstf("compare", *self._route(tmp_path, route)) == 1, route
        # The JUnit file says why: every test skipped, not run.
        root = ET.parse(tmp_path / "junit.xml").getroot()
        cases = list(root.iter("testcase"))
        assert cases and all(
            [(c.tag, c.get("message")) for c in tc] == [("skipped", "not run")]
            for tc in cases
        )


# ---------------------------------------------------------------------------
# Finding 62 — interactive review exit code
# ---------------------------------------------------------------------------


@dataclass
class _FakeComp:
    model_id: str = "Lib.T"
    passed: bool = True
    sim_success: bool = True
    has_reference: bool = True
    warnings: list = field(default_factory=list)
    error_message: str | None = None
    variables: list = field(default_factory=list)
    test_id: int | None = None
    metric_tree: object = None
    ran: bool = True
    # What `reporting.status.classify` reads, which the review asks for a
    # refused test.
    simulate_only: bool = False
    evaluated: bool = False


def _review_env(tmp_path, statuses=None):
    """A real config + store over a tmp project whose last run completed.

    The review loop's `[a]` goes through `accept_run_results`, which reads the
    run's batch manifest — so a store fake carrying only `store_reference` can
    no longer stand in for the store here.
    """
    lib = tmp_path / "Lib"
    lib.mkdir()
    (lib / "package.mo").write_text("package Lib\nend Lib;\n", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    manifest = {
        f"test_{i:04d}": {
            "model_id": model_id,
            "ref_id": None,
            "last_run_status": status,
        }
        for i, (model_id, status) in enumerate(
            (statuses or {"Lib.A": "passed", "Lib.B": "passed"}).items(), 1
        )
    }
    (work / "batch_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    config = Config(source_path=lib, reference_root=tmp_path / "refs", work_dir=work)
    return config, ReferenceStore(config)


def _feed_inputs(monkeypatch, answers):
    it = iter(answers)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(it))


class TestInteractiveReviewExitCode:
    def test_skipping_all_failing_tests_exits_one(self, monkeypatch, tmp_path):
        config, store = _review_env(tmp_path)
        _feed_inputs(monkeypatch, ["s"])
        rc = _interactive_review(
            [], {}, [_FakeComp("Lib.A", passed=False)], store, config
        )
        assert rc == 1

    def test_quit_on_green_suite_exits_zero(self, monkeypatch, tmp_path):
        config, store = _review_env(tmp_path)
        _feed_inputs(monkeypatch, ["q"])
        comps = [_FakeComp("Lib.A"), _FakeComp("Lib.B")]
        rc = _interactive_review([], {}, comps, store, config)
        assert rc == 0

    def test_completed_green_review_exits_zero(self, monkeypatch, tmp_path):
        config, store = _review_env(tmp_path)
        _feed_inputs(monkeypatch, ["s", "s"])
        comps = [_FakeComp("Lib.A"), _FakeComp("Lib.B")]
        rc = _interactive_review([], {}, comps, store, config)
        assert rc == 0

    def test_sim_failed_auto_skip_exits_one(self, tmp_path):
        config, store = _review_env(tmp_path)
        comps = [_FakeComp("Lib.A", passed=False, sim_success=False)]
        rc = _interactive_review([], {}, comps, store, config)
        assert rc == 1

    def test_a_test_not_run_is_not_a_failure(self, monkeypatch, tmp_path, capsys):
        """D145: `run -i` exits as `run` does — a test with no result reads
        NOT RUN, is passed over, and is not left failing."""
        config, store = _review_env(tmp_path)
        _feed_inputs(monkeypatch, ["s"])
        comps = [
            _FakeComp("Lib.A", passed=False, sim_success=False, ran=False),
            _FakeComp("Lib.B"),
        ]
        rc = _interactive_review([], {}, comps, store, config)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Status: NOT RUN" in out and "SIM_FAIL" not in out
        assert "Skipping (not run)" in out

    def test_a_review_in_which_nothing_ran_exits_one(self, tmp_path, capsys):
        """D145 §5: `run -i` exits as `run` does — nothing ran is not a
        green run, and it says so. (A review is quit only at a prompt,
        which only a test that ran is offered.)"""
        config, store = _review_env(tmp_path)
        not_run = _FakeComp("Lib.A", passed=False, sim_success=False, ran=False)
        assert _interactive_review([], {}, [not_run], store, config) == 1
        assert _said_nothing_ran(capsys.readouterr().err) == [_nothing_ran_line(1)]
        assert _interactive_review([], {}, [], store, config) == 1
        assert _said_nothing_ran(capsys.readouterr().err) == [_nothing_ran_line(0)]

    def test_accepting_the_failure_resolves_it(self, monkeypatch, tmp_path, capsys):
        config, store = _review_env(tmp_path)
        test = _mk_test("Lib.A")
        result = _mk_result("Lib.A")
        _feed_inputs(monkeypatch, ["a"])
        rc = _interactive_review(
            [test],
            {"Lib.A": result},
            [_FakeComp("Lib.A", passed=False)],
            store,
            config,
        )
        assert rc == 0
        assert "Done. Accepted 1, skipped 0." in capsys.readouterr().out
        assert store.get_reference("Lib.A") is not None

    def test_quit_after_skipping_a_failure_exits_one(self, monkeypatch, tmp_path):
        config, store = _review_env(tmp_path)
        _feed_inputs(monkeypatch, ["s", "q"])
        comps = [_FakeComp("Lib.A", passed=False), _FakeComp("Lib.B", passed=False)]
        rc = _interactive_review([], {}, comps, store, config)
        assert rc == 1

    def test_a_poisoned_result_is_refused_and_the_loop_continues(
        self, monkeypatch, tmp_path, capsys
    ):
        """Leftover B interplay: the store refuses a non-finite trajectory;
        the review loop reports the reason and reviews the next test."""
        config, store = _review_env(tmp_path)
        tests = [_mk_test("Lib.A"), _mk_test("Lib.B")]
        results = {
            "Lib.A": _mk_result("Lib.A", values=[1.0] * 5 + [float("nan")] + [1.0] * 5),
            "Lib.B": _mk_result("Lib.B"),
        }
        comps = [_FakeComp("Lib.A", passed=False), _FakeComp("Lib.B", passed=False)]
        _feed_inputs(monkeypatch, ["a", "s"])
        rc = _interactive_review(tests, results, comps, store, config)
        assert rc == 1
        out = capsys.readouterr().out
        assert "non-finite" in out
        assert "Done. Accepted 0, skipped 2." in out
        assert store.get_reference("Lib.A") is None

    def test_a_pending_test_is_refused_and_stores_nothing(
        self, monkeypatch, tmp_path, capsys
    ):
        """`[a]` refuses what `compare --accept` refuses: a test the last run
        left in flight has no authoritative result to bless."""
        config, store = _review_env(tmp_path, {"Lib.A": "pending"})
        test = _mk_test("Lib.A")
        _feed_inputs(monkeypatch, ["a"])
        rc = _interactive_review(
            [test],
            {"Lib.A": _mk_result("Lib.A")},
            [_FakeComp("Lib.A", passed=False)],
            store,
            config,
        )
        assert rc == 1
        assert "re-run before accepting" in capsys.readouterr().out
        assert not list(store.ref_dir.glob("ref_*.json"))

    def test_a_simulate_only_test_stores_nothing_and_still_exits_zero(
        self, monkeypatch, tmp_path, capsys
    ):
        """A simulate-only test is neither stored nor skipped by the accept
        function; the loop prints the accept's own line for it — the one
        `--accept` prints, where it read `Not accepted: …` in red, as a
        refusal — and counts the test as passed over. The exit code stays
        0 — the comparison passed, so nothing the loop passed over is still
        failing."""
        config, store = _review_env(tmp_path, {"Lib.S": "passed"})
        test = _mk_test("Lib.S", simulate_only=True)
        _feed_inputs(monkeypatch, ["a"])
        rc = _interactive_review(
            [test],
            {"Lib.S": _mk_result("Lib.S")},
            [_FakeComp("Lib.S")],
            store,
            config,
        )
        out = re.sub(r"\x1b\[\d+m", "", capsys.readouterr().out)
        assert "  Simulate-only, nothing to store: Lib.S\n" in out
        assert "Not accepted" not in out
        assert "Done. Accepted 0, skipped 1." in out
        assert store.index.get_id("Lib.S") is None
        assert rc == 0


class TestMergedOutcomeReachesTheReviewWithItsVerdict:
    def test_a_timed_out_merged_in_test_is_never_offered_for_acceptance(
        self, tmp_path, monkeypatch, capsys
    ):
        """`run --rerun failed -i` then `a`: the review is handed the merged
        outcome. A merged-in test whose run timed out must arrive as a
        failed simulation — the loop skips it before any prompt — so its
        partial result can never become the primary baseline. Before the
        verdict moved into `read_results`, it arrived green (NO BASELINE),
        was prompted, and `a` stored it."""
        from conftest import python_example_copy
        from dstf.backends import dataset_kind_for, get_backend
        from dstf.comparison.comparator import compare_all
        from dstf.pipeline import run_pipeline

        ramp_id = "PythonTestingLib.Examples.SimpleRamp"
        constant_id = "PythonTestingLib.Examples.ConstantCsv"
        config, all_tests = python_example_copy(tmp_path, baselines=False)
        backend, store = get_backend(config), ReferenceStore(config)
        backend.quiet = True
        ramp = [t for t in all_tests if t.model_id == ramp_id]
        constant = [t for t in all_tests if t.model_id == constant_id]

        run_pipeline(config, ramp, backend=backend, store=store)
        path = config.work_dir / "batch_manifest.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        for entry in data.values():
            if entry["model_id"] == ramp_id:
                entry["last_run_status"] = "timed_out"
        path.write_text(json.dumps(data), encoding="utf-8")

        outcome = run_pipeline(
            config,
            constant,
            backend=backend,
            store=store,
            merge=True,
            candidates=all_tests,
        )
        comparisons = compare_all(
            outcome.scope_tests,
            outcome.results,
            store,
            config.tolerance,
            config.default_points,
            dataset_kind=dataset_kind_for(backend),
        )
        # Accept everything the loop offers, in whatever order discovery
        # lists the two. A failed simulation is skipped without a prompt, so
        # only ConstantCsv is prompted and one `a` is consumed; the second
        # `a` is consumed only if the ramp IS prompted — the defect — and
        # then stores it.
        _feed_inputs(monkeypatch, ["a", "a"])
        rc = _interactive_review(
            outcome.scope_tests, outcome.results, comparisons, store, config
        )
        out = capsys.readouterr().out

        assert "Skipping (simulation failed)" in out
        assert store.get_reference(ramp_id) is None
        assert store.get_reference(constant_id) is not None
        assert rc == 1  # the timed-out test is still unresolved


# ---------------------------------------------------------------------------
# Finding 63 — case-sensitive filter matching on every OS
# ---------------------------------------------------------------------------


def test_filter_matching_is_case_sensitive_even_if_fnmatch_is_not(monkeypatch):
    """Simulate Windows' case-insensitive fnmatch.fnmatch; _filter_tests must
    use fnmatchcase so both OS partitions select identical test sets."""
    import fnmatch as fnmatch_mod

    monkeypatch.setattr(
        fnmatch_mod,
        "fnmatch",
        lambda name, pat: fnmatch_mod.fnmatchcase(name.lower(), pat.lower()),
    )
    tests = [_mk_test("Lib.Foo")]
    assert _filter_tests(tests, pattern="lib.foo") == []
    assert len(_filter_tests(tests, pattern="Lib.Foo")) == 1


# ---------------------------------------------------------------------------
# Finding 64 — commas inside [] character classes are not separators
# ---------------------------------------------------------------------------


class TestFilterCommaSplit:
    def test_character_class_comma_survives(self):
        assert _resolve_filter_patterns("Test[A,B]*,Other.*") == [
            "Test[A,B]*",
            "Other.*",
        ]

    def test_plain_comma_split_unchanged(self):
        assert _resolve_filter_patterns("Foo.A, Foo.B") == ["Foo.A", "Foo.B"]

    def test_unclosed_bracket_is_one_pattern(self):
        assert _resolve_filter_patterns("Test[A,B") == ["Test[A,B"]

    def test_filter_tests_with_character_class(self):
        tests = [
            _mk_test("Lib.TestA1"),
            _mk_test("Lib.TestC1"),
            _mk_test("Other.X"),
        ]
        got = {
            t.model_id for t in _filter_tests(tests, pattern="Lib.Test[A,B]*,Other.*")
        }
        assert got == {"Lib.TestA1", "Other.X"}


# ---------------------------------------------------------------------------
# Finding 65 — --package respects the dot boundary
# ---------------------------------------------------------------------------


def test_package_filter_respects_dot_boundary():
    tests = [
        _mk_test("MyLib.Fluid"),
        _mk_test("MyLib.Fluid.PipeTest"),
        _mk_test("MyLib.FluidExperimental.Test"),
    ]
    got = {t.model_id for t in _filter_tests(tests, package="MyLib.Fluid")}
    assert got == {"MyLib.Fluid", "MyLib.Fluid.PipeTest"}


# ---------------------------------------------------------------------------
# Finding 66 — BatchManifest atomic save + corrupt-load quarantine
# ---------------------------------------------------------------------------


class TestBatchManifestRobustness:
    def test_corrupt_manifest_is_quarantined_not_fatal(self, tmp_path, caplog):
        p = tmp_path / "batch_manifest.json"
        p.write_text("{ definitely not json", encoding="utf-8")
        with caplog.at_level(logging.WARNING):
            bm = BatchManifest.load(p)
        assert bm.manifest == {}
        assert not p.exists()
        corrupt = tmp_path / "batch_manifest.json.corrupt"
        assert corrupt.exists()
        assert corrupt.read_text(encoding="utf-8").startswith("{ definitely")
        assert any("corrupt" in r.message.lower() for r in caplog.records)

    def test_wrong_shape_manifest_is_quarantined(self, tmp_path):
        p = tmp_path / "batch_manifest.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        bm = BatchManifest.load(p)
        assert bm.manifest == {}
        assert (tmp_path / "batch_manifest.json.corrupt").exists()

    def test_assign_test_keys_survives_corrupt_manifest(self, tmp_path):
        """The startup-crash regression: run/compare must proceed with fresh
        keys instead of dying on a torn manifest."""
        from dstf.backends.base import assign_test_keys

        (tmp_path / "batch_manifest.json").write_text("garbage", encoding="utf-8")
        manifest_map, items = assign_test_keys(tmp_path, [_mk_test("Lib.T1")])
        assert [tk for _, tk in items] == ["test_0001"]
        assert manifest_map["test_0001"]["model_id"] == "Lib.T1"

    def test_save_round_trips_and_leaves_no_tmp_files(self, tmp_path):
        bm = BatchManifest(
            work_dir=tmp_path,
            manifest={"test_0001": {"model_id": "Lib.T1", "ref_id": None}},
        )
        path = bm.save()
        assert BatchManifest.load(path).manifest["test_0001"]["model_id"] == "Lib.T1"
        assert list(tmp_path.glob("*.tmp")) == []


# ---------------------------------------------------------------------------
# Leftover A — ConfigError exits cleanly with code 2
# ---------------------------------------------------------------------------


class TestConfigErrorCleanExit:
    def test_missing_explicit_config_exits_two(self, capsys, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        rc = main(["--config", str(tmp_path / "nope" / "testing.json"), "discover"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "Config error:" in err
        assert "Traceback" not in err

    def test_malformed_config_exits_two(self, capsys, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        bad = tmp_path / "testing.json"
        bad.write_text("{ nope", encoding="utf-8")
        rc = main(["--config", str(bad), "discover"])
        assert rc == 2
        assert f"Config error: {bad.resolve()}: not JSON (" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# --batch on a backend with one way to run is an error, not a no-op
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source_type", "backend"), [("fmu", "FMPy"), ("python", "Python")]
)
def test_batch_on_a_backend_with_no_workers_is_a_config_error(
    tmp_path, capsys, source_type, backend
):
    """`--batch` turns the workers that stay alive off. FMPy and Python have
    none, and the flag was silently dropped there. Said before the library
    is scanned or anything simulates."""
    cfg = tmp_path / "testing.json"
    cfg.write_text(
        json.dumps({"source_type": source_type, "backend": backend}), encoding="utf-8"
    )

    # Read, this spec is an error of its own: the refusal must come first.
    spec = tmp_path / "Resources" / "ReferenceResults" / "test_spec.json"
    spec.parent.mkdir(parents=True)
    spec.write_text("{ not json", encoding="utf-8")
    work = tmp_path / "work"
    rc = main(["run", "--config", str(cfg), "--batch", "--work-dir", str(work)])
    assert rc == 2
    err = capsys.readouterr().err
    assert err.startswith(f"Config error: --batch does not apply to the {backend} ")
    assert len(err.strip().splitlines()) == 1
    assert not work.exists()


def test_batch_stays_on_a_backend_that_has_workers(tmp_path):
    from dstf.cli.run_cmds import _refuse_batch_without_workers

    for backend in ("Dymola", "OpenModelica", "Julia"):
        _refuse_batch_without_workers(SimpleNamespace(backend=backend))


# ---------------------------------------------------------------------------
# Finding 60 — export --output takes a string path
# ---------------------------------------------------------------------------


class TestExportOutputPath:
    def test_json_export_to_string_path(self, tmp_config_dir, tmp_path):
        out = tmp_path / "exports" / "refs.json"
        out.parent.mkdir()  # a directory that is not there is refused
        rc = main(
            [
                "--config",
                str(tmp_config_dir),
                "baseline",
                "export",
                "--format",
                "json",
                "--output",
                str(out),
            ]
        )
        assert rc == 0
        assert out.exists()
        json.loads(out.read_text(encoding="utf-8"))

    def test_csv_export_to_string_path(self, tmp_config_dir, tmp_path):
        out = tmp_path / "exports" / "refs.csv"
        out.parent.mkdir()  # a directory that is not there is refused
        rc = main(
            [
                "--config",
                str(tmp_config_dir),
                "baseline",
                "export",
                "--format",
                "csv",
                "--output",
                str(out),
            ]
        )
        assert rc == 0
        assert out.exists()


# =========================================================================
# Phase C-v round 1 — CLI UX batch (v1.0 plan §5).
#
# Decisions encoded here:
#   1. Global flags are accepted BEFORE and AFTER the subcommand.
#   2. User errors become ConfigError → "Config error: ..." + exit 2, never
#      a traceback.
#   3. Every `manifest cleanup` path is a dry-run without --apply.
#   4. --parallel means different things on `run` and `compare`; both
#      subparsers say so.
#   8. `compare` without --backend uses the backend the work dir's run
#      recorded, not environment auto-detect.
# =========================================================================


# --- 1. the project flags, on either side of the command ------------------
# (which command reads which flag, and that both sides agree for every
# command, is tests/test_cli_tree.py)


def test_subcommand_global_flag_does_not_erase_the_pre_subcommand_value():
    """argparse copies the subparser's whole namespace onto the top-level
    one, so the copies a command offers default to SUPPRESS. Without that, a
    subparser default of None would wipe a value given before the
    subcommand (silently, with no usage error to notice)."""
    parser = build_arg_parser()
    args = parser.parse_args(["--config", "X", "run", "--filter", "A"])
    assert args.config == "X"
    # (a second, different value after the command is an error: one value
    # per flag, tests/test_cli_tree.py)


def test_global_flags_default_to_none_when_given_nowhere():
    """SUPPRESS is only on the commands' copies; the top-level parser still
    guarantees the attribute exists, whatever the command offers."""
    from dstf.cli.parser import PROJECT_FLAGS

    for argv in (["discover"], ["author"], ["spec", "schema"]):
        args = build_arg_parser().parse_args(argv)
        for dest in PROJECT_FLAGS:
            assert getattr(args, dest) is None


def test_config_after_subcommand_reaches_the_built_config(tmp_path):
    """End-to-end for item 1: the flag doesn't just parse, it lands in Config."""
    from dstf.cli.common import _build_config

    lib = _fake_library(tmp_path)
    cfg = tmp_path / "testing.json"
    cfg.write_text(json.dumps({"source_path": "FakeLib"}), encoding="utf-8")
    args = build_arg_parser().parse_args(["run", "--config", str(cfg)])
    assert _build_config(args).source_path == lib


# --- 2. ConfigError instead of raw tracebacks ----------------------------


def test_missing_filter_file_is_a_config_error(tmp_path, capsys):
    """`--filter @typo.txt` used to raise FileNotFoundError out of
    _resolve_filter_patterns and print a traceback."""
    from dstf.config import ConfigError

    with pytest.raises(ConfigError, match="Filter file not found"):
        _resolve_filter_patterns(f"@{tmp_path / 'nope.txt'}")

    rc = main(
        [
            "--source-path",
            str(_fake_library(tmp_path)),
            "discover",
            "--filter",
            f"@{tmp_path / 'nope.txt'}",
        ]
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert err.startswith("Config error: Filter file not found")
    assert "Traceback" not in err


def test_missing_modelica_package_is_a_config_error(tmp_path, capsys):
    """A source path with no package.mo anywhere used to surface
    find_package_dir's FileNotFoundError as a traceback."""
    empty = tmp_path / "empty"
    empty.mkdir()
    (tmp_path / "testing.json").write_text(
        json.dumps({"source_path": str(empty)}), encoding="utf-8"
    )
    rc = main(["discover", "--config", str(tmp_path / "testing.json")])
    assert rc == 2
    err = capsys.readouterr().err
    assert err.startswith("Config error: ")
    assert "package.mo" in err
    assert "Traceback" not in err


@pytest.mark.parametrize(
    ("argv", "missing"),
    [
        (["companion", "add"], "model, name, path"),
        (["companion", "add", "Lib.M"], "name, path"),
        (["companion", "freeze", "Lib.M"], "name"),
        (["companion", "remove"], "model, name"),
        (["soft-check", "add", "Lib.M", "om"], "source"),
        (["soft-check", "remove"], "model, name"),
        (["spec", "add"], "model"),
        (["spec", "update"], "patch.json"),
    ],
)
def test_missing_positionals_are_the_parser_s_usage_error(
    argv, missing, tmp_path, capsys
):
    """Each action is its own parser, so what it needs is required BY the
    parser: the usage line of that action, the names missing, exit 2 — and
    no Config built (a build can write a default testing.json)."""
    with pytest.raises(SystemExit) as exc:
        main(["--source-path", str(_fake_library(tmp_path)), *argv])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert f"usage: dstf {' '.join(argv[:2])} " in err
    assert f"the following arguments are required: {missing}" in err
    assert not list(tmp_path.rglob("testing.json"))


@pytest.mark.parametrize("group", ["companion", "soft-check"])
def test_a_list_still_works_without_positionals(group, tmp_path, capsys):
    """`list` legitimately takes none: every test's."""
    rc = main(["--source-path", str(_fake_library(tmp_path)), group, "list"])
    assert rc == 0
    assert "Config error" not in capsys.readouterr().err


def test_malformed_metrics_block_is_a_config_error(tmp_path, capsys):
    """A bad `metrics` tree in test_spec.json raised MetricSpecError out of
    the parser. It stays loud at discovery — just as a user error."""
    lib = _fake_library(tmp_path)
    spec = tmp_path / "test_spec.json"
    spec.write_text(
        json.dumps(
            {
                "tests": [
                    {
                        "model": "FakeLib.M",
                        "variables": ["x"],
                        "metrics": {"combinator": "no-such-combinator", "of": []},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    rc = main(["discover", "--source-path", str(lib), "--test-spec", str(spec)])
    assert rc == 2
    err = capsys.readouterr().err
    assert err.startswith("Config error: ")
    assert "test_spec.json" in err
    assert "Traceback" not in err


# --- 3. dry-run is the universal destructive default ---------------------


def _obsolete_ref(tmp_path):
    """A reference root holding one obsolete ref_NNNN.json."""
    from dstf.config import detect_os as _detect_os

    ref_dir = tmp_path / "refs" / "Dymola" / _detect_os()
    ref_dir.mkdir(parents=True)
    (ref_dir / "ref_0001.json").write_text(
        json.dumps(
            {
                "test_id": 1,
                "model_id": "FakeLib.Gone",
                "status": "obsolete",
                "time": [0.0, 1.0],
                "variables": [],
            }
        ),
        encoding="utf-8",
    )
    return ref_dir / "ref_0001.json"


def test_manifest_cleanup_is_a_dry_run_by_default(tmp_path, capsys):
    """`manifest cleanup` deleted obsolete reference files on sight while
    `--orphans` dry-ran. Unified: nothing is removed without --apply."""
    lib = _fake_library(tmp_path)
    ref_file = _obsolete_ref(tmp_path)
    rc = main(
        [
            "--source-path",
            str(lib),
            "--reference-root",
            str(tmp_path / "refs"),
            "manifest",
            "cleanup",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "Dry run" in out
    assert "ref_0001.json" in out
    assert ref_file.exists(), "dry run must not delete anything"


def test_manifest_cleanup_apply_removes(tmp_path, capsys):
    lib = _fake_library(tmp_path)
    ref_file = _obsolete_ref(tmp_path)
    rc = main(
        [
            "--source-path",
            str(lib),
            "--reference-root",
            str(tmp_path / "refs"),
            "manifest",
            "cleanup",
            "--apply",
        ]
    )
    assert rc == 0
    assert "Removed 1 obsolete reference files." in capsys.readouterr().out
    assert not ref_file.exists()


def test_manifest_cleanup_with_nothing_to_do(tmp_path, capsys):
    lib = _fake_library(tmp_path)
    rc = main(
        [
            "--source-path",
            str(lib),
            "--reference-root",
            str(tmp_path / "refs"),
            "manifest",
            "cleanup",
        ]
    )
    assert rc == 0
    assert "No obsolete references" in capsys.readouterr().out


# --- 4. --parallel disambiguation ----------------------------------------


def test_parallel_help_disambiguates_run_from_compare():
    """One flag name, two meanings. Each subparser's help says which, and
    points at the other — the rename was explicitly ruled out."""
    parser = build_arg_parser()
    subparsers_action = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    )

    def _parallel_help(command):
        sub = subparsers_action.choices[command]
        return next(a for a in sub._actions if a.dest == "parallel").help

    run_help = _parallel_help("run")
    compare_help = _parallel_help("compare")
    assert "SIMULATION" in run_help and "compare" in run_help
    assert "RENDERING" in compare_help and "run" in compare_help


# --- 8. compare takes its backend from the run's metadata ----------------


def _work_dir_with_metadata(tmp_path, backend):
    work = tmp_path / "work"
    work.mkdir(parents=True, exist_ok=True)
    (work / "status.json").write_text(
        json.dumps(
            {"tests": [], "metadata": {"backend_name": backend, "backend": backend}}
        ),
        encoding="utf-8",
    )
    return work


def test_compare_uses_the_backend_recorded_by_the_run(tmp_path):
    """`compare` auto-detected a backend from the environment, so comparing
    an OpenModelica work dir on a machine with Dymola read the Dymola
    reference partition and reported SIM_FAIL for everything."""
    from dstf.backends.progress import recorded_backend

    assert recorded_backend(_work_dir_with_metadata(tmp_path, "OpenModelica"))[0] == (
        "OpenModelica"
    )


def test_compare_backend_metadata_absent_falls_back(tmp_path):
    """No snapshot, or one written before provenance was stamped → today's
    auto-detect, unchanged."""
    from dstf.backends.progress import recorded_backend

    work = tmp_path / "work"
    work.mkdir()
    assert recorded_backend(work)[0] is None
    (work / "status.json").write_text(
        json.dumps({"tests": [], "metadata": None}), encoding="utf-8"
    )
    assert recorded_backend(work)[0] is None
    (work / "status.json").write_text("{not json", encoding="utf-8")
    assert recorded_backend(work)[0] is None


def test_unregistered_recorded_backend_is_a_config_error(tmp_path, capsys):
    """Review K2: status.json is data, and a recorded label this install
    can't resolve (an unloaded plug-in, a snapshot copied from another
    machine) flowed into get_backend_class's bare ValueError, which main()
    does not catch — a traceback on someone else's data."""
    lib = _fake_library(tmp_path)
    work = _work_dir_with_metadata(tmp_path, "NoSuchBackend")

    rc = main(["--source-path", str(lib), "compare", "--work-dir", str(work)])
    assert rc == 2
    err = capsys.readouterr().err
    assert err.startswith("Config error: ")
    assert "NoSuchBackend" in err
    assert "--backend" in err
    assert "Traceback" not in err


def test_compare_prints_the_stale_work_dir_warning(tmp_path, capsys):
    """The warning is `recorded_backend`'s return value now; `cmd_compare`
    is what puts it in front of the user. Dropping that print would let
    `compare` fall back to auto-detect on a pre-D123 work dir with nothing
    said — the failure D123 records."""
    lib = _fake_library(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    (work / "status.json").write_text(
        json.dumps({"tests": [], "metadata": {"simulator": "OpenModelica"}}),
        encoding="utf-8",
    )
    main(["--source-path", str(lib), "compare", "--work-dir", str(work)])
    err = capsys.readouterr().err
    assert "predates the Backend rename" in err and "--backend" in err


def test_stale_work_dir_without_backend_name_warns(tmp_path):
    """A work dir written before the Backend rename must say so.

    Its status.json records the backend under the old key, so
    recorded_backend cannot read it and the D120 guard silently stops
    firing — `compare` falls back to auto-detect and could score an
    OpenModelica work dir against the Dymola partition, SIM_FAIL for every
    test, with nothing said. Reading the old key would be a compatibility
    path; warning is not.
    """
    from dstf.backends.progress import recorded_backend

    work = tmp_path / "work"
    work.mkdir()
    (work / "status.json").write_text(
        json.dumps({"tests": [], "metadata": {"simulator": "OpenModelica"}}),
        encoding="utf-8",
    )

    label, warning = recorded_backend(work)
    assert label is None
    assert "predates the Backend rename" in warning
    assert "--backend" in warning


def test_work_dir_with_no_metadata_at_all_is_silent(tmp_path):
    """Only a STALE stamp warrants the warning; an unstamped run is normal."""
    from dstf.backends.progress import recorded_backend

    work = tmp_path / "work"
    work.mkdir()
    (work / "status.json").write_text(json.dumps({"tests": []}), encoding="utf-8")

    assert recorded_backend(work) == (None, None)


def test_an_explicit_backend_bypasses_the_recorded_backend_check(tmp_path, capsys):
    """--backend still wins outright: the adoption branch (and therefore
    its validation) is skipped entirely."""
    lib = _fake_library(tmp_path)
    work = _work_dir_with_metadata(tmp_path, "NoSuchBackend")

    main(
        [
            "--source-path",
            str(lib),
            "compare",
            "--work-dir",
            str(work),
            "--backend",
            "Dymola",
        ]
    )
    err = capsys.readouterr().err
    assert "NoSuchBackend" not in err


def test_id_mapping_written_with_lf(tmp_path, monkeypatch):
    """reference_manifest.json is machine-readable output a user may keep,
    so it gets LF like every other JSON we write.

    A byte assertion cannot fail on Linux — CPython
        compiles the CRLF write translation in rather than reading
        os.linesep — so pin the argument at the call site instead.
    """

    class _Index:
        def active_tests(self):
            return {"0001": "Lib.A"}

    class _Store:
        index = _Index()

    class _Config:
        work_dir = tmp_path / "work"

    seen: dict[str, object] = {}
    real = Path.write_text

    def spy(self, data, *args, **kwargs):
        seen[self.name] = kwargs.get("newline")
        return real(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", spy)
    write_id_mapping(_Store(), _Config())

    assert seen["reference_manifest.json"] == "\n"


# =========================================================================
# --no-browser: `run` and `compare` open no tab, and still print the link
# =========================================================================


class TestNoBrowser:
    @pytest.fixture
    def lib(self, tmp_path):
        from conftest import python_example_copy

        config, _ = python_example_copy(tmp_path)

        def dstf(command: str, *extra: str) -> int:
            return main(
                ["--config", str(config.config_file), command]
                + ["--work-dir", str(config.work_dir), *extra]
            )

        return dstf, config.work_dir / "dashboard.html"

    def test_the_opener_itself(self, tmp_path, capsys, launched_openers):
        from dstf.reporting.report_suite import open_in_browser

        page = tmp_path / "p.html"
        open_in_browser(page, launch=False)
        assert launched_openers == []
        assert capsys.readouterr().out == f"View: {page.resolve().as_uri()}\n"
        open_in_browser(page)
        assert launched_openers == [page]

    @pytest.mark.parametrize(
        "command,extra,opened",
        [
            ("run", [], 1),  # the live dashboard, before the run
            ("run", ["--report"], 2),  # and the report, after it
            ("compare", [], 0),
            ("compare", ["--report"], 1),
        ],
    )
    def test_the_default_opens_the_dashboard(
        self, lib, launched_openers, command, extra, opened
    ):
        dstf, dashboard = lib
        if command == "compare":
            assert dstf("run", "--no-browser") == 0
            launched_openers.clear()
        assert dstf(command, *extra) == 0
        assert launched_openers == [dashboard] * opened

    @pytest.mark.parametrize(
        "command,extra",
        [("run", []), ("run", ["--report"]), ("compare", ["--report"])],
    )
    def test_no_browser_opens_nothing_and_prints_the_link(
        self, lib, launched_openers, capsys, command, extra
    ):
        dstf, dashboard = lib
        if command == "compare":
            assert dstf("run", "--no-browser") == 0
        capsys.readouterr()
        assert dstf(command, *extra, "--no-browser") == 0
        assert launched_openers == []
        assert f"View: {dashboard.resolve().as_uri()}" in capsys.readouterr().out

    def test_the_review_plot_follows_the_flag(
        self, lib, launched_openers, monkeypatch, capsys
    ):
        """`p` in the interactive review writes the test's page; under
        --no-browser it prints the link and opens nothing."""
        dstf, _ = lib
        answers = iter(["p", "q"])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
        dstf("run", "-i", "all", "--no-browser")
        assert launched_openers == []
        out = capsys.readouterr().out
        assert "Plots saved to" in out and out.count("View: file://") == 2


# =========================================================================
# The console verdict table, --report's pages and --report-format's file
# are three independent things: any combination does all of what it asks
# =========================================================================


@pytest.fixture
def scored(run_env):
    """Two tests with a baseline; T1 will pass and T2 fail. Returns a runner
    of one command with the given flags."""
    tests = [_T1, _T2]
    run_env.make(tests, results={m: _mk_result(m) for m in (_T1, _T2)})
    assert cmd_run(_run_args(run_env, "--accept")) == 0
    results = {
        _T1: _mk_result(_T1),
        _T2: _mk_result(_T2, values=np.cos(np.linspace(0.0, 1.0, 11))),
    }

    def dstf(command: str, *extra: str) -> int:
        run_env.make(tests, results=results, prior_results=results)
        args = build_arg_parser().parse_args(
            ["--config", str(run_env.config_dir), command]
            + ["--work-dir", str(run_env.work_dir), "--no-browser", *extra]
        )
        return (cmd_run if command == "run" else cmd_compare)(args)

    return dstf


def _pages(run_env) -> int:
    return len(list(run_env.work_dir.glob("reports/*/interactive.html")))


class TestTheThreeReportsAreIndependent:
    @pytest.mark.parametrize("command", ["run", "compare"])
    @pytest.mark.parametrize("report", [False, True])
    @pytest.mark.parametrize("fmt", ["console", "junit", "html"])
    def test_every_combination_does_all_it_asks(
        self, scored, run_env, tmp_path, capsys, command, report, fmt
    ):
        out_file = tmp_path / f"out.{fmt}"
        extra = ["--report"] if report else []
        if fmt != "console":
            extra += ["--report-format", fmt, "--output", str(out_file)]
        capsys.readouterr()
        assert scored(command, *extra) == 1  # T2 fails, on every route
        out = capsys.readouterr().out

        # the verdict table, always
        lines = out.splitlines()
        assert [ln for ln in lines if ln.endswith(_T1)] == [f"  PASS  [0001] {_T1}"]
        assert [ln for ln in lines if ln.endswith(_T2)][0] == f"  FAIL  [0002] {_T2}"
        assert "Results: 1/2 passed" in lines
        assert "Failure Details:" in lines

        # --report's pages, when asked and only then
        assert _pages(run_env) == (2 if report else 0)
        assert ("Report: " in out) is report

        # --report-format's file, when asked and only then
        assert out_file.exists() is (fmt != "console")
        if fmt == "junit":
            assert ET.parse(out_file).getroot().get("failures") == "1"
            assert f"JUnit report written to {out_file}" in lines
        if fmt == "html":
            assert _T2 in out_file.read_text(encoding="utf-8")
            assert f"HTML report written to {out_file}" in lines

    def test_an_html_file_written_with_the_pages_links_them(
        self, scored, run_env, capsys
    ):
        """`--report --report-format html --output <work_dir>/r.html`: the
        file is written AFTER the pages, so its rows link them."""
        out_file = run_env.work_dir / "r.html"
        scored(
            "compare", "--report", "--report-format", "html", "--output", str(out_file)
        )
        assert out_file.read_text(encoding="utf-8").count("/interactive.html") >= 2

    def test_nothing_to_rerun_still_writes_what_it_was_asked_for(
        self, scored, run_env, tmp_path, capsys
    ):
        """`run --rerun sim-failed` where nothing failed to simulate runs
        nothing and exits 0 — and still prints the verdicts as they stand
        and writes the junit file it was asked for."""
        out_file = tmp_path / "j.xml"
        capsys.readouterr()
        rc = scored(
            "run",
            "--rerun",
            "sim-failed",
            "--report-format",
            "junit",
            "--output",
            str(out_file),
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "Nothing to rerun" in out and "Results: 1/2 passed" in out
        assert ET.parse(out_file).getroot().get("failures") == "1"


# =========================================================================
# A run or compare without --report removes the per-test pages of the tests
# in its scope (a stale page must not survive) — and says that it did
# =========================================================================

_REMOVED = (
    "Removed {n} per-test report page{s} written by an earlier --report (now "
    "stale); `compare --report` writes {them} again."
)


def _removed_lines(out: str) -> list[str]:
    return [ln for ln in out.splitlines() if ln.startswith("Removed ")]


class TestRemovedReportPagesAreSaid:
    @pytest.mark.parametrize("command", ["run", "compare"])
    def test_the_pages_go_and_one_line_says_how_many(
        self, scored, run_env, capsys, command
    ):
        scored("run", "--report")
        assert _pages(run_env) == 2
        capsys.readouterr()
        scored(command)
        assert _pages(run_env) == 0
        assert _removed_lines(capsys.readouterr().out) == [
            _REMOVED.format(n=2, s="s", them="them")
        ]
        # nothing left to remove: nothing said
        scored(command)
        assert _removed_lines(capsys.readouterr().out) == []

    @pytest.mark.parametrize("command", ["run", "compare"])
    def test_pages_that_are_written_again_are_not_announced(
        self, scored, run_env, capsys, command
    ):
        scored("run", "--report")
        capsys.readouterr()
        scored(command, "--report")
        assert _pages(run_env) == 2
        assert _removed_lines(capsys.readouterr().out) == []

    def test_one_page_is_one_page(self, scored, run_env, capsys):
        scored("run", "--report")
        capsys.readouterr()
        scored("run", "--filter", _T1)
        assert _pages(run_env) == 1  # T2's is out of scope and stays
        assert _removed_lines(capsys.readouterr().out) == [
            _REMOVED.format(n=1, s="", them="it")
        ]

    @pytest.mark.parametrize("command", ["run", "compare"])
    def test_an_accept_writes_no_pages_and_says_so_too(
        self, scored, run_env, capsys, command
    ):
        """`--accept` writes no report (`--report` beside it is the parser's
        error): the pages are gone, and the line says so."""
        scored("run", "--report")
        capsys.readouterr()
        scored(command, "--accept")
        assert _pages(run_env) == 0
        assert _removed_lines(capsys.readouterr().out) == [
            _REMOVED.format(n=2, s="s", them="them")
        ]

    def test_a_review_writes_no_pages_and_says_so_too(
        self, scored, run_env, capsys, monkeypatch
    ):
        scored("run", "--report")
        capsys.readouterr()
        monkeypatch.setattr("builtins.input", lambda prompt="": "s")
        scored("run", "-i", "all")
        assert _pages(run_env) == 0
        assert _removed_lines(capsys.readouterr().out) == [
            _REMOVED.format(n=2, s="s", them="them")
        ]


# =========================================================================
# A test whose stop time came from nowhere runs to the framework's default,
# and `run` says so before it starts — with the reason that is true of it
# =========================================================================


class TestRunSaysWhenAStopTimeIsTheDefault:
    NEITHER = (
        "{model}: neither test_spec.json nor the model's experiment annotation "
        "gives a stop time; it runs to the default, 1 s."
    )
    NOT_FOUND = (
        "{model}: not found under source_path, so its experiment annotation "
        "was not read; it runs to the default, 1 s."
    )
    DEPENDENCY = "Modelica.Blocks.Examples.PID_Controller"

    @staticmethod
    def _said(out: str) -> list[str]:
        return [ln for ln in out.splitlines() if "runs to the default" in ln]

    def _made(self, run_env):
        """A Modelica library of three models: T1 gives no stop time, T2's
        annotation gives 1 s, T3's spec entry gives 1 s — and a spec entry
        for a class that is not in the library."""
        lib = run_env.config_dir / "Lib"
        lib.mkdir()
        (lib / "package.mo").write_text("package Lib\nend Lib;\n", encoding="utf-8")
        annotations = {
            "T1": "",
            "T2": "  annotation(experiment(StopTime=1.0));\n",
            "T3": "",
        }
        for name, annotation in annotations.items():
            (lib / f"{name}.mo").write_text(
                f"within Lib;\nmodel {name}\n  Real x = time;\n{annotation}end {name};\n",
                encoding="utf-8",
            )
        (run_env.config_dir / "testing.json").write_text(
            json.dumps({"source_path": "Lib", "backend": FAKE}), encoding="utf-8"
        )
        models = [_T1, _T2, "Lib.T3", self.DEPENDENCY]
        run_env.make(
            [
                _T1,
                _T2,
                {
                    "model": "Lib.T3",
                    "variables": ["x"],
                    "simulation": {"stop_time": 1.0},
                },
                # declared in the spec only, and not a class of this library
                self.DEPENDENCY,
            ],
            results={m: _mk_result(m) for m in models},
        )

    def test_one_line_names_the_model_and_the_default(self, run_env, capsys):
        self._made(run_env)
        cmd_run(_run_args(run_env, "--no-browser", "--filter", "Lib.*"))
        assert self._said(capsys.readouterr().out) == [self.NEITHER.format(model=_T1)]

    def test_a_model_that_was_not_found_is_not_said_to_have_no_annotation(
        self, run_env, capsys
    ):
        """`Modelica.Blocks.Examples.PID_Controller` says `StopTime=4`: its
        file is a dependency's and was never read. The line said its
        annotation gave no stop time."""
        self._made(run_env)
        cmd_run(_run_args(run_env, "--no-browser", "--filter", "Modelica.*"))
        assert self._said(capsys.readouterr().out) == [
            self.NOT_FOUND.format(model=self.DEPENDENCY)
        ]

    def test_only_the_tests_that_run_are_named(self, run_env, capsys):
        self._made(run_env)
        cmd_run(_run_args(run_env, "--no-browser", "--filter", _T2))
        assert self._said(capsys.readouterr().out) == []

    def test_a_non_modelica_source_is_told_too_and_not_about_an_annotation(
        self, tmp_path, capsys
    ):
        """The Python example with a `simulation` block removed: it runs to
        the same default, and the line speaks of no model's annotation —
        there is no such layer for it."""
        from conftest import python_example_copy

        config, _ = python_example_copy(tmp_path)
        spec = json.loads(config.spec_path.read_text(encoding="utf-8"))
        del spec["tests"][0]["simulation"]
        model = spec["tests"][0]["model"]
        config.spec_path.write_text(json.dumps(spec), encoding="utf-8")
        main(
            ["--config", str(config.config_file), "run", "--no-browser"]
            + ["--work-dir", str(config.work_dir)]
        )
        assert self._said(capsys.readouterr().out) == [
            f"{model}: test_spec.json gives no stop time; it runs to the default, 1 s."
        ]


def test_compare_reads_backend_path_for_a_backend_built_from_its_executable(
    tmp_path, capsys
):
    """`compare` starts no simulator, but the Python and Julia backends are
    not built without their executable: with the configured interpreter
    gone, `compare` is refused, and `--backend-path` names one that is
    there. The flag is read on `compare` for them."""
    import json
    import shutil

    from conftest import python_example_copy

    config, _ = python_example_copy(tmp_path)
    cfg = Path(config.config_file)
    work = ["--work-dir", str(config.work_dir)]
    assert main(["run", "--config", str(cfg), *work, "--no-browser"]) == 0
    data = json.loads(cfg.read_text(encoding="utf-8"))
    data["backends"]["Python"] = {"executables": [str(tmp_path / "gone" / "python")]}
    cfg.write_text(json.dumps(data), encoding="utf-8")
    capsys.readouterr()
    with pytest.raises(SystemExit) as refused:
        main(["compare", "--config", str(cfg), *work, "--no-browser"])
    assert refused.value.code == 1
    assert "Python binary not found" in capsys.readouterr().err
    python = shutil.which("python3") or sys.executable
    rc = main(
        ["compare", "--config", str(cfg), *work, "--no-browser"]
        + ["--backend-path", python]
    )
    assert rc == 0


# =========================================================================
# run and compare over the registered backend: what they say and write
# =========================================================================

_ORPHAN_NOTE = (
    "Note: 1 orphan manifest entries (models no longer in discovery). "
    "Run 'manifest cleanup --orphans' to list/prune."
)


def _with_an_orphan(env):
    BatchManifest(
        work_dir=env.work_dir,
        manifest={"test_0009": {"model_id": "Lib.Gone", "ref_id": None}},
    ).save()


@pytest.mark.parametrize("command", ["run", "compare"])
def test_a_manifest_entry_no_longer_discovered_is_noted(run_env, capsys, command):
    run_env.make(["Lib.T1"], results={"Lib.T1": _mk_result("Lib.T1")})
    _with_an_orphan(run_env)
    main(
        ["--config", str(run_env.config_dir), command]
        + ["--work-dir", str(run_env.work_dir), "--no-browser"]
    )
    assert _ORPHAN_NOTE in capsys.readouterr().out.splitlines()


def test_a_run_with_no_orphan_says_nothing_of_orphans(run_env, capsys):
    run_env.make(["Lib.T1"], results={"Lib.T1": _mk_result("Lib.T1")})
    cmd_run(_run_args(run_env, "--no-browser"))
    assert "orphan manifest entries" not in capsys.readouterr().out


def test_an_aborted_run_says_why(run_env, capsys):
    behaviour = run_env.make(["Lib.T1"], results={})

    def boom(tests):
        raise RuntimeError("all workers failed to start")

    behaviour.run_tests = boom
    assert cmd_run(_run_args(run_env, "--no-browser")) == 1
    assert "Run aborted: all workers failed to start" in capsys.readouterr().err


def test_a_rerun_keeps_a_still_failing_test_in_its_verdict_and_exits_one(
    run_env, capsys
):
    """`--rerun` reads back the tests it did not re-run: the one still
    failing keeps the run red."""
    t1, t2 = "Lib.T1", "Lib.T2"
    behaviour = run_env.make([t1, t2], results={t1: _mk_result(t1), t2: _mk_result(t2)})
    assert cmd_run(_run_args(run_env, "--accept", "--no-browser")) == 0
    behaviour.prior_results = {
        t1: _mk_result(t1, success=False),
        t2: _mk_result(t2, values=[1.0] * 11),
    }
    behaviour.results = {t1: _mk_result(t1), t2: _mk_result(t2, values=[1.0] * 11)}
    behaviour.manifest_map = {
        "test_0001": {"model_id": t1, "ref_id": None},
        "test_0002": {"model_id": t2, "ref_id": None},
    }
    rc = cmd_run(_run_args(run_env, "--rerun", "sim-failed", "--no-browser"))
    verdicts = json.loads((run_env.work_dir / "verdicts.json").read_text())
    assert [(t["model_id"], t["status"]) for t in verdicts["tests"]] == [
        (t1, "PASS"),
        (t2, "FAIL"),
    ]
    assert rc == 1
    assert "Results: 1/2 passed" in capsys.readouterr().out


def test_a_filtered_rerun_with_nothing_to_rerun_reports_every_tests_last_verdict(
    run_env, capsys
):
    """The filter picks what may be re-run; the verdicts are every test's."""
    t1, t2 = "Lib.T1", "Lib.T2"
    behaviour = run_env.make([t1, t2], results={t1: _mk_result(t1), t2: _mk_result(t2)})
    assert cmd_run(_run_args(run_env, "--accept", "--no-browser")) == 0
    behaviour.prior_results = {
        t1: _mk_result(t1),
        t2: _mk_result(t2, values=[1.0] * 11),
    }
    rc = cmd_run(
        _run_args(run_env, "--rerun", "failed", "--filter", t1, "--no-browser")
    )
    verdicts = json.loads((run_env.work_dir / "verdicts.json").read_text())
    assert rc == 0
    assert "Nothing to rerun" in capsys.readouterr().out
    assert [(t["model_id"], t["status"]) for t in verdicts["tests"]] == [
        (t1, "PASS"),
        (t2, "FAIL"),
    ]


@pytest.mark.parametrize("command", ["run --rerun", "compare"])
def test_the_id_mapping_is_written_by_a_command_that_runs_nothing(run_env, command):
    run_env.make(["Lib.T1"], results={"Lib.T1": _mk_result("Lib.T1")})
    assert cmd_run(_run_args(run_env, "--accept", "--no-browser")) == 0
    mapping = run_env.work_dir / "reference_manifest.json"
    mapping.unlink(missing_ok=True)
    run_env.behaviour.prior_results = {"Lib.T1": _mk_result("Lib.T1")}
    argv = ["run", "--rerun", "failed"] if command == "run --rerun" else ["compare"]
    main(
        ["--config", str(run_env.config_dir), *argv]
        + ["--work-dir", str(run_env.work_dir), "--no-browser"]
    )
    assert json.loads(mapping.read_text(encoding="utf-8")) == {"ref_0001": "Lib.T1"}


def test_the_live_page_is_there_when_the_tab_opens_on_it(run_env, monkeypatch):
    run_env.make(["Lib.T1"], results={"Lib.T1": _mk_result("Lib.T1")})
    there: list[bool] = []
    monkeypatch.setattr(
        "dstf.reporting.report_suite._launch_opener",
        lambda path: there.append(Path(path).exists()),
    )
    cmd_run(_run_args(run_env))
    assert there == [True]


@pytest.mark.parametrize("inside", [True, False], ids=["inside", "outside"])
def test_a_page_written_outside_the_work_dir_says_its_links_will_not_resolve(
    run_env, capsys, inside
):
    run_env.make(["Lib.T1"], results={"Lib.T1": _mk_result("Lib.T1")})
    out = (run_env.work_dir if inside else run_env.config_dir) / "page.html"
    cmd_run(
        _run_args(
            run_env, "--report-format", "html", "--output", str(out), "--no-browser"
        )
    )
    note = (
        "note: per-test report links (and the Plotly they load from reports/) "
        f"resolve only inside {run_env.work_dir}"
    )
    assert (note in capsys.readouterr().err.splitlines()) is not inside


def test_the_page_names_the_project_it_reruns_with(run_env):
    """A backend that writes no status.json of its own: the page's commands
    still name this project's testing.json and work dir."""
    run_env.make(["Lib.T1"], results={"Lib.T1": _mk_result("Lib.T1")})
    out = run_env.work_dir / "page.html"
    cmd_run(
        _run_args(
            run_env, "--report-format", "html", "--output", str(out), "--no-browser"
        )
    )
    page = out.read_text(encoding="utf-8")
    assert str(run_env.config_dir / "testing.json") in page
    assert f"--work-dir {run_env.work_dir}" in page


@pytest.mark.parametrize("rerun", [False, True], ids=["run", "nothing to rerun"])
def test_a_project_with_no_testing_json_gets_the_default_one(
    run_env, tmp_path, monkeypatch, rerun
):
    root = tmp_path / "project"
    lib = root / "Lib"
    (lib / "Resources" / "ReferenceResults").mkdir(parents=True)
    (lib / "package.mo").write_text(
        "package Lib\n  model T1\n    Real x;\n  equation\n    x = time;\n"
        "  end T1;\nend Lib;\n"
    )
    for spec_dir in (lib, root):
        (spec_dir / "Resources" / "ReferenceResults").mkdir(parents=True, exist_ok=True)
        (spec_dir / "Resources" / "ReferenceResults" / "test_spec.json").write_text(
            json.dumps({"tests": [{"model": "Lib.T1", "variables": ["x"]}]})
        )
    run_env.make(["Lib.T1"], results={"Lib.T1": _mk_result("Lib.T1")})
    run_env.behaviour.prior_results = {"Lib.T1": _mk_result("Lib.T1")}
    monkeypatch.chdir(lib)
    argv = ["run", "--backend", FAKE, "--work-dir", str(root / "work")]
    main(argv + ["--no-browser"] + (["--rerun", "failed"] if rerun else []))
    assert sorted(p.relative_to(root) for p in root.rglob("testing.json")) == [
        Path("testing.json")
    ]


# ---------------------------------------------------------------------------
# run_pipeline, the library callable, over the registered backend
# ---------------------------------------------------------------------------


def _pipeline(env, work_dir=None):
    from dstf.backends import select_backend
    from dstf.discovery.test_registry import discover_tests

    config = Config(
        config_file=env.config_dir / "testing.json",
        work_dir=work_dir or env.work_dir,
    )
    return (
        config,
        discover_tests(config),
        select_backend(config, persistent=False, to_run=True),
        ReferenceStore(config),
    )


def test_a_library_run_without_merge_scopes_the_tests_it_was_given(run_env):
    from dstf.pipeline import run_pipeline

    t1, t2 = "Lib.T1", "Lib.T2"
    behaviour = run_env.make([t1, t2], results={t1: _mk_result(t1), t2: _mk_result(t2)})
    behaviour.manifest_map = {
        "test_0001": {"model_id": t1, "ref_id": None},
        "test_0002": {"model_id": t2, "ref_id": None},
    }
    config, tests, backend, store = _pipeline(run_env)
    out = run_pipeline(
        config, tests[:1], backend=backend, store=store, candidates=tests
    )
    assert [t.model_id for t in out.scope_tests] == [t1]


def test_a_library_run_makes_its_work_dir(run_env, tmp_path):
    from dstf.pipeline import run_pipeline

    run_env.make(["Lib.T1"], results={"Lib.T1": _mk_result("Lib.T1")})
    work = tmp_path / "x" / "y" / "work"
    config, tests, backend, store = _pipeline(run_env, work)
    out = run_pipeline(config, tests, backend=backend, store=store)
    assert list(out.results) == ["Lib.T1"]
    assert work.is_dir()


def test_a_library_run_clears_the_tests_last_run_and_paints_the_page_first(run_env):
    from dstf.pipeline import run_pipeline

    behaviour = run_env.make(["Lib.T1"], results={"Lib.T1": _mk_result("Lib.T1")})
    BatchManifest(
        work_dir=run_env.work_dir,
        manifest={"test_0001": {"model_id": "Lib.T1", "ref_id": None}},
    ).save()
    stale = run_env.work_dir / "test_0001" / "stale.mat"
    stale.parent.mkdir()
    stale.write_text("old")
    seen = {}
    run = behaviour.run_tests

    def looking(tests):
        seen["stale"] = stale.exists()
        seen["page"] = (run_env.work_dir / "dashboard.html").exists()
        return run(tests)

    behaviour.run_tests = looking
    config, tests, backend, store = _pipeline(run_env)
    run_pipeline(config, tests, backend=backend, store=store)
    assert seen == {"stale": False, "page": True}


def test_a_requested_chain_the_backend_cannot_run_is_said(run_env):
    from dstf.pipeline import run_pipeline

    run_env.make(["Lib.T1"], results={"Lib.T1": _mk_result("Lib.T1")})
    config, tests, backend, store = _pipeline(run_env)
    tests[0].requested_baselines = ["dymola-via-fmpy"]
    out = run_pipeline(config, tests, backend=backend, store=store)
    assert out.notices[1:] == [
        "Running cross-backend chains for 1 test(s)...",
        "  Lib.T1: chain skipped (see logs)",
        "Cross-backend chains: 0/1 succeeded",
    ]
