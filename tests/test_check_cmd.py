"""`dstf check [backend]`: one command says where each backend is on this
machine and by which rule it was found, and whether the one the project
runs with is ready. It reads a project and writes nothing — and neither
does `discover`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from dstf.backends.dymola import interface_loader
from dstf.backends.dymola.backend import DymolaBackend
from dstf.backends.julia.backend import JuliaBackend, manifest_julia_version
from dstf.backends.locate import EXECUTABLE, SEARCH, Found, Located, Rule
from dstf.backends.openmodelica.backend import OpenModelicaBackend
from dstf.cli import main
from dstf.config import Config

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def no_installs(tmp_path, monkeypatch):
    """A machine with nothing installed (see `test_backend_locate`)."""
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


def _python_project(directory: Path, **config) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "testing.json"
    path.write_text(json.dumps({"source_type": "python", **config}), encoding="utf-8")
    return path


class TestTheList:
    def test_one_line_per_backend_the_selected_one_marked(
        self, no_installs, tmp_path, capsys
    ):
        path = _python_project(tmp_path / "proj", backends={"Python": {}})
        assert main(["--config", str(path), "check"]) == 0
        out = capsys.readouterr().out.splitlines()
        assert out[0] == f"Backends (testing.json: {path})"
        # The config's entries first, then every other registered backend.
        assert [line[4:].split()[0] for line in out[1:6]] == [
            "Python",
            "Dymola",
            "OpenModelica",
            "FMPy",
            "Julia",
        ]
        assert out[1] == (
            f"  * Python        found      {sys.executable}  (search: the running interpreter)"
        )
        assert out[2] == "    Dymola        not found  looked for dymola on PATH"
        assert out[3] == "    OpenModelica  not found  looked for omc on PATH"
        assert out[5].startswith(
            "    Julia         not found  looked for julia on PATH, "
        )
        assert out[6] == "  * the backend this project runs with"

    def test_the_rule_names_the_config_s_block(self, no_installs, tmp_path, capsys):
        python = _program(no_installs / "python3")
        path = _python_project(
            tmp_path / "proj", backends={"Python": {"executables": ["python3"]}}
        )
        assert main(["--config", str(path), "check"]) == 0
        out = capsys.readouterr().out
        assert f"{python}  (testing.json: backends.Python.executables)" in out
        # …and the full diagnosis of the selected one follows the list.
        assert (
            f"\nPython\n  executable: {python}\n"
            "    found by: testing.json: backends.Python.executables\n"
            "\nOK — Python is ready.\n"
        ) in out

    def test_an_entry_named_after_a_version_is_listed_by_its_name(
        self, no_installs, tmp_path, capsys
    ):
        path = _python_project(tmp_path / "proj", backends={"Python 3.12": {}})
        assert main(["--config", str(path), "check"]) == 0
        out = capsys.readouterr().out
        assert "  * Python 3.12   found" in out
        # No second line for the backend that entry is of.
        assert out.count("the running interpreter") == 2  # the list, the diagnosis
        assert "\nPython 3.12\n  executable:" in out

    def test_the_selected_backend_not_found_is_exit_1_and_says_what_to_do(
        self, no_installs, tmp_path, capsys
    ):
        gone = str(tmp_path / "venv" / "bin" / "python")
        path = _python_project(
            tmp_path / "proj", backends={"Python": {"executables": [gone]}}
        )
        assert main(["--config", str(path), "check"]) == 1
        out = capsys.readouterr().out
        assert (
            f"  * Python        not found  the configured path(s) do not exist: {gone}"
            in out
        )
        assert (
            "\nFAIL — Python is not ready. Fix one of:\n"
            '  1. Install Python, or set "executables" in its block of testing.json\'s '
            '"backends" to where it is\n'
        ) in out

    def test_a_backend_named_and_no_class_for_it(self, no_installs, tmp_path, capsys):
        path = _python_project(tmp_path / "proj", backend="Simulink")
        assert main(["--config", str(path), "check"]) == 1
        out = capsys.readouterr().out
        assert (
            "  * Simulink      not found  no backend is registered under this name"
            in out
        )
        assert out.endswith("Simulink: no backend is registered under this name.\n")


class TestWithNoTestingJson:
    def test_the_search_alone_and_nothing_written(
        self, no_installs, tmp_path, capsys, monkeypatch
    ):
        """A directory with no project: the backends are still where they
        are. What a Modelica project with an empty testing.json would run
        with is marked — with only omc on PATH, OpenModelica."""
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.chdir(empty)
        omc = _program(no_installs / "omc")
        from dstf.backends.openmodelica import session_loader

        monkeypatch.setattr(
            session_loader,
            "describe_om_session",
            lambda: {
                "ompython_installed": True,
                "version": "3.6.0",
                "import_ok": True,
                "error": None,
            },
        )
        assert main(["check"]) == 0
        out = capsys.readouterr().out.splitlines()
        assert out[0] == "Backends (no testing.json here: the search alone)"
        assert out[1].split()[:2] == ["Dymola", "not"]
        assert out[2] == f"  * OpenModelica  found      {omc}  (search: PATH)"
        assert (
            out[6]
            == "  * the backend a Modelica project with no testing.json runs with"
        )
        assert list(empty.iterdir()) == []

    def test_nothing_installed_is_exit_1_with_the_fix(
        self, no_installs, tmp_path, capsys, monkeypatch
    ):
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.chdir(empty)
        assert main(["check"]) == 1
        out = capsys.readouterr().out
        assert "  * Dymola        not found  looked for dymola on PATH" in out
        assert (
            "\nFAIL — Dymola is not ready. Fix one of:\n  1. Install Dymola, or set "
            in out
        )
        assert list(empty.iterdir()) == []

    def test_a_library_with_no_testing_json_gets_none_written(
        self, no_installs, tmp_path, capsys, monkeypatch
    ):
        """`check` and `discover` read; they used to leave a default
        testing.json beside the library as a side effect of looking."""
        lib = tmp_path / "work" / "Lib"
        lib.mkdir(parents=True)
        (lib / "package.mo").write_text("package Lib end Lib;")
        monkeypatch.chdir(tmp_path / "work")
        assert main(["check", "Python"]) == 0
        assert main(["discover"]) == 1  # no tests in it
        out = capsys.readouterr().out
        assert "Created default" not in out
        assert not list(tmp_path.rglob("testing.json"))
        # Every other command still starts a project with one.
        Config(source_path=lib)
        assert "Created default testing.json" in capsys.readouterr().out
        assert (tmp_path / "work" / "testing.json").is_file()


class TestOneBackend:
    def test_a_named_backend_is_diagnosed_alone(self, no_installs, tmp_path, capsys):
        path = _python_project(tmp_path / "proj", backends={"Python": {}})
        assert main(["--config", str(path), "check", "Julia"]) == 1
        out = capsys.readouterr().out
        assert out.startswith(
            "Julia\n  executable: not found — looked for julia on PATH"
        )
        assert "Backends (" not in out
        assert "FAIL — Julia is not ready. Fix one of:\n  1. Install Julia from " in out

    def test_an_unknown_name_is_argparse_s_invalid_choice(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(["check", "Dymolla"])
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "argument backend: invalid choice: 'Dymolla' (choose from " in err
        assert "Dymola, OpenModelica, FMPy, Julia, Python" in err

    def test_the_named_backend_s_entry_gives_its_settings(
        self, no_installs, tmp_path, capsys
    ):
        """`check Python` in a project whose entry is called "Python 3.12"
        diagnoses that entry, by its settings."""
        python = _program(no_installs / "python3.12")
        path = _python_project(
            tmp_path / "proj",
            backend="Dymola",
            backends={"Dymola": {}, "Python 3.12": {"executables": ["python3.12"]}},
        )
        assert main(["--config", str(path), "check", "Python"]) == 0
        out = capsys.readouterr().out
        assert out.startswith(
            f"Python 3.12\n  executable: {python}\n"
            "    found by: testing.json: backends.Python 3.12.executables\n"
        )


class TestTheBackendsOwnDiagnoses:
    def test_julia_says_its_version_and_whether_the_project_s_minimum_is_met(
        self, no_installs, tmp_path, capsys
    ):
        """The minimum is the one the files state: the Julia the project's
        Manifest.toml was resolved with."""
        project = tmp_path / "proj"
        path = _python_project(project, source_type="julia", backends={"Julia": {}})
        (project / "Manifest.toml").write_text(
            '# machine-generated\n\njulia_version = "1.11.9"\nmanifest_format = "2.0"\n'
        )
        julia = _program(
            no_installs / "julia", '#!/bin/sh\necho "julia version 1.10.4"\n'
        )
        assert main(["--config", str(path), "check"]) == 1
        out = capsys.readouterr().out
        assert "  version: julia version 1.10.4\n" in out
        assert (
            f"  needed: Julia 1.11 or newer — {project / 'Manifest.toml'} "
            "was resolved with 1.11.9: NOT met\n"
        ) in out
        assert "  1. Install Julia 1.11 or newer, or resolve the project again" in out

        julia.write_text('#!/bin/sh\necho "julia version 1.12.0"\n')
        assert main(["--config", str(path), "check"]) == 0
        out = capsys.readouterr().out
        assert "was resolved with 1.11.9: met\n" in out
        assert out.endswith("\nOK — Julia is ready.\n")

    def test_julia_with_no_project_says_only_the_version(self, no_installs, capsys):
        julia = _program(
            no_installs / "julia", '#!/bin/sh\necho "julia version 1.10.4"\n'
        )
        located = JuliaBackend.locate({})
        diagnosis = JuliaBackend.diagnose(located)
        assert diagnosis.usable
        assert diagnosis.lines[:3] == (
            f"  executable: {julia}",
            "    found by: search: PATH",
            "  version: julia version 1.10.4",
        )
        assert not any("needed" in line for line in diagnosis.lines)

    def test_the_julia_example_states_its_minimum(self):
        """What README.md and pyproject.toml's `julia` marker say the Julia
        example needs is what its Manifest.toml was resolved with."""
        needed = manifest_julia_version(
            REPO / "examples" / "julia" / "JuliaMtkTestingLib"
        )
        assert needed is not None
        minimum = ".".join(needed.split(".")[:2])
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        assert f"install Julia {minimum}+" in readme
        pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        assert f'"julia: requires Julia {minimum}+ ' in pyproject

    def test_dymola_needs_its_interface_to_import(self, no_installs, monkeypatch):
        dymola = _program(no_installs / "dymola")
        located = DymolaBackend.locate({})
        assert located.found and not located.complete
        diagnosis = DymolaBackend.diagnose(located, entry="Dymola")
        assert not diagnosis.usable
        assert diagnosis.lines[0] == f"  executable: {dymola}"
        assert diagnosis.lines[2].startswith("  Python interface: not found — ")
        assert "  import ok: False" in diagnosis.lines
        assert (
            len(diagnosis.fixes) == 1 and "DYMOLA_INTERFACE_PATH" in diagnosis.fixes[0]
        )

    def test_dymola_ready_when_the_located_interface_imports(
        self, no_installs, monkeypatch
    ):
        seen = []

        def describe(found):
            seen.append(found)
            return {
                "archive": found.path,
                "format": "whl",
                "discovered_dirs": [],
                "sys_path_entry": "/cache/dymola",
                "import_ok": True,
                "error": None,
                "dymola_interface_module": "dymola.dymola_interface",
            }

        monkeypatch.setattr(interface_loader, "describe_dymola_interface", describe)
        located = Located(
            (
                Found(EXECUTABLE, "/opt/dymola/bin64/dymola.sh", Rule(SEARCH, "PATH")),
                Found(
                    interface_loader.INTERFACE,
                    "/opt/dymola/x.whl",
                    Rule(SEARCH, "beside the executable"),
                ),
            )
        )
        diagnosis = DymolaBackend.diagnose(located)
        assert diagnosis.usable and diagnosis.fixes == ()
        assert [found.path for found in seen] == ["/opt/dymola/x.whl"]
        assert diagnosis.lines == (
            "  executable: /opt/dymola/bin64/dymola.sh",
            "    found by: search: PATH",
            "  Python interface: /opt/dymola/x.whl",
            "    found by: search: beside the executable",
            "  interface format: .whl",
            "  sys.path entry: /cache/dymola",
            "  import ok: True",
            "  module: dymola.dymola_interface",
        )

    def test_openmodelica_needs_ompython_as_well_as_omc(self, no_installs, monkeypatch):
        from dstf.backends.openmodelica import session_loader

        omc = _program(no_installs / "omc")
        located = OpenModelicaBackend.locate({})

        def describe(import_ok):
            return lambda: {
                "ompython_installed": import_ok,
                "version": "3.6.0" if import_ok else None,
                "import_ok": import_ok,
                "error": None
                if import_ok
                else "ModuleNotFoundError: No module named 'OMPython'",
            }

        monkeypatch.setattr(session_loader, "describe_om_session", describe(True))
        ready = OpenModelicaBackend.diagnose(located)
        assert ready.usable
        assert ready.lines == (
            f"  executable: {omc}",
            "    found by: search: PATH",
            "  OMPython installed: True",
            "  OMPython version: 3.6.0",
            "  import ok: True",
        )
        monkeypatch.setattr(session_loader, "describe_om_session", describe(False))
        missing = OpenModelicaBackend.diagnose(located)
        assert not missing.usable
        assert len(missing.fixes) == 1 and "[om] extra" in missing.fixes[0]
        assert "--batch" in missing.fixes[0]

    def test_a_backend_that_says_no_more_gets_the_locate_lines(self, no_installs):
        """FMPy and Python override nothing: a new backend has a diagnosis
        for free."""
        from dstf.backends.python.backend import PythonBackend

        located = PythonBackend.locate({})
        diagnosis = PythonBackend.diagnose(located, entry="")
        assert diagnosis.usable
        assert diagnosis.lines == (
            f"  executable: {sys.executable}",
            "    found by: search: the running interpreter",
        )
