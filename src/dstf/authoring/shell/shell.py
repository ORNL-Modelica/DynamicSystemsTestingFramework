"""The shell as a process (roadmap §2; D129): one Session, one thread that
opens the project, one queue every Session event crosses on its way to the
pages, one app-level timer that drains it, the pages a browser has open,
and the child processes "Open in another backend…" starts. It knows no
capability by name: `placed` lists what is registered at a placement with
each one's answer to `available`. Nothing here is a page — the pages are
`suite.py` and `test_page.py`, one object per browser tab."""

from __future__ import annotations

import logging
import queue
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote

from fastapi.responses import FileResponse, JSONResponse
from nicegui import app, ui

from ...cli.common import _filter_tests
from ...reporting.theme import css_variables, quasar_palette
from ..capabilities import Capability, Placement, plural, registered_capabilities
from ..session import Selection, Session, SessionView
from .style import STYLE

logger = logging.getLogger(__name__)

TICK = 0.1  # seconds; a Session event reaches every page within one


class Page(Protocol):
    """What the shell fans events out to: a live page and its client."""

    client: Any  # nicegui.Client; `is_deleted` prunes the page

    def handle(self, event: dict[str, Any]) -> None: ...


@dataclass
class LoadState:
    """Where the open stands, kept by the shell from the Session's `loading`
    events (decision 9) and read by every page's progress line."""

    phase: str = "idle"  # idle | scan | results | done | failed
    done: int = 0
    total: int = 0
    cached: bool = False
    #: The last open's failure, or a warning from an open that completed —
    #: one model's scoring raised; a reopen that could not build its backend
    #: and left the project as it was.
    error: str | None = None
    #: Models announced with `error=True`: scored by nobody, drawn as such.
    failed_models: list[str] = field(default_factory=list)

    @property
    def in_progress(self) -> bool:
        return self.phase in ("scan", "results")

    def text(self) -> str:
        """The progress line (brief §3.8: "scanning library… N tests found")."""
        if self.phase == "scan":
            if self.cached:
                return f"library scan from cache: {plural(self.total, 'test')}"
            if self.total:
                return f"scanning library… {self.done} of {self.total}"
            return "scanning library…"
        if self.phase == "results":
            return f"scoring results… {self.done} of {self.total}"
        if self.phase == "failed":
            return f"could not open the project: {self.error}"
        if self.error:
            return f"warning: {self.error}"
        return ""


class Shell:
    def __init__(
        self,
        config_path: Path,
        *,
        work_dir: Path | None = None,
        filter: str | None = None,
        package: str | None = None,
        show_browser: bool = True,
    ) -> None:
        self.config_path = Path(config_path)
        self.work_dir = work_dir
        self.filter, self.package = filter, package
        self.show_browser = show_browser
        # What worker threads post — a job's completion, the rescan's merge:
        # Session mutations that must run on the UI loop. `tick` runs them.
        self._posted: queue.Queue[Callable[[], None]] = queue.Queue()
        self.session = Session(post=self._posted.put)
        # Every Session event, from whichever thread emitted it, crosses here
        # and is fanned out to the pages on the UI loop by `tick`.
        self._events: queue.Queue[dict[str, Any]] = queue.Queue()
        for event in ("spec", "results", "job", "loading"):
            self.session.on(event, self._events.put)
        self.pages: list[Page] = []
        self.load = LoadState()
        self._open_thread: threading.Thread | None = None
        self.children: list[subprocess.Popen[bytes]] = []
        self._mounted = False
        self._timer: Any = None
        self._ticks = 0

    # -- the open ------------------------------------------------------------

    def open_async(self, backend: str | None = None) -> None:
        """Open the project — or reopen it on `backend` (brief §3.6) — on a
        thread; the pages follow the `loading` events and the `opened`
        event this shell adds at the end. Refused while an open runs.
        `Session.open` itself refuses while edits are pending or a job runs,
        and that refusal arrives as the `opened` event's error."""
        if self._open_thread is not None and self._open_thread.is_alive():
            raise RuntimeError("the project is still opening")
        self.load = LoadState(phase="scan")
        thread = threading.Thread(
            target=self._open_worker, args=(backend,), name="dstf-open", daemon=True
        )
        self._open_thread = thread
        thread.start()

    def _open_worker(self, backend: str | None) -> None:
        error: str | None = None
        try:
            self.session.open(self.config_path, backend=backend, work_dir=self.work_dir)
        except Exception as e:  # shown on the page (decision 8), never lost on a thread
            logger.exception("opening %s failed", self.config_path)
            error = f"{type(e).__name__}: {e}"
        self._events.put({"event": "opened", "error": error})

    # -- the loop --------------------------------------------------------------

    def start(self) -> None:
        """The app-level timer: one per process, once the event loop runs. A
        stopped app nulls its timers' callbacks (`Timer._cleanup`), so a
        Shell that outlives one app run gets a new timer with the next."""
        if self._timer is None or self._timer.callback is None:
            self._timer = app.timer(TICK, self.tick)

    def register(self, page: Page) -> None:
        self.pages.append(page)

    def tick(self) -> None:
        """Run what worker threads posted, then hand every queued Session
        event to every live page — a page whose client is gone is dropped.
        Once a second while a job runs, a `poll` event, so the runs card
        re-reads `status.json` (§4.7)."""
        while True:
            try:
                fn = self._posted.get_nowait()
            except queue.Empty:
                break
            fn()
        events: list[dict[str, Any]] = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                break
        self._ticks += 1
        every = max(1, round(1.0 / TICK))  # once a second, whatever the tick
        if self.session.running is not None and self._ticks % every == 0:
            events.append({"event": "poll"})
        # `is_deleted` is set by the browser's disconnect (after NiceGUI's
        # reconnect timeout); a page navigated away from in the user
        # simulation is never deleted, so tests pick a page by its client.
        self.pages = [p for p in self.pages if not p.client.is_deleted]
        for event in events:
            self._absorb(event)
            for page in list(self.pages):
                try:
                    page.handle(event)
                except Exception:  # one page's failure is not another's
                    logger.exception("a page failed on %r", event.get("event"))

    def _absorb(self, event: dict[str, Any]) -> None:
        """`LoadState` from the events (decision 9)."""
        kind = event["event"]
        load = self.load
        if kind == "loading":
            phase = event.get("phase")
            if phase == "scan":
                if load.phase != "scan":
                    # An open this shell did not start (an `OpenProject`
                    # outcome runs `Session.open` on the UI loop): a fresh
                    # state, so the previous project's failures do not carry.
                    load = self.load = LoadState(phase="scan")
                load.cached = bool(event.get("cached"))
                load.done = int(event.get("done", 0))
                load.total = int(event.get("total", 0))
            elif phase == "results":
                load.phase = "results"
                load.done, load.total = int(event["done"]), int(event["total"])
                if event.get("error"):
                    load.failed_models.append(str(event["model"]))
            elif phase == "done":
                load.phase = "done"
        elif kind == "spec" and load.phase == "scan":
            # The merge is done: the scan is over and the results are next.
            load.phase = "results"
            load.done, load.total = 0, len(event.get("models", ()))
        elif kind == "opened":
            error = event.get("error")
            if error is None:
                return
            if self.session.project is not None:
                # A project is open: this one, complete after a scoring
                # failure, or the previous one after a refused reopen.
                load.phase, load.error = "done", error
            else:
                load.phase, load.error = "failed", error

    # -- what the pages read -------------------------------------------------

    def placed(
        self, placement: Placement, view: SessionView
    ) -> list[tuple[Capability, bool | str]]:
        """Every registered capability that attaches at `placement`, with
        `available`'s answer for `view`, in registration order."""
        return [
            (cap, cap.available(view))
            for cap in registered_capabilities()
            if placement in cap.placements
        ]

    def view_for(self, selection: Selection) -> SessionView:
        return SessionView(self.session, selection)

    def visible_tests(self) -> list[str]:
        """The project's tests after the CLI's `--filter` / `--package`."""
        project = self.session.project
        if project is None:
            return []
        return [
            t.model_id for t in _filter_tests(project.tests, self.filter, self.package)
        ]

    def busy_reason(self) -> str | None:
        """Why a Save, a Discard or a backend change is refused now, or None:
        the open thread is still writing, or a job holds models."""
        if self.load.in_progress:
            return "the project is still loading"
        running = self.session.running
        if running is not None:
            return f"{running.label} is running"
        return None

    def stop_reasons(self) -> list[str]:
        """What Stop server would lose (brief §3.3): pending edits, a running
        job — sub-project 5 adds the queue."""
        reasons: list[str] = []
        n = len(self.session.spec.pending)
        if n:
            reasons.append(f"{plural(n, 'unsaved edit')} will be lost")
        running = self.session.running
        if running is not None:
            reasons.append(f"{running.label} is still running")
        return reasons

    def stop(self) -> None:
        """Stop server: the children first (decision 7), then NiceGUI."""
        self.terminate_children()
        app.shutdown()

    def terminate_children(self) -> None:
        for child in self.children:
            if child.poll() is None:
                child.terminate()

    # -- the pages ---------------------------------------------------------------

    def page_setup(self) -> None:
        """Every page starts here: the Quasar palette and the token
        stylesheet from `reporting.theme`, the shell's own on top — one token
        table for the dashboard, the report and the tool (roadmap §1
        predicate 2; the body font is this shell's to apply)."""
        ui.colors(**quasar_palette())
        ui.add_css(css_variables() + STYLE)

    def mount(self) -> None:
        """The routes beside the index: the dashboard's files, resolved
        against the OPEN project's work dir per request so a reopen follows,
        and the Test page. Once per process; `index` calls it too, so a
        simulation that mounted only the root page gets them on its first
        build."""
        if self._mounted:
            return
        self._mounted = True

        @app.get("/dashboard/{path:path}")
        def _dashboard(path: str) -> Any:
            project = self.session.project
            if project is None:
                return JSONResponse({"error": "no project open"}, status_code=409)
            work_dir = project.config.work_dir
            assert work_dir is not None  # resolved in Config.__post_init__
            target = (work_dir / path).resolve()
            if work_dir.resolve() not in target.parents or not target.is_file():
                return JSONResponse({"error": "not found"}, status_code=404)
            return FileResponse(target)

        @ui.page("/test/{model_id}")
        async def _test(model_id: str) -> None:
            from .test_page import TestPage

            self.start()
            await TestPage(self).build(unquote(model_id))

    async def index(self) -> None:
        """The Suite page at `/`: `ui.run(shell.index, ...)` mounts it, the
        tests `user_simulation(root=shell.index)`."""
        from .suite import SuitePage

        self.mount()
        self.start()
        await SuitePage(self).build()
