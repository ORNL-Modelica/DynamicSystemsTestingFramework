"""NiceGUI controls from a mode's Config dataclass, via derive_schema."""

import dataclasses
from typing import Literal

import pytest

pytest.importorskip("nicegui")
from nicegui.testing.user_simulation import user_simulation  # noqa: E402

from dstf.authoring.forms import render_form  # noqa: E402
from dstf.comparison.mode_schema import derive_schema  # noqa: E402


@dataclasses.dataclass
class _Cfg:
    tolerance: float = dataclasses.field(
        default=1e-4, metadata={"label": "Tol", "ui_min": 0.0}
    )
    count_must_match: bool = True
    mode_kind: Literal["band", "rel"] = "band"
    note: str = ""
    max_value: float | None = None
    points: list | None = None  # passthrough


async def test_every_field_type_gets_a_control_and_values_round_trip():
    seen = []
    forms = {}

    def root():
        forms["f"] = render_form(
            derive_schema(_Cfg, mode="x"),
            {"tolerance": 0.5, "points": [[0, 1]]},
            on_change=lambda values: seen.append(dict(values)),
        )

    async with user_simulation(root=root) as user:
        await user.open("/")
        await user.should_see("Tol")
        form = forms["f"]
        # Only what was given is a value; a default is a placeholder, never
        # written for the user (a leaf without `tolerance` scores at the
        # test's base tolerance, and the form must not silently pin 1e-4).
        assert form.values() == {"tolerance": 0.5, "points": [[0, 1]]}
        # ui_min is a SOFT bound: a validation rule, never a clamping prop
        # (NiceGUI's `min` clamps on blur and fires on_change).
        rules = form.controls["tolerance"].validation
        assert rules and all(r(0.5) for r in rules.values())
        assert not all(r(-1.0) for r in rules.values())
        assert form.controls["tolerance"].props.get("min") is None
        form.controls["max_value"].set_value(2.0)
        assert seen[-1]["max_value"] == 2.0 and "count_must_match" not in seen[-1]
        form.controls["mode_kind"].set_value("rel")
        assert seen[-1]["mode_kind"] == "rel"
        # A passthrough field is shown read-only as JSON, never edited here.
        assert form.controls["points"].props.get("readonly") is not None
        assert "points" not in seen[-1] or seen[-1]["points"] == [[0, 1]]


async def test_none_values_are_omitted_from_the_values_dict():
    forms = {}

    def root():
        forms["f"] = render_form(derive_schema(_Cfg), {}, on_change=lambda v: None)

    async with user_simulation(root=root) as user:
        await user.open("/")
        assert (
            "max_value" not in forms["f"].values()
            and "points" not in forms["f"].values()
        )
