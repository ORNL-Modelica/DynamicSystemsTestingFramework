"""The tab's theme on one page (D148): the project's (`testing.json`
``"theme"``) until the tab switches, then the tab's own — kept in its
`TabState`, so a reload and the tab's other page keep it, and a second tab
on the same server is not touched.

The switch is LIVE, with no reload: the `:root` token block is one
`<style>` this module owns and rewrites in place, Quasar's palette and its
dark mode follow, and the page then re-sends what the server drew with a
theme's literals in it — its plots. A drawn plot takes its new figure as a
`Plotly.react` on the same node (`PlotCard.redraw`), which keeps the zoom,
the drag config, the arrows' map and a staged edit. `Plotly.relayout` with
a template does not: it leaves the ring and the context series in the old
theme's colours, and the next server redraw paints the old ground again
(measured in Chromium on the tool's plotly.js 3.1.1, 2026-10-08).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING

from nicegui import ui

from ...config import ConfigError, load_config_file
from ...reporting.theme import THEMES, css_variables, is_dark, quasar_palette
from .tabstate import TabState

if TYPE_CHECKING:
    from .shell import Shell

#: The id of the `<style>` holding the theme's `:root` block.
STYLE_ID = "dstf-theme"


def project_theme(shell: Shell) -> str:
    """The theme a tab starts in: the open project's, and before the open
    has built its `Config` — the first page is drawn before a result is
    read — what its testing.json says. A file the open will refuse reads
    `default` here: the page's progress line says why it did not open."""
    project = shell.session.project
    if project is not None:
        return project.config.theme
    try:
        said = load_config_file(shell.config_path).get("theme")
    except (ConfigError, AttributeError):
        return "default"
    return said if isinstance(said, str) and said in THEMES else "default"


def first_paint(shell: Shell) -> None:
    """The page's ground in its first HTML — called BEFORE the handshake,
    as a page's scripts are. Which tab this is, and so its theme, is known
    only once the browser has connected; until then the window was white,
    0.2-1.1 s of every load in a dark project (measured in Chromium). The
    PROJECT's ground stands in: right for every tab that has not switched,
    and the shell's own `html` rule, laid with the tab's tokens, follows it
    in the cascade and wins."""
    page = THEMES[project_theme(shell)]["page"]
    ui.add_head_html(f"<style>html {{ background: {page}; }}</style>")


class TabTheme:
    def __init__(
        self,
        shell: Shell,
        state: TabState,
        switched: Callable[[], None] | None = None,
    ) -> None:
        """Lay the tab's theme on the page being built: Quasar's palette
        and dark mode, and the token block. ``switched`` is the page's own
        follow-up to a switch — what it must re-send."""
        self.shell, self.state, self._switched = shell, state, switched
        self.client = ui.context.client
        name = self.name
        ui.colors(**quasar_palette(name))
        # Quasar's dark mode beside the tokens: what Quasar draws itself (a
        # menu, a field, a dialog's backdrop) and AG Grid, which NiceGUI
        # switches off `body--dark`, follow it.
        self._dark = ui.dark_mode(is_dark(name))
        ui.add_head_html(f'<style id="{STYLE_ID}">{css_variables(name)}</style>')

    @property
    def name(self) -> str:
        return self.state.theme or project_theme(self.shell)

    @property
    def other(self) -> str:
        """The bundled theme a switch goes to: the next one, around."""
        names = list(THEMES)
        return names[(names.index(self.name) + 1) % len(names)]

    def switch(self) -> None:
        name = self.other
        self.state.theme = name
        # `ui.colors` sets Quasar's variables once, when it mounts: a
        # switch sets them where it did.
        palette = {
            f"--q-{key.replace('_', '-')}": value
            for key, value in quasar_palette(name).items()
        }
        self.client.run_javascript(
            f"document.getElementById({json.dumps(STYLE_ID)}).textContent = "
            f"{json.dumps(css_variables(name))};"
            f"for (const [k, v] of Object.entries({json.dumps(palette)})) "
            "document.body.style.setProperty(k, v);"
        )
        self._dark.value = is_dark(name)
        if self._switched is not None:
            self._switched()
