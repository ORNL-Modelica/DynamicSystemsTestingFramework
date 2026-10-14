"""The one rule that names a UnitTests component's variables.

A variable's NAME is the result file's — ``<instance>.x[i]``. What the
model wrote in ``x={...}`` is its EXPRESSION: a label recorded beside the
name, never a key. Every site that used to spell ``unitTests.x[i]`` (or
relabel it) asks this function instead.
"""

from __future__ import annotations

from dstf.discovery.test_registry import TestModel
from dstf.discovery.unit_tests import is_unit_test_variable, unit_test_variables


def _test(n_vars, x_expressions, instance="unitTests", source="unit_tests", **kw):
    return TestModel(
        model_id="Lib.Examples.M",
        source_file=None,
        source_package="Lib.Examples",
        short_name="M",
        n_vars=n_vars,
        x_expressions=list(x_expressions),
        unit_tests_instance=instance,
        source=source,
        **kw,
    )


def _pairs(test):
    return [(v.name, v.expression) for v in unit_test_variables(test)]


def test_one_expression_per_variable_is_that_variables_expression():
    assert _pairs(_test(2, ["a.Q", "pipe.T[1]"])) == [
        ("unitTests.x[1]", "a.Q"),
        ("unitTests.x[2]", "pipe.T[1]"),
    ]


def test_one_expression_for_many_variables_is_indexed():
    """``x=y`` where y is an array: the model named the array, not each
    element, so element i reads as ``y[i]``."""
    assert _pairs(_test(2, ["x"])) == [
        ("unitTests.x[1]", "x[1]"),
        ("unitTests.x[2]", "x[2]"),
    ]


def test_a_single_concatenation_for_many_variables_is_indexed_too():
    """``x=cat(1, a, b)`` is one expression covering the whole array, so it
    indexes like ``x=y`` does. (It is NOT the mismatch case: that is two
    expressions for three variables, below.)"""
    assert _pairs(_test(3, ["cat(1, a, b)"])) == [
        ("unitTests.x[1]", "cat(1, a, b)[1]"),
        ("unitTests.x[2]", "cat(1, a, b)[2]"),
        ("unitTests.x[3]", "cat(1, a, b)[3]"),
    ]


def test_expressions_that_match_neither_shape_leave_the_label_empty():
    """Two expressions for three variables says nothing about which is
    which: the variables keep their names and read as them."""
    assert _pairs(_test(3, ["a", "b"])) == [
        ("unitTests.x[1]", ""),
        ("unitTests.x[2]", ""),
        ("unitTests.x[3]", ""),
    ]


def test_the_component_names_the_variables():
    assert [n for n, _ in _pairs(_test(2, ["a", "b"], instance="ut"))] == [
        "ut.x[1]",
        "ut.x[2]",
    ]


def test_a_spec_sourced_test_has_no_component_variables():
    assert unit_test_variables(_test(0, [], source="spec")) == []
    assert unit_test_variables(_test(2, ["a", "b"], source="spec")) == []


def test_a_both_sourced_test_has_them():
    assert [n for n, _ in _pairs(_test(1, ["a"], source="both"))] == ["unitTests.x[1]"]


def test_no_variables_is_no_variables():
    assert unit_test_variables(_test(0, [])) == []


def test_a_unit_tests_element_is_told_by_the_tests_instance():
    """What never folds into a family (O3): an element of the component's
    array under THIS test's instance name — however the test declared it."""
    test = _test(0, [], instance="ut", source="spec")
    assert is_unit_test_variable(test, "ut.x[1]")
    assert is_unit_test_variable(test, "ut.x[12]")
    for name in (
        "unitTests.x[1]",
        "ut.x",
        "ut.x[]",
        "ut.x[1].y",
        "ut.y[1]",
        "a.ut.x[1]",
        # a digit to `str.isdigit`, never an index of an array
        "ut.x[\u00b2]",
        "ut.x[\u0661]",
    ):
        assert not is_unit_test_variable(test, name), name
    assert all(
        is_unit_test_variable(test, v.name)
        for v in unit_test_variables(_test(3, ["a"], instance="ut"))
    )
