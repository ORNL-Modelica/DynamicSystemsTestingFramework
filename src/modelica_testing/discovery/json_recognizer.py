"""JSON-driven Recognizer (PTA.2).

Lets users declare a custom test-recognition convention in `testing.json`
without writing Python. Schema:

    {
      "name": "<unique recognizer name>",
      "applies_to": ["modelica"],          # Config.source_type values
      "match": {
        "type": "component-instantiation" | "extends",
        ...                                # match-type-specific keys
      },
      "fields": {
        "<TestModel-field-name>": {
          "from": "parameter" | "constant" | "experiment-annotation",
          ...                              # source-specific keys
        },
        ...
      }
    }

Modelica match types:
  - `component-instantiation` (`component_name`): match any Modelica class
    that instantiates a component of the given class. Accepts the fully
    qualified name and any tail-suffix (Modelica's import rules).
  - `extends` (`class_pattern`): match any class with `extends X` where X
    matches the given fnmatch glob (e.g. `*Icons.Example`).

Field sources:
  - `parameter` (`name`, optional `shape`): extract from the matched
    component's parameter list. Only valid with `component-instantiation`
    matches. `shape`: `scalar` (default), `array`, `array_floats`.
  - `constant` (`value`): use the literal value verbatim.
  - `experiment-annotation` (`name`): extract from the standard Modelica
    `experiment(...)` annotation. Case-insensitive first letter (handles
    `StopTime` vs. `stopTime`).

Deferred (capture-and-revisit, see SESSION_HANDOFF.md and vision.md):
  - Match composition (`all-of` / `any-of`) — needed when multiple match
    patterns must combine (e.g. "extends X AND in folder Y").
  - Folder filter (`paths_include` / `paths_exclude`) — orthogonal to
    match patterns; constrains which files feed the recognizer.
  - More match types: `class-name-glob`, `annotation`. Add when asked.
  - More field sources: `annotation`. Add when asked.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Union

from .mo_parser import (
    _extract_balanced_braces,
    _extract_model_name,
    _extract_within,
    _parse_float_list,
    _parse_x_expressions,
)
from .recognizer import Recognizer, RecognizerResult


class RecognizerSpecError(ValueError):
    """Raised when a JSON recognizer spec is malformed."""


# Fields a JSON spec can write into a RecognizerResult. Keep in sync with
# RecognizerResult dataclass (model_id and source_file are filled implicitly).
_VALID_FIELDS = frozenset({
    "n_vars",
    "x_expressions",
    "x_raw",
    "x_reference",
    "error_expected",
    "stop_time",
    "tolerance",
    "method",
    "number_of_intervals",
    "output_interval",
    # PTA.4 — richer-contract fields
    "simulate_only",
    "requested_fmu_export",
    "requested_baselines",
})


# ---------------------------------------------------------------------------
# Match contexts
# ---------------------------------------------------------------------------

@dataclass
class ComponentMatch:
    """A component-instantiation match. Carries the parameter-list text so
    the `parameter` field source can extract from it."""
    component_class: str
    instance_name: str
    param_text: str


@dataclass
class ExtendsMatch:
    """An extends match."""
    extended_class: str


@dataclass
class ClassNameMatch:
    """A class-name-glob match (PTA-follow.3)."""
    qualified_name: str


@dataclass
class AnnotationMatch:
    """A generic-annotation match: the matched annotation's param text."""
    annotation_name: str
    param_text: str


@dataclass
class CompositeMatch:
    """A composite of one or more sub-matches (PTA-follow.2: all-of / any-of).

    Field-source interpreters that need a specific leaf-match shape (e.g.,
    `parameter` needs a ComponentMatch) traverse the composite via
    :func:`_find_match`.
    """
    matches: list


MatchContext = Union[ComponentMatch, ExtendsMatch, CompositeMatch]


def _find_match(match: MatchContext, type_):
    """Return the first sub-match of the given type, traversing composites."""
    if isinstance(match, type_):
        return match
    if isinstance(match, CompositeMatch):
        for m in match.matches:
            found = _find_match(m, type_)
            if found is not None:
                return found
    return None


# Match-type → field-sources allowed mapping, validated at parse time.
# `parameter` requires a parameter list, which only `component-instantiation`
# matches provide. Composite types (all-of / any-of) inherit the union of
# allowed sources across their children — see _allowed_sources_for.
_MATCH_TYPE_ALLOWED_FIELD_SOURCES = {
    "component-instantiation": frozenset({"parameter", "constant", "experiment-annotation", "annotation"}),
    "extends": frozenset({"constant", "experiment-annotation", "annotation"}),
    "class-name-glob": frozenset({"constant", "experiment-annotation", "annotation"}),
}


def _collect_leaf_match_types(match_spec: dict) -> set[str]:
    """Walk a match spec and return the set of leaf match types it contains."""
    t = match_spec.get("type")
    if t in ("all-of", "any-of"):
        out: set[str] = set()
        for child in match_spec.get("matchers", []):
            out |= _collect_leaf_match_types(child)
        return out
    return {t} if t else set()


def _allowed_sources_for(match_spec: dict) -> frozenset[str]:
    """Union of field sources allowed for any leaf match type in the spec."""
    leaf_types = _collect_leaf_match_types(match_spec)
    out: set[str] = set()
    for t in leaf_types:
        out |= _MATCH_TYPE_ALLOWED_FIELD_SOURCES.get(t, frozenset())
    return frozenset(out)


# ---------------------------------------------------------------------------
# Match interpreter registry
# ---------------------------------------------------------------------------

_MatchFn = Callable[[str, dict], Optional[MatchContext]]
_MATCH_INTERPRETERS: dict[str, _MatchFn] = {}


def _register_match(type_name: str):
    def deco(fn: _MatchFn) -> _MatchFn:
        _MATCH_INTERPRETERS[type_name] = fn
        return fn
    return deco


@_register_match("component-instantiation")
def _match_component_instantiation(content: str, spec: dict) -> Optional[ComponentMatch]:
    full = spec["component_name"]
    segments = full.split(".")
    # Try fully qualified, then progressively shorter tail-suffixes — most
    # specific first so longer matches win when alternatives overlap.
    tails = [".".join(segments[i:]) for i in range(len(segments))]
    pattern_str = "|".join(re.escape(t) for t in tails)
    pattern = re.compile(rf'\b(?:{pattern_str})\s+(\w+)\s*\(', re.DOTALL)
    # Strip annotations + string literals so prose mentioning the
    # component class doesn't false-trigger. Offsets inside the stripped
    # content stay valid for param-text extraction (block comments get
    # replaced with spaces, preserving offsets).
    stripped = _strip_modelica_literals(content)
    m = pattern.search(stripped)
    if not m:
        return None
    instance_name = m.group(1)
    matched_class = m.group(0).rsplit(instance_name, 1)[0].strip()
    paren_open = m.end() - 1
    depth = 0
    end = paren_open
    for i in range(paren_open, len(stripped)):
        if stripped[i] == "(":
            depth += 1
        elif stripped[i] == ")":
            depth -= 1
            if depth == 0:
                end = i
                break
    return ComponentMatch(
        component_class=matched_class,
        instance_name=instance_name,
        param_text=stripped[paren_open + 1:end],
    )


@_register_match("extends")
def _match_extends(content: str, spec: dict) -> Optional[ExtendsMatch]:
    pattern_glob = spec["class_pattern"]
    # Strip comments + string literals first — prose like "extends
    # ModelicaTestingLib.Icons.Example" inside a doc annotation would
    # otherwise register as a real extends, misidentifying icon classes
    # as tests.
    stripped = _strip_modelica_literals(content)
    for m in re.finditer(r'\bextends\s+([\w.]+)', stripped):
        extended = m.group(1)
        if fnmatch.fnmatchcase(extended, pattern_glob):
            return ExtendsMatch(extended_class=extended)
    return None


# Modelica string literals + comments. Stripped before source-scan regexes
# so prose inside a Documentation annotation doesn't trigger false matches
# on ``extends``, ``UnitTests(...)``, etc.
_MODELICA_STRING = re.compile(r'"(?:[^"\\]|\\.)*"', re.DOTALL)
_MODELICA_LINE_COMMENT = re.compile(r'//[^\n]*')
_MODELICA_BLOCK_COMMENT = re.compile(r'/\*[\s\S]*?\*/')


def _strip_modelica_literals(content: str) -> str:
    """Remove string literals + comments from Modelica source.

    Used by match functions that scan for declarations — annotation
    HTML / doc prose that happens to contain a keyword like
    ``extends`` would otherwise trigger false positives.
    """
    # Order matters: strip block comments first (they can contain //),
    # then line comments, then strings.
    out = _MODELICA_BLOCK_COMMENT.sub(" ", content)
    out = _MODELICA_LINE_COMMENT.sub("", out)
    out = _MODELICA_STRING.sub(" ", out)
    return out


@_register_match("class-name-glob")
def _match_class_name_glob(content: str, spec: dict) -> Optional[ClassNameMatch]:
    """Match a class whose fully qualified name matches an fnmatch glob."""
    pattern_glob = spec["class_pattern"]
    within = _extract_within(content)
    class_name = _extract_model_name(content)
    if not class_name:
        return None
    qualified = f"{within}.{class_name}" if within else class_name
    if fnmatch.fnmatchcase(qualified, pattern_glob):
        return ClassNameMatch(qualified_name=qualified)
    return None


@_register_match("all-of")
def _match_all_of(content: str, spec: dict) -> Optional[CompositeMatch]:
    """All child matchers must match; result aggregates all child contexts."""
    matches = []
    for child_spec in spec.get("matchers", []):
        child_fn = _MATCH_INTERPRETERS.get(child_spec.get("type"))
        if child_fn is None:
            return None
        result = child_fn(content, child_spec)
        if result is None:
            return None
        matches.append(result)
    return CompositeMatch(matches=matches) if matches else None


@_register_match("any-of")
def _match_any_of(content: str, spec: dict) -> Optional[CompositeMatch]:
    """First matching child wins; result wraps just that child."""
    for child_spec in spec.get("matchers", []):
        child_fn = _MATCH_INTERPRETERS.get(child_spec.get("type"))
        if child_fn is None:
            continue
        result = child_fn(content, child_spec)
        if result is not None:
            return CompositeMatch(matches=[result])
    return None


# ---------------------------------------------------------------------------
# Field-source interpreter registry
# ---------------------------------------------------------------------------

_FieldFn = Callable[[str, MatchContext, dict], Any]
_FIELD_INTERPRETERS: dict[str, _FieldFn] = {}


def _register_field(source_name: str):
    def deco(fn: _FieldFn) -> _FieldFn:
        _FIELD_INTERPRETERS[source_name] = fn
        return fn
    return deco


def _coerce_scalar(raw: str) -> Any:
    """Best-effort coerce a raw token to bool / int / float / str."""
    raw = raw.strip().strip('"')
    if not raw:
        return None
    if raw == "true":
        return True
    if raw == "false":
        return False
    try:
        if "." in raw or "e" in raw or "E" in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        try:
            return float(raw)
        except ValueError:
            return raw


def _extract_param_value(param_text: str, name: str, shape: str) -> Any:
    """Extract a parameter value of the given shape from a parameter list."""
    if shape == "array":
        m = re.search(rf'\b{re.escape(name)}\s*=\s*\{{', param_text)
        if not m:
            return None
        raw = _extract_balanced_braces(param_text, m.end() - 1)
        return _parse_x_expressions(raw)
    if shape == "array_floats":
        m = re.search(rf'\b{re.escape(name)}\s*=\s*\{{', param_text)
        if not m:
            return None
        raw = _extract_balanced_braces(param_text, m.end() - 1)
        return _parse_float_list(raw)
    # scalar — try quoted string first, then bare token
    m = re.search(rf'\b{re.escape(name)}\s*=\s*"([^"]*)"', param_text)
    if m:
        return m.group(1)
    m = re.search(rf'\b{re.escape(name)}\s*=\s*([^,\s)]+)', param_text)
    if not m:
        return None
    return _coerce_scalar(m.group(1))


@_register_field("parameter")
def _field_parameter(content: str, match: MatchContext, spec: dict) -> Any:
    cm = _find_match(match, ComponentMatch)
    if cm is None:
        return None  # composite that didn't include a component-instantiation
    return _extract_param_value(cm.param_text, spec["name"], spec.get("shape", "scalar"))


@_register_field("constant")
def _field_constant(content: str, match: MatchContext, spec: dict) -> Any:
    return spec["value"]


@_register_field("annotation")
def _field_annotation(content: str, match: MatchContext, spec: dict) -> Any:
    """Extract a parameter from an arbitrary Modelica annotation block.

    Spec: ``{"from": "annotation", "annotation": "<name>", "name": "<key>"}``
    where ``annotation`` is the annotation block name (e.g. ``__MyVendor_Test``)
    and ``name`` is the parameter inside it.
    """
    ann_name = spec["annotation"]
    m = re.search(
        rf'\b{re.escape(ann_name)}\s*\(',
        content,
    )
    if not m:
        return None
    # Extract balanced parentheses
    paren_open = m.end() - 1
    depth = 0
    end = paren_open
    for i in range(paren_open, len(content)):
        if content[i] == "(":
            depth += 1
        elif content[i] == ")":
            depth -= 1
            if depth == 0:
                end = i
                break
    param_text = content[paren_open + 1:end]
    return _extract_param_value(param_text, spec["name"], spec.get("shape", "scalar"))


@_register_field("experiment-annotation")
def _field_experiment_annotation(content: str, match: MatchContext, spec: dict) -> Any:
    m = re.search(r'experiment\s*\(([^)]*)\)', content)
    if not m:
        return None
    name = spec["name"]
    if not name:
        return None
    pt = m.group(1)
    shape = spec.get("shape", "scalar")
    # Try the user-given name, then alt-case first letter — Modelica writes
    # both StopTime and stopTime interchangeably.
    candidates = [name]
    if name[0].isalpha():
        alt = name[0].swapcase() + name[1:]
        if alt != name:
            candidates.append(alt)
    for candidate in candidates:
        value = _extract_param_value(pt, candidate, shape)
        if value is not None:
            return value
    return None


# ---------------------------------------------------------------------------
# JsonRecognizer + parser
# ---------------------------------------------------------------------------

class JsonRecognizer(Recognizer):
    """Recognizer configured by a JSON spec."""

    def __init__(self, spec: dict):
        self.name: str = spec["name"]
        self.applies_to = frozenset(spec.get("applies_to", ["modelica"]))
        self._match_spec: dict = spec["match"]
        self._field_specs: dict = spec.get("fields", {})
        # PTA-follow.1: optional path filters. Globs match against the path
        # relative to the discovery base (config.library_dir for modelica).
        self._paths_include: list[str] = list(spec.get("paths_include", []))
        self._paths_exclude: list[str] = list(spec.get("paths_exclude", []))

    def applies_to_path(self, source_file: Path, base: Path) -> bool:
        if not self._paths_include and not self._paths_exclude:
            return True
        try:
            rel = source_file.relative_to(base).as_posix()
        except ValueError:
            # File isn't under base — exclude conservatively.
            return False
        if self._paths_include and not any(
            fnmatch.fnmatchcase(rel, g) for g in self._paths_include
        ):
            return False
        if any(fnmatch.fnmatchcase(rel, g) for g in self._paths_exclude):
            return False
        return True

    def recognize(self, source_file: Path) -> Optional[RecognizerResult]:
        try:
            content = source_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        match_fn = _MATCH_INTERPRETERS[self._match_spec["type"]]
        match_ctx = match_fn(content, self._match_spec)
        if match_ctx is None:
            return None
        within = _extract_within(content)
        model_name = _extract_model_name(content)
        if not model_name:
            return None
        # Defensive: skip if the matched component class IS the class being
        # defined here (the bundled recognizer hits this when the UnitTests
        # component's documentation contains example usage; user-defined
        # recognizers can hit the same on their template's defining file).
        cm = _find_match(match_ctx, ComponentMatch)
        if cm is not None:
            short_class = cm.component_class.rsplit(".", 1)[-1]
            if short_class == model_name:
                return None
        model_id = f"{within}.{model_name}" if within else model_name
        result = RecognizerResult(model_id=model_id, source_file=source_file)
        for field_name, field_spec in self._field_specs.items():
            field_fn = _FIELD_INTERPRETERS[field_spec["from"]]
            value = field_fn(content, match_ctx, field_spec)
            if value is not None:
                setattr(result, field_name, value)
        return result


def _validate_match_spec(rec_name: str, match: dict) -> None:
    """Validate a (possibly composite) match spec, raising on error."""
    if not isinstance(match, dict) or "type" not in match:
        raise RecognizerSpecError(
            f"recognizer '{rec_name}' has malformed match (missing 'type')"
        )
    mtype = match["type"]
    if mtype not in _MATCH_INTERPRETERS:
        raise RecognizerSpecError(
            f"recognizer '{rec_name}' has unknown match type '{mtype}'. "
            f"Known: {sorted(_MATCH_INTERPRETERS)}"
        )
    if mtype in ("all-of", "any-of"):
        children = match.get("matchers")
        if not isinstance(children, list) or not children:
            raise RecognizerSpecError(
                f"recognizer '{rec_name}' match.{mtype} requires 'matchers' (non-empty list)"
            )
        for child in children:
            _validate_match_spec(rec_name, child)
        return
    if mtype == "component-instantiation" and "component_name" not in match:
        raise RecognizerSpecError(
            f"recognizer '{rec_name}' match.component-instantiation requires 'component_name'"
        )
    if mtype == "extends" and "class_pattern" not in match:
        raise RecognizerSpecError(
            f"recognizer '{rec_name}' match.extends requires 'class_pattern'"
        )
    if mtype == "class-name-glob" and "class_pattern" not in match:
        raise RecognizerSpecError(
            f"recognizer '{rec_name}' match.class-name-glob requires 'class_pattern'"
        )


def parse_recognizer_spec(spec: dict) -> Recognizer:
    """Validate and parse a JSON recognizer spec into a Recognizer instance."""
    if not isinstance(spec, dict):
        raise RecognizerSpecError(
            f"recognizer spec must be a dict, got {type(spec).__name__}"
        )
    if "name" not in spec:
        raise RecognizerSpecError("recognizer spec missing required field 'name'")
    name = spec["name"]
    if "match" not in spec:
        raise RecognizerSpecError(
            f"recognizer '{name}' missing required field 'match'"
        )
    match = spec["match"]
    _validate_match_spec(name, match)
    match_type = match["type"]
    fields = spec.get("fields", {})
    if not isinstance(fields, dict):
        raise RecognizerSpecError(
            f"recognizer '{name}' fields must be a dict"
        )
    allowed_sources = _allowed_sources_for(match)
    for fname, fspec in fields.items():
        if fname not in _VALID_FIELDS:
            raise RecognizerSpecError(
                f"recognizer '{name}' targets unknown field '{fname}'. "
                f"Known: {sorted(_VALID_FIELDS)}"
            )
        if not isinstance(fspec, dict) or "from" not in fspec:
            raise RecognizerSpecError(
                f"recognizer '{name}' field '{fname}' missing 'from'"
            )
        source = fspec["from"]
        if source not in _FIELD_INTERPRETERS:
            raise RecognizerSpecError(
                f"recognizer '{name}' field '{fname}' uses unknown source '{source}'. "
                f"Known: {sorted(_FIELD_INTERPRETERS)}"
            )
        if source not in allowed_sources:
            raise RecognizerSpecError(
                f"recognizer '{name}' field '{fname}' source '{source}' isn't compatible "
                f"with match type '{match_type}'. Allowed for this match type: "
                f"{sorted(allowed_sources)}"
            )
        if source == "parameter" and "name" not in fspec:
            raise RecognizerSpecError(
                f"recognizer '{name}' field '{fname}' (source=parameter) requires 'name'"
            )
        if source == "constant" and "value" not in fspec:
            raise RecognizerSpecError(
                f"recognizer '{name}' field '{fname}' (source=constant) requires 'value'"
            )
        if source == "experiment-annotation" and "name" not in fspec:
            raise RecognizerSpecError(
                f"recognizer '{name}' field '{fname}' "
                f"(source=experiment-annotation) requires 'name'"
            )
        if source == "annotation":
            if "annotation" not in fspec:
                raise RecognizerSpecError(
                    f"recognizer '{name}' field '{fname}' "
                    f"(source=annotation) requires 'annotation'"
                )
            if "name" not in fspec:
                raise RecognizerSpecError(
                    f"recognizer '{name}' field '{fname}' "
                    f"(source=annotation) requires 'name'"
                )
    return JsonRecognizer(spec)
