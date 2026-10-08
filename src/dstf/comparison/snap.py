"""The one snap rule (D149): a value the POINTER placed is written at a
readable step.

A drag released on the plot wrote where the pointer happened to stop, to 12
significant digits — ``0.373778382112`` for a tolerance nobody would type.
A value placed by the pointer (a drag's release, a click, and the preview
of either) is rounded to a step of the axis it was placed on: the power of
ten nearest to a thousandth of what that axis SHOWED, so a plot drawn about
a thousand pixels wide snaps to about a pixel, and a zoom makes the step
finer. A typed value is never snapped: this is called for a position, never
for a field.
"""

from __future__ import annotations

import math


def _exponent(span: object) -> int | None:
    """The step's power of ten for an axis showing ``span``, or None where
    ``span`` is no positive finite number: then nothing is snapped."""
    if isinstance(span, bool) or not isinstance(span, (int, float)):
        return None
    if not math.isfinite(span) or span <= 0:
        return None
    return round(math.log10(span / 1000))


def snap_step(span: object) -> float | None:
    """The step a value placed on an axis showing ``span`` is rounded to:
    ``10 ** round(log10(span / 1000))`` — nearest in the exponent, so a span
    of 3.1 snaps to 0.001 and one of 3.2 to 0.01. None for no span."""
    exponent = _exponent(span)
    return None if exponent is None else 10.0**exponent


def snapped(value: float, span: object, *, fraction: bool = False) -> float:
    """``value`` as a pointer on an axis showing ``span`` writes it.

    A coordinate, and a tolerance side in the axis' own units, is rounded
    to a multiple of :func:`snap_step` — as a decimal, so the result is the
    float a person would type (``0.374``, never ``0.37400000000000005``);
    a value exactly half way between two multiples goes to the even one.
    A RELATIVE side (``fraction``: a fraction of its target, on no axis) is
    rounded to 3 significant digits. A value that snaps to zero is ``0.0``,
    never ``-0.0``. With no span — a caller that is no pointer — ``value``
    comes back as it is.
    """
    exponent = _exponent(span)
    if exponent is None or not math.isfinite(value):
        return value
    if fraction:
        return float(f"{value:.3g}") + 0.0
    return round(float(value), -exponent) + 0.0
