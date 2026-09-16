"""The Suite page (brief §3.2 "two pages", §3.8; DESIGN.md "Tables"): what a
batch acts on. The header, the progress line, the test list with its
filters and checkboxes, the batch actions, the runs card. One object per
browser tab; its selection lives in the tab (`tabstate.py`), never in the
Session (D129)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nicegui import ui

from .header import Header
from .runs import RunsCard

if TYPE_CHECKING:
    from .shell import Shell


class SuitePage:
    def __init__(self, shell: Shell) -> None:
        self.shell = shell
        self.client: Any = None

    async def build(self) -> None:
        self.client = ui.context.client
        await self.client.connected()
        self.shell.page_setup()
        ui.page_title("DSTF")
        self.header = Header(self.shell)
        with ui.column().classes("dstf-suite w-full q-pa-md gap-3"):
            self.progress = ui.label().classes("dstf-progress").mark("progress")
            with ui.column().classes("gap-1") as self.hint:
                ui.label("No project is open. Start the tool on one:")
                ui.code("dstf author --config <path/to/testing.json>")
            self.body = ui.column().classes("w-full gap-3")  # the list (Task 6)
            self.runs = RunsCard(self.shell)
        self.shell.register(self)
        self.refresh()

    def refresh(self) -> None:
        load = self.shell.load
        text = load.text()
        self.progress.set_text(text)
        self.progress.set_visibility(bool(text))
        self.hint.set_visibility(
            self.shell.session.project is None and not load.in_progress
        )

    def handle(self, event: dict[str, Any]) -> None:
        self.header.handle(event)
        self.refresh()
        if event["event"] in ("job", "poll", "opened", "spec"):
            # `spec` too: an open's commit and a close reset `last_job`, the
            # notices and the artifacts, and the card must not keep showing
            # the previous project's.
            self.runs.refresh()
