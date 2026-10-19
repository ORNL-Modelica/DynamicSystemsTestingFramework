"""The zero-count rule: a system-size list's summaries, written and read in
one place."""

from __future__ import annotations

from collections.abc import Iterable

SUMMARIES = ("count", "total", "max")


def summaries(key: str, values: list[int]) -> dict[str, int]:
    """`<key>_count/_total/_max` of a system-size list — 0, 0, 0 for an empty
    one. The parser writes these ALWAYS: code that writes never leaves a
    field out (a reader would have to guess what its absence means)."""
    return {
        f"{key}_count": len(values),
        f"{key}_total": sum(values),
        f"{key}_max": max(values, default=0),
    }


def sizes_systems(value: object) -> bool:
    """Is ``value`` a system-size list — the sizes the parser summarizes
    (``nonlinear: [3, 5, 1]``, or ``[]``)? A list of whole numbers; a list
    of names (``state_names``) is not one."""
    return isinstance(value, list) and all(
        isinstance(v, int) and not isinstance(v, bool) for v in value
    )


def read_summary(category: dict, key: str) -> int | None:
    """A summary a baseline written before `summaries` lacks: 0 where
    `key` is `<list>_count|_total|_max` and `<list>` is present AND EMPTY in
    the same category. Anything else stays missing — a stats path absent
    from a side hard-fails its leaf (tree_eval: a path that silently scored
    0 would be a green test measuring nothing), and only an empty list
    says what its summary is."""
    base, _, field = key.rpartition("_")
    if field not in SUMMARIES or key in category:
        return None
    return 0 if category.get(base) == [] else None


def diagnostic_final(entries: Iterable[object], name: str) -> float | None:
    """The final value of the listed name ``name`` among ``entries``: a
    run's recorded diagnostics (trajectories: their last sample) or a
    baseline's stored summaries (their ``final``). None where none is
    named so, or it holds no value. What the event-count warning, the
    dashboard's CPU time and the Statistics rows read — the one copy a
    result once carried in its statistics is gone (D162)."""
    for entry in entries:
        if getattr(entry, "name", None) != name:
            continue
        if hasattr(entry, "final"):
            final = entry.final
            return None if final is None else float(final)
        values = getattr(entry, "values", None)
        if values is None or len(values) == 0:
            return None
        return float(values[-1])
    return None
