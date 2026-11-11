"""A test's stored baseline at several revisions of its reference root —
the facts ``dstf baseline plot`` draws. Data only: the figure is
:mod:`dstf.figures.history`'s, the images and the page ``reporting``'s.

Every revision is a git revision, in the order given; the first is the one
each later revision is measured against. Git is read through
:mod:`.git_revisions` only, one ``cat-file`` and one blob cache for every
revision. Baselines are paired by model id per backend/os folder and a
variable by name, then by label (:func:`~.baseline_diff.keyed_many`,
:func:`~.baseline_diff.pair_variables`), as ``baseline diff`` pairs them.

Per test and folder, a revision is skipped where the baseline is absent;
where it cannot be read, or the folder holds two baselines of the test, it
is skipped with its reason; and where its time and values equal the last
kept revision's (variables paired by the same rule) it is skipped and
counted. A folder is drawn only where two or more revisions are kept. Over
:data:`MAX_KEPT`, the first, the last and the ones that moved most against
the revision before them are kept, and the others are said.

Per variable: its series at each kept revision that holds it, and each
later one's :func:`~.baseline_diff.difference_series` against the first
that holds it — ``baseline diff``'s Change measure itself, so for two
revisions the largest finite point of a variable's difference is the
Change that command prints.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .baseline import Baseline, StoredVariable
from .baseline_codec import decode_reference, parse_body
from .baseline_diff import (
    BaselineFile,
    Diff,
    Difference,
    DiffError,
    SeriesChange,
    compare_series,
    diff_files,
    difference_series,
    files_at,
    folder_order,
    keyed_many,
    pair_variables,
    series_change,
    short_name,
)
from .git_revisions import Commit, Repository

#: The most revisions one folder of a test draws.
MAX_KEPT = 12


@dataclass(frozen=True)
class Revision:
    """A revision as it is drawn: the commit and its committer date."""

    commit: Commit
    #: ``YYYY-MM-DD``
    date: str


@dataclass(frozen=True)
class Line:
    """One variable at one kept revision."""

    revision: Revision
    name: str
    #: what a person reads: its expression, else its name
    label: str
    time: np.ndarray
    values: np.ndarray
    #: this line minus the variable's first line, on the first's grid;
    #: None for the first line itself
    difference: Difference | None = None
    #: the Change ``baseline diff`` would print for the first and this line
    change: SeriesChange | None = None


@dataclass(frozen=True)
class VariableHistory:
    """One variable across the kept revisions that hold it."""

    lines: list[Line]
    #: (revision's short hash, old name, new name) of each rename
    renames: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def label(self) -> str:
        """The newest line's label."""
        return self.lines[-1].label


@dataclass(frozen=True)
class FolderHistory:
    """One test's baseline in one backend/os folder, across the revisions."""

    folder: str
    kept: list[Revision]
    #: skipped: time and values equal to the last kept revision's
    equal: list[Revision]
    #: skipped: no baseline of the test in the folder
    absent: list[Revision]
    #: skipped, with the reason
    unreadable: list[tuple[Revision, str]]
    #: kept but left out: more than :data:`MAX_KEPT` were
    thinned: list[Revision]
    variables: list[VariableHistory]


@dataclass(frozen=True)
class TestHistory:
    """One test: each folder drawn (two or more kept revisions)."""

    model_id: str
    #: whether ``baseline diff`` first → last lists it
    listed: bool
    folders: list[FolderHistory]


@dataclass(frozen=True)
class History:
    """Every test drawn, the revisions, and ``baseline diff`` first → last."""

    root: Path
    revisions: list[Revision]
    diff: Diff
    tests: list[TestHistory]

    @property
    def images(self) -> int:
        """One per variable of each folder drawn."""
        return sum(len(f.variables) for t in self.tests for f in t.folders)


def baseline_history(
    root: Path,
    revs: list[str],
    *,
    tests: list[str] | None = None,
    folders: list[str] | None = None,
) -> History:
    """The baselines of the reference root ``root`` at the revisions
    ``revs``: of the tests ``tests`` names (each a short name or a full
    model id), else of every test ``baseline diff`` first → last lists as
    changed or new; in the folders ``folders`` names, else in every one.
    Raises :class:`~.git_revisions.GitError` for what git cannot answer and
    :class:`DiffError` for a name or a folder no baseline answers to."""
    repo = Repository.holding(root)
    commits = [repo.commit(rev) for rev in revs]
    revisions = [Revision(c, repo.date(c)) for c in commits]
    with repo.reader() as read:
        sides = files_at(repo, commits, read)
    diff = diff_files(Path(root), commits[0], commits[-1], sides[0], sides[-1])
    keyed = keyed_many(sides)

    held = sorted({folder for folder, _ in keyed}, key=folder_order)
    unknown = [f for f in folders or [] if f not in held]
    if unknown:
        them = "it" if len(unknown) == 1 else "them"
        raise DiffError(
            f"--folder {', '.join(unknown)}: no baseline in {them} at these "
            f"revisions (they hold {', '.join(held) or 'none'})"
        )
    drawn_folders = [f for f in held if folders is None or f in folders]

    ids = sorted({model_id for _, model_id in keyed})
    if tests is None:
        chosen = sorted(
            t.model_id
            for t in diff.tests
            if any(b.status in ("changed", "new") for b in t.baselines.values())
        )
    else:
        chosen = _named(tests, ids)
    listed = {t.model_id for t in diff.tests}

    decoded: dict[bytes, Baseline] = {}
    out = []
    for model_id in chosen:
        drawn = []
        for folder in drawn_folders:
            per_revision = keyed.get((folder, model_id))
            if per_revision is None:
                continue
            got = _folder(folder, list(zip(revisions, per_revision)), decoded)
            if got is not None:
                drawn.append(got)
        out.append(TestHistory(model_id, model_id in listed, drawn))
    return History(Path(root), revisions, diff, out)


def _named(names: list[str], ids: list[str]) -> list[str]:
    """The model ids the names match, each a short name or a full model id,
    in model-id order; a name that matches none is refused."""
    chosen: set[str] = set()
    unknown = []
    for name in names:
        got = {m for m in ids if name in (m, short_name(m))}
        if not got:
            unknown.append(name)
        chosen |= got
    if unknown:
        such = (
            f"a short name such as {short_name(ids[0])}, or a full model id"
            if ids
            else "they hold none"
        )
        raise DiffError(
            f"--test: no baseline at these revisions is of a test named "
            f"{', '.join(unknown)} ({such})"
        )
    return sorted(chosen)


def _folder(
    folder: str,
    per_revision: list[tuple[Revision, list[BaselineFile]]],
    decoded: dict[bytes, Baseline],
) -> FolderHistory | None:
    """One test's baseline in one folder across the revisions, or None
    where fewer than two revisions are kept."""
    kept: list[tuple[Revision, Baseline]] = []
    equal: list[Revision] = []
    absent: list[Revision] = []
    unreadable: list[tuple[Revision, str]] = []
    for revision, files in per_revision:
        if not files:
            absent.append(revision)
        elif len(files) > 1:
            names = ", ".join(f.name for f in files)
            unreadable.append((revision, f"two baselines of the test: {names}"))
        elif files[0].why:
            unreadable.append((revision, files[0].why))
        else:
            raw = files[0].raw
            if raw not in decoded:
                decoded[raw] = decode_reference(parse_body(raw)).baseline
            baseline = decoded[raw]
            if kept and _same_values(kept[-1][1], baseline):
                equal.append(revision)
            else:
                kept.append((revision, baseline))
    if len(kept) < 2:
        return None
    kept, thinned = _thin(kept)
    return FolderHistory(
        folder=folder,
        kept=[revision for revision, _ in kept],
        equal=equal,
        absent=absent,
        unreadable=unreadable,
        thinned=thinned,
        variables=_variables(kept),
    )


def _same_values(a: Baseline, b: Baseline) -> bool:
    """The same time and, variable by variable, the same values — paired
    as ``baseline diff`` pairs them, so a rename alone is no change."""
    if not _equal(a.time, b.time):
        return False
    return all(
        old is not None and new is not None and _equal(old.values, new.values)
        for old, new in pair_variables(a.variables, b.variables)
    )


def _equal(a: list[float], b: list[float]) -> bool:
    return np.array_equal(np.asarray(a, float), np.asarray(b, float), equal_nan=True)


def _thin(
    kept: list[tuple[Revision, Baseline]],
) -> tuple[list[tuple[Revision, Baseline]], list[Revision]]:
    """At most :data:`MAX_KEPT`: the first, the last, and the ones between
    that moved most against the kept revision before them."""
    if len(kept) <= MAX_KEPT:
        return kept, []
    middle = range(1, len(kept) - 1)
    moves = {i: _move(kept[i - 1][1], kept[i][1]) for i in middle}
    best = sorted(middle, key=lambda i: moves[i], reverse=True)[: MAX_KEPT - 2]
    keep = {0, len(kept) - 1, *best}
    return (
        [k for i, k in enumerate(kept) if i in keep],
        [revision for i, (revision, _) in enumerate(kept) if i not in keep],
    )


def _move(a: Baseline, b: Baseline) -> tuple[int, float, float]:
    """How much ``b`` moved against ``a``: any non-finite difference first,
    then the largest percent, then the largest absolute difference (the
    order ``baseline diff`` ranks its tests in)."""
    non_finite, pct, absolute = 0, -1.0, -1.0
    for old, new in pair_variables(a.variables, b.variables):
        if old is None or new is None:
            continue
        c = compare_series(
            np.asarray(a.time, float),
            np.asarray(old.values, float),
            np.asarray(b.time, float),
            np.asarray(new.values, float),
        )
        non_finite = max(non_finite, int(c.non_finite > 0))
        if c.largest is None:
            continue
        if c.scale == "absolute":
            absolute = max(absolute, c.largest)
        else:
            pct = max(pct, c.largest)
    return (non_finite, pct, absolute)


@dataclass
class _Chain:
    """A variable as it is followed across the kept revisions: its newest
    stored entry, and its entries so far."""

    newest: StoredVariable
    held: list[tuple[Revision, Baseline, StoredVariable]]
    renames: list[tuple[str, str, str]] = field(default_factory=list)


def _variables(kept: list[tuple[Revision, Baseline]]) -> list[VariableHistory]:
    """Each variable of any kept revision: each revision paired against the
    newest entry of every variable seen so far (so a rename at any revision
    is followed), in the order they first appear."""
    chains: list[_Chain] = []
    for revision, baseline in kept:
        newest = {id(c.newest): c for c in chains}
        pairs = pair_variables([c.newest for c in chains], baseline.variables)
        for old, new in pairs:
            if new is None:
                continue
            if old is None:
                chains.append(_Chain(new, [(revision, baseline, new)]))
                continue
            chain = newest[id(old)]
            if old.name != new.name:
                chain.renames.append((revision.commit.short, old.name, new.name))
            chain.newest = new
            chain.held.append((revision, baseline, new))
    return [VariableHistory(_lines(c.held), c.renames) for c in chains]


def _lines(held: list[tuple[Revision, Baseline, StoredVariable]]) -> list[Line]:
    out: list[Line] = []
    for revision, baseline, v in held:
        time = np.asarray(baseline.time, float)
        values = np.asarray(v.values, float)
        label = v.expression or v.name
        if not out:
            out.append(Line(revision, v.name, label, time, values))
            continue
        first = out[0]
        d = difference_series(first.time, first.values, time, values)
        out.append(
            Line(revision, v.name, label, time, values, d, change=series_change(d))
        )
    return out
