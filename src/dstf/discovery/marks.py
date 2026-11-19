"""Which of a test's marks applies to a run: whether it is skipped, and how
long it may run.

THE one reader of an entry's ``skip`` and ``backends`` keys
(``TestModel.skip``, ``TestModel.backend_marks``). The most specific mark
wins: the block of the run's backend and OS (``"OpenModelica/windows"``),
then its backend's (``"OpenModelica"``), then the entry's own (``skip``;
``simulation.timeout``), then the run's ``--timeout``. A block that says
only one of the two keys leaves the other to the next one down.

The backend is the run's family (``config.backend``: ``"Dymola"`` for an
entry named ``"Dymola 2025"``), as the reference folders spell it, and the
OS ``config.os_name``: the two parts of ``<reference_root>/<Backend>/<os>/``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..config import Config
    from .test_registry import TestModel

#: What a mark on the entry itself is said to be on.
EVERY_BACKEND = "every backend"


@dataclass(frozen=True)
class SkipMark:
    """Why a test is not run here, and where the mark that says so is:
    ``where`` is the key of its ``backends`` block (``"OpenModelica"``,
    ``"OpenModelica/windows"``) or :data:`EVERY_BACKEND` for the entry's
    own — what "skipped on <where>: <reason>" is worded from."""

    where: str
    reason: str


def _block(test: TestModel, config: Config, key: str) -> tuple[str, Any] | None:
    """``(where, value)`` of ``key`` in the most specific ``backends`` block
    of ``test`` that applies to ``config``'s backend and OS and says it, or
    None."""
    if not test.backend_marks:
        return None
    backend = config.backend
    for where in (f"{backend}/{config.os_name}", backend):
        value = test.backend_marks.get(where, {}).get(key)
        if value is not None:
            return where, value
    return None


def skip_reason(test: TestModel, config: Config) -> SkipMark | None:
    """Why ``test`` is not run on ``config``'s backend and OS, or None when
    no mark says so."""
    found = _block(test, config, "skip")
    if found is not None:
        return SkipMark(*found)
    if test.skip is not None:
        return SkipMark(EVERY_BACKEND, test.skip)
    return None


def timeout_for(test: TestModel, config: Config) -> int:
    """The seconds ``test`` may run on ``config``'s backend and OS: its
    block's ``timeout``, else its ``simulation.timeout``, else the run's."""
    found = _block(test, config, "timeout")
    if found is not None:
        return found[1]
    if test.timeout is not None:
        return test.timeout
    return config.timeout
