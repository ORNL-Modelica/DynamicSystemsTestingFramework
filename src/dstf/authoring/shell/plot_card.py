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
from typing import TYPE_CHECKING, Any

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
                # The plot's whole toolbar (decision 9): the legend already
                # toggles the overlays and the error traces.
                self.focus = ui.button("Focus", on_click=partial(page.focus, index))
                self.focus.props("flat dense no-caps").mark(f"focus:{index}")
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
            only = [name for name in resolved.series if name not in known]
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

    # -- the plot ------------------------------------------------------------------

    def _figure(self) -> dict:
        """The Session's figure with this card's own `editrevision` on it.
        Plotly's `editrevision` DEFAULTS to `uirevision`, which the tool's
        figure pins to "keep" so a zoom survives a redraw — and with it
        pinned, `Plotly.react` keeps a shape the user dragged whenever the
        value the server sends equals the one it sent before, which is
        exactly the snap-back after a refused drag (measured in Chromium).
        An incrementing revision says the shapes are new. The layout is
        COPIED: a Session figure is deterministic, and nothing here writes
        into what it was handed."""
        figure = self.page.shell.session.plot_figure(
            self.page.model_id, self.index, editing=self.page.editing_for(self.index)
        )
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
        # Whatever a previous try left: `show()` is called again on every
        # opening row, and a slot that kept each failure's label stacked
        # them — with the recovered plot drawn UNDER them.
        self.slot.clear()
        with self.slot:
            try:
                figure = self._figure()
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
        self.plot.update_figure(self._figure())
        self._drawn = self.signature(view)

    def signature(self, view: Any = UNREAD) -> str:
        """What this plot draws that an edit can move: which criterion is
        open — its rings, and only its — and every leaf on this plot's
        series. Comparing it to what was last sent is the ONE thing that
        decides a redraw."""
        view = self._view(view)
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
        self._edited = self._edited_now(self._view(view))

    def close_editor(self) -> None:
        """The editor goes, and its subscriptions with it: a panel nobody
        can see must not go on staging edits from drags."""
        if self.editor is not None:
            self.editor.clear()
        row = self.rows.get(self.open_path or "")
        if row is not None:
            row.slot.clear()
            row.row.classes(remove="dstf-crit-open")
        self.editor, self.open_path, self._edited = None, None, None

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
