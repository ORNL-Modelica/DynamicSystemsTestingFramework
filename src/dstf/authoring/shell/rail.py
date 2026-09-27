"""The Test page's rail (the owner's ruling, 2026-09-25; DESIGN.md "The
Variables drawer"): 40 px of labelled buttons down the left edge, and ONE
drawer at a time beside them, 320 px, which pushes the plots narrower
rather than covering them (`behavior=desktop`: below Quasar's 1024 px
breakpoint a drawer otherwise turns into an overlay). Plotly follows the
narrower slot by itself — NiceGUI's plotly element observes its size. Which
drawer is open is the tab's (`TabState.drawer`), so a switch of test keeps
it open."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import regex
from nicegui import ui

from ...reporting.status import (
    FAIL,
    NO_REF,
    PASS,
    SIM_FAIL,
    STATUS_CLASSES,
    classify,
)
from ..needle import matcher
from .drawer_controls import TIP_ABOVE, filter_box, menu_button
from .test_list import Row, TestList, _name, _package
from .variables_drawer import VariablesDrawer

if TYPE_CHECKING:
    from .test_page import TestPage

RAIL, PANEL = 40, 320

EDITED = "EDITED"
#: What `TestList.paint` calls a model the open could not score.
SCORING_FAILED = "scoring failed"
#: The Tests drawer's status pills, in the Suite's order, and EDITED.
PILLS = (PASS, FAIL, SIM_FAIL, NO_REF, EDITED)
#: Each pill's `.dstf-pill-<status>` tokens; EDITED reads as the Suite's
#: Edits cell and the rows' ``N unsaved`` pill do.
PILL_CSS = {**{s: STATUS_CLASSES[s] for s in PILLS if s != EDITED}, EDITED: "warn"}
#: "Status, failures first": FAIL, SIM_FAIL, scoring failed, NO_REF, PASS,
#: then no verdict yet — NOT `STATUS_ORDER`, which lists PASS first.
FAILURES_FIRST = (FAIL, SIM_FAIL, SCORING_FAILED, NO_REF, PASS, "")
SORTS = (
    ("package", "Package order"),
    ("name", "Name A→Z"),
    ("status", "Status, failures first"),
)
NAMES = (("short", "Short"), ("full", "Full"))


class TestsDrawer:
    """The Suite page's list, without checkboxes, and the drawers' one
    control set (the owner's ruling Q7): the filter with `.*` / `Aa`, sort,
    group and names, and compact status pills with EDITED — every control
    the tab's (`TabState`), so a switch of test keeps them. This test is
    highlighted; a click opens that test in this tab, and pending edits are
    the Session's and survive the switch."""

    def __init__(self, page: TestPage, panel: ui.column) -> None:
        self.page = page
        state = page.state
        #: The filter's kept ids and its sentence, and what they were
        #: computed from (`_kept`).
        self.kept: tuple[object, set[str], str | None] | None = None
        with panel:
            ui.label("Tests").classes("text-subtitle2")
            self.box = filter_box(
                state.tests_needle,
                state.tests_regex,
                state.tests_case,
                self._on_filter,
                "filter tests",
                "tests-drawer:",
            )
            with ui.row().classes("items-center no-wrap gap-0 w-full"):
                self.sort = menu_button(
                    "sort",
                    "Sort",
                    SORTS,
                    state.tests_sort,
                    self._on_sort,
                    "tests-drawer:sort",
                )
                self.group = (
                    ui.button(icon="account_tree", on_click=self._on_group)
                    .props("flat dense round")
                    .mark("tests-drawer:group")
                )
                self.group.props["aria-label"] = "Group by package"
                with self.group:
                    self.group_tip = ui.tooltip("").props(TIP_ABOVE)
                self.names = menu_button(
                    "label",
                    "Names",
                    NAMES,
                    "short" if state.tests_short else "full",
                    self._on_names,
                    "tests-drawer:names",
                )
            with ui.row().classes("items-center gap-1 w-full"):
                self.pills: dict[str, ui.button] = {}
                for word in PILLS:
                    self.pills[word] = (
                        ui.button(on_click=partial(self._on_pill, word))
                        .props("flat dense no-caps")
                        .classes(
                            f"dstf-count-pill dstf-pill dstf-pill-{PILL_CSS[word]}"
                        )
                        .mark(f"tests-drawer:pill:{word}")
                    )
            self.list = TestList(
                page.shell,
                on_open=self._open,
                current=page.model_id,
                marker="tests-drawer:",
            )
        self._paint_group()
        self._rebuild()

    # -- the controls ------------------------------------------------------------

    def _on_filter(self, needle: str, regex: bool, case: bool) -> None:
        state = self.page.state
        state.tests_needle, state.tests_regex, state.tests_case = needle, regex, case
        self._filter()

    def _on_sort(self, value: str) -> None:
        self.page.state.tests_sort = value
        self._rebuild()

    def _on_group(self) -> None:
        state = self.page.state
        state.tests_grouped = not state.tests_grouped
        self._paint_group()
        self._rebuild()

    def _paint_group(self) -> None:
        """Pressed while grouped; the tooltip says what a click does."""
        grouped = self.page.state.tests_grouped
        self.group.props(f'aria-pressed="{str(grouped).lower()}"')
        self.group_tip.set_text("Show flat" if grouped else "Group by package")

    def _on_names(self, value: str) -> None:
        self.page.state.tests_short = value == "short"
        self._rebuild()

    def _on_pill(self, word: str) -> None:
        self.page.state.tests_pills.symmetric_difference_update({word})
        self._filter()

    # -- the rows ----------------------------------------------------------------

    def _ids(self) -> list[str]:
        """The tests in the chosen order; package order is the shell's."""
        ids = self.page.shell.visible_tests()
        state = self.page.state
        if state.tests_sort == "name":
            return sorted(
                ids, key=lambda m: (_name(m, state.tests_short).casefold(), m)
            )
        if state.tests_sort == "status":
            position = {m: i for i, m in enumerate(ids)}
            return sorted(ids, key=lambda m: (_rank(self._verdict(m)), position[m]))
        return ids

    def _verdict(self, model_id: str) -> str:
        """What the row's badge says (`TestList.paint`), read before the
        rows exist: the order is decided first."""
        comp = self.page.shell.session.comparisons.get(model_id)
        if comp is not None:
            return classify(comp)
        return SCORING_FAILED if model_id in self.page.shell.load.failed_models else ""

    def _drawn(self) -> list[str]:
        """The order the rows are drawn in now: `_ids`, by package first
        when grouped (`TestList.rebuild`)."""
        ids = self._ids()
        if not self.page.state.tests_grouped:
            return ids
        position = {m: i for i, m in enumerate(ids)}
        return sorted(ids, key=lambda m: (_package(m), position[m]))

    def _rebuild(self) -> None:
        """The rows from scratch in the chosen order and grouping, and the
        tab's filter APPLIED to them: a drawer built on the next test's page
        showed the filter in its box over every row until some event
        happened to apply it."""
        state = self.page.state
        self.list.rebuild(
            self._ids(), grouped=state.tests_grouped, short=state.tests_short
        )
        self._filter()

    def _open(self, model_id: str) -> None:
        ui.navigate.to(f"/test/{quote(model_id, safe='')}")

    def _kept(self) -> tuple[set[str], str | None]:
        """The ids the filter keeps, and the sentence under the box — every
        id, and why, for a bad pattern or one that ran out of time: the
        drawer hides nothing then. Cached on the needle, its toggles and
        the ids: events repaint the rows every second during a job, and a
        pathological pattern cost 0.79 s of CPU per pass over 331 ids (the
        plan's executing review)."""
        state = self.page.state
        ids = tuple(self.list.rows)
        key = (state.tests_needle, state.tests_regex, state.tests_case, ids)
        if self.kept is None or self.kept[0] != key:
            why: str | None = None
            try:
                match = matcher(
                    state.tests_needle, regex=state.tests_regex, case=state.tests_case
                )
                kept = {m for m in ids if match(m)}
            except regex.error as e:
                kept, why = set(ids), f"not a regular expression: {e}"
            except TimeoutError:
                kept, why = set(ids), "the pattern took too long; simplify it"
            self.kept = (key, kept, why)
        return self.kept[1], self.kept[2]

    def _filter(self) -> None:
        """The filter and the pills over the rows; the pills count every
        row, whatever the filter. The statuses pressed are OR'd, and EDITED
        with them — the Suite's rule (`suite_grid.js`)."""
        state = self.page.state
        counts = dict.fromkeys(PILLS, 0)
        for row in self.list.rows.values():
            if row.verdict in counts:
                counts[row.verdict] += 1
            if row.pending:
                counts[EDITED] += 1
        for word, button in self.pills.items():
            on = word in state.tests_pills
            button.set_text(f"{word} {counts[word]}")
            button.props(f'aria-pressed="{str(on).lower()}"')
            if on:
                button.classes(add="dstf-count-pill-on")
            else:
                button.classes(remove="dstf-count-pill-on")
            # One switched on stays enabled at 0, so it can be switched off.
            button.set_enabled(counts[word] > 0 or on)
        kept, why = self._kept()
        self.box.say(why)
        pills = state.tests_pills

        def keep(row: Row) -> bool:
            if row.model_id not in kept:
                return False
            if not pills:
                return True
            return row.verdict in pills or (EDITED in pills and row.pending > 0)

        self.list.filter(keep)

    def handle(self, event: dict[str, Any]) -> None:
        """The Suite page's rules for its rows (`SuitePage.handle`): a `spec`
        that moved the test set rebuilds them, and one that did not repaints
        the rows whose edits it moved; a scored model repaints its own row.
        Under the status sort, a verdict that moved the order rebuilds
        them."""
        kind = event["event"]
        if kind == "spec":
            if set(self.list.rows) != set(self.page.shell.visible_tests()):
                self._rebuild()
                return
            for model_id in event.get("models") or list(self.list.rows):
                self.list.paint(str(model_id))
        elif kind == "loading":
            if event.get("phase") == "results":
                if not self.list.rows:
                    self._rebuild()  # a drawer opened before the spec event
                    return
                self.list.paint(str(event["model"]))
        elif kind == "results":
            for model_id in event.get("models", ()):
                self.list.paint(str(model_id))
        if self.page.state.tests_sort == "status" and list(self.list.rows) != (
            self._drawn()
        ):
            self._rebuild()
            return
        self._filter()


def _rank(verdict: str) -> int:
    """Where "Status, failures first" puts a verdict: a word it does not
    list goes last."""
    return (
        FAILURES_FIRST.index(verdict)
        if verdict in FAILURES_FIRST
        else len(FAILURES_FIRST)
    )


class Rail:
    """The rail and its drawers' panels. A drawer's content is built the
    first time it opens."""

    def __init__(self, page: TestPage) -> None:
        self.page = page
        self.tests: TestsDrawer | None = None
        self.variables: VariablesDrawer | None = None
        self.buttons: dict[str, ui.button] = {}
        self.panels: dict[str, ui.column] = {}
        self.drawer = (
            ui.left_drawer(value=True)
            .props(f"width={RAIL} behavior=desktop bordered")
            .classes("dstf-rail-drawer")
        )
        with self.drawer, ui.row().classes("no-wrap gap-0 items-stretch w-full h-full"):
            with ui.column().classes("dstf-rail gap-1 items-center"):
                for kind, icon, text, tip in (
                    ("tests", "format_list_bulleted", "Tests", None),
                    ("variables", "data_array", "Vars", "Variables"),
                ):
                    with (
                        ui.button(on_click=lambda k=kind: self.toggle(k))
                        .props("flat dense no-caps stack")
                        .mark(f"rail:{kind}") as button
                    ):
                        ui.icon(icon)
                        ui.label(text).classes("dstf-rail-label")
                        if tip is not None:  # "Vars" is short for it
                            ui.tooltip(tip)
                    self.buttons[kind] = button
            for kind in ("tests", "variables"):
                panel = (
                    ui.column()
                    .classes("dstf-drawer-panel q-pa-sm gap-2")
                    .mark(f"drawer:{kind}")
                )
                panel.set_visibility(False)
                self.panels[kind] = panel

    def open(self, kind: str | None) -> None:
        """Show ``kind``'s panel and hide the other; None closes both. The
        drawer widens to hold the panel, which is what narrows the plots.
        The Variables drawer is built again only when it was not the one
        open: an open one follows the page by `refresh`, and Add series
        (a select, then this) built it twice."""
        was = self.page.state.drawer
        for name, panel in self.panels.items():
            panel.set_visibility(name == kind)
        for name, button in self.buttons.items():
            button.classes(remove="dstf-rail-on")
            if name == kind:
                button.classes(add="dstf-rail-on")
        self.drawer.props(f"width={RAIL + PANEL if kind else RAIL}")
        self.page.state.drawer = kind
        if kind == "tests" and self.tests is None:
            self.tests = TestsDrawer(self.page, self.panels["tests"])
        elif kind == "variables":
            if self.variables is None:
                self.variables = VariablesDrawer(self.page, self.panels["variables"])
            elif was != "variables":
                self.variables.rebuild()  # it was not followed while closed

    def toggle(self, kind: str) -> None:
        self.open(None if self.page.state.drawer == kind else kind)

    def refresh(self) -> None:
        """The open Variables drawer follows the page — the plot selected,
        the layout staged, the result read — and builds again only when one
        of those moved (`VariablesDrawer.refresh`). The ONE way the page's
        changes reach it: `TestPage.select` and the end of `TestPage.sync`
        call this. A closed one is left alone; it is rebuilt when it opens."""
        if self.page.state.drawer == "variables" and self.variables is not None:
            self.variables.refresh()

    def handle(self, event: dict[str, Any]) -> None:
        if self.tests is not None:
            self.tests.handle(event)
