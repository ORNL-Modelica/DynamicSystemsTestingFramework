"""Tests for config.py — path resolution, testing.json loading."""

import json
import os
import shutil
from pathlib import Path

import pytest

from dstf.backends import backend_family, backend_names
from dstf.backends.openmodelica.mos_generator import classify_dependency
from dstf.config import (
    Config,
    ConfigError,
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

    def test_a_library_beside_an_ancestor_is_not_adopted(self, tmp_path):
        """Not a sibling, and not a child of an ancestor: from `a/b/c`, the
        library `a/Other` is somebody else's."""
        _make_lib(tmp_path / "a", "Other")
        start = tmp_path / "a" / "b" / "c"
        start.mkdir(parents=True)
        with pytest.raises(FileNotFoundError, match="Could not find a Modelica"):
            find_package_dir(start)
        # Nor from the directory beside it.
        with pytest.raises(FileNotFoundError):
            find_package_dir(tmp_path / "a" / "b")

    def test_from_inside_a_library_it_is_the_ancestor_that_is_a_package(self, tmp_path):
        """From `Lib/Resources/Data` the library is `Lib` — not `Lib/Sub`,
        the sub-package that is a child of that ancestor."""
        lib = _make_lib(tmp_path, "Lib")
        (lib / "Sub").mkdir()
        (lib / "Sub" / "package.mo").write_text("within Lib;\npackage Sub end Sub;")
        start = lib / "Resources" / "Data"
        start.mkdir(parents=True)
        assert find_package_dir(start) == lib.resolve()

    def test_the_library_s_parent_finds_the_library_in_it(self, tmp_path):
        """The one case a child is taken: the start directory is the
        library's parent, where its testing.json is kept. Of two, the first
        by name — not whichever the directory listing gives first."""
        for name in ("Zeta", "Alpha", "Mid"):
            _make_lib(tmp_path, name)
        (tmp_path / "Aaa").mkdir()  # no package.mo: not a library
        assert find_package_dir(tmp_path) == (tmp_path / "Alpha").resolve()

    def test_a_source_path_that_is_no_package_is_not_the_library_beside_it(
        self, tmp_path
    ):
        """`discover --source-path <a Python library>` listed the Modelica
        tests of the directory BESIDE it."""
        _make_lib(tmp_path, "MoLib")
        (tmp_path / "PyLib").mkdir()
        with pytest.raises(ConfigError, match="Could not find a Modelica package"):
            Config(source_path=tmp_path / "PyLib", write_default=False)

    def test_nothing_beside_the_way_up_is_looked_at(self, tmp_path, monkeypatch):
        """Not adopted, and not asked about either: a directory beside one
        above — a private directory of the system's temporary one, from
        anywhere below that — may be one the system will not let us look
        into."""
        _make_lib(tmp_path / "a", "Other")
        start = tmp_path / "a" / "b" / "c"
        (start / "In").mkdir(parents=True)
        asked: list[Path] = []
        for name in ("exists", "is_dir", "is_file", "iterdir", "stat"):
            real = getattr(Path, name)

            def spy(self, *args, _real=real, **kwargs):
                asked.append(Path(self))
                return _real(self, *args, **kwargs)

            monkeypatch.setattr(Path, name, spy)
        with pytest.raises(FileNotFoundError):
            find_package_dir(start)
        monkeypatch.undo()
        way_up = [start.resolve(), *start.resolve().parents]
        allowed = {
            *way_up,
            *(d / "package.mo" for d in way_up),
            start.resolve() / "In",
            start.resolve() / "In" / "package.mo",
        }
        assert asked and set(asked) <= allowed, sorted(set(asked) - allowed)

    def test_a_directory_above_that_cannot_be_asked_is_skipped(
        self, tmp_path, monkeypatch
    ):
        lib = _make_lib(tmp_path, "Lib")
        start = lib / "Resources" / "Data"
        start.mkdir(parents=True)
        real = Path.exists

        def exists(self, *args, **kwargs):
            if self == (lib / "Resources" / "package.mo").resolve():
                raise PermissionError(13, "Permission denied", str(self))
            return real(self, *args, **kwargs)

        monkeypatch.setattr(Path, "exists", exists)
        assert find_package_dir(start) == lib.resolve()

    def test_a_directory_in_it_that_cannot_be_read_is_skipped(self, tmp_path):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            pytest.skip("running as root — chmod 0 does not block reads")
        locked = tmp_path / "Aaa"
        locked.mkdir()
        lib = _make_lib(tmp_path, "Lib")
        locked.chmod(0)
        try:
            assert find_package_dir(tmp_path) == lib.resolve()
        finally:
            locked.chmod(0o755)  # let pytest clean tmp_path up

    def test_the_library_is_looked_for_from_the_working_directory(
        self, tmp_path, monkeypatch
    ):
        _make_lib(tmp_path / "a", "Other")
        (tmp_path / "a" / "empty").mkdir()
        monkeypatch.chdir(tmp_path / "a" / "empty")
        with pytest.raises(ConfigError, match="Could not find a Modelica package"):
            Config(write_default=False)

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
    """Which backend a project that names none runs with (`_pick_backend`),
    and its integration through Config.__post_init__ when testing.json omits
    the `backend` key."""

    def test_looks_like_path_linux(self):
        from dstf.backends.locate import looks_like_path

        assert looks_like_path("/usr/bin/omc")
        assert not looks_like_path("omc")
        assert not looks_like_path("dymola.exe")

    def test_looks_like_path_windows(self):
        from dstf.backends.locate import looks_like_path

        assert looks_like_path("C:\\Program Files\\Dymola.exe")
        assert looks_like_path("D:/some/path")
        assert not looks_like_path("Dymola")

    def test_auto_detect_picks_first_resolvable(self, tmp_path, monkeypatch):
        """First entry whose binary resolves wins. If an earlier entry has
        pinned absolute paths that don't exist, that entry is skipped (no
        fallback to a same-named binary on PATH)."""
        from dstf.config import _pick_backend

        # Create a fake "omc" binary on a temp PATH — simulate OM available.
        fake_omc = tmp_path / "omc"
        fake_omc.write_text("#!/bin/sh\n")
        fake_omc.chmod(0o755)
        monkeypatch.setenv("PATH", str(tmp_path))
        monkeypatch.delenv("OPENMODELICAHOME", raising=False)

        backends = {
            # pinned path, doesn't exist
            "Dymola": {"executables": ["C:\\nope\\dymola.exe"]},
            "OpenModelica": {"executables": ["omc"]},  # bare name → PATH lookup
        }
        assert _pick_backend(backends, "modelica") == "OpenModelica"

    def test_auto_detect_pinned_paths_block_fallback(self, tmp_path, monkeypatch):
        """If a backend's list holds pinned absolute paths and NONE exist, we
        must NOT fall back to a same-named binary on PATH — the pinned list
        expresses intent to use *that* install."""
        from dstf.backends.dymola.backend import DymolaBackend

        fake_dymola = tmp_path / "dymola"
        fake_dymola.write_text("#!/bin/sh\n")
        fake_dymola.chmod(0o755)
        monkeypatch.setenv("PATH", str(tmp_path))

        pinned = "C:\\Program Files\\Dymola\\Dymola.exe"  # pinned, missing
        located = DymolaBackend.locate({"executables": [pinned]})
        # Fallback suppressed ⇒ nothing resolves despite `dymola` on PATH.
        assert not located.found
        assert located.executable is None
        assert located.parts[0].detail == (
            f"the configured executables: {pinned} does not exist"
        )

    def test_where_nothing_resolves_the_first_entry_is_the_one_named(
        self, tmp_path, monkeypatch
    ):
        """Nothing found is not a switch to a backend the project did not
        list (it used to become "Dymola", whatever the map held): the first
        entry is selected, and it is the one the run says is missing."""
        from dstf.config import _pick_backend

        # Empty PATH and bogus paths — nothing resolves.
        monkeypatch.setenv("PATH", str(tmp_path))
        monkeypatch.delenv("OPENMODELICAHOME", raising=False)
        backends = {
            "OpenModelica": {"executables": ["/nope/omc"]},
            "Dymola": {"executables": ["C:\\nope\\dymola.exe"]},
        }
        assert _pick_backend(backends, "modelica") == "OpenModelica"

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
                        # pinned, missing
                        "Dymola": {"executables": ["C:\\nope\\dymola.exe"]},
                        # PATH lookup → hits fake
                        "OpenModelica": {"executables": ["omc"]},
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
                    "backends": {"Dymola": {"executables": ["C:\\nope\\dymola.exe"]}},
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
    as "the tool ignores my config", with no diagnostic anywhere. They are
    refused as any key nothing reads is (`config_shape.TESTING_JSON`), and
    so is the top-level `show_ide` that moved into Dymola's block.
    Without this test nothing in the tree fails if a rebase or cherry-pick
    restores an old-key read.
    """

    @pytest.mark.parametrize(
        "key", ["simulator", "simulators", "simulator_setup", "show_ide"]
    )
    def test_old_key_is_an_unknown_field(self, tmp_path, key):
        cfg = tmp_path / "testing.json"
        value = {} if key == "simulators" else "x"
        cfg.write_text(json.dumps({key: value}), encoding="utf-8")

        with pytest.raises(ConfigError) as exc:
            load_config_file(cfg)

        assert str(exc.value).startswith(f'{cfg}: unknown field "{key}"')

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
        from dstf.backends.dymola import interface_loader

        monkeypatch.chdir(tmp_path)
        # A machine with no simulator on it, whatever this one has.
        monkeypatch.setenv("PATH", str(tmp_path))
        monkeypatch.delenv("OPENMODELICAHOME", raising=False)
        monkeypatch.setattr(interface_loader, "platform_glob_bases", lambda: [])
        lib = _make_lib(tmp_path)
        cfg = tmp_path / "testing.json"
        cfg.write_text("{}", encoding="utf-8")
        config = Config(source_path=lib)
        assert cfg.read_text(encoding="utf-8") == "{}"
        # Nothing named, nothing found: the first backend that runs Modelica.
        assert config.backend_name == "Dymola"
        assert config.backend_path is None


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


class TestTheProjectToleranceIsOneFiniteNumber:
    """``tolerance`` in testing.json, and ``--tolerance``, is the project's
    base tolerance: one finite number. Anything else is the config error
    every other bad testing.json value is — never a traceback, and never
    ``"0.5"`` or ``true`` read as a number."""

    RULE = "the base tolerance must be a number"

    def _said(self, where: str, value: object) -> str:
        """The value as JSON holds it, cut at 60 characters; a dict alone
        is told where a pair belongs."""
        shown = json.dumps(value)
        if len(shown) > 60:
            shown = shown[:60] + "…"
        pair = "; a lower/upper pair belongs on a criterion"
        return (
            f"{self.RULE}{pair if isinstance(value, dict) else ''} ({where} is {shown})"
        )

    def _write(self, tmp_path: Path, raw: str) -> Path:
        cfg_dir = tmp_path / "proj"
        _make_lib(cfg_dir)
        text = json.dumps({"source_path": "Lib", "backend": "Dymola", "tolerance": 0})
        path = cfg_dir / "testing.json"
        path.write_text(text.replace('"tolerance": 0', f'"tolerance": {raw}'))
        return path

    @pytest.mark.parametrize(
        "raw",
        [
            '{"lower": 0.1, "upper": 0.2}',
            "{}",
            '"abc"',
            '"0.5"',
            "[1]",
            "true",
            "1" + "0" * 400,
            "NaN",
            "Infinity",
            "-Infinity",
        ],
    )
    def test_anything_else_in_the_file_is_a_config_error(self, tmp_path, raw):
        path = self._write(tmp_path, raw)
        with pytest.raises(ConfigError) as exc:
            Config(config_file=str(path))
        assert str(exc.value) == self._said("testing.json tolerance", json.loads(raw))

    @pytest.mark.parametrize("raw,value", [("null", 1e-4), ("0", 0.0), ("-1", -1.0)])
    def test_null_is_the_default_and_a_number_is_itself(self, tmp_path, raw, value):
        config = Config(config_file=str(self._write(tmp_path, raw)))
        assert config.tolerance == value and type(config.tolerance) is float

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, "0.5"], ids=repr)
    def test_an_explicit_one_is_judged_too(self, tmp_path, bad):
        path = self._write(tmp_path, "0.5")
        with pytest.raises(ConfigError) as exc:
            Config(config_file=str(path), tolerance=bad)
        assert str(exc.value) == self._said("--tolerance", bad)

    @pytest.mark.parametrize(
        "file_raw,flag,said",
        [
            ('"0.5"', [], 'testing.json tolerance is "0.5"'),
            ("0.5", ["--tolerance", "nan"], "--tolerance is NaN"),
        ],
    )
    def test_the_cli_says_it_in_one_line_and_exits_2(
        self, tmp_path, capsys, monkeypatch, file_raw, flag, said
    ):
        from dstf.cli import main

        monkeypatch.chdir(tmp_path)
        path = self._write(tmp_path, file_raw)
        work = tmp_path / "work"
        assert (
            main(["--config", str(path), "compare", *flag, "--work-dir", str(work)])
            == 2
        )
        err = capsys.readouterr().err
        assert f"Config error: {self.RULE} ({said})" in err
        assert "Traceback" not in err

    def test_the_tool_refuses_to_open_it(self, tmp_path):
        from dstf.authoring.session import Session

        path = self._write(tmp_path, "[1]")
        with pytest.raises(ConfigError, match=r"testing\.json tolerance is \[1\]"):
            Session().open(path, work_dir=tmp_path / "work")


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
            "backends": {"OpenModelica": {"executables": ["omc"]}},  # no "backend" key
        }
        (cfg_dir / "testing.json").write_text(json.dumps(cfg), encoding="utf-8")
        return cfg_dir

    def test_cli_backend_path_survives_autodetect(self, tmp_path, monkeypatch):
        """--backend-path without --backend: auto-detect may fill the
        simulator NAME, but the explicit path must win."""
        cfg_dir = self._autodetect_setup(tmp_path, monkeypatch)
        custom = tmp_path / "custom" / "omc"
        custom.parent.mkdir()
        custom.write_text("#!/bin/sh\n")
        custom.chmod(0o755)
        config = Config(
            config_file=str(cfg_dir / "testing.json"),
            backend_path=str(custom),
        )
        assert config.backend_name == "OpenModelica"
        assert config.backend_path == str(custom)

    def test_a_cli_backend_path_that_is_gone_is_not_the_autodetected_one(
        self, tmp_path, monkeypatch
    ):
        cfg_dir = self._autodetect_setup(tmp_path, monkeypatch)
        config = Config(
            config_file=str(cfg_dir / "testing.json"),
            backend_path="/my/custom/omc",
        )
        assert config.backend_name == "OpenModelica"
        assert config.backend_path is None

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
    assert "Python" in backend_names()
    assert backend_family("Python 3.12") == "Python"
    assert backend_family("Python") == "Python"


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
    backends to switch to (brief §3.6): the map is kept whole, a settings
    block per entry."""
    (tmp_path / "testing.json").write_text(
        json.dumps(
            {
                "source_type": "python",
                "source_path": ".",
                "library_name": "L",
                "backend": "Python",
                "backends": {
                    "Python": {"executables": ["python3"]},
                    "Dymola 2026x": {
                        "executables": [
                            "C:/Program Files/Dymola 2026x/bin64/Dymola.exe"
                        ],
                        "show_ide": True,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    config = Config(config_file=tmp_path / "testing.json")
    assert config.backends == {
        "Python": {"executables": ["python3"]},
        "Dymola 2026x": {
            "executables": ["C:/Program Files/Dymola 2026x/bin64/Dymola.exe"],
            "show_ide": True,
        },
    }


class TestTheme:
    """``theme`` in testing.json names a bundled theme (D148): a name, never
    a path or a table of colours, and any other value is the config error
    every other bad testing.json value is."""

    def _config(self, tmp_path: Path, raw: str | None) -> Path:
        cfg = {"source_type": "python", "source_path": ".", "backend": "Python"}
        text = json.dumps({**cfg, "theme": 0} if raw is not None else cfg)
        path = tmp_path / "testing.json"
        path.write_text(text.replace('"theme": 0', f'"theme": {raw}'), encoding="utf-8")
        return path

    def test_the_default_is_default(self, tmp_path):
        assert Config(config_file=self._config(tmp_path, None)).theme == "default"

    @pytest.mark.parametrize("name", ["default", "dark"])
    def test_a_bundled_name_is_read(self, tmp_path, name):
        path = self._config(tmp_path, json.dumps(name))
        assert Config(config_file=path).theme == name

    @pytest.mark.parametrize(
        "raw",
        ['"solarized"', '"Dark"', '""', '"themes/mine.css"', "null", "1", "[]", "{}"],
    )
    def test_anything_else_is_a_config_error_naming_the_bundled_ones(
        self, tmp_path, raw
    ):
        with pytest.raises(ConfigError) as exc:
            Config(config_file=self._config(tmp_path, raw))
        assert str(exc.value) == (
            f"testing.json theme is {raw}: the bundled themes are default, dark"
        )

    def test_one_set_in_code_is_judged_too(self, tmp_path):
        with pytest.raises(ConfigError, match="the bundled themes are default, dark"):
            Config(config_file=self._config(tmp_path, None), theme="solarized")

    def test_the_cli_says_it_in_one_line_and_exits_2(
        self, tmp_path, capsys, monkeypatch
    ):
        from dstf.cli import main

        monkeypatch.chdir(tmp_path)
        path = self._config(tmp_path, '"solarized"')
        work = tmp_path / "work"
        assert main(["--config", str(path), "compare", "--work-dir", str(work)]) == 2
        err = capsys.readouterr().err
        assert 'Config error: testing.json theme is "solarized"' in err
        assert "Traceback" not in err

    def test_there_is_no_flag_for_it(self):
        """The theme is the project's, and the tab's in the tool: a run
        takes it from testing.json."""
        from dstf.cli.main import build_arg_parser

        assert "--theme" not in build_arg_parser().format_help()
