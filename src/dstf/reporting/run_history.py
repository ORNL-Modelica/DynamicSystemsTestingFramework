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
run: no default ``testing.json``, no wipe, no stamp. What is read of a run
with no configuration (:func:`read_run`, :func:`read_runs`,
:func:`paired_folders`) is ``dstf diff``'s too (:mod:`.run_diff`).

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
moved, and a test one run of a folder holds alone as one only in that run —
the tests of the current spec only: a test it no longer lists is not named.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
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
    #: every model id a drawn run holds: what a test's name is told apart in
    held: list[str]
    #: without tests named, each folder's tests (of the current spec) one
    #: run of it holds alone:
    #: (folder, that run's label, the model ids), folders in folder order,
    #: labels in label order; not drawn
    only: list[tuple[str, str, list[str]]]
    #: the tests were named (``--test``): each section's table holds every
    #: tracked value
    named: bool = False


def _run_folder_name(directory: Path) -> str:
    return directory.name or directory.resolve().name


@dataclass(frozen=True)
class RunDir:
    """A run's work dir as it is read with no configuration: what ``plot``
    and ``diff`` both read of it first."""

    label: str
    directory: Path
    #: its ``<Backend>/<os>`` (:func:`~.results_summary.run_folder`)
    folder: str
    #: the name of the backend that ran it, as its status.json says it
    backend_name: str
    #: its manifest: each test key's entry, an old manifest's bare model id
    #: read as ``{"model_id": ...}``
    entries: dict[str, dict]

    @property
    def held(self) -> set[str]:
        """The model ids its manifest names."""
        return {entry["model_id"] for entry in self.entries.values()}

    @property
    def run(self) -> Run:
        """Its line's source: its label and its folder's name."""
        return Run(self.label, _run_folder_name(self.directory))


def read_run(label: str, directory: Path) -> RunDir:
    """One ``LABEL=DIR`` read with no configuration: its manifest, and the
    backend and the folder its status.json names. Raises :class:`RunError`
    for a directory that is no run's work dir, a manifest or status.json
    that cannot be read, a status.json that names no backend and os, and a
    backend not registered here. Nothing is written."""
    from ..backends import backend_class, backend_family
    from ..backends.progress import read_status

    given = f"{label}={directory}"
    if not (directory / MANIFEST).is_file():
        raise RunError(
            f"{given}: not a run's work dir (no {MANIFEST} in it; `dstf run` "
            "writes one)"
        )
    entries = _manifest_entries(given, directory / MANIFEST)
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
    return RunDir(label, directory, folder, backend_name, entries)


def _manifest_entries(given: str, path: Path) -> dict[str, dict]:
    """A run's manifest, each entry an object with its model id. Read here,
    not by ``BatchManifest.load``, which moves a manifest it cannot read
    aside: a run is never written."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise RunError(f"{given}: its {MANIFEST} cannot be read ({e})") from e
    if not isinstance(data, dict):
        raise RunError(f"{given}: its {MANIFEST} holds no JSON object")
    entries = {}
    for key, entry in data.items():
        # an old manifest names its model alone; a newer one in an object
        model_id = entry.get("model_id") if isinstance(entry, dict) else entry
        if not (isinstance(model_id, str) and model_id):
            raise RunError(
                f"{given}: its {MANIFEST} holds an entry with no model id ({key})"
            )
        entries[key] = entry if isinstance(entry, dict) else {"model_id": model_id}
    return entries


def read_runs[T](
    runs: list[tuple[str, Path]], then: Callable[[RunDir], T]
) -> dict[str, list[tuple[RunDir, T]]]:
    """The runs ``runs`` (``(label, dir)`` in the order given), each read
    by :func:`read_run` and then by ``then``, by ``<Backend>/<os>`` — each
    folder's runs in the order their labels were first given, so the first
    is the reference whatever order the arguments name a folder's runs in.
    Raises :class:`RunError` for a directory given twice and for two runs
    of one label and folder, as well as for what the two readers refuse."""
    by_folder: dict[str, list[tuple[RunDir, T]]] = {}
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
        run = read_run(label, directory)
        read = then(run)
        if (label, run.folder) in seen:
            raise RunError(
                f"{label}: two runs of {run.folder}, {seen[(label, run.folder)]} "
                f"and {directory}: give each its own label"
            )
        seen[(label, run.folder)] = directory
        by_folder.setdefault(run.folder, []).append((run, read))
    order = list(dict.fromkeys(label for label, _ in runs))
    for read_here in by_folder.values():
        read_here.sort(key=lambda r: order.index(r[0].label))
    return by_folder


def paired_folders(
    labels: Mapping[str, list[str]],
    folders: list[str] | None,
    command: str,
) -> tuple[list[str], list[str]]:
    """Of the folders the runs are of (``labels``: each folder's runs'
    labels, in order) — those ``folders`` names, else every one — the ones
    of two runs or more and the ones of one, each in folder order. Raises
    :class:`RunError` for a ``folders`` name no run is of, and where no
    chosen folder has two runs (``command`` is what the sentence says needs
    them: "plot draws two runs or more")."""
    held_folders = sorted(labels, key=folder_order)
    unknown = [f for f in folders or [] if f not in labels]
    if unknown:
        them = "it" if len(unknown) == 1 else "them"
        raise RunError(
            f"--folder {', '.join(unknown)}: no run of {them} (the runs are of "
            f"{', '.join(held_folders)})"
        )
    chosen = [f for f in held_folders if folders is None or f in folders]
    paired = [f for f in chosen if len(labels[f]) >= 2]
    if not paired:
        given = ", ".join(f"{f} ({', '.join(labels[f])})" for f in chosen)
        raise RunError(f"{command} of one backend/os; there is one of each: {given}")
    return paired, [f for f in chosen if len(labels[f]) < 2]


def _read_results(
    run: RunDir, config_for: ConfigFor, discovered: dict[str, list]
) -> tuple[dict, set[str]]:
    """A run's results of the discovered tests, read under its
    configuration, and the model ids its manifest names of them."""
    from ..backends import BackendUnavailableError, select_backend
    from ..discovery.test_registry import discover_tests

    given = f"{run.label}={run.directory}"
    config = config_for(run.directory, run.backend_name)
    if run.backend_name not in discovered:
        discovered[run.backend_name] = discover_tests(config)
    tests = discovered[run.backend_name]
    ids = {t.model_id for t in tests}
    if not run.held & ids:
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
    return {m: r for m, r in results.items() if m in ids}, run.held & ids


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
    read = read_runs(runs, lambda run: _read_results(run, config_for, discovered))
    paired, single = paired_folders(
        {f: [run.label for run, _ in each] for f, each in read.items()},
        folders,
        "plot draws two runs or more",
    )
    drawn = {
        f: [(run.run, results, held) for run, (results, held) in read[f]]
        for f in paired
    }
    alone = [(f, read[f][0][0].run) for f in single]
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
        held=ids,
        only=[] if named is not None else _only(drawn, ids),
        named=named is not None,
    )


def _only(
    drawn: dict[str, list[tuple[Run, dict, set[str]]]], ids: list[str]
) -> list[tuple[str, str, list[str]]]:
    """Each folder's tests one of its runs holds alone, by that run's label:
    the tests no section is drawn of (:func:`_section`)."""
    out = []
    for folder, read in drawn.items():
        for run, _, held in read:
            alone = [
                m
                for m in ids
                if m in held and sum(m in other for _, _, other in read) == 1
            ]
            if alone:
                out.append((folder, run.label, alone))
    return out


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
