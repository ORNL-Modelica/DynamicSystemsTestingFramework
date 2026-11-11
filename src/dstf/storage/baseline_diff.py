"""What changed in a library's stored baselines between two revisions of
its reference root, or between a revision and the files on disk — the
facts ``dstf baseline diff`` prints. Data only: the words and the layout
are ``reporting.baseline_diff_text``'s.

Pairing. A baseline is a ``ref_*.json`` directly in a ``<Backend>/<os>/``
folder of the reference root (a soft check or a companion is deeper, and
is none). Two revisions' baselines are paired by MODEL ID inside each
folder, never by file name: before D165 a file was ``ref_NNNN.json``, after
it ``ref_<code>.json``. A model id held by two files of one folder at one
revision is said (``two baselines at …``) and neither is compared:
choosing one is a guess. A file whose body cannot be read is said
(``cannot be read at <rev> (<why>)``) and the run goes on; it is paired by
the model id its JSON names, or else by its path.

What counts as a change: the parts that score — ``time``, ``variables``,
``diagnostics``, ``simulation``, ``statistics``, ``sampling`` and
``comparison`` (its tolerance and overrides change the verdict). Dates, an
old ``test_id`` and key order do not: such a file is unchanged, and counted
in :attr:`Diff.metadata_only`.

How much a variable changed (:func:`compare_series`): the largest
``|new - old|`` with new read on OLD's grid, at old's points inside new's
time span (within one instant, `~dstf.comparison.algorithms.SAME_INSTANT`:
a float32 end time is the same end; the others are counted, not
compared), as a percent of OLD's
scale — the nrmse rule's (`~dstf.comparison.algorithms.error_scale`), so a
range under 1e-10 of the value is a constant and the percent is of its
value, and an old series of zeros reads the absolute difference. Values
are the codec's (a single-precision file reads through float32). Only the
stored samples are compared: what happened between them is not seen.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np

from ..comparison.algorithms import SAME_INSTANT, error_scale
from ..reporting.stats_rows import StatRow, statistics_rows
from .baseline import Baseline, ReferenceFile, StoredVariable
from .baseline_codec import (
    decode_reference,
    model_id_of,
    parse_body,
    shown_digits,
)
from .git_revisions import Commit, Repository

#: What a variable's change is a percent of: old's range, old's value (a
#: constant), or nothing — an old series of zeros, whose change is the
#: absolute difference.
Scale = Literal["range", "value", "absolute"]
#: A baseline of one test in one backend/os folder.
Status = Literal["changed", "new", "removed", "unreadable", "two baselines"]
#: The parts of a stored baseline whose change is a change of the baseline.
COMPARED_PARTS = (
    "time",
    "variables",
    "diagnostics",
    "simulation",
    "statistics",
    "sampling",
    "comparison",
)
#: The statistic the overview's effort figure is read from (Dymola writes
#: it; OpenModelica's statistics hold no step count).
STEPS = ("simulation", "accepted_steps")


class DiffError(Exception):
    """A request the reference root cannot answer; one sentence."""


@dataclass(frozen=True)
class SeriesChange:
    """How much one series changed against an older one."""

    #: the largest change: a percent of old's scale, or the absolute
    #: difference when ``scale`` is ``"absolute"``; None when no point of
    #: both sides was finite and compared
    largest: float | None
    scale: Scale
    #: old's time of the largest change
    at: float | None
    #: old's points compared: inside new's span, both sides finite
    compared: int = 0
    #: old's points outside new's time span: counted, not compared
    outside: int = 0
    #: points where a side is NaN or infinite and the two differ
    non_finite: int = 0
    #: old's time of the first of those
    non_finite_at: float | None = None
    #: why nothing was compared ("no time in the old baseline"); "" otherwise
    note: str = ""


@dataclass(frozen=True)
class VariableDiff:
    """One variable of a changed baseline, by its name."""

    name: str
    #: what a person reads: the newer side's expression, else its name
    label: str
    #: ``changed``: on both sides and compared (its change may be 0);
    #: ``new`` / ``removed``: on one side only
    status: Literal["changed", "new", "removed"]
    change: SeriesChange | None = None
    #: (old, new) when the expression under this name changed
    expression: tuple[str, str] | None = None


@dataclass(frozen=True)
class SettingChange:
    """A setting that differs: ``section`` is simulation, sampling or
    comparison; ``key`` is dotted below it; None is absent."""

    section: str
    key: str
    old: Any
    new: Any


@dataclass(frozen=True)
class BaselineDiff:
    """One test's baseline in one backend/os folder that is not unchanged."""

    folder: str
    status: Status
    old_file: str | None = None
    new_file: str | None = None
    #: what the row says for ``unreadable`` / ``two baselines``
    note: str = ""
    variables: list[VariableDiff] = field(default_factory=list)
    settings: list[SettingChange] = field(default_factory=list)
    #: the solver statistics, old as the baseline side and new as the run
    #: side (`reporting.stats_rows`), text rows left out; a ``time`` row is
    #: machine-dependent (:func:`machine_dependent`)
    effort: list[StatRow] = field(default_factory=list)
    #: ``accepted_steps`` (old, new) where both sides record it
    steps: tuple[float, float] | None = None


@dataclass(frozen=True)
class TestDiff:
    """One test whose baseline changed in at least one folder."""

    #: its model id — or, for a file whose model id cannot be read on
    #: either side, the file's path inside the reference root
    model_id: str
    baselines: dict[str, BaselineDiff]
    #: with ``against``: each other folder's newer baseline scored against
    #: that folder's newer one (``new``: only in the other folder,
    #: ``removed``: only in the ``against`` one)
    against: dict[str, list[VariableDiff]] = field(default_factory=dict)
    #: the folders where its baseline is unchanged
    unchanged: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Diff:
    """Every baseline that changed between two sides of a reference root."""

    root: Path
    old: Commit
    #: None: the files on disk
    new: Commit | None
    against: str | None
    #: every backend/os folder either side holds a baseline in: backends
    #: alphabetical, then OSes
    folders: list[str]
    tests: list[TestDiff]
    #: baselines whose compared parts are equal at both sides
    unchanged: int
    #: of those, files whose bytes differ (dates, an old test_id, key order)
    metadata_only: int


def machine_dependent(row: StatRow) -> bool:
    """A statistic that measures the machine, not the solver: every row the
    statistics rows class as a time (the ``timing`` section, ``cpu_time*``,
    ``*_wall``, ``simulation.wall_time``, the listed name ``CPUtime``)."""
    return row.kind == "time"


# --- one series against another ---------------------------------------------


def compare_series(
    old_time: np.ndarray,
    old_values: np.ndarray,
    new_time: np.ndarray,
    new_values: np.ndarray,
) -> SeriesChange:
    """How much ``new`` changed against ``old`` (the module docstring's
    rule). An event is two samples at one time: old's first sample there
    meets new's value just before the event, its second new's value just
    after (a straight line through the step reads a 0.1 % move as 100 %).
    A point where either side is NaN or infinite is a non-finite
    difference, unless both hold the same one."""
    to, yo = np.asarray(old_time, float), np.asarray(old_values, float)
    tn, yn = np.asarray(new_time, float), np.asarray(new_values, float)
    note = _unreadable_series(to, yo, "old") or _unreadable_series(tn, yn, "new")
    if note:
        return SeriesChange(largest=None, scale="absolute", at=None, note=note)

    # new on old's grid: after an event by default, before it for the first
    # of old's samples at a time it holds twice
    post = np.concatenate((np.diff(tn) != 0, [True]))
    pre = np.concatenate(([True], np.diff(tn) != 0))
    on_old = np.interp(to, tn[post], yn[post])
    repeats_next = np.concatenate((np.diff(to) == 0, [False]))
    repeats_prev = np.concatenate(([False], np.diff(to) == 0))
    first_of_event = repeats_next & ~repeats_prev
    on_old[first_of_event] = np.interp(to[first_of_event], tn[pre], yn[pre])

    # A time within one instant of new's ends is inside its span: a
    # single-precision run ends at float32(11.9) = 11.8999996, and old's
    # samples at 11.9 are that instant, not outside it.
    reach = SAME_INSTANT * max(float(tn[-1] - tn[0]), abs(tn[0]), abs(tn[-1]))
    inside = (to >= tn[0] - reach) & (to <= tn[-1] + reach)
    t, a, b = to[inside], yo[inside], on_old[inside]
    finite = np.isfinite(a) & np.isfinite(b)
    same = (a == b) | (np.isnan(a) & np.isnan(b))
    differs = ~finite & ~same

    finite_old = yo[np.isfinite(yo)]
    if finite_old.size:
        scale_value, constant = error_scale(finite_old)
        magnitude = float(np.max(np.abs(finite_old)))
        # error_scale divides an all-but-zero constant by 1.0: no scale
        absolute = constant and scale_value != magnitude
        scale: Scale = "absolute" if absolute else "value" if constant else "range"
    else:
        scale_value, scale = 1.0, "absolute"

    largest = at = None
    if finite.any():
        d = np.abs(b[finite] - a[finite])
        i = int(np.argmax(d))
        largest = (
            float(d[i]) if scale == "absolute" else float(d[i] / scale_value * 100)
        )
        at = float(t[finite][i])
    return SeriesChange(
        largest=largest,
        scale=scale,
        at=at,
        compared=int(finite.sum()),
        outside=int((~inside).sum()),
        non_finite=int(differs.sum()),
        non_finite_at=float(t[differs][0]) if differs.any() else None,
    )


def _unreadable_series(time: np.ndarray, values: np.ndarray, side: str) -> str:
    """Why a side's series cannot be compared, or ""."""
    if time.size == 0:
        return f"no time in the {side} baseline"
    if values.size != time.size:
        return f"the {side} baseline holds {values.size} values for {time.size} times"
    if not np.isfinite(time).all():
        return f"the {side} baseline's time is not finite"
    if np.any(np.diff(time) < 0):
        return f"the {side} baseline's time is not in order"
    return ""


def _variables(old: Baseline, new: Baseline) -> list[VariableDiff]:
    """Every variable of either side, by name: old's order, then new's."""
    olds = _by_name(old.variables)
    news = _by_name(new.variables)
    out: list[VariableDiff] = []
    for name, ov in olds.items():
        nv = news.get(name)
        if nv is None:
            out.append(VariableDiff(name, _label(ov), "removed"))
            continue
        change = compare_series(
            np.asarray(old.time, float),
            np.asarray(ov.values, float),
            np.asarray(new.time, float),
            np.asarray(nv.values, float),
        )
        said = (ov.expression or "", nv.expression or "")
        out.append(
            VariableDiff(
                name,
                _label(nv),
                "changed",
                change,
                expression=said if said[0] != said[1] else None,
            )
        )
    out += [VariableDiff(n, _label(v), "new") for n, v in news.items() if n not in olds]
    return out


def _by_name(variables: list[StoredVariable]) -> dict[str, StoredVariable]:
    """The first variable of each name (`Baseline.variable`'s rule)."""
    out: dict[str, StoredVariable] = {}
    for v in variables:
        out.setdefault(v.name, v)
    return out


def _label(v: StoredVariable) -> str:
    return v.expression or v.name


# --- what a side of the reference root holds ----------------------------------


@dataclass
class _File:
    """A baseline file at one side: its path inside the reference root,
    its bytes, and what its JSON says."""

    path: str
    raw: bytes
    model_id: str | None
    #: why its body cannot be read; "" when it can
    why: str

    @property
    def folder(self) -> str:
        return self.path.rsplit("/", 1)[0]

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[1]


def _is_baseline(path: str) -> bool:
    """A primary baseline: ``ref_*.json`` directly in ``<Backend>/<os>/``."""
    parts = path.split("/")
    return (
        len(parts) == 3 and parts[2].startswith("ref_") and parts[2].endswith(".json")
    )


def _file(path: str, raw: bytes) -> _File:
    try:
        body = parse_body(raw)
    except (ValueError, RecursionError) as e:  # JSONDecodeError, UndecodableBaseline
        return _File(path, raw, model_id_of(raw), str(e) or type(e).__name__)
    model_id = body.get("model_id")
    if not isinstance(model_id, str) or not model_id:
        return _File(path, raw, None, "it names no model id")
    return _File(path, raw, model_id, "")


def _at_commit(
    repo: Repository, commit: Commit, read: Callable[[str], bytes]
) -> list[_File]:
    parsed: dict[str, _File] = {}
    out = []
    for path, blob in sorted(repo.files(commit).items()):
        if not _is_baseline(path):
            continue
        if blob not in parsed:
            parsed[blob] = _file(path, read(blob))
        known = parsed[blob]
        out.append(_File(path, known.raw, known.model_id, known.why))
    return out


def _on_disk(root: Path) -> list[_File]:
    out = []
    for path in sorted(root.glob("*/*/ref_*.json")):
        if path.is_file():
            out.append(_file(path.relative_to(root).as_posix(), path.read_bytes()))
    return out


def _keyed(
    olds: list[_File], news: list[_File]
) -> dict[tuple[str, str], tuple[list[_File], list[_File]]]:
    """Both sides' files by (folder, model id). A file whose model id
    cannot be read takes the model id of the other side's file at its
    path, or else its own path as its key."""
    by_path = {
        "old": {f.path: f.model_id for f in olds if f.model_id},
        "new": {f.path: f.model_id for f in news if f.model_id},
    }
    out: dict[tuple[str, str], tuple[list[_File], list[_File]]] = defaultdict(
        lambda: ([], [])
    )
    for side, files, other in ((0, olds, "new"), (1, news, "old")):
        for f in files:
            model_id = f.model_id or by_path[other].get(f.path) or f.path
            out[(f.folder, model_id)][side].append(f)
    return out


# --- the diff ------------------------------------------------------------------


def diff_baselines(
    root: Path, rev1: str, rev2: str | None, *, against: str | None = None
) -> Diff:
    """Every baseline of the reference root ``root`` that changed between
    the revisions ``rev1`` and ``rev2`` — or, ``rev2`` None, the files on
    disk. Raises :class:`~dstf.storage.git_revisions.GitError` for what git
    cannot answer and :class:`DiffError` for an ``against`` folder the
    newer side holds no baseline in."""
    repo = Repository.holding(root)
    old = repo.commit(rev1)
    new = repo.commit(rev2) if rev2 is not None else None
    with repo.reader() as read:
        olds = _at_commit(repo, old, read)
        news = _at_commit(repo, new, read) if new is not None else _on_disk(Path(root))
    old_said = old.short
    new_said = new.short if new is not None else "the files on disk"
    folders = sorted(
        {f.folder for f in (*olds, *news)}, key=lambda f: tuple(f.split("/"))
    )
    if against is not None and against not in {f.folder for f in news}:
        held = ", ".join(
            sorted({f.folder for f in news}, key=lambda f: tuple(f.split("/")))
        )
        raise DiffError(
            f"--against {against}: no baselines in {against} at {new_said}"
            + (f" (it holds {held})" if held else "")
        )

    unchanged = metadata_only = 0
    changed: dict[str, dict[str, BaselineDiff]] = defaultdict(dict)
    same: dict[str, list[str]] = defaultdict(list)
    keyed = _keyed(olds, news)
    for (folder, model_id), (o, n) in keyed.items():
        if len(o) == len(n) and sorted(f.raw for f in o) == sorted(f.raw for f in n):
            unchanged += 1
            same[model_id].append(folder)
            continue
        got = _baseline(folder, o, n, old_said, new_said)
        if got is None:
            unchanged += 1
            metadata_only += 1
            same[model_id].append(folder)
            continue
        changed[model_id][folder] = got

    tests = []
    for model_id in sorted(changed):
        compared: dict[str, list[VariableDiff]] = {}
        if against is not None:
            compared = _against(model_id, against, keyed, folders)
        unchanged_in = sorted(same.get(model_id, []), key=lambda f: tuple(f.split("/")))
        tests.append(TestDiff(model_id, changed[model_id], compared, unchanged_in))
    return Diff(
        root=Path(root),
        old=old,
        new=new,
        against=against,
        folders=folders,
        tests=tests,
        unchanged=unchanged,
        metadata_only=metadata_only,
    )


def _baseline(
    folder: str, olds: list[_File], news: list[_File], old_said: str, new_said: str
) -> BaselineDiff | None:
    """One test's baseline in one folder, or None where its compared parts
    are equal (it differs in metadata only)."""
    old_file = olds[0].name if len(olds) == 1 else None
    new_file = news[0].name if len(news) == 1 else None
    if len(olds) > 1 or len(news) > 1:
        note = "; ".join(
            f"{_count(len(files))} baselines at {said}: "
            + ", ".join(f.name for f in files)
            for files, said in ((olds, old_said), (news, new_said))
            if len(files) > 1
        )
        return BaselineDiff(folder, "two baselines", note=note)
    unreadable = "; ".join(
        f"cannot be read at {said} ({files[0].why})"
        for files, said in ((olds, old_said), (news, new_said))
        if files and files[0].why
    )
    if unreadable:
        return BaselineDiff(folder, "unreadable", old_file, new_file, note=unreadable)
    if not olds:
        return BaselineDiff(folder, "new", new_file=new_file)
    if not news:
        return BaselineDiff(folder, "removed", old_file=old_file)
    a = decode_reference(parse_body(olds[0].raw))
    b = decode_reference(parse_body(news[0].raw))
    if all(_same(x, y) for x, y in zip(_parts(a), _parts(b), strict=True)):
        return None
    rows = statistics_rows(
        b.baseline.statistics,
        a.baseline.statistics,
        listing=sorted(
            {d.name for d in (*a.baseline.diagnostics, *b.baseline.diagnostics)}
        ),
        run_diagnostics=b.baseline.diagnostics,
        baseline_diagnostics=a.baseline.diagnostics,
        baseline_digits=shown_digits(a.sampling),
        run_digits=shown_digits(b.sampling),
    )
    return BaselineDiff(
        folder,
        "changed",
        old_file,
        new_file,
        variables=_variables(a.baseline, b.baseline),
        settings=_settings(a, b),
        effort=[row for row in rows if row.kind != "text"],
        steps=_steps(a.baseline.statistics, b.baseline.statistics),
    )


def _count(n: int) -> str:
    return {2: "two", 3: "three"}.get(n, str(n))


def _parts(ref: ReferenceFile) -> tuple:
    """The compared parts (:data:`COMPARED_PARTS`), in that order."""
    b = ref.baseline
    return (
        b.time,
        b.variables,
        b.diagnostics,
        b.simulation,
        b.statistics,
        ref.sampling,
        ref.comparison,
    )


def _same(a: Any, b: Any) -> bool:
    """Equal, a NaN equal to a NaN: a baseline that stores NaN at the same
    place on both sides has not changed there."""
    if a == b:
        return True
    if isinstance(a, float) and isinstance(b, float):
        return math.isnan(a) and math.isnan(b)
    if is_dataclass(a) and is_dataclass(b) and type(a) is type(b):
        return _same(asdict(a), asdict(b))  # type: ignore[arg-type]
    if isinstance(a, Mapping) and isinstance(b, Mapping):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list | tuple) and isinstance(b, list | tuple):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=True))
    return False


def _flat(section: Mapping, prefix: str = "") -> dict[str, Any]:
    """A section's settings by dotted key (``variable_overrides.x``)."""
    out: dict[str, Any] = {}
    for key, value in section.items():
        if isinstance(value, Mapping) and value and prefix == "":
            out.update(_flat(value, f"{key}."))
        else:
            out[f"{prefix}{key}"] = value
    return out


def _settings(a: ReferenceFile, b: ReferenceFile) -> list[SettingChange]:
    """The settings that differ: the simulation's, the sampling's and the
    comparison's (each section's keys sorted)."""
    sections = (
        ("simulation", a.baseline.simulation, b.baseline.simulation),
        (
            "sampling",
            asdict(a.sampling) if a.sampling else {},
            asdict(b.sampling) if b.sampling else {},
        ),
        ("comparison", a.comparison, b.comparison),
    )
    out = []
    for section, old, new in sections:
        o, n = _flat(old), _flat(new)
        for key in sorted({*o, *n}):
            if key in o and key in n and _same(o[key], n[key]):
                continue
            out.append(SettingChange(section, key, o.get(key), n.get(key)))
    return out


def _steps(old: Mapping, new: Mapping) -> tuple[float, float] | None:
    section, key = STEPS

    def read(stats: Mapping) -> float | None:
        got = stats.get(section)
        value = got.get(key) if isinstance(got, Mapping) else None
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        return float(value)

    o, n = read(old), read(new)
    return (o, n) if o is not None and n is not None else None


def _against(
    model_id: str,
    against: str,
    keyed: Mapping[tuple[str, str], tuple[list[_File], list[_File]]],
    folders: list[str],
) -> dict[str, list[VariableDiff]]:
    """Each other folder's newer baseline of ``model_id`` scored against
    the ``against`` folder's newer one; a folder where either is not one
    readable file is left out."""
    reference = _one_readable(keyed.get((against, model_id), ([], []))[1])
    if reference is None:
        return {}
    out = {}
    for folder in folders:
        if folder == against:
            continue
        other = _one_readable(keyed.get((folder, model_id), ([], []))[1])
        if other is not None:
            out[folder] = _variables(reference.baseline, other.baseline)
    return out


def _one_readable(files: list[_File]) -> ReferenceFile | None:
    if len(files) != 1 or files[0].why:
        return None
    return decode_reference(parse_body(files[0].raw))
