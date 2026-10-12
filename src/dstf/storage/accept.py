"""Accepting a run's results as baselines — the one function the CLI's two
``--accept`` paths, its interactive review and the authoring tool's Accept
call."""

from __future__ import annotations

from pathlib import Path

from ..backends.types import TestResult
from ..comparison.comparator import nothing_scored, refused_base_tolerance
from ..discovery.test_registry import TestModel
from .batch_manifest import unfinished_run_models
from .ref_index import RefIndex
from .reference_store import ReferenceStore

#: A baseline file larger than this many bytes — 5 MB — is said by the accept
#: that wrote it (D153). A constant, not a key: the remedy it names is the key.
LARGE_BASELINE_BYTES = 5_000_000


def large_baseline_warning(store: ReferenceStore, model_id: str) -> str | None:
    """The sentence for ``model_id``'s reference file when it is larger than
    `LARGE_BASELINE_BYTES`, else ``None``. A warning: the baseline IS stored.
    The size is the file's, and its counts are read back from it — only for
    a file that is over, so an accept of small baselines reads nothing."""
    test_id = store.index.get_id(model_id)
    if test_id is None:
        return None
    ref_file = store.ref_dir / RefIndex.ref_filename(test_id)
    try:
        size = ref_file.stat().st_size
    except OSError:
        return None
    if size <= LARGE_BASELINE_BYTES:
        return None
    reference = store.get_reference(model_id) or {}
    points = len(reference.get("time", []))
    n_vars = len(reference.get("variables", []))
    return (
        f"{ref_file.name} ({model_id}) is {size / 1e6:.1f} MB ({points:,} points "
        f"× {n_vars:,} variable{'' if n_vars == 1 else 's'}) — set "
        '"baseline_points" for this test to store fewer.'
    )


#: How many lost names :func:`lost_variables_warning` spells out.
_NAMED_LOST = 6


def _variable_names(reference: dict | None) -> list[str]:
    return [v.get("name") or "" for v in (reference or {}).get("variables", [])]


def lost_variables_warning(
    store: ReferenceStore, model_id: str, held_before: list[str]
) -> str | None:
    """The sentence for a baseline just written that no longer holds a
    variable the one it replaced held (``held_before``, its names), else
    ``None``. A warning: the baseline IS stored — accepting is how a
    variable is dropped on purpose — but a run that LOST one rewrote the
    baseline without it, and nothing said so."""
    test_id = store.index.get_id(model_id)
    if test_id is None:
        return None
    holds = set(_variable_names(store.get_reference(model_id)))
    lost = [name for name in held_before if name and name not in holds]
    if not lost:
        return None
    named = ", ".join(lost[:_NAMED_LOST])
    if len(lost) > _NAMED_LOST:
        named += f" and {len(lost) - _NAMED_LOST} more"
    return (
        f"{RefIndex.ref_filename(test_id)} ({model_id}) no longer holds {named} — "
        f"the accepted result does not hold {'it' if len(lost) == 1 else 'them'}."
    )


def accept_refusal(
    store: ReferenceStore, test: TestModel, result: TestResult | None
) -> str | None:
    """Why the comparison REFUSES ``test`` — its base tolerance is no
    number, or its result holds nothing it tracks — or ``None``: the
    comparison's own decisions (`refused_base_tolerance`, `nothing_scored`),
    asked of the test and its stored reference as ``compare_one`` asks
    them. A test with no successful result is not refused: it has its own
    reason."""
    if result is None or not result.success:
        return None
    refused = refused_base_tolerance(
        test, store.get_reference(test.model_id), store.config.tolerance
    )
    if refused is not None:
        return refused.error_message
    return nothing_scored(test, result)


def accept_run_results(
    store: ReferenceStore,
    tests: list[TestModel],
    results: dict[str, TestResult],
    work_dir: Path,
) -> tuple[int, list[tuple[str, str]], list[str]]:
    """:meth:`ReferenceStore.accept_results` plus two refusals and two warnings.

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

    A baseline file just written that is larger than `LARGE_BASELINE_BYTES`
    is said once (`large_baseline_warning`): it IS stored, and counts as
    stored. Every accept path prints what this returns, so the CLI and the
    tool say the same sentence. So is a baseline that no longer holds a
    variable the one it replaced held (`lost_variables_warning`).

    Returns ``(stored, skipped, warnings)``: how many baselines were written;
    ``accept_results``'s skips with the refusals appended — the refused
    tests in the order given, then the unfinished ones in model order; and
    the warnings in the order written: per test, what it lost, then its
    size.
    """
    unfinished = unfinished_run_models(work_dir) & {t.model_id for t in tests}
    refused = {
        t.model_id: reason
        for t in tests
        if t.model_id not in unfinished
        and (reason := accept_refusal(store, t, results.get(t.model_id))) is not None
    }
    accepted = [
        t for t in tests if t.model_id not in unfinished and t.model_id not in refused
    ]
    # What each baseline holds now: read before the accept replaces it.
    held_before = {
        t.model_id: _variable_names(store.get_reference(t.model_id)) for t in accepted
    }
    stored, skipped = store.accept_results(accepted, results)
    return (
        len(stored),
        [
            *skipped,
            *refused.items(),
            *(
                (
                    model_id,
                    f"last run did not complete for this test (aborted or "
                    f"cancelled while it was in flight; {work_dir} records no "
                    f"verdict for it) — re-run before accepting",
                )
                for model_id in sorted(unfinished)
            ),
        ],
        [
            warning
            for model_id in stored
            for warning in (
                lost_variables_warning(store, model_id, held_before[model_id]),
                large_baseline_warning(store, model_id),
            )
            if warning is not None
        ],
    )
