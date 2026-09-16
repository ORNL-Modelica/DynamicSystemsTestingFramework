"""The centre plot's event hook (two-tier design §4.6 "Panel", amended in
sub-project 6): ``panel.figure.on(kind, handler)``. Moved here unchanged from
the old shell; sub-project 3's Test page attaches it to each plot."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from nicegui import ui

# The index is bounded so a hostile key cannot reach `int()`'s digit limit;
# `[0-9]`, not `\d` (which matches any Unicode digit, and `int()` accepts
# those), and `\Z`, not `$` (which also matches before a trailing newline).
_SHAPE_KEY = re.compile(r"^shapes\[([0-9]{1,9})\]\.(.+)\Z")


class FigureHook:
    """``panel.figure.on(kind, handler)``: handlers get a primitive id and a
    payload. Wired to the centre plot's Plotly events. The payload contract
    (the spike's, over the payloads plotly.js actually sends):

    - ``plotly_click`` / ``plotly_selected``: the id of the first point whose
      trace ``meta.kind`` is ``"handle"``, else of the first point —
      ``customdata``, then ``meta.id``, then ``curveNumber`` — with the event
      payload. Under the figure's ``hovermode: "x unified"`` a click lists
      every trace at that x, so a handle is not necessarily ``points[0]``.
    - ``plotly_relayout`` with keys matching ``shapes[N].<key>``: dispatched
      ONCE PER SHAPE INDEX, ascending. The payload never names the shape;
      the hook resolves the id from the rendered figure's
      ``layout.shapes[N].name`` (``""`` when that lookup fails) and hands the
      handler that shape's keys with the ``shapes[N].`` prefix stripped —
      ``{"y0": 6.0, "y1": 6.0}``, ``{"xanchor": 4.0, "yanchor": 2.99}``, or a
      ring's pixel resize ``{"x0": -7, ..., "y1": 7}``. Positions arrive in
      data coordinates. The hook does not interpret them: the capability does,
      from the handle's primitive type (a Marker's ring moves only by
      ``xanchor``/``yanchor``). ``on_shape_moved`` is then called once, in a
      ``finally``: a raising handler still gets its snap-back. A relayout
      mixing shape keys with others (a zoom) is a shape relayout; the other
      keys are dropped.
    - Any other relayout (a zoom): dispatched once with id ``""`` and the raw
      payload (``{}`` when it is not a dict); ``on_shape_moved`` is not called.

    Handler semantics: each shape index is dispatched in turn, ascending,
    from its own snapshot of the handlers, taken when that shape's dispatch
    starts. A handler registered since the snapshot (a panel rebuilt by the
    act it triggered) is not called for that shape; one removed since the
    snapshot (``clear``) is skipped, checked by identity against the current
    list, so a rebuilt panel's predecessor never runs. A handler registered
    twice is called twice. A handler that raises ENDS the relayout: later
    handlers and later shapes are not dispatched, the exception propagates,
    and ``on_shape_moved`` still runs. A malformed payload never makes
    dispatch raise."""

    def __init__(self, on_shape_moved: Callable[[], None] | None = None) -> None:
        self._on_shape_moved = on_shape_moved
        self._handlers: dict[str, list[tuple[str, Callable[[str, dict], None]]]] = {}
        self._plot: Any = None
        self.dispatching = False  # true while handlers run

    def clear(self, owner: str | None = None) -> None:
        """Remove one owner's handlers, or every handler. The shell rebuilds
        panels on selection and job events (the whole tabs region) and on
        results events (slot by slot); a subscriber that re-registers in
        `panel()` must not accumulate."""
        if owner is None:
            self._handlers = {}
            return
        # `_call` skips a handler removed mid-dispatch by IDENTITY against
        # the current list, so the entries left here must be the SAME tuple
        # objects — rebuilding them (`(o, h)` anew) would make every
        # surviving handler look removed and skip it.
        self._handlers = {
            kind: [entry for entry in entries if entry[0] != owner]
            for kind, entries in self._handlers.items()
        }

    def on(
        self, kind: str, handler: Callable[[str, dict], None], owner: str = ""
    ) -> None:
        self._handlers.setdefault(kind, []).append((owner, handler))

    def owned(self, owner: str) -> _OwnedFigureHook:
        """The view of this hook a capability's panel holds: its `on`
        registers under ``owner``, so `clear(owner)` removes exactly those."""
        return _OwnedFigureHook(self, owner)

    def attach(self, plot: ui.plotly) -> None:
        self._plot = plot
        for event in ("plotly_click", "plotly_relayout", "plotly_selected"):
            plot.on(event, lambda e, kind=event: self._dispatch(kind, e.args))

    def _shape_name(self, index: int) -> str:
        # A known race: the index is resolved against the figure the SERVER
        # last sent. A drag released after an in-place `update_figure` that
        # changed the shape list, but resolved before the browser has applied
        # that update and the relayout has flown back, can resolve to the
        # wrong name. The window runs from `update_figure` through both
        # websocket legs. The snap-back redraw corrects only the picture: once
        # edits are staged from drags, a wrongly resolved handle's edit stays
        # staged.
        figure = getattr(self._plot, "figure", None)
        layout = figure.get("layout") if isinstance(figure, dict) else None
        shapes = layout.get("shapes") if isinstance(layout, dict) else None
        if not isinstance(shapes, list) or index >= len(shapes):
            return ""
        shape = shapes[index]
        name = shape.get("name") if isinstance(shape, dict) else None
        return name if isinstance(name, str) else ""

    def _dispatch(self, kind: str, payload: Any) -> None:
        data = payload if isinstance(payload, dict) else {}
        if kind == "plotly_relayout":
            by_shape: dict[int, dict] = {}
            for key, value in data.items():
                match = _SHAPE_KEY.match(key) if isinstance(key, str) else None
                if match is not None:
                    by_shape.setdefault(int(match.group(1)), {})[match.group(2)] = value
            if not by_shape:
                self._call(kind, "", data)
                return
            try:
                for i in sorted(by_shape):
                    self._call(kind, self._shape_name(i), by_shape[i])
            finally:
                if self._on_shape_moved is not None:
                    self._on_shape_moved()
            return
        points = data.get("points")
        points = (
            [p for p in points if isinstance(p, dict)]
            if isinstance(points, list)
            else []
        )
        chosen = next(
            (p for p in points if _meta(p).get("kind") == "handle"),
            points[0] if points else {},
        )
        # The figure layer stamps every trace's primitive id in `meta.id`
        # (figures/plotly.py); marker groups carry per-point ids in
        # `customdata`. The trace index is the last resort.
        primitive = chosen.get("customdata")
        if primitive is None:
            primitive = _meta(chosen).get("id")
        if primitive is None:
            primitive = chosen.get("curveNumber")
        self._call(kind, "" if primitive is None else str(primitive), data)

    def _call(self, kind: str, primitive: str, payload: dict) -> None:
        """One event: the snapshot is taken here, and each entry is checked
        by identity against the current list just before it is called."""
        snapshot = list(self._handlers.get(kind, []))
        was, self.dispatching = self.dispatching, True
        try:
            for entry in snapshot:
                if not any(e is entry for e in self._handlers.get(kind, [])):
                    continue  # removed since the snapshot
                entry[1](primitive, payload)
        finally:
            self.dispatching = was


def _meta(point: dict) -> dict:
    trace = point.get("data")
    meta = trace.get("meta") if isinstance(trace, dict) else None
    return meta if isinstance(meta, dict) else {}


class _OwnedFigureHook:
    """`FigureHook.owned(owner)`: satisfies the capability-side protocol."""

    def __init__(self, hook: FigureHook, owner: str) -> None:
        self._hook, self._owner = hook, owner

    def on(self, kind: str, handler: Callable[[str, dict], None]) -> None:
        self._hook.on(kind, handler, owner=self._owner)
