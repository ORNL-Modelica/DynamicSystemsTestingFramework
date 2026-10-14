"""What an editor refuses to stage because it can never score: a negative
tolerance — a number, or either side of a pair; a value's or a time's; the
criterion's or a table row's — a tube's constant width whose sides sum
below zero (a negative number, or a pair that leaves nothing between its
bounds) with no positive minimum width under it, and bounds out of order.
And one value that scores but says nothing: a tube's minimum width below
zero, a floor under no width.

The EDITOR's rule, raised from a capability's ``act`` before anything is
staged, the way a window that ends before it starts is refused. It is not
the scorer's and not the spec parser's: a spec written by hand that holds
such a value is read, staged and scored exactly as it always was."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypeGuard


class Refused(ValueError):
    """A value an editor will not stage, said as its reason. A person's own
    — a negative tolerance on the way to a good one, bounds out of order,
    a required cell left blank — so it is shown and never logged
    (`errors.USER_ERRORS`): as a plain `ValueError`, which a bug's also
    is, each keystroke of one logged a traceback."""


#: The keys that hold a tolerance, on a criterion and in a row of its tables.
TOLERANCES = ("tolerance", "time_tolerance")

#: The keys that hold a tube's constant width (``TubeConfig``).
WIDTHS = ("tube_abs", "tube_rel")

#: The key that holds a tube's minimum width: ONE number, a floor under
#: both sides.
MIN_WIDTH = "tube_min_width"

#: The metrics whose two bounds may not be EQUAL either: the scorer refuses
#: a scalar leaf with ``min_value >= max_value`` when it is built
#: (``modes._validate_scalar_config``), as its drag editor does
#: (``handles.edit_scalar_handle``). A range with equal bounds scores.
STRICT_BOUNDS = frozenset({"scalar"})

_SIDES = ("lower", "upper")


def _is_number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _num(value: float) -> str:
    """A number in a refusal, to 12 significant digits, its minus as U+2212
    (the report's sign). The sign only: an exponent's ``-`` stays."""
    text = f"{float(value):.12g}"
    return f"−{text[1:]}" if text.startswith("-") else text


def refuse_negative(
    key: str,
    value: object,
    side_names: tuple[str, str] | None = None,
    where: str = "",
) -> None:
    """Raise :class:`Refused` when the tolerance ``value`` held under ``key``
    is negative: ``tolerance must not be negative; got −0.5`` for a number,
    ``tolerance (lower side) must not be negative; got −0.5`` for a side of
    a pair — named as its fields are where they have words for it
    (``side_names``: a time tolerance's ``early`` / ``late``), ``where``
    before it (``"row 1: "``). A zero is a tolerance. Anything that is no
    number or pair of numbers is not this rule's to refuse."""
    if isinstance(value, dict):
        named = dict(zip(_SIDES, side_names or _SIDES))
        sides = [(f"{key} ({named[s]} side)", value.get(s)) for s in _SIDES]
    else:
        sides = [(key, value)]
    for what, side in sides:
        if _is_number(side) and side < 0:
            raise Refused(f"{where}{what} must not be negative; got {_num(side)}")


def refuse_negative_width(key: str, value: object, floor: object) -> None:
    """Raise :class:`Refused` when the tube width ``value`` held under ``key``
    puts the tube's upper bound under its lower one, so that nothing is
    ever inside it (it was staged, and read "0.0% inside tube"): a negative
    NUMBER — both sides — ``tube_abs must not be negative; got −0.3``, and
    a PAIR of numbers whose sides sum below zero, ``tube_abs sides must not
    sum below zero; got lower −0.1, upper −0.5``. The sum, not either
    side: a pair with one side negative and a sum above zero is a band
    shifted off its reference, which a run can lie in, and one whose sides
    sum to exactly zero is a band of no width — a zero is a tolerance.

    Only where ``floor``, the leaf's minimum width, is no positive number.
    A positive one widens BOTH sides to itself
    (``algorithms._tube_bounds``), so no width over it leaves nothing
    inside: ``{"lower": -0.1, "upper": -0.5}`` over a floor of 0.2 is a
    tube 0.2 either side of its reference. Such a leaf, written by hand,
    was refused every edit — a window end, and the Min width that would
    repair one with no floor — with the width's sentence. A floor that is
    absent, zero, negative or no number floors nothing, and the width is
    refused as it was: the floor cleared from such a leaf too."""
    if _is_number(floor) and floor > 0:
        return
    if _is_number(value):
        refuse_negative(key, value)
        return
    if not isinstance(value, dict):
        return
    lower, upper = value.get("lower"), value.get("upper")
    if _is_number(lower) and _is_number(upper) and lower + upper < 0:
        raise Refused(
            f"{key} sides must not sum below zero; "
            f"got lower {_num(lower)}, upper {_num(upper)}"
        )


def refuse_negative_floor(key: str, value: object) -> None:
    """Raise :class:`Refused` when the tube's minimum width ``value`` held
    under ``key`` is a negative number: ``tube_min_width must not be
    negative; got −0.3``. A floor below zero is under every width: it
    floors nothing, and the criterion scores as if the field were empty —
    which is what the field should then be. Only a NUMBER is this rule's:
    the floor takes one, and anything else is the leaf's config error,
    said by its row (``comparison.sides.parse_number``)."""
    if _is_number(value):
        refuse_negative(key, value)


def refuse_bounds(metric: str, params: Mapping[str, object]) -> None:
    """Raise :class:`Refused` when ``params`` holds both bounds out of order:
    ``min_value 9 exceeds max_value 2`` — and, for a metric of
    :data:`STRICT_BOUNDS`, when they are equal."""
    lo, hi = params.get("min_value"), params.get("max_value")
    if not _is_number(lo) or not _is_number(hi):
        return
    if lo > hi:
        raise Refused(f"min_value {_num(lo)} exceeds max_value {_num(hi)}")
    if lo == hi and metric in STRICT_BOUNDS:
        raise Refused(
            f"min_value {_num(lo)} equals max_value {_num(hi)}; "
            f"a {metric} criterion needs min_value < max_value"
        )
