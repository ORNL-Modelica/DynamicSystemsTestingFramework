"""The `run` and `compare` commands, plus the backend selection and report
routing they share."""

import argparse
import sys
from pathlib import Path

from ..backends import dataset_kind_for
from ..backends.progress import recorded_backend
from ..config import DEFAULT_STOP_TIME, ConfigError
from ..discovery.test_registry import discover_tests
from ..pipeline import run_pipeline, wipe_stale_state_for_scope, write_id_mapping
from .common import (
    _build_config,
    _exit_code,
    _filter_tests,
    _find_orphan_manifest_entries,
    _parse_review_filter,
    _should_review,
)
from .review import _interactive_review

#: `--report-format`: each format and the file it is written to, in the
#: current directory, when no --output names one (``console`` writes none).
#: The first is the default.
REPORT_FILES: dict[str, str | None] = {
    "console": None,
    "junit": "test-results.xml",
    "html": "test-report.html",
}


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


def _refuse_batch_without_workers(config) -> None:
    """``--batch`` asks for the backend's script mode INSTEAD of its workers
    that stay alive. A backend with no such workers (FMPy, Python) runs its
    tests one way, and the flag chose nothing there: a flag that does not
    apply is an error, said before anything simulates. An unregistered
    backend name is :func:`_get_backend`'s to say."""
    from ..backends import Capability, get_backend_class

    try:
        cls = get_backend_class(config)
    except ValueError:
        return
    if Capability.PERSISTENT_WORKERS not in cls.capabilities:
        raise ConfigError(
            f"--batch does not apply to the {config.backend} backend: it has "
            f"no workers that stay alive for --batch to turn off, and runs "
            f"its tests one way. Run without --batch."
        )


def _report_accept(
    config,
    stored: int,
    skipped: list[tuple[str, str]],
    warnings: list[str],
    notes: list[str],
) -> int:
    """Print the accept outcome; nonzero when anything was skipped (review
    2026-07-06 finding 30: a partial accept is a failure CI must see). A
    warning — a large baseline file, which IS stored — changes no exit code,
    and neither does a note: the simulate-only tests, which have no baseline
    to store, are named, where `Accepted 0 test baselines` said nothing of
    them."""
    print(f"\nAccepted {stored} test baselines to {config.reference_dir}")
    for line in notes:
        print(line)
    for line in warnings:
        print(f"Warning: {line}")
    if skipped:
        print(f"Skipped {len(skipped)} test(s):")
        for model_id, reason in skipped:
            print(f"  {model_id}: {reason}")
        return 1
    return 0


def _browser(args: argparse.Namespace) -> bool:
    """Whether this command opens a browser tab on the pages it writes:
    yes, unless ``--no-browser``."""
    return not getattr(args, "no_browser", False)


def _say_removed_pages(n: int) -> None:
    """One line for the per-test report pages a run or compare removed and
    is not writing again. The removal is intended — a stale page must not
    survive the run that outdated it — but silent, it read as lost work.
    `compare --report` is what writes them again from EVERY caller: under
    `--accept --report` and `-i … --report` the line used to say "--report
    writes them again", of the flag that had just been given and wrote
    none."""
    if n:
        pages, them = ("page", "it") if n == 1 else ("pages", "them")
        print(
            f"Removed {n} per-test report {pages} written by an earlier --report "
            f"(now stale); `compare --report` writes {them} again."
        )


def _default_stop_time_notes(tests: list, config) -> list[str]:
    """One line per test about to run to the framework's default stop time
    because nothing gave it one: it runs for 1 s, which is rarely what its
    author meant. Why nothing did is said as it is:

    - a Modelica model that was found, with no ``experiment`` annotation
      and no ``simulation.stop_time`` in its entry — neither layer gives one;
    - a Modelica model that was NOT found under ``source_path`` (a
      dependency's: ``Modelica.Blocks.Examples.PID_Controller``, whose
      annotation says ``StopTime=4``) — its annotation was never read, and
      "the model's annotation gives no stop time" was false of it;
    - any other source type, which has no annotation layer: the entry
      gives none. Every backend runs such a test to the same default."""
    ran = f"it runs to the default, {DEFAULT_STOP_TIME:g} s."

    def why(test) -> str:
        if config.source_type != "modelica":
            return "test_spec.json gives no stop time"
        if test.source_file == Path(""):
            return (
                "not found under source_path, so its experiment annotation was not read"
            )
        return (
            "neither test_spec.json nor the model's experiment annotation "
            "gives a stop time"
        )

    return [
        f"{t.model_id}: {why(t)}; {ran}"
        for t in tests
        if t.field_sources.get("stop_time") == "default"
    ]


def cmd_run(args: argparse.Namespace) -> int:
    """Run tests and compare/accept results."""
    config = _build_config(args)
    if args.batch:
        _refuse_batch_without_workers(config)
    all_tests = discover_tests(config)
    tests = _filter_tests(all_tests, args.filter, args.package)
    _notify_orphans(config, all_tests)

    from ..storage.reference_store import ReferenceStore

    store = ReferenceStore(config)
    if not args.accept:
        # A route that scores stops at a baseline it cannot read (`Config
        # error: <reference file>: …`, exit 2) BEFORE it simulates: the
        # reference files are there now. `--merge` and `--rerun` score every
        # discovered test's last result, any other run the tests it runs.
        from ..comparison.comparator import refuse_stored_overrides

        merged = getattr(args, "merge", False) or (
            getattr(args, "rerun", None) is not None
        )
        refuse_stored_overrides(all_tests if merged else tests, store)
    write_id_mapping(store, config)

    backend = _get_backend(config, persistent=not args.batch)

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
                theme=config.theme,
                comparisons=prior_comps,
                results=prior_results,
            )
            # The verdicts as they stand, on whatever was asked for. The
            # exit code stays 0 regardless of them: "nothing to rerun" is
            # this invocation's whole outcome.
            scope_tests = [t for t in all_tests if t.model_id in prior_results]
            _show_reports(prior_comps, prior_results, scope_tests, store, config, args)
            return 0

    if not tests:
        print("No tests matched the filter.")
        return 1

    for line in _default_stop_time_notes(tests, config):
        print(line)

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
    render_live(
        config.work_dir, rerun_prefix=build_rerun_prefix(config), theme=config.theme
    )
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
    results = None
    try:
        open_in_browser(config.work_dir / "dashboard.html", launch=_browser(args))

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
        # Only the scoring route below writes pages; --accept and -i return
        # without, whatever --report says.
        if not (args.report and not args.accept and args.interactive is None):
            _say_removed_pages(outcome.report_pages_removed)

        if args.accept:
            # review 2026-07-06 finding 30 (part 2): accept ONLY the tests
            # simulated in THIS run (`tests`), never the merged --rerun/--merge
            # scope (`scope_tests`) — the merged scope's results for
            # out-of-scope tests are stale on-disk artifacts from older runs,
            # and stale results must never become baselines.
            from ..storage.accept import accept_run_results

            return _report_accept(
                config, *accept_run_results(store, tests, results, config.work_dir)
            )
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
                theme=config.theme,
                comparisons=comparisons,
                results=results,
            )
            final_rendered = True
            return _interactive_review(
                scope_tests,
                results,
                comparisons,
                store,
                config,
                review_filter=args.interactive,
                browser=_browser(args),
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
            # comparison, the walls from the run's run_stats.json, a CPU
            # time where it kept none from its result; the Reference link
            # is the sidecar's, and stays empty.
            render_final(
                config.work_dir,
                rerun_prefix=build_rerun_prefix(config),
                theme=config.theme,
                comparisons=comparisons,
                results=results,
            )
            final_rendered = True

            _show_reports(comparisons, results, scope_tests, store, config, args)
            return _exit_code(comparisons)
    finally:
        if not final_rendered:
            # A page is not worth the run's own exception: a failing render
            # here replaced a Ctrl+C in flight with its OSError.
            try:
                render_final(
                    config.work_dir,
                    rerun_prefix=build_rerun_prefix(config),
                    theme=config.theme,
                    comparisons=comparisons or (),
                    results=results,
                )
            except Exception as e:
                print(
                    f"# WARN: could not write the final dashboard: {e}", file=sys.stderr
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
    if not getattr(args, "accept", False):
        # As `run` does, before anything is wiped or stamped.
        from ..comparison.comparator import refuse_stored_overrides

        refuse_stored_overrides(tests, store)
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
    pages_removed = wipe_stale_state_for_scope(
        config,
        tests,
        backend.ref_id_map,
        wipe_sim_dirs=False,
    )
    # --accept returns without a report, whatever --report says.
    if getattr(args, "accept", False) or not getattr(args, "report", False):
        _say_removed_pages(pages_removed)
    # The verdicts status.json holds for these tests are of an earlier
    # compare: cleared here, and stamped again below by a route that scores
    # (an --accept scores nothing, and leaves none standing).
    from ..backends.progress import stamp_verdicts

    stamp_verdicts(config.work_dir, {t.model_id: None for t in tests})

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

        return _report_accept(
            config, *accept_run_results(store, tests, results, config.work_dir)
        )

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
    # NRMSE, Variables and Warnings come from its comparison, the walls
    # from the run's run_stats.json, a CPU time where it kept none from its
    # result; the Reference link is the sidecar's, and stays empty.
    from ..reporting.dashboard_render import build_rerun_prefix, render_final

    render_final(
        config.work_dir,
        rerun_prefix=build_rerun_prefix(config),
        theme=config.theme,
        comparisons=comparisons,
        results=results,
    )

    _show_reports(comparisons, results, tests, store, config, args)
    return _exit_code(comparisons)


def _show_reports(comparisons, results, tests, store, config, args) -> None:
    """What a scored ``run`` or ``compare`` shows — three independent
    things, and any combination does all of what it asks:

    * ``verdicts.json`` in the work dir and the console verdict table,
      ALWAYS;
    * ``--report``: the per-test pages;
    * ``--report-format junit|html``: that file, at ``--output``.

    In that order: the verdict first, and the HTML file after the pages its
    rows link. (``--report`` used to return before the other two, and a
    ``--report-format`` printed no table — `compare --report
    --report-format junit` wrote no file and said nothing.) The exit code
    is the caller's: ``_exit_code``.
    """
    from ..reporting.console_report import print_report
    from ..reporting.verdicts import write_verdicts

    # What was decided, for a script: one file, on every scoring route, and
    # each test's verdict beside its simulation status in status.json.
    write_verdicts(config.work_dir, comparisons, tests, results)
    print_report(comparisons)
    if getattr(args, "report", False):
        _generate_report_suite(
            comparisons, results, tests, store, config, browser=_browser(args)
        )
    _write_report_file(comparisons, args, config, results)


def _generate_report_suite(
    comparisons, results, tests, store, config, *, browser: bool
) -> None:
    """Generate per-test reports + refresh the unified dashboard, and open
    it in a browser tab when ``browser``.

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
        theme=config.theme,
        comparisons=comparisons,
        results=results,
    )
    dashboard_path = config.work_dir / "dashboard.html"
    print(f"Report: {dashboard_path}")
    open_in_browser(dashboard_path, launch=browser)


def _report_output(args: argparse.Namespace, fmt: str) -> str:
    """Where ``--report-format fmt`` writes: ``--output``, else the format's
    own file name in the current directory."""
    default = REPORT_FILES[fmt]
    assert default is not None  # a format that writes a file
    return args.output if args.output is not None else default


def _write_report_file(
    comparisons: list, args: argparse.Namespace, config, results: dict
) -> None:
    """Write the file ``--report-format junit|html`` asks for (``console``,
    the default, asks for none); ``results`` give the HTML page its CPU
    times, as they gave ``render_final``."""
    fmt = args.report_format
    if fmt == "junit":
        from ..reporting.junit_report import generate_junit_report

        out = Path(_report_output(args, fmt))
        generate_junit_report(comparisons, out)
        print(f"JUnit report written to {out}")
    elif fmt == "html":
        # One HTML surface, not three: --report-format html writes the same
        # dashboard page render_final produces (both callers have already
        # refreshed it), just to the requested path.
        from ..reporting.dashboard_render import build_rerun_prefix, render_final_to

        out = Path(_report_output(args, fmt))
        render_final_to(
            config.work_dir,
            out,
            rerun_prefix=build_rerun_prefix(config),
            theme=config.theme,
            comparisons=comparisons,
            results=results,
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
