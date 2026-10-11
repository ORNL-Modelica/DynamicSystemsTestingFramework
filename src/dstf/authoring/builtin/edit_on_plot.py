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

A plot takes three more gestures for the open criterion (D149), each a pure
function here over the Session's view and each ending in the same one
``replace``: a HELD handle previews the leaf its release would stage
(:func:`dragged` — the release's own function, so the fields show what will
be written); a Ctrl+click adds a row that settles on the data
(:func:`clicked_row`); and a click with a field armed sets that field
(:func:`placed`) — through the handle that sets it, where there is one, so
a click is refused and clamped as that handle's drag is. A ⌖ is drawn only
where :func:`placeable` says a click can set its field: one per table row,
and on a form field in the state the criterion is in.
"""

from __future__ import annotations

import copy
import dataclasses
import math
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any

from ...comparison.geometry import HLine, Marker, VLine, Window, grip_axis
from ...comparison.handles import (
    HandleEdit,
    HandleEditError,
    RowPlace,
    _written,
    handle_fields,
)
from ...comparison.mode_schema import SIDE_LABELS
from ...comparison.registry import DEFAULT_METRIC, MetricDescriptor, metric
from ...comparison.sides import parse_sides
from ...comparison.snap import snapped
from ...figures.assemble import leaves_by_variable
from ..capabilities import Panel, Patch, Placement
from ..session import no_tree_error
from ..spec_document import PatchOp
from ..tree_pointer import node_at
from ..unscorable import Refused, refuse_bounds, refuse_negative

if TYPE_CHECKING:
    from ...figures.view import LeafView, VariableView
    from ..session import SessionView

# NO module-level `nicegui` import: `import dstf.authoring` runs
# register_builtins(), and the Session must import without the extra.

#: A list field's ``columns`` metadata: per row key its kind — ``"float"``,
#: ``"float?"``, ``"sides"``, ``"sides?"`` or a tuple of choices — and, for a
#: ``sides`` kind, optionally what its two sides are called. Read by INDEX:
#: an entry is two or three long.
Columns = tuple[tuple[Any, ...], ...]


#: What a ⌖ names (D149): a field of the open leaf by its keys from the
#: raw leaf down to the value — ``("min_value",)``, ``("window", "start")``,
#: one side of a tolerance ending in ``"lower"`` or ``"upper"`` whether the
#: file holds a number or a pair (`handles.handle_fields` names a handle's
#: fields the same way) — or a ROW of a table, ``("points", 3)``.
Pointer = tuple[Any, ...]

#: The tables whose rows have no ring: the column a click sets directly.
_RINGLESS = {"events": "time"}

#: A bound and the other one: a BLANK bound is placed where the leaf draws
#: its other.
_BOUNDS = {"min_value": "max_value", "max_value": "min_value"}

#: Why the place is a sentence on a test with nothing scored BECAUSE OF A
#: RUN (D151), ``{}`` being the word its criteria say in place of a verdict
#: (`live_rows.WAITING`, `NOT_SCORED`): a handle is drawn from a scored
#: criterion's geometry, and the table is edited beside its handles.
UNSCORED = "{} — its table and its handles on the plot are edited once it is scored"


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


def _run_span(view: VariableView, leaf: LeafView) -> tuple[float, float] | None:
    """The ACTUAL series' first and last time as ``leaf``'s scorer received
    it: the evaluator slices the run to the leaf's window (inclusive) first
    and says so in the leaf's geometry (its ``Window``), so the span is the
    samples inside that. None with no actual series, or none inside."""
    time = view.act_time
    if time is None:
        return None
    window = next((p for p in leaf.geometry if isinstance(p, Window)), None)
    if window is not None and window.start is not None:
        time = time[time >= window.start]
    if window is not None and window.end is not None:
        time = time[time <= window.end]
    if len(time) == 0:
        return None
    return float(time[0]), float(time[-1])


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


def _visible(said: object) -> tuple[float | None, float | None]:
    """What the page says its axes SHOWED — ``{"x": span, "y": span}``, at
    the press of a drag or at a click — as `HandleEdit.visible`. An axis it
    does not say (an arrow anchored to the paper has no y; a caller that is
    no page says none) is None, and nothing placed on it is snapped."""
    spans = said if isinstance(said, dict) else {}

    def span(value: object) -> float | None:
        return float(value) if _is_number(value) and value > 0 else None  # type: ignore[arg-type,operator]

    return span(spans.get("x")), span(spans.get("y"))


def _click(said: object) -> tuple[str, float, float, tuple[float | None, float | None]]:
    """A click as the page reports it (`grips.js`): the panel it is on, its
    position in that panel's axes, and what those axes showed. The spectrum
    is the one panel on a second x axis (`figures.plotly._xref`)."""
    click = said if isinstance(said, dict) else {}
    x, y = click.get("x"), click.get("y")
    if (
        not isinstance(x, (int, float))
        or not isinstance(y, (int, float))
        or not (math.isfinite(x) and math.isfinite(y))
    ):
        raise ValueError(f"a click at ({x!r}, {y!r}) names no place on the plot")

    def span(ends: object) -> float | None:
        if (
            isinstance(ends, (list, tuple))
            and len(ends) == 2
            and all(map(_is_number, ends))
        ):
            return abs(ends[1] - ends[0])
        return None

    panel = "spectrum" if click.get("xaxis") == "x2" else "time"
    visible = _visible({"x": span(click.get("xrange")), "y": span(click.get("yrange"))})
    return panel, float(x), float(y), visible


def _scored(
    s: SessionView, model_id: str, variable: str, path: str, view: VariableView | None
) -> tuple[dict, MetricDescriptor | None, VariableView, LeafView | None]:
    """The open leaf as every gesture on the plot reads it: its raw node,
    its metric's descriptor, the variable's view (``view`` when the caller
    holds one: a held drag asks many times of a Session that does not
    move) and the leaf as it was scored — None where it was not."""
    raw = _leaf_at(s, model_id, path)
    desc = metric(raw.get("metric", DEFAULT_METRIC))
    if view is None:
        view = s.view(model_id, variable)  # full resolution, not the figure
    leaf = next((lv for lv in view.leaves if lv.path == path), None)
    return raw, desc, view, leaf


def _edit(
    path: str,
    raw: dict,
    view: VariableView,
    leaf: LeafView | None,
    local: str,
    x: float | None,
    y: float | None,
    visible: tuple[float | None, float | None],
) -> HandleEdit:
    """One position of the handle ``local`` of the leaf at ``path``, with
    everything an editor measures from."""
    geometry = leaf.geometry if leaf is not None else ()
    # The number a tolerance side is measured from, where the leaf's own
    # geometry holds one: a scalar's baseline scalar.
    target = next(
        (
            p.value
            for p in geometry
            if isinstance(p, HLine) and p.id == f"{path}/scalar/reference"
        ),
        None,
    )
    return HandleEdit(
        leaf=raw,
        local=local,
        x=x,
        y=y,
        reference=_reference(view),
        target=target,
        # What a time may be dragged to where the leaf is scored against
        # the RUN and not the baseline (a checkpoint with a value of its
        # own): the page holds a release to the axis, which runs past the
        # data.
        span=_run_span(view, leaf) if leaf is not None else None,
        visible=visible,
    )


def _reference(view: VariableView) -> tuple[Any, Any] | None:
    if view.ref_time is None or view.ref_values is None:
        return None
    return view.ref_time, view.ref_values


def dragged(
    s: SessionView,
    *,
    model_id: str,
    variable: str,
    handle: str,
    shape: dict,
    view: VariableView | None = None,
) -> tuple[str, dict, dict] | None:
    """What a drag of ``handle`` to ``shape`` — the figure hook's per-shape
    payload — MEANS: ``(leaf path, the raw leaf, the leaf the drag
    writes)``, or None for a shape no editor moves and for no move at all.
    It reads and stages nothing, so the release of a handle and the preview
    of one still held are this one function over the same payload (D149):
    the fields show, snapped, what the release will write. Refused as the
    drag is, each refusal naming its leaf."""
    if not handle:
        return None
    handle = _handle_id(handle)
    paths = [p for p, _ in _leaf_paths(s, model_id, variable)]
    path = _leaf_path_of(handle, paths)
    if path is None:
        raise ValueError(f"{handle} names no leaf on {variable} of {model_id}")
    if view is None:
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
    assert leaf is not None  # the primitive is its
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
    shape = shape if isinstance(shape, dict) else {}
    # Several leaves on one variable can refuse the same local id: every
    # refusal below names its leaf.
    try:
        position = _position(primitive, shape)
        if position is None:
            return None
        x, y = position
        local = handle[len(path) + 1 :]
        visible = _visible(shape.get("visible"))
        new = desc.edit_handle(_edit(path, raw, view, leaf, local, x, y, visible))
    except HandleEditError as e:
        raise HandleEditError(f"{path}: {e}", reason=e.reason) from None
    return path, raw, new


def clicked_row(
    s: SessionView, *, model_id: str, variable: str, path: str, click: dict
) -> dict | None:
    """The leaf at ``path`` with the row a Ctrl+click at ``click`` adds to
    its table (D149) — the metric's own placer, which settles the row on
    the data; every field the click does not set is what ``Add row`` gives
    today. None where the metric has no rows to add to, and where the
    placer adds none (a tube of constant width)."""
    raw, desc, view, leaf = _scored(s, model_id, variable, path, None)
    if desc is None or desc.place_row is None:
        return None
    panel, x, y, visible = _click(click)
    reference = _reference(view)
    span = None
    if reference is not None and len(reference[0]):
        span = float(reference[0][0]), float(reference[0][-1])
    blanks = {
        field: _new_row(columns, copy.deepcopy(raw.get(field) or []), span)
        for field, columns in _list_fields(desc.name)
    }
    actual = None
    if view.act_time is not None and view.act_values is not None:
        actual = view.act_time, view.act_values
    try:
        new = desc.place_row(
            RowPlace(
                leaf=raw,
                blanks=blanks,
                panel=panel,
                x=x,
                y=y,
                reference=reference,
                actual=actual,
                geometry=leaf.geometry if leaf is not None else (),
                visible=visible,
                span=_run_span(view, leaf) if leaf is not None else None,
            )
        )
    except HandleEditError as e:
        raise HandleEditError(f"{path}: {e}", reason=e.reason) from None
    return None if new == raw else new


@dataclass(frozen=True)
class _Target:
    """How a click sets one field of the open leaf (:func:`_target`): on
    which ``panel``, and either through ``handles`` that write it — each
    where it stands along x (None: nowhere), its local id and the click's
    coordinates it is handed — or ``direct``: the field's own keys and the
    axis of the click read into them."""

    panel: str
    handles: tuple[tuple[float | None, str, tuple[str, ...]], ...] = ()
    direct: tuple[Pointer, str] | None = None


def _target(
    raw: dict, path: str, leaf: LeafView | None, pointer: Pointer
) -> _Target | None:
    """How a click sets ``pointer`` in the criterion's CURRENT state, or
    None where none can — the ONE rule a ⌖ is drawn by (:func:`placeable`)
    and a click acts by (:func:`placed`), so no ⌖ stands on a field a click
    would refuse.

    A ROW of a table, ``(table, i)``, is placed as its ring let go at the
    click: handed the click's x — its time, a peak's frequency — and the
    click's y only where that ring writes the row's ``value``, which the
    ring's own editor does only for a row that HOLDS one. A declared event
    has no ring: its time is the click's x. A window's ends are the click's
    x. Any other field, where handles of the leaf write it: an arrow is
    handed both coordinates (it writes along its own axis and only
    MEASURES along the other — a constant tube's side from the reference
    at the click's time), a line the one on its axis. And a BLANK bound of
    a leaf that draws its other one — a range, a scalar scored by its
    bounds — is the click's y."""
    if len(pointer) == 2 and pointer[0] == "window" and pointer[1] in ("start", "end"):
        return _Target("time", direct=(pointer, "x"))
    handles = [
        (p, _handle_id(p.id)[len(path) + 1 :])
        for p in (leaf.geometry if leaf is not None else ())
        if _draggable(p) and p.id.startswith(path + "/")
    ]
    if len(pointer) == 2 and type(pointer[1]) is int:
        table, i = pointer
        rows = raw.get(table)
        if not isinstance(rows, list) or not 0 <= i < len(rows):
            return None
        for p, local in handles:
            fields = handle_fields(raw, local)
            at = fields.get("x", ())
            if isinstance(p, Marker) and p.kind == "handle" and at[:2] == pointer:
                valued = fields.get("y") == (*pointer, "value")
                return _Target(
                    p.panel, ((None, local, ("x", "y") if valued else ("x",)),)
                )
        if table in _RINGLESS:
            return _Target("time", direct=((table, i, _RINGLESS[table]), "x"))
        return None
    found = [
        (p.panel, (_stands_at(p), local, ("x", "y") if p.kind == "grip" else (axis,)))
        for p, local in handles
        for axis, field in handle_fields(raw, local).items()
        # A criterion's own field: a cell of a row is no place, its row is.
        if field == pointer and all(isinstance(key, str) for key in pointer)
    ]
    if found:
        return _Target(found[0][0], tuple(handle for _panel, handle in found))
    if len(pointer) == 1 and pointer[0] in _BOUNDS and raw.get(pointer[0]) is None:
        other = (_BOUNDS[pointer[0]],)
        if any(other in handle_fields(raw, local).values() for _p, local in handles):
            return _Target("time", direct=(pointer, "y"))
    return None


def placeable(
    s: SessionView, *, model_id: str, variable: str, path: str
) -> Callable[[Pointer], bool]:
    """Whether a click on the plot can set a field of the leaf at ``path``
    as it now is (:func:`_target`): what a panel asks before it draws a ⌖.
    The leaf is read on the first question and once."""
    read: list[Any] = []

    def can(pointer: Pointer) -> bool:
        if not read:
            try:
                read.append(_scored(s, model_id, variable, path, None))
            except (KeyError, ValueError):
                read.append(None)  # no such leaf: nothing of it is placed
        if read[0] is None:
            return False
        raw, _desc, _view, leaf = read[0]
        return _target(raw, path, leaf, tuple(pointer)) is not None

    return can


def _stands_at(primitive: object) -> float | None:
    """Where a handle stands along x, to tell which of several a click is
    nearest; a bound line stands at none."""
    if isinstance(primitive, VLine):
        return primitive.time
    return primitive.x if isinstance(primitive, Marker) else None


def placed(
    s: SessionView,
    *,
    model_id: str,
    variable: str,
    path: str,
    pointer: Pointer,
    click: dict,
) -> dict | None:
    """The leaf at ``path`` with the field ``pointer`` set by a click
    (D149: arm a field, then click the plot), or None where it is as it
    was. How is :func:`_target`'s to say; where it says None the click is
    refused — and no ⌖ was drawn. A click IS a drag of the field's handle
    released at the click, so it is snapped, clamped and refused as that
    drag is; of several handles that write the field (a criterion's
    tolerance its rows defer to) the one nearest the click is used, and a
    tolerance side's EDGE lands on the click. A field set directly is
    snapped, and a bound then refused as a typed one is.

    Every field belongs to ONE panel — the axes its handle, or its
    meaning, lives on — and a click on the other is refused in words that
    name where to click (``click["label"]`` is what the field is called):
    the spectrum's magnitude was written as a bound, a frequency as a
    time."""
    pointer = tuple(pointer)
    raw, desc, view, leaf = _scored(s, model_id, variable, path, None)
    panel, x, y, visible = _click(click)
    target = _target(raw, path, leaf, pointer)
    if target is None:
        raise Refused("nothing on this plot sets that field")
    if panel != target.panel:
        label = click.get("label") or "this field"
        where = "spectrum" if target.panel == "spectrum" else "time plot"
        reason = f"{label} is set on the {where}"
        raise HandleEditError(f"{path}: {reason}", reason=reason)
    if target.direct is not None:
        field, axis = target.direct
        across = axis == "x"
        new = copy.deepcopy(raw)
        node: Any = new
        for key in field[:-1]:
            node = node.setdefault(key, {}) if isinstance(node, dict) else node[key]
        node[field[-1]] = _written(
            snapped(x if across else y, visible[0 if across else 1])
        )
        refuse_bounds(str(new.get("metric", DEFAULT_METRIC)), new)
        return None if new == raw else new
    if desc is None or desc.edit_handle is None:
        raise ValueError(f"{raw.get('metric')} has no on-plot handles")
    _at, local, axes = min(
        target.handles, key=lambda one: abs(one[0] - x) if one[0] is not None else 0.0
    )
    at_x = x if "x" in axes else None
    at_y = y if "y" in axes else None
    try:
        new = desc.edit_handle(_edit(path, raw, view, leaf, local, at_x, at_y, visible))
    except HandleEditError as e:
        raise HandleEditError(f"{path}: {e}", reason=e.reason) from None
    return None if new == raw else new


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
        # The handles are drawn from the comparison's tree; every edit of
        # one goes through the Session's, which may have none.
        tree, why = s.tree_or_reason(model_id)
        if tree is None:
            assert why is not None  # `tree` is None
            return why
        metric_name = dict(_leaf_paths(s, model_id, variable)).get(leaf_path)
        if metric_name is None:
            # Nothing of the test is scored while it runs, or after a run
            # that did not finish (D151): the criterion is there, and the
            # page's word for it is the reason.
            word = (s.tree_view(model_id) or {}).get("unscored")
            if word:
                return UNSCORED.format(word)
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
                # A release ends its preview: staged, the editor was
                # rebuilt from the leaf; refused or no move at all, the
                # fields go back to what is staged.
                ui_panel.preview(None)

        ui_panel.figure.on("plotly_relayout", on_relayout)

        #: The drag being held and the variable's view it is previewed
        #: over: one read for the whole of a drag — the Session does not
        #: move under a held handle, and a view is every trajectory read
        #: again.
        held: dict[str, Any] = {"drag": None, "view": None}

        def on_move(handle: str, payload: dict) -> None:
            """A held handle's would-be position (D149): the leaf its
            release would stage, shown in the fields and staged nowhere. A
            position the release would refuse previews nothing: the
            release says why."""
            if not handle or payload.get("end"):
                ui_panel.preview(None)
                return
            drag = payload.get("drag")
            if held["drag"] != drag or held["view"] is None:
                held.update(drag=drag, view=s.view(model_id, variable))
            try:
                found = dragged(
                    s,
                    model_id=model_id,
                    variable=variable,
                    handle=handle,
                    shape=payload,
                    view=held["view"],
                )
            except ValueError:
                found = None
            revision = drag if isinstance(drag, int) else 0
            ui_panel.preview(None if found is None else found[2], revision)

        ui_panel.figure.on("dstf_move", on_move)

        def on_click(_handle: str, click: dict) -> None:
            """Ctrl+click adds a row (D149); ⌘ is Ctrl where Ctrl+click is
            the context menu. A plain click is an armed field's
            (`on_place`), and any other modifier's is nothing."""
            if click.get("ctrl") or click.get("meta"):
                ui_panel.submit(
                    kind="click",
                    model_id=model_id,
                    variable=variable,
                    path=leaf_path,
                    click=click,
                )

        ui_panel.figure.on("dstf_click", on_click)
        ui_panel.on_place(
            lambda pointer, click: ui_panel.submit(
                kind="place",
                model_id=model_id,
                variable=variable,
                path=leaf_path,
                pointer=pointer,
                click=click,
            )
        )
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
            tables = _list_fields(name)
            if tables:
                # Said where a table is drawn: a range or a scalar has lines
                # on the plot and no table, and the editor's own hint line
                # says what its plot drags.
                ui.label("drag on the plot, or edit the table").classes("text-xs")
            shows = []
            can = placeable(s, model_id=model_id, variable=variable, path=path)
            for field, columns in tables:
                ui.label(_field_label(name, field)).classes("text-xs text-bold")
                rows = copy.deepcopy(raw.get(field) or [])
                show = _table(
                    ui, path, field, columns, rows, span, ui_panel, model_id, can
                )
                shows.append((field, show))

            def preview(leaf: dict | None, revision: int, shows: list = shows) -> None:
                for field, show in shows:
                    show(None if leaf is None else leaf.get(field), revision)

            ui_panel.on_preview(preview)

    def act(self, s: SessionView, **inputs: Any) -> Patch | None:
        kind = inputs["kind"]
        if kind == "handle":
            return self._act_handle(s, **inputs)
        if kind == "rows":
            return self._act_rows(s, **inputs)
        if kind == "click":
            new = clicked_row(
                s,
                model_id=inputs["model_id"],
                variable=inputs["variable"],
                path=inputs["path"],
                click=inputs["click"],
            )
            return _replace(inputs["model_id"], inputs["path"], new)
        if kind == "place":
            new = placed(
                s,
                model_id=inputs["model_id"],
                variable=inputs["variable"],
                path=inputs["path"],
                pointer=inputs["pointer"],
                click=inputs["click"],
            )
            return _replace(inputs["model_id"], inputs["path"], new)
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
        found = dragged(
            s, model_id=model_id, variable=variable, handle=handle, shape=shape
        )
        if found is None:
            return None
        path, raw, new = found
        return _replace(model_id, path, None if new == raw else new)

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
                    raise Refused(f"row {i}: {key} is required")
            for key, value in row.items():
                if _is_number(value) and not math.isfinite(value):
                    raise Refused(f"row {i}: {key} = {value!r} is not a finite number")
                if key in sided and value is not None:
                    # The one reader of a number-or-pair; its ValueError —
                    # a missing side, a third key, a side that is no finite
                    # number — is the refusal.
                    try:
                        parse_sides(value, f"row {i}: {key}")
                    except ValueError as e:
                        raise Refused(str(e)) from None
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
            raise Refused(f"{field}: no row has an upper or a lower width")
        new = copy.deepcopy(raw)
        new[field] = cleaned
        if new == raw:
            return None
        return Patch(ops=[PatchOp(model_id, "replace", path, new)])


def _replace(model_id: str, path: str, new: dict | None) -> Patch | None:
    """The one op every gesture on the plot stages — the whole leaf
    replaced — or None where the gesture left the leaf as it was."""
    if new is None:
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
    can: Callable[[Pointer], bool],
) -> Callable[[list | None, int], None]:
    """One list field's rows, a control per column, ``Remove row`` per row
    and ``Add row``. Returns the table's PREVIEW (D149): called with the
    rows a held drag would write — and the drag's number — every number
    cell and tolerance side whose value would change shows that value,
    staging nothing (`TypedNumber.preview`); called with None, or with a
    list of another length, they show what they hold again. A row the
    plot can place (``can``: `placeable`) carries ONE ⌖, in its first cell
    (`Panel.place_button`), naming it by the table and its index: armed,
    the click is its ring let go there. A row added or removed moves what
    an index names, so it disarms (`Panel.disarm`). The rows live here: neither a refused submit nor a
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
    cell holds it back. "What it held" is what the STAGED list holds for
    it, never what the cell shows: a refused cell shows a value the spec
    never took, and entered again with that for its start, its put-back
    was refused in turn and the `-0` stayed. And whichever way a put-back
    goes, the list as shown is handed over once more after it, so the
    sentence left standing is the first refusal of the rows AS SHOWN — not
    the put-back's, of a value no cell shows any more."""
    from ..forms import SidesControl, label_width, sides_control
    from ..typed_number import TypedNumber

    mark = f"{path}:{field}"
    container = ui.column().classes("w-full").mark(mark)
    required = [column[0] for column in columns if column[1] in _REQUIRED]
    cells: dict[tuple[int, str], Any] = {}  # the required cells, as rendered
    numbers: dict[tuple[int, str], Any] = {}  # every number cell, as rendered
    #: The tolerance cells, as rendered: the row each is on, its column and
    #: the control.
    pairs: list[tuple[dict, str, SidesControl]] = []
    #: The list as the spec holds it — what this table was built on, then
    #: what it last staged — and, per row shown, where that row comes from:
    #: its index in that list, or, for a row added while a cell held the
    #: list back, the row as it was added (a copy: the spec holds no row
    #: for it yet, and the index it is shown at may be ANOTHER row's there
    #: — one removed while the list was held back).
    spec: list[dict] = copy.deepcopy(rows)
    origin: list[int | dict] = list(range(len(rows)))
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
        at_of = {id(row): at for row, at in zip(rows, origin) if isinstance(at, int)}
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
    #: and after the table heard a focus or changed its rows — and what the
    #: staged list held there when it was entered: what a refused edit puts
    #: back.
    taken: dict[str, Any] = {"cell": None, "entered": None}

    def enter(i: int, key: str) -> None:
        """``(i, key)`` is the first cell edited since another was, or
        since a focus was heard: what the STAGED list holds for it now is
        its start (`spec`, by the row's `origin`) — as the criterion's form
        starts a field from the leaf its last accepted submit left
        (`edit_criteria`'s ``taken["leaf"]``). A row the spec does not hold
        yet (added while the list was held back) has no staged value: it
        starts from what it was added with (its `origin`) — every time it
        is entered, not the first alone. Started from what the row SHOWS,
        a cell left on a refused value and entered again went back to that
        value: the put-back was refused in turn, and a `-0` typed on the
        way stayed in the list once the rest of it could be staged."""
        if taken["cell"] != (i, key):
            taken["cell"] = (i, key)
            at = origin[i]
            start = spec[at] if isinstance(at, int) else at
            taken["entered"] = copy.deepcopy(start.get(key))

    def left() -> None:
        """A focus in the table, or its rows moved: whichever cell is
        edited next starts from what the staged list holds for it then."""
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
            # Refused. The cell goes back to what the spec held for it
            # when it was entered — where values typed on the way to this
            # one were staged — and the refusal is said again: a put-back
            # that went through hid it, and one refused in its turn (another
            # cell shows a refused value too) said its own, of a value this
            # cell no longer shows.
            i, key = taken["cell"]
            back = copy.deepcopy(rows)
            back[i][key] = copy.deepcopy(taken["entered"])
            if held(back) != spec:
                stage(back)
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
        ui_panel.disarm()  # a ⌖ names its row by index
        left()
        rows.pop(i)
        origin.pop(i)
        render()
        submit()

    def add() -> None:
        ui_panel.disarm()
        left()
        rows.append(_new_row(columns, rows, span()))
        origin.append(copy.deepcopy(rows[-1]))
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
        numbers.clear()
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
                            numbers[(i, key)] = cell
                            if key == columns[0][0] and can((field, i)):
                                with cell.add_slot("append"):
                                    ui_panel.place_button((field, i), f"row {i + 1}")
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

    def preview(shown: list | None, revision: int) -> None:
        if shown is not None and len(shown) != len(rows):
            shown = None  # no drag adds or removes a row
        for (i, key), cell in numbers.items():
            would = None if shown is None else shown[i].get(key)
            cell.preview(None if would == rows[i].get(key) else would, revision)
        for row, key, group in pairs:
            at = next((n for n, still in enumerate(rows) if still is row), None)
            would = None if shown is None or at is None else shown[at].get(key)
            group.preview(None if would == row.get(key) else would, revision)

    return preview
