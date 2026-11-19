"""A marked test queued in the tool draws its live samples while it runs,
as any test does (D151); one that is not queued draws nothing of a run.
Over `live_fake`'s project — the Python backend made live, a real pool and
a real result file growing a row at a time — with A and B marked skip on
the Python backend."""

from __future__ import annotations

import json
import threading

import pytest

from dstf.authoring.session import Session
from dstf.backends.pool import ItemState
from dstf.discovery.marks import SkipMark
from dstf.reporting.status import NOT_RUN, SKIPPED, classify
from live_fake import A, live_project, make_live, steer
from test_session import B
from test_session_queue import Loop

MARK = SkipMark("Python", "not on this lane")


@pytest.fixture
def marked(tmp_path, monkeypatch):
    """The live project with A and B marked, opened in a Session."""
    make_live(monkeypatch.setattr)
    cfg, work, examples = live_project(tmp_path)
    spec = cfg.parent / "test_spec.json"
    doc = json.loads(spec.read_text(encoding="utf-8"))
    for entry in doc["tests"]:
        if entry["model"] in (A, B):
            entry["backends"] = {"Python": {"skip": MARK.reason}}
    spec.write_text(json.dumps(doc), encoding="utf-8")
    loop = Loop()
    session = Session(post=loop.post)
    session.open(cfg, work_dir=work)
    session.rescan_done.wait(30)
    loop.pump()
    yield session, loop, examples
    for hold in examples.glob("*.hold"):
        hold.unlink()
    session.stop_queue()
    session.wait_background(30)
    assert not [t for t in threading.enumerate() if "-pool-" in t.name]


def test_a_queued_marked_test_draws_its_live_samples_and_an_unqueued_one_none(
    marked,
):
    session, loop, examples = marked
    assert classify(session.comparisons[B]) == SKIPPED
    steer(examples, A, hold=5)
    session.enqueue([A])
    loop.until(
        lambda: (
            session.queue_state(A) is ItemState.RUNNING
            and A not in session.project.results
        ),
        what="A running",
    )

    def drawn() -> bool:
        # a page asks for what it draws, then the Session reads the file
        session.live_runs.ask(A, "page", {"x"})
        session.live_runs.poll()
        shown = session.view(A, "x").act_time
        return shown is not None and len(shown) == 5

    loop.until(drawn, timeout=30, what="A's first five rows drawn")
    comp = session.comparisons[A]
    assert (classify(comp), comp.mark, comp.skipped) == (NOT_RUN, MARK, False)
    # B was never queued: nothing of a run is drawn, its result set aside
    view = session.view(B, "x")
    assert (view.act_time, view.act_values) == (None, None)
    assert session.partial(B) is None
