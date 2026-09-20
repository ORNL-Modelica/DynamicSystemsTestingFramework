"""Capabilities at a placement (brief §3.2; DESIGN.md "Buttons", "Disabled
states explain themselves"): one outlined button per registered capability
whose `placements` names this one, labelled by the capability's `caption`,
disabled with the sentence `available` gave beside it; a click opens the
bar's dialog hosting the capability's `panel`, whose `submit` runs `act`
and hands the outcome to the Session. The bar knows no capability by name.
The dialog is not rebuilt while open (decision 4): a confirm list belongs
to the moment of the click; a successful act closes it, a raising one
shows the error in it and changes nothing.

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
        self.dialog = ui.dialog()  # one element, rebuilt per open
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

    def open(self, cap: Capability) -> None:
        """The capability's panel, in the dialog, over the selection as it is
        at this click."""
        view = self.shell.view_for(self.selection_of())
        owner = f"{self.marker}:{cap.name}"
        figure: FigureHookProtocol = _NoFigure()
        if self.figure is not None:
            self.figure.clear(owner)  # a second open never doubles a delivery
            figure = self.figure.owned(owner)

        def close() -> None:
            if self.figure is not None:
                self.figure.clear(owner)
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
                    self.shell.session.apply(cap.act(view, **inputs))
                except Exception as e:  # shown here; a raising act changes nothing
                    self.errors[cap.name] = f"{type(e).__name__}: {e}"
                    error.set_text(self.errors[cap.name])
                    error.set_visibility(True)
                    return
                self.errors.pop(cap.name, None)
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
    survives."""

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
        self.errors, self.cells = {}, {}

    def build(self, skip: str | None = None) -> None:
        """Rebuild every capability's place — but ``skip``'s, left untouched
        (the panel whose own submit this follows: rebuilding it would destroy
        the control being typed in). A changed set of capabilities rebuilds
        them all."""
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
                self.shell.session.apply(cap.act(view, **inputs))
            except Exception as e:  # shown above the panel; changes nothing
                error.set_text(f"{type(e).__name__}: {e}")
                error.set_visibility(True)
                return
            error.set_visibility(False)
            if self.on_applied is not None:
                self.on_applied(cap.name)

        figure: FigureHookProtocol = _NoFigure()
        if self.figure is not None:
            figure = self.figure.owned(self._owner(cap.name))
        with ui.column().classes("w-full gap-2").mark(f"panel:{cap.name}"):
            try:
                cap.panel(view, ShellPanel(submit, figure))
            except Exception as e:  # shown in the slot, never breaks the page
                ui.label(f"{type(e).__name__}: {e}").classes("text-negative")
