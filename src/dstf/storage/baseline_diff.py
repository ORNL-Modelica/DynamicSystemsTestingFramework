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

A baseline's variables are paired by NAME, the variable's identity (D132);
the ones left over on each side are then paired by label (``expression or
name``, read without white space) where that label is held by one variable
on each side: a library that renamed a variable and kept what it reads
(``PHS.p`` became ``unitTests.x[1]`` with ``PHS.p`` as its expression) is
compared across the rename, which is said (:attr:`VariableDiff.renamed`).

What counts as a change: the parts that score — ``time``, ``variables``,
``diagnostics``, ``simulation``, ``statistics``, ``sampling`` and
``comparison`` (its tolerance and overrides change the verdict). Dates, an
old ``test_id`` and key order do not: such a file is unchanged, and counted
in :attr:`Diff.metadata_only`. ``sampling`` is compared only where both
sides record it: a file written before it existed holds none, and the next
accept writes it, so one side without it is old data
(:attr:`BaselineDiff.sampling_first`), not a change. Of ``sampling``, the
setting and the precision are settings; the point counts are what the run
produced (:attr:`BaselineDiff.samples`).

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

A change no larger than the rounding of the files is no move
(:attr:`SeriesChange.within_saved_digits`): where every compared point
differs by at most one step in the last digit the coarser of the two files
was saved with (`~.baseline_codec.saved_step`, at
`~.baseline_codec.shown_digits`: 7 for a single-precision file or one with
no ``sampling``, 15 for a double one), the change is the files' rounding —
a baseline accepted again at another precision, its numbers kept. Its
size is still the true largest difference; the words say it as none.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, is_dataclass, replace
from pathlib import Path
from typing import Any, Literal

import numpy as np

from ..comparison.algorithms import SAME_INSTANT, error_scale
from ..reporting.stats_rows import StatRow, statistics_rows
from .baseline import Baseline, ReferenceFile, Sampling, StoredVariable
from .baseline_codec import (
    decode_reference,
    model_id_of,
    parse_body,
    saved_step,
    shown_digits,
)
from .git_revisions import Commit, Repository

#: What a variable's change is a percent of: old's range, old's value (a
#: constant), or nothing — an old series of zeros, whose change is the
#: absolute difference.
Scale = Literal["range", "value", "absolute"]
#: A baseline of one test in one backend/os folder.
Status = Literal["changed", "new", "removed", "unreadable", "two baselines"]
#: The parts of a stored baseline whose change is a change of the baseline
#: (``sampling`` only where both sides record it).
COMPARED_PARTS = (
    "time",
    "variables",
    "diagnostics",
    "simulation",
    "statistics",
    "sampling",
    "comparison",
)
#: Of ``sampling``, the keys that are settings; the others (the point
#: counts) are what the run produced.
SAMPLING_SETTINGS = ("setting", "precision")
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
    #: old's samples in the middle of an event (a time it stores three
    #: times or more) that new stores another number of times there: no
    #: sample of new's to meet, so counted, not compared
    uneven: int = 0
    #: points where a side is NaN or infinite and the two differ
    non_finite: int = 0
    #: old's time of the first of those
    non_finite_at: float | None = None
    #: why nothing was compared ("no time in the old baseline"); "" otherwise
    note: str = ""
    #: the change is the files' rounding: some point moved, and every one
    #: compared by no more than one step in the last digit the coarser
    #: file was saved with (`~.baseline_codec.saved_step`); every point
    #: compared and each finite on both sides. ``largest`` keeps its size.
    within_saved_digits: bool = False


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
    #: (old name, new name) of a variable paired by its label (``name`` is
    #: the new one); None for one paired by its name
    renamed: tuple[str, str] | None = None


@dataclass(frozen=True)
class SettingChange:
    """A setting that differs: ``section`` is simulation, sampling or
    comparison; ``key`` is dotted below it; None is absent. (Also a
    sampling point count that differs, in :attr:`BaselineDiff.samples`.)"""

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
    #: the sampling point counts that differ (``source_points``,
    #: ``stored_points``): what the run produced, no setting
    samples: list[SettingChange] = field(default_factory=list)
    #: the newer side's sampling where the older side records none (old
    #: data, written before it existed): said, not compared
    sampling_first: Sampling | None = None
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


def short_name(model_id: str) -> str:
    """A test's short name: the last part of its model id (a path, for a
    file whose model id cannot be read, whole)."""
    return model_id.rsplit(".", 1)[-1] if "/" not in model_id else model_id


def folder_order(folder: str) -> tuple[str, ...]:
    """The order folders are listed in: backends alphabetical, then OSes."""
    return tuple(folder.split("/"))


def machine_dependent(row: StatRow) -> bool:
    """A statistic that measures the machine, not the solver: every row the
    statistics rows class as a time (the ``timing`` section, ``cpu_time*``,
    ``*_wall``, ``simulation.wall_time``, the listed name ``CPUtime``)."""
    return row.kind == "time"


# --- one series against another ---------------------------------------------


@dataclass(frozen=True)
class Difference:
    """``new - old`` at each of old's points, paired as :func:`compare_series`
    pairs them: the series a change is the largest of, and what the change
    panel of ``baseline plot`` draws."""

    #: old's time
    time: np.ndarray
    #: new read on old's grid, minus old; NaN at old's points outside new's span
    delta: np.ndarray
    scale: Scale
    #: what a percent is of: old's range or value (1.0 for ``absolute``)
    scale_value: float
    #: old's points inside new's time span (within one instant)
    inside: np.ndarray
    #: of those, the points where both sides are finite: the compared ones
    #: (an :attr:`uneven` point is none)
    finite: np.ndarray
    #: of those inside, the points where a side is NaN or infinite and the
    #: two differ
    differs: np.ndarray
    #: why nothing was compared; "" otherwise
    note: str = ""
    #: of those inside, the middle samples of an event that new stores
    #: another number of times (:attr:`SeriesChange.uneven`); None: none
    uneven: np.ndarray | None = None
    #: the larger of ``|old|`` and ``|new on old's grid|`` at each of old's
    #: points: what a step of the saved digits is a step of; None: none
    magnitude: np.ndarray | None = None

    @property
    def scaled(self) -> np.ndarray:
        """``delta`` as a change is said: a percent of the scale, or the
        absolute difference."""
        if self.scale == "absolute":
            return self.delta
        return self.delta / self.scale_value * 100


def difference_series(
    old_time: np.ndarray,
    old_values: np.ndarray,
    new_time: np.ndarray,
    new_values: np.ndarray,
) -> Difference:
    """``new - old`` on old's grid (the module docstring's rule). An event
    is two samples or more at one time: old's first sample there meets
    new's value just before the event, its last new's value just after (a
    straight line through the step reads a 0.1 % move as 100 %), and one
    between them new's sample of the same place at that time where new
    stores it as many times — else that one is not compared
    (:attr:`Difference.uneven`). A point where either side is NaN or
    infinite is a non-finite difference, unless both hold the same one."""
    to, yo = np.asarray(old_time, float), np.asarray(old_values, float)
    tn, yn = np.asarray(new_time, float), np.asarray(new_values, float)
    note = _unreadable_series(to, yo, "old") or _unreadable_series(tn, yn, "new")
    if note:
        none = np.zeros(0, bool)
        return Difference(to[:0], to[:0], "absolute", 1.0, none, none, none, note)

    # new on old's grid: after an event by default, before it for the first
    # of old's samples at a time it holds twice
    post = np.concatenate((np.diff(tn) != 0, [True]))
    pre = np.concatenate(([True], np.diff(tn) != 0))
    with np.errstate(invalid="ignore"):  # a line to or from an infinity
        on_old = np.interp(to, tn[post], yn[post])
        repeats_next = np.concatenate((np.diff(to) == 0, [False]))
        repeats_prev = np.concatenate(([False], np.diff(to) == 0))
        first_of_event = repeats_next & ~repeats_prev
        on_old[first_of_event] = np.interp(to[first_of_event], tn[pre], yn[pre])
    # Between old's first and last sample at a time stored three times or
    # more: new's sample of the same place there, else nothing to meet. By
    # new's value just after, an identical series read 100 %.
    uneven = np.zeros(to.size, bool)
    middle = repeats_next & repeats_prev
    if middle.any():
        new_runs = dict(zip(*_runs(tn)))
        for at, (start, count) in zip(*_runs(to)):
            if count < 3:
                continue
            between = slice(start + 1, start + count - 1)
            partner = new_runs.get(at)
            if partner is not None and partner[1] == count:
                on_old[between] = yn[partner[0] + 1 : partner[0] + count - 1]
            else:
                uneven[between] = True

    # A time within one instant of new's ends is inside its span: a
    # single-precision run ends at float32(11.9) = 11.8999996, and old's
    # samples at 11.9 are that instant, not outside it.
    reach = SAME_INSTANT * max(float(tn[-1] - tn[0]), abs(tn[0]), abs(tn[-1]))
    inside = (to >= tn[0] - reach) & (to <= tn[-1] + reach)
    uneven &= inside
    met = inside & ~uneven
    finite = met & np.isfinite(yo) & np.isfinite(on_old)
    same = (yo == on_old) | (np.isnan(yo) & np.isnan(on_old))
    differs = met & ~finite & ~same

    finite_old = yo[np.isfinite(yo)]
    if finite_old.size:
        scale_value, constant = error_scale(finite_old)
        magnitude = float(np.max(np.abs(finite_old)))
        # error_scale divides an all-but-zero constant by 1.0: no scale
        absolute = constant and scale_value != magnitude
        scale: Scale = "absolute" if absolute else "value" if constant else "range"
    else:
        scale_value, scale = 1.0, "absolute"

    with np.errstate(invalid="ignore"):  # inf - inf
        delta = np.where(met, on_old - yo, np.nan)
    return Difference(
        to,
        delta,
        scale,
        float(scale_value),
        inside,
        finite,
        differs,
        uneven=uneven,
        magnitude=np.fmax(np.abs(yo), np.abs(on_old)),
    )


def _runs(time: np.ndarray) -> tuple[list[float], list[tuple[int, int]]]:
    """Each time of ``time`` once, and where its samples start and how many
    there are (``time`` in order)."""
    starts = np.flatnonzero(np.concatenate(([True], np.diff(time) != 0)))
    counts = np.diff(np.append(starts, time.size))
    return (
        [float(time[s]) for s in starts],
        [(int(s), int(c)) for s, c in zip(starts, counts)],
    )


def series_change(d: Difference, digits: int | None = None) -> SeriesChange:
    """The change a difference series says: its largest compared point, as
    a percent of the scale (or absolute), and what was not compared; with
    ``digits``, the significant digits of the coarser file, whether it is
    the files' rounding (:attr:`SeriesChange.within_saved_digits`)."""
    if d.note:
        return SeriesChange(largest=None, scale="absolute", at=None, note=d.note)
    largest = at = None
    if d.finite.any():
        size = np.abs(d.delta[d.finite])
        i = int(np.argmax(size))
        largest = (
            float(size[i])
            if d.scale == "absolute"
            else float(size[i] / d.scale_value * 100)
        )
        at = float(d.time[d.finite][i])
    change = SeriesChange(
        largest=largest,
        scale=d.scale,
        at=at,
        compared=int(d.finite.sum()),
        outside=int((~d.inside).sum()),
        uneven=0 if d.uneven is None else int(d.uneven.sum()),
        non_finite=int(d.differs.sum()),
        non_finite_at=float(d.time[d.differs][0]) if d.differs.any() else None,
    )
    if digits is None or not _rounding_only(d, change, digits):
        return change
    return replace(change, within_saved_digits=True)


def _rounding_only(d: Difference, change: SeriesChange, digits: int) -> bool:
    """Whether a change is the files' rounding at ``digits``: some point
    moved, every point compared and finite on both sides, and each by no
    more than one step of those digits."""
    if (
        not change.largest
        or change.non_finite
        or change.outside
        or change.uneven
        or change.note
        or d.magnitude is None
    ):
        return False
    step = saved_step(d.magnitude[d.finite], digits)
    return bool(np.all(np.abs(d.delta[d.finite]) <= step))


def compare_series(
    old_time: np.ndarray,
    old_values: np.ndarray,
    new_time: np.ndarray,
    new_values: np.ndarray,
    digits: int | None = None,
) -> SeriesChange:
    """How much ``new`` changed against ``old``: the largest point of their
    :func:`difference_series`; with ``digits`` (the coarser file's), whether
    that is the files' rounding (:func:`series_change`)."""
    return series_change(
        difference_series(old_time, old_values, new_time, new_values), digits
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


def pair_variables(
    olds: list[StoredVariable], news: list[StoredVariable]
) -> list[tuple[StoredVariable | None, StoredVariable | None]]:
    """Every variable of either side as (old, new), None on the side that
    lacks it: old's order, then new's. Paired by name; then each left over
    on both sides with a label (``expression or name``, read without white
    space: :func:`_label_key`) that one variable of each side holds. A pair
    of two names is a rename."""
    old_by, new_by = _by_name(olds), _by_name(news)
    old_labels = Counter(_label_key(v) for v in old_by.values())
    new_labels = Counter(_label_key(v) for v in new_by.values())
    left = {
        _label_key(v): v
        for name, v in new_by.items()
        if name not in old_by and new_labels[_label_key(v)] == 1
    }
    partner: dict[str, StoredVariable] = {}
    for name, v in old_by.items():
        label = _label_key(v)
        if name not in new_by and old_labels[label] == 1 and label in left:
            partner[name] = left.pop(label)
    taken = {v.name for v in partner.values()}
    out: list[tuple[StoredVariable | None, StoredVariable | None]] = [
        (v, new_by[name] if name in new_by else partner.get(name))
        for name, v in old_by.items()
    ]
    out += [
        (None, v)
        for name, v in new_by.items()
        if name not in old_by and name not in taken
    ]
    return out


def saved_digits(a: ReferenceFile, b: ReferenceFile) -> int:
    """The significant digits the coarser of two files was saved with: a
    difference within one step of them is the files' rounding."""
    return min(shown_digits(a.sampling), shown_digits(b.sampling))


def _variables(old: Baseline, new: Baseline, digits: int) -> list[VariableDiff]:
    """Every variable of either side, paired by :func:`pair_variables`:
    old's order, then new's; ``digits`` the coarser file's."""
    out: list[VariableDiff] = []
    for ov, nv in pair_variables(old.variables, new.variables):
        if nv is None:
            assert ov is not None
            out.append(VariableDiff(ov.name, _label(ov), "removed"))
            continue
        if ov is None:
            out.append(VariableDiff(nv.name, _label(nv), "new"))
            continue
        change = compare_series(
            np.asarray(old.time, float),
            np.asarray(ov.values, float),
            np.asarray(new.time, float),
            np.asarray(nv.values, float),
            digits,
        )
        renamed = (ov.name, nv.name) if ov.name != nv.name else None
        said = (ov.expression or "", nv.expression or "")
        out.append(
            VariableDiff(
                nv.name,
                _label(nv),
                "changed",
                change,
                expression=said if said[0] != said[1] and renamed is None else None,
                renamed=renamed,
            )
        )
    return out


def _by_name(variables: list[StoredVariable]) -> dict[str, StoredVariable]:
    """The first variable of each name (`Baseline.variable`'s rule)."""
    out: dict[str, StoredVariable] = {}
    for v in variables:
        out.setdefault(v.name, v)
    return out


def _label(v: StoredVariable) -> str:
    return v.expression or v.name


def _label_key(v: StoredVariable) -> str:
    """The label a variable is paired by: its label with no white space. A
    library's rename wrote its old names as expressions in its own spelling
    (``alphas[1,1]`` as ``alphas[1, 1]``, a ``cat(`` over several lines),
    and white space changes nothing an expression reads."""
    return "".join(_label(v).split())


# --- what a side of the reference root holds ----------------------------------


@dataclass
class BaselineFile:
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


def _file(path: str, raw: bytes) -> BaselineFile:
    try:
        body = parse_body(raw)
    except (ValueError, RecursionError) as e:  # JSONDecodeError, UndecodableBaseline
        return BaselineFile(path, raw, model_id_of(raw), str(e) or type(e).__name__)
    model_id = body.get("model_id")
    if not isinstance(model_id, str) or not model_id:
        return BaselineFile(path, raw, None, "it names no model id")
    return BaselineFile(path, raw, model_id, "")


def files_at(
    repo: Repository, commits: list[Commit], read: Callable[[str], bytes]
) -> list[list[BaselineFile]]:
    """Each commit's baseline files, one list per commit; a blob held at
    several commits is read and parsed once."""
    parsed: dict[str, BaselineFile] = {}
    sides = []
    for commit in commits:
        out = []
        for path, blob in sorted(repo.files(commit).items()):
            if not _is_baseline(path):
                continue
            if blob not in parsed:
                parsed[blob] = _file(path, read(blob))
            known = parsed[blob]
            out.append(BaselineFile(path, known.raw, known.model_id, known.why))
        sides.append(out)
    return sides


def _on_disk(root: Path) -> list[BaselineFile]:
    out = []
    for path in sorted(root.glob("*/*/ref_*.json")):
        if path.is_file():
            out.append(_file(path.relative_to(root).as_posix(), path.read_bytes()))
    return out


def keyed_many(
    sides: list[list[BaselineFile]],
) -> dict[tuple[str, str], list[list[BaselineFile]]]:
    """Every side's files by (folder, model id), one list per side. A file
    whose model id cannot be read takes the model id another side's file
    at its path names — the nearest side's, the earlier first on a tie —
    or else its own path as its key."""
    by_path = [{f.path: f.model_id for f in files if f.model_id} for files in sides]
    out: dict[tuple[str, str], list[list[BaselineFile]]] = defaultdict(
        lambda: [[] for _ in sides]
    )
    for i, files in enumerate(sides):
        others = sorted(
            (j for j in range(len(sides)) if j != i), key=lambda j: abs(j - i)
        )
        for f in files:
            model_id = f.model_id or next(
                (by_path[j][f.path] for j in others if f.path in by_path[j]), f.path
            )
            out[(f.folder, model_id)][i].append(f)
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
        if new is None:
            (olds,) = files_at(repo, [old], read)
            news = _on_disk(Path(root))
        else:
            olds, news = files_at(repo, [old, new], read)
    return diff_files(root, old, new, olds, news, against=against)


def diff_files(
    root: Path,
    old: Commit,
    new: Commit | None,
    olds: list[BaselineFile],
    news: list[BaselineFile],
    *,
    against: str | None = None,
) -> Diff:
    """:func:`diff_baselines` of files already read: ``olds`` at ``old``,
    ``news`` at ``new`` (None: on disk)."""
    old_said = old.short
    new_said = new.short if new is not None else "the files on disk"
    folders = sorted({f.folder for f in (*olds, *news)}, key=folder_order)
    if against is not None and against not in {f.folder for f in news}:
        held = ", ".join(sorted({f.folder for f in news}, key=folder_order))
        raise DiffError(
            f"--against {against}: no baselines in {against} at {new_said}"
            + (f" (it holds {held})" if held else "")
        )

    unchanged = metadata_only = 0
    changed: dict[str, dict[str, BaselineDiff]] = defaultdict(dict)
    same: dict[str, list[str]] = defaultdict(list)
    keyed = keyed_many([olds, news])
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
        unchanged_in = sorted(same.get(model_id, []), key=folder_order)
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
    folder: str,
    olds: list[BaselineFile],
    news: list[BaselineFile],
    old_said: str,
    new_said: str,
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
    both = a.sampling is not None and b.sampling is not None
    if all(_same(x, y) for x, y in zip(_parts(a, both), _parts(b, both), strict=True)):
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
        variables=_variables(a.baseline, b.baseline, saved_digits(a, b)),
        settings=_settings(a, b),
        samples=_samples(a.sampling, b.sampling),
        sampling_first=b.sampling if a.sampling is None else None,
        effort=[row for row in rows if row.kind != "text"],
        steps=_steps(a.baseline.statistics, b.baseline.statistics),
    )


def _count(n: int) -> str:
    return {2: "two", 3: "three"}.get(n, str(n))


def _parts(ref: ReferenceFile, sampling: bool) -> tuple:
    """The compared parts (:data:`COMPARED_PARTS`), in that order;
    ``sampling`` False (a side records none) leaves it out."""
    b = ref.baseline
    return (
        b.time,
        b.variables,
        b.diagnostics,
        b.simulation,
        b.statistics,
        ref.sampling if sampling else None,
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
    """The settings that differ: the simulation's, the sampling's (its
    :data:`SAMPLING_SETTINGS`, where both sides record it) and the
    comparison's (each section's keys sorted)."""
    sampled = a.sampling is not None and b.sampling is not None
    sections = (
        ("simulation", a.baseline.simulation, b.baseline.simulation),
        (
            "sampling",
            _sampling_part(a.sampling, SAMPLING_SETTINGS) if sampled else {},
            _sampling_part(b.sampling, SAMPLING_SETTINGS) if sampled else {},
        ),
        ("comparison", a.comparison, b.comparison),
    )
    return [c for section, old, new in sections for c in _changes(section, old, new)]


def _samples(old: Sampling | None, new: Sampling | None) -> list[SettingChange]:
    """The sampling point counts that differ, where both sides record it."""
    if old is None or new is None:
        return []
    counts = tuple(k for k in asdict(old) if k not in SAMPLING_SETTINGS)
    return _changes(
        "sampling", _sampling_part(old, counts), _sampling_part(new, counts)
    )


def _sampling_part(sampling: Sampling | None, keys: tuple[str, ...]) -> dict:
    got = asdict(sampling) if sampling is not None else {}
    return {k: got[k] for k in keys if k in got}


def _changes(section: str, old: Mapping, new: Mapping) -> list[SettingChange]:
    """One section's keys that differ, sorted."""
    o, n = _flat(old), _flat(new)
    return [
        SettingChange(section, key, o.get(key), n.get(key))
        for key in sorted({*o, *n})
        if not (key in o and key in n and _same(o[key], n[key]))
    ]


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
    keyed: Mapping[tuple[str, str], list[list[BaselineFile]]],
    folders: list[str],
) -> dict[str, list[VariableDiff]]:
    """Each other folder's newer baseline of ``model_id`` scored against
    the ``against`` folder's newer one; a folder where either is not one
    readable file is left out."""
    reference = _one_readable(keyed.get((against, model_id), [[], []])[1])
    if reference is None:
        return {}
    out = {}
    for folder in folders:
        if folder == against:
            continue
        other = _one_readable(keyed.get((folder, model_id), [[], []])[1])
        if other is not None:
            out[folder] = _variables(
                reference.baseline, other.baseline, saved_digits(reference, other)
            )
    return out


def _one_readable(files: list[BaselineFile]) -> ReferenceFile | None:
    if len(files) != 1 or files[0].why:
        return None
    return decode_reference(parse_body(files[0].raw))
