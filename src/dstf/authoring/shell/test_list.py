"""The tests as rows, grouped by package or flat (brief §3.2; DESIGN.md
"Tables"): the Tests drawer's list (the Suite page's is a table,
`suite_grid.py`). A row's verdict and its unsaved edits repaint in place;
the list is rebuilt only when the test set, the order or the layout
moves."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from nicegui import ui
from nicegui.elements.mixins.text_element import TextElement

from ...reporting.rows import unsaved_text
from ...reporting.status import SCORING_FAILED, classify_with_class
from ...reporting.theme import STATUS

if TYPE_CHECKING:
    from .shell import Shell


@dataclass
class Row:
    """One test's row: the elements a click or a rescore updates in place —
    the whole list is never resent (roadmap §2 "the row model")."""

    model_id: str
    group: str
    container: ui.row
    name: ui.button
    badge: ui.badge
    #: ``N unsaved`` while the test has staged edits, hidden otherwise
    edits: ui.label
    verdict: str = ""  # the classifier's word, "" while unscored
    pending: int = 0  # the test's staged edits


def _package(model_id: str) -> str:
    return model_id.rpartition(".")[0]


def _name(model_id: str, short: bool) -> str:
    return model_id.rsplit(".", 1)[-1] if short else model_id


def _full_name(button: ui.button, model_id: str) -> None:
    """A Full name, cut at its START (the stylesheet's `.dstf-cut-start`):
    TRANSFORM's ids share long package prefixes, and cut at the end two of
    them read alike. Drawn in place of Quasar's label, in the `.block` span
    Quasar draws one in, the id in a `<bdi>`: the span is right-to-left,
    and a bare `direction: rtl` moved a leading or trailing `_` or `.` to
    the other end (Chromium); inside the isolate the id keeps its order.
    Not isolates from `::before` / `::after`: those entered the button's
    accessible name."""
    with button, ui.element("span").classes("block dstf-cut-start"):
        TextElement(tag="bdi", text=model_id)


class TestList:
    """The list's rows and group labels. ``current`` gets
    ``dstf-row-current`` on its row; ``marker`` prefixes every marker the
    list sets: ``tests``, ``group:<g>``, ``<id>``."""

    def __init__(
        self,
        shell: Shell,
        *,
        on_open: Callable[[str], None],
        current: str | None = None,
        marker: str,
    ) -> None:
        self.shell = shell
        self.on_open = on_open
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
                    name = (
                        ui.button(
                            _name(model_id, short) if short else "",
                            on_click=lambda m=model_id: self.on_open(m),
                        )
                        .props("flat dense no-caps align=left")
                        .classes("dstf-name")
                        .mark(f"{self.marker}{model_id}")
                    )
                    # Cut with an ellipsis in a narrow drawer: the whole id
                    # is its title (on the props dict: a props string
                    # breaks on a quote).
                    name.props["title"] = model_id
                    if not short:
                        _full_name(name, model_id)
                    # `color=None`: a Quasar badge otherwise carries
                    # `bg-primary`, whose `!important` beats the inline
                    # status fill and paints every verdict blue (the review
                    # of Task 4 of the 2026-09-20 Test page plan, in
                    # Chromium).
                    edits = (
                        ui.label()
                        .classes("dstf-pill dstf-pill-warn dstf-pill-named")
                        .mark(f"{self.marker}edits:{model_id}")
                    )
                    badge = ui.badge("", color=None).classes("dstf-badge")
                if model_id == self.current:
                    container.classes(add="dstf-row-current")
                self.rows[model_id] = Row(
                    model_id, group, container, name, badge, edits
                )
                self.paint(model_id)

    def paint(self, model_id: str) -> None:
        """One row's verdict: the badge's word and fill, the row's tint.
        `…` while unscored; `scoring failed` for a model the open announced
        with `error=True` (Task 1 of the 2026-09-16 Suite page plan). And
        its staged edits: ``N unsaved``, as the Suite's Edits column."""
        row = self.rows.get(model_id)
        if row is None:
            return
        comp = self.shell.session.comparisons.get(model_id)
        if comp is not None:
            verdict, css = classify_with_class(comp)
        elif model_id in self.shell.load.failed_models:
            verdict, css = SCORING_FAILED, "sim-fail"
        else:
            verdict, css = "", "queued"
        row.verdict = verdict
        row.pending = sum(
            1 for op in self.shell.session.spec.pending if op.model_id == model_id
        )
        row.edits.set_text(unsaved_text(row.pending))
        row.edits.set_visibility(row.pending > 0)
        row.badge.set_text(verdict or "…")
        row.badge.style(f"background-color: var(--dstf-{css}-fill)")
        # Add and remove, never replace: NiceGUI hides an element by appending
        # a `hidden` class, and a replaced list dropped it — every filtered-out
        # row came back on a rescore (the review's probe of Task 6 of the
        # 2026-09-16 Suite page plan).
        row.container.classes(
            remove=" ".join(f"dstf-tint-{s}" for s in STATUS), add=f"dstf-tint-{css}"
        )

    def filter(self, keep: Callable[[Row], bool]) -> None:
        """Visibility only — the rows stay. A group's label shows while any
        of its rows does."""
        shown_by_group: dict[str, bool] = {}
        for row in self.rows.values():
            shown = keep(row)
            row.container.set_visibility(shown)
            shown_by_group[row.group] = shown_by_group.get(row.group, False) or shown
        for group, label in self.groups.items():
            label.set_visibility(shown_by_group.get(group, False))
