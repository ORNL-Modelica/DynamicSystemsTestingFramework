"""The one place a UnitTests component's variables are named.

A variable's NAME is the one it has in the result file —
``<instance>.x[i]``. The text the model wrote in ``x={...}`` is its
EXPRESSION: a label that rides beside the name, never a key. The
backends that extract a result, and the variable filter OpenModelica
simulates under, ask here rather than spelling ``unitTests.x[i]``
themselves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .test_registry import TestModel


#: The array the component tracks, ``x={...}`` (see :func:`unit_test_variables`).
_ARRAY = "x"


def unit_test_name(instance: str, index: int) -> str:
    """``<instance>.x[i]`` — the result-file name of element ``index``."""
    return f"{instance}.{_ARRAY}[{index}]"


def is_unit_test_variable(test: TestModel, name: str) -> bool:
    """Whether ``name`` is an element of the test's UnitTests component,
    ``<instance>.x[i]`` — declared through the component or tracked by that
    name. Each is its own test quantity, whatever its expression reads as,
    so no plot folds it into a family with another (the owner's ruling O3,
    2026-09-28)."""
    head = f"{test.unit_tests_instance}.{_ARRAY}["
    # ASCII digits: `str.isdigit` takes `²` and `١` for one too
    return (
        name.startswith(head)
        and name.endswith("]")
        and re.fullmatch(r"[0-9]+", name[len(head) : -1]) is not None
    )


@dataclass(frozen=True)
class UnitTestVariable:
    """One tracked variable of the component: its result-file name and the
    expression the model wrote for it (``""`` when nothing said which
    expression belongs to which index)."""

    name: str
    expression: str


def unit_test_variables(test: TestModel) -> list[UnitTestVariable]:
    """The component's variables in index order, empty for a test with no
    such component (``source == "spec"``, or no variables declared).

    The component is assumed to name its tracked array ``x`` — the bundled
    one does, and a custom component reached through a JSON recognizer is
    taken to follow it.

    The expression is ``x_expressions[i - 1]`` when the model wrote one per
    variable, and ``f"{x_expressions[0]}[{i}]"`` when it wrote a SINGLE
    expression covering all of them — an array (``x=y``) or a concatenation
    (``x=cat(1, a, b)``), whose element ``i`` reads as ``y[i]`` /
    ``cat(1, a, b)[i]``.

    Any other shape leaves the expression ``""``: two expressions for three
    variables says nothing about which piece is which, and a wrong label is
    worse than none.
    """
    if test.source not in ("unit_tests", "both") or test.n_vars <= 0:
        return []
    exprs = test.x_expressions
    out: list[UnitTestVariable] = []
    for i in range(1, test.n_vars + 1):
        if len(exprs) == test.n_vars:
            expression = exprs[i - 1]
        elif len(exprs) == 1:
            expression = f"{exprs[0]}[{i}]"
        else:
            expression = ""
        out.append(
            UnitTestVariable(unit_test_name(test.unit_tests_instance, i), expression)
        )
    return out


__all__ = [
    "UnitTestVariable",
    "is_unit_test_variable",
    "unit_test_name",
    "unit_test_variables",
]
