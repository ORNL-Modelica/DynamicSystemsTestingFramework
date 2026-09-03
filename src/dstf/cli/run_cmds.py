"""The `run` and `compare` commands, plus the runner selection and report
routing they share."""

import argparse
import sys
from pathlib import Path

from ..config import ConfigError
from ..discovery.test_registry import discover_tests
from .common import (
    _build_config,
    _filter_tests,
    _find_orphan_manifest_entries,
    _parse_review_filter,
    _should_review,
    _write_id_mapping,
)
from .review import _interactive_review


def _notify_orphans(config, all_tests: list) -> None:
    """One-line notice if orphan manifest entries exist."""
    orphans = _find_orphan_manifest_entries(config, all_tests)
    if orphans:
        print(
            f"Note: {len(orphans)} orphan manifest entries (models no longer in "
            f"discovery). Run 'manifest cleanup --orphans' to list/prune."
        )


def _wipe_stale_state_for_scope(
    config,
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
       runner's outputs (.mat, log, dsfinal, etc.). ``wipe_sim_dirs=True``
       only for ``cmd_run`` — ``cmd_compare`` reads these dirs to
       re-evaluate prior sims and must not erase them.

    test_key for an in-scope test is read from the persistent
    ``batch_manifest.json``. Tests not yet in the manifest (first-time
    runs) have nothing to wipe — the runner will create a fresh dir.
    """
    import shutil

    manifest_path = config.work_dir / "batch_manifest.json"
    if manifest_path.exists():
        from ..simulators import BatchManifest

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


def _get_runner(config, persistent: bool = False):
    """Get the appropriate simulator runner for the configured simulator.

    When *persistent* is True and the backend **declares**
    :attr:`Capability.PERSISTENT_WORKERS`, pre-flight the variant's
    dependency probe and instantiate it. When the probe raises
    :class:`RuntimeError`, print a unified abort message pointing at both
    fixes (install the dep, or re-run with ``--batch``) and exit ``1`` — no
    silent fallback.

    The declaration is what gates, not the presence of the hook: a backend
    that ships an experimental ``persistent_runner_cls`` it isn't ready to
    have driven says so by leaving the flag off, and lands on the batch
    path. Registration-time validation
    (:func:`simulators._validate_capabilities`) guarantees the converse —
    flag implies a real override — so a declaring backend always has a
    class to return.

    Backend-agnostic: no string matching on backend names. New persistent
    backends plug in by declaring the capability and overriding
    ``persistent_runner_cls`` + ``preflight`` on their runner classes.
    """
    from ..simulators import get_runner_class
    from ..simulators.types import Capability

    batch_cls = get_runner_class(config)
    if not persistent:
        return batch_cls(config)
    if Capability.PERSISTENT_WORKERS not in batch_cls.capabilities:
        # Batch-only backend (FMPy, Python today). Persistent flag is a
        # no-op rather than an error so a single CLI works across mixed
        # backends in a multi-library config.
        return batch_cls(config)
    pcls = batch_cls.persistent_runner_cls()
    if pcls is None:
        # Unreachable for a validated backend (the flag implies the
        # override); kept as the type narrowing for the calls below.
        return batch_cls(config)
    try:
        pcls.preflight(config)
    except RuntimeError as exc:
        backend = config.simulator_backend
        print(
            f"Persistent-worker {backend} unavailable. Aborting.\n"
            f"\n"
            f"{exc}\n"
            f"\n"
            f"Or re-run with `--batch` to use the per-test subprocess runner instead.",
            file=sys.stderr,
        )
        sys.exit(1)
    return pcls(config)


def _dataset_kind_for(runner):
    """The single :class:`DatasetType` *runner* declares, else the default.

    ``produced_datasets`` is the backend's declaration of what shape its
    results carry; the MetricTree evaluator refuses a leaf whose metric
    can't score that shape. When a backend declares exactly one kind we can
    say authoritatively which it is, so we thread it. A backend declaring
    several can't be resolved from the class alone — the kind would have to
    come off each result — so we fall back to ``TIME_SERIES``, which is what
    the evaluator assumes anyway. All five builtins declare exactly
    ``{TIME_SERIES}``, so this is behaviour-neutral today; it exists so the
    first backend to declare something else is scored against the right
    metric set without a second wiring pass.
    """
    from ..simulators.types import DatasetType

    kinds = type(runner).produced_datasets
    if len(kinds) == 1:
        return next(iter(kinds))
    return DatasetType.TIME_SERIES


def cmd_run(args: argparse.Namespace) -> int:
    """Run tests and compare/accept results."""
    config = _build_config(args)
    all_tests = discover_tests(config)
    tests = _filter_tests(all_tests, args.filter, args.package)
    _notify_orphans(config, all_tests)

    from ..storage.reference_store import ReferenceStore

    store = ReferenceStore(config)
    _write_id_mapping(store, config)

    runner = _get_runner(config, persistent=not getattr(args, "batch", False))

    # --rerun selects tests by status from prior comparisons (no new sim yet).
    # Default category: failed. Implies --merge so the report covers the full suite.
    if getattr(args, "rerun", None) is not None:
        from ..comparison.comparator import compare_all

        rerun_filter = args.rerun or "failed"
        rerun_categories = _parse_review_filter(rerun_filter)
        prior_results = runner.read_last_results(all_tests)
        if not prior_results:
            print(
                "--rerun requires prior results in the work directory. Run a full pass first."
            )
            return 1
        prior_comps = compare_all(
            all_tests,
            prior_results,
            store,
            config.tolerance,
            config.default_points,
            dataset_kind=_dataset_kind_for(runner),
        )
        rerun_models = {
            c.model_id for c in prior_comps if _should_review(c, rerun_categories)
        }
        tests = [t for t in tests if t.model_id in rerun_models]
        args.merge = True
        print(
            f"--rerun {rerun_filter}: selected {len(tests)} of {len(prior_comps)} tests"
        )

        if not tests:
            # review 2026-07-06 finding 59: an empty rerun set means the
            # prior run already satisfies the requested categories — that
            # is SUCCESS (a green prior run must not fail CI), unlike an
            # empty user --filter below. Still refresh the merged report so
            # the dashboard reflects the (unchanged) full-suite state.
            print(f"Nothing to rerun (no tests in categories: {rerun_filter})")
            config.work_dir.mkdir(parents=True, exist_ok=True)
            from ..reporting.dashboard_render import build_rerun_prefix, render_final

            render_final(config.work_dir, rerun_prefix=build_rerun_prefix(config))
            if args.report:
                scope_tests = [t for t in all_tests if t.model_id in prior_results]
                # Exit code stays 0 regardless of prior-result verdicts:
                # "nothing to rerun" is this invocation's whole outcome.
                _generate_report_suite(
                    prior_comps, prior_results, scope_tests, store, config
                )
            return 0

    if not tests:
        print("No tests matched the filter.")
        return 1

    # Pre-populate model_id → "ref_NNNN" map so the live dashboard can link
    # to the correct per-test report directory (matches generate_report_suite naming).
    for test in tests:
        ref_id = store.index.get_id(test.model_id)
        if ref_id:
            runner.ref_id_map[test.model_id] = f"ref_{ref_id}"

    # Clean each in-scope test's prior footprint before running. Without
    # this, a prior run's stale `comparison_data.json` sidecar could bleed
    # through `_enrich_row_from_comparison` and override a fresh sim's
    # verdict in the dashboard. Out-of-scope tests are untouched so
    # `--merge` and `--rerun` keep working.
    config.work_dir.mkdir(parents=True, exist_ok=True)
    _wipe_stale_state_for_scope(
        config,
        tests,
        runner.ref_id_map,
        wipe_sim_dirs=True,
    )

    # Open the unified dashboard *before* the run starts so the user sees
    # live progress as tests register. The first ProgressReporter.register
    # call will overwrite this initial empty page; subsequent meta-refresh
    # ticks pick up updates. Final mode lands at the same URL after compare.
    from ..reporting.dashboard_render import build_rerun_prefix, render_live
    from ..reporting.report_suite import open_in_browser

    render_live(config.work_dir, rerun_prefix=build_rerun_prefix(config))
    open_in_browser(config.work_dir / "dashboard.html")

    try:
        manifests = runner.run_tests(tests)
    except RuntimeError as exc:
        # Runners raise on unrecoverable startup conditions (e.g. all persistent
        # workers failed to spawn). Convert to a clean exit instead of dumping a
        # traceback — the runner has typically already printed the cause.
        print(f"\nRun aborted: {exc}", file=sys.stderr)
        return 1

    # Enrich batch manifest with ref IDs now that store is available
    for m in manifests:
        m.enrich_ref_ids(store.index)

    # 4.B.4 — cross-backend chains. Tests whose recognizer set
    # requested_baselines to include a known chain target (today only
    # "dymola-via-fmpy") get a second baseline produced by chaining backends.
    # MetricTree leaves can then score against it via "against": "<chain-name>".
    _run_cross_backend_chains(tests, runner, config, store)

    # --merge expands the read/compare/report scope to every test that has
    # results on disk (per the persistent batch manifest), not just what was
    # rerun this invocation. Lets you incrementally rerun a subset and still
    # get a full report covering the whole suite.
    if getattr(args, "merge", False):
        merged_model_ids = {
            entry["model_id"] for m in manifests for entry in m.manifest.values()
        }
        scope_tests = [t for t in all_tests if t.model_id in merged_model_ids]
    else:
        scope_tests = tests

    results = runner.read_results(manifests, scope_tests)

    if args.accept:
        # review 2026-07-06 finding 30 (part 2): accept ONLY the tests
        # simulated in THIS run (`tests`), never the merged --rerun/--merge
        # scope (`scope_tests`) — the merged scope's results for
        # out-of-scope tests are stale on-disk artifacts from older runs,
        # and stale results must never become baselines.
        stored, skipped = store.accept_results(tests, results)
        print(f"\nAccepted {stored} test baselines to {config.reference_dir}")
        if skipped:
            # review 2026-07-06 finding 30 (part 1): a partial accept is a
            # failure — name what was skipped and exit nonzero so CI can't
            # silently bless an incomplete baseline set.
            print(f"Skipped {len(skipped)} test(s):")
            for model_id, reason in skipped:
                print(f"  {model_id}: {reason}")
            return 1
        return 0
    elif args.interactive is not None:
        from ..comparison.comparator import compare_all

        comparisons = compare_all(
            scope_tests,
            results,
            store,
            config.tolerance,
            config.default_points,
            dataset_kind=_dataset_kind_for(runner),
        )
        return _interactive_review(
            scope_tests,
            results,
            comparisons,
            store,
            config,
            review_filter=args.interactive,
        )
    else:
        from ..comparison.comparator import compare_all

        comparisons = compare_all(
            scope_tests,
            results,
            store,
            config.tolerance,
            config.default_points,
            dataset_kind=_dataset_kind_for(runner),
        )

        # Always refresh the unified dashboard before any --report work.
        # Without --report the per-test sidecars don't exist, so the
        # post-comparison columns stay dashed; the live snapshot is
        # frozen at the moment compare_all finished.
        from ..reporting.dashboard_render import build_rerun_prefix, render_final

        render_final(config.work_dir, rerun_prefix=build_rerun_prefix(config))

        if args.report:
            return _generate_report_suite(
                comparisons, results, scope_tests, store, config
            )

        return _output_report(comparisons, args, config)


def _run_cross_backend_chains(tests, runner, config, store) -> None:
    """For each test with ``requested_baselines`` listing a known chain target,
    invoke the chain and store the resulting named baseline (4.B.4).

    Today only ``dymola-via-fmpy`` is recognized. Failures are logged + skipped
    (don't abort the run); chains are best-effort enrichment of baselines.
    Requires Windows + Dymola (with FMI export) for the export step — the chain
    silently no-ops on platforms that don't support it (logs a warning).
    """
    from ..simulators.cross_backend import (
        CROSS_BACKEND_BASELINE_NAME,
        produce_dymola_via_fmpy_baseline,
    )

    chain_tests = [
        t for t in tests if CROSS_BACKEND_BASELINE_NAME in (t.requested_baselines or [])
    ]
    if not chain_tests:
        return

    print(
        f"[experimental] Cross-backend chain '{CROSS_BACKEND_BASELINE_NAME}' is "
        f"scoped to autonomous FMU-exportable tests only (no external inputs, "
        f"no python-driver tests); end-to-end validation on real Dymola pending. "
        f"See D65."
    )
    print(f"Running cross-backend chains for {len(chain_tests)} test(s)...")
    n_ok = 0
    for t in chain_tests:
        ok = produce_dymola_via_fmpy_baseline(t, runner, config, store)
        if ok:
            n_ok += 1
            print(f"  {t.model_id}: '{CROSS_BACKEND_BASELINE_NAME}' baseline written")
        else:
            print(f"  {t.model_id}: chain skipped (see logs)")
    print(f"Cross-backend chains: {n_ok}/{len(chain_tests)} succeeded")


def _recorded_backend(work_dir: Path) -> str | None:
    """The simulator label the last run in *work_dir* used, per ``status.json``.

    ``None`` when the work dir has no snapshot, an unreadable one, or one
    written before run provenance was stamped (``metadata: null``).
    """
    import json

    try:
        snap = json.loads((work_dir / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    meta = snap.get("metadata") or {}
    label = meta.get("simulator") or meta.get("backend")
    return label if isinstance(label, str) and label else None


def cmd_compare(args: argparse.Namespace) -> int:
    """Compare last run results against stored references."""
    config = _build_config(args)

    # Without --simulator, `compare` used to auto-detect a backend from the
    # environment — which on a machine with Dymola installed meant comparing
    # an OpenModelica work dir against the Dymola reference partition and
    # reporting SIM_FAIL for every test. The work dir knows what produced it:
    # prefer the run's own recorded backend. An explicit --simulator still
    # wins, and a work dir with no metadata keeps the auto-detect behaviour.
    if not getattr(args, "simulator", None):
        work_dir = config.work_dir
        recorded = _recorded_backend(work_dir)
        if recorded and recorded != config.simulator:
            args.simulator = recorded
            config = _build_config(args)
            # status.json is data, and data can name a backend this install
            # can't resolve — a plug-in that isn't loaded here, a snapshot
            # copied from another machine. get_runner_class raises a bare
            # ValueError that main() doesn't catch, so translate it before it
            # becomes a traceback.
            from ..simulators import get_runner_class

            try:
                get_runner_class(config)
            except ValueError as exc:
                raise ConfigError(
                    f"The last run in {work_dir} recorded backend "
                    f"'{recorded}', which is not registered here: {exc} "
                    f"Pass --simulator to choose one explicitly."
                ) from exc
            print(
                f"Using backend '{recorded}' recorded by the last run in "
                f"{work_dir} (pass --simulator to override)."
            )

    all_tests = discover_tests(config)
    tests = _filter_tests(all_tests, args.filter, args.package)
    _notify_orphans(config, all_tests)

    from ..comparison.comparator import compare_all
    from ..storage.reference_store import ReferenceStore

    runner = _get_runner(config)
    store = ReferenceStore(config)
    _write_id_mapping(store, config)

    # Pre-compute ref_id_map so the wipe + downstream report rendering can
    # find each test's `ref_NNNN/` report dir. Compare reads prior sim
    # outputs from disk, so the sim work dirs must NOT be wiped here —
    # only the (now-stale) per-test report sidecars + HTML.
    for test in tests:
        ref_id = store.index.get_id(test.model_id)
        if ref_id:
            runner.ref_id_map[test.model_id] = f"ref_{ref_id}"
    config.work_dir.mkdir(parents=True, exist_ok=True)
    _wipe_stale_state_for_scope(
        config,
        tests,
        runner.ref_id_map,
        wipe_sim_dirs=False,
    )

    results = runner.read_last_results(tests)

    if getattr(args, "accept", False):
        # Accept-without-rerun: store the last run's on-disk results as
        # baselines. read_last_results already forced any test whose run the
        # batch manifest records as failed/timed_out to success=False (falling
        # back to the status.json cross-check only for manifest entries
        # written before that field existed), so accept_results skips them —
        # a partial/stale .mat can't become a baseline.
        #
        # Pending entries need their own refusal: a test that was in flight
        # when a run was aborted has no recorded verdict at all, and its
        # truncated .mat may still parse cleanly. Acceptance requires an
        # authoritative *successful run*, so refuse rather than guess.
        # Note this is deliberately NOT keyed on the comparison verdict — a
        # test that ran fine and now compares FAILED is exactly the drift case
        # `--accept` exists to re-baseline.
        # Terminal, mirroring `run --accept` (finding 30: partial accept
        # exits nonzero).
        from ..simulators.base import unfinished_run_models

        unfinished = unfinished_run_models(config.work_dir) & {
            t.model_id for t in tests
        }
        stored, skipped = store.accept_results(
            [t for t in tests if t.model_id not in unfinished], results
        )
        skipped = list(skipped) + [
            (
                model_id,
                f"last run did not complete for this test (aborted while it was "
                f"in flight; {config.work_dir} still records it as pending) — "
                f"re-run before accepting",
            )
            for model_id in sorted(unfinished)
        ]
        print(f"\nAccepted {stored} test baselines to {config.reference_dir}")
        if skipped:
            print(f"Skipped {len(skipped)} test(s):")
            for model_id, reason in skipped:
                print(f"  {model_id}: {reason}")
            return 1
        return 0

    comparisons = compare_all(
        tests,
        results,
        store,
        config.tolerance,
        config.default_points,
        dataset_kind=_dataset_kind_for(runner),
    )

    # Always refresh the unified dashboard, even without --report. Without
    # per-test sidecars (no --report run yet), the post-run columns stay
    # dashed and the page reads as a "live" snapshot frozen at the moment
    # comparison finished.
    from ..reporting.dashboard_render import build_rerun_prefix, render_final

    render_final(config.work_dir, rerun_prefix=build_rerun_prefix(config))

    if getattr(args, "report", False):
        return _generate_report_suite(comparisons, results, tests, store, config)

    return _output_report(comparisons, args, config)


def _generate_report_suite(comparisons, results, tests, store, config) -> int:
    """Generate per-test reports + refresh the unified dashboard.

    generate_report_suite (in report_suite) writes per-test
    interactive.html + comparison_data.json sidecars under
    work_dir/reports/. dashboard_render.render_final then reads
    those sidecars and produces the top-level work_dir/dashboard.html.
    """
    from ..reporting.dashboard_render import build_rerun_prefix, render_final
    from ..reporting.report_suite import generate_report_suite, open_in_browser

    generate_report_suite(comparisons, results, tests, store, config)
    render_final(config.work_dir, rerun_prefix=build_rerun_prefix(config))
    dashboard_path = config.work_dir / "dashboard.html"
    print(f"Report: {dashboard_path}")
    open_in_browser(dashboard_path)

    n_failed = sum(1 for c in comparisons if not c.passed)
    return 1 if n_failed else 0


def _output_report(comparisons: list, args: argparse.Namespace, config) -> int:
    """Route comparisons to the selected report format."""
    fmt = getattr(args, "report_format", "console")
    if fmt == "junit":
        from ..reporting.junit_report import generate_junit_report

        out = Path(getattr(args, "output", None) or "test-results.xml")
        generate_junit_report(comparisons, out)
        print(f"JUnit report written to {out}")
        n_failed = sum(1 for c in comparisons if not c.passed)
        return 1 if n_failed else 0
    elif fmt == "html":
        # One HTML surface, not three: --report-format html writes the same
        # dashboard page render_final produces (both callers have already
        # refreshed it), just to the requested path.
        from ..reporting.dashboard_render import build_rerun_prefix, render_final_to

        out = Path(getattr(args, "output", None) or "test-report.html")
        render_final_to(config.work_dir, out, rerun_prefix=build_rerun_prefix(config))
        print(f"HTML report written to {out}")
        if out.resolve().parent != config.work_dir.resolve():
            # The page's per-test rows link reports/<dir>/interactive.html
            # relative to the work dir; from anywhere else they dangle —
            # and so does the Plotly those pages load from the reports
            # root. The dashboard itself needs no Plotly (it draws no
            # charts), so this page renders fully wherever it is written;
            # nothing is copied alongside it.
            print(
                f"note: per-test report links (and the Plotly they load from "
                f"reports/) resolve only inside {config.work_dir}",
                file=sys.stderr,
            )
        n_failed = sum(1 for c in comparisons if not c.passed)
        return 1 if n_failed else 0
    else:
        from ..reporting.console_report import print_report

        return print_report(comparisons)
