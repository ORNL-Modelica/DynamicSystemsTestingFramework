"""Marks beside the commands that ask the simulator what a test uses:
`run --changed-since` asks only the tests no mark keeps from running (R4l),
and `dstf uses` flags a marked test on its line while `-o` still lists it.

Over tests/test_run_changed_since.py's project (a git copy of the bundled
Modelica library, a registered fake backend that answers what each test
uses and "simulates" at the simulator's boundary)."""

from __future__ import annotations

import json

from dstf.cli import main
from test_run_changed_since import (  # noqa: F401  (the fixtures, re-used)
    _painted,
    _read,
    _run,
    proj,
)
from test_uses_cli import (  # noqa: F401  (the fixtures, re-used)
    LIB,
    UNIT_TESTS,
    WITH,
    _touch,
    env,
    nothing_installed,
    repo,
)

#: The bundled library's tests that hold no UnitTests component: the fake
#: says none of them uses it.
WITHOUT = [
    f"{LIB}.Examples.{n}"
    for n in ("HeatRodTest", "NoUnitTest", "SimulateOnlyTest", "ThermostatBuildingTest")
]
REASON = "not on this lane"


def _mark(project, *models: str) -> None:
    """``skip`` on each model's entry of the library's test_spec.json (an
    entry made for a test the scan alone finds), committed: the run that
    follows sees only the edit it makes after."""
    spec = project.lib / "Resources" / "ReferenceResults" / "test_spec.json"
    doc = json.loads(spec.read_text(encoding="utf-8"))
    entries = {e["model"]: e for e in doc["tests"]}
    for model in models:
        if model not in entries:
            entries[model] = {"model": model}
            doc["tests"].append(entries[model])
        entries[model]["skip"] = REASON
    spec.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    project.git.commit("marks")


def test_changed_since_asks_only_unmarked_tests_and_running_all_of_them_is_full(
    proj,  # noqa: F811
    capsys,
):
    """R4l: every test no mark keeps from running uses what changed, so
    the run simulates what a full run would: it reads full, its marked
    tests are counted (not asked), and they get no row."""
    _mark(proj, *WITHOUT)
    proj.asked.clear()
    _touch(proj, "Components", "UnitTests.mo")
    rc, out, err = _run(proj, capsys, "--changed-since", "HEAD")
    # MultiFrequencyTest fails on the fake's sin(t), as it does unmarked
    assert rc == 1, out + err
    assert sorted(proj.simulated) == sorted(WITH)
    assert [sorted(asked) for asked in proj.asked] == [sorted(WITH)]
    assert "4 tests marked skip are not asked: they are not run on FakeRuns/" in out
    assert "Selected all 10 tests: each uses what changed.\n" in out
    assert "not a full run" not in out.lower()
    selection = _read(proj.work, "status.json")["selection"]
    assert (selection["selected"], selection["skipped"], selection["of"]) == (
        10,
        4,
        14,
    )
    assert _painted(proj.work / "dashboard.html") is None
    verdicts = _read(proj.work, "verdicts.json")
    assert {t["model_id"] for t in verdicts["tests"]} == set(WITH)


def test_uses_flags_a_marked_test_and_its_file_still_lists_it(env, capsys):  # noqa: F811
    simple = f"{LIB}.Examples.SimpleTest"
    spec = env.lib / "Resources" / "ReferenceResults" / "test_spec.json"
    doc = json.loads(spec.read_text(encoding="utf-8"))
    (entry,) = [e for e in doc["tests"] if e["model"] == simple]
    entry["skip"] = REASON
    spec.write_text(json.dumps(doc), encoding="utf-8")
    listed = env.cwd / "users.txt"
    rc = main(["uses", "--config", str(env.config), "-o", str(listed), UNIT_TESTS])
    out = capsys.readouterr().out
    assert rc == 0
    lines = out.splitlines()
    assert f"  {simple} (skipped on every backend: {REASON})" in lines
    assert f"  {WITH[0]}" in lines
    assert listed.read_text(encoding="utf-8").splitlines() == sorted(WITH)
