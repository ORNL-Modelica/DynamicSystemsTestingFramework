"""The command tree (`dstf.cli.parser`): what it must keep being.

* Every node — the top level, each group, each command — explains itself:
  a description, an example that parses, help on every option and
  positional, in words a user reads.
* A command offers exactly the flags it reads. The four that say where the
  project is work on either side of the command; a flag a command does not
  read is an error on both sides, never dropped.
* A value that cannot be meant is the parser's error, saying what is allowed.
"""

from __future__ import annotations

import argparse
import re
import shlex

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
            # said by the command, in words: argparse cannot see this one
            assert f"dstf {command}: error: {flag} does not apply" in before


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
    """An abbreviation is a flag too."""
    text = _parse(["--test", "spec.json", "author", "--config", "t.json"], capsys)
    assert isinstance(text, str) and "--test-spec does not apply" in text
    assert "it reads --config" in text


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
    text = _parse([command, "-o", "out.xml"], capsys)
    assert isinstance(text, str) and f"dstf {command}: error: --output" in text
    for fmt in ("junit", "html"):
        args = _parse([command, "--report-format", fmt, "-o", "out"], capsys)
        assert isinstance(args, argparse.Namespace) and args.output == "out"
    # another command's --output is its own
    assert _parse(["spec", "schema", "-o", "s.json"], capsys).output == "s.json"
