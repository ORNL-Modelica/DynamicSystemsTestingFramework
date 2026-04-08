"""Tests for discovery: mo_parser, spec_parser, test_registry."""

import json
from pathlib import Path

import pytest

from modelica_testing.discovery.mo_parser import (
    parse_mo_file,
    _extract_within,
    _extract_model_name,
)
from modelica_testing.discovery.spec_parser import (
    parse_test_spec,
    add_to_test_spec,
    update_test_variables,
)
from modelica_testing.discovery.test_registry import discover_tests
from modelica_testing.config import Config


FIXTURES_DIR = Path(__file__).parent / "fixtures"
PROJECT_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# .mo parser
# ---------------------------------------------------------------------------

class TestMoParser:
    def test_parse_with_unit_tests(self):
        """Parse a .mo file that has a UnitTests component."""
        result = parse_mo_file(PROJECT_ROOT / "ModelicaTestingLib" / "Examples" / "SimpleTest.mo")
        assert result is not None
        assert result.model_id == "ModelicaTestingLib.Examples.SimpleTest"
        assert result.unit_test is not None
        assert result.unit_test.n == 2
        assert result.unit_test.x_expressions == ["x", "y"]

    def test_parse_without_unit_tests(self):
        """Parse a .mo file without UnitTests => returns None (nothing to test)."""
        result = parse_mo_file(PROJECT_ROOT / "ModelicaTestingLib" / "Examples" / "NoUnitTest.mo")
        assert result is None

    def test_parse_experiment(self):
        """Extract experiment annotation."""
        result = parse_mo_file(PROJECT_ROOT / "ModelicaTestingLib" / "Examples" / "SimpleTest.mo")
        assert result.experiment is not None
        assert result.experiment.stop_time == 10.0
        assert result.experiment.tolerance == 1e-6
        assert result.experiment.method == "Dassl"

    def test_extract_within(self):
        assert _extract_within("within MyLib.Examples;\nmodel Foo") == "MyLib.Examples"
        assert _extract_within("model Foo") == ""

    def test_extract_model_name(self):
        assert _extract_model_name('model SimpleTest "A test"') == "SimpleTest"
        assert _extract_model_name('package Foo "Bar"') == "Foo"

    def test_nonexistent_file(self):
        result = parse_mo_file(Path("/nonexistent/file.mo"))
        assert result is None


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

    def test_nonexistent_spec(self):
        """Nonexistent spec file returns empty list."""
        tests = parse_test_spec(Path("/nonexistent/test_spec.json"))
        assert tests == []


# ---------------------------------------------------------------------------
# test_registry (discover_tests)
# ---------------------------------------------------------------------------

class TestDiscoverTests:
    def test_discover_from_mo_files(self, sample_models_dir):
        """Discover tests from .mo files with UnitTests."""
        config = Config(package_path=sample_models_dir)
        tests = discover_tests(config)
        model_ids = [t.model_id for t in tests]
        assert "ModelicaTestingLib.Examples.SimpleTest" in model_ids

    def test_discover_with_spec(self, sample_models_dir, sample_test_spec):
        """Discover with spec file adds spec-only tests."""
        config = Config(
            package_path=sample_models_dir,
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
            package_path=sample_models_dir,
            test_spec_file=sample_test_spec,
        )
        tests = discover_tests(config)
        by_id = {t.model_id: t for t in tests}

        assert by_id["ModelicaTestingLib.Examples.SimpleTest"].source == "unit_tests"
        assert by_id["ModelicaTestingLib.Examples.SpecOnly"].source == "spec"
