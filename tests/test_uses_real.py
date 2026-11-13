"""`dstf uses` over a copy of the bundled Modelica library, asked of a real
simulator. Each carries its simulator's marker, so CI's selection starts
none."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from conftest import PROJECT_ROOT
from dstf.cli import main
from test_live_results_real import _dymola_available, _ompython_available

LIB = "ModelicaTestingLib"
UNIT_TESTS = f"{LIB}.Components.UnitTests"


def _holding_a_unit_tests_component(lib: Path) -> list[str]:
    """The example tests that declare a UnitTests component, read from
    their sources (a mention in a test's documentation is none)."""
    return sorted(
        f"{LIB}.Examples.{path.stem}"
        for path in (lib / "Examples").glob("*.mo")
        if re.search(r"\bUnitTests\s+\w+\s*\(", path.read_text(encoding="utf-8"))
    )


@pytest.mark.parametrize(
    "backend",
    [
        pytest.param(
            "OpenModelica",
            marks=[
                pytest.mark.ompython,
                pytest.mark.skipif(
                    not _ompython_available(), reason="needs omc + OMPython"
                ),
            ],
        ),
        pytest.param(
            "Dymola",
            marks=[
                pytest.mark.dymola,
                pytest.mark.skipif(not _dymola_available(), reason="needs Dymola"),
            ],
        ),
    ],
)
def test_the_tests_that_hold_a_unit_tests_component_and_no_other(
    backend, tmp_path, monkeypatch, capsys
):
    lib = tmp_path / LIB
    shutil.copytree(PROJECT_ROOT / "examples" / "modelica" / LIB, lib)
    config = lib / "Resources" / "ReferenceResults" / "testing.json"
    monkeypatch.chdir(tmp_path)
    before = sorted(tmp_path.rglob("*"))
    out_file = tmp_path / "uses.txt"

    rc = main(
        ["uses", UNIT_TESTS, "--config", str(config), "--backend", backend]
        + ["-o", str(out_file)]
    )
    out, err = capsys.readouterr()

    expected = _holding_a_unit_tests_component(lib)
    assert len(expected) > 1
    assert rc == 0, err
    assert out_file.read_text(encoding="utf-8").splitlines() == expected
    assert out.startswith(f"{UNIT_TESTS} is used by {len(expected)} of ")
    assert "Could not be asked" not in out
    # nothing written but --output: no work dir, nothing in the library or
    # the current directory
    assert sorted(tmp_path.rglob("*")) == sorted([*before, out_file])
