"""Selection state per browser tab (brief §3.2; D129): what the Suite table
has selected (its filters, sort and layout are the viewer's, in the
browser's localStorage — `suite_grid.js`). Kept in NiceGUI's tab storage, so
it is there after navigating to a Test page and back within the tab, and a
second tab on the same server starts with its own. Volatile: gone when the
tab closes. Never the Session's — that is the decision."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nicegui import app

KEY = "dstf"


@dataclass
class TabState:
    checked: set[str] = field(default_factory=set)
    #: The Test page's open drawer — "tests", "variables" or None — and
    #: every control of both drawers (3c; step T, Q7): the tab's, so a
    #: switch of test, which builds new drawers, keeps them.
    drawer: str | None = None
    #: The open drawer's panel width in px, dragged by its right edge (K5):
    #: the tab's, so a switch of test keeps it.
    drawer_width: int = 320
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
    #: The tree card's folded groups (U5, Q8 / Y6): model id -> the folded
    #: groups' node paths. The tab's, so a switch of test keeps them.
    tree_folded: dict[str, list[str]] = field(default_factory=dict)


async def connected_tab_state(client: Any) -> TabState | None:
    """This tab's state once ``client`` has a live connection, or None when
    the client was deleted first — every page awaits this before drawing,
    and returns on None: there is no browser to draw for.

    ``Client.connected()`` also returns when the client is DELETED: a plain
    GET (the tests' readiness probe, a curl) makes a client that never
    connects, NiceGUI prunes it a minute later, and `tab_state` raised
    "app.storage.tab can only be used with a client connection" from the
    page's build. And a handshake followed by a disconnect before the build
    resumed (a loop held up past the ping budget) leaves a live client with
    no tab until the browser reconnects: that one is waited for again."""
    while not client.has_socket_connection:
        if client.is_deleted:
            return None
        await client.connected()
    return tab_state()


def tab_state() -> TabState:
    """This tab's state, created on first use. Needs a connected client:
    a page's build asks `connected_tab_state`."""
    store = app.storage.tab
    state = store.get(KEY)
    if not isinstance(state, TabState):
        state = TabState()
        store[KEY] = state
    return state
