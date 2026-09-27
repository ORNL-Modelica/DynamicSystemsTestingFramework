"""The Session's work off the UI loop and the dashboard it leaves alone
(D137 §6): a job's `after_reload` (Rerun's dashboard render) runs on one
background thread, in order, on the comparisons of its own reload; a job
waits for it before it runs; Save, Discard and Reload never rewrite the work
dir's `dashboard.html` — it is the report of the last run, compare or tool
Rerun — and are refused while a job runs."""

from __future__ import annotations

import threading
import time

import pytest

from dstf.authoring.capabilities import Job
from dstf.authoring.session import JobRunningError, Session
from dstf.reporting.dashboard_render import render_final
from dstf.reporting.status import FAIL, PASS, classify
from test_session import LOOSEN, A, _project, _started


@pytest.fixture
def opened(tmp_path):
    """FakeLib after a run that left a final page, opened in the tool."""
    cfg, work = _project(tmp_path)
    render_final(work)
    s = Session()
    s.open(cfg, work_dir=work)
    return s, work


def test_save_discard_and_reload_leave_the_dashboard_byte_identical(opened):
    """The page is the last run's report: the tool's spec edits do not
    rewrite it, whatever they do to the verdicts."""
    s, work = opened
    before = (work / "dashboard.html").read_bytes()
    s.stage([LOOSEN])
    s.discard()
    assert s.wait_background(10)
    assert (work / "dashboard.html").read_bytes() == before
    s.stage([LOOSEN])
    s.save()
    assert classify(s.comparisons[A]) == PASS  # the save changed a verdict
    assert s.wait_background(10)
    assert (work / "dashboard.html").read_bytes() == before
    s.stage([LOOSEN])
    s.reload()
    assert s.wait_background(10)
    assert (work / "dashboard.html").read_bytes() == before


def test_a_jobs_after_reload_runs_off_the_ui_loop(opened):
    """Rerun's `after_reload` renders the dashboard: the job's completion
    (on the UI loop in the tool) must not wait for it."""
    s, _work = opened
    release, ran = threading.Event(), threading.Event()

    def slow(project, comparisons):
        assert release.wait(10)
        ran.set()

    jobs = _started(s)
    s.apply(
        Job(
            run=lambda ctx: None,
            label="rerun",
            models=frozenset({A}),
            after_reload=slow,
        )
    )
    try:
        assert jobs[0].done.wait(5) and jobs[0].reloaded  # finished, hook pending
        assert not ran.is_set()
    finally:
        release.set()
    assert s.wait_background(10) and ran.is_set()


def test_after_reload_gets_the_comparisons_of_its_own_reload(opened, monkeypatch):
    """The hook renders the verdicts the job's reload scored, even when it
    waits its turn behind another: an edit staged meanwhile changes the
    Session's comparisons, not the ones it was handed."""
    import dstf.authoring.session as session_mod

    # The second job starts while the first's hook still runs (past the
    # wait), so its own hook queues behind it.
    monkeypatch.setattr(session_mod, "BACKGROUND_WAIT", 0.1)
    s, _work = opened
    release = threading.Event()
    seen: list[dict] = []

    def blocking(project, comparisons):
        assert release.wait(10)

    jobs = _started(s)
    s.apply(
        Job(
            run=lambda ctx: None,
            label="rerun",
            models=frozenset({A}),
            after_reload=blocking,
        )
    )
    try:
        assert jobs[0].done.wait(5)
        s.apply(
            Job(
                run=lambda ctx: None,
                label="rerun",
                models=frozenset({A}),
                after_reload=lambda p, c: seen.append(c),
            )
        )
        assert jobs[1].done.wait(5)
        s.stage([LOOSEN])  # rescored while the second hook waits its turn
        assert classify(s.comparisons[A]) == PASS
    finally:
        release.set()
    assert s.wait_background(10)
    assert classify(seen[0][A]) == FAIL


def test_a_job_runs_after_the_last_jobs_after_reload(opened):
    """Rerun then Rerun: the first's render, still queued, must not land on
    the second's live page — the second run starts once it has landed."""
    s, _work = opened
    order: list[str] = []

    def slow(project, comparisons):
        time.sleep(0.5)
        order.append("first render")

    jobs = _started(s)
    s.apply(
        Job(
            run=lambda ctx: None,
            label="rerun",
            models=frozenset({A}),
            after_reload=slow,
        )
    )
    assert jobs[0].done.wait(5)
    s.apply(
        Job(
            run=lambda ctx: order.append("second run"),
            label="rerun",
            models=frozenset({A}),
            reloads_results=False,
        )
    )
    assert jobs[1].done.wait(10) and jobs[1].error is None
    assert order == ["first render", "second run"]


@pytest.mark.parametrize(
    "exc", [SystemExit(3), KeyboardInterrupt(), OSError("disk full")]
)
def test_a_raising_hook_is_logged_and_the_next_one_runs(opened, caplog, exc):
    """Whatever a hook raises — SystemExit too — is logged; the thread goes
    on, so `wait_background` never hangs on a thread that died busy."""
    s, _work = opened
    ran = threading.Event()

    def boom(project, comparisons):
        raise exc

    jobs = _started(s)
    with caplog.at_level("ERROR", logger="dstf.authoring.session"):
        s.apply(
            Job(
                run=lambda ctx: None,
                label="rerun",
                models=frozenset({A}),
                after_reload=boom,
            )
        )
        assert jobs[0].done.wait(5) and jobs[0].error is None
        assert s.wait_background(5)
    assert "rerun: after_reload failed" in caplog.text
    s.apply(
        Job(
            run=lambda ctx: None,
            label="rerun",
            models=frozenset({A}),
            after_reload=lambda p, c: ran.set(),
        )
    )
    assert jobs[1].done.wait(5)
    assert s.wait_background(5) and ran.is_set()


class TestRefusedWhileAJobRuns:
    """Save, Discard and Reload change what a running job's reload scores:
    the header refuses them, and so does the Session."""

    def _running(self, s):
        release = threading.Event()
        s.apply(
            Job(
                run=lambda ctx: release.wait(10),
                label="rerun",
                models=frozenset({"FakeLib.B"}),
                reloads_results=False,
            )
        )
        assert s.running is not None
        return release

    @pytest.mark.parametrize("act", ["save", "discard", "reload"])
    def test_refused(self, opened, act):
        s, _work = opened
        s.stage([LOOSEN])
        release = self._running(s)
        try:
            with pytest.raises(JobRunningError, match="rerun is still running"):
                getattr(s, act)()
            assert len(s.spec.pending) == 2  # nothing saved, nothing dropped
        finally:
            release.set()
