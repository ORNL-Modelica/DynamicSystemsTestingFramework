"""The Variables drawer's rows (the roadmap's 3c; DESIGN.md "The Variables
drawer"), as data: the page draws them and knows none of the rules.

Every variable of the last result, in groups, with arrays folded into one
row (`figures.families`, the one family rule both tiers use), filtered,
and each row's state against the selected plot. Three groupings (the
drawer's group menu): by component — the tracked ones first, then by the
first dot component of the NAME, inside any ``der(...)`` ("(top level)"
for a name with none), the diagnostics last; flat, one group; and tracked
first, the rest in one group after them. Two orders: result-file order, or
A→Z by what a row reads as. Three reads (the names menu): the expression,
the result-file name, or both."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, replace

from ..discovery.plots import PlotSpec
from ..figures.families import family_label, group_families, mask_quoted

TOP_LEVEL = "(top level)"
#: The one group of the flat grouping, and the second of "tracked first".
ALL, OTHER = "All variables", "Other variables"


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
    #: beside it are both "Tracked" and both key ``Tracked.a[]``. A tracked
    #: family in a group of untracked ones too (Flat) keys ``tracked:<key>``:
    #: under Flat both ``pipe.T[]`` rows are in "All variables".
    id: str
    members: tuple[str, ...]  #: names, by index; one for a lone variable
    text: str  #: what it reads as: the family label, or the label
    #: what it reads as by result-file names: the family label folded over
    #: the NAMES (``unitTests.x[1:2]``), or the name
    file_text: str
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
    group: str = "component",
    order: str = "file",
    read: str = "both",
) -> list[VarGroup]:
    """``names`` in the drawer's groups. ``scored`` maps a scored name to
    its number of criteria; ``keep`` is the filter, asked of the name AND
    the label. Absent from either map, a name reads as itself.

    Two maps, the report's rule for a plot title
    (``reporting/report_suite.py``): the FOLD is over ``texts`` (the test's
    disambiguated texts, ``pipe.T[1] (u1)``), so two variables whose labels
    collide belong to no family — ``x={pipe.T[1], pipe.T[1]}`` is two rows,
    never one reading ``pipe.T[1,1]``; a row READS ``labels`` (the plain
    label), with the name muted beside it where they differ.

    ``group`` (the drawer's group menu): ``component`` — tracked, then by
    component, top level, diagnostics; ``flat`` — one group, "All
    variables"; ``tracked`` — "Tracked", then "Other variables" with
    everything else, diagnostics included. Families fold in every one, the
    tracked names apart from the rest: under Flat a tracked family whose
    expressions are ``pipe.T[i]`` and the result's own ``pipe.T[i]`` fold
    to the same key, and one row over both read ``pipe.T[1,1,2,2]``.
    ``order``: ``file`` keeps a group's rows in first-appearance
    (result-file) order; ``name`` sorts them by what they are shown as —
    ``file_text`` when ``read`` (the names menu) is ``file``, else
    ``text``."""
    tracked_set, diag = set(tracked), set(diagnostics)
    kept = [n for n in names if keep(n) or (n in labels and keep(labels[n]))]
    by_group: dict[tuple[int, str], list[str]] = {}
    for name in kept:
        if group == "flat":
            where = (1, ALL)
        elif group == "tracked":
            where = (0, "Tracked") if name in tracked_set else (1, OTHER)
        else:
            where = _group(name, tracked_set, diag)
        by_group.setdefault(where, []).append(name)
    # the layout indexed once: scanning every plot per name cost 395 ms over
    # 6,093 names and 300 plots
    index: dict[str, list[int]] = {}
    for i, plot in enumerate(layout):
        for s in plot.series:
            at = index.setdefault(s.var, [])
            if not at or at[-1] != i:
                at.append(i)
    showing = {n: tuple(index.get(n, ())) for n in kept}
    position = {n: i for i, n in enumerate(kept)}
    out: list[VarGroup] = []
    for rank, title in sorted(by_group, key=lambda g: (g[0], g[1].casefold(), g[1])):
        in_group = by_group[rank, title]
        # Folded apart, then merged back in result-file order (a family's
        # first appearance, `group_families`' own order).
        families = sorted(
            group_families([n for n in in_group if n in tracked_set], texts)
            + group_families([n for n in in_group if n not in tracked_set], texts),
            key=lambda f: min(position[m] for m in f[1]),
        )
        rows = []
        for key, members in families:
            text = family_label(key, members, labels)
            # Folded again, over the NAMES: `key` is the fold over the texts
            # (`pipe.T[]` for x={pipe.T[3], pipe.T[5]}), and read with the
            # names' indices it said `pipe.T[1:2]` for `unitTests.x[1:2]`.
            file_text = ", ".join(
                family_label(k, m) for k, m in group_families(members)
            )
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
                    id=f"{rank}/{title}/"
                    + ("tracked:" if rank != 0 and members[0] in tracked_set else "")
                    + key,
                    members=tuple(members),
                    text=text,
                    file_text=file_text,
                    name=lone if lone is not None and text != lone else None,
                    on_selected=len(on),
                    elsewhere=tuple(elsewhere),
                    stranded=stuck,
                    criteria=sum(scored[m] for m in stuck),
                )
            )
        if order == "name":
            shown = (lambda r: r.file_text) if read == "file" else (lambda r: r.text)
            rows.sort(key=lambda r: (shown(r).casefold(), shown(r)))
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
