"""``dstf diff``'s output: the facts of :mod:`.run_diff` as Markdown a
reviewer pastes into a pull request (:func:`as_markdown`), or as strict
JSON (:func:`as_json`).

The Markdown, in order: a headline of the two labels and the tests
compared; an overview table, one row per backend/os — the result files
compared, the varying groups ("signals") by class, the constant groups
("parameters"); one key line saying what a group and each class is; the
tests where something differs, those not compared and those one run
holds; and for the tests asked for (:func:`detailed`), one ``<details>``
each. A Change is said as ``baseline diff`` says it (:mod:`.baseline_diff_text`'s
numbers), with what it is a percent of: ``57 % of range``. Facts only:
nothing here judges a change, and nothing knows a library or a tool. The
JSON is always complete; identical groups are in it as counts.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Collection
from dataclasses import asdict
from typing import Any

from ..storage.baseline_diff import (
    DiffError,
    SeriesChange,
    machine_dependent,
    short_name,
)
from .baseline_diff_text import (
    _COUNTED,
    _change_text,
    _code,
    _plural,
    _row,
    _stat,
    _stat_json,
    _strict,
    _true_amount,
)
from .run_diff import (
    NOT_HELD,
    READ,
    Constant,
    FolderDiff,
    RunDiff,
    Signal,
    TestRunDiff,
    signal_order,
)
from .stats_rows import StatRow

#: How many names, constants and changed groups a detail lists before it
#: says how many more.
LISTED = 20
#: A cell where nothing was compared.
_DASH = "–"
#: A cell of a kind of group the files hold none of.
NONE = "none"
#: Said beside the counts of a backend whose files hold the tracked
#: variables alone (``saves_tracked_signals_only``).
TRACKED_ONLY = "this tool saves only the tracked signals; parameters are kept"

#: What the JSON's ``limits`` says, in full.
LIMITS = (
    "_Limits: only the stored samples are compared, so what a run did between "
    "them is not seen. A group is the variables whose values are equal, bit for "
    "bit, in both runs; it is counted once and shown by its shortest name. A "
    "constant holds one value at every stored point of both runs. Identical: "
    "the same time points and every value equal; rounding: as many points, and "
    "at each one the time and the value within one storage step of the coarser "
    "file's precision; "
    "near zero (with a cut only): every value of both runs smaller in size than "
    "the cut; derivative: a name of the group holds der(; changed: the rest, "
    "and any value finite in one run only. A Change is the largest |later − "
    "first|, the later run read on the first's time grid, as a percent of the "
    "first run's range (of its value where it is constant; the absolute "
    "difference where it is zero). The solver's statistics are listed, never "
    "scored._"
)


# --- numbers ---------------------------------------------------------------


def amount(change: SeriesChange) -> str:
    """A Change's size with what it is of: ``57 % of range``, ``3.0 % of
    value``, ``1e-05 absolute``; ``0 %`` for none."""
    said = _true_amount(change)
    if change.scale == "range" and change.largest:
        said += " of range"
    return said


def _change_words(change: SeriesChange) -> str:
    """A changed group's cell in the detail: its Change and when, then what
    was not compared (``baseline diff``'s words, the percent said of
    what)."""
    text = _change_text(change)
    if change.largest is not None and not change.note:
        shown = _true_amount(change)
        if text.startswith(shown):
            text = amount(change) + text[len(shown) :]
    return text


def _size(change: SeriesChange) -> str:
    """The largest changed group's size as a cell says it."""
    if change.note:
        return change.note
    if change.non_finite:
        return "not finite"
    if change.largest is None:
        return "nothing compared"
    return amount(change)


def _absolute(value: float) -> str:
    return f"{value:.3g}"


def _kind_count(n: int, kind: str) -> str:
    """A class counted: ``3 derivatives``, ``1 differs``, ``2 differ``."""
    if kind == "derivative":
        return f"{_count(n)} derivative{'s' * (n != 1)}"
    if kind == "differs":
        return f"{_count(n)} differ{'s' * (n == 1)}"
    return f"{_count(n)} {kind}"


def _count(n: int) -> str:
    return f"{n:,}"


# --- counts ----------------------------------------------------------------


def _signal_counts(tests: list[TestRunDiff]) -> Counter[str]:
    out: Counter[str] = Counter()
    for t in tests:
        out.update(t.signal_counts)
    return out


def _constant_counts(tests: list[TestRunDiff]) -> Counter[str]:
    out: Counter[str] = Counter()
    for t in tests:
        out.update(t.constant_counts)
    return out


def _largest_changed(tests: list[TestRunDiff]) -> Signal | None:
    """The changed group ranked first over ``tests`` (each test's list is
    ranked already)."""
    changed = [s for t in tests for s in t.of("changed")]
    return min(changed, key=signal_order, default=None)


def signals_cell(tests: list[TestRunDiff]) -> str:
    """The varying groups said: ``none``, ``all identical``; else near
    zero, derivatives and rounding counted, then the changed ones with the
    largest Change (``changed: none`` beside the others)."""
    counts = _signal_counts(tests)
    if not tests:
        return _DASH
    if not sum(counts.values()):
        return NONE
    if sum(counts.values()) == counts["identical"]:
        return "all identical"
    parts = []
    if counts["near zero"]:
        parts.append(f"{_count(counts['near zero'])} near zero")
    if counts["derivative"]:
        n = counts["derivative"]
        parts.append(f"{_count(n)} derivative{'s' * (n != 1)}")
    if counts["rounding"]:
        parts.append(f"{_count(counts['rounding'])} rounding")
    largest = _largest_changed(tests)
    if largest is None:
        return ", ".join(parts) + "; changed: none"
    changed = f"{_count(counts['changed'])} changed"
    # A 0 % beside "changed" reads as a contradiction: where the runs hold
    # other time points, the move is off the reference's points.
    unmeasured = (
        largest.change.largest == 0
        and not (largest.change.non_finite or largest.change.note)
        and any(not t.same_points for t in tests)
    )
    if not unmeasured:
        changed += f" ({_size(largest.change)})"
    return "; ".join([", ".join(parts), changed]) if parts else changed


def constants_cell(tests: list[TestRunDiff]) -> str:
    """The constant groups said: ``none``, ``all identical``; else how many
    differ and how many are within rounding."""
    if not tests:
        return _DASH
    counts = _constant_counts(tests)
    if not sum(counts.values()):
        return NONE
    parts = []
    if counts["differs"]:
        n = counts["differs"]
        parts.append(f"{_count(n)} differ{'s' * (n == 1)}")
    if counts["rounding"]:
        parts.append(f"{_count(counts['rounding'])} rounding")
    return ", ".join(parts) or "all identical"


def _names_mark(tests: list[TestRunDiff], f: FolderDiff) -> list[str]:
    """The names one run's files hold only, counted per side, and whether
    the runs' files hold other time points."""
    old = sum(len(t.only_old) for t in tests)
    new = sum(len(t.only_new) for t in tests)
    marks = []
    if old:
        marks.append(f"{_plural(old, 'name')} only in {f.old.label}")
    if new:
        marks.append(f"{_plural(new, 'name')} only in {f.new.label}")
    if any(not t.same_points for t in tests):
        marks.append("time points differ")
    return marks


def _marked(text: str, marks: list[str]) -> str:
    return f"{text} ({'; '.join(marks)})" if marks else text


# --- the overview ----------------------------------------------------------


def _overview(diff: RunDiff) -> list[str]:
    header = ["Tool/OS", "Result files", "Signals", "Parameters"]
    lines = [_row(header), _row(["---"] * len(header))]
    for f in diff.folders:
        marks = _names_mark(f.tests, f)
        lines.append(
            _row(
                [
                    f"{f.folder} ({TRACKED_ONLY})" if f.tracked_only else f.folder,
                    f"{_count(len(f.tests))} of {_count(f.held)}",
                    _marked(signals_cell(f.tests), marks),
                    constants_cell(f.tests),
                ]
            )
        )
    return lines


def _key(diff: RunDiff) -> str:
    """The one line under the table: what a group and each class is."""
    first = diff.labels[0]
    said = [
        "A group = the variables whose values are equal in both runs, counted "
        "once; a parameter holds one value throughout.",
        "Identical = the same time points, every value equal; rounding = point "
        "by point within one storage step of the coarser file's precision;",
    ]
    if diff.near_zero is not None:
        said.append(f"near zero = every value smaller in size than {diff.near_zero:g};")
    said.append(
        f"derivative = a der( variable; changed = the rest, by its biggest move "
        f"against {first}. Only stored points are compared."
    )
    return " ".join(said)


def _list(names: list[str], listed: int = LISTED) -> str:
    """Names as code, the first ``listed``, then how many more."""
    shown = ", ".join(_code(n) for n in names[:listed])
    if len(names) > listed:
        shown += f" and {_count(len(names) - listed)} more"
    return shown


def _where(f: FolderDiff, diff: RunDiff) -> str:
    return f"{f.folder}: " if len(diff.folders) > 1 else ""


def _test_lines(diff: RunDiff) -> list[str]:
    """Under the key: per backend/os, the tests where something differs,
    those not compared, and those one run holds."""
    lines = []
    for f in diff.folders:
        where = _where(f, diff)
        differs = [short_name(t.model_id) for t in f.tests if t.differs]
        if differs:
            lines.append(
                f"{where}Differs in {len(differs)} of {_plural(len(f.tests), 'test')}: "
                f"{_list(differs)}."
            )
        elif f.tests:
            beyond = " beyond rounding" if any(t.rounded for t in f.tests) else ""
            lines.append(
                f"{where}Nothing differs{beyond} in {_plural(len(f.tests), 'test')}."
            )
        for side, label in (("old", f.old.label), ("new", f.new.label)):
            other = "new" if side == "old" else "old"
            only = [
                u
                for u in f.uncompared
                if getattr(u, other) == NOT_HELD and getattr(u, side) != NOT_HELD
            ]
            if only:
                said = [
                    _code(short_name(u.model_id))
                    + ("" if getattr(u, side) == READ else f" ({getattr(u, side)})")
                    for u in only
                ]
                lines.append(f"{where}Only in {label}: {', '.join(said)}.")
        both = [u for u in f.uncompared if NOT_HELD not in (u.old, u.new)]
        if both:
            said = []
            for u in both:
                why = "; ".join(
                    f"{label}: {state}"
                    for label, state in ((f.old.label, u.old), (f.new.label, u.new))
                    if state != READ
                )
                said.append(f"{_code(short_name(u.model_id))} ({why})")
            lines.append(f"{where}Not compared: {', '.join(said)}.")
    for folder, run in diff.alone:
        lines.append(f"{folder}: one run only ({run.label}), not compared.")
    return lines


# --- the detail ------------------------------------------------------------


def _group_name(names: list[str]) -> str:
    """A group as it is shown: its shortest name, and how many more."""
    return _code(names[0]) + (f" (+{len(names) - 1})" if len(names) > 1 else "")


def _what(test: TestRunDiff, f: FolderDiff) -> str:
    """A test's summary line: its signals, then its parameters where any
    is not identical (``signals all identical; parameters: 1 differs``)."""
    signals, constants = signals_cell([test]), constants_cell([test])
    if constants in ("all identical", NONE):
        said = signals
    elif signals in ("all identical", NONE):
        said = f"signals {signals}; parameters: {constants}"
    else:
        said = f"{signals}; parameters: {constants}"
    return _marked(said, _names_mark([test], f))


def _count_line(title: str, counts: dict[str, int], always: str) -> str:
    """A detail's line of a kind of group: how many, then each class that
    holds any, and ``always`` even at 0 (``0 changed``); ``none`` where
    there are no groups."""
    n = sum(counts.values())
    if not n:
        return f"- {title}: {NONE}"
    classes = ", ".join(
        _kind_count(k, kind) for kind, k in counts.items() if k or kind == always
    )
    return f"- {title} ({_count(n)} group{'s' * (n != 1)}): {classes}"


def _points(test: TestRunDiff, side: int, label: str) -> str:
    """A run's time points, and their span where the runs' times differ
    beyond rounding."""
    said = f"{_count(test.points[side])} in {label}"
    if not test.same_points:
        first, last = test.spans[side]
        said += f" ({first:g}–{last:g} s)"
    return said


def _measured_on(test: TestRunDiff, label: str) -> str:
    """Where the runs hold other time points: that a Change is read on the
    reference's points alone."""
    return (
        "" if test.same_points else f"; a Change is measured on {label}'s points only"
    )


def _changed(row: StatRow) -> bool:
    """A statistic whose two values differ, or that one run lacks — at all,
    not by a criterion's tolerance: nothing here is scored."""
    if row.baseline is None or row.run is None:
        return row.baseline is not row.run
    a, b = row.baseline, row.run
    if (
        isinstance(a, float)
        and isinstance(b, float)
        and math.isnan(a)
        and math.isnan(b)
    ):
        return False
    return bool(a != b)


def _statistics(rows: list[StatRow]) -> list[str]:
    """The statistics' lines: those that differ, those that do not (a
    translation log's counted), each list the first :data:`LISTED` and how
    many more, then the machine-dependent times."""
    solver = [r for r in rows if not machine_dependent(r)]
    moved = [_stat(r, both=True) for r in solver if _changed(r)]
    same = [
        _stat(r, both=False)
        for r in solver
        if not _changed(r) and r.section != _COUNTED
    ]
    counted = sum(not _changed(r) and r.section == _COUNTED for r in solver)
    same = _capped(same)
    if counted:
        same.append(_plural(counted, f"{_COUNTED} statistic"))
    machine = [_stat(r, both=False) for r in rows if machine_dependent(r)]
    out = []
    for title, said in (
        ("Changed", _capped(moved)),
        ("Unchanged", same),
        ("Machine-dependent", machine),
    ):
        if said:
            out.append(f"  - {title}: " + "; ".join(said))
    return out


def _capped(said: list[str]) -> list[str]:
    if len(said) <= LISTED:
        return said
    return [*said[:LISTED], f"and {_count(len(said) - LISTED)} more"]


def _constant_line(c: Constant) -> str:
    return f"{_group_name(c.names)} {c.old:g} → {c.new:g}"


def _detail(f: FolderDiff, test: TestRunDiff) -> list[str]:
    old, new = f.old.label, f.new.label
    lines = [
        "<details>",
        f"<summary><code>{short_name(test.model_id)}</code> · {f.folder} — "
        f"{_what(test, f)}</summary>",
        "",
        f"`{test.model_id}`",
        "",
        f"- Variables: {_count(test.variables[0])} in {old}, "
        f"{_count(test.variables[1])} in {new}; points: {_points(test, 0, old)}, "
        f"{_points(test, 1, new)}{_measured_on(test, old)}",
    ]
    if test.only_old:
        lines.append(f"- Only in {old}: {_list(test.only_old)}")
    if test.only_new:
        lines.append(f"- Only in {new}: {_list(test.only_new)}")
    lines.append(_count_line("Signals", test.signal_counts, "changed"))
    lines.append(_count_line("Parameters", test.constant_counts, "differs"))
    differ = [c for c in test.constants if c.kind == "differs"]
    if differ:
        shown = "; ".join(_constant_line(c) for c in differ[:LISTED])
        more = len(differ) - LISTED
        lines.append(
            f"- Parameters that differ, {old} → {new}: {shown}"
            + (f"; and {_count(more)} more" if more > 0 else "")
        )
    near = test.of("near zero")
    if near and near[0].largest_absolute is not None:
        s = near[0]
        at = (
            f", {s.largest_absolute_at:g} s"
            if s.largest_absolute_at is not None
            else ""
        )
        lines.append(
            f"- Near zero, the largest |{new} − {old}|: {_absolute(s.largest_absolute)} "
            f"({_group_name(s.names)}{at})"
        )
    derivatives = test.of("derivative")
    if derivatives:
        s = derivatives[0]
        lines.append(
            f"- Derivatives, the largest: {_group_name(s.names)} "
            f"{_change_words(s.change)}"
        )
    changed = test.of("changed")
    if changed:
        lines += ["", _row(["Changed", "Change"]), _row(["---", "---"])]
        for s in changed[:LISTED]:
            lines.append(_row([_group_name(s.names), _change_words(s.change)]))
        if len(changed) > LISTED:
            lines += ["", f"And {_count(len(changed) - LISTED)} more changed groups."]
        lines.append("")
    effort = _statistics(test.statistics)
    if effort:
        lines += [f"- Statistics, {old} → {new} (not scored):", *effort]
    return [*lines, "", "</details>"]


# --- the whole -------------------------------------------------------------


def _compared(diff: RunDiff) -> list[str]:
    return sorted({t.model_id for f in diff.folders for t in f.tests})


def detailed(diff: RunDiff, names: list[str]) -> set[tuple[str, str]]:
    """The (folder, model id) of the tests ``--details`` names: for no
    name, every compared test where something differs; else every
    compared test whose short name or full model id is a name. Raises
    :class:`DiffError` naming every name that matches no compared test."""
    pairs = [(f.folder, t) for f in diff.folders for t in f.tests]
    if not names:
        return {(folder, t.model_id) for folder, t in pairs if t.differs}
    chosen: set[tuple[str, str]] = set()
    unknown = []
    for name in names:
        got = {
            (folder, t.model_id)
            for folder, t in pairs
            if name in (t.model_id, short_name(t.model_id))
        }
        if not got:
            unknown.append(name)
        chosen |= got
    if unknown:
        ids = _compared(diff)
        such = (
            f"a short name such as {short_name(ids[0])}, or a full model id"
            if ids
            else "it compares none"
        )
        raise DiffError(
            f"--details: no test this diff compares is named {', '.join(unknown)} "
            f"({such})"
        )
    return chosen


def as_markdown(diff: RunDiff, details: Collection[tuple[str, str]] = ()) -> str:
    """The Markdown a reviewer pastes into a pull request: the headline, the
    overview, its key line and the tests' lines, then a folded section for
    each (folder, model id) in ``details`` (:func:`detailed`)."""
    first, later = diff.labels
    tests = _compared(diff)
    lines = [
        f"**Every variable, {first} → {later}** ({_plural(len(tests), 'test')})",
        "",
        *_overview(diff),
        "",
        _key(diff),
        "",
    ]
    said = _test_lines(diff)
    if said:
        lines += [*said, ""]
    for f in diff.folders:
        for test in f.tests:
            if (f.folder, test.model_id) in details:
                lines += [*_detail(f, test), ""]
    return "\n".join(lines).rstrip("\n") + "\n"


# --- JSON ------------------------------------------------------------------


def _signal_json(s: Signal) -> dict:
    return {
        "names": s.names,
        "kind": s.kind,
        "change": asdict(s.change),
        "largest_absolute": s.largest_absolute,
        "largest_absolute_at": s.largest_absolute_at,
    }


def _test_json(t: TestRunDiff) -> dict:
    return {
        "model_id": t.model_id,
        "variables": {"old": t.variables[0], "new": t.variables[1]},
        "points": {"old": t.points[0], "new": t.points[1]},
        "only_old": t.only_old,
        "only_new": t.only_new,
        "signals": {
            "counts": t.signal_counts,
            "groups": [_signal_json(s) for s in t.signals],
        },
        "parameters": {
            "counts": t.constant_counts,
            "groups": [asdict(c) for c in t.constants],
        },
        "statistics": [_stat_json(r) for r in t.statistics],
    }


def _folder_json(f: FolderDiff) -> dict:
    return {
        "folder": f.folder,
        "old": asdict(f.old),
        "new": asdict(f.new),
        "tracked_only": f.tracked_only,
        "tests": [_test_json(t) for t in f.tests],
        "uncompared": [asdict(u) for u in f.uncompared],
    }


def as_json(diff: RunDiff) -> str:
    """The facts as strict JSON (non-finite numbers as null plus a flag)."""
    data: dict[str, Any] = {
        "labels": list(diff.labels),
        "near_zero": diff.near_zero,
        "folders": [_folder_json(f) for f in diff.folders],
        "alone": [
            {"folder": folder, "label": run.label, "run": run.folder}
            for folder, run in diff.alone
        ],
        "limits": LIMITS,
    }
    return json.dumps(_strict(data), indent=2, allow_nan=False) + "\n"
