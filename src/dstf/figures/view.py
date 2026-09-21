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

from ..comparison.algorithms import actual_on_reference_grid
from ..comparison.geometry import Band, Primitive, Series
from ..comparison.registry import summarize
from ..comparison.types import _EPS, ScoreSummary, VariableComparison
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
    #: The variable's result-file name — the key everywhere.
    name: str
    #: What a person reads for it (``display_label``): the expression the
    #: model wrote, or the name. Defaults to the name, so a view built
    #: without one draws exactly what it always drew.
    label: str = ""
    act_time: np.ndarray | None = None
    act_values: np.ndarray | None = None
    ref_time: np.ndarray | None = None
    ref_values: np.ndarray | None = None
    overlays: tuple[OverlayView, ...] = ()
    leaves: tuple[LeafView, ...] = ()
    errors: tuple[Series, ...] = ()
    #: Pre-decimation sample counts, stamped by :func:`decimate_view`.
    full_samples: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # "Defaults to the name" as a real value, not a read-time fallback:
        # every reader (the figure's hover, a family fold, a card title) sees
        # a label, and `replace` carries it.
        if not self.label:
            object.__setattr__(self, "label", self.name)

    @property
    def has_spectrum(self) -> bool:
        return any(p.panel == "spectrum" for leaf in self.leaves for p in leaf.geometry)


def _array(values) -> np.ndarray | None:
    if values is None:
        return None
    arr = np.asarray(values, dtype=float)
    return arr if arr.size else None


def _overlay_array(values) -> np.ndarray:
    # Lists or arrays; `or []` would ask an array for its truth value.
    return np.asarray([] if values is None else values, dtype=float)


def error_series(
    ref_time: np.ndarray | None,
    ref_values: np.ndarray | None,
    act_time: np.ndarray | None,
    act_values: np.ndarray | None,
) -> tuple[Series, ...]:
    """Signed, absolute and normalised error of the actual on the reference
    grid, through the comparator's own event-aware interpolation
    (:func:`~dstf.comparison.algorithms.actual_on_reference_grid`) and its
    normalisation: the reference range, or its magnitude for a constant
    reference. The error drawn is the error scored."""
    if ref_time is None or ref_values is None or act_time is None or act_values is None:
        return ()
    times, ref_on, act_on = actual_on_reference_grid(
        ref_time, ref_values, act_time, act_values
    )
    if len(times) == 0:
        return ()
    signed = act_on - ref_on
    absolute = np.abs(signed)
    span = float(np.max(ref_values) - np.min(ref_values))
    if span < _EPS:
        magnitude = float(np.max(np.abs(ref_values)))
        scale = magnitude if magnitude > _EPS else 1.0
    else:
        scale = span
    return (
        Series(id="error/signed", kind="error", x=times, y=signed),
        Series(id="error/abs", kind="error", x=times, y=absolute),
        Series(id="error/nrmse", kind="error", x=times, y=absolute / scale),
    )


def build_view(
    name: str,
    *,
    label: str = "",
    act_time=None,
    act_values=None,
    ref_time=None,
    ref_values=None,
    overlays: Sequence[dict] = (),
    leaves: Sequence[tuple[str, VariableComparison]] = (),
) -> VariableView:
    """Assemble a view from array-likes (lists or arrays), overlay dicts of
    the report's shape (``name`` / ``role`` / ``kind`` / ``time`` /
    ``values``), and ``(leaf_path, VariableComparison)`` pairs. ``label``
    is what a person reads for the variable; empty means its name."""
    at, av, rt, rv = (
        _array(act_time),
        _array(act_values),
        _array(ref_time),
        _array(ref_values),
    )
    return VariableView(
        name=name,
        label=label,
        act_time=at,
        act_values=av,
        ref_time=rt,
        ref_values=rv,
        overlays=tuple(
            OverlayView(
                name=str(ov.get("name") or ""),
                role=str(ov.get("role") or "companion"),
                kind=ov.get("kind"),
                time=_overlay_array(ov.get("time")),
                values=_overlay_array(ov.get("values")),
            )
            for ov in overlays
        ),
        leaves=tuple(
            LeafView(
                path=path,
                metric=vc.mode,
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


def _decimate_overlay(ov: OverlayView, max_samples: int) -> OverlayView:
    t2, v2 = lttb(ov.time, ov.values, max_samples)
    return OverlayView(
        name=ov.name,
        role=ov.role,
        kind=ov.kind,
        time=t2,
        values=v2,
        full_samples=len(ov.time) if ov.full_samples is None else ov.full_samples,
    )


def decimate_view(view: VariableView, max_samples: int) -> VariableView:
    """A copy under the sample budget: trajectories, overlays, error series
    and bands are LTTB-decimated; ``full_samples`` records what they were."""
    if max_samples is None or max_samples <= 0:
        return view
    at, av = _pair(view.act_time, view.act_values, max_samples)
    rt, rv = _pair(view.ref_time, view.ref_values, max_samples)

    # Pre-decimation counts, keyed by primitive id, so the figure can mark
    # every decimated trace — bands, spectra and error series included. A
    # count already stamped (a cached base view decimated again) is kept.
    full: dict[str, int] = dict(view.full_samples)
    full.setdefault("act", 0 if view.act_time is None else len(view.act_time))
    full.setdefault("ref", 0 if view.ref_time is None else len(view.ref_time))

    def _prim(p: Primitive) -> Primitive:
        if isinstance(p, Band):
            full.setdefault(p.id, len(p.time))
            t2, l2, u2 = decimate_band(p.time, p.lower, p.upper, max_samples)
            return replace(p, time=t2, lower=l2, upper=u2)
        if isinstance(p, Series):
            full.setdefault(p.id, len(p.x))
            x2, y2 = lttb(p.x, p.y, max_samples)
            return replace(p, x=x2, y=y2)
        return p

    leaves = tuple(
        replace(leaf, geometry=tuple(_prim(p) for p in leaf.geometry))
        for leaf in view.leaves
    )
    errors = tuple(_prim(s) for s in view.errors)
    return replace(
        view,
        act_time=at,
        act_values=av,
        ref_time=rt,
        ref_values=rv,
        overlays=tuple(_decimate_overlay(ov, max_samples) for ov in view.overlays),
        leaves=leaves,
        errors=errors,  # type: ignore[arg-type]
        full_samples=full,
    )
