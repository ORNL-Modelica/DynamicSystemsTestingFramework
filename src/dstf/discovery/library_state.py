"""What a project's source files looked like at one moment.

A simulator that stays alive (the tool's queue: Dymola, OpenModelica, Julia)
read the library once, when its worker started; a file edited since is not
what it runs. A :class:`LibraryState` is the stamp — modification time and
size — of every source file at one Queue click, so "has anything changed
since this worker read the library" is a comparison of two states and, when
the answer is no, costs nothing in the simulator.

Which files, by source type (:func:`source_stamps`): a Modelica library's
``*.mo`` and ``package.order`` under ``source_path`` and under every
dependency given as a PATH; a Julia project's ``*.jl`` and ``*.toml``. A
dependency given by NAME (``"Modelica"``) is found by the simulator, not by
this tool, and is not swept. An FMU or Python project runs one process per
test, which reads its source as it is: nothing is swept.

Stat only, no file is opened. Over a 9P mount (``/mnt/d``) LISTING a
directory is the slow step — a library of 4 117 such files in 872
directories took 6.9 s one directory after the other — so the walk fans
the directories out over threads there (1.3 s with 8; 16 are no faster). On
a native file system one thread walks the same library in 0.06 s and the
threads would only cost (0.57 s), so a root is walked on threads only when
its own listing was slow.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import Config

#: ``(st_mtime_ns, st_size)``.
Stamp = tuple[int, int]

#: A root whose own listing took longer than this is walked on threads.
_SLOW_LISTING_SECONDS = 0.002
_THREADS = 8


def stamp(path: Path | str) -> Stamp | None:
    """The stamp of one file now; None when there is no such file."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def _list(
    directory: str, wanted: Callable[[str], bool]
) -> tuple[list[str], list[str], dict[Path, Stamp]]:
    """One directory: its subdirectories, those of them that are symbolic
    links (apart: :func:`_followed` decides which are walked), and the
    stamp of each wanted file in it. A link is read as what it points at;
    a dangling one is nothing."""
    subs: list[str] = []
    links: list[str] = []
    found: dict[Path, Stamp] = {}
    try:
        entries = os.scandir(directory)
    except OSError:  # removed while the walk ran, or not a directory
        return subs, links, found
    with entries:
        for entry in entries:
            try:
                if entry.is_dir():
                    (links if entry.is_symlink() else subs).append(entry.path)
                elif wanted(entry.name):
                    st = entry.stat()
                    found[Path(entry.path)] = (st.st_mtime_ns, st.st_size)
            except OSError:
                continue
    return subs, links, found


def _followed(links: list[str], seen: set[str]) -> list[str]:
    """The linked directories of *links* to walk: each real directory is
    entered through a link once, and the root never — so a link that leads
    back to a directory above it ends the walk instead of repeating it. In
    name order, so two links to one directory are told apart the same way
    by every sweep."""
    walked: list[str] = []
    for link in sorted(links):
        real = os.path.realpath(link)
        if real not in seen:
            seen.add(real)
            walked.append(link)
    return walked


def sweep(roots: Iterable[Path], wanted: Callable[[str], bool]) -> dict[Path, Stamp]:
    """The stamp of every file under *roots* whose name is *wanted*, keyed
    by its path as it is spelled from its root — through a sub-directory
    that is a symbolic link too (a sub-package kept elsewhere is part of
    the library the simulator loads). A root that is a file is stamped
    itself; one that does not exist holds nothing."""
    stamps: dict[Path, Stamp] = {}
    for root in roots:
        if not root.is_dir():
            own = stamp(root)
            if own is not None:
                stamps[root] = own
            continue
        seen = {os.path.realpath(root)}
        began = time.perf_counter()
        pending, links, found = _list(str(root), wanted)
        pending += _followed(links, seen)
        stamps.update(found)
        if time.perf_counter() - began <= _SLOW_LISTING_SECONDS:
            while pending:
                subs, links, found = _list(pending.pop(), wanted)
                pending += subs + _followed(links, seen)
                stamps.update(found)
            continue
        with ThreadPoolExecutor(_THREADS, thread_name_prefix="dstf-sweep") as pool:
            while pending:
                listed = list(pool.map(lambda sub: _list(sub, wanted), pending))
                pending = [sub for subs, _, _ in listed for sub in subs]
                pending += _followed(
                    [link for _, links, _ in listed for link in links], seen
                )
                for _, _, found in listed:
                    stamps.update(found)
    return stamps


def _modelica_source(name: str) -> bool:
    return name.endswith(".mo") or name == "package.order"


def _julia_source(name: str) -> bool:
    return name.endswith((".jl", ".toml"))


def dependency_root(entry: str) -> Path | None:
    """Where a ``dependencies`` entry's files are: the directory of a
    library given by its directory or its ``package.mo``, the one file of a
    library given as ``Name.mo``; None for a library given by name. Spelled
    as the entry is, and the ONE place that says it: the sweep keys a
    dependency's files under this, and a backend asks :func:`under` with
    this which of them a change touched."""
    from ..config import dependency_is_path

    if not dependency_is_path(entry):
        return None
    path = Path(entry)
    return path.parent if path.name == "package.mo" else path


def under(path: Path, root: Path) -> bool:
    """Whether a swept *path* is *root* or a file below it — both as the
    sweep spells them: *root* is the library's directory or a
    :func:`dependency_root`, never a resolved form of either (a path
    through a symbolic link, or one holding ``..``, has another)."""
    return path == root or root in path.parents


def source_stamps(config: Config) -> dict[Path, Stamp] | None:
    """The stamp of every source file a live simulator holds of *config*'s
    project; None for a source type no worker keeps loaded."""
    if config.source_type == "modelica":
        roots = [config.library_dir]
        roots += [
            root
            for entry in config.dependencies
            if (root := dependency_root(entry)) is not None
        ]
        return sweep(roots, _modelica_source)
    if config.source_type == "julia" and config.source_path is not None:
        return sweep([config.source_path], _julia_source)
    return None


@dataclass(frozen=True)
class LibraryChanges:
    """The files that differ between two states, each list sorted."""

    #: In both, with another stamp.
    changed: tuple[Path, ...] = ()
    added: tuple[Path, ...] = ()
    removed: tuple[Path, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.changed or self.added or self.removed)


@dataclass(frozen=True, eq=False)
class LibraryState:
    """The source files at one sweep. ``serial`` orders the states of one
    Session: a higher one was swept later. Two states are the same state
    only when they are the same object (:func:`following`)."""

    serial: int
    stamps: Mapping[Path, Stamp]

    def changes_since(self, older: LibraryState) -> LibraryChanges:
        new, old = self.stamps, older.stamps
        return LibraryChanges(
            changed=tuple(
                sorted(p for p, s in new.items() if p in old and old[p] != s)
            ),
            added=tuple(sorted(p for p in new if p not in old)),
            removed=tuple(sorted(p for p in old if p not in new)),
        )


def following(last: LibraryState | None, stamps: Mapping[Path, Stamp]) -> LibraryState:
    """The state a sweep that found *stamps* is: *last* itself when no file
    differs from it — whoever holds *last* then has nothing to read — else
    the next one."""
    if last is None:
        return LibraryState(1, stamps)
    if stamps == last.stamps:
        return last
    return LibraryState(last.serial + 1, stamps)


__all__ = [
    "LibraryChanges",
    "LibraryState",
    "Stamp",
    "dependency_root",
    "following",
    "source_stamps",
    "stamp",
    "sweep",
    "under",
]
