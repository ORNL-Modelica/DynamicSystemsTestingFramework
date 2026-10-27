"""The plot extras as the tool edits them: pure functions over the `plots`
key's entries — context series and watch plots — and the ONE op on
`/plots` a gesture stages."""

from __future__ import annotations

import pytest

from dstf.authoring.layout import (
    add_context,
    layout_op,
    new_watch,
    remove_context,
    remove_watch,
    reset_op,
)
from dstf.authoring.spec_document import PatchOp
from dstf.discovery.plots import PlotExtra, ResolvedPlot, parse_plots


def _plot(*subject, context=(), watch=False):
    return ResolvedPlot(index=0, subject=subject, context=context, watch=watch)


X = _plot("x")
FAMILY = _plot("p[1]", "p[2]")


def test_add_context_appends_what_the_entry_lacks_once_and_skips_the_subject():
    extras = (PlotExtra("x", ("z",)),)
    assert add_context(extras, X, ["x", "z", "w", "w"]) == (PlotExtra("x", ("z", "w")),)


def test_add_context_on_a_family_finds_the_entry_on_any_member():
    extras = (PlotExtra("a"), PlotExtra("p[2]", ("z",)))
    assert add_context(extras, FAMILY, ["w", "p[1]"]) == (
        PlotExtra("a"),
        PlotExtra("p[2]", ("z", "w")),
    )
    # none yet: the entry goes on the subject's first member
    assert add_context((), FAMILY, ["w"]) == (PlotExtra("p[1]", ("w",)),)


def test_add_context_keeps_the_entrys_place():
    extras = (PlotExtra("c"), PlotExtra("x", ("z",)), PlotExtra("d"))
    assert add_context(extras, X, ["w"]) == (
        PlotExtra("c"),
        PlotExtra("x", ("z", "w")),
        PlotExtra("d"),
    )


def test_removing_a_derived_plots_last_context_drops_its_entry():
    extras = (PlotExtra("x", ("z", "w")), PlotExtra("c"))
    assert remove_context(extras, X, "z") == (PlotExtra("x", ("w",)), PlotExtra("c"))
    # a name it does not have changes nothing
    assert remove_context(extras, X, "q") == extras
    assert remove_context((PlotExtra("x", ("z",)),), X, "z") == ()


def test_a_watch_plot_keeps_its_entry_with_no_context():
    """A watch plot's entry IS the plot."""
    watch = _plot("c", context=("d",), watch=True)
    assert remove_context((PlotExtra("c", ("d",)),), watch, "d") == (PlotExtra("c"),)


def test_new_watch_appends_and_refuses_a_second_entry():
    assert new_watch((PlotExtra("x", ("z",)),), "c") == (
        PlotExtra("x", ("z",)),
        PlotExtra("c"),
    )
    with pytest.raises(ValueError):
        new_watch((PlotExtra("c"),), "c")


def test_remove_watch_takes_its_entry():
    extras = (PlotExtra("x", ("z",)), PlotExtra("c", ("d",)), PlotExtra("e"))
    assert remove_watch(extras, "c") == (PlotExtra("x", ("z",)), PlotExtra("e"))
    with pytest.raises(KeyError):
        remove_watch(extras, "nope")


def test_the_op_is_add_without_a_plots_key_and_replace_with_one():
    extras = (PlotExtra("x", ("z",)),)
    raw = [{"var": "x", "context": ["z"]}]
    assert layout_op("M", {"model": "M"}, extras) == PatchOp("M", "add", "/plots", raw)
    assert layout_op("M", None, extras) == PatchOp("M", "add", "/plots", raw)
    assert layout_op("M", {"model": "M", "plots": []}, extras) == PatchOp(
        "M", "replace", "/plots", raw
    )
    assert parse_plots(raw) == extras


def test_removing_the_last_extra_removes_the_key():
    """Not `plots: []`: the key goes, so Reset layout hides again."""
    entry = {"model": "M", "plots": [{"var": "x", "context": ["z"]}]}
    assert layout_op("M", entry, ()) == PatchOp("M", "remove", "/plots")
    assert reset_op("M") == PatchOp("M", "remove", "/plots")


def test_removing_a_context_name_reaches_every_entry_on_the_family():
    """p[2] was a watch plot with context w, then got a criterion (Q14):
    the family's plot draws z and w from two entries, and the × on w takes
    it off — the entry left with nothing on a derived plot goes."""
    extras = (PlotExtra("p[1]", ("z",)), PlotExtra("p[2]", ("w", "z")))
    family = _plot("p[1]", "p[2]", context=("z", "w"))
    assert remove_context(extras, family, "w") == (
        PlotExtra("p[1]", ("z",)),
        PlotExtra("p[2]", ("z",)),
    )
    assert remove_context(extras, family, "z") == (PlotExtra("p[2]", ("w",)),)


def test_no_extras_on_an_entry_with_no_key_is_no_op():
    """Nothing to remove: an entry with no ``plots`` key — or no entry —
    and no extras stage nothing (a `remove` of a member that is not there
    is refused by the patch)."""
    assert layout_op("M", {"model": "M"}, ()) is None
    assert layout_op("M", None, ()) is None
