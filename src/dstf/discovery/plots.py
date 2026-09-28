"""The optional ``plots`` key of a ``test_spec.json`` entry, and the plots
both tiers draw.

A test's plots are DERIVED from its criteria tree (:func:`derive_plots`):
one plot per scored variable or array family, where the tree first names
it. The key holds only a person's extras — each entry one variable and the
``context`` series drawn grey beside it. An entry on a scored variable
decorates that variable's plot; an entry on an unscored one is a WATCH
plot, after the derived ones, in list order. Absent and ``[]`` both mean no
extras (``[]`` is kept as the key's value, so a writer can tell it from an
absent key); ``null`` is refused. Read by both tiers, ignored by scoring.
The parser refuses what it does not know, so a typo is loud. No I/O here:
the spec parser, the patch validator, the Session and the report all call
in.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..backends.types import is_glob_pattern

if TYPE_CHECKING:
    from ..comparison.tree_spec import LeafSpec, SpecNode
    from .test_registry import TestModel

#: A family folds onto one plot only when its members' largest magnitudes
#: are within this ratio of each other (the owner's ruling O1, 2026-09-28):
#: TRANSFORM's `check_*` tests hold unrelated quantities in one local
#: `x[i]` (`check_atm`: ~1e-5 beside ~1e5), and folding them by expression
#: alone drew one of them as a flat line on the other's axis.
ONE_SCALE_RATIO = 10.0


class PlotsSpecError(ValueError):
    """A ``plots`` value the parser refuses; the message starts with where."""


@dataclass(frozen=True)
class PlotExtra:
    """One entry of the ``plots`` key: a variable and its context series.
    Scored, it decorates that variable's derived plot; unscored, it is a
    watch plot."""

    var: str
    context: tuple[str, ...] = ()


@dataclass(frozen=True)
class SeriesSpec:
    """The key's former shape, kept importable until the Test page's plot
    cards and the Variables drawer stop editing it (the plan's Tasks 5-6)."""

    var: str


@dataclass(frozen=True)
class PlotSpec:
    """The key's former shape; see :class:`SeriesSpec`."""

    series: tuple[SeriesSpec, ...]
    kind: str = "time"


@dataclass(frozen=True, kw_only=True)
class ResolvedPlot:
    """One plot as a tier draws it. ``subject`` is one variable or the
    members of one array family (by index); ``context`` the grey, unscored
    series beside it; ``watch`` a plot no criterion names; ``missing`` the
    subject and context names the result lacks."""

    index: int
    subject: tuple[str, ...]
    context: tuple[str, ...] = ()
    watch: bool = False
    missing: tuple[str, ...] = ()
    kind: str = "time"

    @property
    def series(self) -> tuple[str, ...]:
        """Every name the plot draws — the subject, then the context —
        missing ones included: a reader checks ``missing``."""
        return self.subject + self.context


def _unknown(obj: dict, known: set[str], at: str) -> None:
    for key in obj:
        if key not in known:
            raise PlotsSpecError(f"{at}: unknown key {key!r}")


def _one_name(value: object, at: str, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlotsSpecError(f"{at}: must be a non-empty string")
    if is_glob_pattern(value):
        raise PlotsSpecError(f"{at}: {value!r} is a pattern; {what} one variable")
    return value


def parse_plots(raw: object, *, path: str = "plots") -> tuple[PlotExtra, ...]:
    if not isinstance(raw, list):
        raise PlotsSpecError(f"{path}: must be a list of plot extras")
    extras: list[PlotExtra] = []
    for i, entry in enumerate(raw):
        at = f"{path}[{i}]"
        if not isinstance(entry, dict):
            raise PlotsSpecError(f"{at}: must be an object")
        _unknown(entry, {"var", "context"}, at)
        var = _one_name(entry.get("var"), f"{at}.var", "an entry names")
        if any(e.var == var for e in extras):
            raise PlotsSpecError(f"{at}.var: {var!r} already has an entry")
        context_raw = entry.get("context", [])
        if not isinstance(context_raw, list):
            raise PlotsSpecError(f"{at}.context: must be a list of names")
        context: list[str] = []
        for j, one in enumerate(context_raw):
            c_at = f"{at}.context[{j}]"
            name = _one_name(one, c_at, "a context series names")
            if name == var:
                raise PlotsSpecError(f"{c_at}: {name!r} is the plot's own variable")
            if name in context:
                raise PlotsSpecError(f"{c_at}: {name!r} is already in the context")
            context.append(name)
        extras.append(PlotExtra(var, tuple(context)))
    return tuple(extras)


def plots_to_raw(extras: Sequence[PlotExtra]) -> list[dict]:
    """The JSON a writer stores: ``context`` only when there is some."""
    return [
        {"var": e.var, **({"context": list(e.context)} if e.context else {})}
        for e in extras
    ]


def tree_leaves(spec: SpecNode | None) -> list[LeafSpec] | None:
    """The leaves of ``spec`` that name a series of the result, depth
    first; ``None`` for no tree. A stored-scalar leaf (a string
    ``quantity``) names a statistic, not a series, and has no plot.

    Walks the SPEC, never the materialized tree: the Session's plots are
    painted before any baseline is read."""
    if spec is None:
        return None
    from ..comparison.tree_spec import LeafSpec, resolves_a_variable

    out: list[LeafSpec] = []

    def walk(node: SpecNode) -> None:
        if isinstance(node, LeafSpec):
            if resolves_a_variable(node):
                out.append(node)
            return
        for child in node.children:
            walk(child)

    walk(spec)
    return out


def result_magnitudes(result: object) -> dict[str, float]:
    """``{name: max |v|}`` over each result variable's actual samples — what
    :func:`derive_plots` folds a family by. A variable with no finite sample
    reads NaN (its scale is unknown); it is still a key, because the keys
    are also the names the result CARRIES. A failed run's partial file
    contributes nothing, as in :func:`tracked_names`."""
    import numpy as np

    if result is None or not getattr(result, "success", True):
        return {}
    out: dict[str, float] = {}
    for var in getattr(result, "variables", None) or ():
        if not var.name:
            continue
        values = np.abs(np.asarray(var.values, dtype=float))
        finite = values[np.isfinite(values)]
        out[var.name] = float(finite.max()) if finite.size else math.nan
    return out


def _one_scale(members: Sequence[str], magnitudes: Mapping[str, float]) -> bool:
    """Do ``members`` share a scale (O1)? Every one must have a finite
    magnitude; those above zero must lie within :data:`ONE_SCALE_RATIO` of
    each other, and a member at zero folds with any."""
    scales = [magnitudes.get(name) for name in members]
    if any(m is None or not math.isfinite(m) for m in scales):
        return False
    positive = [m for m in scales if m is not None and m > 0]
    return not positive or max(positive) <= ONE_SCALE_RATIO * min(positive)


def derive_plots(
    leaves: Sequence[LeafSpec] | None,
    labels: Mapping[str, str],
    extras: Sequence[PlotExtra],
    *,
    tracked: Sequence[str],
    available: Iterable[str] | None = None,
    magnitudes: Mapping[str, float] | None = None,
) -> tuple[ResolvedPlot, ...]:
    """The plots both tiers draw for one test.

    ``leaves`` are the tree's (:func:`tree_leaves`), in depth-first order.
    Their variables fold into array families by LABEL (``labels``: name ->
    its plain label, the expression for a UnitTests variable — the
    :mod:`~dstf.figures.families` rule), and a family folds only when its
    members share a scale by ``magnitudes`` (:func:`result_magnitudes`;
    ``None``, no result, folds nothing). One plot per folded family or lone
    variable, where the tree first names it — a family where the tree first
    names any member, an unfolded member where the tree first names IT.
    ``None`` leaves (no tree: nothing scored) derive one plot per
    ``tracked`` name instead, unfolded.

    Each extra on a member of a derived subject adds its context there;
    every other extra is a watch plot, after the derived ones, in list
    order.

    ``available`` is every name in the result file (``None`` when it
    cannot be listed: nothing is missing then). A name the result carries
    (a key of ``magnitudes``) is never missing — a listing of the file may
    be filtered; a leaf's name the result lacks is."""
    from ..figures.families import group_families

    subjects: list[tuple[str, ...]]
    if leaves is None:
        subjects = [(name,) for name in dict.fromkeys(n for n in tracked if n)]
    else:
        order = list(dict.fromkeys(leaf.variable for leaf in leaves if leaf.variable))
        family_of: dict[str, tuple[str, ...]] = {}
        for _, members in group_families(order, labels):
            folds = (
                len(members) > 1
                and magnitudes is not None
                and _one_scale(members, magnitudes)
            )
            for name in members:
                family_of[name] = tuple(members) if folds else (name,)
        subjects = list(dict.fromkeys(family_of[name] for name in order))
    member_of = {name: i for i, subject in enumerate(subjects) for name in subject}
    context: list[list[str]] = [[] for _ in subjects]
    watch: list[PlotExtra] = []
    for extra in extras:
        at = member_of.get(extra.var)
        if at is None:
            watch.append(extra)
            continue
        for name in extra.context:
            if name not in subjects[at] and name not in context[at]:
                context[at].append(name)
    have = None if available is None else set(available) | set(magnitudes or ())
    rows = [(s, tuple(c), False) for s, c in zip(subjects, context)]
    rows += [((e.var,), e.context, True) for e in watch]
    return tuple(
        ResolvedPlot(
            index=i,
            subject=subject,
            context=ctx,
            watch=is_watch,
            missing=tuple(
                n for n in subject + ctx if have is not None and n not in have
            ),
        )
        for i, (subject, ctx, is_watch) in enumerate(rows)
    )


def default_plots(tracked: Sequence[str]) -> tuple[PlotSpec, ...]:
    """The former default layout, for the former layout edits (see
    :class:`SeriesSpec`)."""
    return tuple(PlotSpec(series=(SeriesSpec(v),)) for v in tracked)


def tracked_names(scored: Sequence[str], result: object) -> list[str]:
    """The variables a test HAS a plot for by default, in the order both
    tiers draw them: what the comparison scored, then the rest of the loaded
    result's, de-duplicated. ONE rule, called by the tool's `Session.tracked`
    and by the report's `plot_inputs` — the two drew different default
    layouts (the report one plot per scored variable, the tool the union),
    and the owner's ruling is that both tiers draw the same plots.

    Every name here is the result file's (a UnitTests variable's is
    ``<instance>.x[i]``) and nothing relabels it, so a variable the tree
    scores and the result carries is one name and is listed once — listing
    the same trajectory under two spellings drew it twice (the 3b review). A
    result variable with no name has nothing to plot by and is skipped; a
    tree leaf's variable, on the other hand, is listed whether or not the
    result carries it, because ``scored`` is what the test's author wrote.

    A result of a simulation that FAILED contributes nothing: what a backend
    wrote before it stopped is a partial file, not the test's plots, and the
    page drew a card per half-written variable (the final review).

    ``scored`` comes first even when the tree's order differs from the
    result file's: it is the order the test's author wrote."""
    names = [name for name in scored if name]
    have = set(names)
    out = list(dict.fromkeys(names))
    if not getattr(result, "success", True):
        return out
    for var in getattr(result, "variables", None) or ():
        if not var.name or var.name in have:
            continue
        have.add(var.name)
        out.append(var.name)
    return out


def resolved_plots(
    test: TestModel, tracked: Sequence[str], *, available: Sequence[str] | None
) -> tuple[ResolvedPlot, ...]:
    """Interim, until the report derives its plots from the tree (the
    plan's Task 4): one plot per ``tracked`` name, the test's extras on
    them. ``available`` as in :func:`derive_plots`; a tracked name is never
    missing, as before."""
    return derive_plots(
        None,
        {},
        test.plots or (),
        tracked=tracked,
        available=None if available is None else [*available, *tracked],
    )
