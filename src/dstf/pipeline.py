"""The run pipeline: the library form of ``dstf run``.

Configuration and tests in, results out. Everything ``cmd_run`` does between
"these are the tests" and "here is the report" lives here — the id mapping, the
stale-state wipe, the live dashboard's first paint, the simulation, the
cross-backend chains, the ``--merge`` scope widening and the result read —
with nothing a CLI owns: it never writes to the console, never exits the
process, never opens a browser and never prompts. Failures are exceptions; the
caller decides whether that is an exit code or a red job panel.

It lives beside :mod:`dstf.config` and :mod:`dstf.plugins` rather than in a
layer package because it orchestrates a backend, a store and a work dir and
belongs to none of them. It imports nothing from the CLI package — the CLI
imports this, never the reverse.

The run's real product is on disk, not in the returned
:class:`RunOutcome`: ``batch_manifest.json``, ``status.json``,
``dashboard.html``, ``reference_manifest.json`` and the per-test sim dirs. The
authoring shell reads a finished run back through ``backend.read_last_results``
rather than holding the outcome, so a caller that drops the return value has
lost nothing the next read cannot recover.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .backends.base import Backend
    from .backends.pool import QueueItem, SimulationPool
    from .backends.types import TestResult, TestRunResult
    from .config import Config
    from .discovery.test_registry import TestModel
    from .storage.batch_manifest import BatchManifest
    from .storage.reference_store import ReferenceStore


@dataclass(frozen=True)
class RunOutcome:
    """What one pipeline run produced.

    ``results`` is keyed by exactly the models in ``scope_tests`` — with
    ``merge`` that is wider than the tests just simulated, because the
    out-of-scope entries are read back from the previous runs' files, and
    never wider than the scope, whatever else the work dir's manifest holds
    from earlier runs. Every result carries its run verdict
    (``Backend.read_results``): a merged-in test whose last run failed or
    timed out is ``success=False`` here exactly as ``compare`` reports it.
    """

    manifests: list[BatchManifest]
    results: dict[str, TestResult]
    scope_tests: list[TestModel]
    notices: list[str]


def write_id_mapping(store: ReferenceStore, config: Config) -> None:
    """Write a ref ID → model ID mapping file to the work directory."""
    active = store.index.active_tests()
    if not active:
        return

    work_dir = config.work_dir
    assert work_dir is not None  # resolved in Config.__post_init__
    mapping_path = work_dir / "reference_manifest.json"
    work_dir.mkdir(parents=True, exist_ok=True)
    mapping = {f"ref_{tid}": active[tid] for tid in sorted(active)}
    # LF on every platform, like every other JSON we write.
    mapping_path.write_text(
        json.dumps(mapping, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


def wipe_stale_state_for_scope(
    config: Config,
    tests: list,
    ref_id_map: dict,
    *,
    wipe_sim_dirs: bool,
) -> None:
    """Clear per-test report sidecars (and optionally sim work dirs) for
    the tests this invocation is about to (re-)process.

    A run cleans its own footprint so a prior run's stale state cannot
    bleed through. Out-of-scope tests' directories are left untouched —
    that's what makes ``--merge`` and ``--rerun`` work.

    Two layers of state to clear:

    1. Per-test report dir at ``<work_dir>/reports/<report_dir>/``. Holds
       the ``comparison_data.json`` sidecar (read by the dashboard's
       enricher) and ``interactive.html``. ``report_dir`` is ``ref_NNNN``
       when a baseline exists for the model, else ``test_NNNN``.

    2. Per-test sim work dir at ``<work_dir>/<test_key>/``. Holds the
       backend's outputs (.mat, log, dsfinal, etc.). ``wipe_sim_dirs=True``
       only for a run — ``cmd_compare`` reads these dirs to re-evaluate
       prior sims and must not erase them.

    ``ref_id_map`` is the *backend's* map (a plain dict of model_id →
    ``"ref_NNNN"``), pre-populated by the caller; not the store's index.

    test_key for an in-scope test is read from the persistent
    ``batch_manifest.json``. Tests not yet in the manifest (first-time
    runs) have nothing to wipe — the backend will create a fresh dir.
    """
    work_dir = config.work_dir
    assert work_dir is not None  # resolved in Config.__post_init__
    from .backends.base import last_test_keys

    model_to_key = last_test_keys(work_dir)

    reports_root = work_dir / "reports"
    for test in tests:
        test_key = model_to_key.get(test.model_id)
        report_dir = ref_id_map.get(test.model_id) or test_key
        if wipe_sim_dirs and test_key:
            sim_dir = work_dir / test_key
            if sim_dir.exists():
                shutil.rmtree(sim_dir)
        if report_dir:
            rep_dir = reports_root / report_dir
            if rep_dir.exists():
                shutil.rmtree(rep_dir)


def run_cross_backend_chains(tests, backend, config, store) -> list[str]:
    """For each test with ``requested_baselines`` listing a known chain target,
    invoke the chain and store the resulting named baseline (4.B.4).

    Today only ``dymola-via-fmpy`` is recognized. Failures are logged + skipped
    (don't abort the run); chains are best-effort enrichment of baselines.
    Requires Windows + Dymola (with FMI export) for the export step — the chain
    silently no-ops on platforms that don't support it (logs a warning).

    Returns the lines describing what it did, for the caller to show however
    it shows things; the chain itself says nothing to a console.
    """
    from .backends.cross_backend import (
        CROSS_BACKEND_BASELINE_NAME,
        produce_dymola_via_fmpy_baseline,
    )

    chain_tests = [
        t for t in tests if CROSS_BACKEND_BASELINE_NAME in (t.requested_baselines or [])
    ]
    if not chain_tests:
        return []

    lines = [
        f"[experimental] Cross-backend chain '{CROSS_BACKEND_BASELINE_NAME}' is "
        f"scoped to autonomous FMU-exportable tests only (no external inputs, "
        f"no python-driver tests); end-to-end validation on real Dymola pending. "
        f"See D65.",
        f"Running cross-backend chains for {len(chain_tests)} test(s)...",
    ]
    n_ok = 0
    for t in chain_tests:
        ok = produce_dymola_via_fmpy_baseline(t, backend, config, store)
        if ok:
            n_ok += 1
            lines.append(
                f"  {t.model_id}: '{CROSS_BACKEND_BASELINE_NAME}' baseline written"
            )
        else:
            lines.append(f"  {t.model_id}: chain skipped (see logs)")
    lines.append(f"Cross-backend chains: {n_ok}/{len(chain_tests)} succeeded")
    return lines


def run_pipeline(
    config: Config,
    tests: list,
    *,
    backend: Backend,
    store: ReferenceStore,
    merge: bool = False,
    candidates: list | None = None,
) -> RunOutcome:
    """Simulate *tests* and read their results back.

    *backend* and *store* come from the caller rather than being built here:
    the CLI already needs the backend before the run (``--rerun`` reads the
    last results through it) and the authoring session holds both.

    *merge* widens the read/compare scope to every model the returned batch
    manifests know about, not just what ran this call — the incremental-rerun
    story. *candidates* is the population that widening draws from, in its own
    order (the CLI passes every discovered test); without it the scope can
    only be *tests*.

    Raises whatever the backend raises. An empty *tests* is not an error here:
    whether "nothing to run" is a failure is the caller's policy.
    """
    if not tests:
        return RunOutcome(manifests=[], results={}, scope_tests=[], notices=[])

    work_dir = config.work_dir
    assert work_dir is not None  # resolved in Config.__post_init__

    write_id_mapping(store, config)

    # Pre-populate model_id → "ref_NNNN" map so the live dashboard can link
    # to the correct per-test report directory (matches generate_report_suite
    # naming), and so the wipe below finds each test's report dir.
    for test in tests:
        ref_id = store.index.get_id(test.model_id)
        if ref_id:
            backend.ref_id_map[test.model_id] = f"ref_{ref_id}"

    # Clean each in-scope test's prior footprint before running. Without
    # this, a prior run's stale `comparison_data.json` sidecar could bleed
    # through `rows.enrich_row` and override a fresh sim's verdict in the
    # dashboard. Out-of-scope tests are untouched so
    # `merge` keeps working.
    work_dir.mkdir(parents=True, exist_ok=True)
    wipe_stale_state_for_scope(config, tests, backend.ref_id_map, wipe_sim_dirs=True)

    # Paint the unified dashboard before the run starts, so a reader sees
    # live progress as tests register. The first ProgressReporter.register
    # call overwrites this initial empty page. Idempotent: the CLI renders
    # it a moment earlier too, before opening a browser tab on it.
    from .reporting.dashboard_render import build_rerun_prefix, render_live

    render_live(work_dir, rerun_prefix=build_rerun_prefix(config), theme=config.theme)

    manifests = backend.run_tests(tests)

    # Enrich batch manifest with ref IDs now that store is available
    for m in manifests:
        m.enrich_ref_ids(store.index)

    # 4.B.4 — cross-backend chains. Tests whose recognizer set
    # requested_baselines to include a known chain target (today only
    # "dymola-via-fmpy") get a second baseline produced by chaining backends.
    # MetricTree leaves can then score against it via "against": "<chain-name>".
    notices = run_cross_backend_chains(tests, backend, config, store)

    if merge:
        merged_model_ids = {
            entry["model_id"] for m in manifests for entry in m.manifest.values()
        }
        population = candidates if candidates is not None else tests
        scope_tests = [t for t in population if t.model_id in merged_model_ids]
    else:
        scope_tests = list(tests)

    # `read_results` walks the whole accumulating batch manifest and emits a
    # "not found in discovery" failure for every model outside `scope_tests`,
    # so on a work dir with history the raw dict carries phantom failures for
    # earlier runs' models. The outcome is the scope, nothing wider.
    scope_ids = {t.model_id for t in scope_tests}
    results = {
        k: v
        for k, v in backend.read_results(manifests, scope_tests).items()
        if k in scope_ids
    }

    return RunOutcome(
        manifests=list(manifests),
        results=results,
        scope_tests=scope_tests,
        notices=notices,
    )


class ItemRun:
    """The run pipeline for ONE test at a time — what :func:`run_pipeline`
    does for a list, cut at the test: its key and "pending" stamp, the wipe
    of that test's stale state only, and after the run its recorded verdict
    and its one result read back.

    It is the simulation pool's ``ItemPath``: the pool's threads call
    :meth:`begin` and :meth:`end` around each run, so both serialize their
    read-modify-write of ``batch_manifest.json`` on one lock.

    What a list run writes once per run is NOT written per item:
    ``status.json`` and ``dashboard.html`` are one run's snapshot, and a
    queue's live view is the tool's own pages. :meth:`write_status` writes
    them for the items given, when asked (a queue that drained).
    """

    def __init__(self, config: Config, *, backend: Backend, store: ReferenceStore):
        self.config = config
        self.backend = backend
        self.store = store
        self._manifest_lock = threading.Lock()

    def begin(self, test: TestModel) -> str:
        """Key *test*, stamp it pending and clear what its last run left;
        returns its key."""
        config, backend = self.config, self.backend
        ref_id = self.store.index.get_id(test.model_id)
        if ref_id:
            backend.ref_id_map[test.model_id] = f"ref_{ref_id}"
        with self._manifest_lock:
            _, keyed = backend.key_tests([test])
        wipe_stale_state_for_scope(
            config, [test], backend.ref_id_map, wipe_sim_dirs=True
        )
        return keyed[0][1]

    def end(
        self, test: TestModel, test_key: str, run_result: TestRunResult
    ) -> TestResult | None:
        """Record *run_result* in the manifest (a cancelled run as
        cancelled) and return the result read back — ``None`` for a
        cancelled run, the failure for a failed one."""
        from .storage.batch_manifest import BatchManifest

        work_dir = self.config.work_dir
        assert work_dir is not None  # resolved in Config.__post_init__
        with self._manifest_lock:
            # Re-read: another item's thread has saved since this one's
            # begin, and ref ids may have been assigned by an accept.
            manifest = BatchManifest.load(work_dir / "batch_manifest.json")
            manifest.enrich_ref_ids(self.store.index)
            manifest.record_result(run_result)
        return self.backend.result_of_run(test, test_key, run_result)

    def write_status(self, items: Iterable[QueueItem]) -> None:
        """Write ``status.json`` and the (live) ``dashboard.html`` for the
        *items* that ran — one snapshot, as a list run leaves it at its end,
        each row passed / failed / timed_out / cancelled. Items that never
        ran (removed, or failed before a worker started) have no row. The
        caller renders the final page (``render_final``) as after a run."""
        from .backends.progress import ProgressReporter, TestStatus
        from .reporting.dashboard_render import build_rerun_prefix

        work_dir = self.config.work_dir
        assert work_dir is not None  # resolved in Config.__post_init__
        rows = []
        started: list[float] = []
        ended: list[float] = []
        for item in items:
            rr = item.run_result
            if rr is None or item.test_key is None:
                continue
            if item.started_at is not None and item.ended_at is not None:
                started.append(item.started_at)
                ended.append(item.ended_at)
            if rr.cancelled:
                status = "cancelled"
            elif rr.timed_out:
                status = "timed_out"
            else:
                status = "passed" if rr.success else "failed"
            rows.append(
                TestStatus(
                    test_key=item.test_key,
                    model_id=item.model_id,
                    status=status,
                    started_wall=item.started_at,
                    elapsed=rr.elapsed,
                    detail=None if rr.success else item.message,
                    worker_id=item.worker_id,
                    report_dir=self.backend.ref_id_map.get(item.model_id)
                    or item.test_key,
                    field_sources=item.test.field_sources,
                )
            )
        work_dir.mkdir(parents=True, exist_ok=True)
        progress = ProgressReporter(
            work_dir,
            len(rows),
            rerun_prefix=build_rerun_prefix(self.config),
            metadata=self.backend._build_run_metadata(**self.backend.run_versions()),
            theme=self.config.theme,
        )
        # The wall time of the items reported: from the first one's start
        # to the last one's end.
        now = time.time()
        progress.load(
            rows,
            started_wall=min(started, default=now),
            ended_wall=max(ended, default=now),
        )
        progress.finalize()


def open_pool(
    config: Config,
    *,
    backend: Backend,
    store: ReferenceStore,
    on_start: Callable[[QueueItem], None] | None = None,
    on_end: Callable[[QueueItem], None] | None = None,
    workers: int | None = None,
) -> SimulationPool:
    """A simulation pool over *backend* that runs each item through
    :class:`ItemRun` — the pool's ``path``, so ``pool.path.write_status``
    writes ``status.json`` and the dashboard for the items given.

    *backend* runs the items, so it is the persistent-preferring one
    (``select_backend(config, persistent=True)``). See
    :class:`~dstf.backends.pool.SimulationPool` for the queue itself, the
    callbacks' contract and ``workers``.
    """
    from .backends.pool import SimulationPool

    write_id_mapping(store, config)
    return SimulationPool(
        backend,
        ItemRun(config, backend=backend, store=store),
        on_start=on_start,
        on_end=on_end,
        workers=workers,
    )
