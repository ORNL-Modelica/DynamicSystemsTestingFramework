"""Golden-file HTML structure snapshot (D66 / PHASE_6_PLAN testing strategy).

Renders interactive.html against synthesized fixtures covering every mode
and hashes the structural DOM (with timestamps / trajectory data stripped)
to catch functional regressions without styling churn.

To refresh the snapshots after an intentional template change:

    UPDATE_GOLDEN=1 uv run pytest tests/test_interactive_html_snapshot.py
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import numpy as np
import pytest

from modelica_testing.reporting.plot_comparison import _decimate_context_for_html


GOLDEN_DIR = Path(__file__).parent / "golden"
UPDATE = os.environ.get("UPDATE_GOLDEN") == "1"


# ---------------------------------------------------------------------------
# Fixture synthesis — minimal template context per mode
# ---------------------------------------------------------------------------

def _common_trajectory(name: str, n: int = 50) -> dict:
    t = np.linspace(0.0, 10.0, n)
    return {
        "index": 1,
        "name": name,
        "act_time": t.tolist(),
        "act_values": np.sin(t).tolist(),
        "ref_time": t.tolist(),
        "ref_values": np.sin(t).tolist(),
    }


def _var_entry(mode: str, mode_values: dict, *,
               mode_controls_html: str = "",
               score_display: str = "",
               criterion: str = "",
               cli_authoritative: bool = False) -> dict:
    return {
        "name": "x",
        "passed": True,
        "nrmse": 1.0e-5,
        "rmse": 1.0e-5,
        "signal_range": 2.0,
        "max_abs_error": 1.0e-5,
        "max_abs_error_time": 5.0,
        "reference_final": 0.0,
        "actual_final": 0.0,
        "is_constant": False,
        "tolerance_used": 1.0e-4,
        "mode": mode,
        "score_display": score_display,
        "criterion": criterion,
        "tube_points_inside": None,
        "tube_worst_violation": None,
        "tube_worst_violation_time": None,
        "mode_controls_html": mode_controls_html,
        "mode_values": mode_values,
        "cli_authoritative": cli_authoritative,
    }


def _build_context(mode: str) -> dict:
    from modelica_testing.reporting.ui.mode_controls import get_mode_ui

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
    controls = ui.render(variable="x", values=values) if ui else ""
    cli_auth = mode in ("event-timing", "dominant-frequency")

    variables = [_var_entry(mode, values,
                            mode_controls_html=controls,
                            score_display="fixture",
                            criterion="fixture",
                            cli_authoritative=cli_auth)]

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
        "variables": variables,
        "diagnostic_plots": [],
        "diagnostic_summaries": [],
        "compared_plots": [],
        "nobaseline_plots": [],
        "artifacts": [],
        "trajectories": [_common_trajectory("x")],
        "diag_trajectories": [],
        "nobaseline_trajectories": [],
        "metric_tree_view": None,
        "spec_path": "",
    }
    _decimate_context_for_html(context, 1000)
    return context


def _render_interactive(context: dict) -> str:
    from jinja2 import Environment, FileSystemLoader

    tpl_dir = Path(__file__).resolve().parents[1] / "src" / "modelica_testing" / "reporting" / "templates"
    env = Environment(loader=FileSystemLoader(str(tpl_dir)), autoescape=True)
    return env.get_template("interactive.html").render(**context)


# ---------------------------------------------------------------------------
# Noise stripping — keep structural signal, drop data that naturally drifts
# ---------------------------------------------------------------------------

_NOISE_PATTERNS = [
    re.compile(r'const TRAJECTORIES = \[.*?\];', re.DOTALL),
    re.compile(r'const DIAG_TRAJECTORIES = \[.*?\];', re.DOTALL),
    re.compile(r'const NB_TRAJECTORIES = \[.*?\];', re.DOTALL),
    re.compile(r'value="[0-9eE.+\-]+"'),  # strip embedded numeric defaults
    re.compile(r'>[0-9]+\.[0-9]+e[+\-]?[0-9]+<'),  # stringified floats inside tags
]


def _structural_hash(html: str) -> str:
    stripped = html
    for pat in _NOISE_PATTERNS:
        stripped = pat.sub("", stripped)
    # Collapse whitespace so cosmetic indentation churn doesn't trip us.
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
        # First-run capture — record but don't fail.
        return

    expected = golden_file.read_text(encoding="utf-8").strip()
    assert h == expected, (
        f"Structural HTML hash changed for mode={mode}. "
        f"If intentional, re-run with UPDATE_GOLDEN=1 to refresh."
    )


def test_every_mode_fixture_contains_its_panel_signature():
    """Beyond hashing, assert each mode's rendered HTML carries its
    unique panel signature so a regression can't silently blank a mode."""
    signatures = {
        "nrmse": 'var-tol-input',  # existing slider, not auto-derived
        "tube": 'See tube editor below plot',  # 6.1.4 — cell defers to rich editor
        "final_only": 'var-tol-input',
        "range": 'data-field="min_value"',
        "event-timing": 'cli-authoritative',
        "dominant-frequency": 'cli-authoritative',
    }
    for mode, sig in signatures.items():
        ctx = _build_context(mode)
        html = _render_interactive(ctx)
        assert sig in html, f"Mode {mode!r} rendered HTML missing signature {sig!r}"


def test_mode_scorers_registry_present_in_output():
    ctx = _build_context("nrmse")
    html = _render_interactive(ctx)
    assert "const MODE_SCORERS" in html
    for key in ["nrmse:", "tube:", "range:", "final_only:"]:
        assert key in html, f"Scorer entry {key!r} missing from rendered HTML"
