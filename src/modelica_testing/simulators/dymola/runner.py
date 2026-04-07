"""Dymola-specific simulator runner."""

import logging
import subprocess
import time
from pathlib import Path
from typing import Optional

from ...config import Config
from ...discovery.test_registry import TestModel
from ..base import (
    SimulatorRunner,
    TestRunResult,
    TestResult,
    VariableResult,
    _print_progress,
    resolve_variable_patterns,
)
from .log_parser import parse_dslog
from .mat_reader import read_dymola_mat

logger = logging.getLogger(__name__)


class DymolaRunner(SimulatorRunner):
    """Runs Modelica simulations using Dymola."""

    def run_single_test(
        self,
        test: TestModel,
        test_key: str,
        index: int,
        total: int,
    ) -> TestRunResult:
        # Each test gets its own subdirectory to avoid file conflicts
        test_dir = self.config.work_dir / test_key
        test_dir.mkdir(parents=True, exist_ok=True)

        script_path = _generate_mos(test, test_key, test_dir, self.config)
        short_name = test.model_id.rsplit(".", 1)[-1]

        _print_progress(index, total, short_name, "running")

        start_time = time.monotonic()
        try:
            cmd = [self.config.simulator_path]
            if not self.config.show_ide:
                cmd.append("-nowindow")
            cmd.append(str(script_path))

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(test_dir),
            )
            stdout, stderr = proc.communicate(timeout=self.config.timeout)
            elapsed = time.monotonic() - start_time

            # dslog.txt is in the per-test directory — no rename needed
            statistics = parse_dslog(test_dir / "dslog.txt")

            mat_path = test_dir / f"{test_key}.mat"
            if proc.returncode != 0 or not mat_path.exists():
                msg = stderr.decode(errors="replace").strip() or "Dymola returned non-zero exit code"
                _print_progress(index, total, short_name, "FAIL", elapsed, msg)
                return TestRunResult(
                    model_id=test.model_id,
                    test_key=test_key,
                    success=False,
                    elapsed=elapsed,
                    error_message=msg,
                    statistics=statistics,
                )

            _print_progress(index, total, short_name, "ok", elapsed)
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=True,
                elapsed=elapsed,
                statistics=statistics,
            )

        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - start_time
            proc.kill()
            proc.communicate()
            msg = f"Timed out after {self.config.timeout}s"
            _print_progress(index, total, short_name, "TIMEOUT", elapsed, msg)
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                elapsed=elapsed,
                error_message=msg,
                timed_out=True,
            )

        except FileNotFoundError:
            elapsed = time.monotonic() - start_time
            msg = f"Dymola not found: {self.config.simulator_path}"
            _print_progress(index, total, short_name, "ERROR", elapsed, msg)
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                elapsed=elapsed,
                error_message=msg,
            )

    def read_result(
        self,
        test: TestModel,
        test_key: str,
        run_result: Optional[TestRunResult],
    ) -> TestResult:
        stats = run_result.statistics if run_result else None
        test_dir = self.config.work_dir / test_key
        mat_path = test_dir / f"{test_key}.mat"

        if not mat_path.exists():
            return TestResult(
                model_id=test.model_id,
                success=False,
                error_message=f"Result file not found: {mat_path}",
                statistics=stats,
            )

        mat_data = read_dymola_mat(mat_path)
        if mat_data is None:
            return TestResult(
                model_id=test.model_id,
                success=False,
                error_message=f"Failed to parse: {mat_path}",
                statistics=stats,
            )

        variables = _extract_variables(mat_data, test)
        return TestResult(
            model_id=test.model_id,
            success=True,
            variables=variables,
            statistics=stats,
        )


def _generate_mos(
    test: TestModel,
    test_key: str,
    test_dir: Path,
    config: Config,
) -> Path:
    """Generate a .mos script for a single test."""
    parts = [f'"{test.model_id}"']

    if test.stop_time != 1.0:
        if test.stop_time == int(test.stop_time):
            parts.append(f"stopTime={int(test.stop_time)}")
        else:
            parts.append(f"stopTime={test.stop_time}")

    if test.number_of_intervals is not None:
        parts.append(f"numberOfIntervals={test.number_of_intervals}")

    if test.output_interval is not None:
        parts.append(f"outputInterval={test.output_interval}")

    parts.append(f'method="{test.method}"')
    parts.append(f"tolerance={test.tolerance}")
    parts.append(f'resultFile="{test_key}"')

    lines = [
        f'// {test.model_id}',
        f'cd("{test_dir.as_posix()}");',
    ]

    for dep_path in config.dependencies:
        dep_pkg = Path(dep_path).resolve() / "package.mo"
        lines.append(f'openModel("{dep_pkg.as_posix()}");')

    lines.extend([
        f'openModel("{(config.library_dir / "package.mo").as_posix()}");',
        # cd again after openModel — Dymola can change cwd when opening a library
        f'cd("{test_dir.as_posix()}");',
        f'simulateModel({",".join(parts)});',
        'Modelica.Utilities.System.exit();',
    ])

    script_path = test_dir / f"{test_key}.mos"
    script_path.write_text("\n".join(lines), encoding="utf-8")
    return script_path


def _extract_variables(
    mat_data: dict, test: TestModel
) -> list[VariableResult]:
    """Extract tracked variables from parsed mat data.

    Handles three cases:
    - UnitTests variables (unitTests.x[1..n]) from in-model component
    - Pattern-based variables from external spec (globs, wildcards)
    - Both merged together (deduplicated)
    """
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

    return results
