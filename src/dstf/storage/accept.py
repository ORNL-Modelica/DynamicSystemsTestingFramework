"""Accepting a run's results as baselines — the one function the CLI's two
``--accept`` paths, its interactive review and the authoring tool's Accept
call."""

from __future__ import annotations

from pathlib import Path

from ..backends.types import TestResult
from ..comparison.comparator import refused_base_tolerance
from ..discovery.test_registry import TestModel
from .batch_manifest import unfinished_run_models
from .reference_store import ReferenceStore


def accept_refusal(
    store: ReferenceStore, test: TestModel, result: TestResult | None
) -> str | None:
    """Why the comparison REFUSES ``test`` — its base tolerance is no number
    — or ``None``: the comparison's own decision (`refused_base_tolerance`),
    asked of the test and its stored reference as ``compare_one`` asks it. A
    test with no successful result is not refused: it has its own reason."""
    if result is None or not result.success:
        return None
    refused = refused_base_tolerance(
        test, store.get_reference(test.model_id), store.config.tolerance
    )
    return None if refused is None else refused.error_message


def accept_run_results(
    store: ReferenceStore,
    tests: list[TestModel],
    results: dict[str, TestResult],
    work_dir: Path,
) -> tuple[int, list[tuple[str, str]]]:
    """:meth:`ReferenceStore.accept_results` plus two refusals.

    A test the batch manifest in ``work_dir`` still records as pending was
    in flight when a run was aborted: it has no recorded verdict, and its
    truncated result file may still parse. Acceptance requires an
    authoritative successful run, so it is skipped with the reason, never
    stored. Deliberately NOT keyed on the comparison verdict — a test that
    ran fine and now compares FAILED is exactly the drift case accepting
    exists to re-baseline.

    A test the comparison REFUSES (`accept_refusal`) is skipped with the
    refusal's sentence: its result was scored against nothing, and the
    baseline written would carry — or keep — a base no comparison reads.
    Its reference file is not touched.

    Returns ``(stored, skipped)`` exactly as ``accept_results`` does, with
    the refusals appended: the refused tests in the order given, then the
    unfinished ones in model order.
    """
    unfinished = unfinished_run_models(work_dir) & {t.model_id for t in tests}
    refused = {
        t.model_id: reason
        for t in tests
        if t.model_id not in unfinished
        and (reason := accept_refusal(store, t, results.get(t.model_id))) is not None
    }
    stored, skipped = store.accept_results(
        [
            t
            for t in tests
            if t.model_id not in unfinished and t.model_id not in refused
        ],
        results,
    )
    return stored, [
        *skipped,
        *refused.items(),
        *(
            (
                model_id,
                f"last run did not complete for this test (aborted while it was "
                f"in flight; {work_dir} still records it as pending) — "
                f"re-run before accepting",
            )
            for model_id in sorted(unfinished)
        ),
    ]
