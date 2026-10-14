"""Tests for the D66 baseline-role split — primary / companion / soft_check.

Storage-layer tests for :class:`ReferenceStore` soft_check and companion
APIs. Covers the three-role separation at the persistence level:
primary (existing), soft_checks (scored with ``against:`` inside ``warn``),
companions (plot-only overlays, never scored against).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pytest

from dstf.backends.types import TestResult, VariableResult
from dstf.config import Config
from dstf.discovery.test_registry import TestModel
from dstf.reporting.overlay_loader import load_overlays
from dstf.storage.reference_store import (
    ReferenceStore,
)
from dstf.storage.roles import _read_variable_mapping


def _make_test_model(model_id="Lib.Test1"):
    return TestModel(
        model_id=model_id,
        source_file=None,
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

    def test_the_written_entries_carry_an_expression(self, store_with_primary):
        """`soft-check add` hands this writer the entries of a file someone
        else produced — an older one has no ``expression`` key at all. A
        writer never leaves the field out: the imported entry is written
        with the empty text, in the same key order ``accept`` produces, so
        an entry WITHOUT the key is one no writer of this version made."""
        store, test = store_with_primary
        store.add_soft_check(
            test.model_id,
            "imported",
            time=[0.0, 1.0],
            variables=[
                {"index": 1, "name": "x", "values": [0.0, 1.0]},
                {
                    "index": 2,
                    "name": "unitTests.x[1]",
                    "expression": "pipe.T[1]",
                    "values": [0.0, 2.0],
                },
            ],
        )
        sc_file = store.ref_dir / "soft_checks" / "ref_0001" / "imported.json"
        written = json.loads(sc_file.read_text())["variables"]
        assert [list(entry) for entry in written] == [
            ["index", "name", "expression", "values"],
            ["index", "name", "expression", "values"],
        ]
        assert [entry["expression"] for entry in written] == ["", "pipe.T[1]"]

    def test_an_entry_in_another_key_order_is_written_in_accepts(
        self, store_with_primary
    ):
        """The order is IMPOSED, not patched in beside whatever ``name`` was
        found: an entry that already carried ``expression``, or one whose
        ``name`` came after its ``values``, kept its own order, and two
        files written by one command read differently. Any key this writer
        does not name follows, in its own order — and the caller's dict is
        never mutated (it may be a live result's)."""
        store, test = store_with_primary
        entry = {
            "values": [0.0, 1.0],
            "unit": "K",
            "expression": "pipe.T[1]",
            "name": "unitTests.x[1]",
            "index": 1,
        }
        before = copy.deepcopy(entry)
        store.add_soft_check(
            test.model_id, "odd-order", time=[0.0, 1.0], variables=[entry]
        )
        sc_file = store.ref_dir / "soft_checks" / "ref_0001" / "odd-order.json"
        (written,) = json.loads(sc_file.read_text())["variables"]
        assert list(written) == ["index", "name", "expression", "values", "unit"]
        # Values as well as key order: the caller's entry may be a live
        # result's, and this writer reorders and defaults fields.
        assert entry == before and list(entry) == list(before)

    def test_a_null_expression_is_written_as_the_empty_text(self, store_with_primary):
        """JSON someone else wrote may hold `"expression": null` — an
        `soft-check add` source, a hand-edited file. Every reader of the
        field takes `""` for "no expression" (`display_label`, the soft
        check, the migrate hint); `None` is a third value none of them
        tests for, and it would be written back out as `null`."""
        store, test = store_with_primary
        store.add_soft_check(
            test.model_id,
            "nulled",
            time=[0.0, 1.0],
            variables=[
                {
                    "index": 1,
                    "name": "unitTests.x[1]",
                    "expression": None,
                    "values": [0.0, 1.0],
                }
            ],
        )
        sc_file = store.ref_dir / "soft_checks" / "ref_0001" / "nulled.json"
        (written,) = json.loads(sc_file.read_text())["variables"]
        assert written["expression"] == ""

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

    def test_the_command_says_a_companion_that_is_not_there(
        self, store_with_primary, tmp_path, capsys
    ):
        """`companion freeze <model> <typo>` exited 0 saying "is already
        frozen or does not exist". A companion that does not exist is exit
        1 and says so; one already frozen stays exit 0."""
        from dstf.cli import main

        store, test = store_with_primary
        external = tmp_path / "rig_data.csv"
        external.write_text("time,value\n0,0\n1,1\n")
        store.add_companion(test.model_id, "rig", path=external)
        where = [
            "--source-path",
            str(store.config.source_path),
            "--reference-root",
            str(store.config.reference_root),
            "--backend",
            str(store.config.backend_name),
        ]

        def freeze(name):
            rc = main(["companion", "freeze", test.model_id, name, *where])
            return rc, capsys.readouterr().out.strip().splitlines()[-1]

        assert freeze("rgi") == (
            1,
            f"Companion 'rgi' not found on {test.model_id}",
        )
        assert freeze("rig") == (0, f"Frozen companion 'rig' for {test.model_id}")
        assert freeze("rig") == (
            0,
            f"Companion 'rig' of {test.model_id} is already frozen",
        )

    def test_freeze_json_keeps_data_and_metadata_apart(
        self, store_with_primary, tmp_path
    ):
        """A frozen JSON companion's data goes to ``<name>.data.json``.

        The metadata file is ``<name>.json``; writing the data there too
        meant the metadata write destroyed the trajectory that had just
        been copied, leaving a companion that could never be plotted.
        """
        store, test = store_with_primary
        payload = {
            "time": [0.0, 1.0],
            "variables": [{"name": "x", "values": [1.0, 2.0]}],
        }
        external = tmp_path / "analytical.json"
        external.write_text(json.dumps(payload), encoding="utf-8")
        store.add_companion(test.model_id, "analytical", path=external)

        assert store.freeze_companion(test.model_id, "analytical") is True

        co_dir = store.ref_dir / "companions" / "ref_0001"
        assert json.loads((co_dir / "analytical.data.json").read_text()) == payload

        companions = store.get_companions(test.model_id)
        assert set(companions) == {"analytical"}
        assert companions["analytical"].kind == "frozen"
        assert companions["analytical"].format == "json"
        assert companions["analytical"].data_file == "analytical.data.json"

        overlays = load_overlays(store, test.model_id)
        by_name = {ov.name: ov for ov in overlays}
        assert by_name["analytical"].status == "loaded"
        assert by_name["analytical"].variables["x"].values == [1.0, 2.0]

    def test_freeze_does_not_clobber_another_companion(
        self, store_with_primary, tmp_path
    ):
        store, test = store_with_primary
        other = tmp_path / "rig.csv"
        other.write_text("time,value\n0,0\n", encoding="utf-8")
        store.add_companion(test.model_id, "rig", path=other)
        store.freeze_companion(test.model_id, "rig")

        src = tmp_path / "analytical.json"
        src.write_text('{"time": [], "variables": []}', encoding="utf-8")
        store.add_companion(test.model_id, "analytical", path=src)
        store.freeze_companion(test.model_id, "analytical")

        co_dir = store.ref_dir / "companions" / "ref_0001"
        assert (co_dir / "rig.csv").read_text() == "time,value\n0,0\n"
        assert set(store.get_companions(test.model_id)) == {"rig", "analytical"}

    def test_the_variable_mapping_round_trips(self, store_with_primary, tmp_path):
        """The companion records HOW its variables map onto the test's, in
        its own metadata. That record is the whole story at read time."""
        store, test = store_with_primary
        external = tmp_path / "rig.csv"
        external.write_text("time,sensor_a\n0,0\n1,1\n", encoding="utf-8")
        store.add_companion(
            test.model_id, "rig", path=external, variables={"x": "sensor_a"}
        )
        meta = json.loads(
            (store.ref_dir / "companions" / "ref_0001" / "rig.json").read_text()
        )
        assert meta["variables"] == {"x": "sensor_a"}
        assert store.get_companions(test.model_id)["rig"].variables == {"x": "sensor_a"}

    def test_a_companion_with_no_mapping_still_writes_the_key(
        self, store_with_primary, tmp_path
    ):
        """A writer never leaves the field out, so a registration WITHOUT
        the key is one no writer of this version made."""
        store, test = store_with_primary
        external = tmp_path / "rig.csv"
        external.write_text("time,h\n0,0\n", encoding="utf-8")
        store.add_companion(test.model_id, "rig", path=external)
        meta = json.loads(
            (store.ref_dir / "companions" / "ref_0001" / "rig.json").read_text()
        )
        assert meta["variables"] == {}
        assert store.get_companions(test.model_id)["rig"].variables == {}

    def test_metadata_written_before_the_key_existed_reads_as_no_mapping(
        self, store_with_primary, tmp_path
    ):
        """Old data is tolerated: a registration from before the mapping
        existed falls back on name, then expression."""
        store, test = store_with_primary
        external = tmp_path / "rig.csv"
        external.write_text("time,h\n0,0\n", encoding="utf-8")
        co_dir = store.ref_dir / "companions" / "ref_0001"
        co_dir.mkdir(parents=True)
        (co_dir / "rig.json").write_text(
            json.dumps({"kind": "external", "format": "csv", "path": str(external)}),
            encoding="utf-8",
        )
        assert store.get_companions(test.model_id)["rig"].variables == {}

    def test_freeze_carries_the_mapping_through(self, store_with_primary, tmp_path):
        store, test = store_with_primary
        external = tmp_path / "rig.csv"
        external.write_text("time,sensor_a\n0,0\n1,1\n", encoding="utf-8")
        store.add_companion(
            test.model_id, "rig", path=external, variables={"x": "sensor_a"}
        )
        assert store.freeze_companion(test.model_id, "rig") is True
        companions = store.get_companions(test.model_id)
        assert companions["rig"].kind == "frozen"
        assert companions["rig"].variables == {"x": "sensor_a"}

    def test_data_suffix_name_rejected(self, store_with_primary, tmp_path):
        """``<x>.data`` would own the filename a frozen ``<x>`` writes into."""
        store, test = store_with_primary
        src = tmp_path / "x.json"
        src.write_text("{}", encoding="utf-8")
        with pytest.raises(ValueError, match=r"\.data"):
            store.add_companion(test.model_id, "analytical.data", path=src)

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


class TestReadingAMalformedVariableMap:
    """A record that cannot be read as pairs is MALFORMED, not absent:
    every finding is said, so the author can see it on the report's
    overlay table, and the companion keeps its record's authority
    instead of quietly guessing from spellings (D132)."""

    def test_a_registration_from_before_the_key_records_nothing(self):
        assert _read_variable_mapping({"kind": "external"}) == ({}, ())

    def test_the_empty_object_a_no_map_registration_writes_records_nothing(self):
        assert _read_variable_mapping({"variables": {}}) == ({}, ())

    @pytest.mark.parametrize("bad", ["", None, 3, ["sensor_a"], {"n": "s"}])
    def test_an_entry_that_is_not_a_variable_name_is_dropped_and_said(self, bad):
        mapping, warnings = _read_variable_mapping(
            {"variables": {"x": bad, "y": "sensor_b"}}
        )
        assert mapping == {"y": "sensor_b"}
        assert warnings == ("mapping for x is not a variable name",)

    @pytest.mark.parametrize("bad", ["x=y", ["x", "y"], 7, None, True])
    def test_a_variables_that_is_not_an_object_is_a_record_that_says_so(self, bad):
        mapping, warnings = _read_variable_mapping({"variables": bad})
        assert mapping == {}
        assert len(warnings) == 1 and "pairs" in warnings[0]

    def test_the_findings_reach_the_companion_and_then_the_overlay(
        self, store_with_primary, tmp_path
    ):
        """End to end over the real store: a hand-edited metadata file."""
        store, test = store_with_primary
        external = tmp_path / "rig.json"
        external.write_text(
            json.dumps(
                {"time": [0, 1], "variables": [{"name": "h", "values": [1, 2]}]}
            ),
            encoding="utf-8",
        )
        co_dir = store.ref_dir / "companions" / "ref_0001"
        co_dir.mkdir(parents=True)
        (co_dir / "rig.json").write_text(
            json.dumps(
                {
                    "kind": "external",
                    "format": "json",
                    "path": str(external),
                    "variables": {"x": None},
                }
            ),
            encoding="utf-8",
        )
        companion = store.get_companions(test.model_id)["rig"]
        assert companion.variables == {}
        assert companion.mapping_warnings == ("mapping for x is not a variable name",)

        overlay = load_overlays(store, test.model_id)[0]
        assert overlay.mapping_malformed is True
        assert overlay.warnings == ["mapping for x is not a variable name"]


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


# ---------------------------------------------------------------------------
# `dstf companion add --map` — the CLI that writes the record
# ---------------------------------------------------------------------------


class TestCompanionMapCLI:
    """``--map TEST_VAR=COMPANION_VAR``, validated against both files at
    registration: a mapping naming something neither side has would draw
    nothing and say nothing, months later, in a report."""

    @staticmethod
    def _run(store, test, *argv):
        from dstf.cli import main

        return main(
            [
                "--source-path",
                str(store.config.source_path),
                "--reference-root",
                str(store.config.reference_root),
                "companion",
                *argv,
            ]
        )

    @pytest.fixture
    def rig(self, tmp_path):
        path = tmp_path / "rig.csv"
        path.write_text("time,sensor_a,sensor_b\n0,0,0\n1,1,2\n", encoding="utf-8")
        return path

    def test_add_with_a_map_records_it(self, store_with_primary, rig, capsys):
        store, test = store_with_primary
        rc = self._run(
            store, test, "add", test.model_id, "rig", str(rig), "--map", "x=sensor_a"
        )
        assert rc == 0, capsys.readouterr()
        assert store.get_companions(test.model_id)["rig"].variables == {"x": "sensor_a"}

    def test_two_test_variables_may_name_one_companion_variable(
        self, store_with_primary, rig, capsys
    ):
        """One curve drawn twice is a legitimate thing to ask for."""
        store, test = store_with_primary
        t = np.linspace(0, 10, 11)
        store.store_reference(
            test,
            TestResult(
                model_id=test.model_id,
                success=True,
                variables=[
                    VariableResult(index=1, time=t, values=np.sin(t), name="x"),
                    VariableResult(index=2, time=t, values=np.cos(t), name="y"),
                ],
            ),
        )
        rc = self._run(
            store,
            test,
            "add",
            test.model_id,
            "rig",
            str(rig),
            "--map",
            "x=sensor_a",
            "--map",
            "y=sensor_a",
        )
        assert rc == 0, capsys.readouterr()
        assert store.get_companions(test.model_id)["rig"].variables == {
            "x": "sensor_a",
            "y": "sensor_a",
        }

    def test_an_unknown_test_variable_is_refused(self, store_with_primary, rig, capsys):
        store, test = store_with_primary
        rc = self._run(
            store, test, "add", test.model_id, "rig", str(rig), "--map", "nope=sensor_a"
        )
        assert rc == 1
        out = capsys.readouterr().out
        assert "nope" in out
        assert "rig" not in store.get_companions(test.model_id)

    def test_an_unknown_companion_variable_is_refused(
        self, store_with_primary, rig, capsys
    ):
        store, test = store_with_primary
        rc = self._run(
            store, test, "add", test.model_id, "rig", str(rig), "--map", "x=sensor_z"
        )
        assert rc == 1
        out = capsys.readouterr().out
        assert "sensor_z" in out
        assert "rig" not in store.get_companions(test.model_id)

    def test_a_map_without_an_equals_sign_is_refused(
        self, store_with_primary, rig, capsys
    ):
        store, test = store_with_primary
        rc = self._run(store, test, "add", test.model_id, "rig", str(rig), "--map", "x")
        assert rc == 1
        assert "TEST_VAR=COMPANION_VAR" in capsys.readouterr().out

    def test_one_test_variable_may_not_be_mapped_twice(
        self, store_with_primary, rig, capsys
    ):
        store, test = store_with_primary
        rc = self._run(
            store,
            test,
            "add",
            test.model_id,
            "rig",
            str(rig),
            "--map",
            "x=sensor_a",
            "--map",
            "x=sensor_b",
        )
        assert rc == 1
        assert "x" in capsys.readouterr().out

    def test_list_shows_the_mapping(self, store_with_primary, rig, capsys):
        store, test = store_with_primary
        self._run(
            store, test, "add", test.model_id, "rig", str(rig), "--map", "x=sensor_a"
        )
        capsys.readouterr()
        assert self._run(store, test, "list", test.model_id) == 0
        out = capsys.readouterr().out
        assert "x -> sensor_a" in out

    def test_a_model_with_no_baseline_is_told_so(self, store_with_primary, rig, capsys):
        """There is nothing to check the left-hand names against, and the
        user needs to hear that — not that the baseline they have not
        stored holds none of them."""
        store, test = store_with_primary
        rc = self._run(
            store, test, "add", "Lib.Missing", "rig", str(rig), "--map", "x=sensor_a"
        )
        assert rc == 1
        out = capsys.readouterr().out
        assert "No primary baseline" in out
        assert "Its variables are" not in out

    @pytest.mark.parametrize(
        "argv",
        [
            ("freeze", "Lib.Test1", "rig"),
            ("remove", "Lib.Test1", "rig"),
            ("list", "Lib.Test1"),
        ],
    )
    def test_only_add_accepts_a_map(self, store_with_primary, rig, capsys, argv):
        """Only ``add`` writes the record, and an option silently ignored
        is an option the user believes took effect."""
        store, test = store_with_primary
        assert (
            self._run(
                store,
                test,
                "add",
                test.model_id,
                "rig",
                str(rig),
                "--map",
                "x=sensor_a",
            )
            == 0
        )
        capsys.readouterr()
        # `--map` is `companion add`'s flag: no other action's parser has it.
        with pytest.raises(SystemExit) as exc:
            self._run(store, test, *argv, "--map", "x=sensor_b")
        assert exc.value.code == 2
        assert (
            f"dstf companion {argv[0]}: error: unrecognized arguments: "
            "--map x=sensor_b" in capsys.readouterr().err
        )
        # and nothing was done: the registration still says what it said
        companions = store.get_companions(test.model_id)
        assert companions["rig"].kind == "external"
        assert companions["rig"].variables == {"x": "sensor_a"}

    def test_list_over_every_model_shows_the_mapping(
        self, store_with_primary, rig, capsys
    ):
        store, test = store_with_primary
        self._run(
            store, test, "add", test.model_id, "rig", str(rig), "--map", "x=sensor_a"
        )
        capsys.readouterr()
        assert self._run(store, test, "list") == 0
        assert "x -> sensor_a" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# The roles where a test is scored: run, compare, the tool's rescore
# ---------------------------------------------------------------------------


def _leaf(**extra) -> dict:
    return {"metric": "nrmse", "variable": "x", "tolerance": 0.01, **extra}


def _warned(*leaves: dict) -> dict:
    """The primary criterion, and *leaves* under a ``warn`` beside it."""
    return {
        "combinator": "and",
        "children": [_leaf(), {"combinator": "warn", "children": list(leaves)}],
    }


#: A tree wrong in its FILE — wrong whatever a folder of baselines holds —
#: and the start of the sentence it is refused with where it is scored.
_WRONG_IN_THE_FILE = {
    "a stored soft check outside warn": (
        {"combinator": "and", "children": [_leaf(), _leaf(against="other")]},
        "metrics.children[1]: This criterion scores against 'other' outside a 'warn'",
    ),
    "a stored companion outside warn": (
        {"combinator": "and", "children": [_leaf(), _leaf(against="rig")]},
        "metrics.children[1]: This criterion scores against 'rig' outside a 'warn'",
    ),
    "a name nothing holds outside warn": (
        {"combinator": "and", "children": [_leaf(), _leaf(against="nosuch")]},
        "metrics.children[1]: This criterion scores against 'nosuch' outside a 'warn'",
    ),
    "warnings alone": (
        {"combinator": "warn", "children": [_leaf()]},
        "metrics: Every tree needs at least one criterion scored against the primary",
    ),
    "a weighted sum of opposite directions": (
        {
            "combinator": "weighted",
            "threshold": 1.0,
            "weights": [1.0, 1.0],
            "children": [
                _leaf(),
                {"metric": "tube", "variable": "x", "tube_width_mode": "rel"},
            ],
        },
        "metrics: A 'weighted' node cannot mix lower-is-better and higher-is-better",
    ),
}

#: A tree whose FILE is right, and which a folder may not hold a baseline
#: of: a criterion under ``warn`` naming what this folder does not store as
#: a soft check. `spec update` and the tool refuse to WRITE it here; where
#: it is scored it is an advisory criterion not scored, and the test passes.
_RIGHT_IN_THE_FILE = {
    "a name this folder does not hold, under warn": "nosuch",
    "a companion under warn": "rig",
}


class TestRolesWhereATestIsScored:
    """Where a test is scored, a tree is refused only for what is wrong in
    its FILE, never for what this ``<Backend>/<os>`` folder happens to hold:
    the same spec is scored in every folder, and a soft check is imported
    into one. A tree that is refused fails with every rule it breaks, each
    with its place in the tree, and no criterion scored."""

    @pytest.fixture
    def roles(self, store_with_primary, tmp_path):
        from dstf.backends.types import TestResult

        store, test = store_with_primary
        time = np.linspace(0, 10, 11)
        store.add_soft_check(
            test.model_id,
            "other",
            time=list(time),
            variables=[{"index": 1, "name": "x", "values": list(1.5 * np.sin(time))}],
        )
        rig = tmp_path / "rig.csv"
        rig.write_text("time,x\n0,0\n10,1\n")
        store.add_companion(test.model_id, "rig", str(rig))
        result = _make_test_result()
        assert isinstance(result, TestResult)
        return store, test, result

    def _compare(self, roles, tree: dict):
        from dstf.comparison.comparator import compare_one
        from dstf.comparison.tree_spec import parse_metric_tree

        store, test, result = roles
        test = copy.copy(test)
        test.metric_tree_spec = parse_metric_tree(tree)
        return compare_one(test, result, store)

    @pytest.mark.parametrize("broken", sorted(_WRONG_IN_THE_FILE))
    def test_a_tree_wrong_in_its_file_is_refused(self, roles, broken):
        from dstf.reporting.status import FAIL, classify, refusal

        tree, sentence = _WRONG_IN_THE_FILE[broken]
        comp = self._compare(roles, tree)
        assert classify(comp) == FAIL
        assert comp.variables == [] and comp.metric_tree is None
        reason = refusal(comp)
        assert reason is not None and reason.startswith(sentence), reason

    @pytest.mark.parametrize("case", sorted(_RIGHT_IN_THE_FILE))
    def test_a_baseline_this_folder_does_not_hold_is_advisory(self, roles, case):
        from dstf.reporting.status import PASS, classify, refusal

        store, _, _ = roles
        name = _RIGHT_IN_THE_FILE[case]
        comp = self._compare(roles, _warned(_leaf(against=name)))
        assert classify(comp) == PASS, comp.error_message
        assert refusal(comp) is None
        errors = [v.diagnostics.get("error") for v in comp.variables if not v.passed]
        folder = f"{store.config.backend}/{store.config.os_name}"
        assert errors == [f"no soft check {name!r} is stored for {folder}"]

    def test_a_soft_check_under_warn_warns_and_the_test_passes(self, roles):
        from dstf.reporting.status import refusal

        comp = self._compare(roles, _warned(_leaf(against="other")))
        assert comp.passed, comp.error_message
        assert refusal(comp) is None
        assert comp.metric_tree is not None

    def test_every_rule_the_tree_breaks_is_said(self, roles):
        """A tree that breaks two rules says both, each where it is."""
        from dstf.reporting.status import refusal

        tree = {
            "combinator": "warn",
            "children": [
                {
                    "combinator": "weighted",
                    "threshold": 1.0,
                    "weights": [1.0, 1.0],
                    "children": [
                        _leaf(),
                        {"metric": "tube", "variable": "x", "tube_width_mode": "rel"},
                    ],
                }
            ],
        }
        reason = refusal(self._compare(roles, tree))
        assert reason is not None
        assert "metrics.children[0]: A 'weighted' node cannot mix" in reason
        assert "metrics: Every tree needs at least one criterion" in reason

    def test_a_tree_wrong_in_its_file_with_no_baseline_is_refused(
        self, sample_models_dir, tmp_path
    ):
        """No accept can repair a tree wrong in its file, so the refusal
        wins over NO_REF ("accept it"), as a base tolerance that is no
        number and a result that holds nothing the test tracks do."""
        from dstf.comparison.comparator import compare_one
        from dstf.comparison.tree_spec import parse_metric_tree
        from dstf.reporting.status import FAIL, classify, refusal

        store = ReferenceStore(
            Config(source_path=sample_models_dir, reference_root=tmp_path / "r")
        )
        test = copy.copy(_make_test_model())
        tree, sentence = _WRONG_IN_THE_FILE["warnings alone"]
        test.metric_tree_spec = parse_metric_tree(tree)
        comp = compare_one(test, _make_test_result(), store)
        assert store.get_reference(test.model_id) is None
        assert classify(comp) == FAIL
        assert (refusal(comp) or "").startswith(sentence)
        assert comp.has_reference is False


class TestWhatEachRuleReads:
    """Each baseline-role rule reads the file alone or the folder too. The
    file's rules are the ones a test is refused by where it is scored; the
    full check (`spec update`, the tool) adds the folder's."""

    def test_the_file_s_rules_read_no_folder(self):
        from dstf.comparison.tree_spec import parse_metric_tree
        from dstf.comparison.validator import validate_tree

        for tree, sentence in _WRONG_IN_THE_FILE.values():
            errors = validate_tree(parse_metric_tree(tree))
            assert errors and str(errors[0]).startswith(sentence), errors
        for name in _RIGHT_IN_THE_FILE.values():
            assert validate_tree(parse_metric_tree(_warned(_leaf(against=name)))) == []

    def test_the_full_check_refuses_what_this_folder_does_not_hold(self):
        from dstf.comparison.tree_spec import parse_metric_tree
        from dstf.comparison.validator import role_lookup_from_names, validate_tree

        held = role_lookup_from_names(soft_checks=["other"], companions=["rig"])
        nosuch = validate_tree(
            parse_metric_tree(_warned(_leaf(against="nosuch"))), held
        )
        assert [e.message[:40] for e in nosuch] == [
            "This criterion scores against 'nosuch', "
        ]
        rig = validate_tree(parse_metric_tree(_warned(_leaf(against="rig"))), held)
        assert "the companion 'rig'" in rig[0].message
        assert (
            validate_tree(parse_metric_tree(_warned(_leaf(against="other"))), held)
            == []
        )
