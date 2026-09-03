"""The shared JSON-driver skeleton (``simulators/common/json_driver.py``).

Julia and Python are the same subprocess-per-test backend; the run/read
machinery is one implementation (C-iii U-4). These tests pin that machinery
directly — timeout mapping, the non-zero-return message precedence, the
numeric validation of a driver payload — plus the Julia tool-version stamp,
which the batch lane gained in the same change.

Everything here stubs ``subprocess.run``; nothing needs a julia binary.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import make_test_model
from dstf.config import Config
from dstf.discovery.test_registry import TestModel
from dstf.simulators.common.json_driver import JsonDriverRunner

# ---------------------------------------------------------------------------
# Fixtures / fakes
# ---------------------------------------------------------------------------


def _tm(model_id: str = "Lib.A", *, source: Path | None = None) -> TestModel:
    """conftest's builder with this lane's defaults: one tracked variable, a
    lowercase solver name (what the JSON driver payload carries), and an
    optionally-absent source file."""
    return make_test_model(
        model_id,
        source_file=source,
        variable_patterns=["x"],
        method="dassl",
    )


class _FakeRunner(JsonDriverRunner):
    """A JSON-driver backend whose 'tool' is never actually spawned."""

    driver_path = Path("/nonexistent/driver.ext")
    tool_label = "Fake"
    action_label = "execution"
    stdio_prefix = "fake"
    source_suffix = ".ext"

    @property
    def tool_binary(self) -> Path:
        return Path("/nonexistent/faketool")

    def build_command(
        self, binary, driver, user_file, stop_time, tolerance, result_path
    ) -> list[str]:
        return [str(binary), str(driver), str(user_file), str(result_path)]


def _config(tmp_path: Path, **kwargs) -> Config:
    lib = tmp_path / "Lib"
    lib.mkdir(parents=True, exist_ok=True)
    (lib / "package.mo").write_text("package Lib end Lib;")
    return Config(
        source_path=lib,
        reference_root=tmp_path / "refs",
        simulator="Dymola",
        work_dir=tmp_path / "work",
        timeout=5,
        **kwargs,
    )


def _fake_runner(tmp_path: Path) -> tuple[_FakeRunner, TestModel]:
    cfg = _config(tmp_path)
    user_file = cfg.source_path / "Lib.A.ext"
    user_file.write_text("# user source")
    return _FakeRunner(cfg), _tm()


def _patch_run(monkeypatch, fn):
    monkeypatch.setattr(
        "dstf.simulators.common.json_driver.subprocess.run", fn, raising=True
    )


def _proc(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# run_single_test
# ---------------------------------------------------------------------------


class TestSourceResolution:
    def test_missing_source_fails_before_spawning(self, tmp_path):
        cfg = _config(tmp_path)  # no Lib.A.ext written
        runner = _FakeRunner(cfg)

        result = runner.run_single_test(_tm(), "test_0001", 1, 1)

        assert result.success is False
        assert result.error_message.startswith("Fake source not found for Lib.A.")
        assert ".ext file under source_path" in result.error_message

    def test_spec_source_field_wins_over_the_model_id_fallback(self, tmp_path):
        cfg = _config(tmp_path)
        explicit = cfg.source_path / "elsewhere.ext"
        explicit.write_text("# user source")
        runner = _FakeRunner(cfg)

        assert runner._resolve_source(_tm(source=Path("elsewhere.ext"))) == explicit


class TestTimeoutMapping:
    def test_timeout_is_reported_as_timed_out_with_the_tool_label(
        self, tmp_path, monkeypatch
    ):
        runner, test = _fake_runner(tmp_path)

        def boom(*a, **kw):
            raise subprocess.TimeoutExpired(
                cmd="faketool",
                timeout=5.0,
                output=b"partial out",
                stderr=b"partial err",
            )

        _patch_run(monkeypatch, boom)

        result = runner.run_single_test(test, "test_0001", 1, 1)

        assert result.success is False
        assert result.timed_out is True
        assert result.error_message.startswith("Fake execution exceeded 5.0s timeout")
        # C-v item 5: every timeout names the per-test escape hatch.
        assert "simulation.timeout" in result.error_message
        # review finding 23: bytes on TimeoutExpired still reach the artifacts.
        test_dir = runner.config.work_dir / "test_0001"
        assert (test_dir / "fake_stdout.txt").read_text() == "partial out"
        assert (test_dir / "fake_stderr.txt").read_text() == "partial err"

    def test_per_test_timeout_overrides_the_config_timeout(self, tmp_path, monkeypatch):
        runner, test = _fake_runner(tmp_path)
        test.timeout = 99
        seen: dict = {}

        def boom(*a, **kw):
            seen["timeout"] = kw["timeout"]
            raise subprocess.TimeoutExpired(cmd="faketool", timeout=99.0)

        _patch_run(monkeypatch, boom)

        result = runner.run_single_test(test, "test_0001", 1, 1)

        assert seen["timeout"] == 99.0
        assert result.error_message.startswith("Fake execution exceeded 99.0s timeout")


class TestFailureMessagePrecedence:
    """rc != 0 → the driver's structured error, else the stderr tail, else a
    generic 'returned N'."""

    def _run(self, tmp_path, monkeypatch, *, stderr, failure_json=None):
        runner, test = _fake_runner(tmp_path)
        result_path = runner.config.work_dir / "test_0001" / "result.json"

        def fake(*a, **kw):
            if failure_json is not None:
                result_path.write_text(json.dumps(failure_json))
            return _proc(returncode=3, stderr=stderr)

        _patch_run(monkeypatch, fake)
        return runner.run_single_test(test, "test_0001", 1, 1)

    def test_driver_error_wins_over_stderr(self, tmp_path, monkeypatch):
        result = self._run(
            tmp_path,
            monkeypatch,
            stderr="noise\nlast stderr line\n",
            failure_json={"success": False, "error": "driver said no"},
        )
        assert result.error_message == "Fake execution failed: driver said no"

    def test_stderr_tail_when_no_driver_error(self, tmp_path, monkeypatch):
        result = self._run(tmp_path, monkeypatch, stderr="noise\nlast stderr line\n")
        assert result.error_message == "Fake execution failed: last stderr line"

    def test_generic_when_stderr_is_blank(self, tmp_path, monkeypatch):
        # review finding 75: whitespace-only stderr must not IndexError.
        result = self._run(tmp_path, monkeypatch, stderr="   \n  \n")
        assert result.error_message == "Fake execution failed: fake returned 3"

    def test_success_records_the_wall_time(self, tmp_path, monkeypatch):
        runner, test = _fake_runner(tmp_path)
        _patch_run(monkeypatch, lambda *a, **kw: _proc(returncode=0))

        result = runner.run_single_test(test, "test_0001", 1, 1)

        assert result.success is True
        assert result.statistics["simulation"]["wall_time"] == result.elapsed


# ---------------------------------------------------------------------------
# read_result — payload validation
# ---------------------------------------------------------------------------


def _julia_runner(tmp_path: Path):
    """A real JuliaRunner whose 'julia binary' is this interpreter — nothing
    is spawned, but JuliaConfig.from_config resolves without julia on PATH."""
    from dstf.simulators.julia.runner import JuliaRunner

    lib = tmp_path / "Lib"
    lib.mkdir(parents=True, exist_ok=True)
    (lib / "Project.toml").write_text('name = "Lib"\n')
    cfg = Config(
        source_type="julia",
        source_path=lib,
        reference_root=tmp_path / "refs",
        simulator="Julia",
        simulator_path=sys.executable,
        work_dir=tmp_path / "work",
        timeout=5,
    )
    return JuliaRunner(cfg)


def _write_payload(runner, payload: dict) -> None:
    test_dir = runner.config.work_dir / "test_0001"
    test_dir.mkdir(parents=True, exist_ok=True)
    (test_dir / "result.json").write_text(json.dumps(payload))


class TestJuliaPayloadHardening:
    """Decided change A: the numeric belt (review finding 29), which only the
    Python backend had, now covers Julia too — a non-numeric payload fails
    THIS test instead of raising through the whole read phase."""

    def test_non_numeric_variable_fails_the_test_without_raising(self, tmp_path):
        runner = _julia_runner(tmp_path)
        _write_payload(
            runner,
            {
                "success": True,
                "time": [0.0, 1.0],
                "variables": {"x": ["not", "numeric"]},
            },
        )

        result = runner.read_result(_tm(), "test_0001", None)

        assert result.success is False
        assert "Variable 'x' has non-numeric values" in result.error_message

    def test_non_numeric_time_fails_the_test(self, tmp_path):
        runner = _julia_runner(tmp_path)
        _write_payload(
            runner,
            {"success": True, "time": ["t0"], "variables": {"x": [1.0]}},
        )

        result = runner.read_result(_tm(), "test_0001", None)

        assert result.success is False
        assert "Non-numeric 'time' array" in result.error_message

    def test_numeric_payload_still_reads(self, tmp_path):
        runner = _julia_runner(tmp_path)
        _write_payload(
            runner,
            {"success": True, "time": [0.0, 1.0], "variables": {"x": [0.0, 2.0]}},
        )

        result = runner.read_result(_tm(), "test_0001", None)

        assert result.success is True
        assert [v.name for v in result.variables] == ["x"]
        assert result.variables[0].values[-1] == pytest.approx(2.0)

    def test_missing_result_names_the_backend(self, tmp_path):
        runner = _julia_runner(tmp_path)

        result = runner.read_result(_tm(), "test_0001", None)

        assert result.success is False
        assert "No Julia result at" in result.error_message
        assert "(did simulation run?)" in result.error_message

    def test_driver_failure_payload_is_surfaced(self, tmp_path):
        runner = _julia_runner(tmp_path)
        _write_payload(runner, {"success": False, "error": "MTK blew up"})

        result = runner.read_result(_tm(), "test_0001", None)

        assert result.success is False
        assert result.error_message == "MTK blew up"


# ---------------------------------------------------------------------------
# Julia tool version (decided change B)
# ---------------------------------------------------------------------------


def _patch_julia_version(monkeypatch, *, banner="julia version 1.11.2\n", boom=False):
    """Stub the ``<binary> --version`` probe."""

    def fake(cmd, **kw):
        if boom:
            raise OSError("no such binary")
        assert cmd[-1] == "--version", cmd
        return _proc(stdout=banner)

    monkeypatch.setattr(
        "dstf.simulators.julia.runner.subprocess.run", fake, raising=True
    )


class TestJuliaToolVersion:
    def test_describe_reports_the_binary_banner(self, tmp_path, monkeypatch):
        runner = _julia_runner(tmp_path)
        _patch_julia_version(monkeypatch)

        assert runner.describe_tool_version() == "julia version 1.11.2"

    def test_describe_degrades_to_none(self, tmp_path, monkeypatch):
        runner = _julia_runner(tmp_path)
        _patch_julia_version(monkeypatch, boom=True)

        assert runner.describe_tool_version() is None

    def test_batch_run_stamps_a_non_null_tool_version(self, tmp_path, monkeypatch):
        """The batch lane used to stamp null — the probe only existed on the
        persistent class."""
        runner = _julia_runner(tmp_path)
        (runner.config.source_path / "Lib.A.jl").write_text("# user source")

        # The version probe and the driver invocation go through the same
        # subprocess.run, so one stub serves both, dispatching on argv.
        def fake(cmd, **kw):
            if cmd[-1] == "--version":
                return _proc(stdout="julia version 1.11.2\n")
            Path(cmd[-1]).write_text(
                json.dumps({"success": True, "time": [0.0], "variables": {"x": [0.0]}})
            )
            return _proc(returncode=0)

        _patch_run(monkeypatch, fake)

        runner.run_tests([_tm()])

        status = json.loads((runner.config.work_dir / "status.json").read_text())
        assert status["metadata"]["tool_version"] == "julia version 1.11.2"

    def test_persistent_lane_still_stamps_without_a_worker_probe(
        self, tmp_path, monkeypatch
    ):
        """The persistent template stamps provenance from
        ``describe_tool_version`` in the shared prologue, before workers exist
        — so PersistentJuliaRunner needs no ``_probe_worker_version``."""
        from dstf.simulators.julia.persistent_runner import PersistentJuliaRunner

        runner = _julia_runner(tmp_path)
        persistent = PersistentJuliaRunner(runner.config)
        _patch_julia_version(monkeypatch)

        setup = persistent._prepare_run([_tm()])

        assert setup.run_metadata["tool_version"] == "julia version 1.11.2"
        status = json.loads((runner.config.work_dir / "status.json").read_text())
        assert status["metadata"]["tool_version"] == "julia version 1.11.2"
