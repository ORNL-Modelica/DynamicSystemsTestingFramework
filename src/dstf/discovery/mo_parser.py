"""Parse Modelica .mo files to extract UnitTests block info and experiment annotations."""

import re
from dataclasses import dataclass, field
from pathlib import Path


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


def _extract_within(text: str) -> str:
    """Extract the 'within' clause to get the parent package path."""
    m = re.search(r"within\s+([\w.]+)\s*;", text)
    return m.group(1) if m else ""


def _extract_model_name(text: str) -> str:
    """Extract the model/class name from the definition."""
    m = re.search(r"(?:model|block|class|package)\s+(\w+)", text)
    return m.group(1) if m else ""


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


_MO_STRING = re.compile(r'"(?:[^"\\]|\\.)*"', re.DOTALL)
_MO_LINE_COMMENT = re.compile(r"//[^\n]*")
_MO_BLOCK_COMMENT = re.compile(r"/\*[\s\S]*?\*/")


def _strip_modelica_literals(text: str) -> str:
    """Strip comments + string literals from Modelica source.

    Prevents false-positive matches where prose inside an
    ``annotation(Documentation(...))`` block mentions a keyword like
    ``UnitTests(...)`` or ``extends Foo``. Block comments replaced with
    a single space (may shift offsets, fine since scanners operate on
    the stripped text consistently).
    """
    out = _MO_BLOCK_COMMENT.sub(" ", text)
    out = _MO_LINE_COMMENT.sub("", out)
    out = _MO_STRING.sub(" ", out)
    return out


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


def _parse_experiment(text: str) -> ExperimentInfo | None:
    """Extract experiment() annotation parameters."""
    m = re.search(r"experiment\s*\(([^)]*)\)", text)
    if not m:
        return None

    param_text = m.group(1)
    info = ExperimentInfo()

    # StopTime (Modelica standard) or stopTime
    m2 = re.search(r"(?:S|s)topTime\s*=\s*([0-9eE.+-]+)", param_text)
    if m2:
        info.stop_time = float(m2.group(1))

    # Tolerance
    m2 = re.search(r"(?:T|t)olerance\s*=\s*([0-9eE.+-]+)", param_text)
    if m2:
        info.tolerance = float(m2.group(1))

    # __Dymola_Algorithm or method
    m2 = re.search(r'__Dymola_Algorithm\s*=\s*"([^"]+)"', param_text)
    if m2:
        info.method = m2.group(1)

    # __Dymola_NumberOfIntervals or numberOfIntervals
    m2 = re.search(r"(?:__Dymola_)?[Nn]umberOfIntervals\s*=\s*(\d+)", param_text)
    if m2:
        info.number_of_intervals = int(m2.group(1))

    # Interval (output interval length) — standard Modelica
    m2 = re.search(r"(?<!\w)[Ii]nterval\s*=\s*([0-9eE.+-]+)", param_text)
    if m2:
        info.output_interval = float(m2.group(1))

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
    model_name = _extract_model_name(text)
    if not model_name:
        return None

    # Skip the UnitTests component definition itself — it contains example
    # usage in documentation that the parser would pick up as a false positive
    if model_name == "UnitTests":
        return None

    model_id = f"{within}.{model_name}" if within else model_name
    experiment = _parse_experiment(text)

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
