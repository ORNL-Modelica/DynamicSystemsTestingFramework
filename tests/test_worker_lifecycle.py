"""A persistent worker's start is bounded, its death takes its children with
it, and a running test can be told to stop.

Three things the shared `Worker` template owns for every backend, tested here
with fake workers (and real OS processes where the claim is about processes):

* `start_within` — `start()` against `worker_startup_timeout`, failing at
  once when the worker's process has died, with the backend's own words;
* the watchdog's death watch — the tracked process gone mid-test ends the
  test, and the children recorded while it was alive are killed;
* `request_cancel` — the backend's soft stop (`cancel_running`) with the
  worker reused, else the kill-and-restart path, and a cancelled result
  either way.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import psutil
import pytest

from conftest import make_test_model
from dstf.backends.base import no_worker_message
from dstf.backends.persistent import (
    STARTUP_TIMEOUT_HINT,
    PersistentBackendBase,
    Worker,
    WorkerRunner,
    WorkerStartError,
)
from dstf.backends.types import TestRunResult


def _config(tmp_path, **kw):
    return SimpleNamespace(work_dir=tmp_path, timeout=30.0, parallel=1, **kw)


def _wait_until(pred, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(0.01)
    raise AssertionError("condition not reached")


def _ok(test, test_key) -> TestRunResult:
    return TestRunResult(model_id=test.model_id, test_key=test_key, success=True)


_SLEEP = "import time; time.sleep(120)"


class _Worker(Worker):
    """A template worker whose `run_test` blocks on a gate, and whose start
    is scripted."""

    _START_POLL_SECONDS = 0.02
    _CHILD_WATCH_SECONDS = 0.05
    cancel_grace = 2.0
    launch_grace = 0.0  # these starts launch nothing, unless a test says so

    def __init__(self, worker_id, config, *, start=None, soft_cancel=None):
        super().__init__(worker_id, config)
        self._start_script = start
        self._soft = soft_cancel
        self.handle: object | None = None
        self.gate = threading.Event()
        self.entered = threading.Event()
        self.n_started = 0
        self.n_closed = 0
        self.inner_success = True

    def start(self):
        self.n_started += 1
        if self._start_script is not None:
            self._start_script(self)
        self.handle = object()

    def is_alive(self):
        return self.handle is not None

    def _detach_interface(self):
        handle, self.handle = self.handle, None
        return handle

    def _shutdown_interface(self, handle, grace):
        self.n_closed += 1
        self.gate.set()  # a closed tool lets go of the call parked in it

    def run_test(self, test, test_key, progress=None):
        self.entered.set()
        self.gate.wait(30)
        return TestRunResult(
            model_id=test.model_id, test_key=test_key, success=self.inner_success
        )

    def cancel_running(self, test_key):
        if self._soft is None:
            return False
        return self._soft(self)


# ---------------------------------------------------------------------------
# Bounded start
# ---------------------------------------------------------------------------


class TestBoundedStart:
    def test_a_start_that_sleeps_fails_at_the_bound(self, tmp_path):
        release = threading.Event()
        worker = _Worker(0, _config(tmp_path), start=lambda w: release.wait(30))

        t0 = time.monotonic()
        with pytest.raises(WorkerStartError) as raised:
            worker.start_within(0.2)

        assert str(raised.value) == f"did not start within 0.2 s {STARTUP_TIMEOUT_HINT}"
        assert time.monotonic() - t0 < 5.0
        assert not worker.is_alive()
        release.set()

    def test_the_message_names_the_step_and_the_key_to_raise(self, tmp_path):
        """A slow load in a live tool is not a hang: the sentence says which
        call the tool is in and which key gives it longer."""
        release = threading.Event()

        class _Loading(_Worker):
            def process_detail(self):
                return f"omc is still running during {self._step}"

        def start(worker):
            worker._step = 'loadFile("/lib/package.mo")'
            release.wait(30)

        worker = _Loading(0, _config(tmp_path), start=start)
        with pytest.raises(WorkerStartError) as raised:
            worker.start_within(0.2)
        release.set()

        assert str(raised.value) == (
            "did not start within 0.2 s — omc is still running during "
            'loadFile("/lib/package.mo") (raise "worker_startup_timeout" in '
            "testing.json if this library needs longer)"
        )

    def test_the_bound_comes_from_the_config_then_the_worker_class(self, tmp_path):
        assert _Worker(0, _config(tmp_path)).startup_bound() == 600.0
        configured = _config(tmp_path, worker_startup_timeout=7)
        assert _Worker(0, configured).startup_bound() == 7.0

    def test_the_default_bound_outlasts_a_slow_library_load(self, tmp_path):
        """A large library is minutes of `loadFile` in a live omc (142.9 s,
        measured): the default is for a tool that hangs, so no backend's is
        below ten minutes — and a start of 100 "seconds" passes the default
        path, here with both scaled by a thousand."""
        from dstf.backends.dymola.persistent_backend import DymolaWorker
        from dstf.backends.julia.persistent_backend import JuliaWorker
        from dstf.backends.openmodelica.persistent_backend import OpenModelicaWorker

        assert Worker.startup_timeout >= 600.0
        for cls in (DymolaWorker, OpenModelicaWorker, JuliaWorker):
            assert cls.startup_timeout >= 600.0, cls.__name__

        class _Scaled(_Worker):
            startup_timeout = Worker.startup_timeout / 1000

        worker = _Scaled(0, _config(tmp_path), start=lambda w: time.sleep(100 / 1000))
        worker.start_within()  # the bound is startup_bound(): the default
        assert worker.is_alive()

    def test_a_start_in_flight_reports_its_step_and_its_seconds(self, tmp_path):
        release, loading = threading.Event(), threading.Event()

        def start(worker):
            worker._step = 'loadFile("/lib/package.mo")'
            loading.set()
            release.wait(30)
            worker._step = ""

        worker = _Worker(0, _config(tmp_path), start=start)
        assert worker.starting() is None  # nothing started yet
        th = threading.Thread(target=worker.start_within, args=(30.0,))
        th.start()
        try:
            assert loading.wait(10)
            phase, seconds = worker.starting()
            assert phase == 'loadFile("/lib/package.mo")' and 0.0 <= seconds < 10.0
            time.sleep(0.05)
            assert worker.starting()[1] > seconds  # it counts on
        finally:
            release.set()
            th.join(10)
        assert worker.is_alive() and worker.starting() is None  # started

    def test_a_start_that_failed_is_not_starting_any_more(self, tmp_path):
        release = threading.Event()
        worker = _Worker(0, _config(tmp_path), start=lambda w: release.wait(30))
        with pytest.raises(WorkerStartError):
            worker.start_within(0.1)
        assert worker.starting() is None
        release.set()

    def test_an_abandoned_start_that_returns_later_is_closed(self, tmp_path):
        """The start thread nobody waits for any more must not leave a live
        tool behind, and the worker is never started again."""
        release = threading.Event()
        worker = _Worker(0, _config(tmp_path), start=lambda w: release.wait(30))
        with pytest.raises(WorkerStartError):
            worker.start_within(0.1)

        release.set()
        _wait_until(lambda: worker.n_closed == 1)
        assert not worker.is_alive()
        with pytest.raises(WorkerStartError, match="never returned"):
            worker.start_within(5.0)
        assert worker.n_started == 1

    def test_a_given_up_start_kills_the_process_it_had_launched(self, tmp_path):
        """The tool is up and the start still waits on it when the bound
        passes: the worker is closed, and the tool goes with it."""
        release = threading.Event()
        launched: list[subprocess.Popen] = []

        def start(worker):
            proc = subprocess.Popen([sys.executable, "-c", _SLEEP])
            launched.append(proc)
            worker._track_launch(proc.pid)
            release.wait(30)  # the tool's first answer, which never comes

        worker = _Worker(0, _config(tmp_path), start=start)
        try:
            with pytest.raises(WorkerStartError, match="did not start within"):
                worker.start_within(0.3)

            assert launched, "the start never launched its tool"
            _wait_until(lambda: launched[0].poll() is not None)
            assert not worker.is_alive() and worker.pids == set()
        finally:
            release.set()
            for proc in launched:
                proc.kill()

    def test_a_process_launched_after_the_give_up_is_killed_at_its_birth(
        self, tmp_path
    ):
        """Given up before the tool existed: nobody is left to kill it, so
        the launch does — and the start ends there."""
        release, ended = threading.Event(), threading.Event()
        launched: list[subprocess.Popen] = []
        raised: list[BaseException] = []

        def start(worker):
            release.wait(30)  # e.g. the launch waiting for a free port
            proc = subprocess.Popen([sys.executable, "-c", _SLEEP])
            launched.append(proc)
            try:
                worker._track_launch(proc.pid)
            except WorkerStartError as exc:
                raised.append(exc)
                raise
            finally:
                ended.set()

        worker = _Worker(0, _config(tmp_path), start=start)
        try:
            with pytest.raises(WorkerStartError):
                worker.start_within(0.1)
            assert launched == []

            release.set()
            assert ended.wait(10)
            _wait_until(lambda: launched[0].poll() is not None)
            assert [str(exc) for exc in raised] == ["its start was given up"]
            assert not worker.is_alive()
        finally:
            release.set()
            for proc in launched:
                proc.kill()

    def test_the_launch_itself_kills_what_an_abandoned_start_brought_up(self, tmp_path):
        """Before the start returns, not after: the give-up waits only for
        that return, and its caller may be gone a moment later."""
        proc = subprocess.Popen([sys.executable, "-c", _SLEEP])
        worker = _Worker(0, _config(tmp_path))
        worker._start_abandoned = True
        try:
            with pytest.raises(WorkerStartError, match="its start was given up"):
                worker._track_launch(proc.pid)
            assert proc.wait(10) is not None
        finally:
            proc.kill()

    def test_the_give_up_waits_for_a_launch_in_flight(self, tmp_path):
        """A launch call that hands its process over only when it returns
        (OMPython's): the give-up does not return before that — a caller
        that exits right after it would leave the tool running."""
        release = threading.Event()
        launched: list[subprocess.Popen] = []

        def start(worker):
            release.wait(30)  # inside the launch call
            proc = subprocess.Popen([sys.executable, "-c", _SLEEP])
            launched.append(proc)
            worker._track_launch(proc.pid)

        worker = _Worker(0, _config(tmp_path), start=start)
        worker.launch_grace = 20.0
        threading.Timer(0.4, release.set).start()
        try:
            t0 = time.monotonic()
            with pytest.raises(WorkerStartError, match="did not start within 0.1 s"):
                worker.start_within(0.1)

            # Launched, and killed, by the time the give-up returned.
            assert len(launched) == 1 and launched[0].wait(5) is not None
            assert time.monotonic() - t0 < 10.0  # the launch's return, not the grace
        finally:
            release.set()
            for proc in launched:
                proc.kill()

    def test_a_start_whose_process_died_fails_at_once(self, tmp_path):
        """Not at the bound: the moment the tracked process is gone."""
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        release = threading.Event()

        def start(worker):
            worker.pids = {proc.pid}
            proc.kill()
            release.wait(30)  # the reply that never comes

        class _Named(_Worker):
            def process_detail(self):
                return f"tool exited (code {proc.wait()}) during loadModel(Lib)"

        worker = _Named(0, _config(tmp_path), start=start)
        t0 = time.monotonic()
        with pytest.raises(
            WorkerStartError, match=r"tool exited \(code -9\) during loadModel"
        ):
            worker.start_within(60.0)

        assert time.monotonic() - t0 < 10.0
        release.set()

    def test_a_start_that_raises_keeps_its_own_message(self, tmp_path):
        def start(worker):
            raise RuntimeError("Worker 0: failed to load library — no licence")

        worker = _Worker(0, _config(tmp_path), start=start)
        with pytest.raises(WorkerStartError, match="no licence"):
            worker.start_within(5.0)

    def test_the_message_names_each_worker_once(self):
        message = no_worker_message(
            "OpenModelica",
            {1: "did not start within 60 s", 0: "Worker 0: failed to load X"},
        )
        assert message == (
            "No OpenModelica worker could start — worker 0: failed to load X; "
            "worker 1: did not start within 60 s"
        )


class _Backend(PersistentBackendBase):
    backend_label = "Stub"
    restart_retry_backoff_seconds = 0.01

    def read_result(self, test, test_key, run_result):  # pragma: no cover
        raise NotImplementedError


def _cli_config(tmp_path, **kw):
    from dstf.config import Config

    (tmp_path / "package.mo").write_text("package Lib end Lib;")
    return Config(
        source_path=tmp_path,
        reference_root=tmp_path / "refs",
        backend_name="OpenModelica",
        work_dir=tmp_path / "work",
        **kw,
    )


def _write_testing_json(tmp_path, **keys):
    import json

    (tmp_path / "package.mo").write_text("package Lib end Lib;")
    path = tmp_path / "testing.json"
    path.write_text(json.dumps({"source_path": ".", "backend": "Dymola", **keys}))
    return path


def test_testing_json_sets_the_bound(tmp_path):
    from dstf.config import Config

    unset = Config(config_file=_write_testing_json(tmp_path), work_dir=tmp_path / "w")
    assert unset.worker_startup_timeout is None  # each backend's own default
    path = _write_testing_json(tmp_path, worker_startup_timeout=90)
    config = Config(config_file=path, work_dir=tmp_path / "w")
    assert config.worker_startup_timeout == 90.0
    assert _Worker(0, config).startup_bound() == 90.0


@pytest.mark.parametrize("bad", ["60", True, 0, -5, float("inf"), [60]])
def test_a_bound_that_is_no_positive_number_is_a_config_error(tmp_path, bad):
    from dstf.config import Config, ConfigError

    path = _write_testing_json(tmp_path, worker_startup_timeout=bad)
    with pytest.raises(ConfigError, match="worker_startup_timeout"):
        Config(config_file=path, work_dir=tmp_path / "w")


def test_a_run_whose_workers_hang_at_start_fails_with_the_message(tmp_path):
    """The CLI's `run`: a start that never returns used to be waited on for
    good, every test "queued". Now the run ends, each test failed with the
    backend's message."""
    import json

    release = threading.Event()

    class _Hung(_Backend):
        def make_worker(self, worker_id):
            return _Worker(worker_id, self.config, start=lambda w: release.wait(30))

    config = _cli_config(tmp_path, parallel=2)
    config.worker_startup_timeout = 0.2
    backend = _Hung(config)
    backend.quiet = True

    with pytest.raises(RuntimeError) as raised:
        backend.run_tests([make_test_model("Lib.A"), make_test_model("Lib.B")])
    release.set()

    reason = f"did not start within 0.2 s {STARTUP_TIMEOUT_HINT}"
    message = f"No Stub worker could start — worker 0: {reason}; worker 1: {reason}"
    assert str(raised.value) == f"{message}. Try re-running with --batch."
    manifest = json.loads((config.work_dir / "batch_manifest.json").read_text())
    assert {e["last_run_status"] for e in manifest.values()} == {"failed"}
    rows = json.loads((config.work_dir / "status.json").read_text())["tests"]
    assert [(r["status"], r["detail"]) for r in rows] == [("failed", message)] * 2


def test_a_restart_that_never_returns_retires_the_worker(tmp_path):
    """No second attempt on a worker whose start is still parked somewhere."""
    release = threading.Event()
    backend = _Backend(_config(tmp_path, worker_startup_timeout=0.1))
    backend.quiet = True
    worker = _Worker(0, backend.config, start=lambda w: release.wait(30))
    runner = WorkerRunner(backend, worker)

    result = runner.run(make_test_model("Lib.A"), "test_0001")
    release.set()

    assert runner.retired == "Worker dead; restart exhausted"
    assert result.error_message == runner.retired
    assert worker.n_started == 1


# ---------------------------------------------------------------------------
# A worker that dies takes its children with it
# ---------------------------------------------------------------------------

_PARENT = (
    "import subprocess, sys, time;"
    "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']);"
    "print(c.pid, flush=True); time.sleep(120)"
)


def _tool_with_child():
    """A stand-in tool process with one child doing the simulating."""
    parent = subprocess.Popen([sys.executable, "-c", _PARENT], stdout=subprocess.PIPE)
    assert parent.stdout is not None
    child = psutil.Process(int(parent.stdout.readline()))
    return parent, child


def _gone(proc: psutil.Process) -> bool:
    return not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE


class TestWorkerDeath:
    def test_the_tracked_process_dying_mid_test_kills_its_children(self, tmp_path):
        parent, child = _tool_with_child()
        try:
            worker = _Worker(0, _config(tmp_path))
            worker.start()
            worker.pids = {parent.pid}
            box: dict = {}
            th = threading.Thread(
                target=lambda: box.update(
                    r=worker.run_test_with_timeout(make_test_model(), "test_0001", 60)
                )
            )
            th.start()
            worker.entered.wait(10)
            time.sleep(0.3)  # several watch ticks: the child is on record
            parent.kill()  # the tool dies by itself; its child is orphaned
            parent.wait()
            th.join(20)

            assert not th.is_alive(), "the watchdog never noticed the death"
            result = box["r"]
            assert not result.success and not result.cancelled
            assert "exited during the test" in result.error_message
            # Charged against the restart budget: nobody killed it on purpose.
            assert result.worker_killed is False and result.timed_out is False
            assert not worker.is_alive()
            _wait_until(lambda: _gone(child))
        finally:
            for proc in (child, parent):
                try:
                    proc.kill()
                except (psutil.Error, OSError):
                    pass

    def test_the_dispatch_step_restarts_after_a_death(self, tmp_path):
        """Failed, then the next test restarts the worker — as a worker
        exception is handled, and charged the same."""
        parent, child = _tool_with_child()
        try:
            backend = _Backend(_config(tmp_path))
            backend.quiet = True
            worker = _Worker(0, backend.config)
            worker.start()
            worker.pids = {parent.pid}
            runner = WorkerRunner(backend, worker)
            threading.Timer(0.3, lambda: (parent.kill(), parent.wait())).start()

            first = runner.run(make_test_model("Lib.A"), "test_0001")
            assert not first.success and not worker.is_alive()

            worker.gate.set()
            second = runner.run(make_test_model("Lib.B"), "test_0002")
            assert second.success
            assert worker.n_started == 2 and worker._n_restarts == 0
            _wait_until(lambda: _gone(child))
        finally:
            for proc in (child, parent):
                try:
                    proc.kill()
                except (psutil.Error, OSError):
                    pass


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


def _run_in_thread(worker, test_key="test_0001", timeout=60.0):
    box: dict = {}
    th = threading.Thread(
        target=lambda: box.update(
            r=worker.run_test_with_timeout(make_test_model(), test_key, timeout)
        )
    )
    th.start()
    assert worker.entered.wait(10)
    return th, box


class TestCancel:
    def test_a_soft_stop_cancels_and_keeps_the_worker(self, tmp_path):
        def soft(worker):
            worker.inner_success = False  # the tool reports "stopped early"
            worker.gate.set()
            return True

        worker = _Worker(0, _config(tmp_path), soft_cancel=soft)
        worker.start()
        th, box = _run_in_thread(worker)

        assert worker.request_cancel("test_0001") is True
        th.join(10)

        result = box["r"]
        assert result.cancelled and not result.success
        assert result.error_message == "Cancelled"
        assert result.worker_killed is False
        assert worker.is_alive() and worker.n_closed == 0

    def test_without_a_soft_stop_the_worker_is_killed(self, tmp_path):
        worker = _Worker(0, _config(tmp_path))
        worker.start()
        th, box = _run_in_thread(worker)

        assert worker.request_cancel("test_0001") is True
        th.join(10)

        result = box["r"]
        assert result.cancelled and result.worker_killed and not result.timed_out
        assert not worker.is_alive() and worker.n_closed == 1

    def test_a_soft_stop_the_tool_ignores_falls_back_to_the_kill(self, tmp_path):
        worker = _Worker(0, _config(tmp_path), soft_cancel=lambda w: True)
        worker.cancel_grace = 0.2
        worker.start()
        th, box = _run_in_thread(worker)

        t0 = time.monotonic()
        worker.request_cancel("test_0001")
        th.join(10)

        assert box["r"].cancelled and box["r"].worker_killed
        assert 0.2 <= time.monotonic() - t0 < 5.0

    def test_a_run_that_finished_anyway_keeps_its_result(self, tmp_path):
        """The stop landed as the simulation completed: a result is a result."""

        def soft(worker):
            worker.gate.set()  # inner_success stays True
            return True

        worker = _Worker(0, _config(tmp_path), soft_cancel=soft)
        worker.start()
        th, box = _run_in_thread(worker)
        worker.request_cancel("test_0001")
        th.join(10)

        assert box["r"].success and not box["r"].cancelled

    def test_only_the_running_test_can_be_cancelled(self, tmp_path):
        worker = _Worker(0, _config(tmp_path))
        worker.start()
        assert worker.request_cancel("test_0001") is False  # nothing runs
        th, box = _run_in_thread(worker)
        assert worker.request_cancel("test_0002") is False  # another test
        worker.gate.set()
        th.join(10)
        assert box["r"].success
        assert worker.request_cancel("test_0001") is False  # it ended

    def test_a_cancelled_kill_is_not_charged_to_the_restart_budget(self, tmp_path):
        backend = _Backend(_config(tmp_path))
        backend.quiet = True
        worker = _Worker(0, backend.config)
        worker.start()
        runner = WorkerRunner(backend, worker)
        threading.Timer(0.2, lambda: runner.cancel("test_0001")).start()

        first = runner.run(make_test_model("Lib.A"), "test_0001")
        assert first.cancelled and not worker.is_alive()

        worker.gate.set()
        assert runner.run(make_test_model("Lib.B"), "test_0002").success
        assert worker.n_started == 2 and worker._n_restarts == 0


# ---------------------------------------------------------------------------
# Each backend's own stop
# ---------------------------------------------------------------------------

posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="names a process by a copied executable"
)


def _spawn_tool(tmp_path, children: list[tuple[list[str], str]]):
    """A stand-in tool process whose children are *children* — ``(argv,
    cwd)`` each. Returns ``(tool, [psutil child])`` once they all run."""
    script = (
        "import subprocess, sys, time, json\n"
        "for argv, cwd in json.loads(sys.argv[1]):\n"
        "    print(subprocess.Popen(argv, cwd=cwd).pid, flush=True)\n"
        "time.sleep(120)\n"
    )
    import json

    tool = subprocess.Popen(
        [sys.executable, "-c", script, json.dumps(children)], stdout=subprocess.PIPE
    )
    assert tool.stdout is not None
    return tool, [psutil.Process(int(tool.stdout.readline())) for _ in children]


def _kill_all(tool, children) -> None:
    for proc in (*children, tool):
        try:
            proc.kill()
        except (psutil.Error, OSError):
            pass
    tool.wait()


@posix_only
class TestDymolaStop:
    """The `stop` file, for the process NAMED dymosim and no other."""

    def _worker(self, tmp_path):
        from dstf.backends.dymola.persistent_backend import DymolaWorker

        return DymolaWorker(
            0,
            SimpleNamespace(work_dir=tmp_path, backend_path=None),
            SimpleNamespace(show_ide=False, backend_setup=[]),
            object,
        )

    def _dymosim(self, tmp_path) -> str:
        import shutil

        exe = tmp_path / "bin" / "dymosim"
        exe.parent.mkdir()
        shutil.copy(shutil.which("sleep"), exe)
        return str(exe)

    def test_a_running_dymosim_is_asked_to_stop_in_its_own_directory(self, tmp_path):
        sim_dir, other = tmp_path / "test_0001", tmp_path / "elsewhere"
        sim_dir.mkdir()
        other.mkdir()
        tool, children = _spawn_tool(
            tmp_path,
            [
                ([self._dymosim(tmp_path), "120"], str(sim_dir)),
                # The compiler: "dymosim" on its command line, not its name.
                (["sleep", "120", "dymosim"], str(other)),
            ],
        )
        try:
            _wait_until(lambda: children[0].name() == "dymosim")
            worker = self._worker(tmp_path)
            worker.pids = {tool.pid}

            assert worker.cancel_running("test_0001") is True
            assert (sim_dir / "stop").read_text() == ""
            assert not (other / "stop").exists()
            assert all(child.is_running() for child in children)
        finally:
            _kill_all(tool, children)

    def test_during_translation_there_is_nothing_to_ask(self, tmp_path):
        other = tmp_path / "elsewhere"
        other.mkdir()
        tool, children = _spawn_tool(
            tmp_path, [(["sleep", "120", "dymosim"], str(other))]
        )
        try:
            worker = self._worker(tmp_path)
            worker.pids = {tool.pid}
            assert worker.cancel_running("test_0001") is False
            assert not (other / "stop").exists()
        finally:
            _kill_all(tool, children)

    def test_the_backend_that_holds_the_worker_declares_cancel(self):
        from dstf.backends.dymola.backend import DymolaBackend
        from dstf.backends.dymola.persistent_backend import PersistentDymolaBackend
        from dstf.backends.types import Capability

        assert Capability.CANCEL in PersistentDymolaBackend.capabilities
        assert Capability.CANCEL not in DymolaBackend.capabilities


@posix_only
class TestDymolaLaunch:
    """Dymola is on record from the moment its process exists — not from the
    moment the interface's constructor, which then waits for it to answer,
    hands back a finished session."""

    def _interface(self, gate_before_launch: threading.Event | None = None):
        launched: list[subprocess.Popen] = []
        answered = threading.Event()

        class _Interface:
            """DymolaInterface's shape where the launch is concerned."""

            def __init__(self, showwindow=False, dymolapath=None):
                self._dymola_process = None
                if gate_before_launch is not None:
                    gate_before_launch.wait(30)
                self._start_dymola(dymolapath)
                answered.wait(30)  # ping ... ping ... until Dymola answers

            def _start_dymola(self, dymolapath):
                self._dymola_process = subprocess.Popen([dymolapath, "120"])
                launched.append(self._dymola_process)

        return _Interface, launched, answered

    def _worker(self, tmp_path, interface_cls, bound: float):
        import shutil

        from dstf.backends.dymola.persistent_backend import DymolaWorker

        exe = tmp_path / "bin" / "dymola"  # the name the kill guard asks for
        exe.parent.mkdir()
        shutil.copy(shutil.which("sleep"), exe)
        worker = DymolaWorker(
            0,
            SimpleNamespace(
                work_dir=tmp_path, backend_path=exe, worker_startup_timeout=bound
            ),
            SimpleNamespace(show_ide=False, backend_setup=[]),
            interface_cls,
        )
        worker._START_POLL_SECONDS = 0.02
        return worker

    def test_a_start_given_up_during_the_launch_kills_dymola(self, tmp_path):
        interface_cls, launched, answered = self._interface()
        worker = self._worker(tmp_path, interface_cls, 0.3)
        try:
            with pytest.raises(WorkerStartError, match="did not start within 0.3 s"):
                worker.start_within()

            assert len(launched) == 1, "the constructor never launched"
            _wait_until(lambda: launched[0].poll() is not None)
            assert worker.dymola is None and worker.pids == set()
        finally:
            answered.set()
            for proc in launched:
                proc.kill()

    def test_a_dymola_launched_after_the_give_up_is_killed_at_once(self, tmp_path):
        gate = threading.Event()
        interface_cls, launched, answered = self._interface(gate)
        worker = self._worker(tmp_path, interface_cls, 0.1)
        try:
            with pytest.raises(WorkerStartError):
                worker.start_within()
            assert launched == []

            gate.set()  # the launch goes ahead on the thread nobody waits for
            _wait_until(lambda: len(launched) == 1)
            _wait_until(lambda: launched[0].poll() is not None)
            assert worker.dymola is None
        finally:
            gate.set()
            answered.set()
            for proc in launched:
                proc.kill()

    def test_a_started_worker_tracks_the_launched_process(self, tmp_path):
        interface_cls, launched, answered = self._interface()
        answered.set()
        worker = self._worker(tmp_path, interface_cls, 5.0)
        interface_cls.cd = lambda self, path: None
        interface_cls.openModel = lambda self, path: True
        interface_cls.ExecuteCommand = lambda self, cmd: True
        interface_cls.getLastErrorLog = lambda self: ""
        interface_cls.close = lambda self: None
        worker.config.dependencies = []
        worker.config.library_dir = tmp_path
        try:
            worker.start_within()
            assert worker.is_alive() and worker.pids == {launched[0].pid}
            worker.close(grace=0.5)
            _wait_until(lambda: launched[0].poll() is not None)
        finally:
            for proc in launched:
                proc.kill()


class TestOpenModelicaStop:
    def _worker(self, tmp_path, session=None):
        from dstf.backends.openmodelica.persistent_backend import OpenModelicaWorker

        worker = OpenModelicaWorker(
            0,
            SimpleNamespace(work_dir=tmp_path),
            SimpleNamespace(std_version="3.3", diagnostic_variables=[]),
            lambda: None,
        )
        worker.session = session
        return worker

    def test_omc_s_children_are_killed_and_omc_is_left(self, tmp_path):
        tool, children = _spawn_tool(
            tmp_path, [([sys.executable, "-c", "import time; time.sleep(120)"], ".")]
        )
        try:
            worker = self._worker(tmp_path)
            worker.pids = {tool.pid}

            assert worker.cancel_running("test_0001") is True
            _wait_until(lambda: _gone(children[0]))
            assert tool.poll() is None
            # omc's own phases: no child, so no soft stop.
            assert worker.cancel_running("test_0001") is False
        finally:
            _kill_all(tool, children)

    def test_the_detail_is_omc_s_exit_its_step_and_its_log(self, tmp_path):
        process = SimpleNamespace(poll=lambda: -9)
        session = SimpleNamespace(
            omc_process=SimpleNamespace(
                _omc_process=process, get_log=lambda: "Killed\n"
            )
        )
        worker = self._worker(tmp_path, session)
        worker._step = "loadModel(Modelica)"

        assert worker.process_detail() == (
            "omc exited (code -9) during loadModel(Modelica); omc log: Killed"
        )
        process.poll = lambda: None
        assert worker.process_detail().startswith("omc is still running during")

    def test_the_backend_that_holds_the_worker_declares_cancel(self):
        from dstf.backends.openmodelica.backend import OpenModelicaBackend
        from dstf.backends.openmodelica.persistent_backend import (
            PersistentOpenModelicaBackend,
        )
        from dstf.backends.types import Capability

        assert Capability.CANCEL in PersistentOpenModelicaBackend.capabilities
        assert Capability.CANCEL not in OpenModelicaBackend.capabilities


def test_every_cancel_flag_has_a_stop_behind_it():
    """The flag is what the queue gates on: a registered class that declares
    it stops its own single-test path, a persistent variant's worker runs
    under the shared watchdog (whose kill is the stop of last resort) or
    reads the cancel in a watchdog of its own."""
    import dstf.backends as backends
    from dstf.backends.base import Backend
    from dstf.backends.types import Capability

    for name in ("Dymola", "OpenModelica", "FMPy", "Julia", "Python"):
        try:
            backends._import_builtin_backend(name)
        except ImportError:
            continue
    declared = set()
    for name, cls in backends._REGISTRY.items():
        variant = cls.persistent_backend_cls()
        if Capability.CANCEL in cls.capabilities:
            declared.add(name)
            assert cls.cancel_test is not Backend.cancel_test, name
        if variant is not None and Capability.CANCEL in variant.capabilities:
            declared.add(f"{name} (persistent)")
            hears = variant.worker_cls.run_test_with_timeout
            assert (
                hears is Worker.run_test_with_timeout
                or "_cancel" in hears.__code__.co_names
            ), name
    assert declared == {
        "Python",
        "Dymola (persistent)",
        "OpenModelica (persistent)",
        "Julia (persistent)",
    }


def test_a_cancel_flag_without_a_stop_is_refused_at_registration():
    from dstf.backends import _validate_capabilities
    from dstf.backends.base import Backend
    from dstf.backends.types import Capability

    class _Claims(Backend):
        capabilities = frozenset({Capability.CANCEL})

        def read_result(self, test, test_key, run_result):  # pragma: no cover
            raise NotImplementedError

    with pytest.raises(TypeError, match="Capability.CANCEL"):
        _validate_capabilities(_Claims, "Claims")


def test_the_python_backend_kills_the_test_s_subprocess(tmp_path):
    from conftest import python_example_copy
    from dstf.backends import select_backend

    config, _ = python_example_copy(tmp_path)
    slow = config.source_path / "Slow.py"
    slow.write_text(
        "import time\n\n\ndef simulate(stop_time, tolerance):\n"
        "    time.sleep(120)\n    return {'time': [0.0], 'variables': {'x': [0.0]}}\n"
    )
    backend = select_backend(config, persistent=True)
    backend.quiet = True
    test = make_test_model("Lib.Slow", source_file=slow, variable_patterns=["x"])
    assert backend.cancel_test("test_0001") is False  # nothing runs yet
    box: dict = {}
    th = threading.Thread(
        target=lambda: box.update(r=backend.run_single_test(test, "test_0001", 1, 1))
    )
    th.start()
    _wait_until(lambda: "test_0001" in backend._procs)
    child = psutil.Process(backend._procs["test_0001"].pid)

    t0 = time.monotonic()
    assert backend.cancel_test("test_0001") is True
    th.join(20)

    result = box["r"]
    assert result.cancelled and not result.success and not result.timed_out
    assert result.error_message == "Cancelled"
    assert time.monotonic() - t0 < 10.0
    assert _gone(child)
    assert backend._procs == {} and backend._cancelled == set()
