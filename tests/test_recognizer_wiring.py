"""Tests for PTA.3 — user recognizers wired into Config + discovery."""

from __future__ import annotations

import json
import shutil
import textwrap
from pathlib import Path

import pytest

from modelica_testing.config import Config
from modelica_testing.discovery.json_recognizer import (
    JsonRecognizer,
    RecognizerSpecError,
)
from modelica_testing.discovery.test_registry import discover_tests


PROJECT_ROOT = Path(__file__).parent.parent
SAMPLE_LIB = PROJECT_ROOT / "examples" / "modelica" / "ModelicaTestingLib"


def _make_lib(tmp_path: Path) -> Path:
    """Copy ModelicaTestingLib into tmp_path so we can drop extra .mo files."""
    dest = tmp_path / "TestLib"
    shutil.copytree(SAMPLE_LIB, dest)
    return dest


def _write_testing_json(config_dir: Path, source_path_rel: str, **extra) -> Path:
    """Write a minimal testing.json with optional recognizer config."""
    cfg = {
        "source_path": source_path_rel,
        "simulator": "Dymola",
        "simulators": {},
        **extra,
    }
    p = config_dir / "testing.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Config loads recognizers from testing.json
# ---------------------------------------------------------------------------

class TestConfigLoadsRecognizers:
    def test_no_recognizers_defaults_to_empty(self, tmp_path):
        lib = _make_lib(tmp_path)
        cfg_dir = tmp_path / "refs"
        cfg_dir.mkdir()
        cfg_path = _write_testing_json(cfg_dir, "../TestLib")
        config = Config(config_file=str(cfg_path))
        assert config.recognizers == []
        assert config.disabled_bundled == []

    def test_recognizers_parsed_from_json(self, tmp_path):
        lib = _make_lib(tmp_path)
        cfg_dir = tmp_path / "refs"
        cfg_dir.mkdir()
        cfg_path = _write_testing_json(
            cfg_dir, "../TestLib",
            recognizers=[{
                "name": "test:icons",
                "match": {"type": "extends", "class_pattern": "*Icons.Example"},
            }],
        )
        config = Config(config_file=str(cfg_path))
        assert len(config.recognizers) == 1
        assert isinstance(config.recognizers[0], JsonRecognizer)
        assert config.recognizers[0].name == "test:icons"

    def test_disabled_bundled_parsed_from_json(self, tmp_path):
        lib = _make_lib(tmp_path)
        cfg_dir = tmp_path / "refs"
        cfg_dir.mkdir()
        cfg_path = _write_testing_json(
            cfg_dir, "../TestLib",
            disable_bundled=["modelica:bundled-unit-tests"],
        )
        config = Config(config_file=str(cfg_path))
        assert config.disabled_bundled == ["modelica:bundled-unit-tests"]

    def test_bad_recognizer_spec_raises(self, tmp_path):
        lib = _make_lib(tmp_path)
        cfg_dir = tmp_path / "refs"
        cfg_dir.mkdir()
        cfg_path = _write_testing_json(
            cfg_dir, "../TestLib",
            recognizers=[{"name": "bad", "match": {"type": "no-such-type"}}],
        )
        with pytest.raises(RecognizerSpecError, match="unknown match type"):
            Config(config_file=str(cfg_path))


# ---------------------------------------------------------------------------
# Discovery picks up user recognizers + respects disable_bundled
# ---------------------------------------------------------------------------

class TestDiscoveryWiring:
    def _add_extends_test_model(self, lib: Path) -> str:
        """Add a model that uses extends-style instead of UnitTests; returns model_id."""
        examples_dir = lib / "Examples"
        new_mo = examples_dir / "ExtendsExample.mo"
        new_mo.write_text(
            textwrap.dedent("""\
                within ModelicaTestingLib.Examples;
                model ExtendsExample
                  extends Modelica.Icons.Example;
                  Real x;
                equation
                  x = time;
                  annotation(experiment(StopTime=42, Tolerance=1e-7));
                end ExtendsExample;
            """),
            encoding="utf-8",
        )
        # Add to package.order
        order = examples_dir / "package.order"
        existing = order.read_text(encoding="utf-8") if order.exists() else ""
        order.write_text(existing + "ExtendsExample\n", encoding="utf-8")
        return "ModelicaTestingLib.Examples.ExtendsExample"

    def test_user_recognizer_discovers_extends_models(self, tmp_path):
        lib = _make_lib(tmp_path)
        new_id = self._add_extends_test_model(lib)
        cfg_dir = tmp_path / "refs"
        cfg_dir.mkdir()
        cfg_path = _write_testing_json(
            cfg_dir, "../TestLib",
            recognizers=[{
                "name": "test:icons-example",
                "match": {"type": "extends", "class_pattern": "*Icons.Example"},
                "fields": {
                    "stop_time": {"from": "experiment-annotation", "name": "StopTime"},
                    "tolerance": {"from": "experiment-annotation", "name": "Tolerance"},
                },
            }],
        )
        config = Config(config_file=str(cfg_path))
        tests = discover_tests(config)
        by_id = {t.model_id: t for t in tests}

        assert new_id in by_id
        assert by_id[new_id].stop_time == 42
        assert by_id[new_id].tolerance == 1e-7

    def test_bundled_still_works_alongside_user(self, tmp_path):
        """User recognizer is additive — bundled tests still discovered."""
        lib = _make_lib(tmp_path)
        self._add_extends_test_model(lib)
        cfg_dir = tmp_path / "refs"
        cfg_dir.mkdir()
        cfg_path = _write_testing_json(
            cfg_dir, "../TestLib",
            recognizers=[{
                "name": "test:icons-example",
                "match": {"type": "extends", "class_pattern": "*Icons.Example"},
            }],
        )
        config = Config(config_file=str(cfg_path))
        tests = discover_tests(config)
        ids = {t.model_id for t in tests}
        # Both bundled-discovered (SimpleTest has UnitTests) and user-discovered
        # (ExtendsExample) are present.
        assert "ModelicaTestingLib.Examples.SimpleTest" in ids
        assert "ModelicaTestingLib.Examples.ExtendsExample" in ids

    def test_disable_bundled_drops_unit_tests_models(self, tmp_path):
        lib = _make_lib(tmp_path)
        self._add_extends_test_model(lib)
        cfg_dir = tmp_path / "refs"
        cfg_dir.mkdir()
        cfg_path = _write_testing_json(
            cfg_dir, "../TestLib",
            recognizers=[{
                "name": "test:icons-example",
                "match": {"type": "extends", "class_pattern": "*Icons.Example"},
            }],
            disable_bundled=["modelica:bundled-unit-tests"],
        )
        config = Config(config_file=str(cfg_path))
        tests = discover_tests(config)
        ids = {t.model_id for t in tests}
        # SimpleTest (and other UnitTests-based models) gone, ExtendsExample present.
        assert "ModelicaTestingLib.Examples.SimpleTest" not in ids
        assert "ModelicaTestingLib.Examples.ExtendsExample" in ids

    def test_user_field_overrides_bundled_per_field(self, tmp_path):
        """When both bundled and user recognize the same model, user values
        win per-field — last writer in the registration order."""
        lib = _make_lib(tmp_path)
        # SimpleTest is UnitTests-based with stop_time=10 from experiment annotation.
        # Add a second recognizer that ALSO matches SimpleTest (it has neither
        # extends nor a custom component — but we can use a constant override
        # by registering a recognizer that matches via UnitTests instantiation).
        cfg_dir = tmp_path / "refs"
        cfg_dir.mkdir()
        cfg_path = _write_testing_json(
            cfg_dir, "../TestLib",
            recognizers=[{
                "name": "user:tighter-tolerance",
                "match": {
                    "type": "component-instantiation",
                    "component_name": "UnitTests",
                },
                "fields": {
                    "tolerance": {"from": "constant", "value": 9.99e-9},
                },
            }],
        )
        config = Config(config_file=str(cfg_path))
        tests = discover_tests(config)
        by_id = {t.model_id: t for t in tests}
        # Bundled set tolerance from experiment annotation; user constant wins.
        assert by_id["ModelicaTestingLib.Examples.SimpleTest"].tolerance == 9.99e-9
