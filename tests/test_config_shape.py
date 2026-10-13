"""`testing.json` is read through ONE key table, and is strict.

`config_shape.TESTING_JSON` is what `Config` refuses an unknown key by, what
it takes its defaults from, what `spec schema testing` publishes and what the
docs' key lists are held to. A backend's settings block is checked the same
way against the table the backend declares.
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

import pytest

from dstf.backends.dymola.backend import DymolaBackend, DymolaConfig
from dstf.cli import main
from dstf.cli.main import build_arg_parser
from dstf.config import Config, ConfigError, load_config_file
from dstf.config_shape import TESTING_JSON, Setting, default, refused
from dstf.discovery.spec_shape import NOTES
from dstf.reporting.schema_export import build_config_schema, build_schema

REPO = Path(__file__).resolve().parents[1]


def _project(tmp_path: Path, config: dict) -> Path:
    """A Python-source project (no simulator, no package.mo) whose
    testing.json holds ``config``."""
    path = tmp_path / "testing.json"
    path.write_text(
        json.dumps({"source_type": "python", "backend": "Python", **config}),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# A key nothing reads is an error that names it
# ---------------------------------------------------------------------------


class TestUnknownKeys:
    def test_a_slip_is_offered_its_spelling(self, tmp_path):
        path = _project(tmp_path, {"tolerence": 0.01})
        with pytest.raises(ConfigError) as exc:
            Config(config_file=path)
        assert str(exc.value) == (
            f'{path}: unknown field "tolerence" — did you mean "tolerance"?'
        )

    def test_any_other_lists_the_fields(self, tmp_path):
        path = _project(tmp_path, {"colour": "red"})
        with pytest.raises(ConfigError) as exc:
            Config(config_file=path)
        assert str(exc.value) == (
            f'{path}: unknown field "colour"; the fields here are: '
            + ", ".join(sorted(TESTING_JSON))
        )

    @pytest.mark.parametrize(
        "command",
        [
            ["discover"],
            ["run"],
            ["compare"],
            ["baseline", "export"],
            ["manifest", "show"],
            ["manifest", "dump"],
            ["manifest", "cleanup"],
            ["spec", "add", "Lib.M"],
            ["spec", "update", "patch.json"],
            ["companion", "add", "Lib.M", "name", "rig.csv"],
            ["companion", "list"],
            ["companion", "freeze", "Lib.M", "name"],
            ["companion", "remove", "Lib.M", "name"],
            ["soft-check", "add", "Lib.M", "name", "ref.json"],
            ["soft-check", "list"],
            ["soft-check", "remove", "Lib.M", "name"],
            ["check"],
        ],
        ids=lambda command: " ".join(command[:2]),
    )
    def test_every_command_that_reads_the_file_says_it_and_exits_2(
        self, tmp_path, capsys, command
    ):
        """In the words and with the exit code a test_spec.json key is
        refused with — and before the command does anything."""
        path = _project(tmp_path, {"tolerence": 0.01})
        assert main(["--config", str(path), *command]) == 2
        err = capsys.readouterr().err
        assert err == (
            f'Config error: {path}: unknown field "tolerence" — did you mean "tolerance"?\n'
        )
        assert sorted(p.name for p in tmp_path.iterdir()) == ["testing.json"]

    @pytest.mark.parametrize("key", sorted(NOTES))
    def test_the_note_keys_are_the_spec_s(self, tmp_path, key):
        note = {"who": "me"} if key == "metadata" else "why this project is set up so"
        assert Config(config_file=_project(tmp_path, {key: note})).backend == "Python"

    def test_a_schema_pointer_is_read_by_nothing_and_allowed(self, tmp_path):
        path = _project(tmp_path, {"$schema": "./testing.schema.json"})
        assert Config(config_file=path).backend == "Python"

    def test_a_file_that_is_no_object(self, tmp_path):
        path = tmp_path / "testing.json"
        path.write_text('["a"]', encoding="utf-8")
        with pytest.raises(ConfigError) as exc:
            load_config_file(path)
        assert str(exc.value) == f'{path}: must be an object, got ["a"]'


class TestWrongTypes:
    @pytest.mark.parametrize(
        ("key", "value", "said"),
        [
            ("max_embedded_samples", "abc", 'must be a whole number, got "abc"'),
            ("max_embedded_samples", 2.5, "must be a whole number, got 2.5"),
            ("dependencies", "Modelica", 'must be a list of strings, got "Modelica"'),
            ("dependencies", [1], "must be a list of strings, got [1]"),
            ("backend_setup", {"a": 1}, 'must be a list of strings, got {"a": 1}'),
            ("source_path", 3, "must be a string, got 3"),
            ("reference_root", None, "must be a string, got null"),
            ("backends", ["Dymola"], 'must be an object, got ["Dymola"]'),
            (
                "recognizers",
                {"name": "x"},
                'must be a list of objects, got {"name": "x"}',
            ),
            ("disable_bundled", "x", 'must be a list of strings, got "x"'),
            ("metadata", "text", 'must be an object, got "text"'),
        ],
    )
    def test_a_value_of_another_type_names_its_key(self, tmp_path, key, value, said):
        path = _project(tmp_path, {key: value})
        with pytest.raises(ConfigError) as exc:
            Config(config_file=path)
        assert str(exc.value) == f"{path}: {key}: {said}"

    def test_a_key_with_a_rule_of_its_own_keeps_its_sentence(self, tmp_path):
        """A base tolerance, a baseline's points, a theme and the start
        bound each say more than a type: the type check leaves them to it."""
        with pytest.raises(ConfigError, match="the base tolerance must be a number"):
            Config(config_file=_project(tmp_path, {"tolerance": "0.5"}))
        with pytest.raises(ConfigError, match="the points a baseline keeps are"):
            Config(config_file=_project(tmp_path, {"baseline_points": 1}))
        with pytest.raises(ConfigError, match="the bundled themes are"):
            Config(config_file=_project(tmp_path, {"theme": 3}))
        with pytest.raises(ConfigError, match="a positive number of seconds"):
            Config(config_file=_project(tmp_path, {"worker_startup_timeout": "soon"}))
        own = {key for key, setting in TESTING_JSON.items() if setting.own_rule}
        assert own == {
            "tolerance",
            "baseline_points",
            "theme",
            "worker_startup_timeout",
        }


# ---------------------------------------------------------------------------
# One place per default
# ---------------------------------------------------------------------------


class TestDefaults:
    def test_config_s_defaults_are_the_table_s(self, tmp_path):
        """Every key with a default that `Config` has a field for: the
        field's default and the built Config's value are the table's."""
        fields = {f.name: f for f in dataclasses.fields(Config)}
        path = tmp_path / "testing.json"
        # Only what a non-Modelica project must say; every other key unset.
        path.write_text('{"source_type": "python"}', encoding="utf-8")
        config = Config(config_file=path)
        checked = []
        for key, setting in TESTING_JSON.items():
            if setting.default is None or key not in fields:
                continue
            field = fields[key]
            declared = (
                field.default_factory()
                if field.default_factory is not dataclasses.MISSING
                else field.default
            )
            # `tolerance` is None until it is resolved: unset is told from 0.
            if key != "tolerance":
                assert declared == setting.default, key
            if key != "source_type":
                assert getattr(config, key) == setting.default, key
            checked.append(key)
        assert checked == [
            "source_type",
            "backends",
            "backend_setup",
            "dependencies",
            "tolerance",
            "baseline_points",
            "max_embedded_samples",
            "diagnostic_variables",
            "theme",
            "recognizers",
        ]

    def test_a_default_is_a_copy(self):
        default("diagnostic_variables").append("x")
        assert default("diagnostic_variables") == ["CPUtime", "EventCounter"]

    def test_every_key_says_what_it_is_for(self):
        for key, setting in TESTING_JSON.items():
            assert len(setting.description) > 20, key
            assert not re.search(r"\bD\d{2,3}\b", setting.description), (
                key
            )  # no decision ids
            if setting.default is not None:
                assert "Default" in setting.description, key


# ---------------------------------------------------------------------------
# A settings block per backend
# ---------------------------------------------------------------------------


class TestBackendBlocks:
    def test_an_unknown_key_names_the_block(self, tmp_path):
        path = _project(tmp_path, {"backends": {"Dymola": {"show_id": True}}})
        with pytest.raises(ConfigError) as exc:
            Config(config_file=path)
        assert str(exc.value) == (
            f'{path}: backends.Dymola: unknown field "show_id" — did you mean "show_ide"?'
        )

    def test_a_key_that_is_no_near_miss_lists_the_block_s_fields(self, tmp_path):
        """The one did-you-mean (`strict_keys.near_miss`) offers a name for
        a slip of the hand only: `show_ui` is three edits from `show_ide`."""
        path = _project(tmp_path, {"backends": {"Dymola": {"show_ui": True}}})
        with pytest.raises(ConfigError) as exc:
            Config(config_file=path)
        assert str(exc.value) == (
            f'{path}: backends.Dymola: unknown field "show_ui"; the fields here are: '
            "batch_size, executables, interface, show_ide"
        )

    def test_an_entry_is_its_backend_s_by_how_its_name_starts(self, tmp_path):
        path = _project(tmp_path, {"backends": {"Dymola 2025": {"batch_sise": 3}}})
        with pytest.raises(ConfigError) as exc:
            Config(config_file=path)
        assert str(exc.value) == (
            f'{path}: backends.Dymola 2025: unknown field "batch_sise" — '
            'did you mean "batch_size"?'
        )

    def test_the_old_list_form_names_the_new_one(self, tmp_path):
        path = _project(tmp_path, {"backends": {"Dymola": ["/usr/local/bin/dymola"]}})
        with pytest.raises(ConfigError) as exc:
            Config(config_file=path)
        assert str(exc.value) == (
            f'{path}: backends.Dymola must be an object — {{"executables": [...]}} '
            "(was a list of paths)"
        )

    @pytest.mark.parametrize(
        ("block", "said"),
        [
            (
                {"show_ide": "yes"},
                'backends.Dymola.show_ide: must be true or false, got "yes"',
            ),
            (
                {"batch_size": 0},
                "backends.Dymola.batch_size: must be a whole number of 1 or more, got 0",
            ),
            (
                {"executables": "dymola"},
                'backends.Dymola.executables: must be a list of strings, got "dymola"',
            ),
            (
                {"interface": None},
                "backends.Dymola.interface: must be a string, got null",
            ),
        ],
    )
    def test_a_value_of_another_type_names_block_and_key(self, tmp_path, block, said):
        path = _project(tmp_path, {"backends": {"Dymola": block}})
        with pytest.raises(ConfigError) as exc:
            Config(config_file=path)
        assert str(exc.value) == f"{path}: {said}"

    def test_a_block_that_is_no_object(self, tmp_path):
        path = _project(tmp_path, {"backends": {"Dymola": "dymola"}})
        with pytest.raises(ConfigError) as exc:
            Config(config_file=path)
        assert (
            str(exc.value)
            == f'{path}: backends.Dymola: must be an object, got "dymola"'
        )

    def test_an_entry_of_no_registered_backend(self, tmp_path):
        path = _project(tmp_path, {"backends": {"Simulink": {}}})
        with pytest.raises(ConfigError) as exc:
            Config(config_file=path)
        assert str(exc.value).startswith(
            f"{path}: backends.Simulink: no backend is registered under that name; "
            "the backends are: Dymola, OpenModelica, FMPy, Julia, Python"
        )

    def test_an_empty_block_is_valid(self, tmp_path):
        path = _project(tmp_path, {"backends": {"Dymola": {}, "Python": {}}})
        config = Config(config_file=path)
        assert config.backends == {"Dymola": {}, "Python": {}}

    def test_fmpy_has_no_executable_to_name(self, tmp_path):
        path = _project(tmp_path, {"backends": {"FMPy": {"executables": ["fmpy"]}}})
        with pytest.raises(ConfigError) as exc:
            Config(config_file=path)
        assert str(exc.value) == (
            f'{path}: backends.FMPy: unknown field "executables" — there are no settings here'
        )

    def test_the_top_level_show_ide_is_an_unknown_key(self, tmp_path):
        path = _project(tmp_path, {"show_ide": True})
        with pytest.raises(ConfigError) as exc:
            Config(config_file=path)
        assert str(exc.value).startswith(f'{path}: unknown field "show_ide"')

    def test_dymola_s_settings_reach_its_backend(self, tmp_path):
        """What `--show-ide`, `--batch-size` and `--dymola-interface` said
        is said in the block, and the backend reads it from there."""
        path = _project(
            tmp_path,
            {
                "backend": "Dymola",
                "backends": {"Dymola": {"show_ide": True, "batch_size": 3}},
            },
        )
        config = Config(config_file=path)
        assert config.backend_settings == {"show_ide": True, "batch_size": 3}
        dymola = DymolaConfig.from_config(config)
        assert (dymola.show_ide, dymola.batch_size) == (True, 3)
        assert DymolaBackend.settings_of(config) == {
            "executables": [],
            "show_ide": True,
            "batch_size": 3,
            "interface": None,
        }

    def test_the_selected_entry_s_block_is_the_one_read(self, tmp_path):
        path = _project(
            tmp_path,
            {
                "backend": "Dymola 2025",
                "backends": {"Dymola 2026": {"show_ide": True}, "Dymola 2025": {}},
            },
        )
        assert DymolaConfig.from_config(Config(config_file=path)).show_ide is False

    def test_a_map_given_to_config_is_checked_the_same(self, tmp_path):
        with pytest.raises(ConfigError) as exc:
            Config(
                config_file=_project(tmp_path, {}),
                backends={"Dymola": {"show_id": True}},
            )
        assert str(exc.value) == (
            'Config: backends.Dymola: unknown field "show_id" — did you mean "show_ide"?'
        )

    def test_every_backend_s_table_is_a_table(self):
        from dstf.backends import backend_class, backend_names

        for name in backend_names()[:5]:
            for key, setting in backend_class(name).settings.items():
                assert isinstance(setting, Setting), (name, key)
                assert refused("", {key: setting.default}, {key: setting}) is None or (
                    setting.default is None
                ), (name, key)


class TestTheDeletedFlags:
    @pytest.mark.parametrize(
        "argv",
        [
            ["--dymola-interface", "x", "discover"],
            ["run", "--show-ide"],
            ["run", "--batch-size", "3"],
            ["check-dymola"],
            ["check-openmodelica"],
            ["check-julia"],
        ],
    )
    def test_they_are_gone_from_the_parser(self, argv, capsys):
        with pytest.raises(SystemExit) as exc:
            build_arg_parser().parse_args(argv)
        assert exc.value.code == 2
        capsys.readouterr()

    def test_batch_backend_and_backend_path_stay(self):
        args = build_arg_parser().parse_args(
            ["run", "--batch", "--backend", "Dymola", "--backend-path", "/x/dymola"]
        )
        assert (args.batch, args.backend, args.backend_path) == (
            True,
            "Dymola",
            "/x/dymola",
        )


# ---------------------------------------------------------------------------
# The files the repo ships
# ---------------------------------------------------------------------------


class TestTheRepoSFiles:
    @pytest.mark.parametrize(
        "path",
        [
            "examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/testing.json",
            "examples/julia/JuliaMtkTestingLib/Resources/ReferenceResults/testing.json",
            "examples/python/PythonTestingLib/Resources/ReferenceResults/testing.json",
            "examples/fmu/testing.json",
            "tests/fixtures/testing.json",
            "testing.json",
        ],
    )
    def test_each_is_in_the_shape_the_tool_reads(self, path):
        from dstf.config import _backend_blocks

        data = load_config_file(REPO / path)
        blocks = _backend_blocks(data.get("backends", {}), path)
        assert all(isinstance(block, dict) for block in blocks.values())

    @pytest.mark.parametrize("example", ["modelica", "julia", "python"])
    def test_an_example_s_backends_are_blocks_of_executables(self, example):
        (path,) = (REPO / "examples" / example).glob(
            "*/Resources/ReferenceResults/testing.json"
        )
        config = Config(config_file=path, write_default=False)
        assert config.backends
        for block in config.backends.values():
            assert list(block) == ["executables"]

    def test_the_default_file_the_tool_writes(self, tmp_path, capsys, monkeypatch):
        lib = tmp_path / "Lib"
        lib.mkdir()
        (lib / "package.mo").write_text("package Lib end Lib;")
        monkeypatch.chdir(tmp_path)
        Config(source_path=lib, reference_root=tmp_path / "refs")
        capsys.readouterr()
        written = json.loads((tmp_path / "refs" / "testing.json").read_text())
        assert written == {"backends": {}, "dependencies": []}
        # …and it reads back clean, as the file it is.
        assert load_config_file(tmp_path / "refs") == written


# ---------------------------------------------------------------------------
# The schema, and the pages that list the keys
# ---------------------------------------------------------------------------


class TestSchema:
    def test_it_is_the_table(self):
        schema = build_config_schema()
        assert schema["title"] == "DSTF testing.json"
        assert schema["additionalProperties"] is False
        assert list(schema["properties"]) == list(TESTING_JSON)
        for key, setting in TESTING_JSON.items():
            node = schema["properties"][key]
            assert node["description"] == setting.description
            assert node.get("default") == setting.default
            for word, value in setting.schema.items():
                assert node[word] == value

    def test_each_backend_s_block_is_its_table(self):
        blocks = build_config_schema()["properties"]["backends"]
        assert blocks["additionalProperties"] is False
        patterns = blocks["patternProperties"]
        assert list(patterns)[:5] == [
            "^Dymola",
            "^OpenModelica",
            "^FMPy",
            "^Julia",
            "^Python",
        ]
        dymola = patterns["^Dymola"]
        assert dymola["additionalProperties"] is False
        assert list(dymola["properties"]) == list(DymolaBackend.settings)
        assert dymola["properties"]["show_ide"]["description"] == (
            DymolaBackend.settings["show_ide"].description
        )
        assert patterns["^FMPy"]["properties"] == {}

    def test_spec_schema_s_default_is_the_spec_s(self, capsys):
        assert main(["spec", "schema"]) == 0
        assert json.loads(capsys.readouterr().out) == build_schema()
        assert main(["spec", "schema", "test_spec"]) == 0
        assert json.loads(capsys.readouterr().out) == build_schema()

    def test_spec_schema_writes_testing_json_s(self, tmp_path, capsys):
        out = tmp_path / "testing.schema.json"
        assert main(["spec", "schema", "testing", "-o", str(out)]) == 0
        capsys.readouterr()
        assert json.loads(out.read_text(encoding="utf-8")) == build_config_schema()


def _keys_listed(text: str, pattern: str) -> list[str]:
    return re.findall(pattern, text, flags=re.MULTILINE)


class TestTheDocsListTheTable:
    """The two key lists a person reads name the keys the code reads — no
    more, no fewer. The notes and `$schema` are read by nothing and listed
    by neither."""

    KEYS = sorted(set(TESTING_JSON) - set(NOTES) - {"$schema"})

    def test_usage_md_s_key_table(self):
        text = (REPO / "docs" / "usage.md").read_text(encoding="utf-8")
        table = text[
            text.index("| Field | Required |") : text.index("**Backend settings.**")
        ]
        assert sorted(_keys_listed(table, r"^\| `([^`]+)` \|")) == self.KEYS

    def test_claude_md_s_configuration_list(self):
        text = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
        section = text[
            text.index("## Configuration") : text.index("### test_spec.json format")
        ]
        assert sorted(_keys_listed(section, r"^- `([^`]+)` —")) == self.KEYS
