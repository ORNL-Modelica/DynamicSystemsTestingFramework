"""Tests for cross-backend chain orchestration (4.B.3).

The Dymola export step is mocked (Linux WSL has no Dymola); the FMPy half
runs against a real Reference-FMU when available.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dstf.discovery.test_registry import TestModel

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


pytestmark = [
    pytest.mark.skipif(
        not _fmpy_available() or not _reference_fmus_available(),
        reason="Requires fmpy + Reference-FMUs binaries",
    ),
]


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

    # Mock primary runner: its export_fmu just returns the staged FMU path.
    primary_runner = MagicMock()
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

    # Primary ref file is unchanged by soft_check writes — no legacy
    # flat 'baselines' dict expected.
    ref = store.get_reference("BouncingBall")
    assert ref is not None
    assert "baselines" not in ref or not ref["baselines"]


def test_chain_returns_false_when_export_fails(tmp_path):
    """When primary backend can't export FMU, chain logs + returns False."""
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
    primary_runner.export_fmu.side_effect = NotImplementedError(
        "no FMI export option in license"
    )

    test = _make_chain_test()
    ok = produce_dymola_via_fmpy_baseline(test, primary_runner, config, store)
    assert ok is False
