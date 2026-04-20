"""Tests for storage: RefIndex and ReferenceStore."""

import json
from pathlib import Path

import numpy as np
import pytest

from modelica_testing.storage.reference_store import (
    Baseline,
    PRIMARY_BASELINE,
    RefIndex,
    ReferenceStore,
    _downsample,
    _extract_baselines,
)
from modelica_testing.simulators.base import TestResult, VariableResult
from modelica_testing.discovery.test_registry import TestModel
from modelica_testing.config import Config


FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# RefIndex (in-memory index built from ref files)
# ---------------------------------------------------------------------------

class TestRefIndex:
    def _write_ref(self, ref_dir, test_id, model_id, status="active"):
        """Helper to write a minimal ref file."""
        ref_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "model_id": model_id,
            "test_id": test_id,
            "status": status,
            "date_added": "2026-01-01T00:00:00+00:00",
            "last_updated": "2026-01-01T00:00:00+00:00",
            "n_vars": 0,
            "time": [],
            "variables": [],
        }
        path = ref_dir / f"ref_{test_id}.json"
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def test_scan_empty_dir(self, tmp_path):
        """Empty directory produces empty index."""
        index = RefIndex(tmp_path)
        assert index.active_tests() == {}

    def test_scan_nonexistent_dir(self, tmp_path):
        """Nonexistent directory produces empty index."""
        index = RefIndex(tmp_path / "nonexistent")
        assert index.active_tests() == {}

    def test_scan_finds_refs(self, tmp_path):
        """Scanning finds ref files and builds the index."""
        self._write_ref(tmp_path, "0001", "MyLib.Test1")
        self._write_ref(tmp_path, "0002", "MyLib.Test2")
        index = RefIndex(tmp_path)
        active = index.active_tests()
        assert len(active) == 2
        assert active["0001"] == "MyLib.Test1"
        assert active["0002"] == "MyLib.Test2"

    def test_get_id(self, tmp_path):
        """Look up ID by model_id."""
        self._write_ref(tmp_path, "0001", "MyLib.Test1")
        index = RefIndex(tmp_path)
        assert index.get_id("MyLib.Test1") == "0001"
        assert index.get_id("NonExistent") is None

    def test_get_model_id(self, tmp_path):
        """Look up model_id by ID."""
        self._write_ref(tmp_path, "0001", "MyLib.Test1")
        index = RefIndex(tmp_path)
        assert index.get_model_id("0001") == "MyLib.Test1"
        assert index.get_model_id("9999") is None

    def test_obsolete_excluded_from_active(self, tmp_path):
        """Obsolete tests are excluded from active_tests."""
        self._write_ref(tmp_path, "0001", "MyLib.Test1")
        self._write_ref(tmp_path, "0002", "MyLib.Test2", status="obsolete")
        index = RefIndex(tmp_path)
        active = index.active_tests()
        assert len(active) == 1
        assert "0001" in active

    def test_obsolete_excluded_from_get_model_id(self, tmp_path):
        """Obsolete tests return None from get_model_id."""
        self._write_ref(tmp_path, "0001", "MyLib.Test1", status="obsolete")
        index = RefIndex(tmp_path)
        assert index.get_model_id("0001") is None

    def test_next_id_empty(self, tmp_path):
        """Next ID on empty dir is 0001."""
        index = RefIndex(tmp_path)
        assert index.next_id() == "0001"

    def test_next_id_increments(self, tmp_path):
        """Next ID is one more than the highest existing."""
        self._write_ref(tmp_path, "0001", "MyLib.Test1")
        self._write_ref(tmp_path, "0003", "MyLib.Test3")
        index = RefIndex(tmp_path)
        assert index.next_id() == "0004"

    def test_next_id_includes_obsolete(self, tmp_path):
        """Next ID counts obsolete IDs (never reuse)."""
        self._write_ref(tmp_path, "0001", "MyLib.Test1")
        self._write_ref(tmp_path, "0002", "MyLib.Test2", status="obsolete")
        index = RefIndex(tmp_path)
        assert index.next_id() == "0003"

    def test_register_new(self, tmp_path):
        """Register a new model returns next ID."""
        index = RefIndex(tmp_path)
        test_id = index.register("MyLib.Test1")
        assert test_id == "0001"

    def test_register_idempotent(self, tmp_path):
        """Registering the same model twice returns the same ID."""
        self._write_ref(tmp_path, "0001", "MyLib.Test1")
        index = RefIndex(tmp_path)
        assert index.register("MyLib.Test1") == "0001"

    def test_all_tests_includes_all_statuses(self, tmp_path):
        """all_tests() returns everything including obsolete and skip."""
        self._write_ref(tmp_path, "0001", "MyLib.Test1")
        self._write_ref(tmp_path, "0002", "MyLib.Test2", status="obsolete")
        self._write_ref(tmp_path, "0003", "MyLib.Test3", status="skip")
        index = RefIndex(tmp_path)
        all_t = index.all_tests()
        assert len(all_t) == 3

    def test_ref_filename(self):
        """Static method generates correct filename."""
        assert RefIndex.ref_filename("0001") == "ref_0001.json"
        assert RefIndex.ref_filename("0042") == "ref_0042.json"


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
        t1 = np.linspace(0, 5, 500)
        t2 = np.linspace(5, 10, 500)
        time = np.concatenate([t1, t2])
        values = np.concatenate([np.sin(t1), np.sin(t2) + 1])

        t_out, v_out = _downsample(time, values, max_points=200)
        t5_indices = [i for i, t in enumerate(t_out) if abs(t - 5.0) < 1e-10]
        assert len(t5_indices) >= 2, "Event boundary at t=5.0 should be preserved"


# ---------------------------------------------------------------------------
# ReferenceStore (store + load round-trip)
# ---------------------------------------------------------------------------

class TestReferenceStore:
    def _make_test_model(self, model_id="ModelicaTestingLib.Examples.Test1"):
        return TestModel(
            model_id=model_id,
            source_file=Path(""),
            source_package="ModelicaTestingLib.Examples",
            short_name=model_id.rsplit(".", 1)[-1],
            n_vars=2,
            variable_patterns=[],
            source="unit_tests",
        )

    def _make_test_result(self, model_id="ModelicaTestingLib.Examples.Test1"):
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
            source_path=sample_models_dir,
            reference_root=tmp_path / "refs",
        )
        store = ReferenceStore(config)
        test = self._make_test_model()
        result = self._make_test_result()

        stored = store.store_reference(test, result)
        assert stored is True

        ref = store.get_reference(test.model_id)
        assert ref is not None
        assert ref["model_id"] == test.model_id
        assert ref["n_vars"] == 2
        assert len(ref["variables"]) == 2
        assert ref["variables"][0]["name"] == "x"

    def test_status_and_dates_stored(self, sample_models_dir, tmp_path):
        """status, date_added, and last_updated are in stored reference."""
        config = Config(
            source_path=sample_models_dir,
            reference_root=tmp_path / "refs",
        )
        store = ReferenceStore(config)
        test = self._make_test_model()
        result = self._make_test_result()

        store.store_reference(test, result)
        ref = store.get_reference(test.model_id)

        assert ref["status"] == "active"
        assert "date_added" in ref
        assert "last_updated" in ref

    def test_date_added_preserved_on_update(self, sample_models_dir, tmp_path):
        """date_added stays the same when a reference is updated."""
        config = Config(
            source_path=sample_models_dir,
            reference_root=tmp_path / "refs",
        )
        store = ReferenceStore(config)
        test = self._make_test_model()
        result = self._make_test_result()

        store.store_reference(test, result)
        ref1 = store.get_reference(test.model_id)
        original_date = ref1["date_added"]

        # Store again (update)
        store.store_reference(test, result)
        ref2 = store.get_reference(test.model_id)
        assert ref2["date_added"] == original_date

    def test_diagnostics_stored(self, sample_models_dir, tmp_path):
        """Diagnostics are stored as a scalar summary, not a full trajectory.

        Full trajectories of nondeterministic values (CPUtime) were
        producing spurious git diffs on every re-accept — the summary
        keeps the regression-meaningful data (final, min, max) without
        bloating the baseline file.
        """
        config = Config(
            source_path=sample_models_dir,
            reference_root=tmp_path / "refs",
        )
        store = ReferenceStore(config)
        test = self._make_test_model()
        result = self._make_test_result()

        store.store_reference(test, result)
        ref = store.get_reference(test.model_id)

        assert "diagnostics" in ref
        assert len(ref["diagnostics"]) == 1
        diag = ref["diagnostics"][0]
        assert diag["name"] == "CPUtime"
        # Summary shape: {final, min, max}; no 'values' trajectory.
        assert diag["final"] == pytest.approx(1.0)  # last value of linspace(0,1,101)
        assert diag["min"] == pytest.approx(0.0)
        assert diag["max"] == pytest.approx(1.0)
        assert "values" not in diag

    def test_statistics_stored(self, sample_models_dir, tmp_path):
        """Statistics with diagnostic finals are stored."""
        config = Config(
            source_path=sample_models_dir,
            reference_root=tmp_path / "refs",
        )
        store = ReferenceStore(config)
        test = self._make_test_model()
        result = self._make_test_result()

        store.store_reference(test, result)
        ref = store.get_reference(test.model_id)

        assert ref["statistics"]["CPUtime"] == 1.0
        assert ref["statistics"]["EventCounter"] == 5

    def test_failed_result_not_stored(self, sample_models_dir, tmp_path):
        """Failed simulations are not stored as references."""
        config = Config(
            source_path=sample_models_dir,
            reference_root=tmp_path / "refs",
        )
        store = ReferenceStore(config)
        test = self._make_test_model()
        result = TestResult(model_id=test.model_id, success=False)

        stored = store.store_reference(test, result)
        assert stored is False

    def test_set_status(self, sample_models_dir, tmp_path):
        """set_status updates the status field in the ref file."""
        config = Config(
            source_path=sample_models_dir,
            reference_root=tmp_path / "refs",
        )
        store = ReferenceStore(config)
        test = self._make_test_model()
        result = self._make_test_result()

        store.store_reference(test, result)
        store.set_status(test.model_id, "skip")

        ref = store.get_reference(test.model_id)
        assert ref["status"] == "skip"

    def test_cleanup_obsolete(self, sample_models_dir, tmp_path):
        """cleanup_obsolete removes files with obsolete status."""
        config = Config(
            source_path=sample_models_dir,
            reference_root=tmp_path / "refs",
        )
        store = ReferenceStore(config)
        test = self._make_test_model()
        result = self._make_test_result()

        store.store_reference(test, result)
        store.set_status(test.model_id, "obsolete")

        removed = store.cleanup_obsolete()
        assert removed == 1

        ref_files = list(config.reference_dir.glob("ref_*.json"))
        assert len(ref_files) == 0

    def test_number_of_intervals_auto_derived(self, sample_models_dir, tmp_path):
        """numberOfIntervals is derived from result when not explicitly set."""
        config = Config(
            source_path=sample_models_dir,
            reference_root=tmp_path / "refs",
        )
        store = ReferenceStore(config)
        test = self._make_test_model()
        # test has number_of_intervals=None and output_interval=None
        assert test.number_of_intervals is None
        assert test.output_interval is None

        result = self._make_test_result()  # 101 time points
        store.store_reference(test, result)
        ref = store.get_reference(test.model_id)

        # Should derive: 101 unique times - 1 = 100 intervals
        assert ref["simulation"]["number_of_intervals"] == 100
        assert ref["simulation"]["output_interval"] is None

    def test_output_interval_preserved(self, sample_models_dir, tmp_path):
        """output_interval is stored when explicitly set."""
        config = Config(
            source_path=sample_models_dir,
            reference_root=tmp_path / "refs",
        )
        store = ReferenceStore(config)
        test = self._make_test_model()
        test.output_interval = 0.5
        result = self._make_test_result()

        store.store_reference(test, result)
        ref = store.get_reference(test.model_id)

        assert ref["simulation"]["output_interval"] == 0.5
        assert ref["simulation"]["number_of_intervals"] is None

    def test_json_field_order(self, sample_models_dir, tmp_path):
        """Metadata fields come before data fields in reference JSON."""
        config = Config(
            source_path=sample_models_dir,
            reference_root=tmp_path / "refs",
        )
        store = ReferenceStore(config)
        test = self._make_test_model()
        result = self._make_test_result()

        store.store_reference(test, result)

        ref_files = list(config.reference_dir.glob("ref_*.json"))
        assert len(ref_files) == 1
        data = json.loads(ref_files[0].read_text())
        keys = list(data.keys())

        assert keys.index("model_id") < keys.index("time")
        assert keys.index("test_id") < keys.index("variables")
        assert keys.index("status") < keys.index("time")
        assert keys.index("date_added") < keys.index("time")

    def test_index_rebuilt_from_files(self, sample_models_dir, tmp_path):
        """A new ReferenceStore instance rebuilds the index from ref files."""
        config = Config(
            source_path=sample_models_dir,
            reference_root=tmp_path / "refs",
        )
        store1 = ReferenceStore(config)
        test = self._make_test_model()
        result = self._make_test_result()
        store1.store_reference(test, result)

        # Create a new store (simulates restart)
        store2 = ReferenceStore(config)
        ref = store2.get_reference(test.model_id)
        assert ref is not None
        assert ref["model_id"] == test.model_id


# ---------------------------------------------------------------------------
# Baseline view (Phase 1.7a: read-side unified interface)
# ---------------------------------------------------------------------------

class TestBaselineView:
    """Legacy flat files and forward multi-baseline files must present the
    same ``Baseline`` view to readers."""

    def test_extract_baselines_legacy_flat(self):
        """Legacy flat schema → single synthetic ``primary`` baseline."""
        flat = {
            "model_id": "MyLib.Test1",
            "date_added": "2026-01-15T10:00:00+00:00",
            "last_updated": "2026-02-20T12:30:00+00:00",
            "simulation": {"stop_time": 10.0, "tolerance": 1e-4},
            "comparison": {"tolerance": 0.01},
            "statistics": {"CPUtime": 2.5},
            "time": [0.0, 1.0, 2.0],
            "variables": [{"index": 1, "name": "x", "values": [0.0, 1.0, 2.0]}],
            "diagnostics": [{"name": "CPUtime", "values": [0.1, 0.2, 0.3]}],
        }
        baselines = _extract_baselines(flat)
        assert list(baselines.keys()) == [PRIMARY_BASELINE]
        b = baselines[PRIMARY_BASELINE]
        assert isinstance(b, Baseline)
        assert b.time == [0.0, 1.0, 2.0]
        assert b.simulation["stop_time"] == 10.0
        assert b.comparison["tolerance"] == 0.01
        assert b.statistics["CPUtime"] == 2.5
        assert len(b.variables) == 1
        assert len(b.diagnostics) == 1
        # Provenance synthesized from file metadata
        assert b.provenance["origin"] == "legacy-flat"
        assert b.provenance["captured_at"] == "2026-01-15T10:00:00+00:00"
        assert b.provenance["last_updated"] == "2026-02-20T12:30:00+00:00"

    def test_extract_baselines_legacy_no_last_updated_diff(self):
        """When last_updated == date_added, don't duplicate in provenance."""
        flat = {
            "date_added": "2026-01-15T10:00:00+00:00",
            "last_updated": "2026-01-15T10:00:00+00:00",
            "time": [], "variables": [],
        }
        b = _extract_baselines(flat)[PRIMARY_BASELINE]
        assert "captured_at" in b.provenance
        assert "last_updated" not in b.provenance

    def test_extract_baselines_hybrid_schema(self):
        """Hybrid: flat primary + optional additional baselines under ``baselines`` key."""
        data = {
            "model_id": "MyLib.Test1",
            "date_added": "2026-01-15T10:00:00+00:00",
            # flat fields are the primary baseline
            "simulation": {"stop_time": 10.0},
            "time": [0.0, 1.0],
            "variables": [{"index": 1, "name": "x", "values": [0.0, 1.0]}],
            # additional baselines live under this key
            "baselines": {
                "experiment": {
                    "provenance": {
                        "origin": "rig-run-2024-03-15",
                        "citation": "Internal report XYZ",
                    },
                    "time": [0.0, 0.5, 1.0],
                    "variables": [{"index": 1, "name": "x", "values": [0.0, 0.6, 1.05]}],
                },
                "analytical": {
                    "provenance": {"origin": "closed-form"},
                    "time": [0.0, 1.0],
                    "variables": [{"index": 1, "name": "x", "values": [0.0, 1.0]}],
                },
            },
        }
        baselines = _extract_baselines(data)
        assert set(baselines.keys()) == {"primary", "experiment", "analytical"}
        # Primary comes from the flat top-level fields
        assert baselines["primary"].simulation["stop_time"] == 10.0
        assert baselines["primary"].time == [0.0, 1.0]
        assert baselines["primary"].provenance["origin"] == "legacy-flat"
        # Additional baselines come from the nested map
        assert baselines["experiment"].provenance["citation"] == "Internal report XYZ"
        assert baselines["experiment"].time == [0.0, 0.5, 1.0]
        assert baselines["analytical"].provenance["origin"] == "closed-form"

    def test_extract_baselines_ignores_primary_under_baselines_key(self):
        """An accidental 'primary' entry under ``baselines`` is ignored in favor of flat fields."""
        data = {
            "simulation": {"stop_time": 10.0},
            "time": [0.0],
            "variables": [],
            "baselines": {
                "primary": {"simulation": {"stop_time": 999.0}, "time": [], "variables": []},
            },
        }
        baselines = _extract_baselines(data)
        assert set(baselines.keys()) == {"primary"}
        assert baselines["primary"].simulation["stop_time"] == 10.0  # from flat, not nested

    def test_get_baseline_returns_primary_by_default(self, sample_models_dir, tmp_path):
        """End-to-end: store a ref, then read it back via the Baseline view."""
        config = Config(source_path=sample_models_dir, reference_root=tmp_path / "refs")
        store = ReferenceStore(config)
        test = TestReferenceStore()._make_test_model()
        result = TestReferenceStore()._make_test_result()
        store.store_reference(test, result)

        b = store.get_baseline(test.model_id)
        assert b is not None
        assert b.name == PRIMARY_BASELINE
        assert len(b.variables) == 2
        assert b.provenance["origin"] == "legacy-flat"

    def test_get_baseline_unknown_name_returns_none(self, sample_models_dir, tmp_path):
        config = Config(source_path=sample_models_dir, reference_root=tmp_path / "refs")
        store = ReferenceStore(config)
        test = TestReferenceStore()._make_test_model()
        store.store_reference(test, TestReferenceStore()._make_test_result())

        assert store.get_baseline(test.model_id, name="experiment") is None

    def test_list_baseline_names_legacy(self, sample_models_dir, tmp_path):
        """Legacy files always expose exactly ``["primary"]``."""
        config = Config(source_path=sample_models_dir, reference_root=tmp_path / "refs")
        store = ReferenceStore(config)
        test = TestReferenceStore()._make_test_model()
        store.store_reference(test, TestReferenceStore()._make_test_result())

        assert store.list_baseline_names(test.model_id) == [PRIMARY_BASELINE]

    def test_get_baselines_no_reference(self, sample_models_dir, tmp_path):
        """No ref file → empty dict (not None, not error)."""
        config = Config(source_path=sample_models_dir, reference_root=tmp_path / "refs")
        store = ReferenceStore(config)
        assert store.get_baselines("MyLib.Nonexistent") == {}
        assert store.list_baseline_names("MyLib.Nonexistent") == []
        assert store.get_baseline("MyLib.Nonexistent") is None

    def test_store_reference_preserves_additional_baselines(self, sample_models_dir, tmp_path):
        """Accepting new primary results must not wipe out non-primary baselines."""
        config = Config(source_path=sample_models_dir, reference_root=tmp_path / "refs")
        store = ReferenceStore(config)
        test = TestReferenceStore()._make_test_model()

        # 1. Store initial primary.
        store.store_reference(test, TestReferenceStore()._make_test_result())

        # 2. Manually inject an experiment baseline into the ref file (simulates
        #    a user adding an experiment baseline out-of-band).
        ref_file = store.ref_dir / "ref_0001.json"
        data = json.loads(ref_file.read_text(encoding="utf-8"))
        data["baselines"] = {
            "experiment": {
                "provenance": {"origin": "rig-run-2024-03-15", "citation": "Report XYZ"},
                "time": [0.0, 0.5, 1.0],
                "variables": [{"index": 1, "name": "x", "values": [0.0, 0.5, 1.0]}],
            }
        }
        ref_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

        # 3. Accept fresh primary results → experiment baseline must survive.
        store.store_reference(test, TestReferenceStore()._make_test_result())

        data_after = json.loads(ref_file.read_text(encoding="utf-8"))
        assert "experiment" in data_after.get("baselines", {})
        assert data_after["baselines"]["experiment"]["provenance"]["citation"] == "Report XYZ"

    def test_store_reference_drops_accidental_primary_under_baselines(self, sample_models_dir, tmp_path):
        """On rewrite, any 'primary' entry under 'baselines' is dropped (flat fields are authoritative)."""
        config = Config(source_path=sample_models_dir, reference_root=tmp_path / "refs")
        store = ReferenceStore(config)
        test = TestReferenceStore()._make_test_model()
        store.store_reference(test, TestReferenceStore()._make_test_result())

        ref_file = store.ref_dir / "ref_0001.json"
        data = json.loads(ref_file.read_text(encoding="utf-8"))
        data["baselines"] = {"primary": {"simulation": {"stop_time": 999.0}}}
        ref_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

        store.store_reference(test, TestReferenceStore()._make_test_result())
        data_after = json.loads(ref_file.read_text(encoding="utf-8"))
        # Either the baselines key is absent, or it exists but has no primary entry
        assert "primary" not in data_after.get("baselines", {})

