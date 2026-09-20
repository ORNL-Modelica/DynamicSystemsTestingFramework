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
