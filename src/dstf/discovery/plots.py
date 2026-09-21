"""The optional ``plots`` key of a ``test_spec.json`` entry (brief §3.10).

A test's plot layout: a list of plots, each a ``kind`` and a list of series,
each a variable of the result. Read by both tiers, ignored by scoring. Absent
means one plot per tracked variable; ``[]`` means no plots. ``kind`` is
reserved for later plot kinds and a series for an expression and a unit
label; until they exist the parser refuses what it does not know, so a typo
is loud. No I/O here: the spec parser, the patch validator, the Session and
the report all call in.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..backends.types import is_glob_pattern

if TYPE_CHECKING:
    from .test_registry import TestModel

PLOT_KINDS = ("time",)


class PlotsSpecError(ValueError):
    """A ``plots`` value the parser refuses; the message starts with where."""


@dataclass(frozen=True)
class SeriesSpec:
    var: str


@dataclass(frozen=True)
class PlotSpec:
    series: tuple[SeriesSpec, ...]
    kind: str = "time"


@dataclass(frozen=True)
class ResolvedPlot:
    """One plot as a tier draws it: the series the result has, in order, and
    the ones it lacks (decision 3)."""

    index: int
    kind: str
    series: tuple[str, ...]
    missing: tuple[str, ...]


def _unknown(obj: dict, known: set[str], at: str) -> None:
    for key in obj:
        if key not in known:
            raise PlotsSpecError(f"{at}: unknown key {key!r}")


def parse_plots(raw: object, *, path: str = "plots") -> tuple[PlotSpec, ...]:
    if not isinstance(raw, list):
        raise PlotsSpecError(f"{path}: must be a list of plots")
    plots: list[PlotSpec] = []
    for i, plot in enumerate(raw):
        at = f"{path}[{i}]"
        if not isinstance(plot, dict):
            raise PlotsSpecError(f"{at}: must be an object")
        _unknown(plot, {"kind", "series"}, at)
        kind = plot.get("kind", "time")
        if kind not in PLOT_KINDS:
            raise PlotsSpecError(
                f"{at}.kind: {kind!r} is not a plot kind (known: {', '.join(PLOT_KINDS)})"
            )
        series_raw = plot.get("series")
        if not isinstance(series_raw, list) or not series_raw:
            raise PlotsSpecError(f"{at}.series: must be a non-empty list")
        series: list[SeriesSpec] = []
        for j, one in enumerate(series_raw):
            s_at = f"{at}.series[{j}]"
            if not isinstance(one, dict):
                raise PlotsSpecError(f"{s_at}: must be an object")
            _unknown(one, {"var"}, s_at)
            var = one.get("var")
            if not isinstance(var, str) or not var.strip():
                raise PlotsSpecError(f"{s_at}.var: must be a non-empty string")
            if is_glob_pattern(var):
                raise PlotsSpecError(
                    f"{s_at}.var: {var!r} is a pattern; a series names one variable"
                )
            if any(s.var == var for s in series):
                raise PlotsSpecError(f"{s_at}.var: {var!r} is already on this plot")
            series.append(SeriesSpec(var))
        plots.append(PlotSpec(series=tuple(series), kind=kind))
    return tuple(plots)


def plots_to_raw(plots: Sequence[PlotSpec]) -> list[dict]:
    """The JSON a writer stores: ``kind`` always written."""
    return [
        {"kind": p.kind, "series": [{"var": s.var} for s in p.series]} for p in plots
    ]


def default_plots(tracked: Sequence[str]) -> tuple[PlotSpec, ...]:
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
    variable the result file left nameless has nothing to plot by; the
    comparison's own name for it arrives through ``scored``.

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
    """The plots both tiers draw for ``test``: its ``plots`` key, else one per
    name in ``tracked`` (the variables the comparison scored or the result
    resolved, in the page's order). ``available`` is every name in the result
    file, or ``None`` when the backend cannot list it — then nothing is
    called missing."""
    plots = test.plots if test.plots is not None else default_plots(tracked)
    have = None if available is None else set(available) | set(tracked)
    out: list[ResolvedPlot] = []
    for i, plot in enumerate(plots):
        names = [s.var for s in plot.series]
        out.append(
            ResolvedPlot(
                index=i,
                kind=plot.kind,
                series=tuple(n for n in names if have is None or n in have),
                missing=tuple(n for n in names if have is not None and n not in have),
            )
        )
    return tuple(out)
