"""Capabilities at a placement (brief §3.2; DESIGN.md "Buttons", "Disabled
states explain themselves"): one outlined button per registered capability
whose `placements` names this one, labelled by the capability's `caption`,
disabled with the sentence `available` gave beside it; a click opens the
bar's dialog hosting the capability's `panel`, whose `submit` runs `act`
and hands the outcome to the Session. The bar knows no capability by name.
The dialog is not rebuilt while open (decision 4): a confirm list belongs
to the moment of the click; a successful act closes it, a raising one
shows the error in it and changes nothing."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING, Any

from nicegui import ui

from ..capabilities import Capability, Placement
from ..capabilities import FigureHook as FigureHookProtocol
from ..session import Selection
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
    ) -> None:
        self.shell, self.placement, self.selection_of = shell, placement, selection_of
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
            except Exception:  # the label is the fallback; the page draws
                caption = cap.label
            self.actions[cap.name].set(caption, reason)

    def open(self, cap: Capability) -> None:
        """The capability's panel, in the dialog, over the selection as it is
        at this click."""
        view = self.shell.view_for(self.selection_of())
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
                self.dialog.close()

            with slot:
                try:
                    cap.panel(view, ShellPanel(submit, _NoFigure()))
                except Exception as e:  # shown in the slot, never breaks the page
                    ui.label(f"{type(e).__name__}: {e}").classes("text-negative")
            ui.button("Close", on_click=self.dialog.close).props(
                "flat dense no-caps"
            ).mark(f"close:{cap.name}")
        self.dialog.open()
