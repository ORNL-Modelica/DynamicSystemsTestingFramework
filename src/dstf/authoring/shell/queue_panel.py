"""The simulation queue on the page (D150; brief §3.6; the `Main` and
`QueueIndicator` artboards): the header's indicator, on every page, and the
queue panel on the Suite page — one row per item in run order, drawn from
`authoring.queue_rows`. The panel also carries what the runs card it
replaced did beside the job: the store writes' lines and the artifacts.

Nothing here reads a file or a clock of its own: the rows are the Session's
queue as data, repainted on its `queue` events, and the shell's `poll` —
once a second while a test runs or a worker starts — moves only the words of the
rows still to end and the lines of the workers being started."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from nicegui import ui

from ...backends.pool import ItemState
from ...reporting.status import STATUS_CLASSES, STATUS_LABELS
from ..capabilities import Capability, Placement
from ..errors import user_text
from ..queue_rows import (
    CANCEL,
    OPEN,
    REMOVE,
    RESTARTING,
    QueueRow,
    counts_text,
    indicator,
    queue_rows,
    starting_texts,
)
from ..session import Selection
from ..typed_number import TypedNumber
from .actions import ActionBar, log_unless_users
from .widgets import Action, IconAction

if TYPE_CHECKING:
    from .shell import Shell

#: The panel's anchor: the header's indicator links to it.
ANCHOR = "queue"

QUEUE_CSS = """
/* It gives way only past the counts' width: a stopped pool's message is
   cut with an ellipsis, "1 running · 2 queued" never is (it was, at 1024). */
.dstf-indicator { font-size: 13px; color: var(--dstf-text-secondary); text-decoration: none;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 0 1 auto;
  min-width: 10.5em; text-align: center; }
.dstf-indicator:hover { text-decoration: underline; }
.dstf-indicator-running { color: var(--dstf-running-dark); font-weight: 600; }
.dstf-indicator-failed { color: var(--dstf-fail-dark); font-weight: 600; }
/* The table and the panel side by side (the Main artboard); under 1200 px,
   and while the queue is empty, the panel is a line above the table. */
.dstf-suite-body { display: flex; flex-wrap: wrap; align-items: flex-start; gap: 16px;
  width: 100%; }
.dstf-suite-main { flex: 1 1 0; min-width: 0; }
.dstf-queue.dstf-card { flex: 0 0 420px; min-width: 0; padding: 8px 0; scroll-margin-top: 64px; }
.dstf-queue.dstf-queue-empty { flex: 1 1 100%; order: -1; padding: 2px 0; }
@media (max-width: 1199px) { .dstf-queue.dstf-card { flex: 1 1 100%; order: -1; } }
.dstf-queue-head { padding: 0 12px; row-gap: 4px; }
.dstf-queue-title { font-weight: 600; }
.dstf-queue-workers { width: 76px; }
.dstf-queue-row { padding: 4px 12px; border-top: 1px solid var(--dstf-border-hairline); }
/* Above the table every row is a row the table sits lower by: there the
   rows scroll inside about a third of the window (fifteen uncleared ones
   put the table below the fold). Beside it the panel pushes nothing. */
@media (max-width: 1199px) { .dstf-queue-rows { max-height: 34vh; overflow-y: auto; } }
.dstf-queue-num { width: 20px; text-align: right; flex: none; font-family: var(--dstf-mono);
  color: var(--dstf-text-muted); }
.dstf-queue-what { flex: 1 1 0; min-width: 0; }
.dstf-queue-what > * { max-width: 100%; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }
.dstf-queue-words { font-size: 12px; color: var(--dstf-text-secondary); }
.dstf-queue-starting > * { padding: 2px 12px; overflow-wrap: anywhere; }
.dstf-queue-acts { flex: none; }
.dstf-queue-error { margin: 0 12px; padding: 4px 8px; border-radius: 4px; font-size: 12px;
  color: var(--dstf-fail-dark); background: var(--dstf-fail-tint);
  border: 1px solid var(--dstf-fail-border); overflow-wrap: anywhere; }
.dstf-queue-notes { padding: 4px 12px 0; overflow-wrap: anywhere; }
"""

WORKERS_HINT = (
    "How many tests run at once. A change applies to workers started after "
    "it: a running test keeps its worker."
)
STOP_WORKERS_HINT = (
    "Ends the simulator processes and frees their licence seats; the next "
    "queued test starts new ones."
)


class QueueIndicator:
    """The header's queue indicator (every page): `3 running · 5 queued`,
    `idle`, or — red — the first words of the message the pool stopped
    with. A click opens the Suite page at the queue panel."""

    def __init__(self, shell: Shell) -> None:
        self.shell = shell
        ui.add_css(QUEUE_CSS)
        self.link = (
            ui.link("", f"/#{ANCHOR}").classes("dstf-indicator").mark("queue-indicator")
        )
        with self.link:
            self.tip = ui.tooltip("").mark("queue-indicator:tip")
        self.refresh()

    def refresh(self) -> None:
        state = indicator(self.shell.session)
        self.link.set_text(state.text)
        self.link.classes(
            remove="dstf-indicator-idle dstf-indicator-running dstf-indicator-failed",
            add=f"dstf-indicator-{state.tone}",
        )
        self.tip.set_text(state.tip)


class QueuePanel:
    def __init__(self, shell: Shell) -> None:
        self.shell = shell
        #: The test a row's capability button was clicked on: the selection
        #: the panel's one dialog acts over.
        self.row_model: str | None = None
        #: The words of the rows still to end — waiting or running — and
        #: the state each was drawn in, repainted by `tick`.
        self._moving: dict[str, tuple[ItemState, ui.label]] = {}
        #: One line per worker being started (`_paint_starting`).
        self._starting: list[ui.label] = []
        cores = os.cpu_count() or 1
        with (
            ui.column()
            .classes("dstf-card dstf-queue gap-1")
            .props(f"id={ANCHOR}")
            .mark("queue") as self.card
        ):
            with ui.row().classes("dstf-queue-head items-center gap-2 w-full"):
                ui.label("Simulation queue").classes("dstf-queue-title")
                self.counts = ui.label().classes("dstf-why").mark("queue-counts")
                ui.space()
                self.workers = (
                    TypedNumber(
                        label="workers",
                        min=1,
                        max=cores,
                        step=1,
                        format="%d",
                        on_change=self._set_workers,
                    )
                    .props("dense outlined")
                    .classes("dstf-queue-workers")
                    .mark("queue-workers")
                )
                with self.workers:
                    ui.tooltip(WORKERS_HINT)
                self.workers_note = ui.label().classes("dstf-why").mark("queue-note")
                self.clear = (
                    ui.button("Clear finished", on_click=self._clear)
                    .props("flat dense no-caps")
                    .mark("queue-clear")
                )
                more = (
                    ui.button(icon="more_vert")
                    .props('flat dense round aria-label="Queue actions"')
                    .props('aria-haspopup="menu"')
                    .mark("queue-more")
                )
                with (
                    more,
                    ui.menu(),
                    ui.menu_item("Stop workers", on_click=self._stop_workers)
                    .props('role="menuitem"')
                    .mark("queue-stop-workers"),
                ):
                    ui.tooltip(STOP_WORKERS_HINT)
            self.error = ui.label().classes("dstf-queue-error").mark("queue-error")
            # The workers being brought up, one line each: a library's
            # load is minutes, and its tests wait meanwhile.
            self.starting = (
                ui.column().classes("dstf-queue-starting w-full gap-0")
            ).mark("queue-starting")
            self.rows = (
                ui.column().classes("dstf-queue-rows w-full gap-0").mark("queue-rows")
            )
            self.notes = ui.column().classes("dstf-queue-notes w-full gap-1")
        # The rows' capability buttons open ONE dialog, the panel's — a
        # row's own would go with the row at the next repaint. The bar
        # draws no button of its own.
        self.bar = ActionBar(
            shell,
            Placement.SUITE,
            lambda: Selection(
                tests=frozenset({self.row_model} if self.row_model else ())
            ),
            marker="queue-row-actions",
        )
        self.bar.clear()
        self.bar.container.set_visibility(False)
        self.refresh()

    # -- the Session's events --------------------------------------------------

    def handle(self, event: dict[str, Any]) -> None:
        kind = event["event"]
        if kind == "poll":
            self.tick()
        elif kind in ("queue", "notice", "results", "spec", "opened"):
            # `results`: a finished row's badge is its test's verdict, and
            # an edit rescored it. `spec`: an open or a close resets the
            # queue and the notices.
            self.refresh()

    def tick(self) -> None:
        """The words of the rows still to end — a running row's seconds or
        how far its result file has got (D151), a waiting row's "worker
        starting" — and the starting workers' lines; nothing else moved."""
        rows = {r.model_id: r for r in queue_rows(self.shell.session)}
        for model_id, (state, label) in self._moving.items():
            row = rows.get(model_id)
            if row is not None and row.state is state:
                label.set_text(_words(row))
        self._paint_starting()

    def _paint_starting(self) -> None:
        texts = starting_texts(self.shell.session)
        if len(self._starting) != len(texts):
            self.starting.clear()
            with self.starting:
                self._starting = [
                    ui.label().classes("dstf-queue-words").mark("queue-starting-worker")
                    for _ in texts
                ]
        for label, text in zip(self._starting, texts):
            label.set_text(text)
        self.starting.set_visibility(bool(texts))

    def refresh(self) -> None:
        session = self.shell.session
        rows = queue_rows(session)
        stopped = session.queue_stopped
        empty = not rows and stopped is None
        self.card.classes(
            remove="dstf-queue-empty", add="dstf-queue-empty" if empty else ""
        )
        self.counts.set_text(counts_text(rows))
        project = session.project
        self.workers.set_enabled(project is not None)
        if project is not None and self.workers.value != session.workers:
            self.workers.set_value(session.workers)
        self.workers_note.set_text(
            f"of {project.config.parallel} in testing.json" if project else ""
        )
        ended = any(r.state not in (ItemState.WAITING, ItemState.RUNNING) for r in rows)
        self.clear.set_visibility(ended or stopped is not None)
        self.error.set_text(stopped or "")
        self.error.set_visibility(stopped is not None)
        self._paint_starting()
        self.rows.clear()
        self._moving = {}
        with self.rows:
            for row in rows:
                self._row(row)
        self.notes.clear()
        with self.notes:
            for notice in session.notices:
                ui.label(notice).classes("text-xs").mark("notice")
            for artifact in session.artifacts:
                for path in artifact.paths:
                    ui.link(
                        f"{artifact.label}: {path.name}", path.as_uri(), new_tab=True
                    )
        self.notes.set_visibility(bool(session.notices or session.artifacts))

    # -- one row -----------------------------------------------------------------

    def _row(self, row: QueueRow) -> None:
        model_id = row.model_id
        css = STATUS_CLASSES[row.status]
        with (
            ui.row()
            .classes(
                f"dstf-queue-row dstf-tint-{css} items-center gap-2 w-full no-wrap"
            )
            # The row's test, for a script that reads the page.
            .props(f'data-model="{model_id}"')
            .mark(f"queue-row:{model_id}")
        ):
            ui.label(str(row.number)).classes("dstf-queue-num")
            ui.badge(STATUS_LABELS[row.status], color=None).classes("dstf-badge").style(
                f"background-color: var(--dstf-{css}-fill)"
            ).mark(f"queue-status:{model_id}")
            with ui.column().classes("dstf-queue-what gap-0"):
                with ui.link(
                    model_id.rpartition(".")[2], f"/test/{quote(model_id, safe='')}"
                ).mark(f"queue-test:{model_id}"):
                    ui.tooltip(model_id)
                words = (
                    ui.label(_words(row))
                    .classes("dstf-queue-words")
                    .mark(f"queue-words:{model_id}")
                )
                if row.message:
                    # The backend's message in full; the row says its first line.
                    with words:
                        ui.tooltip(row.message).mark(f"queue-words:{model_id}:tip")
                if row.state in (ItemState.WAITING, ItemState.RUNNING):
                    self._moving[model_id] = (row.state, words)
            with ui.row().classes("dstf-queue-acts items-center gap-1 no-wrap"):
                self._actions(row)

    def _actions(self, row: QueueRow) -> None:
        model_id = row.model_id
        item = self.shell.session.queue[row.number - 1]
        if REMOVE in row.actions:
            with (
                ui.button("Remove", on_click=lambda: self._do(self._remove, item))
                .props("flat dense no-caps")
                .mark(f"queue-remove:{model_id}")
            ):
                ui.tooltip("Takes it out of the queue before it starts.")
        if CANCEL in row.actions:
            with (
                ui.button("Cancel", on_click=lambda: self._do(self._cancel, item))
                .props("flat dense no-caps color=negative")
                .mark(f"queue-cancel:{model_id}")
            ):
                ui.tooltip("Stops this simulation. It leaves no result.")
        elif row.cancel_why is not None:
            # No Cancel where the backend cannot stop a test: the sentence
            # says why, in the button's place.
            with ui.icon("info").classes("text-secondary"):
                ui.tooltip(row.cancel_why).mark(f"queue-cancel:{model_id}:why")
        if OPEN not in row.actions:
            return
        url = f"/test/{quote(model_id, safe='')}"
        ui.button("Open test", on_click=lambda: ui.navigate.to(url)).props(
            "flat dense no-caps"
        ).mark(f"queue-open:{model_id}")
        # What the Suite's batch bar offers, for this one test: Accept on a
        # finished row, Queue again on any. The panel knows neither by name.
        view = self.shell.view_for(Selection(tests=frozenset({model_id})))
        for cap, avail in self.shell.placed(Placement.SUITE, view):
            try:
                caption = cap.caption(view)
            except Exception as e:  # the label is the fallback; the row draws
                caption = cap.label
                self.shell.report_once(cap.name, "caption", e)
            reason = (
                None
                if avail is True
                else (avail if isinstance(avail, str) else "not available")
            )
            marker = f"queue-{cap.name}:{model_id}"
            icon = getattr(cap, "icon", None)
            action: Action | IconAction
            if icon is not None:
                action = IconAction(
                    self._opener(cap, model_id), icon=icon, marker=marker
                )
            else:
                action = Action(
                    self._opener(cap, model_id),
                    props="flat dense no-caps",
                    marker=marker,
                    why=False,
                )
            action.set(caption, reason)

    def _opener(self, cap: Capability, model_id: str) -> Callable[[], None]:
        def open_dialog() -> None:
            self.row_model = model_id
            self.bar.open(cap)

        return open_dialog

    # -- the panel's own actions -------------------------------------------------

    def _do(self, act: Callable[[Any], None], item: Any) -> None:
        """A row's Remove or Cancel; a refusal is said, never raised."""
        try:
            act(item)
        except Exception as e:
            log_unless_users(e, "queue")
            ui.notify(user_text(e), type="warning")

    def _remove(self, item: Any) -> None:
        if not self.shell.session.remove(item):
            ui.notify(f"{item.model_id} has already started", type="warning")

    def _cancel(self, item: Any) -> None:
        if not self.shell.session.cancel(item):
            ui.notify(f"{item.model_id} is no longer running", type="warning")

    def _set_workers(self, e: Any) -> None:
        session = self.shell.session
        if e.value is None or session.project is None:
            return
        count = int(e.value)
        if count != session.workers:
            session.set_workers(count)

    def _clear(self) -> None:
        self.shell.session.clear_finished()

    def _stop_workers(self) -> None:
        try:
            self.shell.session.stop_workers()
        except Exception as e:
            log_unless_users(e, "stop workers")
            ui.notify(user_text(e), type="warning")
            return
        ui.notify("workers stopped")


def _words(row: QueueRow) -> str:
    """The row's state line: its words, and its worker while it runs — the
    worker's line above says which while it is being brought back."""
    if row.worker is None or row.progress == RESTARTING:
        return row.words
    return f"{row.words} · worker {row.worker}"
