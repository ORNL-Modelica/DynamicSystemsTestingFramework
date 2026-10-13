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
    #: The component's instance name in the model — its variables are
    #: ``<instance>.x[i]`` in the result file.
    instance: str = "unitTests"
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


_WS_RUN_RE = re.compile(r"\s+")
_WS_AFTER_OPEN_RE = re.compile(r"([\[({])\s+")
_WS_BEFORE_CLOSE_RE = re.compile(r"\s+([\])},])")
#: A comma that is not the expression's last character: exactly one space
#: follows it in the normal form. A comma directly before a closing bracket
#: needs no exception here — the space this puts after it is taken back by
#: ``_WS_BEFORE_CLOSE_RE``, which runs after.
_COMMA_RE = re.compile(r",\s*(?!$)")


def normalize_expression(text: str) -> str:
    """One expression in its normal form, without its source layout.

    A ``.mo`` file wraps a long expression over two lines, or spells an
    index ``[1,2]`` where the result file spells it ``[1, 2]``; that is the
    file's formatting, not part of what the expression names. The normal
    form: whitespace runs become one space; none is kept directly after an
    opening bracket or directly before a closing bracket or a comma; and
    every comma is followed by exactly one space — which is how Dymola's own
    result names read (``a[1, 2]``). So ``a.b[\\n        1, 1]``,
    ``a.b[ 1 ,1 ]`` and ``a.b[1,1]`` are one expression, and a source
    reformat does not read as a changed expression on compare.

    Normalizing an expression already in normal form changes nothing.

    OUT OF SCOPE: a comma inside a string literal within an expression —
    the rule is textual and would put a space inside the literal. On the
    ``.mo`` UnitTests path no literal can reach here: ``_parse_unit_tests``
    blanks comments and string literals before it looks for ``x=`` at all.
    A JSON recognizer's ``parameter`` source slices the ORIGINAL text, so
    one could reach here that way; no ``x={...}`` in the libraries this
    parses contains one.
    """
    text = _WS_RUN_RE.sub(" ", text.strip())
    text = _COMMA_RE.sub(", ", text)
    text = _WS_AFTER_OPEN_RE.sub(r"\1", text)
    return _WS_BEFORE_CLOSE_RE.sub(r"\1", text)


def _parse_x_expressions(raw: str) -> list[str]:
    """Parse x={expr1, expr2, ...} into individual expressions.

    Handles simple comma-separated variables but returns raw text for
    complex cases like cat() or array comprehensions. Every expression
    returned is normalized (:func:`normalize_expression`).
    """
    # If it contains cat( or 'for', it's complex — return as single raw entry
    stripped = raw.strip()
    if "cat(" in stripped or " for " in stripped:
        return [normalize_expression(stripped)]

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
            expr = normalize_expression("".join(current))
            if expr:
                expressions.append(expr)
            current = []
        else:
            current.append(ch)
    last = normalize_expression("".join(current))
    if last:
        expressions.append(last)

    return expressions


def _take_argument(text: str, start: int) -> str:
    """The rest of one argument of a parameter list, from ``start``.

    Ends at the next top-level comma or at the end of the list (the closing
    bracket that was never opened inside it), nesting respected — so
    ``x=a.b[1].c, x_reference={1}`` yields ``a.b[1].c`` and ``x=a.b[1, :]``
    yields the whole index, where a regex over bracket contents stopped at
    ``a.b[1]`` and ``a.b``.
    """
    depth = 0
    for i in range(start, len(text)):
        c = text[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                return text[start:i]
            depth -= 1
        elif c == "," and depth == 0:
            return text[start:i]
    return text[start:]


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
    are in exactly one of code / string / line-comment / block-comment /
    quoted name, and the delimiters that matter depend on which.

    A quoted name (``'a"b'``, ``'in ner'``) is CODE and is kept as it is,
    but what it holds delimits nothing: a ``"``, ``//`` or ``/*`` inside one
    starts no string and no comment. ``model 'a"b'`` was read as a string
    running to the ``"`` of its own ``end 'a"b';``. It ends at its closing
    ``'`` — or at the end of its line, which no quoted name crosses, so an
    apostrophe astray in code costs one line and not the rest of the file.
    """
    out: list[str] = []
    i, n = 0, len(text)
    NORMAL, STRING, LINE, BLOCK, QUOTED = 0, 1, 2, 3, 4
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
                if c == "'":
                    state = QUOTED
        elif state == QUOTED:
            if c == "\\" and i + 1 < n and text[i + 1] != "\n":
                out.append(text[i : i + 2])  # an escape: `\'` closes nothing
                i += 2
            else:
                out.append(c)
                i += 1
                if c in "'\n":
                    state = NORMAL
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


def _parse_unit_tests(text: str, source: object = "") -> UnitTestInfo | None:
    """Extract UnitTests block parameters from model text."""
    # Match the UnitTests declaration — may span multiple lines
    # Patterns: "UnitTests unitTests(" or "ErrorAnalysis.UnitTests unitTests("
    # Strip comments + strings first so documentation prose that mentions
    # ``UnitTests(...)`` for example purposes doesn't misidentify the
    # surrounding model as a test.
    text = _strip_modelica_literals(text)
    # The leading ``\b`` is what makes this the UnitTests component and not
    # any class whose name merely ends in it (``MyUnitTests bar(`` was read
    # as an instance named ``bar``). A dotted prefix still matches: the
    # optional ``Utilities.ErrorAnalysis.`` group, and any longer prefix
    # (``TRANSFORM.Utilities.ErrorAnalysis.``) because the ``.`` before
    # ``UnitTests`` is itself a word boundary.
    pattern = re.compile(
        r"\b(?:Utilities\.ErrorAnalysis\.)?UnitTests\s+(\w+)\s*\(", re.DOTALL
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

    # group(1) is the component's INSTANCE name (the word after the class),
    # not the class: its variables are ``<instance>.x[i]``.
    info = UnitTestInfo(instance=match.group(1))

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
            info.x_expressions = [normalize_expression(raw)]
    else:
        # x=varName, x=some.qualified.name, x=a.b[1].c, x=a.b[1, :] — a bare
        # expression. It runs to the end of ITS argument; a bracket regex
        # truncated it (``a.b[1].c`` became ``a.b[1]``, ``a.b[1, :]`` became
        # ``a.b``), which then rode into the baseline as the expression.
        m2 = re.search(r"\bx\s*=\s*", param_text)
        if m2:
            raw = _take_argument(param_text, m2.end()).strip()
            if raw:
                info.x_raw = raw
                info.x_expressions = [normalize_expression(raw)]

    # Extract x_reference={...}
    m = re.search(r"\bx_reference\s*=\s*\{", param_text)
    if m:
        brace_start = m.end() - 1
        raw = _extract_balanced_braces(param_text, brace_start)
        info.x_reference = _parse_float_list(raw)

    # Extract errorExpected= (finding 56 applies here too: an expression like
    # ``1e-3*2`` must warn and keep the default, not silently truncate)
    error_expected = _parse_number(
        param_text, r"\berrorExpected", source, "errorExpected"
    )
    if error_expected is not None:
        info.error_expected = error_expected

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

    unit_test = _parse_unit_tests(text, source=path)
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
# A class by its name: where it is stored, and its own experiment annotation
# ---------------------------------------------------------------------------


@dataclass
class LocatedClass:
    """A class of the library found by its qualified name: the file that
    holds it and its own ``experiment(...)`` annotation (None when it has
    none)."""

    mo_file: Path
    experiment: ExperimentInfo | None


@dataclass(frozen=True)
class _ClassSpan:
    """One class declaration in stripped source: ``start`` at its keyword,
    ``body`` just past its name, ``end`` past its ``end Name;`` — or, for a
    ``short`` class (``model A = B(...) annotation(...);``), past the ``;``
    that ends its one statement."""

    name: str
    start: int
    body: int
    end: int
    short: bool = False
    #: How many brackets of the scanned text are open around a short class.
    #: 0 is a declaration of that text; one written inside a modification
    #: (``Pipe p(redeclare model HT = Ideal);``) is no class of the body the
    #: statement stands in (:func:`_named`).
    depth: int = 0


#: A Modelica name: an identifier, or a quoted one (``'in ner'``).
_NAME = r"(\w+|'(?:[^'\\]|\\.)*')"

#: A class declaration or an ``end Name;``, in comment/string-stripped text.
#: The kinds are :data:`_CLASS_DECL_RE`'s — the ones that can hold a model.
#: Group 2 marks a SHORT class (``model A = B(...)``), which has no body and
#: no ``end``; ``model extends A`` is named by what it extends.
_CLASS_TOKEN_RE = re.compile(
    rf"\b(?:model|block|class|package)\s+(?:extends\s+)?{_NAME}(\s*=)?"
    rf"|\bend\s+{_NAME}\s*;"
)

_OPENERS, _CLOSERS = "([{", ")]}"
#: A bracket of stripped source (group 1) — or a quoted name, which is code
#: there and whose brackets open and close nothing (``Real 'a(b' = 1;``). It
#: ends as the lexer ends it: at its closing ``'`` or at the end of its line.
_BRACKET_RE = re.compile(r"'(?:\\[^\n]|[^'\n])*'?|([()\[\]{}])")


def _statement_end(stripped: str, start: int, end: int) -> int:
    """Just past the ``;`` that ends the statement at ``start`` — the first
    one outside every bracket — or ``end`` when there is none. A short
    class written INSIDE a modification (``A(redeclare model M = B
    annotation(...))``) ends before the bracket that closes the list it
    sits in: what follows that bracket is the enclosing statement's."""
    depth = 0
    for i in range(start, end):
        c = stripped[i]
        if c in _OPENERS:
            depth += 1
        elif c in _CLOSERS:
            if depth == 0:
                return i
            depth -= 1
        elif depth == 0 and c == ";":
            return i + 1
    return end


def _direct_classes(stripped: str, start: int, end: int) -> list[_ClassSpan]:
    """The classes declared DIRECTLY in ``stripped[start:end]`` — a file, or
    one class's body — in source order; a class nested deeper belongs to
    the one that holds it.

    A stack of open declarations, closed by the ``end`` that names the top
    one: ``end if;`` / ``end for;`` and the ``end`` of a kind not tracked
    here (a function, a record) name nothing on it and close nothing. A
    short class opens nothing: it is one statement, and its span runs to
    that statement's ``;`` — it carries an ``annotation(...)`` of its own,
    which is not the enclosing class's.

    A short class written inside a bracket — a modification's ``redeclare
    model HT = Ideal`` — is in the list with the ``depth`` it stands at: its
    text is not the enclosing class's own (its annotation neither), and it
    is no class of this text by name. Taken for one, it hid the class ``HT``
    declared after it, which was then found with no annotation — so did a
    class redeclared in a sibling short class's modification.
    """
    out: list[_ClassSpan] = []
    stack: list[tuple[str, int, int]] = []
    depth, at = 0, start
    for m in _CLASS_TOKEN_RE.finditer(stripped, start, end):
        for found in _BRACKET_RE.finditer(stripped, at, m.start()):
            if found.group(1):
                depth += 1 if found.group(1) in _OPENERS else -1
        at = m.end()
        closed = m.group(3)
        if closed is not None:
            if stack and stack[-1][0] == closed:
                name, opened, body = stack.pop()
                if not stack:
                    out.append(_ClassSpan(name, opened, body, m.end()))
        elif m.group(2) is None:
            stack.append((m.group(1), m.start(), m.end()))
        elif not stack:
            after = _statement_end(stripped, m.end(), end)
            out.append(_ClassSpan(m.group(1), m.start(), m.end(), after, True, depth))
    return out


def find_class(
    library_dir: Path, library_name: str, model_id: str
) -> LocatedClass | None:
    """Locate the class ``model_id`` in the library's files and read its
    own ``experiment(...)`` annotation — for a test that names a model no
    recognizer found (declared in ``test_spec.json`` only).

    A Modelica class is stored as its own file (``A/B/C.mo``), as a
    directory (``A/B/C/package.mo``), or inside the file of a class that
    holds it (``A/B/package.mo``, ``A/B.mo``, ... up to the library's
    ``package.mo``). Those are tried from the most specific: a handful of
    path probes per name, never a walk of the library. A file is trusted
    only when its ``within`` clause and its class say it is the one looked
    for. None when the name is not a class of this library (another
    library's model, a component). A short class (``model S = L.Rod
    annotation(...);``) is one, read like any other: its own annotation,
    not the one of the class it is made from.

    The annotation is the class's OWN: the text of the classes nested in
    it — a short class's one statement and a class of a quoted name
    included — is blanked first, so a nested model's ``experiment`` — which
    the source spells before the enclosing class's annotation — is not read
    as the parent's. It is then read by :func:`_parse_experiment`, the reader
    a scanned test's goes through.
    """
    names = model_id.split(".")
    if len(names) < 2 or names[0] != library_name:
        return None
    inner = names[1:]
    for k in range(len(inner), -1, -1):
        owner, nested = inner[:k], inner[k:]
        folder = library_dir.joinpath(*owner)
        candidates = [folder / "package.mo"]
        if owner:
            candidates.insert(0, folder.parent / f"{owner[-1]}.mo")
        for path in candidates:
            if not path.is_file():
                continue
            found = _class_in_file(path, [library_name, *owner], nested)
            if found is not None:
                return found
    return None


def _class_in_file(
    path: Path, top: list[str], nested: list[str]
) -> LocatedClass | None:
    """The class ``nested`` deep inside the file's top class ``top`` (the
    top class itself for an empty ``nested``), or None when the file does
    not declare it."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if _extract_within(text) != ".".join(top[:-1]):
        return None
    stripped = _strip_modelica_literals(text)
    span = _named(_direct_classes(stripped, 0, len(stripped)), top[-1])
    for name in nested:
        if span is None:
            return None
        # A short class has no body: the classes of its own are the ones
        # its modification redeclares, one bracket into its statement.
        inside = _direct_classes(stripped, span.body, span.end)
        span = _named(inside, name, depth=1 if span.short else 0)
    if span is None:
        return None
    # Blanked 1:1, so `_parse_experiment`'s offsets into the original text
    # (its string-valued fields) still hold.
    own = list(text[span.start : span.end])
    for child in _direct_classes(stripped, span.body, span.end):
        for i in range(child.start - span.start, child.end - span.start):
            if own[i] != "\n":
                own[i] = " "
    return LocatedClass(
        mo_file=path, experiment=_parse_experiment("".join(own), source=path)
    )


def _named(spans: list[_ClassSpan], name: str, depth: int = 0) -> _ClassSpan | None:
    """The class of that name declared at ``depth`` — a short class too:
    ``model S = L.Rod annotation(experiment(StopTime=13));`` is a class of
    the library with an annotation of its own, in its one statement. (It
    was passed over, and a run said of ``Examples/ShortOne.mo`` that the
    model was "not found under source_path".) It has no body: nothing is
    found inside it but the classes its own modification redeclares.

    ``depth`` is 0 in a file and in a class's body: a ``redeclare model X
    = …`` inside a component's or an ``extends`` clause's modification
    there is no class ``X`` of that body."""
    return next((s for s in spans if s.name == name and s.depth == depth), None)


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
            unit_tests_instance=ut.instance if ut else None,
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
