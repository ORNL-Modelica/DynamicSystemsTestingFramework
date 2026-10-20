"""``verdicts.json`` and the verdict in ``status.json``: what a scoring run
or compare decided, on disk, for a script — a plain run left no
per-criterion result there, and ``status.json`` says ``"passed"`` of a test
whose comparison failed (it records that the simulation ran)."""

from __future__ import annotations

import json

import pytest

from conftest import python_example_copy
from dstf.backends.progress import (
    DONE_PHASE,
    ProgressReporter,
    read_status,
    stamp_verdicts,
)
from dstf.cli.main import main

RAMP = "PythonTestingLib.Examples.SimpleRamp"


def _strict(path) -> dict:
    def refuse(token: str):
        raise AssertionError(f"non-strict JSON constant: {token}")

    return json.loads(path.read_text(encoding="utf-8"), parse_constant=refuse)


def _ran(tmp_path):
    """The Python example run once — every test PASS. Returns ``(config,
    tests, dstf)``, ``dstf(*args) -> exit`` over that project."""
    config, tests = python_example_copy(tmp_path)

    def dstf(command: str, *flags: str) -> int:
        return main(
            ["--config", str(config.config_file), command, "--no-browser"]
            + ["--work-dir", str(config.work_dir), *flags]
        )

    assert dstf("run") == 0
    return config, tests, dstf


# Each fixture is its own copy, and no test that reads it changes its
# baselines: a test that changed a shared one (the baseline moved, then
# accepted) passed only after the test before it on the same process, and
# pytest-xdist hands a module's tests to different workers. The `broken`
# tests do rewrite `verdicts.json` and the reports, but each writes them
# before it reads them, so neither depends on the other's order.


@pytest.fixture(scope="module")
def ran(tmp_path_factory):
    """Run, and left as the run left it."""
    return _ran(tmp_path_factory.mktemp("verdicts"))


@pytest.fixture(scope="module")
def broken(tmp_path_factory):
    """Run, then SimpleRamp's baseline moved: a compare FAILS it."""
    config, tests, dstf = _ran(tmp_path_factory.mktemp("verdicts-broken"))
    _break_ramp(config)
    return config, tests, dstf


def _break_ramp(config) -> None:
    for path in config.reference_dir.glob("ref_*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["model_id"] == RAMP:
            data["variables"][0]["values"] = [
                v + 1.0 for v in data["variables"][0]["values"]
            ]
            path.write_text(json.dumps(data), encoding="utf-8")


def test_a_plain_run_writes_every_tests_verdict_and_the_totals(ran):
    config, tests, _dstf = ran
    doc = _strict(config.work_dir / "verdicts.json")
    assert doc["totals"] == {
        "total": len(tests),
        "PASS": len(tests),
        "FAIL": 0,
        "SIM_FAIL": 0,
        "NO_REF": 0,
        "NOT RUN": 0,
    }
    assert doc["exit_code"] == 0
    assert [t["model_id"] for t in doc["tests"]] == [t.model_id for t in tests]
    assert {t["status"] for t in doc["tests"]} == {"PASS"}
    assert doc["metadata"]["backend_name"] == "Python"
    assert not list(config.work_dir.glob("verdicts.json.*"))  # no temp file left


def test_the_run_leaves_status_json_with_the_verdict_and_a_terminal_phase(ran):
    config, tests, _dstf = ran
    rows = read_status(config.work_dir)["tests"]
    assert len(rows) == len(tests)
    for row in rows:
        # `status` is the SIMULATION's; the comparison's is beside it
        assert (row["status"], row["verdict"], row["phase"]) == (
            "passed",
            "PASS",
            DONE_PHASE,
        )


def test_a_compare_says_fail_where_status_json_says_the_simulation_passed(broken):
    config, _tests, dstf = broken
    assert dstf("compare") == 1
    doc = _strict(config.work_dir / "verdicts.json")
    assert doc["totals"]["FAIL"] == 1 and doc["exit_code"] == 1
    (ramp,) = [t for t in doc["tests"] if t["model_id"] == RAMP]
    assert ramp["status"] == "FAIL"
    # the criteria tree: each node's verdict, and a criterion's metric,
    # variable, window, baseline, measure and tolerance
    (leaf,) = ramp["tree"]["children"]
    assert ramp["tree"]["passed"] is False and leaf["passed"] is False
    assert (leaf["metric"], leaf["variable"], leaf["against"], leaf["window"]) == (
        "nrmse",
        "x",
        "primary",
        {},
    )
    assert leaf["tolerance"] == config.tolerance
    assert leaf["score"] > leaf["tolerance"] and "→ FAIL" in leaf["criterion"]
    (row,) = [r for r in read_status(config.work_dir)["tests"] if r["model_id"] == RAMP]
    assert (row["status"], row["verdict"]) == ("passed", "FAIL")


def test_the_tree_is_the_one_the_report_page_holds(broken):
    """One builder: with `--report`, a test's tree here is the `tree_view`
    of its `comparison_data.json`."""
    config, _tests, dstf = broken
    assert dstf("compare", "--report") == 1
    trees = {
        t["model_id"]: t["tree"]
        for t in _strict(config.work_dir / "verdicts.json")["tests"]
    }
    sidecars = list(config.work_dir.glob("reports/*/comparison_data.json"))
    assert len(sidecars) == len(trees)
    for path in sidecars:
        data = _strict(path)
        assert trees[data["summary"]["model_id"]] == data["tree_view"]


def test_an_accept_scores_nothing_and_leaves_no_stale_verdict(tmp_path):
    """Over the FAIL a compare left in `verdicts.json`. It rewrites the
    baselines: a project of its own."""
    config, _tests, dstf = _ran(tmp_path)
    _break_ramp(config)
    assert dstf("compare") == 1
    assert (config.work_dir / "verdicts.json").exists()
    assert dstf("compare", "--accept") == 0
    assert not (config.work_dir / "verdicts.json").exists()
    assert {r["verdict"] for r in read_status(config.work_dir)["tests"]} == {None}


# -- status.json's writer -----------------------------------------------------


def test_a_finished_test_is_in_the_terminal_phase(tmp_path):
    """It kept the last phase it was seen in: a finished run's status.json
    said every test was still "finalizing"."""
    pr = ProgressReporter(tmp_path, total=2)
    pr.register("test_0001", "Lib.A")
    pr.register("test_0002", "Lib.B")
    pr.on_start("test_0001")
    pr.on_phase("test_0001", "finalizing")
    pr.on_finish("test_0001", success=True, elapsed=1.0)
    rows = {r["model_id"]: r for r in read_status(tmp_path)["tests"]}
    assert rows["Lib.A"]["phase"] == DONE_PHASE
    assert rows["Lib.B"]["phase"] is None and rows["Lib.B"]["verdict"] is None


@pytest.mark.parametrize("parallel", ["1", "2"])
def test_a_test_that_failed_before_it_simulated_has_ended_too(tmp_path, parallel):
    """A test its backend could not start — no source file — is returned
    as failed without a word to the reporter: its row stayed `"status":
    "queued", "phase": null` beside `"verdict": "SIM_FAIL"`."""
    from test_session import A, B, _project

    cfg, work = _project(tmp_path)
    (cfg.parent.parent / "Examples" / "A.py").unlink()
    assert main(
        ["--config", str(cfg), "run", "--no-browser", "--work-dir", str(work)]
        + ["--parallel", parallel]
    )
    rows = {r["model_id"]: r for r in read_status(work)["tests"]}
    row = rows[A]
    assert (row["status"], row["phase"], row["verdict"]) == (
        "failed",
        DONE_PHASE,
        "SIM_FAIL",
    )
    assert row["detail"].startswith(f"Python source not found for {A}.")
    # every row of a finished run has ended
    assert {r["phase"] for r in rows.values()} == {DONE_PHASE}
    assert rows[B]["status"] == "failed" and "simulate()" in rows[B]["detail"]


def test_a_result_ends_a_row_once(tmp_path):
    """`on_result` is for the caller that holds a run's result: it ends a
    row its backend left open, and leaves one the backend ended as it is."""
    pr = ProgressReporter(tmp_path, total=3)
    for key, model in (("t1", "Lib.A"), ("t2", "Lib.B"), ("t3", "Lib.C")):
        pr.register(key, model)
    pr.on_start("t1")
    pr.on_finish("t1", success=False, elapsed=2.0, detail="the solver's own words")
    pr.on_result("t1", success=False, elapsed=9.0, detail="a second saying")
    pr.on_result("t2", success=False, elapsed=0.0, detail="no source", timed_out=False)
    rows = {r["model_id"]: r for r in read_status(tmp_path)["tests"]}
    assert (rows["Lib.A"]["detail"], rows["Lib.A"]["elapsed"]) == (
        "the solver's own words",
        2.0,
    )
    assert (
        rows["Lib.B"]["status"],
        rows["Lib.B"]["phase"],
        rows["Lib.B"]["detail"],
    ) == (
        "failed",
        DONE_PHASE,
        "no source",
    )
    # a test nothing has a result for was never run: it has not ended
    assert (rows["Lib.C"]["status"], rows["Lib.C"]["phase"]) == ("queued", None)


def test_stamping_sets_only_the_named_tests_verdict(tmp_path):
    pr = ProgressReporter(tmp_path, total=2)
    pr.register("test_0001", "Lib.A")
    pr.register("test_0002", "Lib.B")
    pr.finalize()
    before = read_status(tmp_path)
    stamp_verdicts(tmp_path, {"Lib.A": "FAIL", "Lib.Unknown": "PASS"})
    after = read_status(tmp_path)
    assert [r["verdict"] for r in after["tests"]] == ["FAIL", None]
    for row in after["tests"]:
        row["verdict"] = None
    assert after == before  # nothing else moved
    stamp_verdicts(tmp_path, {"Lib.A": None})
    assert read_status(tmp_path)["tests"][0]["verdict"] is None


def test_stamping_a_work_dir_with_no_snapshot_writes_none(tmp_path):
    stamp_verdicts(tmp_path, {"Lib.A": "PASS"})
    assert not (tmp_path / "status.json").exists()
