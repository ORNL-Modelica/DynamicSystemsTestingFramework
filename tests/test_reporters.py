"""Tests for the three output-formatter reporters (D88).

`junit_report`, `console_report`, and `html_report` sat at 0% coverage
despite being pure functions — and the JUnit XML in particular is a
contract with external CI systems, so a silent regression there breaks
every downstream consumer. These tests pin the structural contract of
each formatter across the pass / fail / sim-error / no-baseline / warning
branches.
"""

import xml.etree.ElementTree as ET

from dstf.comparison.types import (
    StructuralWarning,
    TestComparison,
    VariableComparison,
)
from dstf.reporting.console_report import print_report
from dstf.reporting.html_report import generate_html_report
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
    return TestComparison(model_id=model_id, passed=False, has_reference=False)


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
# HTML report
# --------------------------------------------------------------------------


class TestHtmlReport:
    def test_title_uses_dstf_name(self, tmp_path):
        # Title must track the DSTF rename (D81), not the legacy "Modelica".
        out = tmp_path / "report.html"
        generate_html_report([_passing()], out)
        page = out.read_text()
        assert "<title>DSTF Test Report</title>" in page
        assert "Modelica" not in page

    def test_writes_file_with_summary_counts(self, tmp_path):
        out = tmp_path / "nested" / "report.html"
        generate_html_report([_passing(), _failing(), _no_ref(), _warned()], out)
        assert out.exists()
        page = out.read_text()
        assert page.startswith("<!DOCTYPE html>")
        # summary: 2 passed (passing + warned), 2 failed, 1 no-ref, 1 warning
        assert "<strong>2</strong> passed" in page
        assert "<strong>1</strong> no baseline" in page

    def test_status_spans_present(self, tmp_path):
        out = tmp_path / "report.html"
        generate_html_report([_passing(), _failing(), _sim_failed(), _no_ref()], out)
        page = out.read_text()
        assert ">PASS<" in page
        assert ">FAIL<" in page
        assert ">SIM_FAIL<" in page
        assert ">NO_REF<" in page

    def test_failing_variable_detail_row(self, tmp_path):
        out = tmp_path / "report.html"
        generate_html_report([_failing()], out)
        page = out.read_text()
        assert '<table class="details">' in page
        assert "<td>h</td>" in page  # the failed variable

    def test_model_id_is_html_escaped(self, tmp_path):
        out = tmp_path / "report.html"
        generate_html_report([_passing("Lib.<script>.X")], out)
        page = out.read_text()
        assert "&lt;script&gt;" in page
        assert "<script>" not in page

    def test_error_message_escaped_for_sim_failure(self, tmp_path):
        comp = TestComparison(
            model_id="Lib.Examples.C",
            passed=False,
            sim_success=False,
            error_message="failed <b>hard</b>",
        )
        out = tmp_path / "report.html"
        generate_html_report([comp], out)
        page = out.read_text()
        assert "&lt;b&gt;hard&lt;/b&gt;" in page

    def test_structural_warning_table(self, tmp_path):
        out = tmp_path / "report.html"
        generate_html_report([_warned()], out)
        page = out.read_text()
        assert '<table class="warnings">' in page
        assert "<td>Event count</td>" in page
