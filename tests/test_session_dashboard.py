"""The work dir's `dashboard.html` as the Session re-renders it (D137 §6):
after a Save, a Discard or a Reload, off the UI loop, and never over a run
the Session did not read."""

from __future__ import annotations

import json
import threading
import time

import pytest

from dstf.authoring.capabilities import Job
from dstf.authoring.session import JobRunningError, Session
from dstf.reporting import dashboard_render
from dstf.reporting.dashboard_render import render_final, render_live
from dstf.reporting.status import FAIL, PASS, classify
from test_session import LOOSEN, A, _project

_REFRESH = '<meta http-equiv="refresh"'


def _status(work, start_wall: float) -> None:
    """status.json as a run that started at *start_wall* leaves it."""
    (work / "status.json").write_text(
        json.dumps(
            {
                "metadata": {"backend_name": "Python"},
                "start_wall": start_wall,
                "tests": [{"test_key": "test_0001", "model_id": A, "status": "passed"}],
            }
        ),
        encoding="utf-8",
    )


def _row_status(work, model_id: str) -> str | None:
    import re

    page = (work / "dashboard.html").read_text(encoding="utf-8")
    rows = re.findall(r'<tr[^>]*data-model="' + re.escape(model_id) + r'"[^>]*>', page)
    if not rows:
        return None
    found = re.search(r'data-status="([^"]*)"', rows[0])
    return found.group(1) if found else None


@pytest.fixture
def opened(tmp_path):
    """FakeLib after a run that left a final page, opened in the tool."""
    cfg, work = _project(tmp_path)
    _status(work, 1000.0)
    render_final(work)
    s = Session()
    s.open(cfg, work_dir=work)
    return s, work


def test_a_save_renders_the_saved_verdicts(opened):
    s, work = opened
    assert classify(s.comparisons[A]) == FAIL
    s.stage([LOOSEN])
    s.save()
    assert s.wait_background(10)
    assert _row_status(work, A) == "pass"


def test_a_run_since_the_reload_keeps_its_page(opened, caplog):
    """A CLI run after the tool read its results rewrote status.json and
    rendered its own verdicts: the tool's Save must not stamp the verdicts
    it read BEFORE that run over them."""
    s, work = opened
    _status(work, 2000.0)  # another run started since
    render_final(work)
    before = (work / "dashboard.html").read_bytes()
    s.stage([LOOSEN])
    with caplog.at_level("INFO", logger="dstf.authoring.session"):
        s.save()
        assert s.wait_background(10)
    assert (work / "dashboard.html").read_bytes() == before
    assert "a run started after the tool read its results" in caplog.text


def test_a_live_page_is_left_alone(opened, caplog):
    """A run is writing the page: a final one would stop its refresh."""
    s, work = opened
    render_live(work)
    before = (work / "dashboard.html").read_bytes()
    s.stage([LOOSEN])
    with caplog.at_level("INFO", logger="dstf.authoring.session"):
        s.discard()
        assert s.wait_background(10)
    assert (work / "dashboard.html").read_bytes() == before
    assert _REFRESH in before.decode("utf-8")
    assert "a run is writing it" in caplog.text


def test_results_read_under_a_live_page_are_never_rendered(tmp_path, caplog):
    """The tool opened while a run was writing: what it read is that run's
    half. The run then ends (same start, a final page); a Save must still
    leave the page to it."""
    cfg, work = _project(tmp_path)
    _status(work, 1000.0)
    render_live(work)
    s = Session()
    s.open(cfg, work_dir=work)
    render_final(work)  # the run ends
    before = (work / "dashboard.html").read_bytes()
    s.stage([LOOSEN])
    with caplog.at_level("INFO", logger="dstf.authoring.session"):
        s.save()
        assert s.wait_background(10)
    assert (work / "dashboard.html").read_bytes() == before
    assert "read its results while a run was writing them" in caplog.text


def test_a_reload_renders_the_dropped_edits_away(opened):
    """Reload — what a refused Save offers — drops the pending edits like a
    Discard, so the page must not keep their verdicts either."""
    s, work = opened
    s.stage([LOOSEN])
    # A rerun under the edit would have written this; render it by hand.
    render_final(work, comparisons=list(s.comparisons.values()))
    assert _row_status(work, A) == "pass"
    p = s.spec.path
    p.write_text(p.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    s.reload()
    assert s.wait_background(10)
    assert _row_status(work, A) == "fail"


def test_save_returns_before_the_render_finishes(opened, monkeypatch):
    """The render runs off the UI loop: over TRANSFORM with --report
    sidecars it took 1.6 s."""
    s, work = opened
    release = threading.Event()
    started = threading.Event()
    real = dashboard_render.render_final

    def slow(*a, **k):
        started.set()
        assert release.wait(10)
        real(*a, **k)

    monkeypatch.setattr(dashboard_render, "render_final", slow)
    s.stage([LOOSEN])
    t0 = time.monotonic()
    s.save()
    assert time.monotonic() - t0 < 1.0
    assert started.wait(10)
    assert not s.wait_background(0.05)  # still rendering
    release.set()
    assert s.wait_background(10)
    assert _row_status(work, A) == "pass"


def test_a_raising_render_is_logged_and_the_next_one_runs(opened, monkeypatch, caplog):
    s, work = opened
    real = dashboard_render.render_final
    calls = []

    def boom_once(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("disk full")
        real(*a, **k)

    monkeypatch.setattr(dashboard_render, "render_final", boom_once)
    s.stage([LOOSEN])
    with caplog.at_level("ERROR", logger="dstf.authoring.session"):
        s.save()  # does not raise
        assert s.wait_background(10)
    assert "re-rendering" in caplog.text and "disk full" in caplog.text
    assert s.spec.pending == []  # the Save stands
    s.stage([LOOSEN])
    s.discard()  # the worker survived the failure
    assert s.wait_background(10)
    assert len(calls) == 2
    assert _row_status(work, A) == "pass"  # the saved spec's verdict


def test_renders_land_in_the_order_asked(opened, monkeypatch):
    """A Save's render queued behind a slow Discard's waits for it: the
    page ends on the Save's verdicts, never overtaken by the older ones."""
    s, work = opened
    real = dashboard_render.render_final
    order = []
    release = threading.Event()

    def recording(work_dir, comparisons=(), **k):
        comps = list(comparisons)
        order.append(classify(next(c for c in comps if c.model_id == A)))
        if len(order) == 1:
            assert release.wait(10)  # the Discard's render is slow
        real(work_dir, comparisons=comps, **k)

    monkeypatch.setattr(dashboard_render, "render_final", recording)
    s.stage([LOOSEN])
    s.discard()  # A fails: the loaded spec
    s.stage([LOOSEN])
    s.save()  # A passes
    time.sleep(0.3)
    assert order == [FAIL]  # the Save's render waits its turn
    release.set()
    assert s.wait_background(10)
    assert order == [FAIL, PASS]
    assert _row_status(work, A) == "pass"


def test_a_jobs_after_reload_runs_off_the_ui_loop(opened):
    """Rerun's `after_reload` renders the dashboard: the job's completion
    (on the UI loop in the tool) must not wait for it."""
    s, _work = opened
    release, ran = threading.Event(), threading.Event()

    def slow(project, comparisons):
        assert release.wait(10)
        ran.set()

    jobs = []
    s.on("job", lambda e: jobs.append(e["job"]) if e["state"] == "started" else None)
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


class TestRefusedWhileAJobRuns:
    """Save and Discard change what a running job's reload scores: the
    header refuses them, and so does the Session."""

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

    def test_save_refuses(self, opened):
        s, _work = opened
        s.stage([LOOSEN])
        release = self._running(s)
        try:
            with pytest.raises(JobRunningError, match="rerun is still running"):
                s.save()
            assert len(s.spec.pending) == 2  # nothing saved
        finally:
            release.set()

    def test_discard_refuses(self, opened):
        s, _work = opened
        s.stage([LOOSEN])
        release = self._running(s)
        try:
            with pytest.raises(JobRunningError, match="rerun is still running"):
                s.discard()
            assert len(s.spec.pending) == 2  # nothing dropped
        finally:
            release.set()
