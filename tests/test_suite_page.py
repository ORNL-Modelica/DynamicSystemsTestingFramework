"""The Suite page's table, Python-side (brief §3.2, §3.8; the Suite table,
S2a): the rows Python sends the grid — every column's text and sort value,
from the row model — the selection it receives, kept in the tab across a
navigation, the icon actions' availability, and the grid refusing an
element update (NiceGUI's aggrid rebuilds the grid on one). The grid's rows,
filters, sort and groups live in the browser: `test_author_suite_playwright`
drives them. In NiceGUI's user simulation."""

from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

pytest.importorskip("nicegui")

from nicegui.testing.user_simulation import user_simulation  # noqa: E402

from dstf.authoring import capabilities as caps  # noqa: E402
from dstf.authoring.builtin import register_builtins  # noqa: E402
from dstf.authoring.shell.shell import Shell  # noqa: E402
from dstf.authoring.shell.suite import SuitePage  # noqa: E402
from dstf.backends.pool import ItemState  # noqa: E402
from test_session import A, B, C, D, G, _project  # noqa: E402
from test_session_queue import drop_item, fake_item  # noqa: E402


@pytest.fixture(autouse=True)
def _registry():
    caps._reset_for_tests()
    register_builtins()
    yield
    caps._reset_for_tests()
    register_builtins()


@pytest.fixture
def shell(tmp_path):
    cfg, work = _project(tmp_path)
    s = Shell(cfg, work_dir=work)
    s.session.open(cfg, work_dir=work)
    s.load.phase = "done"
    return s


async def _settled(predicate, tries: int = 60) -> None:
    for _ in range(tries):
        if predicate():
            return
        await asyncio.sleep(0.05)
    assert predicate()


def _one(user, marker: str):
    (element,) = user.find(marker=marker).elements
    return element


async def _open(user, path: str = "/") -> None:
    """Open the page AND wait for it to be built: every page awaits its
    client's handshake before drawing, and `user.open` returns as soon as
    the handshake is sent — the page resumes on the next loop iteration.
    The header's wordmark is the first element every page draws."""
    await user.open(path)
    await user.should_see(marker="wordmark", retries=20)


def _suite(shell, user) -> SuitePage:
    """The Suite page built for the user's CURRENT client: a page the user
    navigated away from stays registered (the simulation never deletes its
    client), so the shell may hold several."""
    (page,) = [
        p for p in shell.pages if isinstance(p, SuitePage) and p.client is user.client
    ]
    return page


def short(model_id: str) -> str:
    return model_id.rsplit(".", 1)[-1]


def _sent(page) -> dict[str, dict]:
    """The rows the page built the grid with, by id (group rows are the
    browser's to add)."""
    return {r["id"]: r for r in page.grid.options["rowData"]}


async def test_the_rows_python_sends_carry_every_columns_text_and_sort(shell):
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        page = _suite(shell, user)
        rows = _sent(page)
        assert list(rows) == [A, B, C, D, G]  # the project's order
        a = rows[A]
        assert a["status"] == "FAIL" and a["status_class"] == "fail"
        assert a["status__sort"] is not None
        assert (a["_name_short"], a["_name_full"], a["_pkg"]) == ("A", A, "FakeLib")
        assert a["model"] == A  # the Test column filters on the full name
        assert a["nrmse__sort"] > 0.1 and a["nrmse"] == format(a["nrmse__sort"], ".4e")
        assert a["edits"] == "" and a["edits__sort"] is None
        assert {rows[m]["status"] for m in (D, G)} == {"PASS"}
        # C's run never finished it: NOT RUN (D145).
        assert (rows[B]["status"], rows[C]["status"]) == ("NO_REF", "NOT RUN")
        # A text column with no value filters as empty (S1's filter_text).
        assert rows[B]["ref"] == "—" and rows[B]["ref__sort"] is None


async def test_the_grid_offers_the_tools_columns(shell):
    """The tool's columns: the row model's, bar Key / Worker / Elapsed —
    a live run's, always "—" in the tool, so not even the chooser offers
    them — plus Edits, shown before Message; the chooser offers the hidden
    ones."""
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        defs = _suite(shell, user).grid.options["columnDefs"]
        shown = [d["colId"] for d in defs if not d.get("hide")]
        assert shown == [
            "status",
            "model",
            "nrmse",
            "criteria",
            "sim",
            "warnings",
            "edits",
            "detail",
        ]
        hidden = {d["colId"] for d in defs if d.get("hide")}
        assert hidden == {"ref", "resolution", "translate", "total"}
        labels = {d["colId"]: d["headerName"] for d in defs}
        assert (labels["model"], labels["edits"]) == ("Test", "Edits")
        nrmse = next(d for d in defs if d["colId"] == "nrmse")
        assert nrmse["width"] >= 130  # "1.5309e-01" and its header at 1280
        # Message takes the width left over.
        detail = next(d for d in defs if d["colId"] == "detail")
        assert detail["flex"] == 1 and detail["minWidth"] > 0
        # Nor does a row carry them.
        row = _sent(_suite(shell, user))[A]
        assert not {"test", "worker", "elapsed"} & set(row)


async def test_the_selection_the_grid_reports_is_the_tabs_and_the_actions(shell):
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        page = _suite(shell, user)
        queue = page.actions.actions["enqueue"].button
        assert not queue.enabled
        page._on_selection([A, D])
        assert page.state.checked == {A, D}
        assert queue.enabled
        assert queue.props["aria-label"] == "Queue 2"
        page._on_selection([])
        assert page.state.checked == set() and not queue.enabled


async def test_a_selection_survives_a_visit_to_a_test(shell):
    """Brief §3.2: the selection is the tab's, so it is there when the Test
    page hands the tab back: the new page's grid selects it once ready."""
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        first = _suite(shell, user)
        first._on_selection([D, G])
        await user.open(f"/test/{A}")
        await user.should_see("← Suite", retries=20)
        user.find("← Suite").click()
        await _settled(lambda: _suites(shell, user))
        second = _suite(shell, user)
        assert second is not first and second.state is first.state
        ready = second.grid.options[":onGridReady"]
        assert json.dumps([D, G]) in ready


def _suites(shell, user):
    return [
        p for p in shell.pages if isinstance(p, SuitePage) and p.client is user.client
    ]


async def test_a_sealed_grid_refuses_an_element_update(shell):
    """NiceGUI's aggrid re-creates the grid on any element update: sort,
    filters, selection and every patched row lost. `.classes` / `.props`
    reach `update` through an observable whose handler LOGS what it raises,
    so the guard blocks the update and the caller sees no raise; a direct
    `update()` raises."""
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        page = _suite(shell, user)
        outbox = page.client.outbox
        outbox.updates.pop(page.grid.id, None)
        page.grid.classes(add="touched")
        assert page.grid.id not in outbox.updates
        with pytest.raises(RuntimeError, match="driven by run_grid_method"):
            page.grid.update()


# -- rows repaint live: one transaction per tick ------------------------------


@pytest.fixture
def sent(monkeypatch) -> list[dict]:
    """Every transaction the Suite grid sends, as model ids / (id, status,
    edits) — what reached the browser, not what the Session holds."""
    from dstf.authoring.shell.suite_grid import SuiteGrid

    out: list[dict] = []

    def record(self, *, add=(), update=(), remove=(), select=()):
        out.append(
            {
                "add": [r.model_id for r in add],
                "added": [(r.model_id, r.status, r.edits) for r in add],
                "update": [(r.model_id, r.status, r.edits) for r in update],
                "remove": sorted(remove),
                "select": sorted(select),
            }
        )

    monkeypatch.setattr(SuiteGrid, "transaction", record)
    return out


def _tx(update=(), remove=()) -> dict:
    """A transaction that adds nothing."""
    return {
        "add": [],
        "added": [],
        "update": list(update),
        "remove": sorted(remove),
        "select": [],
    }


def _loaded(model_id: str) -> dict:
    return {"event": "loading", "phase": "results", "model": model_id, "done": 1}


async def test_a_scored_model_sends_its_row_alone(shell, sent):
    """Not the table: one row, in one transaction."""
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        shell._events.put(_loaded(D))
        shell.tick()
        assert sent == [_tx(update=[(D, "PASS", 0)])]
        sent.clear()
        shell._events.put({"event": "results", "models": {A, B}})
        shell.tick()
        assert sent == [_tx(update=[(A, "FAIL", 0), (B, "NO_REF", 0)])]
        shell.tick()  # nothing queued, nothing sent
        assert len(sent) == 1


async def test_a_staged_edit_sends_its_rescored_row_once(shell, sent):
    """`stage` emits `spec` then `results` for A in one tick: ONE
    transaction, A's row with its edits and its rescored verdict. The
    Accept path queues N + 2 events in a tick; per event, 333
    transactions over 331 rows."""
    from test_session import LOOSEN

    async with user_simulation(root=shell.index) as user:
        await _open(user)
        shell.session.stage([LOOSEN])
        shell.tick()
        assert sent == [_tx(update=[(A, "PASS", 1)])]


async def test_a_spec_event_adds_and_removes_rows(shell, sent):
    """A `spec` event names only the CHANGED models (a close names none):
    the rows to add and remove are the visible tests against the grid's."""
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        page = _suite(shell, user)
        page.state.checked = {D}
        shell.session.close()
        shell.tick()
        assert sent == [_tx(remove=[A, B, C, D, G])]
        assert page.ids == []
        sent.clear()
        shell.session.open(shell.config_path, work_dir=shell.work_dir)
        shell.tick()
        # The rows come back in the project's order; the tab's selection
        # rides with them.
        assert sent[0]["add"] == [A, B, C, D, G] and sent[0]["select"] == [D]
        assert page.ids == [A, B, C, D, G]


async def test_a_new_test_in_the_spec_gets_a_row(shell, sent):
    """A model the spec newly names (by editing the file and reloading,
    the rescan's and Save's path) is added; the others are only updated."""
    spec_path = shell.session.spec.path
    (spec_path.parent.parent / "Examples" / "E.py").write_text(
        "def simulate(stop_time, tolerance):\n    return {}\n", encoding="utf-8"
    )
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        spec["tests"].append(
            {
                "model": "FakeLib.E",
                "source": "../Examples/E.py",
                "variables": ["x"],
                "simulation": {"stop_time": 1.0, "tolerance": 1e-6},
            }
        )
        spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
        shell.session.reload()
        shell.tick()
        assert len(sent) == 1
        assert sent[0]["add"] == ["FakeLib.E"] and sent[0]["remove"] == []
        assert ("FakeLib.E", "SIM_FAIL", 0) not in sent[0]["update"]  # added, once
        assert "FakeLib.E" in _suite(shell, user).ids


async def test_a_queue_event_paints_its_rows_queued_running_then_their_verdicts(
    shell, sent
):
    """The rows a `queue` event names are repainted, and no other: QUEUED
    while a test waits, RUNNING once it starts, its verdict when the queue
    no longer holds it."""
    session = shell.session
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        a = fake_item(session, A, ItemState.WAITING)
        d = fake_item(session, D, ItemState.WAITING)
        session._emit("queue", state="queued", models={A, D})
        shell.tick()
        assert sent == [_tx(update=[(A, "QUEUED", 0), (D, "QUEUED", 0)])]
        sent.clear()
        a.state, a.started_at, a.worker_id = ItemState.RUNNING, time.time(), 0
        session._emit("queue", state="started", models={A})
        shell.tick()
        assert sent == [_tx(update=[(A, "RUNNING", 0)])]  # D's row is not sent
        sent.clear()
        drop_item(session, a)
        drop_item(session, d)
        session._emit("queue", state="removed", models={A, D})
        shell.tick()
        assert sent == [_tx(update=[(A, "FAIL", 0), (D, "PASS", 0)])]


def _gate_per_model(monkeypatch) -> dict[str, threading.Event]:
    """Each result is handed on when ITS gate is set, so rows can be seen
    filling in one at a time. The reader hands its results on in its thread
    pool's completion order (`Backend.read_results` iterates `as_completed`),
    which is not this test's order, so the results are collected first and
    released, on the reader's thread, as their gates open."""
    from dstf.backends.base import Backend

    real = Backend.read_last_results
    gates: dict[str, threading.Event] = {m: threading.Event() for m in (A, B, C, D, G)}

    def gated(self, tests, on_result=None):
        held: dict[str, object] = {}
        results = real(self, tests, lambda m, r: held.__setitem__(m, r))
        deadline = time.monotonic() + 10
        while held:
            assert time.monotonic() < deadline
            for model_id in list(held):
                if gates[model_id].is_set():
                    result = held.pop(model_id)
                    if on_result is not None:
                        on_result(model_id, result)
            time.sleep(0.01)
        return results

    monkeypatch.setattr(Backend, "read_last_results", gated)
    return gates


async def test_rows_fill_in_as_results_are_scored(tmp_path, monkeypatch, sent):
    """Brief §3.8: the rows fill in as results are scored — per `loading`
    event, not at the end. A page drawn before the open has no rows; the
    merge adds them waiting, each result repaints its own."""
    gates = _gate_per_model(monkeypatch)
    cfg, work = _project(tmp_path)
    shell = Shell(cfg, work_dir=work)
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        page = _suite(shell, user)
        shell.open_async()
        await _settled(lambda: page.ids == [A, B, C, D, G])
        added = [tx for tx in sent if tx["add"]]
        assert added[0]["add"] == [A, B, C, D, G]
        gates[A].set()
        await _settled(lambda: (A, "FAIL", 0) in _updates(sent))
        assert (D, "PASS", 0) not in _updates(sent)  # still gated
        for gate in gates.values():
            gate.set()
        await _settled(lambda: shell.load.phase == "done")
        await _settled(lambda: (D, "PASS", 0) in _updates(sent))
        assert (C, "NOT RUN", 0) in _updates(sent)


def _updates(sent: list[dict]) -> list[tuple]:
    """Every row sent, added or repainted, in order."""
    return [row for tx in sent for row in (*tx["added"], *tx["update"])]


async def test_a_model_whose_scoring_raised_reads_scoring_failed(
    tmp_path, monkeypatch, sent
):
    import dstf.authoring.session as session_mod
    from dstf.authoring.suite_rows import suite_row

    real = session_mod.compare_one

    def failing(test, *args, **kwargs):
        if test.model_id == D:
            raise ValueError("boom")
        return real(test, *args, **kwargs)

    monkeypatch.setattr(session_mod, "compare_one", failing)
    cfg, work = _project(tmp_path)
    shell = Shell(cfg, work_dir=work)
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        shell.open_async()
        await _settled(lambda: shell.load.phase == "done")
        await _settled(lambda: (D, "SIM_FAIL", 0) in _updates(sent))
        assert (A, "FAIL", 0) in _updates(sent)
        row = suite_row(shell.session, D, failed=shell.load.failed_models)
        assert row.message.startswith("scoring failed")


async def test_a_transaction_is_one_call_to_the_tables_script(shell):
    from dstf.authoring.shell.suite_grid import row_json
    from dstf.authoring.suite_rows import suite_row

    async with user_simulation(root=shell.index) as user:
        await _open(user)
        page = _suite(shell, user)
        calls: list[str] = []
        page.client.run_javascript = lambda code, **kw: calls.append(code)
        row = suite_row(shell.session, A)
        page.grid.transaction(update=[row], remove=[B], select=[A])
        (code,) = calls
        assert code.startswith("DstfSuite.apply(") and code.endswith(")")
        tx = json.loads(code[len("DstfSuite.apply(") : -1])
        assert tx == {
            "add": [],
            "update": [json.loads(json.dumps(row_json(row)))],
            "remove": [B],
            "select": [A],
        }


async def test_rows_dropped_and_named_again_in_one_tick_are_repainted(shell, sent):
    """A close and an open in one tick: a `spec` event is diffed against the
    visible tests as they are when the tick handles it — after both — so
    the grid keeps its rows and they are repainted, not removed and added
    again in one transaction."""
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        shell.session.close()
        shell.session.open(shell.config_path, work_dir=shell.work_dir)
        shell.tick()
        assert len(sent) == 1
        assert sent[0]["add"] == [] and sent[0]["remove"] == []
        assert [m for m, _, _ in sent[0]["update"]] == [A, B, C, D, G]


def test_the_count_pills_are_the_status_tables_and_include_not_run():
    """The Suite's pills are handed to its script from Python, in the words
    of `reporting.status` (the script spells none); NOT RUN has its own
    (D145), after NO_REF. Three come and go — each drawn only while a row
    counts under it, which the table says too (the script named two of
    them): SCORING, a row not scored yet, and the queue's QUEUED and
    RUNNING. A row waiting for its verdict counted under QUEUED."""
    from dstf.authoring.shell.suite_grid import PILLS, row_json, scripts_html
    from dstf.reporting.rows import TestRow

    assert [k for k, _, _ in PILLS] == [
        "pass",
        "fail",
        "sim-fail",
        "no-ref",
        "not-run",
        "edited",
        "scoring",
        "queued",
        "running",
    ]
    assert PILLS[-4:] == (
        ("edited", "EDITED", False),
        ("scoring", "SCORING", True),
        ("queued", "QUEUED", True),
        ("running", "RUNNING", True),
    )
    assert not any(passing for _, _, passing in PILLS[:5])
    assert {k: word for k, word, _ in PILLS}["not-run"] == "NOT RUN"
    assert 'window.DstfSuitePills = [["pass", "PASS", false]' in scripts_html("k")
    assert '["not-run", "NOT RUN", false]' in scripts_html("k")
    assert '["scoring", "SCORING", true]' in scripts_html("k")
    # what the script counts a row under
    waiting = row_json(TestRow(model_id="Lib.A", waiting=True))
    assert (waiting["status"], waiting["status_class"]) == ("SCORING", "scoring")


def test_the_views_storage_key_is_the_same_before_and_after_the_open(
    tmp_path, monkeypatch
):
    """The table's stored view is keyed by the config file: known before
    the open, and the same after it. By the work dir it was the config
    path before an open without `--work-dir` and the derived work dir
    after, so a reload after the open lost the view."""
    cfg, _work = _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    shell = Shell(cfg, work_dir=None)
    page = SuitePage(shell)
    before = page._project_key()
    shell.session.open(cfg)
    assert page._project_key() == before == str(cfg.resolve())


async def test_a_transaction_that_fails_is_sent_again_next_tick(shell, monkeypatch):
    """The grid holds what the last flush SENT: a transaction that raised
    (the client gone mid-send, a row that failed to build) is retried on
    the next tick, not dropped as though the grid had it."""
    from dstf.authoring.shell.suite_grid import SuiteGrid

    sent: list[list[str]] = []

    def once_failing(self, *, add=(), update=(), remove=(), select=()):
        if not sent:
            sent.append([])
            raise RuntimeError("the client went away")
        sent.append([r.model_id for r in (*add, *update)] + list(remove))

    monkeypatch.setattr(SuiteGrid, "transaction", once_failing)
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        shell._events.put(_loaded(D))
        shell.session.close()  # D's row and every other row leave
        shell.tick()  # the transaction raises (logged by the tick)
        shell.tick()  # nothing new: the same transaction, again
        assert sent == [[], [A, B, C, D, G]]
        shell.tick()
        assert len(sent) == 2
