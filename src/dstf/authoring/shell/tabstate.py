"""Selection state per browser tab (brief §3.2; D129): what the Suite page
has checked, its filter and its layout. Kept in NiceGUI's tab storage, so
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
    needle: str = ""
    status_filter: set[str] = field(default_factory=set)
    grouped: bool = True
    short: bool = True


def tab_state() -> TabState:
    """This tab's state, created on first use. Needs a connected client: a
    page awaits ``ui.context.client.connected()`` before calling this."""
    store = app.storage.tab
    state = store.get(KEY)
    if not isinstance(state, TabState):
        state = TabState()
        store[KEY] = state
    return state
