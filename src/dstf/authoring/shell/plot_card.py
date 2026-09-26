"""One plot of the Test page (brief §3.2; DESIGN.md "Plots are modular",
"Criteria rows"): its title and status pills, the slot the plot is drawn
into when it scrolls into view, the criteria rows of its tracked series
with each row's node controls, the one open criterion's editor below its
row, and what attaches at the VARIABLE placement below. A card owns its
`ui.plotly`, its `FigureHook` and its snap-back; the card knows no
capability by name.

A card is a 320 px slot until `show()` is called — by the browser's
`dstf_plot_visible`, by an opening editor, or by a test. Nothing on the
page draws a figure before that: a test with a dozen variables would
otherwise assemble a dozen figures nobody has scrolled to."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from functools import partial
from typing import TYPE_CHECKING, Any

from nicegui import ui

from ...backends.types import VariableText
from ...comparison import registry
from ...discovery.plots import ResolvedPlot
from ...figures.families import family_label, group_families
from ...figures.plotly import _LABELS, Visibility
from ..capabilities import Placement
from ..layout import Layout, remove_plot, stranded, stranded_sentence
from ..session import Selection
from ..tree_text import leaf_name, leaves_of, parent_kinds
from .actions import ActionBar, InlineHost, RowTarget
from .figure_hook import FigureHook
from .tree_card import leaf_readout, leaf_status
from .widgets import Pill

if TYPE_CHECKING:
    from .test_page import TestPage

logger = logging.getLogger(__name__)

#: Worst first: a card's pill is the worst of its variable's criteria, never
#: the first one's (a passing leaf above a failing one must not read PASS).
_WORST = ("fail", "warn", "pass", "queued")

#: What a leaf contributes to a plot's signature: everything a staged edit
#: can move in what the plot DRAWS — its geometry (the metric and its
#: parameters, the window it is scored over, the baseline it is scored
#: against) and its verdict, which the row beside the plot reads out. Not
#: the trajectories: no leaf sees a rerun, which forces its own redraw.
_SIGNED = (
    "metric",
    "params",
    "window",
    "against",
    "passed",
    "score_display",
    "criterion",
    "error",
)

#: What the OPEN editor's fields were built from: the leaf at the open path,
#: as the page's tree view showed it. Its verdict is not in it — a rerun
#: changes no field — and nothing else in the document is either: comparing
#: the whole document's pending ops meant an edit to ANOTHER model, followed
#: by any `job` event, rebuilt this editor and threw away a half-typed table
#: row; and a combinator edit that moved no leaf rebuilt it for nothing (the
#: 3b review). ``advisory`` is the one key no test pins: nothing the editor
#: offers writes it, so a change to it can only arrive from a hand-written
#: patch — it is here because the field IS the leaf's, not because a path
#: was found that moves it.
_EDITED = ("metric", "variable", "params", "window", "against", "advisory")

#: "Not read yet" — `None` cannot mean it: a test with nothing scored has a
#: `tree_view` of None, and a card that read it for itself cost one read per
#: shown card on every row click (the 3b review).
UNREAD: Any = object()


def worst(statuses: list[str]) -> str:
    return next((status for status in _WORST if status in statuses), "queued")


def drawn(leaf: dict, choice: bool | None, is_open: bool) -> bool:
    """Whether one criterion draws on its plot (legend b). The open one
    always does — the plot is its editing surface. Else the swatch's choice,
    when there is one; else a failing criterion does, so a FAIL never starts
    hidden; else the metric's own default."""
    if is_open:
        return True
    if choice is not None:
        return choice
    if leaf.get("passed") is False:
        return True
    desc = registry.metric(str(leaf.get("metric")))
    return desc is None or desc.drawn_by_default


def folded(
    names: Sequence[str],
    labels: Mapping[str, str] | None = None,
    by: Mapping[str, str] | None = None,
) -> str:
    """``names`` as a plot names them, the way the report's plot titles do
    (`report_suite`): each array family one entry, READ through ``labels``
    (``pipe.T[1:3]``), the fold taken over ``by`` — ``labels`` when not
    given — so two series whose texts collide are no family. The card's
    title, its muted names line, its plot-only line and the focus bar all
    name a plot through this, so they fold alike: the focus bar read the
    unfolded texts while the title above it folded them."""
    fold_over = labels if by is None else by
    return ", ".join(
        family_label(key, members, labels)
        for key, members in group_families(names, fold_over)
    )


#: The swatch's tooltip: what a click on it would do, or why it does nothing.
_SWATCH_TIPS = {
    "open": "the open criterion always draws",
    True: "Hide this criterion's drawing",
    False: "Show this criterion's drawing",
}
_SWATCH_STATES = " ".join(
    f"dstf-swatch-{s}"
    for s in ("on", "off", "locked", "pass", "fail", "warn", "queued")
)


class CriterionRow:
    """One criterion under its plot (DESIGN.md "Criteria rows"): the swatch
    that shows or hides its drawing, the pill, the metric and its variable,
    the CLI's readout, and that node's controls — whatever attaches at NODE.
    `paint` writes a new verdict into the row that is already there."""

    def __init__(self, card: PlotCard, leaf: dict, taker: str | None) -> None:
        index, path = card.index, str(leaf["path"])
        self.card, self.leaf = card, leaf
        #: The parent's combinator the controls were built under: it takes
        #: the child `+` adds, so a kind that moves rebuilds them (`paint`).
        self.taker = taker
        self.path, self.variable = path, str(leaf.get("variable"))
        model, variable = card.page.model_id, self.variable
        # The row WRAPS: the stylesheet's `.dstf-crit { flex-wrap: wrap }`,
        # emitted after Quasar's, overrides the `no-wrap` class below (a
        # long name keeps one line; the readout and controls move under it).
        self.row = ui.row().classes("dstf-crit items-center no-wrap gap-3 w-full")
        # The row IS the way in: its editor opens below it, and a second
        # click closes it — or closes a form row open under it and opens
        # nothing (board 1A). A click on the node's controls inside the row
        # is not a click on the row — `InlineHost`'s column stops it.
        self.row.on("click", partial(card.page.row_click, index, path))
        with self.row.mark(f"criterion:{index}:{path}"):
            # The criterion's drawing on the plot above, shown or hidden
            # (legend b). Its click is stopped in the BROWSER before it
            # reaches the row, which would open the editor too; the user
            # simulation's click never bubbles, so only Chromium can say so.
            # A button to the keyboard as well: Tab reaches it, and Enter or
            # Space toggles it the way a click does (Space's default would
            # scroll the page).
            self.swatch = (
                ui.element("span")
                .classes("dstf-swatch")
                .props('role=button tabindex="0"')
                .mark(f"swatch:{index}:{path}")
                .on(
                    "click",
                    self._toggle,
                    js_handler="(e) => { e.stopPropagation(); emit(); }",
                )
                .on(
                    "keydown",
                    self._toggle,
                    js_handler=(
                        "(e) => { if (e.key === 'Enter' || e.key === ' ') "
                        "{ e.preventDefault(); e.stopPropagation(); emit(); } }"
                    ),
                )
            )
            with self.swatch:
                self.swatch_tip = ui.tooltip("")
            # Hidden until the plot is drawn: which leaves draw anything is
            # known only from the figure (`PlotCard.drawable`).
            self.swatch.set_visibility(False)
            self.pill = Pill(leaf_status(leaf), f"criterion-pill:{index}:{path}")
            self.name = ui.label().classes(
                "dstf-crit-name text-body2 text-weight-medium"
            )
            self.readout = ui.label().classes("dstf-mono text-xs")
            self.readout.mark(f"criterion-readout:{index}:{path}")
            ui.space()
            self.target = RowTarget(card.page, f"crit:{index}:{path}", self.row, path)
            self.host = InlineHost(
                card.page.shell,
                Placement.NODE,
                partial(
                    Selection,
                    tests=frozenset({model}),
                    variable=variable,
                    node_path=path,
                ),
                figure=None,
                marker=f"node:{index}:{path}",
                row=self.target,
            )
            # A host drawn inside a row keeps to its own width, or the
            # cluster takes the rest of the row and sits left of the end
            # (the 3b review, in Chromium).
            self.host.column.classes(remove="w-full", add="dstf-node-host")
        # The row's slot: its editor, or a form row — one at a time. The
        # host is built once its panels have somewhere to open a row.
        self.slot = ui.column().classes("w-full gap-0")
        self.target.slot = self.slot
        self.host.build()
        self.paint(leaf)

    def paint(self, leaf: dict) -> None:
        self.pill.set(leaf_status(leaf))
        self.name.set_text(leaf_name(leaf))
        self.readout.set_text(leaf_readout(leaf))
        self.paint_swatch(leaf)

    def paint_swatch(self, leaf: dict) -> None:
        """Filled in the pill's status colour while the drawing shows, an
        outline of it while hidden; locked while this criterion is open. A
        verdict that flips moves a default, so every paint repaints it."""
        self.leaf = leaf
        card = self.card
        is_open = card.is_open(self.path)
        on = drawn(leaf, card.choices.get(self.path), is_open)
        self.swatch.set_visibility(self.path in card.drawable)
        # ONE call: a remove and then an add are two changes to the element
        # even when they cancel, and a no-op sync re-sent every swatch.
        self.swatch.classes(
            remove=_SWATCH_STATES,
            add=f"dstf-swatch-{'on' if on else 'off'} dstf-swatch-{leaf_status(leaf)}"
            + (" dstf-swatch-locked" if is_open else ""),
        )
        if is_open:
            self.swatch.props("aria-disabled=true")
        else:
            self.swatch.props(remove="aria-disabled")
        self.swatch_tip.set_text(_SWATCH_TIPS["open" if is_open else on])

    def _toggle(self) -> None:
        card = self.card
        if card.is_open(self.path):
            return  # the open criterion always draws: it is the editing surface
        card.choices[self.path] = not drawn(
            self.leaf, card.choices.get(self.path), False
        )
        # By the signature, which carries the hidden set: one send, and only
        # of this card.
        card.redraw_if_moved()


class PlotCard:
    def __init__(
        self,
        page: TestPage,
        index: int,
        resolved: ResolvedPlot,
        view: dict | None,
        tracked: Sequence[str],
        scope: Mapping[str, VariableText],
        layout: Layout,
        scored: Mapping[str, int],
    ) -> None:
        """``view``, ``tracked``, ``scope``, ``layout`` and ``scored`` (a
        variable's number of criteria) are the page's ONE read of each,
        handed to every card: a card that read them for itself cost one read
        per plot on every rebuild (the Task 5-6 review)."""
        self.page, self.index, self.resolved = page, index, resolved
        self.plot: ui.plotly | None = None
        # Created here, attached in `show()`: a panel hosted under this card
        # subscribes before there is a plot to attach to.
        self.hook = FigureHook(on_shape_moved=self.redraw)
        self.rows: dict[str, CriterionRow] = {}
        self.pills: dict[str, Pill] = {}
        self.bars: list[ActionBar] = []
        self.editor: InlineHost | None = None  # the open criterion's, if it is here
        #: The open editor's own column in its row's slot: the slot also
        #: holds the row host's error labels, so closing takes only this.
        self.editor_box: ui.column | None = None
        self.open_path: str | None = None
        self._drawn: str | None = None  # the signature of what the plot shows
        #: Whether this card has been asked to draw. A plot that raised is
        #: retried on the next sync, and only on a card somebody wanted.
        self.wanted = False
        #: The text of the error the last try raised, or None. The retry is
        #: free; the traceback is not — a card that cannot draw is tried
        #: again by every sync, a rescan's progress ticks included, and 50
        #: of those over two failing cards logged 100 tracebacks for one
        #: broken result (the final review). A new text is a new line.
        self._error: str | None = None
        #: Stamped on every figure this card sends; see `_figure`.
        self._revision = 0
        #: The leaf the open editor was built from (`_EDITED`).
        self._edited: tuple | None = None
        #: Legend b: the swatches' choices by leaf path (a leaf with none
        #: follows `drawn`'s defaults), the one error series shown, and the
        #: leaves the last figure drew anything for — only those get a
        #: swatch. Set before the rows, which paint their swatch from them.
        self.choices: dict[str, bool] = {}
        self.error: str | None = None
        self.drawable: frozenset[str] = frozenset()
        self.error_select: ui.select | None = None
        model = page.model_id
        known = set(tracked)
        #: This plot's TRACKED series — the ones that carry criteria rows and
        #: an "Add a criterion for x" footer. Not "scored": a tracked variable
        #: no criterion covers is still the test's, and the footer is the way
        #: back to one. The rest of the series are drawn and nothing more.
        self.tracked = [name for name in resolved.series if name in known]
        #: A card drawing ONE series has its variable in its title, so its
        #: pill is the bare status; several pills side by side read "FAIL
        #: FAIL" and look like the plot's verdict said twice (the board).
        self.named_pills = len(resolved.series) > 1
        with (
            ui.card()
            .classes("dstf-card dstf-plot w-full")
            .mark(f"plot:{index}") as card
        ):
            self.card = card
            # A click anywhere on the card selects its plot — the toolbar,
            # the title, a criteria row — except on the plot itself, which
            # takes zooms and handle drags (3c, planner's choice 8). The guard is
            # the browser's: the user simulation calls every click listener
            # and ignores `js_handler`.
            card.on(
                "click",
                partial(page.select, index),
                js_handler=(
                    "(e) => { if (!e.target.closest('.dstf-plot-slot')) emit(); }"
                ),
            )
            # The card's header row WRAPS: the title with its pills, and the
            # toolbar as one unit, right-aligned on the title's line when it
            # fits and on a line of its own when not. A row that neither
            # wrapped nor shrank put Remove plot at 1140-1202 px of a 900 px
            # window beside a drawer, on a real library's long names.
            with ui.row().classes("dstf-card-head items-center gap-3 w-full"):
                # The title READS the plain labels (the expressions the
                # model wrote); the names it is keyed by ride beside it,
                # muted, and only when one of them differs — a card for
                # `x` would otherwise say `x x`. The muted name is what
                # tells two series sharing one expression apart HERE, so
                # the title does not also carry it in parentheses.
                labels = {
                    n: scope[n].label if n in scope else n for n in resolved.series
                }
                #: What each of this card's series READS as everywhere the
                #: name is NOT beside it — its pill, the focus bar, its
                #: plot-only line, its "Add a criterion for x" button.
                #: Disambiguated over the whole TEST, so all of those agree.
                self.texts = {
                    n: scope[n].text if n in scope else n for n in resolved.series
                }
                # Array families fold, as the report's plot titles do
                # (`report_suite`): the fold over the texts, so two series
                # whose labels collide are no family; each family READS its
                # labels' slice (`pipe.T[1:3]`), and the muted line folds
                # the names the same way.
                series = list(resolved.series)
                with ui.row().classes("dstf-card-title items-center gap-3"):
                    ui.label(folded(series, labels, self.texts)).classes(
                        "text-subtitle2"
                    ).mark(f"plot-title:{index}")
                    if any(labels[n] != n for n in series):
                        ui.label(folded(series)).classes("dstf-varname").mark(
                            f"plot-name:{index}"
                        )
                    for variable in self.tracked:
                        if self._leaves(view, variable):
                            pill = Pill("queued", f"plot-pill:{index}:{variable}")
                            if self.named_pills:
                                # The name keeps its own case: the pill's
                                # text is uppercased by the stylesheet, and
                                # `X FAIL` names no variable of this test.
                                pill.label.classes(add="dstf-pill-named")
                            self.pills[variable] = pill
                # The plot's whole toolbar (decision 9): the legend toggles
                # the signals and the overlays; the criteria are toggled
                # from their rows (legend b) and the errors from this select,
                # built once the plot is drawn.
                with ui.row().classes("dstf-card-tools items-center no-wrap gap-3"):
                    self.error_slot = ui.row().classes("items-center gap-0")
                    self.focus = ui.button("Focus", on_click=partial(page.focus, index))
                    self.focus.props("flat dense no-caps").mark(f"focus:{index}")
                    ui.button("Add series", on_click=self._add_series).props(
                        "flat dense no-caps"
                    ).mark(f"add-series:{index}")
                    self._remove_button(index, tuple(layout), scored)
            if resolved.missing:
                ui.label(f"Not in this result: {', '.join(resolved.missing)}").classes(
                    "dstf-why"
                ).mark(f"plot-missing:{index}")
            self.slot = ui.element("div").classes("dstf-plot-slot w-full")
            self.slot.props(f'data-plot="{index}"').mark(f"plot-slot:{index}")
            criteria = ui.column().classes("dstf-criteria w-full gap-0")
            takers = parent_kinds(view)
            with criteria.mark(f"criteria:{index}"):
                for variable in self.tracked:
                    for leaf in self._leaves(view, variable):
                        path = str(leaf["path"])
                        self.rows[path] = CriterionRow(self, leaf, takers.get(path))
            only = [name for name in resolved.series if name not in known]
            if only:
                verb = (
                    "is plot only: it has"
                    if len(only) == 1
                    else "are plot only: they have"
                )
                # Read as the texts: a plot-only variable NAMED like a
                # tracked one's expression must not read the same as the
                # criteria row above it. A family is one entry, as in the
                # report's plot-only lines.
                texts = folded(only, self.texts)
                ui.label(f"{texts} {verb} no criteria.").classes("dstf-why").mark(
                    f"plot-only:{index}"
                )
            if self.tracked:
                # ONE wrapping row for every variable's footer: one full-width
                # row each pushed the next plot off the screen (the board).
                with ui.row().classes("items-center gap-2 wrap"):
                    for variable in self.tracked:
                        self.bars.append(
                            ActionBar(
                                page.shell,
                                Placement.VARIABLE,
                                partial(
                                    Selection,
                                    tests=frozenset({model}),
                                    variable=variable,
                                    # The page's one read of the scope, so
                                    # the footer's caption reads this
                                    # variable's text without building it
                                    # per card (`Selection.texts`).
                                    texts=scope,
                                ),
                                marker=f"variable-actions:{index}:{variable}",
                                figure=self.hook,
                            )
                        )
        self.paint(view)

    def _remove_button(
        self, index: int, layout: Layout, scored: Mapping[str, int]
    ) -> None:
        """Remove plot, grey with the reason when it would leave a scored
        variable on no plot (the owner's ruling, Q7 A; 3c, planner's choice 9).
        A disabled Quasar button takes no pointer events, so the reason's
        tooltip is on a span around it."""
        after = remove_plot(layout, index)
        stuck = stranded(layout, after, list(scored))
        with ui.element("span").mark(f"remove-plot:{index}:why"):
            # Its click stops in the BROWSER: bubbling to the card selected
            # it, and after the rebuild the ring sat on whichever plot moved
            # into the removed one's place.
            button = (
                ui.button("Remove plot")
                .props("flat dense no-caps")
                .mark(f"remove-plot:{index}")
                .on(
                    "click",
                    partial(self.page.remove_plot, index, after),
                    js_handler="(e) => { e.stopPropagation(); emit(); }",
                )
            )
            if stuck:
                button.disable()
                # Wrapped at 320 px: unbounded, the sentence ran as one line
                # across the plot area (3c's LOOK, at 1280 px).
                ui.tooltip(
                    stranded_sentence(
                        [self.texts.get(v, v) for v in stuck],
                        sum(scored[v] for v in stuck),
                    )
                ).props("max-width=320px")

    def _add_series(self) -> None:
        """Select this plot and open the Variables drawer on it (Q5 A)."""
        self.page.select(self.index)
        self.page.rail.open("variables")

    @staticmethod
    def _leaves(view: dict | None, variable: str) -> list[dict]:
        return [leaf for leaf in leaves_of(view) if leaf.get("variable") == variable]

    def _view(self, view: Any = UNREAD) -> dict | None:
        """The page's read of this sync, or one of this card's own when it
        was not given one."""
        if view is UNREAD:
            return self.page.shell.session.tree_view(self.page.model_id)
        return view

    def _edited_now(self, view: dict | None) -> tuple | None:
        """The leaf under the open editor, as ``view`` shows it — what the
        editor's fields say. ``None`` when no editor is open, or when the
        leaf it was opened on is no longer in the tree (a shape change,
        which rebuilds the cards anyway)."""
        if self.open_path is None:
            return None
        leaf = next(
            (n for n in leaves_of(view) if str(n["path"]) == self.open_path), None
        )
        if leaf is None:
            return None
        return tuple(
            json.dumps(leaf.get(key), sort_keys=True, default=str) for key in _EDITED
        )

    def paint(self, view: dict | None) -> None:
        """Pills, rows and footers in place, from the page's one `tree_view`
        read: a verdict that flips must not cost the card it sits in."""
        for variable, pill in self.pills.items():
            status = worst([leaf_status(leaf) for leaf in self._leaves(view, variable)])
            # The pill READS the label, like the title above it: `x[1] FAIL`
            # on a TRANSFORM card named nothing a person recognises.
            text = (
                f"{self.texts.get(variable, variable)} {status.upper()}"
                if self.named_pills
                else None
            )
            pill.set(status, text)
        self._paint_rows(view)
        takers = parent_kinds(view)
        for path, row in self.rows.items():
            if takers.get(path) != row.taker:
                # The row stands, but its parent changed kind — `+` grey
                # under a warn or a weighted — and the controls read it
                # when they are built (the Task 6 review): an edit from
                # weighted to and left `+` grey with the weighted reason.
                row.taker = takers.get(path)
                row.host.build()
            else:
                # What its node's controls MAY do moves too (a job's lock
                # comes and goes without the shape moving).
                row.host.follow()
        if self.editor is not None:
            edited = self._edited_now(view)
            if edited != self._edited:
                # The leaf under the open editor moved by an edit staged
                # ELSEWHERE — a Discard, a Save, another node's controls.
                # The fields kept showing the value that edit undid, and the
                # next keystroke staged it straight back (the Task 6 review).
                self.editor.build()
                self._edited = edited
            else:
                # The editor is not rebuilt while it is open, so a lock that
                # greys its panels has to reach them here.
                self.editor.follow()
        for bar in self.bars:
            bar.refresh()

    def _paint_rows(self, view: dict | None) -> None:
        for leaf in leaves_of(view):
            row = self.rows.get(str(leaf["path"]))
            if row is not None:
                row.paint(leaf)

    # -- the plot ------------------------------------------------------------------

    def _leaves_all(self, view: dict | None) -> list[dict]:
        """Every leaf on this plot's series, tracked or not."""
        return [
            leaf
            for leaf in leaves_of(view)
            if leaf.get("variable") in self.resolved.series
        ]

    def is_open(self, path: str) -> bool:
        """Whether ``path`` is the open criterion — the page's word, the ONE
        source the rings read too. Not `open_path`: that is set after
        `show()` draws, so the first open of a hidden criterion on an
        undrawn card sent it hidden and then, at once, drawn."""
        return path in self.page.editing_for(self.index)

    def visibility(self, view: Any = UNREAD) -> Visibility:
        """What this card chose to show (legend b): the leaves `drawn` says
        are off, and the one error series."""
        hidden = frozenset(
            str(leaf["path"])
            for leaf in self._leaves_all(self._view(view))
            if not drawn(
                leaf,
                self.choices.get(str(leaf["path"])),
                self.is_open(str(leaf["path"])),
            )
        )
        return Visibility(hidden=hidden, error=self.error)

    def _drawable(self, figure: dict, view: dict | None) -> frozenset[str]:
        """The leaves this figure draws ANYTHING for: a trace with the path
        in ``meta.leaf``, or a shape named under it. A windowed nrmse draws
        one shape and no trace, and its swatch must still show. A shape is
        matched on its path plus ``/``, so ``/metrics/children/1`` never
        claims a shape of ``/metrics/children/10``."""
        paths = [str(leaf["path"]) for leaf in self._leaves_all(view)]
        found = {(tr.get("meta") or {}).get("leaf") for tr in figure["data"]}
        for shape in figure["layout"].get("shapes", []):
            name = str(shape.get("name", ""))
            hit = next((p for p in paths if name.startswith(p + "/")), None)
            if hit is not None:
                found.add(hit)
        return frozenset(str(p) for p in found if p is not None)

    def _figure(self, view: Any = UNREAD) -> dict:
        """The Session's figure with this card's own `editrevision` on it.
        Plotly's `editrevision` DEFAULTS to `uirevision`, which the tool's
        figure pins to "keep" so a zoom survives a redraw — and with it
        pinned, `Plotly.react` keeps a shape the user dragged whenever the
        value the server sends equals the one it sent before, which is
        exactly the snap-back after a refused drag (measured in Chromium).
        An incrementing revision says the shapes are new. The layout is
        COPIED: a Session figure is deterministic, and nothing here writes
        into what it was handed."""
        view = self._view(view)
        figure = self.page.shell.session.plot_figure(
            self.page.model_id,
            self.index,
            editing=self.page.editing_for(self.index),
            visibility=self.visibility(view),
        )
        self.drawable = self._drawable(figure, view)
        self._revision += 1
        return {
            **figure,
            "layout": {**figure["layout"], "editrevision": self._revision},
        }

    def show(self, view: Any = UNREAD) -> None:
        """Draw the plot, once: the browser said this slot is near the
        viewport (or a test did). The card remembers it was asked, so a try
        that raised is retried by the next sync."""
        self.wanted = True
        if self.plot is not None:
            return
        view = self._view(view)
        # Whatever a previous try left: `show()` is called again on every
        # opening row, and a slot that kept each failure's label stacked
        # them — with the recovered plot drawn UNDER them.
        self.slot.clear()
        with self.slot:
            try:
                figure = self._figure(view)
            except Exception as e:  # a plot that cannot draw says so in place
                text = f"{type(e).__name__}: {e}"
                if text != self._error:  # the same failure is not news
                    logger.exception("plot %s of %s", self.index, self.page.model_id)
                    self._error = text
                ui.label(text).classes("text-negative")
                return
            self._error = None
            self.plot = ui.plotly(figure).classes("w-full").style("height: 320px")
        self.hook.attach(self.plot)
        self._drawn = self.signature(view)
        self._error_select(figure)
        # `drawable` is first known here: the swatches show with the plot.
        self._paint_rows(view)

    def _error_select(self, figure: dict) -> None:
        """One select for the error series (legend b), on a plot whose
        figure has any; a plot of several series has none. Asked of every
        figure sent, so a redraw that gains error traces gains the select."""
        ids = [
            tr["meta"]["id"]
            for tr in figure["data"]
            if (tr.get("meta") or {}).get("kind") == "error"
        ]
        if not ids or self.error_select is not None:
            return
        with self.error_slot:
            self.error_select = (
                ui.select(
                    {"none": "No error", **{i: _LABELS[i] for i in ids}},
                    value="none",
                    on_change=lambda e: self._choose_error(e.value),
                )
                .props("dense options-dense")
                .mark(f"error-select:{self.index}")
            )

    def _choose_error(self, value: str) -> None:
        self.error = None if value == "none" else value
        # By the signature, which carries the error: one send.
        self.redraw_if_moved()

    def _retried(self, view: Any) -> bool:
        """A card with no plot: retry the draw if somebody asked for one.
        Nothing else asked again — a result file being rewritten under a
        `show()` left the card showing its error until a row opened or
        Focus landed on it (the 3b review)."""
        if self.plot is not None:
            return False
        if self.wanted:
            self.show(view)
        return True

    def redraw(self, view: Any = UNREAD) -> None:
        """`Plotly.react` on the same node — the figure's `config` never
        depends on what is being edited, so the zoom survives. Also the
        hook's snap-back after every drag, which is the ONE redraw that is
        not decided by the signature: a refused drag moved nothing, and the
        picture still has to go back. ``view`` is the page's read of this
        sync, so a forced redraw costs no read of its own."""
        if self._retried(view):
            return
        assert self.plot is not None
        view = self._view(view)
        figure = self._figure(view)
        self.plot.update_figure(figure)
        self._drawn = self.signature(view)
        self._error_select(figure)
        # The swatches say what was just sent (an open or a close moves one).
        self._paint_rows(view)

    def signature(self, view: Any = UNREAD) -> str:
        """What this plot draws that an edit can move: which criterion is
        open — its rings, and only its — every leaf on this plot's series,
        which leaves are hidden and which error series shows. Comparing it
        to what was last sent is the ONE thing that decides a redraw."""
        view = self._view(view)
        leaves = [
            [leaf.get("path"), *(leaf.get(key) for key in _SIGNED)]
            for leaf in leaves_of(view)
            if leaf.get("variable") in self.resolved.series
        ]
        return json.dumps(
            [
                list(self.page.editing_for(self.index)),
                leaves,
                sorted(self.visibility(view).hidden),
                self.error,
            ],
            sort_keys=True,
            default=str,
        )

    def redraw_if_moved(self, view: Any = UNREAD) -> None:
        """Re-send the figure when what it draws has moved, and only then: a
        plot nobody's edit touched must not be re-sent (the cost the 3b
        review measured is elements on the wire, not a figure assembled)."""
        if self._retried(view):
            return
        if self.signature(view) != self._drawn:
            self.redraw(view)

    # -- the one open criterion's editor -------------------------------------------

    def open_editor(self, path: str, view: Any = UNREAD) -> None:
        """The editor, below its row and at the plot's width (board 2A):
        one hint line and Done, then every capability placed at CRITERION,
        stacked without headings — the row above already names the
        criterion, so the editor reads as one thing (brief §3.2)."""
        row, index = self.rows[path], self.index
        model, variable = self.page.model_id, row.variable
        self.open_path = path
        row.row.classes(add="dstf-crit-open")
        with row.slot:
            self.editor_box = ui.column().classes("dstf-editor w-full gap-2")
        with self.editor_box:
            with ui.row().classes("items-center no-wrap gap-3 w-full"):
                ui.label(
                    "Drag a ring on the plot, or edit the fields. Edits are "
                    "staged as you make them; Save writes them."
                ).classes("dstf-why")
                ui.space()
                ui.button("Done", on_click=self.page.close_editor).props(
                    "flat dense no-caps"
                ).mark(f"editor-done:{index}:{path}")
            self.editor = InlineHost(
                self.page.shell,
                Placement.CRITERION,
                partial(
                    Selection,
                    tests=frozenset({model}),
                    variable=variable,
                    leaf_path=path,
                ),
                figure=self.hook,
                marker=f"editor:{index}:{path}",
                on_applied=self._applied,
            )
            self.editor.build()
        self._edited = self._edited_now(self._view(view))

    def close_editor(self) -> None:
        """The editor goes, and its subscriptions with it: a panel nobody
        can see must not go on staging edits from drags. Only the editor's
        own column: the row's slot also holds its host's error labels, and
        a close that cleared the slot deleted them — a refused Wrap on that
        row then staged nothing and showed nothing (the review, in
        Chromium)."""
        if self.editor is not None:
            self.editor.clear()
        if self.editor_box is not None and not self.editor_box.is_deleted:
            self.editor_box.delete()
        row = self.rows.get(self.open_path or "")
        if row is not None:
            row.row.classes(remove="dstf-crit-open")
        self.editor, self.editor_box = None, None
        self.open_path, self._edited = None, None

    def _applied(self, name: str) -> None:
        """What a successful submit from the editor leaves to do. A drag
        rebuilds the WHOLE editor — no field is being typed during one, and
        the table has to follow the ring. A panel's own submit rebuilds
        every OTHER panel and leaves that one alone, so the control being
        typed in survives."""
        if self.editor is None:
            return
        if self.hook.dispatching:
            self.editor.build()
        else:
            self.editor.build(skip=name)
        # Recorded AFTER this apply and before its events reach the page,
        # from a FRESH tree view, so a panel's own edit is not read as one
        # staged elsewhere — and a first edit of an unmaterialized tree,
        # which stages the materialization AND the replace, is one edit
        # here rather than two.
        self._edited = self._edited_now(self._view())

    def dispose(self) -> None:
        """Give up this card's handlers before it is dropped: a rebuilt page
        must not leave a removed row's panel subscribed to the hook."""
        if self.editor is not None:
            self.editor.clear()
        for row in self.rows.values():
            row.host.clear()
        self.hook.clear()
