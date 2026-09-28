"""A test's plot extras as the tool edits them: the `plots` key's entries —
context series on a plot, and watch plots — changed one gesture at a time
and staged as ONE op on `/plots` (`Session.stage` collapses a run of them,
Convention 8). UI-free: the Variables drawer, a plot card and the tree
card's Reset layout all call in, so the rules below hold on every surface
at once.

The plots themselves are derived from the criteria tree
(:func:`~dstf.discovery.plots.derive_plots`): nothing here can remove a
derived plot or take its subject.

The edits of the key's former shape below the new ones (`Layout`,
`add_series` ... `stranded_sentence`) are what the Test page's plot cards
and the Variables drawer still call; they go when those do (the plan's
Tasks 5-6)."""

from __future__ import annotations

from collections.abc import Sequence

from ..discovery.plots import (
    PlotExtra,
    PlotSpec,
    ResolvedPlot,
    SeriesSpec,
    plots_to_raw,
)
from .spec_document import PatchOp

Extras = tuple[PlotExtra, ...]


def _entry_of(extras: Sequence[PlotExtra], plot: ResolvedPlot) -> int | None:
    """The index of the entry on any member of ``plot``'s subject: a
    family's entry is whichever member has one."""
    return next((i for i, e in enumerate(extras) if e.var in plot.subject), None)


def add_context(
    extras: Sequence[PlotExtra], plot: ResolvedPlot, names: Sequence[str]
) -> Extras:
    """``names`` drawn as context on ``plot``, in order, minus its subject's
    members and what it has. The entry is the one on any subject member,
    else a new one on the subject's first member, at the end."""
    extras = tuple(extras)
    at = _entry_of(extras, plot)
    entry = extras[at] if at is not None else PlotExtra(plot.subject[0])
    added = tuple(
        n
        for n in dict.fromkeys(names)
        if n not in plot.subject and n not in entry.context
    )
    new = PlotExtra(entry.var, entry.context + added)
    if at is None:
        return extras + (new,)
    return extras[:at] + (new,) + extras[at + 1 :]


def remove_context(
    extras: Sequence[PlotExtra], plot: ResolvedPlot, name: str
) -> Extras:
    """``plot`` without context ``name``. A derived plot's entry left with
    no context goes — it records nothing; a watch plot's stays (it IS the
    plot)."""
    extras = tuple(extras)
    at = _entry_of(extras, plot)
    if at is None:
        return extras
    entry = extras[at]
    new = PlotExtra(entry.var, tuple(n for n in entry.context if n != name))
    if not new.context and not plot.watch:
        return extras[:at] + extras[at + 1 :]
    return extras[:at] + (new,) + extras[at + 1 :]


def new_watch(extras: Sequence[PlotExtra], var: str) -> Extras:
    """A watch plot of ``var`` after the others. A second entry on one
    variable is refused, as `parse_plots` refuses it."""
    if any(e.var == var for e in extras):
        raise ValueError(f"{var!r} already has an entry")
    return (*extras, PlotExtra(var))


def remove_watch(extras: Sequence[PlotExtra], var: str) -> Extras:
    """The watch plot of ``var`` gone (KeyError when there is none)."""
    kept = tuple(e for e in extras if e.var != var)
    if len(kept) == len(extras):
        raise KeyError(var)
    return kept


def layout_op(
    model_id: str, entry: dict | None, extras: Sequence[PlotExtra]
) -> PatchOp:
    """The ONE op a gesture stages: ``add`` when the effective entry has no
    ``plots`` key (or no entry: the op creates it), ``replace`` when it has.
    No extras left is ``remove`` — the key goes rather than staying ``[]``,
    so Reset layout hides again."""
    if not extras:
        return reset_op(model_id)
    verb = "replace" if entry is not None and "plots" in entry else "add"
    return PatchOp(model_id, verb, "/plots", plots_to_raw(extras))


def reset_op(model_id: str) -> PatchOp:
    """Reset layout: the key goes, and with it every context series and
    watch plot; the derived plots stay."""
    return PatchOp(model_id, "remove", "/plots")


# -- the key's former shape, until the plot cards and the drawer move --------

Layout = tuple[PlotSpec, ...]


def drawn_layout(plots: Sequence[ResolvedPlot]) -> Layout:
    """What the page draws, in the key's former shape: what the plot cards'
    Remove plot and the Variables drawer's checks still read, until they
    edit the extras (the plan's Tasks 5-6)."""
    return tuple(
        PlotSpec(series=tuple(SeriesSpec(n) for n in p.series), kind=p.kind)
        for p in plots
    )


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
