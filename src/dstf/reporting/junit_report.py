"""JUnit XML report generation for CI integration."""

import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

from ..comparison.comparator import TestComparison
from .status import (
    FAIL,
    NO_REF,
    NOT_RUN,
    SIM_FAIL,
    advisory_failures,
    advisory_text,
    classify,
    count_by_status,
    failed_criteria,
    group_failures,
    refusal,
)


def _failure_message(failed: list[str], groups: list[str], advisory: list[str]) -> str:
    """`1 criterion failed`, and `, with 1 advisory failure` when some
    comparison under a `warn` failed beside it. With no failing criterion
    the test failed at a group, and the message is that group's line (`2
    groups failed` for more than one; the body has each)."""
    n = len(failed)
    if n == 0 and groups:
        text = groups[0] if len(groups) == 1 else f"{len(groups)} groups failed"
    else:
        text = f"{n} {'criterion' if n == 1 else 'criteria'} failed"
    if advisory:
        text += f", with {advisory_text(len(advisory))}"
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
                # One line per criterion that did not pass, in the words
                # of the console's block (`failed_criteria`): which
                # criterion it is — two on one variable are two of them —
                # and the measured value against what it is held to, or
                # why it was not scored. A failure under `warn` cannot
                # fail the test: it is not counted with the ones that do,
                # and its line says so.
                criteria = failed_criteria(comp)
                failed = [c.line for c in criteria if not c.advisory]
                advisory = [c.line for c in criteria if c.advisory]
                # A test the comparison refused has no failing criterion to
                # count: its reason is the message and the body.
                refused = refusal(comp)
                # A group that fails on its own — a weighted sum over its
                # threshold — fails the test with no criterion to list.
                groups = group_failures(comp)
                fail = ET.SubElement(
                    tc,
                    "failure",
                    {
                        "message": refused
                        or _failure_message(failed, groups, advisory),
                        "type": "RegressionFailure",
                    },
                )
                fail.text = refused or "\n".join(failed + groups + advisory)
            else:
                # PASS — and it may have passed with a failure under `warn`:
                # no <failure>, counted in nothing, and said, where it used
                # to say nothing at all.
                passed_with = advisory_failures(comp)
                if passed_with:
                    n = len(passed_with)
                    they = "it" if n == 1 else "they"
                    said = ET.SubElement(tc, "system-out")
                    said.text = "\n".join(
                        [
                            f"{advisory_text(n)} (under `warn`: {they} cannot "
                            "fail the test)"
                        ]
                        + [c.line for c in passed_with]
                    )

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
