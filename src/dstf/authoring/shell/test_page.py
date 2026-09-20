"""The Test page (brief §3.2; roadmap §3): one test — its own header as a
second row of the shared one, its criteria tree closed to a line, and its
plots stacked, each drawn when it scrolls into view with that variable's
criteria rows under it. The editor arrives with the task after this one.
One object per browser tab, like the Suite page. The page knows no
capability by name: what edits a tree node or a variable is whatever
attaches at that placement."""

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
        self.rebuild()
        self._shape = self.shape(self.shell.session.tree_view(model_id))
        self.shell.register(self)
        self.sync()

    def _subheader(self) -> None:
        """The test's own header, drawn inside the shared one (decision 8):
        the way back, the test, its verdict and where it was scored, then
        the TEST-placement buttons."""
        model_id = self.model_id
        ui.link("← Suite", "/").mark("back")
        ui.label(model_id).classes("text-subtitle1 dstf-mono").mark("model")
        self.badge = ui.badge("").classes("dstf-badge").mark("status")
        self.meta = ui.label().classes("text-secondary text-xs").mark("meta")
        ui.space()
        self.actions = ActionBar(
            self.shell,
            Placement.TEST,
            lambda: Selection(tests=frozenset({model_id})),
            marker="test-actions",
        )

    def unscored_reason(self) -> str:
        """Why this test has no tree: it never ran, or it has no baseline to
        be scored against."""
        project = self.shell.session.project
        result = project.results.get(self.model_id) if project is not None else None
        if result is None or not result.success:
            return "no result"
        return "no baseline"

    def open_criterion(self, path: str) -> None:
        """Open the criterion at ``path`` — the editor below its row, which
        arrives with the next task. The tree line's jumps call it."""

    # -- the cards -------------------------------------------------------------

    def editing_for(self, index: int) -> tuple[str, ...]:
        """The leaf paths whose rings plot ``index`` draws — the open
        criterion's, once there is an editor to open one. A tuple, never a
        bare string: `rings_for` refuses one."""
        return ()

    def rebuild(self) -> None:
        """The cards, from the plots as they are now: one per plot of the
        test's ``plots`` key, else one per tracked variable."""
        assert self.column is not None and self.count is not None
        session = self.shell.session
        for card in self.cards:
            card.dispose()
        self.column.clear()
        plots = session.plots(self.model_id)
        with self.column:
            self.cards = [PlotCard(self, plot.index, plot) for plot in plots]
        project = session.project
        test = project.test(self.model_id) if project is not None else None
        if not plots:
            self.count.set_text(f"no plots: this test has {self.unscored_reason()}")
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
        ref_id = project.store.index.get_id(self.model_id)
        ref = None
        if ref_id is not None and project.store.get_reference(self.model_id):
            ref = RefIndex.ref_filename(ref_id).rsplit(".", 1)[0]
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
                self.rebuild()
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
