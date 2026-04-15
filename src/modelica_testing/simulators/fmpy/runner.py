"""FMPy-backed simulator runner.

Consumes prebuilt FMUs via the FMPy Python library. Produces the same
``TestResult`` / ``VariableResult`` shape the Dymola backend produces, so the
comparator, storage, and reporting layers work unchanged.

Simulation flow per test (``run_single_test``):
  1. Resolve the FMU path from ``test.mo_file`` (spec_parser puts it there
     when the spec entry has an ``"fmu"`` field).
  2. Read the model description to discover scalar variables.
  3. Resolve ``test.variable_patterns`` against the declared names.
  4. Call ``fmpy.simulate_fmu`` with the resolved settings.
  5. Persist the returned structured array to ``<test_dir>/result.npz`` so a
     later ``compare`` pass can re-read without re-simulating — matches the
     on-disk pattern the Dymola backend uses for ``dsres.mat``.

``read_result`` loads the .npz and builds a ``TestResult``.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

import numpy as np

from ...config import Config
from ...discovery.test_registry import TestModel
from .. import register
from ..base import (
    Capability,
    DatasetType,
    SimulatorRunner,
    TestResult,
    TestRunResult,
    VariableResult,
    resolve_variable_patterns,
)

logger = logging.getLogger(__name__)


# FMPy solver names. Dymola's default ``Dassl`` maps to FMPy's ``CVode``
# (both BDF-family; closest semantic match). ``Euler`` is also available.
_SOLVER_MAP = {
    "Dassl": "CVode",
    "Cvode": "CVode",
    "CVode": "CVode",
    "Euler": "Euler",
}


@register("FMPy")
class FmpyRunner(SimulatorRunner):
    """Runs FMU simulations using the FMPy Python library.

    Consumes prebuilt FMUs — does not compile from Modelica sources. For
    Modelica → FMU conversion, a future ``supports_fmu_export`` capability
    on a Modelica backend (Dymola / OMC) will chain into this runner for
    cross-backend verification.
    """

    capabilities = frozenset({
        Capability.PERSISTENT_WORKERS,  # FMPy instances are cheap to keep alive
        # Deliberately absent:
        #   BATCH_FALLBACK — FMPy *is* the Python path; no script fallback exists
        #   FMU_EXPORT — FMPy consumes FMUs, doesn't produce them
        #   EXPERIMENT_INGEST — not applicable
    })
    produced_datasets = frozenset({DatasetType.TIME_SERIES})

    #: Filename used for the on-disk structured-array cache per test.
    RESULT_FILENAME = "result.npz"

    def __init__(self, config: Config):
        super().__init__(config)
        # Defer the fmpy import so a bare import of this module doesn't error
        # when fmpy isn't installed — the runner only errors if actually used.
        try:
            import fmpy  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "The FMPy backend requires the optional 'fmpy' extra: "
                "uv pip install -e \".[fmpy]\""
            ) from exc

    # ------------------------------------------------------------------
    # Simulation
    # ------------------------------------------------------------------

    def run_single_test(
        self,
        test: TestModel,
        test_key: str,
        index: int,
        total: int,
    ) -> TestRunResult:
        import fmpy

        fmu_path = _resolve_fmu_path(test)
        if fmu_path is None or not fmu_path.exists():
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                error_message=(
                    f"FMU file not found for {test.model_id}. "
                    f"Ensure the test_spec.json entry has an 'fmu' field "
                    f"resolving to an existing .fmu file."
                ),
            )

        test_dir = self.config.work_dir / test_key
        test_dir.mkdir(parents=True, exist_ok=True)

        if self.progress:
            self.progress.on_start(test_key)

        wall_start = time.monotonic()
        try:
            md = fmpy.read_model_description(str(fmu_path))
            available = _list_scalar_variables(md)
            requested = _resolve_requested_outputs(test, available)

            solver = _SOLVER_MAP.get(test.method, "CVode")
            sim_kwargs = {
                "filename": str(fmu_path),
                "validate": False,       # trust Reference-FMUs; validate adds startup cost
                "stop_time": test.stop_time,
                "solver": solver,
                "relative_tolerance": test.tolerance,
                "output": requested or None,  # None = record all variables
            }
            if test.output_interval is not None:
                sim_kwargs["output_interval"] = test.output_interval
            elif test.number_of_intervals is not None and test.number_of_intervals > 0:
                sim_kwargs["output_interval"] = test.stop_time / test.number_of_intervals

            result = fmpy.simulate_fmu(**sim_kwargs)

        except Exception as exc:  # FMPy raises various exception types
            elapsed = time.monotonic() - wall_start
            msg = f"FMPy simulation failed: {type(exc).__name__}: {exc}"
            logger.warning("Test %s: %s", test.model_id, msg)
            if self.progress:
                self.progress.on_finish(
                    test_key, success=False, elapsed=elapsed, error=msg,
                )
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                elapsed=elapsed,
                error_message=msg,
                sim_wall=elapsed,
            )

        elapsed = time.monotonic() - wall_start
        _save_result(test_dir / self.RESULT_FILENAME, result)

        if self.progress:
            self.progress.on_finish(test_key, success=True, elapsed=elapsed)

        return TestRunResult(
            model_id=test.model_id,
            test_key=test_key,
            success=True,
            elapsed=elapsed,
            sim_wall=elapsed,
            statistics={"simulation": {"wall_time": elapsed}},
        )

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def read_result(
        self,
        test: TestModel,
        test_key: str,
        run_result: TestRunResult,
    ) -> TestResult:
        result_path = self.config.work_dir / test_key / self.RESULT_FILENAME
        if not result_path.exists():
            return TestResult(
                model_id=test.model_id,
                success=False,
                error_message=f"No FMPy result at {result_path} (did simulation run?)",
                statistics=run_result.statistics if run_result else None,
            )

        arr = _load_result(result_path)
        available = [name for name in arr.dtype.names if name != "time"]

        # Resolve patterns (spec can use globs like "h" or "*"); for Modelica
        # flows we re-use the same mechanism as DymolaRunner.
        requested = resolve_variable_patterns(test.variable_patterns, available)
        if not requested:
            # Default: all non-time columns (parity with Dymola default behavior
            # when variables=[] is "simulate only" and variables=["*"] is "all").
            if "*" in test.variable_patterns:
                requested = available
            elif not test.variable_patterns:
                requested = []

        time_arr = np.asarray(arr["time"], dtype=np.float64)
        variables = [
            VariableResult(
                index=i + 1,
                name=name,
                time=time_arr,
                values=np.asarray(arr[name], dtype=np.float64),
            )
            for i, name in enumerate(requested)
        ]

        return TestResult(
            model_id=test.model_id,
            success=True,
            variables=variables,
            diagnostics=[],  # FMPy doesn't surface CPUtime/EventCounter equivalents today
            statistics=run_result.statistics if run_result else None,
        )


# ---------------------------------------------------------------------------
# Helpers (free functions for testability)
# ---------------------------------------------------------------------------

def _resolve_fmu_path(test: TestModel) -> Optional[Path]:
    """Get the FMU binary path for a test.

    MVP: spec_parser stores the resolved path in ``test.mo_file``. The field
    name is Modelica-flavored; its contents for FMU tests are the .fmu file.
    """
    p = test.mo_file
    return p if p and str(p) else None


def _list_scalar_variables(md) -> list[str]:
    """Return all scalar variable names from an FMPy model description."""
    return [sv.name for sv in md.modelVariables]


def _resolve_requested_outputs(test: TestModel, available: list[str]) -> list[str]:
    """Resolve the test's variable patterns against the FMU's declared variables.

    When the spec asks for everything (``"*"``), pass ``None`` through to FMPy
    (``None`` means "record all outputs" in FMPy). We resolve explicitly here
    too so the count is accurate for logs.
    """
    if not test.variable_patterns:
        return []
    if test.variable_patterns == ["*"]:
        return available
    return resolve_variable_patterns(test.variable_patterns, available)


def _save_result(path: Path, arr: np.ndarray) -> None:
    """Save an FMPy structured array to an .npz file.

    FMPy's output is a structured record array. We keep the structured dtype
    to preserve column names, using ``np.savez_compressed`` for a small
    on-disk footprint.
    """
    np.savez_compressed(path, result=arr)


def _load_result(path: Path) -> np.ndarray:
    """Load a structured array previously saved by ``_save_result``."""
    with np.load(path, allow_pickle=False) as npz:
        return npz["result"]
