"""What an editor refuses to stage because it can never score: a negative
tolerance — a number, or either side of a pair; a value's or a time's; the
criterion's or a table row's — and bounds out of order.

The EDITOR's rule, raised from a capability's ``act`` before anything is
staged, the way a window that ends before it starts is refused. It is not
the scorer's and not the spec parser's: a spec written by hand that holds
such a value is read, staged and scored exactly as it always was."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypeGuard

#: The keys that hold a tolerance, on a criterion and in a row of its tables.
TOLERANCES = ("tolerance", "time_tolerance")

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
    """Raise ``ValueError`` when the tolerance ``value`` held under ``key``
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
            raise ValueError(f"{where}{what} must not be negative; got {_num(side)}")


def refuse_bounds(metric: str, params: Mapping[str, object]) -> None:
    """Raise ``ValueError`` when ``params`` holds both bounds out of order:
    ``min_value 9 exceeds max_value 2`` — and, for a metric of
    :data:`STRICT_BOUNDS`, when they are equal."""
    lo, hi = params.get("min_value"), params.get("max_value")
    if not _is_number(lo) or not _is_number(hi):
        return
    if lo > hi:
        raise ValueError(f"min_value {_num(lo)} exceeds max_value {_num(hi)}")
    if lo == hi and metric in STRICT_BOUNDS:
        raise ValueError(
            f"min_value {_num(lo)} equals max_value {_num(hi)}; "
            f"a {metric} leaf needs min_value < max_value"
        )
