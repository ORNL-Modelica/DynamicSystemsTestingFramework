"""`dstf uses` through `main`, over a copy of the bundled Modelica library
and a registered fake backend that answers what each test uses: every
sentence the command prints, its exit codes, its file, and the hint."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import PROJECT_ROOT
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
        + "Could not be asked (counted as using it):\n"
        f"  {HEAT} — Error: class\n  HeatRodTest not found.\n"
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
        f"Wrote {out_file}: 1 tests\n"
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

    rc, out, _ = _uses(env, capsys, UNIT_TESTS, "--package", HEAT)
    assert rc == 0
    assert env.asked[-1] == [HEAT]
    assert out == f"{UNIT_TESTS} is used by none of the 1 tests.\n"


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
    env.fails = RuntimeError("Worker -1: failed to load library package.mo")
    out_file = env.cwd / "uses.txt"
    rc, out, err = _uses(env, capsys, UNIT_TESTS, "-o", str(out_file))
    assert rc == 1 and out == ""
    assert err == (
        "Asking FakeUses what 14 tests use ...\n"
        "FakeUses could not be asked: Worker -1: failed to load library package.mo\n"
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
