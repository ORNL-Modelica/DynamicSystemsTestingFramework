"""The Statistics rows both tiers render: the report's Statistics table and
the tool's Statistics card.

One row per statistic the run or its baseline recorded, side by side. A
row is ``changed`` when the criterion its ``+`` would add would fail — the
scalar mode's own verdict over the seed
:func:`~dstf.comparison.seeds.seed_quantity` writes, so the tolerance lives
in one place. Not the report's old "any difference": a CPU time 3% off is
not a change the tool would score.

The rows keep the report's order and sections: translation, simulation,
timing, then any other section in the order they appear (the baseline's
first); keys alphabetical inside a section, ``timing`` its fixed four
first; the top-level scalars (``CPUtime``, ``EventCounter``, folded in from
the diagnostics) sorted in among the Simulation keys — or, with no
``simulation`` section on either side, a Simulation section of their own
at the end.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

from ..backends.common.statistics import SUMMARIES, read_summary, sizes_systems
from ..comparison.modes import ScalarConfig, ScalarMode
from ..comparison.seeds import quantity_kind, seed_quantity
from ..comparison.tree_eval import resolve_scalar_path

#: The section a leaf's quantity lands in when no row holds it (S4).
OTHER = "other scored"
SECTION_TITLES = {
    "translation": "Translation",
    "simulation": "Simulation",
    "timing": "Timing",
    OTHER: "Other scored quantities",
}
_PREFERRED = ("translation", "simulation", "timing")
_TIMING_RANK = {
    k: i
    for i, k in enumerate(("translation_wall", "sim_wall", "other_wall", "total_wall"))
}


@dataclass(frozen=True)
class StatRow:
    section: str  # "translation" | "simulation" | "timing" | other | OTHER
    key: str  # the key inside its section ("nonlinear_count", "CPUtime")
    path: str  # the quantity path a leaf reads ("translation.nonlinear_count")
    label: str  # "Nonlinear count"; a top-level name as is
    kind: Literal["count", "time", "text"]
    run: Any  # a number, or a list / string for a `text` row; None: not recorded
    baseline: Any
    changed: bool
    delta: str  # "0→3" | "+38%" | "+0.12 s" | "" (S6)


def changed(
    path: str,
    kind: Literal["count", "time"],
    run: float | None,
    baseline: float | None,
) -> bool:
    """Would the criterion a ``+`` on this row adds fail? Never when a side
    has no value (S3): there is nothing to compare, and an accept records
    it."""
    if run is None or baseline is None:
        return False
    seed = seed_quantity(path, kind, into="and")
    leaf = seed["children"][0] if "combinator" in seed else seed
    params = {k: v for k, v in leaf.items() if k not in ("metric", "variable")}
    got = ScalarMode(ScalarConfig(**params)).compare(
        np.array([0.0]),
        np.array([float(baseline)]),
        np.array([0.0]),
        np.array([float(run)]),
    )
    return not got.passed


def _is_number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _number(v: object) -> float | None:
    return float(v) if isinstance(v, (int, float)) else None


def _count_text(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


def _rounded(v: float) -> str:
    """3 significant figures KEEPING zeros (``0.800``, ``2.10``): ``0.8``
    beside ``1.12`` read as a coarser number. ``100.`` loses its point, and
    from 999.5 a time is whole seconds (``1234``): ``#.3g`` wrote
    ``1.00e+03``."""
    return f"{v:.0f}" if abs(v) >= 999.5 else f"{v:#.3g}".rstrip(".")


def _value_text(kind: str, value: Any, time: Callable[[float], str]) -> str:
    if value is None:
        return ""
    if kind == "text" or not _is_number(value):
        return _text(value)
    if kind == "time":
        return f"{time(float(value))} s"
    return _count_text(value)


def report_text(kind: str, value: Any) -> str:
    """A row's value as the REPORT prints it, in full: a time with its unit
    (``0.045845 s``, ``1.600425 s``, ``1234567.89 s`` — ``:.15g``, where
    ``:g`` stopped at six figures, ``1.60042 s``), a count whole
    (``1234567``, never ``1.23457e+06``), a list joined; ``""`` for a side
    that recorded none. The report is where a number is read to its last
    digit."""
    return _value_text(kind, value, lambda v: f"{v:.15g}")


def card_text(kind: str, value: Any) -> str:
    """A row's value as the tool's CARD prints it, rounded to be compared
    at a glance: a time to 3 significant figures keeping its zeros
    (``0.800 s``), whole seconds from 999.5 s (``1234 s``); otherwise as
    :func:`report_text`."""
    return _value_text(kind, value, _rounded)


def _delta(kind: str, run: float | None, baseline: float | None) -> str:
    """S6: a count reads ``0→3``, a time ``+38%`` (``+0.12 s`` over a zero
    baseline) — whenever both sides are present and differ, changed or
    not. Under 1% a time keeps one decimal (``+0.3%``), and under 0.05%
    reads ``+<0.1%``: a time that moved never reads ``+0%`` or ``+0.0%``."""
    if run is None or baseline is None or run == baseline:
        return ""
    if kind == "count":
        return f"{_count_text(baseline)}→{_count_text(run)}"
    if baseline == 0:
        return f"{run - baseline:+.2g} s"
    ratio = (run - baseline) / baseline
    if abs(ratio) < 0.0005:
        return f"{'+' if ratio > 0 else '-'}<0.1%"
    return f"{ratio:+.1%}" if abs(ratio) < 0.01 else f"{ratio:+.0%}"


def _text(v: object) -> str:
    """The report's formatting of a list or string: joined; None as empty."""
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    if v is None or v == "":
        return ""
    return str(v)


#: A word a label spells in capitals: `cpu_time_integration` reads "CPU
#: time integration".
_UPPER = {"cpu": "CPU"}


def _label(key: str, top_level: bool) -> str:
    if top_level:
        return key
    text = " ".join(_UPPER.get(word, word) for word in key.split("_"))
    return text[:1].upper() + text[1:]


def _diagnostic_values(diagnostics: Sequence, name: str) -> np.ndarray | None:
    """The run's samples of the diagnostic ``name``, if it recorded one."""
    for entry in diagnostics:
        if getattr(entry, "name", None) == name:
            values = getattr(entry, "values", None)
            return None if values is None else np.asarray(values, dtype=float)
    return None


def _row(
    section: str,
    key: str,
    path: str,
    run: object,
    baseline: object,
    *,
    top_level: bool = False,
    samples: np.ndarray | None = None,
) -> StatRow:
    label = _label(key, top_level)
    if (run is None or _is_number(run)) and (baseline is None or _is_number(baseline)):
        kind = quantity_kind(path, samples)
        r, b = _number(run), _number(baseline)
        return StatRow(
            section,
            key,
            path,
            label,
            kind,
            run,
            baseline,
            changed(path, kind, r, b),
            _delta(kind, r, b),
        )
    # A list or a string: the report's rule — formatted text differs, both
    # sides non-empty. Nothing scores it, so the card never shows it (S1).
    rt, bt = _text(run), _text(baseline)
    return StatRow(
        section,
        key,
        path,
        label,
        "text",
        run,
        baseline,
        bool(rt and bt and rt != bt),
        "",
    )


def _section(stats: Mapping, name: str) -> Mapping:
    got = stats.get(name)
    return got if isinstance(got, Mapping) else {}


def _summary_or_none(category: Mapping, key: str) -> object:
    if key in category:
        return category[key]
    return read_summary(dict(category), key)


def _summary_keys(*categories: Mapping) -> set[str]:
    """``<list>_count`` / ``_total`` / ``_max`` of every system-size list
    either side holds: the zero rule surfaces. A baseline written before
    `summaries` holds ``nonlinear: []`` and no count; beside a run that
    recorded none either, no row said the count stays 0, and no `+` could
    require it."""
    return {
        f"{key}_{field}"
        for category in categories
        for key, value in category.items()
        if sizes_systems(value)
        for field in SUMMARIES
    }


def statistics_rows(
    run: Mapping | None,
    baseline: Mapping | None,
    *,
    run_diagnostics: Sequence = (),
    baseline_diagnostics: Sequence = (),
    extra_paths: Iterable[str] = (),
) -> list[StatRow]:
    """Every statistic the run or its baseline recorded, in the report's
    order (the module docstring), then a row per ``extra_paths`` entry no
    row holds (S4: a leaf's quantity always has a home). A system-size
    list on either side gives its three summary rows on both; a summary
    key a side lacks reads through :func:`read_summary` (a baseline's empty
    ``nonlinear`` gives ``nonlinear_count`` 0). Never writes into its
    inputs."""
    run, baseline = run or {}, baseline or {}
    names: list[str] = []
    for src in (baseline, run):
        for k, v in src.items():
            if isinstance(v, Mapping) and k not in names:
                names.append(k)
    top = sorted(
        {
            k
            for src in (baseline, run)
            for k, v in src.items()
            if not isinstance(v, Mapping)
        }
    )
    ordered = [n for n in _PREFERRED if n in names]
    ordered += [n for n in names if n not in _PREFERRED]

    def top_row(key: str) -> StatRow:
        return _row(
            "simulation",
            key,
            key,
            run.get(key),
            baseline.get(key),
            top_level=True,
            samples=_diagnostic_values(run_diagnostics, key),
        )

    rows: list[StatRow] = []
    for name in ordered:
        r_cat, b_cat = _section(run, name), _section(baseline, name)
        keys = {*r_cat, *b_cat, *_summary_keys(r_cat, b_cat)}
        keyed: list[tuple[str, str]] = [(k, f"{name}.{k}") for k in keys]
        if name == "simulation":
            keyed += [(k, k) for k in top if k not in r_cat and k not in b_cat]
        keyed.sort()
        if name == "timing":
            keyed.sort(key=lambda kp: _TIMING_RANK.get(kp[0], len(_TIMING_RANK)))
        for key, path in keyed:
            if path == key:
                rows.append(top_row(key))
                continue
            r = _summary_or_none(r_cat, key)
            b = _summary_or_none(b_cat, key)
            rows.append(_row(name, key, path, r, b))
    if "simulation" not in names:
        rows += [top_row(key) for key in top]

    # A text row holds no leaf: the card never shows one (S1). `X.final`
    # is the row `X` only for a top-level `X` (S2).
    held = {row.path for row in rows if row.kind != "text"}
    for path in dict.fromkeys(extra_paths):
        name, _, field = path.rpartition(".")
        if path in held or (field == "final" and "." not in name and name in held):
            continue
        r = resolve_scalar_path(path, dict(run), list(run_diagnostics))
        b = resolve_scalar_path(path, dict(baseline), list(baseline_diagnostics))
        samples = _diagnostic_values(run_diagnostics, name or path)
        rows.append(_row(OTHER, path, path, r, b, top_level=True, samples=samples))
        held.add(path)
    return rows
