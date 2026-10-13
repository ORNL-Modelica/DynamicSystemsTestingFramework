"""Rows before a cold library scan finishes: `Session.open` names the
models it can before the scan — the spec document's entries and the last
run's batch manifest — in a `known` event; the shell filters them as
`visible_tests` filters the project's tests (`--filter` / `--package`), and
the Suite page draws them waiting until the merge's `spec` event replaces
them. A scan-cache hit changes nothing: the merge follows at once."""

from __future__ import annotations

import asyncio
import json
import threading

import pytest

from dstf.authoring import session as session_mod
from dstf.authoring.session import Session
from test_session import A, B, C, D, G, _project


@pytest.fixture
def slow_scan(monkeypatch):
    """A cold scan (no cache) that blocks until the gate is set."""
    gate = threading.Event()
    real = session_mod.scan_source_tests

    def blocked(config, **kw):
        assert gate.wait(10)
        return real(config, **kw)

    monkeypatch.setattr(session_mod, "load_scan", lambda config: None)
    monkeypatch.setattr(session_mod, "scan_source_tests", blocked)
    return gate


def _with_manifest(work, *model_ids: str, junk: bool = False) -> None:
    """The last run's manifest, naming `model_ids` too — and, with `junk`,
    an entry that is not an entry at all (data: skipped)."""
    path = work / "batch_manifest.json"
    entries = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    for i, m in enumerate(model_ids, 900):
        entries[f"test_{i:04d}"] = {"model_id": m, "ref_id": None}
    if junk:
        entries["junk"] = "not an entry"
    path.write_text(json.dumps(entries), encoding="utf-8")


def test_known_is_emitted_before_the_scan(tmp_path, slow_scan):
    cfg, work = _project(tmp_path)
    _with_manifest(work, D, "FakeLib.Gone", junk=True)
    s = Session()
    events: list[dict] = []
    seen = threading.Event()

    def on_known(e):
        events.append(e)
        seen.set()

    s.on("known", on_known)
    t = threading.Thread(target=s.open, args=(cfg,), kwargs={"work_dir": work})
    t.start()
    try:
        assert seen.wait(10), "no known event while the scan blocks"
        assert s.project is None  # the scan has not finished
        # The spec's models, then the manifest's it did not name, once each.
        assert list(events[0]["models"]) == [A, B, C, D, G, "FakeLib.Gone"]
    finally:
        slow_scan.set()
        t.join(10)
    assert s.project is not None


def test_a_bad_tree_does_not_keep_the_known_rows_back(tmp_path, slow_scan):
    """The ids are read from each entry's `model`, not through the spec
    parser, which validates trees: a bad one is the merge's to raise, and
    an open that fails takes back what it named."""
    cfg, work = _project(tmp_path)
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["tests"][0]["metrics"] = {"metric": "no-such-metric", "variable": "x"}
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    s = Session()
    events: list[list[str]] = []
    s.on("known", lambda e: events.append(list(e["models"])))
    slow_scan.set()
    with pytest.raises(Exception):  # noqa: B017 - whatever the merge raises
        s.open(cfg, work_dir=work)
    assert events == [[A, B, C, D, G], []]


def test_the_shell_filters_known_ids_like_visible_tests(tmp_path, slow_scan):
    pytest.importorskip("nicegui")
    from dstf.authoring.shell.shell import Shell

    cfg, work = _project(tmp_path)
    sh = Shell(cfg, work_dir=work, filter="FakeLib.[AB]")
    sh.open_async()
    try:
        for _ in range(100):
            sh.tick()
            if sh.visible_tests():
                break
            threading.Event().wait(0.05)
        assert sh.session.project is None
        assert sh.visible_tests() == [A, B]
    finally:
        slow_scan.set()
        sh._open_thread.join(10)
    sh.tick()
    assert sh.known == []  # the merge's `spec` event replaced them
    assert sh.visible_tests() == [A, B]


def test_a_close_drops_the_known_ids(tmp_path):
    """Every `spec` event clears them: a close otherwise left
    `visible_tests()` returning the ids of a project no longer open."""
    pytest.importorskip("nicegui")
    from dstf.authoring.shell.shell import Shell

    cfg, work = _project(tmp_path)
    sh = Shell(cfg, work_dir=work)
    sh.session._emit("known", models=[A, B])
    sh.tick()
    assert sh.visible_tests() == [A, B]
    sh.session.close()
    sh.tick()
    assert sh.visible_tests() == []


def _recording(monkeypatch) -> list[dict]:
    from dstf.authoring.shell.suite_grid import SuiteGrid

    sent: list[dict] = []

    def record(self, *, add=(), update=(), remove=(), select=()):
        sent.append(
            {
                "add": [(r.model_id, r.status_text) for r in add],
                "update": [(r.model_id, r.status_text) for r in update],
                "remove": sorted(remove),
                "select": sorted(select),
            }
        )

    monkeypatch.setattr(SuiteGrid, "transaction", record)
    return sent


async def _until(predicate, tries: int = 200) -> None:
    for _ in range(tries):
        if predicate():
            return
        await asyncio.sleep(0.05)
    assert predicate()


def _final(sent: list[dict], initial: dict[str, str] | None = None) -> dict[str, str]:
    """The grid's rows after `sent`, from the rows it was built with."""
    final: dict[str, str] = dict(initial or {})
    for tx in sent:
        for m in tx["remove"]:
            final.pop(m, None)
        for m, text in tx["add"] + tx["update"]:
            final[m] = text
    return final


async def test_the_suite_page_draws_known_rows_waiting_then_the_scans(
    tmp_path, slow_scan, monkeypatch
):
    pytest.importorskip("nicegui")
    from nicegui.testing.user_simulation import user_simulation

    from dstf.authoring import capabilities as caps
    from dstf.authoring.builtin import register_builtins
    from dstf.authoring.shell.shell import Shell
    from dstf.authoring.shell.suite import SuitePage

    caps._reset_for_tests()
    register_builtins()
    sent = _recording(monkeypatch)
    cfg, work = _project(tmp_path)
    # A model the last run had and the library no longer does: drawn while
    # the scan runs, dropped when the scan does not find it.
    _with_manifest(work, "FakeLib.Gone")
    sh = Shell(cfg, work_dir=work)
    try:
        async with user_simulation(root=sh.index) as user:
            await user.open("/")
            await user.should_see(marker="wordmark", retries=20)
            (page,) = [p for p in sh.pages if isinstance(p, SuitePage)]
            page.state.checked = {D}
            sh.open_async()
            await _until(lambda: any(tx["add"] for tx in sent))
            assert sh.session.project is None
            (first,) = [tx for tx in sent if tx["add"]]
            assert first["add"] == [
                (m, "SCORING") for m in (A, B, C, D, G, "FakeLib.Gone")
            ]
            # The tab's selection reaches rows drawn after the grid was ready.
            assert first["select"] == [D]
            slow_scan.set()
            await _until(lambda: sh.load.phase == "done")
            await asyncio.sleep(0.3)
            assert _final(sent) == {
                A: "FAIL",
                B: "NO_REF",
                C: "NOT RUN",  # its run never finished it (D145)
                D: "PASS",
                G: "PASS",
            }
            assert page.ids == [A, B, C, D, G]
    finally:
        slow_scan.set()
        caps._reset_for_tests()
        register_builtins()


def _grid_rows(sh) -> dict[str, str]:
    """The rows the one Suite page built its grid with: id -> status text."""
    from dstf.authoring.shell.suite import SuitePage

    (page,) = [p for p in sh.pages if isinstance(p, SuitePage)]
    return {r["id"]: r["status"] for r in page.grid.options["rowData"]}


async def test_a_reopen_draws_the_known_rows_waiting(tmp_path, monkeypatch):
    """During a reopen the old project is still open until the merge: its
    comparisons are not the new open's, so the known rows wait — the known
    ones, not the old project's tests (B, C and G drop out, a model only
    the last run names is added)."""
    pytest.importorskip("nicegui")
    from nicegui.testing.user_simulation import user_simulation

    from dstf.authoring import capabilities as caps
    from dstf.authoring.builtin import register_builtins
    from dstf.authoring.shell.shell import Shell

    caps._reset_for_tests()
    register_builtins()
    sent = _recording(monkeypatch)
    cfg, work = _project(tmp_path)
    sh = Shell(cfg, work_dir=work)
    sh.session.open(cfg, work_dir=work)
    sh.load.phase = "done"
    try:
        async with user_simulation(root=sh.index) as user:
            await user.open("/")
            await user.should_see(marker="wordmark", retries=20)
            initial = _grid_rows(sh)
            assert set(initial) == {A, B, C, D, G}
            sh.session._emit("known", models=[A, D, "FakeLib.Gone"])
            sh.tick()
            assert _final(sent, initial) == {
                A: "SCORING",
                D: "SCORING",
                "FakeLib.Gone": "SCORING",
            }
            assert sh.session.project is not None  # the old one
    finally:
        caps._reset_for_tests()
        register_builtins()


async def test_a_failed_reopen_draws_the_old_projects_rows_again(tmp_path, monkeypatch):
    """An open that fails takes back what it named (`known` with none):
    every row the grid holds reads the open project's verdict again, not
    SCORING — the `known []` event names no model, and repainting only the
    event's models left every row waiting for good."""
    pytest.importorskip("nicegui")
    from nicegui.testing.user_simulation import user_simulation

    from dstf.authoring import capabilities as caps
    from dstf.authoring.builtin import register_builtins
    from dstf.authoring.shell.shell import Shell

    caps._reset_for_tests()
    register_builtins()
    sent = _recording(monkeypatch)
    cfg, work = _project(tmp_path)
    sh = Shell(cfg, work_dir=work)
    sh.session.open(cfg, work_dir=work)
    sh.load.phase = "done"
    # A spec the merge refuses, behind a scan that blocks (cold).
    spec_path = cfg.parent / "test_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["tests"][0]["metrics"] = {"metric": "no-such-metric", "variable": "x"}
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    gate = threading.Event()
    real = session_mod.scan_source_tests

    def blocked(config, **kw):
        assert gate.wait(10)
        return real(config, **kw)

    monkeypatch.setattr(session_mod, "load_scan", lambda config: None)
    monkeypatch.setattr(session_mod, "scan_source_tests", blocked)
    try:
        async with user_simulation(root=sh.index) as user:
            await user.open("/")
            await user.should_see(marker="wordmark", retries=20)
            initial = _grid_rows(sh)
            assert initial == {
                A: "FAIL",
                B: "NO_REF",
                C: "NOT RUN",  # its run never finished it (D145)
                D: "PASS",
                G: "PASS",
            }
            sh.open_async()
            await _until(lambda: bool(sh.known))
            await _until(lambda: _final(sent, initial)[A] == "SCORING")
            gate.set()
            sh._open_thread.join(10)
            await _until(lambda: sh.load.error is not None)
            await asyncio.sleep(0.3)
            assert sh.session.project is not None  # the old one, still open
            assert _final(sent, initial) == initial
    finally:
        gate.set()
        caps._reset_for_tests()
        register_builtins()


async def test_a_model_named_and_dropped_in_one_tick_is_never_sent(
    tmp_path, monkeypatch
):
    """A page drawn before the open; the open (a scan-cache hit, so no
    tick between its events) names a model only the last run has, then
    its merge drops it: in one tick the model is added and removed, and the
    grid is sent neither — a `remove` for a row it never had."""
    pytest.importorskip("nicegui")
    from nicegui.testing.user_simulation import user_simulation

    from dstf.authoring import capabilities as caps
    from dstf.authoring.builtin import register_builtins
    from dstf.authoring.shell.shell import Shell

    caps._reset_for_tests()
    register_builtins()
    sent = _recording(monkeypatch)
    cfg, work = _project(tmp_path)
    _with_manifest(work, "FakeLib.Gone")
    sh = Shell(cfg, work_dir=work)
    try:
        async with user_simulation(root=sh.index) as user:
            await user.open("/")
            await user.should_see(marker="wordmark", retries=20)
            sh.session.open(cfg, work_dir=work)
            sh.tick()
            (tx,) = sent
            assert [m for m, _ in tx["add"]] == [A, B, C, D, G]
            assert tx["remove"] == []
    finally:
        caps._reset_for_tests()
        register_builtins()
