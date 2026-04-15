"""Abstract simulator interface and common result types."""

import json
import logging
import sys
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Optional

import numpy as np

from ..config import Config
from ..discovery.test_registry import TestModel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Backend capabilities + dataset types (Phase 1 abstraction)
# ---------------------------------------------------------------------------
# See docs/vision.md and docs/extensibility.md. Each backend declares what it
# supports; framework features gate on these rather than on backend class.

class Capability(str, Enum):
    """Declared abilities of a simulator backend."""

    #: Backend can hold a loaded model in memory across multiple tests.
    PERSISTENT_WORKERS = "persistent-workers"
    #: Backend exposes a non-interactive script-driven fallback.
    BATCH_FALLBACK = "batch-fallback"
    #: Backend can export a test artefact as an FMU (enables cross-backend verification).
    FMU_EXPORT = "fmu-export"
    #: Backend reads pre-recorded data instead of simulating (e.g., CSV from a test rig).
    EXPERIMENT_INGEST = "experiment-ingest"


class DatasetType(str, Enum):
    """Typed categories of result data a backend may produce.

    The framework currently materialises only ``TIME_SERIES``. Other types
    are reserved for Phase 3+ metrics (events, spectra, distributions).
    """

    TIME_SERIES = "time-series"
    SCALARS = "scalars"
    EVENTS = "events"
    SPECTRUM = "spectrum"
    DISTRIBUTION = "distribution"


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
    diagnostics: list[VariableResult] = field(default_factory=list)
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
    # Phase breakdown (captured by the persistent runner; None in batch mode)
    translation_wall: Optional[float] = None
    sim_wall: Optional[float] = None


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
    """Maps test keys to model IDs (and optionally ref IDs) and tracks run results."""
    batch_id: int
    work_dir: Path
    manifest: dict[str, dict]  # test_NNNN -> {"model_id": ..., "ref_id": ...}
    results: list[TestRunResult] = field(default_factory=list)

    def test_dir(self, test_key: str) -> Path:
        """Per-test subdirectory for all simulation artifacts."""
        return self.work_dir / test_key

    def model_id(self, test_key: str) -> str:
        """Get the model ID for a test key."""
        return self.manifest[test_key]["model_id"]

    def save(self) -> Path:
        path = self.work_dir / "batch_manifest.json"
        path.write_text(json.dumps(self.manifest, indent=2), encoding="utf-8")
        return path

    def enrich_ref_ids(self, ref_index) -> None:
        """Add ref_id to each entry using the reference index."""
        for test_key, entry in self.manifest.items():
            ref_id = ref_index.get_id(entry["model_id"])
            entry["ref_id"] = f"ref_{ref_id}" if ref_id else None
        self.save()

    @classmethod
    def load(cls, path: Path) -> "BatchManifest":
        data = json.loads(path.read_text(encoding="utf-8"))
        # Handle legacy format: test_key -> model_id string
        normalized = {}
        for key, val in data.items():
            if isinstance(val, str):
                normalized[key] = {"model_id": val, "ref_id": None}
            else:
                normalized[key] = val
        return cls(batch_id=0, work_dir=path.parent, manifest=normalized)


def assign_test_keys(
    work_dir: Path,
    tests: list,
) -> tuple[dict[str, dict], list[tuple]]:
    """Assign test_keys for the given tests, reusing existing manifest entries.

    Returns (merged_manifest_map, [(test, test_key)]).
    Existing entries for tests not in the input list are preserved
    so the manifest accumulates across runs (incremental workflow).
    Each entry gets a `last_run_at` timestamp updated for tests being run.
    """
    manifest_path = work_dir / "batch_manifest.json"
    if manifest_path.exists():
        existing = BatchManifest.load(manifest_path).manifest
    else:
        existing = {}

    model_to_key = {entry["model_id"]: tk for tk, entry in existing.items()}
    used_nums = set()
    for tk in existing:
        try:
            used_nums.add(int(tk.split("_")[-1]))
        except (ValueError, IndexError):
            pass
    next_num = max(used_nums) + 1 if used_nums else 1

    timestamp = time.time()
    test_items = []
    for test in tests:
        if test.model_id in model_to_key:
            test_key = model_to_key[test.model_id]
        else:
            test_key = f"test_{next_num:04d}"
            next_num += 1
            existing[test_key] = {"model_id": test.model_id, "ref_id": None}
        existing[test_key]["last_run_at"] = timestamp
        test_items.append((test, test_key))

    return existing, test_items


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

    Subclasses should override ``capabilities`` and ``produced_datasets`` to
    declare what they support. The framework gates features on these
    declarations rather than on backend class (see docs/extensibility.md).
    """

    #: Capabilities the backend declares (populate in subclass).
    capabilities: frozenset[Capability] = frozenset()
    #: Dataset types the backend can produce (populate in subclass).
    produced_datasets: frozenset[DatasetType] = frozenset({DatasetType.TIME_SERIES})

    def __init__(self, config: Config):
        self.config = config
        self.progress = None  # set to a ProgressReporter during run_tests()
        # Optional model_id → "ref_NNNN" map for dashboard report links
        # (set by CLI before run_tests if reference IDs are known)
        self.ref_id_map: dict[str, Optional[str]] = {}

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

        from .progress import ProgressReporter
        self.progress = ProgressReporter(self.config.work_dir, total)

        # Assign test_keys (reuse existing from prior runs if present — supports
        # incremental workflows where the manifest accumulates known tests).
        manifest_map, key_pairs = assign_test_keys(self.config.work_dir, tests)
        test_items = [
            (test, test_key, i + 1)
            for i, (test, test_key) in enumerate(key_pairs)
        ]
        for test, test_key, _ in test_items:
            report_dir = self.ref_id_map.get(test.model_id) or test_key
            self.progress.register(test_key, test.model_id, report_dir=report_dir)

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
        wall_start = time.monotonic()

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
        wall_elapsed = time.monotonic() - wall_start
        n_ok = sum(1 for r in run_results if r.success)
        n_fail = sum(1 for r in run_results if not r.success and not r.timed_out)
        n_timeout = sum(1 for r in run_results if r.timed_out)
        sum_time = sum(r.elapsed for r in run_results)

        print(file=sys.stderr)
        print(
            f"Simulations complete: {n_ok} ok, {n_fail} failed, "
            f"{n_timeout} timed out ({wall_elapsed:.0f}s elapsed, {sum_time:.0f}s total)",
            file=sys.stderr,
        )

        manifest.results = run_results
        self.progress.finalize()
        return [manifest]

    def read_results(
        self,
        manifests: list[BatchManifest],
        tests: list[TestModel],
    ) -> dict[str, TestResult]:
        """Read all simulation results from completed batch runs (parallel)."""
        test_lookup = {t.model_id: t for t in tests}
        results: dict[str, TestResult] = {}

        run_results = {}
        for manifest in manifests:
            for rr in manifest.results:
                run_results[rr.model_id] = rr

        # Build work items: (test_key, model_id, test_model, run_result)
        work_items = []
        for manifest in manifests:
            for test_key, entry in manifest.manifest.items():
                model_id = entry["model_id"]
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

                work_items.append((test_key, model_id, test_model, rr))

        if not work_items:
            return results

        total = len(work_items)
        completed = 0
        print(f"\nReading {total} result files...")
        wall_start = time.monotonic()
        per_test_elapsed: list[float] = []
        elapsed_lock = Lock()

        def _read_one(item):
            test_key, model_id, test_model, rr = item
            t0 = time.monotonic()
            res = self.read_result(test_model, test_key, rr)
            return test_key, model_id, res, time.monotonic() - t0

        # Parallelize reads with thread pool
        with ThreadPoolExecutor() as executor:
            futures = {executor.submit(_read_one, item): item for item in work_items}
            for future in as_completed(futures):
                test_key, model_id, result, dt = future.result()
                results[model_id] = result
                with elapsed_lock:
                    per_test_elapsed.append(dt)
                completed += 1
                short = model_id.rsplit(".", 1)[-1]
                _print_progress(completed, total, f"{test_key} {short}", "read")

        wall = time.monotonic() - wall_start
        total_work = sum(per_test_elapsed)
        if per_test_elapsed:
            speedup = (total_work / wall) if wall > 0 else 0.0
            avg = total_work / len(per_test_elapsed)
            slowest = max(per_test_elapsed)
            print(
                f"Read phase: {wall:.0f}s wall, {total_work:.0f}s total work, "
                f"{speedup:.1f}x parallel speedup (avg {avg:.1f}s/test, slowest {slowest:.1f}s)"
            )
        return results

    def read_last_results(
        self,
        tests: list[TestModel],
    ) -> dict[str, TestResult]:
        """Read results from the most recent batch run in the work directory."""
        work_dir = self.config.work_dir
        if not work_dir.exists():
            return {}

        manifest_paths = sorted(work_dir.glob("batch_manifest.json"))
        if not manifest_paths:
            return {}

        manifests = [BatchManifest.load(p) for p in manifest_paths]
        return self.read_results(manifests, tests)
