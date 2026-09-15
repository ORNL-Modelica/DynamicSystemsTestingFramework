"""The artboards. Run build.py; it imports this module."""

from __future__ import annotations

from build import (
    ACTUAL,
    C,
    REFERENCE,
    STATUS,
    PASS_FILL,
    HANDLE,
    badge,
    btn,
    check,
    col,
    decay,
    decay_off,
    flex,
    ibtn,
    icon,
    inp,
    pill,
    plot,
    ramp,
    row,
    select,
    sine,
    sine_off,
    spacer,
    td,
    write,
)

LIB = "ModelicaTestingLib"
PKG = "ModelicaTestingLib.Examples"

# --- shared pieces ------------------------------------------------------------


def queue_indicator(state: str = "running") -> str:
    if state == "idle":
        return flex(
            [f'<span class="dot" style="background: #bbbbbb"></span>',
             '<span class="small text2">queue idle</span>'],
            gap=6,
        )
    if state == "failed":
        return flex(
            [f'<span class="dot" style="background: {STATUS["fail"][0]}"></span>',
             f'<span class="small" style="color: {STATUS["fail"][1]}; font-weight: 600">1 failed</span>',
             '<span class="small text2">· 2 queued</span>'],
            gap=6,
        )
    return flex(
        [f'<span class="dot" style="background: {ACTUAL}"></span>',
         f'<span class="small" style="color: {STATUS["running"][1]}; font-weight: 600">1 running</span>',
         '<span class="small text2">· 2 queued</span>',
         '<span class="progress" style="width: 80px"><span style="width: 42%"></span></span>'],
        gap=6,
    )


def header(*, queue: str = "running", edits: int = 3, tests: int = 2, page: str = "suite",
           backend: str = "Dymola") -> str:
    left = flex(
        [
            '<span class="wordmark">DSTF</span>',
            f'<span style="font-size: 16px; font-weight: 600">{LIB}</span>',
            select(backend, 150, sm=True),
        ],
        gap=14,
    )
    if edits:
        pending = flex(
            [
                btn(f"Save {edits} spec edits ({tests} tests) to test_spec.json", "primary",
                    "Writes every pending edit into the project's test_spec.json. Use it when the criteria are right."),
                btn(f"Discard {edits} edits", "", "Drops every pending edit and restores the spec as loaded."),
                btn("Export patch", "", "Writes the pending edits as a JSON-Patch file without touching test_spec.json."),
            ],
            gap=8,
        )
    else:
        pending = flex(
            [
                '<span class="small muted">no pending edits</span>',
                btn("Save 0 spec edits", "primary disabled"),
                btn("Discard", "disabled"),
                btn("Export patch", "disabled"),
            ],
            gap=8,
        )
    right = flex(
        [
            pending,
            '<span style="width: 1px; height: 24px; background: #dddddd"></span>',
            btn("Dashboard", "", "Opens the run's dashboard.html in a new tab.", icon("ext", C["accent"], 14)),
            btn("Stop server", "", "Stops dstf author. Asks first when edits are pending or the queue is not empty.",
                icon("stop", C["accent"], 14)),
        ],
        gap=12,
    )
    return (
        '<div class="header">'
        + flex([left, spacer(), queue_indicator(queue), spacer(), right], gap=16)
        + "</div>"
    )


def crit_row(status: str, metric: str, var: str, readout: str, *, open_: bool = False,
             advisory: bool = False, waiting: bool = False) -> str:
    controls = flex(
        [ibtn("plus", "Add a criterion beside this one"),
         ibtn("wrap", "Wrap in a combinator (and / or / k-of-n / warn / weighted)"),
         ibtn("minus", "Remove this criterion (asks first)")],
        gap=2,
    )
    if waiting:
        verdict = pill("pending", "waiting")
        readout_html = '<span class="mono muted">waiting for result</span>'
    else:
        verdict = pill(status)
        readout_html = f'<span class="mono text2">{readout}</span>'
    adv = f' <span class="pill" style="background: #fff3cd; color: #8a6d00; border-color: #ffc107">advisory</span>' if advisory else ""
    return (
        f'<div class="crit{" open" if open_ else ""}">'
        + verdict
        + f'<span class="name">{metric} · {var}</span>{adv}'
        + readout_html
        + spacer()
        + controls
        + "</div>"
    )


def overlay_toolbar(var: str, *, window: bool = False) -> str:
    return flex(
        [
            '<span class="small text2">Overlays</span>',
            '<span class="chip on">reference · Dymola</span>',
            '<span class="chip">OpenModelica <span class="chip role-soft" style="height: 16px">soft check</span></span>',
            '<span class="chip">MSL 4.0.0 <span class="chip role-comp" style="height: 16px">companion</span></span>',
            '<span style="width: 12px"></span>',
            '<span class="small text2">Error overlay</span>',
            select("none", 130, sm=True),
            spacer(),
            btn("Set window" if not window else "Clear window", "", "Starts a brush on the plot; the drawn span becomes the open criterion's window.", icon("brush", C["accent"], 14)),
            btn("Focus this variable", "", "Hides the other variables until you leave the page or click again."),
        ],
        gap=8,
    )


def series_chip(name: str, tracked: bool = True) -> str:
    tag = "" if tracked else ' <span class="chip" style="height: 14px; font-size: 10px">plot only</span>'
    return f'<span class="chip on"><span class="mono">{name}</span>{tag}</span>'


def var_header(var: str, status: str, unit: str = "", extra_series: list[str] | None = None) -> str:
    chips = [series_chip(var)] + [series_chip(e, tracked=False) for e in extra_series or []]
    return flex(
        [
            f'<span class="h2" style="color: #333333">{var}</span>',
            pill(status),
            f'<span class="small muted">{unit}</span>' if unit else "",
            '<span style="width: 8px"></span>',
            '<span class="label">series</span>',
            *chips,
            btn("Add series", "", "Any variable or expression from the result, drawn on this plot. Series that are not tracked have no criteria.", icon("plus", C["accent"], 14)),
            spacer(),
            btn("Separate", "", "One plot per series.") if extra_series else "",
            ibtn("close", "Remove this plot (the variable stays tracked; its criteria stay in the tree)"),
        ],
        gap=8,
    )


def var_section(var: str, status: str, plot_html: str, crits: list[str], *, unit: str = "",
                editor: str = "", toolbar: str | None = None, add_label: str | None = None,
                extra_series: list[str] | None = None) -> str:
    add = add_label or f"Add a criterion for {var}"
    return (
        '<div class="card" style="padding: 16px">'
        + col(
            [
                var_header(var, status, unit, extra_series),
                toolbar if toolbar is not None else overlay_toolbar(var),
                plot_html,
                '<div style="display: flex; flex-direction: column; gap: 0">' + "".join(crits) + "</div>",
                editor,
                flex([btn(add, "", "", icon("plus", C["accent"], 14))]),
            ],
            gap=10,
        )
        + "</div>"
    )


def test_subheader(*, status: str = "fail", running: bool = False, name: str = "TubeToleranceTest",
                   drawer: str | None = None) -> str:
    left = flex(
        [
            btn("Suite", "", "Back to the suite page", icon("chev-l", C["accent"], 14)),
            btn("Tests", "outline" if drawer == "tests" else "", "Opens the test switcher beside the page (one drawer at a time)", icon("menu", C["accent"], 14)),
            btn("Variables", "outline" if drawer == "variables" else "", "Opens the variable browser beside the page: every variable in the result, filtered, with a checkbox per row for the selected plot", icon("grip", C["accent"], 14)),
            '<span style="width: 1px; height: 24px; background: #dddddd"></span>',
            f'<span class="h1">{PKG}.{name}</span>',
            badge("running", "running") if running else badge(status),
            '<span class="small text2" title="backend · last run · baseline">Dymola · ref_0007</span>' if running else '<span class="small text2" title="Ran 14 s ago on Dymola; baseline ref_0007 accepted 2026-09-12">Dymola · 14 s ago · ref_0007</span>',
        ],
        gap=10,
    )
    if running:
        actions = flex(
            [
                '<span class="why" title="Stop time, tolerance and output interval are locked until the run ends. Criteria stay editable and are rescored when the result arrives.">settings locked while running · criteria editable</span>',
                btn("Cancel this run", "danger", "Stops the simulation; the partial result is kept for inspection.", icon("stop", STATUS["fail"][1], 14)),
            ],
            gap=10,
        )
    else:
        actions = flex(
            [
                btn("Rerun this test (Dymola)", "outline", "Adds it to the queue. Runs with the 1 unsaved simulation edit applied."),
                btn("Accept this result as baseline", "outline", "Replaces ref_0007 with this run's result and saves the comparison settings with it."),
            ],
            gap=8,
        )
    return (
        '<div class="sub" style="position: sticky; top: 49px; z-index: 4; background: #fafafa; border-bottom: 1px solid #dddddd; padding: 8px 24px">'
        + flex([left, spacer(), actions], gap=16)
        + "</div>"
    )


def key_stats(*, running: bool = False) -> str:
    stats = [
        ("verdict", "FAIL" if not running else "—", "1 of 7 criteria failed" if not running else "waiting for result"),
        ("CPU time", "0.42 s" if not running else "—", "ref 0.40 s"),
        ("events", "12" if not running else "—", "ref 12"),
        ("samples", "1000 shown", "decimated from 5001"),
        ("stop time", "10 s", "tolerance 1e-6 · dassl"),
    ]
    cards = []
    for label, value, ref in stats:
        color = STATUS["fail"][1] if value == "FAIL" else C["text"]
        cards.append(
            f'<div class="stat"><div class="label">{label}</div>'
            f'<div class="value" style="color: {color}">{value}</div>'
            f'<div class="ref">{ref}</div></div>'
        )
    return flex(cards, gap=12)


def tree_card(*, collapsed: bool = True, running: bool = False) -> str:
    summary = "6 pass · 1 fail" if not running else "waiting for result"
    head = flex(
        [
            icon("chev-r" if collapsed else "chev-d", C["text2"]),
            '<span class="h2">Criteria tree</span>',
            '<span class="mono text2">and [7]</span>',
            f'<span class="small text2">{summary}</span>',
            spacer(),
            ibtn("plus", "Add a criterion under the root"),
            ibtn("wrap", "Wrap the root in a combinator"),
        ],
        gap=10,
    )
    return '<div class="card" style="padding: 10px 16px">' + head + "</div>"


def x_plot(**kw) -> str:
    return plot(
        actual=sine_off,
        reference=sine,
        legend=[("Actual", ACTUAL, "line"), ("Reference", REFERENCE, "dash")],
        **kw,
    )


TUBE_LEGEND = [("Actual", ACTUAL, "line"), ("Reference", REFERENCE, "dash"),
               ("Tube · x", PASS_FILL, "fill"), ("Tube points", HANDLE, "diamond")]


def tube_band(t):
    w = 0.05 + 0.05 * (1 - abs(t - 5) / 5)
    return (sine(t) - w, sine(t) + w)


def tube_band_rel(t):
    w = 0.03 + 0.06 * abs(sine(t))
    return (sine(t) - w, sine(t) + w)


def x_crits(open_: str | None = None, waiting: bool = False) -> list[str]:
    return [
        crit_row("fail", "nrmse", "x", "NRMSE 1.035e-2 · tolerance 1.0e-2", open_=open_ == "nrmse", waiting=waiting),
        crit_row("pass", "tube", "x", "100.0 % inside · band ± 0.05..0.10", open_=open_ == "tube", waiting=waiting),
        crit_row("pass", "range", "x", "max violation 0.000e+00 · [-1.05, 1.05]", open_=open_ == "range", waiting=waiting),
        crit_row("pass", "points", "x", "5 of 5 points matched", open_=open_ == "points", waiting=waiting, advisory=False),
    ]


# --- artboards ----------------------------------------------------------------


def suite_page() -> str:
    counters = []
    for key, label, value, active in [
        ("total", "total", "12", False), ("pass", "passed", "8", False), ("fail", "failed", "2", True),
        ("sim-fail", "sim failed", "1", False), ("no-ref", "no baseline", "1", False),
        ("running", "running", "1", False), ("queued", "queued", "2", False),
    ]:
        fill = "#555555" if key == "total" else STATUS[key][0]
        dark = C["text"] if key == "total" else STATUS[key][1]
        counters.append(
            f'<div class="counter{" active" if active else ""}" style="border-left-color: {fill}">'
            f'<div class="label">{label}</div><div class="value" style="color: {dark}">{value}</div></div>'
        )
    bar = (
        '<div class="bar">'
        f'<span style="width: 67%; background: {STATUS["pass"][0]}"></span>'
        f'<span style="width: 17%; background: {STATUS["fail"][0]}"></span>'
        f'<span style="width: 8%; background: {STATUS["sim-fail"][0]}"></span>'
        f'<span style="width: 8%; background: {STATUS["no-ref"][0]}"></span>'
        f'<span style="width: 9%; background: {ACTUAL}"></span>'
        "</div>"
    )
    toolbar = flex(
        [
            select("Grouped by package", 170, sm=True),
            select("Short names", 120, sm=True),
            f'<span class="input sm ph" style="width: 240px; gap: 6px">{icon("search", "#999999", 14)}filter tests by name or detail…</span>',
            '<span class="small text2">showing 12 of 12</span>',
            spacer(),
            '<span class="small" style="font-weight: 600; color: #1976D2">2 selected</span>',
            btn("Rerun 2 selected tests (Dymola)", "outline",
                "Adds them to the queue in order. Runs with the 1 unsaved simulation edit applied."),
            btn("Accept 2 selected results as baselines", "outline",
                "Replaces their reference results with the last run. Use after a change you meant to make."),
            btn("Clear selection", ""),
        ],
        gap=10,
    )
    hdr = row(
        '<th style="width: 28px"></th>', '<th style="width: 90px">status</th>', "<th>test</th>",
        "<th>detail</th>", '<th class="num" style="width: 80px">last run</th>',
        '<th style="width: 110px">baseline</th>',
    )
    filt = row(
        "<th></th>",
        f'<th>{inp("", 70, ph=True)}</th>', f'<th>{inp("", 200, ph=True)}</th>',
        f'<th>{inp("", 300, ph=True)}</th>', "<th></th>", "<th></th>",
    )
    tests = [
        ("fail", "TubeToleranceTest", "x: NRMSE 1.035e-2 > 1.0e-2", "14 s", "ref_0007", True, "running"),
        ("fail", "MetricTreeTest", "y: 2 of 5 points missed", "2 min", "ref_0003", True, ""),
        ("sim-fail", "RangeCheckTest", "Dymola: translation failed (see log)", "2 min", "ref_0009", False, ""),
        ("no-ref", "NoUnitTest", "no baseline · accept to create one", "2 min", "—", False, ""),
        ("pass", "SimpleTest", "", "2 min", "ref_0001", False, ""),
        ("pass", "ConstantTest", "", "2 min", "ref_0002", False, ""),
        ("pass", "EventTest", "", "—", "ref_0004", False, "queued"),
        ("pass", "FrequencyTest", "", "—", "ref_0005", False, "queued"),
        ("pass", "MultiFrequencyTest", "", "2 min", "ref_0006", False, ""),
        ("pass", "IntervalTest", "", "2 min", "ref_0008", False, ""),
        ("pass", "PointsCheckTest", "", "2 min", "ref_0010", False, ""),
        ("pass", "SimulateOnlyTest", "simulate only", "2 min", "—", False, ""),
    ]
    rows = [row(td(f"{PKG} · 12 tests", extra=' colspan="6"'), cls="group")]
    for status, name, detail, last, ref, sel, live in tests:
        _, dark, tint, _ = STATUS[status]
        if live == "running":
            st = '<span style="white-space: nowrap">' + badge("running") + f' <span class="small" style="color: {STATUS["running"][1]}">14 s</span></span>'
            bg = STATUS["running"][2]
        elif live == "queued":
            st = '<span style="white-space: nowrap">' + badge("queued") + ' <span class="small muted">was pass</span></span>'
            bg = "#ffffff"
        else:
            st = badge(status)
            bg = {"pass": "#f1f8e9", "fail": "#ffebee", "sim-fail": "#ffebee", "no-ref": "#fff8e1"}[status]
        if sel:
            bg = C["sel"]
        style = f' style="background: {bg}"'
        rows.append(
            "<tr>"
            + td(check(sel), extra=style)
            + td(st, extra=style)
            + td(f'<a href="#">{name}</a>', extra=style)
            + td(f'<span class="text2">{detail}</span>', extra=style)
            + td(last, "num", extra=style)
            + td(f'<span class="mono">{ref}</span>', extra=style)
            + "</tr>"
        )
    table = '<div class="card" style="overflow: hidden"><table><thead>' + hdr + filt + "</thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    list_card = col(
        [table, '<span class="small text2">Click a test to open it. The checkbox only collects tests for the actions above; it never changes what you are viewing.</span>'],
        gap=6, style="flex: 1 1 0; min-width: 0",
    )
    queue = queue_panel()
    body = col(
        [
            flex(counters, gap=10),
            bar,
            toolbar,
            flex([list_card, queue], gap=24, align="flex-start"),
        ],
        gap=16,
        style="padding: 24px",
    )
    return '<div style="width: 1440px; min-height: 1040px; background: #fafafa; box-sizing: border-box">' + header(page="suite") + body + "</div>"


def queue_panel() -> str:
    def qrow(pos: str, status_html: str, name: str, note: str, actions: list[str], bg: str = "#ffffff") -> str:
        return (
            f'<div style="display: flex; flex-direction: row; align-items: center; gap: 10px; padding: 6px 10px; border-top: 1px solid #eeeeee; background: {bg}">'
            + f'<span class="mono muted" style="width: 20px; text-align: right">{pos}</span>'
            + status_html
            + f'<div style="display: flex; flex-direction: column; gap: 1px; min-width: 0; flex: 1 1 0"><a href="#" class="small" style="font-weight: 600">{name}</a><span class="small text2">{note}</span></div>'
            + flex(actions, gap=4)
            + "</div>"
        )

    head = flex(
        [
            '<span class="h2">Simulation queue</span>',
            '<span class="small text2">Dymola · this process</span>',
            spacer(),
            '<span class="small text2">workers</span>',
            inp("4", 44),
            '<span class="small muted">of 4 in testing.json</span>',
        ],
        gap=8,
    )
    rows = [
        qrow("1", badge("running"), "TubeToleranceTest", "running · 14 s · worker 2",
             [btn("Cancel", "danger", "Stops this simulation. Keeps the partial result.")], STATUS["running"][2]),
        qrow("2", badge("queued"), "EventTest", "waiting · 1 ahead",
             [btn("Remove", "", "Takes it out of the queue before it starts.")]),
        qrow("3", badge("queued"), "FrequencyTest", "waiting · 2 ahead · 1 unsaved simulation edit applies",
             [btn("Remove", "", "Takes it out of the queue before it starts.")]),
        qrow("", badge("pass"), "SimpleTest", "finished · 9 s · rescored: pass",
             [btn("Open test", ""), btn("Accept as baseline", "outline")]),
        qrow("", badge("sim-fail"), "RangeCheckTest", "failed · Dymola: translation failed",
             [btn("Open test", ""), btn("Show log", "")]),
    ]
    foot = col(
        [
            flex([btn("Clear finished", ""), spacer(), '<span class="small text2">numbered in run order · first in, first out · a test is never queued twice</span>'], gap=8),
            '<span class="why">The queue lives in memory. Stop server asks first while it is not empty.</span>',
        ],
        gap=4,
    )
    return (
        '<div class="card" style="width: 420px; flex: none; padding: 12px 0 10px">'
        + col([f'<div style="padding: 0 12px">{head}</div>', "".join(rows), f'<div style="padding: 8px 12px 0">{foot}</div>'], gap=8)
        + "</div>"
    )


def queue_indicator_board() -> str:
    def case(title: str, ind: str, note: str) -> str:
        return col(
            [f'<span class="label">{title}</span>',
             f'<div class="header" style="position: static; padding: 8px 16px; border: 1px solid #dddddd; border-radius: 4px">{ind}</div>',
             f'<span class="small text2">{note}</span>'],
            gap=6,
        )
    body = col(
        [
            '<span class="h2">Header queue indicator · three states</span>',
            case("idle", queue_indicator("idle"), "Nothing queued. Clicking it opens the Suite page's queue panel."),
            case("running", queue_indicator("running"), "Progress is the running item's elapsed share of its last duration. Counts update as items finish."),
            case("failed", queue_indicator("failed"), "Stays red until the failed row is cleared or opened."),
        ],
        gap=16,
        style="padding: 24px",
    )
    return '<div style="width: 520px; min-height: 360px; background: #fafafa; box-sizing: border-box">' + body + "</div>"


def test_page() -> str:
    variables = col(
        [
            flex(
                [
                    '<span class="h2">Plots</span>',
                    '<span class="small text2">3 plots · one per tracked variable</span>',
                    spacer(),
                    btn("Add plot", "", "A new plot with any variable or expression from the result. Plots are independent of the tracked variables: add, remove, or put several series on one.", icon("plus", C["accent"], 14)),
                ],
                gap=10,
            ),
            var_section("x", "fail", x_plot(), x_crits()),
            var_section(
                "v", "pass",
                plot(actual=decay_off, reference=decay, legend=[("Actual", ACTUAL, "line"), ("Reference", REFERENCE, "dash")]),
                [crit_row("pass", "nrmse", "v", "NRMSE 3.2e-5 · tolerance 1.0e-4"),
                 crit_row("pass", "event-timing", "v", "12 of 12 events within 0.01 s")],
            ),
            var_section(
                "y", "pass",
                plot(actual=ramp, reference=ramp, yr=(-0.25, 1.25), legend=[("Actual", ACTUAL, "line"), ("Reference", REFERENCE, "dash")]),
                [crit_row("pass", "scalar", "y", "final 1.000 · ref 1.000 · tolerance 1e-3 abs")],
            ),
        ],
        gap=12,
    )
    body = col([key_stats(), tree_card(), variables], gap=16, style="padding: 16px 24px 24px")
    return '<div style="width: 1440px; background: #fafafa; box-sizing: border-box">' + header(page="test") + test_subheader() + body + "</div>"


def tube_editor() -> str:
    fields = flex(
        [
            col(['<span class="label">width mode</span>', select("band (± offset)", 150, sm=True)], gap=2),
            col(['<span class="label">symmetry</span>', select("synced (upper = lower)", 170, sm=True)], gap=2),
            col(['<span class="label">interpolation</span>', select("linear", 100, sm=True)], gap=2),
            col(['<span class="label">min width</span>', inp("0", 70)], gap=2),
            col(['<span class="label">window</span>', flex([inp("1.0", 70), '<span class="small text2">to</span>', inp("8.0", 70)], gap=4)], gap=2),
            spacer(),
            col(['<span class="label">live</span>', f'<span style="font-weight: 600; color: {STATUS["pass"][1]}">PASS · 100.0 % inside</span>'], gap=2),
        ],
        gap=16, align="flex-end",
    )
    tbl = (
        '<table style="width: 420px"><thead>'
        + row("<th>time</th>", '<th class="num">width (signal units)</th>', '<th style="width: 36px"></th>')
        + "</thead><tbody>"
        + "".join(
            row(td(inp(t, 90)), td(inp(w, 110), "num"), td(ibtn("close", "Delete this point")))
            for t, w in [("0", "0.05"), ("5", "0.10"), ("10", "0.05")]
        )
        + "</tbody></table>"
    )
    return (
        '<div class="editor">'
        + col(
            [
                flex(
                    [
                        f'<span class="hint">{icon("grip", C["text2"], 14)} Drag a ring to move a point · Shift+click the plot adds one · Shift+right-click deletes the one under the cursor · Set window starts the brush</span>',
                        spacer(),
                        btn("Reset", "", "Restores this criterion's parameters and window as saved.", icon("reset", C["accent"], 14)),
                        btn("Done", "outline", "Closes the editor. Edits are already staged; Save writes them."),
                    ],
                    gap=8,
                ),
                fields,
                flex(
                    [
                        col(['<span class="label">control points · 3 · band</span>', tbl,
                             flex([btn("Add point", "", "", icon("plus", C["accent"], 14)),
                                   btn("Seed from reference", "", "Places control points at the reference's extrema.")], gap=4)], gap=4),
                    ],
                    gap=24, align="flex-start",
                ),
            ],
            gap=10,
        )
        + "</div>"
    )


def test_tube_page() -> str:
    p = plot(
        actual=sine_off, reference=sine, band=tube_band, window=(1.0, 8.0),
        rings=[(0, 1.05 - 1.0), (5, sine(5) + 0.10), (10, sine(10) + 0.05)],
        legend=TUBE_LEGEND,
        hint="editing tube · x — rings are the control points",
    )
    crits = x_crits(open_="tube")
    # the editor sits directly under the open row; the rows after it follow the editor
    sec = var_section("x", "fail", p, crits[:2], editor=tube_editor() + "".join(crits[2:]),
                      toolbar=overlay_toolbar("x", window=True))
    focus = flex(
        [
            '<span class="h2">Plots</span>',
            '<span class="small text2">3 plots · focused on x</span>',
            btn("Show all 3", ""),
            spacer(),
            btn("Add plot", "", "A new plot with any variable or expression from the result. Plots are independent of the tracked variables: add, remove, or put several series on one.", icon("plus", C["accent"], 14)),
        ],
        gap=10,
    )
    body = col([key_stats(), tree_card(), focus, sec], gap=16, style="padding: 16px 24px 24px")
    return '<div style="width: 1440px; background: #fafafa; box-sizing: border-box">' + header(page="test") + test_subheader() + body + "</div>"


def points_editor() -> str:
    rows = [
        ("0", "ref 0.000", "0.001", "abs", "0.00", "pass", "match · Δ 1.2e-4"),
        ("1.5708", "1.0", "0.05", "rel", "0.00", "pass", "match · Δ 0.012"),
        ("3.1416", "ref 0.000", "0.02", "abs", "0.05", "pass", "match · Δ 0.006"),
        ("4.7124", "-1.0", "0.05", "rel", "0.00", "fail", "miss · Δ 0.071 (tol 0.05)"),
        ("10", "ref -0.544", "0.01", "abs", "0.00", "pass", "match · Δ 0.003"),
    ]
    tbl = (
        '<table style="width: 760px"><thead>'
        + row("<th>time</th>", "<th>value</th>", '<th class="num">tolerance</th>', "<th>mode</th>",
              '<th class="num">time tolerance</th>', "<th>match (live)</th>", '<th style="width: 36px"></th>')
        + "</thead><tbody>"
        + "".join(
            row(td(inp(t, 80)), td(inp(v, 90) if not v.startswith("ref") else f'<span class="mono muted">{v}</span>'),
                td(inp(tol, 70), "num"), td(select(mode, 70, sm=True)), td(inp(tt, 70), "num"),
                td(pill(st, "match" if st == "pass" else "miss") + f' <span class="mono text2">{note.split(" · ")[1]}</span>'),
                td(ibtn("close", "Delete this point")))
            for t, v, tol, mode, tt, st, note in rows
        )
        + "</tbody></table>"
    )
    return (
        '<div class="editor">'
        + col(
            [
                flex(
                    [
                        f'<span class="hint">{icon("grip", C["text2"], 14)} Drag a diamond to move a point (value only when it is explicit) · drag a box edge to change its tolerance · Shift+click adds · Shift+right-click deletes</span>',
                        spacer(),
                        btn("Reset", "", "", icon("reset", C["accent"], 14)),
                        btn("Done", "outline"),
                    ],
                    gap=8,
                ),
                flex(
                    [
                        col(['<span class="label">window</span>', flex([inp("0", 70), '<span class="small text2">to</span>', inp("10", 70)], gap=4)], gap=2),
                        spacer(),
                        col(['<span class="label">live</span>', f'<span style="font-weight: 600; color: {STATUS["fail"][1]}">FAIL · 4 of 5 points matched</span>'], gap=2),
                    ],
                    gap=16, align="flex-end",
                ),
                tbl,
                flex(
                    [
                        btn("Add point", "", "", icon("plus", C["accent"], 14)),
                        btn("Snapshot from reference", "", "Fills every blank value with the reference at that time and makes it explicit.", icon("camera", C["accent"], 14)),
                        '<span class="why">switching abs ↔ rel converts the tolerance so the box does not move</span>',
                    ],
                    gap=8,
                ),
            ],
            gap=10,
        )
        + "</div>"
    )


def test_points_page() -> str:
    p = plot(
        actual=sine_off, reference=sine,
        diamonds=[(0, 0, 0.12, 0.05), (1.5708, 1.0, 0.12, 0.05), (3.1416, 0, 0.12, 0.05),
                  (4.7124, -1.0, 0.12, 0.05), (10, sine(10), 0.12, 0.05)],
        legend=[("Actual", ACTUAL, "line"), ("Reference", REFERENCE, "dash"), ("Points · x", HANDLE, "diamond")],
        hint="editing points · x — diamonds are the targets, boxes their tolerance",
    )
    crits = [
        crit_row("fail", "nrmse", "x", "NRMSE 1.035e-2 · tolerance 1.0e-2"),
        crit_row("pass", "tube", "x", "100.0 % inside · band ± 0.05..0.10"),
        crit_row("pass", "range", "x", "max violation 0.000e+00 · [-1.05, 1.05]"),
        crit_row("fail", "points", "x", "4 of 5 points matched", open_=True),
    ]
    sec = var_section("x", "fail", p, crits, editor=points_editor())
    focus = flex(
        ['<span class="h2">Plots</span>', '<span class="small text2">3 plots · focused on x</span>', btn("Show all 3", ""), spacer(),
         btn("Add plot", "", "A new plot with any variable or expression from the result. Plots are independent of the tracked variables: add, remove, or put several series on one.", icon("plus", C["accent"], 14))],
        gap=10,
    )
    body = col([key_stats(), tree_card(), focus, sec], gap=16, style="padding: 16px 24px 24px")
    return '<div style="width: 1440px; background: #fafafa; box-sizing: border-box">' + header(page="test") + test_subheader() + body + "</div>"


def test_running_page() -> str:
    p = plot(actual=sine_off, reference=sine, actual_until=4.2, running_marker=4.2,
             legend=[("Actual (live)", ACTUAL, "line"), ("Reference", REFERENCE, "dash")])
    sec = var_section("x", "pending", p, x_crits(waiting=True))
    p2 = plot(actual=decay_off, reference=decay, actual_until=4.2,
              legend=[("Actual (live)", ACTUAL, "line"), ("Reference", REFERENCE, "dash")])
    sec2 = var_section("v", "pending", p2, [crit_row("pass", "nrmse", "v", "", waiting=True),
                                              crit_row("pass", "event-timing", "v", "", waiting=True)])
    note = flex(
        [
            '<span class="h2">Plots</span>',
            '<span class="small text2">3 plots · live from Dymola · refreshed every second while this page is open</span>',
            spacer(),
            btn("Add plot", "", "A new plot with any variable or expression from the result. Plots are independent of the tracked variables: add, remove, or put several series on one.", icon("plus", C["accent"], 14)),
        ],
        gap=10,
    )
    body = col([key_stats(running=True), tree_card(running=True), note, sec, sec2], gap=16, style="padding: 16px 24px 24px")
    return '<div style="width: 1440px; background: #fafafa; box-sizing: border-box">' + header(page="test") + test_subheader(running=True) + body + "</div>"


RAMP = ["#90caf9", "#42a5f5", "#1976D2", "#0d47a1"]  # one hue, lightness by index


def variables_drawer() -> str:
    def toggle(text: str, on: bool, title: str) -> str:
        st = f"border-color: {C['accent']}; background: {C['sel']}; color: {C['accent']}" if on else "color: #666666"
        return f'<span class="chip mono" title="{title}" style="height: 20px; cursor: pointer; {st}">{text}</span>'

    filt = flex(
        [
            f'<span class="input sm" style="flex: 1 1 0; gap: 6px; min-width: 0">{icon("search", "#999999", 14)}<span class="mono">pipe.T[</span></span>',
            toggle(".*", False, "Regular expression (off: substring, or glob with * and ?; [ ] match literally)"),
            toggle("Aa", False, "Match case"),
        ],
        gap=4,
    )

    def vrow(name: str, *, checked: bool = False, tag: str = "", others: str = "", indent: int = 0,
             array: str = "", hover: bool = False) -> str:
        t = f'<span class="chip" style="height: 15px; font-size: 10px">{tag}</span>' if tag else ""
        dots = f'<span class="small muted" title="also on {others}">· {others}</span>' if others else ""
        arr = ""
        if array:
            arr = flex(
                ['<span class="label">indices</span>', inp(array, 86), '<span class="why">start:step:stop or a list</span>'],
                gap=6, style=f"padding: 2px 0 4px {30 + indent}px",
            )
        bg = C["sel"] if checked else "#ffffff"
        newp = f'<span class="small" style="color: {C["accent"]}">new plot</span>' if hover else ""
        return (
            f'<div style="display: flex; flex-direction: column; background: {bg}">'
            + flex([check(checked), f'<span class="mono" style="flex: 1 1 0; min-width: 0; overflow: hidden; text-overflow: ellipsis">{name}</span>', t, dots, newp],
                   gap=6, style=f"padding: 4px 10px 4px {10 + indent}px; min-height: 26px")
            + arr
            + "</div>"
        )

    def group(title: str, rows: list[str], *, open_: bool = True) -> str:
        head = flex([icon("chev-d" if open_ else "chev-r", C["text2"], 12), f'<span class="small" style="font-weight: 600; color: #555555">{title}</span>'],
                    gap=4, style="padding: 6px 10px 2px")
        return head + ("".join(rows) if open_ else "")

    rows = [
        group("tracked · 3", [
            vrow("x", checked=True, tag="tracked"),
            vrow("v", tag="tracked", others="plot 2"),
            vrow("y", tag="tracked", others="plot 3"),
        ]),
        group("pipe · 61", [
            vrow("T[1..20]", indent=12, array="1:5:20", hover=True),
            vrow("m_flow[1..21]", indent=12),
            vrow("p[1..20]", indent=12),
        ]),
        group("other · 4", [
            vrow("der(x)", checked=True, tag="plot only"),
            vrow("der(v)"),
            vrow("source.y"),
            vrow("integrator.y"),
        ]),
        group("diagnostics · 2", [vrow("CPUtime", tag="diagnostic"), vrow("EventCounter", tag="diagnostic")], open_=False),
    ]
    head = col(
        [
            flex(['<span class="h2">Variables</span>', spacer(), ibtn("close", "Close the drawer; plots widen again")], gap=6),
            filt,
            '<span class="small text2">70 of 4,213 match · 3 tracked · arrays folded by index</span>',
            f'<div class="card" style="padding: 6px 8px; background: {C["sel"]}; border-color: #90caf9">'
            + col(['<span class="label">selected plot · 1 of 3</span>',
                   '<span class="small">checkbox = on this plot · hover a row for <span style="color: #1976D2">new plot</span> · click a plot header to select it</span>'], gap=2)
            + "</div>",
        ],
        gap=8, style="padding: 12px 10px 6px",
    )
    return (
        '<div class="card" style="width: 320px; flex: none; padding: 0; overflow: hidden; align-self: stretch">'
        + head
        + '<div style="display: flex; flex-direction: column">' + "".join(rows) + "</div>"
        + f'<div style="padding: 10px"><span class="why">Later: a profile plot kind, index on the x axis at chosen times, from the same series model. A criterion named pipe.T[*] scores every index under one leaf.</span></div>'
        + "</div>"
    )


def plot_another_page() -> str:
    W = 1440 - 48 - 320 - 24 - 32  # page padding, drawer, gap, card padding
    two = plot(width=W, actual=sine_off, reference=sine,
               legend=[("x · actual", ACTUAL, "line"), ("x · reference", REFERENCE, "dash"), ("der(x) · actual", "#7B1FA2", "line")])
    two = two.replace("</svg>", f'<path d="{_der_path(W)}" fill="none" stroke="#7B1FA2" stroke-width="2"/></svg>')
    crits = [crit_row("fail", "nrmse", "x", "NRMSE 1.035e-2 · tolerance 1.0e-2"),
             crit_row("pass", "tube", "x", "100.0 % inside · band ± 0.05..0.10")]
    selected = var_section("x", "fail", two, crits, extra_series=["der(x)"],
                           editor=flex(['<span class="why">der(x) is plot only: it has no reference and no criteria.</span>',
                                        btn("Track der(x) and add a criterion", "outline", "Adds der(x) to this test's variables and opens a new nrmse criterion for it; the next Accept stores its reference.")], gap=10))
    selected = selected.replace('<div class="card" style="padding: 16px">', f'<div class="card" style="padding: 16px; box-shadow: 0 0 0 2px {C["accent"]} inset">', 1)
    # a family plot: pipe.T[1:5:20], one hue ramped by index, legend collapsed
    import math as _m
    fam = plot(width=W, height=260, yr=(280, 360), actual=None, reference=None,
               legend=[("pipe.T[1:5:20]", RAMP[2], "line")])
    L, R, T, B = 64, 24, 16, 40
    pw, ph = W - L - R, 260 - T - B
    paths = []
    for k, idx in enumerate([1, 6, 11, 16]):
        pts = []
        for i in range(201):
            t = i * 10 / 200
            y = 300 + 40 * (1 - _m.exp(-0.4 * t)) * (1 - idx / 24) + 4 * _m.sin(t + idx)
            pts.append((L + t / 10 * pw, T + (360 - y) / 80 * ph))
        paths.append(f'<path d="M{" L".join(f"{x:.1f} {y:.1f}" for x, y in pts)}" fill="none" stroke="{RAMP[k]}" stroke-width="2"/>')
    ramp_legend = (
        f'<g><rect x="{L+14}" y="{T+30}" width="6" height="10" fill="{RAMP[0]}"/><rect x="{L+20}" y="{T+30}" width="6" height="10" fill="{RAMP[1]}"/>'
        f'<rect x="{L+26}" y="{T+30}" width="6" height="10" fill="{RAMP[2]}"/><rect x="{L+32}" y="{T+30}" width="6" height="10" fill="{RAMP[3]}"/>'
        f'<text x="{L+44}" y="{T+39}" fill="#444444" font-size="10">index 1 → 16 · hover names the node</text></g>'
    )
    fam = fam.replace("</svg>", "".join(paths) + ramp_legend + "</svg>")
    fam_card = (
        '<div class="card" style="padding: 16px">'
        + col(
            [
                flex([
                    '<span class="h2" style="color: #333333">pipe.T[1:5:20]</span>',
                    '<span class="chip">plot only · 4 of 20 nodes</span>',
                    '<span style="width: 8px"></span>', '<span class="label">series</span>',
                    '<span class="chip on"><span class="mono">pipe.T[1:5:20]</span> <span class="chip" style="height: 14px; font-size: 10px">array</span></span>',
                    btn("Add series", "", "", icon("plus", C["accent"], 14)),
                    spacer(),
                    btn("Plot against index", "", "Later: a profile plot, index on the x axis at chosen times."),
                    ibtn("close", "Remove this plot"),
                ], gap=8),
                fam,
                '<span class="why">One hue for the family, lightness by index; the legend folds to one entry. Track pipe.T[*] to score every node under one criterion.</span>',
            ],
            gap=10,
        )
        + "</div>"
    )
    plots = col(
        [
            flex(['<span class="h2">Plots</span>', '<span class="small text2">4 plots · plot 1 selected · drawer open, plots narrowed</span>', spacer(),
                  btn("Add plot", "outline", "An empty plot, selected; pick its series in the drawer.", icon("plus", C["accent"], 14))], gap=10),
            selected,
            fam_card,
        ],
        gap=12, style="flex: 1 1 0; min-width: 0",
    )
    body = flex([variables_drawer(), plots], gap=24, align="flex-start", style="padding: 16px 24px 24px")
    return '<div style="width: 1440px; background: #fafafa; box-sizing: border-box">' + header(page="test") + test_subheader(drawer="variables") + body + "</div>"


def _der_path(W: int = 1360) -> str:
    """cos(t) on the same axes as x_plot (0..10, -1.25..1.25, W x 320)."""
    import math as _m
    L, R, T, B, H = 64, 24, 16, 40, 320
    pw, ph = W - L - R, H - T - B
    pts = []
    for i in range(201):
        t = i * 10 / 200
        pts.append((L + t / 10 * pw, T + (1.25 - _m.cos(t)) / 2.5 * ph))
    return "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in pts)


def startup_page() -> str:
    progress = (
        '<div class="card" style="padding: 12px 16px">'
        + col(
            [
                flex(['<span class="h2">Opening ModelicaTestingLib on Dymola</span>', spacer(), '<span class="small text2">0.8 s</span>'], gap=10),
                '<div class="progress"><span style="width: 45%"></span></div>',
                flex(
                    [
                        f'<span class="small">{icon("check", STATUS["pass"][0], 14)} testing.json read</span>',
                        f'<span class="small" style="color: {STATUS["running"][1]}; font-weight: 600">scanning library… 9 of about 12 tests found (cached scan from 2026-09-14 in use)</span>',
                        '<span class="small muted">reading results · scoring · pending</span>',
                    ],
                    gap=16,
                ),
            ],
            gap=8,
        )
        + "</div>"
    )
    hdr = row('<th style="width: 28px"></th>', '<th style="width: 90px">status</th>', "<th>test</th>", "<th>detail</th>",
              '<th class="num" style="width: 80px">last run</th>', '<th style="width: 110px">baseline</th>')
    found = [("SimpleTest", "pass", "2 min", "ref_0001"), ("ConstantTest", "pass", "2 min", "ref_0002"),
             ("MetricTreeTest", "fail", "2 min", "ref_0003"), ("EventTest", "", "", ""), ("FrequencyTest", "", "", ""),
             ("MultiFrequencyTest", "", "", ""), ("TubeToleranceTest", "", "", ""), ("IntervalTest", "", "", ""),
             ("RangeCheckTest", "", "", "")]
    rows = [row(td(f"{PKG} · 9 so far", extra=' colspan="6"'), cls="group")]
    for name, st, last, ref in found:
        if st:
            cell = badge(st)
            bg = {"pass": "#f1f8e9", "fail": "#ffebee"}[st]
            detail = "" if st == "pass" else '<span class="text2">y: 2 of 5 points missed</span>'
            link = f'<a href="#">{name}</a>'
        else:
            cell = '<span class="pill" style="background: #f5f5f5; color: #888888; border-color: #dddddd">scoring…</span>'
            bg = "#ffffff"
            detail = ""
            link = f'<span class="muted">{name}</span>'
        s = f' style="background: {bg}"'
        rows.append("<tr>" + td(check(), extra=s) + td(cell, extra=s) + td(link, extra=s) + td(detail, extra=s)
                    + td(last, "num", extra=s) + td(f'<span class="mono">{ref}</span>', extra=s) + "</tr>")
    table = '<div class="card" style="overflow: hidden"><table><thead>' + hdr + "</thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    toolbar = flex(
        [
            select("Grouped by package", 170, sm=True), select("Short names", 120, sm=True),
            f'<span class="input sm ph" style="width: 240px; gap: 6px">{icon("search", "#999999", 14)}filter tests…</span>',
            spacer(),
            btn("Rerun 0 selected tests", "outline disabled"), btn("Accept 0 selected results", "outline disabled"),
            '<span class="why">batch actions unlock when loading finishes</span>',
        ],
        gap=10,
    )
    body = col(
        [progress, toolbar, flex([col([table, '<span class="small text2">A test opens as soon as it is scored.</span>'], gap=6, style="flex: 1 1 0; min-width: 0"),
                                  '<div class="card" style="width: 420px; flex: none; padding: 12px"><span class="h2">Simulation queue</span><div class="small text2" style="margin-top: 6px">empty · available now</div></div>'],
                                 gap=24, align="flex-start")],
        gap=16, style="padding: 24px",
    )
    return '<div style="width: 1440px; min-height: 760px; background: #fafafa; box-sizing: border-box">' + header(queue="idle", edits=0, page="suite") + body + "</div>"


def backend_page() -> str:
    menu = (
        '<div class="card" style="width: 360px; padding: 6px 0; box-shadow: 0 2px 8px rgba(0,0,0,0.1)">'
        + col(
            [
                '<div style="padding: 4px 12px" class="label">backend for this project</div>',
                f'<div style="display: flex; align-items: center; gap: 8px; padding: 6px 12px; background: {C["sel"]}">{icon("check", C["accent"], 14)}<span style="font-weight: 600">Dymola</span><span class="small text2">2026x · 12 results · 11 baselines</span></div>',
                '<div style="display: flex; align-items: center; gap: 8px; padding: 6px 12px 6px 34px; opacity: 0.55"><span>OpenModelica</span><span class="small text2">1.27 · 9 results · 9 baselines</span></div>',
                '<div style="display: flex; align-items: center; gap: 8px; padding: 6px 12px 6px 34px; opacity: 0.55"><span>FMPy</span><span class="small text2">no results yet</span></div>',
                '<div style="padding: 2px 12px 6px 34px"><span class="why">switching reopens the project on that backend — refused now: 3 unsaved edits, 1 run in flight</span></div>',
                '<div class="rule"></div>',
                f'<div style="display: flex; align-items: center; gap: 8px; padding: 8px 12px">{icon("ext", C["accent"], 14)}<a href="#">Open in another backend…</a><span class="small text2">a second tool, another tab</span></div>',
            ],
            gap=2,
        )
        + "</div>"
    )
    dialog = (
        '<div class="card" style="width: 520px; padding: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.1)">'
        + col(
            [
                '<span class="h2" style="color: #333333">Open ModelicaTestingLib in another backend</span>',
                flex([col(['<span class="label">backend</span>', select("OpenModelica", 200)], gap=2),
                      col(['<span class="label">port</span>', inp("8081", 80, sm=False)], gap=2)], gap=16, align="flex-end"),
                '<span class="small text2">Starts a second dstf author process on that backend and opens it in a new browser tab. One project and one backend per process; each keeps its own queue and pending edits. Both save to the same test_spec.json, and Save refuses when the file changed on disk.</span>',
                flex([spacer(), btn("Cancel", ""), btn("Open in new tab", "primary")], gap=8),
            ],
            gap=14,
        )
        + "</div>"
    )
    body = flex(
        [col(['<span class="label">the backend control in the header, open</span>', menu], gap=8),
         col(['<span class="label">Open in another backend…</span>', dialog], gap=8)],
        gap=48, align="flex-start", style="padding: 24px",
    )
    return '<div style="width: 1440px; min-height: 520px; background: #fafafa; box-sizing: border-box">' + header(page="suite") + body + "</div>"


def build_all() -> None:
    write("Main", suite_page())
    write("QueueIndicator", queue_indicator_board())
    write("Test", test_page())
    write("TestTube", test_tube_page())
    write("TestPoints", test_points_page())
    write("TestRunning", test_running_page())
    write("PlotAnother", plot_another_page())
    write("Startup", startup_page())
    write("Backend", backend_page())
