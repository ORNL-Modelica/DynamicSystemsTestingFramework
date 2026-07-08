"""Regression tests for the 2026-07-06 code-review backend fixes.

Covers findings 20-29 and 73-75 (CODE_REVIEW_2026-07-06.md, Theme 3 +
mat-reader/serialization items). Each test class names the finding it
pins down. Fake Workers / fake subprocesses follow the patterns in
test_openmodelica_persistent.py / test_openmodelica_runner.py.

The Julia driver (.jl) changes cannot be executed here (no julia binary
on this machine); the driver-content tests at the bottom pin the decision
textually so a regression at least trips something.
"""

from __future__ import annotations

import json
import logging
import os
import struct
import subprocess
import sys
import threading
import time
import types
from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from dstf.discovery.test_registry import TestModel
from dstf.simulators.base import (
    Capability,
    PersistentRunnerBase,
    TestRunResult,
    Worker,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
JULIA_DRIVER_DIR = PROJECT_ROOT / "src" / "dstf" / "simulators" / "julia"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _tm(model_id: str = "Lib.T", **kw) -> TestModel:
    parts = model_id.rsplit(".", 1)
    defaults = dict(
        model_id=model_id,
        source_file=Path(""),
        source_package=parts[0] if len(parts) > 1 else "",
        short_name=parts[-1],
        n_vars=0,
        source="spec",
        # Explicit sim fields: direct-constructed TestModels bypass
        # discovery's default resolution (finding 13 made the dataclass
        # defaults None-sentinels), and runners need concrete values.
        stop_time=1.0,
        tolerance=1e-6,
        method="Dassl",
    )
    defaults.update(kw)
    return TestModel(**defaults)


def _write_mat4(path: Path, blocks: list[tuple[str, np.ndarray, int]]) -> None:
    """Write a minimal MAT4 file: list of (name, 2D array, mopt)."""
    dtype_by_p = {0: "<f8", 2: "<i4", 5: "<u1"}
    with open(path, "wb") as f:
        for name, arr, mopt in blocks:
            arr = np.asarray(arr)
            mrows, ncols = arr.shape
            name_bytes = name.encode("ascii") + b"\x00"
            f.write(struct.pack("<5i", mopt, mrows, ncols, 0, len(name_bytes)))
            f.write(name_bytes)
            dt = dtype_by_p[(mopt % 100) // 10]
            f.write(arr.astype(dt).tobytes(order="F"))


def _char_matrix(strings: list[str]) -> np.ndarray:
    """Char-code matrix stored (max_len, n_strings) — the on-disk binTrans
    orientation the reader transposes back with ``.T``."""
    max_len = max(len(s) for s in strings)
    m = np.zeros((max_len, len(strings)), dtype=np.uint8)
    for j, s in enumerate(strings):
        for i, ch in enumerate(s):
            m[i, j] = ord(ch)
    return m


def _write_dsres_mat(
    path: Path,
    aclass_kind: str = "binTrans",
    y_data_col: int = 0,
) -> None:
    """Synthesize a tiny DSresult mat: variables time, x, y.

    ``y_data_col`` is y's dataInfo column entry — 0 reproduces finding 74's
    wrap-to-last-row bug; a positive value makes y a normal variable.
    """
    n = 5
    t = np.linspace(0.0, 1.0, n)
    x = 2.0 * t
    data_2 = np.vstack([t, x])  # rows: 0=time, 1=x
    # dataInfo rows per var: (matrix_idx, col, interp, protected)
    info_rows = np.array(
        [
            [0, 1, 0, -1],  # time
            [2, 2, 0, -1],  # x → data_2 col index 2 → row 1
            [2, y_data_col, 0, -1],  # y
        ],
        dtype=np.int32,
    )
    _write_mat4(
        path,
        [
            ("Aclass", _char_matrix(["Atrajectory", "1.1", "", aclass_kind]), 51),
            ("name", _char_matrix(["time", "x", "y"]), 51),
            ("dataInfo", info_rows.T, 20),  # on-disk (4, n_vars)
            ("data_2", data_2, 0),
        ],
    )


# ---------------------------------------------------------------------------
# Finding 23 — shared subprocess-output decode helper
# ---------------------------------------------------------------------------


class TestDecodeOutput:
    def test_none_becomes_empty(self):
        from dstf.simulators.common.proc_output import decode_output

        assert decode_output(None) == ""

    def test_bytes_decoded_utf8(self):
        from dstf.simulators.common.proc_output import decode_output

        assert decode_output("héllo".encode()) == "héllo"

    def test_invalid_bytes_replaced_not_raised(self):
        from dstf.simulators.common.proc_output import decode_output

        out = decode_output(b"ok \xff\xfe bad")
        assert "ok" in out and "bad" in out

    def test_str_passthrough(self):
        from dstf.simulators.common.proc_output import decode_output

        assert decode_output("already text") == "already text"


class TestTimeoutExpiredBytesHandling:
    """Finding 23: TimeoutExpired.stdout/.stderr are bytes even with
    text=True; the timeout handlers must not TypeError on them."""

    def test_julia_runner_timeout_with_bytes_output(self, tmp_path, monkeypatch):
        from dstf.simulators.julia import runner as jr

        fake_julia = tmp_path / "julia"
        fake_julia.write_text("#!/bin/sh\n")
        user = tmp_path / "T.jl"
        user.write_text("# stub\n")
        cfg = SimpleNamespace(
            simulator_path=str(fake_julia),
            source_path=tmp_path,
            work_dir=tmp_path / "work",
            timeout=1,
        )
        runner = jr.JuliaRunner(cfg)

        def fake_run(*a, **kw):
            raise subprocess.TimeoutExpired(
                cmd=["julia"], timeout=1, output=b"partial out", stderr=b"partial err"
            )

        monkeypatch.setattr(jr.subprocess, "run", fake_run)
        test = _tm("T", source_file=user, variable_patterns=["x"])
        rr = runner.run_single_test(test, "test_0001", 1, 1)
        assert rr.success is False
        assert rr.timed_out is True
        stdout_txt = (tmp_path / "work" / "test_0001" / "julia_stdout.txt").read_text()
        assert "partial out" in stdout_txt

    def test_python_runner_timeout_with_bytes_output(self, tmp_path, monkeypatch):
        from dstf.simulators.python import runner as pr

        user = tmp_path / "T.py"
        user.write_text("# stub\n")
        cfg = SimpleNamespace(
            simulator_path=None,
            source_path=tmp_path,
            work_dir=tmp_path / "work",
            timeout=1,
        )
        runner = pr.PythonRunner(cfg)

        def fake_run(*a, **kw):
            raise subprocess.TimeoutExpired(
                cmd=["python"], timeout=1, output=b"po", stderr=b"pe"
            )

        monkeypatch.setattr(pr.subprocess, "run", fake_run)
        test = _tm("T", source_file=user, variable_patterns=["x"])
        rr = runner.run_single_test(test, "test_0001", 1, 1)
        assert rr.success is False
        assert rr.timed_out is True
        assert (
            "po" in (tmp_path / "work" / "test_0001" / "python_stdout.txt").read_text()
        )

    def test_openmodelica_runner_timeout_with_bytes_output(self, tmp_path, monkeypatch):
        from dstf.config import Config
        from dstf.simulators.openmodelica.runner import OpenModelicaRunner

        (tmp_path / "package.mo").write_text("package Lib end Lib;")
        cfg = Config(
            source_path=tmp_path,
            reference_root=tmp_path / "refs",
            simulator="OpenModelica",
            work_dir=tmp_path / "work",
        )
        runner = OpenModelicaRunner(cfg)

        def fake_run(*a, **kw):
            raise subprocess.TimeoutExpired(
                cmd=["omc"], timeout=1, output=b"omc partial", stderr=None
            )

        monkeypatch.setattr(
            "dstf.simulators.openmodelica.runner.subprocess.run", fake_run
        )
        test = _tm("Lib.A", variable_patterns=["x"])
        rr = runner.run_single_test(test, "test_0001", 1, 1)
        assert rr.success is False
        assert rr.timed_out is True
        stdout_txt = (tmp_path / "work" / "test_0001" / "omc_stdout.txt").read_text()
        assert "omc partial" in stdout_txt
        assert "[TimeoutExpired]" in stdout_txt


# ---------------------------------------------------------------------------
# Finding 75 — whitespace-only stderr tail must not IndexError
# ---------------------------------------------------------------------------


class TestStderrTailGuard:
    def test_julia_whitespace_only_stderr(self, tmp_path, monkeypatch):
        from dstf.simulators.julia import runner as jr

        fake_julia = tmp_path / "julia"
        fake_julia.write_text("#!/bin/sh\n")
        user = tmp_path / "T.jl"
        user.write_text("# stub\n")
        cfg = SimpleNamespace(
            simulator_path=str(fake_julia),
            source_path=tmp_path,
            work_dir=tmp_path / "work",
            timeout=1,
        )
        runner = jr.JuliaRunner(cfg)
        monkeypatch.setattr(
            jr.subprocess,
            "run",
            lambda *a, **kw: CompletedProcess(
                args=a, returncode=1, stdout="", stderr="   \n\t\n"
            ),
        )
        test = _tm("T", source_file=user, variable_patterns=["x"])
        rr = runner.run_single_test(test, "test_0001", 1, 1)
        assert rr.success is False
        assert "returned 1" in (rr.error_message or "")

    def test_python_whitespace_only_stderr(self, tmp_path, monkeypatch):
        from dstf.simulators.python import runner as pr

        user = tmp_path / "T.py"
        user.write_text("# stub\n")
        cfg = SimpleNamespace(
            simulator_path=None,
            source_path=tmp_path,
            work_dir=tmp_path / "work",
            timeout=1,
        )
        runner = pr.PythonRunner(cfg)
        monkeypatch.setattr(
            pr.subprocess,
            "run",
            lambda *a, **kw: CompletedProcess(
                args=a, returncode=1, stdout="", stderr="  \n \n"
            ),
        )
        test = _tm("T", source_file=user, variable_patterns=["x"])
        rr = runner.run_single_test(test, "test_0001", 1, 1)
        assert rr.success is False
        assert "returned 1" in (rr.error_message or "")


# ---------------------------------------------------------------------------
# Finding 74 — mat_reader Aclass + dataInfo column-0 guards
# ---------------------------------------------------------------------------


class TestMatReaderGuards:
    def test_binnormal_layout_refused(self, tmp_path, caplog):
        from dstf.simulators.common.mat_reader import read_result_mat

        mat = tmp_path / "res.mat"
        _write_dsres_mat(mat, aclass_kind="binNormal", y_data_col=2)
        with caplog.at_level(logging.ERROR, logger="dstf.simulators.common.mat_reader"):
            result = read_result_mat(mat)
        assert result is None
        assert "binNormal" in caplog.text

    def test_binnormal_layout_refused_in_name_listing(self, tmp_path, caplog):
        from dstf.simulators.common.mat_reader import list_result_mat_variables

        mat = tmp_path / "res.mat"
        _write_dsres_mat(mat, aclass_kind="binNormal", y_data_col=2)
        with caplog.at_level(logging.ERROR, logger="dstf.simulators.common.mat_reader"):
            names = list_result_mat_variables(mat)
        assert names is None
        assert "binNormal" in caplog.text

    def test_bintrans_still_reads_normally(self, tmp_path):
        from dstf.simulators.common.mat_reader import read_result_mat

        mat = tmp_path / "res.mat"
        _write_dsres_mat(mat, aclass_kind="binTrans", y_data_col=2)
        result = read_result_mat(mat)
        assert result is not None
        assert "x" in result and "y" in result
        _, x_vals = result["x"]
        np.testing.assert_allclose(x_vals, 2.0 * np.linspace(0.0, 1.0, 5))

    def test_datainfo_column_zero_skips_variable(self, tmp_path):
        """col 0 used to become index -1 → last row's data (the WRONG
        variable) returned under the requested name."""
        from dstf.simulators.common.mat_reader import read_result_mat

        mat = tmp_path / "res.mat"
        _write_dsres_mat(mat, aclass_kind="binTrans", y_data_col=0)
        result = read_result_mat(mat)
        assert result is not None
        assert "x" in result
        assert "y" not in result  # skipped, not silently wrong

    def test_datainfo_column_zero_with_selection(self, tmp_path):
        from dstf.simulators.common.mat_reader import read_result_mat

        mat = tmp_path / "res.mat"
        _write_dsres_mat(mat, aclass_kind="binTrans", y_data_col=0)
        result = read_result_mat(mat, variable_names={"x", "y"})
        assert result is not None
        assert "x" in result
        assert "y" not in result


# ---------------------------------------------------------------------------
# Finding 28 — capability-honesty check must compare classmethod __func__
# ---------------------------------------------------------------------------


class TestCapabilityHonesty:
    def test_persistent_workers_without_override_trips(self):
        from dstf.simulators import _REGISTRY, register
        from dstf.simulators.base import SimulatorRunner

        try:
            with pytest.raises(TypeError, match="PERSISTENT_WORKERS"):

                @register("BogusPersistentBackend")
                class Bogus(SimulatorRunner):
                    capabilities = frozenset({Capability.PERSISTENT_WORKERS})

                    def read_result(self, test, test_key, run_result):
                        raise NotImplementedError

        finally:
            _REGISTRY.pop("BogusPersistentBackend", None)

    def test_persistent_workers_with_override_passes(self):
        from dstf.simulators import _REGISTRY, register
        from dstf.simulators.base import SimulatorRunner

        try:

            @register("HonestPersistentBackend")
            class Honest(SimulatorRunner):
                capabilities = frozenset({Capability.PERSISTENT_WORKERS})

                @classmethod
                def persistent_runner_cls(cls):
                    return cls

                def read_result(self, test, test_key, run_result):
                    raise NotImplementedError

            assert _REGISTRY["HonestPersistentBackend"] is Honest
        finally:
            _REGISTRY.pop("HonestPersistentBackend", None)

    def test_fmpy_runner_dropped_aspirational_persistent_flag(self):
        """FmpyRunner has no persistent runner; the flag was aspirational
        and now trips the (repaired) honesty check."""
        from dstf.simulators.fmpy.runner import FmpyRunner

        assert Capability.PERSISTENT_WORKERS not in FmpyRunner.capabilities
        assert FmpyRunner.persistent_runner_cls() is None


# ---------------------------------------------------------------------------
# Finding 73 — FMPy timeout flag + save inside try
# ---------------------------------------------------------------------------


def _install_fake_fmpy(monkeypatch, simulate_fn):
    fake = types.ModuleType("fmpy")
    fake.read_model_description = lambda p: SimpleNamespace(
        modelVariables=[SimpleNamespace(name="h")]
    )
    fake.simulate_fmu = simulate_fn
    monkeypatch.setitem(sys.modules, "fmpy", fake)


class TestFmpyRunnerFixes:
    def _make_runner(self, tmp_path, monkeypatch, simulate_fn):
        _install_fake_fmpy(monkeypatch, simulate_fn)
        from dstf.simulators.fmpy.runner import FmpyRunner

        cfg = SimpleNamespace(
            work_dir=tmp_path / "work",
            source_type="fmu",
            simulator="FMPy",
            parallel=1,
            timeout=60,
        )
        return FmpyRunner(cfg)

    def _make_fmu_test(self, tmp_path, **kw):
        fmu = tmp_path / "Model.fmu"
        fmu.write_bytes(b"fake fmu")
        return _tm("Model", source_file=fmu, variable_patterns=["h"], **kw)

    def test_timeout_result_sets_timed_out_flag(self, tmp_path, monkeypatch):
        def slow_sim(**kw):
            time.sleep(0.5)
            return None

        runner = self._make_runner(tmp_path, monkeypatch, slow_sim)
        test = self._make_fmu_test(tmp_path, timeout=1)
        test.timeout = None
        runner.config.timeout = 0.05
        rr = runner.run_single_test(test, "test_0001", 1, 1)
        assert rr.success is False
        assert (
            rr.timed_out is True
        )  # finding 73: was False → dashboard/console disagreed

    def test_save_oserror_fails_single_test_not_run(self, tmp_path, monkeypatch):
        runner = self._make_runner(tmp_path, monkeypatch, lambda **kw: object())
        monkeypatch.setattr(
            "dstf.simulators.fmpy.runner._save_result",
            lambda path, arr: (_ for _ in ()).throw(OSError("disk full")),
        )
        test = self._make_fmu_test(tmp_path)
        rr = runner.run_single_test(test, "test_0001", 1, 1)  # must NOT raise
        assert rr.success is False
        assert "disk full" in (rr.error_message or "")


# ---------------------------------------------------------------------------
# Finding 29 — non-numeric driver output: driver + runner sides
# ---------------------------------------------------------------------------

_PY_DRIVER = PROJECT_ROOT / "src" / "dstf" / "simulators" / "python" / "run_test.py"


class TestPythonNonNumericValues:
    def test_driver_rejects_non_numeric_values(self, tmp_path):
        import shutil as _sh

        user = tmp_path / "bad_types.py"
        user.write_text(
            "def simulate(stop_time, tolerance):\n"
            "    return {'time': [0.0, 1.0], 'variables': {'x': ['a', 'b']}}\n"
        )
        result = tmp_path / "result.json"
        proc = subprocess.run(
            [
                _sh.which("python") or "python3",
                str(_PY_DRIVER),
                str(user),
                "1.0",
                "1e-6",
                str(result),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode != 0
        payload = json.loads(result.read_text())
        assert payload["success"] is False
        assert "'x'" in payload["error"]
        assert "non-numeric" in payload["error"]

    def test_driver_rejects_non_numeric_time(self, tmp_path):
        import shutil as _sh

        user = tmp_path / "bad_time.py"
        user.write_text(
            "def simulate(stop_time, tolerance):\n"
            "    return {'time': ['zero'], 'variables': {'x': [1.0]}}\n"
        )
        result = tmp_path / "result.json"
        proc = subprocess.run(
            [
                _sh.which("python") or "python3",
                str(_PY_DRIVER),
                str(user),
                "1.0",
                "1e-6",
                str(result),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode != 0
        payload = json.loads(result.read_text())
        assert payload["success"] is False
        assert "time" in payload["error"]

    def test_read_result_turns_non_numeric_into_failed_test(self, tmp_path):
        """Runner-side belt: a hand-tampered/legacy result.json with string
        values must fail the one test, not abort the whole read phase."""
        from dstf.simulators.python import runner as pr

        cfg = SimpleNamespace(
            simulator_path=None,
            source_path=tmp_path,
            work_dir=tmp_path / "work",
            timeout=1,
        )
        runner = pr.PythonRunner(cfg)
        test_dir = tmp_path / "work" / "test_0001"
        test_dir.mkdir(parents=True)
        (test_dir / "result.json").write_text(
            json.dumps(
                {
                    "success": True,
                    "time": [0.0, 1.0],
                    "variables": {"x": ["not", "numbers"]},
                }
            )
        )
        test = _tm("T", variable_patterns=["x"])
        res = runner.read_result(test, "test_0001", None)
        assert res.success is False
        assert "x" in (res.error_message or "")


# ---------------------------------------------------------------------------
# Finding 25 — progress snapshot taken inside the write lock
# ---------------------------------------------------------------------------


class TestProgressSnapshotUnderLock:
    def test_snapshot_taken_while_write_lock_held(self, tmp_path, monkeypatch):
        from dstf.simulators.progress import ProgressReporter

        pr = ProgressReporter(tmp_path, total=1)
        monkeypatch.setattr(pr, "_render_dashboard", lambda mode: None)
        held: list[bool] = []
        orig = pr._snapshot

        def spy():
            held.append(pr._write_lock.locked())
            return orig()

        monkeypatch.setattr(pr, "_snapshot", spy)
        pr.register("t1", "M.A")
        assert held and all(held)

    def test_written_snapshots_never_regress_under_threads(self, tmp_path, monkeypatch):
        from dstf.simulators.progress import ProgressReporter

        n = 40
        pr = ProgressReporter(tmp_path, total=n)
        monkeypatch.setattr(pr, "_render_dashboard", lambda mode: None)
        written: list[dict] = []
        monkeypatch.setattr(
            pr, "_write_json", lambda snapshot: written.append(snapshot)
        )

        for i in range(n):
            pr.register(f"t{i}", f"M.T{i}")

        def work(i: int):
            pr.on_start(f"t{i}", worker_id=0)
            pr.on_finish(f"t{i}", success=True, elapsed=0.001)

        threads = [threading.Thread(target=work, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        done = [
            s["counts"]["passed"] + s["counts"]["failed"] + s["counts"]["timed_out"]
            for s in written
        ]
        # With snapshot-in-lock, writes are serialized snapshots — a stale
        # snapshot can never overwrite a newer one.
        assert done == sorted(done)
        assert written[-1]["counts"]["passed"] == n


# ---------------------------------------------------------------------------
# Findings 20 + 21 + 24 — persistent dispatch loop lifecycle
# ---------------------------------------------------------------------------


class _StubRunner(PersistentRunnerBase):
    backend_label = "Stub"

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


def _stub_runner(tmp_path) -> _StubRunner:
    cfg = SimpleNamespace(timeout=5.0, parallel=2, work_dir=tmp_path)
    return _StubRunner(cfg)


class TestDispatchDeadWorker:
    def test_dead_worker_exits_thread_instead_of_draining_queue(self, tmp_path):
        """Finding 20: the dead worker fail-marks ONLY its held item and
        exits; the healthy worker runs everything else."""
        runner = _stub_runner(tmp_path)
        dead = _DeadWorker(0, runner.config)
        good = _GoodWorker(1, runner.config, delay=0.05)
        items = [(_tm(f"Lib.T{i}"), f"test_{i:04d}") for i in range(6)]

        results = runner._dispatch_with_restart([dead, good], items, total=6)

        assert len(results) == 6
        failures = [r for r in results if not r.success]
        assert len(failures) == 1
        assert "restart exhausted" in (failures[0].error_message or "")
        assert len(good.ran) == 5

    def test_last_dead_worker_drains_queue_without_hanging(self, tmp_path):
        """Finding 20: with no live worker left, the queue is drained and
        every remaining test still gets a recorded result."""
        runner = _stub_runner(tmp_path)
        dead = _DeadWorker(0, runner.config)
        items = [(_tm(f"Lib.T{i}"), f"test_{i:04d}") for i in range(4)]

        box: dict = {}

        def run():
            box["results"] = runner._dispatch_with_restart([dead], items, total=4)

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
        runner = _stub_runner(tmp_path)
        w = _RaiseOnceWorker(0, runner.config)
        items = [(_tm(f"Lib.T{i}"), f"test_{i:04d}") for i in range(3)]

        results = runner._dispatch_with_restart([w], items, total=3)

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
        runner = _stub_runner(tmp_path)
        w0 = _FailStartWorker(0, runner.config)
        w1 = _FailStartWorker(1, runner.config)
        live = runner._start_workers_parallel([w0, w1])
        assert live == []
        assert w0.n_closed >= 1
        assert w1.n_closed >= 1

    def test_all_workers_closed_before_all_failed_raise(self, tmp_path):
        from dstf.config import Config

        (tmp_path / "package.mo").write_text("package Lib end Lib;")
        cfg = Config(
            source_path=tmp_path,
            reference_root=tmp_path / "refs",
            simulator="OpenModelica",
            work_dir=tmp_path / "work",
            parallel=2,
        )

        created: list[_FailStartWorker] = []

        class _FailingRunner(_StubRunner):
            worker_cls = _FailStartWorker

            def make_worker(self, worker_id):
                w = _FailStartWorker(worker_id, self.config)
                created.append(w)
                return w

        runner = _FailingRunner(cfg)
        with pytest.raises(RuntimeError, match="failed to start"):
            runner.run_tests([_tm("Lib.A"), _tm("Lib.B")])
        assert created
        assert all(w.n_closed >= 1 for w in created)


# ---------------------------------------------------------------------------
# Finding 26 — persistent OM honors the configured omc path
# ---------------------------------------------------------------------------


class _MiniOmcSession:
    def __init__(self):
        self.calls: list[str] = []

    def sendExpression(self, expr: str, parsed: bool = True):
        self.calls.append(expr)
        if expr.startswith("getErrorString"):
            return '""\n'
        return "true\n"

    def getpid(self):
        return 4242


class TestOmPersistentOmcPath:
    def _worker(self, tmp_path, simulator_path):
        from dstf.simulators.openmodelica.persistent_runner import OpenModelicaWorker
        from dstf.simulators.openmodelica.runner import OpenModelicaConfig

        cfg = SimpleNamespace(
            simulator_path=simulator_path,
            dependencies=[],
            library_dir=tmp_path,
            work_dir=tmp_path / "work",
        )
        om_cfg = OpenModelicaConfig(omc_path=str(simulator_path or "omc"))
        return OpenModelicaWorker(0, cfg, om_cfg, _MiniOmcSession)

    def test_configured_omc_path_exports_openmodelicahome(self, tmp_path, monkeypatch):
        om_home = tmp_path / "om-1.24"
        omc = om_home / "bin" / "omc"
        omc.parent.mkdir(parents=True)
        omc.write_text("#!/bin/sh\n")
        monkeypatch.delenv("OPENMODELICAHOME", raising=False)

        worker = self._worker(tmp_path, str(omc))
        worker.start()
        assert os.environ.get("OPENMODELICAHOME") == str(om_home)

    def test_no_configured_path_leaves_env_alone(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPENMODELICAHOME", raising=False)
        worker = self._worker(tmp_path, None)
        worker.start()
        assert "OPENMODELICAHOME" not in os.environ


# ---------------------------------------------------------------------------
# Finding 22 (+76-part) — Dymola batch timeout: tree-kill + disk salvage
# ---------------------------------------------------------------------------


class _FakeDymolaProc:
    def __init__(self):
        self.pid = 2**28 + 12345  # (almost certainly) nonexistent pid
        self.killed = False
        self.communicate_calls: list[float | None] = []
        self.stdout = None
        self.stderr = None

    def communicate(self, timeout=None):
        self.communicate_calls.append(timeout)
        if len(self.communicate_calls) == 1:
            raise subprocess.TimeoutExpired(cmd="dymola", timeout=timeout)
        return (b"", b"")

    def kill(self):
        self.killed = True


class TestDymolaBatchTimeout:
    def test_tree_kill_and_salvage_of_completed_tests(self, tmp_path, monkeypatch):
        from dstf.simulators.dymola import runner as dr

        wd = tmp_path / "work"
        wd.mkdir()
        cfg = SimpleNamespace(
            show_ide=False,
            simulator_setup=[],
            diagnostic_variables=["CPUtime"],
            work_dir=wd,
            simulator_path="dymola-fake",
            timeout=1,
            parallel=1,
        )
        runner = dr.DymolaRunner(cfg)

        test_a = _tm("Lib.A", stop_time=1.0)
        test_b = _tm("Lib.B", stop_time=1.0)
        items = [(test_a, "test_0001"), (test_b, "test_0002")]

        # Test A verifiably completed on disk before the batch deadline.
        dir_a = wd / "test_0001"
        dir_a.mkdir()
        n = 5
        t = np.linspace(0.0, 1.0, n)
        _write_mat4(dir_a / "dsres.mat", [("data_2", np.vstack([t, 2 * t]), 0)])
        (dir_a / "dsfinal.txt").write_text("done")
        # Test B produced nothing.
        (wd / "test_0002").mkdir()

        fake = _FakeDymolaProc()
        monkeypatch.setattr(dr.subprocess, "Popen", lambda *a, **kw: fake)

        startup = wd / "startup.mos"
        shutdown = wd / "shutdown.mos"
        startup.write_text("// stub")
        shutdown.write_text("// stub")

        results = runner._run_batch(items, startup, shutdown, 0, 2)

        # Tree-kill happened, and the post-kill communicate was bounded.
        assert fake.killed is True
        assert len(fake.communicate_calls) == 2
        assert fake.communicate_calls[1] is not None

        by_id = {r.model_id: r for r in results}
        assert by_id["Lib.A"].success is True  # salvaged from disk
        assert by_id["Lib.A"].timed_out is False
        assert by_id["Lib.B"].success is False
        assert by_id["Lib.B"].timed_out is True


# ---------------------------------------------------------------------------
# Finding 76-parts — cross-backend ValueError + export_fmu cleanup
# ---------------------------------------------------------------------------


class TestCrossBackendSoftCheckValueError:
    def test_valueerror_from_add_soft_check_is_logged_and_skipped(
        self, tmp_path, monkeypatch, caplog
    ):
        from dstf.config import Config
        from dstf.simulators.base import TestResult, VariableResult
        from dstf.simulators.cross_backend import produce_dymola_via_fmpy_baseline

        fmu_dir = tmp_path / "fmu_examples"
        fmu_dir.mkdir()
        fmu_path = fmu_dir / "Model.fmu"
        fmu_path.write_bytes(b"fake")

        config = Config(
            source_path=fmu_dir,
            reference_root=tmp_path / "refs",
            source_type="fmu",
            simulator="FMPy",
            work_dir=tmp_path / "work",
        )

        class _FakeFmpyRunner:
            def __init__(self, cfg):
                self.config = cfg

            def run_single_test(self, test, test_key, index, total):
                return TestRunResult(
                    model_id=test.model_id, test_key=test_key, success=True
                )

            def read_result(self, test, test_key, run_result):
                return TestResult(
                    model_id=test.model_id,
                    success=True,
                    variables=[
                        VariableResult(
                            index=1,
                            name="h",
                            time=np.array([0.0, 1.0]),
                            values=np.array([1.0, 0.5]),
                        )
                    ],
                )

        monkeypatch.setattr("dstf.simulators.fmpy.runner.FmpyRunner", _FakeFmpyRunner)

        primary = MagicMock()
        primary.export_fmu.return_value = fmu_path

        store = MagicMock()
        store.add_soft_check.side_effect = ValueError("name collides with companion")

        test = _tm("Model", variable_patterns=["h"])
        with caplog.at_level(logging.WARNING):
            ok = produce_dymola_via_fmpy_baseline(test, primary, config, store)
        assert ok is False  # logged + skipped, not raised
        assert "cannot store baseline" in caplog.text


class TestExportFmuCleanup:
    def test_one_shot_worker_closed_via_guarded_close(self, tmp_path, monkeypatch):
        from dstf.simulators.dymola import persistent_runner as pr

        created: list = []
        closed: list = []

        def fake_start(self):
            self.dymola = MagicMock()
            created.append(self)

        orig_close = pr.DymolaWorker.close

        def spy_close(self, grace: float = 5.0):
            closed.append(self)
            orig_close(self, grace=0.1)

        monkeypatch.setattr(pr.DymolaWorker, "start", fake_start)
        monkeypatch.setattr(pr.DymolaWorker, "close", spy_close)
        monkeypatch.setattr(pr, "load_dymola_interface", lambda p=None: MagicMock())
        monkeypatch.setattr(
            pr.DymolaWorker,
            "export_fmu",
            lambda self, test, output_dir: output_dir / "X.fmu",
        )

        cfg = SimpleNamespace(
            show_ide=False,
            simulator_setup=[],
            diagnostic_variables=[],
            work_dir=tmp_path,
            dymola_interface_path=None,
            simulator_path=None,
            dependencies=[],
            library_dir=tmp_path,
        )
        runner = pr.PersistentDymolaRunner(cfg)
        out = tmp_path / "out"
        out.mkdir()
        runner.export_fmu(_tm("M.X"), out)

        assert created, "worker was never started"
        assert created[0] in closed, (
            "export_fmu cleanup must call worker.close() (guarded hard-kill "
            "path), not worker.dymola.close()"
        )


# ---------------------------------------------------------------------------
# Findings 4/27/75 — Julia driver scripts (content pins; julia not runnable
# on this machine, so the decisions are at least pinned textually)
# ---------------------------------------------------------------------------


class TestJuliaDriverContent:
    def test_both_drivers_check_retcode_and_stop_time(self):
        run_test = (JULIA_DRIVER_DIR / "run_test.jl").read_text(encoding="utf-8")
        run_persistent = (JULIA_DRIVER_DIR / "run_persistent.jl").read_text(
            encoding="utf-8"
        )
        for text in (run_test, run_persistent):
            assert "successful_retcode" in text, "finding 4: retcode unchecked"
            assert "sol.t[end]" in text, "finding 4: stop-time reach unchecked"
            assert "retcode" in text

    def test_persistent_driver_poisons_build_mtk_system_before_include(self):
        """finding 27 (completed post-audit): the sentinel must be rebound
        BEFORE every include — an isdefined check alone only caught the
        first-ever bad file; after any good test had defined the function,
        a later bad file silently reused the previous test's model."""
        run_persistent = (JULIA_DRIVER_DIR / "run_persistent.jl").read_text(
            encoding="utf-8"
        )
        assert "stale-definition guard" in run_persistent
        # rindex: the phrase also appears in the header comment; the code
        # call is the last occurrence.
        include_pos = run_persistent.rindex("include(user_file)")
        poison_pos = run_persistent.index("stale-definition guard")
        assert poison_pos < include_pos, (
            "finding 27: the poison sentinel must be installed before "
            "include(user_file), not after"
        )

    def test_batch_driver_failure_json_uses_manual_escaper(self):
        run_test = (JULIA_DRIVER_DIR / "run_test.jl").read_text(encoding="utf-8")
        assert "Base.repr" not in run_test, (
            "finding 75: Julia repr escaping produces invalid JSON (\\$)"
        )
        assert "json_escape" in run_test


class TestJuliaPersistentPipeEncoding:
    def test_worker_popen_uses_utf8_replace(self, tmp_path, monkeypatch):
        """Finding 75: on cp1252 Windows a UTF-8 Julia backtrace kills the
        reader thread unless the pipes decode utf-8 with errors=replace."""
        from dstf.simulators.julia import persistent_runner as jpr

        captured: dict = {}

        def fake_popen(*args, **kwargs):
            captured.update(kwargs)
            raise OSError("stop here — kwargs captured")

        monkeypatch.setattr(jpr.subprocess, "Popen", fake_popen)
        julia_cfg = SimpleNamespace(julia_binary=Path("julia"), project_dir=tmp_path)
        worker = jpr.JuliaWorker(0, SimpleNamespace(work_dir=tmp_path), julia_cfg)
        with pytest.raises(OSError):
            worker.start()
        assert captured.get("encoding") == "utf-8"
        assert captured.get("errors") == "replace"
