"""End to end (spec §6): open, queue one test, accept it, queue it again.

Against a temporary copy of ``examples/python/PythonTestingLib`` with its
``Python/`` partition removed — nothing has a baseline and no run has
happened, so every test opens NOT RUN. The scenario then runs one test
through the real backend on the Session's simulation queue (D150), stages
an edit on it while it runs, accepts the result and queues it once more.

Pure Python: the capabilities' ``act`` is called directly and the outcomes
go through ``Session.apply``, so no NiceGUI is involved. The pool's
callbacks arrive on its own threads and are posted: ``Loop`` (from
``test_session_queue``) stands in for the UI loop and runs them on the
test's thread. A test that reads the Session BETWEEN a run's start and its
end runs the posted calls one at a time (``_until``): the simulations here
are the example's own — ``ConstantCsv`` is ~0.25 s, no scipy — and cannot
be held (pytest-timeout is not a dependency: every wait is bounded and
raises).
"""

from __future__ import annotations

import json
import queue
import re
import threading
import time

import psutil
import pytest

from conftest import python_example_copy
from dstf.authoring.builtin.accept import Accept
from dstf.authoring.builtin.enqueue import EnqueueTests
from dstf.authoring.session import QueueError, Selection, Session, SessionView
from dstf.authoring.spec_document import PatchOp
from dstf.authoring.suite_rows import suite_row
from dstf.backends.pool import ItemState
from dstf.backends.progress import read_status
from dstf.reporting.status import FAIL, NOT_RUN, PASS, RUNNING, classify
from test_session_queue import Loop

CONSTANT_CSV = "PythonTestingLib.Examples.ConstantCsv"
SIMPLE_RAMP = "PythonTestingLib.Examples.SimpleRamp"
POINTS_CHECK = "PythonTestingLib.Examples.PointsCheck"

#: 1.0 is outside [0.99, 0.5]: ConstantCsv fails under this edit.
TIGHTEN = PatchOp(
    CONSTANT_CSV, "replace", "/comparison/variable_overrides/x/max_value", 0.5
)


def _assert_silent(capsys) -> None:
    """The tool's process never narrates: both streams empty, every time."""
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "")


class Tool:
    """A Session whose posts a test runs, as the shell's tick does."""

    def __init__(self) -> None:
        self.loop = Loop()
        self.session = Session(post=self.loop.post)
        #: The state of every `queue` event the Session emitted, in order.
        self.said: list[str] = []
        self.session.on("queue", lambda event: self.said.append(event["state"]))

    def open(self, config, work_dir) -> Session:
        self.session.open(config.config_file, work_dir=work_dir)
        # The open's rescan ends on the loop when the scan was cached.
        self.loop.until(self.session.rescan_done.is_set, what="the open's rescan")
        return self.session

    def view(self, *models: str) -> SessionView:
        return SessionView(self.session, Selection(tests=frozenset(models)))

    def queue(self, *models: str) -> None:
        """The Queue click, taken to the point where its tests are queued:
        the click's read of the library runs off the loop, and what queues
        them is posted when it is back."""
        self.session.apply(EnqueueTests().act(self.view(*models)))
        self.until("queued")

    def finished(self, *models: str) -> None:
        """Until every one of ``models`` ran to its end and was taken in."""
        self.loop.until(
            lambda: all(
                self.session.queue_state(m) is ItemState.FINISHED for m in models
            ),
            what=f"{models} finished",
        )

    def until(self, state: str, timeout: float = 60.0) -> None:
        """Run what was posted ONE call at a time until the Session says
        ``state`` of its queue. One at a time, so the test stands between a
        run's start and its end however fast the simulation is: the end is
        posted and waits for the next call."""
        seen = len(self.said)
        deadline = time.monotonic() + timeout
        while state not in self.said[seen:]:
            try:
                fn = self.loop.posted.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                raise AssertionError(f"the queue never said {state!r}") from None
            fn()


@pytest.fixture
def tool():
    """Whatever the test asserted, it leaves no simulation behind: the
    queue's pool is ended, its threads joined and its subprocesses gone."""
    me = psutil.Process()
    before = {p.pid for p in me.children(recursive=True)}
    t = Tool()
    yield t
    t.session.stop_queue()
    assert t.session.wait_background(30)
    assert not [th for th in threading.enumerate() if "-pool-" in th.name]
    started = [p for p in me.children(recursive=True) if p.pid not in before]
    _gone, alive = psutil.wait_procs(started, timeout=10)
    assert alive == []


def test_open_queue_accept_queue_again_over_the_python_example(tmp_path, capsys, tool):
    config, _tests = python_example_copy(tmp_path, baselines=False)
    work_dir = tmp_path / "work"
    assert not work_dir.exists()  # no run has happened yet

    session = tool.open(config, work_dir)
    project = session.project

    # Nothing ran, so `read_last_results` is empty and every test is scored
    # with no result at all — NOT RUN (D145), not NO_REF (which needs a
    # result) and not SIM_FAIL (nothing ran to fail).
    assert {t.model_id for t in project.tests} == {
        CONSTANT_CSV,
        SIMPLE_RAMP,
        POINTS_CHECK,
    }
    for model_id in (CONSTANT_CSV, SIMPLE_RAMP, POINTS_CHECK):
        comp = session.comparisons[model_id]
        assert classify(comp) == NOT_RUN, model_id
        assert comp.error_message == "No simulation results found"
    assert project.backend.quiet is True
    others = {m: session.comparisons[m] for m in (SIMPLE_RAMP, POINTS_CHECK)}
    _assert_silent(capsys)

    # 1. The test is queued and starts: it reads RUNNING, with no result.
    view = tool.view(CONSTANT_CSV)
    tool.queue(CONSTANT_CSV)
    assert [i.model_id for i in session.queue] == [CONSTANT_CSV]
    tool.until("started")
    assert session.queue_state(CONSTANT_CSV) is ItemState.RUNNING
    assert suite_row(session, CONSTANT_CSV).status == RUNNING
    assert CONSTANT_CSV not in project.results
    assert classify(session.comparisons[CONSTANT_CSV]) == NOT_RUN

    # An edit of the running test is staged, not refused: it is scored when
    # the result arrives.
    session.stage([TIGHTEN])
    assert [op.model_id for op in session.spec.pending] == [CONSTANT_CSV]
    # Accept is refused, before the click and at it: the result it would
    # store is about to be replaced by this run's.
    refusal = f"{CONSTANT_CSV} is running: accept it when its run has ended"
    assert Accept().available(view) == refusal
    with pytest.raises(QueueError) as refused:
        session.apply(Accept().act(view))
    assert str(refused.value) == refusal
    assert not list(project.config.reference_dir.glob("ref_*.json"))

    # 2. It ends: its own result is taken and it alone is rescored, with the
    # criteria as they stand now.
    tool.until("ended")
    assert session.queue_state(CONSTANT_CSV) is ItemState.FINISHED
    assert project.results[CONSTANT_CSV].success
    assert set(project.results) == {CONSTANT_CSV}
    assert classify(session.comparisons[CONSTANT_CSV]) == FAIL  # the staged edit
    for model_id, comp in others.items():
        assert session.comparisons[model_id] is comp, model_id
        assert classify(comp) == NOT_RUN, model_id
    session.discard()
    assert not session.spec.pending
    comp = session.comparisons[CONSTANT_CSV]
    assert classify(comp) == PASS
    # Its only leaf is `range`, which scores against declared bounds: it
    # passes before any baseline exists.
    assert comp.has_reference is False
    # The queue drained: status.json lists the one item that ran, and the
    # page is a static one — a live one would refresh forever.
    assert session.wait_background(30)
    status = read_status(work_dir)
    assert [(t["model_id"], t["status"]) for t in status["tests"]] == [
        (CONSTANT_CSV, "passed")
    ]
    assert status["counts"]["passed"] == 1
    page = (work_dir / "dashboard.html").read_text(encoding="utf-8")
    assert '<meta http-equiv="refresh"' not in page
    _assert_silent(capsys)

    # 3. Its run has ended: Accept stores the baseline through the shared
    # accept function and says so in one notice line; the reload sees the
    # new reference.
    assert Accept().available(view) is True
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

    # 4. Queued again: its last result goes when the run starts, and the run
    # leaves the store exactly as it was.
    stored_bytes = reference.read_bytes()
    tool.queue(CONSTANT_CSV)
    tool.until("started")
    assert CONSTANT_CSV not in project.results
    assert classify(session.comparisons[CONSTANT_CSV]) == NOT_RUN
    tool.until("ended")
    assert [(i.model_id, i.state) for i in session.queue] == [
        (CONSTANT_CSV, ItemState.FINISHED)
    ] * 2
    comp = session.comparisons[CONSTANT_CSV]
    assert classify(comp) == PASS
    assert comp.has_reference is True
    assert reference.read_bytes() == stored_bytes
    assert sorted(p.name for p in reference.parent.glob("ref_*.json")) == [
        "ref_0001.json"
    ]

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


def test_a_drained_queue_ends_on_a_dashboard_with_the_sessions_verdicts(tmp_path, tool):
    """Once nothing is queued or running the Session writes the page, static,
    with ITS verdicts: the test the staged edit makes fail reads fail, the
    other pass — not pending."""
    config, _tests = python_example_copy(tmp_path)
    work_dir = tmp_path / "work"
    session = tool.open(config, work_dir)
    session.stage([TIGHTEN])  # the run of ConstantCsv fails
    tool.queue(CONSTANT_CSV, POINTS_CHECK)
    tool.finished(CONSTANT_CSV, POINTS_CHECK)
    assert session.wait_background(30)  # the drain's render is off the loop
    page = (work_dir / "dashboard.html").read_text(encoding="utf-8")
    assert '<meta http-equiv="refresh"' not in page
    assert _dashboard_status(work_dir, CONSTANT_CSV) == "fail"
    assert _dashboard_status(work_dir, POINTS_CHECK) == "pass"


def test_a_drained_queue_stamps_each_verdict_into_status_json(tmp_path, tool):
    """status.json's `status` says only that a simulation ran: the verdict
    beside it is the Session's, as a `run` leaves it — FAIL for the test
    the staged edit fails, whose simulation "passed"."""
    config, _tests = python_example_copy(tmp_path)
    work_dir = tmp_path / "work"
    session = tool.open(config, work_dir)
    session.stage([TIGHTEN])  # the run of ConstantCsv fails
    tool.queue(CONSTANT_CSV, POINTS_CHECK)
    tool.finished(CONSTANT_CSV, POINTS_CHECK)
    assert session.wait_background(30)
    rows = {t["model_id"]: t for t in read_status(work_dir)["tests"]}
    assert {m: (t["status"], t["verdict"]) for m, t in rows.items()} == {
        CONSTANT_CSV: ("passed", "FAIL"),
        POINTS_CHECK: ("passed", "PASS"),
    }


def test_save_and_discard_after_a_drained_queue_leave_its_dashboard_alone(
    tmp_path, tool
):
    """The dashboard is the report of the last run, compare or drained
    queue: a test run under an unsaved edit leaves the edit's verdicts in
    it, and a Save or a Discard after it does not rewrite it (D137 §6)."""
    config, _tests = python_example_copy(tmp_path)
    work_dir = tmp_path / "work"
    session = tool.open(config, work_dir)
    session.stage([TIGHTEN])
    tool.queue(CONSTANT_CSV)
    tool.finished(CONSTANT_CSV)
    assert session.wait_background(30)
    assert _dashboard_status(work_dir, CONSTANT_CSV) == "fail"
    page = (work_dir / "dashboard.html").read_bytes()
    session.discard()
    assert session.comparisons[CONSTANT_CSV].passed
    assert session.wait_background(30)
    assert (work_dir / "dashboard.html").read_bytes() == page
    session.stage([TIGHTEN])
    session.save()
    assert session.wait_background(30)
    assert (work_dir / "dashboard.html").read_bytes() == page


def _dashboard_models(work_dir) -> list[str]:
    page = (work_dir / "dashboard.html").read_text(encoding="utf-8")
    return re.findall(r'<tr[^>]*data-model="([^"]*)"', page)


def test_a_drained_queues_dashboard_lists_every_test(tmp_path, tool):
    """A queue that ran one test rewrites status.json with that test alone;
    the page it renders lists every test the Session scored, not its one
    row."""
    config, _tests = python_example_copy(tmp_path)
    work_dir = tmp_path / "work"
    session = tool.open(config, work_dir)
    tool.queue(CONSTANT_CSV)
    tool.finished(CONSTANT_CSV)
    assert session.wait_background(30)
    assert [t["model_id"] for t in read_status(work_dir)["tests"]] == [CONSTANT_CSV]
    every = {CONSTANT_CSV, SIMPLE_RAMP, POINTS_CHECK}
    assert sorted(_dashboard_models(work_dir)) == sorted(every)
    assert _dashboard_models(work_dir)[0] == CONSTANT_CSV  # status.json's first


def test_a_criterion_on_a_variable_the_python_backend_recorded_scores_at_once(
    tmp_path, tool
):
    """The owner's O2 over a real backend: the Python backend records every
    variable the script returns, so a criterion staged on one the spec does
    not track re-reads the result — APPENDED after the tracked ones — and
    scores it with no second run; the strip counts nothing new."""
    from dstf.authoring.situation import new_variables

    config, _tests = python_example_copy(tmp_path)
    src = config.config_file.parent.parent.parent / "Examples" / "ConstantCsv.py"
    text = src.read_text(encoding="utf-8")
    assert '"variables": {"x": values}' in text
    src.write_text(
        text.replace(
            '"variables": {"x": values}', '"variables": {"x": values, "y": values}'
        ),
        encoding="utf-8",
    )
    work_dir = tmp_path / "work"
    session = tool.open(config, work_dir)
    tool.queue(CONSTANT_CSV)
    tool.finished(CONSTANT_CSV)
    assert [v.name for v in session.project.results[CONSTANT_CSV].variables] == ["x"]
    ran = len(session.queue)

    leaf = {"metric": "range", "variable": "y", "min_value": 0.5, "max_value": 1.5}
    session.stage([PatchOp(CONSTANT_CSV, "add", "/metrics/children/-", leaf)])
    assert len(session.queue) == ran  # nothing was queued for it
    result = session.project.results[CONSTANT_CSV]
    assert [(v.index, v.name) for v in result.variables] == [(1, "x"), (2, "y")]
    test = session.project.test(CONSTANT_CSV)
    assert new_variables(test, result, config.diagnostic_variables) == 0
    scored = session.comparisons[CONSTANT_CSV].metric_tree.children[-1]
    assert scored.passed, scored.diagnostics
