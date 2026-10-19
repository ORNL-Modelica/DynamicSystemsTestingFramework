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
from dstf.storage.baseline_codec import decode_reference


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
# reporter batch.
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
            plots=(),  # no test to derive them from
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
            plots=(),  # no test to derive them from
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


def test_a_template_comment_is_no_line_of_the_page(tmp_path):
    """The summary's first branch stands one line under its `<div>`, as
    every branch tag leaves it: a `{# … #}` put above the branches was a
    blank line more in every per-test page."""
    plot_dir = tmp_path / "report"
    generate_comparison_plots(
        plots=(),  # no test to derive them from
        model_id="Fixture.Comment",
        ref_data=None,
        result=None,
        comparisons=[_nonfinite_comparison()],
        plot_dir=plot_dir,
    )
    html = (plot_dir / "interactive.html").read_text(encoding="utf-8")
    (after,) = re.findall(r'<div class="summary" id="summary">(\s*)\S', html)
    assert after.count("\n") == 2, repr(after)


class TestRunMetadataBanner:
    """Run-provenance banner in the per-test interactive.html."""

    def test_banner_rendered_when_metadata_present(self, tmp_path):
        plot_dir = tmp_path / "report"
        generate_comparison_plots(
            plots=(),  # no test to derive them from
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
            plots=(),  # no test to derive them from
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
            variable_patterns=["x", "y"],
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
            comp = compare_test(model, result, decode_reference(ref))
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
    from dstf.discovery.test_registry import TestModel

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
        test_model=TestModel(
            model_id="Fixture.Order",
            source_file=Path("fixture.mo"),
            source_package="Fixture",
            short_name="Order",
            n_vars=2,
        ),
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
            plots=(),  # no test to derive them from
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


class TestWorstNrmse:
    """The worst NRMSE among the criteria SCORED by nrmse. A sentinel leaf
    carries inf/nan; a plain max() let it swallow every real score on the
    test. And a max over every leaf read a tube's NRMSE — a number its
    criterion does not score by — as the test's worst."""

    @staticmethod
    def _scored(nrmse: float, name: str = "h", mode: str = "nrmse"):
        vc = _nonfinite_comparison(name)
        vc.nrmse = nrmse
        vc.mode = mode
        return vc

    def test_sentinel_does_not_swallow_the_finite_leaves(self):
        from dstf.reporting.context_builder import worst_nrmse

        worst, n_unscored = worst_nrmse(
            [self._scored(1e-5), _nonfinite_comparison("bad"), self._scored(3e-4)]
        )
        assert worst == 3e-4
        assert n_unscored == 1

    def test_all_unscored_reports_the_count(self):
        from dstf.reporting.context_builder import worst_nrmse

        worst, n_unscored = worst_nrmse(
            [_nonfinite_comparison("a"), _nonfinite_comparison("b")]
        )
        assert worst is None
        assert n_unscored == 2

    def test_nan_counts_as_unscored(self):
        from dstf.reporting.context_builder import worst_nrmse

        worst, n_unscored = worst_nrmse([self._scored(float("nan")), self._scored(2.0)])
        assert worst == 2.0
        assert n_unscored == 1

    def test_a_passing_tubes_nrmse_is_not_the_tests_worst(self):
        """The evidence: a tube at 100 % inside whose `nrmse` field is
        2.8213e-01, beside the nrmse criterion that fails at 4.264e-02."""
        from dstf.reporting.context_builder import worst_nrmse

        tube = self._scored(2.8213e-01, "x", mode="tube")
        tube.passed = True
        failing = self._scored(4.264e-02, "x")
        advisory = self._scored(2.128e-02, "x")
        assert worst_nrmse([tube, failing, advisory]) == (4.264e-02, 0)

    @pytest.mark.parametrize(
        "mode",
        ["tube", "range", "points", "event-timing", "scalar", "dominant-frequency", ""],
    )
    def test_a_test_with_no_nrmse_criterion_has_no_worst_nrmse(self, mode):
        """Each of these modes keeps a number of its own in the field — and
        a criterion with NO metric (`""`: a flat override naming a mode
        nothing is registered under) is not one scored by nrmse: no surface
        reads a default into an empty mode."""
        from dstf.reporting.context_builder import worst_nrmse

        assert worst_nrmse([self._scored(0.5, mode=mode)]) == (None, 0)

    def test_an_unscored_criterion_of_another_mode_is_still_counted(self):
        from dstf.reporting.context_builder import worst_nrmse

        bad = _nonfinite_comparison("bad")
        bad.mode = "range"
        assert worst_nrmse([self._scored(1e-5), bad]) == (1e-5, 1)

    def test_summary_sidecar_carries_both(self, tmp_path):
        plot_dir = tmp_path / "report"
        generate_comparison_plots(
            plots=(),  # no test to derive them from
            model_id="Fixture.Worst",
            ref_data=None,
            result=None,
            comparisons=[
                self._scored(1e-5),
                _nonfinite_comparison("bad"),
                self._scored(0.9, mode="tube"),
            ],
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
        worst = next(s for s in stats if s["label"] == "Worst NRMSE")
        assert worst["current"] == "n/a (1 unscored criterion)"

    def test_the_report_header_reads_the_columns_number(self):
        """One name, one number: the header stat is the dashboard column's."""
        from dstf.reporting.context_builder import _build_key_stats

        comps = [self._scored(0.28, mode="tube"), self._scored(4.264e-02)]
        (worst,) = [s for s in _build_key_stats(comps, {}, {}) if "Worst" in s["label"]]
        assert (worst["label"], worst["current"]) == ("Worst NRMSE", "4.2640e-02")

    def test_the_report_header_reads_a_dash_with_no_nrmse_criterion(self):
        from dstf.reporting.context_builder import _build_key_stats

        comps = [self._scored(0.28, mode="tube")]
        (worst,) = [s for s in _build_key_stats(comps, {}, {}) if "Worst" in s["label"]]
        assert (worst["label"], worst["current"]) == ("Worst NRMSE", "—")
        assert [s for s in _build_key_stats([], {}, {}) if "Worst" in s["label"]] == []


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
            variable_patterns=["x"],
            variable_overrides={
                "x": {"mode": "tube", "tube_width_mode": "band", "tube_abs": 0.01}
            },
        )
        comp = compare_test(test, result, decode_reference(ref))
        plot_dir = tmp_path / "plots"
        html = generate_comparison_plots(
            "M",
            decode_reference(ref),
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
            variable_patterns=["x"],
            variable_overrides={"x": {"mode": "points", "tolerance": 0.01}},
        )
        comp = compare_test(test, result, decode_reference(ref))

        built: list[dict] = []
        real = report_suite._build_figures

        def spy(*args, **kwargs):
            out = real(*args, **kwargs)
            built.append(out)
            return out

        # The plots' figures are ``plot_page_keys``' — a no-baseline test's
        # too (O2); the diagnostic ones stayed in ``_build_figures``, so
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
            decode_reference(ref),
            result,
            comp.variables,
            tmp_path / "plots",
            test_model=test,
            metric_tree=comp.metric_tree,
        )
        # No baseline, no comparisons: one plot per tracked variable.
        report_suite.generate_comparison_plots(
            "M", None, result, [], tmp_path / "nb_plots", test_model=test
        )
        assert len(built) == 2 and len(page_built) == 2
        figures, diag, nb = (
            page_built[0]["plot_figures"],
            built[0]["diag_figures"],
            page_built[1]["plot_figures"],
        )
        # Every list the loop checks is non-empty, and the per-variable
        # figure really carries a handle — else the loop is vacuous.
        assert page_built[0]["plot_series"] == [["x"]] and diag and nb
        assert any(
            (tr.get("meta") or {}).get("kind") == "handle" for tr in figures[0]["data"]
        )
        for fig in [*figures, *diag, *nb]:
            assert "edits" not in fig["config"]
            # No pixel-sized shape: a draggable ring is one, and only the
            # tool's figure has any.
            assert not any(
                s.get("xsizemode") == "pixel" for s in fig["layout"].get("shapes") or []
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
            "plot_sections": [
                {"index": 0, "title": "x", "series": ["x"], "subject": ["x"]}
            ],
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
                    "title": "pipe.T[1:2]",
                    "series": ["pipe.T[1]", "pipe.T[2]", "other"],
                    "subject": ["pipe.T[1]", "pipe.T[2]"],
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
        # The plot is named by its SUBJECT, as its heading is; the context
        # series `other` is a trace on it.
        assert {r["variable"] for r in rows} == {"pipe.T[1:2]"}

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
        comp = compare_test(test, result, decode_reference(ref))
        plot_dir = tmp_path / ("first" if sentinel_first else "second")
        html = generate_comparison_plots(
            "M",
            decode_reference(ref),
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
        comp = compare_test(test, result, decode_reference(ref))
        plot_dir = tmp_path / "lone"
        html = generate_comparison_plots(
            "M",
            decode_reference(ref),
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
        assert "tube criterion not scored" in criterion
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
        comp = compare_test(test, result, decode_reference(ref))
        html = generate_comparison_plots(
            "M",
            decode_reference(ref),
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
        assert "1 / 3 criteria passed" in " ".join(summary.text().split())
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

    def test_a_plots_mount_carries_its_subjects_leaves_each_led_by_its_ancestors(
        self, tmp_path
    ):
        page, _comp = self._render(tmp_path)
        doc = _parse(page)
        assert list(self._leaves(doc.by_id("nodes-0"))) == [self.TUBE, self.ADVISORY]
        assert list(self._leaves(doc.by_id("nodes-1"))) == [self.RANGE]
        # No combinator is drawn under a plot: where a leaf sits in the tree
        # is its row's breadcrumb, the root's kind first.
        crumbs = lambda mount: [  # noqa: E731
            " ".join(el.text().split()) for el in mount.by_class("node-crumbs")
        ]
        for mount in ("nodes-0", "nodes-1"):
            assert doc.by_id(mount).by_class("node-combinator") == [], mount
        assert crumbs(doc.by_id("nodes-0")) == ["and ›", "and › warn ›"]
        assert crumbs(doc.by_id("nodes-1")) == ["and ›"]
        # Each plot's crumbs are as wide as its longest, so its leaf names
        # start in one column: no fixed width that a deeper row outgrows.
        widths = lambda mount: [  # noqa: E731
            el.attrs.get("style") for el in mount.by_class("node-crumbs")
        ]
        assert widths(doc.by_id("nodes-0")) == ["min-width: 12ch"] * 2
        assert widths(doc.by_id("nodes-1")) == ["min-width: 5ch"]

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
            variable_patterns=["x"],
            **kw,
        )

    def _render(self, tmp_path, *, test_model, metric_tree, comparisons, overlays=None):
        _t, _ref_v, ref, result = self._inputs()
        html = generate_comparison_plots(
            "M",
            decode_reference(ref),
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
        return test, compare_test(test, result, decode_reference(ref))

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
    """The page's plots are DERIVED from the criteria tree
    (`discovery.plots.derive_plots`): one per scored variable or array
    family, in the order the tree first names it, then the watch plots.
    Each section lists its subject's criteria in tree order, each led by its
    ancestors' kinds, and its context series, drawn grey and not scored."""

    #: x and y, each scored where this tree puts it: y first, and a k-of-n
    #: holding one criterion on each.
    NESTED = {
        "combinator": "and",
        "children": [
            {"metric": "nrmse", "variable": "y", "tolerance": 0.5},
            {
                "combinator": "k-of-n",
                "k": 1,
                "children": [
                    {"metric": "nrmse", "variable": "x", "tolerance": 0.5},
                    {"metric": "range", "variable": "y", "max_value": 5.0},
                ],
            },
        ],
    }

    #: Scores x and y alone: a result variable beyond them is scored by no
    #: implicit tree, so it can be context or a watch plot.
    XY = {
        "combinator": "and",
        "children": [
            {"metric": "nrmse", "variable": "x", "tolerance": 0.5},
            {"metric": "nrmse", "variable": "y", "tolerance": 0.5},
        ],
    }

    def _render(self, tmp_path, *, metrics=None, extras=None, extra_vars=(), **kw):
        """x (sin) and y (cos) with a baseline, and ``extra_vars`` in the
        result that no baseline has (scored by nothing only under an
        explicit tree, ``XY``). The plots come from `plot_inputs`, the way
        the run resolves them, unless the caller passes ``plots=``."""
        import json
        from pathlib import Path

        import numpy as np

        from dstf.backends.types import TestResult, VariableResult
        from dstf.comparison.comparator import compare_test
        from dstf.comparison.tree_spec import parse_metric_tree
        from dstf.discovery.plots import parse_plots
        from dstf.discovery.test_registry import TestModel
        from dstf.reporting.report_suite import generate_comparison_plots, plot_inputs

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
                *(
                    VariableResult(index=3 + i, name=n, time=t, values=t * 0 + 7.0)
                    for i, n in enumerate(extra_vars)
                ),
            ],
        )
        test = TestModel(
            model_id="M",
            source_file=Path("fixture.mo"),
            source_package="Fixture",
            short_name="M",
            n_vars=2,
            variable_patterns=["x", "y"],
            metric_tree_spec=parse_metric_tree(metrics) if metrics else None,
            plots=parse_plots(extras) if extras is not None else None,
        )
        comp = compare_test(test, result, decode_reference(ref))
        if "plots" not in kw:
            kw["plots"], kw["extra_series"] = plot_inputs(
                test, comp.variables, result, None, None, ()
            )
        plot_dir = tmp_path / "plots"
        html = generate_comparison_plots(
            "M",
            decode_reference(ref),
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

    def _render_no_baseline(self, tmp_path, *, success=True, layout=None, **kw):
        """No reference and no comparisons: the NO_REF regime, where
        ``variables_by_name`` is empty and every tracked series lives in
        ``nobaseline_trajectories``. The plots come from `plot_inputs`;
        ``success=False`` is a simulation that stopped partway and left
        what it had behind."""
        import json
        from pathlib import Path

        import numpy as np

        from dstf.backends.types import TestResult, VariableResult
        from dstf.discovery.plots import parse_plots
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
            plots=parse_plots(layout) if layout is not None else None,
        )
        kw["plots"], kw["extra_series"] = plot_inputs(test, [], result, None, None, ())
        plot_dir = tmp_path / "plots"
        html = generate_comparison_plots(
            "M", None, result, [], plot_dir, test_model=test, **kw
        )
        page = html.read_text(encoding="utf-8")
        sidecar = json.loads(
            (plot_dir / "comparison_data.json").read_text(encoding="utf-8")
        )
        return page, sidecar

    @staticmethod
    def _rows(page: str, index: int) -> list[tuple[str, str, str]]:
        """``(crumbs, variable, leaf path)`` of plot ``index``'s criteria
        rows, in page order."""
        section = _parse(page).by_id(f"variable-section-{index}")
        out = []
        for row in section.by_class("criteria-row"):
            crumbs = row.by_class("node-crumbs")
            leaf = next(el for el in row.walk() if "node-leaf" in el.classes)
            out.append(
                (
                    crumbs[0].text() if crumbs else "",
                    row.attrs["data-variable"],
                    leaf.attrs["data-path"],
                )
            )
        return out

    def test_with_no_extras_the_page_has_one_section_per_scored_variable(
        self, tmp_path
    ):
        page, sidecar = self._render(tmp_path)
        assert _page_data(page, "PLOT_SERIES") == [["x"], ["y"]]
        assert len(_page_data(page, "PLOT_FIGURES")) == 2
        assert "VARIABLE_ORDER" not in page and "  FIGURES:" not in page
        assert 'id="plot-0"' in page and 'id="plot-1"' in page
        assert page.count('class="error-overlay-select"') == 2
        # ONE variable: its title names it, so its badge is the bare verdict.
        badges = _parse(page).by_id("variable-section-0").by_class("var-status")
        assert [el.text().strip() for el in badges] == ["PASS"]
        # The sidecar records what the page DREW: each plot's subject, its
        # context, whether it is a watch plot, and what the result lacked.
        assert sidecar["plots"] == [
            {
                "kind": "time",
                "series": [n],
                "subject": [n],
                "context": [],
                "watch": False,
                "missing": [],
            }
            for n in ("x", "y")
        ]

    def test_the_plots_follow_the_tree_not_the_variables(self, tmp_path):
        """The tree names y first: y's plot comes first, x's second, though
        the result (and the scored list) hold x before y."""
        page, sidecar = self._render(tmp_path, metrics=self.NESTED)
        assert _page_data(page, "PLOT_SERIES") == [["y"], ["x"]]
        assert [p["subject"] for p in sidecar["plots"]] == [["y"], ["x"]]

    def test_a_criterion_row_is_led_by_its_ancestors_in_tree_order(self, tmp_path):
        """y's plot carries both criteria on y, where the tree has them: the
        root's child first, the k-of-n's second — each led by the kinds
        above it, the root's first."""
        page, _ = self._render(tmp_path, metrics=self.NESTED)
        assert self._rows(page, 0) == [
            ("and ›", "y", "/metrics/children/0"),
            ("and › k-of-n ›", "y", "/metrics/children/1/children/1"),
        ]
        assert self._rows(page, 1) == [
            ("and › k-of-n ›", "x", "/metrics/children/1/children/0"),
        ]
        # The whole tree is still at the top, every leaf in it.
        full = _parse(page).by_id("nodes-full")
        assert len([el for el in full.walk() if "node-leaf" in el.classes]) == 3

    def test_a_context_series_renders_muted_and_is_not_a_criterion(self, tmp_path):
        page, sidecar = self._render(
            tmp_path,
            metrics=self.XY,
            extras=[{"var": "x", "context": ["z"]}],
            extra_vars=("z",),
        )
        assert _page_data(page, "PLOT_SERIES") == [["x", "z"], ["y"]]
        (fig, _) = _page_data(page, "PLOT_FIGURES")
        (ctx_trace,) = [tr for tr in fig["data"] if tr["meta"]["kind"] == "context"]
        assert ctx_trace["meta"]["id"] == "context/z"
        assert ctx_trace["y"] == [7.0] * 51
        # One subject: x keeps its error select and its actual trace.
        assert '<select class="error-overlay-select" data-plot="0">' in page
        assert [tr["meta"]["id"] for tr in fig["data"] if tr["meta"]["kind"] == "act"]
        section = _parse(page).by_id("variable-section-0")
        (context,) = section.by_class("plot-context")
        assert " ".join(context.text().split()) == "Context: z"
        assert [
            el.attrs["data-variable"] for el in section.by_class("criteria-row")
        ] == ["x"]
        assert sidecar["plots"][0]["subject"] == ["x"]
        assert sidecar["plots"][0]["context"] == ["z"]

    def test_a_watch_plot_says_no_criteria_yet(self, tmp_path):
        page, sidecar = self._render(
            tmp_path, metrics=self.XY, extras=[{"var": "z"}], extra_vars=("z",)
        )
        assert _page_data(page, "PLOT_SERIES") == [["x"], ["y"], ["z"]]
        assert sidecar["plots"][2]["watch"] is True
        section = _parse(page).by_id("variable-section-2")
        notes = [el.text() for el in section.by_class("plot-note")]
        assert notes == ["No criteria yet."]
        assert section.by_class("criteria-row") == []
        assert page.count("No criteria yet.") == 1
        (_, _, fig) = _page_data(page, "PLOT_FIGURES")
        (act,) = [tr for tr in fig["data"] if tr["meta"]["kind"] == "act"]
        assert act["y"] == [7.0] * 51

    def test_a_branching_pipes_test_has_one_plot_per_unit_tests_variable(
        self, tmp_path
    ):
        """BranchingDynamicPipes' shape: ten UnitTests variables whose
        expressions read ``cat(1, pipe2.mediums.T, pipe2.mediums.p)[i]``.
        Each is a test quantity of its own (O3): ten plots, no fold."""
        import numpy as np

        from dstf.backends.types import TestResult, VariableResult
        from dstf.comparison.comparator import compare_test
        from dstf.discovery.test_registry import TestModel
        from dstf.reporting.report_suite import generate_comparison_plots, plot_inputs

        t = np.linspace(0.0, 1.0, 21)
        names = [f"unitTests.x[{i}]" for i in range(1, 11)]
        expr = [f"cat(1, pipe2.mediums.T, pipe2.mediums.p)[{i}]" for i in range(1, 11)]
        ref = {
            "test_id": "ref_0001",
            "time": t.tolist(),
            "variables": [
                {"index": i + 1, "name": n, "values": (t * (i + 1)).tolist()}
                for i, n in enumerate(names)
            ],
        }
        result = TestResult(
            model_id="M",
            success=True,
            variables=[
                VariableResult(
                    index=i + 1, name=n, time=t, values=t * (i + 1), expression=e
                )
                for i, (n, e) in enumerate(zip(names, expr))
            ],
        )
        test = TestModel(
            model_id="M",
            source_file=Path("fixture.mo"),
            source_package="Fixture",
            short_name="M",
            n_vars=10,
            source="unit_tests",
            x_expressions=expr,
        )
        comp = compare_test(test, result, decode_reference(ref))
        plots, extra = plot_inputs(test, comp.variables, result, None, None, ())
        html = generate_comparison_plots(
            "M",
            decode_reference(ref),
            result,
            comp.variables,
            tmp_path / "p",
            test_model=test,
            metric_tree=comp.metric_tree,
            plots=plots,
            extra_series=extra,
        )
        page = html.read_text(encoding="utf-8")
        # In the tree's order, which is the index order here — x[10] last.
        assert _page_data(page, "PLOT_SERIES") == [[n] for n in names]
        titles = [
            next(
                el
                for el in _parse(page).by_id(f"variable-section-{i}").walk()
                if el.tag == "h2"
            ).text()
            for i in range(10)
        ]
        assert all(expr[i] in titles[i] for i in range(10))

    def test_an_array_of_anything_else_folds_into_one_plot(self, tmp_path):
        """``pipe.T[1..3]`` — result-file names, no UnitTests element: one
        family plot, every member drawn (by index), one badge per member,
        and no error select (the subject is not ONE variable). The tree
        names them 3, 1, 2: the criteria rows are where the TREE has them,
        not in the members' order."""
        import numpy as np

        from dstf.backends.types import TestResult, VariableResult
        from dstf.comparison.comparator import compare_test
        from dstf.comparison.tree_spec import parse_metric_tree
        from dstf.discovery.test_registry import TestModel
        from dstf.reporting.report_suite import generate_comparison_plots, plot_inputs

        t = np.linspace(0.0, 1.0, 21)
        names = [f"pipe.T[{i}]" for i in (1, 2, 3)]
        ref = {
            "test_id": "ref_0001",
            "time": t.tolist(),
            "variables": [
                {"index": i + 1, "name": n, "values": (t + i).tolist()}
                for i, n in enumerate(names)
            ],
        }
        result = TestResult(
            model_id="M",
            success=True,
            variables=[
                VariableResult(index=i + 1, name=n, time=t, values=t + i)
                for i, n in enumerate(names)
            ],
        )
        test = TestModel(
            model_id="M",
            source_file=Path("fixture.mo"),
            source_package="Fixture",
            short_name="M",
            n_vars=3,
            metric_tree_spec=parse_metric_tree(
                {
                    "combinator": "and",
                    "children": [
                        {"metric": "nrmse", "variable": names[i], "tolerance": 0.5}
                        for i in (2, 0, 1)
                    ],
                }
            ),
        )
        comp = compare_test(test, result, decode_reference(ref))
        plots, extra = plot_inputs(test, comp.variables, result, None, None, ())
        html = generate_comparison_plots(
            "M",
            decode_reference(ref),
            result,
            comp.variables,
            tmp_path / "p",
            test_model=test,
            metric_tree=comp.metric_tree,
            plots=plots,
            extra_series=extra,
        )
        page = html.read_text(encoding="utf-8")
        assert _page_data(page, "PLOT_SERIES") == [names]
        (fig,) = _page_data(page, "PLOT_FIGURES")
        acts = [t["meta"]["id"] for t in fig["data"] if t["meta"]["kind"] == "act"]
        assert acts == [f"actual/{n}" for n in names]
        section = _parse(page).by_id("variable-section-0")
        (h2,) = [el for el in section.walk() if el.tag == "h2"]
        # A badge per member, in the members' order (pipe.T[3] is the
        # tree's first variable, so its index is 0).
        assert [el.attrs["data-vidx"] for el in h2.by_class("var-status")] == [
            "1",
            "2",
            "0",
        ]
        # Each names its member, as the tool's family pills do: three bare
        # "PASS" badges side by side said nothing about which is which.
        assert [" ".join(el.text().split()) for el in h2.by_class("var-status")] == [
            f"{n} PASS" for n in names
        ]
        assert h2.text().split()[-1] == "pipe.T[1:3]"
        assert 'class="error-overlay-select"' not in page
        assert "Overlays and error traces show" not in page
        assert [r[1] for r in self._rows(page, 0)] == [names[i] for i in (2, 0, 1)]

    def test_a_plot_the_caller_resolved_is_drawn_as_given(self, tmp_path):
        """`plot_page_keys` draws the plots it is handed — a whole tree at
        the top, whatever the sections below hold."""
        from dstf.discovery.plots import ResolvedPlot

        plots = (ResolvedPlot(index=0, subject=("x",)),)
        page, _ = self._render(tmp_path, plots=plots)
        assert _page_data(page, "PLOT_SERIES") == [["x"]]
        assert 'id="nodes-full"' in page  # the whole tree, y's leaf included
        assert 'id="nodes-1"' not in page

    def test_the_figures_stay_uneditable(self, tmp_path):
        page, _ = self._render(
            tmp_path, extras=[{"var": "x", "context": ["y"]}], extra_vars=()
        )
        for fig in _page_data(page, "PLOT_FIGURES"):
            assert "edits" not in fig["config"]

    def test_a_series_in_no_source_is_named_missing_not_drawn(self, tmp_path):
        """A caller that resolved the plots without a name list (or a result
        file that lost the variable between the list and the read)."""
        from dstf.discovery.plots import ResolvedPlot

        plots = (ResolvedPlot(index=0, subject=("x",), context=("ghost",)),)
        page, sidecar = self._render(tmp_path, plots=plots)
        assert _page_data(page, "PLOT_SERIES") == [["x"]]
        assert "Not in this result: ghost" in page
        assert sidecar["plots"][0]["missing"] == ["ghost"]
        assert sidecar["plots"][0]["context"] == ["ghost"]

    def test_a_no_baseline_test_draws_one_plot_per_tracked_variable(self, tmp_path):
        """Nothing scored: one plot per tracked variable, as the tool draws
        (O2), from the no-baseline trajectories. The "Simulated Variables
        (No Baseline)" block that drew them before is gone — a test's
        curves are drawn once, by its plots."""
        page, sidecar = self._render_no_baseline(tmp_path)
        ids = re.findall(r'id="((?:variable-section|plot|nb-plot)-\d+)"', page)
        assert ids == ["variable-section-0", "plot-0", "variable-section-1", "plot-1"]
        assert "No Baseline" not in page and "NB_FIGURES" not in page
        assert [p["subject"] for p in sidecar["plots"]] == [["x"], ["y"]]
        assert "Not in this result" not in page
        (fig, _) = _page_data(page, "PLOT_FIGURES")
        (trace,) = [tr for tr in fig["data"] if tr["meta"]["kind"] == "act"]
        assert len(trace["y"]) == 51 and len(trace["x"]) == 51
        # Nothing scores it, and it is not a watch plot: no criteria line.
        assert "No criteria yet." not in page
        assert 'class="error-overlay-select"' not in page

    def test_a_failed_simulations_partial_result_is_not_a_layout(self, tmp_path):
        """`success=False` with variables in it: whatever the backend wrote
        before it stopped. It is not the test's plots — the page drew a
        section per half-written variable (the final review)."""
        page, sidecar = self._render_no_baseline(tmp_path, success=False)
        ids = re.findall(r'id="((?:variable-section|plot|nb-plot)-\d+)"', page)
        assert ids == []
        assert sidecar["plots"] == []

    def test_a_no_baseline_tests_extras_decorate_its_plots(self, tmp_path):
        page, sidecar = self._render_no_baseline(
            tmp_path, layout=[{"var": "x", "context": ["y"]}]
        )
        assert [(p["subject"], p["context"]) for p in sidecar["plots"]] == [
            (["x"], ["y"]),
            (["y"], []),
        ]
        (fig, _) = _page_data(page, "PLOT_FIGURES")
        assert [tr["meta"]["kind"] for tr in fig["data"]].count("context") == 1

    def test_sections_are_numbered_by_position_not_by_the_plots_index(self, tmp_path):
        """``report.js`` paints ``PLOT_FIGURES[idx]`` into ``plot-${idx}``,
        so a section numbered by anything but its position renders blank."""
        from dstf.discovery.plots import ResolvedPlot

        plots = (ResolvedPlot(index=3, subject=("x",)),)
        page, _ = self._render(tmp_path, plots=plots)
        assert 'id="plot-0"' in page and 'id="plot-3"' not in page
        assert '<select class="error-overlay-select" data-plot="0">' in page
        assert 'id="nodes-0"' in page

    #: A leaf on ``gone``, which neither the reference nor the result has,
    #: under a k-of-n that x satisfies.
    GONE = {
        "combinator": "and",
        "children": [
            {"metric": "nrmse", "variable": "y", "tolerance": 0.5},
            {
                "combinator": "k-of-n",
                "k": 1,
                "children": [
                    {"metric": "nrmse", "variable": "gone", "tolerance": 0.5},
                    {"metric": "nrmse", "variable": "x", "tolerance": 0.5},
                ],
            },
        ],
    }

    def test_a_tree_leaf_the_result_lacks_is_named_missing(self, tmp_path):
        """The tree names ``gone`` and the result does not carry it: its plot
        says so, as the tool's card does, and draws nothing — no empty
        chart with an error select over a variable that is not there. Its
        verdict still shows: the leaf failed."""
        page, sidecar = self._render(tmp_path, metrics=self.GONE)
        assert [p["subject"] for p in sidecar["plots"]] == [["y"], ["gone"], ["x"]]
        assert sidecar["plots"][1]["missing"] == ["gone"]
        assert sidecar["plots"][1]["series"] == []
        assert _page_data(page, "PLOT_SERIES")[1] == []
        section = _parse(page).by_id("variable-section-1")
        notes = [" ".join(el.text().split()) for el in section.by_class("plot-note")]
        assert notes == ["Not in this result: gone"]
        assert [el.text().strip() for el in section.by_class("var-status")] == ["FAIL"]
        # No empty chart: the heading, the badge, the note and the row.
        assert section.by_class("plotly-chart") == []
        assert len(section.by_class("criteria-row")) == 1
        assert _parse(page).by_id("variable-section-0").by_class("plotly-chart")
        assert '<select class="error-overlay-select" data-plot="1">' not in page
        # The plots the result has keep theirs.
        assert '<select class="error-overlay-select" data-plot="0">' in page
        assert '<select class="error-overlay-select" data-plot="2">' in page

    def test_a_section_with_no_chart_embeds_no_figure(self, tmp_path):
        """No drawable SUBJECT, no chart — whatever the context: ``gone``'s
        section draws ``z`` as context and still has no chart, so the page
        carries no figure for it (a null holds its place, since
        ``PLOT_FIGURES[idx]`` paints ``plot-${idx}``)."""
        page, _ = self._render(
            tmp_path,
            metrics=self.GONE,
            extras=[{"var": "gone", "context": ["z"]}],
            extra_vars=("z",),
        )
        section = _parse(page).by_id("variable-section-1")
        assert section.by_class("plotly-chart") == []
        figures = _page_data(page, "PLOT_FIGURES")
        assert len(figures) == 3
        assert figures[1] is None
        assert figures[0] is not None and figures[2] is not None

    def test_a_statistics_leaf_is_no_criterion_of_its_variables_plot(self, tmp_path):
        """A stored-scalar leaf's ``variable`` is free text naming the leaf,
        not a series (`names_a_series`): x's plot lists its nrmse leaf and
        not the leaf that happens to spell x — as the tool's card does."""
        tree = {
            "combinator": "and",
            "children": [
                *self.XY["children"],
                {
                    "metric": "scalar",
                    "variable": "x",
                    "quantity": "CPUtime.final",
                    "max_value": 1e9,
                },
            ],
        }
        page, _ = self._render(tmp_path, metrics=tree)
        assert self._rows(page, 0) == [("and ›", "x", "/metrics/children/0")]
        full = _parse(page).by_id("nodes-full")
        assert len([el for el in full.walk() if "node-leaf" in el.classes]) == 3

    def test_a_plots_title_is_its_subject_never_its_context(self, tmp_path):
        page, _ = self._render(
            tmp_path,
            metrics=self.XY,
            extras=[{"var": "x", "context": ["z"]}],
            extra_vars=("z",),
        )
        section = _parse(page).by_id("variable-section-0")
        (h2,) = [el for el in section.walk() if el.tag == "h2"]
        assert h2.text().split()[-1] == "x"
        assert "z" not in h2.text().split()

    def test_a_one_subject_plot_with_context_names_its_traces_in_the_banner(
        self, tmp_path
    ):
        page, _ = self._render(
            tmp_path,
            metrics=self.XY,
            extras=[{"var": "x", "context": ["z"]}],
            extra_vars=("z",),
            max_embedded_samples=10,
        )
        (banner,) = re.findall(r'<div id="decimation-banner">(.*?)</div>', page, re.S)
        rows = [
            " ".join(r.split())
            for r in re.findall(r'<span class="dec-trace">([^<]*)</span>', banner)
        ]
        assert any(r.startswith("x Actual ") for r in rows), rows
        assert any(r.startswith("x z · context ") for r in rows), rows

    def test_a_caller_gives_the_plots_or_the_test_they_derive_from(self, tmp_path):
        """With neither, the page would draw no plot at all and say
        nothing: a caller must give one."""
        from dstf.reporting.report_suite import generate_comparison_plots

        with pytest.raises(ValueError, match="plots"):
            generate_comparison_plots("M", None, None, [], tmp_path / "p")

    def test_a_failed_run_has_no_plots_whatever_its_tree_and_extras(self):
        """O2, literally: a run that failed has no plots on either tier —
        not its tree's, not its extras' (the tool's twin is on the Test
        page)."""
        import numpy as np

        from dstf.backends.types import TestResult, VariableResult
        from dstf.comparison.tree_spec import parse_metric_tree
        from dstf.discovery.plots import parse_plots
        from dstf.discovery.test_registry import TestModel
        from dstf.reporting.report_suite import plot_inputs

        t = np.linspace(0.0, 1.0, 5)
        result = TestResult(
            model_id="M",
            success=False,
            variables=[VariableResult(index=1, name="x", time=t, values=t)],
        )
        test = TestModel(
            model_id="M",
            source_file=Path("fixture.mo"),
            source_package="Fixture",
            short_name="M",
            n_vars=2,
            metric_tree_spec=parse_metric_tree(self.XY),
            plots=parse_plots([{"var": "x", "context": ["z"]}, {"var": "w"}]),
        )
        assert plot_inputs(test, [], result, None, None, ()) == ((), {})


class TestReviewPlotsPath:
    """``cli/review.py``'s per-test plot goes through the same helper."""

    def test_review_resolves_the_plots_it_hands_the_renderer(
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
            {"var": "x", "context": ["z"]}
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
        assert [(p.subject, p.context) for p in seen["plots"]] == [
            (("x",), ("z",)),
            (("y",), ()),
        ]
        assert list(seen["extra_series"]) == ["z"]
        # The sidecar's verdict comes from the comparison itself.
        assert seen["comparison"] is s.comparisons[A]


def test_a_context_series_the_result_carries_is_read_from_memory(tmp_path):
    """An explicit tree scoring only x, a result tracking x and y, x's plot
    carrying y as context: y has no plot of its own (nothing scores it),
    and its curve rides in ``extra_series`` from the result in memory —
    never from the file."""
    from types import SimpleNamespace

    import numpy as np

    from dstf.backends.types import TestResult, VariableResult
    from dstf.comparison.tree_spec import parse_metric_tree
    from dstf.discovery.plots import parse_plots
    from dstf.discovery.test_registry import TestModel
    from dstf.reporting.report_suite import plot_inputs

    t = np.linspace(0, 1, 3)

    class Files:
        @classmethod
        def result_names(cls, test_dir):
            return ["x", "y"]

        @classmethod
        def result_series(cls, test_dir, names):
            assert names == []  # y is in memory: nothing is read from the file
            return {}

    test = TestModel(
        model_id="M",
        source_file=Path("fixture.mo"),
        source_package="Fixture",
        short_name="M",
        n_vars=2,
        metric_tree_spec=parse_metric_tree({"metric": "nrmse", "variable": "x"}),
        plots=parse_plots([{"var": "x", "context": ["y"]}]),
    )
    # `plot_inputs` reads what was compared by name, nothing else.
    scored = [SimpleNamespace(name="x")]
    result = TestResult(
        model_id="M",
        success=True,
        variables=[
            VariableResult(name="x", index=1, time=t, values=t),
            VariableResult(name="y", index=2, time=t, values=2 * t),
        ],
    )
    plots, extra = plot_inputs(test, scored, result, Files, tmp_path, ())
    assert [(p.subject, p.context, p.missing) for p in plots] == [(("x",), ("y",), ())]
    assert list(extra) == ["y"]
    assert extra["y"][1].tolist() == (2 * t).tolist()


def test_unit_tests_elements_the_result_lacks_never_fold():
    """A tree naming two UnitTests elements the result does not carry (a
    NO_REF test: nothing compared) still draws one plot per element (O3):
    which names are elements is asked of every leaf, not only of the
    tracked names."""
    import numpy as np

    from dstf.backends.types import TestResult, VariableResult
    from dstf.comparison.tree_spec import parse_metric_tree
    from dstf.discovery.test_registry import TestModel
    from dstf.reporting.report_suite import plot_inputs

    t = np.linspace(0.0, 1.0, 5)
    result = TestResult(
        model_id="M",
        success=True,
        variables=[VariableResult(index=1, name="unitTests.x[1]", time=t, values=t)],
    )
    test = TestModel(
        model_id="M",
        source_file=Path("fixture.mo"),
        source_package="Fixture",
        short_name="M",
        n_vars=6,
        metric_tree_spec=parse_metric_tree(
            {
                "combinator": "and",
                "children": [
                    {"metric": "nrmse", "variable": "unitTests.x[5]"},
                    {"metric": "nrmse", "variable": "unitTests.x[6]"},
                ],
            }
        ),
    )
    plots, _ = plot_inputs(test, [], result, None, None, ())
    assert [p.subject for p in plots] == [("unitTests.x[5]",), ("unitTests.x[6]",)]


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

    def test_a_context_naming_an_untracked_variable_reads_it_from_the_file(
        self, tmp_path
    ):
        args = self._args(tmp_path, [{"var": "x", "context": ["z", "gone"]}])
        plot = args["plots"][0]
        assert plot.subject == ("x",) and plot.context == ("z", "gone")
        assert plot.missing == ("gone",)
        assert list(args["extra_series"]) == ["z"]
        assert args["extra_series"]["z"][1].tolist() == [7.0] * 5

    def test_without_extras_the_result_file_is_not_opened(self, tmp_path, monkeypatch):
        from dstf.backends.python.backend import PythonBackend

        def boom(cls, *a):
            raise AssertionError("derived plots must not touch the result file")

        monkeypatch.setattr(PythonBackend, "result_names", classmethod(boom))
        monkeypatch.setattr(PythonBackend, "result_series", classmethod(boom))
        args = self._args(tmp_path, None)
        # Derived without a name list: one plot per scored variable, every
        # series answered from the result in memory.
        assert [p.subject for p in args["plots"]] == [("x",), ("y",)]
        assert all(p.missing == () for p in args["plots"])
        assert args["extra_series"] == {}

    def test_with_no_backend_class_the_extras_are_resolved_without_a_name_list(
        self, tmp_path
    ):
        """M5.9 — the backend class is resolved ONCE per suite, wrapped; an
        unregistered backend leaves it ``None``, and every page then draws
        its plots instead of raising. With no file to list, what the result
        carries is what the plots can draw: a name it lacks is missing."""
        args = self._args(
            tmp_path, [{"var": "x", "context": ["gone"]}], backend_cls=None
        )
        plot = args["plots"][0]
        assert plot.context == ("gone",) and plot.missing == ("gone",)
        assert args["extra_series"] == {}


class TestBothTiersDrawTheSamePlots:
    """The owner's rulings: a test's plots are derived from its criteria
    tree (D140), one list, and both the authoring tool and the report draw
    it — the same subjects, context and watch plots, in the same order.
    Nothing scored is one plot per tracked variable on BOTH tiers (O2)."""

    @staticmethod
    def _tool(session, model):
        return [(p.subject, p.context, p.watch) for p in session.plots(model)]

    @staticmethod
    def _report_sidecar(session, model, out):
        """The sidecar of the page the SUITE writes for ``model`` — the
        production path from `_build_per_test_args` on, the result file
        read through the project's backend."""
        from dstf.backends.base import last_test_keys
        from dstf.reporting.report_suite import _build_per_test_args, _render_one_test

        project = session.project
        keys = last_test_keys(project.config.work_dir)
        args = _build_per_test_args(
            session.comparisons[model],
            project.results,
            {t.model_id: t for t in project.tests},
            project.store,
            project.config,
            {model: {"test_key": keys.get(model), "last_run_at": None}},
            backend_cls=type(project.backend),
        )
        entry = _render_one_test(args, out)
        return json.loads(
            (
                out / Path(entry["report_path"]).parent / "comparison_data.json"
            ).read_text(encoding="utf-8")
        )

    def _report(self, session, model, out):
        return [
            (tuple(p["subject"]), tuple(p["context"]), p["watch"])
            for p in self._report_sidecar(session, model, out)["plots"]
        ]

    @staticmethod
    def _derived(*names):
        return [((n,), (), False) for n in names]

    @staticmethod
    def _session(cfg, work):
        from dstf.authoring.session import Session

        s = Session()
        s.open(cfg, work_dir=work)
        return s

    def test_a_tracked_variable_no_criterion_scores_has_no_plot_on_either_tier(
        self, tmp_path
    ):
        """A tracks x and y and its tree scores x alone: one plot. y is a
        variable the Variables drawer offers (context, or a watch plot)."""
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
        assert self._tool(s, A) == self._derived("x")
        assert self._report(s, A, tmp_path / "r") == self._derived("x")

    def test_every_fixture_model_draws_the_same_plots_on_both_tiers(self, tmp_path):
        from test_session import A, B, D, G, _two_var_project

        cfg, work = _two_var_project(tmp_path)
        s = self._session(cfg, work)
        for model in (A, B, D, G):
            assert self._tool(s, model) == self._report(
                s, model, tmp_path / "r" / model
            ), model
        # B has no baseline, so nothing is scored: one plot per tracked
        # variable, on both tiers (O2).
        assert self._tool(s, B) == self._derived("x", "y")

    def test_a_tree_and_watch_plots_draw_the_same_on_both_tiers(self, tmp_path):
        """`_page_project`'s A: an explicit tree scoring x twice and y once,
        and five watch plots over variables only the result file has."""
        from test_session import PAGE_WATCH, A, _page_project

        cfg, work = _page_project(tmp_path)
        s = self._session(cfg, work)
        expected = self._derived("x", "y") + [((w,), (), True) for w in PAGE_WATCH]
        assert self._tool(s, A) == expected
        assert self._report(s, A, tmp_path / "r") == expected

    def test_a_missing_context_name_is_in_the_label_scope_on_both_tiers(self, tmp_path):
        """A context name the result lacks is still a series of the test's
        plots, so it is in the label scope of BOTH tiers: `z`, missing,
        still makes the variable whose expression reads `z` read its name
        beside it. The report left missing names out of its scope, and the
        same variable read `z` there and `z (unitTests.x[1])` in the tool."""
        from test_session import UT1, A, _awkward_expression_project, with_expressions

        cfg, work = _awkward_expression_project(tmp_path)
        spec_path = cfg.parent / "test_spec.json"
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        next(e for e in spec["tests"] if e["model"] == A)["plots"] = [
            {"var": "y", "context": ["z"]}
        ]
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        s = self._session(cfg, work)
        with_expressions(s, A, {UT1: "z"})
        tool = s.plots(A)
        sidecar = self._report_sidecar(s, A, tmp_path / "r")
        assert [p.missing for p in tool] == [(), ("z",)]
        assert [p["missing"] for p in sidecar["plots"]] == [[], ["z"]]
        assert s.labels(A)[UT1] == f"z ({UT1})"
        assert sidecar["variable_texts"][UT1]["text"] == f"z ({UT1})"
        assert {
            name: {"label": t.label, "text": t.text}
            for name, t in s.variable_texts(A).items()
        } == sidecar["variable_texts"]

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
        assert self._tool(s, A) == self._derived(UT1, "y")
        assert self._report(s, A, tmp_path / "r") == self._derived(UT1, "y")

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
        assert self._tool(s, A) == self._derived(UT1, "y")
        assert self._report(s, A, tmp_path / "r") == self._derived(UT1, "y")
        view = s.view(A, UT1)
        assert len(view.act_values) == 5

    def test_an_awkward_expression_on_a_watch_plot_draws_its_trace(self, tmp_path):
        """A variable no leaf scores, made a watch plot, rides in
        ``extra_series`` from the result in memory — read there under the
        same name the page plots it by. A second spelling matched none of
        the tracked names, so the section drew nothing under its own title
        (the final review)."""
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
                entry["plots"] = [{"var": UT1}]
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        s = self._session(cfg, work)
        with_expressions(s, A, {UT1: AWKWARD_EXPRESSION})
        project = s.project
        comp = s.comparisons[A]
        result = project.results.get(A)
        plots, extra = plot_inputs(
            project.test(A), comp.variables, result, type(project.backend), None, ()
        )
        assert [(p.subject, p.watch) for p in plots] == [
            (("y",), False),
            ((UT1,), True),
        ]
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
        on the trajectory — on both tiers."""
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
        assert self._tool(s, B) == self._derived(UT1, "y")
        assert self._report(s, B, tmp_path / "r") == self._derived(UT1, "y")
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
        trajectories, _diag, nobaseline = build_trajectories(
            comparisons, result, decode_reference(ref)
        )
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


def test_the_drawn_reference_is_paired_by_name_not_by_index():
    """A baseline accepted before the pattern-order change stored the spec's
    `["b", "a"]` sorted — a at index 1, b at 2 — and the run now resolves
    them in pattern order, b at 1. Scoring pairs by name; the DRAWN
    reference (and the error overlays taken from it) paired by index, so b
    drew a's reference. Both pair by name."""
    import numpy as np

    from conftest import make_test_model
    from dstf.backends.types import (
        TestResult,
        VariableResult,
        resolve_variable_patterns,
    )
    from dstf.comparison.comparator import compare_test
    from dstf.reporting.context_builder import build_trajectories

    t = np.linspace(0, 1, 11)
    vals = {"a": np.full_like(t, 1.0), "b": np.full_like(t, 100.0)}
    reference = {
        "test_id": 1,
        "time": t.tolist(),
        "variables": [
            {"index": 1, "name": "a", "expression": "", "values": vals["a"].tolist()},
            {"index": 2, "name": "b", "expression": "", "values": vals["b"].tolist()},
        ],
    }
    test = make_test_model("P.M", variable_patterns=["b", "a"])
    order = resolve_variable_patterns(test.variable_patterns, ["a", "b"])
    assert order == ["b", "a"]
    result = TestResult(
        model_id="P.M",
        success=True,
        variables=[
            VariableResult(index=i + 1, time=t, values=vals[n], name=n)
            for i, n in enumerate(order)
        ],
    )
    comp = compare_test(test, result, decode_reference(reference))
    assert comp.passed
    trajectories, _diag, _nb = build_trajectories(
        list(comp.variables), result, decode_reference(reference)
    )
    by = {tr["name"]: tr for tr in trajectories}
    assert by["b"]["ref_values"] == vals["b"].tolist()
    assert by["a"]["ref_values"] == vals["a"].tolist()


def test_a_name_stored_twice_draws_its_first_entry():
    """Duplicate names in a stored reference: scoring reads the FIRST entry
    (the comparator); the drawn reference is the same one."""
    import numpy as np

    from conftest import make_test_model
    from dstf.backends.types import TestResult, VariableResult
    from dstf.comparison.comparator import compare_test
    from dstf.reporting.context_builder import build_trajectories

    t = np.linspace(0, 1, 11)
    reference = {
        "test_id": 1,
        "time": t.tolist(),
        "variables": [
            {"index": 1, "name": "a", "expression": "", "values": [1.0] * 11},
            {"index": 2, "name": "a", "expression": "", "values": [9.0] * 11},
        ],
    }
    test = make_test_model("P.M", variable_patterns=["a"])
    result = TestResult(
        model_id="P.M",
        success=True,
        variables=[VariableResult(index=1, time=t, values=np.ones(11), name="a")],
    )
    comp = compare_test(test, result, decode_reference(reference))
    assert comp.passed  # scored against the first entry
    (traj,), _diag, _nb = build_trajectories(
        list(comp.variables), result, decode_reference(reference)
    )
    assert traj["ref_values"] == [1.0] * 11


class TestAVariableReadsAsItsExpression:
    """The rendered page: the title reads the expression, the muted name
    rides beside it, and every KEY on the page is still the name."""

    N1, N2 = "unitTests.x[1]", "unitTests.x[2]"

    def _render(
        self,
        tmp_path,
        expressions,
        metrics=None,
        name="page",
        plots=None,
        samples=500,
        points=51,
        extra=(),
    ):
        import json
        from pathlib import Path

        import numpy as np

        from dstf.backends.types import TestResult, VariableResult
        from dstf.comparison.comparator import compare_test
        from dstf.comparison.tree_spec import parse_metric_tree
        from dstf.discovery.plots import parse_plots
        from dstf.discovery.test_registry import TestModel
        from dstf.reporting.report_suite import generate_comparison_plots, plot_inputs

        t = np.linspace(0.0, 1.0, points)
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
        # ``plots`` is the entry's extras (context series, watch plots);
        # ``extra`` names variables only the result FILE holds, handed in
        # as the backend would read them. Both are part of the page's label
        # scope.
        if plots is not None:
            test.plots = parse_plots(plots)
        comp = compare_test(test, result, decode_reference(ref))

        # Through the production producer, not a hand-rolled layout: the
        # page's plots are derived from the tree, and `plot_inputs` also
        # hands back the arrays of the series no scored entry carries.
        class Files:
            """The result FILE: the result's variables and ``extra``."""

            @classmethod
            def result_names(cls, test_dir):
                return [*names, *extra]

            @classmethod
            def result_series(cls, test_dir, wanted):
                return {n: (t, np.cos(t)) for n in wanted if n in extra}

        resolved, extra_series = plot_inputs(
            test, comp.variables, result, Files, tmp_path, ()
        )
        html = generate_comparison_plots(
            "M",
            decode_reference(ref),
            result,
            comp.variables,
            tmp_path / name,
            test_model=test,
            metric_tree=comp.metric_tree,
            max_embedded_samples=samples,
            plots=resolved,
            extra_series=extra_series,
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
        assert actual["meta"]["label"] == "pipe.T[1]"

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

    def _render_no_baseline(self, tmp_path, expressions, name="nobase"):
        """A test with NO reference: nothing is scored, so every tracked
        curve lives in ``nobaseline_trajectories`` and is drawn by its own
        plot — one per tracked variable (O2)."""
        import json
        from pathlib import Path

        import numpy as np

        from dstf.backends.types import TestResult, VariableResult
        from dstf.discovery.test_registry import TestModel
        from dstf.reporting.report_suite import generate_comparison_plots, plot_inputs

        t = np.linspace(0.0, 1.0, 51)
        names = list(expressions)
        result = TestResult(
            model_id="M",
            success=True,
            variables=[
                VariableResult(
                    index=i + 1,
                    name=n,
                    time=t,
                    values=np.sin(t) * (i + 1),
                    expression=expressions[n],
                )
                for i, n in enumerate(names)
            ],
        )
        test = TestModel(
            model_id="M",
            source_file=Path("fixture.mo"),
            source_package="Fixture",
            short_name="M",
            n_vars=len(names),
            source="unit_tests",
            x_expressions=[expressions[n] for n in names],
        )
        plots, extra = plot_inputs(test, [], result, None, None, ())
        html = generate_comparison_plots(
            "M",
            None,
            result,
            [],
            tmp_path / name,
            test_model=test,
            plots=plots,
            extra_series=extra,
        )
        return html.read_text(encoding="utf-8"), json.loads(
            (tmp_path / name / "comparison_data.json").read_text(encoding="utf-8")
        )

    def test_a_no_baseline_section_reads_the_label_and_shows_the_name(self, tmp_path):
        """A test with no reference names its variables in its plots'
        headings. Rendered from the name alone it said `unitTests.x[1]`,
        with nothing telling the reader which quantity that is."""
        page, _sidecar = self._render_no_baseline(tmp_path, {self.N1: "pipe.T[1]"})
        heading = next(
            el
            for el in _parse(page).by_id("variable-section-0").walk()
            if el.tag == "h2"
        )
        assert "pipe.T[1]" in heading.text()
        (muted,) = heading.by_class("dstf-varname")
        assert muted.text() == self.N1
        figure = _page_data(page, "PLOT_FIGURES")[0]
        actual = next(tr for tr in figure["data"] if tr["meta"]["kind"] == "act")
        assert actual["meta"]["label"] == "pipe.T[1]"

    def test_a_no_baseline_plot_reads_the_tests_scope(self, tmp_path):
        """A test with no reference scores nothing, so its plots are drawn
        from the no-baseline trajectories. Those series read the page's
        TEXTS like every other: two variables sharing `a.Q` gave two
        figures one identical legend entry."""
        page, _sidecar = self._render_no_baseline(
            tmp_path, {self.N1: "a.Q", self.N2: "a.Q"}, name="nb-plot"
        )
        names = [
            tr["meta"]["label"]
            for figure in _page_data(page, "PLOT_FIGURES")
            for tr in figure["data"]
            if tr.get("meta", {}).get("kind") == "act"
        ]
        assert names == [f"a.Q ({self.N1})", f"a.Q ({self.N2})"]

    def test_a_no_baseline_section_with_no_expression_renders_as_it_did(self, tmp_path):
        page, _sidecar = self._render_no_baseline(tmp_path, {"y": ""}, name="nb-plain")
        heading = next(
            el
            for el in _parse(page).by_id("variable-section-0").walk()
            if el.tag == "h2"
        )
        assert heading.text().split()[-1] == "y"
        assert heading.by_class("dstf-varname") == []
        figure = _page_data(page, "PLOT_FIGURES")[0]
        actual = next(tr for tr in figure["data"] if tr["meta"]["kind"] == "act")
        assert "hovertemplate" not in actual

    def test_unique_expressions_read_exactly_as_the_labels(self, tmp_path):
        """The no-regression predicate for the ordinary case — every
        expression of the test distinct, one spec-tracked variable beside
        them. Nothing on this page carries a name in parentheses: the
        scope disambiguates only what is ambiguous, and widening it to the
        whole test must not start spelling names at every reader."""
        page, _sidecar = self._render(
            tmp_path,
            {self.N1: "pipe.T[1]", self.N2: "a.Q", "y": ""},
            name="unique",
            plots=[{"var": self.N1, "context": [self.N2, "y"]}],
        )
        heading = next(
            el
            for el in _parse(page).by_id("variable-section-0").walk()
            if el.tag == "h2"
        )
        assert [m.text() for m in heading.by_class("dstf-varname")] == [self.N1]
        assert "pipe.T[1]" in heading.text()
        labels = [
            el.text()
            for el in _parse(page).by_id("nodes-full").walk()
            if "node-label" in el.classes
        ]
        assert labels == ["and", "nrmse · pipe.T[1]", "nrmse · a.Q", "nrmse · y"]
        figure = _page_data(page, "PLOT_FIGURES")[0]
        by_id = {tr["meta"]["id"]: tr for tr in figure["data"] if tr.get("meta")}
        assert by_id["actual"]["meta"]["label"] == "pipe.T[1]"
        assert by_id[f"context/{self.N2}"]["name"] == "a.Q · context"
        assert by_id["context/y"]["name"] == "y · context"
        (context,) = _parse(page).by_id("variable-section-0").by_class("plot-context")
        assert " ".join(context.text().split()) == "Context: a.Q, y"
        assert "(unitTests.x[" not in page

    def test_the_scope_is_the_test_not_the_scored_list(self, tmp_path):
        """Case (a) of the b147918 review: two variables share one
        expression and only ONE of them is scored. The scored list holds
        one `a.Q`, so a row disambiguated over it read `nrmse · a.Q` under
        a card titled `a.Q` with `unitTests.x[1]` muted beside — the very
        ambiguity the parentheses exist to remove. The scope is the TEST."""
        page, _sidecar = self._render(
            tmp_path,
            {self.N1: "a.Q", self.N2: "a.Q"},
            metrics={"metric": "nrmse", "variable": self.N1, "tolerance": 0.5},
            name="scoped",
        )
        labels = [
            el.text()
            for el in _parse(page).by_id("nodes-full").walk()
            if "node-label" in el.classes
        ]
        assert labels == [f"nrmse · a.Q ({self.N1})"]

    def test_a_title_beside_a_muted_name_reads_the_plain_label(self, tmp_path):
        """Case (d): the heading already carries the name, muted. Reading
        the disambiguated text there said `a.Q (unitTests.x[1])` with
        `unitTests.x[1]` beside it — the name twice, once in each style."""
        page, _sidecar = self._render(
            tmp_path, {self.N1: "a.Q", self.N2: "a.Q"}, name="plain-title"
        )
        heading = next(
            el
            for el in _parse(page).by_id("variable-section-0").walk()
            if el.tag == "h2"
        )
        (muted,) = heading.by_class("dstf-varname")
        assert muted.text() == self.N1
        assert "a.Q" in heading.text()
        assert f"a.Q ({self.N1})" not in heading.text()

    def test_a_legend_reads_the_tests_scope_not_the_plots(self, tmp_path):
        """Case (b): the plot draws only ONE of the two variables sharing
        `a.Q`, so a legend disambiguated over the PLOT read `a.Q` while
        that variable's criteria row read `a.Q (unitTests.x[1])`."""
        page, _sidecar = self._render(
            tmp_path,
            {self.N1: "a.Q", self.N2: "a.Q"},
            name="plotscope",
            plots=[{"var": self.N1, "context": ["z"]}],
            extra=("z",),
        )
        figure = _page_data(page, "PLOT_FIGURES")[0]
        actual = next(tr for tr in figure["data"] if tr["meta"]["id"] == "actual")
        assert actual["meta"]["label"] == f"a.Q ({self.N1})"

    def test_a_context_variable_named_like_an_expression(self, tmp_path):
        """Case (c): the result file holds a variable genuinely NAMED `z`,
        and a scored one whose EXPRESSION is `z`. The context one keeps its
        plain `z` — its label IS its name, so nothing about it is
        ambiguous — and the one carrying the expression reads its name
        beside it. Both said `z`, on the same plot, one line apart."""
        page, _sidecar = self._render(
            tmp_path,
            {self.N1: "z"},
            name="plotonly",
            plots=[{"var": self.N1, "context": ["z"]}],
            extra=("z",),
        )
        (context,) = _parse(page).by_id("variable-section-0").by_class("plot-context")
        assert " ".join(context.text().split()) == "Context: z"
        labels = [
            el.text()
            for el in _parse(page).by_id("nodes-full").walk()
            if "node-label" in el.classes
        ]
        assert labels == ["and", f"nrmse · z ({self.N1})"]
        figure = _page_data(page, "PLOT_FIGURES")[0]
        by_id = {tr["meta"]["id"]: tr for tr in figure["data"] if tr.get("meta")}
        assert by_id["actual"]["meta"]["label"] == f"z ({self.N1})"
        assert by_id["context/z"]["name"] == "z · context"

    def test_a_tracked_name_and_an_expression_spelling_it_never_fold(self, tmp_path):
        """A spec-tracked `pipe.T[1]` beside a `unitTests.x[1]` whose
        EXPRESSION is `pipe.T[1]`: a UnitTests element folds with nothing
        (O3), so they are two plots — never one named `pipe.T[1,1]` or
        `pipe.T[1:2]`, a third thing neither of them is."""
        page, _sidecar = self._render(
            tmp_path,
            {"pipe.T[1]": "", self.N1: "pipe.T[1]"},
            name="fold",
            samples=8,
            points=201,
        )
        assert _page_data(page, "PLOT_SERIES") == [["pipe.T[1]"], [self.N1]]
        assert "pipe.T[1,1]" not in page and "pipe.T[1:2]" not in page
        banner = _parse(page).by_id("decimation-banner")
        traces = [el.text() for el in banner.by_class("dec-trace")]
        assert any(t.startswith("pipe.T[1] ") for t in traces), traces
        assert any(t.startswith(f"pipe.T[1] ({self.N1}) ") for t in traces), traces

    def test_the_decimation_banner_names_a_plot_by_its_subject(self, tmp_path):
        """The banner names a trace's PLOT the way its heading does — by the
        subject — and the trace by its own name: a context trace is not a
        second subject, and no result-file name is spelled where the page
        reads expressions."""
        page, _sidecar = self._render(
            tmp_path,
            {self.N1: "pipe.T[1]", self.N2: "a.Q"},
            name="banner",
            plots=[{"var": self.N1, "context": [self.N2]}],
            samples=8,
            points=201,
        )
        banner = _parse(page).by_id("decimation-banner")
        traces = [el.text() for el in banner.by_class("dec-trace")]
        assert any(t.startswith("pipe.T[1] Actual ") for t in traces), traces
        assert any(t.startswith("pipe.T[1] a.Q · context ") for t in traces), traces
        assert not any(self.N1 in t for t in traces)

    def test_a_watch_plot_reads_the_tests_scope(self, tmp_path):
        """A variable the result tracks and the tree does not score reads
        the same text as every other variable of its test — here on the
        watch plot it was given.

        Its heading, its hover and the decimation banner all said the bare
        `unitTests.x[2]` while the tool, over the same result, said
        `a.Q (unitTests.x[2])`, until the whole test's scope was stamped on
        the context.
        """
        n3 = "unitTests.x[3]"
        page, _sidecar = self._render(
            tmp_path,
            {self.N1: "a.Q", self.N2: "a.Q", n3: "b.Q"},
            metrics={
                "combinator": "and",
                "children": [
                    {"metric": "nrmse", "variable": self.N1, "tolerance": 0.5},
                    {"metric": "nrmse", "variable": n3, "tolerance": 0.5},
                ],
            },
            name="unscored",
            plots=[{"var": self.N2}],
            samples=8,
            points=201,
        )
        parsed = _parse(page)
        # Its section: the watch plot, after the two the tree derives.
        section = parsed.by_id("variable-section-2")
        heading = next(el for el in section.walk() if el.tag == "h2")
        assert "a.Q" in heading.text()
        (muted,) = heading.by_class("dstf-varname")
        assert muted.text() == self.N2
        assert [el.text() for el in section.by_class("plot-note")] == [
            "No criteria yet."
        ]
        figure = _page_data(page, "PLOT_FIGURES")[2]
        actual = next(tr for tr in figure["data"] if tr["meta"]["kind"] == "act")
        assert actual["meta"]["label"] == f"a.Q ({self.N2})"
        traces = [
            el.text() for el in parsed.by_id("decimation-banner").by_class("dec-trace")
        ]
        assert any(t.startswith(f"a.Q ({self.N2}) ") for t in traces), traces

    def test_the_banner_tells_two_plots_of_one_expression_apart(self, tmp_path):
        """The banner shows no muted name, so it reads the TEXTS: two
        single-series plots of two variables sharing `a.Q` were two rows
        spelled identically, naming neither curve."""
        page, _sidecar = self._render(
            tmp_path,
            {self.N1: "a.Q", self.N2: "a.Q"},
            name="banner-shared",
            samples=8,
            points=201,
        )
        banner = _parse(page).by_id("decimation-banner")
        traces = [el.text() for el in banner.by_class("dec-trace")]
        assert any(t.startswith(f"a.Q ({self.N1}) ") for t in traces)
        assert any(t.startswith(f"a.Q ({self.N2}) ") for t in traces)

    def test_two_variables_sharing_one_expression_are_tellable_apart(self, tmp_path):
        """`x={a.Q, a.Q}` is legal: two result-file variables, one
        expression. Read as the label alone, both criteria rows said
        `nrmse · a.Q` with nothing saying which variable each scored."""
        page, _sidecar = self._render(
            tmp_path, {self.N1: "a.Q", self.N2: "a.Q"}, name="shared"
        )
        labels = [
            el.text()
            for el in _parse(page).by_id("nodes-full").walk()
            if "node-label" in el.classes
        ]
        assert labels == [
            "and",
            f"nrmse · a.Q ({self.N1})",
            f"nrmse · a.Q ({self.N2})",
        ]
        # The keys are untouched.
        assert _page_data(page, "PLOT_SERIES") == [[self.N1], [self.N2]]


class TestTheStatisticsTable:
    """The report's Statistics table and Key stats render the one row model:
    a row is tinted when the criterion its `+` would add would fail; its
    order and sections are the report's as before, only the labels are the
    row model's."""

    # Built fresh for every call: a builder that wrote into its input would
    # otherwise hide it from the next test.
    @property
    def RUN(self):
        return {
            "translation": {"nonlinear": [1, 1, 1], "nonlinear_count": 3},
            "simulation": {"state_events": 2},
            "timing": {"total_wall": 4.02},
            "CPUtime": 1.12,
        }

    @property
    def BASE(self):
        return {
            "translation": {"nonlinear": []},
            "simulation": {"state_events": 2},
            "timing": {"total_wall": 3.91},
            "CPUtime": 0.80,
        }

    def _sections(self):
        from dstf.reporting.context_builder import _build_statistics_sections

        return _build_statistics_sections(self.BASE, self.RUN)

    def _rows(self):
        return {r["label"]: r for s in self._sections() for r in s["rows"]}

    def test_a_time_3_percent_off_is_not_tinted_and_a_count_change_is(self):
        rows = self._rows()
        assert not rows["Total wall"]["changed"]
        assert rows["Nonlinear count"]["changed"]
        assert rows["CPUtime"]["changed"]
        assert not rows["State events"]["changed"]

    def test_an_empty_list_reads_its_count_as_zero(self):
        row = self._rows()["Nonlinear count"]
        assert (row["current"], row["reference"]) == ("3", "0")

    def test_a_value_reads_in_full_with_its_unit(self):
        """The report keeps full precision — only the tool's card rounds: a
        time as recorded, with its unit, in the table and in Key stats."""
        row = self._rows()["CPUtime"]
        assert (row["current"], row["reference"]) == ("1.12 s", "0.8 s")
        from dstf.reporting.context_builder import _build_key_stats

        stats = {s["label"]: s for s in _build_key_stats([], self.BASE, self.RUN)}
        assert stats["CPU Time"]["reference"] == "0.8 s"

    def test_the_sections_and_their_order_are_the_reports(self):
        assert [
            (s["title"], [r["label"] for r in s["rows"]]) for s in self._sections()
        ] == [
            (
                "Translation Statistics",
                ["Nonlinear", "Nonlinear count", "Nonlinear max", "Nonlinear total"],
            ),
            ("Simulation Statistics", ["CPUtime", "State events"]),
            ("Timing", ["Total wall"]),
        ]

    def test_a_folded_diagnostic_takes_its_kind_from_the_runs_samples(self):
        """A user-configured diagnostic folded into the statistics (`Wall`):
        the report reads the run's samples as the tool's card does —
        fractional, so a time, and 2.4 -> 2.5 is inside its 10%: untinted."""
        import numpy as np

        from dstf.backends.types import TestResult, VariableResult
        from dstf.reporting.context_builder import _build_template_context

        t = np.array([0.0, 1.0, 2.0])
        wall = VariableResult(
            index=0, time=t, values=np.array([0.0, 1.5, 2.5]), name="Wall"
        )
        run = {"Wall": 2.5, "simulation": {"state_events": 1}}
        ref = {"statistics": {"Wall": 2.4, "simulation": {"state_events": 1}}}
        ctx = _build_template_context(
            "M",
            [],
            decode_reference(ref),
            run,
            None,
            result=TestResult("M", True, diagnostics=[wall], statistics=run),
        )
        rows = {r["label"]: r for s in ctx["statistics_sections"] for r in s["rows"]}
        assert not rows["Wall"]["changed"]

    def test_the_inputs_are_never_written(self):
        from dstf.reporting.context_builder import (
            _build_key_stats,
            _build_statistics_sections,
        )

        run, base = self.RUN, self.BASE
        _build_statistics_sections(base, run)
        _build_key_stats([], base, run)
        assert (base, run) == (self.BASE, self.RUN)

    def test_key_stats_read_the_rows(self):
        from dstf.reporting.context_builder import _build_key_stats

        stats = {s["label"]: s for s in _build_key_stats([], self.BASE, self.RUN)}
        nonlinear = stats["Nonlinear Systems"]
        assert (nonlinear["current"], nonlinear["reference"]) == ("3", "0")
        assert nonlinear["changed"]
        assert stats["CPU Time"]["changed"]
        run = {**self.RUN, "CPUtime": 0.82}
        stats = {s["label"]: s for s in _build_key_stats([], self.BASE, run)}
        assert not stats["CPU Time"]["changed"]

    def test_key_stats_a_side_not_recorded_reads_blank(self):
        from dstf.reporting.context_builder import _build_key_stats

        stats = {s["label"]: s for s in _build_key_stats([], {}, {"CPUtime": 1.12})}
        assert stats["CPU Time"]["reference"] == ""
        assert not stats["CPU Time"]["changed"]
