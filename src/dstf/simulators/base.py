"""Abstract simulator interface and common result types."""

import json
import logging
import queue
import sys
import threading
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
    error_message: str | None = None
    statistics: dict | None = None


@dataclass
class TestRunResult:
    """Result of running a single test."""

    model_id: str
    test_key: str
    success: bool
    elapsed: float = 0.0
    error_message: str | None = None
    timed_out: bool = False
    statistics: dict | None = None
    # Phase breakdown (captured by the persistent runner; None in batch mode)
    translation_wall: float | None = None
    sim_wall: float | None = None
    # Set by persistent workers when the test run left the worker process
    # dead (we called close()). The dispatch loop uses this to decide
    # whether the next restart should count against the budget — workers
    # killed for benign reasons (timeout, post-timeout disk-rescue) get a
    # fresh start without ticking the bound. Independent of `timed_out`
    # and `success`: a disk-rescued test can have success=True AND
    # worker_killed=True (the .mat was written just before the watchdog
    # killed Dymola).
    worker_killed: bool = False


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
        # review 2026-07-06 finding 66: atomic replace — a torn write made
        # every subsequent run/compare crash at startup until the file was
        # hand-deleted. Lazy import: storage imports simulators (TestResult),
        # so a module-level import here would be circular.
        from ..storage.reference_store import _atomic_write_text

        _atomic_write_text(path, json.dumps(self.manifest, indent=2))
        return path

    def enrich_ref_ids(self, ref_index) -> None:
        """Add ref_id to each entry using the reference index."""
        for _test_key, entry in self.manifest.items():
            ref_id = ref_index.get_id(entry["model_id"])
            entry["ref_id"] = f"ref_{ref_id}" if ref_id else None
        self.save()

    @classmethod
    def load(cls, path: Path) -> "BatchManifest":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError(f"expected a JSON object, got {type(data).__name__}")
        except (ValueError, OSError) as exc:
            # review 2026-07-06 finding 66: a corrupt manifest must not crash
            # startup. Quarantine it (keeping the bytes for forensics) and
            # start from an empty manifest — every caller already treats a
            # missing manifest as empty, so test keys are simply re-assigned
            # on the next run. (JSONDecodeError is a ValueError subclass.)
            corrupt = path.with_name(f"{path.name}.corrupt")
            where = ""
            try:
                path.replace(corrupt)
                where = f"; moved to {corrupt.name}"
            except OSError:
                pass
            logger.warning(
                "Corrupt batch manifest %s (%s)%s — starting with an empty manifest",
                path,
                exc,
                where,
            )
            return cls(batch_id=0, work_dir=path.parent, manifest={})
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


def _print_run_header(
    total: int,
    suffix_phrase: str,
    parallel: int,
    timeout_s: float,
    work_dir: Path | None,
    *,
    timeout_per_test: bool = False,
    metadata: dict | None = None,
) -> None:
    """Print the standard "Running N tests..." header + dashboard URL.

    Single source of truth for the header style across all runners (default
    batch loop, persistent template, batch Dymola's batched-mos override).
    *suffix_phrase* differentiates modes — empty for the default loop,
    ``"via persistent <Backend> workers"`` for persistent runners, or
    ``"in M batch(es) of up to K"`` for the legacy Dymola batch path.

    *timeout_per_test* toggles the units in the timeout label: the default
    batch loop applies one timeout per test, while batch Dymola and the
    persistent template apply it per worker-test, so both prefer the
    ``s/test`` rendering.
    """
    timeout_label = (
        f"timeout={timeout_s}s/test" if timeout_per_test else f"timeout={timeout_s}s"
    )
    parts = [f"Running {total} tests"]
    if suffix_phrase:
        parts.append(f" {suffix_phrase}")
    parts.append(f" (parallel={parallel}, {timeout_label})")
    print("".join(parts), file=sys.stderr)
    if metadata:
        # Provenance line so the terminal (like the dashboard) says which
        # backend/version produced these results — the tool_version may still
        # be None here for persistent backends and is filled in on the report.
        label = metadata.get("simulator") or metadata.get("backend") or "unknown"
        bits = [label]
        if metadata.get("os"):
            bits.append(metadata["os"])
        if metadata.get("dstf_version"):
            bits.append(f"DSTF {metadata['dstf_version']}")
        print(f"Backend: {' · '.join(bits)}", file=sys.stderr)
    if work_dir is not None:
        dashboard = work_dir / "dashboard.html"
        print(f"Live progress: {dashboard.resolve().as_uri()}", file=sys.stderr)


def _print_progress(
    index: int,
    total: int,
    name: str,
    status: str,
    elapsed: float | None = None,
    detail: str | None = None,
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
# Abstract persistent-worker interface
# ---------------------------------------------------------------------------


class Worker(ABC):
    """One long-lived simulator subprocess wrapped as a test-running unit.

    Each persistent-worker backend (Dymola / OpenModelica / Julia today)
    has its own concrete subclass that owns a real OS process — a Dymola
    instance, an ``OMCSessionZMQ``, a ``julia --project=...`` subprocess.
    The shape declared here is what :class:`PersistentRunnerBase` (Phase 4)
    will rely on to orchestrate them generically.

    Subclasses extend ``__init__`` with backend-specific config (e.g.
    ``DymolaConfig``, an interface class to instantiate). They must always
    call ``super().__init__(worker_id, config)`` so the framework's
    invariants (worker_id stable across restarts; ``self.config`` available
    to dispatch logic) hold.

    Lifecycle contract:
        ``__init__`` → ``start()`` → ``run_test_with_timeout()`` × N
            → ``close()``
    ``close()`` MUST be idempotent — :class:`PersistentRunnerBase`'s teardown
    calls it on every worker including ones that never started successfully.

    On failure, ``start()`` raises (typically :class:`RuntimeError`); the
    runner records the failure and excludes that worker from the live pool.
    Inside the dispatch loop, ``run_test_with_timeout`` MUST NOT raise on
    test-level failures — return a ``TestRunResult`` with ``success=False``
    instead. Reserve raises for worker-level catastrophes (subprocess died,
    pipe broken) so the caller's restart logic can intervene.
    """

    def __init__(self, worker_id: int, config: Config):
        self.worker_id = worker_id
        self.config = config

    @abstractmethod
    def start(self) -> None:
        """Bring the worker process up and apply startup (library loads,
        framework settings). Raise on failure.
        """

    @abstractmethod
    def close(self, grace: float = 5.0) -> None:
        """Tear the worker down. Try graceful first; hard-kill if the
        graceful path doesn't return within *grace* seconds. Idempotent —
        safe to call on a worker that never started or already closed.
        """

    @abstractmethod
    def is_alive(self) -> bool:
        """True iff the worker can accept another test. Backends without
        watchable subprocess state can implement this as ``self._handle is
        not None``.
        """

    @abstractmethod
    def run_test_with_timeout(
        self,
        test: TestModel,
        test_key: str,
        timeout: float,
        progress: Optional["object"] = None,
    ) -> "TestRunResult":
        """Run *test* with a watchdog. On timeout, hard-kill the worker
        process and return ``TestRunResult(timed_out=True, success=False)``.
        Test-level failures (sim error, missing variable) return a
        ``success=False`` result, not a raise. Worker-level catastrophes
        (subprocess died, pipe broken) MAY raise so the caller restarts.

        *progress* is the optional :class:`ProgressReporter` so the worker
        can emit ``on_phase`` updates (translation vs simulation) for the
        live dashboard. Backends that don't have meaningful sub-phases
        accept and ignore the parameter.
        """

    def export_fmu(self, test: TestModel, output_dir: "Path") -> "Path":
        """Export *test*'s model as an FMU into *output_dir*.

        Backends that declare :attr:`Capability.FMU_EXPORT` on their
        :class:`SimulatorRunner` MUST override this; the default raises
        :class:`NotImplementedError`. Used by 4.B's cross-backend chain
        (e.g. Dymola export → FMPy simulate as a second baseline).
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support FMU export. "
            f"Backend must declare Capability.FMU_EXPORT and override export_fmu()."
        )


# ---------------------------------------------------------------------------
# Abstract simulator interface
# ---------------------------------------------------------------------------


class SimulatorRunner(ABC):
    """Abstract interface for running simulations across backends.

    Concrete backends today: Dymola, FMPy. The abstraction is dataset-shape
    agnostic — a backend declares what it produces (``produced_datasets``)
    and what it can do (``capabilities``).

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
    #: Per-test files the backend produces that should appear in the report's
    #: "Simulation Artifacts" section as ``(filename, label)`` pairs.
    #: Backend-agnostic reporter walks this list and skips files that don't
    #: exist — lets each runner own its artifact names without the reporter
    #: hardcoding anyone's on-disk conventions.
    artifact_files: tuple[tuple[str, str], ...] = ()

    def __init__(self, config: Config):
        self.config = config
        self.progress = None  # set to a ProgressReporter during run_tests()
        # Optional model_id → "ref_NNNN" map for dashboard report links
        # (set by CLI before run_tests if reference IDs are known)
        self.ref_id_map: dict[str, str | None] = {}

    @classmethod
    def persistent_runner_cls(cls) -> type["SimulatorRunner"] | None:
        """Return the persistent-worker variant of this runner, or ``None``
        if the backend is batch-only.

        Override on backends that ship a persistent runner; the override
        lazy-imports its sibling persistent module so the optional dep
        (DymolaInterface, OMPython, ``julia`` binary) is not pulled in
        during a plain batch run. The CLI calls this — keeping the lookup
        on the runner class rather than in CLI code means a new persistent
        backend is purely a runner-module change with no CLI edit.
        """
        return None

    @classmethod
    def preflight(cls, config: Config) -> None:
        """Cheap pre-startup dependency probe.

        Override on persistent runners that have an external dependency
        (Python module, native binary, license file) to raise
        :class:`RuntimeError` with an install hint when it's missing.
        Cheap probes only — full failure modes (worker compile errors,
        license issues) still surface from ``run_tests``. Default is a
        no-op for runners with no extra dependencies beyond what the
        package declares.
        """
        return None

    def describe_tool_version(self) -> str | None:
        """Best-effort actual tool version (e.g. ``"Dymola 2026x"``, an omc
        version, a Python version), or ``None`` when the backend can't cheaply
        report one without a live session.

        Called eagerly by ``run_tests`` to stamp run provenance. Persistent
        backends whose version only exists once a worker is up should leave
        this ``None`` and instead override :meth:`_probe_worker_version`, which
        the persistent template calls after startup. MUST NOT raise — a failed
        version probe degrades to the configured simulator label, never an
        aborted run.
        """
        return None

    def _safe_tool_version(self) -> str | None:
        """``describe_tool_version()`` with a blanket guard — provenance is
        never allowed to abort a run, so any probe failure degrades to
        ``None`` (the reporter then falls back to the configured label)."""
        try:
            return self.describe_tool_version()
        except Exception:
            logger.debug("tool-version probe failed", exc_info=True)
            return None

    def _build_run_metadata(
        self,
        tool_version: str | None = None,
        library_versions: dict | None = None,
    ) -> dict:
        """Assemble the run-provenance dict handed to ``ProgressReporter``.

        Kept as a helper (not inlined) so both the batch and persistent
        ``run_tests`` templates build identical provenance from ``self.config``.
        """
        from .run_metadata import RunMetadata

        return RunMetadata.from_config(
            self.config,
            tool_version=tool_version,
            library_versions=library_versions,
        ).as_dict()

    def export_fmu(self, test: TestModel, output_dir: "Path") -> "Path":
        """Export the test's model as an FMU into ``output_dir``.

        Backends must declare ``Capability.FMU_EXPORT`` in their
        ``capabilities`` to call this; the default raises
        :class:`NotImplementedError`. Used by 4.B's cross-backend chain
        (e.g., Dymola export → FMPy simulate as a second baseline).

        Returns the absolute path to the produced ``.fmu`` file.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support FMU export. "
            f"Backend must declare Capability.FMU_EXPORT and override export_fmu()."
        )

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

        from ..reporting.dashboard_render import build_rerun_prefix
        from .progress import ProgressReporter

        # Batch/subprocess backends can report their version cheaply and
        # eagerly (no live session to wait on); persistent backends leave this
        # None and patch it in once a worker is up (see below).
        run_metadata = self._build_run_metadata(self._safe_tool_version())
        self.progress = ProgressReporter(
            self.config.work_dir,
            total,
            rerun_prefix=build_rerun_prefix(self.config),
            metadata=run_metadata,
        )

        # Assign test_keys (reuse existing from prior runs if present — supports
        # incremental workflows where the manifest accumulates known tests).
        manifest_map, key_pairs = assign_test_keys(self.config.work_dir, tests)
        test_items = [
            (test, test_key, i + 1) for i, (test, test_key) in enumerate(key_pairs)
        ]
        for test, test_key, _ in test_items:
            report_dir = self.ref_id_map.get(test.model_id) or test_key
            self.progress.register(
                test_key,
                test.model_id,
                report_dir=report_dir,
                field_sources=test.field_sources,
            )

        manifest = BatchManifest(
            batch_id=0,
            work_dir=self.config.work_dir,
            manifest=manifest_map,
        )
        manifest.save()

        _print_run_header(
            total,
            "",
            self.config.parallel,
            self.config.timeout,
            self.config.work_dir,
            metadata=run_metadata,
        )

        def _result_status(rr: TestRunResult) -> str:
            if rr.timed_out:
                return "timeout"
            return "ok" if rr.success else "fail"

        run_results: list[TestRunResult] = []
        wall_start = time.monotonic()
        completed = 0
        key_lookup = {t.model_id: tk for t, tk, _ in test_items}

        if self.config.parallel <= 1:
            for test, test_key, idx in test_items:
                result = self.run_single_test(test, test_key, idx, total)
                run_results.append(result)
                completed += 1
                _print_progress(
                    completed,
                    total,
                    f"{test_key} {test.model_id}",
                    _result_status(result),
                    elapsed=result.elapsed,
                )
        else:
            futures = {}
            with ThreadPoolExecutor(max_workers=self.config.parallel) as pool:
                for test, test_key, idx in test_items:
                    future = pool.submit(
                        self.run_single_test, test, test_key, idx, total
                    )
                    futures[future] = test.model_id

                for future in as_completed(futures):
                    result = future.result()
                    run_results.append(result)
                    completed += 1
                    model_id = futures[future]
                    test_key = key_lookup.get(model_id, "")
                    _print_progress(
                        completed,
                        total,
                        f"{test_key} {model_id}",
                        _result_status(result),
                        elapsed=result.elapsed,
                    )

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


# ---------------------------------------------------------------------------
# Persistent-worker template
# ---------------------------------------------------------------------------


class PersistentRunnerBase(SimulatorRunner):
    """Template-method class for persistent-worker runners.

    Owns the orchestration shared by Dymola / OpenModelica / Julia persistent
    runners — so adding a new persistent backend is a one-class job, not a
    250-line copy-paste of the dispatch loop.

    Subclass contract:

      - Set ``worker_cls`` to your :class:`Worker` subclass.
      - Set ``backend_label`` (e.g. ``"Dymola"``) — used in headers and
        thread names. Read by both the run-header line and the final
        completion message.
      - Override :meth:`make_worker` if your worker constructor needs
        backend-specific args beyond ``(worker_id, config)``. Use
        :meth:`setup_before_workers` to populate any class state your
        ``make_worker`` reads.
      - Override :meth:`setup_before_workers` if your backend has runtime
        patches that must apply before any worker spawns (Dymola: log
        filter + parallel-startup lock).
      - Override :meth:`preflight` (already declared on ``SimulatorRunner``)
        to add a cheap dependency probe — see :meth:`_get_runner` in the CLI.

    The :meth:`run_tests` template enforces the lifecycle:

        setup_before_workers → ProgressReporter → assign_test_keys
            → BatchManifest → make_worker × N → start in parallel
            → filter live workers (raise if zero) → dispatch with restart
            → close all workers → finalize progress

    Subclasses generally don't need to touch :meth:`run_tests` itself.
    """

    #: Worker subclass to instantiate per slot. MUST be set by subclasses.
    worker_cls: type["Worker"] = None  # type: ignore[assignment]

    #: Human-readable backend name for headers and thread names.
    backend_label: str = ""

    #: Per-worker restart budget. After a timeout or worker-level exception
    #: the dispatch loop will try to ``start()`` the worker again up to this
    #: many times; on exhaustion the in-flight test gets a synthetic
    #: ``Worker dead; restart exhausted`` failure result.
    max_restarts_per_worker: int = 3

    @classmethod
    def persistent_runner_cls(cls):
        return cls  # we ARE the persistent variant; stop recursing

    def setup_before_workers(self) -> None:
        """Hook for runtime patches that must take effect before any worker
        spawns. Default no-op. Dymola uses this to install its log filter
        and narrow the parallel-startup lock; OpenModelica uses it to
        cache its session class; Julia today needs nothing here.
        """
        return None

    def make_worker(self, worker_id: int) -> "Worker":
        """Construct one :class:`Worker`. Default calls
        ``self.worker_cls(worker_id, self.config)``. Backends with extra
        construction args (e.g. ``DymolaConfig`` + interface class) override
        this — typically reading state stashed by
        :meth:`setup_before_workers`.
        """
        if self.worker_cls is None:
            raise NotImplementedError(
                f"{type(self).__name__} must set the `worker_cls` class attr "
                f"or override `make_worker`."
            )
        return self.worker_cls(worker_id, self.config)

    def starting_workers_message(self, n_workers: int) -> str:
        """The "Starting N <backend> worker(s)..." line printed before
        worker startup. Override to add backend-specific guidance — e.g.
        Julia's warmup notice that the first run does several minutes of
        JIT compilation. Default uses :attr:`backend_label`.
        """
        return f"Starting {n_workers} {self.backend_label} worker(s)..."

    def _probe_worker_version(self, live_workers: list["Worker"]) -> str | None:
        """Best-effort actual tool version, asked of a live worker.

        The persistent template calls this once workers are up (before
        dispatch) to stamp run provenance. Default returns ``None``; backends
        whose session can name its version (Dymola's ``DymolaVersion()``,
        omc's ``getVersion()``) override this. MUST NOT raise — the template
        wraps it in :meth:`_safe_probe_worker_version`.
        """
        return None

    def _safe_probe_worker_version(self, live_workers: list["Worker"]) -> str | None:
        """Guarded :meth:`_probe_worker_version` — a probe failure degrades to
        ``None`` and never disrupts the run."""
        if not live_workers:
            return None
        try:
            return self._probe_worker_version(live_workers)
        except Exception:
            logger.debug("worker version probe failed", exc_info=True)
            return None

    def _probe_library_versions(self, live_workers: list["Worker"]) -> dict | None:
        """Best-effort loaded-library versions (e.g. ``{"Modelica": "4.1.0"}``)
        asked of a live worker. Default ``None``; Modelica backends override
        (Dymola reads the bundled MSL under ``$DYMOLA``; omc uses
        ``getVersion(Modelica)``). The MSL version is the second drift suspect
        after the tool version itself. MUST NOT raise — wrapped by
        :meth:`_safe_probe_library_versions`."""
        return None

    def _safe_probe_library_versions(self, live_workers: list["Worker"]) -> dict | None:
        """Guarded :meth:`_probe_library_versions`."""
        if not live_workers:
            return None
        try:
            return self._probe_library_versions(live_workers)
        except Exception:
            logger.debug("worker library-version probe failed", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # The template
    # ------------------------------------------------------------------

    def run_tests(self, tests: list[TestModel]) -> list[BatchManifest]:
        if not tests:
            return []

        self.setup_before_workers()
        self.config.work_dir.mkdir(parents=True, exist_ok=True)
        total = len(tests)

        from ..reporting.dashboard_render import build_rerun_prefix
        from .progress import ProgressReporter

        # Persistent backends usually can't name their version until a worker
        # is live, so tool_version starts None and is patched in after startup
        # via _probe_worker_version (see below).
        run_metadata = self._build_run_metadata(self._safe_tool_version())
        self.progress = ProgressReporter(
            self.config.work_dir,
            total,
            rerun_prefix=build_rerun_prefix(self.config),
            metadata=run_metadata,
        )

        manifest_map, test_items = assign_test_keys(self.config.work_dir, tests)
        for test, test_key in test_items:
            report_dir = self.ref_id_map.get(test.model_id) or test_key
            self.progress.register(
                test_key,
                test.model_id,
                report_dir=report_dir,
                field_sources=test.field_sources,
            )

        manifest = BatchManifest(
            batch_id=0,
            work_dir=self.config.work_dir,
            manifest=manifest_map,
        )
        manifest.save()

        n_workers = max(1, self.config.parallel)
        _print_run_header(
            total,
            f"via persistent {self.backend_label} workers",
            n_workers,
            self.config.timeout,
            self.config.work_dir,
            timeout_per_test=True,
            metadata=run_metadata,
        )

        workers = [self.make_worker(i) for i in range(n_workers)]

        start_all = time.monotonic()
        print(self.starting_workers_message(n_workers), file=sys.stderr)

        live_workers = self._start_workers_parallel(workers)
        if not live_workers:
            # review 2026-07-06 (finding 24): close every worker before the
            # raise — start()-failed workers already close themselves in the
            # startup handler, but close() is idempotent and this guarantees
            # no orphaned simulator process (license seat) survives the abort.
            for w in workers:
                try:
                    w.close()
                except Exception:
                    logger.debug(
                        "Worker %s: close() during startup-failure cleanup raised",
                        w.worker_id,
                        exc_info=True,
                    )
            if self.progress is not None:
                self.progress.finalize()
            raise RuntimeError(
                f"All {self.backend_label} persistent workers failed to start. "
                f"See per-worker errors above. Try re-running with --batch."
            )
        print(
            f"  {len(live_workers)}/{n_workers} workers ready in "
            f"{time.monotonic() - start_all:.1f}s",
            file=sys.stderr,
        )

        # Now that a worker is live, ask it for the real tool version and the
        # loaded library versions (e.g. MSL) and stamp them into the run
        # provenance. Best-effort — a failed probe leaves the configured
        # simulator label as the shown version and omits the library line.
        updates: dict = {}
        version = self._safe_probe_worker_version(live_workers)
        if version:
            updates["tool_version"] = version
        libs = self._safe_probe_library_versions(live_workers)
        if libs:
            updates["library_versions"] = libs
        if updates and self.progress is not None:
            self.progress.update_metadata(**updates)

        results = self._dispatch_with_restart(live_workers, test_items, total)

        for w in workers:
            w.close()

        wall = time.monotonic() - start_all
        self._print_run_summary(results, total, wall)

        manifest.results = results
        if self.progress is not None:
            self.progress.finalize()
        return [manifest]

    # ------------------------------------------------------------------
    # Internal building blocks
    # ------------------------------------------------------------------

    def _start_workers_parallel(self, workers: list["Worker"]) -> list["Worker"]:
        """Spawn every worker concurrently. Returns the subset that started
        successfully; failures are logged per-worker. Caller decides what
        to do on zero-live-workers (the template raises ``RuntimeError``).
        """
        worker_ready: dict[int, bool] = {}
        ready_lock = threading.Lock()

        def _start_one(w: "Worker"):
            t0 = time.monotonic()
            try:
                w.start()
                dt = time.monotonic() - t0
                with ready_lock:
                    worker_ready[w.worker_id] = True
                print(f"  Worker {w.worker_id}: ready ({dt:.1f}s)", file=sys.stderr)
            except Exception as exc:
                with ready_lock:
                    worker_ready[w.worker_id] = False
                print(
                    f"  Worker {w.worker_id}: start FAILED — {exc}",
                    file=sys.stderr,
                )
                # review 2026-07-06 (finding 24): a worker that failed mid-
                # start may already have spawned its subprocess; close it so
                # the process (and its license seat) doesn't leak. close() is
                # contractually idempotent + safe on never-started workers.
                try:
                    w.close()
                except Exception:
                    logger.debug(
                        "Worker %s: close() after failed start raised",
                        w.worker_id,
                        exc_info=True,
                    )

        with ThreadPoolExecutor(max_workers=len(workers)) as pool:
            futures = [pool.submit(_start_one, w) for w in workers]
            for f in as_completed(futures):
                f.result()  # propagate unexpected errors; start errors already printed

        return [w for w in workers if worker_ready.get(w.worker_id)]

    def _dispatch_with_restart(
        self,
        live_workers: list["Worker"],
        test_items: list,
        total: int,
    ) -> list[TestRunResult]:
        """Shared queue + sentinel + per-worker restart dispatch.

        Each test goes once into ``work_queue``; each live worker gets one
        sentinel ``None`` to signal "drain done." Workers that die mid-run
        get up to :attr:`max_restarts_per_worker` restart attempts; on
        exhaustion the in-flight test gets a synthetic failure result.
        """
        work_queue: queue.Queue[tuple | None] = queue.Queue()
        for item in test_items:
            work_queue.put(item)
        for _ in live_workers:
            work_queue.put(None)

        results: list[TestRunResult] = []
        results_lock = threading.Lock()
        completed = [0]

        def _record(test, test_key, tr: TestRunResult) -> None:
            with results_lock:
                results.append(tr)
                completed[0] += 1
                idx = completed[0]
            label = f"{test_key} {test.model_id.rsplit('.', 1)[-1]}"
            status = "ok" if tr.success else ("TIMEOUT" if tr.timed_out else "FAIL")
            parts = []
            if getattr(tr, "translation_wall", None) is not None:
                parts.append(f"xlate {tr.translation_wall:.1f}s")
            if getattr(tr, "sim_wall", None) is not None:
                parts.append(f"sim {tr.sim_wall:.1f}s")
            if tr.success and parts:
                detail_str: str | None = ", ".join(parts)
            elif not tr.success:
                detail_str = (tr.error_message or "")[:80]
            else:
                detail_str = None
            _print_progress(
                idx,
                total,
                label,
                status,
                elapsed=tr.elapsed,
                detail=detail_str,
            )
            if self.progress is not None:
                self.progress.on_finish(
                    test_key,
                    success=tr.success,
                    elapsed=tr.elapsed,
                    detail=None if tr.success else (tr.error_message or "")[:120],
                    timed_out=tr.timed_out,
                )

        def _try_restart(w: "Worker", count_against_budget: bool = True) -> bool:
            """Bring the worker back up after a death.

            ``count_against_budget`` distinguishes two scenarios:

            * **True** (default): worker died because something is genuinely
              wrong with it (translation returned False with no diagnostic
              log; RPC error; start() failure on a previous iteration). The
              counter increments; after ``max_restarts_per_worker`` such
              consecutive failures, we declare the worker permanently dead.
            * **False**: worker died because the dispatch loop hard-killed
              it after a per-test *timeout*. The worker was healthy when
              the test started — it actively translated/simulated for the
              full timeout budget. Restart is just to bring up a fresh
              process; the count of "consecutive worker deaths" should not
              tick up. This avoids a known cascade where 3 slow tests in
              a row exhaust the budget and synthesize ~290 fake failures
              for the rest of the queue.
            """
            n_restarts = getattr(w, "_n_restarts", 0)
            if count_against_budget and n_restarts >= self.max_restarts_per_worker:
                return False
            if count_against_budget:
                n_restarts += 1
                w._n_restarts = n_restarts  # type: ignore[attr-defined]
            t0 = time.monotonic()
            try:
                w.start()
                if count_against_budget:
                    print(
                        f"  Worker {w.worker_id}: restarted "
                        f"({time.monotonic() - t0:.1f}s, attempt {n_restarts}/"
                        f"{self.max_restarts_per_worker})",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"  Worker {w.worker_id}: restarted post-timeout "
                        f"({time.monotonic() - t0:.1f}s — not counted)",
                        file=sys.stderr,
                    )
                return True
            except Exception as exc:
                print(
                    f"  Worker {w.worker_id}: restart FAILED — {exc}",
                    file=sys.stderr,
                )
                return False

        # review 2026-07-06 (finding 20): dispatch-thread liveness registry.
        # A worker whose restart failed permanently must NOT stay in the
        # dispatch loop — it would tight-loop on the shared queue, instantly
        # fail-marking tests that healthy workers could have run. Its thread
        # exits instead — unless it is the LAST live thread, in which case it
        # drains the queue and fail-marks the remainder so every test gets a
        # recorded result and nothing hangs. (There is no work_queue.join()
        # anywhere — the thread joins below are the only sync point — so an
        # early thread exit cannot deadlock; leftover sentinels are inert.)
        live_threads = [len(live_workers)]
        live_threads_lock = threading.Lock()

        def _fail_dead(test, test_key: str, message: str) -> None:
            _record(
                test,
                test_key,
                TestRunResult(
                    model_id=test.model_id,
                    test_key=test_key,
                    success=False,
                    elapsed=0.0,
                    error_message=message,
                ),
            )

        def _drain_remaining(reason: str) -> None:
            while True:
                try:
                    item = work_queue.get_nowait()
                except queue.Empty:
                    return
                if item is not None:
                    test, test_key = item
                    _fail_dead(test, test_key, reason)
                work_queue.task_done()

        def _worker_loop(w: "Worker"):
            # Track whether the previous test left the worker dead for a
            # benign reason (we deliberately hard-killed it after a per-
            # test timeout, or it was disk-rescued post-timeout). Such
            # restarts shouldn't count against the worker's permanent-
            # broken budget — the worker was healthy, the test was slow.
            prev_killed_worker = False
            try:
                while True:
                    item = work_queue.get()
                    if item is None:
                        work_queue.task_done()
                        return
                    test, test_key = item
                    restart_exhausted = False
                    try:
                        if not w.is_alive():
                            if not _try_restart(
                                w, count_against_budget=not prev_killed_worker
                            ):
                                # review 2026-07-06 (finding 20): fail-mark
                                # ONLY the item this thread already holds;
                                # exit the loop right after the finally so
                                # remaining tests go to live workers.
                                _fail_dead(
                                    test,
                                    test_key,
                                    "Worker dead; restart exhausted",
                                )
                                restart_exhausted = True
                            else:
                                prev_killed_worker = False  # consumed by restart
                        if not restart_exhausted:
                            if self.progress is not None:
                                self.progress.on_start(test_key, worker_id=w.worker_id)
                            timeout = float(
                                test.timeout
                                if test.timeout is not None
                                else self.config.timeout
                            )
                            try:
                                tr = w.run_test_with_timeout(
                                    test,
                                    test_key,
                                    timeout,
                                    progress=self.progress,
                                )
                            except Exception as exc:
                                # review 2026-07-06 (finding 21): the Worker
                                # ABC contract permits raises for worker-level
                                # catastrophes. A raise must be recorded — not
                                # kill the dispatch thread and silently drop
                                # the in-flight test (plus, at --parallel 1,
                                # the whole remaining queue). The next
                                # iteration's is_alive()/restart logic handles
                                # the possibly-dead worker.
                                logger.exception(
                                    "Worker %s raised while running %s",
                                    w.worker_id,
                                    test.model_id,
                                )
                                _record(
                                    test,
                                    test_key,
                                    TestRunResult(
                                        model_id=test.model_id,
                                        test_key=test_key,
                                        success=False,
                                        elapsed=0.0,
                                        error_message=f"worker exception: {exc!r}",
                                    ),
                                )
                                prev_killed_worker = False
                            else:
                                _record(test, test_key, tr)
                                # Reset the restart counter on a successful
                                # test — the worker has demonstrably recovered.
                                # Consecutive *real* worker deaths (translation
                                # broke / RPC dead) still accumulate to
                                # ``max_restarts_per_worker`` and trip restart-
                                # exhausted. Benign worker kills (timeout,
                                # post-timeout disk-rescue) flow through
                                # prev_killed_worker so they don't poison the
                                # counter.
                                if tr.success and not tr.worker_killed:
                                    w._n_restarts = 0  # type: ignore[attr-defined]
                                prev_killed_worker = bool(
                                    tr.timed_out or tr.worker_killed
                                )
                    finally:
                        work_queue.task_done()
                    if restart_exhausted:
                        return
            finally:
                # review 2026-07-06 (finding 20): last thread out drains the
                # queue so no test is left unrecorded and no consumer hangs.
                with live_threads_lock:
                    live_threads[0] -= 1
                    is_last = live_threads[0] == 0
                if is_last:
                    _drain_remaining(
                        "Worker dead; restart exhausted (no live workers remaining)"
                    )

        thread_prefix = self.backend_label.lower().replace(" ", "-") or "pworker"
        threads = [
            threading.Thread(
                target=_worker_loop,
                args=(w,),
                name=f"{thread_prefix}-pworker-{w.worker_id}",
            )
            for w in live_workers
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results

    def _print_run_summary(
        self,
        results: list[TestRunResult],
        total: int,
        wall: float,
    ) -> None:
        total_work = sum(r.elapsed for r in results)
        speedup = (total_work / wall) if wall > 0 else 0.0
        n_ok = sum(1 for r in results if r.success)
        n_timeout = sum(1 for r in results if r.timed_out)
        n_fail = total - n_ok - n_timeout
        print(file=sys.stderr)
        print(
            f"Persistent {self.backend_label} run complete: "
            f"{n_ok} ok, {n_fail} failed, {n_timeout} timed out "
            f"({wall:.0f}s wall, {total_work:.0f}s total work, "
            f"{speedup:.1f}x parallel speedup)",
            file=sys.stderr,
        )
