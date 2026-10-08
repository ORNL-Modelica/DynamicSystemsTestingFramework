"""A page opened while a cold open finishes: nothing the UI loop runs waits
on the open's disk reads. Over TRANSFORM a Test page open during the cold
scan ran its first full sync on the loop the moment `Session.open` set
`project` — the reference index read whole (~4 s) and a wait on the
sibling-backend index's lock, held by the warm thread — and the loop,
blocked past NiceGUI's ping budget, lost the socket; the reconnect's
replay left the browser on the stale "Scoring…" paint until a reload.

Here: the open loads the reference index before the project is live, a
read never waits on the sibling index (its overlays follow in an
`overlays` event once it is ready), a page whose client never
connected — a plain GET — returns instead of raising from `tab_state`, and
one tick handles a run of queued scan steps as its last."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import threading
import time

import pytest

from dstf.authoring.session import Session
from dstf.backends.pool import ItemState
from dstf.config import Config
from dstf.reporting import overlay_loader as ol
from dstf.storage.ref_index import RefIndex
from dstf.storage.reference_store import ReferenceStore
from test_session import A, D, _project
from test_session_queue import fake_item


def _with_sibling(tmp_path):
    """`_project` with A's baseline also stored under another backend's
    partition: a sibling-backend overlay named ``Dymola/<os>``."""
    cfg, work = _project(tmp_path)
    store = ReferenceStore(Config(config_file=cfg, work_dir=work))
    ref_dir = store.ref_dir
    ref_id = store.index.get_id(A)
    assert ref_id is not None
    peer = ref_dir.parent.parent / "Dymola" / ref_dir.name
    peer.mkdir(parents=True)
    shutil.copy(ref_dir / RefIndex.ref_filename(ref_id), peer)
    return cfg, work, f"Dymola/{ref_dir.name}"


@pytest.fixture
def gated_warm(monkeypatch):
    """The warm thread blocks until the gate is set, then builds the index
    for real: a warm still running when the page reads."""
    gate = threading.Event()
    real = ol.warm_sibling_backend_index

    def warm(config):
        assert gate.wait(10)
        real(config)

    monkeypatch.setattr(ol, "warm_sibling_backend_index", warm)
    ol._sibling_backend_index.cache_clear()
    yield gate
    gate.set()


def _names(view) -> list[str]:
    return [o.name for o in view.overlays]


def test_the_reference_index_is_read_before_the_project_is_live(tmp_path, monkeypatch):
    """The loop reads `project` the moment it is set — a page's `_paint`
    asks the index for the baseline's name. Loaded lazily, the index was
    scanned on the loop (every ref file read whole) and on the open thread
    at once, by a `_scan` that clears and refills."""
    cfg, work = _project(tmp_path)
    s = Session()
    scans: list[bool] = []
    real = RefIndex._scan

    def spy(self):
        scans.append(s.project is None)
        real(self)

    monkeypatch.setattr(RefIndex, "_scan", spy)
    s.open(cfg, work_dir=work)
    assert scans == [True]
    s.project.store.index.get_id(A)
    s.plots(A)
    assert scans == [True]


def test_a_read_never_waits_on_the_sibling_index(tmp_path, gated_warm):
    """While the warm thread builds the sibling index it holds its lock; a
    read on the loop that waited for it blocked the loop for as long."""
    cfg, work = _project(tmp_path)
    s = Session()
    s.open(cfg, work_dir=work)
    assert not s.overlay_index_ready.is_set()
    done = threading.Event()

    def read():
        s.plots(A)
        s.view(A, "x")
        done.set()

    with ol._INDEX_LOCK:  # the warm thread, mid-build
        threading.Thread(target=read, daemon=True).start()
        assert done.wait(3.0), "a read waited on the sibling index's lock"


def test_the_sibling_overlays_follow_once_the_index_is_ready(tmp_path, gated_warm):
    """A read before the index is ready has no sibling overlay; the ready
    index names the models read without it in ONE `overlays` event, and
    the next read has it. A model not read before is not named."""
    cfg, work, sibling = _with_sibling(tmp_path)
    s = Session()
    events: list[set] = []
    arrived = threading.Event()

    def on_overlays(e):
        events.append(set(e["models"]))
        arrived.set()

    s.on("overlays", on_overlays)
    s.open(cfg, work_dir=work)
    assert sibling not in _names(s.view(A, "x"))
    gated_warm.set()
    assert arrived.wait(5.0)
    assert events == [{A}]
    assert sibling in _names(s.view(A, "x"))


def test_a_read_after_the_index_is_ready_has_its_overlays_at_once(tmp_path, gated_warm):
    cfg, work, sibling = _with_sibling(tmp_path)
    s = Session()
    events: list[set] = []
    s.on("overlays", lambda e: events.append(set(e["models"])))
    s.open(cfg, work_dir=work)
    gated_warm.set()
    assert s.overlay_index_ready.wait(5.0)
    assert sibling in _names(s.view(A, "x"))
    s.view(D, "x")
    assert events == []  # nothing was read without the index


def test_a_warm_of_a_project_no_longer_open_names_nothing(tmp_path, monkeypatch):
    """A reopen before the first open's warm finished: the late warm is
    the old project's and names nothing; this one's reads — taken without
    the index — gain their sibling overlays once this project's own warm is
    done, on their next read. The reads themselves are kept."""
    cfg, work, _sibling = _with_sibling(tmp_path)
    gates = [threading.Event(), threading.Event()]
    calls: list[int] = []
    real = ol.warm_sibling_backend_index

    def warm(config):
        gate = gates[len(calls)]
        calls.append(1)
        assert gate.wait(10)
        real(config)

    monkeypatch.setattr(ol, "warm_sibling_backend_index", warm)
    ol._sibling_backend_index.cache_clear()
    s = Session()
    events: list[set] = []
    s.on("overlays", lambda e: events.append(set(e["models"])))
    try:
        s.open(cfg, work_dir=work)
        first = s.overlay_index_ready
        s.open(cfg, work_dir=work)
        s.view(A, "x")
        reads = s._reads[A]
        gates[0].set()
        assert first.wait(5.0)
        time.sleep(0.2)  # the post runs on the warm thread (no UI loop here)
        assert events == [] and s._reads[A] is reads
        gates[1].set()
        assert s.overlay_index_ready.wait(5.0)
        for _ in range(50):
            if events:
                break
            time.sleep(0.05)
        assert events == [{A}] and s._reads[A] is reads and not reads.siblings
        s.view(A, "x")
        assert reads.siblings
    finally:
        for gate in gates:
            gate.set()


def test_the_ready_index_keeps_a_running_models_file_reads(tmp_path, gated_warm):
    """A model the queue is running reads nothing from its result file —
    the file is being rewritten; its run's end answers it. The ready index
    used to drop every read taken without it, and with it what was cached
    from the file: a running model's untracked series and its names were
    gone until its run ended."""
    cfg, work = _project(tmp_path)
    result = work / "test_0001" / "result.json"
    data = json.loads(result.read_text())
    data["variables"]["y"] = [1, 2, 3, 4, 5]  # untracked
    result.write_text(json.dumps(data))
    posted: list = []
    s = Session(post=posted.append)
    s.open(cfg, work_dir=work)
    assert s._file_series(A, "y") is not None
    assert "y" in s.variable_names(A)
    reads = s._reads[A]
    fake_item(s, A, ItemState.RUNNING)
    gated_warm.set()
    assert s.overlay_index_ready.wait(5.0)
    assert _eventually(lambda: posted)
    posted.pop(0)()  # the UI loop runs `_overlays_ready`
    assert s._reads[A] is reads
    assert s._file_series(A, "y") is not None
    assert "y" in s.variable_names(A)
    assert reads.siblings


def test_a_read_during_a_reopen_never_asks_the_sibling_index(tmp_path, monkeypatch):
    """The ready flag is the project's: a read in the reopen's window —
    the new project live, before the open named its warm — once saw the
    OLD project's set flag, read with siblings and built the new project's
    sibling index on the UI loop."""
    cfg, work, _sibling = _with_sibling(tmp_path)
    asked: list[str] = []

    class InTheWindow(Session):
        armed = False

        def __setattr__(self, name, value):
            object.__setattr__(self, name, value)
            # `self.spec = spec` runs right after `self.project = Project(...)`
            if name == "spec" and self.armed:
                real = ol.load_sibling_backend_overlays
                monkeypatch.setattr(
                    ol,
                    "load_sibling_backend_overlays",
                    lambda c, m: (asked.append(m), real(c, m))[1],
                )
                self._reads_for(A)  # a UI-loop read landing in the window
                monkeypatch.setattr(ol, "load_sibling_backend_overlays", real)

    ol._sibling_backend_index.cache_clear()
    s = InTheWindow()
    s.open(cfg, work_dir=work)
    assert s.overlay_index_ready.wait(5.0)
    ol._sibling_backend_index.cache_clear()  # a reopen on another backend: cold
    s.armed = True
    s.open(cfg, work_dir=work)
    assert asked == []


def test_the_ready_flag_is_set_before_the_redraw_is_posted(tmp_path):
    """The posted redraw re-reads: a read that still saw the flag unset
    would read without the siblings again, and nothing would follow."""
    cfg, work = _project(tmp_path)
    seen: list[bool] = []
    s = Session(post=lambda fn: seen.append(s.overlay_index_ready.is_set()))
    s.open(cfg, work_dir=work)
    assert s.overlay_index_ready.wait(5.0)
    assert _eventually(lambda: seen)
    assert seen == [True]


def test_a_warm_that_fails_posts_nothing(tmp_path, monkeypatch):
    cfg, work = _project(tmp_path)

    def broken(config):
        raise OSError("the sibling root is unreadable")

    monkeypatch.setattr(ol, "warm_sibling_backend_index", broken)
    posted: list = []
    s = Session(post=posted.append)
    s.open(cfg, work_dir=work)
    assert s.overlay_index_ready.wait(5.0)
    time.sleep(0.2)
    assert posted == []


def test_a_gone_ui_loop_does_not_kill_the_warm_thread(tmp_path, monkeypatch):
    """A shutdown while the index builds: the post onto the closed loop
    raises, on the warm thread, where nothing catches it."""
    cfg, work = _project(tmp_path)
    errors: list = []
    monkeypatch.setattr(threading, "excepthook", lambda a: errors.append(a.exc_value))

    def gone(fn):
        raise RuntimeError("the event loop is closed")

    s = Session(post=gone)
    s.open(cfg, work_dir=work)
    assert s.overlay_index_ready.wait(5.0)
    time.sleep(0.2)
    assert errors == []


def _eventually(predicate, tries: int = 100) -> bool:
    for _ in range(tries):
        if predicate():
            return True
        time.sleep(0.02)
    return bool(predicate())


# -- the pages ------------------------------------------------------------------


async def _settled(predicate, tries: int = 100) -> None:
    for _ in range(tries):
        if predicate():
            return
        await asyncio.sleep(0.05)
    assert predicate()


async def test_the_test_page_redraws_when_its_overlays_arrive(tmp_path, gated_warm):
    pytest.importorskip("nicegui")
    from nicegui.testing.user_simulation import user_simulation

    from dstf.authoring import capabilities
    from dstf.authoring.builtin import register_builtins
    from dstf.authoring.shell.shell import Shell
    from dstf.authoring.shell.test_page import TestPage
    from test_test_page import _counted, _open

    capabilities._reset_for_tests()
    register_builtins()
    cfg, work, sibling = _with_sibling(tmp_path)
    shell = Shell(cfg, work_dir=work)
    shell.session.open(cfg, work_dir=work)
    shell.load.phase = "done"
    try:
        async with user_simulation(root=shell.index) as user:
            shell.mount()
            await _open(user, f"/test/{A}")
            (page,) = [
                p
                for p in shell.pages
                if isinstance(p, TestPage) and p.client is user.client
            ]
            with page.client:
                page.cards[0].show()
            card = page.cards[0]

            def drawn() -> list[str]:
                return [str(t.get("name")) for t in card.plot.figure["data"]]

            assert not any(sibling in n for n in drawn())
            sent = _counted(card)
            gated_warm.set()
            await _settled(lambda: sent[0] == 1)
            assert any(sibling in n for n in drawn())
    finally:
        capabilities._reset_for_tests()
        register_builtins()


@pytest.mark.parametrize("path", ["/", f"/test/{A}"])
async def test_a_client_that_never_connects_builds_nothing(tmp_path, caplog, path):
    """A plain GET (the readiness probe, a curl) makes a client that never
    connects; NiceGUI deletes it a minute later, which ends the page's
    `connected()` wait. The page then has no one to draw for: it returns,
    and `tab_state` — which needs a connection — is never asked, nor is
    anything built."""
    pytest.importorskip("nicegui")
    from nicegui import Client
    from nicegui.testing.user_simulation import user_simulation

    from dstf.authoring.shell.shell import Shell

    cfg, work = _project(tmp_path)
    shell = Shell(cfg, work_dir=work)
    shell.session.open(cfg, work_dir=work)
    shell.load.phase = "done"
    setups: list[int] = []
    shell.page_setup = lambda: setups.append(1)  # type: ignore[method-assign]
    with caplog.at_level(logging.ERROR):
        async with user_simulation(root=shell.index) as user:
            shell.mount()
            response = await asyncio.wait_for(user.http_client.get(path), 2)
            assert response.status_code == 200, response.status_code
            (client_id,) = re.findall(r"'client_id': '([0-9a-f-]+)'", response.text)
            await asyncio.sleep(0.1)  # the build is waiting for the handshake
            Client.instances[client_id].delete()  # `Client.prune_instances`
            await asyncio.sleep(0.3)
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []
    assert setups == [] and shell.pages == []


async def test_the_tab_waits_out_a_disconnect_before_the_build_resumes(monkeypatch):
    """A handshake followed by a disconnect before the page resumed (a
    starved loop): the client is alive and reconnecting, so the page waits
    for it again rather than raising or giving up."""
    from dstf.authoring.shell import tabstate

    class Flaky:
        is_deleted = False

        def __init__(self):
            self.waits = 0

        @property
        def has_socket_connection(self):
            return self.waits >= 2

        async def connected(self):
            self.waits += 1

    class Gone:
        is_deleted = True
        has_socket_connection = False

        async def connected(self):
            pass

    sentinel = tabstate.TabState()
    monkeypatch.setattr(tabstate, "tab_state", lambda: sentinel)
    flaky = Flaky()
    assert await tabstate.connected_tab_state(flaky) is sentinel
    assert flaky.waits == 2
    assert await tabstate.connected_tab_state(Gone()) is None


async def test_an_overlays_event_resyncs_only_its_tests_page(tmp_path):
    pytest.importorskip("nicegui")
    from nicegui.testing.user_simulation import user_simulation

    from dstf.authoring import capabilities
    from dstf.authoring.builtin import register_builtins
    from dstf.authoring.shell.shell import Shell
    from dstf.authoring.shell.test_page import TestPage
    from test_test_page import _open

    capabilities._reset_for_tests()
    register_builtins()
    cfg, work = _project(tmp_path)
    shell = Shell(cfg, work_dir=work)
    shell.session.open(cfg, work_dir=work)
    shell.load.phase = "done"
    try:
        async with user_simulation(root=shell.index) as user:
            shell.mount()
            await _open(user, f"/test/{A}")
            await _open(user, f"/test/{D}")
            pages = {p.model_id: p for p in shell.pages if isinstance(p, TestPage)}
            synced: dict[str, list[bool]] = {A: [], D: []}
            for m, p in pages.items():
                p.sync = lambda force=False, m=m: synced[m].append(force)  # type: ignore[method-assign]
            shell._events.put({"event": "overlays", "models": {A}})
            shell.tick()
            assert synced == {A: [True], D: []}
    finally:
        capabilities._reset_for_tests()
        register_builtins()


async def test_a_tick_handles_a_run_of_scan_steps_as_its_last(tmp_path):
    """Each scan step carries the running count, and every one synced an
    open Test page in full and re-read `status.json` in its header: one
    tick over 3,269 queued steps (TRANSFORM's .mo count, a TRANSFORM-sized
    `status.json`) held the UI loop about 13 s; folded, 0.02 s."""
    pytest.importorskip("nicegui")
    from nicegui.testing.user_simulation import user_simulation

    from dstf.authoring import capabilities
    from dstf.authoring.builtin import register_builtins
    from dstf.authoring.shell.shell import Shell
    from dstf.authoring.shell.suite import SuitePage
    from dstf.authoring.shell.test_page import TestPage
    from test_test_page import _open

    capabilities._reset_for_tests()
    register_builtins()
    cfg, work = _project(tmp_path)
    shell = Shell(cfg, work_dir=work)
    shell.session.open(cfg, work_dir=work)
    shell.load.phase = "done"
    n = 3000
    try:
        async with user_simulation(root=shell.index) as user:
            shell.mount()
            await _open(user, "/")
            await _open(user, f"/test/{A}")
            (suite,) = [p for p in shell.pages if isinstance(p, SuitePage)]
            (page,) = [p for p in shell.pages if isinstance(p, TestPage)]
            synced: list[bool] = []
            page.sync = lambda force=False: synced.append(force)  # type: ignore[method-assign]
            for i in range(n):
                shell._events.put(
                    {"event": "loading", "phase": "scan", "done": i + 1, "total": n}
                )
            shell.tick()
            assert len(synced) == 1
            assert suite.progress.text == f"scanning library… {n} of {n}"
    finally:
        capabilities._reset_for_tests()
        register_builtins()


def test_only_adjacent_scan_steps_fold():
    """A scan step folds into the step before it, never into another
    event: the order of everything else is what the pages hear."""
    pytest.importorskip("nicegui")
    from dstf.authoring.shell.shell import _folded

    def step(done: int) -> dict:
        return {"event": "loading", "phase": "scan", "done": done, "total": 9}

    known = {"event": "known", "models": [A]}
    spec = {"event": "spec", "models": {A}}
    done = {"event": "loading", "phase": "done", "seconds": 1.0}
    assert _folded([known, step(1), step(2), spec, step(3), done, step(4)]) == [
        known,
        step(2),
        spec,
        step(3),
        done,
        step(4),
    ]
