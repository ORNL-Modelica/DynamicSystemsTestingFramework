"""End to end (spec §6): open, rerun one test, accept it.

Against a temporary copy of ``examples/python/PythonTestingLib`` with its
``Python/`` partition removed — nothing has a baseline and no run has
happened, so every test opens as a simulation failure. The scenario then
runs one test through the real backend, stages an edit on a locked model
while it runs, accepts the result and reruns it once more.

Pure Python: the capabilities' ``act`` is called directly and the outcomes
go through ``Session.apply``, so no NiceGUI is involved. ``Session()``
takes no ``post``, so a job's completion runs on the worker thread and
``RunningJob.done`` is the wait (pytest-timeout is not a dependency: the
bound is ``wait(60)`` and the assertion on its return value is the
failure). Only ``ConstantCsv`` is ever run — ~0.25 s, no scipy.
"""

from __future__ import annotations

import json
import re

import pytest

from conftest import python_example_copy
from dstf.authoring.builtin.accept import Accept
from dstf.authoring.builtin.rerun import Rerun
from dstf.authoring.session import (
    JobRunningError,
    LockedError,
    Selection,
    Session,
    SessionView,
    StaleSpecError,
)
from dstf.authoring.spec_document import PatchOp
from dstf.backends.progress import read_status
from dstf.reporting.status import PASS, SIM_FAIL, classify

CONSTANT_CSV = "PythonTestingLib.Examples.ConstantCsv"
SIMPLE_RAMP = "PythonTestingLib.Examples.SimpleRamp"
POINTS_CHECK = "PythonTestingLib.Examples.PointsCheck"


def _assert_silent(capsys) -> None:
    """The tool's process never narrates: both streams empty, every time."""
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "")


def test_open_rerun_accept_rerun_over_the_python_example(tmp_path, capsys):
    config, _tests = python_example_copy(tmp_path, baselines=False)
    work_dir = tmp_path / "work"
    assert not work_dir.exists()  # no run has happened yet

    session = Session()  # no post: the completion runs on the worker thread
    session.open(config.config_file, work_dir=work_dir)
    project = session.project

    # Nothing ran, so `read_last_results` is empty and every test is scored
    # with no result at all — SIM_FAIL, not NO_REF (which needs a result).
    assert {t.model_id for t in project.tests} == {
        CONSTANT_CSV,
        SIMPLE_RAMP,
        POINTS_CHECK,
    }
    for model_id in (CONSTANT_CSV, SIMPLE_RAMP, POINTS_CHECK):
        comp = session.comparisons[model_id]
        assert classify(comp) == SIM_FAIL, model_id
        assert comp.error_message == "No simulation results found"
    assert project.backend.quiet is True
    _assert_silent(capsys)

    # 1. The rerun starts; its models are locked from the moment `apply`
    # returns (the RunningJob is set before the thread starts).
    view = SessionView(session, Selection(tests=frozenset({CONSTANT_CSV})))
    session.apply(Rerun().act(view))
    job = session.running
    assert job is not None and job.label == "rerun"

    with pytest.raises(LockedError, match="rerun is running") as locked:
        # The lock check precedes op parsing, so the op's own validity is
        # not what refuses this.
        session.stage(
            [
                PatchOp(
                    CONSTANT_CSV,
                    "replace",
                    "/comparison/variable_overrides/x/max_value",
                    2.0,
                )
            ]
        )
    assert locked.value.models == frozenset({CONSTANT_CSV})
    # A model the job does not hold still takes an edit. `add`, because the
    # entry has no `comparison` member (a `replace` there is a PatchError)
    # and `/simulation/*` is outside the stageable whitelist.
    session.stage([PatchOp(SIMPLE_RAMP, "add", "/comparison/tolerance", 0.5)])
    assert len(session.spec.pending) == 1
    # Discard (and Save) wait for the job: its reload scores the spec.
    with pytest.raises(JobRunningError, match="rerun is still running"):
        session.discard()
    assert len(session.spec.pending) == 1

    # 2. The job finishes and the results are reloaded from disk.
    assert job.done.wait(60)
    assert job.error is None
    session.discard()
    assert not session.spec.pending
    assert session.running is None
    assert project.results[CONSTANT_CSV].success
    comp = session.comparisons[CONSTANT_CSV]
    assert classify(comp) == PASS
    # Its only leaf is `range`, which scores against declared bounds: it
    # passes before any baseline exists.
    assert comp.has_reference is False
    assert read_status(work_dir)["counts"]["passed"] == 1
    # The run ended on a static page: a live one would refresh forever.
    page = (work_dir / "dashboard.html").read_text(encoding="utf-8")
    assert '<meta http-equiv="refresh"' not in page
    for model_id in (SIMPLE_RAMP, POINTS_CHECK):
        assert classify(session.comparisons[model_id]) == SIM_FAIL, model_id
    _assert_silent(capsys)

    # 3. Accept stores the baseline through the shared accept function and
    # says so in one notice line; the reload sees the new reference.
    session.apply(Accept().act(view))
    assert session.notices[-1].startswith("Accept: stored 1")
    comp = session.comparisons[CONSTANT_CSV]
    assert classify(comp) == PASS
    assert comp.has_reference is True

    reference = project.config.reference_dir / "ref_0001.json"  # empty partition
    assert json.loads(reference.read_text())["model_id"] == CONSTANT_CSV
    # The selection is ConstantCsv alone, so the accept stored exactly that
    # test: one baseline file, and no id burned on anything else.
    assert sorted(p.name for p in reference.parent.glob("ref_*.json")) == [
        "ref_0001.json"
    ]
    raw = reference.read_bytes()
    assert b"\r" not in raw and raw.endswith(b"\n")  # LF on every platform (D122)
    _assert_silent(capsys)

    # 4. A second rerun, on a fresh backend of its own, leaves the store
    # exactly as it was.
    stored_bytes = reference.read_bytes()
    session.apply(Rerun().act(view))
    second = session.running
    assert second is not None
    assert second.done.wait(60)
    assert second.error is None
    assert session.running is None
    comp = session.comparisons[CONSTANT_CSV]
    assert classify(comp) == PASS
    assert comp.has_reference is True
    assert reference.read_bytes() == stored_bytes

    # 5. Nothing printed at any point in the scenario.
    _assert_silent(capsys)


def _dashboard_status(work_dir, model_id: str) -> str:
    """The verdict `dashboard.html` shows for `model_id`."""
    page = (work_dir / "dashboard.html").read_text(encoding="utf-8")
    (row,) = re.findall(
        r'<tr[^>]*data-model="' + re.escape(model_id) + r'"[^>]*>', page
    )
    found = re.search(r'data-status="([^"]*)"', row)
    assert found is not None, row
    return found.group(1)


#: 1.0 is outside [0.99, 0.5]: ConstantCsv fails under this edit.
TIGHTEN = PatchOp(
    CONSTANT_CSV, "replace", "/comparison/variable_overrides/x/max_value", 0.5
)


def test_a_tool_rerun_ends_on_a_dashboard_with_the_sessions_verdicts(tmp_path):
    """The run's own last render is static and unscored (the Session scores
    what it reloads); once the Session has rescored, the rerun renders the
    same page with its verdicts: the rerun test the staged edit makes fail
    reads fail, the other pass — not pending."""
    config, _tests = python_example_copy(tmp_path)
    work_dir = tmp_path / "work"
    session = Session()
    session.open(config.config_file, work_dir=work_dir)
    session.stage([TIGHTEN])  # the rerun of ConstantCsv fails
    view = SessionView(
        session, Selection(tests=frozenset({CONSTANT_CSV, POINTS_CHECK}))
    )
    session.apply(Rerun().act(view))
    job = session.running
    assert job is not None and job.done.wait(60) and job.error is None
    assert session.wait_background(10)  # the rerun's render is off the loop
    page = (work_dir / "dashboard.html").read_text(encoding="utf-8")
    assert '<meta http-equiv="refresh"' not in page
    assert _dashboard_status(work_dir, CONSTANT_CSV) == "fail"
    assert _dashboard_status(work_dir, POINTS_CHECK) == "pass"


def test_save_and_discard_rerender_the_dashboard(tmp_path):
    """A rerun under an unsaved edit leaves `dashboard.html` with the edit's
    verdicts. Once the edit is gone they are gone from the file too:
    Discard renders the loaded verdicts, Save the saved ones."""
    config, _tests = python_example_copy(tmp_path)
    work_dir = tmp_path / "work"
    session = Session()
    session.open(config.config_file, work_dir=work_dir)
    session.stage([TIGHTEN])
    view = SessionView(session, Selection(tests=frozenset({CONSTANT_CSV})))
    session.apply(Rerun().act(view))
    job = session.running
    assert job is not None and job.done.wait(60) and job.error is None
    assert session.wait_background(10)
    assert _dashboard_status(work_dir, CONSTANT_CSV) == "fail"
    session.discard()
    assert session.wait_background(10)
    assert _dashboard_status(work_dir, CONSTANT_CSV) == "pass"
    session.stage([TIGHTEN])
    assert session.wait_background(10)
    assert (
        _dashboard_status(work_dir, CONSTANT_CSV) == "pass"
    )  # staging renders nothing
    session.save()
    assert session.wait_background(10)
    assert _dashboard_status(work_dir, CONSTANT_CSV) == "fail"


def test_a_reload_renders_the_dropped_edits_verdicts_away(tmp_path):
    """Reload — what a Save refused over a changed spec offers — drops the
    pending edits like Discard, and their verdicts from the page."""
    config, _tests = python_example_copy(tmp_path)
    work_dir = tmp_path / "work"
    session = Session()
    session.open(config.config_file, work_dir=work_dir)
    session.stage([TIGHTEN])
    session.apply(
        Rerun().act(SessionView(session, Selection(tests=frozenset({CONSTANT_CSV}))))
    )
    job = session.running
    assert job is not None and job.done.wait(60) and job.error is None
    assert session.wait_background(10)
    assert _dashboard_status(work_dir, CONSTANT_CSV) == "fail"
    p = session.spec.path
    p.write_text(p.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(StaleSpecError):
        session.save()
    session.reload()
    assert not session.spec.pending and session.comparisons[CONSTANT_CSV].passed
    assert session.wait_background(10)
    assert _dashboard_status(work_dir, CONSTANT_CSV) == "pass"


def test_a_save_leaves_a_later_cli_runs_page_alone(tmp_path, monkeypatch):
    """The tool reads a run; a CLI `dstf run` after it fails a test and
    renders that FAIL; a Save in the tool must not stamp the verdicts it
    read before that run over it."""
    from dstf.cli import main

    config, _tests = python_example_copy(tmp_path)
    work_dir = config.work_dir
    monkeypatch.setattr("dstf.reporting.report_suite.open_in_browser", lambda p: None)
    argv = ["--config", str(config.config_file), "run", "--work-dir", str(work_dir)]
    argv += ["--filter", CONSTANT_CSV]
    assert main(argv) == 0
    session = Session()
    session.open(config.config_file, work_dir=work_dir)
    assert session.comparisons[CONSTANT_CSV].passed
    csv = config.source_path / "Examples" / "data" / "constant_experiment.csv"
    csv.write_text(csv.read_text(encoding="utf-8").replace("1.0", "7.0"))
    assert main(argv) == 1
    assert _dashboard_status(work_dir, CONSTANT_CSV) == "fail"
    session.stage([PatchOp(POINTS_CHECK, "add", "/comparison/tolerance", 0.02)])
    session.save()
    assert session.wait_background(10)
    assert _dashboard_status(work_dir, CONSTANT_CSV) == "fail"


def test_save_and_discard_write_no_dashboard_where_no_run_left_one(tmp_path):
    config, _tests = python_example_copy(tmp_path)
    work_dir = tmp_path / "work"
    session = Session()
    session.open(config.config_file, work_dir=work_dir)
    session.stage([TIGHTEN])
    session.discard()
    session.stage([TIGHTEN])
    session.save()
    assert not (work_dir / "dashboard.html").exists()
