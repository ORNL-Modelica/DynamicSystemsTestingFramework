"""The Variables drawer's rows (the roadmap's 3c; DESIGN.md "The Variables
drawer"), as data: the page draws them and knows none of the rules.

Every variable of the last result, in groups, with arrays folded into one
row the way the plots fold them (`discovery.plots.derive_plots`: by
RESULT-FILE NAME, a UnitTests element never), filtered, and each row's
state against the plots: in the selected plot's context,
its subject, some plot's subject. Three groupings (the
drawer's group menu): by component — the tracked ones first, then by the
first dot component of the NAME, inside any ``der(...)`` ("(top level)"
for a name with none), the diagnostics last; flat, one group; and tracked
first, the rest in one group after them. Two orders: result-file order, or
A→Z by what a row reads as. Three reads (the names menu): the expression,
the result-file name, or both."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, replace

from ..discovery.plots import ResolvedPlot
from ..figures.families import family_label, group_families, mask_quoted

TOP_LEVEL = "(top level)"
#: The one group of the flat grouping, and the second of "tracked first".
ALL, OTHER = "All variables", "Other variables"


@dataclass(frozen=True)
class VarRow:
    #: the family key (``pipe.T[]``; a lone variable's too when it has one,
    #: ``$DER.x[]``), else the name. Not unique: the tracked ``pipe.T[1:2]``
    #: keys ``pipe.T[]`` as the untracked rest of the array does — ``id`` is.
    key: str
    #: ``<group rank>/<group title>/<key>``, unique within the drawer. The
    #: rank is in it because a title is not unique: the tracked
    #: ``Tracked.a[1:2]`` and the untracked ``Tracked.a[3:4]`` beside it are
    #: both "Tracked" and both key ``Tracked.a[]``. A tracked family in a
    #: group of untracked ones too (Flat) keys ``tracked:<key>``: under Flat
    #: both ``pipe.T[]`` rows are in "All variables".
    id: str
    members: tuple[str, ...]  #: names, by index; one for a lone variable
    text: str  #: what it reads as: the family label, or the label
    #: what it reads as by result-file names: the family label
    #: (``pipe.T[1:2]``), or the name
    file_text: str
    name: str | None  #: the muted name beside ``text``, when they differ
    #: members the selected plot draws as CONTEXT — what its check adds or
    #: takes off
    on_selected: int
    elsewhere: tuple[int, ...]  #: other plots showing any member, 0-based
    #: whether a member is the selected plot's SUBJECT: its check is on and
    #: grey, since a plot cannot lose its subject
    is_subject: bool
    #: the plot (0-based) a member is the subject of, or None: a variable
    #: with a plot of its own has its "+ new plot" grey
    subject_of: int | None


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
    plots: Sequence[ResolvedPlot],
    selected: int | None,
    keep: Callable[[str], bool],
    unit_tests: Collection[str] = (),
    group: str = "component",
    order: str = "file",
    read: str = "both",
) -> list[VarGroup]:
    """``names`` in the drawer's groups, each row's state read off
    ``plots`` and the ``selected`` one (None: no plot); ``keep`` is the
    filter, asked of the name AND the label. Absent from either map, a name
    reads as itself.

    The rows fold as the plots do (the owner's ruling O3,
    :func:`~dstf.discovery.plots.derive_plots`): by RESULT-FILE NAME, never
    by label, and each of the ``unit_tests`` names is a row of its own
    whatever its expression reads — three UnitTests variables whose
    expressions are ``pipe.T[1:3]`` are three plots, and one row over them
    named a plot there is not. A row READS ``labels`` (the plain label),
    with the name muted beside it where they differ.

    ``group`` (the drawer's group menu): ``component`` — tracked, then by
    component, top level, diagnostics; ``flat`` — one group, "All
    variables"; ``tracked`` — "Tracked", then "Other variables" with
    everything else, diagnostics included. Families fold in every one, the
    tracked names apart from the rest: under Flat the tracked
    ``pipe.T[1:2]`` and the untracked rest of the array fold to the same
    key, and one row over both hid which of them the test tracks.
    ``order``: ``file`` keeps a group's rows in first-appearance
    (result-file) order; ``name`` sorts them by what they are shown as —
    ``file_text`` when ``read`` (the names menu) is ``file``, else
    ``text``."""
    tracked_set, diag, apart = set(tracked), set(diagnostics), set(unit_tests)
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
    # the plots indexed once: scanning every plot per name cost 395 ms over
    # 6,093 names and 300 plots
    index: dict[str, list[int]] = {}
    subject_of: dict[str, int] = {}
    for i, plot in enumerate(plots):
        for name in plot.subject:
            subject_of.setdefault(name, i)
        for name in plot.series:
            at = index.setdefault(name, [])
            if not at or at[-1] != i:
                at.append(i)
    # the page's cards can lag the Session's plots by one sync
    chosen = (
        plots[selected] if selected is not None and 0 <= selected < len(plots) else None
    )
    context = set(chosen.context) if chosen is not None else set()
    subject = set(chosen.subject) if chosen is not None else set()
    showing = {n: tuple(index.get(n, ())) for n in kept}
    position = {n: i for i, n in enumerate(kept)}
    out: list[VarGroup] = []
    for rank, title in sorted(by_group, key=lambda g: (g[0], g[1].casefold(), g[1])):
        in_group = by_group[rank, title]
        # Folded apart, then merged back in result-file order (a family's
        # first appearance, `group_families`' own order).
        families = sorted(
            [(n, [n]) for n in in_group if n in apart]
            + group_families(
                [n for n in in_group if n in tracked_set and n not in apart]
            )
            + group_families(
                [n for n in in_group if n not in tracked_set and n not in apart]
            ),
            key=lambda f: min(position[m] for m in f[1]),
        )
        rows = []
        for key, members in families:
            text = family_label(key, members, labels)
            file_text = family_label(key, members)
            lone = members[0] if len(members) == 1 else None
            on = [m for m in members if m in context]
            elsewhere = sorted(
                {i for m in members for i in showing[m] if i != selected}
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
                    is_subject=any(m in subject for m in members),
                    subject_of=next(
                        (subject_of[m] for m in members if m in subject_of), None
                    ),
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
