"""The command tree (`dstf.cli.parser`): what it must keep being.

* Every node — the top level, each group, each command — explains itself:
  a description, an example that parses, help on every option and
  positional, in words a user reads.
* A command offers exactly the flags it reads. The four that say where the
  project is work on either side of the command, once; a flag a command
  does not read is an error on both sides, and so is one the other flags on
  the line mean will not be read (`--report` beside `--accept`) — but a
  flag that asks for something NOT to happen (`--no-browser`).
* A value that cannot be meant is the parser's error, saying what is allowed.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
from pathlib import Path

import pytest

from dstf.cli import build_arg_parser, main
from dstf.cli.parser import COMMAND_GROUPS, PROJECT_FLAGS

# ---------------------------------------------------------------------------
# The tree, as argparse holds it
# ---------------------------------------------------------------------------


def _subparsers(node: argparse.ArgumentParser) -> list[argparse._SubParsersAction]:
    return [a for a in node._actions if isinstance(a, argparse._SubParsersAction)]


def _walk(node: argparse.ArgumentParser, path: str = ""):
    """Every parser of the tree with its command path, the root (``""``)
    first — by what argparse can reach, not by what the tree records."""
    yield path, node
    for sub in _subparsers(node):
        for name, child in sub.choices.items():
            yield from _walk(child, f"{path} {name}".strip())


PARSER = build_arg_parser()
NODES = dict(_walk(PARSER))
COMMANDS = sorted(path for path, node in NODES.items() if not _subparsers(node))
GROUPS = sorted(path for path, node in NODES.items() if path and _subparsers(node))


def _texts(path: str, node: argparse.ArgumentParser):
    """Every text the node's ``--help`` prints, with what it is."""
    yield f"`dstf {path}` description", node.description
    yield f"`dstf {path}` epilog", node.epilog
    for action in node._actions:
        if isinstance(action, argparse._SubParsersAction):
            for choice in action._choices_actions:
                yield f"`dstf {path}` listing of {choice.dest}", choice.help
        else:
            name = "/".join(action.option_strings) or action.metavar or action.dest
            yield f"`dstf {path}` {name}", action.help


def test_the_tree_records_what_argparse_reaches():
    assert dict(PARSER.nodes) == NODES
    assert sorted(PARSER.commands) == COMMANDS
    assert GROUPS == ["baseline", "companion", "manifest", "soft-check", "spec"]


# ---------------------------------------------------------------------------
# Fence 1 — every node explains itself, in a user's words
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", sorted(NODES))
def test_every_node_has_a_description_and_an_example(path):
    node = NODES[path]
    assert (node.description or "").strip(), f"`dstf {path}` has no description"
    assert "dstf " in (node.epilog or ""), f"`dstf {path}` has no example command"
    assert isinstance(node.formatter_class, type) and issubclass(
        node.formatter_class, argparse.RawDescriptionHelpFormatter
    ), f"`dstf {path}` would re-wrap its examples"


@pytest.mark.parametrize("path", sorted(NODES))
def test_every_option_and_positional_has_help(path):
    node = NODES[path]
    for action in node._actions:
        if isinstance(action, argparse._SubParsersAction):
            listed = {choice.dest for choice in action._choices_actions}
            assert listed == set(action.choices), (
                f"`dstf {path}` lists {sorted(listed)} of its "
                f"{sorted(action.choices)}: a command with no one-line help"
            )
            continue
        name = "/".join(action.option_strings) or action.metavar or action.dest
        assert action.help is not argparse.SUPPRESS, f"`dstf {path}` hides {name}"
        assert (action.help or "").strip(), f"`dstf {path}` {name} has no help"


#: What a help text must not hold: markup and the project's own shorthand.
_NOT_FOR_A_USER = {
    "a reST double backtick": re.compile(r"``"),
    "a decision id": re.compile(r"\bD\d+\b"),
    'the word "legacy"': re.compile(r"legacy", re.IGNORECASE),
}


@pytest.mark.parametrize("path", sorted(NODES))
def test_the_help_is_written_for_a_user(path):
    for what, text in _texts(path, NODES[path]):
        for name, pattern in _NOT_FOR_A_USER.items():
            found = pattern.search(text or "")
            assert not found, f"{what} holds {name}: {text!r}"


@pytest.mark.parametrize("path", sorted(NODES))
def test_a_default_is_stated_where_there_is_one(path):
    """An option whose value the parser fills in says so."""
    for action in NODES[path]._actions:
        if isinstance(action, argparse._SubParsersAction | argparse._HelpAction):
            continue
        if action.default in (None, False, argparse.SUPPRESS):
            continue
        name = "/".join(action.option_strings) or action.dest
        assert f"Default: {action.default}" in action.help, (
            f"`dstf {path}` {name} defaults to {action.default!r} and does not say so"
        )


def test_the_top_level_help_lists_every_command_once_under_a_heading(monkeypatch):
    monkeypatch.setenv("COLUMNS", "80")
    listed = [path for _heading, paths in COMMAND_GROUPS for path in paths]
    assert sorted(listed) == COMMANDS
    text = PARSER.format_help()
    for heading, paths in COMMAND_GROUPS:
        assert f"\n{heading}:\n" in text
        section = text.split(f"\n{heading}:\n", 1)[1].split("\n\n", 1)[0]
        rows = [
            line.split("  ")[1] for line in section.splitlines() if line[2:3] != " "
        ]
        assert rows == list(paths)
    for path in COMMANDS:
        summary = PARSER.summaries[path]
        # one line each, at 80 columns
        assert summary and "\n" not in summary and len(summary) <= 54
        assert summary in text


def test_the_top_level_help_keeps_its_examples_and_the_project_flags(monkeypatch):
    """`format_help` is the tree's own (`_Root`): it lays out the listing,
    and must still print the examples and the rows of the four flags that
    say where the project is."""
    monkeypatch.setenv("COLUMNS", "80")
    text = PARSER.format_help()
    examples = _examples(PARSER)
    assert len(examples) >= 3
    for line in examples:
        assert f"  {line}\n" in text, line
    section = text.split("\nwhere the project is:\n", 1)[1]
    for flag in PROJECT_FLAGS.values():
        assert re.search(rf"^  {flag} [A-Z]+ ", section, re.M), flag


#: A word each command's one line must hold — written here, so that two
#: commands' lines swapped, or one copied over another, is seen.
_SUMMARY_WORDS = {
    "discover": "List the tests",
    "run": "Simulate",
    "compare": "without simulating",
    "author": "authoring tool",
    "check": "backends",
    "spec add": "Add a test",
    "spec update": "JSON-Patch",
    "spec schema": "schema",
    "baseline export": "every stored baseline",
    "soft-check add": "as a soft check",
    "soft-check list": "soft checks",
    "soft-check remove": "Remove a soft check",
    "companion add": "as a companion",
    "companion list": "companions",
    "companion freeze": "Copy a companion",
    "companion remove": "Remove a companion",
    "manifest show": "status",
    "manifest dump": "mapping",
    "manifest cleanup": "obsolete",
}


@pytest.mark.parametrize("path", COMMANDS)
def test_each_command_s_line_is_its_own(path):
    summary = PARSER.summaries[path]
    assert _SUMMARY_WORDS[path] in summary, summary
    assert list(PARSER.summaries.values()).count(summary) == 1
    # the group's listing says the same
    if " " in path:
        group, action = path.split()
        (sub,) = _subparsers(NODES[group])
        (listed,) = [c.help for c in sub._choices_actions if c.dest == action]
        assert listed == summary


def test_dstf_with_no_command_prints_the_help_and_exits_1(capsys):
    assert main([]) == 1
    assert "\nrun and score:\n" in capsys.readouterr().out


def test_variables_with_no_value_is_a_simulate_only_test(capsys):
    args = _parse(["spec", "add", "Lib.M", "--variables"], capsys)
    assert isinstance(args, argparse.Namespace), args
    assert args.variables == []


@pytest.mark.parametrize(
    ("flag", "field"), [("--timeout", "timeout"), ("--parallel", "parallel")]
)
def test_a_flag_s_default_in_its_help_is_the_code_s(flag, field, monkeypatch):
    """The help states Config's default, not a number written beside it."""
    import dataclasses

    from dstf.config import Config

    (spec,) = [f for f in dataclasses.fields(Config) if f.name == field]
    monkeypatch.setattr(spec, "default", 617)
    nodes = build_arg_parser().nodes
    for path in ("run", "compare"):
        for action in nodes[path]._actions:
            if flag in action.option_strings:
                assert "Default: 617." in action.help, (path, action.help)


def test_a_description_keeps_a_hyphenated_name_whole(monkeypatch):
    """The descriptions are filled by `_text`, an option's help by the
    tree's formatter; a name with a hyphen in it (`soft-check`,
    `--report-format`) is never cut at the hyphen."""
    monkeypatch.setenv("COLUMNS", "80")
    from dstf.cli.parser import _text

    names = "--reference-root soft-check dynamic-systems-testing-framework"
    for pad in range(60, 78):
        text = _text("x" * pad + " " + names)
        words = text.split()
        assert words[1:] == names.split(), text
    for path, node in NODES.items():
        # the description and every option's help, as printed
        for line in build_arg_parser().nodes[path].format_help().splitlines():
            assert not re.search(r"\w-$", line), f"`dstf {path}`: {line!r}"


def test_the_tree_builds_and_prints_without_a_plug_in_or_a_file(monkeypatch):
    """`--help` and `--version` wait for neither — and the documentation's
    generator walks the same tree."""
    from pathlib import Path

    def refuse(*args, **kwargs):
        raise AssertionError("the tree read a file or loaded a plug-in")

    monkeypatch.setattr("dstf.plugins.load_plugins", refuse)
    monkeypatch.setattr(Path, "read_text", refuse)
    monkeypatch.setattr(Path, "read_bytes", refuse)
    monkeypatch.setattr(Path, "open", refuse)
    for node in dict(_walk(build_arg_parser())).values():
        assert node.format_help()


# ---------------------------------------------------------------------------
# Fence 2 — every example in every epilog parses
# ---------------------------------------------------------------------------


def _examples(node: argparse.ArgumentParser) -> list[str]:
    return [
        line.strip()
        for line in (node.epilog or "").splitlines()
        if line.strip().startswith("dstf ")
    ]


@pytest.mark.parametrize(
    ("path", "example"),
    [(path, example) for path in sorted(NODES) for example in _examples(NODES[path])],
)
def test_every_example_parses(path, example, capsys):
    argv = shlex.split(example)[1:]
    try:
        args = build_arg_parser().parse_args(argv)
    except SystemExit:
        pytest.fail(
            f"the example of `dstf {path}` does not parse: {example}\n"
            f"{capsys.readouterr().err}"
        )
    # An example under a command is an example OF that command.
    from dstf.cli.parser import command_path

    if path in COMMANDS:
        assert command_path(args) == path
    elif path:
        assert command_path(args).startswith(path + " ")


def _dashboard_kinds() -> list[str]:
    """The dashboard's Command menu: every command it can offer."""
    from dstf.reporting.dashboard_render import COMMAND_KINDS

    kinds = [kind for kind, _, _ in COMMAND_KINDS]
    assert "run" in kinds and "author" in kinds and len(kinds) >= 5
    return kinds


#: Each way a Modelica project's run can be started: the directory it
#: starts in (under the test's project) and the flags that say where the
#: project is. None simulates here: the run's Config is built as `run`
#: builds it.
_STARTED = {
    "--config": (".", ["--config", "testing.json"]),
    "--config, a directory": (".", ["--config", "."]),
    "--source-path and --reference-root": (
        ".",
        ["--source-path", "Lib", "--reference-root", "refs"],
    ),
    "--source-path, testing.json in the reference root": (
        "A",
        ["--source-path", "Lib", "--reference-root", "cfgrefs"],
    ),
    # the run writes a default one beside the library
    "--source-path, no testing.json anywhere": ("bare", ["--source-path", "Lib"]),
    "the current directory": (".", []),
    # testing.json names no library: the package is found from the cwd
    "the library is the current directory": ("nosrc/Lib", ["--config", ".."]),
}

#: Where a Config says the project is: everything a command that is to do
#: the same thing to the same project must read the same.
_WHERE = (
    "config_found",
    "source_path",
    "reference_root",
    "spec_path",
    "work_dir",
    "backend_name",
)


def _where(config) -> dict:
    return {name: getattr(config, name) for name in _WHERE}


@pytest.fixture
def built_configs(monkeypatch):
    """Every Config built from here on, in order."""
    from dstf.config import Config

    built: list = []
    post_init = Config.__post_init__

    def spy(self):
        post_init(self)
        built.append(self)

    monkeypatch.setattr(Config, "__post_init__", spy)
    return built


def _page_flags(page: Path) -> dict[str, str]:
    """The flags the dashboard puts after each command it offers, by the
    command, as its page holds them; the page's menu offers exactly those."""
    import json

    html = page.read_text(encoding="utf-8")
    found = re.search(r"const RERUN_FLAGS = (.*);", html)
    assert found, "the page names no flags for its commands"
    flags = json.loads(found.group(1))
    menu = html.split('<select id="cmd-kind"', 1)[1].split("</select>", 1)[0]
    offered = re.findall(r'<option value="([^"]+)">', menu)
    assert sorted(offered) == sorted(flags)
    return {kind: flags[kind] for kind in offered}


def _check_the_built_commands(
    flags: dict[str, str],
    run_config,
    elsewhere: Path,
    built,
    capsys,
    monkeypatch,
    models: str = "Lib.A,Lib.B",
) -> None:
    """Each command of the Command menu, built as the page builds it
    (`dashboard.js`: ``dstf <kind> <flags> --filter …``, and ` --merge
    --report` after a plain `run`), parses; `compare`, executed from
    another directory, reads the project the run read."""
    monkeypatch.chdir(elsewhere)
    assert set(flags) <= set(_dashboard_kinds())
    lines = {}
    for kind, after in flags.items():
        tail = " --merge --report" if kind == "run" else ""
        line = f'dstf {kind} {after} --filter "{models}"{tail}'
        argv = shlex.split(line)
        args = _parse(argv[1:], capsys)
        assert isinstance(args, argparse.Namespace), f"{line}\n{args}"
        assert args.filter == models
        lines[kind] = argv
        words = shlex.split(after)
        for flag, value in zip(words, words[1:]):
            if flag in PROJECT_FLAGS.values() or flag == "--work-dir":
                assert Path(value).is_absolute(), f"{flag} {value}"
    built.clear()
    main(lines["compare"][1:])
    capsys.readouterr()
    assert built, "compare built no Config"
    assert _where(built[-1]) == _where(run_config), " ".join(lines["compare"])


@pytest.mark.parametrize("started", sorted(_STARTED))
def test_every_command_the_dashboard_builds_reads_the_run_s_project(
    started, tmp_path, monkeypatch, capsys, built_configs
):
    """After a run started any way at all, the commands the dashboard
    builds for it parse, and `compare` — pasted into a terminal in another
    directory — reads the testing.json, library, reference root, spec and
    work dir the run read. A run made with --source-path and
    --reference-root built `dstf --source-path … author`, which exits 2."""
    from dstf.cli.common import _build_config
    from dstf.reporting.dashboard_render import build_rerun_flags

    project = tmp_path / "project"
    for lib in ("Lib", "A/Lib", "bare/Lib", "nosrc/Lib"):
        (project / lib).mkdir(parents=True)
        (project / lib / "package.mo").write_text("within ;\npackage Lib\nend Lib;\n")
    (project / "refs").mkdir()
    (project / "A" / "cfgrefs").mkdir()
    (project / "testing.json").write_text(
        '{"source_path": "Lib", "backend": "OpenModelica"}'
    )
    (project / "A" / "cfgrefs" / "testing.json").write_text(
        '{"source_path": "../Lib", "backend": "OpenModelica"}'
    )
    (project / "nosrc" / "testing.json").write_text('{"backend": "OpenModelica"}')
    cwd, flags = _STARTED[started]
    monkeypatch.chdir(project / cwd)
    config = _build_config(build_arg_parser().parse_args(["run", *flags]))
    # as `run` does once past its checks, before its dashboard
    config.write_default_file()
    capsys.readouterr()
    assert config.config_found is not None, "a run always has a testing.json"
    assert config.config_found.is_file()
    built = build_rerun_flags(config)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _check_the_built_commands(
        built,
        config,
        elsewhere,
        built_configs,
        capsys,
        monkeypatch,
    )


_CONSTANT_CSV = "PythonTestingLib.Examples.ConstantCsv"

#: What the page says where it offers no `author`.
_NO_AUTHOR = (
    "No author command: dstf author reads a testing.json, --work-dir and "
    "--backend only, and this run used more"
)

#: Each override a run of the Python example can be given beside its
#: testing.json, relative to the directory the run starts in — and whether
#: `author`, which reads --config, --backend and --work-dir alone, can be
#: offered on the same project.
_OVERRIDES = {
    "none": ([], True),
    "--reference-root": (["--reference-root", "alt"], False),
    "--test-spec": (["--test-spec", "lib/Resources/Alt/test_spec.json"], False),
    "--source-path": (["--source-path", "lib"], False),
    "--work-dir": (["--work-dir", "work"], True),
    # an entry of `backends` whose name has a space: its work dir differs
    "--backend": (["--backend", "Python alt"], True),
    "every one": (
        [
            "--reference-root",
            "alt",
            "--test-spec",
            "lib/Resources/Alt/test_spec.json",
            "--source-path",
            "lib",
            "--work-dir",
            "work",
            "--backend",
            "Python alt",
        ],
        False,
    ),
}


@pytest.mark.parametrize("given", sorted(_OVERRIDES))
@pytest.mark.parametrize("config_given", [True, False], ids=["--config", "found"])
def test_a_run_s_dashboard_commands_do_what_the_run_did_from_anywhere(
    given, config_given, tmp_path, monkeypatch, capsys, built_configs
):
    """A run of the Python example with overrides beside its testing.json:
    the dashboard it writes builds commands that carry every one, absolute,
    and `compare`, executed from another directory, reads the project the
    run read. A run with `--reference-root ALT` built `dstf --config C
    compare --accept`, which wrote into C's own root and left ALT alone;
    `author` is not offered where it cannot read an override, and the page
    says why."""
    import json
    import shutil

    from conftest import PROJECT_ROOT

    project = tmp_path / "project"
    lib = project / "lib"
    shutil.copytree(
        PROJECT_ROOT / "examples" / "python" / "PythonTestingLib",
        lib,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    refs = lib / "Resources" / "ReferenceResults"
    cfg = json.loads((refs / "testing.json").read_text(encoding="utf-8"))
    cfg["backends"]["Python alt"] = cfg["backends"]["Python"]
    (refs / "testing.json").write_text(json.dumps(cfg), encoding="utf-8")
    (lib / "Resources" / "Alt").mkdir()
    shutil.copy(refs / "test_spec.json", lib / "Resources" / "Alt" / "test_spec.json")
    (project / "alt").mkdir()

    overrides, author = _OVERRIDES[given]
    if config_given:
        monkeypatch.chdir(project)
        argv = ["--config", "lib/Resources/ReferenceResults/testing.json"]
    else:
        # found by the search, in the directory the run starts in
        monkeypatch.chdir(refs)
        argv = []
        overrides = [
            str(Path("../../..") / v) if v.startswith(("alt", "lib", "work")) else v
            for v in overrides
        ]
    built_configs.clear()
    main(["run", *argv, *overrides, "--filter", _CONSTANT_CSV, "--no-browser"])
    capsys.readouterr()
    run_config = next(c for c in built_configs if c.config_found is not None)
    assert run_config.config_found == refs / "testing.json"
    page = run_config.work_dir / "dashboard.html"
    flags = _page_flags(page)
    html = page.read_text(encoding="utf-8")
    assert ("author" in flags) is author, flags
    assert (_NO_AUTHOR in html) is not author

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _check_the_built_commands(
        flags,
        run_config,
        elsewhere,
        built_configs,
        capsys,
        monkeypatch,
        models=_CONSTANT_CSV,
    )
    # what it executed: the run's results scored again, in the run's work dir
    verdicts = json.loads((run_config.work_dir / "verdicts.json").read_text())
    assert [t["model_id"] for t in verdicts["tests"]] == [_CONSTANT_CSV]


#: Each setting a run can be given that changes what a command does to the
#: project, beside the places: its flags (``WRAPPER`` stands for an
#: interpreter that logs each start), and the Config field and value a
#: pasted command must read.
_SETTINGS = {
    "--backend-path": (["--backend-path", "WRAPPER"], "backend_path", "WRAPPER"),
    "--tolerance": (["--tolerance", "0.5"], "tolerance", 0.5),
    "--timeout": (["--timeout", "37"], "timeout", 37),
    "--default-points": (["--default-points"], "default_points", True),
}


@pytest.mark.parametrize("given", sorted(_SETTINGS))
def test_the_dashboard_s_commands_carry_the_run_s_settings(
    given, tmp_path, monkeypatch, capsys, built_configs
):
    """A run of the Python example given one of the settings above: the
    dashboard's `run` and `compare`, executed from another directory, read
    it as the run did — the run's interpreter simulates again, the run's
    tolerance scores — and `compare --accept` and `author` carry none that
    they do not read. A pasted `run` after `--backend-path WRAPPER`
    simulated with the default python3; a pasted `compare` after
    `--tolerance 0.5` scored at testing.json's 1e-2."""
    import json
    import shutil
    import sys

    from conftest import PROJECT_ROOT

    lib = tmp_path / "lib"
    shutil.copytree(
        PROJECT_ROOT / "examples" / "python" / "PythonTestingLib",
        lib,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    log = tmp_path / "starts.log"
    wrapper = tmp_path / "py.sh"
    wrapper.write_text(f'#!/bin/sh\necho x >> "{log}"\nexec "{sys.executable}" "$@"\n')
    wrapper.chmod(0o755)

    def starts() -> int:
        return len(log.read_text().splitlines()) if log.exists() else 0

    flags, field, value = _SETTINGS[given]
    flags = [str(wrapper) if f == "WRAPPER" else f for f in flags]
    value = str(wrapper) if value == "WRAPPER" else value
    monkeypatch.chdir(tmp_path)
    cfg = "lib/Resources/ReferenceResults/testing.json"
    built_configs.clear()
    main(["run", "--config", cfg, *flags, "--filter", _CONSTANT_CSV, "--no-browser"])
    capsys.readouterr()
    run_config = next(c for c in built_configs if c.config_found is not None)
    assert getattr(run_config, field) == value
    ran = starts()
    assert (ran > 0) is (given == "--backend-path")
    page = run_config.work_dir / "dashboard.html"
    commands = _page_commands(page, _CONSTANT_CSV)
    assert "author" not in commands, "author reads none of the run's settings"
    # what each reads (`cli.parser.READS`): compare simulates nothing, and
    # an accept scores nothing
    carried = {
        "run": True,
        "run --rerun failed,sim-failed": True,
        "compare": given != "--timeout",
        "compare --accept": given == "--backend-path",
    }
    for kind, line in commands.items():
        assert (flags[0] in line) is carried[kind], line
        args = _parse(line[1:], capsys)
        assert isinstance(args, argparse.Namespace), f"{line}\n{args}"

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    for kind in ("run", "compare"):
        built_configs.clear()
        main(commands[kind][1:])
        capsys.readouterr()
        pasted = next(c for c in built_configs if c.config_found is not None)
        assert pasted.work_dir == run_config.work_dir
        if carried[kind]:
            assert getattr(pasted, field) == value, " ".join(commands[kind])
        if kind == "run" and given == "--backend-path":
            assert starts() > ran, "the pasted run simulated with another python"
    verdicts = json.loads((run_config.work_dir / "verdicts.json").read_text())
    assert [t["model_id"] for t in verdicts["tests"]] == [_CONSTANT_CSV]


def _page_commands(page: Path, models: str) -> dict[str, list[str]]:
    """Each command of the page's Command menu as its builder writes it for
    ``models`` (`dashboard.js`: ``dstf <kind> <flags> --filter …``, and
    ``--merge --report`` after a plain `run`), split into words."""
    lines = {}
    for kind, after in _page_flags(page).items():
        tail = " --merge --report" if kind == "run" else ""
        lines[kind] = shlex.split(f'dstf {kind} {after} --filter "{models}"{tail}')
    return lines


def test_with_no_testing_json_the_dashboard_offers_no_author(tmp_path, monkeypatch):
    """A Config built with no testing.json found and none written (the run
    as a library call, `run_pipeline`): the commands name the library, the
    reference root and the work dir, absolute, and `author` — which needs a
    testing.json — is not offered; the page says why."""
    from dstf.config import Config
    from dstf.reporting.dashboard_render import build_rerun_flags, render_live

    lib = tmp_path / "Lib"
    lib.mkdir()
    (lib / "package.mo").write_text("within ;\npackage Lib\nend Lib;\n")
    monkeypatch.chdir(tmp_path)
    config = Config(source_path=Path("Lib"), backend_name="OpenModelica")
    assert config.config_found is None
    flags = build_rerun_flags(config)
    assert "author" not in flags
    for kind in ("run", "compare", "compare --accept"):
        assert shlex.split(flags[kind]) == [
            "--source-path",
            str(lib),
            "--reference-root",
            str(config.reference_root),
            "--work-dir",
            str(config.work_dir),
            "--backend",
            "OpenModelica",
        ], kind
    render_live(tmp_path / "w", rerun_flags=flags)
    page = (tmp_path / "w" / "dashboard.html").read_text(encoding="utf-8")
    assert '<option value="author">' not in page
    assert '<option value="run">' in page
    assert _NO_AUTHOR in page


# ---------------------------------------------------------------------------
# Fence 4 — a command offers exactly the flags it reads
# ---------------------------------------------------------------------------

_PROJECT = set(PROJECT_FLAGS.values())
_STORE = {"--config", "--source-path", "--reference-root"}
#: Offered after the command only (a command that reads one), never before.
_AFTER_ONLY = {"--backend", "--backend-path", "--work-dir"}

#: What each command reads, written here from the code that runs it — NOT
#: read back from the parser: the flags that locate the project, the backend
#: (the simulator, or the reference partition it names) and the work dir.
READS: dict[str, set[str]] = {
    # the library and the spec; no reference partition, no work dir
    "discover": _PROJECT,
    "run": _PROJECT | _AFTER_ONLY,
    "compare": _PROJECT | _AFTER_ONLY,
    # the Session builds its Config from testing.json alone
    "author": {"--config", "--backend", "--work-dir"},
    # the search for a testing.json; never the spec, a partition or a work dir
    "check": _STORE,
    "spec add": _PROJECT,
    # a patched tree is validated against the partition's baseline roles
    "spec update": _PROJECT | {"--backend"},
    # the schema is the installation's: no project
    "spec schema": set(),
    # the default output file is in the work dir
    "baseline export": _STORE | {"--backend", "--work-dir"},
    "soft-check add": _STORE | {"--backend"},
    "soft-check list": _STORE | {"--backend"},
    "soft-check remove": _STORE | {"--backend"},
    "companion add": _STORE | {"--backend"},
    "companion list": _STORE | {"--backend"},
    "companion freeze": _STORE | {"--backend"},
    "companion remove": _STORE | {"--backend"},
    "manifest show": _STORE | {"--backend"},
    # reference_manifest.json is written to the work dir
    "manifest dump": _STORE | {"--backend", "--work-dir"},
    # --orphans discovers the tests and cleans the work dir
    "manifest cleanup": _PROJECT | {"--backend", "--work-dir"},
}

#: The least each command parses with.
ARGV: dict[str, list[str]] = {
    # its optional positional given, so a value after a refused flag is not
    # taken for it
    "check": ["Dymola"],
    "spec schema": ["test_spec"],
    "spec add": ["Lib.M"],
    "spec update": ["patch.json"],
    "soft-check add": ["Lib.M", "other", "ref.json"],
    "soft-check remove": ["Lib.M", "other"],
    "companion add": ["Lib.M", "rig", "rig.csv"],
    "companion freeze": ["Lib.M", "rig"],
    "companion remove": ["Lib.M", "rig"],
    # --test-spec and --work-dir are read with --orphans only
    "manifest cleanup": ["--orphans"],
}

_FLAGS = sorted(_PROJECT | _AFTER_ONLY)


def _dest(flag: str) -> str:
    return flag.lstrip("-").replace("-", "_")


def _parse(argv: list[str], capsys) -> argparse.Namespace | str:
    """The namespace, or the usage error's text."""
    try:
        return build_arg_parser().parse_args(argv)
    except SystemExit as exc:
        assert exc.code == 2
        return capsys.readouterr().err


def test_the_table_names_every_command():
    assert sorted(READS) == COMMANDS
    assert set(ARGV) <= set(COMMANDS)


def test_the_code_s_table_agrees_with_this_one():
    """`cli.parser.READS` is what the tree is built from; this file's table
    of the project, backend and work-dir flags is written from the code
    that runs each command. The two say the same."""
    from dstf.cli.parser import READS as CODE

    assert sorted(CODE) == COMMANDS
    for command, flags in READS.items():
        assert {f for f in _FLAGS if _dest(f) in CODE[command]} == flags, command


def test_a_flag_the_table_does_not_name_cannot_be_offered(monkeypatch):
    """`discover` offering `--tolerance`, `manifest show` offering
    `--filter`: the tree refuses to build."""
    from dstf.cli import parser

    reads = dict(parser.READS)
    reads["discover"] = tuple(d for d in reads["discover"] if d != "package")
    monkeypatch.setattr(parser, "READS", reads)
    with pytest.raises(AssertionError, match="`dstf discover` offers"):
        build_arg_parser()


# --- each command's handler reads what the table says it reads -------------


def _functions(module) -> dict:
    import inspect

    return {
        name: fn
        for name, fn in vars(module).items()
        if inspect.isfunction(fn) and fn.__module__.startswith("dstf.cli")
    }


def _handler_reads(fn, seen=None) -> set[str]:
    """The attributes of ``args`` a handler reads: ``args.x`` and
    ``getattr(args, "x", …)`` in its body, and in every function of
    ``dstf.cli`` it hands ``args`` to, followed by name.

    Its limits: ``_build_config(args)`` counts as reading every flag that
    reaches Config (it reads them in a loop over ``_CONFIG_FLAGS``), whether
    or not the command then uses the Config field; a read in a branch that
    never runs counts; ``args`` passed on under another name, or read
    through ``vars(args)``, is not seen."""
    import ast
    import inspect
    import sys
    import textwrap

    from dstf.cli.common import _CONFIG_FLAGS

    seen = set() if seen is None else seen
    if fn in seen:
        return set()
    seen.add(fn)
    if fn.__name__ == "_build_config":
        return set(_CONFIG_FLAGS) | {"default_points", "may_create_project"}
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    module = _functions(sys.modules[fn.__module__])
    reads: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "args"
        ):
            reads.add(node.attr)
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        given = [*node.args, *(k.value for k in node.keywords)]
        if not any(isinstance(a, ast.Name) and a.id == "args" for a in given):
            continue
        if node.func.id == "getattr" and isinstance(node.args[1], ast.Constant):
            reads.add(node.args[1].value)
        elif node.func.id in module:
            reads |= _handler_reads(module[node.func.id], seen)
    return reads


@pytest.mark.parametrize("command", COMMANDS)
def test_the_handler_reads_every_flag_the_command_offers(command):
    """A flag offered and not read is the silent drop this tree exists to
    refuse: `author` passing `backend=None` on, `manifest show` listing the
    obsolete references without asking `--show-obsolete`."""
    from dstf.cli.main import _COMMANDS
    from dstf.cli.parser import READS as CODE

    reads = _handler_reads(_COMMANDS[command])
    unread = sorted(set(CODE[command]) - reads)
    assert not unread, f"`dstf {command}` offers {unread} and its handler reads none"


@pytest.mark.parametrize("command", COMMANDS)
@pytest.mark.parametrize("flag", _FLAGS)
def test_a_flag_is_offered_where_it_is_read_and_an_error_elsewhere(
    command, flag, capsys
):
    words = command.split() + ARGV.get(command, [])
    after = _parse([*words, flag, "VALUE"], capsys)
    before = _parse([flag, "VALUE", *words], capsys)
    if flag in READS[command]:
        assert isinstance(after, argparse.Namespace), after
        assert getattr(after, _dest(flag)) == "VALUE"
        if flag in _PROJECT:
            # either side of the command, to the same value
            assert isinstance(before, argparse.Namespace), before
            assert getattr(before, _dest(flag)) == "VALUE"
        else:
            # after the command only: the top level does not know it
            assert isinstance(before, str)
    else:
        assert isinstance(after, str), f"`dstf {command}` took {flag} and dropped it"
        assert f"dstf {command}: error:" in after and flag in after
        assert isinstance(before, str), (
            f"{flag} before `dstf {command}` was taken and dropped"
        )
        if flag in _PROJECT:
            # said by the command, in words, on either side: before it,
            # argparse cannot see this one; after it, argparse would say
            # no more than "unrecognized"
            assert f"dstf {command}: error: {flag} does not apply" in before
            assert f"dstf {command}: error: {flag} does not apply" in after


@pytest.mark.parametrize("command", COMMANDS)
def test_the_help_lists_exactly_the_flags_it_reads(command, capsys):
    """A flag that parses and is not in the help is not discoverable; one in
    the help that the command does not read is a promise it cannot keep."""
    with pytest.raises(SystemExit):
        build_arg_parser().parse_args([*command.split(), "--help"])
    out = capsys.readouterr().out
    options = out.split("\noptions:\n", 1)[1]
    offered = {flag for flag in _FLAGS if re.search(rf"^  {flag}( |$)", options, re.M)}
    assert offered == READS[command]


def test_a_flag_before_the_command_wins_nothing_it_is_not_read_for(capsys):
    text = _parse(["--test-spec", "spec.json", "author", "--config", "t.json"], capsys)
    assert isinstance(text, str) and "--test-spec does not apply" in text
    assert "it reads --config" in text


#: Every option string of the tree, whichever command has it.
_EVERY_FLAG = {
    s for node in NODES.values() for a in node._actions for s in a.option_strings
}


@pytest.mark.parametrize("path", sorted(NODES))
def test_no_flag_is_taken_by_a_prefix_of_its_name(path, capsys):
    """argparse's abbreviations are off at every node: `compare --a` was
    `--accept` (and stored the baselines), `manifest cleanup --o --a` was
    `--orphans --apply`. Every prefix of every long flag is an unknown
    flag, unless it is a flag of that node itself (`--report` of
    `--report-format`)."""
    node = NODES[path]
    own = {s for a in node._actions for s in a.option_strings}
    words = path.split() + ARGV.get(path, [])
    longs = sorted(s for s in own if s.startswith("--") and s != "--help")
    assert longs or path in GROUPS
    for flag in longs:
        for end in range(3, len(flag)):
            prefix = flag[:end]
            if prefix in own:
                continue
            # `=VALUE` so that the prefix of a flag taking no value is not
            # refused for its value instead; the root takes its own before
            # a command
            argv = (
                [f"{prefix}=VALUE", "discover"]
                if not path
                else [*words, f"{prefix}=VALUE"]
            )
            text = _parse(argv, capsys)
            assert isinstance(text, str), f"`dstf {path}` took {prefix} for {flag}"
            unknown = f"unrecognized arguments: {prefix}=VALUE" in text
            # before the command, another command's flag (`--source` of
            # `spec add`, a prefix of `--source-path`) is said as the flag
            # it is
            elsewhere = prefix in _EVERY_FLAG and f"{prefix} does not apply" in text
            assert unknown or elsewhere, text


@pytest.mark.parametrize(
    ("argv", "said"),
    [
        (
            ["--backend", "Python", "run", "--no-browser"],
            "dstf run: error: --backend goes after the command: dstf run --backend …",
        ),
        (
            ["--batch", "run"],
            "dstf run: error: --batch goes after the command: dstf run --batch …",
        ),
        (
            ["--work-dir=w", "manifest", "dump"],
            "dstf manifest dump: error: --work-dir goes after the command: "
            "dstf manifest dump --work-dir …",
        ),
        # between a group and its action
        (
            ["spec", "--config", "C", "add", "M"],
            "dstf spec add: error: --config goes after the command: "
            "dstf spec add --config …",
        ),
        (
            ["-i", "failed", "run"],
            "dstf run: error: --interactive goes after the command: "
            "dstf run --interactive …",
        ),
        # before a command that does not read it at all
        (
            ["--batch", "compare"],
            "dstf compare: error: --batch does not apply: `dstf compare` does "
            "not read it (it is a flag of: run)",
        ),
        # no command at all
        (
            ["--backend", "Python"],
            "dstf: error: --backend goes after the command: dstf <command> --backend …",
        ),
    ],
    ids=lambda v: " ".join(v) if isinstance(v, list) else "",
)
def test_a_command_s_flag_before_the_command_says_where_it_goes(argv, said, capsys):
    """`dstf --backend Python run` was `invalid choice: 'Python'`, `dstf
    --batch run` `unrecognized arguments: --batch` under a usage that lists
    `[--batch]`, `dstf spec --config C add M` `invalid choice: 'C'`."""
    text = _parse(argv, capsys)
    assert isinstance(text, str), f"{argv} parsed"
    assert said in text, text
    assert "invalid choice" not in text


@pytest.mark.parametrize(
    ("argv", "said"),
    [
        # a flag's value that is a command's name is the value, not the command
        (
            ["--filter", "check", "run"],
            "dstf run: error: --filter goes after the command: dstf run --filter …",
        ),
        (
            ["--work-dir", "check", "run"],
            "dstf run: error: --work-dir goes after the command: dstf run --work-dir …",
        ),
        (
            ["--work-dir=check", "run"],
            "dstf run: error: --work-dir goes after the command: dstf run --work-dir …",
        ),
        # a word that is no command, or no action of its group, is argparse's
        (
            ["spec", "ad", "M", "--config", "C"],
            "dstf spec: error: argument <action>: invalid choice: 'ad'",
        ),
        (
            ["--backend", "Python", "rn"],
            "dstf: error: argument <command>: invalid choice: 'rn'",
        ),
        # a flag whose value is optional takes ONE word: the next is the
        # command (not "--interactive goes after the command", every word
        # up to `run` swallowed)
        (
            ["-i", "failed", "extra", "run"],
            "dstf: error: argument <command>: invalid choice: 'extra'",
        ),
        (
            ["--rerun", "failed", "extra", "run"],
            "dstf: error: argument <command>: invalid choice: 'extra'",
        ),
    ],
    ids=lambda v: " ".join(v) if isinstance(v, list) else "",
)
def test_the_command_word_is_one_that_is_there(argv, said, capsys):
    """`dstf --filter check run` was `--filter does not apply: dstf check
    does not read it` — the value `check` taken for the command; `dstf spec
    ad M --config C` was `--config goes after the command`, where the word
    the user mistyped is `ad`."""
    text = _parse(argv, capsys)
    assert isinstance(text, str), f"{argv} parsed"
    assert said in text, text


@pytest.mark.parametrize("flag", sorted(_PROJECT))
@pytest.mark.parametrize("spelled", ["{flag} {value}", "{flag}={value}"])
def test_a_project_flag_with_two_values_is_refused(flag, spelled, capsys):
    """Given before and after the command, the later won, said only in the
    usage guide."""

    def given(value):
        return spelled.format(flag=flag, value=value).split()

    text = _parse([*given("a.json"), "discover", *given("b.json")], capsys)
    assert isinstance(text, str), f"{flag} took two values"
    assert (
        f"dstf discover: error: {flag} is given twice, a.json before the "
        f"command and b.json after it: write it once"
    ) in text
    # the same value on both sides, or one side twice (argparse's last wins)
    args = _parse([*given("a.json"), "discover", *given("a.json")], capsys)
    assert getattr(args, _dest(flag)) == "a.json"
    # the same path, spelled two ways
    for other in ("./a.json", str(Path("a.json").resolve())):
        args = _parse([*given("a.json"), "discover", *given(other)], capsys)
        assert isinstance(args, argparse.Namespace), f"{flag} a.json / {other}"
    args = _parse(["discover", *given("a.json"), *given("b.json")], capsys)
    assert getattr(args, _dest(flag)) == "b.json"


def test_a_directory_and_its_testing_json_are_one_config(tmp_path, capsys):
    """`--config DIR discover --config DIR/testing.json` was refused as
    given twice: both name the one file, as Config reads them (a directory
    is its testing.json). Another file in it is still two values."""
    (tmp_path / "testing.json").write_text("{}")
    (tmp_path / "other.json").write_text("{}")
    for one, other in (
        (tmp_path, tmp_path / "testing.json"),
        (tmp_path / "testing.json", tmp_path),
    ):
        args = _parse(
            ["--config", str(one), "discover", "--config", str(other)], capsys
        )
        assert isinstance(args, argparse.Namespace), f"{one} / {other}: {args}"
    text = _parse(
        [
            "--config",
            str(tmp_path),
            "discover",
            "--config",
            str(tmp_path / "other.json"),
        ],
        capsys,
    )
    assert isinstance(text, str) and "--config is given twice" in text


def test_the_top_level_help_says_a_flag_is_written_once():
    text = " ".join(PARSER.format_help().split())
    assert "Each is written once, on either side." in text


def test_a_flag_no_command_has_is_still_argparse_s_to_say(capsys):
    """Where a flag of the tree goes is said; a name that is no flag
    anywhere (a removed one too) gets no hint."""
    text = _parse(["--bogus", "run"], capsys)
    assert isinstance(text, str) and "goes after" not in text
    assert "--bogus" in text


def test_a_refused_flag_builds_no_config(tmp_path, monkeypatch, capsys):
    """The refusal is the parser's: nothing is resolved, so nothing — a
    default testing.json beside the library — is written."""
    lib = tmp_path / "X"
    lib.mkdir()
    (lib / "package.mo").write_text("package X\nend X;\n", encoding="utf-8")
    monkeypatch.chdir(lib)
    for argv in (
        ["--test-spec", "s.json", "baseline", "export"],
        ["manifest", "show", "--work-dir", "w"],
        ["--config", "t.json", "spec", "schema"],
    ):
        with pytest.raises(SystemExit) as exc:
            main(argv)
        assert exc.value.code == 2
        capsys.readouterr()
    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*")) == [
        "X",
        "X/package.mo",
    ]


# --- a group, and an action's own flags ------------------------------------


@pytest.mark.parametrize("group", GROUPS)
def test_a_group_with_no_action_prints_its_help_and_exits_2(group, capsys):
    with pytest.raises(SystemExit) as exc:
        main([group])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert err.startswith(f"usage: dstf {group} [-h] <action> ...")
    assert NODES[group].description in err and "\nactions:\n" in err
    assert err.rstrip().endswith(
        f"dstf {group}: error: the following arguments are required: <action>"
    )


#: The 1.0 command lines, one per line (and unquoted, so that a search of the
#: repository for an old name finds its history and nothing else).
_OLD_NAMES = """
add Lib.M
spec-update patch.json
export-schema
export
import-baseline Lib.M other ref.json
check-dymola
"""


@pytest.mark.parametrize("line", _OLD_NAMES.split("\n")[1:-1])
def test_an_old_command_name_is_an_invalid_choice_and_nothing_more(line, capsys):
    """A clean break: no alias, no hidden spelling, no "renamed" hint."""
    with pytest.raises(SystemExit) as exc:
        main(line.split())
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert f"invalid choice: '{line.split()[0]}'" in err
    assert "renamed" not in err and "instead" not in err


@pytest.mark.parametrize(
    "argv",
    [
        # --orphans / --apply are cleanup's
        ["manifest", "show", "--orphans"],
        ["manifest", "dump", "--apply"],
        ["manifest", "cleanup", "--show-obsolete"],
        # --map and --format are `companion add`'s
        ["companion", "list", "--map", "x=y"],
        ["companion", "freeze", "Lib.M", "rig", "--map", "x=y"],
        ["companion", "remove", "Lib.M", "rig", "--format", "csv"],
        # a selection where nothing is selected
        ["baseline", "export", "--filter", "A*"],
        ["check", "Dymola", "--filter", "A*"],
        # the run's alone
        ["compare", "--batch"],
        ["compare", "--timeout", "5"],
        ["compare", "--rerun"],
        ["author", "--backend-path", "/x/julia"],
        ["author", "--tolerance", "0.1"],
    ],
    ids=" ".join,
)
def test_a_flag_of_another_action_is_an_error(argv, capsys):
    text = _parse(argv, capsys)
    assert isinstance(text, str) and "unrecognized arguments" in text


# ---------------------------------------------------------------------------
# One definition per flag
# ---------------------------------------------------------------------------


def _actions(option: str):
    return [
        (path, action)
        for path, node in NODES.items()
        for action in node._actions
        if option in action.option_strings
    ]


#: Each command that writes to --output, with what it needs beside it.
_OUTPUTS = {
    "run": ["--report-format", "junit"],
    "compare": ["--report-format", "junit"],
    "spec schema": [],
    "baseline export": [],
}


@pytest.mark.parametrize("command", sorted(_OUTPUTS))
def test_an_output_that_cannot_be_written_is_refused_before_any_work(
    command, tmp_path, monkeypatch, capsys
):
    """`compare --report-format junit -o <dir>` ended in a traceback after
    scoring, `spec schema -o missing/x.json` in a FileNotFoundError: a run
    of an hour must not end by failing to write its report."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "adir").mkdir()
    words = [*command.split(), *_OUTPUTS[command]]
    text = _parse([*words, "-o", "adir"], capsys)
    assert isinstance(text, str), f"{command} took a directory"
    assert "argument -o/--output: adir is a directory: name a file to write" in text, (
        text
    )
    text = _parse([*words, "-o", "missing/x.json"], capsys)
    assert isinstance(text, str), f"{command} took a file in no directory"
    assert (
        "argument -o/--output: missing/x.json: the directory missing does not exist"
        in text
    ), text
    # a new file in a directory that is there
    args = _parse([*words, "-o", "adir/x.json"], capsys)
    assert isinstance(args, argparse.Namespace) and args.output == "adir/x.json"


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0,
    reason="permissions are not enforced for root, nor POSIX modes on Windows",
)
@pytest.mark.parametrize("command", sorted(_OUTPUTS))
def test_an_output_without_write_permission_is_refused_before_any_work(
    command, tmp_path, monkeypatch, capsys
):
    """`run --report-format junit -o <read-only dir>/x.xml` ran every test
    and ended in a PermissionError traceback, exit 1; a read-only existing
    file the same."""
    monkeypatch.chdir(tmp_path)
    ro = tmp_path / "ro"
    ro.mkdir()
    (ro / "old.xml").write_text("")
    (ro / "old.xml").chmod(0o444)
    ro.chmod(0o555)
    writable = tmp_path / "rw.xml"
    writable.write_text("")
    (tmp_path / "rofile.xml").write_text("")
    (tmp_path / "rofile.xml").chmod(0o444)
    words = [*command.split(), *_OUTPUTS[command]]
    try:
        for name, said in (
            ("ro/x.xml", "ro/x.xml: the directory ro cannot be written"),
            ("ro/old.xml", "ro/old.xml cannot be written"),
            ("rofile.xml", "rofile.xml cannot be written"),
        ):
            text = _parse([*words, "-o", name], capsys)
            assert isinstance(text, str), f"{command} took {name}"
            assert f"argument -o/--output: {said}" in text, text
        # an existing file that can be written is overwritten
        args = _parse([*words, "-o", "rw.xml"], capsys)
        assert isinstance(args, argparse.Namespace)
    finally:
        ro.chmod(0o755)


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0,
    reason="permissions are not enforced for root, nor POSIX modes on Windows",
)
def test_manifest_dump_into_a_work_dir_it_cannot_write_is_one_sentence(
    tmp_path, capsys
):
    """`manifest dump --work-dir <read-only dir>` ended in a PermissionError
    traceback."""
    from conftest import python_example_copy

    config, _ = python_example_copy(tmp_path)
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o555)
    try:
        rc = main(
            ["manifest", "dump", "--config", str(config.config_file)]
            + ["--work-dir", str(ro)]
        )
    finally:
        ro.chmod(0o755)
    err = capsys.readouterr().err
    assert rc == 2
    assert err.strip() == (
        f"Config error: {ro / 'reference_manifest.json'} cannot be written: "
        "Permission denied"
    ), err


@pytest.mark.parametrize(
    ("flag", "said"),
    [("--test-spec", "no such file"), ("--work-dir", "no such directory")],
)
def test_manifest_cleanup_orphans_of_a_path_that_is_not_there_is_refused(
    flag, said, tmp_path, capsys
):
    """`manifest cleanup --orphans --work-dir <missing>` said `No orphan
    manifest entries.`, exit 0 — of a work dir it never read."""
    from conftest import python_example_copy

    config, _ = python_example_copy(tmp_path)
    missing = tmp_path / "missing" / ("spec.json" if flag == "--test-spec" else "w")
    rc = main(
        ["manifest", "cleanup", "--orphans", "--config", str(config.config_file)]
        + [flag, str(missing)]
    )
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.err.strip() == f"Config error: {flag} {missing}: {said}"
    assert "No orphan" not in captured.out


def test_soft_check_add_s_source_that_holds_no_object_is_said(tmp_path, capsys):
    """A source file holding a JSON list ended in an AttributeError
    traceback. It is the command's data error, exit 1, naming the file."""
    (tmp_path / "testing.json").write_text(
        '{"source_type": "python", "backend": "Python"}'
    )
    source = tmp_path / "list.json"
    source.write_text("[1, 2]")
    rc = main(
        ["soft-check", "add", "Lib.A", "om", str(source)]
        + ["--config", str(tmp_path / "testing.json")]
    )
    assert rc == 1
    out = capsys.readouterr().out
    assert f"Failed to read {source}: it holds a JSON list, not an object" in out


def test_output_has_its_short_form_everywhere():
    found = _actions("--output")
    assert [path for path, _ in found] == [
        "run",
        "compare",
        "spec schema",
        "baseline export",
    ]
    for path, action in found:
        assert action.option_strings == ["-o", "--output"], path


def test_format_lists_its_choices_in_one_order():
    found = _actions("--format")
    assert [path for path, _ in found] == ["baseline export", "companion add"]
    assert {tuple(action.choices) for _, action in found} == {("json", "csv")}


def test_no_browser_is_one_flag():
    helps = {path: action.help for path, action in _actions("--no-browser")}
    assert sorted(helps) == ["author", "compare", "run"]
    assert {re.sub(r" on .*?;", " on X;", text) for text in helps.values()} == {
        "Open no browser tab on X; its link is printed either way."
    }


@pytest.mark.parametrize(
    ("option", "convention"),
    [
        ("--filter", "ONE argument"),
        ("--variables", "SPACE-separated"),
        ("--map", "REPEAT the flag"),
        ("--interactive", "ONE comma-separated argument"),
        ("--rerun", "ONE comma-separated argument"),
    ],
)
def test_a_list_valued_flag_says_how_its_list_is_written(option, convention):
    """Three conventions, not unified: each flag's help says which is its."""
    found = _actions(option)
    assert found
    for path, action in found:
        assert convention in action.help, f"`dstf {path}` {option}"


# ---------------------------------------------------------------------------
# A value that cannot be meant is the parser's error
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "allowed"),
    [
        (["run", "--timeout", "0"], "0: must be a whole number of 1 or more (seconds)"),
        (["run", "--timeout", "-5"], "-5: must be a whole number of 1 or more"),
        (["run", "--timeout", "1.5"], "'1.5': must be a whole number of 1 or more"),
        (["run", "--parallel", "0"], "0: must be a whole number of 1 or more"),
        (["compare", "--parallel", "0"], "0: must be a whole number of 1 or more"),
        (["author", "--port", "0"], "0: must be a whole number from 1 to 65535"),
        (["author", "--port", "70000"], "70000: must be a whole number from 1 to"),
    ],
    ids=lambda value: " ".join(value) if isinstance(value, list) else "",
)
def test_a_value_out_of_range_is_refused_with_what_is_allowed(argv, allowed, capsys):
    """`--timeout 0` and `--parallel 0` were dropped by a truthiness test
    and the run went on with the default; `--port 0` served on 8080."""
    text = _parse(argv, capsys)
    assert isinstance(text, str), f"{argv} parsed"
    assert f"argument {argv[1]}: {allowed}" in text


def _takes_a_value(action: argparse.Action) -> bool:
    return action.nargs != 0 and not isinstance(
        action, argparse._SubParsersAction | argparse._HelpAction
    )


@pytest.mark.parametrize("path", COMMANDS)
def test_an_empty_value_is_refused_for_every_flag_and_positional(path, capsys):
    """`compare --accept --filter ""` stored every baseline (an empty filter
    selected everything); `--backend ""` was read as not given, `--work-dir
    ""` as the current directory, `--config ""` as <cwd>/testing.json. A
    value of blanks only (`--filter "  "`) is the same empty value."""
    node = NODES[path]
    positionals = [a for a in node._actions if not a.option_strings]
    words = path.split() + ARGV.get(path, [])
    taking = [a for a in node._actions if _takes_a_value(a)]
    assert taking
    for action, blank in [(a, b) for a in taking for b in ("", "  ")]:
        if action.option_strings:
            argv = [*words, action.option_strings[-1], blank]
            name = "/".join(action.option_strings)
        else:
            # this positional empty, the others as ARGV gives them
            given = list(ARGV.get(path, []))
            index = positionals.index(action)
            given[index : index + 1] = [blank]
            argv = [*path.split(), *given]
            name = action.metavar or action.dest
        text = _parse(argv, capsys)
        assert isinstance(text, str), f"`dstf {path}` took {blank!r} for {name}"
        assert f"argument {name}: an empty value names nothing" in text, text


@pytest.mark.parametrize("flag", ["--config", "--source-path", "--test-spec"])
def test_an_empty_project_flag_before_the_command_is_refused(flag, capsys):
    text = _parse([flag, "", "discover"], capsys)
    assert isinstance(text, str) and "an empty value names nothing" in text


@pytest.mark.parametrize(
    "argv",
    [
        ["compare", "--accept", "--filter", ","],
        ["run", "--filter", " , "],
        ["run", "--rerun", ","],
        ["run", "-i", ","],
    ],
    ids=" ".join,
)
def test_a_list_of_nothing_but_commas_is_refused(argv, capsys):
    """Patterns that are all empty select every test, as an empty filter did."""
    text = _parse(argv, capsys)
    assert isinstance(text, str), f"{argv} parsed"
    assert "names nothing" in text, text


def test_the_values_given_reach_the_config(tmp_path):
    from dstf.cli.common import _build_config

    cfg = tmp_path / "testing.json"
    cfg.write_text('{"source_type": "python", "backend": "Python"}', encoding="utf-8")
    args = build_arg_parser().parse_args(
        ["run", "--config", str(cfg), "--timeout", "5", "--parallel", "3"]
        + ["--tolerance", "0", "--work-dir", str(tmp_path / "w"), "--default-points"]
    )
    config = _build_config(args)
    assert (config.timeout, config.parallel, config.tolerance) == (5, 3, 0.0)
    assert config.work_dir == tmp_path / "w" and config.default_points is True


def test_the_port_s_default_is_the_one_the_help_states():
    from dstf.cli.author_cmd import DEFAULT_PORT

    args = build_arg_parser().parse_args(["author"])
    assert args.port == DEFAULT_PORT
    (port,) = [a for _path, a in _actions("--port")]
    assert f"Default: {DEFAULT_PORT}." in port.help


@pytest.mark.parametrize("command", ["run", "compare"])
def test_output_beside_the_console_format_is_an_error(command, capsys):
    """It names the file junit and html write; the console format writes
    none, and the flag was read by nothing."""
    said = (
        f"dstf {command}: error: --output out.xml does not apply with "
        "--report-format console: the console report is printed"
    )
    # the console format given, or taken as the default: the same sentence
    # (it said "without --report-format" of a line that had one)
    for given in ([], ["--report-format", "console"]):
        text = _parse([command, *given, "-o", "out.xml"], capsys)
        assert isinstance(text, str) and said in text, text
    for fmt in ("junit", "html"):
        args = _parse([command, "--report-format", fmt, "-o", "out"], capsys)
        assert isinstance(args, argparse.Namespace) and args.output == "out"
    # another command's --output is its own
    assert _parse(["spec", "schema", "-o", "s.json"], capsys).output == "s.json"


# ---------------------------------------------------------------------------
# A flag that will not be read beside the others on the line is refused
# ---------------------------------------------------------------------------

#: How each flag of the rows is written on a command line.
_GIVEN = {
    "accept": ["--accept"],
    "interactive": ["-i", "failed"],
    "rerun": ["--rerun", "failed"],
    "filter": ["--filter", "Lib.*"],
    "package": ["--package", "Lib"],
    "report": ["--report"],
    "report_format": ["--report-format", "junit"],
    "tolerance": ["--tolerance", "0.5"],
    "default_points": ["--default-points"],
    "merge": ["--merge"],
    "parallel": ["--parallel", "2"],
    "orphans": ["--orphans"],
    "work_dir": ["--work-dir", "w"],
    "test_spec": ["--test-spec", "s.json"],
    "reference_root": ["--reference-root", "r"],
    "config": ["--config", "t.json"],
}


def _given(command: str, dest: str, *, bare: bool = False) -> list[str]:
    """``dest`` on a command line; an ``--output`` with a format that writes
    a file, unless ``bare``."""
    if dest == "output":
        if bare or command == "baseline export":
            return ["-o", "out.xml"]
        return [*_GIVEN["report_format"], "-o", "out.xml"]
    return _GIVEN[dest]


#: Command lines refused because a flag on them will not be read beside
#: another — written here by hand, NOT generated from `UNREAD`, so a row or
#: a flag dropped from that table fails this list (the generated tests
#: follow the table wherever it goes).
_REFUSED_BY_HAND = [
    ["run", "--accept", "--report-format", "junit"],
    ["run", "--accept", "--report"],
    ["run", "--accept", "--report-format", "junit", "-o", "x.xml"],
    ["run", "--accept", "--merge", "--filter", "Lib.*"],
    ["run", "--accept", "--tolerance", "0.1"],
    ["run", "--accept", "--default-points"],
    ["run", "-i", "failed", "--report"],
    ["run", "-i", "failed", "--report-format", "html"],
    ["run", "--merge"],
    ["run", "-o", "x.xml"],
    ["compare", "--accept", "--report"],
    ["compare", "--accept", "--report-format", "junit"],
    ["compare", "--accept", "--tolerance", "0.1"],
    ["compare", "--accept", "--parallel", "2"],
    ["compare", "--parallel", "2"],
    ["compare", "-o", "x.xml"],
    ["baseline", "export", "-o", "x.json", "--work-dir", "w"],
    ["manifest", "cleanup", "--work-dir", "w"],
    ["check", "--config", "testing.json", "--reference-root", "refs"],
]


@pytest.mark.parametrize("argv", _REFUSED_BY_HAND, ids=" ".join)
def test_each_line_shown_refused_is_refused(argv, capsys):
    text = _parse(argv, capsys)
    assert isinstance(text, str), f"dstf {' '.join(argv)} parsed"
    assert " does not apply " in text, text


def _unread_cases():
    from dstf.cli.parser import UNREAD

    for row in UNREAD:
        for dest in row.flags:
            for beside in row.beside if row.with_ else [None]:
                yield row, dest, beside


@pytest.mark.parametrize(
    ("row", "dest", "beside"),
    list(_unread_cases()),
    ids=lambda v: v if isinstance(v, str) else (v.command if v else "-"),
)
def test_a_flag_that_will_not_be_read_beside_another_is_refused(
    row, dest, beside, capsys
):
    # the positionals the command needs, and none of ARGV's flags
    positionals = [w for w in ARGV.get(row.command, []) if not w.startswith("-")]
    words = row.command.split() + positionals
    with_ = _given(row.command, beside) if beside else []
    argv = [*words, *with_, *_given(row.command, dest, bare=not row.with_)]
    text = _parse(argv, capsys)
    assert isinstance(text, str), f"dstf {' '.join(argv)} parsed"
    node = NODES[row.command]
    flag = lambda d: [a for a in node._actions if a.dest == d][0]  # noqa: E731
    name = lambda d: flag(d).option_strings[-1]  # noqa: E731
    # a flag read only beside one that always has a value (--report-format,
    # console by default) is not read WITH that value
    default = (
        flag(row.beside[0]).default
        if not beside
        and len(row.beside) == 1
        and isinstance(flag(row.beside[0]).default, str)
        else None
    )
    where = (
        f"with {name(beside)}"
        if beside
        else f"with {name(row.beside[0])} {default}"
        if default
        else "without "
        + ", ".join(name(d) for d in row.beside[:-1])
        + (" or " if len(row.beside) > 1 else "")
        + name(row.beside[-1])
    )
    assert f" does not apply {where}: {row.why}" in text, text
    assert f"dstf {row.command}: error: --" in text
    # the help says it where the user reads the flag
    help_text = " ".join(node.format_help().split())
    assert f"{row.why}." in help_text


def test_the_sentence_names_both_flags_and_the_value(capsys):
    text = _parse(
        ["run", "--accept", "--report-format", "junit", "-o", "a.xml"], capsys
    )
    assert (
        "dstf run: error: --report-format junit does not apply with --accept: "
        "an accept stores the results and writes no report"
    ) in text


@pytest.mark.parametrize(
    "argv",
    [
        # --rerun scores the last run to choose its tests: the tolerance is read
        ["run", "--accept", "--rerun", "failed", "--tolerance", "0.5"],
        ["run", "--accept", "--rerun", "--default-points"],
        ["run", "--merge", "--filter", "Lib.*"],
        ["run", "--merge", "--package", "Lib"],
        ["run", "--merge", "--rerun"],
        ["run", "-i", "--merge", "--filter", "Lib.*", "--tolerance", "0.5"],
        ["compare", "--parallel", "2", "--report"],
        ["compare", "--accept", "--filter", "Lib.*"],
        ["manifest", "cleanup", "--orphans", "--work-dir", "w"],
        ["--test-spec", "s.json", "manifest", "cleanup", "--orphans"],
        ["baseline", "export", "--work-dir", "w"],
        ["check", "--reference-root", "r"],
        ["--config", "t.json", "check", "--source-path", "L"],
        # a flag that asks for something NOT to happen is always accepted
        ["run", "--accept", "--no-browser"],
        ["run", "-i", "--no-browser"],
        ["compare", "--accept", "--no-browser"],
        ["compare", "--no-browser"],
    ],
    ids=" ".join,
)
def test_a_flag_that_is_read_beside_the_others_parses(argv, capsys):
    args = _parse(argv, capsys)
    assert isinstance(args, argparse.Namespace), args


def test_a_flag_written_before_the_command_counts_too(capsys):
    text = _parse(["--test-spec", "s.json", "manifest", "cleanup"], capsys)
    assert "--test-spec s.json does not apply without --orphans" in text
    text = _parse(["--config", "t.json", "check", "--reference-root", "r"], capsys)
    assert "--reference-root r does not apply with --config" in text


# ---------------------------------------------------------------------------
# Only a command that may create the project's files starts a project
# ---------------------------------------------------------------------------

#: The commands that write a default testing.json where none is found, each
#: because it may create a file of the project — a baseline, a spec entry, a
#: soft check, a companion. Every other reads, lists, exports or removes
#: what is there, and writes no config.
CREATES = {
    "run",
    "compare",
    "spec add",
    "spec update",
    "soft-check add",
    "companion add",
    "companion freeze",
}

#: A command line for each command that looks for a project (`author` and
#: `spec schema` look for none), run in a directory that holds only a library.
IN_A_LIBRARY = {
    "discover": [],
    "check": [],
    "run": ["--backend", "OpenModelica", "--no-browser"],
    "compare": ["--backend", "OpenModelica"],
    "spec add": ["Lib.A"],
    "spec update": ["{file}"],
    "baseline export": [],
    "soft-check add": ["Lib.A", "om", "{file}"],
    "soft-check list": [],
    "soft-check remove": ["Lib.A", "om"],
    "companion add": ["Lib.A", "exp", "{file}"],
    "companion list": [],
    "companion freeze": ["Lib.A", "exp"],
    "companion remove": ["Lib.A", "exp"],
    "manifest show": [],
    "manifest dump": [],
    "manifest cleanup": [],
}


#: Of CREATES, the commands that get past their own checks in that
#: directory: only these write the default testing.json. The others are
#: refused there — `run` finds no simulator, `spec update`'s patch and
#: `soft-check add`'s source hold nothing, `companion add` has no baseline
#: and `companion freeze` no companion — and a command that fails at its
#: own checks leaves no file behind.
PAST_ITS_CHECKS = {"compare", "spec add"}


def test_every_command_says_whether_it_may_create_the_project_s_files():
    assert sorted({*IN_A_LIBRARY, "author", "spec schema"}) == COMMANDS
    assert PARSER.creates == CREATES


@pytest.mark.parametrize("path", sorted(IN_A_LIBRARY))
def test_a_command_that_creates_nothing_of_a_project_writes_no_config(
    path, tmp_path, monkeypatch, capsys
):
    """`manifest show`, `baseline export`, `companion list`, `soft-check
    list` and the commands that remove left a default testing.json beside
    the library, as a side effect of looking for one; a command that may
    create the project's files wrote it before its own checks failed it
    (`companion freeze` of a companion that is not there, exit 1)."""
    lib = tmp_path / "work" / "Lib"
    lib.mkdir(parents=True)
    # Lib.A is a class of the library: `spec add` writes an entry for it
    (lib / "package.mo").write_text(
        "within ;\npackage Lib\n  model A\n  end A;\nend Lib;\n"
    )
    patch = tmp_path / "file.json"
    patch.write_text("{}")
    # No simulator anywhere: `run` is refused before it starts one.
    (tmp_path / "bin").mkdir()
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    monkeypatch.delenv("OPENMODELICAHOME", raising=False)
    monkeypatch.chdir(tmp_path / "work")
    argv = [*path.split(), *(a.format(file=patch) for a in IN_A_LIBRARY[path])]
    try:
        main(argv)
    except SystemExit:
        pass
    capsys.readouterr()
    created = (tmp_path / "work" / "testing.json").is_file()
    assert created is (path in PAST_ITS_CHECKS), path
    assert not list(tmp_path.rglob("Lib/testing.json"))


@pytest.mark.parametrize("path", sorted(IN_A_LIBRARY))
def test_a_command_s_help_says_what_it_does_where_no_testing_json_is_found(path):
    text = " ".join(NODES[path].format_help().split())
    writes = "When none is found, this command writes a default one beside the library"
    reads = "When none is found, the project is read on defaults and none is written."
    assert (writes in text) is (path in CREATES)
    assert (reads in text) is (path not in CREATES)


def test_a_compare_keeps_the_run_s_settings_for_what_it_does_not_read(
    tmp_path, monkeypatch, capsys
):
    """The dashboard's commands do what the RUN that made the results did: a
    `compare` re-renders the page with its own settings for what it reads,
    and keeps the run's for the rest. After `run --timeout 7` and a plain
    `compare`, the page's `run` lost `--timeout 7`; and a compare given
    `--tolerance 0.5` scored with it, so its `compare` carries it."""
    import json
    import shutil

    from conftest import PROJECT_ROOT

    lib = tmp_path / "lib"
    shutil.copytree(
        PROJECT_ROOT / "examples" / "python" / "PythonTestingLib",
        lib,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    config = lib / "Resources" / "ReferenceResults" / "testing.json"
    monkeypatch.chdir(tmp_path)
    argv = ["--config", str(config), "--work-dir", str(tmp_path / "work")]
    main(["run", *argv, "--filter", _CONSTANT_CSV, "--timeout", "7", "--no-browser"])
    page = tmp_path / "work" / "dashboard.html"
    assert "--timeout 7" in _page_flags(page)["run"]
    main(["compare", *argv, "--filter", _CONSTANT_CSV, "--tolerance", "0.5"])
    capsys.readouterr()
    flags = _page_flags(page)
    assert "--timeout 7" in flags["run"], flags
    assert "--timeout" not in flags["compare"], flags
    assert "--tolerance 0.5" in flags["compare"], flags
    assert json.loads((tmp_path / "work" / "status.json").read_text("utf-8"))[
        "run_given"
    ]
