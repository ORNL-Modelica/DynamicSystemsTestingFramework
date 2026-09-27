"""Selection state per browser tab (brief §3.2; D129): what the Suite table
has selected (its filters, sort and layout are the viewer's, in the
browser's localStorage — `suite_grid.js`). Kept in NiceGUI's tab storage, so
it is there after navigating to a Test page and back within the tab, and a
second tab on the same server starts with its own. Volatile: gone when the
tab closes. Never the Session's — that is the decision."""

from __future__ import annotations

from dataclasses import dataclass, field

from nicegui import app

KEY = "dstf"


@dataclass
class TabState:
    checked: set[str] = field(default_factory=set)
    #: The Test page's open drawer — "tests", "variables" or None — and the
    #: Tests drawer's filter (3c): the tab's, so a switch of test keeps them.
    drawer: str | None = None
    tests_needle: str = ""


def tab_state() -> TabState:
    """This tab's state, created on first use. Needs a connected client: a
    page awaits ``ui.context.client.connected()`` before calling this."""
    store = app.storage.tab
    state = store.get(KEY)
    if not isinstance(state, TabState):
        state = TabState()
        store[KEY] = state
    return state
