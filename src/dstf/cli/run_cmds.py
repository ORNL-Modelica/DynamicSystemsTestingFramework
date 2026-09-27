"""The `run` and `compare` commands, plus the backend selection and report
routing they share."""

import argparse
import sys
from pathlib import Path

from ..backends import dataset_kind_for
from ..backends.progress import recorded_backend
from ..config import ConfigError
from ..discovery.test_registry import discover_tests
from ..pipeline import run_pipeline, wipe_stale_state_for_scope, write_id_mapping
from .common import (
    _build_config,
    _filter_tests,
    _find_orphan_manifest_entries,
    _parse_review_filter,
    _should_review,
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


def _get_backend(config, persistent: bool = False):
    """The CLI's wrapper over :func:`backends.select_backend`.

    The choice itself — persistent variant when the backend declares
    :attr:`Capability.PERSISTENT_WORKERS` and its dependency probe passes,
    batch otherwise — lives in ``backends``, where callers that are not a
    CLI can reach it. Backend-agnostic: no string matching on backend
    names; new persistent backends plug in by declaring the capability and
    overriding ``persistent_backend_cls`` + ``preflight``.

    This layer is only the CLI's answer to "the backend is unavailable":
    print the message and exit ``1``, rather than fall back silently or
    traceback.
    """
    from ..backends import BackendUnavailableError, select_backend

    try:
        return select_backend(config, persistent=persistent)
    except BackendUnavailableError as exc:
        print(f"{exc} Aborting.", file=sys.stderr)
        sys.exit(1)


def _report_accept(config, stored: int, skipped: list[tuple[str, str]]) -> int:
    """Print the accept outcome; nonzero when anything was skipped (review
    2026-07-06 finding 30: a partial accept is a failure CI must see)."""
    print(f"\nAccepted {stored} test baselines to {config.reference_dir}")
    if skipped:
        print(f"Skipped {len(skipped)} test(s):")
        for model_id, reason in skipped:
            print(f"  {model_id}: {reason}")
        return 1
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Run tests and compare/accept results."""
    config = _build_config(args)
    all_tests = discover_tests(config)
    tests = _filter_tests(all_tests, args.filter, args.package)
    _notify_orphans(config, all_tests)

    from ..storage.reference_store import ReferenceStore

    store = ReferenceStore(config)
    write_id_mapping(store, config)

    backend = _get_backend(config, persistent=not getattr(args, "batch", False))

    # --rerun selects tests by status from prior comparisons (no new sim yet).
    # Default category: failed. Implies --merge so the report covers the full suite.
    if getattr(args, "rerun", None) is not None:
        from ..comparison.comparator import compare_all

        rerun_filter = args.rerun or "failed"
        rerun_categories = _parse_review_filter(rerun_filter)
        prior_results = backend.read_last_results(all_tests)
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
            dataset_kind=dataset_kind_for(backend),
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

            render_final(
                config.work_dir,
                rerun_prefix=build_rerun_prefix(config),
                comparisons=prior_comps,
            )
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

    # Open the unified dashboard *before* the run starts so the user sees
    # live progress as tests register. The first ProgressReporter.register
    # call will overwrite this initial empty page; subsequent meta-refresh
    # ticks pick up updates. The page stays live through the backend's
    # finalize; every way out of this command below renders the final page
    # at the same URL, or a watching browser refreshes forever.
    # The page has to exist before the tab opens on it — run_pipeline paints
    # it again a moment later, which is idempotent.
    from ..reporting.dashboard_render import (
        build_rerun_prefix,
        render_final,
        render_live,
    )
    from ..reporting.report_suite import open_in_browser

    config.work_dir.mkdir(parents=True, exist_ok=True)
    render_live(config.work_dir, rerun_prefix=build_rerun_prefix(config))
    # A path that scores renders the final page itself, with its
    # comparisons, before the work that follows (the review, the report
    # suite), and sets ``final_rendered``. Every other way out ends in the
    # ``finally``: an aborted run, ``--accept`` (accepted without scoring:
    # the rows read SIMULATED, which is true), and any exception after the
    # page went live — Ctrl+C mid-simulation or in the compare phase, an
    # error in read_results, compare_all or the accept — which renders
    # with whatever comparisons exist and lets the exception propagate.
    final_rendered = False
    comparisons = None
    try:
        open_in_browser(config.work_dir / "dashboard.html")

        try:
            outcome = run_pipeline(
                config,
                tests,
                backend=backend,
                store=store,
                merge=getattr(args, "merge", False),
                candidates=all_tests,
            )
        except RuntimeError as exc:
            # Backends raise on unrecoverable startup conditions (e.g. all persistent
            # workers failed to spawn). Convert to a clean exit instead of dumping a
            # traceback — the backend has typically already printed the cause.
            print(f"\nRun aborted: {exc}", file=sys.stderr)
            return 1

        for line in outcome.notices:
            print(line)
        results, scope_tests = outcome.results, outcome.scope_tests

        if args.accept:
            # review 2026-07-06 finding 30 (part 2): accept ONLY the tests
            # simulated in THIS run (`tests`), never the merged --rerun/--merge
            # scope (`scope_tests`) — the merged scope's results for
            # out-of-scope tests are stale on-disk artifacts from older runs,
            # and stale results must never become baselines.
            from ..storage.accept import accept_run_results

            stored, skipped = accept_run_results(store, tests, results, config.work_dir)
            return _report_accept(config, stored, skipped)
        elif args.interactive is not None:
            from ..comparison.comparator import compare_all

            comparisons = compare_all(
                scope_tests,
                results,
                store,
                config.tolerance,
                config.default_points,
                dataset_kind=dataset_kind_for(backend),
            )
            render_final(
                config.work_dir,
                rerun_prefix=build_rerun_prefix(config),
                comparisons=comparisons,
            )
            final_rendered = True
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
                dataset_kind=dataset_kind_for(backend),
            )

            # Always refresh the unified dashboard before any --report work.
            # Without --report the per-test sidecars don't exist: each row's
            # verdict, Worst NRMSE, Variables and Warnings come from its
            # comparison; the Translate / Sim / Total walls and the Reference
            # link are the sidecar's, and stay empty.
            render_final(
                config.work_dir,
                rerun_prefix=build_rerun_prefix(config),
                comparisons=comparisons,
            )
            final_rendered = True

            if args.report:
                return _generate_report_suite(
                    comparisons, results, scope_tests, store, config
                )

            return _output_report(comparisons, args, config)
    finally:
        if not final_rendered:
            render_final(
                config.work_dir,
                rerun_prefix=build_rerun_prefix(config),
                comparisons=comparisons or (),
            )


def cmd_compare(args: argparse.Namespace) -> int:
    """Compare last run results against stored references."""
    config = _build_config(args)

    # Without --backend, `compare` used to auto-detect a backend from the
    # environment — which on a machine with Dymola installed meant comparing
    # an OpenModelica work dir against the Dymola reference partition and
    # reporting SIM_FAIL for every test. The work dir knows what produced it:
    # prefer the run's own recorded backend. An explicit --backend still
    # wins, and a work dir with no metadata keeps the auto-detect behaviour.
    if not getattr(args, "backend", None):
        work_dir = config.work_dir
        recorded, warning = recorded_backend(work_dir)
        if warning:
            print(warning, file=sys.stderr)
        if recorded and recorded != config.backend_name:
            args.backend = recorded
            config = _build_config(args)
            # status.json is data, and data can name a backend this install
            # can't resolve — a plug-in that isn't loaded here, a snapshot
            # copied from another machine. get_backend_class raises a bare
            # ValueError that main() doesn't catch, so translate it before it
            # becomes a traceback.
            from ..backends import get_backend_class

            try:
                get_backend_class(config)
            except ValueError as exc:
                raise ConfigError(
                    f"The last run in {work_dir} recorded backend "
                    f"'{recorded}', which is not registered here: {exc} "
                    f"Pass --backend to choose one explicitly."
                ) from exc
            print(
                f"Using backend '{recorded}' recorded by the last run in "
                f"{work_dir} (pass --backend to override)."
            )

    all_tests = discover_tests(config)
    tests = _filter_tests(all_tests, args.filter, args.package)
    _notify_orphans(config, all_tests)

    from ..comparison.comparator import compare_all
    from ..storage.reference_store import ReferenceStore

    backend = _get_backend(config)
    store = ReferenceStore(config)
    write_id_mapping(store, config)

    # Pre-compute ref_id_map so the wipe + downstream report rendering can
    # find each test's `ref_NNNN/` report dir. Compare reads prior sim
    # outputs from disk, so the sim work dirs must NOT be wiped here —
    # only the (now-stale) per-test report sidecars + HTML.
    for test in tests:
        ref_id = store.index.get_id(test.model_id)
        if ref_id:
            backend.ref_id_map[test.model_id] = f"ref_{ref_id}"
    config.work_dir.mkdir(parents=True, exist_ok=True)
    wipe_stale_state_for_scope(
        config,
        tests,
        backend.ref_id_map,
        wipe_sim_dirs=False,
    )

    results = backend.read_last_results(tests)

    if getattr(args, "accept", False):
        # Accept-without-rerun: store the last run's on-disk results as
        # baselines. read_last_results already forced any test whose run the
        # batch manifest records as failed/timed_out to success=False (falling
        # back to the status.json cross-check only for manifest entries
        # written before that field existed), so accept_results skips them —
        # a partial/stale .mat can't become a baseline. The pending-entry
        # refusal `accept_run_results` adds on top is documented there.
        # Terminal, mirroring `run --accept` (finding 30: partial accept
        # exits nonzero).
        from ..storage.accept import accept_run_results

        stored, skipped = accept_run_results(store, tests, results, config.work_dir)
        return _report_accept(config, stored, skipped)

    comparisons = compare_all(
        tests,
        results,
        store,
        config.tolerance,
        config.default_points,
        dataset_kind=dataset_kind_for(backend),
    )

    # Always refresh the unified dashboard, even without --report. Without
    # per-test sidecars (no --report run yet), each row's verdict, Worst
    # NRMSE, Variables and Warnings come from its comparison; the Translate
    # / Sim / Total walls and the Reference link are the sidecar's, and stay
    # empty.
    from ..reporting.dashboard_render import build_rerun_prefix, render_final

    render_final(
        config.work_dir,
        rerun_prefix=build_rerun_prefix(config),
        comparisons=comparisons,
    )

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
    render_final(
        config.work_dir,
        rerun_prefix=build_rerun_prefix(config),
        comparisons=comparisons,
    )
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
        render_final_to(
            config.work_dir,
            out,
            rerun_prefix=build_rerun_prefix(config),
            comparisons=comparisons,
        )
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
