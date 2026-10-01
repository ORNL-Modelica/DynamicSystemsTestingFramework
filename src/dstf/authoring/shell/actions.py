"""Capabilities at a placement (brief §3.2; DESIGN.md "Buttons", "Disabled
states explain themselves"). `ActionBar` hosts SUITE and TEST: one button
per registered capability whose `placements` names this one, labelled by
the capability's `caption`, disabled with the sentence `available` gave
beside it; a click opens the bar's dialog hosting the capability's
`panel`, whose `submit` runs `act` and hands the outcome to the Session.
The bar knows no capability by name. The dialog is not rebuilt while open
(decision 4): a confirm list belongs to the moment of the click; an act
that applied something closes it, a raising one shows the error in it and
changes nothing, and one that returned nothing leaves it open — it changed
nothing to close over. There is no plot at either placement: a dialog's
`panel.figure` raises on a subscription.

`InlineHost` hosts the same contract the other way (brief §3.2): no button
and no dialog — every capability placed here has its panel built straight
into a slot the page owns, which is how the criterion editor, a tree
node's controls and a plot's criteria header (VARIABLE) are drawn without
the shell knowing a capability by name.
A host given a `RowTarget` lends its panels the page's form row under the
row it is drawn in (`Panel.row`) and the page's `reveal`; a bar's dialog
draws a panel's row inside the dialog, below the panel."""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING, Any

from nicegui import ui

from ..capabilities import Capability, Emphasis, Placement
from ..capabilities import FigureHook as FigureHookProtocol
from ..errors import USER_ERRORS, user_text
from ..session import Selection
from .figure_hook import FigureHook
from .widgets import Action, IconAction, MenuAction

if TYPE_CHECKING:
    from .shell import Shell

logger = logging.getLogger(__name__)

LOADING = "batch actions unlock when loading finishes"


def log_unless_users(e: BaseException, name: str) -> None:
    """Log a failed act unless a person's own edit raised it: `user_text`
    shows a ValueError's text alone, and a JSONDecodeError or a numpy shape
    error from a bug is one — it must still reach the log."""
    if not isinstance(e, USER_ERRORS):
        logger.exception("%s failed", name)


#: A form row's content: ``build(close)`` draws it; ``close`` takes it away.
RowBuild = Callable[[Callable[[], None]], None]


class _NoFigure:
    """`Panel.figure` where there is no plot (SUITE, TEST, a tree row): a
    subscription here is a contract misuse, and loud (decision 3)."""

    def on(self, kind: str, handler: Callable[[str, dict], None]) -> None:
        raise RuntimeError(f"no figure at this placement to subscribe {kind!r} to")


class ShellPanel:
    """The container a capability owns (§4.6 "Panel"): the dialog's slot,
    the figure hook, and `submit` — the one way an outcome reaches the
    Session."""

    figure: FigureHookProtocol

    def __init__(
        self,
        submit: Callable[..., bool],
        figure: FigureHookProtocol,
        row: Callable[[str, RowBuild], None] | None = None,
        reveal: Callable[[str], None] | None = None,
        lead: ui.element | None = None,
        target: RowTarget | None = None,
        fail: Callable[[BaseException], None] | None = None,
    ) -> None:
        self._submit, self.figure = submit, figure
        #: Says a refusal the way a failed `submit` does (a drop refused
        #: before it reaches the act); None where nothing is said.
        self._fail = fail
        self._row, self._reveal, self._lead = row, reveal, lead
        #: The row this panel is drawn in, where there is one.
        self._target = target

    @property
    def can_move(self) -> bool:
        return self._target is not None and self._target.drag

    def movable(
        self,
        on_drop: Callable[[str, str], None],
        zones: Callable[[], dict[str, dict[str, str | None]]] | None = None,
    ) -> bool:
        target = self._target
        if target is None or not target.drag:
            return False
        target.on_drop, target.zones = on_drop, zones
        if self._fail is not None:
            target.refuse = self._fail
        if zones is not None and target.grip is not None:
            target.grip.classes(remove="dstf-grip-off")
        return True

    def moved(self, node_path: str, text: str) -> None:
        # Quiet where there is no page (a dialog): the contract's no-op.
        if self._target is not None:
            self._target.page.moved(node_path, text)

    def submit(self, **inputs: Any) -> bool:
        return self._submit(**inputs)

    def row(self, title: str, build: RowBuild) -> None:
        # Loud, like `_NoFigure`: a placement that draws no row asked for
        # one is a contract misuse, not a form to drop silently.
        if self._row is None:
            raise RuntimeError("no row at this placement")
        self._row(title, build)

    def lead(self, build: Callable[[], None]) -> None:
        # Loud, like `row`: a placement with no lead place asked for one.
        if self._lead is None:
            raise RuntimeError("no lead place at this placement")
        self._lead.clear()
        with self._lead:
            build()

    def reveal(self, leaf_path: str) -> None:
        # Quiet where there is no page (a dialog): the contract's no-op.
        if self._reveal is not None:
            self._reveal(leaf_path)


def _say_nothing(_e: BaseException) -> None:
    """`RowTarget.refuse` where no panel has registered a drop."""


class RowTarget:
    """Where an InlineHost's panels open their rows: a slot the page owns
    under one row, and the page's one-open-at-a-time rule. ``key`` names the
    slot page-wide (``tree:<path>``, ``crit:<index>:<path>`` or
    ``plot:<index>``); ``node`` is the node path the forms act on — what the
    page watches to rebuild an open row. ``index`` is the plot the row is
    on (None for a tree row): the plot a criterion revealed from this row
    opens on, when that plot holds it."""

    def __init__(
        self, page: Any, key: str, row: ui.row, node: str, index: int | None
    ) -> None:
        self.page, self.key, self.row, self.node = page, key, row, node
        self.index = index
        #: The slot under the row, set by the row's owner once it exists:
        #: the host is built INSIDE the row, before the slot below it is.
        self.slot: ui.column | None = None
        #: Hides the errors the host drawn in this row shows; set by that
        #: host. The page calls it when a form row opens or closes under
        #: THIS row, and for no other row's.
        self.hush: Callable[[], None] = lambda: None
        #: The row's lead place, beside its status, where a panel draws
        #: what the node's kind is read from (`Panel.lead`); None in a row
        #: that has none (a criteria row). Emptied with the host.
        self.lead: ui.element | None = None
        #: Whether a node drawn in this row may be moved from it (U4, V2):
        #: set by the tree card, and by no other row's owner.
        self.drag = False
        #: The row's drag handle (the tree card's, hidden until a panel
        #: makes the row draggable; None on the root's), and what a panel
        #: registered through `Panel.movable`: a drop on the row, the zones
        #: of a drag from it.
        self.grip: ui.element | None = None
        self.on_drop: Callable[[str, str], None] | None = None
        self.zones: Callable[[], dict[str, dict[str, str | None]]] | None = None
        #: Says a drop refused before it reached the panel's act, the way
        #: that panel says its own refusals (`TreeCard.dropped`).
        self.refuse: Callable[[BaseException], None] = _say_nothing

    def open(self, title: str, build: RowBuild) -> None:
        self.page.open_row(self, title, build)


class ActionBar:
    """The bar and its one dialog, at SUITE or TEST."""

    def __init__(
        self,
        shell: Shell,
        placement: Placement,
        selection_of: Callable[[], Selection],
        *,
        marker: str,
        icons: bool = False,
        emphasis: Callable[[Capability], Emphasis | None] | None = None,
    ) -> None:
        self.shell, self.placement, self.selection_of = shell, placement, selection_of
        self.marker = marker
        #: Draw each capability that declares an ``icon`` (a Material icon
        #: name) as an icon button; one that declares none keeps its text.
        self.icons = icons
        #: How the page's situation draws each capability: NEXT filled,
        #: MENU in the ⋮ menu, None quiet. Without it every button is
        #: today's outlined one.
        self.emphasis = emphasis
        self.errors: dict[
            str, str
        ] = {}  # capability name -> the error its dialog shows
        self.actions: dict[str, Action | IconAction | MenuAction] = {}
        #: What the buttons were built from: each placed capability's name
        #: and its emphasis. A move in either rebuilds them.
        self.built: list[tuple[str, Emphasis | None]] = []
        self.dialog = ui.dialog()  # one element, rebuilt per open
        self.container = (
            ui.row().classes("dstf-actions items-center gap-3 wrap").mark(marker)
        )
        self.refresh()

    def clear(self) -> None:
        """No buttons, and none built from: the next `refresh` draws them
        afresh (a page whose test has gone)."""
        self.dialog.close()
        self.container.clear()
        self.actions, self.built = {}, []

    def refresh(self) -> None:
        """Captions and availability from the current selection; the buttons
        are rebuilt only when the capabilities placed here, or the emphasis
        the page's situation gives them, changed."""
        view = self.shell.view_for(self.selection_of())
        placed = self.shell.placed(self.placement, view)
        emphasis = self.emphasis
        built = [
            (cap.name, emphasis(cap) if emphasis is not None else None)
            for cap, _ in placed
        ]
        if built != self.built:
            self._build([cap for cap, _ in placed], [e for _, e in built])
            self.built = built
        loading = self.shell.load.in_progress
        for cap, avail in placed:
            if loading:
                reason: str | None = LOADING
            elif avail is True:
                reason = None
            else:
                reason = avail if isinstance(avail, str) else "not available"
            try:
                caption = cap.caption(view)
            except Exception as e:  # the label is the fallback; the page draws
                caption = cap.label
                self.shell.report_once(cap.name, "caption", e)
            self.actions[cap.name].set(caption, reason)

    def _build(self, caps: list[Capability], emphases: list[Emphasis | None]) -> None:
        self.container.clear()
        self.actions = {}
        menu = [cap for cap, e in zip(caps, emphases) if e is Emphasis.MENU]
        with self.container:
            for cap, e in zip(caps, emphases):
                if e is Emphasis.MENU:
                    continue
                marker = f"action:{cap.name}"
                icon = getattr(cap, "icon", None) if self.icons else None
                if icon is not None:
                    self.actions[cap.name] = IconAction(
                        partial(self.open, cap), icon=icon, marker=marker
                    )
                    continue
                if self.emphasis is None:
                    props = "outline dense no-caps"
                elif e is Emphasis.NEXT:
                    props = "unelevated dense no-caps color=primary"
                else:
                    props = "flat dense no-caps"
                self.actions[cap.name] = Action(
                    partial(self.open, cap), props=props, marker=marker
                )
            if menu:
                # Named, with no tooltip: a tooltip covered the open menu's
                # first item. It says whether its menu is open, as the
                # header's ⋮ does.
                more = (
                    ui.button(icon="more_vert")
                    .props('flat dense round aria-label="More actions"')
                    .props('aria-haspopup="menu" aria-expanded="false"')
                    .mark("action-more")
                )
                with more, ui.menu() as opened:
                    opened.on_value_change(
                        lambda e, more=more: more.props(
                            f'aria-expanded="{"true" if e.value else "false"}"'
                        )
                    )
                    for cap in menu:
                        self.actions[cap.name] = MenuAction(
                            partial(self.open, cap),
                            marker=f"menu-action:{cap.name}",
                        )

    def open(self, cap: Capability) -> None:
        """The capability's panel, in the dialog, over the selection as it is
        at this click."""
        view = self.shell.view_for(self.selection_of())

        def close() -> None:
            self.dialog.close()

        self.dialog.clear()
        with self.dialog, ui.card().classes("dstf-dialog"):
            ui.label(cap.label).classes("text-h6")
            error = ui.label(self.errors.get(cap.name, "")).classes("text-negative")
            error.mark(f"error:{cap.name}")
            error.set_visibility(bool(error.text))
            slot = ui.column().classes("w-full").mark(f"panel:{cap.name}")
            # The form row's column, below the panel, exists BEFORE the
            # panel runs: a panel may open its row while it is built, and
            # a column made after it raised NameError from `row` (the Task
            # 5 review).
            form = ui.column().classes("w-full gap-2")

            def submit(**inputs: Any) -> bool:
                try:
                    outcome = cap.act(view, **inputs)
                    self.shell.session.apply(outcome)
                except Exception as e:  # shown here; a raising act changes nothing
                    log_unless_users(e, cap.name)
                    self.errors[cap.name] = user_text(e)
                    error.set_text(self.errors[cap.name])
                    error.set_visibility(True)
                    return False
                self.errors.pop(cap.name, None)
                if outcome is not None:
                    # Like `InlineHost.on_applied`: an act returning None
                    # changed nothing. Every plot sends `{"autosize": true}`
                    # at mount, and a subscribing panel answering it with
                    # None must not close the dialog it sits in.
                    close()
                return True

            def row(title: str, build: RowBuild) -> None:
                """A dialog has no row to open one under: the form is drawn
                in the dialog, below the panel, one at a time."""
                form.clear()

                def close_row() -> None:
                    form.clear()

                with form:
                    ui.label(title).classes("text-subtitle2")
                    build(close_row)

            with slot:
                try:
                    cap.panel(view, ShellPanel(submit, _NoFigure(), row=row))
                except Exception as e:  # shown in the slot, never breaks the page
                    ui.label(f"{type(e).__name__}: {e}").classes("text-negative")
            ui.button("Close", on_click=close).props("flat dense no-caps").mark(
                f"close:{cap.name}"
            )
        self.dialog.open()


class InlineHost:
    """Every capability placed at `placement`, its panel built inline in
    this host's column — no button, no heading (brief §3.2: Edit criteria
    and Edit on plot read as one editor; a node's controls are a panel).
    An unavailable capability keeps its place and says why (brief §3.3) —
    once per sentence: a second capability refused for the same reason
    draws no line of its own.
    `build()` rebuilds from the selection as it is now; `clear()` empties
    the column and drops this host's figure handlers and the form row its
    panels opened. A raising act shows above that capability's panel — in
    a host drawn in a row, on a line of its own under the row — and
    changes nothing. The error belongs to that row: the next success
    hides it in place, and so does a form row opening or closing under
    THAT row (`hush`), or the host rebuilding; another row's form does
    not. The panel is not rebuilt, so a field being typed in survives.
    `on_applied` is called with that capability's name when its act
    returned an outcome, and only then: an act returning ``None`` changed
    nothing, and the rebuild it would trigger discards a half-typed row
    for nothing. `follow()` is how a host that is never rebuilt keeps up
    with `available`: a job's lock comes and goes without the tree's shape
    moving."""

    def __init__(
        self,
        shell: Shell,
        placement: Placement,
        selection_of: Callable[[], Selection],
        *,
        figure: FigureHook | None,
        marker: str,
        on_applied: Callable[[str], None] | None = None,
        row: RowTarget | None = None,
    ) -> None:
        self.shell, self.placement, self.selection_of = shell, placement, selection_of
        self.figure, self.marker, self.on_applied = figure, marker, on_applied
        self.row = row
        if row is not None:
            row.hush = self.hush
        self.errors: dict[str, ui.label] = {}
        self.cells: dict[str, ui.column] = {}  # capability name -> its place
        #: capability name -> the `available` answer its place was built on
        self.answers: dict[str, bool | str] = {}
        self.column = ui.column().classes("w-full gap-2").mark(marker)
        # A click inside the editor is the editor's: without this, a click on
        # a node's wrap icon also reached the row that hosts it and opened its
        # editor (the review, in Chromium).
        self.column.on("click", js_handler="(e) => e.stopPropagation()")

    def _owner(self, name: str) -> str:
        return f"{self.marker}:{name}"

    def hush(self) -> None:
        """Hide every error this host shows, as a form row opens or closes
        under this host's row: a row's error goes with the row. A refused
        act's error stayed up after its form was reopened (the Task 6
        review)."""
        for label in self.errors.values():
            if not label.is_deleted:
                label.set_visibility(False)

    def clear(self) -> None:
        if self.figure is not None:
            for name in self.cells:
                self.figure.clear(self._owner(name))
        if self.row is not None:
            # A form row lives only as long as the host whose panel opened
            # it: rebuilt under a job's lock, the host said "rerun is
            # running" while its row stayed open with live buttons, and an
            # act from it staged nothing and showed nothing — the error
            # label had gone with the rebuild (the Task 5 review).
            self.row.page.drop_row(self.row)
            # Its error labels live in the row's slot, not in the column.
            for label in self.errors.values():
                if not label.is_deleted:
                    label.delete()
            # And what its panels drew in the row's lead place: a host
            # rebuilt to its why-line leaves the kind to the row's text.
            if self.row.lead is not None and not self.row.lead.is_deleted:
                self.row.lead.clear()
            # And what they registered for the drag: a host rebuilt under
            # a job's lock takes no drop and offers no handle.
            self.row.on_drop = self.row.zones = None
            self.row.refuse = _say_nothing
            if self.row.grip is not None and not self.row.grip.is_deleted:
                self.row.grip.classes(add="dstf-grip-off")
        self.column.clear()
        self.errors, self.cells, self.answers = {}, {}, {}

    def build(self, skip: str | None = None) -> None:
        """Rebuild every capability's place — but ``skip``'s, left untouched
        (the panel whose own submit this follows: rebuilding it would destroy
        the control being typed in). A changed set of capabilities rebuilds
        them all. ``skip`` misleads in one state: a skipped capability that
        has become UNAVAILABLE keeps its stale panel and gets no why-line
        until the next full ``build()``."""
        view = self.shell.view_for(self.selection_of())
        placed = self.shell.placed(self.placement, view)
        if skip is None or [cap.name for cap, _ in placed] != list(self.cells):
            self.clear()
            skip = None
        # The why-lines drawn so far. Edit criteria and Edit on plot are both
        # refused on a test the Session has no tree for, or one a job holds,
        # and the editor read the same sentence twice (the review).
        said: set[str] = set()
        for cap, avail in placed:
            if cap.name == skip:
                continue
            if cap.name not in self.cells:
                with self.column:
                    self.cells[cap.name] = ui.column().classes("w-full gap-1")
            elif self.figure is not None:
                self.figure.clear(self._owner(cap.name))
            cell = self.cells[cap.name]
            cell.clear()
            with cell:
                self._build_one(cap, avail, view, said)
            # Not the skipped capability's: its place still shows the answer
            # it was built on, and `follow` is what brings it back in line.
            self.answers[cap.name] = avail

    def follow(self) -> None:
        """Rebuild when an `available` answer has moved since this host was
        built, and only then (the Task 4 review): a row that is repainted
        rather than rebuilt otherwise keeps the answer of the moment it was
        drawn — a tree opened during a rerun kept "rerun is running on …"
        after the job ended, and one opened before the job kept controls
        that a lock refuses. A full build resets the error labels, which is
        why hiding a stale one is not this method's job."""
        view = self.shell.view_for(self.selection_of())
        answers = {
            cap.name: avail for cap, avail in self.shell.placed(self.placement, view)
        }
        if answers != self.answers:
            self.build()

    def _build_one(
        self, cap: Capability, avail: bool | str, view: Any, said: set[str]
    ) -> None:
        slot = self.row.slot if self.row is not None else None
        if slot is not None:
            # A host drawn in a row puts its error on a line of its own in
            # the row's slot, under the icons: in the host, a long refusal
            # widened the host and pushed its icons left of every other
            # row's (the Task 6 review, in Chromium).
            with slot:
                error = ui.label("").classes("text-negative text-xs self-end")
        else:
            error = ui.label("").classes("text-negative text-xs")
        error.mark(f"error:{cap.name}")
        error.set_visibility(False)
        self.errors[cap.name] = error
        if avail is not True:
            sentence = avail if isinstance(avail, str) else "not available"
            if sentence not in said:
                said.add(sentence)
                ui.label(sentence).classes("dstf-why").mark(f"why:{cap.name}")
            return

        def fail(e: BaseException) -> None:
            log_unless_users(e, cap.name)
            error.set_text(user_text(e))
            error.set_visibility(True)

        def submit(**inputs: Any) -> bool:
            try:
                outcome = cap.act(view, **inputs)
                self.shell.session.apply(outcome)
            except Exception as e:  # shown, and changes nothing
                fail(e)
                return False
            error.set_visibility(False)
            if outcome is not None and self.on_applied is not None:
                self.on_applied(cap.name)
            return True

        figure: FigureHookProtocol = _NoFigure()
        if self.figure is not None:
            figure = self.figure.owned(self._owner(cap.name))
        with ui.column().classes("w-full gap-2").mark(f"panel:{cap.name}"):
            try:
                cap.panel(
                    view,
                    ShellPanel(
                        submit,
                        figure,
                        row=None if self.row is None else self.row.open,
                        reveal=None
                        if self.row is None
                        else partial(self.row.page.reveal, index=self.row.index),
                        lead=None if self.row is None else self.row.lead,
                        target=self.row,
                        fail=fail,
                    ),
                )
            except Exception as e:  # shown in the slot, never breaks the page
                ui.label(f"{type(e).__name__}: {e}").classes("text-negative")
