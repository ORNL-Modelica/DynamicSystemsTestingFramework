"""NiceGUI controls from a mode's Config dataclass, via derive_schema."""

import ast
import dataclasses
from pathlib import Path
from typing import Literal

import pytest

pytest.importorskip("nicegui")
from nicegui import ui  # noqa: E402
from nicegui.testing.user_simulation import user_simulation  # noqa: E402

import dstf.authoring  # noqa: E402
from dstf.authoring.forms import SidesControl, render_form  # noqa: E402
from dstf.authoring.typed_number import TypedNumber  # noqa: E402
from dstf.comparison.mode_schema import derive_schema  # noqa: E402
from dstf.comparison.sides import Sides  # noqa: E402


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


async def test_a_checkbox_and_a_select_show_the_effective_default_unwritten():
    """A checkbox has no placeholder: an unset bool shows its default (what
    the leaf scores with), and the value is not written until changed.
    Same for an enum."""
    forms = {}

    def root():
        forms["f"] = render_form(derive_schema(_Cfg), {}, on_change=lambda v: None)

    async with user_simulation(root=root) as user:
        await user.open("/")
        form = forms["f"]
        assert form.controls["count_must_match"].value is True
        assert form.controls["mode_kind"].value == "band"
        assert form.values() == {}
        form.controls["count_must_match"].set_value(False)
        assert form.values() == {"count_must_match": False}


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


# -- values in effect; one bottom line (U2, K3 + K4) -----------------------------

IN_EFFECT = {"tolerance": {"value": 0.01, "source": "test"}}


async def test_a_field_without_a_value_shows_the_value_in_effect_as_its_hint():
    forms = {}

    def root():
        forms["f"] = render_form(
            derive_schema(_Cfg, mode="x"),
            {},
            on_change=lambda v: None,
            in_effect=IN_EFFECT,
        )

    async with user_simulation(root=root) as user:
        await user.open("/")
        form = forms["f"]
        tol = form.controls["tolerance"]
        assert tol.props.get("hint") == "in effect: 0.01 (the test's tolerance)"
        # the dataclass default is no longer offered beside it
        assert tol.props.get("placeholder") is None
        assert form.values() == {}  # an in-effect value is never written


async def test_a_typed_value_takes_the_hint_away_and_clearing_it_brings_it_back():
    """The hint names the value in effect while the field is empty: under a
    typed 0.5 it named a value no longer applied."""
    forms = {}

    def root():
        forms["f"] = render_form(
            derive_schema(_Cfg, mode="x"),
            {},
            on_change=lambda v: None,
            in_effect=IN_EFFECT,
        )

    async with user_simulation(root=root) as user:
        await user.open("/")
        tol = forms["f"].controls["tolerance"]
        tol.set_value(0.5)
        assert not tol.props.get("hint")
        assert forms["f"].values() == {"tolerance": 0.5}
        tol.set_value(None)
        assert tol.props.get("hint") == "in effect: 0.01 (the test's tolerance)"
        assert forms["f"].values() == {}


async def test_a_field_with_its_own_value_shows_no_hint_until_it_is_cleared():
    """The hint names what applies while the field is empty: an own 0.5
    shows none, and cleared the field says the base."""
    forms = {}

    def root():
        forms["f"] = render_form(
            derive_schema(_Cfg, mode="x"),
            {"tolerance": 0.5},
            on_change=lambda v: None,
            in_effect=IN_EFFECT,
        )

    async with user_simulation(root=root) as user:
        await user.open("/")
        tol = forms["f"].controls["tolerance"]
        assert not tol.props.get("hint")
        assert forms["f"].values() == {"tolerance": 0.5}
        tol.set_value(None)
        assert tol.props.get("hint") == "in effect: 0.01 (the test's tolerance)"
        assert forms["f"].values() == {}
        tol.set_value(0.2)
        assert not tol.props.get("hint")


async def test_every_one_line_field_reserves_the_bottom_line():
    forms = {}

    def root():
        forms["f"] = render_form(
            derive_schema(_Cfg, mode="x"), {}, on_change=lambda v: None
        )

    async with user_simulation(root=root) as user:
        await user.open("/")
        c = forms["f"].controls
        for name in ("tolerance", "mode_kind", "note", "max_value"):
            assert "bottom-slots" in c[name].props, name


async def test_a_field_with_a_hint_is_wide_enough_for_it_on_one_line():
    """K8: the hint's line never widens a Quasar field by itself."""
    from dstf.authoring.forms import HINT_PADDING_PX, HINT_PX_PER_CHAR

    forms = {}

    def root():
        forms["f"] = render_form(
            derive_schema(_Cfg, mode="x"),
            {},
            on_change=lambda v: None,
            in_effect=IN_EFFECT,
            classes="dstf-field",
        )

    async with user_simulation(root=root) as user:
        await user.open("/")
        c = forms["f"].controls
        hint = c["tolerance"].props["hint"]
        width = int(c["tolerance"]._style["min-width"].removesuffix("px"))
        assert width >= len(hint) * HINT_PX_PER_CHAR + HINT_PADDING_PX
        assert "min-width" not in c["note"]._style


# -- a number field keeps what is typed in it ----------------------------------


def test_every_number_field_of_the_tool_is_a_typed_number():
    """`ui.number` is called nowhere under `authoring/`: a plain one is
    overwritten, under the keys, by the server's answer to an earlier key
    (`typed_number.py`)."""
    root = Path(dstf.authoring.__file__).parent
    plain = [
        f"{path.relative_to(root)}:{node.lineno}"
        for path in sorted(root.rglob("*.py"))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "number"
    ]
    assert plain == []


async def test_a_typed_number_is_drawn_by_its_own_component():
    """`ui.number` names `q-input` as its tag over the component a subclass
    registers; left so, the browser draws the plain field."""
    made = {}

    def root():
        made["n"] = TypedNumber(label="n", value=0.3)

    async with user_simulation(root=root) as user:
        await user.open("/")
        component = TypedNumber.component
        assert component.path.name == "typed_number.js" and component.path.is_file()
        assert made["n"].tag == component.tag != "q-input"
        assert isinstance(made["n"], ui.number) and made["n"].value == 0.3


async def test_an_edit_resends_the_field_only_when_its_hint_changes(monkeypatch):
    """A prop written is the whole field sent again. The hint is written
    when a value takes it away and when a clearing brings it back, not on
    every key between."""
    forms = {}

    def root():
        forms["f"] = render_form(
            derive_schema(_Cfg, mode="x"),
            {},
            on_change=lambda v: None,
            in_effect=IN_EFFECT,
        )

    async with user_simulation(root=root) as user:
        await user.open("/")
        tol = forms["f"].controls["tolerance"]
        assert isinstance(tol, TypedNumber)
        sent = []
        update = tol.update
        monkeypatch.setattr(tol, "update", lambda: (sent.append(1), update())[1])
        tol.set_value(0.5)  # the hint goes
        assert not tol.props.get("hint") and len(sent) > 1
        sent.clear()
        tol.set_value(0.6)
        assert len(sent) == 1  # the value's own
        tol.set_value(None)
        assert tol.props.get("hint") == "in effect: 0.01 (the test's tolerance)"


# -- a tolerance's two sides: two fields and a link toggle ---------------------

HINT = "in effect: 0.01 (the test's tolerance)"


def _sides(value, *, hint=None, side_labels=("−", "+")):
    """A page holding one `sides_control`: what ``on_change`` was handed, in
    order, the control once built, and the page's root."""
    from dstf.authoring.forms import sides_control

    seen: list = []
    made: dict = {}

    def root():
        made["g"] = sides_control(
            "Tol", value, seen.append, side_labels=side_labels, hint=hint, mark="t"
        )

    return seen, made, root


async def test_linked_typing_in_either_field_sets_both_and_hands_over_once():
    """The call COUNT: setting the other field from the server fires that
    field's own change handler, and unguarded every keystroke reached
    `on_change` twice — which "one pending edit" cannot see (the Session
    folds two replaces of one leaf)."""
    seen, made, root = _sides(None)
    async with user_simulation(root=root) as user:
        await user.open("/")
        g = made["g"]
        assert g.linked and (g.lower.value, g.upper.value) == (None, None)
        assert g.lower.props["label"] == "− Tol" and g.upper.props["label"] == "+ Tol"
        g.lower.set_value(0.05)
        assert g.upper.value == 0.05
        assert seen == [0.05]
        g.upper.set_value(0.07)
        assert g.lower.value == 0.07
        assert seen == [0.05, 0.07]
        # Cleared in either: both clear, and the tolerance is unset.
        g.upper.set_value(None)
        assert g.lower.value is None
        assert seen == [0.05, 0.07, None]
        assert g.linked and not g.half()
        assert g.lower.error is None and g.upper.error is None


async def test_the_side_labels_and_the_marks_are_the_callers():
    seen, made, root = _sides(0.2, side_labels=("before", "after"))
    async with user_simulation(root=root) as user:
        await user.open("/")
        g = made["g"]
        assert g.lower.props["label"] == "before Tol"
        assert g.upper.props["label"] == "after Tol"
        assert user.find(marker="t:lower").elements == {g.lower}
        assert user.find(marker="t:upper").elements == {g.upper}
        assert user.find(marker="t:link").elements == {g.link}
        assert (g.lower.value, g.upper.value) == (0.2, 0.2)
        # The bottom line is the form's to reserve: a table's row does not.
        assert "bottom-slots" not in g.lower.props
        assert "bottom-slots" not in g.upper.props


async def test_the_toggle_says_its_state():
    seen, made, root = _sides(0.2)
    async with user_simulation(root=root) as user:
        await user.open("/")
        g = made["g"]

        def state():
            (tip,) = [c for c in g.link.default_slot.children]
            return g.link.props["icon"], g.link.props["aria-pressed"], tip.text

        assert state() == ("link", "true", "Linked: both sides change together")
        assert g.link.props["aria-label"] == "Link the two sides of Tol"
        user.find(marker="t:link").click()
        assert state() == (
            "link_off",
            "false",
            "Unlinked: each side is set on its own",
        )
        user.find(marker="t:link").click()
        assert state() == ("link", "true", "Linked: both sides change together")


async def test_a_pair_opens_unlinked_and_typing_hands_over_the_pair():
    """The shape is the link state: a pair is unlinked — equal sides too."""
    seen, made, root = _sides({"lower": 0.1, "upper": 0.3})
    equal = _sides({"lower": 0.2, "upper": 0.2})
    async with user_simulation(root=root) as user:
        await user.open("/")
        g = made["g"]
        assert not g.linked and g.link.props["icon"] == "link_off"
        assert (g.lower.value, g.upper.value) == (0.1, 0.3)
        g.lower.set_value(0.2)
        assert g.upper.value == 0.3  # its own side only
        assert seen == [{"lower": 0.2, "upper": 0.3}]
        g.upper.set_value(0.2)
        assert seen[-1] == {"lower": 0.2, "upper": 0.2}  # equal, and still a pair
        assert not g.linked
    async with user_simulation(root=equal[2]) as user:
        await user.open("/")
        g = equal[1]["g"]
        assert not g.linked and (g.lower.value, g.upper.value) == (0.2, 0.2)
        g.lower.set_value(0.4)
        assert g.upper.value == 0.2 and equal[0] == [{"lower": 0.4, "upper": 0.2}]


async def test_a_pair_with_one_side_blank_hands_over_nothing_and_is_marked():
    """Half a pair is no value: nothing is handed over, the blank field
    says "required" through its own validation — the one mark NiceGUI does
    not reset — and `half()` tells a caller its last value is stale."""
    seen, made, root = _sides({"lower": 0.1, "upper": 0.3})
    async with user_simulation(root=root) as user:
        await user.open("/")
        g = made["g"]
        assert not g.half()
        g.lower.set_value(None)
        assert seen == []
        assert g.half()
        assert g.lower.error == "required" and g.upper.error is None
        g.lower.set_value(0.25)
        assert seen == [{"lower": 0.25, "upper": 0.3}]
        assert not g.half() and g.lower.error is None
        # The other side, and then both blank: unset, and linked again.
        g.upper.set_value(None)
        assert g.half() and g.upper.error == "required" and g.lower.error is None
        assert seen == [{"lower": 0.25, "upper": 0.3}]
        g.lower.set_value(None)
        assert seen == [{"lower": 0.25, "upper": 0.3}, None]
        assert g.linked and g.link.props["icon"] == "link"
        assert not g.half()
        assert g.lower.error is None and g.upper.error is None


@pytest.mark.parametrize(
    ("lower", "upper", "larger"), [(0.5, 0.2, 0.5), (0.2, 0.5, 0.5), (0.3, 0.3, 0.3)]
)
async def test_unlinking_writes_the_number_twice_and_relinking_the_larger_side(
    lower, upper, larger
):
    """Re-link takes the LARGER side for both, whichever it is: a re-link
    never narrows what the criterion allows."""
    seen, made, root = _sides(0.2)
    async with user_simulation(root=root) as user:
        await user.open("/")
        g = made["g"]
        user.find(marker="t:link").click()
        assert seen == [{"lower": 0.2, "upper": 0.2}]
        assert not g.linked and (g.lower.value, g.upper.value) == (0.2, 0.2)
        g.lower.set_value(lower)
        g.upper.set_value(upper)
        seen.clear()
        user.find(marker="t:link").click()
        assert seen == [larger]
        assert g.linked and (g.lower.value, g.upper.value) == (larger, larger)
        g.lower.set_value(0.9)  # linked again: both move, one call
        assert g.upper.value == 0.9 and seen == [larger, 0.9]


async def test_unset_unlinked_stays_unset_and_a_half_pair_relinks_to_its_side():
    seen, made, root = _sides(None, hint=HINT)
    async with user_simulation(root=root) as user:
        await user.open("/")
        g = made["g"]
        user.find(marker="t:link").click()
        assert seen == [] and not g.linked
        assert (g.lower.value, g.upper.value) == (None, None)
        assert g.lower.error is None and g.upper.error is None  # nothing required
        assert g.lower.props.get("hint") == HINT
        # One side typed, unlinked: half a pair.
        g.upper.set_value(0.4)
        assert seen == [] and g.half() and g.lower.error == "required"
        # Linked again: the one side there is both.
        user.find(marker="t:link").click()
        assert seen == [0.4] and (g.lower.value, g.upper.value) == (0.4, 0.4)
        assert not g.half() and g.lower.error is None
        # And unset, re-linked: nothing is written.
        seen.clear()
        g.lower.set_value(None)
        assert seen == [None]
        user.find(marker="t:link").click()
        user.find(marker="t:link").click()
        assert seen == [None] and g.linked


async def test_the_hint_shows_under_the_first_field_only_while_both_are_empty():
    from dstf.authoring.forms import hint_width

    seen, made, root = _sides(None, hint=HINT)
    pair = _sides({"lower": 0.1, "upper": 0.3}, hint=HINT)
    async with user_simulation(root=root) as user:
        await user.open("/")
        g = made["g"]
        assert g.lower.props.get("hint") == HINT
        assert not g.upper.props.get("hint")
        g.upper.set_value(0.5)
        assert not g.lower.props.get("hint") and not g.upper.props.get("hint")
        g.lower.set_value(None)
        assert g.lower.props.get("hint") == HINT
        assert seen == [0.5, None]
        # The width the hint needs on one line is the stylesheet's to apply
        # (it drops it where the row has no room): a variable on the group,
        # no min-width — and no inline `flex-wrap`, which no rule overrides.
        assert "dstf-sides-hinted" in g.classes
        assert g._style["--dstf-hint-width"] == f"{hint_width(HINT)}px"
        assert hint_width(HINT) >= len(HINT) * 5.4 + 24
        assert "min-width" not in g.lower._style and "min-width" not in g._style
        assert "flex-wrap" not in g._style
    async with user_simulation(root=pair[2]) as user:
        await user.open("/")
        g = pair[1]["g"]
        assert "dstf-sides" in g.classes
        assert not g.lower.props.get("hint")  # a side holds a value
        g.lower.set_value(None)
        assert not g.lower.props.get("hint")  # the other still does
        g.upper.set_value(None)
        assert g.lower.props.get("hint") == HINT


@pytest.mark.parametrize(
    "written", ["0.01", True, {"lower": 0.1}, {"lower": "a", "upper": 0.3}, [1]]
)
async def test_a_value_that_is_no_tolerance_still_opens(written):
    """What the file holds may be the leaf's config error; its editor opens
    all the same, each side that is no number shown blank."""
    seen, made, root = _sides(written)
    async with user_simulation(root=root) as user:
        await user.open("/")
        g = made["g"]
        assert g.linked == (not isinstance(written, dict))
        assert g.lower.value in (None, 0.1) and g.upper.value in (None, 0.3)
        assert seen == []


@dataclasses.dataclass(frozen=True)
class _Sided:
    tolerance: Sides = dataclasses.field(
        default=Sides(1e-4, 1e-4),
        metadata={"label": "Tol", "help": "how far", "side_labels": ("early", "late")},
    )
    ceiling: float = 1e-4


async def test_a_sides_field_renders_as_the_group_and_a_float_as_one_field():
    seen = []
    forms = {}

    def root():
        forms["f"] = render_form(
            derive_schema(_Sided, mode="x"),
            {"tolerance": {"lower": 0.1, "upper": 0.3}},
            on_change=lambda values: seen.append(dict(values)),
            classes="dstf-field",
            in_effect={
                "tolerance": {"value": 0.01, "source": "test"},
                "ceiling": {"value": 0.01, "source": "test"},
            },
        )

    async with user_simulation(root=root) as user:
        await user.open("/")
        form = forms["f"]
        g = form.controls["tolerance"]
        assert isinstance(g, SidesControl)
        assert not g.linked and (g.lower.value, g.upper.value) == (0.1, 0.3)
        assert g.lower.props["label"] == "early Tol"
        assert g.upper.props["label"] == "late Tol"
        assert user.find(marker="tolerance:link").elements == {g.link}
        assert form.values() == {"tolerance": {"lower": 0.1, "upper": 0.3}}
        # The row's classes are each field's, not the group's; and both
        # reserve the bottom line, as every one-line field of a form does.
        assert "dstf-field" in g.lower.classes and "dstf-field" in g.upper.classes
        assert "dstf-field" not in g.classes
        assert "bottom-slots" in g.lower.props and "bottom-slots" in g.upper.props
        g.upper.set_value(0.4)
        assert seen == [{"tolerance": {"lower": 0.1, "upper": 0.4}}]
        user.find(marker="tolerance:link").click()
        assert seen[-1] == {"tolerance": 0.4}
        g.lower.set_value(None)  # unset: omitted, and the hint says what applies
        assert seen[-1] == {} and form.values() == {}
        assert g.lower.props.get("hint") == HINT
        # A plain number stays ONE field, hinted as before.
        assert isinstance(g.lower, TypedNumber) and isinstance(g.upper, TypedNumber)
        assert isinstance(form.controls["ceiling"], TypedNumber)
        assert form.controls["ceiling"].props.get("hint") == HINT


async def test_an_unset_sides_field_shows_the_hint_and_writes_nothing():
    seen = []
    forms = {}

    def root():
        forms["f"] = render_form(
            derive_schema(_Sided, mode="x"),
            {},
            on_change=lambda values: seen.append(dict(values)),
            in_effect={"tolerance": {"value": 0.01, "source": "test"}},
        )

    async with user_simulation(root=root) as user:
        await user.open("/")
        g = forms["f"].controls["tolerance"]
        assert g.linked and (g.lower.value, g.upper.value) == (None, None)
        assert g.lower.props.get("hint") == HINT
        # No placeholder beside it: the default is not what applies.
        assert g.lower.props.get("placeholder") is None
        assert forms["f"].values() == {} and seen == []


async def test_a_required_tolerance_marks_both_fields_while_it_is_unset():
    """A table's required column: blank, both fields say so — and `None` is
    still handed over; holding the list back is the table's."""
    from dstf.authoring.forms import sides_control

    seen: list = []
    made: dict = {}

    def root():
        made["g"] = sides_control(
            "tol", None, seen.append, side_labels=("−", "+"), required=True
        )

    async with user_simulation(root=root) as user:
        await user.open("/")
        g = made["g"]
        assert (g.lower.error, g.upper.error) == ("required", "required")
        assert not g.half()  # nothing is not half a pair
        g.upper.set_value(0.2)
        assert seen == [0.2] and (g.lower.error, g.upper.error) == (None, None)
        g.lower.set_value(None)
        assert seen == [0.2, None]
        assert (g.lower.error, g.upper.error) == ("required", "required")
