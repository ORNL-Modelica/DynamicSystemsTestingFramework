"""The Test page's rail (the owner's ruling, 2026-09-25; DESIGN.md "The
Variables drawer"): 40 px of labelled buttons down the left edge, and ONE
drawer at a time beside them, 320 px, which pushes the plots narrower
rather than covering them (`behavior=desktop`: below Quasar's 1024 px
breakpoint a drawer otherwise turns into an overlay). Plotly follows the
narrower slot by itself — NiceGUI's plotly element observes its size. Which
drawer is open is the tab's (`TabState.drawer`), so a switch of test keeps
it open."""

from __future__ import annotations

import json
from functools import partial
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import regex
from nicegui import ui

from ...reporting.status import (
    FAIL,
    NO_REF,
    NOT_RUN,
    PASS,
    SCORING_FAILED,
    SIM_FAIL,
    STATUS_CLASSES,
    classify,
)
from ..needle import matcher
from .drawer_controls import TIP_ABOVE, filter_box, menu_button
from .test_list import Row, TestList, _name, _package
from .variables_drawer import VariablesDrawer

if TYPE_CHECKING:
    from .test_page import TestPage

RAIL = 40
#: A dragged panel's bounds (K5): at least ``MIN_PANEL`` px, at most
#: ``MAX_FRACTION`` of the window AND the window less the rail and
#: ``MIN_PLOTS`` px for the page's plots column (at 900 px, 60% left the
#: plots 320 px and a context chip's × past its card). The browser clamps
#: to both, which only it can measure, and sends the window's width with
#: the release; the server holds the same bounds again (`max_panel`).
MIN_PANEL, MAX_FRACTION, MIN_PLOTS = 240, 0.6, 480


def max_panel(window: float) -> float:
    """The widest a dragged panel may be in a ``window`` px wide window —
    `_WIDTH`'s ``max``. The floor wins when a narrow window leaves less."""
    return min(MAX_FRACTION * window, window - RAIL - MIN_PLOTS)


#: The drawer's width in the BROWSER (K5), installed by `Rail.__init__` as
#: `window.__dstfDrawer`: the visible panel (`offsetParent`: both drawers'
#: panels are in the row, one hidden, B10), the clamp to `MIN_PANEL` and
#: `max_panel` — only the browser knows the window's width — and `apply`,
#: which sets the panel's width and the drawer's `width` prop on NiceGUI's
#: element store (`mounted_app.elements`, reactive, so Quasar re-lays the
#: page out and Plotly's observer reflows each plot) while the pointer or a
#: key moves it. `mounted_app.elements[id].props` is NiceGUI 3.16's
#: INTERNAL client-side store, not an API: the lookup is guarded (`store`),
#: so on a NiceGUI that renames it the panel still moves and the release
#: still sends the width — only the drawer lags the panel until the
#: server's update (the Playwright test reading the drawer's width
#: mid-drag fails first). `fit` clamps a width the window no longer holds
#: — the tab's width from a wider window — on load (the width the server
#: has, sent at once) and on a window `resize` (sent once, 150 ms after
#: the last).
_WIDTH = """(() => {
  const RAIL = __RAIL__, MIN = __MIN__;
  const panel = () => [...document.querySelectorAll('.dstf-drawer-panel')]
    .find((p) => p.offsetParent !== null);
  const max = () => Math.min(__MAX__ * window.innerWidth, window.innerWidth - RAIL - __PLOTS__);
  const clamp = (w) => Math.round(Math.max(MIN, Math.min(w, max())));
  const width = (p) => Math.round(p.getBoundingClientRect().width);
  // `mounted_app` is a top-level `let` of nicegui.js — a global binding,
  // no property of `window` — hence the try.
  const store = () => {
    try { return mounted_app?.elements; } catch { return undefined; }
  };
  const bounds = () => document.getElementById('__GRIP__')
    ?.setAttribute('aria-valuemax', Math.round(Math.max(MIN, max())));
  const apply = (p, w) => {
    p.style.width = w + 'px';
    const drawer = store()?.[__DRAWER__];
    if (drawer) drawer.props.width = RAIL + w;
    document.getElementById('__GRIP__')?.setAttribute('aria-valuenow', w);
    bounds();
  };
  const send = (w) => emitEvent('dstf_drawer_width', w, window.innerWidth);
  let timer = null;
  const fit = (known) => {
    const p = panel();
    const w = known ?? (p ? width(p) : null);
    bounds();
    if (w === null) return;
    const c = clamp(w);
    if (c === w) return;
    if (p) apply(p, c);
    clearTimeout(timer);
    if (known !== undefined) send(c);
    else timer = setTimeout(() => send(c), 150);
  };
  window.__dstfDrawer = {panel, max, clamp, width, apply, fit};
  if (!window.__dstfDrawerResize) {
    window.__dstfDrawerResize = true;
    window.addEventListener('resize', () => window.__dstfDrawer?.fit());
  }
})();"""

#: The grip's drag: the panel follows the pointer (`apply`), and the
#: release sends the width to Python once, which sets the same values and
#: keeps them in `TabState`.
_DRAG = """(e) => {
  if (e.button !== 0) return;
  const d = window.__dstfDrawer, panel = d?.panel();
  if (!panel) return;
  e.preventDefault();
  const handle = e.currentTarget;
  const x0 = e.clientX, w0 = panel.getBoundingClientRect().width;
  let w = w0;
  handle.setPointerCapture(e.pointerId);
  const move = (m) => {
    w = d.clamp(w0 + m.clientX - x0);
    d.apply(panel, w);
  };
  const up = () => {
    handle.removeEventListener('pointermove', move);
    handle.removeEventListener('pointerup', up);
    emit(w, window.innerWidth);
  };
  handle.addEventListener('pointermove', move);
  handle.addEventListener('pointerup', up);
}"""

#: The grip from the keyboard (a focusable `separator`, WAI-ARIA's window
#: splitter): ←/→ 16 px, with Shift 64, Home the narrowest, End the
#: widest; the panel follows each key, and the key's release sends the
#: width once — a held → sends one width, not one per repeat. A key moves
#: the grip's `dirty` mark and a send clears it; leaving the grip sends a
#: width still marked: a key held while the focus moves (→ held, Tab) is
#: released on another element, and the width it left never reached the
#: server.
_STEP, _BIG_STEP = 16, 64
_GRIP_KEYS = ("ArrowLeft", "ArrowRight", "Home", "End")
_KEYDOWN = """(e) => {
  const d = window.__dstfDrawer, panel = d?.panel();
  if (!panel) return;
  const w = d.width(panel), step = e.shiftKey ? __BIG__ : __STEP__;
  const to = {ArrowLeft: w - step, ArrowRight: w + step, Home: __MIN__, End: d.max()}[e.key];
  if (to === undefined) return;
  e.preventDefault();
  d.apply(panel, d.clamp(to));
  e.currentTarget.dataset.dirty = '1';
}"""
_KEYUP = """(e) => {
  if (!__KEYS__.includes(e.key)) return;
  const d = window.__dstfDrawer, panel = d?.panel();
  delete e.currentTarget.dataset.dirty;
  if (panel) emit(d.width(panel), window.innerWidth);
}"""
_BLUR = """(e) => {
  if (!e.currentTarget.dataset.dirty) return;
  delete e.currentTarget.dataset.dirty;
  const d = window.__dstfDrawer, panel = d?.panel();
  if (panel) emit(d.width(panel), window.innerWidth);
}"""


EDITED = "EDITED"
#: The Tests drawer's status pills, in the Suite's order, and EDITED.
PILLS = (PASS, FAIL, SIM_FAIL, NO_REF, NOT_RUN, EDITED)
#: Each pill's `.dstf-pill-<status>` tokens; EDITED reads as the Suite's
#: Edits cell and the rows' ``N unsaved`` pill do.
PILL_CSS = {**{s: STATUS_CLASSES[s] for s in PILLS if s != EDITED}, EDITED: "warn"}
#: "Status, failures first": FAIL, SIM_FAIL, scoring failed, NO_REF, PASS,
#: NOT RUN (no result, D145), then no verdict yet — NOT `STATUS_ORDER`,
#: which lists PASS first.
FAILURES_FIRST = (FAIL, SIM_FAIL, SCORING_FAILED, NO_REF, PASS, NOT_RUN, "")
SORTS = (
    ("package", "Package order"),
    ("name", "Name A→Z"),
    ("status", "Status, failures first"),
)
NAMES = (("short", "Short"), ("full", "Full"))


class TestsDrawer:
    """The Suite page's list, without checkboxes, and the drawers' one
    control set (the owner's ruling Q7): the filter with `.*` / `Aa`, sort,
    group and names, and compact status pills with EDITED — every control
    the tab's (`TabState`), so a switch of test keeps them. This test is
    highlighted; a click opens that test in this tab, and pending edits are
    the Session's and survive the switch."""

    def __init__(self, page: TestPage, panel: ui.column) -> None:
        self.page = page
        state = page.state
        #: The filter's kept ids and its sentence, and what they were
        #: computed from (`_kept`).
        self.kept: tuple[object, set[str], str | None] | None = None
        with panel:
            ui.label("Tests").classes("text-subtitle2")
            self.box = filter_box(
                state.tests_needle,
                state.tests_regex,
                state.tests_case,
                self._on_filter,
                "filter tests",
                "tests-drawer:",
            )
            with ui.row().classes("items-center no-wrap gap-0 w-full"):
                self.sort = menu_button(
                    "sort",
                    "Sort",
                    SORTS,
                    state.tests_sort,
                    self._on_sort,
                    "tests-drawer:sort",
                )
                self.group = (
                    ui.button(icon="account_tree", on_click=self._on_group)
                    .props("flat dense round")
                    .mark("tests-drawer:group")
                )
                self.group.props["aria-label"] = "Group by package"
                with self.group:
                    self.group_tip = ui.tooltip("").props(TIP_ABOVE)
                self.names = menu_button(
                    "label",
                    "Names",
                    NAMES,
                    "short" if state.tests_short else "full",
                    self._on_names,
                    "tests-drawer:names",
                )
            with ui.row().classes("items-center gap-1 w-full"):
                self.pills: dict[str, ui.button] = {}
                for word in PILLS:
                    self.pills[word] = (
                        ui.button(on_click=partial(self._on_pill, word))
                        .props("flat dense no-caps")
                        .classes(
                            f"dstf-count-pill dstf-pill dstf-pill-{PILL_CSS[word]}"
                        )
                        # One word: NiceGUI splits a marker on whitespace,
                        # and "NOT RUN" has a space.
                        .mark(f"tests-drawer:pill:{word.replace(' ', '_')}")
                    )
            self.list = TestList(
                page.shell,
                on_open=self._open,
                current=page.model_id,
                marker="tests-drawer:",
            )
        self._paint_group()
        self._rebuild()

    # -- the controls ------------------------------------------------------------

    def _on_filter(self, needle: str, regex: bool, case: bool) -> None:
        state = self.page.state
        state.tests_needle, state.tests_regex, state.tests_case = needle, regex, case
        self._filter()

    def _on_sort(self, value: str) -> None:
        self.page.state.tests_sort = value
        self._rebuild()

    def _on_group(self) -> None:
        state = self.page.state
        state.tests_grouped = not state.tests_grouped
        self._paint_group()
        self._rebuild()

    def _paint_group(self) -> None:
        """Pressed while grouped; the tooltip says what a click does."""
        grouped = self.page.state.tests_grouped
        self.group.props(f'aria-pressed="{str(grouped).lower()}"')
        self.group_tip.set_text("Show flat" if grouped else "Group by package")

    def _on_names(self, value: str) -> None:
        self.page.state.tests_short = value == "short"
        self._rebuild()

    def _on_pill(self, word: str) -> None:
        self.page.state.tests_pills.symmetric_difference_update({word})
        self._filter()

    # -- the rows ----------------------------------------------------------------

    def _ids(self) -> list[str]:
        """The tests in the chosen order; package order is the shell's."""
        ids = self.page.shell.visible_tests()
        state = self.page.state
        if state.tests_sort == "name":
            return sorted(
                ids, key=lambda m: (_name(m, state.tests_short).casefold(), m)
            )
        if state.tests_sort == "status":
            position = {m: i for i, m in enumerate(ids)}
            return sorted(ids, key=lambda m: (_rank(self._verdict(m)), position[m]))
        return ids

    def _verdict(self, model_id: str) -> str:
        """What the row's badge says (`TestList.paint`), read before the
        rows exist: the order is decided first."""
        comp = self.page.shell.session.comparisons.get(model_id)
        if comp is not None:
            return classify(comp)
        return SCORING_FAILED if model_id in self.page.shell.load.failed_models else ""

    def _drawn(self) -> list[str]:
        """The order the rows are drawn in now: `_ids`, by package first
        when grouped (`TestList.rebuild`)."""
        ids = self._ids()
        if not self.page.state.tests_grouped:
            return ids
        position = {m: i for i, m in enumerate(ids)}
        return sorted(ids, key=lambda m: (_package(m), position[m]))

    def _rebuild(self) -> None:
        """The rows from scratch in the chosen order and grouping, and the
        tab's filter APPLIED to them: a drawer built on the next test's page
        showed the filter in its box over every row until some event
        happened to apply it."""
        state = self.page.state
        self.list.rebuild(
            self._ids(), grouped=state.tests_grouped, short=state.tests_short
        )
        self._filter()

    def _open(self, model_id: str) -> None:
        ui.navigate.to(f"/test/{quote(model_id, safe='')}")

    def _kept(self) -> tuple[set[str], str | None]:
        """The ids the filter keeps, and the sentence under the box — every
        id, and why, for a bad pattern or one that ran out of time: the
        drawer hides nothing then. Cached on the needle, its toggles and
        the SET of ids, never their order: events repaint the rows every
        second during a job, a rescore under the status sort reorders them,
        and a pathological pattern cost 0.79 s of CPU per pass over 331 ids
        (the plan's executing review)."""
        state = self.page.state
        ids = frozenset(self.list.rows)
        key = (state.tests_needle, state.tests_regex, state.tests_case, ids)
        if self.kept is None or self.kept[0] != key:
            why: str | None = None
            try:
                match = matcher(
                    state.tests_needle, regex=state.tests_regex, case=state.tests_case
                )
                kept = {m for m in ids if match(m)}
            except regex.error as e:
                kept, why = set(ids), f"not a regular expression: {e}"
            except TimeoutError:
                kept, why = set(ids), "the pattern took too long; simplify it"
            self.kept = (key, kept, why)
        return self.kept[1], self.kept[2]

    def _filter(self) -> None:
        """The filter and the pills over the rows; the pills count every
        row, whatever the filter. The statuses pressed are OR'd, and EDITED
        with them — the Suite's rule (`suite_grid.js`). A test the open
        could not score is SIM_FAIL to the pills, counted and kept, as the
        Suite's row says it (`suite_rows`); the status sort keeps it a
        place of its own (`FAILURES_FIRST`)."""
        state = self.page.state
        counts = dict.fromkeys(PILLS, 0)
        for row in self.list.rows.values():
            pill = _pill_word(row)
            if pill in counts:
                counts[pill] += 1
            if row.pending:
                counts[EDITED] += 1
        for word, button in self.pills.items():
            on = word in state.tests_pills
            button.set_text(f"{word} {counts[word]}")
            button.props(f'aria-pressed="{str(on).lower()}"')
            if on:
                button.classes(add="dstf-count-pill-on")
            else:
                button.classes(remove="dstf-count-pill-on")
            # One switched on stays enabled at 0, so it can be switched off.
            button.set_enabled(counts[word] > 0 or on)
        kept, why = self._kept()
        self.box.say(why)
        pills = state.tests_pills

        def keep(row: Row) -> bool:
            if row.model_id not in kept:
                return False
            if not pills:
                return True
            return _pill_word(row) in pills or (EDITED in pills and row.pending > 0)

        self.list.filter(keep)

    def handle(self, event: dict[str, Any]) -> None:
        """The Suite page's rules for its rows (`SuitePage.handle`): a `spec`
        that moved the test set rebuilds them, and one that did not repaints
        the rows whose edits it moved; a scored model repaints its own row.
        Under the status sort, a verdict that moved the order rebuilds
        them."""
        kind = event["event"]
        if kind == "spec":
            if set(self.list.rows) != set(self.page.shell.visible_tests()):
                self._rebuild()
                return
            for model_id in event.get("models") or list(self.list.rows):
                self.list.paint(str(model_id))
        elif kind == "loading":
            if event.get("phase") == "results":
                if not self.list.rows:
                    self._rebuild()  # a drawer opened before the spec event
                    return
                self.list.paint(str(event["model"]))
        elif kind == "results":
            for model_id in event.get("models", ()):
                self.list.paint(str(model_id))
        if self.page.state.tests_sort == "status" and list(self.list.rows) != (
            self._drawn()
        ):
            self._rebuild()
            return
        self._filter()


def _pill_word(row: Row) -> str:
    """The status pill a row counts under: its verdict, SIM_FAIL for
    "scoring failed"."""
    return SIM_FAIL if row.verdict == SCORING_FAILED else row.verdict


def _rank(verdict: str) -> int:
    """Where "Status, failures first" puts a verdict: a word it does not
    list goes last."""
    return (
        FAILURES_FIRST.index(verdict)
        if verdict in FAILURES_FIRST
        else len(FAILURES_FIRST)
    )


class Rail:
    """The rail and its drawers' panels. A drawer's content is built the
    first time it opens."""

    def __init__(self, page: TestPage) -> None:
        self.page = page
        self.tests: TestsDrawer | None = None
        self.variables: VariablesDrawer | None = None
        self.buttons: dict[str, ui.button] = {}
        self.panels: dict[str, ui.column] = {}
        self.drawer = (
            ui.left_drawer(value=True)
            .props(f"width={RAIL} behavior=desktop bordered")
            .classes("dstf-rail-drawer")
        )
        with (
            self.drawer,
            ui.row().classes("dstf-rail-row no-wrap gap-0 items-stretch w-full h-full"),
        ):
            with ui.column().classes("dstf-rail gap-1 items-center"):
                for kind, icon, text, tip in (
                    ("tests", "format_list_bulleted", "Tests", None),
                    ("variables", "data_array", "Vars", "Variables"),
                ):
                    with (
                        ui.button(on_click=lambda k=kind: self.toggle(k))
                        .props("flat dense no-caps stack")
                        .mark(f"rail:{kind}") as button
                    ):
                        ui.icon(icon)
                        ui.label(text).classes("dstf-rail-label")
                        if tip is not None:  # "Vars" is short for it
                            ui.tooltip(tip)
                    self.buttons[kind] = button
            for kind in ("tests", "variables"):
                panel = (
                    ui.column()
                    .classes("dstf-drawer-panel q-pa-sm gap-2")
                    .mark(f"drawer:{kind}")
                )
                panel.set_visibility(False)
                self.panels[kind] = panel
            self.grip = (
                ui.element("div")
                .classes("dstf-drawer-handle")
                .mark("drawer-handle")
                .on(
                    "pointerdown",
                    lambda e: self.resize(*e.args),
                    js_handler=_DRAG,
                )
                .on(
                    "keydown",
                    js_handler=_KEYDOWN.replace("__MIN__", str(MIN_PANEL))
                    .replace("__STEP__", str(_STEP))
                    .replace("__BIG__", str(_BIG_STEP)),
                )
                .on(
                    "keyup",
                    lambda e: self.resize(*e.args),
                    js_handler=_KEYUP.replace("__KEYS__", json.dumps(_GRIP_KEYS)),
                )
                .on("blur", lambda e: self.resize(*e.args), js_handler=_BLUR)
            )
            # A window splitter (WAI-ARIA): focusable, its value the panel's
            # width — the most is the browser's (`_WIDTH`'s `bounds`).
            grip = self.grip.props
            grip["role"] = "separator"
            grip["aria-orientation"] = "vertical"
            grip["aria-label"] = "Resize the drawer"
            grip["tabindex"] = "0"
            grip["aria-valuemin"] = str(MIN_PANEL)
            self.grip.set_visibility(False)
        ui.on("dstf_drawer_width", lambda e: self.resize(*e.args))
        ui.run_javascript(
            _WIDTH.replace("__DRAWER__", str(self.drawer.id))
            .replace("__GRIP__", self.grip.html_id)
            .replace("__MIN__", str(MIN_PANEL))
            .replace("__MAX__", str(MAX_FRACTION))
            .replace("__PLOTS__", str(MIN_PLOTS))
            .replace("__RAIL__", str(RAIL))
        )

    def open(self, kind: str | None) -> None:
        """Show ``kind``'s panel and hide the other; None closes both. The
        drawer widens to hold the panel, which is what narrows the plots.
        The Variables drawer is built again only when it was not the one
        open: an open one follows the page by `refresh`, and Add series
        (a select, then this) built it twice."""
        was = self.page.state.drawer
        for name, panel in self.panels.items():
            panel.set_visibility(name == kind)
        for name, button in self.buttons.items():
            button.classes(remove="dstf-rail-on")
            if name == kind:
                button.classes(add="dstf-rail-on")
        self.page.state.drawer = kind
        self._width(self.page.state.drawer_width)
        if kind == "tests" and self.tests is None:
            self.tests = TestsDrawer(self.page, self.panels["tests"])
        elif kind == "variables":
            if self.variables is None:
                self.variables = VariablesDrawer(self.page, self.panels["variables"])
            elif was != "variables":
                self.variables.rebuild()  # it was not followed while closed

    def _width(self, width: int) -> None:
        """The drawer's `width` prop and the panels' width: RAIL alone with
        no drawer open."""
        kind = self.page.state.drawer
        self.drawer.props(f"width={RAIL + width if kind else RAIL}")
        for panel in self.panels.values():
            panel.style(f"width: {width}px")
        self.grip.props["aria-valuenow"] = str(width)
        self.grip.set_visibility(kind is not None)
        self.grip.update()
        if kind is not None:
            # The tab's width may be one a wider window held: the browser
            # clamps it to this one's and sends it back (`fit`).
            self.page.client.run_javascript(f"window.__dstfDrawer?.fit({width})")

    def resize(self, width: Any, window: Any) -> None:
        """A drag's release (`_DRAG`): the width the browser clamped and the
        window's width, held to the same bounds again here (`max_panel`,
        then the floor), kept in `TabState`."""
        try:
            px = int(round(min(float(width), max_panel(float(window)))))
        except (TypeError, ValueError, OverflowError):
            return
        px = max(MIN_PANEL, px)
        self.page.state.drawer_width = px
        self._width(px)

    def toggle(self, kind: str) -> None:
        self.open(None if self.page.state.drawer == kind else kind)

    def refresh(self) -> None:
        """The open Variables drawer follows the page — the plot selected,
        the layout staged, the result read — and builds again only when one
        of those moved (`VariablesDrawer.refresh`). The ONE way the page's
        changes reach it: `TestPage.select` and the end of `TestPage.sync`
        call this. A closed one is left alone; it is rebuilt when it opens."""
        if self.page.state.drawer == "variables" and self.variables is not None:
            self.variables.refresh()

    def handle(self, event: dict[str, Any]) -> None:
        if self.tests is not None:
            self.tests.handle(event)
