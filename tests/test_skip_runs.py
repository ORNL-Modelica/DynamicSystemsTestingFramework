"""Marks take effect through the commands (`main`), over a copy of the
bundled Python library with one test marked skip on the Python backend:
`run` does not simulate it and reads it SKIPPED, `--include-skipped` runs
it, `compare` reads it SKIPPED whatever its last result, an accept stores
nothing for it, `--rerun` and `--merge` leave it out of what they run, and
`discover` lists every mark. Each run is a real one (the Python backend);
every assertion reads what the command wrote — verdicts.json, status.json,
the batch manifest, the reference folder, dashboard.html — or printed."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

import markupsafe
import pytest

from conftest import python_example_copy
from dstf.cli import main

LIB = "PythonTestingLib.Examples"
MARKED = f"{LIB}.ConstantCsv"
POINTS = f"{LIB}.PointsCheck"
RAMP = f"{LIB}.SimpleRamp"
EVERY = {MARKED, POINTS, RAMP}
REASON = "its CSV reader is not ready here"
#: The mark as verdicts.json writes it.
SAID = f"Python: {REASON}"


class Project:
    """The copy, its spec and its work directory."""

    def __init__(self, tmp_path: Path, *, baselines: bool = True):
        config, _ = python_example_copy(tmp_path, baselines=baselines)
        self.config = config.config_file
        self.spec = self.config.parent / "test_spec.json"
        self.refs = self.config.parent / "Python" / "linux"
        self.work = tmp_path / "work"

    def edit(self, model: str, **keys) -> None:
        """Set ``keys`` on ``model``'s entry of test_spec.json."""
        doc = json.loads(self.spec.read_text(encoding="utf-8"))
        (entry,) = [e for e in doc["tests"] if e["model"] == model]
        entry.update(keys)
        self.spec.write_text(json.dumps(doc, indent=2), encoding="utf-8")

    def mark(self, *models: str) -> None:
        for model in models or (MARKED,):
            self.edit(model, backends={"Python": {"skip": REASON}})

    def failing(self) -> None:
        """MARKED's range criterion set where its constant 1.0 is not: FAIL."""
        self.edit(
            MARKED,
            comparison={
                "variable_overrides": {
                    "x": {"mode": "range", "min_value": 2.0, "max_value": 3.0}
                }
            },
        )

    def dstf(self, capsys, *argv: str, command: str = "run") -> tuple[int, str]:
        rc = main(
            [command, "--config", str(self.config), "--work-dir", str(self.work)]
            + ["--no-browser", *argv]
        )
        out, err = capsys.readouterr()
        return rc, out + err

    def read(self, name: str) -> dict:
        return json.loads((self.work / name).read_text(encoding="utf-8"))

    def verdicts(self) -> dict[str, dict]:
        return {t["model_id"]: t for t in self.read("verdicts.json")["tests"]}

    def runs(self) -> dict[str, float]:
        """Each model the work directory's runs simulated, with when it was
        last run: what a run that simulates nothing leaves as it was."""
        path = self.work / "batch_manifest.json"
        if not path.exists():
            return {}
        manifest = json.loads(path.read_text(encoding="utf-8"))
        return {e["model_id"]: e["last_run_at"] for e in manifest.values()}

    def stored(self) -> set[str]:
        """The models the reference folder holds a baseline of."""
        if not self.refs.is_dir():
            return set()
        return {
            json.loads(p.read_text(encoding="utf-8"))["model_id"]
            for p in self.refs.glob("ref_*.json")
        }

    def rows(self) -> dict[str, str]:
        """The dashboard's rows, by model: each one's text."""
        page = (self.work / "dashboard.html").read_text(encoding="utf-8")
        body = page.split('<tbody id="results-tbody">', 1)[1].split("</tbody>", 1)[0]
        return {
            re.search(r'data-model="([^"]+)"', tr).group(1): _text(tr)  # type: ignore[union-attr]
            for tr in re.findall(r"<tr\b.*?</tr>", body, flags=re.S)
        }

    def row_html(self, model: str) -> str:
        """The dashboard's row of ``model``, as the page spells it."""
        page = (self.work / "dashboard.html").read_text(encoding="utf-8")
        body = page.split('<tbody id="results-tbody">', 1)[1].split("</tbody>", 1)[0]
        (row,) = [
            tr
            for tr in re.findall(r"<tr\b.*?</tr>", body, flags=re.S)
            if f'data-model="{model}"' in tr
        ]
        return row

    def notice(self) -> str:
        """The dashboard's not-a-full-run notice, as text ('' for none)."""
        page = (self.work / "dashboard.html").read_text(encoding="utf-8")
        found = re.search(r'<div class="run-notice"[^>]*>(.*?)</div>', page, re.S)
        return markupsafe.Markup(found.group(1)).striptags() if found else ""


def _text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def _kept(status: dict) -> dict:
    """status.json but each test's verdict stamp: what a command that
    simulates nothing and scores again leaves as it was."""
    return {
        **{k: v for k, v in status.items() if k not in ("tests", "updated_at")},
        "tests": [
            {k: v for k, v in t.items() if k != "verdict"} for t in status["tests"]
        ],
    }


@pytest.fixture
def proj(tmp_path, monkeypatch):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    return Project(tmp_path)


# -- run ------------------------------------------------------------------------


def test_a_marked_test_is_not_simulated_and_reads_skipped(proj, capsys):
    """R4a."""
    proj.mark()
    rc, out = proj.dstf(capsys)
    assert rc == 0, out
    assert set(proj.runs()) == {POINTS, RAMP}
    verdicts = proj.verdicts()
    assert verdicts[MARKED]["status"] == "SKIPPED"
    assert verdicts[MARKED]["mark"] == SAID
    assert (verdicts[RAMP]["status"], verdicts[RAMP]["mark"]) == ("PASS", None)
    doc = proj.read("verdicts.json")
    # a run that leaves out only marked tests is a full run
    assert doc["selection"] is None
    assert proj.read("status.json")["selection"] is None
    assert doc["exit_code"] == 0
    assert "1 test marked skip is not run on Python/linux" in out
    assert f"SKIPPED  {MARKED} (skipped on {SAID})" in out
    assert "\nResults: 2/2 passed · 1 skipped\n" in out
    assert proj.notice() == ""


def test_include_skipped_runs_a_marked_test_and_says_its_mark_can_go(proj, capsys):
    """R4b."""
    proj.mark()
    rc, out = proj.dstf(capsys, "--include-skipped")
    assert rc == 0, out
    assert set(proj.runs()) == EVERY
    verdicts = proj.verdicts()
    assert (verdicts[MARKED]["status"], verdicts[MARKED]["mark"]) == ("PASS", SAID)
    assert f"{MARKED}: marked skip on Python, passes now — remove the mark" in out
    assert "not run on Python/linux" not in out


def test_a_run_whose_every_test_is_marked_simulates_nothing_and_is_its_own(
    proj, capsys
):
    """R4c, without --merge: the report is printed, status.json is this
    invocation's — no test simulated — and the dashboard brings back no
    row of the run before."""
    assert proj.dstf(capsys)[0] == 0
    before = proj.runs()
    assert set(before) == EVERY
    proj.mark(MARKED, POINTS, RAMP)
    rc, out = proj.dstf(capsys)
    assert rc == 0, out
    assert proj.runs() == before
    status = proj.read("status.json")
    assert status["tests"] == []
    assert status["selection"] is None
    assert status["metadata"]["backend"] == "Python"
    assert {m: v["status"] for m, v in proj.verdicts().items()} == dict.fromkeys(
        EVERY, "SKIPPED"
    )
    assert proj.read("verdicts.json")["exit_code"] == 0
    rows = proj.rows()
    assert set(rows) == EVERY
    assert all("SKIPPED" in row and "PASS" not in row for row in rows.values()), rows
    assert "3 tests marked skip are not run on Python/linux" in out
    assert "Results: 0/0 passed · 3 skipped" in out


def test_with_merge_a_run_whose_every_chosen_test_is_marked_keeps_the_last_run(
    proj, capsys
):
    """R4c with --merge: nothing simulated, the suite's last run kept in
    status.json, every test scored again — each SKIPPED."""
    assert proj.dstf(capsys)[0] == 0
    before, status = proj.runs(), proj.read("status.json")
    proj.mark(MARKED, POINTS, RAMP)
    rc, out = proj.dstf(capsys, "--merge", "--filter", f"{LIB}.*")
    assert rc == 0, out
    assert proj.runs() == before
    assert _kept(proj.read("status.json")) == _kept(status)
    assert {m: v["status"] for m, v in proj.verdicts().items()} == dict.fromkeys(
        EVERY, "SKIPPED"
    )


def test_merge_beside_a_filter_of_a_marked_test_keeps_the_suite_s_last_verdicts(
    proj, capsys
):
    """R4i."""
    assert proj.dstf(capsys)[0] == 0
    before, status = proj.runs(), proj.read("status.json")
    proj.mark()
    rc, out = proj.dstf(capsys, "--merge", "--filter", MARKED)
    assert rc == 0, out
    assert proj.runs() == before
    assert _kept(proj.read("status.json")) == _kept(status)
    assert {m: v["status"] for m, v in proj.verdicts().items()} == {
        MARKED: "SKIPPED",
        POINTS: "PASS",
        RAMP: "PASS",
    }


def test_a_rerun_whose_only_failure_is_marked_has_nothing_to_rerun(proj, capsys):
    """R4h."""
    proj.failing()
    rc, out = proj.dstf(capsys)
    assert rc == 1, out
    assert proj.verdicts()[MARKED]["status"] == "FAIL"
    before = proj.runs()
    proj.mark()
    rc, out = proj.dstf(capsys, "--rerun")
    assert rc == 0, out
    # the marked test, whose last run failed, is said to be left out
    assert (
        "Nothing to rerun among the tests not marked skip (no tests in "
        "categories: failed)\n1 test marked skip is not run on Python/linux "
        "(--include-skipped runs it).\n"
    ) in out
    assert proj.runs() == before
    assert {m: v["status"] for m, v in proj.verdicts().items()} == {
        MARKED: "SKIPPED",
        POINTS: "PASS",
        RAMP: "PASS",
    }


def test_a_filter_that_leaves_only_a_marked_test_runs_none_and_says_it_is_marked(
    proj, capsys
):
    """R4j."""
    proj.mark()
    rc, out = proj.dstf(capsys, "--filter", MARKED)
    assert rc == 0, out
    assert proj.runs() == {}
    block = {"selected": 0, "skipped": 1, "of": 3, "filter": MARKED}
    assert proj.read("status.json")["selection"] == block
    assert proj.read("verdicts.json")["selection"] == block
    assert proj.notice() == (
        f"Not a full run. None of the 3 tests was run: the one that --filter "
        f"{MARKED} matches is marked skip. The other 2 were not run."
    )
    assert (
        "0/0 passed · 1 skipped (0 of 3 tests run, 1 marked skip; not a full run)"
        in out
    )


def test_a_filter_beside_a_marked_test_counts_it_apart(proj, capsys):
    """One run and one marked of the two a filter leaves: 1 of 3 run, the
    marked one said apart."""
    proj.mark()
    rc, out = proj.dstf(capsys, "--filter", f"{MARKED},{RAMP}")
    assert rc == 0, out
    assert proj.read("status.json")["selection"] == {
        "selected": 1,
        "skipped": 1,
        "of": 3,
        "filter": f"{MARKED},{RAMP}",
    }
    assert proj.notice() == (
        f"Not a full run. 1 of 3 tests was run: the one that --filter "
        f"{MARKED},{RAMP} matches, and 1 marked skip was not run. The other 1 "
        "was not run."
    )
    assert "(1 of 3 tests run, 1 marked skip; not a full run)" in out


def test_a_marked_test_that_still_fails_fails_no_run_and_says_it_is_marked(
    proj, capsys, tmp_path
):
    """R4m: exit 0, its mark in verdicts.json, `(marked skip on Python)` on
    its console line and its dashboard row, and `dstf summary` lists it
    apart, never as a new failure."""
    proj.work = tmp_path / "main"
    assert proj.dstf(capsys)[0] == 0
    proj.failing()
    proj.mark()
    proj.work = tmp_path / "pr"
    rc, out = proj.dstf(capsys, "--include-skipped")
    assert rc == 0, out
    verdict = proj.verdicts()[MARKED]
    assert (verdict["status"], verdict["mark"]) == ("FAIL", SAID)
    assert proj.read("verdicts.json")["exit_code"] == 0
    assert f"FAIL  {MARKED} (marked skip on Python)" in out
    assert f"{MARKED}: marked skip on Python, still does not pass (FAIL)" in out
    assert "(marked skip on Python)" in proj.rows()[MARKED]

    rc = main(
        ["summary", f"main={tmp_path / 'main'}", f"pr={tmp_path / 'pr'}"]
        + ["--base", "main"]
    )
    said = capsys.readouterr().out
    assert rc == 0
    assert "Python/linux, pr: 1 marked skip</summary>" in said
    assert "not passing" not in said
    assert (
        "Marked skip, failing no run:\n\n"
        "| Test | Status | main | Mark | What failed |\n"
        "| --- | --- | --- | --- | --- |\n"
        f'| <code title="{MARKED}">ConstantCsv</code> | FAIL | PASS | {SAID} | '
        # its constant 1.0 against the range 2–3 the failing() override sets
        "x: max violation 1.000e+00 → FAIL (requires 0) |\n"
    ) in said


@pytest.mark.parametrize("include", [True, False])
def test_an_accept_never_fails_a_run_over_a_marked_test(tmp_path, capsys, include):
    """Design 4: a marked test never changes the exit code — also when
    --include-skipped runs it, its simulation fails, and the accept cannot
    store it: the refusal is printed, the exit code stays 0."""
    proj = Project(tmp_path, baselines=False)
    script = proj.config.parent.parent.parent / "Examples" / "ConstantCsv.py"
    script.write_text(
        "def simulate(stop_time, tolerance):\n    raise RuntimeError('broken')\n"
    )
    proj.mark()
    rc, out = proj.dstf(capsys, "--accept", *(["--include-skipped"] if include else []))
    assert rc == 0, out
    assert proj.stored() == {POINTS, RAMP}
    if include:
        assert f"Skipped 1 test(s):\n  {MARKED}: " in out
        # and so does a compare's accept of that run's results
        rc, out = proj.dstf(capsys, "--accept", "--include-skipped", command="compare")
        assert rc == 0, out
        assert f"Skipped 1 test(s):\n  {MARKED}: " in out
    else:
        assert f"not stored: {MARKED} is marked skip on Python ({REASON})" in out


@pytest.mark.parametrize(
    "argv", [[], ["-i"], ["--accept"], ["--merge", "-i", "--filter", RAMP]]
)
def test_a_run_of_only_marked_tests_leaves_no_verdicts_of_the_run_before(
    proj, capsys, monkeypatch, argv
):
    """Every test marked: whatever the route, verdicts.json is this
    invocation's (each test SKIPPED) or there is none — never the last
    run's PASS rows beside a dashboard that reads them SKIPPED — and the
    dashboard it wrote is named on a View line. Without --merge,
    status.json is this invocation's; with it, the last run's."""
    assert proj.dstf(capsys)[0] == 0
    status = proj.read("status.json")
    proj.mark(MARKED, POINTS, RAMP)
    monkeypatch.setattr("builtins.input", lambda prompt="": "s")
    rc, out = proj.dstf(capsys, *argv)
    assert rc == 0, out
    if "--merge" in argv:
        assert proj.read("status.json") == status
    else:
        assert proj.read("status.json")["tests"] == []
    verdicts = proj.work / "verdicts.json"
    if argv:
        assert not verdicts.exists()
    else:
        assert {v["status"] for v in proj.verdicts().values()} == {"SKIPPED"}
    view = f"View: {(proj.work / 'dashboard.html').resolve().as_uri()}"
    assert view in out.splitlines()


def test_a_merge_beside_a_marked_test_counts_it_apart_from_those_scored(proj, capsys):
    """`--merge --filter <unmarked>` with a marked test among the others:
    the marked one reads SKIPPED, so it is not among the tests "scored on
    their last results"."""
    assert proj.dstf(capsys)[0] == 0
    proj.mark()
    rc, out = proj.dstf(capsys, "--merge", "--filter", RAMP)
    assert rc == 0, out
    assert proj.verdicts()[MARKED]["status"] == "SKIPPED"
    assert proj.notice() == (
        f"Not a full run. 1 of 3 tests was run: the one that --filter {RAMP} "
        "matches, and 1 marked skip was not run. The other 1 was not run: its "
        "last result, if there is one, is scored."
    )
    assert "(1 of 3 tests run, 1 marked skip; not a full run)" in out


def test_a_skipped_row_links_no_folder_of_an_older_run(proj, capsys):
    """Its Key names the test's folder, which holds an earlier run's files:
    a SKIPPED row does not link it."""
    assert proj.dstf(capsys)[0] == 0
    proj.mark()
    assert proj.dstf(capsys)[0] == 0
    assert 'href="test_' not in proj.row_html(MARKED)
    assert 'href="test_' in proj.row_html(RAMP)


def test_a_skipped_row_reads_nothing_of_an_older_report(proj, capsys):
    """`run --report`, then the mark, then a plain run: the sidecar the
    report left for the marked test is not read into its row — no link to
    that page, no timing, no source of a run."""
    assert proj.dstf(capsys, "--report")[0] == 0
    assert "interactive.html" in proj.row_html(MARKED)
    proj.mark()
    assert proj.dstf(capsys)[0] == 0
    assert "interactive.html" not in proj.row_html(MARKED)
    assert proj.rows()[MARKED] == (
        f"test_0001 ref_f612690ea655 {MARKED} SKIPPED — — — — 0/0 0 — — — "
        f"skipped on {SAID}"
    )


def _unreadable_override(proj: Project, model: str) -> None:
    """``model``'s stored baseline given an override with a key nothing
    reads: a route that scores it stops at it (exit 2)."""
    (ref,) = [
        p
        for p in proj.refs.glob("ref_*.json")
        if json.loads(p.read_text(encoding="utf-8"))["model_id"] == model
    ]
    doc = json.loads(ref.read_text(encoding="utf-8"))
    overrides = doc.setdefault("comparison", {}).setdefault("variable_overrides", {})
    overrides["x"] = {"mode": "nrmse", "bogus_key": 1}
    ref.write_text(json.dumps(doc), encoding="utf-8")


def test_a_marked_test_s_unreadable_baseline_stops_no_run_or_compare(proj, capsys):
    """A test the marks keep out is not scored, so its stored file is not
    read either; an unmarked test's still stops the command."""
    assert proj.dstf(capsys)[0] == 0
    _unreadable_override(proj, MARKED)
    proj.mark()
    # a --merge scores every test's last result: still not the marked one
    routes = [("run", []), ("run", ["--merge", "--filter", POINTS]), ("compare", [])]
    for command, argv in routes:
        rc, out = proj.dstf(capsys, *argv, command=command)
        assert rc == 0, out
        assert proj.verdicts()[MARKED]["status"] == "SKIPPED"
    _unreadable_override(proj, RAMP)
    for command, argv in routes:
        rc, out = proj.dstf(capsys, *argv, command=command)
        assert rc == 2, out
        assert (
            'comparison.variable_overrides[x]: nrmse · x: unknown field "bogus_key"'
            in out
        )
        assert "ref_9bbf24e6f8aa.json" in out


def test_summary_spells_a_reason_s_markup_as_text(proj, capsys):
    """A reason holding a quote, a bracket and an ampersand is said as
    written in `dstf summary`'s Markdown, never as markup."""
    proj.edit(MARKED, backends={"Python": {"skip": 'say "no" <now> & then'}})
    assert proj.dstf(capsys)[0] == 0
    assert main(["summary", f"pr={proj.work}"]) == 0
    said = capsys.readouterr().out
    assert (
        "| Test | Status | Mark | What failed |\n| --- | --- | --- | --- |\n"
        f'| <code title="{MARKED}">ConstantCsv</code> | SKIPPED | '
        'Python: say "no" &lt;now&gt; &amp; then |  |\n'
    ) in said


# -- accept -------------------------------------------------------------------


def test_an_accept_stores_nothing_for_a_marked_test_and_says_so(tmp_path, capsys):
    """R4d."""
    proj = Project(tmp_path, baselines=False)
    proj.mark()
    rc, out = proj.dstf(capsys, "--accept")
    assert rc == 0, out
    assert proj.stored() == {POINTS, RAMP}
    assert MARKED not in proj.runs()
    assert f"not stored: {MARKED} is marked skip on Python ({REASON})" in out
    assert "Skipped" not in out


@pytest.mark.parametrize("left_by", ["include-skipped", "a run before the mark"])
def test_compare_accept_stores_nothing_for_a_marked_test_whose_result_is_there(
    tmp_path, capsys, left_by
):
    """R4e: a result of the marked test is in the work directory — left by
    `run --include-skipped`, or by a run before the test was marked."""
    proj = Project(tmp_path, baselines=False)
    if left_by == "include-skipped":
        proj.mark()
        assert proj.dstf(capsys, "--include-skipped")[0] == 0
    else:
        assert proj.dstf(capsys)[0] == 0
        proj.mark()
    assert MARKED in proj.runs()
    rc, out = proj.dstf(capsys, "--accept", command="compare")
    assert rc == 0, out
    assert proj.stored() == {POINTS, RAMP}
    assert f"not stored: {MARKED} is marked skip on Python ({REASON})" in out


def test_compare_accept_with_include_skipped_stores_a_marked_test(tmp_path, capsys):
    proj = Project(tmp_path, baselines=False)
    proj.mark()
    assert proj.dstf(capsys, "--include-skipped")[0] == 0
    rc, out = proj.dstf(capsys, "--accept", "--include-skipped", command="compare")
    assert rc == 0, out
    assert proj.stored() == EVERY
    assert "not stored" not in out


# -- compare ------------------------------------------------------------------


@pytest.mark.parametrize("left_by", ["include-skipped", "a run before the mark"])
def test_compare_reads_a_marked_test_skipped_whatever_its_last_result(
    proj, capsys, left_by
):
    """R4g."""
    if left_by == "include-skipped":
        proj.mark()
        assert proj.dstf(capsys, "--include-skipped")[0] == 0
    else:
        assert proj.dstf(capsys)[0] == 0
        proj.mark()
    rc, out = proj.dstf(capsys, command="compare")
    assert rc == 0, out
    verdict = proj.verdicts()[MARKED]
    assert (verdict["status"], verdict["mark"]) == ("SKIPPED", SAID)
    assert proj.read("verdicts.json")["selection"] is None
    assert "SKIPPED" in proj.rows()[MARKED]

    rc, out = proj.dstf(capsys, "--include-skipped", command="compare")
    assert rc == 0, out
    verdict = proj.verdicts()[MARKED]
    assert (verdict["status"], verdict["mark"]) == ("PASS", SAID)


def test_a_compare_of_marked_tests_none_of_which_has_a_result_exits_0(proj, capsys):
    """R4n: every test marked, --include-skipped, and no result to score —
    each NOT RUN, carrying its mark: no failure, and not "nothing ran"."""
    proj.mark(MARKED, POINTS, RAMP)
    rc, out = proj.dstf(capsys, "--include-skipped", command="compare")
    assert rc == 0, out
    assert {(v["status"], v["mark"]) for v in proj.verdicts().values()} == {
        ("NOT RUN", SAID)
    }
    assert "Nothing ran" not in out


# -- --only-skipped -------------------------------------------------------------

#: The selection block of a run, or a compare, of the one marked test.
ONLY = {"selected": 1, "skipped": 0, "of": 3, "only_skipped": True}


def test_only_skipped_runs_just_the_marked_tests_and_says_it_is_no_full_run(
    proj, capsys
):
    assert proj.dstf(capsys)[0] == 0
    before = proj.runs()
    proj.mark()
    rc, out = proj.dstf(capsys, "--only-skipped")
    assert rc == 0, out
    after = proj.runs()
    assert after[MARKED] != before[MARKED]
    assert {m: after[m] for m in (POINTS, RAMP)} == {
        m: before[m] for m in (POINTS, RAMP)
    }
    assert set(proj.verdicts()) == {MARKED}
    assert (proj.verdicts()[MARKED]["status"], proj.verdicts()[MARKED]["mark"]) == (
        "PASS",
        SAID,
    )
    assert proj.read("status.json")["selection"] == ONLY
    assert proj.read("verdicts.json")["selection"] == ONLY
    assert "(1 marked skip, failing no run; 1 of 3 tests run; not a full run)" in out
    assert f"{MARKED}: marked skip on Python, passes now — remove the mark" in out
    assert proj.notice() == (
        "Not a full run. 1 of 3 tests was run: the one that a mark keeps from "
        "running here (--only-skipped). The other 2 were not run."
    )


def test_only_skipped_with_merge_scores_the_others_on_their_last_results(proj, capsys):
    assert proj.dstf(capsys)[0] == 0
    proj.mark()
    rc, out = proj.dstf(capsys, "--only-skipped", "--merge")
    assert rc == 0, out
    assert {m: v["status"] for m, v in proj.verdicts().items()} == dict.fromkeys(
        EVERY, "PASS"
    )
    assert proj.read("status.json")["selection"] == {**ONLY, "merged": True}


@pytest.mark.parametrize("command", ["run", "compare"])
def test_only_skipped_with_no_test_marked_here_does_nothing_and_exits_0(
    proj, capsys, command
):
    """A mark for another backend is no mark here: nothing to run, and
    nothing written."""
    proj.edit(POINTS, backends={"OpenModelica": {"skip": "its solver differs"}})
    rc, out = proj.dstf(capsys, "--only-skipped", command=command)
    assert rc == 0, out
    assert out.splitlines()[-1] == "No test is marked skip on Python/linux."
    assert not proj.work.exists()


def test_only_skipped_compare_scores_just_the_marked_tests(proj, capsys):
    assert proj.dstf(capsys)[0] == 0
    proj.mark()
    rc, out = proj.dstf(capsys, "--only-skipped", command="compare")
    assert rc == 0, out
    assert set(proj.verdicts()) == {MARKED}
    assert proj.verdicts()[MARKED]["status"] == "PASS"
    assert proj.read("verdicts.json")["selection"] == {**ONLY, "compare": True}
    assert (
        "(1 marked skip, failing no run; 1 of 3 tests scored; not a full compare)"
        in out
    )


def test_only_skipped_narrows_before_rerun_chooses(proj, capsys):
    """--rerun chooses among the marked tests: a failing test that is not
    marked is not run again."""
    proj.failing()
    proj.edit(
        RAMP,
        comparison={
            "variable_overrides": {
                "x": {"mode": "range", "min_value": 50.0, "max_value": 60.0}
            }
        },
    )
    assert proj.dstf(capsys)[0] == 1
    before = proj.runs()
    proj.mark()
    rc, out = proj.dstf(capsys, "--only-skipped", "--rerun")
    # RAMP's last result, scored beside the rerun, still fails the run
    assert rc == 1, out
    after = proj.runs()
    assert after[MARKED] != before[MARKED]
    assert after[RAMP] == before[RAMP]
    assert proj.read("status.json")["selection"] == {
        **ONLY,
        "rerun": "failed",
        "merged": True,
    }


def test_only_skipped_accept_stores_the_marked_tests_only(tmp_path, capsys):
    proj = Project(tmp_path, baselines=False)
    proj.mark()
    rc, out = proj.dstf(capsys, "--only-skipped", "--accept")
    assert rc == 0, out
    assert proj.stored() == {MARKED}


@pytest.mark.parametrize("command", ["run", "compare"])
@pytest.mark.parametrize("flag", ["--filter", "--package"])
def test_only_skipped_takes_the_marked_tests_the_selection_flags_chose(
    proj, capsys, command, flag
):
    """Two tests marked; --filter or --package names one of them: only that
    one is run, or scored."""
    proj.mark(MARKED, RAMP)
    assert proj.dstf(capsys, "--include-skipped")[0] == 0
    before = proj.runs()
    rc, out = proj.dstf(capsys, "--only-skipped", flag, MARKED, command=command)
    assert rc == 0, out
    assert set(proj.verdicts()) == {MARKED}
    changed = {m for m, at in proj.runs().items() if at != before[m]}
    assert changed == ({MARKED} if command == "run" else set())
    key = "filter" if flag == "--filter" else "package"
    block = {**ONLY, key: MARKED, **({"compare": True} if command == "compare" else {})}
    assert proj.read("verdicts.json")["selection"] == block


@pytest.mark.parametrize("command", ["run", "compare"])
def test_only_skipped_beside_a_filter_of_unmarked_tests_does_nothing_and_exits_0(
    proj, capsys, command
):
    """The filter chose tests, none of them marked here: nothing to run is
    no failure, and the last run's files stay as they are."""
    proj.mark()
    assert proj.dstf(capsys)[0] == 0
    files = {
        name: (proj.work / name).read_bytes()
        for name in ("verdicts.json", "status.json", "dashboard.html")
    }
    rc, out = proj.dstf(capsys, "--only-skipped", "--filter", RAMP, command=command)
    assert rc == 0, out
    assert out.splitlines()[-1] == (
        "No test is marked skip on Python/linux among the tests --filter chose."
    )
    assert {name: (proj.work / name).read_bytes() for name in files} == files


def test_only_skipped_beside_a_filter_that_matches_nothing_fails(proj, capsys):
    proj.mark()
    rc, out = proj.dstf(capsys, "--only-skipped", "--filter", "Nothing.*")
    assert rc == 1, out
    assert out.splitlines()[-1] == "No tests matched the filter."


def test_an_include_skipped_run_s_selection_says_no_only_skipped(proj, capsys):
    proj.mark()
    rc, out = proj.dstf(capsys, "--include-skipped", "--filter", f"{MARKED},{RAMP}")
    assert rc == 0, out
    assert proj.read("status.json")["selection"] == {
        "selected": 2,
        "skipped": 0,
        "of": 3,
        "filter": f"{MARKED},{RAMP}",
    }


# -- discover -------------------------------------------------------------------


def test_discover_lists_every_mark_and_which_keep_a_test_from_running_here(
    proj, capsys
):
    """R4f: every mark, on every backend, the ones this backend and OS
    apply said so; the header counts the marks, by where each is, apart
    from the ones that apply here. Then every backend's time limit, the
    one that sets a test's limit here said so."""
    proj.edit(
        MARKED,
        backends={
            "Python": {"skip": REASON, "timeout": 100},
            "Python/linux": {"timeout": 50},
        },
    )
    proj.edit(RAMP, skip="slow everywhere")
    proj.edit(
        POINTS,
        backends={
            "OpenModelica/windows": {"skip": "its solver differs"},
            "Dymola": {"timeout": 30},
        },
    )
    rc = main(["discover", "--config", str(proj.config)])
    out = capsys.readouterr().out
    assert rc == 0
    block = out.split("\nMarked skip", 1)[1].strip().splitlines()
    assert block == [
        ": 3 marks (1 on Python, 1 on OpenModelica/windows, 1 on every backend); "
        "2 keep a test from running on Python/linux, where this config runs:",
        f"  {MARKED}: skipped on {SAID} — not run here",
        f"  {POINTS}: skipped on OpenModelica/windows: its solver differs",
        f"  {RAMP}: skipped on every backend: slow everywhere — not run here",
        "",
        "Time limits per backend:",
        f"  {MARKED}: 100 s on Python",
        f"  {MARKED}: 50 s on Python/linux — applies here",
        f"  {POINTS}: 30 s on Dymola",
    ]


def test_discover_says_when_no_mark_keeps_a_test_from_running_here(proj, capsys):
    """Marks only for another backend: the header says there are some, and
    that none of them applies where this config runs — not "0 tests"
    above a list of marks."""
    proj.edit(POINTS, backends={"OpenModelica": {"skip": "its solver differs"}})
    proj.edit(RAMP, backends={"OpenModelica": {"skip": "slow", "timeout": 240}})
    rc = main(["discover", "--config", str(proj.config)])
    out = capsys.readouterr().out
    assert rc == 0
    block = out.split("\nMarked skip", 1)[1].strip().splitlines()
    assert block == [
        ": 2 marks (2 on OpenModelica); none keeps a test from running on "
        "Python/linux, where this config runs:",
        f"  {POINTS}: skipped on OpenModelica: its solver differs",
        f"  {RAMP}: skipped on OpenModelica: slow",
        "",
        "Time limits per backend:",
        f"  {RAMP}: 240 s on OpenModelica",
    ]


@pytest.mark.parametrize("command", ["run", "compare"])
def test_a_skipped_test_gets_no_report_page(proj, capsys, command):
    """`--report` after a run before the mark: no page is written for the
    marked test (one said "Simulation failed" and not its reason), the
    count leaves it out, and its dashboard row links none."""
    assert proj.dstf(capsys)[0] == 0
    proj.mark()
    rc, out = proj.dstf(capsys, "--report", command=command)
    assert rc == 0, out
    assert "Generating 2 reports" in out
    written = {
        json.loads(p.read_text(encoding="utf-8"))["model_id"]
        for p in (proj.work / "reports").glob("*/comparison_data.json")
    }
    assert written == {POINTS, RAMP}
    assert "interactive.html" not in proj.row_html(MARKED)
    assert "interactive.html" in proj.row_html(RAMP)
