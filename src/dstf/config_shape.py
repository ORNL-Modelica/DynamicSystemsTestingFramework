"""The shape of ``testing.json``: its keys, and for each its type, its
default and what it is for.

ONE table, read three times. ``Config`` refuses a key that is not in it —
``"tolerence": 0.01`` used to be skipped in silence, and the project ran on
the default — and takes its defaults from it; ``spec schema`` publishes
it, descriptions included. So the keys a person reads about, the ones an
editor checks and the ones the tool reads cannot differ.

A backend declares its own settings in the same shape (:class:`Setting`,
``Backend.settings``): the block ``backends.<name>`` is checked against the
table of the backend that entry names, by the check a top-level key goes
through (:func:`refused`).
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .discovery.spec_shape import NOTES
from .json_types import holds, said


@dataclass(frozen=True)
class Setting:
    """One key: the JSON-Schema fragment its value must be (draft 2020-12),
    its default — ``None`` where there is none to write down: the key is
    optional and the description says what happens without it — and a
    description written for the person who sets it."""

    schema: Mapping[str, Any]
    default: Any
    description: str
    #: The value has a rule of its own that ``Config`` holds it to, with a
    #: sentence of its own — a base tolerance, a theme's name. The type
    #: check here leaves it to that one.
    own_rule: bool = False
    #: A backend setting whose value — a string, or each string of a list —
    #: is a path: :data:`FILE` or :data:`PROGRAM`. ``Config`` hands the
    #: backend an absolute one (``backends.locate.anchored``).
    path: str = ""


#: :attr:`Setting.path`: a path. A relative one is relative to the
#: directory of the ``testing.json`` it is written in, as ``source_path``
#: is, and ``~`` is the home directory.
FILE = "file"
#: :attr:`Setting.path`: a path as :data:`FILE` — or, with no separator in
#: it, a program's name, which is looked up on ``PATH``.
PROGRAM = "program"


def _note(key: str) -> Setting:
    return Setting(
        NOTES[key],
        None,
        "A note for people. Nothing reads it.",
    )


_STRINGS = {"type": "array", "items": {"type": "string"}}

#: What every backend's settings block may hold, whatever the backend.
EXECUTABLES = Setting(
    {"type": "array", "items": {"type": "string", "minLength": 1}},
    [],
    "Where this backend's program is, in order of preference: paths, or "
    "program names looked up on PATH. An entry with a / or a \\ in it is a "
    "path: a relative one is relative to this file, and ~ is your home "
    "directory; any other entry is a program name. The first that is a "
    "file that can be executed is used. If any entry is a path and none "
    "of them is one, the backend is not found — no other install is "
    "searched for. Default: none; the backend is searched for.",
    path=PROGRAM,
)

#: Every key of ``testing.json``, in the order a reference page lists them.
TESTING_JSON: dict[str, Setting] = {
    "$schema": Setting(
        {"type": "string"},
        None,
        "The JSON Schema an editor checks this file against as it is typed "
        "(`dstf spec schema` writes one). Nothing else reads it.",
    ),
    "source_path": Setting(
        {"type": "string"},
        None,
        "The library under test, relative to this file: for a Modelica "
        "library the directory that holds its package.mo; otherwise the "
        "directory of the FMUs, the Julia project or the Python tests. "
        "Default: a Modelica library is looked for from the directory the "
        "command is run in; any other source is this file's directory.",
    ),
    "source_type": Setting(
        {"type": "string"},
        "modelica",
        'What source_path holds: "modelica", "fmu", "julia" or "python". '
        "Only a Modelica library is scanned for tests (its .mo files); the "
        'others take every test from test_spec.json. Default: "modelica".',
    ),
    "library_name": Setting(
        {"type": "string"},
        None,
        "The library's name in reports and in the default work_dir. A "
        "Modelica library is always named by its package.mo; for any other "
        "source the default is the name of this file's directory.",
    ),
    "backend": Setting(
        {"type": "string"},
        None,
        "The simulator to run with: an entry of backends, or a backend's "
        "name. A name that starts with a backend's is that backend — \"Dymola "
        '2025" is a Dymola. Default: the first entry of backends that is '
        "found on this machine; with no backends, the first installed "
        "backend that runs this source_type.",
    ),
    "backends": Setting(
        {"type": "object"},
        {},
        "The simulators this project runs with, in order of preference: one "
        'settings block per entry, {"<name>": {"executables": [...]}}. Each '
        "block takes executables and that backend's own settings; an empty "
        "block {} means the backend is searched for. Default: none.",
    ),
    "backend_setup": Setting(
        _STRINGS,
        [],
        "Commands run in the simulator once its libraries are loaded and "
        "before any test, in the simulator's own scripting language. "
        "Default: none.",
    ),
    "dependencies": Setting(
        _STRINGS,
        [],
        "Libraries loaded before the library under test: a library's name "
        '("Modelica") or a path relative to this file — a library\'s '
        "directory, its package.mo, or a one-file library Name.mo. "
        "Default: none.",
    ),
    "reference_root": Setting(
        {"type": "string"},
        None,
        "Where the stored baselines are, relative to this file. They are "
        "kept below it by backend and operating system, <Backend>/<os>/. "
        "Default: this file's directory if it is named ReferenceResults, "
        "else Resources/ReferenceResults beside the library.",
    ),
    "test_spec": Setting(
        {"type": "string"},
        None,
        "The file of test definitions and criteria, relative to this file. "
        "Default: test_spec.json in reference_root.",
    ),
    "work_dir": Setting(
        {"type": "string"},
        None,
        "Where a run writes its results and reports; a relative path is "
        "relative to this file. Default: "
        "testing_output/<library>/<backend>/<os>/ under the directory the "
        "command is run in.",
    ),
    "os": Setting(
        {"type": "string"},
        None,
        "The operating-system part of the baselines' folder, "
        '<reference_root>/<Backend>/<os>/: "linux", "windows" or "macos". '
        "Default: the system the tool runs on.",
    ),
    "tolerance": Setting(
        {"type": ["number", "null"]},
        1e-4,
        "The base comparison tolerance: what a variable is compared with "
        "when neither its test nor its stored baseline sets one. One finite "
        "number. Default: 0.0001.",
        own_rule=True,
    ),
    "baseline_points": Setting(
        {"oneOf": [{"const": "all"}, {"type": "integer", "minimum": 2}]},
        "all",
        "The points a stored baseline keeps of its run: "
        '"all" — every point the simulator wrote — or a whole number of 2 '
        "or more, which thins a longer run to at most that many. A test is "
        "compared at its baseline's points, so this is also the resolution "
        "of the comparison. A test_spec.json entry's own baseline_points "
        'wins for that test. Default: "all".',
        own_rule=True,
    ),
    "worker_startup_timeout": Setting(
        {"type": ["number", "null"], "exclusiveMinimum": 0},
        None,
        "Seconds a simulator that stays open between tests may take to "
        "start before the run fails. The bound is for a simulator that "
        "hangs; one that cannot start fails at once. Default: 600.",
        own_rule=True,
    ),
    "max_embedded_samples": Setting(
        {"type": "integer"},
        1000,
        "The most points per variable a report page's plot holds; a longer "
        "trajectory is thinned for the plot only — scoring and the stored "
        "baselines are untouched. Below 3, nothing is thinned. Default: 1000.",
    ),
    "diagnostic_variables": Setting(
        _STRINGS,
        ["CPUtime", "EventCounter"],
        "Variables recorded from every simulation and shown in the reports, "
        'but not compared. Default: ["CPUtime", "EventCounter"].',
    ),
    "theme": Setting(
        {"type": "string"},
        "default",
        "The colour theme of the dashboard and the report pages, and the "
        'one `dstf author` opens in: "default" or "dark". Default: '
        '"default".',
        own_rule=True,
    ),
    "recognizers": Setting(
        {"type": "array", "items": {"type": "object"}},
        [],
        "Rules of your own that find tests in the library's source files, "
        "added to the bundled ones: each an object with name, applies_to, "
        "match and fields. Default: none.",
    ),
    "disable_bundled": Setting(
        _STRINGS,
        [],
        "Names of bundled recognizers to switch off, for example "
        '"modelica:bundled-unit-tests". Default: none.',
    ),
    **{key: _note(key) for key in NOTES},
}


def default(key: str, table: Mapping[str, Setting] = TESTING_JSON) -> Any:
    """``key``'s default as ``table`` holds it — a copy, so a list handed to
    one ``Config`` is not another's."""
    return copy.deepcopy(table[key].default)


def defaults(table: Mapping[str, Setting]) -> dict[str, Any]:
    """Every key of ``table`` at its default."""
    return {key: default(key, table) for key in table}


def refused(
    where: str, block: Mapping[str, Any], table: Mapping[str, Setting]
) -> str | None:
    """What to say about the first key of ``block`` that ``table`` does not
    hold, or whose value is not of its type — or None. ``where`` names the
    block (``backends.Dymola``; empty for the file's own keys):

    - ``backends.Dymola: unknown field "show_ui" — did you mean
      "show_ide"?`` — the words a ``test_spec.json`` key is refused with
      (:func:`~dstf.comparison.strict_keys.unknown_field`);
    - ``backends.Dymola.show_ide: must be true or false, got "yes"``.

    A key with a rule of its own (:attr:`Setting.own_rule`) is left to it.
    """
    # Here, not at the top: `comparison` imports the backends, which import
    # `config`, which imports this module.
    from .comparison.sides import _as_json
    from .comparison.strict_keys import unknown_field

    if block and not table:
        return (
            f'{where}: unknown field "{next(iter(block))}" — there are no settings here'
        )
    problem = unknown_field(block, table)
    if problem is not None:
        return f"{where}: {problem}" if where else problem
    for key, value in block.items():
        setting = table[key]
        if setting.own_rule or holds(setting.schema, value):
            continue
        named = f"{where}.{key}" if where else key
        return f"{named}: must be {said(setting.schema)}, got {_as_json(value)}"
    return None


def schema_properties(table: Mapping[str, Setting]) -> dict[str, Any]:
    """``table`` as a JSON-Schema ``properties`` object: each key's
    fragment, with its description and — where it has one — its default."""
    properties: dict[str, Any] = {}
    for key, setting in table.items():
        node = {
            **copy.deepcopy(dict(setting.schema)),
            "description": setting.description,
        }
        if setting.default is not None:
            node["default"] = copy.deepcopy(setting.default)
        properties[key] = node
    return properties
