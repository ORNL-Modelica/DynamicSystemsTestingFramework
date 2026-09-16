"""`Action`: a button whose label is its caption and whose disabled state
carries the sentence that says why and what enables it, beside it
(DESIGN.md "Disabled states explain themselves": never a bare grey
button). The header's Save / Discard / Export and every capability's
button at a placement are Actions."""

from __future__ import annotations

from collections.abc import Callable

from nicegui import ui


class Action:
    def __init__(
        self,
        on_click: Callable[[], None],
        *,
        props: str,
        marker: str,
        why: bool = True,
    ) -> None:
        with ui.row().classes("items-center no-wrap gap-2") as self.container:
            self.button = ui.button(on_click=on_click).props(props).mark(marker)
            self.why: ui.label | None = (
                ui.label().classes("dstf-why").mark(f"{marker}:why") if why else None
            )

    def set(self, caption: str, reason: str | None) -> None:
        """``reason`` None enables the button; a sentence disables it and is
        shown beside it (when this Action carries a why-line)."""
        self.button.set_text(caption)
        if reason is None:
            self.button.enable()
        else:
            self.button.disable()
        if self.why is not None:
            self.why.set_text(reason or "")
            self.why.set_visibility(reason is not None)
