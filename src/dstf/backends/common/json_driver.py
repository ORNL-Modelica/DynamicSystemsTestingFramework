"""Shared skeleton for JSON-driver subprocess backends.

Julia (D77) and Python (D80) are the same backend shape: spawn one subprocess
per test running a shipped driver script, hand it the user's source file plus
``(stop_time, tolerance, result_path)``, and read back a JSON payload of
``{"success", "time", "variables"}``. Everything that machinery does — source
resolution, the timeout mapping, the stdout/stderr artifacts, the failure-
message precedence, numeric validation of the payload — is identical, so it
lives here once (C-iii U-4). What genuinely differs is the class-level hooks
below: which driver, which binary, how the argv is built, and the labels that
go into messages and artifact filenames.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from abc import abstractmethod
from pathlib import Path
from typing import ClassVar

import numpy as np

from ...discovery.test_registry import TestModel
from ..base import Backend
from ..types import (
    TestResult,
    TestRunResult,
    VariableResult,
    resolve_variable_patterns,
    timeout_message,
)
from .proc_output import decode_output

logger = logging.getLogger(__name__)


class JsonDriverBackend(Backend):
    """Subprocess-per-test backend driving a shipped JSON-writing script."""

    #: Shipped driver script handed to the tool binary.
    driver_path: ClassVar[Path]
    #: Human label for the tool in messages ("Julia", "Python").
    tool_label: ClassVar[str]
    #: What the tool does to a test, for messages ("simulation", "execution").
    action_label: ClassVar[str]
    #: Prefix of the stdout/stderr artifacts, also the binary name in the
    #: generic non-zero-return message ("julia", "python").
    stdio_prefix: ClassVar[str]
    #: Extension of the user's source file, incl. the dot (".jl", ".py").
    source_suffix: ClassVar[str]
    #: Name the driver writes its JSON result under, inside the test dir.
    result_filename: ClassVar[str] = "result.json"

    @property
    @abstractmethod
    def tool_binary(self) -> Path:
        """The executable the driver script runs under."""
        ...

    @abstractmethod
    def build_command(
        self,
        binary: Path,
        driver: Path,
        user_file: Path,
        stop_time: float,
        tolerance: float,
        result_path: Path,
    ) -> list[str]:
        """Full argv for one test. The driver's own four arguments must stay
        in this order — every driver script reads them positionally."""
        ...

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
        user_file = self._resolve_source(test)
        if user_file is None or not user_file.exists():
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                error_message=(
                    f"{self.tool_label} source not found for {test.model_id}. "
                    f"Ensure the test_spec.json entry has a 'source' field "
                    f"resolving to an existing {self.source_suffix} file under "
                    f"source_path."
                ),
            )

        test_dir = self.config.work_dir / test_key
        test_dir.mkdir(parents=True, exist_ok=True)
        result_path = test_dir / self.result_filename
        stdout_path = test_dir / f"{self.stdio_prefix}_stdout.txt"
        stderr_path = test_dir / f"{self.stdio_prefix}_stderr.txt"

        if self.progress:
            self.progress.on_start(test_key)
            self.progress.on_phase(test_key, "simulating")

        timeout = float(
            test.timeout if test.timeout is not None else self.config.timeout
        )

        cmd = self.build_command(
            self.tool_binary,
            self.driver_path,
            user_file,
            test.stop_time,
            test.tolerance,
            result_path,
        )
        logger.debug("%s cmd: %s", self.tool_label, " ".join(cmd))

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
            msg = timeout_message(
                f"{self.tool_label} {self.action_label} exceeded {timeout}s timeout"
            )
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
                else f"{self.stdio_prefix} returned {proc.returncode}"
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
                error_message=(f"{self.tool_label} {self.action_label} failed: {err}"),
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
        result_path = self.config.work_dir / test_key / self.result_filename
        stats = run_result.statistics if run_result else None
        if not result_path.exists():
            return TestResult(
                model_id=test.model_id,
                success=False,
                error_message=(
                    f"No {self.tool_label} result at {result_path} "
                    f"(did {self.action_label} run?)"
                ),
                statistics=stats,
            )

        try:
            with result_path.open(encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            return TestResult(
                model_id=test.model_id,
                success=False,
                error_message=f"Failed to parse {result_path.name}: {exc}",
                statistics=stats,
            )

        if not payload.get("success", False):
            return TestResult(
                model_id=test.model_id,
                success=False,
                error_message=payload.get(
                    "error", f"{self.tool_label} driver reported failure"
                ),
                statistics=stats,
            )

        # review 2026-07-06 (finding 29): non-numeric values in a result
        # payload (a user script returning strings, a tampered file) must
        # fail THIS test, not raise through read_results and abort the whole
        # read phase for every test. The drivers validate too; this is the
        # backend-side belt.
        try:
            time_arr = np.asarray(payload.get("time", []), dtype=np.float64)
        except (ValueError, TypeError) as exc:
            return TestResult(
                model_id=test.model_id,
                success=False,
                error_message=(
                    f"Non-numeric 'time' array in {result_path.name}: {exc}"
                ),
                statistics=stats,
            )

        available = list(payload.get("variables", {}).keys())
        requested = resolve_variable_patterns(test.variable_patterns, available)
        if not requested:
            if "*" in test.variable_patterns:
                requested = available
            elif not test.variable_patterns:
                requested = []

        variables = []
        # index is the position in the *requested* list, so a name the driver
        # did not emit leaves a gap in the numbering rather than shifting the
        # rest. The index is a display/ordering handle, not a key — reference
        # matching is by name — so the gap is harmless and kept deliberately.
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
                    statistics=stats,
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
            statistics=stats,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_source(self, test: TestModel) -> Path | None:
        return resolve_source_file(test, self.config, self.source_suffix)


def resolve_source_file(test: TestModel, config, suffix: str) -> Path | None:
    """Resolve the user's source file.

    Priority: ``test.source_file`` (spec_parser fills this when the entry has
    a ``"source"`` field) → ``<source_path>/<model_id><suffix>`` fallback.

    Module-level rather than backend-only because the persistent Julia worker
    resolves the same file without being a backend.
    """
    if test.source_file is not None and str(test.source_file):
        p = Path(test.source_file)
        if not p.is_absolute() and config.source_path:
            p = config.source_path / p
        return p
    # Fallback: model_id mapped to a filename.
    if config.source_path:
        return config.source_path / f"{test.model_id}{suffix}"
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


__all__ = ["JsonDriverBackend", "resolve_source_file"]
