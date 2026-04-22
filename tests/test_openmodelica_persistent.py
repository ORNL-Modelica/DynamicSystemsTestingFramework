"""Unit + integration tests for the persistent-worker OpenModelica runner.

Unit tests use a FakeSession that quacks like ``OMCSessionZMQ`` — no
dependency on real OMPython. Integration tests require both omc on PATH
and OMPython installed, and are gated accordingly.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from modelica_testing.config import Config
from modelica_testing.discovery.test_registry import TestModel


# ---------------------------------------------------------------------------
# FakeSession + fixtures
# ---------------------------------------------------------------------------


class FakeSession:
    """Minimal OMCSessionZMQ stand-in for unit tests.

    Records every ``sendExpression`` call. Caller can seed canned responses
    via ``responses`` (expression prefix → value-or-callable). Default
    return is ``True`` (so load/setOption sequences succeed).
    """

    def __init__(self, responses: dict | None = None, pid: int = 99999):
        self.calls: list[str] = []
        self._responses: dict = responses or {}
        self._pid = pid
        self.closed = False

    def sendExpression(self, expr: str):
        self.calls.append(expr)
        # Longest-prefix match so `simulate(` beats `simu`
        matches = [k for k in self._responses if expr.startswith(k)]
        if matches:
            key = max(matches, key=len)
            val = self._responses[key]
            return val(expr) if callable(val) else val
        if expr.startswith("getErrorString"):
            return ""
        return True

    def getpid(self):
        return self._pid


def _make_test(**overrides) -> TestModel:
    defaults: dict[str, Any] = dict(
        model_id="Demo.Example.A",
        source_file=Path(""),
        source_package="Demo.Example",
        short_name="A",
        n_vars=0,
        variable_patterns=["x"],
        stop_time=1.0,
        tolerance=1e-6,
        method="dassl",
    )
    defaults.update(overrides)
    return TestModel(**defaults)


def _make_config(tmp_path: Path, **overrides) -> Config:
    (tmp_path / "package.mo").write_text("package Lib end Lib;")
    defaults: dict[str, Any] = dict(
        source_path=tmp_path,
        reference_root=tmp_path / "refs",
        simulator="OpenModelica",
        work_dir=tmp_path / "work",
    )
    defaults.update(overrides)
    return Config(**defaults)


# ---------------------------------------------------------------------------
# Worker startup
# ---------------------------------------------------------------------------


class TestWorkerStartup:
    def test_startup_sequence_with_msl_auto_injection(self, tmp_path):
        """Empty `dependencies` still triggers loadModel(Modelica) — matches
        the batch runner's auto-injection so a unified testing.json works."""
        from modelica_testing.simulators.openmodelica.persistent_runner import (
            OpenModelicaWorker,
        )
        from modelica_testing.simulators.openmodelica.runner import (
            OpenModelicaConfig,
        )

        cfg = _make_config(tmp_path, dependencies=[])
        om_cfg = OpenModelicaConfig.from_config(cfg)
        fake = FakeSession()
        worker = OpenModelicaWorker(0, cfg, om_cfg, lambda: fake)
        worker.start()

        # Order matters: setCommandLineOptions first, then loadModel(Modelica),
        # then loadFile(package.mo).
        assert any("setCommandLineOptions" in c for c in fake.calls)
        assert any("loadModel(Modelica)" in c for c in fake.calls)
        assert any("package.mo" in c and "loadFile" in c for c in fake.calls)
        # MSL appears before main library load
        idx_msl = next(i for i, c in enumerate(fake.calls) if "loadModel(Modelica)" in c)
        idx_lib = next(i for i, c in enumerate(fake.calls) if "package.mo" in c)
        assert idx_msl < idx_lib

    def test_startup_respects_user_deps_order(self, tmp_path):
        from modelica_testing.simulators.openmodelica.persistent_runner import (
            OpenModelicaWorker,
        )
        from modelica_testing.simulators.openmodelica.runner import (
            OpenModelicaConfig,
        )

        cfg = _make_config(tmp_path, dependencies=["Modelica", "SDF"])
        om_cfg = OpenModelicaConfig.from_config(cfg)
        fake = FakeSession()
        worker = OpenModelicaWorker(0, cfg, om_cfg, lambda: fake)
        worker.start()

        # MSL not double-loaded (user already listed it)
        msl_count = sum(1 for c in fake.calls if "loadModel(Modelica)" in c)
        assert msl_count == 1
        # SDF loaded too
        assert any("loadModel(SDF)" in c for c in fake.calls)

    def test_startup_raises_on_library_load_failure(self, tmp_path):
        from modelica_testing.simulators.openmodelica.persistent_runner import (
            OpenModelicaWorker,
        )
        from modelica_testing.simulators.openmodelica.runner import (
            OpenModelicaConfig,
        )

        cfg = _make_config(tmp_path, dependencies=["Modelica"])
        om_cfg = OpenModelicaConfig.from_config(cfg)
        # Simulate loadFile(package.mo) failing
        fake = FakeSession(responses={
            'loadFile("': lambda e: False,
            "getErrorString": "[library.mo:1:1-1:1] Error: Package syntax error",
        })
        worker = OpenModelicaWorker(0, cfg, om_cfg, lambda: fake)
        with pytest.raises(RuntimeError, match="failed to load library"):
            worker.start()

    def test_startup_captures_pid(self, tmp_path):
        from modelica_testing.simulators.openmodelica.persistent_runner import (
            OpenModelicaWorker,
        )
        from modelica_testing.simulators.openmodelica.runner import (
            OpenModelicaConfig,
        )

        cfg = _make_config(tmp_path, dependencies=["Modelica"])
        om_cfg = OpenModelicaConfig.from_config(cfg)
        fake = FakeSession(pid=12345)
        worker = OpenModelicaWorker(0, cfg, om_cfg, lambda: fake)
        worker.start()
        assert 12345 in worker.pids

    def test_startup_applies_std_version(self, tmp_path):
        """std_version threads into setCommandLineOptions."""
        from modelica_testing.simulators.openmodelica.persistent_runner import (
            OpenModelicaWorker,
        )
        from modelica_testing.simulators.openmodelica.runner import (
            OpenModelicaConfig,
        )

        cfg = _make_config(tmp_path, dependencies=["Modelica"])
        # Inject a non-default std_version
        base = OpenModelicaConfig.from_config(cfg)
        om_cfg = OpenModelicaConfig(
            omc_path=base.omc_path,
            simulator_setup=base.simulator_setup,
            diagnostic_variables=base.diagnostic_variables,
            std_version="3.3",
        )
        fake = FakeSession()
        worker = OpenModelicaWorker(0, cfg, om_cfg, lambda: fake)
        worker.start()
        assert any(
            'setCommandLineOptions("--std=3.3")' in c for c in fake.calls
        )


# ---------------------------------------------------------------------------
# run_test
# ---------------------------------------------------------------------------


class TestWorkerRunTest:
    def _bootstrap_worker(self, tmp_path, responses=None):
        from modelica_testing.simulators.openmodelica.persistent_runner import (
            OpenModelicaWorker,
        )
        from modelica_testing.simulators.openmodelica.runner import (
            OpenModelicaConfig,
        )

        cfg = _make_config(tmp_path, dependencies=["Modelica"])
        om_cfg = OpenModelicaConfig.from_config(cfg)
        fake = FakeSession(responses=responses or {})
        worker = OpenModelicaWorker(0, cfg, om_cfg, lambda: fake)
        worker.start()
        return worker, fake, cfg

    def test_success_path_returns_record_timings(self, tmp_path):
        """Simulate returns a record dict; worker parses timings, writes
        stdout artifact, returns success."""
        test = _make_test(stop_time=1.0)
        # Fake simulate() returns a record dict and writes a placeholder MAT.
        work_dir = tmp_path / "work"
        test_dir = work_dir / "test_0001"

        record = {
            "resultFile": str(test_dir / "result_res.mat"),
            "messages": "The simulation finished successfully.",
            "timeFrontend": 0.1,
            "timeBackend": 0.05,
            "timeSimCode": 0.01,
            "timeTemplates": 0.02,
            "timeCompile": 0.9,
            "timeSimulation": 0.03,
            "timeTotal": 1.11,
        }

        def fake_simulate(expr: str):
            # Simulate OM's side effect: write a MAT with enough time span
            # to clear the disk-check
            test_dir.mkdir(parents=True, exist_ok=True)
            _write_stub_mat(test_dir / "result_res.mat", stop_time=test.stop_time)
            return record

        worker, fake, cfg = self._bootstrap_worker(
            tmp_path, responses={"simulate(": fake_simulate},
        )
        result = worker.run_test(test, "test_0001")

        assert result.success is True
        assert result.translation_wall == pytest.approx(0.1 + 0.05 + 0.01 + 0.02 + 0.9)
        assert result.sim_wall == pytest.approx(0.03)
        assert result.statistics["timing"]["total"] == pytest.approx(1.11)
        # Artifact written (parity with batch runner)
        stdout_artifact = test_dir / "omc_stdout.txt"
        assert stdout_artifact.exists()
        assert "record SimulationResult" in stdout_artifact.read_text()

    def test_simulate_expression_uses_build_simulate_args(self, tmp_path):
        """The sendExpression("simulate(...)") string contains kwargs
        produced by build_simulate_args — tolerance / method / stopTime etc."""
        test = _make_test(
            stop_time=5.0, tolerance=1e-8, method="Dassl",
            number_of_intervals=100,
        )
        test_dir = tmp_path / "work" / "test_0001"

        captured: dict = {}

        def fake_simulate(expr: str):
            captured["expr"] = expr
            test_dir.mkdir(parents=True, exist_ok=True)
            _write_stub_mat(test_dir / "result_res.mat", stop_time=test.stop_time)
            return {
                "resultFile": str(test_dir / "result_res.mat"),
                "messages": "ok",
                "timeFrontend": 0.0, "timeBackend": 0.0, "timeSimCode": 0.0,
                "timeTemplates": 0.0, "timeCompile": 0.0, "timeSimulation": 0.0,
                "timeTotal": 0.0,
            }

        worker, fake, _ = self._bootstrap_worker(
            tmp_path, responses={"simulate(": fake_simulate},
        )
        worker.run_test(test, "test_0001")

        expr = captured["expr"]
        assert expr.startswith(f"simulate({test.model_id}")
        assert "stopTime=5.0" in expr
        assert "tolerance=1e-08" in expr
        assert 'method="dassl"' in expr  # lowercased per build_simulate_args
        assert "numberOfIntervals=100" in expr
        assert 'outputFormat="mat"' in expr
        assert 'fileNamePrefix="result"' in expr

    def test_failure_surfaces_error_notices(self, tmp_path):
        """When simulate returns a record with empty resultFile + Error
        notices in getErrorString, run_test returns a readable error."""
        test = _make_test()

        def fake_simulate(expr: str):
            return {
                "resultFile": "",
                "messages": "Simulation Failed. Model: Demo.Example.A does not exist!",
                "timeFrontend": 0.0, "timeBackend": 0.0, "timeSimCode": 0.0,
                "timeTemplates": 0.0, "timeCompile": 0.0, "timeSimulation": 0.0,
                "timeTotal": 0.0,
            }

        responses = {
            "simulate(": fake_simulate,
            "getErrorString": "Error: Class Demo.Example.A not found.\n",
        }
        worker, _, _ = self._bootstrap_worker(tmp_path, responses=responses)
        result = worker.run_test(test, "test_0001")

        assert result.success is False
        assert result.error_message
        low = result.error_message.lower()
        assert "not found" in low or "does not exist" in low

    def test_session_exception_triggers_disk_fallback(self, tmp_path):
        """If sendExpression raises but result_res.mat exists + reaches
        stop_time, disk-fallback salvages the success."""
        test = _make_test(stop_time=1.0)
        test_dir = tmp_path / "work" / "test_0001"

        def boom(expr: str):
            # Side-effect: write MAT before the "crash"
            test_dir.mkdir(parents=True, exist_ok=True)
            _write_stub_mat(test_dir / "result_res.mat", stop_time=test.stop_time)
            raise RuntimeError("ZMQ connection dropped")

        worker, _, _ = self._bootstrap_worker(
            tmp_path, responses={"simulate(": boom},
        )
        result = worker.run_test(test, "test_0001")
        assert result.success is True

    def test_session_exception_no_mat_is_failure(self, tmp_path):
        """Exception + no MAT on disk → failure with the session error surfaced."""
        test = _make_test()

        def boom(expr: str):
            raise RuntimeError("ZMQ connection dropped")

        worker, _, _ = self._bootstrap_worker(
            tmp_path, responses={"simulate(": boom},
        )
        result = worker.run_test(test, "test_0001")
        assert result.success is False
        assert "OMCSessionZMQ error" in (result.error_message or "")
        assert "ZMQ connection dropped" in (result.error_message or "")


# ---------------------------------------------------------------------------
# Timeout + close
# ---------------------------------------------------------------------------


class TestWorkerLifecycle:
    def test_run_test_with_timeout_hard_kills_on_hang(self, tmp_path):
        """A hung simulate() triggers close() and returns a timed_out result."""
        import time

        from modelica_testing.simulators.openmodelica.persistent_runner import (
            OpenModelicaWorker,
        )
        from modelica_testing.simulators.openmodelica.runner import (
            OpenModelicaConfig,
        )

        cfg = _make_config(tmp_path, dependencies=["Modelica"])
        om_cfg = OpenModelicaConfig.from_config(cfg)

        def hang(expr: str):
            time.sleep(5.0)  # longer than the test's 0.3s timeout
            return True

        fake = FakeSession(responses={"simulate(": hang})
        worker = OpenModelicaWorker(0, cfg, om_cfg, lambda: fake)
        worker.start()

        test = _make_test()
        tr = worker.run_test_with_timeout(test, "test_0001", timeout=0.3)
        assert tr.timed_out is True
        assert tr.success is False
        # close() resets session
        assert worker.is_alive() is False

    def test_close_is_idempotent(self, tmp_path):
        from modelica_testing.simulators.openmodelica.persistent_runner import (
            OpenModelicaWorker,
        )
        from modelica_testing.simulators.openmodelica.runner import (
            OpenModelicaConfig,
        )

        cfg = _make_config(tmp_path, dependencies=["Modelica"])
        om_cfg = OpenModelicaConfig.from_config(cfg)
        worker = OpenModelicaWorker(0, cfg, om_cfg, lambda: FakeSession())
        worker.start()
        worker.close()
        worker.close()  # second call is a no-op — should not raise
        assert worker.is_alive() is False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_parsed_from_record_timings_and_success(self):
        from modelica_testing.simulators.openmodelica.persistent_runner import (
            _parsed_from_record,
        )

        rec = {
            "resultFile": "/tmp/result_res.mat",
            "messages": "The simulation finished successfully.",
            "timeFrontend": 0.1, "timeBackend": 0.2, "timeSimCode": 0.05,
            "timeTemplates": 0.03, "timeCompile": 0.4, "timeSimulation": 0.6,
            "timeTotal": 1.38,
        }
        parsed = _parsed_from_record(rec, error_notices=[])
        assert parsed.success is True
        assert parsed.result_file == "/tmp/result_res.mat"
        assert parsed.timings is not None
        assert parsed.timings["frontend"] == pytest.approx(0.1)
        assert parsed.timings["simulation"] == pytest.approx(0.6)

    def test_parsed_from_record_failed_messages(self):
        from modelica_testing.simulators.openmodelica.persistent_runner import (
            _parsed_from_record,
        )

        rec = {"resultFile": "/x.mat", "messages": "Simulation Failed — bad init"}
        parsed = _parsed_from_record(rec, error_notices=[])
        assert parsed.success is False  # "Failed" in messages

    def test_parsed_from_record_none_or_empty(self):
        from modelica_testing.simulators.openmodelica.persistent_runner import (
            _parsed_from_record,
        )

        assert _parsed_from_record(None, error_notices=["Error: bad"]).success is False
        assert _parsed_from_record({}, error_notices=[]).success is False

    def test_synthesize_stdout_artifact_contains_record(self):
        from modelica_testing.simulators.openmodelica.persistent_runner import (
            _synthesize_stdout_artifact,
        )

        text = _synthesize_stdout_artifact(
            worker_id=3,
            simulate_expr='simulate(Foo.Bar, stopTime=1.0)',
            returned_record={
                "resultFile": "/x.mat",
                "timeFrontend": 0.1,
            },
            error_string="Warning: nothing critical",
        )
        assert "worker=3" in text
        assert ">>> simulate(Foo.Bar" in text
        assert "record SimulationResult" in text
        assert "end SimulationResult;" in text
        assert 'resultFile = "/x.mat",' in text
        assert "timeFrontend = 0.1," in text
        assert "Warning: nothing critical" in text


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


class TestCLISelectionWiring:
    def test_persistent_runner_inherits_batch(self):
        from modelica_testing.simulators.openmodelica.persistent_runner import (
            PersistentOpenModelicaRunner,
        )
        from modelica_testing.simulators.openmodelica.runner import (
            OpenModelicaRunner,
        )
        assert issubclass(PersistentOpenModelicaRunner, OpenModelicaRunner)

    def test_get_runner_selects_persistent_when_ompython_available(
        self, tmp_path, monkeypatch,
    ):
        """CLI _get_runner(persistent=True) picks PersistentOpenModelicaRunner
        when load_omc_session() succeeds (OMPython installed)."""
        from modelica_testing import cli as cli_mod
        from modelica_testing.simulators.openmodelica.persistent_runner import (
            PersistentOpenModelicaRunner,
        )

        cfg = _make_config(tmp_path, dependencies=["Modelica"])
        # Stub load_omc_session so the import path resolves without OMPython.
        monkeypatch.setattr(
            "modelica_testing.simulators.openmodelica.session_loader.load_omc_session",
            lambda: FakeSession,
        )
        runner = cli_mod._get_runner(cfg, persistent=True)
        assert isinstance(runner, PersistentOpenModelicaRunner)

    def test_get_runner_falls_back_when_ompython_missing(
        self, tmp_path, monkeypatch, capsys,
    ):
        """load_omc_session raising RuntimeError → CLI sticks with batch
        OpenModelicaRunner and prints a fallback notice."""
        from modelica_testing import cli as cli_mod
        from modelica_testing.simulators.openmodelica.persistent_runner import (
            PersistentOpenModelicaRunner,
        )
        from modelica_testing.simulators.openmodelica.runner import (
            OpenModelicaRunner,
        )

        cfg = _make_config(tmp_path, dependencies=["Modelica"])

        def _raise():
            raise RuntimeError("OMPython is not installed.")

        monkeypatch.setattr(
            "modelica_testing.simulators.openmodelica.session_loader.load_omc_session",
            _raise,
        )
        runner = cli_mod._get_runner(cfg, persistent=True)
        assert isinstance(runner, OpenModelicaRunner)
        assert not isinstance(runner, PersistentOpenModelicaRunner)
        stderr = capsys.readouterr().err
        assert "Persistent-worker OpenModelica unavailable" in stderr

    def test_get_runner_batch_mode_never_selects_persistent(
        self, tmp_path, monkeypatch,
    ):
        from modelica_testing import cli as cli_mod
        from modelica_testing.simulators.openmodelica.persistent_runner import (
            PersistentOpenModelicaRunner,
        )

        cfg = _make_config(tmp_path, dependencies=["Modelica"])
        monkeypatch.setattr(
            "modelica_testing.simulators.openmodelica.session_loader.load_omc_session",
            lambda: FakeSession,
        )
        runner = cli_mod._get_runner(cfg, persistent=False)
        assert not isinstance(runner, PersistentOpenModelicaRunner)


# ---------------------------------------------------------------------------
# Integration tests (real omc + real OMPython)
# ---------------------------------------------------------------------------


def _ompython_available() -> bool:
    try:
        import OMPython  # noqa: F401
        return True
    except ImportError:
        return False


pytestmark_integration = pytest.mark.skipif(
    shutil.which("omc") is None or not _ompython_available(),
    reason="persistent-worker integration needs both omc + OMPython",
)


@pytestmark_integration
class TestPersistentIntegration:
    def test_msl_smoke(self, tmp_path):
        """Spin up one real persistent worker; run one MSL-only test; ensure
        the worker loads MSL once and simulate() returns a usable record."""
        from modelica_testing.simulators.openmodelica.persistent_runner import (
            OpenModelicaWorker,
        )
        from modelica_testing.simulators.openmodelica.runner import (
            OpenModelicaConfig,
        )
        from modelica_testing.simulators.openmodelica.session_loader import (
            load_omc_session,
        )

        # OM's loadFile strictly checks the package.mo directory name matches
        # the package name inside it — so we need an EmptyLib/ subdir, not
        # pytest-tmp-path/package.mo directly.
        lib_root = tmp_path / "EmptyLib"
        lib_root.mkdir()
        (lib_root / "package.mo").write_text("package EmptyLib end EmptyLib;")
        cfg = Config(
            source_path=lib_root,
            reference_root=tmp_path / "refs",
            simulator="OpenModelica",
            work_dir=tmp_path / "work",
            dependencies=["Modelica"],
            timeout=120,
        )
        om_cfg = OpenModelicaConfig.from_config(cfg)
        session_cls = load_omc_session()
        worker = OpenModelicaWorker(0, cfg, om_cfg, session_cls)
        worker.start()
        try:
            test = TestModel(
                model_id="Modelica.Blocks.Examples.PID_Controller",
                source_file=Path(""),
                source_package="Modelica.Blocks.Examples",
                short_name="PID_Controller",
                n_vars=0,
                variable_patterns=["PI.y"],
                stop_time=2.0,
                tolerance=1e-6,
                method="dassl",
            )
            result = worker.run_test(test, "test_0001")
            assert result.success is True
            assert result.translation_wall is not None
            assert result.sim_wall is not None
        finally:
            worker.close()


# ---------------------------------------------------------------------------
# Tiny MAT-writer helper (avoids pulling in scipy just for a placeholder)
# ---------------------------------------------------------------------------


_FIXTURE_MAT = (
    Path(__file__).parent
    / "fixtures"
    / "results_openmodelica"
    / "pid_controller_res.mat"
)


def _write_stub_mat(path: Path, *, stop_time: float) -> None:
    """Stage a real OM result_res.mat under ``path`` for disk-fallback tests.

    Uses the existing PID-controller fixture (extents 0.0 → 1.0). The unit
    tests that call this pass ``stop_time=1.0``, so ``read_mat_time_extents``
    returns a last_time that clears the disk-check tolerance.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    import shutil as _sh
    _sh.copy(_FIXTURE_MAT, path)
