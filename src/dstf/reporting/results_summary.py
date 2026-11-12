"""Several runs' verdicts side by side — the facts ``dstf summary`` prints.
Data only: the words and the layout are ``reporting.results_summary_text``'s.

A run is a ``verdicts.json`` (`reporting.verdicts`) under a labelled
directory: ``LABEL=DIR``, one or more, a label repeating where one change
was run in several places (a Windows and a Linux work dir). Each file is
one cell of a matrix: its column the label, its row the backend/os its
``metadata`` names — ``backend``, the reference folder's family
(``Dymola``), never ``backend_name``, the testing.json entry ("Dymola
2026x") — so a row is a ``<Backend>/<os>`` folder of the reference root.

A cell counts its tests as the file does: PASS over ``totals.total``, which
holds NOT RUN too. With a base label, a test not passing in a cell is "not
new" when the base label's cell of the SAME backend/os failed it too (FAIL
or SIM_FAIL) or ended it with the same status word — a base NO_REF or NOT
RUN scored nothing, so a failure over it is new; one the base cell does not
hold is ``not in <base>``; and a backend/os the base label has no run of
has no such count, said in the cell. Nothing is judged and nothing is recomputed: every word is the
file's.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .status import FAILURES, PASS
from .verdicts import VERDICTS_FILE

#: The longest reason a list row holds, in characters.
REASON_LENGTH = 200


class SummaryError(Exception):
    """A request the runs cannot answer; one sentence."""


@dataclass(frozen=True)
class NotPassing:
    """A test of a cell whose status is not PASS."""

    model_id: str
    #: the file's status word: FAIL, SIM_FAIL, NO_REF, NOT RUN
    status: str
    #: what failed: the first failing criterion that is not advisory, else
    #: the message's last non-empty line; "" when the file says neither
    reason: str
    #: with a base: the base cell's status word for it, or ``not in
    #: <base>``; None with no base, or no base run of this backend/os
    base: str | None = None

    @property
    def not_new(self) -> bool:
        """The base failed it too, or ended it with the same status word
        (NO_REF on both); False with no base to tell."""
        return self.base is not None and (
            self.base in FAILURES or self.base == self.status
        )


@dataclass(frozen=True)
class Cell:
    """One run: a label's verdicts in one backend/os."""

    label: str
    folder: str
    path: Path
    passed: int
    total: int
    #: the file's totals as it gives them, by status word
    totals: dict[str, int]
    #: its tests that did not pass, in the file's order
    not_passing: list[NotPassing] = field(default_factory=list)
    #: of those, the ones that are not new (:attr:`NotPassing.not_new`);
    #: None with no base, in the base's own cell, or with no base run of this backend/os
    not_new: int | None = None
    #: why there is no such count where there is a base ("no main run for
    #: Dymola/linux"); "" otherwise
    base_note: str = ""


@dataclass(frozen=True)
class Summary:
    #: the labels in the order first given: the matrix's columns
    labels: list[str]
    #: every backend/os a run is of: the rows, backends alphabetical, then OS
    folders: list[str]
    cells: dict[tuple[str, str], Cell]
    base: str | None = None


def _verdict_files(label: str, folder: Path) -> list[Path]:
    """Every verdicts.json under ``folder``, not under a ``reports``
    directory (a per-test page's), sorted."""
    if not folder.is_dir():
        raise SummaryError(f"{label}={folder}: no such directory")
    found = sorted(
        p
        for p in folder.rglob(VERDICTS_FILE)
        if p.is_file() and "reports" not in p.relative_to(folder).parts[:-1]
    )
    if not found:
        raise SummaryError(
            f"{label}={folder}: no {VERDICTS_FILE} in it (a scoring `dstf run` "
            "or `dstf compare` writes one in its work dir)"
        )
    return found


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise SummaryError(f"{path}: not JSON ({e})") from e
    if not isinstance(data, dict):
        raise SummaryError(f"{path}: not a verdicts file (no JSON object)")
    return data


def run_folder(meta: Any) -> str | None:
    """The ``<Backend>/<os>`` folder a run's metadata names — a
    ``verdicts.json``'s or a ``status.json``'s, one shape — from its
    ``backend`` (the reference folder's family, never ``backend_name``) and
    its ``os``; None where it names no backend and os."""
    backend = meta.get("backend") if isinstance(meta, dict) else None
    os_name = meta.get("os") if isinstance(meta, dict) else None
    if not (isinstance(backend, str) and backend and isinstance(os_name, str)):
        return None
    return f"{backend}/{os_name}"


def _folder(path: Path, data: dict) -> str:
    folder = run_folder(data.get("metadata"))
    if folder is None:
        raise SummaryError(
            f"{path}: its metadata names no backend and os (it was written "
            "without the run's status.json)"
        )
    return folder


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _tests(data: dict) -> list[dict]:
    tests = data.get("tests")
    return [t for t in tests if isinstance(t, dict)] if isinstance(tests, list) else []


def failing_criterion(tree: Any) -> str:
    """The first failing criterion of a verdicts tree that is not advisory
    (depth first, in order), as ``label: criterion (error)``; "" for none.
    A node that passed is not searched: a leaf failing under an ``or`` that
    passed did not fail the test."""
    if not isinstance(tree, dict) or tree.get("passed") is True:
        return ""
    if tree.get("kind") == "leaf":
        if tree.get("passed") is not False or tree.get("advisory"):
            return ""
        text = str(tree.get("label") or tree.get("name") or "")
        if tree.get("criterion"):
            text += f": {tree['criterion']}"
        if tree.get("error"):
            text += f" ({tree['error']})"
        return text
    for child in tree.get("children") or []:
        found = failing_criterion(child)
        if found:
            return found
    return ""


#: An OpenModelica error's tag: ``[translation:error:63]``. What precedes
#: it is the call omc echoes (``sendExpression(simulate(…), False)']: ``),
#: which names the model and its settings, never why it failed.
_ERROR_TAG = re.compile(r"\[\w+:error:\d+\]")
#: A line said once for a run of it (``backends.common.failure_message``):
#: a warning repeated on the way, seldom the reason.
_REPEATED = re.compile(r"\(repeated \d+ times\)$")


def _message_reason(message: Any) -> str:
    """The piece of a failed run's message that says why: from its first
    OpenModelica error tag to that line's end; else its last non-empty
    line that is not a repeated one (a traceback's first line says
    nothing), a repeated line only when nothing else is."""
    if not isinstance(message, str):
        return ""
    tag = _ERROR_TAG.search(message)
    if tag:
        return message[tag.start() :].splitlines()[0].strip()
    lines = [line.strip() for line in message.splitlines() if line.strip()]
    said = [line for line in lines if not _REPEATED.search(line)] or lines
    return said[-1] if said else ""


def reason(test: dict) -> str:
    """What a list row says failed, at most :data:`REASON_LENGTH`
    characters from its start: the first failing criterion; else what the
    message says of why (:func:`_message_reason`)."""
    text = failing_criterion(test.get("tree")) or _message_reason(test.get("message"))
    if len(text) > REASON_LENGTH:
        text = text[: REASON_LENGTH - 1] + "…"
    return text


def _folder_key(folder: str) -> tuple[str, ...]:
    return tuple(folder.split("/"))


def read_summary(runs: list[tuple[str, Path]], base: str | None = None) -> Summary:
    """The matrix over ``runs`` (``(label, dir)`` in the order given), and
    with ``base`` each cell's tests against that label's. Raises
    :class:`SummaryError` for a directory with no verdicts.json, a file
    with no backend/os, two files for one label and backend/os, or a
    ``base`` that is no label."""
    labels = list(dict.fromkeys(label for label, _ in runs))
    if base is not None and base not in labels:
        raise SummaryError(
            f"--base {base}: no such label (the labels are {', '.join(labels)})"
        )
    read: dict[tuple[str, str], tuple[Path, dict]] = {}
    for label, directory in runs:
        for path in _verdict_files(label, Path(directory)):
            data = _read(path)
            key = (label, _folder(path, data))
            if key in read and read[key][0].resolve() == path.resolve():
                raise SummaryError(
                    f"{label}: {read[key][0]} is given twice (one DIR twice, or "
                    "one DIR inside another)"
                )
            if key in read:
                raise SummaryError(
                    f"{label}: two {VERDICTS_FILE} for {key[1]}, {read[key][0]} "
                    f"and {path}: pass each work dir as its own LABEL=DIR"
                )
            read[key] = (path, data)

    statuses = {
        key: {str(t.get("model_id")): str(t.get("status")) for t in _tests(data)}
        for key, (_, data) in read.items()
    }
    cells = {}
    for (label, folder), (path, data) in read.items():
        given = data.get("totals")
        totals: dict = given if isinstance(given, dict) else {}
        base_tests = (
            statuses.get((base, folder)) if base is not None and label != base else None
        )
        rows = []
        for t in _tests(data):
            status = str(t.get("status"))
            if status == PASS:
                continue
            model_id = str(t.get("model_id"))
            said = None
            if base_tests is not None:
                said = base_tests.get(model_id, f"not in {base}")
            rows.append(NotPassing(model_id, status, reason(t), said))
        not_new = None
        note = ""
        if base_tests is not None:
            not_new = sum(r.not_new for r in rows)
        elif base is not None and label != base:
            note = f"no {base} run for {folder}"
        cells[(label, folder)] = Cell(
            label=label,
            folder=folder,
            path=path,
            passed=_count(totals.get(PASS)),
            total=_count(totals.get("total")),
            totals={str(k): _count(v) for k, v in totals.items()},
            not_passing=rows,
            not_new=not_new,
            base_note=note,
        )
    folders = sorted({folder for _, folder in read}, key=_folder_key)
    return Summary(labels=labels, folders=folders, cells=cells, base=base)
