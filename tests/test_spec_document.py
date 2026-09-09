"""The spec as the Session holds it: loaded document, content hash, pending ops."""

import json

import pytest

from dstf.authoring.spec_document import PatchOp, SpecDocument, content_hash
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
