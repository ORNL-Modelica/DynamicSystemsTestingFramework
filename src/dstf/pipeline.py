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
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .backends.base import Backend
    from .backends.types import TestResult
    from .config import Config
    from .discovery.test_registry import TestModel
    from .storage.batch_manifest import BatchManifest
    from .storage.reference_store import ReferenceStore


@dataclass(frozen=True)
class RunOutcome:
    """What one pipeline run produced.

    ``results`` covers every model in scope — with ``merge`` that is wider
    than the tests just simulated, because the out-of-scope entries are read
    back from the previous runs' files.
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

    mapping_path = config.work_dir / "reference_manifest.json"
    config.work_dir.mkdir(parents=True, exist_ok=True)
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
    manifest_path = config.work_dir / "batch_manifest.json"
    if manifest_path.exists():
        from .storage.batch_manifest import BatchManifest

        manifest = BatchManifest.load(manifest_path).manifest
        model_to_key = {entry["model_id"]: tk for tk, entry in manifest.items()}
    else:
        model_to_key = {}

    reports_root = config.work_dir / "reports"
    for test in tests:
        test_key = model_to_key.get(test.model_id)
        report_dir = ref_id_map.get(test.model_id) or test_key
        if wipe_sim_dirs and test_key:
            sim_dir = config.work_dir / test_key
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
    # through `_enrich_row_from_comparison` and override a fresh sim's
    # verdict in the dashboard. Out-of-scope tests are untouched so
    # `merge` keeps working.
    config.work_dir.mkdir(parents=True, exist_ok=True)
    wipe_stale_state_for_scope(config, tests, backend.ref_id_map, wipe_sim_dirs=True)

    # Paint the unified dashboard before the run starts, so a reader sees
    # live progress as tests register. The first ProgressReporter.register
    # call overwrites this initial empty page. Idempotent: the CLI renders
    # it a moment earlier too, before opening a browser tab on it.
    from .reporting.dashboard_render import build_rerun_prefix, render_live

    render_live(config.work_dir, rerun_prefix=build_rerun_prefix(config))

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

    results = backend.read_results(manifests, scope_tests)

    return RunOutcome(
        manifests=list(manifests),
        results=results,
        scope_tests=scope_tests,
        notices=notices,
    )
