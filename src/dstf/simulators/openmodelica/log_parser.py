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
Error/Warning/Notification lines that appeared BEFORE it. The return shape is
a small ``ParsedOmcOutput`` dataclass the runner consumes to populate
``TestRunResult``.

The parser is total — any shape of input produces a ``ParsedOmcOutput``, with
``success=False`` and ``timings=None`` on truncation / missing record.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class ParsedOmcOutput:
    """Structured view of what the runner needs from omc stdout."""

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

# Lines like 'Error: ...' / 'Warning: ...' / 'Notification: ...'
_NOTICE_RE = re.compile(
    r"^\s*(?:Error|Warning|Notification)\b[: ].*$",
    re.MULTILINE,
)


def parse_omc_stdout(text: str) -> ParsedOmcOutput:
    """Parse captured omc stdout into a ``ParsedOmcOutput``."""
    m = _RECORD_RE.search(text)
    preamble = text if not m else text[: m.start()]
    error_notices = [mo.group(0).strip() for mo in _NOTICE_RE.finditer(preamble)]

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
