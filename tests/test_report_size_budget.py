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
passed at 4 MB.
"""

from __future__ import annotations

import numpy as np

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
    assert "PLOT_FIGURES:" in page and 'tube/band"' in page
    assert size < BUDGET_BYTES, (
        f"interactive.html = {size / 1024 / 1024:.2f} MB exceeds the "
        f"{BUDGET_BYTES / 1024 / 1024:.0f} MB ceiling at "
        f"{N_VARS} vars × {N_SAMPLES} samples (cap {MAX_EMBEDDED})"
    )


def test_figures_are_decimated_to_the_cap(tmp_path):
    """The cap must reach the figures, or the ceiling is doing the
    decimation's job."""
    from test_report_suite import _page_data

    html, _ = _render_real_page(tmp_path, 2, N_SAMPLES, MAX_EMBEDDED)
    page = html.read_text(encoding="utf-8")
    figures = _page_data(page, "PLOT_FIGURES")
    sections = _page_data(page, "PLOT_SERIES")
    # Or the loop below is vacuous on an empty list and the cap goes
    # unchecked.
    assert len(sections) == len(figures) == 2
    for (name,), fig in zip(sections, figures):
        for tr in fig["data"]:
            n = len(tr.get("x") or [])
            assert n <= 2 * MAX_EMBEDDED + 2, (name, tr.get("name"), n)
        actual = next(tr for tr in fig["data"] if tr["name"] == "Actual")
        assert actual["meta"]["full_samples"] == N_SAMPLES
