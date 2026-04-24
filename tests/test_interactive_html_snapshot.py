"""Golden-file HTML structure snapshot (D66 / PHASE_6_PLAN testing strategy).

Renders interactive.html against synthesized fixtures covering every mode
and hashes the structural DOM (with timestamps / trajectory data stripped)
to catch functional regressions without styling churn.

To refresh the snapshots after an intentional template change:

    UPDATE_GOLDEN=1 uv run pytest tests/test_interactive_html_snapshot.py

Stage-2 rewrite: the template now mounts a recursive ``SpecNodeView``
below one plot per unique variable. Fixtures build a single-leaf tree
view per mode; the golden hashes confirm the per-mode render path
exercises its registry entry end-to-end.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

import numpy as np
import pytest

from dstf.reporting.plot_comparison import _decimate_context_for_html
from dstf.reporting.ui.mode_controls import emit_mode_schemas


GOLDEN_DIR = Path(__file__).parent / "golden"
UPDATE = os.environ.get("UPDATE_GOLDEN") == "1"


# ---------------------------------------------------------------------------
# Fixture synthesis — minimal template context per mode
# ---------------------------------------------------------------------------

_MODE_TO_METRIC = {
    "nrmse": "nrmse",
    "tube": "tube",
    "final_only": "final-only",
    "range": "range",
    "event-timing": "event-timing",
    "dominant-frequency": "dominant-frequency",
}


def _trajectory(name: str, n: int = 50) -> dict:
    t = np.linspace(0.0, 10.0, n)
    return {
        "index": 1,
        "name": name,
        "act_time": t.tolist(),
        "act_values": np.sin(t).tolist(),
        "ref_time": t.tolist(),
        "ref_values": np.sin(t).tolist(),
    }


def _leaf(mode: str, mode_values: dict, *, path: str = "/metrics/children/0",
          mode_controls_html: str = "",
          window_controls_html: str = "") -> dict:
    metric = _MODE_TO_METRIC[mode]
    cli_auth = metric in ("event-timing", "dominant-frequency")
    return {
        "kind": "leaf",
        "path": path,
        "metric": metric,
        "variable": "x",
        "params": dict(mode_values),
        "against": "primary",
        "window": {},
        "children": [],
        "passed": True,
        "score": 1.0e-5,
        "label": "x",
        "name": "x",
        "mode_effective": metric if metric != "final-only" else "final_only",
        "nrmse": 1.0e-5,
        "rmse": 1.0e-5,
        "signal_range": 2.0,
        "max_abs_error": 1.0e-5,
        "max_abs_error_time": 5.0,
        "reference_final": 0.0,
        "actual_final": 0.0,
        "is_constant": False,
        "tolerance_used": 1.0e-4,
        "score_display": "fixture",
        "criterion": "fixture",
        "tube_points_inside": None,
        "tube_worst_violation": None,
        "tube_worst_violation_time": None,
        "mode_values": dict(mode_values),
        "mode_controls_html": mode_controls_html,
        "window_controls_html": window_controls_html,
        "window_values": {},
        "cli_authoritative": cli_auth,
    }


def _build_context(mode: str) -> dict:
    from dstf.reporting.ui.mode_controls import (
        get_mode_ui, render_window_controls_html,
    )

    mode_values_map = {
        "nrmse": {"tolerance": 1e-4},
        "tube": {"tube_width_mode": "rel", "tube_abs": 0.0, "tube_rel": 0.02,
                 "tube_min_width": 0.0, "tube_interpolation": "linear"},
        "final_only": {"tolerance": 1e-4},
        "range": {"min_value": -1.0, "max_value": 1.0},
        "event-timing": {"time_tolerance": 1e-3, "count_must_match": True},
        "dominant-frequency": {"rel_tolerance": 0.01, "min_frequency": 0.0},
    }
    values = mode_values_map[mode]
    ui = get_mode_ui(mode)
    controls_html = ui.render(variable="x", values=values) if ui else ""
    window_html = render_window_controls_html(variable="x", values={})

    leaf = _leaf(mode, values, mode_controls_html=controls_html,
                 window_controls_html=window_html)
    tree_view = {
        "kind": "combinator",
        "combinator": "and",
        "path": "/metrics",
        "passed": True,
        "label": "and[1]",
        "children": [leaf],
    }
    traj = _trajectory("x")
    variables_by_name = {
        "x": {
            "name": "x",
            "trajectory": traj,
            "overlays": [],
            "leaf_paths": [leaf["path"]],
        },
    }

    context = {
        "model_id": "Fixture.Mode." + mode.replace("-", "_"),
        "n_passed": 1,
        "sim_failed": False,
        "last_run_at": 0,
        "last_run_str": "",
        "warnings": [],
        "key_stats": {},
        "ref_info": [],
        "sim_params": [],
        "statistics_sections": [],
        "diagnostic_plots": [],
        "diagnostic_summaries": [],
        "compared_plots": [],
        "nobaseline_plots": [],
        "artifacts": [],
        "trajectories": [traj],
        "diag_trajectories": [],
        "nobaseline_trajectories": [],
        "metric_tree_view": None,
        "spec_path": "",
        "tree_view": tree_view,
        "variables_by_name": variables_by_name,
        "mode_schemas": emit_mode_schemas(),
        "overlay_rows": [],
    }
    _decimate_context_for_html(context, 1000)
    return context


def _render_interactive(context: dict) -> str:
    from jinja2 import Environment, FileSystemLoader

    tpl_dir = Path(__file__).resolve().parents[1] / "src" / "dstf" / "reporting" / "templates"
    env = Environment(loader=FileSystemLoader(str(tpl_dir)), autoescape=True)
    return env.get_template("interactive.html").render(**context)


# ---------------------------------------------------------------------------
# Noise stripping — keep structural signal, drop data that naturally drifts
# ---------------------------------------------------------------------------

_NOISE_PATTERNS = [
    # Stage-5 extraction: per-report data lives in window.MT_REPORT, which
    # contains the bulky inline JSON blobs — strip the whole block.
    re.compile(r'window\.MT_REPORT\s*=\s*\{.*?\};', re.DOTALL),
    re.compile(r'value="[0-9eE.+\-]+"'),  # strip embedded numeric defaults
    re.compile(r'>[0-9]+\.[0-9]+e[+\-]?[0-9]+<'),  # stringified floats inside tags
]


_JS_PATH = (
    Path(__file__).resolve().parents[1]
    / "src" / "dstf" / "reporting" / "templates" / "interactive.js"
)


def _read_js() -> str:
    return _JS_PATH.read_text(encoding="utf-8")


def _structural_hash(html: str) -> str:
    stripped = html
    for pat in _NOISE_PATTERNS:
        stripped = pat.sub("", stripped)
    stripped = re.sub(r'\s+', ' ', stripped).strip()
    return hashlib.sha256(stripped.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

MODES = ["nrmse", "tube", "final_only", "range", "event-timing", "dominant-frequency"]


@pytest.mark.parametrize("mode", MODES)
def test_interactive_html_structural_hash(mode):
    ctx = _build_context(mode)
    html = _render_interactive(ctx)
    h = _structural_hash(html)

    safe_mode = mode.replace("-", "_")
    golden_file = GOLDEN_DIR / f"interactive_{safe_mode}.hash"

    if UPDATE or not golden_file.exists():
        GOLDEN_DIR.mkdir(exist_ok=True)
        golden_file.write_text(h + "\n", encoding="utf-8")
        if UPDATE:
            pytest.skip(f"Updated golden: {golden_file.name}")
        return

    expected = golden_file.read_text(encoding="utf-8").strip()
    assert h == expected, (
        f"Structural HTML hash changed for mode={mode}. "
        f"If intentional, re-run with UPDATE_GOLDEN=1 to refresh."
    )


def test_every_mode_fixture_contains_its_leaf_metric_in_data():
    """The rendered HTML carries each mode's leaf metric inside
    ``window.MT_REPORT.TREE_VIEW``; that keeps the per-mode render
    path honest without grepping for JS tokens that now live in
    interactive.js."""
    metric_signatures = {
        "nrmse": '"metric": "nrmse"',
        "tube": '"metric": "tube"',
        "final_only": '"metric": "final-only"',
        "range": '"metric": "range"',
        "event-timing": '"metric": "event-timing"',
        "dominant-frequency": '"metric": "dominant-frequency"',
    }
    for mode, sig in metric_signatures.items():
        ctx = _build_context(mode)
        html = _render_interactive(ctx)
        assert sig in html, f"Mode {mode!r} rendered HTML missing leaf signature {sig!r}"


def test_html_loads_interactive_js():
    """Stage-5 extraction: template must reference the standalone JS file."""
    ctx = _build_context("nrmse")
    html = _render_interactive(ctx)
    assert '<script src="interactive.js"></script>' in html
    assert "window.MT_REPORT" in html


def test_interactive_js_exports_required_globals():
    """The standalone JS file must set up the same state the Stage-2
    template + Stage-4 structural editor + Stage-5 plot-editor registries
    expect. Checked by grep here (fast smoke); behavior checked by
    Playwright."""
    js = _read_js()
    for symbol in [
        "MODEL_ID", "TREE_VIEW", "VARIABLES_BY_NAME", "MODE_SCHEMAS",
        "leafState", "WORKING_TREE", "activeLeafPath",
        "MODE_PLOT_CONTRIBUTIONS", "MODE_PLOT_EDITORS",
        "activateLeaf", "deactivateLeaf",
        "buildPatchData", "nodeToSpec",
        "buildWindowBrushControl",
    ]:
        assert symbol in js, f"interactive.js missing expected symbol {symbol!r}"


def test_plot_contribution_registry_present_in_js():
    js = _read_js()
    assert "MODE_PLOT_CONTRIBUTIONS" in js
    for key in ["nrmse", "final-only", "range", "tube", "event-timing", "dominant-frequency"]:
        assert f"'{key}'" in js, f"Contribution entry {key!r} missing from interactive.js"


def test_plot_editor_registry_wires_tube_and_range():
    js = _read_js()
    assert "MODE_PLOT_EDITORS['tube']" in js
    assert "MODE_PLOT_EDITORS['range']" in js
    assert "shapePosition" in js  # range uses Plotly's shape-drag config
    # Tube v2 binds its Shift+click/drag/right-click via capture-phase DOM
    # events (not plotly_click) so Plotly's pan/zoom doesn't swallow them.
    assert "addEventListener('mousedown'" in js
    assert "addEventListener('contextmenu'" in js
