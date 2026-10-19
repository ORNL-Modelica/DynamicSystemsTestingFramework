"""A tolerance's two sides — the ONE place a number-or-pair is read or written.

A spec holds a tolerance as a number (both sides equal) or as the pair
``{"lower": a, "upper": b}``. Every scorer takes :class:`Sides`; a number
``v`` is ``Sides(v, v)``, so a spec holding only numbers scores as it did
before a pair existed. What the sides MEAN is the scorer's: ``actual −
target`` must lie in ``[−lower, upper]``; in time, ``lower`` is how much
earlier and ``upper`` how much later.
"""

from __future__ import annotations

import json
import math
import numbers
from dataclasses import dataclass
from typing import TypeGuard


@dataclass(frozen=True)
class Sides:
    """A tolerance with two sides. With ``d = actual - target``, a check passes
    when ``-lower <= d <= upper``. A spec writes one number for both sides, or
    ``{"lower": a, "upper": b}``.

    Attributes:
        lower: How far below its target the value may lie (in time: how much
            earlier).
        upper: How far above it (in time: how much later).
    """

    lower: float
    upper: float

    @property
    def equal(self) -> bool:
        return self.lower == self.upper


def _is_number(value: object) -> TypeGuard[int | float]:
    """A finite real number that is not a bool: stricter than
    ``float(value)``, which also takes ``True`` and the string ``"0.01"``,
    and no stricter on a number — a numpy scalar is one. An int too large
    for a float is not: it has no finite float."""
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


def is_base_tolerance(value: object) -> bool:
    """Whether ``value`` can be a test's BASE tolerance (the spec entry's or
    the reference JSON's ``comparison.tolerance``, the project's): ONE finite
    number. Not a pair — nrmse, the default metric, has one ceiling — and
    not a bool, a string or a list, which ``float()`` took or raised on;
    not NaN or an infinity, which every leaf then refused in its own words;
    and 0 or more: a negative ceiling failed every nrmse test (``--tolerance
    -1`` was taken)."""
    return _is_number(value) and value >= 0


def _as_json(value: object) -> str:
    """``value`` in a refusal, cut to 60 characters: as the JSON file holds
    it (``true``, ``NaN``, ``{"lower": …}``), not as Python spells it; its
    ``repr`` only where JSON cannot write it."""
    try:
        text = json.dumps(value, allow_nan=True, ensure_ascii=False)
    except (TypeError, ValueError):
        text = repr(value)
    if len(text) > 60:
        text = text[:60] + "…"
    return text


def base_tolerance_refused(where: str, value: object) -> str:
    """What a base tolerance that is not :func:`is_base_tolerance` is refused
    with — a test's comparison's ``error_message``, a project's config
    error: the rule, then where the value is written (``where``, in words)
    and what it is (:func:`_as_json`). Where a pair belongs is said to a
    dict alone: a string or a list was no attempt at one. A number is
    refused for being negative, and is told so."""
    if _is_number(value):
        return f"the base tolerance must be 0 or more ({where} is {_as_json(value)})"
    rule = "the base tolerance must be a number"
    if isinstance(value, dict):
        rule += "; a lower/upper pair belongs on a criterion"
    return f"{rule} ({where} is {_as_json(value)})"


#: The keys of a pair, and of nothing else: what :func:`parse_sides` reads.
PAIR_KEYS = ("lower", "upper")


def parse_sides(value: object, what: str) -> Sides:
    """``value`` as :class:`Sides`: a number is both sides, a dict whose keys
    are exactly ``lower`` and ``upper`` is the pair, a :class:`Sides` is
    itself. Anything else is a ``ValueError`` naming ``what`` and the value
    (:func:`_as_json`).
    No sign check: a negative side is not refused here, as a negative number
    never was."""
    if isinstance(value, Sides):
        return value
    if _is_number(value):
        return Sides(float(value), float(value))
    if isinstance(value, dict) and set(value) == set(PAIR_KEYS):
        lower, upper = value["lower"], value["upper"]
        if _is_number(lower) and _is_number(upper):
            return Sides(float(lower), float(upper))
    raise ValueError(
        f'{what} must be a number or {{"lower": …, "upper": …}}, got {_as_json(value)}'
    )


def parse_number(value: object, what: str) -> float:
    """``value`` as the ONE number a field without sides takes — a tube's
    minimum width, a floor under both of its sides. Anything else — a
    lower/upper pair, a string, a bool, a list, NaN or an infinity — is a
    ``ValueError`` naming ``what`` and the value (:func:`_as_json`), the
    way :func:`parse_sides` refuses what is no number or pair. No sign
    check here either."""
    if _is_number(value):
        return float(value)
    raise ValueError(f"{what} must be one number, got {_as_json(value)}")


def as_written(sides: Sides) -> float | dict:
    """``sides`` as data leaving Python (a tree view, a leaf's diagnostics):
    the number when the sides are equal, else the pair. NOT how the tool
    writes a spec — there the shape is the author's."""
    if sides.equal:
        return sides.upper
    return {"lower": sides.lower, "upper": sides.upper}


def bounds_text(min_value: float | None, max_value: float | None) -> str | None:
    """A criterion's declared bounds in words: ``in [a, b]``, ``≥ a`` or
    ``≤ b``; None with neither. What the Statistics card's requirement and
    a row's ``+`` both say of a bounds criterion."""
    if min_value is not None and max_value is not None:
        return f"in [{min_value:g}, {max_value:g}]"
    if min_value is not None:
        return f"≥ {min_value:g}"
    if max_value is not None:
        return f"≤ {max_value:g}"
    return None


def sides_text(sides: Sides, fmt: str = ".3e") -> str:
    """``sides`` in a sentence: the one number when equal (a minus as
    U+2212; a zero reads as zero whatever its sign bit), else
    :func:`signed_sides`."""
    if sides.equal:
        text = format(abs(sides.upper) if sides.upper == 0 else sides.upper, fmt)
        return f"−{text[1:]}" if text.startswith("-") else text
    return signed_sides(sides, fmt)


def signed_sides(
    sides: Sides, fmt: str = ".3e", *, scale: float = 1.0, unit: str = ""
) -> str:
    """``−lower / +upper`` (U+2212, as the report's other signs) — the
    band's two edges as signed numbers, each ``scale``d and followed by
    ``unit`` (``100`` and ``%`` for a relative pair), so a negative lower
    side, whose edge lies above the target, reads ``+…``. A zero side reads
    by its side, not its sign bit: ``-0.0`` is ``0.0``."""
    lower = _signed(-sides.lower * scale, fmt, zero="−")
    upper = _signed(sides.upper * scale, fmt, zero="+")
    return f"{lower}{unit} / {upper}{unit}"


def signed(value: float, fmt: str = ".3e") -> str:
    """A deviation in a sentence: ``value`` with its sign always — ``+``, or
    U+2212 for the minus, as :func:`signed_sides` writes an edge; a zero
    reads ``+``."""
    return _signed(value, fmt, zero="+")


def _signed(value: float, fmt: str, *, zero: str) -> str:
    """``value`` with its sign, the minus as U+2212; a zero takes ``zero``.
    The sign only: an exponent's ``-`` stays as the format wrote it."""
    text = format(abs(value) if value == 0 else value, f"+{fmt}")
    if value == 0:
        return zero + text[1:]
    return f"−{text[1:]}" if text.startswith("-") else text
