"""What the tool says when an action fails: an error a person caused reads as
its plain reason, the location already on the screen beside it; anything
else is a bug and keeps its class name."""

from __future__ import annotations

import pytest

from dstf.authoring.errors import user_text
from dstf.comparison.handles import HandleEditError
from dstf.discovery.patch_apply import PatchError, apply_patch_to_document


def test_a_patch_error_shows_its_reason_only():
    e = PatchError("validator rejected the patched tree: metrics: X", reason="X")
    assert user_text(e) == "X"


def test_a_handle_error_shows_its_reason_only():
    e = HandleEditError(
        "/metrics/children/1: range/max: lower above upper", reason="lower above upper"
    )
    assert user_text(e) == "lower above upper"


def test_an_expected_error_has_no_class_name():
    assert user_text(ValueError("no result for x")) == "no result for x"


def test_an_unexpected_error_keeps_its_class_name():
    assert user_text(KeyError("k")) == "KeyError: 'k'"


def test_a_failing_ops_reason_drops_the_op_and_its_path():
    """``str()`` names the op and the pointer for the CLI; the reason is the
    part a person can act on."""
    data = {"tests": [{"model": "M", "variables": ["x"]}]}
    with pytest.raises(PatchError) as box:
        apply_patch_to_document(
            data, "M", [{"op": "remove", "path": "/comparison/nope"}]
        )
    assert str(box.value).startswith("/comparison/nope: patch_ops[0]")
    assert "patch_ops" not in box.value.reason
    assert "/comparison" not in box.value.reason
    assert user_text(box.value) == box.value.reason
