"""The header both pages share (DESIGN.md "Header"; brief §3.3): "DSTF", the
library and the backend on the left; the run indicator in the centre; the
pending count with Save / Discard / Export, the dashboard link and Stop
server on the right. Every button's label says its object, effect and
count; a disabled one carries the sentence that says why (DESIGN.md
"Disabled states explain themselves"). Off-white with a hairline below,
never an accent bar.

A page may ask for a second row of its own (``below``): it is drawn INSIDE
this fixed header, which is what makes it sticky without offset arithmetic
(decision 8). The Test page's header is the one."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from nicegui import ui

from ...storage.ref_index import _atomic_write_text
from ..capabilities import plural
from ..session import StaleSpecError
from .widgets import Action

if TYPE_CHECKING:
    from .shell import Shell


class Header:
    def __init__(self, shell: Shell, below: Callable[[], None] | None = None) -> None:
        self.shell = shell
        self.drawer = PendingDrawer(shell)
        self.dialog = ui.dialog()  # the header's dialogs, one element rebuilt per use
        with ui.header().classes("dstf-header column no-wrap gap-0") as bar:
            row = ui.row().classes("dstf-header-row items-center no-wrap gap-3 w-full")
        with row:
            ui.label("DSTF").classes("dstf-wordmark").mark("wordmark")
            self.library = ui.label().classes("text-secondary").mark("project")
            self.backend = (
                ui.select([], label="backend", on_change=self._change_backend)
                .props("dense outlined options-dense")
                .mark("backend")
            )
            self.backend_why = ui.label().classes("dstf-why").mark("backend:why")
            self.other = (
                ui.button("Open in another backend…", on_click=self._open_other)
                .props("flat dense no-caps")
                .mark("other-backend")
            )
            ui.space()
            self.runs = ui.label().classes("text-secondary").mark("runs")
            ui.space()
            self.pending = (
                ui.button(on_click=self.drawer.toggle)
                .props("flat dense no-caps")
                .mark("pending")
            )
            self.save = Action(
                self._save,
                props="unelevated dense no-caps color=primary",
                marker="save",
            )
            self.discard = Action(
                self._discard, props="flat dense no-caps", marker="discard", why=False
            )
            self.export = Action(
                self._export, props="flat dense no-caps", marker="export", why=False
            )
            ui.link("dashboard", "/dashboard/dashboard.html", new_tab=True)
            self.stop = (
                ui.button("Stop server", on_click=self._stop)
                .props("outline dense no-caps color=negative")
                .mark("stop")
            )
        if below is not None:
            # A second row INSIDE the fixed header (decision 8): sticky with
            # no offset arithmetic and no dependence on the first row's
            # height. The Test page's own header is what `below` draws.
            with bar:
                sub = ui.row().classes(
                    "dstf-subheader items-center no-wrap gap-3 w-full"
                )
            with sub.mark("subheader"):
                below()
        self.refresh()

    def handle(self, event: dict[str, Any]) -> None:
        self.refresh()

    def refresh(self) -> None:
        shell, s = self.shell, self.shell.session
        project, load = s.project, shell.load
        if project is not None:
            self.library.set_text(project.config.library_name or "")
            if not load.in_progress:
                # Not while a reopen runs: the select shows the user's choice
                # until the Session commits it.
                current = project.config.backend_name
                options = sorted(
                    set(project.config.backends) | ({current} if current else set())
                )
                self.backend.set_options(options, value=current)
        else:
            self.library.set_text("opening…" if load.in_progress else "no project")
        why = self._backend_why()
        self.backend.set_enabled(why is None)
        self.backend_why.set_text(why or "")
        self.backend_why.set_visibility(why is not None)
        self.other.set_enabled(project is not None)
        running = s.running
        self.runs.set_text(
            f"1 running · {running.label}" if running is not None else "idle"
        )
        n = len(s.spec.pending)
        models = len({op.model_id for op in s.spec.pending})
        self.pending.set_text(plural(n, "pending edit"))
        reason = "no pending edits" if n == 0 else shell.busy_reason()
        self.save.set(
            f"Save {plural(n, 'spec edit')} ({plural(models, 'test')}) to "
            f"{s.spec.path.name}",
            reason,
        )
        self.discard.set(f"Discard {plural(n, 'edit')}", reason)
        self.export.set(
            f"Export {plural(n, 'edit')} to spec_patch.json",
            "no pending edits" if n == 0 else None,
        )
        self.drawer.refresh()

    # -- the backend -------------------------------------------------------------

    def _backend_why(self) -> str | None:
        """Why the backend cannot change now (brief §3.6: refused while edits
        are pending or a run is in flight), or None."""
        s = self.shell.session
        if s.project is None:
            return "no project open"
        busy = self.shell.busy_reason()
        if busy is not None:
            return f"backend change waits: {busy}"
        n = len(s.spec.pending)
        if n:
            return (
                f"backend change waits: save or discard {plural(n, 'pending edit')} "
                "first"
            )
        return None

    def _change_backend(self, e: Any) -> None:
        """Reopen on the chosen backend (brief §3.6), on the shell's open
        thread; the page follows the loading events as on startup."""
        project = self.shell.session.project
        if project is None or e.value in (None, project.config.backend_name):
            return
        why = self._backend_why()
        if why is not None:
            ui.notify(why, type="warning")
            self.backend.set_value(project.config.backend_name)
            return
        self.shell.open_async(e.value)
        self.refresh()

    def _open_other(self) -> None:
        """A second tool process on another backend (brief §3.6): its own
        port and browser tab; one project and backend per process."""
        project = self.shell.session.project
        if project is None:
            return
        current = project.config.backend_name  # `str | None`, as in `refresh`
        options = sorted(
            set(project.config.backends) | ({current} if current else set())
        )
        self.dialog.clear()
        with self.dialog, ui.card():
            ui.label("Open this project in another backend").classes("text-h6")
            ui.label(
                "A second dstf author process on its own port, opening its own browser "
                "tab; one project and backend per process. Stop server here ends it too."
            ).classes("text-xs text-secondary")
            choice = (
                ui.select(options, value=current, label="backend")
                .props("dense outlined")
                .mark("other-choice")
            )
            link = ui.link("", "/", new_tab=True).mark("other-link")
            link.set_visibility(False)

            def start() -> None:
                url = self.shell.spawn(str(choice.value))
                link.set_text(f"open {url}")
                link.props(f'href="{url}"')
                link.set_visibility(True)

            with ui.row():
                ui.button("Start", on_click=start).props(
                    "unelevated dense no-caps color=primary"
                ).mark("start-other")
                ui.button("Close", on_click=self.dialog.close).props(
                    "flat dense no-caps"
                )
        self.dialog.open()

    # -- the edits -----------------------------------------------------------------

    def _save(self) -> None:
        try:
            self.shell.session.save()
            ui.notify("saved")
        except StaleSpecError as e:
            self.dialog.clear()
            with self.dialog, ui.card():

                def reload() -> None:
                    self.shell.session.reload()
                    self.dialog.close()

                ui.label(f"{e.path} changed on disk since it was loaded.")
                with ui.row():
                    ui.button("Reload", on_click=reload).props("outline dense no-caps")
                    ui.button("Keep my edits", on_click=self.dialog.close).props(
                        "flat dense no-caps"
                    )
            self.dialog.open()
        except Exception as e:
            ui.notify(f"save failed: {e}", type="negative")

    def _discard(self) -> None:
        self.shell.session.discard()

    def _export(self) -> None:
        project = self.shell.session.project
        assert project is not None  # the Action is disabled without one
        work_dir = project.config.work_dir
        assert work_dir is not None  # resolved in Config.__post_init__
        path = work_dir / "spec_patch.json"
        _atomic_write_text(
            path, json.dumps(self.shell.session.export(), indent=2) + "\n"
        )
        ui.notify(f"wrote {path}")

    # -- stop ----------------------------------------------------------------------

    def _stop(self) -> None:
        """Stop server (brief §3.3): at once with nothing to lose; else a
        confirm naming what would be lost."""
        reasons = self.shell.stop_reasons()
        if not reasons:
            self.shell.stop()
            return
        self.dialog.clear()
        with self.dialog, ui.card():
            ui.label("Stop the server?").classes("text-h6")
            for reason in reasons:
                ui.label(reason).mark("stop-reason")
            with ui.row():
                ui.button("Stop anyway", on_click=self.shell.stop).props(
                    "outline dense no-caps color=negative"
                ).mark("stop-anyway")
                ui.button("Keep running", on_click=self.dialog.close).props(
                    "flat dense no-caps"
                )
        self.dialog.open()


class PendingDrawer:
    """The pending-edit drawer (§4.7; brief §3.3 "the drawer listing them by
    test"): staged ops in readable form, grouped by model. Toggled by the
    header's pending button."""

    def __init__(self, shell: Shell) -> None:
        self.shell = shell
        self.element = ui.right_drawer(value=False).props("bordered")
        with self.element:
            self.body = ui.column().classes("w-full gap-1")

    def toggle(self) -> None:
        self.element.toggle()

    def refresh(self) -> None:
        self.body.clear()
        with self.body:
            groups = self.shell.session.spec.grouped()
            if not groups:
                ui.label("nothing staged").classes("text-secondary")
                return
            for model_id, ops in groups:
                ui.label(model_id).classes("text-bold dstf-mono").mark("drawer-model")
                for op in ops:
                    value = json.dumps(op.get("value")) if "value" in op else ""
                    ui.label(f"{op['op']} {op['path']} {value}"[:200]).classes(
                        "text-xs dstf-mono"
                    ).mark("drawer-op")
