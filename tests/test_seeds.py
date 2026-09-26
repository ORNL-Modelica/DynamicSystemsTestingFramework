"""A new criterion's first values: a `range`, `tube`, `scalar` or
`dominant-frequency` leaf added with only a metric and a variable was
misconfigured or always failed. Each seeded leaf must resolve AND pass when
scored on the data it was seeded from, through the path scoring uses."""

from __future__ import annotations

import numpy as np
import pytest

from dstf.backends.types import VariableResult
from dstf.comparison.modes import resolve_mode
from dstf.comparison.seeds import SeedData, seeded_leaf
from dstf.comparison.tree_eval import PRIMARY_BASELINE, BaselineView, evaluate_spec
from dstf.comparison.tree_spec import parse_metric_tree

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
                    "x": {
                        "index": 1,
                        "name": "x",
                        "time": rt.tolist(),
                        "values": rv.tolist(),
                    }
                },
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
    """Bins follow the span: 0.184 Hz lands on 0.200 Hz over a 10 s
    reference and on 0.222 Hz over a 9 s result. A 5% window around the
    reference's bin holds no bin of the result; two bins wide, it does."""
    f0 = 0.184
    rt = np.linspace(0.0, 10.0, 1001)
    at = np.linspace(0.0, 9.0, 901)
    rv = np.sin(2 * np.pi * f0 * rt)
    av = np.sin(2 * np.pi * f0 * at)
    leaf = seeded_leaf("dominant-frequency", "x", SeedData(ref=(rt, rv), act=None))
    assert _score(leaf, act=(at, av), ref=(rt, rv)).passed, leaf


def test_a_peak_at_the_first_bin_is_not_seeded_wider_than_half():
    """Two bins around bin 1 is +-200%: a window from below zero to three
    times the peak, which matches anything."""
    v = np.sin(2 * np.pi * 0.1 * T)  # one period: the peak is bin 1
    (peak,) = seeded_leaf("dominant-frequency", "x", SeedData(ref=(T, v), act=None))[
        "peaks"
    ]
    assert peak["tolerance"] == 0.5


def test_nrmse_points_event_timing_are_not_seeded():
    for name in ("nrmse", "points", "event-timing"):
        assert seeded_leaf(name, "x", DATA) == {"metric": name, "variable": "x"}


def test_no_data_leaves_the_bare_leaf():
    assert seeded_leaf("range", "x", SeedData(ref=None, act=None)) == {
        "metric": "range",
        "variable": "x",
    }


@pytest.mark.parametrize(
    "offset, amp", [(101325.0, 1e-3), (1.2345678e7, 0.5), (573.15, 1e-4), (0.0, 1e-9)]
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
