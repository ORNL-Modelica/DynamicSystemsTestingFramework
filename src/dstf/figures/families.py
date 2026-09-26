"""Array families (DESIGN.md "Array families on a plot"; brief §3.9).

Indexed names fold by their LAST integer index: ``pipe.T[3]`` and
``pipe.medium[3].T`` each belong to a family; ``x[2,3]`` does not.
``massPorts[4, 1].C[3]`` does, by its last index. One function, so the
figure's legend, the Variables drawer's folded row and the folded criteria
row agree on what a family is.

A family is a DISPLAY fold, so it folds over what a person reads — the
label, which for a UnitTests variable is the expression the model wrote.
Callers pass a ``{name: label}`` mapping; the members stay names, because
that is what everything else is keyed by.

Two rules, and the caller says which by what it passes. A RESULT-FILE NAME
(the label a variable has when it has no expression) folds by its last
single-integer index and nothing else — result files spell names in ways no
whitelist anticipates (``θ[1]``, ``pipe₊T[1]`` from Julia/MTK, ``T-wall[1]``,
``$DER.x[1]``), and every one of them was a family before expressions
existed. An EXPRESSION the model wrote is held to the strict shape below:
it is text a person typed, and ``sum(pipe.Qs[1])`` folded with
``sum(pipe.Qs[2])`` gives two quantities one legend entry, one colour ramp
and a label naming a third thing neither of them is."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

_LAST_INDEX = re.compile(r"\[(\d+)\](?!.*\[\d+\])")
#: A multi-dimensional index (``x[2,3]``) is not one array position.
_MULTI_INDEX = re.compile(r"\[[^\]]*,[^\]]*\]")
#: A Modelica variable name as a person TYPES one: identifiers (letters in
#: any script, so ``θ[1]`` is one), dots and index brackets. A quoted
#: identifier (``a.'b c'[2]``) is collapsed to one letter first, so its
#: spaces and punctuation do not have to be allowed everywhere.
_PLAIN_NAME = re.compile(r"^[^\W\d][\w.\[\]]*$")
#: Modelica's Q-IDENT: a backslash escapes the next character, so
#: ``'it\'s'`` is one identifier, not ``'it\'`` and a stray ``s'``.
_QUOTED_ID = re.compile(r"'(?:[^'\\]|\\.)*'")
#: An index of integers only, one or several (`[3]`, `[4, 1]`): a position
#: in an array, however many dimensions. Collapsed before `_PLAIN_NAME`, so
#: an expression that indexes a port two-dimensionally is still a plain name
#: (`massPorts[4, 1].C[1]`). A slice (`[6, :]`) is not one: it stays and
#: refuses the name. No space just inside the brackets, as `_LAST_INDEX`
#: allows none: otherwise `x[1].C[ 2 ]` folded on its FIRST index.
_INT_INDEX = re.compile(r"\[\d+(?:\s*,\s*\d+)*\]")
#: ``der(...)``, and only that: a derivative IS an element of the array it
#: differentiates. Any OTHER call is a reduction over the array — ``sum(...)``
#: of two different slices is two quantities, not two elements of one.
#:
#: Indexed AFTER the call too (``der(x)[1]``): that is a plain indexed name
#: of the derivative of an array, so ``der(x)[1]`` and ``der(x)[2]`` are two
#: elements of one array and fold. ``der(x[1], 2)`` does NOT — a second
#: derivative is a call of two arguments, and the index inside it belongs to
#: the argument, not to what the call returns; it stays a family of one.
_DER_CALL = re.compile(r"^der\((.*)\)(?:\[\d+\])*$")

#: Four stops per hue, light to dark (Material 200 / 400 / 700 / 900). The
#: first is DESIGN.md's accent ramp; a second family on a plot takes the
#: next hue, and the list cycles.
HUES: tuple[tuple[str, str, str, str], ...] = (
    ("#90caf9", "#42a5f5", "#1976d2", "#0d47a1"),
    ("#a5d6a7", "#66bb6a", "#388e3c", "#1b5e20"),
    ("#ce93d8", "#ab47bc", "#7b1fa2", "#4a148c"),
)


def mask_quoted(text: str) -> str:
    """``text`` with every character inside a quoted identifier replaced by
    a letter, quotes kept, and the SAME length — so a position found in the
    mask slices ``text``. A quoted identifier is one name: the ``[5]`` of
    ``a.'x[5]'`` is no index and the dot of ``'a.b'.c`` no separator, and
    a search that reads the mask cannot mistake them for either."""
    return _QUOTED_ID.sub(lambda m: "'" + "q" * (len(m[0]) - 2) + "'", text)


def _names_an_element(text: str) -> bool:
    """Does ``text`` spell a variable, rather than compute one?

    A plain name with any number of ``der(...)`` wrappers and leading
    minus signs around it, in any order — ``-der(x[2])`` and
    ``der(-x[2])`` are the same quantity and must answer the same. A
    ``der(...)`` may itself be indexed (``der(x)[1]``, the derivative of an
    array taken element by element). Everything else —
    ``sum(core.Qs[6, :])``, ``cat(1, a, b)[2]``, ``abs(x[1])``,
    ``der(x[1], 2)`` — is an expression: its value is not an element of the
    array whose index it happens to end with.
    """
    while True:
        if text.startswith("-"):
            text = text[1:]
            continue
        inner = _DER_CALL.match(text)
        if inner is None:
            break
        text = inner.group(1)
    return bool(_PLAIN_NAME.match(_INT_INDEX.sub("[0]", _QUOTED_ID.sub("q", text))))


def family_of(text: str, *, expression: bool = False) -> tuple[str, int] | None:
    """``(family key, index)`` — the text with its last ``[integer]`` emptied
    — or ``None`` when it names no element of an array.

    ``expression=False`` (the default) reads ``text`` as a RESULT-FILE
    NAME: anything with a single-integer last index is a family, and a
    multi-dimensional index (``x[2,3]``) is not. ``massPorts[4, 1].C[3]``
    is, by its last index: a multi-dimensional index refuses a family only
    AFTER the last single one. This is the only rule a name ever meets — a
    result file spells names the caller does not get to whitelist.

    ``expression=True`` reads it as a label a person typed, and only a
    plain indexed name qualifies (``pipe.T[3]``, ``der(pipe.T[3])``,
    ``der(pipe.T)[3]``, ``pipe.medium[3].T``, ``a.'b c'[2]``, ``-x[1]``).
    ``cat(1, a, b)[2]``, ``sum(core.Qs[6, :])``, ``sum(pipe.Qs[1])`` and
    ``der(x[1], 2)`` are computations, not elements of an array — the
    second derivative's index belongs to the ARGUMENT of a two-argument
    call, and nothing here can say the call returns an element of it.
    """
    if expression and not _names_an_element(text):
        return None
    masked = mask_quoted(text)  # `a.'x[5]'` has no index; the key is `text`'s
    m = _LAST_INDEX.search(masked)
    if m is None:
        return None
    # Multi-dimensional AFTER the last single index (`a[1].b[2,3]`): the
    # element's own position is two-dimensional, so it is no member of an
    # `a` array. BEFORE it (`massPorts[4, 1].C[3]`) it is where the array
    # sits, and the element is C's 3rd (3c ruling).
    if _MULTI_INDEX.search(masked, m.end()):
        return None
    return text[: m.start()] + "[]" + text[m.end() :], int(m.group(1))


def _labelled(name: str, labels: Mapping[str, str] | None) -> str:
    return (labels or {}).get(name, name)


def _family_of_member(name: str, labels: Mapping[str, str] | None):
    """The family of what ``name`` READS as, under the rule its text earns:
    a text that IS the name is a result-file name, anything else is a label
    the model or the page composed."""
    text = _labelled(name, labels)
    return family_of(text, expression=text != name)


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
        fam = _family_of_member(name, labels)
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
        fam[1] for m in members if (fam := _family_of_member(m, labels)) is not None
    ]
    # the fold marker, never a `[]` inside a quoted identifier (`a.'b[]'.x[]`)
    at = mask_quoted(key).find("[]")
    if at == -1:
        return key
    return key[:at] + f"[{slice_text(indices)}]" + key[at + 2 :]


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
