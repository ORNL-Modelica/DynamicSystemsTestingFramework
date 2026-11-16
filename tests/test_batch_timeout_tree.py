"""A test's time limit, or a cancel, stops what the test started — and only that.

omc runs the compiled simulation through ``/bin/sh -c``; a timeout that
killed only omc left the shell and the simulation running, reparented to
init. These tests drive the real launch (``run_killable``, through the
backend that uses it where one is named) with a fake tool — a shell script,
a ``simulate()`` file — that starts a grandchild and then outlives its
time limit, and look for the grandchild afterwards. Every process a test
starts is written down and, if the code under test failed to stop it,
killed by its PID in a ``finally`` once its command line is checked.

Linux only: the fake tools are POSIX shell, and the kernel's reparenting
is what makes a left child invisible to its old parent.
"""

from __future__ import annotations

import dataclasses
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from conftest import make_test_model
from dstf.config import Config

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="POSIX shell fakes, Linux reparenting"
)

#: How long a fake tool and its grandchild would run if nobody stopped them.
CHILD_SECONDS = "60"
#: How long a bystander — a process that is not the test's — runs.
BYSTANDER_SECONDS = "62"


def _running(pid: int, seconds: str = CHILD_SECONDS) -> bool:
    """True while *pid* is our ``sleep <seconds>`` and runs (not a zombie)."""
    try:
        proc = psutil.Process(pid)
        return proc.status() != psutil.STATUS_ZOMBIE and proc.cmdline() == [
            "sleep",
            seconds,
        ]
    except psutil.Error:
        return False


def _recorded_pid(pid_file: Path, seconds: str = CHILD_SECONDS) -> int:
    """The PID a fake tool wrote down, once that process runs ``sleep
    <seconds>``: a shell writes ``$!`` before its forked child has become
    the ``sleep``, and a check made in between would pass for nothing."""
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        text = pid_file.read_text().strip() if pid_file.exists() else ""
        if text and _running(int(text), seconds):
            return int(text)
        time.sleep(0.02)
    raise AssertionError(f"no running sleep {seconds} at the PID in {pid_file}")


class _Watch:
    """Learns the PID in *pid_file* while that process runs ``sleep``, from
    a thread started before the call under test: once the call returns the
    process should be gone, and a PID read then would prove nothing."""

    def __init__(self, pid_file: Path, seconds: str = CHILD_SECONDS):
        self._found: list[int] = []
        self._thread = threading.Thread(
            target=self._poll, args=(pid_file, seconds), daemon=True
        )
        self._thread.start()

    def _poll(self, pid_file: Path, seconds: str) -> None:
        try:
            self._found.append(_recorded_pid(pid_file, seconds))
        except AssertionError:
            pass

    def pid(self) -> int:
        self._thread.join(10)
        assert self._found, "the fake tool's grandchild was never seen running"
        return self._found[0]


def _still_running_after(pid: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _running(pid):
            return False
        time.sleep(0.05)
    return _running(pid)


def _stop_recorded(pid_files: list[Path], tmp_path: Path) -> None:
    """Kill what a failing run left, by its recorded PID only, and only a
    process of ours: a ``sleep`` of the lengths these tests use, or one
    whose command line names a file under *tmp_path* (the fake driver).
    A PID file never written is skipped."""
    for pid_file in pid_files:
        try:
            proc = psutil.Process(int(pid_file.read_text().strip()))
            cmdline = proc.cmdline()
        except (OSError, ValueError, psutil.Error):
            continue
        ours = cmdline in (["sleep", CHILD_SECONDS], ["sleep", BYSTANDER_SECONDS])
        if ours or any(arg.startswith(str(tmp_path)) for arg in cmdline):
            try:
                proc.kill()
            except psutil.Error:
                pass


#: A grandchild under a shell, as omc's ``system()`` starts the simulation:
#: the shell writes the grandchild's PID into the test's directory.
_GRANDCHILD = f"sh -c 'sleep {CHILD_SECONDS} & echo $! > child.pid; wait'"

#: A grandchild whose shell has already exited: reparented before the time
#: limit, it is under the tool no longer, and only its directory (the
#: test's) and its birth after the tool tell it was the test's.
_DETACHED = f"sh -c 'sleep {CHILD_SECONDS} > /dev/null 2>&1 & echo $! > child.pid'"


def _outside(pid_file: Path) -> str:
    """A shell line: a shell that moves to ``/`` and stays alive over a
    grandchild there. Nothing but the walk down the live tree reaches it."""
    inner = f"cd /; sleep {CHILD_SECONDS} & echo $! > {pid_file}; wait"
    return f"sh -c {shlex.quote(inner)}"


class TestOpenModelicaBatch:
    @pytest.mark.parametrize(
        "start_child", [_GRANDCHILD, _DETACHED], ids=["under-a-shell", "detached"]
    )
    def test_a_timeout_stops_the_simulation_omc_started(self, tmp_path, start_child):
        from dstf.backends.openmodelica.backend import OpenModelicaBackend

        fake_omc = tmp_path / "omc"
        fake_omc.write_text(
            "#!/bin/sh\necho $$ > omc.pid\necho 'omc started'\n"
            f"{start_child} &\nexec sleep {CHILD_SECONDS}\n"
        )
        fake_omc.chmod(0o755)
        (tmp_path / "package.mo").write_text("package Lib end Lib;")
        cfg = Config(
            source_path=tmp_path,
            reference_root=tmp_path / "refs",
            backend_name="OpenModelica",
            work_dir=tmp_path / "work",
        )
        backend = OpenModelicaBackend(cfg)
        backend.om_config = dataclasses.replace(
            backend.om_config, omc_path=str(fake_omc)
        )
        test = make_test_model("Lib.Slow", timeout=2)
        test_dir = tmp_path / "work" / "test_0001"
        pid_file = test_dir / "child.pid"
        try:
            watch = _Watch(pid_file)
            started = time.monotonic()
            result = backend.run_single_test(test, "test_0001", 1, 1)
            took = time.monotonic() - started
            child = watch.pid()

            assert result.timed_out is True
            assert "omc exceeded 2.0s timeout" in (result.error_message or "")
            assert (test_dir / "omc_stdout.txt").read_text() == (
                "omc started\n\n[TimeoutExpired]\n"
            )
            assert took < 15
            assert not _still_running_after(child, 3.0), (
                f"pid {child} (sleep {CHILD_SECONDS}) outlived the timeout"
            )
        finally:
            _stop_recorded([pid_file, test_dir / "omc.pid"], tmp_path)


def _python_backend(tmp_path: Path, simulate_body: str, timeout: float):
    from dstf.backends.python.backend import PythonBackend

    user = tmp_path / "slow.py"
    # The grandchild's output goes nowhere: one that held the driver's
    # pipes would keep a bare kill's drain waiting for it to end.
    user.write_text(
        "import os, subprocess, time\n"
        "def simulate(stop_time, tolerance):\n"
        "    open('driver.pid', 'w').write(str(os.getpid()))\n"
        f"    subprocess.Popen({simulate_body!r}, shell=True,\n"
        "        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        f"    time.sleep({CHILD_SECONDS})\n"
    )
    cfg = SimpleNamespace(
        backend_path=sys.executable,
        source_path=tmp_path,
        work_dir=tmp_path / "work",
        timeout=timeout,
        diagnostic_variables=[],
    )
    return PythonBackend(cfg), make_test_model("Slow", source_file=user)


class TestPythonBatch:
    def test_a_timeout_stops_what_simulate_started(self, tmp_path):
        backend, test = _python_backend(tmp_path, _GRANDCHILD, timeout=2)
        test_dir = tmp_path / "work" / "test_0001"
        pid_file = test_dir / "child.pid"
        try:
            watch = _Watch(pid_file)
            result = backend.run_single_test(test, "test_0001", 1, 1)
            child = watch.pid()

            assert result.timed_out is True
            assert result.error_message is not None
            assert "Python execution exceeded 2.0s timeout" in result.error_message
            assert not _still_running_after(child, 3.0), (
                f"pid {child} (sleep {CHILD_SECONDS}) outlived the timeout"
            )
        finally:
            _stop_recorded([pid_file, test_dir / "driver.pid"], tmp_path)

    @pytest.mark.parametrize("left", ["outside-the-folder", "detached-in-the-folder"])
    def test_a_cancel_stops_what_simulate_started(self, tmp_path, left):
        """The author tool's cancel kills the driver's whole tree — also a
        grandchild that left the test's folder — and the launch then
        sweeps the folder for one no longer under the driver."""
        test_dir = tmp_path / "work" / "test_0001"
        if left == "outside-the-folder":
            # The whole chain under the driver leaves the folder, so that
            # no sweep of it can stand in for the cancel's own tree kill.
            pid_file = tmp_path / "child.pid"
            start_child = f"cd /; {_outside(pid_file)}"
        else:
            pid_file = test_dir / "child.pid"
            start_child = _DETACHED
        backend, test = _python_backend(tmp_path, start_child, timeout=30)
        results: list = []
        runner = threading.Thread(
            target=lambda: results.append(
                backend.run_single_test(test, "test_0001", 1, 1)
            )
        )
        try:
            runner.start()
            child = _recorded_pid(pid_file)

            assert backend.cancel_test("test_0001") is True
            assert not _still_running_after(child, 3.0), (
                f"pid {child} (sleep {CHILD_SECONDS}) outlived the cancel"
            )
            runner.join(15)
            assert [r.cancelled for r in results] == [True]
        finally:
            _stop_recorded([pid_file, test_dir / "driver.pid"], tmp_path)
            runner.join(15)


def _detached_in(directory: Path, pid_file: Path) -> int:
    """A ``sleep`` in *directory* whose parent has already exited — adopted
    by a reaper, as a test's leftover is — but which is no test's."""
    directory.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "sh",
            "-c",
            f"(cd {shlex.quote(str(directory))} && exec sleep {BYSTANDER_SECONDS})"
            f" > /dev/null 2>&1 & echo $! > {shlex.quote(str(pid_file))}",
        ],
        check=True,
    )
    return _recorded_pid(pid_file, BYSTANDER_SECONDS)


class TestOnlyTheTestsProcessesAreStopped:
    """The sweep of the test's folder takes what the test's tool started,
    never a process that merely sits there: a person reading a log in that
    folder from another terminal keeps their shell."""

    @pytest.mark.parametrize(
        "where",
        ["reparented-in-the-folder", "child-of-this-process", "sibling-folder"],
    )
    def test_a_bystander_started_after_the_tool_survives_its_timeout(
        self, tmp_path, where
    ):
        from dstf.backends.common.proc_kill import run_killable

        test_dir = tmp_path / "work" / "test_0001"
        test_dir.mkdir(parents=True)
        pid_file = tmp_path / "bystander.pid"
        tool_pid = tmp_path / "tool.pid"
        bystanders: list[subprocess.Popen] = []
        seen: list[int] = []

        def start_bystander(proc, *_born):
            tool_pid.write_text(str(proc.pid))
            if where == "child-of-this-process":
                child = subprocess.Popen(
                    [
                        "sh",
                        "-c",
                        f"cd {shlex.quote(str(test_dir))} "
                        f"&& exec sleep {BYSTANDER_SECONDS}",
                    ]
                )
                bystanders.append(child)
                pid_file.write_text(str(child.pid))
                seen.append(_recorded_pid(pid_file, BYSTANDER_SECONDS))
            else:
                folder = (
                    test_dir
                    if where.endswith("the-folder")
                    else (tmp_path / "work" / "test_0002")
                )
                seen.append(_detached_in(folder, pid_file))

        try:
            with pytest.raises(subprocess.TimeoutExpired):
                run_killable(
                    ["sleep", CHILD_SECONDS],
                    cwd=test_dir,
                    timeout=1.5,
                    on_spawn=start_bystander,
                )
            (bystander,) = seen
            time.sleep(0.5)

            assert _running(bystander, BYSTANDER_SECONDS), (
                f"the bystander {bystander} ({where}) was killed"
            )
        finally:
            _stop_recorded([pid_file, tool_pid], tmp_path)
            for child in bystanders:
                child.wait(5)

    def test_a_process_in_the_folder_born_before_the_tool_survives(self, tmp_path):
        from dstf.backends.common.proc_kill import run_killable

        test_dir = tmp_path / "work" / "test_0001"
        pid_file = tmp_path / "bystander.pid"
        tool_pid = tmp_path / "tool.pid"
        bystander = _detached_in(test_dir, pid_file)
        time.sleep(0.1)  # born a clock tick before the tool, at least
        try:
            with pytest.raises(subprocess.TimeoutExpired):
                run_killable(
                    ["sleep", CHILD_SECONDS],
                    cwd=test_dir,
                    timeout=1.5,
                    on_spawn=lambda proc, *_: tool_pid.write_text(str(proc.pid)),
                )
            time.sleep(0.5)

            assert _running(bystander, BYSTANDER_SECONDS)
        finally:
            _stop_recorded([pid_file, tool_pid], tmp_path)


class TestWhatTheStopReaches:
    def test_a_grandchild_under_a_live_shell_outside_the_folder(self, tmp_path):
        """Only the walk down the tool's live tree finds it: its shell left
        the test's folder and is not yet anyone's orphan."""
        from dstf.backends.common.proc_kill import run_killable

        test_dir = tmp_path / "work" / "test_0001"
        test_dir.mkdir(parents=True)
        pid_file = tmp_path / "child.pid"
        tool_pid = tmp_path / "tool.pid"
        try:
            watch = _Watch(pid_file)
            with pytest.raises(subprocess.TimeoutExpired):
                run_killable(
                    [
                        "sh",
                        "-c",
                        f"{_outside(pid_file)} & exec sleep {CHILD_SECONDS}",
                    ],
                    cwd=test_dir,
                    timeout=1.5,
                    on_spawn=lambda proc, *_: tool_pid.write_text(str(proc.pid)),
                )
            child = watch.pid()

            assert not _still_running_after(child, 3.0)
        finally:
            _stop_recorded([pid_file, tool_pid], tmp_path)

    def test_an_interrupt_stops_the_tree_and_still_propagates(self, tmp_path):
        from dstf.backends.common.proc_kill import run_killable

        test_dir = tmp_path / "work" / "test_0001"
        test_dir.mkdir(parents=True)
        pid_file = tmp_path / "child.pid"
        tool_pid = tmp_path / "tool.pid"
        previous = signal.signal(signal.SIGINT, signal.default_int_handler)
        interrupt = threading.Timer(1.5, os.kill, (os.getpid(), signal.SIGINT))
        try:
            watch = _Watch(pid_file)
            with pytest.raises(KeyboardInterrupt):
                run_killable(
                    [
                        "sh",
                        "-c",
                        f"{_outside(pid_file)} & exec sleep {CHILD_SECONDS}",
                    ],
                    cwd=test_dir,
                    timeout=30,
                    on_spawn=lambda proc, *_: (
                        tool_pid.write_text(str(proc.pid)),
                        interrupt.start(),
                    ),
                )
            child = watch.pid()

            assert not _still_running_after(child, 3.0)
        finally:
            interrupt.cancel()
            signal.signal(signal.SIGINT, previous)
            _stop_recorded([pid_file, tool_pid], tmp_path)

    def test_a_leftover_holding_the_output_does_not_hold_the_run(self, tmp_path):
        """A process that escaped the stop — out of the tree and the folder —
        and still holds the tool's stdout: the read after the kill is
        bounded, and what the tool wrote is kept."""
        from dstf.backends.common.proc_kill import run_killable
        from dstf.backends.common.proc_output import decode_output

        test_dir = tmp_path / "work" / "test_0001"
        test_dir.mkdir(parents=True)
        pid_file = tmp_path / "escaped.pid"
        tool_pid = tmp_path / "tool.pid"
        script = (
            f"( (cd / && exec sleep {CHILD_SECONDS}) & echo $! > {pid_file} ); "
            f"echo partial-output; exec sleep {CHILD_SECONDS}"
        )
        try:
            started = time.monotonic()
            with pytest.raises(subprocess.TimeoutExpired) as raised:
                run_killable(
                    ["sh", "-c", script],
                    cwd=test_dir,
                    timeout=1.5,
                    on_spawn=lambda proc, *_: tool_pid.write_text(str(proc.pid)),
                )
            took = time.monotonic() - started

            assert took < 20
            assert decode_output(raised.value.stdout) == "partial-output\n"
        finally:
            _stop_recorded([pid_file, tool_pid], tmp_path)
