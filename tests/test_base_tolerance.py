"""A test's BASE tolerance is one finite number, judged once, before anything
else a comparison decides — through the CLI, over a real Python-backend
project: every shape a JSON file can hold there, with and without a stored
baseline, on the tree path, the flat path and a baseline-free flat test."""

from __future__ import annotations

import json
import shutil
import xml.etree.ElementTree as ET

import pytest

from dstf.cli import main

RAMP = "PythonTestingLib.Examples.SimpleRamp"
CSV = "PythonTestingLib.Examples.ConstantCsv"

#: What a JSON file can hold as a base tolerance that is no finite number.
SHAPES = {
    "pair": {"lower": 0.1, "upper": 0.2},
    "one-sided": {"lower": 0.1},
    "digits": "0.5",
    "string": "abc",
    "bool": True,
    "list": [1],
}
#: Python's ``json`` reads these three literals; no other shape is non-finite.
NON_FINITE = {"nan": "NaN", "inf": "Infinity", "huge-int": "1" + "0" * 400}

#: A test's criteria, as its spec entry holds them: (model, entry fields).
KINDS = {
    "tree": (RAMP, {"metrics": {"metric": "nrmse", "variable": "x"}}),
    "flat": (RAMP, {}),
    "baseline-free flat": (CSV, {}),
    "baseline-free tree": (
        CSV,
        {"metrics": {"metric": "range", "variable": "x", "min_value": 0.99}},
    ),
}


def sentence(source: str, value: object) -> str:
    """The refusal: the value as the JSON file holds it, cut at 60
    characters; a dict alone is told where a pair belongs."""
    text = json.dumps(value)
    if len(text) > 60:
        text = text[:60] + "…"
    pair = "; a lower/upper pair belongs on a criterion"
    return (
        f"the base tolerance must be a number{pair if isinstance(value, dict) else ''}"
        f" ({source} is {text})"
    )


SPEC = "test_spec.json comparison.tolerance"
BASELINE = "the baseline's comparison.tolerance"


class Project:
    """The example library, run once; each test starts from its files as
    they were after that run."""

    def __init__(self, config, pristine):
        self.config = config
        self.ref_root = config.config_file.parent
        self.spec = self.ref_root / "test_spec.json"
        self._pristine = pristine

    def reset(self) -> None:
        shutil.rmtree(self.ref_root)
        shutil.copytree(self._pristine, self.ref_root)

    def dstf(self, command: str, *extra: str) -> int:
        return main(
            ["--config", str(self.config.config_file), command]
            + ["--work-dir", str(self.config.work_dir), *extra]
        )

    def reference(self, model_id: str):
        for path in sorted(self.ref_root.rglob("ref_*.json")):
            if json.loads(path.read_text(encoding="utf-8"))["model_id"] == model_id:
                return path
        return None

    def edit_spec(self, model_id: str, raw_tolerance: str | None, **fields) -> None:
        """Set ``model_id``'s entry: ``fields`` over it, and its base
        tolerance as the JSON text ``raw_tolerance`` (a literal such as
        ``NaN`` has no Python value that ``json.dumps`` writes back)."""
        data = json.loads(self.spec.read_text(encoding="utf-8"))
        (entry,) = [e for e in data["tests"] if e["model"] == model_id]
        entry.update(fields)
        if raw_tolerance is not None:
            entry.setdefault("comparison", {})["tolerance"] = "@@BASE@@"
        text = json.dumps(data, indent=2)
        if raw_tolerance is not None:
            text = text.replace('"@@BASE@@"', raw_tolerance)
        self.spec.write_text(text, encoding="utf-8")

    def edit_reference(self, model_id: str, raw_tolerance: str) -> None:
        path = self.reference(model_id)
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("comparison", {})["tolerance"] = "@@BASE@@"
        path.write_text(
            json.dumps(data).replace('"@@BASE@@"', raw_tolerance), encoding="utf-8"
        )

    def junit(self, tmp_path) -> tuple[int, dict[str, tuple[str, str | None]]]:
        """``compare`` to a JUnit file: the exit code and, per test, what its
        case holds — ``("failure", message)``, or ``("pass", None)``."""
        out = tmp_path / "junit.xml"
        rc = self.dstf("compare", "--report-format", "junit", "--output", str(out))
        cases = {}
        for case in ET.parse(out).getroot().iter("testcase"):
            child = next(iter(case), None)
            name = f"{case.get('classname')}.{case.get('name')}"
            cases[name] = (
                ("pass", None) if child is None else (child.tag, child.get("message"))
            )
        return rc, cases


@pytest.fixture(scope="module")
def _ran(tmp_path_factory):
    from conftest import python_example_copy

    root = tmp_path_factory.mktemp("base_tolerance")
    config, _ = python_example_copy(root)
    pristine = root / "pristine"
    project = Project(config, pristine)
    with pytest.MonkeyPatch.context() as m:
        m.setattr("dstf.reporting.report_suite.open_in_browser", lambda p: None)
        assert project.dstf("run") == 0
    shutil.copytree(project.ref_root, pristine)
    return project


@pytest.fixture
def project(_ran, monkeypatch):
    monkeypatch.setattr("dstf.reporting.report_suite.open_in_browser", lambda p: None)
    _ran.reset()
    yield _ran
    _ran.reset()


def _case(cases: dict, model_id: str):
    (found,) = [v for k, v in cases.items() if k.endswith(model_id.split(".")[-1])]
    return found


def test_the_project_compares_clean_as_it_is(project, tmp_path):
    rc, cases = project.junit(tmp_path)
    assert rc == 0 and set(cases.values()) == {("pass", None)}


@pytest.mark.parametrize("kind", list(KINDS))
@pytest.mark.parametrize("baseline", [True, False], ids=["baseline", "no baseline"])
@pytest.mark.parametrize("shape", list(SHAPES))
def test_a_spec_base_that_is_no_number_refuses_the_test(
    project, tmp_path, shape, baseline, kind
):
    model_id, fields = KINDS[kind]
    bad = SHAPES[shape]
    project.edit_spec(model_id, json.dumps(bad), **fields)
    if not baseline:
        project.reference(model_id).unlink()
    rc, cases = project.junit(tmp_path)
    assert _case(cases, model_id) == ("failure", sentence(SPEC, bad))
    assert rc == 1


@pytest.mark.parametrize("kind", list(KINDS))
@pytest.mark.parametrize("baseline", [True, False], ids=["baseline", "no baseline"])
@pytest.mark.parametrize("shape", list(NON_FINITE))
def test_a_spec_base_that_is_not_finite_refuses_the_test(
    project, tmp_path, shape, baseline, kind
):
    model_id, fields = KINDS[kind]
    raw = NON_FINITE[shape]
    project.edit_spec(model_id, raw, **fields)
    if not baseline:
        project.reference(model_id).unlink()
    rc, cases = project.junit(tmp_path)
    assert _case(cases, model_id) == ("failure", sentence(SPEC, json.loads(raw)))
    assert rc == 1


@pytest.mark.parametrize("kind", list(KINDS))
@pytest.mark.parametrize("shape", [*SHAPES, *NON_FINITE])
def test_a_baselines_base_that_is_no_number_refuses_the_test(
    project, tmp_path, shape, kind
):
    model_id, fields = KINDS[kind]
    raw = NON_FINITE.get(shape) or json.dumps(SHAPES[shape])
    project.edit_spec(model_id, None, **fields)
    project.edit_reference(model_id, raw)
    rc, cases = project.junit(tmp_path)
    assert _case(cases, model_id) == ("failure", sentence(BASELINE, json.loads(raw)))
    assert rc == 1


@pytest.mark.parametrize("kind", list(KINDS))
def test_a_number_in_the_spec_shadows_whatever_the_baseline_holds(
    project, tmp_path, kind
):
    """The base in effect is the one judged: the spec's number is what the
    test is scored with, so the baseline's is never read."""
    model_id, fields = KINDS[kind]
    project.edit_spec(model_id, "0.5", **fields)
    project.edit_reference(model_id, '{"lower": 0.1, "upper": 0.2}')
    rc, cases = project.junit(tmp_path)
    assert _case(cases, model_id) == ("pass", None) and rc == 0


@pytest.mark.parametrize("baseline", [True, False], ids=["baseline", "no baseline"])
@pytest.mark.parametrize("raw", ["0", "-1", "0.5", "null"])
def test_a_number_or_no_base_is_never_refused(project, tmp_path, raw, baseline):
    """A baseline-free test reads no tolerance: zero, a negative number and
    an unset base score it exactly as a base of 0.5 does."""
    project.edit_spec(CSV, raw)
    if not baseline:
        project.reference(CSV).unlink()
    rc, cases = project.junit(tmp_path)
    assert _case(cases, CSV) == ("pass", None) and rc == 0


# ---------------------------------------------------------------------------
# Accepting a refused test is refused, on every accept path
# ---------------------------------------------------------------------------

PAIR = {"lower": 0.1, "upper": 0.2}

#: Where the bad base is written, and how the refusal names it.
SOURCES = {"spec": SPEC, "baseline": BASELINE}


def _refuse(project: Project, source: str) -> tuple[str, bytes]:
    """Make RAMP a refused test from ``source``; its sentence and its
    reference file's bytes as accepting must leave them."""
    if source == "spec":
        project.edit_spec(RAMP, json.dumps(PAIR))
    else:
        project.edit_reference(RAMP, json.dumps(PAIR))
    return sentence(SOURCES[source], PAIR), project.reference(RAMP).read_bytes()


@pytest.mark.parametrize("source", list(SOURCES))
@pytest.mark.parametrize("command", ["compare", "run"])
def test_accept_skips_a_refused_test_and_accepts_the_others(
    project, capsys, command, source
):
    said, before = _refuse(project, source)
    others = {
        m: project.reference(m).read_bytes()
        for m in (CSV, RAMP.replace("SimpleRamp", "PointsCheck"))
    }
    capsys.readouterr()
    # A partial accept exits 1, as it does for any other skipped test.
    assert project.dstf(command, "--accept") == 1
    out = capsys.readouterr().out
    assert "Accepted 2 test baselines" in out
    assert f"Skipped 1 test(s):\n  {RAMP}: {said}" in out
    assert project.reference(RAMP).read_bytes() == before
    # The others were written again: a new `last_updated`.
    assert all(project.reference(m).read_bytes() != b for m, b in others.items())


@pytest.mark.parametrize("command", ["compare", "run"])
def test_accept_stores_no_baseline_for_a_refused_test_that_has_none(
    project, capsys, command
):
    project.edit_spec(RAMP, json.dumps(PAIR))
    project.reference(RAMP).unlink()
    assert project.dstf(command, "--accept") == 1
    assert f"{RAMP}: {sentence(SPEC, PAIR)}" in capsys.readouterr().out
    assert project.reference(RAMP) is None


@pytest.mark.parametrize("source", list(SOURCES))
def test_the_interactive_review_does_not_accept_a_refused_test(
    project, capsys, monkeypatch, source
):
    said, before = _refuse(project, source)
    typed = iter(["a", "s"])
    prompts = []

    def read(prompt):
        prompts.append(prompt)
        return next(typed)

    monkeypatch.setattr("builtins.input", read)
    assert project.dstf("run", "-i", "failed") == 1
    out = capsys.readouterr().out
    assert "Accepted (" not in out
    assert project.reference(RAMP).read_bytes() == before
    # The list says why, and the prompt does not offer what would be refused.
    assert f"{RAMP}\x1b[0m\n  Status: \x1b[91mFAIL\x1b[0m\n  Error: {said}\n" in out
    assert prompts == ["  [v]ariables  [s]kip  [d]etail  [p]lot  [q]uit > "] * 2
    assert "Invalid choice. Enter v/s/d/p/q." in out


def test_the_review_says_fail_for_a_refused_test_that_has_no_baseline(
    project, capsys, monkeypatch
):
    """The status word is the classifier's: it read NO BASELINE — "accept
    it" — above the refusal."""
    project.edit_spec(RAMP, json.dumps(PAIR))
    project.reference(RAMP).unlink()
    prompts = []

    def read(prompt):
        prompts.append(prompt)
        return "s"

    monkeypatch.setattr("builtins.input", read)
    assert project.dstf("run", "-i", "failed") == 1
    out = capsys.readouterr().out
    said = sentence(SPEC, PAIR)
    assert f"{RAMP}\x1b[0m\n  Status: \x1b[91mFAIL\x1b[0m\n  Error: {said}\n" in out
    assert "NO BASELINE" not in out
    assert len(prompts) == 1
    # ... and "no-baseline" does not stop at it.
    prompts.clear()
    assert project.dstf("run", "-i", "no-baseline") == 1
    assert prompts == [] and "Status:" not in capsys.readouterr().out


def test_rerun_selects_a_refused_test_that_has_no_baseline_as_failed(project, capsys):
    project.edit_spec(RAMP, json.dumps(PAIR))
    project.reference(RAMP).unlink()
    assert project.dstf("run", "--rerun", "no-baseline") == 0
    out = capsys.readouterr().out
    assert "--rerun no-baseline: selected 0 of 3 tests" in out
    assert "Nothing to rerun" in out
    assert project.dstf("run", "--rerun", "failed") == 1
    assert "--rerun failed: selected 1 of 3 tests" in capsys.readouterr().out


def test_the_review_still_offers_accept_for_a_test_that_fails_its_criteria(
    project, capsys, monkeypatch
):
    project.edit_spec(RAMP, "0", variables=["x"])
    reference = project.reference(RAMP)
    data = json.loads(reference.read_text(encoding="utf-8"))
    data["variables"][0]["values"] = [v + 1.0 for v in data["variables"][0]["values"]]
    reference.write_text(json.dumps(data), encoding="utf-8")
    prompts = []

    def read(prompt):
        prompts.append(prompt)
        return "a"

    monkeypatch.setattr("builtins.input", read)
    assert project.dstf("run", "-i", "failed") == 0
    out = capsys.readouterr().out
    assert prompts == ["  [a]ccept  [v]ariables  [s]kip  [d]etail  [p]lot  [q]uit > "]
    assert "Accepted (1 variables)" in out and "Error:" not in out


# ---------------------------------------------------------------------------
# The per-test report page
# ---------------------------------------------------------------------------


def _report(project: Project) -> int:
    """``compare --report`` into an empty reports dir: a page is named by
    its reference id, so a test that lost its baseline leaves the old one."""
    shutil.rmtree(project.config.work_dir / "reports", ignore_errors=True)
    return project.dstf("compare", "--report")


def _summary_line(project: Project, model_id: str) -> str:
    import html
    import re

    (page,) = [
        p
        for p in (project.config.work_dir / "reports").rglob("interactive.html")
        if f"<h1>{model_id}</h1>" in p.read_text(encoding="utf-8")
    ]
    text = page.read_text(encoding="utf-8")
    block = re.search(r'<div class="summary" id="summary">(.*?)</div>', text, re.S)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", block.group(1))).split())


@pytest.mark.parametrize("kind", ["flat", "baseline-free flat"])
@pytest.mark.parametrize("baseline", [True, False], ids=["baseline", "no baseline"])
def test_the_report_page_says_the_refusal_not_no_baseline(project, baseline, kind):
    model_id, fields = KINDS[kind]
    project.edit_spec(model_id, json.dumps(PAIR), **fields)
    if not baseline:
        project.reference(model_id).unlink()
    assert _report(project) == 1
    assert _summary_line(project, model_id) == f"Not scored: {sentence(SPEC, PAIR)}"


def test_the_report_page_of_a_test_with_no_baseline_still_says_so(project):
    project.reference(RAMP).unlink()
    assert _report(project) == 0
    assert _summary_line(project, RAMP) == "1 variables (no baseline)"


@pytest.mark.parametrize("source", list(SOURCES))
def test_the_tools_accept_does_not_accept_a_refused_test(project, source):
    pytest.importorskip("nicegui")
    from dstf.authoring.builtin.accept import Accept
    from dstf.authoring.session import Selection, Session, SessionView

    said, before = _refuse(project, source)
    session = Session()
    session.open(project.config.config_file, work_dir=project.config.work_dir)
    assert session.comparisons[RAMP].error_message == said

    # Alone, the button is greyed with the reason.
    alone = SessionView(session, Selection(tests=frozenset({RAMP})))
    assert Accept().available(alone) == said

    # In a batch the refused test is skipped and named; the other is stored.
    both = SessionView(session, Selection(tests=frozenset({RAMP, CSV})))
    assert Accept().available(both) is True
    assert Accept().caption(both) == "Accept 1 result as baselines"
    from dstf.authoring.builtin.accept import _rows

    assert {m: (status, action) for m, status, action in _rows(both)} == {
        RAMP: ("FAIL", said),
        CSV: ("PASS", "will store"),
    }
    # `act` alone is what a caller that skipped `available` reaches.
    for view, stored in ((alone, 0), (both, 1)):
        line = Accept().act(view).run(session.project.store)
        assert line.startswith(f"Accept: stored {stored} baseline(s)")
        assert f"; skipped {RAMP}: {said}" in line
        assert project.reference(RAMP).read_bytes() == before


def test_the_one_accept_path_refuses_only_what_the_comparison_refuses(project):
    """`accept_run_results` asks the comparison's own decision: a base that
    is a number, zero included, is stored as it always was."""
    from dstf.discovery.test_registry import discover_tests
    from dstf.storage.accept import accept_run_results
    from dstf.storage.reference_store import ReferenceStore

    project.edit_spec(RAMP, "0")
    tests = [t for t in discover_tests(project.config) if t.model_id == RAMP]
    session_results = _results(project, tests)
    store = ReferenceStore(project.config)
    assert accept_run_results(
        store, tests, session_results, project.config.work_dir
    ) == (1, [])
    stored = json.loads(project.reference(RAMP).read_text(encoding="utf-8"))
    assert stored["comparison"]["tolerance"] == 0


def _results(project: Project, tests):
    from dstf.backends import get_backend_class

    backend = get_backend_class(project.config)(project.config)
    return backend.read_last_results(tests)


class TestTheStoreWritesNoBaseThatIsNoNumber:
    """`store_reference` itself: whoever calls it, a reference JSON never
    holds a base tolerance that is not one finite number."""

    @staticmethod
    def _store(project, tolerance, existing=None):
        from dstf.discovery.test_registry import discover_tests
        from dstf.storage.reference_store import ReferenceStore

        if existing is not None:
            project.edit_reference(RAMP, existing)
        (test,) = [t for t in discover_tests(project.config) if t.model_id == RAMP]
        result = _results(project, [test])[RAMP]
        test.comparison_tolerance = tolerance
        assert ReferenceStore(project.config).store_reference(test, result)
        return json.loads(project.reference(RAMP).read_text(encoding="utf-8"))

    @pytest.mark.parametrize(
        "bad",
        [PAIR, {"lower": 0.1}, "0.5", "abc", True, [1], float("nan"), float("inf")],
        ids=repr,
    )
    def test_the_tests_is_not_written(self, project, bad):
        assert "comparison" not in self._store(project, bad)

    @pytest.mark.parametrize("raw", ['{"lower": 0.1, "upper": 0.2}', '"0.5"', "NaN"])
    def test_an_existing_one_is_not_carried_over(self, project, raw):
        assert "comparison" not in self._store(project, None, existing=raw)

    @pytest.mark.parametrize("number", [0, 0.25, -1])
    def test_a_number_is_written_and_an_existing_number_is_kept(self, project, number):
        assert self._store(project, number)["comparison"] == {"tolerance": number}
        kept = self._store(project, None, existing=json.dumps(number))
        assert kept["comparison"] == {"tolerance": number}

    def test_the_tests_number_wins_over_an_existing_one(self, project):
        stored = self._store(project, 0.25, existing="0.5")
        assert stored["comparison"] == {"tolerance": 0.25}
