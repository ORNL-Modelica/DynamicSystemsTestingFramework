"""Tests for reporting/overlay_loader.py — A2 / idea #50.

Soft_checks come from the reference store; companions come from external
(or frozen) files. Loader tolerates every failure mode gracefully — a
companion file that has moved or is unparseable must not fail the
report render.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pytest

from dstf.reporting.overlay_loader import (
    Overlay,
    OverlayVariable,
    attach_overlays_to_trajectories,
    load_overlays,
    overlay_summary,
)


@dataclass
class _FakeCompanion:
    name: str
    kind: str
    format: str = "json"
    path: Optional[str] = None
    data_file: Optional[str] = None
    provenance: dict = field(default_factory=dict)


@dataclass
class _FakeBaseline:
    name: str
    time: list
    variables: list


class _FakeStore:
    """Minimal ReferenceStore surrogate for loader unit tests.

    The loader only touches ``get_soft_checks``, ``get_companions``,
    ``_companion_dir_for``, and ``ref_dir``. Use a tiny fake rather than
    dragging the real ReferenceStore + on-disk ref file format in.
    """

    def __init__(self, ref_dir: Path, soft_checks=None, companions=None, companion_dir=None):
        self.ref_dir = ref_dir
        self._soft_checks = soft_checks or {}
        self._companions = companions or {}
        self._companion_dir = companion_dir

    def get_soft_checks(self, model_id):
        return self._soft_checks

    def get_companions(self, model_id):
        return self._companions

    def _companion_dir_for(self, model_id):
        return self._companion_dir


class TestSoftCheckLoading:
    def test_soft_check_flattens_to_overlay_vars(self, tmp_path):
        baseline = _FakeBaseline(
            name="experiment",
            time=[0.0, 1.0, 2.0],
            variables=[
                {"name": "h", "values": [1.0, 0.5, 0.1]},
                {"name": "v", "values": [0.0, -1.0, -2.0]},
            ],
        )
        store = _FakeStore(tmp_path, soft_checks={"experiment": baseline})

        overlays = load_overlays(store, "M")
        assert len(overlays) == 1
        ov = overlays[0]
        assert ov.role == "soft_check"
        assert ov.status == "loaded"
        assert set(ov.variables.keys()) == {"h", "v"}
        assert ov.variables["h"].values == [1.0, 0.5, 0.1]
        assert ov.variables["h"].time == [0.0, 1.0, 2.0]

    def test_soft_check_missing_values_skipped(self, tmp_path):
        baseline = _FakeBaseline(
            name="experiment",
            time=[0.0, 1.0],
            variables=[
                {"name": "h", "values": [1.0, 0.5]},
                {"name": "v"},  # no values
                {"values": [1, 2]},  # no name
            ],
        )
        store = _FakeStore(tmp_path, soft_checks={"experiment": baseline})

        overlays = load_overlays(store, "M")
        assert set(overlays[0].variables.keys()) == {"h"}

    def test_soft_check_empty_marked_invalid(self, tmp_path):
        baseline = _FakeBaseline(name="experiment", time=[], variables=[])
        store = _FakeStore(tmp_path, soft_checks={"experiment": baseline})
        overlays = load_overlays(store, "M")
        assert overlays[0].status == "invalid"


class TestCompanionJSON:
    def test_loads_json_with_time_plus_variables(self, tmp_path):
        data_file = tmp_path / "ext.json"
        data_file.write_text(json.dumps({
            "time": [0.0, 1.0, 2.0],
            "variables": [
                {"name": "h", "values": [1.0, 2.0, 3.0]},
            ],
        }))
        companion = _FakeCompanion(
            name="analytical",
            kind="external",
            format="json",
            path=str(data_file),
        )
        store = _FakeStore(tmp_path, companions={"analytical": companion})

        overlays = load_overlays(store, "M")
        assert overlays[0].status == "loaded"
        assert overlays[0].role == "companion"
        assert overlays[0].variables["h"].values == [1.0, 2.0, 3.0]

    def test_relative_path_resolved_against_ref_dir(self, tmp_path):
        data_file = tmp_path / "companions" / "analytical.json"
        data_file.parent.mkdir()
        data_file.write_text(json.dumps({
            "time": [0.0, 1.0],
            "variables": [{"name": "h", "values": [0.1, 0.9]}],
        }))
        companion = _FakeCompanion(
            name="analytical",
            kind="external",
            format="json",
            path="companions/analytical.json",  # relative
        )
        store = _FakeStore(tmp_path, companions={"analytical": companion})
        overlays = load_overlays(store, "M")
        assert overlays[0].status == "loaded"

    def test_missing_file_marked_missing(self, tmp_path):
        companion = _FakeCompanion(
            name="analytical",
            kind="external",
            format="json",
            path=str(tmp_path / "does_not_exist.json"),
        )
        store = _FakeStore(tmp_path, companions={"analytical": companion})
        overlays = load_overlays(store, "M")
        assert overlays[0].status == "missing"
        assert "not found" in overlays[0].note

    def test_invalid_json_marked_invalid(self, tmp_path):
        data_file = tmp_path / "broken.json"
        data_file.write_text("not { valid json")
        companion = _FakeCompanion(
            name="broken",
            kind="external",
            format="json",
            path=str(data_file),
        )
        store = _FakeStore(tmp_path, companions={"broken": companion})
        overlays = load_overlays(store, "M")
        assert overlays[0].status == "invalid"
        assert "parse error" in overlays[0].note

    def test_frozen_companion_resolves_via_companion_dir(self, tmp_path):
        co_dir = tmp_path / "companions_dir"
        co_dir.mkdir()
        data_file = co_dir / "rig.data.json"
        data_file.write_text(json.dumps({
            "time": [0.0, 1.0],
            "variables": [{"name": "h", "values": [0.0, 1.0]}],
        }))
        companion = _FakeCompanion(
            name="rig",
            kind="frozen",
            format="json",
            data_file="rig.data.json",
        )
        store = _FakeStore(
            tmp_path, companions={"rig": companion}, companion_dir=co_dir,
        )
        overlays = load_overlays(store, "M")
        assert overlays[0].status == "loaded"
        assert overlays[0].variables["h"].values == [0.0, 1.0]


class TestCompanionCSV:
    def test_loads_wide_csv(self, tmp_path):
        csv_file = tmp_path / "rig.csv"
        csv_file.write_text("time,h,v\n0.0,1.0,0.0\n1.0,0.8,-0.2\n2.0,0.3,-0.5\n")
        companion = _FakeCompanion(
            name="rig",
            kind="external",
            format="csv",
            path=str(csv_file),
        )
        store = _FakeStore(tmp_path, companions={"rig": companion})
        overlays = load_overlays(store, "M")
        assert overlays[0].status == "loaded"
        assert overlays[0].variables["h"].values == [1.0, 0.8, 0.3]
        assert overlays[0].variables["v"].values == [0.0, -0.2, -0.5]
        assert overlays[0].variables["h"].time == [0.0, 1.0, 2.0]

    def test_csv_skips_non_numeric_rows(self, tmp_path):
        csv_file = tmp_path / "rig.csv"
        csv_file.write_text("time,h\n0.0,1.0\nnot-a-number,0.5\n2.0,3.0\n")
        companion = _FakeCompanion(
            name="rig", kind="external", format="csv", path=str(csv_file),
        )
        store = _FakeStore(tmp_path, companions={"rig": companion})
        overlays = load_overlays(store, "M")
        assert overlays[0].status == "loaded"
        # Bad row dropped; two good rows remain
        assert overlays[0].variables["h"].values == [1.0, 3.0]

    def test_empty_csv_marked_invalid(self, tmp_path):
        csv_file = tmp_path / "empty.csv"
        csv_file.write_text("")
        companion = _FakeCompanion(
            name="empty", kind="external", format="csv", path=str(csv_file),
        )
        store = _FakeStore(tmp_path, companions={"empty": companion})
        overlays = load_overlays(store, "M")
        assert overlays[0].status == "invalid"


class TestAttachOverlaysToTrajectories:
    def test_attaches_only_matching_variables(self):
        trajectories = [
            {"name": "h", "act_time": [0, 1], "act_values": [0, 1]},
            {"name": "v", "act_time": [0, 1], "act_values": [0, 1]},
        ]
        overlays = [
            Overlay(
                name="experiment", role="soft_check", status="loaded",
                variables={
                    "h": OverlayVariable(time=[0, 1, 2], values=[1, 0.5, 0.1]),
                },
            ),
        ]
        attach_overlays_to_trajectories(trajectories, overlays)
        assert len(trajectories[0]["overlays"]) == 1
        assert trajectories[0]["overlays"][0]["name"] == "experiment"
        # 'v' didn't match any overlay variable
        assert trajectories[1]["overlays"] == []

    def test_skips_non_loaded_overlays(self):
        trajectories = [{"name": "h"}]
        overlays = [
            Overlay(name="missing_one", role="companion", status="missing",
                    variables={"h": OverlayVariable(time=[], values=[])}),
        ]
        attach_overlays_to_trajectories(trajectories, overlays)
        assert trajectories[0]["overlays"] == []

    def test_works_on_nobaseline_trajectory_shape(self):
        """NO_REF plots use ``{index, name, time, values}`` instead of the
        compare-path's ``{name, act_time, act_values, ref_time, ref_values}``.
        The attach pass matches purely on ``name``, so the same helper
        threads sibling-backend overlays onto no-baseline plots too — this
        is the pre-accept cross-check path for a brand-new backend / OS.
        """
        nobaseline = [
            {"index": 1, "name": "h", "time": [0, 1, 2], "values": [1, 0.5, 0]},
            {"index": 2, "name": "v", "time": [0, 1, 2], "values": [0, -1, -2]},
        ]
        overlays = [
            Overlay(
                name="OpenModelica/linux", role="companion",
                kind="sibling-backend", status="loaded",
                variables={
                    "h": OverlayVariable(time=[0, 1, 2], values=[1, 0.6, 0.1]),
                },
            ),
        ]
        attach_overlays_to_trajectories(nobaseline, overlays)
        assert len(nobaseline[0]["overlays"]) == 1
        attached = nobaseline[0]["overlays"][0]
        assert attached["name"] == "OpenModelica/linux"
        assert attached["role"] == "companion"
        assert attached["kind"] == "sibling-backend"
        assert attached["time"] == [0, 1, 2]
        # 'v' didn't match any overlay variable
        assert nobaseline[1]["overlays"] == []


class TestOverlaySummary:
    def test_surfaces_missing_and_invalid(self):
        overlays = [
            Overlay(name="rig", role="companion", kind="external",
                    status="missing", note="file not found"),
            Overlay(name="experiment", role="soft_check", status="loaded",
                    variables={"h": OverlayVariable(time=[0], values=[1])}),
        ]
        rows = overlay_summary(overlays)
        assert len(rows) == 2
        assert rows[0]["status"] == "missing"
        assert "file not found" in rows[0]["note"]
        assert rows[1]["status"] == "loaded"
        assert rows[1]["variables"] == ["h"]


class TestStoreExceptionsSwallowed:
    """If the store itself blows up, the loader returns an empty list
    rather than crashing the report."""

    def test_soft_check_exception_swallowed(self, tmp_path):
        class BadStore(_FakeStore):
            def get_soft_checks(self, model_id):
                raise RuntimeError("oops")

        store = BadStore(tmp_path)
        assert load_overlays(store, "M") == []

    def test_companion_exception_swallowed(self, tmp_path):
        class BadStore(_FakeStore):
            def get_companions(self, model_id):
                raise RuntimeError("oops")

        store = BadStore(tmp_path)
        assert load_overlays(store, "M") == []

    def test_none_store_returns_empty(self, tmp_path):
        assert load_overlays(None, "M") == []


# ---------------------------------------------------------------------------
# Sibling-backend auto-companions
# ---------------------------------------------------------------------------

class _FakeConfigForSibling:
    """Minimum surface load_sibling_backend_overlays touches."""
    def __init__(self, reference_root, simulator_backend, os_name):
        self.reference_root = reference_root
        self.simulator_backend = simulator_backend
        self.os_name = os_name


def _write_ref(dir_path: Path, stem: str, model_id: str, varname: str = "h", status: str = "active"):
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / f"{stem}.json").write_text(json.dumps({
        "model_id": model_id,
        "status": status,
        "time": [0.0, 0.5, 1.0],
        "variables": [{"name": varname, "values": [1.0, 0.5, 0.0]}],
    }))


class TestSiblingBackendOverlays:
    def setup_method(self):
        # Clear the lru_cache between tests — it keys on the arg tuple so
        # a fresh tmp_path already dodges collisions, but being explicit
        # prevents surprises if a test reuses a path.
        from dstf.reporting.overlay_loader import (
            _sibling_backend_index,
        )
        _sibling_backend_index.cache_clear()

    def test_discovers_peer_backend_ref(self, tmp_path):
        from dstf.reporting.overlay_loader import (
            load_sibling_backend_overlays,
        )
        # Dymola/windows has a ref for model M; current partition is
        # OpenModelica/linux (which may or may not have its own).
        _write_ref(tmp_path / "Dymola" / "windows", "ref_0001", "M")
        _write_ref(tmp_path / "OpenModelica" / "linux", "ref_0001", "M")

        cfg = _FakeConfigForSibling(tmp_path, "OpenModelica", "linux")
        overlays = load_sibling_backend_overlays(cfg, "M")
        assert len(overlays) == 1
        assert overlays[0].name == "Dymola/windows"
        assert overlays[0].kind == "sibling-backend"
        assert overlays[0].role == "companion"
        assert overlays[0].status == "loaded"
        assert "h" in overlays[0].variables

    def test_excludes_current_partition(self, tmp_path):
        from dstf.reporting.overlay_loader import (
            load_sibling_backend_overlays,
        )
        # Only the current partition has a ref — nothing to overlay.
        _write_ref(tmp_path / "OpenModelica" / "linux", "ref_0001", "M")
        cfg = _FakeConfigForSibling(tmp_path, "OpenModelica", "linux")
        assert load_sibling_backend_overlays(cfg, "M") == []

    def test_skips_obsolete_refs(self, tmp_path):
        from dstf.reporting.overlay_loader import (
            load_sibling_backend_overlays,
        )
        _write_ref(tmp_path / "Dymola" / "windows", "ref_0001", "M",
                   status="obsolete")
        cfg = _FakeConfigForSibling(tmp_path, "OpenModelica", "linux")
        assert load_sibling_backend_overlays(cfg, "M") == []

    def test_multiple_siblings_produce_multiple_overlays(self, tmp_path):
        from dstf.reporting.overlay_loader import (
            load_sibling_backend_overlays,
        )
        _write_ref(tmp_path / "Dymola" / "windows", "ref_0001", "M")
        _write_ref(tmp_path / "FMPy" / "linux", "ref_0001", "M")
        cfg = _FakeConfigForSibling(tmp_path, "OpenModelica", "linux")
        overlays = load_sibling_backend_overlays(cfg, "M")
        names = {o.name for o in overlays}
        assert names == {"Dymola/windows", "FMPy/linux"}

    def test_no_config_means_no_sibling_overlays(self, tmp_path):
        """load_overlays without a config must behave identically to the
        old API — no auto-discovery."""
        from dstf.reporting.overlay_loader import load_overlays
        _write_ref(tmp_path / "Dymola" / "windows", "ref_0001", "M")
        store = _FakeStore(tmp_path / "OpenModelica" / "linux")
        assert load_overlays(store, "M") == []

    def test_load_overlays_integrates_sibling_when_config_passed(self, tmp_path):
        from dstf.reporting.overlay_loader import load_overlays
        _write_ref(tmp_path / "Dymola" / "windows", "ref_0001", "M")
        store = _FakeStore(tmp_path / "OpenModelica" / "linux")
        cfg = _FakeConfigForSibling(tmp_path, "OpenModelica", "linux")
        overlays = load_overlays(store, "M", config=cfg)
        assert any(o.kind == "sibling-backend" for o in overlays)

    def test_attach_threads_kind_through(self):
        """attach_overlays_to_trajectories must propagate `kind` so the JS
        can style sibling-backend overlays distinctly."""
        trajectories = [{"name": "h"}]
        overlays = [
            Overlay(
                name="Dymola/windows",
                role="companion",
                kind="sibling-backend",
                status="loaded",
                variables={"h": OverlayVariable(time=[0, 1], values=[1, 0])},
            ),
        ]
        attach_overlays_to_trajectories(trajectories, overlays)
        attached = trajectories[0]["overlays"]
        assert len(attached) == 1
        assert attached[0]["kind"] == "sibling-backend"
        assert attached[0]["role"] == "companion"
