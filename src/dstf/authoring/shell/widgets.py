"""`Action`: a button whose label is its caption and whose disabled state
carries the sentence that says why and what enables it, beside it
(DESIGN.md "Disabled states explain themselves": never a bare grey
button). The header's Save / Discard / Export and every capability's
button at a placement are Actions; `IconAction` is one drawn as an
icon, its caption or its reason the tooltip."""

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


class IconAction:
    """An Action drawn as an icon button: the caption — or, disabled, the
    sentence that says why — is its tooltip and its accessible name (an
    icon's ligature text is not a name). A disabled Quasar button takes no
    pointer events, so the tooltip sits on a span around it (as Remove
    plot's does)."""

    def __init__(self, on_click: Callable[[], None], *, icon: str, marker: str) -> None:
        with ui.element("span").classes("dstf-icon-action") as self.container:
            self.button = (
                ui.button(icon=icon, on_click=on_click)
                .props("outline dense")
                .mark(marker)
            )
            self.tip = ui.tooltip("").mark(f"{marker}:tip")

    def set(self, caption: str, reason: str | None) -> None:
        text = caption if reason is None else reason
        self.tip.set_text(text)
        self.button.props["aria-label"] = text
        self.button.update()
        if reason is None:
            self.button.enable()
        else:
            self.button.disable()


class MenuAction:
    """An Action drawn as an item of a bar's ⋮ menu: its caption the item's
    text (in a label: a menu item has no text of its own to set);
    disabled, the sentence that says why is its tooltip."""

    def __init__(self, on_click: Callable[[], None], *, marker: str) -> None:
        self.button = (
            ui.menu_item(on_click=on_click).props('role="menuitem"').mark(marker)
        )
        with self.button, ui.item_section():
            self.text = ui.label()
            self.tip = ui.tooltip("").mark(f"{marker}:tip")
        self.tip.set_visibility(False)

    def set(self, caption: str, reason: str | None) -> None:
        self.text.set_text(caption)
        self.tip.set_text(reason or "")
        self.tip.set_visibility(reason is not None)
        if reason is None:
            self.button.enable()
        else:
            self.button.disable()


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
