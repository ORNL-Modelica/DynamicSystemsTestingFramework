"""The zero-count rule: a system-size list's summaries, written and read in
one place."""

from __future__ import annotations

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
