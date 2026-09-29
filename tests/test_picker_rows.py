"""The add form's variable picker as data (the plan's Task 3, K2): three
sections, filtered on the label OR the name, capped."""

from __future__ import annotations

import pytest

from dstf.authoring.needle import matcher
from dstf.authoring.picker_rows import CAP, SECTIONS, picker_rows


def _all(_text: str) -> bool:
    return True


def test_three_sections_in_order_and_each_name_once():
    """``diagnostics`` are what the run RECORDED: each is listed there, and
    only there — a Dymola file names `CPUtime` as a variable too, and a
    recorded one the listing lacks is still one a criterion may score."""
    got = picker_rows(
        on_plot=["x", "z"],
        names=["x", "y", "z", "w", "CPUtime"],
        diagnostics=["CPUtime", "EventCounter"],
        labels={"x": "a.Q"},
        keep=_all,
    )
    assert [title for title, _ in got.sections] == list(SECTIONS)
    assert [[r.name for r in rows] for _, rows in got.sections] == [
        ["x", "z"],
        ["y", "w"],
        ["CPUtime", "EventCounter"],
    ]
    (x, _z), _, _ = (rows for _, rows in got.sections)
    assert (x.text, x.name) == ("a.Q", "x")


def test_an_empty_section_is_absent():
    got = picker_rows(on_plot=[], names=["y"], diagnostics=[], labels={}, keep=_all)
    assert [title for title, _ in got.sections] == ["Other variables"]


def test_the_filter_reads_the_label_and_the_name():
    labels = {"unitTests.x[1]": "pipe.T[3]"}
    keep = matcher("pipe.T*", regex=False, case=False)
    got = picker_rows(
        on_plot=[],
        names=["unitTests.x[1]", "q"],
        diagnostics=[],
        labels=labels,
        keep=keep,
    )
    assert [r.name for _, rows in got.sections for r in rows] == ["unitTests.x[1]"]
    keep = matcher("unitTests", regex=False, case=True)
    got = picker_rows(
        on_plot=[],
        names=["unitTests.x[1]", "q"],
        diagnostics=[],
        labels=labels,
        keep=keep,
    )
    assert [r.name for _, rows in got.sections for r in rows] == ["unitTests.x[1]"]


def test_at_most_cap_rows_and_the_cap_line_counts_all():
    names = [f"v{i}" for i in range(77_000)]
    got = picker_rows(on_plot=["v0"], names=names, diagnostics=[], labels={}, keep=_all)
    assert got.shown == CAP == 200
    assert got.total == 77_000
    assert sum(len(rows) for _, rows in got.sections) == CAP
    assert got.capped == "Showing 200 of 77,000 — narrow the search"


def test_under_the_cap_there_is_no_cap_line():
    got = picker_rows(on_plot=[], names=["a"], diagnostics=[], labels={}, keep=_all)
    assert got.capped is None


def test_a_regex_that_runs_out_raises_for_the_caller_to_say():
    def slow(_text: str) -> bool:
        raise TimeoutError("the pattern took too long")

    with pytest.raises(TimeoutError):
        picker_rows(on_plot=[], names=["a"], diagnostics=[], labels={}, keep=slow)
