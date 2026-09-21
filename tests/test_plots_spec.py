"""The optional `plots` key of a test_spec.json entry (brief §3.10)."""

from __future__ import annotations

import json

import pytest

from conftest import make_test_model
from dstf.config import ConfigError
from dstf.discovery.plots import (
    PlotSpec,
    PlotsSpecError,
    ResolvedPlot,
    SeriesSpec,
    default_plots,
    parse_plots,
    plots_to_raw,
    resolved_plots,
    tracked_names,
)
from dstf.discovery.spec_parser import spec_models_from_document
from dstf.discovery.test_registry import merge_tests, spec_tests_by_model

TWO = [
    {"kind": "time", "series": [{"var": "x"}, {"var": "der(x)"}]},
    {"series": [{"var": "pipe.T[1]"}]},
]


def test_parse_reads_plots_and_defaults_kind_to_time():
    plots = parse_plots(TWO)
    assert plots == (
        PlotSpec(series=(SeriesSpec("x"), SeriesSpec("der(x)")), kind="time"),
        PlotSpec(series=(SeriesSpec("pipe.T[1]"),), kind="time"),
    )


def test_round_trip_always_writes_kind():
    assert plots_to_raw(parse_plots(TWO)) == [
        {"kind": "time", "series": [{"var": "x"}, {"var": "der(x)"}]},
        {"kind": "time", "series": [{"var": "pipe.T[1]"}]},
    ]


def test_an_empty_list_is_no_plots_not_the_default():
    assert parse_plots([]) == ()


@pytest.mark.parametrize(
    ("raw", "needle"),
    [
        ({"series": []}, "plots: must be a list"),
        ([["x"]], "plots[0]: must be an object"),
        ([{"series": [{"var": "x"}], "title": "T"}], "plots[0]: unknown key 'title'"),
        ([{"kind": "xy", "series": [{"var": "x"}]}], "plots[0].kind: 'xy'"),
        ([{"kind": "time"}], "plots[0].series: must be a non-empty list"),
        ([{"series": []}], "plots[0].series: must be a non-empty list"),
        ([{"series": ["x"]}], "plots[0].series[0]: must be an object"),
        (
            [{"series": [{"var": ""}]}],
            "plots[0].series[0].var: must be a non-empty string",
        ),
        (
            [{"series": [{"var": 3}]}],
            "plots[0].series[0].var: must be a non-empty string",
        ),
        (
            [{"series": [{"var": "x", "expr": "x-1"}]}],
            "plots[0].series[0]: unknown key 'expr'",
        ),
        (
            [{"series": [{"var": "pipe.T[*]"}]}],
            "plots[0].series[0].var: 'pipe.T[*]' is a pattern",
        ),
        (
            [{"series": [{"var": "x"}, {"var": "x"}]}],
            "plots[0].series[1].var: 'x' is already on this plot",
        ),
    ],
)
def test_each_refusal_names_where_and_what(raw, needle):
    with pytest.raises(PlotsSpecError) as e:
        parse_plots(raw)
    assert needle in str(e.value)


def test_a_variable_may_be_on_two_plots():
    raw = [{"series": [{"var": "x"}]}, {"series": [{"var": "x"}, {"var": "y"}]}]
    assert len(parse_plots(raw)) == 2


def test_default_is_one_plot_per_tracked_variable_in_order():
    assert default_plots(["b", "a"]) == (
        PlotSpec(series=(SeriesSpec("b"),)),
        PlotSpec(series=(SeriesSpec("a"),)),
    )


def test_resolved_plots_absent_key_follows_the_tracked_list():
    test = make_test_model("Lib.T")
    assert test.plots is None
    assert resolved_plots(test, ["x", "y"], available=["x", "y", "z"]) == (
        ResolvedPlot(index=0, kind="time", series=("x",), missing=()),
        ResolvedPlot(index=1, kind="time", series=("y",), missing=()),
    )


def test_resolved_plots_names_a_series_the_result_lacks():
    test = make_test_model("Lib.T")
    test.plots = parse_plots([{"series": [{"var": "x"}, {"var": "gone"}]}])
    assert resolved_plots(test, ["x"], available=["x", "z"]) == (
        ResolvedPlot(index=0, kind="time", series=("x",), missing=("gone",)),
    )


def test_resolved_plots_with_no_name_list_trusts_the_layout():
    """`available=None`: the backend could not list the file (decision 6)."""
    test = make_test_model("Lib.T")
    test.plots = parse_plots([{"series": [{"var": "x"}, {"var": "z"}]}])
    assert resolved_plots(test, ["x"], available=None)[0].series == ("x", "z")


def test_a_tracked_name_is_never_missing_even_if_the_file_list_lacks_it():
    """unitTests.x[i] style names are tracked by index and may be absent
    from a name list that was filtered."""
    test = make_test_model("Lib.T")
    assert resolved_plots(test, ["x"], available=[])[0].series == ("x",)


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
            _doc([{"series": []}]), base_dir=tmp_path, label="spec.json"
        )
    assert "spec.json" in str(e.value)
    assert "tests[Lib.T].plots[0].series" in str(e.value)


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


def test_an_empty_layout_survives_the_parser_and_the_merge(tmp_path):
    """`[]` is "no plots", not falsy-therefore-default (a guard: its
    mutations are `if plots_raw:` in the parser and `if spec_test.plots:`
    in the merge)."""
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


def test_a_blank_var_is_refused():
    with pytest.raises(PlotsSpecError, match=r"series\[0\]\.var: must be a non-empty"):
        parse_plots([{"series": [{"var": "  "}]}])


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


def test_a_variable_the_comparison_labels_xn_is_not_listed_twice():
    """TRANSFORM has ~70 tests whose result carries a `cat(...)` name: the
    comparison scores it as `x[N]`, and reading the RAW name here put the
    same trajectory in the list twice — a second card with its own "Add a
    criterion for cat(1, a, b)" footer."""
    result = _Result(_Var("cat(1, a, b)", 1), _Var("y", 2))
    assert tracked_names(["x[1]", "y"], result) == ["x[1]", "y"]
    # And with nothing scored the label is still what the list carries.
    assert tracked_names([], result) == ["x[1]", "y"]


def test_a_name_with_a_newline_in_it_takes_the_same_label():
    assert tracked_names([], _Result(_Var("a\nb", 4))) == ["x[4]"]


def test_a_nameless_result_variable_is_left_to_the_comparison():
    """It has no name to plot by; the comparison's own label for it arrives
    through ``scored`` when there is one."""
    assert tracked_names([], _Result(_Var("", 1), _Var("y", 2))) == ["y"]
    assert tracked_names(["x[1]"], _Result(_Var("", 1))) == ["x[1]"]


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
