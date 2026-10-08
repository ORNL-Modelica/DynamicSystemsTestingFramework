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
import socket
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote

from fastapi.responses import FileResponse, JSONResponse
from nicegui import app, ui

from ...backends.pool import ItemState
from ...cli.common import _filter_tests
from ...reporting.theme import level_variables
from ..capabilities import Capability, Placement, plural, registered_capabilities
from ..queue_rows import stop_reason
from ..session import Selection, Session, SessionView
from .style import QUASAR, STYLE
from .tab_theme import TabTheme
from .tabstate import TabState

logger = logging.getLogger(__name__)

TICK = 0.1  # seconds; a Session event reaches every page within one
#: How long the shutdown waits for a drained queue's dashboard render still
#: running (1.6 s over TRANSFORM's work dir with --report sidecars).
SHUTDOWN_WAIT = 5.0


class Page(Protocol):
    """What the shell fans events out to: a live page and its client."""

    client: Any  # nicegui.Client; `is_deleted` prunes the page

    def handle(self, event: dict[str, Any]) -> None: ...

    def flush(self) -> None:
        """After every event of one tick: send what the page coalesced (the
        Suite table sends one transaction per tick, not one per event)."""
        ...


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
        # What other threads post — the simulation pool's callbacks, the
        # rescan's merge: Session mutations that must run on the UI loop.
        # `tick` runs them.
        self._posted: queue.Queue[Callable[[], None]] = queue.Queue()
        self.session = Session(post=self._posted.put)
        # Every Session event, from whichever thread emitted it, crosses here
        # and is fanned out to the pages on the UI loop by `tick`.
        self._events: queue.Queue[dict[str, Any]] = queue.Queue()
        #: The models an open named before its scan finished (its `known`
        #: event), until the merge's `spec` event: the tests while an open
        #: is under way.
        self.known: list[str] = []
        for event in (
            "spec",
            "results",
            "queue",
            "notice",
            "loading",
            "known",
            "overlays",
        ):
            self.session.on(event, self._events.put)
        self.pages: list[Page] = []
        self.load = LoadState()
        self._open_thread: threading.Thread | None = None
        self.children: list[subprocess.Popen[bytes]] = []
        #: (capability name, what) already logged: `available` / `caption` are read
        #: on every refresh, so a raising one is logged once, not per refresh.
        self._reported: set[tuple[str, str]] = set()
        self._mounted = False
        self._timer: Any = None
        self._ticks = 0

    # -- the open ------------------------------------------------------------

    def open_async(self, backend: str | None = None) -> None:
        """Open the project — or reopen it on `backend` (brief §3.6) — on a
        thread; the pages follow the `loading` events and the `opened`
        event this shell adds at the end. Refused while an open runs.
        `Session.open` itself refuses while edits are pending or the queue
        holds a test, and that refusal arrives as the `opened` event's error."""
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
        event to every live page, then let each flush what it coalesced — a
        page whose client is gone is dropped.
        Once a second while a test runs, a `poll` event: the clock a running
        row's elapsed seconds are repainted by. It reads no file — the
        queue's callbacks are the live signal (D150)."""
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
        if self._ticks % every == 0 and self.running():
            events.append({"event": "poll"})
        # `is_deleted` is set by the browser's disconnect (after NiceGUI's
        # reconnect timeout); a page navigated away from in the user
        # simulation is never deleted, so tests pick a page by its client.
        self.pages = [p for p in self.pages if not p.client.is_deleted]
        for event in _folded(events):
            self._absorb(event)
            for page in list(self.pages):
                try:
                    page.handle(event)
                except Exception:  # one page's failure is not another's
                    logger.exception("a page failed on %r", event.get("event"))
        for page in list(self.pages):
            try:
                page.flush()
            except Exception:
                logger.exception("a page failed to flush")

    def _absorb(self, event: dict[str, Any]) -> None:
        """`LoadState` from the events (decision 9)."""
        kind = event["event"]
        load = self.load
        if kind == "known":
            self.known = [str(m) for m in event.get("models", ())]
            return
        if kind == "spec":
            # The merge (the project's tests now), or a close: either way
            # the open's names are done with.
            self.known = []
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
                if not load.in_progress:
                    # A reload after the open — a store write's — announces
                    # the same events, and no `done` follows them: read
                    # here, it left the shell "still loading" for good (the
                    # Task 5 review's probe).
                    return
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
        `available`'s answer for `view`, in registration order. An
        `available` that raises answers with its exception: read on every
        refresh of every page, it must never break the page (a plug-in
        that raised here blanked the Suite page — the Task 7 review)."""
        placed: list[tuple[Capability, bool | str]] = []
        for cap in registered_capabilities():
            if placement not in cap.placements:
                continue
            try:
                answer: bool | str = cap.available(view)
            except Exception as e:  # the plug-in's, shown as its button's reason
                answer = f"{type(e).__name__}: {e}"
                self.report_once(cap.name, "available", e)
            placed.append((cap, answer))
        return placed

    def report_once(self, name: str, what: str, error: Exception) -> None:
        """Log a capability's raising `available` / `caption` with its
        traceback, once per capability and method: the button's why-line
        is the user's signal, the log is the plug-in author's."""
        if (name, what) in self._reported:
            return
        self._reported.add((name, what))
        logger.warning(
            "capability %r: %s raised %s: %s",
            name,
            what,
            type(error).__name__,
            error,
            exc_info=error,
        )

    def view_for(self, selection: Selection) -> SessionView:
        return SessionView(self.session, selection)

    def visible_tests(self) -> list[str]:
        """The project's tests after the CLI's `--filter` / `--package` —
        while an open is under way, the models it named, filtered the same
        way (`_filter_tests` reads nothing but the model id)."""
        project = self.session.project
        if self.known:
            tests: list[Any] = [_Named(m) for m in self.known]
        elif project is not None:
            tests = list(project.tests)
        else:
            return []
        return [t.model_id for t in _filter_tests(tests, self.filter, self.package)]

    def running(self) -> bool:
        """Whether the queue is running a test now."""
        session = self.session
        return any(session.item_state(i) is ItemState.RUNNING for i in session.queue)

    def busy_reason(self) -> str | None:
        """Why a Save, a Discard or a backend change is refused now, or None:
        the open thread is still writing. The queue refuses none of the
        first two (D150): a queued or running test stays editable."""
        if self.load.in_progress:
            return "the project is still loading"
        return None

    def stop_reasons(self) -> list[str]:
        """What Stop server would lose (brief §3.3, §3.6): pending edits, and
        the tests the queue still holds."""
        reasons: list[str] = []
        n = len(self.session.spec.pending)
        if n:
            reasons.append(f"{plural(n, 'unsaved edit')} will be lost")
        held = stop_reason(self.session)
        if held is not None:
            reasons.append(f"{held} — stop anyway?")
        return reasons

    def stop(self) -> None:
        """Stop server: the queue first — its waiting tests removed, its
        running ones stopped, every worker's simulator process ended — then
        the children (decision 7), then NiceGUI."""
        self.session.stop_queue()
        self.terminate_children()
        app.shutdown()

    def shutdown(self) -> None:
        """NiceGUI's shutdown (Stop server, a Ctrl-C): the queue's pool ends
        — a Ctrl-C leaves no simulator process behind either — then a
        drained queue's dashboard render still running off the UI loop
        lands (it is on a daemon thread the process's exit would end
        mid-write), then the children end (decision 7)."""
        self.session.stop_queue()
        if not self.session.wait_background(SHUTDOWN_WAIT):
            logger.warning(
                "exiting with the queue's dashboard render still running after %.0f s",
                SHUTDOWN_WAIT,
            )
        self.terminate_children()

    def terminate_children(self) -> None:
        for child in self.children:
            if child.poll() is None:
                child.terminate()

    def spawn(self, backend: str) -> str:
        """`Open in another backend…` (brief §3.6): a second `dstf author` on
        `backend`, on a free port, from the same config — with this
        process's `--work-dir`, `--filter` and `--package` when it got them
        (decision 6: `--work-dir` means "this work dir"). The child opens its
        own browser tab unless this process was started with `--no-browser`,
        so no popup for the browser to block; the URL is returned for the
        dialog's link. `terminate_children` ends what this started
        (decision 7)."""
        port = _free_port()
        cmd = [
            sys.executable,
            "-m",
            "dstf",
            "--config",
            str(self.config_path),
            "author",
            "--backend",
            backend,
            "--port",
            str(port),
        ]
        if self.work_dir is not None:
            cmd += ["--work-dir", str(self.work_dir)]
        if self.filter:
            cmd += ["--filter", self.filter]
        if self.package:
            cmd += ["--package", self.package]
        if not self.show_browser:
            cmd.append("--no-browser")
        self.children.append(subprocess.Popen(cmd))
        return f"http://127.0.0.1:{port}/"

    # -- the pages ---------------------------------------------------------------

    def page_setup(
        self, state: TabState, switched: Callable[[], None] | None = None
    ) -> TabTheme:
        """Every page starts here: the Quasar palette and the token
        stylesheet from `reporting.theme`, in the tab's theme (`TabTheme`,
        which ``switched`` is handed to), the shell's own on top — one token
        table for the dashboard, the report and the tool (roadmap §1
        predicate 2; the body font is this shell's to apply)."""
        theme = TabTheme(self, state, switched)
        ui.add_css(level_variables() + STYLE + QUASAR)
        return theme

    def mount(self) -> None:
        """The routes beside the index: the Suite table's two scripts, the
        Test page's grips module, the dashboard's files, resolved against the OPEN project's work dir per
        request so a reopen follows, and the Test page. Once per process; `index` calls it too, so a
        simulation that mounted only the root page gets them on its first
        build."""
        if self._mounted:
            return
        self._mounted = True

        # The Suite table's scripts: its own and the typed filters the
        # dashboard inlines (one module, two surfaces).
        from .suite_grid import ASSETS, FILTERS_JS, HERE

        app.add_static_file(
            local_file=HERE / "suite_grid.js", url_path=f"{ASSETS}/suite_grid.js"
        )
        app.add_static_file(local_file=FILTERS_JS, url_path=f"{ASSETS}/filters.js")
        # The Test page's: the tolerance grips' hover (`grips.js`).
        from .plot_card import GRIPS_JS, GRIPS_URL

        app.add_static_file(local_file=GRIPS_JS, url_path=GRIPS_URL)

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


def _is_scan_step(event: dict[str, Any]) -> bool:
    return event["event"] == "loading" and event.get("phase") == "scan"


def _folded(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``events`` with each run of adjacent scan steps reduced to its last:
    a step carries the running count, so the last says all the run does.
    One by one, every step synced an open Test page in full and re-read
    `status.json` in its header: one tick over 3,269 queued steps
    (TRANSFORM's .mo count, a TRANSFORM-sized `status.json`, one Test page)
    held the UI loop about 13 s, and 0.02 s folded."""
    out: list[dict[str, Any]] = []
    for event in events:
        if out and _is_scan_step(event) and _is_scan_step(out[-1]):
            out[-1] = event
        else:
            out.append(event)
    return out


@dataclass(frozen=True)
class _Named:
    """A model id where `_filter_tests` wants a test."""

    model_id: str


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
