"""The interactive review loop (`run -i`): accept / skip / detail / plot."""

from pathlib import Path

from ..discovery.test_registry import TestModel
from .common import _parse_review_filter, _should_review


def _get_spec_path(config) -> Path:
    """Get the test_spec.json path, using config or default location."""
    if config.test_spec_file is not None:
        return config.test_spec_file
    return config.reference_root / "test_spec.json"


def _interactive_review(
    tests: list[TestModel],
    results: dict,
    comparisons: list,
    store,
    config=None,
    review_filter: str = "all",
) -> int:
    """Interactively review each test and accept/reject results."""
    from ..backends import resolve_variable_patterns

    # review 2026-07-06 (found while testing finding 62): the old import
    # `from .backends.dymola.mat_reader import read_dymola_mat` pointed at
    # a module removed in the backends/common refactor, so EVERY -i
    # invocation crashed with ModuleNotFoundError before the loop started.
    # The [v] path only needs variable names — use the names-only reader.
    from ..backends.common.mat_reader import list_result_mat_variables
    from ..discovery.spec_parser import update_test_variables

    # Deferred: run_cmds imports this module at module scope, so the
    # dependency back on its backend selection has to resolve at call time.
    from .run_cmds import _get_backend

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
                        backend = _get_backend(config)
                        result = backend.read_result(test, test_key, None)

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
    from ..reporting.report_suite import generate_comparison_plots, open_in_browser

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
        from ..storage.ref_index import RefIndex

        ref_file = store.ref_dir / RefIndex.ref_filename(test_id)

    from ..backends import get_backend_class

    try:
        artifact_files = tuple(get_backend_class(config).artifact_files)
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
        print("  Plot generation failed")


def _find_test_key(model_id: str, config) -> str | None:
    """Find the test_NNNN key for a model from the batch manifest."""
    from ..backends import BatchManifest

    mp = config.work_dir / "batch_manifest.json"
    if not mp.exists():
        return None
    bm = BatchManifest.load(mp)
    for tk, entry in bm.manifest.items():
        if entry["model_id"] == model_id:
            return tk
    return None


def _print_detail(comp) -> None:
    """Print each leaf's mode-aware verdict for interactive review."""
    from ..comparison.registry import summarize

    if comp.error_message:
        print(f"  Error: {comp.error_message}")

    for var in comp.variables:
        name = var.name or f"x[{var.index}]"
        status = "\033[92mPASS\033[0m" if var.passed else "\033[91mFAIL\033[0m"
        summary = summarize(var)
        # The flat-route unknown-mode sentinel carries mode="" on purpose
        # (it must not be captioned "nrmse"); print no bracket for it.
        mode = f"[{var.mode}] " if var.mode else ""
        print(f"    {status}  {name}  {mode}{summary.display}")
        print(f"           {summary.criterion}")

    if comp.warnings:
        print("    Structural warnings:")
        for w in comp.warnings:
            print(f"      {w.field}: {w.reference_value} -> {w.current_value}")
    print()
