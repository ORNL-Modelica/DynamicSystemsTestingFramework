"""Persistent-worker watchdog / restart lifecycle.

Covers the parts of a worker kill that only show up around a *timeout*:
the process tree left behind by a parent-only kill, the exec thread the
watchdog abandons, and the state a restart must not inherit. Fakes follow
the patterns in test_openmodelica_persistent.py / test_review_fixes_backends.py.
"""

from __future__ import annotations

import io
import json
import logging
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from dstf.backends.persistent import PersistentBackendBase, Worker
from dstf.backends.types import TestRunResult, timeout_message
from dstf.discovery.test_registry import TestModel
from dstf.storage.batch_manifest import BatchManifest


@pytest.fixture(autouse=True)
def _fake_pids_name_no_process(monkeypatch):
    """The fakes here track made-up PIDs, which the watchdog's death watch
    would read as "the tool died". That watch has its own tests, over real
    processes (test_worker_lifecycle.py)."""
    monkeypatch.setattr(
        "dstf.backends.persistent.process_gone", lambda pids, name_hint=None: False
    )


def _tm(model_id: str = "Lib.A") -> TestModel:
    return TestModel(
        model_id=model_id,
        source_file=Path(""),
        source_package="Lib",
        short_name=model_id.rsplit(".", 1)[-1],
        n_vars=0,
        variable_patterns=["x"],
        stop_time=1.0,
        tolerance=1e-6,
        method="dassl",
    )


# ---------------------------------------------------------------------------
# Fake psutil — the tracked PID has a child doing the actual simulating
# ---------------------------------------------------------------------------


class _FakeNoSuchProcess(Exception):
    pass


class _FakeAccessDenied(Exception):
    pass


class _FakeProcess:
    def __init__(self, pid: int, name: str, children=(), create_time: float = 100.0):
        self.pid = pid
        self._name = name
        self._children = list(children)
        self._create_time = create_time
        self.killed = False

    def name(self) -> str:
        return self._name

    def children(self, recursive: bool = False):
        return list(self._children)

    def create_time(self) -> float:
        return self._create_time

    def kill(self) -> None:
        self.killed = True


def _install_fake_psutil(monkeypatch, table: dict[int, _FakeProcess]):
    def _process(pid):
        if pid not in table:
            raise _FakeNoSuchProcess(pid)
        return table[pid]

    fake = SimpleNamespace(
        Process=_process,
        NoSuchProcess=_FakeNoSuchProcess,
        AccessDenied=_FakeAccessDenied,
    )
    monkeypatch.setitem(__import__("sys").modules, "psutil", fake)
    return fake


class TestKillsWholeProcessTree:
    """A parent-only kill leaves the simulation child running — burning CPU
    and holding the result files open (Windows then fails the next run's
    rmtree with PermissionError)."""

    def test_dymola_worker_kills_dymosim_child(self, tmp_path, monkeypatch):
        from dstf.backends.dymola.persistent_backend import DymolaWorker

        child = _FakeProcess(1002, "dymosim")
        parent = _FakeProcess(1001, "dymola.exe", children=[child])
        _install_fake_psutil(monkeypatch, {1001: parent, 1002: child})

        worker = DymolaWorker(
            worker_id=0,
            config=SimpleNamespace(
                work_dir=tmp_path,
                backend_path=None,
                dependencies=[],
                library_dir=tmp_path,
            ),
            dymola_config=SimpleNamespace(
                show_ide=False,
                backend_setup=[],
                diagnostic_variables=[],
            ),
            dymola_interface_cls=object,
        )
        worker.pids = {1001}
        worker._kill_tracked_pids()

        assert child.killed is True
        assert parent.killed is True
        assert worker.pids == set()

    def test_om_worker_kills_model_binary_child(self, tmp_path, monkeypatch):
        from dstf.backends.openmodelica.persistent_backend import (
            OpenModelicaWorker,
        )

        child = _FakeProcess(2002, "Lib.A")  # the compiled model executable
        parent = _FakeProcess(2001, "omc", children=[child])
        _install_fake_psutil(monkeypatch, {2001: parent, 2002: child})

        worker = OpenModelicaWorker(
            0,
            SimpleNamespace(work_dir=tmp_path),
            SimpleNamespace(std_version="3.3", diagnostic_variables=[], mat_sync=False),
            lambda: None,
        )
        worker.pids = {2001}
        worker._kill_tracked_pids()

        assert child.killed is True
        assert parent.killed is True

    def test_recycled_pid_is_left_alone(self, tmp_path, monkeypatch):
        """The tracked PID may have been reused by an unrelated process —
        the name guard is what stops us killing it."""
        from dstf.backends.dymola.persistent_backend import DymolaWorker

        child = _FakeProcess(3002, "some-child")
        stranger = _FakeProcess(3001, "python", children=[child])
        _install_fake_psutil(monkeypatch, {3001: stranger, 3002: child})

        worker = DymolaWorker(
            worker_id=0,
            config=SimpleNamespace(
                work_dir=tmp_path,
                backend_path=None,
                dependencies=[],
                library_dir=tmp_path,
            ),
            dymola_config=SimpleNamespace(
                show_ide=False,
                backend_setup=[],
                diagnostic_variables=[],
            ),
            dymola_interface_cls=object,
        )
        worker.pids = {3001}
        worker._kill_tracked_pids()

        assert stranger.killed is False
        assert child.killed is False


class TestKillProcessTreeRealProcesses:
    """kill_process_tree against real OS processes.

    The fake-psutil tests above pin the kill *ordering and guards*; only a
    real process can validate signal delivery and psutil's recursive child
    enumeration — the semantics the watchdog actually relies on.
    """

    def test_parent_and_child_both_die(self):
        import subprocess
        import sys
        import time

        import psutil

        from dstf.backends.common.proc_kill import kill_process_tree

        # Parent spawns a child and idles — the pre-fix parent-only kill
        # left exactly this child running.
        parent = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import subprocess, sys, time\n"
                "p = subprocess.Popen("
                "[sys.executable, '-c', 'import time; time.sleep(120)'])\n"
                "print(p.pid, flush=True)\n"
                "time.sleep(120)\n",
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        child_pid = None
        try:
            child_pid = int(parent.stdout.readline())
            assert psutil.pid_exists(child_pid)

            kill_process_tree(parent.pid)

            parent.wait(timeout=10)
            # Kill delivery to the (now reparented) child can lag; a
            # not-yet-reaped corpse shows as a zombie, which counts as dead.
            deadline = time.time() + 10
            while time.time() < deadline:
                try:
                    if psutil.Process(child_pid).status() == psutil.STATUS_ZOMBIE:
                        break
                except psutil.NoSuchProcess:
                    break
                time.sleep(0.1)
            else:
                pytest.fail(f"child {child_pid} survived kill_process_tree")
        finally:
            for pid in (parent.pid, child_pid):
                if pid is None:
                    continue
                try:
                    psutil.Process(pid).kill()
                except Exception:
                    pass
            parent.stdout.close()


# ---------------------------------------------------------------------------
# The abandoned exec thread must not reach the restarted interface
# ---------------------------------------------------------------------------


class _BlockingDymolaInterface:
    def __init__(self, gate: threading.Event | None = None):
        self.calls: list[str] = []
        self.gate = gate

    def cd(self, path):
        self.calls.append("cd")

    def clearlog(self):
        self.calls.append("clearlog")

    def translateModel(self, model_id):
        self.calls.append("translateModel")
        return True

    def simulateModel(self, model_id, **kwargs):
        self.calls.append("simulateModel")
        if self.gate is not None:
            self.gate.wait(10)
        return True

    def savelog(self, path):
        self.calls.append("savelog")
        return True


class _BlockingSession:
    def __init__(self, gate: threading.Event | None = None):
        self.calls: list[str] = []
        self.gate = gate

    def sendExpression(self, expr: str, parsed: bool = True):
        self.calls.append(expr)
        if expr.startswith("simulate(") and self.gate is not None:
            self.gate.wait(10)
        return "true\n"


class TestExecThreadPinsItsInterface:
    """On timeout the watchdog kills the worker and abandons the exec thread
    (``t.join(0.5)``); the pool then restarts the worker with a *new*
    interface on the same attribute. If the abandoned thread re-reads that
    attribute when it finally unblocks, it drives the fresh interface
    concurrently with the next test — the "Mismatch request/response ID"
    poisoning."""

    def test_dymola_run_test_uses_the_interface_it_started_with(self, tmp_path):
        from dstf.backends.dymola.persistent_backend import DymolaWorker

        gate = threading.Event()
        first = _BlockingDymolaInterface(gate=gate)
        second = _BlockingDymolaInterface()

        worker = DymolaWorker(
            worker_id=0,
            config=SimpleNamespace(
                work_dir=tmp_path,
                backend_path=None,
                dependencies=[],
                library_dir=tmp_path,
            ),
            dymola_config=SimpleNamespace(
                show_ide=False,
                backend_setup=[],
                diagnostic_variables=[],
            ),
            dymola_interface_cls=object,
        )
        worker.dymola = first

        t = threading.Thread(
            target=worker.run_test, args=(_tm(), "test_0001"), daemon=True
        )
        t.start()
        _wait_until(lambda: "simulateModel" in first.calls)

        # The restart the dispatch loop would do while this thread is stuck.
        worker.dymola = second
        gate.set()
        t.join(10)

        assert not t.is_alive()
        assert second.calls == []
        assert "savelog" in first.calls

    def test_om_run_test_uses_the_session_it_started_with(self, tmp_path):
        from dstf.backends.openmodelica.persistent_backend import (
            OpenModelicaWorker,
        )

        gate = threading.Event()
        first = _BlockingSession(gate=gate)
        second = _BlockingSession()

        worker = OpenModelicaWorker(
            0,
            SimpleNamespace(work_dir=tmp_path),
            SimpleNamespace(std_version="3.3", diagnostic_variables=[], mat_sync=False),
            lambda: None,
        )
        worker.session = first

        t = threading.Thread(
            target=worker.run_test, args=(_tm(), "test_0001"), daemon=True
        )
        t.start()
        _wait_until(lambda: any(c.startswith("simulate(") for c in first.calls))

        worker.session = second
        gate.set()
        t.join(10)

        assert not t.is_alive()
        assert second.calls == []
        # getErrorString() runs after the simulate call — on the old session.
        assert any(c.startswith("getErrorString") for c in first.calls)


def _wait_until(pred, timeout: float = 10.0) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(0.01)
    pytest.fail("condition not reached within timeout")


# ---------------------------------------------------------------------------
# A restart must not inherit the killed process's output
# ---------------------------------------------------------------------------


class TestJuliaRestartQueue:
    def test_start_discards_the_killed_process_stdout(self, tmp_path, monkeypatch):
        """A stale 'ready' pulse left by the killed process would satisfy
        _wait_for_ready before the new process has loaded anything."""
        from dstf.backends.julia import persistent_backend as jpr

        worker = jpr.JuliaWorker(
            0,
            SimpleNamespace(work_dir=tmp_path),
            SimpleNamespace(julia_binary=Path("julia"), project_dir=tmp_path),
        )
        worker._stdout_q.put('{"event": "ready"}')

        fake_proc = SimpleNamespace(
            pid=4242,
            stdin=io.StringIO(),
            stdout=io.StringIO(""),
            stderr=io.StringIO(""),
            poll=lambda: None,
        )
        monkeypatch.setattr(jpr.subprocess, "Popen", lambda *a, **k: fake_proc)
        monkeypatch.setattr(
            jpr.JuliaWorker, "_wait_for_ready", lambda self, timeout: True
        )

        worker.start()

        assert worker._stdout_q.empty()


class TestJuliaTimeoutKill:
    def test_timeout_kills_the_tracked_tree_and_flags_worker_killed(
        self, tmp_path, monkeypatch
    ):
        """The deadline poll uses the shared kill, and the result says the
        worker died — the dispatch loop's benign-restart accounting keys on
        ``timed_out or worker_killed``."""
        from dstf.backends import persistent as sim_persistent
        from dstf.backends.julia import persistent_backend as jpr

        (tmp_path / "Lib.A.jl").write_text("# stub")
        worker = jpr.JuliaWorker(
            0,
            SimpleNamespace(work_dir=tmp_path, source_path=tmp_path),
            SimpleNamespace(julia_binary=Path("julia"), project_dir=tmp_path),
        )
        worker.proc = SimpleNamespace(
            pid=4242,
            stdin=io.StringIO(),
            stdout=io.StringIO(""),
            stderr=io.StringIO(""),
            poll=lambda: None,
            kill=lambda: None,
            wait=lambda timeout=None: 0,
        )
        worker.pids = {4242}

        killed: list[tuple[int, str | None]] = []
        monkeypatch.setattr(
            sim_persistent,
            "kill_process_tree",
            lambda pid, name_hint=None: killed.append((pid, name_hint)),
        )

        # No response ever arrives on the stdout queue → deadline expires.
        tr = worker.run_test_with_timeout(_tm(), "test_0001", timeout=0.1)

        assert tr.timed_out is True
        assert tr.worker_killed is True
        assert killed == [(4242, "julia")]
        assert worker.pids == set()

    def test_a_cancel_is_the_same_kill_and_a_cancelled_result(
        self, tmp_path, monkeypatch
    ):
        """Julia's own poll loop hears `request_cancel` — the driver has no
        stop short of its process."""
        from dstf.backends import persistent as sim_persistent
        from dstf.backends.julia import persistent_backend as jpr

        (tmp_path / "Lib.A.jl").write_text("# stub")
        worker = jpr.JuliaWorker(
            0,
            SimpleNamespace(work_dir=tmp_path, source_path=tmp_path),
            SimpleNamespace(julia_binary=Path("julia"), project_dir=tmp_path),
        )
        worker.proc = SimpleNamespace(
            pid=4242,
            stdin=io.StringIO(),
            stdout=io.StringIO(""),
            stderr=io.StringIO(""),
            poll=lambda: None,
            kill=lambda: None,
            wait=lambda timeout=None: 0,
        )
        worker.pids = {4242}
        killed: list[int] = []
        monkeypatch.setattr(
            sim_persistent,
            "kill_process_tree",
            lambda pid, name_hint=None: killed.append(pid),
        )
        worker.arm("test_0001")
        threading.Timer(0.2, lambda: worker.request_cancel("test_0001")).start()

        t0 = time.monotonic()
        tr = worker.run_test_with_timeout(_tm(), "test_0001", timeout=60)

        assert tr.cancelled and tr.worker_killed and not tr.timed_out
        assert tr.error_message == "Cancelled"
        assert killed == [4242] and not worker.is_alive()
        assert time.monotonic() - t0 < 5.0


# ---------------------------------------------------------------------------
# Orphans: the child outlives a graceful parent exit
# ---------------------------------------------------------------------------


class TestOrphanedChildIsStillKilled:
    def test_child_snapshotted_before_graceful_close_is_killed_after(
        self, tmp_path, monkeypatch
    ):
        """A graceful shutdown that succeeds takes the parent away and leaves
        dymosim/the model binary orphaned — unreachable from a tree walk that
        starts at the (now gone) parent. close() snapshots the children first
        so they are still killable afterwards."""
        from dstf.backends.persistent import Worker

        child = _FakeProcess(5002, "dymosim")
        parent = _FakeProcess(5001, "dymola.exe", children=[child])
        table = {5001: parent, 5002: child}
        _install_fake_psutil(monkeypatch, table)

        class _GracefulWorker(Worker):
            kill_name_hint = "dymola"

            def start(self):  # pragma: no cover — never called
                raise AssertionError

            def is_alive(self):
                return self.handle is not None

            def _detach_interface(self):
                h = self.handle
                self.handle = None
                return h

            def _shutdown_interface(self, handle, grace):
                # The graceful close succeeds: the parent exits, the child
                # keeps running.
                table.pop(5001, None)

        worker = _GracefulWorker(0, SimpleNamespace(work_dir=tmp_path))
        worker.handle = object()
        worker.pids = {5001}

        worker.close(grace=2.0)

        assert parent.killed is False  # it exited on its own
        assert child.killed is True
        assert worker.pids == set()

    def test_recycled_orphan_pid_is_left_alone(self, tmp_path, monkeypatch):
        """The graceful window is seconds long and a PID freed inside it can
        be reissued — on Windows almost immediately, possibly to another
        worker's fresh dymosim. The snapshot records each child's creation
        time; a different one means a different process."""
        from dstf.backends.persistent import Worker

        child = _FakeProcess(6002, "dymosim", create_time=100.0)
        parent = _FakeProcess(6001, "dymola.exe", children=[child])
        table = {6001: parent, 6002: child}
        _install_fake_psutil(monkeypatch, table)

        class _RecyclingWorker(Worker):
            kill_name_hint = "dymola"

            def start(self):  # pragma: no cover — never called
                raise AssertionError

            def is_alive(self):
                return self.handle is not None

            def _detach_interface(self):
                h = self.handle
                self.handle = None
                return h

            def _shutdown_interface(self, handle, grace):
                # Parent exits; the child's PID is reissued to a stranger.
                table.pop(6001, None)
                table[6002] = stranger

        stranger = _FakeProcess(6002, "someone-elses-job", create_time=999.0)

        worker = _RecyclingWorker(0, SimpleNamespace(work_dir=tmp_path))
        worker.handle = object()
        worker.pids = {6001}

        worker.close(grace=2.0)

        assert stranger.killed is False
        assert child.killed is False  # it is gone; the PID is not it

    def test_snapshot_honours_the_name_guard(self, monkeypatch):
        """A recycled *parent* PID must not hand us a stranger's children to
        kill later — the guard belongs on the snapshot too, not only on the
        kill."""
        from dstf.backends.common.proc_kill import snapshot_children

        child = _FakeProcess(7002, "some-child")
        stranger = _FakeProcess(7001, "python", children=[child])
        _install_fake_psutil(monkeypatch, {7001: stranger, 7002: child})

        assert snapshot_children(7001, name_hint="dymola") == set()
        assert snapshot_children(7001) == {(7002, 100.0)}

    def test_child_spawned_during_the_graceful_window_is_killed(
        self, tmp_path, monkeypatch
    ):
        """A timeout during *translation* snapshots a tree with no dymosim in
        it; Dymola starts dymosim inside the graceful window, from the
        simulateModel call the abandoned exec thread already issued, and the
        parent then exits and orphans it. Only the walks taken *during* the
        window — while the parent is still alive to be walked — can reach it;
        a walk after the graceful attempt finds no parent left (measured
        live: two surviving `sh -c ./dymosim` processes)."""
        from dstf.backends.persistent import Worker

        parent = _FakeProcess(8001, "dymola.exe")
        late_child = _FakeProcess(8002, "dymosim", create_time=200.0)
        table = {8001: parent}
        _install_fake_psutil(monkeypatch, table)

        class _LateSpawnWorker(Worker):
            kill_name_hint = "dymola"

            def start(self):  # pragma: no cover — never called
                raise AssertionError

            def is_alive(self):
                return self.handle is not None

            def _detach_interface(self):
                h = self.handle
                self.handle = None
                return h

            def _shutdown_interface(self, handle, grace):
                # dymosim appears only now — after the first snapshot — and
                # the parent then exits, orphaning it. Only a walk taken
                # while the parent was still alive can reach it.
                time.sleep(0.6)
                parent._children.append(late_child)
                table[8002] = late_child
                time.sleep(0.6)
                table.pop(8001, None)

        worker = _LateSpawnWorker(0, SimpleNamespace(work_dir=tmp_path))
        worker.handle = object()
        worker.pids = {8001}

        worker.close(grace=4.0)

        assert late_child.killed is True
        assert parent.killed is False  # it exited on its own


# ---------------------------------------------------------------------------
# The shared watchdog template (Worker.run_test_with_timeout)
# ---------------------------------------------------------------------------


class _TemplateWorker:
    """Mixin body for the fake workers below — a run_test that blocks or
    raises, plus a scriptable disk rescue."""

    kill_name_hint = None

    def __init__(self, worker_id, config, block: bool = False, raise_exc=None):
        super().__init__(worker_id, config)
        self.handle: object | None = object()
        self.block = block
        self.raise_exc = raise_exc
        self.rescue: object | None = None
        self.gate = threading.Event()
        self.closed = 0

    def start(self):  # pragma: no cover — never called
        raise AssertionError

    def is_alive(self):
        return self.handle is not None

    def _detach_interface(self):
        h = self.handle
        self.handle = None
        self.closed += 1
        return h

    def _shutdown_interface(self, handle, grace):
        pass

    def run_test(self, test, test_key, progress=None):
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.block:
            self.gate.wait(10)
        from dstf.backends.types import TestRunResult

        return TestRunResult(
            model_id=test.model_id,
            test_key=test_key,
            success=True,
            elapsed=0.0,
        )

    def _evaluate_from_disk(self, test, test_key, elapsed):
        return self.rescue


def _template_worker(tmp_path, **kwargs):
    from dstf.backends.persistent import Worker

    cls = type("_FakeTemplateWorker", (_TemplateWorker, Worker), {})
    return cls(0, SimpleNamespace(work_dir=tmp_path), **kwargs)


class TestSharedWatchdogTemplate:
    def test_timeout_kills_and_reports_timed_out(self, tmp_path, monkeypatch):
        from dstf.backends import persistent as sim_persistent

        killed: list[int] = []
        monkeypatch.setattr(
            sim_persistent,
            "kill_process_tree",
            lambda pid, name_hint=None: killed.append(pid),
        )
        worker = _template_worker(tmp_path, block=True)
        worker.pids = {7001}

        try:
            tr = worker.run_test_with_timeout(_tm(), "test_0001", timeout=0.1)
        finally:
            worker.gate.set()

        assert tr.timed_out is True
        assert tr.worker_killed is True
        assert tr.success is False
        assert worker.closed == 1
        assert killed == [7001]
        assert worker.is_alive() is False

    def test_timeout_rescued_from_disk_still_flags_worker_killed(self, tmp_path):
        from dstf.backends.types import TestRunResult

        worker = _template_worker(tmp_path, block=True)
        worker.rescue = TestRunResult(
            model_id="Lib.A",
            test_key="test_0001",
            success=True,
            elapsed=0.0,
        )

        try:
            tr = worker.run_test_with_timeout(_tm(), "test_0001", timeout=0.1)
        finally:
            worker.gate.set()

        assert tr.success is True
        assert tr.timed_out is False
        assert tr.worker_killed is True

    def test_exception_path_leaves_worker_killed_false(self, tmp_path):
        """Worker exceptions signal a genuinely broken worker, so they DO
        count against the restart budget — the flag that exempts a restart
        must stay off."""
        worker = _template_worker(tmp_path, raise_exc=RuntimeError("rpc dropped"))

        tr = worker.run_test_with_timeout(_tm(), "test_0001", timeout=5.0)

        assert tr.success is False
        assert tr.timed_out is False
        assert tr.worker_killed is False
        assert "rpc dropped" in (tr.error_message or "")
        assert worker.closed == 1

    def test_exception_path_rescues_a_completed_simulation(self, tmp_path):
        from dstf.backends.types import TestRunResult

        worker = _template_worker(tmp_path, raise_exc=RuntimeError("rpc dropped"))
        worker.rescue = TestRunResult(
            model_id="Lib.A",
            test_key="test_0001",
            success=True,
            elapsed=0.0,
        )

        tr = worker.run_test_with_timeout(_tm(), "test_0001", timeout=5.0)

        assert tr.success is True
        assert tr.worker_killed is False
        assert worker.closed == 1  # broken worker is still torn down

    def test_clean_run_returns_the_inner_result(self, tmp_path):
        worker = _template_worker(tmp_path)

        tr = worker.run_test_with_timeout(_tm(), "test_0001", timeout=5.0)

        assert tr.success is True
        assert worker.closed == 0

    def test_timeout_waits_for_the_kill_before_returning(self, tmp_path, monkeypatch):
        """kill_process_tree returns before the OS tears the process down.
        A worker still reading alive gets the next test dispatched to it,
        which then fails "crashed mid-test" — a result with no
        ``worker_killed`` flag, so the restart it forces is charged against
        the budget. The watchdog reaps the process (and drops the handle)
        before returning."""
        from dstf.backends import persistent as sim_persistent
        from dstf.backends.julia import persistent_backend as jpr

        (tmp_path / "Lib.A.jl").write_text("# stub")
        worker = jpr.JuliaWorker(
            0,
            SimpleNamespace(work_dir=tmp_path, source_path=tmp_path),
            SimpleNamespace(julia_binary=Path("julia"), project_dir=tmp_path),
        )

        class _LingeringProc:
            """Reads alive until someone waits on it — the real race."""

            pid = 4242

            def __init__(self):
                self.stdin = io.StringIO()
                self.stdout = io.StringIO("")
                self.stderr = io.StringIO("")
                self.dead = False
                self.waits = 0

            def poll(self):
                return 0 if self.dead else None

            def kill(self):
                pass  # the OS is slow; poll() keeps reading alive

            def wait(self, timeout=None):
                self.waits += 1
                self.dead = True
                return 0

        proc = _LingeringProc()
        worker.proc = proc
        worker.pids = {4242}
        monkeypatch.setattr(
            sim_persistent, "kill_process_tree", lambda pid, name_hint=None: None
        )

        tr = worker.run_test_with_timeout(_tm(), "test_0001", timeout=0.1)

        assert tr.timed_out is True
        assert proc.waits == 1
        assert worker.is_alive() is False

    def test_timeout_drops_the_handle_even_if_the_process_lingers(
        self, tmp_path, monkeypatch
    ):
        """A process that outlives the reap window must still leave the
        worker dead — otherwise the dispatcher hands it the next test."""
        from dstf.backends import persistent as sim_persistent
        from dstf.backends.julia import persistent_backend as jpr

        (tmp_path / "Lib.A.jl").write_text("# stub")
        worker = jpr.JuliaWorker(
            0,
            SimpleNamespace(work_dir=tmp_path, source_path=tmp_path),
            SimpleNamespace(julia_binary=Path("julia"), project_dir=tmp_path),
        )

        def _never_exits(timeout=None):
            raise jpr.subprocess.TimeoutExpired(cmd="julia", timeout=timeout)

        worker.proc = SimpleNamespace(
            pid=4242,
            stdin=io.StringIO(),
            stdout=io.StringIO(""),
            stderr=io.StringIO(""),
            poll=lambda: None,
            kill=lambda: None,
            wait=_never_exits,
        )
        worker.pids = {4242}
        monkeypatch.setattr(jpr, "_KILL_EXIT_TIMEOUT", 0.05)
        monkeypatch.setattr(
            sim_persistent, "kill_process_tree", lambda pid, name_hint=None: None
        )

        tr = worker.run_test_with_timeout(_tm(), "test_0001", timeout=0.1)

        assert tr.timed_out is True
        assert worker.is_alive() is False


# ---------------------------------------------------------------------------
# close(): a detached handle owes a graceful shutdown
# ---------------------------------------------------------------------------


class TestCloseHookContract:
    def test_detach_without_shutdown_is_a_typeerror(self, tmp_path):
        """The graceful thread only logs what it catches, so a worker that
        detaches a handle it can't shut down would hard-kill its way through
        the whole run in silence. Fail loudly instead."""
        from dstf.backends.persistent import Worker

        class _HalfWorker(Worker):
            def start(self):  # pragma: no cover — never called
                raise AssertionError

            def is_alive(self):
                return True

            def _detach_interface(self):
                return object()

        worker = _HalfWorker(0, SimpleNamespace(work_dir=tmp_path))
        with pytest.raises(TypeError, match="_shutdown_interface"):
            worker.close()

    def test_graceful_failure_is_logged_not_swallowed(self, tmp_path, caplog):
        from dstf.backends.persistent import Worker

        class _RaisingShutdownWorker(Worker):
            def start(self):  # pragma: no cover — never called
                raise AssertionError

            def is_alive(self):
                return False

            def _detach_interface(self):
                return object()

            def _shutdown_interface(self, handle, grace):
                raise RuntimeError("rpc already gone")

        worker = _RaisingShutdownWorker(0, SimpleNamespace(work_dir=tmp_path))
        with caplog.at_level(logging.WARNING):
            worker.close(grace=2.0)  # must not raise — the PID kill follows

        assert "graceful shutdown raised" in caplog.text
        assert "RuntimeError: rpc already gone" in caplog.text
        # The stack belongs at DEBUG: a raise here is routine on the timeout
        # path, and a traceback per timed-out test is noise in the run output.
        assert "Traceback" not in caplog.text


class TestJuliaCloseAndReap:
    def _worker(self, tmp_path):
        from dstf.backends.julia import persistent_backend as jpr

        return jpr.JuliaWorker(
            0,
            SimpleNamespace(work_dir=tmp_path, source_path=tmp_path),
            SimpleNamespace(julia_binary=Path("julia"), project_dir=tmp_path),
        )

    def test_close_quits_waits_and_closes_every_pipe(self, tmp_path):
        """The template's close(): quit JSON written, process waited, all
        three pipes closed, handle dropped."""
        worker = self._worker(tmp_path)

        class _Pipe(io.StringIO):
            def __init__(self):
                super().__init__()
                self.written: list[str] = []

            def write(self, s):
                self.written.append(s)
                return len(s)

        class _Proc:
            pid = 4242

            def __init__(self):
                self.stdin = _Pipe()
                self.stdout = _Pipe()
                self.stderr = _Pipe()
                self.waited = []

            def poll(self):
                return None

            def wait(self, timeout=None):
                self.waited.append(timeout)
                return 0

        proc = _Proc()
        worker.proc = proc

        worker.close(grace=2.0)

        assert "".join(proc.stdin.written) == '{"cmd": "quit"}\n'
        assert proc.waited == [2.0]
        assert proc.stdin.closed and proc.stdout.closed and proc.stderr.closed
        assert worker.proc is None
        assert worker.is_alive() is False

    def test_unreapable_process_does_not_block_on_the_reader_pipes(
        self, tmp_path, monkeypatch
    ):
        """When the kill doesn't land, stdout/stderr must be left to the
        reader threads: they are parked in readline() holding the
        BufferedReader lock, so closing those pipes here blocks the dispatch
        thread until the child exits — at --parallel 1, the whole run."""
        from dstf.backends import persistent as sim_persistent
        from dstf.backends.julia import persistent_backend as jpr

        (tmp_path / "Lib.A.jl").write_text("# stub")
        worker = self._worker(tmp_path)

        class _RecordingStream(io.StringIO):
            """_close_pipes swallows every exception, so a raising close()
            would prove nothing — record the attempt instead."""

            def __init__(self, initial=""):
                super().__init__(initial)
                self.close_attempted = False

            def close(self):
                self.close_attempted = True
                super().close()

        stdin, stdout, stderr = (
            _RecordingStream(),
            _RecordingStream(),
            _RecordingStream(),
        )
        proc = SimpleNamespace(
            pid=4242,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            poll=lambda: None,
            kill=lambda: None,
            wait=lambda timeout=None: (_ for _ in ()).throw(
                jpr.subprocess.TimeoutExpired(cmd="julia", timeout=timeout)
            ),
        )
        worker.proc = proc
        worker.pids = {4242}
        monkeypatch.setattr(jpr, "_KILL_EXIT_TIMEOUT", 0.05)
        monkeypatch.setattr(
            sim_persistent, "kill_process_tree", lambda pid, name_hint=None: None
        )

        tr = worker.run_test_with_timeout(_tm(), "test_0001", timeout=0.1)

        assert tr.timed_out is True
        assert stdin.close_attempted is True  # safe: nobody reads stdin
        # The reader threads hold these; closing them here blocks until the
        # child exits, which is the process we already failed to kill.
        assert stdout.close_attempted is False
        assert stderr.close_attempted is False
        assert worker.is_alive() is False

    def test_tree_kill_no_op_still_kills_the_owned_popen(self, tmp_path, monkeypatch):
        """kill_process_tree does nothing without psutil, on a name-guard
        miss or on AccessDenied. We own the Popen — use it."""
        from dstf.backends import persistent as sim_persistent

        (tmp_path / "Lib.A.jl").write_text("# stub")
        worker = self._worker(tmp_path)

        state = {"killed": False}

        def _kill():
            state["killed"] = True

        worker.proc = SimpleNamespace(
            pid=4242,
            stdin=io.StringIO(),
            stdout=io.StringIO(""),
            stderr=io.StringIO(""),
            poll=lambda: None if not state["killed"] else 0,
            kill=_kill,
            wait=lambda timeout=None: 0,
        )
        worker.pids = {4242}
        monkeypatch.setattr(
            sim_persistent, "kill_process_tree", lambda pid, name_hint=None: None
        )

        worker.run_test_with_timeout(_tm(), "test_0001", timeout=0.1)

        assert state["killed"] is True

    def test_wedged_driver_is_killed_through_the_owned_popen(self, tmp_path):
        """The template's PID kill is a no-op without psutil or on a name
        mismatch, so the close path must not rely on it alone."""
        import subprocess

        class _Wedged:
            pid = 4242

            def __init__(self):
                self.stdin = io.StringIO()
                self.stdout = io.StringIO("")
                self.stderr = io.StringIO("")
                self.killed = False

            def poll(self):
                return None

            def wait(self, timeout=None):
                raise subprocess.TimeoutExpired(cmd="julia", timeout=timeout)

            def kill(self):
                self.killed = True

        worker = self._worker(tmp_path)
        proc = _Wedged()
        worker.proc = proc

        worker.close(grace=0.2)

        assert proc.killed is True
        assert worker.proc is None


# ---------------------------------------------------------------------------
# close() stays inside its grace budget
# ---------------------------------------------------------------------------


class TestCloseHonoursTheGraceBudget:
    def test_slow_tree_walks_do_not_extend_the_teardown(self, tmp_path, monkeypatch):
        """The orphan walks are paid out of *grace*, not added to it: a
        recursive children() call costs tens of ms on Windows and close()
        runs ~20 of them inside a 5s Dymola grace."""
        from dstf.backends import persistent as sim_persistent
        from dstf.backends.persistent import Worker

        def _slow_walk(pid, name_hint=None):
            time.sleep(0.05)  # a slow psutil walk
            return set()

        monkeypatch.setattr(sim_persistent, "snapshot_children", _slow_walk)

        class _NeverFinishesWorker(Worker):
            _ORPHAN_POLL_SECONDS = 0.4

            def start(self):  # pragma: no cover — never called
                raise AssertionError

            def is_alive(self):
                return self.handle is not None

            def _detach_interface(self):
                h = self.handle
                self.handle = None
                return h

            def _shutdown_interface(self, handle, grace):
                time.sleep(30)  # wedged: the graceful attempt never returns

        worker = _NeverFinishesWorker(0, SimpleNamespace(work_dir=tmp_path))
        worker.handle = object()
        worker.pids = {9001}

        grace = 0.5
        t0 = time.monotonic()
        worker.close(grace=grace)
        elapsed = time.monotonic() - t0

        # Waiting a full tick past the deadline (and only then checking it)
        # costs an extra POLL — 0.4s here, and a whole second of a 5s Dymola
        # teardown at the real 0.25s tick.
        assert elapsed < grace + 0.3


# =========================================================================
# Folded in from tests/test_review_fixes_backends.py — 2026-07-06 review,
# backend fixes (findings 20-29 and 73-75, CODE_REVIEW_2026-07-06.md).
#
# Findings 20 + 21 + 24 — persistent dispatch loop lifecycle: a dead worker
# fail-marks only its held item and exits (the last one drains the queue
# instead of hanging), a raise from ``run_test_with_timeout`` is recorded and
# the loop continues, and a worker whose ``start()`` raised is still
# ``close()``d (it may hold a license seat).
#
# ``_tm`` is the one already defined at the top of this module.
# =========================================================================


# ---------------------------------------------------------------------------
# Findings 20 + 21 + 24 — persistent dispatch loop lifecycle
# ---------------------------------------------------------------------------


class _StubBackend(PersistentBackendBase):
    backend_label = "Stub"
    # The production default is seconds (a licence seat takes a moment to be
    # released); tests only care that the retry happens, not that it waits.
    restart_retry_backoff_seconds = 0.01

    def read_result(self, test, test_key, run_result):  # pragma: no cover
        raise NotImplementedError


class _GoodWorker(Worker):
    def __init__(self, worker_id, config, delay: float = 0.0):
        super().__init__(worker_id, config)
        self.delay = delay
        self.ran: list[str] = []

    def start(self):
        pass

    def close(self, grace: float = 5.0):
        pass

    def is_alive(self):
        return True

    def run_test_with_timeout(self, test, test_key, timeout, progress=None):
        if self.delay:
            time.sleep(self.delay)
        self.ran.append(test_key)
        return TestRunResult(
            model_id=test.model_id,
            test_key=test_key,
            success=True,
            elapsed=self.delay,
        )


class _DeadWorker(Worker):
    """Never alive, never restartable — the finding-20 scenario."""

    def start(self):
        raise RuntimeError("permanently dead")

    def close(self, grace: float = 5.0):
        pass

    def is_alive(self):
        return False

    def run_test_with_timeout(self, test, test_key, timeout, progress=None):
        raise AssertionError("a dead worker must never run tests")


class _RaiseOnceWorker(_GoodWorker):
    def __init__(self, worker_id, config):
        super().__init__(worker_id, config)
        self.calls = 0

    def run_test_with_timeout(self, test, test_key, timeout, progress=None):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("kaboom")
        return super().run_test_with_timeout(test, test_key, timeout, progress)


def _scratch_manifest(tmp_path, items=()) -> BatchManifest:
    """A manifest covering the given ``(test, test_key)`` pairs so dispatch's
    incremental verdict recording has real entries to write into (and so the
    tests exercise that plumbing rather than a stub)."""
    return BatchManifest(
        work_dir=tmp_path,
        manifest={tk: {"model_id": t.model_id, "ref_id": None} for t, tk in items},
    )


def _stub_backend(tmp_path) -> _StubBackend:
    cfg = SimpleNamespace(timeout=5.0, parallel=2, work_dir=tmp_path)
    return _StubBackend(cfg)


class TestDispatchDeadWorker:
    def test_dead_worker_exits_thread_instead_of_draining_queue(self, tmp_path):
        """Finding 20: the dead worker fail-marks ONLY its held item and
        exits; the healthy worker runs everything else."""
        backend = _stub_backend(tmp_path)
        dead = _DeadWorker(0, backend.config)
        good = _GoodWorker(1, backend.config, delay=0.05)
        items = [(_tm(f"Lib.T{i}"), f"test_{i:04d}") for i in range(6)]

        results = backend._dispatch_with_restart(
            [dead, good], items, total=6, manifest=_scratch_manifest(tmp_path, items)
        )

        assert len(results) == 6
        failures = [r for r in results if not r.success]
        assert len(failures) == 1
        assert "restart exhausted" in (failures[0].error_message or "")
        assert len(good.ran) == 5

    def test_last_dead_worker_drains_queue_without_hanging(self, tmp_path):
        """Finding 20: with no live worker left, the queue is drained and
        every remaining test still gets a recorded result."""
        backend = _stub_backend(tmp_path)
        dead = _DeadWorker(0, backend.config)
        items = [(_tm(f"Lib.T{i}"), f"test_{i:04d}") for i in range(4)]

        box: dict = {}

        def run():
            box["results"] = backend._dispatch_with_restart(
                [dead], items, total=4, manifest=_scratch_manifest(tmp_path, items)
            )

        th = threading.Thread(target=run, daemon=True)
        th.start()
        th.join(20)
        assert not th.is_alive(), "dispatch hung after last worker died"
        results = box["results"]
        assert len(results) == 4
        assert all(not r.success for r in results)
        assert all("restart exhausted" in (r.error_message or "") for r in results)


class TestDispatchWorkerRaise:
    def test_worker_raise_is_recorded_and_loop_continues(self, tmp_path):
        """Finding 21: a raise from run_test_with_timeout must not kill the
        dispatch thread — the test gets a failed result, the rest still run."""
        backend = _stub_backend(tmp_path)
        w = _RaiseOnceWorker(0, backend.config)
        items = [(_tm(f"Lib.T{i}"), f"test_{i:04d}") for i in range(3)]

        results = backend._dispatch_with_restart(
            [w], items, total=3, manifest=_scratch_manifest(tmp_path, items)
        )

        assert len(results) == 3
        failures = [r for r in results if not r.success]
        assert len(failures) == 1
        assert "worker exception" in (failures[0].error_message or "")
        assert "kaboom" in (failures[0].error_message or "")
        assert sum(1 for r in results if r.success) == 2


class _FailStartWorker(Worker):
    def __init__(self, worker_id, config):
        super().__init__(worker_id, config)
        self.n_closed = 0

    def start(self):
        raise RuntimeError("start blew up")

    def close(self, grace: float = 5.0):
        self.n_closed += 1

    def is_alive(self):
        return False

    def run_test_with_timeout(self, test, test_key, timeout, progress=None):
        raise AssertionError("never runs")


class TestStartupFailureCleanup:
    def test_failed_start_worker_is_closed(self, tmp_path):
        """Finding 24: a worker whose start() raised may have spawned a real
        process (license seat) — it must be close()d."""
        backend = _stub_backend(tmp_path)
        w0 = _FailStartWorker(0, backend.config)
        w1 = _FailStartWorker(1, backend.config)
        live, failures = backend._start_workers_parallel([w0, w1])
        assert live == []
        assert failures == {0: "start blew up", 1: "start blew up"}
        assert w0.n_closed >= 1
        assert w1.n_closed >= 1

    def test_all_workers_closed_before_all_failed_raise(self, tmp_path):
        from dstf.config import Config

        (tmp_path / "package.mo").write_text("package Lib end Lib;")
        cfg = Config(
            source_path=tmp_path,
            reference_root=tmp_path / "refs",
            backend_name="OpenModelica",
            work_dir=tmp_path / "work",
            parallel=2,
        )

        created: list[_FailStartWorker] = []

        class _FailingBackend(_StubBackend):
            worker_cls = _FailStartWorker

            def make_worker(self, worker_id):
                w = _FailStartWorker(worker_id, self.config)
                created.append(w)
                return w

        backend = _FailingBackend(cfg)
        with pytest.raises(RuntimeError, match="No Stub worker could start"):
            backend.run_tests([_tm("Lib.A"), _tm("Lib.B")])
        assert created
        assert all(w.n_closed >= 1 for w in created)


# =========================================================================
# Phase C-v round 1 — items 5 and 6.
#
#   5. A timeout names its per-test escape hatch (simulation.timeout in
#      test_spec.json), because --timeout only moves the global default.
#   6. A failed post-timeout restart gets exactly ONE retry after a short
#      backoff before the worker is retired. At --parallel 1 a single
#      licence blip used to fail-mark the whole remaining queue.
# =========================================================================


class _AlwaysTimeoutWorker(Worker):
    """run_test never returns, so the shared watchdog always expires."""

    def start(self):
        pass

    def close(self, grace: float = 5.0):
        pass

    def is_alive(self):
        return True

    def run_test(self, test, test_key, progress=None):
        time.sleep(30)  # abandoned by the watchdog


def test_timeout_message_names_the_per_test_override(tmp_path):
    """Item 5: the message the dispatcher, the console report and the
    dashboard all read must say how to raise the limit for THIS test."""
    worker = _AlwaysTimeoutWorker(0, SimpleNamespace(work_dir=tmp_path))
    tr = worker.run_test_with_timeout(_tm(), "test_0001", timeout=0.05)

    assert tr.timed_out is True
    assert "Timed out after 0s" in (tr.error_message or "")
    assert "simulation.timeout" in (tr.error_message or "")
    assert "test_spec.json" in (tr.error_message or "")
    assert "--timeout" in (tr.error_message or "")


def test_timeout_detail_is_not_truncated_past_the_hint(tmp_path, capsys):
    """The dispatcher's progress line clipped a failure detail at 80 chars —
    exactly where the hint starts. Timeouts print in full."""
    backend = _stub_backend(tmp_path)
    worker = _AlwaysTimeoutWorker(0, backend.config)
    backend.config.timeout = 0.05

    items = [(_tm("Lib.A"), "test_0001")]
    backend._dispatch_with_restart(
        [worker], items, total=1, manifest=_scratch_manifest(tmp_path, items)
    )

    assert "simulation.timeout" in capsys.readouterr().err


class _FlakyStartWorker(_GoodWorker):
    """Dead after its first test; ``start()`` fails ``n_start_failures``
    times before succeeding (or forever, when the count is None)."""

    def __init__(self, worker_id, config, n_start_failures):
        super().__init__(worker_id, config)
        self.n_start_failures = n_start_failures
        self.start_calls = 0
        self.alive = True

    def start(self):
        self.start_calls += 1
        if self.n_start_failures is None or self.start_calls <= self.n_start_failures:
            raise RuntimeError("licence server busy")
        self.alive = True

    def is_alive(self):
        return self.alive

    def run_test_with_timeout(self, test, test_key, timeout, progress=None):
        self.alive = False  # dies after every test, forcing a restart
        return super().run_test_with_timeout(test, test_key, timeout, progress)


class TestRestartRetry:
    def test_one_retry_rescues_a_transient_start_failure(self, tmp_path, capsys):
        """Item 6: start() fails once then succeeds — the retry must bring
        the worker back and the queue must finish, not be fail-marked."""
        backend = _stub_backend(tmp_path)
        w = _FlakyStartWorker(0, backend.config, n_start_failures=1)
        items = [(_tm(f"Lib.T{i}"), f"test_{i:04d}") for i in range(3)]

        results = backend._dispatch_with_restart(
            [w], items, total=3, manifest=_scratch_manifest(tmp_path, items)
        )

        assert len(results) == 3
        assert all(r.success for r in results), [r.error_message for r in results]
        err = capsys.readouterr().err
        assert "retrying restart" in err

    def test_a_persistently_failing_start_retires_after_exactly_two_attempts(
        self, tmp_path, capsys
    ):
        """The retry is one extra attempt, not an unbounded loop: two
        start() calls per restart, then the worker is retired."""
        backend = _stub_backend(tmp_path)
        w = _FlakyStartWorker(0, backend.config, n_start_failures=None)
        items = [(_tm(f"Lib.T{i}"), f"test_{i:04d}") for i in range(3)]

        results = backend._dispatch_with_restart(
            [w], items, total=3, manifest=_scratch_manifest(tmp_path, items)
        )

        # First test runs on the already-live worker and kills it; the
        # restart before test 2 then burns both attempts and retires.
        assert w.start_calls == 2
        assert sum(1 for r in results if r.success) == 1
        assert len(results) == 3
        err = capsys.readouterr().err
        assert "restart failed twice" in err
        assert "retiring the worker" in err

    def test_the_retry_does_not_double_charge_the_restart_budget(self, tmp_path):
        """Two start() calls are ONE restart attempt — the budget must tick
        once, or a flaky licence server would exhaust it twice as fast.

        The test result is a failure so the counter isn't reset by the
        "worker has demonstrably recovered" branch and stays observable.
        """

        class _FailingTest(_FlakyStartWorker):
            def run_test_with_timeout(self, test, test_key, timeout, progress=None):
                self.alive = False
                return TestRunResult(
                    model_id=test.model_id,
                    test_key=test_key,
                    success=False,
                    error_message="model did not translate",
                )

        backend = _stub_backend(tmp_path)
        w = _FailingTest(0, backend.config, n_start_failures=1)
        w.alive = False

        items = [(_tm("Lib.A"), "test_0001")]
        results = backend._dispatch_with_restart(
            [w], items, total=1, manifest=_scratch_manifest(tmp_path, items)
        )
        assert results[0].error_message == "model did not translate"
        assert w.start_calls == 2
        assert w._n_restarts == 1

    def test_the_production_backoff_is_a_few_seconds(self):
        """The stub subclass shortens it; the shipped default must be long
        enough for a licence seat to actually come back."""
        assert 1.0 <= PersistentBackendBase.restart_retry_backoff_seconds <= 30.0


# =========================================================================
# Review K1 (half b) + K3 — the dispatcher's durability and detail width.
# =========================================================================


class TestDispatchRecordsIncrementally:
    def test_each_verdict_is_on_disk_before_the_run_ends(self, tmp_path):
        """An abort in the dispatch loop skips every epilogue, so the
        manifest must already hold what the run reached. The dispatcher is
        the one place ALL persistent results pass through."""
        backend = _stub_backend(tmp_path)
        items = [(_tm(f"Lib.T{i}"), f"test_{i:04d}") for i in range(3)]
        manifest = _scratch_manifest(tmp_path, items)
        for entry in manifest.manifest.values():
            entry["last_run_status"] = "pending"

        seen: list[dict] = []

        class _Observing(_GoodWorker):
            def run_test_with_timeout(self, test, test_key, timeout, progress=None):
                seen.append(
                    json.loads((tmp_path / "batch_manifest.json").read_text())
                    if (tmp_path / "batch_manifest.json").exists()
                    else {}
                )
                return super().run_test_with_timeout(test, test_key, timeout, progress)

        backend._dispatch_with_restart(
            [_Observing(0, backend.config)], items, total=3, manifest=manifest
        )

        # By the time test 3 started, tests 1 and 2 were durable.
        assert seen[2]["test_0000"]["last_run_status"] == "passed"
        assert seen[2]["test_0001"]["last_run_status"] == "passed"
        assert seen[2]["test_0002"]["last_run_status"] == "pending"

    def test_restart_exhausted_failures_are_recorded_too(self, tmp_path):
        """The synthetic failures a retired worker produces are real verdicts
        — they must not read back as 'pending' and slip past the guard."""
        backend = _stub_backend(tmp_path)
        items = [(_tm(f"Lib.T{i}"), f"test_{i:04d}") for i in range(2)]
        manifest = _scratch_manifest(tmp_path, items)
        manifest.mark_pending({t.model_id for t, _ in items})

        backend._dispatch_with_restart(
            [_DeadWorker(0, backend.config)], items, total=2, manifest=manifest
        )

        saved = json.loads((tmp_path / "batch_manifest.json").read_text())
        assert {e["last_run_status"] for e in saved.values()} == {"failed"}


def test_dashboard_detail_keeps_the_whole_timeout_hint(tmp_path):
    """K3: the on_finish detail clipped at 120 chars, which cuts the longest
    backend's timeout message (Julia's runs to ~128) mid-hint."""
    captured: dict = {}

    class _Progress:
        def on_start(self, test_key, worker_id=None):
            pass

        def on_finish(self, test_key, success, elapsed, detail=None, timed_out=False):
            captured["detail"] = detail

    backend = _stub_backend(tmp_path)
    backend.progress = _Progress()
    long_msg = timeout_message("Julia simulation exceeded 600.0s timeout")
    assert len(long_msg) > 120, "the regression needs an over-120-char message"

    class _TimeoutWorker(_GoodWorker):
        def run_test_with_timeout(self, test, test_key, timeout, progress=None):
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                timed_out=True,
                error_message=long_msg,
            )

    items = [(_tm("Lib.A"), "test_0001")]
    backend._dispatch_with_restart(
        [_TimeoutWorker(0, backend.config)],
        items,
        total=1,
        manifest=_scratch_manifest(tmp_path, items),
    )

    assert captured["detail"] == long_msg
    assert captured["detail"].endswith("--timeout)")


def test_a_non_timeout_failure_detail_is_still_clipped(tmp_path):
    """The lift is timeout-only; an ordinary failure's detail stays bounded
    so one runaway error message can't flood the dashboard."""
    captured: dict = {}

    class _Progress:
        def on_start(self, test_key, worker_id=None):
            pass

        def on_finish(self, test_key, success, elapsed, detail=None, timed_out=False):
            captured["detail"] = detail

    backend = _stub_backend(tmp_path)
    backend.progress = _Progress()

    class _FailWorker(_GoodWorker):
        def run_test_with_timeout(self, test, test_key, timeout, progress=None):
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                error_message="x" * 400,
            )

    items = [(_tm("Lib.A"), "test_0001")]
    backend._dispatch_with_restart(
        [_FailWorker(0, backend.config)],
        items,
        total=1,
        manifest=_scratch_manifest(tmp_path, items),
    )

    assert len(captured["detail"]) == 120
