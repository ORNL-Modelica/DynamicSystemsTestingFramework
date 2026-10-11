"""Abstract backend interface and the shared run-loop scaffolding.

:class:`Backend` is the backend contract plus the default per-test
batch loop; :meth:`Backend._prepare_run` is the one prologue every
``run_tests`` implementation goes through. The persistent-worker lane
(:class:`~dstf.backends.persistent.Worker` +
:class:`~dstf.backends.persistent.PersistentBackendBase`) builds on this
module; the value types it passes around live in
:mod:`dstf.backends.types`.
"""

import logging
import sys
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Collection
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING

import numpy as np

from ..config import Config
from ..discovery.test_registry import TestModel
from ..storage.batch_manifest import (
    CANCELLED_RUN_STATUS,
    PENDING_RUN_STATUS,
    RECORDED_RUN_STATUSES,
    BatchManifest,
)
from ..storage.run_stats import read_run_stats
from .common.result_variables import merge_timing_stats, run_timing
from .types import Capability, DatasetType, TestResult, TestRunResult

if TYPE_CHECKING:
    from .progress import ProgressReporter

logger = logging.getLogger(__name__)


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


def last_test_keys(work_dir: Path) -> dict[str, str]:
    """``{model_id: test_key}`` from the work dir's manifest; ``{}`` when
    there is none."""
    manifest_path = work_dir / "batch_manifest.json"
    if not manifest_path.exists():
        return {}
    manifest = BatchManifest.load(manifest_path).manifest
    return {entry["model_id"]: key for key, entry in manifest.items()}


# ---------------------------------------------------------------------------
# Progress output (shared across backends)
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
    quiet: bool = False,
) -> None:
    """Print the standard "Running N tests..." header + dashboard URL.

    Single source of truth for the header style across all backends (default
    batch loop, persistent template, batch Dymola's batched-mos override).
    *suffix_phrase* differentiates modes — empty for the default loop,
    ``"via persistent <Backend> workers"`` for persistent backends, or
    ``"in M batch(es) of up to K"`` for the legacy Dymola batch path.

    *timeout_per_test* toggles the units in the timeout label: the default
    batch loop applies one timeout per test, while batch Dymola and the
    persistent template apply it per worker-test, so both prefer the
    ``s/test`` rendering.

    *quiet* (``Backend.quiet``) drops the whole header: a run driven from a
    UI process narrates a console nobody is reading.
    """
    if quiet:
        return
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
        label = metadata.get("backend_name") or "unknown"
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
    *,
    quiet: bool = False,
):
    """Thread-safe progress output; *quiet* (``Backend.quiet``) prints nothing."""
    if quiet:
        return
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


def no_worker_message(label: str, failures: dict[int, str]) -> str:
    """The sentence every waiting test fails with when no worker of a
    backend could start: each worker's reason in the backend's own words,
    e.g. ``No OpenModelica worker could start — worker 0: omc exited (code
    -9) during loadModel(Modelica)``. A reason that already names its worker
    (``Worker 0: failed to load ...``) is not named twice."""
    parts = []
    for worker_id, reason in sorted(failures.items()):
        prefix = f"worker {worker_id}: "
        if reason.lower().startswith(prefix):
            reason = reason[len(prefix) :]
        parts.append(prefix + reason)
    return f"No {label} worker could start — " + "; ".join(parts)


#: Run outcomes that make a result on disk untrustworthy: the ``.mat`` (if
#: any) is partial or left over from an earlier, better run.
FAILED_RUN_STATUSES = ("failed", "timed_out")


def _force_failed(result: "TestResult | None", status: str) -> None:
    """Mark a result read back from disk as failed because its run says so
    — and say that the run says so before the reader's own sentence, where
    the reader already failed it over what it found: a run killed by its
    timeout leaves a partial file or none, and "Partial result: …" or "No
    Python result at …" alone does not say why. Whether or not the file
    held a row. A failure in the run's own words (``TestResult.run_reason``:
    the driver wrote why into its result file) is left as it is — it reads
    as the run said it."""
    if result is None:
        return
    recorded = f"Run recorded {status}"
    if result.success:
        result.success = False
        result.error_message = result.error_message or recorded
    elif not result.error_message:
        result.error_message = recorded
    elif not result.run_reason:
        result.error_message = f"{recorded} — {result.error_message}"


def _with_run_timing(stats: dict | None, rr: TestRunResult) -> dict | None:
    """*stats* with the run's wall-clock block (:func:`run_timing`) merged
    into its ``timing``, or *stats* as it was when the run measured no wall.

    Built on a copy: ``merge_timing_stats`` updates ``stats["timing"]`` in
    place, and a backend's ``stats`` may share that dict with the run
    result's own statistics (a shallow ``dict(run_result.statistics)``).
    """
    timing = run_timing(rr)
    if not timing:
        return stats
    out = dict(stats or {})
    out["timing"] = dict(out.get("timing") or {})
    return merge_timing_stats(out, timing)


def failed_run_result(rr: TestRunResult) -> TestResult:
    """The result of a run that failed: its message and its statistics, with
    nothing read from disk — whatever file it left is partial."""
    return TestResult(
        model_id=rr.model_id,
        success=False,
        error_message=rr.error_message or "Simulation failed",
        statistics=_with_run_timing(rr.statistics, rr),
        run_reason=True,
    )


def kept_run_result(
    test_dir: Path, model_id: str, test_key: str
) -> TestRunResult | None:
    """The run result a past run kept in *test_dir* (``run_stats.json``,
    :mod:`dstf.storage.run_stats`), rebuilt so ``read_result`` and
    :func:`run_timing` read it as they read an in-process one; ``None`` when
    none was kept.

    ``success=True`` whatever that run's verdict: the verdict is the
    manifest's (:func:`run_verdicts`), applied after the read, exactly as for
    a reload without the file.
    """
    kept = read_run_stats(test_dir, model_id)
    if kept is None:
        return None
    return TestRunResult(
        model_id=model_id,
        test_key=test_key,
        success=True,
        elapsed=kept["elapsed"],
        statistics=kept["statistics"],
        translation_wall=kept["translation_wall"],
        sim_wall=kept["sim_wall"],
    )


def run_verdicts(work_dir: Path, manifests: list[BatchManifest]) -> dict[str, str]:
    """``model_id -> failed run status`` for every test whose last run the
    manifests record as failed or timed out.

    The on-disk result read can be fooled by a partial or stale ``.mat`` into
    reporting success, which would let ``compare`` show a green test whose run
    actually failed and let ``compare --accept`` bless a garbage baseline. The
    run's verdict is authoritative, and it is persisted per test in the batch
    manifest (``last_run_status``, written by
    :meth:`BatchManifest.record_result` as each result lands).

    An entry is authoritative ONLY when it carries one of
    :data:`RECORDED_RUN_STATUSES`. Two cases fall through to the ``status.json``
    cross-check instead:

    * no field at all — a manifest written before this record existed (old
      data, not old code: the writer always writes it now);
    * :data:`~dstf.storage.batch_manifest.PENDING_RUN_STATUS` — a run started this test and never reached a
      verdict for it, i.e. it was aborted. Trusting a pending entry would mean
      trusting nothing at all; trusting a *carried-forward* verdict, which is
      what pending replaced, would mean reporting a previous run's green over
      this run's partial ``.mat``.

    A work dir with neither record (status.json absent or unreadable) yields
    no verdicts — the disk read stands.
    """
    from .progress import read_status

    verdicts: dict[str, str] = {}
    covered: set[str] = set()
    for manifest in manifests:
        for entry in manifest.manifest.values():
            status = entry.get("last_run_status")
            if status not in RECORDED_RUN_STATUSES:
                continue
            covered.add(entry["model_id"])
            if status in FAILED_RUN_STATUSES:
                verdicts[entry["model_id"]] = status
    snap = read_status(work_dir)
    for row in (snap or {}).get("tests", []):
        model_id = row.get("model_id")
        if model_id in covered:
            continue
        if row.get("status") in FAILED_RUN_STATUSES:
            verdicts[model_id] = row["status"]
    return verdicts


# ---------------------------------------------------------------------------
# Abstract backend interface
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunSetup:
    """Everything :meth:`Backend._prepare_run` produces for a run.

    One prologue for all three ``run_tests`` implementations (default batch
    loop, persistent template, batch Dymola). The copies drifted before: the
    batch Dymola path silently lost run provenance (C-9/D96), so the shared
    ``run_metadata`` stamp lives here and nowhere else.
    """

    total: int
    run_metadata: dict
    progress: "ProgressReporter"
    #: ``(test, test_key)`` in the caller's test order.
    keyed_tests: list[tuple[TestModel, str]]
    manifest: BatchManifest


class ItemRunner:
    """What ONE thread of the simulation pool drives: bring the thing that
    runs tests up, run a test on it, stop a running one, let it go.

    The strategy seam between the pool (:mod:`dstf.backends.pool`) and how a
    backend runs a single test, obtained from :meth:`Backend.item_runner`.
    This base is the runner of a backend WITHOUT persistent workers: nothing
    to start, and a test goes through the backend's own single-test path
    (:meth:`Backend.run_single_test`) on the pool's thread. A persistent
    backend's is :class:`~dstf.backends.persistent.WorkerRunner`, over one
    :class:`~dstf.backends.persistent.Worker`.

    A subclass overrides the hooks (``start``, ``_run``, ``_stop_test``,
    ``stop``, ``close``); ``expect`` / ``run`` / ``cancel`` are the template
    that makes a cancel land whenever it arrives after ``expect``.
    """

    #: Set when this runner can take no more tests: the sentence why. The
    #: pool then lets its thread go.
    retired: str | None = None

    def __init__(self, backend: "Backend", worker_id: int):
        self.backend = backend
        self.worker_id = worker_id
        self._lock = Lock()
        #: The test this runner is on, from ``expect`` to the end of ``run``,
        #: the token of that run (``expect``), and whether a stop was asked
        #: of it.
        self._key: str | None = None
        self._token: object | None = None
        self._cancel_asked = False

    # -- hooks ---------------------------------------------------------

    def start(self) -> None:
        """Bring the runner up. Raises ``RuntimeError`` whose text is the
        reason, in the backend's own words, when it cannot."""

    def starting(self) -> tuple[str, float] | None:
        """While the thing that runs tests is being brought up — the first
        time, or back after a death: ``(phase, seconds so far)``, the phase
        in the backend's own words. ``None`` otherwise, and always here:
        this runner has nothing to start."""
        return None

    def _run(self, test: TestModel, test_key: str) -> TestRunResult:
        """Run *test* to its end on the calling thread."""
        if self._cancel_asked:
            return TestRunResult(
                model_id=test.model_id,
                test_key=test_key,
                success=False,
                error_message="Cancelled",
                cancelled=True,
            )
        return self.backend.run_single_test(test, test_key, 1, 1)

    def _stop_test(self, test_key: str) -> bool:
        """Stop *test_key* now; ``False`` while there is nothing to stop yet
        (its process does not exist) — ``cancel`` asks again."""
        return self.backend.cancel_test(test_key)

    def stop(self, test_key: str | None) -> None:
        """The pool is closing: from another thread, end whatever is in
        flight as fast as the backend can — *test_key* when a test runs
        here, a start otherwise. A backend that cannot stop a test lets it
        finish."""
        if test_key is not None and Capability.CANCEL in self.backend.capabilities:
            with self._lock:
                token = self._token if self._key == test_key else None
            self.cancel(token)

    def close(self) -> None:
        """Let go of the runner, on its own thread; idempotent."""

    # -- template ------------------------------------------------------

    def expect(self, test_key: str, token: object) -> None:
        """*test_key* is about to run here, as *token*: a ``cancel(token)``
        is heard from now on. The token is the caller's own name for this
        ONE run (the pool's is the queue item) — a test's key comes back
        with every rerun of the test, so a stop named by the key could land
        on the run after the one it was meant for. The pool calls this under
        its own lock, as it publishes the key, so no cancel can fall between
        the two."""
        with self._lock:
            self._key, self._token, self._cancel_asked = test_key, token, False

    def run(self, test: TestModel, test_key: str) -> TestRunResult:
        """Run *test* to its end on the calling thread. Never raises on a
        test-level failure; a ``cancel`` makes it return promptly. A run
        nobody announced (``expect``) has no token and cannot be
        cancelled."""
        with self._lock:
            if self._key != test_key:
                self._key, self._token, self._cancel_asked = test_key, None, False
        try:
            return self._run(test, test_key)
        finally:
            with self._lock:
                self._key = self._token = None

    def cancel(self, token: object) -> bool:
        """Stop the run announced as *token*, from another thread; ``False``
        when this runner is not on it — it has ended, or never was here.
        Only called on a backend that declares :attr:`Capability.CANCEL`.
        :meth:`run` then returns a failed or ``cancelled`` result — or the
        test's own, if it finished anyway."""
        # The test may be a few statements short of having a process to
        # stop; ask until it has one or has ended. Bounded all the same: a
        # stop that cannot be delivered must not hold the caller.
        deadline = time.monotonic() + 5.0
        asked = False
        while True:
            # Looked at and stopped under one lock: the run cannot end, and
            # the next one begin under the same key, between the two.
            with self._lock:
                if token is None or self._token is not token or self._key is None:
                    return asked
                self._cancel_asked = asked = True
                if self._stop_test(self._key):
                    return True
            if time.monotonic() >= deadline:
                return True
            time.sleep(0.01)


class Backend(ABC):
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
    #: exist — lets each backend own its artifact names without the reporter
    #: hardcoding anyone's on-disk conventions.
    artifact_files: tuple[tuple[str, str], ...] = ()
    #: Suppress every console line a run narrates (header, progress bar,
    #: summary, read phase, worker lifecycle). The CLI leaves it False; the
    #: authoring tool sets it, because its backend's console is nobody's.
    #: Declared on the class as well as set in ``__init__`` so a test double
    #: that never calls ``__init__`` still reads a value.
    quiet: bool = False

    def __init__(self, config: Config):
        self.config = config
        self.quiet: bool = False
        self.progress = None  # set to a ProgressReporter during run_tests()
        # Optional model_id → "ref_NNNN" map for dashboard report links
        # (set by CLI before run_tests if reference IDs are known)
        self.ref_id_map: dict[str, str | None] = {}

    @classmethod
    def persistent_backend_cls(cls) -> type["Backend"] | None:
        """Return the persistent-worker variant of this backend, or ``None``
        if the backend is batch-only.

        Override on backends that ship a persistent variant; the override
        lazy-imports its sibling persistent module so the optional dep
        (DymolaInterface, OMPython, ``julia`` binary) is not pulled in
        during a plain batch run. The CLI calls this — keeping the lookup
        on the backend class rather than in CLI code means a new persistent
        backend is purely a backend-module change with no CLI edit.
        """
        return None

    @classmethod
    def preflight(cls, config: Config) -> None:
        """Cheap pre-startup dependency probe.

        Override on persistent backends that have an external dependency
        (Python module, native binary, license file) to raise
        :class:`RuntimeError` with an install hint when it's missing.
        Cheap probes only — full failure modes (worker compile errors,
        license issues) still surface from ``run_tests``. Default is a
        no-op for backends with no extra dependencies beyond what the
        package declares.
        """
        return None

    def describe_tool_version(self) -> str | None:
        """Best-effort actual tool version (e.g. ``"Dymola 2026x"``, an omc
        version, a Python version), or ``None`` when the backend can't cheaply
        report one without a live session.

        Called eagerly by ``run_tests`` to stamp run provenance. A backend
        whose version is cheap to probe (a subprocess ``--version``) should
        implement it here even when it also has a persistent lane — the
        persistent classes inherit it (Julia does this). Only a version that
        NEEDS a live session belongs in :meth:`_probe_worker_version`, which
        the persistent template calls after startup. MUST NOT raise — a failed
        version probe degrades to the configured backend label, never an
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

    @property
    def label(self) -> str:
        """The backend's name in a sentence a person reads ("Dymola")."""
        return self.config.backend

    def prepare_item_runners(self) -> None:
        """Once per pool, before the first :meth:`item_runner`. Raises
        ``RuntimeError`` when this backend cannot run one test at a time."""
        if type(self).run_single_test is Backend.run_single_test:
            raise RuntimeError(
                f"{type(self).__name__} runs whole batches and has no "
                f"single-test path; the simulation queue needs its "
                f"persistent workers (backends.select_backend(config, "
                f"persistent=True))."
            )

    def item_runner(self, worker_id: int) -> ItemRunner:
        """The runner one pool thread drives (see :class:`ItemRunner`)."""
        return ItemRunner(self, worker_id)

    def cancel_test(self, test_key: str) -> bool:
        """Stop the test :meth:`run_single_test` is running under *test_key*,
        from another thread; ``False`` when none is. That call then returns
        a ``cancelled`` result. Backends whose single-test path can be
        stopped declare :attr:`Capability.CANCEL` and override this."""
        raise NotImplementedError(
            f"{type(self).__name__} declares no Capability.CANCEL: its "
            f"single-test path cannot be stopped."
        )

    def run_versions(self) -> dict:
        """``tool_version`` / ``library_versions`` for run provenance, as far
        as this backend can name them now (see :meth:`_build_run_metadata`)."""
        version = self._safe_tool_version()
        return {"tool_version": version} if version else {}

    @abstractmethod
    def read_result(
        self,
        test: TestModel,
        test_key: str,
        run_result: TestRunResult,
    ) -> TestResult:
        """Read simulation output for a completed test."""
        ...

    @classmethod
    def result_names(cls, test_dir: Path) -> list[str] | None:
        """Every variable name in the result file under ``test_dir``
        (``work_dir/<test_key>``), tracked or not; ``None`` when this backend
        keeps no file it can list. A classmethod: the report reads a test's
        files without instantiating a backend."""
        return None

    @classmethod
    def result_series(
        cls, test_dir: Path, names: Collection[str]
    ) -> dict[str, tuple[np.ndarray, np.ndarray]]:
        """``{name: (time, values)}`` for the ``names`` the file has."""
        return {}

    @classmethod
    def live_result_path(cls, test_dir: Path) -> Path | None:
        """The result file of the test run in ``test_dir``
        (``work_dir/<test_key>``) that GROWS while the run is in progress —
        the path a :class:`~.common.live_mat.LiveMatReader` is built for.
        ``None`` for a backend that writes its result once, at the end.

        Overridden by exactly the backends that declare
        ``Capability.LIVE_RESULTS``. The file need not exist yet: a
        persistent worker wipes the directory before each run."""
        return None

    def _prepare_run(self, tests: list[TestModel]) -> RunSetup:
        """Shared ``run_tests`` prologue: work dir, provenance, progress
        reporter, test keys and the saved batch manifest.

        Every ``run_tests`` implementation MUST go through here — the three
        copies of these steps drifted once already (C-9: batch Dymola lost the
        run-provenance stamp). Backend-specific staging (persistent
        ``setup_before_workers``, Dymola's per-test ``.mos`` generation) and
        the run header stay at the call sites.
        """
        self.config.work_dir.mkdir(parents=True, exist_ok=True)
        total = len(tests)

        from ..reporting.dashboard_render import build_rerun_prefix
        from .progress import ProgressReporter

        # Cheap version probes run here, eagerly (subprocess --version);
        # a version that needs a live session arrives later via
        # _probe_worker_version once a worker is up.
        run_metadata = self._build_run_metadata(self._safe_tool_version())
        progress = ProgressReporter(
            self.config.work_dir,
            total,
            rerun_prefix=build_rerun_prefix(self.config),
            metadata=run_metadata,
            theme=self.config.theme,
        )
        self.progress = progress

        manifest, keyed_tests = self.key_tests(tests)
        for test, test_key in keyed_tests:
            report_dir = self.ref_id_map.get(test.model_id) or test_key
            progress.register(
                test_key,
                test.model_id,
                report_dir=report_dir,
                field_sources=test.field_sources,
            )

        return RunSetup(
            total=total,
            run_metadata=run_metadata,
            progress=progress,
            keyed_tests=keyed_tests,
            manifest=manifest,
        )

    def key_tests(
        self, tests: list[TestModel]
    ) -> tuple[BatchManifest, list[tuple[TestModel, str]]]:
        """Key *tests* in the work dir's manifest and stamp them pending:
        the saved manifest and ``(test, test_key)`` in the caller's order.

        The half of the prologue a run of ONE test needs as much as a run of
        a list (:meth:`_prepare_run` is the other caller).
        """
        self.config.work_dir.mkdir(parents=True, exist_ok=True)
        # Assign test_keys (reuse existing from prior runs if present — supports
        # incremental workflows where the manifest accumulates known tests).
        manifest_map, keyed_tests = assign_test_keys(self.config.work_dir, tests)
        manifest = BatchManifest(
            work_dir=self.config.work_dir,
            manifest=manifest_map,
        )
        # Clear this run's scope back to "pending" before a single test
        # starts. assign_test_keys carries prior entries forward wholesale, so
        # without this a previous run's "passed" would sit on a test this run
        # fails and then aborts before recording — and `compare` would trust
        # it over the partial .mat on disk. Out-of-scope entries keep their
        # own last verdict (that is what --merge/--rerun report over).
        manifest.mark_pending({t.model_id for t in tests})
        manifest.save()
        return manifest, keyed_tests

    @property
    def run_summary_phrase(self) -> str:
        """Leading phrase of the end-of-run summary line
        (see :meth:`_print_run_summary`)."""
        return "Simulations complete"

    def _print_run_summary(
        self,
        results: list[TestRunResult],
        total: int,
        wall: float,
    ) -> None:
        """End-of-run counts + timing, identical in shape for every backend."""
        if self.quiet:
            return
        total_work = sum(r.elapsed for r in results)
        speedup = (total_work / wall) if wall > 0 else 0.0
        n_ok = sum(1 for r in results if r.success)
        n_timeout = sum(1 for r in results if r.timed_out)
        n_fail = total - n_ok - n_timeout
        print(file=sys.stderr)
        print(
            f"{self.run_summary_phrase}: "
            f"{n_ok} ok, {n_fail} failed, {n_timeout} timed out "
            f"({wall:.0f}s wall, {total_work:.0f}s total work, "
            f"{speedup:.1f}x parallel speedup)",
            file=sys.stderr,
        )

    def run_tests(self, tests: list[TestModel]) -> list[BatchManifest]:
        """Run all tests with progress, timeouts, and parallelism.

        Subclasses generally don't need to override this — override
        run_single_test and read_result instead.
        """
        if not tests:
            return []

        setup = self._prepare_run(tests)
        total = setup.total
        manifest = setup.manifest
        test_items = [
            (test, test_key, i + 1)
            for i, (test, test_key) in enumerate(setup.keyed_tests)
        ]

        _print_run_header(
            total,
            "",
            self.config.parallel,
            self.config.timeout,
            self.config.work_dir,
            metadata=setup.run_metadata,
            quiet=self.quiet,
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
                # Persist the verdict as it lands, not in an epilogue an
                # abort would skip (see BatchManifest.record_result).
                manifest.record_result(result)
                completed += 1
                _print_progress(
                    completed,
                    total,
                    f"{test_key} {test.model_id}",
                    _result_status(result),
                    elapsed=result.elapsed,
                    quiet=self.quiet,
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
                    manifest.record_result(result)
                    completed += 1
                    model_id = futures[future]
                    test_key = key_lookup.get(model_id, "")
                    _print_progress(
                        completed,
                        total,
                        f"{test_key} {model_id}",
                        _result_status(result),
                        elapsed=result.elapsed,
                        quiet=self.quiet,
                    )

        self._print_run_summary(run_results, total, time.monotonic() - wall_start)

        manifest.results = run_results
        setup.progress.finalize()
        return [manifest]

    def read_results(
        self,
        manifests: list[BatchManifest],
        tests: list[TestModel],
        on_result: Callable[[str, TestResult], None] | None = None,
    ) -> dict[str, TestResult]:
        """Read all simulation results from completed batch runs (parallel).
        ``on_result(model_id, result)`` is called on the calling thread as
        each result is stored.

        Every result carries the run's verdict. A test whose last run is
        recorded as failed or timed out — the manifest's ``last_run_status``,
        or the ``status.json`` row for an entry with no recorded verdict
        (:func:`run_verdicts`) — is forced to ``success=False`` before it is
        stored and before the
        callback sees it, whatever its result file reads back as — a
        partial ``.mat`` from a timed-out run parses as a success. The
        verdict is applied in this one reader and not by its callers
        because the caller that forgot was ``run_pipeline``: ``run --merge``
        / ``--rerun`` reported PASS for a merged-in test whose run timed
        out while ``compare`` on the same work dir reported SIM_FAIL, and
        ``run --rerun failed -i`` then offered that partial run for
        acceptance as the primary baseline.

        An entry the last run left :data:`PENDING_RUN_STATUS` — it started
        the test and never finished it — with no result from this process's
        run and no recorded failure (:func:`run_verdicts`) yields NO result:
        its result file is not read, whatever it holds. This is the one
        place "no result exists" is told from "the simulation failed"
        (D145): the comparator reads a test with no result as NOT RUN, and a
        test whose run recorded a failure — here, or in ``status.json`` for a
        pending entry — as SIM_FAIL.

        A run that was cancelled (:data:`CANCELLED_RUN_STATUS`, or a
        cancelled run result in hand) yields no result either: it was
        stopped on request, and what it left on disk is partial.
        """
        work_dir = self.config.work_dir
        assert work_dir is not None  # resolved in Config.__post_init__
        verdicts = run_verdicts(work_dir, manifests)
        test_lookup = {t.model_id: t for t in tests}
        results: dict[str, TestResult] = {}

        def _store(model_id: str, result: TestResult) -> None:
            if model_id in verdicts:
                _force_failed(result, verdicts[model_id])
            results[model_id] = result
            if on_result is not None:
                on_result(model_id, result)

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

                if rr is not None and rr.cancelled:
                    continue  # stopped on request: no result
                if rr and not rr.success:
                    _store(model_id, failed_run_result(rr))
                    continue

                if rr is None and entry.get("last_run_status") == CANCELLED_RUN_STATUS:
                    continue  # stopped on request: no result
                if (
                    rr is None
                    and entry.get("last_run_status") == PENDING_RUN_STATUS
                    and model_id not in verdicts
                ):
                    continue  # never finished: no result (D145)

                if test_model is None:
                    _store(
                        model_id,
                        TestResult(
                            model_id=model_id,
                            success=False,
                            error_message="Test model not found in discovery",
                        ),
                    )
                    continue

                work_items.append((test_key, model_id, test_model, rr))

        if not work_items:
            return results

        total = len(work_items)
        completed = 0
        if not self.quiet:
            print(f"\nReading {total} result files...")
        wall_start = time.monotonic()
        per_test_elapsed: list[float] = []
        elapsed_lock = Lock()

        def _read_one(item):
            test_key, model_id, test_model, rr = item
            t0 = time.monotonic()
            res = self.read_run_result(test_model, test_key, rr)
            return test_key, model_id, res, time.monotonic() - t0

        # Parallelize reads with thread pool
        with ThreadPoolExecutor() as executor:
            futures = {executor.submit(_read_one, item): item for item in work_items}
            for future in as_completed(futures):
                test_key, model_id, result, dt = future.result()
                _store(model_id, result)
                with elapsed_lock:
                    per_test_elapsed.append(dt)
                completed += 1
                short = model_id.rsplit(".", 1)[-1]
                _print_progress(
                    completed, total, f"{test_key} {short}", "read", quiet=self.quiet
                )

        wall = time.monotonic() - wall_start
        total_work = sum(per_test_elapsed)
        if per_test_elapsed and not self.quiet:
            speedup = (total_work / wall) if wall > 0 else 0.0
            avg = total_work / len(per_test_elapsed)
            slowest = max(per_test_elapsed)
            print(
                f"Read phase: {wall:.0f}s wall, {total_work:.0f}s total work, "
                f"{speedup:.1f}x parallel speedup (avg {avg:.1f}s/test, slowest {slowest:.1f}s)"
            )
        return results

    def result_of_run(
        self, test: TestModel, test_key: str, run_result: TestRunResult
    ) -> TestResult | None:
        """What ONE run that just ended produced, from its run result in
        hand: ``None`` for a cancelled run, the failure for a run that
        failed, else the result read back (:meth:`read_run_result`).

        :meth:`read_results`' per-test step for a caller holding one run
        result — the simulation queue reads each test back as it finishes.
        """
        if run_result.cancelled:
            return None
        if not run_result.success:
            return failed_run_result(run_result)
        return self.read_run_result(test, test_key, run_result)

    def read_run_result(
        self,
        test: TestModel,
        test_key: str,
        run_result: TestRunResult | None,
    ) -> TestResult:
        """:meth:`read_result` with the run's timing block (:func:`run_timing`)
        merged into the result's statistics — the one place it is built, for
        every backend.

        With no run result in hand (a reload, a ``compare``, a test ``--merge``
        did not re-run), the one the test's last run kept beside the result
        (:func:`kept_run_result`): its walls, and the statistics the backend
        parsed from the run, which a reload otherwise loses.
        """
        if run_result is None:
            run_result = kept_run_result(
                self.config.work_dir / test_key, test.model_id, test_key
            )
        res = self.read_result(test, test_key, run_result)
        if run_result is not None:
            res.statistics = _with_run_timing(res.statistics, run_result)
        return res

    def read_last_results(
        self,
        tests: list[TestModel],
        on_result: Callable[[str, TestResult], None] | None = None,
    ) -> dict[str, TestResult]:
        """Read results from the most recent batch run in the work
        directory: :meth:`read_results` over the manifest on disk, so each
        result reaches ``on_result`` with its run verdict already applied."""
        work_dir = self.config.work_dir
        if not work_dir.exists():
            return {}

        manifest_paths = sorted(work_dir.glob("batch_manifest.json"))
        if not manifest_paths:
            return {}

        manifests = [BatchManifest.load(p) for p in manifest_paths]
        return self.read_results(manifests, tests, on_result=on_result)
