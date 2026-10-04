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
from functools import partial
from typing import Any

from nicegui import ui

from ..comparison.mode_schema import SIDE_LABELS, FieldSpec, Schema
from .typed_number import TypedNumber

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
#: What the link toggle between a tolerance's two fields says, by state.
LINK_TIPS = {
    True: "Linked: both sides change together",
    False: "Unlinked: each side is set on its own",
}
_OTHER = {"lower": "upper", "upper": "lower"}


def in_effect_hint(entry: dict) -> str:
    """``in effect: 0.01 (the test's tolerance)``."""
    source = SOURCES.get(str(entry.get("source")), str(entry.get("source")))
    return f"in effect: {entry['value']:g} ({source})"


def hint_width(hint: str) -> int:
    """The px a field needs for ``hint`` on one line under it."""
    return math.ceil(len(hint) * HINT_PX_PER_CHAR) + HINT_PADDING_PX


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

    ``in_effect`` (the tree view leaf's, K3) names the value a field
    applies while it is EMPTY: its hint line says it whenever the field is
    empty — at open, and after an own value is cleared — in place of the
    dataclass default as a placeholder, which is not what applies, and
    ``values()`` never writes it. A field holding a value shows no hint.

    A number field handed what is no number — a pair on nrmse, ``"0.1"``,
    ``true``, a list: the leaf's config error, which its criteria row says —
    opens BLANK and without that hint: the hint means "nothing is set here,
    so this applies", and something is set. The value stays the form's
    until the field is edited.
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
        effect = in_effect.get(spec.name)
        hint = in_effect_hint(effect) if effect is not None else None
        if spec.type == "sides":
            # A tolerance with two sides: one group, which keeps its own
            # hint and reserves the bottom line under both its fields.
            group = sides_control(
                spec.label or spec.name,
                current,
                partial(_set, form, spec.name, on_change=on_change),
                side_labels=spec.side_labels or SIDE_LABELS,
                hint=hint,
                mark=spec.name,
            )
            for side in (group.lower, group.upper):
                side.props(BOTTOM)
                if classes:
                    side.classes(classes)
                if spec.help:
                    side.tooltip(spec.help)
            form.controls[spec.name] = group
            continue
        control = _control(
            spec,
            current,
            lambda v, name=spec.name, hint=hint: _edited(
                form, name, v, on_change, hint
            ),
        )
        if hint is not None:
            if current is None:
                control.props["hint"] = hint
            # Wide enough for it with a value too: clearing one does not
            # reflow the row.
            control.style(f"min-width: {hint_width(hint)}px")
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


def _edited(
    form: Form, name: str, value: Any, on_change: OnChange, hint: str | None
) -> None:
    """An edit of ``name``: a typed value is the one in effect now, so the
    hint naming another goes; cleared, the field says it again. The width
    the hint needed is kept: the row does not reflow as a value is typed."""
    control = form.controls.get(name)
    if hint is not None and control is not None:
        # Only when it changes, as `SidesControl._say_hint`: every write
        # of a prop is a round trip that resends the field.
        shown = None if value is not None else hint
        if control.props.get("hint") != shown:
            control.props["hint"] = shown
            control.update()
    _set(form, name, value, on_change)


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

        return TypedNumber(
            label=label, value=_shown(current), on_change=on_number, **kwargs
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


def _shown(value: object) -> Any:
    """What a number field shows for ``value``: a number, else blank — a
    field or a side the file holds as anything else is the leaf's config
    error, which its criteria row says; the editor still opens."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return None


class SidesControl(ui.row):
    """A tolerance's two sides: the ``lower`` field, the ``link`` toggle and
    the ``upper`` field, in one group. :func:`sides_control` says what it
    shows and what it hands ``on_change``."""

    def __init__(
        self,
        label: str,
        value: Any,
        on_change: Callable[[Any], None],
        *,
        side_labels: tuple[str, str],
        hint: str | None,
        mark: str | None,
        required: bool,
    ) -> None:
        # Not `wrap=False`: that is an inline style, and whether the group
        # wraps is the stylesheet's to say (it does where it has no room).
        super().__init__()
        self.classes("dstf-sides")
        pair = isinstance(value, dict)
        #: The shape the spec holds IS the link state: a number (or nothing)
        #: is linked, a pair is not — equal sides or not.
        self.linked = not pair
        self._sides: dict[str, float | None] = {
            side: _shown(value.get(side) if pair else value)
            for side in ("lower", "upper")
        }
        self._on_change, self._hint, self._syncing = on_change, hint, False
        self._required = required
        #: The spec holds something no side shows (``"0.1"``, ``true``, a
        #: list, a pair of two non-numbers): both fields are blank, and the
        #: hint is not said — it means nothing is set here. Until an edit.
        self._unusable = value is not None and not any(
            v is not None for v in self._sides.values()
        )
        with self:
            self.lower = self._field("lower", f"{side_labels[0]} {label}")
            self.link = ui.button(on_click=self.toggle).props(
                f'flat dense round size=sm aria-label="Link the two sides of {label}"'
            )
            with self.link:
                self._tip = ui.tooltip("")
            self.upper = self._field("upper", f"{side_labels[1]} {label}")
        if mark:
            self.lower.mark(f"{mark}:lower")
            self.upper.mark(f"{mark}:upper")
            self.link.mark(f"{mark}:link")
        if hint is not None:
            # Wide enough for the hint on one line, as a single field is —
            # but as a variable the stylesheet reads, not a min-width: where
            # the row is too narrow for the group that width is dropped
            # rather than push the editor wider than its card.
            self.classes("dstf-sides-hinted")
            self.style(f"--dstf-hint-width: {hint_width(hint)}px")
        self._paint()
        self._say_hint()
        self._mark_blanks()

    def half(self) -> bool:
        """Whether one side is blank and the other is not, unlinked: a pair
        with one side is no value, so nothing was handed to ``on_change``
        and what it last got is stale."""
        return self._blank("lower") or self._blank("upper")

    def toggle(self) -> None:
        """Unlink: the number becomes the pair holding it twice (nothing
        stays nothing). Link: both sides take the LARGER one, so a re-link
        never narrows what the criterion allows."""
        self.linked = not self.linked
        self._paint()
        present = [v for v in self._sides.values() if v is not None]
        if not self.linked:
            if present:  # linked, both fields held the one number
                self._on_change({"lower": present[0], "upper": present[0]})
            return
        if not present:
            return
        both = max(present)
        for side in ("lower", "upper"):
            self._sides[side] = both
            self._show(side, both)
        self._mark_blanks()
        self._on_change(both)

    def _field(self, side: str, label: str) -> TypedNumber:
        # "required" through the field's own validation: NiceGUI validates
        # a field after its change handlers and resets an `error` prop it
        # did not set itself, so a mark written as a prop never showed.
        return TypedNumber(
            label=label,
            value=self._sides[side],
            on_change=lambda e: self._edited(side, e.value),
            validation=lambda _value: "required" if self._missing(side) else None,
        ).props("dense")

    def _blank(self, side: str) -> bool:
        return (
            not self.linked
            and self._sides[side] is None
            and self._sides[_OTHER[side]] is not None
        )

    def _missing(self, side: str) -> bool:
        """Whether ``side``'s field says "required": the blank half of a
        pair, or — where the tolerance itself is required — any blank."""
        return self._blank(side) or (self._required and self._sides[side] is None)

    def _edited(self, side: str, value: Any) -> None:
        if self._syncing:  # the other field, set from here: not an edit
            return
        self._unusable = False  # what the spec holds next is this control's
        value = None if value is None else float(value)
        self._sides[side] = value
        if self.linked:
            self._sides[_OTHER[side]] = value
            self._show(_OTHER[side], value)
            self._say_hint()
            self._on_change(value)
            return
        lower, upper = self._sides["lower"], self._sides["upper"]
        if lower is None and upper is None:
            self.linked = True  # nothing is one shape, and it is linked
            self._paint()
        self._mark_blanks()
        self._say_hint()
        if lower is None and upper is None:
            self._on_change(None)
        elif lower is not None and upper is not None:
            self._on_change({"lower": lower, "upper": upper})

    def _show(self, side: str, value: float | None) -> None:
        """Set ``side``'s field without it counting as an edit: a value set
        from the server fires the field's own change handler, and unguarded
        every keystroke reached ``on_change`` twice."""
        self._syncing = True
        try:
            (self.lower if side == "lower" else self.upper).value = value
        finally:
            self._syncing = False

    def _paint(self) -> None:
        self.link.icon = "link" if self.linked else "link_off"
        self.link.props(f"aria-pressed={'true' if self.linked else 'false'}")
        self._tip.text = LINK_TIPS[self.linked]

    def _say_hint(self) -> None:
        """The value in effect, under the first field while BOTH are empty:
        with a side typed it named a value no longer applied. Nor while the
        spec holds a value the fields cannot show: that one is set."""
        if self._hint is None:
            return
        empty = self._sides["lower"] is None and self._sides["upper"] is None
        hint = self._hint if empty and not self._unusable else None
        if self.lower.props.get("hint") != hint:
            self.lower.props["hint"] = hint
            self.lower.update()

    def _mark_blanks(self) -> None:
        for side in (self.lower, self.upper):
            side.validate(return_result=False)


def sides_control(
    label: str,
    value: Any,
    on_change: Callable[[Any], None],
    *,
    side_labels: tuple[str, str],
    hint: str | None = None,
    mark: str | None = None,
    required: bool = False,
) -> SidesControl:
    """Two number fields and a link toggle for a tolerance that is a number
    or a ``{"lower", "upper"}`` pair — the leaf form's control and a table
    row's. The fields read ``{side_labels[0]} {label}`` and
    ``{side_labels[1]} {label}``; ``mark`` marks them ``{mark}:lower`` and
    ``{mark}:upper`` and the toggle ``{mark}:link``.

    ``value`` is what the spec holds, and its shape is the link state:
    ``None`` is unset (both fields empty, linked, ``hint`` — the value in
    effect — under the first one while both are empty), a number is linked
    (both fields show it), a pair is unlinked. ``on_change`` gets what the
    spec should hold next:

    - linked, typing in either field sets both and hands over the number;
      clearing either clears both and hands over ``None``;
    - unlinked, typing hands over the pair; while ONE side is blank nothing
      is handed over and the blank field is marked "required"
      (:meth:`SidesControl.half` says so to a caller holding a stale
      value); both blank is ``None``, and the control is linked again;
    - the toggle: unlinking hands over the pair holding the number twice
      (unset stays unset, and unlinked); linking hands over the larger side
      — of those present — for both.

    ``required`` (a table's required column) marks a blank field "required"
    while the whole tolerance is unset too; ``None`` is still handed over,
    and holding it back is the caller's. The fields reserve no bottom line
    of their own: a form's row does (`render_form`), a table's does not.
    """
    return SidesControl(
        label,
        value,
        on_change,
        side_labels=side_labels,
        hint=hint,
        mark=mark,
        required=required,
    )
