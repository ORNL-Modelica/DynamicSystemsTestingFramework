"""Command-line interface for the Modelica testing system."""

import argparse
import fnmatch
import sys
from pathlib import Path

from .config import Config, ConfigError
from .discovery.test_registry import TestModel, discover_tests


def _split_filter_list(spec: str) -> list[str]:
    """Split a comma-separated filter list on commas NOT inside ``[]``.

    review 2026-07-06 finding 64: naive comma-splitting broke fnmatch
    character classes like ``Test[A,B]*``. Small scanner: a comma inside an
    open ``[`` class belongs to the class. No escape mechanism — an
    unclosed ``[`` makes the rest of the string one pattern (fnmatch treats
    an unmatched ``[`` as a literal anyway).
    """
    parts: list[str] = []
    buf: list[str] = []
    in_class = False
    for ch in spec:
        if ch == "," and not in_class:
            parts.append("".join(buf))
            buf = []
            continue
        if ch == "[" and not in_class:
            in_class = True
        elif ch == "]" and in_class:
            in_class = False
        buf.append(ch)
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _resolve_filter_patterns(spec: str) -> list[str]:
    """Resolve a --filter argument into a list of glob patterns.

    Accepts:
      - "Foo.Bar.*"           — single glob
      - "Foo.A,Foo.B,Foo.C"   — comma-separated globs (commas inside []
                                character classes are part of the class)
      - "@path/to/file.txt"   — file with one pattern per line; '#' starts a comment
    """
    if spec.startswith("@"):
        path = Path(spec[1:])
        if not path.exists():
            raise FileNotFoundError(f"Filter file not found: {path}")
        patterns = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if line:
                patterns.append(line)
        return patterns
    return _split_filter_list(spec)


def _filter_tests(
    tests: list[TestModel],
    pattern: str | None = None,
    package: str | None = None,
) -> list[TestModel]:
    """Filter tests by glob/list/file on model_id, plus optional package prefix."""
    filtered = tests
    if package:
        # review 2026-07-06 finding 65: match the package itself or children
        # across a dot boundary — "MyLib.Fluid" must not also select
        # "MyLib.FluidExperimental.*" (raw startswith did).
        filtered = [
            t
            for t in filtered
            if t.model_id == package or t.model_id.startswith(package + ".")
        ]
    if pattern:
        patterns = _resolve_filter_patterns(pattern)
        # review 2026-07-06 finding 63: fnmatchcase — fnmatch.fnmatch is
        # case-insensitive on Windows, so the same filter selected different
        # tests across the two OS partitions this project maintains.
        filtered = [
            t
            for t in filtered
            if any(fnmatch.fnmatchcase(t.model_id, p) for p in patterns)
        ]
    return filtered


def _find_orphan_manifest_entries(config, all_tests: list) -> dict[str, str]:
    """Return {test_key: model_id} for manifest entries whose model_id is no
    longer in the discovered tests. These usually indicate renamed/removed
    models — they don't break correctness (compare_all skips them) but their
    work dirs and report dirs accumulate on disk.
    """
    manifest_path = config.work_dir / "batch_manifest.json"
    if not manifest_path.exists():
        return {}
    from .simulators import BatchManifest

    manifest = BatchManifest.load(manifest_path)
    discovered = {t.model_id for t in all_tests}
    return {
        tk: entry["model_id"]
        for tk, entry in manifest.manifest.items()
        if entry["model_id"] not in discovered
    }


def _notify_orphans(config, all_tests: list) -> None:
    """One-line notice if orphan manifest entries exist."""
    orphans = _find_orphan_manifest_entries(config, all_tests)
    if orphans:
        print(
            f"Note: {len(orphans)} orphan manifest entries (models no longer in "
            f"discovery). Run 'manifest cleanup --orphans' to list/prune."
        )


def _write_id_mapping(store, config) -> None:
    """Write a ref ID → model ID mapping file to the work directory."""
    active = store.index.active_tests()
    if not active:
        return
    import json

    mapping_path = config.work_dir / "reference_manifest.json"
    config.work_dir.mkdir(parents=True, exist_ok=True)
    mapping = {f"ref_{tid}": active[tid] for tid in sorted(active)}
    mapping_path.write_text(json.dumps(mapping, indent=2) + "\n", encoding="utf-8")


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
        from .simulators import BatchManifest

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


def cmd_discover(args: argparse.Namespace) -> int:
    """Discover and list all test models."""
    config = _build_config(args)
    tests = discover_tests(config)
    tests = _filter_tests(tests, args.filter, args.package)

    if not tests:
        print("No tests found.")
        return 1

    # Print summary table
    print(
        f"{'Model ID':<90} {'Vars':>4}  {'StopTime':>10}  {'Method':<10}  {'Source':<10}"
    )
    print("-" * 130)
    for t in tests:
        stop = f"{t.stop_time:g}"
        print(
            f"{t.model_id:<90} {t.n_vars:>4}  {stop:>10}  {t.method:<10}  {t.source:<10}"
        )

    print(f"\nTotal: {len(tests)} tests")

    total_vars = sum(t.n_vars for t in tests)
    print(f"  Total tracked variables: {total_vars}")

    by_source = {}
    for t in tests:
        by_source[t.source] = by_source.get(t.source, 0) + 1
    for source, count in sorted(by_source.items()):
        print(f"  Source '{source}': {count}")

    return 0


def _get_runner(config, persistent: bool = False):
    """Get the appropriate simulator runner for the configured simulator.

    When *persistent* is True and the backend ships a persistent-worker
    variant (advertised via :meth:`SimulatorRunner.persistent_runner_cls`),
    pre-flight the variant's dependency probe and instantiate it. When the
    probe raises :class:`RuntimeError`, print a unified abort message
    pointing at both fixes (install the dep, or re-run with ``--batch``)
    and exit ``1`` — no silent fallback.

    Backend-agnostic: no string matching on backend names. New persistent
    backends plug in by overriding ``persistent_runner_cls`` + ``preflight``
    on their runner classes.
    """
    from .simulators import get_runner_class

    batch_cls = get_runner_class(config)
    if not persistent:
        return batch_cls(config)
    pcls = batch_cls.persistent_runner_cls()
    if pcls is None:
        # Batch-only backend (FMPy, Python today). Persistent flag is a
        # no-op rather than an error so a single CLI works across mixed
        # backends in a multi-library config.
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


def cmd_run(args: argparse.Namespace) -> int:
    """Run tests and compare/accept results."""
    config = _build_config(args)
    all_tests = discover_tests(config)
    tests = _filter_tests(all_tests, args.filter, args.package)
    _notify_orphans(config, all_tests)

    from .storage.reference_store import ReferenceStore

    store = ReferenceStore(config)
    _write_id_mapping(store, config)

    runner = _get_runner(config, persistent=not getattr(args, "batch", False))

    # --rerun selects tests by status from prior comparisons (no new sim yet).
    # Default category: failed. Implies --merge so the report covers the full suite.
    if getattr(args, "rerun", None) is not None:
        from .comparison.comparator import compare_all

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
            from .reporting.dashboard_render import build_rerun_prefix, render_final

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
    from .reporting.dashboard_render import build_rerun_prefix, render_live
    from .reporting.plot_comparison import open_in_browser

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
        from .comparison.comparator import compare_all

        comparisons = compare_all(
            scope_tests, results, store, config.tolerance, config.default_points
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
        from .comparison.comparator import compare_all

        comparisons = compare_all(
            scope_tests, results, store, config.tolerance, config.default_points
        )

        # Always refresh the unified dashboard before any --report work.
        # Without --report the per-test sidecars don't exist, so the
        # post-comparison columns stay dashed; the live snapshot is
        # frozen at the moment compare_all finished.
        from .reporting.dashboard_render import build_rerun_prefix, render_final

        render_final(config.work_dir, rerun_prefix=build_rerun_prefix(config))

        if args.report:
            return _generate_report_suite(
                comparisons, results, scope_tests, store, config
            )

        return _output_report(comparisons, args)


def _run_cross_backend_chains(tests, runner, config, store) -> None:
    """For each test with ``requested_baselines`` listing a known chain target,
    invoke the chain and store the resulting named baseline (4.B.4).

    Today only ``dymola-via-fmpy`` is recognized. Failures are logged + skipped
    (don't abort the run); chains are best-effort enrichment of baselines.
    Requires Windows + Dymola (with FMI export) for the export step — the chain
    silently no-ops on platforms that don't support it (logs a warning).
    """
    from .simulators.cross_backend import (
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


def cmd_compare(args: argparse.Namespace) -> int:
    """Compare last run results against stored references."""
    config = _build_config(args)
    all_tests = discover_tests(config)
    tests = _filter_tests(all_tests, args.filter, args.package)
    _notify_orphans(config, all_tests)

    from .comparison.comparator import compare_all
    from .storage.reference_store import ReferenceStore

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
        # baselines. read_last_results already forced any test the run
        # recorded as failed/timed_out to success=False (via status.json),
        # so accept_results skips them — a partial/stale .mat can't become a
        # baseline. Terminal, mirroring `run --accept` (finding 30: partial
        # accept exits nonzero).
        stored, skipped = store.accept_results(tests, results)
        print(f"\nAccepted {stored} test baselines to {config.reference_dir}")
        if skipped:
            print(f"Skipped {len(skipped)} test(s):")
            for model_id, reason in skipped:
                print(f"  {model_id}: {reason}")
            return 1
        return 0

    comparisons = compare_all(
        tests, results, store, config.tolerance, config.default_points
    )

    # Always refresh the unified dashboard, even without --report. Without
    # per-test sidecars (no --report run yet), the post-run columns stay
    # dashed and the page reads as a "live" snapshot frozen at the moment
    # comparison finished.
    from .reporting.dashboard_render import build_rerun_prefix, render_final

    render_final(config.work_dir, rerun_prefix=build_rerun_prefix(config))

    if getattr(args, "report", False):
        return _generate_report_suite(comparisons, results, tests, store, config)

    return _output_report(comparisons, args)


def cmd_export(args: argparse.Namespace) -> int:
    """Export reference data."""
    config = _build_config(args)
    from .storage.reference_store import ReferenceStore

    store = ReferenceStore(config)

    # review 2026-07-06 finding 60: --output arrives as a string — coerce to
    # Path (the ReferenceStore export APIs call .parent on it).
    if args.format == "csv":
        out = Path(args.output) if args.output else (config.work_dir / "references.csv")
        store.export_csv(out)
        print(f"Exported to {out}")
    else:
        out = (
            Path(args.output) if args.output else (config.work_dir / "references.json")
        )
        store.export_json(out)
        print(f"Exported to {out}")

    return 0


def cmd_check_openmodelica(args: argparse.Namespace) -> int:
    """Diagnose OMPython availability + the `omc` binary."""
    import shutil as _shutil

    from .simulators.openmodelica.session_loader import describe_om_session

    info = describe_om_session()
    omc_path = _shutil.which("omc")

    print("OpenModelica diagnostic")
    print("=======================")
    print(f"  OMPython installed: {info['ompython_installed']}")
    print(f"  OMPython version:   {info['version'] or '(unknown)'}")
    print(f"  Import ok:          {info['import_ok']}")
    if info["error"]:
        print(f"  Error:              {info['error']}")
    print(f"  omc binary:         {omc_path or '(not on PATH)'}")

    ok = info["import_ok"] and bool(omc_path)
    if ok:
        print("\nOK — persistent-worker OpenModelica is ready.")
        return 0
    print("\nFAIL — fix one of:")
    if not info["import_ok"]:
        print('  - Install OMPython:   uv pip install -e ".[om]"')
    if not omc_path:
        print("  - Install OpenModelica and ensure `omc` is on PATH")
    print(
        "Or run with --batch to use the omc-script fallback (still needs omc on PATH)."
    )
    return 1


def cmd_check_julia(args: argparse.Namespace) -> int:
    """Diagnose the `julia` binary on PATH."""
    import shutil as _shutil
    import subprocess as _subprocess

    julia_path = _shutil.which("julia")
    print("Julia diagnostic")
    print("================")
    print(f"  julia binary: {julia_path or '(not on PATH)'}")
    if not julia_path:
        print(
            "\nFAIL — install Julia from https://julialang.org/downloads/ "
            "(or via `juliaup`)."
        )
        return 1
    try:
        out = _subprocess.run(
            [julia_path, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        print(f"  version:      {out.stdout.strip() or '(no output)'}")
    except Exception as exc:  # noqa: BLE001
        print(f"  version probe failed: {exc}")
        return 1
    print("\nOK — julia is invocable.")
    print(
        "Note: first run on a project pays a multi-minute MTK / OrdinaryDiffEq "
        "JIT compile (subsequent runs hit the cache)."
    )
    return 0


def cmd_check_dymola(args: argparse.Namespace) -> int:
    """Diagnose discovery of Dymola's Python interface."""
    from .simulators.dymola.interface_loader import describe_dymola_interface

    override = (
        Path(args.dymola_interface) if getattr(args, "dymola_interface", None) else None
    )
    info = describe_dymola_interface(override)

    print("Dymola Python interface diagnostic")
    print("==================================")
    print(f"  Archive found:   {info['archive'] or '(none)'}")
    if info["format"]:
        print(f"  Format:          .{info['format']}")
    print(f"  sys.path entry:  {info['sys_path_entry'] or '(none)'}")
    print(f"  import ok:       {info['import_ok']}")
    if info.get("dymola_interface_module"):
        print(f"  Module:          {info['dymola_interface_module']}")
    if info["error"]:
        print(f"  Error:           {info['error']}")
    print()
    print("Searched roots:")
    for r in info["search_roots"]:
        print(f"  - {r}")
    if info["discovered_dirs"]:
        print("Discovered install dirs (newest first):")
        for d in info["discovered_dirs"]:
            print(f"  - {d}")

    if info["import_ok"]:
        print("\nOK — ready to use the Dymola Python interface.")
        return 0
    print("\nFAIL — fix one of:")
    print("  1. Pass --dymola-interface <path>")
    print("  2. Set DYMOLA_INTERFACE_PATH env var")
    print("  3. Add 'dymola_interface_path' to testing.json")
    return 1


def cmd_manifest(args: argparse.Namespace) -> int:
    """Show and manage test references."""
    config = _build_config(args)
    from .storage.reference_store import ReferenceStore

    store = ReferenceStore(config)
    action = args.action

    if action == "show":
        all_tests = store.index.all_tests()
        active = {tid: e for tid, e in all_tests.items() if e["status"] != "obsolete"}
        obsolete = {tid: e for tid, e in all_tests.items() if e["status"] == "obsolete"}
        skipped = {tid: e for tid, e in all_tests.items() if e["status"] == "skip"}

        print(f"References: {config.reference_dir}")
        print(
            f"Active: {len(active)}  Skip: {len(skipped)}  Obsolete: {len(obsolete)}\n"
        )

        if active:
            print(f"{'ID':>6}  {'Status':<10}  {'Model ID'}")
            print("-" * 90)
            for tid in sorted(active):
                entry = active[tid]
                print(f"{tid:>6}  {entry['status']:<10}  {entry['model_id']}")

        if skipped:
            print("\nSkipped:")
            for tid in sorted(skipped):
                print(f"{tid:>6}  {skipped[tid]['model_id']}")

        if obsolete and args.show_obsolete:
            print("\nObsolete:")
            for tid in sorted(obsolete):
                print(f"{tid:>6}  {obsolete[tid]['model_id']}")

        return 0

    elif action == "dump":
        _write_id_mapping(store, config)
        mapping_path = config.work_dir / "reference_manifest.json"
        print(f"Written to {mapping_path}")
        return 0

    elif action == "cleanup":
        if getattr(args, "orphans", False):
            return _cleanup_orphans(config, apply=getattr(args, "apply", False))
        removed = store.cleanup_obsolete()
        if removed:
            print(f"Removed {removed} obsolete reference files.")
        else:
            print("No obsolete references to clean up.")
        return 0

    return 1


def _cleanup_orphans(config, apply: bool) -> int:
    """List or prune manifest entries (and their on-disk dirs) for models
    no longer in discover_tests. Dry-run by default; pass apply=True to delete.
    """
    all_tests = discover_tests(config)
    orphans = _find_orphan_manifest_entries(config, all_tests)
    if not orphans:
        print("No orphan manifest entries.")
        return 0

    report_dir = config.work_dir / "reports"
    print(f"Found {len(orphans)} orphan manifest entries:")
    for tk, model_id in sorted(orphans.items()):
        work = config.work_dir / tk
        # Try both possible report-dir names
        rep_test = report_dir / tk
        rep_ref = None
        from .storage.reference_store import ReferenceStore

        store = ReferenceStore(config)
        ref_id = store.index.get_id(model_id)
        if ref_id:
            rep_ref = report_dir / f"ref_{ref_id}"
        bits = []
        if work.exists():
            bits.append(f"work_dir={tk}/")
        if rep_test.exists():
            bits.append(f"reports/{tk}/")
        if rep_ref and rep_ref.exists():
            bits.append(f"reports/ref_{ref_id}/")
        print(f"  {tk}  {model_id}  [{', '.join(bits) or 'manifest only'}]")

    if not apply:
        print("\nDry run. Re-run with --apply to actually remove.")
        return 0

    import shutil

    from .simulators import BatchManifest

    manifest_path = config.work_dir / "batch_manifest.json"
    manifest = BatchManifest.load(manifest_path)
    n_dirs = 0
    for tk, model_id in orphans.items():
        for candidate in [
            config.work_dir / tk,
            report_dir / tk,
        ]:
            if candidate.exists():
                shutil.rmtree(candidate)
                n_dirs += 1
        from .storage.reference_store import ReferenceStore

        store = ReferenceStore(config)
        ref_id = store.index.get_id(model_id)
        if ref_id:
            rep_ref = report_dir / f"ref_{ref_id}"
            if rep_ref.exists():
                shutil.rmtree(rep_ref)
                n_dirs += 1
        manifest.manifest.pop(tk, None)
    manifest.save()
    print(f"\nRemoved {len(orphans)} manifest entries and {n_dirs} directories.")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    """Add a test to test_spec.json."""
    config = _build_config(args)
    model_id = args.model_id
    variables = args.variables or []

    from .discovery.spec_parser import add_to_test_spec

    # Determine spec file location
    spec_path = config.test_spec_file
    if spec_path is None:
        spec_path = config.reference_root / "test_spec.json"

    added = add_to_test_spec(spec_path, model_id, variables)
    if added:
        var_desc = ", ".join(variables) if variables else "(simulate only)"
        print(f"Added: {model_id}")
        print(f"  Variables: {var_desc}")
        print(f"  Spec file: {spec_path}")
        return 0
    else:
        # Already exists — prompt to overwrite
        try:
            choice = (
                input(f"  {model_id} already exists in spec. Overwrite? [y/n] > ")
                .strip()
                .lower()
            )
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return 1
        if choice == "y":
            add_to_test_spec(spec_path, model_id, variables, overwrite=True)
            print(f"Updated: {model_id}")
            return 0
        else:
            print("Skipped.")
            return 0


def cmd_spec_update(args: argparse.Namespace) -> int:
    """Update test_spec.json from either a legacy flat dict OR an RFC 6902
    patch envelope (6.4.3). Auto-detect by shape: presence of ``"patch"``
    → new path; else legacy ``update_test_comparison``."""
    import json as json_mod

    from .discovery.patch_apply import PatchError
    from .discovery.spec_parser import update_test_comparison

    config = _build_config(args)
    spec_path = config.test_spec_file
    if spec_path is None:
        spec_path = config.reference_root / "test_spec.json"

    json_file = Path(args.json_file)
    if not json_file.exists():
        print(f"File not found: {json_file}")
        return 1

    try:
        update_data = json_mod.loads(json_file.read_text(encoding="utf-8"))
    except (json_mod.JSONDecodeError, OSError) as e:
        print(f"Failed to read {json_file}: {e}")
        return 1

    model_id = update_data.get("model")
    if not model_id:
        print("JSON must contain a 'model' field")
        return 1

    patch = update_data.get("patch")
    if isinstance(patch, list):
        # RFC 6902 path. Mutate in-memory first, then validate metric tree
        # (6.4.4), then write — no partial state on validation failure.
        try:
            _apply_validated_patch(spec_path, model_id, patch, config)
        except PatchError as e:
            print(f"Patch failed: {e}")
            return 1
        print(f"Applied {len(patch)} op(s) to {model_id} → {spec_path}")
        for op in patch:
            print(f"  {op.get('op'):<8} {op.get('path'):<60} {op.get('value', '')}")
        return 0

    # Legacy path: overwrites comparison block. Preserved for a transition
    # cycle; see PHASE_6_PLAN 6.4.3.
    update_test_comparison(spec_path, update_data)
    print(f"Updated comparison settings for {model_id} (legacy dict format)")
    print(f"  Spec file: {spec_path}")
    return 0


def _apply_validated_patch(spec_path, model_id, patch, config):
    """Apply a patch with dry-run + validate → then write. Validator
    enforcement lives in `comparison/validator.py`; we only invoke it
    when the patched entry has a `metrics` tree."""
    from .discovery.patch_apply import apply_patch

    # Stage 0 (review 2026-07-06 finding 41): per-leaf ops against a
    # flat-override entry need the implicit tree materialized first.
    patch = _materialize_implicit_metrics(spec_path, model_id, patch)
    # Stage 1: dry-run apply in memory to check path/whitelist errors.
    mutated = apply_patch(spec_path, model_id, patch, write=False)
    # Stage 2: validate metric tree (if present).
    _validate_entry_metric_tree(mutated, model_id, config)
    # Stage 3: commit.
    apply_patch(spec_path, model_id, patch, write=True)


def _materialize_implicit_metrics(spec_path, model_id, patch):
    """Prepend an op materializing the implicit metrics tree when needed.

    review 2026-07-06 finding 41: the reporter's per-leaf patch ops target
    ``/metrics/children/N/...``, but tests defined via flat
    ``comparison.variable_overrides`` (the primary documented format) have
    no ``metrics`` block — ``patch_apply`` refuses to auto-create an array
    parent, so no scalar edit on a flat-override test could round-trip.
    Materialize the SAME implicit tree the reporter displayed (one leaf
    per tracked variable, params from ``comparison.variable_overrides``,
    wrapped in an AND — see ``synthesize_implicit_tree``) as a leading
    ``add /metrics`` op, then let the per-leaf ops land on it.
    """
    import json as json_mod

    from .comparison.tree_spec import spec_to_raw, synthesize_implicit_tree
    from .discovery.patch_apply import PatchError

    needs_tree = any(
        isinstance(op, dict) and str(op.get("path", "")).startswith("/metrics/")
        for op in patch
    )
    if not needs_tree or not spec_path.exists():
        return patch
    try:
        data = json_mod.loads(spec_path.read_text(encoding="utf-8"))
    except (json_mod.JSONDecodeError, OSError):
        return patch  # let apply_patch surface the real read error
    tests = data.get("tests") if isinstance(data, dict) else None
    entry = next(
        (
            e
            for e in (tests or [])
            if isinstance(e, dict) and e.get("model") == model_id
        ),
        None,
    )
    if entry is None or "metrics" in entry:
        return patch
    variables = entry.get("variables") or []
    if not variables:
        raise PatchError(
            f"cannot materialize an implicit metrics tree for {model_id!r}: "
            "the entry declares no tracked variables to build leaves from"
        )
    overrides = (entry.get("comparison") or {}).get("variable_overrides") or {}
    tree = synthesize_implicit_tree(variables, variable_overrides=overrides)
    print(
        f"Note: materialized implicit metrics tree for {model_id} "
        f"({len(variables)} leaves) — entry had no 'metrics' block"
    )
    return [{"op": "add", "path": "/metrics", "value": spec_to_raw(tree)}, *patch]


def _validate_entry_metric_tree(data, model_id, config):
    """When the patched entry has a `metrics` tree, run the D66 validator.

    Skipped when the entry has no tree (legacy implicit-AND) or when the
    validator's baseline-role lookup can't be built (missing ref store).
    """
    entry = None
    for e in data.get("tests", []):
        if isinstance(e, dict) and e.get("model") == model_id:
            entry = e
            break
    if entry is None or "metrics" not in entry:
        return

    from .comparison.tree_spec import parse_metric_tree
    from .comparison.validator import role_lookup_from_store, validate_tree
    from .discovery.patch_apply import PatchError
    from .storage.reference_store import ReferenceStore

    try:
        tree = parse_metric_tree(entry["metrics"])
    except Exception as e:
        raise PatchError(f"patched metric tree is invalid: {e}") from e

    store = ReferenceStore(config)
    lookup = role_lookup_from_store(store, model_id)
    errors = validate_tree(tree, lookup)
    if errors:
        msg = "; ".join(str(e) for e in errors)
        raise PatchError(f"validator rejected the patched tree: {msg}")


def cmd_companion(args: argparse.Namespace) -> int:
    """Manage plot-only companion references (D66).

    Companions are experimental / analytical / rig data shown as plot
    overlays but never scored against. Four actions:
      add <model> <name> <path> [--format csv|json]
      list [<model>]
      freeze <model> <name>
      remove <model> <name>
    """
    config = _build_config(args)
    from .storage.reference_store import ReferenceStore

    store = ReferenceStore(config)
    action = args.action

    if action == "add":
        try:
            ok = store.add_companion(
                args.model_id,
                args.name,
                path=Path(args.path),
                format=getattr(args, "format", None),
            )
        except (ValueError, FileNotFoundError) as e:
            print(f"Error: {e}")
            return 1
        print(
            f"Registered companion {args.name!r} → {args.path}"
            if ok
            else f"Failed to register companion {args.name!r}"
        )
        return 0 if ok else 1

    if action == "list":
        model_id = getattr(args, "model_id", None)
        if model_id:
            companions = store.get_companions(model_id)
            if not companions:
                print(f"No companions registered for {model_id}")
                return 0
            print(f"Companions for {model_id}:")
            for name, co in companions.items():
                extra = co.path if co.kind == "external" else co.data_file
                print(f"  {name:<30} kind={co.kind:<8} format={co.format:<5} {extra}")
            return 0
        # All models
        total = 0
        for _tid, entry in sorted(store.index.all_tests().items()):
            if entry["status"] == "obsolete":
                continue
            companions = store.get_companions(entry["model_id"])
            if companions:
                print(f"{entry['model_id']}:")
                for name, co in companions.items():
                    extra = co.path if co.kind == "external" else co.data_file
                    print(
                        f"  {name:<30} kind={co.kind:<8} format={co.format:<5} {extra}"
                    )
                total += len(companions)
        if total == 0:
            print("No companions registered.")
        return 0

    if action == "freeze":
        try:
            ok = store.freeze_companion(args.model_id, args.name)
        except FileNotFoundError as e:
            print(f"Error: {e}")
            return 1
        if ok:
            print(f"Frozen companion {args.name!r} for {args.model_id}")
        else:
            print(f"Companion {args.name!r} is already frozen or does not exist")
        return 0

    if action == "remove":
        ok = store.remove_companion(args.model_id, args.name)
        if ok:
            print(f"Removed companion {args.name!r} from {args.model_id}")
        else:
            print(f"Companion {args.name!r} not found on {args.model_id}")
        return 0 if ok else 1

    return 1


def cmd_soft_check(args: argparse.Namespace) -> int:
    """Manage soft_check baselines (advisory cross-checks)."""
    config = _build_config(args)
    from .storage.reference_store import ReferenceStore

    store = ReferenceStore(config)
    action = args.action

    if action == "list":
        model_id = getattr(args, "model_id", None)
        if model_id:
            checks = store.get_soft_checks(model_id)
            if not checks:
                print(f"No soft_checks registered for {model_id}")
                return 0
            print(f"Soft_checks for {model_id}:")
            for name, bl in checks.items():
                origin = (
                    bl.provenance.get("source") or bl.provenance.get("origin") or "-"
                )
                print(f"  {name:<30} n_vars={len(bl.variables):<4} origin={origin}")
            return 0
        total = 0
        for _tid, entry in sorted(store.index.all_tests().items()):
            if entry["status"] == "obsolete":
                continue
            checks = store.get_soft_checks(entry["model_id"])
            if checks:
                print(f"{entry['model_id']}:")
                for name, bl in checks.items():
                    origin = (
                        bl.provenance.get("source")
                        or bl.provenance.get("origin")
                        or "-"
                    )
                    print(f"  {name:<30} n_vars={len(bl.variables):<4} origin={origin}")
                total += len(checks)
        if total == 0:
            print("No soft_checks registered.")
        return 0

    if action == "remove":
        ok = store.remove_soft_check(args.model_id, args.name)
        if ok:
            print(f"Removed soft_check {args.name!r} from {args.model_id}")
        else:
            print(f"Soft_check {args.name!r} not found on {args.model_id}")
        return 0 if ok else 1

    return 1


def cmd_import_baseline(args: argparse.Namespace) -> int:
    """Import another regression system's primary as a soft_check (D66).

    Reads a reference-shape JSON from ``source_path`` (same schema a
    ``ref_NNNN.json`` file uses at its top level) and stores it under
    the local model's soft_checks with the given name.
    """
    import json as json_mod

    config = _build_config(args)
    from .storage.reference_store import ReferenceStore

    store = ReferenceStore(config)
    source = Path(args.source_path)
    if not source.exists():
        print(f"File not found: {source}")
        return 1
    try:
        data = json_mod.loads(source.read_text(encoding="utf-8"))
    except (json_mod.JSONDecodeError, OSError) as e:
        print(f"Failed to read {source}: {e}")
        return 1

    time = data.get("time") or []
    variables = data.get("variables") or []
    if not time or not variables:
        print("Source file missing 'time' or 'variables' — cannot import")
        return 1

    provenance = dict(data.get("provenance") or {})
    provenance.setdefault("origin", "import-baseline")
    provenance.setdefault("imported_from", str(source))

    try:
        ok = store.add_soft_check(
            args.model_id,
            args.name,
            time=time,
            variables=variables,
            diagnostics=data.get("diagnostics"),
            simulation=data.get("simulation"),
            statistics=data.get("statistics"),
            provenance=provenance,
        )
    except (ValueError, FileNotFoundError) as e:
        print(f"Error: {e}")
        return 1
    if ok:
        print(f"Imported {source} → soft_check {args.name!r} on {args.model_id}")
    return 0 if ok else 1


def migrate_baselines_tree(ref_root: Path, apply: bool) -> tuple[int, int]:
    """Walk ``ref_root`` and migrate legacy flat named-baselines to soft_checks.

    Returns ``(files_touched, baselines_moved)``. Prints to stdout.
    Callers not going through the CLI use this directly (cleaner tests).
    """
    import json as json_mod

    print(f"Scanning {ref_root} for legacy flat named-baselines...")
    print(f"Mode: {'APPLY' if apply else 'DRY-RUN — pass --apply to commit'}")
    print()

    total_files = 0
    total_moves = 0
    for ref_file in sorted(ref_root.rglob("ref_*.json")):
        if "/companions/" in str(ref_file) or "/soft_checks/" in str(ref_file):
            continue  # skip files that already live inside the new subdirs
        try:
            data = json_mod.loads(ref_file.read_text(encoding="utf-8"))
        except (json_mod.JSONDecodeError, OSError) as e:
            print(f"  ! {ref_file} — unreadable: {e}")
            continue
        extras = data.get("baselines")
        if not extras or not isinstance(extras, dict):
            continue

        total_files += 1
        ref_stem = ref_file.stem  # "ref_NNNN"
        sc_dir = ref_file.parent / "soft_checks" / ref_stem
        print(f"  {ref_file}")
        for name, entry in extras.items():
            total_moves += 1
            target = sc_dir / f"{name}.json"
            try:
                rel = target.relative_to(ref_root)
            except ValueError:
                rel = target
            print(f"    {name!r} -> {rel}")
            if apply:
                sc_dir.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    json_mod.dumps(entry, indent=2) + "\n", encoding="utf-8"
                )
        if apply:
            # Strip `baselines` from the primary file (leave everything else intact).
            data.pop("baselines", None)
            ref_file.write_text(json_mod.dumps(data, indent=2) + "\n", encoding="utf-8")

    print()
    print(
        f"{total_moves} baseline(s) across {total_files} file(s) "
        f"{'migrated' if apply else 'to migrate'}."
    )
    if not apply and total_moves > 0:
        print("Re-run with --apply to actually move them.")
    return total_files, total_moves


def cmd_export_schema(args: argparse.Namespace) -> int:
    """Emit a JSON-Schema document describing ``test_spec.json`` (6.4.5)."""
    import json as json_mod

    from .reporting.schema_export import build_schema

    schema = build_schema()
    text = json_mod.dumps(schema, indent=2) + "\n"
    out = getattr(args, "output", None)
    if out:
        Path(out).write_text(text, encoding="utf-8")
        print(f"Wrote schema to {out}")
    else:
        sys.stdout.write(text)
    return 0


def cmd_migrate_baselines(args: argparse.Namespace) -> int:
    """CLI wrapper — resolves config.reference_root and delegates."""
    config = _build_config(args)
    ref_root = config.reference_root
    apply = getattr(args, "apply", False)
    if not ref_root.exists():
        print(f"reference_root does not exist: {ref_root}")
        return 1
    migrate_baselines_tree(ref_root, apply)
    return 0


def _get_spec_path(config) -> Path:
    """Get the test_spec.json path, using config or default location."""
    if config.test_spec_file is not None:
        return config.test_spec_file
    return config.reference_root / "test_spec.json"


_VALID_REVIEW_FILTERS = {
    "all",
    "failed",
    "no-baseline",
    "warnings",
    "sim-failed",
    "passed",
}


def _parse_review_filter(filter_str: str) -> set[str]:
    """Parse and validate the review filter string."""
    filters = {f.strip() for f in filter_str.split(",") if f.strip()}
    invalid = filters - _VALID_REVIEW_FILTERS
    if invalid:
        raise argparse.ArgumentTypeError(
            f"Invalid review filter(s): {', '.join(sorted(invalid))}. "
            f"Valid: {', '.join(sorted(_VALID_REVIEW_FILTERS - {'all'}))}"
        )
    if "all" in filters:
        return {"all"}
    return filters


def _review_filter_arg(value: str) -> str:
    """argparse ``type=`` callback for ``-i`` / ``--rerun`` category lists.

    review 2026-07-06 finding 58: validate at parse time so a typo like
    ``-i sim_failed`` aborts in under a second with the valid-categories
    message (argparse exit code 2) — not after the full simulation run.
    """
    _parse_review_filter(value)  # raises argparse.ArgumentTypeError on typos
    return value


def _should_review(comp, filters: set[str]) -> bool:
    """Check if a comparison matches the active review filters."""
    if "all" in filters:
        return True
    if "sim-failed" in filters and not comp.sim_success:
        return True
    if "no-baseline" in filters and not comp.has_reference:
        return True
    # A failing test matches "failed" even without a baseline —
    # baseline-free modes (range / declared events / declared peaks)
    # carry a real verdict (review 2026-07-06, finding 5).
    if "failed" in filters and comp.sim_success and not comp.passed:
        return True
    if "warnings" in filters and comp.warnings:
        return True
    return bool(
        "passed" in filters and comp.sim_success and comp.passed and not comp.warnings
    )


def _interactive_review(
    tests: list[TestModel],
    results: dict,
    comparisons: list,
    store,
    config=None,
    review_filter: str = "all",
) -> int:
    """Interactively review each test and accept/reject results."""
    from .discovery.spec_parser import update_test_variables
    from .simulators import resolve_variable_patterns

    # review 2026-07-06 (found while testing finding 62): the old import
    # `from .simulators.dymola.mat_reader import read_dymola_mat` pointed at
    # a module removed in the simulators/common refactor, so EVERY -i
    # invocation crashed with ModuleNotFoundError before the loop started.
    # The [v] path only needs variable names — use the names-only reader.
    from .simulators.common.mat_reader import list_result_mat_variables

    filters = _parse_review_filter(review_filter)

    test_lookup = {t.model_id: t for t in tests}
    n_accepted = 0
    n_skipped = 0
    n_auto_skipped = 0
    # review 2026-07-06 finding 62: one consistent exit rule — after the
    # review loop ends (completed OR quit), exit 1 iff any test the loop
    # passed over (reviewed, user-skipped, or auto-skipped) is still
    # failing/sim-failed and was not accepted during this session; else 0.
    # Tests never reached before a quit were neither reviewed nor skipped,
    # so they don't count.
    n_unresolved = 0

    def _still_failing(c) -> bool:
        return (not c.sim_success) or not c.passed

    n_total = len(comparisons)

    spec_path = _get_spec_path(config) if config else None

    # ANSI colors
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    filter_desc = f" (filter: {review_filter})" if "all" not in filters else ""
    print(f"\nInteractive review: {n_total} tests{filter_desc}\n")

    for idx, comp in enumerate(comparisons, 1):
        if not _should_review(comp, filters):
            n_auto_skipped += 1
            if _still_failing(comp):
                n_unresolved += 1
            continue
        model_id = comp.model_id

        # Status line
        if not comp.sim_success:
            status = f"{RED}SIM_FAIL{RESET}"
        elif not comp.has_reference:
            status = f"{YELLOW}NO BASELINE{RESET}"
        elif comp.passed:
            status = f"{GREEN}PASS{RESET}"
        else:
            status = f"{RED}FAIL{RESET}"

        warn = ""
        if comp.warnings:
            warn = f" {YELLOW}[{len(comp.warnings)} warning(s)]{RESET}"

        if comp.test_id:
            id_tag = f"[{comp.test_id}] "
        elif not comp.has_reference:
            id_tag = "[new] "
        else:
            id_tag = ""
        print(f"[{idx}/{n_total}] {BOLD}{id_tag}{model_id}{RESET}")
        print(f"  Status: {status}{warn}")

        if comp.error_message and not comp.sim_success:
            print(f"  Error: {comp.error_message}")

        # Brief variable summary
        if comp.variables:
            n_vars_pass = sum(1 for v in comp.variables if v.passed)
            n_vars_total = len(comp.variables)
            if comp.passed:
                print(f"  Variables: {n_vars_pass}/{n_vars_total} passed")
            else:
                print(
                    f"  Variables: {n_vars_pass}/{n_vars_total} passed, {n_vars_total - n_vars_pass} failed"
                )

        # Can't accept if simulation failed
        if not comp.sim_success:
            print("  Skipping (simulation failed)\n")
            n_skipped += 1
            n_unresolved += 1
            continue

        # Prompt
        result = results.get(model_id)
        has_result = result is not None and result.success
        test = test_lookup.get(model_id)
        added_patterns = []  # Track patterns added via [v] this session

        while True:
            if has_result:
                prompt = "  [a]ccept  [v]ariables  [s]kip  [d]etail  [p]lot  [q]uit > "
            else:
                prompt = "  [s]kip  [d]etail  [p]lot  [q]uit > "
            try:
                choice = input(prompt).strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nAborted.")
                return 1

            if choice == "a" and has_result:
                # If variables were added, re-read the .mat to resolve them
                if added_patterns and test and config:
                    test.variable_patterns = list(
                        set(test.variable_patterns + added_patterns)
                    )
                    test_key = _find_test_key(model_id, config)
                    if test_key:
                        runner = _get_runner(config)
                        result = runner.read_result(test, test_key, None)

                stored_ok = False
                store_err = None
                if test and result:
                    try:
                        stored_ok = store.store_reference(test, result)
                    except ValueError as e:
                        # review 2026-07-06 leftover B: store_reference now
                        # rejects non-finite trajectories — one poisoned
                        # result must not crash the review loop.
                        store_err = str(e)
                if stored_ok:
                    n_vars = len(result.variables) if result.variables else 0
                    print(f"  {GREEN}Accepted ({n_vars} variables).{RESET}\n")
                    n_accepted += 1
                else:
                    detail = f": {store_err}" if store_err else "."
                    print(f"  {RED}Failed to store{detail}{RESET}\n")
                    n_skipped += 1
                    if _still_failing(comp):
                        n_unresolved += 1
                break

            elif choice == "v" and has_result:
                try:
                    raw = input("  Variable patterns (comma-separated): ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\nAborted.")
                    return 1
                if raw:
                    new_patterns = [p.strip() for p in raw.split(",") if p.strip()]
                    added_patterns.extend(new_patterns)
                    # Save to test_spec.json
                    if spec_path:
                        update_test_variables(spec_path, model_id, new_patterns)
                        print(
                            f"  Added {len(new_patterns)} pattern(s) to {spec_path.name}"
                        )
                    else:
                        print(
                            f"  Added {len(new_patterns)} pattern(s) (in-memory only, no spec file configured)"
                        )
                    # Show what would match from the .mat
                    if config:
                        test_key = _find_test_key(model_id, config)
                        if test_key:
                            test_dir = config.work_dir / test_key
                            mat_path = test_dir / "dsres.mat"
                            if mat_path.exists():
                                available = list_result_mat_variables(mat_path)
                                if available:
                                    matched = resolve_variable_patterns(
                                        new_patterns, available
                                    )
                                    print(
                                        f"  Matched {len(matched)} variables: {', '.join(matched[:10])}"
                                    )
                                    if len(matched) > 10:
                                        print(f"    ... and {len(matched) - 10} more")

            elif choice == "s":
                print("  Skipped.\n")
                n_skipped += 1
                if _still_failing(comp):
                    n_unresolved += 1
                break

            elif choice == "d":
                _print_detail(comp)

            elif choice == "p":
                if config:
                    _generate_and_open_plots(
                        model_id, comp, result, store, config, test
                    )
                else:
                    print("  No config available for plot generation.")

            elif choice == "q":
                auto = f", {n_auto_skipped} auto-skipped" if n_auto_skipped else ""
                print(f"\nStopped. Accepted {n_accepted}, skipped {n_skipped}{auto}.")
                # review 2026-07-06 finding 62: quit uses the same rule as
                # completion — unresolved failures seen so far decide.
                return 1 if n_unresolved else 0

            else:
                options = "a/v/s/d/p/q" if has_result else "s/d/p/q"
                print(f"  Invalid choice. Enter {options}.")

    auto = f", {n_auto_skipped} auto-skipped" if n_auto_skipped else ""
    print(f"\nDone. Accepted {n_accepted}, skipped {n_skipped}{auto}.")
    # review 2026-07-06 finding 62: see n_unresolved above.
    return 1 if n_unresolved else 0


def _generate_and_open_plots(model_id, comp, result, store, config, test=None) -> None:
    """Generate comparison plots and open in browser."""
    from .reporting.plot_comparison import generate_comparison_plots, open_in_browser

    test_key = _find_test_key(model_id, config)
    if test_key:
        plot_dir = config.work_dir / test_key / "plots"
    else:
        plot_dir = config.work_dir / "plots" / model_id.replace(".", "_")

    ref_data = store.get_reference(model_id)

    test_dir = config.work_dir / test_key if test_key else None

    # Resolve reference file path for clickable link in report
    ref_file = None
    test_id = store.index.get_id(model_id)
    if test_id:
        from .storage.reference_store import RefIndex

        ref_file = store.ref_dir / RefIndex.ref_filename(test_id)

    from .simulators import get_runner_class

    try:
        artifact_files = tuple(get_runner_class(config).artifact_files)
    except ValueError:
        artifact_files = ()

    html_path = generate_comparison_plots(
        model_id=model_id,
        ref_data=ref_data,
        result=result,
        comparisons=comp.variables,
        plot_dir=plot_dir,
        test_dir=test_dir,
        test_model=test,
        ref_file=ref_file,
        warnings=comp.warnings,
        metric_tree=comp.metric_tree,
        artifact_files=artifact_files,
        max_embedded_samples=config.max_embedded_samples,
    )

    if html_path:
        print(f"  Plots saved to {plot_dir}")
        open_in_browser(html_path)
    else:
        print("  Plot generation failed (matplotlib not installed?)")


def _find_test_key(model_id: str, config) -> str | None:
    """Find the test_NNNN key for a model from the batch manifest."""
    from .simulators import BatchManifest

    manifest_paths = sorted(config.work_dir.glob("batch_manifest.json"))
    for mp in manifest_paths:
        bm = BatchManifest.load(mp)
        for tk, entry in bm.manifest.items():
            if entry["model_id"] == model_id:
                return tk
    return None


def _print_detail(comp) -> None:
    """Print detailed variable comparison for interactive review."""
    if comp.error_message:
        print(f"  Error: {comp.error_message}")

    for var in comp.variables:
        name = var.name or f"x[{var.index}]"
        status = "\033[92mPASS\033[0m" if var.passed else "\033[91mFAIL\033[0m"
        print(f"    {status}  {name}")
        if var.is_constant:
            print(f"           RMSE: {var.rmse:.6e} (constant signal)")
        else:
            print(f"           NRMSE: {var.nrmse:.6e} (range: {var.signal_range:.4e})")
        print(
            f"           Max abs error: {var.max_abs_error:.6e} at t={var.max_abs_error_time:g}"
        )
        print(
            f"           Final: ref={var.reference_final:.6e}  act={var.actual_final:.6e}"
        )

    if comp.warnings:
        print("    Structural warnings:")
        for w in comp.warnings:
            print(f"      {w.field}: {w.reference_value} -> {w.current_value}")
    print()


def _generate_report_suite(comparisons, results, tests, store, config) -> int:
    """Generate per-test reports + refresh the unified dashboard.

    generate_report_suite (in plot_comparison) writes per-test
    interactive.html + comparison_data.json sidecars under
    work_dir/reports/. dashboard_render.render_final then reads
    those sidecars and produces the top-level work_dir/dashboard.html.
    """
    from .reporting.dashboard_render import build_rerun_prefix, render_final
    from .reporting.plot_comparison import generate_report_suite, open_in_browser

    generate_report_suite(comparisons, results, tests, store, config)
    render_final(config.work_dir, rerun_prefix=build_rerun_prefix(config))
    dashboard_path = config.work_dir / "dashboard.html"
    print(f"Report: {dashboard_path}")
    open_in_browser(dashboard_path)

    n_failed = sum(1 for c in comparisons if not c.passed)
    return 1 if n_failed else 0


def _output_report(comparisons: list, args: argparse.Namespace) -> int:
    """Route comparisons to the selected report format."""
    fmt = getattr(args, "report_format", "console")
    if fmt == "junit":
        from .reporting.junit_report import generate_junit_report

        out = Path(getattr(args, "output", None) or "test-results.xml")
        generate_junit_report(comparisons, out)
        print(f"JUnit report written to {out}")
        n_failed = sum(1 for c in comparisons if not c.passed)
        return 1 if n_failed else 0
    elif fmt == "html":
        from .reporting.html_report import generate_html_report

        out = Path(getattr(args, "output", None) or "test-report.html")
        generate_html_report(comparisons, out)
        print(f"HTML report written to {out}")
        n_failed = sum(1 for c in comparisons if not c.passed)
        return 1 if n_failed else 0
    else:
        from .reporting.console_report import print_report

        return print_report(comparisons)


def _build_config(args: argparse.Namespace) -> Config:
    """Build Config from parsed CLI arguments."""
    kwargs = {}
    if args.source_path:
        kwargs["source_path"] = Path(args.source_path)
    if hasattr(args, "config") and args.config:
        kwargs["config_file"] = Path(args.config)
    if hasattr(args, "reference_root") and args.reference_root:
        kwargs["reference_root"] = Path(args.reference_root)
    if hasattr(args, "simulator") and args.simulator:
        kwargs["simulator"] = args.simulator
    if hasattr(args, "simulator_path") and args.simulator_path:
        kwargs["simulator_path"] = args.simulator_path
    if hasattr(args, "show_ide") and args.show_ide:
        kwargs["show_ide"] = True
    if hasattr(args, "work_dir") and args.work_dir:
        kwargs["work_dir"] = Path(args.work_dir)
    if hasattr(args, "parallel") and args.parallel:
        kwargs["parallel"] = args.parallel
    if hasattr(args, "batch_size") and args.batch_size is not None:
        kwargs["batch_size"] = args.batch_size
    if hasattr(args, "dymola_interface") and args.dymola_interface:
        kwargs["dymola_interface_path"] = Path(args.dymola_interface)
    if hasattr(args, "tolerance") and args.tolerance:
        kwargs["tolerance"] = args.tolerance
    if hasattr(args, "default_points") and args.default_points:
        kwargs["default_points"] = args.default_points
    if hasattr(args, "timeout") and args.timeout:
        kwargs["timeout"] = args.timeout
    if hasattr(args, "test_spec") and args.test_spec:
        kwargs["test_spec_file"] = Path(args.test_spec)
    return Config(**kwargs)


def build_arg_parser() -> argparse.ArgumentParser:
    """Construct the top-level CLI parser. Pulled out of :func:`main` so
    tests can introspect subcommands and flags without dispatching, and
    so :func:`main` stays a thin parse-and-dispatch shell.
    """
    from . import __version__ as _dstf_version

    parser = argparse.ArgumentParser(
        prog="dstf",
        description="Dynamic Systems Testing Framework — regression & unit testing for time-dependent system behavior",
    )
    parser.add_argument(
        "--version",
        "-V",
        action="version",
        version=f"%(prog)s {_dstf_version}",
        help="Print the installed dstf version and exit.",
    )
    parser.add_argument(
        "--source-path",
        type=str,
        default=None,
        help="Path to the source for the library under test (Modelica package dir containing package.mo, FMU dir, ...). Default: auto-detect from cwd.",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to testing.json config file (default: look near package directory)",
    )
    parser.add_argument(
        "--reference-root",
        type=str,
        default=None,
        help="Path to reference results root (default: <library>/Resources/ReferenceResults)",
    )
    parser.add_argument(
        "--test-spec",
        type=str,
        default=None,
        help="Path to test_spec.json (external test definitions)",
    )
    parser.add_argument(
        "--dymola-interface",
        type=str,
        default=None,
        help="Path to Dymola's Python interface archive (dymola.egg or dymola-*.whl) "
        "or the directory containing it. Overrides auto-discovery.",
    )

    # Parent parsers for flag groups shared across subcommands. argparse
    # merges these via parents=[...] on add_parser; each subcommand inherits
    # only the groups that apply, keeping --help output identical to the
    # pre-refactor inline form while eliminating duplication.
    filter_parent = argparse.ArgumentParser(add_help=False)
    filter_parent.add_argument(
        "--filter",
        type=str,
        help="Glob, comma-separated list, or @file (one pattern per line) — "
        "matches against model_id. Commas inside [] character classes are "
        "part of the class, not separators (e.g. 'Test[A,B]*' is one pattern).",
    )
    filter_parent.add_argument("--package", type=str, help="Filter by package prefix")

    compare_parent = argparse.ArgumentParser(add_help=False)
    compare_parent.add_argument(
        "--tolerance", type=float, help="Override comparison tolerance"
    )
    compare_parent.add_argument(
        "--default-points",
        action="store_true",
        help="Use points mode (with empty points list) as the default for variables without an explicit ``mode`` override.",
    )

    report_parent = argparse.ArgumentParser(add_help=False)
    report_parent.add_argument(
        "--report-format",
        choices=["console", "junit", "html"],
        default="console",
        help="Output format for test report",
    )
    report_parent.add_argument(
        "--report",
        action="store_true",
        help="Render per-test interactive deep-dive reports under "
        "<work_dir>/reports/<test>/. The unified <work_dir>/dashboard.html "
        "always renders regardless of this flag — it just doesn't link to "
        "per-test pages without --report.",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # discover
    subparsers.add_parser(
        "discover",
        parents=[filter_parent],
        help="Discover and list all test models",
    )

    # run
    p_run = subparsers.add_parser(
        "run",
        parents=[filter_parent, compare_parent, report_parent],
        help="Run tests (simulate via the configured backend + compare against references)",
    )
    # review 2026-07-06 finding 61: review-then-accept (-i) and
    # accept-everything (--accept) are mutually exclusive intents — make
    # combining them a hard argparse error instead of silently letting
    # --accept win and skipping the review the user asked for.
    accept_or_review = p_run.add_mutually_exclusive_group()
    accept_or_review.add_argument(
        "-i",
        "--interactive",
        nargs="?",
        const="all",
        default=None,
        metavar="FILTER",
        type=_review_filter_arg,  # review 2026-07-06 finding 58: parse-time check
        help="Interactive review. Optional filter: failed, no-baseline, "
        "warnings, sim-failed, passed (comma-separated). Default: all.",
    )
    accept_or_review.add_argument(
        "--accept",
        action="store_true",
        help="Accept results as new baseline references",
    )
    p_run.add_argument(
        "--simulator", type=str, help="Simulator name (e.g., 'Dymola 2025')"
    )
    p_run.add_argument(
        "--simulator-path",
        type=str,
        help="Absolute path to simulator executable (overrides config)",
    )
    p_run.add_argument(
        "--show-ide",
        action="store_true",
        help="Show Dymola GUI instead of running headless",
    )
    p_run.add_argument("--work-dir", type=str, help="Working directory for output")
    p_run.add_argument(
        "--parallel", type=int, help="Number of parallel Dymola instances"
    )
    p_run.add_argument(
        "--batch-size",
        type=int,
        dest="batch_size",
        help="(--batch only) Tests per Dymola session (default: all-per-worker). Small values (3-5) give better load balancing and crash isolation but reload the library more often.",
    )
    p_run.add_argument(
        "--batch",
        action="store_true",
        help="Use the legacy batched script runner (Dymola .mos / OpenModelica subprocess) instead of the default persistent-worker mode. Falls back to this automatically if the backend's Python interface (Dymola DymolaInterface or OMPython) can't be loaded.",
    )
    p_run.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Per-test timeout in seconds (default: 60)",
    )
    p_run.add_argument(
        "--merge",
        action="store_true",
        help="When used with --filter, expand the read/compare/report scope to "
        "include all tests in the persistent batch manifest (not just the "
        "filtered subset). Lets you rerun a few tests but still see a full "
        "report covering the whole suite.",
    )
    p_run.add_argument(
        "--rerun",
        nargs="?",
        const="failed",
        default=None,
        metavar="CATEGORIES",
        type=_review_filter_arg,  # review 2026-07-06 finding 58: parse-time check
        help="Rerun tests selected by status from the prior run. Categories: "
        "failed, no-baseline, warnings, sim-failed, passed (comma-separated). "
        "Default: failed. Implies --merge.",
    )

    # compare
    p_compare = subparsers.add_parser(
        "compare",
        parents=[filter_parent, compare_parent, report_parent],
        help="Compare last results against references",
    )
    p_compare.add_argument(
        "--simulator",
        type=str,
        help="Simulator name (e.g., 'Dymola 2025'). Affects which "
        "<reference_root>/<Backend>/<os>/ partition is read.",
    )
    p_compare.add_argument(
        "--simulator-path",
        type=str,
        help="Absolute path to simulator executable (overrides config). "
        "Mainly affects which backend identity is used for partition lookup.",
    )
    p_compare.add_argument(
        "--work-dir", type=str, help="Working directory containing prior run outputs"
    )
    p_compare.add_argument(
        "--parallel",
        type=int,
        help="Threads for --report rendering (Plotly serialization + JSON sidecar dump). "
        "Comparison itself is fast; this is the post-comparison report suite. Default: 1.",
    )
    p_compare.add_argument(
        "--accept",
        action="store_true",
        help="Accept the last run's on-disk results as new baselines WITHOUT "
        "re-simulating. Skips tests the run recorded as failed/timed_out.",
    )

    # export
    p_export = subparsers.add_parser(
        "export",
        parents=[filter_parent],
        help="Export reference data",
    )
    p_export.add_argument(
        "--format",
        choices=["json", "csv"],
        default="json",
        help="Export format",
    )
    p_export.add_argument("--output", type=str, help="Output file path")

    # manifest
    p_manifest = subparsers.add_parser("manifest", help="Manage the test manifest")
    p_manifest.add_argument(
        "action",
        choices=["show", "dump", "cleanup"],
        help="'show': display all references and their status. "
        "'dump': write ref ID to model ID mapping to work directory. "
        "'cleanup': remove reference files with status 'obsolete'.",
    )
    p_manifest.add_argument(
        "--show-obsolete",
        action="store_true",
        help="Also show obsolete entries (show only)",
    )
    p_manifest.add_argument(
        "--orphans",
        action="store_true",
        help="cleanup: target orphan batch_manifest entries (models no longer "
        "in discovery) instead of obsolete reference files. Lists by "
        "default; pass --apply to actually remove.",
    )
    p_manifest.add_argument(
        "--apply",
        action="store_true",
        help="cleanup --orphans: actually delete orphan dirs and manifest entries (default is dry-run).",
    )

    # add
    p_add = subparsers.add_parser("add", help="Add a test to test_spec.json")
    p_add.add_argument(
        "model_id",
        type=str,
        help="Fully qualified Modelica model path (e.g., 'MyLib.Examples.Test')",
    )
    p_add.add_argument(
        "--variables",
        nargs="*",
        default=None,
        help="Variable patterns to track (e.g., 'pipe.T*' 'pump.m_flow'). "
        "Omit for simulate-only.",
    )

    # spec-update
    p_spec_update = subparsers.add_parser(
        "spec-update",
        help="Update comparison tolerances in test_spec.json from a JSON file",
    )
    p_spec_update.add_argument(
        "json_file",
        type=str,
        help="Path to JSON file with tolerance settings (e.g., from interactive report export)",
    )

    # companion — manage plot-only overlays (D66 baseline-role split)
    p_companion = subparsers.add_parser(
        "companion",
        help="Manage plot-only companion references (external data shown as overlays)",
    )
    p_companion.add_argument(
        "action",
        choices=["add", "list", "freeze", "remove"],
        help="'add': register an external file pointer. 'list': show companions. "
        "'freeze': copy external data into ref storage. 'remove': deregister.",
    )
    p_companion.add_argument(
        "model_id",
        nargs="?",
        help="Model ID (required for add/freeze/remove; optional for list)",
    )
    p_companion.add_argument(
        "name", nargs="?", help="Companion name (required for add/freeze/remove)"
    )
    p_companion.add_argument(
        "path", nargs="?", help="Path to data file (required for add)"
    )
    p_companion.add_argument(
        "--format",
        choices=["csv", "json"],
        default=None,
        help="Data format (auto-detected from extension if omitted)",
    )

    # soft-check — manage advisory cross-check baselines (D66)
    p_soft = subparsers.add_parser(
        "soft-check",
        help="Manage soft_check baselines (advisory cross-checks; warn-only scoring)",
    )
    p_soft.add_argument(
        "action",
        choices=["list", "remove"],
        help="'list': show soft_checks. 'remove': deregister. "
        "Note: there is no `soft-check add` — by design, soft_checks are "
        "added only via `import-baseline` (D66 baseline-role split: "
        "soft_checks come from another regression system's primary, not "
        "from local user input).",
    )
    p_soft.add_argument("model_id", nargs="?")
    p_soft.add_argument("name", nargs="?")

    # import-baseline — import another system's primary as a soft_check (D66)
    p_import = subparsers.add_parser(
        "import-baseline",
        help="Import another regression system's primary as a soft_check",
    )
    p_import.add_argument(
        "model_id", type=str, help="Local model ID to attach the soft_check to"
    )
    p_import.add_argument(
        "name",
        type=str,
        help="Soft_check name (how MetricTree leaves will reference it)",
    )
    p_import.add_argument(
        "source_path",
        type=str,
        help="Path to source reference JSON (flat ref file shape)",
    )

    # export-schema — emit JSON-Schema for test_spec.json (6.4.5)
    p_schema = subparsers.add_parser(
        "export-schema",
        help="Emit a JSON-Schema describing the test_spec.json shape",
    )
    p_schema.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="Write schema to this file instead of stdout",
    )

    # migrate-baselines — one-off migration from flat named-baselines to soft_checks/
    p_migrate = subparsers.add_parser(
        "migrate-baselines",
        help="One-off migration: move pre-D66 flat named-baselines to soft_checks/",
    )
    p_migrate.add_argument(
        "--apply",
        action="store_true",
        help="Actually perform the moves (default is dry-run)",
    )

    # check-* — backend diagnostic commands (symmetric across Dymola / OM / Julia)
    subparsers.add_parser(
        "check-dymola",
        help="Locate and load Dymola's Python interface; report what was found",
    )
    subparsers.add_parser(
        "check-openmodelica",
        help="Probe OMPython + the `omc` binary; report whether the OM stack is ready",
    )
    subparsers.add_parser(
        "check-julia",
        help="Probe the `julia` binary on PATH; report version and readiness",
    )

    return parser


# Subcommand → handler dispatch table. Lives at module scope so tests can
# assert the registered commands without re-running `main()`. Update both
# the parser (in :func:`build_arg_parser`) and this dict when adding a
# subcommand — symmetry is checked by :data:`_COMMANDS_PARSER_PARITY` test.
_COMMANDS = {
    "discover": cmd_discover,
    "run": cmd_run,
    "compare": cmd_compare,
    "export": cmd_export,
    "manifest": cmd_manifest,
    "add": cmd_add,
    "spec-update": cmd_spec_update,
    "companion": cmd_companion,
    "soft-check": cmd_soft_check,
    "import-baseline": cmd_import_baseline,
    "export-schema": cmd_export_schema,
    "migrate-baselines": cmd_migrate_baselines,
    "check-dymola": cmd_check_dymola,
    "check-openmodelica": cmd_check_openmodelica,
    "check-julia": cmd_check_julia,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 1
    try:
        return _COMMANDS[args.command](args)
    except ConfigError as exc:
        # review 2026-07-06 leftover A: a testing.json problem (malformed,
        # unreadable, missing explicit --config) is a user error — print one
        # clean line and exit 2 (argparse's usage-error code), never a
        # traceback.
        print(f"Config error: {exc}", file=sys.stderr)
        return 2


def main_entry() -> None:
    """Entry point for the console_scripts wrapper."""
    sys.exit(main())
