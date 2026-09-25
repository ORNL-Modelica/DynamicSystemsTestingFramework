"""The tests as rows grouped by package (brief §3.2; DESIGN.md "Tables"):
the Suite page's list, with checkboxes, and the Tests drawer's, without
(the owner's ruling, 2026-09-25). A row's verdict repaints in place; the
list is rebuilt only when the test set or the layout moves."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from nicegui import ui

from ...reporting.status import classify_with_class
from ...reporting.theme import STATUS

if TYPE_CHECKING:
    from .shell import Shell


@dataclass
class Row:
    """One test's row: the elements a check, a click or a rescore updates in
    place — the whole list is never resent (roadmap §2 "the row model")."""

    model_id: str
    group: str
    container: ui.row
    checkbox: ui.checkbox | None  # None in a list without checkboxes
    name: ui.button
    badge: ui.badge
    verdict: str = ""  # the classifier's word, "" while unscored


def _package(model_id: str) -> str:
    return model_id.rpartition(".")[0]


def _name(model_id: str, short: bool) -> str:
    return model_id.rsplit(".", 1)[-1] if short else model_id


class TestList:
    """The list's rows and group labels. ``checked is None`` builds no
    checkbox (the drawer); ``current`` gets ``dstf-row-current`` on its row;
    ``marker`` prefixes every marker the list sets — ``""`` keeps the Suite
    page's: ``tests``, ``group:<g>``, ``check:<id>``, ``<id>``."""

    def __init__(
        self,
        shell: Shell,
        *,
        on_open: Callable[[str], None],
        checked: set[str] | None = None,
        on_check: Callable[[str, bool], None] | None = None,
        current: str | None = None,
        marker: str = "",
    ) -> None:
        self.shell = shell
        self.on_open = on_open
        self.checked = checked
        self.on_check = on_check
        self.current = current
        self.marker = marker
        self.rows: dict[str, Row] = {}
        self.groups: dict[str, ui.label] = {}
        self.column = (
            ui.column().classes("dstf-tests w-full gap-0").mark(f"{marker}tests")
        )

    def rebuild(self, ids: Sequence[str], *, grouped: bool, short: bool) -> None:
        """The rows from scratch, in ``ids`` order — by package first when
        ``grouped``, each group under its label."""
        self.column.clear()
        self.rows, self.groups = {}, {}
        position = {m: i for i, m in enumerate(ids)}
        order = (
            sorted(ids, key=lambda m: (_package(m), position[m])) if grouped else ids
        )
        with self.column:
            current: str | None = None
            for model_id in order:
                group = _package(model_id)
                if grouped and group != current:
                    current = group
                    self.groups[group] = (
                        ui.label(group or "(top level)")
                        .classes("dstf-group")
                        .mark(f"{self.marker}group:{group}")
                    )
                with ui.row().classes(
                    "dstf-row items-center no-wrap gap-2 w-full"
                ) as container:
                    checkbox = None
                    if self.checked is not None:
                        checkbox = (
                            ui.checkbox(
                                value=model_id in self.checked,
                                on_change=lambda e, m=model_id: self._check(
                                    m, bool(e.value)
                                ),
                            )
                            .props("dense")
                            .mark(f"{self.marker}check:{model_id}")
                        )
                    name = (
                        ui.button(
                            _name(model_id, short),
                            on_click=lambda m=model_id: self.on_open(m),
                        )
                        .props("flat dense no-caps align=left")
                        .classes("dstf-name")
                        .mark(f"{self.marker}{model_id}")
                    )
                    # `color=None`: a Quasar badge otherwise carries
                    # `bg-primary`, whose `!important` beats the inline
                    # status fill and paints every verdict blue (the Task 4
                    # review, in Chromium).
                    badge = ui.badge("", color=None).classes("dstf-badge")
                if model_id == self.current:
                    container.classes(add="dstf-row-current")
                self.rows[model_id] = Row(
                    model_id, group, container, checkbox, name, badge
                )
                self.paint(model_id)

    def _check(self, model_id: str, on: bool) -> None:
        if self.on_check is not None:
            self.on_check(model_id, on)

    def paint(self, model_id: str) -> None:
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

    def filter(self, needle: str, statuses: set[str]) -> None:
        """Visibility only — the rows stay. A group's label shows while any
        of its rows does."""
        needle = needle.lower()
        shown_by_group: dict[str, bool] = {}
        for row in self.rows.values():
            shown = (not needle or needle in row.model_id.lower()) and (
                not statuses or row.verdict in statuses
            )
            row.container.set_visibility(shown)
            shown_by_group[row.group] = shown_by_group.get(row.group, False) or shown
        for group, label in self.groups.items():
            label.set_visibility(shown_by_group.get(group, False))

    def shown(self) -> list[str]:
        return [m for m, row in self.rows.items() if row.container.visible]
