"""The Variables drawer (the roadmap's 3c; DESIGN.md "The Variables drawer";
the owner's rulings Q5-Q7, 2026-09-25, and Q12-Q14, 2026-09-28): every
variable of the last result in `variable_rows`' groups, folded and
filtered, each row checked against the selected plot. Its filter and its
sort, group and names menus are the tab's (`TabState`), drawn by
`drawer_controls` as the Tests drawer's are. It edits the plot extras: a
check adds a row's members to the selected plot's CONTEXT or takes them
off (the plot's subject is on and grey — a derived plot's subject is the
tree's), "+ new plot" makes a WATCH plot of one variable after the others.
The rules are not here: the rows are data (`authoring.variable_rows`), the
edits pure functions (`authoring.layout`), and the page stages what they
return (`TestPage.stage_layout`).

Rebuilt whole when it opens, and when what it draws moved under the page
it sits on (`Rail.refresh`: a select, a sync), never while closed. So its
check is a button with `role=checkbox`, not a `ui.checkbox`: a checkbox's
`set_value` fires its own `on_change`, and a repaint of one would stage an
edit."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import partial
from typing import TYPE_CHECKING

import regex
from nicegui import ui

from ...backends.types import VariableText
from ...discovery.plots import PlotExtra, ResolvedPlot
from ...discovery.unit_tests import is_unit_test_variable
from ...figures.families import HUES
from ..layout import add_context, new_watch, remove_context
from ..needle import matcher
from ..variable_rows import VarGroup, VarRow, cap, variable_groups
from .drawer_controls import filter_box, menu_button
from .widgets import MiddleCut

if TYPE_CHECKING:
    from .test_page import TestPage

#: At most this many top-level rows (planner's choice 4): a result of
#: thousands of names drew thousands of rows. An opened family shows at most
#: ``MEMBERS`` of its own.
ROWS, MEMBERS = 200, 50

#: A family's swatch marks the row as an array: four blocks, one per stop
#: of the accent ramp (the first hue's, `figures.families`), light to dark,
#: as the owner-approved board drew it — discrete, never a gradient
#: (DESIGN.md "Don't use gradients"). It is not the ramp its members are
#: drawn in: a row is tied to no plot, and a plot gives a family its hue by
#: the family's order on that plot.
_RAMP = HUES[0]


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
    plots: tuple[ResolvedPlot, ...]
    #: the names that are the test's UnitTests elements: rows of their own,
    #: as they are plots of their own (O3)
    unit_tests: frozenset[str]


def _slug(title: str) -> str:
    """A group title as one marker word: NiceGUI splits markers on
    whitespace, and "(top level)" was two (the executing review)."""
    return re.sub(r"[^\w.$]+", "-", title).strip("-")


def _state(row: VarRow) -> tuple[str, str]:
    """``(aria-checked, icon)``: all, some or none of the row's members in
    the selected plot's context — or the plot's subject, which it always
    shows."""
    if row.is_subject or row.on_selected == len(row.members):
        return "true", "check_box"
    if row.on_selected:
        return "mixed", "indeterminate_check_box"
    return "false", "check_box_outline_blank"


class VariablesDrawer:
    def __init__(self, page: TestPage, panel: ui.column) -> None:
        self.page = page
        #: The families opened to their members, by `_family`: kept across
        #: rebuilds, so a check inside an opened family leaves it open. Never
        #: by row id, which carries the group — a family opened By component
        #: closed under Flat — nor by its members, which a filter narrows.
        self.opened: set[tuple[str, bool]] = set()
        #: The test's tracked names, as the last rebuild read them.
        self.tracked: frozenset[str] = frozenset()
        #: The texts two or more of the drawn rows read as.
        self.alike: set[str] = set()
        #: The filter and its toggles are the tab's (`TabState`): a switch
        #: of test builds a new drawer, and it starts from them.
        self.state = page.state
        #: What the last rebuild read, for the clicks on the rows it drew.
        self.texts: dict[str, str] = {}
        #: What the drawn rows were built from (`_signature`): `refresh`
        #: builds again only when it moved.
        self.drawn: object = None
        with panel:
            ui.label("Variables").classes("text-subtitle2")
            state = self.state
            self.box = filter_box(
                state.vars_needle,
                state.vars_regex,
                state.vars_case,
                self._on_filter,
                "filter: substring, or a glob with * ?",
                "var-",
            )
            with ui.row().classes("items-center no-wrap gap-0 w-full"):
                menu_button(
                    "sort",
                    "Sort",
                    (("file", "Result-file order"), ("name", "A→Z")),
                    state.vars_sort,
                    partial(self._set, "vars_sort"),
                    "var-sort",
                )
                menu_button(
                    "account_tree",
                    "Group",
                    (
                        ("component", "By component"),
                        ("flat", "Flat"),
                        ("tracked", "Tracked first"),
                    ),
                    state.vars_group,
                    partial(self._set, "vars_group"),
                    "var-group-by",
                )
                menu_button(
                    "label",
                    "Names",
                    (
                        ("expression", "Expression"),
                        ("file", "Result-file name"),
                        ("both", "Both"),
                    ),
                    state.vars_names,
                    partial(self._set, "vars_names"),
                    "var-names",
                )
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

    def _on_filter(self, needle: str, regex: bool, case: bool) -> None:
        state = self.state
        state.vars_needle, state.vars_regex, state.vars_case = needle, regex, case
        self.rebuild()

    def _set(self, key: str, value: str) -> None:
        """A menu's choice, the tab's (`TabState`)."""
        setattr(self.state, key, value)
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
        """ONE read of each — the names, the tracked names, the texts, the
        plots — or None on a page whose test is gone: every read raises for
        a model the open project does not have."""
        page = self.page
        if page.gone:
            return None
        session, model_id = page.shell.session, page.model_id
        names = session.variable_names(model_id)
        project = session.project
        test = project.test(model_id) if project is not None else None
        return _Reads(
            names=names,
            tracked=session.tracked(model_id),
            scope=session.variable_texts(model_id),
            plots=session.plots(model_id),
            unit_tests=frozenset(
                n for n in names if test is not None and is_unit_test_variable(test, n)
            ),
        )

    def _signature(self, reads: _Reads | None) -> object:
        """Everything a build draws from: the reads, the selected plot and
        the number of plots, the filter and the menus, the families opened,
        and the reason an empty result gives."""
        page = self.page
        head = (
            page.selected,
            len(page.cards),
            self.state.vars_needle,
            self.state.vars_regex,
            self.state.vars_case,
            self.state.vars_group,
            self.state.vars_names,
            self.state.vars_sort,
            frozenset(self.opened),
        )
        if reads is None:
            return head, None
        return head, (
            tuple(reads.names),
            tuple(reads.tracked),
            tuple((n, t.label, t.text) for n, t in reads.scope.items()),
            reads.plots,
            reads.unit_tests,
            "" if reads.names else page.unscored_reason(),
        )

    def _draw(self, reads: _Reads | None) -> None:
        """Build the rows, and record what they were built from only once
        they are: a draw that raised had recorded it first, and every later
        refresh then saw nothing moved and left the cleared body empty. A
        bad regex, or one that ran out of time, is a draw that completed —
        it is drawn as its reason."""
        signature = self._signature(reads)
        self.drawn = None
        self._build(reads)
        self.drawn = signature

    def _build(self, reads: _Reads | None) -> None:
        page = self.page
        self.body.clear()
        if reads is None:
            self.target.set_text("")
            return
        n = len(page.cards)
        self.target.set_text(
            f"Checkboxes add context to plot {page.selected + 1} of {n}"
            if n
            else "No plot yet: + new plot adds one"
        )
        try:
            state = self.state
            keep = matcher(
                state.vars_needle, regex=state.vars_regex, case=state.vars_case
            )
        except regex.error as e:
            self.box.say(f"not a regular expression: {e}")
            return
        self.box.say(None)
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
            self.tracked = frozenset(reads.tracked)
            # Everything but the names and the filter, for the members too.
            groups_of = partial(
                variable_groups,
                tracked=reads.tracked,
                diagnostics=(
                    project.config.diagnostic_variables if project is not None else ()
                ),
                labels={name: t.label for name, t in scope.items()},
                plots=reads.plots,
                unit_tests=reads.unit_tests,
                selected=page.selected if n else None,
                group=self.state.vars_group,
                order=self.state.vars_sort,
                read=self.state.vars_names,
            )
            # `keep` is only ever asked here: a typed regex that ran out of
            # its budget has drawn nothing yet, and draws nothing
            try:
                found = groups_of(names=names, keep=keep)
            except TimeoutError:
                self.box.say("the pattern took too long; simplify it")
                return
            groups, shown, total = cap(found, ROWS)
            drawn = [
                (group, [(row, self._members(row, groups_of)) for row in group.rows])
                for group in groups
            ]
            # The one label rule (`display_label`) over what the drawer
            # draws: a tracked expression `a.Q` beside an untracked variable
            # NAMED `a.Q` is no collision in the test's scope when no plot or
            # criterion holds the second, and two rows read "a.Q".
            reads_as = Counter(
                r.text
                for _, rows in drawn
                for row, members in rows
                for r in (row, *members)
            )
            self.alike = {text for text, n in reads_as.items() if n > 1}
            for group, rows in drawn:
                ui.label(group.title).classes("dstf-group").mark(
                    f"var-group:{group.rank}-{_slug(group.title)}"
                )
                for row, members in rows:
                    self._row(row)
                    for member in members:
                        self._row(member, member=True)
                    if members and len(row.members) > MEMBERS:
                        ui.label(f"… {len(row.members) - MEMBERS} more").classes(
                            "dstf-why dstf-var-member"
                        )
            if shown < total:
                ui.label(
                    f"Showing {shown} of {total:,} rows — narrow the filter"
                ).classes("dstf-why").mark("var-cap")

    def _members(
        self, row: VarRow, groups_of: Callable[..., list[VarGroup]]
    ) -> list[VarRow]:
        """An opened family's members, each drawn as a row of its own under
        it (none for a family not opened). Each is `variable_groups`' row for
        that one name — the same rules, the same state — with an id under its
        family's."""
        if len(row.members) < 2 or self._family(row) not in self.opened:
            return []
        out = []
        for name in row.members[:MEMBERS]:
            (group,) = groups_of(names=[name], keep=lambda _: True)
            (member,) = group.rows
            out.append(replace(member, id=f"{row.id}/{name}"))
        return out

    def _row(self, row: VarRow, *, member: bool = False) -> None:
        page, key = self.page, row.id
        # What is muted beside the row's text: a lone variable's name. A
        # family has none to mute: rows fold by NAME, so its text is its
        # names folded (`file_text`).
        muted = row.name
        said = row.text if muted is None else f"{row.text} ({muted})"
        with (
            ui.row()
            .classes(
                "dstf-var-row items-center no-wrap gap-1 w-full"
                + (" dstf-var-member" if member else "")
            )
            .mark(marker("var-row", key))
        ):
            # Names go on the props dict, never through a props string: a
            # name can hold a `"`. A check's only text was its icon's
            # ligature, `check_box_outline_blank`.
            if len(row.members) > 1:
                opened = self._family(row) in self.opened
                chevron = (
                    ui.button(
                        icon="expand_more" if opened else "chevron_right",
                        on_click=partial(self._open, self._family(row)),
                    )
                    .props("flat dense round size=sm")
                    .mark(marker("var-open", key))
                )
                chevron.props["aria-label"] = f"{'Close' if opened else 'Open'} {said}"
                chevron.props["aria-expanded"] = "true" if opened else "false"
            checked, icon = _state(row)
            # A disabled Quasar button takes no pointer events: the reason's
            # tooltip is on a span around it.
            with ui.element("span").mark(marker("var-why", key)):
                check = (
                    ui.button(icon=icon, on_click=partial(self._check, row))
                    .props(
                        f'flat dense round size=sm role="checkbox" aria-checked="{checked}"'
                    )
                    .mark(marker("var-check", key))
                )
                # A row with a muted name says it too: two rows
                # reading "a.Q" had two checks a screen reader read alike.
                check.props["aria-label"] = said
                if not page.cards:
                    check.disable()  # no plot to act on: "+ new plot" makes one
                elif row.is_subject:
                    # A plot cannot lose its subject: a derived plot's is the
                    # tree's, a watch plot's is the plot.
                    check.disable()
                    ui.tooltip("The plot's subject")
            if len(row.members) > 1:
                with ui.element("span").classes("dstf-var-ramp"):
                    for stop in _RAMP:
                        ui.element("span").style(f"background: {stop}")
            # A real library's names outrun 320 px and are cut with an
            # ellipsis: the text carries all of it, and the name, as its
            # title (on the props dict: a name can hold a `"`).
            # The names menu: the expression alone, the result-file name
            # alone, or both — the expression with the name muted beside it.
            # The expression alone still keeps the name beside a label
            # another name of the test carries (`variable_texts`, the one
            # label rule) or another row drawn here reads (`alike`): two rows
            # reading "a.Q" said nothing of which was which.
            names = self.state.vars_names
            shown = row.file_text if names == "file" else row.text
            text = MiddleCut(shown).classes("dstf-var-text dstf-mono text-xs")
            text.props["title"] = said
            # A family has no one name for the scope test: `alike` is what
            # says it reads as another drawn row does.
            if muted is not None and (
                names == "both"
                or (
                    names == "expression"
                    and (
                        (row.name is not None and self.texts.get(row.name) != row.text)
                        or row.text in self.alike
                    )
                )
            ):
                ui.label(muted).classes("dstf-varname")
            ui.space()
            # These two never wrap (the stylesheet's `.dstf-var-where` rule
            # keeps each to one line and unshrunk): squeezed by a long name,
            # "+ new plot" took two lines and "plot 2, 3" broke after "plot".
            if row.elsewhere:
                ui.label(
                    "plot " + ", ".join(str(i + 1) for i in row.elsewhere)
                ).classes("dstf-why dstf-var-where")
            # A watch plot is of ONE variable, and a variable has one plot
            # at most: grey, with why, on a family's row (its members,
            # opened, each make one) and on any plot's subject.
            why = (
                f"Already the subject of plot {row.subject_of + 1}"
                if row.subject_of is not None
                else "A watch plot shows one variable: open the family"
                if len(row.members) > 1
                else None
            )
            with ui.element("span").mark(marker("var-new-why", key)):
                new = (
                    ui.button("+ new plot", on_click=partial(self._new, row))
                    .props("flat dense no-caps size=sm")
                    .classes("dstf-var-new")
                    .mark(marker("var-new", key))
                )
                if why is not None:
                    new.disable()
                    ui.tooltip(why)

    # -- the edits -----------------------------------------------------------------

    def _family(self, row: VarRow) -> tuple[str, bool]:
        """What an opened family is known by: its key, and whether it is
        tracked. The key alone is not enough — a tracked family whose
        expressions are ``pipe.T[i]`` keys ``pipe.T[]`` as the result's own
        ``pipe.T[i]`` do — and a family is all tracked or all not
        (`variable_rows` folds the two apart)."""
        return row.key, row.members[0] in self.tracked

    def _open(self, family: tuple[str, bool]) -> None:
        if family in self.opened:
            self.opened.discard(family)
        else:
            self.opened.add(family)
        self.rebuild()

    def _check(self, row: VarRow) -> None:
        """The row as CONTEXT on the selected plot: all of its members
        there take them off, else the missing ones are added — never the
        plot's subject.

        ``row`` was drawn before the last change, and the selection may name
        a plot there no longer is: a real double-click raised IndexError on
        its second click once. So the plots are read NOW, a selection
        outside them does nothing, and on or off is what that plot holds
        now — never ``row.on_selected``."""
        page = self.page
        session = page.shell.session
        plots = session.plots(page.model_id)
        if not 0 <= page.selected < len(plots):
            return
        plot = plots[page.selected]
        members = [m for m in row.members if m not in plot.subject]
        if not members:
            return  # the plot's subject: its check is grey
        extras: tuple[PlotExtra, ...] = session.layout(page.model_id)
        if all(m in plot.context for m in members):
            for name in members:
                extras = remove_context(extras, plot, name)
        else:
            extras = add_context(extras, plot, members)
        page.stage_layout(extras)

    def _new(self, row: VarRow) -> None:
        """A WATCH plot of the row's one variable, after every other plot,
        selected. Read NOW: a variable some plot has as its subject — a
        second click before the page caught up with the first — makes
        none."""
        page = self.page
        session = page.shell.session
        plots = session.plots(page.model_id)
        if len(row.members) != 1 or any(row.members[0] in p.subject for p in plots):
            return
        extras = new_watch(session.layout(page.model_id), row.members[0])
        page.stage_layout(extras, select=len(plots))
