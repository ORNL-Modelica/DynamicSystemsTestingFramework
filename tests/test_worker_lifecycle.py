"""A persistent worker's start is bounded, its death takes its children with
it, and a running test can be told to stop.

Three things the shared `Worker` template owns for every backend, tested here
with fake workers (and real OS processes where the claim is about processes):

* `start_within` — `start()` against `worker_startup_timeout`, failing at
  once when the worker's process has died, with the backend's own words;
* the watchdog's death watch — the tracked process gone mid-test ends the
  test, and the children recorded while it was alive are killed; gone while
  the worker sat idle, it fails no test — the worker is restarted first;
* `request_cancel` — the backend's soft stop (`cancel_running`) with the
  worker reused, else the kill-and-restart path, and a cancelled result
  either way.
"""

from __future__ import annotations

import http.client
import subprocess
import sys
import threading
import time
import urllib.error
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
        return self.handle is not None and not self.tool_gone()

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

    assert runner.retired == (
        f"Worker dead; restart exhausted — did not start within 0.1 s "
        f"{STARTUP_TIMEOUT_HINT}"
    )
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
    try:
        return not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True  # reaped between the two looks


def _on_record(worker, child: psutil.Process) -> bool:
    """A watch tick has recorded *child* among the tool's children."""
    return any(pid == child.pid for pid, _ in tuple(worker._orphan_children))


def _kill_quietly(*procs) -> None:
    for proc in procs:
        try:
            proc.kill()
        except (psutil.Error, OSError):
            pass


linux_only = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="an orphan is found by its working directory and its parent",
)


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
            _wait_until(lambda: _on_record(worker, child), 30)
            parent.kill()  # the tool dies by itself; its child is orphaned
            parent.wait()
            th.join(60)

            assert not th.is_alive(), "the watchdog never noticed the death"
            result = box["r"]
            assert not result.success and not result.cancelled
            assert "exited during the test" in result.error_message
            # Charged against the restart budget: nobody killed it on purpose.
            assert result.worker_killed is False and result.timed_out is False
            assert not worker.is_alive()
            _wait_until(lambda: _gone(child), 30)
        finally:
            _kill_quietly(child, parent)

    def test_a_tool_that_dies_between_two_looks_still_loses_its_children(
        self, tmp_path, monkeypatch
    ):
        """The tick walks the tool's tree and looks whether the tool is
        there. A death between the two used to leave the child on no
        record: the look said "alive", the walk that followed found nothing
        and replaced the record of the tick before."""
        import dstf.backends.persistent as persistent

        parent, child = _tool_with_child()
        worker = _Worker(0, _config(tmp_path))
        real_gone = persistent.process_gone
        died: list[bool] = []

        def dies_right_after_the_look(pids, name_hint=None):
            gone = real_gone(pids, name_hint)
            if not gone and not died and _on_record(worker, child):
                died.append(True)
                parent.kill()
                parent.wait()
            return gone

        monkeypatch.setattr(persistent, "process_gone", dies_right_after_the_look)
        try:
            worker.start()
            worker.pids = {parent.pid}
            result = worker.run_test_with_timeout(make_test_model(), "test_0001", 60)

            assert died and "exited during the test" in result.error_message
            _wait_until(lambda: _gone(child), 30)
        finally:
            _kill_quietly(child, parent)

    @linux_only
    def test_a_child_no_tick_saw_is_found_by_its_mark_and_no_stranger_is(
        self, tmp_path
    ):
        """dymosim appears and the tool is killed within one tick: the
        record is empty. What the tool left is found by all of — parentless,
        born after the tool, in this test's directory — and a process that
        fails one of the three is never touched."""
        test_dir, sibling = tmp_path / "test_0001", tmp_path / "test_0002"
        test_dir.mkdir()
        sibling.mkdir()
        sleeper = [sys.executable, "-c", _SLEEP]
        # Born before the tool, orphaned, in the test's directory.
        launcher, (older,) = _spawn_tool(tmp_path, [(sleeper, str(test_dir))])
        launcher.kill()
        launcher.wait()
        time.sleep(0.1)
        # The tool's own: one in the test's directory, one in a sibling's.
        tool, (left_behind, elsewhere) = _spawn_tool(
            tmp_path, [(sleeper, str(test_dir)), (sleeper, str(sibling))]
        )
        # Not an orphan: this process's child, in the test's directory.
        ours = subprocess.Popen(sleeper, cwd=test_dir)
        strangers = {
            "born before the tool": older,
            "running in another test's directory": elsewhere,
            "that is somebody's live child": psutil.Process(ours.pid),
        }

        class _Blind(_Worker):
            def _live_descendants(self):
                return set()  # no tick ever saw a child

        worker = _Blind(0, _config(tmp_path))
        try:
            worker.start()
            worker.pids = {tool.pid}
            threading.Thread(
                target=lambda: (worker.entered.wait(30), tool.kill(), tool.wait())
            ).start()
            result = worker.run_test_with_timeout(make_test_model(), "test_0001", 60)

            assert "exited during the test" in result.error_message
            _wait_until(lambda: _gone(left_behind), 30)
            time.sleep(0.3)  # a kill of a stranger would have landed by now
            for why, proc in strangers.items():
                assert not _gone(proc), f"killed a process {why}"
        finally:
            _kill_quietly(left_behind, tool, ours, *strangers.values())
            ours.wait()

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

            def die_once_on_record():
                _wait_until(lambda: _on_record(worker, child), 30)
                parent.kill()
                parent.wait()

            threading.Thread(target=die_once_on_record).start()

            first = runner.run(make_test_model("Lib.A"), "test_0001")
            assert not first.success and not worker.is_alive()

            worker.gate.set()
            second = runner.run(make_test_model("Lib.B"), "test_0002")
            assert second.success
            assert worker.n_started == 2 and worker._n_restarts == 0
            _wait_until(lambda: _gone(child), 30)
        finally:
            _kill_quietly(child, parent)


class _Stepping(_Worker):
    """Says its step as Dymola and omc do — the call in flight, named after
    the test — for the models in `names_its_step`, and its exit with it."""

    names_its_step: frozenset = frozenset()

    def start(self):
        super().start()
        self.tool = subprocess.Popen([sys.executable, "-c", _SLEEP])
        self.pids = {self.tool.pid}

    def run_test(self, test, test_key, progress=None):
        if test.model_id in self.names_its_step:
            self._step = f"simulate({test.model_id})"
        return super().run_test(test, test_key, progress)

    def process_detail(self):
        return "tool exited" + (f" during {self._step}" if self._step else "")


class _Progress:
    """`Backend.progress`, calling `between` once the first test has been
    recorded — the moment between two tests of a run."""

    #: The run's metadata, as `record_result` asks it (none recorded here).
    metadata = None

    def __init__(self, between):
        self._between = between

    def on_start(self, test_key, worker_id=None):
        pass

    def on_finish(self, test_key, **kw):
        between, self._between = self._between, None
        if between is not None:
            between()


class TestIdleDeath:
    """The tool gone while its worker waited for a test: that test is run,
    on a restarted worker. Only a death DURING a test fails it."""

    def _runner(self, tmp_path, *, quiet=True, names_its_step=()):
        backend = _Backend(_config(tmp_path))
        backend.quiet = quiet
        worker = _Stepping(0, backend.config)
        worker.names_its_step = frozenset(names_its_step)
        worker.start()
        worker.gate.set()
        return backend, worker, WorkerRunner(backend, worker)

    def _kill_tool(self, worker):
        worker.tool.kill()
        worker.tool.wait()

    def test_the_next_test_runs_on_a_restarted_worker(self, tmp_path):
        backend, worker, runner = self._runner(tmp_path)
        try:
            assert runner.run(make_test_model("Lib.A"), "test_0001").success
            first_tool = worker.tool
            self._kill_tool(worker)
            # Dead, and nobody has closed it: it still holds its handle.
            assert worker.handle is not None and not worker.is_alive()

            second = runner.run(make_test_model("Lib.B"), "test_0002")

            assert second.success and second.model_id == "Lib.B"
            assert second.error_message is None
            assert worker.n_started == 2 and worker.n_closed == 1
            assert worker.tool is not first_tool and worker.is_alive()
            # Charged like any death nobody ordered, and paid back by the pass.
            assert worker._n_restarts == 0
        finally:
            _kill_quietly(worker.tool)

    def test_the_restart_is_charged_to_the_worker_s_budget(self, tmp_path):
        """A tool that dies whenever it is left alone is a broken worker:
        the restarts add up, and at the budget it is retired."""
        backend, worker, runner = self._runner(tmp_path)
        backend.max_restarts_per_worker = 1
        try:
            self._kill_tool(worker)
            worker.inner_success = False  # no pass pays the charge back
            first = runner.run(make_test_model("Lib.A"), "test_0001")
            assert first.model_id == "Lib.A" and worker._n_restarts == 1
            self._kill_tool(worker)

            second = runner.run(make_test_model("Lib.B"), "test_0002")

            assert second.error_message == "Worker dead; restart exhausted"
            assert runner.retired == "Worker dead; restart exhausted"
        finally:
            _kill_quietly(worker.tool)

    def test_the_restart_shows_as_a_start_in_flight(self, tmp_path):
        """What the queue lists as `starting`: the worker coming back."""
        backend, worker, runner = self._runner(tmp_path)
        seen: list = []
        worker._start_script = lambda w: seen.append(runner.starting())
        try:
            self._kill_tool(worker)
            assert runner.run(make_test_model("Lib.A"), "test_0001").success
            ((phase, seconds),) = seen
            assert phase == "" and seconds >= 0.0
            assert runner.starting() is None
        finally:
            _kill_quietly(worker.tool)

    def test_a_run_s_dispatch_restarts_between_two_tests(self, tmp_path, capsys):
        """`run_tests` drives the same step: a tool gone between two tests
        of a run costs neither. The line it prints names no test the tool
        was not in — it sat idle."""
        from dstf.storage.batch_manifest import BatchManifest

        backend, worker, _ = self._runner(
            tmp_path, quiet=False, names_its_step=["Lib.A", "Lib.B"]
        )
        backend.progress = _Progress(between=lambda: self._kill_tool(worker))
        items = [(make_test_model(f"Lib.{n}"), f"test_{n}") for n in "AB"]
        manifest = BatchManifest(
            work_dir=tmp_path,
            manifest={tk: {"model_id": t.model_id, "ref_id": None} for t, tk in items},
        )
        try:
            results = backend._dispatch_with_restart([worker], items, 2, manifest)

            assert [(r.model_id, r.success) for r in results] == [
                ("Lib.A", True),
                ("Lib.B", True),
            ]
            assert worker.n_started == 2
            err = capsys.readouterr().err
            assert (
                "  Worker 0: its process was gone before Lib.B — tool exited\n"
            ) in err
            assert "Worker 0: restarted (" in err
        finally:
            _kill_quietly(worker.tool)

    def test_a_death_during_a_test_fails_that_test_in_its_own_name(self, tmp_path):
        """Lib.A named its step and passed. The tool then dies during Lib.B,
        before Lib.B's first call: the message must not say Lib.A's."""
        backend, worker, runner = self._runner(tmp_path, names_its_step=["Lib.A"])
        try:
            assert runner.run(make_test_model("Lib.A"), "test_0001").success
            worker.gate.clear()
            worker.entered.clear()
            threading.Thread(
                target=lambda: (worker.entered.wait(30), self._kill_tool(worker))
            ).start()

            second = runner.run(make_test_model("Lib.B"), "test_0002")

            assert second.model_id == "Lib.B" and not second.success
            assert second.error_message == (
                "Worker process exited during the test — tool exited"
            )
        finally:
            _kill_quietly(worker.tool)

    def test_a_death_during_a_test_names_the_call_it_was_in(self, tmp_path):
        backend, worker, runner = self._runner(tmp_path, names_its_step=["Lib.A"])
        worker.gate.clear()
        threading.Thread(
            target=lambda: (worker.entered.wait(30), self._kill_tool(worker))
        ).start()
        try:
            first = runner.run(make_test_model("Lib.A"), "test_0001")
            assert first.error_message == (
                "Worker process exited during the test — tool exited during "
                "simulate(Lib.A)"
            )
        finally:
            _kill_quietly(worker.tool)


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

    def test_a_runner_hears_only_the_token_of_the_run_it_is_on(self, tmp_path):
        """Not a key (a rerun has the same), not another run's token, and
        nothing at all for a run nobody announced."""
        backend = _Backend(_config(tmp_path))
        backend.quiet = True
        worker = _Worker(0, backend.config)
        worker.start()
        runner = WorkerRunner(backend, worker)
        token, box = object(), {}

        def run(key):
            box[key] = runner.run(make_test_model("Lib.A"), key)

        runner.expect("test_0001", token)
        th = threading.Thread(target=run, args=("test_0001",))
        th.start()
        assert worker.entered.wait(10)
        assert runner.cancel(object()) is False  # another run's
        assert runner.cancel("test_0001") is False  # the key is not the token
        assert runner.cancel(None) is False
        time.sleep(0.2)
        assert th.is_alive() and worker.is_alive()  # nothing landed

        assert runner.cancel(token) is True
        th.join(10)
        assert box["test_0001"].cancelled
        assert runner.cancel(token) is False  # that run is over

        worker.gate.set()
        worker.entered.clear()
        th = threading.Thread(target=run, args=("test_0002",))  # unannounced
        th.start()
        th.join(10)
        assert box["test_0002"].success

    def test_a_tool_that_died_as_the_test_returned_is_closed_and_restarted(
        self, tmp_path
    ):
        """An interface that turns a lost connection into a failed result:
        `run_test` returns, and the worker would look alive. It is closed,
        so the next test starts a new tool instead of failing on a dead one."""
        tool = subprocess.Popen([sys.executable, "-c", _SLEEP])

        class _Returns(_Worker):
            def run_test(self, test, test_key, progress=None):
                if tool.poll() is None:
                    tool.kill()
                    tool.wait()
                    return TestRunResult(
                        model_id=test.model_id,
                        test_key=test_key,
                        success=False,
                        error_message="connection lost",
                    )
                return _ok(test, test_key)

        backend = _Backend(_config(tmp_path))
        backend.quiet = True
        worker = _Returns(0, backend.config)
        worker.start()
        worker.pids = {tool.pid}
        runner = WorkerRunner(backend, worker)

        first = runner.run(make_test_model("Lib.A"), "test_0001")
        assert first.error_message == "connection lost" and not first.cancelled
        assert not worker.is_alive() and worker.n_closed == 1

        second = runner.run(make_test_model("Lib.B"), "test_0002")
        assert second.success and worker.n_started == 2

    def test_a_cancelled_kill_is_not_charged_to_the_restart_budget(self, tmp_path):
        backend = _Backend(_config(tmp_path))
        backend.quiet = True
        worker = _Worker(0, backend.config)
        worker.start()
        runner = WorkerRunner(backend, worker)
        token = object()
        runner.expect("test_0001", token)
        threading.Timer(0.2, lambda: runner.cancel(token)).start()

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


def _spawn_tool(
    tmp_path, children: list[tuple[list[str], str]], name: str | None = None
):
    """A stand-in tool process whose children are *children* — ``(argv,
    cwd)`` each. Returns ``(tool, [psutil child])`` once they all run.

    With *name* the tool is a shell script of that name, so that is its
    process name (``dymola``, ``omc`` — what a worker's PID guard asks
    for); without, a Python process."""
    if name is None:
        script = (
            "import subprocess, sys, time, json\n"
            "for argv, cwd in json.loads(sys.argv[1]):\n"
            "    print(subprocess.Popen(argv, cwd=cwd).pid, flush=True)\n"
            "time.sleep(120)\n"
        )
        import json

        argv = [sys.executable, "-c", script, json.dumps(children)]
    else:
        import shlex

        path = tmp_path / "tools" / name
        path.parent.mkdir(exist_ok=True)
        lines = ["#!/bin/sh"]
        for child_argv, cwd in children:
            lines += [
                f"(cd {shlex.quote(cwd)} && exec {shlex.join(child_argv)}) &",
                "echo $!",
            ]
        # Parked on its stdin, not on `wait`: it outlives its children, and
        # has none of its own beside them.
        path.write_text("\n".join([*lines, "read _", ""]))
        path.chmod(0o755)
        argv = [str(path)]
    tool = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert tool.stdout is not None
    found = [psutil.Process(int(tool.stdout.readline())) for _ in children]
    if name is not None:
        _wait_until(lambda: psutil.Process(tool.pid).name() == name)
    return tool, found


#: The compiler that builds dymosim: "dymosim" on its command line (``-o
#: dymosim``), not as its name.
_COMPILER = [sys.executable, "-c", _SLEEP, "-o", "dymosim"]


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
                (_COMPILER, str(other)),
            ],
            name="dymola",
        )
        try:
            _wait_until(lambda: children[0].name() == "dymosim")
            _wait_until(lambda: "dymosim" in children[1].cmdline())
            assert children[1].name() != "dymosim"
            worker = self._worker(tmp_path)
            worker.pids = {tool.pid}

            assert worker.cancel_running("test_0001") is True
            assert (sim_dir / "stop").read_text() == ""
            assert not (other / "stop").exists()
            assert not any(_gone(child) for child in children)  # asked, not killed
        finally:
            _kill_all(tool, children)

    def test_during_translation_there_is_nothing_to_ask(self, tmp_path):
        other = tmp_path / "elsewhere"
        other.mkdir()
        tool, children = _spawn_tool(tmp_path, [(_COMPILER, str(other))], name="dymola")
        try:
            _wait_until(lambda: "dymosim" in children[0].cmdline())
            worker = self._worker(tmp_path)
            worker.pids = {tool.pid}
            assert worker.cancel_running("test_0001") is False
            assert not (other / "stop").exists()
            assert not _gone(children[0])  # the compiler was there to be asked
        finally:
            _kill_all(tool, children)

    def test_a_tracked_pid_that_is_no_longer_dymola_asks_nobody(self, tmp_path):
        """A recycled PID: the dymosim under it is somebody else's."""
        sim_dir = tmp_path / "test_0001"
        sim_dir.mkdir()
        tool, children = _spawn_tool(
            tmp_path, [([self._dymosim(tmp_path), "120"], str(sim_dir))]
        )
        try:
            _wait_until(lambda: children[0].name() == "dymosim")
            worker = self._worker(tmp_path)
            worker.pids = {tool.pid}  # a Python process now

            assert worker.cancel_running("test_0001") is False
            assert not (sim_dir / "stop").exists()
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


class TestDymolaCloseNoise:
    """Dymola's interface retries a request ten times, a second apart, and
    prints every failed attempt. A thread still inside a call when its
    Dymola is killed prints that for the rest of the call and through the
    next: the noise of a Dymola this process closed is not printed — and
    nothing else is hidden."""

    #: The request in flight when the tool is killed, then every retry.
    LOST = [
        http.client.RemoteDisconnected("Remote end closed connection without response"),
        *(
            urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
            for _ in range(3)
        ),
    ]

    @pytest.fixture
    def dymola(self, monkeypatch):
        """`dymola.dymola_interface_internal`'s shape where a request is
        concerned — a static logger that prints, and a request method that
        logs each failed attempt — installed as that module. Returns
        `(the interface class, what the logger printed)`."""
        import types

        from dstf.backends.dymola import persistent_backend

        printed: list[object] = []

        class DymolaLogger:
            @staticmethod
            def Error(msg):
                DymolaLogger._PrintMessage("error", msg)

            @staticmethod
            def _PrintMessage(level, msg):
                printed.append(msg)

        class DymolaInterfaceInternal:
            def _make_rpc_call(self, attempts, ignoreExceptions=False):
                for failure in attempts:
                    if not ignoreExceptions:
                        DymolaLogger.Error(failure)
                return ""

        module = types.ModuleType("dymola.dymola_interface_internal")
        module.DymolaLogger = DymolaLogger
        module.DymolaInterfaceInternal = DymolaInterfaceInternal
        package = types.ModuleType("dymola")
        package.dymola_interface_internal = module
        monkeypatch.setitem(sys.modules, "dymola", package)
        monkeypatch.setitem(sys.modules, "dymola.dymola_interface_internal", module)
        # The filter is installed once per process: again, on this module.
        monkeypatch.setattr(persistent_backend, "_dymola_logger_patched", False)
        return DymolaInterfaceInternal, printed

    def _closing(self, tmp_path, interface):
        """A worker on *interface*, entered into its teardown."""
        worker = _dymola_worker(tmp_path)
        worker.dymola = interface
        return worker._quiet()

    def test_a_closed_dymola_s_lost_connection_is_not_printed(
        self, tmp_path, dymola, monkeypatch
    ):
        interface_cls, printed = dymola
        interface = interface_cls()
        with self._closing(tmp_path, interface):
            interface._make_rpc_call(self.LOST)  # the call in flight at the kill
        interface._make_rpc_call(self.LOST)  # the abandoned thread's next call
        assert printed == []

        # Not for a while: for good. The call after the next still retries.
        now = time.monotonic()
        monkeypatch.setattr(time, "monotonic", lambda: now + 3600.0)
        interface._make_rpc_call(self.LOST)
        assert printed == []

    def test_another_dymola_s_lost_connection_is_still_printed(self, tmp_path, dymola):
        """A second worker's Dymola that stops answering while the first is
        being closed is a finding: its lines are not the first's."""
        interface_cls, printed = dymola
        closed, live = interface_cls(), interface_cls()
        with self._closing(tmp_path, closed):
            live._make_rpc_call(self.LOST)
            assert printed == self.LOST
        live._make_rpc_call(self.LOST[:1])
        assert printed == [*self.LOST, self.LOST[0]]

    def test_what_a_closed_dymola_says_beside_that_is_still_printed(
        self, tmp_path, dymola
    ):
        interface_cls, printed = dymola
        interface = interface_cls()
        said = ['Error when calling Dymola function "savelog".', ValueError("no JSON")]
        with self._closing(tmp_path, interface):
            interface._make_rpc_call([self.LOST[0], *said])
        assert printed == said

    def test_a_close_that_raises_is_not_swallowed(self, tmp_path, dymola):
        interface_cls, _printed = dymola
        with pytest.raises(RuntimeError, match="the close itself failed"):
            with self._closing(tmp_path, interface_cls()):
                raise RuntimeError("the close itself failed")


@posix_only
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

    def test_a_tracked_pid_that_is_no_longer_omc_loses_no_child(self, tmp_path):
        """A recycled PID: its children are somebody else's, and stay."""
        tool, children = _spawn_tool(tmp_path, [([sys.executable, "-c", _SLEEP], ".")])
        try:
            worker = self._worker(tmp_path)
            worker.pids = {tool.pid}  # a Python process now

            assert worker.cancel_running("test_0001") is False
            time.sleep(0.3)  # a kill would have landed by now
            assert not _gone(children[0])
        finally:
            _kill_all(tool, children)

    def test_omc_s_children_are_killed_and_omc_is_left(self, tmp_path):
        tool, children = _spawn_tool(
            tmp_path, [([sys.executable, "-c", _SLEEP], ".")], name="omc"
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

    def test_the_detail_leaves_out_omc_s_startup_notices(self, tmp_path):
        """A slow `loadFile` in a live omc: the log holds what omc wrote as
        it came up and nothing since — not a word about the load."""
        log = ["Created ZeroMQ Server.\nDumped server port in file: /tmp/om.port.1f\n"]
        session = SimpleNamespace(
            omc_process=SimpleNamespace(
                _omc_process=SimpleNamespace(poll=lambda: None),
                get_log=lambda: log[0],
            )
        )
        worker = self._worker(tmp_path, session)
        worker._step = 'loadFile("/lib/package.mo")'

        assert worker.process_detail() == (
            'omc is still running during loadFile("/lib/package.mo")'
        )
        log[0] += "Error: out of memory\n\n"  # ...and the last line that is one
        assert worker.process_detail() == (
            'omc is still running during loadFile("/lib/package.mo"); '
            "omc log: Error: out of memory"
        )

    def test_the_backend_that_holds_the_worker_declares_cancel(self):
        from dstf.backends.openmodelica.backend import OpenModelicaBackend
        from dstf.backends.openmodelica.persistent_backend import (
            PersistentOpenModelicaBackend,
        )
        from dstf.backends.types import Capability

        assert Capability.CANCEL in PersistentOpenModelicaBackend.capabilities
        assert Capability.CANCEL not in OpenModelicaBackend.capabilities


def _dymola_worker(tmp_path):
    from dstf.backends.dymola.persistent_backend import DymolaWorker

    worker = DymolaWorker(
        0,
        SimpleNamespace(work_dir=tmp_path, backend_path=None),
        SimpleNamespace(show_ide=False, backend_setup=[]),
        object,
    )
    worker.dymola = object()
    return worker


def _omc_worker(tmp_path):
    from dstf.backends.openmodelica.persistent_backend import OpenModelicaWorker

    worker = OpenModelicaWorker(
        0,
        SimpleNamespace(work_dir=tmp_path),
        SimpleNamespace(std_version="3.3", diagnostic_variables=[]),
        lambda: None,
    )
    worker.session = object()
    return worker


@posix_only
@pytest.mark.parametrize(
    "make, name", [(_dymola_worker, "dymola"), (_omc_worker, "omc")]
)
class TestAliveMeansTheToolRuns:
    """A worker that holds its handle is alive only while the process it
    tracks is still its tool — what the dispatch step asks before every
    test, so a tool that died while idle is restarted, not handed one."""

    def test_while_the_tool_runs_and_not_after_it_died(self, tmp_path, make, name):
        tool, _ = _spawn_tool(tmp_path, [], name=name)
        try:
            worker = make(tmp_path)
            worker.pids = {tool.pid}
            assert worker.is_alive() and not worker.tool_gone()

            tool.kill()
            tool.wait()

            assert worker.tool_gone() and not worker.is_alive()
        finally:
            _kill_all(tool, [])

    def test_a_pid_that_is_another_process_s_by_now_is_not_the_tool(
        self, tmp_path, make, name
    ):
        stranger = subprocess.Popen([sys.executable, "-c", _SLEEP])
        try:
            worker = make(tmp_path)
            worker.pids = {stranger.pid}
            assert worker.tool_gone() and not worker.is_alive()
        finally:
            _kill_all(stranger, [])

    def test_without_a_pid_the_handle_is_all_there_is(self, tmp_path, make, name):
        """omc's PID could not be resolved; or the worker was closed."""
        worker = make(tmp_path)
        assert worker.pids == set() and not worker.tool_gone()
        assert worker.is_alive()
        worker._detach_interface()
        assert not worker.is_alive()


def test_a_julia_worker_whose_process_died_idle_is_restarted(tmp_path, monkeypatch):
    """Julia's worker owns its `Popen`, so its `is_alive` always asked the
    process; what the dispatch step adds is closing what the dead one left
    (its pipes) before the start replaces it."""
    from pathlib import Path

    from dstf.backends.julia import persistent_backend as jpr

    launched: list[subprocess.Popen] = []
    real_popen = subprocess.Popen

    def popen(cmd, **kw):
        launched.append(real_popen([sys.executable, "-c", _SLEEP], **kw))
        return launched[-1]

    monkeypatch.setattr(jpr.subprocess, "Popen", popen)
    monkeypatch.setattr(jpr.JuliaWorker, "_wait_for_ready", lambda self, timeout: True)
    monkeypatch.setattr(jpr.JuliaWorker, "kill_name_hint", None)  # a python stand-in
    ran: list[tuple[str, int]] = []

    def run(self, test, test_key, timeout, progress=None, before=None):
        ran.append((test.model_id, self.proc.pid))
        return _ok(test, test_key)

    monkeypatch.setattr(jpr.JuliaWorker, "run_test_with_timeout", run)
    backend = _Backend(_config(tmp_path))
    backend.quiet = True
    worker = jpr.JuliaWorker(
        0,
        backend.config,
        SimpleNamespace(julia_binary=Path("julia"), project_dir=tmp_path),
    )
    runner = WorkerRunner(backend, worker)
    try:
        worker.start()
        assert runner.run(make_test_model("Lib.A"), "test_0001").success
        first = launched[0]
        first.kill()
        first.wait()
        assert not worker.is_alive() and worker.tool_gone()

        assert runner.run(make_test_model("Lib.B"), "test_0002").success

        assert ran == [("Lib.A", first.pid), ("Lib.B", launched[1].pid)]
        assert len(launched) == 2 and worker.is_alive()
        assert first.stdin.closed and first.stdout.closed  # the dead one's pipes
    finally:
        worker.close(grace=0.5)
        for proc in launched:
            _kill_quietly(proc)


def test_a_tool_that_dies_during_what_runs_before_the_test_runs_no_test(tmp_path):
    """The watchdog gives the body up when the tool is gone; a body that
    comes back from `before` after that — the closed tool let go of it —
    must not go on to run the test."""
    tool = subprocess.Popen([sys.executable, "-c", _SLEEP])
    worker = _Worker(0, _config(tmp_path))
    ran: list[str] = []
    worker.run_test = lambda test, key, progress=None: ran.append(key)
    back = threading.Event()

    def before():
        tool.kill()
        tool.wait()
        worker.gate.wait(30)  # parked in the dead tool until it is closed
        back.set()

    try:
        worker.start()
        worker.pids = {tool.pid}
        result = worker.run_test_with_timeout(
            make_test_model(), "test_0001", 60, before=before
        )
        assert "exited during the test" in result.error_message
        assert back.wait(15)
        time.sleep(0.05)
        assert ran == []
    finally:
        _kill_quietly(tool)


def test_a_julia_worker_is_restarted_to_read_a_changed_project(tmp_path, monkeypatch):
    """Julia's worker keeps `Worker.reload`'s default: a test queued against
    source files newer than the ones its process loaded gets a new process,
    once per state, and the driver's own watched run is handed no reload."""
    from pathlib import Path

    from dstf.backends.julia import persistent_backend as jpr
    from dstf.discovery.library_state import LibraryState

    launched: list[subprocess.Popen] = []
    real_popen = subprocess.Popen

    def popen(cmd, **kw):
        launched.append(real_popen([sys.executable, "-c", _SLEEP], **kw))
        return launched[-1]

    monkeypatch.setattr(jpr.subprocess, "Popen", popen)
    monkeypatch.setattr(jpr.JuliaWorker, "_wait_for_ready", lambda self, timeout: True)
    monkeypatch.setattr(jpr.JuliaWorker, "kill_name_hint", None)  # a python stand-in
    ran: list[tuple[str, int, object]] = []

    def run(self, test, test_key, timeout, progress=None, before=None):
        ran.append((test.model_id, self.proc.pid, before))
        return _ok(test, test_key)

    monkeypatch.setattr(jpr.JuliaWorker, "run_test_with_timeout", run)
    backend = _Backend(_config(tmp_path))
    backend.quiet = True
    worker = jpr.JuliaWorker(
        0,
        backend.config,
        SimpleNamespace(julia_binary=Path("julia"), project_dir=tmp_path),
    )
    assert not worker.reloads_in_place
    runner = WorkerRunner(backend, worker)
    src = tmp_path / "src" / "Lib.jl"
    s1, s2 = LibraryState(1, {src: (1, 10)}), LibraryState(2, {src: (2, 11)})
    try:
        worker.start()
        runner.library = s1  # what the pool notes of a start
        assert runner.run(make_test_model("Lib.A"), "test_0001", s1).success
        assert runner.run(make_test_model("Lib.B"), "test_0002", s2).success
        assert runner.run(make_test_model("Lib.C"), "test_0003", s2).success

        first, second = launched
        assert ran == [
            ("Lib.A", first.pid, None),
            ("Lib.B", second.pid, None),
            ("Lib.C", second.pid, None),
        ]
        assert first.poll() is not None and worker.is_alive()
        assert worker._n_restarts == 0  # not a death: nothing is charged
    finally:
        worker.close(grace=0.5)
        for proc in launched:
            _kill_quietly(proc)


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


def test_a_driver_that_had_finished_keeps_its_result_when_cancelled_late(
    tmp_path, monkeypatch
):
    """The stop arrives after the driver exited 0 and before its run was
    read: there is nothing left to kill, and what it wrote is a result."""
    from conftest import python_example_copy
    from dstf.backends import select_backend
    from dstf.backends.common import json_driver

    config, _ = python_example_copy(tmp_path)
    quick = config.source_path / "Quick.py"
    quick.write_text(
        "def simulate(stop_time, tolerance):\n"
        "    return {'time': [0.0, 1.0], 'variables': {'x': [0.0, 1.0]}}\n"
    )
    backend = select_backend(config, persistent=True)
    backend.quiet = True
    real_run = json_driver.run_killable
    asked: list[bool] = []

    def finished_then_cancelled(cmd, **kw):
        proc = real_run(cmd, **kw)
        asked.append(backend.cancel_test("test_0001"))
        return proc

    monkeypatch.setattr(json_driver, "run_killable", finished_then_cancelled)
    test = make_test_model("Lib.Quick", source_file=quick, variable_patterns=["x"])

    result = backend.run_single_test(test, "test_0001", 1, 1)

    assert asked == [True]  # the stop was taken: its process was on record
    assert result.success and not result.cancelled, result.error_message
    assert backend._cancelled == set()


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
