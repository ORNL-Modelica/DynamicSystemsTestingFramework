"""Tests for the output-formatter reporters (D88).

`junit_report` and `console_report` sat at 0% coverage despite being pure
functions — and the JUnit XML in particular is a contract with external
CI systems, so a silent regression there breaks every downstream
consumer. These tests pin the structural contract of each formatter
across the pass / fail / sim-error / no-baseline / warning branches, plus
the `--report-format` routing that selects between them.
"""

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


def _no_ref(model_id="Lib.Examples.D"):
    # Mirrors compare_all's NO_REF short-circuit: passed=True, nothing
    # evaluated. (A comp with passed=False now classifies as FAILED even
    # without a baseline — review 2026-07-06, finding 5.)
    return TestComparison(model_id=model_id, passed=True, has_reference=False)


def _sentinel_var(
    name="h",
    error=(
        "metric 'range' misconfigured: RangeConfig requires at least one of "
        "min_value / max_value"
    ),
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
    """The two attributes `_output_report`'s html branch reads off Config."""
    return types.SimpleNamespace(
        work_dir=work_dir, config_file=None, source_path=None, reference_root=None
    )


class TestReportFormatRouting:
    """`--report-format html` renders the dashboard page, not a third surface.

    `html_report.py` was retired: one HTML surface (the dashboard) instead
    of three overlapping ones. These pin the routing — a file appears at
    the requested path, it is the dashboard renderer's output, and the
    exit code still tracks failures.
    """

    def test_html_writes_dashboard_render_to_output_path(self, tmp_path):
        from dstf.cli.run_cmds import _output_report
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
        from dstf.cli.run_cmds import _output_report

        work_dir = tmp_path / "work"
        work_dir.mkdir()
        out = tmp_path / "r.html"
        args = types.SimpleNamespace(report_format="html", output=str(out))
        assert (
            _output_report([_passing(), _failing()], args, _fake_config(work_dir), {})
            == 1
        )

    def test_junit_route_unchanged(self, tmp_path):
        from dstf.cli.run_cmds import _output_report

        out = tmp_path / "r.xml"
        args = types.SimpleNamespace(report_format="junit", output=str(out))
        assert _output_report([_passing()], args, _fake_config(tmp_path), {}) == 0
        assert ET.parse(out).getroot().tag == "testsuites"

    def test_console_route_is_default(self, tmp_path, capsys):
        from dstf.cli.run_cmds import _output_report

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
