"""The Suite page (brief §3.2 "two pages", §3.8; DESIGN.md "Tables"): what a
batch acts on. The header, the progress line, one toolbar — the count pills
and the view toggles (drawn by the table's script), the selection's label,
the batch actions as icons — the Suite table (`suite_grid.py`), the runs
card. The table filters, sorts and groups in the browser; Python sends its
rows and receives its selection, which lives in the tab (`tabstate.py`),
never in the Session (D129). One object per browser tab."""

from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)


class SuitePage:
    def __init__(self, shell: Shell) -> None:
        self.shell = shell
        self.client: Any = None
        #: The tests the grid shows once this tick is flushed: the visible
        #: tests, in the project's order.
        self.ids: list[str] = []
        #: The tests the grid holds now: what the last flush sent.
        self._sent: list[str] = []
        #: What this tick's events touched, repainted by `flush` in one
        #: transaction.
        self._update: set[str] = set()
        #: The last flush raised building its rows: this one builds them one
        #: by one and leaves out the ones that raise.
        self._retrying = False
        #: Visible tests whose row failed to build when they were to be
        #: added: not in the grid, and added again only once an event
        #: touches them.
        self._dropped: set[str] = set()

    async def build(self) -> None:
        self.client = ui.context.client
        # BEFORE the handshake, so they are in the page's first HTML: after
        # it, `add_head_html` inserts through insertAdjacentHTML, which
        # never runs a <script> — the grid drew no rows, "DstfSuite is not
        # defined".
        ui.add_head_html(scripts_html(f"{STORAGE}:{self._project_key()}"))
        # The grid's height too: added after the handshake, it arrived after
        # the grid was built whenever the scripts came from the browser's
        # cache — every second load — and AG Grid, ready at NiceGUI's
        # default 256 px, drew the first 15 rows of a 710 px table and left
        # the rest blank until a scroll or a resize.
        ui.add_css(GRID_CSS)
        await self.client.connected()
        self.state: TabState = tab_state()
        self.shell.page_setup()
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
            self._sent = list(self.ids)
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
        """What names this project for the table's stored view: its config
        file, resolved — known before the open and the same after it (the
        work dir is derived by the open when `--work-dir` is not given).
        `dstf author` refuses to start without a config, so there always is
        one. A reopen on another backend keeps the view."""
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
            # the visible tests against the grid's, at the flush.
            self.ids = self.shell.visible_tests()
        self._update |= self._touched(event) & set(self.ids)
        self.refresh()

    def _touched(self, event: dict[str, Any]) -> set[str]:
        """The models whose row an event may have changed."""
        kind = event["event"]
        if kind == "known":
            # Every row: an open's names wait, and its failure's `known`
            # (naming none) turns every row back to the open project's
            # verdict — repainting only the event's models left every row
            # reading "…" after a failed reopen.
            return set(self.ids)
        if kind in ("spec", "results"):
            return {str(m) for m in event.get("models", ())}
        if kind == "loading" and event.get("phase") == "results":
            return {str(event["model"])}
        job = event.get("job") if kind == "job" else None
        return set(job.models) if job is not None else set()

    def flush(self) -> None:
        """This tick's rows, as ONE transaction: the Accept path queues N + 2
        events in a tick, and one transaction per event was 333 over 331
        rows. The rows to add and drop are the visible tests against the
        grid's — a model named and dropped within the tick is sent neither,
        so the grid is never told to drop a row it never had, and one
        dropped and named again stays (repainted if an event touched it).
        An added row is sent once, with the tab's selection. What the grid
        holds moves only once the transaction is sent: one that raises is
        sent again on the next tick.

        Retried once: when building the rows raised, the next tick builds
        them one by one, sends the ones that build and moves on — one row
        that always raises froze the grid (a traceback per tick, no update
        sent). A row left out keeps its last painted state; one that was to
        be added is not in the grid, and is added again once an event
        touches it."""
        held, sent = set(self.ids), set(self._sent)
        self._dropped &= held
        add = [
            m
            for m in self.ids
            if m not in sent and (m not in self._dropped or m in self._update)
        ]
        remove = [m for m in self._sent if m not in held]
        update = [m for m in self.ids if m in sent and m in self._update]
        added = set(add)
        if add or update or remove:
            if self._retrying:
                add_rows, bad_add = self._rows_each(add)
                update_rows, bad_update = self._rows_each(update)
                if bad_add or bad_update:
                    logger.error(
                        "the Suite table left out %s: %s",
                        ", ".join(bad_add + bad_update),
                        "its row failed to build again",
                    )
                added -= set(bad_add)
            else:
                try:
                    add_rows, update_rows = self._rows(add), self._rows(update)
                except Exception:
                    self._retrying = True
                    raise  # the shell logs it; the next tick goes row by row
            self.grid.transaction(
                add=add_rows,
                update=update_rows,
                remove=remove,
                select=[m for m in add if m in added and m in self.state.checked],
            )
            self._dropped = (self._dropped - added) | (set(add) - added)
        self._retrying = False
        self._sent = [m for m in self.ids if m in sent or m in added]
        self._update = set()

    def _rows_each(self, ids: list[str]) -> tuple[list[TestRow], list[str]]:
        """``ids``' rows built one at a time, and the ids whose build raised."""
        rows: list[TestRow] = []
        bad: list[str] = []
        for m in ids:
            try:
                rows += self._rows([m])
            except Exception:
                bad.append(m)
        return rows, bad
