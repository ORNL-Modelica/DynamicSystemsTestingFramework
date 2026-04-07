"""Abstract simulator interface and common result types."""

import json
import logging
import sys
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Optional

import numpy as np

from ..config import Config
from ..discovery.test_registry import TestModel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Common result types (shared across all simulator backends)
# ---------------------------------------------------------------------------

@dataclass
class VariableResult:
    """Time series for a single tracked variable."""
    index: int  # 1-based
    time: np.ndarray
    values: np.ndarray
    name: str = ""  # Variable name (e.g., "pipe.T[1]" or "unitTests.x[1]")


@dataclass
class TestResult:
    """Results from a single test simulation."""
    model_id: str
    success: bool
    variables: list[VariableResult] = field(default_factory=list)
    error_message: Optional[str] = None
    statistics: Optional[dict] = None


@dataclass
class TestRunResult:
    """Result of running a single test."""
    model_id: str
    test_key: str
    success: bool
    elapsed: float = 0.0
    error_message: Optional[str] = None
    timed_out: bool = False
    statistics: Optional[dict] = None


# Variables to exclude when resolving "*" or glob patterns (Dymola internals, etc.)
_EXCLUDE_PREFIXES = ("der(", "$", "_")
_EXCLUDE_NAMES = {"time"}


def _pattern_to_regex(pattern: str):
    """Convert a simple glob pattern to a compiled regex.

    Only * (match any) and ? (match one) are wildcards.
    All other characters (including Modelica's [] for array indices,
    parentheses, dots) are treated as literals.
    """
    import re as _re
    parts = []
    for ch in pattern:
        if ch == "*":
            parts.append(".*")
        elif ch == "?":
            parts.append(".")
        else:
            parts.append(_re.escape(ch))
    return _re.compile("".join(parts))


def resolve_variable_patterns(
    patterns: list[str],
    available_vars: list[str],
) -> list[str]:
    """Resolve variable patterns against available variable names from simulation.

    Pattern types:
    - Explicit name: "pipe.T[1]" — must exist exactly
    - Glob: "medium.T*" — fnmatch against available names
    - Wildcard: "*" — all non-internal variables
    - Empty list: [] — no variables (simulate-only mode)

    Returns sorted list of resolved variable names.
    """
    if not patterns:
        return []

    resolved = set()
    for pattern in patterns:
        if pattern == "*":
            # All non-internal variables
            for var in available_vars:
                if var in _EXCLUDE_NAMES:
                    continue
                if any(var.startswith(p) for p in _EXCLUDE_PREFIXES):
                    continue
                resolved.add(var)
        elif "*" in pattern or "?" in pattern:
            # Glob pattern — only * and ? are wildcards, everything else
            # (including [] from Modelica array indices) is literal
            regex = _pattern_to_regex(pattern)
            matched = [v for v in available_vars if regex.fullmatch(v)]
            if not matched:
                logger.warning("Pattern '%s' matched no variables", pattern)
            resolved.update(matched)
        else:
            # Explicit name
            if pattern in available_vars:
                resolved.add(pattern)
            else:
                logger.warning("Variable '%s' not found in results", pattern)

    return sorted(resolved)


@dataclass
class BatchManifest:
    """Maps numeric test IDs to model IDs and tracks run results."""
    batch_id: int
    work_dir: Path
    manifest: dict[str, str]  # test_NNNN -> model_id
    results: list[TestRunResult] = field(default_factory=list)

    def test_dir(self, test_key: str) -> Path:
        """Per-test subdirectory for all simulation artifacts."""
        return self.work_dir / test_key

    def mat_file(self, test_key: str) -> Path:
        return self.test_dir(test_key) / f"{test_key}.mat"

    def save(self) -> Path:
        path = self.work_dir / f"batch_{self.batch_id:04d}_manifest.json"
        path.write_text(json.dumps(self.manifest, indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> "BatchManifest":
        data = json.loads(path.read_text(encoding="utf-8"))
        batch_id = int(path.stem.split("_")[1])
        return cls(batch_id=batch_id, work_dir=path.parent, manifest=data)


# ---------------------------------------------------------------------------
# Progress output (shared across simulators)
# ---------------------------------------------------------------------------

_print_lock = Lock()


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
            print("".join(parts), end="\r", flush=True, file=sys.stderr)
            return

        parts.append(f" -> {status}")
        if elapsed is not None:
            parts.append(f" ({elapsed:.1f}s)")
        if detail:
            parts.append(f"  [{detail}]")

        print("".join(parts), file=sys.stderr)


# ---------------------------------------------------------------------------
# Abstract simulator interface
# ---------------------------------------------------------------------------

class SimulatorRunner(ABC):
    """Abstract interface for running Modelica simulations.

    Subclasses must implement read_result(). They should override
    run_tests() for batch execution, or implement run_single_test()
    to use the default per-process execution.
    """

    def __init__(self, config: Config):
        self.config = config

    def run_single_test(
        self,
        test: TestModel,
        test_key: str,
        index: int,
        total: int,
    ) -> TestRunResult:
        """Run a single test and return the result.

        Override this for per-process execution (default run_tests uses this).
        Not needed if run_tests is overridden entirely (e.g., batch execution).
        """
        raise NotImplementedError("Override run_tests() or run_single_test()")

    @abstractmethod
    def read_result(
        self,
        test: TestModel,
        test_key: str,
        run_result: TestRunResult,
    ) -> TestResult:
        """Read simulation output for a completed test."""
        ...

    def run_tests(self, tests: list[TestModel]) -> list[BatchManifest]:
        """Run all tests with progress, timeouts, and parallelism.

        Subclasses generally don't need to override this — override
        run_single_test and read_result instead.
        """
        if not tests:
            return []

        self.config.work_dir.mkdir(parents=True, exist_ok=True)
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
            work_dir=self.config.work_dir,
            manifest=manifest_map,
        )
        manifest.save()

        print(
            f"Running {total} tests"
            f" (parallel={self.config.parallel}, timeout={self.config.timeout}s)",
            file=sys.stderr,
        )

        run_results: list[TestRunResult] = []

        if self.config.parallel <= 1:
            for test, test_key, idx in test_items:
                result = self.run_single_test(test, test_key, idx, total)
                run_results.append(result)
        else:
            futures = {}
            with ThreadPoolExecutor(max_workers=self.config.parallel) as pool:
                for test, test_key, idx in test_items:
                    future = pool.submit(
                        self.run_single_test, test, test_key, idx, total
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

    def read_results(
        self,
        manifests: list[BatchManifest],
        tests: list[TestModel],
    ) -> dict[str, TestResult]:
        """Read all simulation results from completed batch runs."""
        test_lookup = {t.model_id: t for t in tests}
        results: dict[str, TestResult] = {}

        run_results = {}
        for manifest in manifests:
            for rr in manifest.results:
                run_results[rr.model_id] = rr

        for manifest in manifests:
            for test_key, model_id in manifest.manifest.items():
                rr = run_results.get(model_id)
                test_model = test_lookup.get(model_id)

                if rr and not rr.success:
                    results[model_id] = TestResult(
                        model_id=model_id,
                        success=False,
                        error_message=rr.error_message or "Simulation failed",
                        statistics=rr.statistics if rr else None,
                    )
                    continue

                if test_model is None:
                    results[model_id] = TestResult(
                        model_id=model_id,
                        success=False,
                        error_message="Test model not found in discovery",
                    )
                    continue

                result = self.read_result(test_model, test_key, rr)
                results[model_id] = result

        return results

    def read_last_results(
        self,
        tests: list[TestModel],
    ) -> dict[str, TestResult]:
        """Read results from the most recent batch run in the work directory."""
        work_dir = self.config.work_dir
        if not work_dir.exists():
            return {}

        manifest_paths = sorted(work_dir.glob("batch_*_manifest.json"))
        if not manifest_paths:
            return {}

        manifests = [BatchManifest.load(p) for p in manifest_paths]
        return self.read_results(manifests, tests)
