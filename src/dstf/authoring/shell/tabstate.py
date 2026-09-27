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
    #: The Test page's open drawer — "tests", "variables" or None — and
    #: every control of both drawers (3c; step T, Q7): the tab's, so a
    #: switch of test, which builds new drawers, keeps them.
    drawer: str | None = None
    tests_needle: str = ""
    tests_regex: bool = False
    tests_case: bool = False
    tests_sort: str = "package"  # "package" | "name" | "status"
    tests_grouped: bool = True
    tests_short: bool = True
    #: The status pills pressed (a status word, or "EDITED"); none = all.
    tests_pills: set[str] = field(default_factory=set)
    vars_needle: str = ""
    vars_regex: bool = False
    vars_case: bool = False
    vars_group: str = "component"  # "component" | "flat" | "tracked"
    vars_names: str = "both"  # "expression" | "file" | "both"
    vars_sort: str = "file"  # "file" | "name"


def tab_state() -> TabState:
    """This tab's state, created on first use. Needs a connected client: a
    page awaits ``ui.context.client.connected()`` before calling this."""
    store = app.storage.tab
    state = store.get(KEY)
    if not isinstance(state, TabState):
        state = TabState()
        store[KEY] = state
    return state
