"""The tool honours a mark (R4k, the Session's half; the page's is
tests/test_author_skip_playwright.py): a marked test that this session's
queue has not run reads SKIPPED — its result on disk, from a run before the
mark, is not read, plotted or offered to Accept — and once the person
queues it, it is simulated, scored and offered like any other, carrying its
mark. Over a copy of the bundled Python library, run once by `dstf run`
before the mark, through the real backend on the Session's queue
(tests/test_accept_and_rerun.py's `Tool`)."""

from __future__ import annotations

import json

from conftest import python_example_copy
from dstf.authoring.builtin.accept import Accept
from dstf.authoring.suite_rows import suite_row
from dstf.cli import main
from dstf.discovery.marks import SkipMark
from dstf.reporting.status import NOT_RUN, PASS, RUNNING, SKIPPED, classify
from test_accept_and_rerun import Tool, tool  # noqa: F401  (the fixture, re-used)

MARKED = "PythonTestingLib.Examples.ConstantCsv"
RAMP = "PythonTestingLib.Examples.SimpleRamp"
REASON = "its CSV reader is not ready here"
MARK = SkipMark("Python", REASON)
SKIPPED_SAID = f"skipped on Python: {REASON}"


def _marked_copy(tmp_path, capsys):
    """The copy, run once whole, then MARKED marked skip on Python."""
    config, _ = python_example_copy(tmp_path)
    work = tmp_path / "work"
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    rc = main(
        ["run", "--config", str(config.config_file), "--work-dir", str(work)]
        + ["--no-browser"]
    )
    capsys.readouterr()
    assert rc == 0
    spec = config.config_file.parent / "test_spec.json"
    doc = json.loads(spec.read_text(encoding="utf-8"))
    (entry,) = [e for e in doc["tests"] if e["model"] == MARKED]
    entry["backends"] = {"Python": {"skip": REASON}}
    spec.write_text(json.dumps(doc), encoding="utf-8")
    return config, work


def test_a_marked_test_reads_skipped_until_it_is_queued_then_runs_like_any_other(
    tmp_path,
    capsys,
    tool: Tool,  # noqa: F811
):
    config, work = _marked_copy(tmp_path, capsys)
    baseline = config.reference_dir / "ref_f612690ea655.json"
    kept = baseline.read_bytes()
    session = tool.open(config, work)
    project = session.project

    # Unqueued: SKIPPED, and nothing its run before the mark left is read.
    comp = session.comparisons[MARKED]
    assert (classify(comp), comp.mark) == (SKIPPED, MARK)
    assert MARKED not in project.results
    assert RAMP in project.results
    view = session.view(MARKED, "x")
    assert (view.act_time, view.act_values) == (None, None)
    row = suite_row(session, MARKED, edits={})
    assert (row.status, row.message) == (SKIPPED, SKIPPED_SAID)
    # Accept offers nothing for it, says why, and stores nothing of it.
    alone = tool.view(MARKED)
    assert Accept().available(alone) == f"{SKIPPED_SAID} — queue it to run it"
    both = tool.view(MARKED, RAMP)
    session.apply(Accept().act(both))
    assert (
        f"not stored: {MARKED} is marked skip on Python ({REASON})"
        in (session.notices[-1])
    )
    assert baseline.read_bytes() == kept
    # A reload of the spec keeps it skipped.
    session.reload()
    assert classify(session.comparisons[MARKED]) == SKIPPED

    # Queued: simulated and scored, carrying its mark; Accept offers it.
    tool.queue(MARKED)
    tool.finished(MARKED)
    comp = session.comparisons[MARKED]
    assert (classify(comp), comp.mark, comp.skipped) == (PASS, MARK, False)
    assert project.results[MARKED].success
    assert Accept().available(alone) is True
    # Its result is this session's queue's: a reload scores it, not skips it.
    session.reload()
    assert classify(session.comparisons[MARKED]) == PASS


def test_a_mark_taken_out_of_the_file_is_read_again_by_a_reload(
    tmp_path,
    capsys,
    tool: Tool,  # noqa: F811
):
    """The result that was set aside while the mark held is the test's
    again once a reload reads the spec without the mark."""
    config, work = _marked_copy(tmp_path, capsys)
    session = tool.open(config, work)
    assert classify(session.comparisons[MARKED]) == SKIPPED
    spec = config.config_file.parent / "test_spec.json"
    doc = json.loads(spec.read_text(encoding="utf-8"))
    (entry,) = [e for e in doc["tests"] if e["model"] == MARKED]
    del entry["backends"]
    spec.write_text(json.dumps(doc), encoding="utf-8")
    session.reload()
    comp = session.comparisons[MARKED]
    assert (classify(comp), comp.mark) == (PASS, None)
    assert session.project.results[MARKED].success


def test_a_queued_marked_test_is_not_skipped_while_it_runs(
    tmp_path,
    capsys,
    tool: Tool,  # noqa: F811
):
    """From the moment its run starts, a marked test the person queued is
    that run's: it reads NOT RUN carrying its mark, as any running test,
    never SKIPPED with "queue it to run it"."""
    config, work = _marked_copy(tmp_path, capsys)
    session = tool.open(config, work)
    tool.queue(MARKED)
    tool.until("started")
    comp = session.comparisons[MARKED]
    assert (classify(comp), comp.mark, comp.skipped) == (NOT_RUN, MARK, False)
    row = suite_row(session, MARKED, edits={})
    assert row.status == RUNNING and "skipped" not in (row.message or "")
    tool.finished(MARKED)
    assert classify(session.comparisons[MARKED]) == PASS


def test_a_project_opened_again_reads_a_marked_test_skipped_again(
    tmp_path,
    capsys,
    tool: Tool,  # noqa: F811
):
    """What this session's queue ran is its own until a project is opened:
    opened again, the marked test reads SKIPPED, its result set aside."""
    config, work = _marked_copy(tmp_path, capsys)
    session = tool.open(config, work)
    tool.queue(MARKED)
    tool.finished(MARKED)
    assert classify(session.comparisons[MARKED]) == PASS
    session.stop_queue()
    assert session.wait_background(30)
    tool.open(config, work)
    comp = session.comparisons[MARKED]
    assert (classify(comp), comp.skipped) == (SKIPPED, True)
    assert MARKED not in session.project.results
