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


class Pill:
    """The tinted status pill of a criterion or a tree node (DESIGN.md
    "Status badges": tinted inside a page, filled only in the header). It
    repaints in place — a verdict that flips must not cost the row it sits
    in."""

    def __init__(self, status: str, marker: str) -> None:
        self.label = ui.label().classes("dstf-pill").mark(marker)
        self.status = ""
        self.set(status)

    def set(self, status: str, text: str | None = None) -> None:
        if self.status:
            self.label.classes(remove=f"dstf-pill-{self.status}")
        self.status = status
        self.label.classes(add=f"dstf-pill-{status}")
        self.label.set_text(text if text is not None else status)
