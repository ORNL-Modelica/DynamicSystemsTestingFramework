"""Which tests use a class: the facts ``dstf uses`` is built from, as
functions of what they are given.

A backend answers per test with the classes its model uses
(:meth:`dstf.backends.base.Backend.uses`), read from the file its simulator
writes of the model and everything it uses. Here those classes are cut to
the project's — its library and the dependencies given as a path — by the
top packages they are under; a name the command is given is checked to be
one of them; and the answers are turned round to the users of a class.

``--since REV`` asks for the classes stored in the files that differ
between a git revision and the disk (:func:`changed_since`): the project's
source roots (:func:`source_roots`), each compared in the repository that
holds it, and each differing file read as the class that stores it
(:func:`class_of`). Git is only read (:mod:`dstf.storage.git_revisions`).
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from itertools import takewhile
from pathlib import Path
from typing import TYPE_CHECKING

from .library_state import dependency_file, dependency_root
from .mo_parser import UnclosedClass, declared_classes, is_declared

if TYPE_CHECKING:
    from ..config import Config
    from ..storage.git_revisions import Commit, Repository


def _top_names(file: Path) -> list[str] | None:
    """The top-level classes ``file`` declares, in source order; None when
    it cannot be read, or ends inside a class (what it declares is not
    known)."""
    try:
        text = file.read_text(encoding="utf-8", errors="replace")
        return [name for name in declared_classes(text) if "." not in name]
    except (OSError, UnclosedClass):
        return None


def top_packages(config: Config) -> dict[str, Path]:
    """The top-level classes of the project, each with the file it is
    loaded by: the library's, and each of a dependency given as a path —
    named by what that file declares, never by its directory's name. A
    dependency given by name (``Modelica``) is not the project's."""
    found = {str(config.library_name): config.library_dir / "package.mo"}
    for entry in config.dependencies:
        if dependency_root(entry) is None:
            continue
        file = dependency_file(entry)
        for name in _top_names(file) or ():
            found.setdefault(name, file)
    return found


def project_classes(text: str, tops: Collection[str]) -> frozenset[str]:
    """The classes a simulator's file of one model declares under the
    project's top packages ``tops``. The rest is not the project's: the
    model the tool wraps the test in, at top level (Dymola's
    ``Lib_Examples_Test``, omc's ``Test_total``), and any library loaded by
    name (omc writes the parts of ``Modelica`` and ``Complex`` used)."""
    return frozenset(
        name for name in declared_classes(text) if name.split(".", 1)[0] in tops
    )


def is_class(config: Config, name: str) -> bool:
    """Whether ``name`` is a class of the project: a top package of the
    library or of a dependency given as a path, or a class under one of any
    kind (a ``type`` and an ``operator`` included) — what a simulator's
    answer can hold. A one-file dependency is read whole."""
    tops = top_packages(config)
    top = name.split(".", 1)[0]
    file = tops.get(top)
    if file is None:
        return False
    if name == top:
        return True
    if file.name != "package.mo":
        try:
            text = file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        return name in declared_classes(text)
    return is_declared(file.parent, top, name)


def users(
    answers: Mapping[str, frozenset[str] | str], name: str
) -> tuple[list[str], list[str]]:
    """The tests that use class ``name``, and the tests that could not be
    asked (each counted as using every class), each sorted by model id."""
    using = sorted(
        test
        for test, got in answers.items()
        if not isinstance(got, str) and name in got
    )
    unasked = sorted(test for test, got in answers.items() if isinstance(got, str))
    return using, unasked


#: The longest reason a test that could not be asked is given, in
#: characters: it is printed on the test's own line.
REASON_LIMIT = 300


def one_line(said: str, otherwise: str) -> str:
    """A simulator's reason for a test it could not answer for, as the one
    line it is printed on: its whitespace collapsed, cut to
    :data:`REASON_LIMIT` characters; ``otherwise`` when it said nothing."""
    text = " ".join(said.split())
    if not text:
        return otherwise
    return text if len(text) <= REASON_LIMIT else text[: REASON_LIMIT - 1] + "…"


# --------------------------------------------------------------------------
# What changed since a revision (`dstf uses --since`)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceRoot:
    """A place the project's Modelica files are: the library's directory,
    a dependency's directory, or the one file of a one-file dependency
    (``Name.mo``) — resolved, as a simulator reads it."""

    path: Path
    #: The top-level classes it is loaded as, by what its file declares;
    #: none when that cannot be read, or a name is an earlier root's.
    tops: tuple[str, ...]

    @property
    def one_file(self) -> bool:
        """Whether the root is one file (:func:`dependency_file`'s rule)."""
        return self.path.suffix == ".mo"

    @property
    def label(self) -> str:
        """The root as the output names it: its top packages, or its path
        when it has none."""
        return " / ".join(self.tops) or str(self.path)


def source_roots(config: Config) -> list[SourceRoot]:
    """The library's root, then each dependency given as a path, in the
    order ``dependencies`` lists them. A root is named by what the file it
    is loaded by declares, never by its directory's name; one that declares
    a name an earlier root holds is not that class, and has no name."""
    roots = [SourceRoot(config.library_dir.resolve(), (str(config.library_name),))]
    taken = set(roots[0].tops)
    for entry in config.dependencies:
        root = dependency_root(entry)
        if root is None:
            continue
        names = _top_names(dependency_file(entry)) or []
        tops = () if not names or taken & set(names) else tuple(names)
        taken.update(names)
        roots.append(SourceRoot(Path(os.path.realpath(root)), tops))
    return roots


#: A Modelica identifier as a folder or a file is named: a quoted class
#: name (``'my model'``) is stored under another spelling.
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def class_of(top: str, path: str, deleted: bool) -> str | None:
    """The class to ask for a file of a directory root whose top package
    is ``top``; ``path`` is the file's path inside the root,
    ``/``-separated. None when no class can be asked: every test is then
    counted.

    ``A/B/C.mo`` stores ``<top>.A.B.C``, ``A/B/package.mo`` the package
    ``<top>.A.B`` (the root's own ``package.mo``: ``<top>``). A DELETED
    file is asked as the package that held it — the tests that used its
    class can no longer be asked for it, so the holder's users are counted
    (more than used it, on purpose); with the root's own ``package.mo``
    gone, the top package cannot be named. A folder or file whose name is
    no plain identifier is read as the nearest package above it that is
    one. Any other file — not ``.mo`` — is stored by no class (a caller
    leaves ``package.order`` out first: it orders a browser's tree)."""
    *folders, name = path.split("/")
    if not name.endswith(".mo"):
        return None
    parts = folders if name == "package.mo" else [*folders, name[: -len(".mo")]]
    if deleted:
        if not parts:
            return None
        parts = parts[:-1]
    plain = takewhile(_IDENTIFIER.fullmatch, parts)
    return ".".join([top, *plain])


#: Why a changed file counts every test.
NO_CLASS = "no class stores it"
NO_TOP = "its top package cannot be named"


@dataclass(frozen=True)
class ChangedFile:
    """A file of a root that differs between the revision and the disk."""

    #: Inside its root, ``/``-separated; a one-file root's: its name.
    path: str
    deleted: bool
    #: The classes asked for it; none when it counts every test.
    classes: tuple[str, ...]
    #: Why it counts every test (:data:`NO_CLASS`, :data:`NO_TOP`); None
    #: when its classes are asked.
    reason: str | None = None


@dataclass(frozen=True)
class RootChanges:
    root: SourceRoot
    #: Those that count every test first, then by path.
    files: tuple[ChangedFile, ...]
    #: The folders under the root that are another repository (a nested
    #: one, a submodule): their files are not compared.
    not_compared: tuple[str, ...] = ()


@dataclass(frozen=True)
class RepoChanges:
    """One repository: the revision as it resolved there, and the roots
    it holds."""

    top: Path
    commit: Commit
    roots: tuple[RootChanges, ...]

    @property
    def files(self) -> list[ChangedFile]:
        return [f for root in self.roots for f in root.files]


@dataclass(frozen=True)
class Changes:
    """What differs between a revision and the disk, per repository."""

    repos: tuple[RepoChanges, ...]
    #: Roots in no git repository: not compared.
    unversioned: tuple[SourceRoot, ...] = ()

    @property
    def classes(self) -> list[str]:
        """The changed classes, each once, sorted by name."""
        return sorted({c for repo in self.repos for f in repo.files for c in f.classes})

    @property
    def every_test(self) -> list[ChangedFile]:
        """The changed files that count every test."""
        return [f for repo in self.repos for f in repo.files if f.reason]


def _dstf_files(config: Config, root: Path) -> Callable[[Path], bool]:
    """Whether a file under ``root`` is DSTF's own, not the library's
    source: under the reference root when that folder is INSIDE the root
    (the bundled library keeps it so — every baseline an accept writes),
    and the project's testing.json and test_spec.json wherever they are.
    Never compared: an accept would count every test. A reference root
    that is the root, or holds it, hides nothing: the library's own files
    are there too."""
    refs = config.reference_root
    inside = refs is not None and root in refs.parents
    own = {
        p.resolve() for p in (config.config_found, config.spec_path) if p is not None
    }

    def is_dstf(path: Path) -> bool:
        return path in own or (inside and refs in path.parents)

    return is_dstf


def _modelica_on_disk(
    root: Path, is_dstf: Callable[[Path], bool]
) -> tuple[list[str], list[str]]:
    """The ``.mo`` files under a directory root now, by path inside it, and
    the folders under it that are another repository. A linked directory
    is not entered (git holds the link, not what is under it); a ``.mo``
    link is a file of its own. DSTF's own files are left out."""
    found: list[str] = []
    nested: list[str] = []
    for here, dirs, files in os.walk(root):
        inside = Path(here).relative_to(root).as_posix()
        if inside != "." and (".git" in dirs or ".git" in files):
            nested.append(inside)
            dirs[:] = []
            continue
        dirs[:] = sorted(d for d in dirs if d != ".git")
        for name in files:
            if name.endswith(".mo") and not is_dstf(Path(here, name)):
                found.append(name if inside == "." else f"{inside}/{name}")
    return found, sorted(nested)


def _compare(
    git: Repository, commit: Commit, root: SourceRoot, is_dstf: Callable[[Path], bool]
) -> tuple[dict[str, str], list[str], list[str]]:
    """A root's files at the revision (path → blob id), the candidates on
    disk to hash beside them, and its nested repositories — DSTF's own
    files left out of both."""
    at_rev = git.files(commit)
    if root.one_file:
        name = root.path.name
        at_rev = {name: at_rev[name]} if name in at_rev else {}
        return at_rev, [name], []
    on_disk, nested = _modelica_on_disk(root.path, is_dstf)
    at_rev = {
        path: blob
        for path, blob in at_rev.items()
        if not is_dstf(root.path / path)
        and not any(path.startswith(f"{n}/") for n in nested)
    }
    return at_rev, on_disk, nested


def _changed_file(root: SourceRoot, path: str, deleted: bool) -> ChangedFile:
    if not root.tops:
        return ChangedFile(path, deleted, (), NO_TOP)
    if root.one_file:
        return ChangedFile(path, deleted, root.tops)
    asked = class_of(root.tops[0], path, deleted)
    if asked is not None:
        return ChangedFile(path, deleted, (asked,))
    return ChangedFile(path, deleted, (), NO_TOP if path.endswith(".mo") else NO_CLASS)


def changed_since(config: Config, rev: str, say: Callable[[str], None]) -> Changes:
    """The project's files that differ between the git revision ``rev`` and
    the disk — uncommitted edits included — each with the classes to ask
    for it; ``say`` is told what is being done.

    A candidate is every file under a root at ``rev`` and every ``.mo``
    file on disk (a new data file matters once a ``.mo`` file names it);
    it differs when only one side has it, or their blob ids differ, the
    disk's as git would store it. ``package.order`` is left out.

    Raises:
        GitError: The library is in no repository; a repository does not
            know ``rev`` (the sentence names the roots it holds when the
            project is in several, since a commit hash is known in one);
            git is not here. A dependency in no repository is not an
            error: it is not compared (:attr:`Changes.unversioned`).
    """
    from ..storage.git_revisions import GitError, NotARepository, Repository

    held: dict[Path, list[tuple[SourceRoot, Repository]]] = {}
    unversioned: list[SourceRoot] = []
    for i, root in enumerate(source_roots(config)):
        try:
            git = Repository.holding(root.path.parent if root.one_file else root.path)
        except NotARepository:
            if i == 0:
                raise
            say(f"{root.label} is in no git repository: its files are not compared.")
            unversioned.append(root)
            continue
        held.setdefault(git.top, []).append((root, git))

    # The revision in every repository before any file is compared: one
    # that does not know it is said alone.
    commits: dict[Path, Commit] = {}
    for top, roots in held.items():
        try:
            commits[top] = roots[0][1].commit(rev)
        except GitError as exc:
            if len(held) == 1:
                raise
            holds = ", ".join(root.label for root, _ in roots)
            raise GitError(
                f"{exc} (it holds {holds}): give a revision every repository "
                "of the project knows, a branch or a tag"
            ) from exc

    repos: list[RepoChanges] = []
    for top, roots in held.items():
        commit = commits[top]
        compared = [
            (root, git, *_compare(git, commit, root, _dstf_files(config, root.path)))
            for root, git in roots
        ]
        n = sum(len({*at_rev, *on_disk}) for _, _, at_rev, on_disk, _ in compared)
        say(f"Comparing {n} file{'' if n == 1 else 's'} with {rev} ...")
        changes = []
        for root, git, at_rev, on_disk, nested in compared:
            now = git.blobs_on_disk(sorted({*at_rev, *on_disk}))
            files = []
            for path in {*at_rev, *now}:
                if path.split("/")[-1] == "package.order":
                    continue
                if path in now and now[path] == at_rev.get(path):
                    continue
                files.append(_changed_file(root, path, deleted=path not in now))
            files.sort(key=lambda f: (f.reason is None, f.path))
            changes.append(RootChanges(root, tuple(files), tuple(nested)))
        repos.append(RepoChanges(top, commit, tuple(changes)))
    return Changes(tuple(repos), tuple(unversioned))
