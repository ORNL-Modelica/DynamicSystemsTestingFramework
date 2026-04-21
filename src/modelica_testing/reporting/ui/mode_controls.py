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
class PlotContribution:
    """Declarative description of a leaf's contribution to its variable plot.

    Server-side stub — the Stage-2 JS-side ``MODE_PLOT_CONTRIBUTIONS``
    registry is what actually mutates Plotly. This dataclass exists so
    that every mode's contract is represented in the Python registry too,
    which is the source of truth for "what does this mode contribute,
    visually?" for tooling like the recommender (Phase 7) and the
    JSON-Schema export.

    Fields are intentionally Plotly-shaped — `shapes` and `annotations`
    map 1:1 to ``layout.shapes`` / ``layout.annotations``; ``traces`` is
    a list of trace specs added to the plot. ``secondary_panel`` requests
    a sibling panel (e.g., dominant-frequency's spectrum view) keyed by
    panel name; the template allocates one ``<div>`` per requested panel.
    """

    traces: list[dict] = field(default_factory=list)
    shapes: list[dict] = field(default_factory=list)
    annotations: list[dict] = field(default_factory=list)
    secondary_panel: Optional[str] = None  # e.g. "spectrum" for dominant-frequency


#: A mode's plot-contribution function takes the leaf's config values dict
#: and returns a :class:`PlotContribution`. Static contributions only here
#: — interactive editing (tube shaping) lives JS-side. Default ``None``
#: means "no intrinsic plot artifact" (NRMSE).
PlotContributionFn = Callable[[dict[str, Any]], PlotContribution]


@dataclass
class ModeUI:
    name: str
    config_cls: type
    schema: Schema
    custom_renderer: Optional[CustomRenderer] = None
    plot_contribution: Optional[PlotContributionFn] = None
    # Marker that the mode has a JS-side interactive plot editor
    # registered under ``MODE_PLOT_EDITORS[name]``. Python-side is just
    # the declaration; the editor itself lives in the template JS
    # (Shift+click handlers, drag, control-point markers). Having the
    # slot here keeps "what does this mode support" discoverable from
    # the single Python registry (recommender, schema export, etc.).
    has_plot_editor: bool = False

    def render(self, *, variable: Optional[str] = None,
               values: Optional[dict[str, Any]] = None) -> str:
        if self.custom_renderer is not None:
            return self.custom_renderer(
                self.schema, mode=self.name, variable=variable, values=values,
            )
        return render_schema_html(
            self.schema, mode=self.name, variable=variable, values=values,
        )

    def contribute_to_plot(self, values: dict[str, Any]) -> Optional[PlotContribution]:
        """Return the leaf's static plot contribution, or ``None``.

        Stage-1 stub — Stage 2 implements per-mode contributions
        (tube polygon, range dashed lines, final-only marker, etc.).
        """
        if self.plot_contribution is None:
            return None
        return self.plot_contribution(values)


_REGISTRY: dict[str, ModeUI] = {}


def register_mode_ui(
    name: str,
    config_cls: type,
    *,
    custom_renderer: Optional[CustomRenderer] = None,
    plot_contribution: Optional[PlotContributionFn] = None,
    has_plot_editor: bool = False,
) -> ModeUI:
    """Register a mode's UI. Returns the :class:`ModeUI` for test/inspection."""
    schema = derive_schema(config_cls, mode=name)
    entry = ModeUI(
        name=name,
        config_cls=config_cls,
        schema=schema,
        custom_renderer=custom_renderer,
        plot_contribution=plot_contribution,
        has_plot_editor=has_plot_editor,
    )
    _REGISTRY[name] = entry
    return entry


def get_mode_ui(name: str) -> Optional[ModeUI]:
    """Return the registered ModeUI for ``name`` or None if not registered."""
    return _REGISTRY.get(name)


def registered_modes() -> list[str]:
    return list(_REGISTRY.keys())


def emit_mode_schemas() -> dict[str, dict]:
    """Return ``{mode_name: schema_dict}`` for every registered mode.

    The Stage-2 JS-side recursive UI renders leaf controls from these
    schemas rather than from pre-rendered HTML — that way newly-added
    leaves (via the in-browser +/- editor) can build their own controls
    without a server round-trip. Python stays the single source of truth
    for field types / defaults / Literal choices.
    """
    return {name: ui.schema.to_dict() for name, ui in _REGISTRY.items()}


# ---------------------------------------------------------------------------
# Window controls — universal cross-mode suffix (idea #46 UI surfacing)
# ---------------------------------------------------------------------------

def render_window_controls_html(
    variable: Optional[str] = None,
    values: Optional[dict[str, Any]] = None,
) -> str:
    """Render the universal window inputs for a tree-backed leaf.

    ``window`` lives on :class:`LeafSpec` (not on any ``ModeConfig``), so
    it's rendered as a separate fragment rather than threading synthetic
    fields through the mode schema. Returns a ``<div class="window-controls"
    data-variable="...">`` with two number inputs, ``data-field="window_start"``
    and ``data-field="window_end"``. Empty ``values`` leaves the inputs
    blank (open-ended window).

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
    return (
        f'<div class="window-controls" data-variable="{var_attr}" '
        'title="Restrict this leaf to a time window [start, end] before scoring">'
        '<span class="wc-label">Window:</span>'
        f'<label class="wc-field"><span>start</span>'
        f'<input type="number" step="any" data-field="window_start"{start_attr}>'
        '</label>'
        f'<label class="wc-field"><span>end</span>'
        f'<input type="number" step="any" data-field="window_end"{end_attr}>'
        '</label>'
        '</div>'
    )


# ---------------------------------------------------------------------------
# Bundled registrations — six comparison modes
# ---------------------------------------------------------------------------

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
    # Tube has a JS-side interactive plot editor (MODE_PLOT_EDITORS.tube)
    # for shaping time-varying control points. The schema-driven inputs
    # (tube_width_mode, tube_rel, tube_abs, tube_min_width,
    # tube_interpolation, passthrough tube_points) remain; the editor
    # sits on top of them, activated by clicking the leaf node.
    register_mode_ui("tube", TubeConfig, has_plot_editor=True)
    register_mode_ui("final_only", FinalOnlyConfig)
    # Range has a JS-side interactive plot editor — drag the dashed
    # min/max reference lines directly on the plot. Scalar inputs in
    # the leaf's controls also edit the same state (bidirectional sync).
    register_mode_ui("range", RangeConfig, has_plot_editor=True)
    register_mode_ui("event-timing", EventTimingConfig)
    register_mode_ui("dominant-frequency", DominantFrequencyConfig)


_register_bundled()
