"""The Test page (brief §3.2): one test, its variables and its editing —
which arrive with sub-project 3. Until then a stub: the header, the way
back, the model and its verdict, and the TEST-placement buttons (Task 7).
One object per browser tab, like the Suite page."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nicegui import ui

from ...reporting.status import classify_with_class
from ..capabilities import Placement
from ..session import Selection
from .actions import ActionBar
from .header import Header
from .tabstate import tab_state

if TYPE_CHECKING:
    from .shell import Shell


class TestPage:
    def __init__(self, shell: Shell) -> None:
        self.shell = shell
        self.client: Any = None
        self.model_id = ""
        self.badge: ui.badge | None = None
        self.actions: ActionBar | None = None

    async def build(self, model_id: str) -> None:
        self.client = ui.context.client
        await self.client.connected()
        self.state = tab_state()  # the tab's, shared with its Suite page
        self.shell.page_setup()
        self.model_id = model_id
        ui.page_title(f"DSTF — {model_id.rsplit('.', 1)[-1]}")
        self.header = Header(self.shell)
        with ui.column().classes("dstf-test w-full q-pa-md gap-3"):
            ui.link("← Suite", "/").mark("back")
            project = self.shell.session.project
            test = project.test(model_id) if project is not None else None
            if test is None:
                ui.label(f"{model_id} is not a test of this project").classes(
                    "text-negative"
                )
                self.shell.register(self)
                return
            with ui.row().classes("items-center gap-3"):
                ui.label(model_id).classes("text-h6 dstf-mono").mark("model")
                self.badge = ui.badge("").classes("dstf-badge").mark("status")
            self.actions = ActionBar(
                self.shell,
                Placement.TEST,
                lambda: Selection(tests=frozenset({model_id})),
                marker="test-actions",
            )
            ui.label(
                "Plots, criteria and the Variables drawer arrive with the Test page "
                "(sub-project 3)."
            ).classes("text-secondary text-xs")
        self.shell.register(self)
        self._paint()

    def _paint(self) -> None:
        if self.badge is None:
            return
        comp = self.shell.session.comparisons.get(self.model_id)
        verdict, css = classify_with_class(comp) if comp is not None else ("", "queued")
        self.badge.set_text(verdict or "…")
        self.badge.style(f"background-color: var(--dstf-{css}-fill)")

    def handle(self, event: dict[str, Any]) -> None:
        self.header.handle(event)
        if self.actions is not None:
            self.actions.refresh()
        if event["event"] in ("results", "loading", "spec"):
            self._paint()
