"""The open criterion editor's ⌖ buttons and its one armed field (D149:
arm a field, then click the plot).

A field that names a place on the plot carries a ⌖ (`Panel.place_button`).
Pressed, it ARMS that field: the next plain click on the criterion's plot
is handed to the panel that drew it (`Panel.on_place`), which sets the
field from the click; a click that goes through disarms, a refused one
says why under the plot and leaves the field armed. One field is armed at
a time — one editor is open on a page, and this is its — and pressing ⌖
again, Esc, closing the editor or opening another criterion disarms.

The armed field is the SERVER's to know (it reads the click), so its
button's highlight is an attribute the server renders — ``aria-pressed``,
which the stylesheet reads — never a class the page sets.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable
from dataclasses import dataclass

from nicegui import ui

from .figure_hook import FigureHook

#: The hook kinds a panel's place handler and preview handler are
#: subscribed under (`ShellPanel.on_place` / `.on_preview`): so a rebuilt
#: panel's go with its other handlers (`FigureHook.clear(owner)`).
PLACE = "dstf_place"
PREVIEW = "dstf_preview"

#: A click with any of these held is not a plain click.
MODIFIERS = ("ctrl", "shift", "alt", "meta")


@dataclass(frozen=True)
class Armed:
    """The armed field: the capability whose panel drew it, the name that
    panel gave it (`Panel.place_button`'s ``pointer``) and its label."""

    owner: str
    pointer: Hashable
    label: str


class Placing:
    """One plot card's: the ⌖ buttons of the editor open under it, which
    of their fields is armed, and the hook its clicks arrive on."""

    def __init__(self, hook: FigureHook, on_change: Callable[[], None]) -> None:
        self.hook = hook
        #: Called when the armed field changes: the card says it in the
        #: editor's hint line.
        self._on_change = on_change
        self.armed: Armed | None = None
        self._buttons: dict[tuple[str, Hashable], ui.button] = {}

    def button(self, owner: str, pointer: Hashable, label: str) -> None:
        """Draw the ⌖ of the field ``owner``'s panel calls ``pointer``,
        into the current slot; its click never reaches the field it sits
        in. Out of the Tab order: it is the first half of a gesture whose
        second is a click on the plot, and in it Tab from a field met a ⌖
        before the next field — twice in every tolerance."""
        armed = Armed(owner, pointer, label)
        button = (
            ui.button(icon="my_location", color=None)
            .props('flat dense round size=xs tabindex="-1"')
            .classes("dstf-arm")
            .on(
                "click",
                lambda: self.toggle(armed),
                js_handler="(e) => { e.stopPropagation(); emit(); }",
            )
        )
        button.props["aria-label"] = f"Set {label} by a click on the plot"
        button.props["aria-pressed"] = "true" if self.armed == armed else "false"
        # The browser's own tooltip, not an element: a table of thirty
        # checkpoints draws 180 of these on every rebuild of the editor.
        button.props["title"] = "Set by a click on the plot"
        self._buttons[owner, pointer] = button

    def toggle(self, armed: Armed) -> None:
        self._set(None if self.armed == armed else armed)

    def disarm(self) -> None:
        if self.armed is not None:
            self._set(None)

    def settle(self) -> None:
        """After the editor's panels were rebuilt: the buttons that went
        with them are forgotten, and a field that is no longer drawn — its
        row removed, its mode switched away — is no longer armed."""
        self._buttons = {k: b for k, b in self._buttons.items() if not b.is_deleted}
        armed = self.armed
        if armed is not None and (armed.owner, armed.pointer) not in self._buttons:
            self._set(None)

    def forget(self) -> None:
        """The editor closed: nothing is armed and no button is its."""
        self._buttons = {}
        self.disarm()

    def _set(self, armed: Armed | None) -> None:
        self.armed = armed
        for (owner, pointer), button in self._buttons.items():
            if button.is_deleted:
                continue
            on = armed is not None and (armed.owner, armed.pointer) == (owner, pointer)
            pressed = "true" if on else "false"
            # Only when it changes: every write of a prop resends the button.
            if button.props.get("aria-pressed") != pressed:
                button.props["aria-pressed"] = pressed
                button.update()
        self._on_change()

    def clicked(self, click: dict) -> None:
        """A click on the plot, as the page reports it: every subscriber
        of the raw click hears it (Ctrl+click adds a row), and a PLAIN one
        is the armed field's."""
        self.hook.emit("dstf_click", click)
        if self.armed is not None and not any(click.get(key) for key in MODIFIERS):
            self.hook.emit(PLACE, click)
