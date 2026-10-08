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
        #: The panel drawn beside this figure, once `host` has built it.
        self.panel: HostPanel | None = None

    def on(self, kind: str, handler: Callable[[str, dict], None]) -> None:
        self.handlers.append((kind, handler))


class HostPanel:
    """`Panel` for the host: ``row`` draws the form inline, in a column of
    its own below the panel (marked ``host-row``, its title
    ``host-row:title``), and ``close`` empties it; ``lead`` draws into a
    span of its own above the panel (marked ``host-lead``), emptied first;
    ``reveal`` records the path in ``reveals`` — there is no page to open
    it on — ``moved`` what it was told in ``moves``, and ``edited`` the
    path maps it was told in ``edits``. ``on_click`` keeps the row click a
    panel registered (U5) in ``click``; `host` then draws a button marked
    ``host-click`` that calls it, standing for the row. ``kept`` is the
    test's dict when it hands one — to a second host too, as a shell hands
    the same one to a rebuilt panel — else this panel's own. A plot's side
    of the panel (D149) is recorded: the ⌖ a panel drew in ``places``
    (pointer, label), its place handler in ``place``, and what it previewed
    in ``previews`` — handed on to every `on_preview` handler, as a shell
    hands it to every panel of the editor."""

    def __init__(
        self,
        submit: Callable[..., bool],
        figure: RecordingFigure,
        reveals: list[str] | None = None,
        edits: list[dict[str, str]] | None = None,
        kept: dict | None = None,
    ) -> None:
        self.figure = figure
        self.kept: dict = {} if kept is None else kept
        self._submit = submit
        self.reveals = reveals if reveals is not None else []
        self.edits = edits if edits is not None else []
        #: Set by `host` once the panel is drawn, so the row sits below it.
        self.slot: ui.column | None = None
        self.lead_slot: ui.element | None = None
        self.closed = 0
        #: What `moved` was told: (node path, text, paths), in order.
        self.moves: list[tuple[str, str, dict[str, str]]] = []
        #: What `on_click` registered: the row's click, or None.
        self.click: Callable[[], None] | None = None
        self.places: list[tuple[Any, str]] = []
        self.place: Callable[[Any, dict], bool] | None = None
        self.previews: list[tuple[dict | None, int]] = []
        self.previewers: list[Callable[[dict | None, int], None]] = []

    def place_button(self, pointer: Any, label: str) -> None:
        self.places.append((pointer, label))

    def on_place(self, handler: Callable[[Any, dict], bool]) -> None:
        self.place = handler

    def preview(self, leaf: dict | None, revision: int = 0) -> None:
        self.previews.append((leaf, revision))
        for handler in self.previewers:
            handler(leaf, revision)

    def on_preview(self, handler: Callable[[dict | None, int], None]) -> None:
        self.previewers.append(handler)

    def submit(self, **inputs: Any) -> bool:
        return self._submit(**inputs)

    def row(self, title: str, build: Callable[[Callable[[], None]], None]) -> None:
        slot = self.slot
        assert slot is not None

        def close() -> None:
            self.closed += 1
            slot.clear()

        slot.clear()
        with slot:
            ui.label(title).mark("host-row:title")
            build(close)

    def lead(self, build: Callable[[], None]) -> None:
        if self.lead_slot is None:
            self.lead_slot = ui.element("span").mark("host-lead")
        self.lead_slot.clear()
        with self.lead_slot:
            build()

    def reveal(self, leaf_path: str) -> None:
        self.reveals.append(leaf_path)

    #: No tree card here: the host offers no moving (U4).
    can_move = False

    def movable(self, on_drop: Any, zones: Any = None) -> bool:
        return False

    def moved(self, node_path: str, text: str, paths: dict[str, str]) -> None:
        self.moves.append((node_path, text, paths))

    def edited(self, paths: dict[str, str]) -> None:
        self.edits.append(paths)

    def on_click(self, handler: Callable[[], None]) -> bool:
        self.click = handler
        return True


@asynccontextmanager
async def host(
    session: Session,
    cap: Any,
    selection: Selection,
    reveals: list[str] | None = None,
    edits: list[dict[str, str]] | None = None,
    kept: dict | None = None,
) -> AsyncIterator[tuple[User, dict[str, str], RecordingFigure]]:
    """Yield ``(user, errors, figure)`` with the panel built at ``/`` — or,
    when ``available`` is not True, a label marked ``reason`` in its place.
    The panel's ``reveal`` appends to ``reveals`` when one is given, and
    its ``edited`` to ``edits``; ``kept`` is its `Panel.kept`."""
    errors: dict[str, str] = {}
    figure = RecordingFigure()
    view = SessionView(session, selection)

    def submit(**inputs: Any) -> bool:
        try:
            session.apply(cap.act(view, **inputs))
            errors.pop(cap.name, None)
        except Exception as e:  # a shell shows this; the host records it
            errors[cap.name] = f"{type(e).__name__}: {e}"
            return False
        return True

    def root() -> None:
        avail = cap.available(view)
        if avail is not True:
            ui.label(avail if isinstance(avail, str) else "not available").mark(
                "reason"
            )
            return
        panel = HostPanel(submit, figure, reveals, edits, kept)
        figure.panel = panel
        cap.panel(view, panel)
        if panel.click is not None:
            # The row the panel would be drawn in: its click.
            ui.button("row", on_click=panel.click).mark("host-click")
        panel.slot = ui.column().classes("w-full").mark("host-row")

    async with user_simulation(root=root) as user:
        await user.open("/")
        yield user, errors, figure
