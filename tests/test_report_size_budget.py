"""interactive.html payload guard.

Renders a 50-variable × 5000-sample test through the REAL report path
(``generate_comparison_plots``, tube leaves on half the variables) so the
page carries what a real page carries — the Python figure dicts with their
bands and error series — and asserts the file stays under a ceiling.

The ceiling is a regression guard, not a target: it catches decimation
silently switching off (5x the samples) or a figure duplicating its
arrays, and nothing finer. The owner's ruling of 2026-09-08 dropped the
old 5 MB budget — "freedom to do what is best, optimise later, within
reason" — after the figure layer measured 14 MB at this fixture while the
old test, which rendered a hand-built context with no figures, still
passed at 4 MB. Also verifies that decimation does NOT leak into the data
artifact (comparison_data.json shape).
"""

from __future__ import annotations

import json
import math

import numpy as np

from dstf.reporting.report_suite import _decimate_context_for_html

BUDGET_BYTES = 25 * 1024 * 1024  # regression ceiling, see the module docstring
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


def _render_real_page(tmp_path, n_vars: int, n_samples: int, cap: int):
    """The real report path: compare, then generate_comparison_plots."""
    from pathlib import Path

    from dstf.backends.types import TestResult, VariableResult
    from dstf.comparison.comparator import compare_test
    from dstf.discovery.test_registry import TestModel
    from dstf.reporting.report_suite import generate_comparison_plots

    trajectories = [_build_trajectory(i, n_samples) for i in range(n_vars)]
    ref = {
        "test_id": "ref_0001",
        "time": trajectories[0]["ref_time"],
        "variables": [
            {"index": i + 1, "name": t["name"], "values": t["ref_values"]}
            for i, t in enumerate(trajectories)
        ],
    }
    result = TestResult(
        model_id="TestLib.Wide.Fixture",
        success=True,
        variables=[
            VariableResult(
                index=i + 1,
                name=t["name"],
                time=np.asarray(t["act_time"]),
                values=np.asarray(t["act_values"]),
            )
            for i, t in enumerate(trajectories)
        ],
    )
    # Tube leaves on every other variable: bands ride into the figures.
    overrides = {
        t["name"]: {"mode": "tube", "tube_width_mode": "band", "tube_abs": 0.05}
        for i, t in enumerate(trajectories)
        if i % 2 == 0
    }
    test = TestModel(
        model_id="TestLib.Wide.Fixture",
        source_file=Path("fixture.mo"),
        source_package="TestLib",
        short_name="Fixture",
        n_vars=n_vars,
        variable_overrides=overrides,
    )
    comp = compare_test(test, result, ref)
    plot_dir = tmp_path / "plots"
    html = generate_comparison_plots(
        "TestLib.Wide.Fixture",
        ref,
        result,
        comp.variables,
        plot_dir,
        test_model=test,
        metric_tree=comp.metric_tree,
        max_embedded_samples=cap,
    )
    return html, plot_dir


def test_real_interactive_html_stays_under_the_ceiling(tmp_path):
    html, _ = _render_real_page(tmp_path, N_VARS, N_SAMPLES, MAX_EMBEDDED)
    page = html.read_text(encoding="utf-8")
    size = len(page.encode("utf-8"))
    # The guard must be looking at a page with figures in it.
    assert "FIGURES:" in page and 'tube/band"' in page
    assert size < BUDGET_BYTES, (
        f"interactive.html = {size / 1024 / 1024:.2f} MB exceeds the "
        f"{BUDGET_BYTES / 1024 / 1024:.0f} MB ceiling at "
        f"{N_VARS} vars × {N_SAMPLES} samples (cap {MAX_EMBEDDED})"
    )


def test_figures_are_decimated_to_the_cap(tmp_path):
    """The cap must reach the figures, or the ceiling is doing the
    decimation's job."""
    import re

    html, _ = _render_real_page(tmp_path, 2, N_SAMPLES, MAX_EMBEDDED)
    page = html.read_text(encoding="utf-8")
    start = page.index("FIGURES:")
    block = page[start : page.index("DIAG_FIGURES:", start)]
    figures = json.loads(re.search(r"FIGURES:\s*(\{.*?\}),\s*$", block, re.S).group(1))
    for name, fig in figures.items():
        for tr in fig["data"]:
            n = len(tr.get("x") or [])
            assert n <= 2 * MAX_EMBEDDED + 2, (name, tr.get("name"), n)
        actual = next(tr for tr in fig["data"] if tr["name"] == "Actual")
        assert actual["meta"]["full_samples"] == N_SAMPLES


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
