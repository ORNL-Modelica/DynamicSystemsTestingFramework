"""Console (terminal) report for test comparison results."""

import sys

from ..comparison.comparator import TestComparison, VariableComparison

# ANSI color codes
_GREEN = "\033[92m"
_RED = "\033[91m"
_YELLOW = "\033[93m"
_RESET = "\033[0m"
_BOLD = "\033[1m"


def _color(text: str, code: str) -> str:
    """Wrap text in ANSI color if stdout is a terminal."""
    if sys.stdout.isatty():
        return f"{code}{text}{_RESET}"
    return text


def _pass_fail(passed: bool) -> str:
    if passed:
        return _color("PASS", _GREEN)
    return _color("FAIL", _RED)


def print_report(comparisons: list[TestComparison]) -> int:
    """Print a console report and return exit code (0=all passed, 1=failures)."""
    if not comparisons:
        print("No comparisons to report.")
        return 1

    n_passed = 0
    n_failed = 0
    n_no_ref = 0
    n_sim_fail = 0
    failures: list[TestComparison] = []
    warned: list[TestComparison] = []

    for comp in comparisons:
        status = _pass_fail(comp.passed)

        if not comp.sim_success:
            n_sim_fail += 1
            status = _color("SIM_FAIL", _RED)
        elif not comp.has_reference:
            n_no_ref += 1
            status = _color("NO_REF", _YELLOW)
        elif comp.passed:
            n_passed += 1
        else:
            n_failed += 1

        # Show warning indicator if structural changes detected
        warn_tag = ""
        if comp.warnings:
            warn_tag = _color(" [WARN]", _YELLOW)
            warned.append(comp)

        if comp.test_id:
            id_tag = f"[{comp.test_id}] "
        elif not comp.has_reference:
            id_tag = "[new] "
        else:
            id_tag = ""
        print(f"  {status}  {id_tag}{comp.model_id}{warn_tag}")

        if not comp.passed and comp.sim_success:
            failures.append(comp)

    # Summary
    total = len(comparisons)
    print()
    print(_color(f"Results: {n_passed}/{total} passed", _BOLD))
    if n_failed:
        print(_color(f"  Failed: {n_failed}", _RED))
    if n_sim_fail:
        print(_color(f"  Simulation errors: {n_sim_fail}", _RED))
    if n_no_ref:
        print(_color(f"  No baseline: {n_no_ref}", _YELLOW))
    if warned:
        print(_color(f"  Structural warnings: {len(warned)}", _YELLOW))

    # Print failure details
    if failures:
        print()
        print(_color("Failure Details:", _BOLD))
        print("-" * 80)
        for comp in failures:
            id_tag = f"[{comp.test_id}] " if comp.test_id else ""
            print(f"\n  {id_tag}{comp.model_id}")
            if comp.error_message:
                print(f"    Error: {comp.error_message}")
            for var in comp.variables:
                if not var.passed:
                    _print_var_failure(var)

    # Print structural warnings
    if warned:
        print()
        print(_color("Structural Warnings:", _BOLD))
        print("-" * 80)
        for comp in warned:
            print(f"\n  {comp.model_id}")
            for w in comp.warnings:
                print(f"    {w.field}: {w.reference_value} -> {w.current_value}")

    return 0 if n_failed == 0 and n_sim_fail == 0 else 1


def _print_var_failure(var: VariableComparison):
    """Print details for a failed variable comparison."""
    name = var.name or f"x[{var.index}]"
    print(f"    {name}:")
    if var.is_constant:
        print(f"      RMSE:               {var.rmse:.6e} (constant signal)")
    else:
        print(
            f"      NRMSE:              {var.nrmse:.6e} (signal range: {var.signal_range:.4e})"
        )
        print(f"      RMSE:               {var.rmse:.6e}")
    print(
        f"      Max abs error:      {var.max_abs_error:.6e} (at t={var.max_abs_error_time:g})"
    )
    print(f"      Reference final:    {var.reference_final:.6e}")
    print(f"      Actual final:       {var.actual_final:.6e}")
