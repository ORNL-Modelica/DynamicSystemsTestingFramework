"""Every JSON file a project keeps under version control is written in one
layout — two-space indent, a newline at the end — so a write that changes
one value is a one-line diff, not a whole-file one."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from conftest import PROVENANCE, make_test_model
from dstf.backends.types import TestResult, VariableResult
from dstf.config import Config
from dstf.storage.reference_store import ReferenceStore


def _store(tmp_path: Path) -> ReferenceStore:
    lib = tmp_path / "FakeLib"
    lib.mkdir()
    (lib / "package.mo").write_text("package FakeLib\nend FakeLib;\n")
    store = ReferenceStore(
        Config(source_path=lib, reference_root=tmp_path / "refs", work_dir=tmp_path)
    )
    t = np.array([0.0, 1.0])
    for model in ("FakeLib.A", "FakeLib.B"):
        store.store_reference(
            make_test_model(model, variable_patterns=["x"]),
            TestResult(model, True, variables=[VariableResult(1, t, t, name="x")]),
            provenance=PROVENANCE,
        )
    return store


def _high_water_mark(tmp_path: Path) -> Path:
    return _store(tmp_path).ref_dir / ".id_high_water.json"


def _companion(tmp_path: Path, *, frozen: bool) -> Path:
    store = _store(tmp_path)
    src = tmp_path / "rig.csv"
    src.write_text("time,y\n0,1\n1,2\n")
    store.add_companion("FakeLib.A", "rig", src, variables={"x": "y"})
    if frozen:
        store.freeze_companion("FakeLib.A", "rig")
    return store.ref_dir / "companions" / "ref_0001" / "rig.json"


def _test_spec(tmp_path: Path) -> Path:
    from dstf.discovery.spec_parser import add_to_test_spec, update_test_variables

    spec = tmp_path / "test_spec.json"
    add_to_test_spec(spec, "Lib.A", ["x"])
    update_test_variables(spec, "Lib.A", ["y"])
    return spec


WRITERS = {
    "a test_spec.json an edit writes": _test_spec,
    "the id high-water mark": _high_water_mark,
    "a companion's registration": lambda p: _companion(p, frozen=False),
    "a frozen companion's registration": lambda p: _companion(p, frozen=True),
}


@pytest.mark.parametrize("what", list(WRITERS))
def test_every_versioned_file_is_written_two_space_indented(tmp_path, what):
    text = WRITERS[what](tmp_path).read_text(encoding="utf-8")
    assert text == json.dumps(json.loads(text), indent=2) + "\n"
