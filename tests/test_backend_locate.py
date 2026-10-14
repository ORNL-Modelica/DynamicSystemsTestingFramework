"""Each backend finds itself: `Backend.locate`, the rules it goes by, and the
backend a project that names none runs with.

Every test hides this machine's own installs (`no_installs`): the search
looks at a `PATH`, a home directory and standard folders that are all under
`tmp_path`, so a Dymola, an omc or a Julia that happens to be installed
answers nothing here.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest

from dstf.backends import _REGISTRY, backend_class, backend_names, register
from dstf.backends.base import Backend
from dstf.backends.dymola import interface_loader
from dstf.backends.dymola.backend import DymolaBackend
from dstf.backends.dymola.interface_loader import INTERFACE
from dstf.backends.fmpy.backend import FmpyBackend
from dstf.backends.julia.backend import JuliaBackend
from dstf.backends.locate import (
    COMMAND_LINE,
    ENVIRONMENT,
    EXECUTABLE,
    SEARCH,
    SETTING,
    Located,
    Rule,
    find_executable,
    with_backend_path,
)
from dstf.backends.openmodelica.backend import OpenModelicaBackend
from dstf.backends.python.backend import PythonBackend
from dstf.config import Config, ConfigError
from dstf.config_shape import Setting


@pytest.fixture
def no_installs(tmp_path, monkeypatch):
    """A machine with nothing installed: an empty `PATH`, an empty home, no
    standard folder, no variable set. Returns the directory `PATH` is."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.delenv("OPENMODELICAHOME", raising=False)
    monkeypatch.delenv("DYMOLA_INTERFACE_PATH", raising=False)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(interface_loader, "platform_glob_bases", lambda: [])
    return bin_dir


def _program(path: Path, text: str = "#!/bin/sh\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)
    return path


def _wheel(directory: Path, version: str) -> Path:
    """A `python_interface` folder's wheel, as Dymola ships one."""
    directory.mkdir(parents=True, exist_ok=True)
    wheel = directory / f"dymola-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as z:
        z.writestr("dymola/__init__.py", "")
    return wheel


def _dymola_install(base: Path, name: str, version: str) -> tuple[Path, Path]:
    """A Dymola install tree under ``base``: its program and its wheel."""
    install = base / name
    program = _program(
        install / "bin64" / interface_loader.executable_glob().split("/")[-1]
    )
    wheel = _wheel(install / "Modelica" / "Library" / "python_interface", version)
    return program, wheel


# ---------------------------------------------------------------------------
# The one search: settings, then environment, then PATH and the folders
# ---------------------------------------------------------------------------


class TestFindExecutable:
    def test_the_first_configured_entry_that_exists_wins(self, no_installs, tmp_path):
        second = _program(tmp_path / "opt" / "tool-2" / "tool")
        found = find_executable(
            {"executables": [str(tmp_path / "opt" / "tool-1" / "tool"), str(second)]},
            ("tool",),
        )
        assert found.path == str(second)
        assert found.rule == Rule(SETTING, "executables")

    def test_a_configured_name_resolves_on_path(self, no_installs):
        on_path = _program(no_installs / "mytool")
        found = find_executable({"executables": ["mytool"]}, ("tool",))
        assert (found.path, found.rule) == (str(on_path), Rule(SETTING, "executables"))

    def test_pinned_paths_that_do_not_exist_end_the_search(self, no_installs):
        """A pinned list is an instruction: with none of its paths there,
        the same-named program on PATH is NOT taken in its place."""
        _program(no_installs / "tool")
        pinned = ["C:\\Program Files\\Tool\\tool.exe", "/nope/tool"]
        found = find_executable({"executables": pinned}, ("tool",))
        assert found.path is None and found.rule is None
        assert found.detail == (
            "the configured executables: C:\\Program Files\\Tool\\tool.exe "
            "does not exist, /nope/tool does not exist"
        )

    def test_one_pinned_path_among_names_ends_the_search(self, no_installs):
        """ANY full path pins the list — not only a list of nothing else."""
        _program(no_installs / "tool")
        found = find_executable({"executables": ["tool-9", "/nope/tool"]}, ("tool",))
        assert found.path is None and found.rule is None

    def test_backend_path_is_the_one_entry_and_always_pinned(self, no_installs):
        """`--backend-path` is the most pinned path there is: it is the one
        entry looked at — over the block's — and when it is not there, no
        other install is taken in its place. A bare name too."""
        on_path = _program(no_installs / "tool")
        block = {"executables": [str(on_path)]}
        gone = find_executable(with_backend_path(block, "/nope/tool"), ("tool",))
        assert gone.path is None and gone.rule is None
        assert gone.detail == "--backend-path /nope/tool does not exist"
        name = find_executable(with_backend_path(block, "tool-9"), ("tool",))
        assert name.path is None
        assert name.detail == "--backend-path tool-9 is not on PATH"

    def test_backend_path_found_names_the_command_line(self, no_installs, tmp_path):
        other = _program(tmp_path / "opt" / "tool")
        found = find_executable(with_backend_path({}, str(other)), ("tool",))
        assert (found.path, found.rule) == (
            str(other),
            Rule(COMMAND_LINE, "--backend-path"),
        )
        assert found.rule.said("Dymola") == "the command line: --backend-path"
        assert Located((found,)).lines("Dymola")[1] == (
            "    found by: the command line: --backend-path"
        )

    def test_a_directory_is_no_executable(self, no_installs, tmp_path):
        """The install's folder for its program: `check` called it ready,
        and the run then could not start it."""
        install = tmp_path / "opt" / "tool-2026"
        install.mkdir(parents=True)
        found = find_executable({"executables": [str(install)]}, ("tool",))
        assert found.path is None
        assert found.detail == f"the configured executables: {install} is a directory"

    @pytest.mark.skipif(
        sys.platform.startswith("win"), reason="Windows has no executable bit"
    )
    def test_a_file_that_cannot_be_executed_is_no_executable(
        self, no_installs, tmp_path, monkeypatch
    ):
        notes = tmp_path / "opt" / "tool"
        notes.parent.mkdir()
        notes.write_text("not a program")
        found = find_executable({"executables": [str(notes)]}, ("tool",))
        assert found.path is None
        assert found.detail == f"the configured executables: {notes} is not executable"
        # The same holds wherever a path comes from: a variable, a folder.
        monkeypatch.setenv("TOOL_HOME", str(tmp_path / "opt"))
        found = find_executable(
            {},
            ("tool",),
            environment=[("TOOL_HOME", lambda home: [Path(home) / "tool"])],
            folders=[(tmp_path, "opt/tool")],
        )
        assert found.path is None

    def test_names_that_do_not_resolve_fall_through_to_the_search(self, no_installs):
        on_path = _program(no_installs / "tool")
        found = find_executable({"executables": ["tool-9"]}, ("tool",))
        assert (found.path, found.rule) == (str(on_path), Rule(SEARCH, "PATH"))

    def test_the_environment_comes_before_path(
        self, no_installs, tmp_path, monkeypatch
    ):
        _program(no_installs / "tool")
        in_home = _program(tmp_path / "toolhome" / "bin" / "tool")
        monkeypatch.setenv("TOOL_HOME", str(tmp_path / "toolhome"))
        found = find_executable(
            {},
            ("tool",),
            environment=[("TOOL_HOME", lambda home: [Path(home) / "bin" / "tool"])],
        )
        assert (found.path, found.rule) == (
            str(in_home),
            Rule(ENVIRONMENT, "TOOL_HOME"),
        )

    def test_the_standard_folders_come_last_the_best_match_first(
        self, no_installs, tmp_path
    ):
        base = tmp_path / "opt"
        _program(base / "tool-1" / "bin" / "tool")
        newer = _program(base / "tool-2" / "bin" / "tool")
        found = find_executable(
            {},
            ("tool",),
            folders=[(base, "tool-*/bin/tool")],
            best_first=lambda path: -int(path.parent.parent.name[-1]),
        )
        assert found.path == str(newer)
        assert found.rule == Rule(SEARCH, f"the standard folder {base}")

    def test_not_found_says_where_it_looked(self, no_installs, tmp_path):
        base = tmp_path / "opt"
        found = find_executable({}, ("tool",), folders=[(base, "tool-*/bin/tool")])
        assert found.path is None
        assert found.detail == f"looked for tool on PATH, {base}"

    def test_a_rule_says_itself_in_words(self):
        assert Rule(SETTING, "executables").said("Dymola") == (
            "testing.json: backends.Dymola.executables"
        )
        assert Rule(ENVIRONMENT, "X_HOME").said() == "environment variable X_HOME"
        assert Rule(SEARCH, "PATH").said("Dymola") == "search: PATH"


# ---------------------------------------------------------------------------
# Dymola: the executable, and the interface that belongs to it
# ---------------------------------------------------------------------------


class TestDymolaInterface:
    """The four rungs the Python interface is found by, in order."""

    def _two_installs(self, tmp_path, monkeypatch):
        base = tmp_path / "Program Files"
        older = _dymola_install(base, "Dymola 2025x", "2025.0")
        newer = _dymola_install(base, "Dymola 2026x", "2026.0")
        monkeypatch.setattr(interface_loader, "platform_glob_bases", lambda: [base])
        return older, newer

    def test_the_interface_setting_wins(self, no_installs, tmp_path, monkeypatch):
        (_, _), (newer_exe, _) = self._two_installs(tmp_path, monkeypatch)
        odd = _wheel(tmp_path / "odd" / "place", "2024.0")
        monkeypatch.setenv("DYMOLA_INTERFACE_PATH", str(tmp_path / "elsewhere"))
        located = DymolaBackend.locate(
            {"executables": [str(newer_exe)], "interface": str(odd.parent)}
        )
        part = located.part(INTERFACE)
        assert (part.path, part.rule) == (str(odd), Rule(SETTING, "interface"))

    def test_the_setting_may_name_the_archive_itself(self, no_installs, tmp_path):
        odd = _wheel(tmp_path / "odd", "2024.0")
        part = DymolaBackend.locate({"interface": str(odd)}).part(INTERFACE)
        assert part.path == str(odd)

    def test_a_setting_that_holds_no_archive_is_not_found(
        self, no_installs, tmp_path, monkeypatch
    ):
        """Not a fall through to the newest Dymola's interface."""
        self._two_installs(tmp_path, monkeypatch)
        empty = tmp_path / "empty"
        empty.mkdir()
        part = DymolaBackend.locate({"interface": str(empty)}).part(INTERFACE)
        assert part.path is None
        assert part.detail == (
            f"the configured interface holds no dymola-*.whl or dymola.egg: {empty}"
        )

    def test_the_environment_variable_is_next(self, no_installs, tmp_path, monkeypatch):
        (_, _), (newer_exe, _) = self._two_installs(tmp_path, monkeypatch)
        odd = _wheel(tmp_path / "odd", "2024.0")
        monkeypatch.setenv("DYMOLA_INTERFACE_PATH", str(odd.parent))
        part = DymolaBackend.locate({"executables": [str(newer_exe)]}).part(INTERFACE)
        assert part.path == str(odd)
        assert part.rule == Rule(ENVIRONMENT, "DYMOLA_INTERFACE_PATH")

    def test_the_older_dymola_the_config_names_gets_its_own_interface(
        self, no_installs, tmp_path, monkeypatch
    ):
        """Two Dymola versions installed and testing.json names the OLDER
        executable: the older one's interface is the one loaded. The search
        used to glob the standard folders and take the newest Dymola's
        package whatever the config said — a 2025 run driven through 2026's
        interface."""
        (older_exe, older_wheel), (_, newer_wheel) = self._two_installs(
            tmp_path, monkeypatch
        )
        located = DymolaBackend.locate({"executables": [str(older_exe)]})
        assert located.executable == str(older_exe)
        part = located.part(INTERFACE)
        assert part.path == str(older_wheel) != str(newer_wheel)
        assert part.rule == Rule(SEARCH, "beside the executable")

    def test_the_run_loads_what_locate_found(self, no_installs, tmp_path, monkeypatch):
        """…and that is the archive a run's workers load: `preflight` hands
        the loader the located part, not a search of its own."""
        from dstf.backends.dymola import persistent_backend

        (older_exe, older_wheel), _ = self._two_installs(tmp_path, monkeypatch)
        lib = tmp_path / "Lib"
        lib.mkdir()
        (lib / "package.mo").write_text("package Lib end Lib;")
        (tmp_path / "testing.json").write_text(
            json.dumps(
                {
                    "source_path": "Lib",
                    "backends": {"Dymola": {"executables": [str(older_exe)]}},
                }
            )
        )
        config = Config(config_file=tmp_path / "testing.json")
        assert config.backend_name == "Dymola"
        assert config.backend_path == str(older_exe)
        loaded = []
        monkeypatch.setattr(interface_loader, "load_dymola_interface", loaded.append)
        persistent_backend.PersistentDymolaBackend.preflight(config)
        assert [found.path for found in loaded] == [str(older_wheel)]

    def test_a_symlink_to_the_program_is_followed(
        self, no_installs, tmp_path, monkeypatch
    ):
        (older_exe, older_wheel), _ = self._two_installs(tmp_path, monkeypatch)
        link = no_installs / "dymola"
        link.symlink_to(older_exe)
        located = DymolaBackend.locate({})
        assert located.parts[0].rule == Rule(SEARCH, "PATH")
        assert located.path(INTERFACE) == str(older_wheel)

    def test_a_launcher_script_names_its_install(
        self, no_installs, tmp_path, monkeypatch
    ):
        """Linux's `/usr/local/bin/dymola`: a link to a few lines of shell
        outside the install, which export where it is."""
        (older_exe, older_wheel), _ = self._two_installs(tmp_path, monkeypatch)
        install = older_exe.parent.parent
        launcher = _program(
            no_installs / "dymola-2025x-x86_64",
            "#!/bin/sh\nexport PATH\n"
            f'export DYMOLA="{install}"\n'
            "export LD_LIBRARY_PATH=$DYMOLA/bin/lib64:$LD_LIBRARY_PATH\n"
            'exec $DYMOLA/bin64/dymola "$@"\n',
        )
        (no_installs / "dymola").symlink_to(launcher)
        located = DymolaBackend.locate({})
        assert located.executable == str(no_installs / "dymola")
        assert located.path(INTERFACE) == str(older_wheel)
        assert located.part(INTERFACE).rule == Rule(SEARCH, "beside the executable")

    def test_the_standard_folders_are_for_an_install_that_cannot_be_told(
        self, no_installs, tmp_path, monkeypatch
    ):
        """An executable that says nothing of its install (here: one that is
        no Dymola's at all) leaves the newest one of the folders — and the
        part says so, in one line `check` prints and a run logs."""
        _, (_, newer_wheel) = self._two_installs(tmp_path, monkeypatch)
        stray = _program(tmp_path / "stray" / "dymola")
        located = DymolaBackend.locate({"executables": [str(stray)]})
        part = located.part(INTERFACE)
        assert part.path == str(newer_wheel)
        assert part.rule == Rule(
            SEARCH, f"the standard folder {tmp_path / 'Program Files'}"
        )
        assert part.detail == (
            f"taken from {newer_wheel.parents[3]}: the install of {stray} could "
            "not be determined (its launcher sets no DYMOLA)"
        )
        assert f"    {part.detail}" in located.lines("Dymola")

    def test_an_install_that_is_known_and_holds_no_archive_is_not_found(
        self, no_installs, tmp_path, monkeypatch
    ):
        """It used to take the NEWEST standard-folder interface in silence:
        the defect the rule exists to end. Once the executable's install is
        known, the interface is looked for THERE and nowhere else."""
        (older_exe, older_wheel), _ = self._two_installs(tmp_path, monkeypatch)
        older_wheel.unlink()
        part = DymolaBackend.locate({"executables": [str(older_exe)]}).part(INTERFACE)
        assert part.path is None and part.rule is None
        assert part.detail == (
            f"no dymola-*.whl or dymola.egg in {older_wheel.parent} "
            f"(the install of {older_exe})"
        )

    def _launcher(self, tmp_path, monkeypatch, text: str, env: str | None = None):
        """A launcher outside the installs, holding ``text`` with ``{older}``
        and ``{newer}`` as the two installs. Returns what was located and
        the two wheels."""
        # As Linux has them, no space in a path: an unquoted one ends at it.
        base = tmp_path / "opt"
        older_exe, older_wheel = _dymola_install(base, "dymola-2025x-x86_64", "2025.0")
        newer_exe, newer_wheel = _dymola_install(base, "dymola-2026x-x86_64", "2026.0")
        monkeypatch.setattr(interface_loader, "platform_glob_bases", lambda: [base])
        older, newer = older_exe.parent.parent, newer_exe.parent.parent
        if env is None:
            monkeypatch.delenv("DYMOLA", raising=False)
        else:
            monkeypatch.setenv("DYMOLA", env.format(older=older, newer=newer))
        launcher = _program(
            tmp_path / "bin" / "dymola", text.format(older=older, newer=newer)
        )
        located = DymolaBackend.locate({"executables": [str(launcher)]})
        return located.part(INTERFACE), older_wheel, newer_wheel, launcher

    @pytest.mark.parametrize(
        "text",
        [
            "#!/bin/sh\nexport DYMOLA={older} # the install\nexec $DYMOLA/bin64/dymola\n",
            '#!/bin/sh\nexport DYMOLA={older}; exec $DYMOLA/bin64/dymola "$@"\n',
            "#!/bin/sh\nDYMOLA='{older}' && export DYMOLA\nexec $DYMOLA/bin64/dymola\n",
            "#!/bin/sh\nexport DYMOLA=${{DYMOLA:-{older}}}\nexec $DYMOLA/bin64/dymola\n",
            # The last assignment wins, as it does to the shell.
            "#!/bin/sh\nDYMOLA={newer}\nDYMOLA={older}\nexport DYMOLA\nexec x\n",
            # Past 4096 bytes: a launcher with a licence text above it.
            "#!/bin/sh\n" + "# notes\n" * 700 + 'export DYMOLA="{older}"\nexec x\n',
            "#!/bin/sh\r\nexport DYMOLA={older}\r\nexec $DYMOLA/bin64/dymola\r\n",
        ],
        ids=["comment", "semicolon", "and", "default", "two", "big", "crlf"],
    )
    def test_a_launcher_is_read_as_the_shell_reads_it(
        self, no_installs, tmp_path, monkeypatch, text
    ):
        """Each of these silently took the newest standard-folder interface."""
        part, older_wheel, _, _ = self._launcher(tmp_path, monkeypatch, text)
        assert part.path == str(older_wheel)
        assert part.rule == Rule(SEARCH, "beside the executable")
        assert part.detail == ""

    def test_a_default_install_yields_to_the_environment_as_in_the_shell(
        self, no_installs, tmp_path, monkeypatch
    ):
        part, _, newer_wheel, _ = self._launcher(
            tmp_path,
            monkeypatch,
            "#!/bin/sh\nexport DYMOLA=${{DYMOLA:-{older}}}\nexec x\n",
            env="{newer}",
        )
        assert part.path == str(newer_wheel)
        assert part.rule == Rule(SEARCH, "beside the executable")

    def test_a_launcher_whose_install_is_gone_is_looked_for_there_alone(
        self, no_installs, tmp_path, monkeypatch
    ):
        gone = tmp_path / "opt" / "dymola-gone"
        part, _, _, launcher = self._launcher(
            tmp_path, monkeypatch, f"#!/bin/sh\nexport DYMOLA={gone}\nexec x\n"
        )
        assert part.path is None
        assert part.detail == (
            "no dymola-*.whl or dymola.egg in "
            f"{gone / 'Modelica' / 'Library' / 'python_interface'} "
            f"(the install of {launcher})"
        )

    @pytest.mark.parametrize(
        ("line", "why"),
        [
            ("export DYMOLA=$HOME/dymola", "export DYMOLA=$HOME/dymola"),
            ('DYMOLA="$(dirname "$0")/.."', 'DYMOLA="$(dirname "$0")/.."'),
            ("export DYMOLA='unclosed", "export DYMOLA='unclosed"),
        ],
    )
    def test_a_line_that_cannot_be_read_is_never_a_wrong_install(
        self, no_installs, tmp_path, monkeypatch, line, why
    ):
        """It is "cannot be determined": the standard folders, and the line."""
        part, _, newer_wheel, launcher = self._launcher(
            tmp_path,
            monkeypatch,
            "#!/bin/sh\nDYMOLA={older}\n"
            + line.replace("{", "{{").replace("}", "}}")
            + "\nexec x\n",
        )
        assert part.path == str(newer_wheel)
        assert part.detail == (
            f"taken from {newer_wheel.parents[3]}: the install of {launcher} could "
            f"not be determined (its launcher sets DYMOLA by a line that is not "
            f"read here: {why})"
        )

    def test_a_run_logs_where_the_interface_was_taken_from(
        self, no_installs, tmp_path, monkeypatch, caplog
    ):
        from dstf.backends.dymola import persistent_backend

        _, (_, newer_wheel) = self._two_installs(tmp_path, monkeypatch)
        stray = _program(tmp_path / "stray" / "dymola")
        lib = tmp_path / "Lib"
        lib.mkdir()
        (lib / "package.mo").write_text("package Lib end Lib;")
        (tmp_path / "testing.json").write_text(
            json.dumps(
                {
                    "source_path": "Lib",
                    "backends": {"Dymola": {"executables": [str(stray)]}},
                }
            )
        )
        config = Config(config_file=tmp_path / "testing.json")
        monkeypatch.setattr(
            persistent_backend, "load_dymola_interface", lambda found: object
        )
        monkeypatch.setattr(persistent_backend, "_install_dymola_log_filter", list)
        monkeypatch.setattr(
            persistent_backend, "_patch_dymola_for_parallel_startup", list
        )
        backend = persistent_backend.PersistentDymolaBackend(config)
        with caplog.at_level("WARNING"):
            backend.setup_before_workers()
        (record,) = [r for r in caplog.records if "taken from" in r.getMessage()]
        assert record.getMessage() == (
            f"Dymola's Python interface was taken from {newer_wheel.parents[3]}: "
            f"the install of {stray} could not be determined (its launcher sets "
            "no DYMOLA)"
        )

    def test_no_executable_named_the_newest_install_is_found(
        self, no_installs, tmp_path, monkeypatch
    ):
        _, (newer_exe, newer_wheel) = self._two_installs(tmp_path, monkeypatch)
        located = DymolaBackend.locate({})
        assert located.executable == str(newer_exe)
        assert located.path(INTERFACE) == str(newer_wheel)

    def test_nothing_anywhere(self, no_installs):
        located = DymolaBackend.locate({})
        assert not located.found and not located.complete
        assert located.part(INTERFACE).detail == (
            "no Dymola executable was found, and there is no "
            "Modelica/Library/python_interface under the standard folders ((none))"
        )

    def test_linux_install_names_sort_by_version(self):
        key = interface_loader.dymola_sort_key
        names = [
            "/opt/dymola-2024x-x86_64",
            "/opt/dymola-2026x-x86_64",
            "/opt/dymola-2025x-x86_64",
        ]
        assert sorted(map(Path, names), key=key) == [
            Path("/opt/dymola-2026x-x86_64"),
            Path("/opt/dymola-2025x-x86_64"),
            Path("/opt/dymola-2024x-x86_64"),
        ]


# ---------------------------------------------------------------------------
# The other four
# ---------------------------------------------------------------------------


class TestOpenModelica:
    def test_openmodelicahome_names_the_install(
        self, no_installs, tmp_path, monkeypatch
    ):
        omc = _program(tmp_path / "om" / "bin" / "omc")
        _program(no_installs / "omc")
        monkeypatch.setenv("OPENMODELICAHOME", str(tmp_path / "om"))
        located = OpenModelicaBackend.locate({})
        assert located.executable == str(omc)
        assert located.parts[0].rule == Rule(ENVIRONMENT, "OPENMODELICAHOME")

    def test_the_block_comes_before_the_variable(
        self, no_installs, tmp_path, monkeypatch
    ):
        _program(tmp_path / "om" / "bin" / "omc")
        mine = _program(tmp_path / "mine" / "omc")
        monkeypatch.setenv("OPENMODELICAHOME", str(tmp_path / "om"))
        located = OpenModelicaBackend.locate({"executables": [str(mine)]})
        assert located.executable == str(mine)

    def test_path_is_the_search(self, no_installs):
        omc = _program(no_installs / "omc")
        located = OpenModelicaBackend.locate({})
        assert (located.executable, located.parts[0].rule) == (
            str(omc),
            Rule(SEARCH, "PATH"),
        )

    def test_program_files_is_searched_by_version_not_by_spelling(
        self, no_installs, tmp_path, monkeypatch
    ):
        """1.9.0 sorted above 1.25.0: the folders were compared as text."""
        from dstf.backends.openmodelica import backend as om

        base = tmp_path / "Program Files"
        for version in ("1.9.0", "1.25.0", "1.22.3"):
            _program(base / f"OpenModelica{version}-64bit" / "bin" / "omc.exe")
        monkeypatch.setattr(
            om, "_install_folders", lambda: [(base, "OpenModelica*/bin/omc.exe")]
        )
        found = OpenModelicaBackend.locate({}).parts[0]
        assert found.path == str(base / "OpenModelica1.25.0-64bit" / "bin" / "omc.exe")
        assert found.rule == Rule(SEARCH, f"the standard folder {base}")


class TestJulia:
    def test_juliaup_s_folder_is_searched_newest_first(self, no_installs, tmp_path):
        """juliaup keeps each version under the home directory and need not
        put its launcher on PATH."""
        versions = tmp_path / "home" / ".julia" / "juliaup"
        _program(versions / "julia-1.10.4+0.x64.linux.gnu" / "bin" / JULIA)
        newest = _program(versions / "julia-1.11.9+0.x64.linux.gnu" / "bin" / JULIA)
        _program(versions / "julia-1.9.0+0.x64.linux.gnu" / "bin" / JULIA)
        located = JuliaBackend.locate({})
        assert located.executable == str(newest)
        assert located.parts[0].rule == Rule(SEARCH, f"the standard folder {versions}")

    def test_path_comes_before_the_folders(self, no_installs, tmp_path):
        _program(
            tmp_path / "home" / ".julia" / "juliaup" / "julia-1.11.9" / "bin" / JULIA
        )
        on_path = _program(no_installs / "julia")
        assert JuliaBackend.locate({}).executable == str(on_path)

    def test_not_found(self, no_installs):
        located = JuliaBackend.locate({})
        assert not located.found
        assert located.parts[0].detail.startswith("looked for julia on PATH, ")


JULIA = "julia.exe" if sys.platform.startswith("win") else "julia"


class TestPython:
    def test_the_running_interpreter_where_none_is_named(self, no_installs):
        """Not some other `python` on PATH: with none named, the one
        running the tool is the only one known to work."""
        _program(no_installs / "python")
        located = PythonBackend.locate({})
        assert located.executable == sys.executable
        assert located.parts[0].rule == Rule(SEARCH, "the running interpreter")

    def test_the_configured_interpreter(self, no_installs):
        mine = _program(no_installs / "python3")
        located = PythonBackend.locate({"executables": ["python", "python3"]})
        assert (located.executable, located.parts[0].rule) == (
            str(mine),
            Rule(SETTING, "executables"),
        )

    def test_a_pinned_interpreter_that_is_gone_is_not_found(
        self, no_installs, tmp_path
    ):
        located = PythonBackend.locate(
            {"executables": [str(tmp_path / "venv" / "python")]}
        )
        assert not located.found


class TestFmpy:
    def test_found_iff_fmpy_is_there_to_import(self, monkeypatch):
        there = importlib.util.find_spec("fmpy") is not None
        assert FmpyBackend.locate({}).found is there
        monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
        located = FmpyBackend.locate({})
        assert not located.found
        assert located.executable is None
        assert (
            located.parts[0].detail
            == "this Python has no fmpy; install the [fmpy] extra"
        )


# ---------------------------------------------------------------------------
# Which backend a project that names none runs with
# ---------------------------------------------------------------------------


def _project(tmp_path: Path, config: dict) -> Path:
    lib = tmp_path / "Lib"
    lib.mkdir(exist_ok=True)
    (lib / "package.mo").write_text("package Lib end Lib;")
    path = tmp_path / "testing.json"
    path.write_text(json.dumps({"source_path": "Lib", **config}))
    return path


class TestWhichBackendRuns:
    def test_no_backends_a_machine_with_only_omc_runs_openmodelica(
        self, no_installs, tmp_path
    ):
        """An empty testing.json used to mean Dymola, installed or not: on a
        machine with only omc every run ended at "Dymola not found"."""
        omc = _program(no_installs / "omc")
        config = Config(config_file=_project(tmp_path, {}))
        assert config.backend_name == "OpenModelica"
        assert config.backend_path == str(omc)

    def test_no_backends_dymola_is_looked_for_first(self, no_installs, tmp_path):
        _program(no_installs / "omc")
        dymola = _program(no_installs / "dymola")
        config = Config(config_file=_project(tmp_path, {"backends": {}}))
        assert (config.backend_name, config.backend_path) == ("Dymola", str(dymola))

    def test_no_backends_and_nothing_installed_names_the_first_candidate(
        self, no_installs, tmp_path
    ):
        config = Config(config_file=_project(tmp_path, {}))
        assert config.backend_name == "Dymola"
        assert config.backend_path is None
        assert not config.located.found

    def test_the_search_is_over_the_backends_of_the_source_type(
        self, no_installs, tmp_path
    ):
        """A Modelica library is never handed to the Python backend because
        an interpreter is always there to find."""
        assert [
            n for n in backend_names() if "modelica" in backend_class(n).source_types
        ] == [
            "Dymola",
            "OpenModelica",
        ]
        path = tmp_path / "testing.json"
        path.write_text(json.dumps({"source_type": "python"}))
        assert Config(config_file=path).backend_name == "Python"
        path.write_text(json.dumps({"source_type": "julia"}))
        assert Config(config_file=path).backend_name == "Julia"
        path.write_text(json.dumps({"source_type": "fmu"}))
        assert Config(config_file=path).backend_name == "FMPy"

    def test_a_source_type_no_backend_runs_is_not_one(self, no_installs, tmp_path):
        """In a file, whatever backend is named beside it: it used to be
        read as "not Modelica", and the library's tests were not looked
        for. A plug-in adds a source type by its backend's `source_types`
        (`TestAPlugInBackend`)."""
        path = tmp_path / "testing.json"
        for config in (
            {"source_type": "simulink"},
            {"source_type": "simulink", "backend": "Python"},
        ):
            path.write_text(json.dumps(config))
            with pytest.raises(ConfigError) as exc:
                Config(config_file=path)
            assert str(exc.value) == (
                f'{path}: source_type: must be one of "modelica", "fmu", "julia", '
                '"python", got "simulink"'
            )

    def test_a_source_type_given_in_code_that_no_backend_runs_asks_for_a_name(
        self, no_installs
    ):
        from dstf.config import _pick_backend

        with pytest.raises(ConfigError) as exc:
            _pick_backend({}, "simulink")
        assert str(exc.value).startswith(
            'no backend runs source_type "simulink": name one with "backend"'
        )

    def test_backends_in_the_order_written(self, no_installs, tmp_path):
        _program(no_installs / "omc")
        _program(no_installs / "dymola")
        config = Config(
            config_file=_project(
                tmp_path, {"backends": {"OpenModelica": {}, "Dymola": {}}}
            )
        )
        assert config.backend_name == "OpenModelica"

    def test_a_pinned_block_never_falls_back_to_another_install(
        self, no_installs, tmp_path
    ):
        """The one entry's paths are gone and a `dymola` IS on PATH: the
        backend is not found, and the run says which paths it looked at."""
        _program(no_installs / "dymola")
        pinned = "C:\\Program Files\\Dymola 2025x\\bin64\\Dymola.exe"
        config = Config(
            config_file=_project(
                tmp_path, {"backends": {"Dymola": {"executables": [pinned]}}}
            )
        )
        assert config.backend_name == "Dymola"
        assert config.backend_path is None
        assert config.located.missing().startswith(
            "executable not found — the configured executables: "
            f"{pinned} does not exist"
        )

    def test_a_backend_path_that_is_not_there_is_not_found(self, no_installs, tmp_path):
        """`--backend-path /nope/omc` with an omc on PATH: the run used to
        start that other omc and report its tests passed."""
        _program(no_installs / "omc")
        config = Config(
            config_file=_project(tmp_path, {"backends": {"OpenModelica": {}}}),
            backend_path="/nope/omc",
        )
        assert config.backend_path is None
        assert not config.located.found
        assert config.located.missing() == (
            "executable not found — --backend-path /nope/omc does not exist"
        )
        with pytest.raises(RuntimeError) as exc:
            OpenModelicaBackend.require_found(config)
        assert str(exc.value) == (
            "OpenModelica not found: executable not found — "
            "--backend-path /nope/omc does not exist."
        )

    def test_python_is_not_the_running_interpreter_under_a_backend_path(
        self, no_installs, tmp_path
    ):
        path = tmp_path / "testing.json"
        path.write_text(json.dumps({"source_type": "python"}))
        config = Config(config_file=path, backend_path="nope-python")
        assert config.backend_path is None and not config.located.found

    def test_backend_path_is_the_one_executable_looked_at(self, no_installs, tmp_path):
        """`--backend-path` over the block: the path stays as given, and the
        interface is looked for beside THAT executable."""
        base = tmp_path / "Program Files"
        (older_exe, older_wheel) = _dymola_install(base, "Dymola 2025x", "2025.0")
        (newer_exe, _) = _dymola_install(base, "Dymola 2026x", "2026.0")
        config = Config(
            config_file=_project(
                tmp_path, {"backends": {"Dymola": {"executables": [str(newer_exe)]}}}
            ),
            backend_path=str(older_exe),
        )
        assert config.backend_path == str(older_exe)
        assert config.located.path(INTERFACE) == str(older_wheel)


# ---------------------------------------------------------------------------
# A path in testing.json is relative to testing.json
# ---------------------------------------------------------------------------


class TestPathsInTestingJson:
    """As `source_path` is: a relative path in a block is relative to the
    file's directory, wherever the command is run, and `~` is the home
    directory. They used to be read against the working directory, and —
    not being "full paths" — fell through to the search when not found."""

    @pytest.fixture
    def elsewhere(self, no_installs, tmp_path, monkeypatch):
        """Run from a directory that is not the project's, with an omc on
        PATH that no test here means."""
        _program(no_installs / "omc")
        (tmp_path / "elsewhere").mkdir()
        monkeypatch.chdir(tmp_path / "elsewhere")
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        return tmp_path

    def _config(self, tmp_path: Path, block: dict, name="OpenModelica") -> Config:
        return Config(config_file=_project(tmp_path, {"backends": {name: block}}))

    def test_a_relative_executable_is_beside_the_file(self, elsewhere):
        mine = _program(elsewhere / "tools" / "omc")
        config = self._config(elsewhere, {"executables": ["./tools/omc"]})
        assert config.backend_path == str(mine)
        assert config.located.parts[0].rule == Rule(SETTING, "executables")
        assert config.backends["OpenModelica"]["executables"] == [str(mine)]
        # No leading dot is needed: a separator makes it a path.
        assert self._config(
            elsewhere, {"executables": ["tools/omc"]}
        ).backend_path == str(mine)

    def test_a_relative_executable_that_is_not_there_is_not_found(self, elsewhere):
        config = self._config(elsewhere, {"executables": ["./tools/omc"]})
        assert config.backend_path is None
        assert config.located.parts[0].detail == (
            f"the configured executables: {elsewhere / 'tools' / 'omc'} does not exist"
        )

    def test_a_tilde_is_the_home_directory(self, elsewhere):
        mine = _program(elsewhere / "home" / "om" / "bin" / "omc")
        config = self._config(elsewhere, {"executables": ["~/om/bin/omc"]})
        assert config.backend_path == str(mine)
        gone = self._config(elsewhere, {"executables": ["~/nope/omc"]})
        assert gone.backend_path is None
        assert f"{elsewhere / 'home' / 'nope' / 'omc'} does not exist" in (
            gone.located.missing()
        )

    def test_a_name_with_no_separator_is_looked_up_on_path(self, elsewhere):
        config = self._config(elsewhere, {"executables": ["omc"]})
        assert config.backend_path == str(elsewhere / "bin" / "omc")
        assert config.backends["OpenModelica"]["executables"] == ["omc"]

    def test_a_path_of_another_system_is_left_as_written(self, elsewhere):
        pinned = "C:\\Program Files\\OpenModelica\\bin\\omc.exe"
        config = self._config(elsewhere, {"executables": [pinned]})
        assert config.backends["OpenModelica"]["executables"] == [pinned]
        assert config.backend_path is None

    @pytest.mark.parametrize(
        ("block", "said"),
        [
            (
                {"executables": [""]},
                'backends.Dymola.executables: must be a list of non-empty strings, got [""]',
            ),
            (
                {"interface": ""},
                'backends.Dymola.interface: must be a non-empty string, got ""',
            ),
        ],
    )
    def test_an_empty_string_is_no_path(self, elsewhere, block, said):
        with pytest.raises(ConfigError) as exc:
            self._config(elsewhere, block, name="Dymola")
        assert str(exc.value) == f"{elsewhere / 'testing.json'}: {said}"

    def test_dymola_s_interface_is_beside_the_file(self, elsewhere):
        wheel = _wheel(elsewhere / "iface", "2025.0")
        config = self._config(elsewhere, {"interface": "iface"}, name="Dymola")
        assert config.located.path(INTERFACE) == str(wheel)
        assert config.backends["Dymola"]["interface"] == str(elsewhere / "iface")
        home = _wheel(elsewhere / "home" / "dy", "2026.0")
        config = self._config(elsewhere, {"interface": "~/dy"}, name="Dymola")
        assert config.located.path(INTERFACE) == str(home)

    def test_backend_path_is_relative_to_where_the_command_is_run(self, elsewhere):
        here = _program(elsewhere / "elsewhere" / "omc")
        config = Config(
            config_file=_project(elsewhere, {"backends": {"OpenModelica": {}}}),
            backend_path="./omc",
        )
        assert config.backend_path == str(here)

    def test_each_key_s_description_says_the_rule(self):
        from dstf.config_shape import EXECUTABLES

        assert "relative to this file" in EXECUTABLES.description
        assert (
            "relative to this file" in DymolaBackend.settings["interface"].description
        )


# ---------------------------------------------------------------------------
# A plug-in backend brings its own table and its own locate
# ---------------------------------------------------------------------------


@pytest.fixture
def acme(tmp_path):
    """A backend a plug-in would register: one setting of its own, one
    executable to find. Nothing in `config.py` knows it."""

    @register("Acme")
    class AcmeBackend(Backend):
        settings = {
            **Backend.settings,
            "licence_server": Setting(
                {"type": "string"},
                "localhost",
                "Where the licence is. Default: localhost.",
            ),
        }
        source_types = frozenset({"acme"})
        binaries = ("acmesim",)

        def read_result(self, test, test_key):  # pragma: no cover - never run
            raise NotImplementedError

    try:
        yield AcmeBackend
    finally:
        _REGISTRY.pop("Acme", None)


class TestAPlugInBackend:
    def test_its_block_is_read_by_its_own_table(self, no_installs, tmp_path, acme):
        exe = _program(no_installs / "acmesim")
        path = tmp_path / "testing.json"
        path.write_text(
            json.dumps(
                {
                    "source_type": "acme",
                    "backends": {"Acme 3": {"licence_server": "lic.example"}},
                }
            )
        )
        config = Config(config_file=path)
        assert config.backend_name == "Acme 3"
        assert config.backend == "Acme"
        assert config.backend_path == str(exe)
        assert config.located.parts[0].rule == Rule(SEARCH, "PATH")
        assert acme.settings_of(config) == {
            "executables": [],
            "licence_server": "lic.example",
        }

    def test_its_default_is_its_table_s(self, no_installs, tmp_path, acme):
        path = tmp_path / "testing.json"
        path.write_text(json.dumps({"source_type": "acme"}))
        config = Config(config_file=path)
        # No `backends`: found by the search over the backends of its source type.
        assert config.backend_name == "Acme"
        assert acme.settings_of(config)["licence_server"] == "localhost"

    def test_a_key_its_table_does_not_hold_is_refused(
        self, no_installs, tmp_path, acme
    ):
        path = tmp_path / "testing.json"
        path.write_text(
            json.dumps(
                {"source_type": "acme", "backends": {"Acme": {"license_server": "x"}}}
            )
        )
        with pytest.raises(ConfigError) as exc:
            Config(config_file=path)
        assert str(exc.value) == (
            f'{path}: backends.Acme: unknown field "license_server" — '
            'did you mean "licence_server"?'
        )

    def test_its_executable_from_its_block(self, no_installs, tmp_path, acme):
        exe = _program(tmp_path / "acme" / "acmesim")
        located = acme.locate({"executables": [str(exe)]})
        assert isinstance(located, Located)
        assert located.path(EXECUTABLE) == str(exe)
        assert located.parts[0].rule == Rule(SETTING, "executables")

    def test_its_source_type_is_one_a_project_may_say(self, acme):
        from dstf.backends import source_types
        from dstf.reporting.schema_export import build_config_schema

        assert source_types() == ["modelica", "fmu", "julia", "python", "acme"]
        schema = build_config_schema()
        assert schema["properties"]["source_type"]["enum"] == source_types()

    def test_the_schema_reads_an_entry_s_backend_as_the_loader_does(
        self, no_installs, tmp_path, acme
    ):
        """A backend whose name starts with another's — `AcmePlus` beside
        `Acme` — is its own: the loader takes the longest name an entry
        starts with, and the schema's `^Acme` used to hold an `AcmePlus`
        entry to BOTH tables."""
        import re as regex

        from dstf.reporting.schema_export import build_config_schema

        @register("AcmePlus")
        class AcmePlusBackend(Backend):
            settings = {
                **Backend.settings,
                "cores": Setting({"type": "integer"}, 1, "How many cores it may use."),
            }

            def read_result(self, test, test_key):  # pragma: no cover
                raise NotImplementedError

        try:
            path = tmp_path / "testing.json"
            blocks = {"AcmePlus 2": {"cores": 4}, "Acme 3": {"licence_server": "x"}}
            path.write_text(json.dumps({"source_type": "acme", "backends": blocks}))
            Config(config_file=path)  # the loader takes both
            patterns = build_config_schema()["properties"]["backends"][
                "patternProperties"
            ]
            for entry, block in blocks.items():
                matching = [p for p in patterns if regex.search(p, entry)]
                assert len(matching) == 1, (entry, matching)
                assert set(block) <= set(patterns[matching[0]]["properties"])
        finally:
            _REGISTRY.pop("AcmePlus", None)

    def test_it_is_listed_after_the_built_ins(self, acme):
        names = backend_names()
        assert names[:5] == ["Dymola", "OpenModelica", "FMPy", "Julia", "Python"]
        assert "Acme" in names[5:]


# ---------------------------------------------------------------------------
# A run of a backend that was not found starts nothing, on either run mode
# ---------------------------------------------------------------------------


@pytest.fixture
def workers_or_batch(tmp_path):
    """A backend with both run modes — workers that stay alive, and
    `--batch` — that records every run it is asked for. A project of one
    test for it. Returns `(testing.json, the runs)`."""
    from dstf.backends.types import Capability

    runs: list[str] = []

    @register("Twomode")
    class TwomodeBackend(Backend):
        capabilities = frozenset(
            {Capability.BATCH_FALLBACK, Capability.PERSISTENT_WORKERS}
        )
        source_types = frozenset({"twomode"})
        binaries = ("twomodesim",)

        @classmethod
        def persistent_backend_cls(cls):
            return TwomodeWorkers

        def run_tests(self, tests):
            runs.append(type(self).__name__)
            return []

        def read_result(self, test, test_key):  # pragma: no cover - never run
            raise NotImplementedError

    class TwomodeWorkers(TwomodeBackend):
        pass

    project = tmp_path / "proj"
    project.mkdir()
    (project / "test_spec.json").write_text(
        json.dumps({"tests": [{"model": "A", "variables": ["x"]}]})
    )
    path = project / "testing.json"
    path.write_text(
        json.dumps(
            {
                "source_type": "twomode",
                "reference_root": ".",
                "work_dir": "work",
                "backends": {"Twomode": {}},
            }
        )
    )
    try:
        yield path, runs
    finally:
        _REGISTRY.pop("Twomode", None)


class TestARunRefusesUpFront:
    """One place decides (`Backend.require_found`, asked by
    `select_backend` for a run): no backend has a check of its own, so
    `--batch` cannot run on to one failed simulation per test."""

    @pytest.mark.parametrize("mode", [[], ["--batch"]], ids=["workers", "batch"])
    def test_a_backend_path_that_is_not_there(
        self, no_installs, workers_or_batch, capsys, mode
    ):
        """With a `twomodesim` on PATH: the run used to start that one."""
        from dstf.cli import main

        path, runs = workers_or_batch
        _program(no_installs / "twomodesim")
        argv = ["run", "--config", str(path), "--no-browser", *mode]
        with pytest.raises(SystemExit) as exc:
            main([*argv, "--backend-path", "/nope/twomodesim"])
        assert exc.value.code == 1
        assert capsys.readouterr().err == (
            "Twomode not found: executable not found — "
            "--backend-path /nope/twomodesim does not exist. Aborting.\n"
        )
        assert runs == []
        # Nothing was started: no work directory, no dashboard.
        assert not (path.parent / "work").exists()

    @pytest.mark.parametrize("mode", [[], ["--batch"]], ids=["workers", "batch"])
    def test_a_backend_that_is_not_installed(
        self, no_installs, workers_or_batch, capsys, mode
    ):
        from dstf.cli import main

        path, runs = workers_or_batch
        argv = ["run", "--config", str(path), "--no-browser", *mode]
        with pytest.raises(SystemExit) as exc:
            main(argv)
        assert exc.value.code == 1
        assert capsys.readouterr().err == (
            "Twomode not found: executable not found — "
            "looked for twomodesim on PATH. Aborting.\n"
        )
        assert runs == []

    @pytest.mark.parametrize(
        ("mode", "ran"),
        [([], "TwomodeWorkers"), (["--batch"], "TwomodeBackend")],
        ids=["workers", "batch"],
    )
    def test_a_backend_that_is_found_runs(
        self, no_installs, workers_or_batch, capsys, mode, ran
    ):
        from dstf.cli import main

        path, runs = workers_or_batch
        _program(no_installs / "twomodesim")
        main(["run", "--config", str(path), "--no-browser", *mode])
        assert runs == [ran]

    def test_reading_needs_no_executable(self, no_installs, workers_or_batch):
        """`compare` and the tool read results through the backend; only a
        run is refused."""
        from dstf.backends import BackendUnavailableError, select_backend

        path, _ = workers_or_batch
        config = Config(config_file=path)
        assert not config.located.found
        assert select_backend(config, persistent=False, to_run=False) is not None
        with pytest.raises(BackendUnavailableError, match="^Twomode not found: "):
            select_backend(config, persistent=False, to_run=True)
        with pytest.raises(BackendUnavailableError, match="^Twomode not found: "):
            select_backend(config, persistent=True, to_run=True)


def _entry(kind: str, tmp_path: Path, bin_dir: Path) -> tuple[list[str], str | None]:
    """An `executables` list of one ``kind``, and what is said of it where
    it is no program to run (None: it is one)."""
    if kind == "a program":
        return [str(_program(tmp_path / "opt" / "sim"))], None
    if kind == "a name on PATH":
        _program(bin_dir / "sim-2")
        return ["sim-2"], None
    if kind == "a directory":
        (tmp_path / "opt" / "sim-dir").mkdir(parents=True)
        return [str(tmp_path / "opt" / "sim-dir")], "is a directory"
    if kind == "a file that is not executable":
        (tmp_path / "opt").mkdir()
        (tmp_path / "opt" / "notes.txt").write_text("")
        return [str(tmp_path / "opt" / "notes.txt")], "is not executable"
    if kind == "a path that is gone":
        return [str(tmp_path / "opt" / "gone")], "does not exist"
    assert kind == "a name that is nowhere"
    return ["sim-9"], "looked for sim-9 / twomodesim on PATH"


class TestCheckAndRunAgree:
    """`check` says a backend is ready iff a run of it starts: both read
    one `locate`. A directory used to be "ready" to `check` and a
    PermissionError to the run."""

    @pytest.mark.parametrize(
        "kind",
        [
            "a program",
            "a name on PATH",
            "a directory",
            pytest.param(
                "a file that is not executable",
                marks=pytest.mark.skipif(
                    sys.platform.startswith("win"),
                    reason="Windows has no executable bit",
                ),
            ),
            "a path that is gone",
            "a name that is nowhere",
        ],
    )
    def test_on_every_entry(self, no_installs, workers_or_batch, capsys, kind):
        from dstf.cli import main

        path, runs = workers_or_batch
        entries, why = _entry(kind, path.parent, no_installs)
        config = json.loads(path.read_text())
        config["backends"] = {"Twomode": {"executables": entries}}
        path.write_text(json.dumps(config))

        check = main(["check", "--config", str(path)])
        said = capsys.readouterr().out
        try:
            main(["run", "--config", str(path), "--no-browser", "--batch"])
            refused = None
        except SystemExit as exc:
            assert exc.code == 1
            refused = capsys.readouterr().err

        if why is None:
            assert check == 0 and refused is None and runs == ["TwomodeBackend"]
            assert "\nOK — Twomode is ready.\n" in said
        else:
            assert check == 1 and runs == []
            assert why in said and why in (refused or "")
            assert (refused or "").startswith("Twomode not found: executable not ")
