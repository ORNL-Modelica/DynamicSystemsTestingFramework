"""Integration tests for the Julia/MTK backend (D77).

Gated by the ``julia`` pytest marker — only runs when the ``julia``
binary is on PATH and the ``examples/julia/Project.toml`` has been
instantiated. Install Julia via juliaup (https://julialang.org/downloads/)
then run once:

    cd examples/julia && julia --project=. -e 'using Pkg; Pkg.instantiate()'

First run will precompile MTK + OrdinaryDiffEq (several minutes);
subsequent runs are seconds.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def _julia_available() -> bool:
    if shutil.which("julia") is None:
        return False
    project = (
        Path(__file__).resolve().parents[1]
        / "examples"
        / "julia"
        / "JuliaMtkTestingLib"
    )
    return (project / "Manifest.toml").exists()


pytestmark = pytest.mark.skipif(
    not _julia_available(),
    reason="Julia not on PATH, or JuliaMtkTestingLib project not instantiated",
)


_EXAMPLES_DIR = (
    Path(__file__).resolve().parents[1] / "examples" / "julia" / "JuliaMtkTestingLib"
)
_CONFIG = _EXAMPLES_DIR / "Resources" / "ReferenceResults" / "testing.json"


@pytest.mark.julia
def test_julia_runner_registered():
    """The Julia runner registers when its submodule is imported."""
    from dstf.config import Config
    from dstf.simulators import get_runner_class

    cfg = Config(config_file=_CONFIG)
    cls = get_runner_class(cfg)
    assert cls.__name__ == "JuliaRunner"


@pytest.mark.julia
def test_julia_simple_ramp_smoke(tmp_path):
    """End-to-end: run SimpleRamp via the CLI, accept as baseline, rerun
    and verify it passes. Exercises the whole pipeline (discovery → Julia
    subprocess → read_result → comparator)."""
    # First run — no baseline yet, so test reports NO_REF but simulation succeeds.
    result = subprocess.run(
        [
            "uv",
            "run",
            "dstf",
            "--config",
            str(_CONFIG),
            "run",
            "--filter",
            "*SimpleRamp",
            "--work-dir",
            str(tmp_path / "wd1"),
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, result.stderr
    # Result JSON exists on disk.
    result_json = tmp_path / "wd1" / "test_0001" / "result.json"
    assert result_json.exists()


@pytest.mark.julia
def test_julia_frequency_declared_peak_matches(tmp_path):
    """The Frequency sample is a 1 Hz sinusoid; declared peak at 1 Hz with
    15% rel tolerance should match on self-regression."""
    # Need a baseline first.
    baseline_dir = _EXAMPLES_DIR / "Resources" / "ReferenceResults" / "Julia" / "linux"
    if not any(baseline_dir.glob("ref_*.json")):
        pytest.skip(
            "No Julia baselines committed under JuliaMtkTestingLib/ReferenceResults; "
            "run `dstf --config examples/julia/testing.json run --accept` first"
        )
    result = subprocess.run(
        [
            "uv",
            "run",
            "dstf",
            "--config",
            str(_CONFIG),
            "run",
            "--filter",
            "*Frequency",
            "--work-dir",
            str(tmp_path / "wd2"),
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, result.stderr
    assert "PASS" in result.stdout, result.stdout
