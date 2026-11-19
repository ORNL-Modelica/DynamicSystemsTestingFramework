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
    assert "Nothing to rerun (no tests in categories: failed)" in out
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
        f'Marked skip, failing no run: <code title="{MARKED}">ConstantCsv</code> '
        f"(FAIL, {SAID})."
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


# -- discover -------------------------------------------------------------------


def test_discover_lists_every_mark_and_which_keep_a_test_from_running_here(
    proj, capsys
):
    """R4f: every mark, on every backend, the ones this backend and OS
    apply said so."""
    proj.mark()
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
        "(2 tests not run on Python/linux):",
        f"  {MARKED}: skipped on {SAID} — not run here",
        f"  {POINTS}: skipped on OpenModelica/windows: its solver differs",
        f"  {RAMP}: skipped on every backend: slow everywhere — not run here",
    ]


def test_a_skipped_test_s_report_page_draws_nothing_of_its_last_run(proj, capsys):
    """`compare --report` after a run before the mark: the marked test's
    page holds no trajectory, statistic or time of that run."""
    assert proj.dstf(capsys)[0] == 0
    proj.mark()
    rc, out = proj.dstf(capsys, "--report", command="compare")
    assert rc == 0, out
    key = next(k for k, m in _keys(proj).items() if m == MARKED)
    page = json.loads(
        (proj.work / "reports" / key / "comparison_data.json").read_text(
            encoding="utf-8"
        )
    )
    assert page["trajectories"] == page["nobaseline_trajectories"] == []
    assert page["last_run_at"] is None
    (wall,) = [
        row
        for section in page["statistics_sections"]
        for row in section["rows"]
        if row["label"] == "Wall time"
    ]
    assert wall["current"] == ""


def _keys(proj: Project) -> dict[str, str]:
    manifest = json.loads((proj.work / "batch_manifest.json").read_text("utf-8"))
    return {k: e["model_id"] for k, e in manifest.items()}
