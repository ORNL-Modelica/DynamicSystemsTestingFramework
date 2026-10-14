"""Whether a value read from a JSON file is of the type its key takes, and
how a refusal says which type that is.

ONE table, read by both strict files: ``test_spec.json``'s ``simulation``
block (:mod:`dstf.discovery.spec_parser`) and every key of ``testing.json``
and of a backend's settings block (:mod:`dstf.config_shape`). Each key says
its type as a JSON-Schema fragment; this module is what holds a value to
it.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from typing import Any


def is_number(value: object) -> bool:
    """A JSON number that is one: no bool, no NaN, no infinity — and no
    integer too large for a float (a 1 and 400 zeros), which no simulator
    can be handed: ``math.isfinite`` raises on it."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


#: A JSON-Schema ``type`` → whether a value is one, and how a refusal says
#: it. A whole number is WRITTEN as one: ``3``, not ``3.0`` — which JSON
#: Schema lets by, and which then reached code that slices and counts with
#: it as a float.
HOLDS: dict[str, Callable[[object], bool]] = {
    "number": is_number,
    "integer": lambda v: is_number(v) and isinstance(v, int),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
}
SAID = {
    "number": "a number",
    "integer": "a whole number",
    "string": "a string",
    "boolean": "true or false",
    "array": "a list",
    "object": "an object",
}
_SAID_MANY = {
    "number": "numbers",
    "integer": "whole numbers",
    "string": "strings",
    "boolean": "true or false values",
    "array": "lists",
    "object": "objects",
}


def said(schema: Mapping[str, Any]) -> str | None:
    """What a value of ``schema`` is, in words — ``a whole number of 1 or
    more``, ``a list of strings``, ``one of "linux", "windows"`` — or None
    for a fragment that names no one ``type`` (a ``oneOf``: its key says
    its own rule)."""
    if "enum" in schema:
        return "one of " + ", ".join(json.dumps(choice) for choice in schema["enum"])
    kind = schema.get("type")
    if not isinstance(kind, str):
        return None
    text = SAID[kind]
    if kind == "string" and schema.get("minLength"):
        text = "a non-empty string"
    items = schema.get("items")
    if kind == "array" and isinstance(items, dict) and items.get("type") in _SAID_MANY:
        many = _SAID_MANY[items["type"]]
        text += f" of non-empty {many}" if items.get("minLength") else f" of {many}"
    if "minimum" in schema:
        text += f" of {schema['minimum']} or more"
    return text


def holds(schema: Mapping[str, Any], value: object) -> bool:
    """Whether ``value`` is what ``schema`` takes: one of its ``enum``, or
    its ``type``, a list's ``items``, a number's ``minimum`` and a string's
    ``minLength`` — the words :func:`said` says. A fragment with neither
    holds anything here."""
    if "enum" in schema:
        return any(value == choice for choice in schema["enum"])
    kind = schema.get("type")
    if not isinstance(kind, str):
        return True
    if not HOLDS[kind](value):
        return False
    if kind == "string" and len(value) < schema.get("minLength", 0):  # type: ignore[arg-type]
        return False
    items = schema.get("items")
    if kind == "array" and isinstance(items, dict):
        return all(holds(items, item) for item in value)  # type: ignore[attr-defined]
    return "minimum" not in schema or bool(value >= schema["minimum"])
