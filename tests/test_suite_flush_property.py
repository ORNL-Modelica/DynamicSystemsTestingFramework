"""The Suite grid holds the truth after every tick (`SuitePage.flush`): over
hand-built and seeded random sequences of Session events — opens, closes,
`known` rows, staged edits, discards, tests joining, starting in and leaving
the simulation queue, rescores and a spec changed on disk — the rows a fake
grid holds after the tick's ONE transaction equal the rows the page would
build now, and no transaction adds a row the grid holds, updates one it
lacks or removes one it never had. Adopted from the S2a review's harness;
`NSEEDS` widens the random sweep."""

from __future__ import annotations

import json
import logging
import os
import random
import time

import pytest

pytest.importorskip("nicegui")

from nicegui.testing.user_simulation import user_simulation  # noqa: E402

from dstf.authoring import capabilities as caps  # noqa: E402
from dstf.authoring.builtin import register_builtins  # noqa: E402
from dstf.authoring.shell.shell import Shell  # noqa: E402
from dstf.authoring.shell.suite import SuitePage  # noqa: E402
from dstf.authoring.shell.suite_grid import SuiteGrid, row_json  # noqa: E402
from dstf.backends.pool import ItemState  # noqa: E402
from test_session import LOOSEN, A, B, C, D, G, _project  # noqa: E402
from test_session_queue import drop_item, fake_item  # noqa: E402

ALL = [A, B, C, D, G]


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


class FakeGrid:
    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.errors: list[str] = []


@pytest.fixture
def grid(monkeypatch):
    g = FakeGrid()

    def tx(self, *, add=(), update=(), remove=(), select=()):
        for r in add:
            if r.model_id in g.rows:
                g.errors.append(f"add of held {r.model_id}")
            g.rows[r.model_id] = json.loads(json.dumps(row_json(r)))
        for r in update:
            if r.model_id not in g.rows:
                g.errors.append(f"update of absent {r.model_id}")
            g.rows[r.model_id] = json.loads(json.dumps(row_json(r)))
        for m in remove:
            if m not in g.rows:
                g.errors.append(f"remove of absent {m}")
            g.rows.pop(m, None)

    monkeypatch.setattr(SuiteGrid, "transaction", tx)
    return g


async def _open(user):
    await user.open("/")
    await user.should_see(marker="wordmark", retries=20)


def _page(shell, user) -> SuitePage:
    (p,) = [
        x for x in shell.pages if isinstance(x, SuitePage) and x.client is user.client
    ]
    return p


def _truth(shell, page):
    ids = shell.visible_tests()
    return {r.model_id: json.loads(json.dumps(row_json(r))) for r in page._rows(ids)}


def _check(shell, page, grid, label):
    assert not grid.errors, (label, grid.errors)
    truth = _truth(shell, page)
    assert set(grid.rows) == set(truth), (label, sorted(grid.rows), sorted(truth))
    for m in truth:
        assert grid.rows[m] == truth[m], (label, m, grid.rows[m], truth[m])


def _init(page, grid):
    grid.rows = {
        r["id"]: r for r in json.loads(json.dumps(page.grid.options["rowData"]))
    }


# -- actions -------------------------------------------------------------------


def _held(shell) -> bool:
    """Whether the queue holds a test: an open and a close are refused then."""
    return bool(shell.session.queue)


def act_known_subset(shell, rng):
    k = rng.sample(ALL, rng.randint(0, 5))
    shell._events.put({"event": "known", "models": k})


def act_known_clear(shell, rng):
    shell._events.put({"event": "known", "models": []})


def act_close_open(shell, rng):
    if _held(shell) or shell.session.spec.pending:
        return
    shell.session.close()
    shell.session.open(shell.config_path, work_dir=shell.work_dir)


def act_close(shell, rng):
    if _held(shell) or shell.session.spec.pending:
        return
    shell.session.close()


def act_open(shell, rng):
    if _held(shell) or shell.session.spec.pending:
        return
    shell.session.open(shell.config_path, work_dir=shell.work_dir)


def act_stage(shell, rng):
    # Not refused while the queue holds its test: a queued or running test
    # stays editable.
    if shell.session.project is None:
        return
    try:
        shell.session.stage([LOOSEN])
    except Exception:
        pass


def act_discard(shell, rng):
    if shell.session.project is None:
        return
    shell.session.discard()


def act_queue(shell, rng):
    """One to three tests join the queue, waiting; one it holds is skipped."""
    session = shell.session
    if session.project is None:
        return
    models = [
        m for m in rng.sample(ALL, rng.randint(1, 3)) if session.queue_state(m) is None
    ]
    if not models:
        return
    for m in models:
        fake_item(session, m, ItemState.WAITING)
    shell._events.put({"event": "queue", "state": "queued", "models": set(models)})


def act_start(shell, rng):
    """The first waiting test starts."""
    item = next((i for i in shell.session.queue if i.state is ItemState.WAITING), None)
    if item is None:
        return
    item.state, item.started_at, item.worker_id = ItemState.RUNNING, time.time(), 0
    shell._events.put({"event": "queue", "state": "started", "models": {item.model_id}})


def act_leave(shell, rng):
    """One of the tests the queue holds leaves it."""
    session = shell.session
    if not session.queue:
        return
    item = rng.choice(session.queue)
    drop_item(session, item)
    shell._events.put({"event": "queue", "state": "removed", "models": {item.model_id}})


def act_results(shell, rng):
    shell._events.put({"event": "results", "models": set(rng.sample(ALL, 2))})


def act_spec_change_disk(shell, rng):
    """Change A's tolerance on disk, then close+open in one tick: A's row
    is dropped and named again with CHANGED data."""
    if _held(shell) or shell.session.spec.pending or shell.session.project is None:
        return
    p = shell.session.spec.path
    spec = json.loads(p.read_text(encoding="utf-8"))
    for e in spec["tests"]:
        if e["model"] == A:
            c = e.setdefault("comparison", {})
            c["tolerance"] = 0.5 if c.get("tolerance") != 0.5 else 0.01
    p.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    shell.session.close()
    shell.session.open(shell.config_path, work_dir=shell.work_dir)


ACTIONS = [
    act_known_subset,
    act_known_clear,
    act_close_open,
    act_close,
    act_open,
    act_stage,
    act_discard,
    act_queue,
    act_start,
    act_leave,
    act_results,
    act_spec_change_disk,
]


_OPENING = {"on": False}


def _pick(shell, rng):
    """An open under way (a `known` outstanding) refuses every other act, as
    the shell's busy_reason does: only its merge (an open) or its failure
    (`known []`) follows."""
    if _OPENING["on"]:
        _OPENING["on"] = False
        return rng.choice([act_known_clear, act_open_force])
    a = rng.choice(ACTIONS)
    if a is act_known_subset:
        if _held(shell) or shell.session.spec.pending:
            return act_results
        _OPENING["on"] = True
    return a


def act_open_force(shell, rng):
    shell.session.open(shell.config_path, work_dir=shell.work_dir)


async def test_named_sequences(shell, grid):
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        page = _page(shell, user)
        _init(page, grid)
        rng = random.Random(0)
        seqs = [
            [act_known_subset, act_known_clear],
            [act_known_subset],
            [act_known_clear],
            [act_stage, act_discard],
            [act_close, act_open],
            [act_spec_change_disk],
            [act_queue],
            [act_start],
            [act_stage],  # while the queue holds a test
            [act_discard],
            [act_leave, act_leave, act_leave],  # the queue is empty again
            [act_close],
            [act_known_subset, act_open],
            [act_open],
        ]
        for i, seq in enumerate(seqs):
            for a in seq:
                a(shell, rng)
            shell.tick()
            _check(shell, page, grid, f"seq {i} {[a.__name__ for a in seq]}")


@pytest.mark.parametrize("seed", range(int(os.environ.get("NSEEDS", "10"))))
async def test_random_sequences(shell, grid, seed):
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        page = _page(shell, user)
        _init(page, grid)
        rng = random.Random(seed)
        for tick in range(12):
            names = []
            for _ in range(rng.randint(1, 4)):
                a = _pick(shell, rng)
                names.append(a.__name__)
                a(shell, rng)
            shell.tick()
            # the truth only after the known phase is over: shell.known rows wait
            _check(shell, page, grid, f"seed {seed} tick {tick} {names}")


async def test_a_row_that_never_builds_does_not_freeze_the_grid(
    shell, grid, monkeypatch, caplog
):
    """A model whose row always raises: the tick after the failure sends the
    other rows without it and moves on — the grid does not stop taking
    updates, and the failure is logged twice at most, not on every tick."""
    import dstf.authoring.shell.suite as suite_mod

    real = suite_mod.suite_rows

    def broken(session, ids, **kw):
        if B in ids:
            raise RuntimeError("B's row is broken")
        return real(session, ids, **kw)

    def truth(m):
        (row,) = real(shell.session, [m], failed=set())
        return json.loads(json.dumps(row_json(row)))

    async with user_simulation(root=shell.index) as user:
        await _open(user)
        page = _page(shell, user)
        _init(page, grid)
        monkeypatch.setattr(suite_mod, "suite_rows", broken)
        with caplog.at_level("WARNING"):
            shell._events.put({"event": "results", "models": {A, B}})
            shell.session.stage([LOOSEN])  # A's verdict changes
            for _ in range(2):
                shell.tick()
            assert grid.rows[A] == truth(A)
            for _ in range(20):
                shell.tick()
            shell.session.discard()  # a later update reaches the grid too
            shell.tick()
            assert grid.rows[A] == truth(A)
        assert not grid.errors
        logged = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert 1 <= len(logged) <= 2, [r.getMessage() for r in logged]


async def test_a_row_that_never_builds_is_not_added_and_is_tried_again_when_touched(
    shell, grid, monkeypatch
):
    """An added row dropped by a failing build is not in the grid, so it is
    never updated or removed there; it is added once an event touches it
    and its row builds."""
    import dstf.authoring.shell.suite as suite_mod

    real = suite_mod.suite_rows
    broken_on = {"yes": True}

    def broken(session, ids, **kw):
        if broken_on["yes"] and B in ids:
            raise RuntimeError("B's row is broken")
        return real(session, ids, **kw)

    async with user_simulation(root=shell.index) as user:
        await _open(user)
        page = _page(shell, user)
        _init(page, grid)
        monkeypatch.setattr(suite_mod, "suite_rows", broken)
        shell.session.close()
        shell.tick()  # every row removed: no row is built
        assert grid.rows == {}
        shell.session.open(shell.config_path, work_dir=shell.work_dir)
        for _ in range(3):
            shell.tick()  # every row added but B, whose row fails
        assert set(grid.rows) == set(ALL) - {B}
        assert not grid.errors
        broken_on["yes"] = False
        shell.tick()
        assert B not in grid.rows  # not tried again untouched
        shell._events.put({"event": "results", "models": {B}})
        shell.tick()
        _check(shell, page, grid, "B added once touched")


async def test_rows_dropped_by_a_failure_of_every_row_come_back_once_it_passes(
    shell, grid, monkeypatch
):
    """A failure that is no one row's (every build raises) lasting two ticks
    drops every row the open added: the retrying tick builds each and each
    raises. The next flush that builds tries the dropped rows once more, so
    the grid holds every visible row within a few idle ticks of healing."""
    import dstf.authoring.shell.suite as suite_mod

    real = suite_mod.suite_rows
    broken = {"yes": False}

    def flaky(session, ids, **kw):
        if broken["yes"] and list(ids):
            raise RuntimeError("every row is broken")
        return real(session, ids, **kw)

    async with user_simulation(root=shell.index) as user:
        await _open(user)
        page = _page(shell, user)
        _init(page, grid)
        monkeypatch.setattr(suite_mod, "suite_rows", flaky)
        shell.session.close()
        shell.tick()
        assert grid.rows == {}
        broken["yes"] = True
        shell.session.open(shell.config_path, work_dir=shell.work_dir)
        shell.tick()  # raises: the shell logs it
        shell.tick()  # row by row: every row raises and is dropped
        assert grid.rows == {}
        broken["yes"] = False
        for _ in range(3):
            shell.tick()
        _check(shell, page, grid, "every row back, untouched")


async def test_a_retried_row_that_still_fails_stays_out_without_a_log_line(
    shell, grid, monkeypatch, caplog
):
    """The one retry of the dropped rows builds each: a row whose own build
    still raises stays dropped, and is not logged a third time."""
    import dstf.authoring.shell.suite as suite_mod

    real = suite_mod.suite_rows
    broken = {"all": False}

    def flaky(session, ids, **kw):
        ids = list(ids)
        if ids and (broken["all"] or B in ids):
            raise RuntimeError("broken")
        return real(session, ids, **kw)

    async with user_simulation(root=shell.index) as user:
        await _open(user)
        page = _page(shell, user)
        _init(page, grid)
        monkeypatch.setattr(suite_mod, "suite_rows", flaky)
        shell.session.close()
        shell.tick()
        broken["all"] = True
        shell.session.open(shell.config_path, work_dir=shell.work_dir)
        with caplog.at_level("WARNING"):
            shell.tick()
            shell.tick()
            n = len([r for r in caplog.records if r.levelno >= logging.WARNING])
            broken["all"] = False
            for _ in range(3):
                shell.tick()
            assert len([r for r in caplog.records if r.levelno >= logging.WARNING]) == n
        assert set(grid.rows) == set(shell.visible_tests()) - {B}
        assert not grid.errors


async def test_a_failure_after_a_successful_flush_gets_its_own_traceback(
    shell, grid, monkeypatch, caplog
):
    """The row-by-row retry is for the ONE flush after a failure: once a
    flush succeeds, the next failure raises again and the shell logs its
    traceback, rather than a one-line "left out"."""
    import dstf.authoring.shell.suite as suite_mod

    real = suite_mod.suite_rows
    broken = {"yes": False}

    def flaky(session, ids, **kw):
        if broken["yes"] and list(ids):
            raise RuntimeError("every row is broken")
        return real(session, ids, **kw)

    def tracebacks() -> int:
        return len(
            [r for r in caplog.records if r.getMessage() == "a page failed to flush"]
        )

    async with user_simulation(root=shell.index) as user:
        await _open(user)
        page = _page(shell, user)
        _init(page, grid)
        monkeypatch.setattr(suite_mod, "suite_rows", flaky)
        with caplog.at_level("WARNING"):
            broken["yes"] = True
            shell._events.put({"event": "results", "models": {A}})
            shell.tick()
            assert tracebacks() == 1
            shell.tick()  # row by row
            broken["yes"] = False
            shell._events.put({"event": "results", "models": {A}})
            shell.tick()  # succeeds
            broken["yes"] = True
            shell._events.put({"event": "results", "models": {A}})
            shell.tick()
            assert tracebacks() == 2
