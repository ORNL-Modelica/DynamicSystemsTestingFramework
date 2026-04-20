"""End-to-end migration + evaluation tests for the D66 baseline-role split.

Exercises the `migrate-baselines` CLI shape and confirms that tree leaves
with `against: <soft_check_name>` continue to evaluate identically before
and after the migration (i.e., the on-disk move doesn't change scoring).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from modelica_testing.config import Config
from modelica_testing.discovery.test_registry import TestModel
from modelica_testing.simulators.base import TestResult, VariableResult
from modelica_testing.storage.reference_store import ReferenceStore


def _write_legacy_ref(ref_dir: Path, test_id: str, model_id: str,
                      named: dict[str, dict]) -> Path:
    """Write a pre-D66 flat ref file with a `baselines` dict."""
    ref_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "model_id": model_id,
        "test_id": test_id,
        "status": "active",
        "date_added": "2026-01-01T00:00:00+00:00",
        "last_updated": "2026-01-01T00:00:00+00:00",
        "n_vars": 1,
        "time": [0.0, 1.0, 2.0],
        "variables": [{"index": 1, "name": "x", "values": [0.0, 1.0, 2.0]}],
        "baselines": named,
    }
    ref_file = ref_dir / f"ref_{test_id}.json"
    ref_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return ref_file


def _run_migrate(ref_root: Path, apply: bool = True) -> None:
    """Invoke the migration walk directly (bypass CLI config resolution)."""
    from modelica_testing.cli import migrate_baselines_tree
    migrate_baselines_tree(ref_root, apply)


class TestMigrateBaselines:
    def test_dry_run_does_not_move(self, tmp_path):
        ref_dir = tmp_path
        _write_legacy_ref(ref_dir, "0001", "Lib.Test", {
            "experiment": {
                "time": [0.0, 1.0],
                "variables": [{"index": 1, "name": "x", "values": [0.1, 0.9]}],
            }
        })
        _run_migrate(ref_dir, apply=False)
        # Legacy dict still in primary file, no soft_checks dir
        data = json.loads((ref_dir / "ref_0001.json").read_text())
        assert "baselines" in data
        assert not (ref_dir / "soft_checks").exists()

    def test_apply_moves_baselines_to_soft_check_subdir(self, tmp_path):
        ref_dir = tmp_path
        _write_legacy_ref(ref_dir, "0001", "Lib.Test", {
            "experiment": {
                "time": [0.0, 1.0],
                "variables": [{"index": 1, "name": "x", "values": [0.1, 0.9]}],
                "provenance": {"source": "rig-A"},
            },
            "analytical": {
                "time": [0.0, 1.0],
                "variables": [{"index": 1, "name": "x", "values": [0.2, 0.8]}],
            }
        })
        _run_migrate(ref_dir, apply=True)

        # Primary file no longer has the dict
        data = json.loads((ref_dir / "ref_0001.json").read_text())
        assert "baselines" not in data

        # Each entry landed as a standalone soft_check file
        sc_dir = ref_dir / "soft_checks" / "ref_0001"
        assert (sc_dir / "experiment.json").exists()
        assert (sc_dir / "analytical.json").exists()

        experiment = json.loads((sc_dir / "experiment.json").read_text())
        assert experiment["time"] == [0.0, 1.0]
        assert experiment["provenance"] == {"source": "rig-A"}

    def test_apply_preserves_primary_flat_fields(self, tmp_path):
        ref_dir = tmp_path
        _write_legacy_ref(ref_dir, "0001", "Lib.Test", {
            "experiment": {
                "time": [0.0, 1.0],
                "variables": [{"index": 1, "name": "x", "values": [0.1, 0.9]}],
            }
        })
        _run_migrate(ref_dir, apply=True)
        data = json.loads((ref_dir / "ref_0001.json").read_text())
        # Primary survives intact
        assert data["model_id"] == "Lib.Test"
        assert data["n_vars"] == 1
        assert data["time"] == [0.0, 1.0, 2.0]
        assert data["variables"][0]["name"] == "x"

    def test_empty_baselines_dict_is_noop(self, tmp_path):
        ref_dir = tmp_path
        _write_legacy_ref(ref_dir, "0001", "Lib.Test", {})
        _run_migrate(ref_dir, apply=True)
        assert not (ref_dir / "soft_checks").exists()

    def test_already_migrated_file_unchanged(self, tmp_path):
        """Running migration a second time is a no-op (nothing to move)."""
        ref_dir = tmp_path
        _write_legacy_ref(ref_dir, "0001", "Lib.Test", {
            "experiment": {
                "time": [0.0, 1.0],
                "variables": [{"index": 1, "name": "x", "values": [0.1, 0.9]}],
            }
        })
        _run_migrate(ref_dir, apply=True)
        before = (ref_dir / "soft_checks" / "ref_0001" / "experiment.json").read_text()
        _run_migrate(ref_dir, apply=True)
        after = (ref_dir / "soft_checks" / "ref_0001" / "experiment.json").read_text()
        assert before == after


class TestPostMigrationScoring:
    """Soft_checks targeted via `against:` must score identically to what
    the pre-migration `baselines` dict produced."""

    def test_get_baselines_returns_migrated_soft_checks(self, tmp_path, sample_models_dir):
        """After migration, `get_baselines` (the comparator lookup) sees
        the same names it did pre-migration."""
        # Config derives reference_dir = reference_root / <backend> / <os>,
        # so write the legacy ref file at the partitioned path the store
        # actually scans.
        ref_root = tmp_path / "refs"
        config = Config(source_path=sample_models_dir, reference_root=ref_root)
        ref_dir = config.reference_dir
        _write_legacy_ref(ref_dir, "0001", "Lib.Test", {
            "experiment": {
                "time": [0.0, 1.0, 2.0],
                "variables": [{"index": 1, "name": "x", "values": [0.1, 0.5, 0.9]}],
            }
        })
        store = ReferenceStore(config)

        # Pre-migration: experiment visible via flat-baselines-read
        baselines_before = store.get_baselines("Lib.Test")
        assert "experiment" in baselines_before

        # Migrate
        _run_migrate(ref_dir, apply=True)

        # Post-migration: experiment visible via soft_checks subdir
        # (fresh store to force cache reset)
        store2 = ReferenceStore(config)
        baselines_after = store2.get_baselines("Lib.Test")
        assert "experiment" in baselines_after
        # Trajectory equality — the data survived the move intact
        assert baselines_before["experiment"].time == baselines_after["experiment"].time
        assert (baselines_before["experiment"].variables[0]["values"]
                == baselines_after["experiment"].variables[0]["values"])
