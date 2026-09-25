"""The Variables drawer's rows (the roadmap's 3c; DESIGN.md "The Variables
drawer"), as data: the page draws them and knows none of the rules.

Every variable of the last result, in groups — the tracked ones first, then
by the first dot component of the NAME ("(top level)" for a name with
none), the diagnostics last — with arrays folded into one row over their
LABELS (`figures.families`, the one family rule both tiers use), filtered,
and each row's state against the selected plot."""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass

from ..backends.types import _pattern_to_regex, is_glob_pattern
from ..discovery.plots import PlotSpec
from ..figures.families import family_label, group_families
from .layout import plots_showing

TOP_LEVEL = "(top level)"


@dataclass(frozen=True)
class VarRow:
    key: str  #: the family key, or the name of a lone variable
    members: tuple[str, ...]  #: names, by index; one for a lone variable
    text: str  #: what it reads as: the family label, or the label
    name: str | None  #: the muted name beside ``text``, when they differ
    on_selected: int  #: members the selected plot shows
    elsewhere: tuple[int, ...]  #: other plots showing any member, 0-based
    #: members a removal from the selected plot would leave on no plot while
    #: a criterion scores them (the owner's ruling): the row refuses it.
    stranded: tuple[str, ...]
    criteria: int  #: criteria on the stranded members, for the sentence


@dataclass(frozen=True)
class VarGroup:
    title: str
    rows: tuple[VarRow, ...]


def matcher(needle: str, *, regex: bool, case: bool) -> Callable[[str], bool]:
    """What the filter box keeps. A regex (``.*`` on) searches; a glob (a
    ``*`` or ``?`` in it) matches the WHOLE text with ``[ ]`` literal — the
    rule a spec's ``variables`` list uses, so what you type here means what
    it means there; anything else is a substring. ``re.error`` for a bad
    regex, for the drawer to say."""
    flags = 0 if case else re.IGNORECASE
    if not needle:
        return lambda text: True
    if regex:
        pattern = re.compile(needle, flags)
        return lambda text: pattern.search(text) is not None
    if is_glob_pattern(needle):
        glob = re.compile(_pattern_to_regex(needle).pattern, flags)
        return lambda text: glob.fullmatch(text) is not None
    folded = needle if case else needle.lower()
    return lambda text: folded in (text if case else text.lower())


def _group_title(name: str, tracked: set[str], diagnostics: set[str]) -> str:
    if name in tracked:
        return "Tracked"
    if name in diagnostics:
        return "Diagnostics"
    head, dot, _ = name.partition(".")
    # the component, not its index: `port[2, 1].C[1]` is in "port"
    return head.split("[", 1)[0] if dot else TOP_LEVEL


def variable_groups(
    *,
    names: Sequence[str],
    tracked: Sequence[str],
    diagnostics: Collection[str],
    labels: Mapping[str, str],
    layout: Sequence[PlotSpec],
    selected: int | None,
    scored: Mapping[str, int],
    keep: Callable[[str], bool],
) -> list[VarGroup]:
    """``names`` in the drawer's groups. ``labels`` maps a name to what it
    reads as (absent: the name); ``scored`` maps a scored name to its number
    of criteria; ``keep`` is the filter, asked of the name AND the label."""
    tracked_set, diag = set(tracked), set(diagnostics)
    kept = [n for n in names if keep(n) or (n in labels and keep(labels[n]))]
    by_title: dict[str, list[str]] = {}
    for name in kept:
        by_title.setdefault(_group_title(name, tracked_set, diag), []).append(name)
    middle = sorted(
        t for t in by_title if t not in ("Tracked", TOP_LEVEL, "Diagnostics")
    )
    order = ["Tracked", *middle, TOP_LEVEL, "Diagnostics"]
    layout = tuple(layout)
    showing = {n: plots_showing(layout, n) for n in kept}
    out: list[VarGroup] = []
    for title in order:
        members_of = by_title.get(title)
        if not members_of:
            continue
        rows = []
        for key, members in group_families(members_of, labels):
            text = family_label(key, members, labels)
            lone = members[0] if len(members) == 1 else None
            on = [m for m in members if selected is not None and selected in showing[m]]
            elsewhere = sorted(
                {i for m in members for i in showing[m] if i != selected}
            )
            stuck = tuple(m for m in on if m in scored and showing[m] == (selected,))
            rows.append(
                VarRow(
                    key=key,
                    members=tuple(members),
                    text=text,
                    name=lone if lone is not None and text != lone else None,
                    on_selected=len(on),
                    elsewhere=tuple(elsewhere),
                    stranded=stuck,
                    criteria=sum(scored[m] for m in stuck),
                )
            )
        out.append(VarGroup(title, tuple(rows)))
    return out


def cap(groups: list[VarGroup], limit: int) -> tuple[list[VarGroup], int, int]:
    """At most ``limit`` rows, groups in order: ``(groups, shown, total)``.
    A result of thousands of names drew thousands of rows otherwise."""
    total = sum(len(g.rows) for g in groups)
    out, left = [], limit
    for g in groups:
        if left <= 0:
            break
        out.append(VarGroup(g.title, g.rows[:left]))
        left -= len(out[-1].rows)
    return out, min(total, limit), total
