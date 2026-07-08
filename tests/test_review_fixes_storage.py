"""Regression tests for the 2026-07-06 review — Theme 4: reference store integrity.

Decisions encoded here (see CODE_REVIEW_2026-07-06.md, findings 3, 31-36):

* ``_downsample`` never drops the first index, the last index, or event
  boundaries. When the linspace/event union overshoots ``max_points``,
  interior linspace candidates are dropped instead. If event indices alone
  exceed the budget, the events are evenly thinned but the endpoints stay.
* Every reference-store JSON write is atomic (tmp file in the same
  directory + replace) — an interrupted write must not corrupt a baseline.
* New IDs are claimed by exclusive file creation, so a stale in-memory
  index or a concurrent ``--accept`` in another process cannot allocate
  the same ID and silently overwrite another model's baseline.
* ``cleanup_obsolete`` also removes the deleted ref's orphaned
  ``soft_checks/ref_NNNN/`` and ``companions/ref_NNNN/`` directories, so
  a later reuse of the ID cannot inherit another model's role data.
* ``add_companion`` resolves relative paths against the CWD, stores the
  absolute path, and rejects missing files at registration time. The
  overlay loader keeps ref_dir-relative resolution as a legacy fallback.
* soft_check / companion names must be filename-safe — path separators,
  ``..``, and Windows-illegal characters are rejected everywhere a name
  reaches the filesystem.
* Duplicate active ``model_id`` across ref files: the scan warns naming
  both files and deterministically keeps the lowest-numbered one.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pytest

import dstf.storage.reference_store as rs_mod
from dstf.config import Config
from dstf.discovery.test_registry import TestModel
from dstf.simulators.base import TestResult, VariableResult
from dstf.storage.reference_store import (
    ReferenceStore,
    RefIndex,
    _downsample,
)


def _mk_model(model_id="Lib.Test1") -> TestModel:
    return TestModel(
        model_id=model_id,
        source_file=Path(""),
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
    store.store_reference(test, _mk_result())
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

        monkeypatch.setattr(rs_mod.time, "sleep", lambda _s: None)

        def boom(self, _other):
            raise OSError("simulated crash during replace")

        monkeypatch.setattr(rs_mod.Path, "replace", boom)
        with pytest.raises(OSError):
            rs_mod._atomic_write_text(target, '{"model_id": "Lib.B"}')

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

        store.store_reference(test, _mk_result())  # update existing ref
        store.add_soft_check(
            test.model_id,
            "experiment",
            time=[0.0, 1.0],
            variables=[{"index": 1, "name": "x", "values": [0.0, 1.0]}],
        )
        store.add_companion(test.model_id, "rig", path=ext)
        store.freeze_companion(test.model_id, "rig")
        store.set_status(test.model_id, "skip")

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

        store1.store_reference(_mk_model("Lib.A"), _mk_result("Lib.A"))
        store2.store_reference(_mk_model("Lib.B"), _mk_result("Lib.B"))

        fresh = ReferenceStore(config)
        ref_a = fresh.get_reference("Lib.A")
        ref_b = fresh.get_reference("Lib.B")
        assert ref_a is not None and ref_a["model_id"] == "Lib.A"
        assert ref_b is not None and ref_b["model_id"] == "Lib.B"
        assert ref_a["test_id"] != ref_b["test_id"]

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
            time=[0.0, 1.0],
            variables=[{"index": 1, "name": "x", "values": [0.0, 1.0]}],
        )
        ext = tmp_path / "rig.csv"
        ext.write_text("time,x\n0,1\n", encoding="utf-8")
        store.add_companion(test.model_id, "rig", path=ext)
        store.freeze_companion(test.model_id, "rig")

        sc_dir = store.ref_dir / "soft_checks" / "ref_0001"
        co_dir = store.ref_dir / "companions" / "ref_0001"
        assert sc_dir.is_dir() and co_dir.is_dir()

        store.set_status(test.model_id, "obsolete")
        assert store.cleanup_obsolete() == 1
        assert not sc_dir.exists()
        assert not co_dir.exists()


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
                time=[0.0],
                variables=[{"index": 1, "name": "x", "values": [0.0]}],
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
                time=[0.0],
                variables=[{"index": 1, "name": "x", "values": [0.0]}],
            )
        assert not (store.ref_dir / "evil.json").exists()
        assert not (store.ref_dir.parent / "evil.json").exists()

    def test_error_message_names_offending_character(self, store_with_primary):
        store, test = store_with_primary
        with pytest.raises(ValueError, match="/"):
            store.add_soft_check(
                test.model_id,
                "a/b",
                time=[0.0],
                variables=[{"index": 1, "name": "x", "values": [0.0]}],
            )

    def test_good_names_still_accepted(self, store_with_primary):
        store, test = store_with_primary
        ok = store.add_soft_check(
            test.model_id,
            "dymola-via-fmpy",  # cross_backend.py's constant must stay legal
            time=[0.0],
            variables=[{"index": 1, "name": "x", "values": [0.0]}],
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

        with caplog.at_level(logging.WARNING, logger="dstf.storage.reference_store"):
            index = RefIndex(tmp_path)
            assert index.get_id("Lib.Dup") == "0001"

        messages = [r.getMessage() for r in caplog.records]
        assert any("ref_0001" in m and "ref_0002" in m for m in messages)

    def test_lowest_wins_regardless_of_scan_order(self, tmp_path, caplog):
        """Numeric comparison, not string order of the scan."""
        self._write_ref(tmp_path, "0010", "Lib.Dup")
        self._write_ref(tmp_path, "0002", "Lib.Dup")
        with caplog.at_level(logging.WARNING, logger="dstf.storage.reference_store"):
            index = RefIndex(tmp_path)
            assert index.get_id("Lib.Dup") == "0002"
