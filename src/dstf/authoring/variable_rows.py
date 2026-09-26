"""The Variables drawer's rows (the roadmap's 3c; DESIGN.md "The Variables
drawer"), as data: the page draws them and knows none of the rules.

Every variable of the last result, in groups — the tracked ones first, then
by the first dot component of the NAME, inside any ``der(...)`` ("(top
level)" for a name with none), the diagnostics last — with arrays folded
into one row (`figures.families`, the one family rule both tiers use),
filtered, and each row's state against the selected plot."""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, replace

from ..backends.types import is_glob_pattern
from ..discovery.plots import PlotSpec
from ..figures.families import family_label, group_families, mask_quoted

TOP_LEVEL = "(top level)"


@dataclass(frozen=True)
class VarRow:
    #: the family key (``pipe.T[]``; a lone variable's too when it has one,
    #: ``$DER.x[]``), else the name. Not unique: a tracked family whose
    #: expressions are ``pipe.T[i]`` keys ``pipe.T[]`` as the result file's
    #: own ``pipe.T[i]`` do — ``id`` is.
    key: str
    #: ``<group rank>/<group title>/<key>``, unique within the drawer. The
    #: rank is in it because a title is not unique: a tracked family whose
    #: expressions read ``Tracked.a[i]`` and the untracked ``Tracked.a[i]``
    #: beside it are both "Tracked" and both key ``Tracked.a[]``.
    id: str
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
    #: 0 tracked, 1 a prefix group, 2 top level, 3 diagnostics: a variable
    #: named ``Tracked.a`` is in a prefix group titled "Tracked", never in
    #: the tracked one
    rank: int
    title: str
    rows: tuple[VarRow, ...]


def matcher(needle: str, *, regex: bool, case: bool) -> Callable[[str], bool]:
    """What the filter box keeps. A regex (``.*`` on) searches; a glob (a
    ``*`` or ``?`` in it) matches the WHOLE text with ``[ ]`` literal — the
    rule a spec's ``variables`` list uses, so what you type here means what
    it means there (`_glob`); anything else is a substring. ``re.error`` for
    a bad regex, for the drawer to say.

    A typed regex is Python's `re`, backtracking and all: it is the user's
    own pattern on their own local tool. A glob is not left to it — a glob
    compiled to one regex, ``*a*a*a*a*a*a*a*b`` over one 60-character name,
    took over 20 s on the UI event loop, blocking every tab."""
    flags = 0 if case else re.IGNORECASE
    if not needle:
        return lambda text: True
    if regex:
        pattern = re.compile(needle, flags)
        return lambda text: pattern.search(text) is not None
    if is_glob_pattern(needle):
        return _glob(needle, flags)
    folded = needle if case else needle.lower()
    return lambda text: folded in (text if case else text.lower())


def _glob(needle: str, flags: int) -> Callable[[str], bool]:
    """``needle`` matched against the whole text as `_pattern_to_regex`'s
    anchored regex matches it, without backtracking. Split on ``*``, each
    piece is a fixed-length regex (``?`` is ``.``, everything else literal)
    with no quantifier: the first must match at the start, the last at the
    end, and each middle one is found left to right — the leftmost place
    leaves the most text for the rest, so no other place is ever tried. A
    ``*`` spans no newline and ``?`` matches none, as the regex's ``.``
    does not (`test_the_glob_means_what_the_specs_regex_means`)."""
    pieces = [
        re.compile("".join("." if ch == "?" else re.escape(ch) for ch in p), flags)
        for p in needle.split("*")
    ]
    if len(pieces) == 1:  # a ``?`` and no ``*``
        (only,) = pieces
        return lambda text: only.fullmatch(text) is not None
    head, *middle, tail = pieces
    width = len(needle.rsplit("*", 1)[1])  # the last piece's, one per character

    def keep(text: str) -> bool:
        start, end = head.match(text), len(text) - width
        if start is None or end < start.end() or tail.fullmatch(text, end) is None:
            return False
        at = start.end()
        for piece in middle:
            found = piece.search(text, at, end)
            if found is None or text.find("\n", at, found.start()) != -1:
                return False
            at = found.end()
        return text.find("\n", at, end) == -1

    return keep


def _group(name: str, tracked: set[str], diagnostics: set[str]) -> tuple[int, str]:
    if name in tracked:
        return 0, "Tracked"
    if name in diagnostics:
        return 3, "Diagnostics"
    # a derivative is in the group of what it differentiates:
    # `der(pipe.T[1])` is in "pipe", never "der(pipe"
    while name.startswith("der("):
        name = name[4:]
    # found in the mask: `'a.b'.c` is in "'a.b'", never "'a"
    masked = mask_quoted(name)
    dot = masked.find(".")
    if dot == -1:
        return 2, TOP_LEVEL
    # the component, not its index: `port[2, 1].C[1]` is in "port"
    bracket = masked.find("[", 0, dot)
    return 1, name[: dot if bracket == -1 else bracket]


def variable_groups(
    *,
    names: Sequence[str],
    tracked: Sequence[str],
    diagnostics: Collection[str],
    labels: Mapping[str, str],
    texts: Mapping[str, str],
    layout: Sequence[PlotSpec],
    selected: int | None,
    scored: Mapping[str, int],
    keep: Callable[[str], bool],
) -> list[VarGroup]:
    """``names`` in the drawer's groups. ``scored`` maps a scored name to
    its number of criteria; ``keep`` is the filter, asked of the name AND
    the label. Absent from either map, a name reads as itself.

    Two maps, the report's rule for a plot title
    (``reporting/report_suite.py``): the FOLD is over ``texts`` (the test's
    disambiguated texts, ``pipe.T[1] (u1)``), so two variables whose labels
    collide belong to no family — ``x={pipe.T[1], pipe.T[1]}`` is two rows,
    never one reading ``pipe.T[1,1]``; a row READS ``labels`` (the plain
    label), with the name muted beside it where they differ."""
    tracked_set, diag = set(tracked), set(diagnostics)
    kept = [n for n in names if keep(n) or (n in labels and keep(labels[n]))]
    by_group: dict[tuple[int, str], list[str]] = {}
    for name in kept:
        by_group.setdefault(_group(name, tracked_set, diag), []).append(name)
    # the layout indexed once: scanning every plot per name cost 395 ms over
    # 6,093 names and 300 plots
    index: dict[str, list[int]] = {}
    for i, plot in enumerate(layout):
        for s in plot.series:
            at = index.setdefault(s.var, [])
            if not at or at[-1] != i:
                at.append(i)
    showing = {n: tuple(index.get(n, ())) for n in kept}
    out: list[VarGroup] = []
    for rank, title in sorted(by_group, key=lambda g: (g[0], g[1].casefold(), g[1])):
        rows = []
        for key, members in group_families(by_group[rank, title], texts):
            text = family_label(key, members, labels)
            lone = members[0] if len(members) == 1 else None
            on = [m for m in members if selected is not None and selected in showing[m]]
            elsewhere = sorted(
                {i for m in members for i in showing[m] if i != selected}
            )
            # a row partly on the plot ADDS its missing members on a click:
            # no removal is on offer, so nothing is stranded
            stuck = (
                tuple(m for m in on if m in scored and showing[m] == (selected,))
                if len(on) == len(members)
                else ()
            )
            rows.append(
                VarRow(
                    key=key,
                    id=f"{rank}/{title}/{key}",
                    members=tuple(members),
                    text=text,
                    name=lone if lone is not None and text != lone else None,
                    on_selected=len(on),
                    elsewhere=tuple(elsewhere),
                    stranded=stuck,
                    criteria=sum(scored[m] for m in stuck),
                )
            )
        out.append(VarGroup(rank, title, tuple(rows)))
    return out


def cap(groups: list[VarGroup], limit: int) -> tuple[list[VarGroup], int, int]:
    """At most ``limit`` rows, groups in order: ``(groups, shown, total)``.
    A result of thousands of names drew thousands of rows otherwise."""
    total = sum(len(g.rows) for g in groups)
    out, left = [], limit
    for g in groups:
        if left <= 0:
            break
        out.append(replace(g, rows=g.rows[:left]))
        left -= len(out[-1].rows)
    return out, min(total, limit), total
