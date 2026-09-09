"""NiceGUI controls from a mode's Config dataclass (§4.7 "Edit criteria").

``derive_schema`` describes the dataclass — field name, type, default,
choices, label, help, bounds; this module turns each ``FieldSpec`` into one
control and hands the caller the live values. The NiceGUI emitter is the
sibling of ``reporting/schema_export``'s JSON-Schema emitter: same input,
different surface.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from nicegui import ui

from ..comparison.mode_schema import FieldSpec, Schema

OnChange = Callable[[dict], None]


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


def render_form(schema: Schema, values: dict, on_change: OnChange) -> Form:
    """Build one control per field into the current NiceGUI container.

    ``values`` pre-fills (missing keys fall back to the field's default);
    ``on_change`` receives the full values dict after every edit.
    """
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
                .tooltip(spec.help or "edited on the plot, not here")
            )
            continue
        if spec.name in values:
            form._values[spec.name] = current
        control = _control(
            spec, current, lambda v, name=spec.name: _set(form, name, v, on_change)
        )
        if current is None and spec.default is not None:
            control.props(f'placeholder="{spec.default}"')
        if spec.help:
            control.tooltip(spec.help)
        form.controls[spec.name] = control
    return form


def _set(form: Form, name: str, value: Any, on_change: OnChange) -> None:
    form._values[name] = value
    on_change(form.values())


def _control(spec: FieldSpec, current: Any, set_value: Callable) -> ui.element:
    label = spec.label or spec.name
    if spec.type in ("float", "int"):
        # ui_min / ui_max are the schema's SOFT bounds (a hint for a control,
        # never a clamp — mode_schema.py); NiceGUI's own `min`/`max` props
        # clamp on blur and fire on_change with the clamped value, which
        # would stage a rewrite the user never typed. Validate instead.
        validation: dict[str, Callable[[Any], bool]] = {}
        if spec.ui_min is not None:
            lo = spec.ui_min
            validation[f"below {lo}"] = lambda v, lo=lo: v is None or v >= lo
        if spec.ui_max is not None:
            hi = spec.ui_max
            validation[f"above {hi}"] = lambda v, hi=hi: v is None or v <= hi
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
