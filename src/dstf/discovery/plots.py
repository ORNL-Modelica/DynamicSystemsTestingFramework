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

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..backends.types import is_glob_pattern

if TYPE_CHECKING:
    from ..backends.types import TestResult
    from ..comparison.tree_spec import LeafSpec, SpecNode
    from .test_registry import TestModel


class PlotsSpecError(ValueError):
    """A ``plots`` value the parser refuses; the message starts with where."""


@dataclass(frozen=True)
class PlotExtra:
    """One entry of the ``plots`` key: a variable and its context series.
    Scored, it decorates that variable's derived plot; unscored, it is a
    watch plot."""

    var: str
    context: tuple[str, ...] = ()


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
    """Refuse a key nothing reads, in the words every other part of the
    spec is refused with (``strict_keys.unknown_field``). Imported late:
    the comparison package reaches back into discovery."""
    from ..comparison.strict_keys import unknown_field

    problem = unknown_field(obj, known)
    if problem is not None:
        raise PlotsSpecError(f"{at}: {problem}")


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


def tree_leaves(
    spec: SpecNode | None, diagnostics: Collection[str]
) -> list[LeafSpec] | None:
    """The leaves of ``spec`` that a plot shows, depth first; ``None`` for
    no tree. A leaf on the Statistics card (`on_the_card`: a statistic, or
    a number one stored summary of a name the test lists — ``diagnostics``,
    its `diagnostic_names`) has no plot: numbers on the card, curves on
    plots.

    Walks the SPEC, never the materialized tree: the Session's plots are
    painted before any baseline is read."""
    if spec is None:
        return None
    from ..comparison.tree_spec import LeafSpec, on_the_card

    out: list[LeafSpec] = []

    def walk(node: SpecNode) -> None:
        if isinstance(node, LeafSpec):
            if not on_the_card(node, diagnostics):
                out.append(node)
            return
        for child in node.children:
            walk(child)

    walk(spec)
    return out


def plot_leaves(
    test: TestModel, compared: Sequence[str], diagnostics: Collection[str]
) -> list[LeafSpec] | None:
    """The leaves a test's plots derive from, in tree order: its authored
    ``metrics`` tree — even with nothing compared yet, a test with no
    baseline: the tree names what will be scored — else the implicit tree of
    what was ``compared``; ``None`` with neither (one plot per tracked
    variable, the owner's ruling O2). ONE rule, for the tool's
    `Session.plots` and the report. ``diagnostics``: the names the test
    lists (:func:`tree_leaves`)."""
    from ..comparison.tree_spec import synthesize_implicit_tree

    if test.metric_tree_spec is not None:
        return tree_leaves(test.metric_tree_spec, diagnostics)
    if not compared:
        return None
    return tree_leaves(
        synthesize_implicit_tree(
            list(compared), variable_overrides=test.variable_overrides
        ),
        diagnostics,
    )


def derive_plots(
    leaves: Sequence[LeafSpec] | None,
    extras: Sequence[PlotExtra],
    *,
    tracked: Sequence[str],
    unit_tests: Collection[str] = (),
    available: Iterable[str] | None = None,
) -> tuple[ResolvedPlot, ...]:
    """The plots both tiers draw for one test.

    ``leaves`` are the tree's (:func:`plot_leaves`), in depth-first order;
    ``None`` (no tree: nothing scored) derives from the ``tracked`` names
    instead. Their variables fold into array families by RESULT-FILE NAME
    (the :mod:`~dstf.figures.families` rule over the names: ``pipe.T[1]`` ..
    ``pipe.T[4]`` are one plot), with no scale check — except the
    ``unit_tests`` names, each its own plot whatever its expression (the
    owner's ruling O3: a UnitTests element is a test quantity of its own,
    and `check_atm`'s local ``x`` holds 1 Pa in atm beside 1 atm in Pa).
    One plot per family or lone variable, where the tree first names it — a
    family where the tree first names any member.

    Each extra on a member of a derived subject adds its context there;
    every other extra is a watch plot, after the derived ones, in list
    order.

    ``available`` is every name the plots can be drawn from — the result
    file's listing and the result's own variables — or ``None`` when it
    cannot be listed: nothing is missing then."""
    from ..figures.families import group_families

    if leaves is None:
        order = list(dict.fromkeys(n for n in tracked if n))
    else:
        order = list(dict.fromkeys(leaf.variable for leaf in leaves if leaf.variable))
    apart = set(unit_tests)
    family_of: dict[str, tuple[str, ...]] = {
        name: (name,) for name in order if name in apart
    }
    for _, members in group_families([n for n in order if n not in apart]):
        for name in members:
            family_of[name] = tuple(members)
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
    have = None if available is None else set(available)
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


def plots_of_test(
    test: TestModel,
    compared: Sequence[str],
    result: TestResult | None,
    *,
    listed: Iterable[str] | None,
    diagnostics: Collection[str],
) -> tuple[ResolvedPlot, ...]:
    """The plots of one test — ONE rule, called by the tool's
    `Session.plots` and the report's `plot_inputs`, so both tiers draw the
    same list.

    The leaves are :func:`plot_leaves`', the tracked names
    :func:`tracked_names`', the extras the entry's ``plots`` key. Which
    names are UnitTests elements — never folded (O3) — is asked of every
    leaf AND every tracked name: a leaf the result does not carry is an
    element all the same.

    ``diagnostics`` are the names the test lists (`diagnostic_names`): a
    number one stored summary of one of them answers is on the Statistics
    card, not a plot (:func:`tree_leaves`).

    ``listed`` is the result FILE's names, or ``None`` when it was not
    listed (the report lists it only for a test with extras). A name the
    result in memory carries is never missing — the file's listing may be
    filtered; with no listing, what the result carries is all the plots can
    draw, and a leaf it lacks is missing. With no result either, nothing is
    missing.

    A run that FAILED has no plots, extras included (O2, literally): what a
    backend wrote before it stopped is a partial file, not the test's
    plots."""
    from .unit_tests import is_unit_test_variable

    if result is not None and not result.success:
        return ()
    leaves = plot_leaves(test, compared, diagnostics)
    tracked = tracked_names(compared, result)
    names = [*(leaf.variable for leaf in leaves or ()), *tracked]
    available: list[str] | None = None
    if result is not None:
        available = [v.name for v in result.variables if v.name]
    if listed is not None:
        available = [*listed, *(available or ())]
    return derive_plots(
        leaves,
        tuple(test.plots or ()),
        tracked=tracked,
        unit_tests=[n for n in names if is_unit_test_variable(test, n)],
        available=available,
    )


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
    result file's. The result's own come in its order — the spec's
    patterns' order (`resolve_variable_patterns`: explicit names as
    written, a glob's matches in the file's order), never sorted."""
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
