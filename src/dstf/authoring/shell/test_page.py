"""The Test page (brief §3.2; roadmap §3): one test — its own header as a
second row of the shared one, and its criteria tree closed to a line. The
plots, their criteria rows and the editor arrive with the tasks after this
one. One object per browser tab, like the Suite page. The page knows no
capability by name: what edits a tree node is whatever attaches at that
placement."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nicegui import ui

from ...reporting.status import classify_with_class
from ...storage.ref_index import RefIndex
from ..capabilities import Placement
from ..session import Selection
from .actions import ActionBar
from .header import Header
from .tabstate import tab_state
from .tree_card import TreeCard

if TYPE_CHECKING:
    from .shell import Shell


class TestPage:
    def __init__(self, shell: Shell) -> None:
        self.shell = shell
        self.client: Any = None
        self.model_id = ""
        self.badge: ui.badge | None = None
        self.meta: ui.label | None = None
        self.actions: ActionBar | None = None
        self.tree: TreeCard | None = None

    async def build(self, model_id: str) -> None:
        self.client = ui.context.client
        await self.client.connected()
        self.state = tab_state()  # the tab's, shared with its Suite page
        self.shell.page_setup()
        self.model_id = model_id
        ui.page_title(f"DSTF — {model_id.rsplit('.', 1)[-1]}")
        project = self.shell.session.project
        known = project is not None and project.test(model_id) is not None
        self.header = Header(self.shell, below=self._subheader if known else None)
        with ui.column().classes("dstf-test w-full q-pa-md gap-3"):
            if not known:
                ui.link("← Suite", "/").mark("back")
                ui.label(f"{model_id} is not a test of this project").classes(
                    "text-negative"
                )
                self.shell.register(self)
                return
            self.tree = TreeCard(self)
        self.shell.register(self)
        self.sync()

    def _subheader(self) -> None:
        """The test's own header, drawn inside the shared one (decision 8):
        the way back, the test, its verdict and where it was scored, then
        the TEST-placement buttons."""
        model_id = self.model_id
        ui.link("← Suite", "/").mark("back")
        ui.label(model_id).classes("text-subtitle1 dstf-mono").mark("model")
        self.badge = ui.badge("").classes("dstf-badge").mark("status")
        self.meta = ui.label().classes("text-secondary text-xs").mark("meta")
        ui.space()
        self.actions = ActionBar(
            self.shell,
            Placement.TEST,
            lambda: Selection(tests=frozenset({model_id})),
            marker="test-actions",
        )

    def unscored_reason(self) -> str:
        """Why this test has no tree: it never ran, or it has no baseline to
        be scored against."""
        project = self.shell.session.project
        result = project.results.get(self.model_id) if project is not None else None
        if result is None or not result.success:
            return "no result"
        return "no baseline"

    def open_criterion(self, path: str) -> None:
        """Open the criterion at ``path`` — the editor below its row, which
        arrives with the plot cards. The tree line's jumps call it."""

    # -- following the Session -------------------------------------------------

    def _paint(self) -> None:
        if self.badge is None or self.meta is None:
            return
        session = self.shell.session
        comp = session.comparisons.get(self.model_id)
        verdict, css = classify_with_class(comp) if comp is not None else ("", "queued")
        self.badge.set_text(verdict or "…")
        self.badge.style(f"background-color: var(--dstf-{css}-fill)")
        project = session.project
        if project is None:
            return
        ref_id = project.store.index.get_id(self.model_id)
        ref = None
        if ref_id is not None and project.store.get_reference(self.model_id):
            ref = RefIndex.ref_filename(ref_id).rsplit(".", 1)[0]
        self.meta.set_text(f"{project.config.backend_name} · {ref or 'no baseline'}")

    def sync(self) -> None:
        self._paint()
        if self.tree is not None:
            self.tree.refresh()

    def handle(self, event: dict[str, Any]) -> None:
        self.header.handle(event)
        if self.actions is not None:
            self.actions.refresh()
        if event["event"] in ("results", "spec", "loading", "opened", "job"):
            self.sync()
