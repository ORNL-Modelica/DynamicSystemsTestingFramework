"""Array families (DESIGN.md "Array families on a plot"; brief §3.9).

Indexed names fold by their LAST integer index: ``pipe.T[3]`` and
``pipe.medium[3].T`` each belong to a family; ``x[2,3]`` does not. One
function, so the figure's legend, the Variables drawer's folded row and the
folded criteria row agree on what a family is.

A family is a DISPLAY fold, so it folds over what a person reads — the
label, which for a UnitTests variable is the expression the model wrote.
Callers pass a ``{name: label}`` mapping; the members stay names, because
that is what everything else is keyed by."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

_LAST_INDEX = re.compile(r"\[(\d+)\](?!.*\[\d+\])")
#: A Modelica variable name as a RESULT FILE spells one: identifiers, dots
#: and index brackets, optionally negated (Dymola stores a negated alias as
#: ``-x[1]``). A quoted identifier (``a.'b c'[2]``) is collapsed to one
#: letter first, so its spaces and punctuation do not have to be allowed
#: everywhere.
_PLAIN_NAME = re.compile(r"^-?[A-Za-z_][A-Za-z_0-9.\[\]]*$")
_QUOTED_ID = re.compile(r"'[^']*'")
#: ``der(...)``, and only that: a derivative IS an element of the array it
#: differentiates. Any OTHER call is a reduction over the array — ``sum(...)``
#: of two different slices is two quantities, not two elements of one.
_DER_CALL = re.compile(r"^der\((.*)\)$")

#: Four stops per hue, light to dark (Material 200 / 400 / 700 / 900). The
#: first is DESIGN.md's accent ramp; a second family on a plot takes the
#: next hue, and the list cycles.
HUES: tuple[tuple[str, str, str, str], ...] = (
    ("#90caf9", "#42a5f5", "#1976d2", "#0d47a1"),
    ("#a5d6a7", "#66bb6a", "#388e3c", "#1b5e20"),
    ("#ce93d8", "#ab47bc", "#7b1fa2", "#4a148c"),
)


def _names_an_element(text: str) -> bool:
    """Does ``text`` spell a variable, rather than compute one?

    A plain name, negated or not, with any number of ``der(...)`` around
    it. Everything else — ``sum(core.Qs[6, :])``, ``cat(1, a, b)[2]``,
    ``abs(x[1])`` — is an expression: its value is not an element of the
    array whose index it happens to end with.
    """
    while (inner := _DER_CALL.match(text)) is not None:
        text = inner.group(1)
    return bool(_PLAIN_NAME.match(_QUOTED_ID.sub("q", text)))


def family_of(text: str) -> tuple[str, int] | None:
    """``(family key, index)`` — the text with its last ``[integer]`` emptied
    — or ``None`` when it names no element of an array.

    Only a PLAIN indexed name has a family (``pipe.T[3]``,
    ``der(pipe.T[3])``, ``pipe.medium[3].T``, ``a.'b c'[2]``, ``-x[1]``).
    An expression the model wrote — ``cat(1, a, b)[2]``,
    ``sum(core.Qs[6, :])``, ``sum(pipe.Qs[1])`` — is a label, not an
    element of an array: folding two of those into ``sum(pipe.Qs[1:2])``
    gives two quantities one legend entry, one colour ramp and a label
    naming a third thing neither of them is.

    Over NAMES — which is every caller's input until a label differs from
    one — this is exactly what it has always been; only expressions moved.
    """
    if not _names_an_element(text):
        return None
    m = _LAST_INDEX.search(text)
    if m is None:
        return None
    return text[: m.start()] + "[]" + text[m.end() :], int(m.group(1))


def _labelled(name: str, labels: Mapping[str, str] | None) -> str:
    return (labels or {}).get(name, name)


def group_families(
    names: Sequence[str],
    labels: Mapping[str, str] | None = None,
) -> list[tuple[str, list[str]]]:
    """``(key, members)`` in order of first appearance; members by index.

    The fold is over each name's LABEL (``labels`` maps name → label;
    without it a name is its own label), and the members are the NAMES. A
    label with no family leaves its name its own group under its own name —
    never under the label, or two variables sharing one expression would
    become one group.
    """
    groups: dict[str, list[tuple[int, str]]] = {}
    for name in names:
        fam = family_of(_labelled(name, labels))
        key, index = fam if fam is not None else (name, 0)
        groups.setdefault(key, []).append((index, name))
    return [(key, [n for _, n in sorted(members)]) for key, members in groups.items()]


def slice_text(indices: Sequence[int]) -> str:
    """Modelica slice syntax for an arithmetic run (``1:5:16``, ``1:4``), a
    comma list otherwise."""
    idx = sorted(indices)
    if len(idx) == 1:
        return str(idx[0])
    step = idx[1] - idx[0]
    if step > 0 and all(b - a == step for a, b in zip(idx, idx[1:])):
        return f"{idx[0]}:{idx[-1]}" if step == 1 else f"{idx[0]}:{step}:{idx[-1]}"
    return ",".join(str(i) for i in idx)


def family_label(
    key: str,
    members: Sequence[str],
    labels: Mapping[str, str] | None = None,
) -> str:
    """``pipe.T[1:5:16]`` for a family of several; the one member's LABEL for
    a family of one (``labels`` as in :func:`group_families`)."""
    if len(members) == 1:
        return _labelled(members[0], labels)
    indices = [
        fam[1] for m in members if (fam := family_of(_labelled(m, labels))) is not None
    ]
    return key.replace("[]", f"[{slice_text(indices)}]", 1) if "[]" in key else key


def _rgb(hex_color: str) -> tuple[int, int, int]:
    return int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)


def ramp(hue: Sequence[str], n: int) -> list[str]:
    """``n`` colours along the hue's four stops, light to dark; one colour is
    the third stop (the hue's name shade)."""
    if n == 1:
        return [hue[2]]
    out = []
    for i in range(n):
        pos = i * (len(hue) - 1) / (n - 1)
        lo = min(int(pos), len(hue) - 2)
        frac = pos - lo
        a, b = _rgb(hue[lo]), _rgb(hue[lo + 1])
        out.append(
            "#" + "".join(f"{round(x + (y - x) * frac):02x}" for x, y in zip(a, b))
        )
    return out
