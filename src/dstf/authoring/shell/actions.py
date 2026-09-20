"""Capabilities at a placement (brief §3.2; DESIGN.md "Buttons", "Disabled
states explain themselves"): one outlined button per registered capability
whose `placements` names this one, labelled by the capability's `caption`,
disabled with the sentence `available` gave beside it; a click opens the
bar's dialog hosting the capability's `panel`, whose `submit` runs `act`
and hands the outcome to the Session. The bar knows no capability by name.
The dialog is not rebuilt while open (decision 4): a confirm list belongs
to the moment of the click; an act that applied something closes it, a
raising one shows the error in it and changes nothing, and one that
returned nothing leaves it open — it changed nothing to close over. A bar built with `figure=`
lends that hook to the open panel alone, and takes it back however the
dialog closes.

`InlineHost` hosts the same contract the other way (brief §3.2): no button
and no dialog — every capability placed here has its panel built straight
into a slot the page owns, which is how the criterion editor and a tree
node's controls are drawn without the shell knowing a capability by name."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING, Any

from nicegui import ui

from ..capabilities import Capability, Placement
from ..capabilities import FigureHook as FigureHookProtocol
from ..session import Selection
from .figure_hook import FigureHook
from .widgets import Action

if TYPE_CHECKING:
    from .shell import Shell

LOADING = "batch actions unlock when loading finishes"


class _NoFigure:
    """`Panel.figure` where there is no plot (SUITE, TEST): a subscription
    here is a contract misuse, and loud (decision 3)."""

    def on(self, kind: str, handler: Callable[[str, dict], None]) -> None:
        raise RuntimeError(f"no figure at this placement to subscribe {kind!r} to")


class ShellPanel:
    """The container a capability owns (§4.6 "Panel"): the dialog's slot,
    the figure hook, and `submit` — the one way an outcome reaches the
    Session."""

    figure: FigureHookProtocol

    def __init__(self, submit: Callable[..., None], figure: FigureHookProtocol) -> None:
        self._submit, self.figure = submit, figure

    def submit(self, **inputs: Any) -> None:
        self._submit(**inputs)


class ActionBar:
    """The bar and its one dialog. A `figure=` hook is lent to the panel a
    click opens, under that capability's owner key, and `_release` takes it
    back on every way the dialog can close — Close, a successful submit, a
    dismissal (Esc, the backdrop), and the next open, whichever capability's.
    An invisible panel must not go on staging edits from drags."""

    def __init__(
        self,
        shell: Shell,
        placement: Placement,
        selection_of: Callable[[], Selection],
        *,
        marker: str,
        figure: FigureHook | None = None,
    ) -> None:
        self.shell, self.placement, self.selection_of = shell, placement, selection_of
        self.marker, self.figure = marker, figure
        self.errors: dict[
            str, str
        ] = {}  # capability name -> the error its dialog shows
        self.actions: dict[str, Action] = {}
        self._owner: str | None = None  # the open panel's key on the hook
        self.dialog = ui.dialog()  # one element, rebuilt per open
        # Esc and a backdrop click close the dialog without reaching `close`.
        self.dialog.on_value_change(lambda e: None if e.value else self._release())
        self.container = (
            ui.row().classes("dstf-actions items-center gap-3 wrap").mark(marker)
        )
        self.refresh()

    def refresh(self) -> None:
        """Captions and availability from the current selection; the buttons
        are rebuilt only when the set of capabilities placed here changed."""
        view = self.shell.view_for(self.selection_of())
        placed = self.shell.placed(self.placement, view)
        if [cap.name for cap, _ in placed] != list(self.actions):
            self.container.clear()
            self.actions = {}
            with self.container:
                for cap, _ in placed:
                    self.actions[cap.name] = Action(
                        partial(self.open, cap),
                        props="outline dense no-caps",
                        marker=f"action:{cap.name}",
                    )
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

    def _release(self) -> None:
        """Take the figure back from the panel that had it: its handlers go
        with it, so a dialog that is no longer shown delivers nothing."""
        if self.figure is not None and self._owner is not None:
            self.figure.clear(self._owner)
        self._owner = None

    def open(self, cap: Capability) -> None:
        """The capability's panel, in the dialog, over the selection as it is
        at this click. The panel that had the figure — this capability's on a
        second open, another's on a switch — gives it back first."""
        self._release()
        view = self.shell.view_for(self.selection_of())
        owner = f"{self.marker}:{cap.name}"
        figure: FigureHookProtocol = _NoFigure()
        if self.figure is not None:
            figure = self.figure.owned(owner)
        self._owner = owner

        def close() -> None:
            self._release()
            self.dialog.close()

        self.dialog.clear()
        with self.dialog, ui.card().classes("dstf-dialog"):
            ui.label(cap.label).classes("text-h6")
            error = ui.label(self.errors.get(cap.name, "")).classes("text-negative")
            error.mark(f"error:{cap.name}")
            error.set_visibility(bool(error.text))
            slot = ui.column().classes("w-full").mark(f"panel:{cap.name}")

            def submit(**inputs: Any) -> None:
                try:
                    outcome = cap.act(view, **inputs)
                    self.shell.session.apply(outcome)
                except Exception as e:  # shown here; a raising act changes nothing
                    self.errors[cap.name] = f"{type(e).__name__}: {e}"
                    error.set_text(self.errors[cap.name])
                    error.set_visibility(True)
                    return
                self.errors.pop(cap.name, None)
                if outcome is not None:
                    # Like `InlineHost.on_applied`: an act returning None
                    # changed nothing. Every plot sends `{"autosize": true}`
                    # at mount, and a subscribing panel answering it with
                    # None must not close the dialog it sits in.
                    close()

            with slot:
                try:
                    cap.panel(view, ShellPanel(submit, figure))
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
    An unavailable capability keeps its place and says why (brief §3.3).
    `build()` rebuilds from the selection as it is now; `clear()` empties
    the column and drops this host's figure handlers. A raising act shows
    above that capability's panel and changes nothing; the next success
    hides it in place — the panel is not rebuilt, so a field being typed in
    survives. `on_applied` is called with that capability's name when its
    act returned an outcome, and only then: an act returning ``None``
    changed nothing, and the rebuild it would trigger discards a half-typed
    row for nothing. `follow()` is how a host that is never rebuilt keeps
    up with `available`: a job's lock comes and goes without the tree's
    shape moving."""

    def __init__(
        self,
        shell: Shell,
        placement: Placement,
        selection_of: Callable[[], Selection],
        *,
        figure: FigureHook | None,
        marker: str,
        on_applied: Callable[[str], None] | None = None,
    ) -> None:
        self.shell, self.placement, self.selection_of = shell, placement, selection_of
        self.figure, self.marker, self.on_applied = figure, marker, on_applied
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

    def clear(self) -> None:
        if self.figure is not None:
            for name in self.cells:
                self.figure.clear(self._owner(name))
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
                self._build_one(cap, avail, view)
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

    def _build_one(self, cap: Capability, avail: bool | str, view: Any) -> None:
        error = ui.label("").classes("text-negative text-xs").mark(f"error:{cap.name}")
        error.set_visibility(False)
        self.errors[cap.name] = error
        if avail is not True:
            sentence = avail if isinstance(avail, str) else "not available"
            ui.label(sentence).classes("dstf-why").mark(f"why:{cap.name}")
            return

        def submit(**inputs: Any) -> None:
            try:
                outcome = cap.act(view, **inputs)
                self.shell.session.apply(outcome)
            except Exception as e:  # shown above the panel; changes nothing
                error.set_text(f"{type(e).__name__}: {e}")
                error.set_visibility(True)
                return
            error.set_visibility(False)
            if outcome is not None and self.on_applied is not None:
                self.on_applied(cap.name)

        figure: FigureHookProtocol = _NoFigure()
        if self.figure is not None:
            figure = self.figure.owned(self._owner(cap.name))
        with ui.column().classes("w-full gap-2").mark(f"panel:{cap.name}"):
            try:
                cap.panel(view, ShellPanel(submit, figure))
            except Exception as e:  # shown in the slot, never breaks the page
                ui.label(f"{type(e).__name__}: {e}").classes("text-negative")
