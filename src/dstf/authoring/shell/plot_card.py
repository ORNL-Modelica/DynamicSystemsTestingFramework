"""One plot of the Test page (brief §3.2; DESIGN.md "Plots are modular",
"Criteria rows"): its title and status pills, the slot the plot is drawn
into when it scrolls into view, the criteria rows of its tracked series
with each row's node controls, and what attaches at the VARIABLE placement
below. A card owns its `ui.plotly`, its `FigureHook` and its snap-back; the
card knows no capability by name.

A card is a 320 px slot until `show()` is called — by the browser's
`dstf_plot_visible`, or by a test. Nothing on the page draws a figure
before that: a test with a dozen variables would otherwise assemble a
dozen figures nobody has scrolled to."""

from __future__ import annotations

import logging
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
    def __init__(self, page: TestPage, index: int, resolved: ResolvedPlot) -> None:
        self.page, self.index, self.resolved = page, index, resolved
        self.plot: ui.plotly | None = None
        # Created here, attached in `show()`: a panel hosted under this card
        # subscribes before there is a plot to attach to.
        self.hook = FigureHook(on_shape_moved=self.redraw)
        self.rows: dict[str, CriterionRow] = {}
        self.pills: dict[str, Pill] = {}
        self.bars: list[ActionBar] = []
        session, model = page.shell.session, page.model_id
        tracked = set(session.tracked(model))
        #: This plot's scored series; the rest are drawn but have no criteria.
        self.tracked = [name for name in resolved.series if name in tracked]
        view = session.tree_view(model)
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
                        self.pills[variable] = Pill(
                            "queued", f"plot-pill:{index}:{variable}"
                        )
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
            for variable in self.tracked:
                self.bars.append(
                    ActionBar(
                        page.shell,
                        Placement.VARIABLE,
                        partial(Selection, tests=frozenset({model}), variable=variable),
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
            pill.set(
                worst([leaf_status(leaf) for leaf in self._leaves(view, variable)])
            )
        for leaf in leaves_of(view):
            row = self.rows.get(str(leaf["path"]))
            if row is not None:
                row.paint(leaf)
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
        with self.slot:
            try:
                figure = self._figure()
            except Exception as e:  # a plot that cannot draw says so in place
                logger.exception("plot %s of %s", self.index, self.page.model_id)
                ui.label(f"{type(e).__name__}: {e}").classes("text-negative")
                return
            self.plot = ui.plotly(figure).classes("w-full").style("height: 320px")
        self.hook.attach(self.plot)

    def redraw(self) -> None:
        """`Plotly.react` on the same node — the figure's `config` never
        depends on what is being edited, so the zoom survives. Also the
        hook's snap-back after every drag."""
        if self.plot is not None:
            self.plot.update_figure(self._figure())

    def dispose(self) -> None:
        """Give up this card's handlers before it is dropped: a rebuilt page
        must not leave a removed row's panel subscribed to the hook."""
        for row in self.rows.values():
            row.host.clear()
        self.hook.clear()
