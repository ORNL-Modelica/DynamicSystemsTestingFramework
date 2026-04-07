"""Dymola-specific simulator runner."""

import logging
import shutil
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
        script_path = _generate_mos(test, test_key, self.config)
        short_name = test.model_id.rsplit(".", 1)[-1]

        _print_progress(index, total, short_name, "running")

        start_time = time.monotonic()
        try:
            proc = subprocess.Popen(
                [self.config.dymola_path, "-nowindow", str(script_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(self.config.work_dir),
            )
            stdout, stderr = proc.communicate(timeout=self.config.timeout)
            elapsed = time.monotonic() - start_time

            statistics = _capture_dslog(self.config.work_dir, test_key)

            mat_path = self.config.work_dir / f"{test_key}.mat"
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
            msg = f"Dymola not found: {self.config.dymola_path}"
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
        mat_path = self.config.work_dir / f"{test_key}.mat"

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

        variables = _extract_unit_test_vars(mat_data, test.n_vars)
        return TestResult(
            model_id=test.model_id,
            success=True,
            variables=variables,
            statistics=stats,
        )


def _generate_mos(
    test: TestModel,
    test_key: str,
    config: Config,
) -> Path:
    """Generate a .mos script for a single test."""
    work_dir = config.work_dir

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
        f'cd("{work_dir.as_posix()}");',
    ]

    for dep_path in config.dependencies:
        dep_pkg = Path(dep_path).resolve() / "package.mo"
        lines.append(f'openModel("{dep_pkg.as_posix()}");')

    lines.extend([
        f'openModel("{(config.library_dir / "package.mo").as_posix()}");',
        f'simulateModel({",".join(parts)});',
        'Modelica.Utilities.System.exit();',
    ])

    script_path = work_dir / f"{test_key}.mos"
    script_path.write_text("\n".join(lines), encoding="utf-8")
    return script_path


def _capture_dslog(work_dir: Path, test_key: str) -> Optional[dict]:
    """Capture and rename dslog.txt after a test run."""
    dslog_path = work_dir / "dslog.txt"
    if not dslog_path.exists():
        return None

    saved_path = work_dir / f"{test_key}_dslog.txt"
    try:
        dslog_path.rename(saved_path)
    except OSError:
        try:
            shutil.copy2(dslog_path, saved_path)
        except OSError:
            pass

    return parse_dslog(saved_path)


def _extract_unit_test_vars(
    mat_data: dict, n_vars: int
) -> list[VariableResult]:
    """Extract unitTests.x[1..n] from parsed mat data."""
    results = []
    for i in range(1, n_vars + 1):
        var_name = f"unitTests.x[{i}]"
        if var_name in mat_data:
            time, values = mat_data[var_name]
            results.append(VariableResult(index=i, time=time, values=values))
        else:
            logger.warning("Variable %s not found in results", var_name)
    return results
