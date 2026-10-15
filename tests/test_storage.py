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


def _set_status(store: ReferenceStore, model_id: str, status: str) -> None:
    """Rewrite a reference file's ``status`` by hand (no writer of the
    package does) and refresh the store's index."""
    ref_file = store.reference_file(model_id)
    data = json.loads(ref_file.read_text(encoding="utf-8"))
    data["status"] = status
    ref_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    store.index._loaded = False


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
            source_file=None,
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
                VariableResult(
                    index=1, time=time, values=np.linspace(0, 1, 101), name="CPUtime"
                ),
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
        """Statistics with diagnostic finals are stored."""
        config = Config(
            source_path=sample_models_dir,
            reference_root=tmp_path / "refs",
        )
        store = ReferenceStore(config)
        test = self._make_test_model()
        result = self._make_test_result()

        store.store_reference(test, result, provenance=PROVENANCE)
        ref = store.get_reference(test.model_id)

        assert ref.baseline.statistics["CPUtime"] == 1.0
        assert ref.baseline.statistics["EventCounter"] == 5

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

    def test_cleanup_obsolete(self, sample_models_dir, tmp_path):
        """cleanup_obsolete removes files with obsolete status."""
        config = Config(
            source_path=sample_models_dir,
            reference_root=tmp_path / "refs",
        )
        store = ReferenceStore(config)
        test = self._make_test_model()
        result = self._make_test_result()

        store.store_reference(test, result, provenance=PROVENANCE)
        _set_status(store, test.model_id, "obsolete")

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
        assert keys.index("test_id") < keys.index("variables")
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
            "statistics": {"CPUtime": 2.5},
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
        assert b.statistics["CPUtime"] == 2.5
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
        ref_file = store.ref_dir / "ref_0001.json"
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

        out = tmp_path / "export" / "summary.csv"
        store.export_csv(out)

        with out.open(encoding="utf-8", newline="") as f:
            rows = list(csv.reader(f))
        assert rows[0][0] == "test_id"
        assert rows[1][1] == "ModelicaTestingLib.Examples.Test1"
        assert list(out.parent.glob("*.tmp")) == []
        # csv embeds \r\n itself. Pin that it reaches disk intact and is
        # never doubled to \r\r\n. Since "\n" became the default
        # newline, neither that nor the caller's explicit newline="" is
        # translating, so this can no longer catch a dropped kwarg on any
        # platform — it pins the CSV terminator, not the argument.
        raw = out.read_bytes()
        assert b"\r\n" in raw
        assert b"\r\r\n" not in raw


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
# * New IDs are claimed by exclusive file creation, so a stale in-memory
#   index or a concurrent ``--accept`` in another process cannot allocate
#   the same ID and silently overwrite another model's baseline.
# * ``cleanup_obsolete`` also removes the deleted ref's orphaned
#   ``soft_checks/ref_NNNN/`` and ``companions/ref_NNNN/`` directories, so
#   a later reuse of the ID cannot inherit another model's role data.
# * ``add_companion`` resolves relative paths against the CWD, stores the
#   absolute path, and rejects missing files at registration time. The
#   overlay loader keeps ref_dir-relative resolution as a legacy fallback.
# * soft_check / companion names must be filename-safe — path separators,
#   ``..``, and Windows-illegal characters are rejected everywhere a name
#   reaches the filesystem.
# * Duplicate active ``model_id`` across ref files: the scan warns naming
#   both files and deterministically keeps the lowest-numbered one.
# =========================================================================


def _mk_model(model_id="Lib.Test1") -> TestModel:
    return TestModel(
        model_id=model_id,
        source_file=None,
        source_package="Lib",
        short_name=model_id.rsplit(".", 1)[-1],
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
            "experiment",
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


class TestIdAllocation:
    def test_stale_index_cannot_clobber_another_models_baseline(
        self, sample_models_dir, tmp_path
    ):
        """Two stores over the same partition (simulating two --accept
        processes): the second, holding a stale empty index, must not
        reuse ID 0001 and overwrite the first model's baseline."""
        config = Config(source_path=sample_models_dir, reference_root=tmp_path / "refs")
        store1 = ReferenceStore(config)
        store2 = ReferenceStore(config)
        # Force both indexes to load while the partition is empty.
        assert store1.index.get_id("Lib.A") is None
        assert store2.index.get_id("Lib.B") is None

        store1.store_reference(
            _mk_model("Lib.A"), _mk_result("Lib.A"), provenance=PROVENANCE
        )
        store2.store_reference(
            _mk_model("Lib.B"), _mk_result("Lib.B"), provenance=PROVENANCE
        )

        fresh = ReferenceStore(config)
        ref_a = fresh.get_reference("Lib.A")
        ref_b = fresh.get_reference("Lib.B")
        assert ref_a is not None and ref_a.model_id == "Lib.A"
        assert ref_b is not None and ref_b.model_id == "Lib.B"
        assert ref_a.test_id != ref_b.test_id

    def test_register_skips_id_taken_on_disk_but_missing_from_index(self, tmp_path):
        """An unindexed (e.g. mid-write, unreadable) ref file still blocks
        its ID from being handed out."""
        tmp_path.mkdir(exist_ok=True)
        (tmp_path / "ref_0001.json").write_text("not json", encoding="utf-8")
        index = RefIndex(tmp_path)
        assert index.register("Lib.New") != "0001"

    def test_cleanup_obsolete_removes_orphaned_role_dirs(
        self, store_with_primary, tmp_path
    ):
        """Deleting an obsolete ref must also delete its soft_checks/ and
        companions/ dirs so a reused ID cannot inherit them."""
        store, test = store_with_primary
        store.add_soft_check(
            test.model_id,
            "experiment",
            series(
                "experiment",
                time=[0.0, 1.0],
                variables=[{"index": 1, "name": "x", "values": [0.0, 1.0]}],
            ),
        )
        ext = tmp_path / "rig.csv"
        ext.write_text("time,x\n0,1\n", encoding="utf-8")
        store.add_companion(test.model_id, "rig", path=ext)
        store.freeze_companion(test.model_id, "rig")

        sc_dir = store.ref_dir / "soft_checks" / "ref_0001"
        co_dir = store.ref_dir / "companions" / "ref_0001"
        assert sc_dir.is_dir() and co_dir.is_dir()

        _set_status(store, test.model_id, "obsolete")
        assert store.cleanup_obsolete() == 1
        assert not sc_dir.exists()
        assert not co_dir.exists()


class TestIdHighWaterMark:
    """IDs are never reused, even after the highest ref file is deleted.

    ``next_id()`` scanning alone hands a freed ID to a different model, and
    external artifacts keyed on ref_NNNN (report dirs, notes) then point at
    the wrong test. A per-partition high-water dot-file closes the gap.
    """

    def test_id_freed_by_cleanup_is_not_reused(self, sample_models_dir, tmp_path):
        config = Config(source_path=sample_models_dir, reference_root=tmp_path / "refs")
        store = _mk_store(sample_models_dir, tmp_path)
        store.store_reference(
            _mk_model("Lib.A"), _mk_result("Lib.A"), provenance=PROVENANCE
        )
        store.store_reference(
            _mk_model("Lib.B"), _mk_result("Lib.B"), provenance=PROVENANCE
        )
        assert store.get_reference("Lib.B").test_id == "0002"

        _set_status(store, "Lib.B", "obsolete")
        assert store.cleanup_obsolete() == 1

        # Fresh process, fresh scan: 0002 is gone from disk but still spent.
        fresh = ReferenceStore(config)
        fresh.store_reference(
            _mk_model("Lib.C"), _mk_result("Lib.C"), provenance=PROVENANCE
        )
        assert fresh.get_reference("Lib.C").test_id == "0003"

    def test_high_water_file_is_not_indexed_as_a_ref(self, tmp_path):
        index = RefIndex(tmp_path)
        index.register("Lib.A")
        assert (tmp_path / ".id_high_water.json").exists()
        assert list(RefIndex(tmp_path).all_tests()) == ["0001"]

    def test_scan_only_fallback_when_no_high_water_file(self, tmp_path):
        """Partitions written before the mark existed keep allocating from
        the scan — old data, not old code."""
        (tmp_path / "ref_0007.json").write_text(
            json.dumps({"model_id": "Lib.Old", "test_id": "0007", "status": "active"}),
            encoding="utf-8",
        )
        assert not (tmp_path / ".id_high_water.json").exists()
        assert RefIndex(tmp_path).register("Lib.New") == "0008"

    def test_corrupt_high_water_file_falls_back_to_scan(self, tmp_path):
        """An unreadable mark must degrade to scan-only allocation, never
        crash an accept or hand out a low ID."""
        (tmp_path / "ref_0007.json").write_text(
            json.dumps({"model_id": "Lib.Old", "test_id": "0007", "status": "active"}),
            encoding="utf-8",
        )
        (tmp_path / ".id_high_water.json").write_text("{not json", encoding="utf-8")
        assert RefIndex(tmp_path).register("Lib.New") == "0008"

    def test_mark_write_failure_warns_but_never_fails_the_accept(
        self, tmp_path, monkeypatch, caplog
    ):
        """Losing the mark costs only the never-reused guarantee for the
        partition — the register itself must still succeed."""
        import logging

        import dstf.storage.ref_index as ri

        real = ri._atomic_write_text

        def _flaky(path, text, **kwargs):
            if path.name == ".id_high_water.json":
                raise OSError("disk says no")
            return real(path, text, **kwargs)

        monkeypatch.setattr(ri, "_atomic_write_text", _flaky)
        with caplog.at_level(logging.WARNING):
            assert RefIndex(tmp_path).register("Lib.A") == "0001"
        assert any("high-water" in r.message for r in caplog.records)
        assert not (tmp_path / ".id_high_water.json").exists()

    def test_concurrent_bump_cannot_regress_the_mark(self, tmp_path):
        """A slower writer with a lower ID must not clobber a concurrent
        higher claim (A5 review finding 1). The competitor's O_EXCL-claimed
        ref file is on disk before any bump it makes, so folding the disk
        scan into the written value makes last-writer-wins harmless — even
        when the competitor's own mark write is then overwritten."""
        index = RefIndex(tmp_path)
        # Competitor process: claimed ref_0006 (file on disk). Its own mark
        # write is about to be clobbered by ours — the worst interleaving,
        # represented here by the mark file simply not being there yet.
        (tmp_path / "ref_0006.json").write_text(
            json.dumps(
                {"model_id": "Lib.Other", "test_id": "0006", "status": "active"}
            ),
            encoding="utf-8",
        )
        # Our bump for the lower id 0005 lands last: it must fold the disk
        # scan and write 6, not 5 — a stored-mark-only max writes 5 here.
        index._bump_high_water("0005")
        assert index._read_high_water() == 6


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
        co_dir = store.ref_dir / "companions" / "ref_0001"
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


BAD_NAMES = ["a/b", "a\\b", "..", "a..b", "x:y", "w*ld", "q?m", 'a"b', "<x>", "p|q"]


class TestRoleNameValidation:
    @pytest.mark.parametrize("bad", BAD_NAMES)
    def test_add_soft_check_rejects_unsafe_name(self, store_with_primary, bad):
        store, test = store_with_primary
        with pytest.raises(ValueError) as exc:
            store.add_soft_check(
                test.model_id,
                bad,
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
                "../../evil",
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
                "a/b",
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
            "dymola-via-fmpy",
            series(
                "dymola-via-fmpy",
                time=[0.0],
                variables=[{"index": 1, "name": "x", "values": [0.0]}],
            ),
        )
        assert ok is True


# ---------------------------------------------------------------------------
# Finding 36 — duplicate model_id on scan: lowest ID wins, loudly
# ---------------------------------------------------------------------------


class TestDuplicateModelIdScan:
    def _write_ref(self, ref_dir: Path, test_id: str, model_id: str):
        ref_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "model_id": model_id,
            "test_id": test_id,
            "status": "active",
            "n_vars": 0,
            "time": [],
            "variables": [],
        }
        (ref_dir / f"ref_{test_id}.json").write_text(json.dumps(data), encoding="utf-8")

    def test_lowest_id_wins_with_warning(self, tmp_path, caplog):
        self._write_ref(tmp_path, "0001", "Lib.Dup")
        self._write_ref(tmp_path, "0002", "Lib.Dup")

        with caplog.at_level(logging.WARNING, logger="dstf.storage.ref_index"):
            index = RefIndex(tmp_path)
            assert index.get_id("Lib.Dup") == "0001"

        messages = [r.getMessage() for r in caplog.records]
        assert any("ref_0001" in m and "ref_0002" in m for m in messages)

    def test_lowest_wins_regardless_of_scan_order(self, tmp_path, caplog):
        """Numeric comparison, not string order of the scan."""
        self._write_ref(tmp_path, "0010", "Lib.Dup")
        self._write_ref(tmp_path, "0002", "Lib.Dup")
        with caplog.at_level(logging.WARNING, logger="dstf.storage.ref_index"):
            index = RefIndex(tmp_path)
            assert index.get_id("Lib.Dup") == "0002"


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
        source_package=model_id.split(".")[0],
        short_name=model_id.rsplit(".", 1)[-1],
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


def test_no_shipped_example_partition_carries_a_high_water_mark():
    """The mark keeps an id spent after the baseline that held it is
    removed. A shipped partition has lost none: the scan gives the same
    next id without it, and the two an accept on a scratch copy left in
    the Modelica example were the only ones among the shipped partitions."""
    examples = Path(__file__).parent.parent / "examples"
    partitions = sorted({ref.parent for ref in examples.rglob("ref_*.json")})
    assert len(partitions) >= 7, partitions
    assert sorted(examples.rglob(".id_high_water.json")) == []
    for partition in partitions:
        highest = max(
            int(ref.stem[len("ref_") :]) for ref in partition.glob("ref_*.json")
        )
        assert RefIndex(partition).next_id() == f"{highest + 1:04d}"
