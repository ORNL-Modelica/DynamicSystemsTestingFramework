"""Rerun (§4.6 table, §4.7): SUITE and TEST placements. The selection runs
through the configured backend on a worker thread, as a ``Job``.

The job builds its OWN backend, the persistent variant a CLI run would
choose (§4.6 amendment 5) — the instance ``Session.open`` holds is the
batch class it reads results through — and silences it: the tool's process
never narrates a run to the console it was started from. The run's return
is discarded; the Session reloads the results from disk afterwards, which
is what ``run_pipeline`` leaves behind.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...backends import select_backend
from ...pipeline import run_pipeline
from ..capabilities import Job, JobContext, Panel, Placement, plural

if TYPE_CHECKING:
    from ..session import SessionView

# NO module-level `nicegui` import: `import dstf.authoring` runs
# register_builtins(), and the Session must import without the extra.


def _models(s: SessionView) -> list[str]:
    """The selected models in project order — the order the run takes."""
    project = s.project
    if project is None:
        return []
    selection = s.selection.tests
    return [t.model_id for t in project.tests if t.model_id in selection]


class Rerun:
    """The selected tests simulated again, through the configured backend."""

    name = "rerun"
    label = "Rerun"
    placements = frozenset({Placement.SUITE, Placement.TEST})

    def available(self, s: SessionView) -> bool | str:
        if s.project is None:
            return "no project open"
        if not s.selection.tests:
            return "Rerun needs a selection"
        running = s.running
        if running is not None:
            # One job at a time: `Session._start_job` refuses a second one
            # anyway, and the button says so before the click.
            return f"{running.label} is running"
        return True

    def caption(self, s: SessionView) -> str:
        """Count, backend, and the selection's unsaved edits (brief §3.6: a
        run uses the spec with pending edits applied — `stage` re-merges
        `project.tests` — and the button says so)."""
        project = s.project
        backend = project.config.backend_name if project is not None else "?"
        models = set(_models(s))
        text = f"Rerun {plural(len(models), 'selected test')} ({backend})"
        edits = sum(1 for op in s.pending if op.model_id in models)
        if edits:
            text += f" (with {plural(edits, 'unsaved edit')})"
        return text

    def panel(self, s: SessionView, ui_panel: Panel) -> None:
        from nicegui import ui

        project = s.project
        assert project is not None  # `available` greys the button without one
        ui.label(f"runs through {project.config.backend_name}").classes("text-xs").mark(
            "rerun-backend"
        )
        models = _models(s)
        for model_id in models:
            ui.label(model_id).classes("text-xs")
        ui.button(self.caption(s), on_click=lambda: ui_panel.submit()).props(
            "dense no-caps"
        )

    def act(self, s: SessionView, **inputs: Any) -> Job:
        if s.project is None:
            raise ValueError("no project open")
        selection = frozenset(s.selection.tests)

        def run(ctx: JobContext) -> None:
            backend = select_backend(ctx.project.config, persistent=True)
            backend.quiet = True
            tests = [t for t in ctx.project.tests if t.model_id in ctx.models]
            run_pipeline(
                ctx.project.config, tests, backend=backend, store=ctx.project.store
            )

        return Job(label="rerun", models=selection, run=run)
