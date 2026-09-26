"""The Test page's rail (the owner's ruling, 2026-09-25; DESIGN.md "The
Variables drawer"): 40 px of labelled buttons down the left edge, and ONE
drawer at a time beside them, 320 px, which pushes the plots narrower
rather than covering them (`behavior=desktop`: below Quasar's 1024 px
breakpoint a drawer otherwise turns into an overlay). Plotly follows the
narrower slot by itself — NiceGUI's plotly element observes its size. Which
drawer is open is the tab's (`TabState.drawer`), so a switch of test keeps
it open."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from nicegui import ui

from .test_list import TestList
from .variables_drawer import VariablesDrawer

if TYPE_CHECKING:
    from .test_page import TestPage

RAIL, PANEL = 40, 320


class TestsDrawer:
    """The Suite page's list, without checkboxes: grouped, short names, its
    own filter, this test highlighted (the owner's ruling, Q9). A click
    opens that test in this tab; pending edits are the Session's and
    survive the switch."""

    def __init__(self, page: TestPage, panel: ui.column) -> None:
        self.page = page
        with panel:
            ui.label("Tests").classes("text-subtitle2")
            ui.input(
                "filter", value=page.state.tests_needle, on_change=self._on_needle
            ).props("dense outlined clearable debounce=250").classes("w-full").mark(
                "tests-drawer:needle"
            )
            self.list = TestList(
                page.shell,
                on_open=self._open,
                current=page.model_id,
                marker="tests-drawer:",
            )
        self._rebuild()

    def _rebuild(self) -> None:
        """The rows from scratch, and the tab's filter APPLIED to them: a
        drawer built on the next test's page showed the filter in its box
        over every row until some event happened to apply it."""
        self.list.rebuild(self.page.shell.visible_tests(), grouped=True, short=True)
        self._filter()

    def _open(self, model_id: str) -> None:
        ui.navigate.to(f"/test/{quote(model_id, safe='')}")

    def _on_needle(self, e: Any) -> None:
        self.page.state.tests_needle = e.value or ""
        self._filter()

    def _filter(self) -> None:
        self.list.filter(self.page.state.tests_needle, set())

    def handle(self, event: dict[str, Any]) -> None:
        """The Suite page's rules for its rows (`SuitePage.handle`): a `spec`
        that moved the test set rebuilds them, a scored model repaints its
        own row."""
        kind = event["event"]
        if kind == "spec":
            if set(self.list.rows) != set(self.page.shell.visible_tests()):
                self._rebuild()
        elif kind == "loading":
            if event.get("phase") == "results":
                if not self.list.rows:
                    self._rebuild()  # a drawer opened before the spec event
                self.list.paint(str(event["model"]))
        elif kind == "results":
            for model_id in event.get("models", ()):
                self.list.paint(str(model_id))
        self._filter()


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
