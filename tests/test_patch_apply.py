"""Tests for discovery/patch_apply.py — RFC 6902 on test_spec.json.

Critical invariant from D66: unknown keys and hand-authored notes
(`description`, `info`, `metadata`) must survive patch-apply round-trips.
"""

from __future__ import annotations

import copy

import pytest

from dstf.config import ConfigError
from dstf.discovery.patch_apply import (
    PatchError,
    apply_patch_to_document,
    load_spec_document,
)


class TestBasicOps:
    def test_replace_scalar_in_whitelisted_path(self):
        doc = {"tests": [{"model": "M", "comparison": {"tolerance": 1e-4}}]}
        apply_patch_to_document(
            doc,
            "M",
            [
                {"op": "replace", "path": "/comparison/tolerance", "value": 1e-3},
            ],
        )
        data = doc
        assert data["tests"][0]["comparison"]["tolerance"] == 1e-3

    def test_add_new_key(self):
        doc = {"tests": [{"model": "M", "comparison": {}}]}
        apply_patch_to_document(
            doc,
            "M",
            [
                {"op": "add", "path": "/comparison/tolerance", "value": 0.01},
            ],
        )
        data = doc
        assert data["tests"][0]["comparison"]["tolerance"] == 0.01

    def test_remove_key(self):
        doc = {
            "tests": [
                {"model": "M", "comparison": {"tolerance": 1e-3, "info": "keep me"}}
            ]
        }
        apply_patch_to_document(
            doc,
            "M",
            [
                {"op": "remove", "path": "/comparison/tolerance"},
            ],
        )
        data = doc
        assert "tolerance" not in data["tests"][0]["comparison"]
        assert data["tests"][0]["comparison"]["info"] == "keep me"

    def test_add_nested_path_creates_intermediates(self):
        doc = {"tests": [{"model": "M"}]}
        apply_patch_to_document(
            doc,
            "M",
            [
                {
                    "op": "add",
                    "path": "/comparison/variable_overrides/h/tolerance",
                    "value": 0.01,
                },
            ],
        )
        data = doc
        assert (
            data["tests"][0]["comparison"]["variable_overrides"]["h"]["tolerance"]
            == 0.01
        )


class TestUnknownKeyPreservation:
    """D66 invariant: patch-apply must never destroy hand-authored fields."""

    def test_preserves_entry_level_metadata(self):
        doc = {
            "tests": [
                {
                    "model": "M",
                    "description": "A critical test — last modified 2024-03-15 by AB.",
                    "metadata": {"owner": "fluid-group", "priority": "high"},
                    "comparison": {"tolerance": 1e-4},
                }
            ],
        }
        apply_patch_to_document(
            doc,
            "M",
            [
                {"op": "replace", "path": "/comparison/tolerance", "value": 1e-3},
            ],
        )
        entry = doc["tests"][0]
        assert (
            entry["description"] == "A critical test — last modified 2024-03-15 by AB."
        )
        assert entry["metadata"] == {"owner": "fluid-group", "priority": "high"}

    def test_preserves_comparison_level_unknown_keys(self):
        doc = {
            "tests": [
                {
                    "model": "M",
                    "comparison": {
                        "tolerance": 1e-4,
                        "info": "Expect rising edge at t~2.4s",
                        "metadata": {"rationale": "Solver defaults produce ringing"},
                    },
                }
            ],
        }
        apply_patch_to_document(
            doc,
            "M",
            [
                {"op": "replace", "path": "/comparison/tolerance", "value": 1e-3},
            ],
        )
        comp = doc["tests"][0]["comparison"]
        assert comp["info"] == "Expect rising edge at t~2.4s"
        assert comp["metadata"] == {"rationale": "Solver defaults produce ringing"}

    def test_preserves_sibling_tests(self):
        doc = {
            "tests": [
                {"model": "A", "comparison": {"tolerance": 1e-4}},
                {
                    "model": "B",
                    "comparison": {"tolerance": 1e-5},
                    "metadata": {"note": "keep me untouched"},
                },
                {"model": "C", "comparison": {"tolerance": 1e-6}},
            ],
        }
        apply_patch_to_document(
            doc,
            "B",
            [
                {"op": "replace", "path": "/comparison/tolerance", "value": 1e-3},
            ],
        )
        data = doc
        assert data["tests"][0] == {"model": "A", "comparison": {"tolerance": 1e-4}}
        assert data["tests"][1]["metadata"] == {"note": "keep me untouched"}
        assert data["tests"][1]["comparison"]["tolerance"] == 1e-3
        assert data["tests"][2] == {"model": "C", "comparison": {"tolerance": 1e-6}}


class TestWhitelist:
    def test_outside_whitelist_rejected(self):
        doc = {"tests": [{"model": "M", "simulation": {"stop_time": 10}}]}
        with pytest.raises(PatchError, match="whitelist"):
            apply_patch_to_document(
                doc,
                "M",
                [
                    {"op": "replace", "path": "/simulation/stop_time", "value": 100},
                ],
            )

    def test_default_allows_comparison_and_metrics(self):
        doc = {
            "tests": [
                {
                    "model": "M",
                    "comparison": {"tolerance": 1e-4},
                    "metrics": {
                        "metric": "nrmse",
                        "variable": "h",
                        "tolerance": 1e-4,
                    },
                }
            ]
        }
        apply_patch_to_document(
            doc,
            "M",
            [
                {"op": "replace", "path": "/comparison/tolerance", "value": 1e-3},
                {"op": "replace", "path": "/metrics/tolerance", "value": 1e-3},
            ],
        )
        entry = doc["tests"][0]
        assert entry["comparison"]["tolerance"] == 1e-3
        assert entry["metrics"]["tolerance"] == 1e-3


class TestJsonPointerEscaping:
    def test_escape_tilde_and_slash(self):
        """RFC 6901: `~0` → `~`, `~1` → `/`. Order matters (~1 first)."""
        doc = {"tests": [{"model": "M", "comparison": {"variable_overrides": {}}}]}
        # Variable name literally `a/b~c` escapes to `a~1b~0c`
        apply_patch_to_document(
            doc,
            "M",
            [
                {
                    "op": "add",
                    "path": "/comparison/variable_overrides/a~1b~0c/tolerance",
                    "value": 1e-3,
                },
            ],
        )
        data = doc
        assert data["tests"][0]["comparison"]["variable_overrides"]["a/b~c"] == {
            "tolerance": 1e-3
        }


class TestErrorSurfacing:
    def test_missing_op_raises(self):
        doc = {"tests": [{"model": "M", "comparison": {}}]}
        with pytest.raises(PatchError, match="unsupported op"):
            apply_patch_to_document(doc, "M", [{"op": "copy", "path": "/comparison/x"}])

    def test_replace_missing_key_rejected(self):
        doc = {"tests": [{"model": "M", "comparison": {}}]}
        with pytest.raises(PatchError, match="not present"):
            apply_patch_to_document(
                doc,
                "M",
                [
                    {"op": "replace", "path": "/comparison/does_not_exist", "value": 1},
                ],
            )

    def test_add_missing_value_rejected(self):
        doc = {"tests": [{"model": "M", "comparison": {}}]}
        with pytest.raises(PatchError, match="requires 'value'"):
            apply_patch_to_document(
                doc, "M", [{"op": "add", "path": "/comparison/tolerance"}]
            )

    def test_invalid_path_shape_rejected(self):
        doc = {"tests": [{"model": "M"}]}
        with pytest.raises(PatchError):
            apply_patch_to_document(
                doc, "M", [{"op": "replace", "path": "bad-path", "value": 1}]
            )


class TestWholesaleMetricsReplace:
    """Stage-4 structural edits emit a single ``add`` at ``/metrics`` with
    the whole new tree. Verify wholesale replacement preserves hand-
    authored sibling keys on the test entry."""

    def test_add_metrics_tree_upserts_when_absent(self):
        doc = {
            "tests": [
                {
                    "model": "M",
                    "description": "hand-authored",
                    "comparison": {"tolerance": 1e-4},
                }
            ]
        }
        new_tree = {
            "combinator": "and",
            "children": [
                {"metric": "nrmse", "variable": "h"},
                {"metric": "nrmse", "variable": "v"},
            ],
        }
        apply_patch_to_document(
            doc,
            "M",
            [
                {"op": "add", "path": "/metrics", "value": new_tree},
            ],
        )
        entry = doc["tests"][0]
        assert entry["metrics"] == new_tree
        assert entry["description"] == "hand-authored"
        assert entry["comparison"]["tolerance"] == 1e-4

    def test_add_metrics_tree_replaces_existing(self):
        doc = {
            "tests": [
                {
                    "model": "M",
                    "metrics": {"metric": "nrmse", "variable": "h"},
                    "description": "keep me",
                }
            ]
        }
        new_tree = {
            "combinator": "warn",
            "children": [{"metric": "nrmse", "variable": "h"}],
        }
        apply_patch_to_document(
            doc,
            "M",
            [
                {"op": "add", "path": "/metrics", "value": new_tree},
            ],
        )
        entry = doc["tests"][0]
        assert entry["metrics"] == new_tree
        assert entry["description"] == "keep me"

    def test_add_metrics_preserves_sibling_test_entries(self):
        doc = {
            "tests": [
                {"model": "A", "metrics": {"metric": "nrmse", "variable": "x"}},
                {"model": "B", "metrics": {"metric": "nrmse", "variable": "y"}},
            ]
        }
        new_tree = {
            "combinator": "and",
            "children": [
                {"metric": "nrmse", "variable": "y"},
                {"metric": "range", "variable": "y", "min_value": -1, "max_value": 1},
            ],
        }
        apply_patch_to_document(
            doc,
            "B",
            [
                {"op": "add", "path": "/metrics", "value": new_tree},
            ],
        )
        data = doc
        assert data["tests"][0]["metrics"] == {"metric": "nrmse", "variable": "x"}
        assert data["tests"][1]["metrics"] == new_tree


class TestWindowPath:
    """Idea #46 UI surfacing — reporter emits window upserts via 'add' on
    /metrics/.../window. Verify round-trip with tree leaves."""

    def test_add_window_on_root_leaf(self):
        doc = {
            "tests": [
                {
                    "model": "M",
                    "metrics": {
                        "metric": "nrmse",
                        "variable": "h",
                        "tolerance": 1e-4,
                    },
                }
            ]
        }
        apply_patch_to_document(
            doc,
            "M",
            [
                {
                    "op": "add",
                    "path": "/metrics/window",
                    "value": {"start": 1.0, "end": 5.0},
                },
            ],
        )
        metrics = doc["tests"][0]["metrics"]
        assert metrics["window"] == {"start": 1.0, "end": 5.0}
        # Original fields untouched
        assert metrics["metric"] == "nrmse"
        assert metrics["variable"] == "h"

    def test_add_window_on_nested_leaf(self):
        doc = {
            "tests": [
                {
                    "model": "M",
                    "metrics": {
                        "combinator": "and",
                        "children": [
                            {"metric": "nrmse", "variable": "x"},
                            {"metric": "nrmse", "variable": "y"},
                        ],
                    },
                }
            ]
        }
        apply_patch_to_document(
            doc,
            "M",
            [
                {
                    "op": "add",
                    "path": "/metrics/children/1/window",
                    "value": {"start": 2.0},
                },
            ],
        )
        children = doc["tests"][0]["metrics"]["children"]
        assert children[0] == {"metric": "nrmse", "variable": "x"}
        assert children[1]["window"] == {"start": 2.0}

    def test_add_upserts_existing_window(self):
        """'add' on a pre-existing window replaces the value (must_exist=False
        doesn't reject already-present keys — matches reporter's upsert usage)."""
        doc = {
            "tests": [
                {
                    "model": "M",
                    "metrics": {
                        "metric": "nrmse",
                        "variable": "h",
                        "window": {"start": 0.0, "end": 10.0},
                    },
                }
            ]
        }
        apply_patch_to_document(
            doc,
            "M",
            [
                {
                    "op": "add",
                    "path": "/metrics/window",
                    "value": {"start": 1.0, "end": 5.0},
                },
            ],
        )
        assert doc["tests"][0]["metrics"]["window"] == {
            "start": 1.0,
            "end": 5.0,
        }

    def test_remove_window(self):
        doc = {
            "tests": [
                {
                    "model": "M",
                    "metrics": {
                        "metric": "nrmse",
                        "variable": "h",
                        "window": {"start": 1.0, "end": 5.0},
                    },
                }
            ]
        }
        apply_patch_to_document(
            doc,
            "M",
            [
                {"op": "remove", "path": "/metrics/window"},
            ],
        )
        metrics = doc["tests"][0]["metrics"]
        assert "window" not in metrics
        assert metrics["metric"] == "nrmse"


class TestMissingModel:
    def test_creates_new_entry_when_model_not_found(self):
        doc = {"tests": [{"model": "A", "comparison": {"tolerance": 1e-4}}]}
        apply_patch_to_document(
            doc,
            "B",
            [
                {"op": "add", "path": "/comparison/tolerance", "value": 1e-3},
            ],
        )
        data = doc
        assert len(data["tests"]) == 2
        assert data["tests"][1]["model"] == "B"
        assert data["tests"][1]["comparison"]["tolerance"] == 1e-3

    def test_a_missing_spec_file_is_the_empty_document(self, tmp_path):
        doc = load_spec_document(tmp_path / "nonexistent.json")
        apply_patch_to_document(
            doc,
            "B",
            [
                {"op": "add", "path": "/comparison/tolerance", "value": 1e-3},
            ],
        )
        assert doc == {"tests": [{"model": "B", "comparison": {"tolerance": 1e-3}}]}


class TestRejectionEdges:
    """Validation/rejection guards (D88) — these protect the spec from
    malformed patches exported by the reporter. They were the uncovered
    branches in the 77% baseline."""

    def _spec(self):
        return {"tests": [{"model": "M", "comparison": {"a": [1, 2]}}]}

    def test_corrupt_json_rejected(self, tmp_path):
        spec = tmp_path / "spec.json"
        spec.write_text("{not json", encoding="utf-8")
        with pytest.raises(ConfigError, match=r"spec\.json: not JSON \("):
            load_spec_document(spec)
        assert spec.read_text(encoding="utf-8") == "{not json"

    def test_root_not_object_rejected(self, tmp_path):
        spec = tmp_path / "spec.json"
        spec.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(ConfigError, match=r"spec\.json: must be an object, got"):
            load_spec_document(spec)

    def test_tests_not_list_rejected(self, tmp_path):
        spec = tmp_path / "spec.json"
        spec.write_text('{"tests": {}}', encoding="utf-8")
        with pytest.raises(ConfigError, match=r"spec\.json: tests: must be a list"):
            load_spec_document(spec)

    def test_pointer_must_start_with_slash(self):
        with pytest.raises(PatchError, match="JSON-Pointer"):
            apply_patch_to_document(
                self._spec(),
                "M",
                [{"op": "add", "path": "comparison/x", "value": 1}],
            )

    def test_add_requires_value(self):
        with pytest.raises(PatchError, match="requires 'value'"):
            apply_patch_to_document(
                self._spec(), "M", [{"op": "add", "path": "/comparison/x"}]
            )

    def test_empty_path_rejected(self):
        # An empty pointer fails the RFC 6901 format guard (it does not start
        # with "/"), protecting the entry root from a wholesale overwrite.
        with pytest.raises(PatchError, match="JSON-Pointer"):
            apply_patch_to_document(
                self._spec(), "M", [{"op": "add", "path": "", "value": 1}]
            )

    def test_remove_missing_key(self):
        with pytest.raises(PatchError, match="not present"):
            apply_patch_to_document(
                self._spec(),
                "M",
                [{"op": "remove", "path": "/comparison/ghost"}],
            )

    def test_list_index_out_of_range(self):
        with pytest.raises(PatchError, match="out of range"):
            apply_patch_to_document(
                self._spec(),
                "M",
                [{"op": "replace", "path": "/comparison/a/9", "value": 0}],
            )

    def test_list_index_not_integer(self):
        with pytest.raises(PatchError, match="must be an integer"):
            apply_patch_to_document(
                self._spec(),
                "M",
                [{"op": "replace", "path": "/comparison/a/x", "value": 0}],
            )

    def test_append_token_rejected_on_replace(self):
        with pytest.raises(PatchError, match="append"):
            apply_patch_to_document(
                self._spec(),
                "M",
                [{"op": "replace", "path": "/comparison/a/-", "value": 0}],
            )


def _one_leaf_document() -> dict:
    return {
        "tests": [
            {
                "model": "M",
                "comparison": {},
                "metrics": {
                    "combinator": "and",
                    "children": [{"metric": "nrmse", "variable": "x"}],
                },
            }
        ]
    }


@pytest.mark.parametrize(
    "op,reason",
    [
        (
            {"op": "replace", "path": "/metrics/children/1", "value": {}},
            "list index 1 out of range (len 1)",
        ),
        ({"op": "remove", "path": "/metrics/children/1"}, "list index 1 out of range"),
        (
            {"op": "replace", "path": "/metrics/children/1/tolerance", "value": 1},
            "list index 1 out of range",
        ),
        (
            {"op": "remove", "path": "/metrics/children/-"},
            "'-' (append) not allowed on non-add op",
        ),
        (
            {"op": "replace", "path": "/metrics/children/-/tolerance", "value": 1},
            "'-' (append) not allowed on non-add op",
        ),
        (
            {"op": "remove", "path": "/comparison/missing/x"},
            "key 'missing' not present (use 'add' for new keys)",
        ),
        (
            {"op": "add", "path": "/comparison/x/5/y", "value": 1},
            "key 'x' not present and the next token '5' is an array index — "
            "refusing to auto-create a dict in place of an array (the test "
            "entry likely has no explicit 'metrics' tree yet)",
        ),
    ],
    ids=[
        "replace one past the end",
        "remove one past the end",
        "descend one past the end",
        "remove the append token",
        "descend through the append token",
        "remove under a missing key",
        "an index under a key that is not there",
    ],
)
def test_a_refused_op_is_a_patch_error_and_changes_nothing(op, reason):
    document = _one_leaf_document()
    with pytest.raises(PatchError) as box:
        apply_patch_to_document(document, "M", [op])
    assert box.value.reason == reason
    assert document == _one_leaf_document()


# =========================================================================
# Folded in from tests/test_review_fixes_discovery.py — regression tests for
# the 2026-07-06 review, Themes 2 & 6.
#
# Finding 49: RFC 6902 `add` at an existing array index INSERTS; index == len
# appends; index > len raises PatchError; auto-creating a dict parent where
# the next token is an array index raises PatchError (no silent corruption).
# =========================================================================


# ---------------------------------------------------------------------------
# Finding 49 — RFC 6902 array `add` semantics
# ---------------------------------------------------------------------------


class TestFinding49ArrayAdd:
    def _spec(self) -> dict:
        return {
            "tests": [
                {
                    "model": "M",
                    "metrics": {
                        "combinator": "and",
                        "children": [
                            {"metric": "nrmse", "variable": "x"},
                            {"metric": "nrmse", "variable": "y"},
                        ],
                    },
                }
            ]
        }

    def test_add_at_existing_index_inserts(self):
        doc = self._spec()
        new_leaf = {"metric": "range", "variable": "z"}
        apply_patch_to_document(
            doc,
            "M",
            [{"op": "add", "path": "/metrics/children/1", "value": new_leaf}],
        )
        children = doc["tests"][0]["metrics"]["children"]
        assert [c["variable"] for c in children] == ["x", "z", "y"]

    def test_add_dash_appends(self):
        doc = self._spec()
        apply_patch_to_document(
            doc,
            "M",
            [
                {
                    "op": "add",
                    "path": "/metrics/children/-",
                    "value": {"metric": "nrmse", "variable": "z"},
                }
            ],
        )
        children = doc["tests"][0]["metrics"]["children"]
        assert [c["variable"] for c in children] == ["x", "y", "z"]

    def test_add_at_len_appends(self):
        doc = self._spec()
        apply_patch_to_document(
            doc,
            "M",
            [
                {
                    "op": "add",
                    "path": "/metrics/children/2",
                    "value": {"metric": "nrmse", "variable": "z"},
                }
            ],
        )
        children = doc["tests"][0]["metrics"]["children"]
        assert [c["variable"] for c in children] == ["x", "y", "z"]

    def test_add_beyond_len_raises_patch_error(self):
        doc = self._spec()
        with pytest.raises(PatchError, match="out of range"):
            apply_patch_to_document(
                doc,
                "M",
                [
                    {
                        "op": "add",
                        "path": "/metrics/children/9",
                        "value": {"metric": "nrmse", "variable": "z"},
                    }
                ],
            )

    def test_add_refuses_dict_autocreate_for_array_index(self):
        """A per-leaf patch against a test with no metrics tree must fail
        loudly instead of silently creating {"children": {"0": ...}}."""
        doc = {"tests": [{"model": "M", "comparison": {"tolerance": 1e-4}}]}
        with pytest.raises(PatchError, match="array index"):
            apply_patch_to_document(
                doc,
                "M",
                [
                    {
                        "op": "add",
                        "path": "/metrics/children/0/tolerance",
                        "value": 0.01,
                    }
                ],
            )
        # No dict stands where the array would be. (The document is the
        # caller's to drop: the one writer, spec update, writes nothing.)
        assert "children" not in doc["tests"][0]["metrics"]


def test_plots_is_a_whitelisted_subtree():
    from dstf.discovery.patch_apply import apply_patch_to_document

    data = {"tests": [{"model": "M", "variables": ["x"]}]}
    extras = [{"var": "x", "context": []}]
    apply_patch_to_document(
        data, "M", [{"op": "add", "path": "/plots", "value": extras}]
    )
    apply_patch_to_document(
        data, "M", [{"op": "add", "path": "/plots/0/context/-", "value": "y"}]
    )
    assert data["tests"][0]["plots"] == [{"var": "x", "context": ["y"]}]


def test_variables_is_still_outside_the_whitelist():
    """A guard (passes before the change)."""
    from dstf.discovery.patch_apply import (
        DEFAULT_WHITELIST,
        PatchError,
        apply_patch_to_document,
    )

    assert DEFAULT_WHITELIST == ("/comparison", "/metrics", "/plots")
    op = [{"op": "add", "path": "/variables/-", "value": "y"}]
    data = {"tests": [{"model": "M", "variables": ["x"]}]}
    with pytest.raises(PatchError, match="whitelist"):
        apply_patch_to_document(data, "M", op)


def _containers(value) -> set[int]:
    """The id of every dict and list in ``value``."""
    found: set[int] = set()
    if isinstance(value, dict | list):
        found.add(id(value))
        for inner in value.values() if isinstance(value, dict) else value:
            found |= _containers(inner)
    return found


class TestADocumentSharesNothingWithThePatchThatWroteIt:
    """An op's value is written into the document as a copy. Written in by
    reference, a later op of the same patch edited the earlier op's own
    value: the list the caller held was then another patch, and applied
    again — `spec_update`'s replay over a marked copy, on the way to the
    file — it did its later ops twice."""

    @staticmethod
    def _ops() -> list[dict]:
        return [
            {"op": "add", "path": "/metrics", "value": {"children": [{"k": [1]}]}},
            {"op": "add", "path": "/metrics/b", "value": 1},
            {"op": "add", "path": "/metrics/children/-", "value": {"k": [2]}},
            {"op": "replace", "path": "/metrics/children/0", "value": {"k": [3]}},
            {"op": "add", "path": "/metrics/children/0/k/-", "value": 4},
            {"op": "remove", "path": "/metrics/children/1/k"},
        ]

    def test_applying_a_patch_leaves_its_ops_as_they_were(self):

        from dstf.discovery.patch_apply import apply_patch_to_document

        ops = self._ops()
        given = copy.deepcopy(ops)
        data = {"tests": [{"model": "M"}]}
        apply_patch_to_document(data, "M", ops)
        assert ops == given
        assert data["tests"][0]["metrics"] == {
            "children": [{"k": [3, 4]}, {}],
            "b": 1,
        }

    def test_the_same_ops_give_two_documents_that_share_no_object(self):
        from dstf.discovery.patch_apply import apply_patch_to_document

        ops = self._ops()
        first = {"tests": [{"model": "M"}]}
        second = {"tests": [{"model": "M"}]}
        apply_patch_to_document(first, "M", ops)
        apply_patch_to_document(second, "M", ops)
        assert first == second
        assert not _containers(first) & _containers(second)
        assert not _containers(first) & _containers(ops)
        # and an edit of one is no edit of the other, nor of the patch
        first["tests"][0]["metrics"]["children"][0]["k"].append(5)
        assert second["tests"][0]["metrics"]["children"][0]["k"] == [3, 4]
        assert ops == self._ops()
