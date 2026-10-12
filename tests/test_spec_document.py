"""The spec as the Session holds it: loaded document, content hash, pending ops."""

import json

import pytest

from dstf.authoring.spec_document import (
    PatchOp,
    SpecDocument,
    content_hash,
    edit_count,
    edit_counts,
)
from dstf.config import ConfigError
from dstf.discovery.patch_apply import PatchError

ENTRY = {"model": "M", "variables": ["x"], "comparison": {"tolerance": 0.1}}


def _spec(tmp_path, *entries):
    p = tmp_path / "test_spec.json"
    p.write_text(
        json.dumps({"tests": list(entries)}, indent=2) + "\n", encoding="utf-8"
    )
    return p


def test_load_keeps_the_document_and_its_hash(tmp_path):
    p = _spec(tmp_path, ENTRY)
    doc = SpecDocument.load(p)
    assert doc.loaded == {"tests": [ENTRY]}
    assert doc.content_hash == content_hash(p) and doc.pending == []
    assert not doc.on_disk_changed()


def test_a_missing_file_is_the_empty_document(tmp_path):
    doc = SpecDocument.load(tmp_path / "absent.json")
    assert doc.loaded == {"tests": []} and doc.content_hash is None
    assert not doc.on_disk_changed()
    (tmp_path / "absent.json").write_text("{}", encoding="utf-8")
    assert doc.on_disk_changed()


def test_malformed_json_is_a_config_error(tmp_path):
    p = tmp_path / "test_spec.json"
    p.write_text('{"tests": [', encoding="utf-8")
    with pytest.raises(ConfigError, match="test_spec.json"):
        SpecDocument.load(p)


@pytest.mark.parametrize("body", ['{"tests": {"M": 1}}', '{"tests": "abc"}', "[]"])
def test_a_malformed_shape_is_a_config_error_at_load(tmp_path, body):
    """The same shape rule spec-update's loader applies: a non-list `tests`
    used to load fine and explode as AttributeError on the first replay."""
    p = tmp_path / "test_spec.json"
    p.write_text(body, encoding="utf-8")
    with pytest.raises(ConfigError, match="test_spec.json"):
        SpecDocument.load(p)


def test_the_replay_is_cached_while_pending_only_grows(tmp_path):
    doc = SpecDocument.load(_spec(tmp_path, ENTRY))
    first = doc.entry("M")
    assert doc.entry("M") is first  # same replay
    doc.pending.append(PatchOp("M", "replace", "/comparison/tolerance", 0.5))
    again = doc.entry("M")
    assert again is not first and again["comparison"]["tolerance"] == 0.5
    assert doc.effective() is not doc.effective()  # callers get copies


def test_effective_applies_pending_without_touching_loaded(tmp_path):
    doc = SpecDocument.load(_spec(tmp_path, ENTRY))
    doc.pending.append(PatchOp("M", "replace", "/comparison/tolerance", 0.5))
    eff = doc.effective()
    assert eff["tests"][0]["comparison"]["tolerance"] == 0.5
    assert doc.loaded["tests"][0]["comparison"]["tolerance"] == 0.1
    assert doc.entry("M")["comparison"]["tolerance"] == 0.5


def test_grouped_keeps_first_seen_model_order_and_op_order(tmp_path):
    doc = SpecDocument.load(_spec(tmp_path, ENTRY, {"model": "N", "variables": ["y"]}))
    doc.pending += [
        PatchOp("N", "add", "/comparison", {"tolerance": 1.0}),
        PatchOp("M", "replace", "/comparison/tolerance", 0.5),
        PatchOp("N", "replace", "/comparison/tolerance", 2.0),
    ]
    assert doc.grouped() == [
        (
            "N",
            [
                {"op": "add", "path": "/comparison", "value": {"tolerance": 1.0}},
                {"op": "replace", "path": "/comparison/tolerance", "value": 2.0},
            ],
        ),
        ("M", [{"op": "replace", "path": "/comparison/tolerance", "value": 0.5}]),
    ]


def test_remove_op_carries_no_value():
    assert PatchOp("M", "remove", "/comparison/tolerance").raw() == {
        "op": "remove",
        "path": "/comparison/tolerance",
    }


def test_a_bad_pending_op_surfaces_from_effective(tmp_path):
    doc = SpecDocument.load(_spec(tmp_path, ENTRY))
    doc.pending.append(PatchOp("M", "replace", "/simulation/stop_time", 3))
    with pytest.raises(PatchError):
        doc.effective()


def test_on_disk_changed_after_a_write(tmp_path):
    p = _spec(tmp_path, ENTRY)
    doc = SpecDocument.load(p)
    p.write_text(p.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert doc.on_disk_changed()


def test_saved_entry_is_a_copy_of_the_loaded_entry_whatever_is_pending(tmp_path):
    doc = SpecDocument.load(_spec(tmp_path, ENTRY))
    doc.pending.append(PatchOp("M", "replace", "/comparison/tolerance", 0.5))
    doc.pending.append(PatchOp("N", "add", "/comparison", {"tolerance": 1}))
    saved = doc.saved_entry("M")
    assert saved == ENTRY and doc.entry("M")["comparison"]["tolerance"] == 0.5
    saved["comparison"]["tolerance"] = 9
    assert doc.loaded["tests"][0]["comparison"]["tolerance"] == 0.1
    assert doc.saved_entry("N") is None  # new in the pending ops, not loaded


TREE_X = {
    "combinator": "and",
    "children": [{"metric": "nrmse", "variable": "x", "tolerance": 0.1}],
}


def test_a_materializing_op_says_so_and_its_raw_form_does_not():
    op = PatchOp("M", "add", "/metrics", TREE_X, materializes=True)
    assert op.materializes and not PatchOp("M", "add", "/metrics", TREE_X).materializes
    assert op.raw() == {"op": "add", "path": "/metrics", "value": TREE_X}


def test_the_preview_base_is_the_saved_entry_with_its_materialization(tmp_path):
    """What the drawer diffs against: the entry as saved, with the tree the
    Session wrote out for it — which scores the same — already in it; the
    edits after it are what the diff shows."""
    doc = SpecDocument.load(_spec(tmp_path, ENTRY))
    doc.pending += [
        PatchOp("M", "add", "/metrics", TREE_X, materializes=True),
        PatchOp("M", "add", "/metrics/children/0/tolerance", 0.5),
        PatchOp("N", "add", "/comparison", {"tolerance": 1}),
    ]
    assert doc.preview_base("M") == {**ENTRY, "metrics": TREE_X}
    assert doc.loaded["tests"] == [ENTRY]  # nothing written into `loaded`
    base = doc.preview_base("M")
    base["metrics"]["children"][0]["tolerance"] = 9
    assert doc.preview_base("M")["metrics"] == TREE_X  # a copy
    # No materialization: the saved entry; a new one with none: no entry.
    assert doc.preview_base("N") is None
    doc.pending.append(PatchOp("P", "add", "/metrics", TREE_X, materializes=True))
    assert doc.preview_base("P") == {"model": "P", "metrics": TREE_X}


def test_the_preview_base_without_a_materialization_is_the_saved_entry(tmp_path):
    doc = SpecDocument.load(_spec(tmp_path, ENTRY))
    doc.pending.append(PatchOp("M", "add", "/metrics", TREE_X))
    assert doc.preview_base("M") == ENTRY


def test_the_edits_are_counted_by_what_was_done_not_by_the_ops_it_took():
    """One act of the user's is one edit of each entry it touched: a flat
    test's tree written out under its first edit, an add over twenty
    Indices and a folded row's twenty criteria are each ONE."""
    pending = [
        PatchOp("A", "add", "/metrics", {"combinator": "and"}, gesture=1),
        PatchOp("A", "add", "/metrics/children/0/tolerance", 0.5, gesture=1),
        PatchOp("B", "add", "/comparison", {}, gesture=2),
        *(
            PatchOp("B", "add", "/metrics/children/-", {"i": i}, gesture=3)
            for i in range(20)
        ),
        PatchOp("A", "add", "/plots", [], gesture=3),  # the same act, another entry
    ]
    assert edit_counts(pending) == {"A": 2, "B": 2}
    assert edit_count(pending) == 4
    assert edit_counts([]) == {} and edit_count([]) == 0


def test_an_op_that_nothing_staged_is_an_edit_of_its_own():
    pending = [PatchOp("A", "add", f"/plots/{i}", {}) for i in range(3)]
    assert all(op.gesture is None for op in pending)
    assert edit_counts(pending) == {"A": 3} and edit_count(pending) == 3


def test_the_gesture_is_no_part_of_the_op():
    one = PatchOp("A", "replace", "/x", 1, gesture=1)
    assert one == PatchOp("A", "replace", "/x", 1, gesture=7)
    assert "gesture" not in one.raw()
