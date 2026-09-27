"""The row model both HTML surfaces render (``reporting.rows``).

One definition of a test's row — its fields, its columns, their text and
their sort values — so the static dashboard and the tool's Suite table
cannot show one test two ways. ``summary_fields`` is the one computation
of the numbers the per-test sidecar carries.
"""

import json

import pytest

from dstf.comparison.types import TestComparison, VariableComparison
from dstf.reporting.report_suite import generate_comparison_plots
from dstf.reporting.rows import (
    COLUMNS,
    TestRow,
    enrich_row,
    row_from_status,
    summary_fields,
)


def _var(name, *, passed=True, nrmse=1e-6):
    return VariableComparison(
        index=0,
        name=name,
        passed=passed,
        nrmse=nrmse,
        rmse=nrmse,
        signal_range=1.0,
        max_abs_error=nrmse,
        max_abs_error_time=0.0,
        reference_final=1.0,
        actual_final=1.0,
        tolerance_used=1e-4,
    )


@pytest.fixture
def sample_comparison():
    return TestComparison(
        model_id="Lib.A",
        passed=False,
        variables=[_var("a"), _var("b", passed=False, nrmse=0.5)],
    )


def test_the_columns_both_surfaces_show():
    assert [c.key for c in COLUMNS] == [
        "test",
        "ref",
        "model",
        "status",
        "resolution",
        "worker",
        "elapsed",
        "nrmse",
        "vars",
        "warnings",
        "translate",
        "sim",
        "total",
        "detail",
    ]
    kinds = {c.key: c.kind for c in COLUMNS}
    assert kinds["status"] == "status"
    assert {k for k, v in kinds.items() if v == "number"} == {
        "elapsed",
        "nrmse",
        "vars",
        "warnings",
        "translate",
        "sim",
        "total",
    }
    col = {c.key: c for c in COLUMNS}
    # The resize-follow reads --col-w-detail: the key stays, the label reads
    # what the column holds.
    assert col["detail"].label == "Message"
    assert "passed" in (col["vars"].title or "")
    assert (col["resolution"].title or "").startswith("Provenance of stop_time")


def test_a_live_row_before_any_comparison():
    row = row_from_status(
        {
            "test_key": "test_0001",
            "model_id": "Lib.A",
            "status": "running",
            "worker_id": 2,
            "elapsed": 3.25,
            "report_dir": "ref_0007",
            "field_sources": {"stop_time": "annotation"},
        }
    )
    assert row.status == "RUNNING" and row.ref_id == "ref_0007"
    cells = {c.key: c.text(row) for c in COLUMNS}
    assert cells["worker"] == "W2" and cells["elapsed"] == "3.2"  # "%.1f"
    assert cells["nrmse"] == "—" and cells["resolution"] == "annotation"


def test_a_simulated_but_unscored_live_row_keeps_its_live_word():
    row = row_from_status({"test_key": "t", "model_id": "Lib.A", "status": "passed"})
    assert row.status is None and row.status_text == "SIMULATED"
    # The existing "no verdict yet" theme token styles it.
    assert row.status_class == "pending"


def test_summary_fields_are_the_sidecars_numbers(sample_comparison):
    s = summary_fields(sample_comparison, stats={"timing": {"sim_wall": 1.5}})
    assert s["status"] == "FAIL"
    assert (s["status_text"], s["status_class"]) == ("FAIL", "fail")
    assert s["sim_wall"] == 1.5 and s["n_vars"] == len(sample_comparison.variables)
    assert (s["n_vars_passed"], s["worst_nrmse"], s["n_unscored"]) == (1, 0.5, 0)


def test_the_sidecar_carries_the_classifiers_word(tmp_path, sample_comparison):
    plot_dir = tmp_path / "report"
    generate_comparison_plots(
        model_id="Lib.A",
        ref_data=None,
        result=None,
        comparisons=sample_comparison.variables,
        plot_dir=plot_dir,
        comparison=sample_comparison,
    )
    summary = json.loads((plot_dir / "comparison_data.json").read_text("utf-8"))[
        "summary"
    ]
    assert summary["status"] == "FAIL"
    assert summary["model_id"] == "Lib.A" and summary["n_vars_passed"] == 1


def test_a_sidecar_without_a_comparison_has_numbers_and_no_verdict(
    tmp_path, sample_comparison
):
    plot_dir = tmp_path / "report"
    generate_comparison_plots(
        model_id="Lib.A",
        ref_data=None,
        result=None,
        comparisons=sample_comparison.variables,
        plot_dir=plot_dir,
    )
    summary = json.loads((plot_dir / "comparison_data.json").read_text("utf-8"))[
        "summary"
    ]
    assert summary["status"] is None and summary["status_text"] is None
    assert summary["status_class"] is None
    assert (summary["n_vars"], summary["worst_nrmse"]) == (2, 0.5)


def test_a_stale_or_foreign_summary_does_not_enrich():
    row = row_from_status({"test_key": "t", "model_id": "Lib.A", "status": "passed"})
    assert (
        enrich_row(row, {"model_id": "Lib.B", "status": "FAIL"}, start_wall=None) == row
    )
    assert (
        enrich_row(
            row,
            {"model_id": "Lib.A", "status": "FAIL", "written_at": 1.0},
            start_wall=2.0,
        )
        == row
    )
    assert (
        enrich_row(
            row,
            {"model_id": "Lib.A", "status": "FAIL", "written_at": 3.0},
            start_wall=2.0,
        ).status
        == "FAIL"
    )


def test_a_summary_enriches_the_row_with_its_numbers():
    row = row_from_status({"test_key": "t", "model_id": "Lib.A", "status": "passed"})
    rich = enrich_row(
        row,
        {"model_id": "Lib.A", "status": "PASS", "worst_nrmse": 2e-5, "n_vars": 3},
        start_wall=None,
    )
    # The sidecar's key is worst_nrmse; the row's is worst_score.
    assert (rich.status, rich.worst_score, rich.n_vars) == ("PASS", 2e-5, 3)


def test_a_sidecar_written_before_status_reads_its_status_text():
    # Old data: a sidecar from before `status` existed carries only the
    # word, and TIMED OUT is the one word that is not its status.
    row = row_from_status({"test_key": "t", "model_id": "Lib.A", "status": "passed"})
    for text, status in (("SIM_FAIL", "SIM_FAIL"), ("TIMED OUT", "TIMED_OUT")):
        old = {"model_id": "Lib.A", "status_text": text}
        assert enrich_row(row, old, start_wall=None).status == status


def test_display_text_and_sort_value():
    row = TestRow(
        model_id="Lib.A",
        test_key="t",
        status="PASS",
        worst_score=1.23456e-5,
        n_vars=4,
        n_vars_passed=3,
        n_unscored=0,
        n_warnings=0,
    )
    col = {c.key: c for c in COLUMNS}
    assert col["nrmse"].text(row) == "1.2346e-05"
    assert col["nrmse"].sort(row) == 1.23456e-5
    assert col["vars"].text(row) == "3/4" and col["vars"].sort(row) == 3
    # the sort value is the STATUS_ORDER index
    assert col["status"].text(row) == "PASS" and col["status"].sort(row) == 2
    assert col["model"].sort(row) == "lib.a"
    unscored = TestRow(model_id="Lib.B", test_key="u", status="FAIL", n_unscored=2)
    assert col["nrmse"].text(unscored) == "n/a (2 unscored)"
    assert col["nrmse"].sort(unscored) is None
    # A text column with no value reads "—" and sorts as nothing, not as "—".
    assert col["ref"].text(unscored) == "—" and col["ref"].sort(unscored) is None
    assert col["status"].sort(TestRow(model_id="Lib.C")) is None


def test_the_numbers_read_as_the_dashboard_reads_them():
    row = TestRow(
        model_id="Lib.A",
        n_warnings=2,
        translation_wall=1.0,
        sim_wall=0.125,
        total_wall=12.3456,
        field_sources={"stop_time": "annotation", "tolerance": "test_spec"},
        message="boom",
    )
    cells = {c.key: c.text(row) for c in COLUMNS}
    assert cells["warnings"] == "2"
    assert (cells["translate"], cells["sim"], cells["total"]) == (
        "1.00",
        "0.12",
        "12.35",
    )
    assert cells["resolution"] == "mixed" and cells["detail"] == "boom"


def test_the_suite_writes_each_sidecar_with_its_tests_verdict(
    tmp_path, sample_comparison
):
    from types import SimpleNamespace

    from dstf.reporting.report_suite import _build_per_test_args, _render_one_test

    args = _build_per_test_args(
        sample_comparison,
        results={},
        test_lookup={},
        store=SimpleNamespace(
            get_reference=lambda _m: None,
            index=SimpleNamespace(get_id=lambda _m: None),
            ref_dir=tmp_path,
        ),
        config=SimpleNamespace(
            work_dir=tmp_path, max_embedded_samples=1000, reference_root=tmp_path
        ),
        manifest_meta_by_model={},
    )
    _render_one_test(args, tmp_path / "reports")
    sidecar = tmp_path / "reports" / args["report_id"] / "comparison_data.json"
    assert json.loads(sidecar.read_text("utf-8"))["summary"]["status"] == "FAIL"


def test_a_summary_with_a_status_outside_the_vocabulary_keeps_the_rows():
    """A sidecar is data: a status no surface has a word for is ignored,
    and the row keeps its own (the page would otherwise KeyError on it)."""
    row = row_from_status({"test_key": "t", "model_id": "Lib.A", "status": "failed"})
    rich = enrich_row(
        row, {"model_id": "Lib.A", "status": "BOGUS", "n_vars": 2}, start_wall=None
    )
    assert (rich.status, rich.n_vars) == ("SIM_FAIL", 2)
    assert rich.status_text == "SIM_FAIL"


def test_an_unknown_live_state_is_not_silently_simulated():
    """status.json is data too: a state the vocabulary does not know has no
    verdict, and says what it was rather than reading as SIMULATED with no
    reason."""
    row = row_from_status({"test_key": "t", "model_id": "Lib.A", "status": "paused"})
    assert row.status is None
    assert row.message == "status 'paused'"
    detailed = row_from_status(
        {"test_key": "t", "model_id": "Lib.A", "status": "paused", "detail": "why"}
    )
    assert detailed.message == "status 'paused': why"


def test_a_live_ref_id_is_only_a_ref_report_dir():
    """A test with no baseline yet registers under its test_key: that is no
    reference, and the Reference column must not name it as one."""
    row = row_from_status(
        {"test_key": "test_0003", "model_id": "Lib.A", "report_dir": "test_0003"}
    )
    assert row.ref_id is None and row.report_dir == "test_0003"
    assert {c.key: c.text(row) for c in COLUMNS}["ref"] == "—"


def test_an_entry_without_a_status_is_queued():
    """ProgressReporter registers every test queued; an entry that names no
    state has not started, and has nothing to explain."""
    row = row_from_status({"test_key": "t", "model_id": "Lib.A"})
    assert (row.status, row.message) == ("QUEUED", None)


def test_a_status_that_is_not_a_word_is_unknown():
    """status.json is data: a list, a number or a null is no state. A list
    is unhashable and used to raise out of the page."""
    for raw in (["passed"], 3, {"a": 1}):
        row = row_from_status(
            {"test_key": "t", "model_id": "Lib.A", "status": raw, "detail": "d"}
        )
        assert row.status is None
        assert row.message == f"status {raw!r}: d"
    # A null says nothing: no verdict, and no "status None" message.
    row = row_from_status({"test_key": "t", "model_id": "Lib.A", "status": None})
    assert (row.status, row.message) == (None, None)
    row = row_from_status(
        {"test_key": "t", "model_id": "Lib.A", "status": None, "detail": "d"}
    )
    assert (row.status, row.message) == (None, "d")


def test_a_sidecar_status_that_is_not_a_word_is_ignored():
    row = row_from_status({"test_key": "t", "model_id": "Lib.A", "status": "running"})
    for bad in (
        {"status": ["PASS"]},
        {"status": {"a": 1}},
        {"status_text": ["PASS"]},
        {"status": None, "status_text": {"a": 1}},
    ):
        rich = enrich_row(row, {"model_id": "Lib.A", **bad}, start_wall=None)
        assert rich.status == "RUNNING", bad


def test_an_empty_provenance_does_not_erase_the_live_rows():
    row = row_from_status(
        {
            "test_key": "t",
            "model_id": "Lib.A",
            "status": "passed",
            "field_sources": {"stop_time": "annotation"},
        }
    )
    for empty in ({}, None):
        rich = enrich_row(
            row, {"model_id": "Lib.A", "field_sources": empty}, start_wall=None
        )
        assert rich.resolution == "annotation"
    rich = enrich_row(
        row,
        {"model_id": "Lib.A", "field_sources": {"stop_time": "test_spec"}},
        start_wall=None,
    )
    assert rich.resolution == "test_spec"
