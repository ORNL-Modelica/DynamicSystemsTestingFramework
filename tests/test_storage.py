"""Tests for storage: TestManifest and ReferenceStore."""

import json
from pathlib import Path

import numpy as np
import pytest

from modelica_testing.storage.reference_store import (
    TestManifest,
    ReferenceStore,
    _downsample,
)
from modelica_testing.simulators.base import TestResult, VariableResult
from modelica_testing.discovery.test_registry import TestModel
from modelica_testing.config import Config


FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# TestManifest
# ---------------------------------------------------------------------------

class TestTestManifest:
    def test_register_new(self, tmp_path):
        """Register a new model returns a numeric ID."""
        manifest = TestManifest(tmp_path / "test_manifest.json")
        test_id = manifest.register("MyLib.Examples.Test1")
        assert test_id == "0001"

    def test_register_increments(self, tmp_path):
        """Each new model gets the next sequential ID."""
        manifest = TestManifest(tmp_path / "test_manifest.json")
        id1 = manifest.register("MyLib.Test1")
        id2 = manifest.register("MyLib.Test2")
        id3 = manifest.register("MyLib.Test3")
        assert id1 == "0001"
        assert id2 == "0002"
        assert id3 == "0003"

    def test_register_idempotent(self, tmp_path):
        """Registering the same model twice returns the same ID."""
        manifest = TestManifest(tmp_path / "test_manifest.json")
        id1 = manifest.register("MyLib.Test1")
        id2 = manifest.register("MyLib.Test1")
        assert id1 == id2

    def test_get_id(self, tmp_path):
        """Look up ID by model_id."""
        manifest = TestManifest(tmp_path / "test_manifest.json")
        manifest.register("MyLib.Test1")
        assert manifest.get_id("MyLib.Test1") == "0001"
        assert manifest.get_id("MyLib.NonExistent") is None

    def test_get_model_id(self, tmp_path):
        """Look up model_id by numeric ID."""
        manifest = TestManifest(tmp_path / "test_manifest.json")
        manifest.register("MyLib.Test1")
        assert manifest.get_model_id("0001") == "MyLib.Test1"
        assert manifest.get_model_id("9999") is None

    def test_mark_obsolete(self, tmp_path):
        """Obsolete tests are excluded from lookups."""
        manifest = TestManifest(tmp_path / "test_manifest.json")
        manifest.register("MyLib.Test1")
        manifest.mark_obsolete("0001")
        assert manifest.get_id("MyLib.Test1") is None
        assert manifest.get_model_id("0001") is None

    def test_ids_never_reused(self, tmp_path):
        """New registrations after obsolete still increment."""
        manifest = TestManifest(tmp_path / "test_manifest.json")
        manifest.register("MyLib.Test1")  # 0001
        manifest.mark_obsolete("0001")
        id2 = manifest.register("MyLib.Test2")  # Should be 0002, not 0001
        assert id2 == "0002"

    def test_active_tests(self, tmp_path):
        """active_tests() returns only non-obsolete entries."""
        manifest = TestManifest(tmp_path / "test_manifest.json")
        manifest.register("MyLib.Test1")
        manifest.register("MyLib.Test2")
        manifest.register("MyLib.Test3")
        manifest.mark_obsolete("0002")

        active = manifest.active_tests()
        assert len(active) == 2
        assert "0001" in active
        assert "0003" in active
        assert "0002" not in active

    def test_persistence(self, tmp_path):
        """Manifest persists to disk and survives reload."""
        path = tmp_path / "test_manifest.json"
        m1 = TestManifest(path)
        m1.register("MyLib.Test1")
        m1.register("MyLib.Test2")

        # Create new instance (simulates restart)
        m2 = TestManifest(path)
        assert m2.get_id("MyLib.Test1") == "0001"
        assert m2.get_id("MyLib.Test2") == "0002"

    def test_ref_filename(self):
        """Static method generates correct filename."""
        assert TestManifest.ref_filename("0001") == "ref_0001.json"
        assert TestManifest.ref_filename("0042") == "ref_0042.json"


# ---------------------------------------------------------------------------
# Downsampling
# ---------------------------------------------------------------------------

class TestDownsample:
    def test_short_series_unchanged(self):
        """Series shorter than max points passes through."""
        time = np.array([0.0, 1.0, 2.0])
        values = np.array([0.0, 1.0, 2.0])
        t_out, v_out = _downsample(time, values, max_points=100)
        assert len(t_out) == 3

    def test_preserves_endpoints(self):
        """Downsampled series keeps first and last points."""
        time = np.linspace(0, 100, 10000)
        values = np.sin(time)
        t_out, v_out = _downsample(time, values, max_points=500)
        assert t_out[0] == 0.0
        assert t_out[-1] == 100.0
        assert len(t_out) <= 500

    def test_preserves_events(self):
        """Duplicate time points (events) are preserved during downsampling."""
        # Create a series with an event at t=5.0
        t1 = np.linspace(0, 5, 500)
        t2 = np.linspace(5, 10, 500)
        time = np.concatenate([t1, t2])  # t=5.0 appears twice
        values = np.concatenate([np.sin(t1), np.sin(t2) + 1])

        t_out, v_out = _downsample(time, values, max_points=200)

        # The duplicate at t=5.0 should be preserved
        t5_indices = [i for i, t in enumerate(t_out) if abs(t - 5.0) < 1e-10]
        assert len(t5_indices) >= 2, "Event boundary at t=5.0 should be preserved"


# ---------------------------------------------------------------------------
# ReferenceStore (store + load round-trip)
# ---------------------------------------------------------------------------

class TestReferenceStore:
    def _make_test_model(self, model_id="ModelicaTestingLib.Examples.Test1"):
        """Create a minimal TestModel for testing."""
        return TestModel(
            model_id=model_id,
            mo_file=Path(""),
            package_path="TestLib.Examples",
            short_name=model_id.rsplit(".", 1)[-1],
            n_vars=2,
            variable_patterns=[],
            source="unit_tests",
        )

    def _make_test_result(self, model_id="ModelicaTestingLib.Examples.Test1"):
        """Create a TestResult with synthetic data."""
        time = np.linspace(0, 10, 101)
        return TestResult(
            model_id=model_id,
            success=True,
            variables=[
                VariableResult(index=1, time=time, values=np.sin(time), name="x"),
                VariableResult(index=2, time=time, values=np.cos(time), name="y"),
            ],
            diagnostics=[
                VariableResult(index=1, time=time, values=np.linspace(0, 1, 101), name="CPUtime"),
            ],
            statistics={"CPUtime": 1.0, "EventCounter": 5},
        )

    def test_store_and_load(self, sample_models_dir, tmp_path):
        """Store a reference and load it back."""
        config = Config(
            package_path=sample_models_dir,
            reference_root=tmp_path / "refs",
        )
        store = ReferenceStore(config)
        test = self._make_test_model()
        result = self._make_test_result()

        stored = store.store_reference(test, result)
        assert stored is True

        # Load it back
        ref = store.get_reference(test.model_id)
        assert ref is not None
        assert ref["model_id"] == test.model_id
        assert ref["n_vars"] == 2
        assert len(ref["variables"]) == 2
        assert ref["variables"][0]["name"] == "x"

    def test_diagnostics_stored(self, sample_models_dir, tmp_path):
        """Diagnostics section is stored in reference JSON."""
        config = Config(
            package_path=sample_models_dir,
            reference_root=tmp_path / "refs",
        )
        store = ReferenceStore(config)
        test = self._make_test_model()
        result = self._make_test_result()

        store.store_reference(test, result)
        ref = store.get_reference(test.model_id)

        assert "diagnostics" in ref
        assert len(ref["diagnostics"]) == 1
        assert ref["diagnostics"][0]["name"] == "CPUtime"

    def test_statistics_stored(self, sample_models_dir, tmp_path):
        """Statistics with diagnostic finals are stored."""
        config = Config(
            package_path=sample_models_dir,
            reference_root=tmp_path / "refs",
        )
        store = ReferenceStore(config)
        test = self._make_test_model()
        result = self._make_test_result()

        store.store_reference(test, result)
        ref = store.get_reference(test.model_id)

        assert ref["statistics"]["CPUtime"] == 1.0
        assert ref["statistics"]["EventCounter"] == 5

    def test_manifest_created(self, sample_models_dir, tmp_path):
        """Storing a reference creates the manifest."""
        config = Config(
            package_path=sample_models_dir,
            reference_root=tmp_path / "refs",
        )
        store = ReferenceStore(config)
        test = self._make_test_model()
        result = self._make_test_result()

        store.store_reference(test, result)
        assert config.manifest_file.exists()

    def test_failed_result_not_stored(self, sample_models_dir, tmp_path):
        """Failed simulations are not stored as references."""
        config = Config(
            package_path=sample_models_dir,
            reference_root=tmp_path / "refs",
        )
        store = ReferenceStore(config)
        test = self._make_test_model()
        result = TestResult(model_id=test.model_id, success=False)

        stored = store.store_reference(test, result)
        assert stored is False

    def test_json_field_order(self, sample_models_dir, tmp_path):
        """Metadata fields come before data fields in reference JSON."""
        config = Config(
            package_path=sample_models_dir,
            reference_root=tmp_path / "refs",
        )
        store = ReferenceStore(config)
        test = self._make_test_model()
        result = self._make_test_result()

        store.store_reference(test, result)

        # Read the raw JSON to check key order
        ref_files = list(config.reference_dir.glob("ref_*.json"))
        assert len(ref_files) == 1
        data = json.loads(ref_files[0].read_text())
        keys = list(data.keys())

        # model_id and test_id should come before time and variables
        assert keys.index("model_id") < keys.index("time")
        assert keys.index("test_id") < keys.index("variables")
