"""A test's plot extras as the tool edits them: the `plots` key's entries —
context series on a plot, and watch plots — changed one gesture at a time
and staged as ONE op on `/plots` (`Session.stage` collapses a run of them,
Convention 8). UI-free: the Variables drawer, a plot card and the tree
card's Reset layout all call in, so the rules below hold on every surface
at once.

The plots themselves are derived from the criteria tree
(:func:`~dstf.discovery.plots.derive_plots`): nothing here can remove a
derived plot or take its subject."""

from __future__ import annotations

from collections.abc import Sequence

from ..discovery.plots import PlotExtra, ResolvedPlot, plots_to_raw
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
