"""The Variables drawer (the roadmap's 3c; DESIGN.md "The Variables drawer";
the owner's rulings Q5-Q7, 2026-09-25): every variable of the last result
in `variable_rows`' groups, folded and filtered, each row checked against
the selected plot. It does every layout edit (Q6 A): a check adds a row's
members to the selected plot or takes them off, "+ new plot" puts them on a
plot of their own below it. The rules are not here: the rows are data
(`authoring.variable_rows`), the edits pure functions (`authoring.layout`),
and the page stages what they return (`TestPage.stage_layout`).

Rebuilt whole when it opens, and when what it draws moved under the page
it sits on (`Rail.refresh`: a select, a sync), never while closed. So its
check is a button with `role=checkbox`, not a `ui.checkbox`: a checkbox's
`set_value` fires its own `on_change`, and a repaint of one would stage an
edit."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import partial
from typing import TYPE_CHECKING

from nicegui import ui

from ...backends.types import VariableText
from ...figures.families import HUES
from ..layout import (
    Layout,
    add_series,
    new_plot,
    remove_series,
    stranded,
    stranded_sentence,
)
from ..variable_rows import VarGroup, VarRow, cap, matcher, variable_groups

if TYPE_CHECKING:
    from .test_page import TestPage

#: At most this many top-level rows (planner's choice 4): a result of
#: thousands of names drew thousands of rows. An opened family shows at most
#: ``MEMBERS`` of its own.
ROWS, MEMBERS = 200, 50

#: A family's swatch: the ramp its members are drawn in, the first hue's
#: four stops (`figures.families`), light to dark.
_RAMP = f"background: linear-gradient(90deg, {', '.join(HUES[0])})"


_SPACE = re.compile(r"\s+")


def marker(kind: str, row_id: str) -> str:
    """A row's ``var-*`` marker: ``kind:row_id`` with ALL whitespace taken
    out. NiceGUI splits a marker on whitespace, so ``port[2, 1].C[]``'s was
    two words that ``port[2, 3, 1].C[]``'s marker also carried, and a find
    for the one found both. Every row marker is built here; a test looks one
    up through it."""
    return f"{kind}:{_SPACE.sub('', row_id)}"


@dataclass(frozen=True)
class _Reads:
    """The Session's reads one build draws from, taken once."""

    names: list[str]
    tracked: list[str]
    scope: dict[str, VariableText]
    layout: Layout
    scored: dict[str, int]


def _slug(title: str) -> str:
    """A group title as one marker word: NiceGUI splits markers on
    whitespace, and "(top level)" was two (the executing review)."""
    return re.sub(r"[^\w.$]+", "-", title).strip("-")


def _state(row: VarRow) -> tuple[str, str]:
    """``(aria-checked, icon)``: all, some or none of the row's members on
    the selected plot."""
    if row.on_selected == len(row.members):
        return "true", "check_box"
    if row.on_selected:
        return "mixed", "indeterminate_check_box"
    return "false", "check_box_outline_blank"


class VariablesDrawer:
    def __init__(self, page: TestPage, panel: ui.column) -> None:
        self.page = page
        #: The families opened to their members, by row id: kept across
        #: rebuilds, so a check inside an opened family leaves it open.
        self.opened: set[str] = set()
        self.regex = False
        self.case = False
        #: What the last rebuild read, for the clicks on the rows it drew.
        self.texts: dict[str, str] = {}
        #: What the drawn rows were built from (`_signature`): `refresh`
        #: builds again only when it moved.
        self.drawn: object = None
        with panel:
            ui.label("Variables").classes("text-subtitle2")
            with ui.row().classes("items-center no-wrap gap-1 w-full"):
                self.needle = (
                    ui.input(
                        placeholder="filter: substring, or a glob with * ?",
                        on_change=lambda _: self.rebuild(),
                    )
                    .props("dense outlined clearable debounce=250")
                    .classes("col")
                    .mark("var-needle")
                )
                self.regex_button = self._toggle(
                    ".*", "var-regex", "Regular expression"
                )
                self.case_button = self._toggle("Aa", "var-case", "Match case")
            self.why = ui.label().classes("dstf-why").mark("var-filter:why")
            self.why.set_visibility(False)
            # The plot the checks act on, with the ring its card wears; a
            # click brings that card into view.
            with (
                ui.row()
                .classes("dstf-var-target items-center no-wrap gap-2")
                .on("click", self._reveal)
            ):
                ui.element("span").classes("dstf-var-ring")
                self.target = ui.label().mark("var-target")
            self.body = ui.column().classes("w-full gap-0")
        self.rebuild()

    def _toggle(self, text: str, marker: str, tip: str) -> ui.button:
        button = (
            ui.button(text, on_click=partial(self._flip, marker))
            .props('flat dense no-caps aria-pressed="false"')
            .classes("dstf-mono")
            .mark(marker)
        )
        with button:
            ui.tooltip(tip)
        return button

    def _flip(self, marker: str) -> None:
        if marker == "var-regex":
            self.regex = not self.regex
            self.regex_button.props(f'aria-pressed="{str(self.regex).lower()}"')
        else:
            self.case = not self.case
            self.case_button.props(f'aria-pressed="{str(self.case).lower()}"')
        self.rebuild()

    def _reveal(self) -> None:
        page = self.page
        if 0 <= page.selected < len(page.cards):
            card = page.cards[page.selected].card
            ui.run_javascript(
                f'document.getElementById("{card.html_id}")'
                '?.scrollIntoView({block: "nearest"})'
            )

    # -- the rows ----------------------------------------------------------------

    def rebuild(self) -> None:
        """Every row from the Session as it is now, against the selected
        plot, whether it moved or not: the drawer opening, its filter, a
        family opened."""
        self._draw(self._read())

    def refresh(self) -> None:
        """The page moved (`Rail.refresh`: a select, a sync): build again
        only when what the rows are drawn from did. `TestPage.sync` ends
        here, and a Rerun's five job events rebuilt every row of an open
        drawer five times over a list that had not changed."""
        reads = self._read()
        if self._signature(reads) != self.drawn:
            self._draw(reads)

    def _read(self) -> _Reads | None:
        """ONE read of each — the names, the texts, the layout, the tree —
        or None on a page whose test is gone: every read raises for a model
        the open project does not have."""
        page = self.page
        if page.gone:
            return None
        session, model_id = page.shell.session, page.model_id
        return _Reads(
            names=session.variable_names(model_id),
            tracked=session.tracked(model_id),
            scope=session.variable_texts(model_id),
            layout=session.layout(model_id),
            scored=page.scored(session.tree_view(model_id)),
        )

    def _signature(self, reads: _Reads | None) -> object:
        """Everything a build draws from: the reads, the selected plot and
        the number of plots, the filter, the families opened, and the
        reason an empty result gives."""
        page = self.page
        head = (
            page.selected,
            len(page.cards),
            self.needle.value or "",
            self.regex,
            self.case,
            frozenset(self.opened),
        )
        if reads is None:
            return head, None
        return head, (
            tuple(reads.names),
            tuple(reads.tracked),
            tuple((n, t.label, t.text) for n, t in reads.scope.items()),
            reads.layout,
            tuple(reads.scored.items()),
            "" if reads.names else page.unscored_reason(),
        )

    def _draw(self, reads: _Reads | None) -> None:
        page = self.page
        self.drawn = self._signature(reads)
        self.body.clear()
        if reads is None:
            self.target.set_text("")
            return
        n = len(page.cards)
        self.target.set_text(
            f"Checkboxes act on plot {page.selected + 1} of {n}"
            if n
            else "No plot yet: + new plot adds one"
        )
        try:
            keep = matcher(self.needle.value or "", regex=self.regex, case=self.case)
        except re.error as e:
            self.why.set_text(f"not a regular expression: {e}")
            self.why.set_visibility(True)
            return
        self.why.set_visibility(False)
        names = reads.names
        project = page.shell.session.project
        with self.body:
            if not names:
                reason = page.unscored_reason()
                ui.label(
                    reason
                    if reason == "this test has no result"
                    else "the result has no variables"
                ).classes("dstf-why").mark("var-empty")
                return
            scope = reads.scope
            self.texts = {name: t.text for name, t in scope.items()}
            # Everything but the names and the filter, for the members too.
            groups_of = partial(
                variable_groups,
                tracked=reads.tracked,
                diagnostics=(
                    project.config.diagnostic_variables if project is not None else ()
                ),
                labels={name: t.label for name, t in scope.items()},
                texts=self.texts,
                layout=reads.layout,
                selected=page.selected if n else None,
                scored=reads.scored,
            )
            groups, shown, total = cap(groups_of(names=names, keep=keep), ROWS)
            for group in groups:
                ui.label(group.title).classes("dstf-group").mark(
                    f"var-group:{group.rank}-{_slug(group.title)}"
                )
                for row in group.rows:
                    self._row(row)
                    if len(row.members) > 1 and row.id in self.opened:
                        self._members(row, groups_of)
            if shown < total:
                ui.label(
                    f"Showing {shown} of {total:,} rows — narrow the filter"
                ).classes("dstf-why").mark("var-cap")

    def _members(self, row: VarRow, groups_of: Callable[..., list[VarGroup]]) -> None:
        """An opened family's members, each a row of its own under it. Each
        is `variable_groups`' row for that one name — the same rules, the
        same state — with an id under its family's."""
        for name in row.members[:MEMBERS]:
            (group,) = groups_of(names=[name], keep=lambda _: True)
            (member,) = group.rows
            self._row(replace(member, id=f"{row.id}/{name}"), member=True)
        if len(row.members) > MEMBERS:
            ui.label(f"… {len(row.members) - MEMBERS} more").classes(
                "dstf-why dstf-var-member"
            )

    def _row(self, row: VarRow, *, member: bool = False) -> None:
        page, key = self.page, row.id
        with (
            ui.row()
            .classes(
                "dstf-var-row items-center no-wrap gap-1 w-full"
                + (" dstf-var-member" if member else "")
            )
            .mark(marker("var-row", key))
        ):
            if len(row.members) > 1:
                ui.button(
                    icon="expand_more" if key in self.opened else "chevron_right",
                    on_click=partial(self._open, key),
                ).props("flat dense round size=sm").mark(marker("var-open", key))
            checked, icon = _state(row)
            # A disabled Quasar button takes no pointer events: the reason's
            # tooltip is on a span around it, as on Remove plot.
            with ui.element("span").mark(marker("var-why", key)):
                check = (
                    ui.button(icon=icon, on_click=partial(self._check, row))
                    .props(
                        f'flat dense round size=sm role="checkbox" aria-checked="{checked}"'
                    )
                    .mark(marker("var-check", key))
                )
                if not page.cards:
                    check.disable()  # no plot to act on: "+ new plot" makes one
                elif row.stranded:
                    check.disable()
                    # Wrapped, as Remove plot's: one line ran over the plots.
                    ui.tooltip(self._sentence(row.stranded, row.criteria)).props(
                        "max-width=320px"
                    )
            if len(row.members) > 1:
                ui.element("span").classes("dstf-var-ramp").style(_RAMP)
            ui.label(row.text).classes("dstf-var-text dstf-mono text-xs")
            if row.name is not None:
                ui.label(row.name).classes("dstf-varname")
            ui.space()
            if row.elsewhere:
                ui.label(
                    "plot " + ", ".join(str(i + 1) for i in row.elsewhere)
                ).classes("dstf-why")
            ui.button("+ new plot", on_click=partial(self._new, row)).props(
                "flat dense no-caps size=sm"
            ).classes("dstf-var-new").mark(marker("var-new", key))

    def _sentence(self, names: tuple[str, ...] | list[str], criteria: int) -> str:
        return stranded_sentence([self.texts.get(n, n) for n in names], criteria)

    # -- the edits -----------------------------------------------------------------

    def _open(self, key: str) -> None:
        if key in self.opened:
            self.opened.discard(key)
        else:
            self.opened.add(key)
        self.rebuild()

    def _check(self, row: VarRow) -> None:
        """All of the row on the selected plot: take it off, unless that
        leaves a scored variable on no plot (the owner's ruling, Q7 A — the
        row is already grey then; this is the belt to its braces). Otherwise
        add what is missing."""
        page = self.page
        if not page.cards:
            return
        session = page.shell.session
        layout = session.layout(page.model_id)
        if row.on_selected == len(row.members):
            new = remove_series(layout, page.selected, row.members)
            scored = page.scored(session.tree_view(page.model_id))
            stuck = stranded(layout, new, list(scored))
            if stuck:
                ui.notify(
                    self._sentence(stuck, sum(scored[v] for v in stuck)),
                    type="warning",
                )
                return
        else:
            new = add_series(layout, page.selected, row.members)
        page.stage_layout(new)

    def _new(self, row: VarRow) -> None:
        """A plot of the row's members below the selected one, selected
        (planner's choice 1); the members stay where they were."""
        page = self.page
        layout = page.shell.session.layout(page.model_id)
        new, at = new_plot(layout, page.selected if page.cards else None, row.members)
        page.stage_layout(new, select=at)
