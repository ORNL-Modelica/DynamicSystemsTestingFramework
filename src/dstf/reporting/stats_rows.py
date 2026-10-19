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
first; the listed names' finals (``CPUtime``, ``EventCounter``, a test's
own ``diagnostic_variables``: the run's diagnostics and the baseline's
summaries) sorted in among the Simulation keys — or, with no
``simulation`` section on either side, a Simulation section of their own
at the end.

A number is shown at the precision it was recorded with (owner,
2026-10-18): a listed name's final at 7 significant digits from a float32
run or a single-precision baseline, 15 otherwise (`StatRow.digits`); a
statistic of the log at 15. Storage and scoring are unchanged.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

from ..backends.common.statistics import (
    SUMMARIES,
    diagnostic_final,
    read_summary,
    sizes_systems,
)
from ..comparison.modes import ScalarConfig, ScalarMode
from ..comparison.seeds import (
    COUNTS,
    TIMES,
    SeedData,
    quantity_kind,
    seed_quantity,
    seed_scalar,
)
from ..comparison.tree_eval import resolve_scalar_path
from ..storage.baseline_codec import DOUBLE_SHOWN, recorded_digits

Kind = Literal["count", "time", "value"]

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
    #: a `value` is a listed name that is neither a time nor a count (a
    #: height): no unit, a percentage delta
    kind: Literal["count", "time", "value", "text"]
    run: Any  # a number, or a list / string for a `text` row; None: not recorded
    baseline: Any
    changed: bool
    delta: str  # "0→3" | "+38%" | "+0.12 s" | "" (S6)
    #: the significant digits each side was recorded with, (run, baseline):
    #: what `report_text` and `card_text` show it at
    digits: tuple[int, int] = (DOUBLE_SHOWN, DOUBLE_SHOWN)


def changed(
    path: str,
    kind: Kind,
    run: float | None,
    baseline: float | None,
) -> bool:
    """Would the criterion a ``+`` on this row adds fail? Never when a side
    has no value (S3): there is nothing to compare, and an accept records
    it. A `value` takes the ordinary scalar seed's tolerance (1% of the
    baseline), a count and a time their known ones."""
    if run is None or baseline is None:
        return False
    if kind == "value":
        one = np.array([0.0])
        leaf = seed_scalar(SeedData(ref=(one, np.array([float(baseline)])), act=None))
    else:
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


def _shown(value: float, digits: int) -> float:
    """``value`` at the ``digits`` it was recorded with (`StatRow.digits`):
    a float32's CPU time ``0.04324999824166298`` is ``0.04325``."""
    return float(f"{value:.{digits}g}")


def _value_text(
    kind: str, value: Any, number: Callable[[float], str], digits: int
) -> str:
    if value is None:
        return ""
    if kind == "text" or not _is_number(value):
        return _text(value)
    if kind == "time":
        return f"{number(_shown(float(value), digits))} s"
    if kind == "value":
        return number(_shown(float(value), digits))
    return _count_text(value)


def report_text(kind: str, value: Any, digits: int = DOUBLE_SHOWN) -> str:
    """A row's value as the REPORT prints it, in full — to the ``digits``
    it was recorded with (`StatRow.digits`): a time with its unit
    (``0.045845 s``, ``1.600425 s``, ``1234567.89 s`` — where ``:g``
    stopped at six figures, ``1.60042 s``), a `value` without one, a count
    whole (``1234567``, never ``1.23457e+06``), a list joined; ``""`` for a
    side that recorded none. The report is where a number is read to its
    last digit."""
    return _value_text(kind, value, lambda v: f"{v:.15g}", digits)


def card_text(kind: str, value: Any, digits: int = DOUBLE_SHOWN) -> str:
    """A row's value as the tool's CARD prints it, rounded to be compared
    at a glance: a time to 3 significant figures keeping its zeros
    (``0.800 s``), whole seconds from 999.5 s (``1234 s``), a `value` the
    same without the unit — each from the number at the ``digits`` it was
    recorded with; otherwise as :func:`report_text`."""
    return _value_text(kind, value, _rounded, digits)


def _delta(kind: str, run: float | None, baseline: float | None) -> str:
    """S6: a count reads ``0→3``, a time or a `value` ``+38%`` (a time
    ``+0.12 s`` over a zero baseline, a value ``+0.12``) — whenever both
    sides are present and differ, changed or not. The percentage is of the
    baseline's magnitude: over a negative baseline a value that went down
    reads down. Under 1% it keeps one decimal (``+0.3%``), and under 0.05%
    reads ``+<0.1%``: a number that moved never reads ``+0%`` or ``+0.0%``;
    likewise one that is not zero never reads ``-100%`` — only a fall to
    zero does — but ``-<100%``."""
    if run is None or baseline is None or run == baseline:
        return ""
    if kind == "count":
        return f"{_count_text(baseline)}→{_count_text(run)}"
    if baseline == 0:
        return f"{run - baseline:+.2g}" + (" s" if kind == "time" else "")
    ratio = (run - baseline) / abs(baseline)
    if abs(ratio) < 0.0005:
        return f"{'+' if ratio > 0 else '-'}<0.1%"
    if run != 0 and -1 < ratio <= -0.995:
        return "-<100%"
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
            return None if values is None else np.asarray(values)
    return None


def listed_names(
    run_diagnostics: Iterable[Any], baseline_diagnostics: Iterable[Any]
) -> set[str]:
    """The listed names the rows are built from: every name the run
    recorded or the baseline summarizes — a row each (and its ``+`` is
    that name's number)."""
    return {str(d.name) for d in (*run_diagnostics, *baseline_diagnostics) if d.name}


def listed_kind(name: str) -> Kind:
    """A listed name's row kind, by its name alone: CPUtime a time,
    EventCounter a count, any other a `value` — never guessed from its
    values (a height is no time)."""
    return "time" if name in TIMES else "count" if name in COUNTS else "value"


def _row(
    section: str,
    key: str,
    path: str,
    run: object,
    baseline: object,
    *,
    top_level: bool = False,
    kind: Kind | None = None,
    digits: tuple[int, int] = (DOUBLE_SHOWN, DOUBLE_SHOWN),
) -> StatRow:
    label = _label(key, top_level)
    if (run is None or _is_number(run)) and (baseline is None or _is_number(baseline)):
        kind = kind or quantity_kind(path)
        r, b = _number(run), _number(baseline)
        # The delta reads the numbers as they are shown; whether the row
        # changed is the scorer's verdict, over the numbers it scores.
        shown_r = None if r is None else _shown(r, digits[0])
        shown_b = None if b is None else _shown(b, digits[1])
        return StatRow(
            section,
            key,
            path,
            label,
            kind,
            run,
            baseline,
            changed(path, kind, r, b),
            _delta(kind, shown_r, shown_b),
            digits,
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
    baseline_digits: int = DOUBLE_SHOWN,
) -> list[StatRow]:
    """Every statistic the run or its baseline recorded, and the final of
    every listed name either holds (the run's diagnostics, the baseline's
    summaries), in the report's order (the module docstring), then a row
    per ``extra_paths`` entry no row holds (S4: a leaf's quantity always
    has a home). A system-size list on either side gives its three summary
    rows on both; a summary key a side lacks reads through
    :func:`read_summary` (a baseline's empty ``nonlinear`` gives
    ``nonlinear_count`` 0). ``baseline_digits`` is the precision the
    baseline's file says it was recorded with
    (`~dstf.storage.baseline_codec.shown_digits`); a run's is its array's.
    Never writes into its inputs."""
    run, baseline = run or {}, baseline or {}
    names: list[str] = []
    for src in (baseline, run):
        for k, v in src.items():
            if isinstance(v, Mapping) and k not in names:
                names.append(k)
    listed = listed_names(run_diagnostics, baseline_diagnostics)
    # A top-level number that is no listed name keeps its row; one named
    # like a listed name is the copy its final once had, never read.
    top = sorted(
        {
            k
            for src in (baseline, run)
            for k, v in src.items()
            if not isinstance(v, Mapping) and k not in listed
        }
        | listed
    )
    ordered = [n for n in _PREFERRED if n in names]
    ordered += [n for n in names if n not in _PREFERRED]

    def top_row(key: str) -> StatRow:
        if key not in listed:
            return _row(
                "simulation", key, key, run.get(key), baseline.get(key), top_level=True
            )
        samples = _diagnostic_values(run_diagnostics, key)
        return _row(
            "simulation",
            key,
            key,
            diagnostic_final(run_diagnostics, key),
            diagnostic_final(baseline_diagnostics, key),
            top_level=True,
            kind=listed_kind(key),
            digits=(
                recorded_digits(samples) if samples is not None else DOUBLE_SHOWN,
                baseline_digits,
            ),
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

    # A text row holds no leaf: the card never shows one (S1). A listed
    # name's min or max (`card_row`: `X.min`, `X.max`) is read from the two
    # sides' summaries, as its final's row is; any other path is a
    # statistic's.
    held = {row.path for row in rows if row.kind != "text"}
    for path in dict.fromkeys(extra_paths):
        if path in held:
            continue
        name, _, field_name = path.rpartition(".")
        if name in listed and field_name in ("min", "max"):
            samples = _diagnostic_values(run_diagnostics, name)
            rows.append(
                _row(
                    OTHER,
                    path,
                    path,
                    _summary(run_diagnostics, name, field_name),
                    _summary(baseline_diagnostics, name, field_name),
                    top_level=True,
                    kind=listed_kind(name),
                    digits=(
                        recorded_digits(samples)
                        if samples is not None
                        else DOUBLE_SHOWN,
                        baseline_digits,
                    ),
                )
            )
        else:
            r = resolve_scalar_path(path, dict(run))
            b = resolve_scalar_path(path, dict(baseline))
            rows.append(_row(OTHER, path, path, r, b, top_level=True))
        held.add(path)
    return rows


def _summary(entries: Sequence, name: str, field_name: str) -> float | None:
    """The ``min`` or ``max`` of the listed name ``name`` among ``entries``:
    a run's diagnostic over its samples, a baseline's summary's field."""
    for entry in entries:
        if getattr(entry, "name", None) != name:
            continue
        if hasattr(entry, field_name):
            value = getattr(entry, field_name)
            return None if value is None else float(value)
        raw = getattr(entry, "values", None)
        values = np.asarray([] if raw is None else raw, dtype=float)
        if values.size == 0:
            return None
        return float(values.min() if field_name == "min" else values.max())
    return None
