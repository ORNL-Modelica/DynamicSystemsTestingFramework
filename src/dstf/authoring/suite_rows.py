"""A Suite row from the Session: the row model's ``TestRow``
(``reporting.rows``) built from what the Session holds — its comparison
through ``summary_fields`` (the numbers the report's sidecar carries,
computed the same way), the result's timing, the test's provenance, its
staged edits and its place in the simulation queue. In process: a sidecar is the
full-resolution context, one JSON parse per test. No UI framework here
(the Session tier)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Iterable, Mapping
from dataclasses import replace
from typing import TYPE_CHECKING

from ..backends.pool import ItemState
from ..reporting.rows import TestRow, summary_fields
from ..reporting.status import (
    QUEUED,
    RUNNING,
    SCORING_FAILED,
    SIM_FAIL,
    scored_status,
)

if TYPE_CHECKING:
    from .session import Session


def _edits(session: Session) -> Mapping[str, int]:
    return Counter(op.model_id for op in session.spec.pending)


def suite_row(
    session: Session,
    model_id: str,
    *,
    failed: Collection[str] = (),
    edits: Mapping[str, int] | None = None,
) -> TestRow:
    """One Suite row. A test the queue holds reads QUEUED or RUNNING,
    whatever else is true of it — QUEUED from the click on, while the
    click's read of the library is still out (`Session.checking`); then the
    comparison — CANCELLED where it
    has no result because its run in the queue was stopped (until that
    row is cleared); then a scoring failure the open announced (``failed`` —
    the shell's set, kept until the next open, so a model a later rescore
    scored reads its verdict, not the failure); else the row waits for its
    verdict. ``edits`` is the pending-op count per model; ``suite_rows``
    counts it once for every row."""
    counts = _edits(session) if edits is None else edits
    project = session.project
    test = project.test(model_id) if project is not None else None
    row = TestRow(
        model_id=model_id,
        field_sources=dict(test.field_sources) if test is not None else {},
        edits=counts.get(model_id, 0),
    )
    state = session.queue_state(model_id)
    comp = session.comparisons.get(model_id)
    if state is ItemState.WAITING or model_id in session.checking:
        return replace(row, status=QUEUED)
    if state is ItemState.RUNNING:
        return replace(row, status=RUNNING)
    if comp is not None:
        result = project.results.get(model_id) if project is not None else None
        s = summary_fields(
            comp,
            stats=result.statistics if result is not None else None,
            ref_id=f"ref_{comp.test_id}" if comp.test_id else None,
            field_sources=row.field_sources,
        )
        return replace(
            row,
            status=scored_status(s["status"], cancelled=state is ItemState.CANCELLED),
            ref_id=s["ref_id"],
            worst_score=s["worst_nrmse"],
            n_unscored=s["n_unscored"],
            n_vars=s["n_vars"],
            n_vars_passed=s["n_vars_passed"],
            n_warnings=s["n_warnings"],
            translation_wall=s["translation_wall"],
            sim_wall=s["sim_wall"],
            sim_cpu=s["sim_cpu"],
            total_wall=s["total_wall"],
            message=comp.error_message,
        )
    if model_id in failed:
        return replace(row, status=SIM_FAIL, message=f"{SCORING_FAILED} (see the log)")
    return replace(row, waiting=True)


def suite_rows(
    session: Session, model_ids: Iterable[str], *, failed: Collection[str] = ()
) -> list[TestRow]:
    """The rows of ``model_ids``, in that order; the edits counted once."""
    counts = _edits(session)
    failed_set = set(failed)
    return [suite_row(session, m, failed=failed_set, edits=counts) for m in model_ids]
