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

from ...reporting.rows import COLUMNS, EDITS, Column, TestRow

HERE = Path(__file__).parent
#: The typed filters, one module for the dashboard and this table.
FILTERS_JS = HERE.parent.parent / "reporting" / "templates" / "filters.js"
#: Where the shell serves the two scripts (`Shell.mount`).
ASSETS = "/_dstf"
#: A per-viewer key for the table's view (localStorage), per project.
STORAGE = "dstf-suite-v1"

TOOL_COLUMNS: tuple[Column, ...] = (*COLUMNS, EDITS)
#: Shown by default: the verdict, the test, its numbers, its message and its
#: edits. Key, Worker and Elapsed are a live run's (the runs card shows a
#: running job); they and the rest are in the column chooser.
VISIBLE = ("status", "model", "nrmse", "vars", "sim", "warnings", "detail", "edits")
ORDER = (
    *VISIBLE,
    "test",
    "ref",
    "resolution",
    "worker",
    "elapsed",
    "translate",
    "total",
)
#: A text filter's tooltip; the Test column reads the full test name.
TEXT_HELP = "text, *glob*, or a regex with .*"
FILTER_HELP = {
    "model": "the full test name: " + TEXT_HELP,
    "number": ">1e-4, <=3, 1..10",
}


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
    return data


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
            "floatingFilterComponentParams": {
                "kind": c.kind,
                "help": FILTER_HELP.get(key) or FILTER_HELP.get(c.kind, TEXT_HELP),
            },
            "suppressFloatingFilterButton": True,
            "suppressHeaderFilterButton": True,
            "suppressHeaderMenuButton": True,
            "width": 110,
        }
        if c.title:
            d["headerTooltip"] = c.title
        if c.kind == "number":
            d["type"] = "rightAligned"
            d["cellClass"] = "dstf-num"
        if key == "status":
            d[":cellRenderer"] = "DstfSuite.cell.status"
        elif key == "model":
            d[":cellRenderer"] = "DstfSuite.cell.model"
            d.update(flex=3, minWidth=160)
            del d["width"]
        elif key == "nrmse":
            # "1.5309e-01" and its header: 110 px cut both at 1280.
            d["width"] = 135
        elif key == "detail":
            d.update(flex=2, minWidth=120)
            del d["width"]
        elif key == "edits":
            d[":cellRenderer"] = "DstfSuite.cell.edits"
            d["width"] = 100
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

    def update(self) -> None:
        if self._sealed:
            raise RuntimeError(
                "the Suite grid is driven by run_grid_method; an element "
                "update rebuilds it"
            )
        super().update()


def scripts_html(key: str) -> str:
    """The head of the Suite page: the table's localStorage key, then the
    typed filters and the table's script. It must be in the page's FIRST
    HTML: after the handshake NiceGUI inserts head HTML with
    insertAdjacentHTML, which never runs a <script>."""
    # "</" would end the inline script early; JSON may escape it.
    literal = json.dumps(key).replace("</", "<\\/")
    return (
        f"<script>window.DstfSuiteKey = {literal};</script>\n"
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
#dstf-suite-tools { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 12px; }
#dstf-suite-selection { display: inline-flex; align-items: center; gap: 4px; font-size: 13px; }
.dstf-pills, .dstf-views { display: inline-flex; flex-wrap: wrap; gap: 4px; align-items: center; }
.dstf-tb { border: 1px solid var(--dstf-border); background: var(--dstf-surface);
  color: var(--dstf-text); border-radius: 4px; min-height: 28px; padding: 0 6px;
  cursor: pointer; display: inline-flex; align-items: center; }
.dstf-tb .material-icons { font-size: 18px; }
.dstf-tb.on { outline: 2px solid var(--dstf-accent); }
.dstf-tb:disabled { opacity: .5; cursor: default; }
.dstf-tb-txt { font-size: 12px; font-family: var(--dstf-mono); }
#dstf-colmenu { position: absolute; z-index: 9000; background: var(--dstf-surface);
  border: 1px solid var(--dstf-border); border-radius: 4px; padding: 6px 10px;
  display: flex; flex-direction: column; gap: 2px; font-size: 13px;
  box-shadow: 0 2px 8px rgba(0,0,0,.15); }
"""
