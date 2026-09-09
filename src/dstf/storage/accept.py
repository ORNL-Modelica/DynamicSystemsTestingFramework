"""Accepting a run's results as baselines — the one function the CLI's two
``--accept`` paths and the authoring tool's Accept call."""

from __future__ import annotations

from pathlib import Path

from ..backends.types import TestResult
from ..discovery.test_registry import TestModel
from .batch_manifest import unfinished_run_models
from .reference_store import ReferenceStore


def accept_run_results(
    store: ReferenceStore,
    tests: list[TestModel],
    results: dict[str, TestResult],
    work_dir: Path,
) -> tuple[int, list[tuple[str, str]]]:
    """:meth:`ReferenceStore.accept_results` plus the aborted-run refusal.

    A test the batch manifest in ``work_dir`` still records as pending was
    in flight when a run was aborted: it has no recorded verdict, and its
    truncated result file may still parse. Acceptance requires an
    authoritative successful run, so it is skipped with the reason, never
    stored. Deliberately NOT keyed on the comparison verdict — a test that
    ran fine and now compares FAILED is exactly the drift case accepting
    exists to re-baseline.

    Returns ``(stored, skipped)`` exactly as ``accept_results`` does, with
    the refusals appended in model order.
    """
    unfinished = unfinished_run_models(work_dir) & {t.model_id for t in tests}
    stored, skipped = store.accept_results(
        [t for t in tests if t.model_id not in unfinished], results
    )
    return stored, list(skipped) + [
        (
            model_id,
            f"last run did not complete for this test (aborted while it was "
            f"in flight; {work_dir} still records it as pending) — "
            f"re-run before accepting",
        )
        for model_id in sorted(unfinished)
    ]
