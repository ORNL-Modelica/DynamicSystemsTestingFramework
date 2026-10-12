"""The spec-update read-modify-write as a library: materialization, validation,
one read and one write for every model in a patch set."""

import copy
import json
from pathlib import Path

import pytest

from dstf.comparison.tree_spec import parse_metric_tree
from dstf.comparison.validator import role_lookup_from_names, validate_tree
from dstf.config import Config
from dstf.discovery.patch_apply import PatchError, apply_patch_to_document
from dstf.discovery.spec_update import (
    apply_validated_patches,
    implicit_tree_op,
    materialize_implicit_metrics,
    spec_entry,
)

FLAT = {
    "model": "M",
    "variables": ["x", "y"],
    "comparison": {"variable_overrides": {"x": {"tolerance": 0.1}}},
}
# The raw tree is flat: a leaf is {"metric": ..., "variable": ..., <param>: ...}, no "params" key.
LEAF_OP = {"op": "replace", "path": "/metrics/children/0/tolerance", "value": 0.5}


def _config(tmp_path) -> Config:
    (tmp_path / "testing.json").write_text(
        json.dumps({"source_type": "fmu", "backend": "FMPy", "reference_root": "."}),
        encoding="utf-8",
    )
    return Config(config_file=tmp_path / "testing.json")


def _spec(tmp_path, *entries) -> Path:
    p = tmp_path / "test_spec.json"
    p.write_text(
        json.dumps({"tests": list(entries)}, indent=2) + "\n", encoding="utf-8"
    )
    return p


class TestMaterialization:
    def test_flat_entry_gets_the_implicit_tree_prepended(self):
        ops = materialize_implicit_metrics(dict(FLAT), [LEAF_OP])
        assert ops[0]["op"] == "add" and ops[0]["path"] == "/metrics"
        assert ops[0] == implicit_tree_op(dict(FLAT))
        assert ops[1:] == [LEAF_OP]
        leaves = ops[0]["value"]["children"]
        assert [leaf["variable"] for leaf in leaves] == ["x", "y"]
        assert leaves[0]["tolerance"] == 0.1 and "tolerance" not in leaves[1]

    def test_a_glob_pattern_is_refused(self):
        """A `*` leaf is a sentinel that fails; the flat path scored the
        variables the result resolved. Never write that tree (Conventions #7)."""
        for patterns in (["*"], ["pipe.T*"], ["x", "tank.?"]):
            with pytest.raises(PatchError, match="pattern"):
                implicit_tree_op({"model": "M", "variables": patterns})

    def test_an_array_element_is_a_literal_name(self):
        """`[]` is a Modelica array index, not a glob (the resolver's rule);
        the documented `pipe.T[1]` must materialize to a leaf of that name."""
        op = implicit_tree_op({"model": "M", "variables": ["pipe.T[1]", "pipe.m_flow"]})
        assert [leaf["variable"] for leaf in op["value"]["children"]] == [
            "pipe.T[1]",
            "pipe.m_flow",
        ]

    def test_a_patch_that_supplies_the_tree_is_not_materialized_again(self):
        tree_op = implicit_tree_op(dict(FLAT))
        assert materialize_implicit_metrics(dict(FLAT), [tree_op, LEAF_OP]) == [
            tree_op,
            LEAF_OP,
        ]

    def test_an_entry_with_a_tree_is_left_alone(self):
        entry = {**FLAT, "metrics": {"metric": "nrmse", "variable": "x"}}
        assert materialize_implicit_metrics(entry, [LEAF_OP]) == [LEAF_OP]

    def test_only_a_null_metrics_is_no_tree(self):
        """`has_metrics` asks for a value, not a truthy one: an EMPTY tree is
        a tree (the validator refuses it by name), a null is none."""
        from dstf.discovery.spec_update import has_metrics

        assert has_metrics({**FLAT, "metrics": {}})
        assert has_metrics({**FLAT, "metrics": []})
        assert not has_metrics({**FLAT, "metrics": None})
        assert not has_metrics(dict(FLAT)) and not has_metrics(None)
        assert materialize_implicit_metrics({**FLAT, "metrics": {}}, [LEAF_OP]) == [
            LEAF_OP
        ]

    def test_ops_outside_metrics_need_nothing(self):
        op = {"op": "replace", "path": "/comparison/tolerance", "value": 1.0}
        assert materialize_implicit_metrics(dict(FLAT), [op]) == [op]

    def test_an_unknown_mode_is_refused(self):
        """The flat path fails a typo'd mode loudly; synthesize_implicit_tree
        would quietly score nrmse instead. Never write that tree."""
        entry = {
            "model": "M",
            "variables": ["x"],
            "comparison": {"variable_overrides": {"x": {"mode": "nrmsee"}}},
        }
        with pytest.raises(PatchError, match="nrmsee"):
            implicit_tree_op(entry)

    def test_no_variables_is_a_patch_error(self):
        with pytest.raises(PatchError, match="no tracked variables"):
            implicit_tree_op({"model": "M", "variables": []})

    def test_missing_entry_is_left_to_apply_patch(self):
        assert materialize_implicit_metrics(None, [LEAF_OP]) == [LEAF_OP]


class TestApplyValidatedPatches:
    def test_two_models_one_write(self, tmp_path):
        spec = _spec(tmp_path, dict(FLAT), {"model": "N", "variables": ["z"]})
        applied = apply_validated_patches(
            spec,
            [
                ("M", [LEAF_OP]),
                (
                    "N",
                    [{"op": "add", "path": "/comparison", "value": {"tolerance": 0.2}}],
                ),
            ],
            _config(tmp_path),
        )
        data = json.loads(spec.read_text(encoding="utf-8"))
        m, n = spec_entry(data, "M"), spec_entry(data, "N")
        assert m["metrics"]["children"][0]["tolerance"] == 0.5
        assert n["comparison"] == {"tolerance": 0.2}
        assert [model for model, _ in applied] == ["M", "N"]
        assert applied[0][1][0]["path"] == "/metrics"  # materialization is reported
        assert spec.read_bytes().endswith(b"}\n") and b"\r\n" not in spec.read_bytes()

    def test_no_groups_writes_nothing(self, tmp_path):
        """An empty envelope (the tool's export with nothing pending) must
        not rewrite — and reformat — the user's file."""
        spec = tmp_path / "test_spec.json"
        spec.write_text('{"tests":[{"model":"M","variables":["x"]}]}', encoding="utf-8")
        before = spec.read_bytes()
        assert apply_validated_patches(spec, [], _config(tmp_path)) == []
        assert spec.read_bytes() == before

    def test_a_bad_group_writes_nothing(self, tmp_path):
        spec = _spec(tmp_path, dict(FLAT), {"model": "N", "variables": ["z"]})
        before = spec.read_bytes()
        with pytest.raises(PatchError):
            apply_validated_patches(
                spec,
                [
                    ("M", [LEAF_OP]),
                    (
                        "N",
                        [
                            {
                                "op": "replace",
                                "path": "/simulation/stop_time",
                                "value": 1,
                            }
                        ],
                    ),
                ],
                _config(tmp_path),
            )
        assert spec.read_bytes() == before

    def test_the_validator_rejects_a_tree_with_no_hard_leaf_and_writes_nothing(
        self, tmp_path
    ):
        """D66: every tree needs one primary leaf outside `warn`; the file
        is untouched when the validator says no."""
        spec = _spec(tmp_path, dict(FLAT))
        before = spec.read_bytes()
        all_warn = {
            "combinator": "warn",
            "children": [{"metric": "nrmse", "variable": "x"}],
        }
        with pytest.raises(PatchError, match="validator rejected") as box:
            apply_validated_patches(
                spec,
                [("M", [{"op": "add", "path": "/metrics", "value": all_warn}])],
                _config(tmp_path),
            )
        assert spec.read_bytes() == before
        # The CLI prints the location; the tool shows the validator's own
        # sentence, which is the whole reason.
        (err,) = validate_tree(parse_metric_tree(all_warn), role_lookup_from_names())
        assert str(box.value) == f"validator rejected the patched tree: {err}"
        assert box.value.reason == err.message

    def test_the_result_equals_applying_to_the_document_in_memory(self, tmp_path):
        spec = _spec(tmp_path, dict(FLAT))
        expected = json.loads(spec.read_text(encoding="utf-8"))
        apply_patch_to_document(
            expected, "M", materialize_implicit_metrics(dict(FLAT), [LEAF_OP])
        )
        apply_validated_patches(spec, [("M", [LEAF_OP])], _config(tmp_path))
        assert json.loads(spec.read_text(encoding="utf-8")) == expected


NRMSE = {"metric": "nrmse", "variable": "x"}
#: A criterion that can never be scored. An entry that holds one makes the
#: write's check replay the patch (`refuse_newly_unscorable`).
BARE = {"metric": "range", "variable": "x"}


def _bounded(minimum: int) -> dict:
    return {"metric": "range", "variable": "x", "min_value": minimum}


class TestAPatchIsAppliedOnceToTheFileThatIsWritten:
    """The unscorable-criterion check replays the patch over a marked copy
    of the entry. While an op's value went into the document by reference,
    that replay ran its later ops inside the earlier ops' values — the
    objects the document to be written held — after every check had
    passed: the file got them twice. Each test reads the WRITTEN file."""

    @staticmethod
    def _held(tmp_path) -> Path:
        tree = {"combinator": "and", "children": [dict(NRMSE), dict(BARE)]}
        return _spec(tmp_path, {"model": "M", "variables": ["x"], "metrics": tree})

    @staticmethod
    def _tree(spec: Path) -> dict:
        return json.loads(spec.read_text(encoding="utf-8"))["tests"][0]["metrics"]

    def test_a_remove_after_the_replace_that_wrote_its_list_removes_one(self, tmp_path):
        spec = self._held(tmp_path)
        children = [dict(NRMSE), dict(BARE), _bounded(1), _bounded(2)]
        ops = [
            {
                "op": "replace",
                "path": "/metrics",
                "value": {"combinator": "and", "children": children},
            },
            {"op": "remove", "path": "/metrics/children/2"},
        ]
        apply_validated_patches(spec, [("M", ops)], _config(tmp_path))
        assert self._tree(spec)["children"] == [NRMSE, BARE, _bounded(2)]

    def test_a_remove_inside_the_group_the_patch_added_removes_one(self, tmp_path):
        spec = self._held(tmp_path)
        group = {
            "combinator": "k-of-n",
            "k": 1,
            "children": [dict(NRMSE), _bounded(1)],
        }
        ops = [
            {"op": "add", "path": "/metrics/children/-", "value": group},
            {"op": "remove", "path": "/metrics/children/2/children/0"},
        ]
        apply_validated_patches(spec, [("M", ops)], _config(tmp_path))
        assert self._tree(spec)["children"][2] == {**group, "children": [_bounded(1)]}

    def test_an_add_inside_the_group_the_patch_added_adds_one(self, tmp_path):
        spec = self._held(tmp_path)
        ops = [
            {
                "op": "add",
                "path": "/metrics/children/-",
                "value": {"combinator": "or", "children": [dict(NRMSE)]},
            },
            {
                "op": "add",
                "path": "/metrics/children/2/children/-",
                "value": _bounded(0),
            },
        ]
        apply_validated_patches(spec, [("M", ops)], _config(tmp_path))
        assert self._tree(spec)["children"][2]["children"] == [NRMSE, _bounded(0)]

    def test_the_ops_it_returns_are_the_ops_it_was_given(self, tmp_path):
        """What a caller says it did is what it was asked to do: the ops
        are not changed by the apply, nor by the check's replay."""
        spec = self._held(tmp_path)
        ops = [
            {
                "op": "add",
                "path": "/metrics/children/-",
                "value": {"combinator": "or", "children": [dict(NRMSE)]},
            },
            {
                "op": "add",
                "path": "/metrics/children/2/children/-",
                "value": _bounded(0),
            },
        ]
        given = copy.deepcopy(ops)
        ((_, applied),) = apply_validated_patches(spec, [("M", ops)], _config(tmp_path))
        assert applied == given and ops == given


def test_a_patch_that_leaves_plots_invalid_writes_nothing(tmp_path):
    config = _config(tmp_path)
    spec_path = _spec(tmp_path, {"model": "M", "variables": ["x"]})
    before = spec_path.read_bytes()
    bad = [{"op": "add", "path": "/plots", "value": [{"var": "x", "series": []}]}]
    with pytest.raises(PatchError, match=r'plots\[0\]: unknown field "series"'):
        apply_validated_patches(spec_path, [("M", bad)], config)
    assert spec_path.read_bytes() == before


def test_a_valid_plots_patch_is_written(tmp_path):
    config = _config(tmp_path)
    spec_path = _spec(tmp_path, {"model": "M", "variables": ["x"]})
    layout = [{"var": "x", "context": ["z"]}, {"var": "w"}]
    apply_validated_patches(
        spec_path, [("M", [{"op": "add", "path": "/plots", "value": layout}])], config
    )
    assert json.loads(spec_path.read_text())["tests"][0]["plots"] == layout
