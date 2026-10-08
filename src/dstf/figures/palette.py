"""What a figure takes from a theme (D148): one named table per bundled
theme, read by name wherever a figure is built.

A figure's colours are literals, never CSS variables: the report's
plotly.js and the tool's read a figure dict, not a stylesheet. Most of
them carry meaning and are the same in every theme — the actual and the
reference, a pass and a fail region, the error series, a grip
(``plotly.py``). The ones HERE follow the theme:

* the figure's chrome — a reference event's guide line, an open
  criterion's ring and handle markers, the grey of a context series;
* the colours that sink into a dark plot — the soft-check overlay and the
  darkest stops of the array families' ramps;
* the layout template: the plot's ground, grid, axes, legend, hover label
  and toolbar.

The default theme's template is ``None``: its plot is plotly.js's own
defaults and its figure carries no ``layout.template`` key at all. A dark
table's neutrals are its token table's (``reporting.theme``), as literals;
``tests/test_themes.py`` pins the two equal.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .families import HUES


@dataclass(frozen=True)
class Palette:
    #: A reference event's dashed guide line.
    reference_event: str
    #: An open criterion's ring and its handle markers, and the ring's
    #: fill — the same colour at a quarter, so the ring takes the pointer
    #: over its whole area.
    ring: str
    ring_fill: str
    #: A context series: the theme's ``text-muted``.
    context: str
    #: A soft-check overlay.
    soft_check: str
    #: The array families' ramps, light to dark (:func:`~.families.ramp`).
    hues: tuple[tuple[str, str, str, str], ...]
    #: ``layout.template.layout``, or ``None`` for plotly.js's own defaults.
    #: Its ``xaxis`` / ``yaxis`` reach EVERY axis of that letter — the
    #: spectrum's and the error axes' too; whatever a figure sets itself
    #: (an error axis's red ticks) wins over it. A figure takes a COPY.
    template: dict | None = None


def _axis(*, grid: str, line: str, text: str) -> dict:
    return {
        "gridcolor": grid,
        "zerolinecolor": line,
        "linecolor": line,
        "tickcolor": line,
        "tickfont": {"color": text},
        "title": {"font": {"color": text}},
    }


#: The dark theme's neutrals, as its token table names them.
_DARK = MappingProxyType(
    {
        "surface": "#1e1e1e",
        "surface-hover": "#2a2a2a",
        "border": "#484848",
        "border-hairline": "#303030",
        "text": "#e4e4e4",
        "text-secondary": "#b8b8b8",
        "text-muted": "#9e9e9e",
    }
)

_DARK_AXIS = _axis(
    grid=_DARK["border-hairline"], line=_DARK["border"], text=_DARK["text-secondary"]
)

PALETTES: Mapping[str, Palette] = MappingProxyType(
    {
        "default": Palette(
            reference_event="#757575",
            ring="#2e7d32",
            ring_fill="rgba(46,125,50,0.25)",
            context="#888888",
            soft_check="#7B1FA2",
            hues=HUES,
        ),
        "dark": Palette(
            # Each of these on the dark plot (#1e1e1e), default -> dark:
            # the event line 3.6 -> 7.0, the ring 3.3 -> 7.1, the soft
            # check 2.0 -> 4.7 (WCAG contrast).
            reference_event="#a8a8a8",
            ring="#66bb6a",
            ring_fill="rgba(102,187,106,0.25)",
            context=_DARK["text-muted"],
            soft_check="#ba68c8",
            # The four stops under 2.2:1 on the dark plot, each raised in
            # lightness at its own hue and saturation until it clears 3:1
            # and still stands darker than the stop before it. The eight
            # others are the default theme's.
            hues=(
                (*HUES[0][:3], "#1264e2"),
                (*HUES[1][:3], "#237b2a"),
                (*HUES[2][:2], "#a631d8", "#8a40e4"),
            ),
            template={
                "paper_bgcolor": _DARK["surface"],
                "plot_bgcolor": _DARK["surface"],
                "font": {"color": _DARK["text"]},
                "xaxis": _DARK_AXIS,
                "yaxis": _DARK_AXIS,
                "legend": {"font": {"color": _DARK["text"]}},
                "hoverlabel": {
                    "bgcolor": _DARK["surface-hover"],
                    "bordercolor": _DARK["border"],
                    "font": {"color": _DARK["text"]},
                },
                "modebar": {
                    "bgcolor": "rgba(0,0,0,0)",
                    "color": _DARK["text-muted"],
                    "activecolor": _DARK["text"],
                },
            },
        ),
    }
)


def palette_of(theme: str) -> Palette:
    """The palette of the bundled theme ``theme``; any other name is a
    ``ValueError`` that says the bundled ones."""
    try:
        return PALETTES[theme]
    except KeyError:
        raise ValueError(
            f"unknown theme {theme!r}: the bundled themes are {', '.join(PALETTES)}"
        ) from None
