"""`dstf discover` prints nothing that is not true: no solver for a source
that has none, no variable count for patterns nothing has resolved yet, and a
total that says what it counts."""

from __future__ import annotations

import json
import re
from pathlib import Path

from dstf.cli import main

REPO = Path(__file__).parent.parent
MODELICA = (
    REPO / "examples/modelica/ModelicaTestingLib/Resources/ReferenceResults"
) / "testing.json"


def _discover(capsys, config: Path) -> tuple[dict[str, list[str]], list[str]]:
    """``({short model name: its row's cells}, the lines under the table)``.
    Cells are separated by two or more spaces."""
    capsys.readouterr()
    assert main(["--config", str(config), "discover"]) == 0
    table, _, totals = capsys.readouterr().out.partition("\nTotal: ")
    rows = {}
    for line in table.splitlines()[2:]:
        cells = re.split(r"\s{2,}", line.strip())
        rows[cells[0].rsplit(".", 1)[-1]] = cells[1:]
    return rows, ("Total: " + totals).splitlines()


def test_a_python_test_has_no_method_and_its_patterns_are_called_patterns(
    tmp_path, capsys
):
    """The Python example: nothing in it names a solver, and `Dassl` is the
    framework's default for a Modelica tool, not something a Python
    `simulate()` uses. Its variables are tracked by pattern, resolved after
    the run: `Vars 0` and `Total tracked variables: 0` were not true."""
    from conftest import python_example_copy

    config, _ = python_example_copy(tmp_path)
    rows, totals = _discover(capsys, config.config_file)
    assert rows == {
        "ConstantCsv": ["1 pattern", "5", "—", "spec"],
        "PointsCheck": ["1 pattern", "10", "—", "spec"],
        "SimpleRamp": ["1 pattern", "5", "—", "spec"],
    }
    assert totals[:3] == [
        "Total: 3 tests",
        "  UnitTests variables: 0",
        "  Variable patterns: 3 (each is matched against its test's result after the run)",
    ]


def test_a_method_the_spec_names_is_shown_on_any_source(tmp_path, capsys):
    from conftest import python_example_copy

    config, _ = python_example_copy(tmp_path)
    spec_path = config.spec_path
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["tests"][0]["simulation"]["method"] = "RK45"
    spec["tests"][0]["variables"] = ["x", "y*"]
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    rows, totals = _discover(capsys, config.config_file)
    assert rows["SimpleRamp"] == ["2 patterns", "5", "RK45", "spec"]
    assert totals[2].startswith("  Variable patterns: 4 ")


def test_a_modelica_test_shows_its_unit_tests_variables_and_its_method(capsys):
    """On a Modelica source the default method IS what runs (`Dassl`), and a
    UnitTests component's variables are a count known before any run."""
    rows, totals = _discover(capsys, MODELICA)
    assert rows["SimpleTest"] == ["2", "10", "Dassl", "both"]
    # declared in test_spec.json only, by one pattern; its method is the default
    assert rows["NoUnitTest"][0] == "1 pattern"
    assert rows["NoUnitTest"][2:] == ["Dassl", "spec"]
    # recognized by `extends`, with no UnitTests component and no pattern
    assert rows["SimulateOnlyTest"][0] == "0"
    assert totals[1] == "  UnitTests variables: 14"
    assert totals[2].startswith("  Variable patterns: 1 ")


def test_unit_tests_variables_and_patterns_on_one_test_are_both_shown(
    tmp_path, capsys
):
    import shutil

    lib = tmp_path / "ModelicaTestingLib"
    shutil.copytree(MODELICA.parents[2], lib)
    refs = lib / "Resources" / "ReferenceResults"
    spec = json.loads((refs / "test_spec.json").read_text(encoding="utf-8"))
    for entry in spec["tests"]:
        if entry["model"].endswith(".SimpleTest"):
            entry["variables"] = ["x", "der*"]
    (refs / "test_spec.json").write_text(json.dumps(spec), encoding="utf-8")
    rows, _ = _discover(capsys, refs / "testing.json")
    assert rows["SimpleTest"][0] == "2 + 2 patterns"
