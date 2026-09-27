"""A Suite row from the Session (``authoring.suite_rows``): the row model's
``TestRow`` built from what the Session holds, in process."""

from __future__ import annotations

from dataclasses import replace

import pytest

from dstf.authoring.session import RunningJob, Session
from dstf.authoring.suite_rows import suite_row, suite_rows
from test_session import LOOSEN, A, B, C, D, _project


@pytest.fixture
def session(tmp_path) -> Session:
    cfg, work = _project(tmp_path)
    s = Session()
    s.open(cfg, work_dir=work)
    return s


def test_a_scored_pass(session):
    row = suite_row(session, D)
    comp = session.comparisons[D]
    assert row.status == "PASS" and row.model_id == D
    assert row.n_vars == 1 and row.n_vars_passed == 1
    assert row.worst_score is not None and row.worst_score < 0.01
    assert row.ref_id == f"ref_{comp.test_id}"
    assert row.field_sources == session.project.test(D).field_sources
    assert row.edits == 0 and not row.waiting


def test_no_baseline_has_no_ref_id(session):
    row = suite_row(session, B)
    assert row.status == "NO_REF"
    assert row.ref_id is None  # never "ref_None"


def test_walls_from_the_results_timing(session):
    session.project.results[D].statistics = {
        "timing": {"sim_wall": 1.5, "total_wall": 2.0}
    }
    row = suite_row(session, D)
    assert row.sim_wall == 1.5 and row.total_wall == 2.0


def test_the_message_is_the_comparisons_error(session):
    session.comparisons[D] = replace(session.comparisons[D], error_message="boom")
    assert suite_row(session, D).message == "boom"


def test_a_staged_edit_counts_and_rescores(session):
    assert suite_row(session, A).status == "FAIL"
    session.stage([LOOSEN])
    row = suite_row(session, A)
    # A first LOOSEN materializes A's implicit tree too: every pending op on
    # A counts, as the header's count does.
    assert row.edits == len([op for op in session.spec.pending if op.model_id == A])
    assert row.edits >= 1
    assert row.status == "PASS"


def test_running_beats_everything(session):
    session.running = RunningJob(label="rerun", models=frozenset({A}))
    assert suite_row(session, A, failed={A}).status == "RUNNING"


def test_a_failed_scoring(session):
    del session.comparisons[D]  # `_reload_results` pops a failed model's verdict
    row = suite_row(session, D, failed={D})
    assert row.status == "SIM_FAIL"
    assert row.message.startswith("scoring failed")


def test_a_comparison_beats_an_earlier_scoring_failure(session):
    """The shell's failed set is only reset by the next open: a model whose
    scoring failed on open and that a later rescore scored reads its
    verdict, not "scoring failed" for the rest of the session."""
    assert suite_row(session, D, failed={D}).status == "PASS"


def test_waiting_before_a_comparison(session):
    del session.comparisons[C]
    row = suite_row(session, C)
    assert row.status is None and row.waiting and row.status_text == "…"


def test_rows_in_the_given_order(session):
    rows = suite_rows(session, [D, A, B], failed=set())
    assert [r.model_id for r in rows] == [D, A, B]
