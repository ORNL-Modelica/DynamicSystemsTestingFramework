"""The tool's session switch (D148), in NiceGUI's user simulation — no
browser: which theme a tab starts in, what the ⋮ item offers and does, what
the Test page re-sends, and that the choice is the TAB's. What the browser
computes and keeps through the switch (the zoom, the arrows, a staged
edit, a reload, a second tab) is `tests/test_themes_playwright.py`."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("nicegui")

from nicegui.testing.user_simulation import user_simulation  # noqa: E402

from dstf.authoring.shell.shell import Shell  # noqa: E402
from dstf.authoring.shell.tab_theme import (  # noqa: E402
    STYLE_ID,
    TabTheme,
    project_theme,
)
from dstf.authoring.shell.tabstate import TabState  # noqa: E402
from dstf.figures.palette import palette_of  # noqa: E402
from dstf.reporting import theme  # noqa: E402
from test_session import A  # noqa: E402
from test_test_page import (  # noqa: E402,F401
    _one,
    _open,
    _registry,
    _sans_rev,
    _test_page,
)
from test_themes import themed_project  # noqa: E402


def _shell(tmp_path, name: str | None, opened: bool = True) -> Shell:
    cfg, work = themed_project(tmp_path, name)
    shell = Shell(cfg, work_dir=work)
    if opened:
        shell.session.open(cfg, work_dir=work)
        shell.load.phase = "done"
    return shell


def _recorded(page) -> list[str]:
    """What the page's client is sent to run from here on, in order."""
    sent: list[str] = []
    page.client.run_javascript = lambda code, *a, **k: sent.append(code)
    return sent


# -- the theme a tab starts in --------------------------------------------------


@pytest.mark.parametrize("name", [None, "default", "dark"])
def test_a_tab_starts_in_its_projects_theme(tmp_path, name):
    shell = _shell(tmp_path, name)
    assert project_theme(shell) == (name or "default")


def test_the_theme_is_read_before_the_project_is_open(tmp_path):
    """The first page is drawn before a result is read: a dark project's
    must not be drawn light until someone reloads it."""
    shell = _shell(tmp_path, "dark", opened=False)
    assert shell.session.project is None
    assert project_theme(shell) == "dark"


@pytest.mark.parametrize("raw", ['"solarized"', "7", "[]"])
def test_a_file_the_open_will_refuse_reads_default_here(tmp_path, raw):
    shell = _shell(tmp_path, "dark", opened=False)
    text = shell.config_path.read_text(encoding="utf-8")
    shell.config_path.write_text(text.replace('"dark"', raw), encoding="utf-8")
    assert json.loads(shell.config_path.read_text(encoding="utf-8"))["theme"] != "dark"
    assert project_theme(shell) == "default"
    shell.config_path.write_text("{ not json", encoding="utf-8")
    assert project_theme(shell) == "default"


def test_the_tabs_choice_outranks_the_projects(tmp_path):
    shell = _shell(tmp_path, "dark")
    # No page: `name` and `other` read the tab's state and the shell alone.
    chosen = TabTheme.__new__(TabTheme)
    chosen.shell, chosen.state = shell, TabState(theme="default")
    unchosen = TabTheme.__new__(TabTheme)
    unchosen.shell, unchosen.state = shell, TabState()
    assert (chosen.name, chosen.other) == ("default", "dark")
    assert (unchosen.name, unchosen.other) == ("dark", "default")


# -- the page -------------------------------------------------------------------


async def test_a_dark_projects_page_is_built_dark(tmp_path):
    shell = _shell(tmp_path, "dark")
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        (page,) = shell.pages
        assert page.theme.name == "dark"
        assert page.state.theme is None  # nobody switched
        assert page.theme._dark.value is True
        head = user.client._head_html
        assert f'<style id="{STYLE_ID}">' in head
        assert "--dstf-page: #121212;" in head and "color-scheme: dark;" in head
        assert _one(user, "theme").default_slot.children[0].text == "Default theme"


async def test_a_default_projects_page_offers_dark(tmp_path):
    shell = _shell(tmp_path, None)
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        (page,) = shell.pages
        assert page.theme.name == "default"
        assert page.theme._dark.value is False
        assert "--dstf-page: #fafafa;" in user.client._head_html
        assert "color-scheme" not in user.client._head_html
        assert _one(user, "theme").default_slot.children[0].text == "Dark theme"


async def test_the_item_switches_this_tab_and_offers_the_way_back(tmp_path):
    shell = _shell(tmp_path, None)
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        (page,) = shell.pages
        sent = _recorded(page)
        user.find(marker="theme").click()
        assert page.state.theme == "dark"
        assert page.theme.name == "dark" and page.theme._dark.value is True
        assert _one(user, "theme").default_slot.children[0].text == "Default theme"
        # The token block rewritten in place, and Quasar's own variables.
        (dark,) = sent
        assert f'getElementById("{STYLE_ID}").textContent' in dark
        assert "--dstf-page: #121212;" in dark
        assert '"--q-primary": "#64b5f6"' in dark
        assert '"--q-dark": "#1e1e1e"' in dark and '"--q-dark-page": "#121212"' in dark
        user.find(marker="theme").click()
        assert page.state.theme == "default"
        assert page.theme._dark.value is False
        assert _one(user, "theme").default_slot.children[0].text == "Dark theme"
        assert "--dstf-page: #fafafa;" in sent[1]
        assert '"--q-primary": "#1976D2"' in sent[1]


async def test_the_tabs_theme_follows_it_to_its_other_page(tmp_path):
    """Switched on the Suite page, kept on the Test page of the same tab:
    its token block, its dark mode and its plots."""
    shell = _shell(tmp_path, None)
    async with user_simulation(root=shell.index) as user:
        await _open(user)
        user.find(marker="theme").click()
        page = await _test_page(shell, user)
        assert page.theme.name == "dark" and page.theme._dark.value is True
        with page.client:
            page.cards[0].show()
        figure = page.cards[0].plot.figure
        assert figure["layout"]["template"] == {"layout": palette_of("dark").template}


async def test_a_switch_re_sends_every_drawn_plot_and_no_other(tmp_path):
    shell = _shell(tmp_path, None)
    async with user_simulation(root=shell.index) as user:
        page = await _test_page(shell, user)
        with page.client:
            page.cards[0].show()
        first, undrawn = page.cards[0], page.cards[1]
        plot = first.plot
        assert "template" not in plot.figure["layout"]
        assert _sans_rev(plot.figure) == shell.session.plot_figure(
            A, 0, editing=(), visibility=first.visibility()
        )
        revision = plot.figure["layout"]["editrevision"]
        user.find(marker="theme").click()
        # The same element, a new figure: a `Plotly.react`, never a rebuild.
        assert first.plot is plot and undrawn.plot is None
        assert (
            plot.figure["layout"]["template"]["layout"]["paper_bgcolor"]
            == (theme.DARK_THEME["surface"])
        )
        assert plot.figure["layout"]["editrevision"] == revision + 1
        assert _sans_rev(plot.figure) == shell.session.plot_figure(
            A, 0, editing=(), visibility=first.visibility(), theme="dark"
        )
        # A card drawn after the switch is drawn in the tab's theme.
        with page.client:
            undrawn.show()
        assert "template" in undrawn.plot.figure["layout"]
        user.find(marker="theme").click()
        assert "template" not in plot.figure["layout"]
        assert "template" not in undrawn.plot.figure["layout"]


async def test_the_sessions_figure_is_default_unless_told(tmp_path):
    """The theme is the tab's: the Session holds none, and two tabs in two
    themes read one Session."""
    shell = _shell(tmp_path, "dark")
    session = shell.session
    assert "template" not in session.plot_figure(A, 0)["layout"]
    assert "template" not in session.figure(A, "x")["layout"]
    for build in (
        lambda: session.plot_figure(A, 0, theme="dark"),
        lambda: session.figure(A, "x", theme="dark"),
        lambda: session.plot_figure(A, 2, theme="dark"),  # a watch plot
    ):
        assert build()["layout"]["template"] == {"layout": palette_of("dark").template}
