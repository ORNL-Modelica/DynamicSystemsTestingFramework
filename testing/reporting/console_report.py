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

        print(f"  {status}  {comp.model_id}")

        if not comp.passed:
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
        print(_color(f"  No reference: {n_no_ref}", _YELLOW))

    # Print failure details
    if failures:
        print()
        print(_color("Failure Details:", _BOLD))
        print("-" * 80)
        for comp in failures:
            print(f"\n  {comp.model_id}")
            if comp.error_message:
                print(f"    Error: {comp.error_message}")
            for var in comp.variables:
                if not var.passed:
                    _print_var_failure(var)

    return 0 if n_failed == 0 and n_sim_fail == 0 else 1


def _print_var_failure(var: VariableComparison):
    """Print details for a failed variable comparison."""
    expr = var.expression or f"x[{var.index}]"
    print(f"    unitTests.x[{var.index}] ({expr}):")
    print(f"      RMS abs error: {var.abs_error_rms:.6e}")
    print(f"      RMS rel error: {var.rel_error_rms:.6e}")
    print(f"      Max abs error: {var.max_abs_error:.6e}")
    print(f"      Reference final: {var.reference_final:.6e}")
    print(f"      Actual final:    {var.actual_final:.6e}")
