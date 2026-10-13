"""Pure unit tests for OpenModelica .mos script generation."""

from pathlib import Path

import pytest

from dstf.backends.openmodelica.mos_generator import (
    build_simulate_args,
    build_simulate_mos,
    build_variable_filter,
    classify_dependency,
)
from dstf.discovery.test_registry import TestModel


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
        assert classify_dependency("Modelica.Blocks") == (
            "loadModel",
            "Modelica.Blocks",
        )

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


class TestBuildSimulateArgs:
    """The UnitTests component's variables are not in the spec's patterns,
    so the filter has to name them — and it asks the one naming rule, which
    knows the component's instance."""

    def _filter_arg(self, test) -> str:
        (arg,) = [
            a for a in build_simulate_args(test, []) if a.startswith("variableFilter")
        ]
        return arg

    def test_the_filter_names_the_components_variables(self):
        import re

        test = _make_test(n_vars=2, variable_patterns=[], source="unit_tests")
        regex = self._filter_arg(test).split("=", 1)[1].strip('"').replace("\\\\", "\\")
        pat = re.compile(regex)
        assert pat.fullmatch("unitTests.x[1]")
        assert pat.fullmatch("unitTests.x[2]")

    def test_the_filter_uses_the_components_own_instance(self):
        import re

        test = _make_test(
            n_vars=2,
            variable_patterns=[],
            source="unit_tests",
            unit_tests_instance="ut",
        )
        regex = self._filter_arg(test).split("=", 1)[1].strip('"').replace("\\\\", "\\")
        pat = re.compile(regex)
        assert pat.fullmatch("ut.x[1]")
        assert pat.fullmatch("ut.x[2]")
        assert not pat.fullmatch("unitTests.x[1]")

    def test_a_spec_sourced_test_adds_nothing(self):
        import re

        test = _make_test(n_vars=0, variable_patterns=["x"], source="spec")
        regex = self._filter_arg(test).split("=", 1)[1].strip('"').replace("\\\\", "\\")
        pat = re.compile(regex)
        assert pat.fullmatch("x")
        assert not pat.fullmatch("unitTests.x[1]")


class TestBuildSimulateMos:
    def test_includes_std_version_option(self):
        mos = build_simulate_mos(
            test=_make_test(),
            test_dir=Path("/tmp/test_0001"),
            library_package_mo=Path("/lib/package.mo"),
            dependencies=[],
            backend_setup=[],
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
            backend_setup=[],
            diagnostic_vars=[],
        )
        load_model_line = mos.index("loadModel(Modelica)")
        load_file_line = mos.index('loadFile("/other/Lib/package.mo")')
        load_main_line = mos.index('loadFile("/lib/package.mo")')
        assert load_model_line < load_file_line < load_main_line

    def test_backend_setup_between_loads_and_cd(self):
        mos = build_simulate_mos(
            test=_make_test(),
            test_dir=Path("/tmp/test_0001"),
            library_package_mo=Path("/lib/package.mo"),
            dependencies=[],
            backend_setup=['setDebugFlags("foo")'],
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
            backend_setup=[],
            diagnostic_vars=[],
        )
        assert "simulate(Demo.Example.A" in mos
        assert "stopTime=42.0" in mos
        assert "tolerance=1e-09" in mos or "tolerance=1e-9" in mos
        assert 'method="euler"' in mos
        assert 'outputFormat="mat"' in mos
        assert 'fileNamePrefix="result"' in mos
        assert 'variableFilter="' in mos

    def test_no_sentinel_or_print_block(self):
        """The print+sentinel dance was removed — timings come from omc's
        REPL-echoed SimulationResult record, not explicit prints. This test
        guards against accidentally re-introducing them."""
        mos = build_simulate_mos(
            test=_make_test(),
            test_dir=Path("/tmp/test_0001"),
            library_package_mo=Path("/lib/package.mo"),
            dependencies=[],
            backend_setup=[],
            diagnostic_vars=[],
        )
        assert "<<<" not in mos
        assert "print(" not in mos
        # Bare simulate(...) — no assignment to `res`.
        assert "res :=" not in mos
        assert "simulate(Demo.Example.A" in mos

    def test_test_dir_uses_forward_slashes(self):
        """Even on Windows paths, the emitted cd() should use forward slashes."""
        mos = build_simulate_mos(
            test=_make_test(),
            test_dir=Path("C:/work/test_0001"),
            library_package_mo=Path("C:/lib/package.mo"),
            dependencies=[],
            backend_setup=[],
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
            backend_setup=[],
            diagnostic_vars=[],
        )
        assert "loadModel(" not in mos
        assert mos.count("loadFile(") == 1  # only the main library


class TestTheFilterNamesTheCriteriasVariables:
    def test_a_criterion_variable_passes_the_filter_diagnostic_once(self):
        import re

        from dstf.comparison.tree_spec import parse_metric_tree

        test = _make_test(
            n_vars=1,
            variable_patterns=["x"],
            source="both",
            metric_tree_spec=parse_metric_tree(
                {
                    "combinator": "and",
                    "children": [
                        {"metric": "nrmse", "variable": "pipe.T[1]"},
                        {
                            "metric": "scalar",
                            "variable": "CPUtime",
                            "quantity": {"reduce": "max"},
                        },
                    ],
                }
            ),
        )
        (arg,) = [
            a
            for a in build_simulate_args(test, ["CPUtime"])
            if a.startswith("variableFilter")
        ]
        regex = arg.split("=", 1)[1].strip('"').replace("\\\\", "\\")
        pat = re.compile(regex)
        assert pat.fullmatch("pipe.T[1]")
        assert pat.fullmatch("unitTests.x[1]")
        assert not pat.fullmatch("pipe.T[2]")
        assert regex.count("CPUtime") == 1


class TestMatSync:
    """``-mat_sync=1`` makes the simulation executable write each output row
    to the result file as it is produced, so a run can be watched. It costs
    run time, so it is a run option only a caller sets
    (``Config.live_results``): off, the simulate call is the one a CLI run
    has always made."""

    #: `build_simulate_args` for `_make_test()` as it was before the option
    #: existed, verbatim.
    WITHOUT = [
        "stopTime=10.0",
        "numberOfIntervals=500",
        "tolerance=1e-06",
        'method="dassl"',
        'outputFormat="mat"',
        'fileNamePrefix="result"',
        'variableFilter="^(time|x|y\\\\.z\\\\[1\\\\]|CPUtime)$"',
    ]

    def test_off_the_arguments_are_what_they_were(self):
        test = _make_test()
        assert build_simulate_args(test, ["CPUtime"]) == self.WITHOUT
        assert build_simulate_args(test, ["CPUtime"], mat_sync=False) == self.WITHOUT

    def test_on_one_argument_is_added_and_nothing_else_changes(self):
        on = build_simulate_args(_make_test(), ["CPUtime"], mat_sync=True)
        assert on == [*self.WITHOUT, 'simflags="-mat_sync=1"']

    def _mos(self, **kw) -> str:
        return build_simulate_mos(
            test=_make_test(),
            test_dir=Path("/w/test_0001"),
            library_package_mo=Path("/lib/package.mo"),
            dependencies=["Modelica"],
            backend_setup=[],
            diagnostic_vars=["CPUtime"],
            **kw,
        )

    def test_the_script_carries_it_only_when_asked(self):
        off = self._mos()
        assert off == self._mos(mat_sync=False)
        assert "simflags" not in off and "mat_sync" not in off
        (call,) = [ln for ln in off.splitlines() if ln.startswith("simulate(")]
        assert call == f"simulate(Demo.Example.A, {', '.join(self.WITHOUT)});"
        on = self._mos(mat_sync=True)
        assert on == off.replace(
            call, call[: -len(");")] + ', simflags="-mat_sync=1");'
        )

    def test_only_a_caller_turns_it_on(self, tmp_path):
        """Not ``testing.json`` and not the command line: neither has a key
        or a flag for it, and a ``dstf run`` builds a Config with it off."""
        import json

        from dstf.backends.openmodelica.backend import OpenModelicaConfig
        from dstf.cli.common import _build_config
        from dstf.cli.main import build_arg_parser
        from dstf.config import Config, ConfigError

        lib = tmp_path / "Lib"
        lib.mkdir()
        (lib / "package.mo").write_text("package Lib end Lib;\n")
        argv = ["--config", str(lib / "testing.json"), "--source-path", str(lib)]
        for key in ("live_results", "mat_sync"):
            (lib / "testing.json").write_text(
                json.dumps({"backend": "OpenModelica", key: True})
            )
            with pytest.raises(ConfigError, match=f'unknown field "{key}"'):
                _build_config(build_arg_parser().parse_args([*argv, "run"]))
        (lib / "testing.json").write_text(json.dumps({"backend": "OpenModelica"}))
        config = _build_config(build_arg_parser().parse_args([*argv, "run"]))
        assert config.live_results is False
        assert OpenModelicaConfig.from_config(config).mat_sync is False
        every_option = build_arg_parser().format_help()
        for sub in ("run", "compare"):
            with pytest.raises(SystemExit):
                build_arg_parser().parse_args([*argv, sub, "--live-results"])
            with pytest.raises(SystemExit):
                build_arg_parser().parse_args([*argv, sub, "--mat-sync"])
        assert "live" not in every_option.lower()

        watched = Config(
            source_path=lib, backend_name="OpenModelica", live_results=True
        )
        assert OpenModelicaConfig.from_config(watched).mat_sync is True

    def test_the_batch_backend_writes_it_into_the_script(self, tmp_path, monkeypatch):
        from subprocess import CompletedProcess

        from dstf.backends.openmodelica.backend import OpenModelicaBackend
        from dstf.config import Config

        (tmp_path / "package.mo").write_text("package Lib end Lib;")
        omc = tmp_path / "omc"
        omc.write_text("#!/bin/sh\n")
        calls = {}
        for live in (False, True):
            cfg = Config(
                source_path=tmp_path,
                reference_root=tmp_path / "refs",
                backend_name="OpenModelica",
                backend_path=str(omc),
                work_dir=tmp_path / f"work_{live}",
                live_results=live,
            )
            monkeypatch.setattr(
                "dstf.backends.openmodelica.backend.subprocess.run",
                lambda cmd, **kw: CompletedProcess(cmd, 1, stdout="", stderr=""),
            )
            OpenModelicaBackend(cfg).run_single_test(
                _make_test(), test_key="test_0001", index=1, total=1
            )
            mos = (cfg.work_dir / "test_0001" / "simulate.mos").read_text()
            (calls[live],) = [
                ln for ln in mos.splitlines() if ln.startswith("simulate(")
            ]
        assert "simflags" not in calls[False]
        assert calls[True] == calls[False][: -len(");")] + ', simflags="-mat_sync=1");'
