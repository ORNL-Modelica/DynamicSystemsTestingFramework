"""The Session's work off the UI loop and the dashboard it leaves alone
(D137 §6, D150): the drained queue's `status.json` and dashboard are
written on one background thread, in order, on the verdicts as they stood
when the queue drained; Save, Discard and Reload never rewrite the work
dir's `dashboard.html` — it is the report of the last run, compare or
drained queue."""

from __future__ import annotations

import threading

import pytest

import dstf.reporting.dashboard_render as dashboard
from dstf.authoring.session import Session, _Background
from dstf.backends.pool import ItemState
from dstf.reporting.dashboard_render import render_final
from dstf.reporting.status import FAIL, PASS, classify
from test_session import LOOSEN, A, D, _project
from test_session_queue import Harness


@pytest.fixture
def opened(tmp_path):
    """FakeLib after a run that left a final page, opened in the tool."""
    cfg, work = _project(tmp_path)
    render_final(work)
    s = Session()
    s.open(cfg, work_dir=work)
    return s, work


@pytest.fixture
def h(tmp_path):
    """FakeLib with a queue that runs (`test_session_queue.Harness`)."""
    harness = Harness(tmp_path)
    yield harness
    harness.session.stop_queue()
    harness.session.wait_background(30)


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


def test_the_drained_queue_s_write_runs_off_the_ui_loop(h, monkeypatch):
    """The dashboard's render is the slow part of the write: the item's end
    (on the UI loop in the tool) must not wait for it."""
    release, ran = threading.Event(), threading.Event()
    threads: list[str] = []

    def slow(work_dir, **kwargs):
        threads.append(threading.current_thread().name)
        assert release.wait(10)
        ran.set()

    monkeypatch.setattr(dashboard, "render_final", slow)
    h.session.enqueue([D])
    try:
        h.ended(D)  # taken in, the write pending
        assert (
            h.state(D) is ItemState.FINISHED
            and classify(h.session.comparisons[D]) == PASS
        )
        assert not h.session.wait_background(0.2) and not ran.is_set()
    finally:
        release.set()
    assert h.session.wait_background(10) and ran.is_set()
    assert threads == ["dstf-background"]


def test_the_write_gets_the_verdicts_as_they_stood_when_the_queue_drained(
    h, monkeypatch
):
    """The page is the drained queue's report, even when its write waits
    its turn behind another: an edit staged meanwhile changes the Session's
    comparisons, not the ones the write was handed."""
    release = threading.Event()
    seen: list[dict] = []
    monkeypatch.setattr(
        dashboard,
        "render_final",
        lambda work_dir, comparisons, results: seen.append(
            {c.model_id: c for c in comparisons}
        ),
    )
    h.session._background.submit("a write ahead of it", lambda: release.wait(10))
    try:
        h.session.enqueue([A])
        h.ended(A)
        assert classify(h.session.comparisons[A]) == FAIL
        h.session.stage([LOOSEN])  # rescored while the write waits its turn
        assert classify(h.session.comparisons[A]) == PASS
    finally:
        release.set()
    assert h.session.wait_background(10)
    assert classify(seen[0][A]) == FAIL


@pytest.mark.parametrize(
    "exc", [SystemExit(3), KeyboardInterrupt(), OSError("disk full")]
)
def test_a_raising_write_is_logged_and_the_next_one_runs(h, monkeypatch, caplog, exc):
    """Whatever a write raises — SystemExit too — is logged; the thread goes
    on, so `wait_background` never hangs on a thread that died busy."""
    ran = threading.Event()

    def boom(work_dir, **kwargs):
        raise exc

    monkeypatch.setattr(dashboard, "render_final", boom)
    with caplog.at_level("ERROR", logger="dstf.authoring.session"):
        h.session.enqueue([D])
        h.ended(D)
        assert h.state(D) is ItemState.FINISHED
        assert h.session.wait_background(5)
    assert "the queue's status.json and dashboard failed" in caplog.text
    monkeypatch.setattr(dashboard, "render_final", lambda work_dir, **kw: ran.set())
    h.session.enqueue([D])
    h.ended(D)
    assert h.session.wait_background(5) and ran.is_set()


def test_the_background_runs_what_it_is_handed_in_order():
    """Two writes queued behind a running one land in the order handed —
    the later drain's page is the one left on disk."""
    bg = _Background()
    first_running, release = threading.Event(), threading.Event()
    ran: list[str] = []

    def first() -> None:
        first_running.set()
        release.wait(5)
        ran.append("first")

    bg.submit("first", first)
    assert first_running.wait(5)
    bg.submit("second", lambda: ran.append("second"))
    bg.submit("third", lambda: ran.append("third"))
    release.set()
    assert bg.wait(5)
    assert ran == ["first", "second", "third"]
