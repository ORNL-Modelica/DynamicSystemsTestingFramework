"""Tests for DymolaBackend.export_fmu (4.B.2).

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

from conftest import make_test_model as _tm
from dstf.backends.dymola.persistent_backend import DymolaWorker
from dstf.discovery.test_registry import TestModel


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
        backend_path=None,
        dependencies=[],
        library_dir=tmp_path,
    )
    dymola_config = SimpleNamespace(
        show_ide=False,
        backend_setup=[],
        diagnostic_variables=["CPUtime", "EventCounter"],
    )
    worker = DymolaWorker(
        worker_id=99,
        config=config,
        dymola_config=dymola_config,
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
        worker.dymola.translateModelFMU.side_effect = RuntimeError(
            "fmi-export not licensed"
        )
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
        from dstf.backends.fmpy.backend import FmpyBackend

        # FmpyBackend doesn't declare FMU_EXPORT — base default applies
        config = SimpleNamespace(
            source_type="fmu",
            backend_name="FMPy",
            parallel=1,
            timeout=60,
            work_dir=tmp_path,
        )
        # Skip if fmpy not installed — instantiation would fail before our
        # test. The constructor signals that with the Backend contract's
        # RuntimeError, not an ImportError.
        try:
            backend = FmpyBackend(config)  # type: ignore[arg-type]
        except RuntimeError:
            pytest.skip("fmpy not installed")
        with pytest.raises(NotImplementedError, match="FMU_EXPORT"):
            backend.export_fmu(_mock_test(), tmp_path)


# =========================================================================
# Folded in from tests/test_review_fixes_backends.py — 2026-07-06 review,
# backend fixes (findings 20-29 and 73-75, CODE_REVIEW_2026-07-06.md).
#
# Finding 76-part — the one-shot export worker is torn down through the
# guarded ``worker.close()`` hard-kill path, not ``worker.dymola.close()``.
# =========================================================================


class TestExportFmuCleanup:
    def test_one_shot_worker_closed_via_guarded_close(self, tmp_path, monkeypatch):
        from dstf.backends.dymola import persistent_backend as pr

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
            backend_setup=[],
            diagnostic_variables=[],
            work_dir=tmp_path,
            dymola_interface_path=None,
            backend_path=None,
            dependencies=[],
            library_dir=tmp_path,
        )
        backend = pr.PersistentDymolaBackend(cfg)
        out = tmp_path / "out"
        out.mkdir()
        backend.export_fmu(_tm("M.X"), out)

        assert created, "worker was never started"
        assert created[0] in closed, (
            "export_fmu cleanup must call worker.close() (guarded hard-kill "
            "path), not worker.dymola.close()"
        )
