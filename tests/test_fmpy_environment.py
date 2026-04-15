"""Phase 2.1 sanity tests: FMPy dependency + Reference-FMUs submodule are wired.

These tests prove only that the environment is set up — not that the FMPy
backend runs. They skip cleanly when fmpy isn't installed or when the
submodule hasn't been initialized, so the main test suite passes either way.

Run just these tests:
    uv run pytest tests/test_fmpy_environment.py -v
"""

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
REFERENCE_FMUS_DIR = PROJECT_ROOT / "examples" / "fmu" / "reference-fmus"


def _fmpy_available() -> bool:
    try:
        import fmpy  # noqa: F401
        return True
    except ImportError:
        return False


def _reference_fmus_available() -> bool:
    """Submodule initialized iff the directory exists and is non-empty."""
    return REFERENCE_FMUS_DIR.exists() and any(REFERENCE_FMUS_DIR.iterdir())


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
    reason="Reference-FMUs submodule not initialized (run: git submodule update --init examples/fmu/reference-fmus)",
)
def test_reference_fmus_submodule_present():
    """Submodule directory exists and looks like the Reference-FMUs repo."""
    # The repo's README is the stable top-level marker
    readme = REFERENCE_FMUS_DIR / "README.md"
    assert readme.exists(), (
        f"Expected {readme} — submodule may be empty. "
        "Run: git submodule update --init examples/fmu/reference-fmus"
    )


@pytest.mark.fmpy
@pytest.mark.reference_fmus
@pytest.mark.skipif(
    not _fmpy_available() or not _reference_fmus_available(),
    reason="fmpy and Reference-FMUs submodule both required",
)
def test_fmpy_can_read_bouncing_ball():
    """End-to-end wiring check: fmpy reads BouncingBall's model description.

    This is the minimum viable "FMPy + fixtures wired" proof. No simulation
    yet — that's Phase 2.3.
    """
    import fmpy

    # Reference-FMUs lays out pre-built FMUs under a deterministic path.
    # FMI 2.0 CoSimulation is the most broadly supported starting point.
    candidates = list(REFERENCE_FMUS_DIR.rglob("BouncingBall.fmu"))
    assert candidates, (
        f"No BouncingBall.fmu found under {REFERENCE_FMUS_DIR}. "
        "The submodule may be on an unexpected layout — inspect and update this test."
    )

    fmu_path = candidates[0]
    md = fmpy.read_model_description(fmu_path)
    assert md is not None
    assert md.modelName.lower().startswith("bouncingball") or "BouncingBall" in md.modelName
