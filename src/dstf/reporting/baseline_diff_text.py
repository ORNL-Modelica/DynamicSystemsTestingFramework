"""``dstf baseline diff``'s output: the facts of
:mod:`dstf.storage.baseline_diff` as Markdown a reviewer pastes into a pull
request (:func:`as_markdown`), or as strict JSON (:func:`as_json`).

The Markdown, in order: one line of counts; the two revisions' subjects;
an overview table, one row per test, sorted by its largest change; for the
tests asked for (:func:`detailed`), one ``<details>`` each with every
variable, the settings that changed and the solver effort old → new; and
the limits. A number is a percent with two significant digits under 10 %
(a trailing zero kept: ``1.0 %``), whole above, in scientific notation
under 0.001 %; a time is ``%g`` seconds. Facts only: nothing here judges
a change, and nothing knows a library. The JSON is always complete.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Collection
from dataclasses import asdict
from typing import Any

from ..storage.baseline_diff import (
    BaselineDiff,
    Diff,
    DiffError,
    SeriesChange,
    SettingChange,
    TestDiff,
    VariableDiff,
    machine_dependent,
)
from .stats_rows import SECTION_TITLES, StatRow, report_text

#: What every output says it did not do.
LIMITS = (
    "_Limits: only the stored samples are compared, so what a run did "
    "between them is not seen. A change is the largest |new − old|, the "
    "new baseline read on the old one's time grid, as a percent of the old "
    "baseline's range (of its value where it is constant; the absolute "
    "difference where it is zero); the old baseline's points outside the "
    "new one's time span are counted, not compared._"
)

#: A cell of a folder where the test has no baseline on either side.
_NONE = ""
#: A figure that does not exist: no steps recorded, or no point compared.
_DASH = "–"
#: What a GitHub pull-request body or comment holds, in characters.
GITHUB_LIMIT = 65_536


# --- numbers ---------------------------------------------------------------


def percent(value: float) -> str:
    """A percent as it is printed: ``0``; ``2.3e-04 %`` under 0.001 %;
    two significant digits under 10 %, a trailing zero kept (``0.0020 %``,
    ``0.19 %``, ``1.0 %``); a whole percent above (``157 %``)."""
    if value == 0:
        return "0"
    size = abs(value)
    if size < 1e-3:
        return f"{value:.1e} %"
    if size < 10:
        # '#' keeps the trailing zero; 9.96 rounds to "10.", a whole percent
        return f"{value:#.2g}".removesuffix(".") + " %"
    return f"{value:.0f} %"


def _signed(value: float) -> str:
    return percent(value) if value <= 0 else "+" + percent(value)


def _seconds(t: float) -> str:
    return f"{t:g} s"


def _amount(change: SeriesChange) -> str:
    """A change's size: a percent, ``of value`` for a constant's, or the
    absolute difference."""
    assert change.largest is not None
    if change.largest == 0:
        return "0"
    if change.scale == "absolute":
        return f"{change.largest:.2g} absolute"
    return percent(change.largest) + (" of value" if change.scale == "value" else "")


def _change_text(change: SeriesChange) -> str:
    """A variable's cell in the detail: its change and when, then what was
    not compared."""
    if change.note:
        return change.note
    parts = []
    if change.largest is not None:
        moved = change.at is not None and change.largest != 0
        at = f" ({_seconds(change.at)})" if moved else ""
        parts.append(_amount(change) + at)
    if change.non_finite:
        where = change.non_finite_at
        parts.append(
            f"{change.non_finite} point{'s' * (change.non_finite != 1)} not finite"
            + (f" from {_seconds(where)}" if where is not None else "")
        )
    if change.outside:
        parts.append(f"{change.outside} old points outside the new time span")
    return "; ".join(parts) or "nothing compared"


# --- ranking ---------------------------------------------------------------


def _rank(variables: list[VariableDiff]) -> tuple[int, float, float]:
    """How large a set of variable changes is, for sorting: any non-finite
    difference first, then the largest percent, then the largest absolute
    difference."""
    non_finite = _non_finite(variables)
    pct = [
        v.change.largest
        for v in variables
        if v.change and v.change.largest is not None and v.change.scale != "absolute"
    ]
    absolute = [
        v.change.largest
        for v in variables
        if v.change and v.change.largest is not None and v.change.scale == "absolute"
    ]
    return (int(non_finite), max(pct, default=-1.0), max(absolute, default=-1.0))


def _size(change: SeriesChange) -> tuple[int, float]:
    """How large a compared change is: any percent above any absolute
    difference, then by its number."""
    assert change.largest is not None
    return (int(change.scale != "absolute"), change.largest)


def _largest(variables: list[VariableDiff]) -> SeriesChange | None:
    """The change the rank puts first: the largest percent, else the
    largest absolute difference."""
    compared = [
        v.change for v in variables if v.change and v.change.largest is not None
    ]
    return max(compared, key=_size, default=None)


def _non_finite(variables: list[VariableDiff]) -> bool:
    return any(v.change and v.change.non_finite for v in variables)


def _test_rank(test: TestDiff) -> tuple[int, int, float, float]:
    compared = [b for b in test.baselines.values() if b.status == "changed"]
    if not compared:
        return (1, 0, 0.0, 0.0)
    variables = [v for b in compared for v in b.variables]
    nf, pct, absolute = _rank(variables)
    return (0, -nf, -pct, -absolute)


# --- the overview ------------------------------------------------------------


def _short(model_id: str) -> str:
    return model_id.rsplit(".", 1)[-1] if "/" not in model_id else model_id


def _code(text: str) -> str:
    """``text`` as a code span: fenced by one backtick more than the
    longest run in it, padded where it holds any (``c`d`` reads
    ```` `` c`d `` ````)."""
    if "`" not in text:
        return f"`{text}`"
    fence = "`" * (max(len(run) for run in re.findall("`+", text)) + 1)
    return f"{fence} {text} {fence}"


_STATUS_CELL = {
    "new": "new",
    "removed": "removed",
    "unreadable": "cannot be read",
    "two baselines": "two baselines",
}


def _cell(b: BaselineDiff) -> str:
    if b.status != "changed":
        return _STATUS_CELL[b.status]
    largest = _largest(b.variables)
    text = _amount(largest) if largest is not None else _DASH
    added = sum(v.status == "new" for v in b.variables)
    gone = sum(v.status == "removed" for v in b.variables)
    if added:
        text += f" +{added} new"
    if gone:
        text += f" −{gone} removed"
    if b.settings:
        text += " · settings"
    if _non_finite(b.variables):
        text += " · not finite"
    return text


def _against_cell(test: TestDiff, several: bool) -> str:
    """The largest change of any folder against ``--against``, chosen as a
    cell's is (:func:`_largest`), and the folder it is in; `` · not
    finite`` when any folder holds a non-finite difference."""
    if not test.against:
        return _NONE
    best: tuple[tuple[int, float], str, SeriesChange] | None = None
    for folder, variables in test.against.items():
        largest = _largest(variables)
        if largest is not None and (best is None or _size(largest) > best[0]):
            best = (_size(largest), folder, largest)
    text = _DASH if best is None else _amount(best[2])
    if best is not None and several:
        text += f" {best[1]}"
    if any(_non_finite(variables) for variables in test.against.values()):
        text += " · not finite"
    return text


def _steps_folder(diff: Diff) -> str | None:
    """The first folder, in column order, where a listed test records its
    steps on both sides."""
    for folder in diff.folders:
        for test in diff.tests:
            b = test.baselines.get(folder)
            if b is not None and b.steps is not None:
                return folder
    return None


def _steps_cell(test: TestDiff, folder: str) -> str:
    b = test.baselines.get(folder)
    if b is None and folder in test.unchanged:
        return "unchanged"
    if b is None or b.steps is None or b.steps[0] == 0:
        return _DASH
    old, new = b.steps
    return _signed((new - old) / old * 100)


def _row(cells: list[str]) -> str:
    """A table row; a ``|`` in a cell is escaped (inside a code span too,
    as GitHub's tables read it), so a name holding one keeps the columns."""
    return "| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |"


def _overview(diff: Diff, tests: list[TestDiff]) -> list[str]:
    steps = _steps_folder(diff)
    header = ["Test", *diff.folders]
    if diff.against:
        header.append(f"vs {diff.against}")
    if steps:
        header.append(f"steps, {steps}")
    lines = [_row(header), _row(["---"] * len(header))]
    several = len(diff.folders) > 2
    for test in tests:
        cells = [_code(_short(test.model_id))]
        for folder in diff.folders:
            b = test.baselines.get(folder)
            cells.append(
                _cell(b) if b else "unchanged" if folder in test.unchanged else _NONE
            )
        if diff.against:
            cells.append(_against_cell(test, several))
        if steps:
            cells.append(_steps_cell(test, steps))
        lines.append(_row(cells))
    return lines


# --- the detail --------------------------------------------------------------


def _variable_cell(v: VariableDiff) -> str:
    if v.status != "changed":
        return v.status
    assert v.change is not None
    text = _change_text(v.change)
    if v.expression is not None:
        text += f"; expression {v.expression[0] or '–'} → {v.expression[1] or '–'}"
    return text


def _against_variable_cell(v: VariableDiff, against: str, folder: str) -> str:
    if v.status == "new":
        return f"not in {against}"
    if v.status == "removed":
        return f"not in {folder}"
    return _variable_cell(v)


def _variable_table(diff: Diff, test: TestDiff) -> list[str]:
    columns: list[tuple[str, dict[str, str]]] = []
    labels: dict[str, str] = {}
    for folder in diff.folders:
        b = test.baselines.get(folder)
        if b is None or b.status != "changed":
            continue
        cells = {}
        for v in b.variables:
            labels.setdefault(v.name, v.label)
            cells[v.name] = _variable_cell(v)
        columns.append((folder, cells))
    for folder in diff.folders:
        if folder not in test.against:
            continue
        cells = {}
        for v in test.against[folder]:
            labels.setdefault(v.name, v.label)
            cells[v.name] = _against_variable_cell(v, str(diff.against), folder)
        columns.append((f"{folder} vs {diff.against}", cells))
    if not columns:
        return []
    lines = [
        _row(["Variable", *(head for head, _ in columns)]),
        _row(["---"] * (len(columns) + 1)),
    ]
    for name, label in labels.items():
        lines.append(
            _row([_code(label), *(cells.get(name, "") for _, cells in columns)])
        )
    return lines


def _value(value: Any) -> str:
    if value is None:
        return "–"
    if isinstance(value, str):
        return value
    if isinstance(value, float):
        return f"{value:g}"
    return json.dumps(value, default=str)


def _setting(s: SettingChange) -> str:
    return f"{s.section} {s.key} {_value(s.old)} → {_value(s.new)}"


def _stat_label(row: StatRow) -> str:
    if row.section == "simulation" or row.label == row.key:
        return row.label
    title = SECTION_TITLES.get(row.section, row.section)
    return f"{title} {row.label[:1].lower()}{row.label[1:]}"


def _stat_values(row: StatRow) -> tuple[str, str]:
    run_digits, baseline_digits = row.digits
    old = report_text(row.kind, row.baseline, baseline_digits) or "–"
    new = report_text(row.kind, row.run, run_digits) or "–"
    return old, new


def _stat(row: StatRow, *, both: bool) -> str:
    old, new = _stat_values(row)
    if old == new and not both:
        return f"{_stat_label(row)} {old}"
    return f"{_stat_label(row)} {old} → {new}"


#: The section whose unchanged rows are counted, not listed: a translation
#: log holds dozens, and listed they took 33 real baselines' Markdown past
#: what a pull-request comment holds (65,536 characters).
_COUNTED = "translation"


def _effort(rows: list[StatRow]) -> list[str]:
    solver = [r for r in rows if not machine_dependent(r)]
    moved = [_stat(r, both=True) for r in solver if r.changed]
    same = [
        _stat(r, both=False) for r in solver if not r.changed and r.section != _COUNTED
    ]
    counted = sum(not r.changed and r.section == _COUNTED for r in solver)
    if counted:
        same.append(f"{_plural(counted, f'{_COUNTED} statistic')}")
    machine = [_stat(r, both=False) for r in rows if machine_dependent(r)]
    out = []
    if moved:
        out.append("  - Solver effort, old → new: " + "; ".join(moved))
    if same:
        out.append("  - Unchanged: " + "; ".join(same))
    if machine:
        out.append("  - Machine-dependent: " + "; ".join(machine))
    return out


def _baseline_lines(b: BaselineDiff) -> list[str]:
    file = b.new_file or b.old_file
    head = f"- **{b.folder}**" + (f" `{file}`" if file else "")
    if b.status == "new":
        return [f"{head}: new baseline"]
    if b.status == "removed":
        return [f"{head}: removed"]
    if b.status != "changed":
        return [f"{head}: {b.note}"]
    if b.old_file and b.new_file and b.old_file != b.new_file:
        head = f"- **{b.folder}** `{b.old_file}` → `{b.new_file}`"
    settings = (
        "settings: " + "; ".join(_setting(s) for s in b.settings)
        if b.settings
        else "settings unchanged"
    )
    if b.sampling_first is not None:
        first = b.sampling_first
        settings += (
            f"; sampling first recorded ({_value(first.setting)}, {first.precision})"
        )
    samples = []
    if b.samples:
        samples.append(
            "  - Samples, old → new: "
            + "; ".join(f"{s.key} {_value(s.old)} → {_value(s.new)}" for s in b.samples)
        )
    return [f"{head}: {settings}", *samples, *_effort(b.effort)]


def _detail(diff: Diff, test: TestDiff) -> list[str]:
    ranked = [b for b in test.baselines.values() if b.status == "changed"]
    largest = _largest([v for b in ranked for v in b.variables])
    what = (
        _amount(largest)
        if largest is not None
        else ", ".join(
            sorted(
                {_STATUS_CELL.get(b.status, b.status) for b in test.baselines.values()}
            )
        )
    )
    lines = [
        "<details>",
        f"<summary><code>{_short(test.model_id)}</code> — {what}</summary>",
        "",
        f"`{test.model_id}`",
        "",
    ]
    table = _variable_table(diff, test)
    if table:
        lines += [*table, ""]
    for folder in diff.folders:
        b = test.baselines.get(folder)
        if b is not None:
            lines += _baseline_lines(b)
    if test.unchanged:
        lines.append("- Unchanged in " + ", ".join(test.unchanged))
    return [*lines, "", "</details>"]


# --- the whole ---------------------------------------------------------------


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' * (n != 1)}"


def _counts(diff: Diff) -> str:
    """The first line's counts: tests, then each folder's baselines."""
    tests: Counter[str] = Counter()
    for test in diff.tests:
        statuses = {b.status for b in test.baselines.values()}
        if statuses <= {"unreadable", "two baselines"}:
            tests["not compared"] += 1
        elif statuses in ({"new"}, {"removed"}) and not test.unchanged:
            # new: no folder held it at the older side; removed: none holds
            # it at the newer. A folder that gained one is a changed test.
            tests[statuses.pop()] += 1
        else:
            tests["changed"] += 1
    per_folder = []
    for folder in diff.folders:
        got: Counter[str] = Counter(
            t.baselines[folder].status for t in diff.tests if folder in t.baselines
        )
        if got:
            words = (
                f"{got[s]} {_STATUS_CELL.get(s, s)}"
                for s in ("changed", "new", "removed", "unreadable", "two baselines")
                if got[s]
            )
            per_folder.append(f"{folder}: " + ", ".join(words))
    text = (
        f"{_plural(tests['changed'], 'test')} changed, {tests['new']} new, "
        f"{tests['removed']} removed"
    )
    if tests["not compared"]:
        text += f", {tests['not compared']} not compared"
    if per_folder:
        text += " (" + "; ".join(per_folder) + ")"
    return (
        f"{text}. {_plural(diff.unchanged, 'baseline')} unchanged, "
        f"{diff.metadata_only} of them in metadata only."
    )


def detailed(diff: Diff, names: list[str]) -> list[str]:
    """The model ids of the tests ``--details`` names, in the order named:
    every listed test for no name; else each test whose short name or full
    model id is a name. Raises :class:`DiffError` naming every name that
    matches no listed test."""
    if not names:
        return [t.model_id for t in diff.tests]
    chosen: list[str] = []
    unknown = []
    for name in names:
        got = [
            t.model_id for t in diff.tests if name in (t.model_id, _short(t.model_id))
        ]
        if not got:
            unknown.append(name)
        chosen += [m for m in got if m not in chosen]
    if unknown:
        tests = _sorted(diff.tests)
        such = (
            f"a short name such as {_short(tests[0].model_id)}, or a full model id"
            if tests
            else "it lists none"
        )
        raise DiffError(
            f"--details: no test this diff lists is named {', '.join(unknown)} ({such})"
        )
    return chosen


def _sorted(tests: list[TestDiff]) -> list[TestDiff]:
    """The overview's order: largest change first."""
    return sorted(tests, key=lambda t: (_test_rank(t), t.model_id))


def as_markdown(diff: Diff, details: Collection[str] = ()) -> str:
    """The Markdown a reviewer pastes into a pull request: the counts, the
    overview and the limits, and a folded section for each test whose
    model id is in ``details`` (:func:`detailed`)."""
    new = diff.new.short if diff.new else "files on disk"
    lines = [f"**Baselines changed {diff.old.short}..{new}**: {_counts(diff)}", ""]
    old_said = f"`{diff.old.short}` {diff.old.subject}"
    new_said = (
        f"`{diff.new.short}` {diff.new.subject}" if diff.new else "the files on disk"
    )
    lines += [f"{old_said} → {new_said}", ""]
    tests = _sorted(diff.tests)
    if not tests:
        lines += ["No baseline changed.", ""]
    else:
        lines += [*_overview(diff, tests), ""]
        for test in tests:
            if test.model_id in details:
                lines += [*_detail(diff, test), ""]
    lines.append(LIMITS)
    return "\n".join(lines) + "\n"


# --- JSON ----------------------------------------------------------------------


def _strict(value: Any) -> Any:
    """``value`` with every non-finite number written as null, and beside
    it in an object ``<key>_non_finite``: ``"nan"``, ``"inf"`` or
    ``"-inf"`` — strict JSON that keeps the fact."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(item, float) and not math.isfinite(item):
                out[key] = None
                out[f"{key}_non_finite"] = str(item)
            else:
                out[key] = _strict(item)
        return out
    if isinstance(value, list | tuple):
        return [_strict(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _stat_json(row: StatRow) -> dict:
    return {
        "section": row.section,
        "key": row.key,
        "label": row.label,
        "kind": row.kind,
        "old": row.baseline,
        "new": row.run,
        "changed": row.changed,
        "machine_dependent": machine_dependent(row),
    }


def _baseline_json(b: BaselineDiff) -> dict:
    return {
        "status": b.status,
        "old_file": b.old_file,
        "new_file": b.new_file,
        "note": b.note,
        "variables": [asdict(v) for v in b.variables],
        "settings": [asdict(s) for s in b.settings],
        "samples": [asdict(s) for s in b.samples],
        "sampling_first": asdict(b.sampling_first) if b.sampling_first else None,
        "effort": [_stat_json(r) for r in b.effort],
        "steps": {"old": b.steps[0], "new": b.steps[1]} if b.steps else None,
    }


def as_json(diff: Diff) -> str:
    """The facts as strict JSON (non-finite numbers as null plus a flag)."""
    data = {
        "reference_root": str(diff.root),
        "old": asdict(diff.old),
        "new": asdict(diff.new) if diff.new else None,
        "against": diff.against,
        "folders": diff.folders,
        "unchanged": diff.unchanged,
        "metadata_only": diff.metadata_only,
        "tests": [
            {
                "model_id": t.model_id,
                "baselines": {f: _baseline_json(b) for f, b in t.baselines.items()},
                "against": {f: [asdict(v) for v in vs] for f, vs in t.against.items()},
                "unchanged_in": t.unchanged,
            }
            for t in diff.tests
        ],
        "limits": LIMITS,
    }
    return json.dumps(_strict(data), indent=2, allow_nan=False) + "\n"
