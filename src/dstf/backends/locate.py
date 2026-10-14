"""Where a backend's install is, and by which rule it was found.

What :meth:`Backend.locate <dstf.backends.base.Backend.locate>` returns and
the one search it is built from. A backend's install is looked for nowhere
else: ``Config`` asks ``locate`` which backend a run uses and what its
executable is, ``dstf check`` prints what it answers, and a backend that
needs a second thing (Dymola's Python interface) adds it as another
:class:`Found`.

The rules, in order — the first that finds something wins:

1. the executable the command line names (:data:`COMMAND_LINE`), else the
   backend's block in ``testing.json`` (:data:`SETTING`);
2. an environment variable, where the backend has one (:data:`ENVIRONMENT`);
3. the search: ``PATH``, then the backend's standard install folders
   (:data:`SEARCH`).
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: The kinds of rule a thing is found by.
COMMAND_LINE = "command line"
SETTING = "setting"
ENVIRONMENT = "environment"
SEARCH = "search"

#: What a backend's program is called in a :class:`Found`.
EXECUTABLE = "executable"

#: The flag that names the executable on the command line, and the key
#: ``Config`` hands its value to ``locate`` under (:func:`with_backend_path`)
#: — no key of a block: a ``testing.json`` that held it is refused.
BACKEND_PATH = "--backend-path"


@dataclass(frozen=True)
class Rule:
    """The rule that found a thing: its kind and what it names — the
    flag, the setting's key, the variable's name, or where the search
    looked (``PATH``, a folder)."""

    kind: str
    name: str

    def said(self, entry: str = "") -> str:
        """The rule in words. ``entry`` is the ``backends`` entry the
        settings came from, where there is one to name."""
        if self.kind == COMMAND_LINE:
            return f"the command line: {self.name}"
        if self.kind == SETTING:
            block = f"backends.{entry}." if entry else ""
            return f"testing.json: {block}{self.name}"
        if self.kind == ENVIRONMENT:
            return f"environment variable {self.name}"
        return f"search: {self.name}"


@dataclass(frozen=True)
class Found:
    """One thing of a backend's install: ``what`` it is, its ``path`` and
    the ``rule`` that found it — or, not found, no path and no rule, and
    ``detail`` saying where it was looked for. Found, ``detail`` is what
    else is known of it (a version)."""

    what: str
    path: str | None = None
    rule: Rule | None = None
    detail: str = ""


@dataclass(frozen=True)
class Located:
    """A backend's install: its parts, the first being the one a run cannot
    do without (the executable; FMPy's package)."""

    parts: tuple[Found, ...]

    @property
    def found(self) -> bool:
        """Whether the backend is installed: its first part was found."""
        return bool(self.parts) and self.parts[0].path is not None

    @property
    def complete(self) -> bool:
        """Whether every part was found."""
        return all(part.path is not None for part in self.parts)

    def part(self, what: str) -> Found | None:
        return next((p for p in self.parts if p.what == what), None)

    def path(self, what: str) -> str | None:
        """The path of the part called ``what``, or None: not found, or
        this backend has no such part."""
        part = self.part(what)
        return part.path if part is not None else None

    @property
    def executable(self) -> str | None:
        return self.path(EXECUTABLE)

    def missing(self) -> str:
        """Why the backend is not found, for an error: each part without a
        path and where it was looked for."""
        return "; ".join(
            f"{part.what} not found — {part.detail}"
            if part.detail
            else f"{part.what} not found"
            for part in self.parts
            if part.path is None
        )

    def lines(self, entry: str = "") -> list[str]:
        """The parts as ``dstf check`` prints them: what, where, and the
        rule that found it."""
        out: list[str] = []
        for part in self.parts:
            if part.path is None:
                out.append(f"  {part.what}: not found — {part.detail}")
                continue
            out.append(f"  {part.what}: {part.path}")
            assert part.rule is not None  # a found part names its rule
            out.append(f"    found by: {part.rule.said(entry)}")
            if part.detail:
                out.append(f"    {part.detail}")
        return out


_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def looks_like_path(entry: str) -> bool:
    """True if ``entry`` is an OS-specific path (not just a bare binary name).

    POSIX-absolute (``/usr/bin/omc``) or Windows-drive-rooted
    (``C:\\Program Files\\...``) count as pinned paths; bare names like
    ``"omc"`` or ``"dymola.exe"`` do not. A regex and not pathlib's
    ``is_absolute``: that returns False for ``C:\\...`` on Linux (PosixPath
    does not recognize Windows drive letters).
    """
    if entry.startswith("/"):
        return True
    return bool(_WINDOWS_DRIVE_RE.match(entry))


def with_backend_path(settings: Mapping[str, Any], path: str) -> dict[str, Any]:
    """``settings`` for a run whose executable the command line named:
    ``path`` is then the one entry looked at, over the block's own."""
    return {**settings, BACKEND_PATH: path}


def configured(settings: Mapping[str, Any]) -> tuple[list[str], Rule]:
    """The executables ``settings`` names, in order, and the rule they are
    named by: the command line's one over the block's list."""
    named = settings.get(BACKEND_PATH)
    if named is not None:
        return [str(named)], Rule(COMMAND_LINE, BACKEND_PATH)
    entries = [str(e) for e in settings.get("executables") or []]
    return entries, Rule(SETTING, "executables")


def pins(settings: Mapping[str, Any]) -> bool:
    """Whether ``settings`` says WHICH install is meant, so that no other
    may be taken in its place: the command line named the executable, or
    the block's list holds a path (:func:`looks_like_path`)."""
    entries, rule = configured(settings)
    return rule.kind == COMMAND_LINE or any(looks_like_path(e) for e in entries)


def find_executable(
    settings: Mapping[str, Any],
    binaries: Sequence[str],
    *,
    environment: Sequence[tuple[str, Callable[[str], Iterable[Path]]]] = (),
    folders: Sequence[tuple[Path, str]] = (),
    best_first: Callable[[Path], Any] | None = None,
) -> Found:
    """A backend's executable, by the rules of this module.

    1. What ``settings`` names (:func:`configured`), in order: the first
       that exists on disk or resolves on ``PATH``. Settings that PIN the
       executable (:func:`pins`) and name none that is there are NOT
       FOUND, and nothing below is tried: a list of OS-specific absolute
       paths (``C:\\Program Files\\...\\Dymola.exe``) says the user wants
       *that* install, and treating some same-named binary on ``PATH`` as
       its equal would silently run another simulator on a mixed-OS setup
       (a Linux machine beside the Windows one often has a ``dymola`` on
       ``PATH`` that is a different version). ``--backend-path`` pins
       whatever it is spelled like.
    2. ``environment``: ``(VARIABLE, candidates)`` pairs — the first set
       variable one of whose candidate paths exists.
    3. ``binaries`` on ``PATH``, then ``folders``: the backend's standard
       install folders as ``(folder, glob below it)`` pairs, the matches of
       all of them sorted by ``best_first`` (the newest version first).
    """
    entries, rule = configured(settings)
    refused: list[str] = []
    for entry in entries:
        path = Path(entry)
        if path.is_absolute() and path.exists():
            return Found(EXECUTABLE, str(path), rule)
        on_path = shutil.which(entry)
        if on_path:
            return Found(EXECUTABLE, on_path, rule)
        why = "does not exist" if looks_like_path(entry) else "is not on PATH"
        refused.append(f"{entry} {why}")
    if pins(settings):
        named = (
            f"{rule.name} "
            if rule.kind == COMMAND_LINE
            else "the configured executables: "
        )
        return Found(EXECUTABLE, detail=named + ", ".join(refused))
    for variable, candidates in environment:
        value = os.environ.get(variable)
        if not value:
            continue
        for candidate in candidates(value):
            if candidate.exists():
                return Found(EXECUTABLE, str(candidate), Rule(ENVIRONMENT, variable))
    for binary in binaries:
        on_path = shutil.which(binary)
        if on_path:
            return Found(EXECUTABLE, on_path, Rule(SEARCH, "PATH"))
    matches = [
        (match, base)
        for base, pattern in folders
        if base.is_dir()
        for match in base.glob(pattern)
    ]
    if best_first is not None:
        matches.sort(key=lambda pair: best_first(pair[0]))
    for match, base in matches:
        return Found(
            EXECUTABLE, str(match), Rule(SEARCH, f"the standard folder {base}")
        )
    looked = ["PATH", *dict.fromkeys(str(base) for base, _ in folders)]
    names = [*entries, *(b for b in binaries if b not in entries)]
    return Found(
        EXECUTABLE,
        detail=f"looked for {' / '.join(names) or 'no program'} on {', '.join(looked)}",
    )
