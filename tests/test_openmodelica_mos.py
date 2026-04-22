"""Pure unit tests for OpenModelica .mos script generation."""

from pathlib import Path

from modelica_testing.discovery.test_registry import TestModel
from modelica_testing.simulators.openmodelica.mos_generator import (
    build_simulate_mos,
    classify_dependency,
    build_variable_filter,
)


def _make_test(**overrides) -> TestModel:
    defaults = dict(
        model_id="Demo.Example.A",
        source_file=Path(""),
        source_package="Demo.Example",
        short_name="A",
        n_vars=0,
        variable_patterns=["x", "y.z[1]"],
        stop_time=10.0,
        tolerance=1e-6,
        method="dassl",
        number_of_intervals=500,
    )
    defaults.update(overrides)
    return TestModel(**defaults)


class TestClassifyDependency:
    def test_bare_library_name(self):
        assert classify_dependency("Modelica") == ("loadModel", "Modelica")

    def test_bare_dotted_name(self):
        assert classify_dependency("Modelica.Blocks") == ("loadModel", "Modelica.Blocks")

    def test_path_with_slash(self):
        kind, arg = classify_dependency("/abs/path/to/Lib")
        assert kind == "loadFile"
        assert arg.endswith("package.mo")
        assert arg.startswith("/abs/path/to/Lib")

    def test_path_ending_in_mo(self):
        kind, arg = classify_dependency("/a/b/package.mo")
        assert kind == "loadFile"
        assert arg == "/a/b/package.mo"

    def test_windows_style_path(self):
        kind, arg = classify_dependency("C:\\Libs\\Foo")
        assert kind == "loadFile"
        # normalized path ends with package.mo
        assert arg.endswith("package.mo")


class TestBuildVariableFilter:
    def test_includes_time_and_diagnostics(self):
        regex = build_variable_filter(
            patterns=["x", "y"],
            diagnostic_vars=["CPUtime", "EventCounter"],
        )
        # Must match time, the requested vars, and the diagnostics.
        import re
        pat = re.compile(regex)
        assert pat.fullmatch("time")
        assert pat.fullmatch("x")
        assert pat.fullmatch("y")
        assert pat.fullmatch("CPUtime")
        assert pat.fullmatch("EventCounter")
        # Must NOT match unrelated.
        assert not pat.fullmatch("unrelated_var")

    def test_escapes_regex_metacharacters(self):
        """Names like 'pipe.T[1]' contain regex metacharacters — must be escaped."""
        regex = build_variable_filter(patterns=["pipe.T[1]"], diagnostic_vars=[])
        import re
        pat = re.compile(regex)
        assert pat.fullmatch("pipe.T[1]")
        # The '.' must be literal, not "any char".
        assert not pat.fullmatch("pipeXT[1]")

    def test_glob_star_expands_to_regex(self):
        """Pattern 'pipe.T*' must match 'pipe.T[1]', 'pipe.Tabc', etc."""
        regex = build_variable_filter(patterns=["pipe.T*"], diagnostic_vars=[])
        import re
        pat = re.compile(regex)
        assert pat.fullmatch("pipe.T[1]")
        assert pat.fullmatch("pipe.Tabc")
        assert not pat.fullmatch("pipeXT[1]")  # '.' still literal

    def test_anchored(self):
        """Filter must be fully anchored so OM's partial-match semantics
        don't over-match (pattern 'x' shouldn't hit 'phi' or 'x_der')."""
        regex = build_variable_filter(patterns=["x"], diagnostic_vars=[])
        assert regex.startswith("^(")
        assert regex.endswith(")$")

    def test_empty_patterns_produces_time_only_matcher(self):
        regex = build_variable_filter(patterns=[], diagnostic_vars=[])
        import re
        pat = re.compile(regex)
        assert pat.fullmatch("time")
        assert not pat.fullmatch("anything_else")


class TestBuildSimulateMos:
    def test_includes_std_version_option(self):
        mos = build_simulate_mos(
            test=_make_test(),
            test_dir=Path("/tmp/test_0001"),
            library_package_mo=Path("/lib/package.mo"),
            dependencies=[],
            simulator_setup=[],
            diagnostic_vars=[],
            std_version="latest",
        )
        assert 'setCommandLineOptions("--std=latest")' in mos

    def test_loads_bare_library_name_before_loadfile_deps(self):
        mos = build_simulate_mos(
            test=_make_test(),
            test_dir=Path("/tmp/test_0001"),
            library_package_mo=Path("/lib/package.mo"),
            dependencies=["Modelica", "/other/Lib"],
            simulator_setup=[],
            diagnostic_vars=[],
        )
        load_model_line = mos.index("loadModel(Modelica)")
        load_file_line = mos.index('loadFile("/other/Lib/package.mo")')
        load_main_line = mos.index('loadFile("/lib/package.mo")')
        assert load_model_line < load_file_line < load_main_line

    def test_simulator_setup_between_loads_and_cd(self):
        mos = build_simulate_mos(
            test=_make_test(),
            test_dir=Path("/tmp/test_0001"),
            library_package_mo=Path("/lib/package.mo"),
            dependencies=[],
            simulator_setup=["setDebugFlags(\"foo\")"],
            diagnostic_vars=[],
        )
        setup_pos = mos.index('setDebugFlags("foo")')
        cd_pos = mos.index('cd("/tmp/test_0001")')
        load_pos = mos.index('loadFile("/lib/package.mo")')
        assert load_pos < setup_pos < cd_pos

    def test_simulate_call_fields(self):
        mos = build_simulate_mos(
            test=_make_test(stop_time=42.0, tolerance=1e-9, method="euler"),
            test_dir=Path("/tmp/test_0001"),
            library_package_mo=Path("/lib/package.mo"),
            dependencies=[],
            simulator_setup=[],
            diagnostic_vars=[],
        )
        assert "simulate(Demo.Example.A" in mos
        assert "stopTime=42.0" in mos
        assert "tolerance=1e-09" in mos or "tolerance=1e-9" in mos
        assert 'method="euler"' in mos
        assert 'outputFormat="mat"' in mos
        assert 'fileNamePrefix="result"' in mos
        assert 'variableFilter="' in mos

    def test_sentinel_timing_block_present(self):
        mos = build_simulate_mos(
            test=_make_test(),
            test_dir=Path("/tmp/test_0001"),
            library_package_mo=Path("/lib/package.mo"),
            dependencies=[],
            simulator_setup=[],
            diagnostic_vars=[],
        )
        assert "<<<MT_PHASE_TIMINGS>>>" in mos
        assert "<<<MT_PHASE_TIMINGS_END>>>" in mos
        for field in ("timeFrontend", "timeBackend", "timeSimCode",
                      "timeTemplates", "timeCompile", "timeSimulation",
                      "timeTotal", "resultFile", "messages"):
            assert field in mos, f"missing timing field {field} in .mos"

    def test_test_dir_uses_forward_slashes(self):
        """Even on Windows paths, the emitted cd() should use forward slashes."""
        mos = build_simulate_mos(
            test=_make_test(),
            test_dir=Path("C:/work/test_0001"),
            library_package_mo=Path("C:/lib/package.mo"),
            dependencies=[],
            simulator_setup=[],
            diagnostic_vars=[],
        )
        assert 'cd("C:/work/test_0001")' in mos
        assert 'loadFile("C:/lib/package.mo")' in mos

    def test_empty_dependencies(self):
        mos = build_simulate_mos(
            test=_make_test(),
            test_dir=Path("/tmp/test_0001"),
            library_package_mo=Path("/lib/package.mo"),
            dependencies=[],
            simulator_setup=[],
            diagnostic_vars=[],
        )
        assert "loadModel(" not in mos
        assert mos.count("loadFile(") == 1  # only the main library
