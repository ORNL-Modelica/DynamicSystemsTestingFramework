"""A `dstf run` that simulates fewer than every discovered test says so:
in `status.json` and `verdicts.json` (`selection`: how many were run of
how many the project has, and what narrowed them), on the console, on the
dashboard, and in every `dstf summary` cell it is read into.

Through the real command over a copy of the bundled Python example
(three tests, no simulator), reading the files the run wrote and what
`summary` prints of them. `--changed-since` is
tests/test_run_changed_since.py's."""

from __future__ import annotations

import json
import re
from pathlib import Path

import markupsafe
import pytest

from conftest import python_example_copy
from dstf.cli.main import main

CONSTANT_CSV = "PythonTestingLib.Examples.ConstantCsv"
SIMPLE_RAMP = "PythonTestingLib.Examples.SimpleRamp"
POINTS_CHECK = "PythonTestingLib.Examples.PointsCheck"

_NOTICE = re.compile(r'<div class="run-notice" id="run-notice">(.*?)</div>', re.S)


@pytest.fixture
def proj(tmp_path):
    config, tests = python_example_copy(tmp_path)
    assert len(tests) == 3
    return config


def _run(proj, capsys, *argv) -> str:
    rc = main(
        ["run", "--config", str(proj.config_file), "--work-dir", str(proj.work_dir)]
        + ["--no-browser", *argv]
    )
    out, err = capsys.readouterr()
    assert rc == 0, out + err
    return out


def _read(proj, name: str) -> dict:
    return json.loads((Path(proj.work_dir) / name).read_text(encoding="utf-8"))


def _notice(proj) -> str | None:
    page = (Path(proj.work_dir) / "dashboard.html").read_text(encoding="utf-8")
    found = _NOTICE.search(page)
    return None if found is None else markupsafe.Markup(found.group(1)).striptags()


def _summary(capsys, *runs: str) -> tuple[str, list[dict]]:
    """`summary`'s Markdown, and its JSON's cells, over ``runs``
    (LABEL=DIR, the first the base)."""
    base = runs[0].split("=", 1)[0]
    assert main(["summary", *runs, "--base", base]) == 0
    markdown = capsys.readouterr().out
    assert main(["summary", *runs, "--base", base, "--format", "json"]) == 0
    return markdown, json.loads(capsys.readouterr().out)["cells"]


def _tighten_constant_csv(proj) -> None:
    """ConstantCsv's x is 1.0: outside [0.99, 0.5] it FAILs."""
    spec = Path(proj.config_file).parent / "test_spec.json"
    data = json.loads(spec.read_text(encoding="utf-8"))
    (entry,) = [t for t in data["tests"] if t["model"] == CONSTANT_CSV]
    entry["comparison"]["variable_overrides"]["x"]["max_value"] = 0.5
    spec.write_text(json.dumps(data), encoding="utf-8")


def test_a_run_narrowed_by_filter_says_how_many_of_the_project_s_tests_ran(
    proj, capsys
):
    out = _run(proj, capsys, "--filter", "*Constant*")
    block = {"filter": "*Constant*", "selected": 1, "skipped": 0, "of": 3}
    assert _read(proj, "status.json")["selection"] == block
    assert _read(proj, "verdicts.json")["selection"] == block
    assert "\nResults: 1/1 passed (1 of 3 tests run; not a full run)\n" in out
    assert _notice(proj) == (
        "Not a full run. 1 of 3 tests was run: the one that --filter "
        "*Constant* matches. The other 2 were not run."
    )

    markdown, cells = _summary(capsys, f"some={proj.work_dir}")
    assert "| Python/linux | 1/1 · 1 of 3 tests run |" in markdown
    assert (
        '"N of M tests run" = the run simulated N of the project\'s M tests '
        "(--filter, --package, --rerun, --changed-since or --only-skipped): "
        "not a full run."
    ) in markdown
    assert [c["selection"] for c in cells] == [block]


def test_a_run_narrowed_by_package_says_so(proj, capsys):
    _run(proj, capsys, "--package", SIMPLE_RAMP)
    assert _read(proj, "status.json")["selection"] == {
        "package": SIMPLE_RAMP,
        "selected": 1,
        "skipped": 0,
        "of": 3,
    }
    assert _notice(proj) == (
        f"Not a full run. 1 of 3 tests was run: the one in --package "
        f"{SIMPLE_RAMP}. The other 2 were not run."
    )
    markdown, _ = _summary(capsys, f"pkg={proj.work_dir}")
    assert "| Python/linux | 1/1 · 1 of 3 tests run |" in markdown


def test_filter_and_package_together_are_both_recorded(proj, capsys):
    _run(
        proj,
        capsys,
        "--package",
        "PythonTestingLib.Examples",
        "--filter",
        f"{SIMPLE_RAMP},{CONSTANT_CSV}",
    )
    assert _read(proj, "status.json")["selection"] == {
        "filter": f"{SIMPLE_RAMP},{CONSTANT_CSV}",
        "package": "PythonTestingLib.Examples",
        "selected": 2,
        "skipped": 0,
        "of": 3,
    }
    assert _notice(proj) == (
        "Not a full run. 2 of 3 tests were run: the ones in --package "
        f"PythonTestingLib.Examples that --filter {SIMPLE_RAMP},{CONSTANT_CSV} "
        "matches. The other 1 was not run."
    )


def test_a_rerun_says_so_and_that_the_others_were_scored_on_their_last_results(
    proj, capsys, tmp_path
):
    _tighten_constant_csv(proj)
    rc = main(
        ["run", "--config", str(proj.config_file), "--work-dir", str(proj.work_dir)]
        + ["--no-browser"]
    )
    full_out = capsys.readouterr().out
    assert rc == 1, full_out
    assert _read(proj, "status.json")["selection"] is None
    full = tmp_path / "full"
    full.mkdir()
    (full / "verdicts.json").write_text(
        (Path(proj.work_dir) / "verdicts.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    rc = main(
        ["run", "--config", str(proj.config_file), "--work-dir", str(proj.work_dir)]
        + ["--no-browser", "--rerun"]
    )
    out = capsys.readouterr().out
    assert rc == 1, out
    block = {"rerun": "failed", "merged": True, "selected": 1, "skipped": 0, "of": 3}
    assert _read(proj, "status.json")["selection"] == block
    verdicts = _read(proj, "verdicts.json")
    assert verdicts["selection"] == block
    # every test scored, one of them simulated
    assert verdicts["totals"]["total"] == 3
    assert "\nResults: 2/3 passed (1 of 3 tests run; not a full run)\n" in out
    assert _notice(proj) == (
        "Not a full run. 1 of 3 tests was run: the one that the last run left "
        "in failed (--rerun). The other 2 were not run: their last results, "
        "where there are any, are scored."
    )

    markdown, _ = _summary(capsys, f"full={full}", f"rerun={proj.work_dir}")
    assert (
        "| Python/linux | 2/3 (1 FAIL) | 2/3 (1 FAIL; 1 also fails on full) "
        "· 1 of 3 tests run |"
    ) in markdown
    assert (
        " With --merge or --rerun the others were scored on their last results, "
        "where there were any."
    ) in markdown


def test_a_merged_filter_run_records_the_merge(proj, capsys):
    _run(proj, capsys)
    out = _run(proj, capsys, "--filter", SIMPLE_RAMP, "--merge")
    assert _read(proj, "status.json")["selection"] == {
        "filter": SIMPLE_RAMP,
        "merged": True,
        "selected": 1,
        "skipped": 0,
        "of": 3,
    }
    assert _read(proj, "verdicts.json")["totals"]["total"] == 3
    assert "\nResults: 3/3 passed (1 of 3 tests run; not a full run)\n" in out


def test_a_merge_with_no_earlier_results_claims_none_were_scored(proj, capsys):
    """A fresh work directory holds no last result: the run scores only
    the test it ran, and nothing says the others were scored."""
    out = _run(proj, capsys, "--filter", SIMPLE_RAMP, "--merge")
    block = {"filter": SIMPLE_RAMP, "selected": 1, "skipped": 0, "of": 3}
    assert _read(proj, "status.json")["selection"] == block
    verdicts = _read(proj, "verdicts.json")
    assert (verdicts["selection"], verdicts["totals"]["total"]) == (block, 1)
    assert "\nResults: 1/1 passed (1 of 3 tests run; not a full run)\n" in out
    assert _notice(proj) == (
        f"Not a full run. 1 of 3 tests was run: the one that --filter "
        f"{SIMPLE_RAMP} matches. The other 2 were not run."
    )
    markdown, _ = _summary(capsys, f"merged={proj.work_dir}")
    assert "| Python/linux | 1/1 · 1 of 3 tests run |" in markdown
    assert "last results" not in markdown


def test_a_merge_over_some_earlier_results_says_only_those_are_scored(proj, capsys):
    """One of the two tests not run has a last result: it is scored, the
    other is not, and the notice claims no result the work directory
    lacks."""
    _run(proj, capsys, "--filter", CONSTANT_CSV)
    _run(proj, capsys, "--filter", SIMPLE_RAMP, "--merge")
    selection = _read(proj, "status.json")["selection"]
    assert selection == {
        "filter": SIMPLE_RAMP,
        "merged": True,
        "selected": 1,
        "skipped": 0,
        "of": 3,
    }
    assert _read(proj, "verdicts.json")["totals"]["total"] == 2
    assert _notice(proj) == (
        f"Not a full run. 1 of 3 tests was run: the one that --filter "
        f"{SIMPLE_RAMP} matches. The other 2 were not run: their last results, "
        "where there are any, are scored."
    )
    markdown, _ = _summary(capsys, f"merged={proj.work_dir}")
    assert "| Python/linux | 2/2 · 1 of 3 tests run |" in markdown
    assert (
        " With --merge or --rerun the others were scored on their last results, "
        "where there were any."
    ) in markdown


def test_a_rerun_that_accepts_scores_nothing_and_claims_no_merge(proj, capsys):
    _tighten_constant_csv(proj)
    rc = main(
        ["run", "--config", str(proj.config_file), "--work-dir", str(proj.work_dir)]
        + ["--no-browser"]
    )
    capsys.readouterr()
    assert rc == 1
    _run(proj, capsys, "--rerun", "--accept")
    assert _read(proj, "status.json")["selection"] == {
        "rerun": "failed",
        "selected": 1,
        "skipped": 0,
        "of": 3,
    }
    assert _notice(proj) == (
        "Not a full run. 1 of 3 tests was run: the one that the last run left "
        "in failed (--rerun). The other 2 were not run."
    )


def test_a_full_run_writes_no_block_and_summary_says_nothing_of_one(proj, capsys):
    out = _run(proj, capsys)
    assert _read(proj, "status.json")["selection"] is None
    assert _read(proj, "verdicts.json")["selection"] is None
    assert "\nResults: 3/3 passed\n" in out
    assert _notice(proj) is None
    markdown, cells = _summary(capsys, f"all={proj.work_dir}")
    assert "| Python/linux | 3/3 |" in markdown
    assert "tests run" not in markdown
    assert [c["selection"] for c in cells] == [None]


def test_a_filter_that_leaves_every_test_writes_no_block(proj, capsys):
    out = _run(proj, capsys, "--filter", "PythonTestingLib.*")
    assert _read(proj, "status.json")["selection"] is None
    assert "\nResults: 3/3 passed\n" in out
    assert _notice(proj) is None


def _compare(proj, capsys, *argv, rc: int = 0) -> str:
    got = main(
        ["compare", "--config", str(proj.config_file), "--work-dir", str(proj.work_dir)]
        + ["--no-browser", *argv]
    )
    out, err = capsys.readouterr()
    assert got == rc, out + err
    return out


#: The summary's key line for a compare of fewer than the project's tests.
COMPARE_KEY = (
    ' "N of M tests scored" = a compare scored N of the project\'s M tests '
    "(--filter, --package or --only-skipped), the others not: not a full compare."
)


def test_a_compare_narrowed_by_filter_says_how_many_it_scored(proj, capsys):
    """After a full run, `compare --filter` scores one of the three tests:
    its verdicts, console, dashboard and `summary` cell say it SCORED one,
    never that only one was run — the other two were, and their rows say
    so. status.json keeps the run's block — it records the simulations —
    and a later compare without a filter reads that again: a full run."""
    _run(proj, capsys)
    out = _compare(proj, capsys, "--filter", "*Constant*")
    block = {
        "filter": "*Constant*",
        "selected": 1,
        "skipped": 0,
        "of": 3,
        "compare": True,
    }
    verdicts = _read(proj, "verdicts.json")
    assert (verdicts["selection"], verdicts["totals"]["total"]) == (block, 1)
    assert "\nResults: 1/1 passed (1 of 3 tests scored; not a full compare)\n" in out
    assert _notice(proj) == (
        "Not a full compare. 1 of 3 tests was scored: the one that --filter "
        "*Constant* matches. The other 2 were not scored."
    )
    markdown, cells = _summary(capsys, f"some={proj.work_dir}")
    assert "| Python/linux | 1/1 · 1 of 3 tests scored |" in markdown
    assert COMPARE_KEY in markdown
    assert "tests run" not in markdown
    assert [c["selection"] for c in cells] == [block]
    assert _read(proj, "status.json")["selection"] is None

    out = _compare(proj, capsys)
    assert _read(proj, "verdicts.json")["selection"] is None
    assert "\nResults: 3/3 passed\n" in out
    assert _notice(proj) is None


def test_a_compare_of_another_filter_than_the_run_s_names_what_it_scored(proj, capsys):
    """The run simulated ConstantCsv alone; the compare scores SimpleRamp
    alone, which has no result: it says SimpleRamp was scored and the
    other two were not — not that SimpleRamp ran and ConstantCsv did
    not."""
    _run(proj, capsys, "--filter", "*Constant*")
    out = _compare(proj, capsys, "--filter", "*Ramp*", rc=1)
    block = {"filter": "*Ramp*", "selected": 1, "skipped": 0, "of": 3, "compare": True}
    verdicts = _read(proj, "verdicts.json")
    assert verdicts["selection"] == block
    assert [(t["model_id"], t["status"]) for t in verdicts["tests"]] == [
        (SIMPLE_RAMP, "NOT RUN")
    ]
    assert "(1 of 3 tests scored; not a full compare)\n" in out
    assert "tests run" not in out
    assert _notice(proj) == (
        "Not a full compare. 1 of 3 tests was scored: the one that --filter "
        "*Ramp* matches. The other 2 were not scored."
    )
    markdown, _ = _summary(capsys, f"ramp={proj.work_dir}")
    assert "· 1 of 3 tests scored |" in markdown and COMPARE_KEY in markdown
    assert "tests run" not in markdown
    # status.json is still the run's: ConstantCsv simulated, by --filter
    assert _read(proj, "status.json")["selection"] == {
        "filter": "*Constant*",
        "selected": 1,
        "skipped": 0,
        "of": 3,
    }


#: The notice of a compare of SimpleRamp alone, by --package.
_RAMP_BY_PACKAGE = (
    f"Not a full compare. 1 of 3 tests was scored: the one in --package "
    f"{SIMPLE_RAMP}. The other 2 were not scored."
)


def test_a_compare_narrowed_by_package_says_so_in_its_html_file(proj, capsys, tmp_path):
    _run(proj, capsys)
    page = tmp_path / "page.html"
    _compare(
        proj,
        capsys,
        "--package",
        SIMPLE_RAMP,
        "--report-format",
        "html",
        "-o",
        str(page),
    )
    block = {
        "package": SIMPLE_RAMP,
        "selected": 1,
        "skipped": 0,
        "of": 3,
        "compare": True,
    }
    assert _read(proj, "verdicts.json")["selection"] == block
    found = _NOTICE.search(page.read_text(encoding="utf-8"))
    assert found is not None
    assert (markupsafe.Markup(found.group(1)).striptags(), _notice(proj)) == (
        _RAMP_BY_PACKAGE,
        _RAMP_BY_PACKAGE,
    )


def test_a_narrowed_compare_s_report_suite_dashboard_says_so(proj, capsys):
    """`--report` writes the dashboard again beside the per-test pages: it
    says what the compare scored too, not the run's (full) block."""
    _run(proj, capsys)
    _compare(proj, capsys, "--package", SIMPLE_RAMP, "--report")
    assert (Path(proj.work_dir) / "reports").is_dir()
    assert _notice(proj) == _RAMP_BY_PACKAGE


def test_a_rerun_with_nothing_to_rerun_says_what_this_invocation_ran(proj, capsys):
    """An earlier run of one test by --filter, all passing: `--rerun`
    finds nothing to rerun, simulates nothing and exits 0. Its verdicts,
    console and dashboard say it ran none of the project's 3 tests and
    scored the last results — not the earlier run's "1 of 3 by --filter"."""
    _run(proj, capsys, "--filter", SIMPLE_RAMP)
    out = _run(proj, capsys, "--rerun")
    assert "Nothing to rerun (no tests in categories: failed)" in out
    block = {"rerun": "failed", "merged": True, "selected": 0, "skipped": 0, "of": 3}
    assert _read(proj, "verdicts.json")["selection"] == block
    assert "(0 of 3 tests run; not a full run)\n" in out
    assert _notice(proj) == (
        "Not a full run. None of the 3 tests was run: there is none that the "
        "last run left in failed (--rerun). Their last results, where there "
        "are any, are scored."
    )
    markdown, cells = _summary(capsys, f"rerun={proj.work_dir}")
    assert "· 0 of 3 tests run |" in markdown
    assert [c["selection"] for c in cells] == [block]
    # status.json records the simulations: the run that made them
    assert _read(proj, "status.json")["selection"] == {
        "filter": SIMPLE_RAMP,
        "selected": 1,
        "skipped": 0,
        "of": 3,
    }
