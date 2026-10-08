"""The visual system's tokens (DESIGN.md "Colors", "Typography", "Themes"):
one table both tiers read. Rendered to CSS custom properties for the
dashboard, the per-test report and the tool's page, and mirrored into the
tool's Quasar palette. The figure layer keeps its literal hex: what a
figure takes from a theme is a table of its own, under the same names
(``figures/palette.py``).

A theme is a NAME of :data:`THEMES` (D148). It sets the neutrals, the
accent, the selection tint and the text on an accent fill. ``ROLES`` and
every status FILL are the same under every theme; a dark theme dims what a
status lays behind text (:func:`status_colours`).
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
        #: Text on a FILLED accent (the primary button). A token of its
        #: own: on a dark surface no one blue reads as link text and
        #: carries white text at once (white on the dark accent is 2.2:1).
        "on-accent": "#ffffff",
    }
)

#: Neutral near-black, never blue-tinted (D148). The page is Quasar's own
#: dark page, so a Quasar piece nobody restyled sits on the same ground.
DARK_THEME: Mapping[str, str] = MappingProxyType(
    {
        "page": "#121212",
        "surface": "#1e1e1e",
        "surface-hover": "#2a2a2a",
        "surface-active": "#353535",
        "border": "#484848",
        "border-hairline": "#303030",
        "text": "#e4e4e4",
        "text-secondary": "#b8b8b8",
        "text-muted": "#9e9e9e",
        "text-disabled": "#7d7d7d",
        "accent": "#64b5f6",
        "accent-hover": "#90caf9",
        "selection": "#1d3a57",
        "shadow": "rgba(0,0,0,0.5)",
        "on-accent": "#0b1a2b",
    }
)

#: The bundled themes, by the name `testing.json`'s ``"theme"`` says. A
#: name only (brief §3.10): no theme file, no inline colours.
THEMES: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {"default": DEFAULT_THEME, "dark": DARK_THEME}
)

#: The themes that write light on dark: Quasar's dark mode, the browser's
#: ``color-scheme`` and the dimmed status parts follow this.
DARK_THEMES = frozenset({"dark"})


def theme_named(name: str) -> Mapping[str, str]:
    """The token table of the bundled theme ``name``; any other name is a
    ``ValueError`` that says the bundled ones."""
    try:
        return THEMES[name]
    except KeyError:
        raise ValueError(
            f"unknown theme {name!r}: the bundled themes are {', '.join(THEMES)}"
        ) from None


def is_dark(name: str) -> bool:
    theme_named(name)
    return name in DARK_THEMES


#: The neutral grey: a live row still queued, and a test with no result
#: (NOT RUN, D145) — one token under two classes, so a pill and a filter
#: can still tell them apart.
_NEUTRAL: Mapping[str, str] = MappingProxyType(
    {
        "fill": "#888888",
        "dark": "#555555",
        "tint": "#f5f5f5",
        "pill": "#f5f5f5",
        "border": "#dddddd",
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
        "queued": _NEUTRAL,
        "not-run": _NEUTRAL,
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

#: The tree card's level bars (U5, Q1): level i's bar takes ``level-<(i-1)
#: % 4 + 1>``, and the deeper ``-on`` shade while its group is hovered.
#: Blue, violet, cyan, slate — never a status's green, red or amber: a bar
#: says where a row sits, never how it scored.
LEVELS: Mapping[str, str] = MappingProxyType(
    {
        "level-1": "#7fb0ec",
        "level-2": "#a990dc",
        "level-3": "#5fbfcf",
        "level-4": "#9aa8b8",
        "level-1-on": "#2f7fd8",
        "level-2-on": "#7650c6",
        "level-3-on": "#1f97ab",
        "level-4-on": "#6b7b8f",
    }
)


#: How much of a status's FILL a dark theme lays over its surface for each
#: part that sits behind or around text (D148): the row tint, the pill, the
#: pill's border. The light table's pastels would be light islands there.
_DIMMED: Mapping[str, float] = MappingProxyType(
    {"tint": 0.14, "pill": 0.24, "border": 0.6}
)

#: The text a dark theme writes on a status tint or pill, and on the
#: surface: the status's pastel — the light table's pill border. Warn's
#: border is its fill, so its pastel is the next lighter amber.
_PASTELS: Mapping[str, str] = MappingProxyType(
    {
        **{status: parts["border"] for status, parts in STATUS.items()},
        "warn": "#ffd54f",
    }
)


def _channels(colour: str) -> tuple[int, int, int]:
    return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)


def _mix(top: str, under: str, alpha: float) -> str:
    """``top`` laid at ``alpha`` over ``under``, both ``#rrggbb``."""
    pairs = zip(_channels(top), _channels(under))
    return "#" + "".join(f"{round(a * alpha + b * (1 - alpha)):02x}" for a, b in pairs)


def status_colours(name: str = "default") -> Mapping[str, Mapping[str, str]]:
    """``STATUS`` as the theme ``name`` draws it. A light theme's is the
    table. A dark one keeps every FILL — the badge is white on it in every
    theme — and dims the rest: tint, pill and border are the fill laid thin
    over the theme's surface (:data:`_DIMMED`), and ``dark``, the text on
    them, is the status's pastel (:data:`_PASTELS`)."""
    if not is_dark(name):
        return STATUS
    surface = THEMES[name]["surface"]
    return {
        status: {
            "fill": parts["fill"],
            "dark": _PASTELS[status],
            **{
                part: _mix(parts["fill"], surface, alpha)
                for part, alpha in _DIMMED.items()
            },
        }
        for status, parts in STATUS.items()
    }


def css_variables(name: str = "default") -> str:
    """The ``:root`` block every page starts its stylesheet with, for the
    bundled theme ``name``."""
    theme = theme_named(name)
    lines = [f"  --dstf-{token}: {value};" for token, value in theme.items()]
    lines.append(f"  --dstf-font: {FONT_STACK};")
    lines.append(f"  --dstf-mono: {MONO_STACK};")
    if is_dark(name):
        # What the browser draws itself — a <select>'s list, a checkbox, a
        # scrollbar — follows this and nothing else.
        lines.append("  color-scheme: dark;")
    for status, parts in status_colours(name).items():
        lines += [
            f"  --dstf-{status}-{part}: {value};" for part, value in parts.items()
        ]
    for role, parts in ROLES.items():
        lines += [
            f"  --dstf-role-{role}-{part}: {value};" for part, value in parts.items()
        ]
    return ":root {\n" + "\n".join(lines) + "\n}\n"


def level_variables() -> str:
    """The ``:root`` block of the tree card's level tokens: the tool's
    alone (`Shell.page_setup`). In `css_variables` they were eight unused
    properties on every report and dashboard (the U5 review, H5)."""
    lines = [f"  --dstf-{name}: {value};" for name, value in LEVELS.items()]
    return ":root {\n" + "\n".join(lines) + "\n}\n"


def quasar_palette(name: str = "default") -> dict[str, str]:
    """``ui.colors(**quasar_palette())``: the tool's Quasar names from the
    table. A dark theme names Quasar's two dark grounds as well — what it
    paints a card, a menu and the page with under ``body--dark``."""
    theme = theme_named(name)
    grounds = (
        {"dark": theme["surface"], "dark_page": theme["page"]} if is_dark(name) else {}
    )
    return {
        **grounds,
        "primary": theme["accent"],
        "secondary": theme["text-secondary"],
        "accent": theme["accent"],
        "positive": STATUS["pass"]["fill"],
        "negative": STATUS["fail"]["fill"],
        "warning": STATUS["warn"]["fill"],
        "info": STATUS["pending"]["fill"],
    }
