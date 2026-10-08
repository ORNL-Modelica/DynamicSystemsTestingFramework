"""A criterion over an array family, as data (D152): the Indices syntax
(`figures.families.parse_slice` / `slice_text`), which criteria rows fold
(`authoring.family_rows.fold_rows`), and what an edit of a fold stages."""

from __future__ import annotations

import copy

import pytest

from dstf.authoring.family_rows import (
    Fold,
    as_one,
    family_in,
    fold_key,
    fold_rows,
    is_fold,
    leaf_of,
    reindex,
    tally,
)
from dstf.authoring.spec_document import PatchOp
from dstf.authoring.tree_move import path_map
from dstf.comparison.tree_spec import parse_metric_tree, spec_to_view
from dstf.figures.families import parse_slice, slice_text
from dstf.reporting.tree_rows import criteria_rows, leaves_of

M = "Lib.M"


# -- the Indices syntax -------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "indices"),
    [
        ("3", [3]),
        ("1:5", [1, 2, 3, 4, 5]),
        ("1:5:20", [1, 6, 11, 16]),  # start:step:end, as Modelica writes it
        ("1:5:16", [1, 6, 11, 16]),
        ("1,3,7", [1, 3, 7]),
        ("1:3,7,10:2:20", [1, 2, 3, 7, 10, 12, 14, 16, 18, 20]),
        ("7,1:3", [1, 2, 3, 7]),  # any order between the parts
        (" 1 : 3 , 7 ", [1, 2, 3, 7]),
        ("4:4", [4]),
    ],
)
def test_an_indices_text_names_its_indices_ascending(text, indices):
    assert parse_slice(text) == indices


def test_a_ranges_end_is_included():
    """Modelica's `1:20` is twenty elements: an exclusive end would drop
    the last one from every family written whole."""
    assert parse_slice("1:20")[-1] == 20
    assert len(parse_slice("1:20")) == 20
    assert parse_slice("1:5:16")[-1] == 16


@pytest.mark.parametrize(
    ("text", "says"),
    [
        ("", "Indices is empty"),
        ("  ", "Indices is empty"),
        (",", "Indices is empty"),
        ("5:1", "5:1 ends before it starts."),
        ("9:2:1", "9:2:1 ends before it starts."),
        ("1:3,2", "2 is written twice."),
        ("4,4", "4 is written twice."),
        ("0", "0: indices start at 1."),
        ("0:3", "0:3: indices start at 1."),
        ("-1", "-1: indices start at 1."),
        ("1:0:5", "1:0:5: the step is at least 1."),
        ("1:-1:5", "1:-1:5: the step is at least 1."),
        ("a", "'a' is not an index or a range"),
        ("1:", "'1:' is not an index or a range"),
        ("1:2:3:4", "'1:2:3:4' is not an index or a range"),
        ("1.5", "'1.5' is not an index or a range"),
        ("1,,3", "'' is not an index or a range"),
        ("1..5", "'1..5' is not an index or a range"),
        ("1:999999999", "names 999,999,999 indices: too many."),
    ],
)
def test_an_indices_text_that_is_no_slice_is_refused_with_a_sentence(text, says):
    with pytest.raises(ValueError) as e:
        parse_slice(text)
    assert says in str(e.value)


@pytest.mark.parametrize(
    ("indices", "text"),
    [
        ([1, 2, 3, 4, 5, 6, 8, 9, 10], "1:6,8:10"),  # a gap
        ([1, 2, 4, 5, 6, 7], "1,2,4:7"),
        # only an UNBROKEN run is a range here: `1:2:5,6:8` reads as nothing
        ([1, 3, 5, 6, 7, 8], "1,3,5:8"),
        ([1, 2, 4, 6, 8], "1,2,4,6,8"),
        ([2, 5, 6, 9], "2,5,6,9"),
        ([1, 2, 3, 7], "1:3,7"),
        ([1, 2, 3, 4, 7, 8], "1:4,7,8"),
        ([1, 2, 4], "1,2,4"),
        ([1, 3], "1:2:3"),
    ],
)
def test_a_slice_with_gaps_prints_its_runs_and_reads_back(indices, text):
    assert slice_text(indices) == text
    assert parse_slice(text) == indices


@pytest.mark.parametrize("seed", range(40))
def test_parse_of_print_is_the_set_printed(seed):
    import random

    rng = random.Random(seed)
    indices = sorted(rng.sample(range(1, 40), rng.randint(1, 25)))
    assert parse_slice(slice_text(indices)) == indices


# -- which rows fold -----------------------------------------------------------------


def _leaf(i: int, metric: str = "nrmse", name: str = "pipe.T", **more) -> dict:
    return {"metric": metric, "variable": f"{name}[{i}]", **more}


def _rows(tree: dict, subject: list[str]) -> list[dict]:
    """The criteria rows of ``tree`` on ``subject``, as a plot card reads
    them off the view of the parsed tree (no verdicts)."""
    return criteria_rows(spec_to_view(parse_metric_tree(tree)), subject)


def _all_rows(tree: dict) -> list[dict]:
    """`_rows` for a card whose subject is every variable the tree names."""
    view = spec_to_view(parse_metric_tree(tree))
    return criteria_rows(view, [leaf["variable"] for leaf in leaves_of(view)])


def _shape(entries) -> list:
    """Each entry as a test reads it: a fold as its name and member paths'
    last indices, a row as its variable."""
    out = []
    for entry in entries:
        if isinstance(entry, Fold):
            out.append((entry.name, [int(p.rsplit("/", 1)[1]) for p in entry.paths]))
        else:
            out.append(entry["variable"])
    return out


def _and(*children: dict) -> dict:
    return {"combinator": "and", "children": list(children)}


def test_identical_criteria_of_one_family_fold_into_one_row():
    tree = _and(*(_leaf(i) for i in range(1, 7)))
    (fold,) = fold_rows(_all_rows(tree))
    assert isinstance(fold, Fold)
    assert fold.name == "nrmse · pipe.T[1:6]"
    assert fold.key == fold_key("/metrics/children/0")
    assert fold.paths == tuple(f"/metrics/children/{i}" for i in range(6))
    assert fold.crumbs == "and ›"


def test_a_member_that_differs_is_its_own_row_and_the_label_shows_the_gap():
    leaves = [_leaf(i) for i in range(1, 7)]
    leaves[2]["tolerance"] = 0.5  # pipe.T[3]
    entries = fold_rows(_all_rows(_and(*leaves)))
    assert _shape(entries) == [
        ("nrmse · pipe.T[1,2,4:6]", [0, 1, 3, 4, 5]),
        "pipe.T[3]",
    ]


def test_two_groups_of_one_family_are_two_folds():
    leaves = [_leaf(i, tolerance=0.1 if i <= 3 else 0.2) for i in range(1, 7)]
    assert _shape(fold_rows(_all_rows(_and(*leaves)))) == [
        ("nrmse · pipe.T[1:3]", [0, 1, 2]),
        ("nrmse · pipe.T[4:6]", [3, 4, 5]),
    ]


def test_leaves_under_different_parents_do_not_fold_together():
    """`and[T1, T2, or[T3, T4]]`: one criterion on four elements, but an
    edit of "all" under the `and` must not reach into the `or`."""
    tree = _and(
        _leaf(1), _leaf(2), {"combinator": "or", "children": [_leaf(3), _leaf(4)]}
    )
    entries = fold_rows(_all_rows(tree))
    assert [e.label for e in entries] == ["pipe.T[1:2]", "pipe.T[3:4]"]
    assert [e.crumbs for e in entries] == ["and ›", "and › or ›"]


def test_a_lone_leaf_under_another_parent_is_a_row():
    tree = _and(_leaf(1), _leaf(2), {"combinator": "warn", "children": [_leaf(3)]})
    assert _shape(fold_rows(_all_rows(tree))) == [
        ("nrmse · pipe.T[1:2]", [0, 1]),
        "pipe.T[3]",
    ]


def test_the_variable_is_the_one_thing_two_members_may_differ_in():
    """Everything else is the criterion: the metric, a parameter, the
    window, the baseline."""
    base = [_leaf(1), _leaf(2)]
    assert len(fold_rows(_all_rows(_and(*base)))) == 1
    for other in (
        _leaf(2, metric="tube", tube_width_mode="band", tube_band=1.0),
        _leaf(2, tolerance=0.5),
        _leaf(2, window={"start": 0.0, "end": 1.0}),
        _leaf(2, against="other"),
    ):
        assert _shape(fold_rows(_all_rows(_and(_leaf(1), other)))) == [
            "pipe.T[1]",
            "pipe.T[2]",
        ]


def test_a_window_on_some_members_splits_the_family_by_it():
    window = {"start": 0.0, "end": 0.5}
    leaves = [_leaf(1, window=window), _leaf(2), _leaf(3, window=window), _leaf(4)]
    assert _shape(fold_rows(_all_rows(_and(*leaves)))) == [
        ("nrmse · pipe.T[1:2:3]", [0, 2]),
        ("nrmse · pipe.T[2:2:4]", [1, 3]),
    ]


def test_a_number_is_compared_as_json_not_as_python():
    """`1` is `1.0`; `true` is no number (`json_equal`)."""
    assert (
        len(fold_rows(_all_rows(_and(_leaf(1, tolerance=1), _leaf(2, tolerance=1.0)))))
        == 1
    )
    tree = _and(_leaf(1, flag=True), _leaf(2, flag=1))
    assert len(fold_rows(_all_rows(tree))) == 2


def test_members_need_not_be_adjacent():
    """The fold stands where its FIRST member's row was, and a row between
    two members follows it."""
    tree = _and(
        _leaf(1),
        _leaf(2, tolerance=0.5),
        _leaf(3),
        {"metric": "nrmse", "variable": "m"},
    )
    assert _shape(fold_rows(_all_rows(tree))) == [
        ("nrmse · pipe.T[1:2:3]", [0, 2]),
        "pipe.T[2]",
        "m",
    ]


def test_a_group_of_one_is_a_row():
    assert _shape(
        fold_rows(_all_rows(_and(_leaf(1), {"metric": "nrmse", "variable": "m"})))
    ) == [
        "pipe.T[1]",
        "m",
    ]
    # A "family" of one element, scored twice: one variable, no fold.
    assert _shape(fold_rows(_all_rows(_and(_leaf(1), _leaf(1))))) == [
        "pipe.T[1]",
        "pipe.T[1]",
    ]


def test_a_fold_names_each_element_once():
    """A second identical leaf on an element the fold holds is its own row."""
    tree = _and(_leaf(1), _leaf(2), _leaf(1))
    assert _shape(fold_rows(_all_rows(tree))) == [
        ("nrmse · pipe.T[1:2]", [0, 1]),
        "pipe.T[1]",
    ]


def test_two_families_fold_apart():
    tree = _and(_leaf(1), _leaf(1, name="pipe.p"), _leaf(2), _leaf(2, name="pipe.p"))
    assert _shape(fold_rows(_all_rows(tree))) == [
        ("nrmse · pipe.T[1:2]", [0, 2]),
        ("nrmse · pipe.p[1:2]", [1, 3]),
    ]


def test_a_unit_tests_element_never_folds():
    """D140: each is a test quantity of its own, whatever its name's shape."""
    names = ["unitTests.x[1]", "unitTests.x[2]", "unitTests.x[3]"]
    tree = _and(*({"metric": "nrmse", "variable": n} for n in names))
    assert _shape(fold_rows(_all_rows(tree), unit_tests=names)) == names
    # ... and by its name alone it would have: this is the rule, not a shape.
    assert len(fold_rows(_all_rows(tree))) == 1


def test_a_name_that_is_no_element_never_folds():
    tree = _and(
        {"metric": "nrmse", "variable": "x[2,3]"},
        {"metric": "nrmse", "variable": "x[2,4]"},
        {"metric": "nrmse", "variable": "m"},
        {"metric": "nrmse", "variable": "m"},
    )
    assert _shape(fold_rows(_all_rows(tree))) == ["x[2,3]", "x[2,4]", "m", "m"]


def test_a_family_behind_a_two_dimensional_index_folds_by_its_last():
    """D8: what `families.py` folds today — the last single-integer index."""
    tree = _and(
        *({"metric": "nrmse", "variable": f"port[2, 1].C[{i}]"} for i in (1, 2, 3))
    )
    (fold,) = fold_rows(_all_rows(tree))
    assert fold.label == "port[2, 1].C[1:3]"


def test_a_root_leaf_is_a_row():
    assert _shape(fold_rows(_all_rows(_leaf(1)))) == ["pipe.T[1]"]


def test_the_rows_of_a_plot_fold_whatever_else_the_tree_holds():
    """The card's rows are its SUBJECT's (`criteria_rows`); a stored-scalar
    leaf is no row."""
    tree = _and(
        _leaf(1),
        {"metric": "nrmse", "variable": "m"},
        _leaf(2),
        {
            "metric": "scalar",
            "variable": "CPUtime",
            "quantity": "CPUtime.final",
            "tolerance": 1,
        },
    )
    rows = _rows(tree, ["pipe.T[1]", "pipe.T[2]"])
    assert _shape(fold_rows(rows)) == [("nrmse · pipe.T[1:2]", [0, 2])]


def test_a_pinned_group_stays_together_and_apart():
    """The open folded editor's members are held: a value on its way to
    another (`0.05` is `0.0` first) equals a second group's for a keystroke,
    and merged with it the next keystroke would carry that group along."""
    leaves = [_leaf(i, tolerance=0.0) for i in range(1, 5)]
    rows = _all_rows(_and(*leaves))
    assert _shape(fold_rows(rows)) == [("nrmse · pipe.T[1:4]", [0, 1, 2, 3])]
    held = ["/metrics/children/2", "/metrics/children/3"]
    assert _shape(fold_rows(rows, pinned=held)) == [
        ("nrmse · pipe.T[1:2]", [0, 1]),
        ("nrmse · pipe.T[3:4]", [2, 3]),
    ]


def test_a_fold_key_is_no_path_and_names_its_first_member():
    key = fold_key("/metrics/children/3")
    assert is_fold(key) and not is_fold("/metrics/children/3")
    assert leaf_of(key) == "/metrics/children/3"
    assert leaf_of("/metrics/children/3") == "/metrics/children/3"


def test_the_count_is_the_members_by_verdict():
    assert tally(["pass"] * 18 + ["fail"] * 2) == "18 pass · 2 fail"
    assert tally(["pass", "pass"]) == "2 pass"
    assert tally(["fail", "warn", "queued", "queued"]) == (
        "0 pass · 1 fail · 1 advisory · 2 unscored"
    )


# -- the family an Indices field is about ------------------------------------------

NAMES = [
    "x",
    *(f"pipe.T[{i}]" for i in range(1, 21)),
    "m[1]",
    "unitTests.x[1]",
    "unitTests.x[2]",
]


def test_a_family_is_every_element_the_file_has():
    family = family_in(NAMES, "pipe.T[3]")
    assert family is not None
    assert family.label == "pipe.T[1:20]"
    assert family.text == "1:20"
    assert family.chosen("1:5:20") == [
        "pipe.T[1]",
        "pipe.T[6]",
        "pipe.T[11]",
        "pipe.T[16]",
    ]
    assert family.indices_of(["pipe.T[4]", "pipe.T[2]", "pipe.T[3]"]) == "2:4"


def test_no_family_no_indices_field():
    assert family_in(NAMES, "x") is None  # no element of an array
    assert family_in(NAMES, "m[1]") is None  # the only element there is
    # D140: a UnitTests element is a test quantity of its own
    assert family_in(NAMES, "unitTests.x[1]", unit_tests=NAMES[-2:]) is None
    assert family_in(NAMES, "unitTests.x[1]") is not None  # ... by that rule alone


def test_an_index_the_family_lacks_is_refused_naming_its_size():
    family = family_in(NAMES, "pipe.T[1]")
    assert family is not None
    with pytest.raises(ValueError) as e:
        family.chosen("1:5:25")
    assert str(e.value) == "pipe.T[1:20] has 20 elements; 21 is not one of them."
    with pytest.raises(ValueError) as e:
        family.chosen("19:22")
    assert str(e.value) == "pipe.T[1:20] has 20 elements; 21:22 are not among them."
    with pytest.raises(ValueError, match="Indices is empty"):
        family.chosen("")


def test_a_family_with_a_gap_in_the_file_says_what_it_has():
    family = family_in(["a[1]", "a[2]", "a[4]"], "a[1]")
    assert family is not None
    assert family.text == "1,2,4"
    with pytest.raises(ValueError) as e:
        family.chosen("3")
    assert str(e.value) == "a[1,2,4] has 3 elements; 3 is not one of them."


# -- an edit of a fold ----------------------------------------------------------------


def _tree6() -> dict:
    return _and(
        {"metric": "nrmse", "variable": "m"},
        *(_leaf(i, tolerance=0.1) for i in range(1, 5)),
        {"metric": "nrmse", "variable": "z"},
    )


def _apply(tree: dict, ops) -> dict:
    return as_one(M, tree, ops).value


def test_ops_as_one_are_the_tree_they_give_and_leave_the_tree_read_alone():
    tree = _tree6()
    before = copy.deepcopy(tree)
    ops = [
        PatchOp(M, "replace", "/metrics/children/1", _leaf(1, tolerance=0.3)),
        PatchOp(M, "remove", "/metrics/children/5"),
    ]
    one = as_one(M, tree, ops)
    assert (one.model_id, one.op, one.path) == (M, "add", "/metrics")
    assert [c["variable"] for c in one.value["children"]] == [
        "m",
        "pipe.T[1]",
        "pipe.T[2]",
        "pipe.T[3]",
        "pipe.T[4]",
    ]
    assert one.value["children"][1]["tolerance"] == 0.3
    assert tree == before


MEMBERS = [f"/metrics/children/{i}" for i in range(1, 5)]


def test_indices_grow_adds_after_the_last_member_with_the_folds_criterion():
    tree = _tree6()
    names = [f"pipe.T[{i}]" for i in (1, 2, 3, 4, 6, 7)]
    plan = reindex(M, tree, MEMBERS, names)
    new = _apply(tree, plan.ops)
    assert [c["variable"] for c in new["children"]] == [
        "m",
        "pipe.T[1]",
        "pipe.T[2]",
        "pipe.T[3]",
        "pipe.T[4]",
        "pipe.T[6]",
        "pipe.T[7]",
        "z",
    ]
    assert new["children"][5] == _leaf(6, tolerance=0.1)
    assert plan.paths == tuple(f"/metrics/children/{i}" for i in range(1, 7))
    # what stood stays where it was; `z` moved two down
    moved = path_map(tree, plan.ops)
    assert moved["/metrics/children/5"] == "/metrics/children/7"
    assert all(moved[p] == p for p in MEMBERS)


def test_indices_shrink_removes_those_leaves_and_nothing_else():
    tree = _tree6()
    plan = reindex(M, tree, MEMBERS, ["pipe.T[2]", "pipe.T[4]"])
    new = _apply(tree, plan.ops)
    assert [c["variable"] for c in new["children"]] == [
        "m",
        "pipe.T[2]",
        "pipe.T[4]",
        "z",
    ]
    assert plan.paths == ("/metrics/children/1", "/metrics/children/2")
    assert all(op.op == "remove" for op in plan.ops)


def test_indices_swap_adds_and_removes_in_one_plan():
    tree = _tree6()
    plan = reindex(M, tree, MEMBERS, ["pipe.T[3]", "pipe.T[9]"])
    new = _apply(tree, plan.ops)
    assert [c["variable"] for c in new["children"]] == [
        "m",
        "pipe.T[3]",
        "pipe.T[9]",
        "z",
    ]
    assert plan.paths == ("/metrics/children/1", "/metrics/children/2")
    # the first member went: the added leaf still carries the fold's criterion
    assert new["children"][2] == _leaf(9, tolerance=0.1)


def test_indices_unchanged_is_no_op():
    plan = reindex(M, _tree6(), MEMBERS, [f"pipe.T[{i}]" for i in range(1, 5)])
    assert plan.ops == ()
    assert plan.paths == tuple(MEMBERS)


def test_members_apart_keep_their_places_and_the_new_one_follows_the_last():
    tree = _and(
        _leaf(1), {"metric": "nrmse", "variable": "m"}, _leaf(2), _leaf(3, tolerance=9)
    )
    plan = reindex(
        M,
        tree,
        ["/metrics/children/0", "/metrics/children/2"],
        ["pipe.T[2]", "pipe.T[5]"],
    )
    new = _apply(tree, plan.ops)
    assert [c["variable"] for c in new["children"]] == [
        "m",
        "pipe.T[2]",
        "pipe.T[5]",
        "pipe.T[3]",
    ]
    assert plan.paths == ("/metrics/children/1", "/metrics/children/2")


@pytest.mark.parametrize(
    "paths",
    [
        [],
        ["/metrics/children/1", "/metrics/children/9"],  # names no node
        ["/metrics/children/1", "/metrics"],  # not siblings
        ["/metrics"],  # the root has no parent to add beside
    ],
)
def test_paths_that_are_no_fold_are_refused(paths):
    with pytest.raises(ValueError, match="no longer one folded row"):
        reindex(M, _tree6(), paths, ["pipe.T[1]"])
