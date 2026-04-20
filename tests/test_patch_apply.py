"""Tests for discovery/patch_apply.py — RFC 6902 on test_spec.json.

Critical invariant from D66: unknown keys and hand-authored notes
(`description`, `info`, `metadata`) must survive patch-apply round-trips.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from modelica_testing.discovery.patch_apply import PatchError, apply_patch


def _write_spec(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _read_spec(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TestBasicOps:
    def test_replace_scalar_in_whitelisted_path(self, tmp_path):
        spec = tmp_path / "spec.json"
        _write_spec(spec, {"tests": [{"model": "M", "comparison": {"tolerance": 1e-4}}]})
        apply_patch(spec, "M", [
            {"op": "replace", "path": "/comparison/tolerance", "value": 1e-3},
        ])
        data = _read_spec(spec)
        assert data["tests"][0]["comparison"]["tolerance"] == 1e-3

    def test_add_new_key(self, tmp_path):
        spec = tmp_path / "spec.json"
        _write_spec(spec, {"tests": [{"model": "M", "comparison": {}}]})
        apply_patch(spec, "M", [
            {"op": "add", "path": "/comparison/tolerance", "value": 0.01},
        ])
        data = _read_spec(spec)
        assert data["tests"][0]["comparison"]["tolerance"] == 0.01

    def test_remove_key(self, tmp_path):
        spec = tmp_path / "spec.json"
        _write_spec(spec, {"tests": [{"model": "M", "comparison": {"tolerance": 1e-3, "info": "keep me"}}]})
        apply_patch(spec, "M", [
            {"op": "remove", "path": "/comparison/tolerance"},
        ])
        data = _read_spec(spec)
        assert "tolerance" not in data["tests"][0]["comparison"]
        assert data["tests"][0]["comparison"]["info"] == "keep me"

    def test_add_nested_path_creates_intermediates(self, tmp_path):
        spec = tmp_path / "spec.json"
        _write_spec(spec, {"tests": [{"model": "M"}]})
        apply_patch(spec, "M", [
            {"op": "add", "path": "/comparison/variable_overrides/h/tolerance", "value": 0.01},
        ])
        data = _read_spec(spec)
        assert data["tests"][0]["comparison"]["variable_overrides"]["h"]["tolerance"] == 0.01


class TestUnknownKeyPreservation:
    """D66 invariant: patch-apply must never destroy hand-authored fields."""

    def test_preserves_entry_level_metadata(self, tmp_path):
        spec = tmp_path / "spec.json"
        _write_spec(spec, {
            "tests": [{
                "model": "M",
                "description": "A critical test — last modified 2024-03-15 by AB.",
                "metadata": {"owner": "fluid-group", "priority": "high"},
                "comparison": {"tolerance": 1e-4},
            }],
        })
        apply_patch(spec, "M", [
            {"op": "replace", "path": "/comparison/tolerance", "value": 1e-3},
        ])
        entry = _read_spec(spec)["tests"][0]
        assert entry["description"] == "A critical test — last modified 2024-03-15 by AB."
        assert entry["metadata"] == {"owner": "fluid-group", "priority": "high"}

    def test_preserves_comparison_level_unknown_keys(self, tmp_path):
        spec = tmp_path / "spec.json"
        _write_spec(spec, {
            "tests": [{
                "model": "M",
                "comparison": {
                    "tolerance": 1e-4,
                    "info": "Expect rising edge at t~2.4s",
                    "metadata": {"rationale": "Solver defaults produce ringing"},
                },
            }],
        })
        apply_patch(spec, "M", [
            {"op": "replace", "path": "/comparison/tolerance", "value": 1e-3},
        ])
        comp = _read_spec(spec)["tests"][0]["comparison"]
        assert comp["info"] == "Expect rising edge at t~2.4s"
        assert comp["metadata"] == {"rationale": "Solver defaults produce ringing"}

    def test_preserves_sibling_tests(self, tmp_path):
        spec = tmp_path / "spec.json"
        _write_spec(spec, {
            "tests": [
                {"model": "A", "comparison": {"tolerance": 1e-4}},
                {"model": "B", "comparison": {"tolerance": 1e-5},
                 "metadata": {"note": "keep me untouched"}},
                {"model": "C", "comparison": {"tolerance": 1e-6}},
            ],
        })
        apply_patch(spec, "B", [
            {"op": "replace", "path": "/comparison/tolerance", "value": 1e-3},
        ])
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
            apply_patch(spec, "M", [
                {"op": "replace", "path": "/simulation/stop_time", "value": 100},
            ])

    def test_default_allows_comparison_and_metrics(self, tmp_path):
        spec = tmp_path / "spec.json"
        _write_spec(spec, {"tests": [{"model": "M", "comparison": {"tolerance": 1e-4},
                                       "metrics": {"metric": "nrmse", "variable": "h",
                                                   "tolerance": 1e-4}}]})
        apply_patch(spec, "M", [
            {"op": "replace", "path": "/comparison/tolerance", "value": 1e-3},
            {"op": "replace", "path": "/metrics/tolerance", "value": 1e-3},
        ])
        entry = _read_spec(spec)["tests"][0]
        assert entry["comparison"]["tolerance"] == 1e-3
        assert entry["metrics"]["tolerance"] == 1e-3


class TestJsonPointerEscaping:
    def test_escape_tilde_and_slash(self, tmp_path):
        """RFC 6901: `~0` → `~`, `~1` → `/`. Order matters (~1 first)."""
        spec = tmp_path / "spec.json"
        _write_spec(spec, {"tests": [{"model": "M", "comparison": {"variable_overrides": {}}}]})
        # Variable name literally `a/b~c` escapes to `a~1b~0c`
        apply_patch(spec, "M", [
            {"op": "add",
             "path": "/comparison/variable_overrides/a~1b~0c/tolerance",
             "value": 1e-3},
        ])
        data = _read_spec(spec)
        assert data["tests"][0]["comparison"]["variable_overrides"]["a/b~c"] == {"tolerance": 1e-3}


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
            apply_patch(spec, "M", [
                {"op": "replace", "path": "/comparison/does_not_exist", "value": 1},
            ])

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


class TestMissingModel:
    def test_creates_new_entry_when_model_not_found(self, tmp_path):
        spec = tmp_path / "spec.json"
        _write_spec(spec, {"tests": [{"model": "A", "comparison": {"tolerance": 1e-4}}]})
        apply_patch(spec, "B", [
            {"op": "add", "path": "/comparison/tolerance", "value": 1e-3},
        ])
        data = _read_spec(spec)
        assert len(data["tests"]) == 2
        assert data["tests"][1]["model"] == "B"
        assert data["tests"][1]["comparison"]["tolerance"] == 1e-3

    def test_creates_spec_file_when_missing(self, tmp_path):
        spec = tmp_path / "nonexistent.json"
        apply_patch(spec, "B", [
            {"op": "add", "path": "/comparison/tolerance", "value": 1e-3},
        ])
        assert spec.exists()
        data = _read_spec(spec)
        assert data["tests"][0]["model"] == "B"
