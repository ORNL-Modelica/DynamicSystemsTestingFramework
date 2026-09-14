"""The authoring shell (§4.6 "The shell", §4.7): fixed, and it knows no
capability by name. It reads a SessionView, lists capabilities by scope,
and routes every outcome through Session.apply. Everything it draws comes
from the Session: the figure dict, the report's tree view, the verdicts.
"""

from __future__ import annotations

import json
import math
import queue
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi.responses import FileResponse, JSONResponse
from nicegui import app, ui

from ..backends.progress import read_status
from ..cli.common import _filter_tests
from ..reporting.status import classify_with_class
from ..storage.ref_index import _atomic_write_text
from .capabilities import (
    Capability,
    Scope,
    registered_capabilities,
)
from .capabilities import FigureHook as FigureHookProtocol
from .session import (
    JobRunningError,
    PendingEditsError,
    RunningJob,
    Session,
    SessionView,
    StaleSpecError,
)


def _find_node(view: dict | None, path: str) -> dict | None:
    if view is None:
        return None
    if view.get("path") == path:
        return view
    for child in view.get("children") or []:
        found = _find_node(child, path)
        if found is not None:
            return found
    return None


# The classifier's CSS class names, as the NiceGUI palette.
_BADGE_COLOR = {
    "pass": "positive",
    "fail": "negative",
    "warn": "warning",
    "sim-fail": "negative",
    "no-ref": "grey",
}


# The index is bounded so a hostile key cannot reach `int()`'s digit limit;
# `[0-9]`, not `\d` (which matches any Unicode digit, and `int()` accepts
# those), and `\Z`, not `$` (which also matches before a trailing newline).
_SHAPE_KEY = re.compile(r"^shapes\[([0-9]{1,9})\]\.(.+)\Z")


class FigureHook:
    """``panel.figure.on(kind, handler)``: handlers get a primitive id and a
    payload. Wired to the centre plot's Plotly events. The payload contract
    (the spike's, over the payloads plotly.js actually sends):

    - ``plotly_click`` / ``plotly_selected``: the id of the first point whose
      trace ``meta.kind`` is ``"handle"``, else of the first point —
      ``customdata``, then ``meta.id``, then ``curveNumber`` — with the event
      payload. Under the figure's ``hovermode: "x unified"`` a click lists
      every trace at that x, so a handle is not necessarily ``points[0]``.
    - ``plotly_relayout`` with keys matching ``shapes[N].<key>``: dispatched
      ONCE PER SHAPE INDEX, ascending. The payload never names the shape;
      the hook resolves the id from the rendered figure's
      ``layout.shapes[N].name`` (``""`` when that lookup fails) and hands the
      handler that shape's keys with the ``shapes[N].`` prefix stripped —
      ``{"y0": 6.0, "y1": 6.0}``, ``{"xanchor": 4.0, "yanchor": 2.99}``, or a
      ring's pixel resize ``{"x0": -7, ..., "y1": 7}``. Positions arrive in
      data coordinates. The hook does not interpret them: the capability does,
      from the handle's primitive type (a Marker's ring moves only by
      ``xanchor``/``yanchor``). ``on_shape_moved`` is then called once, in a
      ``finally``: a raising handler still gets its snap-back. A relayout
      mixing shape keys with others (a zoom) is a shape relayout; the other
      keys are dropped.
    - Any other relayout (a zoom): dispatched once with id ``""`` and the raw
      payload (``{}`` when it is not a dict); ``on_shape_moved`` is not called.

    Handler semantics: each shape index is dispatched in turn, ascending,
    from its own snapshot of the handlers, taken when that shape's dispatch
    starts. A handler registered since the snapshot (a panel rebuilt by the
    act it triggered) is not called for that shape; one removed since the
    snapshot (``clear``) is skipped, checked by identity against the current
    list, so a rebuilt panel's predecessor never runs. A handler registered
    twice is called twice. A handler that raises ENDS the relayout: later
    handlers and later shapes are not dispatched, the exception propagates,
    and ``on_shape_moved`` still runs. A malformed payload never makes
    dispatch raise."""

    def __init__(self, on_shape_moved: Callable[[], None] | None = None) -> None:
        self._on_shape_moved = on_shape_moved
        self._handlers: dict[str, list[tuple[str, Callable[[str, dict], None]]]] = {}
        self._plot: Any = None
        self.dispatching = False  # true while handlers run

    def clear(self, owner: str | None = None) -> None:
        """Remove one owner's handlers, or every handler. The shell rebuilds
        panels on selection and job events (the whole tabs region) and on
        results events (slot by slot); a subscriber that re-registers in
        `panel()` must not accumulate."""
        if owner is None:
            self._handlers = {}
            return
        # `_call` skips a handler removed mid-dispatch by IDENTITY against
        # the current list, so the entries left here must be the SAME tuple
        # objects — rebuilding them (`(o, h)` anew) would make every
        # surviving handler look removed and skip it.
        self._handlers = {
            kind: [entry for entry in entries if entry[0] != owner]
            for kind, entries in self._handlers.items()
        }

    def on(
        self, kind: str, handler: Callable[[str, dict], None], owner: str = ""
    ) -> None:
        self._handlers.setdefault(kind, []).append((owner, handler))

    def owned(self, owner: str) -> _OwnedFigureHook:
        """The view of this hook a capability's panel holds: its `on`
        registers under ``owner``, so `clear(owner)` removes exactly those."""
        return _OwnedFigureHook(self, owner)

    def attach(self, plot: ui.plotly) -> None:
        self._plot = plot
        for event in ("plotly_click", "plotly_relayout", "plotly_selected"):
            plot.on(event, lambda e, kind=event: self._dispatch(kind, e.args))

    def _shape_name(self, index: int) -> str:
        # A known race: the index is resolved against the figure the SERVER
        # last sent. A drag released after an in-place `update_figure` that
        # changed the shape list, but resolved before the browser has applied
        # that update and the relayout has flown back, can resolve to the
        # wrong name. The window runs from `update_figure` through both
        # websocket legs. The snap-back redraw corrects only the picture: once
        # edits are staged from drags, a wrongly resolved handle's edit stays
        # staged.
        figure = getattr(self._plot, "figure", None)
        layout = figure.get("layout") if isinstance(figure, dict) else None
        shapes = layout.get("shapes") if isinstance(layout, dict) else None
        if not isinstance(shapes, list) or index >= len(shapes):
            return ""
        shape = shapes[index]
        name = shape.get("name") if isinstance(shape, dict) else None
        return name if isinstance(name, str) else ""

    def _dispatch(self, kind: str, payload: Any) -> None:
        data = payload if isinstance(payload, dict) else {}
        if kind == "plotly_relayout":
            by_shape: dict[int, dict] = {}
            for key, value in data.items():
                match = _SHAPE_KEY.match(key) if isinstance(key, str) else None
                if match is not None:
                    by_shape.setdefault(int(match.group(1)), {})[match.group(2)] = value
            if not by_shape:
                self._call(kind, "", data)
                return
            try:
                for i in sorted(by_shape):
                    self._call(kind, self._shape_name(i), by_shape[i])
            finally:
                if self._on_shape_moved is not None:
                    self._on_shape_moved()
            return
        points = data.get("points")
        points = (
            [p for p in points if isinstance(p, dict)]
            if isinstance(points, list)
            else []
        )
        chosen = next(
            (p for p in points if _meta(p).get("kind") == "handle"),
            points[0] if points else {},
        )
        # The figure layer stamps every trace's primitive id in `meta.id`
        # (figures/plotly.py); marker groups carry per-point ids in
        # `customdata`. The trace index is the last resort.
        primitive = chosen.get("customdata")
        if primitive is None:
            primitive = _meta(chosen).get("id")
        if primitive is None:
            primitive = chosen.get("curveNumber")
        self._call(kind, "" if primitive is None else str(primitive), data)

    def _call(self, kind: str, primitive: str, payload: dict) -> None:
        """One event: the snapshot is taken here, and each entry is checked
        by identity against the current list just before it is called."""
        snapshot = list(self._handlers.get(kind, []))
        was, self.dispatching = self.dispatching, True
        try:
            for entry in snapshot:
                if not any(e is entry for e in self._handlers.get(kind, [])):
                    continue  # removed since the snapshot
                entry[1](primitive, payload)
        finally:
            self.dispatching = was


def _meta(point: dict) -> dict:
    trace = point.get("data")
    meta = trace.get("meta") if isinstance(trace, dict) else None
    return meta if isinstance(meta, dict) else {}


class _OwnedFigureHook:
    """`FigureHook.owned(owner)`: satisfies the capability-side protocol."""

    def __init__(self, hook: FigureHook, owner: str) -> None:
        self._hook, self._owner = hook, owner

    def on(self, kind: str, handler: Callable[[str, dict], None]) -> None:
        self._hook.on(kind, handler, owner=self._owner)


class ShellPanel:
    """The container a capability owns (§4.6 "Panel"): the current NiceGUI
    slot, the figure hook, and ``submit`` — the one way an outcome reaches
    the Session."""

    figure: FigureHookProtocol

    def __init__(self, shell: Shell, cap: Capability, figure: FigureHookProtocol):
        self._shell, self._cap, self.figure = shell, cap, figure

    def submit(self, **inputs: Any) -> None:
        self._shell.act(self._cap, **inputs)


class Shell:
    """One per process, one browser tab: the v1 shape (two tabs already share
    the drawer element). ``root`` builds the page; every region is a
    refreshable method the Session's events refresh. The in-place redraw and
    the figure hook follow the last-built plot, so a second tab's centre does
    not redraw in place."""

    def __init__(
        self, *, filter: str | None = None, package: str | None = None
    ) -> None:
        self._posted: queue.Queue[Callable[[], None]] = queue.Queue()
        self.session = Session(post=self._posted.put)
        self.view = SessionView(self.session)
        self.filter, self.package = filter, package
        self.errors: dict[str, str] = {}  # capability name -> last error in its tab
        self.figure_hook = FigureHook(on_shape_moved=self._mark_figure_dirty)
        self._built = False
        self.needle = ""  # the test list's filter text; survives refreshes
        self._routes_added = False
        # The centre's plot element, the `_centre_key` it was built for, and
        # the in-place redraw state `_follow_centre` / `_tick` share. One tab
        # (the class docstring): the in-place redraw and the figure hook
        # follow the last-built plot; a second tab's centre does not redraw
        # in place.
        self._plot: ui.plotly | None = None
        self._plot_key: tuple[str, str, tuple[str, ...]] | None = None
        self._figure_dirty = False
        self._figure_rev = 0  # stamped as `layout.editrevision`
        # The capability panels: one slot (container, capability) per
        # available capability, rebuilt in place by `_fill_slot`; the tab
        # panels element whose `value` is the active tab; the capability
        # whose `act` is running; the panel builds in progress, innermost
        # last (a capability's name, "" for the tabs region itself); and the
        # slots a rebuild deferred out of a figure-hook dispatch or a panel
        # build owes (`_tick` performs it), None for none.
        self._slots: dict[str, tuple[ui.element, Capability]] = {}
        self._tab_panels: ui.tab_panels | None = None
        self._acting: Capability | None = None
        self._filling: list[str] = []
        self._panels_pending: set[str] | None = None
        # Session events -> regions. The whole `tabs` region is NOT on
        # spec/results: Edit criteria stages on every control change, and
        # rebuilding the tab would destroy the control being typed in. Tabs
        # follow the selection, the job (the lock reason) and a close; the
        # panels inside them follow `results` slot by slot (`_follow_panels`).
        self.session.on("spec", self._on_spec)
        self.session.on("results", self._on_results)
        self.session.on("job", self._on_job)
        self.session.on("selection", self._on_selection)

    # -- lifecycle ---------------------------------------------------------

    def open(
        self,
        config_path: Path,
        *,
        backend: str | None = None,
        work_dir: Path | None = None,
    ) -> None:
        self.session.open(config_path, backend=backend, work_dir=work_dir)

    def visible_tests(self) -> list[str]:
        project = self.session.project
        if project is None:
            return []
        tests = _filter_tests(project.tests, self.filter, self.package)
        return [t.model_id for t in tests]

    def _on_spec(self, event: dict) -> None:
        self._refresh("header", "tests", "drawer", "jobs")
        self._follow_centre()
        if self.session.project is None:
            # A close emits only `spec` — no rescore, so no `results` for
            # the panels to follow — and resets the selection.
            self._refresh("tabs")

    def _on_results(self, event: dict) -> None:
        self._refresh("tests", "leaf_error")
        self._follow_centre()
        self._follow_panels()

    def _follow_panels(self) -> None:
        """Rebuild every capability's panel after a rescore, so none shows —
        or re-stages on its next keystroke — a leaf another capability, a
        Discard, a Save or a Reload has since changed. On `results`, not
        `spec`: `spec` fires before the rescore, and a panel rebuilt there
        would read the old comparisons.

        The acting capability's own slot is skipped while its panel-originated
        act runs (the field being typed in survives) — EXCEPT when the act
        changes which capabilities are available: then the whole tab set
        rebuilds, the actor's panel included. An act from the figure hook
        rebuilds the actor's slot too, and is deferred to `_tick`, because a
        rebuild inside the dispatch would clear and replace the handlers of
        every other subscriber to that event before they heard it.

        A results event emitted while a panel is being built (a panel that
        rescores in its build) is deferred to `_tick` as well, without the
        slots being built: rebuilding them synchronously would re-enter the
        build that emitted it, and a panel that rescores on every build would
        recurse. Nothing before the page is built: `Shell.open` runs before
        `root`."""
        if not self._built:
            return
        if self.figure_hook.dispatching:
            self._panels_pending = (self._panels_pending or set()) | set(self._slots)
            return
        if self._filling:
            owed = set(self._slots) - set(self._filling)
            self._panels_pending = (self._panels_pending or set()) | owed
            return
        skip = self._acting.name if self._acting is not None else None
        self._rebuild_panels({name for name in self._slots if name != skip})

    def _rebuild_panels(self, names: set[str]) -> None:
        """The tabs, when the available capabilities changed; otherwise the
        slots ``names`` names."""
        available = [cap.name for cap, avail in self.applicable() if avail is True]
        if available != list(self._slots):
            self._refresh("tabs")
            return
        for name in list(self._slots):
            if name in names:
                self._fill_slot(self._slots[name][1])

    def _fill_slot_later_or_now(self, cap: Capability) -> None:
        """One capability's slot: now, or at the next tick while the figure
        hook is dispatching or a panel is being built (see `_follow_panels`)."""
        if self.figure_hook.dispatching or self._filling:
            self._panels_pending = (self._panels_pending or set()) | {cap.name}
        else:
            self._fill_slot(cap)

    def _follow_centre(self) -> None:
        """Redraw the centre after a spec, results or selection event. While
        the plot element shows the same (model, variable, names) it is kept
        and only marked dirty — `_tick` redraws it once, however many events a
        single change emitted — and the tree below it is refreshed. A rebuilt
        plot element loses the user's zoom (the spike: replacing the element
        resets it; `update_figure` goes through `Plotly.react`, which keeps
        it). Anything else — a selection of another test or variable, an
        open, a close, a changed name list — rebuilds the centre; open and
        close reset the selection, so the key needs no project identity.
        Before the page is built there is no plot, so this only ever reaches
        the guarded `_refresh`."""
        key = self._centre_key()
        if (
            key is not None
            and key == self._plot_key
            and self._plot is not None
            and not self._plot.is_deleted
        ):
            self._figure_dirty = True
            self._tree.refresh()
        else:
            self._refresh("centre")

    def _mark_figure_dirty(self) -> None:
        """The hook's shape-moved callback. A drag moved a shape in the
        browser whether or not anything was staged; the redraw re-sends the
        figure so the shape lands where the Session says the handle is."""
        self._figure_dirty = True

    def _centre_key(self) -> tuple[str, str, tuple[str, ...]] | None:
        """``(model_id, variable, names)`` the centre plots for the current
        selection, or None when it plots nothing."""
        resolved = self._centre_selection()
        if resolved is None:
            return None
        model_id, variable, names = resolved
        if variable is None:
            return None
        return (model_id, variable, names)

    def _centre_selection(
        self,
    ) -> tuple[str, str | None, tuple[str, ...]] | None:
        """The one resolution `_centre` and `_centre_key` share: the single
        selected test, the variable it plots (the selected one when the
        picker lists it, else the first name, else None) and the picker's
        names. None without a project or a single selected test."""
        if self.session.project is None:
            return None
        sel = self.session.selection
        if len(sel.tests) != 1:
            return None
        (model_id,) = tuple(sel.tests)
        names = self._variable_names(model_id)
        variable = (
            sel.variable if sel.variable in names else (names[0] if names else None)
        )
        return (model_id, variable, tuple(names))

    def _on_selection(self, event: dict) -> None:
        # A capability's refusal is about the selection it was shown for.
        self.errors.clear()
        # A new selection lands on the first enabled tab (a leaf click on
        # Edit criteria, which registers first); only the rebuilds other
        # causes trigger keep the active tab. `_tabs` reads the tab to keep
        # from this element, so forgetting it is the cause passed along —
        # the rebuild itself runs later, deferred.
        self._tab_panels = None
        self._refresh("tests")
        # A tree-row click changes only `leaf_path`: the key is the same, so
        # the plot (and the user's zoom) stays and only the tree is refreshed.
        # Before `tabs`, the order the regions always refreshed in: a panel
        # (Edit tree) repeats the leaf path, and the simulated click in the
        # shell tests picks the lowest element id among the matches.
        self._follow_centre()
        self._refresh("tabs")

    def _on_job(self, event: dict) -> None:
        if event.get("state") in ("finished", "failed"):
            # A lock refusal shown in a tab is about a job that is now over.
            self.errors.clear()
        # Unconditional, for every state including `written`: `results`
        # rebuilds panels but not the tabs (a lock reason), and skips the
        # acting capability's own panel, so this is what re-renders the rows
        # of the panel whose act made the store write.
        self._refresh("jobs", "tests", "tabs")

    def _refresh(self, *regions: str) -> None:
        """Refresh regions that exist. NiceGUI defers each refresh to a
        background task; before the page is built there is nothing to do."""
        if not self._built:
            return
        for region in regions:
            getattr(self, f"_{region}").refresh()

    # -- capabilities ------------------------------------------------------

    def applicable(self) -> list[tuple[Capability, bool | str]]:
        """Every registered capability whose scope matches the selection,
        with ``available``'s answer. PROJECT always matches; TESTS needs a
        selection; VARIABLE a variable; LEAF a leaf. With no project open,
        only PROJECT-scope capabilities are listed (§4.6)."""
        sel = self.session.selection
        out: list[tuple[Capability, bool | str]] = []
        for cap in registered_capabilities():
            if self.session.project is None:
                matches = cap.scope is Scope.PROJECT
            else:
                matches = (
                    cap.scope is Scope.PROJECT
                    or (cap.scope is Scope.TESTS and bool(sel.tests))
                    or (cap.scope is Scope.VARIABLE and sel.variable is not None)
                    or (cap.scope is Scope.LEAF and sel.leaf_path is not None)
                )
            if matches:
                out.append((cap, cap.available(self.view)))
        return out

    def act(self, cap: Capability, **inputs: Any) -> None:
        """``Panel.submit`` lands here: the capability's ``act``, then the
        Session applies the outcome. An exception is shown in the tab. An
        ``act`` that raises changes nothing; an outcome that is a sequence
        whose later item fails leaves its earlier items applied — the
        Session is not rolled back. While it runs the capability is
        ``_acting``: the panels the outcome's events rebuild skip its own."""
        try:
            was, self._acting = self._acting, cap
            try:
                outcome = cap.act(self.view, **inputs)
                self.session.apply(outcome)
            finally:
                self._acting = was
            if self.errors.pop(cap.name, None) is not None:
                self._fill_slot_later_or_now(cap)  # the error it showed is gone
        except Exception as e:  # shown in the tab, never raised into NiceGUI
            self.errors[cap.name] = f"{type(e).__name__}: {e}"
            self._fill_slot_later_or_now(cap)

    # -- the page ----------------------------------------------------------

    def root(self) -> None:
        """The index page. ``ui.run(shell.root, ...)`` mounts it; the tests
        mount it through ``user_simulation(root=shell.root)``. Top-level
        layout elements (header, footer, drawer) are created here, once;
        only their CONTENT is refreshable."""
        self._add_routes()
        with ui.header().classes("items-center"):
            self._header()
        with ui.right_drawer(value=False) as self._drawer_el:
            self._drawer()
        with ui.row().classes("w-full no-wrap"):
            with ui.column().classes("w-1/5"):
                self._tests()
            with ui.column().classes("w-3/5"):
                self._centre()
            with ui.column().classes("w-1/5"):
                self._tabs()
        with ui.footer():
            self._jobs()
        ui.timer(0.1, self._tick)
        ui.timer(1.0, self._poll, immediate=False)
        self._built = True

    def _add_routes(self) -> None:
        """The dashboard link: one route resolving the OPEN project's work
        dir per request, so an OpenProject swap follows."""
        if self._routes_added:
            return
        self._routes_added = True

        @app.get("/dashboard/{path:path}")
        def _dashboard(path: str):
            project = self.session.project
            if project is None:
                return JSONResponse({"error": "no project open"}, status_code=409)
            work_dir = project.config.work_dir
            assert work_dir is not None  # resolved in Config.__post_init__
            target = (work_dir / path).resolve()
            if work_dir.resolve() not in target.parents or not target.is_file():
                return JSONResponse({"error": "not found"}, status_code=404)
            return FileResponse(target)

    def _tick(self) -> None:
        """Drain what worker threads posted (a job's completion); the
        Session events it emits refresh the regions. Then, if a spec or
        results event (or a shape drag) marked the centre figure dirty and
        the plot still shows the same key, redraw it in place, once. Before
        either, the panel rebuilds a figure-hook dispatch deferred."""
        while True:
            try:
                fn = self._posted.get_nowait()
            except queue.Empty:
                break
            fn()
        if (
            self._panels_pending is not None
            and not self.figure_hook.dispatching
            and not self._filling
        ):
            names, self._panels_pending = self._panels_pending, None
            self._rebuild_panels(names)
        if not self._figure_dirty:
            return
        self._figure_dirty = False
        key = self._centre_key()
        if key is None or key != self._plot_key:
            return  # the centre is (being) rebuilt with a fresh figure
        if self._plot is None or self._plot.is_deleted:
            return
        model_id, variable, _names = key
        self._figure_rev += 1
        fig = self.session.figure(model_id, variable)
        # `editrevision` must change on every update: with `uirevision`
        # constant, `Plotly.react` otherwise KEEPS a user's shape drag when
        # the new figure repeats the value sent last, and a refused or no-op
        # drag would not snap back (the spike).
        fig.setdefault("layout", {})["editrevision"] = self._figure_rev
        self._plot.update_figure(fig)

    def _poll(self) -> None:
        """While a job runs, re-read its progress: the job panel polls
        ``status.json``, exactly as the live dashboard does (§4.7)."""
        if self.session.running is not None:
            self._refresh("jobs")

    @ui.refreshable_method
    def _header(self) -> None:
        project = self.session.project
        if project is None:
            ui.label("dstf — no project open")
            ui.button("Quit", on_click=app.shutdown)
            return
        cfg = project.config
        ui.label(cfg.library_name or "").classes("text-lg").mark("project")
        ui.label(f"backend: {cfg.backend_name}")
        ui.button(
            f"{len(self.session.spec.pending)} pending",
            on_click=lambda: self._drawer_el.toggle(),
        ).props("flat").mark("pending")
        ui.button("Save", on_click=self._save)
        ui.button("Discard", on_click=self.session.discard)
        ui.button("Export", on_click=self._export)
        ui.link("dashboard", "/dashboard/dashboard.html", new_tab=True)
        ui.button("Close project", on_click=self._close)
        ui.button("Quit", on_click=app.shutdown)

    @ui.refreshable_method
    def _drawer(self) -> None:
        """The pending-patch drawer (§4.7): staged ops in readable form,
        grouped by model."""
        groups = self.session.spec.grouped()
        if not groups:
            ui.label("nothing staged")
            return
        for model_id, ops in groups:
            ui.label(model_id).classes("text-bold")
            for op in ops:
                value = json.dumps(op.get("value")) if "value" in op else ""
                ui.label(f"{op['op']} {op['path']} {value}"[:200]).classes("text-xs")

    def _save(self) -> None:
        try:
            self.session.save()
            ui.notify("saved")
        except StaleSpecError as e:
            with ui.dialog() as dialog, ui.card():

                def reload() -> None:
                    self.session.reload()
                    dialog.close()

                ui.label(f"{e.path} changed on disk since it was loaded.")
                ui.button("Reload", on_click=reload)
                ui.button("Keep my edits", on_click=dialog.close)
            dialog.open()
        except Exception as e:
            ui.notify(f"save failed: {e}", type="negative")

    def _export(self) -> None:
        project = self.session.project
        assert project is not None
        work_dir = project.config.work_dir
        assert work_dir is not None  # resolved in Config.__post_init__
        path = work_dir / "spec_patch.json"
        _atomic_write_text(path, json.dumps(self.session.export(), indent=2) + "\n")
        ui.notify(f"wrote {path}")

    def _close(self) -> None:
        try:
            self.session.close()
        except (PendingEditsError, JobRunningError) as e:
            ui.notify(str(e), type="warning")

    @ui.refreshable_method
    def _tests(self) -> None:
        project = self.session.project
        if project is None:
            return
        # The needle lives on the shell: every Session event rebuilds this
        # region, and a filter typed over 300 tests must survive a click.
        ui.input("filter", value=self.needle, on_change=self._on_filter).props("dense")
        rows = ui.column().classes("w-full")

        def draw() -> None:
            rows.clear()
            selected = self.session.selection.tests
            with rows:
                for model_id in self.visible_tests():
                    if self.needle and self.needle.lower() not in model_id.lower():
                        continue
                    comp = self.session.comparisons.get(model_id)
                    status, css = (
                        classify_with_class(comp) if comp is not None else ("", "")
                    )
                    with ui.row().classes("items-center no-wrap"):
                        ui.checkbox(
                            value=model_id in selected,
                            on_change=lambda e, m=model_id: self._toggle(m, e.value),
                        )
                        ui.button(
                            model_id.rsplit(".", 1)[-1],
                            on_click=lambda m=model_id: self._select_one(m),
                        ).props("flat dense no-caps").mark(model_id)
                        ui.badge(status, color=_BADGE_COLOR.get(css, "grey"))

        draw()

    def _on_filter(self, e) -> None:
        self.needle = e.value or ""
        self._refresh("tests")

    def _select_one(self, model_id: str) -> None:
        names = self._variable_names(model_id)
        self.session.select(
            tests={model_id}, variable=names[0] if names else None, leaf_path=None
        )

    def _variable_names(self, model_id: str) -> list[str]:
        """The variables the centre can plot: the scored names, else the
        result's. What a plain click selects is the first of them, so a
        VARIABLE-scope capability applies as soon as a plot is up."""
        project = self.session.project
        if project is None:
            return []
        comp = self.session.comparisons.get(model_id)
        names = (
            list(dict.fromkeys(vc.name for vc in comp.variables if vc.name))
            if comp is not None
            else []
        )
        if not names:
            result = project.results.get(model_id)
            names = [v.name for v in result.variables if v.name] if result else []
        return names

    def _toggle(self, model_id: str, on: bool) -> None:
        tests = set(self.session.selection.tests)
        (tests.add if on else tests.discard)(model_id)
        self.session.select(tests=tests, variable=None, leaf_path=None)

    @ui.refreshable_method
    def _centre(self) -> None:
        self._plot, self._plot_key = None, None
        project = self.session.project
        if project is None:
            ui.label("No project open. Start the tool on one:").classes("text-lg")
            ui.code("dstf author --config <path/to/testing.json>")
            return
        resolved = self._centre_selection()
        if resolved is None:
            tests = self.session.selection.tests
            ui.label(f"{len(tests)} tests selected" if tests else "select a test")
            return
        model_id, variable, names = resolved
        ui.label(model_id).classes("text-lg")
        if names:
            ui.select(
                list(names),
                value=variable,
                label="variable",
                on_change=lambda e: self.session.select(
                    tests={model_id}, variable=e.value, leaf_path=None
                ),
            ).props("dense")
        if variable is not None:
            fig = self.session.figure(model_id, variable)
            fig.setdefault("layout", {})["editrevision"] = self._figure_rev
            plot = ui.plotly(fig).classes("w-full")
            self._plot, self._plot_key = plot, (model_id, variable, names)
            self.figure_hook.attach(plot)
        self._tree(model_id)

    @ui.refreshable_method
    def _tree(self, model_id: str) -> None:
        """The selected test's criteria rows, below the plot: refreshed on
        its own when the plot is kept in place. A deferred refresh can run
        after a close or an open in the same pass: with no project, or a
        model the open project does not test, there is nothing to draw."""
        project = self.session.project
        if project is None or project.test(model_id) is None:
            return
        view = self.session.tree_view(model_id)
        if view is None:
            ui.label("no criteria scored")
            return
        self._tree_rows(model_id, view, depth=0)

    def _tree_rows(self, model_id: str, node: dict, depth: int) -> None:
        indent = f"ml-{min(depth * 4, 16)}"
        if node.get("kind") == "leaf":
            # The report's own reading: an advisory (warn-wrapped) leaf that
            # fails does not fail the test; an unevaluated leaf has no verdict.
            if node.get("passed") is None:
                verdict = "—"
            elif node.get("passed"):
                verdict = "PASS"
            else:
                verdict = "WARN" if node.get("advisory") else "FAIL"
            text = (
                f"{node['path']}  {node.get('name') or node.get('variable')} · "
                f"{node.get('mode_effective') or node.get('metric')}  {verdict}  "
                f"{node.get('score_display') or ''}"
            )
            ui.button(
                text,
                on_click=lambda n=node: self.session.select(
                    tests={model_id}, variable=n.get("variable"), leaf_path=n["path"]
                ),
            ).props("flat dense no-caps align=left").classes(indent)
            if node.get("error"):
                ui.label(node["error"]).classes(f"{indent} text-negative text-xs")
            return
        label = node.get("combinator", "")
        extra = f" k={node['k']}" if "k" in node else ""
        ui.label(
            f"{node['path']}  {label}{extra} [{len(node.get('children') or [])}]"
        ).classes(indent)
        for child in node.get("children") or []:
            self._tree_rows(model_id, child, depth + 1)

    @ui.refreshable_method
    def _tabs(self) -> None:
        self._filling.append("")  # a results event meanwhile is deferred
        try:
            self._build_tabs()
        finally:
            self._filling.pop()

    def _build_tabs(self) -> None:
        # A slot from the previous build has a deleted container: none may
        # survive into this one.
        self._slots = {}
        previous = self._tab_panels.value if self._tab_panels is not None else None
        self._tab_panels = None
        self.figure_hook.clear()  # every panel below registers afresh
        applicable = self.applicable()
        if not applicable:
            ui.label("nothing applies to this selection").classes("text-xs")
            return
        with ui.tabs() as tabs:
            for cap, avail in applicable:
                tab = ui.tab(cap.label)
                if avail is not True:
                    tab.disable()
                    if isinstance(avail, str):
                        tab.tooltip(avail)
        enabled = [cap.label for cap, avail in applicable if avail is True]
        # The active tab is kept when it is still available; after a
        # selection change there is none to keep (`_on_selection`).
        active = (
            previous
            if previous in enabled
            else (enabled[0] if enabled else applicable[0][0].label)
        )
        with ui.tab_panels(tabs, value=active).classes("w-full") as panels:
            self._tab_panels = panels
            for cap, avail in applicable:
                with ui.tab_panel(cap.label):
                    if avail is not True:
                        ui.label(
                            avail if isinstance(avail, str) else "not available"
                        ).classes("text-grey")
                        continue
                    self._slots[cap.name] = (ui.column().classes("w-full"), cap)
                    self._fill_slot(cap)

    def _fill_slot(self, cap: Capability) -> None:
        """Build (or rebuild in place) ``cap``'s panel in its slot: its
        error, the leaf's config error for a LEAF-scope panel, the panel.
        The capability's previous hook handlers go with the old panel. A
        panel whose build raises shows the exception here, and the other
        tabs still render. The active tab is untouched."""
        slot = self._slots.get(cap.name)
        if slot is None or slot[0].is_deleted:
            return
        container = slot[0]
        container.clear()
        self.figure_hook.clear(cap.name)
        self._filling.append(cap.name)
        try:
            self._build_slot(container, cap)
        finally:
            self._filling.pop()

    def _build_slot(self, container: ui.element, cap: Capability) -> None:
        with container:
            if cap.name in self.errors:
                ui.label(self.errors[cap.name]).classes("text-negative")
            if cap.scope is Scope.LEAF:
                self._leaf_error()
            try:
                cap.panel(
                    self.view, ShellPanel(self, cap, self.figure_hook.owned(cap.name))
                )
            except Exception as e:  # shown in the slot, never breaks the page
                ui.label(f"{type(e).__name__}: {e}").classes("text-negative")

    @ui.refreshable_method
    def _leaf_error(self) -> None:
        """The selected leaf's config error, above every LEAF-scope panel
        (§4.7 "inline on its form"), refreshed on every rescore — the
        acting panel is not rebuilt by its own act's rescore, so the
        capability cannot keep this current and the shell does."""
        sel = self.session.selection
        if len(sel.tests) != 1 or sel.leaf_path is None:
            return
        (model_id,) = tuple(sel.tests)
        node = _find_node(self.session.tree_view(model_id), sel.leaf_path)
        if node is not None and node.get("error"):
            ui.label(node["error"]).classes("text-negative").mark("leaf-error")

    @ui.refreshable_method
    def _jobs(self) -> None:
        """The footer (§4.7): the running job's per-test progress from
        ``status.json``, else the last job's outcome and what its results
        say went wrong, then the store writes' lines and the artifacts.
        Nothing here may raise — a malformed snapshot renders what it has."""
        job = self.session.running
        last = self.session.last_job
        ui.label(f"running: {job.label}" if job else "no job running")
        if job is not None:
            self._progress_rows(job)
        elif last is not None:
            if last.error is None:
                ui.label(f"finished: {last.label}")
                # Only a job that reloaded the results owns them: after a
                # failure, or after a job that asked for no reload, the
                # outcome line stands alone rather than over the previous
                # run's messages.
                if last.reloaded:
                    self._result_rows(last)
            else:
                ui.label(
                    f"failed: {last.label} — {type(last.error).__name__}: {last.error}"
                ).classes("text-negative")
        for notice in self.session.notices:
            ui.label(notice).classes("text-xs")
        for artifact in self.session.artifacts:
            for path in artifact.paths:
                ui.link(f"{artifact.label}: {path.name}", path.as_uri(), new_tab=True)

    def _progress_rows(self, job: RunningJob) -> None:
        """One row per scoped test in the snapshot — but only a snapshot this
        job wrote: a ``start_wall`` older than the job's start belongs to the
        previous run (the same guard `dashboard_render` applies to a sidecar).
        The slack is small because the job's own snapshot can never be older
        than ``started_wall`` — that is sampled before the thread starts, the
        reporter's inside ``run_tests`` — so it only ever admits a stale one,
        and a sub-second rerun re-clicked at once would show the last run's
        rows. Every shape here is whatever a file on disk held."""
        project = self.session.project
        work_dir = project.config.work_dir if project is not None else None
        if work_dir is None:
            return
        status = read_status(work_dir)
        if status is None:
            return
        start_wall = status.get("start_wall")
        # `json.loads` accepts the literal `NaN`, and every comparison with a
        # NaN is False — so a non-finite value would slip past the staleness
        # test below. Non-finite is no timestamp: treat it as stale.
        if not isinstance(start_wall, (int, float)) or not math.isfinite(start_wall):
            start_wall = 0.0
        if start_wall < job.started_wall - 0.1:
            return
        rows = status.get("tests")
        if not isinstance(rows, list):
            rows = []
        for row in rows:
            model_id = row.get("model_id") if isinstance(row, dict) else None
            if not isinstance(model_id, str) or model_id not in job.models:
                continue
            parts = [model_id]
            for key in ("status", "phase"):
                if row.get(key):
                    parts.append(str(row[key]))
            elapsed = row.get("elapsed")
            if isinstance(elapsed, (int, float)):
                parts.append(f"{elapsed:.1f} s")
            if row.get("detail"):
                parts.append(str(row["detail"]))
            ui.label(" · ".join(parts)).classes("text-xs").mark("job-row")

    def _result_rows(self, last: RunningJob) -> None:
        """What the finished job left: the reloaded result's failure message
        per model it ran (§4.7 "the backend's failure message from the
        results"), in project order. Only ever called for a job whose reload
        ran, so every row here is that job's own."""
        project = self.session.project
        if project is None:
            return
        for test in project.tests:
            if test.model_id not in last.models:
                continue
            result = project.results.get(test.model_id)
            text = test.model_id
            if result is not None and not result.success:
                text = f"{text} · {result.error_message or 'simulation failed'}"
            ui.label(text).classes("text-xs").mark("job-row")


def run_app(
    config_path: Path,
    *,
    filter: str | None = None,
    package: str | None = None,
    port: int | None = None,
    work_dir: Path | None = None,
    backend: str | None = None,
    show: bool = True,
) -> None:
    """``dstf author``: open the project, mount the shell, serve until Quit."""
    shell = Shell(filter=filter, package=package)
    shell.open(config_path, backend=backend, work_dir=work_dir)
    project = shell.session.project
    ui.run(
        shell.root,
        host="127.0.0.1",
        port=port or 8080,
        show=show,
        reload=False,
        title=f"dstf author — {project.config.library_name if project else ''}",
    )
