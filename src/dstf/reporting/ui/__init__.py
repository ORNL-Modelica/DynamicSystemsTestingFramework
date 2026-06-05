"""Reporter UI helpers — auto-derived per-mode control panels (Phase 6.1.1).

The reporter-as-IDE surface (D66) auto-generates interactive form controls
from each :class:`ComparisonMode`'s typed Config dataclass. This package
owns the introspection-to-schema and schema-to-HTML pipeline plus the
mode registry that bridges mode names to their UI shape.
"""

from .mode_controls import (
    derive_schema,
    get_mode_ui,
    register_mode_ui,
    render_schema_html,
)

__all__ = [
    "derive_schema",
    "get_mode_ui",
    "register_mode_ui",
    "render_schema_html",
]
