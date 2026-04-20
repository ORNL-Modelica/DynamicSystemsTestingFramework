"""End-to-end cmd_spec_update tests — both legacy dict and new patch format."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest


def _run_spec_update(spec_path: Path, update_json_path: Path) -> int:
    """Invoke cmd_spec_update directly, bypassing arg parsing."""
    from modelica_testing.cli import cmd_spec_update

    # Use the spec's parent dir as reference_root so _build_config can
    # locate testing.json. Write a minimal one.
    cfg_dir = spec_path.parent
    if not (cfg_dir / "testing.json").exists():
        # source_type=fmu skips Modelica package.mo auto-discovery that
        # would otherwise walk out of the temp directory.
        (cfg_dir / "testing.json").write_text(
            json.dumps({
                "source_type": "fmu",
                "simulator": "FMPy",
                "reference_root": ".",
            }),
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
        spec.write_text(json.dumps({
            "tests": [{
                "model": "MyLib.A",
                "comparison": {"tolerance": 1e-4},
                "metadata": {"owner": "unit"},
            }],
        }), encoding="utf-8")

        patch = tmp_path / "spec_patch.json"
        patch.write_text(json.dumps({
            "model": "MyLib.A",
            "patch": [
                {"op": "replace", "path": "/comparison/tolerance", "value": 1e-3},
            ],
        }), encoding="utf-8")

        rc = _run_spec_update(spec, patch)
        assert rc == 0

        data = json.loads(spec.read_text())
        assert data["tests"][0]["comparison"]["tolerance"] == 1e-3
        # Metadata survived
        assert data["tests"][0]["metadata"] == {"owner": "unit"}

    def test_patch_whitelist_rejection_exits_nonzero(self, tmp_path):
        spec = tmp_path / "test_spec.json"
        spec.write_text(json.dumps({
            "tests": [{"model": "M", "simulation": {"stop_time": 10}}],
        }), encoding="utf-8")

        patch = tmp_path / "p.json"
        patch.write_text(json.dumps({
            "model": "M",
            "patch": [{"op": "replace", "path": "/simulation/stop_time", "value": 100}],
        }), encoding="utf-8")

        rc = _run_spec_update(spec, patch)
        assert rc == 1
        # Spec file should be unchanged
        data = json.loads(spec.read_text())
        assert data["tests"][0]["simulation"]["stop_time"] == 10


class TestLegacyDictFormat:
    """Legacy flat-dict format still works for a transition cycle."""

    def test_legacy_dict_still_honored(self, tmp_path):
        spec = tmp_path / "test_spec.json"
        spec.write_text(json.dumps({
            "tests": [{"model": "MyLib.A", "comparison": {"tolerance": 1e-4}}],
        }), encoding="utf-8")

        legacy = tmp_path / "legacy.json"
        legacy.write_text(json.dumps({
            "model": "MyLib.A",
            "comparison": {"tolerance": 1e-3},
        }), encoding="utf-8")

        rc = _run_spec_update(spec, legacy)
        assert rc == 0
        data = json.loads(spec.read_text())
        assert data["tests"][0]["comparison"]["tolerance"] == 1e-3
