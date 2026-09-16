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
# * Finding 76h (support) — the template emits ``VARIABLE_ORDER`` as an
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
# Finding 76h (support) — explicit VARIABLE_ORDER emission
# ---------------------------------------------------------------------------


def test_template_emits_variable_order(tmp_path):
    """Integer-like variable names must keep template order in JS — the
    template emits VARIABLE_ORDER explicitly (Object.keys() would sort
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
    m = re.search(r"VARIABLE_ORDER:\s*(\[[^\]]*\])", html)
    assert m, "window.MT_REPORT must carry VARIABLE_ORDER"
    assert json.loads(m.group(1)) == ["zeta", "123"]


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
        assert "FIGURES:" in page and '"/metrics/children/0/tube/band"' in page
        assert "figures" not in sidecar and "diag_figures" not in sidecar
        assert len(sidecar["trajectories"][0]["act_time"]) == 3001
        start = page.index("FIGURES:")
        assert '"full_samples": 3001' in page[start:]

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
        assert len(built) == 2
        figures, diag, nb = (
            built[0]["figures"],
            built[0]["diag_figures"],
            built[1]["nb_figures"],
        )
        # Every list the loop checks is non-empty, and the per-variable
        # figure really carries a handle — else the loop is vacuous.
        assert "x" in figures and diag and nb
        assert any(
            (tr.get("meta") or {}).get("kind") == "handle"
            for tr in figures["x"]["data"]
        )
        for fig in [*figures.values(), *diag, *nb]:
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
            "figures": {
                "x": {
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
            }
        }
        assert _decimated_traces(ctx) == [
            {"variable": "x", "trace": "Actual", "full": 9, "embedded": 2},
            {"variable": "x", "trace": "Tube", "full": 9, "embedded": 2},
            {"variable": "x", "trace": "Unstamped", "full": 9, "embedded": 2},
        ]

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
        figures = json.loads(
            re.search(r"FIGURES:\s*(\{.*?\}),\n\s*DIAG_FIGURES", page, re.S).group(1)
        )
        sidecar = json.loads(
            (plot_dir / "comparison_data.json").read_text(encoding="utf-8")
        )
        return page, figures, sidecar

    @pytest.mark.parametrize("sentinel_first", [True, False])
    def test_a_sentinel_leaf_does_not_blank_its_variables_plot(
        self, tmp_path, sentinel_first
    ):
        page, figures, _sidecar = self._render_two_leaves(tmp_path, sentinel_first)
        traces = figures["x"]["data"]
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
            for sh in figures["x"]["layout"].get("shapes", [])
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
        figures = json.loads(
            re.search(r"FIGURES:\s*(\{.*?\}),\n\s*DIAG_FIGURES", page, re.S).group(1)
        )
        traces = {tr.get("name"): tr for tr in figures["x"]["data"]}
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
