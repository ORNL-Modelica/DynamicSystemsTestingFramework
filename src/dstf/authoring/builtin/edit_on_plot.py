"""Edit on plot (§4.6 table, §4.7): the CRITERION placement — the open
criterion's handles, and the grips on each side of its tolerances, drag on
the plot its row sits under, and the op targets that leaf; a handle of any
other leaf is refused by name. In the editor, a
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
from functools import partial
from typing import TYPE_CHECKING, Any

from ...comparison.geometry import HLine, Marker, VLine, grip_axis
from ...comparison.handles import HandleEdit, HandleEditError
from ...comparison.mode_schema import SIDE_LABELS
from ...comparison.registry import DEFAULT_METRIC, metric
from ...comparison.sides import parse_sides
from ...figures.assemble import leaves_by_variable
from ..capabilities import Panel, Patch, Placement
from ..session import no_tree_error
from ..spec_document import PatchOp
from ..tree_pointer import node_at
from ..unscorable import refuse_negative

if TYPE_CHECKING:
    from ..session import SessionView

# NO module-level `nicegui` import: `import dstf.authoring` runs
# register_builtins(), and the Session must import without the extra.

#: A list field's ``columns`` metadata: per row key its kind — ``"float"``,
#: ``"float?"``, ``"sides"``, ``"sides?"`` or a tuple of choices — and, for a
#: ``sides`` kind, optionally what its two sides are called. Read by INDEX:
#: an entry is two or three long.
Columns = tuple[tuple[Any, ...], ...]


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


def _draggable(primitive: object) -> bool:
    """Whether a drag of ``primitive`` is an edit: a handle's ring or line,
    or a grip's arrow. By TYPE as well as by kind: a box (`points/box/i`,
    `peaks/window/i`) is `kind="handle"` too, and no editor moves one."""
    if isinstance(primitive, (Marker, HLine)) and primitive.kind == "handle":
        return True
    return isinstance(primitive, (Marker, VLine)) and primitive.kind == "grip"


def _grip_position(
    grip: Marker | VLine, shape: dict
) -> tuple[float | None, float | None] | None:
    """Where a dragged grip was released: its OWN axis from the payload,
    the other from the grip itself. A release carries both anchors, and the
    one off the grip's axis is not where the pointer went — Plotly re-snaps
    it, and past 8 px of sideways drift moves it in full (Chromium,
    2026-10-02); an event's or a peak's arrow is anchored to the paper in y,
    which a diagonal drag moves too. None when its own anchor is absent."""
    x = grip.time if isinstance(grip, VLine) else grip.x
    y = None if isinstance(grip, VLine) else grip.y
    if grip_axis(grip) == "x":
        moved = shape.get("xanchor")
        return None if moved is None else (moved, y)
    moved = shape.get("yanchor")
    return None if moved is None else (x, moved)


def _position(
    primitive: Marker | HLine | VLine, shape: dict
) -> tuple[float | None, float | None] | None:
    """The dragged position the hook's per-shape payload means for the
    handle's PRIMITIVE TYPE, or None when it is no move. A grip moves on
    one axis (:func:`_grip_position`). A Marker's ring
    moves only by ``xanchor``/``yanchor`` — the ``x0``..``y1`` of a
    pixel-sized shape are offsets about its anchor, never a position (a
    ring is a pixel ``path``, which sends none). An HLine moves by
    ``y0``/``y1``: both when the whole line was dragged, the one present
    when one end was, neither (a horizontal-only drag) is no move. Any
    other primitive never gets here: `_act_handle` snaps it back."""
    if primitive.kind == "grip":
        assert isinstance(primitive, (Marker, VLine))  # `_draggable`
        return _grip_position(primitive, shape)
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
    :func:`_new_time`'s. After existing rows: a copy of the last one — a
    pair it holds copied too, never shared. On an empty table: any other
    required float or tolerance ``0.0``; any other optional one blank, so
    omitted (a new checkpoint follows the baseline, and the criterion's
    tolerance); a choice its first. The one exception: a ``tube_points``
    row gets ``upper`` and ``lower`` ``0.0`` — optional in the reader,
    which refuses a tube with no width at all."""
    keys = [column[0] for column in columns]
    if rows:
        last = copy.deepcopy(rows[-1])
        if "time" in keys:
            last["time"] = _new_time(rows, span)
        return last
    row: dict[str, Any] = {}
    for key, kind, *_ in columns:
        if key == "time":
            row[key] = _new_time(rows, span)
        elif isinstance(kind, tuple):
            row[key] = kind[0]
        elif kind in _REQUIRED:
            row[key] = 0.0
    if keys == ["time", "upper", "lower"]:  # tube_points
        row.update(upper=0.0, lower=0.0)
    return row


#: The column kinds a row cannot leave blank, and those that are a
#: tolerance with two sides (a number, or a lower/upper pair).
_REQUIRED = ("float", "sides")
_SIDES = ("sides", "sides?")

#: A table cell reserves no line under it: a validated field holds one for
#: its message from its first validation on (Quasar: any `error` prop, set
#: or not), which made a row half again as tall the moment a cell was
#: typed in. A cell grows that line only while it says "required".
_NO_BOTTOM = "hide-bottom-space"

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
        ui.label("drag on the plot, or edit the table").classes("text-xs")
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
        # A window, a box, a result line is drawn, not editable. The page
        # takes the pointer off it (the figure's `POINTER`, `grips.js`), so
        # no drag of one should arrive; one that does is answered with
        # nothing, and the figure hook's redraw snaps it back. Resolved
        # before the open-leaf check: whichever criterion is open, such a
        # drag is not an error the user made.
        if not _draggable(primitive):
            return None
        assert isinstance(primitive, (Marker, HLine, VLine))  # `_draggable`
        open_leaf = s.selection.leaf_path
        if open_leaf is not None and path != open_leaf:
            # Only the open leaf's rings and grips are drawn
            # (`Session.figure`'s `editing`), and every other leaf's bound
            # LINES, on the plot too, are inert in the page — but Plotly
            # itself would drag them, and a release can cross a redraw
            # (`FigureHook._shape_name`): one that reaches here is not this
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
        # The number a tolerance side is measured from, where the leaf's own
        # geometry holds one: a scalar's baseline scalar.
        target = next(
            (
                p.value
                for p in (leaf.geometry if leaf else ())
                if isinstance(p, HLine) and p.id == f"{path}/scalar/reference"
            ),
            None,
        )
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
                    target=target,
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
        required = [column[0] for column in columns if column[1] in _REQUIRED]
        #: A tolerance column -> what its two sides are called, where the
        #: column says (a time tolerance's `before` / `after`).
        sided = {
            column[0]: (column[2] if len(column) > 2 else None)
            for column in columns
            if column[1] in _SIDES
        }
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
                if key in sided and value is not None:
                    # The one reader of a number-or-pair; its ValueError —
                    # a missing side, a third key, a side that is no finite
                    # number — is the refusal.
                    parse_sides(value, f"row {i}: {key}")
                    # ... and the editor's own: a tolerance below zero can
                    # never be met (`..unscorable`).
                    refuse_negative(key, value, sided[key], where=f"row {i}: ")
            cleaned.append({k: v for k, v in row.items() if v is not None})
        # The reader's own refusal (`algorithms._interpolate_tube_widths`),
        # raised here by name rather than as the rescore's config error.
        is_tube = [column[0] for column in columns] == ["time", "upper", "lower"]
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
    whole list. (``act`` still refuses a blank, for a direct call.)

    A tolerance column (``sides`` / ``sides?``) is the form's control,
    `forms.sides_control`: two fields and a link toggle, named by the
    column's own side labels. With ONE side of a pair blank the control
    hands the table nothing — the row still holds the pair it had — so a
    half pair holds the whole list back as a blank required cell does.
    The one thing a half cell does hand over is the pair it held when its
    field took the focus, where values typed on the way to blank were
    staged (`0.2` backspaced away is `0.`, `0`, then nothing): that list
    is staged, so the row holds no pair nobody meant, unless ANOTHER cell
    holds the list back — then those values were never staged either.
    ``Add row`` and ``Remove row`` too: they rebuild the rows, and a half
    cell is rebuilt as it stands — blank and marked — not from the pair
    its row still holds, so nothing is staged until the blank is filled
    (or its row is the one removed).

    This PANEL is rebuilt too, by edits it did not make and is not told
    of: a drag on the plot rebuilds the whole editor, an edit in the
    criterion's form this panel. Built from the spec alone, a blank cell
    came back holding its old value, unmarked, and held nothing back. So
    every cell that shows what the spec does not hold — a required cell
    blank, a tolerance half typed — is written, as it becomes one, where
    it outlives the panel (`remember`, into ``Panel.kept``), and read back
    here: by field, the row's index in the SPEC's list and column, with the
    value the spec's row held under it. A cell whose row has since changed
    in the spec — that side dragged on the plot — shows the spec's again.
    What is not kept is anything else a held-back list holds: another
    cell's value typed meanwhile, a row added or removed. Rebuilt, the
    table shows the spec's rows, the blank cell still blank.

    A value ``act`` REFUSES is typed by way of others it takes — `-0.5`
    over a tolerance is nothing, `-0`, `-0.`, then `-0.5` — and each of
    those was staged: refused, the list stayed as the last of them left
    it, a tolerance of 0 beside a field reading −0.5. So the cell goes
    back to what it held when it was entered (`enter`), as a refused field
    of the criterion's form does: the list is staged with that one cell
    put back, and the refusal said again. The field keeps what was typed,
    and while it does the list is refused whole, like a blank required
    cell holds it back."""
    from ..forms import SidesControl, label_width, sides_control
    from ..typed_number import TypedNumber

    mark = f"{path}:{field}"
    container = ui.column().classes("w-full").mark(mark)
    required = [column[0] for column in columns if column[1] in _REQUIRED]
    cells: dict[tuple[int, str], Any] = {}  # the required cells, as rendered
    #: The tolerance cells, as rendered: the row each is on, its column and
    #: the control.
    pairs: list[tuple[dict, str, SidesControl]] = []
    #: The list as the spec holds it — what this table was built on, then
    #: what it last staged — and, per row shown, that row's index in it:
    #: None for a row added while a cell held the list back.
    spec: list[dict] = copy.deepcopy(rows)
    origin: list[int | None] = list(range(len(rows)))
    #: ``(field, index in the spec's list, column)`` -> ``{"base": what the
    #: spec's row holds there, "shown": None for a blank cell, else the
    #: half pair its fields show}``: `Panel.kept`, the one record of what
    #: this table's cells show that the spec does not hold.
    kept = ui_panel.kept
    #: The half pairs read back from it, by ``(id(row), column)``.
    restored: dict[tuple[int, str], dict] = {}
    for name, at, key in [k for k in kept if k[0] == field]:
        cell = kept[name, at, key]
        if at >= len(rows) or rows[at].get(key) != cell["base"]:
            del kept[name, at, key]  # the spec's row moved under it
        elif isinstance(cell["shown"], dict):
            restored[id(rows[at]), key] = cell["shown"]
        else:
            rows[at][key] = None

    def remember() -> None:
        """Rewrite this table's part of ``kept`` from its cells as they
        stand: called after every edit of one, whatever it staged."""
        for k in [k for k in kept if k[0] == field]:
            del kept[k]
        at_of = {id(row): at for row, at in zip(rows, origin) if at is not None}
        for row in rows:
            at = at_of.get(id(row))
            if at is None:
                continue  # a row the spec does not hold yet
            for key in required:
                if row.get(key) is None:
                    base = copy.deepcopy(spec[at].get(key))
                    kept[field, at, key] = {"base": base, "shown": None}
        for row, key, group in pairs:
            at = at_of.get(id(row))
            if at is not None and group.half():
                base = copy.deepcopy(spec[at].get(key))
                kept[field, at, key] = {"base": base, "shown": group.shown()}

    def blanks(but: SidesControl | None = None) -> bool:
        """Mark each required cell blank or not; True when any is, or when
        a tolerance cell other than ``but`` is half a pair (it marks its
        own fields). The mark is the cell's own validation (`missing`):
        written as an `error` prop it never showed — NiceGUI validates a
        field after its change handlers and resets an `error` it did not
        set itself."""
        for cell in cells.values():
            cell.validate(return_result=False)
        return any(row.get(key) is None for row in rows for key in required) or any(
            group.half() for _row, _key, group in pairs if group is not but
        )

    def missing(i: int, key: str) -> str | None:
        return f"{key} is required" if rows[i].get(key) is None else None

    #: The cell being edited — ``(row shown, column)``, None before any is
    #: and after the table heard a focus or changed its rows — and what its
    #: row held there when it was entered: what a refused edit puts back.
    taken: dict[str, Any] = {"cell": None, "entered": None}

    def enter(i: int, key: str) -> None:
        """``(i, key)`` is the first cell edited since another was, or
        since a focus was heard: what its row holds now is its start."""
        if taken["cell"] != (i, key):
            taken["cell"] = (i, key)
            taken["entered"] = copy.deepcopy(rows[i].get(key))

    def left() -> None:
        """A focus in the table, or its rows moved: whichever cell is
        edited next starts from what its row holds then."""
        taken["cell"] = None

    def held(listed: list[dict]) -> list[dict]:
        """``listed`` as the spec would hold it: a blank optional cell left
        out, as `act` leaves it out."""
        return [
            {k: copy.deepcopy(v) for k, v in row.items() if v is not None}
            for row in listed
        ]

    def stage(listed: list[dict]) -> bool:
        """Submit ``listed`` — the rows shown, or those with one cell put
        back; True when it went through, and the spec holds it."""
        if not ui_panel.submit(
            kind="rows",
            model_id=model_id,
            path=path,
            field=field,
            rows=copy.deepcopy(listed),
        ):
            return False
        spec[:] = held(listed)
        origin[:] = range(len(rows))
        return True

    def submit(but: SidesControl | None = None) -> None:
        held_back = blanks(but)
        if not held_back and not stage(rows) and taken["cell"] is not None:
            # Refused. The cell goes back to what it held when it was
            # entered — where values typed on the way to this one were
            # staged — and the refusal, which that stage hid, is said again.
            i, key = taken["cell"]
            back = copy.deepcopy(rows)
            back[i][key] = copy.deepcopy(taken["entered"])
            if held(back) != spec and stage(back):
                stage(rows)
        remember()

    def set_cell(i: int, key: str, value: Any) -> None:
        enter(i, key)
        rows[i][key] = value
        submit()

    def set_sides(i: int, key: str, value: Any) -> None:
        """A tolerance cell handed ``value`` over. Half as it does so, it
        is putting back what it held when its field took the focus: its
        own blank side does not hold THAT list back."""
        enter(i, key)
        rows[i][key] = value
        own = next(g for row, k, g in pairs if row is rows[i] and k == key)
        submit(but=own)

    def remove(i: int) -> None:
        left()
        rows.pop(i)
        origin.pop(i)
        render()
        submit()

    def add() -> None:
        left()
        rows.append(_new_row(columns, rows, span()))
        origin.append(None)
        render()
        submit()

    def render(halves: dict[tuple[int, str], dict] | None = None) -> None:
        # A half pair lives in its control alone: rebuilt from its row, the
        # blank side came back holding the pair's old value, no cell was
        # half any more, and the list was staged with that value in it.
        # ``halves``: those of the panel before this one, read from `kept`.
        if halves is None:
            halves = {
                (id(row), key): group.shown()
                for row, key, group in pairs
                if group.half() and any(row is still for still in rows)
            }
        container.clear()
        cells.clear()
        pairs.clear()
        with container:
            # The rows alone scroll; `Add row` follows their container.
            scroll = ui.column().classes("w-full dstf-table-scroll")
            for i, row in enumerate(rows):
                # A row wraps INSIDE itself where it is wider than the
                # editor (a points row is ten controls), never sideways.
                with scroll, ui.row().classes("dstf-table-row"):
                    for key, kind, *labels in columns:
                        if kind in _SIDES:
                            group = sides_control(
                                key,
                                row.get(key),
                                partial(set_sides, i, key),
                                side_labels=labels[0] if labels else SIDE_LABELS,
                                mark=f"{mark}:{i}:{key}",
                                required=key in required,
                                shown=halves.get((id(row), key)),
                                on_edit=remember,
                            )
                            for side in (group.lower, group.upper):
                                side.props(_NO_BOTTOM)
                                # At least as wide as its label, which an
                                # EMPTY cell draws full size: the cells'
                                # 120 px cut "before time_tolerance".
                                width = label_width(side.props["label"])
                                side.style(f"min-width: {width}px")
                            pairs.append((row, key, group))
                        elif isinstance(kind, tuple):
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
                                TypedNumber(
                                    label=key,
                                    value=row.get(key),
                                    on_change=lambda e, i=i, key=key: set_cell(
                                        i, key, e.value
                                    ),
                                    validation=(
                                        (lambda _v, i=i, key=key: missing(i, key))
                                        if key in required
                                        else None
                                    ),
                                )
                                .props(f"dense {_NO_BOTTOM}")
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

    # Whichever cell it is: what its row holds now is its start.
    container.on("focusin", lambda _e: left(), [])
    render(restored)
    if any(k[0] == field for k in kept):
        blanks()  # a blank cell read back says so from the start
