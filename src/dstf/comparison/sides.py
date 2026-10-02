"""A tolerance's two sides — the ONE place a number-or-pair is read or written.

A spec holds a tolerance as a number (both sides equal) or as the pair
``{"lower": a, "upper": b}``. Every scorer takes :class:`Sides`; a number
``v`` is ``Sides(v, v)``, so a spec holding only numbers scores as it did
before a pair existed. What the sides MEAN is the scorer's: ``actual −
target`` must lie in ``[−lower, upper]``; in time, ``lower`` is how much
earlier and ``upper`` how much later.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TypeGuard


@dataclass(frozen=True)
class Sides:
    lower: float
    upper: float

    @property
    def equal(self) -> bool:
        return self.lower == self.upper


def _is_number(value: object) -> TypeGuard[int | float]:
    """A finite number that is not a bool: stricter than ``float(value)``,
    which also takes ``True`` and the string ``"0.01"``."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def parse_sides(value: object, what: str) -> Sides:
    """``value`` as :class:`Sides`: a number is both sides, a dict whose keys
    are exactly ``lower`` and ``upper`` is the pair, a :class:`Sides` is
    itself. Anything else is a ``ValueError`` naming ``what`` and the value.
    No sign check: a negative side is not refused here, as a negative number
    never was."""
    if isinstance(value, Sides):
        return value
    if _is_number(value):
        return Sides(float(value), float(value))
    if isinstance(value, dict) and set(value) == {"lower", "upper"}:
        lower, upper = value["lower"], value["upper"]
        if _is_number(lower) and _is_number(upper):
            return Sides(float(lower), float(upper))
    raise ValueError(
        f'{what} must be a number or {{"lower": …, "upper": …}}, got {value!r}'
    )


def as_written(sides: Sides) -> float | dict:
    """``sides`` as data leaving Python (a tree view, a leaf's diagnostics):
    the number when the sides are equal, else the pair. NOT how the tool
    writes a spec — there the shape is the author's."""
    if sides.equal:
        return sides.upper
    return {"lower": sides.lower, "upper": sides.upper}


def sides_text(sides: Sides, fmt: str = ".3e") -> str:
    """``sides`` in a sentence: the one number when equal, else
    ``−lower / +upper`` (U+2212, as the report's other signs)."""
    if sides.equal:
        return format(sides.upper, fmt)
    return f"−{sides.lower:{fmt}} / +{sides.upper:{fmt}}"
