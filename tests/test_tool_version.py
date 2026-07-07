"""Tests for per-backend tool-version capture.

Live-simulator paths (real DymolaVersion() / omc getVersion()) can't run on
CI, so we test the parsing + orchestration seams with stubs, and the Python
backend end-to-end (its 'tool' is just an interpreter binary).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from dstf.simulators.base import PersistentRunnerBase

# ---------------------------------------------------------------------------
# Dymola worker version parsing (stubbed DymolaInterface)
# ---------------------------------------------------------------------------


class _FakeDymola:
    def __init__(self, value):
        self._value = value

    def ExecuteCommand(self, cmd):
        if isinstance(self._value, Exception):
            raise self._value
        assert cmd == "DymolaVersion()"
        return self._value


def _dymola_worker(value):
    from dstf.simulators.dymola.persistent_runner import DymolaWorker

    # Bypass __init__ (needs a real DymolaConfig + interface class); we only
    # exercise tool_version(), which reads self.dymola.
    w = DymolaWorker.__new__(DymolaWorker)
    w.dymola = _FakeDymola(value)
    return w


class TestDymolaWorkerVersion:
    def test_returns_version_string(self):
        assert _dymola_worker("Dymola 2026x Refresh 1").tool_version() == (
            "Dymola 2026x Refresh 1"
        )

    def test_strips_whitespace(self):
        assert _dymola_worker("  Dymola 2025x  ").tool_version() == "Dymola 2025x"

    def test_none_when_interface_absent(self):
        from dstf.simulators.dymola.persistent_runner import DymolaWorker

        w = DymolaWorker.__new__(DymolaWorker)
        w.dymola = None
        assert w.tool_version() is None

    def test_none_when_execute_raises(self):
        assert _dymola_worker(RuntimeError("boom")).tool_version() is None

    def test_none_when_non_string_result(self):
        assert _dymola_worker(True).tool_version() is None
        assert _dymola_worker("").tool_version() is None


# ---------------------------------------------------------------------------
# OpenModelica worker version parsing (stubbed OMC session)
# ---------------------------------------------------------------------------


class _FakeSession:
    def __init__(self, raw):
        self._raw = raw

    def sendExpression(self, expr, parsed=False):
        if isinstance(self._raw, Exception):
            raise self._raw
        assert expr == "getVersion()"
        return self._raw


def _om_worker(raw):
    from dstf.simulators.openmodelica.persistent_runner import OpenModelicaWorker

    w = OpenModelicaWorker.__new__(OpenModelicaWorker)
    w.session = _FakeSession(raw)
    return w


class TestOpenModelicaWorkerVersion:
    def test_unwraps_quoted_wire_string(self):
        # omc returns a quoted, newline-terminated string on the wire.
        assert _om_worker('"OpenModelica 1.27.0-dev"\n').tool_version() == (
            "OpenModelica 1.27.0-dev"
        )

    def test_none_when_session_absent(self):
        from dstf.simulators.openmodelica.persistent_runner import OpenModelicaWorker

        w = OpenModelicaWorker.__new__(OpenModelicaWorker)
        w.session = None
        assert w.tool_version() is None

    def test_none_when_send_raises(self):
        assert _om_worker(RuntimeError("zmq down")).tool_version() is None

    def test_none_when_empty(self):
        assert _om_worker('""\n').tool_version() is None


# ---------------------------------------------------------------------------
# Probe orchestration on PersistentRunnerBase / concrete runners
# ---------------------------------------------------------------------------


class _ConcretePersistent(PersistentRunnerBase):
    """Minimal concretization so ABC lets us __new__ an instance."""

    def read_result(self, test, test_key, run_result):  # pragma: no cover
        raise NotImplementedError


class TestProbeOrchestration:
    def test_base_probe_returns_none(self):
        base = _ConcretePersistent.__new__(_ConcretePersistent)
        assert base._probe_worker_version([SimpleNamespace()]) is None

    def test_safe_probe_empty_list(self):
        base = _ConcretePersistent.__new__(_ConcretePersistent)
        assert base._safe_probe_worker_version([]) is None

    def test_safe_probe_swallows_exceptions(self):
        class Boom(_ConcretePersistent):
            def _probe_worker_version(self, live_workers):
                raise RuntimeError("nope")

        b = Boom.__new__(Boom)
        assert b._safe_probe_worker_version([SimpleNamespace()]) is None

    def test_dymola_probe_first_non_none_wins(self):
        from dstf.simulators.dymola.persistent_runner import PersistentDymolaRunner

        runner = PersistentDymolaRunner.__new__(PersistentDymolaRunner)
        workers = [
            SimpleNamespace(tool_version=lambda: None),
            SimpleNamespace(tool_version=lambda: "Dymola 2026x"),
            SimpleNamespace(tool_version=lambda: "Dymola 2025x"),
        ]
        assert runner._probe_worker_version(workers) == "Dymola 2026x"

    def test_dymola_probe_none_when_all_none(self):
        from dstf.simulators.dymola.persistent_runner import PersistentDymolaRunner

        runner = PersistentDymolaRunner.__new__(PersistentDymolaRunner)
        workers = [SimpleNamespace(tool_version=lambda: None)]
        assert runner._probe_worker_version(workers) is None


# ---------------------------------------------------------------------------
# Python backend end-to-end (real interpreter --version)
# ---------------------------------------------------------------------------


class TestPythonRunnerVersion:
    def test_reports_interpreter_version(self, tmp_path):
        from dstf.config import Config
        from dstf.simulators.python.runner import PythonRunner

        lib = tmp_path / "MyPyLib"
        (lib / "Examples").mkdir(parents=True)
        (lib / "Examples" / "Foo.py").write_text(
            "def simulate(stop_time, tolerance):\n    return {}\n"
        )
        ref_root = lib / "Resources" / "ReferenceResults"
        ref_root.mkdir(parents=True)
        cfg_path = ref_root / "testing.json"
        cfg_path.write_text(
            '{"source_type": "python", "source_path": "../..", '
            '"library_name": "MyPyLib", "simulators": {"Python": ["python"]}, '
            '"simulator": "Python"}'
        )
        cfg = Config(config_file=cfg_path)
        version = PythonRunner(cfg).describe_tool_version()
        assert version is not None
        assert version.startswith("Python ")


class TestFmpyRunnerVersion:
    def test_reports_fmpy_version(self, tmp_path):
        pytest.importorskip("fmpy")
        from dstf.config import Config
        from dstf.simulators.fmpy.runner import FmpyRunner

        lib = tmp_path / "fmus"
        lib.mkdir()
        ref_root = lib / "Resources" / "ReferenceResults"
        ref_root.mkdir(parents=True)
        cfg_path = ref_root / "testing.json"
        cfg_path.write_text(
            '{"source_type": "fmu", "source_path": "../..", '
            '"library_name": "fmus", "simulators": {"FMPy": ["fmpy"]}, '
            '"simulator": "FMPy"}'
        )
        cfg = Config(config_file=cfg_path)
        version = FmpyRunner(cfg).describe_tool_version()
        assert version is not None
        assert version.startswith("FMPy ")
