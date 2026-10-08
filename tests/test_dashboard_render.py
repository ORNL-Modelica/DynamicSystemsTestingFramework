"""Tests for the unified dashboard renderer."""

import json
import re
from pathlib import Path

import markupsafe

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
    row_a = next(r for r in ctx["tests"] if r.model_id == "Lib.A")
    # A live "passed" is a simulation that ran, not a verdict: SIMULATED,
    # styled as "no verdict yet"; running → ("RUNNING", "running")
    assert row_a.status_text == "SIMULATED"
    assert row_a.status_class == "pending"
    assert row_a.worst_score is None
    assert row_a.n_vars is None
    row_b = next(r for r in ctx["tests"] if r.model_id == "Lib.B")
    assert row_b.status_text == "RUNNING"
    assert row_b.status_class == "running"


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
    # The sidecar's key is worst_nrmse; the row's is worst_score.
    assert row.worst_score == 1.2e-5
    assert row.n_vars == 3
    assert row.translation_wall == 0.5


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


def test_run_metadata_flows_to_context(tmp_path):
    snapshot = {
        "total": 1,
        "elapsed": 0.0,
        "counts": {},
        "tests": [],
        "updated_at": 0.0,
        "metadata": {
            "backend": "Dymola",
            "backend_name": "Dymola 2026x",
            "os": "linux",
            "tool_version": "Dymola 2026x Refresh 1",
            "dstf_version": "0.1.0",
            "generated_at": 1_700_000_000.0,
        },
    }
    _write_status_json(tmp_path, snapshot)
    ctx = build_dashboard_context(tmp_path, mode="final")
    rm = ctx["run_metadata"]
    assert rm["label"] == "Dymola 2026x"
    assert rm["os"] == "linux"
    assert rm["tool_version"] == "Dymola 2026x Refresh 1"
    assert rm["dstf_version"] == "0.1.0"
    # epoch formatted to a human date string (year present)
    assert rm["generated_str"] and "20" in rm["generated_str"]


def test_run_metadata_surfaces_msl_from_library_versions(tmp_path):
    snapshot = {
        "total": 1,
        "counts": {},
        "tests": [],
        "updated_at": 0.0,
        "metadata": {
            "backend": "Dymola",
            "backend_name": "Dymola 2026x",
            "os": "linux",
            "library_versions": {"Modelica": "4.1.0"},
        },
    }
    _write_status_json(tmp_path, snapshot)
    ctx = build_dashboard_context(tmp_path, mode="final")
    assert ctx["run_metadata"]["msl"] == "4.1.0"


def test_render_final_shows_msl_in_banner(tmp_path):
    snapshot = {
        "total": 1,
        "elapsed": 1.0,
        "counts": {"passed": 1},
        "tests": [],
        "updated_at": 0.0,
        "metadata": {
            "backend": "Dymola",
            "backend_name": "Dymola 2026x",
            "os": "linux",
            "library_versions": {"Modelica": "4.0.0"},
        },
    }
    _write_status_json(tmp_path, snapshot)
    render_final(tmp_path)
    out = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert "MSL 4.0.0" in out


def test_run_metadata_absent_is_none(tmp_path):
    snapshot = {"total": 0, "counts": {}, "tests": [], "updated_at": 0.0}
    _write_status_json(tmp_path, snapshot)
    ctx = build_dashboard_context(tmp_path, mode="final")
    assert ctx["run_metadata"] is None


def test_render_final_shows_metadata_banner(tmp_path):
    snapshot = {
        "total": 1,
        "elapsed": 1.0,
        "counts": {"passed": 1},
        "tests": [],
        "updated_at": 0.0,
        "metadata": {
            "backend": "Dymola",
            "backend_name": "Dymola 2026x",
            "os": "linux",
            "tool_version": "Dymola 2026x Refresh 1",
            "dstf_version": "0.1.0",
            "generated_at": 1_700_000_000.0,
        },
    }
    _write_status_json(tmp_path, snapshot)
    render_final(tmp_path)
    out = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert "Dymola 2026x" in out  # configured label
    assert "Dymola 2026x Refresh 1" in out  # reported version
    assert "linux" in out
    assert "run-meta" in out  # the banner element


def test_render_final_no_banner_without_metadata(tmp_path):
    snapshot = {
        "total": 1,
        "elapsed": 1.0,
        "counts": {"passed": 1},
        "tests": [],
        "updated_at": 0.0,
    }
    _write_status_json(tmp_path, snapshot)
    render_final(tmp_path)
    out = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert "run-meta" not in out


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
        "rerun_prefix": 'dstf --config "/some/path/testing.json"',
    }
    _write_status_json(tmp_path, snapshot)
    render_live(tmp_path)
    out = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert "RERUN_PREFIX" in out
    assert "/some/path/testing.json" in out


def test_rerun_prefix_carries_no_subcommand():
    """The prefix is global flags only — the dashboard's command menu owns
    the subcommand. A prefix that still ended in ``run`` would build
    ``dstf ... run compare --filter ...``.
    """
    from types import SimpleNamespace

    from dstf.reporting.dashboard_render import build_rerun_prefix

    with_config = build_rerun_prefix(
        SimpleNamespace(config_file="/some/path/testing.json")
    )
    with_paths = build_rerun_prefix(
        SimpleNamespace(config_file=None, source_path="/lib", reference_root="/refs")
    )
    for prefix in (with_config, with_paths):
        assert prefix.startswith("dstf ")
        assert prefix.split()[-1] not in {"run", "compare", "discover"}
        assert " run" not in prefix


def test_rerun_prefix_kwarg_overrides_snapshot(tmp_path):
    """Explicit rerun_prefix kwarg wins over status.json's value."""
    snapshot = {
        "total": 0,
        "elapsed": 0.0,
        "eta_seconds": None,
        "counts": {},
        "tests": [],
        "updated_at": 0.0,
        "rerun_prefix": "dstf STALE",
    }
    _write_status_json(tmp_path, snapshot)
    render_live(tmp_path, rerun_prefix="dstf FRESH")
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
    and the statuses the pills are drawn from must both be in the page."""
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
    assert "const STATUSES = " in out  # the pills, keyed by status class
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
    assert row.worst_score == 1.2e-5
    assert row.n_vars_passed == 3
    assert row.n_warnings == 1
    assert row.ref_id == "ref_0042"


def test_stale_sidecar_ignored_by_timestamp(tmp_path):
    """A sidecar written before the run's start_wall must not enrich the row.

    Repro of the user-reported bug: cmd_run wrote sidecars with FAIL last
    week; this run shows passed in status.json. Without the timestamp
    guard, rows.enrich_row would override the live row with 'fail'.
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
    # The live row stands — the stale sidecar is ignored
    assert row.status_text == "SIMULATED"
    assert row.status_class == "pending"
    # Sidecar's worst_nrmse must NOT have been copied
    assert row.worst_score is None


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
    assert row.status_text == "FAIL"
    assert row.status_class == "fail"
    assert row.worst_score == 0.05


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
    assert row.status_text == "SIMULATED"  # Mismatched sidecar ignored
    assert row.worst_score is None


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
    assert row.worst_score == 0.05  # Legacy sidecar enriched normally
    assert row.n_vars == 3


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


def test_model_id_is_escaped_in_the_final_page(tmp_path):
    """The dashboard is the --report-format html surface now; the escaping
    guard the retired html_report tests carried moves here (D117)."""
    snapshot = {
        "total": 1,
        "elapsed": 1.0,
        "eta_seconds": None,
        "counts": {"queued": 0, "running": 0, "passed": 1, "failed": 0, "timed_out": 0},
        "tests": [
            {
                "test_key": "test_0001",
                "model_id": "Lib.<script>alert(1)</script>",
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
    html = (tmp_path / "dashboard.html").read_text()
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def _one_test_snapshot() -> dict:
    return {
        "total": 1,
        "elapsed": 5.0,
        "eta_seconds": None,
        "counts": {"passed": 1},
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


def _write_sidecar(work_dir: Path, summary: dict) -> None:
    test_dir = work_dir / "reports" / "test_0001"
    test_dir.mkdir(parents=True, exist_ok=True)
    (test_dir / "comparison_data.json").write_text(
        json.dumps({"model_id": "Lib.A", "summary": summary})
    )


class TestWorstNrmseColumn:
    """A test whose only leaf is a sentinel has no score to show. It used
    to read "—", the same as a test that had not been compared at all —
    the inf/nan swallowed the per-test max in the sidecar. It now names
    what happened."""

    def test_unscored_count_reaches_the_row(self, tmp_path):
        _write_status_json(tmp_path, _one_test_snapshot())
        _write_sidecar(
            tmp_path, {"model_id": "Lib.A", "worst_nrmse": None, "n_unscored": 1}
        )
        row = build_dashboard_context(tmp_path, mode="final")["tests"][0]
        assert row.worst_score is None
        assert row.n_unscored == 1

    def test_cell_says_n_a_with_the_count(self, tmp_path):
        _write_status_json(tmp_path, _one_test_snapshot())
        _write_sidecar(
            tmp_path, {"model_id": "Lib.A", "worst_nrmse": None, "n_unscored": 2}
        )
        render_final(tmp_path)
        html = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
        assert "n/a (2 unscored)" in html

    def test_a_finite_worst_still_wins_the_cell(self, tmp_path):
        _write_status_json(tmp_path, _one_test_snapshot())
        _write_sidecar(
            tmp_path,
            {"model_id": "Lib.A", "worst_nrmse": 1.25e-4, "n_unscored": 1},
        )
        render_final(tmp_path)
        html = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
        assert "1.2500e-04" in html
        assert "n/a (" not in html

    def test_uncompared_test_still_reads_em_dash(self, tmp_path):
        """No sidecar at all — nothing was scored and nothing was skipped;
        the honest cell is still "—"."""
        _write_status_json(tmp_path, _one_test_snapshot())
        render_final(tmp_path)
        html = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
        assert "n/a (" not in html


# ---- The row model (reporting.rows) is what the page renders ----------------


def _two_row_final_page(tmp_path: Path) -> str:
    """A final page over two rows: one with a fresh sidecar, one live only."""
    snapshot = {
        "total": 2,
        "elapsed": 5.0,
        "eta_seconds": None,
        "counts": {"passed": 2},
        "start_wall": 1.0,
        "tests": [
            {
                "test_key": "test_0001",
                "model_id": "Lib.A",
                "status": "passed",
                "elapsed": 2.0,
                "worker_id": 0,
                "report_dir": "test_0001",
                "field_sources": {"stop_time": "annotation"},
                "detail": "a message",
            },
            {
                "test_key": "test_0002",
                "model_id": "Lib.B",
                "status": "passed",
                "elapsed": 1.0,
                "worker_id": 1,
                "report_dir": "test_0002",
            },
        ],
        "updated_at": 6.0,
    }
    _write_status_json(tmp_path, snapshot)
    _write_sidecar(
        tmp_path,
        {
            "model_id": "Lib.A",
            "written_at": 2.0,
            "status": "FAIL",
            "worst_nrmse": 0.5,
            "n_vars": 2,
            "n_vars_passed": 1,
            "n_warnings": 3,
            "n_unscored": 0,
        },
    )
    render_final(tmp_path)
    return (tmp_path / "dashboard.html").read_text(encoding="utf-8")


def _body_rows(html: str) -> list[str]:
    body = html.split('<tbody id="results-tbody">', 1)[1].split("</tbody>", 1)[0]
    return re.findall(r"<tr\b.*?</tr>", body, flags=re.S)


def _attrs(tr: str) -> dict[str, str]:
    opening = tr.split(">", 1)[0]
    return dict(re.findall(r'([\w-]+)="([^"]*)"', opening))


def test_the_header_is_the_column_list(tmp_path):
    from dstf.reporting.rows import COLUMNS

    html = _two_row_final_page(tmp_path)
    head = html.split("<thead>", 1)[1].split("</tr>", 1)[0]
    ths = re.findall(r"<th\b([^>]*)>(.*?)</th>", head, flags=re.S)
    assert 'class="sel-col"' in ths[0][0]
    keys = [re.search(r'data-key="([^"]*)"', a).group(1) for a, _ in ths[1:]]
    labels = [re.sub(r"<[^>]*>.*", "", inner, flags=re.S) for _, inner in ths[1:]]
    assert keys == [c.key for c in COLUMNS]
    assert labels == [c.label for c in COLUMNS]
    # The model id is the test a person reads; the work-dir key is a key.
    assert labels[keys.index("model")] == "Test"
    assert labels[keys.index("test")] == "Key"
    assert 'data-key="vars" title="Variables passed / scored.' in head


def test_every_row_carries_every_columns_attributes(tmp_path):
    from dstf.reporting.rows import COLUMNS

    rows = _body_rows(_two_row_final_page(tmp_path))
    assert len(rows) == 2
    for tr in rows:
        attrs = _attrs(tr)
        for c in COLUMNS:
            assert f"data-sort-{c.key}" in attrs, c.key
            if c.key != "status":
                assert f"data-{c.key}" in attrs, c.key
    a, b = (_attrs(tr) for tr in rows)
    # The Resolution filter had no attribute to read and hid every row.
    assert a["data-resolution"] == "annotation"
    # data-status is the status CLASS: the pills', the counts' and the live
    # tick's key.
    assert (a["data-status"], b["data-status"]) == ("fail", "pending")
    assert a["data-sort-status"] == "3"  # FAIL's place in STATUS_ORDER
    assert a["data-nrmse"] == "5.0000e-01" and a["data-sort-nrmse"] == "0.5"
    # No value: both attributes are empty (no filter matches, sorted last);
    # only the cell reads "—".
    assert b["data-nrmse"] == "" and b["data-sort-nrmse"] == ""
    assert a["data-detail"] == "a message"


def test_the_cells_keep_their_markup(tmp_path):
    a, b = _body_rows(_two_row_final_page(tmp_path))
    assert '<span class="status-badge fail">FAIL</span>' in a
    assert '<span class="status-badge pending">SIMULATED</span>' in b
    assert '<a href="test_0001/">test_0001</a>' in a
    assert (
        '<td class="model-cell" title="Lib.A">'
        '<a href="reports/test_0001/interactive.html">Lib.A</a></td>'
    ) in a
    assert '<span title="all fields from annotation">annotation</span>' in a
    assert '<td class="elapsed-cell">2.0</td>' in a
    assert '<span class="warn">3</span>' in a
    assert '<td class="detail-cell" title="a message">a message</td>' in a


def test_every_column_but_status_has_a_typed_filter(tmp_path):
    from dstf.reporting.rows import COLUMNS

    html = _two_row_final_page(tmp_path)
    found = re.findall(r'data-col-filter="([^"]*)" data-kind="([^"]*)"', html)
    # The status column has none: the pills filter it.
    assert found == [(c.key, c.kind) for c in COLUMNS if c.kind != "status"]


def test_a_cancelled_row_reads_cancelled_and_counts_as_not_run(tmp_path):
    """D150: the queue's cancel leaves a status.json row "cancelled". The
    page says CANCELLED on a not-run row — the NOT RUN pill counts and
    filters it — where it said "status 'cancelled'" in the message."""
    _write_status_json(
        tmp_path,
        {
            "total": 1,
            "elapsed": 1.0,
            "counts": {},
            "start_wall": 1.0,
            "tests": [
                {
                    "test_key": "test_0001",
                    "model_id": "Lib.A",
                    "status": "cancelled",
                    "detail": "Cancelled",
                    "report_dir": "test_0001",
                }
            ],
            "updated_at": 2.0,
        },
    )
    render_final(tmp_path)
    html = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    (row,) = _body_rows(html)
    assert _attrs(row)["data-status"] == "not-run"
    assert '<span class="status-badge not-run">CANCELLED</span>' in row
    assert "status &#39;cancelled&#39;" not in html and "status 'cancelled'" not in html


def test_the_pills_are_the_statuses(tmp_path):
    html = _two_row_final_page(tmp_path)
    m = re.search(r"const STATUSES = (\[.*?\]);\n", html)
    assert m, "the page injects the statuses the pills are drawn from"
    assert json.loads(m.group(1)) == [
        ["queued", "QUEUED"],
        ["running", "RUNNING"],
        ["pass", "PASS"],
        ["fail", "FAIL"],
        ["sim-fail", "SIM_FAIL"],
        ["no-ref", "NO_REF"],
        ["timed-out", "TIMED OUT"],
        ["not-run", "NOT RUN"],
    ]
    assert "['warnings', 'warnings']" in html  # the cross-cut pill


def test_the_saved_state_key_moved_with_the_pill_keys(tmp_path):
    html = _two_row_final_page(tmp_path)
    assert "'dstf-dashboard-v2:'" in html
    assert "dstf-dashboard-v1:" not in html


def test_a_final_page_without_sidecars_shows_verdicts(tmp_path):
    """`dstf run` / `compare` without --report write no sidecars; the page
    they render still shows each test's verdict, from its comparison."""
    from dstf.comparison.types import TestComparison

    snapshot = {
        "total": 3,
        "elapsed": 5.0,
        "counts": {"passed": 3},
        "tests": [
            {
                "test_key": f"test_000{i}",
                "model_id": m,
                "status": "passed",
                "report_dir": f"test_000{i}",
            }
            for i, m in enumerate(("Lib.A", "Lib.B", "Lib.C"), 1)
        ],
        "updated_at": 0.0,
    }
    _write_status_json(tmp_path, snapshot)
    comparisons = [
        TestComparison(model_id="Lib.A", passed=True, test_id="0001"),
        TestComparison(model_id="Lib.B", passed=False),
        TestComparison(model_id="Lib.C", passed=True),
    ]
    rows = build_dashboard_context(tmp_path, mode="final", comparisons=comparisons)[
        "tests"
    ]
    assert [r.status for r in rows] == ["PASS", "FAIL", "PASS"]
    assert rows[0].ref_id == "ref_0001"
    render_final(tmp_path, comparisons=comparisons)
    html = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    statuses = [_attrs(tr)["data-status"] for tr in _body_rows(html)]
    assert statuses == ["pass", "fail", "pass"]


def test_a_refused_tests_reason_is_its_message(tmp_path):
    """A test the comparison refused (a FAIL with a reason and no variable)
    says the reason in the Message column — the tool's Suite row does —
    and a FAIL scored variable by variable keeps what the run said."""
    from test_reporters import REFUSED, _failing, _refused

    snapshot = _one_test_snapshot()
    (entry,) = snapshot["tests"]
    snapshot["tests"] = [
        {**entry, "model_id": "Lib.R"},
        {**entry, "model_id": "Lib.F", "test_key": "test_0002", "detail": "3 events"},
    ]
    for t in snapshot["tests"]:
        t["report_dir"] = t["test_key"]
    _write_status_json(tmp_path, snapshot)
    comparisons = [_refused("Lib.R"), _failing("Lib.F")]
    rows = build_dashboard_context(tmp_path, mode="final", comparisons=comparisons)[
        "tests"
    ]
    assert [(r.status, r.message) for r in rows] == [
        ("FAIL", REFUSED),
        ("FAIL", "3 events"),
    ]
    render_final(tmp_path, comparisons=comparisons)
    html = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    refused, failing = _body_rows(html)
    said = markupsafe.escape(REFUSED)  # the sentence quotes the value it read
    assert said in refused and said not in failing


def test_a_test_with_no_result_reads_not_run_in_grey(tmp_path):
    """D145: a test the last run never finished (a status.json row still
    queued, as an aborted run leaves it) and one it never ran (no row at
    all) read NOT RUN on the final page: the row's class and badge, a
    style for each, and a pill of its own counted by that class."""
    from conftest import make_test_model
    from dstf.comparison.comparator import compare_one
    from dstf.comparison.types import TestComparison

    snapshot = {
        "total": 2,
        "elapsed": 5.0,
        "counts": {"passed": 1, "queued": 1},
        "tests": [
            {"test_key": "test_0001", "model_id": "Lib.A", "status": "passed"},
            {"test_key": "test_0002", "model_id": "Lib.B", "status": "queued"},
        ],
        "updated_at": 0.0,
    }
    _write_status_json(tmp_path, snapshot)
    comparisons = [
        TestComparison(model_id="Lib.A", passed=True, test_id="0001"),
        compare_one(make_test_model("Lib.B"), None, None),
        compare_one(make_test_model("Lib.C"), None, None),
    ]
    render_final(tmp_path, comparisons=comparisons)
    html = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    rows = _body_rows(html)
    assert [_attrs(tr)["data-status"] for tr in rows] == [
        "pass",
        "not-run",
        "not-run",
    ]
    assert rows[1].count('<span class="status-badge not-run">NOT RUN</span>') == 1
    for rule in (".counter.not-run", "tr.not-run td", ".status-badge.not-run"):
        assert rule in html, rule
    assert "var(--dstf-not-run-fill)" in html


def _warning():
    from dstf.comparison.types import StructuralWarning

    return StructuralWarning(field="n_states", reference_value="4", current_value="5")


def test_the_comparison_in_hand_decides_the_verdict_over_a_sidecar(tmp_path):
    """A sidecar from an earlier `--report` said PASS; the range was tightened
    since and `run --rerun sim-failed` renders with the comparison it just
    scored, which says FAIL. The comparison's verdict and numbers win; what
    only the sidecar knows (its reference link, its ref_id, its walls)
    survives."""
    from dstf.comparison.types import TestComparison

    snapshot = _one_test_snapshot()
    snapshot["start_wall"] = 1.0
    _write_status_json(tmp_path, snapshot)
    _write_sidecar(
        tmp_path,
        {
            "model_id": "Lib.A",
            "written_at": 2.0,
            "status": "PASS",
            "worst_nrmse": 1e-6,
            "n_unscored": 0,
            "n_vars": 1,
            "n_vars_passed": 1,
            "n_warnings": 0,
            "ref_id": "ref_0042",
            "ref_file": "file:///refs/ref_0042.json",
            "translation_wall": 1.5,
            "sim_wall": 2.5,
            "total_wall": 4.0,
        },
    )
    comp = TestComparison(
        model_id="Lib.A",
        passed=False,
        variables=[_failing_var()],
        warnings=[_warning()],
    )
    (row,) = build_dashboard_context(tmp_path, mode="final", comparisons=[comp])[
        "tests"
    ]
    assert row.status == "FAIL"
    assert (row.worst_score, row.n_vars, row.n_vars_passed) == (0.5, 1, 0)
    assert (row.n_warnings, row.n_unscored) == (1, 0)
    assert (row.ref_id, row.ref_file) == ("ref_0042", "file:///refs/ref_0042.json")
    assert (row.translation_wall, row.sim_wall, row.total_wall) == (1.5, 2.5, 4.0)


def _failing_var():
    from dstf.comparison.types import VariableComparison

    return VariableComparison(
        index=0,
        name="x",
        passed=False,
        nrmse=0.5,
        rmse=0.5,
        signal_range=1.0,
        max_abs_error=0.5,
        max_abs_error_time=0.0,
        reference_final=1.0,
        actual_final=1.0,
        tolerance_used=1e-4,
    )


def test_a_page_without_sidecars_keeps_what_the_live_row_knows(tmp_path):
    """No sidecar: the row's verdict and numbers are the comparison's, its
    provenance the live row's, and a comparison with no stored baseline
    names none — never "ref_None"."""
    from dstf.comparison.types import TestComparison

    snapshot = _one_test_snapshot()
    snapshot["tests"][0]["field_sources"] = {"stop_time": "annotation"}
    _write_status_json(tmp_path, snapshot)
    comp = TestComparison(
        model_id="Lib.A", passed=True, test_id=None, warnings=[_warning()]
    )
    render_final(tmp_path, comparisons=[comp])
    (tr,) = _body_rows((tmp_path / "dashboard.html").read_text(encoding="utf-8"))
    attrs = _attrs(tr)
    assert attrs["data-resolution"] == "annotation"
    assert attrs["data-sort-warnings"] == "1"
    assert "ref_None" not in tr
    assert attrs["data-sort-ref"] == ""


def test_a_zero_is_a_value_not_an_absence(tmp_path):
    """A Warnings `=0` filter reads data-sort-warnings: a 0 rendered as ""
    is a row with no value, which no number filter matches."""
    from dstf.comparison.types import TestComparison

    _write_status_json(tmp_path, _one_test_snapshot())
    render_final(tmp_path, comparisons=[TestComparison(model_id="Lib.A", passed=True)])
    (tr,) = _body_rows((tmp_path / "dashboard.html").read_text(encoding="utf-8"))
    assert _attrs(tr)["data-sort-warnings"] == "0"


def test_the_pending_badge_is_styled(tmp_path):
    """A SIMULATED row's badge has the theme's "no verdict yet" fill."""
    _write_status_json(tmp_path, _one_test_snapshot())
    render_final(tmp_path)
    html = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert '<span class="status-badge pending">SIMULATED</span>' in html
    assert re.search(
        r"\.status-badge\.pending\s*\{\s*background:\s*var\(--dstf-pending-fill\)",
        html,
    )


def test_an_absent_text_value_is_empty_to_the_filter(tmp_path):
    """The cell reads "—"; the filter attribute is empty, so a regex `.`
    matches only rows that have a message, and "—" matches none."""
    _two_row_final_page(tmp_path)
    html = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    a, b = (_attrs(tr) for tr in _body_rows(html))
    assert a["data-detail"] == "a message"
    assert b["data-detail"] == "" and b["data-ref"] == ""
    assert '<td class="detail-cell"' in _body_rows(html)[1]
    assert "—" in _body_rows(html)[1]


def test_a_sidecar_with_an_unknown_status_does_not_break_the_page(tmp_path):
    snapshot = _one_test_snapshot()
    _write_status_json(tmp_path, snapshot)
    _write_sidecar(
        tmp_path,
        {"model_id": "Lib.A", "status": "BOGUS", "n_vars": 2, "n_vars_passed": 1},
    )
    render_final(tmp_path)
    (tr,) = _body_rows((tmp_path / "dashboard.html").read_text(encoding="utf-8"))
    # The row keeps its own state; the sidecar's numbers still land.
    assert _attrs(tr)["data-status"] == "pending"
    assert _attrs(tr)["data-vars"] == "1/2"


def _passing_sidecar(ref_id: str) -> dict:
    return {
        "model_id": "Lib.A",
        "written_at": 2.0,
        "status": "PASS",
        "worst_nrmse": 1e-6,
        "n_unscored": 0,
        "n_vars": 1,
        "n_vars_passed": 1,
        "n_warnings": 0,
        "ref_id": ref_id,
        "ref_file": f"file:///refs/{ref_id}.json",
        "total_wall": 4.0,
    }


def _sidecar_in(work_dir: Path, report_dir: str, summary: dict) -> None:
    d = work_dir / "reports" / report_dir
    d.mkdir(parents=True, exist_ok=True)
    (d / "comparison_data.json").write_text(json.dumps({"summary": summary}))


def test_the_comparisons_reference_wins_over_the_sidecars(tmp_path):
    """`compare --backend <other>` on another backend's work dir: the run
    pre-populated report_dir ref_0005 and its sidecar names ref_0005, but
    this command scored against ref_0007. The row names the baseline its
    verdict came from, and links to no other one."""
    from dstf.comparison.types import TestComparison

    snapshot = _one_test_snapshot()
    snapshot["start_wall"] = 1.0
    snapshot["tests"][0]["report_dir"] = "ref_0005"
    _write_status_json(tmp_path, snapshot)
    _sidecar_in(tmp_path, "ref_0005", _passing_sidecar("ref_0005"))
    comp = TestComparison(
        model_id="Lib.A", passed=False, variables=[_failing_var()], test_id="0007"
    )
    (row,) = build_dashboard_context(tmp_path, mode="final", comparisons=[comp])[
        "tests"
    ]
    assert (row.ref_id, row.ref_file) == ("ref_0007", None)
    assert row.total_wall == 4.0  # what only the sidecar knows survives
    render_final(tmp_path, comparisons=[comp])
    (tr,) = _body_rows((tmp_path / "dashboard.html").read_text(encoding="utf-8"))
    assert _attrs(tr)["data-ref"] == "ref_0007"
    assert "ref_0005.json" not in tr
    # No sidecar: the report_dir's ref_0005 loses to the comparison's too.
    (tmp_path / "reports" / "ref_0005" / "comparison_data.json").unlink()
    (row,) = build_dashboard_context(tmp_path, mode="final", comparisons=[comp])[
        "tests"
    ]
    assert (row.ref_id, row.ref_file) == ("ref_0007", None)


def test_a_sidecar_that_agrees_with_the_comparison_keeps_its_link(tmp_path):
    from dstf.comparison.types import TestComparison

    snapshot = _one_test_snapshot()
    snapshot["start_wall"] = 1.0
    snapshot["tests"][0]["report_dir"] = "ref_0005"
    _write_status_json(tmp_path, snapshot)
    _sidecar_in(tmp_path, "ref_0005", _passing_sidecar("ref_0005"))
    comp = TestComparison(model_id="Lib.A", passed=True, test_id="0005")
    (row,) = build_dashboard_context(tmp_path, mode="final", comparisons=[comp])[
        "tests"
    ]
    assert (row.ref_id, row.ref_file) == ("ref_0005", "file:///refs/ref_0005.json")


def test_the_comparisons_numbers_replace_the_sidecars(tmp_path):
    """Every number the comparison has replaces the sidecar's: a sidecar
    with 2 unscored leaves over 3 variables, a comparison with 1 scored."""
    from dstf.comparison.types import TestComparison

    snapshot = _one_test_snapshot()
    snapshot["start_wall"] = 1.0
    _write_status_json(tmp_path, snapshot)
    _write_sidecar(
        tmp_path,
        {
            **_passing_sidecar("ref_0042"),
            "worst_nrmse": None,
            "n_unscored": 2,
            "n_vars": 3,
            "n_vars_passed": 3,
        },
    )
    comp = TestComparison(model_id="Lib.A", passed=False, variables=[_failing_var()])
    (row,) = build_dashboard_context(tmp_path, mode="final", comparisons=[comp])[
        "tests"
    ]
    assert (row.n_unscored, row.n_vars, row.n_vars_passed) == (0, 1, 0)


# ---------------------------------------------------------------------------
# A page without --report shows each test's timing, from the run_stats.json
# its run kept beside the result; a fresh sidecar's walls win.
# ---------------------------------------------------------------------------


def _timing_snapshot(tmp_path, start_wall=None):
    snapshot = {
        "total": 1,
        "elapsed": 5.0,
        "counts": {"passed": 1},
        "tests": [
            {
                "test_key": "test_0001",
                "model_id": "Lib.A",
                "status": "passed",
                "report_dir": "ref_0001",
            }
        ],
        "updated_at": 0.0,
    }
    if start_wall is not None:
        snapshot["start_wall"] = start_wall
    _write_status_json(tmp_path, snapshot)
    kept = tmp_path / "test_0001"
    kept.mkdir()
    (kept / "run_stats.json").write_text(
        json.dumps(
            {
                "model_id": "Lib.A",
                "translation_wall": 3.333,
                "sim_wall": 5.555,
                "elapsed": 10.004,
                "statistics": None,
            }
        )
    )


def _timing_sidecar(tmp_path, **summary):
    d = tmp_path / "reports" / "ref_0001"
    d.mkdir(parents=True)
    (d / "comparison_data.json").write_text(
        json.dumps({"summary": {"model_id": "Lib.A", **summary}})
    )


def test_a_page_without_sidecars_shows_the_kept_timing(tmp_path):
    _timing_snapshot(tmp_path)
    row = build_dashboard_context(tmp_path, mode="final")["tests"][0]
    assert (row.translation_wall, row.sim_wall, row.total_wall) == (3.33, 5.55, 10.0)
    render_final(tmp_path)
    html = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert "5.55" in html and "3.33" in html and "10.00" in html


def test_a_fresh_sidecars_walls_win(tmp_path):
    _timing_snapshot(tmp_path, start_wall=100.0)
    _timing_sidecar(tmp_path, written_at=200.0, sim_wall=1.0, total_wall=2.0)
    row = build_dashboard_context(tmp_path, mode="final")["tests"][0]
    assert (row.translation_wall, row.sim_wall, row.total_wall) == (None, 1.0, 2.0)


def test_a_stale_sidecar_falls_through_to_the_kept_timing(tmp_path):
    _timing_snapshot(tmp_path, start_wall=100.0)
    _timing_sidecar(tmp_path, written_at=50.0, sim_wall=1.0, total_wall=2.0)
    row = build_dashboard_context(tmp_path, mode="final")["tests"][0]
    assert (row.sim_wall, row.total_wall) == (5.55, 10.0)


def test_a_live_page_reads_no_kept_timing(tmp_path):
    _timing_snapshot(tmp_path)
    row = build_dashboard_context(tmp_path, mode="live")["tests"][0]
    assert row.sim_wall is None


# ---------------------------------------------------------------------------
# A final page lists status.json's rows and every test with a comparison in
# hand (D136 amendment): each run rewrites status.json with only its own
# scope, so `compare` after a `--filter` run listed only that run's tests.
# ---------------------------------------------------------------------------


def _scoped_run(tmp_path):
    """status.json names B alone (a `--filter` run); the manifest keys A, B,
    C and D; A kept its timing."""
    _write_status_json(
        tmp_path,
        {
            "total": 1,
            "elapsed": 1.0,
            "counts": {"passed": 1},
            "start_wall": 100.0,
            "tests": [
                {
                    "test_key": "test_0002",
                    "model_id": "Lib.B",
                    "status": "passed",
                    "report_dir": "test_0002",
                }
            ],
            "updated_at": 101.0,
        },
    )
    manifest = {
        "test_0003": {"model_id": "Lib.C", "ref_id": None},
        "test_0001": {"model_id": "Lib.A", "ref_id": "ref_0007"},
        "test_0002": {"model_id": "Lib.B", "ref_id": None},
        "test_0004": {"model_id": "Lib.D", "ref_id": None},
    }
    (tmp_path / "batch_manifest.json").write_text(json.dumps(manifest))
    kept = tmp_path / "test_0001"
    kept.mkdir()
    (kept / "run_stats.json").write_text(
        json.dumps(
            {
                "model_id": "Lib.A",
                "translation_wall": None,
                "sim_wall": 2.5,
                "elapsed": 3.0,
                "statistics": None,
            }
        )
    )


def test_a_final_page_lists_every_test_with_a_comparison(tmp_path):
    from dstf.comparison.types import TestComparison

    _scoped_run(tmp_path)
    comparisons = [
        TestComparison(model_id="Lib.A", passed=False),
        TestComparison(model_id="Lib.Z", passed=True),  # in no manifest
        TestComparison(model_id="Lib.C", passed=True),
        TestComparison(model_id="Lib.B", passed=True),
    ]
    ctx = build_dashboard_context(tmp_path, mode="final", comparisons=comparisons)
    rows = ctx["tests"]
    # status.json's order, then the manifest's, then the rest by model id;
    # D has neither a status row nor a comparison.
    assert [r.model_id for r in rows] == ["Lib.B", "Lib.C", "Lib.A", "Lib.Z"]
    assert [r.status for r in rows] == ["PASS", "PASS", "FAIL", "PASS"]
    assert ctx["total"] == 4
    a = rows[2]
    assert (a.test_key, a.ref_id, a.report_dir) == ("test_0001", "ref_0007", "ref_0007")
    assert (a.sim_wall, a.total_wall) == (2.5, 3.0)  # its kept timing
    z = rows[3]
    assert (z.test_key, z.ref_id, z.report_dir) == (None, None, None)
    render_final(tmp_path, comparisons=comparisons)
    html = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert [_attrs(tr)["data-status"] for tr in _body_rows(html)] == [
        "pass",
        "pass",
        "fail",
        "pass",
    ]
    assert "4 tests" in html


def test_a_live_page_lists_only_status_rows(tmp_path):
    from dstf.comparison.types import TestComparison

    _scoped_run(tmp_path)
    comparisons = [TestComparison(model_id="Lib.A", passed=False)]
    ctx = build_dashboard_context(tmp_path, mode="live", comparisons=comparisons)
    assert [r.model_id for r in ctx["tests"]] == ["Lib.B"]
    assert ctx["total"] == 1


def test_a_final_page_without_comparisons_lists_status_rows(tmp_path):
    """A run that scored nothing (`run --accept`, an abort, the tool's
    Rerun before its reload) lists its own rows, reading SIMULATED."""
    _scoped_run(tmp_path)
    rows = build_dashboard_context(tmp_path, mode="final")["tests"]
    assert [r.model_id for r in rows] == ["Lib.B"]


def test_manifest_rows_order_status_then_manifest_then_model_id(tmp_path):
    from dstf.comparison.types import TestComparison

    _scoped_run(tmp_path)
    comparisons = [
        TestComparison(model_id=m, passed=True)
        for m in ("Lib.Z", "Lib.D", "Lib.Y", "Lib.A", "Lib.B")
    ]
    ctx = build_dashboard_context(tmp_path, mode="final", comparisons=comparisons)
    assert [r.model_id for r in ctx["tests"]] == [
        "Lib.B",  # status.json's
        "Lib.A",  # the manifest's order: A before D
        "Lib.D",
        "Lib.Y",  # keyed by no manifest: by model id
        "Lib.Z",
    ]


def _manifest_only(tmp_path, manifest_text: str, comparisons) -> list:
    """The final rows over a work dir with no status.json and this
    ``batch_manifest.json`` text."""
    (tmp_path / "batch_manifest.json").write_text(manifest_text, encoding="utf-8")
    ctx = build_dashboard_context(tmp_path, mode="final", comparisons=comparisons)
    return ctx["tests"]


def test_a_model_under_two_manifest_keys_takes_the_last(tmp_path):
    """As ``last_test_keys`` does: the later key is the directory the last
    run wrote."""
    from dstf.comparison.types import TestComparison

    manifest = {
        "test_0001": {"model_id": "Lib.A", "ref_id": None},
        "test_0002": {"model_id": "Lib.B", "ref_id": None},
        "test_0003": {"model_id": "Lib.A", "ref_id": "ref_0009"},
    }
    comps = [TestComparison(model_id=m, passed=True) for m in ("Lib.A", "Lib.B")]
    rows = _manifest_only(tmp_path, json.dumps(manifest), comps)
    a = next(r for r in rows if r.model_id == "Lib.A")
    assert (a.test_key, a.ref_id, a.report_dir) == ("test_0003", "ref_0009", "ref_0009")
    assert [r.model_id for r in rows].count("Lib.A") == 1


def test_manifest_rows_read_a_malformed_manifest_as_data(tmp_path):
    """A non-string ref_id, an entry that is not an object, one without a
    model id: each such entry is read for what it holds or skipped — never a
    crash, and the manifest file is not moved."""
    from dstf.comparison.types import TestComparison

    manifest = {
        "test_0001": {"model_id": "Lib.A", "ref_id": 7},
        "test_0002": "Lib.B",
        "test_0003": {"ref_id": "ref_0001"},
        "test_0004": {"model_id": 12},
        "test_0005": {"model_id": "Lib.C", "ref_id": "ref_0005"},
    }
    comps = [
        TestComparison(model_id=m, passed=True) for m in ("Lib.A", "Lib.B", "Lib.C")
    ]
    rows = {
        r.model_id: r for r in _manifest_only(tmp_path, json.dumps(manifest), comps)
    }
    assert list(rows) == ["Lib.A", "Lib.C", "Lib.B"]
    a = rows["Lib.A"]
    assert (a.test_key, a.ref_id, a.report_dir) == ("test_0001", None, "test_0001")
    b = rows["Lib.B"]  # not an object: listed by model id, keyed by nothing
    assert (b.test_key, b.report_dir) == (None, None)
    c = rows["Lib.C"]
    assert (c.test_key, c.ref_id) == ("test_0005", "ref_0005")
    assert (tmp_path / "batch_manifest.json").exists()


def test_a_corrupt_manifest_lists_the_scored_tests_by_model_id(tmp_path):
    from dstf.comparison.types import TestComparison

    comps = [TestComparison(model_id=m, passed=True) for m in ("Lib.B", "Lib.A")]
    for text in ('{"test_0001": {"model_id": "Lib.A"', '["Lib.A"]', "null"):
        rows = _manifest_only(tmp_path, text, comps)
        assert [(r.model_id, r.test_key) for r in rows] == [
            ("Lib.A", None),
            ("Lib.B", None),
        ]
        assert (tmp_path / "batch_manifest.json").read_text(encoding="utf-8") == text


# ---------------------------------------------------------------------------
# A row the final page adds beyond status.json is judged against its OWN
# run: its sidecar is stale when written before that test's last run started
# (its ``batch_manifest.json`` entry's ``last_run_at``), not before the last
# run's start — which every sidecar outside the last run's scope was.
# ---------------------------------------------------------------------------


def _sidecar_at(work_dir: Path, report_dir: str, model_id: str, written_at: float):
    d = work_dir / "reports" / report_dir
    d.mkdir(parents=True, exist_ok=True)
    (d / "comparison_data.json").write_text(
        json.dumps(
            {
                "summary": {
                    "model_id": model_id,
                    "written_at": written_at,
                    "status": "PASS",
                    "ref_file": f"../../refs/{report_dir}.json",
                }
            }
        ),
        encoding="utf-8",
    )


def _last_run_at(work_dir: Path, **by_key: float) -> None:
    """Stamp manifest entries' ``last_run_at``, as ``assign_test_keys`` does
    at a run's start."""
    path = work_dir / "batch_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for key, at in by_key.items():
        manifest[key]["last_run_at"] = at
    path.write_text(json.dumps(manifest), encoding="utf-8")


def _final_row(work_dir: Path, comps, model_id: str):
    ctx = build_dashboard_context(work_dir, mode="final", comparisons=comps)
    (row,) = [r for r in ctx["tests"] if r.model_id == model_id]
    return row


def test_an_added_rows_sidecar_is_judged_against_its_own_run(tmp_path, capsys):
    import os

    from dstf.comparison.types import TestComparison

    _scoped_run(tmp_path)  # status.json: B alone, started at 100.0
    # A's own run started at 50.0; its sidecar was written at 60.0. Its
    # run_stats.json's mtime (now) is not the anchor.
    _last_run_at(tmp_path, test_0001=50.0)
    _sidecar_at(tmp_path, "ref_0007", "Lib.A", 60.0)
    comps = [TestComparison(model_id="Lib.A", passed=True)]
    a = _final_row(tmp_path, comps, "Lib.A")
    assert a.ref_file == "../../refs/ref_0007.json"  # fresh against its own run
    assert "stale sidecar" not in capsys.readouterr().err
    # A ran again at 70.0: the sidecar written at 60.0 is its earlier run's,
    # whatever its run_stats.json's mtime says.
    _last_run_at(tmp_path, test_0001=70.0)
    os.utime(tmp_path / "test_0001" / "run_stats.json", (10.0, 10.0))
    a = _final_row(tmp_path, comps, "Lib.A")
    assert a.ref_file is None
    err = capsys.readouterr().err
    assert "stale sidecar for 'Lib.A'" in err
    assert "last_run_at=70.0" in err and "start_wall" not in err


def test_added_rows_without_run_stats_refuse_sidecars_older_than_their_run(
    tmp_path, capsys
):
    """The review's scenario b4: a one-test run, the others' tests keeping
    no run_stats.json. A sidecar written before its test's ``last_run_at``
    is refused; one written after is taken — per test, not per page."""
    from dstf.comparison.types import TestComparison

    _scoped_run(tmp_path)  # B alone in status.json; only A keeps run_stats
    _last_run_at(tmp_path, test_0003=50.0, test_0004=50.0)
    _sidecar_at(tmp_path, "test_0003", "Lib.C", 40.0)  # before C's run: stale
    _sidecar_at(tmp_path, "test_0004", "Lib.D", 60.0)  # after D's run: fresh
    comps = [TestComparison(model_id=m, passed=True) for m in ("Lib.C", "Lib.D")]
    ctx = build_dashboard_context(tmp_path, mode="final", comparisons=comps)
    rows = {r.model_id: r for r in ctx["tests"]}
    assert rows["Lib.C"].ref_file is None
    assert rows["Lib.D"].ref_file == "../../refs/test_0004.json"
    err = capsys.readouterr().err
    assert "stale sidecar for 'Lib.C'" in err
    assert "Lib.D" not in err


def test_an_added_row_whose_manifest_entry_has_no_last_run_at_is_not_guarded(
    tmp_path, capsys
):
    """A manifest entry without ``last_run_at`` (or with a value that is not
    a number) is no guard, as for a status.json without ``start_wall``."""
    from dstf.comparison.types import TestComparison

    _scoped_run(tmp_path)
    _last_run_at(tmp_path, test_0004="yesterday")
    _sidecar_at(tmp_path, "test_0003", "Lib.C", 1.0)
    _sidecar_at(tmp_path, "test_0004", "Lib.D", 1.0)
    comps = [TestComparison(model_id=m, passed=True) for m in ("Lib.C", "Lib.D")]
    ctx = build_dashboard_context(tmp_path, mode="final", comparisons=comps)
    rows = {r.model_id: r for r in ctx["tests"]}
    assert rows["Lib.C"].ref_file == "../../refs/test_0003.json"
    assert rows["Lib.D"].ref_file == "../../refs/test_0004.json"
    assert "stale sidecar" not in capsys.readouterr().err


def test_a_merge_run_keeps_the_other_tests_reference_links(
    tmp_path, monkeypatch, capsys
):
    """`run --report`, then `run --filter <one> --merge`: status.json holds
    the one; the others' sidecars are from their own (earlier) run and
    still fresh — their Reference links stay, and no stale warning."""
    from conftest import python_example_copy
    from dstf.cli import main

    config, tests = python_example_copy(tmp_path)
    work = config.work_dir
    monkeypatch.setattr("dstf.reporting.report_suite.open_in_browser", lambda p: None)
    argv = ["--config", str(config.config_file), "run", "--work-dir", str(work)]
    main([*argv, "--report"])

    def links(html: str) -> dict[str, bool]:
        return {
            _attrs(tr)["data-model"]: 'title="Open reference JSON"' in tr
            for tr in _body_rows(html)
        }

    first = links((work / "dashboard.html").read_text(encoding="utf-8"))
    assert len(first) == len(tests) and sum(first.values()) >= 2
    capsys.readouterr()
    one = tests[0].model_id
    main([*argv, "--filter", one, "--merge"])
    err = capsys.readouterr().err
    after = links((work / "dashboard.html").read_text(encoding="utf-8"))
    others = {m: v for m, v in first.items() if m != one}
    assert {m: after[m] for m in others} == others
    assert not [
        line for line in err.splitlines() if "stale sidecar" in line and one not in line
    ]


# ---------------------------------------------------------------------------
# The status bar of a final page that lists more tests than the last run ran.
# ---------------------------------------------------------------------------


def _status_bar(html: str) -> str:
    m = re.search(r'<div class="status-bar">(.*?)</div>', html, re.S)
    assert m is not None
    return " ".join(m.group(1).split())


def test_a_final_page_beyond_the_last_run_says_what_the_run_ran(tmp_path):
    from dstf.comparison.types import TestComparison

    _scoped_run(tmp_path)  # status.json: B alone, elapsed 1.0
    comps = [TestComparison(model_id=m, passed=True) for m in ("Lib.A", "Lib.B")]
    render_final(tmp_path, comparisons=comps)
    bar = _status_bar((tmp_path / "dashboard.html").read_text(encoding="utf-8"))
    assert bar.startswith("Final report · 2 tests · last run: 1 test in 1.0s")
    assert "ran for" not in bar


def test_a_final_page_of_the_last_run_alone_says_ran_for(tmp_path):
    from dstf.comparison.types import TestComparison

    _scoped_run(tmp_path)
    render_final(tmp_path, comparisons=[TestComparison(model_id="Lib.B", passed=True)])
    bar = _status_bar((tmp_path / "dashboard.html").read_text(encoding="utf-8"))
    assert bar.startswith("Final report · 1 test · ran for 1s")


def test_a_final_page_with_no_status_names_no_last_run(tmp_path):
    """No status.json (a `compare` over a work dir whose run kept none): the
    page lists the tests scored and says nothing of a run it cannot see —
    not "last run: 0 tests in 0.0s", nor a finish at the epoch."""
    from dstf.comparison.types import TestComparison

    comps = [TestComparison(model_id=m, passed=True) for m in ("Lib.A", "Lib.B")]
    render_final(tmp_path, comparisons=comps)
    bar = _status_bar((tmp_path / "dashboard.html").read_text(encoding="utf-8"))
    assert bar == "Final report · 2 tests"


def test_every_filter_carries_its_kinds_hint(tmp_path):
    """The dashboard's filter row and the tool's Suite table read one
    table of hints (``rows.FILTER_HINTS``): a text filter's placeholder
    ``*Pipe*``, a number filter's ``>1e-4``, each with its explanation."""
    import html as html_lib

    from dstf.reporting.rows import COLUMNS, FILTER_HINTS

    assert FILTER_HINTS["text"][0] == "*Pipe*"
    assert FILTER_HINTS["number"][0] == ">1e-4"
    page = _two_row_final_page(tmp_path)
    found = re.findall(
        r'data-col-filter="([^"]*)" data-kind="([^"]*)" placeholder="([^"]*)" '
        r'title="([^"]*)"',
        page,
    )
    assert [
        (k, kind, html_lib.unescape(p), html_lib.unescape(t)) for k, kind, p, t in found
    ] == [(c.key, c.kind, *FILTER_HINTS[c.kind]) for c in COLUMNS if c.kind != "status"]


# ---------------------------------------------------------------------------
# The tool's version, short: what the tool's header names the backend by.
# ---------------------------------------------------------------------------


def test_the_short_tool_version_names_dymolas_release():
    from dstf.reporting.dashboard_render import short_tool_version

    assert (
        short_tool_version("Dymola Version 2026x, 2025-10-10", "Dymola")
        == "Dymola 2026x"
    )
    assert (
        short_tool_version("Dymola Version 2024x Refresh 1, 2024-04-12", "Dymola")
        == "Dymola 2024x Refresh 1"
    )


def test_the_short_tool_version_names_openmodelicas_release():
    from dstf.reporting.dashboard_render import short_tool_version

    for raw in (
        "OpenModelica 1.27.0~dev-123-gabcdef",
        "OpenModelica 1.27.0-dev",
        "OpenModelica v1.27.0 (64-bit)",
    ):
        assert short_tool_version(raw, "OpenModelica") == "OpenModelica 1.27.0", raw


def test_the_short_tool_version_is_otherwise_the_backends_name():
    from dstf.reporting.dashboard_render import short_tool_version

    assert short_tool_version("Python 3.12.3", "Python") == "Python"
    assert short_tool_version("FMPy 0.3.20", "FMPy") == "FMPy"
    assert short_tool_version(None, "Dymola") == "Dymola"
    assert short_tool_version("", "Dymola") == "Dymola"


# ---------------------------------------------------------------------------
# Sim (s) falls back to the result file's CPU time, marked, where no wall
# time was kept (no run_stats.json, no sidecar walls): a Dymola result.
# ---------------------------------------------------------------------------


def _cpu_only(tmp_path):
    """status.json lists Lib.A; no run_stats.json, no sidecar."""
    _write_status_json(
        tmp_path,
        {
            "total": 1,
            "counts": {"passed": 1},
            "tests": [
                {
                    "test_key": "test_0001",
                    "model_id": "Lib.A",
                    "status": "passed",
                    "report_dir": "test_0001",
                }
            ],
            "updated_at": 0.0,
        },
    )
    from dstf.backends.types import TestResult
    from dstf.comparison.types import TestComparison

    comp = TestComparison(model_id="Lib.A", passed=True)
    # As Dymola's read_result folds its diagnostics into the statistics.
    result = TestResult(
        model_id="Lib.A",
        success=True,
        statistics={"CPUtime": 0.046, "EventCounter": 0.0},
    )
    return comp, result


def test_a_dymola_result_without_run_stats_shows_its_cpu_time(tmp_path):
    from dstf.reporting.rows import SIM_CPU_TIP

    comp, result = _cpu_only(tmp_path)
    render_final(tmp_path, comparisons=[comp], results={"Lib.A": result})
    html = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert (
        f'<td class="tagged" title="{SIM_CPU_TIP}">0.05 <span class="cell-tag">cpu</span></td>'
        in html
    )
    assert 'data-sort-sim="0.046"' in html


def test_a_kept_wall_wins_over_the_results_cpu_time(tmp_path):
    comp, result = _cpu_only(tmp_path)
    kept = tmp_path / "test_0001"
    kept.mkdir()
    (kept / "run_stats.json").write_text(
        json.dumps(
            {
                "model_id": "Lib.A",
                "translation_wall": None,
                "sim_wall": 5.555,
                "elapsed": 6.0,
                "statistics": None,
            }
        )
    )
    ctx = build_dashboard_context(
        tmp_path, mode="final", comparisons=[comp], results={"Lib.A": result}
    )
    (row,) = ctx["tests"]
    assert (row.sim_wall, row.sim_cpu, row.sim_source) == (5.55, 0.046, "wall")
    render_final(tmp_path, comparisons=[comp], results={"Lib.A": result})
    html = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert "<td>5.55</td>" in html and 'class="cell-tag"' not in html


def test_only_a_refused_test_has_its_reason_as_the_message(tmp_path):
    """A comparison carries an ``error_message`` for more than a refusal —
    a NO_REF's "No reference baseline stored", a failed simulation's
    reason, a scored FAIL's or a PASS's stray one: none of those replaces
    what the run said in the Message column."""
    from test_reporters import _failing, _no_ref, _passing, _sim_failed

    makers = {
        "Lib.N": _no_ref,
        "Lib.S": _sim_failed,
        "Lib.F": _failing,
        "Lib.P": _passing,
    }
    snapshot = _one_test_snapshot()
    (entry,) = snapshot["tests"]
    snapshot["tests"] = [
        {**entry, "model_id": m, "test_key": f"test_{i:04d}", "detail": f"said {m}"}
        for i, m in enumerate(makers, 1)
    ]
    for t in snapshot["tests"]:
        t["report_dir"] = t["test_key"]
    _write_status_json(tmp_path, snapshot)
    comparisons = []
    for model_id, make in makers.items():
        comp = make(model_id)
        comp.error_message = comp.error_message or "some other reason"
        comparisons.append(comp)
    assert all(c.error_message for c in comparisons)
    assert any(c.variables for c in comparisons if c.model_id == "Lib.F")
    rows = build_dashboard_context(tmp_path, mode="final", comparisons=comparisons)[
        "tests"
    ]
    assert {r.model_id: r.message for r in rows} == {m: f"said {m}" for m in makers}
