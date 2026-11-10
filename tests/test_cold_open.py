"""A page opened while a cold open finishes: nothing the UI loop runs waits
on the open's disk reads. Over TRANSFORM a Test page open during the cold
scan ran its first full sync on the loop the moment `Session.open` set
`project` — the reference index read whole (~4 s) — and the loop, blocked
past NiceGUI's ping budget, lost the socket; the reconnect's replay left
the browser on the stale "Scoring…" paint until a reload.

Here: the open loads the reference index before the project is live, a
read has its sibling-backend overlays at once (each is one file found by
the model's name — no folder is scanned, so nothing is warmed), a page
whose client never connected — a plain GET — returns instead of raising
from `tab_state`, and one tick handles a run of queued scan steps as its
last."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import time

import pytest

from dstf.authoring.session import Session
from dstf.config import Config
from dstf.storage.ref_index import RefIndex
from dstf.storage.reference_store import ReferenceStore
from test_session import A, _project


def _with_sibling(tmp_path):
    """`_project` with A's baseline also stored under another backend's
    partition: a sibling-backend overlay named ``Dymola/<os>``."""
    cfg, work = _project(tmp_path)
    store = ReferenceStore(Config(config_file=cfg, work_dir=work))
    ref_file = store.reference_file(A)
    assert ref_file is not None
    peer = store.ref_dir.parent.parent / "Dymola" / store.ref_dir.name
    peer.mkdir(parents=True)
    shutil.copy(ref_file, peer)
    return cfg, work, f"Dymola/{store.ref_dir.name}"


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
    s.project.store.index.stem(A)
    s.plots(A)
    assert scans == [True]


def test_a_read_has_its_sibling_overlays_at_once(tmp_path):
    cfg, work, sibling = _with_sibling(tmp_path)
    s = Session()
    s.open(cfg, work_dir=work)
    assert sibling in _names(s.view(A, "x"))


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


async def test_the_test_page_draws_the_sibling_overlay_at_its_first_paint(tmp_path):
    pytest.importorskip("nicegui")
    from nicegui.testing.user_simulation import user_simulation

    from dstf.authoring import capabilities
    from dstf.authoring.builtin import register_builtins
    from dstf.authoring.shell.shell import Shell
    from dstf.authoring.shell.test_page import TestPage
    from test_test_page import _open

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

            await _settled(lambda: any(sibling in n for n in drawn()))
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
    shell.page_setup = lambda *_: setups.append(1)  # type: ignore[method-assign]
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
