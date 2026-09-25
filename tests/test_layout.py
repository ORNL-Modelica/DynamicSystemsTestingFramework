"""The plot layout as the tool edits it (3c): pure functions over the
`plots` key's PlotSpecs, and the ONE op on `/plots` a gesture stages."""

from __future__ import annotations

import pytest

from dstf.authoring.layout import (
    add_series,
    current_layout,
    layout_op,
    new_plot,
    plots_showing,
    remove_plot,
    remove_series,
    reset_op,
    stranded,
    stranded_sentence,
)
from dstf.authoring.spec_document import PatchOp
from dstf.discovery.plots import PlotSpec, SeriesSpec, parse_plots


def _l(*plots):
    return tuple(PlotSpec(series=tuple(SeriesSpec(v) for v in p)) for p in plots)


def test_no_plots_key_is_one_plot_per_tracked_variable():
    assert current_layout(None, ["x", "y"]) == _l(["x"], ["y"])
    assert current_layout(_l(["x", "y"]), ["x", "y"]) == _l(["x", "y"])
    assert current_layout((), ["x"]) == ()


def test_add_series_appends_what_the_plot_lacks_in_order():
    assert add_series(_l(["x"], ["y"]), 0, ["z", "x", "z"]) == _l(["x", "z"], ["y"])


def test_removing_a_plots_last_series_removes_the_plot():
    assert remove_series(_l(["x", "z"], ["y"]), 0, ["z"]) == _l(["x"], ["y"])
    assert remove_series(_l(["x"], ["y"]), 0, ["x"]) == _l(["y"])


def test_a_new_plot_goes_below_the_selected_one_and_says_where():
    layout, at = new_plot(_l(["x"], ["y"]), 0, ["z"])
    assert (layout, at) == (_l(["x"], ["z"], ["y"]), 1)
    assert new_plot((), None, ["z"]) == (_l(["z"]), 0)


def test_remove_plot():
    assert remove_plot(_l(["x"], ["y"]), 0) == _l(["y"])


@pytest.mark.parametrize(
    "edit",
    [
        lambda layout: remove_plot(layout, -1),
        lambda layout: remove_plot(layout, len(layout)),
        lambda layout: add_series(layout, -1, ["z"]),
        lambda layout: add_series(layout, len(layout), ["z"]),
        lambda layout: new_plot(layout, -1, ["z"]),
        lambda layout: new_plot(layout, -2, ["z"]),
        lambda layout: new_plot(layout, len(layout), ["z"]),
    ],
    ids=[
        "remove_plot-1",
        "remove_plot-len",
        "add_series-1",
        "add_series-len",
        "new_plot-1",
        "new_plot-2",
        "new_plot-len",
    ],
)
def test_an_index_outside_the_layout_is_refused(edit):
    """A negative index would count from the end and one past it would
    append or do nothing: a gesture names a plot the page draws, or none."""
    with pytest.raises(IndexError):
        edit(_l(["x"], ["y"]))


def test_stranded_names_the_scored_variables_a_change_takes_off_every_plot():
    before = _l(["x"], ["x", "y"], ["z"])
    assert stranded(before, remove_plot(before, 0), ["x", "y"]) == []
    assert stranded(before, remove_plot(before, 1), ["x", "y"]) == ["y"]
    # a scored variable no plot showed before is not this change's doing
    assert stranded(_l(["x"]), (), ["x", "q"]) == ["x"]
    assert plots_showing(before, "x") == (0, 1)


def test_the_stranded_sentence_in_the_singular_and_the_plural():
    """One sentence for Remove plot's tooltip and the drawer's grey row."""
    assert stranded_sentence(["x"], 1) == (
        "x is scored by 1 criterion and this is the only plot that shows it. "
        "Add it to another plot first, or remove its criteria."
    )
    assert stranded_sentence(["x"], 2).startswith("x is scored by 2 criteria and")
    assert stranded_sentence(["x", "y"], 2) == (
        "x, y are scored by 2 criteria and this is the only plot that shows "
        "them. Add them to another plot first, or remove their criteria."
    )


def test_the_op_is_add_without_a_plots_key_and_replace_with_one():
    layout = _l(["x", "z"])
    raw = [{"kind": "time", "series": [{"var": "x"}, {"var": "z"}]}]
    assert layout_op("M", {"model": "M"}, layout) == PatchOp("M", "add", "/plots", raw)
    assert layout_op("M", None, layout) == PatchOp("M", "add", "/plots", raw)
    assert layout_op("M", {"model": "M", "plots": []}, layout) == PatchOp(
        "M", "replace", "/plots", raw
    )
    assert parse_plots(raw) == layout
    assert reset_op("M") == PatchOp("M", "remove", "/plots")
