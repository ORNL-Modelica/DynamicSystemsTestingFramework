"""A test marked skip, for every backend or for one, and a time limit for one
backend: the ``skip`` and ``backends`` keys of a ``test_spec.json`` entry,
how they are refused, and the one resolver every reader asks
(``discovery.marks``).

Most specific wins: a backend and OS (``"OpenModelica/windows"``), the
backend (``"OpenModelica"``), the entry (``skip``; ``simulation.timeout``),
then the run (``--timeout``).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from dstf.cli import main
from dstf.config import Config, ConfigError
from dstf.discovery.marks import SkipMark, skip_reason, timeout_for
from dstf.discovery.spec_parser import spec_models_from_document
from dstf.discovery.test_registry import TestModel, merge_tests

MODEL = "PythonTestingLib.Examples.SimpleRamp"
BACKENDS = "Dymola, OpenModelica, FMPy, Julia, Python"


def _entry(**keys) -> dict:
    return {"model": MODEL, "variables": ["x"], **keys}


def _parse(entry: dict) -> TestModel:
    (test,) = spec_models_from_document(
        {"tests": [entry]}, base_dir=Path("."), label="test_spec.json"
    )
    return test


def _refusal(entry: dict) -> str:
    with pytest.raises(ConfigError) as caught:
        _parse(entry)
    return str(caught.value)


# -- the format: what is refused -------------------------------------------------


def test_a_backend_name_that_is_no_backend_is_refused_with_the_backends_listed():
    said = _refusal(_entry(backends={"OpenModelca": {"skip": "slow"}}))
    assert said == (
        f"test_spec.json: tests[{MODEL}].backends.OpenModelca: no backend has "
        "this name. A key here is a backend's name as the reference folders "
        "spell it (Dymola, not a testing.json entry's name like \"Dymola "
        '2025"), alone or with an operating system after a / '
        f"(Dymola/linux); the backends are: {BACKENDS}"
    )


def test_a_testing_json_entry_name_is_not_a_backend_key():
    said = _refusal(_entry(backends={"OpenModelica 1.23": {"skip": "slow"}}))
    assert said.startswith(
        f"test_spec.json: tests[{MODEL}].backends.OpenModelica 1.23: no backend "
        "has this name."
    )


def test_an_operating_system_that_is_none_is_refused_with_the_systems_listed():
    said = _refusal(_entry(backends={"OpenModelica/win": {"skip": "slow"}}))
    assert said == (
        f'test_spec.json: tests[{MODEL}].backends.OpenModelica/win: "win" is '
        "no operating system; after the backend's name and a / a key names "
        "one of: linux, windows, macos"
    )


def test_an_empty_reason_is_refused_on_the_entry():
    said = _refusal(_entry(skip=""))
    assert said == (
        f'test_spec.json: tests[{MODEL}].skip: must be a non-empty string, got ""'
    )


@pytest.mark.parametrize(
    ("backends", "where", "problem"),
    [
        (
            {"Python": {"skip": ""}},
            "backends.Python.skip",
            'must be a non-empty string, got ""',
        ),
        # No `false` that clears a mark: absent is the only "not marked".
        (
            {"Python": {"skip": False}},
            "backends.Python.skip",
            "must be a non-empty string, got false",
        ),
        (
            {"Python": {"timeout": "60"}},
            "backends.Python.timeout",
            'must be a whole number, got "60"',
        ),
        (
            {"Python": {"timeout": 1.5}},
            "backends.Python.timeout",
            "must be a whole number, got 1.5",
        ),
        (
            {"Python": {"timeout": None}},
            "backends.Python.timeout",
            "must be a whole number, got null",
        ),
        ({"Python": "slow"}, "backends.Python", 'must be an object, got "slow"'),
        (
            {"Python": {"skp": "slow"}},
            "backends.Python",
            'unknown field "skp" — did you mean "skip"?',
        ),
    ],
)
def test_a_backend_block_holds_a_reason_and_a_whole_number_of_seconds(
    backends, where, problem
):
    assert _refusal(_entry(backends=backends)) == (
        f"test_spec.json: tests[{MODEL}].{where}: {problem}"
    )


@pytest.mark.parametrize("value", [False, None, 3, ["slow"]])
def test_an_entry_skip_that_is_no_reason_is_refused(value):
    said = _refusal(_entry(skip=value))
    assert said == (
        f"test_spec.json: tests[{MODEL}].skip: must be a non-empty string, "
        f"got {json.dumps(value)}"
    )


def test_backends_that_is_no_object_is_refused():
    assert _refusal(_entry(backends=["Python"])) == (
        f'test_spec.json: tests[{MODEL}].backends: must be an object, got ["Python"]'
    )


def test_every_backend_and_every_system_is_a_key():
    names = BACKENDS.split(", ")
    keys = [
        *names,
        *(f"{n}/{os}" for n in names for os in ("linux", "windows", "macos")),
    ]
    test = _parse(_entry(backends={key: {"skip": key} for key in keys}))
    assert sorted(test.backend_marks) == sorted(keys)


REFUSED = [
    {"skip": ""},
    {"backends": {"OpenModelca": {}}},
    {"backends": {"OpenModelica 1.23": {}}},
    {"backends": {"OpenModelica/win": {}}},
    {"backends": {"Python": {"skp": "slow"}}},
    {"backends": {"Python": {"skip": False}}},
    {"backends": {"Python": {"timeout": 1.5}}},
]


def test_the_published_schema_takes_what_the_parser_takes():
    jsonschema = pytest.importorskip("jsonschema")
    from dstf.reporting.schema_export import build_schema

    validator = jsonschema.Draft202012Validator(build_schema())
    taken = _entry(
        skip="slow",
        backends={
            "OpenModelica": {"skip": "slow", "timeout": 600},
            "Python/macos": {"timeout": 7},
        },
    )
    assert validator.is_valid({"tests": [taken]})
    _parse(taken)
    for keys in REFUSED:
        assert not validator.is_valid({"tests": [_entry(**keys)]}), keys
        _refusal(_entry(**keys))


# -- the resolver ----------------------------------------------------------------


@pytest.fixture
def config_of(tmp_path):
    """A Config of ``backend`` on ``os_name``, with the run's ``--timeout``."""
    lib = tmp_path / "Lib"
    lib.mkdir()
    (lib / "package.mo").write_text("package Lib end Lib;")

    def make(backend: str, os_name: str, timeout: int = 45) -> Config:
        return Config(
            source_path=lib,
            reference_root=tmp_path / "refs",
            backend_name=backend,
            os_name=os_name,
            work_dir=tmp_path / "work",
            timeout=timeout,
        )

    return make


MARKED = _entry(
    skip="fails everywhere",
    simulation={"timeout": 120},
    backends={
        "OpenModelica": {"skip": "omc is slow", "timeout": 600},
        "OpenModelica/windows": {"skip": "no compiler on windows"},
        "Dymola/linux": {"timeout": 900},
    },
)


@pytest.mark.parametrize(
    ("backend", "os_name", "mark", "seconds"),
    [
        # The backend and OS's reason; its time limit is the backend's.
        (
            "OpenModelica",
            "windows",
            SkipMark("OpenModelica/windows", "no compiler on windows"),
            600,
        ),
        ("OpenModelica", "linux", SkipMark("OpenModelica", "omc is slow"), 600),
        # Dymola/linux says only a time limit: the entry's reason stands.
        ("Dymola", "linux", SkipMark("every backend", "fails everywhere"), 900),
        ("Dymola", "windows", SkipMark("every backend", "fails everywhere"), 120),
    ],
)
def test_the_most_specific_mark_wins(config_of, backend, os_name, mark, seconds):
    test = _parse(MARKED)
    config = config_of(backend, os_name)
    assert skip_reason(test, config) == mark
    assert timeout_for(test, config) == seconds


def test_a_test_with_no_mark_runs_for_the_run_s_time_limit(config_of):
    test = _parse(_entry())
    config = config_of("OpenModelica", "linux", timeout=45)
    assert skip_reason(test, config) is None
    assert timeout_for(test, config) == 45


def test_a_backend_time_limit_wins_over_the_run_s(config_of):
    test = _parse(_entry(backends={"Python": {"timeout": 7}}))
    assert timeout_for(test, config_of("Python", "linux", timeout=45)) == 7
    # ... on that backend only.
    assert timeout_for(test, config_of("OpenModelica", "linux", timeout=45)) == 45


def test_a_whole_number_written_as_a_float_is_read_as_seconds(config_of):
    test = _parse(_entry(backends={"Python": {"timeout": 7.0}}))
    seconds = timeout_for(test, config_of("Python", "linux"))
    assert seconds == 7
    assert isinstance(seconds, int)


def test_the_merge_keeps_the_marks_of_a_test_a_recognizer_also_found(config_of):
    scanned = TestModel(model_id=MODEL, source_file=None, n_vars=1)
    spec = _parse(MARKED)
    (merged,) = merge_tests({MODEL: scanned}, {MODEL: spec})
    config = config_of("OpenModelica", "windows")
    assert skip_reason(merged, config) == SkipMark(
        "OpenModelica/windows", "no compiler on windows"
    )
    assert timeout_for(merged, config) == 600


# -- the time limit, applied by a real run --------------------------------------


def _sleepy_project(tmp_path: Path, backends: dict) -> Path:
    """A Python-backend project of one test whose ``simulate`` sleeps 3 s,
    with ``simulation.timeout`` 60."""
    lib = tmp_path / "Sleepy"
    lib.mkdir()
    (lib / "Slow.py").write_text(
        "import time\n"
        "def simulate(stop_time, tolerance):\n"
        "    time.sleep(3)\n"
        "    return {'time': [0.0, 1.0], 'variables': {'x': [0.0, 1.0]}}\n"
    )
    (lib / "test_spec.json").write_text(
        json.dumps(
            {
                "tests": [
                    {
                        "model": "Sleepy.Slow",
                        "source": "Slow.py",
                        "variables": ["x"],
                        "simulation": {"stop_time": 1.0, "timeout": 60},
                        "backends": backends,
                    }
                ]
            }
        )
    )
    (lib / "testing.json").write_text(
        json.dumps(
            {
                "source_type": "python",
                "source_path": ".",
                "library_name": "Sleepy",
                "test_spec": "./test_spec.json",
                "reference_root": "./refs",
                "backend": "Python",
                "backends": {"Python": {"executables": [sys.executable]}},
            }
        )
    )
    return lib / "testing.json"


def test_a_backend_time_limit_stops_a_run_its_entry_would_let_finish(tmp_path, capsys):
    """``simulation.timeout`` 60 and ``backends.Python.timeout`` 1: the run is
    stopped after 1 s, not 3 — the backend's limit is the one applied."""
    config = _sleepy_project(tmp_path, {"Python": {"timeout": 1}})
    work = tmp_path / "work"
    argv = ["run", "--config", str(config), "--no-browser", "--work-dir", str(work)]
    assert main(argv) == 1

    err = capsys.readouterr().err
    (line,) = [ln for ln in err.splitlines() if "Sleepy.Slow" in ln and "%)" in ln]
    assert " -> timeout (1." in line
    verdicts = json.loads((work / "verdicts.json").read_text(encoding="utf-8"))
    (entry,) = verdicts["tests"]
    assert entry["status"] == "SIM_FAIL"
    assert entry["message"].startswith("Python execution exceeded 1.0s timeout (")
