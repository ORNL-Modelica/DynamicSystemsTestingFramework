"""The Suite page (brief §3.2 "two pages", §3.8; DESIGN.md "Tables"): what a
batch acts on. The header, the progress line, the test list with its
filters and checkboxes, the batch actions, the runs card. One object per
browser tab; its selection lives in the tab (`tabstate.py`), never in the
Session (D129)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from nicegui import ui

from ...reporting.status import FAIL, NO_REF, PASS, SIM_FAIL, count_by_status
from ..capabilities import Placement, plural
from ..session import Selection
from .actions import ActionBar
from .header import Header
from .runs import RunsCard
from .tabstate import TabState, tab_state
from .test_list import Row, TestList
from .widgets import Action

if TYPE_CHECKING:
    from .shell import Shell


CHIP_ORDER = (PASS, FAIL, SIM_FAIL, NO_REF)


class SuitePage:
    def __init__(self, shell: Shell) -> None:
        self.shell = shell
        self.client: Any = None

    async def build(self) -> None:
        self.client = ui.context.client
        await self.client.connected()
        self.state: TabState = tab_state()
        self.shell.page_setup()
        ui.page_title("DSTF")
        self.header = Header(self.shell)
        self.chips: dict[str, ui.button] = {}
        with ui.column().classes("dstf-suite w-full q-pa-md gap-3"):
            self.progress = ui.label().classes("dstf-progress").mark("progress")
            with ui.column().classes("gap-1") as self.hint:
                ui.label("No project is open. Start the tool on one:")
                ui.code("dstf author --config <path/to/testing.json>")
            with ui.row().classes("items-center no-wrap gap-3 w-full"):
                self.needle = (
                    ui.input(
                        "filter", value=self.state.needle, on_change=self._on_needle
                    )
                    .props("dense outlined clearable")
                    .mark("needle")
                )
                self.grouping = (
                    ui.toggle(
                        {True: "grouped", False: "flat"},
                        value=self.state.grouped,
                        on_change=self._on_layout,
                    )
                    .props("dense no-caps")
                    .mark("grouping")
                )
                self.naming = (
                    ui.toggle(
                        {True: "short names", False: "full names"},
                        value=self.state.short,
                        on_change=self._on_layout,
                    )
                    .props("dense no-caps")
                    .mark("naming")
                )
                with ui.row().classes("items-center gap-1").mark("counts"):
                    for status in CHIP_ORDER:
                        self.chips[status] = (
                            ui.button(on_click=lambda s=status: self._toggle_chip(s))
                            .props("flat dense no-caps")
                            .classes("dstf-chip")
                            .mark(f"chip:{status}")
                        )
                ui.space()
                self.check_shown = Action(
                    self._check_shown,
                    props="flat dense no-caps",
                    marker="check-shown",
                    why=False,
                )
                self.uncheck = Action(
                    self._uncheck, props="flat dense no-caps", marker="uncheck"
                )
            self.actions = ActionBar(
                self.shell,
                Placement.SUITE,
                lambda: Selection(tests=frozenset(self.state.checked)),
                marker="suite-actions",
            )
            self.list = TestList(
                self.shell,
                on_open=self._open,
                checked=self.state.checked,
                on_check=self._toggle,
            )
            self.runs = RunsCard(self.shell)
        self.shell.register(self)
        self.rebuild()

    # -- the rows ---------------------------------------------------------------

    @property
    def rows(self) -> dict[str, Row]:
        return self.list.rows

    def rebuild(self) -> None:
        """The rows from scratch: on the first build, on a changed test set
        (a `spec` event), on a layout toggle. A check, a click, a filter or a
        rescore never comes here — each updates one element."""
        self.list.rebuild(
            self.shell.visible_tests(),
            grouped=self.state.grouped,
            short=self.state.short,
        )
        self._apply_filter()
        self.refresh()

    def _paint(self, model_id: str) -> None:
        self.list.paint(model_id)

    def _apply_filter(self) -> None:
        self.list.filter(self.state.needle, self.state.status_filter)

    def _shown(self) -> list[str]:
        return self.list.shown()

    def refresh(self) -> None:
        load = self.shell.load
        text = load.text()
        self.progress.set_text(text)
        self.progress.set_visibility(bool(text))
        self.hint.set_visibility(
            self.shell.session.project is None and not load.in_progress
        )
        # A copy: the open thread inserts into `comparisons` while this loop
        # runs on the UI loop, and iterating a dict another thread grows
        # raises. `dict.copy` is one C call under the GIL.
        comparisons = self.shell.session.comparisons.copy()
        counts = count_by_status([c for m, c in comparisons.items() if m in self.rows])
        for status, chip in self.chips.items():
            chip.set_text(f"{status} {counts[status]}")
            chip.classes(
                replace="dstf-chip"
                + (" dstf-chip-active" if status in self.state.status_filter else "")
            )
        shown = self._shown()
        self.check_shown.set(
            f"Check {plural(len(shown), 'shown test')}",
            None if shown else "nothing is shown",
        )
        checked = len(self.state.checked & set(self.rows))
        self.uncheck.set(
            f"Uncheck {plural(checked, 'test')}",
            None if checked else "nothing is checked",
        )

    # -- the user's gestures ------------------------------------------------------

    def _toggle(self, model_id: str, on: bool) -> None:
        (self.state.checked.add if on else self.state.checked.discard)(model_id)
        self.refresh()
        self.actions.refresh()

    def _open(self, model_id: str) -> None:
        """A click views (brief §3.2): the Test page, in this tab."""
        ui.navigate.to(f"/test/{quote(model_id, safe='')}")

    def _on_needle(self, e: Any) -> None:
        self.state.needle = e.value or ""
        self._apply_filter()
        self.refresh()

    def _toggle_chip(self, status: str) -> None:
        wanted = self.state.status_filter
        (wanted.discard if status in wanted else wanted.add)(status)
        self._apply_filter()
        self.refresh()

    def _on_layout(self, e: Any) -> None:
        self.state.grouped = bool(self.grouping.value)
        self.state.short = bool(self.naming.value)
        self.rebuild()

    def _check_shown(self) -> None:
        for model_id in self._shown():
            self.state.checked.add(model_id)
            box = self.rows[model_id].checkbox
            if box is not None:
                box.set_value(True)
        self.refresh()
        self.actions.refresh()

    def _uncheck(self) -> None:
        for model_id in list(self.state.checked):
            self.state.checked.discard(model_id)
            row = self.rows.get(model_id)
            if row is not None and row.checkbox is not None:
                row.checkbox.set_value(False)
        self.refresh()
        self.actions.refresh()

    # -- the Session's events ----------------------------------------------------

    def handle(self, event: dict[str, Any]) -> None:
        """Every Session event, from the shell's tick. No early return: the
        card and the rows are independent regions, and a `spec` that rebuilds
        the rows (a close, an `OpenProject`, the rescan adding a model) is
        exactly the event after which the card must not show the previous
        project's outcome."""
        self.header.handle(event)
        kind = event["event"]
        if kind in ("job", "poll", "opened", "spec"):
            self.runs.refresh()  # `spec`: an open or a close resets the card's sources
        if kind in ("results", "job", "spec", "opened") or (
            kind == "loading" and event.get("phase") == "done"
        ):
            self.actions.refresh()
        if kind == "spec":
            if set(self.rows) != set(self.shell.visible_tests()):
                self.rebuild()  # ends with `_apply_filter` and `refresh`; repeating them below is cheap
        elif kind == "loading":
            if event.get("phase") == "results":
                if not self.rows:
                    self.rebuild()  # a page opened before the spec event
                self._paint(str(event["model"]))
        elif kind == "results":
            for model_id in event.get("models", ()):
                self._paint(str(model_id))
        self._apply_filter()
        self.refresh()
