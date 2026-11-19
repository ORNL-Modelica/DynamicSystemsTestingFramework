"""``verdicts.json``: what a scoring ``run`` or ``compare`` decided, as one
file in the work dir — every test's status word and its criteria tree with
each node's verdict and numbers, and the run's totals.

For a script or an agent. Without it a plain run leaves no per-criterion
result on disk at all (``--report`` writes the tree, into one
``comparison_data.json`` per test), and ``status.json`` says ``"passed"``
of a test whose comparison FAILED: it records that the simulation ran.

Nothing here is a second computation. The status word is the console's
(:mod:`.status`), and a test's ``tree`` is the dict
:func:`~.context_builder.build_tree_view` returns — the ``tree_view`` of
that test's ``comparison_data.json`` and the tree the tool shows.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..backends.progress import AS_RECORDED
from ..comparison.types import TestComparison
from ..storage.names import baseline_stem
from ..storage.ref_index import _atomic_write_text
from .context_builder import build_tree_view, test_texts
from .status import (
    SKIPPED,
    STATUS_LABELS,
    STATUS_ORDER,
    classify,
    count_by_status,
    exit_code,
    mark_words,
)

#: The file's name, beside ``status.json`` in the work dir.
VERDICTS_FILE = "verdicts.json"


def status_word(comp: TestComparison) -> str:
    """The word the console prints for ``comp``: ``PASS``, ``FAIL``,
    ``SIM_FAIL``, ``NO_REF``, ``NOT RUN``, ``SKIPPED``."""
    return STATUS_LABELS[classify(comp)]


def test_verdict(comp: TestComparison, test: Any, result: Any) -> dict:
    """One test of the file: its model id, the baseline it was scored
    against (``ref_<code>``, or null), its status word, what the
    comparison said where there is no tree to say it (a failed simulation's
    error, a refusal, "No reference baseline stored", why it was
    skipped), the mark it carries for this backend and OS (``Python:
    <reason>``, or null — a script reads there why a FAIL left the exit
    code 0), its warnings, and its criteria ``tree`` — None for a test
    nothing was compared for."""
    return {
        "model_id": comp.model_id,
        # The baseline it was scored against, by its name: ``ref_<code>``.
        "ref_id": baseline_stem(comp.model_id) if comp.baseline_path else None,
        "status": status_word(comp),
        "message": comp.error_message or None,
        "mark": mark_words(comp.mark) if comp.mark is not None else None,
        "warnings": [w.text for w in comp.warnings],
        "tree": build_tree_view(
            list(comp.variables),
            test,
            comp.metric_tree,
            test_texts(comp.variables, result),
        ),
    }


def build_verdicts(
    comparisons: Iterable[TestComparison],
    tests: Iterable[Any],
    results: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    selection: Mapping[str, Any] | None = None,
) -> dict:
    """The document :func:`write_verdicts` writes: the scored tests in the
    order given, and their totals by status word — every word of the
    console's summary, zero included — with the exit code they give. The
    ``total`` is of the tests not SKIPPED: what the Results line counts
    passes over."""
    comps = list(comparisons)
    by_model = {t.model_id: t for t in tests}
    counts = count_by_status(comps)
    totals = {
        STATUS_LABELS[s]: counts[s]
        for s in STATUS_ORDER
        if s in STATUS_LABELS and (counts[s] or s in _ALWAYS)
    }
    return {
        "written_at": datetime.now(UTC).isoformat(timespec="seconds"),
        # The run these verdicts are of: status.json's provenance block
        # (backend, tool version, os), or null where there is none.
        "metadata": dict(metadata) if metadata else None,
        # What the run's tests were chosen by: status.json's ``selection``
        # (N of the project's M tests, and the filter, package, rerun or
        # revision that chose them), or null for a run of every test.
        "selection": dict(selection) if selection else None,
        "totals": {"total": len(comps) - counts[SKIPPED], **totals},
        "exit_code": exit_code(comps),
        "tests": [
            test_verdict(c, by_model.get(c.model_id), (results or {}).get(c.model_id))
            for c in comps
        ],
    }


#: The words a total is always given for, a zero included: what a compare
#: can say of a test. (A QUEUED or RUNNING row is the live page's.)
_ALWAYS = frozenset({"PASS", "FAIL", "SIM_FAIL", "NO_REF", "NOT RUN", SKIPPED})


def write_verdicts(
    work_dir: Path,
    comparisons: Iterable[TestComparison],
    tests: Iterable[Any],
    results: Mapping[str, Any] | None = None,
    selection: object = AS_RECORDED,
) -> Path:
    """Write ``<work_dir>/verdicts.json`` — atomically, as strict JSON (a
    sentinel's inf/nan is ``null``) — and stamp each test's verdict into
    ``status.json`` beside its simulation status. Returns the path.

    ``selection``: what the scored tests were chosen by — status.json's
    (the last run's) unless the command that scores them narrowed them
    itself: a ``compare --filter``, a ``run --rerun`` with nothing to
    rerun (D170)."""
    from ..backends.progress import read_status, stamp_verdicts
    from .report_suite import _json_sanitize

    comps = list(comparisons)
    snapshot = read_status(work_dir) or {}
    if selection is AS_RECORDED:
        selection = snapshot.get("selection")
    document = build_verdicts(
        comps,
        tests,
        results,
        snapshot.get("metadata"),
        selection if isinstance(selection, dict) else None,
    )
    path = work_dir / VERDICTS_FILE
    work_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(
        path, json.dumps(_json_sanitize(document), indent=2, allow_nan=False) + "\n"
    )
    stamp_verdicts(work_dir, {c.model_id: status_word(c) for c in comps})
    return path


def remove_verdicts(work_dir: Path) -> None:
    """Remove a ``verdicts.json`` that a run or compare is about to make
    stale — with the per-test pages, and for the same reason: a file that
    outlives the results it describes says what is no longer true. The
    command that scores writes it again."""
    (work_dir / VERDICTS_FILE).unlink(missing_ok=True)
