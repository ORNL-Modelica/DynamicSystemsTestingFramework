"""The runs card (§4.7 "the job panel"; brief §3.6 puts the queue panel on
the Suite page, and sub-project 5 replaces this card with it): the running
job's per-test progress from `status.json`, else the last job's outcome
and what its results say went wrong, then the store writes' lines and the
artifacts. Nothing here may raise — a malformed snapshot renders what it
has."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from nicegui import ui

from ...backends.progress import read_status
from ..session import RunningJob

if TYPE_CHECKING:
    from .shell import Shell


class RunsCard:
    def __init__(self, shell: Shell) -> None:
        self.shell = shell
        self.container = ui.column().classes("dstf-card w-full gap-1").mark("runs-card")
        self.refresh()

    def refresh(self) -> None:
        self.container.clear()
        with self.container:
            self._draw()

    def _draw(self) -> None:
        session = self.shell.session
        job = session.running
        last = session.last_job
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
        for notice in session.notices:
            ui.label(notice).classes("text-xs")
        for artifact in session.artifacts:
            for path in artifact.paths:
                ui.link(f"{artifact.label}: {path.name}", path.as_uri(), new_tab=True)

    def _progress_rows(self, job: RunningJob) -> None:
        """One row per scoped test in the snapshot — but only a snapshot this
        job wrote: a ``start_wall`` older than the job's start belongs to the
        previous run (the same guard `dashboard_render` applies to a sidecar).
        Every shape here is whatever a file on disk held."""
        project = self.shell.session.project
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
        per model it ran (§4.7), in project order. Only ever called for a job
        whose reload ran, so every row here is that job's own."""
        project = self.shell.session.project
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
