"""Review 2026-07-06 reporter-batch fixes — Python-side regression tests.

Covers:

* Finding 41 — ``cmd_spec_update`` materializes the implicit metric tree
  before applying reporter-emitted per-leaf ops to a flat-override entry
  (round-trip: flat entry → per-leaf op → valid spec carrying the edit).
* Finding 68 — ``comparison_data.json`` and the embedded interactive.html
  context are strict JSON (non-finite floats sanitized to null).
* Finding 37 (support) — ``_decimate_context_for_html`` stamps
  pre-decimation ``full_samples`` counts for the JS decimation banner.
* Finding 76h (support) — the template emits ``VARIABLE_ORDER`` as an
  explicit JSON array so integer-like variable names can't desync plot IDs.

The JS-side behavior for findings 37-40, 42-47, and 76 is exercised by
``tests/test_scorer_parity.py`` (node runner) and the Playwright suite.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pytest

from dstf.comparison.tree_spec import (
    parse_metric_tree,
    spec_to_raw,
    synthesize_implicit_tree,
)
from dstf.comparison.types import VariableComparison
from dstf.reporting.plot_comparison import (
    _decimate_context_for_html,
    _json_sanitize,
    generate_comparison_plots,
)

# ---------------------------------------------------------------------------
# Finding 41 — spec-update materializes the implicit tree
# ---------------------------------------------------------------------------


def _run_spec_update(spec_path: Path, update_json_path: Path) -> int:
    """Invoke cmd_spec_update directly, bypassing arg parsing (same
    harness as tests/test_spec_update_cli.py)."""
    from dstf.cli import cmd_spec_update

    cfg_dir = spec_path.parent
    if not (cfg_dir / "testing.json").exists():
        (cfg_dir / "testing.json").write_text(
            json.dumps(
                {
                    "source_type": "fmu",
                    "simulator": "FMPy",
                    "reference_root": ".",
                }
            ),
            encoding="utf-8",
        )
    ns = argparse.Namespace(
        config=str(cfg_dir / "testing.json"),
        source_path=None,
        reference_root=str(cfg_dir),
        test_spec=str(spec_path),
        dymola_interface=None,
        json_file=str(update_json_path),
    )
    return cmd_spec_update(ns)


def _write_spec(tmp_path: Path, entry: dict) -> Path:
    spec = tmp_path / "test_spec.json"
    spec.write_text(json.dumps({"tests": [entry]}), encoding="utf-8")
    return spec


def _write_patch(tmp_path: Path, model: str, ops: list[dict]) -> Path:
    patch = tmp_path / "spec_patch.json"
    patch.write_text(json.dumps({"model": model, "patch": ops}), encoding="utf-8")
    return patch


class TestSpecUpdateMaterialization:
    def test_per_leaf_op_round_trips_on_flat_entry(self, tmp_path):
        """Reporter-style per-leaf op against a flat-override entry: the
        implicit tree is materialized, the op lands, and the result parses
        as a valid metric tree carrying the edit."""
        spec = _write_spec(
            tmp_path,
            {
                "model": "MyLib.A",
                "variables": ["h", "v"],
                "comparison": {
                    "tolerance": 1e-4,
                    "variable_overrides": {
                        "v": {
                            "mode": "tube",
                            "tube_width_mode": "rel",
                            "tube_rel": 0.05,
                        }
                    },
                },
            },
        )
        patch = _write_patch(
            tmp_path,
            "MyLib.A",
            [{"op": "add", "path": "/metrics/children/0/tolerance", "value": 0.5}],
        )

        rc = _run_spec_update(spec, patch)
        assert rc == 0

        data = json.loads(spec.read_text(encoding="utf-8"))
        entry = data["tests"][0]
        assert "metrics" in entry

        # Materialized tree mirrors what the reporter displayed: an AND of
        # one leaf per tracked variable, overrides flowing into leaf params.
        tree = parse_metric_tree(entry["metrics"])
        leaves = tree.children
        assert [leaf.variable for leaf in leaves] == ["h", "v"]
        assert leaves[0].metric == "nrmse"
        assert leaves[0].params["tolerance"] == 0.5  # the applied edit
        assert leaves[1].metric == "tube"
        assert leaves[1].params["tube_rel"] == 0.05

        # The flat comparison block survives untouched (whitelist scope).
        assert entry["comparison"]["variable_overrides"]["v"]["tube_rel"] == 0.05

    def test_existing_metrics_tree_is_not_rematerialized(self, tmp_path):
        spec = _write_spec(
            tmp_path,
            {
                "model": "MyLib.B",
                "variables": ["h"],
                "metrics": {
                    "combinator": "and",
                    "children": [
                        {"metric": "nrmse", "variable": "h", "tolerance": 1e-3}
                    ],
                },
            },
        )
        patch = _write_patch(
            tmp_path,
            "MyLib.B",
            [
                {
                    "op": "replace",
                    "path": "/metrics/children/0/tolerance",
                    "value": 2e-3,
                }
            ],
        )
        rc = _run_spec_update(spec, patch)
        assert rc == 0
        data = json.loads(spec.read_text(encoding="utf-8"))
        entry = data["tests"][0]
        assert entry["metrics"]["children"][0]["tolerance"] == 2e-3
        assert len(entry["metrics"]["children"]) == 1

    def test_wholesale_metrics_add_needs_no_materialization(self, tmp_path):
        """A structural export (single add at exactly /metrics) applies
        as-is on a flat entry — no materialization prepended."""
        spec = _write_spec(
            tmp_path,
            {"model": "MyLib.C", "variables": ["h"]},
        )
        new_tree = {
            "combinator": "and",
            "children": [{"metric": "range", "variable": "h", "min_value": 0.0}],
        }
        patch = _write_patch(
            tmp_path,
            "MyLib.C",
            [{"op": "add", "path": "/metrics", "value": new_tree}],
        )
        rc = _run_spec_update(spec, patch)
        assert rc == 0
        data = json.loads(spec.read_text(encoding="utf-8"))
        assert data["tests"][0]["metrics"] == new_tree

    def test_flat_entry_without_variables_fails_cleanly(self, tmp_path):
        """No tracked variables → nothing to materialize leaves from; the
        command errors instead of writing a broken spec."""
        spec = _write_spec(tmp_path, {"model": "MyLib.D"})
        original = spec.read_text(encoding="utf-8")
        patch = _write_patch(
            tmp_path,
            "MyLib.D",
            [{"op": "add", "path": "/metrics/children/0/tolerance", "value": 0.5}],
        )
        rc = _run_spec_update(spec, patch)
        assert rc == 1
        assert spec.read_text(encoding="utf-8") == original  # untouched


class TestSpecToRaw:
    def test_round_trips_through_parse(self):
        raw = {
            "combinator": "k-of-n",
            "k": 2,
            "children": [
                {"metric": "nrmse", "variable": "h", "tolerance": 1e-3},
                {
                    "metric": "tube",
                    "variable": "v",
                    "tube_width_mode": "rel",
                    "tube_rel": 0.05,
                    "against": "experiment",
                    "window": {"start": 1.0, "end": 5.0},
                },
                {
                    "combinator": "weighted",
                    "children": [
                        {"metric": "range", "variable": "p", "min_value": 0.0}
                    ],
                    "weights": [2.0],
                    "threshold": 0.5,
                    "direction": "greater",
                },
            ],
        }
        tree = parse_metric_tree(raw)
        assert spec_to_raw(tree) == raw

    def test_synthesized_implicit_tree_serializes_and_parses(self):
        tree = synthesize_implicit_tree(
            ["a", "b"],
            variable_overrides={"b": {"mode": "range", "min_value": 0.0}},
        )
        raw = spec_to_raw(tree)
        reparsed = parse_metric_tree(raw)
        assert [leaf.metric for leaf in reparsed.children] == ["nrmse", "range"]
        assert reparsed.children[1].params == {"min_value": 0.0}


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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
