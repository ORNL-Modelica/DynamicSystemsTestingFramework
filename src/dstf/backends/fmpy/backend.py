"""FMPy-backed backend.

Consumes prebuilt FMUs via the FMPy Python library. Produces the same
``TestResult`` / ``VariableResult`` shape the Dymola backend produces, so the
comparator, storage, and reporting layers work unchanged.

Simulation flow per test (``run_single_test``):
  1. Take the FMU path from ``test.source_file`` (spec_parser puts it
     there when the spec entry has a ``"source"`` field). No ``source`` is
     no FMU: nothing names one after the model, as Python and Julia do.
  2. Read the model description to discover scalar variables.
  3. Resolve ``test.variable_patterns`` against the declared names.
  4. Call ``fmpy.simulate_fmu`` with the resolved settings.
  5. Persist the returned structured array to ``<test_dir>/result.npz`` so a
     later ``compare`` pass can re-read without re-simulating — matches the
     on-disk pattern the Dymola backend uses for ``dsres.mat``.

``read_result`` loads the .npz and builds a ``TestResult``.

Limitations (D65 — "FMU-path semantic gap"):
  The current ``fmpy.simulate_fmu`` call passes only ``filename`` /
  ``stop_time`` / ``solver`` / ``relative_tolerance`` / ``output`` /
  ``output_interval``. Specifically, the following are **not yet supported**:
    - ``input=`` — no way to drive an external input schedule into the FMU.
    - ``fmi_type=`` — FMPy auto-picks (co-simulation if present, else
      model-exchange); tests cannot select explicitly.
    - ``start_values=`` — cannot override FMU default start values from a
      test spec.
    - "Python-driver" tests — where the *test* is a script that steps the
      FMU and sets inputs over time, rather than a single ``simulate_fmu``
      call.
  This pathway is therefore scoped to **autonomous FMUs** (inputs baked in,
  co-sim/ME distinction irrelevant to the test's semantics). The Reference
  FMUs shipped under ``examples/fmu/`` (BouncingBall / Dahlquist / VanDerPol)
  are the validated shape. Generalizing beyond autonomous FMUs is deferred
  to a dedicated future phase.
"""

from __future__ import annotations

import concurrent.futures
import logging
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np

from ...config import Config
from ...config_shape import Setting
from ...discovery.test_registry import TestModel
from ...discovery.unit_tests import unit_test_variables
from .. import register
from ..base import Backend, Diagnosis
from ..common import result_files
from ..common.result_variables import criterion_variables
from ..locate import SEARCH, Found, Located, Rule
from ..types import (
    Capability,
    DatasetType,
    TestResult,
    TestRunResult,
    VariableResult,
    resolve_variable_patterns,
    timeout_message,
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


#: What FMPy's one part is called in a ``Located``.
PACKAGE = "fmpy package"


#: Held while a model description is read. fmpy validates it against the
#: FMI schema, which it builds anew for each read (`lxml.etree.XMLSchema`),
#: and libxml2 cannot build a schema from two threads at once: under `run
#: --parallel 2` every test failed with `XMLSchemaParseError` ("the given
#: type is not a built-in type", "Document is not valid XML Schema"). The
#: read is milliseconds; the simulations stay side by side.
_MODEL_DESCRIPTION_LOCK = threading.Lock()


@register("FMPy")
class FmpyBackend(Backend):
    """Runs FMU simulations using the FMPy Python library.

    Consumes prebuilt FMUs — does not compile from Modelica sources. For
    Modelica → FMU conversion, a backend declaring ``Capability.FMU_EXPORT``
    (today: :class:`PersistentDymolaBackend`) chains into this one for
    cross-backend verification — see ``backends/cross_backend.py``.
    """

    # Deliberately empty:
    #   PERSISTENT_WORKERS — review 2026-07-06 (finding 28): the flag was
    #     aspirational (no persistent FMPy backend exists) and tripped the
    #     repaired capability-honesty check. Re-add when one actually ships.
    #   BATCH_FALLBACK — FMPy *is* the Python path; no script fallback exists
    #   FMU_EXPORT — FMPy consumes FMUs, doesn't produce them
    capabilities: frozenset[Capability] = frozenset()
    produced_datasets = frozenset({DatasetType.TIME_SERIES})
    #: None: FMPy is a Python library, with no program to name.
    settings: Mapping[str, Setting] = {}
    source_types = frozenset({"fmu"})
    source_suffix = ".fmu"
    artifact_files = (("result.npz", "Result file"),)

    #: Filename used for the on-disk structured-array cache per test.
    RESULT_FILENAME = "result.npz"

    def __init__(self, config: Config):
        super().__init__(config)
        # Defer the fmpy import so a bare import of this module doesn't error
        # when fmpy isn't installed — the backend only errors if actually used.
        try:
            import fmpy  # noqa: F401
        except ImportError as exc:
            # RuntimeError, not ImportError: `Backend.preflight` names it as
            # the contract for "this backend cannot be built here", and it is
            # what `backends._instantiate` translates into
            # `BackendUnavailableError`. The OpenModelica and Dymola
            # dependency probes convert the same way.
            raise RuntimeError(
                "The FMPy backend requires the optional 'fmpy' extra.\n"
                "  source checkout:  uv sync --extra fmpy --locked --inexact\n"
                "  installed tool:   reinstall with the [fmpy] extra"
            ) from exc

    @classmethod
    def locate(cls, settings: Mapping[str, Any]) -> Located:
        """The ``fmpy`` package of the Python running this tool: FMPy has
        no executable, and is found iff it can be imported."""
        import importlib.metadata
        import importlib.util

        spec = importlib.util.find_spec("fmpy")
        if spec is None or spec.origin is None:
            return Located(
                (
                    Found(
                        PACKAGE,
                        detail="this Python has no fmpy; install the [fmpy] extra",
                    ),
                )
            )
        try:
            version = f"version {importlib.metadata.version('fmpy')}"
        except importlib.metadata.PackageNotFoundError:
            version = ""
        return Located(
            (
                Found(
                    PACKAGE,
                    str(Path(spec.origin).parent),
                    Rule(SEARCH, "this Python's packages"),
                    version,
                ),
            )
        )

    @classmethod
    def diagnose(
        cls, located: Located, *, entry: str = "", config: Config | None = None
    ) -> Diagnosis:
        """The located package; not found, what to install — FMPy has no
        program for a block to name."""
        return Diagnosis(
            tuple(located.lines(entry)),
            located.complete,
            ()
            if located.complete
            else (
                "Install fmpy: reinstall dstf with the [fmpy] extra "
                "(from a checkout: uv sync --extra fmpy --locked --inexact)",
            ),
        )

    def describe_tool_version(self) -> str | None:
        """The fmpy library version drives simulation, so it's the run's
        'tool version'. (Per-FMU ``generationTool`` metadata is richer but
        varies test-to-test; the library version is the run-level constant.)"""
        try:
            import fmpy

            return f"FMPy {fmpy.__version__}"
        except Exception:  # pragma: no cover — __init__ already validated import
            return None

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

        fmu_path = test.source_file
        if fmu_path is None:
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                error_message=(
                    f"{test.model_id}: its test_spec.json entry names no FMU — "
                    f"an FMPy test runs the .fmu its 'source' names, a path "
                    f"relative to the spec file."
                ),
            )
        if not fmu_path.exists():
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                error_message=(
                    f"FMU file not found for {test.model_id}. "
                    f"Ensure the test_spec.json entry has a 'source' field "
                    f"— a path relative to the spec file — resolving to an "
                    f"existing .fmu file."
                ),
            )

        test_dir = self.config.work_dir / test_key
        test_dir.mkdir(parents=True, exist_ok=True)

        if self.progress:
            self.progress.on_start(test_key)

        timeout = float(
            test.timeout if test.timeout is not None else self.config.timeout
        )

        wall_start = time.monotonic()
        try:
            if self.progress:
                self.progress.on_phase(test_key, "loading")
            with _MODEL_DESCRIPTION_LOCK:
                md = fmpy.read_model_description(str(fmu_path))
            available = _list_scalar_variables(md)
            requested = _tracked(
                test,
                _resolve_requested_outputs(test, available),
                available,
                self.config.diagnostic_variables,
            )
            if requested and not test.variable_patterns:
                # No patterns: what fmpy records by default stays recorded
                # beside the UnitTests and criteria variables — requesting
                # those alone took the FMU's outputs off the plot. Only the
                # tracked are read (`read_result`).
                requested = list(
                    dict.fromkeys([*_default_outputs(fmpy, md), *requested])
                )

            solver = _SOLVER_MAP.get(test.method, "CVode")
            sim_kwargs = {
                "filename": str(fmu_path),
                "validate": False,  # trust Reference-FMUs; validate adds startup cost
                "stop_time": test.stop_time,
                "solver": solver,
                "relative_tolerance": test.tolerance,
                # None records fmpy's default set (0.3.29's `Recorder` ->
                # `_get_output_variables`: outputs, else up to five states,
                # else local variables) — never every variable. Empty:
                # nothing is compared, so what fmpy picks is only plotted.
                "output": requested or None,
            }
            if test.output_interval is not None:
                sim_kwargs["output_interval"] = test.output_interval
            elif test.number_of_intervals is not None and test.number_of_intervals > 0:
                sim_kwargs["output_interval"] = (
                    test.stop_time / test.number_of_intervals
                )

            if self.progress:
                self.progress.on_phase(test_key, "simulating")

            # FMPy runs in-process; can't kill the C-level FMU compute. Run in a
            # thread and treat result(timeout=...) as the deadline. On timeout
            # the worker thread is left to finish in background — acceptable for
            # the typical sub-second FMU; documented trade-off.
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            future = executor.submit(fmpy.simulate_fmu, **sim_kwargs)
            try:
                result = future.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                elapsed = time.monotonic() - wall_start
                msg = timeout_message(
                    f"FMPy simulation exceeded {timeout}s timeout; the worker "
                    f"thread continues until the FMU returns and can't be "
                    f"force-killed in-process"
                )
                logger.warning("Test %s: %s", test.model_id, msg)
                if self.progress:
                    self.progress.on_finish(
                        test_key,
                        success=False,
                        elapsed=elapsed,
                        detail=msg,
                        timed_out=True,
                    )
                return TestRunResult(
                    model_id=test.model_id,
                    test_key=test_key,
                    success=False,
                    elapsed=elapsed,
                    error_message=msg,
                    sim_wall=elapsed,
                    # review 2026-07-06 (finding 73): without this flag the
                    # summary counted the timeout as a plain failure while
                    # the dashboard (progress) showed timed_out — disagreeing.
                    timed_out=True,
                )
            finally:
                executor.shutdown(wait=False)

            # review 2026-07-06 (finding 73): save INSIDE the try so an
            # OSError (disk full, permissions) fails this one test via the
            # handler below instead of propagating and aborting the run.
            _save_result(test_dir / self.RESULT_FILENAME, result)

        except Exception as exc:  # FMPy raises various exception types
            elapsed = time.monotonic() - wall_start
            msg = f"FMPy simulation failed: {type(exc).__name__}: {exc}"
            logger.warning("Test %s: %s", test.model_id, msg)
            if self.progress:
                self.progress.on_finish(
                    test_key,
                    success=False,
                    elapsed=elapsed,
                    detail=msg,
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
        # flows we re-use the same mechanism as DymolaBackend.
        requested = resolve_variable_patterns(test.variable_patterns, available)
        if not requested:
            # Default: all non-time columns (parity with Dymola default behavior
            # when variables=[] is "simulate only" and variables=["*"] is "all").
            if "*" in test.variable_patterns:
                requested = available
            elif not test.variable_patterns:
                requested = []
        requested = _tracked(
            test, requested, available, self.config.diagnostic_variables
        )
        # Loud on every backend (`extract_variables`): a variable the
        # UnitTests or a criterion names that the saved columns lack is a
        # spec/model mismatch, not something to swallow.
        for name in dict.fromkeys(
            [
                *(v.name for v in unit_test_variables(test)),
                *criterion_variables(test, self.config.diagnostic_variables),
            ]
        ):
            if name not in requested:
                logger.warning(
                    "Variable %s not found in simulation output for %s",
                    name,
                    test.model_id,
                )

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

    @classmethod
    def result_names(cls, test_dir: Path) -> list[str] | None:
        return result_files.npz_names(test_dir / cls.RESULT_FILENAME)

    @classmethod
    def result_series(cls, test_dir, names):
        return result_files.npz_series(test_dir / cls.RESULT_FILENAME, names)


# ---------------------------------------------------------------------------
# Helpers (free functions for testability)
# ---------------------------------------------------------------------------


def _list_scalar_variables(md) -> list[str]:
    """Return all scalar variable names from an FMPy model description."""
    return [sv.name for sv in md.modelVariables]


def _resolve_requested_outputs(test: TestModel, available: list[str]) -> list[str]:
    """Resolve the test's variable patterns against the FMU's declared variables.

    When the spec asks for everything (``"*"``), every declared variable is
    requested explicitly: FMPy's ``output=None`` records the OUTPUTS only.
    """
    if not test.variable_patterns:
        return []
    if test.variable_patterns == ["*"]:
        return available
    return resolve_variable_patterns(test.variable_patterns, available)


def _default_outputs(fmpy, md) -> list[str]:
    """What fmpy records for ``output=None``: its own rule
    (`fmpy.simulation._get_output_variables`, private — asked, not copied,
    so it cannot drift from what fmpy does)."""
    return [v.name for v in fmpy.simulation._get_output_variables(md)]


def _tracked(
    test: TestModel,
    requested: list[str],
    available: list[str],
    diagnostic_vars: list[str],
) -> list[str]:
    """The tracked names, in Dymola's and OpenModelica's order
    (`extract_variables`): the UnitTests variables ``available`` has,
    then ``requested`` (the resolved patterns), then the variables a
    criterion names that ``available`` has — so indices 1..N are the
    UnitTests variables, as a stored baseline records them. The simulate side
    (``available``: the model description's variables) and the read side
    (the saved columns) both go through it, so what is tracked is what is
    read."""
    have = set(available)
    out = [v.name for v in unit_test_variables(test) if v.name in have]
    out += [name for name in requested if name not in out]
    for name in criterion_variables(test, diagnostic_vars):
        if name in have and name not in out:
            out.append(name)
    return out


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
