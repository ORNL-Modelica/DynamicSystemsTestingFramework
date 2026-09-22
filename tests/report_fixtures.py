"""Hand-built per-test report contexts, rendered through the production
Jinja environment. Shared by the browser tests (test_interactive_figures)
and the token tests (test_theme). Kept out of the browser-gated module so a
run without Playwright — CI installs no browser — still renders a page for
the token tests instead of skipping them."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from dstf.reporting.report_suite import plot_page_keys


def _fixture_context(label: str | None = None) -> dict:
    """A minimal render context: one variable, one tube leaf, an empty figure.

    Every key the template dereferences is here — the template renders the
    tree, the summary line and the variable pill server-side, so a missing
    key is a silently blank page rather than an error.

    ``label`` gives the variable an EXPRESSION to read as — stamped where
    ``_build_template_context`` stamps it, in ``variable_texts`` and on the
    trajectory — so a page can be rendered for a variable whose label is
    not its name without a whole comparison behind it.
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
    ctx = {
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
                "label": label or "h",
                "text": label or "h",
                "trajectory": {
                    "index": 1,
                    "name": "h",
                    "label": label or "h",
                    "text": label or "h",
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
        "variable_texts": {"h": {"label": label or "h", "text": label or "h"}},
        "decimated": [],
        "diag_figures": [],
        "nb_figures": [],
        "run_metadata": None,
    }
    # The page's plot keys come from the ONE producer, never hand-rolled:
    # the default layout is one plot per key of ``variables_by_name``.
    ctx.update(plot_page_keys(ctx, None, {}, None, None))
    return ctx


def _render_with_context(tmp_path: Path, ctx: dict) -> Path:
    """Render a context to ``tmp_path/interactive.html`` with its assets."""
    from dstf.reporting.report_suite import jinja_env, write_report_assets

    html = jinja_env().get_template("interactive.html").render(**ctx)
    html_path = tmp_path / "interactive.html"
    html_path.write_text(html, encoding="utf-8")
    write_report_assets(tmp_path)
    return html_path


def _context_with_figure(label: str | None = None):
    from dstf.comparison.algorithms import _compare_tube
    from dstf.comparison.metric_tree import leaf_from_variable, stamp_leaf_path
    from dstf.figures.plotly import variable_figure
    from dstf.figures.view import build_view

    ctx = _fixture_context(label)
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
            label=label or var,
            act_time=act_t,
            act_values=act,
            ref_time=t,
            ref_values=ref,
            leaves=[("/metrics/children/0", vc)],
        )
    )
    # The section and the series `_fixture_context` built already name this
    # one variable; only the figure it paints is replaced.
    ctx["plot_figures"] = [fig]
    return ctx, fig
