"""Auto-derive per-mode UI controls from typed Config dataclasses (Phase 6.1.1).

Each :class:`ComparisonMode`'s Config is a frozen dataclass with strictly
typed fields. This module reads those types via :func:`typing.get_type_hints`
and produces:

1. A **schema** (dict) — introspected field list with types, defaults, and
   ``Literal`` choices — useful as a data artifact (JSON-Schema export in
   6.4.5 feeds off the same machinery).
2. **HTML** — a vanilla-form fragment that slots into interactive.html,
   replacing today's ``n/a (mode=…)`` cells (6.1.5).

Per D66 the :class:`ComparisonMode` ABC stays pure compute — no UI coupling
on the mode class. The bridge lives here instead, keyed on mode name.
"""
from __future__ import annotations

import dataclasses
import html
import typing
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Union


# ---------------------------------------------------------------------------
# Schema types
# ---------------------------------------------------------------------------

#: Recognized field types. ``passthrough`` is the fallback for complex types
#: (nested dataclasses, list[dict], etc.) that auto-derive can't render —
#: the fragment emits a raw-JSON textarea as escape hatch so the form
#: doesn't crash.
FIELD_TYPES = {"float", "int", "bool", "str", "enum", "passthrough"}


@dataclass
class FieldSpec:
    name: str
    type: str  # one of FIELD_TYPES
    default: Any = None
    optional: bool = False
    choices: Optional[list[str]] = None
    label: Optional[str] = None
    help: Optional[str] = None


@dataclass
class Schema:
    mode: Optional[str] = None
    fields: list[FieldSpec] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "fields": [dataclasses.asdict(f) for f in self.fields],
        }


# ---------------------------------------------------------------------------
# Introspection
# ---------------------------------------------------------------------------

def derive_schema(config_cls: type, *, mode: Optional[str] = None) -> Schema:
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
        meta = f.metadata or {}
        fields_out.append(FieldSpec(
            name=f.name,
            type=kind,
            default=default,
            optional=optional,
            choices=choices,
            label=meta.get("label") or _titleize(f.name),
            help=meta.get("help"),
        ))
    return Schema(mode=mode, fields=fields_out)


def _classify_type(hint: Any) -> tuple[str, bool, Optional[list[str]]]:
    """Return (field_kind, optional, choices-if-enum) for a type hint."""
    origin = typing.get_origin(hint)
    args = typing.get_args(hint)

    # Optional[X] == Union[X, None] — unwrap, mark optional, recurse.
    if origin is Union and type(None) in args:
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
        return f.default
    if f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
        try:
            return f.default_factory()  # type: ignore[misc]
        except Exception:
            return None
    return None


def _titleize(name: str) -> str:
    return name.replace("_", " ").strip().capitalize()


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

def render_schema_html(
    schema: Schema,
    *,
    mode: Optional[str] = None,
    variable: Optional[str] = None,
    values: Optional[dict[str, Any]] = None,
) -> str:
    """Render a :class:`Schema` to an HTML form fragment.

    Produces a ``<div class="mode-controls" data-mode="..." data-variable="...">``
    containing labeled inputs for each field. Pure string builder — no
    framework, no JS dependency. Values (if supplied) pre-fill the fields;
    otherwise defaults are used.
    """
    mode_attr = html.escape(mode or schema.mode or "")
    var_attr = html.escape(variable or "")
    values = values or {}

    rows: list[str] = []
    for f in schema.fields:
        current = values.get(f.name, f.default)
        rows.append(_render_field(f, current))

    return (
        f'<div class="mode-controls" data-mode="{mode_attr}" '
        f'data-variable="{var_attr}">'
        + "".join(rows)
        + "</div>"
    )


def _render_field(f: FieldSpec, value: Any) -> str:
    name_attr = html.escape(f.name)
    label = html.escape(f.label or f.name)
    help_attr = f' title="{html.escape(f.help)}"' if f.help else ""

    if f.type == "enum":
        options = []
        for choice in f.choices or []:
            selected = " selected" if str(value) == choice else ""
            options.append(
                f'<option value="{html.escape(choice)}"{selected}>'
                f'{html.escape(choice)}</option>'
            )
        # Optional enums get a leading blank so the user can unset.
        if f.optional:
            blank_selected = " selected" if value is None else ""
            options.insert(0, f'<option value=""{blank_selected}>(unset)</option>')
        return (
            f'<label class="mc-field mc-enum"{help_attr}>'
            f'<span>{label}</span>'
            f'<select data-field="{name_attr}">'
            + "".join(options)
            + "</select></label>"
        )

    if f.type == "bool":
        checked = " checked" if bool(value) else ""
        return (
            f'<label class="mc-field mc-bool"{help_attr}>'
            f'<input type="checkbox" data-field="{name_attr}"{checked}>'
            f'<span>{label}</span></label>'
        )

    if f.type in ("float", "int"):
        step = 'any' if f.type == "float" else '1'
        val_attr = "" if value is None else f' value="{html.escape(str(value))}"'
        return (
            f'<label class="mc-field mc-{f.type}"{help_attr}>'
            f'<span>{label}</span>'
            f'<input type="number" step="{step}" data-field="{name_attr}"{val_attr}>'
            "</label>"
        )

    if f.type == "str":
        val_attr = "" if value is None else f' value="{html.escape(str(value))}"'
        return (
            f'<label class="mc-field mc-str"{help_attr}>'
            f'<span>{label}</span>'
            f'<input type="text" data-field="{name_attr}"{val_attr}>'
            "</label>"
        )

    # Passthrough: complex type — raw JSON textarea. Value serialized best-
    # effort; edits are not introspected, just round-tripped.
    import json as _json
    try:
        raw = _json.dumps(value, default=str) if value is not None else ""
    except Exception:
        raw = ""
    return (
        f'<label class="mc-field mc-passthrough"{help_attr}>'
        f'<span>{label}</span>'
        f'<textarea data-field="{name_attr}" '
        'data-passthrough="true" rows="2">'
        f'{html.escape(raw)}</textarea></label>'
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

#: Optional override callable: takes (schema, *, mode, variable, values)
#: and returns an HTML fragment. Registered per mode for cases where the
#: auto-derived form is insufficient (tube with conditional rel/abs/min
#: fields; range with visual handles on the plot — 6.1.4 territory).
CustomRenderer = Callable[..., str]


@dataclass
class ModeUI:
    name: str
    config_cls: type
    schema: Schema
    custom_renderer: Optional[CustomRenderer] = None

    def render(self, *, variable: Optional[str] = None,
               values: Optional[dict[str, Any]] = None) -> str:
        if self.custom_renderer is not None:
            return self.custom_renderer(
                self.schema, mode=self.name, variable=variable, values=values,
            )
        return render_schema_html(
            self.schema, mode=self.name, variable=variable, values=values,
        )


_REGISTRY: dict[str, ModeUI] = {}


def register_mode_ui(
    name: str,
    config_cls: type,
    *,
    custom_renderer: Optional[CustomRenderer] = None,
) -> ModeUI:
    """Register a mode's UI. Returns the :class:`ModeUI` for test/inspection."""
    schema = derive_schema(config_cls, mode=name)
    entry = ModeUI(
        name=name,
        config_cls=config_cls,
        schema=schema,
        custom_renderer=custom_renderer,
    )
    _REGISTRY[name] = entry
    return entry


def get_mode_ui(name: str) -> Optional[ModeUI]:
    """Return the registered ModeUI for ``name`` or None if not registered."""
    return _REGISTRY.get(name)


def registered_modes() -> list[str]:
    return list(_REGISTRY.keys())


# ---------------------------------------------------------------------------
# Bundled registrations — six comparison modes
# ---------------------------------------------------------------------------

def _tube_cell_renderer(schema, *, mode, variable, values):
    """6.1.4 — tube mode has a dedicated rich editor below the plot
    (template lines ~260+). The variable-table cell just points at it."""
    var_attr = html.escape(variable or "")
    mode_attr = html.escape(mode or "")
    return (
        f'<div class="mode-controls tube-cell" data-mode="{mode_attr}" '
        f'data-variable="{var_attr}">'
        '<span class="hint">→ See tube editor below plot</span>'
        '</div>'
    )


def _register_bundled() -> None:
    """Register the six built-in modes' UI at import time."""
    from ...comparison.modes import (
        DominantFrequencyConfig,
        EventTimingConfig,
        FinalOnlyConfig,
        NrmseConfig,
        RangeConfig,
        TubeConfig,
    )

    register_mode_ui("nrmse", NrmseConfig)
    # 6.1.4 — tube has a rich dedicated editor; cell defers to it.
    register_mode_ui("tube", TubeConfig, custom_renderer=_tube_cell_renderer)
    register_mode_ui("final_only", FinalOnlyConfig)
    # range auto-derived panel is the 6.1.5 default; 6.1.4 adds visual
    # reference lines on the trajectory plot via JS (no custom_renderer
    # needed — the inputs stay in the cell, the plot overlay is additive).
    register_mode_ui("range", RangeConfig)
    register_mode_ui("event-timing", EventTimingConfig)
    register_mode_ui("dominant-frequency", DominantFrequencyConfig)


_register_bundled()
