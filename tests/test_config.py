"""Tests for config.py — path resolution, testing.json loading."""

import json
import shutil
from pathlib import Path

import pytest

from dstf.config import (
    Config,
    load_config_file,
    read_package_name,
    find_package_dir,
    detect_os,
)


FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


class TestUtilityFunctions:
    def test_detect_os(self):
        """OS detection returns a known string."""
        result = detect_os()
        assert result in ("windows", "linux", "darwin")

    def test_read_package_name(self, sample_models_dir):
        """Read package name from package.mo."""
        name = read_package_name(sample_models_dir)
        assert name == "ModelicaTestingLib"

    def test_read_package_name_missing(self, tmp_path):
        """Missing package.mo raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            read_package_name(tmp_path)

    def test_find_package_dir_from_package(self, sample_models_dir):
        """find_package_dir from a directory containing package.mo."""
        result = find_package_dir(sample_models_dir)
        assert result == sample_models_dir.resolve()

    def test_find_package_dir_from_parent(self, sample_models_dir):
        """find_package_dir from the parent of a package directory."""
        # The parent of models/ should find models/ as a child with package.mo
        parent = sample_models_dir.parent
        result = find_package_dir(parent)
        assert (result / "package.mo").exists()

    def test_load_config_file(self, fixtures_dir):
        """Load testing.json from fixtures."""
        config = load_config_file(fixtures_dir)
        assert config["simulator"] == "Dymola"
        assert config["simulator_setup"] == ["OutputCPUtime := true;"]

    def test_load_config_file_missing(self, tmp_path):
        """Missing config file returns empty dict."""
        result = load_config_file(tmp_path)
        assert result == {}


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------


class TestConfig:
    def test_explicit_source_path(self, sample_models_dir):
        """Config with explicit source_path."""
        config = Config(source_path=sample_models_dir)
        assert config.source_path == sample_models_dir.resolve()
        assert config.library_name == "ModelicaTestingLib"

    def test_default_simulator(self, sample_models_dir):
        """Default simulator is Dymola."""
        config = Config(source_path=sample_models_dir)
        assert config.simulator == "Dymola"
        assert config.simulator_backend == "Dymola"

    def test_config_from_file(self, tmp_config_dir):
        """Config loaded from testing.json with source_path."""
        config = Config(
            config_file=str(tmp_config_dir / "testing.json"),
        )
        assert config.library_name == "ModelicaTestingLib"
        assert config.simulator_setup == ["OutputCPUtime := true;"]

    def test_config_relative_source_path(self, tmp_config_dir):
        """source_path in testing.json resolves relative to config file."""
        config = Config(
            config_file=str(tmp_config_dir / "testing.json"),
        )
        assert config.source_path.is_absolute()
        assert (config.source_path / "package.mo").exists()

    def test_config_relative_test_spec(self, tmp_path, sample_models_dir):
        """test_spec path resolves relative to config file location."""
        config_dir = tmp_path / "refs"
        config_dir.mkdir()

        # Copy models
        models_dest = config_dir / "ModelicaTestingLib"
        shutil.copytree(sample_models_dir, models_dest)

        # Create spec file
        spec = {"tests": [{"model": "Test.Model", "variables": ["x"]}]}
        (config_dir / "my_spec.json").write_text(json.dumps(spec))

        # Create config pointing to spec
        cfg = {
            "source_path": "ModelicaTestingLib",
            "simulator": "Dymola",
            "test_spec": "my_spec.json",
        }
        (config_dir / "testing.json").write_text(json.dumps(cfg))

        config = Config(config_file=str(config_dir / "testing.json"))
        assert config.test_spec_file == (config_dir / "my_spec.json").resolve()

    def test_config_relative_dependencies(self, tmp_path, sample_models_dir):
        """Dependencies resolve relative to config file location."""
        config_dir = tmp_path / "refs"
        config_dir.mkdir()

        models_dest = config_dir / "ModelicaTestingLib"
        shutil.copytree(sample_models_dir, models_dest)

        # Create a fake dependency dir
        dep_dir = config_dir / "deps" / "SomeLib"
        dep_dir.mkdir(parents=True)

        cfg = {
            "source_path": "ModelicaTestingLib",
            "simulator": "Dymola",
            "dependencies": ["deps/SomeLib"],
        }
        (config_dir / "testing.json").write_text(json.dumps(cfg))

        config = Config(config_file=str(config_dir / "testing.json"))
        assert len(config.dependencies) == 1
        assert Path(config.dependencies[0]).is_absolute()

    def test_cli_overrides_config_file(self, tmp_config_dir, sample_models_dir):
        """CLI args take precedence over config file values."""
        config = Config(
            config_file=str(tmp_config_dir / "testing.json"),
            simulator="OpenModelica",  # Override config's "Dymola"
        )
        assert config.simulator == "OpenModelica"

    def test_explicit_dymola_cli_overrides_config_file(self, tmp_path):
        """Regression: a CLI passing ``simulator="Dymola"`` is authoritative
        even when testing.json says something else.

        Pre-fix, ``Config.simulator`` defaulted to the literal string
        ``"Dymola"`` and the resolution code used ``self.simulator == "Dymola"``
        as a sentinel meaning "user didn't choose one." That collapsed the
        two cases — explicitly chose Dymola vs. didn't choose — and let
        testing.json silently override an explicit ``--simulator Dymola``.
        Default is now ``None``; only ``None`` triggers file/auto-detect.
        """
        lib_dir = tmp_path / "Lib"
        lib_dir.mkdir()
        (lib_dir / "package.mo").write_text("package Lib end Lib;")
        cfg_dir = tmp_path / "Resources" / "ReferenceResults"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "testing.json").write_text(
            json.dumps(
                {
                    "source_path": str(lib_dir),
                    "simulator": "OpenModelica",
                }
            )
        )
        config = Config(
            config_file=str(cfg_dir / "testing.json"),
            simulator="Dymola",  # CLI says Dymola
        )
        assert config.simulator == "Dymola"

    def test_reference_root_auto_detect(self, tmp_path, sample_models_dir):
        """When testing.json is in ReferenceResults/, that becomes reference_root."""
        ref_dir = tmp_path / "ReferenceResults"
        ref_dir.mkdir()

        models_dest = tmp_path / "models"
        shutil.copytree(sample_models_dir, models_dest)

        cfg = {"source_path": "../models", "simulator": "Dymola"}
        (ref_dir / "testing.json").write_text(json.dumps(cfg))

        config = Config(config_file=str(ref_dir / "testing.json"))
        assert config.reference_root == ref_dir.resolve()

    def test_reference_dir_partitioned(self, sample_models_dir):
        """reference_dir is partitioned by simulator backend and OS."""
        config = Config(source_path=sample_models_dir)
        ref_dir = config.reference_dir
        # Should contain simulator and OS in path
        parts = ref_dir.parts
        assert "Dymola" in parts
        os_name = detect_os()
        assert os_name in parts

    def test_default_diagnostic_variables(self, sample_models_dir):
        """Default diagnostic variables include CPUtime and EventCounter."""
        config = Config(source_path=sample_models_dir)
        assert "CPUtime" in config.diagnostic_variables
        assert "EventCounter" in config.diagnostic_variables

    def test_custom_diagnostic_variables(self, tmp_path, sample_models_dir):
        """diagnostic_variables can be overridden via testing.json."""
        config_dir = tmp_path / "refs"
        config_dir.mkdir()
        models_dest = config_dir / "ModelicaTestingLib"
        shutil.copytree(sample_models_dir, models_dest)

        cfg = {
            "source_path": "ModelicaTestingLib",
            "simulator": "Dymola",
            "diagnostic_variables": ["CPUtime", "EventCounter", "MyCustomVar"],
        }
        (config_dir / "testing.json").write_text(json.dumps(cfg))

        config = Config(config_file=str(config_dir / "testing.json"))
        assert config.diagnostic_variables == ["CPUtime", "EventCounter", "MyCustomVar"]


class TestAutoDetectSimulator:
    """Unit tests for the `_auto_detect_simulator` helper + integration
    through Config.__post_init__ when testing.json omits the `simulator` key."""

    def test_looks_like_path_linux(self):
        from dstf.config import _looks_like_path

        assert _looks_like_path("/usr/bin/omc")
        assert not _looks_like_path("omc")
        assert not _looks_like_path("dymola.exe")

    def test_looks_like_path_windows(self):
        from dstf.config import _looks_like_path

        assert _looks_like_path("C:\\Program Files\\Dymola.exe")
        assert _looks_like_path("D:/some/path")
        assert not _looks_like_path("Dymola")

    def test_auto_detect_picks_first_resolvable(self, tmp_path, monkeypatch):
        """First entry whose binary resolves wins. If an earlier entry has
        pinned absolute paths that don't exist, that entry is skipped (no
        fallback to a same-named binary on PATH)."""
        from dstf.config import _auto_detect_simulator

        # Create a fake "omc" binary on a temp PATH — simulate OM available.
        fake_omc = tmp_path / "omc"
        fake_omc.write_text("#!/bin/sh\n")
        fake_omc.chmod(0o755)
        monkeypatch.setenv("PATH", str(tmp_path))

        simulators = {
            "Dymola": [
                "C:\\nope\\dymola.exe",  # pinned path, doesn't exist
            ],
            "OpenModelica": ["omc"],  # bare name → PATH lookup
        }
        name, path = _auto_detect_simulator(simulators)
        assert name == "OpenModelica"
        assert path == str(fake_omc)

    def test_auto_detect_pinned_paths_block_fallback(self, tmp_path, monkeypatch):
        """If a simulator's list is entirely pinned absolute paths and NONE
        exist, we must NOT fall back to a same-named binary on PATH — the
        pinned list expresses intent to use *that* install."""
        from dstf.config import _auto_detect_simulator

        fake_dymola = tmp_path / "dymola"
        fake_dymola.write_text("#!/bin/sh\n")
        fake_dymola.chmod(0o755)
        monkeypatch.setenv("PATH", str(tmp_path))

        simulators = {
            "Dymola": [
                "C:\\Program Files\\Dymola\\Dymola.exe",  # pinned, missing
            ],
        }
        # Fallback suppressed ⇒ nothing resolves despite `dymola` on PATH.
        assert _auto_detect_simulator(simulators) is None

    def test_auto_detect_none_if_nothing_resolves(self, tmp_path, monkeypatch):
        from dstf.config import _auto_detect_simulator

        # Empty PATH and bogus paths — nothing resolves.
        monkeypatch.setenv("PATH", str(tmp_path))
        simulators = {
            "Dymola": ["C:\\nope\\dymola.exe"],
            "OpenModelica": ["/nope/omc"],
        }
        assert _auto_detect_simulator(simulators) is None

    def test_config_autopicks_when_simulator_key_omitted(self, tmp_path, monkeypatch):
        """Config.__post_init__ auto-picks when testing.json omits the
        `simulator` key AND no --simulator CLI override was passed."""
        fake_omc = tmp_path / "omc"
        fake_omc.write_text("#!/bin/sh\n")
        fake_omc.chmod(0o755)
        monkeypatch.setenv("PATH", str(tmp_path))

        lib_dir = tmp_path / "Lib"
        lib_dir.mkdir()
        (lib_dir / "package.mo").write_text("package Lib end Lib;")

        cfg_dir = tmp_path / "Resources" / "ReferenceResults"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "testing.json").write_text(
            json.dumps(
                {
                    "source_path": str(lib_dir),
                    "simulators": {
                        "Dymola": ["C:\\nope\\dymola.exe"],  # pinned, missing
                        "OpenModelica": ["omc"],  # PATH lookup → hits fake
                    },
                }
            )
        )
        config = Config(config_file=str(cfg_dir / "testing.json"))
        assert config.simulator == "OpenModelica"
        assert config.simulator_path == str(fake_omc)

    def test_config_preserves_explicit_simulator_in_file(self, tmp_path, monkeypatch):
        """If testing.json has an explicit "simulator" key, auto-pick must
        not override it — behavior change would be a breaking surprise."""
        lib_dir = tmp_path / "Lib"
        lib_dir.mkdir()
        (lib_dir / "package.mo").write_text("package Lib end Lib;")
        cfg_dir = tmp_path / "Resources" / "ReferenceResults"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "testing.json").write_text(
            json.dumps(
                {
                    "source_path": str(lib_dir),
                    "simulator": "Dymola",
                    "simulators": {"Dymola": ["C:\\nope\\dymola.exe"]},
                }
            )
        )
        config = Config(config_file=str(cfg_dir / "testing.json"))
        assert config.simulator == "Dymola"
