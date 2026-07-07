"""Tests for the D66 baseline-role split — primary / companion / soft_check.

Storage-layer tests for :class:`ReferenceStore` soft_check and companion
APIs. Covers the three-role separation at the persistence level:
primary (existing), soft_checks (scored with ``against:`` inside ``warn``),
companions (plot-only overlays, never scored against).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from dstf.config import Config
from dstf.discovery.test_registry import TestModel
from dstf.simulators.base import TestResult, VariableResult
from dstf.storage.reference_store import (
    ReferenceStore,
)


def _make_test_model(model_id="Lib.Test1"):
    return TestModel(
        model_id=model_id,
        source_file=Path(""),
        source_package="Lib",
        short_name=model_id.rsplit(".", 1)[-1],
        n_vars=1,
        variable_patterns=[],
        source="unit_tests",
    )


def _make_test_result(model_id="Lib.Test1"):
    time = np.linspace(0, 10, 11)
    return TestResult(
        model_id=model_id,
        success=True,
        variables=[VariableResult(index=1, time=time, values=np.sin(time), name="x")],
    )


@pytest.fixture
def store_with_primary(sample_models_dir, tmp_path):
    """A ReferenceStore with one test's primary baseline already stored."""
    config = Config(source_path=sample_models_dir, reference_root=tmp_path / "refs")
    store = ReferenceStore(config)
    test = _make_test_model()
    store.store_reference(test, _make_test_result())
    return store, test


# ---------------------------------------------------------------------------
# Soft_checks
# ---------------------------------------------------------------------------


class TestSoftCheckStorage:
    def test_empty_by_default(self, store_with_primary):
        store, test = store_with_primary
        assert store.get_soft_checks(test.model_id) == {}

    def test_add_creates_file_in_subdir(self, store_with_primary):
        store, test = store_with_primary
        ok = store.add_soft_check(
            test.model_id,
            "experiment",
            time=[0.0, 1.0, 2.0],
            variables=[{"index": 1, "name": "x", "values": [0.0, 0.5, 0.9]}],
            provenance={"source": "rig-A"},
        )
        assert ok is True

        # Disk layout: soft_checks/ref_NNNN/<name>.json
        sc_file = store.ref_dir / "soft_checks" / "ref_0001" / "experiment.json"
        assert sc_file.exists()
        data = json.loads(sc_file.read_text())
        assert data["time"] == [0.0, 1.0, 2.0]
        assert data["provenance"] == {"source": "rig-A"}

    def test_get_roundtrip(self, store_with_primary):
        store, test = store_with_primary
        store.add_soft_check(
            test.model_id,
            "cross-check",
            time=[0.0, 1.0],
            variables=[{"index": 1, "name": "x", "values": [0.0, 1.0]}],
        )
        soft_checks = store.get_soft_checks(test.model_id)
        assert "cross-check" in soft_checks
        assert soft_checks["cross-check"].time == [0.0, 1.0]

    def test_get_baselines_includes_soft_checks(self, store_with_primary):
        """The combined baseline view (for comparator) covers primary + soft_checks."""
        store, test = store_with_primary
        store.add_soft_check(
            test.model_id,
            "experiment",
            time=[0.0, 1.0],
            variables=[{"index": 1, "name": "x", "values": [0.0, 1.0]}],
        )
        baselines = store.get_baselines(test.model_id)
        assert set(baselines) == {"primary", "experiment"}

    def test_remove(self, store_with_primary):
        store, test = store_with_primary
        store.add_soft_check(
            test.model_id,
            "foo",
            time=[0.0],
            variables=[{"index": 1, "name": "x", "values": [0.0]}],
        )
        assert store.remove_soft_check(test.model_id, "foo") is True
        assert store.get_soft_checks(test.model_id) == {}
        # Removing again returns False
        assert store.remove_soft_check(test.model_id, "foo") is False

    def test_overwrite_false_refuses_existing(self, store_with_primary):
        store, test = store_with_primary
        store.add_soft_check(
            test.model_id,
            "foo",
            time=[0.0],
            variables=[{"index": 1, "name": "x", "values": [0.0]}],
        )
        ok = store.add_soft_check(
            test.model_id,
            "foo",
            time=[1.0],
            variables=[{"index": 1, "name": "x", "values": [1.0]}],
            overwrite=False,
        )
        assert ok is False
        # Original still there
        assert store.get_soft_checks(test.model_id)["foo"].time == [0.0]

    def test_primary_name_rejected(self, store_with_primary):
        store, test = store_with_primary
        with pytest.raises(ValueError, match="primary"):
            store.add_soft_check(
                test.model_id,
                "primary",
                time=[0.0],
                variables=[{"index": 1, "name": "x", "values": [0.0]}],
            )

    def test_empty_name_rejected(self, store_with_primary):
        store, test = store_with_primary
        with pytest.raises(ValueError, match="non-empty"):
            store.add_soft_check(
                test.model_id,
                "",
                time=[0.0],
                variables=[{"index": 1, "name": "x", "values": [0.0]}],
            )

    def test_without_primary_raises(self, sample_models_dir, tmp_path):
        config = Config(source_path=sample_models_dir, reference_root=tmp_path / "refs")
        store = ReferenceStore(config)
        with pytest.raises(FileNotFoundError, match="primary"):
            store.add_soft_check(
                "Nonexistent.Test",
                "foo",
                time=[0.0],
                variables=[{"index": 1, "name": "x", "values": [0.0]}],
            )


# ---------------------------------------------------------------------------
# Companions
# ---------------------------------------------------------------------------


class TestCompanionStorage:
    def test_empty_by_default(self, store_with_primary):
        store, test = store_with_primary
        assert store.get_companions(test.model_id) == {}

    def test_add_external_is_pointer_only(self, store_with_primary, tmp_path):
        store, test = store_with_primary
        external = tmp_path / "rig_data.csv"
        external.write_text("time,value\n0,0\n1,1\n")
        ok = store.add_companion(
            test.model_id,
            "rig",
            path=external,
            provenance={"campaign": "2026-Q1"},
        )
        assert ok is True

        meta_file = store.ref_dir / "companions" / "ref_0001" / "rig.json"
        assert meta_file.exists()
        # No data copied for external kind
        data_file = store.ref_dir / "companions" / "ref_0001" / "rig.csv"
        assert not data_file.exists()

        companions = store.get_companions(test.model_id)
        assert "rig" in companions
        assert companions["rig"].kind == "external"
        assert companions["rig"].format == "csv"  # inferred from extension
        # review 2026-07-06 (finding 34): stored path is the resolved absolute
        # form (was: verbatim), so the loader and the store agree.
        assert companions["rig"].path == str(external.resolve())
        assert companions["rig"].provenance == {"campaign": "2026-Q1"}

    def test_add_external_missing_path_rejected(self, store_with_primary):
        """review 2026-07-06 (finding 34): companions are user-registered
        files, so a wrong path fails fast at registration. Graceful
        degradation still applies at LOAD time if the file moves later
        (see test_overlay_loader.py)."""
        store, test = store_with_primary
        missing = Path("/nonexistent/path/data.csv")
        with pytest.raises(FileNotFoundError):
            store.add_companion(test.model_id, "ghost", path=missing)
        assert "ghost" not in store.get_companions(test.model_id)

    def test_freeze_copies_data_beside_metadata(self, store_with_primary, tmp_path):
        store, test = store_with_primary
        external = tmp_path / "rig_data.csv"
        external.write_text("time,value\n0,0\n1,1\n")
        store.add_companion(test.model_id, "rig", path=external)

        assert store.freeze_companion(test.model_id, "rig") is True

        co_dir = store.ref_dir / "companions" / "ref_0001"
        assert (co_dir / "rig.csv").exists()
        assert (co_dir / "rig.csv").read_text() == "time,value\n0,0\n1,1\n"

        # Metadata flipped to frozen
        companions = store.get_companions(test.model_id)
        assert companions["rig"].kind == "frozen"
        assert companions["rig"].data_file == "rig.csv"
        assert companions["rig"].path is None

    def test_freeze_already_frozen_is_noop(self, store_with_primary, tmp_path):
        store, test = store_with_primary
        external = tmp_path / "data.csv"
        external.write_text("x\n1\n")
        store.add_companion(test.model_id, "c1", path=external)
        store.freeze_companion(test.model_id, "c1")
        # Second freeze returns False (no-op)
        assert store.freeze_companion(test.model_id, "c1") is False

    def test_freeze_missing_source_raises(self, store_with_primary, tmp_path):
        # review 2026-07-06 (finding 34): add_companion now requires the file
        # to exist, so simulate the file moving away *after* registration.
        store, test = store_with_primary
        src = tmp_path / "vanishing.csv"
        src.write_text("x\n1\n")
        store.add_companion(test.model_id, "ghost", path=src)
        src.unlink()
        with pytest.raises(FileNotFoundError):
            store.freeze_companion(test.model_id, "ghost")

    def test_remove_frozen_deletes_data_file(self, store_with_primary, tmp_path):
        store, test = store_with_primary
        external = tmp_path / "data.csv"
        external.write_text("x\n1\n")
        store.add_companion(test.model_id, "c1", path=external)
        store.freeze_companion(test.model_id, "c1")

        assert store.remove_companion(test.model_id, "c1") is True
        co_dir = store.ref_dir / "companions" / "ref_0001"
        # Parent dir cleaned up after last entry removed
        assert not co_dir.exists()

    def test_format_inference_json(self, store_with_primary, tmp_path):
        store, test = store_with_primary
        src = tmp_path / "analytical.json"
        src.write_text("{}")
        store.add_companion(test.model_id, "analytical", path=src)
        assert store.get_companions(test.model_id)["analytical"].format == "json"

    def test_explicit_format_overrides_inference(self, store_with_primary, tmp_path):
        store, test = store_with_primary
        src = tmp_path / "data.dat"
        src.write_text("")
        store.add_companion(test.model_id, "c1", path=src, format="csv")
        assert store.get_companions(test.model_id)["c1"].format == "csv"

    def test_primary_name_rejected(self, store_with_primary, tmp_path):
        store, test = store_with_primary
        with pytest.raises(ValueError, match="primary"):
            store.add_companion(test.model_id, "primary", path=tmp_path / "x")


# ---------------------------------------------------------------------------
# Cross-role namespace
# ---------------------------------------------------------------------------


class TestRoleNamespace:
    def test_soft_check_rejects_companion_name_collision(
        self, store_with_primary, tmp_path
    ):
        store, test = store_with_primary
        src = tmp_path / "data.csv"
        src.write_text("x\n1\n")
        store.add_companion(test.model_id, "shared-name", path=src)
        with pytest.raises(ValueError, match="companion"):
            store.add_soft_check(
                test.model_id,
                "shared-name",
                time=[0.0],
                variables=[{"index": 1, "name": "x", "values": [0.0]}],
            )

    def test_companion_rejects_soft_check_name_collision(
        self, store_with_primary, tmp_path
    ):
        store, test = store_with_primary
        store.add_soft_check(
            test.model_id,
            "shared-name",
            time=[0.0],
            variables=[{"index": 1, "name": "x", "values": [0.0]}],
        )
        src = tmp_path / "data.csv"
        src.write_text("x\n1\n")
        with pytest.raises(ValueError, match="soft_check"):
            store.add_companion(test.model_id, "shared-name", path=src)

    def test_companions_not_returned_by_get_baselines(
        self, store_with_primary, tmp_path
    ):
        """Companions are plot-only — never visible to the comparator via
        get_baselines (which feeds the tree's ``against:`` lookup)."""
        store, test = store_with_primary
        src = tmp_path / "data.csv"
        src.write_text("x\n1\n")
        store.add_companion(test.model_id, "rig", path=src)
        baselines = store.get_baselines(test.model_id)
        assert "rig" not in baselines
        assert set(baselines) == {"primary"}
