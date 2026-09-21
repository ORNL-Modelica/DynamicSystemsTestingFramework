"""Tests for discovery: mo_parser, spec_parser, test_registry."""

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
    _extract_within,
    _parse_experiment,
    _parse_unit_tests,
    parse_mo_file,
)
from dstf.discovery.patch_apply import apply_patch
from dstf.discovery.spec_parser import (
    add_to_test_spec,
    parse_test_spec,
    update_test_variables,
)
from dstf.discovery.test_registry import TestModel, discover_tests

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PROJECT_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# .mo parser
# ---------------------------------------------------------------------------


class TestMoParser:
    def test_parse_with_unit_tests(self):
        """Parse a .mo file that has a UnitTests component."""
        result = parse_mo_file(
            PROJECT_ROOT
            / "examples"
            / "modelica"
            / "ModelicaTestingLib"
            / "Examples"
            / "SimpleTest.mo"
        )
        assert result is not None
        assert result.model_id == "ModelicaTestingLib.Examples.SimpleTest"
        assert result.unit_test is not None
        assert result.unit_test.n == 2
        assert result.unit_test.x_expressions == ["x", "y"]

    def test_parse_without_unit_tests(self):
        """Parse a .mo file without UnitTests => returns None (nothing to test)."""
        result = parse_mo_file(
            PROJECT_ROOT
            / "examples"
            / "modelica"
            / "ModelicaTestingLib"
            / "Examples"
            / "NoUnitTest.mo"
        )
        assert result is None

    def test_parse_experiment(self):
        """Extract experiment annotation."""
        result = parse_mo_file(
            PROJECT_ROOT
            / "examples"
            / "modelica"
            / "ModelicaTestingLib"
            / "Examples"
            / "SimpleTest.mo"
        )
        assert result.experiment is not None
        assert result.experiment.stop_time == 10.0
        assert result.experiment.tolerance == 1e-6
        assert result.experiment.method == "Dassl"

    def test_parse_output_interval(self):
        """Extract Interval from experiment annotation."""
        result = parse_mo_file(
            PROJECT_ROOT
            / "examples"
            / "modelica"
            / "ModelicaTestingLib"
            / "Examples"
            / "IntervalTest.mo"
        )
        assert result is not None
        assert result.experiment.output_interval == 0.5
        assert result.experiment.number_of_intervals is None

    def test_extract_within(self):
        assert _extract_within("within MyLib.Examples;\nmodel Foo") == "MyLib.Examples"
        assert _extract_within("model Foo") == ""

    def test_extract_model_name(self):
        assert _extract_model_name('model SimpleTest "A test"') == "SimpleTest"
        assert _extract_model_name('package Foo "Bar"') == "Foo"

    def test_nonexistent_file(self):
        result = parse_mo_file(Path("/nonexistent/file.mo"))
        assert result is None

    def test_bare_variable(self):
        """x=y where y is an array — bare variable without braces."""
        text = "Utilities.ErrorAnalysis.UnitTests unitTests(n=2, x=y)"
        info = _parse_unit_tests(text)
        assert info.x_expressions == ["y"]
        assert info.x_raw == "y"

    def test_bare_qualified_variable(self):
        """x=some.qualified.name — dotted path without braces."""
        text = "UnitTests ut(n=2, x=heatTransfer.alphas)"
        info = _parse_unit_tests(text)
        assert info.x_expressions == ["heatTransfer.alphas"]
        assert info.x_raw == "heatTransfer.alphas"

    def test_bare_deep_qualified_variable(self):
        """x=a.b.c.d — deeply nested dotted path."""
        text = "UnitTests ut(x=ductOut.port_b.C_outflow)"
        info = _parse_unit_tests(text)
        assert info.x_expressions == ["ductOut.port_b.C_outflow"]

    def test_bare_variable_with_x_reference(self):
        """Bare x=y doesn't interfere with x_reference parsing."""
        text = "UnitTests ut(n=2, x=y, x_reference={1.0, 2.0})"
        info = _parse_unit_tests(text)
        assert info.x_expressions == ["y"]
        assert info.x_reference == [1.0, 2.0]

    def test_no_x_param(self):
        """UnitTests with only x_reference but no x= gives empty expressions."""
        text = "UnitTests ut(n=2, x_reference={1.0, 2.0})"
        info = _parse_unit_tests(text)
        assert info.x_expressions == []
        assert info.x_reference == [1.0, 2.0]

    def test_array_functions(self):
        """x=fill(...), x=zeros(...), etc. are captured like cat(...)."""
        for func in ("fill", "zeros", "ones", "linspace"):
            text = f"UnitTests ut(n=3, x={func}(0, 3))"
            info = _parse_unit_tests(text)
            assert len(info.x_expressions) == 1
            assert info.x_expressions[0].startswith(f"{func}(")

    def test_a_wrapped_index_loses_its_source_whitespace(self):
        """An expression the .mo source wrapped over two lines is one line
        here: the layout is the file's, not the expression's."""
        info = _parse_unit_tests("UnitTests ut(n=2, x={a.b[\n        1, 1], c})")
        assert info.x_expressions == ["a.b[1, 1]", "c"]

    def test_the_instance_name_is_kept(self):
        """The component's instance name — its variables are <instance>.x[i]."""
        assert _parse_unit_tests("UnitTests ut(n=2, x={a, b})").instance == "ut"
        assert (
            _parse_unit_tests(
                "Utilities.ErrorAnalysis.UnitTests unitTests(n=1, x={a})"
            ).instance
            == "unitTests"
        )

    def test_a_wrapped_cat_without_braces_is_normalized(self):
        """The non-braced x=cat(...) branch normalizes too."""
        info = _parse_unit_tests("UnitTests unitTests(n=3, x=cat(1, y,\n   y1))")
        assert info.x_expressions == ["cat(1, y, y1)"]

    def test_a_wrapped_cat_inside_braces_is_normalized(self):
        info = _parse_unit_tests("UnitTests unitTests(n=3, x={cat(1, y,\n   y1)[ 2 ]})")
        assert info.x_expressions == ["cat(1, y, y1)[2]"]

    def test_a_wrapped_bare_name_is_normalized(self):
        """The bare x=name branch normalizes too."""
        info = _parse_unit_tests("UnitTests unitTests(n=1, x=a.b[1,\n   2])")
        assert info.x_expressions == ["a.b[1, 2]"]

    def test_only_the_expression_text_is_normalized(self):
        """A comment inside x={...} is blanked by the literal stripper before
        the whitespace run collapses; a string elsewhere in the file is not
        expression text and normalization never reaches it."""
        info = _parse_unit_tests(
            'UnitTests ut(n=2, x={a /* why */ , b}) "two   spaces";'
        )
        assert info.x_expressions == ["a", "b"]


# ---------------------------------------------------------------------------
# spec_parser
# ---------------------------------------------------------------------------


class TestSpecParser:
    def test_parse_test_spec(self, sample_test_spec):
        """Parse test_spec.json and get TestModels."""
        tests = parse_test_spec(sample_test_spec)
        assert len(tests) == 2

        by_id = {t.model_id: t for t in tests}
        assert "ModelicaTestingLib.Examples.NoUnitTest" in by_id
        assert "ModelicaTestingLib.Examples.SpecOnly" in by_id

        no_ut = by_id["ModelicaTestingLib.Examples.NoUnitTest"]
        assert no_ut.variable_patterns == ["x"]
        assert no_ut.stop_time == 5
        assert no_ut.source == "spec"
        # A spec-only entry names no component; the conventional instance
        # name stands (nothing reads it for a spec-tracked test).
        assert no_ut.unit_tests_instance == "unitTests"

    def test_add_to_spec(self, tmp_path):
        """add_to_test_spec creates and appends entries."""
        spec_path = tmp_path / "test_spec.json"

        # Add first entry
        result = add_to_test_spec(spec_path, "MyLib.Test1", ["var1", "var2"])
        assert result is True

        # Verify file contents
        data = json.loads(spec_path.read_text())
        assert len(data["tests"]) == 1
        assert data["tests"][0]["model"] == "MyLib.Test1"
        assert data["tests"][0]["variables"] == ["var1", "var2"]

        # Add second entry
        add_to_test_spec(spec_path, "MyLib.Test2", ["*"])
        data = json.loads(spec_path.read_text())
        assert len(data["tests"]) == 2

    def test_add_duplicate_returns_false(self, tmp_path):
        """Adding an existing model without overwrite returns False."""
        spec_path = tmp_path / "test_spec.json"
        add_to_test_spec(spec_path, "MyLib.Test1", ["var1"])
        result = add_to_test_spec(spec_path, "MyLib.Test1", ["var2"])
        assert result is False

    def test_add_duplicate_with_overwrite(self, tmp_path):
        """Adding with overwrite=True replaces the entry."""
        spec_path = tmp_path / "test_spec.json"
        add_to_test_spec(spec_path, "MyLib.Test1", ["var1"])
        add_to_test_spec(spec_path, "MyLib.Test1", ["new_var"], overwrite=True)
        data = json.loads(spec_path.read_text())
        assert data["tests"][0]["variables"] == ["new_var"]

    def test_update_variables(self, tmp_path):
        """update_test_variables adds patterns to an existing entry."""
        spec_path = tmp_path / "test_spec.json"
        add_to_test_spec(spec_path, "MyLib.Test1", ["var1"])
        update_test_variables(spec_path, "MyLib.Test1", ["var2", "var3"])
        data = json.loads(spec_path.read_text())
        assert set(data["tests"][0]["variables"]) == {"var1", "var2", "var3"}

    def test_structured_simulation_and_comparison(self, tmp_path):
        """Parse the structured format with simulation and comparison sections."""
        spec_path = tmp_path / "test_spec.json"
        spec_path.write_text(
            json.dumps(
                {
                    "tests": [
                        {
                            "model": "MyLib.Examples.HeatExchanger",
                            "variables": ["pipe.T[1]", "pump.m_flow"],
                            "simulation": {
                                "stop_time": 1000,
                                "tolerance": 1e-4,
                                "method": "Esdirk45a",
                                "number_of_intervals": 500,
                                "timeout": 120,
                            },
                            "comparison": {
                                "tolerance": 0.05,
                                "variable_overrides": {
                                    "pipe.T[1]": {"tolerance": 0.1},
                                },
                            },
                        }
                    ],
                }
            )
        )

        tests = parse_test_spec(spec_path)
        assert len(tests) == 1
        t = tests[0]
        assert t.model_id == "MyLib.Examples.HeatExchanger"
        assert t.stop_time == 1000
        assert t.tolerance == 1e-4
        assert t.method == "Esdirk45a"
        assert t.number_of_intervals == 500
        assert t.timeout == 120
        assert t.comparison_tolerance == 0.05
        assert t.variable_overrides == {"pipe.T[1]": {"tolerance": 0.1}}

    def test_minimal_spec_entry(self, tmp_path):
        """Minimal entry with just model and variables uses all defaults."""
        spec_path = tmp_path / "test_spec.json"
        spec_path.write_text(
            json.dumps(
                {
                    "tests": [{"model": "MyLib.Simple", "variables": ["x"]}],
                }
            )
        )

        tests = parse_test_spec(spec_path)
        assert len(tests) == 1
        t = tests[0]
        assert t.comparison_tolerance is None
        assert t.variable_overrides == {}

    def test_nonexistent_spec(self):
        """Nonexistent spec file returns empty list."""
        tests = parse_test_spec(Path("/nonexistent/test_spec.json"))
        assert tests == []

    def test_fmu_key_is_not_a_source(self, tmp_path, caplog):
        """`source` is the only source-file key; `fmu` warns and is ignored."""
        import logging

        fmu = tmp_path / "Model.fmu"
        fmu.write_bytes(b"")
        spec_path = tmp_path / "test_spec.json"
        spec_path.write_text(
            json.dumps(
                {"tests": [{"model": "Model", "fmu": "Model.fmu", "variables": ["x"]}]}
            )
        )

        with caplog.at_level(logging.WARNING, logger="dstf.discovery.spec_parser"):
            tests = parse_test_spec(spec_path)

        assert tests[0].source_file == Path("")
        assert any("'source'" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# test_registry (discover_tests)
# ---------------------------------------------------------------------------


class TestDiscoverTests:
    def test_discover_from_mo_files(self, sample_models_dir):
        """Discover tests from .mo files with UnitTests."""
        config = Config(source_path=sample_models_dir)
        tests = discover_tests(config)
        model_ids = [t.model_id for t in tests]
        assert "ModelicaTestingLib.Examples.SimpleTest" in model_ids

    def test_discover_with_spec(self, sample_models_dir, sample_test_spec):
        """Discover with spec file adds spec-only tests."""
        config = Config(
            source_path=sample_models_dir,
            test_spec_file=sample_test_spec,
        )
        tests = discover_tests(config)
        model_ids = [t.model_id for t in tests]
        assert "ModelicaTestingLib.Examples.SimpleTest" in model_ids
        assert "ModelicaTestingLib.Examples.NoUnitTest" in model_ids
        assert "ModelicaTestingLib.Examples.SpecOnly" in model_ids

    def test_source_tracking(self, sample_models_dir, sample_test_spec):
        """Source field correctly tracks origin of each test."""
        config = Config(
            source_path=sample_models_dir,
            test_spec_file=sample_test_spec,
        )
        tests = discover_tests(config)
        by_id = {t.model_id: t for t in tests}

        assert by_id["ModelicaTestingLib.Examples.SimpleTest"].source == "unit_tests"
        assert by_id["ModelicaTestingLib.Examples.SpecOnly"].source == "spec"

    def test_spec_timeout_and_metrics_survive_merge_with_unittests(
        self,
        sample_models_dir,
        tmp_path,
    ):
        """Regression: a test discovered from both UnitTests and test_spec
        must keep the spec's ``simulation.timeout`` and ``metrics`` block.

        The merge in discover_tests used to copy stop_time/tolerance/method/
        number_of_intervals/output_interval/comparison_tolerance/
        variable_overrides from the spec, but silently dropped ``timeout`` and
        ``metric_tree_spec`` — so users who set a per-test timeout on a model
        with an in-model UnitTests block got the default timeout instead.
        """
        spec_path = tmp_path / "test_spec.json"
        spec_path.write_text(
            json.dumps(
                {
                    "tests": [
                        {
                            "model": "ModelicaTestingLib.Examples.SimpleTest",
                            "variables": ["x"],
                            "simulation": {"timeout": 300},
                            "metrics": {
                                "metric": "nrmse",
                                "variable": "x",
                                "tolerance": 0.01,
                            },
                        }
                    ],
                }
            )
        )

        config = Config(
            source_path=sample_models_dir,
            test_spec_file=spec_path,
        )
        tests = discover_tests(config)
        by_id = {t.model_id: t for t in tests}
        simple = by_id["ModelicaTestingLib.Examples.SimpleTest"]

        assert simple.source == "both", "fixture model should merge both sources"
        assert simple.timeout == 300, (
            "timeout from test_spec.simulation must survive the merge"
        )
        assert simple.metric_tree_spec is not None, (
            "metric_tree_spec from test_spec.metrics must survive the merge"
        )

    def test_scan_reports_progress_per_file(self, tmp_path):
        from dstf.discovery.test_registry import scan_source_tests

        config = _write_lib(tmp_path)
        ticks = []
        scanned = scan_source_tests(
            config, progress=lambda done, total: ticks.append((done, total))
        )
        assert scanned
        assert (
            ticks[-1][0] == ticks[-1][1] == len(list(config.library_dir.rglob("*.mo")))
        )
        assert [d for d, _ in ticks] == list(range(1, len(ticks) + 1))


def test_field_sources_records_annotation_default():
    """A test discovered only via Modelica annotation has field_sources='annotation'."""
    from dstf.discovery.recognizer import RecognizerResult
    from dstf.discovery.test_registry import (
        _build_test_model_from_recognizer_results,
    )

    r = RecognizerResult(
        model_id="MyLib.Foo",
        stop_time=10.0,
        tolerance=1e-5,
        method="Dassl",
    )
    model = _build_test_model_from_recognizer_results("MyLib.Foo", [r])
    assert model.field_sources["stop_time"] == "annotation"
    assert model.field_sources["tolerance"] == "annotation"
    assert model.field_sources["method"] == "annotation"


def test_field_sources_records_spec_override():
    """When spec overrides annotation, field_sources flips to 'test_spec'."""
    import json
    import tempfile
    from pathlib import Path

    from dstf.config import Config
    from dstf.discovery.test_registry import discover_tests

    spec = {
        "tests": [
            {
                "model": "MyLib.Foo",
                "variables": ["x"],
                "simulation": {"stop_time": 999.0},
            }
        ],
    }
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        # Stub package.mo so Config.__post_init__ short-circuits the
        # find_package_dir parent-walk (which on WSL trips on unreadable
        # Windows-mount artifacts at the filesystem root).
        (td / "package.mo").write_text("package MyLib end MyLib;")
        spec_path = td / "test_spec.json"
        spec_path.write_text(json.dumps(spec))
        config = Config(source_path=td, test_spec_file=spec_path)
        tests = discover_tests(config)
    assert len(tests) == 1
    assert tests[0].field_sources["stop_time"] == "test_spec"


# =========================================================================
# Folded in from tests/test_review_fixes_discovery.py — regression tests for
# the 2026-07-06 review, Themes 2 & 6 (CODE_REVIEW_2026-07-06.md).
#
# Decisions encoded here:
#
# * Finding 13: TestModel's stop_time/tolerance/method default to ``None`` so
#   "explicitly set in the spec" is ``is not None`` — a spec value that happens
#   to equal a framework default still overrides the experiment annotation.
#   Defaults are applied once, at the END of discover_tests
#   (``finalize_defaults``), so downstream consumers keep seeing concrete values.
# * Finding 57: field_sources provenance is "test_spec" only for fields the
#   spec actually set; fields no source set get provenance "default".
# * Finding 16: JSON null in simulation.* means "not set"; non-numeric values
#   log a warning naming model + field and are skipped — discovery never aborts.
# * Finding 52: add_to_test_spec(overwrite=True) preserves hand-authored
#   comparison/simulation/metrics sections, replacing only model + variables.
# * Finding 53: non-dict entries in "tests" are warned about and skipped.
# * Finding 51: duplicate `model` entries in test_spec.json — FIRST entry wins
#   everywhere (discovery, patch, update helpers), each with a warning.
# * Findings 48/55/56: .mo scanners run on comment/string-stripped text with
#   balanced-paren experiment parsing; numeric garbage warns and skips; multi-
#   top-level-class files warn about what was skipped.
# =========================================================================


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
        "backend": "Dymola",
        "backends": {},
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
        consumers (backends format these into scripts) must see concrete values."""
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
            "MyLib.A" in r.message and "stop_time" in r.message for r in caplog.records
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
        # Neither may raise on the non-dict entry.
        assert add_to_test_spec(spec_path, "MyLib.B", ["y"]) is True
        update_test_variables(spec_path, "MyLib.A", ["z"])
        data = json.loads(spec_path.read_text(encoding="utf-8"))
        by_model = {e["model"]: e for e in data["tests"] if isinstance(e, dict)}
        assert set(by_model) == {"MyLib.A", "MyLib.B"}
        assert by_model["MyLib.A"]["variables"] == ["z"]


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
            update_test_variables(spec_path, "M", ["c"])
        data = json.loads(spec_path.read_text(encoding="utf-8"))
        assert data["tests"][0]["variables"] == ["a", "c"]
        assert data["tests"][1]["variables"] == ["b"]
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
        text = (
            "annotation(experiment(__Dymola_Tuning(a=1), StopTime=10, Tolerance=1e-5));"
        )
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

    def test_double_slash_inside_string_does_not_desync_stripper(self):
        """Regression (2026-07-07): a ``//`` inside a string literal (e.g. a
        ``modelica://`` URI) must not be treated as a line comment. The old
        3-pass stripper ran the line-comment regex before the string regex, so
        the ``//`` in the URL blanked the string's closing quote and desynced
        every following string — swallowing the real ``experiment()`` and
        defaulting StopTime to 1.0. Mirrors TRANSFORM's InverseParameterization,
        where a ``__Dymola_Commands`` URL precedes the experiment annotation."""
        text = (
            "within MyLib;\n"
            "model Foo\n"
            "  Real x;\n"
            "  annotation(\n"
            "    __Dymola_Commands(file(ensureSimulated=true)="
            '"modelica://MyLib/Resources/Scripts/plotResults.mos" "plot"),\n'
            "    experiment(StopTime=10, Interval=0.001),\n"
            '    Documentation(info="<html><img src=\\"modelica://x.png\\"></html>"));\n'
            "end Foo;\n"
        )
        info = _parse_experiment(text)
        assert info is not None
        assert info.stop_time == 10.0  # was None → default 1.0 before the fix
        assert info.output_interval == 0.001

    def test_quote_inside_line_comment_does_not_open_string(self):
        """The dual hazard: a ``"`` inside a real ``//`` comment must not start
        a string that swallows following code."""
        text = (
            "model Foo\n"
            '  // a stray quote " here must not open a string\n'
            "  annotation(experiment(StopTime=7));\n"
            "end Foo;\n"
        )
        info = _parse_experiment(text)
        assert info is not None
        assert info.stop_time == 7.0

    def test_block_comment_close_inside_string_is_ignored(self):
        """A ``*/`` inside a string must not end a block comment that was never
        opened; and a ``/*`` inside a string must not start one."""
        text = (
            "model Foo\n"
            '  parameter String s = "not /* a comment */ really";\n'
            "  annotation(experiment(StopTime=3));\n"
            "end Foo;\n"
        )
        info = _parse_experiment(text)
        assert info is not None
        assert info.stop_time == 3.0


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
            "StopTime" in r.message or "stop_time" in r.message for r in caplog.records
        )
        assert any("Foo.mo" in r.message for r in caplog.records)

    def test_garbage_capture_does_not_crash_discovery(self):
        info = _parse_experiment("annotation(experiment(StopTime=1.2.3));")
        assert info is not None
        assert info.stop_time is None

    def test_error_expected_expression_warns_and_keeps_default(self, tmp_path, caplog):
        """errorExpected=1e-3*2 used to silently truncate to 1e-3 — the
        truncating character class survived here after the rest of the file
        moved to _parse_number."""
        mo = tmp_path / "Bar.mo"
        mo.write_text(
            "within MyLib;\n"
            "model Bar\n"
            "  UnitTests ut(n=1, x={x}, errorExpected=1e-3*2);\n"
            "  Real x;\n"
            "  annotation(experiment(StopTime=1));\n"
            "end Bar;\n",
            encoding="utf-8",
        )
        with caplog.at_level(logging.WARNING, logger="dstf.discovery.mo_parser"):
            result = parse_mo_file(mo)
        assert result is not None
        assert result.unit_test.error_expected == 1e-6  # default, NOT 1e-3
        assert any("errorExpected" in r.message for r in caplog.records)
        assert any("Bar.mo" in r.message for r in caplog.records)

    def test_plain_error_expected_still_parsed(self, tmp_path):
        mo = tmp_path / "Baz.mo"
        mo.write_text(
            "within MyLib;\n"
            "model Baz\n"
            "  UnitTests ut(n=1, x={x}, errorExpected=1e-3);\n"
            "  Real x;\n"
            "end Baz;\n",
            encoding="utf-8",
        )
        result = parse_mo_file(mo)
        assert result is not None
        assert result.unit_test.error_expected == 1e-3

    def test_json_recognizer_drops_non_numeric_scalar(self, tmp_path, caplog):
        """A JSON recognizer's best-effort scalar coercion returned the raw
        STRING "0.5*3600" and set it on the float-typed stop_time field."""
        from dstf.discovery.json_recognizer import parse_recognizer_spec

        mo = tmp_path / "FooTest.mo"
        mo.write_text(
            "within MyLib.Examples;\n"
            "model FooTest\n"
            '  MyLib.Testing.SafetyTest myTest(tEnd=0.5*3600, solver="dassl");\n'
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
                    "stop_time": {"from": "parameter", "name": "tEnd"},
                    "method": {"from": "parameter", "name": "solver"},
                },
            }
        )
        with caplog.at_level(logging.WARNING, logger="dstf.discovery.json_recognizer"):
            result = rec.recognize(mo)
        assert result is not None
        assert result.stop_time is None  # left unset, not the string
        assert result.method == "dassl"  # string fields still accept strings
        assert any("stop_time" in r.getMessage() for r in caplog.records)
        assert any("MyLib.Examples.FooTest" in r.getMessage() for r in caplog.records)


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


class TestSpecFromDocument:
    """`spec_models_from_document` is `parse_test_spec` without the disk."""

    def _spec(self):
        return {
            "tests": [
                {
                    "model": "Lib.A",
                    "variables": ["x", "y"],
                    "simulation": {"stop_time": 3.0},
                    "comparison": {"variable_overrides": {"x": {"tolerance": 0.1}}},
                },
                {"model": "Lib.B", "variables": ["z"], "source": "Examples/B.py"},
            ]
        }

    def test_document_and_file_produce_the_same_models(self, tmp_path):
        from dstf.discovery.spec_parser import (
            parse_test_spec,
            spec_models_from_document,
        )

        (tmp_path / "Examples").mkdir()
        (tmp_path / "Examples" / "B.py").write_text("")
        spec_path = tmp_path / "test_spec.json"
        spec_path.write_text(json.dumps(self._spec()), encoding="utf-8")

        from_file = parse_test_spec(spec_path)
        from_doc = spec_models_from_document(
            self._spec(), base_dir=tmp_path, label="in memory"
        )
        assert [t.model_id for t in from_file] == [t.model_id for t in from_doc]
        for a, b in zip(from_file, from_doc):
            assert (
                a.variable_patterns,
                a.stop_time,
                a.variable_overrides,
                a.source_file,
            ) == (b.variable_patterns, b.stop_time, b.variable_overrides, b.source_file)
        assert from_doc[1].source_file == (tmp_path / "Examples" / "B.py").resolve()

    def test_non_object_root_yields_nothing(self):
        from dstf.discovery.spec_parser import spec_models_from_document

        assert spec_models_from_document([], base_dir=Path("."), label="x") == []
        assert (
            spec_models_from_document({"tests": "no"}, base_dir=Path("."), label="x")
            == []
        )

    def test_bad_metric_tree_names_the_label(self):
        from dstf.config import ConfigError
        from dstf.discovery.spec_parser import spec_models_from_document

        doc = {"tests": [{"model": "Lib.A", "variables": ["x"], "metrics": 42}]}
        with pytest.raises(ConfigError, match="in memory"):
            spec_models_from_document(doc, base_dir=Path("."), label="in memory")


# ---------------------------------------------------------------------------
# Discovery from a pre-parsed document; the scan and the merge are separable
# ---------------------------------------------------------------------------


class TestDiscoveryFromDocument:
    def test_merge_does_not_mutate_the_scan(self, tmp_path):
        from dstf.discovery.test_registry import merge_tests, scan_source_tests

        config = _write_lib(tmp_path)
        scanned = scan_source_tests(config)
        model_id = next(iter(scanned))
        before = scanned[model_id].stop_time
        spec_test = _mk_model(
            model_id=model_id,
            stop_time=99.0,
            variable_patterns=["q"],
            variable_overrides={"q": {"tolerance": 0.5}},
        )
        # The merge writes INTO these two containers on the merged copy; a
        # shallow copy of the scan would leak the writes back here.
        sources_before = dict(scanned[model_id].field_sources)
        overrides_before = dict(scanned[model_id].variable_overrides)

        merged = merge_tests(scanned, {model_id: spec_test})
        assert next(t for t in merged if t.model_id == model_id).stop_time == 99.0
        assert scanned[model_id].stop_time == before
        assert scanned[model_id].variable_patterns != ["q"]
        assert scanned[model_id].field_sources == sources_before
        assert scanned[model_id].variable_overrides == overrides_before
        # A second merge against a different spec sees the untouched scan.
        again = merge_tests(scanned, {})
        assert next(t for t in again if t.model_id == model_id).variable_patterns != [
            "q"
        ]

    def test_merge_does_not_mutate_a_spec_only_model(self):
        """The `else` branch stores the spec model; finalize_defaults then
        fills its None sentinels — on the copy, never on the caller's."""
        from dstf.discovery.test_registry import merge_tests

        spec_only = _mk_model(model_id="Lib.SpecOnly", stop_time=None, tolerance=None)
        spec_only.field_sources = {}
        (merged,) = merge_tests({}, {"Lib.SpecOnly": spec_only})
        assert merged.stop_time is not None and merged.tolerance is not None
        assert spec_only.stop_time is None and spec_only.tolerance is None
        assert spec_only.field_sources == {}

    def test_the_fallback_spec_file_is_discovered(self, tmp_path):
        """`dstf add` writes <reference_root>/test_spec.json when no test_spec
        is configured; discovery reads that same file (Conventions #8)."""
        from dstf.discovery.test_registry import discover_tests

        config = _write_lib(tmp_path)  # no "test_spec" key in testing.json
        assert config.test_spec_file is None
        config.spec_path.parent.mkdir(parents=True, exist_ok=True)
        config.spec_path.write_text(
            json.dumps({"tests": [{"model": "Foo.Fallback", "variables": ["x"]}]}),
            encoding="utf-8",
        )
        assert "Foo.Fallback" in {t.model_id for t in discover_tests(config)}
