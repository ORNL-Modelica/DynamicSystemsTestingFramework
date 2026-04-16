"""Tests for the JSON-driven recognizer (PTA.2).

Schema parsing + validation, plus end-to-end matching against synthetic .mo
fixtures written into tmp_path.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from modelica_testing.discovery.json_recognizer import (
    JsonRecognizer,
    RecognizerSpecError,
    parse_recognizer_spec,
)


def _write_mo(tmp_path: Path, name: str, content: str) -> Path:
    """Write a .mo file with normalized indentation."""
    p = tmp_path / f"{name}.mo"
    p.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Schema parsing / validation
# ---------------------------------------------------------------------------

class TestSchemaParsing:
    def test_minimal_component_spec_parses(self):
        spec = {
            "name": "test:minimal",
            "match": {"type": "component-instantiation", "component_name": "Foo"},
        }
        rec = parse_recognizer_spec(spec)
        assert isinstance(rec, JsonRecognizer)
        assert rec.name == "test:minimal"
        assert "modelica" in rec.applies_to

    def test_minimal_extends_spec_parses(self):
        spec = {
            "name": "test:extends",
            "match": {"type": "extends", "class_pattern": "*Examples"},
        }
        rec = parse_recognizer_spec(spec)
        assert isinstance(rec, JsonRecognizer)

    def test_missing_name_raises(self):
        with pytest.raises(RecognizerSpecError, match="missing required field 'name'"):
            parse_recognizer_spec({"match": {"type": "extends", "class_pattern": "*"}})

    def test_missing_match_raises(self):
        with pytest.raises(RecognizerSpecError, match="missing required field 'match'"):
            parse_recognizer_spec({"name": "x"})

    def test_unknown_match_type_raises(self):
        with pytest.raises(RecognizerSpecError, match="unknown match type 'inheritance'"):
            parse_recognizer_spec({
                "name": "x",
                "match": {"type": "inheritance"},
            })

    def test_component_instantiation_requires_component_name(self):
        with pytest.raises(RecognizerSpecError, match="requires 'component_name'"):
            parse_recognizer_spec({
                "name": "x",
                "match": {"type": "component-instantiation"},
            })

    def test_extends_requires_class_pattern(self):
        with pytest.raises(RecognizerSpecError, match="requires 'class_pattern'"):
            parse_recognizer_spec({
                "name": "x",
                "match": {"type": "extends"},
            })

    def test_unknown_field_name_raises(self):
        with pytest.raises(RecognizerSpecError, match="unknown field 'made_up'"):
            parse_recognizer_spec({
                "name": "x",
                "match": {"type": "extends", "class_pattern": "*"},
                "fields": {"made_up": {"from": "constant", "value": 1}},
            })

    def test_parameter_source_with_extends_match_raises(self):
        # `parameter` is only valid with component-instantiation matches.
        with pytest.raises(RecognizerSpecError, match="isn't compatible with match type 'extends'"):
            parse_recognizer_spec({
                "name": "x",
                "match": {"type": "extends", "class_pattern": "*"},
                "fields": {"stop_time": {"from": "parameter", "name": "tEnd"}},
            })

    def test_unknown_field_source_raises(self):
        with pytest.raises(RecognizerSpecError, match="unknown source 'magic'"):
            parse_recognizer_spec({
                "name": "x",
                "match": {"type": "extends", "class_pattern": "*"},
                "fields": {"stop_time": {"from": "magic"}},
            })

    def test_parameter_source_requires_name(self):
        with pytest.raises(RecognizerSpecError, match=r"\(source=parameter\) requires 'name'"):
            parse_recognizer_spec({
                "name": "x",
                "match": {"type": "component-instantiation", "component_name": "Foo"},
                "fields": {"stop_time": {"from": "parameter"}},
            })

    def test_constant_source_requires_value(self):
        with pytest.raises(RecognizerSpecError, match=r"\(source=constant\) requires 'value'"):
            parse_recognizer_spec({
                "name": "x",
                "match": {"type": "extends", "class_pattern": "*"},
                "fields": {"stop_time": {"from": "constant"}},
            })


# ---------------------------------------------------------------------------
# Component-instantiation matching
# ---------------------------------------------------------------------------

class TestComponentMatching:
    def test_match_full_qualified_name(self, tmp_path):
        mo = _write_mo(tmp_path, "FooTest", """
            within MyLib.Examples;
            model FooTest
              MyLib.Testing.SafetyTest myTest(numTracked=3, tEnd=100);
            end FooTest;
        """)
        rec = parse_recognizer_spec({
            "name": "test",
            "match": {"type": "component-instantiation",
                      "component_name": "MyLib.Testing.SafetyTest"},
        })
        result = rec.recognize(mo)
        assert result is not None
        assert result.model_id == "MyLib.Examples.FooTest"

    def test_match_tail_suffix(self, tmp_path):
        # User wrote the short form (e.g., via `import MyLib.Testing`)
        mo = _write_mo(tmp_path, "FooTest", """
            within MyLib.Examples;
            model FooTest
              SafetyTest myTest(numTracked=3);
            end FooTest;
        """)
        rec = parse_recognizer_spec({
            "name": "test",
            "match": {"type": "component-instantiation",
                      "component_name": "MyLib.Testing.SafetyTest"},
        })
        assert rec.recognize(mo) is not None

    def test_no_match_when_component_absent(self, tmp_path):
        mo = _write_mo(tmp_path, "Plain", """
            within MyLib.Examples;
            model Plain
              Real x;
            equation
              x = time;
            end Plain;
        """)
        rec = parse_recognizer_spec({
            "name": "test",
            "match": {"type": "component-instantiation", "component_name": "SafetyTest"},
        })
        assert rec.recognize(mo) is None

    def test_skips_self_definition(self, tmp_path):
        # If the file IS the SafetyTest definition, even if its docstring
        # contains usage examples, we shouldn't treat it as a test.
        mo = _write_mo(tmp_path, "SafetyTest", """
            within MyLib.Testing;
            model SafetyTest "Test template"
              parameter Integer numTracked=0;
              annotation(Documentation(info="<html>
                Usage: SafetyTest myTest(numTracked=3);
              </html>"));
            end SafetyTest;
        """)
        rec = parse_recognizer_spec({
            "name": "test",
            "match": {"type": "component-instantiation", "component_name": "SafetyTest"},
        })
        assert rec.recognize(mo) is None

    def test_extracts_scalar_parameter(self, tmp_path):
        mo = _write_mo(tmp_path, "FooTest", """
            within MyLib.Examples;
            model FooTest
              MyLib.Testing.SafetyTest myTest(numTracked=3, tEnd=100);
            end FooTest;
        """)
        rec = parse_recognizer_spec({
            "name": "test",
            "match": {"type": "component-instantiation",
                      "component_name": "MyLib.Testing.SafetyTest"},
            "fields": {
                "n_vars": {"from": "parameter", "name": "numTracked"},
                "stop_time": {"from": "parameter", "name": "tEnd"},
            },
        })
        result = rec.recognize(mo)
        assert result.n_vars == 3
        assert result.stop_time == 100

    def test_extracts_array_parameter(self, tmp_path):
        mo = _write_mo(tmp_path, "FooTest", """
            within MyLib.Examples;
            model FooTest
              MyLib.Testing.SafetyTest myTest(
                numTracked=2,
                trackedVars={pipe.T, tank.level});
            end FooTest;
        """)
        rec = parse_recognizer_spec({
            "name": "test",
            "match": {"type": "component-instantiation",
                      "component_name": "MyLib.Testing.SafetyTest"},
            "fields": {
                "x_expressions": {"from": "parameter", "name": "trackedVars", "shape": "array"},
            },
        })
        result = rec.recognize(mo)
        assert result.x_expressions == ["pipe.T", "tank.level"]

    def test_constant_field(self, tmp_path):
        mo = _write_mo(tmp_path, "FooTest", """
            within MyLib.Examples;
            model FooTest
              MyLib.Testing.SafetyTest myTest(numTracked=1);
            end FooTest;
        """)
        rec = parse_recognizer_spec({
            "name": "test",
            "match": {"type": "component-instantiation",
                      "component_name": "MyLib.Testing.SafetyTest"},
            "fields": {
                "tolerance": {"from": "constant", "value": 1e-5},
            },
        })
        result = rec.recognize(mo)
        assert result.tolerance == 1e-5


# ---------------------------------------------------------------------------
# extends matching
# ---------------------------------------------------------------------------

class TestExtendsMatching:
    def test_glob_matches_extends(self, tmp_path):
        mo = _write_mo(tmp_path, "FooTest", """
            within MyLib.Examples;
            model FooTest
              extends Modelica.Icons.Example;
              Real x;
            equation
              x = time;
            end FooTest;
        """)
        rec = parse_recognizer_spec({
            "name": "test:icons-example",
            "match": {"type": "extends", "class_pattern": "*Icons.Example"},
        })
        result = rec.recognize(mo)
        assert result is not None
        assert result.model_id == "MyLib.Examples.FooTest"

    def test_glob_does_not_match_unrelated_extends(self, tmp_path):
        mo = _write_mo(tmp_path, "Other", """
            within MyLib;
            model Other
              extends Modelica.Blocks.Interfaces.SISO;
            end Other;
        """)
        rec = parse_recognizer_spec({
            "name": "test:icons-example",
            "match": {"type": "extends", "class_pattern": "*Icons.Example"},
        })
        assert rec.recognize(mo) is None

    def test_no_extends_clause(self, tmp_path):
        mo = _write_mo(tmp_path, "Plain", """
            within MyLib;
            model Plain
              Real x;
            end Plain;
        """)
        rec = parse_recognizer_spec({
            "name": "test:icons-example",
            "match": {"type": "extends", "class_pattern": "*Icons.Example"},
        })
        assert rec.recognize(mo) is None


# ---------------------------------------------------------------------------
# experiment-annotation field source
# ---------------------------------------------------------------------------

class TestClassNameGlob:
    """PTA-follow.3 — class-name-glob match type."""

    def test_glob_matches_qualified_name(self, tmp_path):
        mo = _write_mo(tmp_path, "FooTest", """
            within MyLib.Tests.Power;
            model FooTest
              Real x;
            end FooTest;
        """)
        rec = parse_recognizer_spec({
            "name": "test:all-power-tests",
            "match": {"type": "class-name-glob",
                      "class_pattern": "MyLib.Tests.Power.*"},
        })
        result = rec.recognize(mo)
        assert result is not None
        assert result.model_id == "MyLib.Tests.Power.FooTest"

    def test_glob_does_not_match_other(self, tmp_path):
        mo = _write_mo(tmp_path, "FooTest", """
            within MyLib.Examples;
            model FooTest
            end FooTest;
        """)
        rec = parse_recognizer_spec({
            "name": "test:power",
            "match": {"type": "class-name-glob",
                      "class_pattern": "MyLib.Tests.*"},
        })
        assert rec.recognize(mo) is None

    def test_class_name_glob_requires_pattern(self):
        with pytest.raises(RecognizerSpecError, match="class-name-glob requires 'class_pattern'"):
            parse_recognizer_spec({
                "name": "x",
                "match": {"type": "class-name-glob"},
            })


class TestAnnotationFieldSource:
    """PTA-follow.3 — annotation field source."""

    def test_extracts_from_custom_annotation(self, tmp_path):
        mo = _write_mo(tmp_path, "FooTest", """
            within MyLib.Examples;
            model FooTest
              extends Modelica.Icons.Example;
              annotation(__MyVendor_TestMeta(
                priority="high", timeout=120));
            end FooTest;
        """)
        rec = parse_recognizer_spec({
            "name": "test:vendor",
            "match": {"type": "extends", "class_pattern": "*Icons.Example"},
            "fields": {
                "stop_time": {"from": "annotation",
                              "annotation": "__MyVendor_TestMeta",
                              "name": "timeout"},
            },
        })
        result = rec.recognize(mo)
        assert result is not None
        assert result.stop_time == 120

    def test_annotation_source_requires_annotation_key(self):
        with pytest.raises(RecognizerSpecError, match=r"\(source=annotation\) requires 'annotation'"):
            parse_recognizer_spec({
                "name": "x",
                "match": {"type": "extends", "class_pattern": "*"},
                "fields": {
                    "stop_time": {"from": "annotation", "name": "x"},
                },
            })


class TestMatchComposition:
    """PTA-follow.2 — all-of / any-of recursive composition."""

    def test_all_of_requires_all_children(self, tmp_path):
        # File extends Icons.Example AND has SafetyTest component → match
        mo = _write_mo(tmp_path, "FooTest", """
            within MyLib.Examples;
            model FooTest
              extends Modelica.Icons.Example;
              MyLib.Testing.SafetyTest myTest(numTracked=2);
            end FooTest;
        """)
        rec = parse_recognizer_spec({
            "name": "test:both",
            "match": {
                "type": "all-of",
                "matchers": [
                    {"type": "extends", "class_pattern": "*Icons.Example"},
                    {"type": "component-instantiation",
                     "component_name": "MyLib.Testing.SafetyTest"},
                ],
            },
            "fields": {
                "n_vars": {"from": "parameter", "name": "numTracked"},
            },
        })
        result = rec.recognize(mo)
        assert result is not None
        assert result.n_vars == 2

    def test_all_of_fails_when_one_child_missing(self, tmp_path):
        # Only extends Icons.Example, no SafetyTest → no match
        mo = _write_mo(tmp_path, "FooTest", """
            within MyLib.Examples;
            model FooTest
              extends Modelica.Icons.Example;
            end FooTest;
        """)
        rec = parse_recognizer_spec({
            "name": "test:both",
            "match": {
                "type": "all-of",
                "matchers": [
                    {"type": "extends", "class_pattern": "*Icons.Example"},
                    {"type": "component-instantiation",
                     "component_name": "MyLib.Testing.SafetyTest"},
                ],
            },
        })
        assert rec.recognize(mo) is None

    def test_any_of_first_match_wins(self, tmp_path):
        mo = _write_mo(tmp_path, "FooTest", """
            within MyLib.Examples;
            model FooTest
              MyLib.Testing.SafetyTest myTest(numTracked=5);
            end FooTest;
        """)
        rec = parse_recognizer_spec({
            "name": "test:either",
            "match": {
                "type": "any-of",
                "matchers": [
                    {"type": "extends", "class_pattern": "*Icons.Example"},
                    {"type": "component-instantiation",
                     "component_name": "MyLib.Testing.SafetyTest"},
                ],
            },
            "fields": {
                "n_vars": {"from": "parameter", "name": "numTracked"},
            },
        })
        result = rec.recognize(mo)
        assert result is not None
        assert result.n_vars == 5

    def test_composition_validation_requires_matchers(self):
        with pytest.raises(RecognizerSpecError, match="all-of requires 'matchers'"):
            parse_recognizer_spec({
                "name": "x",
                "match": {"type": "all-of"},
            })

    def test_composition_recursive_validation(self):
        # Nested any-of with bad child match type
        with pytest.raises(RecognizerSpecError, match="unknown match type 'bogus'"):
            parse_recognizer_spec({
                "name": "x",
                "match": {
                    "type": "any-of",
                    "matchers": [
                        {"type": "extends", "class_pattern": "*"},
                        {"type": "bogus"},
                    ],
                },
            })

    def test_parameter_source_allowed_in_composition_with_component_match(self):
        # parameter source becomes valid in an all-of when one child is
        # component-instantiation (the union of leaf-allowed sources).
        # No exception → validation passes.
        parse_recognizer_spec({
            "name": "x",
            "match": {
                "type": "all-of",
                "matchers": [
                    {"type": "extends", "class_pattern": "*"},
                    {"type": "component-instantiation", "component_name": "Foo"},
                ],
            },
            "fields": {
                "n_vars": {"from": "parameter", "name": "n"},
            },
        })

    def test_parameter_source_rejected_in_extends_only_composition(self):
        with pytest.raises(RecognizerSpecError, match="isn't compatible"):
            parse_recognizer_spec({
                "name": "x",
                "match": {
                    "type": "any-of",
                    "matchers": [
                        {"type": "extends", "class_pattern": "*"},
                        {"type": "extends", "class_pattern": "*Other"},
                    ],
                },
                "fields": {
                    "n_vars": {"from": "parameter", "name": "n"},
                },
            })


class TestExperimentAnnotationField:
    def test_extracts_stop_time(self, tmp_path):
        mo = _write_mo(tmp_path, "FooTest", """
            within MyLib.Examples;
            model FooTest
              extends Modelica.Icons.Example;
              annotation(experiment(StopTime=42, Tolerance=1e-7));
            end FooTest;
        """)
        rec = parse_recognizer_spec({
            "name": "test",
            "match": {"type": "extends", "class_pattern": "*Icons.Example"},
            "fields": {
                "stop_time": {"from": "experiment-annotation", "name": "StopTime"},
                "tolerance": {"from": "experiment-annotation", "name": "Tolerance"},
            },
        })
        result = rec.recognize(mo)
        assert result.stop_time == 42
        assert result.tolerance == 1e-7

    def test_case_insensitive_first_letter(self, tmp_path):
        # Same recognizer spec works whether Modelica source uses StopTime or stopTime
        mo = _write_mo(tmp_path, "FooTest", """
            within MyLib.Examples;
            model FooTest
              extends Modelica.Icons.Example;
              annotation(experiment(stopTime=99));
            end FooTest;
        """)
        rec = parse_recognizer_spec({
            "name": "test",
            "match": {"type": "extends", "class_pattern": "*Icons.Example"},
            "fields": {
                "stop_time": {"from": "experiment-annotation", "name": "StopTime"},
            },
        })
        result = rec.recognize(mo)
        assert result.stop_time == 99

    def test_modelica_boolean_coerced(self, tmp_path):
        # `true` / `false` in Modelica params come through as Python bools
        mo = _write_mo(tmp_path, "FooTest", """
            within MyLib.Examples;
            model FooTest
              MyLib.Testing.SafetyTest myTest(simOnly=true);
            end FooTest;
        """)
        rec = parse_recognizer_spec({
            "name": "test",
            "match": {"type": "component-instantiation",
                      "component_name": "MyLib.Testing.SafetyTest"},
            "fields": {
                "simulate_only": {"from": "parameter", "name": "simOnly"},
            },
        })
        result = rec.recognize(mo)
        assert result.simulate_only is True

    def test_constant_bool_field(self, tmp_path):
        # PTA.4 — recognizer can stamp a constant boolean (e.g., "all my
        # tests should request FMU export")
        mo = _write_mo(tmp_path, "FooTest", """
            within MyLib.Examples;
            model FooTest
              extends Modelica.Icons.Example;
            end FooTest;
        """)
        rec = parse_recognizer_spec({
            "name": "test",
            "match": {"type": "extends", "class_pattern": "*Icons.Example"},
            "fields": {
                "requested_fmu_export": {"from": "constant", "value": True},
            },
        })
        result = rec.recognize(mo)
        assert result.requested_fmu_export is True

    def test_constant_list_field(self, tmp_path):
        mo = _write_mo(tmp_path, "FooTest", """
            within MyLib.Examples;
            model FooTest
              extends Modelica.Icons.Example;
            end FooTest;
        """)
        rec = parse_recognizer_spec({
            "name": "test",
            "match": {"type": "extends", "class_pattern": "*Icons.Example"},
            "fields": {
                "requested_baselines": {"from": "constant",
                                        "value": ["dymola-via-fmpy"]},
            },
        })
        result = rec.recognize(mo)
        assert result.requested_baselines == ["dymola-via-fmpy"]

    def test_paths_include_filters(self, tmp_path):
        """PTA-follow.1 — paths_include only feeds matching files."""
        # Two identical-shape files; only one in Examples/ should be matched.
        (tmp_path / "Examples").mkdir()
        (tmp_path / "Internal").mkdir()
        for sub, name in [("Examples", "Wanted"), ("Internal", "Skipped")]:
            (tmp_path / sub / f"{name}.mo").write_text(textwrap.dedent(f"""
                within MyLib.{sub};
                model {name}
                  extends Modelica.Icons.Example;
                end {name};
            """).lstrip(), encoding="utf-8")
        rec = parse_recognizer_spec({
            "name": "test",
            "match": {"type": "extends", "class_pattern": "*Icons.Example"},
            "paths_include": ["Examples/**"],
        })
        assert rec.applies_to_path(tmp_path / "Examples" / "Wanted.mo", tmp_path)
        assert not rec.applies_to_path(tmp_path / "Internal" / "Skipped.mo", tmp_path)

    def test_paths_exclude_filters(self, tmp_path):
        rec = parse_recognizer_spec({
            "name": "test",
            "match": {"type": "extends", "class_pattern": "*"},
            "paths_exclude": ["Internal/**"],
        })
        assert rec.applies_to_path(tmp_path / "Examples" / "Foo.mo", tmp_path)
        assert not rec.applies_to_path(tmp_path / "Internal" / "Bar.mo", tmp_path)

    def test_paths_default_no_filter(self, tmp_path):
        # Without paths_include/exclude, every path passes
        rec = parse_recognizer_spec({
            "name": "test",
            "match": {"type": "extends", "class_pattern": "*"},
        })
        assert rec.applies_to_path(tmp_path / "anywhere" / "x.mo", tmp_path)

    def test_quoted_string_value(self, tmp_path):
        mo = _write_mo(tmp_path, "FooTest", """
            within MyLib.Examples;
            model FooTest
              extends Modelica.Icons.Example;
              annotation(experiment(StopTime=10, __Dymola_Algorithm="Cvode"));
            end FooTest;
        """)
        rec = parse_recognizer_spec({
            "name": "test",
            "match": {"type": "extends", "class_pattern": "*Icons.Example"},
            "fields": {
                "method": {"from": "experiment-annotation", "name": "__Dymola_Algorithm"},
            },
        })
        result = rec.recognize(mo)
        assert result.method == "Cvode"
