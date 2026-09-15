"""The page paints figure dicts and nothing else.

Hand-built contexts here, so a figure can be constructed on its own and
asserted trace for trace. The end-to-end path — a page out of
``generate_comparison_plots`` — is ``tests/test_interactive_report.py``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.playwright


def _fixture_context() -> dict:
    """A minimal render context: one variable, one tube leaf, no figures yet.

    Every key the template dereferences is here — the template renders the
    tree, the summary line and the variable pill server-side, so a missing
    key is a silently blank page rather than an error.
    """
    t = np.linspace(0, 3, 50).tolist()
    ref = [1 + np.sin(x) for x in t]
    act = [v + 0.01 for v in ref]
    leaf = {
        "kind": "leaf",
        "path": "/metrics/children/0",
        "metric": "tube",
        "variable": "h",
        "params": {"tube_width_mode": "band", "tube_abs": 0.05},
        "against": "primary",
        "window": {},
        "children": [],
        "passed": True,
        "score": 0.2,
        "label": "h",
        "name": "h",
        "mode_effective": "tube",
        "score_display": "tube 100% inside",
        "criterion": "every sample inside the tube → PASS",
        "details": [["Points inside", "100%"]],
        "error": None,
        "advisory": False,
    }
    tree = {
        "kind": "combinator",
        "combinator": "and",
        "path": "/metrics",
        "passed": True,
        "label": "and[1]",
        "children": [leaf],
    }
    return {
        "model_id": "Fixture.Figures",
        "n_passed": 1,
        "sim_failed": False,
        "is_simulate_only": False,
        "last_run_at": 0,
        "last_run_str": "",
        "warnings": [],
        "key_stats": [],
        "ref_info": [],
        "sim_params": [],
        "statistics_sections": [],
        "diagnostic_summaries": [],
        "artifacts": [],
        "diag_trajectories": [],
        "nobaseline_trajectories": [],
        "overlay_rows": [],
        "tree_view": tree,
        "tree_summary": {
            "n_pass": 1,
            "n_total": 1,
            "root_passed": True,
            "n_advisory_failed": 0,
        },
        "variables_by_name": {
            "h": {
                "name": "h",
                "trajectory": {
                    "index": 1,
                    "name": "h",
                    "act_time": t,
                    "act_values": act,
                    "ref_time": t,
                    "ref_values": ref,
                },
                "overlays": [],
                "leaf_paths": ["/metrics/children/0"],
                "tree": tree,
                "passed": True,
                "n_advisory_failed": 0,
            }
        },
        "decimated": [],
        "figures": {},
        "diag_figures": [],
        "nb_figures": [],
        "run_metadata": None,
    }


def _render_with_context(tmp_path: Path, ctx: dict) -> Path:
    """Render a context to ``tmp_path/interactive.html`` with its assets."""
    from dstf.reporting.report_suite import jinja_env, write_report_assets

    html = jinja_env().get_template("interactive.html").render(**ctx)
    html_path = tmp_path / "interactive.html"
    html_path.write_text(html, encoding="utf-8")
    write_report_assets(tmp_path)
    return html_path


def _context_with_figure():
    from dstf.comparison.algorithms import _compare_tube
    from dstf.comparison.metric_tree import leaf_from_variable, stamp_leaf_path
    from dstf.figures.plotly import variable_figure
    from dstf.figures.view import build_view

    ctx = _fixture_context()
    var = next(iter(ctx["variables_by_name"]))
    traj = ctx["variables_by_name"][var]["trajectory"]
    t = np.asarray(traj["ref_time"], dtype=float)
    ref = np.asarray(traj["ref_values"], dtype=float)
    act_t = np.asarray(traj["act_time"], dtype=float)
    act = np.asarray(traj["act_values"], dtype=float)
    vc = _compare_tube(
        t, ref, act_t, act, {"tube_width_mode": "band", "tube_abs": 0.05}
    )
    vc.name = var
    stamp_leaf_path(leaf_from_variable(vc), "/metrics/children/0")
    fig = variable_figure(
        build_view(
            var,
            act_time=act_t,
            act_values=act,
            ref_time=t,
            ref_values=ref,
            leaves=[("/metrics/children/0", vc)],
        )
    )
    ctx["figures"] = {var: fig}
    return ctx, fig


def test_first_paint_draws_exactly_the_figure_dict(tmp_path, playwright_browser):
    ctx, fig = _context_with_figure()
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    painted = page.evaluate(
        "() => { const el = document.getElementById('plot-0');"
        " return { n: el.data.length, python: !!el._mt_python,"
        " ids: el.data.map(t => (t.meta || {}).id || null) }; }"
    )
    page.close()
    assert painted["python"] is True
    assert painted["n"] == len(fig["data"])
    assert "/metrics/children/0/tube/band" in painted["ids"]


def test_error_overlay_toggles_the_embedded_error_trace(tmp_path, playwright_browser):
    ctx, _ = _context_with_figure()
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    visible = page.evaluate("""async () => {
        setErrorOverlay(0, 'abs');
        await new Promise(r => setTimeout(r, 100));   // Plotly.restyle settles on the next tick
        const el = document.getElementById('plot-0');
        return el.data.filter(t => (t.meta || {}).kind === 'error').map(t => [t.meta.id, t.visible]);
    }""")
    page.close()
    assert dict(visible) == {
        "error/signed": "legendonly",
        "error/abs": True,
        "error/nrmse": "legendonly",
    }


def test_overlay_picker_toggles_a_python_painted_overlay(tmp_path, playwright_browser):
    """The picker finds an overlay trace by name; the Python figure must
    name it the way ``overlayTraceName`` does, or ticking a soft check
    toggles nothing."""
    from dstf.figures.plotly import variable_figure
    from dstf.figures.view import build_view

    ctx, _ = _context_with_figure()
    var = next(iter(ctx["variables_by_name"]))
    traj = ctx["variables_by_name"][var]["trajectory"]
    overlay = {
        "name": "alt",
        "role": "soft_check",
        "kind": None,
        "time": list(traj["ref_time"]),
        "values": [v * 1.01 for v in traj["ref_values"]],
    }
    ctx["variables_by_name"][var]["overlays"] = [overlay]
    ctx["figures"][var] = variable_figure(
        build_view(
            var,
            act_time=traj["act_time"],
            act_values=traj["act_values"],
            ref_time=traj["ref_time"],
            ref_values=traj["ref_values"],
            overlays=[overlay],
        )
    )
    ctx["nobaseline_trajectories"] = [
        {
            "name": "fresh",
            "time": list(traj["act_time"]),
            "values": list(traj["act_values"]),
            "overlays": [overlay],
        }
    ]
    ctx["nb_figures"] = [
        variable_figure(
            build_view(
                "fresh",
                act_time=traj["act_time"],
                act_values=traj["act_values"],
                overlays=[overlay],
            )
        )
    ]
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    assert page.evaluate("!!document.getElementById('plot-0')._mt_python")
    assert page.evaluate("!!document.getElementById('nb-plot-0')._mt_python")
    seen = {}
    for plot_id in ("plot-0", "nb-plot-0"):
        js = (
            f"() => document.getElementById('{plot_id}')._fullData"
            ".filter(t => (t.meta || {}).kind === 'overlay').map(t => t.visible)"
        )
        before = page.evaluate(js)
        page.locator(f'[data-plot-id="{plot_id}"] input.overlay-toggle').first.check()
        page.wait_for_timeout(100)
        seen[plot_id] = (before, page.evaluate(js))
    page.close()
    assert seen == {
        "plot-0": (["legendonly"], [True]),
        "nb-plot-0": (["legendonly"], [True]),
    }


def test_error_axis_is_hidden_until_an_error_series_is_shown(
    tmp_path, playwright_browser
):
    """A laid-out axis with only legend-only traces gets a placeholder range
    and draws its zero line as a stray black rule across the plot."""
    ctx, _ = _context_with_figure()
    html_path = _render_with_context(tmp_path, ctx)
    page = playwright_browser.new_page()
    page.goto(html_path.as_uri())
    probe = (
        "() => { const el = document.getElementById('plot-0');"
        " const y3 = el._fullLayout.yaxis3;"
        " return { visible: y3 ? y3.visible : null,"
        " y4: el._fullLayout.yaxis4.visible,"
        " zerolines: Array.from(el.querySelectorAll('.zl')).map(n => n.getAttribute('class')) }; }"
    )
    before = page.evaluate(probe)
    page.evaluate("setErrorOverlay(0, 'abs')")
    page.wait_for_timeout(100)
    after = page.evaluate(probe)
    page.evaluate("setErrorOverlay(0, 'none')")
    page.wait_for_timeout(100)
    again = page.evaluate(probe)
    page.close()
    assert before["visible"] is False
    assert not any("y3zl" in c for c in before["zerolines"])
    assert after["visible"] is True and after["y4"] is False
    assert again["visible"] is False and again["y4"] is False
