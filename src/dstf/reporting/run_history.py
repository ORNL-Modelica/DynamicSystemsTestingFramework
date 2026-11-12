"""Two runs or more of a library side by side, test by test — the facts
``dstf plot`` draws. Data only: the images and the page are
:mod:`.baseline_gallery`'s, the per-variable model
:mod:`dstf.storage.baseline_history`'s, so a variable of two runs is
measured exactly as a variable of two revisions.

A run is a work dir, named ``LABEL=DIR``. It is read as ``dstf compare``
reads one, and only read: its ``batch_manifest.json`` says which tests it
ran, its ``status.json`` metadata the backend that ran them and the
``<Backend>/<os>`` folder they are of (:func:`~.results_summary.run_folder`,
``summary``'s rule), and that backend's ``read_last_results`` reads them,
under the CURRENT ``testing.json`` and spec — the tests discovery finds, the
variables they track, each run's verdict applied. Nothing is written into a
run: no default ``testing.json``, no wipe, no stamp.

Runs pair only within one backend/os folder, in the order their labels were
first given; the first is the one every later run is measured against. A
result whose run failed is never a line, whatever its file holds; a test a
run holds no result of is "not run". A run's numbers are its own, so no
rounding of saved digits is read into them. Every run is a line: none is
skipped as equal to another, and none is left out.

Which tests: those named — drawn where any run holds them — else every test
with a variable that moved or that only some runs hold, or a run that
failed or holds no result, in a folder of two runs or more that hold it.
Every other test two runs of a folder hold is named as one where nothing
moved.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from ..storage.baseline import Baseline, StoredVariable
from ..storage.baseline_codec import recorded_digits
from ..storage.baseline_diff import DiffError, folder_order
from ..storage.baseline_history import (
    FolderHistory,
    Run,
    Source,
    TestHistory,
    named_tests,
    variable_histories,
)
from .results_summary import reason, run_folder

if TYPE_CHECKING:
    from ..config import Config

#: How a run is read: its configuration, from its work dir and the name of
#: the backend that ran it.
ConfigFor = Callable[[Path, str], "Config"]

#: The file a run's work dir holds its tests in.
MANIFEST = "batch_manifest.json"


class RunError(DiffError):
    """A run that cannot be read as asked; one sentence."""


@dataclass(frozen=True)
class RunHistory:
    """The runs, each test drawn, and the tests where nothing moved."""

    #: each backend/os of two runs or more, its runs in label order
    folders: dict[str, list[Run]]
    #: each backend/os given one run only, and that run: not drawn
    alone: list[tuple[str, Run]]
    tests: list[TestHistory]
    #: every other test two runs of a folder hold, by model id
    nothing_moved: list[str]
    #: the tests were named (``--test``): each section's table holds every
    #: tracked value
    named: bool = False


def _run_folder_name(directory: Path) -> str:
    return directory.name or directory.resolve().name


def _read_one(
    label: str,
    directory: Path,
    config_for: ConfigFor,
    discovered: dict[str, list],
) -> tuple[str, Run, dict, set[str]]:
    """One ``LABEL=DIR``: its folder, its line's source, its results of the
    discovered tests, and the model ids its manifest names."""
    from ..backends import (
        BackendUnavailableError,
        backend_class,
        backend_family,
        select_backend,
    )
    from ..backends.progress import read_status
    from ..discovery.test_registry import discover_tests

    given = f"{label}={directory}"
    if not (directory / MANIFEST).is_file():
        raise RunError(
            f"{given}: not a run's work dir (no {MANIFEST} in it; `dstf run` "
            "writes one)"
        )
    held = _manifest_ids(given, directory / MANIFEST)
    status = read_status(directory)
    if status is None and (directory / "status.json").exists():
        raise RunError(
            f"{given}: its status.json cannot be read, so the backend and os "
            "that ran it are not known"
        )
    if status is None:
        raise RunError(
            f"{given}: no status.json in it to say which backend and os ran it"
        )
    meta = status.get("metadata")
    folder = run_folder(meta)
    backend_name = meta.get("backend_name") if isinstance(meta, dict) else None
    if folder is None or not (isinstance(backend_name, str) and backend_name):
        raise RunError(
            f"{given}: its status.json names no backend and os (written before "
            "a run recorded them)"
        )
    if backend_class(backend_family(backend_name)) is None:
        raise RunError(
            f"{given}: its run used backend '{backend_name}', which is not "
            "registered here"
        )
    config = config_for(directory, backend_name)
    if backend_name not in discovered:
        discovered[backend_name] = discover_tests(config)
    tests = discovered[backend_name]
    ids = {t.model_id for t in tests}
    if not held & ids:
        raise RunError(
            f"{given}: its run holds none of this library's tests (a run of "
            "another library?)"
        )
    try:
        backend = select_backend(config, persistent=False)
    except BackendUnavailableError as e:
        raise RunError(f"{given}: {e}") from e
    backend.quiet = True
    results = backend.read_last_results(tests)
    run = Run(label, _run_folder_name(directory))
    return folder, run, {m: r for m, r in results.items() if m in ids}, held & ids


def _manifest_ids(given: str, path: Path) -> set[str]:
    """The model ids a run's manifest names. Read here, not by
    ``BatchManifest.load``, which moves a manifest it cannot read aside:
    a run is never written."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise RunError(f"{given}: its {MANIFEST} cannot be read ({e})") from e
    if not isinstance(data, dict):
        raise RunError(f"{given}: its {MANIFEST} holds no JSON object")
    ids = set()
    for key, entry in data.items():
        # an old manifest names its model alone; a newer one in an object
        model_id = entry.get("model_id") if isinstance(entry, dict) else entry
        if not (isinstance(model_id, str) and model_id):
            raise RunError(
                f"{given}: its {MANIFEST} holds an entry with no model id ({key})"
            )
        ids.add(model_id)
    return ids


def _baseline(result) -> tuple[Baseline, dict[str, int]]:
    """A run's result as a baseline on its one shared time (as accept
    stores it), and the digits each variable's array holds."""
    variables = result.variables
    time = [float(t) for t in variables[0].time] if variables else []
    digits = {v.name: recorded_digits(np.asarray(v.values)) for v in variables}
    stored = [
        StoredVariable(v.index, v.name, v.expression, [float(x) for x in v.values])
        for v in variables
    ]
    return Baseline("primary", time, stored), digits


def run_history(
    runs: list[tuple[str, Path]],
    config_for: ConfigFor,
    *,
    tests: list[str] | None = None,
    folders: list[str] | None = None,
) -> RunHistory:
    """The runs ``runs`` (``(label, dir)`` in the order given), each read
    under ``config_for(dir, backend_name)``: of the tests ``tests`` names
    (each a short name or a full model id), else of every test that moved
    or failed (the module's rule); in the folders ``folders`` names, else
    in every one. Raises :class:`RunError` for a run that cannot be read,
    two runs of one label and folder, a folder of fewer than two runs, or
    a name no run answers to."""
    discovered: dict[str, list] = {}
    by_folder: dict[str, list[tuple[Run, dict, set[str]]]] = {}
    seen: dict[tuple[str, str], Path] = {}
    dirs: dict[Path, str] = {}
    for label, directory in runs:
        directory = Path(directory)
        where = directory.resolve()
        if where in dirs:
            raise RunError(
                f"{label}={directory}: this directory is given twice (also as "
                f"{dirs[where]})"
            )
        dirs[where] = label
        folder, run, results, held = _read_one(label, directory, config_for, discovered)
        if (label, folder) in seen:
            raise RunError(
                f"{label}: two runs of {folder}, {seen[(label, folder)]} and "
                f"{directory}: give each its own label"
            )
        seen[(label, folder)] = directory
        by_folder.setdefault(folder, []).append((run, results, held))
    # Per folder, in the order the labels were first given: the reference
    # is the run of the earliest label that has one there, whatever order
    # the arguments name each folder's runs in.
    order = list(dict.fromkeys(label for label, _ in runs))
    for read in by_folder.values():
        read.sort(key=lambda r: order.index(r[0].label))

    held_folders = sorted(by_folder, key=folder_order)
    unknown = [f for f in folders or [] if f not in by_folder]
    if unknown:
        them = "it" if len(unknown) == 1 else "them"
        raise RunError(
            f"--folder {', '.join(unknown)}: no run of {them} (the runs are of "
            f"{', '.join(held_folders)})"
        )
    chosen_folders = [f for f in held_folders if folders is None or f in folders]
    drawn = {f: by_folder[f] for f in chosen_folders if len(by_folder[f]) >= 2}
    alone = [(f, by_folder[f][0][0]) for f in chosen_folders if len(by_folder[f]) < 2]
    if not drawn:
        given = ", ".join(
            f"{f} ({', '.join(run.label for run, _, _ in by_folder[f])})"
            for f in chosen_folders
        )
        raise RunError(
            f"plot draws two runs or more of one backend/os; there is one of "
            f"each: {given}"
        )

    ids = sorted({m for f in drawn.values() for _, _, held in f for m in held})
    named = None if tests is None else named_tests(tests, ids, "run given")
    out: list[TestHistory] = []
    nothing: list[str] = []
    for model_id in ids if named is None else named:
        sections = [
            s
            for s in (
                _section(folder, model_id, read, named=named is not None)
                for folder, read in drawn.items()
            )
            if s is not None
        ]
        if named is not None:
            out.append(TestHistory(model_id, True, sections))
            continue
        shown = [s for s in sections if _differs(s)]
        if shown:
            out.append(TestHistory(model_id, True, shown))
        elif sections:
            nothing.append(model_id)
    return RunHistory(
        folders={f: [run for run, _, _ in read] for f, read in drawn.items()},
        alone=alone,
        tests=out,
        nothing_moved=nothing,
        named=named is not None,
    )


def _differs(section: FolderHistory) -> bool:
    """Whether a section shows a difference: a run that failed or holds no
    result, a variable that moved, or one some run drawn does not hold."""
    return bool(
        section.failed
        or section.not_run
        or any(v.moved or len(v.lines) < len(section.kept) for v in section.variables)
    )


def _section(
    folder: str,
    model_id: str,
    read: list[tuple[Run, dict, set[str]]],
    *,
    named: bool,
) -> FolderHistory | None:
    """One test in one folder across its runs; None where fewer than two
    of them hold it — or, for a test named, where none does."""
    holding = [(run, results) for run, results, held in read if model_id in held]
    if len(holding) < (1 if named else 2):
        return None
    lines: list[tuple[Source, Baseline]] = []
    shown: dict[tuple[Source, str], int] = {}
    failed: list[tuple[Source, str]] = []
    not_run: list[Source] = []
    for run, results in [(run, results) for run, results, _ in read]:
        result = results.get(model_id)
        if result is None:
            not_run.append(run)
        elif not result.success:
            failed.append((run, reason({"message": result.error_message})))
        else:
            baseline, digits = _baseline(result)
            lines.append((run, baseline))
            shown |= {(run, name): d for name, d in digits.items()}
    return FolderHistory(
        folder=folder,
        kept=[source for source, _ in lines],
        equal=[],
        absent=[],
        unreadable=[],
        thinned=[],
        variables=(
            variable_histories(lines, {s: None for s, _ in lines}, shown)
            if len(lines) >= 2
            else []
        ),
        failed=failed,
        not_run=not_run,
    )
