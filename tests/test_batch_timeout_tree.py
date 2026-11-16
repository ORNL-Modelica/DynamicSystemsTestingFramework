"""A batch test's time limit stops everything the test started.

omc runs the compiled simulation through ``/bin/sh -c``; a timeout that
killed only omc left the shell and the simulation running, reparented to
init. These tests drive the real batch path of a backend with a fake tool
(a shell script, a ``simulate()`` file) that starts a grandchild and then
outlives its time limit, and look for the grandchild afterwards.

Linux only: the fake tools are POSIX shell, and the kernel's reparenting
is what makes a left child invisible to its old parent.
"""

from __future__ import annotations

import dataclasses
import sys
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

#: How long a grandchild of the fake tool would run if nobody stopped it.
CHILD_SECONDS = "60"


def _recorded_child(pid_file: Path) -> int:
    """The PID of the grandchild the fake tool wrote down."""
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        text = pid_file.read_text().strip() if pid_file.exists() else ""
        if text:
            return int(text)
        time.sleep(0.05)
    raise AssertionError(f"the fake tool never wrote {pid_file}")


def _running(pid: int) -> bool:
    """True while *pid* is our grandchild and runs (not a zombie)."""
    try:
        proc = psutil.Process(pid)
        return proc.status() != psutil.STATUS_ZOMBIE and proc.cmdline() == [
            "sleep",
            CHILD_SECONDS,
        ]
    except psutil.Error:
        return False


def _still_running_after(pid: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _running(pid):
            return False
        time.sleep(0.05)
    return _running(pid)


def _stop_leftover(pid_file: Path) -> None:
    """Kill the grandchild a failing run left, by its recorded PID only."""
    if not pid_file.exists() or not pid_file.read_text().strip():
        return
    try:
        proc = psutil.Process(int(pid_file.read_text().strip()))
        if proc.cmdline() == ["sleep", CHILD_SECONDS]:
            proc.kill()
    except (psutil.Error, ValueError):
        pass


#: A grandchild under a shell, as omc's ``system()`` starts the simulation:
#: the shell writes the grandchild's PID into the test's directory.
_GRANDCHILD = f"sh -c 'sleep {CHILD_SECONDS} & echo $! > child.pid; wait'"

#: A grandchild whose shell has already exited: reparented before the time
#: limit, it is under the tool no longer, and only its directory (the
#: test's) and its birth after the tool tell it was the test's.
_DETACHED = f"sh -c 'sleep {CHILD_SECONDS} > /dev/null 2>&1 & echo $! > child.pid'"


class TestOpenModelicaBatch:
    @pytest.mark.parametrize(
        "start_child", [_GRANDCHILD, _DETACHED], ids=["under-a-shell", "detached"]
    )
    def test_a_timeout_stops_the_simulation_omc_started(self, tmp_path, start_child):
        from dstf.backends.openmodelica.backend import OpenModelicaBackend

        fake_omc = tmp_path / "omc"
        fake_omc.write_text(
            f"#!/bin/sh\necho 'omc started'\n{start_child} &\nexec sleep 60\n"
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
            started = time.monotonic()
            result = backend.run_single_test(test, "test_0001", 1, 1)
            took = time.monotonic() - started
            child = _recorded_child(pid_file)

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
            _stop_leftover(pid_file)


class TestPythonBatch:
    def test_a_timeout_stops_what_simulate_started(self, tmp_path):
        from dstf.backends.python.backend import PythonBackend

        user = tmp_path / "slow.py"
        # The grandchild's output goes nowhere: one that held the driver's
        # pipes would keep a bare kill's drain waiting for it to end.
        user.write_text(
            "import subprocess, time\n"
            "def simulate(stop_time, tolerance):\n"
            f"    subprocess.Popen({_GRANDCHILD!r}, shell=True,\n"
            "        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
            "    time.sleep(60)\n"
        )
        cfg = SimpleNamespace(
            backend_path=sys.executable,
            source_path=tmp_path,
            work_dir=tmp_path / "work",
            timeout=2,
            diagnostic_variables=[],
        )
        backend = PythonBackend(cfg)
        test = make_test_model("Slow", source_file=user)
        test_dir = tmp_path / "work" / "test_0001"
        pid_file = test_dir / "child.pid"
        try:
            result = backend.run_single_test(test, "test_0001", 1, 1)
            child = _recorded_child(pid_file)

            assert result.timed_out is True
            assert result.error_message is not None
            assert "Python execution exceeded 2.0s timeout" in result.error_message
            assert not _still_running_after(child, 3.0), (
                f"pid {child} (sleep {CHILD_SECONDS}) outlived the timeout"
            )
        finally:
            _stop_leftover(pid_file)
