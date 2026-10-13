"""A test declared only in ``test_spec.json`` takes its model's
``experiment(...)`` annotation, as a scanned test does: the entry's own
``simulation.*`` wins, and what it omits comes from the model (docs/usage.md,
"Two-layer contract"). Before this a spec-only entry was taken as written,
and a model annotated ``StopTime=200`` ran for the framework's 1 s."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dstf.cli import main
from dstf.config import Config
from dstf.discovery.mo_parser import find_class
from dstf.discovery.test_registry import SpecModelLocator, discover_tests

ROD = """\
within RodLib;
model Rod "Twenty-node rod, heated at one end"
  Real T[20](each start=300, each fixed=true);
equation
  der(T) = fill(0, 20);
  annotation (experiment(StopTime=200, Tolerance=1e-6));
end Rod;
"""

PACKAGE = """\
within ;
package RodLib "A small library"
  // experiment(StopTime=77) in a comment is no annotation
  model InPackageFile
    Real x;
  equation
    der(x) = 1;
    annotation (Documentation(info="experiment(StopTime=78)"),
      experiment(StopTime=11, __Dymola_Algorithm="Cvode"));
  end InPackageFile;

  package Sub
    model Deep
      Real x;
      annotation (experiment(StopTime=12, Interval=0.5));
    end Deep;
  end Sub;

  model Short = RodLib.Rod(n=5) "a short class has no body and no end";

  model Outer "its own annotation comes after its nested class's"
    model Inner
      Real x;
      annotation (experiment(StopTime=13));
    end Inner;
    Real y;
    annotation (experiment(StopTime=14, NumberOfIntervals=250));
  end Outer;

  model NoAnnotation
    Real x;
  end NoAnnotation;
  annotation (experiment(StopTime=99));
end RodLib;
"""

PARENT_FILE = """\
within RodLib;
package Parts "a parent class stored as its own file"
  model Shared
    Real x;
    annotation (experiment(StopTime=21));
  end Shared;
  package Other
    model Leaf
      Real x;
      annotation (experiment(StopTime=22));
    end Leaf;
  end Other;
  model Leaf
    Real x;
    annotation (experiment(StopTime=23));
  end Leaf;
end Parts;
"""


@pytest.fixture
def rodlib(tmp_path) -> Path:
    """`RodLib/` beside `RodLib-Tests/` (testing.json + an empty spec)."""
    lib = tmp_path / "RodLib"
    lib.mkdir()
    (lib / "package.mo").write_text(PACKAGE, encoding="utf-8")
    (lib / "Rod.mo").write_text(ROD, encoding="utf-8")
    (lib / "Parts.mo").write_text(PARENT_FILE, encoding="utf-8")
    tests = tmp_path / "RodLib-Tests"
    tests.mkdir()
    (tests / "testing.json").write_text(
        json.dumps(
            {
                "source_path": "../RodLib",
                "backend": "OpenModelica",
                "backends": {},
                "test_spec": "test_spec.json",
            }
        ),
        encoding="utf-8",
    )
    (tests / "test_spec.json").write_text('{"tests": []}\n', encoding="utf-8")
    return tests / "testing.json"


def _write_spec(config_file: Path, *entries: dict) -> None:
    (config_file.parent / "test_spec.json").write_text(
        json.dumps({"tests": list(entries)}), encoding="utf-8"
    )


def _discovered(config_file: Path) -> dict:
    return {t.model_id: t for t in discover_tests(Config(config_file=config_file))}


# ---------------------------------------------------------------------------
# find_class: where a class of the library is, and its own experiment
# ---------------------------------------------------------------------------


class TestFindClass:
    def _find(self, rodlib: Path, model_id: str):
        return find_class(rodlib.parent.parent / "RodLib", "RodLib", model_id)

    def test_a_class_in_its_own_file(self, rodlib):
        found = self._find(rodlib, "RodLib.Rod")
        assert found.mo_file.name == "Rod.mo"
        assert (found.experiment.stop_time, found.experiment.tolerance) == (200, 1e-6)

    def test_a_class_inside_package_mo(self, rodlib):
        found = self._find(rodlib, "RodLib.InPackageFile")
        assert found.mo_file.name == "package.mo"
        # not the comment's 77, not the documentation string's 78, not the
        # package's own 99
        assert found.experiment.stop_time == 11
        assert found.experiment.method == "Cvode"

    def test_a_class_two_levels_inside_package_mo(self, rodlib):
        found = self._find(rodlib, "RodLib.Sub.Deep")
        assert (found.experiment.stop_time, found.experiment.output_interval) == (
            12,
            0.5,
        )

    def test_a_class_inside_a_parent_classes_file(self, rodlib):
        found = self._find(rodlib, "RodLib.Parts.Shared")
        assert found.mo_file.name == "Parts.mo"
        assert found.experiment.stop_time == 21

    def test_a_name_is_looked_for_at_its_own_depth(self, rodlib):
        """`Parts.Leaf` is the class declared DIRECTLY in `Parts`, not the
        `Leaf` of `Parts.Other` that the file spells first."""
        assert self._find(rodlib, "RodLib.Parts.Leaf").experiment.stop_time == 23
        assert self._find(rodlib, "RodLib.Parts.Other.Leaf").experiment.stop_time == 22

    def test_the_annotation_is_the_classes_own(self, rodlib):
        """A nested class's annotation, spelled first, is not its parent's."""
        outer = self._find(rodlib, "RodLib.Outer").experiment
        assert (outer.stop_time, outer.number_of_intervals) == (14, 250)
        assert self._find(rodlib, "RodLib.Outer.Inner").experiment.stop_time == 13

    @pytest.mark.parametrize(
        "nested",
        [
            "model S = RodLib.Rod annotation(experiment(StopTime=12));",
            "model S = RodLib.Rod(T(start={1, 2}), k = f(a; b)) "
            "annotation(experiment(StopTime=12));",
            "model 'in ner'\n    annotation(experiment(StopTime=12));\n  end 'in ner';",
        ],
        ids=["short", "short-with-modifiers", "quoted-name"],
    )
    @pytest.mark.parametrize("own", ["annotation(experiment(StopTime=13));", ""])
    def test_a_nested_short_or_quoted_class_is_not_its_parents_annotation(
        self, rodlib, nested, own
    ):
        """`model S = L.Par annotation(experiment(...));` has no `end S;`,
        and `model 'in ner'` no plain name: neither was seen as nested, and
        its annotation was read as the enclosing class's."""
        lib = rodlib.parent.parent / "RodLib"
        (lib / "Holder.mo").write_text(
            f"within RodLib;\nmodel Holder\n  {nested}\n  Real x;\n  {own}\nend Holder;\n",
            encoding="utf-8",
        )
        found = self._find(rodlib, "RodLib.Holder")
        assert found.mo_file.name == "Holder.mo"
        if own:
            assert found.experiment.stop_time == 13
        else:
            assert found.experiment is None

    @pytest.mark.parametrize(
        "name",
        ["'a\"b'", "'it''s'".replace("''", "\\'"), "'a//b'", "'a/*b'", "'x\"'"],
        ids=["quote", "escaped-apostrophe", "line-comment", "block-comment", "odd"],
    )
    def test_a_nested_class_named_with_a_delimiter_does_not_hide_its_parent(
        self, rodlib, name
    ):
        """A quoted name may hold a `"`, a `//` or a `/*`: none of them
        starts a string or a comment there. `model 'a"b'` was read as a
        string running to the `"` of its own `end 'a"b';`, the class never
        closed, and its PARENT was not found."""
        lib = rodlib.parent.parent / "RodLib"
        (lib / "Holder.mo").write_text(
            f"within RodLib;\nmodel Holder\n"
            f"  model {name}\n    annotation(experiment(StopTime=12));\n  end {name};\n"
            "  model After\n    annotation(experiment(StopTime=15));\n  end After;\n"
            '  Real x "a string with an apostrophe: it\'s";\n'
            "  annotation(experiment(StopTime=13));\nend Holder;\n",
            encoding="utf-8",
        )
        found = self._find(rodlib, "RodLib.Holder")
        assert found is not None and found.experiment.stop_time == 13
        assert self._find(rodlib, "RodLib.Holder.After").experiment.stop_time == 15
        assert self._find(rodlib, f"RodLib.Holder.{name}").experiment.stop_time == 12

    @pytest.mark.parametrize(
        ("source", "stripped"),
        [
            # a quoted name is code, kept whole; the string after it is blanked
            ('model \'a"b\' "doc" x', "model 'a\"b'       x"),
            ("'a//b' // tail", "'a//b'        "),
            ("'a/*b' /* c */ d", "'a/*b'         d"),
            # an escaped apostrophe closes nothing
            ("'a\\'\"b' \"s\"", "'a\\'\"b'    "),
            # an apostrophe astray costs its line, not the file
            ('x \' "kept\ny "s" z', "x ' \"kept\ny     z"),
            # one inside a string or a comment opens nothing
            ("\"it's\" 'q' // it's\n\"t\"", "       'q'        \n   "),
        ],
    )
    def test_what_a_quoted_name_holds_delimits_nothing(self, source, stripped):
        from dstf.discovery.mo_parser import _strip_modelica_literals

        assert _strip_modelica_literals(source) == stripped

    def test_a_class_after_a_nested_short_class_is_still_found(self, rodlib):
        lib = rodlib.parent.parent / "RodLib"
        (lib / "Holder.mo").write_text(
            "within RodLib;\nmodel Holder\n"
            "  model S = RodLib.Rod annotation(experiment(StopTime=12));\n"
            "  model After\n    annotation(experiment(StopTime=15));\n  end After;\n"
            "end Holder;\n",
            encoding="utf-8",
        )
        assert self._find(rodlib, "RodLib.Holder.After").experiment.stop_time == 15
        # ... and the short class is found too, with the annotation it carries
        short = self._find(rodlib, "RodLib.Holder.S")
        assert short.mo_file.name == "Holder.mo" and short.experiment.stop_time == 12

    @pytest.mark.parametrize(
        ("statement", "stop_time"),
        [
            ("model ShortOne = RodLib.Rod annotation(experiment(StopTime=13));", 13),
            (
                "model ShortOne = RodLib.Rod(T(start={1, 2}), k = f(a; b))\n"
                '  "with a description"\n  annotation(experiment(StopTime=13));',
                13,
            ),
            # its own annotation: not the one of a class it redeclares
            (
                "model ShortOne = RodLib.Rod(redeclare model M = RodLib.Rod\n"
                "    annotation(experiment(StopTime=7))) "
                "annotation(experiment(StopTime=13));",
                13,
            ),
            ("model ShortOne = RodLib.Rod;", None),
        ],
        ids=["annotation", "modifiers", "redeclared-short", "none"],
    )
    def test_a_short_class_that_is_its_files_class_is_found_with_its_own_annotation(
        self, rodlib, statement, stop_time
    ):
        """`model ShortOne = Rod annotation(experiment(StopTime=13));` in
        `ShortOne.mo` IS a class of the library with an annotation of its
        own: it was not found, and a run told its author it was "not found
        under source_path, so its experiment annotation was not read"."""
        lib = rodlib.parent.parent / "RodLib"
        (lib / "ShortOne.mo").write_text(
            f"within RodLib;\n{statement}\n", encoding="utf-8"
        )
        found = self._find(rodlib, "RodLib.ShortOne")
        assert found is not None and found.mo_file.name == "ShortOne.mo"
        if stop_time is None:
            assert found.experiment is None
        else:
            assert found.experiment.stop_time == stop_time
        # it has no body: a component of what it is made from is no class
        assert self._find(rodlib, "RodLib.ShortOne.T") is None

    def test_short_classes_inside_a_modification_keep_their_own(self, rodlib):
        """Two redeclared in one list: each is found with its own
        annotation, and neither's is the enclosing class's."""
        lib = rodlib.parent.parent / "RodLib"
        (lib / "ShortOne.mo").write_text(
            "within RodLib;\nmodel ShortOne = RodLib.Rod(\n"
            "    redeclare model M = RodLib.Rod annotation(experiment(StopTime=7)),\n"
            "    redeclare model N = RodLib.Rod annotation(experiment(StopTime=8)))\n"
            "  annotation(experiment(StopTime=13));\n",
            encoding="utf-8",
        )
        stop = {
            name: self._find(rodlib, f"RodLib.ShortOne{name}").experiment.stop_time
            for name in ("", ".M", ".N")
        }
        assert stop == {"": 13, ".M": 7, ".N": 8}

    @pytest.mark.parametrize(
        ("path", "source", "stop_times"),
        [
            (
                "Examples/package.mo",
                "within RodLib;\npackage Examples\n"
                "  model UsesRod = Base(redeclare model Rod = RodLib.Rod2)"
                " annotation(experiment(StopTime=3));\n"
                "  model Rod\n    Real x;\n    annotation(experiment(StopTime=50));\n"
                "  end Rod;\nend Examples;\n",
                {"Examples.Rod": 50, "Examples.UsesRod": 3},
            ),
            (
                "A.mo",
                "within RodLib;\nmodel A\n  Pipe p(redeclare model HT = Ideal);\n"
                "  model HT\n    Real x;\n    annotation(experiment(StopTime=7));\n"
                "  end HT;\n  annotation(experiment(StopTime=9));\nend A;\n",
                {"A.HT": 7, "A": 9},
            ),
            (
                "B.mo",
                "within RodLib;\nmodel B\n"
                "  extends Base(redeclare package Medium = Water,"
                " redeclare model T = Foo);\n"
                "  model T\n    Real x;\n    annotation(experiment(StopTime=11));\n"
                "  end T;\n  annotation(experiment(StopTime=12));\nend B;\n",
                {"B.T": 11, "B": 12},
            ),
            (
                "C.mo",
                "within RodLib;\nmodel C\n"
                "  model T\n    Real x;\n    annotation(experiment(StopTime=11));\n"
                "  end T;\n  Pipe p(redeclare model T = Foo);\n"
                "  annotation(experiment(StopTime=12));\nend C;\n",
                {"C.T": 11, "C": 12},
            ),
            (
                "ShortOne.mo",
                "within RodLib;\nmodel ShortOne = L.Rod(a=1)"
                " annotation(experiment(StopTime=13));\n",
                {"ShortOne": 13},
            ),
            (
                "D.mo",
                "within RodLib;\nmodel D\n"
                "  Pipe p(redeclare model M = Foo) annotation(Placement(x=1));\n"
                "  annotation(experiment(StopTime=21));\nend D;\n",
                {"D": 21, "D.M": None},
            ),
            (
                "Q.mo",
                "within RodLib;\nmodel Q\n"
                "  model 'a\"b'\n    Real x; annotation(experiment(StopTime=1.5));\n"
                "  end 'a\"b';\n"
                "  model N Real x; annotation(experiment(StopTime=2.5)); end N;\n"
                "  annotation(experiment(StopTime=31));\nend Q;\n",
                {"Q": 31, "Q.N": 2.5, "Q.'a\"b'": 1.5},
            ),
        ],
        ids=[
            "sibling-short-class",
            "component",
            "extends",
            "declared-first",
            "short-class",
            "no-such-class",
            "quoted-name",
        ],
    )
    def test_a_class_redeclared_in_a_modification_is_no_class_of_the_body(
        self, rodlib, path, source, stop_times
    ):
        """`Pipe p(redeclare model HT = Ideal);` declares no class `HT` of
        the model it stands in — nor does one in an `extends` clause, or in
        the modification of a short class beside it. Each was taken for the
        class of that name, so the model `HT` declared AFTER it was found
        with no annotation: its test ran for the framework's 1 s."""
        lib = rodlib.parent.parent / "RodLib"
        (lib / path).parent.mkdir(exist_ok=True)
        (lib / path).write_text(source, encoding="utf-8")
        found = {name: self._find(rodlib, f"RodLib.{name}") for name in stop_times}
        assert {
            name: located and located.experiment.stop_time
            for name, located in found.items()
        } == stop_times

    def test_a_redeclare_that_is_an_element_of_the_body_is_a_class(self, rodlib):
        """Outside every bracket it is a declaration like any short class."""
        lib = rodlib.parent.parent / "RodLib"
        (lib / "Holder.mo").write_text(
            "within RodLib;\nmodel Holder\n  extends Base(p(q={1, 2}), r=f(3));\n"
            "  redeclare model M = RodLib.Rod annotation(experiment(StopTime=12));\n"
            "end Holder;\n",
            encoding="utf-8",
        )
        assert self._find(rodlib, "RodLib.Holder.M").experiment.stop_time == 12

    def test_an_annotation_inside_a_modification_is_not_the_enclosing_classes(
        self, rodlib
    ):
        """The class redeclared there is none of the body's, and its text is
        still not the body's own."""
        lib = rodlib.parent.parent / "RodLib"
        (lib / "Holder.mo").write_text(
            "within RodLib;\nmodel Holder\n"
            "  Pipe p(redeclare model M = Foo annotation(experiment(StopTime=5)));\n"
            "  annotation(experiment(StopTime=21));\nend Holder;\n",
            encoding="utf-8",
        )
        assert self._find(rodlib, "RodLib.Holder").experiment.stop_time == 21
        assert self._find(rodlib, "RodLib.Holder.M") is None

    @pytest.mark.parametrize("name", ["'a(b'", "'a)b'", "'a\\'[b'"])
    def test_a_bracket_in_a_quoted_name_opens_and_closes_nothing(self, rodlib, name):
        lib = rodlib.parent.parent / "RodLib"
        (lib / "Holder.mo").write_text(
            f"within RodLib;\nmodel Holder\n  Real {name} = 1;\n"
            "  model S = RodLib.Rod annotation(experiment(StopTime=12));\n"
            "end Holder;\n",
            encoding="utf-8",
        )
        assert self._find(rodlib, "RodLib.Holder.S").experiment.stop_time == 12

    def test_a_short_class_in_package_mo_is_found(self, rodlib):
        found = self._find(rodlib, "RodLib.Short")
        assert found.mo_file.name == "package.mo" and found.experiment is None

    def test_a_class_with_no_annotation_is_found_with_none(self, rodlib):
        found = self._find(rodlib, "RodLib.NoAnnotation")
        assert found.mo_file.name == "package.mo" and found.experiment is None

    @pytest.mark.parametrize(
        "model_id",
        [
            "RodLib.Missing",
            "RodLib.Sub.Missing",
            "RodLib.Rod.T",  # a component, not a class
            "Modelica.Blocks.Examples.PID_Controller",  # another library's
            "OtherLib.Rod",  # another library's, of a name this one has too
            "RodLib",
        ],
    )
    def test_what_is_not_a_class_of_the_library_is_not_found(self, rodlib, model_id):
        assert self._find(rodlib, model_id) is None

    def test_it_agrees_with_the_scan_on_every_scanned_model_of_the_example(self):
        """One reader, two ways in: a model the scan recognizes is found by
        name in the same file, with the same experiment."""
        from dstf.discovery.test_registry import scan_source_tests

        example = Path(__file__).parent.parent / "examples/modelica/ModelicaTestingLib"
        config = Config(
            config_file=example / "Resources" / "ReferenceResults" / "testing.json"
        )
        scanned = scan_source_tests(config)
        assert len(scanned) >= 11
        for model_id, test in scanned.items():
            found = find_class(config.library_dir, config.library_name, model_id)
            assert found is not None, model_id
            assert found.mo_file == test.source_file, model_id
            experiment = found.experiment
            assert (
                experiment.stop_time,
                experiment.tolerance,
                experiment.method,
                experiment.number_of_intervals,
                experiment.output_interval,
            ) == (
                test.stop_time,
                test.tolerance,
                test.method,
                test.number_of_intervals,
                test.output_interval,
            ), model_id

    def test_a_file_that_says_it_belongs_elsewhere_is_not_trusted(self, rodlib):
        lib = rodlib.parent.parent / "RodLib"
        (lib / "Moved.mo").write_text(
            ROD.replace("within RodLib;", "within OtherLib.Somewhere;").replace(
                "Rod", "Moved"
            ),
            encoding="utf-8",
        )
        assert self._find(rodlib, "RodLib.Moved") is None


# ---------------------------------------------------------------------------
# discovery: a spec-only entry is filled from its model
# ---------------------------------------------------------------------------


class TestASpecOnlyEntryTakesItsAnnotation:
    @pytest.mark.parametrize(
        ("annotation", "said"),
        [
            (" annotation(experiment(StopTime=13))", []),
            (
                "",
                [
                    "RodLib.ShortOne: neither test_spec.json nor the model's "
                    "experiment annotation gives a stop time; it runs to the "
                    "default, 1 s."
                ],
            ),
        ],
        ids=["its-own-annotation", "none"],
    )
    def test_a_run_says_what_is_true_of_a_short_class(self, rodlib, annotation, said):
        """With its own `experiment(StopTime=13)` it runs to 13 s and nothing
        is said; with none, what is said is that nothing gives a stop time —
        never that the class was not found."""
        from dstf.cli.run_cmds import _default_stop_time_notes

        lib = rodlib.parent.parent / "RodLib"
        (lib / "ShortOne.mo").write_text(
            f"within RodLib;\nmodel ShortOne = RodLib.Rod{annotation};\n",
            encoding="utf-8",
        )
        _write_spec(rodlib, {"model": "RodLib.ShortOne", "variables": ["T[1]"]})
        config = Config(config_file=rodlib)
        (test,) = discover_tests(config)
        assert test.source_file == lib / "ShortOne.mo"
        assert _default_stop_time_notes([test], config) == said
        if annotation:
            assert test.stop_time == 13
            assert test.field_sources["stop_time"] == "annotation"

    def test_what_the_entry_omits_comes_from_the_model(self, rodlib):
        _write_spec(rodlib, {"model": "RodLib.Rod", "variables": ["T[1]"]})
        rod = _discovered(rodlib)["RodLib.Rod"]
        assert (rod.stop_time, rod.tolerance) == (200, 1e-6)
        assert rod.source == "spec"
        assert rod.source_file == rodlib.parent.parent / "RodLib" / "Rod.mo"
        assert rod.field_sources == {
            "stop_time": "annotation",
            "tolerance": "annotation",
            "method": "default",
            "number_of_intervals": "default",
            "output_interval": "default",
        }
        # it has no UnitTests component: the annotation adds no variable
        assert rod.n_vars == 0 and rod.variable_patterns == ["T[1]"]

    def test_the_entrys_own_simulation_block_still_wins(self, rodlib):
        _write_spec(
            rodlib,
            {
                "model": "RodLib.Rod",
                "variables": ["T[1]"],
                "simulation": {"stop_time": 1.0, "method": "Euler"},
            },
        )
        rod = _discovered(rodlib)["RodLib.Rod"]
        # 1.0 is also the framework default: set is set
        assert (rod.stop_time, rod.tolerance, rod.method) == (1.0, 1e-6, "Euler")
        assert rod.field_sources["stop_time"] == "test_spec"
        assert rod.field_sources["tolerance"] == "annotation"
        assert rod.field_sources["method"] == "test_spec"

    def test_every_annotation_field_is_filled(self, rodlib):
        _write_spec(
            rodlib,
            {"model": "RodLib.InPackageFile"},
            {"model": "RodLib.Sub.Deep"},
            {"model": "RodLib.Outer"},
        )
        tests = _discovered(rodlib)
        assert tests["RodLib.InPackageFile"].method == "Cvode"
        assert tests["RodLib.Sub.Deep"].output_interval == 0.5
        assert tests["RodLib.Outer"].number_of_intervals == 250

    def test_a_model_that_is_not_found_keeps_the_defaults(self, rodlib):
        _write_spec(rodlib, {"model": "RodLib.Missing", "variables": ["x"]})
        missing = _discovered(rodlib)["RodLib.Missing"]
        assert (missing.stop_time, missing.method) == (1.0, "Dassl")
        assert missing.field_sources["stop_time"] == "default"
        assert missing.source_file == Path("")

    def test_the_bundled_examples_spec_only_test(self, tmp_path):
        """`NoUnitTest` is annotated `StopTime=5, Tolerance=1e-4`; with its
        `simulation` block removed the entry still runs to 5."""
        import shutil

        src = Path(__file__).parent.parent / "examples/modelica/ModelicaTestingLib"
        lib = tmp_path / "ModelicaTestingLib"
        shutil.copytree(src, lib)
        refs = lib / "Resources" / "ReferenceResults"
        spec = json.loads((refs / "test_spec.json").read_text(encoding="utf-8"))
        model = "ModelicaTestingLib.Examples.NoUnitTest"
        (entry,) = [e for e in spec["tests"] if e["model"] == model]
        del entry["simulation"]
        (refs / "test_spec.json").write_text(json.dumps(spec), encoding="utf-8")
        test = _discovered(refs / "testing.json")[model]
        assert (test.stop_time, test.tolerance) == (5, 1e-4)
        assert test.source_file == lib / "Examples" / "NoUnitTest.mo"

    def test_dstf_add_then_discover(self, rodlib, capsys):
        """The newcomer's path: `dstf spec add` writes a bare entry, and
        `discover` shows the model's stop time, not 1."""
        argv = ["--config", str(rodlib)]
        assert main([*argv, "spec", "add", "RodLib.Rod", "--variables", "T[1]"]) == 0
        capsys.readouterr()
        assert main([*argv, "discover"]) == 0
        (row,) = [
            ln for ln in capsys.readouterr().out.splitlines() if "RodLib.Rod" in ln
        ]
        assert row.split()[1:] == ["1", "pattern", "200", "Dassl", "spec"]

    def test_dstf_add_with_no_variables_adds_a_simulate_only_test(self, rodlib, capsys):
        """`dstf spec add <model>` says `(simulate only)` and writes an entry
        that declares nothing to score: the test it makes IS simulate-only
        (it failed every run as one that scored nothing)."""
        argv = ["--config", str(rodlib)]
        assert main([*argv, "spec", "add", "RodLib.Rod"]) == 0
        assert "Variables: (simulate only)" in capsys.readouterr().out
        spec = json.loads(rodlib.with_name("test_spec.json").read_text("utf-8"))
        assert spec["tests"] == [{"model": "RodLib.Rod", "variables": []}]
        test = _discovered(rodlib)["RodLib.Rod"]
        assert test.simulate_only is False and test.is_simulate_only is True
        # A pattern makes it a scored test again.
        assert (
            main([*argv, "spec", "add", "RodLib.Parts.Rod", "--variables", "T[1]"]) == 0
        )
        assert _discovered(rodlib)["RodLib.Parts.Rod"].is_simulate_only is False

    def test_a_non_modelica_source_is_unchanged(self, tmp_path, monkeypatch):
        """No annotation is looked for — the library is not even probed —
        and an entry with no stop time keeps the default."""
        from conftest import python_example_copy
        from dstf.discovery import mo_parser

        def not_asked(*a, **k):
            raise AssertionError("a non-Modelica library was searched for a class")

        monkeypatch.setattr(mo_parser, "find_class", not_asked)
        config, _ = python_example_copy(tmp_path)
        spec = json.loads(config.spec_path.read_text(encoding="utf-8"))
        del spec["tests"][0]["simulation"]
        config.spec_path.write_text(json.dumps(spec), encoding="utf-8")
        test = _discovered(config.config_file)[spec["tests"][0]["model"]]
        assert test.stop_time == 1.0
        assert test.field_sources["stop_time"] == "default"
        assert SpecModelLocator(config)(test.model_id) is None


class TestTheLocatorReadsAModelOnce:
    """The authoring Session re-merges the scan against the spec on every
    edit; the locator it holds must not read the library again each time."""

    def test_a_second_ask_reads_nothing(self, rodlib, monkeypatch):
        config = Config(config_file=rodlib)
        locate = SpecModelLocator(config)
        first = locate("RodLib.Rod")
        assert first.experiment.stop_time == 200

        def no_read(*a, **k):
            raise AssertionError("the library was read again")

        monkeypatch.setattr(Path, "read_text", no_read)
        monkeypatch.setattr(Path, "is_file", no_read)
        assert locate("RodLib.Rod") is first
        # a miss is remembered too
        monkeypatch.undo()
        assert locate("RodLib.Missing") is None
        monkeypatch.setattr(Path, "is_file", no_read)
        assert locate("RodLib.Missing") is None


# ---------------------------------------------------------------------------
# the authoring tool: the same merge, and the library read once
# ---------------------------------------------------------------------------


def test_the_tool_fills_a_spec_only_test_and_reads_its_model_once(
    rodlib, tmp_path, monkeypatch
):
    """`dstf author` merges the scan against the spec on every edit. A
    spec-only test shows its model's stop time there too, and an edit does
    not send the Session back to the library's files."""
    pytest.importorskip("nicegui")
    from dstf.authoring.session import Session
    from dstf.authoring.spec_document import PatchOp
    from dstf.discovery import mo_parser

    cfg = json.loads(rodlib.read_text(encoding="utf-8"))
    cfg.update(
        {
            "backend": "Python",
            "backends": {"Python": {"executables": ["python3", "python"]}},
        }
    )
    rodlib.write_text(json.dumps(cfg), encoding="utf-8")
    _write_spec(rodlib, {"model": "RodLib.Rod", "variables": ["T[1]"]})

    asked = []
    real = mo_parser.find_class

    def spy(library_dir, library_name, model_id):
        asked.append(model_id)
        return real(library_dir, library_name, model_id)

    monkeypatch.setattr(mo_parser, "find_class", spy)
    session = Session()
    session.open(rodlib, work_dir=tmp_path / "work")
    rod = session.project.test("RodLib.Rod")
    assert (rod.stop_time, rod.field_sources["stop_time"]) == (200, "annotation")
    session.stage([PatchOp("RodLib.Rod", "add", "/comparison/tolerance", 0.5)])
    session.stage([PatchOp("RodLib.Rod", "add", "/comparison/tolerance", 0.25)])
    rod = session.project.test("RodLib.Rod")
    assert rod.comparison_tolerance == 0.25  # re-merged, twice
    assert (rod.stop_time, rod.tolerance) == (200, 1e-6)  # still the model's
    assert asked == ["RodLib.Rod"]
