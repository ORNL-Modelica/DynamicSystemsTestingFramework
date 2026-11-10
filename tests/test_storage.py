"""Tests for storage: RefIndex and ReferenceStore."""

import csv
import inspect
import json
import logging
from pathlib import Path

import numpy as np
import pytest

import dstf.storage.ref_index as ri_mod
from conftest import PROVENANCE, PROVENANCES, series
from dstf.backends.types import TestResult, VariableResult
from dstf.config import Config
from dstf.discovery.test_registry import TestModel
from dstf.storage.baseline import PRIMARY_BASELINE, Baseline
from dstf.storage.baseline_codec import decode_reference
from dstf.storage.ref_index import RefIndex, _atomic_write_text
from dstf.storage.reference_store import ReferenceStore, _downsample

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _file(store: ReferenceStore, model_id: str) -> dict:
    """A model's reference FILE as JSON — what the writer put on disk."""
    return json.loads(store.reference_file(model_id).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# RefIndex (in-memory index built from ref files)
# ---------------------------------------------------------------------------


#: Models and the codes of their files, LITERALS (``sha256sum`` over the id):
#: a test that placed a file by the function under test would follow it.
T1, T2, T3 = "MyLib.Test1", "MyLib.Test2", "MyLib.Test3"
CODE = {
    T1: "772e48f118ed",
    T2: "4dfc8a0300c3",
    T3: "52aff1e9704e",
    "MyLib.Old": "c90c603220d1",
}


class TestRefIndex:
    def _write_ref(self, ref_dir, model_id, status="active", *, name=None):
        """Helper to write a minimal ref file under its model's name."""
        ref_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "model_id": model_id,
            "status": status,
            "date_added": "2026-01-01T00:00:00+00:00",
            "last_updated": "2026-01-01T00:00:00+00:00",
            "n_vars": 0,
            "time": [],
            "variables": [],
        }
        path = ref_dir / (name or f"ref_{CODE[model_id]}.json")
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def test_scan_nonexistent_dir(self, tmp_path):
        """Nonexistent directory produces empty index."""
        index = RefIndex(tmp_path / "nonexistent")
        assert index.active_tests() == {}

    def test_scan_finds_refs(self, tmp_path):
        """A listing reads every file named for the model it holds."""
        self._write_ref(tmp_path, T1)
        self._write_ref(tmp_path, T2)
        index = RefIndex(tmp_path)
        assert index.active_tests() == {"772e48f118ed": T1, "4dfc8a0300c3": T2}

    def test_a_lookup_opens_the_models_file(self, tmp_path):
        self._write_ref(tmp_path, T1)
        index = RefIndex(tmp_path)
        assert index.file(T1) == tmp_path / "ref_772e48f118ed.json"
        assert index.code(T1) == "772e48f118ed"
        assert index.stem(T1) == "ref_772e48f118ed"
        assert (index.file(T2), index.code(T2), index.stem(T2)) == (None, None, None)

    def test_obsolete_excluded_from_active(self, tmp_path):
        """A status that is none is listed by all_tests, read by no lookup."""
        self._write_ref(tmp_path, T1)
        self._write_ref(tmp_path, T2, status="obsolete")
        index = RefIndex(tmp_path)
        assert index.active_tests() == {"772e48f118ed": T1}
        assert index.file(T2) is None

    def test_a_file_without_status_is_listed_active(self, tmp_path):
        """A reference file written before ``status`` existed — no key at
        all, its variables without ``expression`` — is an active baseline:
        listed, and found by its model. An old ``test_id`` key in it is
        read by nothing."""
        old = {
            "model_id": "MyLib.Old",
            "test_id": "0001",
            "date_added": "2025-03-01T00:00:00+00:00",
            "last_updated": "2025-03-01T00:00:00+00:00",
            "n_vars": 1,
            "time": [0.0, 1.0],
            "variables": [{"index": 1, "name": "x", "values": [0.0, 1.0]}],
        }
        (tmp_path / "ref_c90c603220d1.json").write_text(
            json.dumps(old), encoding="utf-8"
        )
        index = RefIndex(tmp_path)
        assert index.active_tests() == {"c90c603220d1": "MyLib.Old"}
        assert index.code("MyLib.Old") == "c90c603220d1"

    def test_an_index_is_read_once_over_a_missing_directory_too(self, tmp_path):
        """Read on first use, as over a directory that exists: a file
        another writer adds later is not seen by this index."""
        ref_dir = tmp_path / "refs"
        index = RefIndex(ref_dir)
        assert index.file(T1) is None
        self._write_ref(ref_dir, T1)
        assert index.file(T1) is None
        assert RefIndex(ref_dir).file(T1) == ref_dir / "ref_772e48f118ed.json"

    def test_all_tests_includes_all_statuses(self, tmp_path):
        """all_tests() returns every named file, obsolete and skip included."""
        self._write_ref(tmp_path, T1)
        self._write_ref(tmp_path, T2, status="obsolete")
        self._write_ref(tmp_path, T3, status="skip")
        index = RefIndex(tmp_path)
        assert {c: e["status"] for c, e in index.all_tests().items()} == {
            "772e48f118ed": "active",
            "4dfc8a0300c3": "obsolete",
            "52aff1e9704e": "skip",
        }

    def test_a_file_named_for_another_model_is_in_no_listing(self, tmp_path):
        """T1's baseline copied under T2's name: listed by neither, and
        T2's lookup refuses the file."""
        self._write_ref(tmp_path, T1, name="ref_4dfc8a0300c3.json")
        index = RefIndex(tmp_path)
        assert index.all_tests() == {}
        assert index.file(T1) is None
        assert index.file(T2) is None
        assert index.refusal(T2) == (
            "ref_4dfc8a0300c3.json holds the baseline of MyLib.Test1, not of "
            "MyLib.Test2 — two model ids share its code, or the file was copied "
            "by hand"
        )


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
            source_file=None,
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
                VariableResult(
                    index=1, time=time, values=np.linspace(0, 1, 101), name="CPUtime"
                ),
            ],
            statistics={"translation": {"nonlinear_count": 2}},
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

        stored = store.store_reference(test, result, provenance=PROVENANCE)
        assert stored is True

        ref = store.get_reference(test.model_id)
        assert ref is not None
        assert ref.model_id == test.model_id
        assert _file(store, test.model_id)["n_vars"] == 2
        assert len(ref.baseline.variables) == 2
        assert ref.baseline.variables[0].name == "x"

    def test_status_and_dates_stored(self, sample_models_dir, tmp_path):
        """status, date_added, and last_updated are in stored reference."""
        config = Config(
            source_path=sample_models_dir,
            reference_root=tmp_path / "refs",
        )
        store = ReferenceStore(config)
        test = self._make_test_model()
        result = self._make_test_result()

        store.store_reference(test, result, provenance=PROVENANCE)
        ref = store.get_reference(test.model_id)

        assert ref.status == "active"
        assert ref.date_added is not None
        assert ref.last_updated is not None

    def test_date_added_preserved_on_update(self, sample_models_dir, tmp_path):
        """date_added stays the same when a reference is updated."""
        config = Config(
            source_path=sample_models_dir,
            reference_root=tmp_path / "refs",
        )
        store = ReferenceStore(config)
        test = self._make_test_model()
        result = self._make_test_result()

        store.store_reference(test, result, provenance=PROVENANCE)
        ref1 = store.get_reference(test.model_id)
        original_date = ref1.date_added

        # Store again (update)
        store.store_reference(test, result, provenance=PROVENANCE)
        ref2 = store.get_reference(test.model_id)
        assert ref2.date_added == original_date

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

        store.store_reference(test, result, provenance=PROVENANCE)
        ref = _file(store, test.model_id)

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
        """Statistics are stored; a listed name's final is its summary's,
        never a statistic."""
        config = Config(
            source_path=sample_models_dir,
            reference_root=tmp_path / "refs",
        )
        store = ReferenceStore(config)
        test = self._make_test_model()
        result = self._make_test_result()

        store.store_reference(test, result, provenance=PROVENANCE)
        ref = store.get_reference(test.model_id)

        assert ref.baseline.statistics == {"translation": {"nonlinear_count": 2}}
        (cpu,) = ref.baseline.diagnostics
        assert (cpu.name, cpu.final, cpu.min, cpu.max) == ("CPUtime", 1.0, 0.0, 1.0)

    def test_failed_result_not_stored(self, sample_models_dir, tmp_path):
        """Failed simulations are not stored as references."""
        config = Config(
            source_path=sample_models_dir,
            reference_root=tmp_path / "refs",
        )
        store = ReferenceStore(config)
        test = self._make_test_model()
        result = TestResult(model_id=test.model_id, success=False)

        stored = store.store_reference(test, result, provenance=PROVENANCE)
        assert stored is False

    def test_a_failed_result_with_variables_does_not_overwrite(
        self, sample_models_dir, tmp_path
    ):
        store = ReferenceStore(
            Config(source_path=sample_models_dir, reference_root=tmp_path / "refs")
        )
        test = self._make_test_model()
        store.store_reference(test, self._make_test_result(), provenance=PROVENANCE)
        failed = self._make_test_result()
        failed.success = False
        failed.variables[0].values = failed.variables[0].values + 1.0
        assert store.store_reference(test, failed, provenance=PROVENANCE) is False
        assert _file(store, test.model_id)["variables"][0]["values"][1] == (
            pytest.approx(np.sin(0.1))
        )

    def test_a_test_that_names_no_solver_records_none(
        self, sample_models_dir, tmp_path
    ):
        """A test built with no ``method`` (discovery always gives one):
        the baseline says none ran by name — null, not the text "None"."""
        store = ReferenceStore(
            Config(
                source_path=sample_models_dir,
                reference_root=tmp_path / "refs",
                backend_name="Dymola",
            )
        )
        test = self._make_test_model()
        assert test.method is None
        store.store_reference(test, self._make_test_result(), provenance=PROVENANCE)
        assert _file(store, test.model_id)["simulation"]["method"] is None

    def test_a_successful_result_with_no_variables_is_not_stored(
        self, sample_models_dir, tmp_path
    ):
        store = ReferenceStore(
            Config(source_path=sample_models_dir, reference_root=tmp_path / "refs")
        )
        test = self._make_test_model()
        empty = TestResult(model_id=test.model_id, success=True, variables=[])
        assert store.store_reference(test, empty, provenance=PROVENANCE) is False

    @pytest.mark.parametrize("gone", ["the file", "the folder"])
    def test_a_store_rewrites_a_baseline_removed_under_it(
        self, sample_models_dir, tmp_path, gone
    ):
        """A long-lived store (the tool's) whose file or folder another
        process removed writes the baseline again."""
        import shutil

        store = ReferenceStore(
            Config(source_path=sample_models_dir, reference_root=tmp_path / "refs")
        )
        test = self._make_test_model()
        store.store_reference(test, self._make_test_result(), provenance=PROVENANCE)
        if gone == "the file":
            store.reference_file(test.model_id).unlink()
        else:
            shutil.rmtree(store.ref_dir)
        assert (
            store.store_reference(test, self._make_test_result(), provenance=PROVENANCE)
            is True
        )
        assert _file(store, test.model_id)["model_id"] == test.model_id

    def test_a_one_row_result_has_one_interval(self, sample_models_dir, tmp_path):
        store = ReferenceStore(
            Config(source_path=sample_models_dir, reference_root=tmp_path / "refs")
        )
        test = self._make_test_model()
        one = TestResult(
            model_id=test.model_id,
            success=True,
            variables=[
                VariableResult(
                    index=1, time=np.array([0.0]), values=np.array([1.0]), name="x"
                )
            ],
        )
        store.store_reference(test, one, provenance=PROVENANCE)
        assert _file(store, test.model_id)["simulation"]["number_of_intervals"] == 1

    def test_a_listed_names_summary_is_stored_from_however_many_samples(
        self, sample_models_dir, tmp_path
    ):
        store = ReferenceStore(
            Config(source_path=sample_models_dir, reference_root=tmp_path / "refs")
        )
        test = self._make_test_model()
        result = self._make_test_result()
        result.diagnostics = [
            VariableResult(index=0, time=np.array([]), values=np.array([]), name="E"),
            VariableResult(
                index=0, time=np.array([0.0]), values=np.array([3.0]), name="One"
            ),
        ]
        store.store_reference(test, result, provenance=PROVENANCE)
        assert _file(store, test.model_id)["diagnostics"] == [
            {"name": "E"},
            {"name": "One", "final": 3.0, "min": 3.0, "max": 3.0},
        ]

    def test_accept_warns_of_each_test_it_does_not_accept(
        self, sample_models_dir, tmp_path, caplog
    ):
        store = ReferenceStore(
            Config(source_path=sample_models_dir, reference_root=tmp_path / "refs")
        )
        test = self._make_test_model()
        result = self._make_test_result()
        result.variables[0].values[3] = np.nan
        with caplog.at_level(logging.WARNING, logger="dstf"):
            store.accept_results(
                [test], {test.model_id: result}, provenance=PROVENANCES
            )
        assert [r.getMessage() for r in caplog.records] == [
            f"Not accepting {test.model_id}: refusing to store baseline for "
            f"{test.model_id}: variable 'x' contains non-finite values (NaN/Inf)"
        ]

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
        store.store_reference(test, result, provenance=PROVENANCE)
        ref = store.get_reference(test.model_id)

        # Should derive: 101 unique times - 1 = 100 intervals
        assert ref.baseline.simulation["number_of_intervals"] == 100
        assert ref.baseline.simulation["output_interval"] is None

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

        store.store_reference(test, result, provenance=PROVENANCE)
        ref = store.get_reference(test.model_id)

        assert ref.baseline.simulation["output_interval"] == 0.5
        assert ref.baseline.simulation["number_of_intervals"] is None

    def test_json_field_order(self, sample_models_dir, tmp_path):
        """Metadata fields come before data fields in reference JSON."""
        config = Config(
            source_path=sample_models_dir,
            reference_root=tmp_path / "refs",
        )
        store = ReferenceStore(config)
        test = self._make_test_model()
        result = self._make_test_result()

        store.store_reference(test, result, provenance=PROVENANCE)

        ref_files = list(config.reference_dir.glob("ref_*.json"))
        assert len(ref_files) == 1
        data = json.loads(ref_files[0].read_text())
        keys = list(data.keys())

        assert keys.index("model_id") < keys.index("time")
        assert "test_id" not in keys
        assert keys.index("status") < keys.index("time")
        assert keys.index("date_added") < keys.index("time")

    def test_every_variable_entry_carries_an_expression(
        self, sample_models_dir, tmp_path
    ):
        """The expression a UnitTests variable was accepted with travels with
        the baseline — that is what makes the positional name safe to compare
        against. Always written, ``""`` for a variable that has none, so a
        stored entry without the key is an un-migrated one."""
        config = Config(
            source_path=sample_models_dir,
            reference_root=tmp_path / "refs",
        )
        store = ReferenceStore(config)
        time = np.linspace(0, 1, 11)
        result = TestResult(
            model_id="ModelicaTestingLib.Examples.Test1",
            success=True,
            variables=[
                VariableResult(
                    index=1,
                    time=time,
                    values=time,
                    name="unitTests.x[1]",
                    expression="cat(1, a, b)[1]",
                ),
                VariableResult(index=2, time=time, values=time, name="plain"),
            ],
        )
        store.store_reference(self._make_test_model(), result, provenance=PROVENANCE)

        ref = _file(store, "ModelicaTestingLib.Examples.Test1")
        assert [(v["name"], v["expression"]) for v in ref["variables"]] == [
            ("unitTests.x[1]", "cat(1, a, b)[1]"),
            ("plain", ""),
        ]
        assert list(ref["variables"][0])[:3] == ["index", "name", "expression"]

    def test_index_rebuilt_from_files(self, sample_models_dir, tmp_path):
        """A new ReferenceStore instance rebuilds the index from ref files."""
        config = Config(
            source_path=sample_models_dir,
            reference_root=tmp_path / "refs",
        )
        store1 = ReferenceStore(config)
        test = self._make_test_model()
        result = self._make_test_result()
        store1.store_reference(test, result, provenance=PROVENANCE)

        # Create a new store (simulates restart)
        store2 = ReferenceStore(config)
        ref = store2.get_reference(test.model_id)
        assert ref is not None
        assert ref.model_id == test.model_id


# ---------------------------------------------------------------------------
# Baseline view (Phase 1.7a: read-side unified interface)
# ---------------------------------------------------------------------------


class TestBaselineView:
    """A reference file's primary baseline, as every reader sees it."""

    def test_a_flat_file_reads_as_its_primary_baseline(self):
        """The file's top-level fields are its ``primary`` baseline; an old
        ``{name, values}`` diagnostic is summarized on read."""
        flat = {
            "model_id": "MyLib.Test1",
            "date_added": "2026-01-15T10:00:00+00:00",
            "last_updated": "2026-02-20T12:30:00+00:00",
            "simulation": {"stop_time": 10.0, "tolerance": 1e-4},
            "comparison": {"tolerance": 0.01},
            "statistics": {"Foo": 2.5},
            "time": [0.0, 1.0, 2.0],
            "variables": [{"index": 1, "name": "x", "values": [0.0, 1.0, 2.0]}],
            "diagnostics": [{"name": "CPUtime", "values": [0.1, 0.2, 0.3]}],
        }
        ref = decode_reference(flat)
        b = ref.baseline
        assert isinstance(b, Baseline)
        assert b.name == PRIMARY_BASELINE
        assert b.time == [0.0, 1.0, 2.0]
        assert b.simulation["stop_time"] == 10.0
        assert ref.comparison["tolerance"] == 0.01
        assert b.statistics["Foo"] == 2.5
        assert len(b.variables) == 1
        (d,) = b.diagnostics
        assert (d.name, d.final, d.min, d.max) == ("CPUtime", 0.3, 0.1, 0.3)

    def test_a_stored_reference_reads_back_as_its_primary(
        self, sample_models_dir, tmp_path
    ):
        """End-to-end: store a ref, then read it back."""
        config = Config(source_path=sample_models_dir, reference_root=tmp_path / "refs")
        store = ReferenceStore(config)
        test = TestReferenceStore()._make_test_model()
        result = TestReferenceStore()._make_test_result()
        store.store_reference(test, result, provenance=PROVENANCE)

        b = store.get_reference(test.model_id).baseline
        assert b.name == PRIMARY_BASELINE
        assert len(b.variables) == 2
        assert store.get_soft_checks(test.model_id) == {}

    def test_no_reference(self, sample_models_dir, tmp_path):
        """No ref file → None and no soft checks (not an error)."""
        config = Config(source_path=sample_models_dir, reference_root=tmp_path / "refs")
        store = ReferenceStore(config)
        assert store.get_reference("MyLib.Nonexistent") is None
        assert store.get_soft_checks("MyLib.Nonexistent") == {}

    def test_store_reference_drops_a_stray_baselines_key(
        self, sample_models_dir, tmp_path
    ):
        """Writers never carry the deleted key forward."""
        config = Config(source_path=sample_models_dir, reference_root=tmp_path / "refs")
        store = ReferenceStore(config)
        test = TestReferenceStore()._make_test_model()
        store.store_reference(
            test, TestReferenceStore()._make_test_result(), provenance=PROVENANCE
        )
        ref_file = store.reference_file(test.model_id)
        data = json.loads(ref_file.read_text(encoding="utf-8"))
        data["baselines"] = {
            "experiment": {
                "provenance": {"origin": "rig-run-2024-03-15"},
                "time": [0.0, 0.5, 1.0],
                "variables": [{"index": 1, "name": "x", "values": [0.0, 0.5, 1.0]}],
            }
        }
        ref_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

        store.store_reference(
            test, TestReferenceStore()._make_test_result(), provenance=PROVENANCE
        )

        data_after = json.loads(ref_file.read_text(encoding="utf-8"))
        assert "baselines" not in data_after


class TestExports:
    """``export_csv`` writes atomically like every other export — no
    truncated summary and no leftover ``.tmp`` file."""

    def test_export_csv_roundtrip_and_no_tmp_left(self, sample_models_dir, tmp_path):
        config = Config(source_path=sample_models_dir, reference_root=tmp_path / "refs")
        store = ReferenceStore(config)
        helper = TestReferenceStore()
        store.store_reference(
            helper._make_test_model(), helper._make_test_result(), provenance=PROVENANCE
        )

        out = tmp_path / "export" / "nested" / "summary.csv"
        store.export_csv(out)

        with out.open(encoding="utf-8", newline="") as f:
            rows = list(csv.reader(f))
        assert rows[0][0] == "file"
        assert rows[1][:3] == [
            "ref_c6161898c511.json",
            "ModelicaTestingLib.Examples.Test1",
            "active",
        ]
        stored = _file(store, "ModelicaTestingLib.Examples.Test1")
        when = dict(zip(rows[0], rows[1]))
        assert (when["date_added"], when["last_updated"]) == (
            stored["date_added"],
            stored["last_updated"],
        )
        assert list(out.parent.glob("*.tmp")) == []
        # csv embeds \r\n itself. Pin that it reaches disk intact and is
        # never doubled to \r\r\n. Since "\n" became the default
        # newline, neither that nor the caller's explicit newline="" is
        # translating, so this can no longer catch a dropped kwarg on any
        # platform — it pins the CSV terminator, not the argument.
        raw = out.read_bytes()
        assert b"\r\n" in raw
        assert b"\r\r\n" not in raw


def test_export_json_writes_every_stored_reference(sample_models_dir, tmp_path):
    config = Config(source_path=sample_models_dir, reference_root=tmp_path / "refs")
    store = ReferenceStore(config)
    helper = TestReferenceStore()
    store.store_reference(
        helper._make_test_model(), helper._make_test_result(), provenance=PROVENANCE
    )
    out = tmp_path / "export" / "all.json"
    store.export_json(out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert list(data) == ["ModelicaTestingLib.Examples.Test1"]
    assert data["ModelicaTestingLib.Examples.Test1"]["variables"][0]["name"] == "x"


class TestLineEndings:
    """Every JSON DSTF writes lands with LF on every platform.

    The reference tree is version-controlled by the people who consume it.
    While ``_atomic_write_text`` took :func:`open`'s ``newline=None``, a
    baseline accepted on Windows landed CRLF and the same file accepted from
    WSL landed LF, so a one-value edit arrived as a whole-file diff with the
    real change invisible inside it.

    The byte assertions can only fail on a CRLF platform. The signature
    assertion is what fails on Linux too, so it is the one that actually
    guards the decision.
    """

    def test_atomic_write_defaults_to_lf(self):
        default = inspect.signature(_atomic_write_text).parameters["newline"].default
        assert default == "\n"

    def test_atomic_write_emits_lf(self, tmp_path):
        out = tmp_path / "x.json"
        _atomic_write_text(out, '{\n  "a": 1\n}\n')
        assert b"\r" not in out.read_bytes()

    def test_stored_baseline_is_lf(self, sample_models_dir, tmp_path):
        config = Config(source_path=sample_models_dir, reference_root=tmp_path / "refs")
        store = ReferenceStore(config)
        helper = TestReferenceStore()
        store.store_reference(
            helper._make_test_model(), helper._make_test_result(), provenance=PROVENANCE
        )

        refs = list((tmp_path / "refs").rglob("ref_*.json"))
        assert refs
        for ref in refs:
            assert b"\r" not in ref.read_bytes(), ref


# =========================================================================
# Folded in from tests/test_review_fixes_storage.py — regression tests for the
# 2026-07-06 review, Theme 4: reference store integrity (findings 3, 31-36).
#
# * ``_downsample`` never drops the first index, the last index, or event
#   boundaries. When the linspace/event union overshoots ``max_points``,
#   interior linspace candidates are dropped instead. If event indices alone
#   exceed the budget, the events are evenly thinned but the endpoints stay.
# * Every reference-store JSON write is atomic (tmp file in the same
#   directory + replace) — an interrupted write must not corrupt a baseline.
# * A baseline is written under its model's own name, so two stores over one
#   folder (two ``--accept`` processes) cannot overwrite each other's model.
# * ``add_companion`` resolves relative paths against the CWD, stores the
#   absolute path, and rejects missing files at registration time. The
#   overlay loader keeps ref_dir-relative resolution as a legacy fallback.
# * soft_check / companion names must be filename-safe — path separators,
#   ``..``, and Windows-illegal characters are rejected everywhere a name
#   reaches the filesystem.
# =========================================================================


def _mk_model(model_id="Lib.Test1") -> TestModel:
    return TestModel(
        model_id=model_id,
        source_file=None,
        n_vars=1,
        variable_patterns=[],
        source="unit_tests",
    )


def _mk_result(model_id="Lib.Test1") -> TestResult:
    time = np.linspace(0, 10, 11)
    return TestResult(
        model_id=model_id,
        success=True,
        variables=[VariableResult(index=1, time=time, values=np.sin(time), name="x")],
    )


def _mk_store(sample_models_dir, tmp_path) -> ReferenceStore:
    config = Config(source_path=sample_models_dir, reference_root=tmp_path / "refs")
    return ReferenceStore(config)


@pytest.fixture
def store_with_primary(sample_models_dir, tmp_path):
    store = _mk_store(sample_models_dir, tmp_path)
    test = _mk_model()
    store.store_reference(test, _mk_result(), provenance=PROVENANCE)
    return store, test


# ---------------------------------------------------------------------------
# Finding 3 — _downsample must never truncate the tail
# ---------------------------------------------------------------------------


class TestDownsampleTailPreservation:
    def test_off_grid_event_does_not_drop_final_sample(self):
        """Review scenario: n=5000, one event pair off the linspace grid.

        The event indices push the union over max_points; the old code
        trimmed the *sorted* list from the end, silently dropping the final
        time sample so all future comparisons ignored the tail.
        """
        n, max_points = 5000, 2000
        t = np.linspace(0.0, 100.0, n)
        # Pick an event index pair that is NOT on the even-sampling grid so
        # the union of events + linspace candidates overshoots max_points.
        grid = set(np.linspace(0, n - 1, max_points, dtype=int).tolist())
        k = next(i for i in range(2, n - 2) if i not in grid and (i - 1) not in grid)
        t[k] = t[k - 1]  # duplicate time value == Modelica event boundary
        v = np.sin(t)
        v[k:] += 1.0  # discontinuity at the event

        t_out, v_out = _downsample(t, v, max_points=max_points)

        assert len(t_out) <= max_points
        assert t_out[0] == t[0]
        assert t_out[-1] == t[-1], "stored baseline must end at the final sample"
        assert v_out[-1] == pytest.approx(v[-1], rel=1e-12)
        # Both sides of the event boundary survive (duplicate time value).
        dup_times = {t_out[i] for i in range(1, len(t_out)) if t_out[i] == t_out[i - 1]}
        assert float("%.15g" % t[k]) in dup_times

    def test_event_indices_alone_exceeding_budget_are_thinned_not_truncated(self):
        """When events alone exceed max_points, thin them evenly — the
        first and last samples must still survive."""
        n = 3000
        t = np.linspace(0.0, 30.0, n)
        for k in range(2, 1602, 2):  # 800 duplicate pairs -> ~1600 event idx
            t[k] = t[k - 1]
        v = np.arange(n, dtype=float)

        t_out, v_out = _downsample(t, v, max_points=100)

        assert len(t_out) <= 100
        assert t_out[0] == t[0]
        assert t_out[-1] == t[-1]
        assert v_out[-1] == pytest.approx(v[-1])

    def test_plain_series_unchanged_behavior(self):
        """No events: even sampling with endpoints, within budget."""
        t = np.linspace(0.0, 50.0, 10000)
        v = np.cos(t)
        t_out, v_out = _downsample(t, v, max_points=500)
        assert len(t_out) <= 500
        assert t_out[0] == t[0]
        assert t_out[-1] == t[-1]


@pytest.mark.parametrize(
    "time,points,stored_time,stored_values",
    [
        # Every time stamp equal: the first and last samples, once each.
        ([0, 0, 0, 0, 0], 4, [0, 0, 0, 0], [0, 1, 3, 4]),
        # The evenly spaced picks and the event's two samples are one more
        # than the budget: the last pick gives way, never the budget.
        ([0, 1, 2, 3, 4, 4, 6, 7, 8, 9], 5, [0, 2, 4, 4, 9], [0, 2, 4, 5, 9]),
        ([0, 1, 1, 3, 4, 5], 4, [0, 1, 1, 5], [0, 1, 2, 5]),
    ],
    ids=["constant time", "one pick over", "an event at the budget"],
)
def test_a_baseline_stores_at_most_its_points(
    sample_models_dir, tmp_path, time, points, stored_time, stored_values
):
    config = Config(
        source_path=sample_models_dir,
        reference_root=tmp_path / "refs",
        baseline_points=points,
    )
    store = ReferenceStore(config)
    t = np.array(time, dtype=float)
    result = TestResult(
        model_id="Lib.Test1",
        success=True,
        variables=[
            VariableResult(
                index=1, time=t, values=np.arange(len(t), dtype=float), name="x"
            )
        ],
    )
    store.store_reference(_mk_model(), result, provenance=PROVENANCE)
    data = _file(store, "Lib.Test1")
    assert data["time"] == stored_time
    assert data["variables"][0]["values"] == stored_values


# ---------------------------------------------------------------------------
# Finding 31 — atomic JSON writes
# ---------------------------------------------------------------------------


class TestAtomicWrites:
    def test_interrupted_replace_preserves_existing_file(self, tmp_path, monkeypatch):
        """A crash between tmp-write and replace must leave the original
        baseline intact (and no stray tmp files)."""
        target = tmp_path / "ref_0001.json"
        target.write_text('{"model_id": "Lib.A"}', encoding="utf-8")

        monkeypatch.setattr(ri_mod.time, "sleep", lambda _s: None)

        def boom(self, _other):
            raise OSError("simulated crash during replace")

        monkeypatch.setattr(ri_mod.Path, "replace", boom)
        with pytest.raises(OSError):
            ri_mod._atomic_write_text(target, '{"model_id": "Lib.B"}')

        assert json.loads(target.read_text(encoding="utf-8"))["model_id"] == "Lib.A"
        assert list(tmp_path.glob("*.tmp")) == []

    def test_a_write_waits_out_a_file_held_for_a_moment(self, tmp_path, monkeypatch):
        """Windows locks a file another process has open: the replace is
        retried after a pause, not five times back to back."""
        import time

        real_replace = Path.replace
        held_until = []

        def held(self, target):
            if not held_until:
                held_until.append(time.monotonic() + 0.3)
            if time.monotonic() < held_until[0]:
                raise PermissionError("held by another process")
            return real_replace(self, target)

        monkeypatch.setattr(Path, "replace", held)
        target = tmp_path / "ref_0001.json"
        _atomic_write_text(target, '{"model_id": "Lib.A"}')
        assert json.loads(target.read_text(encoding="utf-8")) == {"model_id": "Lib.A"}

    def test_all_reference_json_writes_go_through_tmp_files(
        self, store_with_primary, tmp_path, monkeypatch
    ):
        """No store operation may call write_text directly on a final
        .json path under the ref dir — everything goes tmp + replace."""
        store, test = store_with_primary

        ext = tmp_path / "rig.csv"
        ext.write_text("time,x\n0,1\n1,2\n", encoding="utf-8")

        recorded: list[Path] = []
        real_write_text = Path.write_text

        def spy(self, *args, **kwargs):
            recorded.append(Path(self))
            return real_write_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", spy)

        store.store_reference(
            test, _mk_result(), provenance=PROVENANCE
        )  # update existing ref
        # A new model: its ID is claimed with the codec's stub, then replaced.
        store.store_reference(
            _mk_model("Lib.New"), _mk_result("Lib.New"), provenance=PROVENANCE
        )
        store.add_soft_check(
            test.model_id,
            series(
                "experiment",
                time=[0.0, 1.0],
                variables=[{"index": 1, "name": "x", "values": [0.0, 1.0]}],
            ),
        )
        store.add_companion(test.model_id, "rig", path=ext)
        store.freeze_companion(test.model_id, "rig")
        store.export_json(store.ref_dir / "export.json")
        store.export_csv(store.ref_dir / "export.csv")

        ref_root = str(store.ref_dir)
        direct_json_writes = [
            p for p in recorded if str(p).startswith(ref_root) and p.suffix == ".json"
        ]
        assert direct_json_writes == []
        # And nothing left half-written behind.
        assert list(store.ref_dir.rglob("*.tmp")) == []


# ---------------------------------------------------------------------------
# Findings 32 + 33 — collision-safe ID allocation + orphan role dirs
# ---------------------------------------------------------------------------


class TestTwoWriters:
    def test_two_stores_over_one_folder_write_each_models_own_file(
        self, sample_models_dir, tmp_path
    ):
        """Two stores over the same partition (two --accept processes), each
        read while it was empty: each writes its own model's name, and
        neither touches the other's."""
        config = Config(source_path=sample_models_dir, reference_root=tmp_path / "refs")
        store1 = ReferenceStore(config)
        store2 = ReferenceStore(config)
        store1.index.ensure_loaded()
        store2.index.ensure_loaded()

        store1.store_reference(
            _mk_model("Lib.A"), _mk_result("Lib.A"), provenance=PROVENANCE
        )
        store2.store_reference(
            _mk_model("Lib.B"), _mk_result("Lib.B"), provenance=PROVENANCE
        )

        held = {
            p.name: json.loads(p.read_text(encoding="utf-8"))["model_id"]
            for p in config.reference_dir.iterdir()
        }
        assert held == {
            "ref_6e187be76792.json": "Lib.A",
            "ref_452b135a045c.json": "Lib.B",
        }

    def test_an_unreadable_file_under_the_models_name_is_replaced_by_its_accept(
        self, sample_models_dir, tmp_path
    ):
        """A file cut short at the model's own name (a Ctrl-C mid-write by a
        writer before the atomic one) names no other model: an accept
        writes the baseline over it."""
        store = _mk_store(sample_models_dir, tmp_path)
        store.ref_dir.mkdir(parents=True)
        (store.ref_dir / "ref_6e187be76792.json").write_text(
            "{not js", encoding="utf-8"
        )
        assert store.store_reference(
            _mk_model("Lib.A"), _mk_result("Lib.A"), provenance=PROVENANCE
        )
        data = json.loads(
            (store.ref_dir / "ref_6e187be76792.json").read_text(encoding="utf-8")
        )
        assert data["model_id"] == "Lib.A"


# ---------------------------------------------------------------------------
# Finding 34 — companion relative paths: store absolute, fail fast on missing
# ---------------------------------------------------------------------------


class TestCompanionPathResolution:
    def test_relative_path_resolved_against_cwd_and_stored_absolute(
        self, store_with_primary, tmp_path, monkeypatch
    ):
        store, test = store_with_primary
        cwd = tmp_path / "userdir"
        cwd.mkdir()
        data = cwd / "rig.csv"
        data.write_text("time,x\n0,1\n1,2\n", encoding="utf-8")

        monkeypatch.chdir(cwd)
        store.add_companion(test.model_id, "rig", path=Path("rig.csv"))

        co = store.get_companions(test.model_id)["rig"]
        assert Path(co.path).is_absolute()
        assert Path(co.path) == data.resolve()

        # The loader must find it even from a different CWD.
        monkeypatch.chdir(tmp_path)
        from dstf.reporting.overlay_loader import load_overlays

        overlays = {o.name: o for o in load_overlays(store, test.model_id)}
        assert overlays["rig"].status == "loaded"

    def test_missing_companion_file_rejected_at_add(self, store_with_primary):
        store, test = store_with_primary
        with pytest.raises(FileNotFoundError, match="exist"):
            store.add_companion(
                test.model_id, "ghost", path=Path("/nonexistent/rig.csv")
            )
        assert "ghost" not in store.get_companions(test.model_id)

    def test_legacy_relative_entry_still_resolves_against_ref_dir(
        self, store_with_primary
    ):
        """Pre-fix metadata stored relative paths verbatim; the loader keeps
        ref_dir-relative resolution as a fallback for those entries."""
        store, test = store_with_primary
        co_dir = store.ref_dir / "companions" / "ref_489430e35458"
        co_dir.mkdir(parents=True, exist_ok=True)
        (co_dir / "legacy.json").write_text(
            json.dumps(
                {"kind": "external", "format": "json", "path": "legacy_data.json"}
            ),
            encoding="utf-8",
        )
        (store.ref_dir / "legacy_data.json").write_text(
            json.dumps(
                {"time": [0.0, 1.0], "variables": [{"name": "x", "values": [1, 2]}]}
            ),
            encoding="utf-8",
        )

        from dstf.reporting.overlay_loader import load_overlays

        overlays = {o.name: o for o in load_overlays(store, test.model_id)}
        assert overlays["legacy"].status == "loaded"


# ---------------------------------------------------------------------------
# Finding 35 — role names must be filename-safe
# ---------------------------------------------------------------------------


BAD_NAMES = [
    "a/b",
    "a\\b",
    "..",
    "a..b",
    "x:y",
    "w*ld",
    "q?m",
    'a"b',
    "<x>",
    "p|q",
    ".",
    " x",
    "x ",
]


class TestRoleNameValidation:
    @pytest.mark.parametrize("bad", BAD_NAMES)
    def test_add_soft_check_rejects_unsafe_name(self, store_with_primary, bad):
        store, test = store_with_primary
        with pytest.raises(ValueError) as exc:
            store.add_soft_check(
                test.model_id,
                series(
                    bad,
                    time=[0.0],
                    variables=[{"index": 1, "name": "x", "values": [0.0]}],
                ),
            )
        assert repr(bad) in str(exc.value) or bad in str(exc.value)

    @pytest.mark.parametrize("bad", BAD_NAMES)
    def test_add_companion_rejects_unsafe_name(self, store_with_primary, tmp_path, bad):
        store, test = store_with_primary
        src = tmp_path / "data.csv"
        src.write_text("time,x\n0,1\n", encoding="utf-8")
        with pytest.raises(ValueError):
            store.add_companion(test.model_id, bad, path=src)

    @pytest.mark.parametrize("bad", ["a/b", "..", "a\\b"])
    def test_remove_and_freeze_reject_unsafe_names(self, store_with_primary, bad):
        store, test = store_with_primary
        with pytest.raises(ValueError):
            store.remove_soft_check(test.model_id, bad)
        with pytest.raises(ValueError):
            store.remove_companion(test.model_id, bad)
        with pytest.raises(ValueError):
            store.freeze_companion(test.model_id, bad)

    def test_traversal_name_writes_nothing_outside_role_dir(self, store_with_primary):
        store, test = store_with_primary
        with pytest.raises(ValueError):
            store.add_soft_check(
                test.model_id,
                series(
                    "../../evil",
                    time=[0.0],
                    variables=[{"index": 1, "name": "x", "values": [0.0]}],
                ),
            )
        assert not (store.ref_dir / "evil.json").exists()
        assert not (store.ref_dir.parent / "evil.json").exists()

    def test_error_message_names_offending_character(self, store_with_primary):
        store, test = store_with_primary
        with pytest.raises(ValueError, match="/"):
            store.add_soft_check(
                test.model_id,
                series(
                    "a/b",
                    time=[0.0],
                    variables=[{"index": 1, "name": "x", "values": [0.0]}],
                ),
            )

    def test_good_names_still_accepted(self, store_with_primary):
        store, test = store_with_primary
        ok = store.add_soft_check(
            test.model_id,
            series(
                "dymola-via-fmpy",
                time=[0.0],
                variables=[{"index": 1, "name": "x", "values": [0.0]}],
            ),
        )
        assert ok is True


# =========================================================================
# Folded in from tests/test_review_fixes_cli.py — regression tests for the
# 2026-07-06 review, final CLI/config/manifest batch.
#
# Decision encoded here (leftover B): ``store_reference`` rejects baselines
# whose trajectory contains NaN/Inf (naming model + variable);
# ``accept_results`` skips the poisoned test with a warning and keeps
# accepting the rest.
#
# ``_mk_store`` is the one already defined above (identical definition in the
# source file); the value-taking result builder arrived as ``_mk_result`` and
# was renamed ``_mk_valued_result`` to coexist with this module's ``_mk_result``.
# =========================================================================


def _mk_test(model_id: str, **kw) -> TestModel:
    return TestModel(
        model_id=model_id,
        source_file=None,
        n_vars=1,
        variable_patterns=["x"],
        source="test_spec",
        **kw,
    )


def _mk_valued_result(
    model_id: str,
    values=None,
    success: bool = True,
) -> TestResult:
    t = np.linspace(0.0, 1.0, 11)
    if not success:
        return TestResult(model_id=model_id, success=False, error_message="boom")
    v = np.asarray(values if values is not None else np.sin(t), dtype=float)
    return TestResult(
        model_id=model_id,
        success=True,
        variables=[VariableResult(index=1, time=t, values=v, name="x")],
    )


class TestStoreReferenceRejectsNonFinite:
    @pytest.mark.parametrize("poison", [float("nan"), float("inf"), float("-inf")])
    def test_poisoned_values_rejected_naming_model_and_variable(
        self, sample_models_dir, tmp_path, poison
    ):
        store = _mk_store(sample_models_dir, tmp_path)
        values = [0.0, 1.0, poison] + [2.0] * 8
        with pytest.raises(ValueError) as exc:
            store.store_reference(
                _mk_test("Lib.Bad"),
                _mk_valued_result("Lib.Bad", values),
                provenance=PROVENANCE,
            )
        msg = str(exc.value)
        assert "Lib.Bad" in msg
        assert "x" in msg
        assert "non-finite" in msg
        assert not list((tmp_path / "refs").rglob("ref_*.json"))

    def test_poisoned_time_vector_rejected(self, sample_models_dir, tmp_path):
        store = _mk_store(sample_models_dir, tmp_path)
        result = _mk_valued_result("Lib.Bad")
        result.variables[0].time = np.array([0.0, float("nan"), 2.0])
        result.variables[0].values = np.array([0.0, 1.0, 2.0])
        with pytest.raises(ValueError, match="Lib.Bad"):
            store.store_reference(_mk_test("Lib.Bad"), result, provenance=PROVENANCE)

    def test_finite_trajectory_still_stores(self, sample_models_dir, tmp_path):
        store = _mk_store(sample_models_dir, tmp_path)
        assert store.store_reference(
            _mk_test("Lib.Good"), _mk_valued_result("Lib.Good"), provenance=PROVENANCE
        )

    def test_accept_results_skips_poisoned_and_continues(
        self, sample_models_dir, tmp_path, caplog
    ):
        store = _mk_store(sample_models_dir, tmp_path)
        good, bad = _mk_test("Lib.Good"), _mk_test("Lib.Bad")
        results = {
            "Lib.Good": _mk_valued_result("Lib.Good"),
            "Lib.Bad": _mk_valued_result("Lib.Bad", [float("nan")] * 11),
        }
        with caplog.at_level(logging.WARNING):
            stored, skipped, _ = store.accept_results(
                [bad, good], results, provenance=PROVENANCES
            )
        assert stored == ["Lib.Good"]
        assert [m for m, _ in skipped] == ["Lib.Bad"]
        assert "non-finite" in skipped[0][1]
        assert store.get_reference("Lib.Good") is not None

    def test_accept_results_reports_sim_failures(self, sample_models_dir, tmp_path):
        store = _mk_store(sample_models_dir, tmp_path)
        t1, t2 = _mk_test("Lib.T1"), _mk_test("Lib.T2")
        results = {
            "Lib.T1": _mk_valued_result("Lib.T1"),
            "Lib.T2": _mk_valued_result("Lib.T2", success=False),
        }
        stored, skipped, _ = store.accept_results(
            [t1, t2], results, provenance=PROVENANCES
        )
        assert stored == ["Lib.T1"]
        assert skipped == [("Lib.T2", "simulation failed")]

    def test_accept_results_simulate_only_not_counted_as_skip(
        self, sample_models_dir, tmp_path
    ):
        """Neither stored nor skipped — and named, so that an accept can
        say why it stored nothing for it."""
        store = _mk_store(sample_models_dir, tmp_path)
        sim_only = _mk_test("Lib.SimOnly", simulate_only=True)
        result = TestResult(model_id="Lib.SimOnly", success=True)
        stored, skipped, simulate_only = store.accept_results(
            [sim_only], {"Lib.SimOnly": result}, provenance=PROVENANCES
        )
        assert stored == []
        assert skipped == []
        assert simulate_only == ["Lib.SimOnly"]

    def test_a_simulate_only_test_that_failed_to_simulate_is_a_skip(
        self, sample_models_dir, tmp_path
    ):
        """Its run did not complete: that is its reason, not that it has
        no baseline to store."""
        store = _mk_store(sample_models_dir, tmp_path)
        sim_only = _mk_test("Lib.SimOnly", simulate_only=True)
        failed = TestResult(model_id="Lib.SimOnly", success=False)
        assert store.accept_results(
            [sim_only], {"Lib.SimOnly": failed}, provenance=PROVENANCES
        ) == (
            [],
            [("Lib.SimOnly", "simulation failed")],
            [],
        )


def test_every_shipped_example_baseline_is_named_for_its_model():
    """Every example folder holds only ``ref_<code>.json`` files, each named
    for the model inside it, and no ``.id_high_water.json`` — converted
    whole, as the store refuses a folder holding a numbered file."""
    from dstf.storage.names import baseline_file

    examples = Path(__file__).parent.parent / "examples"
    files = sorted(examples.rglob("ref_*.json"))
    partitions = {ref.parent for ref in files}
    assert len(partitions) >= 7, partitions
    assert sorted(examples.rglob(".id_high_water.json")) == []
    misnamed = [
        ref
        for ref in files
        if ref.name
        != baseline_file(json.loads(ref.read_text(encoding="utf-8"))["model_id"])
    ]
    assert misnamed == []
