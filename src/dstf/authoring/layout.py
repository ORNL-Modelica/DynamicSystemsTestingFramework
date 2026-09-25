"""A test's plot layout as the tool edits it (the roadmap's 3c; D134): the
`plots` key's list, changed one gesture at a time and staged as ONE op on
`/plots` — `Session.stage` collapses a run of them (Convention 8). UI-free:
the Variables drawer, a plot card's toolbar and the tree card's Reset
layout all call in, so the rules below hold on every surface at once."""

from __future__ import annotations

from collections.abc import Sequence

from ..discovery.plots import PlotSpec, SeriesSpec, default_plots, plots_to_raw
from .spec_document import PatchOp

Layout = tuple[PlotSpec, ...]


def current_layout(plots: Sequence[PlotSpec] | None, tracked: Sequence[str]) -> Layout:
    """The layout a gesture edits: the test's ``plots`` key, else one plot
    per tracked variable — the default both tiers draw. The first gesture on
    a test without the key writes that default out with its change."""
    return tuple(plots) if plots is not None else default_plots(tracked)


def _with(layout: Layout, index: int, plot: PlotSpec) -> Layout:
    if not 0 <= index < len(layout):
        raise IndexError(index)
    return layout[:index] + (plot,) + layout[index + 1 :]


def add_series(layout: Layout, index: int, names: Sequence[str]) -> Layout:
    """Plot ``index`` with ``names`` appended, in order, minus those it has."""
    plot = layout[index]
    have = {s.var for s in plot.series}
    added = tuple(SeriesSpec(n) for n in dict.fromkeys(names) if n not in have)
    return _with(layout, index, PlotSpec(series=plot.series + added, kind=plot.kind))


def remove_series(layout: Layout, index: int, names: Sequence[str]) -> Layout:
    """Plot ``index`` without ``names``; a plot left with no series goes —
    a plot is at least one series (`parse_plots` refuses an empty one)."""
    drop = set(names)
    plot = layout[index]
    kept = tuple(s for s in plot.series if s.var not in drop)
    if not kept:
        return remove_plot(layout, index)
    return _with(layout, index, PlotSpec(series=kept, kind=plot.kind))


def new_plot(
    layout: Layout, after: int | None, names: Sequence[str]
) -> tuple[Layout, int]:
    """A plot of ``names`` below plot ``after`` (at the end when None), and
    its index — the page selects it, so the next checkbox lands on it.
    An ``after`` outside the layout is refused, as `remove_plot` refuses
    one: -1 would land the plot at the top and -2 count from the end, and
    one at or past the end would append and return an index nothing has."""
    if after is not None and not 0 <= after < len(layout):
        raise IndexError(after)
    at = len(layout) if after is None else after + 1
    plot = PlotSpec(series=tuple(SeriesSpec(n) for n in dict.fromkeys(names)))
    return layout[:at] + (plot,) + layout[at:], at


def remove_plot(layout: Layout, index: int) -> Layout:
    """Plot ``index`` gone. An index outside the layout is refused, as
    `Session.plot_figure` refuses it: a slice would count a negative one
    from the end, or quietly change nothing."""
    if not 0 <= index < len(layout):
        raise IndexError(index)
    return layout[:index] + layout[index + 1 :]


def plots_showing(layout: Layout, name: str) -> tuple[int, ...]:
    return tuple(
        i for i, plot in enumerate(layout) if any(s.var == name for s in plot.series)
    )


def stranded(before: Layout, after: Layout, scored: Sequence[str]) -> list[str]:
    """The scored variables ``before`` shows on some plot and ``after`` on
    none (the owner's ruling, 2026-09-25): a criterion's row sits under the
    plot that shows its variable, and with no such plot the row has nowhere
    to go. The tool refuses such a change and says which variables."""
    return [
        name
        for name in scored
        if plots_showing(before, name) and not plots_showing(after, name)
    ]


def stranded_sentence(texts: Sequence[str], criteria: int) -> str:
    """Why a change is refused: ``texts`` are what the stranded variables
    read as, ``criteria`` the criteria on them. ONE sentence for Remove
    plot's tooltip and the Variables drawer's grey row, in the singular or
    the plural."""
    one = len(texts) == 1
    return (
        f"{', '.join(texts)} {'is' if one else 'are'} scored by {criteria} "
        f"{'criterion' if criteria == 1 else 'criteria'} and this is the only "
        f"plot that shows {'it' if one else 'them'}. Add {'it' if one else 'them'} "
        f"to another plot first, or remove {'its' if one else 'their'} criteria."
    )


def layout_op(model_id: str, entry: dict | None, layout: Layout) -> PatchOp:
    """The ONE op a gesture stages: ``add`` when the effective entry has no
    ``plots`` key (or no entry: the op creates it), ``replace`` when it has."""
    verb = "replace" if entry is not None and "plots" in entry else "add"
    return PatchOp(model_id, verb, "/plots", plots_to_raw(layout))


def reset_op(model_id: str) -> PatchOp:
    """Reset layout: the key goes, and the test draws one plot per tracked
    variable again."""
    return PatchOp(model_id, "remove", "/plots")
