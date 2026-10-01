"""What both drawers of the Test page draw (the owner's ruling Q7: one
control set on every list): the filter box with its `.*` and `Aa` toggles
beside it and the sentence under it, and an icon button opening a menu of
choices (`role=menuitemradio`, the current one checked). The drawers own
their state (`TabState`); these draw it and report a change."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any

from nicegui import ui

#: A menu's keyboard (Quasar's QMenu has none): ArrowDown / ArrowUp move
#: the focus to the next / previous choice, wrapping; Home / End to the
#: first / last. From outside the choices ArrowDown goes to the first and
#: ArrowUp to the last. Tab closes it; the focus returns to its button (the
#: menu pattern: its choices are out of the tab order, `tabindex="-1"`).
#: The choices are the menu's radio items, or its plain items that are not
#: disabled (the tree card's Move to…: a refused place is no choice).
#: ``__MENU_ID__`` is the menu's element id (`menu_keys`).
_MENU_KEYS = """(e) => {
  if (e.key === 'Tab') { getElement(__MENU_ID__)?.hide(); return; }
  const keys = ['ArrowDown', 'ArrowUp', 'Home', 'End'];
  if (!keys.includes(e.key)) return;
  const items = [...e.currentTarget.querySelectorAll(
    '[role="menuitemradio"], [role="menuitem"]:not([aria-disabled="true"])')];
  if (!items.length) return;
  e.preventDefault();
  const at = items.indexOf(document.activeElement);
  const last = items.length - 1;
  let to;
  if (e.key === 'Home') to = 0;
  else if (e.key === 'End') to = last;
  else if (at < 0) to = e.key === 'ArrowDown' ? 0 : last;
  else if (e.key === 'ArrowDown') to = at === last ? 0 : at + 1;
  else to = at === 0 ? last : at - 1;
  items[to].focus();
}"""


#: A view control's tooltip, above it: below, it covers what opens there.
TIP_ABOVE = 'anchor="top middle" self="bottom middle"'


def menu_keys(menu: ui.menu) -> str:
    """`_MENU_KEYS` for ``menu``."""
    return _MENU_KEYS.replace("__MENU_ID__", str(menu.id))


def _pressed(button: ui.button, on: bool) -> None:
    button.props(f'aria-pressed="{str(on).lower()}"')


@dataclass
class FilterBox:
    needle: ui.input
    regex: ui.button
    case: ui.button
    why: ui.label

    def say(self, text: str | None) -> None:
        """The sentence under the box (a bad pattern, a timeout), or none."""
        self.why.set_text(text or "")
        self.why.set_visibility(bool(text))


def filter_box(
    needle: str,
    regex: bool,
    case: bool,
    on_change: Callable[[str, bool, bool], None],
    placeholder: str,
    marker: str,
) -> FilterBox:
    """The input, with `.*` "Regular expression" and `Aa` "Match case" as
    toggles (`aria-pressed`) beside it, then the sentence line, hidden.
    Markers: ``<marker>needle``, ``<marker>regex``, ``<marker>case``,
    ``<marker>filter:why``. ``on_change(needle, regex, case)`` on any of
    them."""
    state: dict[str, Any] = {"needle": needle, "regex": regex, "case": case}

    def changed() -> None:
        on_change(state["needle"], state["regex"], state["case"])

    def typed(e: Any) -> None:
        state["needle"] = e.value or ""
        changed()

    def flip(key: str, button: ui.button) -> None:
        state[key] = not state[key]
        _pressed(button, state[key])
        changed()

    buttons: dict[str, ui.button] = {}
    with ui.row().classes("items-center no-wrap gap-1 w-full"):
        box = (
            ui.input(placeholder=placeholder, value=needle, on_change=typed)
            .props("dense outlined clearable debounce=250")
            .classes("col")
            .mark(f"{marker}needle")
        )
        for key, text, tip in (
            ("regex", ".*", "Regular expression"),
            ("case", "Aa", "Match case"),
        ):
            button = (
                ui.button(text)
                .props("flat dense no-caps")
                .classes("dstf-mono")
                .mark(f"{marker}{key}")
            )
            button.on_click(partial(flip, key, button))
            _pressed(button, state[key])
            with button:
                ui.tooltip(tip)
            buttons[key] = button
    why = ui.label().classes("dstf-why").mark(f"{marker}filter:why")
    why.set_visibility(False)
    return FilterBox(box, buttons["regex"], buttons["case"], why)


@dataclass
class MenuButton:
    button: ui.button
    items: dict[str, ui.menu_item]
    value: str

    def set(self, value: str) -> None:
        """Check ``value``'s item and no other."""
        self.value = value
        for key, item in self.items.items():
            on = key == value
            item.props(f'aria-checked="{str(on).lower()}"')
            if on:
                item.classes(add="dstf-menu-checked")
            else:
                item.classes(remove="dstf-menu-checked")


def menu_button(
    icon: str,
    tip: str,
    options: Sequence[tuple[str, str]],
    value: str,
    on_change: Callable[[str], None],
    marker: str,
) -> MenuButton:
    """An icon button, named and tooltipped ``tip``, opening a menu of
    ``options`` (``(value, label)``), each a `menuitemradio` with the
    current one checked, and the arrow keys moving among them. Markers:
    ``<marker>`` on the button, ``<marker>:<value>`` on each item."""
    button = ui.button(icon=icon).props("flat dense round").mark(marker)
    # On the props dict: its name is the tip (the icon's ligature names
    # nothing), and a props string would break on a quote.
    button.props["aria-label"] = tip
    button.props["aria-haspopup"] = "menu"
    items: dict[str, ui.menu_item] = {}

    def choose(key: str) -> None:
        menu.set(key)
        on_change(key)

    with button:
        # Above the button: below it (Quasar's default) the tooltip covered
        # the open menu's first choice and its check.
        ui.tooltip(tip).props(TIP_ABOVE)
        with ui.menu().props('role="menu"') as element:
            element.on("keydown", js_handler=menu_keys(element))
            for key, label in options:
                item = (
                    ui.menu_item(on_click=partial(choose, key))
                    .props('role="menuitemradio" tabindex="-1"')
                    .mark(f"{marker}:{key}")
                )
                # `ui.menu_item` has no `set_text`: the text is a label in
                # an item section, the check a hidden icon beside it.
                with item:
                    with ui.item_section().props("side"):
                        ui.icon("check").classes("dstf-menu-check").props(
                            'aria-hidden="true"'
                        )
                    with ui.item_section():
                        ui.item_label(label)
                items[key] = item
    menu = MenuButton(button, items, value)
    menu.set(value)
    return menu
