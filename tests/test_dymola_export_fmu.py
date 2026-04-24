"""Tests for DymolaRunner.export_fmu (4.B.2).

VALIDATION CAVEAT: the real export_fmu requires Windows + Dymola + the FMI
export license option. These tests use a mock DymolaInterface to verify the
plumbing — that translateModelFMU is invoked with the right arguments and
the returned path is resolved correctly. Real-Dymola end-to-end validation
must happen on the user's Windows machine.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from dstf.discovery.test_registry import TestModel
from dstf.simulators.dymola.persistent_runner import DymolaWorker


def _mock_test() -> TestModel:
    return TestModel(
        model_id="MyLib.Tests.Foo",
        source_file=Path(""),
        source_package="MyLib.Tests",
        short_name="Foo",
        n_vars=1,
    )


def _make_worker(tmp_path: Path) -> DymolaWorker:
    """Worker with a mock dymola already attached (no real start())."""
    config = SimpleNamespace(
        work_dir=tmp_path,
        simulator_path=None,
        dependencies=[],
        library_dir=tmp_path,
    )
    dymola_config = SimpleNamespace(
        show_ide=False,
        simulator_setup=[],
        diagnostic_variables=["CPUtime", "EventCounter"],
    )
    worker = DymolaWorker(
        worker_id=99, config=config, dymola_config=dymola_config,
        dymola_interface_cls=MagicMock(),
    )
    worker.dymola = MagicMock()  # bypass start()
    return worker


class TestExportFmu:
    def test_calls_translate_model_fmu_with_expected_args(self, tmp_path):
        worker = _make_worker(tmp_path)
        # Dymola returns the basename without .fmu — produce the file too
        # so the existence check passes.
        fmu_basename = "MyLib_Tests_Foo"
        worker.dymola.translateModelFMU.return_value = fmu_basename
        out = tmp_path / "exports"
        out.mkdir()
        (out / f"{fmu_basename}.fmu").write_bytes(b"FMI mock")

        path = worker.export_fmu(_mock_test(), out)

        worker.dymola.cd.assert_called_with(str(out))
        worker.dymola.translateModelFMU.assert_called_once()
        call = worker.dymola.translateModelFMU.call_args
        assert call.args[0] == "MyLib.Tests.Foo"
        assert call.kwargs["fmiVersion"] == "2"
        assert call.kwargs["fmiType"] == "all"
        assert path == (out / f"{fmu_basename}.fmu").resolve()

    def test_falls_back_to_glob_when_basename_mismatch(self, tmp_path):
        worker = _make_worker(tmp_path)
        worker.dymola.translateModelFMU.return_value = "Wrong_Name"
        out = tmp_path / "exports"
        out.mkdir()
        # Dymola actually wrote a file with a different name (sanitization)
        actual = out / "RealName.fmu"
        actual.write_bytes(b"FMI mock")

        path = worker.export_fmu(_mock_test(), out)
        assert path == actual.resolve()

    def test_raises_when_dymola_returns_empty(self, tmp_path):
        worker = _make_worker(tmp_path)
        worker.dymola.translateModelFMU.return_value = ""
        out = tmp_path / "exports"
        out.mkdir()
        with pytest.raises(RuntimeError, match="returned no path"):
            worker.export_fmu(_mock_test(), out)

    def test_raises_when_no_fmu_produced(self, tmp_path):
        worker = _make_worker(tmp_path)
        worker.dymola.translateModelFMU.return_value = "GhostName"
        out = tmp_path / "exports"
        out.mkdir()
        # Dymola "succeeded" but no .fmu exists on disk
        with pytest.raises(RuntimeError, match="no .fmu found"):
            worker.export_fmu(_mock_test(), out)

    def test_raises_clearly_on_dymola_exception(self, tmp_path):
        worker = _make_worker(tmp_path)
        worker.dymola.translateModelFMU.side_effect = RuntimeError("fmi-export not licensed")
        out = tmp_path / "exports"
        out.mkdir()
        with pytest.raises(RuntimeError, match="translateModelFMU failed"):
            worker.export_fmu(_mock_test(), out)

    def test_raises_when_worker_not_started(self, tmp_path):
        worker = _make_worker(tmp_path)
        worker.dymola = None  # never started
        with pytest.raises(RuntimeError, match="cannot export FMU before start"):
            worker.export_fmu(_mock_test(), tmp_path)


class TestAbstractExportFmuDefault:
    """4.B.1 — base class default raises NotImplementedError."""

    def test_default_raises_with_helpful_message(self, tmp_path):
        from dstf.simulators.fmpy.runner import FmpyRunner
        # FmpyRunner doesn't declare FMU_EXPORT — base default applies
        config = SimpleNamespace(
            source_type="fmu", simulator="FMPy", parallel=1, timeout=60,
            work_dir=tmp_path,
        )
        # Skip if fmpy not installed — instantiation would fail before our test
        try:
            runner = FmpyRunner(config)  # type: ignore[arg-type]
        except ImportError:
            pytest.skip("fmpy not installed")
        with pytest.raises(NotImplementedError, match="FMU_EXPORT"):
            runner.export_fmu(_mock_test(), tmp_path)
