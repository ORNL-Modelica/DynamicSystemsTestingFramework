"""A new criterion's first values, from the data it will score.

A leaf of `range`, `tube`, `scalar` or `dominant-frequency` with only a
`metric` and a `variable` is misconfigured or always fails: `range` wants a
bound, `tube` a width mode, `scalar` a quantity, and `dominant-frequency`
scores but FAILs "no peaks declared". So the tool seeds one when it is
added or its metric is switched: a pure function of the reference series
(else the actual one) per metric, registered on its
:attr:`~.registry.MetricDescriptor.seed`.

A seed lands in a spec file a person reads, so its values are short — but
rounded AWAY from the data, at the leading digit of the margin it leaves:
rounded to 6 significant digits, a range over 101325 +- 0.001 got
``min_value == max_value`` and failed on the data it was seeded from.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .algorithms import _compute_fft_spectrum, _find_top_n_peaks
from .registry import metric as metric_descriptor

#: The margin a seed leaves around the data, as a fraction of its span.
SEED_MARGIN = 0.05
#: The widest relative window a peak is seeded with. Two bins around a peak
#: at FFT bin 1 is +-200%, a window that matches anything.
MAX_PEAK_TOLERANCE = 0.5


@dataclass(frozen=True)
class SeedData:
    """The ``(time, values)`` a new criterion will score, either may be
    missing."""

    ref: tuple[np.ndarray, np.ndarray] | None
    act: tuple[np.ndarray, np.ndarray] | None

    def series(self) -> tuple[np.ndarray, np.ndarray] | None:
        """The reference, else the actual; None when neither has a finite value."""
        for s in (self.ref, self.act):
            if s is not None and np.isfinite(s[1]).any():
                return s
        return None


def _short(x: float) -> float:
    return float(f"{float(x):.6g}")


def _outward(x: float, margin: float, up: bool) -> float:
    """``x`` rounded away from the data at the margin's leading digit:
    readable, and never inside the data (101325 +- 0.001 kept a 6-digit
    rounding's min == max)."""
    q = 10.0 ** math.floor(math.log10(margin))
    n = math.ceil(x / q) if up else math.floor(x / q)
    return float(f"{n * q:.12g}")


def _span(v: np.ndarray) -> tuple[float, float, float]:
    """The finite data's min, max and span; a flat series' span is its
    magnitude (else 1), so a margin is never zero."""
    finite = v[np.isfinite(v)]
    lo, hi = float(finite.min()), float(finite.max())
    span = hi - lo
    if span <= 0.0:
        span = abs(hi) or 1.0
    return lo, hi, span


def seed_range(data: SeedData) -> dict:
    """Both bounds, the data's span plus 5% either side."""
    s = data.series()
    if s is None:
        return {}
    lo, hi, span = _span(s[1])
    m = SEED_MARGIN * span
    return {
        "min_value": _outward(lo - m, m, up=False),
        "max_value": _outward(hi + m, m, up=True),
    }


def seed_tube(data: SeedData) -> dict:
    """A band 5% of the data's span wide around the reference."""
    s = data.series()
    if s is None:
        return {}
    m = SEED_MARGIN * _span(s[1])[2]
    return {"tube_width_mode": "band", "tube_abs": _outward(m, m, up=True)}


def seed_scalar(data: SeedData) -> dict:
    """The final value: within 1% of the baseline's where there is a
    reference, else between bounds 5% of its magnitude either side."""
    s = data.series()
    if s is None:
        return {}
    quantity = {"reduce": "final"}
    if data.ref is not None:
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
    whichever is wider, and never wider than `MAX_PEAK_TOLERANCE`."""
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
    tol = min(max(SEED_MARGIN, 2.0 * bin_width / f), MAX_PEAK_TOLERANCE)
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
