"""The Test page (brief §3.2; roadmap §3): one test — its own header as a
second row of the shared one, its statistics and its criteria tree each
closed to a line, and its
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
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from nicegui import ui

from ...comparison.tree_spec import reads_a_statistic
from ...config import ConfigError
from ...discovery.patch_apply import PatchError
from ...discovery.plots import PlotExtra, ResolvedPlot
from ...reporting.status import SCORING_FAILED, classify, classify_with_class
from ...reporting.tree_rows import leaves_of
from ...storage.ref_index import RefIndex
from ..capabilities import plural
from ..errors import user_text
from ..layout import layout_op, remove_context, remove_watch, reset_op
from ..session import JobRunningError, LockedError
from ..situation import Strip, new_variables, strip_for
from ..tree_pointer import node_or_none
from .actions import ActionBar, RowBuild, RowTarget
from .header import Header
from .plot_card import UNREAD, PlotCard, folded
from .rail import Rail
from .stats_card import StatsCard
from .strip import StatusStrip
from .tabstate import connected_tab_state
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

#: `TestPage.open`'s index for a criterion edited in the Statistics card
#: (U3): the page's one editor is `(plot index, leaf path)`, or `(STATS,
#: leaf path)` — no plot is ever at index -1.
STATS = -1


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
        self.stats: StatsCard | None = None
        self.cards: list[PlotCard] = []
        self.count: ui.label | None = None
        self.column: ui.column | None = None
        self._shape: Any = None
        #: Whether the page is showing "… is not a test of this project"
        #: instead of a test (`_gone`).
        self.gone = False
        #: The one open editor: (plot index, leaf path), or (STATS, leaf
        #: path) for a statistics criterion's, in the Statistics card.
        self.open: tuple[int, str] | None = None
        #: The one open form row: its target, its title, its build, its
        #: column, and the node's raw JSON it was last built from.
        self.form: tuple[RowTarget, str, RowBuild, ui.column, str] | None = None
        #: A criterion to open at the end of the next sync, and the plot
        #: it was asked from (`reveal`).
        self.pending_reveal: tuple[str, int | None] | None = None
        #: A move's row to bring into view and the line it says, for the
        #: end of the next sync (`moved`).
        self.pending_moved: tuple[str, str] | None = None
        #: Whether the move's own `spec` event is still to come: it is the
        #: move's, and takes no line away (`handle`). A flag apart from
        #: `pending_moved`: another event of this test queued in the same
        #: tick, ahead of it, syncs first and says the line.
        self.move_spec_due = False
        self.focused: int | None = None  # the one plot Focus is showing
        self.focus_bar: ui.row | None = None
        self.focus_text: ui.label | None = None
        self.focus_all: ui.button | None = None
        self.focus_switch: ui.select | None = None
        #: The plot the Variables drawer's checkboxes act on (the owner's
        #: ruling, Q5 A): the first until a click on a card, or the
        #: drawer's "+ new plot", selects another. Its card wears
        #: `dstf-plot-selected`.
        self.selected = 0
        #: The selected plot's subject, which a rebuild re-selects wherever
        #: it moved; None when a gesture placed the selection by index.
        self.follow: tuple[str, ...] | None = None

    async def build(self, model_id: str) -> None:
        self.client = ui.context.client
        state = await connected_tab_state(self.client)
        if state is None:
            return
        self.state = state  # the tab's, shared with its Suite page
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
            # Above the tree (the plot-model board): what the run recorded,
            # and the `+` that makes a statistic a criterion.
            self.stats = StatsCard(self)
            self.tree = TreeCard(self)
            self.count = (
                ui.label().classes("text-secondary text-xs").mark("plots:count")
            )
            self.focus_bar = (
                ui.row().classes("dstf-focus-bar items-center gap-3 w-full")
            ).mark("focus-bar")
            with self.focus_bar:
                self.focus_text = ui.label().mark("focus-bar:text")
                # Every plot by its subject (Q11 c), plain text (C4):
                # choosing one focuses it.
                self.focus_switch = (
                    ui.select({}, on_change=lambda e: self.focus_to(e.value))
                    .props("dense options-dense")
                    .mark("focus-switch")
                )
                self.focus_switch.props["aria-label"] = "Plot"
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

    def result(self) -> Any:
        project = self.shell.session.project
        return project.results.get(self.model_id) if project is not None else None

    def run_state(self) -> str:
        """The ONE run-state rule every line of the page words its reason
        by: "none" — no result — "failed" when the result is a failed run,
        else "ok". It reads the result the badge was scored from, so the
        two cannot disagree: the reader hands in no result for a test the
        last run never finished (its manifest entry still pending, D145),
        and a failed one where status.json recorded that run failed — the
        badge's SIM_FAIL, beside which the strip said "No result yet"
        while it asked the manifest instead."""
        result = self.result()
        if result is None:
            return "none"
        return "ok" if result.success else "failed"

    def plots_withheld(self) -> bool:
        """O2's gate: a result that is not a success has no plots whatever
        its tree and extras say, and a watch plot made on it would draw
        nothing — a run that failed (`run_state`'s "failed"). A run that
        never finished the test hands in no result (D145), and withholds
        nothing: there is nothing to draw."""
        result = self.result()
        return result is not None and not result.success

    def _state_reason(self) -> str | None:
        """The clause a run state that is not "ok" gives every line, or
        None."""
        state = self.run_state()
        if state == "failed":
            return "the run failed"
        if state == "none":
            return "this test has no result"
        return None

    def unscored_reason(self) -> str:
        """Why this test has no tree, as a clause the line it is read into
        completes ("nothing scored: this test has no baseline"): its run
        failed or it has no result (`run_state`, first), its scoring
        raised, or it has no baseline to be scored against. A model whose
        scoring raised keeps its result and has no comparison at all — the
        Suite row says `scoring failed` for the same state — so "no
        baseline" would name the wrong cause."""
        reason = self._state_reason()
        if reason is not None:
            return reason
        if self.shell.session.comparisons.get(self.model_id) is None:
            return SCORING_FAILED
        return "this test has no baseline"

    def _no_plots_reason(self, from_tree: bool) -> str:
        """Why there is no PLOT, which is not why there is no tree: a test
        with no tree still draws one plot per tracked variable, so a page
        with none has a failed run or no result (`run_state`, first: a
        failed run has no plots whatever its tree says, O2), a result with
        nothing in it — or a tree whose every criterion scores a statistic
        (a stored-scalar leaf names no series, so it derives no plot).
        ``from_tree`` is `Session.plots_from_tree`, never whether there is
        a tree VIEW: a test with no baseline has none, and its authored
        tree still names its plots."""
        reason = self._state_reason()
        if reason is not None:
            return reason
        if from_tree:
            return "this test's criteria score statistics, not signals"
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
        self._unsay()
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
        if not any(path in card.rows for card in self.cards):
            # A statistics criterion has no plot: the card is its home.
            self.open_criterion(path, None)
            if self.open == (STATS, path):
                self.client.run_javascript(
                    'document.querySelector(".dstf-stats .dstf-editor")'
                    '?.scrollIntoView({block: "nearest"})'
                )
            return
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
        review). A statistics criterion's editor is the Statistics card's
        (U3): it has no plot."""
        self._unsay()
        if index is None and self.stats is not None:
            view = self._view()
            if self.stats.row_for(path) is not None:
                self._close_row()
                self._close()
                if self.stats.open_editor(path, view):
                    self.open = (STATS, path)
                self._redraw_moved(view)
                return
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

    def toggle_stats_editor(self, path: str, *_: Any) -> None:
        """A scored Statistics row's own click: its criterion's editor
        opens under it, or closes if it was the open one."""
        if self.open == (STATS, path):
            self.close_editor()
        else:
            self.open_criterion(path)

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
        index, self.open = self.open[0], None
        if index == STATS:
            if self.stats is not None:
                self.stats.close_editor()
            return
        self.cards[index].close_editor()

    def moved(self, path: str, text: str) -> None:
        """A move went through (U4, V4; `Panel.moved`): the editor and the
        form row close NOW, before the sync the move's events bring — two
        criteria on one variable trading places move no shape, and an
        editor left open showed the other one — and the moved row and its
        line wait for that sync, which draws the row."""
        view = self._view()
        self._close()
        self._close_row()
        self._redraw_moved(view)
        self._unsay()
        self.pending_moved = (path, text)
        self.move_spec_due = True

    def _unsay(self) -> None:
        """Take a move's line away: the next form, editor or edit."""
        if self.tree is not None:
            self.tree.unsay()

    def _redraw_moved(self, view: Any) -> None:
        """The signature is the one thing that decides a redraw: only the
        card whose rings came or went is re-sent."""
        for card in self.cards:
            card.redraw_if_moved(view)

    def jump_to(self, path: str) -> None:
        """The closed tree line's jump (board 1A): open that criterion and
        bring the row it opened under into view — a plot's criteria row, or
        a Statistics row (U3: every leaf of a tree view is on one or the
        other). A criterion with no row drawn opens nothing and scrolls to
        nothing: with another editor open, the jump scrolled to THAT row."""
        self.open_criterion(path)
        if self.open is not None and self.open[1] == path:
            ui.run_javascript(
                'document.querySelector(".dstf-crit-open")'
                '?.scrollIntoView({block: "center"})'
            )

    # -- the cards -------------------------------------------------------------

    def rebuild(self, view: dict | None) -> None:
        """The cards, from the plots as they are now (`Session.plots`: one
        per scored variable or family in tree order, then the watch plots;
        one per tracked variable with no tree). ``view`` and the label
        scope are read ONCE here and handed to every card — a card that
        read them for itself cost one read per plot (the Task 5-6
        review)."""
        assert self.column is not None and self.count is not None
        session = self.shell.session
        self.gone = False
        # The rows the editor hangs under go with the cards: the criterion
        # it was open on may not even exist after this. Closed WITHOUT a
        # redraw — these cards are about to be dropped, and one of them may
        # be a plot the staged layout no longer has. The Statistics card's
        # editor hangs under none of them.
        if self.open is not None and self.open[0] != STATS:
            self._close()
        # Focus names an INDEX, and after this the indices may name other
        # plots; the plots a user had drawn are re-drawn where they are left.
        self.focused = None
        # The selection follows its PLOT, by subject: a watch plot given a
        # criterion becomes derived and moves to where the tree names it,
        # and the index it had names another plot. A gesture that placed
        # the selection itself (`stage_layout`'s ``select``) left none.
        follow = self.follow
        shown = {card.index for card in self.cards if card.plot is not None}
        for card in self.cards:
            card.dispose()
        self.column.clear()
        plots = session.plots(self.model_id)
        # The label scope too: it is one fact about the TEST, and a card
        # that read it for itself cost one `plots` + `tracked` read per
        # plot — 35 for a five-plot test.
        scope = session.variable_texts(self.model_id)
        with self.column:
            self.cards = [
                PlotCard(self, plot.index, plot, view, scope) for plot in plots
            ]
        for card in self.cards:
            # A rebuild must not blank what the user was looking at: the
            # browser's observer has already fired for those slots and will
            # not fire again.
            if card.index in shown:
                card.show(view)
        self._paint_focus(view)
        # The selection names an INDEX too; it is kept — on the plot it
        # followed, else clamped to the cards there are now — and its ring
        # painted on the new cards. The drawer follows at the end of the
        # sync that called this, once.
        moved = next(
            (
                plot.index
                for plot in plots
                if follow and any(name in plot.subject for name in follow)
            ),
            None,
        )
        self._ring(self.selected if moved is None else moved)
        self.count.set_text(
            self._count_text(session.plots_from_tree(self.model_id), plots)
        )
        self._observe()

    def _count_text(self, from_tree: bool, plots: Sequence[ResolvedPlot]) -> str:
        """What the cards are: derived from the criteria, or — nothing
        scored — one per tracked variable or array; counted apart from a
        person's watch plots, which are neither."""
        if not plots:
            return f"no plots: {self._no_plots_reason(from_tree)}"
        watch = sum(1 for plot in plots if plot.watch)
        if watch == len(plots):
            # Nothing derived: "0 plots · derived from the criteria" counted
            # plots that are not there.
            return f"{plural(watch, 'watch plot')} · no criteria on a signal yet"
        why = (
            "derived from the criteria"
            if from_tree
            else "one per tracked variable or array"
        )
        return f"{plural(len(plots) - watch, 'plot')} · {why}" + (
            f" · {watch} watch" if watch else ""
        )

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
        self.follow = self.cards[self.selected].resolved.subject if self.cards else None
        for card in self.cards:
            if card.index == self.selected:
                card.card.classes(add="dstf-plot-selected")
            else:
                card.card.classes(remove="dstf-plot-selected")

    def stage_layout(
        self, extras: Sequence[PlotExtra], select: int | None = None
    ) -> bool:
        """Stage ``extras`` — the test's plot extras after one gesture — as
        the ONE op on `/plots`, and say whether it was staged. An edit that
        changes nothing stages nothing: extras equal to the ones in effect,
        or none on an entry with no key (`layout_op`'s ``None``). ``select``
        is set BEFORE the stage — the rebuild its `spec` event causes reads
        it — and put back if the Session refuses: a refused edit is said,
        and nothing else changes (3c, planner's choice 10)."""
        session = self.shell.session
        if tuple(extras) == session.layout(self.model_id):
            return False
        op = layout_op(self.model_id, session.spec.entry(self.model_id), extras)
        if op is None:
            return False
        before = self.selected, self.follow
        if select is not None:
            self.selected, self.follow = select, None
        try:
            session.stage([op])
        except (LockedError, PatchError, ConfigError, JobRunningError) as e:
            self.selected, self.follow = before
            ui.notify(user_text(e), type="warning")
            return False
        return True

    def remove_watch(self, index: int, var: str) -> None:
        """A watch card's Remove plot: watch plot ``index`` (of ``var``)
        gone, the selection kept on the SAME plot — a removal above it
        shifts it up by one; the selected plot removed selects the one now
        at its index, the last if it was the last (the rebuild clamps). The
        extras are read NOW: a card drawn before the last change may name
        an entry that has gone, and that removes nothing."""
        try:
            extras = remove_watch(self.shell.session.layout(self.model_id), var)
        except KeyError:
            return
        self.stage_layout(extras, select=self.selected - (index < self.selected))

    def remove_context(self, index: int, name: str) -> None:
        """A context series' `×`: ``name`` off plot ``index``, as the plots
        are NOW — a card drawn before the last change may name a plot that
        has moved."""
        plots = self.shell.session.plots(self.model_id)
        if not 0 <= index < len(plots) or name not in plots[index].context:
            return
        extras = self.shell.session.layout(self.model_id)
        self.stage_layout(remove_context(extras, plots[index], name))

    def reset_layout(self) -> None:
        """Stage removing the entry's ``plots`` key: every context series
        and watch plot goes; the derived plots stay. Refused, it is said,
        like any layout edit."""
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

    def focus_to(self, index: Any) -> None:
        """The switcher's choice: focus that plot — the same `focus` path,
        so an editor or a form row on the card it hides closes. Its own
        repaint (`set_options` to the plot already focused) does nothing:
        `focus` on the focused plot would END the focus."""
        if isinstance(index, int) and index != self.focused:
            self.focus(index)

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
        # The sentence counts; the switcher beside it names the plot — the
        # way the plot names itself, its texts, a family folded as on its
        # title. The name in both said it twice.
        self.focus_text.set_text(f"Showing 1 of {plots}")
        if self.focus_switch is not None:
            self.focus_switch.set_options(
                {
                    c.index: folded(c.resolved.subject, c.texts)
                    + (" · watch" if c.resolved.watch else "")
                    for c in self.cards
                },
                value=focused,
            )
        self.focus_all.set_text(f"Show all {plots}")
        self.cards[focused].show(view)

    def shape(self, view: dict | None) -> Any:
        """What the cards are BUILT from and cannot be repainted through:
        the criteria and their variables, the plots themselves — each
        one's subject, context, whether it is a watch plot, and what the
        result lacks. NOT a leaf's
        metric — a mode switch repaints a row's name in place. NOT a
        statistics leaf: it is on no plot, and a `+` in the Statistics card
        rebuilt every plot card — Focus ended, a zoom was lost, every figure
        was re-sent."""
        session = self.shell.session
        return (
            tuple(
                (leaf["path"], leaf.get("variable"))
                for leaf in leaves_of(view)
                if not reads_a_statistic(
                    str(leaf.get("metric")), leaf.get("params") or {}
                )
            ),
            tuple(
                (plot.subject, plot.context, plot.watch, plot.missing)
                for plot in session.plots(self.model_id)
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
        has_result = self.run_state() != "none"
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
            # The merged TestModel carries the EFFECTIVE tree (`stage`
            # re-merges `project.tests`): a staged criterion counts.
            new_variables=new_variables(
                project.test(model_id),
                self.result() if has_result else None,
                project.config.diagnostic_variables,
            ),
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
        if self.stats is not None:
            self.stats.refresh(view)
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
        if self.pending_moved is not None and self.tree is not None:
            # After the tree's rows: the moved row exists once they are
            # rebuilt.
            (path, text), self.pending_moved = self.pending_moved, None
            self.tree.say(path, text)
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
        self.pending_reveal = self.pending_moved = None
        self.move_spec_due = False
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
        if self.stats is not None:
            self.stats.forget()
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
            event["event"] in ("results", "spec", "loading", "opened", "overlays")
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
        elif syncs and event["event"] == "overlays":
            # The sibling-backend overlays arrived after the plots were
            # drawn without them (`Session._overlays_ready`); no leaf's
            # signature sees an overlay, so the drawn plots are re-sent.
            self.sync(force=True)
        elif syncs:
            if event["event"] == "spec":
                if self.move_spec_due:
                    # The move's own event: its line is said by this sync,
                    # or was by an event ahead of it.
                    self.move_spec_due = False
                else:
                    # An edit after a move: the line has said its piece.
                    self._unsay()
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
