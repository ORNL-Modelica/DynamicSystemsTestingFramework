"""UI schema derived from a mode's Config dataclass — what ``export-schema``
writes as JSON-Schema and the authoring tool's form emitter renders.

Each :class:`ComparisonMode`'s Config is a frozen dataclass with strictly
typed fields. :func:`derive_schema` reads those types via
:func:`typing.get_type_hints` and returns the introspected field list —
types, defaults, ``Literal`` choices and the display metadata — as a data
artifact. :mod:`dstf.reporting.schema_export` turns it into the published
JSON Schema; the authoring tool builds its forms from the same
introspection. The report has no forms and does not use this module.

The :class:`ComparisonMode` ABC stays pure compute — no UI coupling on the
mode class. The bridge lives here instead, keyed on mode name.
"""

from __future__ import annotations

import dataclasses
import types
import typing
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Union

from .sides import Sides

# ---------------------------------------------------------------------------
# Schema types
# ---------------------------------------------------------------------------

#: Recognized field types. ``passthrough`` is the fallback for complex types
#: (nested dataclasses, list[dict], etc.) that introspection cannot reduce to
#: a scalar or an enum — a consumer renders those as raw JSON.
FIELD_TYPES = {"float", "int", "bool", "str", "enum", "passthrough"}


@dataclass
class FieldSpec:
    name: str
    type: str  # one of FIELD_TYPES
    default: Any = None
    optional: bool = False
    choices: list[str] | None = None
    label: str | None = None
    help: str | None = None
    # UI hints for number inputs. ``ui_max`` is a SOFT cap: a consumer that
    # renders an input raises it to fit a larger current value rather than
    # clamping the value down.
    ui_min: float | None = None
    ui_max: float | None = None


@dataclass
class Schema:
    mode: str | None = None
    fields: list[FieldSpec] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "fields": [dataclasses.asdict(f) for f in self.fields],
        }


# ---------------------------------------------------------------------------
# Introspection
# ---------------------------------------------------------------------------


def derive_schema(config_cls: type, *, mode: str | None = None) -> Schema:
    """Inspect a dataclass and return its UI schema.

    Handles ``float``, ``int``, ``bool``, ``str``, ``Optional[X]``,
    ``Literal[...]``, and ``Optional[Literal[...]]``. Unknown shapes
    (``list[dict]``, nested dataclasses, etc.) degrade to
    ``type="passthrough"`` so callers can render a raw-JSON fallback
    without crashing.

    Reads ``metadata`` from :func:`dataclasses.field` for display hints:
      - ``label``: human-readable label (defaults to a titleized name)
      - ``help``: tooltip text
    """
    if not dataclasses.is_dataclass(config_cls):
        raise TypeError(f"{config_cls!r} is not a dataclass")

    hints = typing.get_type_hints(config_cls)
    fields_out: list[FieldSpec] = []
    for f in dataclasses.fields(config_cls):
        hint = hints.get(f.name, f.type)
        kind, optional, choices = _classify_type(hint)
        default = _field_default(f)
        meta: Mapping[str, Any] = f.metadata or {}
        fields_out.append(
            FieldSpec(
                name=f.name,
                type=kind,
                default=default,
                optional=optional,
                choices=choices,
                label=meta.get("label") or _titleize(f.name),
                help=meta.get("help"),
                ui_min=meta.get("ui_min"),
                ui_max=meta.get("ui_max"),
            )
        )
    return Schema(mode=mode, fields=fields_out)


def _classify_type(hint: Any) -> tuple[str, bool, list[str] | None]:
    """Return (field_kind, optional, choices-if-enum) for a type hint."""
    origin = typing.get_origin(hint)
    args = typing.get_args(hint)

    # Optional[X] — unwrap, mark optional, recurse. Accept both spellings:
    # typing.Optional/Union[X, None] (origin is typing.Union) and the PEP 604
    # form X | None (origin is types.UnionType on 3.10+).
    if origin in (Union, types.UnionType) and type(None) in args:
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            inner_kind, _, choices = _classify_type(non_none[0])
            return inner_kind, True, choices
        # Multi-type Optional union: give up, passthrough.
        return "passthrough", True, None

    # Literal[...] — enum.
    if origin is typing.Literal:
        return "enum", False, [str(a) for a in args]

    # Plain scalars.
    if hint is float:
        return "float", False, None
    if hint is Sides:
        # A stand-in until the `sides` field kind exists (step E1, Task 7):
        # the forms and the exported schema keep showing one number.
        return "float", False, None
    if hint is bool:
        return "bool", False, None
    if hint is int:
        return "int", False, None
    if hint is str:
        return "str", False, None

    # Everything else (list[dict], nested dataclass, forward ref, ...).
    return "passthrough", False, None


def _field_default(f: dataclasses.Field) -> Any:
    if f.default is not dataclasses.MISSING:
        if isinstance(f.default, Sides):
            # The stand-in's other half (Task 7 writes the pair): the number.
            return f.default.upper
        return f.default
    if f.default_factory is not dataclasses.MISSING:
        try:
            return f.default_factory()
        except Exception:
            return None
    return None


def _titleize(name: str) -> str:
    return name.replace("_", " ").strip().capitalize()
