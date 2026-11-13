"""`dstf uses` through `main`, over a copy of the bundled Modelica library
and a registered fake backend that answers what each test uses: every
sentence the command prints, its exit codes, its file, and the hint."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import PROJECT_ROOT, GitRepo
from dstf.backends import _REGISTRY, Backend, register
from dstf.backends.dymola import interface_loader
from dstf.backends.locate import EXECUTABLE, SEARCH, Found, Located, Rule
from dstf.backends.types import Capability
from dstf.cli import main

LIB = "ModelicaTestingLib"
UNIT_TESTS = f"{LIB}.Components.UnitTests"
EXAMPLE = f"{LIB}.Icons.Example"
HEAT = f"{LIB}.Examples.HeatRodTest"
FAKE, CANNOT = "FakeUses", "FakeCannot"

#: The bundled library's tests that hold a UnitTests component — what the
#: fake says use it.
WITH = [
    f"{LIB}.Examples.{n}"
    for n in (
        "ConstantTest",
        "EventTest",
        "FrequencyTest",
        "IntervalTest",
        "MetricTreeTest",
        "MultiFrequencyTest",
        "PointsCheckTest",
        "RangeCheckTest",
        "SimpleTest",
        "TubeToleranceTest",
    )
]


@pytest.fixture
def nothing_installed(tmp_path, monkeypatch):
    """No real simulator is found (tests/test_backend_locate.py's
    `no_installs`): the hint reads the same here, where Dymola and omc are
    installed, as on CI. Returns the directory `PATH` is."""
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


def _located(found: bool) -> Located:
    if found:
        return Located((Found(EXECUTABLE, sys.executable, Rule(SEARCH, "the test")),))
    return Located((Found(EXECUTABLE, detail="nowhere"),))


@pytest.fixture
def env(tmp_path, monkeypatch, nothing_installed):
    """A copy of the bundled library whose testing.json names the fake
    `FakeUses`; `FakeCannot` is registered beside it and cannot answer.
    ``env.answer(model_id)`` is what `FakeUses` says of a test (a str: it
    could not be asked), ``env.left_out`` the tests it says nothing of and
    ``env.fails`` what it raises instead of answering; ``env.asked`` the
    tests it was asked for, per call; ``env.found`` whether it is installed
    here."""
    lib = tmp_path / "proj" / LIB
    shutil.copytree(PROJECT_ROOT / "examples" / "modelica" / LIB, lib)
    config = lib / "Resources" / "ReferenceResults" / "testing.json"
    data = json.loads(config.read_text(encoding="utf-8"))
    del data["backends"]
    data["backend"] = FAKE
    config.write_text(json.dumps(data), encoding="utf-8")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    env = SimpleNamespace(
        config=config,
        lib=lib,
        data=data,
        cwd=cwd,
        asked=[],
        found=True,
        left_out=set(),
        fails=None,
    )

    def answer(model_id: str) -> frozenset[str] | str:
        used = {LIB, f"{LIB}.Examples", model_id}
        if model_id in WITH:
            used |= {f"{LIB}.Components", UNIT_TESTS}
        return frozenset(used)

    env.answer = answer

    @register(FAKE)
    class FakeUses(Backend):
        capabilities = frozenset({Capability.USES})
        source_types = frozenset({"modelica"})

        @classmethod
        def locate(cls, settings):
            return _located(env.found)

        def read_result(self, test, test_key, run_result):
            raise AssertionError("uses reads no result")

        def uses(self, tests):
            env.asked.append([t.model_id for t in tests])
            if env.fails is not None:
                raise env.fails
            return {
                t.model_id: env.answer(t.model_id)
                for t in tests
                if t.model_id not in env.left_out
            }

    @register(CANNOT)
    class FakeCannot(Backend):
        source_types = frozenset({"modelica"})

        @classmethod
        def locate(cls, settings):
            return _located(True)

        def read_result(self, test, test_key, run_result):
            raise AssertionError("uses reads no result")

    yield env
    del _REGISTRY[FAKE], _REGISTRY[CANNOT]


def _uses(env, capsys, *argv):
    rc = main(["uses", "--config", str(env.config), *argv])
    out, err = capsys.readouterr()
    return rc, out, err


def _write_config(env) -> None:
    env.config.write_text(json.dumps(env.data), encoding="utf-8")


# --------------------------------------------------------------------------
# The answer
# --------------------------------------------------------------------------


def test_a_class_s_users_are_listed_and_stdout_is_only_the_answer(env, capsys):
    rc, out, err = _uses(env, capsys, UNIT_TESTS)
    assert rc == 0
    assert out == f"{UNIT_TESTS} is used by 10 of 14 tests:\n" + "".join(
        f"  {t}\n" for t in WITH
    )
    assert err == "Asking FakeUses what 14 tests use ...\n"
    assert len(env.asked) == 1 and len(env.asked[0]) == 14


def test_a_class_no_test_uses(env, capsys):
    rc, out, _ = _uses(env, capsys, EXAMPLE)
    assert rc == 0
    assert out == f"{EXAMPLE} is used by none of the 14 tests.\n"


def test_several_classes_a_block_each_in_the_order_given_then_the_union(env, capsys):
    simple = f"{LIB}.Examples.SimpleTest"
    rc, out, _ = _uses(env, capsys, simple, EXAMPLE, UNIT_TESTS)
    assert rc == 0
    assert out == (
        f"{simple} is used by 1 of 14 tests:\n"
        f"  {simple}\n"
        f"{EXAMPLE} is used by none of the 14 tests.\n"
        f"{UNIT_TESTS} is used by 10 of 14 tests:\n"
        + "".join(f"  {t}\n" for t in WITH)
        + "10 of 14 tests use at least one of them.\n"
    )
    # one question for all the classes
    assert len(env.asked) == 1


def test_a_test_that_could_not_be_asked_counts_as_using_every_class(env, capsys):
    answer = env.answer
    env.answer = lambda m: (
        "Error: class\n  HeatRodTest not found." if m == HEAT else answer(m)
    )
    rc, out, _ = _uses(env, capsys, EXAMPLE, UNIT_TESTS)
    assert rc == 0
    assert out == (
        f"{EXAMPLE} is used by 1 of 14 tests:\n"
        f"{UNIT_TESTS} is used by 11 of 14 tests:\n"
        + "".join(f"  {t}\n" for t in WITH)
        + "Could not be asked (counted as using each):\n"
        # a reason is printed on its test's one line, whatever the backend said
        f"  {HEAT} — Error: class HeatRodTest not found.\n"
        "11 of 14 tests use at least one of them.\n"
    )


def test_a_test_the_backend_left_out_counts_as_not_asked(env, capsys):
    env.left_out = {HEAT}
    rc, out, _ = _uses(env, capsys, EXAMPLE)
    assert rc == 0
    assert out == (
        f"{EXAMPLE} is used by 1 of 14 tests:\n"
        "Could not be asked (counted as using it):\n"
        f"  {HEAT} — FakeUses gave no answer for it\n"
    )


def test_an_answer_without_the_test_s_own_model_is_not_believed(env, capsys):
    """An answer always holds the model's own class: one that does not
    (an empty file read as nothing, a plug-in's slip) would say the test
    uses nothing. The test could not be asked, and counts as a user."""
    simple = f"{LIB}.Examples.SimpleTest"
    answer = env.answer
    env.answer = lambda m: frozenset() if m == simple else answer(m)
    out_file = env.cwd / "uses.txt"
    rc, out, _ = _uses(env, capsys, EXAMPLE, "-o", str(out_file))
    assert rc == 0
    assert out == (
        f"{EXAMPLE} is used by 1 of 14 tests:\n"
        "Could not be asked (counted as using it):\n"
        f"  {simple} — FakeUses's answer does not hold the test's own model\n"
        f"Wrote {out_file}: 1 test\n"
    )
    assert out_file.read_text(encoding="utf-8") == f"{simple}\n"


def test_filter_and_package_narrow_the_tests_asked(env, capsys):
    rc, out, _ = _uses(env, capsys, UNIT_TESTS, "--filter", f"{LIB}.Examples.S*")
    assert rc == 0
    assert env.asked == [
        [f"{LIB}.Examples.SimpleTest", f"{LIB}.Examples.SimulateOnlyTest"]
    ]
    assert (
        out == f"{UNIT_TESTS} is used by 1 of 2 tests:\n  {LIB}.Examples.SimpleTest\n"
    )


def test_one_test_is_one_test(env, capsys):
    simple = f"{LIB}.Examples.SimpleTest"
    rc, out, err = _uses(env, capsys, UNIT_TESTS, EXAMPLE, "--package", simple)
    assert rc == 0
    assert env.asked == [[simple]]
    assert err == "Asking FakeUses what 1 test uses ...\n"
    assert out == (
        f"{UNIT_TESTS} is used by the 1 test:\n"
        f"  {simple}\n"
        f"{EXAMPLE} is not used by the 1 test.\n"
        "The 1 test uses at least one of them.\n"
    )

    rc, out, _ = _uses(env, capsys, EXAMPLE, f"{LIB}.Icons", "--package", simple)
    assert rc == 0
    assert out.endswith("No test uses any of them.\n")


# --------------------------------------------------------------------------
# --output
# --------------------------------------------------------------------------


def test_output_is_the_union_sorted_and_nothing_else_is_written(env, capsys, tmp_path):
    answer = env.answer
    env.answer = lambda m: "no answer" if m == HEAT else answer(m)
    before = sorted(p for p in tmp_path.rglob("*"))
    out_file = env.cwd / "uses.txt"
    simple = f"{LIB}.Examples.SimpleTest"
    rc, out, _ = _uses(env, capsys, simple, UNIT_TESTS, "-o", str(out_file))
    assert rc == 0
    expected = sorted([*WITH, HEAT])
    assert out_file.read_text(encoding="utf-8") == "".join(f"{t}\n" for t in expected)
    assert out.endswith(f"Wrote {out_file}: 11 tests\n")
    # no work dir, nothing beside the library or in the current directory
    assert sorted(p for p in tmp_path.rglob("*")) == sorted([*before, out_file])


def test_the_output_file_selects_the_same_tests_for_another_command(env, capsys):
    out_file = env.cwd / "uses.txt"
    rc, _, _ = _uses(env, capsys, UNIT_TESTS, "-o", str(out_file))
    assert rc == 0
    rc = main(["discover", "--config", str(env.config), "--filter", f"@{out_file}"])
    out, _ = capsys.readouterr()
    assert rc == 0
    listed = sorted(
        line.split()[0] for line in out.splitlines() if line.startswith(f"{LIB}.")
    )
    assert listed == WITH
    assert "Total: 10 tests" in out


# --------------------------------------------------------------------------
# What is refused, and the exit codes
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        f"{LIB}.Components.Nope",
        "Other.Lib.Class",
        "UnitTests",
        f"{LIB}.Examples.SimpleTest.x",
    ],
)
def test_an_unknown_class_is_refused_before_the_simulator_is_asked(env, capsys, name):
    rc, out, err = _uses(env, capsys, UNIT_TESTS, name)
    assert rc == 2
    assert err == (
        f"Error: {name} is no class of the library {LIB} or of a dependency "
        "given as a path.\n"
    )
    assert out == "" and env.asked == []


def test_a_type_an_operator_and_a_path_dependency_s_class_are_classes(env, capsys):
    """What a simulator's answer can hold, the check must know: `find_class`
    (a test's model) knows no type, no operator, no one-file library and
    not the library itself; a dependency is named by its package, not its
    directory."""
    (env.lib / "Components" / "Extra.mo").write_text(
        f"""within {LIB}.Components;
package Extra
  type Level = Real(unit="m");
  operator record Cx
    Real re;
    encapsulated operator '+'
      function add input Cx a; output Cx c; end add;
    end '+';
  end Cx;
end Extra;
""",
        encoding="utf-8",
    )
    dep = env.lib.parent / "Dep 1.0"
    dep.mkdir()
    (dep / "package.mo").write_text(
        "within ;\npackage Dep\n  model M end M;\nend Dep;\n"
    )
    one = env.lib.parent / "One.mo"
    one.write_text("within ;\npackage One\n  type T = Real;\nend One;\n")
    env.data["dependencies"] = ["../../../Dep 1.0", "../../../One.mo", "Modelica"]
    _write_config(env)

    known = [
        LIB,
        f"{LIB}.Components.Extra.Level",
        f"{LIB}.Components.Extra.Cx.'+'.add",
        "Dep",
        "Dep.M",
        "One",
        "One.T",
    ]
    rc, _, err = _uses(env, capsys, *known)
    assert rc == 0, err
    for name in (
        "Dep 1.0",
        "Dep.Nope",
        "One.Nope",
        "Modelica.Icons.Package",
        f"{LIB}.Components.Extra.Cx.'-'",
    ):
        rc, _, err = _uses(env, capsys, name)
        assert rc == 2, (name, err)


def test_no_test_to_ask_is_exit_1(env, capsys):
    rc, out, err = _uses(env, capsys, UNIT_TESTS, "--package", f"{LIB}.Nope")
    assert (rc, out, err) == (1, "", "No tests found.\n")
    assert env.asked == []


def test_a_simulator_that_could_not_be_asked_is_exit_1_and_counts_nothing(env, capsys):
    env.fails = RuntimeError("failed to load library package.mo — no licence")
    out_file = env.cwd / "uses.txt"
    rc, out, err = _uses(env, capsys, UNIT_TESTS, "-o", str(out_file))
    assert rc == 1 and out == ""
    assert err == (
        "Asking FakeUses what 14 tests use ...\n"
        "FakeUses could not be asked: failed to load library package.mo — no licence\n"
    )
    assert not out_file.exists()


def test_a_backend_that_can_answer_but_is_not_found_says_so(env, capsys):
    """The capability is read on the class a run without --batch uses:
    OpenModelica declares it on its persistent class. With no omc here the
    refusal is the backend's own, and no hint follows."""
    rc, out, err = _uses(env, capsys, UNIT_TESTS, "--backend", "OpenModelica")
    assert rc == 1 and out == ""
    assert err.startswith("OpenModelica not found")
    assert "cannot say" not in err and "--backend" not in err


# --------------------------------------------------------------------------
# A backend that cannot answer, and the hint
# --------------------------------------------------------------------------


def test_the_capability_is_checked_before_the_class(env, capsys):
    """A backend that cannot answer says so first: a mistyped class is no
    reason to try another backend."""
    rc, out, err = _uses(env, capsys, f"{LIB}.Nope", "--backend", CANNOT)
    assert (rc, out) == (1, "")
    assert err.startswith("FakeCannot cannot say what a test uses.\n")


def test_a_backend_that_cannot_answer_names_one_installed_that_can(env, capsys):
    rc, out, err = _uses(env, capsys, UNIT_TESTS, "--backend", CANNOT)
    assert rc == 1 and out == "" and env.asked == []
    assert err == (
        "FakeCannot cannot say what a test uses.\n"
        "FakeUses is installed here and can: add --backend FakeUses.\n"
    )


def test_no_hint_when_none_that_can_is_installed(env, capsys):
    env.found = False
    rc, out, err = _uses(env, capsys, UNIT_TESTS, "--backend", CANNOT)
    assert (rc, out) == (1, "")
    assert err == "FakeCannot cannot say what a test uses.\n"


def test_the_hint_reads_the_capability_on_the_class_a_run_uses(
    env, capsys, nothing_installed
):
    """OpenModelica declares it on its persistent class, never on the
    registered one: a program named omc on PATH (found, never started) is
    named."""
    env.found = False
    omc = nothing_installed / "omc"
    omc.write_text("#!/bin/sh\nexit 1\n")
    omc.chmod(0o755)
    rc, _, err = _uses(env, capsys, UNIT_TESTS, "--backend", CANNOT)
    assert rc == 1
    assert err == (
        "FakeCannot cannot say what a test uses.\n"
        "OpenModelica is installed here and can: add --backend OpenModelica.\n"
    )


def test_the_hint_names_the_project_s_entry_found_by_its_own_block(
    env, capsys, tmp_path
):
    env.found = False
    omc = tmp_path / "opt" / "omc"
    omc.parent.mkdir()
    omc.write_text("#!/bin/sh\nexit 1\n")
    omc.chmod(0o755)
    env.data["backends"] = {"OpenModelica 1.27": {"executables": [str(omc)]}}
    env.data["backend"] = CANNOT
    _write_config(env)
    rc, _, err = _uses(env, capsys, UNIT_TESTS)
    assert rc == 1
    assert err == (
        "FakeCannot cannot say what a test uses.\n"
        'OpenModelica 1.27 is installed here and can: add --backend "OpenModelica 1.27".\n'
    )


def test_no_hint_when_none_can_answer_for_the_source_type(env, capsys, tmp_path):
    lib = tmp_path / "py"
    shutil.copytree(
        PROJECT_ROOT / "examples" / "python" / "PythonTestingLib",
        lib,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    config = lib / "Resources" / "ReferenceResults" / "testing.json"
    rc = main(["uses", "X.Y", "--config", str(config)])
    out, err = capsys.readouterr()
    assert (rc, out) == (1, "")
    assert err == "Python cannot say what a test uses.\n"


# --------------------------------------------------------------------------
# --since: the classes changed since a git revision
# --------------------------------------------------------------------------

#: Found before `nothing_installed` empties PATH.
GIT = shutil.which("git")
SIMPLE = f"{LIB}.Examples.SimpleTest"
EXAMPLES = f"{LIB}.Examples"


@pytest.fixture
def repo(env, nothing_installed, monkeypatch):
    """`env`'s project committed once in a repository whose top is the
    directory that HOLDS the library. `env` empties PATH so that no
    simulator is found: git is put back on it, and the code under test
    reads no git configuration but the repository's."""
    assert GIT is not None
    (nothing_installed / "git").symlink_to(GIT)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    env.git = GitRepo(env.lib.parent)
    env.first = env.git.commit("the library as it was")
    env.short = env.git.git("rev-parse", "--short", "HEAD")
    return env


def _touch(repo, *parts: str) -> None:
    """An uncommitted edit of a file of the library: a comment."""
    path = repo.lib.joinpath(*parts)
    path.write_text(path.read_text(encoding="utf-8") + "// edited\n", encoding="utf-8")


def _asked(out: str) -> list[str]:
    """The classes the answer has a block for, in order."""
    return [
        line.split(" is ")[0]
        for line in out.splitlines()
        if not line.startswith(" ") and " is " in line
    ]


def _dependency(where: Path, name: str = "Dep") -> Path:
    where.mkdir(parents=True)
    (where / "package.mo").write_text(
        f"within ;\npackage {name}\n  model M end M;\nend {name};\n"
    )
    return where


def test_since_an_uncommitted_edit_is_a_changed_class_asked_as_a_named_one(
    repo, capsys
):
    _touch(repo, "Components", "UnitTests.mo")
    rc, out, err = _uses(repo, capsys, "--since", "HEAD")
    assert rc == 0, err
    assert out == (
        f"Changed since HEAD ({repo.short} the library as it was): 1 file\n"
        f"  Components/UnitTests.mo  {UNIT_TESTS}\n"
        f"{UNIT_TESTS} is used by 10 of 14 tests:\n" + "".join(f"  {t}\n" for t in WITH)
    )
    assert re.fullmatch(
        r"Comparing \d+ files with HEAD \.\.\.\nAsking FakeUses what 14 tests use \.\.\.\n",
        err,
    )


def test_since_named_classes_first_then_the_changed_ones_by_name_each_once(
    repo, capsys
):
    """By class name, not by path: `Examples/SimpleTest.mo` sorts before
    `Examples/package.mo`, its class after the package's."""
    _touch(repo, "Examples", "package.mo")
    _touch(repo, "Examples", "SimpleTest.mo")
    _touch(repo, "Components", "UnitTests.mo")
    rc, out, _ = _uses(repo, capsys, EXAMPLE, UNIT_TESTS, EXAMPLE, "--since", "HEAD")
    assert rc == 0
    assert out.startswith(
        f"Changed since HEAD ({repo.short} the library as it was): 3 files\n"
        f"  Components/UnitTests.mo  {UNIT_TESTS}\n"
        f"  Examples/SimpleTest.mo   {SIMPLE}\n"
        f"  Examples/package.mo      {EXAMPLES}\n"
    )
    assert _asked(out) == [EXAMPLE, UNIT_TESTS, EXAMPLES, SIMPLE]
    assert out.endswith("14 of 14 tests use at least one of them.\n")
    assert len(repo.asked) == 1


def test_since_a_deleted_class_file_counts_the_package_that_held_it(repo, capsys):
    (repo.lib / "Examples" / "SimpleTest.mo").unlink()
    rc, out, _ = _uses(repo, capsys, "--since", "HEAD")
    assert rc == 0
    assert out.startswith(
        f"Changed since HEAD ({repo.short} the library as it was): 1 file\n"
        f"  Examples/SimpleTest.mo  deleted → {EXAMPLES}\n"
        f"{EXAMPLES} is used by 14 of 14 tests:\n"
    )


def test_since_nothing_changed_starts_no_simulator(repo, capsys):
    rc, out, err = _uses(repo, capsys, "--since", "HEAD")
    assert rc == 0 and repo.asked == []
    assert out == f"Nothing changed since HEAD ({repo.short} the library as it was).\n"
    assert not err.startswith("Asking") and "Asking" not in err


def test_since_nothing_changed_writes_an_empty_file_that_selects_no_test(repo, capsys):
    out_file = repo.cwd / "uses.txt"
    rc, out, _ = _uses(repo, capsys, "--since", "HEAD", "-o", str(out_file))
    assert rc == 0 and repo.asked == []
    assert out == (
        f"Nothing changed since HEAD ({repo.short} the library as it was).\n"
        f"Wrote {out_file}: 0 tests\n"
    )
    assert out_file.read_bytes() == b""
    rc = main(["discover", "--config", str(repo.config), "--filter", f"@{out_file}"])
    out, _ = capsys.readouterr()
    assert (rc, out) == (1, "No tests found.\n")


def test_since_nothing_changed_still_answers_for_a_class_named(repo, capsys):
    rc, out, _ = _uses(repo, capsys, SIMPLE, "--since", "HEAD")
    assert rc == 0
    assert out == (
        f"Nothing changed since HEAD ({repo.short} the library as it was).\n"
        f"{SIMPLE} is used by 1 of 14 tests:\n  {SIMPLE}\n"
    )


def test_since_a_file_no_class_stores_counts_every_test_and_starts_no_simulator(
    repo, capsys
):
    image = repo.lib / "Resources" / "Images" / "ThermostatBuildingTest.png"
    image.write_bytes(image.read_bytes() + b"\0")
    _touch(repo, "Examples", "SimpleTest.mo")
    out_file = repo.cwd / "uses.txt"
    rc, out, _ = _uses(repo, capsys, "--since", "HEAD", "-o", str(out_file))
    assert rc == 0 and repo.asked == []
    assert out == (
        f"Changed since HEAD ({repo.short} the library as it was): 2 files\n"
        "  Resources/Images/ThermostatBuildingTest.png  (no class stores it)\n"
        f"  Examples/SimpleTest.mo                       {SIMPLE}\n"
        "1 file that no class stores changed: every test is counted (14 tests).\n"
        f"Wrote {out_file}: 14 tests\n"
    )
    assert len(out_file.read_text(encoding="utf-8").splitlines()) == 14
    # a class named is not asked either, and the line says so
    rc, out, _ = _uses(repo, capsys, UNIT_TESTS, "--since", "HEAD", "--package", SIMPLE)
    assert rc == 0 and repo.asked == []
    assert out.endswith(
        "1 file that no class stores changed: every test is counted (1 test); "
        "the class named is not asked.\n"
    )


def test_since_dstf_s_own_files_are_not_changes(repo, capsys):
    """The bundled library keeps its reference root inside itself: an
    accepted baseline or an edited spec is no change of the library."""
    refs = repo.lib / "Resources" / "ReferenceResults"
    spec = refs / "test_spec.json"
    spec.write_text(json.dumps(json.loads(spec.read_text(encoding="utf-8")), indent=1))
    baseline = next((refs / "OpenModelica").rglob("ref_*.json"))
    baseline.write_text(baseline.read_text(encoding="utf-8") + "\n")
    rc, out, _ = _uses(repo, capsys, "--since", "HEAD")
    assert rc == 0 and repo.asked == []
    assert out.startswith("Nothing changed since HEAD (")


def test_since_a_changed_class_is_not_put_through_the_class_check(repo, capsys):
    """A file that differs is what it is: a .mo file in a folder that is no
    package is asked for, and no test uses it."""
    (repo.lib / "Scratch").mkdir()
    (repo.lib / "Scratch" / "Try.mo").write_text("model Try end Try;\n")
    rc, out, _ = _uses(repo, capsys, "--since", "HEAD")
    assert rc == 0
    assert out.endswith(f"{LIB}.Scratch.Try is used by none of the 14 tests.\n")


def test_since_lists_twenty_files_and_asks_for_every_class(repo, capsys):
    for i in range(25):
        (repo.lib / "Examples" / f"New{i:02}.mo").write_text(
            f"within {EXAMPLES};\nmodel New{i:02}\nend New{i:02};\n"
        )
    rc, out, _ = _uses(repo, capsys, "--since", "HEAD")
    assert rc == 0
    head = out.splitlines()[:22]
    assert (
        head[0] == f"Changed since HEAD ({repo.short} the library as it was): 25 files"
    )
    assert head[1:21] == [
        f"  Examples/New{i:02}.mo  {EXAMPLES}.New{i:02}" for i in range(20)
    ]
    assert head[21] == "  … and 5 more"
    assert _asked(out) == [f"{EXAMPLES}.New{i:02}" for i in range(25)]


def test_since_a_long_subject_is_cut(repo, capsys):
    subject = "a subject far longer than one line of the block would ever hold, cut"
    _touch(repo, "Examples", "SimpleTest.mo")
    repo.git.commit(subject)
    rc, out, _ = _uses(repo, capsys, "--since", "HEAD~1")
    assert rc == 0
    short = repo.git.git("rev-parse", "--short", "HEAD~1")
    assert out.startswith(
        f"Changed since HEAD~1 ({short} the library as it was): 1 file\n"
    )
    rc, out, _ = _uses(repo, capsys, "--since", "HEAD")
    short = repo.git.git("rev-parse", "--short", "HEAD")
    assert out == (
        f"Nothing changed since HEAD ({short} a subject far longer than one line "
        "of the block would ever…).\n"
    )


def test_since_the_simulator_failing_prints_no_change(repo, capsys):
    """stdout holds the answer or nothing: the changes are printed with
    the answer."""
    _touch(repo, "Examples", "SimpleTest.mo")
    repo.fails = RuntimeError("no licence")
    rc, out, err = _uses(repo, capsys, "--since", "HEAD")
    assert (rc, out) == (1, "")
    assert err.endswith("FakeUses could not be asked: no licence\n")


def test_since_one_repository_holding_several_roots_has_one_line(repo, capsys):
    """A dependency's folder and a one-file dependency beside the library:
    one revision, each root's files under its top package."""
    _dependency(repo.lib.parent / "Dep 1.0")
    (repo.lib.parent / "One.mo").write_text(
        "within ;\npackage Uno\nend Uno;\npackage Due\nend Due;\n"
    )
    repo.data["dependencies"] = ["../../../Dep 1.0", "../../../One.mo"]
    _write_config(repo)
    repo.git.commit("two dependencies")
    short = repo.git.git("rev-parse", "--short", "HEAD")
    (repo.lib.parent / "Dep 1.0" / "package.mo").write_text(
        "within ;\npackage Dep\n  model M Real x; end M;\nend Dep;\n"
    )
    (repo.lib.parent / "One.mo").write_text(
        (repo.lib.parent / "One.mo").read_text() + "// edited\n"
    )
    _touch(repo, "Examples", "SimpleTest.mo")
    rc, out, err = _uses(repo, capsys, "--since", "HEAD")
    assert rc == 0, err
    assert out.startswith(
        f"Changed since HEAD ({short} two dependencies): 3 files\n"
        f"  {LIB}:\n"
        f"    Examples/SimpleTest.mo  {SIMPLE}\n"
        "  Dep:\n"
        "    package.mo              Dep\n"
        "  Uno / Due:\n"
        "    One.mo                  Uno, Due\n"
        "Dep is used by none of the 14 tests.\n"
    )
    assert _asked(out) == ["Dep", "Due", SIMPLE, "Uno"]


def test_since_a_root_in_a_second_repository_has_its_own_line(repo, capsys, tmp_path):
    dep = _dependency(tmp_path / "elsewhere" / "Dep 1.0")
    other = GitRepo(dep)
    other.commit("the dependency")
    dep_short = other.git("rev-parse", "--short", "HEAD")
    repo.data["dependencies"] = [str(dep)]
    _write_config(repo)
    repo.git.commit("name the dependency")
    short = repo.git.git("rev-parse", "--short", "HEAD")
    _touch(repo, "Examples", "SimpleTest.mo")
    rc, out, err = _uses(repo, capsys, "--since", "HEAD")
    assert rc == 0, err
    assert out.startswith(
        f"Changed since HEAD ({short} name the dependency): 1 file\n"
        f"  {LIB}:\n"
        f"    Examples/SimpleTest.mo  {SIMPLE}\n"
        f"Nothing changed since HEAD ({dep_short} the dependency) in Dep.\n"
        f"{SIMPLE} is used by 1 of 14 tests:\n"
    )
    (dep / "package.mo").write_text("within ;\npackage Dep\n  // x\nend Dep;\n")
    rc, out, _ = _uses(repo, capsys, "--since", "HEAD")
    assert f"Changed since HEAD ({dep_short} the dependency): 1 file\n" in out
    assert "  Dep:\n    package.mo  Dep\n" in out
    # a commit only the library's repository knows
    rc, out, err = _uses(repo, capsys, "--since", repo.first)
    assert (rc, out) == (2, "")
    assert err == (
        f"Error: unknown revision '{repo.first}' in {dep} (it holds Dep): give a "
        "revision every repository of the project knows, a branch or a tag\n"
    )


def test_since_a_dependency_in_no_repository_is_said_and_not_compared(
    repo, capsys, tmp_path, monkeypatch
):
    dep = _dependency(tmp_path / "unpacked" / "Dep 1.0")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path / "unpacked"))
    repo.data["dependencies"] = [str(dep)]
    _write_config(repo)
    rc, out, err = _uses(repo, capsys, "--since", "HEAD")
    assert rc == 0
    assert out == (
        f"Nothing changed since HEAD ({repo.short} the library as it was).\n"
        "Dep: not under git, not compared.\n"
    )
    assert err.startswith("Dep is in no git repository: its files are not compared.\n")


def test_since_a_project_in_no_repository_is_exit_2(
    env, nothing_installed, capsys, monkeypatch
):
    (nothing_installed / "git").symlink_to(GIT)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(env.lib.parent.parent))
    rc, out, err = _uses(env, capsys, "--since", "HEAD")
    assert (rc, out) == (2, "")
    assert err == f"Error: not a git repository: {env.lib}\n"


def test_neither_a_class_nor_since_is_a_usage_error(env, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["uses", "--config", str(env.config)])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert err.startswith("usage: dstf uses ")
    assert err.endswith("dstf uses: error: name a CLASS, or give --since REV\n")


def test_since_what_a_root_without_a_name_and_a_nested_repository_print(repo, capsys):
    """A dependency whose package.mo is gone cannot be asked as a class:
    every test is counted. A nested repository is named, not compared."""
    dep = _dependency(repo.lib.parent / "Dep")
    repo.data["dependencies"] = ["../../../Dep"]
    _write_config(repo)
    inner = GitRepo(repo.lib / "Vendored")
    (inner.top / "package.mo").write_text(
        f"within {LIB};\npackage Vendored\nend Vendored;\n"
    )
    inner.commit("the inner repository")
    repo.git.commit("a dependency and a nested repository")
    short = repo.git.git("rev-parse", "--short", "HEAD")
    (dep / "package.mo").unlink()
    rc, out, _ = _uses(repo, capsys, "--since", "HEAD")
    assert rc == 0 and repo.asked == []
    assert out == (
        f"Changed since HEAD ({short} a dependency and a nested repository): 1 file\n"
        f"  {LIB}:\n"
        "    Vendored/   another repository: not compared\n"
        f"  {os.path.realpath(dep)}:\n"
        "    package.mo  deleted (its top package cannot be named)\n"
        "1 changed file cannot be asked as a class: every test is counted "
        "(14 tests).\n"
    )


# --- --since: which refusal is said first ----------------------------------


def test_since_the_capability_is_said_before_the_revision(repo, capsys):
    rc, out, err = _uses(repo, capsys, "--since", "nope", "--backend", CANNOT)
    assert (rc, out) == (1, "")
    assert err.startswith("FakeCannot cannot say what a test uses.\n")


def test_since_an_unknown_class_is_said_before_the_revision(repo, capsys):
    rc, out, err = _uses(repo, capsys, f"{LIB}.Nope", "--since", "nope")
    assert (rc, out) == (2, "")
    assert err.startswith(f"Error: {LIB}.Nope is no class of the library")


def test_since_an_unknown_revision_is_said_before_no_test_to_ask(repo, capsys):
    rc, out, err = _uses(repo, capsys, "--since", "nope", "--package", f"{LIB}.Nope")
    assert (rc, out) == (2, "")
    assert err == f"Error: unknown revision 'nope' in {repo.lib.parent}\n"


def test_since_no_test_to_ask_is_said_before_nothing_changed(repo, capsys):
    out_file = repo.cwd / "uses.txt"
    rc, out, err = _uses(
        repo, capsys, "--since", "HEAD", "--package", f"{LIB}.Nope", "-o", str(out_file)
    )
    assert (rc, out) == (1, "")
    assert err.endswith("No tests found.\n")
    assert not out_file.exists()


def test_since_on_a_project_that_is_not_modelica_says_so(env, capsys, tmp_path):
    """Pinned with a backend that CAN answer for another source type: with
    the bundled ones the capability check says its sentence first."""

    @register("FakePyUses")
    class FakePyUses(Backend):
        capabilities = frozenset({Capability.USES})
        source_types = frozenset({"python"})

        def read_result(self, test, test_key, run_result):
            raise AssertionError("uses reads no result")

        def uses(self, tests):
            raise AssertionError("never asked")

    try:
        lib = tmp_path / "py"
        shutil.copytree(
            PROJECT_ROOT / "examples" / "python" / "PythonTestingLib",
            lib,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        config = lib / "Resources" / "ReferenceResults" / "testing.json"
        data = json.loads(config.read_text(encoding="utf-8"))
        data.pop("backends", None)
        data["backend"] = "FakePyUses"
        config.write_text(json.dumps(data), encoding="utf-8")
        rc = main(["uses", "--since", "HEAD", "--config", str(config)])
        out, err = capsys.readouterr()
        assert (rc, out) == (1, "")
        assert err == (
            "--since reads a Modelica library's files; this project's "
            "source_type is python.\n"
        )
    finally:
        del _REGISTRY["FakePyUses"]
