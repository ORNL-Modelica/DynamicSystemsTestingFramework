"""The simulation pool over the real simulators — Dymola and OpenModelica.

Hardware tests, deselected in CI by their markers (`dymola`, `ompython`):
they need the tool installed and, for Dymola, a licence. Each builds a
scratch Modelica package in `tmp_path` — three quick models and a stiff one
that simulates for minutes — and asserts, beside the queue's behaviour, that
no tool process is left behind.

What they pin, measured where a number is printed (run with `-s`):

* Dymola: of three queued, one removed, one cancelled mid-simulation by
  dymosim's `stop` file with the worker REUSED, one finished and bit for bit
  the result of an uncancelled run; a cancel during translation, which only
  killing Dymola ends, and the worker restarted for the next item.
* OpenModelica: a run completes and equals `run_pipeline`'s; a cancel kills
  omc's children and omc is reused; omc killed during `loadModel` fails the
  waiting items with omc's own words instead of hanging.
* Both: a start given up — its bound passed, or the pool closed — while the
  tool is still coming up leaves no tool process, launched before the
  give-up or after it.

"No tool process is left" is asked of the whole machine, not of pytest's
descendants: a tool whose worker lost track of it is an orphan, and an
orphan hangs under init. Ours are told from another session's by a mark in
the environment every process a test launches inherits.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import time
import uuid
from pathlib import Path

import numpy as np
import psutil
import pytest

from dstf.backends import BackendUnavailableError, select_backend
from dstf.backends.pool import ItemState
from dstf.config import Config
from dstf.discovery.test_registry import discover_tests
from dstf.pipeline import open_pool, run_pipeline
from dstf.storage.reference_store import ReferenceStore

_PACKAGE = """\
within ;
package QueueLib "Scratch models for the simulation pool's hardware tests"
  model Fast
    Real x(start=1, fixed=true);
  equation
    der(x) = -x;
    annotation(experiment(StopTime=5, Tolerance=1e-6));
  end Fast;

  model Fast2
    Real x(start=0, fixed=true);
    Real y(start=1, fixed=true);
  equation
    der(x) = y;
    der(y) = -x;
    annotation(experiment(StopTime=10, Tolerance=1e-6));
  end Fast2;

  model Slow "A stiff nonlinear chain: minutes of solver steps"
    parameter Integer N=100;
    parameter Real k=1e6;
    Real x[N](each fixed=true, start={if i == 1 then 1.0 else 0.0 for i in 1:N});
    Real v[N](each fixed=true, each start=0);
  equation
    der(x) = v;
    der(v[1]) = -k*(2*x[1] - x[2]) - x[1]^3;
    for i in 2:N - 1 loop
      der(v[i]) = -k*(2*x[i] - x[i - 1] - x[i + 1]) - x[i]^3;
    end for;
    der(v[N]) = -k*(2*x[N] - x[N - 1]) - x[N]^3;
    annotation(experiment(StopTime=200, Tolerance=1e-8, NumberOfIntervals=500));
  end Slow;
end QueueLib;
"""

_TOOLS = ("dymola", "dymosim", "omc")
#: The environment variable `no_tool_left` sets for the length of a test.
_MARK = "DSTF_POOL_TEST_MARK"


def _project(tmp_path: Path, backend_name: str, work: str):
    """The scratch project on *backend_name*: `(config, tests by short
    name, backend)`. Skips when the backend cannot be built here."""
    lib = tmp_path / "QueueLib"
    if not lib.exists():
        lib.mkdir()
        (lib / "package.mo").write_text(_PACKAGE)
        (tmp_path / "testing.json").write_text(
            json.dumps(
                {
                    "source_path": "QueueLib",
                    "reference_root": "refs",
                    "test_spec": "test_spec.json",
                    "backends": {
                        "Dymola": [shutil.which("dymola") or "dymola"],
                        "OpenModelica": [shutil.which("omc") or "omc"],
                    },
                    "dependencies": [],
                }
            )
        )
        (tmp_path / "test_spec.json").write_text(
            json.dumps(
                {
                    "tests": [
                        {"model": "QueueLib.Fast", "variables": ["x"]},
                        {"model": "QueueLib.Fast2", "variables": ["x", "y"]},
                        {
                            "model": "QueueLib.Slow",
                            "variables": ["x[1]"],
                            # A spec entry reads no experiment annotation.
                            "simulation": {
                                "stop_time": 200,
                                "tolerance": 1e-8,
                                "timeout": 900,
                            },
                        },
                    ]
                }
            )
        )
    config = Config(
        config_file=tmp_path / "testing.json",
        backend_name=backend_name,
        work_dir=tmp_path / work,
    )
    try:
        backend = select_backend(config, persistent=True)
    except BackendUnavailableError as exc:
        pytest.skip(str(exc))
    backend.quiet = True
    tests = {t.model_id.rsplit(".", 1)[-1]: t for t in discover_tests(config)}
    return config, tests, backend


def _tool_processes() -> list[tuple[int, str]]:
    """Every live Dymola / dymosim / omc process this test started, wherever
    it hangs in the process table now."""
    mark = os.environ[_MARK]
    found = []
    for proc in psutil.process_iter():
        try:
            name = proc.name().lower()
            if not name.startswith(_TOOLS) or proc.status() == psutil.STATUS_ZOMBIE:
                continue
            if proc.environ().get(_MARK) == mark:
                found.append((proc.pid, name))
        except psutil.Error:
            continue  # gone, or another user's: not ours either way
    return found


def _wait_until(pred, timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(0.02)
    raise AssertionError(f"{what} not reached within {timeout:.0f} s")


def _assert_no_tool_lingers(seconds: float = 6.0) -> None:
    """Watch for *seconds*: no tool process of ours lives past half a second.

    Asked right after a close, not "eventually": a tool nobody tracks does
    go away by itself once the start that launched it returns — seconds
    later, and never when the process that owns that thread has exited by
    then. One launched after the give-up is killed at its birth, so it may
    be seen once."""
    first_seen: dict[int, float] = {}
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        now = time.monotonic()
        alive = dict(_tool_processes())
        first_seen = {pid: first_seen.get(pid, now) for pid in alive}
        lingering = {
            pid: alive[pid] for pid, since in first_seen.items() if now - since > 0.5
        }
        assert not lingering, f"left running after the close: {lingering}"
        time.sleep(0.05)


def _ended(*items) -> bool:
    return all(i.state not in (ItemState.WAITING, ItemState.RUNNING) for i in items)


def _named(name: str) -> list[int]:
    return [pid for pid, proc_name in _tool_processes() if proc_name.startswith(name)]


def _uncancelled(tmp_path: Path, backend_name: str, names: list[str]):
    """The same tests through `run_pipeline`, no queue, another work dir."""
    config, tests, backend = _project(tmp_path, backend_name, "reference")
    outcome = run_pipeline(
        config, [tests[n] for n in names], backend=backend, store=ReferenceStore(config)
    )
    return outcome.results


def _assert_same(got, want) -> None:
    assert got.success and want.success
    assert [v.name for v in got.variables] == [v.name for v in want.variables]
    for a, b in zip(got.variables, want.variables, strict=True):
        assert np.array_equal(a.time, b.time), a.name
        assert np.array_equal(a.values, b.values), a.name
        assert a.values.size > 100


def _manifest(config) -> dict[str, str]:
    data = json.loads((config.work_dir / "batch_manifest.json").read_text())
    return {
        entry["model_id"].rsplit(".", 1)[-1]: entry.get("last_run_status")
        for entry in data.values()
    }


@pytest.fixture
def no_tool_left(monkeypatch):
    monkeypatch.setenv(_MARK, uuid.uuid4().hex)
    assert _tool_processes() == []
    yield
    _wait_until(lambda: _tool_processes() == [], 15, "every tool process gone")


# ---------------------------------------------------------------------------
# Dymola
# ---------------------------------------------------------------------------


@pytest.mark.dymola
def test_dymola_three_queued_removed_cancelled_finished(tmp_path, no_tool_left):
    config, tests, backend = _project(tmp_path, "Dymola", "pool")
    pool = open_pool(config, backend=backend, store=ReferenceStore(config), workers=1)
    try:
        slow = pool.submit(tests["Slow"])
        fast, fast2 = pool.submit(tests["Fast"]), pool.submit(tests["Fast2"])
        assert pool.remove(fast2) is True  # removed before it started

        _wait_until(lambda: _named("dymosim") != [], 120, "dymosim running")
        (dymola_pid,) = _named("dymola")
        time.sleep(3.0)  # mid-simulation
        assert slow.state is ItemState.RUNNING

        t0 = time.monotonic()
        assert pool.cancel(slow) is True
        _wait_until(lambda: _ended(slow), 60, "the cancelled item ended")
        cancel_seconds = time.monotonic() - t0
        _wait_until(lambda: _ended(fast), 120, "the next item finished")
        print(
            f"\nDymola stop-file cancel: {cancel_seconds:.2f} s; "
            f"worker_killed={slow.run_result.worker_killed}"
        )

        assert slow.state is ItemState.CANCELLED and slow.result is None
        assert slow.run_result.worker_killed is False  # the stop file, not a kill
        assert _named("dymola") == [dymola_pid]  # the SAME Dymola ran the next
        assert _named("dymosim") == []
        assert fast.state is ItemState.FINISHED and fast.worker_id == 0
        assert fast2.state is ItemState.REMOVED
        assert cancel_seconds < 15.0
    finally:
        pool.close()

    assert _manifest(config) == {"Slow": "cancelled", "Fast": "passed"}
    reloaded = backend.read_last_results(list(tests.values()))
    assert list(reloaded) == ["QueueLib.Fast"]  # the cancelled one: no result
    _assert_same(
        fast.result, _uncancelled(tmp_path, "Dymola", ["Fast"])["QueueLib.Fast"]
    )


@pytest.mark.dymola
def test_dymola_cancel_during_translation(tmp_path, no_tool_left):
    """No dymosim yet, so no stop file: Dymola itself is killed, and
    restarted for the next item."""
    config, tests, backend = _project(tmp_path, "Dymola", "pool")
    pool = open_pool(config, backend=backend, store=ReferenceStore(config), workers=1)
    try:
        slow = pool.submit(tests["Slow"])
        fast = pool.submit(tests["Fast"])
        _wait_until(lambda: slow.test_key is not None, 120, "the item on its worker")
        (dymola_pid,) = _named("dymola")
        time.sleep(0.3)
        assert _named("dymosim") == [], "translation was over before the cancel"

        t0 = time.monotonic()
        assert pool.cancel(slow) is True
        _wait_until(lambda: _ended(slow), 60, "the cancelled item ended")
        cancel_seconds = time.monotonic() - t0
        _wait_until(lambda: len(_named("dymola")) == 1, 120, "Dymola restarted")
        restart_seconds = time.monotonic() - t0 - cancel_seconds
        _wait_until(lambda: _ended(fast), 120, "the next item finished")
        print(
            f"\nDymola cancel during translation: {cancel_seconds:.2f} s, a new "
            f"Dymola process {restart_seconds:.2f} s later, the next item "
            f"finished {time.monotonic() - t0:.2f} s after the cancel"
        )

        assert slow.state is ItemState.CANCELLED
        assert slow.run_result.worker_killed is True
        assert fast.state is ItemState.FINISHED
        assert dymola_pid not in _named("dymola")  # a NEW Dymola
        assert cancel_seconds < 15.0
    finally:
        pool.close()
    assert _manifest(config) == {"Slow": "cancelled", "Fast": "passed"}


def _start_given_up(tmp_path, backend_name: str, bound: float):
    """Two workers whose start gets *bound* seconds — less than the tool
    needs: both items fail with the bound's sentence. Returns them after
    the pool closed."""
    config, tests, backend = _project(tmp_path, backend_name, "pool")
    config.worker_startup_timeout = bound
    pool = open_pool(config, backend=backend, store=ReferenceStore(config), workers=2)
    try:
        fast, fast2 = pool.submit(tests["Fast"]), pool.submit(tests["Fast2"])
        _wait_until(lambda: _ended(fast, fast2), 60, "the items failed")
    finally:
        pool.close()
    return fast, fast2


@pytest.mark.dymola
@pytest.mark.parametrize("bound", [0.3, 2.0])
def test_dymola_start_given_up_at_its_bound_leaves_no_dymola(
    tmp_path, no_tool_left, bound
):
    """The interface's constructor launches Dymola and then waits seconds
    for it to answer: a start given up inside that wait used to leave the
    Dymola it had launched running, a licence seat with it."""
    fast, fast2 = _start_given_up(tmp_path, "Dymola", bound)

    assert fast.state is fast2.state is ItemState.FAILED
    assert f"worker 0: did not start within {bound:g} s" in fast.message
    assert '"worker_startup_timeout"' in fast.message
    _assert_no_tool_lingers()


@pytest.mark.dymola
@pytest.mark.parametrize("delay", [0.3, 2.0])
def test_dymola_pool_closed_during_the_start_leaves_no_dymola(
    tmp_path, no_tool_left, delay
):
    config, tests, backend = _project(tmp_path, "Dymola", "pool")
    pool = open_pool(config, backend=backend, store=ReferenceStore(config), workers=2)
    try:
        fast, fast2 = pool.submit(tests["Fast"]), pool.submit(tests["Fast2"])
        time.sleep(delay)
    finally:
        pool.close()

    assert fast.state is fast2.state is ItemState.REMOVED
    _assert_no_tool_lingers()


# ---------------------------------------------------------------------------
# OpenModelica
# ---------------------------------------------------------------------------


@pytest.mark.ompython
def test_openmodelica_a_run_completes(tmp_path, no_tool_left):
    config, tests, backend = _project(tmp_path, "OpenModelica", "pool")
    pool = open_pool(config, backend=backend, store=ReferenceStore(config), workers=2)
    try:
        fast, fast2 = pool.submit(tests["Fast"]), pool.submit(tests["Fast2"])
        _wait_until(lambda: _ended(fast, fast2), 300, "both items ended")
        assert fast.state is fast2.state is ItemState.FINISHED, (
            fast.message,
            fast2.message,
        )
        pool.path.write_status([fast, fast2])
    finally:
        pool.close()

    expected = _uncancelled(tmp_path, "OpenModelica", ["Fast", "Fast2"])
    _assert_same(fast.result, expected["QueueLib.Fast"])
    _assert_same(fast2.result, expected["QueueLib.Fast2"])
    status = json.loads((config.work_dir / "status.json").read_text())
    assert [row["status"] for row in status["tests"]] == ["passed", "passed"]
    assert status["metadata"]["tool_version"]  # asked of the live omc


@pytest.mark.ompython
def test_openmodelica_cancel_kills_omc_s_children_and_keeps_omc(tmp_path, no_tool_left):
    config, tests, backend = _project(tmp_path, "OpenModelica", "pool")
    pool = open_pool(config, backend=backend, store=ReferenceStore(config), workers=1)

    def model_binaries() -> list[int]:
        """The compiled model's process(es) under omc (omc names it after
        the result file prefix, `result`)."""
        found = []
        for child in psutil.Process().children(recursive=True):
            try:
                if child.name() == "result" and child.status() != "zombie":
                    found.append(child.pid)
            except psutil.Error:
                continue  # compilers come and go under omc
        return found

    try:
        slow, fast = pool.submit(tests["Slow"]), pool.submit(tests["Fast"])
        _wait_until(lambda: _named("omc") != [], 120, "omc up")
        _wait_until(lambda: model_binaries() != [], 300, "the compiled model running")
        (omc_pid,) = _named("omc")
        time.sleep(3.0)  # mid-simulation

        t0 = time.monotonic()
        assert pool.cancel(slow) is True
        _wait_until(lambda: _ended(slow), 60, "the cancelled item ended")
        cancel_seconds = time.monotonic() - t0
        _wait_until(lambda: _ended(fast), 300, "the next item finished")
        print(
            f"\nOpenModelica cancel (omc's children killed): {cancel_seconds:.2f} s; "
            f"worker_killed={slow.run_result.worker_killed}"
        )

        assert slow.state is ItemState.CANCELLED
        assert slow.run_result.worker_killed is False
        assert _named("omc") == [omc_pid]  # the SAME omc ran the next
        assert fast.state is ItemState.FINISHED, fast.message
        assert cancel_seconds < 15.0
    finally:
        pool.close()

    assert _manifest(config) == {"Slow": "cancelled", "Fast": "passed"}
    _assert_same(
        fast.result, _uncancelled(tmp_path, "OpenModelica", ["Fast"])["QueueLib.Fast"]
    )


@pytest.mark.ompython
def test_omc_killed_during_start_fails_the_items_with_its_words(
    tmp_path, no_tool_left, monkeypatch
):
    """The hang the queue's acceptance names: omc dead while the worker waits
    for its `loadModel` reply. OMPython's wait has no bound; the worker's
    start has."""
    from dstf.backends.openmodelica.persistent_backend import OpenModelicaWorker

    config, tests, backend = _project(tmp_path, "OpenModelica", "pool")
    real_exec = OpenModelicaWorker._exec

    def exec_after_the_kill(self, expr):
        if expr.startswith("loadModel"):
            for pid in self.pids:
                os.kill(pid, signal.SIGKILL)
        return real_exec(self, expr)

    monkeypatch.setattr(OpenModelicaWorker, "_exec", exec_after_the_kill)
    pool = open_pool(config, backend=backend, store=ReferenceStore(config), workers=1)
    try:
        t0 = time.monotonic()
        fast, fast2 = pool.submit(tests["Fast"]), pool.submit(tests["Fast2"])
        _wait_until(lambda: _ended(fast, fast2), 90, "the items failed")
        seconds = time.monotonic() - t0
        print(
            f"\nomc killed during loadModel: failed in {seconds:.2f} s\n{fast.message}"
        )

        assert fast.state is fast2.state is ItemState.FAILED
        assert fast.message == fast2.message == pool.stopped
        assert fast.message.startswith(
            "No OpenModelica worker could start — worker 0: omc exited (code -9) "
            "during loadModel(Modelica)"
        )
        assert seconds < 30.0  # at once, not at the bound
    finally:
        pool.close()


@pytest.mark.ompython
@pytest.mark.parametrize("bound", [0.05, 0.5])
def test_openmodelica_start_given_up_at_its_bound_leaves_no_omc(
    tmp_path, no_tool_left, bound
):
    """0.05 s lands inside OMPython's launch (omc up, its port file awaited),
    0.5 s inside `loadModel(Modelica)`."""
    fast, fast2 = _start_given_up(tmp_path, "OpenModelica", bound)

    assert fast.state is fast2.state is ItemState.FAILED
    assert f"worker 0: did not start within {bound:g} s" in fast.message
    _assert_no_tool_lingers()
