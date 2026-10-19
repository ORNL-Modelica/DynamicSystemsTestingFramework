"""Accepting a run's results as baselines — the one function the CLI's two
``--accept`` paths, its interactive review and the authoring tool's Accept
call."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ..backends.common.result_variables import criterion_variables, tracks
from ..backends.types import TestResult
from ..comparison.comparator import (
    broken_roles,
    nothing_scored,
    refused_base_tolerance,
)
from ..config import Config
from ..discovery.test_registry import TestModel, diagnostic_names
from .baseline import ReferenceFile
from .batch_manifest import BatchManifest, unfinished_run_models
from .ref_index import RefIndex
from .reference_store import ReferenceStore
from .run_stats import read_run_stats

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
    reference = store.get_reference(model_id)
    points = len(reference.baseline.time) if reference is not None else 0
    n_vars = len(reference.baseline.variables) if reference is not None else 0
    return (
        f"{ref_file.name} ({model_id}) is {size / 1e6:.1f} MB ({points:,} points "
        f"× {n_vars:,} variable{'' if n_vars == 1 else 's'}) — set "
        '"baseline_points" for this test to store fewer.'
    )


#: How many names a sentence that lists them spells out
#: (:func:`lost_variables_warnings`, :func:`simulate_only_note`).
_NAMED = 6


def _named(names: list[str]) -> str:
    """Up to `_NAMED` of ``names``, then ``and N more``."""
    named = ", ".join(names[:_NAMED])
    if len(names) > _NAMED:
        named += f" and {len(names) - _NAMED} more"
    return named


def simulate_only_note(model_ids: list[str]) -> str | None:
    """``Simulate-only, nothing to store: MyLib.Tank`` for the tests an
    accept passed over because they declare nothing to score
    (``TestModel.is_simulate_only``), else ``None``. Neither a warning nor a
    skip: such a test has no baseline to store, and its accept did not fail
    — but `Accepted 0 test baselines` with no word of the test read as an
    accept that did nothing, for no reason."""
    if not model_ids:
        return None
    return f"Simulate-only, nothing to store: {_named(model_ids)}"


def _variable_names(reference: ReferenceFile | None) -> list[str]:
    return reference.baseline.names() if reference is not None else []


def lost_variables_warnings(
    store: ReferenceStore, test: TestModel, held_before: list[str]
) -> list[str]:
    """The sentences for a baseline just written that no longer holds a
    variable the one it replaced held (``held_before``, its names); none
    when it lost nothing. A warning: the baseline IS stored — accepting is
    how a variable is dropped on purpose — but a run that LOST one rewrote
    the baseline without it, and nothing said so.

    Two sentences, since a variable leaves a baseline for two reasons, and
    each is said of its own names. One the test still tracks — a UnitTests
    variable, a ``variables`` pattern, a criterion's variable — the run did
    not deliver: ``the accepted result does not hold it``. One it no longer
    tracks was taken out on purpose, and the run may well still write it:
    ``the test no longer tracks it`` — that half read ``the accepted result
    does not hold it`` too, of a result file that held it."""
    model_id = test.model_id
    test_id = store.index.get_id(model_id)
    if test_id is None:
        return []
    holds = set(_variable_names(store.get_reference(model_id)))
    lost = [name for name in held_before if name and name not in holds]
    tracked = tracks(test)
    by_criterion = set(
        criterion_variables(
            test, diagnostic_names(test, store.config.diagnostic_variables)
        )
    )
    asked = [name for name in lost if tracked(name) or name in by_criterion]
    dropped = [name for name in lost if name not in asked]

    def said(names: list[str], why: str) -> str:
        return (
            f"{RefIndex.ref_filename(test_id)} ({model_id}) no longer holds "
            f"{_named(names)} — {why} {'it' if len(names) == 1 else 'them'}."
        )

    return [
        *([said(asked, "the accepted result does not hold")] if asked else []),
        *([said(dropped, "the test no longer tracks")] if dropped else []),
    ]


def accept_refusal(
    store: ReferenceStore, test: TestModel, result: TestResult | None
) -> str | None:
    """Why the comparison REFUSES ``test`` — its base tolerance is no
    number, its result holds nothing it tracks, or its tree is wrong in its
    file — or ``None``: the comparison's own decisions
    (`refused_base_tolerance`, `nothing_scored`, `broken_roles`), asked of
    the test and its stored reference as ``compare_one`` asks them, in its
    order. A test with no successful result is not refused: it has its own
    reason."""
    if result is None or not result.success:
        return None
    refused = refused_base_tolerance(
        test, store.get_reference(test.model_id), store.config.tolerance
    )
    if refused is not None:
        return refused.error_message
    return nothing_scored(test, result) or broken_roles(test)


def _provenance(meta: dict | None, config: Config) -> dict:
    """A baseline's ``provenance``: the metadata of the run that produced the
    result accepted (``RunMetadata.as_dict``, kept in the test's
    ``run_stats.json``), its start as ISO-8601 ``run_started`` — the start
    of the run that produced the result, which for a single test run from
    the tool's queue is that test's own start (``ItemRun.end``). No machine
    name and no load (owner, 2026-10-15). Written whole even when the run
    recorded none: ``backend``, ``backend_name`` and ``os`` from ``config``,
    every other field ``null`` — a writer never leaves a field out."""
    meta = meta or {}
    started = meta.get("generated_at")
    return {
        "backend": meta.get("backend") or config.backend,
        "backend_name": meta.get("backend_name")
        or config.backend_name
        or config.backend,
        "tool_version": meta.get("tool_version"),
        "library_versions": meta.get("library_versions"),
        "dstf_version": meta.get("dstf_version"),
        "os": meta.get("os") or config.os_name,
        "run_started": datetime.fromtimestamp(started, timezone.utc).isoformat(
            timespec="seconds"
        )
        if isinstance(started, (int, float))
        else None,
    }


def _provenances(
    store: ReferenceStore, tests: list[TestModel], work_dir: Path
) -> dict[str, dict]:
    """Each test's ``provenance``, read from the ``run_stats.json`` beside the
    result an accept of it stores: the work dir's batch manifest names the
    test's directory. The file is as fresh as the result (a rerun wipes the
    directory), so a queue rerun in the tool stamps the rerun's metadata."""
    manifest_path = work_dir / "batch_manifest.json"
    manifest = BatchManifest.load(manifest_path) if manifest_path.exists() else None
    keys = (
        {entry["model_id"]: key for key, entry in manifest.manifest.items()}
        if manifest is not None
        else {}
    )
    out = {}
    for test in tests:
        key = keys.get(test.model_id)
        kept = read_run_stats(work_dir / key, test.model_id) if key else None
        out[test.model_id] = _provenance(
            kept["run_metadata"] if kept is not None else None, store.config
        )
    return out


def accept_run_results(
    store: ReferenceStore,
    tests: list[TestModel],
    results: dict[str, TestResult],
    work_dir: Path,
) -> tuple[int, list[tuple[str, str]], list[str], list[str]]:
    """:meth:`ReferenceStore.accept_results` plus two refusals, two warnings
    and a note.

    A test the batch manifest in ``work_dir`` still records as pending was
    in flight when a run was aborted: it has no recorded verdict, and its
    truncated result file may still parse. Acceptance requires an
    authoritative successful run, so it is skipped with the reason, never
    stored. Deliberately NOT keyed on the comparison verdict — a test that
    ran fine and now compares FAILED is exactly the drift case accepting
    exists to re-baseline.

    A test the comparison REFUSES (`accept_refusal`) is skipped with the
    refusal's sentence: its result was scored against nothing, and the
    baseline written would carry — or keep — a base no comparison reads,
    or sit under a tree no accept repairs. Its reference file is not
    touched.

    A baseline file just written that is larger than `LARGE_BASELINE_BYTES`
    is said once (`large_baseline_warning`): it IS stored, and counts as
    stored. Every accept path prints what this returns, so the CLI and the
    tool say the same sentence. So is a baseline that no longer holds a
    variable the one it replaced held (`lost_variables_warnings`).

    A simulate-only test whose run completed is neither stored nor skipped:
    it has no baseline to store. The tests passed over for that are named
    in one line (`simulate_only_note`), which every accept path prints too.

    Returns ``(stored, skipped, warnings, notes)``: how many baselines were
    written; ``accept_results``'s skips with the refusals appended — the
    refused tests in the order given, then the unfinished ones in model
    order; the warnings in the order written: per test, what it lost, then
    its size; and the lines that are neither — the simulate-only tests'.
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
    stored, skipped, simulate_only = store.accept_results(
        accepted, results, provenance=_provenances(store, accepted, work_dir)
    )
    by_model = {t.model_id: t for t in accepted}
    note = simulate_only_note(simulate_only)
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
                *lost_variables_warnings(
                    store, by_model[model_id], held_before[model_id]
                ),
                large_baseline_warning(store, model_id),
            )
            if warning is not None
        ],
        [note] if note is not None else [],
    )
