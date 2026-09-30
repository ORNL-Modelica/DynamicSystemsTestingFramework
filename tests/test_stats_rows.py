"""The Statistics rows both tiers render: every statistic the run and its
baseline recorded, side by side; a row is changed when the criterion its
`+` would add would fail."""

from __future__ import annotations

import copy

import numpy as np

from dstf.backends.types import VariableResult
from dstf.reporting.context_builder import _build_statistics_sections
from dstf.reporting.stats_rows import changed, statistics_rows

RUN = {
    "translation": {
        "nonlinear": [1, 1, 1],
        "nonlinear_count": 3,
        "nonlinear_total": 3,
        "nonlinear_max": 1,
        "continuous_time_states": 4,
        "state_names": ["a", "b"],
    },
    "simulation": {"cpu_time_integration": 0.5, "state_events": 2},
    "timing": {"total_wall": 4.02, "translation_wall": 1.0},
    "CPUtime": 1.12,
    "EventCounter": 7.0,
}
BASE = {
    "translation": {
        "nonlinear": [],
        "continuous_time_states": 4,
        "state_names": ["a", "c"],
    },
    "simulation": {"cpu_time_integration": 0.5, "state_events": 2},
    "timing": {"total_wall": 3.91, "translation_wall": 1.0},
    "CPUtime": 0.80,
    "EventCounter": 7.0,
}


def _by_path(rows):
    return {r.path: r for r in rows}


def test_a_count_that_moved_is_changed_and_an_equal_one_is_not():
    rows = _by_path(statistics_rows(RUN, BASE))
    # The baseline's empty list, stored before the parser wrote its count.
    assert rows["translation.nonlinear_count"].baseline == 0
    assert rows["translation.nonlinear_count"].changed
    assert rows["translation.nonlinear_count"].delta == "0→3"
    assert rows["translation.nonlinear_count"].kind == "count"
    assert not rows["translation.continuous_time_states"].changed
    assert rows["translation.continuous_time_states"].delta == ""
    assert not rows["simulation.state_events"].changed


def test_a_time_is_changed_beyond_the_seeded_tolerance_only():
    rows = _by_path(statistics_rows(RUN, BASE))
    assert rows["CPUtime"].changed
    assert rows["CPUtime"].delta == "+40%"
    assert rows["CPUtime"].section == "simulation"
    assert rows["CPUtime"].kind == "time"
    assert rows["CPUtime"].label == "CPUtime"
    # 3% off: within the 10% the `+` seeds — unchanged, its delta still said.
    assert not rows["timing.total_wall"].changed
    assert rows["timing.total_wall"].delta == "+3%"
    assert rows["EventCounter"].kind == "count"


def test_a_time_under_one_percent_off_reads_one_decimal():
    """Never `+0%` for a time that moved: under 1% the delta keeps one
    decimal; an equal time still says nothing."""
    rows = _by_path(
        statistics_rows(
            {"timing": {"total_wall": 3.92, "sim_wall": 3.90, "other_wall": 1.0}},
            {"timing": {"total_wall": 3.91, "sim_wall": 3.91, "other_wall": 1.0}},
        )
    )
    assert rows["timing.total_wall"].delta == "+0.3%"
    assert rows["timing.sim_wall"].delta == "-0.3%"
    assert rows["timing.other_wall"].delta == ""


def test_a_time_over_a_zero_baseline_reads_in_seconds():
    rows = _by_path(
        statistics_rows({"timing": {"sim_wall": 0.12}}, {"timing": {"sim_wall": 0}})
    )
    assert rows["timing.sim_wall"].delta == "+0.12 s"


def test_a_value_only_on_one_side_is_never_changed():
    rows = _by_path(statistics_rows({"simulation": {"state_events": 2}}, {}))
    assert not rows["simulation.state_events"].changed
    assert rows["simulation.state_events"].delta == ""
    assert rows["simulation.state_events"].baseline is None


def test_text_rows_are_present_and_changed_by_the_report_rule():
    rows = _by_path(statistics_rows(RUN, BASE))
    assert rows["translation.state_names"].kind == "text"
    assert rows["translation.state_names"].changed
    assert rows["translation.nonlinear"].kind == "text"
    # "" (an empty list) against a list: the report never tinted one side empty
    assert not rows["translation.nonlinear"].changed


def test_labels():
    rows = _by_path(statistics_rows(RUN, BASE))
    assert rows["translation.nonlinear_count"].label == "Nonlinear count"
    assert rows["simulation.cpu_time_integration"].label == "CPU time integration"
    assert rows["EventCounter"].label == "EventCounter"


def test_the_report_order_keys_alphabetical_top_level_folded_into_simulation():
    """The report's row order and section placement (Corrections A2): keys
    alphabetical inside a section, `timing` its fixed four first, top-level
    scalars sorted in among the Simulation keys."""
    rows = statistics_rows(RUN, BASE)
    assert list(dict.fromkeys(r.section for r in rows)) == [
        "translation",
        "simulation",
        "timing",
    ]
    assert [r.key for r in rows if r.section == "translation"] == [
        "continuous_time_states",
        "nonlinear",
        "nonlinear_count",
        "nonlinear_max",
        "nonlinear_total",
        "state_names",
    ]
    assert [r.path for r in rows if r.section == "simulation"] == [
        "CPUtime",
        "EventCounter",
        "simulation.cpu_time_integration",
        "simulation.state_events",
    ]
    run = {"timing": {"b": 1.0, "total_wall": 2.0, "a": 1.0, "translation_wall": 1.0}}
    assert [r.key for r in statistics_rows(run, None)] == [
        "translation_wall",
        "total_wall",
        "a",
        "b",
    ]


def test_other_sections_follow_and_top_level_scalars_alone_come_last():
    """Other sections after the three, in the order they appear (baseline
    first, as the report did); with no `simulation` dict on either side,
    the top-level scalars are a Simulation section at the END."""
    run = {"zeta": {"k": 1}, "timing": {"total_wall": 1.0}, "CPUtime": 1.0}
    base = {"alpha": {"k": 1}, "CPUtime": 1.0, "EventCounter": 2.0}
    rows = statistics_rows(run, base)
    assert [(r.section, r.path) for r in rows] == [
        ("timing", "timing.total_wall"),
        ("alpha", "alpha.k"),
        ("zeta", "zeta.k"),
        ("simulation", "CPUtime"),
        ("simulation", "EventCounter"),
    ]


def test_openmodelica_and_fakelib_shapes():
    om = {"timing": {"frontend": 0.1, "total": 2.0, "total_wall": 2.1}, "CPUtime": 1.0}
    rows = statistics_rows(om, om)
    assert {r.path for r in rows} == {
        "timing.frontend",
        "timing.total",
        "timing.total_wall",
        "CPUtime",
    }
    assert all(r.kind == "time" and not r.changed for r in rows)
    fake = {"simulation": {"wall_time": 0.01}, "timing": {"sim_wall": 0.01}}
    rows = _by_path(statistics_rows(fake, None))
    assert rows["simulation.wall_time"].kind == "time"
    assert rows["simulation.wall_time"].baseline is None


def test_a_top_level_statistic_naming_a_diagnostic_takes_its_kind():
    """A user-configured diagnostic folded into the statistics (`Wall`):
    its fractional samples make its row a time, as the add form's
    `Wall.final` leaf is (Corrections A5)."""
    t = np.array([0.0, 1.0, 2.0])
    wall = VariableResult(
        index=0, time=t, values=np.array([0.0, 1.5, 2.5]), name="Wall"
    )
    rows = _by_path(
        statistics_rows(
            {"Wall": 2.5},
            {"Wall": 2.4},
            run_diagnostics=[wall],
            baseline_diagnostics=(),
        )
    )
    assert rows["Wall"].kind == "time"
    assert not rows["Wall"].changed  # 4% off: within the time's 10%
    # The report reads the same run's samples: its row is not tinted either.
    (section,) = _build_statistics_sections(
        {"Wall": 2.4}, {"Wall": 2.5}, run_diagnostics=[wall]
    )
    assert [(r["label"], r["changed"]) for r in section["rows"]] == [("Wall", False)]


def test_extra_paths_become_other_scored_rows():
    rows = statistics_rows(
        RUN, BASE, extra_paths=["translation.typo", "CPUtime.final", "CPUtime"]
    )
    other = [r for r in rows if r.section == "other scored"]
    assert [r.path for r in other] == ["translation.typo"]
    assert rows[-1] is other[0]
    assert other[0].run is None and other[0].baseline is None
    assert not other[0].changed


def test_only_a_top_level_row_holds_a_final_leaf():
    """`X.final` is the row `X` only for a top-level `X` (S2): a leaf
    reading `timing.total_wall.final` is no leaf of the `total_wall` row,
    so it gets its own "other scored" row (S4)."""
    rows = statistics_rows(
        {"timing": {"total_wall": 1.0}},
        {"timing": {"total_wall": 1.0}},
        extra_paths=["timing.total_wall.final"],
    )
    assert [(r.section, r.path) for r in rows] == [
        ("timing", "timing.total_wall"),
        ("other scored", "timing.total_wall.final"),
    ]


def test_a_leaf_reading_a_list_statistic_gets_its_own_row():
    """A text row holds no leaf (the card never shows one, S1): a leaf
    reading `translation.nonlinear` gets an "other scored" row (S4)."""
    stats = {"translation": {"nonlinear": [1]}}
    rows = statistics_rows(stats, stats, extra_paths=["translation.nonlinear"])
    assert [(r.section, r.path, r.kind) for r in rows] == [
        ("translation", "translation.nonlinear", "text"),
        ("other scored", "translation.nonlinear", "count"),
    ]


def test_an_extra_diagnostic_path_no_row_holds_reads_the_diagnostics():
    """A backend that does not fold its diagnostics: a `CPUtime.final` leaf
    gets its own row, its values the diagnostics' finals."""
    t = np.array([0.0, 1.0])
    cpu = VariableResult(index=0, time=t, values=np.array([0.0, 1.12]), name="CPUtime")
    rows = statistics_rows(
        {},
        {},
        run_diagnostics=[cpu],
        baseline_diagnostics=[{"name": "CPUtime", "final": 0.8}],
        extra_paths=["CPUtime.final"],
    )
    (row,) = rows
    assert (row.section, row.run, row.baseline) == ("other scored", 1.12, 0.8)
    assert row.kind == "time" and row.changed


def test_the_inputs_are_never_written():
    run, base = copy.deepcopy(RUN), copy.deepcopy(BASE)
    statistics_rows(run, base, extra_paths=["x.y"])
    assert run == RUN and base == BASE


def test_changed_reads_the_seeds_tolerance():
    assert changed("CPUtime", "time", 1.15, 1.0)
    assert not changed("CPUtime", "time", 1.05, 1.0)
    assert changed("translation.nonlinear_count", "count", 3, 0)
    assert not changed("translation.nonlinear_count", "count", 3, 3)
    assert not changed("CPUtime", "time", None, 1.0)
    assert not changed("CPUtime", "time", 1.0, None)


def test_a_value_reads_the_same_on_both_tiers():
    """A time to 3 significant figures KEEPING its zeros (`0.800 s`, never
    `0.8 s`) and no bare trailing point; a count whole, however large."""
    from dstf.reporting.stats_rows import value_text

    assert value_text("time", 0.8) == "0.800 s"
    assert value_text("time", 2.1) == "2.10 s"
    assert value_text("time", 1.2345) == "1.23 s"
    assert value_text("time", 100.0) == "100 s"
    assert value_text("count", 1234567) == "1234567"
    assert value_text("count", 102.0) == "102"
    assert value_text("count", None) == ""
    assert value_text("text", ["a", "b"]) == "a, b"


def test_a_time_under_a_twentieth_of_a_percent_off_reads_under_a_tenth():
    """`+0.0%` said nothing moved while the delta said it had."""
    rows = _by_path(
        statistics_rows(
            {"timing": {"total_wall": 100.04, "sim_wall": 99.96}},
            {"timing": {"total_wall": 100.0, "sim_wall": 100.0}},
        )
    )
    assert rows["timing.total_wall"].delta == "+<0.1%"
    assert rows["timing.sim_wall"].delta == "-<0.1%"
