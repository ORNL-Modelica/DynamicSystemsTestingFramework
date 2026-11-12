"""Every variable of two runs of one tool, test by test — the facts ``dstf
diff`` prints. Data only: the words and the layout are
:mod:`.run_diff_text`'s. It is the run-folder twin of
:mod:`dstf.storage.baseline_diff`, as :mod:`.run_history` (``dstf plot``) is
of :mod:`dstf.storage.baseline_history`.

A run is a work dir, named ``LABEL=DIR``, read with no configuration and no
library (:func:`~.run_history.read_runs`): its manifest names its tests,
its status.json the backend and the ``<Backend>/<os>`` they are of. Runs
pair within one backend/os; of two labels, the first given is the
reference. Nothing is written into a run.

Which tests: those both runs of a folder hold, by model id (``--test``
narrows them). A test whose run failed or timed out is not compared ("the
run failed"), nor one a run started and did not finish, was stopped in, or
holds no readable result of ("not run"): a partial result file is never
compared. A test only one run holds is said, per side.

What is compared: the WHOLE result file of each test, every variable, read
by the backend that wrote it (``result_names`` / ``result_series``; this
module names no file). A backend whose file holds the tracked variables
alone (``saves_tracked_signals_only``) is said. The solver's statistics in
the file (``result_statistics``) are taken out first and never scored:
their finals are listed old → new, with the run's own statistics
(``run_stats.json``).

A group is the names whose values are bit-identical in BOTH runs: an alias
in one run that is not in the other splits. A group is shown by its
shortest name and counted once. A group that holds one value at every
stored point in both runs is a constant (a parameter): identical (NaN
equals NaN), within rounding (one storage step of the coarser file's
precision at the larger value), or it differs. Any other group falls in the
first of: identical (every compared point equal and none left uncompared,
on one grid or two), within rounding (every compared point within one
storage step there, none left uncompared), near zero (only with a cut:
every value of both runs smaller in size), derivative (a name of the group
holds ``der(``), changed. A value finite on one side only, or a series with
nothing compared, is always changed. The Change is
:func:`~dstf.storage.baseline_diff.difference_series`'s, DSTF's one measure
of a change: the later run on the reference's grid, events paired, as a
percent of the reference's range (of its value where constant; absolute
where zero).
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import numpy as np

from ..storage.baseline_diff import (
    Difference,
    SeriesChange,
    VariableDiff,
    difference_series,
    series_change,
)
from ..storage.baseline_history import Run, named_tests
from .baseline_diff_text import change_rank
from .run_history import RunDir, RunError, paired_folders, read_runs
from .stats_rows import StatRow, statistics_rows

if TYPE_CHECKING:
    from ..backends.base import Backend

#: A varying group's class, in the order a group is tried against them.
Kind = Literal["identical", "rounding", "near zero", "derivative", "changed"]
KINDS: tuple[Kind, ...] = (
    "identical",
    "rounding",
    "near zero",
    "derivative",
    "changed",
)
#: A constant's class.
ConstantKind = Literal["identical", "rounding", "differs"]
CONSTANT_KINDS: tuple[ConstantKind, ...] = ("identical", "rounding", "differs")
#: A side's state of a test: its result was read; else why it is not
#: compared.
READ = "read"
NOT_RUN = "not run"
RUN_FAILED = "the run failed"
NOT_HELD = "not held"


@dataclass(frozen=True)
class Signal:
    """A varying group that is not identical."""

    #: every name of the group, the shortest first: the one shown
    names: list[str]
    kind: Kind
    change: SeriesChange
    #: the largest |later − first| at a compared point; None for none
    largest_absolute: float | None
    #: the reference's time of it
    largest_absolute_at: float | None

    @property
    def name(self) -> str:
        return self.names[0]


@dataclass(frozen=True)
class Constant:
    """A constant group that is not identical."""

    names: list[str]
    kind: ConstantKind
    old: float
    new: float

    @property
    def name(self) -> str:
        return self.names[0]


@dataclass(frozen=True)
class TestRunDiff:
    """One test both runs of a folder hold and compared."""

    model_id: str
    #: the names each file holds, statistics apart: (first, later)
    variables: tuple[int, int]
    #: each file's time points: (first, later)
    points: tuple[int, int]
    #: the names one run's file holds and the other's does not, statistics
    #: apart, sorted
    only_old: list[str]
    only_new: list[str]
    #: the varying groups by class, every class present
    signal_counts: dict[str, int]
    #: every varying group that is not identical, largest change first
    #: within each class
    signals: list[Signal]
    #: the constant groups by class, every class present
    constant_counts: dict[str, int]
    #: every constant group that is not identical, in name order
    constants: list[Constant]
    #: the solver's statistics, old as the baseline side and new as the run
    #: side (`reporting.stats_rows`): the run's own and the result file's
    #: finals; text rows left out
    statistics: list[StatRow] = field(default_factory=list)

    def of(self, kind: Kind) -> list[Signal]:
        return [s for s in self.signals if s.kind == kind]

    @property
    def same(self) -> bool:
        """Whether nothing differs: every group identical, no name in one
        run only."""
        return not (self.signals or self.constants or self.only_old or self.only_new)


@dataclass(frozen=True)
class Uncompared:
    """A test either run of a folder holds that is not compared: each
    side's state — :data:`READ`, :data:`NOT_RUN`, :data:`RUN_FAILED` or
    :data:`NOT_HELD`."""

    model_id: str
    old: str
    new: str


@dataclass(frozen=True)
class FolderDiff:
    """The later run of one backend/os against its reference."""

    folder: str
    old: Run
    new: Run
    #: the backend keeps the tracked variables alone in a result file
    tracked_only: bool
    tests: list[TestRunDiff]
    uncompared: list[Uncompared]

    @property
    def held(self) -> int:
        """The tests either run holds (of those asked for)."""
        return len(self.tests) + len(self.uncompared)


@dataclass(frozen=True)
class RunDiff:
    """Two runs, each backend/os of both compared."""

    #: the two labels, the reference first
    labels: tuple[str, str]
    folders: list[FolderDiff]
    #: each backend/os given one run only, and that run: not compared
    alone: list[tuple[str, Run]]
    #: the near-zero cut (``--near-zero``); None for none
    near_zero: float | None = None


def run_diff(
    runs: list[tuple[str, Path]],
    *,
    tests: list[str] | None = None,
    folders: list[str] | None = None,
    near_zero: float | None = None,
) -> RunDiff:
    """The runs ``runs`` (``(label, dir)`` in the order given, two labels)
    compared variable by variable: of the tests ``tests`` names (each a
    short name or a full model id), else every one both runs hold; in the
    folders ``folders`` names, else in every one of two runs. Raises
    :class:`~.run_history.RunError` for a run that cannot be read, a third
    label, two runs of one label and folder, no folder of two runs, runs
    of a folder with no test in common, or a name no run answers to."""
    labels = list(dict.fromkeys(label for label, _ in runs))
    if len(labels) > 2:
        raise RunError(
            f"diff compares two runs; give two labels (a third: {labels[2]})"
        )
    read = read_runs(runs, lambda run: None)
    paired, single = paired_folders(
        {f: [run.label for run, _ in each] for f, each in read.items()},
        folders,
        "diff compares two runs",
    )
    pairs = {f: (read[f][0][0], read[f][1][0]) for f in paired}
    for folder, (old, new) in pairs.items():
        if not old.held & new.held:
            raise RunError(f"the runs of {folder} hold no test in common")
    ids = sorted({m for old, new in pairs.values() for m in old.held | new.held})
    chosen = None if tests is None else set(named_tests(tests, ids, "run given"))
    return RunDiff(
        labels=(labels[0], labels[1]),
        folders=[
            _folder(folder, old, new, chosen, near_zero)
            for folder, (old, new) in pairs.items()
        ],
        alone=[(f, read[f][0][0].run) for f in single],
        near_zero=near_zero,
    )


def _states(run: RunDir) -> dict[str, tuple[str, Path | None]]:
    """Each model id the run holds: :data:`READ` and its test's directory
    where its result may be read, else why not (:data:`NOT_RUN`, :data:`RUN_FAILED`).
    The verdicts are the run's own (`run_verdicts`), read from the
    manifest as given: ``BatchManifest.load`` moves aside a manifest it
    cannot read, and a run is never written."""
    from ..backends.base import run_verdicts
    from ..storage.batch_manifest import NO_RESULT_RUN_STATUSES, BatchManifest

    failed = run_verdicts(
        run.directory, [BatchManifest(work_dir=run.directory, manifest=run.entries)]
    )
    out: dict[str, tuple[str, Path | None]] = {}
    for key, entry in run.entries.items():
        model_id = entry["model_id"]
        if entry.get("last_run_status") in NO_RESULT_RUN_STATUSES:
            out[model_id] = (NOT_RUN, None)
        elif model_id in failed:
            out[model_id] = (RUN_FAILED, None)
        else:
            out[model_id] = (READ, run.directory / key)
    return out


def _backend(run: RunDir) -> type[Backend]:
    from ..backends import backend_class, backend_family

    cls = backend_class(backend_family(run.backend_name))
    assert cls is not None  # read_run refuses a backend not registered
    return cls


def _folder(
    folder: str,
    old: RunDir,
    new: RunDir,
    chosen: set[str] | None,
    near_zero: float | None,
) -> FolderDiff:
    old_states, new_states = _states(old), _states(new)
    old_cls, new_cls = _backend(old), _backend(new)
    compared: list[TestRunDiff] = []
    uncompared: list[Uncompared] = []
    for model_id in sorted(old_states.keys() | new_states.keys()):
        if chosen is not None and model_id not in chosen:
            continue
        old_state, old_dir = old_states.get(model_id, (NOT_HELD, None))
        new_state, new_dir = new_states.get(model_id, (NOT_HELD, None))
        test = None
        if old_dir is not None and new_dir is not None:
            # one test read at a time, and let go before the next: a
            # result file of 100,000 variables is gigabytes
            test, old_state, new_state = _test(
                model_id, (old_cls, old_dir), (new_cls, new_dir), near_zero
            )
        if test is None:
            uncompared.append(Uncompared(model_id, old_state, new_state))
        else:
            compared.append(test)
    return FolderDiff(
        folder=folder,
        old=old.run,
        new=new.run,
        tracked_only=old_cls.saves_tracked_signals_only
        or new_cls.saves_tracked_signals_only,
        tests=compared,
        uncompared=uncompared,
    )


#: The values of a result file by name, and its time.
_Read = tuple[np.ndarray, dict[str, np.ndarray], dict[str, np.ndarray]]


def _read(cls: type[Backend], test_dir: Path) -> _Read | None:
    """A test's result file whole: its time, its variables and its
    statistics by name (``cls.is_result_statistic``); None where the file
    is not there or cannot be read."""
    names = cls.result_names(test_dir)
    if names is None:
        return None
    series = cls.result_series(test_dir, names)
    if not series:
        return None
    time = next(iter(series.values()))[0]
    variables, statistics = {}, {}
    for name, (_, values) in series.items():
        (statistics if cls.is_result_statistic(name) else variables)[name] = values
    return np.asarray(time), variables, statistics


def _test(
    model_id: str,
    old: tuple[type[Backend], Path],
    new: tuple[type[Backend], Path],
    near_zero: float | None,
) -> tuple[TestRunDiff | None, str, str]:
    """One test's two result files compared; None, with each side's
    state, where a side holds no readable file."""
    a, b = _read(*old), _read(*new)
    if a is None or b is None:
        return None, READ if a else NOT_RUN, READ if b else NOT_RUN
    (ta, va, sa), (tb, vb, sb) = a, b
    shared = [n for n in va if n in vb]
    groups: dict[tuple[bytes, bytes], list[str]] = {}
    for name in shared:
        groups.setdefault((_digest(va[name]), _digest(vb[name])), []).append(name)
    same_grid = ta.shape == tb.shape and bool(np.array_equal(ta, tb))
    signal_counts: Counter[str] = Counter({k: 0 for k in KINDS})
    constant_counts: Counter[str] = Counter({k: 0 for k in CONSTANT_KINDS})
    signals: list[Signal] = []
    constants: list[Constant] = []
    for members in groups.values():
        names = sorted(members, key=lambda n: (len(n), n))
        x, y = va[names[0]], vb[names[0]]
        if _constant(x) and _constant(y):
            kind = _constant_kind(x, y)
            constant_counts[kind] += 1
            if kind != "identical":
                constants.append(Constant(names, kind, float(x[0]), float(y[0])))
            continue
        if same_grid and np.array_equal(x, y, equal_nan=True):
            signal_counts["identical"] += 1
            continue
        signal = _signal(names, (ta, x), (tb, y), near_zero)
        signal_counts[signal.kind] += 1
        if signal.kind != "identical":
            signals.append(signal)
    return (
        TestRunDiff(
            model_id=model_id,
            variables=(len(va), len(vb)),
            points=(ta.size, tb.size),
            only_old=sorted(n for n in va if n not in vb),
            only_new=sorted(n for n in vb if n not in va),
            signal_counts=dict(signal_counts),
            signals=sorted(signals, key=signal_order),
            constant_counts=dict(constant_counts),
            constants=sorted(constants, key=lambda c: c.name),
            statistics=_statistics(model_id, old[1], new[1], sa, sb),
        ),
        READ,
        READ,
    )


def _digest(values: np.ndarray) -> bytes:
    return hashlib.blake2b(
        np.ascontiguousarray(values).tobytes(), digest_size=16
    ).digest()


def _constant(values: np.ndarray) -> bool:
    """One value at every stored point (NaN equals NaN)."""
    if values.size == 0:
        return False
    first = values[0]
    return bool(np.all((values == first) | (np.isnan(values) & np.isnan(first))))


def _coarser(x: np.ndarray, y: np.ndarray) -> type[np.floating]:
    """The coarser precision of two arrays as read: a float32 file's step is
    its own, whatever the other's."""
    return np.float32 if np.float32 in (x.dtype.type, y.dtype.type) else np.float64


def _step(size: np.ndarray, precision: type[np.floating]) -> np.ndarray:
    """One storage step at ``size`` (≥ 0) in ``precision``."""
    return np.spacing(np.asarray(size).astype(precision)).astype(float)


def _constant_kind(x: np.ndarray, y: np.ndarray) -> ConstantKind:
    a, b = float(x[0]), float(y[0])
    if a == b or (np.isnan(a) and np.isnan(b)):
        return "identical"
    size = max(abs(a), abs(b))
    if np.isfinite(size) and abs(a - b) <= _step(np.array(size), _coarser(x, y)):
        return "rounding"
    return "differs"


def _signal(
    names: list[str],
    old: tuple[np.ndarray, np.ndarray],
    new: tuple[np.ndarray, np.ndarray],
    near_zero: float | None,
) -> Signal:
    """A varying group's class (the module docstring's order) and its
    Change, by its first name's two series."""
    (ta, x), (tb, y) = old, new
    d = difference_series(ta, x, tb, y, kind="run")
    change = series_change(d)
    compared = np.abs(d.delta[d.finite])
    largest = at = None
    if compared.size:
        i = int(np.argmax(compared))
        largest, at = float(compared[i]), float(d.time[d.finite][i])
    whole = not (change.note or change.outside or change.uneven or change.non_finite)
    kind: Kind
    if whole and (largest is None or largest == 0):
        kind = "identical"
    elif whole and _within_rounding(d, x, y):
        kind = "rounding"
    elif change.note or change.non_finite:
        kind = "changed"
    elif near_zero is not None and _magnitude(x, y) < near_zero:
        kind = "near zero"
    elif any("der(" in n for n in names):
        kind = "derivative"
    else:
        kind = "changed"
    return Signal(names, kind, change, largest, at)


def _within_rounding(d: Difference, x: np.ndarray, y: np.ndarray) -> bool:
    """Every compared point within one storage step of the coarser file's
    precision at the larger of its two values."""
    old = np.asarray(x, float)[d.finite]
    delta = d.delta[d.finite]
    size = np.fmax(np.abs(old), np.abs(old + delta))
    return bool(np.all(np.abs(delta) <= _step(size, _coarser(x, y))))


def _magnitude(x: np.ndarray, y: np.ndarray) -> float:
    """The largest size of a value either run holds; NaN for none."""
    sizes = [np.nanmax(np.abs(v)) for v in (x, y) if v.size and not np.isnan(v).all()]
    return float(max(sizes)) if sizes else float("nan")


def signal_order(s: Signal) -> tuple:
    """Within a class, the largest change first: the rank ``baseline diff``
    sorts by (any value not finite, then the percent, then the absolute
    difference) — for near zero, whose percent of a range of nothing says
    little, the largest absolute difference."""
    if s.kind == "near zero":
        return (KINDS.index(s.kind), 0, -(s.largest_absolute or 0.0), 0.0, s.name)
    nf, pct, absolute = change_rank([VariableDiff(s.name, s.name, "changed", s.change)])
    return (KINDS.index(s.kind), -nf, -pct, -absolute, s.name)


@dataclass(frozen=True)
class _Final:
    """A result-file statistic as a listed name's row reads it."""

    name: str
    values: np.ndarray


def _statistics(
    model_id: str,
    old_dir: Path,
    new_dir: Path,
    old: dict[str, np.ndarray],
    new: dict[str, np.ndarray],
) -> list[StatRow]:
    """The two runs' statistics, old → new: each run's own
    (``run_stats.json``) and the finals of the result files' statistics."""
    from ..storage.baseline_codec import DOUBLE_SHOWN, recorded_digits
    from ..storage.run_stats import read_run_stats

    def kept(test_dir: Path) -> dict | None:
        stats = read_run_stats(test_dir, model_id)
        return None if stats is None else stats["statistics"]

    digits = recorded_digits(next(iter(old.values()))) if old else DOUBLE_SHOWN
    rows = statistics_rows(
        kept(new_dir),
        kept(old_dir),
        listing=sorted(old.keys() | new.keys()),
        run_diagnostics=[_Final(n, v) for n, v in new.items()],
        baseline_diagnostics=[_Final(n, v) for n, v in old.items()],
        baseline_digits=digits,
    )
    return [row for row in rows if row.kind != "text"]
