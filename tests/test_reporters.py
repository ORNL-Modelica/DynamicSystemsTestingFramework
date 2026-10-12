"""Tests for the output-formatter reporters (D88).

`junit_report` and `console_report` sat at 0% coverage despite being pure
functions — and the JUnit XML in particular is a contract with external
CI systems, so a silent regression there breaks every downstream
consumer. These tests pin the structural contract of each formatter
across the pass / fail / sim-error / no-baseline / warning branches, plus
the `--report-format` routing that selects between them.
"""

import re
import types
import xml.etree.ElementTree as ET
from pathlib import Path

from dstf.comparison.types import (
    StructuralWarning,
    TestComparison,
    VariableComparison,
)
from dstf.reporting.console_report import print_report
from dstf.reporting.junit_report import generate_junit_report


def _var(name="h", *, passed=True, index=0, is_constant=False):
    """Build a VariableComparison with valid numeric fields."""
    return VariableComparison(
        index=index,
        name=name,
        passed=passed,
        nrmse=0.5 if not passed else 1e-6,
        rmse=0.4,
        signal_range=2.0,
        max_abs_error=0.3,
        max_abs_error_time=1.25,
        reference_final=1.0,
        actual_final=1.1,
        is_constant=is_constant,
    )


def _passing(model_id="Lib.Examples.A"):
    return TestComparison(model_id=model_id, passed=True, variables=[_var()])


def _failing(model_id="Lib.Examples.B"):
    return TestComparison(
        model_id=model_id,
        passed=False,
        variables=[_var(passed=False), _var(name="v", index=1, passed=True)],
    )


def _sim_failed(model_id="Lib.Examples.C"):
    return TestComparison(
        model_id=model_id,
        passed=False,
        sim_success=False,
        error_message="Translation failed: boom",
    )


REFUSED = (
    "the base tolerance must be a number; a lower/upper pair belongs on a criterion"
    ' (the baseline\'s comparison.tolerance is {"lower": 0.1, "upper": 0.2})'
)


def _refused(model_id="Lib.Examples.R"):
    """A test `compare_test` refuses: its reference JSON holds a lower/upper
    pair as the base tolerance. FAIL, with the reason and no variable."""
    import numpy as np

    from conftest import make_test_model
    from dstf.backends.types import TestResult, VariableResult
    from dstf.comparison.comparator import compare_test

    t = np.linspace(0.0, 1.0, 5)
    comp = compare_test(
        make_test_model(model_id, variable_patterns=["h"]),
        TestResult(
            model_id=model_id,
            success=True,
            variables=[VariableResult(index=1, name="h", time=t, values=t)],
        ),
        {
            "test_id": "0007",
            "comparison": {"tolerance": {"lower": 0.1, "upper": 0.2}},
            "variables": [
                {"index": 1, "name": "h", "time": t.tolist(), "values": t.tolist()}
            ],
        },
    )
    assert not comp.passed and comp.variables == []
    return comp


def _no_ref(model_id="Lib.Examples.D"):
    # Mirrors compare_all's NO_REF short-circuit: passed=True, nothing
    # evaluated. (A comp with passed=False now classifies as FAILED even
    # without a baseline — review 2026-07-06, finding 5.)
    return TestComparison(model_id=model_id, passed=True, has_reference=False)


def _not_run(model_id="Lib.Examples.N"):
    """A test with no result — never run, or its run never finished it —
    as the comparator builds it (D145). The store is never consulted."""
    from conftest import make_test_model
    from dstf.comparison.comparator import compare_one

    return compare_one(make_test_model(model_id), None, None)


def _sentinel_var(
    name="h",
    error=("metric 'range' misconfigured: needs at least one of min_value / max_value"),
):
    """A leaf whose metric config could not build (D107).

    The name is the variable's — the figure layer pairs leaves with plots
    by it. Every number is inf/nan, so the reason in
    ``diagnostics["error"]`` is the only informative field and each
    surface must print it beside that name.
    """
    return VariableComparison(
        index=0,
        name=name,
        passed=False,
        nrmse=float("inf"),
        rmse=float("inf"),
        signal_range=0.0,
        max_abs_error=float("inf"),
        max_abs_error_time=0.0,
        reference_final=float("nan"),
        actual_final=float("nan"),
        mode="range",
        diagnostics={"error": error},
    )


def _config_error(model_id="Lib.Examples.F"):
    return TestComparison(
        model_id=model_id,
        passed=False,
        variables=[_sentinel_var()],
    )


def _warned(model_id="Lib.Examples.E"):
    return TestComparison(
        model_id=model_id,
        passed=True,
        variables=[_var()],
        warnings=[StructuralWarning("Event count", "3", "5")],
    )


def _scored(tree: dict) -> TestComparison:
    """A real comparison of one variable, `x`, off its baseline by 0.005,
    scored by `tree`."""
    import numpy as np

    from dstf.backends.types import TestResult, VariableResult
    from dstf.comparison.comparator import compare_test
    from dstf.comparison.tree_spec import parse_metric_tree
    from dstf.discovery.test_registry import TestModel

    t = np.array([0.0, 0.5, 1.0])
    values = np.array([1.0, 2.0, 3.0])
    test = TestModel(
        model_id="Lib.Examples.T",
        source_file=Path(""),
        source_package="Lib.Examples",
        short_name="T",
        n_vars=0,
        source="spec",
        variable_patterns=["x"],
        metric_tree_spec=parse_metric_tree(tree),
    )
    result = TestResult(
        model_id=test.model_id,
        success=True,
        variables=[VariableResult(index=1, name="x", time=t, values=values + 0.005)],
    )
    reference = {
        "test_id": "0001",
        "time": t.tolist(),
        "variables": [{"index": 1, "name": "x", "values": values.tolist()}],
    }
    return compare_test(test, result, reference, 1e-4)


#: A tree no criterion of which fails, that fails: its two passing nrmse
#: leaves (5e-3 off a range of 2: 2.5e-3 each) sum, weighted, over 1e-6.
WEIGHTED_ONLY = {
    "combinator": "and",
    "children": [
        {"metric": "nrmse", "variable": "x", "tolerance": 10.0},
        {
            "combinator": "weighted",
            "weights": [1.0, 1.0],
            "threshold": 1e-6,
            "children": [
                {"metric": "nrmse", "variable": "x", "tolerance": 10.0},
                {"metric": "nrmse", "variable": "x", "tolerance": 10.0},
            ],
        },
    ],
}


def _stated(model_id="Lib.Examples.S"):
    """A warning that states something about this run, beside one that
    reports a change from the baseline."""
    return TestComparison(
        model_id=model_id,
        passed=True,
        variables=[_var()],
        warnings=[
            StructuralWarning(
                "Tracked but not scored by any criterion", statement="unitTests.x[2]"
            ),
            StructuralWarning("Event count", "3", "5"),
        ],
    )


_STATED = "Tracked but not scored by any criterion: unitTests.x[2]"


class TestAStatementIsNotAChange:
    """A structural warning with no baseline side is said as a sentence on
    every surface that prints warnings; only a change reads `ref -> cur`."""

    def test_the_two_forms(self):
        stated, changed = _stated().warnings
        assert (stated.text, stated.is_change) == (_STATED, False)
        assert (changed.text, changed.is_change) == ("Event count: 3 -> 5", True)

    def test_the_console_report(self, capsys):
        print_report([_stated()])
        lines = capsys.readouterr().out.splitlines()
        assert f"    {_STATED}" in lines
        assert "    Event count: 3 -> 5" in lines

    def test_the_interactive_review(self, capsys):
        from dstf.cli.review import _print_detail

        _print_detail(_stated())
        lines = capsys.readouterr().out.splitlines()
        assert f"      {_STATED}" in lines
        assert "      Event count: 3 -> 5" in lines

    def test_the_report_page(self, tmp_path):
        import re

        import numpy as np

        from dstf.backends.types import TestResult, VariableResult
        from dstf.reporting.report_suite import generate_comparison_plots

        t = np.linspace(0.0, 1.0, 5)
        ref = {
            "test_id": "0001",
            "time": t.tolist(),
            "variables": [{"index": 1, "name": "h", "values": t.tolist()}],
        }
        result = TestResult(
            model_id="Lib.Examples.S",
            success=True,
            variables=[VariableResult(index=1, name="h", time=t, values=t)],
        )
        comp = _stated()
        html = generate_comparison_plots(
            comp.model_id,
            ref,
            result,
            comp.variables,
            tmp_path / "plots",
            warnings=comp.warnings,
            plots=[],
        )
        page = Path(html).read_text(encoding="utf-8")
        block = re.search(
            r'<div class="warnings-prominent">(.*?)</div>', page, re.DOTALL
        ).group(1)
        assert "Structural Warnings (2)" in block
        # the statement: one sentence, in no Reference / Current column
        assert f'<p class="warning-statement">{_STATED}</p>' in block
        # the change: its row of three cells, and only it
        assert re.findall(r"<tr><td>(.*?)</td></tr>", block) == [
            "Event count</td><td>3</td><td>5"
        ]


# --------------------------------------------------------------------------
# JUnit XML — the external-contract formatter
# --------------------------------------------------------------------------


class TestJUnitReport:
    def test_writes_valid_xml_with_declaration(self, tmp_path):
        out = tmp_path / "nested" / "junit.xml"
        generate_junit_report([_passing()], out)
        assert out.exists()  # parent dir created
        assert out.read_text().startswith("<?xml")
        ET.parse(out)  # parses without error

    def test_aggregate_counts_on_root(self, tmp_path):
        out = tmp_path / "junit.xml"
        generate_junit_report([_passing(), _failing(), _sim_failed()], out)
        root = ET.parse(out).getroot()
        assert root.tag == "testsuites"
        assert root.get("tests") == "3"
        assert root.get("failures") == "1"  # only the regression failure
        assert root.get("errors") == "1"  # the sim failure

    def test_passing_testcase_has_no_children(self, tmp_path):
        out = tmp_path / "junit.xml"
        generate_junit_report([_passing()], out)
        tc = ET.parse(out).getroot().find(".//testcase")
        assert tc is not None
        assert list(tc) == []

    def test_regression_failure_element_carries_variable_detail(self, tmp_path):
        out = tmp_path / "junit.xml"
        generate_junit_report([_failing()], out)
        failure = ET.parse(out).getroot().find(".//failure")
        assert failure is not None
        assert failure.get("type") == "RegressionFailure"
        # Only the failed variable ('h') is reported, not the passing one.
        assert "h:" in failure.text
        assert "NRMSE=" in failure.text

    def test_an_advisory_failure_is_not_counted_as_a_failure(self, tmp_path):
        """One criterion failed and one comparison under `warn` differed:
        the message counts the one that fails the test, and the body calls
        the other advisory. It read "2 variable(s) failed" — of one
        variable, one of whose two criteria cannot fail anything."""
        comp = _scored(
            {
                "combinator": "and",
                "children": [
                    {"metric": "nrmse", "variable": "x", "tolerance": 1e-9},
                    {
                        "combinator": "warn",
                        "children": [
                            {"metric": "nrmse", "variable": "x", "tolerance": 1e-8}
                        ],
                    },
                    {"metric": "nrmse", "variable": "x", "tolerance": 10.0},
                ],
            }
        )
        assert not comp.passed and [v.passed for v in comp.variables] == [
            False,
            False,
            True,
        ]
        out = tmp_path / "junit.xml"
        generate_junit_report([comp], out)
        failure = ET.parse(out).getroot().find(".//failure")
        assert failure.get("message") == "1 criterion failed, with 1 advisory failure"
        failing, advisory = failure.text.splitlines()
        assert failing.startswith("x: NRMSE=")
        assert advisory.startswith("advisory — x: NRMSE=")

    def test_criteria_in_a_group_under_warn_are_advisory_too(self, tmp_path):
        """`and[fail, warn[and[fail, fail]]]`: the `warn` holds a GROUP, and
        both of its criteria are under the warn — one level down."""
        strict = {"metric": "nrmse", "variable": "x", "tolerance": 1e-9}
        comp = _scored(
            {
                "combinator": "and",
                "children": [
                    strict,
                    {
                        "combinator": "warn",
                        "children": [
                            {"combinator": "and", "children": [strict, strict]}
                        ],
                    },
                ],
            }
        )
        assert [v.passed for v in comp.variables] == [False, False, False]
        out = tmp_path / "junit.xml"
        generate_junit_report([comp], out)
        failure = ET.parse(out).getroot().find(".//failure")
        assert failure.get("message") == "1 criterion failed, with 2 advisory failures"
        assert [
            line.startswith("advisory — ") for line in failure.text.splitlines()
        ] == [
            False,
            True,
            True,
        ]

    def test_the_message_counts_criteria(self, tmp_path):
        """Two failing criteria on one variable are two criteria — and no
        advisory clause where nothing advisory failed."""
        comp = _scored(
            {
                "combinator": "and",
                "children": [
                    {"metric": "nrmse", "variable": "x", "tolerance": 1e-9},
                    {"metric": "nrmse", "variable": "x", "tolerance": 1e-8},
                    {
                        "combinator": "warn",
                        "children": [
                            {"metric": "nrmse", "variable": "x", "tolerance": 10.0}
                        ],
                    },
                ],
            }
        )
        out = tmp_path / "junit.xml"
        generate_junit_report([comp], out)
        failure = ET.parse(out).getroot().find(".//failure")
        assert failure.get("message") == "2 criteria failed"
        assert len(failure.text.splitlines()) == 2
        assert "advisory" not in failure.text

    def test_a_test_that_fails_only_at_a_weighted_group_names_it(self, tmp_path):
        """Every criterion passes and the weighted sum is over its
        threshold: the message read `0 criteria failed` over an empty body."""
        comp = _scored(WEIGHTED_ONLY)
        assert not comp.passed and all(v.passed for v in comp.variables)
        out = tmp_path / "junit.xml"
        generate_junit_report([comp], out)
        failure = ET.parse(out).getroot().find(".//failure")
        message = failure.get("message")
        assert re.fullmatch(
            r"weighted group at metrics\.children\[1\]: weighted sum "
            r"\d\.\d{6}e-\d\d is not below its threshold 1e-06",
            message,
        ), message
        assert failure.text == message

    def test_a_failed_group_is_listed_beside_a_failed_criterion(self, tmp_path):
        tree = {
            "combinator": "and",
            "children": [
                {"metric": "nrmse", "variable": "x", "tolerance": 1e-9},
                WEIGHTED_ONLY["children"][1],
            ],
        }
        out = tmp_path / "junit.xml"
        generate_junit_report([_scored(tree)], out)
        failure = ET.parse(out).getroot().find(".//failure")
        assert failure.get("message") == "1 criterion failed"
        criterion, group = failure.text.splitlines()
        assert criterion.startswith("x: NRMSE=")
        assert group.startswith("weighted group at metrics.children[1]: weighted sum")

    def test_a_group_under_warn_fails_nothing_and_is_not_named(self, tmp_path):
        tree = {
            "combinator": "and",
            "children": [
                {"metric": "nrmse", "variable": "x", "tolerance": 1e-9},
                {"combinator": "warn", "children": [WEIGHTED_ONLY["children"][1]]},
            ],
        }
        out = tmp_path / "junit.xml"
        generate_junit_report([_scored(tree)], out)
        failure = ET.parse(out).getroot().find(".//failure")
        assert failure.get("message") == "1 criterion failed"
        assert "group" not in failure.text

    def test_config_error_failure_names_the_reason(self, tmp_path):
        """A sentinel leaf's reason reaches CI, not a wall of inf/nan."""
        out = tmp_path / "junit.xml"
        generate_junit_report([_config_error()], out)
        failure = ET.parse(out).getroot().find(".//failure")
        assert failure is not None
        assert "h: not scored — " in failure.text
        assert "min_value / max_value" in failure.text
        # The meaningless numeric block is suppressed for this leaf.
        assert "NRMSE=" not in failure.text

    def test_a_refused_test_says_why_in_the_message_and_the_body(self, tmp_path):
        """A FAIL with no variable is one the comparison refused: its reason
        is the whole finding, where the element read "0 variable(s) failed"
        over an empty body."""
        out = tmp_path / "junit.xml"
        generate_junit_report([_refused()], out)
        root = ET.parse(out).getroot()
        assert root.get("failures") == "1" and root.get("errors") == "0"
        failure = root.find(".//failure")
        assert failure is not None
        assert failure.get("type") == "RegressionFailure"
        assert failure.get("message") == REFUSED
        assert failure.text == REFUSED

    def test_a_failing_variable_keeps_the_count_as_the_message(self, tmp_path):
        out = tmp_path / "junit.xml"
        generate_junit_report([_failing()], out)
        failure = ET.parse(out).getroot().find(".//failure")
        assert failure.get("message") == "1 criterion failed"

    def test_sim_failure_becomes_error_element(self, tmp_path):
        out = tmp_path / "junit.xml"
        generate_junit_report([_sim_failed()], out)
        err = ET.parse(out).getroot().find(".//error")
        assert err is not None
        assert err.get("type") == "SimulationError"
        assert "boom" in err.get("message")

    def test_no_reference_becomes_skipped(self, tmp_path):
        out = tmp_path / "junit.xml"
        generate_junit_report([_no_ref()], out)
        root = ET.parse(out).getroot()
        assert root.find(".//skipped") is not None
        # A skip is neither a failure nor an error.
        assert root.get("failures") == "0"
        assert root.get("errors") == "0"

    def test_a_test_not_run_is_skipped_not_run(self, tmp_path):
        """D145: no result is not a failure. The testcase carries
        ``<skipped message="not run"/>`` — never <failure>, never <error>
        — and the suite counts it skipped."""
        out = tmp_path / "junit.xml"
        generate_junit_report([_not_run(), _passing()], out)
        root = ET.parse(out).getroot()
        case = root.find(".//testcase[@name='Lib.Examples.N']")
        assert case is not None
        assert [c.tag for c in case] == ["skipped"]
        assert case.find("skipped").get("message") == "not run"
        assert root.get("failures") == "0" and root.get("errors") == "0"
        assert root.find("testsuite").get("skipped") == "1"

    def test_suites_grouped_by_first_two_model_id_parts(self, tmp_path):
        out = tmp_path / "junit.xml"
        generate_junit_report(
            [_passing("Lib.Examples.A"), _passing("Lib.Other.Z")], out
        )
        names = {s.get("name") for s in ET.parse(out).getroot().findall("testsuite")}
        assert names == {"Lib.Examples", "Lib.Other"}


# --------------------------------------------------------------------------
# Console report
# --------------------------------------------------------------------------


class TestConsoleReport:
    def test_empty_returns_failure(self, capsys):
        assert print_report([]) == 1
        assert "No comparisons" in capsys.readouterr().out

    def test_all_pass_returns_zero(self, capsys):
        assert print_report([_passing(), _passing("Lib.Examples.A2")]) == 0
        assert "Results: 2/2 passed" in capsys.readouterr().out

    def test_regression_failure_returns_one_with_details(self, capsys):
        assert print_report([_failing()]) == 1
        out = capsys.readouterr().out
        assert "Failure Details" in out
        assert "Lib.Examples.B" in out
        assert "NRMSE" in out

    def test_a_test_that_fails_only_at_a_weighted_group_says_why(self, capsys):
        """The test was listed under Failure Details with no reason."""
        assert print_report([_scored(WEIGHTED_ONLY)]) == 1
        details = capsys.readouterr().out.split("Failure Details:")[1]
        assert re.search(
            r"\n    weighted group at metrics\.children\[1\]: weighted sum "
            r"\S+ is not below its threshold 1e-06\n",
            details,
        ), details

    def test_each_failed_criterion_is_a_block_that_says_which_it_is(self, capsys):
        """Two criteria failed on ONE variable — a windowed nrmse, and an
        advisory tube under `warn` — beside a range on it: three blocks, in
        the tree's order, each with its metric, its window if it has one,
        its baseline, and the criterion in that metric's own words. They
        were the same five numbers under `x:` three times."""
        comp = _scored(
            {
                "combinator": "and",
                "children": [
                    {
                        "metric": "nrmse",
                        "variable": "x",
                        "tolerance": 1e-9,
                        "window": {"start": 0.0, "end": 0.6},
                    },
                    {"metric": "nrmse", "variable": "x", "tolerance": 10.0},
                    {
                        "combinator": "warn",
                        "children": [
                            {
                                "metric": "tube",
                                "variable": "x",
                                "tube_width_mode": "band",
                                "tube_abs": 0.001,
                            }
                        ],
                    },
                    {"metric": "range", "variable": "x", "max_value": 2.0},
                ],
            }
        )
        assert print_report([comp]) == 1
        details = capsys.readouterr().out.split("Failure Details:")[1]
        heads = [
            ln for ln in details.splitlines() if ln.startswith("    ") and ln[4] != " "
        ]
        assert heads == [
            "    x: nrmse · window 0–0.6 s · against primary",
            "    advisory — x: tube · against primary",
            "    x: range · against primary",
        ]
        # under each head, the measured value against what it is held to
        said = [ln.strip() for ln in details.splitlines() if "→ FAIL" in ln]
        assert said == [
            "NRMSE 5.000e-03 vs tolerance 1.000e-09 → FAIL",
            "0.0% inside tube → FAIL (requires 100%)",
            "max violation 1.005e+00 → FAIL (requires 0)",
        ]
        # a metric's own pairs, never another's under a generic label
        assert "bounds:             [-inf, 2]" in details
        assert "Reference final" not in details and "Max abs error" not in details

    def test_a_block_names_the_baseline_and_a_one_sided_window(self):
        from dstf.reporting.console_report import _window_text

        assert _window_text({"start": 0.5}) == "window from 0.5 s"
        assert _window_text({"end": 2.0}) == "window to 2 s"
        assert _window_text({"start": 0.5, "end": 2.0}) == "window 0.5–2 s"
        assert _window_text({}) == "" and _window_text(None) == ""

    def test_a_block_against_a_soft_check_says_its_name(self, tmp_path):
        """The evidence run's second block: an advisory comparison against
        the `OpenModelica` soft check, of the same variable."""
        import numpy as np

        from conftest import make_test_model
        from dstf.backends.types import TestResult, VariableResult
        from dstf.comparison.comparator import compare_one
        from dstf.comparison.tree_spec import parse_metric_tree
        from dstf.config import Config
        from dstf.reporting.console_report import failure_blocks
        from dstf.storage.reference_store import ReferenceStore
        from test_session import D, _project

        cfg, work = _project(tmp_path)
        store = ReferenceStore(Config(config_file=cfg, work_dir=work))
        t = np.array([i / 4 for i in range(5)])
        assert store.add_soft_check(
            D,
            "OpenModelica",
            time=t.tolist(),
            variables=[{"index": 1, "name": "x", "values": (t * 2).tolist()}],
        )
        test = make_test_model(D, variable_patterns=["x"])
        test.metric_tree_spec = parse_metric_tree(
            {
                "combinator": "and",
                "children": [
                    {"metric": "nrmse", "variable": "x", "tolerance": 1e-9},
                    {
                        "combinator": "warn",
                        "children": [
                            {
                                "metric": "nrmse",
                                "variable": "x",
                                "against": "OpenModelica",
                            }
                        ],
                    },
                ],
            }
        )
        result = TestResult(
            model_id=D,
            success=True,
            variables=[VariableResult(index=1, name="x", time=t, values=t * 1.001)],
        )
        heads = [block[0] for block in failure_blocks(compare_one(test, result, store))]
        assert heads == [
            "    x: nrmse · against primary",
            "    advisory — x: nrmse · against OpenModelica",
        ]

    def test_config_error_prints_the_reason(self, capsys):
        assert print_report([_config_error()]) == 1
        out = capsys.readouterr().out
        # The leaf is named for its variable; the reason sits beside it.
        assert "h:" in out
        assert "Not scored:" in out
        assert "min_value / max_value" in out

    def test_a_non_finite_scalar_reduction_prints_its_reason(self, capsys):
        """The console reads ``diagnostics["error"]`` first. A scalar leaf
        whose reduction came out non-finite now carries one, so the line
        states the cause instead of falling back to the summary — which
        used to print the variable's own name as the reason."""
        import numpy as np

        from dstf.comparison.modes import ScalarConfig, ScalarMode

        t = np.array([0.0, 1.0])
        vc = ScalarMode(
            ScalarConfig(quantity={"reduce": "max"}, tolerance=1.0)
        ).compare(t, np.array([1.0, 2.0]), t, np.array([1.0, float("nan")]))
        vc.name = "h"
        assert (
            print_report(
                [
                    TestComparison(
                        model_id="Lib.Examples.G", passed=False, variables=[vc]
                    )
                ]
            )
            == 1
        )
        out = capsys.readouterr().out
        assert "h:" in out
        assert "Not scored:" in out
        assert "not finite" in out

    def test_sim_failure_returns_one(self, capsys):
        assert print_report([_sim_failed()]) == 1
        out = capsys.readouterr().out
        assert "Simulation errors: 1" in out

    def test_no_reference_does_not_fail_run(self, capsys):
        # NO_REF is informational — it must not flip the exit code.
        assert print_report([_no_ref()]) == 0
        assert "No baseline: 1" in capsys.readouterr().out

    def test_a_test_not_run_does_not_fail_the_run(self, capsys):
        """D145: NOT RUN + PASS exits 0; the line says NOT RUN."""
        assert print_report([_not_run(), _passing()]) == 0
        out = capsys.readouterr().out
        assert "  NOT RUN  Lib.Examples.N\n" in out
        assert "NOT RUN: 1" in out
        assert "SIM_FAIL" not in out and "Simulation errors" not in out

    def test_a_failure_beside_a_test_not_run_still_fails(self, capsys):
        assert print_report([_not_run(), _failing()]) == 1

    def test_structural_warning_section(self, capsys):
        assert print_report([_warned()]) == 0
        out = capsys.readouterr().out
        assert "Structural Warnings" in out
        assert "Event count: 3 -> 5" in out

    def test_constant_signal_failure_path(self, capsys):
        comp = TestComparison(
            model_id="Lib.Examples.K",
            passed=False,
            variables=[_var(passed=False, is_constant=True)],
        )
        assert print_report([comp]) == 1
        assert "constant signal" in capsys.readouterr().out


# --------------------------------------------------------------------------
# --report-format routing
# --------------------------------------------------------------------------


def _fake_config(work_dir: Path):
    """What the html report file's branch reads off Config."""
    return types.SimpleNamespace(
        work_dir=work_dir,
        config_file=None,
        source_path=None,
        reference_root=None,
        theme="default",
    )


def _output_report(comparisons, args, config, results) -> int:
    """What `run` and `compare` do with a scored suite: show it on every
    surface asked for, then exit by it."""
    from dstf.cli.common import _exit_code
    from dstf.cli.run_cmds import _show_reports

    _show_reports(comparisons, results, [], None, config, args)
    return _exit_code(comparisons)


class TestReportFormatRouting:
    """`--report-format html` renders the dashboard page, not a third surface.

    `html_report.py` was retired: one HTML surface (the dashboard) instead
    of three overlapping ones. These pin the routing — a file appears at
    the requested path, it is the dashboard renderer's output, and the
    exit code still tracks failures.
    """

    def test_html_writes_dashboard_render_to_output_path(self, tmp_path):
        from dstf.reporting.dashboard_render import render_final

        work_dir = tmp_path / "work"
        work_dir.mkdir()
        out = tmp_path / "nested" / "r.html"
        args = types.SimpleNamespace(report_format="html", output=str(out))

        code = _output_report([_passing()], args, _fake_config(work_dir), {})

        assert code == 0
        assert out.exists()
        # Byte-identical to what render_final writes for the same work_dir
        # and comparisons: same renderer, different destination. (A final
        # page lists every test scored, so without them it has no row.)
        render_final(work_dir, comparisons=[_passing()])
        assert out.read_text(encoding="utf-8") == (
            work_dir / "dashboard.html"
        ).read_text(encoding="utf-8")

    def test_html_exit_code_tracks_failures(self, tmp_path):

        work_dir = tmp_path / "work"
        work_dir.mkdir()
        out = tmp_path / "r.html"
        args = types.SimpleNamespace(report_format="html", output=str(out))
        assert (
            _output_report([_passing(), _failing()], args, _fake_config(work_dir), {})
            == 1
        )

    def test_every_route_exits_zero_over_not_run_and_pass(self, tmp_path, capsys):
        """D145: `compare` and `run` exit by what ran — a test NOT RUN is
        not a failure on any route; a FAIL beside it still is."""
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        for fmt in ("console", "junit", "html"):
            args = types.SimpleNamespace(
                report_format=fmt, output=str(tmp_path / f"r.{fmt}")
            )
            config = _fake_config(work_dir)
            assert _output_report([_not_run(), _passing()], args, config, {}) == 0, fmt
            assert _output_report([_not_run(), _failing()], args, config, {}) == 1, fmt

    def test_every_route_exits_one_when_nothing_ran(self, tmp_path, capsys):
        """D145 §5: a compare over an empty or wrong work dir, or after a
        pool whose workers all failed to start, has no comparison or none
        that ran — that is not a green run, on any route."""
        from dstf.reporting.status import exit_code

        work_dir = tmp_path / "work"
        work_dir.mkdir()
        for fmt in ("console", "junit", "html"):
            args = types.SimpleNamespace(
                report_format=fmt, output=str(tmp_path / f"r.{fmt}")
            )
            config = _fake_config(work_dir)
            capsys.readouterr()
            assert _output_report([], args, config, {}) == 1, fmt
            assert _output_report([_not_run(), _not_run("L.M")], args, config, {}) == 1
            # One line each, on stderr, saying why (D145 §5).
            said = [
                line
                for line in capsys.readouterr().err.splitlines()
                if line.startswith("Nothing ran")
            ]
            assert said == [
                "Nothing ran: no test in scope.",
                "Nothing ran: no test in scope has a result (2 NOT RUN) — exit 1.",
            ], fmt
        assert exit_code([]) == 1
        assert exit_code(iter([_not_run()])) == 1
        # An iterator is read once: a FAIL is not lost to the nothing-ran
        # check that read it first.
        assert exit_code(iter([_failing()])) == 1
        assert exit_code([_not_run(), _passing()]) == 0

    def test_junit_route_writes_the_file_and_the_verdict_table(self, tmp_path, capsys):
        out = tmp_path / "r.xml"
        args = types.SimpleNamespace(report_format="junit", output=str(out))
        assert _output_report([_passing()], args, _fake_config(tmp_path), {}) == 0
        assert ET.parse(out).getroot().tag == "testsuites"
        assert "Results: 1/1 passed" in capsys.readouterr().out

    def test_console_route_is_default(self, tmp_path, capsys):
        args = types.SimpleNamespace(report_format="console", output=None)
        assert _output_report([_passing()], args, _fake_config(tmp_path), {}) == 0
        assert "Results: 1/1 passed" in capsys.readouterr().out


def test_unknown_mode_sentinel_is_not_captioned_nrmse():
    """The flat-override unknown-mode sentinel resolved no mode at all.

    ``VariableComparison.mode`` defaults to "nrmse", so this leaf used to
    read "nrmse leaf not scored — unknown comparison mode 'tubee'" and
    named a metric the user never wrote (H4).
    """
    import numpy as np

    from dstf.backends.types import TestResult, VariableResult
    from dstf.comparison.comparator import compare_test
    from dstf.comparison.registry import summarize
    from dstf.discovery.test_registry import TestModel

    time = np.linspace(0.0, 1.0, 5)
    test = TestModel(
        model_id="Test.Model",
        source_file=Path(""),
        source_package="Test",
        short_name="Model",
        n_vars=1,
        variable_patterns=["x"],
        variable_overrides={"x": {"mode": "tubee"}},
    )
    result = TestResult(
        model_id="Test.Model",
        success=True,
        variables=[VariableResult(index=1, name="x", time=time, values=time)],
    )
    reference = {
        "test_id": "0001",
        "variables": [
            {"index": 1, "name": "x", "time": time.tolist(), "values": time.tolist()}
        ],
    }
    comp = compare_test(test, result, reference)
    vc = comp.variables[0]
    assert not vc.passed
    assert vc.diagnostics["config_error"] is True

    summary = summarize(vc)
    _score, criterion = summary.display, summary.criterion
    # The caption names no metric — only the error text does (and it lists
    # the valid mode names, so "nrmse" legitimately appears further in).
    assert criterion.startswith("leaf not scored")
    assert not criterion.startswith("nrmse leaf")
    assert "tubee" in criterion


# =========================================================================
# Folded in from tests/test_review_fixes_verdict.py — regression tests for the
# 2026-07-06 review, Theme 1: verdict integrity (CODE_REVIEW_2026-07-06.md).
#
# Decision encoded here:
#
# * A failing test counts as FAILED on every report surface regardless of
#   ``has_reference`` (baseline-free modes); NO_REF is reserved for tests
#   where no comparison ran at all (``TestComparison.evaluated``).
# =========================================================================


# ---------------------------------------------------------------------------
# Report surfaces: failing baseline-free tests are FAILURES
# ---------------------------------------------------------------------------


def _failing_baseline_free_comp() -> TestComparison:
    from dstf.comparison.metric_tree import MetricResult

    return TestComparison(
        model_id="MyLib.RangeTest",
        passed=False,
        sim_success=True,
        has_reference=False,
        metric_tree=MetricResult(passed=False, score=1.0, label="range"),
    )


def _no_ref_comp() -> TestComparison:
    return TestComparison(
        model_id="MyLib.FreshTest",
        passed=True,
        has_reference=False,
        error_message="No reference baseline stored",
    )


class TestReportClassification:
    def test_console_exit_code_nonzero_for_failing_baseline_free(self, capsys):
        from dstf.reporting.console_report import print_report

        rc = print_report([_failing_baseline_free_comp()])
        assert rc == 1
        out = capsys.readouterr().out
        assert "FAIL" in out

    def test_console_no_ref_still_exit_zero(self, capsys):
        from dstf.reporting.console_report import print_report

        rc = print_report([_no_ref_comp()])
        assert rc == 0
        assert "NO_REF" in capsys.readouterr().out

    def test_junit_failing_baseline_free_is_failure(self, tmp_path):
        import xml.etree.ElementTree as ET

        from dstf.reporting.junit_report import generate_junit_report

        out = tmp_path / "junit.xml"
        generate_junit_report([_failing_baseline_free_comp(), _no_ref_comp()], out)
        root = ET.parse(out).getroot()
        assert root.get("failures") == "1"
        cases = {tc.get("name"): tc for tc in root.iter("testcase")}
        assert cases["MyLib.RangeTest"].find("failure") is not None
        assert cases["MyLib.RangeTest"].find("skipped") is None
        assert cases["MyLib.FreshTest"].find("skipped") is not None

    def test_should_review_failed_matches_baseline_free_failure(self):
        from dstf.cli.common import _should_review

        assert _should_review(_failing_baseline_free_comp(), {"failed"}) is True
        assert _should_review(_no_ref_comp(), {"failed"}) is False


# ---------------------------------------------------------------------------
# The text surfaces read the label and print the name beside it
# ---------------------------------------------------------------------------


def _labelled_failure(model_id="Lib.Examples.U"):
    """A failing UnitTests variable: named by its result file, labelled by
    the expression the model wrote."""
    var = _var(name="unitTests.x[1]", passed=False)
    var.expression = "pipe.T[1]"
    return TestComparison(model_id=model_id, passed=False, variables=[var])


class TestTheTextSurfacesNameBoth:
    def test_the_console_prints_the_label_and_the_name(self, capsys):
        assert print_report([_labelled_failure()]) == 1
        out = capsys.readouterr().out
        assert "pipe.T[1] (unitTests.x[1]):" in out

    def test_the_console_prints_the_name_alone_without_an_expression(self, capsys):
        assert print_report([_failing()]) == 1
        out = capsys.readouterr().out
        assert "h:" in out and "h (" not in out

    def test_junit_prints_the_label_and_the_name(self, tmp_path):
        out = tmp_path / "junit.xml"
        generate_junit_report([_labelled_failure()], out)
        failure = ET.parse(out).getroot().find(".//failure")
        assert failure is not None
        assert "pipe.T[1] (unitTests.x[1]): NRMSE=" in failure.text

    def test_the_review_detail_prints_the_label_and_the_name(self, capsys):
        from dstf.cli.review import _print_detail

        _print_detail(_labelled_failure())
        assert "pipe.T[1] (unitTests.x[1])" in capsys.readouterr().out
