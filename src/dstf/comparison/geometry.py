"""Geometry primitives: what a comparison mode scored against, as data.

Each :class:`~.types.VariableComparison` carries the primitives its mode
built while scoring — the tube band on the grid it was enforced on, the
checkpoint boxes, the declared bounds, the detected events, the spectra.
They are the single source every figure draws from (``dstf.figures``), so a
mode cannot draw one envelope and score another.

The vocabulary is deliberately small. A new kind of drawing is a new class
here plus one builder in ``dstf.figures.plotly``; that is cheaper than
smuggling a shape into :class:`Series`, which is the failure this file's
size guards against.

Ids are addresses. A mode emits LOCAL ids (``tube/band``, ``tube/upper/3``,
``points/target/0``) — it does not know its leaf — and the evaluator
prefixes each with the leaf's RFC 6902 path (``/metrics/children/2``)
through :meth:`Primitive.prefixed`, so a figure handle and a patch op share
one name. ``kind`` is a free label the figure builder styles on:
``"result"`` (what was computed), ``"handle"`` (the resolved position of an
authored parameter — a control point, a bound, a declared peak), ``"grip"``
(the resolved position of one SIDE of a tolerance; drawn only for the open
criterion in the tool, never as a trace and never in the report — four more
dots per checkpoint would sit on every report page), ``"ref"`` / ``"act"``
(which curve an event or spectrum belongs to), ``"matched"`` / ``"detected"``
(peak roles), ``"error"`` (the view's error series).

A grip's local id is ``<family>/<edge>/<key>`` — ``points/upper/3``,
``events/late/0``, ``tube/lower/const`` — beside the handle it belongs to
(``points/target/3``): the edge says which side it is, and
:func:`grip_axis` / :func:`grip_outward` which way it moves.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

import numpy as np

Panel = Literal["time", "spectrum"]


@dataclass(frozen=True, kw_only=True, eq=False)
class Primitive:
    id: str
    kind: str = "result"
    panel: Panel = "time"

    def prefixed(self, prefix: str) -> Primitive:
        """A copy whose id is ``<prefix>/<id>`` — the evaluator's stamp."""
        return replace(self, id=f"{prefix}/{self.id}")


@dataclass(frozen=True, kw_only=True, eq=False)
class Band(Primitive):
    """An envelope: ``lower[i] <= y <= upper[i]`` at ``time[i]``."""

    time: np.ndarray
    lower: np.ndarray
    upper: np.ndarray


@dataclass(frozen=True, kw_only=True, eq=False)
class Box(Primitive):
    """A rectangle ``[t0, t1] × [y0, y1]``. ``y0``/``y1`` of ``None`` span the panel."""

    t0: float
    t1: float
    y0: float | None
    y1: float | None
    passed: bool | None = None


@dataclass(frozen=True, kw_only=True, eq=False)
class Marker(Primitive):
    x: float
    y: float
    passed: bool | None = None


@dataclass(frozen=True, kw_only=True, eq=False)
class HLine(Primitive):
    value: float
    passed: bool | None = None


@dataclass(frozen=True, kw_only=True, eq=False)
class VLine(Primitive):
    time: float


#: A grip's edge and the direction along its axis that is OUTWARD from the
#: target: ``+1`` towards larger values (or later times), ``-1`` the other
#: way. A lookup, not a default: an edge that is not here raises.
_GRIP_OUTWARD = {
    "upper": 1,
    "after": 1,
    "late": 1,
    "lower": -1,
    "before": -1,
    "early": -1,
}


def grip_edge(grip: Primitive) -> str:
    """A grip's edge: the segment of its id before the last — ``upper`` of
    ``…/points/upper/3``."""
    return grip.id.split("/")[-2]


def grip_outward(grip: Primitive) -> int:
    """``+1`` or ``-1``: which way along :func:`grip_axis` leads AWAY from
    the target — where the grip is drawn, just outside its edge."""
    return _GRIP_OUTWARD[grip_edge(grip)]


def grip_axis(grip: Marker | VLine) -> Literal["x", "y"]:
    """The ONE axis a grip moves on. A line stands at a time or a frequency:
    x. A marker's ``upper`` and ``lower`` are the edges of a value: y; its
    ``before`` and ``after`` those of a time: x."""
    if isinstance(grip, VLine):
        return "x"
    return "y" if grip_edge(grip) in ("upper", "lower") else "x"


@dataclass(frozen=True, kw_only=True, eq=False)
class Series(Primitive):
    x: np.ndarray
    y: np.ndarray


@dataclass(frozen=True, kw_only=True, eq=False)
class Window(Primitive):
    """The leaf's time window; either end ``None`` means open on that side."""

    start: float | None
    end: float | None
