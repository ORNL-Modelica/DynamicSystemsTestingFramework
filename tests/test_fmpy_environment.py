"""Phase 2.1 sanity tests: FMPy dependency + Reference-FMUs binaries are wired.

These tests prove only that the environment is set up — not that the FMPy
backend runs. They skip cleanly when fmpy isn't installed or when the
Reference-FMUs haven't been fetched, so the main test suite passes either way.

Run just these tests:
    uv run pytest tests/test_fmpy_environment.py -v
"""

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
REFERENCE_FMUS_DIR = PROJECT_ROOT / "examples" / "fmu" / "reference-fmus-binaries"

_FETCH_HINT = "uv run python scripts/fetch_reference_fmus.py"


def _fmpy_available() -> bool:
    try:
        import fmpy  # noqa: F401
        return True
    except ImportError:
        return False


def _reference_fmus_available() -> bool:
    """Binaries fetched iff the directory exists and contains FMUs."""
    return REFERENCE_FMUS_DIR.exists() and any(REFERENCE_FMUS_DIR.rglob("*.fmu"))


@pytest.mark.fmpy
@pytest.mark.skipif(not _fmpy_available(), reason="fmpy not installed")
def test_fmpy_importable():
    """fmpy package imports and exposes expected API surface."""
    import fmpy

    # Minimum API we'll rely on in the runner
    assert hasattr(fmpy, "read_model_description")
    assert hasattr(fmpy, "simulate_fmu")


@pytest.mark.reference_fmus
@pytest.mark.skipif(
    not _reference_fmus_available(),
    reason=f"Reference-FMUs binaries not fetched (run: {_FETCH_HINT})",
)
def test_reference_fmus_fetched():
    """Binaries directory contains FMI 2.0 + 3.0 FMUs from the release ZIP."""
    # FMI 2.0 and 3.0 subdirs are always present after a successful fetch
    fmi_2 = REFERENCE_FMUS_DIR / "2.0"
    fmi_3 = REFERENCE_FMUS_DIR / "3.0"
    assert fmi_2.exists() and any(fmi_2.glob("*.fmu")), (
        f"Expected FMI 2.0 FMUs under {fmi_2} — run: {_FETCH_HINT}"
    )
    assert fmi_3.exists() and any(fmi_3.glob("*.fmu")), (
        f"Expected FMI 3.0 FMUs under {fmi_3} — run: {_FETCH_HINT}"
    )


@pytest.mark.fmpy
@pytest.mark.reference_fmus
@pytest.mark.skipif(
    not _fmpy_available() or not _reference_fmus_available(),
    reason="fmpy and Reference-FMUs binaries both required",
)
def test_fmpy_can_read_bouncing_ball():
    """End-to-end wiring check: fmpy reads BouncingBall's model description.

    This is the minimum viable "FMPy + fixtures wired" proof. No simulation
    yet — that's Phase 2.3.
    """
    import fmpy

    # Prefer FMI 2.0 — broadest FMPy support.
    fmu_path = REFERENCE_FMUS_DIR / "2.0" / "BouncingBall.fmu"
    assert fmu_path.exists(), (
        f"Expected {fmu_path} — run: {_FETCH_HINT}"
    )

    md = fmpy.read_model_description(fmu_path)
    assert md is not None
    assert "BouncingBall" in md.modelName or md.modelName.lower().startswith("bouncingball")


# ---------------------------------------------------------------------------
# Phase 2.2: backend registration + routing
# ---------------------------------------------------------------------------

@pytest.mark.fmpy
@pytest.mark.skipif(not _fmpy_available(), reason="fmpy not installed")
def test_fmpy_backend_registers_under_name():
    """``get_runner`` with ``simulator='FMPy'`` returns an FmpyRunner.

    Proves the registry + config._detect_backend + lazy-import wiring is
    correct end-to-end. Does not attempt simulation (Phase 2.3).
    """
    from modelica_testing.simulators import get_runner
    from modelica_testing.simulators.fmpy import FmpyRunner

    # Minimal config that doesn't need a real library — we only test the factory
    class _FakeConfig:
        simulator = "FMPy"
        source_type = "fmu"

        @property
        def simulator_backend(self):
            return "FMPy"

    runner = get_runner(_FakeConfig())
    assert isinstance(runner, FmpyRunner)
    # Capabilities declared as advertised in docs/extensibility.md §3
    from modelica_testing.simulators.base import Capability, DatasetType
    assert Capability.PERSISTENT_WORKERS in runner.capabilities
    assert Capability.BATCH_FALLBACK not in runner.capabilities
    assert Capability.FMU_EXPORT not in runner.capabilities
    assert runner.produced_datasets == frozenset({DatasetType.TIME_SERIES})


