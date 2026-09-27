"""The Suite grid holds the truth after every tick (`SuitePage.flush`): over
hand-built and seeded random sequences of Session events — opens, closes,
`known` rows, staged edits, discards, jobs, rescores and a spec changed on
disk — the rows a fake grid holds after the tick's ONE transaction equal
the rows the page would build now, and no transaction adds a row the grid
holds, updates one it lacks or removes one it never had. Adopted from the
S2a review's harness; `NSEEDS` widens the random sweep."""

from __future__ import annotations

import json
import os
import random

import pytest

pytest.importorskip("nicegui")

from nicegui.testing.user_simulation import user_simulation  # noqa: E402

from dstf.authoring import capabilities as caps  # noqa: E402
from dstf.authoring.builtin import register_builtins  # noqa: E402
from dstf.authoring.session import RunningJob  # noqa: E402
from dstf.authoring.shell.shell import Shell  # noqa: E402
from dstf.authoring.shell.suite import SuitePage  # noqa: E402
from dstf.authoring.shell.suite_grid import SuiteGrid, row_json  # noqa: E402
from test_session import LOOSEN, A, B, C, D, G, _project  # noqa: E402

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


def act_known_subset(shell, rng):
    k = rng.sample(ALL, rng.randint(0, 5))
    shell._events.put({"event": "known", "models": k})


def act_known_clear(shell, rng):
    shell._events.put({"event": "known", "models": []})


def act_close_open(shell, rng):
    if shell.session.running is not None or shell.session.spec.pending:
        return
    shell.session.close()
    shell.session.open(shell.config_path, work_dir=shell.work_dir)


def act_close(shell, rng):
    if shell.session.running is not None or shell.session.spec.pending:
        return
    shell.session.close()


def act_open(shell, rng):
    if shell.session.running is not None or shell.session.spec.pending:
        return
    shell.session.open(shell.config_path, work_dir=shell.work_dir)


def act_stage(shell, rng):
    if shell.session.project is None or shell.session.running is not None:
        return
    try:
        shell.session.stage([LOOSEN])
    except Exception:
        pass


def act_discard(shell, rng):
    # Refused while a job runs, as the header's busy_reason refuses it.
    if shell.session.project is None or shell.session.running is not None:
        return
    shell.session.discard()


def act_job_start(shell, rng):
    if shell.session.project is None or shell.session.running is not None:
        return
    job = RunningJob(
        label="rerun", models=frozenset(rng.sample(ALL, rng.randint(1, 3)))
    )
    shell.session.running = job
    shell._events.put({"event": "job", "state": "started", "job": job})


def act_job_finish(shell, rng):
    job = shell.session.running
    if job is None:
        return
    shell.session.running = None
    shell._events.put({"event": "job", "state": "finished", "job": job})


def act_results(shell, rng):
    shell._events.put({"event": "results", "models": set(rng.sample(ALL, 2))})


def act_spec_change_disk(shell, rng):
    """Change A's tolerance on disk, then close+open in one tick: A's row
    is dropped and named again with CHANGED data."""
    if (
        shell.session.running is not None
        or shell.session.spec.pending
        or shell.session.project is None
    ):
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
    act_job_start,
    act_job_finish,
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
        if shell.session.running is not None or shell.session.spec.pending:
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
            [act_job_start],
            [act_job_finish],
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
