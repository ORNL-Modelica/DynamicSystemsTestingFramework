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
    #: The theme this tab switched to (D148; `tab_theme.py`), a bundled
    #: name; None until it switches: the project's. The tab's, so a reload
    #: and the tab's other page keep it and another tab does not get it.
    theme: str | None = None
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
    #: What a panel shows that the spec does not hold (`Panel.kept`): model
    #: id -> (node path, capability name) -> that panel's own dict. The
    #: tab's, as the folds are, and taken away as they are (`heard_spec`):
    #: with the test's entry when an edit that is not this tab's lands on it.
    kept: dict[str, dict[tuple[str, str], dict[Any, Any]]] = field(default_factory=dict)
    #: The `spec` revisions this tab's own acts produced (`TestPage._own`),
    #: kept until a later revision is heard. Any other `spec` event for a
    #: test unfolds its groups (the owner's ruling, U5 H2; `heard_spec`).
    #: The tab's, not a page's: a page navigated away from stays registered
    #: until NiceGUI's reconnect timeout and hears every event, and with a
    #: record of its own it heard the new page's edit as foreign and
    #: cleared the folds.
    own_revisions: set[int] = field(default_factory=set)
    #: The last `spec` revision any page of the tab heard or produced: a
    #: page opening in a tab that had no live page compares it with the
    #: Session's (`caught_up`).
    seen_revision: int = 0


def heard_spec(state: TabState, event: dict[str, Any]) -> None:
    """A `spec` event, heard by a page of the tab — EVERY page, the Suite
    page too (K1): one this tab's own edits did not produce unfolds every
    group of each test it names, of every test when it names none (a
    close): their folds followed only the tab's edits (`TestPage.edited`)
    — a Discard, a Save that reloads, another tab's edit, a spec reload.
    What its panels kept beside the spec (`TabState.kept`) goes the same
    way: it was typed against an entry that event replaced.
    Heard only by a page of that test, a Discard from another test's
    header left its folds on the groups the Discard put back. Every page
    of the tab hears this event and reads the same record, so the event's
    own revision stays in it and only those BELOW it go: the Shell hands
    each event to every page before the next (`Shell.tick`), so every page
    has heard them."""
    revision = event["revision"]
    own_revisions = state.own_revisions
    own = revision in own_revisions
    own_revisions.difference_update([r for r in own_revisions if r < revision])
    state.seen_revision = max(state.seen_revision, revision)
    if own:
        return
    models = event.get("models")
    if not models:
        state.tree_folded.clear()
        state.kept.clear()
    for model in models or ():
        state.tree_folded.pop(model, None)
        state.kept.pop(model, None)


def caught_up(state: TabState, revision: int) -> None:
    """A page of the tab opens with the Session at ``revision`` (K2): a tab
    with no live page heard nothing, so a revision after the last it saw
    that none of its acts produced may have changed any test — every fold
    goes, and what the panels kept, as `heard_spec` would have taken them."""
    missed = set(range(state.seen_revision + 1, revision + 1))
    if missed - state.own_revisions:
        state.tree_folded.clear()
        state.kept.clear()
    state.seen_revision = max(state.seen_revision, revision)


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
