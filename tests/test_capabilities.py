"""The capability contract: the registry is loud on a duplicate name, keeps
registration order, and a plug-in module registers through `dstf.plugins`
exactly as it registers a metric."""

import dataclasses
import importlib.metadata

import pytest

from dstf import plugins
from dstf.authoring import capabilities as caps
from dstf.authoring.capabilities import (
    Artifact,
    Capability,
    Job,
    JobContext,
    OpenProject,
    Patch,
    Scope,
    StoreWrite,
    register_capability,
    registered_capabilities,
)


@pytest.fixture(autouse=True)
def _clean():
    # Before AND after: cli/main.py calls load_plugins() in earlier test
    # modules, and its idempotence guard would otherwise skip our entry point.
    caps._reset_for_tests()
    plugins._reset_for_tests()
    yield
    caps._reset_for_tests()
    plugins._reset_for_tests()


class _Dummy:
    def __init__(self, name="dummy", scope=Scope.TESTS):
        self.name, self.label, self.scope = name, name.title(), scope

    def available(self, s):
        return True

    def panel(self, s, ui):
        pass

    def act(self, s, **inputs):
        return None


def test_registration_order_is_kept_and_a_duplicate_name_is_loud():
    a, b = _Dummy("a"), _Dummy("b")
    assert register_capability(a) is a
    register_capability(b)
    assert registered_capabilities() == (a, b)
    with pytest.raises(ValueError, match="capability 'a' is already registered"):
        register_capability(_Dummy("a"))


def test_the_protocol_is_checkable():
    assert isinstance(_Dummy(), Capability)
    assert not isinstance(object(), Capability)


def test_registering_a_non_capability_is_loud_at_registration():
    """plugins.py's rule: fail at startup naming what is wrong, not when
    the user clicks the tab."""

    class _Half:
        name, label, scope = "half", "Half", Scope.TESTS

        def available(self, s):
            return True

        def panel(self, s, ui):
            pass

    with pytest.raises(TypeError, match="missing act"):
        register_capability(_Half())  # type: ignore[arg-type]
    assert registered_capabilities() == ()


def test_outcomes_take_keywords_only():
    """`models` was inserted between the spec's positional fields; a caller
    following the spec's order would silently swap it with reloads_results."""
    with pytest.raises(TypeError):
        Job(lambda ctx: None, "rerun", frozenset())  # type: ignore[misc]


def test_a_plugin_registers_through_the_entry_point_group(monkeypatch):
    """The entry point's load() returns a callable that registers — the
    registration happens INSIDE load_plugins(), so a load_plugins that never
    reaches the entry point fails this test."""

    def register_mine():
        register_capability(_Dummy("from-plugin", Scope.LEAF))

    class _EP:
        name = "fake"

        def load(self):
            return register_mine

    assert not any(c.name == "from-plugin" for c in registered_capabilities())

    def fake_entry_points(*, group=None):
        return [_EP()] if group == plugins.PLUGIN_GROUP else []

    monkeypatch.setattr(importlib.metadata, "entry_points", fake_entry_points)
    plugins.load_plugins()
    assert any(c.name == "from-plugin" for c in registered_capabilities())


def test_outcomes_are_frozen_and_carry_their_models():
    sw = StoreWrite(run=lambda store: None, models=frozenset({"M"}))
    assert sw.models == frozenset({"M"})
    job = Job(run=lambda ctx: None, label="rerun", models=frozenset({"M"}))
    assert job.reloads_results is True
    with pytest.raises(dataclasses.FrozenInstanceError):
        job.label = "x"
    assert Patch(ops=[]).ops == [] and Artifact(paths=[], label="png").label == "png"
    assert (
        OpenProject(config_path=__import__("pathlib").Path("x")).config_path.name == "x"
    )
    assert JobContext.__dataclass_fields__.keys() >= {"project", "models"}


def test_the_session_imports_without_the_ui_extra():
    """A capability module reaches NiceGUI only inside `panel`: importing
    the package registers the builtins, and that must work where the
    [author] extra is not installed. The `None` entry makes any `import
    nicegui` raise."""
    import subprocess
    import sys

    code = (
        "import sys; sys.modules['nicegui'] = None; "
        "import dstf.authoring.session, dstf.authoring.builtin"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
