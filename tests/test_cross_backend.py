"""Tests for cross-backend chain orchestration (4.B.3).

The Dymola export step is mocked (Linux WSL has no Dymola); the FMPy half
runs against a real Reference-FMU when available.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from conftest import make_test_model as _tm
from dstf.discovery.test_registry import TestModel
from dstf.simulators.types import Capability, TestRunResult

PROJECT_ROOT = Path(__file__).parent.parent
REFERENCE_FMUS_DIR = PROJECT_ROOT / "examples" / "fmu" / "reference-fmus-binaries"


def _fmpy_available() -> bool:
    try:
        import fmpy  # noqa: F401

        return True
    except ImportError:
        return False


def _reference_fmus_available() -> bool:
    return REFERENCE_FMUS_DIR.exists() and any(REFERENCE_FMUS_DIR.rglob("*.fmu"))


# Carried by the two chain tests rather than the module: the soft-check
# regression folded in at the bottom drives a fake FmpyRunner and needs
# neither fmpy nor the binaries.
_needs_real_fmpy = pytest.mark.skipif(
    not _fmpy_available() or not _reference_fmus_available(),
    reason="Requires fmpy + Reference-FMUs binaries",
)


def _seed_primary_baseline(store_dir: Path, model_id: str) -> None:
    """Drop a minimal primary baseline file so soft_check writes succeed."""
    sim_dir = store_dir / "FMPy" / "linux"  # matches Config.reference_dir layout
    sim_dir.mkdir(parents=True, exist_ok=True)
    ref = {
        "test_id": "0001",
        "model_id": model_id,
        "n_vars": 1,
        "time": [0.0, 1.0],
        "variables": [{"index": 1, "name": "h", "values": [1.0, 0.5]}],
    }
    (sim_dir / "ref_0001.json").write_text(
        json.dumps(ref, indent=2),
        encoding="utf-8",
    )


def _make_chain_test(model_id: str = "BouncingBall") -> TestModel:
    return TestModel(
        model_id=model_id,
        source_file=Path(""),  # set by chain to the exported FMU
        source_package="",
        short_name=model_id,
        n_vars=1,
        variable_patterns=["h"],
        stop_time=1.0,
        tolerance=1e-6,
        method="Dassl",
    )


@_needs_real_fmpy
def test_chain_writes_named_baseline_with_mock_export(tmp_path):
    """Mock the export_fmu step (no Dymola available); the FMPy half is real.

    We hand the orchestrator a BouncingBall FMU as if Dymola exported it,
    then verify the FMPy result lands as a 'dymola-via-fmpy' baseline on
    the primary reference file.
    """
    from dstf.config import Config
    from dstf.simulators.cross_backend import (
        CROSS_BACKEND_BASELINE_NAME,
        produce_dymola_via_fmpy_baseline,
    )
    from dstf.storage.reference_store import ReferenceStore

    # Set up an FMU example directory + reference store
    fmu_examples_dir = tmp_path / "fmu_examples"
    fmu_examples_dir.mkdir()
    bouncing = REFERENCE_FMUS_DIR / "2.0" / "BouncingBall.fmu"
    if not bouncing.exists():
        pytest.skip("BouncingBall.fmu missing")
    # Stage a copy in the example directory so the Config has something to
    # treat as source_path
    shutil.copy(bouncing, fmu_examples_dir / "BouncingBall.fmu")

    store_root = tmp_path / "refs"
    _seed_primary_baseline(store_root, "BouncingBall")

    config = Config(
        source_path=fmu_examples_dir,
        reference_root=store_root,
        source_type="fmu",
        simulator="FMPy",
        work_dir=tmp_path / "work",
    )
    store = ReferenceStore(config)

    # Mock primary runner: it declares FMU_EXPORT (the chain gates on the
    # capability, not on export_fmu raising) and its export_fmu just returns
    # the staged FMU path.
    primary_runner = MagicMock()
    primary_runner.capabilities = frozenset({Capability.FMU_EXPORT})
    primary_runner.export_fmu.return_value = (
        fmu_examples_dir / "BouncingBall.fmu"
    ).resolve()

    test = _make_chain_test("BouncingBall")
    ok = produce_dymola_via_fmpy_baseline(test, primary_runner, config, store)
    assert ok is True

    # Verify the soft_check landed in the new on-disk layout (D66)
    soft_checks = store.get_soft_checks("BouncingBall")
    assert CROSS_BACKEND_BASELINE_NAME in soft_checks
    cb_soft_check = soft_checks[CROSS_BACKEND_BASELINE_NAME]
    assert cb_soft_check.time
    assert len(cb_soft_check.variables) >= 1
    assert cb_soft_check.provenance["secondary_backend"] == "FMPy"

    # Primary ref file is unchanged by soft_check writes.
    ref = store.get_reference("BouncingBall")
    assert ref is not None
    assert "baselines" not in ref or not ref["baselines"]


@_needs_real_fmpy
def test_chain_returns_false_when_export_fails(tmp_path):
    """A declared exporter whose export blows up: chain logs + returns False."""
    from dstf.config import Config
    from dstf.simulators.cross_backend import (
        produce_dymola_via_fmpy_baseline,
    )
    from dstf.storage.reference_store import ReferenceStore

    fmu_examples_dir = tmp_path / "fmu_examples"
    fmu_examples_dir.mkdir()
    # Need at least one FMU in the dir for source_type=fmu Config
    bouncing = REFERENCE_FMUS_DIR / "2.0" / "BouncingBall.fmu"
    if bouncing.exists():
        shutil.copy(bouncing, fmu_examples_dir / "BouncingBall.fmu")

    config = Config(
        source_path=fmu_examples_dir,
        reference_root=tmp_path / "refs",
        source_type="fmu",
        simulator="FMPy",
        work_dir=tmp_path / "work",
    )
    store = ReferenceStore(config)

    primary_runner = MagicMock()
    primary_runner.capabilities = frozenset({Capability.FMU_EXPORT})
    primary_runner.export_fmu.side_effect = RuntimeError(
        "no FMI export option in license"
    )

    test = _make_chain_test()
    ok = produce_dymola_via_fmpy_baseline(test, primary_runner, config, store)
    assert ok is False


# =========================================================================
# Folded in from tests/test_review_fixes_backends.py — 2026-07-06 review,
# backend fixes (findings 20-29 and 73-75, CODE_REVIEW_2026-07-06.md).
#
# Finding 76-part — a ValueError from ``add_soft_check`` (name collision) is
# logged and skipped, not raised out of the chain.
#
# This one drives a fake FmpyRunner and needs neither fmpy nor the
# Reference-FMU binaries, which is why the dependency gate moved from a
# module-level ``pytestmark`` onto the two chain tests that do need it.
# =========================================================================


class TestCrossBackendSoftCheckValueError:
    def test_valueerror_from_add_soft_check_is_logged_and_skipped(
        self, tmp_path, monkeypatch, caplog
    ):
        from dstf.config import Config
        from dstf.simulators.cross_backend import produce_dymola_via_fmpy_baseline
        from dstf.simulators.types import TestResult, VariableResult

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
        primary.capabilities = frozenset({Capability.FMU_EXPORT})
        primary.export_fmu.return_value = fmu_path

        store = MagicMock()
        store.add_soft_check.side_effect = ValueError("name collides with companion")

        test = _tm("Model", variable_patterns=["h"])
        with caplog.at_level(logging.WARNING):
            ok = produce_dymola_via_fmpy_baseline(test, primary, config, store)
        assert ok is False  # logged + skipped, not raised
        assert "cannot store baseline" in caplog.text
