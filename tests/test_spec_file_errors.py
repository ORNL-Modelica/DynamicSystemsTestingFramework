"""A spec file no command can read is ONE ``Config error:`` line, exit 2.

``dstf author`` said so before it served. ``discover`` / ``run`` /
``compare`` logged ``Failed to read test spec …`` and went on with a spec
of no tests (``No tests found.``, exit 1), ``spec-update`` printed ``Patch
failed: cannot read …``, and a UTF-16 or binary file was a traceback out
of each of them. An entry that could not be a test — no object, no
``model``, ``variables`` that is no list of names, a ``simulation`` value
that is no number — was a warning and a test skipped or run on defaults,
or (``"variables": [1]``) a traceback at the compare.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from dstf.cli import main
from dstf.config import ConfigError
from dstf.discovery.spec_parser import (
    parse_test_spec,
    read_spec_document,
    spec_models_from_document,
)
from test_session import A, _project, _snapshot

# ---------------------------------------------------------------------------
# the file
# ---------------------------------------------------------------------------

#: bytes of a `test_spec.json` → what every command says after the path.
UNREADABLE = {
    "not JSON": (
        b'{"tests": [,]}',
        "not JSON (Expecting value: line 1 column 12 (char 11))",
    ),
    "empty": (b"", "the file is empty"),
    "blank": (b" \n\t\n", "the file is empty"),
    "UTF-16": (
        '{"tests": []}'.encode("utf-16"),
        "not UTF-8 text ('utf-8' codec can't decode byte 0xff in position 0: "
        "invalid start byte)",
    ),
    "binary": (
        bytes(range(256)),
        "not UTF-8 text ('utf-8' codec can't decode byte 0x80 in position 128: "
        "invalid start byte)",
    ),
    "a list": (b"[]", "must be an object, got []"),
    "tests no list": (b'{"tests": {}}', "tests: must be a list, got {}"),
    "a root key nothing reads": (
        b'{"Tests": []}',
        'unknown field "Tests" — did you mean "tests"?',
    ),
}

COMMANDS = ["discover", "run", "compare", "spec-update", "author"]


def _argv(command: str, cfg: Path, work: Path, tmp_path: Path) -> list[str]:
    if command == "discover":
        return ["--config", str(cfg), "discover"]
    if command == "spec-update":
        patch = tmp_path / "patch.json"
        op = {"op": "add", "path": "/comparison", "value": {"tolerance": 0.5}}
        patch.write_text(
            json.dumps({"patches": [{"model": A, "patch": [op]}]}), encoding="utf-8"
        )
        return ["--config", str(cfg), "spec-update", str(patch)]
    extra = ["--port", "8879"] if command == "author" else []
    return ["--config", str(cfg), command, "--no-browser", "--work-dir", str(work)] + (
        extra
    )


@pytest.fixture
def nothing_served(monkeypatch):
    """`dstf author` up to the point where it would serve: a project it does
    not stop at returns 0 from here, and no server starts under pytest."""
    if importlib.util.find_spec("nicegui") is None:
        return
    import dstf.authoring.shell.app as app_mod

    monkeypatch.setattr(app_mod.app, "on_shutdown", lambda fn: None)
    monkeypatch.setattr(app_mod.app, "on_startup", lambda fn: None)
    monkeypatch.setattr(app_mod.ui, "run", lambda *a, **k: None)
    monkeypatch.setattr(app_mod.Shell, "mount", lambda self: None)


def _stopped(command, cfg, work, tmp_path, capsys) -> str:
    """The command's one line, having exited 2 and touched nothing."""
    if command == "author":
        pytest.importorskip("nicegui")
    spec = cfg.parent / "test_spec.json"
    before = (_snapshot(work), spec.read_bytes())
    capsys.readouterr()
    assert main(_argv(command, cfg, work, tmp_path)) == 2
    err = capsys.readouterr().err.strip().splitlines()
    assert (_snapshot(work), spec.read_bytes()) == before
    assert "Traceback" not in "\n".join(err)
    return err[-1]


@pytest.mark.parametrize("command", COMMANDS)
@pytest.mark.parametrize("case", UNREADABLE)
def test_a_file_that_is_no_spec_stops_every_command(
    tmp_path, capsys, nothing_served, command, case
):
    raw, said = UNREADABLE[case]
    cfg, work = _project(tmp_path)
    spec = cfg.parent / "test_spec.json"
    spec.write_bytes(raw)
    assert _stopped(command, cfg, work, tmp_path, capsys) == (
        f"Config error: {spec}: {said}"
    )


def test_a_file_that_cannot_be_opened_says_the_systems_reason(tmp_path):
    spec = tmp_path / "test_spec.json"
    spec.mkdir()
    with pytest.raises(ConfigError) as refused:
        read_spec_document(spec)
    assert str(refused.value).startswith(f"{spec}: ")
    assert "Is a directory" in str(refused.value)


def test_a_missing_file_is_a_spec_of_no_tests(tmp_path):
    assert read_spec_document(tmp_path / "test_spec.json") is None
    assert parse_test_spec(tmp_path / "test_spec.json") == []


@pytest.mark.parametrize("raw", [b"{}", b'{"tests": []}', b'\xef\xbb\xbf{"tests": []}'])
def test_a_spec_of_no_tests_is_read(tmp_path, raw):
    """`{}` holds no tests, and that is no error: a BOM is none either."""
    spec = tmp_path / "test_spec.json"
    spec.write_bytes(raw)
    assert read_spec_document(spec) == {"tests": []}
    assert parse_test_spec(spec) == []


# ---------------------------------------------------------------------------
# an entry
# ---------------------------------------------------------------------------


def _entry(**changes) -> dict:
    entry = {"model": A, "source": "../Examples/A.py", "variables": ["x"]}
    entry.update(changes)
    return {k: v for k, v in entry.items() if v is not ...}


#: the `tests` list → what is said after the path.
NO_TEST = {
    "an entry that is no object": ([1], "tests[0]: must be an object, got 1"),
    "a string": (
        [_entry(), "FakeLib.B"],
        'tests[1]: must be an object, got "FakeLib.B"',
    ),
    "no model": ([_entry(model=...)], 'tests[0]: has no "model"'),
    "an empty model": ([_entry(model="")], 'tests[0]: has no "model"'),
    "a model that is no name": (
        [_entry(model=5)],
        "tests[0].model: must be a model's name, got 5",
    ),
    "variables that is no list": (
        [_entry(variables="x")],
        f'tests[{A}].variables: must be a list of names or patterns, got "x"',
    ),
    "variables null": (
        [_entry(variables=None)],
        f"tests[{A}].variables: must be a list of names or patterns, got null",
    ),
    "a variable that is no name": (
        [_entry(variables=["x", 1])],
        f"tests[{A}].variables[1]: must be a name or a pattern, got 1",
    ),
    "a source that is no path": (
        [_entry(source=5)],
        f"tests[{A}].source: must be a path, got 5",
    ),
    "a stop time that is no number": (
        [_entry(simulation={"stop_time": "abc"})],
        f'tests[{A}].simulation.stop_time: must be a number, got "abc"',
    ),
    "a number written as text": (
        [_entry(simulation={"tolerance": "1e-6"})],
        f'tests[{A}].simulation.tolerance: must be a number, got "1e-6"',
    ),
    "true is no number": (
        [_entry(simulation={"output_interval": True})],
        f"tests[{A}].simulation.output_interval: must be a number, got true",
    ),
    "NaN is no number": (
        [_entry(simulation={"stop_time": float("nan")})],
        f"tests[{A}].simulation.stop_time: must be a number, got NaN",
    ),
    "a pair is no number": (
        [_entry(simulation={"stop_time": {"lower": 1}})],
        f'tests[{A}].simulation.stop_time: must be a number, got {{"lower": 1}}',
    ),
    "intervals that are no whole number": (
        [_entry(simulation={"number_of_intervals": 2.5})],
        f"tests[{A}].simulation.number_of_intervals: must be a whole number, got 2.5",
    ),
    "a timeout written as text": (
        [_entry(simulation={"timeout": "60"})],
        f'tests[{A}].simulation.timeout: must be a whole number, got "60"',
    ),
    "a method that is no name": (
        [_entry(simulation={"method": 5})],
        f"tests[{A}].simulation.method: must be a string, got 5",
    ),
}


@pytest.mark.parametrize("command", ["discover", "run", "compare", "author"])
@pytest.mark.parametrize("case", ["an entry that is no object", "no model"])
def test_an_entry_that_is_no_test_stops_every_command(
    tmp_path, capsys, nothing_served, command, case
):
    tests, said = NO_TEST[case]
    cfg, work = _project(tmp_path)
    spec = cfg.parent / "test_spec.json"
    spec.write_text(json.dumps({"tests": tests}), encoding="utf-8")
    assert _stopped(command, cfg, work, tmp_path, capsys) == (
        f"Config error: {spec}: {said}"
    )


@pytest.mark.parametrize("case", NO_TEST)
def test_an_entry_that_is_no_test_is_refused_with_its_place(tmp_path, case):
    tests, said = NO_TEST[case]
    with pytest.raises(ConfigError) as refused:
        spec_models_from_document({"tests": tests}, base_dir=tmp_path, label="spec")
    assert str(refused.value) == f"spec: {said}"


def test_a_variable_that_is_no_name_was_a_traceback_at_the_compare(tmp_path, capsys):
    cfg, work = _project(tmp_path)
    spec = cfg.parent / "test_spec.json"
    spec.write_text(json.dumps({"tests": [_entry(variables=[1])]}), encoding="utf-8")
    assert _stopped("compare", cfg, work, tmp_path, capsys) == (
        f"Config error: {spec}: tests[{A}].variables[0]: must be a name or a "
        "pattern, got 1"
    )


def test_spec_update_refuses_to_patch_an_entry_that_is_no_test(tmp_path, capsys):
    """The patched entry is checked as the parser checks it: `Patch failed`,
    the file untouched."""
    cfg, work = _project(tmp_path)
    spec = cfg.parent / "test_spec.json"
    spec.write_text(json.dumps({"tests": [_entry(variables="x")]}), encoding="utf-8")
    before = spec.read_bytes()
    assert main(_argv("spec-update", cfg, work, tmp_path)) == 1
    assert capsys.readouterr().out.strip() == (
        f"Patch failed: patched entry is invalid: tests[{A}].variables: must be a "
        'list of names or patterns, got "x"'
    )
    assert spec.read_bytes() == before


def test_what_the_parser_reads_is_still_read(tmp_path):
    """Whole numbers where a number is read, an integral float where a whole
    number is, and `null`: not set."""
    entry = _entry(
        simulation={
            "stop_time": 5,
            "tolerance": 1e-6,
            "method": "Dassl",
            "number_of_intervals": 500.0,
            "output_interval": None,
            "timeout": 120,
        }
    )
    (test,) = spec_models_from_document(
        {"tests": [entry]}, base_dir=tmp_path, label="spec"
    )
    assert (test.stop_time, test.tolerance, test.method) == (5.0, 1e-6, "Dassl")
    assert test.number_of_intervals == 500 and isinstance(test.number_of_intervals, int)
    assert test.output_interval is None and test.timeout == 120
    assert "output_interval" not in test.field_sources


def test_no_shipped_spec_is_stopped():
    repo = Path(__file__).parent.parent
    specs = sorted((repo / "examples").rglob("test_spec*.json"))
    assert len(specs) >= 4
    for path in specs:
        data = read_spec_document(path)
        assert data is not None
        spec_models_from_document(data, base_dir=path.parent, label=str(path))
