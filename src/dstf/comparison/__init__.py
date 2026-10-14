"""Comparison layer: metrics, MetricTrees, and the registries behind both.

Importing this package registers the built-in metrics and combinators, so
``registry.metric(...)`` / ``registry.combinator(...)`` resolve before any
spec is parsed. Third-party metrics register the same way (see
docs/guides/plugin.md).
"""

from __future__ import annotations

from .builtins import register_builtins

register_builtins()
