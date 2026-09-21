"""Tests for reporting/report_suite.py — the per-test report pipeline.

Strict-JSON artifacts, the run-provenance banner, the embedded figure dicts
and the page-versus-sidecar split: the sidecar carries full-resolution
trajectories, the page carries figures and no arrays at all.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from dstf.comparison.types import VariableComparison
from dstf.reporting.report_suite import _json_sanitize, generate_comparison_plots


def _stamped_root_leaf(vc):
    """A single-leaf metric tree addressed the way the evaluator addresses one.

    ``evaluate_spec`` stamps a one-leaf spec's result with the root path
    ``/metrics`` — the same path ``spec_to_view`` gives the root node — and
    the report looks leaves up by that path.
    """
    from dstf.comparison.metric_tree import leaf_from_variable, stamp_leaf_path

    leaf = leaf_from_variable(vc)
    stamp_leaf_path(leaf, "/metrics")
    return leaf


# =========================================================================
# Folded in from tests/test_review_fixes_reporter.py — 2026-07-06 review,
# reporter batch (CODE_REVIEW_2026-07-06.md).
#
# * Finding 68 — ``comparison_data.json`` and the embedded interactive.html
#   context are strict JSON (non-finite floats sanitized to null).
# * Finding 76h (support) — the template emits ``PLOT_SERIES`` as an
#   explicit JSON array so integer-like variable names can't desync plot IDs.
# =========================================================================


# ---------------------------------------------------------------------------
# Finding 68 — strict JSON artifacts
# ---------------------------------------------------------------------------


def _nonfinite_comparison(name: str = "h") -> VariableComparison:
    """Shape produced by the _no_data_failure / missing-baseline sentinels."""
    return VariableComparison(
        index=0,
        name=name,
        passed=False,
        nrmse=float("inf"),
        rmse=float("inf"),
        signal_range=0.0,
        max_abs_error=float("inf"),
        max_abs_error_time=0.0,
        reference_final=float("nan"),
        actual_final=float("nan"),
        tolerance_used=1e-4,
    )


def _raise_on_constant(token: str):
    raise AssertionError(f"non-strict JSON constant emitted: {token}")


class TestStrictJsonArtifacts:
    def test_json_sanitize_replaces_nonfinite(self):
        data = {
            "a": float("inf"),
            "b": [1.0, float("nan"), {"c": float("-inf")}],
            "d": "keep",
            "e": 3,
        }
        out = _json_sanitize(data)
        assert out == {"a": None, "b": [1.0, None, {"c": None}], "d": "keep", "e": 3}
        # Input not mutated.
        assert data["a"] == float("inf")

    def test_comparison_data_json_is_strict(self, tmp_path):
        plot_dir = tmp_path / "report"
        generate_comparison_plots(
            model_id="Fixture.Strict",
            ref_data=None,
            result=None,
            comparisons=[_nonfinite_comparison()],
            plot_dir=plot_dir,
        )
        text = (plot_dir / "comparison_data.json").read_text(encoding="utf-8")
        # Strict parse: any Infinity/NaN token trips the parse_constant hook.
        data = json.loads(text, parse_constant=_raise_on_constant)
        assert data["summary"]["worst_nrmse"] is None  # was inf

    def test_embedded_html_context_is_strict(self, tmp_path):
        plot_dir = tmp_path / "report"
        generate_comparison_plots(
            model_id="Fixture.Strict",
            ref_data=None,
            result=None,
            comparisons=[_nonfinite_comparison()],
            plot_dir=plot_dir,
        )
        html = (plot_dir / "interactive.html").read_text(encoding="utf-8")
        # The only inline script is the window.MT_REPORT data block; no
        # bare Infinity / NaN literal may appear anywhere in it.
        assert re.search(r"\bInfinity\b", html) is None
        assert re.search(r"\bNaN\b", html) is None


class TestRunMetadataBanner:
    """Run-provenance banner in the per-test interactive.html."""

    def test_banner_rendered_when_metadata_present(self, tmp_path):
        plot_dir = tmp_path / "report"
        generate_comparison_plots(
            model_id="Fixture.Prov",
            ref_data=None,
            result=None,
            comparisons=[_nonfinite_comparison()],
            plot_dir=plot_dir,
            run_metadata={
                "label": "Dymola 2026x",
                "backend": "Dymola",
                "os": "linux",
                "tool_version": "Dymola 2026x Refresh 1",
                "dstf_version": "0.1.0",
                "generated_str": "2026-07-07 14:30",
            },
        )
        html = (plot_dir / "interactive.html").read_text(encoding="utf-8")
        assert 'class="run-meta"' in html
        assert "Dymola 2026x" in html
        assert "Dymola 2026x Refresh 1" in html  # reported version distinct
        assert "linux" in html

    def test_no_banner_when_metadata_absent(self, tmp_path):
        plot_dir = tmp_path / "report"
        generate_comparison_plots(
            model_id="Fixture.NoProv",
            ref_data=None,
            result=None,
            comparisons=[_nonfinite_comparison()],
            plot_dir=plot_dir,
        )
        html = (plot_dir / "interactive.html").read_text(encoding="utf-8")
        assert 'class="run-meta"' not in html


class TestViewAndLookupDrift:
    """The tree view walks the spec; the lookup holds what the evaluator
    stamped. A leaf in one and not the other renders with no pill and no
    controls, so the report says so instead of producing a stripped page."""

    def _model(self, spec):
        from types import SimpleNamespace

        return SimpleNamespace(
            model_id="Lib.Drift", metric_tree_spec=spec, variable_overrides=None
        )

    def test_a_leaf_the_evaluator_did_not_stamp_is_named(self, caplog):
        import logging

        from dstf.comparison.metric_tree import implicit_and_tree
        from dstf.comparison.tree_spec import parse_metric_tree
        from dstf.reporting.context_builder import _build_tree_view_and_variables

        # Two spec leaves; the tree carries a stamped result for one of them.
        spec = parse_metric_tree(
            {
                "combinator": "and",
                "children": [
                    {"metric": "nrmse", "variable": "x"},
                    {"metric": "nrmse", "variable": "y"},
                ],
            }
        )
        tree = implicit_and_tree([_nonfinite_comparison("x")])
        with caplog.at_level(logging.WARNING, logger="dstf.reporting.context_builder"):
            _build_tree_view_and_variables(
                [_nonfinite_comparison("x")], self._model(spec), tree, []
            )
        messages = [r.getMessage() for r in caplog.records]
        assert any("Lib.Drift" in m and "/metrics/children/1" in m for m in messages), (
            messages
        )

    def test_both_evaluation_routes_stay_quiet(self, caplog):
        import logging

        import numpy as np

        from dstf.backends.types import TestResult, VariableResult
        from dstf.comparison.comparator import compare_test
        from dstf.discovery.test_registry import TestModel
        from dstf.reporting.context_builder import _build_tree_view_and_variables

        t = np.linspace(0.0, 1.0, 11)
        ref = {
            "test_id": "ref_0001",
            "time": t.tolist(),
            "variables": [
                {"index": 1, "name": "x", "values": t.tolist()},
                {"index": 2, "name": "y", "values": (t * 2).tolist()},
            ],
        }
        result = TestResult(
            model_id="M",
            success=True,
            variables=[
                VariableResult(index=1, name="x", time=t, values=t),
                VariableResult(index=2, name="y", time=t, values=t * 2),
            ],
        )
        base = dict(
            model_id="M",
            source_file=Path("fixture.mo"),
            source_package="Fixture",
            short_name="M",
            n_vars=2,
        )
        flat = TestModel(
            **base,
            variable_overrides={"x": {"mode": "tube", "tube_width_mode": "band"}},
        )
        from dstf.comparison.tree_spec import parse_metric_tree

        explicit = TestModel(
            **base,
            metric_tree_spec=parse_metric_tree(
                {
                    "combinator": "and",
                    "children": [
                        {"metric": "nrmse", "variable": "x"},
                        {
                            "combinator": "warn",
                            "children": [
                                {"metric": "range", "variable": "y", "max_value": 3.0}
                            ],
                        },
                    ],
                }
            ),
        )
        for model in (flat, explicit):
            comp = compare_test(model, result, ref)
            with caplog.at_level(
                logging.WARNING, logger="dstf.reporting.context_builder"
            ):
                tree_view, _ = _build_tree_view_and_variables(
                    comp.variables, model, comp.metric_tree, []
                )
            assert not [
                r for r in caplog.records if "no evaluated comparison" in r.getMessage()
            ], model

            # And every view leaf really was enriched.
            def leaves(node):
                if node.get("kind") == "leaf":
                    yield node
                for c in node.get("children") or []:
                    yield from leaves(c)

            assert all("score_display" in leaf for leaf in leaves(tree_view)), model


# ---------------------------------------------------------------------------
# Finding 76h (support) — explicit plot order emission
# ---------------------------------------------------------------------------


def test_template_emits_plot_order(tmp_path):
    """Integer-like variable names must keep template order in JS — the
    template emits PLOT_SERIES as a LIST (Object.keys() would sort
    '123' ahead of 'alpha')."""
    plot_dir = tmp_path / "report"
    comparisons = [
        _nonfinite_comparison("zeta"),
        _nonfinite_comparison("123"),
    ]
    comparisons[1].index = 1
    generate_comparison_plots(
        model_id="Fixture.Order",
        ref_data=None,
        result=None,
        comparisons=comparisons,
        plot_dir=plot_dir,
    )
    html = (plot_dir / "interactive.html").read_text(encoding="utf-8")
    assert _page_data(html, "PLOT_SERIES") == [["zeta"], ["123"]]


class TestVendoredPlotly:
    """The report ships Plotly with it, not from a CDN — ONE copy per
    suite, at the reports root, reached as ``../plotly.min.js``.

    An air-gapped lab (the reason this changed) got a page with no plots
    and no error at all: the CDN script tag just failed silently. A copy
    per test dir fixed that at 1.25 MB × N (~414 MB for a 331-test suite),
    which is why the copy is suite-level.
    """

    @staticmethod
    def _render(plot_dir: Path, model_id: str = "Fixture.Vendor") -> None:
        generate_comparison_plots(
            model_id=model_id,
            ref_data=None,
            result=None,
            comparisons=[_nonfinite_comparison()],
            plot_dir=plot_dir,
        )

    def test_reports_root_gets_one_plotly(self, tmp_path):
        self._render(tmp_path / "reports" / "ref_0001")
        vendored = tmp_path / "reports" / "plotly.min.js"
        assert vendored.exists()
        assert not (tmp_path / "reports" / "ref_0001" / "plotly.min.js").exists()
        assert vendored.stat().st_size > 1_000_000, "truncated plotly copy"
        assert (
            "plotly.js (cartesian - minified) v2.35.0"
            in vendored.read_text(encoding="utf-8")[:400]
        )

    def test_a_multi_test_suite_stores_exactly_one_copy(self, tmp_path):
        """The footprint claim, asserted: N per-test reports under one work
        dir leave exactly ONE plotly.min.js — not N."""
        for i in range(1, 6):
            self._render(tmp_path / "reports" / f"ref_{i:04d}", f"Fixture.Vendor{i}")
        copies = sorted(tmp_path.rglob("plotly.min.js"))
        assert len(copies) == 1, f"expected one stored copy, found {copies}"
        assert copies[0] == tmp_path / "reports" / "plotly.min.js"
        # And every page resolves to it.
        for i in range(1, 6):
            page = tmp_path / "reports" / f"ref_{i:04d}" / "interactive.html"
            assert '<script src="../plotly.min.js"' in page.read_text(encoding="utf-8")
            assert (page.parent / "../plotly.min.js").resolve() == copies[0]

    def test_repeat_renders_do_not_rewrite_the_copy(self, tmp_path):
        """Idempotent: the second render leaves the existing file alone
        (the report pool calls this once per test, concurrently)."""
        self._render(tmp_path / "reports" / "ref_0001")
        vendored = tmp_path / "reports" / "plotly.min.js"
        stamp = vendored.stat().st_mtime_ns
        self._render(tmp_path / "reports" / "ref_0002", "Fixture.Vendor2")
        assert vendored.stat().st_mtime_ns == stamp
        assert not list(vendored.parent.glob("*.tmp")), "temp copy left behind"

    def test_html_references_it_relatively_and_names_no_cdn(self, tmp_path):
        plot_dir = tmp_path / "reports" / "ref_0001"
        self._render(plot_dir)
        html = (plot_dir / "interactive.html").read_text(encoding="utf-8")
        assert '<script src="../plotly.min.js"' in html
        assert "cdnjs" not in html and "cdn.plot.ly" not in html

    def test_dashboard_needs_no_plotly(self):
        """The dashboard draws no charts, so `--report-format html --output`
        writing it elsewhere loses nothing: only its per-test row links
        (and the Plotly those pages load) are work-dir relative."""
        from dstf.reporting.report_suite import _TEMPLATE_DIR

        tpl = (_TEMPLATE_DIR / "dashboard.html").read_text(encoding="utf-8")
        assert "plotly" not in tpl.lower()

    def test_asset_ships_inside_the_package(self):
        """Packaging guard: the file must live under the installed package
        (hatchling ships everything under src/dstf), not somewhere only the
        source checkout has.
        """
        import dstf.reporting.report_suite as rs

        pkg_root = Path(rs.__file__).resolve().parents[2]
        for name in rs.VENDOR_ASSETS:
            asset = rs._VENDOR_DIR / name
            assert asset.exists()
            assert asset.resolve().is_relative_to(pkg_root / "dstf")


class TestWorstFiniteScore:
    """A sentinel leaf carries inf/nan; a plain max() let it swallow every
    real score on the test, and the dashboard then read "—" for a test
    whose other leaves scored fine."""

    @staticmethod
    def _scored(nrmse: float, name: str = "h"):
        vc = _nonfinite_comparison(name)
        vc.nrmse = nrmse
        return vc

    def test_sentinel_does_not_swallow_the_finite_leaves(self):
        from dstf.reporting.context_builder import worst_finite_score

        worst, n_unscored = worst_finite_score(
            [self._scored(1e-5), _nonfinite_comparison("bad"), self._scored(3e-4)]
        )
        assert worst == 3e-4
        assert n_unscored == 1

    def test_all_unscored_reports_the_count(self):
        from dstf.reporting.context_builder import worst_finite_score

        worst, n_unscored = worst_finite_score(
            [_nonfinite_comparison("a"), _nonfinite_comparison("b")]
        )
        assert worst is None
        assert n_unscored == 2

    def test_nan_counts_as_unscored(self):
        from dstf.reporting.context_builder import worst_finite_score

        worst, n_unscored = worst_finite_score(
            [self._scored(float("nan")), self._scored(2.0)]
        )
        assert worst == 2.0
        assert n_unscored == 1

    def test_summary_sidecar_carries_both(self, tmp_path):
        plot_dir = tmp_path / "report"
        generate_comparison_plots(
            model_id="Fixture.Worst",
            ref_data=None,
            result=None,
            comparisons=[self._scored(1e-5), _nonfinite_comparison("bad")],
            plot_dir=plot_dir,
        )
        summary = json.loads(
            (plot_dir / "comparison_data.json").read_text(encoding="utf-8")
        )["summary"]
        assert summary["worst_nrmse"] == 1e-5
        assert summary["n_unscored"] == 1

    def test_key_stat_says_n_a_when_nothing_scored(self):
        from dstf.reporting.context_builder import _build_key_stats

        stats = _build_key_stats([_nonfinite_comparison("bad")], {}, {})
        worst = next(s for s in stats if s["label"] == "Worst Score")
        assert worst["current"] == "n/a (1 unscored leaf)"


class TestEmbeddedFigures:
    def _render(self, tmp_path):
        import json
        from pathlib import Path

        import numpy as np

        from dstf.backends.types import TestResult, VariableResult
        from dstf.comparison.comparator import compare_test
        from dstf.discovery.test_registry import TestModel
        from dstf.reporting.report_suite import generate_comparison_plots

        t = np.linspace(0.0, 1.0, 3001)
        ref = {
            "test_id": "ref_0001",
            "time": t.tolist(),
            "variables": [{"index": 1, "name": "x", "values": np.sin(t).tolist()}],
        }
        result = TestResult(
            model_id="M",
            success=True,
            variables=[
                VariableResult(index=1, name="x", time=t, values=np.sin(t) + 0.001)
            ],
        )
        test = TestModel(
            model_id="M",
            source_file=Path("fixture.mo"),
            source_package="Fixture",
            short_name="M",
            n_vars=1,
            variable_overrides={
                "x": {"mode": "tube", "tube_width_mode": "band", "tube_abs": 0.01}
            },
        )
        comp = compare_test(test, result, ref)
        plot_dir = tmp_path / "plots"
        html = generate_comparison_plots(
            "M",
            ref,
            result,
            comp.variables,
            plot_dir,
            test_model=test,
            metric_tree=comp.metric_tree,
            max_embedded_samples=500,
        )
        page = html.read_text(encoding="utf-8")
        sidecar = json.loads(
            (plot_dir / "comparison_data.json").read_text(encoding="utf-8")
        )
        return page, sidecar

    def test_page_embeds_a_decimated_figure_per_variable_and_the_sidecar_does_not(
        self, tmp_path
    ):
        page, sidecar = self._render(tmp_path)
        assert "PLOT_FIGURES:" in page and '"/metrics/children/0/tube/band"' in page
        assert "figures" not in sidecar and "plot_figures" not in sidecar
        assert "diag_figures" not in sidecar
        assert len(sidecar["trajectories"][0]["act_time"]) == 3001
        (fig,) = _page_data(page, "PLOT_FIGURES")
        assert any(
            (tr.get("meta") or {}).get("full_samples") == 3001 for tr in fig["data"]
        )

    def test_the_page_carries_no_trajectory_arrays_but_the_sidecar_does(self, tmp_path):
        """The page draws figures. Anything else the arrays could do there
        was deleted with the browser scorers, so the page's copy of the
        context drops them — while ``comparison_data.json``, the documented
        downstream artifact, keeps its full-resolution shape."""
        page, sidecar = self._render(tmp_path)
        marshalled = page[page.index("window.MT_REPORT") :]
        for key in (
            "TREE_VIEW",
            "VARIABLES_BY_NAME",
            "DIAG_TRAJECTORIES",
            "NB_TRAJECTORIES",
            "MODE_SCHEMAS",
            "OVERLAY_ROWS",
        ):
            assert key not in marshalled, key
        assert "act_time" not in page and "ref_values" not in page
        assert len(sidecar["trajectories"][0]["act_time"]) == 3001
        assert len(sidecar["variables_by_name"]["x"]["trajectory"]["act_time"]) == 3001

    def test_the_reports_figures_are_never_editable(self, tmp_path, monkeypatch):
        """The report draws through ``_build_figures``; a handle Marker there
        must get no ring and the figure no ``edits`` (the tool's alone)."""
        from pathlib import Path

        import numpy as np

        from dstf.backends.types import TestResult, VariableResult
        from dstf.comparison.comparator import compare_test
        from dstf.discovery.test_registry import TestModel
        from dstf.reporting import report_suite

        t = np.linspace(0.0, 1.0, 11)
        ref = {
            "test_id": "ref_0001",
            "time": t.tolist(),
            "variables": [{"index": 1, "name": "x", "values": t.tolist()}],
        }
        result = TestResult(
            model_id="M",
            success=True,
            variables=[VariableResult(index=1, name="x", time=t, values=t + 0.001)],
            # A diagnostic, so the page builds a diagnostic figure too.
            diagnostics=[VariableResult(index=0, name="CPUtime", time=t, values=t)],
        )
        test = TestModel(
            model_id="M",
            source_file=Path("fixture.mo"),
            source_package="Fixture",
            short_name="M",
            n_vars=1,
            variable_overrides={"x": {"mode": "points", "tolerance": 0.01}},
        )
        comp = compare_test(test, result, ref)

        built: list[dict] = []
        real = report_suite._build_figures

        def spy(*args, **kwargs):
            out = real(*args, **kwargs)
            built.append(out)
            return out

        # The per-variable figures moved to ``plot_page_keys``; the
        # diagnostic and no-baseline ones stayed in ``_build_figures``, so
        # both producers are watched.
        page_built: list[dict] = []
        real_keys = report_suite.plot_page_keys

        def keys_spy(*args, **kwargs):
            out = real_keys(*args, **kwargs)
            page_built.append(out)
            return out

        monkeypatch.setattr(report_suite, "plot_page_keys", keys_spy)
        monkeypatch.setattr(report_suite, "_build_figures", spy)
        report_suite.generate_comparison_plots(
            "M",
            ref,
            result,
            comp.variables,
            tmp_path / "plots",
            test_model=test,
            metric_tree=comp.metric_tree,
        )
        # No baseline, no comparisons: the page builds no-baseline figures.
        report_suite.generate_comparison_plots(
            "M", None, result, [], tmp_path / "nb_plots", test_model=test
        )
        assert len(built) == 2 and len(page_built) == 2
        figures, diag, nb = (
            page_built[0]["plot_figures"],
            built[0]["diag_figures"],
            built[1]["nb_figures"],
        )
        # Every list the loop checks is non-empty, and the per-variable
        # figure really carries a handle — else the loop is vacuous.
        assert page_built[0]["plot_series"] == [["x"]] and diag and nb
        assert any(
            (tr.get("meta") or {}).get("kind") == "handle" for tr in figures[0]["data"]
        )
        for fig in [*figures, *diag, *nb]:
            assert "edits" not in fig["config"]
            assert not any(
                s.get("type") == "circle" for s in fig["layout"].get("shapes") or []
            )

    def test_the_banner_names_every_decimated_trace(self, tmp_path):
        from dstf.reporting.report_suite import _decimated_traces

        page, _ = self._render(tmp_path)
        # 3001 samples, capped at 500: every curve on x was thinned.
        assert 'id="decimation-banner"' in page
        assert "Actual 3001&rarr;500" in page or "Actual 3001→500" in page
        # The tube band is a CLOSED POLYGON — 500 samples ride as 1000
        # points — so the banner must count its samples, not its points.
        assert "Tube 3001&rarr;500" in page or "Tube 3001→500" in page
        assert "Tube 3001&rarr;1000" not in page and "Tube 3001→1000" not in page

        # The list the banner renders, straight from the figures: the
        # embedded side is ``meta.samples`` where the figure layer stamped
        # one, and the x length only where it did not.
        ctx = {
            "plot_sections": [{"index": 0, "title": "x"}],
            "plot_figures": [
                {
                    "data": [
                        {
                            "name": "Actual",
                            "x": [0, 1],
                            "meta": {"samples": 2, "full_samples": 9},
                        },
                        {
                            "name": "Tube",
                            "x": [0, 1, 1, 0],
                            "meta": {"samples": 2, "full_samples": 9},
                        },
                        {
                            "name": "Reference",
                            "x": [0, 1],
                            "meta": {"samples": 2, "full_samples": 2},
                        },
                        {"name": "Unstamped", "x": [0, 1], "meta": {"full_samples": 9}},
                    ]
                }
            ],
        }
        assert _decimated_traces(ctx) == [
            {"variable": "x", "trace": "Actual", "full": 9, "embedded": 2},
            {"variable": "x", "trace": "Tube", "full": 9, "embedded": 2},
            {"variable": "x", "trace": "Unstamped", "full": 9, "embedded": 2},
        ]

    def test_a_multi_series_traces_name_comes_from_its_meta_id(self):
        """A family's LEAD member carries the family's legend label, so
        naming a thinned trace by ``name`` printed the slice text where the
        member belonged (and ``other other`` for a singleton). On a plot of
        several series the name comes from ``meta.id``; the banner's
        ``variable`` stays the plot's title."""
        from dstf.reporting.report_suite import _decimated_traces

        meta = {"samples": 2, "full_samples": 9}
        ctx = {
            "plot_sections": [
                {
                    "index": 0,
                    "title": "pipe.T[1:2], other",
                    "series": ["pipe.T[1]", "pipe.T[2]", "other"],
                }
            ],
            "plot_figures": [
                {
                    "data": [
                        {
                            "name": "pipe.T[1:2]",
                            "x": [0, 1],
                            "meta": {"id": "actual/pipe.T[1]", **meta},
                        },
                        {
                            "name": "pipe.T[1] (reference)",
                            "x": [0, 1],
                            "meta": {"id": "reference/pipe.T[1]", **meta},
                        },
                        {
                            "name": "other",
                            "x": [0, 1],
                            "meta": {"id": "actual/other", **meta},
                        },
                        {
                            "name": "Tube",
                            "x": [0, 1],
                            "meta": {"id": "tube/band", **meta},
                        },
                    ]
                }
            ],
        }
        rows = _decimated_traces(ctx)
        assert [r["trace"] for r in rows] == [
            "pipe.T[1]",
            "pipe.T[1] (reference)",
            "other",
            "Tube",
        ]
        assert {r["variable"] for r in rows} == {"pipe.T[1:2], other"}

    def _render_two_leaves(self, tmp_path, sentinel_first: bool):
        """One misconfigured tube leaf + one healthy nrmse leaf, both on x.

        The sentinel carries ``index=0`` and no samples; whichever order
        the spec lists the leaves in, the variable's figure must be built
        from the trajectory that HAS data.
        """
        import json
        from pathlib import Path

        import numpy as np

        from dstf.backends.types import TestResult, VariableResult
        from dstf.comparison.comparator import compare_test
        from dstf.comparison.tree_spec import parse_metric_tree
        from dstf.discovery.test_registry import TestModel
        from dstf.reporting.report_suite import generate_comparison_plots

        t = np.linspace(0.0, 1.0, 201)
        ref = {
            "test_id": "ref_0001",
            "time": t.tolist(),
            "variables": [{"index": 1, "name": "x", "values": np.sin(t).tolist()}],
        }
        result = TestResult(
            model_id="M",
            success=True,
            variables=[
                VariableResult(index=1, name="x", time=t, values=np.sin(t) + 0.001)
            ],
        )
        bad = {
            "metric": "tube",
            "variable": "x",
            "tube_width_mode": "nonsense",
            "tube_rel": 0.1,
        }
        good = {"metric": "nrmse", "variable": "x", "tolerance": 0.5}
        children = [bad, good] if sentinel_first else [good, bad]
        spec = parse_metric_tree({"combinator": "and", "children": children})
        test = TestModel(
            model_id="M",
            source_file=Path("fixture.mo"),
            source_package="Fixture",
            short_name="M",
            n_vars=1,
            metric_tree_spec=spec,
        )
        comp = compare_test(test, result, ref)
        plot_dir = tmp_path / ("first" if sentinel_first else "second")
        html = generate_comparison_plots(
            "M",
            ref,
            result,
            comp.variables,
            plot_dir,
            test_model=test,
            metric_tree=comp.metric_tree,
            max_embedded_samples=500,
        )
        page = html.read_text(encoding="utf-8")
        figures = _page_data(page, "PLOT_FIGURES")
        sidecar = json.loads(
            (plot_dir / "comparison_data.json").read_text(encoding="utf-8")
        )
        return page, figures, sidecar

    @pytest.mark.parametrize("sentinel_first", [True, False])
    def test_a_sentinel_leaf_does_not_blank_its_variables_plot(
        self, tmp_path, sentinel_first
    ):
        page, figures, _sidecar = self._render_two_leaves(tmp_path, sentinel_first)
        # One plot, one series: x's. Pinned, so an indexed read cannot
        # silently start asserting on some other variable's figure.
        assert _page_data(page, "PLOT_SERIES") == [["x"]]
        traces = figures[0]["data"]
        names = [tr.get("name") for tr in traces]
        assert "Actual" in names and "Reference" in names
        # Actual/Reference carry the real samples, not the sentinel's empty
        # trajectory.
        actual = next(tr for tr in traces if tr.get("name") == "Actual")
        assert len(actual["x"]) == 201
        # The error traces the healthy leaf's geometry rides on are there;
        # the tube leaf errored, so there is no band.
        ids = [(tr.get("meta") or {}).get("id", "") for tr in traces]
        assert {"error/signed", "error/abs", "error/nrmse"} <= set(ids)
        assert not any("tube/band" in i for i in ids)
        assert not any(
            "tube/band" in (sh.get("name") or "")
            for sh in figures[0]["layout"].get("shapes", [])
        )

    @pytest.mark.parametrize("sentinel_first", [True, False])
    def test_the_sidecar_carries_one_trajectory_entry_per_variable(
        self, tmp_path, sentinel_first
    ):
        """Two leaves on ``x``, one of them a sentinel, are still ONE
        variable. Paired by index, the sentinel's ``index=0`` produced a
        second, empty entry named ``x`` — listed first when the sentinel
        came first, so the sidecar's documented downstream reader met a
        blank trajectory under a name that has samples."""
        _page, _figures, sidecar = self._render_two_leaves(tmp_path, sentinel_first)
        named = [t for t in sidecar["trajectories"] if t["name"] == "x"]
        assert len(named) == 1
        # 201 is the fixture's full sample count: the surviving entry is the
        # one paired with the actual result, never the sentinel's empty one.
        assert len(named[0]["act_time"]) == 201
        assert len(named[0]["ref_values"]) == 201
        assert named[0]["index"] == 1

    def test_a_lone_sentinel_leaf_still_gets_its_variables_plot(self, tmp_path):
        """When EVERY leaf on ``x`` is a sentinel there is no ``index > 0``
        comparison to pair with, so pairing by index found nothing and the
        variable — present in the result, with a baseline — rendered an
        empty figure."""
        import json
        from pathlib import Path

        import numpy as np

        from dstf.backends.types import TestResult, VariableResult
        from dstf.comparison.comparator import compare_test
        from dstf.comparison.tree_spec import parse_metric_tree
        from dstf.discovery.test_registry import TestModel
        from dstf.reporting.report_suite import generate_comparison_plots

        t = np.linspace(0.0, 1.0, 201)
        ref = {
            "test_id": "ref_0001",
            "time": t.tolist(),
            "variables": [{"index": 1, "name": "x", "values": np.sin(t).tolist()}],
        }
        result = TestResult(
            model_id="M",
            success=True,
            variables=[
                VariableResult(index=1, name="x", time=t, values=np.sin(t) + 0.001)
            ],
        )
        spec = parse_metric_tree(
            {
                "metric": "tube",
                "variable": "x",
                "tube_width_mode": "nonsense",
                "tube_rel": 0.1,
            }
        )
        test = TestModel(
            model_id="M",
            source_file=Path("fixture.mo"),
            source_package="Fixture",
            short_name="M",
            n_vars=1,
            metric_tree_spec=spec,
        )
        comp = compare_test(test, result, ref)
        plot_dir = tmp_path / "lone"
        html = generate_comparison_plots(
            "M",
            ref,
            result,
            comp.variables,
            plot_dir,
            test_model=test,
            metric_tree=comp.metric_tree,
            max_embedded_samples=500,
        )
        page = html.read_text(encoding="utf-8")
        figures = _page_data(page, "PLOT_FIGURES")
        assert _page_data(page, "PLOT_SERIES") == [["x"]]
        traces = {tr.get("name"): tr for tr in figures[0]["data"]}
        assert "Actual" in traces and "Reference" in traces
        assert len(traces["Actual"]["x"]) == 201
        assert len(traces["Reference"]["x"]) == 201
        sidecar = json.loads(
            (plot_dir / "comparison_data.json").read_text(encoding="utf-8")
        )
        named = [t for t in sidecar["trajectories"] if t["name"] == "x"]
        assert len(named) == 1
        assert len(named[0]["act_time"]) == 201

    @pytest.mark.parametrize("sentinel_first", [True, False])
    def test_the_sentinels_reason_reaches_the_rendered_tree(
        self, tmp_path, sentinel_first
    ):
        page, _figures, _sidecar = self._render_two_leaves(tmp_path, sentinel_first)
        path = "/metrics/children/0" if sentinel_first else "/metrics/children/1"
        node = next(
            el
            for el in _parse(page).by_id("nodes-full").walk()
            if el.attrs.get("data-path") == path
        )
        # The leaf is labelled with its variable; the reason is beside it.
        label = next(iter(node.by_class("node-label"))).text()
        assert "tube" in label and "x" in label
        assert next(iter(node.by_class("node-score"))).text() == "not scored"
        criterion = next(iter(node.by_class("node-criterion"))).text()
        assert "tube leaf not scored" in criterion
        assert "misconfigured" in criterion


# ---------------------------------------------------------------------------
# The metric tree, server-rendered
# ---------------------------------------------------------------------------


class _Element:
    """One element of the parsed page. Children are elements or text."""

    def __init__(self, tag: str, attrs: dict):
        self.tag = tag
        self.attrs = attrs
        self.children: list = []

    @property
    def classes(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())

    def text(self) -> str:
        out = []
        for child in self.children:
            out.append(child if isinstance(child, str) else child.text())
        return "".join(out)

    def walk(self):
        for child in self.children:
            if isinstance(child, _Element):
                yield child
                yield from child.walk()

    def by_class(self, name: str) -> list:
        return [el for el in self.walk() if name in el.classes]

    def by_id(self, el_id: str):
        return next((el for el in self.walk() if el.attrs.get("id") == el_id), None)


def _parse(html: str) -> _Element:
    """Parse the served page into a tiny element tree.

    Enough of a DOM to ask what the SERVER rendered — the browser tests in
    ``tests/test_interactive_report.py`` ask the same questions of the page
    after the JavaScript has run.
    """
    from html.parser import HTMLParser

    void = {"br", "img", "input", "meta", "link", "hr", "source", "col"}

    class _P(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.root = _Element("#document", {})
            self.stack = [self.root]

        def handle_starttag(self, tag, attrs):
            el = _Element(tag, dict(attrs))
            self.stack[-1].children.append(el)
            if tag not in void:
                self.stack.append(el)

        def handle_startendtag(self, tag, attrs):
            self.stack[-1].children.append(_Element(tag, dict(attrs)))

        def handle_endtag(self, tag):
            for i in range(len(self.stack) - 1, 0, -1):
                if self.stack[i].tag == tag:
                    del self.stack[i:]
                    return

        def handle_data(self, data):
            self.stack[-1].children.append(data)

    parser = _P()
    parser.feed(html)
    return parser.root


class TestServerRenderedTree:
    """The tree, the summary line and the variable pills come off the server.

    Non-browser on purpose: nothing under ``templates/`` rebuilds these
    mounts any more, so the served markup IS what the reader sees, and
    parsing it asks the question directly.
    """

    #: ``x`` carries a passing tube leaf and a failing nrmse leaf wrapped in
    #: ``warn`` (advisory); ``y`` carries a range leaf that breaches its max.
    SPEC = {
        "combinator": "and",
        "children": [
            {
                "metric": "tube",
                "variable": "x",
                "tube_width_mode": "band",
                "tube_abs": 0.1,
                "window": {"start": 0.2, "end": 0.8},
            },
            {
                "combinator": "warn",
                "children": [{"metric": "nrmse", "variable": "x", "tolerance": 1e-12}],
            },
            {"metric": "range", "variable": "y", "max_value": 0.5},
        ],
    }
    TUBE = "/metrics/children/0"
    ADVISORY = "/metrics/children/1/children/0"
    RANGE = "/metrics/children/2"

    def _render(self, tmp_path):
        import numpy as np

        from dstf.backends.types import TestResult, VariableResult
        from dstf.comparison.comparator import compare_test
        from dstf.comparison.tree_spec import parse_metric_tree
        from dstf.discovery.test_registry import TestModel

        t = np.linspace(0.0, 1.0, 201)
        ref = {
            "test_id": "ref_0001",
            "time": t.tolist(),
            "variables": [
                {"index": 1, "name": "x", "values": np.sin(t).tolist()},
                {"index": 2, "name": "y", "values": (0.1 * t).tolist()},
            ],
        }
        result = TestResult(
            model_id="M",
            success=True,
            variables=[
                VariableResult(index=1, name="x", time=t, values=np.sin(t) + 0.01),
                VariableResult(index=2, name="y", time=t, values=1.5 * t),
            ],
        )
        test = TestModel(
            model_id="M",
            source_file=Path("fixture.mo"),
            source_package="Fixture",
            short_name="M",
            n_vars=2,
            metric_tree_spec=parse_metric_tree(self.SPEC),
        )
        comp = compare_test(test, result, ref)
        html = generate_comparison_plots(
            "M",
            ref,
            result,
            comp.variables,
            tmp_path / "plots",
            test_model=test,
            metric_tree=comp.metric_tree,
            max_embedded_samples=500,
        )
        return html.read_text(encoding="utf-8"), comp

    @staticmethod
    def _leaves(mount) -> dict:
        return {
            el.attrs["data-path"]: el
            for el in mount.walk()
            if "node-leaf" in el.classes and el.attrs.get("data-path")
        }

    def test_every_leaf_shows_the_cli_verdict_score_and_criterion(self, tmp_path):
        from dstf.comparison.registry import summarize
        from dstf.comparison.tree_eval import leaf_comparisons_by_path

        page, comp = self._render(tmp_path)
        full = _parse(page).by_id("nodes-full")
        leaves = self._leaves(full)
        by_path = leaf_comparisons_by_path(comp.metric_tree)
        assert set(leaves) == {self.TUBE, self.ADVISORY, self.RANGE}

        verdicts = {}

        def walk(node):
            path = node.diagnostics.get("leaf_path")
            if path is not None and not node.children:
                verdicts[path] = bool(node.passed)
            for child in node.children:
                walk(child)

        walk(comp.metric_tree)

        for path, leaf in leaves.items():
            pills = [el for el in leaf.walk() if "node-status" in el.classes]
            assert len(pills) == 1, path
            assert pills[0].text() == ("PASS" if verdicts[path] else "FAIL"), path
            summary = summarize(by_path[path])
            scores = [el for el in leaf.walk() if "node-score" in el.classes]
            assert [el.text() for el in scores] == [summary.display], path
            # The criterion is TEXT on the page, not a string inside the
            # embedded JSON blob.
            assert summary.criterion in leaf.text(), path

        # The fixture is built so the advisory leaf is the failing one.
        assert verdicts[self.ADVISORY] is False
        assert verdicts[self.TUBE] is True

    def test_each_leaf_carries_its_detail_pairs(self, tmp_path):
        from dstf.comparison.registry import summarize
        from dstf.comparison.tree_eval import leaf_comparisons_by_path

        page, comp = self._render(tmp_path)
        leaves = self._leaves(_parse(page).by_id("nodes-full"))
        by_path = leaf_comparisons_by_path(comp.metric_tree)
        for path, leaf in leaves.items():
            details = [el for el in leaf.walk() if "node-details" in el.classes]
            expected = summarize(by_path[path]).details
            assert len(details) == 1, path
            assert details[0].tag == "dl", path
            pairs = [
                (child.tag, child.text())
                for child in details[0].children
                if isinstance(child, _Element)
            ]
            assert pairs == [
                item for key, value in expected for item in (("dt", key), ("dd", value))
            ], path

    def test_the_advisory_chip_rides_beside_the_summary_and_the_variable(
        self, tmp_path
    ):
        page, _comp = self._render(tmp_path)
        doc = _parse(page)
        summary = doc.by_id("summary-text")
        # The headline colour is the ROOT verdict (the range leaf on y fails
        # it), never the leaf tally; the advisory failure rides beside it as
        # a chip rather than colouring anything.
        assert "fail" in summary.classes
        assert "1 / 3 leaves passed" in " ".join(summary.text().split())
        assert [el.text() for el in summary.by_class("advisory-chip")] == ["1 advisory"]

        pills = doc.by_class("var-status")
        assert [(el.attrs["data-vidx"], el.text()) for el in pills] == [
            ("0", "PASS"),  # x: the gating tube leaf passes
            ("1", "FAIL"),  # y: the range leaf breaches its max
        ]
        chips = [
            el for el in doc.by_class("advisory-chip") if el.attrs.get("data-vidx")
        ]
        assert [(el.attrs["data-vidx"], el.text()) for el in chips] == [
            ("0", "1 advisory")
        ]

    def test_a_variable_mount_carries_only_its_own_leaves_and_their_ancestors(
        self, tmp_path
    ):
        page, _comp = self._render(tmp_path)
        doc = _parse(page)
        assert set(self._leaves(doc.by_id("nodes-0"))) == {self.TUBE, self.ADVISORY}
        assert set(self._leaves(doc.by_id("nodes-1"))) == {self.RANGE}
        # x's mount keeps the warn wrapper its advisory leaf hangs from; y's
        # drops it, because no leaf under it scores y.
        kinds = lambda mount: [  # noqa: E731
            el.by_class("node-label")[0].text()
            for el in mount.walk()
            if "node-combinator" in el.classes
        ]
        assert kinds(doc.by_id("nodes-0")) == ["and", "warn"]
        assert kinds(doc.by_id("nodes-1")) == ["and"]

    def test_no_tree_mount_carries_a_form_control(self, tmp_path):
        page, _comp = self._render(tmp_path)
        doc = _parse(page)
        mounts = [
            el for el in doc.walk() if (el.attrs.get("id") or "").startswith("nodes-")
        ]
        assert {el.attrs["id"] for el in mounts} == {"nodes-full", "nodes-0", "nodes-1"}
        for mount in mounts:
            assert [
                el.tag
                for el in mount.walk()
                if el.tag in ("input", "select", "button", "textarea")
            ] == [], mount.attrs["id"]


class TestRestoredViewingChecks:
    """Three checks the deleted browser suites carried, asked of the server.

    The page no longer builds anything, so what the JavaScript used to be
    asked about — the summary's colour, a third-party metric's pill, the
    decimation banner's list — is now in the markup ``generate_comparison_plots``
    writes, and these render through the real path and parse it.
    """

    @staticmethod
    def _series(n=3001):
        import numpy as np

        t = np.linspace(0.0, 1.0, n)
        return t, np.sin(t)

    @staticmethod
    def _inputs():
        """One variable ``x``, 3001 samples — over the 500-sample budget."""
        from dstf.backends.types import TestResult, VariableResult

        t, ref_v = TestRestoredViewingChecks._series()
        ref = {
            "test_id": "ref_0001",
            "time": t.tolist(),
            "variables": [{"index": 1, "name": "x", "values": ref_v.tolist()}],
        }
        result = TestResult(
            model_id="M",
            success=True,
            variables=[VariableResult(index=1, name="x", time=t, values=ref_v + 0.01)],
        )
        return t, ref_v, ref, result

    @staticmethod
    def _model(**kw):
        from dstf.discovery.test_registry import TestModel

        return TestModel(
            model_id="M",
            source_file=Path("fixture.mo"),
            source_package="Fixture",
            short_name="M",
            n_vars=1,
            **kw,
        )

    def _render(self, tmp_path, *, test_model, metric_tree, comparisons, overlays=None):
        _t, _ref_v, ref, result = self._inputs()
        html = generate_comparison_plots(
            "M",
            ref,
            result,
            comparisons,
            tmp_path / "plots",
            test_model=test_model,
            metric_tree=metric_tree,
            max_embedded_samples=500,
            overlays=overlays,
        )
        assert html is not None
        return html.read_text(encoding="utf-8")

    def _compared(self, **kw):
        """The default implicit-nrmse comparison, or one for a given spec."""
        from dstf.comparison.comparator import compare_test

        _t, _ref_v, ref, result = self._inputs()
        test = self._model(**kw)
        return test, compare_test(test, result, ref)

    def test_a_root_with_no_verdict_renders_pending_not_fail(self, tmp_path):
        """No evaluated tree ⇒ no root verdict ⇒ the summary is ``pending``.

        ``_tree_summary``'s ``root_passed`` is tri-state and ``None`` is
        not ``False``: a page rendered without a metric tree must not paint
        the headline red, because nothing said the test failed.
        """
        test, comp = self._compared()
        page = self._render(
            tmp_path,
            test_model=test,
            metric_tree=None,
            comparisons=comp.variables,
        )
        summary = _parse(page).by_id("summary-text")
        assert "pending" in summary.classes
        assert "fail" not in summary.classes and "pass" not in summary.classes
        assert "root verdict pending" in " ".join(summary.text().split())
        # Every leaf pill is the same tri-state em dash, not a FAIL.
        pills = [
            el
            for el in _parse(page).by_id("nodes-full").walk()
            if "node-status" in el.classes
        ]
        assert pills and all(el.text() == "—" for el in pills)

    def test_a_third_party_metric_renders_in_the_server_tree(self, tmp_path):
        """A metric the framework never heard of reaches the page whole.

        Registered through ``register_metric`` only — no edit to any
        reporting module — it must arrive in the rendered tree with its
        verdict pill, the display ``summarize()`` gives it and its
        criterion as page text.
        """
        from dstf.comparison import registry
        from dstf.comparison.registry import register_metric, summarize
        from dstf.comparison.tree_eval import leaf_comparisons_by_path
        from dstf.comparison.tree_spec import parse_metric_tree
        from test_metric_registry import _final_slope_descriptor

        try:
            register_metric(_final_slope_descriptor())
            spec = parse_metric_tree(
                {"metric": "final-slope", "variable": "x", "max_slope": 1e-3}
            )
            test, comp = self._compared(metric_tree_spec=spec)
            page = self._render(
                tmp_path,
                test_model=test,
                metric_tree=comp.metric_tree,
                comparisons=comp.variables,
            )
        finally:
            registry._reset_for_tests()

        leaves = {
            el.attrs["data-path"]: el
            for el in _parse(page).by_id("nodes-full").walk()
            if "node-leaf" in el.classes and el.attrs.get("data-path")
        }
        assert list(leaves) == ["/metrics"]
        leaf = leaves["/metrics"]
        vc = leaf_comparisons_by_path(comp.metric_tree)["/metrics"]
        summary = summarize(vc)
        pills = [el for el in leaf.walk() if "node-status" in el.classes]
        assert [el.text() for el in pills] == ["PASS" if vc.passed else "FAIL"]
        scores = [el for el in leaf.walk() if "node-score" in el.classes]
        assert [el.text() for el in scores] == [summary.display]
        assert summary.criterion in leaf.text()
        assert "final-slope" in leaf.text()

    def test_a_decimated_soft_check_overlay_is_named_in_the_banner(self, tmp_path):
        """An overlay is a curve on the plot, so the budget thins it too —
        and the banner names it by the trace name the legend shows."""
        from dstf.reporting.overlay_loader import Overlay, OverlayVariable

        test, comp = self._compared()
        t, ref_v = self._series()
        overlays = [
            Overlay(
                name="prev",
                role="soft_check",
                variables={
                    "x": OverlayVariable(
                        time=t.tolist(), values=(ref_v + 0.02).tolist()
                    )
                },
            )
        ]
        page = self._render(
            tmp_path,
            test_model=test,
            metric_tree=comp.metric_tree,
            comparisons=comp.variables,
            overlays=overlays,
        )
        assert 'id="decimation-banner"' in page
        banner = _parse(page).by_id("decimation-banner")
        traces = [el.text() for el in banner.by_class("dec-trace")]
        assert "x Overlay: soft_check/prev 3001→500" in traces


# ---------------------------------------------------------------------------
# The page's plot sections (Task 5)
# ---------------------------------------------------------------------------


def _page_data(page: str, key: str):
    """One key of `window.MT_REPORT`, parsed. Keys are written one per line
    as `KEY: <json>,`."""
    import json
    import re

    m = re.search(rf"^\s*{key}:\s*(.*),\s*$", page, re.M)
    assert m, f"window.MT_REPORT must carry {key}"
    return json.loads(m.group(1))


class TestPlotSections:
    def _render(self, tmp_path, **kw):
        """Two tracked variables, x (passes) and y (passes), nrmse leaves."""
        import json
        from pathlib import Path

        import numpy as np

        from dstf.backends.types import TestResult, VariableResult
        from dstf.comparison.comparator import compare_test
        from dstf.discovery.test_registry import TestModel
        from dstf.reporting.report_suite import generate_comparison_plots

        t = np.linspace(0.0, 1.0, 51)
        ref = {
            "test_id": "ref_0001",
            "time": t.tolist(),
            "variables": [
                {"index": 1, "name": "x", "values": np.sin(t).tolist()},
                {"index": 2, "name": "y", "values": np.cos(t).tolist()},
            ],
        }
        result = TestResult(
            model_id="M",
            success=True,
            variables=[
                VariableResult(index=1, name="x", time=t, values=np.sin(t)),
                VariableResult(index=2, name="y", time=t, values=np.cos(t)),
            ],
        )
        test = TestModel(
            model_id="M",
            source_file=Path("fixture.mo"),
            source_package="Fixture",
            short_name="M",
            n_vars=2,
        )
        comp = compare_test(test, result, ref)
        plot_dir = tmp_path / "plots"
        html = generate_comparison_plots(
            "M",
            ref,
            result,
            comp.variables,
            plot_dir,
            test_model=test,
            metric_tree=comp.metric_tree,
            **kw,
        )
        page = html.read_text(encoding="utf-8")
        sidecar = json.loads(
            (plot_dir / "comparison_data.json").read_text(encoding="utf-8")
        )
        return page, sidecar

    def _render_no_baseline(self, tmp_path, *, success=True, resolve=False, **kw):
        """No reference and no comparisons: the page's no-baseline path,
        where ``variables_by_name`` is empty and every tracked series lives
        in ``nobaseline_trajectories``. ``resolve`` takes the layout from
        `plot_inputs`, the way the run does, instead of the caller's
        ``plots=``; ``success=False`` is a simulation that stopped partway
        and left what it had behind."""
        import json
        from pathlib import Path

        import numpy as np

        from dstf.backends.types import TestResult, VariableResult
        from dstf.discovery.test_registry import TestModel
        from dstf.reporting.report_suite import generate_comparison_plots, plot_inputs

        t = np.linspace(0.0, 1.0, 51)
        result = TestResult(
            model_id="M",
            success=success,
            variables=[
                VariableResult(index=1, name="x", time=t, values=np.sin(t)),
                VariableResult(index=2, name="y", time=t, values=np.cos(t)),
            ],
        )
        test = TestModel(
            model_id="M",
            source_file=Path("fixture.mo"),
            source_package="Fixture",
            short_name="M",
            n_vars=2,
            plots=kw.pop("layout", None),
        )
        if resolve:
            kw["plots"], kw["extra_series"] = plot_inputs(test, [], result, None, None)
        plot_dir = tmp_path / "plots"
        html = generate_comparison_plots(
            "M", None, result, [], plot_dir, test_model=test, **kw
        )
        page = html.read_text(encoding="utf-8")
        sidecar = json.loads(
            (plot_dir / "comparison_data.json").read_text(encoding="utf-8")
        )
        return page, sidecar

    def test_with_no_layout_the_page_has_one_section_per_tracked_variable(
        self, tmp_path
    ):
        page, sidecar = self._render(tmp_path)
        assert _page_data(page, "PLOT_SERIES") == [["x"], ["y"]]
        assert len(_page_data(page, "PLOT_FIGURES")) == 2
        assert "VARIABLE_ORDER" not in page and "  FIGURES:" not in page
        assert 'id="plot-0"' in page and 'id="plot-1"' in page
        assert page.count('class="error-overlay-select"') == 2
        assert sidecar["plots"] == [
            {"kind": "time", "series": ["x"], "missing": []},
            {"kind": "time", "series": ["y"], "missing": []},
        ]

    def test_a_two_series_plot_draws_both_and_says_why_it_has_no_overlays(
        self, tmp_path
    ):
        from dstf.discovery.plots import ResolvedPlot

        plots = (ResolvedPlot(index=0, kind="time", series=("x", "y"), missing=()),)
        page, sidecar = self._render(tmp_path, plots=plots)
        (fig,) = _page_data(page, "PLOT_FIGURES")
        acts = [t["meta"]["id"] for t in fig["data"] if t["meta"]["kind"] == "act"]
        assert acts == ["actual/x", "actual/y"]
        assert "Overlays and error traces show on a plot with one series." in page
        assert 'class="error-overlay-select"' not in page
        assert 'id="nodes-0"' in page and 'id="nodes-1"' in page
        assert sidecar["plots"] == [
            {"kind": "time", "series": ["x", "y"], "missing": []}
        ]

    def test_a_plot_only_series_comes_from_extra_series_and_is_named(self, tmp_path):
        import numpy as np

        from dstf.discovery.plots import ResolvedPlot

        t = np.linspace(0, 1, 5)
        plots = (
            ResolvedPlot(index=0, kind="time", series=("x", "z"), missing=("gone",)),
        )
        page, _ = self._render(
            tmp_path, plots=plots, extra_series={"z": (t, t * 0 + 7)}
        )
        (fig,) = _page_data(page, "PLOT_FIGURES")
        z = next(tr for tr in fig["data"] if tr["meta"]["id"] == "actual/z")
        assert z["y"] == [7.0] * 5
        assert "z is plot only: no criterion scores it." in page
        assert "Not in this result: gone" in page

    def test_a_tracked_variable_on_no_plot_still_has_its_tree_at_the_top(
        self, tmp_path
    ):
        from dstf.discovery.plots import ResolvedPlot

        plots = (ResolvedPlot(index=0, kind="time", series=("x",), missing=()),)
        page, _ = self._render(tmp_path, plots=plots)
        assert _page_data(page, "PLOT_SERIES") == [["x"]]
        assert 'id="nodes-full"' in page  # the whole tree, y's leaf included
        assert 'id="nodes-1"' not in page

    def test_the_figures_stay_uneditable(self, tmp_path):
        from dstf.discovery.plots import ResolvedPlot

        plots = (ResolvedPlot(index=0, kind="time", series=("x", "y"), missing=()),)
        page, _ = self._render(tmp_path, plots=plots)
        (fig,) = _page_data(page, "PLOT_FIGURES")
        assert "edits" not in fig["config"]

    def test_a_series_in_neither_source_is_named_missing_not_drawn(self, tmp_path):
        """A caller that resolved the layout without a name list (or a
        result file that lost the variable between the list and the read)."""
        from dstf.discovery.plots import ResolvedPlot

        plots = (ResolvedPlot(index=0, kind="time", series=("x", "ghost"), missing=()),)
        page, sidecar = self._render(tmp_path, plots=plots)
        assert _page_data(page, "PLOT_SERIES") == [["x"]]
        assert "Not in this result: ghost" in page
        assert sidecar["plots"][0]["missing"] == ["ghost"]

    def test_a_no_baseline_test_draws_a_declared_plot_from_its_trajectories(
        self, tmp_path
    ):
        """Nothing was scored, so ``variables_by_name`` is empty and the
        tracked names are NOT in ``extra_series`` either (``plot_inputs``
        subtracts them). Reading only those two sources called every series
        of the layout missing while the same curve painted below."""
        from dstf.discovery.plots import ResolvedPlot

        plots = (ResolvedPlot(index=0, kind="time", series=("x",), missing=()),)
        page, sidecar = self._render_no_baseline(tmp_path, plots=plots)
        assert _page_data(page, "PLOT_SERIES") == [["x"]]
        assert "Not in this result" not in page
        assert sidecar["plots"][0]["missing"] == []
        (fig,) = _page_data(page, "PLOT_FIGURES")
        (trace,) = [tr for tr in fig["data"] if tr["meta"]["kind"] == "act"]
        assert len(trace["y"]) > 2 and len(trace["x"]) == len(trace["y"])
        # It has no criteria because it has no baseline — the no-baseline
        # block below says so; the section must not claim a second reason.
        assert "is plot only" not in page

    def test_a_no_baseline_test_with_no_layout_is_only_its_no_baseline_block(
        self, tmp_path
    ):
        """Nothing was scored and the test declares no ``plots``: the result
        is drawn ONCE, by the "Simulated Variables (No Baseline)" block that
        has always drawn it. Resolving a default section per tracked name as
        well put every variable on the page twice — a titled section above,
        the same curve again below (the final review)."""
        import re

        page, sidecar = self._render_no_baseline(tmp_path, resolve=True)
        ids = re.findall(r'id="((?:variable-section|plot|nb-plot)-\d+)"', page)
        assert ids == ["nb-plot-0", "nb-plot-1"]
        assert sidecar["plots"] == []

    def test_a_failed_simulations_partial_result_is_not_a_layout(self, tmp_path):
        """`success=False` with variables in it: whatever the backend wrote
        before it stopped. It is not the test's plots — the page drew a
        section per half-written variable (the final review)."""
        import re

        page, _ = self._render_no_baseline(tmp_path, success=False, resolve=True)
        ids = re.findall(r'id="((?:variable-section|plot)-\d+)"', page)
        assert ids == []

    def test_a_no_baseline_test_keeps_the_layout_it_declared(self, tmp_path):
        """The other half of the rule: a test that ASKED for a layout gets
        it, drawn from its no-baseline trajectories."""
        import re

        from dstf.discovery.plots import parse_plots

        page, sidecar = self._render_no_baseline(
            tmp_path, resolve=True, layout=parse_plots([{"series": [{"var": "x"}]}])
        )
        ids = re.findall(r'id="((?:variable-section|plot|nb-plot)-\d+)"', page)
        assert ids == ["variable-section-0", "plot-0", "nb-plot-0", "nb-plot-1"]
        assert sidecar["plots"] == [{"kind": "time", "series": ["x"], "missing": []}]

    def test_a_plot_with_one_or_no_drawn_series_is_not_told_about_overlays(
        self, tmp_path
    ):
        """The sentence explains a MULTI-series plot's missing controls. It
        also printed on a lone plot-only series and on a plot whose every
        series is missing, where it explains nothing."""
        import numpy as np

        from dstf.discovery.plots import ResolvedPlot

        needle = "Overlays and error traces show on a plot with one series."
        t = np.linspace(0, 1, 5)
        lone = (ResolvedPlot(index=0, kind="time", series=("z",), missing=()),)
        page, _ = self._render(tmp_path / "a", plots=lone, extra_series={"z": (t, t)})
        assert needle not in page
        none_drawn = (
            ResolvedPlot(index=0, kind="time", series=("ghost",), missing=()),
        )
        page, _ = self._render(tmp_path / "b", plots=none_drawn)
        assert needle not in page
        # Several series, none of them scored: on plots of their own they
        # would have no overlay or error controls either, so the sentence
        # would be false.
        family = tuple(f"pipe.T[{i}]" for i in (1, 2, 3))
        unscored = (ResolvedPlot(index=0, kind="time", series=family, missing=()),)
        page, _ = self._render(
            tmp_path / "c", plots=unscored, extra_series={n: (t, t) for n in family}
        )
        assert _page_data(page, "PLOT_SERIES") == [list(family)]
        assert needle not in page

    def test_sections_are_numbered_by_position_not_by_the_layouts_index(self, tmp_path):
        """``report.js`` paints ``PLOT_FIGURES[idx]`` into ``plot-${idx}``,
        so a section numbered by anything but its position renders blank."""
        from dstf.discovery.plots import ResolvedPlot

        plots = (ResolvedPlot(index=3, kind="time", series=("x",), missing=()),)
        page, _ = self._render(tmp_path, plots=plots)
        assert 'id="plot-0"' in page and 'id="plot-3"' not in page
        assert '<select class="error-overlay-select" data-plot="0">' in page

    def test_a_variable_on_two_plots_renders_its_criteria_once(self, tmp_path):
        """A variable may sit on several plots; two ``nodes-N`` divs with
        the same id are one document with a duplicate id."""
        from dstf.discovery.plots import ResolvedPlot

        plots = (
            ResolvedPlot(index=0, kind="time", series=("x",), missing=()),
            ResolvedPlot(index=1, kind="time", series=("x", "y"), missing=()),
        )
        page, _ = self._render(tmp_path, plots=plots)
        assert page.count('id="nodes-0"') == 1
        assert page.count("x: criteria are under its first plot.") == 1

    def test_a_multi_series_heading_pairs_each_badge_with_its_own_name(self, tmp_path):
        """``[PASS] x, y`` reads as one verdict for the plot; each badge
        belongs to the variable it scores."""
        from dstf.discovery.plots import ResolvedPlot

        plots = (ResolvedPlot(index=0, kind="time", series=("x", "y"), missing=()),)
        page, _ = self._render(tmp_path, plots=plots)
        section = _parse(page).by_id("variable-section-0")
        (h2,) = [el for el in section.walk() if el.tag == "h2"]
        assert " ".join(h2.text().split()) == "PASS x PASS y"

    def test_a_multi_series_heading_sets_the_plot_only_names_apart(self, tmp_path):
        import numpy as np

        from dstf.discovery.plots import ResolvedPlot

        t = np.linspace(0, 1, 5)
        plots = (ResolvedPlot(index=0, kind="time", series=("x", "v"), missing=()),)
        page, _ = self._render(tmp_path, plots=plots, extra_series={"v": (t, t)})
        section = _parse(page).by_id("variable-section-0")
        (h2,) = [el for el in section.walk() if el.tag == "h2"]
        assert " ".join(h2.text().split()) == "PASS x v"
        (names,) = h2.by_class("plot-only-names")
        assert names.text().strip() == "v"

    def test_an_array_family_gets_one_plot_only_line(self, tmp_path):
        """One line per FAMILY, not per member: four members printed four
        near-identical sentences."""
        import numpy as np

        from dstf.discovery.plots import ResolvedPlot

        t = np.linspace(0, 1, 5)
        members = tuple(f"pipe.T[{i}]" for i in range(1, 5))
        plots = (
            ResolvedPlot(index=0, kind="time", series=("x", *members), missing=()),
        )
        page, _ = self._render(
            tmp_path, plots=plots, extra_series={n: (t, t) for n in members}
        )
        assert page.count("is plot only: no criterion scores it.") == 1
        assert "pipe.T[1:4] is plot only: no criterion scores it." in page


class TestReviewPlotsPath:
    """``cli/review.py``'s per-test plot goes through the same helper."""

    def test_review_resolves_the_layout_it_hands_the_renderer(
        self, tmp_path, monkeypatch
    ):
        import json

        from dstf.authoring.session import Session
        from dstf.cli import review as review_cli
        from dstf.reporting import report_suite
        from test_session import A, _two_var_project

        cfg, work = _two_var_project(tmp_path)
        path = work / "test_0001" / "result.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["variables"]["z"] = [7.0 for _ in payload["time"]]
        path.write_text(json.dumps(payload), encoding="utf-8")
        spec_path = cfg.parent / "test_spec.json"
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        next(e for e in spec["tests"] if e["model"] == A)["plots"] = [
            {"series": [{"var": "x"}, {"var": "z"}]}
        ]
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        s = Session()
        s.open(cfg, work_dir=work)
        project = s.project

        seen: dict = {}

        def fake(**kwargs):
            seen.update(kwargs)
            return None  # no page written, so nothing opens a browser

        monkeypatch.setattr(report_suite, "generate_comparison_plots", fake)
        review_cli._generate_and_open_plots(
            A,
            s.comparisons[A],
            project.results.get(A),
            project.store,
            project.config,
            test=next(t for t in project.tests if t.model_id == A),
        )
        (plot,) = seen["plots"]
        assert plot.series == ("x", "z")
        assert list(seen["extra_series"]) == ["z"]


def test_a_tracked_variable_the_tree_does_not_score_carries_its_own_series(tmp_path):
    """An explicit tree scoring only x, a result tracking x and y, a layout
    naming both: y is TRACKED, so it is not read from the file — and it is
    in no per-variable section either, because no leaf scores it. Leaving it
    out of both sources printed "Not in this result: y" beside a result that
    has it, so its trajectory rides in ``extra_series``, from memory."""
    from types import SimpleNamespace

    import numpy as np

    from dstf.discovery.plots import parse_plots
    from dstf.reporting.report_suite import plot_inputs

    t = np.linspace(0, 1, 3)

    class Files:
        @classmethod
        def result_names(cls, test_dir):
            return ["x", "y"]

        @classmethod
        def result_series(cls, test_dir, names):
            assert names == []  # y is tracked: nothing is read from the file
            return {}

    test = SimpleNamespace(
        plots=parse_plots([{"series": [{"var": "x"}, {"var": "y"}]}]),
        metric_tree_spec=None,
        variable_overrides={},
    )
    scored = [SimpleNamespace(name="x", index=1)]
    result = SimpleNamespace(
        variables=[
            SimpleNamespace(name="x", index=1, time=t, values=t),
            SimpleNamespace(name="y", index=2, time=t, values=2 * t),
        ]
    )
    plots, extra = plot_inputs(test, scored, result, Files, tmp_path)
    assert plots[0].series == ("x", "y") and plots[0].missing == ()
    assert list(extra) == ["y"]
    assert extra["y"][1].tolist() == (2 * t).tolist()


class TestPerTestArgsReadTheLayout:
    """`_build_per_test_args` over a real work dir (the Session fixture's)."""

    def _args(self, tmp_path, layout, **kw):
        import json

        from dstf.authoring.session import Session
        from dstf.reporting.report_suite import _build_per_test_args
        from test_session import A, _two_var_project

        cfg, work = _two_var_project(tmp_path)
        path = work / "test_0001" / "result.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["variables"]["z"] = [7.0 for _ in payload["time"]]
        path.write_text(json.dumps(payload), encoding="utf-8")
        if layout is not None:
            spec_path = cfg.parent / "test_spec.json"
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
            next(e for e in spec["tests"] if e["model"] == A)["plots"] = layout
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
        s = Session()
        s.open(cfg, work_dir=work)
        project = s.project
        kw.setdefault("backend_cls", type(project.backend))
        return _build_per_test_args(
            s.comparisons[A],
            project.results,
            {t.model_id: t for t in project.tests},
            project.store,
            project.config,
            {A: {"test_key": "test_0001", "last_run_at": None}},
            **kw,
        )

    def test_a_layout_naming_an_untracked_variable_reads_it_from_the_file(
        self, tmp_path
    ):
        args = self._args(
            tmp_path, [{"series": [{"var": "x"}, {"var": "z"}, {"var": "gone"}]}]
        )
        (plot,) = args["plots"]
        assert plot.series == ("x", "z") and plot.missing == ("gone",)
        assert list(args["extra_series"]) == ["z"]
        assert args["extra_series"]["z"][1].tolist() == [7.0] * 5

    def test_without_a_layout_the_result_file_is_not_opened(
        self, tmp_path, monkeypatch
    ):
        from dstf.backends.python.backend import PythonBackend

        def boom(cls, *a):
            raise AssertionError("the default layout must not touch the result file")

        monkeypatch.setattr(PythonBackend, "result_names", classmethod(boom))
        monkeypatch.setattr(PythonBackend, "result_series", classmethod(boom))
        args = self._args(tmp_path, None)
        # Resolved without a name list: one plot per tracked variable, the
        # tool's rule, and every series answered from the result in memory.
        assert [p.series for p in args["plots"]] == [("x",), ("y",)]
        assert all(p.missing == () for p in args["plots"])
        assert args["extra_series"] == {}

    def test_with_no_backend_class_a_layout_is_resolved_without_a_name_list(
        self, tmp_path
    ):
        """M5.9 — the backend class is resolved ONCE per suite, wrapped; an
        unregistered backend leaves it ``None``, and every page then draws
        its layout with nothing called missing instead of raising."""
        args = self._args(
            tmp_path,
            [{"series": [{"var": "x"}, {"var": "z"}]}],
            backend_cls=None,
        )
        (plot,) = args["plots"]
        assert plot.series == ("x", "z") and plot.missing == ()
        assert args["extra_series"] == {}


class TestBothTiersDrawTheSamePlots:
    """The owner's ruling (3b): a test's DEFAULT plots are ONE list —
    what was scored, then the rest of what is tracked — and both the
    authoring tool and the report draw it. The report used to draw one plot
    per variable the TREE scored, so a tracked variable no criterion covers
    had a plot on one tier and none on the other."""

    @staticmethod
    def _tool(session, model):
        return [plot.series for plot in session.plots(model)]

    @staticmethod
    def _report(session, model):
        """The page's plot sections — the ONE producer the template loops
        over — for a test whose layout is the default."""
        from dstf.reporting.context_builder import _build_template_context
        from dstf.reporting.report_suite import plot_inputs, plot_page_keys

        project = session.project
        test = project.test(model)
        comp = session.comparisons[model]
        result = project.results.get(model)
        ctx = _build_template_context(
            model,
            list(comp.variables),
            project.store.get_reference(model),
            None,
            None,
            test_model=test,
            result=result,
            metric_tree=comp.metric_tree,
        )
        plots, extra = plot_inputs(
            test, comp.variables, result, type(project.backend), None
        )
        ctx.update(plot_page_keys(ctx, plots, extra, comp.metric_tree, None))
        return [tuple(section["series"]) for section in ctx["plot_sections"]]

    @staticmethod
    def _session(cfg, work):
        from dstf.authoring.session import Session

        s = Session()
        s.open(cfg, work_dir=work)
        return s

    def test_a_tracked_variable_no_criterion_scores_gets_a_plot_on_both_tiers(
        self, tmp_path
    ):
        """A tracks x and y and its tree scores x alone: the roadmap's
        predicate is "a test with N tracked variables shows N plots"."""
        import json

        from test_session import A, _two_var_project

        cfg, work = _two_var_project(tmp_path)
        spec_path = cfg.parent / "test_spec.json"
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        for entry in spec["tests"]:
            if entry["model"] == A:
                entry["metrics"] = {
                    "combinator": "and",
                    "children": [{"metric": "nrmse", "variable": "x"}],
                }
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        s = self._session(cfg, work)
        assert self._tool(s, A) == [("x",), ("y",)]
        assert self._report(s, A) == [("x",), ("y",)]

    @staticmethod
    def _nobaseline(session, model):
        """The names the page's no-baseline block draws. A test with nothing
        scored has NO per-variable sections — that block has always drawn
        its whole result, and a default section each drew every variable a
        second time (the final review)."""
        from dstf.reporting.context_builder import _build_template_context

        project = session.project
        comp = session.comparisons[model]
        ctx = _build_template_context(
            model,
            list(comp.variables),
            project.store.get_reference(model),
            None,
            None,
            test_model=project.test(model),
            result=project.results.get(model),
            metric_tree=comp.metric_tree,
        )
        return [traj["name"] for traj in ctx["nobaseline_trajectories"]]

    def test_every_fixture_model_draws_the_same_plots_on_both_tiers(self, tmp_path):
        from test_session import A, B, D, G, _two_var_project

        cfg, work = _two_var_project(tmp_path)
        s = self._session(cfg, work)
        for model in (A, D, G):
            assert self._tool(s, model) == self._report(s, model), model
        # B has no baseline, so nothing is scored: the same two variables,
        # in the same order, drawn by the block that draws such a test.
        assert self._tool(s, B) == [("x",), ("y",)]
        assert self._report(s, B) == []
        assert self._nobaseline(s, B) == ["x", "y"]

    def test_an_awkward_expression_is_drawn_once_on_both_tiers(self, tmp_path):
        """A UnitTests variable is drawn under the name its result file gives
        it — once — whatever its expression says. It used to be scored under
        a second spelling (`cat(1, a, b)[1]` relabelled `x[1]`), and reading
        the RAW name as well drew the same trajectory twice on the tool."""
        from test_session import (
            AWKWARD_EXPRESSION,
            UT1,
            A,
            _awkward_expression_project,
            with_expressions,
        )

        cfg, work = _awkward_expression_project(tmp_path)
        s = self._session(cfg, work)
        with_expressions(s, A, {UT1: AWKWARD_EXPRESSION})
        assert self._tool(s, A) == [(UT1,), ("y",)]
        assert self._report(s, A) == [(UT1,), ("y",)]

    def test_an_awkward_expression_whose_baseline_lost_it_is_drawn_once(self, tmp_path):
        """`with_baseline=False` does NOT reach the no-baseline regime: the
        fixture's one-variable baseline for A is stored before it, so A is
        scored against a reference that has none of its variables — the
        comparator's lost-baseline branch (the final review). What this
        pins is that the name carries the trajectory there too: a view that
        could not resolve it drew an empty plot under a card the same list
        had just asked for."""
        from dstf.reporting.status import classify
        from test_session import (
            AWKWARD_EXPRESSION,
            UT1,
            A,
            _awkward_expression_project,
            with_expressions,
        )

        cfg, work = _awkward_expression_project(tmp_path, with_baseline=False)
        s = self._session(cfg, work)
        with_expressions(s, A, {UT1: AWKWARD_EXPRESSION})
        assert s.project.store.get_reference(A) is not None
        assert classify(s.comparisons[A]) == "FAIL"
        assert self._tool(s, A) == [(UT1,), ("y",)]
        assert self._report(s, A) == [(UT1,), ("y",)]
        view = s.view(A, UT1)
        assert len(view.act_values) == 5

    def test_an_awkward_expression_on_an_unscored_variable_draws_its_trace(
        self, tmp_path
    ):
        """A tracked variable no leaf scores rides in ``extra_series``, from
        the result in memory — and it is read there under the same name the
        page plots it by. A second spelling matched none of the tracked
        names, so the section drew nothing under its own title (the final
        review)."""
        import json

        from dstf.reporting.context_builder import _build_template_context
        from dstf.reporting.report_suite import plot_inputs, plot_page_keys
        from test_session import (
            AWKWARD_EXPRESSION,
            UT1,
            A,
            _awkward_expression_project,
            with_expressions,
        )

        cfg, work = _awkward_expression_project(tmp_path)
        spec_path = cfg.parent / "test_spec.json"
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        for entry in spec["tests"]:
            if entry["model"] == A:
                entry["metrics"] = {
                    "combinator": "and",
                    "children": [{"metric": "nrmse", "variable": "y"}],
                }
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        s = self._session(cfg, work)
        with_expressions(s, A, {UT1: AWKWARD_EXPRESSION})
        project = s.project
        comp = s.comparisons[A]
        result = project.results.get(A)
        plots, extra = plot_inputs(
            project.test(A), comp.variables, result, type(project.backend), None
        )
        assert [plot.series for plot in plots] == [("y",), (UT1,)]
        assert list(extra) == [UT1] and len(extra[UT1][1]) == 5
        ctx = _build_template_context(
            A,
            list(comp.variables),
            project.store.get_reference(A),
            None,
            None,
            test_model=project.test(A),
            result=result,
            metric_tree=comp.metric_tree,
        )
        ctx.update(plot_page_keys(ctx, plots, extra, comp.metric_tree, None))
        section = next(s for s in ctx["plot_sections"] if s["series"] == [UT1])
        figure = ctx["plot_figures"][section["index"]]
        (trace,) = [tr for tr in figure["data"] if tr["meta"]["kind"] == "act"]
        assert len(trace["y"]) == 5

    def test_an_awkward_expression_with_really_no_baseline_draws_its_trace(
        self, tmp_path
    ):
        """B has a result and no baseline at all, so nothing is scored and
        the name its result file gives the variable is the page's only handle
        on the trajectory."""
        import json

        from dstf.reporting.status import NO_REF, classify
        from test_session import (
            AWKWARD_EXPRESSION,
            UT1,
            B,
            _two_var_project,
            with_expressions,
        )

        cfg, work = _two_var_project(tmp_path)
        t = [i / 4 for i in range(5)]
        (work / "test_0002" / "result.json").write_text(
            json.dumps(
                {
                    "success": True,
                    "time": t,
                    "variables": {UT1: t, "y": [2 * v for v in t]},
                }
            ),
            encoding="utf-8",
        )
        spec_path = cfg.parent / "test_spec.json"
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        for entry in spec["tests"]:
            if entry["model"] == B:
                entry["variables"] = [UT1, "y"]
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        s = self._session(cfg, work)
        with_expressions(s, B, {UT1: AWKWARD_EXPRESSION})
        assert classify(s.comparisons[B]) == NO_REF  # the regime it is named for
        assert s.project.store.get_reference(B) is None
        assert self._tool(s, B) == [(UT1,), ("y",)]
        assert len(s.view(B, UT1).act_values) == 5


class TestTheTrajectoriesCarryTheExpression:
    """``build_trajectories`` is where the page and the sidecar learn a
    variable's expression: the label the report draws beside its name, and
    the text an overlay's own source may spell the quantity with. An entry
    without it reads as a variable whose model wrote none."""

    def _result(self, expression: str):
        import numpy as np

        from dstf.backends.types import TestResult, VariableResult

        t = np.linspace(0.0, 1.0, 5)
        return TestResult(
            model_id="M",
            success=True,
            variables=[
                VariableResult(
                    index=1,
                    name="unitTests.x[1]",
                    time=t,
                    values=t,
                    expression=expression,
                )
            ],
        )

    def test_a_scored_variables_entry_carries_it(self):
        from dstf.reporting.context_builder import build_trajectories

        result = self._result("pipe.T[1]")
        ref = {
            "time": [0.0, 0.25, 0.5, 0.75, 1.0],
            "variables": [
                {
                    "index": 1,
                    "name": "unitTests.x[1]",
                    "expression": "pipe.T[1]",
                    "values": [0.0, 0.25, 0.5, 0.75, 1.0],
                }
            ],
        }
        comparisons = [
            VariableComparison(
                index=1,
                name="unitTests.x[1]",
                passed=True,
                nrmse=0.0,
                rmse=0.0,
                signal_range=1.0,
                max_abs_error=0.0,
                max_abs_error_time=0.0,
                reference_final=1.0,
                actual_final=1.0,
            )
        ]
        trajectories, _diag, nobaseline = build_trajectories(comparisons, result, ref)
        assert [(t["name"], t["expression"]) for t in trajectories] == [
            ("unitTests.x[1]", "pipe.T[1]")
        ]
        assert nobaseline == []

    def test_a_no_baseline_entry_carries_it(self):
        from dstf.reporting.context_builder import build_trajectories

        trajectories, _diag, nobaseline = build_trajectories(
            [], self._result("pipe.T[1]"), None
        )
        assert trajectories == []
        assert [(t["name"], t["expression"]) for t in nobaseline] == [
            ("unitTests.x[1]", "pipe.T[1]")
        ]

    def test_a_variable_whose_model_wrote_none_carries_the_empty_text(self):
        from dstf.reporting.context_builder import build_trajectories

        _traj, _diag, nobaseline = build_trajectories([], self._result(""), None)
        assert nobaseline[0]["expression"] == ""


class TestAVariableReadsAsItsExpression:
    """The rendered page: the title reads the expression, the muted name
    rides beside it, and every KEY on the page is still the name."""

    N1, N2 = "unitTests.x[1]", "unitTests.x[2]"

    def _render(self, tmp_path, expressions, metrics=None, name="page"):
        import json
        from pathlib import Path

        import numpy as np

        from dstf.backends.types import TestResult, VariableResult
        from dstf.comparison.comparator import compare_test
        from dstf.comparison.tree_spec import parse_metric_tree
        from dstf.discovery.test_registry import TestModel
        from dstf.reporting.report_suite import generate_comparison_plots

        t = np.linspace(0.0, 1.0, 51)
        names = list(expressions)
        ref = {
            "test_id": "ref_0001",
            "time": t.tolist(),
            "variables": [
                {
                    "index": i + 1,
                    "name": n,
                    "expression": expressions[n],
                    "values": (np.sin(t) * (i + 1)).tolist(),
                }
                for i, n in enumerate(names)
            ],
        }
        result = TestResult(
            model_id="M",
            success=True,
            variables=[
                VariableResult(
                    index=i + 1,
                    name=n,
                    time=t,
                    values=np.sin(t) * (i + 1) + 0.001,
                    expression=expressions[n],
                )
                for i, n in enumerate(names)
            ],
        )
        spec = parse_metric_tree(
            metrics
            or {
                "combinator": "and",
                "children": [
                    {"metric": "nrmse", "variable": n, "tolerance": 0.5} for n in names
                ],
            }
        )
        test = TestModel(
            model_id="M",
            source_file=Path("fixture.mo"),
            source_package="Fixture",
            short_name="M",
            n_vars=len(names),
            source="unit_tests",
            x_expressions=[expressions[n] for n in names],
            metric_tree_spec=spec,
        )
        comp = compare_test(test, result, ref)
        html = generate_comparison_plots(
            "M",
            ref,
            result,
            comp.variables,
            tmp_path / name,
            test_model=test,
            metric_tree=comp.metric_tree,
            max_embedded_samples=500,
        )
        return html.read_text(encoding="utf-8"), json.loads(
            (tmp_path / name / "comparison_data.json").read_text(encoding="utf-8")
        )

    def test_the_section_reads_the_label_and_is_keyed_by_the_name(self, tmp_path):
        page, _sidecar = self._render(tmp_path, {self.N1: "pipe.T[1]"})
        section = _parse(page).by_id("variable-section-0")
        heading = next(el for el in section.walk() if el.tag == "h2")
        assert "pipe.T[1]" in heading.text()
        (muted,) = heading.by_class("dstf-varname")
        assert muted.text() == self.N1
        tree = next(
            el for el in section.walk() if el.attrs.get("data-variable") is not None
        )
        assert tree.attrs["data-variable"] == self.N1
        assert _page_data(page, "PLOT_SERIES") == [[self.N1]]
        labels = [
            el.text()
            for el in _parse(page).by_id("nodes-full").walk()
            if "node-label" in el.classes
        ]
        assert "nrmse · pipe.T[1]" in labels
        figure = _page_data(page, "PLOT_FIGURES")[0]
        actual = next(tr for tr in figure["data"] if tr["meta"]["id"] == "actual")
        assert actual["hovertemplate"].startswith("pipe.T[1]:")

    def test_a_variable_with_no_expression_renders_as_it_always_did(self, tmp_path):
        """The predicate for every spec-tracked variable in the suite: no
        muted name, no second spelling, nothing added."""
        page, _sidecar = self._render(tmp_path, {"x": ""}, name="plain")
        section = _parse(page).by_id("variable-section-0")
        heading = next(el for el in section.walk() if el.tag == "h2")
        assert heading.text().split()[-1] == "x"
        assert heading.by_class("dstf-varname") == []
        figure = _page_data(page, "PLOT_FIGURES")[0]
        for trace in figure["data"]:
            if trace["meta"]["kind"] in ("act", "ref"):
                assert "hovertemplate" not in trace

    def test_two_variables_on_one_plot_each_read_their_own_label(self, tmp_path):
        import json

        page, _sidecar = self._render(
            tmp_path,
            {self.N1: "pipe.T[1]", self.N2: "sum(core.Qs[6, :])"},
            name="two",
        )
        section = _parse(page).by_id("variable-section-0")
        # One plot per variable by default: each section's own title.
        titles = [
            next(
                el
                for el in _parse(page).by_id(f"variable-section-{i}").walk()
                if el.tag == "h2"
            ).text()
            for i in (0, 1)
        ]
        assert "pipe.T[1]" in titles[0] and "sum(core.Qs[6, :])" in titles[1]
        assert section.attrs["data-plot"] == "0"
        sidecar = json.loads(json.dumps(_sidecar))
        assert [t["name"] for t in sidecar["trajectories"]] == [self.N1, self.N2]
        assert [t["label"] for t in sidecar["trajectories"]] == [
            "pipe.T[1]",
            "sum(core.Qs[6, :])",
        ]

    def test_three_unrelated_expressions_are_three_families_on_one_plot(self):
        """`unitTests.x[1..3]` is not an array. Folded, the three quantities
        took one legend entry and one colour ramp."""
        from dstf.figures.families import group_families

        names = [f"unitTests.x[{i}]" for i in (1, 2, 3)]
        labels = dict(zip(names, ["a.Q", "b.Q", "sum(core.Qs[6, :])"]))
        assert len(group_families(names, labels)) == 3
