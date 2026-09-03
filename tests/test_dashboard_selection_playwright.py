"""Browser tests for the dashboard's selection → rerun-command path.

The owner reported a leak: tests selected in one report showed up in the
rerun command built in the NEXT report opened from the same work dir.
Cause: the dashboard persists filter / sort / selection to localStorage
keyed by ``location.pathname``, and two successive runs into the same
work_dir write the same ``dashboard.html`` — so run N's selection was
restored into run N+1's page and joined whatever the user selected there.

These tests drive two renders of the same path with different
``start_wall`` values (= two runs) through one browser context, which is
what shares the localStorage.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

pytestmark = pytest.mark.playwright

from dstf.reporting.dashboard_render import render_final  # noqa: E402

_MODELS = ["Lib.A", "Lib.B", "Lib.C"]


def _snapshot(start_wall: float) -> dict:
    return {
        "total": len(_MODELS),
        "elapsed": 5.0,
        "eta_seconds": None,
        "start_wall": start_wall,
        "updated_at": start_wall + 5.0,
        "rerun_prefix": "dstf --config testing.json run",
        "counts": {"passed": len(_MODELS)},
        "tests": [
            {
                "test_key": f"test_{i:04d}",
                "model_id": m,
                "status": "passed",
                "elapsed": 1.0,
                "worker_id": 0,
                "report_dir": f"test_{i:04d}",
            }
            for i, m in enumerate(_MODELS, 1)
        ],
    }


def _render(work_dir: Path, start_wall: float) -> Path:
    (work_dir / "status.json").write_text(
        json.dumps(_snapshot(start_wall)), encoding="utf-8"
    )
    render_final(work_dir)
    return work_dir / "dashboard.html"


def _select(page, model: str) -> None:
    page.click(f'tr[data-model="{model}"] input.row-sel')


def test_selection_does_not_leak_across_runs(tmp_path, playwright_browser):
    """Select in run 1, re-render the SAME path as run 2, select something
    else: the rerun command must name only run 2's pick.

    Mutation check for this test: drop the ``s.selectionKey === REPORT_KEY``
    guard in dashboard.js `restoreState` and it fails with
    "Lib.A,Lib.C" — the pre-fix behavior the owner reported.
    """
    work_dir = tmp_path
    ctx = playwright_browser.new_context()
    page = ctx.new_page()

    # --- run 1: select Lib.A -------------------------------------------
    html = _render(work_dir, start_wall=1000.0)
    page.goto(html.as_uri())
    _select(page, "Lib.A")
    assert "Lib.A" in page.input_value("#cmd-out")

    # --- run 2: same work_dir, same URL, new run -----------------------
    html = _render(work_dir, start_wall=2000.0)
    page.goto(html.as_uri())
    # Nothing may be pre-selected: the stored selection belongs to run 1.
    assert page.input_value("#cmd-out") == ""
    assert page.locator("tr.row-selected").count() == 0

    _select(page, "Lib.C")
    cmd = page.input_value("#cmd-out")
    ctx.close()

    assert "Lib.C" in cmd
    assert "Lib.A" not in cmd, (
        f"Run 1's selection leaked into run 2's rerun command: {cmd!r}. "
        "The persisted selection must be scoped to the report it was made in."
    )


def test_selection_survives_a_reload_of_the_same_run(tmp_path, playwright_browser):
    """The guard must not break the feature it protects: live mode reloads
    the page every 5s via meta-refresh, and the selection has to survive
    that. Same run (same start_wall) → selection is restored.
    """
    work_dir = tmp_path
    ctx = playwright_browser.new_context()
    page = ctx.new_page()

    html = _render(work_dir, start_wall=1000.0)
    page.goto(html.as_uri())
    _select(page, "Lib.B")

    # Same run re-rendered (what a live-mode refresh tick does).
    html = _render(work_dir, start_wall=1000.0)
    page.goto(html.as_uri())
    cmd = page.input_value("#cmd-out")
    n_selected = page.locator("tr.row-selected").count()
    ctx.close()

    assert n_selected == 1
    assert "Lib.B" in cmd


def test_report_key_changes_between_runs(tmp_path):
    """The identity the guard keys on must actually differ per run (and
    stay put within one). Pure-Python guard for the JS above.
    """
    from dstf.reporting.dashboard_render import build_dashboard_context

    (tmp_path / "status.json").write_text(
        json.dumps(_snapshot(1000.0)), encoding="utf-8"
    )
    first = build_dashboard_context(tmp_path, mode="final")["report_key"]
    again = build_dashboard_context(tmp_path, mode="final")["report_key"]
    (tmp_path / "status.json").write_text(
        json.dumps(_snapshot(2000.0)), encoding="utf-8"
    )
    second = build_dashboard_context(tmp_path, mode="final")["report_key"]

    assert first == again, "same run must keep the same key across renders"
    assert first != second, "a new run must get a new key"
    assert str(tmp_path.resolve().as_posix()) in first
