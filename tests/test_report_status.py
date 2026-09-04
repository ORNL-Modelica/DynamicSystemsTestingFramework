"""The shared per-test status classifier (U-7) and the output it pins.

`console_report`, `junit_report`, `html_report` and the report suite each
carried their own copy of the SIM_FAIL > FAIL > NO_REF > PASS ordering,
and the same ordering bug had to be fixed twice across them (review
2026-07-06, findings 5 and 69). `reporting.status` is now the single
copy for the surfaces that can share it.

The unification is meant to be verdict-neutral, so the golden strings
below are the *pre-unification* console text and JUnit XML for a mixed
result set, captured verbatim from the code at 39dfc42 before the hoist.
If a future change to `status.classify` moves a verdict, these fail.
"""

import io
import xml.etree.ElementTree as ET
from contextlib import redirect_stdout
from pathlib import Path

from dstf.comparison.types import (
    StructuralWarning,
    TestComparison,
    VariableComparison,
)
from dstf.reporting.console_report import print_report
from dstf.reporting.junit_report import generate_junit_report
from dstf.reporting.status import (
    FAIL,
    NO_REF,
    PASS,
    SIM_FAIL,
    classify,
    count_by_status,
)


def _var(name="h", *, passed=True, index=0):
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
        is_constant=False,
    )


def _mixed():
    """One test per bucket, plus the two edge cases the findings were about."""
    return [
        TestComparison(
            model_id="Lib.Ex.Pass", passed=True, test_id="0001", variables=[_var()]
        ),
        TestComparison(
            model_id="Lib.Ex.Fail",
            passed=False,
            test_id="0002",
            variables=[_var(passed=False), _var(name="v", index=1)],
        ),
        TestComparison(
            model_id="Lib.Ex.SimFail",
            passed=False,
            sim_success=False,
            test_id="0003",
            error_message="translation failed",
        ),
        TestComparison(model_id="Lib.Ex.NoRef", passed=True, has_reference=False),
        TestComparison(
            model_id="Lib.Ex.WarnOnly",
            passed=True,
            test_id="0005",
            variables=[_var()],
            warnings=[
                StructuralWarning(
                    field="Event count", reference_value="3", current_value="5"
                )
            ],
        ),
        # Finding 5: no baseline but a real verdict — FAIL, never NO_REF.
        TestComparison(
            model_id="Lib.Ex.BaselineFreeFail",
            passed=False,
            has_reference=False,
            variables=[_var(passed=False)],
        ),
    ]


# ---------------------------------------------------------------------------
# classify / count_by_status
# ---------------------------------------------------------------------------


class TestClassify:
    def test_each_bucket(self):
        assert [classify(c) for c in _mixed()] == [
            PASS,
            FAIL,
            SIM_FAIL,
            NO_REF,
            PASS,
            FAIL,
        ]

    def test_sim_fail_outranks_missing_baseline(self):
        comp = TestComparison(
            model_id="M", passed=False, sim_success=False, has_reference=False
        )
        assert classify(comp) == SIM_FAIL

    def test_failing_verdict_outranks_no_ref(self):
        """Finding 5: baseline-free modes carry a verdict with nothing stored."""
        comp = TestComparison(
            model_id="M",
            passed=False,
            has_reference=False,
            variables=[_var(passed=False)],
        )
        assert classify(comp) == FAIL

    def test_no_ref_needs_nothing_evaluated(self):
        evaluated = TestComparison(
            model_id="M", passed=True, has_reference=False, variables=[_var()]
        )
        assert classify(evaluated) == PASS
        unevaluated = TestComparison(model_id="M", passed=True, has_reference=False)
        assert classify(unevaluated) == NO_REF

    def test_warnings_do_not_change_the_verdict(self):
        warned = _mixed()[4]
        assert warned.warnings and classify(warned) == PASS

    def test_counts_are_mutually_exclusive(self):
        """Finding 69: header counts must reconcile with row statuses."""
        counts = count_by_status(_mixed())
        assert counts == {PASS: 2, FAIL: 2, SIM_FAIL: 1, NO_REF: 1}
        assert sum(counts.values()) == len(_mixed())

    def test_missing_buckets_read_zero(self):
        counts = count_by_status([_mixed()[0]])
        assert counts[SIM_FAIL] == 0 and counts[NO_REF] == 0


# ---------------------------------------------------------------------------
# Byte-identity pins (pre-unification output, verbatim)
# ---------------------------------------------------------------------------

_CONSOLE_GOLDEN = """\
  PASS  [0001] Lib.Ex.Pass
  FAIL  [0002] Lib.Ex.Fail
  SIM_FAIL  [0003] Lib.Ex.SimFail
  NO_REF  [new] Lib.Ex.NoRef
  PASS  [0005] Lib.Ex.WarnOnly [WARN]
  FAIL  [new] Lib.Ex.BaselineFreeFail

Results: 2/6 passed
  Failed: 2
  Simulation errors: 1
  No baseline: 1
  Structural warnings: 1

Failure Details:
--------------------------------------------------------------------------------

  [0002] Lib.Ex.Fail
    h:
      NRMSE:              5.000000e-01 (signal range: 2.0000e+00)
      RMSE:               4.000000e-01
      Max abs error:      3.000000e-01 (at t=1.25)
      Reference final:    1.000000e+00
      Actual final:       1.100000e+00

  Lib.Ex.BaselineFreeFail
    h:
      NRMSE:              5.000000e-01 (signal range: 2.0000e+00)
      RMSE:               4.000000e-01
      Max abs error:      3.000000e-01 (at t=1.25)
      Reference final:    1.000000e+00
      Actual final:       1.100000e+00

Structural Warnings:
--------------------------------------------------------------------------------

  Lib.Ex.WarnOnly
    Event count: 3 -> 5
"""

_JUNIT_GOLDEN = """\
<?xml version='1.0' encoding='utf-8'?>
<testsuites tests="6" failures="2" errors="1">
  <testsuite name="Lib.Ex" tests="6" failures="2" errors="1" skipped="1">
    <testcase name="Lib.Ex.Pass" classname="Lib.Ex" />
    <testcase name="Lib.Ex.Fail" classname="Lib.Ex">
      <failure message="1 variable(s) failed" type="RegressionFailure">h: NRMSE=5.000000e-01, max_abs=3.000000e-01 at t=1.25, ref_final=1.000000e+00, act_final=1.100000e+00</failure>
    </testcase>
    <testcase name="Lib.Ex.SimFail" classname="Lib.Ex">
      <error message="translation failed" type="SimulationError" />
    </testcase>
    <testcase name="Lib.Ex.NoRef" classname="Lib.Ex">
      <skipped message="No reference baseline stored" />
    </testcase>
    <testcase name="Lib.Ex.WarnOnly" classname="Lib.Ex" />
    <testcase name="Lib.Ex.BaselineFreeFail" classname="Lib.Ex">
      <failure message="1 variable(s) failed" type="RegressionFailure">h: NRMSE=5.000000e-01, max_abs=3.000000e-01 at t=1.25, ref_final=1.000000e+00, act_final=1.100000e+00</failure>
    </testcase>
  </testsuite>
</testsuites>"""


def test_console_output_unchanged_by_unification(capsys):
    # print_report colors only when stdout is a tty; capsys is not, so the
    # captured text is the plain form the golden was recorded in.
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = print_report(_mixed())
    assert buf.getvalue() == _CONSOLE_GOLDEN
    assert code == 1


def test_junit_output_unchanged_by_unification(tmp_path):
    out = tmp_path / "results.xml"
    generate_junit_report(_mixed(), out)
    assert out.read_text(encoding="utf-8") == _JUNIT_GOLDEN
    # and it is still parseable XML, not just a matching string
    assert ET.parse(out).getroot().get("failures") == "2"


# ---------------------------------------------------------------------------
# simulate-only: one verdict, on every surface (D117 closed)
# ---------------------------------------------------------------------------


def _simulate_only_comparison(passed: bool = True) -> TestComparison:
    """What ``compare_test`` returns for a simulate_only test: no
    variables, no baseline, a metric tree holding the single
    "simulation succeeded" leaf."""
    from dstf.comparison.metric_tree import MetricResult

    return TestComparison(
        model_id="Lib.Ex.SimulateOnly",
        passed=passed,
        test_id="0007",
        variables=[],
        has_reference=False,
        simulate_only=True,
        metric_tree=MetricResult(
            passed=passed,
            score=None,
            label="simulate-only",
            diagnostics={"note": "no comparison performed; simulation succeeded"},
        ),
    )


class TestSimulateOnlyClassification:
    """The report suite used to keep a private copy of this classifier
    purely because simulate_only was readable from the TestModel and not
    from the comparison. ``TestComparison.simulate_only`` closes that gap;
    the copy is gone and all three surfaces answer from here."""

    def test_comparator_stamps_the_flag(self):
        """The flag is set where the verdict is made, not inferred later."""
        import numpy as np

        from dstf.backends.types import TestResult, VariableResult
        from dstf.comparison.comparator import compare_test
        from dstf.discovery.test_registry import TestModel

        t = np.linspace(0, 1, 11)
        comp = compare_test(
            TestModel(
                model_id="Lib.Ex.SimulateOnly",
                source_file=Path(""),
                source_package="Lib.Ex",
                short_name="SimulateOnly",
                n_vars=1,
                simulate_only=True,
            ),
            TestResult(
                model_id="Lib.Ex.SimulateOnly",
                success=True,
                variables=[VariableResult(index=1, time=t, values=t)],
            ),
            {"test_id": "0007"},
        )
        assert comp.simulate_only is True
        assert classify(comp) == PASS

    def test_a_passing_simulate_only_is_pass_not_no_ref(self):
        assert classify(_simulate_only_comparison()) == PASS

    def test_a_failing_simulate_only_is_fail(self):
        assert classify(_simulate_only_comparison(passed=False)) == FAIL

    def test_sim_failure_still_outranks_it(self):
        comp = _simulate_only_comparison(passed=False)
        comp.sim_success = False
        assert classify(comp) == SIM_FAIL

    def test_console_junit_and_report_agree(self, tmp_path):
        """The point of the unification: one simulate-only test, three
        surfaces, one verdict. Before, the report suite said PASS while
        this classifier's callers could reach a different answer for the
        same comparison."""
        from types import SimpleNamespace

        from dstf.reporting.report_suite import _build_per_test_args
        from dstf.reporting.status import classify_with_class

        comp = _simulate_only_comparison()

        # 1. console
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = print_report([comp])
        console = buf.getvalue()

        # 2. junit
        out = tmp_path / "results.xml"
        generate_junit_report([comp], out)
        case = ET.parse(out).getroot().find(".//testcase")

        # 3. per-test report row
        args = _build_per_test_args(
            comp,
            results={},
            test_lookup={},
            store=SimpleNamespace(
                get_reference=lambda _m: None,
                index=SimpleNamespace(get_id=lambda _m: None),
                ref_dir=tmp_path,
            ),
            config=SimpleNamespace(
                work_dir=tmp_path,
                max_embedded_samples=1000,
                reference_root=tmp_path,
            ),
            manifest_meta_by_model={},
        )

        assert "PASS" in console
        assert "NO_REF" not in console
        assert code == 0
        assert list(case) == [], (
            "junit emitted a <skipped>/<failure> child for a passing "
            f"simulate-only test: {ET.tostring(case)!r}"
        )
        assert (args["status_text"], args["status_class"]) == ("PASS", "pass")
        assert classify_with_class(comp) == ("PASS", "pass")

    def test_report_row_no_longer_needs_the_test_model(self):
        """The private copy read ``simulate_only`` off the TestModel, so
        the row's verdict depended on a lookup that can miss. It now comes
        from the comparison — an empty test_lookup changes nothing."""
        from types import SimpleNamespace

        from dstf.reporting.report_suite import _build_per_test_args

        common = dict(
            results={},
            store=SimpleNamespace(
                get_reference=lambda _m: None,
                index=SimpleNamespace(get_id=lambda _m: None),
                ref_dir=Path("."),
            ),
            config=SimpleNamespace(
                work_dir=Path("."), max_embedded_samples=1000, reference_root=Path(".")
            ),
            manifest_meta_by_model={},
        )
        comp = _simulate_only_comparison()
        with_model = _build_per_test_args(
            comp,
            test_lookup={comp.model_id: SimpleNamespace(simulate_only=True)},
            **common,
        )
        without_model = _build_per_test_args(comp, test_lookup={}, **common)
        assert with_model["status_text"] == without_model["status_text"] == "PASS"
