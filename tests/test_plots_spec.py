"""The optional `plots` key of a test_spec.json entry (brief §3.10)."""

from __future__ import annotations

import json

import pytest

from conftest import make_test_model
from dstf.comparison.tree_spec import parse_metric_tree
from dstf.config import ConfigError
from dstf.discovery.plots import (
    PlotExtra,
    PlotsSpecError,
    derive_plots,
    parse_plots,
    plot_leaves,
    plots_of_test,
    plots_to_raw,
    tracked_names,
    tree_leaves,
)
from dstf.discovery.spec_parser import spec_models_from_document
from dstf.discovery.test_registry import merge_tests, spec_tests_by_model

TWO = [
    {"var": "unitTests.x[6]", "context": ["unitTests.x[10]"]},
    {"var": "pipe2.mediums[1].p"},
]


def test_parse_reads_extras():
    assert parse_plots(TWO) == (
        PlotExtra("unitTests.x[6]", ("unitTests.x[10]",)),
        PlotExtra("pipe2.mediums[1].p"),
    )


def test_round_trip_writes_context_only_when_there_is_some():
    assert plots_to_raw(parse_plots(TWO)) == TWO


def test_an_empty_list_is_no_extras():
    assert parse_plots([]) == ()


@pytest.mark.parametrize(
    ("raw", "needle"),
    [
        ({"var": "x"}, "plots: must be a list"),
        ([["x"]], "plots[0]: must be an object"),
        ([{"var": "x", "title": "T"}], "plots[0]: unknown key 'title'"),
        ([{"var": "x", "series": []}], "plots[0]: unknown key 'series'"),
        ([{"var": "x", "kind": "time"}], "plots[0]: unknown key 'kind'"),
        ([{}], "plots[0].var: must be a non-empty string"),
        ([{"var": " "}], "plots[0].var: must be a non-empty string"),
        ([{"var": 3}], "plots[0].var: must be a non-empty string"),
        ([{"var": "pipe.T[*]"}], "plots[0].var: 'pipe.T[*]' is a pattern"),
        ([{"var": "x"}, {"var": "x"}], "plots[1].var: 'x' already has an entry"),
        ([{"var": "x", "context": "y"}], "plots[0].context: must be a list"),
        ([{"var": "x", "context": None}], "plots[0].context: must be a list"),
        ([{"var": "x", "context": [3]}], "plots[0].context[0]: must be a non-empty"),
        ([{"var": "x", "context": ["y*"]}], "plots[0].context[0]: 'y*' is a pattern"),
        ([{"var": "x", "context": ["y", "y"]}], "plots[0].context[1]: 'y' is already"),
        ([{"var": "x", "context": ["x"]}], "plots[0].context[0]: 'x' is the plot's"),
    ],
)
def test_each_refusal_names_where_and_what(raw, needle):
    with pytest.raises(PlotsSpecError) as e:
        parse_plots(raw)
    assert needle in str(e.value)


# -- `derive_plots`: the plots both tiers draw --------------------------------


def _leaves(raw):
    return tree_leaves(parse_metric_tree(raw))


def _leaf(metric, var, **params):
    return {"metric": metric, "variable": var, **params}


def _and(*names):
    return {"combinator": "and", "children": [_leaf("nrmse", n) for n in names]}


NESTED = {
    "combinator": "and",
    "children": [
        _leaf("nrmse", "pipe.T[2]"),
        {
            "combinator": "k-of-n",
            "k": 1,
            "children": [
                _leaf("nrmse", "a.Q"),
                _leaf("tube", "pipe.T[1]", tube_rel=0.1),
            ],
        },
        _leaf("range", "b", min_value=0.0),
    ],
}


def _shape(plots):
    return [(p.subject, p.context, p.watch) for p in plots]


def _derive(leaves, extras=(), **kw):
    kw.setdefault("tracked", ())
    return derive_plots(leaves, extras, **kw)


def test_derived_plots_follow_the_tree_and_fold_a_family_where_it_first_appears():
    plots = _derive(_leaves(NESTED))
    assert _shape(plots) == [
        (("pipe.T[1]", "pipe.T[2]"), (), False),
        (("a.Q",), (), False),
        (("b",), (), False),
    ]
    assert [p.index for p in plots] == [0, 1, 2]
    assert all(p.kind == "time" for p in plots)


def test_the_tree_order_is_kept_not_sorted_by_name():
    assert [p.subject for p in _derive(_leaves(_and("z.b", "a.a")))] == [
        ("z.b",),
        ("a.a",),
    ]


def test_an_extra_on_a_scored_variable_adds_context_and_an_unscored_one_is_watch():
    extras = parse_plots(
        [{"var": "c", "context": ["d"]}, {"var": "a.Q", "context": ["e", "f"]}]
    )
    plots = _derive(_leaves(NESTED), extras)
    assert _shape(plots) == [
        (("pipe.T[1]", "pipe.T[2]"), (), False),
        (("a.Q",), ("e", "f"), False),
        (("b",), (), False),
        (("c",), ("d",), True),
    ]
    assert plots[1].series == ("a.Q", "e", "f")


def test_an_extra_on_a_family_member_decorates_the_family():
    extras = parse_plots([{"var": "pipe.T[2]", "context": ["z"]}])
    (fam, *_) = _derive(_leaves(NESTED), extras)
    assert fam.context == ("z",) and not fam.watch


def test_context_on_a_family_skips_its_members_and_repeats():
    extras = parse_plots(
        [
            {"var": "pipe.T[1]", "context": ["pipe.T[2]", "z"]},
            {"var": "pipe.T[2]", "context": ["z", "w"]},
        ]
    )
    (fam, *_) = _derive(_leaves(NESTED), extras)
    assert fam.context == ("z", "w")


def test_watch_plots_keep_their_list_order_after_the_derived_ones():
    extras = parse_plots([{"var": "w2"}, {"var": "b"}, {"var": "w1"}])
    plots = _derive(_leaves(NESTED), extras)
    assert _shape(plots)[:3] == _shape(_derive(_leaves(NESTED)))
    assert _shape(plots)[3:] == [(("w2",), (), True), (("w1",), (), True)]


# -- O3: a UnitTests variable never folds; any other array always does -------

UT = [f"unitTests.x[{i}]" for i in range(1, 5)]


def test_unit_tests_variables_never_fold_whatever_their_expressions():
    """`unitTests.x[1..4]` whose expressions read `pipe.T[1..4]`: each is
    its own test quantity (the UnitTests contract) — four plots. The fold
    reads NAMES, and a UnitTests name is kept out of it."""
    plots = _derive(_leaves(_and(*UT)), unit_tests=UT)
    assert [p.subject for p in plots] == [(n,) for n in UT]


def test_an_array_tracked_by_its_names_is_one_family_plot():
    names = [f"pipe.T[{i}]" for i in range(1, 5)]
    plots = _derive(_leaves(_and(*reversed(names))))
    assert [p.subject for p in plots] == [tuple(names)]


def test_a_check_atm_shaped_unit_tests_pair_is_two_plots():
    """`check_atm`: a local `x[2]` holding 1 Pa in atm and 1 atm in Pa, as
    `unitTests.x[1]` and `unitTests.x[2]`."""
    pair = UT[:2]
    assert [p.subject for p in _derive(_leaves(_and(*pair)), unit_tests=pair)] == [
        (UT[0],),
        (UT[1],),
    ]


def test_an_array_folds_whatever_its_members_scales():
    """No scale rule (O3): a real array is one quantity, however far apart
    its members' magnitudes are."""
    plots = _derive(_leaves(_and("p[1]", "p[2]")))
    assert [p.subject for p in plots] == [("p[1]", "p[2]")]


def test_only_the_named_unit_tests_variables_stay_apart():
    """A family around a UnitTests variable still folds its other members."""
    plots = _derive(
        _leaves(_and("p[1]", UT[0], "p[2]", UT[1])), unit_tests=[UT[0], UT[1]]
    )
    assert [p.subject for p in plots] == [("p[1]", "p[2]"), (UT[0],), (UT[1],)]


# -- leaves, no tree, missing -------------------------------------------------


def test_a_string_quantity_scalar_leaf_adds_no_plot():
    tree = {
        "combinator": "and",
        "children": [
            _leaf("nrmse", "x"),
            _leaf("scalar", "CPUtime", quantity="CPUtime.final", tolerance=1.0),
        ],
    }
    assert [p.subject for p in _derive(_leaves(tree))] == [("x",)]


def test_tree_leaves_of_no_tree_is_none_and_of_one_leaf_is_that_leaf():
    assert tree_leaves(None) is None
    (only,) = tree_leaves(parse_metric_tree(_leaf("nrmse", "x")))
    assert only.variable == "x"


def test_no_tree_is_one_plot_per_tracked_name():
    plots = derive_plots(None, (), tracked=["b", "a", "b"])
    assert _shape(plots) == [(("b",), (), False), (("a",), (), False)]


def test_no_tree_folds_a_tracked_array_but_never_a_unit_tests_variable():
    """Nothing scored: one plot per tracked variable or array (O2, O3)."""
    plots = derive_plots(None, (), tracked=["p[1]", *UT[:2], "p[2]"], unit_tests=UT)
    assert [p.subject for p in plots] == [("p[1]", "p[2]"), (UT[0],), (UT[1],)]


def test_no_tree_still_takes_extras():
    extras = parse_plots([{"var": "a", "context": ["z"]}, {"var": "w"}])
    plots = derive_plots(None, extras, tracked=["a"])
    assert _shape(plots) == [(("a",), ("z",), False), (("w",), (), True)]


def test_a_name_the_result_lacks_is_missing():
    extras = parse_plots([{"var": "a.Q", "context": ["gone"]}])
    plots = _derive(_leaves(NESTED), extras, available=["a.Q", "b"])
    by = {p.subject: p.missing for p in plots}
    assert by[("a.Q",)] == ("gone",)
    assert by[("pipe.T[1]", "pipe.T[2]")] == ("pipe.T[1]", "pipe.T[2]")
    assert by[("b",)] == ()
    # `series` keeps a missing name: readers check `missing`
    (aq,) = [p for p in plots if p.subject == ("a.Q",)]
    assert aq.series == ("a.Q", "gone")


def test_with_no_name_list_nothing_is_missing():
    assert all(not p.missing for p in _derive(_leaves(NESTED)))


# -- `plot_leaves`: which tree the plots derive from --------------------------


def test_plot_leaves_are_the_authored_tree_even_with_nothing_compared():
    """A NO_REF test with a `metrics` tree: the tree names what will be
    scored, so its plots are the tree's (the controller's call on the
    review's finding 7) — never one per tracked variable."""
    test = make_test_model("Lib.T")
    test.metric_tree_spec = parse_metric_tree(_and("y", "x"))
    assert [leaf.variable for leaf in plot_leaves(test, [])] == ["y", "x"]


def test_plot_leaves_are_the_implicit_tree_of_what_was_compared():
    test = make_test_model("Lib.T")
    assert [leaf.variable for leaf in plot_leaves(test, ["b", "a"])] == ["b", "a"]


def test_plot_leaves_are_none_with_no_tree_and_nothing_compared():
    assert plot_leaves(make_test_model("Lib.T"), []) is None


def _doc(plots):
    return {"tests": [{"model": "Lib.T", "variables": ["x"], "plots": plots}]}


def test_the_spec_parser_carries_plots(tmp_path):
    (test,) = spec_models_from_document(_doc(TWO), base_dir=tmp_path, label="mem")
    assert test.plots == parse_plots(TWO)


def test_the_spec_parser_leaves_plots_none_when_absent(tmp_path):
    doc = {"tests": [{"model": "Lib.T", "variables": ["x"]}]}
    (test,) = spec_models_from_document(doc, base_dir=tmp_path, label="mem")
    assert test.plots is None


def test_a_bad_plots_key_is_a_config_error_naming_the_document(tmp_path):
    with pytest.raises(ConfigError) as e:
        spec_models_from_document(
            _doc([{"var": ""}]), base_dir=tmp_path, label="spec.json"
        )
    assert "spec.json" in str(e.value)
    assert "tests[Lib.T].plots[0].var" in str(e.value)


def test_merge_carries_the_specs_plots_onto_a_scanned_test(tmp_path):
    scanned = {"Lib.T": make_test_model("Lib.T")}
    spec = spec_models_from_document(_doc(TWO), base_dir=tmp_path, label="mem")
    (merged,) = merge_tests(scanned, spec_tests_by_model(spec))
    assert merged.plots == parse_plots(TWO)


def test_the_example_specs_parse(tmp_path):
    """No bundled example regressed: every entry's plots (none today) parse."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "examples"
    specs = sorted(root.rglob("test_spec.json"))
    assert specs, "the examples moved; point this test at them"
    for spec in specs:
        data = json.loads(spec.read_text(encoding="utf-8"))
        spec_models_from_document(data, base_dir=spec.parent, label=str(spec))


def test_an_empty_list_survives_the_parser_and_the_merge(tmp_path):
    """`[]` is "no extras", kept as the key's value so a writer can tell it
    from an absent key (`layout_op`'s add or replace)."""
    spec = spec_models_from_document(_doc([]), base_dir=tmp_path, label="mem")
    assert spec[0].plots == ()
    (merged,) = merge_tests(
        {"Lib.T": make_test_model("Lib.T")}, spec_tests_by_model(spec)
    )
    assert merged.plots == ()


def test_a_null_plots_key_is_refused_like_any_other_non_list(tmp_path):
    """Absent is the only spelling of "the default": a `null` the parser let
    through would make every later patch of the entry fail its validation."""
    with pytest.raises(ConfigError, match=r"tests\[Lib\.T\]\.plots: must be a list"):
        spec_models_from_document(_doc(None), base_dir=tmp_path, label="mem")


# -- `tracked_names`: the ONE default-layout rule both tiers draw by --------


class _Var:
    def __init__(self, name, index):
        self.name, self.index = name, index


class _Result:
    def __init__(self, *variables, success=True):
        self.variables = list(variables)
        self.success = success


def test_the_scored_names_come_first_then_the_rest_of_the_result():
    tracked = tracked_names(["x"], _Result(_Var("x", 1), _Var("y", 2)))
    assert tracked == ["x", "y"]


def test_the_scored_order_wins_when_it_differs_from_the_results():
    """The tree's order is the test author's; the result file's is the
    backend's. A variable the tree scores is drawn where the tree put it."""
    result = _Result(_Var("x", 1), _Var("y", 2), _Var("z", 3))
    assert tracked_names(["z", "y"], result) == ["z", "y", "x"]


def test_a_result_variable_is_listed_under_its_own_name_once():
    """Nothing relabels a name: a variable is listed under the name its
    result file gives it, whether or not the comparison scored it. Listing it
    under two spellings put the same trajectory in the list twice — a second
    card with its own "Add a criterion for …" footer (the 3b review)."""
    result = _Result(_Var("unitTests.x[1]", 1), _Var("y", 2))
    assert tracked_names(["unitTests.x[1]", "y"], result) == ["unitTests.x[1]", "y"]
    assert tracked_names([], result) == ["unitTests.x[1]", "y"]


def test_an_awkward_name_is_no_longer_special():
    """Text this function used to special-case — a `cat(` expression, a name
    a wrapped `.mo` line left a newline in — reached it as a variable's NAME
    while the comparison relabelled the same variable `x[N]`. Whatever a
    result calls a variable is what is listed."""
    assert tracked_names([], _Result(_Var("cat(1, a, b)", 1))) == ["cat(1, a, b)"]
    assert tracked_names([], _Result(_Var("a\nb", 4))) == ["a\nb"]


def test_a_nameless_result_variable_has_nothing_to_plot_by():
    """A backend that builds one is a bug; it is skipped rather than drawn
    under a name this function made up. Nothing puts a made-up name into
    ``scored`` either: the comparison of a nameless variable carries the
    same empty name, and an empty name is no name here."""
    assert tracked_names([], _Result(_Var("", 1), _Var("y", 2))) == ["y"]
    assert tracked_names([""], _Result(_Var("", 1))) == []


def test_a_repeated_scored_name_is_listed_once():
    """Several leaves may score one variable."""
    assert tracked_names(["x", "x", "y"], None) == ["x", "y"]


def test_a_variable_a_tree_scores_by_its_raw_name_is_not_listed_twice():
    """The tree-path variant of the same duplicate: an AUTHORED tree may name
    the variable as the result file spells it, and the rest-of-result pass
    then added the comparison's label for the SAME trajectory beside it."""
    result = _Result(_Var("cat(1, a, b)", 1), _Var("y", 2))
    assert tracked_names(["cat(1, a, b)", "y"], result) == ["cat(1, a, b)", "y"]


def test_a_failed_simulations_partial_result_contributes_nothing():
    """A backend that stopped mid-run still writes what it had. Those are not
    the test's plots: the page drew a card per half-written variable, and the
    report a section (the final review). The scored names, if a comparison
    somehow has any, still stand."""
    partial = _Result(_Var("x", 1), _Var("y", 2), success=False)
    assert tracked_names([], partial) == []
    assert tracked_names(["x"], partial) == ["x"]


def test_no_result_leaves_the_scored_names_alone():
    assert tracked_names(["x"], None) == ["x"]


# -- `plots_of_test`: the one function both tiers call -----------------------


def test_a_failed_run_has_no_plots_whatever_its_tree_and_extras():
    """O2, literally: a partial file is not the test's plots."""
    test = make_test_model("Lib.T", plots=parse_plots([{"var": "w"}]))
    test.metric_tree_spec = parse_metric_tree(_and("x"))
    partial = _Result(_Var("x", 1), _Var("w", 2), success=False)
    assert plots_of_test(test, ["x"], partial, listed=["x", "w"]) == ()


def test_with_no_listing_what_the_result_carries_is_what_the_plots_can_draw():
    """``listed=None`` (the report lists the file only for a test with
    extras): the result's own names are all there is, so a leaf it lacks is
    missing. A listing that leaves out a name the result carries does not
    make it missing: the file's listing may be filtered."""
    test = make_test_model("Lib.T")
    test.metric_tree_spec = parse_metric_tree(_and("a", "gone"))
    result = _Result(_Var("a", 1))
    plots = plots_of_test(test, [], result, listed=None)
    assert [(p.subject, p.missing) for p in plots] == [
        (("a",), ()),
        (("gone",), ("gone",)),
    ]
    plots = plots_of_test(test, [], result, listed=["other"])
    assert [(p.subject, p.missing) for p in plots] == [
        (("a",), ()),
        (("gone",), ("gone",)),
    ]


def test_unit_tests_elements_are_asked_of_the_leaves_and_the_tracked_names():
    """A tree's UnitTests leaves the result lacks never fold (they are
    elements all the same), and neither do the tracked elements of a test
    with no tree."""
    test = make_test_model("Lib.T")
    test.metric_tree_spec = parse_metric_tree(_and(*UT[:2]))
    plots = plots_of_test(test, [], _Result(), listed=None)
    assert [p.subject for p in plots] == [(UT[0],), (UT[1],)]
    untreed = make_test_model("Lib.T")
    result = _Result(_Var(UT[0], 1), _Var(UT[1], 2))
    plots = plots_of_test(untreed, [], result, listed=None)
    assert [p.subject for p in plots] == [(UT[0],), (UT[1],)]
