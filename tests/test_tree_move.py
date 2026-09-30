"""Moving a subtree (U4): `plan_move` computes the whole new tree, or a
refusal with its one sentence; `move_destinations` lists every place."""

from __future__ import annotations

import copy

import pytest

from dstf.authoring.tree_move import (
    INTO_ITSELF,
    INTO_WARN,
    ROOT_REFUSAL,
    WARN_UNDER,
    Dest,
    MoveRefused,
    move_destinations,
    plan_move,
    validator_for,
)
from dstf.comparison.tree_spec import MetricSpecError, parse_metric_tree
from dstf.comparison.validator import role_lookup_from_names

R = "/metrics"


def p(*idx: int) -> str:
    return R + "".join(f"/children/{i}" for i in idx)


def L(v: str, **kw) -> dict:
    return {"metric": "nrmse", "variable": v, **kw}


def C(kind: str, *children: dict, **kw) -> dict:
    return {"combinator": kind, "children": list(children), **kw}


FLAT = C("and", L("a"), L("b"), L("c"), L("d"))

NESTED = C("and", L("a"), C("k-of-n", L("b"), L("c"), k=2), C("warn", L("d")))

VALIDATE = validator_for(role_lookup_from_names(soft_checks=["sc"]))


def move(tree, src, dest, validate=VALIDATE):
    before = copy.deepcopy(tree)
    moved = plan_move(tree, src, dest, validate=validate)
    assert tree == before, "plan_move changed its input"
    return moved


# -- reorder, into, onto -----------------------------------------------------


@pytest.mark.parametrize(
    ("src", "dest", "order", "path"),
    [
        (p(0), Dest("at", R, 3), "bcad", p(2)),  # 1 -> 3: before d, as it is now
        (p(2), Dest("at", R, 0), "cabd", p(0)),  # 3 -> 1
        (p(0), Dest("at", R, None), "bcda", p(3)),  # to the end
        (p(1), Dest("onto", R), "acdb", p(3)),  # onto a combinator: its end
    ],
)
def test_reorder_inside_the_root(src, dest, order, path):
    moved = move(FLAT, src, dest)
    assert moved is not None
    assert "".join(n["variable"] for n in moved.tree["children"]) == order
    assert moved.tree["combinator"] == "and"
    assert moved.path == path and moved.notes == ()


def test_into_a_combinator_at_an_index_and_appended():
    tree = C("and", L("a"), C("or", L("b"), L("c")), L("d"))
    moved = move(tree, p(2), Dest("at", p(1), 1))
    assert moved.tree == C("and", L("a"), C("or", L("b"), L("d"), L("c")))
    assert moved.path == p(1, 1)
    moved = move(tree, p(0), Dest("at", p(1), None))
    assert moved.tree == C("and", C("or", L("b"), L("c"), L("a")), L("d"))
    assert moved.path == p(0, 2)
    # onto a combinator is the same as its end
    assert move(tree, p(0), Dest("onto", p(1))) == moved


def test_onto_a_criterion_wraps_both_in_and_target_first():
    moved = move(FLAT, p(3), Dest("onto", p(1)))
    assert moved.tree == C("and", L("a"), C("and", L("b"), L("d")), L("c"))
    assert moved.path == p(1, 1) and moved.notes == ()


def test_a_one_digit_path_is_not_the_prefix_of_a_two_digit_one():
    tree = C("and", *[L(str(i)) for i in range(10)], C("and", L("z")))
    moved = move(tree, p(1), Dest("onto", p(10)))
    assert moved.path == p(9, 1)


# -- D2: an emptied combinator goes, up the tree ----------------------------


def test_out_of_a_warn_the_warn_goes():
    moved = move(NESTED, p(2, 0), Dest("at", R, 0))
    assert moved.tree == C("and", L("d"), L("a"), NESTED["children"][1])
    assert moved.path == p(0)
    assert moved.notes == ("the empty warn was removed",)


def test_emptying_an_and_in_an_or_removes_only_the_and():
    tree = C("and", L("a"), C("or", C("and", L("b")), L("c")))
    moved = move(tree, p(1, 0, 0), Dest("at", R, 1))
    assert moved.tree == C("and", L("a"), L("b"), C("or", L("c")))
    assert moved.path == p(1)
    assert moved.notes == ("the empty and was removed",)


def test_emptying_a_chain_removes_every_link():
    tree = C("and", L("a"), C("or", C("and", L("b"))))
    moved = move(tree, p(1, 0, 0), Dest("at", R, 0))
    assert moved.tree == C("and", L("b"), L("a"))
    assert moved.path == p(0)
    assert moved.notes == ("the empty and was removed", "the empty or was removed")


def test_a_place_before_the_warn_the_move_empties():
    """The destination's anchor is the warn D2 removes: the node lands
    where the warn was."""
    tree = C("and", L("a"), C("warn", L("b")), L("c"))
    moved = move(tree, p(1, 0), Dest("at", R, 1))
    assert moved.tree == C("and", L("a"), L("b"), L("c"))
    assert moved.path == p(1) and moved.notes == ("the empty warn was removed",)


def test_the_destination_is_never_the_empty_combinator_d2_removes():
    """`and[a, or[and[b]]]`, b to the end of the or: the emptied `and`
    goes; the `or` is where b lands, so it stays."""
    tree = C("and", L("a"), C("or", C("and", L("b"))))
    moved = move(tree, p(1, 0, 0), Dest("at", p(1), None))
    assert moved.tree == C("and", L("a"), C("or", L("b")))
    assert moved.path == p(1, 0)
    assert moved.notes == ("the empty and was removed",)


def test_the_root_holding_only_a_chain_takes_its_last_leaf():
    """`and[or[and[a]]]`, a to the root's front: the chain goes, the root
    holds a — never a refusal for an empty root."""
    tree = C("and", C("or", C("and", L("a"))))
    moved = move(tree, p(0, 0, 0), Dest("at", R, 0))
    assert moved.tree == C("and", L("a"))
    assert moved.path == p(0)


def test_before_an_anchor_that_the_chain_removes():
    """`and[a, or[and[b]], c]`, b to `at(root, 1)` — before the `or` the
    move empties: b lands where the `or` was."""
    tree = C("and", L("a"), C("or", C("and", L("b"))), L("c"))
    moved = move(tree, p(1, 0, 0), Dest("at", R, 1))
    assert moved.tree == C("and", L("a"), L("b"), L("c"))
    assert moved.path == p(1)


# -- D3: k and weights -------------------------------------------------------


def test_out_of_a_k_of_n_lowers_k_only_when_it_must():
    tree = C("and", L("a"), C("k-of-n", L("b"), L("c"), L("d"), k=3))
    moved = move(tree, p(1, 0), Dest("at", R, 0))
    assert moved.tree["children"][2] == C("k-of-n", L("c"), L("d"), k=2)
    assert moved.notes == ("k 3 → 2",)
    tree["children"][1]["k"] = 2
    moved = move(tree, p(1, 0), Dest("at", R, 0))
    assert moved.tree["children"][2] == C("k-of-n", L("c"), L("d"), k=2)
    assert moved.notes == ()


def test_a_k_of_n_that_loses_an_emptied_child_lowers_k():
    """D2 then D3, up the pruned chain: the emptied `and` leaves the
    k-of-n, whose k follows."""
    tree = C("and", L("a"), C("k-of-n", C("and", L("b")), L("c"), k=2))
    moved = move(tree, p(1, 0, 0), Dest("at", R, 0))
    assert moved.tree == C("and", L("b"), L("a"), C("k-of-n", L("c"), k=1))
    assert moved.notes == ("the empty and was removed", "k 2 → 1")


def _weighted(*children, weights):
    return C("weighted", *children, weights=weights, threshold=1.0)


def test_into_a_weighted_inserts_weight_one_at_the_index():
    tree = C("and", L("a"), _weighted(L("b"), L("c"), weights=[2, 3]))
    moved = move(tree, p(0), Dest("at", p(1), 1))
    assert moved.tree == C("and", _weighted(L("b"), L("a"), L("c"), weights=[2, 1, 3]))
    assert moved.notes == ("weight 1 given",)


def test_out_of_a_weighted_its_weight_leaves():
    tree = C("and", L("a"), _weighted(L("b"), L("c"), L("d"), weights=[2, 3, 5]))
    moved = move(tree, p(1, 1), Dest("at", R, 0))
    assert moved.tree["children"][2] == _weighted(L("b"), L("d"), weights=[2, 5])
    assert moved.notes == ()


def test_inside_one_weighted_the_weight_travels_with_its_child():
    tree = C("and", L("a"), _weighted(L("b"), L("c"), L("d"), weights=[2, 3, 5]))
    moved = move(tree, p(1, 0), Dest("at", p(1), None))
    assert moved.tree["children"][1] == _weighted(
        L("c"), L("d"), L("b"), weights=[3, 5, 2]
    )
    assert moved.notes == ()


def test_a_weighted_that_loses_an_emptied_child_loses_its_weight():
    tree = C("and", L("a"), _weighted(C("and", L("b")), L("c"), weights=[2, 3]))
    moved = move(tree, p(1, 0, 0), Dest("at", R, 0))
    assert moved.tree["children"][2] == _weighted(L("c"), weights=[3])


# -- refusals (V5, in its order) ---------------------------------------------


@pytest.mark.parametrize(
    ("tree", "src", "dest", "sentence"),
    [
        (NESTED, R, Dest("at", p(1), 0), ROOT_REFUSAL),
        (NESTED, p(1), Dest("onto", p(1)), INTO_ITSELF),
        (NESTED, p(1), Dest("at", p(1), 0), INTO_ITSELF),
        (NESTED, p(1), Dest("onto", p(1, 0)), INTO_ITSELF),
        (NESTED, p(0), Dest("at", p(2), 0), INTO_WARN),
        (NESTED, p(0), Dest("onto", p(2)), INTO_WARN),
        (NESTED, p(2), Dest("at", p(1), 1), WARN_UNDER),
        (NESTED, p(2), Dest("onto", p(1)), WARN_UNDER),
    ],
)
def test_refusals(tree, src, dest, sentence):
    with pytest.raises(MoveRefused) as e:
        move(tree, src, dest)
    assert str(e.value) == sentence


def test_the_sentences():
    assert INTO_WARN == (
        "A warning holds exactly one criterion or group: drop onto it to wrap "
        "both in `and`."
    )
    assert WARN_UNDER == (
        "A warning passes whatever it scores: under or / k-of-n it would pass "
        "its parent with nothing else passing."
    )


def test_where_it_is_comes_before_into_a_warn():
    """A warn's only criterion, "moved" into its own warn, is where it is:
    no move, not the warn's refusal."""
    assert move(NESTED, p(2, 0), Dest("at", p(2), 0)) is None
    assert move(NESTED, p(2, 0), Dest("at", p(2), None)) is None
    assert move(NESTED, p(2, 0), Dest("onto", p(2))) is None


def test_a_move_that_changes_nothing_is_none():
    assert move(FLAT, p(1), Dest("at", R, 1)) is None
    assert move(FLAT, p(1), Dest("at", R, 2)) is None
    assert move(FLAT, p(3), Dest("at", R, None)) is None
    assert move(FLAT, p(3), Dest("onto", R)) is None


# -- D4 by its reason: a child that can never fail, under or / k-of-n ------


def test_a_sibling_leaving_makes_an_and_always_pass_under_an_or():
    """`or[and[warn[a], b], c]`, b out: the `and` would pass whatever it
    scores, under an `or`."""
    tree = C("and", L("z"), C("or", C("and", C("warn", L("a")), L("b")), L("c")))
    with pytest.raises(MoveRefused) as e:
        move(tree, p(1, 0, 1), Dest("at", R, None))
    assert str(e.value) == WARN_UNDER


def test_an_and_of_a_warn_moved_into_an_or():
    tree = C("and", L("z"), C("and", C("warn", L("a"))), C("or", L("b"), L("c")))
    with pytest.raises(MoveRefused) as e:
        move(tree, p(1), Dest("at", p(2), None))
    assert str(e.value) == WARN_UNDER


@pytest.mark.parametrize(
    "group",
    [
        C("or", C("warn", L("a")), L("b")),  # an or: any child
        C("k-of-n", C("warn", L("a")), L("b"), k=1),  # a k-of-n: k of them
    ],
)
def test_a_group_that_always_passes_moved_into_a_k_of_n(group):
    tree = C("and", L("z"), group, C("k-of-n", L("c"), L("d"), k=1))
    with pytest.raises(MoveRefused) as e:
        move(tree, p(1), Dest("at", p(2), 0))
    assert str(e.value) == WARN_UNDER


@pytest.mark.parametrize(
    "group",
    [
        C("k-of-n", C("warn", L("a")), L("b"), k=2),  # k=2: b must pass too
        C("weighted", C("warn", L("a")), L("b"), weights=[1, 1], threshold=1.0),
        C("and", C("warn", L("a")), L("b")),
    ],
)
def test_a_group_that_can_fail_moves_into_an_or(group):
    tree = C("and", L("z"), group, C("or", L("c"), L("d")))
    assert move(tree, p(1), Dest("at", p(2), 0)) is not None


def test_a_warn_onto_a_criterion_under_an_or_is_wrapped_in_and():
    tree = C("and", L("z"), C("warn", L("a")), C("or", L("b"), L("c")))
    moved = move(tree, p(1), Dest("onto", p(2, 0)))
    assert moved.tree == C(
        "and", L("z"), C("or", C("and", L("b"), C("warn", L("a"))), L("c"))
    )


def test_a_tree_as_it_is_stays_as_it_is():
    """An `or` already holding a warn: a move elsewhere, and one that
    leaves the warn beside fewer siblings, are both made — only a pairing
    the move MAKES is refused."""
    tree = C("and", L("z"), L("y"), C("or", C("warn", L("a")), L("b")))
    assert move(tree, p(1), Dest("at", R, 0)) is not None
    assert move(tree, p(2, 1), Dest("at", R, 0)) is not None


# -- the validator's own sentence (C6) ---------------------------------------


def test_the_last_primary_into_a_warn_is_the_validators_refusal():
    tree = C("and", L("a"), C("warn", L("s", against="sc")))
    with pytest.raises(
        MoveRefused, match="at least one criterion scored against the primary"
    ):
        move(tree, p(0), Dest("onto", p(1, 0)))


def test_validator_for_says_the_parsers_message_without_its_path():
    raw = C("and", C("warn", L("a"), L("b")))
    with pytest.raises(MetricSpecError) as parsed:
        parse_metric_tree(raw)
    with pytest.raises(MoveRefused) as e:
        VALIDATE(raw)
    assert str(e.value) == parsed.value.message


def test_validator_for_joins_the_validators_messages():
    raw = C("and", C("warn", L("a")), L("s", against="sc"), L("c", against="co"))
    with pytest.raises(MoveRefused) as e:
        VALIDATE(raw)
    text = str(e.value)
    assert "soft check 'sc'" in text and "'co'" in text
    assert ".metrics" not in text and "metrics." not in text


def test_the_validator_is_called_last_with_the_new_tree():
    seen = []

    def validate(tree):
        seen.append(copy.deepcopy(tree))
        raise MoveRefused("Every tree needs a primary.")

    with pytest.raises(MoveRefused, match="Every tree needs a primary."):
        move(FLAT, p(0), Dest("at", R, 3), validate=validate)
    assert [[n["variable"] for n in t["children"]] for t in seen] == [list("bcad")]


# -- the destinations ----------------------------------------------------------


def test_move_destinations_over_a_nested_tree():
    """Tree order, positions between a combinator's children; the node's
    own row is not listed, and the places beside it read as ONE row."""
    rows = move_destinations(NESTED, p(0), validate=VALIDATE, names={"b": "pipe.T[1]"})
    root = "and · 4 criteria (root)"
    kn = "k-of-n · 2 criteria · k=2"
    assert [(label, dest) for label, dest, _ in rows] == [
        (f"{root} — where it is", Dest("at", R, 0)),
        (f"{kn} — at 1", Dest("at", p(1), 0)),
        ("nrmse · pipe.T[1] — wrap both in and", Dest("onto", p(1, 0))),
        (f"{kn} — at 2", Dest("at", p(1), 1)),
        ("nrmse · c — wrap both in and", Dest("onto", p(1, 1))),
        (f"{kn} — at the end", Dest("at", p(1), None)),
        (f"{root} — at 3", Dest("at", R, 2)),
        ("warn · 1 criterion — at 1", Dest("at", p(2), 0)),
        ("nrmse · d — wrap both in and", Dest("onto", p(2, 0))),
        ("warn · 1 criterion — at the end", Dest("at", p(2), None)),
        (f"{root} — at the end", Dest("at", R, None)),
    ]
    why = {label: reason for label, _, reason in rows}
    assert why["warn · 1 criterion — at 1"] == INTO_WARN
    assert why["warn · 1 criterion — at the end"] == INTO_WARN
    for label, dest, reason in rows:
        if reason is None:
            planned = plan_move(NESTED, p(0), dest, validate=VALIDATE)
            assert (planned is None) == label.endswith("where it is"), label


def test_move_destinations_where_it_is_when_last():
    rows = move_destinations(FLAT, p(3), validate=VALIDATE)
    labels = [label for label, _, _ in rows]
    here = [label for label in labels if label.endswith("where it is")]
    assert here == ["and · 4 criteria (root) — where it is"]
    assert labels[-1] == here[0]
    assert "nrmse · d — wrap both in and" not in labels


def test_move_destinations_of_a_combinator_refuse_its_own_subtree():
    rows = move_destinations(NESTED, p(1), validate=VALIDATE)
    inside = [why for _, dest, why in rows if dest.path.startswith(p(1))]
    assert len(inside) == 5 and set(inside) == {INTO_ITSELF}
    rows = move_destinations(NESTED, p(2), validate=VALIDATE)
    why = {label: w for label, _, w in rows}
    assert why["k-of-n · 2 criteria · k=2 — at 1"] == WARN_UNDER
