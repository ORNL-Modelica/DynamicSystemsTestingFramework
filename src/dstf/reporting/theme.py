"""The visual system's tokens (DESIGN.md "Colors", "Typography", "Themes"):
one table both tiers read. Rendered to CSS custom properties for the
dashboard, the per-test report and the tool's page, and mirrored into the
tool's Quasar palette. The figure layer keeps its literal hex (plot series
carry meaning and never follow a theme).

A theme sets the neutrals, the accent and the selection tint — nothing
else. ``STATUS`` and ``ROLES`` are the same under every theme.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

#: Corrected stack (DESIGN.md "Typography"): the shipped one ended in
#: `monospace` with no generic sans-serif.
FONT_STACK = '"Segoe UI", Roboto, system-ui, -apple-system, sans-serif'
MONO_STACK = 'ui-monospace, Consolas, "Cascadia Mono", monospace'

DEFAULT_THEME: Mapping[str, str] = MappingProxyType(
    {
        "page": "#fafafa",
        "surface": "#ffffff",
        "surface-hover": "#f5f5f5",
        #: One step past surface-hover, for a control that is itself a
        #: hoverable surface: a `th` rests on surface-hover, so its own
        #: hover needs somewhere else to go.
        "surface-active": "#ececec",
        "border": "#dddddd",
        "border-hairline": "#eeeeee",
        "text": "#333333",
        "text-secondary": "#666666",
        "text-muted": "#888888",
        "text-disabled": "#999999",
        "accent": "#1976D2",
        "accent-hover": "#1565C0",
        "selection": "#e3f2fd",
        "shadow": "rgba(0,0,0,0.1)",
    }
)

#: fill: the filled badge and bar segment; dark: text on a tint; tint: the
#: row background; pill: the tinted pill's background; border: its border.
STATUS: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {
        "pass": MappingProxyType(
            {
                "fill": "#4CAF50",
                "dark": "#2E7D32",
                "tint": "#f1f8e9",
                "pill": "#e8f5e9",
                "border": "#a5d6a7",
            }
        ),
        "fail": MappingProxyType(
            {
                "fill": "#f44336",
                "dark": "#C62828",
                "tint": "#ffebee",
                "pill": "#ffebee",
                "border": "#ef9a9a",
            }
        ),
        "sim-fail": MappingProxyType(
            {
                "fill": "#d32f2f",
                "dark": "#B71C1C",
                "tint": "#ffebee",
                "pill": "#ffebee",
                "border": "#ef9a9a",
            }
        ),
        "no-ref": MappingProxyType(
            {
                "fill": "#FF9800",
                "dark": "#E65100",
                "tint": "#fff8e1",
                "pill": "#fff8e1",
                "border": "#ffe0b2",
            }
        ),
        "warn": MappingProxyType(
            {
                "fill": "#ffc107",
                "dark": "#8a6d00",
                "tint": "#fff3cd",
                "pill": "#fff3cd",
                "border": "#ffc107",
            }
        ),
        "timed-out": MappingProxyType(
            {
                "fill": "#9C27B0",
                "dark": "#6A1B9A",
                "tint": "#f3e5f5",
                "pill": "#f3e5f5",
                "border": "#e1bee7",
            }
        ),
        "queued": MappingProxyType(
            {
                "fill": "#888888",
                "dark": "#555555",
                "tint": "#f5f5f5",
                "pill": "#f5f5f5",
                "border": "#dddddd",
            }
        ),
        "running": MappingProxyType(
            {
                "fill": "#2196F3",
                "dark": "#1976D2",
                "tint": "#e3f2fd",
                "pill": "#e3f2fd",
                "border": "#90caf9",
            }
        ),
        "pending": MappingProxyType(
            {
                "fill": "#607D8B",
                "dark": "#607D8B",
                "tint": "#eceff1",
                "pill": "#eceff1",
                "border": "#b0bec5",
            }
        ),
    }
)

ROLES: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {
        "soft_check": MappingProxyType(
            {"bg": "#e8d8f3", "fg": "#5e3a80", "border": "#c4a8df"}
        ),
        "companion": MappingProxyType(
            {"bg": "#d8f3e4", "fg": "#2b6b48", "border": "#a8dfc0"}
        ),
    }
)


def css_variables(theme: Mapping[str, str] = DEFAULT_THEME) -> str:
    """The ``:root`` block every page starts its stylesheet with."""
    lines = [f"  --dstf-{name}: {value};" for name, value in theme.items()]
    lines.append(f"  --dstf-font: {FONT_STACK};")
    lines.append(f"  --dstf-mono: {MONO_STACK};")
    for status, parts in STATUS.items():
        lines += [
            f"  --dstf-{status}-{part}: {value};" for part, value in parts.items()
        ]
    for role, parts in ROLES.items():
        lines += [
            f"  --dstf-role-{role}-{part}: {value};" for part, value in parts.items()
        ]
    return ":root {\n" + "\n".join(lines) + "\n}\n"


def quasar_palette(theme: Mapping[str, str] = DEFAULT_THEME) -> dict[str, str]:
    """``ui.colors(**quasar_palette())``: the tool's Quasar names from the table."""
    return {
        "primary": theme["accent"],
        "secondary": theme["text-secondary"],
        "accent": theme["accent"],
        "positive": STATUS["pass"]["fill"],
        "negative": STATUS["fail"]["fill"],
        "warning": STATUS["warn"]["fill"],
        "info": STATUS["pending"]["fill"],
    }
