"""One spelling of an array index (#28): OpenModelica writes ``a[1,1]``,
Dymola ``a[1, 1]``; the program deletes the whitespace inside an index's
brackets wherever a name enters, so nothing downstream compares two
spellings of one variable."""

import pytest

from dstf.backends.types import raw_by_name, variable_name

#: (as written, its one spelling)
SPELLINGS = [
    ("a.b[1, 1].c", "a.b[1,1].c"),
    ("der(T[1 , 2])", "der(T[1,2])"),
    ("x[ 3 ]", "x[3]"),
    ("a[1].b[2, 3]", "a[1].b[2,3]"),
    ("a[1,\t2]", "a[1,2]"),
    ("pipe.T", "pipe.T"),
    ("a.b + c.d", "a.b + c.d"),
    ("f(a, b)[1, 2] + g(c, d)", "f(a, b)[1,2] + g(c, d)"),
    ("a[1, *]", "a[1,*]"),
    ("a[1,1]", "a[1,1]"),
    ("", ""),
]


@pytest.mark.parametrize(("written", "spelled"), SPELLINGS)
def test_whitespace_inside_an_index_is_deleted_and_nothing_else_changes(
    written, spelled
):
    assert variable_name(written) == spelled


@pytest.mark.parametrize(("written", "spelled"), SPELLINGS)
def test_the_spelling_of_a_spelling_is_itself(written, spelled):
    assert variable_name(variable_name(written)) == spelled


def test_a_file_s_names_map_its_one_spelling_to_the_file_s_own_in_its_order():
    assert list(raw_by_name(["time", "T[1, 2]", "T[2,1]", "x"]).items()) == [
        ("time", "time"),
        ("T[1,2]", "T[1, 2]"),
        ("T[2,1]", "T[2,1]"),
        ("x", "x"),
    ]


def test_two_names_of_a_file_that_are_one_variable_are_refused_naming_both():
    with pytest.raises(ValueError) as caught:
        raw_by_name(["x", "a[1, 1]", "a[1,1]"])
    assert str(caught.value) == "'a[1, 1]' and 'a[1,1]' are the same variable"
