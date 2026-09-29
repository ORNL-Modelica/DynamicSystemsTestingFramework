"""A new criterion's first values, from the data it will score.

A leaf of `range`, `tube`, `scalar` or `dominant-frequency` with only a
`metric` and a `variable` is misconfigured or always fails: `range` wants a
bound, `tube` a width mode, `scalar` a quantity, and `dominant-frequency`
scores but FAILs "no peaks declared". So the tool seeds one when it is
added or its metric is switched: a pure function of the reference series
(else the actual one), cut to the criterion's window when it has one, per
metric, registered on its :attr:`~.registry.MetricDescriptor.seed`.

A seed lands in a spec file a person reads, so its values are short — but
rounded AWAY from the data, at the leading digit of the margin it leaves:
rounded to 6 significant digits, a range over 101325 +- 0.001 got
``min_value == max_value`` and failed on the data it was seeded from. Where
the short form would land back on the data, the value is left unrounded.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .algorithms import _compute_fft_spectrum, _find_top_n_peaks
from .registry import metric as metric_descriptor

#: The margin a seed leaves around the data, as a fraction of its span.
SEED_MARGIN = 0.05
#: ... and never less than this fraction of the data's magnitude: a reference
#: stored at 7 significant digits is rounded coarser than 5% of a small span.
SEED_MARGIN_FLOOR = 1e-6
#: The widest relative window a peak is seeded with. Two bins around a peak
#: at FFT bin 1 is +-200%, a window that matches anything; a peak whose two
#: bins are wider (one in the lowest three bins) is not seeded at all.
MAX_PEAK_TOLERANCE = 0.5


@dataclass(frozen=True)
class SeedData:
    """The ``(time, values)`` a new criterion will score, either may be
    missing."""

    ref: tuple[np.ndarray, np.ndarray] | None
    act: tuple[np.ndarray, np.ndarray] | None

    def within(self, window: dict | None) -> SeedData:
        """Both series cut to ``window``'s ``start`` / ``end`` (either may
        be absent), ends included as the scorer's window includes them: a
        windowed criterion is seeded from the samples it scores."""
        if not window:
            return self
        # float(), as `parse_metric_tree` reads them: a hand-written
        # ``"start": "2"`` parses, and compared raw raised UFuncTypeError.
        start = float(window.get("start", -math.inf))
        end = float(window.get("end", math.inf))

        def cut(s: tuple[np.ndarray, np.ndarray] | None):
            if s is None:
                return None
            keep = (s[0] >= start) & (s[0] <= end)
            return s[0][keep], s[1][keep]

        return SeedData(ref=cut(self.ref), act=cut(self.act))

    def series(self) -> tuple[np.ndarray, np.ndarray] | None:
        """The reference, else the actual; None when neither has a finite value."""
        for s in (self.ref, self.act):
            if s is not None and np.isfinite(s[1]).any():
                return s
        return None


def _short(x: float) -> float:
    return float(f"{float(x):.6g}")


def _outward(x: float, margin: float, up: bool) -> float:
    """``x`` rounded away from the data at the margin's leading digit, when
    that short form still lies on ``x``'s far side; else ``x`` itself.
    Readable where it can be, and never inside the data: 101325 +- 0.001
    kept a 6-digit rounding's min == max, and a margin below 12 significant
    digits of the value (1e5 +- 1e-11) rounded the 12-digit form back onto
    the data."""
    q = 10.0 ** math.floor(math.log10(margin))
    n = math.ceil(x / q) if up else math.floor(x / q)
    short = float(f"{n * q:.12g}")
    return short if (short >= x if up else short <= x) else x


def _span(v: np.ndarray) -> tuple[float, float, float]:
    """The finite data's min, max and span; a flat series' span is its
    magnitude (else 1), so a margin is never zero."""
    finite = v[np.isfinite(v)]
    lo, hi = float(finite.min()), float(finite.max())
    span = hi - lo
    if span <= 0.0:
        span = abs(hi) or 1.0
    return lo, hi, span


def _margin(v: np.ndarray) -> float:
    """5% of the finite data's span, and at least `SEED_MARGIN_FLOOR` of its
    magnitude."""
    lo, hi, span = _span(v)
    # OilWater_PTHX's `STHX.tube.mediums[2].h`: seeded from a 7-digit
    # reference as [130450.58, 130451.02], its float32 result reached 130451.03125.
    return max(SEED_MARGIN * span, SEED_MARGIN_FLOOR * max(abs(lo), abs(hi)))


def seed_range(data: SeedData) -> dict:
    """Both bounds, the data's span plus `_margin` either side."""
    s = data.series()
    if s is None:
        return {}
    lo, hi, _ = _span(s[1])
    m = _margin(s[1])
    return {
        "min_value": _outward(lo - m, m, up=False),
        "max_value": _outward(hi + m, m, up=True),
    }


def seed_tube(data: SeedData) -> dict:
    """A band `_margin` wide around the reference."""
    s = data.series()
    if s is None:
        return {}
    m = _margin(s[1])
    return {"tube_width_mode": "band", "tube_abs": _outward(m, m, up=True)}


def seed_scalar(data: SeedData) -> dict:
    """The final value: within 1% of the baseline's where the seed reads
    the reference, else between bounds 5% of its magnitude either side."""
    s = data.series()
    if s is None:
        return {}
    quantity = {"reduce": "final"}
    # Not `data.ref is not None`: a reference with no finite sample (all
    # NaN, or none inside the window) seeds from the actual, and a
    # tolerance against it failed on its own data.
    if s is data.ref:
        return {"quantity": quantity, "tolerance_mode": "rel", "tolerance": 0.01}
    final = float(s[1][np.isfinite(s[1])][-1])
    half = SEED_MARGIN * (abs(final) or _span(s[1])[2])
    return {
        "quantity": quantity,
        "min_value": _outward(final - half, half, up=False),
        "max_value": _outward(final + half, half, up=True),
    }


def seed_dominant_frequency(data: SeedData) -> dict:
    """The strongest peak of the data's spectrum, 5% wide or two bins,
    whichever is wider. None when two bins are wider than
    `MAX_PEAK_TOLERANCE` — a peak in the lowest three bins is a trend's
    leakage at ~1/span, not a frequency: over TRANSFORM, 279 of 322 varying
    variables have their strongest peak there. The leaf stays bare, and
    scoring says to declare a peak."""
    s = data.series()
    if s is None:
        return {}
    freqs, spectrum = _compute_fft_spectrum(*s)
    peaks = _find_top_n_peaks(freqs, spectrum, 1)
    if not peaks:
        return {}
    f = peaks[0][0]
    bin_width = float(freqs[1] - freqs[0])
    # At least two bins wide: the bins follow the SPAN, so a peak seeded on
    # a 10 s reference sits between the bins of a 9 s result, and matching
    # needs a local maximum inside the window.
    two_bins = 2.0 * bin_width / f
    if two_bins > MAX_PEAK_TOLERANCE:
        return {}
    tol = max(SEED_MARGIN, two_bins)
    return {
        "peaks": [
            {"freq": _short(f), "tolerance": _short(tol), "tolerance_mode": "rel"}
        ]
    }


def seeded_leaf(metric: str, variable: str, data: SeedData) -> dict:
    """``{"metric", "variable"}`` plus the metric's seed, if it has one."""
    leaf = {"metric": metric, "variable": variable}
    desc = metric_descriptor(metric)
    if desc is None or desc.seed is None:
        return leaf
    return {**leaf, **desc.seed(data)}


#: Diagnostics whose kind is known by name (K1): a count is scored exact, a
#: time within 10%. A tiny model's CPUtime can end on a whole 0.0 or 1.0:
#: read by its values alone it is a count, and a next run's 0.01 s fails.
COUNTS = frozenset({"EventCounter"})
TIMES = frozenset({"CPUtime"})


def seed_diagnostic(name: str, values: np.ndarray | None) -> dict:
    """A criterion on the diagnostic ``name`` (the plan's K1): its stored
    final through a quantity path, ``tolerance 0 abs`` for a count and
    ``0.1 rel`` otherwise. A diagnostic of neither known kind is a count
    when every finite sample is a whole number. ``variable`` is the
    diagnostic's name: parsed without one, the leaf's variable becomes the
    quantity text, and its row reads ``scalar · CPUtime.final``.

    A time (`TIMES`) is seeded inside a ``warn``: a run's CPU time moves
    with the machine's load — under ``--parallel`` most of all — and 10%
    would fail a test whose model did not change. A count stays bare: it
    only moves when the model does."""
    finite = values[np.isfinite(values)] if values is not None else np.array([])
    count = name in COUNTS or (
        name not in TIMES
        and finite.size > 0
        and bool(np.all(finite == np.round(finite)))
    )
    leaf = {
        "metric": "scalar",
        "variable": name,
        "quantity": f"{name}.final",
        "tolerance": 0 if count else 0.1,
        "tolerance_mode": "abs" if count else "rel",
    }
    if name in TIMES:
        return {"combinator": "warn", "children": [leaf]}
    return leaf
