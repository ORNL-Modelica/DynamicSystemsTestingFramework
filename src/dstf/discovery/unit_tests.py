"""The one place a UnitTests component's variables are named.

A variable's NAME is the one it has in the result file —
``<instance>.x[i]``. The text the model wrote in ``x={...}`` is its
EXPRESSION: a label that rides beside the name, never a key. The
backends that extract a result, and the variable filter OpenModelica
simulates under, ask here rather than spelling ``unitTests.x[i]``
themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .test_registry import TestModel


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
    variable, ``f"{x_expressions[0]}[{i}]"`` when it wrote a single array
    (``x=y``) covering all of them, and ``""`` for any other shape —
    ``cat(1, a, b)`` for three variables says nothing about which piece is
    which, and a wrong label is worse than none.
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
        out.append(UnitTestVariable(f"{test.unit_tests_instance}.x[{i}]", expression))
    return out


__all__ = ["UnitTestVariable", "unit_test_variables"]
