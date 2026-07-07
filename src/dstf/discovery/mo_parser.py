"""Parse Modelica .mo files to extract UnitTests block info and experiment annotations."""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class UnitTestInfo:
    """Information extracted from a UnitTests block in a .mo file."""

    n: int = 1
    x_expressions: list[str] = field(default_factory=list)
    x_raw: str = ""  # Raw text of x={...} for complex cases (cat, etc.)
    x_reference: list[float] | None = None
    error_expected: float = 1e-6


@dataclass
class ExperimentInfo:
    """Simulation parameters from experiment() annotation."""

    stop_time: float | None = None
    tolerance: float | None = None
    method: str | None = None
    number_of_intervals: int | None = None
    output_interval: float | None = None


@dataclass
class MoParseResult:
    """Full parse result from a .mo file."""

    model_id: str  # Fully qualified Modelica path
    mo_file: Path
    unit_test: UnitTestInfo | None = None
    experiment: ExperimentInfo | None = None


_CLASS_DECL_RE = re.compile(r"\b(?:model|block|class|package)\s+(\w+)")


def _extract_within(text: str) -> str:
    """Extract the 'within' clause to get the parent package path.

    Operates on comment/string-stripped text — review 2026-07-06 finding 48:
    a ``within`` mentioned in a comment must not shadow the real clause.
    """
    stripped = _strip_modelica_literals(text)
    m = re.search(r"\bwithin\s+([\w.]+)\s*;", stripped)
    return m.group(1) if m else ""


def _extract_model_name(text: str, source: object = "") -> str:
    """Extract the first class-like declaration's name.

    Operates on comment/string-stripped text with a word-boundary anchor —
    review 2026-07-06 finding 48: the word "model" in a comment above the
    class must not shadow the real declaration.

    TODO(finding 55): single-file multi-class storage is NOT supported —
    only the FIRST top-level class is parsed. When a second top-level class
    is detected (a class declaration after the first class's ``end Name;``),
    a warning names what was skipped. ``source`` is optional context (file
    path) for that warning.
    """
    stripped = _strip_modelica_literals(text)
    m = _CLASS_DECL_RE.search(stripped)
    if not m:
        return ""
    name = m.group(1)
    # review 2026-07-06 finding 55: warn about sibling top-level classes the
    # parser skips instead of silently dropping their tests.
    end_m = re.search(rf"\bend\s+{re.escape(name)}\s*;", stripped[m.end() :])
    if end_m:
        extra = _CLASS_DECL_RE.search(stripped, m.end() + end_m.end())
        if extra:
            logger.warning(
                "%s: multiple top-level classes in one file are not supported; "
                "parsed %r, skipped %r (and any further classes)",
                source or "<unknown file>",
                name,
                extra.group(1),
            )
    return name


def _extract_balanced_braces(text: str, start: int) -> str:
    """Extract text within balanced braces starting from position of '{'."""
    depth = 0
    i = start
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : i]
        i += 1
    return text[start + 1 :]


def _parse_x_expressions(raw: str) -> list[str]:
    """Parse x={expr1, expr2, ...} into individual expressions.

    Handles simple comma-separated variables but returns raw text for
    complex cases like cat() or array comprehensions.
    """
    # If it contains cat( or 'for', it's complex — return as single raw entry
    stripped = raw.strip()
    if "cat(" in stripped or " for " in stripped:
        return [stripped]

    # Split by commas, respecting nested brackets/parens
    expressions = []
    depth = 0
    current = []
    for ch in stripped:
        if ch in "({[":
            depth += 1
            current.append(ch)
        elif ch in ")}]":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            expr = "".join(current).strip()
            if expr:
                expressions.append(expr)
            current = []
        else:
            current.append(ch)
    last = "".join(current).strip()
    if last:
        expressions.append(last)

    return expressions


def _parse_float_list(raw: str) -> list[float] | None:
    """Parse a simple {val1, val2, ...} list of floats."""
    try:
        parts = raw.strip().split(",")
        return [float(p.strip()) for p in parts if p.strip()]
    except ValueError:
        return None


def _strip_modelica_literals(text: str) -> str:
    """Blank comments + string literals from Modelica source (1:1, newlines
    kept, so offsets into the result index the original — findings 48/50 slice
    the original for string-valued params the blanking removed).

    Single-pass lexer, NOT a sequence of independent regexes. The old 3-pass
    approach (block-comment, then line-comment, then string) was unsound
    because the three languages interleave: a ``//`` inside a string literal
    (``"modelica://..."`` — ubiquitous in Modelica annotation URIs) was eaten
    by the line-comment pass, blanking the string's closing quote and
    desyncing every following string. On TRANSFORM's InverseParameterization
    that swallowed the real ``experiment(StopTime=10)`` — StopTime silently
    defaulted to 1.0 and the sim ran a tenth of its length (regression found
    2026-07-07). A stateful scan is the only correct model: at each position we
    are in exactly one of code / string / line-comment / block-comment, and
    the delimiters that matter depend on which.
    """
    out: list[str] = []
    i, n = 0, len(text)
    NORMAL, STRING, LINE, BLOCK = 0, 1, 2, 3
    state = NORMAL
    while i < n:
        c = text[i]
        two = text[i : i + 2]
        if state == NORMAL:
            if two == "//":
                out.append("  ")
                i += 2
                state = LINE
            elif two == "/*":
                out.append("  ")
                i += 2
                state = BLOCK
            elif c == '"':
                out.append(" ")
                i += 1
                state = STRING
            else:
                out.append(c)
                i += 1
        elif state == STRING:
            if c == "\\" and i + 1 < n:
                # escape: blank the backslash + the escaped char together, so a
                # ``\"`` inside the string is NOT read as the closing quote.
                out.append("  " if text[i + 1] != "\n" else " \n")
                i += 2
            elif c == '"':
                out.append(" ")
                i += 1
                state = NORMAL
            else:
                out.append("\n" if c == "\n" else " ")
                i += 1
        elif state == LINE:
            if c == "\n":
                out.append("\n")
                i += 1
                state = NORMAL
            else:
                out.append(" ")
                i += 1
        else:  # BLOCK  (Modelica block comments do not nest)
            if two == "*/":
                out.append("  ")
                i += 2
                state = NORMAL
            else:
                out.append("\n" if c == "\n" else " ")
                i += 1
    return "".join(out)


def _parse_unit_tests(text: str) -> UnitTestInfo | None:
    """Extract UnitTests block parameters from model text."""
    # Match the UnitTests declaration — may span multiple lines
    # Patterns: "UnitTests unitTests(" or "ErrorAnalysis.UnitTests unitTests("
    # Strip comments + strings first so documentation prose that mentions
    # ``UnitTests(...)`` for example purposes doesn't misidentify the
    # surrounding model as a test.
    text = _strip_modelica_literals(text)
    pattern = re.compile(
        r"(?:Utilities\.ErrorAnalysis\.)?UnitTests\s+\w+\s*\(", re.DOTALL
    )
    match = pattern.search(text)
    if not match:
        return None

    # Find the end of the parameter list — match balanced parentheses
    start = match.end() - 1  # position of the '('
    depth = 0
    end = start
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                end = i
                break

    param_text = text[start + 1 : end]

    info = UnitTestInfo()

    # Extract n=
    m = re.search(r"\bn\s*=\s*(\d+)", param_text)
    if m:
        info.n = int(m.group(1))

    # Extract x={...}
    m = re.search(r"\bx\s*=\s*\{", param_text)
    if m:
        brace_start = m.end() - 1
        raw = _extract_balanced_braces(param_text, brace_start)
        info.x_raw = raw
        info.x_expressions = _parse_x_expressions(raw)
    elif re.search(r"\bx\s*=\s*(?:cat|fill|zeros|ones|linspace)\s*\(", param_text):
        # x=cat(1, ...) or x=fill(...) etc. without outer braces
        m2 = re.search(r"\bx\s*=\s*((?:cat|fill|zeros|ones|linspace)\s*\()", param_text)
        if m2:
            paren_start = param_text.index("(", m2.start())
            depth2 = 0
            end2 = paren_start
            for i in range(paren_start, len(param_text)):
                if param_text[i] == "(":
                    depth2 += 1
                elif param_text[i] == ")":
                    depth2 -= 1
                    if depth2 == 0:
                        end2 = i
                        break
            raw = param_text[m2.start() + 2 : end2 + 1]  # include "func(...)"
            info.x_raw = raw.strip()
            info.x_expressions = [raw.strip()]
    else:
        # x=varName or x=some.qualified.name — bare variable reference
        m2 = re.search(r"\bx\s*=\s*([\w.]+(?:\[[\w.,\s]+\])?)", param_text)
        if m2:
            raw = m2.group(1).strip()
            info.x_raw = raw
            info.x_expressions = [raw]

    # Extract x_reference={...}
    m = re.search(r"\bx_reference\s*=\s*\{", param_text)
    if m:
        brace_start = m.end() - 1
        raw = _extract_balanced_braces(param_text, brace_start)
        info.x_reference = _parse_float_list(raw)

    # Extract errorExpected=
    m = re.search(r"\berrorExpected\s*=\s*([0-9eE.+-]+)", param_text)
    if m:
        try:
            info.error_expected = float(m.group(1))
        except ValueError:
            pass

    return info


def _find_experiment_span(stripped: str) -> tuple[int, int] | None:
    """Locate the ``experiment(...)`` parameter text in stripped source.

    Returns (start, end) offsets of the text BETWEEN the balanced outer
    parentheses, or None when no experiment annotation exists. Balanced-paren
    scan instead of ``[^)]*`` — review 2026-07-06 finding 48: a nested group
    like ``__Dymola_Tuning(...)`` must not truncate the parameter list.
    Offsets are valid in the original text too (1:1 stripping).
    """
    m = re.search(r"\bexperiment\s*\(", stripped)
    if not m:
        return None
    open_pos = m.end() - 1
    depth = 0
    for i in range(open_pos, len(stripped)):
        if stripped[i] == "(":
            depth += 1
        elif stripped[i] == ")":
            depth -= 1
            if depth == 0:
                return (open_pos + 1, i)
    return (open_pos + 1, len(stripped))  # unbalanced — take the rest


def _parse_number(
    param_text: str,
    key_pattern: str,
    source: object,
    field_name: str,
) -> float | None:
    """Extract ``<key> = <number>`` from an annotation parameter list.

    review 2026-07-06 finding 56: accept plain numbers incl. scientific
    notation only; an expression (``0.5*3600``) or garbage capture logs a
    warning naming the file + field and is skipped — it must neither
    silently truncate (old ``[0-9eE.+-]+`` regex kept just ``0.5``) nor
    crash discovery with an uncaught ValueError.
    """
    m = re.search(rf"{key_pattern}\s*=\s*([^,]+)", param_text)
    if not m:
        return None
    raw = m.group(1).strip()
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "%s: ignoring non-numeric experiment %s=%r",
            source or "<unknown file>",
            field_name,
            raw,
        )
        return None


def _parse_experiment(text: str, source: object = "") -> ExperimentInfo | None:
    """Extract experiment() annotation parameters.

    Scans comment/string-stripped text (review 2026-07-06 finding 48: a
    ``// experiment(StopTime=5)`` comment must not shadow the real
    annotation) but recovers string values (``__Dymola_Algorithm="..."``)
    from the ORIGINAL text via the offset-preserving stripper.
    """
    stripped = _strip_modelica_literals(text)
    span = _find_experiment_span(stripped)
    if span is None:
        return None

    start, end = span
    param_text = stripped[start:end]  # numeric fields: comments stripped
    param_orig = text[start:end]  # string fields: literals intact
    info = ExperimentInfo()

    # StopTime (Modelica standard) or stopTime
    info.stop_time = _parse_number(param_text, r"\b[Ss]topTime", source, "StopTime")

    # Tolerance
    info.tolerance = _parse_number(param_text, r"\b[Tt]olerance", source, "Tolerance")

    # __Dymola_Algorithm or method — quoted string, read from original text
    m2 = re.search(r'__Dymola_Algorithm\s*=\s*"([^"]+)"', param_orig)
    if m2:
        info.method = m2.group(1)

    # __Dymola_NumberOfIntervals or numberOfIntervals
    m2 = re.search(r"(?:__Dymola_)?[Nn]umberOfIntervals\s*=\s*(\d+)", param_text)
    if m2:
        info.number_of_intervals = int(m2.group(1))

    # Interval (output interval length) — standard Modelica
    interval = _parse_number(param_text, r"(?<!\w)[Ii]nterval", source, "Interval")
    if interval is not None:
        info.output_interval = interval

    return info


def parse_mo_file(path: Path) -> MoParseResult | None:
    """Parse a .mo file and extract UnitTests and experiment info.

    Returns None if the file cannot be parsed or has no UnitTests block.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    unit_test = _parse_unit_tests(text)
    if unit_test is None:
        return None

    within = _extract_within(text)
    model_name = _extract_model_name(text, source=path)
    if not model_name:
        return None

    # Skip the UnitTests component definition itself — it contains example
    # usage in documentation that the parser would pick up as a false positive
    if model_name == "UnitTests":
        return None

    model_id = f"{within}.{model_name}" if within else model_name
    experiment = _parse_experiment(text, source=path)

    return MoParseResult(
        model_id=model_id,
        mo_file=path,
        unit_test=unit_test,
        experiment=experiment,
    )


# ---------------------------------------------------------------------------
# Bundled recognizer (PTA.1)
# ---------------------------------------------------------------------------

from .recognizer import (  # noqa: E402  (late import: registers the bundled recognizer after its deps are defined)
    Recognizer,
    RecognizerResult,
    register,
)


class BundledModelicaUnitTestsRecognizer(Recognizer):
    """The default Modelica recognizer.

    Matches any class that instantiates the ``UnitTests`` component (with
    parameters ``n``, ``x={...}``, optional ``error_expected``) plus the
    standard ``experiment(...)`` annotation. Wraps :func:`parse_mo_file` and
    translates its ``MoParseResult`` into the framework-neutral
    :class:`RecognizerResult` shape.
    """

    name = "modelica:bundled-unit-tests"
    applies_to = frozenset({"modelica"})

    def recognize(self, source_file: Path) -> RecognizerResult | None:
        parsed = parse_mo_file(source_file)
        if parsed is None:
            return None
        ut = parsed.unit_test
        exp = parsed.experiment
        return RecognizerResult(
            model_id=parsed.model_id,
            source_file=parsed.mo_file,
            n_vars=ut.n if ut else None,
            x_expressions=list(ut.x_expressions) if ut else [],
            x_raw=ut.x_raw if ut else "",
            x_reference=list(ut.x_reference)
            if ut and ut.x_reference is not None
            else None,
            error_expected=ut.error_expected if ut else None,
            stop_time=exp.stop_time if exp else None,
            tolerance=exp.tolerance if exp else None,
            method=exp.method if exp else None,
            number_of_intervals=exp.number_of_intervals if exp else None,
            output_interval=exp.output_interval if exp else None,
        )


register(BundledModelicaUnitTestsRecognizer())
