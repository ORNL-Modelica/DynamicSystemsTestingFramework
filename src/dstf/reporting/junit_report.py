"""JUnit XML report generation for CI integration."""

import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

from ..backends.types import label_with_name
from ..comparison.comparator import TestComparison, VariableComparison
from ..comparison.tree_eval import advisory_comparisons
from .status import (
    FAIL,
    NO_REF,
    NOT_RUN,
    SIM_FAIL,
    classify,
    count_by_status,
    not_scored_reason,
    refusal,
)


def _failure_line(var: VariableComparison) -> str:
    """One failing criterion, for a reader with no plot beside it: the
    expression names the quantity, the name is what a criterion or a
    --filter is written against."""
    name = label_with_name(var.name, var.expression)
    reason = not_scored_reason(var)
    if reason:
        # Sentinel leaf: the numbers are all inf/nan, so CI sees the
        # variable's name and the reason instead of a wall of them.
        return f"{name}: not scored — {reason}"
    return (
        f"{name}: "
        f"NRMSE={var.nrmse:.6e}, "
        f"max_abs={var.max_abs_error:.6e} at t={var.max_abs_error_time:g}, "
        f"ref_final={var.reference_final:.6e}, "
        f"act_final={var.actual_final:.6e}"
    )


def _failure_message(failed: list[str], advisory: list[str]) -> str:
    """`1 criterion failed`, and `, with 1 advisory failure` when some
    comparison under a `warn` failed beside it."""
    n = len(failed)
    text = f"{n} {'criterion' if n == 1 else 'criteria'} failed"
    if advisory:
        m = len(advisory)
        text += f", with {m} advisory failure{'' if m == 1 else 's'}"
    return text


def generate_junit_report(
    comparisons: list[TestComparison],
    output_path: Path,
) -> None:
    """Generate a JUnit XML report from test comparisons."""
    suites: dict[str, list[TestComparison]] = defaultdict(list)
    for comp in comparisons:
        parts = comp.model_id.split(".")
        suite_name = ".".join(parts[:2]) if len(parts) >= 2 else parts[0]
        suites[suite_name].append(comp)

    root = ET.Element("testsuites")
    total_tests = 0
    total_failures = 0
    total_errors = 0

    for suite_name in sorted(suites.keys()):
        suite_comps = suites[suite_name]
        n_tests = len(suite_comps)
        # Buckets are the shared ones from reporting.status, so the JUnit
        # counters can't drift from the console's — review 2026-07-06,
        # finding 5: failing baseline-free tests were emitted as <skipped>
        # with failures="0".
        counts = count_by_status(suite_comps)
        n_failures = counts[FAIL]
        n_errors = counts[SIM_FAIL]
        n_skipped = counts[NO_REF] + counts[NOT_RUN]

        suite_elem = ET.SubElement(
            root,
            "testsuite",
            {
                "name": suite_name,
                "tests": str(n_tests),
                "failures": str(n_failures),
                "errors": str(n_errors),
                "skipped": str(n_skipped),
            },
        )

        for comp in suite_comps:
            tc = ET.SubElement(
                suite_elem,
                "testcase",
                {
                    "name": comp.model_id,
                    "classname": suite_name,
                },
            )

            state = classify(comp)
            if state == NOT_RUN:
                # No result: the test was never run, or its run never
                # finished it. Not a failure and not an error (D145).
                ET.SubElement(tc, "skipped", {"message": "not run"})
            elif state == SIM_FAIL:
                ET.SubElement(
                    tc,
                    "error",
                    {
                        "message": comp.error_message or "Simulation failed",
                        "type": "SimulationError",
                    },
                )
            elif state == NO_REF:
                # No baseline AND nothing evaluated — a genuinely skipped
                # test. Failing baseline-free tests classify as FAIL and
                # land in the <failure> branch below.
                ET.SubElement(
                    tc,
                    "skipped",
                    {
                        "message": "No reference baseline stored",
                    },
                )
            elif state == FAIL:
                # A failure under `warn` cannot fail the test: it is not
                # counted with the ones that do, and its line says so.
                # Each entry of `comp.variables` is one CRITERION — two
                # leaves on one variable are two of them.
                advisory_ids = {id(v) for v in advisory_comparisons(comp.metric_tree)}
                failed: list[str] = []
                advisory: list[str] = []
                for var in comp.variables:
                    if var.passed:
                        continue
                    if id(var) in advisory_ids:
                        advisory.append(f"advisory — {_failure_line(var)}")
                    else:
                        failed.append(_failure_line(var))
                # A test the comparison refused has no failing criterion to
                # count: its reason is the message and the body.
                refused = refusal(comp)
                fail = ET.SubElement(
                    tc,
                    "failure",
                    {
                        "message": refused or _failure_message(failed, advisory),
                        "type": "RegressionFailure",
                    },
                )
                fail.text = refused or "\n".join(failed + advisory)

        total_tests += n_tests
        total_failures += n_failures
        total_errors += n_errors

    root.set("tests", str(total_tests))
    root.set("failures", str(total_failures))
    root.set("errors", str(total_errors))

    tree = ET.ElementTree(root)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(tree, space="  ")
    tree.write(str(output_path), encoding="unicode", xml_declaration=True)
