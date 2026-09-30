"""The Statistics card (U3): every statistic the run and its baseline
recorded, side by side, above the criteria tree. Closed it is one line
naming the rows that changed; open, a row per count or time under its
section, each ending in whatever attaches at STATISTIC (a `+` that adds a
requirement on it) — or, for a row a criterion reads, that criterion's
pill and its requirement in a word or two. The card is a VIEW of the one
tree (Q1 b); it knows no capability by name.

The rows are `Session.statistics` (`reporting.stats_rows`), the row model
the report renders too: a row is "changed" when the criterion its `+`
would add would fail. A refresh rebuilds them only when their SHAPE moved
— the model, the rows' paths, which criteria read them, the root a `+`
lands in (the `TreeCard` rule); a value or a verdict repaints in place."""

from __future__ import annotations

from collections.abc import Sequence
from functools import partial
from typing import TYPE_CHECKING, Any

from nicegui import ui

from ...backends.common.statistics import SUMMARIES
from ...comparison.modes import ScalarConfig
from ...comparison.tree_spec import reads_a_statistic
from ...reporting.stats_rows import SECTION_TITLES, StatRow
from ...reporting.tree_rows import leaves_of
from ..capabilities import Placement
from ..session import Selection
from .actions import InlineHost, RowTarget
from .tree_card import leaf_status
from .widgets import Pill

if TYPE_CHECKING:
    from .test_page import TestPage

NAMED = 3  #: changed entries the closed line names (S6); the rest are counted
#: The node a STATISTIC panel's add acts on: the root, whose END it lands at.
ROOT = "/metrics"


def _entry(row: StatRow) -> tuple[tuple[str, str], int]:
    """What the closed line names ``row`` under, and its rank there: a
    list's `_count` / `_total` / `_max` are ONE entry, the list's (B6),
    ranked in that order; any other row is its own."""
    base, _, field = row.key.rpartition("_")
    if base and field in SUMMARIES:
        return (row.section, base), SUMMARIES.index(field)
    return (row.section, row.key), 0


def line_text(rows: Sequence[StatRow], has_run: bool) -> str:
    """The closed line after "run vs baseline ·": the changed entries —
    counts before times, then in section order; at most `NAMED` named,
    then `+N more` — or why it names none."""
    shown = [r for r in rows if r.kind != "text"]
    if not shown:
        return "nothing recorded"
    if not has_run:
        return "no run result"
    if all(r.baseline is None for r in shown):
        return "no baseline statistics"
    entries: dict[tuple[str, str], tuple[int, StatRow]] = {}
    for row in shown:
        if not row.changed:
            continue
        key, rank = _entry(row)
        if key not in entries or rank < entries[key][0]:
            entries[key] = (rank, row)
    if not entries:
        return "all match"
    sections = list(dict.fromkeys(r.section for r in shown))
    named = sorted(
        (row for _, row in entries.values()),
        key=lambda r: (r.kind != "count", sections.index(r.section)),
    )
    text = ", ".join(f"{r.label} {r.delta}" for r in named[:NAMED])
    more = f", +{len(named) - NAMED} more" if len(named) > NAMED else ""
    return f"{len(named)} changed: {text}{more}"


def summary(leaf: dict) -> str:
    """A scored row's requirement in a word or two (B11): `exact`, `±0.5`,
    `±10%`, `in [a, b]`, `≥ a`, `≤ b`. A value the leaf leaves out is the
    scalar mode's default."""
    p = leaf.get("params") or {}
    lo, hi = p.get("min_value"), p.get("max_value")
    if lo is not None and hi is not None:
        return f"in [{lo:g}, {hi:g}]"
    if lo is not None:
        return f"≥ {lo:g}"
    if hi is not None:
        return f"≤ {hi:g}"
    tol = float(p.get("tolerance", ScalarConfig.tolerance))
    if p.get("tolerance_mode", ScalarConfig.tolerance_mode) == "rel":
        return f"±{tol * 100:g}%"
    return "exact" if tol == 0 else f"±{tol:g}"


def value_text(row: StatRow, value: Any) -> str:
    """A time to 3 significant figures with its unit; a count as it is."""
    if value is None:
        return "—"
    if row.kind == "time":
        return f"{float(value):.3g} s"
    return f"{float(value):g}"


def row_of(quantity: str, paths: Sequence[str] | set[str]) -> str:
    """The row a leaf's quantity is shown on: its own, or — U2's add form
    writes `CPUtime.final` — the top-level statistic's (S2), as
    `statistics_rows` holds it."""
    if quantity in paths:
        return quantity
    name, _, field = quantity.rpartition(".")
    return name if field == "final" and name in paths else quantity


def scored(view: dict | None, paths: Sequence[str]) -> dict[str, list[dict]]:
    """Row path -> the view's leaves reading it, in tree order."""
    held = set(paths)
    out: dict[str, list[dict]] = {}
    for leaf in leaves_of(view):
        params = leaf.get("params") or {}
        if reads_a_statistic(str(leaf.get("metric")), params):
            out.setdefault(row_of(str(params["quantity"]), held), []).append(leaf)
    return out


class _Row:
    """One statistic's row, and what a repaint writes into it."""

    def __init__(
        self,
        row: ui.row,
        run: ui.label,
        base: ui.label,
        delta: ui.label,
        pill: Pill | None = None,
        rule: ui.label | None = None,
        more: ui.label | None = None,
    ) -> None:
        self.row, self.run, self.base, self.delta = row, run, base, delta
        self.pill, self.rule, self.more = pill, rule, more

    def paint(self, stat: StatRow, leaves: list[dict]) -> None:
        self.run.set_text(value_text(stat, stat.run))
        self.base.set_text(value_text(stat, stat.baseline))
        self.delta.set_text(stat.delta)
        if stat.changed:
            self.row.classes(add="dstf-stat-changed")
        else:
            self.row.classes(remove="dstf-stat-changed")
        if self.pill is not None and leaves:
            self.pill.set(leaf_status(leaves[0]))
        if self.rule is not None and leaves:
            self.rule.set_text(summary(leaves[0]))
        if self.more is not None:
            self.more.set_text(f"+{len(leaves) - 1}" if len(leaves) > 1 else "")


class StatsCard:
    def __init__(self, page: TestPage) -> None:
        self.page = page
        self.is_open = False
        self.hosts: list[InlineHost] = []
        self.painters: dict[str, _Row] = {}
        #: Each statistic's row by its path, and the slot under it.
        self.rows_by_path: dict[str, ui.row] = {}
        self.slots: dict[str, ui.column] = {}
        self._shape: tuple = ()
        self._toggle: ui.button | None = None
        self._summary: ui.label | None = None
        with ui.card().classes("dstf-card dstf-stats w-full gap-1").mark("stats"):
            self.line = ui.row().classes("dstf-stats-line items-center gap-3 w-full")
            self.line.mark("stats:line")
            self.rows = ui.column().classes("w-full gap-0").mark("stats:rows")
        # Not refreshed here, as the tree card is not: the page's `sync`
        # fills it, or `forget` leaves it saying nothing.
        self.rows.set_visibility(False)

    def toggle(self) -> None:
        self.is_open = not self.is_open
        self.refresh(self.page._view())

    def forget(self) -> None:
        """The page's test is gone: the card says nothing (`TreeCard.forget`)."""
        for host in self.hosts:
            host.clear()
        self.hosts, self.painters = [], {}
        self.rows_by_path, self.slots = {}, {}
        self.is_open = False
        self._shape = ()
        self.rows.clear()
        self.rows.set_visibility(False)
        self.line.clear()
        self._toggle, self._summary = None, None

    def refresh(self, view: dict | None) -> None:
        """Follow the Session: the line every time, the rows in place unless
        their shape moved. ``view`` is the page's ONE `tree_view` read."""
        session, model = self.page.shell.session, self.page.model_id
        stats = session.statistics(model)
        self._line(stats)
        self.rows.set_visibility(self.is_open)
        if not self.is_open:
            return
        shown = [r for r in stats if r.kind != "text"]
        paths = [r.path for r in shown]
        reads = scored(view, paths)
        tree = session.tree(model)
        shape = (
            model,
            tuple(paths),
            tuple(
                (k, tuple(str(leaf["path"]) for leaf in v)) for k, v in reads.items()
            ),
            # What a `+` lands in, and whether it can: its reason and its
            # sentence read the root.
            None if tree is None else str(tree.get("combinator", "leaf")),
        )
        if shape != self._shape or not self.painters:
            self._shape = shape
            self._build(shown, reads)
            return
        for host in self.hosts:
            # The rows stand; what a `+` MAY do does not (a job's lock).
            host.follow()
        for stat in shown:
            self.painters[stat.path].paint(stat, reads.get(stat.path, []))

    # -- the closed line -------------------------------------------------------

    def _line(self, stats: list[StatRow]) -> None:
        """Built once, repainted in place: a line cleared and rebuilt on
        every sync shrinks the page for a frame, and whatever sits below it
        moves."""
        if self._toggle is None:
            with self.line:
                self._toggle = (
                    ui.button("Statistics", on_click=self.toggle)
                    .props("flat dense no-caps")
                    .mark("stats:toggle")
                )
                ui.label("run vs baseline ·").classes("text-secondary")
                self._summary = (
                    ui.label().classes("text-secondary").mark("stats:summary")
                )
        assert self._summary is not None
        self._toggle.props(f"icon={'expand_more' if self.is_open else 'chevron_right'}")
        self._summary.set_text(line_text(stats, self.page.result() is not None))

    # -- the rows ----------------------------------------------------------------

    def _build(self, shown: list[StatRow], reads: dict[str, list[dict]]) -> None:
        for host in self.hosts:
            host.clear()
        self.hosts, self.painters = [], {}
        self.rows_by_path, self.slots = {}, {}
        self.rows.clear()
        section = None
        with self.rows:
            if shown:
                # The board's columns: the statistic, the run, its baseline.
                with (
                    ui.row()
                    .classes("dstf-stat-row dstf-stat-head items-center no-wrap gap-3")
                    .mark("stats:head")
                ):
                    ui.label("Statistic").classes("dstf-stat-label")
                    ui.label("Run").classes("dstf-stat-num")
                    ui.label("Baseline").classes("dstf-stat-num")
                    ui.element("span").classes("dstf-stat-delta")
                    ui.element("span").classes("dstf-stat-end")
            for stat in shown:
                if stat.section != section:
                    section = stat.section
                    title = SECTION_TITLES.get(
                        section, section.replace("_", " ").capitalize()
                    )
                    ui.label(title).classes("dstf-group").mark("stats:section")
                self._row(stat, reads.get(stat.path, []))

    def _row(self, stat: StatRow, leaves: list[dict]) -> None:
        path, model = stat.path, self.page.model_id
        row = ui.row().classes("dstf-stat-row items-center no-wrap gap-3")
        self.rows_by_path[path] = row
        host: InlineHost | None = None
        target: RowTarget | None = None
        pill: Pill | None = None
        rule: ui.label | None = None
        more: ui.label | None = None
        with row.mark(f"stats:row:{path}"):
            with ui.label(stat.label).classes("dstf-stat-label text-body2"):
                ui.tooltip(path)
            run = ui.label().classes("dstf-stat-num dstf-mono")
            base = ui.label().classes("dstf-stat-num dstf-mono")
            delta = ui.label().classes("dstf-stat-delta text-secondary text-xs")
            with ui.element("span").classes("dstf-stat-end"):
                if leaves:
                    pill = Pill(leaf_status(leaves[0]), f"stats:pill:{path}")
                    rule = ui.label().classes("dstf-mono text-xs")
                    rule.mark(f"stats:rule:{path}")
                    more = ui.label().classes("text-secondary text-xs")
                    more.mark(f"stats:more:{path}")
                else:
                    target = RowTarget(self.page, f"stat:{path}", row, ROOT, None)
                    host = InlineHost(
                        self.page.shell,
                        Placement.STATISTIC,
                        partial(Selection, tests=frozenset({model}), quantity=path),
                        figure=None,
                        marker=f"statistic:{path}",
                        row=target,
                    )
                    host.column.classes(remove="w-full", add="dstf-node-host")
        # The row's slot: a refused add's error, and the editor.
        slot = ui.column().classes("w-full gap-0")
        self.slots[path] = slot
        if host is not None and target is not None:
            target.slot = slot
            host.build()
            self.hosts.append(host)
        self.painters[path] = _Row(row, run, base, delta, pill, rule, more)
        self.painters[path].paint(stat, leaves)
