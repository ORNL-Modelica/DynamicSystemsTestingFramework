"""Regression tests for the 2026-07-06 review — Theme 2: config values
silently ignored or destroyed.

Decisions encoded here (see CODE_REVIEW_2026-07-06.md):

* Finding 11 — a testing.json that EXISTS but fails to parse (or read)
  raises a clear error naming the file; it is never silently replaced by
  ``{}`` and NEVER overwritten by the auto-create-defaults branch.
  A genuinely missing file still yields ``{}``.
* Finding 14 — an explicit ``--config`` path that does not exist raises,
  instead of silently falling through to auto-detection (and possibly
  writing a fresh default testing.json).
* Finding 19 — auto-creating the default testing.json makes the target
  directory first (fresh ``--reference-root``), and auto-create only fires
  when no config file was found anywhere (an existing config — even one
  that parses to ``{}`` — is never overwritten).
* Finding 12 — the documented testing.json ``tolerance`` key is read into
  ``Config.tolerance``. Precedence: explicit constructor/CLI value > file >
  default 1e-4, with is-not-None semantics so an explicit 0.0 is honored.
* Finding 15 — a relative ``work_dir`` in testing.json resolves against the
  config file's directory (like test_spec / dependencies), not the CWD.
* Finding 18 — simulator auto-detect never clobbers an explicitly supplied
  ``simulator_path``; it may fill the simulator *name*, but only fills the
  path when none was given.
"""

import json
import os
from pathlib import Path

import pytest

from dstf.config import Config, load_config_file


def _make_lib(parent: Path, name: str = "Lib") -> Path:
    """Create a minimal Modelica package directory under ``parent``."""
    lib = parent / name
    lib.mkdir(parents=True, exist_ok=True)
    (lib / "package.mo").write_text(f"package {name} end {name};", encoding="utf-8")
    return lib


# ---------------------------------------------------------------------------
# Finding 11 — malformed testing.json must raise, never be overwritten
# ---------------------------------------------------------------------------


class TestFinding11MalformedConfig:
    def test_load_config_file_missing_returns_empty(self, tmp_path):
        """A genuinely missing file is still 'no config' — empty dict."""
        assert load_config_file(tmp_path) == {}
        assert load_config_file(tmp_path / "testing.json") == {}

    def test_load_config_file_malformed_raises_naming_file(self, tmp_path):
        """An existing file with a JSON syntax error raises a clear error
        that names the file and the parse problem — not a silent ``{}``."""
        cfg = tmp_path / "testing.json"
        cfg.write_text('{"simulator": "Dymola",}', encoding="utf-8")  # trailing comma
        with pytest.raises(ValueError) as exc_info:
            load_config_file(tmp_path)
        assert "testing.json" in str(exc_info.value)

    def test_load_config_file_unreadable_raises(self, tmp_path):
        """An existing file that cannot be read (OSError) raises a clear
        error instead of passing silently."""
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            pytest.skip("running as root — chmod 0 does not block reads")
        cfg = tmp_path / "testing.json"
        cfg.write_text("{}", encoding="utf-8")
        cfg.chmod(0)
        try:
            with pytest.raises(ValueError) as exc_info:
                load_config_file(tmp_path)
            assert "testing.json" in str(exc_info.value)
        finally:
            cfg.chmod(0o644)  # let pytest clean tmp_path up

    def test_malformed_explicit_config_raises_and_preserves_file(
        self, tmp_path, monkeypatch
    ):
        """Config(config_file=...) on a malformed file raises and the user's
        file survives byte-for-byte (pre-fix: auto-create overwrote it)."""
        monkeypatch.chdir(tmp_path)
        _make_lib(tmp_path)
        cfg = tmp_path / "testing.json"
        original = '{"simulator": "Dymola", "dependencies": [,]}'
        cfg.write_text(original, encoding="utf-8")
        with pytest.raises(ValueError):
            Config(config_file=str(cfg))
        assert cfg.read_text(encoding="utf-8") == original

    def test_malformed_searched_config_raises_and_preserves_file(
        self, tmp_path, monkeypatch
    ):
        """Same guarantee when the malformed file is discovered via the
        search path (no explicit config_file)."""
        monkeypatch.chdir(tmp_path)
        lib = _make_lib(tmp_path)
        cfg = tmp_path / "testing.json"  # lib parent = first search dir
        original = "{ this is not json"
        cfg.write_text(original, encoding="utf-8")
        with pytest.raises(ValueError):
            Config(source_path=lib)
        assert cfg.read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# Finding 14 — nonexistent --config path must raise
# ---------------------------------------------------------------------------


class TestFinding14MissingExplicitConfig:
    def test_nonexistent_explicit_config_raises(self, tmp_path, monkeypatch):
        """--config pointing at a missing file raises a clear error naming
        the path, and nothing falls through to auto-create defaults."""
        monkeypatch.chdir(tmp_path)
        _make_lib(tmp_path)
        missing = tmp_path / "nope" / "testing.json"
        with pytest.raises(ValueError) as exc_info:
            Config(config_file=str(missing))
        assert "nope" in str(exc_info.value)
        # No silent fall-through: no default config written anywhere.
        assert not missing.exists()
        assert not (tmp_path / "testing.json").exists()

    def test_explicit_config_dir_without_file_raises(self, tmp_path, monkeypatch):
        """--config pointing at an existing directory that has no
        testing.json inside is equally an error."""
        monkeypatch.chdir(tmp_path)
        _make_lib(tmp_path)
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        with pytest.raises(ValueError):
            Config(config_file=str(empty_dir))
        assert not (empty_dir / "testing.json").exists()


# ---------------------------------------------------------------------------
# Finding 19 — auto-create mkdirs first; only fires when nothing was found
# ---------------------------------------------------------------------------


class TestFinding19AutoCreate:
    def test_default_config_created_in_fresh_reference_root(
        self, tmp_path, monkeypatch
    ):
        """First run with a fresh --reference-root: the directory does not
        exist yet, so auto-create must mkdir(parents=True) before writing
        (pre-fix: FileNotFoundError)."""
        monkeypatch.chdir(tmp_path)
        lib = _make_lib(tmp_path / "src")
        ref_root = tmp_path / "fresh" / "refs"  # does not exist
        config = Config(source_path=lib, reference_root=ref_root)
        assert (ref_root / "testing.json").is_file()
        assert config.reference_root == ref_root.resolve()

    def test_existing_empty_config_not_overwritten(self, tmp_path, monkeypatch):
        """A found config that parses to {} counts as 'found' — auto-create
        must not fire and must not overwrite the user's file."""
        monkeypatch.chdir(tmp_path)
        lib = _make_lib(tmp_path)
        cfg = tmp_path / "testing.json"
        cfg.write_text("{}", encoding="utf-8")
        config = Config(source_path=lib)
        assert cfg.read_text(encoding="utf-8") == "{}"
        assert config.simulator == "Dymola"  # historical default still applies


# ---------------------------------------------------------------------------
# Finding 12 — testing.json `tolerance` key is honored
# ---------------------------------------------------------------------------


class TestFinding12ToleranceFromFile:
    def _config_dir(self, tmp_path: Path, extra: dict) -> Path:
        cfg_dir = tmp_path / "proj"
        _make_lib(cfg_dir)
        cfg = {"source_path": "Lib", "simulator": "Dymola", **extra}
        (cfg_dir / "testing.json").write_text(json.dumps(cfg), encoding="utf-8")
        return cfg_dir

    def test_tolerance_read_from_file(self, tmp_path):
        cfg_dir = self._config_dir(tmp_path, {"tolerance": 0.05})
        config = Config(config_file=str(cfg_dir / "testing.json"))
        assert config.tolerance == 0.05

    def test_cli_tolerance_overrides_file(self, tmp_path):
        cfg_dir = self._config_dir(tmp_path, {"tolerance": 0.05})
        config = Config(config_file=str(cfg_dir / "testing.json"), tolerance=0.01)
        assert config.tolerance == 0.01

    def test_zero_file_tolerance_honored(self, tmp_path):
        """An explicit 0.0 in the file is a real value, not 'unset'."""
        cfg_dir = self._config_dir(tmp_path, {"tolerance": 0.0})
        config = Config(config_file=str(cfg_dir / "testing.json"))
        assert config.tolerance == 0.0

    def test_zero_cli_tolerance_overrides_file(self, tmp_path):
        """An explicit constructor 0.0 beats a nonzero file value
        (is-not-None semantics, matching the Batch A comparator fix)."""
        cfg_dir = self._config_dir(tmp_path, {"tolerance": 0.05})
        config = Config(config_file=str(cfg_dir / "testing.json"), tolerance=0.0)
        assert config.tolerance == 0.0

    def test_default_tolerance_when_unset_everywhere(self, tmp_path):
        """No CLI value, no file key: resolved default 1e-4, always a float
        (internal None sentinel must never leak out of __post_init__)."""
        cfg_dir = self._config_dir(tmp_path, {})
        config = Config(config_file=str(cfg_dir / "testing.json"))
        assert config.tolerance == pytest.approx(1e-4)
        assert isinstance(config.tolerance, float)


# ---------------------------------------------------------------------------
# Finding 15 — relative work_dir resolves against the config file's dir
# ---------------------------------------------------------------------------


class TestFinding15WorkDir:
    def test_relative_work_dir_resolves_against_config_dir(self, tmp_path, monkeypatch):
        cfg_dir = tmp_path / "proj"
        _make_lib(cfg_dir)
        cfg = {"source_path": "Lib", "simulator": "Dymola", "work_dir": "out/work"}
        (cfg_dir / "testing.json").write_text(json.dumps(cfg), encoding="utf-8")

        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)  # CWD != config dir — must not matter

        config = Config(config_file=str(cfg_dir / "testing.json"))
        assert config.work_dir == (cfg_dir / "out" / "work").resolve()

    def test_absolute_work_dir_untouched(self, tmp_path, monkeypatch):
        cfg_dir = tmp_path / "proj"
        _make_lib(cfg_dir)
        abs_work = tmp_path / "abs_out"
        cfg = {
            "source_path": "Lib",
            "simulator": "Dymola",
            "work_dir": str(abs_work),
        }
        (cfg_dir / "testing.json").write_text(json.dumps(cfg), encoding="utf-8")

        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        config = Config(config_file=str(cfg_dir / "testing.json"))
        assert config.work_dir == abs_work.resolve()


# ---------------------------------------------------------------------------
# Finding 18 — auto-detect must not clobber an explicit simulator_path
# ---------------------------------------------------------------------------


class TestFinding18SimulatorPath:
    def _autodetect_setup(self, tmp_path: Path, monkeypatch) -> Path:
        """testing.json with no `simulator` key and a simulators map whose
        OpenModelica entry resolves via a fake `omc` on PATH."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        fake_omc = bin_dir / "omc"
        fake_omc.write_text("#!/bin/sh\n")
        fake_omc.chmod(0o755)
        monkeypatch.setenv("PATH", str(bin_dir))

        cfg_dir = tmp_path / "proj"
        _make_lib(cfg_dir)
        cfg = {
            "source_path": "Lib",
            "simulators": {"OpenModelica": ["omc"]},  # no "simulator" key
        }
        (cfg_dir / "testing.json").write_text(json.dumps(cfg), encoding="utf-8")
        return cfg_dir

    def test_cli_simulator_path_survives_autodetect(self, tmp_path, monkeypatch):
        """--simulator-path without --simulator: auto-detect may fill the
        simulator NAME, but the explicit path must win."""
        cfg_dir = self._autodetect_setup(tmp_path, monkeypatch)
        config = Config(
            config_file=str(cfg_dir / "testing.json"),
            simulator_path="/my/custom/omc",
        )
        assert config.simulator == "OpenModelica"
        assert config.simulator_path == "/my/custom/omc"

    def test_autodetect_still_fills_path_when_unset(self, tmp_path, monkeypatch):
        """Guard the happy path: with no explicit simulator_path, auto-detect
        fills both name and path as before."""
        cfg_dir = self._autodetect_setup(tmp_path, monkeypatch)
        config = Config(config_file=str(cfg_dir / "testing.json"))
        assert config.simulator == "OpenModelica"
        assert config.simulator_path == str(tmp_path / "bin" / "omc")
