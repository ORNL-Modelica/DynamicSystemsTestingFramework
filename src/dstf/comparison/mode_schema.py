"""UI schema derived from a mode's Config dataclass — what ``spec schema``
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

from .sides import Sides, as_written

# ---------------------------------------------------------------------------
# Schema types
# ---------------------------------------------------------------------------

#: Recognized field types. ``sides`` is a tolerance with two sides
#: (:class:`~.sides.Sides`): a number, or a ``{"lower", "upper"}`` pair.
#: ``passthrough`` is the fallback for complex types (nested dataclasses,
#: list[dict], etc.) that introspection cannot reduce to a scalar or an
#: enum — a consumer renders those as raw JSON.
FIELD_TYPES = {"float", "int", "bool", "str", "enum", "sides", "passthrough"}

#: What the two sides of a ``sides`` field are called, lower first, unless
#: its metadata says (``side_labels``): a value's are below and above its
#: target. A time tolerance names its own — ``early`` / ``late``.
SIDE_LABELS = ("−", "+")


@dataclass
class FieldSpec:
    """One field of a mode's settings, as :func:`derive_schema` reads it.

    Attributes:
        name: The key a criterion writes.
        type: Its kind: ``float``, ``int``, ``bool``, ``str``, ``enum``,
            ``sides`` or ``passthrough``.
        default: Its value when the criterion does not write it.
        optional: Whether it may be null.
        choices: A choice's values.
        label: Its name in a form.
        help: What it does.
        ui_min: The lowest a form offers.
        ui_max: The highest a form offers at first; a larger value raises it.
        side_labels: A two-sided tolerance's names for its sides.
    """

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
    # A ``sides`` field's two labels, lower first; None on every other kind.
    side_labels: tuple[str, str] | None = None


@dataclass
class Schema:
    """A mode's settings as :func:`derive_schema` reads them.

    Attributes:
        mode: The mode's name.
        fields: Its fields, in order.
    """

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
    """The fields of a mode's settings dataclass, as the forms of the authoring
    tool and the published JSON schema show them.

    Each field's type gives its kind: ``float``, ``int``, ``bool``, ``str``, a
    ``Literal`` of strings (a choice), :class:`~dstf.comparison.sides.Sides`
    (a tolerance with two sides), each optional with ``| None``; any other type
    is shown as raw JSON. Its ``metadata`` gives the rest:

    - ``label``: the field's name in a form;
    - ``help``: what it does, in a sentence for the person writing it — the
      reference page and the schema's ``description``;
    - ``ui_min`` / ``ui_max``: a number field's range in a form;
    - ``side_labels``: what a two-sided tolerance's sides are called;
    - ``columns`` and ``column_help``: for a list of rows, each row's keys
      with their kinds, and what each holds.

    Args:
        config_cls: The dataclass.
        mode: The mode's name, carried into the result.

    Returns:
        The fields, in order, with their kinds, defaults and metadata.

    Raises:
        TypeError: ``config_cls`` is not a dataclass.
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
                side_labels=(
                    tuple(meta.get("side_labels") or SIDE_LABELS)
                    if kind == "sides"
                    else None
                ),
            )
        )
    return Schema(mode=mode, fields=fields_out)


def column_help(config_cls: type) -> dict[str, dict[str, str]]:
    """``{table field: {column: what it holds}}`` for a config's row tables,
    from each field's ``column_help`` metadata — beside its ``columns``,
    which say the columns' names and kinds."""
    if not dataclasses.is_dataclass(config_cls):
        return {}
    return {
        f.name: dict(f.metadata["column_help"])
        for f in dataclasses.fields(config_cls)
        if f.metadata.get("column_help")
    }


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
        return "sides", False, None
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
            # As a spec would hold it: the number while the sides are equal.
            return as_written(f.default)
        return f.default
    if f.default_factory is not dataclasses.MISSING:
        try:
            return f.default_factory()
        except Exception:
            return None
    return None


def _titleize(name: str) -> str:
    return name.replace("_", " ").strip().capitalize()
