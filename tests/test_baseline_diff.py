"""`baseline diff`'s facts (``storage.baseline_diff``): which baselines
changed between two revisions of a reference root — paired by model id,
never by file name — and by how much, per variable, as a percent of the old
baseline's scale (the nrmse rule's), at the time it happens."""

import hashlib
import json
import math
import os

import numpy as np
import pytest

from conftest import GitRepo
from dstf.reporting.stats_rows import report_text, statistics_rows
from dstf.storage.baseline import DiagnosticSummary, Sampling
from dstf.storage.baseline_diff import (
    DiffError,
    compare_series,
    diff_baselines,
    machine_dependent,
)

# --- one series against another ---------------------------------------------


def _t(*values):
    return np.array(values, dtype=float)


def test_identical_series_change_by_nothing():
    got = compare_series(_t(0, 1, 2), _t(0, 5, 10), _t(0, 1, 2), _t(0, 5, 10))
    assert (got.largest, got.scale, got.compared, got.outside) == (0.0, "range", 3, 0)


def test_the_largest_change_is_a_percent_of_old_s_range_at_its_time():
    # range 10; the new run is 0.5 higher at t=2 only
    got = compare_series(
        _t(0, 1, 2, 3), _t(0, 5, 10, 10), _t(0, 1, 2, 3), _t(0, 5, 10.5, 10)
    )
    assert got.largest == pytest.approx(5.0)
    assert (got.at, got.scale) == (2.0, "range")


def test_the_scale_is_old_s_not_new_s():
    # old spans 0..1, new 0..100: the change of 99 at t=1 is 9900 % of old's range
    got = compare_series(_t(0, 1), _t(0, 1), _t(0, 1), _t(0, 100))
    assert got.largest == pytest.approx(9900.0)


def test_new_is_read_on_old_s_grid_by_straight_lines():
    # new holds only the ends; at t=1 its line reads 5, old holds 6: 1 of 10
    got = compare_series(_t(0, 1, 2), _t(0, 6, 10), _t(0, 2), _t(0, 10))
    assert got.largest == pytest.approx(10.0)
    assert got.at == 1.0


def test_old_s_points_outside_new_s_span_are_counted_not_compared():
    got = compare_series(
        _t(0, 1, 2, 3, 4), _t(0, 1, 2, 30, 40), _t(0, 1, 2), _t(0, 1, 2)
    )
    assert (got.compared, got.outside, got.largest) == (3, 2, 0.0)


def test_an_end_time_rounded_through_float32_is_still_inside_new_s_span():
    """A single-precision run ends at float32(11.9) = 11.8999996: old's
    samples at 11.9 are the same instant (`SAME_INSTANT`), not outside the
    new span — a real test's largest change, at its end, was dropped."""
    end = float(np.float32(11.9))
    got = compare_series(_t(0, 6, 11.9), _t(0, 5, 10), _t(0, 6, end), _t(0, 5, 10.1))
    assert (got.outside, got.compared) == (0, 3)
    assert (got.largest, got.at) == (pytest.approx(1.0), 11.9)


def test_a_constant_with_rounding_noise_is_scored_against_its_value():
    """A range under 1e-10 of the value is a constant (the nrmse rule's
    `error_scale`): 1e-7 of noise on 184917 is no range to divide by."""
    old = np.full(5, 184917.0)
    old[2] += 1e-7
    got = compare_series(_t(0, 1, 2, 3, 4), old, _t(0, 1, 2, 3, 4), old + 0.5)
    assert got.scale == "value"
    assert got.largest == pytest.approx(0.5 / 184917.0 * 100, rel=1e-9)


def test_an_old_series_of_zeros_changes_by_the_absolute_difference():
    got = compare_series(_t(0, 1, 2), _t(0, 0, 0), _t(0, 1, 2), _t(0, 0.001, 0))
    assert got.scale == "absolute"
    assert (got.largest, got.at) == (pytest.approx(0.001), 1.0)


def test_at_an_event_old_s_samples_meet_new_s_before_and_after_it():
    """An event is stored as two samples at one time. Old's first meets
    new's value just before, its second new's value just after: a 0.1 %
    move at t=2 reads 0.1 %, where a straight line through the step read
    100 %."""
    t = _t(0, 0.5, 1, 1, 1.5, 2, 3)
    old = _t(0, 0, 0, 10, 10, 10, 10)
    new = old.copy()
    new[5] = 10.01
    got = compare_series(t, old, t, new)
    assert got.largest == pytest.approx(0.1)
    assert got.at == 2.0


def test_an_event_new_does_not_have_reads_new_s_line():
    # new has no event at t=1: both of old's samples there meet new's 5
    got = compare_series(_t(0, 1, 1, 2), _t(0, 0, 10, 10), _t(0, 2), _t(0, 10))
    assert got.largest == pytest.approx(50.0)
    assert got.at == 1.0


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_a_non_finite_new_value_is_a_non_finite_difference(bad):
    new = _t(0, 1, 2, 3, 4)
    new[3] = bad
    got = compare_series(_t(0, 1, 2, 3, 4), _t(0, 1, 2, 3, 4), _t(0, 1, 2, 3, 4), new)
    assert (got.non_finite, got.non_finite_at) == (1, 3.0)
    # the finite points are still compared: no change there
    assert (got.compared, got.largest) == (4, 0.0)


def test_a_non_finite_old_value_is_a_non_finite_difference_and_no_scale():
    old = _t(0, 1, 2, 3, 4)
    old[1] = math.nan
    new = _t(0, 1, 2, 3, 4.4)
    got = compare_series(_t(0, 1, 2, 3, 4), old, _t(0, 1, 2, 3, 4), new)
    assert (got.non_finite, got.non_finite_at) == (1, 1.0)
    # the range is of old's finite values: 0..4, and 0.4 of it at t=4
    assert got.largest == pytest.approx(10.0)
    assert got.at == 4.0


def test_the_same_non_finite_value_on_both_sides_is_no_difference():
    old = _t(0, math.nan, 2, math.inf)
    got = compare_series(_t(0, 1, 2, 3), old, _t(0, 1, 2, 3), old.copy())
    assert (got.non_finite, got.largest, got.compared) == (0, 0.0, 2)


def test_old_holding_nothing_finite_is_compared_in_absolute_terms():
    got = compare_series(_t(0, 1), _t(math.nan, math.nan), _t(0, 1), _t(0, 1))
    assert (got.non_finite, got.largest, got.scale) == (2, None, "absolute")


@pytest.mark.parametrize(
    ("old_t", "new_t", "note"),
    [
        ([], [0, 1], "no time in the old baseline"),
        ([0, 1], [], "no time in the new baseline"),
    ],
)
def test_an_empty_time_is_said_and_nothing_is_compared(old_t, new_t, note):
    got = compare_series(_t(*old_t), _t(*old_t), _t(*new_t), _t(*new_t))
    assert (got.note, got.largest, got.compared) == (note, None, 0)


def test_values_that_do_not_match_the_time_are_said():
    got = compare_series(_t(0, 1, 2), _t(0, 1), _t(0, 1, 2), _t(0, 1, 2))
    assert got.note == "the old baseline holds 2 values for 3 times"
    assert got.largest is None


def test_a_time_out_of_order_is_said():
    got = compare_series(_t(0, 1, 2), _t(0, 1, 2), _t(0, 2, 1), _t(0, 1, 2))
    assert got.note == "the new baseline's time is not in order"


def test_a_time_that_is_not_finite_is_said():
    got = compare_series(_t(0, math.nan), _t(0, 1), _t(0, 1), _t(0, 1))
    assert got.note == "the old baseline's time is not finite"


# --- a reference root at two revisions ---------------------------------------


def _ref(model_id, time=(0.0, 1.0, 2.0), variables=None, **fields) -> str:
    """A primary baseline file's text as DSTF writes one: ``variables`` by
    name → values, or (name, expression, values)."""
    variables = {"x": [0.0, 1.0, 2.0]} if variables is None else variables
    entries = []
    for i, item in enumerate(
        variables.items() if isinstance(variables, dict) else variables, start=1
    ):
        name, expression, values = item if len(item) == 3 else (*item[:1], "", item[1])
        entries.append(
            {"index": i, "name": name, "expression": expression, "values": values}
        )
    doc = {
        "model_id": model_id,
        "status": "active",
        "date_added": "2026-01-01",
        "simulation": {"stop_time": 2.0, "method": "dassl"},
        **fields,
        "time": list(time),
        "variables": entries,
    }
    return json.dumps(doc, indent=2)


@pytest.fixture
def lib(tmp_path):
    repo = GitRepo(tmp_path / "lib")
    (repo.top / "refs").mkdir()
    return repo


def _write(repo, path, text):
    target = repo.top / "refs" / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _tests(diff):
    return {t.model_id: t for t in diff.tests}


def test_a_renamed_file_with_the_same_baseline_is_no_change(lib):
    """Pairing is by model id: a numbered file renamed to its code (and its
    old test_id dropped) holds the same baseline — listed nowhere, counted
    as differing in metadata only."""
    _write(lib, "Dymola/linux/ref_0001.json", _ref("Lib.A", test_id="ref_0001"))
    _write(lib, "Dymola/linux/ref_0002.json", _ref("Lib.B", test_id="ref_0002"))
    one = lib.commit("numbered")
    lib.git("mv", "refs/Dymola/linux/ref_0001.json", "refs/Dymola/linux/ref_aaaa.json")
    lib.git("mv", "refs/Dymola/linux/ref_0002.json", "refs/Dymola/linux/ref_bbbb.json")
    _write(lib, "Dymola/linux/ref_aaaa.json", _ref("Lib.A"))
    _write(lib, "Dymola/linux/ref_bbbb.json", _ref("Lib.B"))
    two = lib.commit("by code")
    diff = diff_baselines(lib.top / "refs", one, two)
    assert diff.tests == []
    assert (diff.unchanged, diff.metadata_only) == (2, 2)


def test_a_changed_baseline_lists_each_variable_s_change(lib):
    _write(
        lib,
        "Dymola/linux/ref_a.json",
        _ref(
            "Lib.A",
            variables=[("unitTests.x[1]", "pipe.T", [0, 5, 10]), ("y", "", [1, 1, 1])],
        ),
    )
    one = lib.commit("one")
    _write(
        lib,
        "Dymola/linux/ref_a.json",
        _ref(
            "Lib.A",
            variables=[
                ("unitTests.x[1]", "pipe.T", [0, 5.5, 10]),
                ("y", "", [1, 1, 1]),
            ],
        ),
    )
    two = lib.commit("two")
    (test,) = diff_baselines(lib.top / "refs", one, two).tests
    base = test.baselines["Dymola/linux"]
    assert (base.status, base.old_file, base.new_file) == (
        "changed",
        "ref_a.json",
        "ref_a.json",
    )
    x, y = base.variables
    assert (x.name, x.label, x.status) == ("unitTests.x[1]", "pipe.T", "changed")
    assert (x.change.largest, x.change.at) == (pytest.approx(5.0), 1.0)
    assert (y.name, y.change.largest, y.change.scale) == ("y", 0.0, "value")


def test_a_variable_on_one_side_only_is_new_or_removed(lib):
    _write(
        lib,
        "Dymola/linux/ref_a.json",
        _ref("Lib.A", variables={"x": [0, 1, 2], "gone": [1, 2, 3]}),
    )
    one = lib.commit("one")
    _write(
        lib,
        "Dymola/linux/ref_a.json",
        _ref("Lib.A", variables={"x": [0, 1, 2], "added": [3, 2, 1]}),
    )
    two = lib.commit("two")
    (test,) = diff_baselines(lib.top / "refs", one, two).tests
    got = {
        v.name: (v.status, v.change) for v in test.baselines["Dymola/linux"].variables
    }
    assert got["gone"] == ("removed", None)
    assert got["added"] == ("new", None)
    assert got["x"][0] == "changed"


def test_an_expression_that_changed_under_one_name_is_said(lib):
    _write(
        lib,
        "Dymola/linux/ref_a.json",
        _ref("Lib.A", variables=[("x", "a.T", [0, 1, 2])]),
    )
    one = lib.commit("one")
    _write(
        lib,
        "Dymola/linux/ref_a.json",
        _ref("Lib.A", variables=[("x", "b.T", [0, 1, 2])]),
    )
    two = lib.commit("two")
    (test,) = diff_baselines(lib.top / "refs", one, two).tests
    (x,) = test.baselines["Dymola/linux"].variables
    assert x.expression == ("a.T", "b.T")


def test_a_variable_renamed_with_its_label_kept_is_paired_as_a_rename(lib):
    """A library renamed every UnitTests variable at one commit (`PHS.p`
    became `unitTests.x[1]`, its old name now the expression): paired by
    name, no number crossed that commit. Left over on both sides with one
    label, the two are one variable."""
    _write(
        lib,
        "Dymola/linux/ref_a.json",
        _ref("Lib.A", variables={"PHS.p": [0, 5, 10], "y": [1, 2, 3]}),
    )
    one = lib.commit("one")
    _write(
        lib,
        "Dymola/linux/ref_a.json",
        _ref(
            "Lib.A",
            variables=[
                ("unitTests.x[1]", "PHS.p", [0, 5.5, 10]),
                ("y", "", [1, 2, 3]),
            ],
        ),
    )
    two = lib.commit("two")
    (test,) = diff_baselines(lib.top / "refs", one, two).tests
    p, y = test.baselines["Dymola/linux"].variables
    assert (p.name, p.label, p.status) == ("unitTests.x[1]", "PHS.p", "changed")
    assert p.renamed == ("PHS.p", "unitTests.x[1]")
    assert (p.change.largest, p.change.at) == (pytest.approx(5.0), 1.0)
    # the rename says it: no expression change beside it
    assert p.expression is None
    assert (y.name, y.renamed) == ("y", None)


def test_a_label_two_variables_of_a_side_share_pairs_nothing(lib):
    _write(
        lib,
        "Dymola/linux/ref_a.json",
        _ref("Lib.A", variables=[("a", "T", [0, 1, 2]), ("b", "T", [0, 1, 3])]),
    )
    one = lib.commit("one")
    _write(
        lib,
        "Dymola/linux/ref_a.json",
        _ref("Lib.A", variables=[("c", "T", [0, 1, 2])]),
    )
    two = lib.commit("two")
    (test,) = diff_baselines(lib.top / "refs", one, two).tests
    got = [(v.name, v.status) for v in test.baselines["Dymola/linux"].variables]
    assert got == [("a", "removed"), ("b", "removed"), ("c", "new")]


def test_a_name_pairs_before_a_label_does(lib):
    """The name is the identity (D132): `a` is paired by its name although
    its label moved to `c`; the leftovers `b` and `c` hold other labels."""
    _write(
        lib,
        "Dymola/linux/ref_a.json",
        _ref("Lib.A", variables=[("a", "p", [0, 1, 2]), ("b", "q", [0, 1, 2])]),
    )
    one = lib.commit("one")
    _write(
        lib,
        "Dymola/linux/ref_a.json",
        _ref("Lib.A", variables=[("a", "q", [0, 1, 2]), ("c", "p", [0, 1, 2])]),
    )
    two = lib.commit("two")
    (test,) = diff_baselines(lib.top / "refs", one, two).tests
    got = [
        (v.name, v.status, v.renamed, v.expression)
        for v in test.baselines["Dymola/linux"].variables
    ]
    assert got == [
        ("a", "changed", None, ("p", "q")),
        ("b", "removed", None, None),
        ("c", "new", None, None),
    ]


def _sampling(setting=500, source=900, stored=3, precision="double"):
    return {
        "setting": setting,
        "source_points": source,
        "stored_points": stored,
        "precision": precision,
    }


def test_settings_sampling_and_comparison_changes_are_listed(lib):
    """Sampling's setting and precision are settings; its point counts are
    what the run produced, listed apart (``samples``), never as a setting."""
    _write(
        lib,
        "Dymola/linux/ref_a.json",
        _ref(
            "Lib.A",
            comparison={"tolerance": 0.01},
            sampling=_sampling(300, 800, 3, "single"),
        ),
    )
    one = lib.commit("one")
    text = json.loads(
        _ref(
            "Lib.A",
            comparison={
                "tolerance": 0.02,
                "variable_overrides": {"x": {"mode": "tube"}},
            },
            sampling=_sampling(500, 900, 3, "double"),
        )
    )
    text["simulation"]["method"] = "Dassl"
    _write(lib, "Dymola/linux/ref_a.json", json.dumps(text))
    two = lib.commit("two")
    (test,) = diff_baselines(lib.top / "refs", one, two).tests
    base = test.baselines["Dymola/linux"]
    got = {(s.section, s.key): (s.old, s.new) for s in base.settings}
    assert got == {
        ("simulation", "method"): ("dassl", "Dassl"),
        ("comparison", "tolerance"): (0.01, 0.02),
        ("comparison", "variable_overrides.x"): (None, {"mode": "tube"}),
        ("sampling", "setting"): (300, 500),
        ("sampling", "precision"): ("single", "double"),
    }
    assert [(s.key, s.old, s.new) for s in base.samples] == [
        ("source_points", 800, 900)
    ]
    assert base.sampling_first is None


def test_a_sampling_change_alone_is_a_change(lib):
    _write(lib, "Dymola/linux/ref_a.json", _ref("Lib.A", sampling=_sampling(300)))
    one = lib.commit("one")
    _write(lib, "Dymola/linux/ref_a.json", _ref("Lib.A", sampling=_sampling(500)))
    two = lib.commit("two")
    (test,) = diff_baselines(lib.top / "refs", one, two).tests
    base = test.baselines["Dymola/linux"]
    assert [(s.section, s.key, s.old, s.new) for s in base.settings] == [
        ("sampling", "setting", 300, 500)
    ]


def test_sampling_recorded_for_the_first_time_is_old_data_not_a_change(lib):
    """A file written before ``sampling`` existed holds none; the next accept
    writes it. That is no setting that changed: a file that only gained it
    is unchanged, and a changed one says it was first recorded."""
    _write(lib, "Dymola/linux/ref_a.json", _ref("Lib.A"))
    _write(lib, "Dymola/linux/ref_b.json", _ref("Lib.B"))
    one = lib.commit("one")
    _write(lib, "Dymola/linux/ref_a.json", _ref("Lib.A", sampling=_sampling()))
    _write(
        lib,
        "Dymola/linux/ref_b.json",
        _ref("Lib.B", variables={"x": [0, 1, 3]}, sampling=_sampling(stored=3)),
    )
    two = lib.commit("two")
    diff = diff_baselines(lib.top / "refs", one, two)
    (test,) = diff.tests
    assert (test.model_id, diff.unchanged, diff.metadata_only) == ("Lib.B", 1, 1)
    base = test.baselines["Dymola/linux"]
    assert (base.settings, base.samples) == ([], [])
    assert base.sampling_first == Sampling(500, 900, 3, "double")


@pytest.mark.parametrize("date", ["last_updated", "date_added"])
def test_only_a_date_that_changed_is_no_change(lib, date):
    _write(lib, "Dymola/linux/ref_a.json", _ref("Lib.A", **{date: "2026-01-01"}))
    one = lib.commit("one")
    _write(lib, "Dymola/linux/ref_a.json", _ref("Lib.A", **{date: "2026-02-02"}))
    two = lib.commit("two")
    diff = diff_baselines(lib.top / "refs", one, two)
    assert (diff.tests, diff.unchanged, diff.metadata_only) == ([], 1, 1)


def test_the_same_nan_stored_on_both_sides_is_no_change(lib):
    """NaN != NaN: equal parts that hold one are still equal. (A single-
    precision file's values are read through float32, each NaN a float of
    its own, so list equality's identity shortcut does not apply.)"""
    single = _sampling(precision="single")
    variables = {"x": [0, math.nan, 2]}
    _write(
        lib,
        "Dymola/linux/ref_a.json",
        _ref("Lib.A", variables=variables, sampling=single, last_updated="1"),
    )
    one = lib.commit("one")
    _write(
        lib,
        "Dymola/linux/ref_a.json",
        _ref("Lib.A", variables=variables, sampling=single, last_updated="2"),
    )
    two = lib.commit("two")
    diff = diff_baselines(lib.top / "refs", one, two)
    assert (diff.tests, diff.unchanged, diff.metadata_only) == ([], 1, 1)


def test_every_time_row_is_machine_dependent_and_no_other():
    """The `timing` section, `cpu_time*`, `simulation.wall_time` and the
    listed name CPUtime measure the machine; a step count does not."""
    stats = {
        "simulation": {
            "accepted_steps": 10,
            "cpu_time": 1.5,
            "cpu_time_integration": 1.0,
            "wall_time": 2.0,
        },
        "timing": {"sim_wall": 3.0},
    }
    diagnostics = [DiagnosticSummary("CPUtime", 1.0, 0.0, 1.0)]
    rows = statistics_rows(
        stats,
        stats,
        listing=["CPUtime"],
        run_diagnostics=diagnostics,
        baseline_diagnostics=diagnostics,
    )
    assert {r.path for r in rows if machine_dependent(r)} == {
        "simulation.cpu_time",
        "simulation.cpu_time_integration",
        "simulation.wall_time",
        "timing.sim_wall",
        "CPUtime",
    }
    assert {r.path for r in rows if not machine_dependent(r)} == {
        "simulation.accepted_steps"
    }


def test_a_single_precision_newer_side_is_shown_at_its_own_digits(lib):
    """The newer side is a stored baseline: its CPUtime final was written
    through float32, and shown at 15 digits it reads 29.8774642944336."""
    cpu = float(np.float32(29.877464))
    _write(
        lib,
        "Dymola/linux/ref_a.json",
        _ref(
            "Lib.A",
            diagnostics=[{"name": "CPUtime", "final": 20.5, "min": 0, "max": 20.5}],
            sampling=_sampling(precision="double"),
        ),
    )
    one = lib.commit("one")
    _write(
        lib,
        "Dymola/linux/ref_a.json",
        _ref(
            "Lib.A",
            variables={"x": [0, 1, 3]},
            diagnostics=[{"name": "CPUtime", "final": cpu, "min": 0, "max": cpu}],
            sampling=_sampling(precision="single"),
        ),
    )
    two = lib.commit("two")
    (test,) = diff_baselines(lib.top / "refs", one, two).tests
    (row,) = [r for r in test.baselines["Dymola/linux"].effort if r.key == "CPUtime"]
    assert report_text(row.kind, row.run, row.digits[0]) == "29.87746 s"


def test_statistics_alone_make_a_change_and_list_the_solver_effort(lib):
    stats_one = {
        "simulation": {
            "accepted_steps": 200,
            "function_evaluations": 400,
            "state_names": ["a", "b"],
        },
        "timing": {"sim_wall": 10.0},
    }
    stats_two = {
        "simulation": {
            "accepted_steps": 150,
            "function_evaluations": 400,
            "state_names": ["a", "c"],
        },
        "timing": {"sim_wall": 7.0},
    }
    _write(lib, "Dymola/linux/ref_a.json", _ref("Lib.A", statistics=stats_one))
    one = lib.commit("one")
    _write(lib, "Dymola/linux/ref_a.json", _ref("Lib.A", statistics=stats_two))
    two = lib.commit("two")
    (test,) = diff_baselines(lib.top / "refs", one, two).tests
    base = test.baselines["Dymola/linux"]
    assert base.status == "changed"
    rows = {r.path: (r.baseline, r.run, r.changed, r.kind) for r in base.effort}
    assert rows == {
        "simulation.accepted_steps": (200, 150, True, "count"),
        "simulation.function_evaluations": (400, 400, False, "count"),
        "timing.sim_wall": (10.0, 7.0, True, "time"),
    }  # the text row (state_names) is dropped
    assert base.steps == (200, 150)


def test_a_new_backend_os_folder_holds_new_baselines(lib):
    _write(lib, "Dymola/linux/ref_a.json", _ref("Lib.A"))
    one = lib.commit("one")
    _write(lib, "OpenModelica/linux/ref_a.json", _ref("Lib.A"))
    two = lib.commit("two")
    diff = diff_baselines(lib.top / "refs", one, two)
    (test,) = diff.tests
    assert set(test.baselines) == {"OpenModelica/linux"}
    assert test.baselines["OpenModelica/linux"].status == "new"
    assert diff.folders == ["Dymola/linux", "OpenModelica/linux"]
    assert diff.unchanged == 1


def test_a_baseline_only_in_the_old_revision_is_removed(lib):
    _write(lib, "Dymola/linux/ref_a.json", _ref("Lib.A"))
    _write(lib, "Dymola/linux/ref_b.json", _ref("Lib.B"))
    one = lib.commit("one")
    (lib.top / "refs" / "Dymola" / "linux" / "ref_b.json").unlink()
    two = lib.commit("two")
    (test,) = diff_baselines(lib.top / "refs", one, two).tests
    assert (test.model_id, test.baselines["Dymola/linux"].status) == (
        "Lib.B",
        "removed",
    )


def test_folders_are_ordered_by_backend_then_os(lib):
    for folder in (
        "OpenModelica/linux",
        "Dymola/windows",
        "Dymola/linux",
        "FMPy/darwin",
    ):
        _write(lib, f"{folder}/ref_a.json", _ref("Lib.A"))
    one = lib.commit("one")
    diff = diff_baselines(lib.top / "refs", one, one)
    assert diff.folders == [
        "Dymola/linux",
        "Dymola/windows",
        "FMPy/darwin",
        "OpenModelica/linux",
    ]


def test_only_a_ref_file_directly_in_a_backend_os_folder_is_a_baseline(lib):
    _write(lib, "Dymola/linux/ref_a.json", _ref("Lib.A"))
    one = lib.commit("one")
    # a soft check, a companion, a stray file at the root: none is a primary
    _write(lib, "Dymola/linux/soft_checks/ref_a/om.json", _ref("Lib.A"))
    _write(lib, "Dymola/linux/companions/ref_a/ref_x.json", _ref("Lib.X"))
    _write(lib, "ref_stray.json", _ref("Lib.S"))
    _write(lib, "Dymola/linux/notes.json", "{}")
    two = lib.commit("two")
    diff = diff_baselines(lib.top / "refs", one, two)
    assert (diff.tests, diff.unchanged) == ([], 1)


def test_a_baseline_that_cannot_be_read_says_where_and_why_and_the_run_goes_on(lib):
    bad = json.loads(_ref("Lib.A"))
    bad["variables"][0].pop("name")
    _write(lib, "Dymola/linux/ref_0001.json", json.dumps(bad))
    _write(lib, "Dymola/linux/ref_b.json", _ref("Lib.B"))
    one = lib.commit("one")
    lib.git("mv", "refs/Dymola/linux/ref_0001.json", "refs/Dymola/linux/ref_a.json")
    _write(lib, "Dymola/linux/ref_a.json", _ref("Lib.A"))
    _write(lib, "Dymola/linux/ref_b.json", _ref("Lib.B", variables={"x": [0, 1, 3]}))
    two = lib.commit("two")
    diff = diff_baselines(lib.top / "refs", one, two)
    tests = _tests(diff)
    a = tests["Lib.A"].baselines["Dymola/linux"]
    short = diff.old.short
    assert a.status == "unreadable"
    assert a.note == f"cannot be read at {short} (variable 1 has no name)"
    assert tests["Lib.B"].baselines["Dymola/linux"].status == "changed"


def test_a_file_that_is_no_json_is_paired_by_its_path(lib):
    _write(lib, "Dymola/linux/ref_a.json", "<<<<<<< HEAD\n")
    one = lib.commit("one")
    _write(lib, "Dymola/linux/ref_a.json", _ref("Lib.A"))
    two = lib.commit("two")
    (test,) = diff_baselines(lib.top / "refs", one, two).tests
    (base,) = test.baselines.values()
    assert base.status == "unreadable"
    assert base.note.startswith("cannot be read at ")
    assert "Expecting value" in base.note


def test_one_model_id_in_two_files_compares_neither(lib):
    _write(
        lib, "Dymola/windows/ref_0011.json", _ref("Lib.P", variables={"x": [0, 1, 2]})
    )
    _write(
        lib, "Dymola/windows/ref_0326.json", _ref("Lib.P", variables={"x": [0, 1, 5]})
    )
    one = lib.commit("one")
    lib.git(
        "rm",
        "-q",
        "refs/Dymola/windows/ref_0011.json",
        "refs/Dymola/windows/ref_0326.json",
    )
    _write(
        lib, "Dymola/windows/ref_pppp.json", _ref("Lib.P", variables={"x": [0, 1, 2]})
    )
    two = lib.commit("two")
    diff = diff_baselines(lib.top / "refs", one, two)
    (test,) = diff.tests
    base = test.baselines["Dymola/windows"]
    assert base.status == "two baselines"
    assert (
        base.note == f"two baselines at {diff.old.short}: ref_0011.json, ref_0326.json"
    )
    assert base.variables == []


def test_a_model_id_in_other_than_ascii_is_paired(lib):
    model = "Lib.Wärme.Rohr_ü"
    _write(lib, "Dymola/linux/ref_0001.json", _ref(model))
    one = lib.commit("one")
    lib.git("mv", "refs/Dymola/linux/ref_0001.json", "refs/Dymola/linux/ref_c0de.json")
    _write(lib, "Dymola/linux/ref_c0de.json", _ref(model, variables={"x": [0, 1, 2.5]}))
    two = lib.commit("two")
    (test,) = diff_baselines(lib.top / "refs", one, two).tests
    assert test.model_id == model
    assert test.baselines["Dymola/linux"].status == "changed"


def test_nan_and_inf_in_a_stored_baseline_are_read_and_reported(lib):
    _write(lib, "Dymola/linux/ref_a.json", _ref("Lib.A", variables={"x": [0, 1, 2]}))
    one = lib.commit("one")
    _write(
        lib,
        "Dymola/linux/ref_a.json",
        _ref("Lib.A", variables={"x": [0, math.nan, math.inf]}),
    )
    two = lib.commit("two")
    (test,) = diff_baselines(lib.top / "refs", one, two).tests
    (x,) = test.baselines["Dymola/linux"].variables
    assert (x.change.non_finite, x.change.non_finite_at) == (2, 1.0)


def test_grids_of_different_length_and_span_are_compared_inside_new_s(lib):
    _write(
        lib,
        "Dymola/linux/ref_a.json",
        _ref("Lib.A", time=[0, 1, 2, 3, 4], variables={"x": [0, 1, 2, 3, 4]}),
    )
    one = lib.commit("one")
    _write(
        lib,
        "Dymola/linux/ref_a.json",
        _ref("Lib.A", time=[0, 2], variables={"x": [0, 2.4]}),
    )
    two = lib.commit("two")
    (test,) = diff_baselines(lib.top / "refs", one, two).tests
    (x,) = test.baselines["Dymola/linux"].variables
    assert (x.change.compared, x.change.outside) == (3, 2)
    assert (x.change.largest, x.change.at) == (pytest.approx(10.0), 2.0)


def test_a_single_precision_file_is_read_through_float32(lib):
    """Values as the codec decodes them: 0.1 in a single-precision file is
    float32's 0.1, and the change from double 0.1 is float32's error."""
    single = {
        "setting": "all",
        "source_points": 3,
        "stored_points": 3,
        "precision": "single",
    }
    _write(lib, "Dymola/linux/ref_a.json", _ref("Lib.A", variables={"x": [0, 0.1, 1]}))
    one = lib.commit("one")
    _write(
        lib,
        "Dymola/linux/ref_a.json",
        _ref("Lib.A", variables={"x": [0, 0.1, 1]}, sampling=single),
    )
    two = lib.commit("two")
    (test,) = diff_baselines(lib.top / "refs", one, two).tests
    (x,) = test.baselines["Dymola/linux"].variables
    assert x.change.largest == pytest.approx(abs(float(np.float32(0.1)) - 0.1) * 100)


def test_against_scores_every_other_folder_s_new_baseline_against_one(lib):
    _write(lib, "Dymola/linux/ref_a.json", _ref("Lib.A", variables={"x": [0, 5, 10]}))
    _write(lib, "Dymola/windows/ref_a.json", _ref("Lib.A", variables={"x": [0, 5, 10]}))
    one = lib.commit("one")
    _write(lib, "Dymola/linux/ref_a.json", _ref("Lib.A", variables={"x": [0, 6, 10]}))
    _write(
        lib,
        "OpenModelica/linux/ref_a.json",
        _ref("Lib.A", variables={"x": [0, 7, 10], "om_only": [1, 2, 3]}),
    )
    two = lib.commit("two")
    diff = diff_baselines(lib.top / "refs", one, two, against="Dymola/linux")
    (test,) = diff.tests
    assert sorted(test.against) == ["Dymola/windows", "OpenModelica/linux"]
    (win,) = test.against["Dymola/windows"]
    assert (win.change.largest, win.change.at) == (pytest.approx(10.0), 1.0)
    om = {v.name: v for v in test.against["OpenModelica/linux"]}
    assert om["x"].change.largest == pytest.approx(10.0)
    assert om["om_only"].status == "new"


def test_without_against_there_is_no_such_comparison(lib):
    _write(lib, "Dymola/linux/ref_a.json", _ref("Lib.A"))
    one = lib.commit("one")
    _write(lib, "OpenModelica/linux/ref_a.json", _ref("Lib.A"))
    two = lib.commit("two")
    (test,) = diff_baselines(lib.top / "refs", one, two).tests
    assert test.against == {}


def test_against_a_folder_neither_revision_holds_is_refused(lib):
    _write(lib, "Dymola/linux/ref_a.json", _ref("Lib.A"))
    one = lib.commit("one")
    with pytest.raises(DiffError, match="no baselines in Julia/linux"):
        diff_baselines(lib.top / "refs", one, one, against="Julia/linux")


# --- the files on disk as the newer side --------------------------------------


def _tree_hash(git_dir):
    h = hashlib.sha256()
    for path in sorted(p for p in git_dir.rglob("*") if p.is_file()):
        h.update(str(path.relative_to(git_dir)).encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def test_the_files_on_disk_are_the_newer_side_and_git_is_not_written(lib):
    """What an accept leaves before `git add`: a changed file and an
    untracked new one. Another file is touched (stat-dirty, same bytes),
    so a command that refreshed the index would write it."""
    _write(lib, "Dymola/linux/ref_a.json", _ref("Lib.A"))
    _write(lib, "Dymola/linux/ref_b.json", _ref("Lib.B"))
    one = lib.commit("one")
    _write(lib, "Dymola/linux/ref_a.json", _ref("Lib.A", variables={"x": [0, 1, 3]}))
    _write(lib, "OpenModelica/linux/ref_a.json", _ref("Lib.A"))
    b = lib.top / "refs" / "Dymola" / "linux" / "ref_b.json"
    st = b.stat()
    os.utime(b, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    before = _tree_hash(lib.top / ".git")
    diff = diff_baselines(lib.top / "refs", one, None)
    assert _tree_hash(lib.top / ".git") == before
    assert diff.new is None
    (test,) = diff.tests
    assert {f: b.status for f, b in test.baselines.items()} == {
        "Dymola/linux": "changed",
        "OpenModelica/linux": "new",
    }
    assert diff.unchanged == 1


def test_files_on_disk_that_differ_only_in_line_endings_are_no_change(lib):
    _write(lib, "Dymola/linux/ref_a.json", _ref("Lib.A"))
    one = lib.commit("one")
    path = lib.top / "refs" / "Dymola" / "linux" / "ref_a.json"
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    diff = diff_baselines(lib.top / "refs", one, None)
    assert (diff.tests, diff.unchanged, diff.metadata_only) == ([], 1, 1)


def test_the_revisions_are_named_by_short_hash_and_subject(lib):
    _write(lib, "Dymola/linux/ref_a.json", _ref("Lib.A"))
    one = lib.commit("first baselines")
    diff = diff_baselines(lib.top / "refs", one, one)
    assert diff.old.subject == "first baselines"
    assert one.startswith(diff.old.short)
    assert diff.new is not None and diff.new.sha == one


def test_a_test_says_where_its_baseline_is_unchanged(lib):
    _write(lib, "Dymola/linux/ref_a.json", _ref("Lib.A"))
    _write(lib, "Dymola/windows/ref_a.json", _ref("Lib.A"))
    one = lib.commit("one")
    _write(lib, "Dymola/linux/ref_a.json", _ref("Lib.A", variables={"x": [0, 1, 3]}))
    two = lib.commit("two")
    (test,) = diff_baselines(lib.top / "refs", one, two).tests
    assert (sorted(test.baselines), test.unchanged) == (
        ["Dymola/linux"],
        ["Dymola/windows"],
    )


def test_an_empty_time_in_a_stored_baseline_is_said_not_raised(lib):
    _write(lib, "Dymola/linux/ref_a.json", _ref("Lib.A", time=[], variables={"x": []}))
    one = lib.commit("one")
    _write(lib, "Dymola/linux/ref_a.json", _ref("Lib.A"))
    two = lib.commit("two")
    (test,) = diff_baselines(lib.top / "refs", one, two).tests
    (x,) = test.baselines["Dymola/linux"].variables
    assert (x.change.note, x.change.largest) == ("no time in the old baseline", None)
