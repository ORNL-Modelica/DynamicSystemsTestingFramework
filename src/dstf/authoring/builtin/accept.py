"""Accept (§4.6 table, §4.7): SUITE and TEST placements. The selected tests'
current results become baselines through
``storage.accept.accept_run_results`` — the one function the CLI's two
``--accept`` paths call — so the tool cannot accept a result the CLI would
refuse.

The confirm list says, per selected model, what accepting will do with it,
including the aborted-run refusal and the comparison's own (a base tolerance
that is no number, in the comparison's sentence), so it never promises what the shared
function declines — with one exception it cannot see from here: a trajectory
carrying NaN/Inf is refused by ``store_reference`` as it writes, and shows up
in the write's own line instead. The button's own availability is read off
those same rows, so an enabled Accept always has at least one baseline to
write. The write reports one line (§4.6 amendment 1): what it stored, and
every refusal with its reason.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...backends.pool import ItemState
from ...reporting.status import classify, refusal
from ...storage.accept import accept_run_results
from ...storage.batch_manifest import unfinished_run_models
from ..capabilities import Emphasis, Panel, Placement, StoreWrite, plural
from ..situation import Situation

if TYPE_CHECKING:
    from pathlib import Path

    from ...storage.reference_store import ReferenceStore
    from ..session import SessionView

# NO module-level `nicegui` import: `import dstf.authoring` runs
# register_builtins(), and the Session must import without the extra.

#: The confirm row's verdict on a model — every word but the last is a
#: reason the shared accept function stores no baseline for it; the last is
#: what it stores. ``WILL_STORE`` is the one truth the button's count, its
#: availability and this forecast share.
REFUSED = "re-run before accepting"
NO_RESULT = "no simulation result"
SIM_FAILED = "simulation failed"
SIMULATE_ONLY = "simulate-only: no baseline"
NO_VARIABLES = "no tracked variables in result"
WILL_STORE = "will store"

#: The states of a test the simulation queue holds.
_HELD = (ItemState.WAITING, ItemState.RUNNING)


def _rows(s: SessionView) -> list[tuple[str, str, str]]:
    """``(model_id, status, what accept will do)`` for every selected model,
    in project order."""
    project = s.project
    if project is None:
        return []
    selection = s.selection.tests
    work_dir = project.config.work_dir
    assert work_dir is not None  # resolved in Config.__post_init__
    unfinished = unfinished_run_models(work_dir)
    rows: list[tuple[str, str, str]] = []
    for test in project.tests:
        model_id = test.model_id
        if model_id not in selection:
            continue
        comp = s.comparisons.get(model_id)
        status = classify(comp) if comp is not None else ""
        result = project.results.get(model_id)
        if model_id in unfinished:
            action = REFUSED  # the guard runs before the result is looked at
        elif result is None:
            action = NO_RESULT
        elif not result.success:
            action = SIM_FAILED
        elif comp is not None and (refused := refusal(comp)) is not None:
            # A test the comparison refused: the row says the comparison's
            # own sentence, which `accept_run_results` skips it with.
            action = refused
        elif test.simulate_only:
            # `accept_results` neither stores nor skips these, by design.
            action = SIMULATE_ONLY
        elif not result.variables:
            # `store_reference` returns False for a variable-less result and
            # `accept_results` skips it with exactly this reason.
            action = NO_VARIABLES
        else:
            action = WILL_STORE
        rows.append((model_id, status, action))
    return rows


def _report(stored: int, skipped: list[tuple[str, str]], *, reference_dir: Path) -> str:
    """The one line the queue panel shows. A partial accept has no other way
    to say which tests it refused and why."""
    parts = [f"Accept: stored {stored} baseline(s) to {reference_dir}"]
    parts += [f"; skipped {model_id}: {reason}" for model_id, reason in skipped]
    return "".join(parts)


class Accept:
    """The selection's current results as baselines, comparison settings
    travelling with them as the CLI's accept does."""

    name = "accept"
    label = "Accept"
    placements = frozenset({Placement.SUITE, Placement.TEST})
    #: drawn as this icon where a bar draws icons (the Suite toolbar)
    icon = "done_all"
    #: the Test page's strip: the next action for a result that differs
    #: from its baseline or has none; nothing new to accept otherwise
    emphasis = {
        Situation.FAIL: Emphasis.NEXT,
        Situation.NO_REF: Emphasis.NEXT,
        Situation.PASS: Emphasis.MENU,
        Situation.EDITS: Emphasis.MENU,
        # a criterion's variable to record first: accepted now, the
        # baseline would not hold it
        Situation.EDITS_NEW: Emphasis.MENU,
        Situation.NEW_VARIABLES: Emphasis.MENU,
        Situation.SIM_FAIL: Emphasis.MENU,
        Situation.NO_RESULT: Emphasis.MENU,
        Situation.QUEUED: Emphasis.MENU,
        Situation.RUNNING: Emphasis.MENU,
        Situation.CANCELLED: Emphasis.MENU,
        Situation.START_FAILED: Emphasis.MENU,
    }

    def available(self, s: SessionView) -> bool | str:
        project = s.project
        if project is None:
            return "no project open"
        if not s.selection.tests:
            return "Accept needs a selection"
        selection = s.selection.tests
        for test in project.tests:
            # The Session's refusal (`apply`), said before the click: the
            # result in memory is about to be replaced by its run's.
            state = s.queue_state(test.model_id)
            if test.model_id in selection and state in _HELD:
                doing = "running" if state is ItemState.RUNNING else "queued"
                return f"{test.model_id} is {doing}: accept it when its run has ended"
        # The button's answer comes from the rows the panel draws, so an
        # enabled button never reads `Accept 0`: a pending, simulate-only or
        # variable-less result is a row the write will not store, and the
        # button carries that row's reason.
        rows = _rows(s)
        if any(action == WILL_STORE for _, _, action in rows):
            return True
        if not rows:
            return "no successful result in the selection"
        return rows[0][2]

    def caption(self, s: SessionView) -> str:
        count = sum(1 for _, _, action in _rows(s) if action == WILL_STORE)
        return f"Accept {plural(count, 'result')} as baselines"

    def panel(self, s: SessionView, ui_panel: Panel) -> None:
        from nicegui import ui

        rows = _rows(s)
        for model_id, status, action in rows:
            ui.label(f"{model_id} · {status} · {action}").classes("text-xs")
        ui.button(self.caption(s), on_click=lambda: ui_panel.submit()).props(
            "dense no-caps"
        )

    def act(self, s: SessionView, **inputs: Any) -> StoreWrite:
        project = s.project
        if project is None:
            raise ValueError("no project open")
        selection = set(s.selection.tests)
        # Everything the write needs is read HERE: `run` is handed a store
        # by the Session and holds no view.
        tests = [t for t in project.tests if t.model_id in selection]
        results = dict(project.results)
        work_dir = project.config.work_dir
        assert work_dir is not None  # resolved in Config.__post_init__
        reference_dir = project.config.reference_dir

        def run(store: ReferenceStore) -> str:
            return _report(
                *accept_run_results(store, tests, results, work_dir),
                reference_dir=reference_dir,
            )

        return StoreWrite(models=frozenset(selection), run=run)
