"""Array families (DESIGN.md "Array families on a plot"; brief §3.9).

Indexed names fold by their LAST integer index: ``pipe.T[3]`` and
``pipe.medium[3].T`` each belong to a family; ``x[2,3]`` does not. One
function, so the figure's legend, the Variables drawer's folded row and the
folded criteria row agree on what a family is."""

from __future__ import annotations

import re
from collections.abc import Sequence

_LAST_INDEX = re.compile(r"\[(\d+)\](?!.*\[\d+\])")

#: Four stops per hue, light to dark (Material 200 / 400 / 700 / 900). The
#: first is DESIGN.md's accent ramp; a second family on a plot takes the
#: next hue, and the list cycles.
HUES: tuple[tuple[str, str, str, str], ...] = (
    ("#90caf9", "#42a5f5", "#1976d2", "#0d47a1"),
    ("#a5d6a7", "#66bb6a", "#388e3c", "#1b5e20"),
    ("#ce93d8", "#ab47bc", "#7b1fa2", "#4a148c"),
)


def family_of(name: str) -> tuple[str, int] | None:
    """``(family key, index)`` — the name with its last ``[integer]`` emptied
    — or ``None`` for a name with no single-integer index."""
    if re.search(r"\[[^\]]*,[^\]]*\]", name):
        return None
    m = _LAST_INDEX.search(name)
    if m is None:
        return None
    return name[: m.start()] + "[]" + name[m.end() :], int(m.group(1))


def group_families(names: Sequence[str]) -> list[tuple[str, list[str]]]:
    """``(key, members)`` in order of first appearance; members by index. A
    name with no family is its own group under its own name."""
    groups: dict[str, list[tuple[int, str]]] = {}
    for name in names:
        fam = family_of(name)
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


def family_label(key: str, members: Sequence[str]) -> str:
    """``pipe.T[1:5:16]`` for a family of several; the name itself for one."""
    if len(members) == 1:
        return members[0]
    indices = [fam[1] for m in members if (fam := family_of(m)) is not None]
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
