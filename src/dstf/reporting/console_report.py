"""Console (terminal) report for test comparison results."""

import sys

from ..comparison.comparator import TestComparison
from .status import (
    FAIL,
    NO_REF,
    NOT_RUN,
    SIM_FAIL,
    STATUS_LABELS,
    FailedCriterion,
    advisory_failures,
    advisory_text,
    classify,
    exit_code,
    failed_criteria,
    group_failures,
)

# ANSI color codes
_GREEN = "\033[92m"
_RED = "\033[91m"
_YELLOW = "\033[93m"
_GREY = "\033[90m"
_RESET = "\033[0m"
_BOLD = "\033[1m"


def _color(text: str, code: str) -> str:
    """Wrap text in ANSI color if stdout is a terminal."""
    if sys.stdout.isatty():
        return f"{code}{text}{_RESET}"
    return text


def print_report(comparisons: list[TestComparison]) -> int:
    """Print a console report and return exit code (0 when nothing FAILED or
    failed to simulate — NOT RUN and NO_REF do not fail it — and something
    ran, else 1: ``exit_code``)."""
    if not comparisons:
        print("No comparisons to report.")
        return 1

    n_passed = 0
    n_failed = 0
    n_no_ref = 0
    n_sim_fail = 0
    n_not_run = 0
    failures: list[TestComparison] = []
    warned: list[TestComparison] = []
    advised: list[tuple[TestComparison, list[FailedCriterion]]] = []

    for comp in comparisons:
        # Ordering (SIM_FAIL > FAIL > NO_REF > PASS) and its rationale live
        # in reporting.status — this is one of its call sites, not a copy.
        state = classify(comp)
        if state == NOT_RUN:
            # No result: nothing ran, so nothing failed (D145).
            n_not_run += 1
            status = _color(STATUS_LABELS[NOT_RUN], _GREY)
        elif state == SIM_FAIL:
            n_sim_fail += 1
            status = _color("SIM_FAIL", _RED)
        elif state == FAIL:
            n_failed += 1
            status = _color("FAIL", _RED)
        elif state == NO_REF:
            n_no_ref += 1
            status = _color("NO_REF", _YELLOW)
        else:
            n_passed += 1
            status = _color("PASS", _GREEN)

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
        # A test that passes with a failure under `warn` says so: it read a
        # plain PASS, with no word of the criterion that failed.
        advisory = advisory_failures(comp)
        advisory_tag = ""
        if advisory:
            advised.append((comp, advisory))
            advisory_tag = _color(f" ({advisory_text(len(advisory))})", _YELLOW)
        print(f"  {status}  {id_tag}{comp.model_id}{advisory_tag}{warn_tag}")

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
    if n_not_run:
        print(_color(f"  {STATUS_LABELS[NOT_RUN]}: {n_not_run}", _GREY))
    if advised:
        print(_color(f"  With advisory failures: {len(advised)}", _YELLOW))
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
            for block in failure_blocks(comp):
                for line in block:
                    print(line)
            # A group that fails on its own: the test's reason where no
            # criterion failed.
            for line in group_failures(comp):
                print(f"    {line}")

    # The criteria that failed under a `warn` in the tests that passed
    if advised:
        print()
        print(_color("Advisory Failures (under `warn`: they fail no test):", _BOLD))
        print("-" * 80)
        for comp, advisory in advised:
            id_tag = f"[{comp.test_id}] " if comp.test_id else ""
            print(f"\n  {id_tag}{comp.model_id}")
            for block in _blocks(advisory):
                for line in block:
                    print(line)

    # Print structural warnings
    if warned:
        print()
        print(_color("Structural Warnings:", _BOLD))
        print("-" * 80)
        for comp in warned:
            print(f"\n  {comp.model_id}")
            for w in comp.warnings:
                print(f"    {w.text}")

    return exit_code(comparisons)


def failure_blocks(comp: TestComparison) -> list[list[str]]:
    """One block of lines per criterion of ``comp`` that did not pass
    (:func:`~.status.failed_criteria`, which JUnit's lines are read from
    too), in TREE order: the line that says which criterion it is, the
    criterion in its metric's own words — or why it was not scored — and
    that metric's detail pairs.

    The blocks used to be the same five generic numbers under the
    variable's name alone: a start-up window's nrmse and an advisory
    comparison against a soft check read as one variable failing twice,
    with nothing to tell them apart."""
    return _blocks(failed_criteria(comp))


def _blocks(criteria: list[FailedCriterion]) -> list[list[str]]:
    blocks: list[list[str]] = []
    for criterion in criteria:
        lines = [f"    {criterion.head}"]
        if criterion.reason:
            # A sentinel (config that could not build, no data to compare)
            # has no meaningful numbers — the reason is the whole finding.
            lines.append(f"      {'Not scored:':<20}{criterion.reason}")
        else:
            lines.append(f"      {criterion.sentence}")
            lines += [
                f"      {label + ':':<20}{value}" for label, value in criterion.details
            ]
        blocks.append(lines)
    return blocks
