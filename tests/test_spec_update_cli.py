"""End-to-end cmd_spec_update tests — the RFC 6902 patch envelope."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from dstf.comparison.tree_spec import parse_metric_tree
from dstf.config import ConfigError


def _run_spec_update(spec_path: Path, update_json_path: Path) -> int:
    """Invoke cmd_spec_update directly, bypassing arg parsing."""
    from dstf.cli.spec_cmds import cmd_spec_update

    # Use the spec's parent dir as reference_root so _build_config can
    # locate testing.json. Write a minimal one.
    cfg_dir = spec_path.parent
    if not (cfg_dir / "testing.json").exists():
        # source_type=fmu skips Modelica package.mo auto-discovery that
        # would otherwise walk out of the temp directory.
        (cfg_dir / "testing.json").write_text(
            json.dumps(
                {
                    "source_type": "fmu",
                    "backend": "FMPy",
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


class TestPatchFormat:
    def test_end_to_end_patch_application(self, tmp_path):
        spec = tmp_path / "test_spec.json"
        spec.write_text(
            json.dumps(
                {
                    "tests": [
                        {
                            "model": "MyLib.A",
                            "comparison": {"tolerance": 1e-4},
                            "metadata": {"owner": "unit"},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        patch = tmp_path / "spec_patch.json"
        patch.write_text(
            json.dumps(
                {
                    "model": "MyLib.A",
                    "patch": [
                        {
                            "op": "replace",
                            "path": "/comparison/tolerance",
                            "value": 1e-3,
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

        rc = _run_spec_update(spec, patch)
        assert rc == 0

        data = json.loads(spec.read_text())
        assert data["tests"][0]["comparison"]["tolerance"] == 1e-3
        # Metadata survived
        assert data["tests"][0]["metadata"] == {"owner": "unit"}

    def test_patch_whitelist_rejection_exits_nonzero(self, tmp_path):
        spec = tmp_path / "test_spec.json"
        spec.write_text(
            json.dumps(
                {
                    "tests": [{"model": "M", "simulation": {"stop_time": 10}}],
                }
            ),
            encoding="utf-8",
        )

        patch = tmp_path / "p.json"
        patch.write_text(
            json.dumps(
                {
                    "model": "M",
                    "patch": [
                        {"op": "replace", "path": "/simulation/stop_time", "value": 100}
                    ],
                }
            ),
            encoding="utf-8",
        )

        rc = _run_spec_update(spec, patch)
        assert rc == 1
        # Spec file should be unchanged
        data = json.loads(spec.read_text())
        assert data["tests"][0]["simulation"]["stop_time"] == 10


class TestNonEnvelopeRejected:
    """The patch envelope is the only accepted input shape."""

    def _spec(self, tmp_path) -> Path:
        spec = tmp_path / "test_spec.json"
        spec.write_text(
            json.dumps(
                {
                    "tests": [{"model": "MyLib.A", "comparison": {"tolerance": 1e-4}}],
                }
            ),
            encoding="utf-8",
        )
        return spec

    def test_flat_tolerance_dict_is_rejected(self, tmp_path):
        spec = self._spec(tmp_path)
        flat = tmp_path / "flat.json"
        flat.write_text(
            json.dumps({"model": "MyLib.A", "comparison": {"tolerance": 1e-3}}),
            encoding="utf-8",
        )

        with pytest.raises(ConfigError, match=r'"patch"'):
            _run_spec_update(spec, flat)

        # Spec file untouched.
        data = json.loads(spec.read_text())
        assert data["tests"][0]["comparison"]["tolerance"] == 1e-4

    def test_non_list_patch_is_rejected(self, tmp_path):
        spec = self._spec(tmp_path)
        bad = tmp_path / "bad.json"
        bad.write_text(
            json.dumps({"model": "MyLib.A", "patch": {"op": "replace"}}),
            encoding="utf-8",
        )

        with pytest.raises(ConfigError, match="not a JSON-Patch envelope"):
            _run_spec_update(spec, bad)


# =========================================================================
# Folded in from tests/test_review_fixes_reporter.py — 2026-07-06 review,
# reporter batch (CODE_REVIEW_2026-07-06.md).
#
# Finding 41 — ``cmd_spec_update`` materializes the implicit metric tree
# before applying reporter-emitted per-leaf ops to a flat-override entry
# (round-trip: flat entry -> per-leaf op -> valid spec carrying the edit).
#
# The source file carried a byte-identical ``_run_spec_update`` harness; the
# one already defined above is used instead.
# =========================================================================


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


class TestMultiModelEnvelope:
    def test_two_models_in_one_file(self, tmp_path):
        spec = tmp_path / "test_spec.json"
        spec.write_text(
            json.dumps(
                {
                    "tests": [
                        {"model": "A", "variables": ["x"]},
                        {"model": "B", "variables": ["y"]},
                    ]
                }
            ),
            encoding="utf-8",
        )
        patch = tmp_path / "p.json"
        patch.write_text(
            json.dumps(
                {
                    "patches": [
                        {
                            "model": "A",
                            "patch": [
                                {
                                    "op": "add",
                                    "path": "/comparison",
                                    "value": {"tolerance": 0.1},
                                }
                            ],
                        },
                        {
                            "model": "B",
                            "patch": [
                                {
                                    "op": "add",
                                    "path": "/comparison",
                                    "value": {"tolerance": 0.2},
                                }
                            ],
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )

        assert _run_spec_update(spec, patch) == 0
        data = json.loads(spec.read_text(encoding="utf-8"))
        assert data["tests"][0]["comparison"] == {"tolerance": 0.1}
        assert data["tests"][1]["comparison"] == {"tolerance": 0.2}

    def test_a_bad_second_model_leaves_the_first_unwritten(self, tmp_path):
        spec = tmp_path / "test_spec.json"
        spec.write_text(
            json.dumps({"tests": [{"model": "A", "variables": ["x"]}]}),
            encoding="utf-8",
        )
        before = spec.read_bytes()
        patch = tmp_path / "p.json"
        patch.write_text(
            json.dumps(
                {
                    "patches": [
                        {
                            "model": "A",
                            "patch": [
                                {
                                    "op": "add",
                                    "path": "/comparison",
                                    "value": {"tolerance": 0.1},
                                }
                            ],
                        },
                        {
                            "model": "A",
                            "patch": [
                                {
                                    "op": "replace",
                                    "path": "/simulation/stop_time",
                                    "value": 3,
                                }
                            ],
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        assert _run_spec_update(spec, patch) == 1
        assert spec.read_bytes() == before
