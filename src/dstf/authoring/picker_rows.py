"""The add form's variable picker as data (the plan's K2): every name a
criterion may score, in three sections — the plot's own series, every
other variable of the result file, the diagnostics the run recorded —
filtered on what a row reads as OR its result-file name, at most `CAP`
rows. The shell draws them (`shell/picker.py`) and knows none of the
rules, as the Variables drawer draws `variable_rows`."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

#: The drawer's cap (`variables_drawer.ROWS`): a result of 76,992 names
#: drew 76,992 rows otherwise.
CAP = 200
ON_PLOT, OTHER, DIAGNOSTICS = "On this plot", "Other variables", "Diagnostics"
SECTIONS = (ON_PLOT, OTHER, DIAGNOSTICS)


@dataclass(frozen=True)
class PickRow:
    name: str  #: the result-file name — what a leaf is written against
    text: str  #: what it reads as (`Session.labels`), else the name
    section: str


@dataclass(frozen=True)
class Picked:
    sections: list[tuple[str, list[PickRow]]]
    shown: int
    total: int

    @property
    def capped(self) -> str | None:
        """The line under a capped list, else None."""
        if self.shown >= self.total:
            return None
        return f"Showing {self.shown} of {self.total:,} — narrow the search"


def picker_rows(
    *,
    on_plot: Sequence[str],
    names: Sequence[str],
    diagnostics: Sequence[str],
    labels: Mapping[str, str],
    keep: Callable[[str], bool],
    cap: int = CAP,
) -> Picked:
    """The sections in the order On this plot / Other variables /
    Diagnostics, each name once (in the first section that has it); an
    empty section is absent. ``names`` is every name of the result file
    (`Session.variable_names`); ``diagnostics`` what the run RECORDED as
    one — each is listed there and only there, whether or not the file
    listing names it too (a Dymola file holds `CPUtime` as a variable).
    ``keep`` is `needle.matcher`'s closure: its ``TimeoutError`` reaches
    the caller, which says it."""
    first = list(dict.fromkeys(on_plot))
    plotted = set(first)
    diag = [d for d in dict.fromkeys(diagnostics) if d not in plotted]
    taken = plotted | set(diag)
    groups = (
        (ON_PLOT, first),
        (OTHER, [n for n in dict.fromkeys(names) if n not in taken]),
        (DIAGNOSTICS, diag),
    )
    out: list[tuple[str, list[PickRow]]] = []
    total = shown = 0
    for title, members in groups:
        rows = []
        for name in members:
            text = labels.get(name, name)
            if not (keep(text) or (text != name and keep(name))):
                continue
            total += 1
            if shown < cap:
                rows.append(PickRow(name, text, title))
                shown += 1
        if rows:
            out.append((title, rows))
    return Picked(out, shown, total)
