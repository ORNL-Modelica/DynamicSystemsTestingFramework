"""The add form's variable picker as data (the plan's K2): every name a
criterion may score, in two sections — the plot's own series, then every
other name of the result file, a listed one (CPUtime) among them: every
name is a variable — filtered on what a row reads as OR its result-file
name, at most `CAP` rows. The shell draws them (`shell/picker.py`) and knows none of the
rules, as the Variables drawer draws `variable_rows`."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from ..backends.types import VariableText

#: The drawer's cap (`variables_drawer.ROWS`): a result of 76,992 names
#: drew 76,992 rows otherwise.
CAP = 200
ON_PLOT, OTHER = "On this plot", "Other variables"
SECTIONS = (ON_PLOT, OTHER)


@dataclass(frozen=True)
class PickRow:
    name: str  #: the result-file name — what a leaf is written against
    text: str  #: what it reads as in the test's scope, else the name
    section: str
    #: the plain label (`VariableText.label`): what the row shows, the
    #: muted name beside it
    label: str


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
    texts: Mapping[str, VariableText],
    keep: Callable[[str], bool],
    cap: int = CAP,
) -> Picked:
    """The sections in the order On this plot / Other variables, each name
    once (in the first section that has it); an empty section is absent.
    ``names`` is every name of the result file (`Session.variable_names`);
    ``texts`` the test's (`Session.variable_texts`), a name it lacks
    reading as itself.
    ``keep`` is `needle.matcher`'s closure: its ``TimeoutError`` reaches
    the caller, which says it."""
    first = list(dict.fromkeys(on_plot))
    plotted = set(first)
    groups = (
        (ON_PLOT, first),
        (OTHER, [n for n in dict.fromkeys(names) if n not in plotted]),
    )
    out: list[tuple[str, list[PickRow]]] = []
    total = shown = 0
    for title, members in groups:
        rows = []
        for name in members:
            entry = texts.get(name)
            text = entry.text if entry is not None else name
            if not (keep(text) or (text != name and keep(name))):
                continue
            total += 1
            if shown < cap:
                label = entry.label if entry is not None else name
                rows.append(PickRow(name, text, title, label))
                shown += 1
        if rows:
            out.append((title, rows))
    return Picked(out, shown, total)
