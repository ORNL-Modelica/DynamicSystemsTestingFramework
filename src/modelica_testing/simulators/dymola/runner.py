"""Dymola-specific simulator runner with batch execution."""

import logging
import math
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import numpy as np

from ...config import Config
from ...discovery.test_registry import TestModel
from ..base import (
    SimulatorRunner,
    TestRunResult,
    TestResult,
    VariableResult,
    BatchManifest,
    resolve_variable_patterns,
)
from .log_parser import parse_dslog
from .mat_reader import list_dymola_mat_variables, read_dymola_mat

logger = logging.getLogger(__name__)


class DymolaRunner(SimulatorRunner):
    """Runs Modelica simulations using Dymola with batch execution.

    Instead of launching a separate Dymola process per test, tests are
    grouped into batches. Each batch runs in a single Dymola session:
    load libraries once, run N tests, exit. This dramatically reduces
    startup overhead for large test suites.
    """

    def run_tests(self, tests: list[TestModel]) -> list[BatchManifest]:
        """Run tests in batches with parallelism."""
        if not tests:
            return []

        self.config.work_dir.mkdir(parents=True, exist_ok=True)
        total = len(tests)

        # Build test keys and manifest
        test_items = []
        manifest_map = {}
        for i, test in enumerate(tests):
            test_key = f"test_{i + 1:04d}"
            manifest_map[test_key] = {"model_id": test.model_id, "ref_id": None}
            test_items.append((test, test_key))

        manifest = BatchManifest(
            batch_id=0,
            work_dir=self.config.work_dir,
            manifest=manifest_map,
        )
        manifest.save()

        # Clean and create per-test directories, generate per-test .mos scripts
        import shutil
        for test, test_key in test_items:
            test_dir = self.config.work_dir / test_key
            if test_dir.exists():
                shutil.rmtree(test_dir)
            test_dir.mkdir(parents=True, exist_ok=True)
            _generate_test_mos(test, test_key, test_dir)

        # Generate shared startup and shutdown scripts
        startup_path = _generate_startup_mos(self.config)
        shutdown_path = _generate_shutdown_mos(self.config)

        # Split tests into batches for parallel workers
        n_workers = max(1, self.config.parallel)
        batch_size = math.ceil(total / n_workers)
        batches = []
        for i in range(0, total, batch_size):
            batches.append(test_items[i:i + batch_size])

        print(
            f"Running {total} tests in {len(batches)} batch(es)"
            f" (parallel={n_workers}, timeout={self.config.timeout}s/test)",
            file=sys.stderr,
        )

        # Generate and run batch scripts
        all_results: list[TestRunResult] = []

        if n_workers <= 1:
            # Sequential: single batch
            results = self._run_batch(
                batches[0], startup_path, shutdown_path, 0, total
            )
            all_results.extend(results)
        else:
            # Parallel: one thread per batch
            from concurrent.futures import ThreadPoolExecutor, as_completed

            offset = 0
            futures = {}
            with ThreadPoolExecutor(max_workers=n_workers) as pool:
                for batch_idx, batch in enumerate(batches):
                    future = pool.submit(
                        self._run_batch, batch, startup_path, shutdown_path,
                        offset, total,
                    )
                    futures[future] = batch_idx
                    offset += len(batch)

                for future in as_completed(futures):
                    all_results.extend(future.result())

        # Summary
        n_ok = sum(1 for r in all_results if r.success)
        n_fail = sum(1 for r in all_results if not r.success and not r.timed_out)
        n_timeout = sum(1 for r in all_results if r.timed_out)
        total_time = sum(r.elapsed for r in all_results)

        print(file=sys.stderr)
        print(
            f"Simulations complete: {n_ok} ok, {n_fail} failed, "
            f"{n_timeout} timed out ({total_time:.0f}s total)",
            file=sys.stderr,
        )

        manifest.results = all_results
        return [manifest]

    def _run_batch(
        self,
        test_items: list[tuple[TestModel, str]],
        startup_path: Path,
        shutdown_path: Path,
        index_offset: int,
        total: int,
    ) -> list[TestRunResult]:
        """Run a batch of tests in a single Dymola session."""
        work_dir = self.config.work_dir
        batch_id = index_offset  # Use offset as unique batch ID

        # Generate the batch script
        batch_script = _generate_batch_mos(
            test_items, startup_path, shutdown_path, work_dir, batch_id
        )

        # Calculate total timeout: per-test timeout * number of tests + startup overhead
        startup_overhead = 120  # seconds for library loading
        total_timeout = sum(
            t.timeout if t.timeout is not None else self.config.timeout
            for t, _ in test_items
        ) + startup_overhead

        # Run the batch
        start_time = time.monotonic()

        try:
            cmd = [self.config.simulator_path]
            if not self.config.show_ide:
                cmd.append("-nowindow")
            cmd.append(str(batch_script))

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(work_dir),
            )
            stdout, stderr = proc.communicate(timeout=total_timeout)
            batch_elapsed = time.monotonic() - start_time

        except subprocess.TimeoutExpired:
            batch_elapsed = time.monotonic() - start_time
            proc.kill()
            proc.communicate()
            # Mark all tests as timed out
            results = []
            for test, test_key in test_items:
                results.append(TestRunResult(
                    model_id=test.model_id,
                    test_key=test_key,
                    success=False,
                    elapsed=batch_elapsed / len(test_items),
                    error_message=f"Batch timed out after {total_timeout}s",
                    timed_out=True,
                ))
            return results

        except FileNotFoundError:
            results = []
            for test, test_key in test_items:
                results.append(TestRunResult(
                    model_id=test.model_id,
                    test_key=test_key,
                    success=False,
                    elapsed=0.0,
                    error_message=f"Dymola not found: {self.config.simulator_path}",
                ))
            return results

        # Evaluate results per test: check if .mat file was produced
        results = []
        from ..base import _print_progress
        for i, (test, test_key) in enumerate(test_items):
            test_dir = work_dir / test_key
            mat_path = test_dir / "dsres.mat"
            short_name = test.model_id.rsplit(".", 1)[-1]
            label = f"{test_key} {short_name}"

            # Parse both dslog.txt (simulation runtime) and translation_log.txt (structural)
            statistics = parse_dslog(test_dir / "dslog.txt")
            translation_stats = parse_dslog(test_dir / "translation_log.txt")
            if translation_stats:
                if statistics is None:
                    statistics = translation_stats
                else:
                    # Merge: each log produces different top-level keys
                    # (dslog → "simulation", translation → "translation")
                    for key, value in translation_stats.items():
                        if key not in statistics:
                            statistics[key] = value
                        elif isinstance(value, dict) and isinstance(statistics[key], dict):
                            statistics[key].update(value)
                        else:
                            statistics[key] = value

            # Check for translation failure (defense in depth)
            translation_failed = False
            translation_log = test_dir / "translation_log.txt"
            if translation_log.exists():
                try:
                    tlog = translation_log.read_text(encoding="utf-8", errors="replace")
                    if "Translation aborted" in tlog or tlog.strip().endswith("= false"):
                        translation_failed = True
                except OSError:
                    pass

            if mat_path.exists() and not translation_failed:
                _print_progress(
                    index_offset + i + 1, total, label, "ok",
                    elapsed=batch_elapsed / len(test_items),
                )
                results.append(TestRunResult(
                    model_id=test.model_id,
                    test_key=test_key,
                    success=True,
                    elapsed=batch_elapsed / len(test_items),
                    statistics=statistics,
                ))
            else:
                msg = "Translation failed" if translation_failed else "No result file produced"
                # Try to get error from dslog
                dslog_path = test_dir / "dslog.txt"
                if dslog_path.exists():
                    try:
                        log_text = dslog_path.read_text(encoding="utf-8", errors="replace")
                        if "ERROR" in log_text or "error" in log_text:
                            # Extract last few lines for context
                            lines = log_text.strip().split("\n")
                            msg = " | ".join(lines[-3:])
                    except OSError:
                        pass

                _print_progress(
                    index_offset + i + 1, total, label, "FAIL",
                    elapsed=batch_elapsed / len(test_items), detail=msg[:80],
                )
                results.append(TestRunResult(
                    model_id=test.model_id,
                    test_key=test_key,
                    success=False,
                    elapsed=batch_elapsed / len(test_items),
                    error_message=msg,
                    statistics=statistics,
                ))

        return results

    def read_result(
        self,
        test: TestModel,
        test_key: str,
        run_result: Optional[TestRunResult],
    ) -> TestResult:
        stats = run_result.statistics if run_result else None
        test_dir = self.config.work_dir / test_key
        mat_path = test_dir / "dsres.mat"

        if not mat_path.exists():
            return TestResult(
                model_id=test.model_id,
                success=False,
                error_message=f"Result file not found: {mat_path}",
                statistics=stats,
            )

        # Phase 1: Get variable names (fast — reads only the name matrix)
        # Then resolve patterns to determine which variables we actually need.
        needed_vars = _compute_needed_variables(
            mat_path, test, self.config.diagnostic_variables,
        )

        # Phase 2: Load only needed variables from the .mat file
        mat_data = read_dymola_mat(mat_path, variable_names=needed_vars)
        if mat_data is None:
            return TestResult(
                model_id=test.model_id,
                success=False,
                error_message=f"Failed to parse: {mat_path}",
                statistics=stats,
            )

        variables, diagnostics = _extract_variables(
            mat_data, test, self.config.diagnostic_variables,
        )

        # Add diagnostic final values to statistics
        if diagnostics:
            if stats is None:
                stats = {}
            for diag in diagnostics:
                if len(diag.values) > 0:
                    sig = 7 if diag.values.dtype == np.float32 else 15
                    stats[diag.name] = float(f"%.{sig}g" % diag.values[-1])

        return TestResult(
            model_id=test.model_id,
            success=True,
            variables=variables,
            diagnostics=diagnostics,
            statistics=stats,
        )


# ---------------------------------------------------------------------------
# .mos script generation
# ---------------------------------------------------------------------------

def _generate_startup_mos(config: Config) -> Path:
    """Generate startup.mos: load dependencies, main library, and setup commands."""
    work_dir = config.work_dir
    lines = [
        "// Startup: load libraries and configure environment",
        f'cd("{work_dir.as_posix()}");',
    ]

    # Load dependencies first
    for dep_path in config.dependencies:
        dep_pkg = Path(dep_path).resolve() / "package.mo"
        lines.append(f'openModel("{dep_pkg.as_posix()}");')

    # Load main library
    lines.append(f'openModel("{(config.library_dir / "package.mo").as_posix()}");')

    # Dymola-specific framework settings (always enabled)
    lines.append("")
    lines.append("// Framework settings")
    lines.append("OutputCPUtime := true;")
    lines.append("Advanced.UI.TranslationInCommandLog := true;")

    # Simulator setup commands (e.g., OutputCPUtime = true)
    if config.simulator_setup:
        lines.append("")
        lines.append("// Simulator setup")
        for cmd in config.simulator_setup:
            # Ensure command ends with semicolon
            cmd = cmd.strip()
            if not cmd.endswith(";"):
                cmd += ";"
            lines.append(cmd)

    script_path = work_dir / "startup.mos"
    script_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return script_path


def _generate_shutdown_mos(config: Config) -> Path:
    """Generate shutdown.mos: exit Dymola."""
    lines = [
        "// Shutdown",
        "Modelica.Utilities.System.exit();",
    ]
    script_path = config.work_dir / "shutdown.mos"
    script_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return script_path


def _generate_test_mos(
    test: TestModel,
    test_key: str,
    test_dir: Path,
) -> Path:
    """Generate a per-test .mos script: cd to test dir and simulate."""
    parts = [f'"{test.model_id}"']

    if test.stop_time != 1.0:
        if test.stop_time == int(test.stop_time):
            parts.append(f"stopTime={int(test.stop_time)}")
        else:
            parts.append(f"stopTime={test.stop_time}")

    # numberOfIntervals takes precedence over outputInterval if both are set
    if test.number_of_intervals is not None:
        parts.append(f"numberOfIntervals={test.number_of_intervals}")
    elif test.output_interval is not None:
        parts.append(f"outputInterval={test.output_interval}")

    parts.append(f'method="{test.method}"')
    parts.append(f"tolerance={test.tolerance}")
    parts.append('resultFile="dsres"')

    lines = [
        f'// {test.model_id}',
        f'cd("{test_dir.as_posix()}");',
        f'clearlog();',
        f'simulateModel({",".join(parts)});',
        f'savelog("translation_log.txt");',
    ]

    script_path = test_dir / "simulate.mos"
    script_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return script_path


def _generate_batch_mos(
    test_items: list[tuple[TestModel, str]],
    startup_path: Path,
    shutdown_path: Path,
    work_dir: Path,
    batch_id: int,
) -> Path:
    """Generate a batch .mos script that runs startup, all tests, then shutdown."""
    lines = [
        f"// Batch {batch_id}: {len(test_items)} tests",
        f'RunScript("{startup_path.as_posix()}");',
    ]

    for test, test_key in test_items:
        test_mos = work_dir / test_key / "simulate.mos"
        lines.append(f'RunScript("{test_mos.as_posix()}");')

    lines.append(f'RunScript("{shutdown_path.as_posix()}");')

    script_path = work_dir / f"batch_{batch_id:04d}.mos"
    script_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return script_path


# ---------------------------------------------------------------------------
# Result extraction
# ---------------------------------------------------------------------------

def _compute_needed_variables(
    mat_path: Path,
    test: TestModel,
    diagnostic_vars: list[str] = None,
) -> Optional[set[str]]:
    """Determine which variables need to be extracted from a .mat file.

    Returns a set of variable names, or None to load everything (fallback).
    This avoids loading all data for large files with thousands of variables.
    """
    if diagnostic_vars is None:
        diagnostic_vars = []

    needed: set[str] = set()

    # UnitTests variables
    if test.source in ("unit_tests", "both") and test.n_vars > 0:
        for i in range(1, test.n_vars + 1):
            needed.add(f"unitTests.x[{i}]")

    # Pattern-based variables — need the full name list to resolve globs
    if test.variable_patterns:
        all_names = list_dymola_mat_variables(mat_path)
        if all_names is None:
            return None  # fallback: load everything
        resolved = resolve_variable_patterns(test.variable_patterns, all_names)
        needed.update(resolved)

    # Diagnostic variables
    needed.update(diagnostic_vars)

    return needed if needed else None


def _extract_variables(
    mat_data: dict,
    test: TestModel,
    diagnostic_vars: list[str] = None,
) -> tuple[list[VariableResult], list[VariableResult]]:
    """Extract tracked variables and diagnostic variables from parsed mat data.

    Returns (variables, diagnostics).
    """
    if diagnostic_vars is None:
        diagnostic_vars = []
    results = []
    seen_names: set[str] = set()
    idx = 1

    # 1. UnitTests variables (source = "unit_tests" or "both")
    if test.source in ("unit_tests", "both") and test.n_vars > 0:
        for i in range(1, test.n_vars + 1):
            var_name = f"unitTests.x[{i}]"
            if var_name in mat_data:
                time, values = mat_data[var_name]
                expr = test.x_expressions[i - 1] if i - 1 < len(test.x_expressions) else var_name
                results.append(VariableResult(index=idx, time=time, values=values, name=expr))
                seen_names.add(var_name)
                seen_names.add(expr)
                idx += 1
            else:
                logger.warning(
                    "Variable %s not found in simulation output for %s",
                    var_name, test.model_id,
                )

    # 2. Pattern-based variables (source = "spec" or "both")
    if test.variable_patterns:
        available = list(mat_data.keys())
        resolved = resolve_variable_patterns(test.variable_patterns, available)
        for var_name in resolved:
            if var_name in seen_names:
                continue
            if var_name in mat_data:
                time, values = mat_data[var_name]
                results.append(VariableResult(index=idx, time=time, values=values, name=var_name))
                seen_names.add(var_name)
                idx += 1

    # 3. Diagnostic variables (auto-captured, not compared)
    diagnostics = []
    diag_idx = 1
    for var_name in diagnostic_vars:
        if var_name in mat_data:
            time, values = mat_data[var_name]
            diagnostics.append(VariableResult(
                index=diag_idx, time=time, values=values, name=var_name,
            ))
            diag_idx += 1

    return results, diagnostics
