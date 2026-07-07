"""Tests for RunMetadata — run-level provenance (backend/simulator/version)."""

from types import SimpleNamespace

from dstf.simulators.run_metadata import RunMetadata


def _stub_config(simulator="Dymola 2026x", backend="Dymola", os_name="linux"):
    """Duck-typed Config: from_config only reads these three members."""
    return SimpleNamespace(
        simulator=simulator,
        simulator_backend=backend,
        os_name=os_name,
    )


class TestFromConfig:
    def test_copies_backend_simulator_os(self):
        meta = RunMetadata.from_config(_stub_config(), now=1000.0)
        assert meta.backend == "Dymola"
        assert meta.simulator == "Dymola 2026x"  # user-configured label preserved
        assert meta.os == "linux"

    def test_injectable_timestamp_is_deterministic(self):
        meta = RunMetadata.from_config(_stub_config(), now=1234.5)
        assert meta.generated_at == 1234.5

    def test_tool_version_defaults_none_and_is_settable(self):
        assert RunMetadata.from_config(_stub_config()).tool_version is None
        meta = RunMetadata.from_config(_stub_config(), tool_version="Dymola 2025x R1")
        assert meta.tool_version == "Dymola 2025x R1"

    def test_dstf_version_is_populated(self):
        # importlib.metadata resolves the installed editable package version.
        meta = RunMetadata.from_config(_stub_config())
        assert meta.dstf_version  # non-empty string, never None
        assert isinstance(meta.dstf_version, str)

    def test_simulator_falls_back_to_backend_when_unset(self):
        cfg = _stub_config(simulator=None, backend="Dymola")
        meta = RunMetadata.from_config(cfg)
        assert meta.simulator == "Dymola"

    def test_os_falls_back_when_unset(self):
        cfg = _stub_config(os_name=None)
        assert RunMetadata.from_config(cfg).os == "unknown"


class TestAsDict:
    def test_roundtrips_all_fields(self):
        meta = RunMetadata(
            backend="Julia",
            simulator="Julia",
            os="linux",
            tool_version="1.11.2",
            dstf_version="0.1.0",
            generated_at=42.0,
            library_versions={"Modelica": "4.1.0"},
        )
        d = meta.as_dict()
        assert d == {
            "backend": "Julia",
            "simulator": "Julia",
            "os": "linux",
            "tool_version": "1.11.2",
            "dstf_version": "0.1.0",
            "generated_at": 42.0,
            "library_versions": {"Modelica": "4.1.0"},
        }

    def test_library_versions_defaults_none_and_flows_via_from_config(self):
        assert RunMetadata.from_config(_stub_config()).library_versions is None
        meta = RunMetadata.from_config(
            _stub_config(), library_versions={"Modelica": "4.0.0"}
        )
        assert meta.library_versions == {"Modelica": "4.0.0"}
        # empty dict normalizes to None (no library line rendered)
        assert (
            RunMetadata.from_config(
                _stub_config(), library_versions={}
            ).library_versions
            is None
        )

    def test_json_serializable(self):
        import json

        d = RunMetadata.from_config(_stub_config(), now=1.0).as_dict()
        # must survive the status.json round-trip
        assert json.loads(json.dumps(d))["backend"] == "Dymola"
