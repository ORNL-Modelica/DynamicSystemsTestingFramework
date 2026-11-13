"""Which tests use a class: the facts ``dstf uses`` is built from, as
functions of what they are given.

A backend answers per test with the classes its model uses
(:meth:`dstf.backends.base.Backend.uses`), read from the file its simulator
writes of the model and everything it uses. Here those classes are cut to
the project's — its library and the dependencies given as a path — by the
top packages they are under; a name the command is given is checked to be
one of them; and the answers are turned round to the users of a class.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from .library_state import dependency_file, dependency_root
from .mo_parser import declared_classes, is_declared

if TYPE_CHECKING:
    from ..config import Config


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
        try:
            text = file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for name in declared_classes(text):
            if "." not in name:
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
