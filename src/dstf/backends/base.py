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
from collections.abc import Callable, Collection, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any

import numpy as np

from ..config import Config
from ..config_shape import EXECUTABLES, Setting, defaults
from ..discovery.test_registry import TestModel
from ..storage.batch_manifest import (
    CANCELLED_RUN_STATUS,
    PENDING_RUN_STATUS,
    RECORDED_RUN_STATUSES,
    BatchManifest,
)
from ..storage.run_stats import read_run_stats
from .common.result_variables import merge_timing_stats, run_timing
from .locate import Located, find_executable
from .types import Capability, DatasetType, TestResult, TestRunResult

if TYPE_CHECKING:
    from ..discovery.library_state import LibraryState
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

    #: The state of the source files that the thing that runs tests has
    #: read AT LEAST, for a runner whose tool stays alive between tests;
    #: ``None`` while nobody said (it is then read again for the first test
    #: that names a state).
    library: "LibraryState | None" = None

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

    def reloading(self) -> tuple[str, float] | None:
        """While a tool that is up reads the library again for the test it
        is about to run: ``(phase, seconds so far)``, as :meth:`starting`.
        ``None`` otherwise, and always here: this runner keeps no tool."""
        return None

    def _run(
        self, test: TestModel, test_key: str, library: "LibraryState | None"
    ) -> TestRunResult:
        """Run *test* to its end on the calling thread. *library* is
        nothing to this runner: its backend starts a process per test,
        which reads the source as it is."""
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

    def run(
        self,
        test: TestModel,
        test_key: str,
        library: "LibraryState | None" = None,
    ) -> TestRunResult:
        """Run *test* to its end on the calling thread. Never raises on a
        test-level failure; a ``cancel`` makes it return promptly. A run
        nobody announced (``expect``) has no token and cannot be
        cancelled.

        With *library* — the source files as swept when the test was queued
        — the test runs against those files as they were then or later: a
        runner whose live tool read them earlier reads them again first,
        and a library that does not load is the test's failure, with the
        tool's message. Without one (a CLI run, whose tools started for it)
        nothing is asked."""
        with self._lock:
            if self._key != test_key:
                self._key, self._token, self._cancel_asked = test_key, None, False
        try:
            return self._run(test, test_key, library)
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


@dataclass(frozen=True)
class Diagnosis:
    """What ``dstf check`` says of one backend, as :meth:`Backend.diagnose` answers.

    Attributes:
        lines: The lines it prints.
        usable: Whether the backend can run as found.
        fixes: When it cannot, what to do about it, one choice each.
    """

    lines: tuple[str, ...]
    usable: bool
    fixes: tuple[str, ...] = ()


class Backend(ABC):
    """A simulator as dstf drives it: where it is installed, how one test
    runs, and how its result is read back.

    A backend plug-in subclasses this, says what it is in class attributes and
    registers the class with :func:`dstf.backends.register`. The framework
    builds one instance per command, with the project's
    :class:`~dstf.config.Config`; it reads :meth:`locate`, :meth:`diagnose` and
    the class attributes without building one.

    A plug-in implements :meth:`read_result`, and either
    :meth:`run_single_test` — the framework's :meth:`run_tests` then runs the
    tests, ``--parallel`` at a time, and records each — or :meth:`run_tests`
    itself. Every other method has a default that works.

    Attributes:
        capabilities: What the backend can do beyond running a test, as
            :class:`~dstf.backends.types.Capability` flags. A flag is a promise:
            :func:`~dstf.backends.register` refuses a class that declares one
            and does not override the method it needs.
        produced_datasets: The kinds of result data the backend produces
            (:class:`~dstf.backends.types.DatasetType`), as a declaration.
            What is checked is the data a result holds: a criterion whose
            mode cannot score it fails.
        settings: The keys of the backend's block in ``testing.json``'s
            ``backends``, by name. Extend the default, which holds
            ``executables``: ``{**Backend.settings, "key": Setting(...)}``.
            ``Config`` refuses a key that is not here, and the reference page
            and the published schema list these.
        source_types: The ``source_type`` values the backend runs. A project
            that names no backend runs with the first installed one that runs
            its source type.
        binaries: The names the backend's program goes by on ``PATH``, for the
            default :meth:`locate`.
        source_suffix: The suffix of the file a test of this backend runs,
            which its ``test_spec.json`` entry names under ``source``
            (``".py"``); None for a backend whose tests name none — a
            Modelica class is found in the library. ``dstf spec add``
            requires ``--source`` where it is set and refuses it where not.
        artifact_files: Files a run leaves in a test's directory that its
            report page links, as ``(file name, label)`` pairs; one that is not
            there is left out.
        quiet: Whether a run prints nothing to the console. A caller with no
            console sets it; a backend that prints checks it first.
        config: The project's configuration, set by the constructor.
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
    #: The keys of this backend's block in ``testing.json``'s ``backends``
    #: map: name → type, default and description, as ``testing.json``'s own
    #: keys are declared (:mod:`dstf.config_shape`). ``Config`` checks a
    #: block against the table of the backend its entry names — a key that
    #: is not here is a config error that names it. A backend adds its own
    #: settings to ``executables`` (``{**Backend.settings, ...}``).
    settings: Mapping[str, Setting] = {"executables": EXECUTABLES}
    #: The ``source_type`` values this backend runs. Read where NO backend
    #: is named and ``backends`` is empty: the installed backends that run
    #: the project's source type are then searched in registration order.
    #: Empty — a backend that declares none — is never picked that way.
    source_types: frozenset[str] = frozenset()
    #: The names this backend's program goes by on ``PATH``.
    binaries: tuple[str, ...] = ()
    #: The suffix of the file a test runs, named by its entry's ``source``;
    #: None: a test names no file.
    source_suffix: str | None = None

    def __init__(self, config: Config):
        """Keep the project's configuration.

        A subclass that extends this calls it first. A backend whose tool cannot
        be used on this machine — an optional package that does not import —
        raises :class:`RuntimeError` with the reason here; the framework reports
        it as the backend being unavailable.

        Args:
            config: The project's configuration, resolved: its paths are absolute
                and its backend's settings are in ``config.backend_settings``.
        """
        self.config = config
        self.quiet: bool = False
        self.progress = None  # set to a ProgressReporter during run_tests()
        # Optional model_id → "ref_NNNN" map for dashboard report links
        # (set by CLI before run_tests if reference IDs are known)
        self.ref_id_map: dict[str, str | None] = {}

    @classmethod
    def locate(cls, settings: Mapping[str, Any]) -> Located:
        """Find this backend's install: the only place it is looked for.

        ``Config`` asks it which backend a project runs with and which executable
        a run uses, and ``dstf check`` prints what it answers. The default finds
        one executable by the rules of :func:`~dstf.backends.locate.find_executable`:
        the executables ``settings`` names (``--backend-path`` among them), then
        :attr:`binaries` on ``PATH``. Override it for an environment variable,
        standard install folders or a second part to find.

        Args:
            settings: The backend's block of ``testing.json``, paths made absolute;
                empty when there is none. The command line's ``--backend-path``
                arrives in it too.

        Returns:
            Each part of the install, first the one a run cannot do without, with
            its path and the rule that found it — or, not found, where it was
            looked for. Settings that name a path and find nothing there must
            return the part not found: the framework then searches nowhere else.
        """
        return Located((find_executable(settings, cls.binaries),))

    @classmethod
    def settings_of(cls, config: Config) -> dict[str, Any]:
        """The backend's settings for a run of ``config``.

        Args:
            config: The project's configuration.

        Returns:
            Its block of ``testing.json``, over the defaults of :attr:`settings`.
        """
        return {**defaults(cls.settings), **getattr(config, "backend_settings", {})}

    @classmethod
    def executable_of(cls, config: Config) -> str | None:
        """The executable a run of ``config`` drives.

        Args:
            config: The project's configuration.

        Returns:
            The path :meth:`locate` found for it, or None when it was not found.
        """
        if getattr(config, "located", None) is not None:
            return config.backend_path
        return getattr(config, "backend_path", None) or cls.locate({}).executable

    @classmethod
    def why_not_found(cls, config: Config) -> str:
        """Which part of this backend a run of ``config`` is missing, and
        where it was looked for."""
        located = getattr(config, "located", None) or cls.locate({})
        return located.missing()

    @classmethod
    def require_found(cls, config: Config) -> None:
        """Raise ``RuntimeError`` when ``config`` looked for this backend
        and did not find it. THE check a run starts with
        (``backends.select_backend(..., to_run=True)``), whatever the
        backend and whichever its run mode: a backend whose configured path
        is gone is not driven through whatever else the simulator's own
        tooling would pick up, and none runs on to one failed simulation
        per test."""
        located = getattr(config, "located", None)
        if located is not None and not located.found:
            raise RuntimeError(f"{config.backend_name} not found: {located.missing()}.")

    @classmethod
    def diagnose(
        cls, located: Located, *, entry: str = "", config: Config | None = None
    ) -> Diagnosis:
        """What ``dstf check`` prints of this backend, and whether it is usable.

        The default prints the parts :meth:`locate` found and calls the backend
        usable when every part was found. Override it to say more of an install —
        a version, an import that must work — and add to those lines.

        Args:
            located: What :meth:`locate` answered.
            entry: The name of the ``backends`` entry the settings came from, or
                ``""``.
            config: The project's configuration, or None where there is no project.

        Returns:
            The lines to print, whether the backend is usable as found, and when it
            is not, the fixes to offer.
        """
        return Diagnosis(tuple(located.lines(entry)), located.complete)

    @classmethod
    def persistent_backend_cls(cls) -> type["Backend"] | None:
        """The variant of this backend that keeps its simulator open between tests.

        Override it on a backend that declares
        :attr:`~dstf.backends.types.Capability.PERSISTENT_WORKERS`, and import the
        variant inside the method, so that its optional dependency is not imported
        by a run that does not use it.

        Returns:
            A subclass of :class:`~dstf.backends.persistent.PersistentBackendBase`,
            or None for a backend that has none.
        """
        return None

    @classmethod
    def preflight(cls, config: Config) -> None:
        """Check, cheaply, that what the backend needs beside its executable is
        there — a Python package, a licence file.

        Called before a persistent backend's workers are started. The executable
        itself is :meth:`locate`'s to find; this is for anything else. The
        default checks nothing.

        Args:
            config: The project's configuration.

        Raises:
            RuntimeError: Something is missing; the message says what and how to
                install it, and is shown as it is.
        """
        return None

    def describe_tool_version(self) -> str | None:
        """The simulator's version, for a run's record, when it is cheap to ask.

        Called before every run. A version that needs a running simulator is a
        persistent backend's to report once its workers are up.

        Returns:
            The version (``"Dymola 2026x"``), or None when the backend cannot say
            it cheaply. It must not raise.
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
        """Export a test's model as an FMU.

        Override it on a backend that declares
        :attr:`~dstf.backends.types.Capability.FMU_EXPORT`; the default raises.

        Args:
            test: The test whose model to export.
            output_dir: The directory to write the FMU into.

        Returns:
            The absolute path of the FMU written.

        Raises:
            NotImplementedError: The backend does not export FMUs.
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
        """Run one test, blocking until it ends.

        The default :meth:`run_tests` calls it once per test, from several threads
        under ``--parallel``; the simulation queue of ``dstf author`` calls it too.
        A test-level failure — the model does not translate, the solver gives up,
        the time limit passes — is a result with ``success=False`` and the reason
        in ``error_message``, not an exception.

        Args:
            test: The test, its simulation settings resolved (``stop_time``,
                ``tolerance``, ``method``, ``number_of_intervals``,
                ``output_interval``, ``timeout``).
            test_key: The name of the test's directory in the work directory,
                ``config.work_dir / test_key``, where its result is to be written.
                The framework empties it before the run.
            index: The test's place in the run, from 1.
            total: How many tests the run holds.

        Returns:
            How the run went. Its result is read back by :meth:`read_result`.
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
        """Stop the test :meth:`run_single_test` is running under ``test_key``.

        Called from another thread. Override it on a backend that declares
        :attr:`~dstf.backends.types.Capability.CANCEL`; that
        :meth:`run_single_test` then returns a result with ``cancelled=True``.

        Args:
            test_key: The key the test runs under.

        Returns:
            Whether a test was running under that key.

        Raises:
            NotImplementedError: The backend cannot stop a running test.
        """
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
        """Read back the result of a test's run.

        Called after the run, and again by ``compare`` and by ``dstf author``, which
        read a work directory without running: so it reads from the files the run
        wrote, not from memory. A result that cannot be read is a result with
        ``success=False`` and the reason, not an exception.

        Args:
            test: The test.
            test_key: The name of the test's directory in ``config.work_dir``.
            run_result: The run's own account, or None when it is not at hand.

        Returns:
            The tracked variables (the test's ``variable_patterns`` resolved
            against what the result holds), each a time series named by its
            name in the result.
        """
        ...

    @classmethod
    def result_names(cls, test_dir: Path) -> list[str] | None:
        """Every variable name the result file of a test holds, tracked or not.

        A class method: a report reads a test's files without building a backend.
        It must not raise on a missing, half-written or malformed file.

        Args:
            test_dir: The test's directory, ``work_dir / test_key``.

        Returns:
            The names, without ``time``; None when the backend keeps no file it can
            list, in which case only tracked variables can be plotted.
        """
        return None

    @classmethod
    def result_series(
        cls, test_dir: Path, names: Collection[str]
    ) -> dict[str, tuple[np.ndarray, np.ndarray]]:
        """Variables of a test's result file, by name — what a plot of an untracked
        variable reads.

        Like :meth:`result_names`, a class method that must not raise.

        Args:
            test_dir: The test's directory, ``work_dir / test_key``.
            names: The variables wanted.

        Returns:
            A mapping of each name the file holds to ``(time, values)``, as
            1-D float arrays; a name it lacks is left out.
        """
        return {}

    @classmethod
    def live_result_path(cls, test_dir: Path) -> Path | None:
        """The result file that grows while a test's run writes it.

        Override it on a backend that declares
        :attr:`~dstf.backends.types.Capability.LIVE_RESULTS`: the authoring tool
        reads that file to draw a running test. It need not exist yet.

        Args:
            test_dir: The test's directory, ``work_dir / test_key``.

        Returns:
            The file's path, or None for a backend that writes its result once,
            at the end.
        """
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

        from ..reporting.dashboard_render import build_rerun_flags, run_given
        from .progress import ProgressReporter

        # Cheap version probes run here, eagerly (subprocess --version);
        # a version that needs a live session arrives later via
        # _probe_worker_version once a worker is up.
        run_metadata = self._build_run_metadata(self._safe_tool_version())
        progress = ProgressReporter(
            self.config.work_dir,
            total,
            rerun_flags=build_rerun_flags(self.config),
            metadata=run_metadata,
            theme=self.config.theme,
            run_given=run_given(self.config),
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
        """Run every test, and record how each went in the work directory.

        The default runs :meth:`run_single_test` for each test, ``--parallel`` at a
        time, with progress on the console and on the live dashboard. Override it
        to run a batch of tests in one go; an override goes through the same
        bookkeeping as the default, by calling ``self._prepare_run(tests)`` first.

        Args:
            tests: The tests to run.

        Returns:
            The batch manifests the run wrote, which ``read_results`` reads
            the results by.
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

        def _end_row(test_key: str, rr: TestRunResult) -> None:
            # A result that came back without a word to the reporter — a
            # test its backend could not start — ends its row here.
            setup.progress.on_result(
                test_key,
                rr.success,
                rr.elapsed,
                detail=rr.error_message,
                timed_out=rr.timed_out,
            )

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
                manifest.record_result(result, setup.progress.metadata)
                _end_row(test_key, result)
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
                    manifest.record_result(result, setup.progress.metadata)
                    completed += 1
                    model_id = futures[future]
                    test_key = key_lookup.get(model_id, "")
                    _end_row(test_key, result)
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

        ``read_results``' per-test step for a caller holding one run
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
        directory: ``read_results`` over the manifest on disk, so each
        result reaches ``on_result`` with its run verdict already applied."""
        work_dir = self.config.work_dir
        if not work_dir.exists():
            return {}

        manifest_paths = sorted(work_dir.glob("batch_manifest.json"))
        if not manifest_paths:
            return {}

        manifests = [BatchManifest.load(p) for p in manifest_paths]
        return self.read_results(manifests, tests, on_result=on_result)
