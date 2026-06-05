"""Tests for discovery/patch_apply.py — RFC 6902 on test_spec.json.

Critical invariant from D66: unknown keys and hand-authored notes
(`description`, `info`, `metadata`) must survive patch-apply round-trips.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dstf.discovery.patch_apply import PatchError, apply_patch


def _write_spec(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _read_spec(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TestBasicOps:
    def test_replace_scalar_in_whitelisted_path(self, tmp_path):
        spec = tmp_path / "spec.json"
        _write_spec(
            spec, {"tests": [{"model": "M", "comparison": {"tolerance": 1e-4}}]}
        )
        apply_patch(
            spec,
            "M",
            [
                {"op": "replace", "path": "/comparison/tolerance", "value": 1e-3},
            ],
        )
        data = _read_spec(spec)
        assert data["tests"][0]["comparison"]["tolerance"] == 1e-3

    def test_add_new_key(self, tmp_path):
        spec = tmp_path / "spec.json"
        _write_spec(spec, {"tests": [{"model": "M", "comparison": {}}]})
        apply_patch(
            spec,
            "M",
            [
                {"op": "add", "path": "/comparison/tolerance", "value": 0.01},
            ],
        )
        data = _read_spec(spec)
        assert data["tests"][0]["comparison"]["tolerance"] == 0.01

    def test_remove_key(self, tmp_path):
        spec = tmp_path / "spec.json"
        _write_spec(
            spec,
            {
                "tests": [
                    {"model": "M", "comparison": {"tolerance": 1e-3, "info": "keep me"}}
                ]
            },
        )
        apply_patch(
            spec,
            "M",
            [
                {"op": "remove", "path": "/comparison/tolerance"},
            ],
        )
        data = _read_spec(spec)
        assert "tolerance" not in data["tests"][0]["comparison"]
        assert data["tests"][0]["comparison"]["info"] == "keep me"

    def test_add_nested_path_creates_intermediates(self, tmp_path):
        spec = tmp_path / "spec.json"
        _write_spec(spec, {"tests": [{"model": "M"}]})
        apply_patch(
            spec,
            "M",
            [
                {
                    "op": "add",
                    "path": "/comparison/variable_overrides/h/tolerance",
                    "value": 0.01,
                },
            ],
        )
        data = _read_spec(spec)
        assert (
            data["tests"][0]["comparison"]["variable_overrides"]["h"]["tolerance"]
            == 0.01
        )


class TestUnknownKeyPreservation:
    """D66 invariant: patch-apply must never destroy hand-authored fields."""

    def test_preserves_entry_level_metadata(self, tmp_path):
        spec = tmp_path / "spec.json"
        _write_spec(
            spec,
            {
                "tests": [
                    {
                        "model": "M",
                        "description": "A critical test — last modified 2024-03-15 by AB.",
                        "metadata": {"owner": "fluid-group", "priority": "high"},
                        "comparison": {"tolerance": 1e-4},
                    }
                ],
            },
        )
        apply_patch(
            spec,
            "M",
            [
                {"op": "replace", "path": "/comparison/tolerance", "value": 1e-3},
            ],
        )
        entry = _read_spec(spec)["tests"][0]
        assert (
            entry["description"] == "A critical test — last modified 2024-03-15 by AB."
        )
        assert entry["metadata"] == {"owner": "fluid-group", "priority": "high"}

    def test_preserves_comparison_level_unknown_keys(self, tmp_path):
        spec = tmp_path / "spec.json"
        _write_spec(
            spec,
            {
                "tests": [
                    {
                        "model": "M",
                        "comparison": {
                            "tolerance": 1e-4,
                            "info": "Expect rising edge at t~2.4s",
                            "metadata": {
                                "rationale": "Solver defaults produce ringing"
                            },
                        },
                    }
                ],
            },
        )
        apply_patch(
            spec,
            "M",
            [
                {"op": "replace", "path": "/comparison/tolerance", "value": 1e-3},
            ],
        )
        comp = _read_spec(spec)["tests"][0]["comparison"]
        assert comp["info"] == "Expect rising edge at t~2.4s"
        assert comp["metadata"] == {"rationale": "Solver defaults produce ringing"}

    def test_preserves_sibling_tests(self, tmp_path):
        spec = tmp_path / "spec.json"
        _write_spec(
            spec,
            {
                "tests": [
                    {"model": "A", "comparison": {"tolerance": 1e-4}},
                    {
                        "model": "B",
                        "comparison": {"tolerance": 1e-5},
                        "metadata": {"note": "keep me untouched"},
                    },
                    {"model": "C", "comparison": {"tolerance": 1e-6}},
                ],
            },
        )
        apply_patch(
            spec,
            "B",
            [
                {"op": "replace", "path": "/comparison/tolerance", "value": 1e-3},
            ],
        )
        data = _read_spec(spec)
        assert data["tests"][0] == {"model": "A", "comparison": {"tolerance": 1e-4}}
        assert data["tests"][1]["metadata"] == {"note": "keep me untouched"}
        assert data["tests"][1]["comparison"]["tolerance"] == 1e-3
        assert data["tests"][2] == {"model": "C", "comparison": {"tolerance": 1e-6}}


class TestWhitelist:
    def test_outside_whitelist_rejected(self, tmp_path):
        spec = tmp_path / "spec.json"
        _write_spec(spec, {"tests": [{"model": "M", "simulation": {"stop_time": 10}}]})
        with pytest.raises(PatchError, match="whitelist"):
            apply_patch(
                spec,
                "M",
                [
                    {"op": "replace", "path": "/simulation/stop_time", "value": 100},
                ],
            )

    def test_default_allows_comparison_and_metrics(self, tmp_path):
        spec = tmp_path / "spec.json"
        _write_spec(
            spec,
            {
                "tests": [
                    {
                        "model": "M",
                        "comparison": {"tolerance": 1e-4},
                        "metrics": {
                            "metric": "nrmse",
                            "variable": "h",
                            "tolerance": 1e-4,
                        },
                    }
                ]
            },
        )
        apply_patch(
            spec,
            "M",
            [
                {"op": "replace", "path": "/comparison/tolerance", "value": 1e-3},
                {"op": "replace", "path": "/metrics/tolerance", "value": 1e-3},
            ],
        )
        entry = _read_spec(spec)["tests"][0]
        assert entry["comparison"]["tolerance"] == 1e-3
        assert entry["metrics"]["tolerance"] == 1e-3


class TestJsonPointerEscaping:
    def test_escape_tilde_and_slash(self, tmp_path):
        """RFC 6901: `~0` → `~`, `~1` → `/`. Order matters (~1 first)."""
        spec = tmp_path / "spec.json"
        _write_spec(
            spec, {"tests": [{"model": "M", "comparison": {"variable_overrides": {}}}]}
        )
        # Variable name literally `a/b~c` escapes to `a~1b~0c`
        apply_patch(
            spec,
            "M",
            [
                {
                    "op": "add",
                    "path": "/comparison/variable_overrides/a~1b~0c/tolerance",
                    "value": 1e-3,
                },
            ],
        )
        data = _read_spec(spec)
        assert data["tests"][0]["comparison"]["variable_overrides"]["a/b~c"] == {
            "tolerance": 1e-3
        }


class TestErrorSurfacing:
    def test_missing_op_raises(self, tmp_path):
        spec = tmp_path / "spec.json"
        _write_spec(spec, {"tests": [{"model": "M", "comparison": {}}]})
        with pytest.raises(PatchError, match="unsupported op"):
            apply_patch(spec, "M", [{"op": "copy", "path": "/comparison/x"}])

    def test_replace_missing_key_rejected(self, tmp_path):
        spec = tmp_path / "spec.json"
        _write_spec(spec, {"tests": [{"model": "M", "comparison": {}}]})
        with pytest.raises(PatchError, match="not present"):
            apply_patch(
                spec,
                "M",
                [
                    {"op": "replace", "path": "/comparison/does_not_exist", "value": 1},
                ],
            )

    def test_add_missing_value_rejected(self, tmp_path):
        spec = tmp_path / "spec.json"
        _write_spec(spec, {"tests": [{"model": "M", "comparison": {}}]})
        with pytest.raises(PatchError, match="requires 'value'"):
            apply_patch(spec, "M", [{"op": "add", "path": "/comparison/tolerance"}])

    def test_invalid_path_shape_rejected(self, tmp_path):
        spec = tmp_path / "spec.json"
        _write_spec(spec, {"tests": [{"model": "M"}]})
        with pytest.raises(PatchError):
            apply_patch(spec, "M", [{"op": "replace", "path": "bad-path", "value": 1}])


class TestWholesaleMetricsReplace:
    """Stage-4 structural edits emit a single ``add`` at ``/metrics`` with
    the whole new tree. Verify wholesale replacement preserves hand-
    authored sibling keys on the test entry."""

    def test_add_metrics_tree_upserts_when_absent(self, tmp_path):
        spec = tmp_path / "spec.json"
        _write_spec(
            spec,
            {
                "tests": [
                    {
                        "model": "M",
                        "description": "hand-authored",
                        "comparison": {"tolerance": 1e-4},
                    }
                ]
            },
        )
        new_tree = {
            "combinator": "and",
            "children": [
                {"metric": "nrmse", "variable": "h"},
                {"metric": "nrmse", "variable": "v"},
            ],
        }
        apply_patch(
            spec,
            "M",
            [
                {"op": "add", "path": "/metrics", "value": new_tree},
            ],
        )
        entry = _read_spec(spec)["tests"][0]
        assert entry["metrics"] == new_tree
        assert entry["description"] == "hand-authored"
        assert entry["comparison"]["tolerance"] == 1e-4

    def test_add_metrics_tree_replaces_existing(self, tmp_path):
        spec = tmp_path / "spec.json"
        _write_spec(
            spec,
            {
                "tests": [
                    {
                        "model": "M",
                        "metrics": {"metric": "nrmse", "variable": "h"},
                        "description": "keep me",
                    }
                ]
            },
        )
        new_tree = {
            "combinator": "warn",
            "children": [{"metric": "nrmse", "variable": "h"}],
        }
        apply_patch(
            spec,
            "M",
            [
                {"op": "add", "path": "/metrics", "value": new_tree},
            ],
        )
        entry = _read_spec(spec)["tests"][0]
        assert entry["metrics"] == new_tree
        assert entry["description"] == "keep me"

    def test_add_metrics_preserves_sibling_test_entries(self, tmp_path):
        spec = tmp_path / "spec.json"
        _write_spec(
            spec,
            {
                "tests": [
                    {"model": "A", "metrics": {"metric": "nrmse", "variable": "x"}},
                    {"model": "B", "metrics": {"metric": "nrmse", "variable": "y"}},
                ]
            },
        )
        new_tree = {
            "combinator": "and",
            "children": [
                {"metric": "nrmse", "variable": "y"},
                {"metric": "range", "variable": "y", "min": -1, "max": 1},
            ],
        }
        apply_patch(
            spec,
            "B",
            [
                {"op": "add", "path": "/metrics", "value": new_tree},
            ],
        )
        data = _read_spec(spec)
        assert data["tests"][0]["metrics"] == {"metric": "nrmse", "variable": "x"}
        assert data["tests"][1]["metrics"] == new_tree


class TestWindowPath:
    """Idea #46 UI surfacing — reporter emits window upserts via 'add' on
    /metrics/.../window. Verify round-trip with tree leaves."""

    def test_add_window_on_root_leaf(self, tmp_path):
        spec = tmp_path / "spec.json"
        _write_spec(
            spec,
            {
                "tests": [
                    {
                        "model": "M",
                        "metrics": {
                            "metric": "nrmse",
                            "variable": "h",
                            "tolerance": 1e-4,
                        },
                    }
                ]
            },
        )
        apply_patch(
            spec,
            "M",
            [
                {
                    "op": "add",
                    "path": "/metrics/window",
                    "value": {"start": 1.0, "end": 5.0},
                },
            ],
        )
        metrics = _read_spec(spec)["tests"][0]["metrics"]
        assert metrics["window"] == {"start": 1.0, "end": 5.0}
        # Original fields untouched
        assert metrics["metric"] == "nrmse"
        assert metrics["variable"] == "h"

    def test_add_window_on_nested_leaf(self, tmp_path):
        spec = tmp_path / "spec.json"
        _write_spec(
            spec,
            {
                "tests": [
                    {
                        "model": "M",
                        "metrics": {
                            "combinator": "and",
                            "children": [
                                {"metric": "nrmse", "variable": "x"},
                                {"metric": "nrmse", "variable": "y"},
                            ],
                        },
                    }
                ]
            },
        )
        apply_patch(
            spec,
            "M",
            [
                {
                    "op": "add",
                    "path": "/metrics/children/1/window",
                    "value": {"start": 2.0},
                },
            ],
        )
        children = _read_spec(spec)["tests"][0]["metrics"]["children"]
        assert children[0] == {"metric": "nrmse", "variable": "x"}
        assert children[1]["window"] == {"start": 2.0}

    def test_add_upserts_existing_window(self, tmp_path):
        """'add' on a pre-existing window replaces the value (must_exist=False
        doesn't reject already-present keys — matches reporter's upsert usage)."""
        spec = tmp_path / "spec.json"
        _write_spec(
            spec,
            {
                "tests": [
                    {
                        "model": "M",
                        "metrics": {
                            "metric": "nrmse",
                            "variable": "h",
                            "window": {"start": 0.0, "end": 10.0},
                        },
                    }
                ]
            },
        )
        apply_patch(
            spec,
            "M",
            [
                {
                    "op": "add",
                    "path": "/metrics/window",
                    "value": {"start": 1.0, "end": 5.0},
                },
            ],
        )
        assert _read_spec(spec)["tests"][0]["metrics"]["window"] == {
            "start": 1.0,
            "end": 5.0,
        }

    def test_remove_window(self, tmp_path):
        spec = tmp_path / "spec.json"
        _write_spec(
            spec,
            {
                "tests": [
                    {
                        "model": "M",
                        "metrics": {
                            "metric": "nrmse",
                            "variable": "h",
                            "window": {"start": 1.0, "end": 5.0},
                        },
                    }
                ]
            },
        )
        apply_patch(
            spec,
            "M",
            [
                {"op": "remove", "path": "/metrics/window"},
            ],
        )
        metrics = _read_spec(spec)["tests"][0]["metrics"]
        assert "window" not in metrics
        assert metrics["metric"] == "nrmse"


class TestMissingModel:
    def test_creates_new_entry_when_model_not_found(self, tmp_path):
        spec = tmp_path / "spec.json"
        _write_spec(
            spec, {"tests": [{"model": "A", "comparison": {"tolerance": 1e-4}}]}
        )
        apply_patch(
            spec,
            "B",
            [
                {"op": "add", "path": "/comparison/tolerance", "value": 1e-3},
            ],
        )
        data = _read_spec(spec)
        assert len(data["tests"]) == 2
        assert data["tests"][1]["model"] == "B"
        assert data["tests"][1]["comparison"]["tolerance"] == 1e-3

    def test_creates_spec_file_when_missing(self, tmp_path):
        spec = tmp_path / "nonexistent.json"
        apply_patch(
            spec,
            "B",
            [
                {"op": "add", "path": "/comparison/tolerance", "value": 1e-3},
            ],
        )
        assert spec.exists()
        data = _read_spec(spec)
        assert data["tests"][0]["model"] == "B"


class TestRejectionEdges:
    """Validation/rejection guards (D88) — these protect the spec from
    malformed patches exported by the reporter. They were the uncovered
    branches in the 77% baseline."""

    def _spec(self, tmp_path):
        spec = tmp_path / "spec.json"
        _write_spec(spec, {"tests": [{"model": "M", "comparison": {"a": [1, 2]}}]})
        return spec

    def test_corrupt_json_rejected(self, tmp_path):
        spec = tmp_path / "spec.json"
        spec.write_text("{not json", encoding="utf-8")
        with pytest.raises(PatchError, match="cannot read"):
            apply_patch(spec, "M", [{"op": "add", "path": "/comparison/x", "value": 1}])

    def test_root_not_object_rejected(self, tmp_path):
        spec = tmp_path / "spec.json"
        spec.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(PatchError, match="root must be an object"):
            apply_patch(spec, "M", [{"op": "add", "path": "/comparison/x", "value": 1}])

    def test_tests_not_list_rejected(self, tmp_path):
        spec = tmp_path / "spec.json"
        spec.write_text('{"tests": {}}', encoding="utf-8")
        with pytest.raises(PatchError, match="'tests' must be a list"):
            apply_patch(spec, "M", [{"op": "add", "path": "/comparison/x", "value": 1}])

    def test_pointer_must_start_with_slash(self, tmp_path):
        with pytest.raises(PatchError, match="JSON-Pointer"):
            apply_patch(
                self._spec(tmp_path),
                "M",
                [{"op": "add", "path": "comparison/x", "value": 1}],
            )

    def test_add_requires_value(self, tmp_path):
        with pytest.raises(PatchError, match="requires 'value'"):
            apply_patch(
                self._spec(tmp_path), "M", [{"op": "add", "path": "/comparison/x"}]
            )

    def test_empty_path_rejected(self, tmp_path):
        # An empty pointer fails the RFC 6901 format guard (it does not start
        # with "/"), protecting the entry root from a wholesale overwrite.
        with pytest.raises(PatchError, match="JSON-Pointer"):
            apply_patch(self._spec(tmp_path), "M", [{"op": "add", "path": "", "value": 1}])

    def test_remove_missing_key(self, tmp_path):
        with pytest.raises(PatchError, match="not present"):
            apply_patch(
                self._spec(tmp_path), "M", [{"op": "remove", "path": "/comparison/ghost"}]
            )

    def test_list_index_out_of_range(self, tmp_path):
        with pytest.raises(PatchError, match="out of range"):
            apply_patch(
                self._spec(tmp_path),
                "M",
                [{"op": "replace", "path": "/comparison/a/9", "value": 0}],
            )

    def test_list_index_not_integer(self, tmp_path):
        with pytest.raises(PatchError, match="must be an integer"):
            apply_patch(
                self._spec(tmp_path),
                "M",
                [{"op": "replace", "path": "/comparison/a/x", "value": 0}],
            )

    def test_append_token_rejected_on_replace(self, tmp_path):
        with pytest.raises(PatchError, match="append"):
            apply_patch(
                self._spec(tmp_path),
                "M",
                [{"op": "replace", "path": "/comparison/a/-", "value": 0}],
            )
