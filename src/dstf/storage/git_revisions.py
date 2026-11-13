"""The one place DSTF reads git: what a folder held at a revision.

Plain ``git`` through ``subprocess`` — no library. What it offers: the
repository that holds a folder (:meth:`Repository.holding`), a revision as
a commit with its short hash and subject (:meth:`Repository.commit`), its
committer date (:meth:`Repository.date`), the files under the folder at a commit with their blob ids
(:meth:`Repository.files`), a blob's bytes (:meth:`Repository.reader`,
one ``git cat-file --batch`` for every read), and the blob id git would give
each file on disk now (:meth:`Repository.blobs_on_disk`).

Every git failure is a :class:`GitError` whose text is one sentence naming
the path or the revision (``not a git repository: …``, ``unknown revision
'xyz' in …``): a command prints it and exits 2, never a traceback.

Reading never writes the repository. Only ``rev-parse``, ``log -1``,
``ls-tree``, ``cat-file`` and ``hash-object`` without ``-w`` run here, none
of which touches the index; ``git diff`` against the work tree refreshes
the index of every stat-dirty file (even with optional locks off), and
``git diff --name-only`` reports a renamed file by its new name only, so
neither is used, and neither is ``git status`` (``tests/test_git_revisions.py``
hashes ``.git`` before and after over a touched file). What a folder holds
ON DISK is read from the disk by its caller, not from git: an untracked file
is exactly what an accept leaves before ``git add``.

One boundary, the user's own: :meth:`Repository.blobs_on_disk` hashes a
file with the repository's conversions applied, as ``git add`` would. Git's
own (line endings, ``ident``) write nothing; a ``filter`` driver's clean
command is the user's program, run as ``git status`` runs it, and may keep
what it is given — git-lfs stores a copy of a changed file under
``.git/lfs/objects``.
"""

from __future__ import annotations

import os
import stat
import subprocess
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

#: Optional locks off for every command (``git --no-optional-locks``).
_ENV = {"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}


class GitError(Exception):
    """Git could not answer; the text is one sentence that names the path
    or the revision."""


@dataclass(frozen=True)
class Commit:
    """A revision as git resolved it."""

    sha: str
    short: str
    subject: str


def _run(
    args: list[str], cwd: Path, given: bytes | None = None
) -> subprocess.CompletedProcess:
    """``git <args>`` in ``cwd``; ``given`` is what it reads on its
    standard input, nothing without."""
    try:
        return subprocess.run(
            ["git", "--no-optional-locks", *args],
            cwd=cwd,
            capture_output=True,
            env={**os.environ, **_ENV},
            **({"stdin": subprocess.DEVNULL} if given is None else {"input": given}),
        )
    except FileNotFoundError as e:
        raise GitError("git is not installed here (no git on the PATH)") from e


def _said(proc: subprocess.CompletedProcess) -> str:
    """Git's own last line of complaint, for a failure no sentence here
    names."""
    lines = proc.stderr.decode("utf-8", "replace").strip().splitlines()
    return lines[-1] if lines else f"git exited with {proc.returncode}"


def _quoted(path: str) -> bytes:
    """``path`` as one line of ``git hash-object --stdin-paths``, which has
    no ``-z``: C-quoted, as git reads a line that starts with a double
    quote — so a name that starts with one, holds a line break or ends in a
    carriage return (which git strips from a line) is read as it is."""
    out = bytearray(b'"')
    for byte in os.fsencode(path):
        if byte in b'"\\':
            out += b"\\" + bytes([byte])
        elif byte < 0x20 or byte == 0x7F:
            out += b"\\%03o" % byte
        else:
            out.append(byte)
    return bytes(out + b'"')


class Repository:
    """The git repository that holds a folder, read at revisions."""

    def __init__(self, top: Path, folder: str):
        #: the repository's work tree
        self.top = top
        #: the folder, as a path inside the repository ("" for its top)
        self.folder = folder

    @classmethod
    def holding(cls, folder: Path) -> Repository:
        """The repository whose work tree holds ``folder``."""
        folder = Path(folder)
        if not folder.is_dir():
            raise GitError(f"not a directory: {folder}")
        proc = _run(["rev-parse", "--show-toplevel", "--show-prefix"], folder)
        if proc.returncode != 0:
            if b"not a git repository" in proc.stderr:
                raise GitError(f"not a git repository: {folder}")
            raise GitError(f"{folder}: {_said(proc)}")
        top, prefix = (proc.stdout.decode("utf-8").splitlines() + [""])[:2]
        return cls(Path(top), prefix.rstrip("/"))

    def commit(self, rev: str) -> Commit:
        """``rev`` as a commit, or ``unknown revision '<rev>' in <top>``."""
        proc = _run(
            [
                "rev-parse",
                "--verify",
                "--quiet",
                "--end-of-options",
                f"{rev}^{{commit}}",
            ],
            self.top,
        )
        if proc.returncode != 0:
            raise GitError(f"unknown revision '{rev}' in {self.top}")
        sha = proc.stdout.decode().strip()
        proc = _run(["log", "-1", "--format=%h%x00%s", sha, "--"], self.top)
        if proc.returncode != 0:
            raise GitError(f"revision '{rev}' in {self.top}: {_said(proc)}")
        short, _, subject = (
            proc.stdout.decode("utf-8", "replace").rstrip("\n").partition("\0")
        )
        return Commit(sha=sha, short=short, subject=subject)

    def date(self, commit: Commit) -> str:
        """``commit``'s committer date, ``YYYY-MM-DD``: when it was made on
        its branch. Not a field of :class:`Commit`, whose fields are
        ``baseline diff``'s JSON."""
        proc = _run(["log", "-1", "--format=%cs", commit.sha, "--"], self.top)
        if proc.returncode != 0:
            raise GitError(f"revision '{commit.short}' in {self.top}: {_said(proc)}")
        return proc.stdout.decode("utf-8", "replace").strip()

    def files(self, commit: Commit) -> dict[str, str]:
        """Every file under the folder at ``commit``: its path inside the
        folder (``/``-separated) → its blob id. A rename keeps the blob id;
        a folder the commit does not hold has no files."""
        where = [self.folder] if self.folder else []
        proc = _run(
            ["ls-tree", "-r", "-z", "--full-tree", commit.sha, "--", *where], self.top
        )
        if proc.returncode != 0:
            raise GitError(
                f"cannot list {self.folder or '.'} at {commit.short}: {_said(proc)}"
            )
        start = len(self.folder) + 1 if self.folder else 0
        out: dict[str, str] = {}
        for entry in proc.stdout.split(b"\0"):
            if not entry:
                continue
            meta, _, path = entry.partition(b"\t")
            _mode, kind, blob = meta.split()
            if kind == b"blob":
                out[path.decode("utf-8", "surrogateescape")[start:]] = blob.decode()
        return out

    def blobs_on_disk(self, paths: Iterable[str]) -> dict[str, str]:
        """The blob id git would store each of ``paths`` under — a path
        inside the folder, as :meth:`files` spells it — for what is on disk
        now. A file is hashed with the repository's conversions applied, as
        ``git add`` would (line endings, ``ident``, a ``filter`` driver's
        clean command): a file checked out with CRLF line endings and
        stored with LF is the blob it was. A symbolic link is the blob of
        its target's name, as git stores one, never what it points at. A
        path that is neither — not there, or a directory — is left out.

        One ``git hash-object --stdin-paths`` for every file, without
        ``-w``: nothing is stored. It reads each path from the work tree's
        top, whatever directory it starts in, and stops at the first it
        cannot read, so only regular files are given, and as many ids as
        paths must come back. A link is one ``git hash-object --stdin
        --no-filters`` each: the id is git's to compute (a repository may
        name its objects by SHA-256)."""
        base = self.top / self.folder if self.folder else self.top
        prefix = f"{self.folder}/" if self.folder else ""
        files: list[str] = []
        out: dict[str, str] = {}
        for path in paths:
            here = base / path
            try:
                mode = here.lstat().st_mode
            except OSError:
                continue
            if stat.S_ISLNK(mode):
                proc = _run(
                    ["hash-object", "--stdin", "--no-filters"],
                    self.top,
                    os.fsencode(os.readlink(here)),
                )
                if proc.returncode != 0:
                    raise GitError(f"cannot hash the link {here}: {_said(proc)}")
                out[path] = proc.stdout.decode().strip()
            elif stat.S_ISREG(mode):
                files.append(path)
        if not files:
            return out
        proc = _run(
            ["hash-object", "--stdin-paths"],
            self.top,
            b"".join(_quoted(prefix + path) + b"\n" for path in files),
        )
        ids = proc.stdout.decode().split()
        if proc.returncode != 0 or len(ids) != len(files):
            raise GitError(
                f"cannot hash the files of {self.folder or '.'} in {self.top}: "
                f"{_said(proc)}"
            )
        out.update(zip(files, ids, strict=True))
        return out

    @contextmanager
    def reader(self) -> Iterator[Callable[[str], bytes]]:
        """A function from a blob id to its bytes, over one ``git cat-file
        --batch`` process that lives as long as the ``with``."""
        try:
            proc = subprocess.Popen(
                ["git", "--no-optional-locks", "cat-file", "--batch"],
                cwd=self.top,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env={**os.environ, **_ENV},
            )
        except FileNotFoundError as e:
            raise GitError("git is not installed here (no git on the PATH)") from e
        assert proc.stdin is not None and proc.stdout is not None
        stdin, stdout = proc.stdin, proc.stdout

        def read(blob: str) -> bytes:
            try:
                stdin.write(blob.encode() + b"\n")
                stdin.flush()
            except BrokenPipeError as e:
                raise GitError(f"git cat-file stopped in {self.top}") from e
            head = stdout.readline().split()
            if len(head) != 3:
                raise GitError(f"blob {blob} cannot be read in {self.top}")
            data = stdout.read(int(head[2]))
            stdout.read(1)  # the newline after the contents
            return data

        try:
            yield read
        finally:
            stdin.close()
            stdout.close()
            proc.wait()
