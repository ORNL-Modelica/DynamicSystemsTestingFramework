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
    zone_dest,
    zone_map,
)
from dstf.comparison.tree_spec import MetricSpecError, parse_metric_tree
from dstf.comparison.validator import role_lookup_from_names, validate_tree

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
    # The row says what changed (D3): the weight left with its child.
    assert moved.notes == ("weight 3 went with it",)


def test_from_one_weighted_into_another_both_weights_are_said():
    tree = C(
        "and",
        _weighted(L("a"), L("b"), weights=[2.5, 3]),
        _weighted(L("c"), L("d"), weights=[4, 5]),
    )
    moved = move(tree, p(0, 0), Dest("at", p(1), 1))
    assert moved.tree == C(
        "and",
        _weighted(L("b"), weights=[3]),
        _weighted(L("c"), L("a"), L("d"), weights=[4, 1, 5]),
    )
    assert moved.notes == ("weight 2.5 went with it", "weight 1 given")


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
    assert moved.notes == ("the empty and was removed", "weight 2 went with it")


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


def test_a_move_among_identical_siblings_that_leaves_the_tree_as_it_is_is_none():
    """Two equal criteria trading places change no tree: nothing to stage."""
    tree = C("and", L("x"), L("x"), L("y", metric="range"))
    assert move(tree, p(0), Dest("at", R, 2)) is None
    # A wrap is a change, even of two equal criteria.
    assert move(tree, p(0), Dest("onto", p(1))) is not None


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


def _labels(rows) -> list[tuple[str, Dest]]:
    """Each place as the menu reads it, "text — where", with its dest."""
    return [(f"{text} — {where}", dest) for text, where, dest, _ in rows]


def test_move_destinations_over_a_nested_tree():
    """Tree order, positions between a combinator's children, each named
    by the row it goes before; the node's own row is not listed, and the
    places beside it read as ONE row."""
    rows = move_destinations(NESTED, p(0), validate=VALIDATE, names={"b": "pipe.T[1]"})
    root = "and · 4 criteria (root)"
    kn = "k-of-n · 2 criteria · k=2"
    assert _labels(rows) == [
        (f"{root} — where it is", Dest("at", R, 0)),
        (f"{kn} — before nrmse · pipe.T[1]", Dest("at", p(1), 0)),
        ("nrmse · pipe.T[1] — wrap both in and", Dest("onto", p(1, 0))),
        (f"{kn} — before nrmse · c", Dest("at", p(1), 1)),
        ("nrmse · c — wrap both in and", Dest("onto", p(1, 1))),
        (f"{kn} — at the end", Dest("at", p(1), None)),
        (f"{root} — before warn · 1 criterion", Dest("at", R, 2)),
        ("warn · 1 criterion — before nrmse · d", Dest("at", p(2), 0)),
        ("nrmse · d — wrap both in and", Dest("onto", p(2, 0))),
        ("warn · 1 criterion — at the end", Dest("at", p(2), None)),
        (f"{root} — at the end", Dest("at", R, None)),
    ]
    why = {f"{t} — {w}": reason for t, w, _, reason in rows}
    assert why["warn · 1 criterion — before nrmse · d"] == INTO_WARN
    assert why["warn · 1 criterion — at the end"] == INTO_WARN
    for _text, where, dest, reason in rows:
        if reason is None:
            planned = plan_move(NESTED, p(0), dest, validate=VALIDATE)
            assert (planned is None) == (where == "where it is"), where


def test_move_destinations_number_the_places_of_siblings_that_read_the_same():
    """Of two siblings that read the same, "before nrmse · x" would not
    say which: those two places are numbered, and a sibling that reads
    apart keeps its name."""
    tree = C("and", L("x"), L("x"), L("y"), L("z"))
    rows = move_destinations(tree, p(3), validate=VALIDATE)
    root = [where for text, where, _, _ in rows if text.endswith("(root)")]
    assert root == ["at 1", "at 2", "before nrmse · y", "where it is"]


def test_move_destinations_where_it_is_when_last():
    rows = move_destinations(FLAT, p(3), validate=VALIDATE)
    labels = [label for label, _ in _labels(rows)]
    here = [label for label in labels if label.endswith("where it is")]
    assert here == ["and · 4 criteria (root) — where it is"]
    assert labels[-1] == here[0]
    assert "nrmse · d — wrap both in and" not in labels


def test_move_destinations_of_a_combinator_refuse_its_own_subtree():
    rows = move_destinations(NESTED, p(1), validate=VALIDATE)
    inside = [why for _, _, dest, why in rows if dest.path.startswith(p(1))]
    assert len(inside) == 5 and set(inside) == {INTO_ITSELF}
    rows = move_destinations(NESTED, p(2), validate=VALIDATE)
    why = {f"{t} — {w}": reason for t, w, _, reason in rows}
    assert why["k-of-n · 2 criteria · k=2 — before nrmse · b"] == WARN_UNDER


# -- what the mutation review of Part A found unpinned ------------------------


def test_a_warn_moves_into_a_weighted_d4_is_or_and_k_of_n_only():
    tree = C(
        "and",
        L("z"),
        C("warn", L("a")),
        C("weighted", L("b"), L("c"), weights=[2, 3], threshold=1.0),
    )
    moved = move(tree, p(1), Dest("at", p(2), None))
    assert moved.tree == C(
        "and",
        L("z"),
        C(
            "weighted",
            L("b"),
            L("c"),
            C("warn", L("a")),
            weights=[2, 3, 1],
            threshold=1.0,
        ),
    )
    assert moved.notes == ("weight 1 given",)


def test_a_weighted_can_always_fail_so_it_moves_into_an_or():
    group = C("weighted", C("warn", L("a")), weights=[1], threshold=1.0)
    tree = C("and", L("z"), group, C("or", L("b"), L("c")))
    assert move(tree, p(1), Dest("at", p(2), 0)) is not None


def test_validator_for_joins_the_messages_with_one_space_as_stage_does():
    raw = C("and", C("warn", L("a")), L("s", against="sc"), L("c", against="co"))
    errors = validate_tree(
        parse_metric_tree(raw), role_lookup_from_names(soft_checks=["sc"])
    )
    assert len(errors) >= 2
    with pytest.raises(MoveRefused) as e:
        VALIDATE(raw)
    assert str(e.value) == " ".join(err.message for err in errors)


def test_d4_is_said_before_the_validators_sentence():
    """p onto q wraps it inside the warn: the and it leaves always passes
    under the or (D4) AND no primary is left outside a warn (validator)."""
    tree = C("and", C("or", C("and", L("p"), C("warn", L("q"))), C("warn", L("r"))))
    with pytest.raises(MoveRefused) as e:
        move(tree, p(0, 0, 0), Dest("onto", p(0, 0, 1, 0)))
    assert str(e.value) == WARN_UNDER
    after = C(
        "and", C("or", C("and", C("warn", C("and", L("q"), L("p")))), C("warn", L("r")))
    )
    with pytest.raises(MoveRefused, match="primary"):
        VALIDATE(after)  # not vacuous: the validator refuses it too


def test_at_on_a_criterion_is_a_callers_error_not_a_refusal():
    with pytest.raises(ValueError, match="is a criterion") as e:
        plan_move(FLAT, p(0), Dest("at", p(1), 0))
    assert not isinstance(e.value, MoveRefused)


@pytest.mark.parametrize(
    ("src", "dest"), [(p(9), Dest("at", R, 0)), (p(0), Dest("at", p(9), 0))]
)
def test_a_path_that_names_no_node_is_a_callers_error(src, dest):
    with pytest.raises(ValueError, match="names no node") as e:
        plan_move(FLAT, src, dest)
    assert not isinstance(e.value, MoveRefused)


def test_d4_is_the_moves_own_rule_not_the_validators():
    with pytest.raises(MoveRefused) as e:
        plan_move(NESTED, p(2), Dest("at", p(1), 1))  # no validate=
    assert str(e.value) == WARN_UNDER


# -- the drag's zones (U4, D1 as amended: a row's top half is before it, its
# bottom half onto it; the root has only onto) -----------------------------


@pytest.mark.parametrize(
    ("path", "zone", "dest"),
    [
        (p(1, 0), "before", Dest("at", p(1), 0)),
        (p(2), "before", Dest("at", R, 2)),
        (p(0), "onto", Dest("onto", p(0))),  # a criterion: wrap both
        (p(1), "onto", Dest("onto", p(1))),  # a combinator: its end
        (R, "onto", Dest("onto", R)),
        (R, "before", None),  # nothing sits beside the root
        (p(0), "after", None),  # no such zone (the owner's ruling)
    ],
)
def test_a_zone_is_a_destination(path, zone, dest):
    assert zone_dest(path, zone) == dest


def test_the_zone_map_says_every_rows_zones_for_one_source():
    """For the k-of-n: allowed (None), where it is (""), or the refusal."""
    zones = zone_map(NESTED, p(1), validate=VALIDATE)
    assert list(zones) == [R, p(0), p(1), p(1, 0), p(1, 1), p(2), p(2, 0)]
    assert zones[R] == {"onto": None}
    assert zones[p(0)] == {"before": None, "onto": None}
    assert zones[p(1)] == {"before": "", "onto": INTO_ITSELF}
    assert zones[p(1, 0)] == {"before": INTO_ITSELF, "onto": INTO_ITSELF}
    assert zones[p(2)] == {"before": "", "onto": INTO_WARN}
    assert zones[p(2, 0)]["before"] == INTO_WARN


def test_the_zone_map_is_the_sources_own():
    """Another source, another map: d's own row is where it is."""
    zones = zone_map(NESTED, p(2, 0), validate=VALIDATE)
    assert zones[p(2, 0)] == {"before": "", "onto": INTO_ITSELF}
    assert zones[p(1)]["onto"] is None  # a criterion can fail: a k-of-n takes it
    assert zones[p(0)] == {"before": None, "onto": None}
