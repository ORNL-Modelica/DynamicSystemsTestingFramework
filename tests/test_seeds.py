"""A new criterion's first values: a `range`, `tube`, `scalar` or
`dominant-frequency` leaf added with only a metric and a variable was
misconfigured or always failed. Each seeded leaf must resolve AND pass when
scored on the data it was seeded from, through the path scoring uses."""

from __future__ import annotations

import numpy as np
import pytest

from dstf.backends.types import VariableResult
from dstf.comparison.modes import resolve_mode
from dstf.comparison.seeds import (
    SeedData,
    _outward,
    quantity_kind,
    seed_quantity,
    seed_variable_number,
    seeded_leaf,
)
from dstf.comparison.tree_eval import (
    PRIMARY_BASELINE,
    BaselineView,
    ScalarSources,
    evaluate_spec,
)
from dstf.comparison.tree_spec import parse_metric_tree
from dstf.storage.baseline import DiagnosticSummary, StoredVariable

SEEDED = ["range", "tube", "scalar", "dominant-frequency"]

T = np.linspace(0.0, 10.0, 1001)
V = 300.0 + 50.0 * np.sin(2 * np.pi * 0.5 * T)
DATA = SeedData(ref=(T, V), act=(T, V))


def _score(leaf: dict, act=(T, V), ref=(T, V)):
    """``leaf`` scored as the run scores it: parsed, resolved, evaluated
    against a primary baseline holding ``ref``."""
    (at, av), (rt, rv) = act, ref
    return evaluate_spec(
        parse_metric_tree(leaf),
        var_results_by_name={
            "x": VariableResult(index=1, name="x", time=at, values=av)
        },
        baselines={
            PRIMARY_BASELINE: BaselineView(
                name=PRIMARY_BASELINE,
                ref_vars_by_name={
                    "x": StoredVariable(
                        index=1, name="x", expression="", values=rv.tolist()
                    )
                },
                shared_ref_time=np.asarray(rt),
            )
        },
        base_tolerance=1e-4,
    )


@pytest.mark.parametrize("name", SEEDED)
def test_a_seeded_leaf_resolves(name):
    leaf = seeded_leaf(name, "x", DATA)
    params = {k: v for k, v in leaf.items() if k not in ("metric", "variable")}
    resolve_mode({"mode": name, **params}, 1e-4, default_points=False)


@pytest.mark.parametrize("name", SEEDED)
def test_a_seeded_leaf_passes_on_its_own_data(name):
    """`resolve_mode` alone accepts a bare tube and a bare
    dominant-frequency; only scoring shows the seed is sensible."""
    result = _score(seeded_leaf(name, "x", DATA))
    assert result.passed, result


def test_range_seed_brackets_the_data_with_a_margin():
    leaf = seeded_leaf("range", "x", DATA)
    assert leaf["min_value"] < V.min() and leaf["max_value"] > V.max()
    assert leaf["max_value"] - leaf["min_value"] <= 1.2 * (V.max() - V.min())


def test_dominant_frequency_seed_is_the_strongest_peak():
    leaf = seeded_leaf("dominant-frequency", "x", DATA)
    (peak,) = leaf["peaks"]
    assert peak["freq"] == pytest.approx(0.5, rel=0.05)
    assert peak["tolerance_mode"] == "rel"


def test_a_peak_seeded_on_one_span_matches_on_another():
    """Bins follow the span: 0.52 Hz lands on 0.500 Hz over a 10 s
    reference and on 0.555 Hz over a 9 s result. A 5% window around the
    reference's bin holds no bin of the result; two bins wide, it does."""
    f0 = 0.52
    rt = np.linspace(0.0, 10.0, 1001)
    at = np.linspace(0.0, 9.0, 901)
    rv = np.sin(2 * np.pi * f0 * rt)
    av = np.sin(2 * np.pi * f0 * at)
    leaf = seeded_leaf("dominant-frequency", "x", SeedData(ref=(rt, rv), act=None))
    assert _score(leaf, act=(at, av), ref=(rt, rv)).passed, leaf


def test_a_peak_is_two_bins_wide():
    """0.5 Hz over 10 s is FFT bin 5: two bins either side is 2/5."""
    leaf = seeded_leaf("dominant-frequency", "x", DATA)
    (peak,) = leaf["peaks"]
    vc = _score(leaf).diagnostics["variable"]
    spectrum = next(p for p in vc.geometry if p.id.endswith("spectrum/ref"))
    assert round(peak["freq"] / (spectrum.x[1] - spectrum.x[0])) == 5
    assert peak["tolerance"] == pytest.approx(0.4)


@pytest.mark.parametrize(
    "v",
    [
        np.sin(2 * np.pi * 0.1 * T),  # one period: the peak is bin 1
        np.sin(2 * np.pi * 0.3 * T),  # bin 3: two bins is +-67%
        300.0 + 10.0 * T,  # a pure trend: its leakage peaks at bin 1
    ],
    ids=["bin-1", "bin-3", "ramp"],
)
def test_a_peak_in_the_lowest_three_bins_is_not_seeded(v):
    """Over TRANSFORM, 279 of 322 varying variables have their strongest
    peak in bins 1-3: a trend's leakage at ~1/span, not a frequency. Two bins
    around such a peak is wider than `MAX_PEAK_TOLERANCE`; the leaf stays
    bare, and scoring says to declare a peak."""
    leaf = seeded_leaf("dominant-frequency", "x", SeedData(ref=(T, v), act=None))
    assert leaf == {"metric": "dominant-frequency", "variable": "x"}


def test_a_peak_at_the_fourth_bin_is_seeded():
    """Bin 4 is the first whose two bins, +-50%, fit the cap."""
    v = np.sin(2 * np.pi * 0.4 * T)
    (peak,) = seeded_leaf("dominant-frequency", "x", SeedData(ref=(T, v), act=None))[
        "peaks"
    ]
    assert peak["tolerance"] == pytest.approx(0.5)


def test_nrmse_points_event_timing_are_not_seeded():
    for name in ("nrmse", "points", "event-timing"):
        assert seeded_leaf(name, "x", DATA) == {"metric": name, "variable": "x"}


def test_no_data_leaves_the_bare_leaf():
    assert seeded_leaf("range", "x", SeedData(ref=None, act=None)) == {
        "metric": "range",
        "variable": "x",
    }


@pytest.mark.parametrize(
    "offset, amp",
    [
        (101325.0, 1e-3),
        (1.2345678e7, 0.5),
        (573.15, 1e-4),
        (0.0, 1e-9),
        # A margin below 12 significant digits of the value: the short
        # form of `lo - m` rounded back INTO the data (1e5 +- 1e-11 seeded
        # min == max == 100000.0).
        (1e5, 1e-11),
        (293.15, 1e-12),
        (101325.0, 1e-7),
        (1.2345678e7, 1e-9),
    ],
)
@pytest.mark.parametrize("name", ["range", "tube", "scalar"])
def test_a_seed_on_a_large_offset_and_a_small_span_passes(name, offset, amp):
    """Rounded to 6 significant digits, 101325 +- 0.001 seeded min == max
    and 1.2345678e7 +- 0.5 seeded bounds that exclude the data: a seed
    rounds AWAY from the data, at its margin's leading digit."""
    v = offset + amp * np.sin(2 * np.pi * 0.5 * T)
    with_ref = seeded_leaf(name, "x", SeedData(ref=(T, v), act=(T, v)))
    assert _score(with_ref, act=(T, v), ref=(T, v)).passed, with_ref
    act_only = seeded_leaf(name, "x", SeedData(ref=None, act=(T, v)))
    assert _score(act_only, act=(T, v), ref=(T, v)).passed, act_only


@pytest.mark.parametrize("name", ["range", "tube", "scalar"])
def test_a_seed_from_a_seven_digit_reference_holds_a_float32_result(name):
    """TRANSFORM's OilWater_PTHX ``STHX.tube.mediums[2].h``: a range seeded
    from a reference stored at 7 significant digits, [130450.58, 130451.02],
    excluded its float32 result at 130451.03125. 5% of a 0.4 span is below
    the storage's own rounding; the margin is never under 1e-6 of the value."""
    x = 130450.81 + 0.23 * np.sin(2 * np.pi * 0.5 * T)  # 130450.58..130451.04
    ref = np.round(x, 1)  # 7 significant digits: 130450.6..130451.0
    act = x.astype(np.float32).astype(np.float64)
    assert act.max() > 130451.02
    leaf = seeded_leaf(name, "x", SeedData(ref=(T, ref), act=(T, act)))
    assert _score(leaf, act=(T, act), ref=(T, ref)).passed, leaf


@pytest.mark.parametrize("x, up", [(1e5 - 2e-11, False), (1e5 + 2e-11, True)])
def test_a_margin_below_twelve_digits_is_never_rounded_back_inward(x, up):
    """The 12-digit short form of 1e5 -+ 2e-11 is 100000.0, inside ``x``."""
    y = _outward(x, 1e-12, up=up)
    assert (y >= x) if up else (y <= x)


def test_a_seeds_numbers_are_short_and_rounded_away_from_the_data():
    """They land in a spec file a person reads: rounded at the leading digit
    of the margin they leave (0.05 x the span 0.0246 is 0.00123; 0.05 x
    the final value 573.15 is 28.7), outward."""
    v = 573.15 + 0.0123 * np.sin(2 * np.pi * 0.5 * T)
    ref = SeedData(ref=(T, v), act=None)
    act = SeedData(ref=None, act=(T, v))
    rng = seeded_leaf("range", "x", ref)
    assert (rng["min_value"], rng["max_value"]) == (573.136, 573.164)
    assert seeded_leaf("tube", "x", ref)["tube_abs"] == 0.002
    scalar = seeded_leaf("scalar", "x", act)
    assert (scalar["min_value"], scalar["max_value"]) == (540.0, 610.0)


def test_a_scalar_seeded_without_a_reference_has_bounds_and_needs_none():
    """A variable no criterion scores has no reference in its view: its
    scalar is seeded baseline-free, and scores with no baseline at all."""
    leaf = seeded_leaf("scalar", "x", SeedData(ref=None, act=(T, V)))
    assert set(leaf) == {"metric", "variable", "quantity", "min_value", "max_value"}
    result = evaluate_spec(
        parse_metric_tree(leaf),
        var_results_by_name={"x": VariableResult(index=1, name="x", time=T, values=V)},
        baselines={},
        base_tolerance=1e-4,
    )
    assert result.passed, result


def test_a_seed_brackets_the_reference_not_the_actual():
    ref, act = 1.0 * T / 10.0, 10.0 + T / 10.0  # 0..1 against 10..11
    data = SeedData(ref=(T, ref), act=(T, act))
    rng = seeded_leaf("range", "x", data)
    assert rng["min_value"] < 0.0 and 1.0 < rng["max_value"] < 10.0
    assert seeded_leaf("tube", "x", data)["tube_abs"] < 1.0


def test_a_reference_with_no_finite_value_falls_back_to_the_actual():
    data = SeedData(ref=(T, np.full_like(T, np.nan)), act=(T, 10.0 + T / 10.0))
    rng = seeded_leaf("range", "x", data)
    assert 9.0 < rng["min_value"] < 10.0 and rng["max_value"] > 11.0


def test_a_scalar_with_a_reference_is_its_final_value_within_one_percent():
    assert seeded_leaf("scalar", "x", DATA) == {
        "metric": "scalar",
        "variable": "x",
        "quantity": {"reduce": "final"},
        "tolerance_mode": "rel",
        "tolerance": 0.01,
    }


def test_a_scalar_whose_reference_is_empty_in_its_window_gets_bounds():
    """The reference ends at 10 s and the window starts at 12: the seed
    reads the actual, so it is the baseline-free form — a tolerance against
    a baseline with no samples to reduce failed on its own data."""
    t_ref, t_act = np.linspace(0.0, 10.0, 101), np.linspace(0.0, 20.0, 201)
    ref, act = (t_ref, 300.0 + t_ref), (t_act, 300.0 + t_act)
    window = {"start": 12.0, "end": 18.0}
    data = SeedData(ref=ref, act=act).within(window)
    leaf = {**seeded_leaf("scalar", "x", data), "window": window}
    assert "tolerance" not in leaf and {"min_value", "max_value"} <= set(leaf)
    assert _score(leaf, act=act, ref=ref).passed, leaf


def test_a_scalar_whose_reference_is_all_nan_gets_bounds():
    ref = (T, np.full_like(T, np.nan))
    leaf = seeded_leaf("scalar", "x", SeedData(ref=ref, act=(T, V)))
    assert "tolerance" not in leaf and {"min_value", "max_value"} <= set(leaf)
    assert _score(leaf, act=(T, V), ref=ref).passed, leaf


def test_a_scalars_bounds_are_around_its_final_value_not_its_first():
    v = 100.0 + 10.0 * T  # 100 -> 200
    leaf = seeded_leaf("scalar", "x", SeedData(ref=None, act=(T, v)))
    assert (leaf["min_value"], leaf["max_value"]) == (190.0, 210.0)


@pytest.mark.parametrize("with_ref", [True, False], ids=["ref", "act-only"])
@pytest.mark.parametrize("name", SEEDED)
def test_flat_zero_data_seeds_without_raising(name, with_ref):
    """A flat zero series has neither a span nor a magnitude: the margin
    falls back to 1, and each seeded leaf passes on it (a
    dominant-frequency has no peak to seed and stays bare)."""
    z = np.zeros_like(T)
    leaf = seeded_leaf(
        name, "x", SeedData(ref=(T, z) if with_ref else None, act=(T, z))
    )
    if name == "dominant-frequency":
        assert leaf == {"metric": name, "variable": "x"}
        return
    assert _score(leaf, act=(T, z), ref=(T, z)).passed, leaf


#: A ramp 300 -> 400 over 0..10 s, scored only over 2..5 s (320..350).
RAMP = 300.0 + 10.0 * T
WINDOW = {"start": 2.0, "end": 5.0}


def test_a_windowed_scalar_is_seeded_from_its_windows_final_value():
    data = SeedData(ref=None, act=(T, RAMP)).within(WINDOW)
    leaf = {**seeded_leaf("scalar", "x", data), "window": WINDOW}
    assert (leaf["min_value"], leaf["max_value"]) == (330.0, 370.0)
    assert _score(leaf, act=(T, RAMP), ref=(T, RAMP)).passed, leaf


def test_a_windowed_range_brackets_only_its_window():
    data = SeedData(ref=(T, RAMP), act=(T, RAMP)).within(WINDOW)
    leaf = {**seeded_leaf("range", "x", data), "window": WINDOW}
    assert (leaf["min_value"], leaf["max_value"]) == (318.0, 352.0)
    assert _score(leaf, act=(T, RAMP), ref=(T, RAMP)).passed, leaf


@pytest.mark.parametrize(
    "window, first, last",
    [
        (None, 0.0, 10.0),
        ({}, 0.0, 10.0),
        ({"start": 2.0}, 2.0, 10.0),
        ({"end": 5.0}, 0.0, 5.0),
    ],
)
def test_a_window_may_leave_either_end_open(window, first, last):
    t, _ = SeedData(ref=(T, RAMP), act=None).within(window).ref
    assert (t[0], t[-1]) == (first, last)


def test_a_hand_written_window_bound_may_be_a_string():
    """`parse_metric_tree` reads a window's bounds with float(), so a spec
    may hold ``"start": "2"``; the seed reads it the same way."""
    t, _ = SeedData(ref=(T, RAMP), act=None).within({"start": "2", "end": "5"}).ref
    assert (t[0], t[-1]) == (2.0, 5.0)


# -- a listed name's number (U2, K1; D162) ---------------------------------------


def _number(name, reduce="final", **params):
    return {
        "metric": "scalar",
        "variable": name,
        "quantity": {"reduce": reduce},
        **params,
    }


def test_a_count_is_seeded_exact():
    leaf = seed_variable_number("EventCounter", np.array([0.0, 3.0, 7.0]), into="and")
    assert leaf == _number("EventCounter", tolerance=0, tolerance_mode="abs")


def test_cpu_time_is_seeded_relative_even_when_its_final_is_whole():
    """A tiny model's CPUtime can be 0.0 or 1.0 at the end: a whole number,
    and no count — seeded as one, a next run's 0.01 s would fail it."""
    for values in ([0.0, 0.5, 1.25], [0.0, 0.0, 0.0], [0.0, 1.0, 1.0]):
        (leaf,) = seed_variable_number("CPUtime", np.array(values), into="and")[
            "children"
        ]
        assert (leaf["tolerance"], leaf["tolerance_mode"]) == (0.1, "rel"), values


def test_cpu_time_is_seeded_inside_a_warn_and_a_count_bare():
    """A run's CPU time moves with the machine's load (`--parallel`): in an
    `and` its criterion warns, never fails; a count only moves with the
    model."""
    node = seed_variable_number("CPUtime", np.array([0.0, 0.2, 0.4]), into="and")
    assert node == {
        "combinator": "warn",
        "children": [_number("CPUtime", tolerance=0.1, tolerance_mode="rel")],
    }
    assert "combinator" not in seed_variable_number(
        "EventCounter", np.array([0.0, 3.0]), into="and"
    )
    # Any other name is no time, whatever its samples: the ordinary seed,
    # bare (D162).
    assert "combinator" not in seed_variable_number(
        "Wall", np.array([0.0, 2.5]), into="and"
    )
    parse_metric_tree(node)  # a tree the evaluator reads


@pytest.mark.parametrize("into", ["or", "k-of-n", "weighted"])
def test_cpu_time_is_seeded_bare_where_a_warn_would_pass_its_parent(into):
    """A `warn` child passes whatever it scores: in an `or` or a `k-of-n` it
    would pass its parent with nothing else passing. Only an `and` takes the
    warn; anywhere else the leaf goes bare, with its 10%."""
    leaf = seed_variable_number("CPUtime", np.array([0.0, 0.2, 0.4]), into=into)
    assert leaf == _number("CPUtime", tolerance=0.1, tolerance_mode="rel")


@pytest.mark.parametrize("values", [[0.0, 2.0, 9.0], [0.0, 2.0, 9.5]])
def test_another_name_is_never_a_kind_guessed_from_its_samples(values):
    """Whole samples made a count, fractional ones a time; now any name
    but CPUtime and EventCounter takes the ordinary scalar seed (bounds
    around its final)."""
    seed = seed_variable_number("Steps", np.array(values), into="and")
    assert set(seed) == {"metric", "variable", "quantity", "min_value", "max_value"}
    assert seed["min_value"] < values[-1] < seed["max_value"]


def test_a_number_leaf_parses_and_scores_its_own_data():
    """Scored from the summaries: the run's recorded EventCounter against
    the baseline's stored final."""
    t = np.array([0.0, 1.0, 2.0])
    diag = VariableResult(
        index=0, time=t, values=np.array([0.0, 2.0, 5.0]), name="EventCounter"
    )
    raw = seed_variable_number("EventCounter", diag.values, into="and")
    spec = parse_metric_tree(raw)
    base = BaselineView(
        name=PRIMARY_BASELINE,
        ref_vars_by_name={},
        shared_ref_time=np.array([]),
        statistics={},
        diagnostics=[DiagnosticSummary(name="EventCounter", final=5.0)],
    )
    got = evaluate_spec(
        spec,
        {},
        {PRIMARY_BASELINE: base},
        1e-4,
        scalars=ScalarSources(statistics={}, diagnostics=[diag]),
    )
    assert got.passed, got
    vc = got.diagnostics["variable"]
    assert (vc.actual_final, vc.reference_final) == (5.0, 5.0)


# -- a quantity's kind and its seed (U3) ------------------------------------------


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("translation.nonlinear_count", "count"),
        ("translation.continuous_time_states", "count"),
        ("simulation.state_events", "count"),
        ("simulation.result_points", "count"),
        ("simulation.accepted_steps", "count"),
        ("EventCounter", "count"),
        ("simulation.cpu_time_integration", "time"),
        ("simulation.wall_time", "time"),
        ("timing.total_wall", "time"),
        ("timing.frontend", "time"),
        ("timing.anything", "time"),
    ],
)
def test_a_quantity_kind_by_its_path(path, kind):
    assert quantity_kind(path) == kind


def test_a_statistic_is_never_a_kind_guessed_from_samples():
    """A path's kind is its path's: a top-level `Wall` is a count."""
    assert quantity_kind("Wall") == "count"


def test_a_count_is_seeded_exact_and_bare():
    assert seed_quantity("translation.nonlinear_count", "count", into="and") == {
        "metric": "scalar",
        "quantity": "translation.nonlinear_count",
        "tolerance": 0,
        "tolerance_mode": "abs",
    }


def test_a_time_under_and_is_seeded_in_a_warn():
    node = seed_quantity("timing.total_wall", "time", into="and")
    assert node == {
        "combinator": "warn",
        "children": [
            {
                "metric": "scalar",
                "quantity": "timing.total_wall",
                "tolerance": 0.1,
                "tolerance_mode": "rel",
            }
        ],
    }
    parse_metric_tree(node)


@pytest.mark.parametrize("into", ["or", "k-of-n", "warn", "weighted"])
def test_a_time_elsewhere_is_seeded_bare(into):
    leaf = seed_quantity("CPUtime", "time", into=into)
    assert leaf == {
        "metric": "scalar",
        "quantity": "CPUtime",
        "tolerance": 0.1,
        "tolerance_mode": "rel",
    }
