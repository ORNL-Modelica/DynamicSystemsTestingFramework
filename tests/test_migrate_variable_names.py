"""`dstf migrate-variable-names` — the seam change's coordination.

Stored baselines and spec keys move from a UnitTests variable's expression
text to its result-file name. The command is dry-run by default, rewrites a
file whole or not at all, and says what it could not do.
"""

import json
from pathlib import Path

import pytest

from dstf.config import Config
from dstf.storage.migrate_names import (
    EXPRESSION_ADDED,
    MIGRATED,
    RENAMED,
    SKIPPED,
    UNKNOWN_MODEL,
    migrate,
)

MODELS = {
    # one expression per variable; the second wrapped over two lines in the
    # source, which is how 210 of TRANSFORM's stored names look
    "Plain": "  Real a; Real b[2,2];\n  UnitTests unitTests(n=2, x={a, b[\n      1, 1]});\n",
    # one expression covering several variables, under its own instance name
    "Cat": "  Real y[2];\n  UnitTests ut(n=2, x=cat(1, y,\n   y));\n",
    # the same expression on two indices
    "Twice": "  Real a;\n  UnitTests unitTests(n=2, x={a, a});\n",
}
#: What each model's baseline was stored under before the identity change.
OLD_NAMES = {
    "Plain": ["a", "b[\n      1, 1]"],
    "Cat": ["cat(1, y,\n   y)[1]", "cat(1, y,\n   y)[2]"],
    "Twice": ["a", "a"],
}
NEW_PLAIN = [("unitTests.x[1]", "a"), ("unitTests.x[2]", "b[1, 1]")]


def _ref(model, names, test_id, extra=None):
    return {
        "model_id": f"MyLib.{model}",
        "test_id": test_id,
        "status": "active",
        "time": [0.0, 1.0],
        "variables": [
            {"index": i, "name": n, "values": [0.1 * i, 1.0 / 3]}
            for i, n in enumerate(names, start=1)
        ],
        "statistics": {"CPUtime": 0.123456789},
        **(extra or {}),
    }


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n")


@pytest.fixture
def lib(tmp_path):
    (tmp_path / "package.mo").write_text("package MyLib\nend MyLib;\n")
    for name, body in MODELS.items():
        (tmp_path / f"{name}.mo").write_text(
            f"within MyLib;\nmodel {name}\n{body}"
            f"  annotation(experiment(StopTime=1));\nend {name};\n"
        )
    spec = {
        "tests": [
            {
                "model": "MyLib.Plain",
                "comparison": {
                    "variable_overrides": {
                        "b[1, 1]": {"tolerance": 0.5},
                        "other": {"tolerance": 1},
                    }
                },
                "metrics": {
                    "combinator": "and",
                    "children": [{"metric": "nrmse", "variable": "a"}],
                },
                "plots": [
                    {"kind": "time", "series": [{"var": "a"}, {"var": "b[2,2]"}]}
                ],
            },
            {
                "model": "MyLib.Twice",
                "comparison": {"variable_overrides": {"a": {"tolerance": 0.5}}},
            },
        ]
    }
    _write(tmp_path / "test_spec.json", spec)
    (tmp_path / "testing.json").write_text(
        json.dumps(
            {
                "source_path": ".",
                "backend": "Dymola",
                "backends": {},
                "dependencies": [],
                "test_spec": "test_spec.json",
                "reference_root": "refs",
            }
        )
    )
    part = tmp_path / "refs" / "Dymola" / "linux"
    (part / "soft_checks" / "ref_0001").mkdir(parents=True)
    (part / "companions" / "ref_0001").mkdir(parents=True)
    other = tmp_path / "refs" / "OpenModelica" / "linux"
    other.mkdir(parents=True)
    files = {
        part / "ref_0001.json": _ref(
            "Plain",
            OLD_NAMES["Plain"],
            "0001",
            {"comparison": {"variable_overrides": {"a": {"tolerance": 0.2}}}},
        ),
        part / "ref_0002.json": _ref("Cat", OLD_NAMES["Cat"], "0002"),
        part / "ref_0003.json": _ref("Gone", ["q"], "0003"),
        other / "ref_0001.json": _ref("Plain", OLD_NAMES["Plain"], "0001"),
    }
    soft = _ref("Plain", OLD_NAMES["Plain"], "0001")
    del soft["model_id"]  # a soft-check file names no model: its primary does
    files[part / "soft_checks" / "ref_0001" / "om.json"] = soft
    # A companion is another source's data, under that source's own names.
    files[part / "companions" / "ref_0001" / "julia.json"] = {
        "time": [0.0, 1.0],
        "variables": [{"index": 1, "name": "a", "values": [0.0, 1.0]}],
    }
    for path, data in files.items():
        _write(path, data)
    return tmp_path


def _cfg(lib):
    return Config(config_file=str(lib / "testing.json"))


def _snapshot(root: Path):
    return {
        str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in root.rglob("*.json")
    }


def _names(path: Path):
    return [
        (v["name"], v["expression"]) for v in json.loads(path.read_text())["variables"]
    ]


def _status(report, path):
    return [f.status for f in report.files if f.path == path]


# ---------------------------------------------------------------------------
# Reference files
# ---------------------------------------------------------------------------


def test_dry_run_writes_nothing(lib):
    """Bytes AND mtime: a rewrite that lands identical bytes is still a
    write the user did not ask for."""
    before = _snapshot(lib)
    report = migrate(_cfg(lib), apply=False)
    assert _snapshot(lib) == before
    assert sum(f.status == RENAMED for f in report.files) == 4


def test_apply_renames_every_partition_primary_and_soft_check(lib):
    """Every ``<Backend>/<os>/`` partition, whatever backend this run is
    configured for — a tree is migrated once, not once per backend."""
    migrate(_cfg(lib), apply=True)
    for rel in (
        "Dymola/linux/ref_0001.json",
        "OpenModelica/linux/ref_0001.json",
        "Dymola/linux/soft_checks/ref_0001/om.json",
    ):
        assert _names(lib / "refs" / rel) == NEW_PLAIN
    assert _names(lib / "refs/Dymola/linux/ref_0002.json") == [
        ("ut.x[1]", "cat(1, y, y)[1]"),
        ("ut.x[2]", "cat(1, y, y)[2]"),
    ]


def test_a_stored_name_is_matched_in_its_normal_form(lib):
    """``b[\\n      1, 1]`` is the same expression as ``b[1, 1]``: the
    source's line wrap is not part of what the name says. Matched raw, every
    wrapped name in the tree would be reported as unrecognized."""
    report = migrate(_cfg(lib), apply=True)
    assert report.clean is False  # only the Twice spec key and the Gone file
    assert not [f for f in report.files if f.status == SKIPPED]
    assert _names(lib / "refs/Dymola/linux/ref_0001.json")[1] == (
        "unitTests.x[2]",
        "b[1, 1]",
    )


def test_a_companion_is_not_touched(lib):
    """A companion is another source's data, keyed by the names that source
    gives its own quantities."""
    path = lib / "refs/Dymola/linux/companions/ref_0001/julia.json"
    before = path.read_bytes()
    report = migrate(_cfg(lib), apply=True)
    assert path.read_bytes() == before
    assert _status(report, path) == []


def test_everything_else_is_unchanged_and_expression_follows_name(lib):
    path = lib / "refs/Dymola/linux/ref_0001.json"
    before = json.loads(path.read_text())
    migrate(_cfg(lib), apply=True)
    text = path.read_text()
    after = json.loads(text)
    assert "\r" not in text and text.endswith("}\n")
    assert list(after["variables"][0]) == ["index", "name", "expression", "values"]

    def strip(d):
        return {
            **{k: v for k, v in d.items() if k not in ("variables", "comparison")},
            "variables": [
                {k: v for k, v in e.items() if k not in ("name", "expression")}
                for e in d["variables"]
            ],
        }

    assert strip(after) == strip(before) and list(after) == list(before)
    assert after["time"] == before["time"]
    assert [v["values"] for v in after["variables"]] == [
        v["values"] for v in before["variables"]
    ]


def test_the_baselines_own_override_keys_move_too(lib):
    """The flat path merges the baseline's own ``variable_overrides`` under
    the spec's. Left un-renamed they silently stop applying."""
    migrate(_cfg(lib), apply=True)
    ref = json.loads((lib / "refs/Dymola/linux/ref_0001.json").read_text())
    assert ref["comparison"]["variable_overrides"] == {
        "unitTests.x[1]": {"tolerance": 0.2}
    }


def test_second_apply_rewrites_nothing(lib):
    migrate(_cfg(lib), apply=True)
    before = _snapshot(lib)
    report = migrate(_cfg(lib), apply=True)
    assert _snapshot(lib) == before
    assert not [f for f in report.files if f.status in (RENAMED, EXPRESSION_ADDED)]
    assert report.spec_rewritten == []


def test_an_already_named_file_that_only_gains_the_field_is_reported_apart(lib):
    """A file already under result-file names, written before the writer
    always wrote ``expression``: it gains the field and nothing else, which
    is a different event from a rename and is counted as one."""
    path = lib / "refs/Dymola/linux/ref_0001.json"
    data = _ref("Plain", [n for n, _ in NEW_PLAIN], "0001")
    data["variables"].append({"index": 3, "name": "other.var", "values": [0.0, 1.0]})
    _write(path, data)
    report = migrate(_cfg(lib), apply=True)
    assert _status(report, path) == [EXPRESSION_ADDED]
    assert _names(path) == [*NEW_PLAIN, ("other.var", "")]


def test_an_expression_that_differs_is_reported_and_left_as_stored(lib):
    """The entry is already migrated and the model's ``x={...}`` has moved
    since. Which quantity the baseline holds is the compare-time expression
    warning's business; rewriting the label here would erase the evidence.
    Reported, and not a failure."""
    path = lib / "refs/Dymola/linux/ref_0001.json"
    data = _ref("Plain", [n for n, _ in NEW_PLAIN], "0001")
    data["variables"] = [
        {"index": 1, "name": "unitTests.x[1]", "expression": "old.thing", "values": []},
        {"index": 2, "name": "unitTests.x[2]", "expression": "b[1, 1]", "values": []},
    ]
    _write(path, data)
    report = migrate(_cfg(lib), apply=True)
    assert _status(report, path) == [MIGRATED]
    assert _names(path)[0] == ("unitTests.x[1]", "old.thing")
    assert any(
        "old.thing" in line and "MyLib.Plain" in line
        for line in report.expression_differs
    )


def test_a_partially_matching_file_is_reported_and_left_whole(lib):
    """All or nothing: half a file's variables renamed is a baseline whose
    entries no longer agree with each other."""
    path = lib / "refs/Dymola/linux/ref_0001.json"
    data = json.loads(path.read_text())
    data["variables"][1]["name"] = "something.else"
    _write(path, data)
    before = path.read_bytes()
    report = migrate(_cfg(lib), apply=True)
    assert path.read_bytes() == before
    (bad,) = [f for f in report.files if f.path == path]
    assert bad.status == SKIPPED
    assert "something.else" in bad.reasons[0] and "b[1, 1]" in bad.reasons[0]
    assert "index 2" in bad.reasons[0] and "unitTests.x[2]" in bad.reasons[0]
    assert not report.clean


def test_an_unknown_model_is_listed_and_untouched(lib):
    path = lib / "refs/Dymola/linux/ref_0003.json"
    before = path.read_bytes()
    report = migrate(_cfg(lib), apply=True)
    assert path.read_bytes() == before
    assert _status(report, path) == [UNKNOWN_MODEL]
    assert not report.clean


# ---------------------------------------------------------------------------
# test_spec.json
# ---------------------------------------------------------------------------


def test_spec_keys_that_name_an_expression_become_the_name(lib):
    report = migrate(_cfg(lib), apply=True)
    spec = json.loads((lib / "test_spec.json").read_text())
    plain, twice = spec["tests"]
    assert plain["comparison"]["variable_overrides"] == {
        "unitTests.x[2]": {"tolerance": 0.5},
        "other": {"tolerance": 1},
    }
    assert plain["metrics"]["children"][0]["variable"] == "unitTests.x[1]"
    assert [s["var"] for s in plain["plots"][0]["series"]] == [
        "unitTests.x[1]",
        "b[2,2]",
    ]
    assert ("MyLib.Plain", "a", "unitTests.x[1]") in report.spec_rewritten
    # An expression two indices share: which one the key meant is the user's
    # call, so it is reported and left.
    assert twice["comparison"]["variable_overrides"] == {"a": {"tolerance": 0.5}}
    assert any("MyLib.Twice" in line for line in report.spec_skipped)
    assert not report.clean


def test_a_pattern_tracked_key_is_not_touched(lib):
    """``a`` is ALSO tracked by a spec pattern, so it names a variable of its
    own in the result file. The key stays."""
    spec_path = lib / "test_spec.json"
    spec = json.loads(spec_path.read_text())
    spec["tests"][0]["variables"] = ["a"]
    _write(spec_path, spec)
    migrate(_cfg(lib), apply=True)
    plain = json.loads(spec_path.read_text())["tests"][0]
    assert plain["metrics"]["children"][0]["variable"] == "a"
    assert [s["var"] for s in plain["plots"][0]["series"]] == ["a", "b[2,2]"]


def test_a_variable_that_loses_its_baseline_entry_is_listed_for_re_accept(lib):
    """Before the change the UnitTests variable and a spec pattern naming
    the same text were ONE stored entry. After it they are two variables and
    the second has no data. Nothing can be invented for it — it is listed,
    the file still migrates, and the exit code does not move."""
    spec_path = lib / "test_spec.json"
    spec = json.loads(spec_path.read_text())
    spec["tests"][0]["variables"] = ["a"]
    _write(spec_path, spec)
    report = migrate(_cfg(lib), apply=True)
    assert ("MyLib.Plain", "a") in report.reaccept
    assert _status(report, lib / "refs/Dymola/linux/ref_0001.json") == [RENAMED]


def test_a_spec_with_nothing_to_rewrite_is_not_rewritten(lib):
    """The write re-serializes the whole file, so it happens only when a key
    actually moved."""
    spec_path = lib / "test_spec.json"
    _write(spec_path, {"tests": [{"model": "MyLib.Cat"}]})
    before = (spec_path.read_bytes(), spec_path.stat().st_mtime_ns)
    migrate(_cfg(lib), apply=True)
    assert (spec_path.read_bytes(), spec_path.stat().st_mtime_ns) == before


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------


def test_cli_exit_codes_and_default_dry_run(lib, capsys):
    from dstf.cli.main import main

    before = _snapshot(lib)
    argv = ["--config", str(lib / "testing.json"), "migrate-variable-names"]
    assert main(argv) == 1  # the Gone file and the Twice spec key
    assert _snapshot(lib) == before
    out = capsys.readouterr().out
    assert "dry run" in out
    assert "Dymola/linux: 3 would rewrite" in out
    assert "OpenModelica/linux: 1 would rewrite" in out
    assert "unknown-model" in out and "ref_0003.json" in out
    assert "re-serialized" in out

    (lib / "refs/Dymola/linux/ref_0003.json").unlink()
    spec = json.loads((lib / "test_spec.json").read_text())
    spec["tests"].pop()
    _write(lib / "test_spec.json", spec)
    assert main([*argv, "--apply"]) == 0
    assert "rewritten" in capsys.readouterr().out
    assert _names(lib / "refs/Dymola/linux/ref_0001.json") == NEW_PLAIN
