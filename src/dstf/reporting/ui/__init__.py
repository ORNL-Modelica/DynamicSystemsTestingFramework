"""Reporter UI helpers — the per-mode schema derived from typed Configs.

Each :class:`ComparisonMode`'s Config dataclass is the single source of
truth for its fields; :func:`derive_schema` introspects one into a schema
the JSON-Schema export publishes and the authoring tool builds forms from.
The report page itself has no forms.
"""

from .mode_controls import derive_schema

__all__ = ["derive_schema"]
