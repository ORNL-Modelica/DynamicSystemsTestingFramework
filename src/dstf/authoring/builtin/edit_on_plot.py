"""Edit on plot (§4.6 table, §4.7): the CRITERION placement — the open
criterion's handles drag on the plot its row sits under, and the op targets
that leaf; a handle of any other leaf is refused by name. In the editor, a
table per handle-bearing list field of that leaf (the Config fields that
declare ``columns``: ``tube_points``, ``points``, ``events``, ``peaks``)
with add and remove rows.

Every staged edit is one ``replace`` of the whole leaf — the op Edit
criteria stages — so consecutive drags on one leaf collapse into one
pending op, and a drag and a form edit of the same leaf read as "leaf ->
new leaf" in the drawer.
"""

from __future__ import annotations

import copy
import dataclasses
import math
from typing import TYPE_CHECKING, Any

from ...comparison.geometry import HLine, Marker
from ...comparison.handles import HandleEdit, HandleEditError
from ...comparison.registry import DEFAULT_METRIC, metric
from ...figures.assemble import leaves_by_variable
from ..capabilities import Panel, Patch, Placement
from ..session import no_tree_error
from ..spec_document import PatchOp
from ..tree_pointer import node_at

if TYPE_CHECKING:
    from ..session import SessionView

# NO module-level `nicegui` import: `import dstf.authoring` runs
# register_builtins(), and the Session must import without the extra.

Columns = tuple[tuple[str, Any], ...]


def _selected(s: SessionView) -> tuple[str, str, str] | None:
    """``(model_id, variable, leaf_path)`` for exactly one test, a variable
    and the open criterion."""
    sel = s.selection
    if len(sel.tests) != 1 or sel.variable is None or sel.leaf_path is None:
        return None
    (model_id,) = tuple(sel.tests)
    return model_id, sel.variable, sel.leaf_path


def _leaf_paths(s: SessionView, model_id: str, variable: str) -> list[tuple[str, str]]:
    """``(leaf_path, metric)`` for every scored leaf on ``variable``, in tree
    order. Cheap: no figure, no view."""
    comp = s.comparisons.get(model_id)
    tree = comp.metric_tree if comp is not None else None
    return [(path, vc.mode) for path, vc in leaves_by_variable(tree).get(variable, [])]


def _list_fields(name: str) -> list[tuple[str, Columns]]:
    """The Config list fields of metric ``name`` that declare their row
    ``columns`` (Task 6's metadata), read from the dataclass fields — not
    through ``derive_schema``, whose ``FieldSpec`` does not carry them."""
    desc = metric(name)
    if desc is None or not dataclasses.is_dataclass(desc.config_cls):
        return []
    return [
        (f.name, f.metadata["columns"])
        for f in dataclasses.fields(desc.config_cls)
        if "columns" in f.metadata
    ]


def _field_label(name: str, field: str) -> str:
    """One table's heading: the Config field's own label, else its name. It
    said `{path} · {metric} · {field}` — every word of which the criterion's
    row says directly above the editor (the Task 6 screenshot)."""
    desc = metric(name)
    if desc is None or not dataclasses.is_dataclass(desc.config_cls):
        return field
    for f in dataclasses.fields(desc.config_cls):
        if f.name == field:
            return str(f.metadata.get("label") or field)
    return field


def _leaf_at(s: SessionView, model_id: str, path: str) -> dict:
    """The raw leaf at ``path`` of ``model_id``'s tree, or a ValueError
    naming what ``path`` or ``model_id`` is instead — the Session's refusal
    (`no_tree_error`) where it has no tree."""
    try:
        tree, why = s.tree_or_reason(model_id)
    except KeyError:
        raise ValueError(f"{model_id} is not a test of this project") from None
    if tree is None:
        assert why is not None  # `tree` is None
        raise no_tree_error(model_id, why)
    tokens = [t for t in path.split("/") if t]
    node: Any = None
    if tokens[:1] == ["metrics"]:
        try:
            node = node_at(tree, path)
        except (KeyError, IndexError, TypeError, ValueError):
            node = None
    if not isinstance(node, dict):
        raise ValueError(f"{path} names no leaf of {model_id}")
    if "combinator" in node:
        raise ValueError(f"{path} is a combinator, not a leaf")
    return node


def _has_handles(name: str) -> bool:
    desc = metric(name)
    return desc is not None and (
        desc.edit_handle is not None or bool(_list_fields(name))
    )


def _leaf_path_of(handle: str, paths: list[str]) -> str | None:
    """The longest leaf path ``handle`` starts with FOLLOWED BY ``/``: a
    handle of ``/metrics/children/10`` never resolves to
    ``/metrics/children/1``."""
    matches = [p for p in paths if handle.startswith(p + "/")]
    return max(matches, key=len) if matches else None


def _position(
    primitive: Marker | HLine, shape: dict
) -> tuple[float | None, float | None] | None:
    """The dragged position the hook's per-shape payload means for the
    handle's PRIMITIVE TYPE, or None when it is no move. A Marker's ring
    moves only by ``xanchor``/``yanchor`` — its ``x0``..``y1`` are a pixel
    RESIZE (an off-centre grab), never a position. An HLine moves by
    ``y0``/``y1``: both when the whole line was dragged, the one present
    when one end was, neither (a horizontal-only drag) is no move. Any
    other primitive never gets here: `_act_handle` snaps it back."""
    if isinstance(primitive, Marker):
        x, y = shape.get("xanchor"), shape.get("yanchor")
        if x is None and y is None:
            return None
        return x, y
    y0, y1 = shape.get("y0"), shape.get("y1")
    if y0 is None and y1 is None:
        return None
    return None, (y0 if y0 is not None else y1)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _new_time(rows: list[dict], span: tuple[float, float] | None) -> float:
    """Where ``Add row`` puts a new row's ``time``: never on an existing one
    (two tube points sharing a time are refused on the plot, and the later
    one wins the interpolation). The midpoint of the LARGEST gap — the first
    such — among the span's start, the rows' numeric times and the span's
    end, sorted; with no reference, among the rows' times alone; with no
    reference and fewer than two timed rows, the last row's time ``+ 1.0``
    (``0.0`` on a table with none)."""
    times = [float(r["time"]) for r in rows if _is_number(r.get("time"))]
    if span is not None:
        marks = sorted([span[0], *times, span[1]])
    elif len(times) >= 2:
        marks = sorted(times)
    else:
        return times[-1] + 1.0 if times else 0.0
    gaps = [(b - a, (a + b) / 2) for a, b in zip(marks, marks[1:])]
    width = max(gap for gap, _ in gaps)
    return next(mid for gap, mid in gaps if gap == width)


def _new_row(
    columns: Columns, rows: list[dict], span: tuple[float, float] | None
) -> dict:
    """The row ``Add row`` appends; its ``time`` (when the table has one) is
    :func:`_new_time`'s. After existing rows: a copy of the last one. On an
    empty table: any other required float ``0.0``; any other optional float
    blank, so omitted (a new checkpoint follows the baseline); a choice its
    first. The one exception: a ``tube_points`` row gets ``upper`` and
    ``lower`` ``0.0`` — optional in the reader, which refuses a tube with no
    width at all."""
    keys = [key for key, _ in columns]
    if rows:
        last = copy.deepcopy(rows[-1])
        if "time" in keys:
            last["time"] = _new_time(rows, span)
        return last
    row: dict[str, Any] = {}
    for key, kind in columns:
        if key == "time":
            row[key] = _new_time(rows, span)
        elif isinstance(kind, tuple):
            row[key] = kind[0]
        elif kind == "float":
            row[key] = 0.0
    if keys == ["time", "upper", "lower"]:  # tube_points
        row.update(upper=0.0, lower=0.0)
    return row


_OUTSIDE = "/outside"


def _handle_id(shape_name: str) -> str:
    """The primitive a dragged shape stands for: a bound's dotted
    continuation outside its window (`_hline`'s ``<id>/outside``, full
    width, so it also covers the bound inside the window) is the bound."""
    return shape_name[: -len(_OUTSIDE)] if shape_name.endswith(_OUTSIDE) else shape_name


class EditOnPlot:
    """Drag a handle on the plot, or edit its table; one replace of the leaf
    per edit."""

    name = "edit-on-plot"
    label = "Edit on plot"
    placements = frozenset({Placement.CRITERION})

    def caption(self, s: SessionView) -> str:
        return self.label

    def available(self, s: SessionView) -> bool | str:
        selected = _selected(s)
        if selected is None:
            return "open one criterion"
        model_id, variable, leaf_path = selected
        running = s.running
        if running is not None and s.is_locked(model_id):
            return f"{running.label} is running on {model_id}"
        # The handles are drawn from the comparison's tree; every edit of
        # one goes through the Session's, which may have none.
        tree, why = s.tree_or_reason(model_id)
        if tree is None:
            assert why is not None  # `tree` is None
            return why
        metric_name = dict(_leaf_paths(s, model_id, variable)).get(leaf_path)
        if metric_name is None:
            return f"{leaf_path} is not a criterion on {variable}"
        if not _has_handles(metric_name):
            return f"{metric_name} has no on-plot handles"
        return True

    def panel(self, s: SessionView, ui_panel: Panel) -> None:
        from nicegui import ui

        selected = _selected(s)
        assert selected is not None  # `available` said so
        model_id, variable, leaf_path = selected

        def on_relayout(handle: str, payload: dict) -> None:
            if handle:
                ui_panel.submit(
                    kind="handle",
                    model_id=model_id,
                    variable=variable,
                    handle=handle,
                    shape=payload,
                )

        ui_panel.figure.on("plotly_relayout", on_relayout)
        ui.label("drag a ring on the plot, or edit the table").classes("text-xs")
        tree = s.tree(model_id)
        if tree is None:
            return

        def span() -> tuple[float, float] | None:
            view = s.view(model_id, variable)
            if view.ref_time is None or len(view.ref_time) == 0:
                return None
            return float(view.ref_time[0]), float(view.ref_time[-1])

        for path, _mode in _leaf_paths(s, model_id, variable):
            if path != leaf_path:  # the editor is the open criterion's
                continue
            raw = node_at(tree, path)
            name = raw.get("metric", DEFAULT_METRIC)
            for field, columns in _list_fields(name):
                ui.label(_field_label(name, field)).classes("text-xs text-bold")
                rows = copy.deepcopy(raw.get(field) or [])
                _table(ui, path, field, columns, rows, span, ui_panel, model_id)

    def act(self, s: SessionView, **inputs: Any) -> Patch | None:
        kind = inputs["kind"]
        if kind == "handle":
            return self._act_handle(s, **inputs)
        if kind == "rows":
            return self._act_rows(s, **inputs)
        raise ValueError(f"unknown kind {kind!r}")

    def _act_handle(
        self,
        s: SessionView,
        *,
        model_id: str,
        variable: str,
        handle: str,
        shape: dict,
        **_: Any,
    ) -> Patch | None:
        if not handle:
            return None
        handle = _handle_id(handle)
        paths = [p for p, _ in _leaf_paths(s, model_id, variable)]
        path = _leaf_path_of(handle, paths)
        if path is None:
            raise ValueError(f"{handle} names no leaf on {variable} of {model_id}")
        view = s.view(model_id, variable)  # full resolution, not the figure
        leaf = next((lv for lv in view.leaves if lv.path == path), None)
        primitive = next(
            (p for p in (leaf.geometry if leaf else ()) if p.id == handle), None
        )
        # By TYPE, not only by kind: a box (`points/box/i`, `peaks/window/i`)
        # is `kind="handle"` too, and no editor moves one. A window, a box, a
        # result line is drawn, not editable; Plotly lets it be dragged all
        # the same, and the figure hook's redraw snaps it back. Resolved
        # before the open-leaf check: whichever criterion is open, such a
        # drag is not an error the user made.
        if not isinstance(primitive, (Marker, HLine)) or primitive.kind != "handle":
            return None
        open_leaf = s.selection.leaf_path
        if open_leaf is not None and path != open_leaf:
            # Only the open leaf's rings are drawn (`Session.figure`'s
            # `editing`), but every leaf's bound LINES are on the plot and
            # Plotly lets them be dragged: one of those is not this
            # editor's.
            raise HandleEditError(
                f"{handle} is not a handle of {open_leaf}",
                reason="Open that criterion to drag its lines.",
            )
        tree, why = s.tree_or_reason(model_id)
        if tree is None:
            assert why is not None  # `tree` is None
            raise no_tree_error(model_id, why)
        raw = node_at(tree, path)
        name = raw.get("metric", DEFAULT_METRIC)
        desc = metric(name)
        if desc is None or desc.edit_handle is None:
            raise HandleEditError(
                f"{path}: {name} has no on-plot handles",
                reason=f"{name} has no on-plot handles",
            )
        # Several leaves on one variable can refuse the same local id: every
        # refusal below names its leaf.
        try:
            position = _position(primitive, shape if isinstance(shape, dict) else {})
            if position is None:
                return None
            x, y = position
            new = desc.edit_handle(
                HandleEdit(
                    leaf=raw,
                    local=handle[len(path) + 1 :],
                    x=x,
                    y=y,
                    reference=(
                        (view.ref_time, view.ref_values)
                        if view.ref_time is not None and view.ref_values is not None
                        else None
                    ),
                )
            )
        except HandleEditError as e:
            raise HandleEditError(f"{path}: {e}", reason=e.reason) from None
        if new == raw:
            return None
        return Patch(ops=[PatchOp(model_id, "replace", path, new)])

    def _act_rows(
        self,
        s: SessionView,
        *,
        model_id: str,
        path: str,
        field: str,
        rows: list[dict],
        **_: Any,
    ) -> Patch | None:
        raw = _leaf_at(s, model_id, path)
        name = raw.get("metric", DEFAULT_METRIC)
        columns = dict(_list_fields(name)).get(field)
        if columns is None:
            raise ValueError(f"{name} has no table {field!r}")
        required = [key for key, kind in columns if kind == "float"]
        cleaned: list[dict] = []
        for i, row in enumerate(rows):
            for key in required:
                if row.get(key) is None:
                    raise ValueError(f"row {i}: {key} is required")
            for key, value in row.items():
                if _is_number(value) and not math.isfinite(value):
                    raise ValueError(
                        f"row {i}: {key} = {value!r} is not a finite number"
                    )
            cleaned.append({k: v for k, v in row.items() if v is not None})
        # The reader's own refusal (`algorithms._interpolate_tube_widths`),
        # raised here by name rather than as the rescore's config error.
        is_tube = [key for key, _ in columns] == ["time", "upper", "lower"]
        if (
            is_tube
            and cleaned
            and not any("upper" in r or "lower" in r for r in cleaned)
        ):
            raise ValueError(f"{field}: no row has an upper or a lower width")
        new = copy.deepcopy(raw)
        new[field] = cleaned
        if new == raw:
            return None
        return Patch(ops=[PatchOp(model_id, "replace", path, new)])


def _table(
    ui: Any,
    path: str,
    field: str,
    columns: Columns,
    rows: list[dict],
    span: Any,
    ui_panel: Panel,
    model_id: str,
) -> None:
    """One list field's rows, a control per column, ``Remove row`` per row
    and ``Add row``. The rows live here: neither a refused submit nor a
    successful one rebuilds this panel — ``InlineHost`` shows a refusal above
    it and leaves it alone, so that a control being typed in survives — so
    the table re-renders its own container after adding or removing a row.
    Only the ROWS scroll: ``Add row`` sits after their container, where a
    long table cannot scroll it out of reach.

    A number cell reports every keystroke, so clearing a REQUIRED cell to
    retype it passes through blank. Submitted, that blank is refused and the
    error stands above a table whose cell says nothing; so while any required
    cell is blank the table keeps its rows here, marks the blank cells and
    submits nothing — the keystroke that fills the last one submits the
    whole list. (``act`` still refuses a blank, for a direct call.)"""
    container = ui.column().classes("w-full")
    mark = f"{path}:{field}"
    required = [key for key, kind in columns if kind == "float"]
    cells: dict[tuple[int, str], Any] = {}  # the required cells, as rendered

    def blanks() -> bool:
        """Mark each required cell blank or not; True when any is."""
        found = False
        for (i, key), cell in cells.items():
            if rows[i].get(key) is None:
                cell.props(f'error error-message="{key} is required"')
                found = True
            else:
                cell.props(remove="error error-message")
        return found

    def submit() -> None:
        if blanks():
            return
        ui_panel.submit(
            kind="rows",
            model_id=model_id,
            path=path,
            field=field,
            rows=copy.deepcopy(rows),
        )

    def set_cell(i: int, key: str, value: Any) -> None:
        rows[i][key] = value
        submit()

    def remove(i: int) -> None:
        rows.pop(i)
        render()
        submit()

    def add() -> None:
        rows.append(_new_row(columns, rows, span()))
        render()
        submit()

    def render() -> None:
        container.clear()
        cells.clear()
        with container:
            # The rows alone scroll; `Add row` follows their container.
            scroll = ui.column().classes("w-full dstf-table-scroll")
            for i, row in enumerate(rows):
                with scroll, ui.row().classes("items-center no-wrap"):
                    for key, kind in columns:
                        if isinstance(kind, tuple):
                            value = row.get(key, kind[0])
                            # A value the author wrote outside the choices
                            # stays selectable rather than break the build.
                            options = list(kind) + ([] if value in kind else [value])
                            ui.select(
                                options,
                                label=key,
                                value=value,
                                on_change=lambda e, i=i, key=key: set_cell(
                                    i, key, e.value
                                ),
                            ).props("dense").mark(f"{mark}:{i}:{key}")
                        else:
                            cell = (
                                ui.number(
                                    label=key,
                                    value=row.get(key),
                                    on_change=lambda e, i=i, key=key: set_cell(
                                        i, key, e.value
                                    ),
                                )
                                .props("dense")
                                .mark(f"{mark}:{i}:{key}")
                            )
                            if key in required:
                                cells[(i, key)] = cell
                    ui.button("Remove row", on_click=lambda i=i: remove(i)).props(
                        "flat dense no-caps"
                    ).mark(f"{mark}:{i}:remove")
            ui.button("Add row", on_click=add).props("flat dense no-caps").mark(
                f"{mark}:add"
            )

    render()
