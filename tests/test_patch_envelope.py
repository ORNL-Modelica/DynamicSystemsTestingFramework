"""Both envelope forms are read; only the multi-model form is written."""

import pytest

from dstf.config import ConfigError
from dstf.discovery.patch_envelope import ModelPatch, build_envelope, parse_envelope

OP = {"op": "replace", "path": "/comparison/tolerance", "value": 0.5}


def test_single_model_form_is_read():
    assert parse_envelope({"model": "M", "patch": [OP]}, label="f") == [
        ModelPatch("M", [OP])
    ]


def test_multi_model_form_is_read_in_order():
    data = {"patches": [{"model": "B", "patch": [OP]}, {"model": "A", "patch": []}]}
    assert [p.model_id for p in parse_envelope(data, label="f")] == ["B", "A"]


def test_build_writes_only_the_multi_model_form():
    env = build_envelope([ModelPatch("M", [OP])])
    assert env == {"patches": [{"model": "M", "patch": [OP]}]}
    assert "model" not in env
    assert parse_envelope(env, label="round trip") == [ModelPatch("M", [OP])]


@pytest.mark.parametrize(
    "bad",
    [
        {"model": "M", "comparison": {"tolerance": 1e-3}},  # no patch
        {"model": "M", "patch": {"op": "replace"}},  # patch not a list
        {"patches": {"model": "M"}},  # patches not a list
        {"patches": [{"patch": [OP]}]},  # a group with no model
        {"patch": [OP]},  # no model at all
        [],  # not an object
    ],
)
def test_other_shapes_are_config_errors_naming_both_forms(bad):
    with pytest.raises(
        ConfigError, match=r'not a JSON-Patch envelope.*"patch".*"patches"'
    ):
        parse_envelope(bad, label="f")
