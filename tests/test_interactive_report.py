"""§4.4's kept list, asserted with Playwright on REAL generated pages.

Every page here comes out of ``compare_test`` + ``generate_comparison_plots``
— the path the CLI takes — not a hand-built Jinja context. That is the point:
this file is the safety net the deletion commit (sub-project 2, "the cut") is
checked against, so it has to describe behaviour a user can see, not the shape
of a fixture.

Three of these could only hold after the cut: the metric tree is read-only,
the decimation banner names the traces it decimated, and no JavaScript
computes a verdict. They were ``xfail(strict=True)`` while it was pending.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.playwright

N_FULL = 3001  # over the cap -> decimated
N_SMALL = 101  # under the cap -> untouched
MAX_EMBEDDED = 500
EVENT_T = 1.0  # duplicate timestamp in the shared reference time array
EVENT_LATE = 1.0005  # the actual reproduces it 0.5 ms late

#: Leaf path -> the variable it scores, mirroring ``_SPEC`` below. Used by the
#: tests to name what they assert on without re-walking the spec.
LEAF_VARIABLES = {
    "/metrics/children/0": "x",  # tube, banded, windowed 0.2..0.8
    "/metrics/children/1/children/0": "x",  # nrmse under warn -> advisory
    "/metrics/children/2": "y",  # points, one pass one fail
    "/metrics/children/3": "z",  # range, max_value breached
    "/metrics/children/4": "e",  # event-timing
    "/metrics/children/5": "f",  # dominant-frequency, one peak unmatched
    "/metrics/children/6": "s",  # scalar, max reduction against the baseline
}

_SPEC = {
    "combinator": "and",
    "children": [
        {
            "metric": "tube",
            "variable": "x",
            "tube_width_mode": "band",
            "tube_points": [
                {"time": 0.0, "upper": 0.05, "lower": 0.05},
                {"time": 10.0, "upper": 0.2, "lower": 0.2},
            ],
            "window": {"start": 0.2, "end": 0.8},
        },
        {
            "combinator": "warn",
            "children": [{"metric": "nrmse", "variable": "x", "tolerance": 1e-12}],
        },
        {
            "metric": "points",
            "variable": "y",
            "points": [
                {"time": 2.0, "tolerance": 0.5},
                {"time": 6.0, "value": 99.0, "tolerance": 0.01},
            ],
        },
        {"metric": "range", "variable": "z", "max_value": 0.5},
        {"metric": "event-timing", "variable": "e", "time_tolerance": 1e-3},
        {
            "metric": "dominant-frequency",
            "variable": "f",
            "peaks": [
                {"freq": 1.0, "tolerance": 0.1, "tolerance_mode": "rel"},
                {"freq": 7.0, "tolerance": 0.02, "tolerance_mode": "rel"},
            ],
        },
        {
            "metric": "scalar",
            "variable": "s",
            "quantity": {"reduce": "max"},
            "tolerance": 0.01,
        },
    ],
}


# ---------------------------------------------------------------------------
# Fixture construction
# ---------------------------------------------------------------------------


def _reference_time() -> np.ndarray:
    """The ONE shared reference time array, carrying the event at t=1.0.

    A reference file has a single ``time`` list for every variable, so the
    duplicate timestamp that marks an event lives here and every reference
    variable's values array is built on it. Keeping it under the cap (and
    the actual trajectories over it) is what lets ``s`` be the one variable
    that is not decimated on either side.
    """
    t = np.linspace(0.0, 10.0, N_SMALL)
    i = int(np.argmin(np.abs(t - EVENT_T)))
    return np.insert(t, i + 1, t[i])


def _actual_time(n: int, *, event: bool = False) -> np.ndarray:
    t = np.linspace(0.0, 10.0, n)
    if not event:
        return t
    i = int(np.searchsorted(t, EVENT_LATE))
    return np.insert(t, i, [EVENT_LATE, EVENT_LATE])


def _signal(name: str, t: np.ndarray) -> np.ndarray:
    if name == "x":
        return np.sin(t)
    if name == "y":
        return 0.5 * t
    if name == "z":
        return 0.1 * t
    if name == "e":
        return np.where(t < EVENT_T, 0.0, 1.0)
    if name == "f":
        return np.sin(2 * np.pi * t) + 0.3 * np.sin(2 * np.pi * 3.0 * t)
    if name == "s":
        return np.cos(t)
    raise KeyError(name)


def _generate_compared(out_dir: Path):
    """One real test through the real path. Returns ``(html, comparison)``."""
    from dstf.backends.types import TestResult, VariableResult
    from dstf.comparison.comparator import compare_test
    from dstf.comparison.tree_spec import parse_metric_tree
    from dstf.discovery.test_registry import TestModel
    from dstf.reporting.overlay_loader import Overlay, OverlayVariable
    from dstf.reporting.report_suite import generate_comparison_plots

    names = ["x", "y", "z", "e", "f", "s"]
    ref_time = _reference_time()
    ref_values = {n: _signal(n, ref_time) for n in names}
    ref = {
        "test_id": "0001",
        "time": ref_time.tolist(),
        "variables": [
            {"index": i + 1, "name": n, "values": ref_values[n].tolist()}
            for i, n in enumerate(names)
        ],
        "statistics": {"simulation": {"cpu_time": 1.0}},
    }

    act_time, act_values = {}, {}
    for name in names:
        if name == "s":
            t = _actual_time(N_SMALL)
        elif name == "e":
            t = _actual_time(N_FULL, event=True)
        else:
            t = _actual_time(N_FULL)
        v = _signal(name, t)
        if name == "x":
            v = v + 0.01  # inside the tube, outside a 1e-12 NRMSE
        elif name == "z":
            v = v * 1.5  # breaches max_value
        elif name == "e":
            v = np.where(t < EVENT_LATE, 0.0, 1.0)
        act_time[name], act_values[name] = t, v

    diag_time = _actual_time(N_SMALL)
    result = TestResult(
        model_id="TestLib.Kept.Fixture",
        success=True,
        variables=[
            VariableResult(index=i + 1, name=n, time=act_time[n], values=act_values[n])
            for i, n in enumerate(names)
        ],
        diagnostics=[
            VariableResult(
                index=1,
                name="CPUtime",
                time=diag_time,
                values=np.linspace(0.0, 2.0, len(diag_time)),
            )
        ],
        statistics={"simulation": {"cpu_time": 1.2}},
    )

    test = TestModel(
        model_id="TestLib.Kept.Fixture",
        source_file=Path("fixture.mo"),
        source_package="TestLib",
        short_name="Fixture",
        n_vars=len(names),
        metric_tree_spec=parse_metric_tree(_SPEC),
    )
    comp = compare_test(test, result, ref)

    test_dir = out_dir / "testdir"
    test_dir.mkdir(parents=True, exist_ok=True)
    (test_dir / "dslog.txt").write_text("simulation log\n", encoding="utf-8")

    overlays = [
        Overlay(
            name="alt_solver",
            role="soft_check",
            variables={
                "x": OverlayVariable(
                    time=ref_time.tolist(),
                    values=(ref_values["x"] * 1.02).tolist(),
                )
            },
        ),
        Overlay(
            name="vendor",
            role="companion",
            kind="external",
            variables={
                "x": OverlayVariable(
                    time=ref_time.tolist(),
                    values=(ref_values["x"] * 0.98).tolist(),
                )
            },
        ),
    ]

    html = generate_comparison_plots(
        "TestLib.Kept.Fixture",
        ref,
        result,
        comp.variables,
        out_dir / "plots",
        test_dir=test_dir,
        test_model=test,
        metric_tree=comp.metric_tree,
        artifact_files=(("dslog.txt", "Simulation log"),),
        max_embedded_samples=MAX_EMBEDDED,
        overlays=overlays,
    )
    assert html is not None
    return html, comp


def _generate_no_baseline(out_dir: Path) -> Path:
    """A page with no comparisons at all — ``nobaseline_trajectories`` only
    fills when ``not comparisons`` (``context_builder._build_trajectories``),
    so the plot and its overlay picker take a different path from the
    compared page's."""
    from dstf.backends.types import TestResult, VariableResult
    from dstf.discovery.test_registry import TestModel
    from dstf.reporting.overlay_loader import Overlay, OverlayVariable
    from dstf.reporting.report_suite import generate_comparison_plots

    t = _actual_time(N_SMALL)
    result = TestResult(
        model_id="TestLib.Fresh.Fixture",
        success=True,
        variables=[VariableResult(index=1, name="nb", time=t, values=np.sin(t))],
    )
    test = TestModel(
        model_id="TestLib.Fresh.Fixture",
        source_file=Path("fixture.mo"),
        source_package="TestLib",
        short_name="Fresh",
        n_vars=1,
    )
    overlays = [
        Overlay(
            name="alt_solver",
            role="soft_check",
            variables={
                "nb": OverlayVariable(
                    time=t.tolist(), values=(np.sin(t) * 1.02).tolist()
                )
            },
        )
    ]
    html = generate_comparison_plots(
        "TestLib.Fresh.Fixture",
        None,
        result,
        [],
        out_dir / "plots",
        test_model=test,
        metric_tree=None,
        max_embedded_samples=MAX_EMBEDDED,
        overlays=overlays,
    )
    assert html is not None
    return html


def _generate_open_window(out_dir: Path) -> Path:
    """One tube leaf whose window has only a ``start`` — the band must run to
    the end of the data rather than stopping at a bound nobody declared."""
    from dstf.backends.types import TestResult, VariableResult
    from dstf.comparison.comparator import compare_test
    from dstf.comparison.tree_spec import parse_metric_tree
    from dstf.discovery.test_registry import TestModel
    from dstf.reporting.report_suite import generate_comparison_plots

    ref_time = _reference_time()
    ref = {
        "test_id": "0002",
        "time": ref_time.tolist(),
        "variables": [
            {"index": 1, "name": "x", "values": _signal("x", ref_time).tolist()}
        ],
    }
    t = _actual_time(N_SMALL)
    result = TestResult(
        model_id="TestLib.OpenWindow.Fixture",
        success=True,
        variables=[
            VariableResult(index=1, name="x", time=t, values=_signal("x", t) + 0.01)
        ],
    )
    test = TestModel(
        model_id="TestLib.OpenWindow.Fixture",
        source_file=Path("fixture.mo"),
        source_package="TestLib",
        short_name="OpenWindow",
        n_vars=1,
        metric_tree_spec=parse_metric_tree(
            {
                "combinator": "and",
                "children": [
                    {
                        "metric": "tube",
                        "variable": "x",
                        "tube_width_mode": "band",
                        "tube_abs": 0.05,
                        "window": {"start": 4.0},
                    }
                ],
            }
        ),
    )
    comp = compare_test(test, result, ref)
    html = generate_comparison_plots(
        "TestLib.OpenWindow.Fixture",
        ref,
        result,
        comp.variables,
        out_dir / "plots",
        test_model=test,
        metric_tree=comp.metric_tree,
        max_embedded_samples=MAX_EMBEDDED,
    )
    assert html is not None
    return html


@pytest.fixture(scope="module")
def compared(tmp_path_factory):
    html, comp = _generate_compared(tmp_path_factory.mktemp("compared"))
    return html, comp


@pytest.fixture(scope="module")
def no_baseline(tmp_path_factory):
    return _generate_no_baseline(tmp_path_factory.mktemp("no_baseline"))


@pytest.fixture(scope="module")
def open_window(tmp_path_factory):
    return _generate_open_window(tmp_path_factory.mktemp("open_window"))


# ---------------------------------------------------------------------------
# Page helpers
# ---------------------------------------------------------------------------


def _open(browser, html_path: Path):
    page = browser.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(Path(html_path).as_uri())
    # Wait for Plotly to have painted every chart. ``FIGURES`` / ``VARIABLE_ORDER``
    # are top-level ``const``s — global lexical bindings, NOT properties of
    # ``window`` — so polling ``window.FIGURES`` would wait forever.
    page.wait_for_function(
        "() => { const els = document.querySelectorAll('.plotly-chart');"
        " return els.length > 0 && Array.from(els).every(el => !!el.data); }"
    )
    return page, errors


def _embedded_figures(html_path: Path) -> dict:
    """The FIGURES dict as it was written into the page."""
    page = Path(html_path).read_text(encoding="utf-8")
    start = page.index("FIGURES:")
    block = page[start : page.index("DIAG_FIGURES:", start)]
    return json.loads(re.search(r"FIGURES:\s*(\{.*?\}),\s*$", block, re.S).group(1))


def _leaf_verdicts(comp) -> dict[str, bool]:
    """``{leaf_path: passed}`` straight off the CLI-evaluated tree."""
    out: dict[str, bool] = {}

    def walk(node):
        path = node.diagnostics.get("leaf_path")
        if path is not None and not node.children:
            out[path] = bool(node.passed)
        for child in node.children:
            walk(child)

    walk(comp.metric_tree)
    return out


# ---------------------------------------------------------------------------
# Kept: the per-variable figure
# ---------------------------------------------------------------------------


def test_every_variable_plot_paints_from_its_figure_dict(compared, playwright_browser):
    """Each plot is the embedded dict, trace for trace, with the geometry its
    metric declares — not something the browser rebuilt."""
    html, _ = compared
    figures = _embedded_figures(html)
    page, errors = _open(playwright_browser, html)
    painted = page.evaluate("""() => {
        const out = {};
        VARIABLE_ORDER.forEach((name, i) => {
          const el = document.getElementById('plot-' + i);
          out[name] = {
            python: !!el._mt_python,
            n: el.data.length,
            ids: el.data.map(t => (t.meta || {}).id || null),
            shapes: (el.layout.shapes || []).map(s => s.name),
            axes: el.data.map(t => [t.xaxis || null, t.yaxis || null]),
          };
        });
        return out;
    }""")
    page.close()

    assert errors == []
    assert set(painted) == set(figures)
    for name, fig in figures.items():
        assert painted[name]["python"] is True, name
        assert painted[name]["n"] == len(fig["data"]), name

    # x: the tube band and the closed window it was scored over.
    assert "/metrics/children/0/tube/band" in painted["x"]["ids"]
    assert "/metrics/children/0/window" in painted["x"]["shapes"]
    # y: one box per declared checkpoint.
    assert {"/metrics/children/2/points/box/0", "/metrics/children/2/points/box/1"} <= (
        set(painted["y"]["shapes"])
    )
    # z: the breached bound.
    assert "/metrics/children/3/range/max" in painted["z"]["shapes"]
    # e: the reference event, the actual event, and the tolerance window.
    assert {
        "/metrics/children/4/events/ref/0",
        "/metrics/children/4/events/act/0",
        "/metrics/children/4/events/window/0",
    } <= set(painted["e"]["shapes"])
    # f: both spectra, on the second (spectrum) panel's axes.
    spectrum_axes = {
        tuple(ax)
        for ax, tid in zip(painted["f"]["axes"], painted["f"]["ids"])
        if tid and tid.endswith(("/spectrum/ref", "/spectrum/act"))
    }
    assert "/metrics/children/5/spectrum/ref" in painted["f"]["ids"]
    assert "/metrics/children/5/spectrum/act" in painted["f"]["ids"]
    assert spectrum_axes == {("x2", "y2")}
    # s: the scalar's actual and reference levels.
    assert {
        "/metrics/children/6/scalar/actual",
        "/metrics/children/6/scalar/reference",
    } <= set(painted["s"]["shapes"])


# ---------------------------------------------------------------------------
# Kept: zoom, hover, legend
# ---------------------------------------------------------------------------


def test_zoom_hover_and_legend_are_live(compared, playwright_browser):
    """Reading a value off a plot is zoom + hover; hiding a curve is the
    legend. All three are Plotly's, and all three must survive the cut."""
    html, _ = compared
    page, errors = _open(playwright_browser, html)
    idx = page.evaluate("() => VARIABLE_ORDER.indexOf('x')")
    assert idx >= 0

    zoomed = page.evaluate(f"""async () => {{
        const el = document.getElementById('plot-{idx}');
        await Plotly.relayout(el, {{'xaxis.range': [2, 4]}});
        return el._fullLayout.xaxis.range;
    }}""")
    assert [round(v, 6) for v in zoomed] == [2.0, 4.0]

    hovered = page.evaluate(f"""async () => {{
        const el = document.getElementById('plot-{idx}');
        await Plotly.relayout(el, {{'xaxis.autorange': true}});
        const trace = el.data.findIndex(t => t.name === 'Actual');
        Plotly.Fx.hover(el, [{{curveNumber: trace, pointNumber: 100}}]);
        await new Promise(r => setTimeout(r, 150));
        return document.querySelectorAll('.hoverlayer .hovertext').length;
    }}""")
    assert hovered > 0, "hovering the Actual trace produced no hover label"

    toggled = page.evaluate(f"""async () => {{
        const el = document.getElementById('plot-{idx}');
        const i = el.data.findIndex(t => t.name === 'Reference');
        const before = el.data[i].visible;
        await Plotly.restyle(el, {{visible: 'legendonly'}}, [i]);
        const after = el.data[i].visible;
        await Plotly.restyle(el, {{visible: true}}, [i]);
        return [before === undefined ? true : before, after, el.data[i].visible];
    }}""")
    page.close()
    assert errors == []
    assert toggled == [True, "legendonly", True]


# ---------------------------------------------------------------------------
# Kept: the overlay picker
# ---------------------------------------------------------------------------


def test_overlay_picker_toggles_soft_check_and_companion(compared, playwright_browser):
    """Overlays ride in as ``legendonly`` traces; the picker flips exactly
    the one whose box was ticked."""
    html, _ = compared
    page, errors = _open(playwright_browser, html)
    idx = page.evaluate("() => VARIABLE_ORDER.indexOf('x')")
    probe = f"""() => {{
        const el = document.getElementById('plot-{idx}');
        return Object.fromEntries(el.data
          .filter(t => (t.meta || {{}}).kind === 'overlay')
          .map(t => [t.meta.id, t.visible]));
    }}"""
    before = page.evaluate(probe)
    seen = {}
    for name in ("alt_solver", "vendor"):
        box = page.locator(
            f'[data-plot-id="plot-{idx}"] input.overlay-toggle[data-ov-name="{name}"]'
        )
        box.check()
        page.wait_for_timeout(120)
        seen[name] = page.evaluate(probe)
        box.uncheck()
        page.wait_for_timeout(120)
    after = page.evaluate(probe)
    page.close()

    assert errors == []
    assert before == {
        "overlay/soft_check/alt_solver": "legendonly",
        "overlay/companion/vendor": "legendonly",
    }
    assert seen["alt_solver"]["overlay/soft_check/alt_solver"] is True
    assert seen["alt_solver"]["overlay/companion/vendor"] == "legendonly"
    assert seen["vendor"]["overlay/companion/vendor"] is True
    assert seen["vendor"]["overlay/soft_check/alt_solver"] == "legendonly"
    assert after == before


def test_overlay_picker_on_the_no_baseline_page(no_baseline, playwright_browser):
    """A no-baseline plot has no comparison behind it at all, so its picker
    is a separate path from the compared page's."""
    page, errors = _open(playwright_browser, no_baseline)
    probe = """() => {
        const el = document.getElementById('nb-plot-0');
        return {
          python: !!el._mt_python,
          overlays: el.data.filter(t => (t.meta || {}).kind === 'overlay')
                           .map(t => t.visible),
        };
    }"""
    before = page.evaluate(probe)
    page.locator('[data-plot-id="nb-plot-0"] input.overlay-toggle').first.check()
    page.wait_for_timeout(120)
    after = page.evaluate(probe)
    page.close()

    assert errors == []
    assert before == {"python": True, "overlays": ["legendonly"]}
    assert after == {"python": True, "overlays": [True]}


# ---------------------------------------------------------------------------
# Kept: the error-overlay toggle
# ---------------------------------------------------------------------------


def test_error_overlay_toggle_shows_exactly_one_error_trace(
    compared, playwright_browser
):
    """signed / abs / per-point NRMSE, one at a time, on the right-hand
    axis — which stays hidden while none is shown."""
    html, _ = compared
    page, errors = _open(playwright_browser, html)
    idx = page.evaluate("() => VARIABLE_ORDER.indexOf('x')")
    probe = f"""() => {{
        const el = document.getElementById('plot-{idx}');
        return {{
          traces: Object.fromEntries(el.data
            .filter(t => (t.meta || {{}}).kind === 'error')
            .map(t => [t.meta.id, t.visible])),
          axis: el._fullLayout.yaxis3 ? el._fullLayout.yaxis3.visible : null,
        }};
    }}"""
    select = page.locator(f'select.error-overlay-select[data-vidx="{idx}"]')
    seen = {}
    for mode in ("signed", "abs", "nrmse", "none"):
        select.select_option(mode)
        select.dispatch_event("change")
        page.wait_for_timeout(150)
        seen[mode] = page.evaluate(probe)
    page.close()

    assert errors == []
    for mode in ("signed", "abs", "nrmse"):
        assert seen[mode]["traces"] == {
            f"error/{m}": (True if m == mode else "legendonly")
            for m in ("signed", "abs", "nrmse")
        }, mode
        assert seen[mode]["axis"] is True, mode
    assert seen["none"]["traces"] == {
        "error/signed": "legendonly",
        "error/abs": "legendonly",
        "error/nrmse": "legendonly",
    }
    assert seen["none"]["axis"] is False


# ---------------------------------------------------------------------------
# Kept: the metric tree
# ---------------------------------------------------------------------------


def test_metric_tree_shows_verdict_and_summary_text_per_leaf(
    compared, playwright_browser
):
    """Every leaf shows the CLI's verdict and the CLI's ``summarize`` text.

    Asserted against the rendered DOM — since the cut the tree is markup
    the server wrote, so every one of these strings is on screen rather
    than travelling inside an embedded blob.
    """
    from dstf.comparison.registry import summarize
    from dstf.comparison.tree_eval import leaf_comparisons_by_path

    html, comp = compared
    verdicts = _leaf_verdicts(comp)
    assert set(verdicts) == set(LEAF_VARIABLES)

    page, errors = _open(playwright_browser, html)
    rendered = page.evaluate("""() => {
        const out = {};
        document.querySelectorAll('#nodes-full .node-leaf[data-path]').forEach(n => {
          const pill = n.querySelector(':scope > .node-header > .node-status');
          const score = n.querySelector(':scope > .node-header > .node-score');
          const crit = n.querySelector(':scope > .node-criterion');
          out[n.dataset.path] = {
            pill: pill ? pill.textContent : null,
            score: score ? score.textContent : null,
            criterion: crit ? crit.textContent : null,
          };
        });
        return out;
    }""")
    body = page.inner_text("body")
    chip = page.locator(".advisory-chip").first.text_content()
    page.close()

    assert errors == []
    assert set(rendered) == set(LEAF_VARIABLES)
    by_path = leaf_comparisons_by_path(comp.metric_tree)
    for path, passed in verdicts.items():
        summary = summarize(by_path[path])
        assert rendered[path]["pill"] == ("PASS" if passed else "FAIL"), path
        assert rendered[path]["score"] == summary.display, path
        assert summary.display in body, path
        assert rendered[path]["criterion"] == summary.criterion, path

    # The warn subtree's failing leaf is advisory, not a test failure — the
    # page has to say so somewhere the reader will see it.
    assert verdicts["/metrics/children/1/children/0"] is False
    assert "advisory" in chip


def test_metric_tree_is_read_only(compared, playwright_browser):
    """After the cut the tree is a server-rendered read-only view: no form
    control of any kind inside a tree mount."""
    html, _ = compared
    page, errors = _open(playwright_browser, html)
    controls = page.evaluate("""() => {
        const out = {};
        document.querySelectorAll('.node-tree[id^="nodes-"]').forEach(mount => {
          out[mount.id] = mount.querySelectorAll('input, select, button, textarea').length;
        });
        return out;
    }""")
    page.close()
    assert errors == []
    assert controls, "no tree mounts found"
    assert set(controls.values()) == {0}, controls


# ---------------------------------------------------------------------------
# Kept: statistics, diagnostics, artifacts, and their plots
# ---------------------------------------------------------------------------


def test_statistics_diagnostics_and_artifacts_render(compared, playwright_browser):
    html, _ = compared
    page, errors = _open(playwright_browser, html)
    for section in ("Statistics", "Diagnostics", "Simulation Artifacts"):
        page.locator(f"summary:has-text('{section}')").first.click()
    text = page.inner_text("body")
    artifact = page.locator("details.artifacts a:has-text('Simulation log')").first
    href = artifact.get_attribute("href")
    page.close()

    assert errors == []
    assert "Cpu Time" in text
    assert "CPUtime" in text
    assert href is not None and href.endswith("dslog.txt")


def test_diagnostic_and_no_baseline_plots_paint_from_dicts(
    compared, no_baseline, playwright_browser
):
    """Diagnostic and no-baseline plots are figure dicts too — they are the
    two plot kinds with no metric tree behind them."""
    html, _ = compared
    page, errors = _open(playwright_browser, html)
    page.locator("summary:has-text('Diagnostics')").first.click()
    diag = page.evaluate("""() => {
        const el = document.getElementById('diag-plot-0');
        return {python: !!el._mt_python, n: el.data.length,
                names: el.data.map(t => t.name)};
    }""")
    page.close()
    assert errors == []
    assert diag["python"] is True
    assert diag["names"] and diag["n"] == len(diag["names"])

    page, errors = _open(playwright_browser, no_baseline)
    nb = page.evaluate("""() => {
        const el = document.getElementById('nb-plot-0');
        return {python: !!el._mt_python, n: el.data.length};
    }""")
    page.close()
    assert errors == []
    assert nb["python"] is True and nb["n"] > 0


# ---------------------------------------------------------------------------
# Kept: the window band, open-ended
# ---------------------------------------------------------------------------


def test_window_band_is_open_ended_when_one_end_is_none(
    compared, open_window, playwright_browser
):
    html, _ = compared
    page, errors = _open(playwright_browser, html)
    idx = page.evaluate("() => VARIABLE_ORDER.indexOf('x')")
    closed = page.evaluate(f"""() => {{
        const el = document.getElementById('plot-{idx}');
        const s = (el.layout.shapes || [])
          .find(s => s.name === '/metrics/children/0/window');
        return s ? [s.x0, s.x1] : null;
    }}""")
    page.close()
    assert errors == []
    assert closed == [0.2, 0.8]

    page, errors = _open(playwright_browser, open_window)
    open_ended = page.evaluate("""() => {
        const el = document.getElementById('plot-0');
        const s = (el.layout.shapes || [])
          .find(s => s.name === '/metrics/children/0/window');
        const last = Math.max(...el.data
          .filter(t => ['Actual', 'Reference'].includes(t.name))
          .map(t => t.x[t.x.length - 1]));
        return s ? [s.x0, s.x1, last] : null;
    }""")
    page.close()
    assert errors == []
    assert open_ended is not None
    x0, x1, last = open_ended
    assert x0 == 4.0
    assert x1 == pytest.approx(last)


# ---------------------------------------------------------------------------
# Kept: the decimation banner (post-cut shape)
# ---------------------------------------------------------------------------


def test_decimation_banner_names_every_decimated_trace(compared, playwright_browser):
    """After the cut the banner is server-rendered from the figures'
    ``full_samples`` stamps, so it can name which traces were decimated.
    ``s`` is the control: 101 samples on both sides, under the cap."""
    html, _ = compared
    page, errors = _open(playwright_browser, html)
    banner = page.locator("#decimation-banner").first
    text = banner.text_content()
    page.close()
    assert errors == []
    for name in ("x", "y", "z", "e", "f"):
        assert re.search(rf"\b{name}\b", text), f"{name} missing from {text!r}"
    assert not re.search(r"\bs\b", text), f"s must not be named: {text!r}"


# ---------------------------------------------------------------------------
# The end state: no verdict is computed in the browser
# ---------------------------------------------------------------------------


def test_no_javascript_computes_a_verdict(compared, playwright_browser):
    html, _ = compared
    page, _errors = _open(playwright_browser, html)
    # ``typeof <name>``, evaluated at global scope — half of these are top-level
    # ``const``s, which are global lexical bindings and never appear on
    # ``window``, so probing ``window[name]`` would pass vacuously.
    defined = page.evaluate("""() => Object.fromEntries(
        ['MODE_SCORERS', 'recomputePassStates', 'buildPatchData', 'activateLeaf',
         '_computeFftSpectrum', '_detectEvents']
          .map(n => [n, new Function('return typeof ' + n)()]))""")
    page.close()
    assert set(defined.values()) == {"undefined"}, defined
