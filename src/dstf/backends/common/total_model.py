"""The loop a backend's :meth:`~dstf.backends.base.Backend.uses` runs over a
live simulator: one question per test, each answered by a file the tool
writes of the model and everything it uses, read here.

What the tool wrote and what cannot be read of it are the test's answer:
a file missing, empty, cut off or unreadable is that test's reason, never
an empty answer (a test that uses nothing) and never the end of the
command. Each question is bounded in time: neither OMPython nor Dymola's
interface gives up on a simulator that hangs or dies inside one.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Collection
from pathlib import Path

from ...discovery.mo_parser import UnclosedClass
from ...discovery.test_registry import TestModel
from ...discovery.uses import one_line, project_classes

#: A backend's question about one test: given the test and the file to
#: write, None when the tool says it wrote the file, else its reason.
Ask = Callable[[TestModel, Path], "str | None"]


def read_answer(file: Path, tops: Collection[str], tool: str) -> frozenset[str] | str:
    """The project's classes in the file ``tool`` wrote of one model, or
    the reason it cannot be read: ``<tool> wrote no readable model for it:
    <why>``."""
    try:
        text = file.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        why = "no file"
    except OSError as exc:
        why = exc.strerror or str(exc)
    else:
        if not text.strip():
            why = "the file is empty"
        else:
            try:
                return project_classes(text, tops)
            except UnclosedClass as exc:
                why = str(exc)
    return one_line(f"{tool} wrote no readable model for it: {why}", tool)


#: The seconds one question may take, by default (a backend's
#: ``question_bound``): a real one takes under a second.
QUESTION_BOUND = 120.0


class _NoAnswer(Exception):
    """The tool did not answer one question within its bound."""


def _within(bound: float, ask: Ask, test: TestModel, file: Path) -> str | None:
    """``ask(test, file)`` in a thread of its own, waited for ``bound``
    seconds; its exception is raised here. A daemon thread: one still
    parked in a call to a hung tool keeps no process alive."""
    box: dict[str, object] = {}
    done = threading.Event()

    def _run() -> None:
        try:
            box["said"] = ask(test, file)
        except BaseException as exc:
            box["exc"] = exc
        finally:
            done.set()

    threading.Thread(target=_run, daemon=True, name="dstf-uses-question").start()
    if not done.wait(bound):
        raise _NoAnswer
    if "exc" in box:
        raise box["exc"]  # type: ignore[misc]
    return box["said"]  # type: ignore[return-value]


def ask_each(
    tests: list[TestModel],
    folder: Path,
    ask: Ask,
    tool: str,
    tops: Collection[str],
    bound: float = QUESTION_BOUND,
) -> dict[str, frozenset[str] | str]:
    """Ask ``tool`` about each test in turn, its file ``<folder>/<n>.mo``:
    per ``model_id``, the project's classes in what it wrote, or its
    reason. An exception from ``ask`` (the tool stopped answering) is
    raised: no test is answered for.

    A question not answered within ``bound`` seconds ends the asking: that
    test's reason says so, and every test after it is not asked and says
    why — each counted as could-not-be-asked. The caller closes the tool,
    which kills it."""
    answers: dict[str, frozenset[str] | str] = {}
    for n, test in enumerate(tests):
        file = folder / f"{n}.mo"
        try:
            said = _within(bound, ask, test, file)
        except _NoAnswer:
            answers[test.model_id] = f"{tool} did not answer within {bound:g} s"
            for rest in tests[n + 1 :]:
                answers[rest.model_id] = f"not asked: {tool} stopped answering"
            break
        answers[test.model_id] = (
            said if said is not None else read_answer(file, tops, tool)
        )
    return answers
