"""Phase 2.3 end-to-end: FmpyRunner simulates a Reference-FMU.

Verifies the actual simulation path — not just registration. Skipped cleanly
if fmpy isn't installed or the Reference-FMUs binaries haven't been fetched.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

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


# Gate the entire module on the two dependencies being present.
pytestmark = [
    pytest.mark.fmpy,
    pytest.mark.reference_fmus,
    pytest.mark.skipif(
        not _fmpy_available() or not _reference_fmus_available(),
        reason=(
            "Requires fmpy installed + Reference-FMUs fetched "
            "(run: uv run python scripts/fetch_reference_fmus.py)"
        ),
    ),
]


def _make_config(tmp_path: Path) -> SimpleNamespace:
    """Minimal config stand-in — the runner only reads work_dir + progress."""
    return SimpleNamespace(
        work_dir=tmp_path,
        progress=None,
        source_type="fmu",
        simulator="FMPy",
        parallel=1,
        timeout=60,
    )


def _make_test(fmu_path: Path, variables: list[str], stop_time: float = 3.0):
    """Build a TestModel pointing at an FMU with the given output variables."""
    from modelica_testing.discovery.test_registry import TestModel

    return TestModel(
        model_id=fmu_path.stem,
        mo_file=fmu_path,            # FMPy backend reads .fmu path from here
        package_path="",
        short_name=fmu_path.stem,
        n_vars=len(variables),
        variable_patterns=variables,
        stop_time=stop_time,
        tolerance=1e-6,
        method="Dassl",              # gets mapped to CVode by FmpyRunner
        source="spec",
    )


# ---------------------------------------------------------------------------
# Single-FMU simulation
# ---------------------------------------------------------------------------

class TestFmpySimulation:
    def test_bouncing_ball_simulates_end_to_end(self, tmp_path):
        """BouncingBall simulates, result round-trips through save/load.

        BouncingBall is the canonical FMI test model: a ball bounces until its
        energy is dissipated. We track `h` (height) — must start at ~1 and
        eventually settle near 0.
        """
        from modelica_testing.simulators.fmpy import FmpyRunner

        fmu = REFERENCE_FMUS_DIR / "2.0" / "BouncingBall.fmu"
        test = _make_test(fmu, variables=["h"], stop_time=3.0)
        config = _make_config(tmp_path)

        runner = FmpyRunner(config)  # type: ignore[arg-type]
        run_result = runner.run_single_test(test, "test_0001", index=1, total=1)

        assert run_result.success, f"Simulation failed: {run_result.error_message}"
        assert run_result.elapsed > 0
        assert (tmp_path / "test_0001" / "result.npz").exists()

        # Read back
        result = runner.read_result(test, "test_0001", run_result)
        assert result.success
        assert len(result.variables) == 1
        h = result.variables[0]
        assert h.name == "h"
        assert len(h.time) > 10  # CVode with default output should produce many points
        assert h.time[0] == pytest.approx(0.0)
        assert h.time[-1] == pytest.approx(3.0, abs=0.05)
        # Physical sanity: ball starts above 0, settles toward 0
        assert h.values[0] > 0.5
        assert abs(h.values[-1]) < h.values[0]

    def test_wildcard_variables(self, tmp_path):
        """``variables=['*']`` records all FMU outputs."""
        from modelica_testing.simulators.fmpy import FmpyRunner

        fmu = REFERENCE_FMUS_DIR / "2.0" / "VanDerPol.fmu"
        test = _make_test(fmu, variables=["*"], stop_time=5.0)
        config = _make_config(tmp_path)

        runner = FmpyRunner(config)  # type: ignore[arg-type]
        run_result = runner.run_single_test(test, "test_0001", index=1, total=1)
        assert run_result.success

        result = runner.read_result(test, "test_0001", run_result)
        assert result.success
        # VanDerPol has at least x0 + x1 as states; expect at least 2 tracked
        names = [v.name for v in result.variables]
        assert len(names) >= 2, f"Expected ≥2 variables under '*', got {names}"

    def test_missing_fmu_reports_failure_gracefully(self, tmp_path):
        """A non-existent FMU produces a clean failure, not an exception."""
        from modelica_testing.simulators.fmpy import FmpyRunner

        test = _make_test(Path("/nonexistent/fake.fmu"), variables=["h"])
        config = _make_config(tmp_path)

        runner = FmpyRunner(config)  # type: ignore[arg-type]
        run_result = runner.run_single_test(test, "test_0001", index=1, total=1)

        assert not run_result.success
        assert "not found" in (run_result.error_message or "").lower()

    def test_result_includes_time_column_matching_variables(self, tmp_path):
        """Time vector and per-variable values are the same length."""
        from modelica_testing.simulators.fmpy import FmpyRunner

        fmu = REFERENCE_FMUS_DIR / "2.0" / "Dahlquist.fmu"
        test = _make_test(fmu, variables=["x"], stop_time=2.0)
        config = _make_config(tmp_path)

        runner = FmpyRunner(config)  # type: ignore[arg-type]
        runner.run_single_test(test, "test_0001", index=1, total=1)
        result = runner.read_result(
            test, "test_0001",
            run_result=None,  # type: ignore[arg-type]
        )
        assert result.success
        x = result.variables[0]
        assert len(x.time) == len(x.values)
        assert x.time.dtype == np.float64
        assert x.values.dtype == np.float64


# ---------------------------------------------------------------------------
# Helper-function tests (no FMU needed)
# ---------------------------------------------------------------------------

class TestFmpyHelpers:
    def test_save_load_roundtrip(self, tmp_path):
        """Structured-array persistence preserves column names + data."""
        from modelica_testing.simulators.fmpy.runner import _load_result, _save_result

        arr = np.zeros(5, dtype=[("time", "f8"), ("h", "f8"), ("v", "f8")])
        arr["time"] = [0.0, 0.1, 0.2, 0.3, 0.4]
        arr["h"] = [1.0, 0.9, 0.8, 0.7, 0.6]
        arr["v"] = [0.0, -1.0, -2.0, -3.0, -4.0]

        path = tmp_path / "result.npz"
        _save_result(path, arr)
        loaded = _load_result(path)

        assert loaded.dtype.names == ("time", "h", "v")
        np.testing.assert_array_equal(loaded["h"], arr["h"])
        np.testing.assert_array_equal(loaded["time"], arr["time"])

    def test_resolve_requested_outputs_wildcard(self):
        """``['*']`` expands to the full available list."""
        from modelica_testing.simulators.fmpy.runner import _resolve_requested_outputs

        test = _make_test(Path("x.fmu"), variables=["*"])
        available = ["h", "v", "m"]
        assert _resolve_requested_outputs(test, available) == available

    def test_resolve_requested_outputs_empty(self):
        """No patterns means no comparison variables (simulate-only)."""
        from modelica_testing.simulators.fmpy.runner import _resolve_requested_outputs

        test = _make_test(Path("x.fmu"), variables=[])
        assert _resolve_requested_outputs(test, ["h", "v"]) == []
