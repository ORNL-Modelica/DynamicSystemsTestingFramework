"""The Suite page (brief §3.2 "two pages", §3.8; DESIGN.md "Tables"): what a
batch acts on. The header, the progress line, the test list with its
filters and checkboxes, the batch actions, the runs card. One object per
browser tab; its selection lives in the tab (`tabstate.py`), never in the
Session (D129)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from nicegui import ui

from ...reporting.status import (
    FAIL,
    NO_REF,
    PASS,
    SIM_FAIL,
    classify_with_class,
    count_by_status,
)
from ...reporting.theme import STATUS
from ..capabilities import Placement, plural
from ..session import Selection
from .actions import ActionBar
from .header import Header
from .runs import RunsCard
from .tabstate import TabState, tab_state
from .widgets import Action

if TYPE_CHECKING:
    from .shell import Shell


CHIP_ORDER = (PASS, FAIL, SIM_FAIL, NO_REF)


@dataclass
class Row:
    """One test's row: the elements a check, a click or a rescore updates in
    place — the whole list is never resent (roadmap §2 "the row model")."""

    model_id: str
    group: str
    container: ui.row
    checkbox: ui.checkbox
    name: ui.button
    badge: ui.badge
    verdict: str = ""  # the classifier's word, "" while unscored


def _package(model_id: str) -> str:
    return model_id.rpartition(".")[0]


def _name(model_id: str, short: bool) -> str:
    return model_id.rsplit(".", 1)[-1] if short else model_id


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
        self.rows: dict[str, Row] = {}
        self.groups: dict[str, ui.label] = {}
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
            self.list = ui.column().classes("dstf-tests w-full gap-0").mark("tests")
            self.runs = RunsCard(self.shell)
        self.shell.register(self)
        self.rebuild()

    # -- the rows ---------------------------------------------------------------

    def rebuild(self) -> None:
        """The rows from scratch: on the first build, on a changed test set
        (a `spec` event), on a layout toggle. A check, a click, a filter or a
        rescore never comes here — each updates one element."""
        self.list.clear()
        self.rows, self.groups = {}, {}
        ids = self.shell.visible_tests()
        position = {m: i for i, m in enumerate(ids)}
        order = (
            sorted(ids, key=lambda m: (_package(m), position[m]))
            if self.state.grouped
            else ids
        )
        with self.list:
            current: str | None = None
            for model_id in order:
                group = _package(model_id)
                if self.state.grouped and group != current:
                    current = group
                    self.groups[group] = (
                        ui.label(group or "(top level)")
                        .classes("dstf-group")
                        .mark(f"group:{group}")
                    )
                with ui.row().classes(
                    "dstf-row items-center no-wrap gap-2 w-full"
                ) as container:
                    checkbox = (
                        ui.checkbox(
                            value=model_id in self.state.checked,
                            on_change=lambda e, m=model_id: self._toggle(
                                m, bool(e.value)
                            ),
                        )
                        .props("dense")
                        .mark(f"check:{model_id}")
                    )
                    name = (
                        ui.button(
                            _name(model_id, self.state.short),
                            on_click=lambda m=model_id: self._open(m),
                        )
                        .props("flat dense no-caps align=left")
                        .classes("dstf-name")
                        .mark(model_id)
                    )
                    badge = ui.badge("").classes("dstf-badge")
                self.rows[model_id] = Row(
                    model_id, group, container, checkbox, name, badge
                )
                self._paint(model_id)
        self._apply_filter()
        self.refresh()

    def _paint(self, model_id: str) -> None:
        """One row's verdict: the badge's word and fill, the row's tint.
        `…` while unscored; `scoring failed` for a model the open announced
        with `error=True` (Task 1)."""
        row = self.rows.get(model_id)
        if row is None:
            return
        comp = self.shell.session.comparisons.get(model_id)
        if comp is not None:
            verdict, css = classify_with_class(comp)
        elif model_id in self.shell.load.failed_models:
            verdict, css = "scoring failed", "sim-fail"
        else:
            verdict, css = "", "queued"
        row.verdict = verdict
        row.badge.set_text(verdict or "…")
        row.badge.style(f"background-color: var(--dstf-{css}-fill)")
        # Add and remove, never replace: NiceGUI hides an element by appending
        # a `hidden` class, and a replaced list dropped it — every filtered-out
        # row came back on a rescore (the Task 6 review's probe).
        row.container.classes(
            remove=" ".join(f"dstf-tint-{s}" for s in STATUS), add=f"dstf-tint-{css}"
        )

    def _apply_filter(self) -> None:
        """Visibility only — the rows stay."""
        needle = self.state.needle.lower()
        wanted = self.state.status_filter
        shown_by_group: dict[str, bool] = {}
        for row in self.rows.values():
            shown = (not needle or needle in row.model_id.lower()) and (
                not wanted or row.verdict in wanted
            )
            row.container.set_visibility(shown)
            shown_by_group[row.group] = shown_by_group.get(row.group, False) or shown
        for group, label in self.groups.items():
            label.set_visibility(shown_by_group.get(group, False))

    def _shown(self) -> list[str]:
        return [m for m, row in self.rows.items() if row.container.visible]

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
            self.rows[model_id].checkbox.set_value(True)
        self.refresh()
        self.actions.refresh()

    def _uncheck(self) -> None:
        for model_id in list(self.state.checked):
            self.state.checked.discard(model_id)
            row = self.rows.get(model_id)
            if row is not None:
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
