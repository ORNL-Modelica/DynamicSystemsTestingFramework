"""Entry-point plug-in loading — the third-party registration seam.

These tests serve fake ``dstf.plugins`` entry points by monkeypatching
``importlib.metadata.entry_points``, so nothing has to be pip-installed to
prove the path works. The load-bearing one is
``test_module_style_plugin_registers_metric``: it registers a metric from
"outside" the package and asserts it shows up in ``comparison.registry``.
"""

from __future__ import annotations

import importlib.metadata
from types import ModuleType

import pytest

from dstf import plugins
from dstf.comparison import registry
from dstf.comparison.registry import MetricDescriptor
from dstf.config import ConfigError


@pytest.fixture(autouse=True)
def _clean_state():
    """Reset the load-once flag and any metric a plug-in registered."""
    plugins._reset_for_tests()
    yield
    plugins._reset_for_tests()
    registry._reset_for_tests()


class _FakeEntryPoint:
    """Stand-in for ``importlib.metadata.EntryPoint`` with a canned ``load``."""

    def __init__(self, name: str, value: str, loader):
        self.name = name
        self.value = value
        self.group = plugins.PLUGIN_GROUP
        self._loader = loader

    def load(self):
        return self._loader()


def _serve(monkeypatch, *eps):
    """Make ``entry_points(group="dstf.plugins")`` yield *eps*."""

    def fake_entry_points(*, group=None):
        return list(eps) if group == plugins.PLUGIN_GROUP else []

    monkeypatch.setattr(importlib.metadata, "entry_points", fake_entry_points)


def _toy_descriptor(name: str) -> MetricDescriptor:
    return MetricDescriptor(
        name=name,
        config_cls=dict,
        build=lambda overrides, tol: None,  # never scored in these tests
    )


# ---------------------------------------------------------------------------


def test_module_style_plugin_registers_metric(monkeypatch):
    """A plug-in whose entry point resolves to a module: import did the work."""
    mod = ModuleType("acme_dstf_plugin")
    registry.register_metric(_toy_descriptor("acme-slope"))  # the "import"

    _serve(
        monkeypatch,
        _FakeEntryPoint("acme", "acme_dstf_plugin", lambda: mod),
    )

    assert plugins.load_plugins() == ["acme"]
    assert "acme-slope" in registry.metric_names()


def test_callable_style_plugin_is_invoked(monkeypatch):
    """A plug-in whose entry point resolves to a callable gets called."""
    calls: list[int] = []

    def register():
        calls.append(1)
        registry.register_metric(_toy_descriptor("acme-callable"))

    _serve(
        monkeypatch,
        _FakeEntryPoint("acme", "acme_dstf_plugin:register", lambda: register),
    )

    assert plugins.load_plugins() == ["acme"]
    assert calls == [1]
    assert "acme-callable" in registry.metric_names()


def test_broken_plugin_raises_config_error_naming_it(monkeypatch):
    """A plug-in that fails to import is a hard error, not a warning."""

    def boom():
        raise ImportError("no module named 'acme_missing_dep'")

    _serve(
        monkeypatch,
        _FakeEntryPoint("acme", "acme_dstf_plugin:register", boom),
    )

    with pytest.raises(ConfigError) as exc:
        plugins.load_plugins()
    assert "acme" in str(exc.value)
    assert "acme_dstf_plugin:register" in str(exc.value)
    assert isinstance(exc.value.__cause__, ImportError)


def test_non_module_non_callable_plugin_raises(monkeypatch):
    """An entry point pointing at a plain value is a declaration bug."""
    _serve(
        monkeypatch,
        _FakeEntryPoint("acme", "acme_dstf_plugin:VERSION", lambda: 42),
    )

    with pytest.raises(ConfigError, match="acme"):
        plugins.load_plugins()


def test_loading_twice_does_not_double_register(monkeypatch):
    """Idempotent: registries raise on duplicates, so a second load must no-op."""
    loads: list[int] = []

    def register():
        loads.append(1)
        registry.register_metric(_toy_descriptor("acme-once"))

    _serve(
        monkeypatch,
        _FakeEntryPoint("acme", "acme_dstf_plugin:register", lambda: register),
    )

    assert plugins.load_plugins() == ["acme"]
    assert plugins.load_plugins() == ["acme"]  # would raise on re-register
    assert loads == [1]


def test_no_plugins_installed_is_fine(monkeypatch):
    _serve(monkeypatch)
    assert plugins.load_plugins() == []


def test_top_level_export_resolves_lazily():
    """``dstf.load_plugins`` is reachable, and unknown attributes still raise."""
    import dstf

    assert "load_plugins" not in vars(dstf)  # not bound at import time
    assert dstf.load_plugins is plugins.load_plugins
    with pytest.raises(AttributeError):
        getattr(dstf, "no_such_attribute")  # noqa: B009 — the point is the raise
