"""The pending drawer's preview (the owner's ruling Q6 A): a test's entry
before and after its staged edits, as ``test_spec.json`` will hold it —
``json.dumps(entry, indent=2)``, the indentation Save writes — diffed line
by line with ``difflib``, and each line's variable names read by their
label with the result-file name muted beside it. Pure: stdlib only."""

from __future__ import annotations

import difflib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from ..backends.types import VariableText

Kind = Literal["same", "add", "del", "gap"]


@dataclass(frozen=True)
class Line:
    kind: Kind
    text: str


#: What stands between two groups of changes.
GAP = Line("gap", "…")


def _dumped(entry: dict | None) -> list[str]:
    return json.dumps(entry, indent=2).splitlines() if entry is not None else []


def entry_lines(
    before: dict | None, after: dict | None, context: int = 3
) -> list[Line]:
    """The changed lines of ``before`` → ``after`` (None: no entry) with
    ``context`` equal lines around each group of changes, a :data:`GAP`
    between two groups, a replaced run as its removed lines then its added
    ones; ``[]`` when the two are equal. ``autojunk`` is off: in a long
    tree ``}`` and ``"metric": "nrmse",`` are each more than 1% of the
    lines, and junking them turned two changed tolerances into some two
    hundred replaced lines."""
    a, b = _dumped(before), _dumped(after)
    matcher = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    out: list[Line] = []
    for n, group in enumerate(matcher.get_grouped_opcodes(context)):
        if n:
            out.append(GAP)
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                out += [Line("same", t) for t in a[i1:i2]]
                continue
            out += [Line("del", t) for t in a[i1:i2]]
            out += [Line("add", t) for t in b[j1:j2]]
    return out


#: A JSON string token, escapes included.
_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')


def spans(text: str, texts: Mapping[str, VariableText]) -> list[tuple[str, bool]]:
    """``text`` as ``(fragment, muted)`` pieces: each JSON string that names
    a variable of ``texts`` whose label is not its name reads as the label,
    then the name muted, its quotes dropped; everything else is kept
    verbatim, so a line with no such name joins back to itself."""
    out: list[tuple[str, bool]] = []
    pos = 0
    for m in _STRING.finditer(text):
        name = json.loads(m.group())
        vt = texts.get(name)
        if vt is None or vt.label == name:
            continue
        if m.start() > pos:
            out.append((text[pos : m.start()], False))
        out += [(vt.label, False), (" " + name, True)]
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], False))
    return out
