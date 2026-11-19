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

from collections.abc import Iterable, Mapping
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


def marks_of(tests: Iterable[TestModel], config: Config) -> dict[str, SkipMark]:
    """The tests of ``tests`` a mark keeps from running on ``config``'s
    backend and OS, by model id, each with its mark (:func:`skip_reason`):
    what a command hands ``compare_all`` and an accept."""
    return {
        t.model_id: mark for t in tests if (mark := skip_reason(t, config)) is not None
    }


def split_marked(
    tests: Iterable[TestModel], marks: Mapping[str, SkipMark]
) -> tuple[list[TestModel], list[TestModel]]:
    """``tests`` split, in their order, into those to run and those
    ``marks`` keeps from running: what ``dstf run`` does before anything
    asks the simulator or chooses a rerun. ``run_pipeline`` simulates the
    tests it is given, marks not applied: a caller of it splits here."""
    to_run: list[TestModel] = []
    marked: list[TestModel] = []
    for test in tests:
        (marked if test.model_id in marks else to_run).append(test)
    return to_run, marked


def every_skip_mark(test: TestModel) -> list[SkipMark]:
    """Every skip mark ``test`` carries, on any backend: the entry's own,
    then each ``backends`` block's that says ``skip``, in the file's order
    — what ``dstf discover`` lists."""
    marks = [SkipMark(EVERY_BACKEND, test.skip)] if test.skip is not None else []
    for where, block in (test.backend_marks or {}).items():
        if block.get("skip") is not None:
            marks.append(SkipMark(where, block["skip"]))
    return marks


@dataclass(frozen=True)
class TimeLimit:
    """A ``backends`` block's time limit: the block's key (``where``, as
    :class:`SkipMark`'s) and its seconds."""

    where: str
    seconds: int


def every_time_limit(test: TestModel) -> list[TimeLimit]:
    """Every ``backends`` block of ``test`` that says ``timeout``, in the
    file's order — what ``dstf discover`` lists."""
    return [
        TimeLimit(where, block["timeout"])
        for where, block in (test.backend_marks or {}).items()
        if block.get("timeout") is not None
    ]


def time_limit_here(test: TestModel, config: Config) -> TimeLimit | None:
    """The ``backends`` block's time limit that applies to ``test`` on
    ``config``'s backend and OS (the most specific one), or None when no
    block's does."""
    found = _block(test, config, "timeout")
    return None if found is None else TimeLimit(*found)


#: How to raise the limit when the one that fired is not known: a batch's
#: limit, the sum of its tests' (Dymola's), names none of them.
ANY_LIMIT_HINT = (
    "raise it per test with simulation.timeout in test_spec.json, "
    "for one backend with backends.<Backend>.timeout there, "
    "or globally with --timeout"
)

#: The run's own limit: ``--timeout``, which every other key wins over.
RUN_LIMIT = "--timeout"


def _limit(test: TestModel, config: Config) -> tuple[str, int | None]:
    """The key that sets ``test``'s time limit on ``config``'s backend and
    OS — ``backends.<where>.timeout``, ``simulation.timeout`` or
    :data:`RUN_LIMIT` — and its seconds (None for the run's, which only
    ``config.timeout`` holds). THE one order of the time limits."""
    here = time_limit_here(test, config)
    if here is not None:
        return f"backends.{here.where}.timeout", here.seconds
    if test.timeout is not None:
        return "simulation.timeout", test.timeout
    return RUN_LIMIT, None


def timeout_for(test: TestModel, config: Config) -> int:
    """The seconds ``test`` may run on ``config``'s backend and OS: its
    block's ``timeout``, else its ``simulation.timeout``, else the run's."""
    seconds = _limit(test, config)[1]
    return config.timeout if seconds is None else seconds


def limit_hint(test: TestModel, config: Config) -> str:
    """How to raise the time limit that stops ``test`` here, naming first
    the key that set it: what a timed-out run's message says, so its first
    advice is the limit that fired — raising ``simulation.timeout`` or
    ``--timeout`` does nothing for a test its backend's block stops. Then
    any key that would win over it once raised."""
    key = _limit(test, config)[0]
    if key == RUN_LIMIT:
        return (
            "the limit is the run's --timeout: raise it with --timeout, per "
            "test with simulation.timeout in test_spec.json, or for one "
            "backend with backends.<Backend>.timeout there"
        )
    if key == "simulation.timeout":
        return (
            "the limit is simulation.timeout in test_spec.json: raise it "
            "there, or for one backend with backends.<Backend>.timeout"
        )
    return f"the limit is {key} in test_spec.json: raise it there"
