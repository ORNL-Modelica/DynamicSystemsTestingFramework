"""Regression tests for the 2026-07-06 review — Themes 2 & 6 (discovery/parsing).

Decisions encoded here (see CODE_REVIEW_2026-07-06.md):

* Finding 13: TestModel's stop_time/tolerance/method default to ``None`` so
  "explicitly set in the spec" is ``is not None`` — a spec value that happens
  to equal a framework default still overrides the experiment annotation.
  Defaults are applied once, at the END of discover_tests
  (``finalize_defaults``), so downstream consumers keep seeing concrete values.
* Finding 57: field_sources provenance is "test_spec" only for fields the
  spec actually set; fields no source set get provenance "default".
* Finding 16: JSON null in simulation.* means "not set"; non-numeric values
  log a warning naming model + field and are skipped — discovery never aborts.
* Finding 52: add_to_test_spec(overwrite=True) preserves hand-authored
  comparison/simulation/metrics sections, replacing only model + variables.
* Finding 53: non-dict entries in "tests" are warned about and skipped.
* Finding 54: update_test_comparison merges (per its docstring): keys present
  in the update win, other keys survive, variable_overrides merge per-variable.
* Finding 49: RFC 6902 `add` at an existing array index INSERTS; index == len
  appends; index > len raises PatchError; auto-creating a dict parent where
  the next token is an array index raises PatchError (no silent corruption).
* Finding 51: duplicate `model` entries in test_spec.json — FIRST entry wins
  everywhere (discovery, patch, update helpers), each with a warning.
* Findings 48/55/56: .mo scanners run on comment/string-stripped text with
  balanced-paren experiment parsing; numeric garbage warns and skips; multi-
  top-level-class files warn about what was skipped.
* Finding 50: component parameter values are extracted from the ORIGINAL text
  span so string-valued parameters (algorithm="Dassl") are extractable.
* Finding 17: bare library names in testing.json `dependencies` pass through
  Config untouched so classify_dependency routes them to loadModel().
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from dstf.config import (
    DEFAULT_METHOD,
    DEFAULT_STOP_TIME,
    DEFAULT_TOLERANCE,
    Config,
)
from dstf.discovery.mo_parser import (
    _extract_model_name,
    _parse_experiment,
    parse_mo_file,
)
from dstf.discovery.patch_apply import PatchError, apply_patch
from dstf.discovery.spec_parser import (
    add_to_test_spec,
    parse_test_spec,
    update_test_comparison,
    update_test_variables,
)
from dstf.discovery.test_registry import TestModel, discover_tests
from dstf.simulators.openmodelica.mos_generator import classify_dependency

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_lib(
    tmp: Path,
    *,
    annotation: str | None = 'StopTime=100, Tolerance=1e-6, __Dymola_Algorithm="Cvode"',
    spec: dict | None = None,
) -> Config:
    """Build a minimal Modelica library + testing.json (+ optional test_spec)."""
    (tmp / "package.mo").write_text("package MyLib\nend MyLib;\n", encoding="utf-8")
    ann = f"  annotation(experiment({annotation}));\n" if annotation else ""
    (tmp / "Foo.mo").write_text(
        "within MyLib;\n"
        "model Foo\n"
        "  Real x;\n"
        "  UnitTests unitTests(n=1, x={x});\n"
        "equation\n"
        "  x = time;\n" + ann + "end Foo;\n",
        encoding="utf-8",
    )
    cfg: dict = {
        "source_path": ".",
        "simulator": "Dymola",
        "simulators": {},
        "dependencies": [],
    }
    if spec is not None:
        (tmp / "test_spec.json").write_text(json.dumps(spec), encoding="utf-8")
        cfg["test_spec"] = "test_spec.json"
    (tmp / "testing.json").write_text(json.dumps(cfg), encoding="utf-8")
    return Config(config_file=str(tmp / "testing.json"))


def _mk_model(**kw) -> TestModel:
    defaults = dict(
        model_id="MyLib.Foo",
        source_file=Path(""),
        source_package="MyLib",
        short_name="Foo",
        n_vars=1,
    )
    defaults.update(kw)
    return TestModel(**defaults)


# ---------------------------------------------------------------------------
# Finding 13 — spec-over-annotation merge sentinel
# ---------------------------------------------------------------------------


class TestFinding13SpecOverridesAnnotation:
    def test_spec_value_equal_to_default_overrides_annotation(self, tmp_path):
        """A spec that explicitly sets stop_time=1.0 (== framework default)
        against experiment(StopTime=100) must run 1.0 s, not 100 s."""
        config = _write_lib(
            tmp_path,
            spec={
                "tests": [
                    {
                        "model": "MyLib.Foo",
                        "variables": ["x"],
                        "simulation": {
                            "stop_time": DEFAULT_STOP_TIME,
                            "tolerance": DEFAULT_TOLERANCE,
                            "method": DEFAULT_METHOD,
                        },
                    }
                ]
            },
        )
        tests = discover_tests(config)
        foo = {t.model_id: t for t in tests}["MyLib.Foo"]
        assert foo.source == "both"
        assert foo.stop_time == DEFAULT_STOP_TIME
        assert foo.tolerance == DEFAULT_TOLERANCE
        assert foo.method == DEFAULT_METHOD
        assert foo.field_sources["stop_time"] == "test_spec"
        assert foo.field_sources["tolerance"] == "test_spec"
        assert foo.field_sources["method"] == "test_spec"

    def test_annotation_wins_when_spec_silent(self, tmp_path):
        config = _write_lib(
            tmp_path,
            spec={"tests": [{"model": "MyLib.Foo", "variables": ["x"]}]},
        )
        tests = discover_tests(config)
        foo = {t.model_id: t for t in tests}["MyLib.Foo"]
        assert foo.stop_time == 100.0
        assert foo.tolerance == 1e-6
        assert foo.method == "Cvode"
        assert foo.field_sources["stop_time"] == "annotation"
        assert foo.field_sources["tolerance"] == "annotation"
        assert foo.field_sources["method"] == "annotation"

    def test_discovered_tests_never_expose_none_sim_fields(self, tmp_path):
        """finalize_defaults runs at the end of discover_tests — downstream
        consumers (runners format these into scripts) must see concrete values."""
        config = _write_lib(
            tmp_path,
            annotation=None,
            spec={"tests": [{"model": "MyLib.SpecOnly", "variables": ["*"]}]},
        )
        for t in discover_tests(config):
            assert t.stop_time is not None
            assert t.tolerance is not None
            assert t.method is not None

    def test_finalize_defaults_unit(self):
        t = _mk_model()
        assert t.stop_time is None and t.tolerance is None and t.method is None
        t.finalize_defaults()
        assert t.stop_time == DEFAULT_STOP_TIME
        assert t.tolerance == DEFAULT_TOLERANCE
        assert t.method == DEFAULT_METHOD


class TestFinding57Provenance:
    def test_spec_only_defaults_stamped_default_not_test_spec(self, tmp_path):
        """Spec-only test with a minimal entry: fields the spec did NOT set
        must show provenance "default", not "test_spec"."""
        config = _write_lib(
            tmp_path,
            annotation=None,
            spec={
                "tests": [
                    {
                        "model": "MyLib.SpecOnly",
                        "variables": ["*"],
                        "simulation": {"stop_time": 42.0},
                    }
                ]
            },
        )
        tests = discover_tests(config)
        t = {t.model_id: t for t in tests}["MyLib.SpecOnly"]
        assert t.stop_time == 42.0
        assert t.field_sources["stop_time"] == "test_spec"
        assert t.field_sources["tolerance"] == "default"
        assert t.field_sources["method"] == "default"
        assert t.field_sources["number_of_intervals"] == "default"
        assert t.field_sources["output_interval"] == "default"


# ---------------------------------------------------------------------------
# Finding 16 — null / non-numeric simulation values
# ---------------------------------------------------------------------------


class TestFinding16NullAndGarbageSpecValues:
    def _spec_with_sim(self, tmp_path, sim: dict) -> Path:
        spec_path = tmp_path / "test_spec.json"
        spec_path.write_text(
            json.dumps(
                {"tests": [{"model": "MyLib.A", "variables": ["x"], "simulation": sim}]}
            ),
            encoding="utf-8",
        )
        return spec_path

    def test_json_null_means_not_set(self, tmp_path):
        """The module docstring itself shows "output_interval": null — nulls
        must be treated as "not set", not crash with float(None)."""
        spec_path = self._spec_with_sim(
            tmp_path,
            {
                "stop_time": None,
                "tolerance": None,
                "output_interval": None,
                "number_of_intervals": None,
                "timeout": None,
                "method": None,
            },
        )
        tests = parse_test_spec(spec_path)
        assert len(tests) == 1
        t = tests[0]
        assert t.stop_time is None
        assert t.tolerance is None
        assert t.output_interval is None
        assert t.number_of_intervals is None
        assert t.timeout is None
        assert t.method is None

    def test_non_numeric_value_warns_and_skips_field(self, tmp_path, caplog):
        spec_path = self._spec_with_sim(
            tmp_path, {"stop_time": "not-a-number", "tolerance": 1e-5}
        )
        with caplog.at_level(logging.WARNING, logger="dstf.discovery.spec_parser"):
            tests = parse_test_spec(spec_path)
        assert len(tests) == 1
        assert tests[0].stop_time is None  # skipped, not aborted
        assert tests[0].tolerance == 1e-5  # sibling field still parsed
        assert any(
            "MyLib.A" in r.message and "stop_time" in r.message
            for r in caplog.records
        )


# ---------------------------------------------------------------------------
# Finding 53 — non-dict entries in "tests"
# ---------------------------------------------------------------------------


class TestFinding53NonDictEntries:
    def test_parse_skips_non_dict_entries_with_warning(self, tmp_path, caplog):
        spec_path = tmp_path / "test_spec.json"
        spec_path.write_text(
            json.dumps(
                {
                    "tests": [
                        "oops-a-string",
                        {"model": "MyLib.A", "variables": ["x"]},
                        42,
                    ]
                }
            ),
            encoding="utf-8",
        )
        with caplog.at_level(logging.WARNING, logger="dstf.discovery.spec_parser"):
            tests = parse_test_spec(spec_path)
        assert [t.model_id for t in tests] == ["MyLib.A"]
        assert sum("non-dict" in r.message for r in caplog.records) == 2

    def test_edit_helpers_tolerate_non_dict_entries(self, tmp_path):
        spec_path = tmp_path / "test_spec.json"
        spec_path.write_text(
            json.dumps({"tests": ["garbage", {"model": "MyLib.A", "variables": []}]}),
            encoding="utf-8",
        )
        # None of these may raise on the non-dict entry.
        assert add_to_test_spec(spec_path, "MyLib.B", ["y"]) is True
        update_test_variables(spec_path, "MyLib.A", ["z"])
        update_test_comparison(
            spec_path, {"model": "MyLib.A", "comparison": {"tolerance": 0.1}}
        )
        data = json.loads(spec_path.read_text(encoding="utf-8"))
        by_model = {
            e["model"]: e for e in data["tests"] if isinstance(e, dict)
        }
        assert set(by_model) == {"MyLib.A", "MyLib.B"}
        assert by_model["MyLib.A"]["comparison"]["tolerance"] == 0.1


# ---------------------------------------------------------------------------
# Finding 52 — overwrite=True preserves hand-authored sections
# ---------------------------------------------------------------------------


class TestFinding52OverwritePreservesSections:
    def test_overwrite_replaces_only_model_and_variables(self, tmp_path):
        spec_path = tmp_path / "test_spec.json"
        spec_path.write_text(
            json.dumps(
                {
                    "tests": [
                        {
                            "model": "MyLib.A",
                            "variables": ["old"],
                            "simulation": {"stop_time": 10},
                            "comparison": {"tolerance": 0.05},
                            "metrics": {"metric": "nrmse", "variable": "old"},
                            "description": "hand-authored",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        assert add_to_test_spec(spec_path, "MyLib.A", ["new"], overwrite=True) is True
        entry = json.loads(spec_path.read_text(encoding="utf-8"))["tests"][0]
        assert entry["variables"] == ["new"]
        assert entry["simulation"] == {"stop_time": 10}
        assert entry["comparison"] == {"tolerance": 0.05}
        assert entry["metrics"] == {"metric": "nrmse", "variable": "old"}
        assert entry["description"] == "hand-authored"


# ---------------------------------------------------------------------------
# Finding 54 — update_test_comparison merges instead of replacing
# ---------------------------------------------------------------------------


class TestFinding54ComparisonMerge:
    def _spec(self, tmp_path) -> Path:
        spec_path = tmp_path / "test_spec.json"
        spec_path.write_text(
            json.dumps(
                {
                    "tests": [
                        {
                            "model": "MyLib.A",
                            "variables": ["a", "b"],
                            "comparison": {
                                "tolerance": 1e-4,
                                "info": "keep me",
                                "variable_overrides": {"a": {"tolerance": 0.1}},
                            },
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return spec_path

    def test_updates_keys_present_keeps_others(self, tmp_path):
        spec_path = self._spec(tmp_path)
        update_test_comparison(
            spec_path, {"model": "MyLib.A", "comparison": {"tolerance": 0.05}}
        )
        comp = json.loads(spec_path.read_text(encoding="utf-8"))["tests"][0][
            "comparison"
        ]
        assert comp["tolerance"] == 0.05
        assert comp["info"] == "keep me"
        assert comp["variable_overrides"] == {"a": {"tolerance": 0.1}}

    def test_variable_overrides_merge_per_variable(self, tmp_path):
        spec_path = self._spec(tmp_path)
        update_test_comparison(
            spec_path,
            {
                "model": "MyLib.A",
                "comparison": {
                    "variable_overrides": {
                        "a": {"mode": "tube"},
                        "b": {"tolerance": 0.2},
                    }
                },
            },
        )
        comp = json.loads(spec_path.read_text(encoding="utf-8"))["tests"][0][
            "comparison"
        ]
        # "a" keeps its existing tolerance AND gains the new mode key.
        assert comp["variable_overrides"]["a"] == {"tolerance": 0.1, "mode": "tube"}
        assert comp["variable_overrides"]["b"] == {"tolerance": 0.2}
        assert comp["tolerance"] == 1e-4


# ---------------------------------------------------------------------------
# Finding 49 — RFC 6902 array `add` semantics
# ---------------------------------------------------------------------------


class TestFinding49ArrayAdd:
    def _spec(self, tmp_path) -> Path:
        spec_path = tmp_path / "spec.json"
        spec_path.write_text(
            json.dumps(
                {
                    "tests": [
                        {
                            "model": "M",
                            "metrics": {
                                "combinator": "and",
                                "children": [
                                    {"metric": "nrmse", "variable": "x"},
                                    {"metric": "nrmse", "variable": "y"},
                                ],
                            },
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return spec_path

    def test_add_at_existing_index_inserts(self, tmp_path):
        spec_path = self._spec(tmp_path)
        new_leaf = {"metric": "range", "variable": "z"}
        apply_patch(
            spec_path,
            "M",
            [{"op": "add", "path": "/metrics/children/1", "value": new_leaf}],
        )
        children = json.loads(spec_path.read_text(encoding="utf-8"))["tests"][0][
            "metrics"
        ]["children"]
        assert [c["variable"] for c in children] == ["x", "z", "y"]

    def test_add_dash_appends(self, tmp_path):
        spec_path = self._spec(tmp_path)
        apply_patch(
            spec_path,
            "M",
            [
                {
                    "op": "add",
                    "path": "/metrics/children/-",
                    "value": {"metric": "nrmse", "variable": "z"},
                }
            ],
        )
        children = json.loads(spec_path.read_text(encoding="utf-8"))["tests"][0][
            "metrics"
        ]["children"]
        assert [c["variable"] for c in children] == ["x", "y", "z"]

    def test_add_at_len_appends(self, tmp_path):
        spec_path = self._spec(tmp_path)
        apply_patch(
            spec_path,
            "M",
            [
                {
                    "op": "add",
                    "path": "/metrics/children/2",
                    "value": {"metric": "nrmse", "variable": "z"},
                }
            ],
        )
        children = json.loads(spec_path.read_text(encoding="utf-8"))["tests"][0][
            "metrics"
        ]["children"]
        assert [c["variable"] for c in children] == ["x", "y", "z"]

    def test_add_beyond_len_raises_patch_error(self, tmp_path):
        spec_path = self._spec(tmp_path)
        with pytest.raises(PatchError, match="out of range"):
            apply_patch(
                spec_path,
                "M",
                [
                    {
                        "op": "add",
                        "path": "/metrics/children/9",
                        "value": {"metric": "nrmse", "variable": "z"},
                    }
                ],
            )

    def test_add_refuses_dict_autocreate_for_array_index(self, tmp_path):
        """A per-leaf patch against a test with no metrics tree must fail
        loudly instead of silently creating {"children": {"0": ...}}."""
        spec_path = tmp_path / "spec.json"
        original = {"tests": [{"model": "M", "comparison": {"tolerance": 1e-4}}]}
        spec_path.write_text(json.dumps(original), encoding="utf-8")
        with pytest.raises(PatchError, match="array index"):
            apply_patch(
                spec_path,
                "M",
                [
                    {
                        "op": "add",
                        "path": "/metrics/children/0/tolerance",
                        "value": 0.01,
                    }
                ],
            )
        # Nothing was written — the spec is not corrupted.
        assert json.loads(spec_path.read_text(encoding="utf-8")) == original


# ---------------------------------------------------------------------------
# Finding 51 — duplicate model entries: first wins, with a warning
# ---------------------------------------------------------------------------


class TestFinding51DuplicateEntries:
    def test_discovery_uses_first_duplicate_and_warns(self, tmp_path, caplog):
        config = _write_lib(
            tmp_path,
            annotation=None,
            spec={
                "tests": [
                    {
                        "model": "MyLib.Dup",
                        "variables": ["x"],
                        "simulation": {"stop_time": 11.0},
                    },
                    {
                        "model": "MyLib.Dup",
                        "variables": ["y"],
                        "simulation": {"stop_time": 22.0},
                    },
                ]
            },
        )
        with caplog.at_level(logging.WARNING, logger="dstf.discovery.test_registry"):
            tests = discover_tests(config)
        dup = {t.model_id: t for t in tests}["MyLib.Dup"]
        assert dup.stop_time == 11.0  # FIRST entry wins (matches patch_apply)
        assert dup.variable_patterns == ["x"]
        assert any("MyLib.Dup" in r.message for r in caplog.records)

    def test_apply_patch_warns_on_duplicates_and_edits_first(self, tmp_path, caplog):
        spec_path = tmp_path / "spec.json"
        spec_path.write_text(
            json.dumps(
                {
                    "tests": [
                        {"model": "M", "comparison": {"tolerance": 1e-4}},
                        {"model": "M", "comparison": {"tolerance": 1e-5}},
                    ]
                }
            ),
            encoding="utf-8",
        )
        with caplog.at_level(logging.WARNING, logger="dstf.discovery.patch_apply"):
            apply_patch(
                spec_path,
                "M",
                [{"op": "replace", "path": "/comparison/tolerance", "value": 0.5}],
            )
        data = json.loads(spec_path.read_text(encoding="utf-8"))
        assert data["tests"][0]["comparison"]["tolerance"] == 0.5
        assert data["tests"][1]["comparison"]["tolerance"] == 1e-5
        assert any("duplicate" in r.message.lower() for r in caplog.records)

    def test_update_helpers_warn_on_duplicates_and_edit_first(self, tmp_path, caplog):
        spec_path = tmp_path / "spec.json"
        spec_path.write_text(
            json.dumps(
                {
                    "tests": [
                        {"model": "M", "variables": ["a"]},
                        {"model": "M", "variables": ["b"]},
                    ]
                }
            ),
            encoding="utf-8",
        )
        with caplog.at_level(logging.WARNING, logger="dstf.discovery.spec_parser"):
            update_test_comparison(
                spec_path, {"model": "M", "comparison": {"tolerance": 0.1}}
            )
        data = json.loads(spec_path.read_text(encoding="utf-8"))
        assert data["tests"][0]["comparison"] == {"tolerance": 0.1}
        assert "comparison" not in data["tests"][1]
        assert any("duplicate" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# Finding 48 — raw-text parsing: comments/strings must not shadow real code
# ---------------------------------------------------------------------------


class TestFinding48StrippedParsing:
    def test_commented_experiment_does_not_shadow_real_annotation(self):
        text = (
            "within MyLib;\n"
            "model Foo\n"
            "  // experiment(StopTime=5)\n"
            "  Real x;\n"
            "  annotation(experiment(StopTime=20, Tolerance=1e-7));\n"
            "end Foo;\n"
        )
        info = _parse_experiment(text)
        assert info is not None
        assert info.stop_time == 20.0
        assert info.tolerance == 1e-7

    def test_model_keyword_in_comment_does_not_shadow_name(self):
        text = "/* the model Bogus was removed */\nmodel RealOne\nend RealOne;\n"
        assert _extract_model_name(text) == "RealOne"

    def test_class_keyword_needs_word_boundary(self):
        text = "// supermodel Nope\nblock B\nend B;\n"
        assert _extract_model_name(text) == "B"

    def test_experiment_with_nested_parens_is_fully_parsed(self):
        """[^)]* used to truncate at the first ')' — StopTime after a nested
        group was silently dropped."""
        text = "annotation(experiment(__Dymola_Tuning(a=1), StopTime=10, Tolerance=1e-5));"
        info = _parse_experiment(text)
        assert info is not None
        assert info.stop_time == 10.0
        assert info.tolerance == 1e-5

    def test_experiment_method_string_survives_stripping(self):
        """String values live in the original text — literal-stripping must not
        blank __Dymola_Algorithm."""
        text = 'annotation(experiment(StopTime=2, __Dymola_Algorithm="Esdirk45a"));'
        info = _parse_experiment(text)
        assert info.method == "Esdirk45a"


# ---------------------------------------------------------------------------
# Finding 56 — numeric garbage in experiment values
# ---------------------------------------------------------------------------


class TestFinding56NumericGarbage:
    def test_arithmetic_expression_warns_and_skips(self, tmp_path, caplog):
        """StopTime=0.5*3600 used to silently truncate to 0.5."""
        mo = tmp_path / "Foo.mo"
        mo.write_text(
            "within MyLib;\n"
            "model Foo\n"
            "  UnitTests ut(n=1, x={x});\n"
            "  Real x;\n"
            "  annotation(experiment(StopTime=0.5*3600, Tolerance=1e-6));\n"
            "end Foo;\n",
            encoding="utf-8",
        )
        with caplog.at_level(logging.WARNING, logger="dstf.discovery.mo_parser"):
            result = parse_mo_file(mo)
        assert result is not None
        assert result.experiment.stop_time is None  # skipped, NOT 0.5
        assert result.experiment.tolerance == 1e-6  # sibling still parsed
        assert any(
            "StopTime" in r.message or "stop_time" in r.message
            for r in caplog.records
        )
        assert any("Foo.mo" in r.message for r in caplog.records)

    def test_garbage_capture_does_not_crash_discovery(self):
        info = _parse_experiment("annotation(experiment(StopTime=1.2.3));")
        assert info is not None
        assert info.stop_time is None


# ---------------------------------------------------------------------------
# Finding 55 — multi-class files warn about undiscovered siblings
# ---------------------------------------------------------------------------


class TestFinding55MultiClassFiles:
    def test_second_top_level_class_warns(self, tmp_path, caplog):
        mo = tmp_path / "Two.mo"
        mo.write_text(
            "within MyLib;\n"
            "model First\n"
            "  UnitTests ut(n=1, x={x});\n"
            "  Real x;\n"
            "end First;\n"
            "model Second\n"
            "  UnitTests ut(n=1, x={y});\n"
            "  Real y;\n"
            "end Second;\n",
            encoding="utf-8",
        )
        with caplog.at_level(logging.WARNING, logger="dstf.discovery.mo_parser"):
            result = parse_mo_file(mo)
        assert result is not None
        assert result.model_id == "MyLib.First"
        assert any("Second" in r.message for r in caplog.records)

    def test_single_class_file_does_not_warn(self, tmp_path, caplog):
        mo = tmp_path / "One.mo"
        mo.write_text(
            "within MyLib;\n"
            "model Only\n"
            "  UnitTests ut(n=1, x={x});\n"
            "  Real x;\n"
            "end Only;\n",
            encoding="utf-8",
        )
        with caplog.at_level(logging.WARNING, logger="dstf.discovery.mo_parser"):
            result = parse_mo_file(mo)
        assert result is not None
        assert not any("skipped" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# Finding 50 — string-valued component parameters
# ---------------------------------------------------------------------------


class TestFinding50StringParameters:
    def test_quoted_string_parameter_is_extractable(self, tmp_path):
        from dstf.discovery.json_recognizer import parse_recognizer_spec

        mo = tmp_path / "FooTest.mo"
        mo.write_text(
            "within MyLib.Examples;\n"
            "model FooTest\n"
            '  MyLib.Testing.SafetyTest myTest(numTracked=2, algorithm="Dassl");\n'
            "end FooTest;\n",
            encoding="utf-8",
        )
        rec = parse_recognizer_spec(
            {
                "name": "test",
                "match": {
                    "type": "component-instantiation",
                    "component_name": "MyLib.Testing.SafetyTest",
                },
                "fields": {
                    "method": {"from": "parameter", "name": "algorithm"},
                    "n_vars": {"from": "parameter", "name": "numTracked"},
                },
            }
        )
        result = rec.recognize(mo)
        assert result is not None
        assert result.method == "Dassl"
        assert result.n_vars == 2

    def test_experiment_annotation_field_ignores_commented_annotation(self, tmp_path):
        from dstf.discovery.json_recognizer import parse_recognizer_spec

        mo = tmp_path / "FooTest.mo"
        mo.write_text(
            "within MyLib.Examples;\n"
            "model FooTest\n"
            "  extends Modelica.Icons.Example;\n"
            "  // experiment(StopTime=5)\n"
            "  annotation(experiment(StopTime=42));\n"
            "end FooTest;\n",
            encoding="utf-8",
        )
        rec = parse_recognizer_spec(
            {
                "name": "test",
                "match": {"type": "extends", "class_pattern": "*Icons.Example"},
                "fields": {
                    "stop_time": {"from": "experiment-annotation", "name": "StopTime"},
                },
            }
        )
        result = rec.recognize(mo)
        assert result is not None
        assert result.stop_time == 42


# ---------------------------------------------------------------------------
# Finding 17 — bare library names in dependencies
# ---------------------------------------------------------------------------


class TestFinding17BareDependencyNames:
    def _config_with_deps(self, tmp_path, deps: list[str]) -> Config:
        (tmp_path / "package.mo").write_text(
            "package MyLib\nend MyLib;\n", encoding="utf-8"
        )
        (tmp_path / "testing.json").write_text(
            json.dumps(
                {
                    "source_path": ".",
                    "simulator": "OpenModelica",
                    "simulators": {},
                    "dependencies": deps,
                }
            ),
            encoding="utf-8",
        )
        return Config(config_file=str(tmp_path / "testing.json"))

    def test_bare_name_passes_through_untouched(self, tmp_path):
        config = self._config_with_deps(tmp_path, ["Modelica"])
        assert config.dependencies == ["Modelica"]
        # ...so the OM classifier routes it to loadModel, not loadFile.
        assert classify_dependency(config.dependencies[0]) == (
            "loadModel",
            "Modelica",
        )

    def test_relative_path_still_absolutized(self, tmp_path):
        (tmp_path / "libs" / "Dep").mkdir(parents=True)
        config = self._config_with_deps(tmp_path, ["libs/Dep"])
        assert config.dependencies == [str((tmp_path / "libs" / "Dep").resolve())]

    def test_bare_name_that_exists_on_disk_is_absolutized(self, tmp_path):
        """A separator-free entry naming a real directory next to testing.json
        is a path, not a library name."""
        (tmp_path / "DepLib").mkdir()
        config = self._config_with_deps(tmp_path, ["DepLib"])
        assert config.dependencies == [str((tmp_path / "DepLib").resolve())]

    def test_dot_and_mo_entries_are_paths(self, tmp_path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "package.mo").write_text("package Sub end Sub;")
        config = self._config_with_deps(tmp_path, ["./sub", "sub/package.mo"])
        assert config.dependencies == [
            str((tmp_path / "sub").resolve()),
            str((tmp_path / "sub" / "package.mo").resolve()),
        ]
