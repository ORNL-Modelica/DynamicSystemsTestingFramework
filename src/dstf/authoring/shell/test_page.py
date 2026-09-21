"""The Test page (brief §3.2; roadmap §3): one test — its own header as a
second row of the shared one, its criteria tree closed to a line, and its
plots stacked, each drawn when it scrolls into view with that variable's
criteria rows under it and, under the row that was clicked, that
criterion's editor — one at a time, page-wide, with the plot above it as
its editing surface. One object per browser tab, like the Suite page. The
page knows no capability by name: what edits a criterion, a tree node or a
variable is whatever attaches at that placement."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from nicegui import ui

from ...reporting.status import classify_with_class
from ...storage.ref_index import RefIndex
from ..capabilities import Placement, plural
from ..session import Selection
from .actions import ActionBar
from .header import Header
from .plot_card import PlotCard
from .tabstate import tab_state
from .tree_card import TreeCard, leaves_of

if TYPE_CHECKING:
    from .shell import Shell

logger = logging.getLogger(__name__)

#: Render on scroll (the 3b spike): ONE observer per browser tab, kept on
#: `window` so a re-run after a rebuild reuses it; a slot near the viewport
#: names itself once, and is stamped so a re-run does not observe it twice.
OBSERVE = """
(() => {
  window.__dstfPlotObserver ??= new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      window.__dstfPlotObserver.unobserve(entry.target);
      emitEvent("dstf_plot_visible", entry.target.dataset.plot);
    }
  }, {rootMargin: "200px"});
  for (const el of document.querySelectorAll(".dstf-plot-slot:not([data-seen])")) {
    el.dataset.seen = "1";
    window.__dstfPlotObserver.observe(el);
  }
})();
"""


class TestPage:
    def __init__(self, shell: Shell) -> None:
        self.shell = shell
        self.client: Any = None
        self.model_id = ""
        self.badge: ui.badge | None = None
        self.meta: ui.label | None = None
        self.actions: ActionBar | None = None
        self.tree: TreeCard | None = None
        self.cards: list[PlotCard] = []
        self.count: ui.label | None = None
        self.column: ui.column | None = None
        self._shape: Any = None
        self.open: tuple[int, str] | None = None  # (plot index, leaf path)

    async def build(self, model_id: str) -> None:
        self.client = ui.context.client
        await self.client.connected()
        self.state = tab_state()  # the tab's, shared with its Suite page
        self.shell.page_setup()
        self.model_id = model_id
        ui.page_title(f"DSTF — {model_id.rsplit('.', 1)[-1]}")
        project = self.shell.session.project
        known = project is not None and project.test(model_id) is not None
        self.header = Header(self.shell, below=self._subheader if known else None)
        with ui.column().classes("dstf-test w-full q-pa-md gap-3"):
            if not known:
                ui.link("← Suite", "/").mark("back")
                ui.label(f"{model_id} is not a test of this project").classes(
                    "text-negative"
                )
                self.shell.register(self)
                return
            self.tree = TreeCard(self)
            self.count = (
                ui.label().classes("text-secondary text-xs").mark("plots:count")
            )
            self.column = ui.column().classes("dstf-plots w-full gap-3").mark("plots")
        ui.on("dstf_plot_visible", lambda e: self.on_visible(e.args))
        view = self.shell.session.tree_view(model_id)
        self.rebuild(view)
        self._shape = self.shape(view)
        self.shell.register(self)
        self.sync()

    def _subheader(self) -> None:
        """The test's own header, drawn inside the shared one (decision 8):
        the way back, the test, its verdict and where it was scored, then
        the TEST-placement buttons."""
        model_id = self.model_id
        ui.link("← Suite", "/").mark("back")
        ui.label(model_id).classes("text-subtitle1 dstf-mono").mark("model")
        # `color=None`: a Quasar badge otherwise carries `bg-primary`, whose
        # `!important` beats the inline status fill and paints every verdict
        # blue (the Task 4 review, in Chromium).
        self.badge = ui.badge("", color=None).classes("dstf-badge").mark("status")
        self.meta = ui.label().classes("text-secondary text-xs").mark("meta")
        ui.space()
        self.actions = ActionBar(
            self.shell,
            Placement.TEST,
            lambda: Selection(tests=frozenset({model_id})),
            marker="test-actions",
        )

    def _result(self) -> Any:
        project = self.shell.session.project
        return project.results.get(self.model_id) if project is not None else None

    def unscored_reason(self) -> str:
        """Why this test has no tree, as a clause the line it is read into
        completes ("nothing scored: this test has no baseline"): it never
        ran, its scoring raised, or it has no baseline to be scored
        against. A model whose scoring raised keeps its result and has no
        comparison at all — the Suite row says `scoring failed` for the
        same state — so "no baseline" would name the wrong cause."""
        result = self._result()
        if result is None or not result.success:
            return "this test has no result"
        if self.shell.session.comparisons.get(self.model_id) is None:
            return "scoring failed"
        return "this test has no baseline"

    def _no_plots_reason(self, test: Any) -> str:
        """Why there is no PLOT, which is not why there is no tree: a
        scored, baselined test whose `plots` key is an empty list asked for
        none, and reading the tree's reason there blamed a baseline sitting
        right beside it in the header (the Task 5-6 review)."""
        if test is not None and test.plots is not None and not test.plots:
            return "this test's plots key is empty"
        result = self._result()
        if result is None or not result.success:
            return "this test has no result"
        return "the result has no variables"

    # -- the one open editor ---------------------------------------------------

    def editing_for(self, index: int) -> tuple[str, ...]:
        """The leaf paths whose rings plot ``index`` draws — the open
        criterion's, and only on the card its row is on. A tuple, never a
        bare string: `rings_for` refuses one."""
        if self.open is not None and self.open[0] == index:
            return (self.open[1],)
        return ()

    def toggle_criterion(self, index: int, path: str) -> None:
        """A criteria row's own click: its editor opens, or closes if that
        row was the open one."""
        if self.open == (index, path):
            self.close_editor()
        else:
            self.open_criterion(path, index)

    def open_criterion(self, path: str, index: int | None = None) -> None:
        """One editor at a time, page-wide (board 2A). The plot above it is
        its editing surface, so it is drawn if it was not."""
        if index is None:
            index = next((card.index for card in self.cards if path in card.rows), None)
        if index is None or not 0 <= index < len(self.cards):
            return
        card = self.cards[index]
        if path not in card.rows:
            return
        self.close_editor()
        self.open = (index, path)
        card.show()
        card.open_editor(path)
        self._redraw_moved()

    def close_editor(self) -> None:
        if self.open is None:
            return
        card = self.cards[self.open[0]]
        self.open = None
        card.close_editor()
        self._redraw_moved()

    def _redraw_moved(self) -> None:
        """The signature is the one thing that decides a redraw: only the
        card whose rings came or went is re-sent."""
        for card in self.cards:
            card.redraw_if_moved()

    def jump_to(self, path: str) -> None:
        """The closed tree line's jump (board 1A): open that criterion and
        bring the row it opened under into view."""
        self.open_criterion(path)
        ui.run_javascript(
            'document.querySelector(".dstf-crit-open")'
            '?.scrollIntoView({block: "center"})'
        )

    # -- the cards -------------------------------------------------------------

    def rebuild(self, view: dict | None) -> None:
        """The cards, from the plots as they are now: one per plot of the
        test's ``plots`` key, else one per tracked variable. ``view`` and
        the tracked names are read ONCE here and handed to every card — a
        card that read them for itself cost one read per plot (the Task 5-6
        review)."""
        assert self.column is not None and self.count is not None
        session = self.shell.session
        # The rows the editor hangs under go with the cards: the criterion
        # it was open on may not even exist after this.
        self.close_editor()
        for card in self.cards:
            card.dispose()
        self.column.clear()
        plots = session.plots(self.model_id)
        tracked = session.tracked(self.model_id)
        with self.column:
            self.cards = [
                PlotCard(self, plot.index, plot, view, tracked) for plot in plots
            ]
        project = session.project
        test = project.test(self.model_id) if project is not None else None
        if not plots:
            self.count.set_text(f"no plots: {self._no_plots_reason(test)}")
        elif test is not None and test.plots is not None:
            self.count.set_text(
                f"{plural(len(plots), 'plot')} · from this test's plots key"
            )
        else:
            self.count.set_text(
                f"{plural(len(plots), 'plot')} · one per tracked variable"
            )
        self._observe()

    def on_visible(self, arg: Any) -> None:
        """The browser's `dstf_plot_visible`: the slot's index, as a string.
        Anything else is a page that has moved on since the observer fired."""
        try:
            index = int(arg) if isinstance(arg, str) else -1
        except ValueError:
            return
        if 0 <= index < len(self.cards):
            self.cards[index].show()

    def _observe(self) -> None:
        ui.run_javascript(OBSERVE)

    def shape(self, view: dict | None) -> Any:
        """What the cards are BUILT from and cannot be repainted through:
        the criteria and their variables, and the plots themselves. NOT a
        leaf's metric — a mode switch repaints a row's name in place."""
        return (
            tuple((leaf["path"], leaf.get("variable")) for leaf in leaves_of(view)),
            tuple(
                (plot.series, plot.missing)
                for plot in self.shell.session.plots(self.model_id)
            ),
        )

    # -- following the Session -------------------------------------------------

    def _paint(self) -> None:
        if self.badge is None or self.meta is None:
            return
        session = self.shell.session
        comp = session.comparisons.get(self.model_id)
        verdict, css = classify_with_class(comp) if comp is not None else ("", "queued")
        self.badge.set_text(verdict or "…")
        self.badge.style(f"background-color: var(--dstf-{css}-fill)")
        project = session.project
        if project is None:
            return
        # The index's id alone: `get_reference` reads the whole baseline off
        # disk, and this runs on every Session event to print one file name.
        ref_id = project.store.index.get_id(self.model_id)
        ref = (
            None if ref_id is None else RefIndex.ref_filename(ref_id).rsplit(".", 1)[0]
        )
        self.meta.set_text(f"{project.config.backend_name} · {ref or 'no baseline'}")

    def sync(self) -> None:
        """Follow the Session: ONE `tree_view` read, handed to the tree card
        and to every plot card. A moved shape rebuilds the cards; otherwise
        each card is painted in place."""
        self._paint()
        view = self.shell.session.tree_view(self.model_id)
        if self.tree is not None:
            self.tree.refresh(view)
        if self.column is None:
            return
        shape = self.shape(view)
        if shape != self._shape:
            self._shape = shape
            with self.column:
                self.rebuild(view)
            return
        for card in self.cards:
            try:
                card.paint(view)
            except Exception:  # one card's failure is not another's
                logger.exception("plot %s of %s", card.index, self.model_id)

    def handle(self, event: dict[str, Any]) -> None:
        self.header.handle(event)
        if self.actions is not None:
            self.actions.refresh()
        if event["event"] in ("results", "spec", "loading", "opened", "job"):
            self.sync()
