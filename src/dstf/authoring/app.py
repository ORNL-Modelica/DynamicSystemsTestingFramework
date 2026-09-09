"""The authoring shell (§4.6 "The shell", §4.7): fixed, and it knows no
capability by name. It reads a SessionView, lists capabilities by scope,
and routes every outcome through Session.apply. Everything it draws comes
from the Session: the figure dict, the report's tree view, the verdicts.
"""

from __future__ import annotations

import json
import queue
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi.responses import FileResponse
from nicegui import app, ui

from ..cli.common import _filter_tests
from ..reporting.status import classify_with_class
from .capabilities import (
    Capability,
    Scope,
    registered_capabilities,
)
from .capabilities import FigureHook as FigureHookProtocol
from .session import (
    JobRunningError,
    PendingEditsError,
    Session,
    SessionView,
    StaleSpecError,
)


class FigureHook:
    """``panel.figure.on(kind, handler)``: handlers get the primitive id and
    the Plotly event payload. Wired to the centre plot's Plotly events;
    no v1 capability subscribes (on-plot editing is the first)."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable[[str, dict], None]]] = {}

    def on(self, kind: str, handler: Callable[[str, dict], None]) -> None:
        self._handlers.setdefault(kind, []).append(handler)

    def attach(self, plot: ui.plotly) -> None:
        for event in ("plotly_click", "plotly_relayout", "plotly_selected"):
            plot.on(event, lambda e, kind=event: self._dispatch(kind, e.args))

    def _dispatch(self, kind: str, payload: Any) -> None:
        data = payload if isinstance(payload, dict) else {}
        points = data.get("points") or [{}]
        first = points[0] if isinstance(points[0], dict) else {}
        primitive = first.get("customdata")
        if primitive is None:
            primitive = first.get("curveNumber")
        for handler in self._handlers.get(kind, []):
            handler("" if primitive is None else str(primitive), data)


class ShellPanel:
    """The container a capability owns (§4.6 "Panel"): the current NiceGUI
    slot, the figure hook, and ``submit`` — the one way an outcome reaches
    the Session."""

    figure: FigureHookProtocol

    def __init__(self, shell: Shell, cap: Capability, figure: FigureHook):
        self._shell, self._cap, self.figure = shell, cap, figure

    def submit(self, **inputs: Any) -> None:
        self._shell.act(self._cap, **inputs)


class Shell:
    """One per process. ``root`` builds the page; every region is a
    refreshable method the Session's events refresh."""

    def __init__(
        self, *, filter: str | None = None, package: str | None = None
    ) -> None:
        self._posted: queue.Queue[Callable[[], None]] = queue.Queue()
        self.session = Session(post=self._posted.put)
        self.view = SessionView(self.session)
        self.filter, self.package = filter, package
        self.errors: dict[str, str] = {}  # capability name -> last error in its tab
        self.figure_hook = FigureHook()
        self._built = False
        self._routes_added = False
        # Session events -> regions. `tabs` is NOT on spec/results: Edit
        # criteria stages on every control change, and rebuilding the tab
        # would destroy the control being typed in. Tabs follow the
        # selection, the job (the lock reason), and an act error.
        self.session.on(
            "spec", lambda e: self._refresh("header", "tests", "centre", "drawer")
        )
        self.session.on("results", lambda e: self._refresh("tests", "centre"))
        self.session.on("job", lambda e: self._refresh("jobs", "tests", "tabs"))
        self.session.on("selection", lambda e: self._refresh("tests", "centre", "tabs"))

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
        Session applies the outcome. An exception is shown in the tab and
        the Session is what it was."""
        try:
            outcome = cap.act(self.view, **inputs)
            self.session.apply(outcome)
            if self.errors.pop(cap.name, None) is not None:
                self._refresh("tabs")
        except Exception as e:  # shown in the tab, never raised into NiceGUI
            self.errors[cap.name] = f"{type(e).__name__}: {e}"
            self._refresh("tabs")

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
                return {"error": "no project open"}
            work_dir = project.config.work_dir
            assert work_dir is not None  # resolved in Config.__post_init__
            target = (work_dir / path).resolve()
            if work_dir.resolve() not in target.parents or not target.is_file():
                return {"error": "not found"}
            return FileResponse(target)

    def _tick(self) -> None:
        """Drain what worker threads posted (a job's completion); the
        Session events it emits refresh the regions."""
        while True:
            try:
                fn = self._posted.get_nowait()
            except queue.Empty:
                return
            fn()

    @ui.refreshable_method
    def _header(self) -> None:
        project = self.session.project
        if project is None:
            ui.label("dstf — no project open")
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
        path.write_text(
            json.dumps(self.session.export(), indent=2) + "\n", encoding="utf-8"
        )
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
        needle = {"text": ""}
        rows = ui.column().classes("w-full")

        def draw() -> None:
            rows.clear()
            selected = self.session.selection.tests
            with rows:
                for model_id in self.visible_tests():
                    if (
                        needle["text"]
                        and needle["text"].lower() not in model_id.lower()
                    ):
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
                        ui.badge(status).classes(css)

        def on_filter(e) -> None:
            needle["text"] = e.value or ""
            draw()

        ui.input("filter", on_change=on_filter).props("dense")
        draw()

    def _select_one(self, model_id: str) -> None:
        self.session.select(tests={model_id}, variable=None, leaf_path=None)

    def _toggle(self, model_id: str, on: bool) -> None:
        tests = set(self.session.selection.tests)
        (tests.add if on else tests.discard)(model_id)
        self.session.select(tests=tests, variable=None, leaf_path=None)

    @ui.refreshable_method
    def _centre(self) -> None:
        project = self.session.project
        if project is None:
            ui.label("No project open. Start the tool on one:").classes("text-lg")
            ui.code("dstf author --config <path/to/testing.json>")
            return
        sel = self.session.selection
        if len(sel.tests) != 1:
            ui.label(
                f"{len(sel.tests)} tests selected" if sel.tests else "select a test"
            )
            return
        (model_id,) = tuple(sel.tests)
        comp = self.session.comparisons.get(model_id)
        names = (
            [vc.name for vc in comp.variables if vc.name] if comp is not None else []
        )
        if not names:
            result = project.results.get(model_id)
            names = (
                [v.name for v in result.variables if v.name]
                if result is not None
                else []
            )
        variable = (
            sel.variable if sel.variable in names else (names[0] if names else None)
        )
        ui.label(model_id).classes("text-lg")
        if names:
            ui.select(
                names,
                value=variable,
                label="variable",
                on_change=lambda e: self.session.select(
                    tests={model_id}, variable=e.value, leaf_path=None
                ),
            ).props("dense")
        if variable is not None:
            plot = ui.plotly(self.session.figure(model_id, variable)).classes("w-full")
            self.figure_hook.attach(plot)
        view = self.session.tree_view(model_id)
        if view is None:
            ui.label("no criteria scored")
            return
        self._tree_rows(model_id, view, depth=0)

    def _tree_rows(self, model_id: str, node: dict, depth: int) -> None:
        indent = f"ml-{min(depth * 4, 16)}"
        if node.get("kind") == "leaf":
            verdict = "PASS" if node.get("passed") else "FAIL"
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
        first = next(
            (cap.label for cap, avail in applicable if avail is True),
            applicable[0][0].label,
        )
        with ui.tab_panels(tabs, value=first).classes("w-full"):
            for cap, avail in applicable:
                with ui.tab_panel(cap.label):
                    if avail is not True:
                        ui.label(
                            avail if isinstance(avail, str) else "not available"
                        ).classes("text-grey")
                        continue
                    if cap.name in self.errors:
                        ui.label(self.errors[cap.name]).classes("text-negative")
                    cap.panel(self.view, ShellPanel(self, cap, self.figure_hook))

    @ui.refreshable_method
    def _jobs(self) -> None:
        job = self.session.running
        ui.label(f"running: {job.label}" if job else "no job running")
        for artifact in self.session.artifacts:
            for path in artifact.paths:
                ui.link(f"{artifact.label}: {path.name}", path.as_uri(), new_tab=True)


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
