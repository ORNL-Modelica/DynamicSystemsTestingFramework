"""Tests for the unified dashboard renderer."""

import json
from pathlib import Path

from dstf.reporting.dashboard_render import (
    build_dashboard_context,
    render_final,
    render_live,
)


def _write_status_json(work_dir: Path, snapshot: dict) -> None:
    (work_dir / "status.json").write_text(json.dumps(snapshot), encoding="utf-8")


def test_build_context_live_only(tmp_path):
    """Live snapshot (no comparison data yet): rows have status/elapsed
    populated; NRMSE/warnings columns are None; auto_refresh=True."""
    snapshot = {
        "total": 2,
        "elapsed": 5.0,
        "eta_seconds": None,
        "counts": {"queued": 0, "running": 1, "passed": 1, "failed": 0, "timed_out": 0},
        "tests": [
            {
                "test_key": "test_0001",
                "model_id": "Lib.A",
                "status": "passed",
                "elapsed": 2.0,
                "worker_id": 0,
                "report_dir": "test_0001",
            },
            {
                "test_key": "test_0002",
                "model_id": "Lib.B",
                "status": "running",
                "elapsed": None,
                "worker_id": 1,
                "report_dir": "test_0002",
            },
        ],
        "updated_at": 0.0,
    }
    _write_status_json(tmp_path, snapshot)
    ctx = build_dashboard_context(tmp_path, mode="live")
    assert ctx["mode"] == "live"
    assert ctx["auto_refresh"] is True
    assert len(ctx["tests"]) == 2
    row_a = next(r for r in ctx["tests"] if r["model_id"] == "Lib.A")
    # Live-mode normalization: passed → ("PASS", "pass"); running → ("RUNNING", "running")
    assert row_a["status_text"] == "PASS"
    assert row_a["status_class"] == "pass"
    assert row_a["worst_nrmse"] is None
    assert row_a["n_vars"] is None
    row_b = next(r for r in ctx["tests"] if r["model_id"] == "Lib.B")
    assert row_b["status_text"] == "RUNNING"
    assert row_b["status_class"] == "running"


def test_build_context_final_with_comparisons(tmp_path):
    """Final snapshot: status.json + comparison_data.json sidecars yield
    a row with both live + post-run fields populated."""
    snapshot = {
        "total": 1,
        "elapsed": 5.0,
        "eta_seconds": None,
        "counts": {"queued": 0, "running": 0, "passed": 1, "failed": 0, "timed_out": 0},
        "tests": [
            {
                "test_key": "test_0001",
                "model_id": "Lib.A",
                "status": "passed",
                "elapsed": 2.0,
                "worker_id": 0,
                "report_dir": "test_0001",
            }
        ],
        "updated_at": 0.0,
    }
    _write_status_json(tmp_path, snapshot)
    test_dir = tmp_path / "reports" / "test_0001"
    test_dir.mkdir(parents=True)
    (test_dir / "comparison_data.json").write_text(
        json.dumps(
            {
                "model_id": "Lib.A",
                "worst_nrmse": 1.2e-5,
                "n_vars": 3,
                "n_vars_passed": 3,
                "n_warnings": 0,
                "translation_wall": 0.5,
                "sim_wall": 1.5,
                "total_wall": 2.0,
            }
        )
    )
    ctx = build_dashboard_context(tmp_path, mode="final")
    assert ctx["mode"] == "final"
    assert ctx["auto_refresh"] is False
    row = ctx["tests"][0]
    assert row["worst_nrmse"] == 1.2e-5
    assert row["n_vars"] == 3
    assert row["translation_wall"] == 0.5


def test_render_live_writes_dashboard_html(tmp_path):
    """render_live writes dashboard.html with the meta-refresh tag and the
    DASHBOARD_MODE='live' marker."""
    snapshot = {
        "total": 1,
        "elapsed": 0.0,
        "eta_seconds": None,
        "counts": {"queued": 1, "running": 0, "passed": 0, "failed": 0, "timed_out": 0},
        "tests": [
            {
                "test_key": "test_0001",
                "model_id": "Lib.A",
                "status": "queued",
                "elapsed": None,
                "worker_id": None,
                "report_dir": "test_0001",
            }
        ],
        "updated_at": 0.0,
    }
    _write_status_json(tmp_path, snapshot)
    render_live(tmp_path)
    out = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert "<title>" in out
    assert "Lib.A" in out
    assert '<meta http-equiv="refresh" content="5">' in out
    assert "DASHBOARD_MODE = 'live'" in out


def test_render_final_strips_refresh(tmp_path):
    snapshot = {
        "total": 1,
        "elapsed": 0.0,
        "eta_seconds": None,
        "counts": {"queued": 0, "running": 0, "passed": 1, "failed": 0, "timed_out": 0},
        "tests": [
            {
                "test_key": "test_0001",
                "model_id": "Lib.A",
                "status": "passed",
                "elapsed": 1.0,
                "worker_id": 0,
                "report_dir": "test_0001",
            }
        ],
        "updated_at": 0.0,
    }
    _write_status_json(tmp_path, snapshot)
    render_final(tmp_path)
    out = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert "DASHBOARD_MODE = 'final'" in out


def test_rerun_prefix_flows_to_template(tmp_path):
    """rerun_prefix from the snapshot (or kwarg override) must end up in the
    rendered HTML so the dashboard's JS rerun-command builder can use it."""
    snapshot = {
        "total": 0,
        "elapsed": 0.0,
        "eta_seconds": None,
        "counts": {},
        "tests": [],
        "updated_at": 0.0,
        "rerun_prefix": 'dstf --config "/some/path/testing.json" run',
    }
    _write_status_json(tmp_path, snapshot)
    render_live(tmp_path)
    out = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert "RERUN_PREFIX" in out
    assert "/some/path/testing.json" in out


def test_rerun_prefix_kwarg_overrides_snapshot(tmp_path):
    """Explicit rerun_prefix kwarg wins over status.json's value."""
    snapshot = {
        "total": 0,
        "elapsed": 0.0,
        "eta_seconds": None,
        "counts": {},
        "tests": [],
        "updated_at": 0.0,
        "rerun_prefix": "dstf STALE run",
    }
    _write_status_json(tmp_path, snapshot)
    render_live(tmp_path, rerun_prefix="dstf FRESH run")
    out = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert "FRESH" in out
    assert "STALE" not in out


def test_dashboard_has_selection_ui(tmp_path):
    """Selection column + sticky footer + rerun-command hooks must be in
    the rendered output."""
    snapshot = {
        "total": 0,
        "elapsed": 0.0,
        "eta_seconds": None,
        "counts": {},
        "tests": [],
        "updated_at": 0.0,
    }
    _write_status_json(tmp_path, snapshot)
    render_live(tmp_path)
    out = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert 'id="sel-all"' in out  # header tristate checkbox
    assert 'class="sel-footer"' in out  # sticky footer container
    assert "toggleAllVisible" in out  # JS handler wired
    assert "buildRerunCommand" in out  # command-building helper exists
    assert "downloadFilter" in out  # download .txt button handler


def test_dashboard_template_has_pill_toggles(tmp_path):
    """Counter pills are clickable filter toggles. The pill-click handler
    name + the PILL_TO_STATUSES map must both be in the rendered JS."""
    snapshot = {
        "total": 0,
        "elapsed": 0.0,
        "eta_seconds": None,
        "counts": {},
        "tests": [],
        "updated_at": 0.0,
    }
    _write_status_json(tmp_path, snapshot)
    render_live(tmp_path)
    out = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert "togglePill" in out  # click handler defined + wired on each pill
    assert "PILL_TO_STATUSES" in out  # pill key → row status_class map
    # The "filter-bar" row of buttons no longer exists — pills handle it
    assert 'class="filter-bar"' not in out


def test_dashboard_template_has_sort_hooks(tmp_path):
    """Each sortable column header must have data-sort and data-key attrs."""
    snapshot = {
        "total": 0,
        "elapsed": 0.0,
        "eta_seconds": None,
        "counts": {},
        "tests": [],
        "updated_at": 0.0,
    }
    _write_status_json(tmp_path, snapshot)
    render_live(tmp_path)
    out = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert 'data-sort="text" data-key="model"' in out
    assert 'data-sort="num" data-key="nrmse"' in out
    assert 'data-sort="num" data-key="elapsed"' in out


def test_dashboard_template_has_per_column_filter(tmp_path):
    """Per-column text filter inputs must be present below headers."""
    snapshot = {
        "total": 0,
        "elapsed": 0.0,
        "eta_seconds": None,
        "counts": {},
        "tests": [],
        "updated_at": 0.0,
    }
    _write_status_json(tmp_path, snapshot)
    render_live(tmp_path)
    out = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert 'class="col-filter"' in out
    assert 'data-col-filter="model"' in out


def test_live_mode_uses_meta_refresh(tmp_path):
    """Live mode must include the auto-refresh meta tag — it's what drives
    the dashboard's auto-refresh on file:// URLs (JS fetch is blocked there
    in Chrome/Edge for security reasons; meta-refresh is unaffected)."""
    snapshot = {
        "total": 0,
        "elapsed": 0.0,
        "eta_seconds": None,
        "counts": {},
        "tests": [],
        "updated_at": 0.0,
    }
    _write_status_json(tmp_path, snapshot)
    render_live(tmp_path)
    out = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert '<meta http-equiv="refresh" content="5">' in out
    assert "DASHBOARD_MODE = 'live'" in out


def test_final_mode_strips_meta_refresh(tmp_path):
    """Final mode must NOT auto-refresh — the page is now static."""
    snapshot = {
        "total": 0,
        "elapsed": 0.0,
        "eta_seconds": None,
        "counts": {},
        "tests": [],
        "updated_at": 0.0,
    }
    _write_status_json(tmp_path, snapshot)
    render_final(tmp_path)
    out = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    # Match the actual tag, not the substring (the JS comment mentions
    # the tag literally for documentation, which would false-positive a
    # naive substring check).
    assert '<meta http-equiv="refresh"' not in out
    assert "DASHBOARD_MODE = 'final'" in out


def test_render_final_picks_up_real_sidecar_shape(tmp_path):
    """The sidecar emitted by generate_comparison_plots includes the
    summary fields (worst_nrmse, n_vars, etc.) at the top level so
    build_dashboard_context can read them without unwrapping."""
    snapshot = {
        "total": 1,
        "elapsed": 5.0,
        "eta_seconds": None,
        "counts": {"queued": 0, "running": 0, "passed": 1, "failed": 0, "timed_out": 0},
        "tests": [
            {
                "test_key": "test_0001",
                "model_id": "Lib.A",
                "status": "passed",
                "elapsed": 2.0,
                "worker_id": 0,
                "report_dir": "test_0001",
            }
        ],
        "updated_at": 0.0,
    }
    _write_status_json(tmp_path, snapshot)
    test_dir = tmp_path / "reports" / "test_0001"
    test_dir.mkdir(parents=True)
    # Sidecar shape after the patch — summary fields under a `summary`
    # block alongside the existing rendering context fields
    (test_dir / "comparison_data.json").write_text(
        json.dumps(
            {
                "model_id": "Lib.A",
                "summary": {
                    "worst_nrmse": 1.2e-5,
                    "n_vars": 3,
                    "n_vars_passed": 3,
                    "n_warnings": 1,
                    "translation_wall": 0.5,
                    "sim_wall": 1.5,
                    "total_wall": 2.0,
                    "ref_id": "ref_0042",
                },
            }
        )
    )
    ctx = build_dashboard_context(tmp_path, mode="final")
    row = ctx["tests"][0]
    assert row["worst_nrmse"] == 1.2e-5
    assert row["n_vars_passed"] == 3
    assert row["n_warnings"] == 1
    assert row["ref_id"] == "ref_0042"


def test_stale_sidecar_ignored_by_timestamp(tmp_path):
    """A sidecar written before the run's start_wall must not enrich the row.

    Repro of the user-reported bug: cmd_run wrote sidecars with FAIL last
    week; this run shows passed in status.json. Without the timestamp
    guard, _enrich_row_from_comparison would override 'pass' with 'fail'.
    """
    snapshot = {
        "total": 1,
        "elapsed": 5.0,
        "eta_seconds": None,
        "counts": {"queued": 0, "running": 0, "passed": 1, "failed": 0, "timed_out": 0},
        "start_wall": 1_000_000.0,  # Run started AT this epoch
        "tests": [
            {
                "test_key": "test_0001",
                "model_id": "Lib.A",
                "status": "passed",
                "elapsed": 2.0,
                "worker_id": 0,
                "report_dir": "test_0001",
            }
        ],
        "updated_at": 1_000_005.0,
    }
    _write_status_json(tmp_path, snapshot)
    test_dir = tmp_path / "reports" / "test_0001"
    test_dir.mkdir(parents=True)
    (test_dir / "comparison_data.json").write_text(
        json.dumps(
            {
                "model_id": "Lib.A",
                "summary": {
                    "model_id": "Lib.A",
                    "written_at": 999_999.0,  # ONE SECOND BEFORE start_wall — stale
                    "status_text": "FAIL",
                    "status_class": "fail",
                    "worst_nrmse": 999.0,
                },
            }
        )
    )
    ctx = build_dashboard_context(tmp_path, mode="final")
    row = ctx["tests"][0]
    # Live-mode 'passed' must win — the stale sidecar is ignored
    assert row["status_text"] == "PASS"
    assert row["status_class"] == "pass"
    # Sidecar's worst_nrmse must NOT have been copied
    assert row["worst_nrmse"] is None


def test_fresh_sidecar_overrides_status(tmp_path):
    """A sidecar written AFTER start_wall is fresh and must enrich the row.

    Validates the guard isn't over-zealous — comparison verdict from the
    current run still wins over live-mode's 'passed' (since the compare
    phase distinguishes pass vs fail vs no-ref vs sim-fail).
    """
    snapshot = {
        "total": 1,
        "elapsed": 5.0,
        "eta_seconds": None,
        "counts": {"queued": 0, "running": 0, "passed": 1, "failed": 0, "timed_out": 0},
        "start_wall": 1_000_000.0,
        "tests": [
            {
                "test_key": "test_0001",
                "model_id": "Lib.A",
                "status": "passed",
                "elapsed": 2.0,
                "worker_id": 0,
                "report_dir": "test_0001",
            }
        ],
        "updated_at": 1_000_010.0,
    }
    _write_status_json(tmp_path, snapshot)
    test_dir = tmp_path / "reports" / "test_0001"
    test_dir.mkdir(parents=True)
    (test_dir / "comparison_data.json").write_text(
        json.dumps(
            {
                "model_id": "Lib.A",
                "summary": {
                    "model_id": "Lib.A",
                    "written_at": 1_000_010.0,  # AFTER start_wall — fresh
                    "status_text": "FAIL",
                    "status_class": "fail",
                    "worst_nrmse": 0.05,
                },
            }
        )
    )
    ctx = build_dashboard_context(tmp_path, mode="final")
    row = ctx["tests"][0]
    # Fresh sidecar's compare verdict wins over live-mode's 'passed'
    assert row["status_text"] == "FAIL"
    assert row["status_class"] == "fail"
    assert row["worst_nrmse"] == 0.05


def test_model_id_mismatch_sidecar_ignored(tmp_path):
    """A sidecar whose summary.model_id doesn't match the row's model_id
    is treated as bookkeeping drift and ignored. Defends against a future
    case where batch_manifest's test_NNNN -> model_id mapping disagrees
    with what's on disk in reports/<report_dir>/.
    """
    snapshot = {
        "total": 1,
        "elapsed": 5.0,
        "eta_seconds": None,
        "counts": {"queued": 0, "running": 0, "passed": 1, "failed": 0, "timed_out": 0},
        "start_wall": 1_000_000.0,
        "tests": [
            {
                "test_key": "test_0001",
                "model_id": "Lib.A",
                "status": "passed",
                "elapsed": 2.0,
                "worker_id": 0,
                "report_dir": "test_0001",
            }
        ],
        "updated_at": 1_000_005.0,
    }
    _write_status_json(tmp_path, snapshot)
    test_dir = tmp_path / "reports" / "test_0001"
    test_dir.mkdir(parents=True)
    (test_dir / "comparison_data.json").write_text(
        json.dumps(
            {
                # Sidecar belongs to a DIFFERENT model — bookkeeping drift
                "model_id": "Lib.B",
                "summary": {
                    "model_id": "Lib.B",
                    "written_at": 1_000_010.0,  # Fresh, but for the wrong model
                    "status_text": "FAIL",
                    "status_class": "fail",
                    "worst_nrmse": 999.0,
                },
            }
        )
    )
    ctx = build_dashboard_context(tmp_path, mode="final")
    row = ctx["tests"][0]
    assert row["status_text"] == "PASS"  # Mismatched sidecar ignored
    assert row["worst_nrmse"] is None


def test_sidecar_without_written_at_passes_guard(tmp_path):
    """Legacy sidecars (written before D91 added the written_at stamp)
    don't have the timestamp field. The guard must not silently drop them
    — that would regress every existing post-D91 cache.
    """
    snapshot = {
        "total": 1,
        "elapsed": 5.0,
        "eta_seconds": None,
        "counts": {"queued": 0, "running": 0, "passed": 1, "failed": 0, "timed_out": 0},
        "start_wall": 1_000_000.0,
        "tests": [
            {
                "test_key": "test_0001",
                "model_id": "Lib.A",
                "status": "passed",
                "elapsed": 2.0,
                "worker_id": 0,
                "report_dir": "test_0001",
            }
        ],
        "updated_at": 1_000_005.0,
    }
    _write_status_json(tmp_path, snapshot)
    test_dir = tmp_path / "reports" / "test_0001"
    test_dir.mkdir(parents=True)
    (test_dir / "comparison_data.json").write_text(
        json.dumps(
            {
                "model_id": "Lib.A",
                "summary": {
                    "model_id": "Lib.A",
                    # No written_at — predates the guard
                    "worst_nrmse": 0.05,
                    "n_vars": 3,
                },
            }
        )
    )
    ctx = build_dashboard_context(tmp_path, mode="final")
    row = ctx["tests"][0]
    assert row["worst_nrmse"] == 0.05  # Legacy sidecar enriched normally
    assert row["n_vars"] == 3


def test_column_resize_handles_present(tmp_path):
    """Each header th has a resize handle so users can drag columns wider.
    JS wires mousedown on the handle to a drag callback; widths persist
    to localStorage alongside filter / selection / sort.
    """
    snapshot = {
        "total": 0,
        "elapsed": 0.0,
        "eta_seconds": None,
        "counts": {},
        "tests": [],
        "updated_at": 0.0,
    }
    _write_status_json(tmp_path, snapshot)
    render_live(tmp_path)
    out = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert 'class="col-resize-handle"' in out
    # JS plumbing
    assert "startColResize" in out
    assert "applyColWidth" in out
    # CSS variable hookup for truncated columns
    assert "--col-w-model" in out
    assert "--col-w-detail" in out
    # Persist alongside other UI state
    assert "colWidths" in out


def test_sticky_chrome_wraps_top_region(tmp_path):
    """The whole top region (h1 + status bar + progress bar + counter pills)
    must be wrapped in #sticky-chrome so it stays visible while scrolling.
    Thead sticks below it via CSS variables fed by a ResizeObserver.
    """
    snapshot = {
        "total": 0,
        "elapsed": 0.0,
        "eta_seconds": None,
        "counts": {},
        "tests": [],
        "updated_at": 0.0,
    }
    _write_status_json(tmp_path, snapshot)
    render_live(tmp_path)
    out = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    # Wrapper exists
    assert 'id="sticky-chrome"' in out
    # CSS rules for the sticky stacking
    assert "#sticky-chrome" in out
    assert "--chrome-height" in out
    assert "--header-row-height" in out
    # JS measurement hook
    assert "updateChromeHeight" in out
    assert "ResizeObserver" in out


def test_resolution_column_shows_provenance(tmp_path):
    """field_sources from status.json (or sidecar) flows into row cells."""
    snapshot = {
        "total": 1,
        "elapsed": 5.0,
        "eta_seconds": None,
        "counts": {"queued": 0, "running": 0, "passed": 1, "failed": 0, "timed_out": 0},
        "tests": [
            {
                "test_key": "test_0001",
                "model_id": "Lib.A",
                "status": "passed",
                "elapsed": 2.0,
                "worker_id": 0,
                "report_dir": "test_0001",
                "field_sources": {
                    "stop_time": "test_spec",
                    "tolerance": "annotation",
                },
            }
        ],
        "updated_at": 0.0,
    }
    _write_status_json(tmp_path, snapshot)
    render_live(tmp_path)
    out = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert "test_spec" in out
    assert "Resolution" in out
