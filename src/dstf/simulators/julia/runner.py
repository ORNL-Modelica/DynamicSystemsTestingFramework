"""Julia / ModelingToolkit subprocess runner (D77).

Invokes a shipped driver script (``run_test.jl``) via ``julia --project``.
The driver loads the user's ``.jl`` file — which exports ``build_mtk_system()``
returning a ``NamedTuple{sys, u0, ps}`` — simulates via ``OrdinaryDiffEq``,
and writes a JSON result. This runner reads it back into a ``TestResult``.

Batch-only for the MVP; each test spawns one Julia subprocess. Julia's
first-run JIT + precompile cost is front-loaded (several minutes for
MTK + OrdinaryDiffEq); subsequent runs are seconds per test. A persistent-
worker path (via ``JuliaCall`` or a stdin-driven long-lived Julia process)
is a future enhancement once the batch path is proven.
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


# Shipped driver script; copied/resolved at runtime.
_DRIVER_PATH = Path(__file__).resolve().parent / "run_test.jl"


@dataclass(frozen=True)
class JuliaConfig:
    """Julia-specific settings extracted from the unified :class:`Config`.

    ``julia_binary`` is the absolute path to the ``julia`` executable.
    ``project_dir`` is the Julia project directory (contains ``Project.toml``
    and ``Manifest.toml``); defaults to the same directory the test sources
    live in (``config.source_path``). That way a user ships a self-contained
    Julia project with MTK pinned.
    """

    julia_binary: Path
    project_dir: Path

    @classmethod
    def from_config(cls, config: Config) -> JuliaConfig:
        # Config.simulator_path is already resolved in __post_init__ via the
        # simulators map + PATH fallback. If that lookup didn't produce
        # anything (e.g., user omitted the path and PATH doesn't include
        # julia), try shutil.which() once more before giving up.
        resolved: Path | None = None
        if config.simulator_path:
            p = Path(config.simulator_path).expanduser()
            if p.exists():
                resolved = p
        if resolved is None:
            on_path = shutil.which("julia")
            if on_path:
                resolved = Path(on_path)
        if resolved is None:
            raise RuntimeError(
                "Julia binary not found. Install Julia (https://julialang.org/"
                "downloads/) and ensure 'julia' is on PATH, or set an explicit "
                "path under testing.json's 'simulators' map."
            )
        project = config.source_path or Path.cwd()
        return cls(julia_binary=resolved, project_dir=project)


@register("Julia")
class JuliaRunner(SimulatorRunner):
    """Subprocess-per-test Julia/MTK runner."""

    capabilities = frozenset(
        {
            Capability.BATCH_FALLBACK,  # subprocess-per-test (this class)
            Capability.PERSISTENT_WORKERS,  # via PersistentJuliaRunner (D78)
            # Deliberately absent:
            #   FMU_EXPORT — MTK.generate_fmu deferred
        }
    )
    produced_datasets = frozenset({DatasetType.TIME_SERIES})
    artifact_files = (
        ("result.json", "Simulation result (time + variables)"),
        ("julia_stdout.txt", "Julia stdout"),
        ("julia_stderr.txt", "Julia stderr"),
    )

    RESULT_FILENAME = "result.json"

    def __init__(self, config: Config):
        super().__init__(config)
        # Resolve once so run_single_test errors are loud + early when
        # the binary is missing; lets --filter walk the test set quickly.
        self.julia_config = JuliaConfig.from_config(config)

    @classmethod
    def persistent_runner_cls(cls):
        from .persistent_runner import PersistentJuliaRunner

        return PersistentJuliaRunner

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
        user_file = _resolve_julia_source(test, self.config)
        if user_file is None or not user_file.exists():
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                error_message=(
                    f"Julia source not found for {test.model_id}. Ensure the "
                    f"test_spec.json entry has a 'source' field resolving to "
                    f"an existing .jl file under source_path."
                ),
            )

        test_dir = self.config.work_dir / test_key
        test_dir.mkdir(parents=True, exist_ok=True)
        result_path = test_dir / self.RESULT_FILENAME
        stdout_path = test_dir / "julia_stdout.txt"
        stderr_path = test_dir / "julia_stderr.txt"

        if self.progress:
            self.progress.on_start(test_key)
            self.progress.on_phase(test_key, "simulating")

        timeout = float(
            test.timeout if test.timeout is not None else self.config.timeout
        )

        cmd = [
            str(self.julia_config.julia_binary),
            f"--project={self.julia_config.project_dir}",
            "--startup-file=no",
            "--color=no",
            str(_DRIVER_PATH),
            str(user_file),
            str(test.stop_time),
            str(test.tolerance),
            str(result_path),
        ]
        logger.debug("Julia cmd: %s", " ".join(cmd))

        wall_start = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                cwd=test_dir,
                capture_output=True,
                text=True,
                # review 2026-07-06 (finding 75): pin utf-8 + replace so a
                # UTF-8 Julia backtrace can't crash decoding on cp1252 hosts.
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
            msg = f"Julia simulation exceeded {timeout}s timeout"
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
            # The driver writes a structured failure JSON on exceptions; if
            # present, we surface its 'error' message. Otherwise fall back
            # to the raw stderr tail.
            # review 2026-07-06 (finding 75): whitespace-only stderr made
            # splitlines()[-1] IndexError inside the failure handler.
            stderr_lines = (proc.stderr or "").strip().splitlines()
            err = _read_failure_error(result_path) or (
                stderr_lines[-1]
                if stderr_lines
                else f"julia returned {proc.returncode}"
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
                error_message=f"Julia simulation failed: {err}",
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
                error_message=f"No Julia result at {result_path} (did simulation run?)",
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
                error_message=payload.get("error", "Julia driver reported failure"),
                statistics=run_result.statistics if run_result else None,
            )

        time_arr = np.asarray(payload.get("time", []), dtype=np.float64)
        available = list(payload.get("variables", {}).keys())
        requested = resolve_variable_patterns(test.variable_patterns, available)
        if not requested:
            if "*" in test.variable_patterns:
                requested = available
            elif not test.variable_patterns:
                requested = []

        variables = [
            VariableResult(
                index=i + 1,
                name=name,
                time=time_arr,
                values=np.asarray(payload["variables"][name], dtype=np.float64),
            )
            for i, name in enumerate(requested)
            if name in payload["variables"]
        ]

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


def _resolve_julia_source(test: TestModel, config: Config) -> Path | None:
    """Resolve the user's ``.jl`` file.

    Priority: ``test.source_file`` (spec_parser fills this when the entry
    has a ``"source"`` field) → ``<source_path>/<model_id>.jl`` fallback.
    """
    if test.source_file is not None and str(test.source_file):
        p = Path(test.source_file)
        if not p.is_absolute() and config.source_path:
            p = config.source_path / p
        return p
    # Fallback: model_id mapped to a filename.
    if config.source_path:
        return config.source_path / f"{test.model_id}.jl"
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
