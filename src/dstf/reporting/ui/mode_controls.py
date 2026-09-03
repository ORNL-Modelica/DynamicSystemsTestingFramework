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
import types
import typing
from dataclasses import dataclass, field
from typing import Any, Union

from ...comparison.registry import metric, metrics

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
    choices: list[str] | None = None
    label: str | None = None
    help: str | None = None
    # UI hints for number inputs. ``ui_max`` is a soft cap — if the
    # current value exceeds it, the rendered input's max becomes
    # ``max(ui_max, current_value)`` so edits aren't clamped down.
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
        meta = f.metadata or {}
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
    mode: str | None = None,
    variable: str | None = None,
    values: dict[str, Any] | None = None,
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
        f'data-variable="{var_attr}">' + "".join(rows) + "</div>"
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
                f"{html.escape(choice)}</option>"
            )
        # Optional enums get a leading blank so the user can unset.
        if f.optional:
            blank_selected = " selected" if value is None else ""
            options.insert(0, f'<option value=""{blank_selected}>(unset)</option>')
        return (
            f'<label class="mc-field mc-enum"{help_attr}>'
            f"<span>{label}</span>"
            f'<select data-field="{name_attr}">'
            + "".join(options)
            + "</select></label>"
        )

    if f.type == "bool":
        checked = " checked" if bool(value) else ""
        return (
            f'<label class="mc-field mc-bool"{help_attr}>'
            f'<input type="checkbox" data-field="{name_attr}"{checked}>'
            f"<span>{label}</span></label>"
        )

    if f.type in ("float", "int"):
        step = "any" if f.type == "float" else "1"
        val_attr = "" if value is None else f' value="{html.escape(str(value))}"'
        min_attr = ""
        max_attr = ""
        if f.ui_min is not None:
            min_attr = f' min="{html.escape(str(f.ui_min))}"'
        if f.ui_max is not None:
            # Soft cap: if the current value exceeds ui_max, bump the input's
            # max so the browser's spinner doesn't clamp it down on edit.
            try:
                cur = float(value) if value is not None else float("-inf")
            except (TypeError, ValueError):
                cur = float("-inf")
            effective_max = max(float(f.ui_max), cur)
            # Keep integer inputs integer-typed in the max attribute.
            if f.type == "int":
                max_attr = f' max="{int(effective_max)}"'
            else:
                max_attr = f' max="{html.escape(str(effective_max))}"'
        return (
            f'<label class="mc-field mc-{f.type}"{help_attr}>'
            f"<span>{label}</span>"
            f'<input type="number" step="{step}" data-field="{name_attr}"'
            f"{min_attr}{max_attr}{val_attr}>"
            "</label>"
        )

    if f.type == "str":
        val_attr = "" if value is None else f' value="{html.escape(str(value))}"'
        return (
            f'<label class="mc-field mc-str"{help_attr}>'
            f"<span>{label}</span>"
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
        f"<span>{label}</span>"
        f'<textarea data-field="{name_attr}" '
        'data-passthrough="true" rows="1">'
        f"{html.escape(raw)}</textarea></label>"
    )


# ---------------------------------------------------------------------------
# Mode UI lookup
# ---------------------------------------------------------------------------


@dataclass
class ModeUI:
    name: str
    config_cls: type
    schema: Schema

    def render(
        self, *, variable: str | None = None, values: dict[str, Any] | None = None
    ) -> str:
        return render_schema_html(
            self.schema,
            mode=self.name,
            variable=variable,
            values=values,
        )


#: Memoized auto-derivations, keyed by metric name. Invalidated by
#: comparing the cached ``config_cls`` against the registry's — so a
#: registry reset or re-registration under the same name re-derives.
_DERIVED: dict[str, ModeUI] = {}


def get_mode_ui(name: str) -> ModeUI | None:
    """Return the ModeUI for ``name``, or None if there is no such mode.

    Derived from the metric registry's ``config_cls`` — registering a
    metric is the only way to get controls, so the UI mode set and the
    metric set cannot drift apart.
    """
    return _derived_mode_ui(name)


def _derived_mode_ui(name: str) -> ModeUI | None:
    desc = metric(name)
    if desc is None:
        return None
    cached = _DERIVED.get(name)
    if cached is not None and cached.config_cls is desc.config_cls:
        return cached
    entry = ModeUI(
        name=name,
        config_cls=desc.config_cls,
        schema=derive_schema(desc.config_cls, mode=name),
    )
    _DERIVED[name] = entry
    return entry


def registered_modes() -> list[str]:
    """Mode names with a UI, in metric-registration order.

    Exactly the metric registry's names — the add-leaf dropdown,
    :func:`emit_mode_schemas`, the schema export and ``tree_spec``
    parsing all read this one list.
    """
    return [desc.name for desc in metrics()]


def emit_mode_schemas() -> dict[str, dict]:
    """Return ``{mode_name: schema_dict}`` for every mode with a UI.

    The Stage-2 JS-side recursive UI renders leaf controls from these
    schemas rather than from pre-rendered HTML — that way newly-added
    leaves (via the in-browser +/- editor) can build their own controls
    without a server round-trip. Python stays the single source of truth
    for field types / defaults / Literal choices.
    """
    out: dict[str, dict] = {}
    for name in registered_modes():
        ui = get_mode_ui(name)
        if ui is not None:
            out[name] = ui.schema.to_dict()
    return out


# ---------------------------------------------------------------------------
# Window controls — universal cross-mode suffix (idea #46 UI surfacing)
# ---------------------------------------------------------------------------


def render_window_controls_html(
    variable: str | None = None,
    values: dict[str, Any] | None = None,
    *,
    time_start: float | None = None,
    time_end: float | None = None,
) -> str:
    """Render the universal window inputs for a tree-backed leaf.

    ``window`` lives on :class:`LeafSpec` (not on any ``ModeConfig``), so
    it's rendered as a separate fragment rather than threading synthetic
    fields through the mode schema. Returns a ``<div class="window-controls"
    data-variable="...">`` with two number inputs, ``data-field="window_start"``
    and ``data-field="window_end"``. Empty ``values`` leaves the inputs
    blank (open-ended window); ``time_start`` / ``time_end``, when given,
    hint the simulation's full range via ``placeholder`` so users see the
    bounds without auto-committing them.

    Emitted only for tree-backed variables (callers decide) — flat-override
    leaves have no window concept, so the reporter suppresses this fragment
    there rather than render a dead UI.
    """
    values = values or {}
    var_attr = html.escape(variable or "")
    start = values.get("start")
    end = values.get("end")
    start_attr = "" if start is None else f' value="{html.escape(str(start))}"'
    end_attr = "" if end is None else f' value="{html.escape(str(end))}"'
    start_ph = (
        f' placeholder="{html.escape(f"{time_start:g}")}"'
        if time_start is not None
        else ""
    )
    end_ph = (
        f' placeholder="{html.escape(f"{time_end:g}")}"' if time_end is not None else ""
    )
    return (
        f'<div class="window-controls" data-variable="{var_attr}" '
        'title="Restrict this leaf to a time window [start, end] before scoring">'
        '<span class="wc-label">Window:</span>'
        f'<label class="wc-field"><span>start</span>'
        f'<input type="number" step="any" data-field="window_start"{start_attr}{start_ph}>'
        "</label>"
        f'<label class="wc-field"><span>end</span>'
        f'<input type="number" step="any" data-field="window_end"{end_attr}{end_ph}>'
        "</label>"
        "</div>"
    )
