"""Build the authoring-tool redesign artboards (Design Components, .dc.html).

Every value comes from DESIGN.md at the repo root. Run from this directory:

    python3 build.py

It writes one <Name>.dc.html per artboard plus canvas.json, which the design
canvas seeds. The artboards are static mockups: no logic script, literal copy,
inline flex/grid so the canvas editor can restyle and rearrange them.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

HERE = Path(__file__).parent

# --- tokens (DESIGN.md) ------------------------------------------------------

C = {
    "page": "#fafafa",
    "surface": "#ffffff",
    "hover": "#f5f5f5",
    "border": "#dddddd",
    "hair": "#eeeeee",
    "text": "#333333",
    "text2": "#666666",
    "muted": "#888888",
    "accent": "#1976D2",
    "accent_hover": "#1565C0",
    "sel": "#e3f2fd",
}
STATUS = {  # fill, dark, tint, border
    "pass": ("#4CAF50", "#2E7D32", "#e8f5e9", "#a5d6a7"),
    "fail": ("#f44336", "#C62828", "#ffebee", "#ef9a9a"),
    "sim-fail": ("#d32f2f", "#B71C1C", "#ffebee", "#ef9a9a"),
    "no-ref": ("#FF9800", "#E65100", "#fff8e1", "#ffe0b2"),
    "warn": ("#ffc107", "#8a6d00", "#fff3cd", "#ffc107"),
    "timed-out": ("#9C27B0", "#6A1B9A", "#f3e5f5", "#e1bee7"),
    "queued": ("#888888", "#555555", "#f5f5f5", "#dddddd"),
    "running": ("#2196F3", "#1976D2", "#e3f2fd", "#90caf9"),
    "pending": ("#607D8B", "#607D8B", "#eceff1", "#b0bec5"),
}
FONT = '"Segoe UI", Roboto, system-ui, -apple-system, sans-serif'
MONO = 'ui-monospace, Consolas, "Cascadia Mono", monospace'

ACTUAL = "#2196F3"
REFERENCE = "#FF9800"
PASS_FILL = "rgba(76,175,80,0.15)"
PASS_LINE = "rgba(76,175,80,0.6)"
FAIL_FILL = "rgba(244,67,54,0.15)"
FAIL_LINE = "rgba(244,67,54,0.8)"
WINDOW_FILL = "rgba(33,150,243,0.08)"
WINDOW_LINE = "rgba(33,150,243,0.4)"
ERROR = "#f44336"
HANDLE = "#2e7d32"
HANDLE_FILL = "rgba(46,125,50,0.25)"

CSS = f"""
body {{ margin: 0; background: {C["page"]}; color: {C["text"]}; font-family: {FONT}; font-size: 14px; line-height: 1.4; }}
a {{ color: {C["accent"]}; text-decoration: none; }} a:hover {{ color: {C["accent_hover"]}; text-decoration: underline; }}
.mono {{ font-family: {MONO}; font-size: 12px; }}
.h1 {{ font-size: 20px; font-weight: 600; margin: 0; }}
.h2 {{ font-size: 16px; font-weight: 600; margin: 0; color: #555555; }}
.small {{ font-size: 12px; }}
.muted {{ color: {C["muted"]}; }}
.text2 {{ color: {C["text2"]}; }}
.label {{ font-size: 10px; color: {C["muted"]}; text-transform: uppercase; letter-spacing: 0.03em; }}
.wordmark {{ font-size: 20px; font-weight: 700; letter-spacing: 0.02em; color: {C["text"]}; }}
.header {{ position: sticky; top: 0; z-index: 5; background: {C["page"]}; border-bottom: 1px solid {C["border"]}; padding: 10px 24px; }}
.card {{ background: {C["surface"]}; border: 1px solid {C["border"]}; border-radius: 4px; }}
.btn {{ display: inline-flex; align-items: center; gap: 6px; height: 28px; padding: 0 10px; border-radius: 3px; font-size: 13px; font-weight: 500; white-space: nowrap; cursor: pointer; border: 1px solid transparent; background: transparent; color: {C["accent"]}; }}
.btn.primary {{ background: {C["accent"]}; color: #ffffff; }}
.btn.outline {{ border-color: {C["accent"]}; }}
.btn.danger {{ border-color: {STATUS["fail"][0]}; color: {STATUS["fail"][1]}; }}
.btn.disabled {{ opacity: 0.5; cursor: default; }}
.btn.icon {{ width: 28px; padding: 0; justify-content: center; color: {C["text2"]}; }}
.badge {{ display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 10.5px; font-weight: 700; letter-spacing: 0.03em; color: #ffffff; text-transform: uppercase; white-space: nowrap; }}
.pill {{ display: inline-block; padding: 1px 7px; border-radius: 3px; font-size: 10.5px; font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase; white-space: nowrap; border: 1px solid; }}
.input {{ display: inline-flex; align-items: center; height: 32px; padding: 0 8px; border: 1px solid #cccccc; border-radius: 2px; background: #ffffff; font-size: 13px; color: {C["text"]}; box-sizing: border-box; }}
.input.sm {{ height: 26px; font-size: 12px; padding: 0 6px; }}
.input.ph {{ color: #999999; }}
.select {{ display: inline-flex; align-items: center; justify-content: space-between; gap: 8px; height: 32px; padding: 0 8px; border: 1px solid #cccccc; border-radius: 2px; background: #ffffff; font-size: 13px; box-sizing: border-box; }}
.select.sm {{ height: 26px; font-size: 12px; }}
.chip {{ display: inline-flex; align-items: center; gap: 4px; padding: 0 6px; height: 20px; border-radius: 3px; font-size: 11px; border: 1px solid {C["border"]}; background: #ffffff; }}
.chip.role-soft {{ background: #e8d8f3; color: #5e3a80; border-color: #c4a8df; }}
.chip.role-comp {{ background: #d8f3e4; color: #2b6b48; border-color: #a8dfc0; }}
.chip.on {{ border-color: {C["accent"]}; background: {C["sel"]}; }}
table {{ border-collapse: collapse; width: 100%; background: #ffffff; font-size: 12px; }}
th, td {{ border: 1px solid {C["border"]}; padding: 4px 8px; text-align: left; height: 28px; box-sizing: border-box; }}
th {{ background: {C["hover"]}; font-weight: 600; }}
td.num, th.num {{ text-align: right; font-family: {MONO}; }}
tr.group td {{ background: {C["hover"]}; font-weight: 600; color: {C["text2"]}; }}
tr.sel td {{ background: {C["sel"]}; }}
.counter {{ background: #ffffff; border: 1px solid {C["border"]}; border-left-width: 4px; border-radius: 4px; padding: 6px 12px; min-width: 90px; }}
.counter .value {{ font-size: 18px; font-weight: 600; }}
.counter.active {{ box-shadow: 0 0 0 2px {C["accent"]} inset; background: {C["sel"]}; }}
.bar {{ height: 14px; background: #eeeeee; border-radius: 7px; overflow: hidden; display: flex; }}
.bar span {{ display: block; height: 100%; }}
.check {{ width: 16px; height: 16px; border: 1px solid #999999; border-radius: 2px; background: #ffffff; display: inline-block; box-sizing: border-box; }}
.check.on {{ background: {C["accent"]}; border-color: {C["accent"]}; }}
.crit {{ display: flex; align-items: center; gap: 10px; padding: 6px 8px; border-top: 1px solid {C["hair"]}; }}
.crit .name {{ font-weight: 600; }}
.crit.open {{ background: {C["sel"]}; }}
.editor {{ margin: 0 0 8px 16px; padding: 12px; border-left: 3px solid {C["accent"]}; background: #ffffff; border-top: 1px solid {C["hair"]}; border-bottom: 1px solid {C["hair"]}; }}
.hint {{ font-size: 12px; color: {C["text2"]}; }}
.why {{ font-size: 12px; color: {C["text2"]}; font-style: italic; }}
.rule {{ height: 1px; background: {C["hair"]}; }}
.stat {{ background: #ffffff; border: 1px solid {C["border"]}; border-radius: 4px; padding: 6px 12px; min-width: 120px; }}
.stat .value {{ font-size: 18px; font-weight: 600; }}
.stat .ref {{ font-size: 11px; color: #999999; }}
.progress {{ height: 6px; background: #eeeeee; border-radius: 3px; overflow: hidden; }}
.progress span {{ display: block; height: 100%; background: {ACTUAL}; }}
.dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 4px; }}
.header span, .header button, .sub span, .sub button, .h2, .btn, .label, .stat .ref {{ white-space: nowrap; }}
.header > div > *, .sub > div > * {{ flex: none; }}
.header > div > div[style*="flex-grow"], .sub > div > div[style*="flex-grow"] {{ flex: 1 1 0; }}
"""


# --- small components --------------------------------------------------------


def badge(status: str, text: str | None = None) -> str:
    fill = STATUS[status][0]
    return f'<span class="badge" style="background: {fill}">{text or status}</span>'


def pill(status: str, text: str | None = None) -> str:
    _, dark, tint, border = STATUS[status]
    return (
        f'<span class="pill" style="background: {tint}; color: {dark}; '
        f'border-color: {border}">{text or status}</span>'
    )


def btn(label: str, kind: str = "", title: str = "", icon: str = "") -> str:
    t = f' title="{title}"' if title else ""
    return f'<button class="btn {kind}"{t}>{icon}{label}</button>'


def icon(name: str, color: str = "currentColor", size: int = 16) -> str:
    """Inline stroke icons on a 16px grid."""
    paths = {
        "plus": '<path d="M8 3v10M3 8h10"/>',
        "minus": '<path d="M3 8h10"/>',
        "wrap": '<path d="M4 3h8v10H4z"/><path d="M6 6h4M6 9h4"/>',
        "close": '<path d="M4 4l8 8M12 4l-8 8"/>',
        "chev-r": '<path d="M6 3l5 5-5 5"/>',
        "chev-d": '<path d="M3 6l5 5 5-5"/>',
        "chev-l": '<path d="M10 3L5 8l5 5"/>',
        "menu": '<path d="M2 4h12M2 8h12M2 12h12"/>',
        "play": '<path d="M4 3l9 5-9 5z"/>',
        "stop": '<path d="M4 4h8v8H4z"/>',
        "search": '<circle cx="7" cy="7" r="4"/><path d="M10 10l4 4"/>',
        "ext": '<path d="M9 3h4v4M13 3L7 9M11 9v4H3V5h4"/>',
        "check": '<path d="M3 8l3 3 7-7"/>',
        "reset": '<path d="M3 8a5 5 0 1 0 2-4"/><path d="M3 3v3h3"/>',
        "brush": '<path d="M3 3v10M13 3v10"/><path d="M3 8h10" stroke-dasharray="2 2"/>',
        "camera": '<path d="M2 5h3l1-2h4l1 2h3v8H2z"/><circle cx="8" cy="9" r="2.5"/>',
        "grip": '<path d="M6 4h.01M10 4h.01M6 8h.01M10 8h.01M6 12h.01M10 12h.01"/>',
    }
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 16 16" fill="none" '
        f'stroke="{color}" stroke-width="1.5" stroke-linecap="round" '
        f'stroke-linejoin="round" style="flex: none">{paths[name]}</svg>'
    )


def ibtn(name: str, title: str) -> str:
    return f'<button class="btn icon" title="{title}">{icon(name)}</button>'


def check(on: bool = False) -> str:
    return f'<span class="check{" on" if on else ""}"></span>'


def select(text: str, width: int = 160, sm: bool = False) -> str:
    return (
        f'<span class="select{" sm" if sm else ""}" style="width: {width}px">'
        f"<span>{text}</span>{icon('chev-d', C['text2'], 12)}</span>"
    )


def inp(text: str, width: int = 90, sm: bool = True, ph: bool = False) -> str:
    cls = "input" + (" sm" if sm else "") + (" ph" if ph else "")
    return f'<span class="{cls}" style="width: {width}px">{text}</span>'


def row(*cells: str, cls: str = "") -> str:
    c = f' class="{cls}"' if cls else ""
    return f"<tr{c}>" + "".join(cells) + "</tr>"


def td(x: str, cls: str = "", extra: str = "") -> str:
    c = f' class="{cls}"' if cls else ""
    return f"<td{c}{extra}>{x}</td>"


def flex(
    children: list[str], gap: int = 8, style: str = "", align: str = "center"
) -> str:
    return (
        f'<div style="display: flex; flex-direction: row; align-items: {align}; '
        f'gap: {gap}px; {style}">' + "".join(children) + "</div>"
    )


def col(children: list[str], gap: int = 8, style: str = "") -> str:
    return (
        f'<div style="display: flex; flex-direction: column; gap: {gap}px; {style}">'
        + "".join(children)
        + "</div>"
    )


def spacer() -> str:
    return '<div style="flex-grow: 1"></div>'


# --- plot ---------------------------------------------------------------------


def _path(pts: list[tuple[float, float]]) -> str:
    return "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in pts)


def plot(
    *,
    width: int = 1360,
    height: int = 320,
    xr: tuple[float, float] = (0, 10),
    yr: tuple[float, float] = (-1.25, 1.25),
    actual=None,
    reference=None,
    actual_until: float | None = None,
    band=None,
    band_fail_after: float | None = None,
    window: tuple[float, float] | None = None,
    rings: list[tuple[float, float]] | None = None,
    diamonds: list[tuple[float, float, float, float]] | None = None,
    hlines: list[tuple[float, str, str]] | None = None,
    legend: list[tuple[str, str, str]] | None = None,
    error_axis: bool = False,
    error=None,
    running_marker: float | None = None,
    hint: str | None = None,
) -> str:
    """A Plotly-looking figure as inline SVG. Functions take t and return y."""
    L, R, T, B = 64, 60 if error_axis else 24, 16, 40
    pw, ph = width - L - R, height - T - B
    n = 200

    def X(t):
        return L + (t - xr[0]) / (xr[1] - xr[0]) * pw

    def Y(v):
        return T + (yr[1] - v) / (yr[1] - yr[0]) * ph

    out = [
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'font-family="Open Sans, {FONT}" font-size="11" style="display: block; background: #ffffff">'
    ]
    # gridlines
    xt = [xr[0] + i * (xr[1] - xr[0]) / 5 for i in range(6)]
    yt = (
        [-1.0, -0.5, 0.0, 0.5, 1.0]
        if yr == (-1.25, 1.25)
        else [yr[0] + i * (yr[1] - yr[0]) / 4 for i in range(5)]
    )
    for t in xt:
        out.append(
            f'<line x1="{X(t):.1f}" y1="{T}" x2="{X(t):.1f}" y2="{T + ph}" stroke="#eeeeee"/>'
        )
        out.append(
            f'<text x="{X(t):.1f}" y="{T + ph + 16}" text-anchor="middle" fill="#444444">{t:g}</text>'
        )
    for v in yt:
        out.append(
            f'<line x1="{L}" y1="{Y(v):.1f}" x2="{L + pw}" y2="{Y(v):.1f}" stroke="#eeeeee"/>'
        )
        out.append(
            f'<text x="{L - 8}" y="{Y(v) + 4:.1f}" text-anchor="end" fill="#444444">{v:.3f}</text>'
        )
    out.append(
        f'<line x1="{L}" y1="{Y(0):.1f}" x2="{L + pw}" y2="{Y(0):.1f}" stroke="#444444"/>'
    )
    out.append(
        f'<text x="{L + pw / 2:.0f}" y="{height - 6}" text-anchor="middle" fill="#444444" font-size="12">Time</text>'
    )
    out.append(
        f'<text transform="translate(14 {T + ph / 2:.0f}) rotate(-90)" text-anchor="middle" fill="#444444" font-size="12">Value</text>'
    )
    if error_axis:
        out.append(
            f'<line x1="{L + pw}" y1="{T}" x2="{L + pw}" y2="{T + ph}" stroke="{ERROR}" stroke-width="1"/>'
        )
        for i, lab in enumerate(["0.02", "0.01", "0.00", "-0.01", "-0.02"]):
            y = T + i * ph / 4
            out.append(
                f'<text x="{L + pw + 6}" y="{y + 4:.1f}" fill="{ERROR}" font-size="10">{lab}</text>'
            )
        out.append(
            f'<text transform="translate({width - 8} {T + ph / 2:.0f}) rotate(90)" text-anchor="middle" fill="{ERROR}" font-size="10">Error [signal units]</text>'
        )
    ts = [xr[0] + i * (xr[1] - xr[0]) / n for i in range(n + 1)]
    if window:
        out.append(
            f'<rect x="{X(window[0]):.1f}" y="{T}" width="{X(window[1]) - X(window[0]):.1f}" height="{ph}" '
            f'fill="{WINDOW_FILL}" stroke="{WINDOW_LINE}" stroke-dasharray="4 3"/>'
        )
    if band:
        up = [(X(t), Y(band(t)[1])) for t in ts]
        lo = [(X(t), Y(band(t)[0])) for t in reversed(ts)]
        out.append(
            f'<path d="{_path(up + lo)} Z" fill="{PASS_FILL}" stroke="{PASS_LINE}" stroke-width="1"/>'
        )
    if band_fail_after is not None and band:
        tsf = [t for t in ts if t >= band_fail_after]
        up = [(X(t), Y(band(t)[1])) for t in tsf]
        lo = [(X(t), Y(band(t)[0])) for t in reversed(tsf)]
        out.append(
            f'<path d="{_path(up + lo)} Z" fill="{FAIL_FILL}" stroke="{FAIL_LINE}" stroke-width="1"/>'
        )
    for v, color, dash in hlines or []:
        out.append(
            f'<line x1="{L}" y1="{Y(v):.1f}" x2="{L + pw}" y2="{Y(v):.1f}" stroke="{color}" stroke-width="1.5" stroke-dasharray="{dash}"/>'
        )
    if reference:
        out.append(
            f'<path d="{_path([(X(t), Y(reference(t))) for t in ts])}" fill="none" stroke="{REFERENCE}" stroke-width="2" stroke-dasharray="6 4"/>'
        )
    if actual:
        tsa = [t for t in ts if actual_until is None or t <= actual_until]
        out.append(
            f'<path d="{_path([(X(t), Y(actual(t))) for t in tsa])}" fill="none" stroke="{ACTUAL}" stroke-width="2"/>'
        )
    if error:
        # error scaled to the right axis: ±0.02 spans the plot height
        out.append(
            f'<path d="{_path([(X(t), T + ph / 2 - error(t) / 0.02 * ph / 2) for t in ts])}" fill="none" stroke="{ERROR}" stroke-width="1" opacity="0.8"/>'
        )
    if running_marker is not None and actual:
        x, y = X(running_marker), Y(actual(running_marker))
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="{ACTUAL}"/>')
        out.append(
            f'<line x1="{x:.1f}" y1="{T}" x2="{x:.1f}" y2="{T + ph}" stroke="{ACTUAL}" stroke-dasharray="3 3"/>'
        )
        out.append(
            f'<rect x="{x + 8:.1f}" y="{T + 6}" width="150" height="20" rx="3" fill="{C["sel"]}" stroke="{ACTUAL}"/>'
        )
        out.append(
            f'<text x="{x + 16:.1f}" y="{T + 20}" fill="{STATUS["running"][1]}" font-weight="600">running · t = {running_marker:g} / {xr[1]:g} s</text>'
        )
    for t, v, dt, dv in diamonds or []:
        bx, by = X(t - dt), Y(v + dv)
        out.append(
            f'<rect x="{bx:.1f}" y="{by:.1f}" width="{X(t + dt) - bx:.1f}" height="{Y(v - dv) - by:.1f}" fill="{PASS_FILL}" stroke="{PASS_LINE}"/>'
        )
        out.append(
            f'<path d="M{X(t):.1f} {Y(v) - 6:.1f} l6 6 l-6 6 l-6 -6 Z" fill="{HANDLE}"/>'
        )
    for t, v in rings or []:
        out.append(
            f'<circle cx="{X(t):.1f}" cy="{Y(v):.1f}" r="7" fill="{HANDLE_FILL}" stroke="{HANDLE}" stroke-width="2"/>'
        )
    if legend:
        y = T + 8
        out.append(
            f'<rect x="{L + 8}" y="{y - 4}" width="150" height="{len(legend) * 16 + 8}" fill="rgba(255,255,255,0.8)"/>'
        )
        for i, (name, color, kind) in enumerate(legend):
            yy = y + 8 + i * 16
            if kind == "line":
                out.append(
                    f'<line x1="{L + 14}" y1="{yy}" x2="{L + 38}" y2="{yy}" stroke="{color}" stroke-width="2"/>'
                )
            elif kind == "dash":
                out.append(
                    f'<line x1="{L + 14}" y1="{yy}" x2="{L + 38}" y2="{yy}" stroke="{color}" stroke-width="2" stroke-dasharray="5 3"/>'
                )
            elif kind == "fill":
                out.append(
                    f'<rect x="{L + 14}" y="{yy - 5}" width="24" height="10" fill="{color}"/>'
                )
            elif kind == "diamond":
                out.append(
                    f'<path d="M{L + 26} {yy - 5} l5 5 l-5 5 l-5 -5 Z" fill="{color}"/>'
                )
            out.append(f'<text x="{L + 44}" y="{yy + 4}" fill="#444444">{name}</text>')
    if hint:
        out.append(
            f'<text x="{L + pw - 4}" y="{T + ph - 8}" text-anchor="end" fill="{C["text2"]}" font-size="11">{hint}</text>'
        )
    out.append("</svg>")
    return "".join(out)


def sine(t):
    return math.sin(t)


def sine_off(t):
    return math.sin(t) * 1.012 + 0.006 * math.sin(3 * t)


def ramp(t):
    return t / 10


def decay(t):
    return math.exp(-0.3 * t) * math.cos(2 * t)


def decay_off(t):
    return math.exp(-0.3 * t) * math.cos(2.02 * t)


# --- document shell -----------------------------------------------------------


def doc(body: str, extra_css: str = "") -> str:
    return (
        '<!doctype html>\n<html>\n<head>\n  <meta charset="utf-8">\n'
        '  <script src="./support.js"></script>\n</head>\n<body>\n<x-dc>\n'
        f"<helmet>\n  <style>{CSS}{extra_css}</style>\n</helmet>\n"
        f"{body}\n</x-dc>\n</body>\n</html>\n"
    )


def write(name: str, body: str, extra_css: str = "") -> None:
    (HERE / f"{name}.dc.html").write_text(doc(body, extra_css), encoding="utf-8")
    print("wrote", name)


# --- canvas layout --------------------------------------------------------------

CANVAS = {
    "artboards": [
        {
            "file": "Main.dc.html",
            "title": "Suite page",
            "x": 0,
            "y": 0,
            "w": 1440,
            "h": 1064,
        },
        {
            "file": "QueueIndicator.dc.html",
            "title": "Header queue indicator",
            "x": 1520,
            "y": 0,
            "w": 520,
            "h": 392,
        },
        {
            "file": "Backend.dc.html",
            "title": "Backend controls",
            "x": 4160,
            "y": 0,
            "w": 1440,
            "h": 544,
        },
        {
            "file": "Startup.dc.html",
            "title": "Startup loading state",
            "x": 2640,
            "y": 0,
            "w": 1440,
            "h": 784,
        },
        {
            "file": "Test.dc.html",
            "title": "Test page, criteria collapsed",
            "x": 0,
            "y": 1200,
            "w": 1440,
            "h": 2094,
        },
        {
            "file": "TestTube.dc.html",
            "title": "Test page, tube criterion open",
            "x": 1520,
            "y": 1200,
            "w": 1440,
            "h": 1339,
        },
        {
            "file": "TestPoints.dc.html",
            "title": "Test page, points criterion open",
            "x": 3040,
            "y": 1200,
            "w": 1440,
            "h": 1381,
        },
        {
            "file": "TestRunning.dc.html",
            "title": "Test page while running",
            "x": 0,
            "y": 3414,
            "w": 1440,
            "h": 1571,
        },
        {
            "file": "PlotAnother.dc.html",
            "title": "Variables drawer: add plot / add series / arrays",
            "x": 1520,
            "y": 3414,
            "w": 1440,
            "h": 1194,
        },
    ],
    "annotations": [
        {
            "id": "brief",
            "x": 0,
            "y": -200,
            "w": 520,
            "text": "Authoring tool redesign, draft 2 (2026-09-15).\nBrief: docs/superpowers/specs/2026-09-15-authoring-tool-redesign-brief.md, section 4 lists these boards.\nTokens: DESIGN.md at the repo root, lifted from the report's stylesheet.\nData is the ModelicaTestingLib demo; every button label follows brief 3.3 (object, effect, count, tooltip).",
        },
        {
            "id": "rulings",
            "x": 560,
            "y": -200,
            "w": 520,
            "text": "Owner rulings (2026-09-15): Material chrome. Themes swap neutrals and accent, never status or series colours. Queue rows numbered. Plots are modular: any number, any series, tracked or plot only. Option C for picking variables: a collapsible Variables drawer (one drawer at a time with Tests), filter is substring or glob with * ? and literal [ ], regex and case toggles off by default; checkbox = on the selected plot, hover for new plot. Arrays fold by index with a start:step:stop field and draw as one hue ramped by index; a profile plot kind comes later.",
        },
    ],
    "launch": {"view": "canvas"},
}


if __name__ == "__main__":
    import pages

    pages.build_all()
    (HERE / "canvas.json").write_text(
        json.dumps(CANVAS, indent=2) + "\n", encoding="utf-8"
    )
    print("wrote canvas.json")
