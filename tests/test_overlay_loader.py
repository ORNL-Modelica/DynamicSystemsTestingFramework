"""Tests for reporting/overlay_loader.py — A2 / idea #50.

Soft_checks come from the reference store; companions come from external
(or frozen) files. Loader tolerates every failure mode gracefully — a
companion file that has moved or is unparseable must not fail the
report render.
"""

from __future__ import annotations

import functools
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
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
    path: str | None = None
    data_file: str | None = None
    provenance: dict = field(default_factory=dict)
    variables: dict = field(default_factory=dict)
    mapping_warnings: tuple = ()


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

    def __init__(
        self, ref_dir: Path, soft_checks=None, companions=None, companion_dir=None
    ):
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
        data_file.write_text(
            json.dumps(
                {
                    "time": [0.0, 1.0, 2.0],
                    "variables": [
                        {"name": "h", "values": [1.0, 2.0, 3.0]},
                    ],
                }
            )
        )
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
        data_file.write_text(
            json.dumps(
                {
                    "time": [0.0, 1.0],
                    "variables": [{"name": "h", "values": [0.1, 0.9]}],
                }
            )
        )
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
        data_file.write_text(
            json.dumps(
                {
                    "time": [0.0, 1.0],
                    "variables": [{"name": "h", "values": [0.0, 1.0]}],
                }
            )
        )
        companion = _FakeCompanion(
            name="rig",
            kind="frozen",
            format="json",
            data_file="rig.data.json",
        )
        store = _FakeStore(
            tmp_path,
            companions={"rig": companion},
            companion_dir=co_dir,
        )
        overlays = load_overlays(store, "M")
        assert overlays[0].status == "loaded"
        assert overlays[0].variables["h"].values == [0.0, 1.0]


class TestTheCompanionSOwnExpressions:
    """A companion may itself be a DSTF baseline of another library, and
    then its variable entries carry an ``expression`` of their own. The
    loader keeps it beside the values — it is the last thing the read rule
    tries for a companion nobody mapped."""

    def test_a_json_companion_keeps_each_variable_s_expression(self, tmp_path):
        data_file = tmp_path / "ext.json"
        data_file.write_text(
            json.dumps(
                {
                    "time": [0.0, 1.0],
                    "variables": [
                        {
                            "name": "unitTests.x[1]",
                            "expression": "a.Q",
                            "values": [1, 2],
                        },
                        {"name": "plain", "values": [3, 4]},
                    ],
                }
            )
        )
        companion = _FakeCompanion(
            name="other-lib", kind="external", format="json", path=str(data_file)
        )
        store = _FakeStore(tmp_path, companions={"other-lib": companion})
        overlays = load_overlays(store, "M")
        assert overlays[0].variables["unitTests.x[1]"].expression == "a.Q"
        assert overlays[0].variables["plain"].expression == ""

    def test_a_csv_companion_has_no_expressions(self, tmp_path):
        csv_file = tmp_path / "rig.csv"
        csv_file.write_text("time,h\n0.0,1.0\n1.0,2.0\n")
        companion = _FakeCompanion(
            name="rig", kind="external", format="csv", path=str(csv_file)
        )
        store = _FakeStore(tmp_path, companions={"rig": companion})
        overlays = load_overlays(store, "M")
        assert overlays[0].variables["h"].expression == ""

    def test_a_soft_check_keeps_its_entries_expressions(self, tmp_path):
        baseline = _FakeBaseline(
            name="experiment",
            time=[0.0, 1.0],
            variables=[
                {"name": "unitTests.x[1]", "expression": "a.Q", "values": [1.0, 2.0]}
            ],
        )
        store = _FakeStore(tmp_path, soft_checks={"experiment": baseline})
        overlays = load_overlays(store, "M")
        assert overlays[0].variables["unitTests.x[1]"].expression == "a.Q"


class TestTheCompanionSMappingReachesTheOverlay:
    def test_the_metadata_mapping_is_carried_onto_the_overlay(self, tmp_path):
        data_file = tmp_path / "ext.json"
        data_file.write_text(
            json.dumps(
                {"time": [0.0, 1.0], "variables": [{"name": "h", "values": [1, 2]}]}
            )
        )
        companion = _FakeCompanion(
            name="rig",
            kind="external",
            format="json",
            path=str(data_file),
            variables={"unitTests.x[1]": "h"},
        )
        store = _FakeStore(tmp_path, companions={"rig": companion})
        overlays = load_overlays(store, "M")
        assert overlays[0].mapping == {"unitTests.x[1]": "h"}

    def test_dstf_s_own_overlays_carry_no_mapping(self, tmp_path):
        """A soft_check, and an auto-discovered sibling-backend reference,
        are DSTF's own data keyed by the same names — there is nothing to
        map, and the read rule's fallbacks are what they use."""
        baseline = _FakeBaseline(
            name="experiment", time=[0.0], variables=[{"name": "h", "values": [1.0]}]
        )
        store = _FakeStore(tmp_path, soft_checks={"experiment": baseline})
        assert load_overlays(store, "M")[0].mapping == {}


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
            name="rig",
            kind="external",
            format="csv",
            path=str(csv_file),
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
            name="empty",
            kind="external",
            format="csv",
            path=str(csv_file),
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
                name="experiment",
                role="soft_check",
                status="loaded",
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

    def test_an_overlay_is_matched_by_name_first_and_by_expression_after(self):
        """An overlay is somebody else's data — a companion names the
        quantity the way ITS library does, and the expression a UnitTests
        variable was written with is the only thing the two spellings share.
        So: the name if the overlay has it, the expression only when it does
        not. A name match wins, or a companion carrying both names would
        decide which trajectory a trace belongs to."""
        trajectories = [
            {"name": "unitTests.x[1]", "expression": "pipe.T[1]"},
            {"name": "unitTests.x[2]", "expression": "pipe.T[2]"},
        ]
        overlays = [
            Overlay(
                name="julia",
                role="companion",
                status="loaded",
                variables={
                    "unitTests.x[1]": OverlayVariable(time=[0, 1], values=[1, 2]),
                    "pipe.T[1]": OverlayVariable(time=[0, 1], values=[3, 4]),
                    "pipe.T[2]": OverlayVariable(time=[0, 1], values=[5, 6]),
                },
            ),
        ]
        attach_overlays_to_trajectories(trajectories, overlays)
        # the name is there, so the expression's series is NOT the one taken
        assert trajectories[0]["overlays"][0]["values"] == [1, 2]
        # no variable under the name: the expression finds the companion's
        assert trajectories[1]["overlays"][0]["values"] == [5, 6]

    def test_a_trajectory_without_an_expression_matches_by_name_alone(self):
        trajectories = [{"name": "h"}]
        overlays = [
            Overlay(
                name="julia",
                role="companion",
                status="loaded",
                variables={"": OverlayVariable(time=[0, 1], values=[1, 2])},
            ),
        ]
        attach_overlays_to_trajectories(trajectories, overlays)
        assert trajectories[0]["overlays"] == []

    def test_skips_non_loaded_overlays(self):
        trajectories = [{"name": "h"}]
        overlays = [
            Overlay(
                name="missing_one",
                role="companion",
                status="missing",
                variables={"h": OverlayVariable(time=[], values=[])},
            ),
        ]
        attach_overlays_to_trajectories(trajectories, overlays)
        assert trajectories[0]["overlays"] == []

    def test_works_on_nobaseline_trajectory_shape(self):
        """NO_REF plots use ``{index, name, time, values}`` instead of the
        compare-path's ``{name, act_time, act_values, ref_time, ref_values}``.
        The attach pass matches on the entry's ``name``, so the same helper
        threads sibling-backend overlays onto no-baseline plots too — this
        is the pre-accept cross-check path for a brand-new backend / OS.
        """
        nobaseline = [
            {"index": 1, "name": "h", "time": [0, 1, 2], "values": [1, 0.5, 0]},
            {"index": 2, "name": "v", "time": [0, 1, 2], "values": [0, -1, -2]},
        ]
        overlays = [
            Overlay(
                name="OpenModelica/linux",
                role="companion",
                kind="sibling-backend",
                status="loaded",
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


def _ov(values, expression=""):
    return OverlayVariable(time=[0, 1], values=values, expression=expression)


class TestTheMappingIsTheRecord:
    """The companion's own metadata says which of ITS variables draw on
    which of the test's. Where there is one, nothing else is tried."""

    def test_a_mapped_trajectory_takes_the_variable_the_mapping_names(self):
        trajectories = [{"name": "unitTests.x[1]", "expression": "a.Q"}]
        overlays = [
            Overlay(
                name="rig",
                role="companion",
                status="loaded",
                mapping={"unitTests.x[1]": "sensor_a"},
                variables={
                    "sensor_a": _ov([1, 2]),
                    "unitTests.x[1]": _ov([3, 4]),
                    "a.Q": _ov([5, 6]),
                },
            )
        ]
        attach_overlays_to_trajectories(trajectories, overlays)
        assert trajectories[0]["overlays"][0]["values"] == [1, 2]

    def test_an_unmapped_trajectory_gets_nothing_even_when_the_names_coincide(self):
        """The mapping is the whole record for a companion that has one: a
        trajectory it does not name draws no trace, though the companion
        happens to hold a variable spelled exactly like it."""
        trajectories = [
            {"name": "unitTests.x[1]", "expression": "a.Q"},
            {"name": "unitTests.x[2]", "expression": "b.Q"},
        ]
        overlays = [
            Overlay(
                name="rig",
                role="companion",
                status="loaded",
                mapping={"unitTests.x[1]": "sensor_a"},
                variables={
                    "sensor_a": _ov([1, 2]),
                    "unitTests.x[2]": _ov([3, 4]),
                    "b.Q": _ov([5, 6]),
                },
            )
        ]
        attach_overlays_to_trajectories(trajectories, overlays)
        assert trajectories[0]["overlays"][0]["values"] == [1, 2]
        assert trajectories[1]["overlays"] == []

    def test_a_mapping_pointing_at_a_variable_the_file_lacks_draws_nothing(self):
        trajectories = [{"name": "unitTests.x[1]", "expression": "a.Q"}]
        overlays = [
            Overlay(
                name="rig",
                role="companion",
                status="loaded",
                mapping={"unitTests.x[1]": "gone"},
                variables={"a.Q": _ov([5, 6])},
            )
        ]
        attach_overlays_to_trajectories(trajectories, overlays)
        assert trajectories[0]["overlays"] == []

    def test_one_companion_variable_may_draw_on_two_trajectories(self):
        trajectories = [{"name": "a"}, {"name": "b"}]
        overlays = [
            Overlay(
                name="rig",
                role="companion",
                status="loaded",
                mapping={"a": "h", "b": "h"},
                variables={"h": _ov([1, 2])},
            )
        ]
        attach_overlays_to_trajectories(trajectories, overlays)
        assert trajectories[0]["overlays"][0]["values"] == [1, 2]
        assert trajectories[1]["overlays"][0]["values"] == [1, 2]


class TestTheFallbacksForACompanionNobodyMapped:
    def test_the_companion_s_own_expression_finds_a_positional_name(self):
        """The repo's example companions are DSTF baselines of another
        library: the Julia test's variable is named ``x`` and the Modelica
        companion names the same quantity ``unitTests.x[1]``, carrying
        ``x`` as its expression. Nothing but the companion's expression
        links the two."""
        trajectories = [{"name": "x", "expression": ""}]
        overlays = [
            Overlay(
                name="modelica-om",
                role="companion",
                status="loaded",
                variables={"unitTests.x[1]": _ov([1, 2], expression="x")},
            )
        ]
        attach_overlays_to_trajectories(trajectories, overlays)
        assert trajectories[0]["overlays"][0]["values"] == [1, 2]

    def test_the_companion_s_expression_also_meets_the_trajectory_s(self):
        trajectories = [{"name": "unitTests.x[1]", "expression": "a.Q"}]
        overlays = [
            Overlay(
                name="other",
                role="companion",
                status="loaded",
                variables={"ut.x[1]": _ov([7, 8], expression="a.Q")},
            )
        ]
        attach_overlays_to_trajectories(trajectories, overlays)
        assert trajectories[0]["overlays"][0]["values"] == [7, 8]

    def test_two_candidates_draw_nothing_and_warn(self):
        """``x={a.Q, a.Q}`` is legal, so a companion of that library has two
        variables carrying one expression. Guessing between them would put
        somebody else's curve under the wrong trajectory."""
        trajectories = [{"name": "a.Q", "expression": ""}]
        overlays = [
            Overlay(
                name="modelica-om",
                role="companion",
                status="loaded",
                variables={
                    "unitTests.x[1]": _ov([1, 2], expression="a.Q"),
                    "unitTests.x[2]": _ov([3, 4], expression="a.Q"),
                },
            )
        ]
        attach_overlays_to_trajectories(trajectories, overlays)
        assert trajectories[0]["overlays"] == []
        assert len(overlays[0].warnings) == 1
        text = overlays[0].warnings[0]
        assert "a.Q" in text
        assert "unitTests.x[1]" in text and "unitTests.x[2]" in text
        assert "add a mapping" in text
        assert "--map" in text

    def test_a_candidate_on_the_name_and_one_on_the_expression_is_ambiguous(self):
        trajectories = [{"name": "a.Q", "expression": "b.Q"}]
        overlays = [
            Overlay(
                name="modelica-om",
                role="companion",
                status="loaded",
                variables={
                    "unitTests.x[1]": _ov([1, 2], expression="a.Q"),
                    "unitTests.x[2]": _ov([3, 4], expression="b.Q"),
                },
            )
        ]
        attach_overlays_to_trajectories(trajectories, overlays)
        assert trajectories[0]["overlays"] == []
        assert len(overlays[0].warnings) == 1

    def test_the_same_ambiguity_is_recorded_once_over_two_attach_passes(self):
        """The reporter attaches over the scored trajectories and then over
        the no-baseline ones, with the same overlay objects."""
        overlays = [
            Overlay(
                name="modelica-om",
                role="companion",
                status="loaded",
                variables={
                    "unitTests.x[1]": _ov([1, 2], expression="a.Q"),
                    "unitTests.x[2]": _ov([3, 4], expression="a.Q"),
                },
            )
        ]
        attach_overlays_to_trajectories([{"name": "a.Q"}], overlays)
        attach_overlays_to_trajectories([{"name": "a.Q"}], overlays)
        assert len(overlays[0].warnings) == 1

    def test_a_mapped_companion_never_warns_about_an_ambiguity(self):
        """The fallbacks are not tried under a mapping, so the two
        same-expression variables are not candidates for anything."""
        overlays = [
            Overlay(
                name="modelica-om",
                role="companion",
                status="loaded",
                mapping={"a.Q": "unitTests.x[2]"},
                variables={
                    "unitTests.x[1]": _ov([1, 2], expression="a.Q"),
                    "unitTests.x[2]": _ov([3, 4], expression="a.Q"),
                },
            )
        ]
        trajectories = [{"name": "a.Q"}]
        attach_overlays_to_trajectories(trajectories, overlays)
        assert trajectories[0]["overlays"][0]["values"] == [3, 4]
        assert overlays[0].warnings == []


class TestOnlyACompanionFallsBackOnSpellings:
    """The fallbacks are for a COMPANION nobody mapped — somebody else's
    file, naming the quantity its own way. Everything else on the picker
    is DSTF's own data under the test's own names: a soft_check (imported
    whole, and scored by name through ``against:``), a cross-backend
    import stored as one, and the auto-discovered primary of a sibling
    partition. Those match by name and nothing else."""

    SOFT_CHECK = {"other.name": _ov([1, 2], expression="a.Q")}

    def test_a_soft_check_does_not_draw_where_it_only_shares_an_expression(self):
        """The scenario this rule is about: a soft_check variable called
        something else entirely, carrying the trajectory's expression. It
        would have been drawn under `unitTests.x[1]` while `against:`
        scored nothing of the sort."""
        trajectories = [{"name": "unitTests.x[1]", "expression": "a.Q"}]
        overlays = [
            Overlay(
                name="dymola-via-fmpy",
                role="soft_check",
                status="loaded",
                variables=dict(self.SOFT_CHECK),
            )
        ]
        attach_overlays_to_trajectories(trajectories, overlays)
        assert trajectories[0]["overlays"] == []
        assert overlays[0].warnings == []

    def test_a_soft_check_variable_named_like_the_expression_is_not_taken_either(self):
        trajectories = [{"name": "unitTests.x[1]", "expression": "a.Q"}]
        overlays = [
            Overlay(
                name="experiment",
                role="soft_check",
                status="loaded",
                variables={"a.Q": _ov([1, 2])},
            )
        ]
        attach_overlays_to_trajectories(trajectories, overlays)
        assert trajectories[0]["overlays"] == []

    def test_a_soft_check_draws_on_the_name_the_two_share(self):
        """The name is the whole rule for it — not no rule at all."""
        trajectories = [{"name": "unitTests.x[1]", "expression": "a.Q"}]
        overlays = [
            Overlay(
                name="experiment",
                role="soft_check",
                status="loaded",
                variables={"unitTests.x[1]": _ov([1, 2], expression="b.Q")},
            )
        ]
        attach_overlays_to_trajectories(trajectories, overlays)
        assert trajectories[0]["overlays"][0]["values"] == [1, 2]

    def test_a_sibling_backend_reference_matches_by_name_only(self):
        """It is the same library's primary under another backend — the
        names are ours on both sides."""
        trajectories = [{"name": "unitTests.x[1]", "expression": "a.Q"}]
        overlays = [
            Overlay(
                name="OpenModelica/linux",
                role="companion",
                kind="sibling-backend",
                status="loaded",
                variables=dict(self.SOFT_CHECK),
            )
        ]
        attach_overlays_to_trajectories(trajectories, overlays)
        assert trajectories[0]["overlays"] == []
        assert overlays[0].warnings == []

    def test_a_registered_companion_in_the_same_shape_still_falls_back(self):
        """The control: what changed is whose data it is, not the rule."""
        trajectories = [{"name": "unitTests.x[1]", "expression": "a.Q"}]
        overlays = [
            Overlay(
                name="rig",
                role="companion",
                kind="external",
                status="loaded",
                variables=dict(self.SOFT_CHECK),
            )
        ]
        attach_overlays_to_trajectories(trajectories, overlays)
        assert trajectories[0]["overlays"][0]["values"] == [1, 2]

    def test_nothing_but_a_companion_can_be_told_to_add_a_mapping(self):
        """``dstf companion add … --map`` is the only remedy there is, so
        an ambiguity — the only thing that words it — must be reachable
        only from a companion. Two variables carrying one expression are
        ambiguous for one; for DSTF's own data they are not candidates at
        all, and nothing is said."""
        ambiguous = {
            "unitTests.x[1]": _ov([1, 2], expression="a.Q"),
            "unitTests.x[2]": _ov([3, 4], expression="a.Q"),
        }
        overlays = [
            Overlay(
                name="experiment",
                role="soft_check",
                status="loaded",
                variables=dict(ambiguous),
            ),
            Overlay(
                name="OpenModelica/linux",
                role="companion",
                kind="sibling-backend",
                status="loaded",
                variables=dict(ambiguous),
            ),
        ]
        attach_overlays_to_trajectories([{"name": "a.Q"}], overlays)
        assert [ov.warnings for ov in overlays] == [[], []]
        rows = overlay_summary(overlays)
        assert [row["warnings"] for row in rows] == [[], []]
        assert "companion add" not in json.dumps(rows)


class TestAMalformedMappingIsSaidAndStillCounts:
    """A ``variables`` record that could not be read is a malformed
    record, not an absent one: the entry is dropped, the author is told
    on the overlay table, and the companion does not quietly go back to
    guessing from spellings."""

    def _store(self, tmp_path, **companion_kwargs):
        data_file = tmp_path / "ext.json"
        data_file.write_text(
            json.dumps(
                {
                    "time": [0.0, 1.0],
                    "variables": [
                        {"name": "h", "values": [1, 2]},
                        {"name": "other", "values": [3, 4]},
                    ],
                }
            )
        )
        companion = _FakeCompanion(
            name="rig",
            kind="external",
            format="json",
            path=str(data_file),
            **companion_kwargs,
        )
        return _FakeStore(tmp_path, companions={"rig": companion}), data_file

    def test_a_dropped_entry_is_reported_and_the_record_still_rules(self, tmp_path):
        store, _ = self._store(
            tmp_path,
            variables={"v": "other"},
            mapping_warnings=("mapping for h is not a variable name",),
        )
        overlay = load_overlays(store, "M")[0]
        assert overlay.warnings == ["mapping for h is not a variable name"]
        assert overlay.mapping_malformed is True
        # `h` is spelled the same on both sides and draws nothing anyway:
        # the author's record is what is read, broken entry and all.
        trajectories = [{"name": "h"}, {"name": "v"}]
        attach_overlays_to_trajectories(trajectories, [overlay])
        assert trajectories[0]["overlays"] == []
        assert trajectories[1]["overlays"][0]["values"] == [3, 4]

    def test_a_record_that_is_not_pairs_at_all_draws_nothing(self, tmp_path):
        """Present-and-empty: there is a record, and none of it survived."""
        store, _ = self._store(
            tmp_path,
            mapping_warnings=("the 'variables' record is not an object",),
        )
        overlay = load_overlays(store, "M")[0]
        assert overlay.mapping == {} and overlay.mapping_malformed is True
        trajectories = [{"name": "h"}]
        attach_overlays_to_trajectories(trajectories, [overlay])
        assert trajectories[0]["overlays"] == []
        assert overlay.warnings == ["the 'variables' record is not an object"]

    def test_no_record_at_all_still_falls_back(self, tmp_path):
        """The other side of the same rule — an unmapped companion is
        unchanged."""
        store, _ = self._store(tmp_path)
        overlay = load_overlays(store, "M")[0]
        assert overlay.mapping_malformed is False
        trajectories = [{"name": "h"}]
        attach_overlays_to_trajectories(trajectories, [overlay])
        assert trajectories[0]["overlays"][0]["values"] == [1, 2]

    def test_a_mapping_naming_a_variable_the_file_lacks_says_which(self, tmp_path):
        """It draws nothing either way — but silently, the author has no
        way to learn the file was replaced under the registration."""
        store, data_file = self._store(tmp_path, variables={"v": "gone"})
        overlay = load_overlays(store, "M")[0]
        assert overlay.warnings == [
            f"mapping for v names gone, which {data_file} does not hold"
        ]
        assert overlay.mapping_malformed is False
        trajectories = [{"name": "v"}]
        attach_overlays_to_trajectories(trajectories, [overlay])
        assert trajectories[0]["overlays"] == []

    def test_a_mapping_the_file_honours_says_nothing(self, tmp_path):
        store, _ = self._store(tmp_path, variables={"v": "other"})
        overlay = load_overlays(store, "M")[0]
        assert overlay.warnings == []


class TestANameStoredTwice:
    """Duplicate names in a stored file: the FIRST entry is the one drawn,
    as the comparator scores it and the context builder draws a primary's
    — the last one had overwritten it."""

    TWICE = [
        {"name": "h", "values": [1.0, 1.0]},
        {"name": "h", "values": [9.0, 9.0]},
    ]

    def test_a_soft_check_overlays_its_first_entry(self, tmp_path):
        baseline = _FakeBaseline(name="e", time=[0.0, 1.0], variables=self.TWICE)
        store = _FakeStore(tmp_path, soft_checks={"e": baseline})
        (ov,) = load_overlays(store, "M")
        assert ov.variables["h"].values == [1.0, 1.0]

    def test_a_json_companion_overlays_its_first_entry(self, tmp_path):
        data_file = tmp_path / "ext.json"
        data_file.write_text(
            json.dumps({"time": [0.0, 1.0], "variables": self.TWICE}),
            encoding="utf-8",
        )
        companion = _FakeCompanion(
            name="ext", kind="external", format="json", path=str(data_file)
        )
        store = _FakeStore(tmp_path, companions={"ext": companion})
        (ov,) = load_overlays(store, "M")
        assert ov.variables["h"].values == [1.0, 1.0]

    def test_a_sibling_backends_reference_overlays_its_first_entry(self, tmp_path):
        from dstf.reporting.overlay_loader import (
            _sibling_backend_index,
            load_sibling_backend_overlays,
        )

        _sibling_backend_index.cache_clear()
        peer = tmp_path / "Dymola" / "windows"
        peer.mkdir(parents=True)
        (peer / "ref_0001.json").write_text(
            json.dumps(
                {
                    "model_id": "M",
                    "status": "active",
                    "time": [0.0, 1.0],
                    "variables": self.TWICE,
                }
            ),
            encoding="utf-8",
        )
        cfg = _FakeConfigForSibling(tmp_path, "OpenModelica", "linux")
        (ov,) = load_sibling_backend_overlays(cfg, "M")
        assert ov.variables["h"].values == [1.0, 1.0]


class TestOverlaySummary:
    def test_an_ambiguity_reaches_the_summary(self):
        overlays = [
            Overlay(
                name="modelica-om",
                role="companion",
                status="loaded",
                variables={
                    "unitTests.x[1]": _ov([1, 2], expression="a.Q"),
                    "unitTests.x[2]": _ov([3, 4], expression="a.Q"),
                },
            )
        ]
        attach_overlays_to_trajectories([{"name": "a.Q"}], overlays)
        rows = overlay_summary(overlays)
        assert rows[0]["warnings"] == overlays[0].warnings
        assert "add a mapping" in rows[0]["warnings"][0]

    def test_an_overlay_with_nothing_to_say_has_no_warnings(self):
        overlays = [
            Overlay(
                name="rig",
                role="companion",
                status="loaded",
                variables={"h": _ov([1, 2])},
            )
        ]
        assert overlay_summary(overlays)[0]["warnings"] == []


class TestTheWarningOnTheReportPage:
    """The overlay table's warning cell, rendered through the production
    template. Its text quotes a companion's own variable names — somebody
    else's file — so it is escaped, and it is the page that says a row
    has something wrong with it."""

    def test_report_page_renders_the_warning_escaped(self, tmp_path):
        from report_fixtures import _fixture_context, _render_with_context

        ctx = _fixture_context()
        overlay = Overlay(
            name="rig",
            role="companion",
            kind="external",
            status="loaded",
            variables={
                "p1": _ov([0, 1], expression="x<b>"),
                "p2": _ov([0, 1], expression="x<b>"),
            },
        )
        attach_overlays_to_trajectories(
            [{"name": "h", "expression": "x<b>"}], [overlay]
        )
        assert overlay.warnings, "the fixture must produce a warning to render"
        overlay.warnings.append("raw <script>alert(1)</script> & stuff")
        ctx["overlay_rows"] = overlay_summary([overlay])

        html = _render_with_context(tmp_path, ctx).read_text(encoding="utf-8")
        assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; stuff" in html
        assert "<script>alert(1)</script>" not in html
        assert 'class="ov-warning"' in html
        assert "h: 2 of this overlay" in html
        assert "--map h=&lt;one of them&gt;" in html
        assert "background: var(--dstf-warn-tint)" in html

    def test_a_row_with_nothing_wrong_is_not_tinted(self, tmp_path):
        from report_fixtures import _fixture_context, _render_with_context

        ctx = _fixture_context()
        ctx["overlay_rows"] = overlay_summary(
            [
                Overlay(
                    name="rig",
                    role="companion",
                    kind="external",
                    status="loaded",
                    variables={"h": _ov([0, 1])},
                )
            ]
        )
        body = _render_with_context(tmp_path, ctx).read_text(encoding="utf-8")
        body = body.split("</style>")[1]
        assert 'class="ov-warning"' not in body
        assert "var(--dstf-warn-tint)" not in body

    def test_surfaces_missing_and_invalid(self):
        overlays = [
            Overlay(
                name="rig",
                role="companion",
                kind="external",
                status="missing",
                note="file not found",
            ),
            Overlay(
                name="experiment",
                role="soft_check",
                status="loaded",
                variables={"h": OverlayVariable(time=[0], values=[1])},
            ),
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

    def __init__(self, reference_root, backend, os_name):
        self.reference_root = reference_root
        self.backend = backend
        self.os_name = os_name


def _write_ref(
    dir_path: Path, stem: str, model_id: str, varname: str = "h", status: str = "active"
):
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / f"{stem}.json").write_text(
        json.dumps(
            {
                "model_id": model_id,
                "status": status,
                "time": [0.0, 0.5, 1.0],
                "variables": [{"name": varname, "values": [1.0, 0.5, 0.0]}],
            }
        )
    )


class TestSiblingBackendOverlays:
    def setup_method(self):
        # Clear the lru_cache between tests — it keys on the arg tuple so
        # a fresh tmp_path already dodges collisions, but being explicit
        # prevents surprises if a test reuses a path.
        from dstf.reporting.overlay_loader import (
            _sibling_backend_index,
        )

        _sibling_backend_index.cache_clear()

    def test_warm_builds_the_index_once_and_a_later_load_hits_the_cache(self, tmp_path):
        from dstf.reporting.overlay_loader import (
            _sibling_backend_index,
            load_sibling_backend_overlays,
            warm_sibling_backend_index,
        )

        _write_ref(tmp_path / "Dymola" / "windows", "ref_0001", "M")
        config = _FakeConfigForSibling(tmp_path, "OpenModelica", "linux")
        _sibling_backend_index.cache_clear()
        warm_sibling_backend_index(config)
        after_warm = _sibling_backend_index.cache_info()
        load_sibling_backend_overlays(config, "M")
        after_load = _sibling_backend_index.cache_info()
        assert after_warm.misses == 1 and after_warm.hits == 0
        assert after_load.misses == 1 and after_load.hits == 1

    def test_two_threads_warming_at_once_build_the_index_once(
        self, tmp_path, monkeypatch
    ):
        import threading

        from dstf.reporting import overlay_loader as ol

        _write_ref(tmp_path / "Dymola" / "windows", "ref_0001", "M")
        config = _FakeConfigForSibling(tmp_path, "OpenModelica", "linux")
        ol._sibling_backend_index.cache_clear()
        builds = []
        real = ol._sibling_backend_index.__wrapped__

        def slow(*args):
            builds.append(threading.current_thread().name)
            time.sleep(0.2)
            return real(*args)

        monkeypatch.setattr(
            ol, "_sibling_backend_index", functools.lru_cache(maxsize=8)(slow)
        )
        threads = [
            threading.Thread(target=ol.warm_sibling_backend_index, args=(config,))
            for _ in range(3)
        ]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        assert len(builds) == 1

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

        _write_ref(tmp_path / "Dymola" / "windows", "ref_0001", "M", status="obsolete")
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


class TestTheRepoSExampleCompanions:
    """The bundled demo libraries cross-overlay each other where the two
    hold ONE model: `Frequency`, `MultiFrequency` and `RangeCheck` carry
    the other library's baseline of it as a companion, in both directions.
    They are the worked example of a companion that names the quantity its
    own way, so each one carries a mapping — and the real loader must draw
    on every trajectory it names, on the curve it is drawn over.

    The like-named tests that are NOT one model carry none: `Constant`,
    `SimpleRamp` / `SimpleTest`, `TubeTolerance` and `MetricTree` are
    different equations in the two libraries, and a companion there drew
    an unrelated curve. The Modelica `MetricTreeTest` carries its model's
    ANALYTIC solution instead, frozen beside its baseline in both Linux
    partitions.
    """

    EXAMPLES = Path(__file__).parent.parent / "examples"
    ROOTS = [
        (
            EXAMPLES / "julia" / "JuliaMtkTestingLib",
            "Julia",
        ),
        (
            EXAMPLES / "modelica" / "ModelicaTestingLib",
            "OpenModelica",
        ),
        (
            EXAMPLES / "modelica" / "ModelicaTestingLib",
            "Dymola",
        ),
    ]

    #: Every example companion, and what it declares — the set this test
    #: pins, so dropping a mapping from the data is a failure and not
    #: merely one fewer thing checked.
    EXPECTED = {
        "modelica-om-frequency": {"x": "unitTests.x[1]"},
        "modelica-om-multifrequency": {"y": "unitTests.x[1]"},
        "modelica-om-rangecheck": {"x": "unitTests.x[1]"},
        "julia-mtk-frequency": {"unitTests.x[1]": "x"},
        "analytic": {"unitTests.x[1]": "x", "unitTests.x[2]": "y"},
        "julia-mtk-multifrequency": {"unitTests.x[1]": "y"},
        "julia-mtk-rangecheck": {"unitTests.x[1]": "x"},
    }

    def _load(self, source, backend):
        """``(companion, test variable) -> the overlays drawn on it``, plus
        ``companion -> its mapping``, over the real store."""
        from dstf.config import Config
        from dstf.storage.reference_store import ReferenceStore

        root = source / "Resources" / "ReferenceResults"
        config = Config(
            source_path=source,
            reference_root=root,
            backend_name=backend,
            os_name="linux",
        )
        store = ReferenceStore(config)
        drawn: dict = {}
        mappings: dict = {}
        for ref_file in sorted((root / backend / "linux").glob("ref_*.json")):
            data = json.loads(ref_file.read_text(encoding="utf-8"))
            companions = [
                o
                for o in load_overlays(store, data["model_id"])
                if o.role == "companion"
            ]
            if not companions:
                continue
            trajectories = [
                {"name": v.get("name", ""), "expression": v.get("expression", "")}
                for v in data.get("variables", [])
            ]
            attach_overlays_to_trajectories(trajectories, companions)
            for ov in companions:
                assert ov.status == "loaded", f"{ov.name}: {ov.note}"
                mappings[ov.name] = dict(ov.mapping)
                for traj in trajectories:
                    if traj["name"] in ov.mapping:
                        drawn[(ov.name, traj["name"])] = [
                            a["name"] for a in traj["overlays"]
                        ]
        return drawn, mappings

    def test_every_mapped_trajectory_of_every_example_companion_draws(self):
        drawn: dict = {}
        mappings: dict = {}
        for source, backend in self.ROOTS:
            one_drawn, one_mappings = self._load(source, backend)
            drawn.update(one_drawn)
            mappings.update(one_mappings)
        assert mappings == self.EXPECTED
        missed = {k: v for k, v in drawn.items() if not v}
        assert missed == {}, f"mapped but drew nothing: {sorted(missed)}"
        for (companion, _var), names in drawn.items():
            assert names == [companion]

    def _off(self, source, backend) -> dict[tuple[str, str], float]:
        """``(companion, test variable) -> how far the companion's curve is
        from the baseline it is drawn on``: the largest difference over the
        time both cover, as a fraction of the baseline's range (of its
        value, for a constant). Read through the real loader."""
        from dstf.config import Config
        from dstf.reporting.overlay_loader import _overlay_variable_for
        from dstf.storage.reference_store import ReferenceStore

        root = source / "Resources" / "ReferenceResults"
        store = ReferenceStore(
            Config(
                source_path=source,
                reference_root=root,
                backend_name=backend,
                os_name="linux",
            )
        )
        off: dict[tuple[str, str], float] = {}
        for model_id in store.index.active_tests().values():
            baseline = store.get_baseline(model_id)
            for ov in load_overlays(store, model_id):
                if ov.role != "companion":
                    continue
                for entry in baseline.variables:
                    curve, _ = _overlay_variable_for(
                        ov, entry["name"], entry.get("expression", "")
                    )
                    if curve is None:
                        continue
                    t = np.asarray(baseline.time, dtype=float)
                    values = np.asarray(entry["values"], dtype=float)
                    ct = np.asarray(curve.time, dtype=float)
                    shared = (t >= ct[0]) & (t <= ct[-1])
                    assert shared.any()
                    apart = np.abs(
                        np.interp(t[shared], ct, curve.values) - values[shared]
                    )
                    scale = float(np.ptp(values)) or float(np.max(np.abs(values)))
                    off[(ov.name, entry["name"])] = float(apart.max()) / scale
        return off

    def test_every_example_companion_lies_on_the_baseline_it_overlays(self):
        """A companion that draws another model's curve is a wrong example:
        `julia-mtk-constant` sat 2.5 from a constant, `julia-mtk-simpleramp`
        half the ramp's range from it, `julia-mtk-tubetolerance` more than
        the signal's whole range — like-named tests of the two libraries
        that are not one model — and so did their mirrors under the Julia
        example, `modelica-om-metrictree` with them. What ships agrees with
        its baseline to 1 % of its range (the worst, a sine against another
        solver's: 3e-3)."""
        off: dict[tuple[str, str], float] = {}
        for source, backend in self.ROOTS:
            off.update(self._off(source, backend))
        assert {name for name, _var in off} == set(self.EXPECTED)
        apart = {k: round(v, 4) for k, v in off.items() if v > 1e-2}
        assert apart == {}, f"companions that are not their baseline's model: {apart}"

    @pytest.mark.parametrize("backend", ["OpenModelica", "Dymola"])
    def test_the_analytic_companion_is_the_models_own_solution(self, backend):
        """`MetricTreeTest`: x' = (1 - x)/0.5 and y' = x - y from zero, so
        x = 1 - e^(-2t) and y = 1 - 2e^(-t) + e^(-2t). The companion's
        curves are those — written out, not a simulator's — and the stored
        baseline of each partition lies on them."""
        from dstf.config import Config
        from dstf.storage.reference_store import ReferenceStore

        source = self.EXAMPLES / "modelica" / "ModelicaTestingLib"
        root = source / "Resources" / "ReferenceResults"
        model = "ModelicaTestingLib.Examples.MetricTreeTest"
        store = ReferenceStore(
            Config(
                source_path=source,
                reference_root=root,
                backend_name=backend,
                os_name="linux",
            )
        )
        (analytic,) = [o for o in load_overlays(store, model) if o.role == "companion"]
        assert (analytic.name, analytic.kind, analytic.status) == (
            "analytic",
            "frozen",
            "loaded",
        )
        exact = {
            "x": lambda t: 1.0 - np.exp(-2.0 * t),
            "y": lambda t: 1.0 - 2.0 * np.exp(-t) + np.exp(-2.0 * t),
        }
        assert set(analytic.variables) == {"x", "y"}
        for name, curve in analytic.variables.items():
            t = np.asarray(curve.time, dtype=float)
            assert (t[0], t[-1], len(t)) == (0.0, 10.0, 501)
            assert np.max(np.abs(np.asarray(curve.values) - exact[name](t))) < 1e-11
        reference = store.get_reference(model)
        t = np.asarray(reference["time"], dtype=float)
        worst = {
            v["name"]: float(
                np.max(
                    np.abs(
                        np.asarray(v["values"]) - exact[analytic.mapping[v["name"]]](t)
                    )
                )
            )
            for v in reference["variables"]
        }
        assert set(worst) == {"unitTests.x[1]", "unitTests.x[2]"}
        assert max(worst.values()) < 1e-5, worst
