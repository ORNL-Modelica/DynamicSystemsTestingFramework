"""An ActionBar in icon form (the Suite toolbar): each placed capability
that declares an ``icon`` is an icon button whose tooltip and accessible
name are its caption, or its why-sentence when it is unavailable; a click
opens the same dialog. One that declares none keeps its text button."""

from __future__ import annotations

import pytest

pytest.importorskip("nicegui")

from nicegui import ui  # noqa: E402
from nicegui.testing.user_simulation import user_simulation  # noqa: E402

from dstf.authoring import capabilities as caps  # noqa: E402
from dstf.authoring.builtin import register_builtins  # noqa: E402
from dstf.authoring.capabilities import Emphasis, Placement  # noqa: E402
from dstf.authoring.session import Selection  # noqa: E402
from dstf.authoring.shell.actions import ActionBar  # noqa: E402
from dstf.authoring.shell.shell import Shell  # noqa: E402
from test_session import A, D, _project  # noqa: E402


@pytest.fixture(autouse=True)
def _registry():
    caps._reset_for_tests()
    register_builtins()
    yield
    caps._reset_for_tests()
    register_builtins()


@pytest.fixture
def shell(tmp_path):
    cfg, work = _project(tmp_path)
    s = Shell(cfg, work_dir=work)
    s.session.open(cfg, work_dir=work)
    s.load.phase = "done"
    return s


class _Plain:
    """A plug-in that declares no icon."""

    name = "plain"
    label = "Plain"
    placements = frozenset({Placement.SUITE})

    def available(self, s):
        return True

    def caption(self, s):
        return "Do the plain thing"

    def panel(self, s, ui_panel):
        pass

    def act(self, s, **inputs):
        return None


def _one(user, marker):
    (e,) = user.find(marker=marker).elements
    return e


def _bar(shell, checked, bars, *, icons):
    async def root():
        await ui.context.client.connected()
        bars.append(
            ActionBar(
                shell,
                Placement.SUITE,
                lambda: Selection(tests=frozenset(checked)),
                marker="suite-actions",
                icons=icons,
            )
        )

    return root


async def test_icon_buttons_with_captions_as_tooltips(shell):
    checked: set[str] = set()
    bars: list[ActionBar] = []
    async with user_simulation(root=_bar(shell, checked, bars, icons=True)) as user:
        await user.open("/")
        await user.should_see(marker="action:enqueue", retries=20)
        bar = bars[0]
        queue = _one(user, "action:enqueue")
        assert queue.props.get("icon") == "playlist_add" and not queue.text
        # Unavailable: the reason is the tooltip and the accessible name.
        assert _one(user, "action:enqueue:tip").text == "Queue needs a selection"
        assert queue.props.get("aria-label") == "Queue needs a selection"
        assert not queue.enabled
        checked.update({A, D})
        bar.refresh()
        assert queue.enabled
        assert _one(user, "action:enqueue:tip").text == "Queue 2"
        assert queue.props.get("aria-label") == "Queue 2"
        accept = _one(user, "action:accept")
        assert accept.props.get("icon") == "done_all" and not accept.text
        # A disabled Quasar button takes no pointer events: the tooltip is
        # on the span around it.
        assert _one(user, "action:enqueue:tip").parent_slot.parent.tag == "span"
        user.find(marker="action:accept").click()
        await user.should_see(marker="panel:accept", retries=20)


async def test_a_capability_without_an_icon_keeps_its_text(shell):
    """The shell names no capability: an icon is what the capability
    declares, so a plug-in that declares none is a text button."""
    caps.register_capability(_Plain())
    bars: list[ActionBar] = []
    async with user_simulation(root=_bar(shell, {A}, bars, icons=True)) as user:
        await user.open("/")
        await user.should_see(marker="action:plain", retries=20)
        plain = _one(user, "action:plain")
        assert plain.text == "Do the plain thing" and not plain.props.get("icon")


async def test_a_bar_without_icons_keeps_its_text_buttons(shell):
    bars: list[ActionBar] = []
    async with user_simulation(root=_bar(shell, {A}, bars, icons=False)) as user:
        await user.open("/")
        await user.should_see(marker="action:enqueue", retries=20)
        assert _one(user, "action:enqueue").text == "Queue 1"
        assert not _one(user, "action:accept").props.get("icon")


# -- emphasis: the capability that is the next action is filled, one that
# is not tucked into the bar's ⋮ menu (the Test page's strip) -------------


def _none(user, marker):
    try:
        user.find(marker=marker)
    except AssertionError:
        return True
    return False


class _Fake(_Plain):
    """A plug-in at the SUITE placement named ``name``; ``why`` makes it
    unavailable with that sentence."""

    def __init__(self, name, why=None):
        self.name, self.label, self.why = name, name.title(), why

    def available(self, s):
        return True if self.why is None else self.why

    def caption(self, s):
        return f"Do {self.name}"

    def panel(self, s, ui_panel):
        ui.label(f"the {self.name} panel")


def _emphasis_bar(shell, answers, bars):
    async def root():
        await ui.context.client.connected()
        bars.append(
            ActionBar(
                shell,
                Placement.SUITE,
                lambda: Selection(tests=frozenset({A})),
                marker="bar",
                emphasis=lambda cap: answers.get(cap.name),
            )
        )

    return root


def _texts(item) -> list[str]:
    """What a menu item reads: its visible labels, top to bottom."""
    return [c.text for c in item.descendants() if isinstance(c, ui.label) and c.visible]


async def test_emphasis_fills_the_next_action_and_tucks_menu_ones_away(shell):
    for name in ("nxt", "qut"):
        caps.register_capability(_Fake(name))
    caps.register_capability(_Fake("mnu", why="nothing new to take"))
    caps.register_capability(_Fake("mn2"))
    answers = {"nxt": Emphasis.NEXT, "mnu": Emphasis.MENU, "mn2": Emphasis.MENU}
    bars: list[ActionBar] = []
    async with user_simulation(root=_emphasis_bar(shell, answers, bars)) as user:
        await user.open("/")
        await user.should_see(marker="action:nxt", retries=20)
        nxt, qut = _one(user, "action:nxt"), _one(user, "action:qut")
        assert "unelevated" in nxt.props and nxt.props.get("color") == "primary"
        assert "flat" in qut.props and "unelevated" not in qut.props
        assert "outline" not in qut.props and "outline" not in nxt.props
        # MENU: not a button in the bar, an item of the ⋮ at its end.
        assert _none(user, "action:mnu")
        more = _one(user, "action-more")
        # Named, with no tooltip: one covered the open menu's first item.
        assert more.props.get("aria-label") == "More actions"
        assert not [c for c in more.default_slot.children if isinstance(c, ui.tooltip)]
        assert more.props.get("icon") == "more_vert"
        item = _one(user, "menu-action:mnu")
        assert item.props.get("role") == "menuitem"
        assert not item.enabled
        # Disabled, it has nothing to act on: its name and the sentence that
        # says why, on the item — not its caption, which counted ("Accept 0
        # results as baselines") what it would not do, with the sentence in
        # a tooltip.
        why = _one(user, "menu-action:mnu:why")
        assert why.visible and why.text == "nothing new to take"
        assert _texts(item) == ["Mnu", "nothing new to take"]
        mn2 = _one(user, "menu-action:mn2")
        assert mn2.enabled
        assert _texts(mn2) == ["Do mn2"]
        # The same dialog `open(cap)` opens.
        user.find(marker="menu-action:mn2").click()
        await user.should_see(marker="panel:mn2", retries=20)
        await user.should_see("the mn2 panel")
        # The answer moves: the bar rebuilds.
        answers.clear()
        answers["mnu"] = Emphasis.NEXT
        bars[0].refresh()
        assert "flat" in _one(user, "action:nxt").props
        mnu = _one(user, "action:mnu")
        assert "unelevated" in mnu.props and not mnu.enabled
        assert _none(user, "action-more")
        assert _none(user, "menu-action:mnu")


async def test_a_bar_with_no_menu_capability_draws_no_more_button(shell):
    bars: list[ActionBar] = []
    async with user_simulation(root=_emphasis_bar(shell, {}, bars)) as user:
        await user.open("/")
        await user.should_see(marker="action:enqueue", retries=20)
        assert _none(user, "action-more")
        assert "flat" in _one(user, "action:enqueue").props


async def test_a_bar_without_emphasis_draws_todays_outlined_buttons(shell):
    bars: list[ActionBar] = []
    async with user_simulation(root=_bar(shell, {A}, bars, icons=False)) as user:
        await user.open("/")
        await user.should_see(marker="action:enqueue", retries=20)
        for marker in ("action:enqueue", "action:accept"):
            props = _one(user, marker).props
            assert "outline" in props and "unelevated" not in props
            assert "flat" not in props
        assert _none(user, "action-more")


async def test_a_cleared_bar_draws_its_buttons_afresh(shell):
    """`clear` forgets what the buttons were built from: the next refresh
    draws them again, whatever the emphasis (a test that comes back)."""
    answers = {"enqueue": Emphasis.NEXT}
    bars: list[ActionBar] = []
    async with user_simulation(root=_emphasis_bar(shell, answers, bars)) as user:
        await user.open("/")
        await user.should_see(marker="action:enqueue", retries=20)
        bars[0].clear()
        assert _none(user, "action:enqueue") and bars[0].actions == {}
        bars[0].refresh()
        assert "unelevated" in _one(user, "action:enqueue").props
