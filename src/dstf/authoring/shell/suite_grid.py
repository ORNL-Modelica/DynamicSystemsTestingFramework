"""The Suite table: an AG Grid Community grid (`ui.aggrid`, AG Grid 34.2 as
NiceGUI 3.16 bundles it) over the row model's columns (`reporting.rows`).
Its filtering, sorting, grouping and the toolbar that drives them run in
the browser (`suite_grid.js`, through the typed filters of
`reporting/templates/filters.js`); Python sends rows and receives the
selection.

The grid is created ONCE per page and afterwards driven only through
`run_grid_method` / `run_javascript`: NiceGUI's aggrid re-creates the whole
grid on any Python-side update of the element (`aggrid.js`: its
`_update_method` is `update_grid`, which destroys the grid and builds a new
one from the element's options), losing the sort, the selection and every
row patched since. `SuiteGrid.seal()` makes that loud."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from nicegui import ui

from ...reporting.rows import COLUMNS, EDITS, FILTER_HINTS, Column, TestRow
from ...reporting.status import (
    FAIL,
    NO_REF,
    NOT_RUN,
    PASS,
    RUNNING,
    SIM_FAIL,
    STATUS_CLASSES,
    STATUS_LABELS,
)

HERE = Path(__file__).parent
#: The typed filters, one module for the dashboard and this table.
FILTERS_JS = HERE.parent.parent / "reporting" / "templates" / "filters.js"
#: Where the shell serves the two scripts (`Shell.mount`).
ASSETS = "/_dstf"
#: A per-viewer key for the table's view (localStorage), per project.
STORAGE = "dstf-suite-v1"

#: A live run's columns (the runs card shows a running job): always "—" in
#: the tool, so not even the column chooser offers them.
LIVE_RUN = ("test", "worker", "elapsed")
TOOL_COLUMNS: tuple[Column, ...] = (
    *(c for c in COLUMNS if c.key not in LIVE_RUN),
    EDITS,
)
#: Shown by default: the verdict, the test, its numbers, its edits and its
#: message — Edits before Message, which takes the width left over, so a
#: narrow window cuts the message, not the edits. The rest are in the
#: column chooser.
VISIBLE = ("status", "model", "nrmse", "vars", "sim", "warnings", "edits", "detail")
ORDER = (*VISIBLE, "ref", "resolution", "translate", "total")
#: What a filter says beyond its kind's hint (``rows.FILTER_HINTS``, the
#: dashboard's words): the Test column shows short names and filters the
#: full one.
FILTER_NOTE = {"model": "Reads the full test name. "}
#: The count pills, ``[key, word]`` in the order the toolbar draws them: a
#: status by its class (a row's ``status_class``), EDITED (a row with
#: staged edits, whatever its status) and RUNNING (drawn only while a job
#: runs or it filters). The words are ``reporting.status``'s: the script
#: reads this table (``window.DstfSuitePills``) and spells none of them.
PILLS: tuple[tuple[str, str], ...] = (
    *(
        (STATUS_CLASSES[s], STATUS_LABELS[s])
        for s in (PASS, FAIL, SIM_FAIL, NO_REF, NOT_RUN)
    ),
    ("edited", "EDITED"),
    (STATUS_CLASSES[RUNNING], STATUS_LABELS[RUNNING]),
)


def row_json(row: TestRow) -> dict[str, Any]:
    """One row as the grid holds it: every column's text and sort value
    (a number filter reads the sort value, never the text), the package it
    groups under and its two names."""
    package, _, short = row.model_id.rpartition(".")
    data: dict[str, Any] = {
        "id": row.model_id,
        "_pkg": package,
        "_name_short": short,
        "_name_full": row.model_id,
        "status_class": row.status_class,
    }
    for c in TOOL_COLUMNS:
        data[c.key] = c.text(row)
        data[f"{c.key}__sort"] = c.sort(row)
        # A number of another kind (Sim (s)'s CPU time): its tag and tooltip.
        for part, get in (("tag", c.tag), ("tip", c.tip)):
            value = get(row) if get is not None else None
            if value is not None:
                data[f"{c.key}__{part}"] = value
    return data


def _filter_params(c: Column) -> dict[str, str]:
    """A column's filter input: its kind, and the dashboard's placeholder
    and tooltip for that kind (the status column's cell mirrors the pills,
    and has neither)."""
    if c.kind == "status":
        return {"kind": c.kind}
    placeholder, title = FILTER_HINTS[c.kind]
    return {
        "kind": c.kind,
        "placeholder": placeholder,
        "help": FILTER_NOTE.get(c.key, "") + title,
    }


def _column_defs() -> list[dict[str, Any]]:
    by_key = {c.key: c for c in TOOL_COLUMNS}
    defs: list[dict[str, Any]] = []
    for key in ORDER:
        c = by_key[key]
        d: dict[str, Any] = {
            "field": c.key,
            "colId": c.key,
            "headerName": c.label,
            "hide": c.key not in VISIBLE,
            ":comparator": f"DstfSuite.comparator({json.dumps(c.key)})",
            "floatingFilter": True,
            ":filter": "DstfSuite.NoFilter",
            ":floatingFilterComponent": "DstfSuite.FloatingInput",
            "floatingFilterComponentParams": _filter_params(c),
            "suppressFloatingFilterButton": True,
            "suppressHeaderFilterButton": True,
            "suppressHeaderMenuButton": True,
            # Narrow enough that Edits is in view at 900 px (Message after
            # it takes the width left over, and may be cut).
            "width": 90,
        }
        if c.title:
            d["headerTooltip"] = c.title
        if c.kind == "number":
            d["type"] = "rightAligned"
            d["cellClass"] = "dstf-num"
        if c.tag is not None:
            d[":cellRenderer"] = "DstfSuite.cell.tagged"
            d[":tooltipValueGetter"] = (
                f"(p) => p.data && p.data[{json.dumps(c.key + '__tip')}]"
            )
        if key == "status":
            d[":cellRenderer"] = "DstfSuite.cell.status"
            d["width"] = 100  # "SIM_FAIL" in its badge and the sort arrow
        elif key == "model":
            d[":cellRenderer"] = "DstfSuite.cell.model"
            # Twice Message's share: 3 of TRANSFORM's 5 longest short names
            # were cut at 1280 px with an equal share; at twice it the
            # longest (47 characters) reads whole in 359 px. The minimum
            # holds Edits in view at 900 px: every column before it is at
            # its width there, and 180 put Edits 21 px past the viewport.
            d.update(flex=2, minWidth=150)
            del d["width"]
            # A name cut at the cell's edge reads in full on hover — the
            # whole cell's tooltip, not only its link's.
            d[":tooltipValueGetter"] = "(p) => p.data && p.data.id"
        elif key == "nrmse":
            # "1.5309e-01" and its header: 110 px cut both at 1280.
            d["width"] = 135
        elif key == "detail":
            d.update(flex=1, minWidth=160)
            del d["width"]
            # A message cut at the cell's edge reads in full on hover; a
            # row with none ("—") has no tooltip.
            d[":tooltipValueGetter"] = (
                "(p) => p.data && p.data.detail__sort != null ? p.value : null"
            )
        elif key == "edits":
            d[":cellRenderer"] = "DstfSuite.cell.edits"
            d["width"] = 120  # "1 unsaved" in its tag: 100 cut it
        defs.append(d)
    return defs


class SuiteGrid(ui.aggrid):
    """The grid element. The page calls `seal()` after its last build-time
    call on it (`.on`, `.classes`, `.mark`): from then on `update()` raises.

    `update()` is the only way an element change reaches the browser —
    NiceGUI's first render enqueues the element straight from
    `Element.__init__` (`outbox.enqueue_update`), never through it — so the
    guard sits there. `.classes` / `.props` / `.style` reach it through an
    observable whose handler LOGS the exception (`events.handle_event`):
    the update is blocked, but such a caller sees no raise, only the log."""

    def __init__(
        self,
        rows: Sequence[TestRow],
        *,
        selected: Iterable[str],
        on_selection: Callable[[list[str]], None],
    ) -> None:
        kinds = {c.key: c.kind for c in TOOL_COLUMNS}
        # Before NiceGUI's __init__: it writes props, and a prop write
        # comes through `update` below.
        self._sealed = False
        super().__init__(
            {
                "columnDefs": _column_defs(),
                "rowData": [row_json(r) for r in rows],
                ":getRowId": "(p) => p.data.id",
                "defaultColDef": {"sortable": True, "resizable": True},
                "rowHeight": 30,
                "headerHeight": 32,
                "floatingFiltersHeight": 32,
                "tooltipShowDelay": 300,
                ":isFullWidthRow": "(p) => !!p.rowNode.data?._g",
                ":fullWidthCellRenderer": "DstfSuite.GroupRow",
                ":getRowClass": "DstfSuite.rowClass",
                "rowSelection": {
                    "mode": "multiRow",
                    "headerCheckbox": True,
                    "selectAll": "filtered",
                    "enableClickSelection": False,
                    ":isRowSelectable": "(n) => !n.data?._g",
                },
                ":isExternalFilterPresent": "() => true",
                ":doesExternalFilterPass": "DstfSuite.externalPass",
                ":postSortRows": "DstfSuite.postSort",
                ":onCellKeyDown": "DstfSuite.keyDown",
                ":onSortChanged": "() => DstfSuite.saveState()",
                ":onColumnVisible": "() => DstfSuite.saveState()",
                ":onSelectionChanged": "() => DstfSuite.renderSelection()",
                "animateRows": False,
                # The tab's selection, selected once the grid is ready.
                ":onGridReady": (
                    f"(p) => DstfSuite.ready(p, {json.dumps(kinds)}, "
                    f"{json.dumps(sorted(selected))})"
                ),
            },
            auto_size_columns=False,
        )
        self.classes("dstf-suite-grid w-full")
        self.on(
            "selectionChanged",
            lambda e: on_selection(list(e.args or [])),
            js_handler=(
                f"() => emit(getElement({self.id}).api.getSelectedRows()"
                ".filter(r => !r._g).map(r => r.id))"
            ),
        )

    def seal(self) -> None:
        self._sealed = True

    def transaction(
        self,
        *,
        add: Sequence[TestRow] = (),
        update: Sequence[TestRow] = (),
        remove: Iterable[str] = (),
        select: Iterable[str] = (),
    ) -> None:
        """One AG Grid transaction, through the table's script (which then
        regroups and recounts): rows to add, rows to repaint, ids to drop,
        and the added ids the tab has selected."""
        tx = {
            "add": [row_json(r) for r in add],
            "update": [row_json(r) for r in update],
            "remove": list(remove),
            "select": list(select),
        }
        self.client.run_javascript(f"DstfSuite.apply({json.dumps(tx)})")

    def update(self) -> None:
        if self._sealed:
            raise RuntimeError(
                "the Suite grid is driven by run_grid_method; an element "
                "update rebuilds it"
            )
        super().update()


def scripts_html(key: str) -> str:
    """The head of the Suite page: the table's localStorage key and its
    count pills (`PILLS`), then the typed filters and the table's script.
    It must be in the page's FIRST HTML: after the handshake NiceGUI
    inserts head HTML with insertAdjacentHTML, which never runs a
    <script>."""

    # "</" would end the inline script early; JSON may escape it.
    def literal(value: object) -> str:
        return json.dumps(value).replace("</", "<\\/")

    return (
        f"<script>window.DstfSuiteKey = {literal(key)};\n"
        f"window.DstfSuitePills = {literal(PILLS)};</script>\n"
        f'<script src="{ASSETS}/filters.js"></script>\n'
        f'<script src="{ASSETS}/suite_grid.js"></script>\n'
    )


GRID_CSS = """
.dstf-suite-grid { height: calc(100vh - 190px); min-height: 320px;
  --ag-font-family: var(--dstf-font); --ag-font-size: 13px;
  --ag-header-background-color: var(--dstf-surface-hover);
  --ag-border-color: var(--dstf-border); --ag-row-border-color: var(--dstf-border-hairline);
  --ag-accent-color: var(--dstf-accent); --ag-selected-row-background-color: var(--dstf-selection);
  --ag-wrapper-border-radius: 4px; }
/* A dark theme (D148): NiceGUI turns the grid's own colour scheme with
   `body--dark`, and AG Grid's dark scheme is blue-tinted with white text.
   The grid's ground, text and controls are the theme's instead. A light
   theme keeps AG Grid's light scheme as it was. */
.body--dark .dstf-suite-grid {
  --ag-background-color: var(--dstf-surface); --ag-foreground-color: var(--dstf-text);
  --ag-text-color: var(--dstf-text); --ag-header-text-color: var(--dstf-text);
  --ag-subtle-text-color: var(--dstf-text-muted);
  --ag-odd-row-background-color: var(--dstf-surface);
  --ag-row-hover-color: var(--dstf-surface-hover);
  --ag-chrome-background-color: var(--dstf-surface-hover);
  --ag-header-column-resize-handle-color: var(--dstf-border);
  --ag-input-background-color: var(--dstf-surface); --ag-input-border-color: var(--dstf-border);
  --ag-checkbox-unchecked-border-color: var(--dstf-text-muted);
  --ag-tooltip-background-color: var(--dstf-surface-active); }
.body--dark .dstf-ff { background: var(--dstf-surface); color: var(--dstf-text); }
.dstf-suite-grid .dstf-num { font-family: var(--dstf-mono); }
.dstf-grp { display: flex; align-items: center; gap: 6px; height: 100%; padding: 0 8px;
  font-weight: 600; background: var(--dstf-surface-hover); cursor: pointer; }
.dstf-grp .material-icons { font-size: 18px; }
.dstf-grp-count { font-weight: 400; color: var(--dstf-text-muted); margin-left: 8px; }
.dstf-badge-cell { color: #fff; font-size: 11px; font-weight: 700; padding: 1px 6px;
  border-radius: 3px; }
.dstf-edits { font-size: 11px; padding: 0 6px; border-radius: 3px;
  border: 1px solid var(--dstf-warn-border); background: var(--dstf-warn-pill);
  color: var(--dstf-warn-dark); }
.dstf-ff { width: 100%; font-size: 12px; border: 1px solid var(--dstf-border);
  border-radius: 2px; padding: 1px 4px; }
.dstf-ff.bad { border-color: var(--dstf-fail-fill); outline: 1px solid var(--dstf-fail-fill); }
.dstf-ff-status { font-size: 11px; color: var(--dstf-text-muted); }
.dstf-cell-tag { font-size: 11px; color: var(--dstf-text-muted); margin-left: 3px; }
/* The pills and the view icons wrap at a narrow width; the selection and
   the actions at the right keep their line. */
#dstf-suite-tools { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 12px;
  flex: 1 1 auto; min-width: 0; }
#dstf-suite-selection { display: inline-flex; align-items: center; gap: 4px; font-size: 13px;
  white-space: nowrap; flex: none; }
.dstf-suite-toolbar > .dstf-actions { flex-wrap: nowrap; flex: none; }
.dstf-pills, .dstf-views { display: inline-flex; flex-wrap: wrap; gap: 4px; align-items: center; }
.dstf-tb { border: 1px solid var(--dstf-border); background: var(--dstf-surface);
  color: var(--dstf-text); border-radius: 4px; min-height: 28px; padding: 0 6px;
  cursor: pointer; display: inline-flex; align-items: center; }
.dstf-tb .material-icons { font-size: 18px; }
.dstf-tb.on { outline: 2px solid var(--dstf-accent); }
.dstf-tb:disabled { opacity: .5; cursor: default; }
.dstf-tb-txt { font-size: 12px; font-family: var(--dstf-mono); }
/* A tooltip reads a whole test id or message: an id has no space to wrap
   at, and at 900 px a 149-character one ran 250 px past the page's edge. */
.ag-tooltip { max-width: min(90vw, 720px); white-space: normal; overflow-wrap: anywhere; }
#dstf-colmenu { position: absolute; z-index: 9000; background: var(--dstf-surface);
  border: 1px solid var(--dstf-border); border-radius: 4px; padding: 6px 10px;
  display: flex; flex-direction: column; gap: 2px; font-size: 13px;
  box-shadow: 0 2px 8px rgba(0,0,0,.15); }
"""
