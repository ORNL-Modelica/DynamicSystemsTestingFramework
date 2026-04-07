"""Drive Dymola simulations with per-test progress, timeouts, and parallelism."""

import json
import logging
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Optional

from ..config import Config
from ..discovery.test_registry import TestModel

logger = logging.getLogger(__name__)

# Lock for thread-safe console output
_print_lock = Lock()


@dataclass
class TestRunResult:
    """Result of running a single test in Dymola."""
    model_id: str
    test_key: str
    success: bool
    elapsed: float = 0.0
    error_message: Optional[str] = None
    timed_out: bool = False
    statistics: Optional[dict] = None


@dataclass
class BatchManifest:
    """Maps numeric test IDs to model IDs and tracks run results."""
    batch_id: int
    work_dir: Path
    manifest: dict[str, str]  # test_NNNN -> model_id
    results: list[TestRunResult] = field(default_factory=list)

    def mat_file(self, test_key: str) -> Path:
        return self.work_dir / f"{test_key}.mat"

    def save(self) -> Path:
        path = self.work_dir / f"batch_{self.batch_id:04d}_manifest.json"
        path.write_text(json.dumps(self.manifest, indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> "BatchManifest":
        data = json.loads(path.read_text(encoding="utf-8"))
        batch_id = int(path.stem.split("_")[1])
        return cls(batch_id=batch_id, work_dir=path.parent, manifest=data)


def _generate_single_mos(
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

    # Load dependencies first (order matters — dependencies before main library)
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


def _run_single_test(
    test: TestModel,
    test_key: str,
    index: int,
    total: int,
    config: Config,
) -> TestRunResult:
    """Run a single test in Dymola with timeout."""
    script_path = _generate_single_mos(test, test_key, config)
    short_name = test.model_id.rsplit(".", 1)[-1]

    _print_progress(index, total, short_name, "running")

    start_time = time.monotonic()
    try:
        proc = subprocess.Popen(
            [config.dymola_path, "-nowindow", str(script_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(config.work_dir),
        )
        stdout, stderr = proc.communicate(timeout=config.timeout)
        elapsed = time.monotonic() - start_time

        # Capture dslog.txt before next test overwrites it
        statistics = _capture_dslog(config.work_dir, test_key)

        mat_path = config.work_dir / f"{test_key}.mat"
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
        proc.communicate()  # clean up
        msg = f"Timed out after {config.timeout}s"
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
        msg = f"Dymola not found: {config.dymola_path}"
        _print_progress(index, total, short_name, "ERROR", elapsed, msg)
        return TestRunResult(
            model_id=test.model_id,
            test_key=test_key,
            success=False,
            elapsed=elapsed,
            error_message=msg,
        )


def _capture_dslog(work_dir: Path, test_key: str) -> Optional[dict]:
    """Capture and rename dslog.txt after a test run.

    Dymola overwrites dslog.txt on each simulation, so we rename it
    to <test_key>_dslog.txt to preserve it.
    """
    from .dslog_parser import parse_dslog

    dslog_path = work_dir / "dslog.txt"
    if not dslog_path.exists():
        return None

    # Rename to preserve per-test log
    saved_path = work_dir / f"{test_key}_dslog.txt"
    try:
        dslog_path.rename(saved_path)
    except OSError:
        # On Windows, rename can fail if file is locked; try copy instead
        import shutil
        try:
            shutil.copy2(dslog_path, saved_path)
        except OSError:
            pass

    return parse_dslog(saved_path)


def _print_progress(
    index: int,
    total: int,
    name: str,
    status: str,
    elapsed: Optional[float] = None,
    detail: Optional[str] = None,
):
    """Thread-safe progress output."""
    with _print_lock:
        pct = (index / total) * 100
        bar_len = 30
        filled = int(bar_len * index / total)
        bar = "=" * filled + "-" * (bar_len - filled)

        parts = [f"  [{bar}] {index}/{total} ({pct:3.0f}%)  {name}"]

        if status == "running":
            parts.append("...")
            # Use \r to overwrite the line
            print("".join(parts), end="\r", flush=True, file=sys.stderr)
            return

        parts.append(f" -> {status}")
        if elapsed is not None:
            parts.append(f" ({elapsed:.1f}s)")
        if detail:
            parts.append(f"  [{detail}]")

        # Print with newline (overwrites the "running" line)
        print("".join(parts), file=sys.stderr)


def run_tests(
    tests: list[TestModel],
    config: Config,
) -> list[BatchManifest]:
    """Run tests in Dymola with per-test timeout, progress, and parallelism.

    Returns list of BatchManifest objects for result reading.
    """
    if not tests:
        return []

    config.work_dir.mkdir(parents=True, exist_ok=True)
    total = len(tests)

    # Build test keys and manifest
    test_items = []
    manifest_map = {}
    for i, test in enumerate(tests):
        test_key = f"test_{i + 1:04d}"
        manifest_map[test_key] = test.model_id
        test_items.append((test, test_key, i + 1))

    manifest = BatchManifest(
        batch_id=0,
        work_dir=config.work_dir,
        manifest=manifest_map,
    )
    manifest.save()

    print(
        f"Running {total} tests"
        f" (parallel={config.parallel}, timeout={config.timeout}s)",
        file=sys.stderr,
    )

    run_results: list[TestRunResult] = []

    if config.parallel <= 1:
        # Sequential execution
        for test, test_key, idx in test_items:
            result = _run_single_test(test, test_key, idx, total, config)
            run_results.append(result)
    else:
        # Parallel execution with thread pool
        futures = {}
        with ThreadPoolExecutor(max_workers=config.parallel) as pool:
            for test, test_key, idx in test_items:
                future = pool.submit(
                    _run_single_test, test, test_key, idx, total, config
                )
                futures[future] = test.model_id

            for future in as_completed(futures):
                run_results.append(future.result())

    # Summary
    n_ok = sum(1 for r in run_results if r.success)
    n_fail = sum(1 for r in run_results if not r.success and not r.timed_out)
    n_timeout = sum(1 for r in run_results if r.timed_out)
    total_time = sum(r.elapsed for r in run_results)

    print(file=sys.stderr)
    print(
        f"Simulations complete: {n_ok} ok, {n_fail} failed, "
        f"{n_timeout} timed out ({total_time:.0f}s total)",
        file=sys.stderr,
    )

    manifest.results = run_results
    return [manifest]
