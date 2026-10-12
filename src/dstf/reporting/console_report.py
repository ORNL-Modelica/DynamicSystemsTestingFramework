"""Console (terminal) report for test comparison results."""

import sys

from ..backends.types import label_with_name
from ..comparison.comparator import TestComparison
from .status import (
    FAIL,
    NO_REF,
    NOT_RUN,
    SIM_FAIL,
    STATUS_LABELS,
    classify,
    exit_code,
    group_failures,
    not_scored_reason,
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
    if n_not_run:
        print(_color(f"  {STATUS_LABELS[NOT_RUN]}: {n_not_run}", _GREY))
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


def _window_text(window: object) -> str:
    """``window 0–0.5 s`` for a leaf scoped to part of the run; ``""`` for
    one that is not."""
    if not isinstance(window, dict):
        return ""
    start, end = window.get("start"), window.get("end")
    if start is None and end is None:
        return ""
    if start is None:
        return f"window to {end:g} s"
    if end is None:
        return f"window from {start:g} s"
    return f"window {start:g}–{end:g} s"


def failure_blocks(comp: TestComparison) -> list[list[str]]:
    """One block of lines per criterion of ``comp`` that did not pass, in
    TREE order — two criteria on one variable are two blocks, and each says
    which it is: its metric, its window if it has one, the baseline it is
    scored against, then the criterion in the metric's own words (the
    measured value against its tolerance or bound — ``registry.summarize``,
    what the report's tree row reads) and that metric's detail pairs. A
    criterion under a ``warn`` cannot fail the test: its block starts
    ``advisory — ``.

    The blocks used to be the same five generic numbers under the
    variable's name alone: a start-up window's nrmse and an advisory
    comparison against a soft check read as one variable failing twice,
    with nothing to tell them apart."""
    from ..comparison.registry import summarize
    from ..comparison.tree_eval import (
        advisory_comparisons,
        flatten_evaluation,
        leaf_comparisons_by_path,
    )

    tree = comp.metric_tree
    if tree is not None:
        said = flatten_evaluation(tree)
        leaves = [
            (vc, said.get(path, {}))
            for path, vc in leaf_comparisons_by_path(tree).items()
        ]
    else:
        leaves = [(vc, {}) for vc in comp.variables]
    advisory = {id(vc) for vc in advisory_comparisons(tree)}
    blocks: list[list[str]] = []
    for vc, leaf in leaves:
        if vc.passed:
            continue
        # `pipe.T[1] (unitTests.x[1])`, the name alone when they agree: the
        # expression says WHICH quantity, the name what to write a
        # criterion or an override against.
        name = label_with_name(vc.name, vc.expression)
        what = [vc.mode or str(leaf.get("mode") or "nrmse")]
        window = _window_text(leaf.get("window"))
        if window:
            what.append(window)
        if leaf.get("against"):
            what.append(f"against {leaf['against']}")
        lead = "advisory — " if id(vc) in advisory else ""
        lines = [f"    {lead}{name}: {' · '.join(what)}"]
        reason = not_scored_reason(vc)
        if reason:
            # A sentinel (config that could not build, no data to compare)
            # has no meaningful numbers — the reason is the whole finding.
            lines.append(f"      {'Not scored:':<20}{reason}")
        else:
            summary = summarize(vc)
            lines.append(f"      {summary.criterion}")
            lines += [
                f"      {label + ':':<20}{value}" for label, value in summary.details
            ]
        blocks.append(lines)
    return blocks
