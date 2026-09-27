"""The Suite page (brief §3.2 "two pages", §3.8; DESIGN.md "Tables"): what a
batch acts on. The header, the progress line, one toolbar — the count pills
and the view toggles (drawn by the table's script), the selection's label,
the batch actions as icons — the Suite table (`suite_grid.py`), the runs
card. The table filters, sorts and groups in the browser; Python sends its
rows and receives its selection, which lives in the tab (`tabstate.py`),
never in the Session (D129). One object per browser tab."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nicegui import ui

from ...reporting.rows import TestRow
from ..capabilities import Placement
from ..session import Selection
from ..suite_rows import suite_rows
from .actions import ActionBar
from .header import Header
from .runs import RunsCard
from .suite_grid import GRID_CSS, STORAGE, SuiteGrid, scripts_html
from .tabstate import TabState, tab_state

if TYPE_CHECKING:
    from .shell import Shell


class SuitePage:
    def __init__(self, shell: Shell) -> None:
        self.shell = shell
        self.client: Any = None
        #: The tests the grid holds, in the order Python sent them.
        self.ids: list[str] = []
        #: What this tick's events touched, sent by `flush` as one
        #: transaction: the models to add (in order) and to repaint, and
        #: the ids to drop.
        self._add: list[str] = []
        self._update: set[str] = set()
        self._remove: list[str] = []

    async def build(self) -> None:
        self.client = ui.context.client
        # BEFORE the handshake, so they are in the page's first HTML: after
        # it, `add_head_html` inserts through insertAdjacentHTML, which
        # never runs a <script> — the grid drew no rows, "DstfSuite is not
        # defined".
        ui.add_head_html(scripts_html(f"{STORAGE}:{self._project_key()}"))
        await self.client.connected()
        self.state: TabState = tab_state()
        self.shell.page_setup()
        ui.add_css(GRID_CSS)
        ui.page_title("DSTF")
        self.header = Header(self.shell)
        with ui.column().classes("dstf-suite w-full q-pa-md gap-3"):
            self.progress = ui.label().classes("dstf-progress").mark("progress")
            with ui.column().classes("gap-1") as self.hint:
                ui.label("No project is open. Start the tool on one:")
                ui.code("dstf author --config <path/to/testing.json>")
            with ui.row().classes(
                "dstf-suite-toolbar items-center w-full gap-3 no-wrap"
            ):
                # Both drawn by `suite_grid.js`; Python never updates them.
                ui.element("div").props("id=dstf-suite-tools").mark("tools")
                ui.space()
                ui.element("div").props("id=dstf-suite-selection").mark("selected")
                self.actions = ActionBar(
                    self.shell,
                    Placement.SUITE,
                    lambda: Selection(tests=frozenset(self.state.checked)),
                    marker="suite-actions",
                    icons=True,
                )
            self.ids = self.shell.visible_tests()
            self.grid = SuiteGrid(
                self._rows(self.ids),
                selected=self.state.checked,
                on_selection=self._on_selection,
            ).mark("grid")
            self.runs = RunsCard(self.shell)
        self.grid.seal()
        self.shell.register(self)
        self.refresh()

    def _project_key(self) -> str:
        """What names this project for the table's stored view: its work
        dir — a reopen on another backend is another one — or, before the
        open has one, the work dir or the config the shell was given."""
        project = self.shell.session.project
        if project is not None and project.config.work_dir is not None:
            return str(project.config.work_dir)
        if self.shell.work_dir is not None:
            return str(self.shell.work_dir.resolve())
        return str(self.shell.config_path.resolve())

    def _rows(self, ids: list[str]) -> list[TestRow]:
        if self.shell.known:
            # An open is under way: the Session's comparisons are the
            # previous project's, or none. Its names wait for its verdicts.
            return [TestRow(model_id=m, waiting=True) for m in ids]
        return suite_rows(
            self.shell.session, ids, failed=set(self.shell.load.failed_models)
        )

    # -- the selection ---------------------------------------------------------

    def _on_selection(self, ids: list[str]) -> None:
        """The grid's selection, from the browser: the tab's (D129)."""
        self.state.checked = set(ids)
        self.actions.refresh()

    def refresh(self) -> None:
        load = self.shell.load
        text = load.text()
        self.progress.set_text(text)
        self.progress.set_visibility(bool(text))
        self.hint.set_visibility(
            self.shell.session.project is None and not load.in_progress
        )

    # -- the Session's events --------------------------------------------------

    def handle(self, event: dict[str, Any]) -> None:
        """Every Session event, from the shell's tick. No early return: the
        card, the actions and the rows are independent regions, and a
        `spec` (a close, an `OpenProject`) is exactly the event after which
        the card must not show the previous project's outcome. The rows an
        event touches are collected; `flush` sends them."""
        self.header.handle(event)
        kind = event["event"]
        if kind in ("job", "poll", "opened", "spec"):
            self.runs.refresh()  # `spec`: an open or a close resets the card's sources
        if kind in ("results", "job", "spec", "opened") or (
            kind == "loading" and event.get("phase") == "done"
        ):
            self.actions.refresh()
        if kind in ("spec", "known"):
            # A `spec` event names only the CHANGED models (an open's names
            # all, before the CLI's filter; a close's none), and `known` the
            # models an open named before its scan: what to add and drop is
            # the visible tests against the grid's.
            now = self.shell.visible_tests()
            have, keep = set(self.ids), set(now)
            self._add += [m for m in now if m not in have]
            gone = [m for m in self.ids if m not in keep]
            self._remove += gone
            self._update -= set(gone)
            self.ids = now
        self._update |= self._touched(event) & set(self.ids)
        self.refresh()

    def _touched(self, event: dict[str, Any]) -> set[str]:
        """The models whose row an event may have changed."""
        kind = event["event"]
        if kind in ("spec", "results", "known"):
            return {str(m) for m in event.get("models", ())}
        if kind == "loading" and event.get("phase") == "results":
            return {str(event["model"])}
        job = event.get("job") if kind == "job" else None
        return set(job.models) if job is not None else set()

    def flush(self) -> None:
        """This tick's rows, as ONE transaction: the Accept path queues N + 2
        events in a tick, and one transaction per event was 333 over 331
        rows. An added row is sent once, with the tab's selection."""
        held = set(self.ids)
        add = [m for m in self._add if m in held]
        remove = self._remove
        repaint = self._update - set(add)
        update = [m for m in self.ids if m in repaint]
        self._add, self._update, self._remove = [], set(), []
        if not (add or update or remove):
            return
        self.grid.transaction(
            add=self._rows(add),
            update=self._rows(update),
            remove=remove,
            select=[m for m in add if m in self.state.checked],
        )
