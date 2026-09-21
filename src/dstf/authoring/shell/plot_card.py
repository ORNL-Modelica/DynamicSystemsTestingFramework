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
from collections.abc import Sequence
from functools import partial
from typing import TYPE_CHECKING

from nicegui import ui

from ...discovery.plots import ResolvedPlot
from ..capabilities import Placement
from ..session import Selection
from .actions import ActionBar, InlineHost
from .figure_hook import FigureHook
from .tree_card import leaf_name, leaf_readout, leaf_status, leaves_of
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


def worst(statuses: list[str]) -> str:
    return next((status for status in _WORST if status in statuses), "queued")


class CriterionRow:
    """One criterion under its plot (DESIGN.md "Criteria rows"): the pill,
    the metric and its variable, the CLI's readout, and that node's controls
    — whatever attaches at NODE. `paint` writes a new verdict into the row
    that is already there."""

    def __init__(self, card: PlotCard, leaf: dict) -> None:
        index, path = card.index, str(leaf["path"])
        self.path, self.variable = path, str(leaf.get("variable"))
        model, variable = card.page.model_id, self.variable
        self.row = ui.row().classes("dstf-crit items-center no-wrap gap-3 w-full")
        # The row IS the way in: its editor opens below it, and a second
        # click closes it. A click on the node's controls inside the row is
        # not a click on the row — `InlineHost`'s column stops it.
        self.row.on("click", partial(card.page.toggle_criterion, index, path))
        with self.row.mark(f"criterion:{index}:{path}"):
            self.pill = Pill(leaf_status(leaf), f"criterion-pill:{index}:{path}")
            self.name = ui.label().classes("text-body2 text-weight-medium")
            self.readout = ui.label().classes("dstf-mono text-xs")
            self.readout.mark(f"criterion-readout:{index}:{path}")
            ui.space()
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
            )
            # A host drawn inside a row keeps to its own width, or the
            # cluster takes the rest of the row and sits left of the end
            # (the 3b review, in Chromium).
            self.host.column.classes(remove="w-full", add="dstf-node-host")
            self.host.build()
        self.slot = ui.column().classes("w-full gap-0")  # the row's editor slot
        self.paint(leaf)

    def paint(self, leaf: dict) -> None:
        self.pill.set(leaf_status(leaf))
        self.name.set_text(leaf_name(leaf))
        self.readout.set_text(leaf_readout(leaf))


class PlotCard:
    def __init__(
        self,
        page: TestPage,
        index: int,
        resolved: ResolvedPlot,
        view: dict | None,
        tracked: Sequence[str],
    ) -> None:
        """``view`` and ``tracked`` are the page's ONE read of each, handed
        to every card: a card that read them for itself cost one read per
        plot on every rebuild (the Task 5-6 review)."""
        self.page, self.index, self.resolved = page, index, resolved
        self.plot: ui.plotly | None = None
        # Created here, attached in `show()`: a panel hosted under this card
        # subscribes before there is a plot to attach to.
        self.hook = FigureHook(on_shape_moved=self.redraw)
        self.rows: dict[str, CriterionRow] = {}
        self.pills: dict[str, Pill] = {}
        self.bars: list[ActionBar] = []
        self.editor: InlineHost | None = None  # the open criterion's, if it is here
        self.open_path: str | None = None
        self._drawn: str | None = None  # the signature of what the plot shows
        model = page.model_id
        scored = set(tracked)
        #: This plot's scored series; the rest are drawn but have no criteria.
        self.tracked = [name for name in resolved.series if name in scored]
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
            with ui.row().classes("items-center no-wrap gap-3 w-full"):
                ui.label(", ".join(resolved.series)).classes("text-subtitle2").mark(
                    f"plot-title:{index}"
                )
                for variable in self.tracked:
                    if self._leaves(view, variable):
                        pill = Pill("queued", f"plot-pill:{index}:{variable}")
                        if self.named_pills:
                            # The name keeps its own case: the pill's text is
                            # uppercased by the stylesheet, and `X FAIL` names
                            # no variable of this test.
                            pill.label.classes(add="dstf-pill-named")
                        self.pills[variable] = pill
                ui.space()
                # Focus is wired to the page in the task that hides the other
                # cards; the button is the toolbar (decision 9).
                self.focus = ui.button("Focus").props("flat dense no-caps")
                self.focus.mark(f"focus:{index}")
            if resolved.missing:
                ui.label(f"Not in this result: {', '.join(resolved.missing)}").classes(
                    "dstf-why"
                ).mark(f"plot-missing:{index}")
            self.slot = ui.element("div").classes("dstf-plot-slot w-full")
            self.slot.props(f'data-plot="{index}"').mark(f"plot-slot:{index}")
            criteria = ui.column().classes("dstf-criteria w-full gap-0")
            with criteria.mark(f"criteria:{index}"):
                for variable in self.tracked:
                    for leaf in self._leaves(view, variable):
                        self.rows[str(leaf["path"])] = CriterionRow(self, leaf)
            only = [name for name in resolved.series if name not in tracked]
            if only:
                verb = (
                    "is plot only: it has"
                    if len(only) == 1
                    else "are plot only: they have"
                )
                ui.label(f"{', '.join(only)} {verb} no criteria.").classes(
                    "dstf-why"
                ).mark(f"plot-only:{index}")
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
                                ),
                                marker=f"variable-actions:{index}:{variable}",
                                figure=self.hook,
                            )
                        )
        self.paint(view)

    @staticmethod
    def _leaves(view: dict | None, variable: str) -> list[dict]:
        return [leaf for leaf in leaves_of(view) if leaf.get("variable") == variable]

    def paint(self, view: dict | None) -> None:
        """Pills, rows and footers in place, from the page's one `tree_view`
        read: a verdict that flips must not cost the card it sits in."""
        for variable, pill in self.pills.items():
            status = worst([leaf_status(leaf) for leaf in self._leaves(view, variable)])
            text = f"{variable} {status.upper()}" if self.named_pills else None
            pill.set(status, text)
        for leaf in leaves_of(view):
            row = self.rows.get(str(leaf["path"]))
            if row is not None:
                row.paint(leaf)
        for row in self.rows.values():
            # The row stands; what its node's controls MAY do does not (a
            # job's lock comes and goes without the shape moving).
            row.host.follow()
        if self.editor is not None:
            # The same for the open editor: it is not rebuilt while it is
            # open, so a lock that greys its panels has to reach them here.
            self.editor.follow()
        for bar in self.bars:
            bar.refresh()

    # -- the plot ------------------------------------------------------------------

    def _figure(self) -> dict:
        return self.page.shell.session.plot_figure(
            self.page.model_id, self.index, editing=self.page.editing_for(self.index)
        )

    def show(self) -> None:
        """Draw the plot, once: the browser said this slot is near the
        viewport (or a test did)."""
        if self.plot is not None:
            return
        # Whatever a previous try left: `show()` is called again on every
        # opening row, and a slot that kept each failure's label stacked
        # them — with the recovered plot drawn UNDER them.
        self.slot.clear()
        with self.slot:
            try:
                figure = self._figure()
            except Exception as e:  # a plot that cannot draw says so in place
                logger.exception("plot %s of %s", self.index, self.page.model_id)
                ui.label(f"{type(e).__name__}: {e}").classes("text-negative")
                return
            self.plot = ui.plotly(figure).classes("w-full").style("height: 320px")
        self.hook.attach(self.plot)
        self._drawn = self.signature()

    def redraw(self) -> None:
        """`Plotly.react` on the same node — the figure's `config` never
        depends on what is being edited, so the zoom survives. Also the
        hook's snap-back after every drag, which is the ONE redraw that is
        not decided by the signature: a refused drag moved nothing, and the
        picture still has to go back."""
        if self.plot is not None:
            self.plot.update_figure(self._figure())
            self._drawn = self.signature()

    def signature(self, view: dict | None = None) -> str:
        """What this plot draws that an edit can move: which criterion is
        open — its rings, and only its — and every leaf on this plot's
        series. Comparing it to what was last sent is the ONE thing that
        decides a redraw."""
        if view is None:
            view = self.page.shell.session.tree_view(self.page.model_id)
        leaves = [
            [leaf.get("path"), *(leaf.get(key) for key in _SIGNED)]
            for leaf in leaves_of(view)
            if leaf.get("variable") in self.resolved.series
        ]
        return json.dumps(
            [list(self.page.editing_for(self.index)), leaves],
            sort_keys=True,
            default=str,
        )

    def redraw_if_moved(self, view: dict | None = None) -> None:
        """Re-send the figure when what it draws has moved, and only then: a
        plot nobody's edit touched must not be re-sent (the cost the 3b
        review measured is elements on the wire, not a figure assembled)."""
        if self.plot is not None and self.signature(view) != self._drawn:
            self.redraw()

    # -- the one open criterion's editor -------------------------------------------

    def open_editor(self, path: str) -> None:
        """The editor, below its row and at the plot's width (board 2A):
        one hint line and Done, then every capability placed at CRITERION,
        stacked without headings — the row above already names the
        criterion, so the editor reads as one thing (brief §3.2)."""
        row, index = self.rows[path], self.index
        model, variable = self.page.model_id, row.variable
        self.open_path = path
        row.row.classes(add="dstf-crit-open")
        with row.slot, ui.column().classes("dstf-editor w-full gap-2"):
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

    def close_editor(self) -> None:
        """The editor goes, and its subscriptions with it: a panel nobody
        can see must not go on staging edits from drags."""
        if self.editor is not None:
            self.editor.clear()
        row = self.rows.get(self.open_path or "")
        if row is not None:
            row.slot.clear()
            row.row.classes(remove="dstf-crit-open")
        self.editor, self.open_path = None, None

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

    def dispose(self) -> None:
        """Give up this card's handlers before it is dropped: a rebuilt page
        must not leave a removed row's panel subscribed to the hook."""
        if self.editor is not None:
            self.editor.clear()
        for row in self.rows.values():
            row.host.clear()
        self.hook.clear()
