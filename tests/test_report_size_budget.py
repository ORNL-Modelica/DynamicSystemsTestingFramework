"""Phase 6.0 — interactive.html payload budget.

Synthesizes a 50-variable × 5000-sample test-shape, runs it through the
decimation helper, renders the interactive template, and asserts the
output stays under the ~5 MB budget. Also verifies that decimation does
NOT leak into the data artifact (comparison_data.json shape).
"""

from __future__ import annotations

import json
import math

import numpy as np

from dstf.reporting.plot_comparison import _decimate_context_for_html

BUDGET_BYTES = 5 * 1024 * 1024  # 5 MB
N_VARS = 50
N_SAMPLES = 5000
# Default Config.max_embedded_samples. The cap is 1000 (not 2000) because
# each variable today embeds both act_time AND ref_time — once the time-array
# dedup follow-up (idea #47) lands, the cap can safely rise to 2000 at the
# same budget.
MAX_EMBEDDED = 1000


def _build_trajectory(idx: int, n: int) -> dict:
    """One variable's worth of fake trajectory data."""
    t = np.linspace(0.0, 10.0, n)
    act = np.sin(t + idx * 0.1) + 0.01 * np.sin(17.3 * t + idx)
    ref = np.sin(t + idx * 0.1)
    return {
        "index": idx,
        "name": f"var_{idx:03d}",
        "act_time": t.tolist(),
        "act_values": act.tolist(),
        "ref_time": t.tolist(),
        "ref_values": ref.tolist(),
    }


def _build_minimal_context(trajectories: list[dict]) -> dict:
    """Minimal Jinja context sufficient for interactive.html to render.

    Synthesizes a Stage-2-shaped context: one entry per unique variable in
    ``variables_by_name``, a flat AND tree with one leaf per variable.
    """
    variables_by_name = {
        t["name"]: {
            "name": t["name"],
            "trajectory": t,
            "overlays": [],
            "leaf_paths": [f"/metrics/children/{i}"],
        }
        for i, t in enumerate(trajectories)
    }
    tree_view = {
        "kind": "combinator",
        "combinator": "and",
        "path": "/metrics",
        "passed": True,
        "label": f"and[{len(trajectories)}]",
        "children": [
            {
                "kind": "leaf",
                "path": f"/metrics/children/{i}",
                "metric": "nrmse",
                "variable": t["name"],
                "params": {"tolerance": 1e-3},
                "against": "primary",
                "window": {},
                "children": [],
                "passed": True,
                "score": 1.2e-4,
                "label": t["name"],
                "mode_effective": "nrmse",
                "name": t["name"],
                "nrmse": 1.2e-4,
                "rmse": 1.2e-4,
                "signal_range": 2.0,
                "max_abs_error": 0.02,
                "max_abs_error_time": 5.0,
                "reference_final": 0.0,
                "actual_final": 0.0,
                "is_constant": False,
                "tolerance_used": 1e-3,
                "score_display": "NRMSE 1.2e-04",
                "criterion": "NRMSE < tol",
                "tube_points_inside": None,
                "tube_worst_violation": None,
                "tube_worst_violation_time": None,
                "mode_values": {"tolerance": 1e-3},
                "mode_controls_html": "",
                "window_controls_html": "",
                "window_values": {},
                "cli_authoritative": False,
            }
            for i, t in enumerate(trajectories)
        ],
    }
    return {
        "model_id": "TestLib.Wide.Fixture",
        "n_passed": len(trajectories),
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
        "trajectories": trajectories,
        "diag_trajectories": [],
        "nobaseline_trajectories": [],
        "metric_tree_view": None,
        "spec_path": "",
        "tree_view": tree_view,
        "variables_by_name": variables_by_name,
        "mode_schemas": {},
        "overlay_rows": [],
    }


def _render_interactive(context: dict) -> str:
    """Render interactive.html from the project Jinja template."""
    from pathlib import Path

    from jinja2 import Environment, FileSystemLoader

    tpl_dir = (
        Path(__file__).resolve().parents[1] / "src" / "dstf" / "reporting" / "templates"
    )
    env = Environment(loader=FileSystemLoader(str(tpl_dir)), autoescape=True)
    template = env.get_template("interactive.html")
    return template.render(**context)


def test_interactive_html_under_budget_after_decimation():
    trajectories = [_build_trajectory(i, N_SAMPLES) for i in range(N_VARS)]
    context = _build_minimal_context(trajectories)

    # Decimate in-place, same call site as the real reporter pipeline.
    _decimate_context_for_html(context, MAX_EMBEDDED)

    html = _render_interactive(context)
    size = len(html.encode("utf-8"))
    assert size < BUDGET_BYTES, (
        f"interactive.html = {size / 1024 / 1024:.2f} MB exceeds "
        f"{BUDGET_BYTES / 1024 / 1024:.1f} MB budget at "
        f"{N_VARS} vars × {N_SAMPLES} samples (cap {MAX_EMBEDDED})"
    )


def test_decimation_does_not_mutate_comparison_data_json():
    """comparison_data.json is serialized BEFORE decimation in the real
    pipeline. Round-trip that here: snapshot the context, serialize,
    decimate, verify the snapshot matches a re-parsed JSON (full-res)."""
    trajectories = [_build_trajectory(i, N_SAMPLES) for i in range(3)]
    context = _build_minimal_context(trajectories)

    # Mimic the reporter: JSON first, decimate after.
    json_bytes = json.dumps(context, default=str).encode("utf-8")
    _decimate_context_for_html(context, MAX_EMBEDDED)

    reparsed = json.loads(json_bytes)
    # Full-resolution on disk
    for traj in reparsed["trajectories"]:
        assert len(traj["act_time"]) == N_SAMPLES
        assert len(traj["ref_time"]) == N_SAMPLES
    # Decimated in memory (now bound for HTML)
    for traj in context["trajectories"]:
        assert len(traj["act_time"]) == MAX_EMBEDDED
        assert len(traj["ref_time"]) == MAX_EMBEDDED


def test_decimation_respects_below_threshold():
    """Small trajectories pass through unchanged."""
    trajectories = [_build_trajectory(i, 500) for i in range(3)]
    context = _build_minimal_context(trajectories)

    _decimate_context_for_html(context, MAX_EMBEDDED)

    for traj in context["trajectories"]:
        assert len(traj["act_time"]) == 500
        assert len(traj["ref_time"]) == 500


def test_decimation_endpoints_preserved():
    """Rendered-signal endpoints should match original — first/last samples
    must survive decimation, or the plot boundaries look wrong."""
    trajectories = [_build_trajectory(i, N_SAMPLES) for i in range(5)]
    originals = [
        (t["act_time"][0], t["act_time"][-1], t["act_values"][0], t["act_values"][-1])
        for t in trajectories
    ]
    context = _build_minimal_context(trajectories)

    _decimate_context_for_html(context, MAX_EMBEDDED)

    for traj, (t0, tN, v0, vN) in zip(context["trajectories"], originals):
        assert math.isclose(traj["act_time"][0], t0)
        assert math.isclose(traj["act_time"][-1], tN)
        assert math.isclose(traj["act_values"][0], v0)
        assert math.isclose(traj["act_values"][-1], vN)
