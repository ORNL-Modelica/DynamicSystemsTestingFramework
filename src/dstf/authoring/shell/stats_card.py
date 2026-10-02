"""The Statistics card (U3): every statistic the run and its baseline
recorded, side by side, above the criteria tree. Closed it is one line
naming the counts that changed, then briefly the times; open, a row per count or time under its
section, each ending in whatever attaches at STATISTIC (a `+` that adds a
requirement on it) — or, for a row a criterion reads, that criterion's
pill and its requirement in a word or two. The card is a VIEW of the one
tree (Q1 b): which rows are scored is the tree as authored, staged edits
included (`Session.statistic_leaves`); the tree view only says each
criterion's verdict. A statistics criterion is edited under its row, by
whatever attaches at STATISTIC_CRITERION. It knows no capability by name.

The rows are `Session.statistics` (`reporting.stats_rows`), the row model
the report renders too: a row is "changed" when the criterion its `+`
would add would fail. A refresh rebuilds them only when their SHAPE moved
— the model, the rows' paths, which criteria read them, the root a `+`
lands in (the `TreeCard` rule); a value or a verdict repaints in place."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from functools import partial
from typing import TYPE_CHECKING, Any

from nicegui import ui

from ...backends.common.statistics import SUMMARIES
from ...comparison.modes import ScalarConfig
from ...comparison.sides import parse_sides, sides_text, signed_sides
from ...reporting.stats_rows import SECTION_TITLES, StatRow, card_text
from ...reporting.tree_rows import leaves_of
from ..capabilities import Placement
from ..session import Selection
from ..tree_pointer import node_or_none
from .actions import InlineHost, RowTarget
from .tree_card import leaf_status
from .widgets import Pill

if TYPE_CHECKING:
    from ...comparison.tree_spec import LeafSpec
    from .test_page import TestPage

NAMED = 3  #: changed counts the closed line names (S6); the rest are counted
TIMES = 2  #: changed times it names after them (the owner, 2026-09-30)
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


def _moved(row: StatRow) -> float:
    """How far a changed time moved, relative to its baseline: its rank
    on the closed line. Off a zero baseline, farther than any."""
    run, base = float(row.run), float(row.baseline)
    return abs((run - base) / base) if base else math.inf


def _more(named: Sequence[StatRow], cap: int) -> str:
    text = ", ".join(f"{r.label} {r.delta}" for r in named[:cap])
    return f"{text} +{len(named) - cap} more" if len(named) > cap else text


def line_text(rows: Sequence[StatRow], has_run: bool) -> str:
    """The closed line after "run vs baseline ·": the changed COUNTS — in
    section order, at most `NAMED` named, then `+N more` — or `no count
    changed`; then, after a `·`, the changed times, the farthest moved
    first, at most `TIMES`. `all match` where no count and no time
    changed; or why it names none — "no run result" where ``has_run`` is
    false: no run, or one that failed. The owner's ruling
    (2026-09-30): over TRANSFORM CPUtime ran ~2.2x its baseline on 324 of
    331 tests, so a line naming times among the counts named CPUtime on
    every test; the open table still tints a changed time."""
    shown = [r for r in rows if r.kind != "text"]
    if not shown:
        return "nothing recorded"
    if not has_run:
        return "no run result"
    if all(r.baseline is None for r in shown):
        return "no baseline statistics"
    entries: dict[tuple[str, str], tuple[int, StatRow]] = {}
    for row in shown:
        if not row.changed or row.kind != "count":
            continue
        key, rank = _entry(row)
        if key not in entries or rank < entries[key][0]:
            entries[key] = (rank, row)
    sections = list(dict.fromkeys(r.section for r in shown))
    counts = sorted(
        (row for _, row in entries.values()),
        key=lambda r: sections.index(r.section),
    )
    times = sorted(
        (r for r in shown if r.changed and r.kind == "time"), key=_moved, reverse=True
    )
    if not counts and not times:
        return "all match"
    text = (
        f"{len(counts)} changed: {_more(counts, NAMED)}"
        if counts
        else "no count changed"
    )
    return f"{text} · {_more(times, TIMES)}" if times else text


def summary(leaf: dict) -> str:
    """A scored row's requirement in a word or two (B11): `exact`, `±0.5`,
    `±10%`, `in [a, b]`, `≥ a`, `≤ b`; a tolerance whose two sides differ
    reads `−0.5 / +1`, `−5% / +10%`. A value the leaf leaves out is the
    scalar mode's default."""
    p = leaf.get("params") or {}
    lo, hi = p.get("min_value"), p.get("max_value")
    if lo is not None and hi is not None:
        return f"in [{lo:g}, {hi:g}]"
    if lo is not None:
        return f"≥ {lo:g}"
    if hi is not None:
        return f"≤ {hi:g}"
    tol = parse_sides(p.get("tolerance", ScalarConfig.tolerance), "tolerance")
    rel = p.get("tolerance_mode", ScalarConfig.tolerance_mode) == "rel"
    if not tol.equal:
        return (
            signed_sides(tol, "g", scale=100, unit="%") if rel else sides_text(tol, "g")
        )
    if rel:
        return f"±{tol.upper * 100:g}%"
    return "exact" if tol.upper == 0 else f"±{tol.upper:g}"


def cell_text(row: StatRow, value: Any) -> str:
    """A value as the card prints it, rounded (`card_text`); `—` for a
    side that recorded none."""
    return card_text(row.kind, value) or "—"


def row_of(quantity: str, paths: Sequence[str] | set[str]) -> str:
    """The row a leaf's quantity is shown on: its own, or — U2's add form
    writes `CPUtime.final` — the top-level statistic's (S2), as
    `statistics_rows` holds it."""
    if quantity in paths:
        return quantity
    name, _, field = quantity.rpartition(".")
    return name if field == "final" and name in paths else quantity


def scored(
    leaves: Sequence[tuple[LeafSpec, str]],
    view: dict | None,
    paths: Sequence[str],
) -> dict[str, list[dict]]:
    """Row path -> the criteria reading it, in tree order: the AUTHORED
    tree's statistics leaves (``leaves``, `Session.statistic_leaves`), each
    as ``{"path", "params", "status"}`` — its status the view's verdict,
    "pending" where the view has none. Read from the view, a test with
    nothing compared (no baseline: no view) showed a staged criterion's row
    as unscored, and its `+` staged the same leaf again."""
    held = set(paths)
    verdicts = {str(n["path"]): leaf_status(n) for n in leaves_of(view)}
    out: dict[str, list[dict]] = {}
    for leaf, path in leaves:
        out.setdefault(row_of(str(leaf.params["quantity"]), held), []).append(
            {
                "path": path,
                "params": leaf.params,
                "status": verdicts.get(path, "pending"),
            }
        )
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
        self.run.set_text(cell_text(stat, stat.run))
        self.base.set_text(cell_text(stat, stat.baseline))
        self.delta.set_text(stat.delta)
        if stat.changed:
            self.row.classes(add="dstf-stat-changed")
        else:
            self.row.classes(remove="dstf-stat-changed")
        if self.pill is not None and leaves:
            self.pill.set(leaves[0]["status"])
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
        #: The job holding this test when the `+`s were drawn: they grey
        #: under it, and are redrawn when it comes or goes.
        self._lock: str | None = None
        self._toggle: ui.button | None = None
        self._summary: ui.label | None = None
        #: The one open editor: its leaf, its host and box, and the leaf as
        #: its fields show it (an edit staged ELSEWHERE rebuilds them).
        self.open_path: str | None = None
        self.editor: InlineHost | None = None
        self.editor_box: ui.column | None = None
        self._edited: str | None = None
        with ui.card().classes("dstf-card dstf-stats w-full gap-1").mark("stats"):
            self.line = ui.row().classes("dstf-stats-line items-center gap-3 w-full")
            self.line.mark("stats:line")
            self.rows = ui.column().classes("w-full gap-0").mark("stats:rows")
        # Not refreshed here, as the tree card is not: the page's `sync`
        # fills it, or `forget` leaves it saying nothing.
        self.rows.set_visibility(False)

    def toggle(self) -> None:
        self.is_open = not self.is_open
        if not self.is_open and self.open_path is not None:
            # An editor nobody can see is no editor.
            self.page.close_editor()
        self.refresh(self.page._view())

    def forget(self) -> None:
        """The page's test is gone: the card says nothing (`TreeCard.forget`)."""
        self.close_editor()
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
        reads = scored(session.statistic_leaves(model), view, paths)
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
            if self.open_path is not None:
                # The rows its editor hangs under go, as a plot card's
                # rebuild takes its editor: the page's one editor with them.
                self.page.drop_editor()
            self._build(shown, reads)
            return
        lock = self._locked()
        moved, self._lock = lock != self._lock, lock
        for host in self.hosts:
            # The rows stand; what a `+` MAY do does not. A job's lock greys
            # it (a why-line in its place said the same thing on every
            # row) — an answer `available` does not carry, so `follow`
            # cannot see it move.
            if moved:
                host.build()
            else:
                host.follow()
        for stat in shown:
            self.painters[stat.path].paint(stat, reads.get(stat.path, []))
        if self.editor is not None:
            edited = self._edited_now()
            if edited != self._edited:
                # The leaf moved by an edit staged ELSEWHERE (a Discard):
                # fields showing the undone value would stage it back.
                self.editor.build()
                self._edited = edited
            else:
                self.editor.follow()

    def _locked(self) -> str | None:
        """The label of the job holding this test, or None."""
        running = self.page.shell.session.running
        if running is None or self.page.model_id not in running.models:
            return None
        return str(running.label)

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
        # A failed run is no result to compare: it recorded no statistics,
        # and its line read "all match" with nothing on the run's side.
        self._summary.set_text(line_text(stats, self.page.run_state() == "ok"))

    # -- the rows ----------------------------------------------------------------

    def _build(self, shown: list[StatRow], reads: dict[str, list[dict]]) -> None:
        self.close_editor()
        self._lock = self._locked()
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
                    pill = Pill(leaves[0]["status"], f"stats:pill:{path}")
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
                        apply=self.page.apply,
                    )
                    host.column.classes(remove="w-full", add="dstf-node-host")
        if leaves:
            # A scored row's own click opens its (first) criterion's editor
            # under it, or closes it, as a criteria row's does.
            row.classes(add="cursor-pointer")
            row.on(
                "click",
                partial(self.page.toggle_stats_editor, str(leaves[0]["path"])),
            )
        # The row's slot: a refused add's error, and the editor.
        slot = ui.column().classes("w-full gap-0")
        self.slots[path] = slot
        if host is not None and target is not None:
            target.slot = slot
            host.build()
            self.hosts.append(host)
        self.painters[path] = _Row(row, run, base, delta, pill, rule, more)
        self.painters[path].paint(stat, leaves)

    # -- the one open editor -------------------------------------------------

    def row_for(self, leaf_path: str) -> str | None:
        """The row a statistics criterion is shown on, or None — a leaf
        that reads no statistic, or none at that path. The authored tree's,
        as `scored` reads it."""
        session, model = self.page.shell.session, self.page.model_id
        leaf = next(
            (
                leaf
                for leaf, path in session.statistic_leaves(model)
                if path == leaf_path
            ),
            None,
        )
        if leaf is None:
            return None
        paths = [r.path for r in session.statistics(model)]
        return row_of(str(leaf.params["quantity"]), paths)

    def open_editor(self, leaf_path: str, view: dict | None) -> bool:
        """The criterion's editor under its row (the `PlotCard.open_editor`
        pattern, with no plot), the card opened if it was closed. The page
        keeps the one-editor rule: it closes what else is open first."""
        key = self.row_for(leaf_path)
        if key is None:
            return False
        if not self.is_open:
            self.is_open = True
            self.refresh(view)
        # Every row a statistics leaf is shown on is built — `row_for` and
        # `statistics_rows` agree on it (S4: a leaf's quantity always has
        # a row) — so its slot exists.
        slot = self.slots[key]
        self.close_editor()
        model = self.page.model_id
        self.open_path = leaf_path
        self.rows_by_path[key].classes(add="dstf-crit-open")
        with slot:
            self.editor_box = ui.column().classes("dstf-editor gap-2")
        with self.editor_box:
            with ui.row().classes("items-center no-wrap gap-3 w-full"):
                ui.label(
                    "Edits are staged as you make them; Save writes them."
                ).classes("dstf-why")
                ui.space()
                ui.button("Done", on_click=self.page.close_editor).props(
                    "flat dense no-caps"
                ).mark(f"stats-editor-done:{leaf_path}")
            self.editor = InlineHost(
                self.page.shell,
                Placement.STATISTIC_CRITERION,
                partial(Selection, tests=frozenset({model}), leaf_path=leaf_path),
                figure=None,
                marker=f"stats-editor:{leaf_path}",
                on_applied=self._applied,
                apply=self.page.apply,
            )
            self.editor.build()
        self._edited = self._edited_now()
        return True

    def _applied(self, name: str) -> None:
        """A panel's own submit rebuilds every OTHER panel, leaving the one
        being typed in; the leaf recorded as it now stands, so its own edit
        is not read as one staged elsewhere."""
        if self.editor is None:
            return
        self.editor.build(skip=name)
        self._edited = self._edited_now()

    def _edited_now(self) -> str | None:
        """The open editor's leaf as AUTHORED — staged edits included — not
        the view's: a test with nothing compared has no view, and its
        editor never redrew for an edit staged elsewhere."""
        if self.open_path is None:
            return None
        session = self.page.shell.session
        leaf = node_or_none(session.tree(self.page.model_id), self.open_path)
        return None if leaf is None else json.dumps(leaf, sort_keys=True, default=str)

    def close_editor(self) -> None:
        """The editor goes — its column only: the row's slot also holds
        nothing else a close may take."""
        if self.editor is not None:
            self.editor.clear()
        if self.editor_box is not None and not self.editor_box.is_deleted:
            self.editor_box.delete()
        for row in self.rows_by_path.values():
            row.classes(remove="dstf-crit-open")
        self.editor, self.editor_box = None, None
        self.open_path, self._edited = None, None
