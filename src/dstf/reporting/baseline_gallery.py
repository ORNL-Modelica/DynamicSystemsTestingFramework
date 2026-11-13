"""``dstf baseline plot``'s images and page, from the facts of
:mod:`dstf.storage.baseline_history`: one figure per variable that moved,
of each folder drawn (:func:`plan_images`, at a path safe on every OS; a
constant in the folder's value table instead, moved or not
(:func:`value_table`); the others named in one line of the page —
unchanged, or moved only by the rounding of a file saved with fewer
digits), and one
self-contained ``gallery.html`` (:func:`gallery_html`) — every PNG inline
as a data URI, no script, nothing fetched — to send to a reviewer.

The output directory is the command's own: a directory that holds a file
this command did not write is refused, and a rewrite removes exactly the
files the last run was to write, as its marker lists them
(:func:`stale_files`) — written before its first image, so a run stopped
part way leaves a directory the next one may write.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..figures.history import KIND_WORDS, HistoryTrace, cut, history_figure
from ..storage.baseline_diff import (
    BaselineDiff,
    VariableDiff,
    folder_order,
    short_name,
)
from ..storage.baseline_history import (
    MAX_KEPT,
    FolderHistory,
    History,
    Line,
    Revision,
    Run,
    Source,
    TestHistory,
    VariableHistory,
)
from .baseline_diff_text import (
    LIMITS,
    change_cell,
    change_rank,
    overview_cells,
    overview_header,
    sorted_tests,
    steps_folder,
    value_change,
)
from .run_history import RunHistory

#: The page every run writes, at the top of the output directory.
GALLERY = "gallery.html"
#: The list of every file a run writes, written before the first: what the
#: next run may remove.
MARKER = ".dstf-baseline-plot.json"
#: A legend's subject, a file name's label, a test folder's name.
SUBJECT_CHARS, LABEL_CHARS, TEST_CHARS = 48, 40, 100
#: A backend/os folder's name (``Dymola-linux``).
_FOLDER_CHARS = 40
#: One note under an image.
_NOTE_CHARS = 150
#: A value table's cell where the source holds no such constant.
_DASH = "–"
#: The most variables each sentence of the line of those not drawn names.
_UNCHANGED_SHOWN = 10
#: Names no file may have on Windows, whatever its extension.
_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"{port}{i}" for port in ("COM", "LPT") for i in range(1, 10)}
)


class OutputRefused(Exception):
    """An output directory this command may not write; one sentence."""


@dataclass(frozen=True)
class Image:
    """One variable of one test in one folder, to be drawn."""

    #: inside the output directory, ``/``-separated
    path: str
    test: str
    folder: str
    label: str
    figure: dict
    notes: list[str] = field(default_factory=list)


def legend_name(revision: Source) -> str:
    """``<date> <short hash> <subject>``, the subject cut at 48 characters."""
    if isinstance(revision, Run):
        return f"{revision.label} ({revision.folder})"
    commit = revision.commit
    return f"{revision.date} {commit.short} {cut(commit.subject, SUBJECT_CHARS)}"


# --- names on disk ---------------------------------------------------------------


def _safe(text: str, limit: int, *, dots: bool) -> str:
    """``text`` as a file name on every OS: each run of other characters
    one ``_`` (a dot kept where ``dots``), cut to ``limit``, no dot, space
    or ``_`` at either end, and never a name Windows reserves."""
    other = r"[^A-Za-z0-9_.-]+" if dots else r"[^A-Za-z0-9_-]+"
    name = re.sub(other, "_", text)[:limit].strip("._ ") or "_"
    if name.split(".")[0].upper() in _RESERVED:
        name = "_" + name
    return name


def _test_folders(model_ids: Iterable[str]) -> dict[str, str]:
    """Each test's folder: its short name; its full model id where two
    tests' short names are one (letter case aside: Windows and macOS read
    them alike); a hash beside a model id that is still one when cut."""
    ids = list(model_ids)
    names = {m: _safe(short_name(m), TEST_CHARS, dots=True) for m in ids}
    seen = Counter(n.lower() for n in names.values())
    names = {
        m: n if seen[n.lower()] == 1 else _safe(m, TEST_CHARS, dots=True)
        for m, n in names.items()
    }
    seen = Counter(n.lower() for n in names.values())
    for m, n in names.items():
        if seen[n.lower()] > 1:
            code = hashlib.sha256(m.encode()).hexdigest()[:8]
            names[m] = f"{n[: TEST_CHARS - 9]}-{code}"
    return names


# --- the images ----------------------------------------------------------------


def plan_images(tests: list[TestHistory]) -> list[Image]:
    """Every image of the history — one per variable that moved and is no
    constant (:attr:`~.VariableHistory.drawn`) — with its figure and its
    notes."""
    folders = _test_folders(t.model_id for t in tests)
    out = []
    for test in tests:
        for folder in test.folders:
            where = f"{folders[test.model_id]}/" + _safe(
                folder.folder.replace("/", "-"), _FOLDER_CHARS, dots=False
            )
            moved = [v for v in folder.variables if v.drawn]
            width = max(2, len(str(len(moved))))
            for n, variable in enumerate(moved, start=1):
                name = f"{n:0{width}d}-{_safe(variable.label, LABEL_CHARS, dots=False)}"
                notes = _notes(folder, variable)
                out.append(
                    Image(
                        path=f"{where}/{name}.png",
                        test=test.model_id,
                        folder=folder.folder,
                        label=variable.label,
                        figure=_figure(test, folder, variable, notes),
                        notes=notes,
                    )
                )
    return out


def _figure(
    test: TestHistory,
    folder: FolderHistory,
    variable: VariableHistory,
    notes: list[str],
) -> dict:
    traces = []
    for line in variable.lines:
        d = line.difference
        traces.append(
            HistoryTrace(
                name=legend_name(line.revision),
                time=line.time,
                values=line.values,
                change_time=None if d is None else d.time,
                # the compared points: the peak drawn is the Change
                change=None if d is None else np.where(d.finite, d.scaled, np.nan),
                non_finite_at=None if d is None else d.time[d.differs],
            )
        )
    first = variable.lines[0].revision
    scales = [line.difference.scale for line in variable.lines if line.difference]
    return history_figure(
        title=f"{short_name(test.model_id)} · {folder.folder} · {variable.label}",
        y_title=variable.label,
        traces=traces,
        unit=scales[0] if scales else "range",
        first=first.first,
        notes=notes,
        kind=first.kind,
    )


def _shorts(revisions: Iterable[Source]) -> str:
    return ", ".join(r.short for r in revisions)


def _points(n: int) -> str:
    return f"{n} point{'s' * (n != 1)}"


def _notes(folder: FolderHistory, variable: VariableHistory) -> list[str]:
    """What the image does not show by itself: the revisions skipped (each
    with the one it equals) or left out, a rename, a revision that stores
    no such variable, where it was first stored, and each line's points not
    compared."""
    notes = []
    if folder.equal:
        groups: dict[Revision, list[Revision]] = {}
        for skipped, kept in folder.equal:
            groups.setdefault(kept, []).append(skipped)
        said = "; ".join(
            f"{_shorts(skipped)} (equal to {kept.commit.short})"
            for kept, skipped in groups.items()
        )
        notes.append(f"Skipped: {said}.")
    if folder.absent:
        notes.append(f"No baseline at {_shorts(folder.absent)}.")
    notes += [
        f"Cannot be read at {r.commit.short} ({why})." for r, why in folder.unreadable
    ]
    notes += _run_notes(folder)
    if folder.thinned:
        total = len(folder.kept) + len(folder.thinned)
        notes.append(
            f"{len(folder.kept)} of {total} revisions drawn: the first, the last "
            f"and the {MAX_KEPT - 2} that moved most; left out "
            f"{_shorts(folder.thinned)}."
        )
    notes += [
        f"Renamed {old} → {new} at {short}." for short, old, new in variable.renames
    ]
    held = {line.revision for line in variable.lines}
    start = variable.lines[0].revision
    words = KIND_WORDS[start.kind]
    after_first = folder.kept.index(start)
    missing = [r for r in folder.kept[after_first:] if r not in held]
    if missing:
        notes.append(words["not held"].format(_shorts(missing)))
    if variable.first_stored not in (None, start):
        notes.append(
            words["first held"].format(variable.first_stored.short)
            + f", which is left out; measured from {start.short}."
        )
    elif after_first:
        notes.append(
            words["first held"].format(start.short)
            + "; nothing earlier to measure against."
        )
    for line in variable.lines[1:]:
        change, short = line.change, line.revision.short
        assert change is not None
        if change.note:
            notes.append(f"{short}: {change.note}.")
            continue
        if change.non_finite:
            at = change.non_finite_at
            notes.append(
                f"{short}: {_points(change.non_finite)} not finite"
                + (f" from {at:g} s" if at is not None else "")
                + " (× on the panel)."
            )
        if change.outside:
            notes.append(
                f"{short}: {_points(change.outside)} of the first outside its "
                "time span, not compared."
            )
        if change.uneven:
            notes.append(
                f"{short}: {_points(change.uneven)} of the first inside an event "
                "it stores another number of times, not compared."
            )
    return [cut(note, _NOTE_CHARS) for note in notes]


# --- the page ------------------------------------------------------------------

_STYLE = """
body { margin: 0 auto; max-width: 1040px; padding: 16px; background: #ffffff;
  color: #1f1f1f; font: 15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
code { font: 13px ui-monospace, "SFMono-Regular", Consolas, monospace; }
h1 { font-size: 22px; } h2 { font-size: 18px; margin-top: 40px; }
h3 { font-size: 15px; color: #444444; }
section { border-top: 1px solid #dddddd; }
table { border-collapse: collapse; margin: 8px 0; }
th, td { border: 1px solid #dddddd; padding: 4px 8px; text-align: left; }
th { background: #f5f5f5; font-weight: 600; }
.id, figcaption, .said { color: #555555; font-size: 13px; }
figure { margin: 12px 0 24px; }
img { max-width: 100%; height: auto; border: 1px solid #eeeeee; }
"""


def _e(text: object) -> str:
    return html.escape(str(text), quote=True)


def gallery_html(
    history: History, images: list[Image], pngs: Mapping[str, bytes]
) -> str:
    """The page: the revisions with their subjects, then one section per
    test — in ``baseline diff``'s order, its overview row, then its images
    (``pngs`` by image path, inlined). A named test ``baseline diff``
    first → last does not list goes last, without a row."""
    diff = history.diff
    steps = steps_folder(diff)
    header = overview_header(diff, steps)
    by_id = {t.model_id: t for t in history.tests}
    order = [t.model_id for t in sorted_tests(diff.tests) if t.model_id in by_id]
    order += [m for m in by_id if m not in order]
    rows = {t.model_id: t for t in diff.tests}
    revisions = "".join(
        f"<li><code>{_e(r.date)} {_e(r.commit.short)}</code> {_e(r.commit.subject)}</li>"
        for r in history.revisions
    )
    tests = len(history.tests)
    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>Baselines at {len(history.revisions)} revisions</title>",
        f"<style>{_STYLE}</style></head><body>",
        f"<h1>Baselines at {len(history.revisions)} revisions</h1>",
        f"<ol>{revisions}</ol>",
        "<p>Each image: the stored baseline of one variable at each revision "
        "(top), and each later revision minus the first, on the first's time "
        "grid, as <code>baseline diff</code> measures a change (bottom).</p>",
        f'<p class="said">{_e(LIMITS.strip("_"))}</p>',
        f"<p>{len(images)} image{'s' * (len(images) != 1)} of "
        f"{tests} test{'s' * (tests != 1)}.</p>",
    ]
    for model_id in order:
        parts += _section(
            by_id[model_id], rows.get(model_id), header, diff, steps, images, pngs
        )
    parts.append("</body></html>")
    return "\n".join(parts) + "\n"


def _section(test, row, header, diff, steps, images, pngs) -> list[str]:
    short = short_name(test.model_id)
    out = [
        "<section>",
        f"<h2><code>{_e(short)}</code></h2>",
        f'<p class="id"><code>{_e(test.model_id)}</code></p>',
    ]
    if row is not None:
        heads = "".join(f"<th>{_e(h)}</th>" for h in header)
        cells = "".join(f"<td>{_e(c)}</td>" for c in overview_cells(diff, row, steps))
        out.append(
            f"<table><tr>{heads}</tr><tr><td><code>{_e(short)}</code></td>"
            f"{cells}</tr></table>"
        )
    else:
        out.append(
            '<p class="said">not changed between the first and last revision</p>'
        )
    if not test.folders:
        out.append(
            '<p class="said">No folder drawn holds two revisions with different '
            "values.</p>"
        )
    for folder in test.folders:
        out.append(f"<h3>{_e(folder.folder)}</h3>")
        unchanged = [
            v.label
            for v in folder.variables
            if not v.moved and not v.rounding_only and not v.flat
        ]
        rounding = [v.label for v in folder.variables if v.rounding_only and not v.flat]
        if unchanged or rounding:
            said = _not_drawn(unchanged, rounding)
            out.append(f'<p class="said">{_e(said)}</p>')
        for image in images:
            if image.test != test.model_id or image.folder != folder.folder:
                continue
            data = base64.b64encode(pngs[image.path]).decode("ascii")
            caption = "<br>".join(_e(n) for n in image.notes)
            out.append(
                f'<figure><img alt="{_e(image.label)}" '
                f'src="data:image/png;base64,{data}">'
                + (f"<figcaption>{caption}</figcaption>" if caption else "")
                + "</figure>"
            )
        out += value_table(folder)
    out.append("</section>")
    return out


def tables(history: History) -> int:
    """The value tables of the history: one per folder drawn that holds a
    constant."""
    return sum(
        any(v.flat for v in folder.variables)
        for test in history.tests
        for folder in test.folders
    )


def value_table(folder: FolderHistory) -> list[str]:
    """The folder's constants (:attr:`~.VariableHistory.flat`), moved or
    not, as one table: a row each, its value at each source drawn — at the
    digits that source holds, ``–`` where it holds none — and the last
    one's Change against the first, in ``baseline diff``'s words. None
    where the folder holds no constant."""
    flat = [v for v in folder.variables if v.flat]
    if not flat:
        return []
    heads = "".join(f"<th>{_e(source.short)}</th>" for source in folder.kept)
    rows = [
        '<table class="constants">',
        f"<tr><th>Constant</th>{heads}<th>Change</th></tr>",
    ]
    for variable in flat:
        at = {line.revision: line for line in variable.lines}
        cells = "".join(
            f"<td>{_e(_constant(at[source]) if source in at else _DASH)}</td>"
            for source in folder.kept
        )
        last = variable.lines[-1].change
        change = _DASH if last is None else value_change(last)
        rows.append(
            f"<tr><td><code>{_e(variable.label)}</code></td>{cells}"
            f"<td>{_e(change)}</td></tr>"
        )
    rows.append("</table>")
    return rows


def _constant(line: Line) -> str:
    """A constant's one value, at the digits its source holds."""
    return f"{float(line.values[0]):.{line.digits}g}"


#: Under a table of a named test's values, where a row holds an end value.
END_KEY = (
    "(end) = the value at the end of each run; Change = the last run's "
    "biggest move over the whole run, against the first."
)


def values_table(folder: FolderHistory, runs: list[Run]) -> list[str]:
    """A named test's every tracked variable in one table (``dstf plot
    --test``), in the order the runs track them: a constant's one value in
    each run, a varying one's value at the end of each run (its label
    marked ``(end)``) — at the digits that run holds, ``–`` where a run
    holds none, its run failed or did not run — and the last line's Change
    against the first: the largest move over the whole run, not the
    difference of the end values. Under it, what ``(end)`` and Change are,
    where a row is marked. None where no two runs are compared."""
    if not folder.variables:
        return []
    heads = "".join(f"<th>{_e(run.short)}</th>" for run in runs)
    rows = [
        '<table class="values">',
        f"<tr><th>Value</th>{heads}<th>Change</th></tr>",
    ]
    for variable in folder.variables:
        at = {line.revision: line for line in variable.lines}
        cells = "".join(
            f"<td>{_e(_value_at(at[run], variable.flat) if run in at else _DASH)}</td>"
            for run in runs
        )
        last = variable.lines[-1].change
        change = _DASH if last is None else value_change(last)
        end = "" if variable.flat else " (end)"
        rows.append(
            f"<tr><td><code>{_e(variable.label)}</code>{end}</td>{cells}"
            f"<td>{_e(change)}</td></tr>"
        )
    rows.append("</table>")
    if not all(v.flat for v in folder.variables):
        rows.append(f'<p class="said">{_e(END_KEY)}</p>')
    return rows


def _value_at(line: Line, flat: bool) -> str:
    """A constant's one value, or a varying one's last, at the digits its
    source holds."""
    value = line.values[0] if flat else line.values[-1]
    return f"{float(value):.{line.digits}g}"


def _not_drawn(unchanged: list[str], rounding: list[str]) -> str:
    """The one line naming a folder's variables that did not move: the
    unchanged, then those that moved by the files' rounding only — each
    sentence only where it names any."""
    return _not_drawn_of(
        [("unchanged", unchanged), ("rounding of the saved digits only", rounding)]
    )


def _not_drawn_of(groups: list[tuple[str, list[str]]]) -> str:
    """The not-drawn line: a sentence for each group that names any."""
    said = []
    for what, labels in groups:
        if not labels:
            continue
        shown = ", ".join(labels[:_UNCHANGED_SHOWN])
        more = len(labels) - _UNCHANGED_SHOWN
        said.append(
            f"Not drawn, {what}: {shown}"
            + (f" … and {more} more" if more > 0 else "")
            + "."
        )
    return " ".join(said)


# --- dstf plot: the runs' page ---------------------------------------------------


def _run_notes(folder: FolderHistory) -> list[str]:
    """Each run of the folder that is no line: its run failed, with why,
    or it holds no result of the test."""
    return [f"{s.short}: the run failed ({why})." for s, why in folder.failed] + [
        f"{s.short}: not run." for s in folder.not_run
    ]


def _run_changes(folder: FolderHistory) -> list[VariableDiff]:
    """Each later line of each variable two runs hold, as ``baseline diff``
    reads a variable: its change against the variable's first line. Every
    run's change, so the rank and the Change cell read the largest move of
    any run and any point not finite, wherever each is — a run that moved
    and a later one that came back still rank and read as moved. Only the
    largest of them and whether any is not finite are read: no count."""
    return [
        VariableDiff(line.name, v.label, "changed", change=line.change)
        for v in folder.variables
        for line in v.lines[1:]
        if line.change is not None
    ]


def run_sections(history: RunHistory) -> list[tuple[TestHistory, FolderHistory]]:
    """One section per test and folder drawn, largest change first, as
    ``baseline diff`` ranks its tests — each variable's largest of any later
    run against the first (:func:`_run_changes`); then by model id and
    folder."""

    def key(item: tuple[TestHistory, FolderHistory]):
        test, folder = item
        nf, pct, absolute = change_rank(_run_changes(folder))
        return (-nf, -pct, -absolute, test.model_id, folder_order(folder.folder))

    return sorted(((t, f) for t in history.tests for f in t.folders), key=key)


def alone_line(folder: str, run: Run) -> str:
    """The line naming a backend/os given one run only."""
    return f"{folder}: one run only ({run.label}), not drawn."


def _section_notes(folder: FolderHistory) -> list[str]:
    """What a section says before its images: each run that is no line,
    and that there is nothing to compare where fewer than two are."""
    notes = _run_notes(folder)
    if len(folder.kept) < 2:
        notes.append("No two runs of it to compare.")
    return notes


def line_names(history: RunHistory) -> dict[str, str]:
    """How every line names each test a drawn run holds: its short name, or
    its full model id where another test a drawn run holds has the same
    short name."""
    seen = Counter(short_name(m) for m in history.held)
    return {m: m if seen[short_name(m)] > 1 else short_name(m) for m in history.held}


def uncompared_lines(history: RunHistory) -> list[str]:
    """One console line per section with fewer than two runs to compare:
    its test, its folder and why."""
    names = line_names(history)
    return [
        f"{names[test.model_id]} · {folder.folder}: " + " ".join(_section_notes(folder))
        for test, folder in run_sections(history)
        if len(folder.kept) < 2
    ]


def _tests_named(model_ids: list[str], names: Mapping[str, str]) -> str:
    """The tests ``model_ids`` by their ``names``, cut past ten: the end of
    a line's sentence."""
    shown = ", ".join(names[m] for m in model_ids[:_UNCHANGED_SHOWN])
    more = len(model_ids) - _UNCHANGED_SHOWN
    return shown + (f" … and {more} more" if more > 0 else "") + "."


def nothing_moved_line(history: RunHistory) -> str:
    """The one line naming the tests where nothing moved, cut past ten."""
    n = len(history.nothing_moved)
    return f"Nothing moved in {n} test{'s' * (n != 1)}: " + _tests_named(
        history.nothing_moved, line_names(history)
    )


def only_lines(history: RunHistory) -> list[str]:
    """One line per folder and run naming the tests that run alone holds
    of the folder's, cut past ten: not drawn."""
    names = line_names(history)
    return [
        f"{folder}: {len(ids)} test{'s' * (len(ids) != 1)} only in {label}, not "
        "drawn: " + _tests_named(ids, names)
        for folder, label, ids in history.only
    ]


def run_tables(history: RunHistory) -> int:
    """The value tables of the page: one per section that holds a constant;
    with tests named, one per section where two runs are compared."""
    return sum(
        bool(folder.variables)
        if history.named
        else any(v.flat for v in folder.variables)
        for test in history.tests
        for folder in test.folders
    )


def run_gallery_html(
    history: RunHistory, images: list[Image], pngs: Mapping[str, bytes]
) -> str:
    """``dstf plot``'s page: the runs of each folder, the tests where
    nothing moved, those only one run of a folder holds, then one section
    per test and folder, largest change of any run first
    (:func:`run_sections`) — its Change, then its images (``pngs``
    by image path, inlined), its table of constants and the line of the
    variables not drawn."""
    labels = list(
        dict.fromkeys(r.label for runs in history.folders.values() for r in runs)
    )
    title = " → ".join(labels)
    runs = "".join(
        f"<li>{_e(folder)}: {_e(' → '.join(legend_name(r) for r in got))}</li>"
        for folder, got in history.folders.items()
    )
    sections = run_sections(history)
    n = len(sections)
    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>Runs {_e(title)}</title>",
        f"<style>{_STYLE}</style></head><body>",
        f"<h1>Runs {_e(title)}</h1>",
        f"<ul>{runs}</ul>",
        "<p>Each image: one variable of each run (top), and each later run "
        "minus the first, on the first's time grid, as <code>baseline "
        "diff</code> measures a change (bottom). A constant is a row of its "
        "section's table instead.</p>",
        f"<p>{len(images)} image{'s' * (len(images) != 1)} and "
        f"{run_tables(history)} table{'s' * (run_tables(history) != 1)} in "
        f"{n} section{'s' * (n != 1)}.</p>",
    ]
    if history.nothing_moved:
        parts.append(f'<p class="said">{_e(nothing_moved_line(history))}</p>')
    parts += [f'<p class="said">{_e(line)}</p>' for line in only_lines(history)]
    for folder, run in history.alone:
        parts.append(f'<p class="said">{_e(alone_line(folder, run))}</p>')
    for test, folder in sections:
        runs = history.folders[folder.folder] if history.named else None
        parts += _run_section(test, folder, images, pngs, runs)
    parts.append("</body></html>")
    return "\n".join(parts) + "\n"


def _run_section(
    test: TestHistory,
    folder: FolderHistory,
    images: list[Image],
    pngs: Mapping[str, bytes],
    runs: list[Run] | None,
) -> list[str]:
    """A section; ``runs`` its folder's runs where the test was named, and
    then its table holds every value (:func:`values_table`)."""
    short = short_name(test.model_id)
    cell = change_cell(
        BaselineDiff(folder.folder, "changed", variables=_run_changes(folder))
    )
    out = [
        "<section>",
        f"<h2><code>{_e(short)}</code> · {_e(folder.folder)}</h2>",
        f'<p class="id"><code>{_e(test.model_id)}</code></p>',
        f"<table><tr><th>Test</th><th>Change, {_e(folder.folder)}</th></tr>"
        f"<tr><td><code>{_e(short)}</code></td><td>{_e(cell)}</td></tr></table>",
    ]
    out += [f'<p class="said">{_e(note)}</p>' for note in _section_notes(folder)]
    for image in images:
        if image.test != test.model_id or image.folder != folder.folder:
            continue
        data = base64.b64encode(pngs[image.path]).decode("ascii")
        caption = "<br>".join(_e(n) for n in image.notes)
        out.append(
            f'<figure><img alt="{_e(image.label)}" '
            f'src="data:image/png;base64,{data}">'
            + (f"<figcaption>{caption}</figcaption>" if caption else "")
            + "</figure>"
        )
    out += value_table(folder) if runs is None else values_table(folder, runs)
    # A named test's table holds every value: none is named twice.
    unchanged = [
        v.label
        for v in folder.variables
        if len(v.lines) > 1 and not v.moved and not v.flat and runs is None
    ]
    only: dict[str, list[str]] = {}
    for v in folder.variables:
        if len(v.lines) == 1 and not v.flat:
            only.setdefault(v.lines[0].revision.short, []).append(v.label)
    said = _not_drawn_of(
        [("unchanged", unchanged)]
        + [(f"only in {label}", labels) for label, labels in only.items()]
    )
    if said:
        out.append(f'<p class="said">{_e(said)}</p>')
    out.append("</section>")
    return out


# --- the output directory --------------------------------------------------------


def _listed(out: Path) -> set[str]:
    """The files the marker of ``out`` says the last run wrote, each inside
    ``out``; none where there is no marker or it cannot be read."""
    marker = out / MARKER
    if not marker.is_file():
        return set()
    try:
        names = json.loads(marker.read_text(encoding="utf-8")).get("files", [])
    except (ValueError, OSError, AttributeError):
        return {MARKER}
    listed = {MARKER}
    for name in names if isinstance(names, list) else []:
        if not isinstance(name, str):
            continue
        path = os.path.normpath(name).replace(os.sep, "/")
        if os.path.isabs(path) or path == ".." or path.startswith("../"):
            continue
        listed.add(path)
    return listed


def stale_files(out: Path, command: str) -> list[Path]:
    """The files a run into ``out`` replaces: none for a directory that is
    not there or is empty; every file of one the last run wrote, its
    marker included. Raises :class:`OutputRefused` for a file, or for a
    directory that holds anything else (a user's folder, ``-o .``), said
    as ``command``'s ("baseline plot")."""
    out = Path(out)
    if out.exists() and not out.is_dir():
        raise OutputRefused(f"{out} is a file: name a directory")
    if not out.exists():
        return []
    files = sorted(p for p in out.rglob("*") if p.is_file() or p.is_symlink())
    listed = _listed(out)
    foreign = [
        p.relative_to(out).as_posix()
        for p in files
        if p.relative_to(out).as_posix() not in listed
    ]
    if foreign:
        shown = ", ".join(foreign[:3])
        if len(foreign) > 3:
            shown += f" and {len(foreign) - 3} more"
        raise OutputRefused(
            f"{out} holds files {command} did not write ({shown}): "
            "name a new or empty directory"
        )
    return files


def clear(out: Path, files: list[Path]) -> None:
    """Remove ``files`` (:func:`stale_files`), then each folder under
    ``out`` that this leaves empty."""
    for path in files:
        path.unlink()
    for folder in sorted({p.parent for p in files}, key=lambda p: -len(p.parts)):
        while folder != out and folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
            folder = folder.parent


def write_marker(out: Path, files: list[str], command: str) -> None:
    """The list of what this run of ``command`` ("baseline plot") writes,
    before it writes any: what the next one may remove, even after this
    one stopped part way."""
    (Path(out) / MARKER).write_text(
        json.dumps({"written_by": f"dstf {command}", "files": files}, indent=1) + "\n",
        encoding="utf-8",
    )
