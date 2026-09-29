"""A page opened while a cold open finishes: nothing the UI loop runs waits
on the open's disk reads. Over TRANSFORM a Test page open during the cold
scan ran its first full sync on the loop the moment `Session.open` set
`project` — the reference index read whole (~4 s) and a wait on the
sibling-backend index's lock, held by the warm thread — and the loop,
blocked past NiceGUI's ping budget, lost the socket; the reconnect's
replay left the browser on the stale "Scoring…" paint until a reload.

Here: the open loads the reference index before the project is live, a
read never waits on the sibling index (its overlays follow in an
`overlays` event once it is ready), and a page whose client never
connected — a plain GET — returns instead of raising from `tab_state`."""

from __future__ import annotations

import asyncio
import re
import shutil
import threading
import time

import pytest

from dstf.authoring.session import Session
from dstf.config import Config
from dstf.reporting import overlay_loader as ol
from dstf.storage.ref_index import RefIndex
from dstf.storage.reference_store import ReferenceStore
from test_session import A, D, _project


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
    index drops the reads taken without it and names their models in ONE
    `overlays` event, and the next read has it. A model not read before
    is not named."""
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


def test_a_warm_of_a_project_no_longer_open_drops_nothing(tmp_path, monkeypatch):
    """A reopen before the first open's warm finished: the late warm is
    the old project's, and this one's reads — taken without the index —
    stay until this project's own warm drops them."""
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
        assert events == [{A}] and A not in s._reads
    finally:
        for gate in gates:
            gate.set()


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


async def test_a_client_that_never_connects_is_not_an_error(tmp_path, caplog):
    """A plain GET (the readiness probe, a curl) makes a client that never
    connects; NiceGUI deletes it a minute later, which ends the page's
    `connected()` wait. The page then has no one to draw for: it returns,
    and `tab_state` — which needs a connection — is never asked."""
    import logging

    pytest.importorskip("nicegui")
    from nicegui import Client
    from nicegui.testing.user_simulation import user_simulation

    from dstf.authoring.shell.shell import Shell

    cfg, work = _project(tmp_path)
    shell = Shell(cfg, work_dir=work)
    shell.session.open(cfg, work_dir=work)
    shell.load.phase = "done"
    with caplog.at_level(logging.ERROR):
        async with user_simulation(root=shell.index) as user:
            response = await user.http_client.get("/")  # never connects
            (client_id,) = re.findall(r"'client_id': '([0-9a-f-]+)'", response.text)
            await asyncio.sleep(0.1)  # the build is waiting for the handshake
            Client.instances[client_id].delete()  # `Client.prune_instances`
            await asyncio.sleep(0.3)
    assert "can only be used with a client connection" not in caplog.text
    assert shell.pages == []


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


def test_the_app_widens_the_socket_ping_budget(tmp_path, monkeypatch):
    """A margin under the fix above: NiceGUI's ping budget follows
    `reconnect_timeout` (interval 0.8x + timeout 0.4x), about 6 s at its
    default 3 s — less than the 5.5 s TRANSFORM stall plus any jitter."""
    pytest.importorskip("nicegui")
    import dstf.authoring.shell.app as app_mod

    runs: list[dict] = []
    monkeypatch.setattr(app_mod.app, "on_shutdown", lambda fn: None)
    monkeypatch.setattr(app_mod.app, "on_startup", lambda fn: None)
    monkeypatch.setattr(app_mod.ui, "run", lambda *a, **k: runs.append(k))
    monkeypatch.setattr(app_mod.Shell, "mount", lambda self: None)
    cfg, work = _project(tmp_path)
    app_mod.run_app(cfg, work_dir=work, show=False)
    (kwargs,) = runs
    assert kwargs["reconnect_timeout"] >= 10.0
