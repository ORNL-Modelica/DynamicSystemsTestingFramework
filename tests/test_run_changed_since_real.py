"""`dstf run --changed-since` over a git copy of the bundled Modelica
library, asked of and run by a real simulator. Each carries its simulator's
marker, so CI's selection starts none."""

from __future__ import annotations

import json
import shutil

import psutil
import pytest

from conftest import PROJECT_ROOT, GitRepo
from dstf.cli import main
from test_live_results_real import _dymola_available, _ompython_available

LIB = "ModelicaTestingLib"
#: The tests the two edits below touch: SimpleTest's own file, and
#: SimulateOnlyTest's base class Icons.Example.
TOUCHED = [f"{LIB}.Examples.SimpleTest", f"{LIB}.Examples.SimulateOnlyTest"]


#: The process names of the simulators, lower-cased (`omc`, `omc.exe`,
#: `Dymola.exe`).
SIMULATORS = ("omc", "dymola")


def _simulators() -> set[int]:
    """The simulator processes this test's process started that are alive
    now: its descendants only, so another program's simulator is not
    counted."""
    found = set()
    for child in psutil.Process().children(recursive=True):
        try:
            name = child.name().lower()
        except psutil.Error:
            continue
        if name.startswith(SIMULATORS):
            found.add(child.pid)
    return found


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
def test_only_the_tests_two_edits_touch_are_run(backend, tmp_path, monkeypatch, capsys):
    lib = tmp_path / "proj" / LIB
    shutil.copytree(PROJECT_ROOT / "examples" / "modelica" / LIB, lib)
    config = lib / "Resources" / "ReferenceResults" / "testing.json"
    git = GitRepo(lib.parent)
    git.commit("the library as it was")
    git.git("tag", "base")
    for parts in (("Icons", "Example.mo"), ("Examples", "SimpleTest.mo")):
        path = lib.joinpath(*parts)
        path.write_text(path.read_text(encoding="utf-8") + "// edited\n")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    work = tmp_path / "work"
    argv = ["run", "--config", str(config), "--backend", backend, "--no-browser"]

    before = _simulators()
    rc = main([*argv, "--work-dir", str(work), "--changed-since", "base"])
    out, err = capsys.readouterr()

    assert rc == 0, out + err
    assert (
        "Selected 2 of 14 tests: the ones that use what changed. Not a full run.\n"
        in out
    )
    assert "(2 of 14 tests selected; not a full run)\n" in out
    verdicts = json.loads((work / "verdicts.json").read_text(encoding="utf-8"))
    assert sorted(t["model_id"] for t in verdicts["tests"]) == TOUCHED
    selection = verdicts["selection"]
    assert (selection["selected"], selection["of"], selection["files"]) == (2, 14, 2)
    # the simulator that was asked, and the run's own, are gone
    assert _simulators() - before == set()

    # the edits committed: nothing changed, nothing is run or written
    git.commit("edited")
    another = tmp_path / "another-work"
    rc = main([*argv, "--work-dir", str(another), "--changed-since", "HEAD"])
    out, err = capsys.readouterr()
    assert (rc, out.splitlines()[-1]) == (0, "No test to run."), out + err
    assert not another.exists()
