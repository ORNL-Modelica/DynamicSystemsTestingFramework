"""A test's row: the one row model the dashboard and the tool's Suite table
render, its columns, and the builders that fill it.

``TestRow`` holds a test's row-level fields; ``COLUMNS`` says, for each
column both surfaces show, its key, its label, its kind (what a filter on
it means) and how a row reads and sorts there. ``summary_fields`` is the
one computation of the numbers a row carries after the compare phase: the
per-test sidecar writes them, and the dashboard reads them back through
``enrich_row``.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from ..comparison.types import TestComparison
from .context_builder import worst_finite_score
from .status import (
    LIVE_STATES,
    STATUS_CLASSES,
    STATUS_LABELS,
    STATUS_ORDER,
    classify_with_class,
    live_status,
)

#: The word a live row reads when its simulation ran and the compare phase
#: has not scored it. Not a status: it is in no count and no pill.
SIMULATED = "SIMULATED"

#: The class that row is styled with: the theme's "no verdict yet" token.
PENDING_CLASS = "pending"

#: The word and class of a tool row the Session has not scored yet.
WAITING = "…"
WAITING_CLASS = "queued"


@dataclass(frozen=True)
class TestRow:
    """One test's row, as both HTML surfaces show it."""

    model_id: str
    test_key: str | None = None
    #: a ``reporting.status`` status, or None: simulated, not scored
    status: str | None = None
    ref_id: str | None = None
    #: file:// URI of the reference JSON
    ref_file: str | None = None
    report_dir: str | None = None
    field_sources: Mapping[str, str] = field(default_factory=dict)
    worker_id: int | None = None
    elapsed: float | None = None
    started_wall: float | None = None
    worst_score: float | None = None
    n_unscored: int | None = None
    n_vars: int | None = None
    n_vars_passed: int | None = None
    n_warnings: int | None = None
    translation_wall: float | None = None
    sim_wall: float | None = None
    #: the result file's final ``CPUtime`` (Dymola's diagnostic): what Sim
    #: (s) shows, marked, where the run kept no wall time
    sim_cpu: float | None = None
    total_wall: float | None = None
    message: str | None = None
    #: the tool's staged edits on this test; 0 in a report
    edits: int = 0
    #: the tool's "not scored yet": no verdict, and no live run giving one —
    #: the Session has not scored this test (yet)
    waiting: bool = False

    @property
    def status_text(self) -> str:
        if self.status:
            return STATUS_LABELS[self.status]
        return WAITING if self.waiting else SIMULATED

    @property
    def status_class(self) -> str:
        if self.status:
            return STATUS_CLASSES[self.status]
        return WAITING_CLASS if self.waiting else PENDING_CLASS

    @property
    def sim_source(self) -> Literal["wall", "cpu"] | None:
        """Where Sim (s) comes from: the wall the run kept, which always
        wins; else the result file's CPU time; else nothing."""
        if self.sim_wall is not None:
            return "wall"
        return "cpu" if self.sim_cpu is not None else None

    @property
    def sim_time(self) -> float | None:
        """The number Sim (s) shows and sorts on, from ``sim_source``."""
        return self.sim_wall if self.sim_wall is not None else self.sim_cpu

    @property
    def resolution(self) -> str | None:
        """The one source every simulation field came from, "mixed", or
        None when the row carries no provenance."""
        sources = set(self.field_sources.values())
        if not sources:
            return None
        return next(iter(sources)) if len(sources) == 1 else "mixed"


@dataclass(frozen=True)
class Column:
    """One column both surfaces show: what a filter on it means (``kind``)
    and how a row reads (``text``) and sorts (``sort``) there."""

    key: str
    label: str
    kind: Literal["text", "number", "status"]
    #: the cell as a person reads it; "—" when absent
    text: Callable[[TestRow], str]
    #: None sorts last in both directions
    sort: Callable[[TestRow], float | str | None]
    #: the header's tooltip
    title: str | None = None
    #: a small word after a row's text ("cpu") and the cell's tooltip, for
    #: a column whose number may be of more than one kind; None when the
    #: row's needs neither
    tag: Callable[[TestRow], str | None] | None = None
    tip: Callable[[TestRow], str | None] | None = None

    def filter_text(self, row: TestRow) -> str:
        """What a column filter reads: the cell's text, or "" when the row
        has no value here. The cell reads "—" then; a filter that read it
        too would match every empty row on a regex ``.`` or on "—"."""
        return "" if self.sort(row) is None else self.text(row)


#: A column filter's hint by the column's kind: its placeholder and its
#: tooltip. The dashboard's filter row and the tool's Suite table read
#: these, so the two say the same thing about the one filter language
#: (``templates/filters.js``).
FILTER_HINTS: dict[str, tuple[str, str]] = {
    "text": (
        "*Pipe*",
        "Substring match by default; use * or ? for glob (e.g. *.Freq*);"
        " .* above makes it a regex",
    ),
    "number": (
        ">1e-4",
        "A comparison (>1e-4, <=5, =0) or an inclusive range (1..10)",
    ),
}


_DASH = "—"


def _text_col(
    key: str, label: str, get: Callable[[TestRow], Any], title: str | None = None
) -> Column:
    """A text column: the value as is, sorting on its lower-cased text."""
    return Column(
        key=key,
        label=label,
        kind="text",
        text=lambda r: _DASH if get(r) in (None, "") else str(get(r)),
        sort=lambda r: None if get(r) in (None, "") else str(get(r)).lower(),
        title=title,
    )


def _num_col(key: str, label: str, get: Callable[[TestRow], Any], spec: str) -> Column:
    """A number column: the value formatted by *spec*, sorting on the value."""
    return Column(
        key=key,
        label=label,
        kind="number",
        text=lambda r: _DASH if get(r) is None else format(get(r), spec),
        sort=get,
    )


#: Sim (s)'s tooltip where the number is the result file's CPU time.
SIM_CPU_TIP = "CPU time from the result file (no wall time was kept)"


def _nrmse_text(row: TestRow) -> str:
    if row.worst_score is not None:
        return format(row.worst_score, ".4e")
    if row.n_unscored:
        return f"n/a ({row.n_unscored} unscored)"
    return _DASH


COLUMNS: tuple[Column, ...] = (
    _text_col("test", "Key", lambda r: r.test_key),
    _text_col("ref", "Reference", lambda r: r.ref_id),
    _text_col("model", "Test", lambda r: r.model_id),
    Column(
        key="status",
        label="Status",
        kind="status",
        text=lambda r: r.status_text,
        sort=lambda r: STATUS_ORDER.index(r.status) if r.status else None,
    ),
    _text_col(
        "resolution",
        "Resolution",
        lambda r: r.resolution,
        title=(
            "Provenance of stop_time / tolerance / method / number_of_intervals"
            " / output_interval — annotation, test_spec, or default"
        ),
    ),
    _text_col(
        "worker", "Worker", lambda r: None if r.worker_id is None else f"W{r.worker_id}"
    ),
    _num_col("elapsed", "Elapsed (s)", lambda r: r.elapsed, ".1f"),
    Column(
        key="nrmse",
        label="Worst NRMSE",
        kind="number",
        text=_nrmse_text,
        sort=lambda r: r.worst_score,
    ),
    Column(
        key="vars",
        label="Variables",
        kind="number",
        text=lambda r: _DASH if r.n_vars is None else f"{r.n_vars_passed}/{r.n_vars}",
        # The number passed, as the column sorted before it had a filter:
        # ">2" there means "more than 2 variables passed".
        sort=lambda r: None if r.n_vars is None else r.n_vars_passed,
        title=(
            "Variables passed / scored. Sorts and filters on the number "
            "passed: >2 means more than 2 passed"
        ),
    ),
    _num_col("warnings", "Warnings", lambda r: r.n_warnings, "d"),
    _num_col("translate", "Translate (s)", lambda r: r.translation_wall, ".2f"),
    # The wall the run kept; else the result file's CPU time, marked: a
    # CPU time is not a wall (it leaves out what the solver waited on), so
    # a number of either kind says which it is.
    replace(
        _num_col("sim", "Sim (s)", lambda r: r.sim_time, ".2f"),
        tag=lambda r: "cpu" if r.sim_source == "cpu" else None,
        tip=lambda r: SIM_CPU_TIP if r.sim_source == "cpu" else None,
    ),
    _num_col("total", "Total (s)", lambda r: r.total_wall, ".2f"),
    # Keyed "detail": the column-resize follow reads --col-w-detail.
    _text_col("detail", "Message", lambda r: r.message),
)

#: The tool's staged edits: a Suite column, in no report (a report has no
#: staged edits).
EDITS = Column(
    key="edits",
    label="Edits",
    kind="number",
    text=lambda r: f"{r.edits} unsaved" if r.edits else "",
    sort=lambda r: r.edits or None,
    title="Staged edits not yet saved to test_spec.json",
)


def cpu_time(stats: Mapping[str, Any] | None) -> float | None:
    """The final value of the result's ``CPUtime`` diagnostic, as Dymola's
    ``read_result`` folds it into the statistics (DSTF sets
    ``OutputCPUtime := true``); None where the result has none — every
    other backend."""
    value = stats.get("CPUtime") if isinstance(stats, Mapping) else None
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def summary_fields(
    comp: TestComparison,
    *,
    warnings: list | None = None,
    stats: Mapping[str, Any] | None = None,
    ref_id: str | None = None,
    ref_file: Path | None = None,
    field_sources: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """The row-level numbers of *comp*, as the per-test sidecar's
    ``summary`` carries them (bar its ``model_id`` / ``written_at``).

    *warnings* defaults to the comparison's own; *stats* is the result's
    statistics, whose ``timing`` block holds the phase walls and whose
    ``CPUtime`` is the result file's CPU time.
    """
    status, status_class = classify_with_class(comp)
    variables = comp.variables
    warns = comp.warnings if warnings is None else warnings
    # Worst score across the leaves that HAVE one: a sentinel leaf carries
    # inf/nan, and a plain max() let it swallow every real number on the
    # test. ``n_unscored`` is what the column says when nothing finite is
    # left.
    worst, n_unscored = worst_finite_score(variables)
    timing = stats.get("timing") if isinstance(stats, Mapping) else None
    if not isinstance(timing, Mapping):
        timing = {}
    return {
        "status": status,
        "status_text": STATUS_LABELS[status],
        "status_class": status_class,
        "ref_id": ref_id,
        # file:// URL so the dashboard's Reference column can hyperlink
        # directly to the stored reference JSON.
        "ref_file": ref_file.as_uri() if ref_file else None,
        "worst_nrmse": worst,
        "n_unscored": n_unscored,
        "n_vars": len(variables),
        "n_vars_passed": sum(1 for v in variables if v.passed),
        "n_warnings": len(warns) if warns else 0,
        "translation_wall": timing.get("translation_wall"),
        "sim_wall": timing.get("sim_wall"),
        "sim_cpu": cpu_time(stats),
        "total_wall": timing.get("total_wall"),
        "field_sources": dict(field_sources or {}),
    }


def row_from_status(entry: Mapping[str, Any]) -> TestRow:
    """A row from one ``status.json`` ``tests[]`` entry — a live row."""
    report_dir = entry.get("report_dir") or ""
    raw = entry.get("status", "queued")
    message = entry.get("detail")
    status = None
    if isinstance(raw, str) and raw in LIVE_STATES:
        status = live_status(raw)
    elif raw is not None:
        # status.json is data: a state no surface has a word for — or no
        # word at all (a list would not even hash) — has no verdict, and
        # says what it was rather than reading SIMULATED. A null says
        # nothing, so the row has no verdict and nothing to add.
        message = f"status {raw!r}" + (f": {message}" if message else "")
    return TestRow(
        model_id=entry.get("model_id") or "",
        test_key=entry.get("test_key"),
        status=status,
        # A live ref_id follows the "ref_NNNN" naming of a report_dir the run
        # pre-populated (backend.ref_id_map); a test with no baseline yet has
        # its test_key there, and no ref_id.
        ref_id=report_dir if report_dir.startswith("ref_") else None,
        report_dir=entry.get("report_dir") or entry.get("test_key"),
        field_sources=entry.get("field_sources") or {},
        worker_id=entry.get("worker_id"),
        elapsed=entry.get("elapsed"),
        started_wall=entry.get("started_wall"),
        message=message,
    )


#: A sidecar's field → the row's.
_SUMMARY_TO_ROW = (
    ("worst_nrmse", "worst_score"),
    ("n_unscored", "n_unscored"),
    ("n_vars", "n_vars"),
    ("n_vars_passed", "n_vars_passed"),
    ("n_warnings", "n_warnings"),
    ("translation_wall", "translation_wall"),
    ("sim_wall", "sim_wall"),
    ("sim_cpu", "sim_cpu"),
    ("total_wall", "total_wall"),
    ("ref_id", "ref_id"),
    ("ref_file", "ref_file"),
    ("field_sources", "field_sources"),
)

_STATUS_BY_LABEL = {label: s for s, label in STATUS_LABELS.items()}


def enrich_row(
    row: TestRow,
    summary: Mapping[str, Any],
    *,
    start_wall: float | None,
    anchor: str = "start_wall",
) -> TestRow:
    """*row* with a ``summary``'s fields, or *row* unchanged when the summary
    is not this run's verdict on this test. Two guards (D91 — see
    ``pipeline.wipe_stale_state_for_scope``):

    1. **model_id mismatch** — the sidecar belongs to another test
       (bookkeeping drift between batch_manifest and reports/).
    2. **Stale timestamp** — its ``written_at`` is older than
       ``start_wall``, when the run that produced this test's result
       started: a prior run's sidecar the wipe did not clear, whose verdict
       must not override this run's. ``anchor`` names that clock in the
       warning — status.json's ``start_wall``, or the manifest entry's
       ``last_run_at`` for a test the last run did not run.

    Both guards print a one-line warning to stderr, so a skipped
    enrichment is visible.
    """
    sidecar_model = summary.get("model_id")
    if sidecar_model and row.model_id and sidecar_model != row.model_id:
        print(
            f"# WARN: sidecar model_id mismatch (expected {row.model_id!r}, "
            f"sidecar has {sidecar_model!r}); ignoring enrichment.",
            file=sys.stderr,
        )
        return row
    written_at = summary.get("written_at")
    if start_wall is not None and written_at is not None and written_at < start_wall:
        print(
            f"# WARN: stale sidecar for {row.model_id!r} "
            f"(written_at={written_at} < {anchor}={start_wall}); "
            f"ignoring enrichment.",
            file=sys.stderr,
        )
        return row
    changes: dict[str, Any] = {
        dst: summary[src] for src, dst in _SUMMARY_TO_ROW if src in summary
    }
    # An empty provenance says nothing: the live row's stands.
    if not changes.get("field_sources", True):
        del changes["field_sources"]
    # The compare phase's verdict replaces the live state. A sidecar written
    # before ``status`` existed carries only the word a person reads. A
    # status outside the vocabulary, or not a word at all, is ignored: the
    # row keeps its own.
    status = summary.get("status")
    if not isinstance(status, str) or not status:
        text = summary.get("status_text")
        status = _STATUS_BY_LABEL.get(text) if isinstance(text, str) else None
    if status in STATUS_LABELS:
        changes["status"] = status
    return replace(row, **changes)
