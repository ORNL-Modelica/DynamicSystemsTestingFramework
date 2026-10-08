"""The header both pages share (DESIGN.md "Header"; brief §3.3): "DSTF", the
library and the backend's name on the left; the queue indicator in the
centre; the pending count with Save and Discard on the right, and a ⋮ menu
at the end holding the rarely used actions — Export, Change backend…, Open
in another backend…, the dashboard and Stop server, each with its full
label. At 900 px the row cut off "dashboard" and "Stop server", and the
owner chose one layout at every width (3c, Q10 A) over a row that changes
with it. The backend was a select in the row until a rerun running, an
edit pending and a caption naming a new entry put Save on four lines at
900 px: the owner moved it into the menu (Q13 A), and the row names the
backend as text. A button may take two lines at 900 px and one at 1280.
Every button's label says its object, effect and count; a disabled one
carries the sentence that says why (DESIGN.md "Disabled states explain
themselves"). Off-white with a hairline below, never an accent bar.

A page may ask for a second row of its own (``below``): it is drawn INSIDE
this fixed header, which is what makes it sticky without offset arithmetic
(decision 8). The Test page's header is the one. The Test page also asks
for a third row (``strip``), drawn inside it for the same reason: its status
strip, the test's situation and its next action."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from nicegui import ui

from ...backends.progress import read_status
from ...reporting.dashboard_render import format_run_metadata, short_tool_version
from ...reporting.status import SCORING_FAILED, classify
from ...storage.ref_index import _atomic_write_text
from ..capabilities import plural
from ..errors import user_text
from ..queue_rows import stop_reason
from ..session import StaleSpecError
from ..situation import SEP, verdict_phrase
from ..spec_document import PatchOp
from ..spec_preview import entry_lines, spans
from .actions import log_unless_users
from .queue_panel import QueueIndicator
from .widgets import Action

if TYPE_CHECKING:
    from .shell import Shell

logger = logging.getLogger(__name__)


def save_caption(pending: Sequence[PatchOp], loaded: dict, file_name: str) -> str:
    """The Save button's caption (brief §3.3: object, effect, count). A
    pending edit to a model with no entry in the file as LOADED creates
    that entry on Save, and the caption says so (the owner's ruling,
    2026-09-25): a layout staged for a test tracked only by its `.mo`
    annotation writes that test's first entry. Counted over ``loaded``,
    never the effective document, where the pending op has already made
    the entry."""
    from ...discovery.spec_update import spec_entry

    models = list(dict.fromkeys(op.model_id for op in pending))
    new = sum(1 for m in models if spec_entry(loaded, m) is None)
    tests = plural(len(models), "test")
    what = f"{tests}, {plural(new, 'new entry', 'new entries')}" if new else tests
    return f"Save {plural(len(pending), 'spec edit')} ({what}) to {file_name}"


def backend_caption(
    backend_name: str | None, os_name: str | None, metadata: dict | None
) -> tuple[str, str]:
    """The header's backend text and its tooltip. The text names the tool's
    version the last run recorded, short ("Dymola 2026x · linux"), when the
    configured backend is the one that ran; a backend that has not run here
    reads its name, its version unknown until it runs. The tooltip says
    whose the version is: the LAST RUN's, in full (``metadata`` is
    status.json's)."""
    meta = format_run_metadata(metadata)
    name = backend_name
    if meta is None:
        tip = "No run here has recorded the tool's version yet"
    else:
        ran = meta["tool_version"] or meta["label"]
        tip = "Last run: " + " · ".join(
            t
            for t in (
                ran,
                meta["msl"] and f"MSL {meta['msl']}",
                meta["os"],
                meta["generated_str"],
            )
            if t
        )
        if metadata and metadata.get("backend_name") == backend_name:
            name = short_tool_version(meta["tool_version"], backend_name)
        else:
            tip = (
                f"{backend_name} has not run here: its version is known once "
                f"it runs. {tip}"
            )
    return " · ".join(t for t in (name, os_name) if t), tip


#: The role every item of the header's menu carries (see `Header`).
MENUITEM = 'role="menuitem"'


class Header:
    def __init__(
        self,
        shell: Shell,
        below: Callable[[], None] | None = None,
        strip: Callable[[], None] | None = None,
    ) -> None:
        self.shell = shell
        self.drawer = PendingDrawer(shell, save=self._save, discard=self._discard)
        self.dialog = ui.dialog()  # the header's dialogs, one element rebuilt per use
        # The backend's own dialog, built once: `refresh` keeps its select's
        # options and value as the row's text, open or closed.
        with ui.dialog() as self.backend_dialog, ui.card():
            ui.label("Change backend").classes("text-h6")
            ui.label(
                "Reopens this project on the chosen backend, in this tab; its "
                "results and baselines are that backend's."
            ).classes("text-xs text-secondary")
            self.backend = (
                ui.select([], label="backend", on_change=self._change_backend)
                .props("dense outlined options-dense")
                .mark("backend")
            )
            ui.button("Close", on_click=self.backend_dialog.close).props(
                "flat dense no-caps"
            )
        with ui.header().classes("dstf-header column no-wrap gap-0") as bar:
            row = ui.row().classes("dstf-header-row items-center no-wrap gap-3 w-full")
        with row:
            ui.label("DSTF").classes("dstf-wordmark").mark("wordmark")
            self.library = ui.label().classes("text-secondary").mark("project")
            self.backend_name = (
                ui.label().classes("text-secondary").mark("backend-name")
            )
            with self.backend_name:
                self.backend_tip = ui.tooltip("").mark("backend-name:tip")
            ui.space()
            self.queue = QueueIndicator(shell)
            ui.space()
            self.pending = (
                ui.button(on_click=self.drawer.toggle)
                .props("flat dense no-caps no-wrap")
                .mark("pending")
            )
            self.save = Action(
                self._save,
                props="dense no-caps color=primary",
                marker="save",
                filled=True,
            )
            self.discard = Action(
                self._discard, props="flat dense no-caps", marker="discard", why=False
            )
            # The ⋮ button says it opens a menu and whether it is open; the
            # items are `menuitem`s of that menu (Quasar draws a `q-item` as
            # a `listitem`, and the attribute given here is what the browser
            # keeps).
            self.more = (
                ui.button(icon="more_vert")
                .props(
                    'flat dense round aria-label="More actions" aria-haspopup="menu" '
                    'aria-expanded="false"'
                )
                .classes("dstf-header-menu")
                .mark("header-menu")
            )
            with self.more, ui.menu() as menu:
                menu.on_value_change(self._menu_toggled)
                # A `ui.menu_item` has no text of its own to set: Export's
                # caption is a label in it, and the reason it is disabled a
                # second line under that.
                with (
                    ui.menu_item(on_click=self._export)
                    .props(MENUITEM)
                    .mark("export") as export
                ):
                    self.export = export
                    with ui.item_section():
                        self.export_text = ui.item_label()
                        self.export_why = (
                            ui.item_label().props("caption").mark("export:why")
                        )
                # Changed rarely, and refused while anything is pending or
                # running: in the row, its select and why-sentence pushed
                # Save onto four lines at 900 px (the owner's ruling, Q13 A).
                with (
                    ui.menu_item(on_click=self.backend_dialog.open)
                    .props(MENUITEM)
                    .mark("change-backend") as change
                ):
                    self.change = change
                    with ui.item_section():
                        ui.item_label("Change backend…")
                        self.backend_why = (
                            ui.item_label().props("caption").mark("backend:why")
                        )
                self.other = (
                    ui.menu_item("Open in another backend…", on_click=self._open_other)
                    .props(MENUITEM)
                    .mark("other-backend")
                )
                # A link the browser follows into a new tab: a `window.open`
                # sent from the server (`ui.navigate.to(..., new_tab=True)`)
                # is what a popup blocker may refuse.
                ui.menu_item("dashboard").props(
                    f'href="/dashboard/dashboard.html" target="_blank" {MENUITEM}'
                ).mark("dashboard")
                self.stop = (
                    ui.menu_item("Stop server", on_click=self._stop)
                    .props(MENUITEM)
                    .classes("text-negative")
                    .mark("stop")
                )
        if below is not None:
            # A second row INSIDE the fixed header (decision 8): sticky with
            # no offset arithmetic and no dependence on the first row's
            # height. The Test page's own header is what `below` draws.
            with bar:
                sub = ui.row().classes("dstf-subheader items-center gap-3 w-full")
            with sub.mark("subheader"):
                below()
        if strip is not None:
            # A third row, under the second: the Test page's status strip,
            # sticky for the same reason. It draws its own row.
            with bar:
                strip()
        self.refresh()

    def handle(self, event: dict[str, Any]) -> None:
        self.refresh()

    def _menu_toggled(self, e: Any) -> None:
        self.more.props(f'aria-expanded="{"true" if e.value else "false"}"')

    def refresh(self) -> None:
        shell, s = self.shell, self.shell.session
        project, load = s.project, shell.load
        if project is not None:
            self.library.set_text(project.config.library_name or "")
            config = project.config
            snapshot = read_status(config.work_dir) if config.work_dir else None
            text, tip = backend_caption(
                config.backend_name,
                config.os_name,
                (snapshot or {}).get("metadata"),
            )
            self.backend_name.set_text(text)
            self.backend_tip.set_text(tip)
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
            self.backend_name.set_text("")
            self.backend_tip.set_text("No project is open")
        why = self._backend_why()
        self.change.set_enabled(why is None)
        self.backend.set_enabled(why is None)
        self.backend_why.set_text(why or "")
        self.backend_why.set_visibility(why is not None)
        self.other.set_enabled(project is not None)
        self.queue.refresh()
        n = len(s.spec.pending)
        self.pending.set_text(plural(n, "pending edit"))
        reason = "no pending edits" if n == 0 else shell.busy_reason()
        self.save.set(
            save_caption(s.spec.pending, s.spec.loaded, s.spec.path.name), reason
        )
        self.discard.set(f"Discard {plural(n, 'edit')}", reason)
        # The drawer's own Save and Discard, gated as these are — here, not
        # in its build: that runs only when what the preview shows moved,
        # and the load moves nothing it shows.
        self.drawer.save.set("Save these", reason)
        self.drawer.discard.set("Discard these", reason)
        self.export_text.set_text(f"Export {plural(n, 'edit')} to spec_patch.json")
        self.export.set_enabled(n > 0)
        self.export_why.set_text("no pending edits" if n == 0 else "")
        self.export_why.set_visibility(n == 0)
        self.drawer.refresh()

    # -- the backend -------------------------------------------------------------

    def _backend_why(self) -> str | None:
        """Why the backend cannot change now (brief §3.6: refused while edits
        are pending or the queue holds a test), or None."""
        s = self.shell.session
        if s.project is None:
            return "no project open"
        busy = self.shell.busy_reason() or stop_reason(s)
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
        self.backend_dialog.close()
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
                    try:
                        self.shell.session.reload()
                    except Exception as e:  # said in the dialog's place, never raised
                        log_unless_users(e, "reload")
                        ui.notify(user_text(e), type="warning")
                        return
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
        try:
            self.shell.session.discard()
        except Exception as e:
            log_unless_users(e, "discard")
            ui.notify(user_text(e), type="warning")

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
        """Stop server (brief §3.3, §3.6): at once with nothing to lose; else
        a confirm naming what would be lost — unsaved edits, and the tests
        the queue still holds, which stopping removes or cancels."""
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


#: The event Esc sends to the drawer's column, and the one listener that
#: sends it — installed once per page load, when the drawer first opens. A
#: keydown on the document, not on the drawer: focus is where the click that
#: opened it left it (the header's button, the strip's). Sent to an element
#: the drawer built with its listener on, never through `ui.on`: that adds a
#: listener to the page's layout, which on the Suite page is already drawn
#: when the header is built, and NiceGUI says so in the browser's console.
#: An Esc while a dialog or a menu is open is theirs (Quasar closes it): the
#: drawer under it stays. One already closing is not open: it stays in the
#: page for its leave animation (~300 ms (Quasar's default
#: transition-duration); Quasar's `-leave-` classes, on a dialog's
#: `.q-dialog__inner`), and a guard that counted it swallowed a second Esc
#: 40 ms after the first.
ESC_EVENT = "dstf_pending_esc"
ESC_JS = f"""
window.__dstfPendingEsc ??= (document.addEventListener("keydown", (e) => {{
  if (e.key !== "Escape") return;
  const open = [...document.querySelectorAll(".q-dialog, .q-menu")].some(
    (el) => !/-leave-/.test((el.querySelector(".q-dialog__inner") ?? el).className));
  if (open) return;
  document.querySelector(".dstf-pending-body")
    ?.dispatchEvent(new CustomEvent("{ESC_EVENT}"));
}}), true);
"""

#: A diff line's prefix, as `git diff` and the board print it.
_PREFIX = {"same": "  ", "add": "+ ", "del": "- ", "gap": "  "}


def materialized_note(leaves: int, new_entry: bool) -> str:
    """The note over a test whose edits begin with its implicit tree written
    out (`PatchOp.materializes`): the diff is against that tree, and Save
    writes it too."""
    they = "it scores" if leaves == 1 else "they score"
    note = (
        f"Save also writes this test's {plural(leaves, 'criterion', 'criteria')} "
        f"out as a tree; {they} the same."
    )
    return f"A new entry for this test. {note}" if new_entry else note


def _leaves(node: Any) -> int:
    """How many criteria a raw tree node holds."""
    if isinstance(node, dict) and isinstance(node.get("children"), list):
        return sum(_leaves(child) for child in node["children"])
    return 1


class PendingDrawer:
    """The pending-edit drawer is the preview (the owner's ruling Q6 A): per
    test, in staging order, its entry before and after its staged edits as
    `test_spec.json` will hold it — added lines green, removed lines red,
    each variable read by its label with its result-file name muted beside
    it — with how many edits it has and what they do to its verdict. The
    `before` is the tree as scoring sees it (`SpecDocument.preview_base`):
    a flat entry's first per-leaf edit writes its implicit tree out, and
    the diff shows the edit, with a note that Save writes the tree too.
    "Save these" and "Discard these" are the header's own Save and Discard
    (the stale-file dialog included), greyed when they are and for the same
    reason. Toggled by the header's pending button; `show(model)` opens it
    on one test's section (the strip's Preview and save). Its close button
    and Esc close it: under 1024 px it is an overlay over the header and
    the strip, and a click on the page behind was the only other way out.

    Built only while open: the header refreshes on every event (the 1 s
    poll during a job included), and a drawer rebuilt there scored every
    edited test's saved spec each time and put every diff back at its left
    edge. Opening it builds it; while open it builds again only when what
    it shows moved (`_key`). Closing it forgets what it drew."""

    WIDTH = 520

    def __init__(
        self,
        shell: Shell,
        save: Callable[[], None],
        discard: Callable[[], None],
    ) -> None:
        self.shell = shell
        self.element = (
            ui.right_drawer(value=False)
            .props(f"bordered width={self.WIDTH}")
            .classes("dstf-pending-drawer")
        )
        with (
            self.element,
            ui.column().classes("dstf-pending-body w-full min-w-0 gap-3") as body,
        ):
            with ui.row().classes("w-full items-center no-wrap gap-2"):
                self.summary = ui.label().classes("text-bold").mark("drawer-summary")
                ui.space()
                ui.button(icon="close", on_click=self.close).props(
                    'flat dense round aria-label="Close"'
                ).mark("drawer-close")
            self.body = ui.column().classes("w-full min-w-0 gap-4")
            with ui.row().classes("gap-2") as self.buttons:
                # Captions and reasons are the header's (`Header.refresh`).
                self.save = Action(
                    save,
                    props="dense no-caps color=primary",
                    marker="drawer-save",
                    filled=True,
                )
                self.discard = Action(
                    discard,
                    props="flat dense no-caps",
                    marker="drawer-discard",
                    why=False,
                )
        self.sections: dict[str, ui.element] = {}
        #: What the drawn preview was built from (`_key`); None: not built.
        self.drawn: object = None
        #: The comparisons whose ids `drawn` records, held while it does: a
        #: freed one's id can be reused by the next, and the key would miss
        #: the verdict it brought.
        self.held: list[Any] = []
        self.element.on_value_change(self._toggled)
        body.on(ESC_EVENT, lambda _: self.close())

    def _toggled(self, e: Any) -> None:
        if e.value:
            ui.run_javascript(ESC_JS)
            self.refresh()
        else:
            # Closed, it follows nothing: its next open builds again.
            self.drawn = None
            self.held = []

    def toggle(self) -> None:
        self.element.toggle()

    def close(self) -> None:
        self.element.set_value(False)

    def show(self, model_id: str) -> None:
        """Open the drawer with ``model_id``'s section scrolled into view
        (the strip's Preview and save)."""
        self.element.set_value(True)
        self.refresh()
        section = self.sections.get(model_id)
        if section is not None:
            ui.run_javascript(
                f'document.getElementById("{section.html_id}")'
                '?.scrollIntoView({block: "start"})'
            )

    def _key(self) -> tuple[object, list[Any]]:
        """What the preview shows moves with: the pending ops, the file, and
        each edited test's verdict (a rescore replaces the comparison) —
        and the comparisons it names by id, to hold."""
        session = self.shell.session
        spec = session.spec
        edited = list(dict.fromkeys(op.model_id for op in spec.pending))
        comps = [session.comparisons.get(m) for m in edited]
        key = (
            tuple(spec.pending),
            spec.content_hash,
            tuple((m, id(c)) for m, c in zip(edited, comps, strict=True)),
        )
        return key, comps

    def refresh(self) -> None:
        """Build the preview again — only while the drawer is open, and only
        when what it shows moved."""
        if not self.element.value:
            return
        key, comps = self._key()
        if key == self.drawn:
            return
        self.drawn = None  # a raise below leaves it to be built again
        self._build()
        self.drawn, self.held = key, comps

    def _saved_statuses(self, models: list[str]) -> dict[str, str | None]:
        """The classifier's word for each test as SAVED, the saved spec
        merged once for all of them. A saved spec whose scoring raises for
        one test says ``scoring failed`` for that test alone (as the strip
        does), the others scored one by one."""
        session = self.shell.session

        def word(comp: Any) -> str | None:
            return classify(comp) if comp is not None else None

        try:
            saved = session.saved_comparisons(models)
        except Exception:
            logger.exception("the saved verdicts of the edited tests")
        else:
            return {m: word(saved[m]) for m in models}
        out: dict[str, str | None] = {}
        for m in models:
            try:
                out[m] = word(session.saved_comparison(m))
            except Exception:
                out[m] = SCORING_FAILED
        return out

    def _build(self) -> None:
        session = self.shell.session
        spec = session.spec
        groups = spec.grouped()
        self.body.clear()
        self.sections = {}
        self.summary.set_text(
            f"{plural(len(spec.pending), 'pending edit')} · "
            f"{plural(len(groups), 'test')}"
        )
        self.summary.set_visibility(bool(groups))
        self.buttons.set_visibility(bool(groups))
        with self.body:
            if not groups:
                ui.label("nothing staged").classes("text-secondary")
                return
            saved = self._saved_statuses([m for m, _ in groups])
            for model_id, ops in groups:
                comp = session.comparisons.get(model_id)
                with (
                    ui.column()
                    .classes("dstf-drawer-section w-full min-w-0 gap-1")
                    .mark(f"drawer-test:{model_id}") as section
                ):
                    self.sections[model_id] = section
                    self._section(model_id, len(ops), comp, saved[model_id])

    def _section(
        self, model_id: str, n: int, comp: Any, saved_status: str | None
    ) -> None:
        session = self.shell.session
        spec = session.spec
        ui.label(model_id).classes("text-bold dstf-mono").mark("drawer-model")
        # One sentence, as the strip's: a plain block of inline labels, its
        # separator a label of its own, whose spaces stand between the words
        # as any inline text's do. Not a flex row: its items are blocks, and
        # a copy or a screen reader reads one per line.
        with ui.element("div").classes("dstf-drawer-status text-xs text-secondary"):
            ui.label(plural(n, "edit")).classes("inline").mark("drawer-count")
            ui.label(SEP).classes("inline")
            ui.label(
                verdict_phrase(
                    classify(comp) if comp is not None else None, saved_status
                )
            ).classes("inline").mark("drawer-verdict")
        trees = [
            op.value
            for op in spec.pending
            if op.model_id == model_id and op.materializes
        ]
        if trees:
            ui.label(
                materialized_note(
                    sum(_leaves(t) for t in trees),
                    spec.saved_entry(model_id) is None,
                )
            ).classes("text-xs text-secondary").mark("drawer-note")
        # Never an empty box: edits that cancel out are not here at all, and
        # neither is a tree written out and not edited (a `rescore`, a value
        # typed and cleared on a flat test) — an entry put back as saved has
        # no pending op, and the tree it already scores by is none of the
        # ops it has (`Session.stage`).
        lines = entry_lines(spec.preview_base(model_id), spec.entry(model_id))
        texts = session.variable_texts(model_id)
        with (
            ui.element("div").classes("dstf-diff").mark("drawer-diff"),
            ui.element("div").classes("dstf-diff-lines"),
        ):
            for line in lines:
                with (
                    ui.element("div")
                    .classes(f"dstf-diff-line dstf-diff-{line.kind}")
                    .mark(f"diff-{line.kind}")
                ):
                    ui.label(_PREFIX[line.kind]).classes("dstf-diff-text")
                    for text, muted in spans(line.text, texts):
                        if muted:
                            ui.label(text).classes("dstf-diff-muted").mark("diff-muted")
                        else:
                            ui.label(text).classes("dstf-diff-text")
