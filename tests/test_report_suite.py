"""Tests for reporting/report_suite.py — the per-test report pipeline.

Strict-JSON artifacts, the embedded-HTML decimation budget and the
pre-decimation ``full_samples`` stamps it leaves behind, the run-provenance
banner, and the CLI-authoritative marking that keeps live JS re-scoring off
the leaves a browser cannot reproduce.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from dstf.comparison.tree_spec import synthesize_implicit_tree
from dstf.comparison.types import VariableComparison
from dstf.reporting.report_suite import (
    _decimate_context_for_html,
    _json_sanitize,
    _mark_decimated_cli_authoritative,
    generate_comparison_plots,
)

# =========================================================================
# Folded in from tests/test_review_fixes_cli.py — regression tests for the
# 2026-07-06 review, final CLI/config/manifest batch (CODE_REVIEW_2026-07-06.md).
#
# Finding 71 — overlays on nobaseline trajectories are decimated under the
# same embedded-HTML budget as everything else.
# =========================================================================


def test_nobaseline_overlays_are_decimated():
    from dstf.reporting.report_suite import _decimate_context_for_html

    big = [float(i) for i in range(5000)]
    context = {
        "nobaseline_trajectories": [
            {
                "name": "x",
                "time": list(big),
                "values": list(big),
                "overlays": [
                    {"name": "sibling", "time": list(big), "values": list(big)}
                ],
            }
        ]
    }
    _decimate_context_for_html(context, 100)
    traj = context["nobaseline_trajectories"][0]
    assert len(traj["time"]) <= 100
    overlay = traj["overlays"][0]
    assert len(overlay["time"]) <= 100
    assert len(overlay["values"]) == len(overlay["time"])


# =========================================================================
# Folded in from tests/test_review_fixes_reporter.py — 2026-07-06 review,
# reporter batch (CODE_REVIEW_2026-07-06.md).
#
# * Finding 68 — ``comparison_data.json`` and the embedded interactive.html
#   context are strict JSON (non-finite floats sanitized to null).
# * Finding 37 (support) — ``_decimate_context_for_html`` stamps
#   pre-decimation ``full_samples`` counts for the JS decimation banner.
# * Finding 76h (support) — the template emits ``VARIABLE_ORDER`` as an
#   explicit JSON array so integer-like variable names can't desync plot IDs.
#
# The JS-side behavior for findings 37-40, 42-47, and 76 is exercised by
# ``tests/test_scorer_parity.py`` (node runner) and the Playwright suite.
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


class TestLeafCountMismatchIsReported:
    """Spec/eval drift drops every per-leaf render artifact from the report.
    Silently producing a stripped-down page hides the cause; warn naming the
    model and both counts."""

    def test_mismatch_warns_naming_model_and_counts(self, caplog):
        import logging
        from types import SimpleNamespace

        from dstf.reporting.context_builder import _build_tree_view_and_variables

        test_model = SimpleNamespace(
            model_id="Lib.Drift",
            metric_tree_spec=synthesize_implicit_tree(["x"]),
            variable_overrides=None,
        )
        with caplog.at_level(logging.WARNING, logger="dstf.reporting.context_builder"):
            _build_tree_view_and_variables(
                [_nonfinite_comparison("x"), _nonfinite_comparison("y")],
                test_model,
                None,
                [],
            )
        messages = [r.getMessage() for r in caplog.records]
        assert any("Lib.Drift" in m and "(1)" in m and "(2)" in m for m in messages), (
            messages
        )

    def test_matching_counts_stay_quiet(self, caplog):
        import logging
        from types import SimpleNamespace

        from dstf.reporting.context_builder import _build_tree_view_and_variables

        test_model = SimpleNamespace(
            model_id="Lib.Ok",
            metric_tree_spec=synthesize_implicit_tree(["x"]),
            variable_overrides=None,
        )
        with caplog.at_level(logging.WARNING, logger="dstf.reporting.context_builder"):
            _build_tree_view_and_variables(
                [_nonfinite_comparison("x")], test_model, None, []
            )
        assert not [r for r in caplog.records if "leaf count" in r.getMessage()]


# ---------------------------------------------------------------------------
# Finding 37 (support) — full_samples counts for the decimation banner
# ---------------------------------------------------------------------------


def _big_context(n: int = 5000) -> dict:
    t = [i / (n - 1) for i in range(n)]
    traj = {
        "index": 0,
        "name": "x",
        "act_time": list(t),
        "act_values": list(t),
        "ref_time": list(t),
        "ref_values": list(t),
    }
    return {
        "trajectories": [traj],
        # Shared object — mirrors _build_tree_view_and_variables, which
        # points variables_by_name at the same trajectory dict.
        "variables_by_name": {"x": {"name": "x", "trajectory": traj}},
        "diag_trajectories": [],
        "nobaseline_trajectories": [],
    }


class TestFullSamplesStamp:
    def test_full_samples_recorded_before_decimation(self):
        ctx = _big_context(5000)
        _decimate_context_for_html(ctx, 1000)
        traj = ctx["variables_by_name"]["x"]["trajectory"]
        assert traj["full_samples"] == {"act": 5000, "ref": 5000}
        assert len(traj["act_time"]) == 1000

    def test_below_threshold_counts_match_embedded(self):
        ctx = _big_context(500)
        _decimate_context_for_html(ctx, 1000)
        traj = ctx["variables_by_name"]["x"]["trajectory"]
        assert traj["full_samples"] == {"act": 500, "ref": 500}
        assert len(traj["act_time"]) == 500  # untouched → banner stays hidden

    @staticmethod
    def _with_overlay(primary_n: int, overlay_n: int, role: str = "soft_check"):
        """Context whose primary and soft_check overlay have INDEPENDENT
        sample counts — the shape the trajectory-level stamp cannot see."""
        ctx = _big_context(primary_n)
        ot = [i / (overlay_n - 1) for i in range(overlay_n)]
        overlay = {
            "name": "openmodelica",
            "role": role,
            "kind": "json",
            "time": list(ot),
            "values": list(ot),
        }
        # Shared list object, exactly like _build_tree_view_and_variables.
        ctx["trajectories"][0]["overlays"] = [overlay]
        ctx["variables_by_name"]["x"]["overlays"] = ctx["trajectories"][0]["overlays"]
        return ctx, overlay

    def test_overlay_full_samples_recorded_before_decimation(self):
        """An overlay is a scorable curve (an against-leaf scores on it), so
        its pre-decimation count must be recorded like the primary's. The
        gap this closes: a variable UNDER the cap carrying a soft_check
        OVER it decimated silently — nothing in the payload said so."""
        ctx, overlay = self._with_overlay(primary_n=500, overlay_n=5000)
        _decimate_context_for_html(ctx, 1000)
        traj = ctx["variables_by_name"]["x"]["trajectory"]
        # Primary is under the cap and untouched...
        assert traj["full_samples"] == {"act": 500, "ref": 500}
        assert len(traj["act_time"]) == 500
        # ...while the overlay was decimated, and says so.
        assert overlay["full_samples"] == 5000
        assert len(overlay["time"]) == 1000
        assert len(overlay["values"]) == 1000

    def test_under_cap_overlay_is_untouched(self):
        ctx, overlay = self._with_overlay(primary_n=500, overlay_n=400)
        _decimate_context_for_html(ctx, 1000)
        assert overlay["full_samples"] == 400
        assert len(overlay["time"]) == 400  # unchanged → banner stays quiet

    def test_overlay_stamp_survives_the_second_decimation_pass(self):
        """``trajectories`` and ``variables_by_name`` share overlay dicts, so
        the decimation walk visits each one twice. The first (pre-decimation)
        stamp must win — a re-stamp on the second pass would record 1000 and
        report the overlay as full-resolution."""
        ctx, overlay = self._with_overlay(primary_n=5000, overlay_n=5000)
        _decimate_context_for_html(ctx, 1000)
        assert overlay["full_samples"] == 5000

    def test_nobaseline_overlays_are_stamped_too(self):
        ctx = _big_context(500)
        n = 5000
        t = [i / (n - 1) for i in range(n)]
        overlay = {
            "name": "rig",
            "role": "companion",
            "time": list(t),
            "values": list(t),
        }
        ctx["nobaseline_trajectories"] = [
            {"name": "y", "time": list(t), "values": list(t), "overlays": [overlay]}
        ]
        _decimate_context_for_html(ctx, 1000)
        assert overlay["full_samples"] == 5000
        assert len(overlay["time"]) == 1000

    def test_unshared_variable_trajectory_also_stamped(self):
        ctx = _big_context(5000)
        # Break the sharing: a distinct full-res copy under variables_by_name.
        n = 5000
        t = [i / (n - 1) for i in range(n)]
        ctx["variables_by_name"]["x"]["trajectory"] = {
            "act_time": list(t),
            "act_values": list(t),
            "ref_time": list(t),
            "ref_values": list(t),
        }
        _decimate_context_for_html(ctx, 1000)
        traj = ctx["variables_by_name"]["x"]["trajectory"]
        assert traj["full_samples"] == {"act": 5000, "ref": 5000}
        assert len(traj["act_time"]) == 1000


# ---------------------------------------------------------------------------
# Dominant-frequency parity — decimated leaves fall back to the CLI verdict
# ---------------------------------------------------------------------------


def _df_context(n: int, *, mode: str = "dominant-frequency") -> dict:
    ctx = _big_context(n)
    ctx["tree_view"] = {
        "kind": "combinator",
        "combinator": "and",
        "path": "/metrics",
        "children": [
            {
                "kind": "leaf",
                "path": "/metrics/children/0",
                "metric": mode,
                "variable": "x",
                "name": "x",
                "mode_effective": mode,
                "cli_authoritative": mode == "event-timing",
            }
        ],
    }
    return ctx


def _leaf_of(ctx: dict) -> dict:
    return ctx["tree_view"]["children"][0]


class TestDecimatedDominantFrequencyIsCliAuthoritative:
    """The live JS FFT runs on the EMBEDDED arrays; the CLI FFTs the full
    trajectory. Their pow-2 bin grids only coincide when nothing was
    decimated, so a decimated dominant-frequency leaf must keep the CLI
    verdict (badge, no live re-score) instead of disagreeing with the pill.
    """

    def test_decimated_leaf_becomes_cli_authoritative(self):
        ctx = _df_context(5000)
        _decimate_context_for_html(ctx, 1000)
        _mark_decimated_cli_authoritative(ctx)
        assert _leaf_of(ctx)["cli_authoritative"] is True

    def test_undecimated_leaf_keeps_live_scoring(self):
        ctx = _df_context(500)
        _decimate_context_for_html(ctx, 1000)
        _mark_decimated_cli_authoritative(ctx)
        assert _leaf_of(ctx)["cli_authoritative"] is False

    def test_other_modes_unaffected_by_decimation(self):
        ctx = _df_context(5000, mode="nrmse")
        _decimate_context_for_html(ctx, 1000)
        _mark_decimated_cli_authoritative(ctx)
        assert _leaf_of(ctx)["cli_authoritative"] is False

    def test_decimation_pins_regardless_of_which_baseline_is_named(self):
        """The against-leaf pin narrowed; this one did not. What diverges
        under decimation is the FFT bin grid, not the reference curve — so
        an against-leaf whose soft_check IS embedded still keeps the CLI
        verdict once its trajectory has been decimated.
        """
        ctx = _df_context(5000)
        _leaf_of(ctx)["against"] = "experiment"
        _leaf_of(ctx)["cli_authoritative"] = False  # as if the overlay is embedded
        _decimate_context_for_html(ctx, 1000)
        _mark_decimated_cli_authoritative(ctx)
        assert _leaf_of(ctx)["cli_authoritative"] is True

    def test_report_pipeline_marks_before_rendering(self, tmp_path, monkeypatch):
        """Wiring: the HTML the reporter renders carries the flag.

        The context builder and the template are stubbed out — this asserts
        only that ``generate_comparison_plots`` decimates and THEN marks,
        before the context reaches the template.
        """
        import dstf.reporting.report_suite as pc

        ctx = _df_context(5000)
        captured = {}
        monkeypatch.setattr(pc, "_build_template_context", lambda *a, **k: ctx)
        monkeypatch.setattr(
            pc,
            "_render_template",
            lambda name, context, path: (
                captured.setdefault("context", context)
                or path.write_text("", encoding="utf-8")
            ),
        )
        generate_comparison_plots(
            model_id="Fixture.DomFreq",
            ref_data=None,
            result=None,
            comparisons=[],
            plot_dir=tmp_path / "report",
            max_embedded_samples=1000,
        )
        leaf = captured["context"]["tree_view"]["children"][0]
        assert leaf["cli_authoritative"] is True

    def test_real_context_builder_end_to_end(self):
        """No stubs: a real dominant-frequency comparison on an over-cap
        trajectory, through the REAL ``_build_template_context`` +
        decimate + mark. Pins the field names / key alignment the
        hand-built contexts above merely mirror (A4 review finding 3) —
        if ``_augment_tree_view``'s emitted shape drifts, this fails
        while the unit tests stay green.
        """
        import numpy as np

        from dstf.backends import TestResult, VariableResult
        from dstf.comparison.algorithms import _compare_dominant_frequency
        from dstf.comparison.metric_tree import leaf_from_variable
        from dstf.comparison.tree_spec import parse_metric_tree
        from dstf.discovery.test_registry import TestModel
        from dstf.reporting.context_builder import _build_template_context

        n = 5000  # over the 1000-sample embed cap
        t = np.linspace(0.0, 1.0, n)
        v = np.sin(2 * np.pi * 5.0 * t)
        peaks = [{"freq": 5.0, "tolerance": 0.1, "tolerance_mode": "rel"}]
        vc = _compare_dominant_frequency(t, v, t, v, peaks=peaks)
        vc.name = "x"
        vc.index = 1
        assert vc.passed  # fixture sanity: the CLI verdict being preserved

        model = TestModel(
            model_id="Fixture.DomFreqE2E",
            source_file=Path("fixture.mo"),
            source_package="Fixture",
            short_name="DomFreqE2E",
            n_vars=1,
            metric_tree_spec=parse_metric_tree(
                {"metric": "dominant-frequency", "variable": "x", "peaks": peaks}
            ),
        )
        result = TestResult(
            model_id=model.model_id,
            success=True,
            variables=[VariableResult(index=1, time=t, values=v)],
        )
        ctx = _build_template_context(
            model_id=model.model_id,
            comparisons=[vc],
            ref_data=None,
            cur_stats=None,
            test_dir=None,
            test_model=model,
            result=result,
            metric_tree=leaf_from_variable(vc),
        )
        _decimate_context_for_html(ctx, 1000)
        _mark_decimated_cli_authoritative(ctx)

        leaf = ctx["tree_view"]
        # Single-leaf spec: the root IS the leaf.
        assert leaf["kind"] == "leaf"
        assert leaf["cli_authoritative"] is True
        # And the CLI verdict survives on the view for the pill to keep.
        assert leaf["passed"] is True


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


class TestAuthoredWindowSeedsTheInputs:
    def test_window_values_mirror_the_authored_window(self):
        """The window input boxes must pre-fill with the leaf's AUTHORED
        window. The old extractor read vc.diagnostics['window'], which the
        evaluator never sets (it stashes the window on the MetricResult),
        so authored windows rendered as empty inputs — masked by browser
        fixtures hand-setting window_values (found on the D103 demo)."""
        import numpy as np

        from dstf.backends import TestResult, VariableResult
        from dstf.comparison.algorithms import _compare_trajectories
        from dstf.comparison.metric_tree import leaf_from_variable
        from dstf.comparison.tree_spec import parse_metric_tree
        from dstf.discovery.test_registry import TestModel
        from dstf.reporting.context_builder import _build_template_context

        t = np.linspace(0.0, 10.0, 50)
        v = 1.0 - np.exp(-t)
        vc = _compare_trajectories(t, v, t, v, 1e-3)
        vc.name = "x"
        vc.index = 1

        model = TestModel(
            model_id="Fixture.WindowSeed",
            source_file=Path("f.mo"),
            source_package="Fixture",
            short_name="WindowSeed",
            n_vars=1,
            metric_tree_spec=parse_metric_tree(
                {
                    "metric": "nrmse",
                    "variable": "x",
                    "tolerance": 1e-3,
                    "window": {"start": 2.5, "end": 10.0},
                }
            ),
        )
        ctx = _build_template_context(
            model_id=model.model_id,
            comparisons=[vc],
            ref_data=None,
            cur_stats=None,
            test_dir=None,
            test_model=model,
            result=TestResult(
                model_id=model.model_id,
                success=True,
                variables=[VariableResult(index=1, time=t, values=v)],
            ),
            metric_tree=leaf_from_variable(vc),
        )
        leaf = ctx["tree_view"]
        assert leaf["window"] == {"start": 2.5, "end": 10.0}
        assert leaf["window_values"] == {"start": 2.5, "end": 10.0}


class TestAgainstLeafIsCliAuthoritative:
    """When a leaf scored against a named baseline can be live-scored.

    It used to be pinned CLI-authoritative unconditionally, on the grounds
    that the page carried only the primary reference. It carries every
    soft_check's trajectory too (they are the plot overlays), and the JS
    scorers now resolve an against-leaf to that curve — so the pin narrows
    to the case where the named baseline is genuinely NOT embedded for
    that variable, which really does leave nothing to score against.
    """

    @staticmethod
    def _leaf_view(against: str | None, overlays=None) -> dict:
        import numpy as np

        from dstf.backends import TestResult, VariableResult
        from dstf.comparison.algorithms import _compare_trajectories
        from dstf.comparison.metric_tree import leaf_from_variable
        from dstf.comparison.tree_spec import parse_metric_tree
        from dstf.discovery.test_registry import TestModel
        from dstf.reporting.context_builder import _build_template_context

        t = np.linspace(0.0, 10.0, 50)
        v = 1.0 - np.exp(-t)
        vc = _compare_trajectories(t, v, t, v, 1e-3)
        vc.name = "x"
        vc.index = 1

        spec: dict = {"metric": "nrmse", "variable": "x", "tolerance": 1e-3}
        if against is not None:
            spec["against"] = against
        model = TestModel(
            model_id="Fixture.Against",
            source_file=Path("f.mo"),
            source_package="Fixture",
            short_name="Against",
            n_vars=1,
            metric_tree_spec=parse_metric_tree(spec),
        )
        ctx = _build_template_context(
            model_id=model.model_id,
            comparisons=[vc],
            ref_data=None,
            cur_stats=None,
            test_dir=None,
            test_model=model,
            result=TestResult(
                model_id=model.model_id,
                success=True,
                variables=[VariableResult(index=1, time=t, values=v)],
            ),
            metric_tree=leaf_from_variable(vc),
            overlays=overlays,
        )
        return ctx["tree_view"]

    @staticmethod
    def _overlay(name: str, role: str = "soft_check", variable: str = "x"):
        import numpy as np

        from dstf.reporting.overlay_loader import Overlay, OverlayVariable

        t = np.linspace(0.0, 10.0, 50)
        return Overlay(
            name=name,
            role=role,
            status="loaded",
            variables={variable: OverlayVariable(list(t), list(1.0 - np.exp(-t)))},
        )

    def test_named_baseline_without_an_embedded_overlay_stays_pinned(self):
        """No overlay for that variable ⇒ the curve is not on the page ⇒
        nothing to live-score against ⇒ the CLI verdict stands."""
        leaf = self._leaf_view("experiment")
        assert leaf["against"] == "experiment"
        assert leaf["cli_authoritative"] is True

    def test_named_baseline_with_an_embedded_overlay_scores_live(self):
        leaf = self._leaf_view("experiment", overlays=[self._overlay("experiment")])
        assert leaf["against"] == "experiment"
        assert leaf["cli_authoritative"] is False

    def test_a_different_baselines_overlay_does_not_unpin_the_leaf(self):
        leaf = self._leaf_view("experiment", overlays=[self._overlay("rig")])
        assert leaf["cli_authoritative"] is True

    def test_a_companion_never_unpins_the_leaf(self):
        """Companions are unscorable (comparison/validator.py). A companion
        that happens to share the name must not make the leaf look
        live-scorable."""
        leaf = self._leaf_view(
            "experiment",
            overlays=[self._overlay("experiment", role="companion")],
        )
        assert leaf["cli_authoritative"] is True

    def test_an_overlay_without_this_variable_does_not_unpin_the_leaf(self):
        leaf = self._leaf_view(
            "experiment",
            overlays=[self._overlay("experiment", variable="somethingelse")],
        )
        assert leaf["cli_authoritative"] is True

    def test_primary_leaf_keeps_live_scoring(self):
        leaf = self._leaf_view(None)
        assert leaf["cli_authoritative"] is False


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
