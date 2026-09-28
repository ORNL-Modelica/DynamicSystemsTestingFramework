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
    plots_to_raw,
    result_magnitudes,
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


NESTED = {
    "combinator": "and",
    "children": [
        _leaf("nrmse", "unitTests.x[2]"),
        {
            "combinator": "k-of-n",
            "k": 1,
            "children": [
                _leaf("nrmse", "a.Q"),
                _leaf("tube", "unitTests.x[1]", tube_rel=0.1),
            ],
        },
        _leaf("range", "b", min_value=0.0),
    ],
}
LABELS = {"unitTests.x[1]": "pipe.T[1]", "unitTests.x[2]": "pipe.T[2]"}
#: Every member on one scale: a family folds (O1).
ONE_SCALE = {"unitTests.x[1]": 301.0, "unitTests.x[2]": 305.0, "a.Q": 1e3, "b": 2.0}


def _shape(plots):
    return [(p.subject, p.context, p.watch) for p in plots]


def _derive(leaves, labels=LABELS, extras=(), **kw):
    kw.setdefault("tracked", ())
    kw.setdefault("magnitudes", ONE_SCALE)
    return derive_plots(leaves, labels, extras, **kw)


def test_derived_plots_follow_the_tree_and_fold_a_family_where_it_first_appears():
    plots = _derive(_leaves(NESTED))
    assert _shape(plots) == [
        (("unitTests.x[1]", "unitTests.x[2]"), (), False),
        (("a.Q",), (), False),
        (("b",), (), False),
    ]
    assert [p.index for p in plots] == [0, 1, 2]
    assert all(p.kind == "time" for p in plots)


def test_the_tree_order_is_kept_not_sorted_by_label():
    tree = {
        "combinator": "and",
        "children": [_leaf("nrmse", "z.b"), _leaf("nrmse", "a.a")],
    }
    assert [p.subject for p in _derive(_leaves(tree), {})] == [("z.b",), ("a.a",)]


def test_an_extra_on_a_scored_variable_adds_context_and_an_unscored_one_is_watch():
    extras = parse_plots(
        [{"var": "c", "context": ["d"]}, {"var": "a.Q", "context": ["e", "f"]}]
    )
    plots = _derive(_leaves(NESTED), extras=extras)
    assert _shape(plots) == [
        (("unitTests.x[1]", "unitTests.x[2]"), (), False),
        (("a.Q",), ("e", "f"), False),
        (("b",), (), False),
        (("c",), ("d",), True),
    ]
    assert plots[1].series == ("a.Q", "e", "f")


def test_an_extra_on_a_family_member_decorates_the_family():
    extras = parse_plots([{"var": "unitTests.x[2]", "context": ["z"]}])
    (fam, *_) = _derive(_leaves(NESTED), extras=extras)
    assert fam.context == ("z",) and not fam.watch


def test_context_on_a_family_skips_its_members_and_repeats():
    extras = parse_plots(
        [
            {"var": "unitTests.x[1]", "context": ["unitTests.x[2]", "z"]},
            {"var": "unitTests.x[2]", "context": ["z", "w"]},
        ]
    )
    (fam, *_) = _derive(_leaves(NESTED), extras=extras)
    assert fam.context == ("z", "w")


def test_watch_plots_keep_their_list_order_after_the_derived_ones():
    extras = parse_plots([{"var": "w2"}, {"var": "b"}, {"var": "w1"}])
    plots = _derive(_leaves(NESTED), extras=extras)
    assert _shape(plots)[:3] == _shape(_derive(_leaves(NESTED)))
    assert _shape(plots)[3:] == [(("w2",), (), True), (("w1",), (), True)]


def test_branching_dynamic_pipes_folds_nothing():
    """`cat(1, T, p)[i]` mixes temperatures and pressures: ten plots."""
    names = [f"unitTests.x[{i}]" for i in range(1, 11)]
    labels = {
        n: f"cat(1, pipe2.mediums.T, pipe2.mediums.p)[{i}]"
        for i, n in enumerate(names, 1)
    }
    tree = {"combinator": "and", "children": [_leaf("nrmse", n) for n in names]}
    plots = _derive(_leaves(tree), labels, magnitudes={n: 300.0 for n in names})
    assert [p.subject for p in plots] == [(n,) for n in names]


def test_a_family_folds_by_label_not_by_name():
    """Two `unitTests.x[i]` whose expressions are unrelated names do not fold."""
    tree = {
        "combinator": "and",
        "children": [
            _leaf("nrmse", "unitTests.x[1]"),
            _leaf("nrmse", "unitTests.x[2]"),
        ],
    }
    labels = {"unitTests.x[1]": "a.T", "unitTests.x[2]": "b.p"}
    assert len(_derive(_leaves(tree), labels)) == 2


# -- O1: a family folds only on one scale -------------------------------------

#: `check_atm`: a local `x[2]` holding 1 Pa in atm and 1 atm in Pa.
CHECK_ATM = {
    "combinator": "and",
    "children": [_leaf("nrmse", "unitTests.x[1]"), _leaf("nrmse", "unitTests.x[2]")],
}
CHECK_ATM_LABELS = {"unitTests.x[1]": "x[1]", "unitTests.x[2]": "x[2]"}


def test_a_check_atm_shaped_family_on_two_scales_is_two_plots():
    magnitudes = {"unitTests.x[1]": 1 / 101325, "unitTests.x[2]": 101325.0}
    plots = _derive(_leaves(CHECK_ATM), CHECK_ATM_LABELS, magnitudes=magnitudes)
    assert [p.subject for p in plots] == [("unitTests.x[1]",), ("unitTests.x[2]",)]


def test_a_pipe_temperature_family_on_one_scale_is_one_plot():
    names = [f"pipe.T[{i}]" for i in range(1, 5)]
    tree = {"combinator": "and", "children": [_leaf("nrmse", n) for n in names]}
    magnitudes = dict(zip(names, (300.0, 310.0, 320.0, 330.0)))
    plots = _derive(_leaves(tree), {}, magnitudes=magnitudes)
    assert [p.subject for p in plots] == [tuple(names)]


def test_no_result_folds_nothing():
    names = [f"pipe.T[{i}]" for i in range(1, 5)]
    tree = {"combinator": "and", "children": [_leaf("nrmse", n) for n in names]}
    plots = _derive(_leaves(tree), {}, magnitudes=None)
    assert [p.subject for p in plots] == [(n,) for n in names]


@pytest.mark.parametrize(
    ("m1", "m2", "folds"),
    [
        (1.0, 10.0, True),  # the bound is inclusive
        (1.0, 10.01, False),
        (-0.0, 1e9, True),  # a member at zero folds with any
        (0.0, 0.0, True),
    ],
)
def test_the_scale_ratio_is_ten(m1, m2, folds):
    names = ["p[1]", "p[2]"]
    tree = {"combinator": "and", "children": [_leaf("nrmse", n) for n in names]}
    plots = _derive(_leaves(tree), {}, magnitudes={"p[1]": m1, "p[2]": m2})
    assert len(plots) == (1 if folds else 2)


def test_a_member_the_result_lacks_does_not_fold():
    """Its scale is unknown, so nothing says the family shares one."""
    names = ["p[1]", "p[2]"]
    tree = {"combinator": "and", "children": [_leaf("nrmse", n) for n in names]}
    assert len(_derive(_leaves(tree), {}, magnitudes={"p[1]": 3.0})) == 2


def test_an_unfolded_family_keeps_each_members_own_tree_position():
    """Split, each member's plot sits where the tree first names it."""
    tree = {
        "combinator": "and",
        "children": [
            _leaf("nrmse", "p[2]"),
            _leaf("nrmse", "a"),
            _leaf("nrmse", "p[1]"),
        ],
    }
    magnitudes = {"p[1]": 1.0, "p[2]": 1e6, "a": 1.0}
    plots = _derive(_leaves(tree), {}, magnitudes=magnitudes)
    assert [p.subject for p in plots] == [("p[2]",), ("a",), ("p[1]",)]


class _Values:
    def __init__(self, name, values):
        import numpy as np

        self.name = name
        self.values = np.asarray(values, dtype=float)


class _Run:
    def __init__(self, *variables, success=True):
        self.variables = list(variables)
        self.success = success


def test_result_magnitudes_read_the_largest_absolute_sample():
    """Every named variable of the result is a key — no finite sample reads
    NaN, an unknown scale — and a failed run's partial file has none."""
    import math

    result = _Run(
        _Values("a", [1.0, -5.0, 2.0]),
        _Values("b", [float("nan"), 3.0]),
        _Values("c", []),
        _Values("d", [float("nan")]),
        _Values("", [7.0]),
    )
    got = result_magnitudes(result)
    assert set(got) == {"a", "b", "c", "d"}
    assert (got["a"], got["b"]) == (5.0, 3.0)
    assert math.isnan(got["c"]) and math.isnan(got["d"])
    assert result_magnitudes(None) == {}
    assert result_magnitudes(_Run(_Values("a", [1.0]), success=False)) == {}


def test_a_member_with_no_finite_sample_does_not_fold():
    names = ["p[1]", "p[2]"]
    tree = {"combinator": "and", "children": [_leaf("nrmse", n) for n in names]}
    magnitudes = {"p[1]": 3.0, "p[2]": float("nan")}
    assert len(_derive(_leaves(tree), {}, magnitudes=magnitudes)) == 2


# -- leaves, no tree, missing -------------------------------------------------


def test_a_string_quantity_scalar_leaf_adds_no_plot():
    tree = {
        "combinator": "and",
        "children": [
            _leaf("nrmse", "x"),
            _leaf("scalar", "CPUtime", quantity="CPUtime.final", tolerance=1.0),
        ],
    }
    assert [p.subject for p in _derive(_leaves(tree), {})] == [("x",)]


def test_tree_leaves_of_no_tree_is_none_and_of_one_leaf_is_that_leaf():
    assert tree_leaves(None) is None
    (only,) = tree_leaves(parse_metric_tree(_leaf("nrmse", "x")))
    assert only.variable == "x"


def test_no_tree_is_one_plot_per_tracked_name():
    plots = derive_plots(None, {}, (), tracked=["b", "a", "b"])
    assert _shape(plots) == [(("b",), (), False), (("a",), (), False)]


def test_no_tree_folds_nothing_even_on_one_scale():
    """Nothing scored: one plot per tracked variable (O2)."""
    names = ["p[1]", "p[2]"]
    plots = derive_plots(
        None, {}, (), tracked=names, magnitudes={n: 1.0 for n in names}
    )
    assert [p.subject for p in plots] == [("p[1]",), ("p[2]",)]


def test_no_tree_still_takes_extras():
    extras = parse_plots([{"var": "a", "context": ["z"]}, {"var": "w"}])
    plots = derive_plots(None, {}, extras, tracked=["a"])
    assert _shape(plots) == [(("a",), ("z",), False), (("w",), (), True)]


def test_a_name_the_result_lacks_is_missing():
    extras = parse_plots([{"var": "a.Q", "context": ["gone"]}])
    plots = _derive(
        _leaves(NESTED), extras=extras, available=["a.Q", "b"], magnitudes=None
    )
    by = {p.subject: p.missing for p in plots}
    assert by[("a.Q",)] == ("gone",)
    assert by[("unitTests.x[1]",)] == ("unitTests.x[1]",)
    assert by[("unitTests.x[2]",)] == ("unitTests.x[2]",)
    assert by[("b",)] == ()
    # `series` keeps a missing name: readers check `missing`
    (aq,) = [p for p in plots if p.subject == ("a.Q",)]
    assert aq.series == ("a.Q", "gone")


def test_a_leaf_the_result_lacks_is_missing_though_tracked():
    """Only a name the RESULT carries is exempt (a listing of the file may
    be filtered); a leaf's name is tracked because the tree names it, and
    the result may not carry it at all."""
    tree = {"combinator": "and", "children": [_leaf("nrmse", "x"), _leaf("nrmse", "y")]}
    plots = _derive(
        _leaves(tree), {}, tracked=["x", "y"], available=[], magnitudes={"x": 1.0}
    )
    assert [p.missing for p in plots] == [(), ("y",)]


def test_a_result_backed_name_is_never_missing():
    plots = derive_plots(
        None, {}, (), available=[], tracked=["x"], magnitudes={"x": float("nan")}
    )
    assert plots[0].missing == ()


def test_with_no_name_list_nothing_is_missing():
    assert all(not p.missing for p in _derive(_leaves(NESTED)))


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
