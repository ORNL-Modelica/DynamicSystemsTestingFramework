"""OpenModelica backend tests.

Unit tests here stub subprocess.run so they don't require an omc binary.
Integration tests at the bottom exercise real omc and are gated on
``shutil.which("omc")`` so CI / machines without omc still pass.
"""

import shutil
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from dstf.config import Config
from dstf.discovery.test_registry import TestModel

FIXTURES = Path(__file__).parent / "fixtures" / "results_openmodelica"


class TestOpenModelicaConfig:
    def test_from_config_basic(self, tmp_path):
        from dstf.backends.openmodelica.backend import (
            OpenModelicaConfig,
        )

        (tmp_path / "package.mo").write_text("package Lib end Lib;")
        cfg = Config(
            source_path=tmp_path,
            reference_root=tmp_path / "refs",
            backend_name="OpenModelica",
            work_dir=tmp_path / "work",
        )
        om = OpenModelicaConfig.from_config(cfg)
        # What `OpenModelicaBackend.locate` found — None on a machine without omc.
        assert om.omc_path == cfg.backend_path
        assert om.std_version == "latest"
        assert "CPUtime" in om.diagnostic_variables
        assert "EventCounter" in om.diagnostic_variables


class TestOpenModelicaBackendUnit:
    def test_registered_as_OpenModelica(self, tmp_path):
        from dstf.backends import get_backend_class
        from dstf.backends.openmodelica.backend import (
            OpenModelicaBackend,
        )

        (tmp_path / "package.mo").write_text("package Lib end Lib;")
        cfg = Config(
            source_path=tmp_path,
            reference_root=tmp_path / "refs",
            backend_name="OpenModelica",
            work_dir=tmp_path / "work",
        )
        cls = get_backend_class(cfg)
        assert cls is OpenModelicaBackend

    def test_capabilities_batch_persistent_and_live(self):
        from dstf.backends.openmodelica.backend import (
            OpenModelicaBackend,
        )
        from dstf.backends.types import Capability

        assert OpenModelicaBackend.capabilities == frozenset(
            {
                Capability.BATCH_FALLBACK,
                Capability.PERSISTENT_WORKERS,
                Capability.LIVE_RESULTS,
            },
        )

    def test_artifact_files_are_static(self):
        from dstf.backends.openmodelica.backend import (
            OpenModelicaBackend,
        )

        names = [name for name, _ in OpenModelicaBackend.artifact_files]
        assert "simulate.mos" in names
        assert "result_res.mat" in names
        assert "omc_stdout.txt" in names
        # No template placeholders
        for n in names:
            assert "{" not in n and "}" not in n

    def test_read_result_warns_on_missing_unit_test_variable(self, tmp_path, caplog):
        """U-3: OM used to skip an absent unitTests variable silently.

        Both DSresult backends now share ``common.result_variables``, whose
        missing-variable warning (previously Dymola-only) is the unified
        behavior: a requested variable absent from the output is loud.
        """
        import logging

        from dstf.backends.openmodelica.backend import OpenModelicaBackend

        (tmp_path / "package.mo").write_text("package Lib end Lib;")
        cfg = Config(
            source_path=tmp_path,
            reference_root=tmp_path / "refs",
            backend_name="OpenModelica",
            work_dir=tmp_path / "work",
        )
        backend = OpenModelicaBackend(cfg)
        # The fixture was captured with variableFilter "^(time|inertia1.phi)$",
        # so it carries no unitTests.x[i] — exactly the missing-variable case.
        test_dir = cfg.work_dir / "test_0001"
        test_dir.mkdir(parents=True)
        shutil.copy(
            FIXTURES / "pid_controller_res.mat",
            test_dir / OpenModelicaBackend.RESULT_MAT_FILENAME,
        )
        test = TestModel(
            model_id="Lib.Examples.MissingVar",
            source_file=Path(""),
            source_package="Lib.Examples",
            short_name="MissingVar",
            n_vars=1,
            variable_patterns=[],
            source="unit_tests",
        )

        with caplog.at_level(logging.WARNING):
            result = backend.read_result(test, "test_0001", None)

        assert result.success
        assert result.variables == []
        assert any(
            "unitTests.x[1]" in r.getMessage()
            and "Lib.Examples.MissingVar" in r.getMessage()
            for r in caplog.records
            if r.levelno >= logging.WARNING
        )

    def test_run_single_test_writes_mos_and_calls_omc(
        self,
        tmp_path,
        monkeypatch,
    ):
        """Subprocess invoked with omc + simulate.mos in test_dir,
        stdout captured to omc_stdout.txt, run_result reflects parsed output."""
        from dstf.backends.openmodelica.backend import (
            OpenModelicaBackend,
        )

        (tmp_path / "package.mo").write_text("package Lib end Lib;")
        # An omc that is there to run: a machine without one fails the test
        # at "omc not found" before anything is written.
        omc = tmp_path / "omc"
        omc.write_text("#!/bin/sh\n")
        cfg = Config(
            source_path=tmp_path,
            reference_root=tmp_path / "refs",
            backend_name="OpenModelica",
            backend_path=str(omc),
            work_dir=tmp_path / "work",
        )
        backend = OpenModelicaBackend(cfg)
        test = TestModel(
            model_id="Lib.A",
            source_file=Path(""),
            source_package="Lib",
            short_name="A",
            n_vars=0,
            variable_patterns=["x"],
            stop_time=1.0,
            tolerance=1e-6,  # explicit: direct construction bypasses discovery defaults (review 2026-07-06, finding 13)
        )

        # Synthetic omc stdout: a well-formed SimulationResult record.
        stdout_synth = (
            "record SimulationResult\n"
            '    resultFile = "/tmp/somewhere/result_res.mat",\n'
            '    simulationOptions = "...",\n'
            '    messages = "The simulation finished successfully.",\n'
            "    timeFrontend = 0.1,\n"
            "    timeBackend = 0.05,\n"
            "    timeSimCode = 0.01,\n"
            "    timeTemplates = 0.02,\n"
            "    timeCompile = 0.9,\n"
            "    timeSimulation = 0.03,\n"
            "    timeTotal = 1.11\n"
            "end SimulationResult;\n"
        )

        # The backend requires result_res.mat to exist AND reach stop_time
        # before declaring success (D99 partial-result belt), so stage a
        # real fixture mat (extents 0.0 → 1.0, matching stop_time=1.0) —
        # a garbage placeholder is now correctly refused.
        fixture_mat = (
            Path(__file__).parent
            / "fixtures"
            / "results_openmodelica"
            / "pid_controller_res.mat"
        )

        def fake_run(cmd, cwd, capture_output, text, timeout, **kwargs):
            # **kwargs absorbs encoding/errors (review 2026-07-06, finding 75)
            shutil.copy(fixture_mat, Path(cwd) / "result_res.mat")
            captured_call["cmd"] = list(cmd)
            captured_call["cwd"] = cwd
            return CompletedProcess(
                args=cmd, returncode=0, stdout=stdout_synth, stderr=""
            )

        captured_call: dict = {}
        monkeypatch.setattr(
            "dstf.backends.openmodelica.backend.subprocess.run",
            fake_run,
        )

        result = backend.run_single_test(test, test_key="test_0001", index=1, total=1)
        # Subprocess invocation
        assert (
            captured_call["cmd"][0].endswith("omc") or captured_call["cmd"][0] == "omc"
        )
        assert captured_call["cmd"][1] == "simulate.mos"
        # stdout file written
        stdout_path = cfg.work_dir / "test_0001" / "omc_stdout.txt"
        assert stdout_path.exists()
        assert "record SimulationResult" in stdout_path.read_text()
        # .mos written
        mos_path = cfg.work_dir / "test_0001" / "simulate.mos"
        assert mos_path.exists()
        assert "simulate(Lib.A" in mos_path.read_text()
        # run_result reflects parsed timing
        assert result.success is True
        assert result.translation_wall == pytest.approx(
            0.1 + 0.05 + 0.01 + 0.02 + 0.9,
        )
        assert result.sim_wall == pytest.approx(0.03)
        assert result.statistics["timing"]["total"] == pytest.approx(1.11)

    def test_run_single_test_surfaces_failure(self, tmp_path, monkeypatch):
        from dstf.backends.openmodelica.backend import (
            OpenModelicaBackend,
        )

        (tmp_path / "package.mo").write_text("package Lib end Lib;")
        cfg = Config(
            source_path=tmp_path,
            reference_root=tmp_path / "refs",
            backend_name="OpenModelica",
            work_dir=tmp_path / "work",
        )
        backend = OpenModelicaBackend(cfg)
        test = TestModel(
            model_id="Lib.DoesNotExist",
            source_file=Path(""),
            source_package="Lib",
            short_name="DoesNotExist",
            n_vars=0,
            variable_patterns=[],
            stop_time=1.0,
            tolerance=1e-6,  # explicit: direct construction bypasses discovery defaults (review 2026-07-06, finding 13)
        )
        stdout_synth = (
            "Error: Class Lib.DoesNotExist not found.\n"
            "record SimulationResult\n"
            '    resultFile = "",\n'
            '    simulationOptions = "...",\n'
            '    messages = "Simulation Failed. Model: Lib.DoesNotExist does not exist!",\n'
            "    timeFrontend = 0.0,\n"
            "    timeBackend = 0.0,\n"
            "    timeSimCode = 0.0,\n"
            "    timeTemplates = 0.0,\n"
            "    timeCompile = 0.0,\n"
            "    timeSimulation = 0.0,\n"
            "    timeTotal = 0.0\n"
            "end SimulationResult;\n"
        )

        def fake_run(cmd, cwd, capture_output, text, timeout, **kwargs):
            # **kwargs absorbs encoding/errors (review 2026-07-06, finding 75)
            return CompletedProcess(
                args=cmd, returncode=0, stdout=stdout_synth, stderr=""
            )

        monkeypatch.setattr(
            "dstf.backends.openmodelica.backend.subprocess.run",
            fake_run,
        )

        result = backend.run_single_test(test, test_key="test_0001", index=1, total=1)
        assert result.success is False
        assert result.error_message
        low = result.error_message.lower()
        assert "not found" in low or "does not exist" in low


# ---------------------------------------------------------------------------
# A timed-out run read back by a later process (no omc needed)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rows", "what_the_file_is"),
    [
        (None, "Result file not found: {mat}"),
        (0, "Empty result: result_res.mat holds no time point"),
        (12, "Partial result: result_res.mat holds 12 time"),
    ],
    ids=["no file", "its header only", "12 of 41 rows"],
)
def test_a_timed_out_run_reads_back_as_timed_out_whatever_its_file_holds(
    tmp_path, rows, what_the_file_is
):
    """`dstf compare`, or the tool started again, after a run its timeout
    killed: the manifest's verdict leads, then what the file is. With no
    file, or one killed before its first row, the message said the file's
    state alone — nothing of the timeout."""
    import json

    import numpy as np

    from conftest import make_test_model
    from dstf.backends.openmodelica.backend import OpenModelicaBackend
    from mat4_files import write_result_mat

    (tmp_path / "package.mo").write_text("package Lib end Lib;")
    work = tmp_path / "work"
    cfg = Config(
        source_path=tmp_path,
        reference_root=tmp_path / "refs",
        backend_name="OpenModelica",
        work_dir=work,
    )
    backend = OpenModelicaBackend(cfg)
    backend.quiet = True
    test_dir = work / "test_0001"
    test_dir.mkdir(parents=True)
    (work / "batch_manifest.json").write_text(
        json.dumps({"test_0001": {"model_id": "Lib.A", "last_run_status": "timed_out"}})
    )
    mat = test_dir / "result_res.mat"
    t = np.linspace(0.0, 4.0, 41)
    if rows is not None:
        write_result_mat(
            mat,
            tool="openmodelica",
            dtype="<f8",
            time=t,
            trajectories={"x": t},
            points=rows,
            declared=0,
        )
    test = make_test_model("Lib.A", stop_time=4.0, variable_patterns=["x"])

    result = backend.read_last_results([test])["Lib.A"]

    assert result.success is False
    assert len(result.variables) == (1 if rows else 0)
    said = "Run recorded timed_out — " + what_the_file_is.format(mat=mat)
    assert result.error_message.startswith(said), result.error_message


# ---------------------------------------------------------------------------
# Integration tests (real omc; skipped if not installed)
# ---------------------------------------------------------------------------

omc_unavailable = pytest.mark.skipif(
    shutil.which("omc") is None,
    reason="omc not installed — integration tests skipped",
)


@omc_unavailable
class TestOpenModelicaIntegration:
    def test_msl_only_smoke(self, tmp_path):
        """End-to-end: MSL-only model via loadModel, real omc."""
        from dstf.backends.openmodelica.backend import (
            OpenModelicaBackend,
        )

        (tmp_path / "package.mo").write_text("package EmptyLib end EmptyLib;")

        cfg = Config(
            source_path=tmp_path,
            reference_root=tmp_path / "refs",
            backend_name="OpenModelica",
            work_dir=tmp_path / "work",
            dependencies=["Modelica"],
            timeout=120,
        )
        backend = OpenModelicaBackend(cfg)
        test = TestModel(
            model_id="Modelica.Blocks.Examples.PID_Controller",
            source_file=Path(""),
            source_package="Modelica.Blocks.Examples",
            short_name="PID_Controller",
            n_vars=0,
            variable_patterns=["inertia1.phi"],
            stop_time=1.0,
            tolerance=1e-6,
            number_of_intervals=50,
            method="dassl",
            source="spec",
        )
        run_result = backend.run_single_test(
            test,
            test_key="test_0001",
            index=1,
            total=1,
        )
        assert run_result.success is True, run_result.error_message
        assert run_result.translation_wall is not None
        assert run_result.sim_wall is not None

        test_result = backend.read_result(test, "test_0001", run_result)
        assert test_result.success is True
        var_names = [v.name for v in test_result.variables]
        assert "inertia1.phi" in var_names

    def test_variable_filter_shrinks_mat(self, tmp_path):
        """variableFilter keeps the MAT small (one var request ⇒ few names)."""
        from dstf.backends.common.mat_reader import (
            list_result_mat_variables,
        )
        from dstf.backends.openmodelica.backend import (
            OpenModelicaBackend,
        )

        (tmp_path / "package.mo").write_text("package EmptyLib end EmptyLib;")
        cfg = Config(
            source_path=tmp_path,
            reference_root=tmp_path / "refs",
            backend_name="OpenModelica",
            work_dir=tmp_path / "work",
            dependencies=["Modelica"],
            timeout=120,
        )
        backend = OpenModelicaBackend(cfg)
        test = TestModel(
            model_id="Modelica.Blocks.Examples.PID_Controller",
            source_file=Path(""),
            source_package="Modelica.Blocks.Examples",
            short_name="PID_Controller",
            n_vars=0,
            variable_patterns=["inertia1.phi"],
            stop_time=0.5,
            tolerance=1e-6,  # explicit: direct construction bypasses discovery defaults (review 2026-07-06, finding 13)
            number_of_intervals=20,
            source="spec",
        )
        rr = backend.run_single_test(test, test_key="t", index=1, total=1)
        assert rr.success is True, rr.error_message

        mat = cfg.work_dir / "t" / OpenModelicaBackend.RESULT_MAT_FILENAME
        names = list_result_mat_variables(mat)
        assert names is not None
        # Without the filter, PID_Controller's MAT has 1000+ vars (derivatives,
        # aliases, parameters). With the filter, should be tens at most.
        assert len(names) < 200, f"variableFilter under-effective: {len(names)} vars"

    def test_missing_model_surfaces_clear_error(self, tmp_path):
        from dstf.backends.openmodelica.backend import (
            OpenModelicaBackend,
        )

        (tmp_path / "package.mo").write_text("package EmptyLib end EmptyLib;")
        cfg = Config(
            source_path=tmp_path,
            reference_root=tmp_path / "refs",
            backend_name="OpenModelica",
            work_dir=tmp_path / "work",
            dependencies=[],
            timeout=30,
        )
        backend = OpenModelicaBackend(cfg)
        test = TestModel(
            model_id="Definitely.Not.Here",
            source_file=Path(""),
            source_package="Definitely.Not",
            short_name="Here",
            n_vars=0,
            variable_patterns=[],
            stop_time=1.0,
            tolerance=1e-6,  # explicit: direct construction bypasses discovery defaults (review 2026-07-06, finding 13)
            source="spec",
        )
        rr = backend.run_single_test(test, test_key="t", index=1, total=1)
        assert rr.success is False
        assert rr.error_message
        low = rr.error_message.lower()
        assert "here" in low or "does not exist" in low or "not found" in low

    def test_reading_captured_mat_fixture(self):
        """Regression: common.mat_reader handles an OM-written MAT.

        The reader embeds the time array as the first element of each
        variable's ``(time, values)`` tuple, NOT as a standalone key named
        ``time`` — same as Dymola's behavior. So we probe via a real
        variable the fixture's variableFilter allowed through.
        """
        from dstf.backends.common.mat_reader import (
            list_result_mat_variables,
            read_result_mat,
        )

        fixture = FIXTURES / "pid_controller_res.mat"
        names = list_result_mat_variables(fixture)
        assert names is not None
        # The fixture was captured with variableFilter = "^(time|inertia1.phi)$"
        # Names exposed by the reader include 'time' + the requested variable.
        assert "time" in names
        assert "inertia1.phi" in names
        data = read_result_mat(fixture)
        assert data is not None
        assert "inertia1.phi" in data
        t_arr, v_arr = data["inertia1.phi"]
        assert len(t_arr) > 2
        assert len(t_arr) == len(v_arr)
