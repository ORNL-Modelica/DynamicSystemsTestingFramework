"""The Test page (brief §3.2; roadmap §3): one test — its own header as a
second row of the shared one, its criteria tree closed to a line, and its
plots stacked, each drawn when it scrolls into view with that variable's
criteria rows under it and, under the row that was clicked, that
criterion's editor — one at a time, page-wide, with the plot above it as
its editing surface. A node's forms open as a row under the row they were
asked from, and that row is the same one thing: it replaces the editor and
the editor replaces it. One object per browser tab, like the Suite page. The
page knows no capability by name: what edits a criterion, a tree node or a
variable is whatever attaches at that placement."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from nicegui import ui

from ...config import ConfigError
from ...discovery.patch_apply import PatchError
from ...reporting.status import SCORING_FAILED, classify, classify_with_class
from ...storage.batch_manifest import unfinished_run_models
from ...storage.ref_index import RefIndex
from ..capabilities import plural
from ..errors import user_text
from ..layout import Layout, layout_op, reset_op
from ..session import JobRunningError, LockedError
from ..situation import Strip, strip_for
from ..tree_pointer import node_or_none
from ..tree_text import leaves_of, nodes_of
from .actions import ActionBar, RowBuild, RowTarget
from .header import Header
from .plot_card import UNREAD, PlotCard, folded
from .rail import Rail
from .strip import StatusStrip
from .tabstate import tab_state
from .tree_card import TreeCard, leaf_status

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

#: The one sentence for both ways a page ends up with no test: built for a
#: model this project never had, or its project swapped under it. `_gone`
#: writes it, and a page built in that state reaches `_gone` through its
#: first `sync`.
NOT_A_TEST = "{} is not a test of this project"


def _raw(node: dict | None) -> str:
    """A raw node as the form row records it, to tell whether it moved."""
    return json.dumps(node, sort_keys=True, default=str)


class TestPage:
    def __init__(self, shell: Shell) -> None:
        self.shell = shell
        self.client: Any = None
        self.model_id = ""
        self.badge: ui.badge | None = None
        self.meta: ui.label | None = None
        #: The status strip under the sub-header, and its TEST-placement bar.
        self.strip: StatusStrip | None = None
        self.actions: ActionBar | None = None
        self.tree: TreeCard | None = None
        self.cards: list[PlotCard] = []
        self.count: ui.label | None = None
        self.column: ui.column | None = None
        self._shape: Any = None
        #: Whether the page is showing "… is not a test of this project"
        #: instead of a test (`_gone`).
        self.gone = False
        self.open: tuple[int, str] | None = None  # (plot index, leaf path)
        #: The one open form row: its target, its title, its build, its
        #: column, and the node's raw JSON it was last built from.
        self.form: tuple[RowTarget, str, RowBuild, ui.column, str] | None = None
        #: A criterion to open at the end of the next sync, and the plot
        #: it was asked from (`reveal`).
        self.pending_reveal: tuple[str, int | None] | None = None
        self.focused: int | None = None  # the one plot Focus is showing
        self.focus_bar: ui.row | None = None
        self.focus_text: ui.label | None = None
        self.focus_all: ui.button | None = None
        #: The plot the Variables drawer's checkboxes act on (the owner's
        #: ruling, Q5 A): the first until a click on a card, or its Add
        #: series, selects another. Its card wears `dstf-plot-selected`.
        self.selected = 0

    async def build(self, model_id: str) -> None:
        self.client = ui.context.client
        await self.client.connected()
        self.state = tab_state()  # the tab's, shared with its Suite page
        self.shell.page_setup()
        self.model_id = model_id
        ui.page_title(f"DSTF — {model_id.rsplit('.', 1)[-1]}")
        # The frame is built whatever the Session holds at this moment: a
        # page opened (or reloaded) while the project is still scanning, or
        # with another library open, was built in the gone state with no
        # frame to fill — `sync` returned early for ever and the test never
        # came back however many events arrived (the final review). `sync`
        # below fills it, or `_gone` writes the sentence into it.
        self.header = Header(self.shell, below=self._subheader, strip=self._strip)
        self.rail = Rail(self)
        with ui.column().classes("dstf-test w-full q-pa-md gap-3"):
            self.tree = TreeCard(self)
            self.count = (
                ui.label().classes("text-secondary text-xs").mark("plots:count")
            )
            self.focus_bar = (
                ui.row().classes("dstf-focus-bar items-center gap-3 w-full")
            ).mark("focus-bar")
            with self.focus_bar:
                self.focus_text = ui.label().mark("focus-bar:text")
                ui.space()
                self.focus_all = (
                    ui.button(on_click=self.show_all)
                    .props("flat dense no-caps")
                    .mark("focus-bar:all")
                )
            self.focus_bar.set_visibility(False)
            self.column = ui.column().classes("dstf-plots w-full gap-3").mark("plots")
        ui.on("dstf_plot_visible", lambda e: self.on_visible(e.args))
        self.shell.register(self)
        self.sync()
        self.rail.open(self.state.drawer)

    def _known(self) -> bool:
        """Whether this page's test is a test of the OPEN project. It can
        stop being one while the page is open: an `OpenProject` outcome
        swaps the library, `Session.close()` takes it away."""
        project = self.shell.session.project
        return project is not None and project.test(self.model_id) is not None

    def _view(self) -> dict | None:
        return self.shell.session.tree_view(self.model_id)

    def _subheader(self) -> None:
        """The test's own header, drawn inside the shared one (decision 8):
        the way back, the test, its verdict and where it was scored. Its
        TEST-placement buttons are the strip's, under it."""
        # One line: beside a long model id and Rerun's caption with an edit
        # pending, "← Suite" broke after the arrow at 1280 px.
        ui.link("← Suite", "/").classes("text-no-wrap").mark("back")
        ui.label(self.model_id).classes("dstf-model text-subtitle1 dstf-mono").mark(
            "model"
        )
        # `color=None`: a Quasar badge otherwise carries `bg-primary`, whose
        # `!important` beats the inline status fill and paints every verdict
        # blue (the Task 4 review, in Chromium).
        self.badge = ui.badge("", color=None).classes("dstf-badge").mark("status")
        self.meta = ui.label().classes("text-secondary text-xs").mark("meta")

    def _strip(self) -> None:
        """The header's third row: the test's situation and its next
        action (Q2 A)."""
        self.strip = StatusStrip(self)
        self.actions = self.strip.actions

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
            return SCORING_FAILED
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

    # -- the one open form row --------------------------------------------------

    def _node(self, path: str) -> dict | None:
        return node_or_none(self.shell.session.tree(self.model_id), path)

    def open_row(self, target: RowTarget, title: str, build: RowBuild) -> None:
        """A form row under ``target``'s row (the 2026-09-21 board, 1A-1D).
        ONE thing open at a time: the editor goes, and any other row — and
        the card whose rings went with the editor is re-sent, once."""
        view = self._view()
        self._close()
        self._close_row()
        # Its own row's error goes as its form opens; any other row's
        # stays with that row (`RowTarget.hush`).
        target.hush()
        self._redraw_moved(view)
        assert target.slot is not None  # set by the row's owner at build
        with target.slot:
            column = (
                ui.column()
                .classes("dstf-form-row w-full gap-2")
                .mark(f"form-row:{target.key}")
            )
        target.row.classes(add="dstf-row-open")
        self.form = (target, title, build, column, _raw(self._node(target.node)))
        self._build_row()
        # Just enough to show the whole row, like the tree line's jump; a
        # row already in view does not move (the owner's ruling). Opened
        # from a criteria row at the viewport's foot, it was drawn below
        # the fold with nothing to say it had opened.
        ui.run_javascript(
            f'document.getElementById("{column.html_id}")'
            '?.scrollIntoView({block: "nearest"})'
        )

    def _build_row(self) -> None:
        assert self.form is not None
        target, title, build, column, _ = self.form
        column.clear()
        with column:
            ui.label(title).classes("text-subtitle2").mark(f"form-title:{target.key}")
            build(self._close_row)

    def _close_row(self) -> None:
        """Take the open form row away, if there is one; nothing is redrawn
        — a row draws on no plot. The error its row's host shows goes with
        it, and no other row's: a page-wide hide took an error off the row
        it was about for a Cancel on another plot's row (the review)."""
        if self.form is None:
            return
        target, _, _, column, _ = self.form
        self.form = None
        target.hush()
        if not column.is_deleted:
            column.delete()
        if not target.row.is_deleted:
            target.row.classes(remove="dstf-row-open")

    def drop_row(self, target: RowTarget) -> None:
        """Close the open form if it hangs under ``target``: a host being
        cleared — rebuilt, dropped with its row, hidden — takes its form
        with it. Any other form is not this host's to close."""
        if self.form is not None and self.form[0] is target:
            self._close_row()

    def _follow_row(self) -> None:
        """The open row after a sync: gone with the rows it hung under (a
        shape change rebuilt them), or rebuilt when an edit staged ELSEWHERE
        moved its node — the criterion editor's rule, applied to a node: a
        form showing a value the node no longer has would stage it straight
        back. Anything else leaves it alone, so a field being typed in
        survives every unrelated sync. A node that is gone closes the row:
        its ``build`` over a path that names nothing drew a form for no
        node (the Task 5 review's probe built twice for one)."""
        if self.form is None:
            return
        target, title, build, column, built = self.form
        if column.is_deleted or target.row.is_deleted:
            self.form = None
            return
        node = self._node(target.node)
        if node is None:
            self._close_row()
            return
        now = _raw(node)
        if now != built:
            self.form = (target, title, build, column, now)
            self._build_row()

    def row_click(self, index: int, path: str) -> None:
        """A criteria row's own click (board 1A): it closes a form open
        under it and opens nothing; else it toggles the row's editor."""
        if self.form is not None and self.form[0].key == f"crit:{index}:{path}":
            self._close_row()
            return
        self.toggle_criterion(index, path)

    def reveal(self, path: str, index: int | None) -> None:
        """Open ``path``'s editor at the end of the next sync — the one that
        draws the row an add just made — on plot ``index``, the plot it was
        asked from, when that plot holds the row; else on the first plot
        that does (a tree row's add has no plot). Recorded only when that
        criterion EXISTS: apply is synchronous, so a refused add left no
        node, and a reveal kept for it opened the next add's criterion by
        itself."""
        if node_or_none(self.shell.session.tree(self.model_id), path) is not None:
            self.pending_reveal = (path, index)

    def _reveal(self, path: str, index: int | None) -> None:
        """The recorded reveal, once the cards are drawn: plot 3's `+`
        opened its editor on plot 0, the first card holding the variable,
        ~855 px above the click and never scrolled to (the Part 2 review).
        Brought into view just enough, like a form row: the row, then the
        editor under it — the row alone brought to the viewport's foot left
        the editor just below it.

        Sent to THIS page's client: `sync` runs from the Shell's timer,
        whose context is no page's, and a bare `ui.run_javascript` from
        there scrolled nothing."""
        if index is not None and not (
            0 <= index < len(self.cards) and path in self.cards[index].rows
        ):
            index = None
        self.open_criterion(path, index)
        if self.open is not None and self.open[1] == path:
            self.client.run_javascript(
                'document.querySelector(".dstf-crit-open")'
                '?.scrollIntoView({block: "nearest"});'
                'document.querySelector(".dstf-editor")'
                '?.scrollIntoView({block: "nearest"})'
            )

    def toggle_criterion(self, index: int, path: str) -> None:
        """A criteria row's own click: its editor opens, or closes if that
        row was the open one."""
        if self.open == (index, path):
            self.close_editor()
        else:
            self.open_criterion(path, index)

    def open_criterion(self, path: str, index: int | None = None) -> None:
        """One editor at a time, page-wide (board 2A). The plot above it is
        its editing surface, so it is drawn if it was not. ONE `tree_view`
        read, handed to every card this touches: reading it per shown card
        cost fourteen reads for one row click on a twelve-plot page (the 3b
        review)."""
        if index is None:
            index = next((card.index for card in self.cards if path in card.rows), None)
        if index is None or not 0 <= index < len(self.cards):
            return
        card = self.cards[index]
        if path not in card.rows:
            return
        view = self._view()
        self._close_row()
        if self.focused is not None and self.focused != index:
            # The editor has to be on screen: the row it opens under is on a
            # card Focus is hiding (board 5A shows ONE plot).
            self.focused = None
            self._paint_focus(view)
        self._close()
        self.open = (index, path)
        card.show(view)
        card.open_editor(path, view)
        self._redraw_moved(view)

    def close_editor(self) -> None:
        view = self._view()
        self._close()
        self._redraw_moved(view)

    def _close(self) -> None:
        """Close the open editor and redraw NOTHING: what is re-sent is the
        caller's to decide, once. A close that redrew on its own re-sent the
        same card twice for a swap between two of its rows — and, in
        `rebuild`, asked the Session for a plot the staged layout had just
        taken away, which ended the rebuild half-done with the new shape
        already recorded (the Task 6 review)."""
        if self.open is None:
            return
        card = self.cards[self.open[0]]
        self.open = None
        card.close_editor()

    def _redraw_moved(self, view: Any) -> None:
        """The signature is the one thing that decides a redraw: only the
        card whose rings came or went is re-sent."""
        for card in self.cards:
            card.redraw_if_moved(view)

    def jump_to(self, path: str) -> None:
        """The closed tree line's jump (board 1A): open that criterion and
        bring the row it opened under into view. A criterion whose variable
        is on no plot of this test has no row to open — the jump said
        nothing at all, and with another editor open it scrolled to THAT
        row instead."""
        self.open_criterion(path)
        if self.open is not None and self.open[1] == path:
            ui.run_javascript(
                'document.querySelector(".dstf-crit-open")'
                '?.scrollIntoView({block: "center"})'
            )
            return
        ui.notify(
            f"{self._variable_of(path)} is on no plot of this test", type="warning"
        )

    def _variable_of(self, path: str) -> str:
        """What a tree node's variable is CALLED, for a sentence about it —
        the leaf's own text, the same one its criteria row reads (a
        notification carries no muted name beside it)."""
        node = next((n for n in nodes_of(self._view()) if n.get("path") == path), None)
        if node is None:
            return path
        return str(node.get("label") or node.get("variable"))

    # -- the cards -------------------------------------------------------------

    def rebuild(self, view: dict | None) -> None:
        """The cards, from the plots as they are now: one per plot of the
        test's ``plots`` key, else one per tracked variable. ``view`` and
        the tracked names are read ONCE here and handed to every card — a
        card that read them for itself cost one read per plot (the Task 5-6
        review)."""
        assert self.column is not None and self.count is not None
        session = self.shell.session
        self.gone = False
        # The rows the editor hangs under go with the cards: the criterion
        # it was open on may not even exist after this. Closed WITHOUT a
        # redraw — these cards are about to be dropped, and one of them may
        # be a plot the staged layout no longer has.
        self._close()
        # Focus names an INDEX, and after this the indices may name other
        # plots; the plots a user had drawn are re-drawn where they are left.
        self.focused = None
        shown = {card.index for card in self.cards if card.plot is not None}
        for card in self.cards:
            card.dispose()
        self.column.clear()
        plots = session.plots(self.model_id)
        tracked = session.tracked(self.model_id)
        # The label scope too: it is one fact about the TEST, and a card
        # that read it for itself cost one `plots` + `tracked` read per
        # plot — 35 for a five-plot test.
        scope = session.variable_texts(self.model_id)
        # The layout and the scored counts too, for every card's Remove
        # plot: a card that asked for them read the layout and the tree
        # once per plot (3c's executing review).
        layout = session.layout(self.model_id)
        scored = self.scored(view)
        with self.column:
            self.cards = [
                PlotCard(self, plot.index, plot, view, tracked, scope, layout, scored)
                for plot in plots
            ]
        for card in self.cards:
            # A rebuild must not blank what the user was looking at: the
            # browser's observer has already fired for those slots and will
            # not fire again.
            if card.index in shown:
                card.show(view)
        self._paint_focus(view)
        # The selection names an INDEX too; it is kept, clamped to the cards
        # there are now, and its ring painted on the new cards. The drawer
        # follows at the end of the sync that called this, once.
        self._ring(self.selected)
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

    # -- the selected plot and the layout ---------------------------------------

    def select(self, index: int) -> None:
        """Select plot ``index``, clamped to the cards (0 with none), move
        the ring to its card, and the open Variables drawer's checks with
        it."""
        self._ring(index)
        self.rail.refresh()

    def _ring(self, index: int) -> None:
        self.selected = max(0, min(index, len(self.cards) - 1))
        for card in self.cards:
            if card.index == self.selected:
                card.card.classes(add="dstf-plot-selected")
            else:
                card.card.classes(remove="dstf-plot-selected")

    def scored(self, view: dict | None) -> dict[str, int]:
        """``{variable: its number of criteria}`` over ``view``, the caller's
        ONE read of the tree — this never reads one itself."""
        out: dict[str, int] = {}
        for leaf in leaves_of(view):
            name = leaf.get("variable")
            if name:
                out[str(name)] = out.get(str(name), 0) + 1
        return out

    def stage_layout(self, layout: Layout, select: int | None = None) -> bool:
        """Stage ``layout`` as the ONE op on `/plots`. ``select`` is set
        BEFORE the stage — the rebuild its `spec` event causes reads it —
        and put back if the Session refuses: a refused edit is said, and
        nothing else changes (3c, planner's choice 10)."""
        session = self.shell.session
        op = layout_op(self.model_id, session.spec.entry(self.model_id), layout)
        before = self.selected
        if select is not None:
            self.selected = select
        try:
            session.stage([op])
        except (LockedError, PatchError, ConfigError, JobRunningError) as e:
            self.selected = before
            ui.notify(user_text(e), type="warning")
            return False
        return True

    def remove_plot(self, index: int, layout: Layout) -> None:
        """Stage ``layout`` (plot ``index`` gone) with the selection kept on
        the SAME plot: a removal above it shifts it up by one; the selected
        plot removed selects the one now at its index, the last if it was
        the last (the rebuild clamps)."""
        self.stage_layout(layout, select=self.selected - (index < self.selected))

    def reset_layout(self) -> None:
        """Stage removing the entry's ``plots`` key: one plot per tracked
        variable again. Refused, it is said, like any layout edit."""
        try:
            self.shell.session.stage([reset_op(self.model_id)])
        except (LockedError, PatchError, ConfigError, JobRunningError) as e:
            ui.notify(user_text(e), type="warning")

    # -- Focus -----------------------------------------------------------------

    def focus(self, index: int) -> None:
        """Board 5A: Focus is a button on each plot, and the focused plot's
        button reads "Focused" and is the way out of it, like the bar's."""
        self.focused = None if self.focused == index else index
        if (
            self.focused is not None
            and self.open is not None
            and self.open[0] != self.focused
        ):
            # An editor nobody can see is no editor, and it goes on staging
            # edits from drags on a plot that is not on screen. The other
            # direction of `open_criterion`'s, which ends the focus instead.
            self.close_editor()
        if self.focused is not None:
            # A form under a criteria row of a card Focus hides goes the
            # same way (the Task 5 review).
            for card in self.cards:
                if card.index != self.focused:
                    for row in card.rows.values():
                        self.drop_row(row.target)
        self._paint_focus()

    def show_all(self) -> None:
        self.focused = None
        self._paint_focus()

    def _paint_focus(self, view: Any = UNREAD) -> None:
        """The bar and every card's visibility, from `focused` as it is now
        — the one place either is written, so a rebuild resets both by
        calling it."""
        if self.focus_bar is None or self.focus_text is None:
            return
        if self.focused is not None and not 0 <= self.focused < len(self.cards):
            self.focused = None
        focused = self.focused
        for card in self.cards:
            card.card.set_visibility(focused is None or card.index == focused)
            card.focus.set_text("Focused" if card.index == focused else "Focus")
        self.focus_bar.set_visibility(focused is not None)
        if focused is None or self.focus_all is None:
            return
        plots = plural(len(self.cards), "plot")
        # The plot named the way it names itself — its texts, not the keys
        # underneath them, and a family folded as on its title.
        card = self.cards[focused]
        name = folded(card.resolved.series, card.texts)
        self.focus_text.set_text(f"Showing 1 of {plots}: {name}")
        self.focus_all.set_text(f"Show all {plots}")
        self.cards[focused].show(view)

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

    def _paint(self, view: dict | None) -> None:
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
        if self.strip is not None:
            # `_gone` blanked the strip and hid its bar; this brings both
            # back, for the project that is open now. The strip never stops
            # a sync: the tree and the cards below it follow whatever it
            # raises.
            try:
                self.strip.paint(self._situation(comp, view))
            except Exception:
                logger.exception("the strip of %s", self.model_id)
                # The paint refreshes the bar; raised before it, the bar
                # still follows a job that locked this test. The bar may be
                # what raised (a capability's `emphasis` that is no dict):
                # raised again here, it would stop the sync after all.
                try:
                    self.strip.actions.refresh()
                except Exception:
                    logger.exception("the bar of %s", self.model_id)

    def _situation(self, comp: Any, view: dict | None) -> Strip:
        """The strip's inputs, read from the Session: the verdict as edited
        and — only with this test's edits pending — as saved."""
        session, model_id = self.shell.session, self.model_id
        project = session.project
        assert project is not None  # `_paint` returned before without one
        edits = sum(1 for op in session.spec.pending if op.model_id == model_id)
        saved_status: str | None = None
        if edits:
            # The saved spec is scored afresh here, and its scoring may raise
            # where the edited one does not (an edit that fixed it): the
            # strip says so, and the page goes on following the edit.
            try:
                saved = session.saved_comparison(model_id)
            except Exception:
                logger.exception("the saved verdict of %s", model_id)
                saved_status = SCORING_FAILED
            else:
                saved_status = classify(saved) if saved is not None else None
        running = session.running
        loading = self.shell.load.in_progress
        # A test whose last run never finished it has a result that is not
        # one (what Accept's "re-run before accepting" reads).
        work_dir = project.config.work_dir
        assert work_dir is not None  # resolved in Config.__post_init__
        has_result = self._result() is not None and (
            model_id not in unfinished_run_models(work_dir)
        )
        statuses = [leaf_status(leaf) for leaf in leaves_of(view)]
        return strip_for(
            running=(
                running.label
                if running is not None and model_id in running.models
                else None
            ),
            edits=edits,
            status=classify(comp) if comp is not None else None,
            saved_status=saved_status,
            has_result=has_result,
            loading=loading,
            # The load names the models whose scoring raised while it read
            # them; after it, a result with no comparison is one whose
            # scoring raised later (a Rerun's or an Accept's reload).
            scoring_failed=comp is None
            and (
                model_id in self.shell.load.failed_models
                or (has_result and not loading)
            ),
            simulate_only=comp is not None and comp.simulate_only,
            fails=statuses.count("fail"),
            criteria=len(statuses),
            advisory=statuses.count("warn"),
        )

    def sync(self, force: bool = False) -> None:
        """Follow the Session: ONE `tree_view` read, handed to the tree card
        and to every plot card. A moved shape rebuilds the cards; otherwise
        each card is painted in place and re-sent only when what it DRAWS
        moved — ``force`` re-sends every drawn plot whether it moved or not,
        which is what a finished job needs (decision 10: a rerun changes the
        trajectories, and no leaf's signature sees that)."""
        if not self._known():
            self._gone()
            return
        view = self._view()
        self._paint(view)
        if self.tree is not None:
            self.tree.refresh(view)
        if self.column is None:
            return
        shape = self.shape(view)
        if shape != self._shape:
            with self.column:
                self.rebuild(view)
            # Recorded AFTER the rebuild ran: a raise inside it left the
            # page with the NEW shape already recorded and half the cards
            # built, and no later sync ever rebuilt it (the 3b review).
            self._shape = shape
        else:
            for card in self.cards:
                try:
                    card.paint(view)
                    if force:
                        card.redraw(view)
                    else:
                        card.redraw_if_moved(view)
                except Exception:  # one card's failure is not another's
                    logger.exception("plot %s of %s", card.index, self.model_id)
        self._follow_row()
        if self.pending_reveal is not None:
            # After the cards: the row an add made exists only once a
            # rebuild drew it.
            (path, index), self.pending_reveal = self.pending_reveal, None
            self._reveal(path, index)
        # Last: the drawer's checks read the cards and the selection.
        self.rail.refresh()

    def _gone(self) -> None:
        """This page's test is no longer a test of the open project — an
        `OpenProject` outcome swapped the library, or the Session was
        closed. Every read below `sync` raises for it, so the page went on
        showing the last project's cards while every event logged a
        traceback. It says what a page BUILT for an unknown model says, and
        a later event that finds the test again rebuilds (`_shape`).

        The sub-header and the strip go with the cards: they stayed live
        and stale — the last project's verdict, its baseline's name, and
        buttons whose Rerun started a job for a model this project does not
        have (the final review). `_paint` brings them back."""
        if self.gone or self.column is None:
            return
        self.gone = True
        self._close()
        self._close_row()
        self.pending_reveal = None
        if self.strip is not None:
            self.strip.forget()
        if self.badge is not None:
            self.badge.set_text("")
            self.badge.style("background-color: transparent")
        if self.meta is not None:
            self.meta.set_text("")
        for card in self.cards:
            card.dispose()
        self.cards = []
        self.focused = None
        self._shape = None
        self.column.clear()
        if self.tree is not None:
            self.tree.forget()
        if self.count is not None:
            self.count.set_text("")
        if self.focus_bar is not None:
            self.focus_bar.set_visibility(False)
        with self.column:
            ui.label(NOT_A_TEST.format(self.model_id)).classes("text-negative").mark(
                "gone"
            )
        # `sync` returns right after this, never reaching its own refresh:
        # an open Variables drawer kept the old test's rows, their checks
        # reading the Session for a model it no longer has. Its gone guard
        # (`VariablesDrawer._read`) empties it.
        self.rail.refresh()

    def flush(self) -> None:
        """Nothing coalesced: every event is handled as it comes."""

    def handle(self, event: dict[str, Any]) -> None:
        self.header.handle(event)
        self.rail.handle(event)
        known = self._known()
        syncs = event["event"] == "job" or (
            event["event"] in ("results", "spec", "loading", "opened")
            and self._mine(event)
        )
        if self.actions is not None and known and not syncs:
            # A sync's paint refreshes the bar; an event that does not sync
            # (another test's) still moves its availability.
            self.actions.refresh()
        if event["event"] == "job":
            # A finished job reloaded the trajectories under every plot and
            # no leaf's signature sees that (decision 10). The other states
            # move no data: they still sync — a lock has to reach the
            # panels — but they re-send no figure. Five job events per
            # Rerun re-sent every drawn plot five times (the 3b review).
            self.sync(force=event.get("state") in ("finished", "failed", "written"))
        elif syncs:
            self.sync()

    def _mine(self, event: dict[str, Any]) -> bool:
        """Whether an event says anything about THIS test. `results` and
        `spec` name the models they moved, a per-model `loading` step names
        the one it read; an event that names neither — the scan's steps,
        `opened` — is the whole project's and is this test's too. A page
        that synced on every model's event re-read the tree and repainted
        every row for a test it says nothing about.

        An EMPTY ``models`` names no model and is the whole project's:
        `Session.close()` emits one, and it is how a page whose test has
        gone hears about it."""
        models = event.get("models")
        if models:
            return self.model_id in models
        model = event.get("model")
        return model is None or model == self.model_id
