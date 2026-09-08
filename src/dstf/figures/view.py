"""The library-neutral picture of one variable.

Both tiers build a :class:`VariableView` the same way — from the report's
trajectory dicts and the evaluated leaves — and hand it to
:func:`~.plotly.variable_figure`. Decimation happens on the view
(:func:`decimate_view`), before any trace exists, so an envelope's two edges
stay on one time axis.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace

import numpy as np

from ..comparison.geometry import Band, Primitive, Series
from ..comparison.registry import summarize
from ..comparison.types import ScoreSummary, VariableComparison
from .decimate import decimate_band, lttb


@dataclass(frozen=True)
class OverlayView:
    name: str
    role: str
    kind: str | None
    time: np.ndarray
    values: np.ndarray
    full_samples: int | None = None


@dataclass(frozen=True)
class LeafView:
    path: str
    metric: str
    passed: bool
    geometry: tuple[Primitive, ...]
    summary: ScoreSummary


@dataclass(frozen=True)
class VariableView:
    name: str
    act_time: np.ndarray | None = None
    act_values: np.ndarray | None = None
    ref_time: np.ndarray | None = None
    ref_values: np.ndarray | None = None
    overlays: tuple[OverlayView, ...] = ()
    leaves: tuple[LeafView, ...] = ()
    errors: tuple[Series, ...] = ()
    #: Pre-decimation sample counts, stamped by :func:`decimate_view`.
    full_samples: dict[str, int] = field(default_factory=dict)

    @property
    def has_spectrum(self) -> bool:
        return any(p.panel == "spectrum" for leaf in self.leaves for p in leaf.geometry)


def _array(values) -> np.ndarray | None:
    if values is None:
        return None
    arr = np.asarray(values, dtype=float)
    return arr if arr.size else None


def error_series(
    ref_time: np.ndarray | None,
    ref_values: np.ndarray | None,
    act_time: np.ndarray | None,
    act_values: np.ndarray | None,
) -> tuple[Series, ...]:
    """Signed, absolute and range-normalised error of the actual on the
    reference grid — the same linear interpolation the comparator uses."""
    if ref_time is None or ref_values is None or act_time is None or act_values is None:
        return ()
    act_on_ref = np.interp(ref_time, act_time, act_values)
    signed = act_on_ref - ref_values
    absolute = np.abs(signed)
    span = float(np.max(ref_values) - np.min(ref_values))
    per_point = absolute / span if span > 1e-15 else absolute
    return (
        Series(id="error/signed", kind="error", x=ref_time, y=signed),
        Series(id="error/abs", kind="error", x=ref_time, y=absolute),
        Series(id="error/nrmse", kind="error", x=ref_time, y=per_point),
    )


def build_view(
    name: str,
    *,
    act_time=None,
    act_values=None,
    ref_time=None,
    ref_values=None,
    overlays: Sequence[dict] = (),
    leaves: Sequence[tuple[str, VariableComparison]] = (),
) -> VariableView:
    """Assemble a view from array-likes (lists or arrays), overlay dicts of
    the report's shape (``name`` / ``role`` / ``kind`` / ``time`` /
    ``values``), and ``(leaf_path, VariableComparison)`` pairs."""
    at, av, rt, rv = (
        _array(act_time),
        _array(act_values),
        _array(ref_time),
        _array(ref_values),
    )
    return VariableView(
        name=name,
        act_time=at,
        act_values=av,
        ref_time=rt,
        ref_values=rv,
        overlays=tuple(
            OverlayView(
                name=str(ov.get("name") or ""),
                role=str(ov.get("role") or "companion"),
                kind=ov.get("kind"),
                time=np.asarray(ov.get("time") or [], dtype=float),
                values=np.asarray(ov.get("values") or [], dtype=float),
            )
            for ov in overlays
        ),
        leaves=tuple(
            LeafView(
                path=path,
                metric=vc.mode or "nrmse",
                passed=bool(vc.passed),
                geometry=tuple(vc.geometry),
                summary=summarize(vc),
            )
            for path, vc in leaves
        ),
        errors=error_series(rt, rv, at, av),
    )


def _pair(time, values, n):
    if time is None or values is None:
        return time, values
    return lttb(time, values, n)


def decimate_view(view: VariableView, max_samples: int) -> VariableView:
    """A copy under the sample budget: trajectories, overlays, error series
    and bands are LTTB-decimated; ``full_samples`` records what they were."""
    if max_samples is None or max_samples <= 0:
        return view
    at, av = _pair(view.act_time, view.act_values, max_samples)
    rt, rv = _pair(view.ref_time, view.ref_values, max_samples)

    def _prim(p: Primitive) -> Primitive:
        if isinstance(p, Band):
            t2, l2, u2 = decimate_band(p.time, p.lower, p.upper, max_samples)
            return replace(p, time=t2, lower=l2, upper=u2)
        if isinstance(p, Series):
            x2, y2 = lttb(p.x, p.y, max_samples)
            return replace(p, x=x2, y=y2)
        return p

    return replace(
        view,
        act_time=at,
        act_values=av,
        ref_time=rt,
        ref_values=rv,
        overlays=tuple(
            OverlayView(
                name=ov.name,
                role=ov.role,
                kind=ov.kind,
                time=lttb(ov.time, ov.values, max_samples)[0],
                values=lttb(ov.time, ov.values, max_samples)[1],
                full_samples=len(ov.time),
            )
            for ov in view.overlays
        ),
        leaves=tuple(
            replace(leaf, geometry=tuple(_prim(p) for p in leaf.geometry))
            for leaf in view.leaves
        ),
        errors=tuple(_prim(s) for s in view.errors),  # type: ignore[misc]
        full_samples={
            "act": 0 if view.act_time is None else len(view.act_time),
            "ref": 0 if view.ref_time is None else len(view.ref_time),
        },
    )
