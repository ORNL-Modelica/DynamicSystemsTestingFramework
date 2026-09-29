"""NiceGUI controls from a mode's Config dataclass (§4.7 "Edit criteria").

``derive_schema`` describes the dataclass — field name, type, default,
choices, label, help, bounds; this module turns each ``FieldSpec`` into one
control and hands the caller the live values. The NiceGUI emitter is the
sibling of ``reporting/schema_export``'s JSON-Schema emitter: same input,
different surface.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from nicegui import ui

from ..comparison.mode_schema import FieldSpec, Schema

OnChange = Callable[[dict], None]

#: What a source of a value in effect reads as (K3); the keys are
#: `TestComparison.tolerance_source`'s.
SOURCES = {
    "test": "the test's tolerance",
    "reference": "the baseline's tolerance",
    "project": "the project's tolerance",
}
#: The one bottom-space rule of an editor row (K4): every one-line field
#: reserves Quasar's bottom line, whether it holds a hint, a validation
#: message or nothing, so the input lines of a row share one y. Without it
#: a number with a validation rule reserved the line and nothing else did,
#: and its input line sat 20 px above the row's.
BOTTOM = "bottom-slots"
#: A hint is on ONE line (K8): Quasar's bottom line is absolutely placed,
#: so its text never widens the field, and at the row's 140 px the hint
#: "in effect: 0.01 (the test's tolerance)" wrapped to three lines under
#: its field. A field with a hint is at least as wide as the hint: px per
#: character of Quasar's 11 px hint font, measured in Chromium (185 px for
#: that 38-character hint, 252 px for 50: 4.9-5.0 px, rounded up for a
#: wider fallback font), plus the bottom line's 12 px padding each side.
HINT_PX_PER_CHAR, HINT_PADDING_PX = 5.4, 24


def in_effect_hint(entry: dict) -> str:
    """``in effect: 0.01 (the test's tolerance)``."""
    source = SOURCES.get(str(entry.get("source")), str(entry.get("source")))
    return f"in effect: {entry['value']:g} ({source})"


@dataclass
class Form:
    """The rendered controls, by field name, and their current values."""

    controls: dict[str, ui.element] = field(default_factory=dict)
    _values: dict = field(default_factory=dict)
    _passthrough: dict = field(default_factory=dict)

    def values(self) -> dict:
        """Every field the caller gave a value or the user edited — ``None``
        is "unset" and is omitted; a dataclass default is shown as the
        control's placeholder and never written (the evaluator falls back
        to the test's base tolerance for a leaf without one, and a written
        1e-4 would change that verdict). Passthrough fields keep the value
        they were given."""
        out = {k: v for k, v in self._values.items() if v is not None}
        out.update({k: v for k, v in self._passthrough.items() if v is not None})
        return out


def render_form(
    schema: Schema,
    values: dict,
    on_change: OnChange,
    *,
    classes: str = "",
    in_effect: dict | None = None,
) -> Form:
    """Build one control per field into the current NiceGUI container.

    ``values`` pre-fills (missing keys fall back to the field's default);
    ``on_change`` receives the full values dict after every edit.
    ``classes`` is added to every control, for a caller laying the fields
    out itself — the criterion editor puts them in one wrapping row, where
    each control needs a width of its own. A multi-line control (a list
    shown as JSON) takes the whole line instead, whatever ``classes`` says.

    ``in_effect`` (the tree view leaf's, K3) names the value the evaluation
    applied to a field the leaf does not set: that field's hint line says
    it, in place of the dataclass default as a placeholder — which is not
    what was applied — and ``values()`` never writes it. A field with a
    value of its own shows no hint.
    """
    in_effect = in_effect or {}
    form = Form()
    for spec in schema.fields:
        current = values.get(spec.name)  # the default is a placeholder, not a value
        if spec.type == "passthrough":
            # Structured data (tube control points, declared peaks,
            # checkpoints): shown as JSON, read-only — on-plot editing (§5
            # step 6) is where these get their controls.
            form._passthrough[spec.name] = current
            form.controls[spec.name] = (
                ui.textarea(
                    label=spec.label or spec.name,
                    value=json.dumps(current) if current is not None else "",
                )
                .props("readonly outlined dense")
                .classes("dstf-field-wide")
                .tooltip(spec.help or "edited on the plot, not here")
            )
            continue
        if spec.name in values:
            form._values[spec.name] = current
        control = _control(
            spec, current, lambda v, name=spec.name: _set(form, name, v, on_change)
        )
        effect = in_effect.get(spec.name) if current is None else None
        if effect is not None:
            hint = in_effect_hint(effect)
            control.props["hint"] = hint
            control.style(
                f"min-width: {math.ceil(len(hint) * HINT_PX_PER_CHAR) + HINT_PADDING_PX}px"
            )
        elif current is None and spec.default is not None:
            control.props(f'placeholder="{spec.default}"')
        if spec.type != "bool":
            control.props(BOTTOM)
        if classes:
            control.classes(classes)
        if spec.help:
            control.tooltip(spec.help)
        form.controls[spec.name] = control
    return form


def _set(form: Form, name: str, value: Any, on_change: OnChange) -> None:
    form._values[name] = value
    on_change(form.values())


def _at_least(lo: float) -> Callable[[Any], bool]:
    return lambda v: v is None or v >= lo


def _at_most(hi: float) -> Callable[[Any], bool]:
    return lambda v: v is None or v <= hi


def _control(spec: FieldSpec, current: Any, set_value: Callable) -> ui.element:
    label = spec.label or spec.name
    if spec.type in ("float", "int"):
        # ui_min / ui_max are the schema's SOFT bounds (a hint for a control,
        # never a clamp — mode_schema.py); NiceGUI's own `min`/`max` props
        # clamp on blur and fire on_change with the clamped value, which
        # would stage a rewrite the user never typed. Validate instead.
        validation: dict[str, Callable[[Any], bool]] = {}
        if spec.ui_min is not None:
            validation[f"below {spec.ui_min}"] = _at_least(spec.ui_min)
        if spec.ui_max is not None:
            validation[f"above {spec.ui_max}"] = _at_most(spec.ui_max)
        kwargs: dict[str, Any] = {"validation": validation}
        if spec.type == "int":
            kwargs["step"] = 1
            kwargs["precision"] = 0
        cast = int if spec.type == "int" else float

        def on_number(e, cast=cast):
            set_value(None if e.value is None else cast(e.value))

        return ui.number(
            label=label, value=current, on_change=on_number, **kwargs
        ).props("dense")
    if spec.type == "bool":
        # A checkbox has no placeholder: show the EFFECTIVE state (the
        # default when unset) and write only when the user changes it.
        shown = bool(spec.default) if current is None else bool(current)
        return ui.checkbox(label, value=shown, on_change=lambda e: set_value(e.value))
    if spec.type == "enum":
        # Same for a select: the default is what the leaf scores with.
        return ui.select(
            list(spec.choices or []),
            label=label,
            value=spec.default if current is None else current,
            on_change=lambda e: set_value(e.value),
        ).props("dense")
    return ui.input(
        label=label,
        value="" if current is None else str(current),
        on_change=lambda e: set_value(e.value),
    ).props("dense")
