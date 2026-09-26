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
    Placement,
    StoreWrite,
    plural,
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
    def __init__(self, name="dummy", placements=frozenset({Placement.SUITE})):
        self.name, self.label, self.placements = name, name.title(), placements

    def available(self, s):
        return True

    def caption(self, s):
        return f"{self.label} {len(s.selection.tests)}"

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
    the user reaches the page that would draw the capability."""

    class _Half:
        name, label = "half", "Half"
        placements = frozenset({Placement.SUITE})

        def available(self, s):
            return True

        def panel(self, s, ui):
            pass

    with pytest.raises(TypeError, match="missing caption, act"):
        register_capability(_Half())  # type: ignore[arg-type]
    assert registered_capabilities() == ()


def test_a_capability_declares_a_non_empty_frozenset_of_placements():
    """The shell draws a capability at every placement it names — a button
    at SUITE, TEST and VARIABLE, the panel itself at CRITERION and NODE; a
    capability naming none would register and never appear, and a plain set
    would let a caller mutate a registered capability's placements."""

    class _Nowhere(_Dummy):
        def __init__(self):
            super().__init__("nowhere", frozenset())

    class _Loose(_Dummy):
        def __init__(self):
            super().__init__("loose", {Placement.SUITE})  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="non-empty frozenset of Placement"):
        register_capability(_Nowhere())
    with pytest.raises(TypeError, match="non-empty frozenset of Placement"):
        register_capability(_Loose())
    assert registered_capabilities() == ()


def test_node_is_a_placement_a_capability_may_name():
    """A tree node's controls are a capability's panel drawn at that node
    (D129 + the Test page's decision 1): nothing else lets the shell draw
    `+` / wrap / `−` without knowing Edit tree by name."""
    assert Placement.NODE.value == "node"
    cap = _Dummy("at-node", frozenset({Placement.NODE}))
    register_capability(cap)
    assert cap in registered_capabilities()


def test_a_capability_may_attach_at_several_placements():
    cap = _Dummy("both", frozenset({Placement.SUITE, Placement.TEST}))
    register_capability(cap)
    assert cap.placements == {Placement.SUITE, Placement.TEST}


def test_plural_counts_its_noun():
    assert plural(0, "test") == "0 tests"
    assert plural(1, "test") == "1 test"
    assert plural(2, "spec edit") == "2 spec edits"


def test_plural_takes_an_irregular_plural():
    assert plural(1, "new entry", "new entries") == "1 new entry"
    assert plural(2, "new entry", "new entries") == "2 new entries"
    assert plural(0, "new entry", "new entries") == "0 new entries"


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
        register_capability(_Dummy("from-plugin", frozenset({Placement.CRITERION})))

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
