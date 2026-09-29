"""The add form's variable picker (the plan's Task 3, Q8, K2): the drawers'
filter box over a scrolling list of rows in sections, filtered on the
SERVER (`picker_rows`, `needle.matcher`) — never a `ui.select` over every
name: TRANSFORM has a test of 76,992. One row is chosen (``value``, the
result-file name); ArrowUp / ArrowDown move the cursor from the filter
box, Enter chooses the cursor's row, a click chooses a row. Under the list
a `Chosen:` line names the choice (K7), so a filter that hides its row
still shows what Add will stage.

A `ui.column` with a ``value``, so a form reads it as it read the select
it replaced."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import regex
from nicegui import ui

from ..needle import matcher
from ..picker_rows import Picked, PickRow
from .drawer_controls import filter_box
from .widgets import MiddleCut

_SPACE = re.compile(r"\s+")

#: The filter box's keys the list answers, the page's default kept for
#: every other key (a caret move, a typed character). Each carries the
#: box's text AS TYPED: the filter reaches the server 250 ms after the last
#: keystroke (`debounce`), and ↓ Enter typed inside that moved the cursor
#: on the list before the filter and chose a row the list then hid.
_KEYS = """(e) => {
  if (['ArrowDown', 'ArrowUp', 'Enter'].includes(e.key)) {
    e.preventDefault();
    emit({key: e.key, needle: e.target.value || ''});
  }
}"""


def _mark(name: str) -> str:
    """A row's marker word: NiceGUI splits a marker on whitespace."""
    return _SPACE.sub("", name)


class VariablePicker(ui.column):
    def __init__(
        self,
        rows_of: Callable[[Callable[[str], bool]], Picked],
        *,
        value: str,
        text_of: Callable[[str], str],
        on_change: Callable[[str], None] | None = None,
        marker: str,
    ) -> None:
        """``rows_of(keep)`` is the rows the filter ``keep`` leaves
        (`picker_rows`); ``text_of(name)`` what a name reads as, for the
        Chosen line."""
        super().__init__()
        self.classes("dstf-picker w-full gap-0")
        self.mark(marker)
        self._rows_of, self._on_change, self._marker = rows_of, on_change, marker
        self._text_of = text_of
        self.value = value
        self.cursor = -1
        self.shown: list[PickRow] = []
        self._drawn: dict[str, ui.element] = {}
        with self:
            self.box = filter_box(
                "",
                False,
                False,
                self._filter,
                "search every variable",
                f"{marker}:pick-",
            )
            # The field's name, as the select it replaced was named.
            self.box.needle.props["label"] = "Variable"
            self.box.needle.on("keydown", self._key, js_handler=_KEYS)
            self.list = (
                ui.column()
                .classes("dstf-pick-list w-full gap-0")
                .mark(f"{marker}:pick-list")
            )
            # A combobox over a listbox (WAI-ARIA): the box keeps the focus
            # and names the cursor's row as its active descendant.
            self.list.props["role"] = "listbox"
            # Not "Variables": a label lookup of the box's "Variable" (the
            # tests', a screen reader's jump) would find both.
            self.list.props["aria-label"] = "Matching names"
            needle = self.box.needle.props
            needle["role"] = "combobox"
            needle["aria-controls"] = self.list.html_id
            needle["aria-expanded"] = "true"
            needle["aria-autocomplete"] = "list"
            self.cap = ui.label().classes("dstf-why").mark(f"{marker}:pick-cap")
            self.chosen = (
                ui.label()
                .classes("dstf-pick-chosen text-caption")
                .mark(f"{marker}:pick-chosen")
            )
        self._state: dict[str, Any] = {"needle": "", "regex": False, "case": False}
        self._say_chosen()
        self._draw()

    # -- the list ------------------------------------------------------------

    def _filter(self, needle: str, regex_on: bool, case: bool) -> None:
        """The box's filter. The same one again — the debounced change after
        a key already drew its needle (`_key`) — draws nothing: a redraw
        would take the cursor off the row just moved to."""
        state = {"needle": needle, "regex": regex_on, "case": case}
        if state == self._state:
            return
        self._state = state
        self._draw()

    def _draw(self) -> None:
        st = self._state
        self.list.clear()
        self._drawn, self.shown, self.cursor = {}, [], -1
        self._active(None)
        try:
            keep = matcher(st["needle"], regex=st["regex"], case=st["case"])
            picked = self._rows_of(keep)
        except regex.error as e:
            self.box.say(f"not a regular expression: {e}")
            self.cap.set_visibility(False)
            return
        except TimeoutError:
            self.box.say("the pattern took too long; simplify it")
            self.cap.set_visibility(False)
            return
        self.box.say(None)
        with self.list:
            for title, rows in picked.sections:
                ui.label(title).classes("dstf-group").mark(
                    f"{self._marker}:pick-section:{_mark(title)}"
                )
                for row in rows:
                    self._row(row)
            if not picked.sections:
                ui.label("no variable matches").classes("dstf-why")
        self.cap.set_text(picked.capped or "")
        self.cap.set_visibility(picked.capped is not None)
        if self.value in self._drawn:
            self.cursor = [r.name for r in self.shown].index(self.value)

    def _row(self, row: PickRow) -> None:
        self.shown.append(row)
        with (
            ui.row()
            .classes("dstf-pick-row items-baseline no-wrap gap-2 w-full")
            .mark(f"{self._marker}:pick:{_mark(row.name)}")
            .on("click", lambda _e, n=row.name: self.set_value(n))
        ) as element:
            text = MiddleCut(row.text).classes("dstf-var-text dstf-mono text-xs")
            text.props["title"] = row.name
            if row.text != row.name:
                ui.label(row.name).classes("dstf-varname dstf-var-text")
        element.props["role"] = "option"
        # Its accessible name is said, not read off the row: the middle
        # cut's two parts read as "…T_very_long_co mponent_name".
        element.props["aria-label"] = (
            row.text if row.text == row.name else f"{row.text} ({row.name})"
        )
        element.props["aria-selected"] = "true" if row.name == self.value else "false"
        if row.name == self.value:
            element.classes(add="dstf-pick-on")
        self._drawn[row.name] = element

    @property
    def options(self) -> dict[str, str]:
        """``{name: text}`` of the rows drawn now, in their order."""
        return {r.name: r.text for r in self.shown}

    # -- choosing ------------------------------------------------------------

    def _say_chosen(self) -> None:
        self.chosen.set_text(
            f"Chosen: {self._text_of(self.value)}" if self.value else "Chosen: none"
        )

    def set_value(self, name: str) -> None:
        """Choose ``name``: its row is highlighted, the Chosen line names
        it, ``on_change`` hears it."""
        old = self._drawn.get(self.value)
        if old is not None:
            old.classes(remove="dstf-pick-on")
            old.props["aria-selected"] = "false"
            old.update()
        self.value = name
        new = self._drawn.get(name)
        if new is not None:
            new.classes(add="dstf-pick-on")
            new.props["aria-selected"] = "true"
            new.update()
            self.cursor = [r.name for r in self.shown].index(name)
        self._say_chosen()
        if self._on_change is not None:
            self._on_change(name)

    def _move(self, to: int) -> None:
        if not self.shown:
            return
        old = self._cursor_element()
        if old is not None:
            old.classes(remove="dstf-pick-cursor")
        self.cursor = to % len(self.shown)
        new = self._cursor_element()
        self._active(new)
        if new is not None:
            new.classes(add="dstf-pick-cursor")
            self.client.run_javascript(
                f'document.getElementById("{new.html_id}")'
                '?.scrollIntoView({block: "nearest"})'
            )

    def _active(self, row: ui.element | None) -> None:
        """The box's ``aria-activedescendant``: the cursor's row, if any."""
        props = self.box.needle.props
        if row is None:
            if props.pop("aria-activedescendant", None) is not None:
                self.box.needle.update()
            return
        props["aria-activedescendant"] = row.html_id
        self.box.needle.update()

    def _cursor_element(self) -> ui.element | None:
        if 0 <= self.cursor < len(self.shown):
            return self._drawn.get(self.shown[self.cursor].name)
        return None

    def _key(self, e: Any) -> None:
        """A key on the list AS THE USER SEES IT: a needle typed since the
        last filter is drawn first (`_KEYS`)."""
        key, needle = e.args["key"], e.args["needle"]
        if needle != self._state["needle"]:
            self._state = {**self._state, "needle": needle}
            self._draw()
        if key == "ArrowDown":
            self._move(self.cursor + 1)
        elif key == "ArrowUp":
            self._move(self.cursor - 1 if self.cursor >= 0 else -1)
        elif key == "Enter" and 0 <= self.cursor < len(self.shown):
            self.set_value(self.shown[self.cursor].name)
