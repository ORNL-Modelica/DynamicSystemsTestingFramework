"""A capability's panel alone, in NiceGUI's user simulation with no shell:
the view is built for one `Selection`, the panel's `submit` runs `act` and
hands the outcome to `Session.apply`, and an exception lands in `errors`
under the capability's name. What the old shell added around a panel — the
rebuild on `results`, the leaf's config error above it, the error cleared
in place — returns with the Test page (roadmap §3, §4) and is not emulated
here. Tests import this as `from capability_host import host`."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from nicegui import ui
from nicegui.testing.user_simulation import User, user_simulation

from dstf.authoring.session import Selection, Session, SessionView


class RecordingFigure:
    """`Panel.figure` for the host: records subscriptions so a test can
    dispatch to them by hand; the shell's plot does the real wiring."""

    def __init__(self) -> None:
        self.handlers: list[tuple[str, Callable[[str, dict], None]]] = []

    def on(self, kind: str, handler: Callable[[str, dict], None]) -> None:
        self.handlers.append((kind, handler))


class HostPanel:
    def __init__(self, submit: Callable[..., None], figure: RecordingFigure) -> None:
        self.figure = figure
        self._submit = submit

    def submit(self, **inputs: Any) -> None:
        self._submit(**inputs)


@asynccontextmanager
async def host(
    session: Session, cap: Any, selection: Selection
) -> AsyncIterator[tuple[User, dict[str, str], RecordingFigure]]:
    """Yield ``(user, errors, figure)`` with the panel built at ``/`` — or,
    when ``available`` is not True, a label marked ``reason`` in its place."""
    errors: dict[str, str] = {}
    figure = RecordingFigure()
    view = SessionView(session, selection)

    def submit(**inputs: Any) -> None:
        try:
            session.apply(cap.act(view, **inputs))
            errors.pop(cap.name, None)
        except Exception as e:  # a shell shows this; the host records it
            errors[cap.name] = f"{type(e).__name__}: {e}"

    def root() -> None:
        avail = cap.available(view)
        if avail is not True:
            ui.label(avail if isinstance(avail, str) else "not available").mark(
                "reason"
            )
            return
        cap.panel(view, HostPanel(submit, figure))

    async with user_simulation(root=root) as user:
        await user.open("/")
        yield user, errors, figure


def _ancestors(element: Any) -> Any:
    while element.parent_slot is not None:
        element = element.parent_slot.parent
        yield element


def open_menu(user: User, marker: str, within: str | None = None) -> None:
    """Open the menu anchored to the icon button marked ``marker``, the way
    a browser does: whatever opens a QMenu reaches the server as the menu's
    own value change, and that is what builds its content. A click under
    the user simulation moves no value, so it opens nothing. ``within``
    scopes the search to the host carrying that marker, as `_click_in`
    does: a tree row and a criteria row carry the same node's controls."""
    elements = user.find(marker=marker).elements
    if within is not None:
        (host,) = user.find(marker=within).elements
        elements = {e for e in elements if host in list(_ancestors(e))}
    (button,) = elements
    menu = next(e for e in button.descendants() if isinstance(e, ui.menu))
    menu.open()
