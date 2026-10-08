"""Queue (D150; brief §3.6): SUITE and TEST placements. The selected tests
are appended to the Session's simulation queue — one per tool process,
first in, first out — as an ``Enqueue``.

The capability starts nothing itself: the Session owns the queue and the
pool it runs on, reads each result back as its test ends and rescores that
test alone. A test already queued or running is not queued twice, and the
caption says how many of the selection that leaves out.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...backends.pool import ItemState
from ..capabilities import Emphasis, Enqueue, Panel, Placement, plural
from ..situation import Situation

if TYPE_CHECKING:
    from ..session import SessionView

# NO module-level `nicegui` import: `import dstf.authoring` runs
# register_builtins(), and the Session must import without the extra.

#: The states of a test the queue already holds.
_HELD = (ItemState.WAITING, ItemState.RUNNING)


def _models(s: SessionView) -> tuple[list[str], list[str]]:
    """The selected models in project order — the order the queue takes —
    as ``(to queue, already queued or running)``."""
    project = s.project
    if project is None:
        return [], []
    selection = s.selection.tests
    new: list[str] = []
    held: list[str] = []
    for test in project.tests:
        if test.model_id in selection:
            (held if s.queue_state(test.model_id) in _HELD else new).append(
                test.model_id
            )
    return new, held


class EnqueueTests:
    """The selected tests appended to the simulation queue. Named for what
    it does, not "queue": that word is the shell's for the queue itself,
    and the shell names no capability (D129)."""

    name = "enqueue"
    label = "Queue"
    placements = frozenset({Placement.SUITE, Placement.TEST})
    #: drawn as this icon where a bar draws icons (the Suite toolbar)
    icon = "playlist_add"
    #: the Test page's strip: the next action when there is no result to
    #: read, a criterion names a variable the last run did not record, or
    #: its last run was cancelled or could not start; in the ⋮ while edits
    #: are pending (their next action is Save) and while the queue holds
    #: the test (the strip's own Remove or Cancel is the action then)
    emphasis = {
        Situation.SIM_FAIL: Emphasis.NEXT,
        Situation.NO_RESULT: Emphasis.NEXT,
        Situation.CANCELLED: Emphasis.NEXT,
        Situation.START_FAILED: Emphasis.NEXT,
        Situation.EDITS: Emphasis.MENU,
        Situation.EDITS_NEW: Emphasis.NEXT,
        Situation.NEW_VARIABLES: Emphasis.NEXT,
        Situation.QUEUED: Emphasis.MENU,
        Situation.RUNNING: Emphasis.MENU,
    }

    def available(self, s: SessionView) -> bool | str:
        if s.project is None:
            return "no project open"
        if not s.selection.tests:
            return "Queue needs a selection"
        refusal = s.queue_refusal
        if refusal is not None:
            # The backend cannot run one test at a time, or cannot be built
            # here: its own sentence, from the first attempt on.
            return refusal
        new, held = _models(s)
        if not new and held:
            if len(held) == 1:
                doing = (
                    "running"
                    if s.queue_state(held[0]) is ItemState.RUNNING
                    else "queued"
                )
                return f"{held[0]} is already {doing}"
            return f"all {len(held)} selected tests are already queued or running"
        return True

    def caption(self, s: SessionView) -> str:
        """The count, how many of the selection the queue already holds
        ("Queue 3 (2 already queued)") and the selection's unsaved edits
        (brief §3.6: a run uses the spec with pending edits applied, and
        the button says so) — as they stand when each test STARTS, which is
        when the queue takes a test's settings. One test whose last run in
        the queue was cancelled or failed reads "Queue again". No backend:
        the header shows it (owner ruling, 2026-09-26); the panel names
        it."""
        new, held = _models(s)
        again = (ItemState.CANCELLED, ItemState.FAILED)
        if len(new) == 1 and not held and s.queue_state(new[0]) in again:
            return "Queue again"
        notes = []
        if held:
            notes.append(f"{len(held)} already queued")
        models = set(new)
        edits = sum(1 for op in s.pending if op.model_id in models)
        if edits:
            notes.append(
                f"with {plural(edits, 'unsaved edit')}, as they stand when each starts"
            )
        return f"Queue {len(new)}" + (f" ({'; '.join(notes)})" if notes else "")

    def panel(self, s: SessionView, ui_panel: Panel) -> None:
        from nicegui import ui

        project = s.project
        assert project is not None  # `available` greys the button without one
        ui.label(f"runs through {project.config.backend_name}").classes("text-xs").mark(
            "queue-backend"
        )
        new, held = _models(s)
        for model_id in new:
            ui.label(model_id).classes("text-xs")
        for model_id in held:
            ui.label(f"{model_id} · already in the queue").classes(
                "text-xs text-secondary"
            )
        ui.button(self.caption(s), on_click=lambda: ui_panel.submit()).props(
            "dense no-caps"
        )

    def act(self, s: SessionView, **inputs: Any) -> Enqueue:
        if s.project is None:
            raise ValueError("no project open")
        new, _held = _models(s)
        return Enqueue(models=tuple(new))
