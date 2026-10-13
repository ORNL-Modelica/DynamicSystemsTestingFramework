"""Tests for the load-modify-write helpers in discovery/spec_parser.py.

The edit helpers (``add_to_test_spec``, ``update_test_variables``) used to
swallow a ``JSONDecodeError``, keep the
empty ``{"tests": []}`` they had initialized, and write it — one typo in a
hand-authored test_spec.json and every entry in it was gone. They now raise
:class:`ConfigError` (which ``cli.main`` renders as a clean message) and
leave the file untouched.
"""

from __future__ import annotations

import json

import pytest

from dstf.config import ConfigError
from dstf.discovery.spec_parser import (
    add_to_test_spec,
    update_test_variables,
)

CORRUPT = '{"tests": [{"model": "Lib.A", "variables": ["x"]},\n'  # truncated


def _edits(spec_path):
    """Each entry point as a zero-arg callable."""
    return {
        "add_to_test_spec": lambda: add_to_test_spec(spec_path, "Lib.B", ["y"]),
        "update_test_variables": lambda: update_test_variables(
            spec_path, "Lib.B", ["y"]
        ),
    }


@pytest.mark.parametrize(
    "entry_point",
    ["add_to_test_spec", "update_test_variables"],
)
def test_corrupt_spec_raises_and_is_left_untouched(tmp_path, entry_point):
    spec = tmp_path / "test_spec.json"
    spec.write_text(CORRUPT, encoding="utf-8")

    with pytest.raises(ConfigError, match=r"test_spec\.json: not JSON \("):
        _edits(spec)[entry_point]()

    assert spec.read_text(encoding="utf-8") == CORRUPT


@pytest.mark.parametrize(
    "entry_point",
    ["add_to_test_spec", "update_test_variables"],
)
def test_non_object_spec_root_raises(tmp_path, entry_point):
    spec = tmp_path / "test_spec.json"
    spec.write_text("[1, 2, 3]\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="must be an object, got"):
        _edits(spec)[entry_point]()

    assert spec.read_text(encoding="utf-8") == "[1, 2, 3]\n"


@pytest.mark.parametrize(
    "entry_point",
    ["add_to_test_spec", "update_test_variables"],
)
def test_missing_spec_is_created(tmp_path, entry_point):
    spec = tmp_path / "sub" / "test_spec.json"
    _edits(spec)[entry_point]()

    data = json.loads(spec.read_text(encoding="utf-8"))
    assert [e["model"] for e in data["tests"]] == ["Lib.B"]


def test_valid_spec_entries_survive_an_edit(tmp_path):
    spec = tmp_path / "test_spec.json"
    spec.write_text(
        json.dumps({"tests": [{"model": "Lib.A", "variables": ["x"]}]}, indent=2),
        encoding="utf-8",
    )
    add_to_test_spec(spec, "Lib.B", ["y"])
    data = json.loads(spec.read_text(encoding="utf-8"))
    assert [e["model"] for e in data["tests"]] == ["Lib.A", "Lib.B"]


def test_spec_is_written_with_lf(tmp_path):
    """test_spec.json is version-controlled by the consumer, so it
    must not pick up CRLF when the edit happens to run on Windows."""
    spec = tmp_path / "test_spec.json"
    add_to_test_spec(spec, "Lib.B", ["y"])

    assert b"\r" not in spec.read_bytes()
