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
import threading
import time
from abc import abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

import numpy as np

from ...discovery.test_registry import TestModel, diagnostic_names
from ..base import Backend
from ..types import (
    TestResult,
    TestRunResult,
    VariableResult,
    resolve_variable_patterns,
    timeout_message,
)
from . import result_files
from .proc_output import decode_output
from .result_variables import criterion_variables


def run_killable(
    cmd: list[str],
    *,
    cwd: Path,
    timeout: float,
    on_spawn: Callable[[subprocess.Popen], None],
) -> subprocess.CompletedProcess:
    """``subprocess.run(cmd, capture_output=True, text=True, timeout=...)``
    with the live ``Popen`` handed to *on_spawn*, so another thread can kill
    it. Same contract otherwise: the child is killed and reaped on timeout
    and ``TimeoutExpired`` carries what it had written.
    """
    with subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        # review 2026-07-06 (finding 75): pin utf-8 + replace so a
        # non-ASCII traceback can't crash decoding on cp1252 hosts.
        encoding="utf-8",
        errors="replace",
    ) as proc:
        on_spawn(proc)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            exc.stdout, exc.stderr = proc.communicate()
            raise
        except BaseException:
            proc.kill()
            raise
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


logger = logging.getLogger(__name__)

#: The reason of a failure JSON that gives none (no ``error``, an empty or a
#: null one) — the run's word and the read-back's.
NO_REASON = "the driver gave no reason"


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
    source_suffix: str
    #: Name the driver writes its JSON result under, inside the test dir.
    result_filename: ClassVar[str] = "result.json"

    def __init__(self, config):
        super().__init__(config)
        #: The driver subprocess of each test in flight, by test key, and the
        #: tests told to stop — what :meth:`cancel_test` works on, from
        #: another thread.
        self._procs_lock = threading.Lock()
        self._procs: dict[str, subprocess.Popen] = {}
        self._cancelled: set[str] = set()

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
                error_message=source_not_found(
                    test, self.config, self.source_suffix, self.tool_label
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

        def _register(spawned: subprocess.Popen) -> None:
            with self._procs_lock:
                self._procs[test_key] = spawned

        try:
            try:
                proc = run_killable(
                    cmd, cwd=test_dir, timeout=timeout, on_spawn=_register
                )
            finally:
                with self._procs_lock:
                    self._procs.pop(test_key, None)
                    cancelled = test_key in self._cancelled
                    self._cancelled.discard(test_key)
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

        if cancelled and proc.returncode != 0:
            # Killed on request (cancel_test): neither a failure nor a
            # result. A driver that had already finished keeps its result.
            if self.progress:
                self.progress.on_finish(
                    test_key, success=False, elapsed=elapsed, detail="Cancelled"
                )
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                elapsed=elapsed,
                error_message="Cancelled",
                sim_wall=elapsed,
                cancelled=True,
            )

        if proc.returncode != 0:
            # The driver writes a structured failure JSON on exceptions; if
            # present, we surface its 'error' message. Otherwise fall back
            # to the raw stderr tail.
            # review 2026-07-06 (finding 75): whitespace-only stderr made
            # splitlines()[-1] IndexError inside the failure handler.
            stderr_lines = (proc.stderr or "").strip().splitlines()
            err = _read_failure_error(result_path)
            if err is None:
                err = (
                    stderr_lines[-1]
                    if stderr_lines
                    else f"{self.stdio_prefix} returned {proc.returncode}"
                )
            msg = self._failure(err)
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

    def _failure(self, reason: str) -> str:
        """The ONE message of a run whose driver failed, ``reason`` being
        the driver's own word for why: what the run's result says, what
        the progress row says, and what `read_result` says of the same
        failure read back from the file the driver wrote it into — the
        queue's row and, after a restart of the tool, the test's page. A
        failure that left no such file (killed by its timeout, a driver
        that died) reads back as the verdict its run recorded
        (``backends.base._force_failed``)."""
        return f"{self.tool_label} {self.action_label} failed: {reason}"

    def cancel_test(self, test_key: str) -> bool:
        """Kill the driver subprocess running *test_key*; its
        ``run_single_test`` then returns a ``cancelled`` result."""
        with self._procs_lock:
            proc = self._procs.get(test_key)
            if proc is None:
                return False
            self._cancelled.add(test_key)
        try:
            proc.kill()
        except OSError:
            pass  # it ended on its own in the meantime
        return True

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
                error_message=self._failure(payload.get("error") or NO_REASON),
                statistics=stats,
                run_reason=True,
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
        # The variables a criterion names, APPENDED after the patterns (their
        # indices stay put); one the payload lacks is loud, as a missing
        # UnitTests variable is on the .mat backends.
        requested = list(requested)
        listed = diagnostic_names(test, self.config.diagnostic_variables)
        for name in criterion_variables(test, listed):
            if name in requested:
                continue
            if name in available:
                requested.append(name)
            else:
                logger.warning(
                    "Variable %s not found in simulation output for %s",
                    name,
                    test.model_id,
                )

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

    @classmethod
    def result_names(cls, test_dir: Path) -> list[str] | None:
        return result_files.json_names(test_dir / cls.result_filename)

    @classmethod
    def result_series(cls, test_dir, names):
        return result_files.json_series(test_dir / cls.result_filename, names)

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
    if test.source_file is not None:
        p = test.source_file
        if not p.is_absolute() and config.source_path:
            p = config.source_path / p
        return p
    # Fallback: model_id mapped to a filename.
    if config.source_path:
        return config.source_path / f"{test.model_id}{suffix}"
    return None


def source_not_found(test: TestModel, config, suffix: str, label: str) -> str:
    """Why ``test`` cannot run: :func:`resolve_source_file` names no file
    that is there. The sentence names the path it looked for, and, for an
    entry with no ``source``, both ways out: name the file, or put it
    where the fallback looks. The Julia worker and ``--batch`` say it."""
    head = f"{label} source not found for {test.model_id}."
    looked = resolve_source_file(test, config, suffix)
    if test.source_file is not None:
        return (
            f"{head} Its test_spec.json entry's 'source' is {looked}, where "
            f"there is no file."
        )
    if looked is None:
        return (
            f"{head} Its test_spec.json entry has no 'source', and there is no "
            f"source_path to look in: add a 'source' naming its {suffix} file."
        )
    return (
        f"{head} Its test_spec.json entry has no 'source', and there is no "
        f"file at {looked}: add a 'source' naming its {suffix} file, or put "
        f"the file at that path."
    )


def _read_failure_error(result_path: Path) -> str | None:
    """If the driver wrote a failure JSON, its 'error' message —
    :data:`NO_REASON` for one that gives none, as `read_result` says of the
    same file; None with no failure JSON."""
    if not result_path.exists():
        return None
    try:
        with result_path.open(encoding="utf-8") as f:
            payload = json.load(f)
        if not payload.get("success", True):
            return payload.get("error") or NO_REASON
    except Exception:
        return None
    return None


__all__ = ["JsonDriverBackend", "resolve_source_file", "source_not_found"]
