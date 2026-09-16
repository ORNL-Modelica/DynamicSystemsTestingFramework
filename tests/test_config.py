"""Tests for config.py — path resolution, testing.json loading."""

import json
import os
import shutil
from pathlib import Path

import pytest

from dstf.backends.openmodelica.mos_generator import classify_dependency
from dstf.config import (
    BACKEND_FAMILIES,
    Config,
    ConfigError,
    _detect_backend,
    detect_os,
    find_package_dir,
    load_config_file,
    read_package_name,
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
        assert config["backend"] == "Dymola"
        assert config["backend_setup"] == ["OutputCPUtime := true;"]

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

    def test_default_backend(self, sample_models_dir):
        """Default simulator is Dymola."""
        config = Config(source_path=sample_models_dir)
        assert config.backend_name == "Dymola"
        assert config.backend == "Dymola"

    def test_config_from_file(self, tmp_config_dir):
        """Config loaded from testing.json with source_path."""
        config = Config(
            config_file=str(tmp_config_dir / "testing.json"),
        )
        assert config.library_name == "ModelicaTestingLib"
        assert config.backend_setup == ["OutputCPUtime := true;"]

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
            "backend": "Dymola",
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
            "backend": "Dymola",
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
            backend_name="OpenModelica",  # Override config's "Dymola"
        )
        assert config.backend_name == "OpenModelica"

    def test_explicit_dymola_cli_overrides_config_file(self, tmp_path):
        """Regression: a CLI passing ``backend_name="Dymola"`` is authoritative
        even when testing.json says something else.

        Pre-fix, ``Config.backend_name`` defaulted to the literal string
        ``"Dymola"`` and the resolution code used ``self.backend_name == "Dymola"``
        as a sentinel meaning "user didn't choose one." That collapsed the
        two cases — explicitly chose Dymola vs. didn't choose — and let
        testing.json silently override an explicit ``--backend Dymola``.
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
                    "backend": "OpenModelica",
                }
            )
        )
        config = Config(
            config_file=str(cfg_dir / "testing.json"),
            backend_name="Dymola",  # CLI says Dymola
        )
        assert config.backend_name == "Dymola"

    def test_reference_root_auto_detect(self, tmp_path, sample_models_dir):
        """When testing.json is in ReferenceResults/, that becomes reference_root."""
        ref_dir = tmp_path / "ReferenceResults"
        ref_dir.mkdir()

        models_dest = tmp_path / "models"
        shutil.copytree(sample_models_dir, models_dest)

        cfg = {"source_path": "../models", "backend": "Dymola"}
        (ref_dir / "testing.json").write_text(json.dumps(cfg))

        config = Config(config_file=str(ref_dir / "testing.json"))
        assert config.reference_root == ref_dir.resolve()

    def test_versioned_name_partitions_on_the_canonical_family(
        self, sample_models_dir, tmp_path
    ):
        """A versioned backend name must partition on its FAMILY, not itself.

        ``backend_name`` is what the user supplied ("Dymola 2026x");
        ``backend`` is the canonical family ("Dymola") that owns the
        reference tree. Collapsing the two — trivially done by a later
        refactor, since the names now differ by one word — sends every
        versioned-name user's lookups to a directory none of their stored
        baselines live in: the whole suite reports NO_BASELINE, and an
        --accept then writes a full parallel reference tree in the wrong
        place. The other reference_dir test cannot catch it, because its
        unversioned default makes the two strings equal.
        """
        config = Config(
            source_path=sample_models_dir,
            reference_root=tmp_path / "refs",
            backend_name="Dymola 2026x",
        )

        assert config.backend_name == "Dymola 2026x"
        assert config.backend == "Dymola"
        # reference tree: canonical family
        assert config.reference_dir.parts[-2] == "Dymola"
        # work dir: the versioned name, so two versions do not collide
        assert "Dymola_2026x" in config.work_dir.parts

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
            "backend": "Dymola",
            "diagnostic_variables": ["CPUtime", "EventCounter", "MyCustomVar"],
        }
        (config_dir / "testing.json").write_text(json.dumps(cfg))

        config = Config(config_file=str(config_dir / "testing.json"))
        assert config.diagnostic_variables == ["CPUtime", "EventCounter", "MyCustomVar"]


class TestAutoDetectBackend:
    """Unit tests for the `_auto_detect_backend` helper + integration
    through Config.__post_init__ when testing.json omits the `backend` key."""

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
        from dstf.config import _auto_detect_backend

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
        name, path = _auto_detect_backend(simulators)
        assert name == "OpenModelica"
        assert path == str(fake_omc)

    def test_auto_detect_pinned_paths_block_fallback(self, tmp_path, monkeypatch):
        """If a simulator's list is entirely pinned absolute paths and NONE
        exist, we must NOT fall back to a same-named binary on PATH — the
        pinned list expresses intent to use *that* install."""
        from dstf.config import _auto_detect_backend

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
        assert _auto_detect_backend(simulators) is None

    def test_auto_detect_none_if_nothing_resolves(self, tmp_path, monkeypatch):
        from dstf.config import _auto_detect_backend

        # Empty PATH and bogus paths — nothing resolves.
        monkeypatch.setenv("PATH", str(tmp_path))
        simulators = {
            "Dymola": ["C:\\nope\\dymola.exe"],
            "OpenModelica": ["/nope/omc"],
        }
        assert _auto_detect_backend(simulators) is None

    def test_config_autopicks_when_backend_key_omitted(self, tmp_path, monkeypatch):
        """Config.__post_init__ auto-picks when testing.json omits the
        `backend` key AND no --backend CLI override was passed."""
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
                    "backends": {
                        "Dymola": ["C:\\nope\\dymola.exe"],  # pinned, missing
                        "OpenModelica": ["omc"],  # PATH lookup → hits fake
                    },
                }
            )
        )
        config = Config(config_file=str(cfg_dir / "testing.json"))
        assert config.backend_name == "OpenModelica"
        assert config.backend_path == str(fake_omc)

    def test_config_preserves_explicit_backend_in_file(self, tmp_path, monkeypatch):
        """If testing.json has an explicit "backend" key, auto-pick must
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
                    "backend": "Dymola",
                    "backends": {"Dymola": ["C:\\nope\\dymola.exe"]},
                }
            )
        )
        config = Config(config_file=str(cfg_dir / "testing.json"))
        assert config.backend_name == "Dymola"


# =========================================================================
# Folded in from tests/test_review_fixes_config.py — regression tests for the
# 2026-07-06 review, Theme 2: config values silently ignored or destroyed.
#
# Decisions encoded here (see CODE_REVIEW_2026-07-06.md):
#
# * Finding 11 — a testing.json that EXISTS but fails to parse (or read)
#   raises a clear error naming the file; it is never silently replaced by
#   ``{}`` and NEVER overwritten by the auto-create-defaults branch.
#   A genuinely missing file still yields ``{}``.
# * Finding 14 — an explicit ``--config`` path that does not exist raises,
#   instead of silently falling through to auto-detection (and possibly
#   writing a fresh default testing.json).
# * Finding 19 — auto-creating the default testing.json makes the target
#   directory first (fresh ``--reference-root``), and auto-create only fires
#   when no config file was found anywhere (an existing config — even one
#   that parses to ``{}`` — is never overwritten).
# * Finding 12 — the documented testing.json ``tolerance`` key is read into
#   ``Config.tolerance``. Precedence: explicit constructor/CLI value > file >
#   default 1e-4, with is-not-None semantics so an explicit 0.0 is honored.
# * Finding 15 — a relative ``work_dir`` in testing.json resolves against the
#   config file's directory (like test_spec / dependencies), not the CWD.
# * Finding 18 — simulator auto-detect never clobbers an explicitly supplied
#   ``backend_path``; it may fill the simulator *name*, but only fills the
#   path when none was given.
# =========================================================================


def _make_lib(parent: Path, name: str = "Lib") -> Path:
    """Create a minimal Modelica package directory under ``parent``."""
    lib = parent / name
    lib.mkdir(parents=True, exist_ok=True)
    (lib / "package.mo").write_text(f"package {name} end {name};", encoding="utf-8")
    return lib


# ---------------------------------------------------------------------------
# Finding 11 — malformed testing.json must raise, never be overwritten
# ---------------------------------------------------------------------------


class TestRenamedKeysAreRejected:
    """The old vocabulary must fail loudly, never be silently ignored.

    Renaming `simulator`/`simulators`/`simulator_setup` with no alias is the
    decision. The hazard it creates is that an untouched testing.json keeps
    PARSING while every setting in it does nothing — which a user experiences
    as "the tool ignores my config", with no diagnostic anywhere. Erroring is
    not a compatibility path: the old key never works again, it just says so.
    Without this test nothing in the tree fails if a rebase or cherry-pick
    restores an old-key read.
    """

    @pytest.mark.parametrize(
        "key,replacement",
        [
            ("simulator", "backend"),
            ("simulators", "backends"),
            ("simulator_setup", "backend_setup"),
        ],
    )
    def test_old_key_raises_and_names_its_replacement(self, tmp_path, key, replacement):
        cfg = tmp_path / "testing.json"
        value = {} if key == "simulators" else "x"
        cfg.write_text(json.dumps({key: value}), encoding="utf-8")

        with pytest.raises(ConfigError, match="renamed key") as exc:
            load_config_file(cfg)

        assert replacement in str(exc.value)

    def test_new_keys_load_clean(self, tmp_path):
        cfg = tmp_path / "testing.json"
        cfg.write_text(
            json.dumps({"backend": "Dymola", "backends": {}, "backend_setup": ["x;"]}),
            encoding="utf-8",
        )

        assert load_config_file(cfg)["backend"] == "Dymola"


class TestFinding11MalformedConfig:
    def test_load_config_file_missing_returns_empty(self, tmp_path):
        """A genuinely missing file is still 'no config' — empty dict."""
        assert load_config_file(tmp_path) == {}
        assert load_config_file(tmp_path / "testing.json") == {}

    def test_load_config_file_malformed_raises_naming_file(self, tmp_path):
        """An existing file with a JSON syntax error raises a clear error
        that names the file and the parse problem — not a silent ``{}``."""
        cfg = tmp_path / "testing.json"
        cfg.write_text('{"backend": "Dymola",}', encoding="utf-8")  # trailing comma
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
        original = '{"backend": "Dymola", "dependencies": [,]}'
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

    def test_default_config_written_with_lf(self, tmp_path, monkeypatch):
        """testing.json is a surface users version-control, so its line
        ending must not depend on the OS that wrote it.

        A byte assertion cannot fail on Linux — CPython
        compiles the CRLF write translation in rather than reading
        os.linesep — so pin the argument at the call site instead.
        """
        monkeypatch.chdir(tmp_path)
        lib = _make_lib(tmp_path / "src")

        seen: dict[str, object] = {}
        real = Path.write_text

        def spy(self, data, *args, **kwargs):
            seen[self.name] = kwargs.get("newline")
            return real(self, data, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", spy)
        Config(source_path=lib, reference_root=tmp_path / "fresh" / "refs")

        assert seen["testing.json"] == "\n"

    def test_existing_empty_config_not_overwritten(self, tmp_path, monkeypatch):
        """A found config that parses to {} counts as 'found' — auto-create
        must not fire and must not overwrite the user's file."""
        monkeypatch.chdir(tmp_path)
        lib = _make_lib(tmp_path)
        cfg = tmp_path / "testing.json"
        cfg.write_text("{}", encoding="utf-8")
        config = Config(source_path=lib)
        assert cfg.read_text(encoding="utf-8") == "{}"
        assert config.backend_name == "Dymola"  # historical default still applies


# ---------------------------------------------------------------------------
# Finding 12 — testing.json `tolerance` key is honored
# ---------------------------------------------------------------------------


class TestFinding12ToleranceFromFile:
    def _config_dir(self, tmp_path: Path, extra: dict) -> Path:
        cfg_dir = tmp_path / "proj"
        _make_lib(cfg_dir)
        cfg = {"source_path": "Lib", "backend": "Dymola", **extra}
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
        cfg = {"source_path": "Lib", "backend": "Dymola", "work_dir": "out/work"}
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
            "backend": "Dymola",
            "work_dir": str(abs_work),
        }
        (cfg_dir / "testing.json").write_text(json.dumps(cfg), encoding="utf-8")

        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        config = Config(config_file=str(cfg_dir / "testing.json"))
        assert config.work_dir == abs_work.resolve()


# ---------------------------------------------------------------------------
# Finding 18 — auto-detect must not clobber an explicit backend_path
# ---------------------------------------------------------------------------


class TestFinding18BackendPath:
    def _autodetect_setup(self, tmp_path: Path, monkeypatch) -> Path:
        """testing.json with no `backend` key and a backends map whose
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
            "backends": {"OpenModelica": ["omc"]},  # no "backend" key
        }
        (cfg_dir / "testing.json").write_text(json.dumps(cfg), encoding="utf-8")
        return cfg_dir

    def test_cli_backend_path_survives_autodetect(self, tmp_path, monkeypatch):
        """--backend-path without --backend: auto-detect may fill the
        simulator NAME, but the explicit path must win."""
        cfg_dir = self._autodetect_setup(tmp_path, monkeypatch)
        config = Config(
            config_file=str(cfg_dir / "testing.json"),
            backend_path="/my/custom/omc",
        )
        assert config.backend_name == "OpenModelica"
        assert config.backend_path == "/my/custom/omc"

    def test_autodetect_still_fills_path_when_unset(self, tmp_path, monkeypatch):
        """Guard the happy path: with no explicit backend_path, auto-detect
        fills both name and path as before."""
        cfg_dir = self._autodetect_setup(tmp_path, monkeypatch)
        config = Config(config_file=str(cfg_dir / "testing.json"))
        assert config.backend_name == "OpenModelica"
        assert config.backend_path == str(tmp_path / "bin" / "omc")


# =========================================================================
# Folded in from tests/test_review_fixes_cli.py — regression tests for the
# 2026-07-06 review, final CLI/config/manifest batch (CODE_REVIEW_2026-07-06.md).
#
# Finding 67 — "Python" is a recognized simulator backend prefix, so
# ``"backend": "Python 3.12"`` resolves.
# =========================================================================


def test_python_backend_name_resolves():
    assert "Python" in BACKEND_FAMILIES
    assert _detect_backend("Python 3.12") == "Python"
    assert _detect_backend("Python") == "Python"


# =========================================================================
# Folded in from tests/test_review_fixes_discovery.py — regression tests for
# the 2026-07-06 review, Themes 2 & 6 (CODE_REVIEW_2026-07-06.md).
#
# Finding 17: bare library names in testing.json `dependencies` pass through
# Config untouched so classify_dependency routes them to loadModel().
# =========================================================================


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
                    "backend": "OpenModelica",
                    "backends": {},
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


def test_spec_path_is_the_configured_file_else_the_reference_root_fallback(tmp_path):
    from dstf.config import Config

    (tmp_path / "package.mo").write_text("package X\nend X;\n", encoding="utf-8")
    explicit = Config(
        source_path=tmp_path,
        reference_root=tmp_path / "refs",
        test_spec_file=tmp_path / "s.json",
    )
    assert explicit.spec_path == (tmp_path / "s.json").resolve()
    fallback = Config(source_path=tmp_path, reference_root=tmp_path / "refs")
    assert fallback.spec_path == fallback.reference_root / "test_spec.json"


def test_config_keeps_the_backends_map_for_the_tools_backend_select(tmp_path):
    """The authoring shell offers testing.json's `backends` names as the
    backends to switch to (brief §3.6); until now the map was read for the
    executable path and dropped."""
    (tmp_path / "testing.json").write_text(
        json.dumps(
            {
                "source_type": "python",
                "source_path": ".",
                "library_name": "L",
                "backend": "Python",
                "backends": {
                    "Python": ["python3"],
                    "Dymola 2026x": ["C:/Program Files/Dymola 2026x/bin64/Dymola.exe"],
                },
            }
        ),
        encoding="utf-8",
    )
    config = Config(config_file=tmp_path / "testing.json")
    assert config.backends == {
        "Python": ["python3"],
        "Dymola 2026x": ["C:/Program Files/Dymola 2026x/bin64/Dymola.exe"],
    }
