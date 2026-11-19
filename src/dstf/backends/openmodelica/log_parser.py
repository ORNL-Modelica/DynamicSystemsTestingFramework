"""Parse OpenModelica's ``omc`` stdout for per-test results.

When ``omc`` runs a ``.mos`` that ends with a top-level ``simulate(...)`` call,
it REPL-echoes the returned record to stdout:

    record SimulationResult
        resultFile = "/path/to/result_res.mat",
        simulationOptions = "...",
        messages = "...",
        timeFrontend = 0.19,
        timeBackend = 0.04,
        ...
        timeTotal = 1.25
    end SimulationResult;

This module extracts that block, classifies success/failure, and surfaces any
Error/Warning/Notification line outside it (:func:`notices`): the script's
``getErrorString()`` calls echo their strings before the record and after
it, and the one after ``simulate()`` holds why a model did not build. The return shape is
a small ``ParsedOmcOutput`` dataclass the backend consumes to populate
``TestRunResult``.

The parser is total — any shape of input produces a ``ParsedOmcOutput``, with
``success=False`` and ``timings=None`` on truncation / missing record.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ParsedOmcOutput:
    """Structured view of what the backend needs from omc stdout."""

    success: bool
    result_file: str = ""
    messages: str = ""
    timings: dict[str, float] | None = None
    error_notices: list[str] = field(default_factory=list)


# Match 'record SimulationResult … end SimulationResult;' lazily across
# however many lines the record spans.
_RECORD_RE = re.compile(
    r"record SimulationResult(?P<body>.*?)end SimulationResult;",
    re.DOTALL,
)

# Match a quoted string-valued record field. The value may span multiple
# lines (OM's 'messages' often does). Handles embedded \" and \\ escapes.
_STR_FIELD_RE = re.compile(
    r'\b(\w+)\s*=\s*"((?:\\.|[^"\\])*)"',
    re.DOTALL,
)

# Match a numeric-valued record field. Run AFTER stripping string fields from
# the body (digits in messages would otherwise get misread).
_NUM_FIELD_RE = re.compile(
    r"\b(\w+)\s*=\s*([-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)",
)

# OM field name -> our internal key
_TIMING_KEYS = {
    "timeFrontend": "frontend",
    "timeBackend": "backend",
    "timeSimCode": "simcode",
    "timeTemplates": "templates",
    "timeCompile": "compile",
    "timeSimulation": "simulation",
    "timeTotal": "total",
}

# A notice: 'Error: ...' / 'Warning: ...' / 'Notification: ...', led by
# where omc points it when it does — '[<file>:4:3-4:14:writable] Error: ...'.
# A file's path may hold a colon (a Windows drive), so it ends at the
# position, not at the first colon.
_NOTICE = re.compile(
    r"(?:\[(?P<file>[^\]\n]+?):(?P<pos>\d+:\d+-\d+:\d+:(?:writable|readonly))\] )?"
    r"(?P<kind>Error|Warning|Notification)\b[: ]"
)


def shown_path(file: str | Path, root: Path | None) -> str:
    """``file`` as an OpenModelica message shows it: its path under the
    library's ``root``, whole for one outside it (an MSL file) or with no
    root."""
    try:
        if root is None:
            raise ValueError
        return Path(file).resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return str(file)


def notice(line: str, root: Path | None = None) -> str | None:
    """``line`` as a failed run's message keeps it when it is one of omc's
    notices, else None. THE one reading of a notice, for the batch run's
    stdout and a live worker's ``getErrorString()``: a line that starts
    with its kind, or with the place omc points it at — the line a model
    that does not translate is refused with, ``[<file>:4:3-4:14:writable]
    Error: Variable y not found in scope Bad.`` — kept with that place,
    its file under ``root`` (:func:`shown_path`). A script echoes each
    ``getErrorString()`` as a quoted string, so the first line of one
    starts with its quote: that is not part of the notice."""
    text = line.strip().removeprefix('"')
    found = _NOTICE.match(text)
    if found is None:
        return None
    if found["file"] is None:
        return text
    rest = text[found.start("kind") :]
    return f"[{shown_path(found['file'], root)}:{found['pos']}] {rest}"


def notices(text: str, root: Path | None = None) -> list[str]:
    """Every notice of ``text``, in its order (:func:`notice`)."""
    return [n for line in text.splitlines() if (n := notice(line, root)) is not None]


def notice_kind(text: str) -> str:
    """A kept notice's kind, past its place: ``Error``, ``Warning`` or
    ``Notification``; ``""`` for a line that is none."""
    found = _NOTICE.match(text)
    return found["kind"] if found else ""


def parse_omc_stdout(text: str, root: Path | None = None) -> ParsedOmcOutput:
    """Parse captured omc stdout into a ``ParsedOmcOutput``: its record,
    and the notices outside it, each place under the library's ``root``."""
    m = _RECORD_RE.search(text)
    outside = text if not m else text[: m.start()] + text[m.end() :]
    error_notices = notices(outside, root)

    if not m:
        return ParsedOmcOutput(success=False, error_notices=error_notices)

    body = m.group("body")

    # Extract string-valued fields first (they may contain digits we don't
    # want to misinterpret as timings).
    str_fields: dict[str, str] = {}
    for fm in _STR_FIELD_RE.finditer(body):
        str_fields[fm.group(1)] = fm.group(2)

    # Strip strings from the body before pulling numbers.
    body_no_strings = _STR_FIELD_RE.sub("", body)
    timings: dict[str, float] = {}
    for fm in _NUM_FIELD_RE.finditer(body_no_strings):
        name = fm.group(1)
        if name in _TIMING_KEYS:
            try:
                timings[_TIMING_KEYS[name]] = float(fm.group(2))
            except ValueError:
                pass

    result_file = str_fields.get("resultFile", "")
    messages = str_fields.get("messages", "")

    success = bool(result_file) and "Failed" not in messages

    return ParsedOmcOutput(
        success=success,
        result_file=result_file,
        messages=messages,
        timings=timings or None,
        error_notices=error_notices,
    )
