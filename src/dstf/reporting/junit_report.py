"""JUnit XML report generation for CI integration."""

import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

from ..comparison.comparator import TestComparison


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
        n_failures = sum(
            1 for c in suite_comps if not c.passed and c.sim_success and c.has_reference
        )
        n_errors = sum(1 for c in suite_comps if not c.sim_success)

        suite_elem = ET.SubElement(
            root,
            "testsuite",
            {
                "name": suite_name,
                "tests": str(n_tests),
                "failures": str(n_failures),
                "errors": str(n_errors),
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

            if not comp.sim_success:
                ET.SubElement(
                    tc,
                    "error",
                    {
                        "message": comp.error_message or "Simulation failed",
                        "type": "SimulationError",
                    },
                )
            elif not comp.has_reference:
                # No baseline — not an error, just a skipped test
                ET.SubElement(
                    tc,
                    "skipped",
                    {
                        "message": "No reference baseline stored",
                    },
                )
            elif not comp.passed:
                msgs = []
                for var in comp.variables:
                    if not var.passed:
                        name = var.name or f"x[{var.index}]"
                        msgs.append(
                            f"{name}: "
                            f"NRMSE={var.nrmse:.6e}, "
                            f"max_abs={var.max_abs_error:.6e} at t={var.max_abs_error_time:g}, "
                            f"ref_final={var.reference_final:.6e}, "
                            f"act_final={var.actual_final:.6e}"
                        )
                fail = ET.SubElement(
                    tc,
                    "failure",
                    {
                        "message": f"{len(msgs)} variable(s) exceeded tolerance",
                        "type": "RegressionFailure",
                    },
                )
                fail.text = "\n".join(msgs)

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
