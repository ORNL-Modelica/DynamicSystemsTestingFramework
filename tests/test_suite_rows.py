"""A Suite row from the Session (``authoring.suite_rows``): the row model's
``TestRow`` built from what the Session holds, in process."""

from __future__ import annotations

from dataclasses import replace

import pytest

from dstf.authoring.session import Session
from dstf.authoring.suite_rows import suite_row, suite_rows
from dstf.backends.pool import ItemState
from test_session import LOOSEN, A, B, C, D, _project
from test_session_queue import drop_item, fake_item


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


def test_a_test_its_run_never_finished_is_not_run(session):
    """D145: C's manifest entry is still pending — the last run never
    finished it — so it has no result: NOT RUN, in the grey token's class,
    not SIM_FAIL."""
    row = suite_row(session, C)
    assert (row.status, row.status_text, row.status_class) == (
        "NOT RUN",
        "NOT RUN",
        "not-run",
    )


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
    # A first LOOSEN writes A's implicit tree out too: two ops, and ONE
    # edit — what the user did — as the header counts it.
    assert len([op for op in session.spec.pending if op.model_id == A]) == 2
    assert row.edits == 1
    assert row.status == "PASS"


def test_queued_and_running_beat_everything(session):
    """A test the queue holds reads QUEUED while it waits and RUNNING while
    it runs, over its verdict and over a scoring failure; out of the queue
    it reads its verdict again."""
    running = fake_item(session, A, ItemState.RUNNING)
    waiting = fake_item(session, D, ItemState.WAITING)
    assert suite_row(session, A, failed={A}).status == "RUNNING"
    assert suite_row(session, D, failed={D}).status == "QUEUED"
    drop_item(session, running)
    drop_item(session, waiting)
    assert suite_row(session, A).status == "FAIL"
    assert suite_row(session, D).status == "PASS"


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
    assert row.status is None and row.waiting and row.status_text == "SCORING"


def test_rows_in_the_given_order(session):
    rows = suite_rows(session, [D, A, B], failed=set())
    assert [r.model_id for r in rows] == [D, A, B]


def test_the_suite_table_shows_sim_after_a_reload(tmp_path):
    """A Session opened over a work dir a run left, holding no run results:
    each row's Sim (s) and Total (s) come from the run statistics kept beside
    the result (``run_stats.json``)."""
    from conftest import python_example_copy
    from dstf.backends import get_backend
    from dstf.pipeline import run_pipeline
    from dstf.storage.reference_store import ReferenceStore

    config, tests = python_example_copy(tmp_path)
    backend = get_backend(config)
    backend.quiet = True
    run_pipeline(config, tests, backend=backend, store=ReferenceStore(config))
    s = Session()
    s.open(config.config_file, work_dir=config.work_dir)
    rows = suite_rows(s, [t.model_id for t in tests], failed=set())
    assert len(rows) == len(tests)
    assert all(r.sim_wall is not None and r.total_wall is not None for r in rows)


def test_a_dymola_row_after_a_reload_shows_the_result_files_cpu_time(tmp_path):
    """A Session reopened over a Dymola work dir whose run kept no
    run_stats.json (a run from before the file, or a batch run): Sim (s)
    is the dsres.mat's final CPUtime, marked as such."""
    import json
    import shutil
    from pathlib import Path

    import numpy as np

    from dstf.reporting.rows import COLUMNS

    here = Path(__file__).parent
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    shutil.copytree(
        here.parent / "examples" / "modelica" / "ModelicaTestingLib",
        config_dir / "ModelicaTestingLib",
    )
    (config_dir / "testing.json").write_text(
        json.dumps(
            {
                "source_path": "ModelicaTestingLib",
                "backend": "Dymola",
                "backends": {},
                "dependencies": [],
            }
        ),
        encoding="utf-8",
    )
    model = "ModelicaTestingLib.Examples.ConstantTest"
    work = tmp_path / "work"
    (work / "test_0001").mkdir(parents=True)
    # The fixture's dsres.mat is ConstantTest's; its CPUtime ends at 0.002.
    shutil.copy(
        here / "fixtures" / "results" / "Dymola" / "dsres.mat",
        work / "test_0001" / "dsres.mat",
    )
    (work / "batch_manifest.json").write_text(
        json.dumps(
            {
                "test_0001": {
                    "model_id": model,
                    "ref_id": None,
                    "last_run_status": "passed",
                }
            }
        ),
        encoding="utf-8",
    )
    s = Session()
    s.open(config_dir / "testing.json", work_dir=work)
    (row,) = suite_rows(s, [model], failed=set())
    sim = {c.key: c for c in COLUMNS}["sim"]
    # The run's own number: the CPUtime diagnostic's final, a float32.
    cpu = float(np.float32(0.002))
    assert (row.sim_wall, row.sim_cpu, row.sim_source) == (None, cpu, "cpu")
    assert (sim.text(row), sim.tag(row)) == ("0.00", "cpu")
