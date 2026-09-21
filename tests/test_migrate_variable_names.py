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


def _reasons(report, path):
    return "; ".join(r for f in report.files if f.path == path for r in f.reasons)


def _nothing_left_undone(lib):
    """The fixture with BOTH of its standing reasons removed — the Gone file
    and the Twice spec key.

    ``clean`` has three independent terms and the fixture trips two of them
    at once, so ``not clean`` holds however many of the three a mutant
    deletes. One reason at a time is the only way each term is its own test.
    """
    (lib / "refs/Dymola/linux/ref_0003.json").unlink()
    spec = json.loads((lib / "test_spec.json").read_text())
    spec["tests"].pop()
    _write(lib / "test_spec.json", spec)


def _exit_code(lib, *flags):
    from dstf.cli.main import main

    return main(
        ["--config", str(lib / "testing.json"), "migrate-variable-names", *flags]
    )


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
    """Idempotent, and idempotent the right way round: every file it
    rewrote reads back as MIGRATED. "Nothing rewritten" alone also holds
    when the second run cannot recognize its own output and skips it."""
    migrate(_cfg(lib), apply=True)
    before = _snapshot(lib)
    report = migrate(_cfg(lib), apply=True)
    assert _snapshot(lib) == before
    assert {f.status for f in report.files} == {MIGRATED, UNKNOWN_MODEL}
    assert report.spec_rewritten == []


def test_a_second_run_over_a_non_default_instance_reads_as_migrated(lib):
    """``MyLib.Cat``'s component is ``ut``, not ``unitTests``. An
    already-migrated check that spells the default instance calls
    ``ut.x[1]`` unrecognized and skips the file it wrote itself."""
    migrate(_cfg(lib), apply=True)
    report = migrate(_cfg(lib), apply=True)
    assert _status(report, lib / "refs/Dymola/linux/ref_0002.json") == [MIGRATED]


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
    # 'a' is reached twice — the metrics leaf and the plot series — and is
    # ONE decision a reader checks, carrying its occurrence count.
    assert ("MyLib.Plain", "a", "unitTests.x[1]", 2) in report.spec_rewritten
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


# ---------------------------------------------------------------------------
# The exit code, one term at a time
# ---------------------------------------------------------------------------


def test_a_tree_with_nothing_left_undone_is_clean(lib):
    """The state in which every guard below is silent. Without it `not
    clean` is true for two reasons at once and no single term is tested."""
    report = migrate(_cfg(lib), apply=False)
    assert report.clean is False
    _nothing_left_undone(lib)
    report = migrate(_cfg(lib), apply=False)
    assert report.clean is True
    assert _exit_code(lib) == 0


def test_a_skipped_file_alone_moves_the_exit_code(lib):
    _nothing_left_undone(lib)
    path = lib / "refs/Dymola/linux/ref_0001.json"
    data = json.loads(path.read_text())
    data["variables"][1]["name"] = "something.else"
    _write(path, data)
    report = migrate(_cfg(lib), apply=False)
    assert _status(report, path) == [SKIPPED]
    assert not report.spec_skipped
    assert UNKNOWN_MODEL not in {f.status for f in report.files}
    assert report.clean is False
    assert _exit_code(lib) == 1


def test_an_unknown_model_alone_moves_the_exit_code(lib):
    _nothing_left_undone(lib)
    _write(lib / "refs/Dymola/linux/ref_0003.json", _ref("Gone", ["q"], "0003"))
    report = migrate(_cfg(lib), apply=False)
    assert UNKNOWN_MODEL in {f.status for f in report.files}
    assert not report.spec_skipped
    assert SKIPPED not in {f.status for f in report.files}
    assert report.clean is False
    assert _exit_code(lib) == 1


def test_a_spec_key_left_alone_moves_the_exit_code(lib):
    _nothing_left_undone(lib)
    spec_path = lib / "test_spec.json"
    spec = json.loads(spec_path.read_text())
    # MyLib.Twice is x={a, a}: 'a' is the expression of two variables.
    spec["tests"].append(
        {"model": "MyLib.Twice", "comparison": {"variable_overrides": {"a": {}}}}
    )
    _write(spec_path, spec)
    report = migrate(_cfg(lib), apply=False)
    assert report.spec_skipped
    assert {f.status for f in report.files} <= {RENAMED, EXPRESSION_ADDED, MIGRATED}
    assert report.clean is False
    assert _exit_code(lib) == 1


# ---------------------------------------------------------------------------
# Read everything, decide everything, then write
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(b'{"model_id": "MyLib.Plain", "variab', id="truncated"),
        pytest.param(b"", id="empty"),
        pytest.param(b"\xff\xfe{}", id="not-utf-8"),
        pytest.param(b"[1, 2]", id="not-an-object"),
    ],
)
def test_a_file_that_cannot_be_read_is_a_skip_naming_it(lib, payload):
    """Never a raw traceback: over 900 baselines a bare JSONDecodeError
    names no file, and it lands AFTER earlier files were written."""
    path = lib / "refs/Dymola/linux/ref_0001.json"
    path.write_bytes(payload)
    report = migrate(_cfg(lib), apply=True)
    assert _status(report, path) == [SKIPPED]
    assert path in report.unreadable
    assert report.clean is False
    assert path.read_bytes() == payload


def test_a_soft_check_whose_primary_cannot_be_read_says_so(lib):
    """The primary is what names a soft check's model; unreadable, the soft
    check beside it is undecidable too — and says which file did it."""
    (lib / "refs/Dymola/linux/ref_0001.json").write_bytes(b"{oops")
    soft = lib / "refs/Dymola/linux/soft_checks/ref_0001/om.json"
    report = migrate(_cfg(lib), apply=True)
    assert _status(report, soft) == [SKIPPED]
    assert "ref_0001.json" in _reasons(report, soft)
    assert soft not in report.unreadable


def test_a_bad_file_late_in_the_walk_leaves_the_earlier_ones_unwritten(lib):
    """Two phases. The walk reaches OpenModelica after Dymola, and the spec
    after both: a file it cannot read there must leave a tree in which
    NOTHING moved, not one whose first partition migrated."""
    (lib / "refs/OpenModelica/linux/ref_0001.json").write_bytes(b'{"model_id": ')
    before = _snapshot(lib)
    report = migrate(_cfg(lib), apply=True)
    assert _snapshot(lib) == before
    assert report.unreadable
    assert _exit_code(lib, "--apply") == 1
    assert _snapshot(lib) == before


def test_an_unreadable_spec_writes_no_baseline_either(lib):
    (lib / "test_spec.json").write_text("{not json")
    before = _snapshot(lib)
    report = migrate(_cfg(lib), apply=True)
    assert _snapshot(lib) == before
    assert lib / "test_spec.json" in report.unreadable
    assert report.clean is False


def test_a_bom_prefixed_file_is_read_and_migrated(lib):
    """A baseline a Windows editor saved keeps its BOM. It parses; it is
    not a reason to refuse the file."""
    path = lib / "refs/Dymola/linux/ref_0001.json"
    path.write_bytes(
        b"\xef\xbb\xbf" + (json.dumps(json.loads(path.read_text()), indent=2)).encode()
    )
    report = migrate(_cfg(lib), apply=True)
    assert _status(report, path) == [RENAMED]
    assert _names(path) == NEW_PLAIN
    assert not path.read_bytes().startswith(b"\xef\xbb\xbf")


# ---------------------------------------------------------------------------
# An ambiguous key, and a malformed entry
# ---------------------------------------------------------------------------


def _twice_ref(lib, overrides):
    path = lib / "refs/Dymola/linux/ref_0004.json"
    _write(
        path,
        _ref(
            "Twice",
            OLD_NAMES["Twice"],
            "0004",
            {"comparison": {"variable_overrides": overrides}},
        ),
    )
    return path


def test_an_override_key_two_indices_share_skips_the_ref_file(lib):
    """``x={a, a}``: 'a' is the expression of BOTH variables. The spec tier
    already refuses to pick; renaming the key to the last index's name
    silently moves the override to a different variable."""
    path = _twice_ref(lib, {"a": {"tolerance": 0.2}})
    before = path.read_bytes()
    report = migrate(_cfg(lib), apply=True)
    assert _status(report, path) == [SKIPPED]
    reasons = _reasons(report, path)
    assert "'a'" in reasons
    assert "unitTests.x[1]" in reasons and "unitTests.x[2]" in reasons
    assert path.read_bytes() == before


def test_an_old_and_a_new_override_key_in_one_ref_map_skips_the_file(lib):
    """Renaming the old onto the new drops one of the two silently."""
    path = lib / "refs/Dymola/linux/ref_0001.json"
    data = json.loads(path.read_text())
    data["comparison"] = {
        "variable_overrides": {
            "a": {"tolerance": 1},
            "unitTests.x[1]": {"tolerance": 2},
        }
    }
    _write(path, data)
    before = path.read_bytes()
    report = migrate(_cfg(lib), apply=True)
    assert _status(report, path) == [SKIPPED]
    assert "'a'" in _reasons(report, path)
    assert "unitTests.x[1]" in _reasons(report, path)
    assert path.read_bytes() == before


def test_an_old_and_a_new_override_key_in_one_spec_map_is_left_alone(lib):
    spec_path = lib / "test_spec.json"
    spec = json.loads(spec_path.read_text())
    spec["tests"][0]["comparison"]["variable_overrides"] = {
        "b[1, 1]": {"tolerance": 0.5},
        "unitTests.x[2]": {"tolerance": 0.9},
    }
    _write(spec_path, spec)
    report = migrate(_cfg(lib), apply=True)
    assert json.loads(spec_path.read_text())["tests"][0]["comparison"][
        "variable_overrides"
    ] == {"b[1, 1]": {"tolerance": 0.5}, "unitTests.x[2]": {"tolerance": 0.9}}
    assert any("'b[1, 1]'" in line for line in report.spec_skipped)
    assert report.clean is False


@pytest.mark.parametrize("index", [None, "1", 1.0], ids=["missing", "string", "float"])
def test_an_entry_without_an_integer_index_skips_the_file(lib, index):
    """It used to fall down the pattern branch: reported 'expression-added',
    exit 0, 'already migrated' on the next run — with its names never
    moved."""
    path = lib / "refs/Dymola/linux/ref_0001.json"
    data = json.loads(path.read_text())
    if index is None:
        del data["variables"][0]["index"]
    else:
        data["variables"][0]["index"] = index
    _write(path, data)
    before = path.read_bytes()
    report = migrate(_cfg(lib), apply=True)
    assert _status(report, path) == [SKIPPED]
    assert "index" in _reasons(report, path)
    assert path.read_bytes() == before
    assert report.clean is False


def test_two_entries_claiming_one_index_skip_the_file(lib):
    path = lib / "refs/Dymola/linux/ref_0001.json"
    data = json.loads(path.read_text())
    data["variables"].append({"index": 1, "name": "a", "values": [9.0, 9.0]})
    _write(path, data)
    before = path.read_bytes()
    report = migrate(_cfg(lib), apply=True)
    assert _status(report, path) == [SKIPPED]
    assert "index 1" in _reasons(report, path)
    assert path.read_bytes() == before


def test_an_entry_past_the_components_own_keeps_its_name(lib):
    """Index 3 of a two-variable component is not the component's, whatever
    its name spells: it keeps the name and gains the empty field."""
    path = lib / "refs/Dymola/linux/ref_0001.json"
    data = json.loads(path.read_text())
    data["variables"].append({"index": 3, "name": "a", "values": [9.0, 9.0]})
    _write(path, data)
    migrate(_cfg(lib), apply=True)
    assert _names(path) == [*NEW_PLAIN, ("a", "")]


# ---------------------------------------------------------------------------
# What the walk reaches
# ---------------------------------------------------------------------------


def test_a_soft_check_is_resolved_from_its_own_partitions_primary(lib):
    """Test ids are per partition: OpenModelica's ref_0001 is a DIFFERENT
    model from Dymola's, and a soft check under Dymola belongs to Dymola's."""
    _write(
        lib / "refs/OpenModelica/linux/ref_0001.json",
        _ref("Cat", OLD_NAMES["Cat"], "0001"),
    )
    migrate(_cfg(lib), apply=True)
    assert _names(lib / "refs/Dymola/linux/soft_checks/ref_0001/om.json") == NEW_PLAIN


@pytest.mark.parametrize("name", ["ref_0001_backup.json", "ref_notes.json"])
def test_a_file_beside_a_baseline_that_is_not_one_is_not_touched(lib, name):
    """A baseline is what the STORE calls one. Somebody's copy kept beside
    it is not read by the store and is not rewritten here."""
    path = lib / "refs/Dymola/linux" / name
    _write(path, _ref("Plain", OLD_NAMES["Plain"], "0001"))
    before = path.read_bytes()
    report = migrate(_cfg(lib), apply=True)
    assert path.read_bytes() == before
    assert _status(report, path) == []


def test_an_orphan_soft_check_directory_is_named_not_migrated(lib):
    """No primary beside it, so nothing says which model it holds. It is
    mentioned so a reader knows it was left, and it is not a skip."""
    orphan = lib / "refs/Dymola/linux/soft_checks/ref_0042"
    orphan.mkdir()
    soft = _ref("Plain", OLD_NAMES["Plain"], "0042")
    del soft["model_id"]
    _write(orphan / "om.json", soft)
    before = (orphan / "om.json").read_bytes()
    report = migrate(_cfg(lib), apply=True)
    assert report.orphan_soft_checks == [orphan]
    assert _status(report, orphan / "om.json") == []
    assert (orphan / "om.json").read_bytes() == before


# ---------------------------------------------------------------------------
# What the report says
# ---------------------------------------------------------------------------


def test_an_already_migrated_file_still_gets_a_stale_override_key_renamed(lib):
    """Re-accepted under the new code BEFORE migrating: new names, the old
    expression-keyed override carried over. The rename map is the MODEL's
    rule, not a by-product of renaming an entry."""
    path = lib / "refs/Dymola/linux/ref_0001.json"
    data = _ref("Plain", [n for n, _ in NEW_PLAIN], "0001")
    for entry, (_, expression) in zip(data["variables"], NEW_PLAIN):
        entry["expression"] = expression
    data["comparison"] = {"variable_overrides": {"b[\n      1, 1]": {"tolerance": 0.5}}}
    _write(path, data)
    report = migrate(_cfg(lib), apply=True)
    assert _status(report, path) == [RENAMED]
    assert json.loads(path.read_text())["comparison"]["variable_overrides"] == {
        "unitTests.x[2]": {"tolerance": 0.5}
    }


def test_a_ref_override_key_a_spec_pattern_tracks_is_left_alone(lib):
    """Same rule as the spec tier: a pattern names a variable of its own in
    the result file, so the key already names a variable."""
    spec_path = lib / "test_spec.json"
    spec = json.loads(spec_path.read_text())
    spec["tests"][0]["variables"] = ["a"]
    _write(spec_path, spec)
    migrate(_cfg(lib), apply=True)
    ref = json.loads((lib / "refs/Dymola/linux/ref_0001.json").read_text())
    assert ref["comparison"]["variable_overrides"] == {"a": {"tolerance": 0.2}}


def test_a_rewritten_file_that_held_crlf_is_named(lib):
    """The store writes every baseline LF, so a CRLF file shows as a
    whole-file diff. Said once, and the files named, before a reviewer
    reads a one-name change as a rewrite of everything."""
    path = lib / "refs/Dymola/linux/ref_0001.json"
    path.write_bytes(path.read_text().replace("\n", "\r\n").encode())
    other = lib / "refs/OpenModelica/linux/ref_0001.json"
    report = migrate(_cfg(lib), apply=True)
    assert report.crlf == [path]
    assert b"\r" not in path.read_bytes()
    assert _status(report, other) == [RENAMED] and other not in report.crlf


def test_a_spec_entry_nothing_was_considered_for_is_mentioned_not_skipped(lib):
    """The one place a spec entry silently does nothing: its model has no
    UnitTests component here — none, or it left the library. Said out loud,
    and informational: the exit code does not move."""
    _nothing_left_undone(lib)
    spec_path = lib / "test_spec.json"
    spec = json.loads(spec_path.read_text())
    spec["tests"].append({"model": "MyLib.Nope", "variables": ["q"]})
    _write(spec_path, spec)
    report = migrate(_cfg(lib), apply=False)
    assert report.spec_not_considered == ["MyLib.Nope"]
    assert report.clean is True


def test_a_spec_key_is_counted_once_per_model_and_key(lib):
    """'a' reached through an override, a leaf and a plot series is ONE
    decision to check, carrying how many places it moved."""
    spec_path = lib / "test_spec.json"
    spec = json.loads(spec_path.read_text())
    spec["tests"][0]["comparison"]["variable_overrides"]["a"] = {"tolerance": 0.1}
    _write(spec_path, spec)
    report = migrate(_cfg(lib), apply=True)
    moved = [(m, old) for m, old, _, _ in report.spec_rewritten]
    assert len(moved) == len(set(moved))
    assert ("MyLib.Plain", "a", "unitTests.x[1]", 3) in report.spec_rewritten
