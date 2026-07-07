"""Python / subprocess runner (D80).

Invokes a shipped driver script (``run_test.py``) via the configured
Python interpreter. The driver loads the user's ``.py`` file — which
must define ``simulate(stop_time, tolerance) -> dict`` — executes it,
and writes a JSON result. This runner reads it back into a ``TestResult``.

Batch-only for the MVP; each test spawns one Python subprocess. Startup
cost is roughly 30-100 ms per test (importlib + any user-file imports);
for trivial tests this dominates over the actual ``simulate()`` call.
A persistent-worker path (long-lived Python subprocess with stdin-JSON
dispatch) is a future enhancement if per-test overhead becomes an issue
— mirrors the D77 → D78 Julia progression.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

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
from ..common.proc_output import decode_output

logger = logging.getLogger(__name__)


# Shipped driver script.
_DRIVER_PATH = Path(__file__).resolve().parent / "run_test.py"


@dataclass(frozen=True)
class PythonConfig:
    """Python-specific settings extracted from the unified :class:`Config`.

    ``python_binary`` is the absolute path to the Python interpreter that
    will run user test scripts. It must have whatever packages the user's
    scripts import (scipy, pandas, ...). The framework does not manage
    the user's environment — pick a ``python`` that has their deps.
    """

    python_binary: Path

    @classmethod
    def from_config(cls, config: Config) -> PythonConfig:
        resolved: Path | None = None
        if config.simulator_path:
            p = Path(config.simulator_path).expanduser()
            if p.exists():
                resolved = p
        if resolved is None:
            for name in ("python", "python3"):
                on_path = shutil.which(name)
                if on_path:
                    resolved = Path(on_path)
                    break
        if resolved is None:
            raise RuntimeError(
                "Python binary not found. Ensure 'python' or 'python3' is on "
                "PATH, or set an explicit path under testing.json's "
                '\'simulators\' map, e.g. {"Python": ["/path/to/venv/bin/python"]}.'
            )
        return cls(python_binary=resolved)


@register("Python")
class PythonRunner(SimulatorRunner):
    """Subprocess-per-test Python runner."""

    capabilities = frozenset(
        {
            Capability.BATCH_FALLBACK,
            # Deliberately absent:
            #   PERSISTENT_WORKERS — stdin-driven long-lived Python process deferred.
            #   FMU_EXPORT — not meaningful for arbitrary Python scripts.
            #   EXPERIMENT_INGEST — a PythonRunner can both simulate and ingest;
            #     the flag is backend-level so we don't declare either role.
        }
    )
    produced_datasets = frozenset({DatasetType.TIME_SERIES})
    artifact_files = (
        ("result.json", "Simulation result (time + variables)"),
        ("python_stdout.txt", "Python stdout"),
        ("python_stderr.txt", "Python stderr"),
    )

    RESULT_FILENAME = "result.json"

    def __init__(self, config: Config):
        super().__init__(config)
        self.python_config = PythonConfig.from_config(config)

    def describe_tool_version(self) -> str | None:
        """Version of the *configured* interpreter (``python_config.
        python_binary``) that actually runs each test's subprocess — not this
        host's Python, which may differ. Probed via ``--version`` with a short
        timeout; ``None`` on any failure."""
        import subprocess

        try:
            out = subprocess.run(
                [str(self.python_config.python_binary), "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            # Older CPython prints the banner to stderr, newer to stdout.
            banner = (out.stdout or out.stderr or "").strip()
            return banner or None
        except Exception:
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
        user_file = _resolve_python_source(test, self.config)
        if user_file is None or not user_file.exists():
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                error_message=(
                    f"Python source not found for {test.model_id}. Ensure the "
                    f"test_spec.json entry has a 'source' field resolving to "
                    f"an existing .py file under source_path."
                ),
            )

        test_dir = self.config.work_dir / test_key
        test_dir.mkdir(parents=True, exist_ok=True)
        result_path = test_dir / self.RESULT_FILENAME
        stdout_path = test_dir / "python_stdout.txt"
        stderr_path = test_dir / "python_stderr.txt"

        if self.progress:
            self.progress.on_start(test_key)
            self.progress.on_phase(test_key, "simulating")

        timeout = float(
            test.timeout if test.timeout is not None else self.config.timeout
        )

        cmd = [
            str(self.python_config.python_binary),
            str(_DRIVER_PATH),
            str(user_file),
            str(test.stop_time),
            str(test.tolerance),
            str(result_path),
        ]
        logger.debug("Python cmd: %s", " ".join(cmd))

        wall_start = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                cwd=test_dir,
                capture_output=True,
                text=True,
                # review 2026-07-06 (finding 75): pin utf-8 + replace so a
                # non-ASCII traceback can't crash decoding on cp1252 hosts.
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            elapsed = time.monotonic() - wall_start
            # review 2026-07-06 (finding 23): TimeoutExpired.stdout/.stderr are
            # bytes even with text=True — decode_output handles None/bytes/str.
            stdout_path.write_text(decode_output(exc.stdout), encoding="utf-8")
            stderr_path.write_text(decode_output(exc.stderr), encoding="utf-8")
            msg = f"Python execution exceeded {timeout}s timeout"
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
                timed_out=True,
            )

        elapsed = time.monotonic() - wall_start
        stdout_path.write_text(proc.stdout or "", encoding="utf-8")
        stderr_path.write_text(proc.stderr or "", encoding="utf-8")

        if proc.returncode != 0:
            # review 2026-07-06 (finding 75): whitespace-only stderr made
            # splitlines()[-1] IndexError inside the failure handler.
            stderr_lines = (proc.stderr or "").strip().splitlines()
            err = _read_failure_error(result_path) or (
                stderr_lines[-1]
                if stderr_lines
                else f"python returned {proc.returncode}"
            )
            if self.progress:
                self.progress.on_finish(
                    test_key,
                    success=False,
                    elapsed=elapsed,
                    detail=err,
                )
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                elapsed=elapsed,
                error_message=f"Python execution failed: {err}",
                sim_wall=elapsed,
            )

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
                error_message=(
                    f"No Python result at {result_path} (did execution run?)"
                ),
                statistics=run_result.statistics if run_result else None,
            )

        try:
            with result_path.open(encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            return TestResult(
                model_id=test.model_id,
                success=False,
                error_message=f"Failed to parse {result_path.name}: {exc}",
                statistics=run_result.statistics if run_result else None,
            )

        if not payload.get("success", False):
            return TestResult(
                model_id=test.model_id,
                success=False,
                error_message=payload.get("error", "Python driver reported failure"),
                statistics=run_result.statistics if run_result else None,
            )

        # review 2026-07-06 (finding 29): non-numeric values in a result
        # payload (user simulate() returning strings, tampered file) must
        # fail THIS test, not raise through read_results and abort the whole
        # read phase for every test. The driver validates too; this is the
        # runner-side belt.
        try:
            time_arr = np.asarray(payload.get("time", []), dtype=np.float64)
        except (ValueError, TypeError) as exc:
            return TestResult(
                model_id=test.model_id,
                success=False,
                error_message=(
                    f"Non-numeric 'time' array in {result_path.name}: {exc}"
                ),
                statistics=run_result.statistics if run_result else None,
            )
        available = list(payload.get("variables", {}).keys())
        requested = resolve_variable_patterns(test.variable_patterns, available)
        if not requested:
            if "*" in test.variable_patterns:
                requested = available
            elif not test.variable_patterns:
                requested = []

        variables = []
        for i, name in enumerate(requested):
            if name not in payload["variables"]:
                continue
            try:
                values = np.asarray(payload["variables"][name], dtype=np.float64)
            except (ValueError, TypeError) as exc:
                return TestResult(
                    model_id=test.model_id,
                    success=False,
                    error_message=(
                        f"Variable '{name}' has non-numeric values in "
                        f"{result_path.name}: {exc}"
                    ),
                    statistics=run_result.statistics if run_result else None,
                )
            variables.append(
                VariableResult(
                    index=i + 1,
                    name=name,
                    time=time_arr,
                    values=values,
                )
            )

        return TestResult(
            model_id=test.model_id,
            success=True,
            variables=variables,
            diagnostics=[],
            statistics=run_result.statistics if run_result else None,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_python_source(test: TestModel, config: Config) -> Path | None:
    """Resolve the user's ``.py`` file.

    Priority: ``test.source_file`` (spec_parser fills this when the entry
    has a ``"source"`` field) → ``<source_path>/<model_id>.py`` fallback.
    """
    if test.source_file is not None and str(test.source_file):
        p = Path(test.source_file)
        if not p.is_absolute() and config.source_path:
            p = config.source_path / p
        return p
    if config.source_path:
        return config.source_path / f"{test.model_id}.py"
    return None


def _read_failure_error(result_path: Path) -> str | None:
    """If the driver wrote a failure JSON, pull its 'error' message."""
    if not result_path.exists():
        return None
    try:
        with result_path.open(encoding="utf-8") as f:
            payload = json.load(f)
        if not payload.get("success", True):
            return payload.get("error")
    except Exception:
        return None
    return None
