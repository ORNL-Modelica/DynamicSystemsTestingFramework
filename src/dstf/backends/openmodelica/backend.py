"""OpenModelica runner: omc subprocess + .mos scripts (batch fallback).

Per-test omc subprocess driven by a generated ``simulate.mos``. The
persistent-worker counterpart lives in :mod:`.persistent_backend` and is the
default CLI path; this one is kept as the fallback (``--batch`` CLI flag,
or automatic on OMPython ImportError). FMU export (``buildModelFMU``) is
still a follow-up.

One-time per machine (bootstrap MSL):

    omc -e 'updatePackageIndex(); installPackage(Modelica); getErrorString();'

``backend_setup`` commands run between library-loading and ``cd()``. These
are emitted as-is and are backend-specific (Dymola-syntactic commands like
``Advanced.UI.TranslationInCommandLog := true`` will fail on omc). Users
maintain a separate ``testing.linux.json`` for the OpenModelica run.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from dataclasses import dataclass

from ...config import Config
from ...discovery.test_registry import TestModel
from .. import register
from ..base import Backend
from ..common.mat_reader import mat_reaches_time, read_result_mat
from ..common.proc_output import decode_output
from ..common.result_variables import (
    compute_needed_variables,
    extract_variables,
    fold_diagnostics_into_statistics,
    merge_timing_stats,
)
from ..types import (
    Capability,
    DatasetType,
    TestResult,
    TestRunResult,
    timeout_message,
)
from .log_parser import parse_omc_stdout
from .mos_generator import build_simulate_mos

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OpenModelicaConfig:
    """OpenModelica-specific settings extracted from the universal Config."""

    omc_path: str
    backend_setup: tuple[str, ...] = ()
    diagnostic_variables: tuple[str, ...] = ("CPUtime", "EventCounter")
    std_version: str = "latest"

    @classmethod
    def from_config(cls, config: Config) -> OpenModelicaConfig:
        # Config.__post_init__ now resolves backend_path via
        # BACKEND_BINARY_NAMES, so the Config-supplied value is authoritative.
        # Fall through to `omc` on PATH only if the config didn't set one at
        # all (e.g., stubbed tests that bypass __post_init__).
        omc_path = config.backend_path or shutil.which("omc") or "omc"
        return cls(
            omc_path=omc_path,
            backend_setup=tuple(config.backend_setup),
            diagnostic_variables=tuple(config.diagnostic_variables),
        )


@register("OpenModelica")
class OpenModelicaBackend(Backend):
    """OpenModelica backend using omc as a subprocess driver."""

    capabilities = frozenset(
        {
            Capability.BATCH_FALLBACK,
            Capability.PERSISTENT_WORKERS,
        }
    )
    produced_datasets = frozenset({DatasetType.TIME_SERIES})
    artifact_files = (
        ("simulate.mos", "Simulation script"),
        ("result_res.mat", "Result file"),
        ("result.log", "Simulation log"),
        ("result_info.json", "Model info"),
        ("omc_stdout.txt", "omc output"),
    )

    RESULT_MAT_FILENAME = "result_res.mat"
    STDOUT_FILENAME = "omc_stdout.txt"
    MOS_FILENAME = "simulate.mos"

    def __init__(self, config: Config):
        super().__init__(config)
        self.om_config = OpenModelicaConfig.from_config(config)

    @classmethod
    def persistent_backend_cls(cls):
        from .persistent_backend import PersistentOpenModelicaBackend

        return PersistentOpenModelicaBackend

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
        test_dir = self.config.work_dir / test_key
        test_dir.mkdir(parents=True, exist_ok=True)

        if self.progress:
            self.progress.on_start(test_key)
            self.progress.on_phase(test_key, "translating")

        # UnitTests component variable names (unitTests.x[1..n]) — unlike
        # spec-sourced tests these don't appear in test.variable_patterns, so
        # we must add them to the variableFilter explicitly or OM's regex
        # will exclude them from the .mat.
        extra_filter_names: list[str] = []
        if test.source in ("unit_tests", "both") and test.n_vars > 0:
            extra_filter_names = [
                f"unitTests.x[{i}]" for i in range(1, test.n_vars + 1)
            ]

        # MSL is always required for Modelica simulation. Dymola auto-loads
        # it; OM needs an explicit loadModel(Modelica). Auto-inject if the
        # user didn't already list it, so a single testing.json with an
        # empty ``dependencies`` works across both backends.
        deps = list(self.config.dependencies)
        if "Modelica" not in deps and not any(
            str(d).rstrip("/\\").endswith("Modelica") for d in deps
        ):
            deps = ["Modelica"] + deps

        # Build and write the .mos
        mos_text = build_simulate_mos(
            test=test,
            test_dir=test_dir,
            library_package_mo=self.config.library_dir / "package.mo",
            dependencies=deps,
            backend_setup=list(self.om_config.backend_setup),
            diagnostic_vars=list(self.om_config.diagnostic_variables),
            std_version=self.om_config.std_version,
            extra_filter_names=extra_filter_names,
        )
        mos_path = test_dir / self.MOS_FILENAME
        mos_path.write_text(mos_text, encoding="utf-8")

        timeout = float(
            test.timeout if test.timeout is not None else self.config.timeout,
        )

        wall_start = time.monotonic()
        try:
            proc = subprocess.run(
                [self.om_config.omc_path, self.MOS_FILENAME],
                cwd=test_dir,
                capture_output=True,
                text=True,
                # review 2026-07-06 (finding 75): pin utf-8 + replace so a
                # non-ASCII omc message can't crash the reader on cp1252 hosts.
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            elapsed = time.monotonic() - wall_start
            # review 2026-07-06 (finding 23): TimeoutExpired.stdout is bytes
            # even with text=True — decode_output handles None/bytes/str.
            (test_dir / self.STDOUT_FILENAME).write_text(
                decode_output(exc.stdout) + "\n[TimeoutExpired]\n",
                encoding="utf-8",
            )
            msg = timeout_message(f"omc exceeded {timeout}s timeout")
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
                timed_out=True,
            )

        elapsed = time.monotonic() - wall_start
        stdout_text = proc.stdout or ""
        (test_dir / self.STDOUT_FILENAME).write_text(stdout_text, encoding="utf-8")

        parsed = parse_omc_stdout(stdout_text)

        # Build statistics.timing — wall-clock seconds captured Python-side.
        stats: dict = {}
        if parsed.timings is not None:
            stats["timing"] = dict(parsed.timings)

        # Translation-wall = sum of all pre-simulation phases.
        translation_wall: float | None = None
        sim_wall: float | None = None
        if parsed.timings is not None:
            t = parsed.timings
            translation_wall = (
                t.get("frontend", 0.0)
                + t.get("backend", 0.0)
                + t.get("simcode", 0.0)
                + t.get("templates", 0.0)
                + t.get("compile", 0.0)
            )
            sim_wall = t.get("simulation")

        mat_path = test_dir / self.RESULT_MAT_FILENAME
        if parsed.success and mat_path.exists():
            # Belt on the accept-integrity invariant (D93 precedent): a
            # partial .mat blessed by --accept poisons the baseline, so
            # even a success-reporting record must show a trajectory that
            # reached stop_time. Empirically (D99, omc 1.27) mid-run
            # aborts return an EMPTY resultFile and never take this
            # branch — this guards against a future omc populating
            # resultFile on partial runs or a truncated write.
            reached, last_time = mat_reaches_time(mat_path, float(test.stop_time))
            if reached:
                if self.progress:
                    self.progress.on_phase(test_key, "simulating")
                    self.progress.on_finish(test_key, success=True, elapsed=elapsed)
                return TestRunResult(
                    model_id=test.model_id,
                    test_key=test_key,
                    success=True,
                    elapsed=elapsed,
                    translation_wall=translation_wall,
                    sim_wall=sim_wall,
                    statistics=stats or None,
                )
            parsed.error_notices.append(
                f"simulate() reported success but {self.RESULT_MAT_FILENAME} "
                f"reaches t={last_time!r} of stop_time={test.stop_time} — "
                f"partial result refused"
            )

        # Failure: build an error message that surfaces both pre-record
        # notices and the simulate() record's `messages` field.
        err_parts: list[str] = []
        if proc.returncode != 0:
            err_parts.append(f"omc exit code {proc.returncode}")
        if parsed.error_notices:
            err_parts.append("; ".join(parsed.error_notices[:5]))
        if parsed.messages:
            err_parts.append(parsed.messages.strip())
        if not parsed.result_file:
            err_parts.append("no result file produced")
        msg = " | ".join(p for p in err_parts if p)[:2048] or "omc simulation failed"

        # If MSL wasn't installed, surface a clear hint.
        if any("Failed to load package Modelica" in n for n in parsed.error_notices):
            msg = (
                "MSL not installed. Run: omc -e "
                "'updatePackageIndex(); installPackage(Modelica); "
                "getErrorString();' | " + msg
            )

        if self.progress:
            self.progress.on_finish(
                test_key,
                success=False,
                elapsed=elapsed,
                detail=msg[:120],
            )
        return TestRunResult(
            model_id=test.model_id,
            test_key=test_key,
            success=False,
            elapsed=elapsed,
            error_message=msg,
            translation_wall=translation_wall,
            sim_wall=sim_wall,
            statistics=stats or None,
        )

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def read_result(
        self,
        test: TestModel,
        test_key: str,
        run_result: TestRunResult | None,
    ) -> TestResult:
        stats = (
            dict(run_result.statistics)
            if run_result and run_result.statistics
            else None
        )
        test_dir = self.config.work_dir / test_key
        mat_path = test_dir / self.RESULT_MAT_FILENAME

        # Enrich stats with wall-clock timing summary (mirrors DymolaBackend).
        if run_result and (
            run_result.translation_wall is not None or run_result.sim_wall is not None
        ):
            timing: dict[str, float] = {}
            if run_result.translation_wall is not None:
                timing["translation_wall"] = round(run_result.translation_wall, 2)
            if run_result.sim_wall is not None:
                timing["sim_wall"] = round(run_result.sim_wall, 2)
            if run_result.elapsed:
                acct = (run_result.translation_wall or 0.0) + (
                    run_result.sim_wall or 0.0
                )
                timing["other_wall"] = round(max(0.0, run_result.elapsed - acct), 2)
                timing["total_wall"] = round(run_result.elapsed, 2)
            stats = merge_timing_stats(stats, timing)

        if not mat_path.exists():
            msg = (
                run_result.error_message if run_result else None
            ) or f"Result file not found: {mat_path}"
            return TestResult(
                model_id=test.model_id,
                success=False,
                error_message=msg,
                statistics=stats,
            )

        diag_names = list(self.om_config.diagnostic_variables)
        needed = compute_needed_variables(mat_path, test, diag_names)

        mat_data = read_result_mat(mat_path, variable_names=needed)
        if mat_data is None:
            return TestResult(
                model_id=test.model_id,
                success=False,
                error_message=f"Failed to parse: {mat_path}",
                statistics=stats,
            )

        variables, diagnostics = extract_variables(mat_data, test, diag_names)
        stats = fold_diagnostics_into_statistics(stats, diagnostics)

        return TestResult(
            model_id=test.model_id,
            success=True,
            variables=variables,
            diagnostics=diagnostics,
            statistics=stats,
        )
