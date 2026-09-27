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
from dstf.authoring.capabilities import Placement  # noqa: E402
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
        await user.should_see(marker="action:rerun", retries=20)
        bar = bars[0]
        rerun = _one(user, "action:rerun")
        assert rerun.props.get("icon") == "replay" and not rerun.text
        # Unavailable: the reason is the tooltip and the accessible name.
        assert _one(user, "action:rerun:tip").text == "Rerun needs a selection"
        assert rerun.props.get("aria-label") == "Rerun needs a selection"
        assert not rerun.enabled
        checked.update({A, D})
        bar.refresh()
        assert rerun.enabled
        assert _one(user, "action:rerun:tip").text == "Rerun 2 selected tests"
        assert rerun.props.get("aria-label") == "Rerun 2 selected tests"
        accept = _one(user, "action:accept")
        assert accept.props.get("icon") == "done_all" and not accept.text
        # A disabled Quasar button takes no pointer events: the tooltip is
        # on the span around it.
        assert _one(user, "action:rerun:tip").parent_slot.parent.tag == "span"
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
        await user.should_see(marker="action:rerun", retries=20)
        assert _one(user, "action:rerun").text == "Rerun 1 selected test"
        assert not _one(user, "action:accept").props.get("icon")
