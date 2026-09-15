# The Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Everything the redesign brief puts before the visible redesign because no layout changes it: the report's two secondary axes, the visual system as one token table both tiers read (with the font-stack correction), the tool-side costs that exist under any layout (the sibling-backend overlay index on the first click, the per-click disk re-reads, decimation), and a Session that opens in reported phases with the library scan cached.

**Architecture:** Sub-project 1 of `docs/superpowers/plans/2026-09-15-authoring-tool-redesign-roadmap.md`, against `docs/superpowers/specs/2026-09-15-authoring-tool-redesign-brief.md` §3.1, §3.4, §3.8, §3.10 and `DESIGN.md` "Colors" / "Typography" / "Themes". The figure layer (`src/dstf/figures/plotly.py`) emits the axes; one shared script (`templates/axes.js`) makes each axis follow its traces in both tiers; the tokens live in Python (`src/dstf/reporting/theme.py`) and are rendered to CSS custom properties into the dashboard, the per-test report and the tool's page; the Session (`src/dstf/authoring/session.py`) gains a per-model read cache, a warmed overlay index, `loading` events per phase and per scored test, and a scan cache under the user's cache directory with a background rescan. The shell (`app.py`) changes in two lines (the script and the palette) and is otherwise untouched: sub-project 2 replaces it.

**Tech Stack:** Python 3.10+, numpy, Jinja2, NiceGUI 3.16 (`[author]`), the vendored plotly.js 2.35.0 (report) and NiceGUI's bundled plotly.js 3.1.1 (tool), pytest + pytest-asyncio (`user_simulation`), Playwright + Chromium, uv.

**Revision:** this is the second text. A fresh-context review of the first found eleven sketches that failed as typed and two silent production defects the tests could not see: the scan cache's key was computed before the bundled recognizer registered, so a fresh `dstf author` never hit it (Task 9); a locked model's manifest entry arrived through the new per-result callback as "not found in discovery" and was rescored mid-job (Task 8). Also folded: a per-test report test rendering through a Jinja environment without the token global (Task 4), the figure cache test counting `lttb` calls the second decimation makes (Task 7), cache-drop tests counting the reads `compare_one` makes (Task 7), the `scan` phase never emitted for a non-Modelica source and C counted wrong (Task 8), the LTTB hoist's "identical output" claim false on collinear data (Task 5), a missing import and a missing `mkdir` (Tasks 6, 9), a wrong fixture pointer and class name (Task 8), a rescan rescoring every model (Task 9), a stale thread marking the next open ready (Tasks 6, 9), and a `done` / `rescan` order race (Task 9).

**Spike results this plan rests on** (run 2026-09-15 from this venv, scripts in the session scratchpad, gone):
- **Two right-hand axes lay out side by side under both builds.** A figure from `variable_figure(_tube_view())` with `yaxis4 = {overlaying: "y", side: "right", anchor: "free", autoshift: true}` rendered under plotly.js 2.35.0 puts the `yaxis4` title 132 px right of the `yaxis3` title (`_shift` 58.25 px), both right of the plot area; under NiceGUI's plotly.js **3.1.1** (`Plotly.version` in the served page — not the 3.8.2 an earlier note recorded) `_fullLayout.yaxis4` exists with `autoshift` honoured. `automargin` on both axes keeps the titles inside the container.
- **The tool's plot node is `getElement(<plot.id>).$el`**: a `DIV.js-plotly-plot` whose `.data` is the figure's trace list; `window.Plotly` is a global in the tool's page (NiceGUI imports it as a module AND exposes it). NiceGUI's `plotly.js` forwards `plotly_restyle` and `plotly_legendclick` to the server, and a legend click on "Actual" sets that trace `visible: "legendonly"` in `el.data`. `Plotly.relayout(el, {...})` from page-side script works on that node.
- **LTTB costs ~13 ms per trace at 2 000, 20 000 and 200 000 input samples** (`lttb(t, v, 1000)`, 5-run mean): the Python loop over the 1 000 output buckets is the cost, not the input length. A figure carries actual, reference, three error series, a band's two edges and the overlays, so the brief's "150 ms, mostly decimation" is this loop times the trace count.
- **On `/mnt/d` a per-file validation of the scan cache costs more than the scan.** TRANSFORM (`/mnt/d/Modelica/TRANSFORM-Library/TRANSFORM`): `rglob("*.mo")` 3 269 files in 8.3 s; parsing them (`recognize`, 331 hits) 19.9 s; a `stat()` sweep over the same files 26.8 s; a directory-mtime sweep over 871 dirs 31.4 s. So the cache validates nothing per file: it opens from the cache and rescans in the background (Task 9).

---

## Conventions for every task

- Run commands from the repo root with `uv run --no-sync ...` (agents share the venv). Gates before every commit: `uv run --no-sync ruff check`, `uv run --no-sync ruff format --check` (fix with `ruff format <files>`), `uv run --no-sync mypy` **bare, no path**, `uv run --no-sync pytest -q -m "not playwright"`, and `uv run --no-sync pytest -q -m playwright` — the browser tests RUN (Task 0 confirms the venv), they do not skip.
- **Commit with explicit paths — `git commit -F <msgfile> -- <paths>` — never a bare `git commit`, never `--amend`.** New files need `git add <file>` first. Messages end with the attribution trailer the session's instructions specify. Nothing is pushed. A worktree is created FROM `v1.0-prep`.
- **Never run `dstf run` / `dstf compare` / `dstf author` against `testing_output/` or any real work directory** from a task or a review. Task 11's real-library gate is the orchestrator's. Tests use `tmp_path`, and the scan-cache tests set `XDG_CACHE_HOME` to a `tmp_path` (a test that writes the user's real cache is a defect).
- Done-checks are predicates over the end state, never counts.
- `docs/ideas.md` and `.soul/` are NOT touched by this plan. `docs/decisions.md` gains exactly one entry, D127, in Task 10.
- **Every file under `src/dstf/authoring/` other than `app.py`, `forms.py` and `builtin/*` still imports no UI framework** (Task 11 predicate 5).
- Calls made here, not in the brief or DESIGN.md:
  1. **The token table is Python** (`reporting/theme.py`), rendered to `:root { --dstf-...: ... }` for both templates and the tool. DESIGN.md's "a theme is one file that sets those variables" holds — the file is a mapping, and sub-project 7 adds `dark` beside `default` in the same module. The tool's Quasar palette is set from the same table (`ui.colors`). The figure layer keeps its literal hex (DESIGN.md "Plot series").
  2. **Colours the templates use today that are not in DESIGN.md's tables map onto the nearest token** (the table in Task 4). The metric tree's combinator rows lose their purple (`#9C27B0` / `#f5f0fa`) for `text-secondary` on `surface-hover`; the decimation banner's amber becomes the `warn` tint/border/dark; the dashboard's warnings counter moves from `#ff9800` / `#E65100` to the `warn` fill `#ffc107` and dark `#8a6d00`. All are visible changes the owner sees in Task 11's screenshot.
  3. **Axis visibility follows the traces in the browser** (`templates/axes.js`, one script for both tiers), because a legend click is a client-side toggle the server never hears in the report. The report's error-overlay select only restyles traces now; the axes follow.
  4. **The scan cache lives under `$XDG_CACHE_HOME/dstf/scan/` (default `~/.cache/dstf/scan/`), never in the work dir.** `Session.open` keeps writing nothing in the work dir (`tests/test_session.py::TestOpen::test_open_writes_nothing_in_the_work_dir`). The cache is keyed by the library path, the recognizer set and the DSTF version, and it validates nothing per file (spike 4).
  5. **A cached open rescans in the background and re-merges when the scan differs**; the Session exposes `rescan_done` (an Event) and emits `loading(phase="rescan", changed=...)`. A manual "Rescan library" control is sub-project 2's.
  6. **`loading` is a new Session event** (`Event` gains `"loading"`): `phase` is `"scan"` (with `done`/`total`, or `cached=True`), `"results"` (with `model`, `done`, `total`), `"done"` (with `seconds`), or `"rescan"` (with `changed`). The current shell ignores it; sub-project 2's Suite page consumes it.
  7. **Results are scored as they are read** (`read_last_results(tests, on_result=...)`); the `results` event stays ONE batch event per reload so the current shell is not rebuilt 331 times. A test is openable as soon as its `loading(phase="results")` event names it, because `Session.comparisons` already holds its verdict.

## File structure

| Path | Responsibility after this plan |
|---|---|
| `src/dstf/figures/plotly.py` | `ERROR_AXES` (series id → axis); `_layout` emits `yaxis3` "Error [signal units]" and `yaxis4` "NRMSE [–]", both hidden, `yaxis4` free-anchored and autoshifted. |
| `src/dstf/figures/view.py` | `decimate_view` and `_decimate_overlay` keep a `full_samples` already stamped (a decimated base view decimates again as a no-op). |
| `src/dstf/figures/decimate.py` | `lttb` with the bucket bounds and the next-bucket means precomputed; identical output. |
| `src/dstf/reporting/templates/axes.js` (new) | `dstfWireSecondaryAxes(gd)` / `dstfFollowSecondaryAxes(gd)`; a MutationObserver wires every `.js-plotly-plot` on the page. |
| `src/dstf/reporting/templates/report.js` | `paintFigure` wires the axes; `setErrorOverlay` restyles traces only. |
| `src/dstf/reporting/report_suite.py` | `INTERACTIVE_JS_PARTS = ("axes.js", "report.js")`; `axes_js_source()`; the Jinja env carries `dstf_css`. |
| `src/dstf/reporting/theme.py` (new) | `DEFAULT_THEME`, `STATUS`, `ROLES`, `FONT_STACK`, `MONO_STACK`, `css_variables(theme)`, `quasar_palette(theme)`. |
| `src/dstf/reporting/templates/interactive.html`, `dashboard.html` | `<style>{{ dstf_css }}...` — every colour a `var(--dstf-...)`; `body { font-family: var(--dstf-font) }`. |
| `src/dstf/reporting/dashboard_render.py` | `_env.globals["dstf_css"]`. |
| `src/dstf/reporting/overlay_loader.py` | `sibling_backend_index(...)` under a lock; `index_args(config)`; `warm_sibling_backend_index(config)`. |
| `src/dstf/backends/base.py` | `run_verdicts(work_dir, manifests) -> dict[str, str]`; `read_results(..., on_result=None)`, `read_last_results(tests, on_result=None)` applying the verdict per result before the callback. |
| `src/dstf/discovery/test_registry.py` | `scan_source_tests(config, progress=None)`. |
| `src/dstf/discovery/scan_cache.py` (new) | `cache_dir()`, `load_scan(config)`, `store_scan(config, scanned)`. |
| `src/dstf/authoring/session.py` | `_ModelReads` cache; `figure()` from a decimated base; `overlay_index_ready`; `loading` events; per-result scoring; the cached open + background rescan; `rescan_done`. |
| `src/dstf/authoring/app.py` | `root()` adds `axes.js` to the body and sets the Quasar palette. Nothing else. |
| `tests/test_figures.py`, `tests/golden/figure_*.json` | The per-family axis assertions; goldens re-blessed. |
| `tests/test_axes_js_playwright.py` (new) | The shared script under the report's plotly.js: legend click and select drive the axes. |
| `tests/test_interactive_report.py`, `tests/test_interactive_figures.py` | The kept error-overlay tests assert per-axis visibility. |
| `tests/test_author_smoke_playwright.py` | The tool: a legend click on an error trace shows its axis. |
| `tests/test_theme.py` (new) | The token table renders; both pages carry only tokens; the stack ends in `sans-serif`. |
| `tests/test_decimate.py` | `lttb` equals the reference implementation on random series. |
| `tests/test_overlay_loader.py`, `tests/test_report_suite.py`, `tests/test_discovery.py`, `tests/test_scan_cache.py` (new), `tests/test_session.py`, `tests/conftest.py` | Each task's tests; the autouse `XDG_CACHE_HOME` redirect. |
| `docs/usage.md`, `docs/architecture.md`, `CLAUDE.md`, `docs/decisions.md` (D127) | Task 10. |

---

### Task 0: The venv carries every extra and Playwright

**Files:** none.

- [ ] **Step 1:** Run `uv sync --extra dev --extra fmpy --extra om --extra author --locked --inexact` (network). Then `uv run --no-sync python -c "import nicegui, fmpy, playwright; print(nicegui.__version__)"` prints `3.16.0`. If `playwright` fails: `uv pip install pytest-playwright && uv run --no-sync playwright install chromium`.

- [ ] **Step 2:** `uv run --no-sync pytest -q -m playwright tests/test_interactive_figures.py` — runs (passes), does not skip. `git status` shows `uv.lock` unchanged.

---

### Task 1: Two secondary axes in the figure layer

**Files:**
- Modify: `src/dstf/figures/plotly.py` (`_error_trace`, `_layout`)
- Test: `tests/test_figures.py` (the class `TestFigure`, method `test_error_series_ride_on_a_right_axis_hidden_by_default`), `tests/golden/figure_*.json`

- [ ] **Step 1: Replace the failing test.** In `tests/test_figures.py` replace the body of `test_error_series_ride_on_a_right_axis_hidden_by_default` with:

```python
    def test_error_series_ride_on_two_right_axes_hidden_by_default(self):
        """Brief §3.4: one labelled right axis per unit family — signed and
        absolute error in signal units, per-point NRMSE dimensionless —
        both laid out hidden until a series on them is shown (axes.js)."""
        fig = variable_figure(_tube_view())
        err = [tr for tr in fig["data"] if tr["meta"].get("kind") == "error"]
        assert {tr["meta"]["id"]: tr["yaxis"] for tr in err} == {
            "error/signed": "y3",
            "error/abs": "y3",
            "error/nrmse": "y4",
        }
        assert all(tr["visible"] == "legendonly" for tr in err)
        layout = fig["layout"]
        for key, title in (
            ("yaxis3", "Error [signal units]"),
            ("yaxis4", "NRMSE [–]"),
        ):
            axis = layout[key]
            assert axis["title"]["text"] == title
            assert axis["overlaying"] == "y" and axis["side"] == "right"
            assert axis["visible"] is False and axis["zeroline"] is False
            assert axis["automargin"] is True
        # The second right axis floats past the first (spike: 58 px under
        # plotly.js 2.35.0), instead of drawing over it.
        assert layout["yaxis4"]["anchor"] == "free"
        assert layout["yaxis4"]["autoshift"] is True
        assert "anchor" not in layout["yaxis3"]

    def test_a_view_without_errors_lays_out_no_secondary_axis(self):
        t = np.linspace(0.0, 1.0, 5)
        fig = variable_figure(build_view("x", act_time=t, act_values=t))
        assert "yaxis3" not in fig["layout"] and "yaxis4" not in fig["layout"]
```

(Delete the old method; `np` and `build_view` are already imported in that file.)

- [ ] **Step 2: Run it to see it fail.** `uv run --no-sync pytest tests/test_figures.py -q -k "two_right_axes or no_secondary_axis"` → the first FAILS on `"error/nrmse": "y3"`; the second passes already (keep it, it pins the negative).

- [ ] **Step 3: Implement.** In `src/dstf/figures/plotly.py`, after `ERROR = "#f44336"` add:

```python
#: Which right-hand axis an error series rides on — one axis per unit family
#: (brief §3.4): signal units for the signed and absolute error, dimensionless
#: for the per-point NRMSE. Unknown ids ride on the signal-units axis.
ERROR_AXES = {"error/signed": "y3", "error/abs": "y3", "error/nrmse": "y4"}
```

Replace `_error_trace`'s `"yaxis": "y3",` with `"yaxis": ERROR_AXES.get(s.id, "y3"),`.

Replace the `if has_errors:` block in `_layout` with:

```python
    if has_errors:
        # Both axes are laid out hidden: a laid-out axis with only
        # legend-only traces gets a placeholder range and draws its zero
        # line as a stray black rule across the plot. templates/axes.js
        # shows an axis while a series on it is visible, in both tiers.
        layout["yaxis3"] = _secondary_axis("Error [signal units]")
        layout["yaxis4"] = {
            **_secondary_axis("NRMSE [–]"),
            # A second right axis anchored to x would draw over the first;
            # free + autoshift stacks it outward (plotly.js >= 2.25; the
            # report vendors 2.35.0, NiceGUI 3.16 bundles 3.1.1).
            "anchor": "free",
            "autoshift": True,
        }
    return layout


def _secondary_axis(title: str) -> dict:
    return {
        "title": {"text": title, "font": {"color": ERROR, "size": 10}},
        "tickformat": ".4g",
        "nticks": 4,
        "automargin": True,
        "overlaying": "y",
        "side": "right",
        "showgrid": False,
        "tickfont": {"color": ERROR, "size": 10},
        "zeroline": False,
        "visible": False,
    }
```

- [ ] **Step 4: Run the figure tests.** `uv run --no-sync pytest tests/test_figures.py -q` → the new test passes; `tests/test_figure_goldens.py` now FAILS for every mode whose view has both curves. Run `uv run --no-sync pytest tests/test_figure_goldens.py -q 2>&1 | grep "^[-+] "` and check the diff lines are ONLY of two kinds: `"yaxis": "y3"` → `"y4"` on the `error/nrmse` trace, and `layout_keys` gaining `"yaxis4"`. Any other line is a bug in Step 3; fix it before blessing.

- [ ] **Step 5: Re-bless the goldens.** `UPDATE_GOLDEN=1 uv run --no-sync pytest tests/test_figure_goldens.py -q`, then `uv run --no-sync pytest tests/test_figure_goldens.py tests/test_figures.py -q` → all pass. `git diff --stat tests/golden` names only `figure_*.json` files.

- [ ] **Step 6: Gate and commit.** ruff / format / mypy / `pytest -q -m "not playwright"` — the browser tests in `test_interactive_report.py` and `test_interactive_figures.py` still pass here because `report.js` still relayouts `yaxis3` and never mentions `yaxis4` (Task 2 changes both).

```bash
git commit -F msg -- src/dstf/figures/plotly.py tests/test_figures.py tests/golden
```
Message: `figures: one right axis per unit family — error in signal units, NRMSE dimensionless — laid out hidden`.

---

### Task 2: `axes.js` — the axes follow their traces, in both tiers

**Files:**
- Create: `src/dstf/reporting/templates/axes.js`
- Modify: `src/dstf/reporting/templates/report.js`, `src/dstf/reporting/report_suite.py:42` (`INTERACTIVE_JS_PARTS`) and a new `axes_js_source()`
- Test: `tests/test_axes_js_playwright.py` (new), `tests/test_interactive_report.py:608-648`, `tests/test_interactive_figures.py:262-290`, `tests/test_report_suite.py` (the part list)

- [ ] **Step 1: The browser test for the shared script.** Create `tests/test_axes_js_playwright.py`:

```python
"""templates/axes.js under the report's vendored plotly.js: a secondary axis
is visible exactly while a trace on it is visible, whether the trace was
shown by the page's select (Plotly.restyle) or by a legend click."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.playwright

sys.path.insert(0, str(Path(__file__).parent))
from test_figures import _tube_view  # noqa: E402

from dstf.figures.plotly import variable_figure  # noqa: E402
from dstf.reporting.report_suite import axes_js_source, copy_vendor_assets  # noqa: E402

PROBE = """() => {
  const el = document.getElementById('p');
  const fl = el._fullLayout;
  return {
    traces: Object.fromEntries(el.data.filter(t => (t.meta || {}).kind === 'error')
                                  .map(t => [t.meta.id, t.visible])),
    y3: fl.yaxis3.visible, y4: fl.yaxis4.visible,
  };
}"""


@pytest.fixture(scope="module")
def page_path(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("axes")
    copy_vendor_assets(root)
    fig = variable_figure(_tube_view())
    (root / "page.html").write_text(
        "<html><head><script src='plotly.min.js'></script>"
        f"<script>{axes_js_source()}</script></head><body>"
        "<div id='p' style='width:900px;height:350px'></div>"
        f"<script>const FIG={json.dumps(fig)};"
        "Plotly.newPlot('p', FIG.data, FIG.layout, FIG.config);"
        "dstfWireSecondaryAxes(document.getElementById('p'));</script>"
        "</body></html>",
        encoding="utf-8",
    )
    return root / "page.html"


def _open(browser, page_path):
    page = browser.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(page_path.as_uri())
    page.wait_for_function("() => !!document.getElementById('p').data")
    return page, errors


def test_both_axes_start_hidden(playwright_browser, page_path):
    page, errors = _open(playwright_browser, page_path)
    seen = page.evaluate(PROBE)
    page.close()
    assert errors == []
    assert seen["y3"] is False and seen["y4"] is False


def test_a_restyle_shows_exactly_the_axis_of_the_shown_series(
    playwright_browser, page_path
):
    page, errors = _open(playwright_browser, page_path)
    seen = {}
    for shown in ("error/nrmse", "error/abs", None):
        page.evaluate(
            """(shown) => { const el = document.getElementById('p');
               const idx = [], vis = [];
               el.data.forEach((t, i) => { if ((t.meta || {}).kind !== 'error') return;
                 idx.push(i); vis.push(t.meta.id === shown ? true : 'legendonly'); });
               Plotly.restyle(el, {visible: vis}, idx); }""",
            shown,
        )
        page.wait_for_timeout(150)
        seen[shown] = page.evaluate(PROBE)
    page.close()
    assert errors == []
    assert (seen["error/nrmse"]["y3"], seen["error/nrmse"]["y4"]) == (False, True)
    assert (seen["error/abs"]["y3"], seen["error/abs"]["y4"]) == (True, False)
    assert (seen[None]["y3"], seen[None]["y4"]) == (False, False)


def test_a_legend_click_drives_the_axis_too(playwright_browser, page_path):
    """The tool has no select: its only toggle is the legend, a client-side
    restyle the server never sees. Plotly's legend entries are SVG text
    over a `.legendtoggle` rect that takes the pointer."""
    page, errors = _open(playwright_browser, page_path)
    entry = page.locator(".legend .traces").filter(has_text="NRMSE (per-point)")
    entry.locator(".legendtoggle").click(force=True)
    page.wait_for_timeout(200)
    after_show = page.evaluate(PROBE)
    entry.locator(".legendtoggle").click(force=True)
    page.wait_for_timeout(200)
    after_hide = page.evaluate(PROBE)
    page.close()
    assert errors == []
    assert after_show["traces"]["error/nrmse"] is True
    assert (after_show["y3"], after_show["y4"]) == (False, True)
    assert after_hide["traces"]["error/nrmse"] == "legendonly"
    assert (after_hide["y3"], after_hide["y4"]) == (False, False)
```

- [ ] **Step 2: Run it to see it fail.** `uv run --no-sync pytest tests/test_axes_js_playwright.py -q` → ImportError on `axes_js_source`.

- [ ] **Step 3: The script.** Create `src/dstf/reporting/templates/axes.js`:

```javascript
// ---- axes.js ----
// A secondary y-axis (one with `overlaying`) is visible exactly while a
// trace on it is visible. The figure layer lays every such axis out
// hidden (dstf.figures.plotly: a laid-out axis with only legend-only
// traces draws a stray zero line); this script shows and hides it as the
// traces toggle — by the report's select (Plotly.restyle) or by a legend
// click, which is a client-side restyle the server never hears.
//
// One script for both tiers: the report loads it as the first part of
// interactive.js; the tool adds it to its page body. It computes nothing
// about the data — it reads `visible` flags and writes `visible` flags.
(function () {
  function anyVisibleOn(gd, axisId) {
    return (gd.data || []).some(function (t) {
      return (t.yaxis || 'y') === axisId && t.visible !== 'legendonly' && t.visible !== false;
    });
  }

  function dstfFollowSecondaryAxes(gd) {
    var layout = gd.layout || {};
    var update = {};
    Object.keys(layout).forEach(function (key) {
      var m = /^yaxis(\d+)$/.exec(key);
      if (!m || !layout[key] || !layout[key].overlaying) return;
      var want = anyVisibleOn(gd, 'y' + m[1]);
      if (Boolean(layout[key].visible) !== want) update[key + '.visible'] = want;
    });
    if (Object.keys(update).length) Plotly.relayout(gd, update);
  }

  function dstfWireSecondaryAxes(gd) {
    if (!gd || typeof gd.on !== 'function' || gd._dstfAxesWired) return;
    gd._dstfAxesWired = true;
    gd.on('plotly_restyle', function () { dstfFollowSecondaryAxes(gd); });
    dstfFollowSecondaryAxes(gd);
  }

  // The tool creates its plot after the page is built and replaces the
  // node on a full redraw; a node that is not yet a plot has no `on` and
  // is skipped until Plotly adds the class. (Plotly keeps a node's event
  // emitter across a second newPlot, so a wired node stays wired.)
  var observer = new MutationObserver(function () {
    document.querySelectorAll('.js-plotly-plot').forEach(dstfWireSecondaryAxes);
  });
  observer.observe(document.documentElement, {
    childList: true, subtree: true, attributes: true, attributeFilter: ['class'],
  });

  window.dstfFollowSecondaryAxes = dstfFollowSecondaryAxes;
  window.dstfWireSecondaryAxes = dstfWireSecondaryAxes;
})();
```

In `src/dstf/reporting/report_suite.py` change the part list and add the reader:

```python
# The report page loads ONE ``interactive.js``: the axes script first (it
# defines what report.js calls), then report.js. The served name is
# historical.
INTERACTIVE_JS_PARTS: tuple[str, ...] = ("axes.js", "report.js")


def axes_js_source() -> str:
    """The shared axis-follow script (templates/axes.js), for the tool's page."""
    return (_TEMPLATE_DIR / "axes.js").read_text(encoding="utf-8")
```

In `templates/report.js`: in `paintFigure` add `dstfWireSecondaryAxes(el);` after `el._mt_python = true;`; in `setErrorOverlay` delete the trailing block (the comment "The axis is laid out hidden…" and the `Plotly.relayout(el, { 'yaxis3.visible': ... })` call) — the restyle above it fires `plotly_restyle`, and axes.js follows. Update the header comment's "(signed / abs / NRMSE, right y-axis)" line to "(signed / abs on the error axis, NRMSE on its own; axes.js shows the axis)".

- [ ] **Step 4: The kept-list tests assert per axis.** In `tests/test_interactive_report.py::test_error_overlay_toggle_shows_exactly_one_error_trace` change the probe's `axis:` line to

```python
          axes: [el._fullLayout.yaxis3.visible, el._fullLayout.yaxis4.visible],
```

and the final assertions to

```python
    expected_axes = {"signed": [True, False], "abs": [True, False], "nrmse": [False, True]}
    for mode in ("signed", "abs", "nrmse"):
        assert seen[mode]["traces"] == {
            f"error/{m}": (True if m == mode else "legendonly")
            for m in ("signed", "abs", "nrmse")
        }, mode
        assert seen[mode]["axes"] == expected_axes[mode], mode
    assert seen["none"]["axes"] == [False, False]
```

(keep whatever the test already asserts for `"none"`'s traces). In `tests/test_interactive_figures.py` the zero-line test at ~262-290 reads `yaxis3` only; extend its probe to return both `visible` flags (`y3 ? y3.visible : null, y4: el._fullLayout.yaxis4.visible`) and assert `after` shows `y3` `True` and `y4` `False` for `'abs'`, both `False` again after `'none'`, zero lines unchanged as the test already checks. In `tests/test_report_suite.py` (nothing there pins the part list today) add:

```python
def test_the_served_script_defines_the_axis_follow_before_the_page_script():
    from dstf.reporting.report_suite import interactive_js_source

    src = interactive_js_source()
    assert src.index("dstfWireSecondaryAxes") < src.index("renderAllPlots")
```

- [ ] **Step 5: Run the browser suite.** `uv run --no-sync pytest -q -m playwright tests/test_axes_js_playwright.py tests/test_interactive_report.py tests/test_interactive_figures.py` → all pass. The legend-click test is the one that fails if `axes.js` only listens to the select (it does not) — that is the state in which this guard fails.

- [ ] **Step 6: Gate and commit.**

```bash
git add src/dstf/reporting/templates/axes.js tests/test_axes_js_playwright.py
git commit -F msg -- src/dstf/reporting/templates/axes.js src/dstf/reporting/templates/report.js src/dstf/reporting/report_suite.py tests/test_axes_js_playwright.py tests/test_interactive_report.py tests/test_interactive_figures.py tests/test_report_suite.py
```
Message: `report: a secondary axis is visible exactly while a series on it is — axes.js, shared by both tiers`.

---

### Task 3: The tool's page carries `axes.js`

**Files:**
- Modify: `src/dstf/authoring/app.py` (`Shell.root`)
- Test: `tests/test_author_smoke_playwright.py`

- [ ] **Step 1: The smoke test.** The file has no server fixture: each test spawns `dstf author` itself (lines 67-107 of `test_the_real_page_edits_one_tolerance_and_saves_nothing`: `_project`, `_free_port`, the `subprocess.Popen([... "-m", "dstf", "--config", ..., "author", "--work-dir", ..., "--port", ..., "--no-browser"])` with `PYTEST_CURRENT_TEST` stripped from `env`, `_wait_until_serving`, `page = playwright_browser.new_page()`, `page.goto(url)`, and the `finally:` that terminates `proc`). Add a third test with that same skeleton and this body between `page.goto(url)` and the `finally`:

```python
        # The row buttons carry the short name ("A"), inside the tests column.
        tests_column = page.locator("div.dstf-tests")
        tests_column.get_by_role("button", name=A.rsplit(".", 1)[-1], exact=True).click()
        page.wait_for_function(
            "() => { const el = document.querySelector('.js-plotly-plot');"
            " return !!(el && el._fullLayout && el._fullLayout.yaxis4); }",
            timeout=EXPECT_TIMEOUT,
        )
        probe = (
            "() => { const fl = document.querySelector('.js-plotly-plot')._fullLayout;"
            " return [fl.yaxis3.visible, fl.yaxis4.visible]; }"
        )
        assert page.evaluate(probe) == [False, False]
        # axes.js runs in the tool's page too: the legend is its only toggle.
        entry = page.locator(".legend .traces").filter(has_text="NRMSE (per-point)")
        entry.locator(".legendtoggle").click(force=True)
        page.wait_for_function(
            "() => document.querySelector('.js-plotly-plot')._fullLayout.yaxis4.visible === true",
            timeout=EXPECT_TIMEOUT,
        )
        assert page.evaluate(probe) == [False, True]
```

Name it `test_a_legend_click_on_an_error_trace_shows_its_axis`; its docstring: "The FakeLib project has a baseline and a result for A, so the centre figure carries the three error series (legend-only) and both axes."

- [ ] **Step 2: Run it to see it fail.** `uv run --no-sync pytest -q -m playwright tests/test_author_smoke_playwright.py -k legend_click` → fails at the `wait_for_function` on `yaxis4.visible === true` (the axis exists from Task 1; nothing shows it).

- [ ] **Step 3: Implement.** In `app.py`, `Shell.root()`, before `ui.header` add:

```python
        # The shared axis-follow script (templates/axes.js): its observer
        # wires every plot this page creates. One script for both tiers.
        ui.add_body_html(f"<script>{axes_js_source()}</script>")
```

with `from ..reporting.report_suite import axes_js_source` at the top of `app.py` (`app.py` may import reporting; the Session already does, lazily). The relayout `axes.js` issues (`yaxis4.visible`) reaches `FigureHook._dispatch` as a non-shape relayout with an empty id; `edit_on_plot.py:210` `on_relayout` returns on an empty handle, so nothing is submitted — check that line still reads `if handle:` before relying on it.

- [ ] **Step 4: Run.** `uv run --no-sync pytest -q -m playwright tests/test_author_smoke_playwright.py` → passes. `uv run --no-sync pytest -q tests/test_shell.py tests/test_builtin_capabilities.py tests/test_edit_on_plot.py` → unchanged.

- [ ] **Step 5: Gate and commit.** `git commit -F msg -- src/dstf/authoring/app.py tests/test_author_smoke_playwright.py`. Message: `author: the page carries axes.js — a legend click on an error trace shows its axis`.

---

### Task 4: The token table, rendered into both pages and the tool

**Files:**
- Create: `src/dstf/reporting/theme.py`, `tests/test_theme.py`
- Modify: `src/dstf/reporting/templates/interactive.html:13-109` and its inline `style=` attributes (156, 157, 230, 243, 248, 252, 268), `src/dstf/reporting/templates/dashboard.html:7-148` and line 154, `src/dstf/reporting/report_suite.py:422` (`_render_template` → `jinja_env`), `src/dstf/reporting/dashboard_render.py:35` (`_env`), `src/dstf/authoring/app.py` (`Shell.root`), `tests/test_interactive_figures.py:111-121` (`_render_with_context`)

- [ ] **Step 1: The tests.** Create `tests/test_theme.py`:

```python
"""DESIGN.md "Colors" / "Typography" / "Themes": one token table, rendered
as CSS custom properties into every page both tiers serve. The pages carry
no colour that is not a token, and the type stack ends in a generic
sans-serif (the shipped stack ended in `monospace`, so a Linux browser
without Segoe UI or Roboto rendered the report in monospace)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from dstf.reporting import theme

HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
STYLE_BLOCK = re.compile(r"<style[^>]*>(.*?)</style>", re.S)
STYLE_ATTR = re.compile(r'style="([^"]*)"')


def _colours_outside_root(html: str) -> list[str]:
    """Every hex literal in a <style> block or a style= attribute that is
    not inside the `:root { ... }` token block."""
    found: list[str] = []
    for block in STYLE_BLOCK.findall(html):
        stripped = re.sub(r":root\s*\{[^}]*\}", "", block)
        found += HEX.findall(stripped)
    for attr in STYLE_ATTR.findall(html):
        found += HEX.findall(attr)
    return found


class TestTable:
    def test_default_tokens_are_the_design_systems(self):
        assert theme.DEFAULT_THEME["page"] == "#fafafa"
        assert theme.DEFAULT_THEME["accent"] == "#1976D2"
        assert theme.STATUS["pass"]["fill"] == "#4CAF50"
        assert theme.STATUS["warn"]["dark"] == "#8a6d00"
        assert theme.ROLES["soft_check"]["fg"] == "#5e3a80"

    def test_css_variables_render_every_token_once(self):
        css = theme.css_variables()
        assert css.startswith(":root {")
        for name in theme.DEFAULT_THEME:
            assert css.count(f"--dstf-{name}:") == 1
        assert "--dstf-pass-fill: #4CAF50;" in css
        assert "--dstf-role-companion-border: #a8dfc0;" in css
        assert f"--dstf-font: {theme.FONT_STACK};" in css

    def test_the_type_stack_ends_in_a_generic_sans_serif(self):
        assert theme.FONT_STACK.split(",")[-1].strip() == "sans-serif"
        assert "monospace" not in theme.FONT_STACK
        assert theme.MONO_STACK.split(",")[-1].strip() == "monospace"

    def test_status_and_roles_are_not_part_of_a_theme(self):
        """A theme swaps neutrals and accent; status fills, role chips and
        plot series carry meaning and never change (DESIGN.md "Themes")."""
        assert not set(theme.DEFAULT_THEME) & {"pass", "fail", "soft_check"}
        with pytest.raises(TypeError):
            theme.DEFAULT_THEME["page"] = "#000000"  # type: ignore[index]

    def test_quasar_palette_mirrors_the_table(self):
        palette = theme.quasar_palette()
        assert palette == {
            "primary": "#1976D2",
            "secondary": "#666666",
            "accent": "#1976D2",
            "positive": "#4CAF50",
            "negative": "#f44336",
            "warning": "#ffc107",
            "info": "#607D8B",
        }


class TestPages:
    def test_the_per_test_report_carries_only_tokens(self, tmp_path):
        from test_interactive_figures import _context_with_figure, _render_with_context

        ctx, _ = _context_with_figure()
        html = _render_with_context(tmp_path, ctx).read_text(encoding="utf-8")
        assert ":root {" in html
        assert _colours_outside_root(html) == []
        assert re.search(r"body\s*\{[^}]*font-family:\s*var\(--dstf-font\)", html)

    def test_the_dashboard_carries_only_tokens(self, tmp_path):
        from dstf.reporting.dashboard_render import render_live
        from test_dashboard_render import _write_status_json

        _write_status_json(
            tmp_path,
            {
                "total": 1,
                "elapsed": 0.0,
                "eta_seconds": None,
                "counts": {"queued": 1, "running": 0, "passed": 0, "failed": 0, "timed_out": 0},
                "tests": [
                    {"test_key": "test_0001", "model_id": "Lib.A", "status": "queued",
                     "elapsed": None, "worker_id": None, "report_dir": "test_0001"}
                ],
                "updated_at": 0.0,
            },
        )
        render_live(tmp_path)
        html = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
        assert ":root {" in html
        assert _colours_outside_root(html) == []
        assert re.search(r"body\s*\{[^}]*font-family:\s*var\(--dstf-font\)", html)

    def test_the_templates_on_disk_carry_no_hex_but_the_root_block(self):
        templates = Path(theme.__file__).parent / "templates"
        for name in ("interactive.html", "dashboard.html"):
            html = (templates / name).read_text(encoding="utf-8")
            assert _colours_outside_root(html) == [], name
```

(The snapshot is the one `tests/test_dashboard_render.py::test_render_live_writes_dashboard_html` writes.)

- [ ] **Step 2: Run to see them fail.** `uv run --no-sync pytest tests/test_theme.py -q` → ImportError.

- [ ] **Step 3: The table.** Create `src/dstf/reporting/theme.py`:

```python
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
        "pass": MappingProxyType({"fill": "#4CAF50", "dark": "#2E7D32", "tint": "#f1f8e9", "pill": "#e8f5e9", "border": "#a5d6a7"}),
        "fail": MappingProxyType({"fill": "#f44336", "dark": "#C62828", "tint": "#ffebee", "pill": "#ffebee", "border": "#ef9a9a"}),
        "sim-fail": MappingProxyType({"fill": "#d32f2f", "dark": "#B71C1C", "tint": "#ffebee", "pill": "#ffebee", "border": "#ef9a9a"}),
        "no-ref": MappingProxyType({"fill": "#FF9800", "dark": "#E65100", "tint": "#fff8e1", "pill": "#fff8e1", "border": "#ffe0b2"}),
        "warn": MappingProxyType({"fill": "#ffc107", "dark": "#8a6d00", "tint": "#fff3cd", "pill": "#fff3cd", "border": "#ffc107"}),
        "timed-out": MappingProxyType({"fill": "#9C27B0", "dark": "#6A1B9A", "tint": "#f3e5f5", "pill": "#f3e5f5", "border": "#e1bee7"}),
        "queued": MappingProxyType({"fill": "#888888", "dark": "#555555", "tint": "#f5f5f5", "pill": "#f5f5f5", "border": "#dddddd"}),
        "running": MappingProxyType({"fill": "#2196F3", "dark": "#1976D2", "tint": "#e3f2fd", "pill": "#e3f2fd", "border": "#90caf9"}),
        "pending": MappingProxyType({"fill": "#607D8B", "dark": "#607D8B", "tint": "#eceff1", "pill": "#eceff1", "border": "#b0bec5"}),
    }
)

ROLES: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {
        "soft_check": MappingProxyType({"bg": "#e8d8f3", "fg": "#5e3a80", "border": "#c4a8df"}),
        "companion": MappingProxyType({"bg": "#d8f3e4", "fg": "#2b6b48", "border": "#a8dfc0"}),
    }
)


def css_variables(theme: Mapping[str, str] = DEFAULT_THEME) -> str:
    """The ``:root`` block every page starts its stylesheet with."""
    lines = [f"  --dstf-{name}: {value};" for name, value in theme.items()]
    lines.append(f"  --dstf-font: {FONT_STACK};")
    lines.append(f"  --dstf-mono: {MONO_STACK};")
    for status, parts in STATUS.items():
        lines += [f"  --dstf-{status}-{part}: {value};" for part, value in parts.items()]
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
```

Format with `ruff format` (the long mapping lines wrap).

- [ ] **Step 4: Both Jinja environments carry `dstf_css`.** In `report_suite.py` add

```python
def jinja_env():
    """The per-test report's Jinja environment: the templates dir, autoescape,
    and the token block every page starts its stylesheet with. One
    constructor, so a test that renders a page gets the same globals."""
    from jinja2 import Environment, FileSystemLoader

    from .theme import css_variables

    env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)
    env.globals["dstf_css"] = css_variables()
    return env
```

and make `_render_template` use `template = jinja_env().get_template(template_name)`. `tests/test_interactive_figures.py::_render_with_context` (lines 111-121) builds its own `Environment` — change it to `jinja_env().get_template("interactive.html")`, or `{{ dstf_css }}` renders empty there and Step 1's report test fails on `":root {" in html`. In `dashboard_render` after `_env = Environment(...)` add `_env.globals["dstf_css"] = css_variables()` with `from .theme import css_variables`.

- [ ] **Step 5: The per-test template.** In `interactive.html` the `<style>` block starts `<style>\n{{ dstf_css }}` (autoescape is on and the block has no `<`, `>` or `&`; if a later token ever needs one, mark it `|safe` — a test in Step 1 reads the rendered page and would show `&quot;` in the stack). Then replace every literal, by this table (selectors from the current file):

| Literal | Token |
|---|---|
| body font-family (line 15) | `var(--dstf-font)` |
| `#fafafa` body, `.node` | `var(--dstf-page)` |
| `#333` body, `details summary:hover`, `.node-label`, `.node-details dd` | `var(--dstf-text)` |
| `#555` h2, `details summary`, `.artifacts summary`, `.plot-controls` | `var(--dstf-text-secondary)` |
| `#666` `.ov-label`, `.node-score`, `.node-criterion` | `var(--dstf-text-secondary)` |
| `#888` `.key-stat .label`, `.node-details dt`, `.node-child-count`, `.node-tree-hint` | `var(--dstf-text-muted)` |
| `#999` `.key-stat .ref` | `var(--dstf-text-disabled)` |
| `#ddd` h2, `.key-stat`, `th, td`, `.variable-section`, `.node`, `.metric-tree-section`; `#ccc` `.error-overlay-select` | `var(--dstf-border)` |
| `#eee` `.plot-section`, `.variable-section h2`; `#e0e0e0` `.overlay-picker` | `var(--dstf-border-hairline)` |
| `#f5f5f5` th; `#f9f9f9` `tr:hover`, `.overlay-picker` | `var(--dstf-surface-hover)` |
| `white` / `#fff` backgrounds (`.key-stat`, tables, `.plot-section`) | `var(--dstf-surface)` |
| `#4CAF50` `.pass` | `var(--dstf-pass-fill)` |
| `#f44336` `.fail` | `var(--dstf-fail-fill)` |
| `#FF9800` `.new` | `var(--dstf-no-ref-fill)` |
| `#607D8B` `.info`; `#607d8b` `.summary .pending`, `.node-status.pending` | `var(--dstf-pending-fill)` |
| `#eceff1` `.node-status.pending` | `var(--dstf-pending-pill)` |
| `#ffc107` `.key-stat.changed`, `.artifacts-prominent`, `.warnings-prominent`, `.advisory-chip` borders | `var(--dstf-warn-border)` |
| `#fff9e6` `.key-stat.changed`; `#fff3cd` `.artifacts-prominent`, `.warnings-prominent`, `.advisory-chip`, `#decimation-banner`, the `style="background:#fff3cd"` attributes | `var(--dstf-warn-tint)` |
| `#8a6d00` `.advisory-chip`; `#664d03` `#decimation-banner` text | `var(--dstf-warn-dark)` |
| `#ffe08a` `#decimation-banner` border | `var(--dstf-warn-border)` |
| `#e8f5e9 / #2e7d32 / #a5d6a7` `.var-status.pass`, `.node-status.pass` | `var(--dstf-pass-pill)` / `var(--dstf-pass-dark)` / `var(--dstf-pass-border)` |
| `#ffebee / #c62828 / #ef9a9a` `.var-status.fail`, `.node-status.fail` | `var(--dstf-fail-pill)` / `var(--dstf-fail-dark)` / `var(--dstf-fail-border)` |
| `#e8d8f3 / #5e3a80 / #c4a8df` `.ov-soft_check` | `var(--dstf-role-soft_check-bg)` / `-fg` / `-border` |
| `#d8f3e4 / #2b6b48 / #a8dfc0` `.ov-companion` | `var(--dstf-role-companion-bg)` / `-fg` / `-border` |
| `#2196F3` `.node-leaf` | `var(--dstf-accent)` |
| `#9C27B0` `.node-combinator` text, `#f5f0fa` its background, `#cbb5d6` `.node-combinator-params` | `var(--dstf-text-secondary)`, `var(--dstf-surface-hover)`, `var(--dstf-text-muted)` (Conventions #2) |
| `#b71c1c` `.node-error` | `var(--dstf-sim-fail-dark)` |
| `style="… color:#888 …"` (156, 157, 252) | `color: var(--dstf-text-muted)` |
| `style="… color:#666"` (248) | `color: var(--dstf-text-secondary)` |
| every `font-family: monospace` in the block | `var(--dstf-mono)` |

- [ ] **Step 6: The dashboard template.** Same treatment for `dashboard.html:7-148` and line 154, by this table:

| Literal | Token |
|---|---|
| body font-family (line 8) | `var(--dstf-font)` |
| `#fafafa` body, `#sticky-chrome`, `.cmd-out` | `var(--dstf-page)` |
| `#333` body | `var(--dstf-text)` |
| `#666` `.status-bar`, `.detail-cell` | `var(--dstf-text-secondary)` |
| `#555` `.counter.queued .value`, `.counter.total`, `.cmd-kind-label` | `var(--dstf-text-secondary)` |
| `#888` `.counter .label`, `.counter.queued`, `tr.queued td`, `.status-badge.queued`, `.unscored`, `.hidden-hint`, `.help-link`, line 154 | `var(--dstf-text-muted)` (the queued badge/counter: `var(--dstf-queued-fill)`) |
| `#ddd` `#sticky-chrome`, `.counter`, `th, td`; `#ccc` `input.col-filter`, `.sel-footer button`, `.cmd-out`, `.cmd-kind-label select` | `var(--dstf-border)` |
| `#eee` `.bar` | `var(--dstf-border-hairline)` |
| `#f5f5f5` `.counter:hover`, `th`, `th.sel-col:hover`; `#e8e8e8` `th:hover`; `#f0f0f0` `.sel-footer button:hover` | `var(--dstf-surface-hover)` |
| `white` backgrounds (`.counter`, tables, `.sel-footer`, inputs) | `var(--dstf-surface)` |
| `#1976D2` `.counter.active` shadow, `.counter.running .value`, `th.sorted-*::after`, `a`, `.sel-footer` border-top, `.count`, `.btn-primary`, `.help-link:hover` | `var(--dstf-accent)` (`.counter.running .value`: `var(--dstf-running-dark)`) |
| `#1565C0` `.btn-primary:hover` | `var(--dstf-accent-hover)` |
| `#e3f2fd` `.counter.active`, `th.resizing`, `tr.running td`, `tr.row-selected td` | `var(--dstf-selection)` (`tr.running td`: `var(--dstf-running-tint)`) |
| `rgba(25,118,210,.2)` `.col-resize-handle:hover` | `var(--dstf-selection)` |
| `rgba(0,0,0,.1)` `.sel-footer` shadow | `var(--dstf-shadow)` |
| `#2196F3` `.counter.running`, `.bar .b-running`, `.status-badge.running` | `var(--dstf-running-fill)` |
| `#4CAF50` `.counter.passed`, `.bar .b-passed`, `.status-badge.pass`, `.save-status`; `#2E7D32` `.counter.passed .value` | `var(--dstf-pass-fill)`; `var(--dstf-pass-dark)` |
| `#f44336` `.counter.failed`, `.bar .b-failed`, `.status-badge.fail`; `#C62828` `.counter.failed .value` | `var(--dstf-fail-fill)`; `var(--dstf-fail-dark)` |
| `#d32f2f` `.counter.sim_failed`, `.status-badge.sim-fail`; `#B71C1C` `.counter.sim_failed .value` | `var(--dstf-sim-fail-fill)`; `var(--dstf-sim-fail-dark)` |
| `#FF9800` `.counter.no_ref`, `.status-badge.no-ref`; `#E65100` `.counter.no_ref .value` | `var(--dstf-no-ref-fill)`; `var(--dstf-no-ref-dark)` |
| `#ff9800` `.counter.warnings`, `.warn`; `#E65100` `.counter.warnings .value` | `var(--dstf-warn-fill)`; `var(--dstf-warn-dark)` |
| `#9C27B0` `.counter.timed_out`, `.bar .b-timed_out`, `.status-badge.timed-out`; `#6A1B9A` `.counter.timed_out .value` | `var(--dstf-timed-out-fill)`; `var(--dstf-timed-out-dark)` |
| `#f1f8e9` `tr.pass td`; `#ffebee` `tr.fail td`, `tr.sim-fail td`; `#fff8e1` `tr.no-ref td`; `#f3e5f5` `tr.timed-out td` | `var(--dstf-pass-tint)`; `var(--dstf-fail-tint)`; `var(--dstf-no-ref-tint)`; `var(--dstf-timed-out-tint)` |
| `font-family: monospace` (`.cmd-out`, `.cmd-kind-label select`) | `var(--dstf-mono)` |

`dashboard.js` (inlined at line 298) carries no colour; if the Step 1 test finds one, it moved — map it the same way.

- [ ] **Step 7: The tool.** In `Shell.root()` after the `add_body_html` line: `ui.colors(**quasar_palette())` and `ui.add_css(css_variables())` (import both from `..reporting.theme`). The shell's own `_BADGE_COLOR` keeps Quasar names — those now resolve to the table's fills.

- [ ] **Step 8: Run.** `uv run --no-sync pytest tests/test_theme.py tests/test_report_suite.py tests/test_dashboard_render.py tests/test_interactive_figures.py -q` and `uv run --no-sync pytest -q -m playwright` → pass. Then a look: `uv run --no-sync pytest -q -m playwright tests/test_theme.py` is not enough for the font — open one rendered page from `tmp_path` in Chromium (a five-line Playwright script in the scratchpad) and assert `getComputedStyle(document.body).fontFamily` ends with `sans-serif`; record the line in the commit message.

- [ ] **Step 9: Gate and commit.**

```bash
git add src/dstf/reporting/theme.py tests/test_theme.py
git commit -F msg -- src/dstf/reporting/theme.py src/dstf/reporting/templates/interactive.html src/dstf/reporting/templates/dashboard.html src/dstf/reporting/report_suite.py src/dstf/reporting/dashboard_render.py src/dstf/authoring/app.py tests/test_theme.py tests/test_interactive_figures.py
```
Message: `reporting: one token table (theme.py) renders the visual system into both pages and the tool; the type stack ends in sans-serif`.

---

### Task 5: LTTB with the bucket arithmetic hoisted out of the loop

**Files:**
- Modify: `src/dstf/figures/decimate.py` (`lttb`)
- Test: `tests/test_decimate.py`

- [ ] **Step 1: The regression test.** Append to `tests/test_decimate.py`:

```python
def _lttb_reference(time, values, n_out):
    """The loop as shipped before the hoist, kept verbatim as the oracle."""
    n_in = len(time)
    t = np.asarray(time, dtype=np.float64)
    v = np.asarray(values, dtype=np.float64)
    bucket_size = (n_in - 2) / (n_out - 2)
    out_idx = np.empty(n_out, dtype=np.int64)
    out_idx[0] = 0
    out_idx[-1] = n_in - 1
    a = 0
    for i in range(n_out - 2):
        bucket_start = int(np.floor(i * bucket_size)) + 1
        bucket_end = int(np.floor((i + 1) * bucket_size)) + 1
        bucket_end = min(bucket_end, n_in - 1)
        if bucket_start >= bucket_end:
            chosen = max(a + 1, min(bucket_start, n_in - 2))
            out_idx[i + 1] = chosen
            a = chosen
            continue
        next_start = bucket_end
        next_end = int(np.floor((i + 2) * bucket_size)) + 1
        next_end = min(next_end, n_in)
        if next_end > next_start:
            avg_t = float(np.mean(t[next_start:next_end]))
            avg_v = float(np.mean(v[next_start:next_end]))
        else:
            avg_t, avg_v = t[-1], v[-1]
        ta, va = t[a], v[a]
        seg_t = t[bucket_start:bucket_end]
        seg_v = v[bucket_start:bucket_end]
        area = np.abs((ta - avg_t) * (seg_v - va) - (ta - seg_t) * (avg_v - va))
        chosen = bucket_start + int(np.argmax(area))
        out_idx[i + 1] = chosen
        a = chosen
    return t[out_idx], v[out_idx]


class TestHoistedLoopIsTheSameAlgorithm:
    """On non-degenerate data the hoisted loop picks the same points. On
    COLLINEAR data every triangle area is round-off noise and the argmax
    may land on a different sample of the same line — ``np.add.reduceat``
    does not sum pairwise as ``np.mean`` does (review measured 1.8e-12
    differences and 411 of 1000 flipped picks on ``v = 3t + 1``). That
    pick is as good as the old one: the samples lie on the line. The
    docstring on ``lttb`` says so."""

    @pytest.mark.parametrize("n_in,n_out", [(11, 5), (1000, 7), (2001, 1000), (50_000, 1000), (1003, 1002)])
    @pytest.mark.parametrize("seed", [0, 1, 2])
    def test_same_points_as_the_reference_loop_on_non_degenerate_data(self, n_in, n_out, seed):
        rng = np.random.default_rng(seed)
        t = np.sort(rng.uniform(0.0, 100.0, n_in))
        v = np.cumsum(rng.standard_normal(n_in))
        want_t, want_v = _lttb_reference(t, v, n_out)
        got_t, got_v = lttb(t, v, n_out)
        np.testing.assert_array_equal(got_t, want_t)
        np.testing.assert_array_equal(got_v, want_v)

    def test_a_bucket_narrower_than_a_point_still_advances(self):
        # n_out close to n_in makes empty buckets; the reference's
        # `max(a + 1, ...)` rule must be reproduced exactly.
        t = np.arange(20.0)
        v = t**2
        want = _lttb_reference(t, v, 19)
        got = lttb(t, v, 19)
        np.testing.assert_array_equal(got[0], want[0])
```

- [ ] **Step 2: Run to see it pass against the unchanged code** (`uv run --no-sync pytest tests/test_decimate.py -q`): the oracle IS the shipped loop, so this passes now — the value is that Step 3 cannot change the output. Time the shipped loop: `uv run --no-sync python -c "import time,numpy as np;from dstf.figures.decimate import lttb;t=np.linspace(0,100,20000);v=np.sin(t);lttb(t,v,1000);s=time.perf_counter();[lttb(t,v,1000) for _ in range(5)];print((time.perf_counter()-s)/5*1e3,'ms')"` → about 13 ms.

- [ ] **Step 3: Hoist.** Replace the body of `lttb` from `bucket_size = ...` to the `return` with:

```python
    bucket_size = (n_in - 2) / (n_out - 2)
    out_idx = np.empty(n_out, dtype=np.int64)
    out_idx[0] = 0
    out_idx[-1] = n_in - 1

    # Every bucket boundary at once: bucket i is [starts[i], ends[i]) and
    # the bucket after it — whose centroid the triangle uses — is
    # [ends[i], next_ends[i]). Same floor arithmetic as the loop had.
    b = np.floor(np.arange(n_out, dtype=np.float64) * bucket_size).astype(np.int64) + 1
    starts = b[: n_out - 2]
    ends = np.minimum(b[1 : n_out - 1], n_in - 1)
    next_ends = np.minimum(b[2:n_out], n_in)
    # Next-bucket means, one reduceat each. ``reduceat`` returns the single
    # element at an index that is not below the next one, so the empty
    # next-buckets are masked afterwards and fall back to the last point.
    counts = next_ends - ends
    sum_t = np.add.reduceat(t, ends)
    sum_v = np.add.reduceat(v, ends)
    # reduceat's last segment runs to the end of the array; the last
    # next-bucket ends at next_ends[-1] (== n_in here, so it agrees).
    has_next = counts > 0
    avg_t = np.where(has_next, sum_t / np.maximum(counts, 1), t[-1])
    avg_v = np.where(has_next, sum_v / np.maximum(counts, 1), v[-1])

    a = 0
    for i in range(n_out - 2):
        bucket_start = int(starts[i])
        bucket_end = int(ends[i])
        if bucket_start >= bucket_end:
            chosen = max(a + 1, min(bucket_start, n_in - 2))
            out_idx[i + 1] = chosen
            a = chosen
            continue
        ta, va = t[a], v[a]
        seg_t = t[bucket_start:bucket_end]
        seg_v = v[bucket_start:bucket_end]
        area = np.abs((ta - avg_t[i]) * (seg_v - va) - (ta - seg_t) * (avg_v[i] - va))
        chosen = bucket_start + int(np.argmax(area))
        out_idx[i + 1] = chosen
        a = chosen

    return t[out_idx], v[out_idx]
```

Add to `lttb`'s docstring: "Bucket means come from ``np.add.reduceat``, which sums a segment left to right where ``np.mean`` sums pairwise; the two differ by round-off, which changes a pick only where every candidate's triangle area IS round-off — collinear data — and then the other sample lies on the same line." `np.add.reduceat(t, ends)` sums `t[ends[i]:ends[i+1]]` for every `i` but the last, which sums to the array's end. `ends[i+1] == min(b[i+2], n_in-1)` and `next_ends[i] == min(b[i+2], n_in)` differ only when `b[i+2] >= n_in - 1`, i.e. for the last interior bucket, whose reduceat segment runs to `n_in` anyway; and a segment with `ends[i] >= ends[i+1]` returns `t[ends[i]]`, which `has_next` masks out. The regression test in Step 1 is what says this index reasoning holds on non-degenerate data — a mismatch fails it by index; the review checked 60 non-degenerate cases including (1003, 1002), (20, 19) and (7, 3) with no difference.

- [ ] **Step 4: Run.** `uv run --no-sync pytest tests/test_decimate.py tests/test_figures.py tests/test_figure_goldens.py -q` → pass. Re-run the timing line → record the number in the commit message (expected: the per-trace cost drops by the two `np.mean` calls and the float conversions; well under 13 ms, still a Python loop).

- [ ] **Step 5: Gate and commit.** `git commit -F msg -- src/dstf/figures/decimate.py tests/test_decimate.py`. Message: `figures: LTTB's bucket bounds and next-bucket means computed once; same picks on non-degenerate data, a same-line pick on collinear data (<n> ms per trace, was 13)`.

---

### Task 6: The sibling-backend overlay index is warmed at open, under a lock

**Files:**
- Modify: `src/dstf/reporting/overlay_loader.py:138-232`, `src/dstf/authoring/session.py` (`open`)
- Test: `tests/test_overlay_loader.py`, `tests/test_session.py`

- [ ] **Step 1: Tests.** In `tests/test_overlay_loader.py`, class `TestSiblingBackendOverlays` (its `setup_method` clears the cache; `_write_ref` and `_FakeConfigForSibling` are the module's helpers), add:

```python
    def test_warm_builds_the_index_once_and_a_later_load_hits_the_cache(self, tmp_path):
        from dstf.reporting.overlay_loader import (
            _sibling_backend_index,
            load_sibling_backend_overlays,
            warm_sibling_backend_index,
        )

        _write_ref(tmp_path / "Dymola" / "windows", "ref_0001", "M")
        config = _FakeConfigForSibling(tmp_path, "OpenModelica", "linux")
        _sibling_backend_index.cache_clear()
        warm_sibling_backend_index(config)
        after_warm = _sibling_backend_index.cache_info()
        load_sibling_backend_overlays(config, "M")
        after_load = _sibling_backend_index.cache_info()
        assert after_warm.misses == 1 and after_warm.hits == 0
        assert after_load.misses == 1 and after_load.hits == 1

    def test_two_threads_warming_at_once_build_the_index_once(self, tmp_path, monkeypatch):
        import threading

        from dstf.reporting import overlay_loader as ol

        _write_ref(tmp_path / "Dymola" / "windows", "ref_0001", "M")
        config = _FakeConfigForSibling(tmp_path, "OpenModelica", "linux")
        ol._sibling_backend_index.cache_clear()
        builds = []
        real = ol._sibling_backend_index.__wrapped__

        def slow(*args):
            builds.append(threading.current_thread().name)
            time.sleep(0.2)
            return real(*args)

        monkeypatch.setattr(ol, "_sibling_backend_index", functools.lru_cache(maxsize=8)(slow))
        threads = [threading.Thread(target=ol.warm_sibling_backend_index, args=(config,)) for _ in range(3)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        assert len(builds) == 1
```

(add `import functools`, `import time` at the top if absent). In `tests/test_session.py`, class `TestOpen`, add:

```python
    def test_open_warms_the_overlay_index_off_the_calling_thread(self, tmp_path):
        """Brief §2.2: the first click paid 3 s for the sibling-backend
        index. It is built once, right after open, on its own thread."""
        from dstf.reporting import overlay_loader as ol

        cfg, work = _project(tmp_path)
        ol._sibling_backend_index.cache_clear()
        s = Session()
        s.open(cfg, work_dir=work)
        assert s.overlay_index_ready.wait(5.0)
        before = ol._sibling_backend_index.cache_info()
        s.view(A, "x")
        after = ol._sibling_backend_index.cache_info()
        assert before.misses == 1
        assert after.misses == 1 and after.hits == before.hits + 1
```

- [ ] **Step 2: Run to see them fail.** ImportError on `warm_sibling_backend_index`; AttributeError on `overlay_index_ready`.

- [ ] **Step 3: Implement.** In `overlay_loader.py`, above `load_sibling_backend_overlays`:

```python
_INDEX_LOCK = threading.Lock()


def index_args(config) -> tuple[str, str, str] | None:
    """The three values the sibling index is keyed by, or None when the
    config names no reference root."""
    reference_root = getattr(config, "reference_root", None)
    if reference_root is None:
        return None
    return (
        str(reference_root),
        getattr(config, "backend", None) or "",
        getattr(config, "os_name", None) or "",
    )


def sibling_backend_index(reference_root: str, current_backend: str, current_os: str):
    """The cached index, built under a lock: ``lru_cache`` does not
    serialise concurrent first calls, and the authoring Session warms the
    index on a thread while a click may ask for it."""
    with _INDEX_LOCK:
        return _sibling_backend_index(reference_root, current_backend, current_os)


def warm_sibling_backend_index(config) -> None:
    """Build the index for ``config`` now (the tool calls this right after
    open, off the UI thread), so no click pays for it."""
    args = index_args(config)
    if args is not None:
        sibling_backend_index(*args)
```

`import threading` at the top. `load_sibling_backend_overlays` becomes:

```python
    args = index_args(config) if config is not None else None
    if args is None:
        return []
    index = sibling_backend_index(*args)
    matches = index.get(model_id, ())
    ... (unchanged from here)
```

In `session.py`: `self.overlay_index_ready = threading.Event()` in `__init__` (set nothing; no project). At the end of `open`, after `self._reload_results()` — a FRESH event per open, captured by the thread, so a thread from a previous open that finishes late cannot mark this open ready:

```python
        ready = threading.Event()
        self.overlay_index_ready = ready
        threading.Thread(
            target=self._warm_overlay_index,
            args=(self.project.config, ready),
            name="dstf-overlay-index",
            daemon=True,
        ).start()
```

and

```python
    def _warm_overlay_index(self, config: Config, ready: threading.Event) -> None:
        from ..reporting.overlay_loader import warm_sibling_backend_index

        try:
            warm_sibling_backend_index(config)
        except Exception:  # a broken sibling file must not kill the thread silently
            logger.exception("warming the sibling-backend overlay index failed")
        finally:
            ready.set()
```

- [ ] **Step 4: Run.** `uv run --no-sync pytest tests/test_overlay_loader.py tests/test_session.py -q` → pass.

- [ ] **Step 5: Gate and commit.** `git commit -F msg -- src/dstf/reporting/overlay_loader.py src/dstf/authoring/session.py tests/test_overlay_loader.py tests/test_session.py`. Message: `author: the sibling-backend overlay index is built once at open, on its own thread, under a lock`.

---

### Task 7: The Session reads a model from disk once, and decimates its base traces once

**Files:**
- Modify: `src/dstf/authoring/session.py` (`view`, `figure`, `_reload_results`, `open`, `close`), `src/dstf/figures/view.py` (`decimate_view`, `_decimate_overlay`)
- Test: `tests/test_session.py`, `tests/test_figures.py`

- [ ] **Step 1: Tests.** In `tests/test_figures.py`, class `TestView`, add:

```python
    def test_decimating_an_already_decimated_view_keeps_the_original_counts(self):
        """The Session caches a decimated base view and decimates it again
        with the leaves; the banner must still say 2001, not 100."""
        once = decimate_view(_tube_view(n=2001), 100)
        twice = decimate_view(once, 100)
        assert twice.full_samples["act"] == 2001
        assert twice.full_samples["ref"] == 2001
        assert all(twice.full_samples[s.id] == 2001 for s in twice.errors)
        np.testing.assert_array_equal(twice.act_time, once.act_time)
```

In `tests/test_session.py`, a new class:

```python
class TestReadCache:
    # `compare_one` reads the reference too (comparator.py:569), on every
    # rescore, so the spy is on the overlay load: `_reads_for` is the only
    # caller of `load_overlays` in the Session.
    @staticmethod
    def _spy_overlays(monkeypatch):
        from dstf.reporting import overlay_loader as ol

        loads = []
        real = ol.load_overlays
        monkeypatch.setattr(
            ol, "load_overlays", lambda store, m, config=None: loads.append(m) or real(store, m, config=config)
        )
        return loads

    def test_a_second_view_of_a_model_reads_nothing_from_disk(self, session, monkeypatch):
        loads = self._spy_overlays(monkeypatch)
        session.view(A, "x")
        session.view(A, "x")
        assert loads == [A]

    def test_a_second_figure_decimates_only_the_leaves(self, session, monkeypatch):
        from dstf.figures import view as view_mod

        calls = []
        real = view_mod.lttb
        monkeypatch.setattr(view_mod, "lttb", lambda t, v, n: calls.append(len(t)) or real(t, v, n))
        session.figure(A, "x")
        first = len(calls)
        session.figure(A, "x")
        # The base — actual, reference, three error series (FakeLib has no
        # overlays) — is decimated on the first call only; a second call
        # decimates the leaves' Series primitives and nothing else. Bands go
        # through decimate.py's own `lttb`, which this spy does not see.
        from dstf.comparison.geometry import Series

        leaf_series = sum(
            isinstance(p, Series)
            for leaf in session.view(A, "x").leaves
            for p in leaf.geometry
        )
        assert first == 5 + leaf_series
        assert len(calls) - first == leaf_series

    def test_a_results_reload_drops_the_cache(self, session, monkeypatch):
        loads = self._spy_overlays(monkeypatch)
        session.view(A, "x")
        session._reload_results()
        session.view(A, "x")
        assert loads == [A, A]

    def test_a_store_write_drops_the_cache(self, session, monkeypatch):
        loads = self._spy_overlays(monkeypatch)
        session.view(D, "x")
        session.apply(StoreWrite(run=lambda store: "", models=frozenset({D})))
        session.view(D, "x")
        assert loads == [D, D]

    def test_a_staged_edit_keeps_the_cache_and_changes_the_leaves(self, session):
        session.figure(A, "x")
        session.stage([LOOSEN])
        fig = session.figure(A, "x")
        leaf = next(tr for tr in fig["data"] if tr["meta"].get("kind") == "act")
        assert leaf  # the base traces are still drawn
        assert session.comparisons[A].passed is True  # the loosened verdict

    def test_close_drops_every_cached_read(self, session):
        session.view(A, "x")
        session.close()
        assert session._reads == {}
```

- [ ] **Step 2: Run to see them fail.** `uv run --no-sync pytest tests/test_session.py -k ReadCache tests/test_figures.py -k already_decimated -q` → the figure one fails on `full_samples["act"] == 100`; `_reads` is missing.

- [ ] **Step 3: `decimate_view` keeps stamped counts.** In `view.py`, `decimate_view`: replace the `full` dict construction with

```python
    # Pre-decimation counts, keyed by primitive id, so the figure can mark
    # every decimated trace — bands, spectra and error series included. A
    # count already stamped (a cached base view decimated again) is kept.
    full: dict[str, int] = dict(view.full_samples)
    full.setdefault("act", 0 if view.act_time is None else len(view.act_time))
    full.setdefault("ref", 0 if view.ref_time is None else len(view.ref_time))
```

and in `_prim` use `full.setdefault(p.id, len(p.time))` / `full.setdefault(p.id, len(p.x))`. In `_decimate_overlay`: `full_samples=len(ov.time) if ov.full_samples is None else ov.full_samples`.

- [ ] **Step 4: The Session cache.** In `session.py` add near `Selection`:

```python
@dataclass
class _ModelReads:
    """What one click re-read from disk before this cache (brief §2.2): the
    primary reference and the overlays; plus the decimated base view per
    variable — trajectories, overlays and error series, no leaves — so an
    edit re-decimates only the criterion's geometry."""

    ref: dict | None
    overlays: list
    bases: dict[str, VariableView] = field(default_factory=dict)
```

In `__init__`: `self._reads: dict[str, _ModelReads] = {}`. Add:

```python
    def _reads_for(self, model_id: str) -> _ModelReads:
        from ..reporting.overlay_loader import load_overlays

        project = self._require_project()
        reads = self._reads.get(model_id)
        if reads is None:
            reads = _ModelReads(
                ref=project.store.get_reference(model_id),
                overlays=load_overlays(project.store, model_id, config=project.config),
            )
            self._reads[model_id] = reads
        return reads
```

`view()` uses `reads = self._reads_for(model_id)`, `ref = reads.ref`, `overlays = reads.overlays` in place of the two calls. `figure()` becomes:

```python
    def figure(self, model_id: str, variable: str) -> dict:
        """The tool's figure is the editable one — handle rings a user can
        drag (``variable_figure(..., editable=True)``); the report's is not.
        The base traces are decimated once per model and variable and
        cached; only the leaves' geometry is decimated per call."""
        from ..figures.plotly import variable_figure
        from ..figures.view import build_view, decimate_view

        project = self._require_project()
        budget = project.config.max_embedded_samples
        reads = self._reads_for(model_id)
        base = reads.bases.get(variable)
        if base is None:
            base = decimate_view(replace(self.view(model_id, variable), leaves=()), budget)
            reads.bases[variable] = base
        comp = self.comparisons.get(model_id)
        leaves = leaves_by_variable(comp.metric_tree if comp is not None else None).get(
            variable, []
        )
        # The leaves are decimated on their own (no arrays but their
        # geometry) and joined to the cached base; the figure is then built
        # WITHOUT a budget, or `variable_figure` would decimate the base a
        # second time. The base's counts win the merge: a leaves-only view
        # stamps "act"/"ref" as 0.
        leaves_view = decimate_view(build_view(variable, leaves=leaves), budget)
        full = dict(leaves_view.full_samples)
        full.update(base.full_samples)
        with_leaves = replace(base, leaves=leaves_view.leaves, full_samples=full)
        return variable_figure(with_leaves, None, editable=True)
```

(`from dataclasses import dataclass, field, replace`.) Invalidate: in `_reload_results` after `project.results = fresh`: `for model_id in list(self._reads): if model_id not in locked: del self._reads[model_id]`; in `open` (at the commit point) and `close`: `self._reads = {}`. `StoreWrite` already calls `_reload_results`.

- [ ] **Step 5: Run.** `uv run --no-sync pytest tests/test_session.py tests/test_figures.py tests/test_shell.py tests/test_edit_on_plot.py tests/test_builtin_capabilities.py -q` → pass. `test_the_tools_figure_rings_every_marker_handle` in `test_session.py` still passes: rings come from the leaves, decimated per call.

- [ ] **Step 6: Gate and commit.** `git commit -F msg -- src/dstf/authoring/session.py src/dstf/figures/view.py tests/test_session.py tests/test_figures.py`. Message: `author: a model's reference and overlays are read once, its base traces decimated once; an edit re-decimates only its criterion`.

---

### Task 8: `loading` events — the Session reports its phases and scores each result as it is read

**Files:**
- Modify: `src/dstf/backends/base.py:181-240` (`_apply_run_verdicts` → `run_verdicts`), `:600-700` (`read_results`, `read_last_results`), `src/dstf/discovery/test_registry.py:174-209` (`scan_source_tests`), `src/dstf/authoring/session.py` (`Event`, `open`, `_reload_results`)
- Test: `tests/test_discovery.py`, `tests/test_session.py`

- [ ] **Step 1: Tests.** In `tests/test_session.py`, class `TestOpen` (the `_project` work dir carries a hand-written `batch_manifest.json` and one `result.json` per model but C; the backend the Session built reads them):

```python
    def test_read_last_results_reports_each_result_with_its_run_verdict_applied(
        self, tmp_path
    ):
        """The tool scores each result as it lands (Session._reload_results);
        a test whose run failed must arrive already forced to success=False,
        or it is scored green for the seconds before the batch verdict."""
        cfg, work = _project(tmp_path)
        # Record A's last run as failed in the manifest _project wrote. The
        # per-test entries live under the key BatchManifest.load reads as
        # `.manifest` (src/dstf/storage/batch_manifest.py); A's result.json
        # still reads as a success.
        path = work / "batch_manifest.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        for entry in data["manifest"].values():
            if entry["model_id"] == A:
                entry["last_run_status"] = "failed"
        path.write_text(json.dumps(data), encoding="utf-8")
        s = Session()
        s.open(cfg, work_dir=work)
        seen = []
        results = s.project.backend.read_last_results(
            s.project.tests,
            on_result=lambda model_id, result: seen.append((model_id, result.success)),
        )
        assert sorted(seen) == sorted((m, r.success) for m, r in results.items())
        assert dict(seen)[A] is False  # the verdict, before the callback
        assert dict(seen)[D] is True
```

In `tests/test_discovery.py`, class `TestDiscoverTests`:

```python
    def test_scan_reports_progress_per_file(self, tmp_path):
        from dstf.discovery.test_registry import scan_source_tests

        config = _write_lib(tmp_path)
        ticks = []
        scanned = scan_source_tests(config, progress=lambda done, total: ticks.append((done, total)))
        assert scanned
        assert ticks[-1][0] == ticks[-1][1] == len(list(config.library_dir.rglob("*.mo")))
        assert [d for d, _ in ticks] == list(range(1, len(ticks) + 1))
```

In `tests/test_session.py`, class `TestOpen`:

```python
    def test_open_reports_its_phases_and_every_scored_test(self, tmp_path):
        """Brief §3.8: the page shows "scanning… N found", fills rows as
        results are scored, and a test opens as soon as it is scored."""
        cfg, work = _project(tmp_path)
        s = Session()
        events = []
        s.on("loading", events.append)
        s.on("results", lambda e: events.append({"event": "results", **e}))
        s.open(cfg, work_dir=work)
        phases = [e["phase"] for e in events if e["event"] == "loading"]
        assert phases[0] == "scan" and phases[-1] == "done"  # "scanning…" fires even for a source that scans nothing
        scored = [e["model"] for e in events if e.get("phase") == "results"]
        # Every model the manifest names: C has no result.json and the reader
        # returns it as a failed result, which is read and announced too.
        assert set(scored) == {A, B, C, D, G}
        # Each results event names a model already scored.
        for e in events:
            if e.get("phase") == "results":
                assert e["done"] <= e["total"]
        # ONE batch results event, after the per-test events (Conventions #7).
        kinds = [e["event"] for e in events]
        assert kinds.count("results") == 1
        assert kinds.index("results") > max(i for i, e in enumerate(events) if e.get("phase") == "results")
        assert events[-1]["phase"] == "done" and events[-1]["seconds"] >= 0

    def test_a_test_is_scored_when_its_loading_event_fires(self, tmp_path):
        cfg, work = _project(tmp_path)
        s = Session()
        verdicts = {}
        s.on("loading", lambda e: verdicts.update({e["model"]: s.comparisons[e["model"]].passed})
             if e["phase"] == "results" else None)
        s.open(cfg, work_dir=work)
        assert verdicts[A] is False and verdicts[D] is True
```

- [ ] **Step 2: Run to see them fail.** `TypeError: unexpected keyword 'on_result'` / `'progress'`; no `loading` events.

- [ ] **Step 3: The per-result run verdict.** In `base.py` replace `_apply_run_verdicts` with a pure lookup plus the mutating wrapper:

```python
def run_verdicts(work_dir: Path, manifests: list[BatchManifest]) -> dict[str, str]:
    """``model_id -> failed run status`` for every test whose last run the
    manifests record as failed or timed out, falling back to ``status.json``
    for tests the manifests are not authoritative about (a pre-field entry,
    or a ``pending`` one from an aborted run). See the docstring that was on
    ``_apply_run_verdicts`` for why the run's verdict, not the disk read,
    is authoritative."""
    from .progress import read_status

    verdicts: dict[str, str] = {}
    covered: set[str] = set()
    for manifest in manifests:
        for entry in manifest.manifest.values():
            status = entry.get("last_run_status")
            if status not in RECORDED_RUN_STATUSES:
                continue
            covered.add(entry["model_id"])
            if status in FAILED_RUN_STATUSES:
                verdicts[entry["model_id"]] = status
    snap = read_status(work_dir)
    for row in (snap or {}).get("tests", []):
        model_id = row.get("model_id")
        if model_id in covered:
            continue
        if row.get("status") in FAILED_RUN_STATUSES:
            verdicts[model_id] = row["status"]
    return verdicts


def _apply_run_verdicts(
    work_dir: Path, manifests: list[BatchManifest], results: dict[str, "TestResult"]
) -> None:
    """Force results whose last run failed/timed out to ``success=False``.
    Mutates *results* in place."""
    for model_id, status in run_verdicts(work_dir, manifests).items():
        _force_failed(results.get(model_id), status)
```

(Move the old docstring's reasoning onto `run_verdicts`; delete `_apply_status_verdicts`.) `read_results(self, manifests, tests, on_result=None)`: after each `results[model_id] = ...` assignment — the two early failures and the pool loop — call `if on_result is not None: on_result(model_id, results[model_id])`. `read_last_results(self, tests, on_result=None)`:

```python
        manifests = [BatchManifest.load(p) for p in manifest_paths]
        verdicts = run_verdicts(work_dir, manifests)

        def _with_verdict(model_id: str, result: "TestResult") -> None:
            if model_id in verdicts:
                _force_failed(result, verdicts[model_id])
            if on_result is not None:
                on_result(model_id, result)

        results = self.read_results(manifests, tests, on_result=_with_verdict)
        return results
```

(`_force_failed` on a result already forced is idempotent — check its body; the batch `_apply_run_verdicts` call is gone from this path.) The callback type: `Callable[[str, "TestResult"], None] | None`. Any other `read_results` override (`tests/test_cli.py:390` has a fake; `grep -rn "def read_results" src tests`) gains the optional parameter.

- [ ] **Step 4: Scan progress.** `scan_source_tests(config, progress: Callable[[int, int], None] | None = None)`: after `for mo_file in sorted(...)` becomes `files = sorted(library_dir.rglob("*.mo"))` then `for done, mo_file in enumerate(files, start=1): ... ; if progress is not None: progress(done, len(files))` (the progress call at the end of each file's loop body).

- [ ] **Step 5: The Session.** `Event = Literal["spec", "results", "job", "selection", "loading"]`. In `open`:

```python
        started = time.monotonic()
        # "scanning library…" before the first file — and the only scan
        # event for a source type that scans nothing (Python, FMU, Julia).
        self._emit("loading", phase="scan", done=0, total=0)
        scanned = scan_source_tests(
            config,
            progress=lambda done, total: self._emit(
                "loading", phase="scan", done=done, total=total
            ),
        )
```

(Task 9 wraps this in the cache.) At the end of `open`, after `_reload_results()` and the overlay thread: `self._emit("loading", phase="done", seconds=time.monotonic() - started)`. `_reload_results` scores per arrival:

```python
    def _reload_results(self) -> None:
        """Re-read the last run and rescore — every model except the ones a
        running job has locked (their directories are being rewritten, so
        their in-memory result and verdict stand until that job's own
        completion reload). Each result is scored as it is read and
        announced as ``loading(phase="results")``; ONE ``results`` event
        follows for the batch."""
        project = self._require_project()
        locked = self.running.models if self.running is not None else frozenset()
        to_read = [t for t in project.tests if t.model_id not in locked]
        project.results = {
            m: r for m, r in project.results.items() if m in locked
        }
        for model_id in list(self._reads):
            if model_id not in locked:
                del self._reads[model_id]
        done = 0

        def on_result(model_id: str, result: TestResult) -> None:
            nonlocal done
            if model_id in locked:
                # `read_results` reports every manifest entry, and a locked
                # model — excluded from `to_read` — comes back as "not found
                # in discovery": storing it would rescore the model to
                # SIM_FAIL in the middle of its own job.
                return
            project.results[model_id] = result
            if project.test(model_id) is not None:
                self._score(model_id)
                done += 1
                self._emit(
                    "loading",
                    phase="results",
                    model=model_id,
                    done=done,
                    total=len(to_read),
                )

        project.backend.read_last_results(to_read, on_result=on_result)
        # Tests with no result at all (never run, or dropped from the
        # manifest) are scored now — NO_REF / SIM_FAIL states.
        for test in to_read:
            if test.model_id not in project.results:
                self._score(test.model_id)
        self._emit("results", models={t.model_id for t in to_read})
```

`_score` reads `project.results.get(model_id)`, so a model is scored against the result that just landed. `on_result` runs on the calling thread (`read_results` invokes it from its own `as_completed` loop), so no listener runs on a pool thread.

- [ ] **Step 6: Run.** `uv run --no-sync pytest tests/test_session.py tests/test_compare_accept.py tests/test_discovery.py tests/test_shell.py tests/test_accept_and_rerun.py tests/test_cli.py -q` → pass. `test_open_emits_spec_before_results` still holds (`spec` is emitted after the merge, before the reload); `test_a_reload_during_a_job_keeps_the_locked_model_as_it_was` is the guard for the `locked` return in `on_result`.

- [ ] **Step 7: Gate and commit.** `git commit -F msg -- src/dstf/backends/base.py src/dstf/discovery/test_registry.py src/dstf/authoring/session.py tests/test_discovery.py tests/test_session.py`. Message: `author: the Session reports its phases — scan progress, each result scored as it is read, done — as loading events`.

---

### Task 9: The scan cache, and a background rescan on a cached open

**Files:**
- Create: `src/dstf/discovery/scan_cache.py`, `tests/test_scan_cache.py`
- Modify: `src/dstf/authoring/session.py` (`open`, a `_rescan` thread, `rescan_done`)
- Test: `tests/test_session.py`

- [ ] **Step 1: Tests.** Create `tests/test_scan_cache.py`:

```python
"""The library scan cached across restarts (brief §3.8), under the user's
cache dir — never the work dir. Keyed by the library path, the recognizer
set and the DSTF version. It validates nothing per file: on the owner's
/mnt/d mount a stat sweep costs more than the scan (plan spike 4), so a
cached open trusts the cache and rescans in the background (Session)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dstf.discovery import scan_cache
from dstf.discovery.test_registry import scan_source_tests
from test_discovery import _write_lib


@pytest.fixture(autouse=True)
def _cache_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


def test_cache_dir_is_under_xdg_cache_home(tmp_path):
    assert scan_cache.cache_dir() == tmp_path / "cache" / "dstf" / "scan"


def test_store_then_load_round_trips_every_test_model(tmp_path):
    config = _write_lib(tmp_path)
    scanned = scan_source_tests(config)
    path = scan_cache.store_scan(config, scanned)
    assert path.parent == scan_cache.cache_dir()
    assert scan_cache.load_scan(config) == scanned


def test_a_missing_cache_loads_none(tmp_path):
    config = _write_lib(tmp_path)
    assert scan_cache.load_scan(config) is None


def test_the_key_changes_with_the_library_path_and_the_recognizers(tmp_path):
    config = _write_lib(tmp_path)
    scanned = scan_source_tests(config)
    scan_cache.store_scan(config, scanned)
    (tmp_path / "elsewhere").mkdir()
    other = _write_lib(tmp_path / "elsewhere")
    assert scan_cache.load_scan(other) is None
    config.disabled_bundled.append("modelica:bundled-unit-tests")
    assert scan_cache.load_scan(config) is None


def test_a_cache_from_another_dstf_version_is_ignored(tmp_path, monkeypatch):
    config = _write_lib(tmp_path)
    path = scan_cache.store_scan(config, scan_source_tests(config))
    data = json.loads(path.read_text(encoding="utf-8"))
    data["dstf_version"] = "0.0.0+other"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert scan_cache.load_scan(config) is None


def test_an_unreadable_cache_loads_none(tmp_path):
    config = _write_lib(tmp_path)
    path = scan_cache.store_scan(config, scan_source_tests(config))
    path.write_text("{not json", encoding="utf-8")
    assert scan_cache.load_scan(config) is None


def test_a_non_modelica_source_is_never_cached(tmp_path):
    config = _write_lib(tmp_path)
    config.source_type = "python"
    assert scan_cache.store_scan(config, {}) is None
    assert scan_cache.load_scan(config) is None


def test_a_fresh_process_finds_the_cache(tmp_path):
    """The bundled recognizer registers only when `mo_parser` is imported,
    which `scan_source_tests` does lazily; a process that loads the cache
    BEFORE scanning anything (every warm `dstf author`) must compute the
    same key, or the cache never hits outside the test that wrote it."""
    import os
    import subprocess
    import sys

    config = _write_lib(tmp_path)
    scan_cache.store_scan(config, scan_source_tests(config))
    probe = (
        "from pathlib import Path\n"
        "from dstf.config import Config\n"
        "from dstf.discovery.scan_cache import load_scan\n"
        f"cfg = Config(config_file={str(tmp_path / 'testing.json')!r})\n"
        "print('HIT' if load_scan(cfg) else 'MISS')\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe],
        env={**os.environ, "XDG_CACHE_HOME": os.environ["XDG_CACHE_HOME"]},
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == "HIT", out.stderr
```

In `tests/test_session.py`, class `TestOpen` (the Python-backend `_project` is `source_type: python` and never caches; these open a Modelica library through the helpers written below the tests):

```python
    def test_a_cached_open_parses_nothing_and_rescans_in_the_background(
        self, tmp_path, monkeypatch
    ):
        from dstf.discovery import scan_cache
        from dstf.discovery import test_registry

        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        cfg = _modelica_project(tmp_path)  # helper: _write_lib + a Python backend entry
        Session().open(cfg, work_dir=tmp_path / "work")  # cold: writes the cache
        assert list(scan_cache.cache_dir().glob("*.json"))

        calls = []
        real = test_registry.scan_source_tests

        def spy(config, progress=None):
            calls.append(threading.current_thread().name)
            return real(config, progress=progress)

        monkeypatch.setattr("dstf.authoring.session.scan_source_tests", spy)
        s = Session()
        events = []
        s.on("loading", events.append)
        s.open(cfg, work_dir=tmp_path / "work")
        assert calls == []  # nothing parsed on the opening thread
        assert any(e["phase"] == "scan" and e.get("cached") for e in events)
        assert s.rescan_done.wait(10.0)
        assert calls == ["dstf-rescan"]
        phases = [e["phase"] for e in events]
        assert phases.index("done") < phases.index("rescan")  # `done` is emitted before the thread starts
        assert events[-1]["phase"] == "rescan" and events[-1]["changed"] is False

    def test_a_source_edit_reaches_the_project_through_the_rescan(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        cfg = _modelica_project(tmp_path)
        Session().open(cfg, work_dir=tmp_path / "work")
        mo, before = _bump_stop_time(tmp_path)
        s = Session()
        rescored = []
        s.on("results", lambda e: rescored.append(set(e["models"])))
        s.open(cfg, work_dir=tmp_path / "work")
        assert s.project.test(before.model_id).stop_time == before.stop_time  # from the cache
        assert s.rescan_done.wait(10.0)
        assert s.project.test(before.model_id).stop_time == before.stop_time + 1
        assert rescored[-1] == {before.model_id}  # only the changed model was rescored

    def test_a_late_thread_from_a_previous_open_does_not_mark_this_one_ready(self, tmp_path, monkeypatch):
        """Each open owns its Events; a rescan or index thread still running
        from the previous open sets ITS events, never the new open's."""
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        cfg = _modelica_project(tmp_path)
        Session().open(cfg, work_dir=tmp_path / "work")
        s = Session()
        s.open(cfg, work_dir=tmp_path / "work")
        first_rescan, first_index = s.rescan_done, s.overlay_index_ready
        s.open(cfg, work_dir=tmp_path / "work")  # allowed: no job, no pending edits
        assert s.rescan_done is not first_rescan
        assert s.overlay_index_ready is not first_index
        assert s.rescan_done.wait(10.0) and s.overlay_index_ready.wait(5.0)

    def test_open_writes_nothing_in_the_work_dir_even_when_it_caches(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        cfg = _modelica_project(tmp_path)
        work = tmp_path / "work"
        work.mkdir()
        Session().open(cfg, work_dir=work)
        assert _snapshot(work) == []
```

The two helpers, module-level in `tests/test_session.py` (`_write_lib` writes `package.mo`, one `Foo.mo` with `annotation(experiment(StopTime=100, ...))` and a `testing.json` naming Dymola with no executables, which `select_backend` refuses; the Python backend builds without one):

```python
def _modelica_project(tmp_path: Path) -> Path:
    """test_discovery._write_lib's Modelica library, opened on the Python
    backend so `select_backend` succeeds without a simulator. The scan is
    what these tests are about; nothing is simulated."""
    from test_discovery import _write_lib

    lib = tmp_path / "MyLib"
    lib.mkdir()
    _write_lib(lib)
    cfg_path = lib / "testing.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg.update({"backend": "Python", "backends": {"Python": ["python3", "python"]}})
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    return cfg_path


def _bump_stop_time(tmp_path: Path) -> tuple[Path, TestModel]:
    """Raise Foo.mo's StopTime by one; returns the file and the model as
    scanned BEFORE the edit (model_id MyLib.Foo, stop_time 100.0)."""
    from dstf.discovery.test_registry import scan_source_tests

    lib = tmp_path / "MyLib"
    before = scan_source_tests(Config(config_file=lib / "testing.json"))["MyLib.Foo"]
    mo = lib / "Foo.mo"
    text = mo.read_text(encoding="utf-8")
    assert f"StopTime={before.stop_time:g}" in text
    mo.write_text(
        text.replace(f"StopTime={before.stop_time:g}", f"StopTime={before.stop_time + 1:g}"),
        encoding="utf-8",
    )
    return mo, before
```

(`TestModel` joins the existing `from dstf...` imports.) In the second test, `_bump_stop_time` runs BEFORE the second `Session()` is opened — the cache still says 100, the rescan finds 101.

- [ ] **Step 2: Run to see them fail.** ImportError on `scan_cache`; `rescan_done` missing.

- [ ] **Step 3: The cache module.** Create `src/dstf/discovery/scan_cache.py`:

```python
"""The source scan, cached across tool restarts (brief §3.8).

Under ``$XDG_CACHE_HOME/dstf/scan/`` (default ``~/.cache/dstf/scan/``),
one JSON file per (library path, recognizer set, DSTF version) — never in
the work dir, which is a run's artifact directory that other tools read,
and never validated per file: on a 9P mount (``/mnt/d``) a ``stat`` sweep
over TRANSFORM's 3 269 ``.mo`` files measured 27 s, longer than parsing
them. The Session opens from the cache and rescans in the background.
Only ``source_type == "modelica"`` scans anything, so only those cache.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import os
from pathlib import Path

from .. import __version__
from ..config import Config
from . import mo_parser  # noqa: F401  — registers the bundled recognizer; the key names it
from .recognizer import get_recognizers
from .test_registry import TestModel

logger = logging.getLogger(__name__)

CACHE_VERSION = 1


def cache_dir() -> Path:
    root = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(root) / "dstf" / "scan"


def _key(config: Config) -> str:
    recognizers = sorted(
        r.name
        for r in get_recognizers("modelica")
        if r.name not in config.disabled_bundled
    ) + sorted(r.name for r in config.recognizers if "modelica" in r.applies_to)
    payload = json.dumps(
        [str(config.library_dir.resolve()), recognizers, CACHE_VERSION], sort_keys=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _path(config: Config) -> Path:
    return cache_dir() / f"{_key(config)}.json"


def _to_json(test: TestModel) -> dict:
    if test.metric_tree_spec is not None:
        raise ValueError(f"{test.model_id}: a scanned test carries no metric tree")
    data = dataclasses.asdict(test)
    data["source_file"] = str(test.source_file)
    return data


def _from_json(data: dict) -> TestModel:
    data = dict(data)
    data["source_file"] = Path(data["source_file"])
    return TestModel(**data)


def load_scan(config: Config) -> dict[str, TestModel] | None:
    """The cached scan for ``config``, or None (no cache, another DSTF
    version, unreadable, or not a Modelica source)."""
    if config.source_type != "modelica":
        return None
    path = _path(config)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("dstf_version") != __version__:
            return None
        return {m: _from_json(d) for m, d in data["tests"].items()}
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError, KeyError) as e:
        logger.warning("ignoring the scan cache %s: %s", path, e)
        return None


def store_scan(config: Config, scanned: dict[str, TestModel]) -> Path | None:
    """Write the scan; returns the path, or None for a non-Modelica source."""
    if config.source_type != "modelica":
        return None
    path = _path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "dstf_version": __version__,
        "library_dir": str(config.library_dir.resolve()),
        "tests": {m: _to_json(t) for m, t in scanned.items()},
    }
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    return path
```

(`Config.recognizers` entries: check the attribute that names a recognizer — `r.name` per `test_registry.py:190-192`; `r.applies_to` is what `scan_source_tests` reads.)

- [ ] **Step 4: The Session.** `self.rescan_done = threading.Event()` in `__init__` (never set there). In `open`, the scan becomes:

```python
        cached = load_scan(config)
        if cached is not None:
            scanned = cached
            self._emit("loading", phase="scan", cached=True, total=len(cached))
        else:
            scanned = scan_source_tests(
                config,
                progress=lambda done, total: self._emit(
                    "loading", phase="scan", done=done, total=total
                ),
            )
            store_scan(config, scanned)
```

After the overlay thread starts and AFTER the `done` event (so `rescan` can never precede `done`, whichever thread `_post` runs `apply` on) — a fresh Event per open, captured by the thread:

```python
        done_event = threading.Event()
        self.rescan_done = done_event
        if cached is not None:
            threading.Thread(
                target=self._rescan,
                args=(self.project, cached, done_event),
                name="dstf-rescan",
                daemon=True,
            ).start()
        else:
            done_event.set()
```

and

```python
    def _rescan(
        self, project: Project, cached: dict[str, TestModel], done: threading.Event
    ) -> None:
        """A cached open trusts the cache; this finds out whether it should
        have. Runs the full scan off the UI thread; everything that touches
        the Session — the re-merge AND the events — goes through ``_post``
        (the UI loop, where every other mutation happens)."""
        try:
            fresh = scan_source_tests(project.config)
        except Exception:
            logger.exception("background rescan failed")

            def failed() -> None:
                try:
                    self._emit("loading", phase="rescan", changed=False, error=True)
                finally:
                    done.set()

            self._post(failed)
            return
        changed_models = {
            m for m in set(fresh) | set(cached) if fresh.get(m) != cached.get(m)
        }
        if changed_models:
            store_scan(project.config, fresh)

        def apply() -> None:
            try:
                # The project may have been swapped or closed meanwhile.
                if changed_models and self.project is project:
                    project.scanned = fresh
                    # Only what changed is rescored: a one-file edit over
                    # TRANSFORM must not run 331 comparisons on the UI loop.
                    self._rebuild_tests(changed_models)
                self._emit("loading", phase="rescan", changed=bool(changed_models))
            finally:
                done.set()

        self._post(apply)
```

Imports: `from ..discovery.scan_cache import load_scan, store_scan`. `_rebuild_tests(models)` re-merges every test (cheap) and rescores `models` plus any model the merge newly names, exactly as a `reload` does; a pending op on a changed model is re-applied by the merge (`self.spec.effective()`), as after a Save.

- [ ] **Step 5: Run.** `uv run --no-sync pytest tests/test_scan_cache.py tests/test_session.py tests/test_shell.py -q` → pass. Also run the whole non-browser suite: a test elsewhere that opens a Modelica project through the Session would now write under the REAL `~/.cache` unless it sets `XDG_CACHE_HOME` — add an autouse fixture to `tests/conftest.py` that points `XDG_CACHE_HOME` at a `tmp_path_factory` dir for every test, and say so in its docstring.

- [ ] **Step 6: Gate and commit.**

```bash
git add src/dstf/discovery/scan_cache.py tests/test_scan_cache.py
git commit -F msg -- src/dstf/discovery/scan_cache.py src/dstf/authoring/session.py tests/test_scan_cache.py tests/test_session.py tests/conftest.py
```
Message: `author: the library scan is cached under the user's cache dir; a cached open rescans in the background and re-merges what changed`.

---

### Task 10: Documents and D127

**Files:**
- Modify: `docs/usage.md` (§"The authoring tool"), `docs/architecture.md` (the `reporting/` and `figures/` listings, the report pipeline paragraph at ~206-212), `CLAUDE.md` (Project Structure: `reporting/theme.py`, `templates/axes.js`; Project Overview's report sentence gains "two labelled error axes"), `docs/decisions.md` (append D127)

- [ ] **Step 1: usage.md.** Under the authoring tool section add a short paragraph: the scan cache (where, that it is trusted and rescanned in the background, `XDG_CACHE_HOME`), and that the first click no longer waits for the overlay index. Under the report section's error-overlay sentence: signed and absolute error on "Error [signal units]", NRMSE on "NRMSE [–]", each axis shown while a series on it is visible, by the select or the legend.

- [ ] **Step 2: architecture.md and CLAUDE.md.** Add `theme.py` and `axes.js` where the trees list `reporting/`; in the pipeline paragraph, "report.js paints the figures and toggles trace visibility" → "axes.js keeps a secondary axis visible exactly while a trace on it is; report.js paints the figures and toggles trace visibility".

- [ ] **Step 3: D127.** Append to `docs/decisions.md`:

```markdown
## D127 — The visual system is one token table; the tool's scan cache lives in the user's cache dir (2026-09-15)

The redesign brief (`docs/superpowers/specs/2026-09-15-authoring-tool-redesign-brief.md`)
and `DESIGN.md` name the colours and type both tiers share. They live once, in
`reporting/theme.py`, as a mapping rendered to `:root { --dstf-...: ... }` for the
dashboard, the per-test report and the authoring tool's page, and mirrored into
the tool's Quasar palette. Status fills, baseline-role chips and plot series
never change with a theme; a theme (sub-project 7) is another mapping in the
same module, named in `testing.json` (owner ruling, brief §3.10). The shipped
type stack ended in `monospace` with no generic sans-serif, so a Linux browser
without Segoe UI or Roboto rendered every report in monospace; the stack now
ends in `sans-serif`. Two colours the report used that are in no table — the
metric tree's combinator purple and the decimation banner's amber — map onto
the neutral and `warn` tokens.

The tool caches the library scan across restarts under
`$XDG_CACHE_HOME/dstf/scan/` (default `~/.cache/dstf/scan/`), keyed by the
library path, the recognizer set and the DSTF version — never in the work dir,
which is a run's artifact directory, and `Session.open` still writes nothing
there. The cache validates nothing per file: on the owner's `/mnt/d` mount a
`stat` sweep over TRANSFORM's 3 269 `.mo` files measured 27 s, longer than
parsing them (20 s). A cached open trusts the cache, rescans on a background
thread, and re-merges what changed.

Also here, no decision needed but recorded: the report's error overlays ride on
one right axis per unit family (brief §3.4), shown while a series on it is
visible by one shared script both tiers load; the Session reads a model's
reference and overlays once per reload and decimates its base traces once; the
sibling-backend overlay index is built at open on its own thread; the Session
reports its phases as `loading` events and scores each result as it is read.

- `src/dstf/reporting/theme.py`, `templates/axes.js`, `discovery/scan_cache.py`,
  `authoring/session.py`.
- Brief §3.1, §3.4, §3.8, §3.10; roadmap sub-project 1.
```

- [ ] **Step 4: Commit.** `git commit -F msg -- docs/usage.md docs/architecture.md CLAUDE.md docs/decisions.md`. Message: `docs: the foundations — tokens, axes, the scan cache (D127)`.

---

### Task 11: Sub-project gate

**Files:** none changed; a scratchpad driver script.

- [ ] **Step 1: The suite.** From the repo root: `uv run --no-sync ruff check && uv run --no-sync ruff format --check && uv run --no-sync mypy && uv run --no-sync pytest -q -m "not playwright" && uv run --no-sync pytest -q -m playwright`. Record the counts; the browser run reports no skips for the authoring and report tests.

- [ ] **Step 2: Predicates over the end state** (each a command or a test named above):
  1. `variable_figure` of any view with both curves lays out `yaxis3` "Error [signal units]" and `yaxis4` "NRMSE [–]", hidden (Task 1's test); under the report's plotly.js and the tool's, an axis is visible exactly while a series on it is visible, by select or legend (Tasks 2, 3 browser tests).
  2. Both rendered pages carry no colour outside the `:root` block and their body stack ends in `sans-serif` (`tests/test_theme.py`); the tool's page carries the same `:root` block (`grep -c "dstf-page" ` on the served page in Step 4).
  3. A second `Session.view` of a model reads nothing from disk; a second `figure` decimates only leaf geometry; `overlay_index_ready` is set without a click (Tasks 6, 7 tests).
  4. `Session.open` emits `loading` per phase and per scored test, then one `results` (Task 8 test); a cached open parses nothing on the opening thread and the rescan re-merges an edit (Task 9 tests).
  5. `grep -rln "nicegui" src/dstf/authoring/` lists only `app.py`, `forms.py` and `builtin/*`.
  6. `grep -rn "XDG_CACHE_HOME\|scan_cache" tests/conftest.py` shows the autouse redirect; `ls ~/.cache/dstf/scan/` after the full suite shows nothing the suite wrote (compare listings before and after).

- [ ] **Step 3: The report over TRANSFORM (the orchestrator, read-only over a COPY).** Copy `testing_output/TRANSFORM/Dymola/linux` to a scratch dir (`cp -r`, ~ the 331 results), then `uv run --no-sync dstf --config /mnt/d/Modelica/TRANSFORM-UnitTests/ReferenceResults/testing.json compare --work-dir <copy> --report --filter "MSLFluid.InverseParameterization"` (one page is enough; `compare` wipes report dirs in the work dir it is given, hence the copy). Open the page in Chromium with Playwright: select `nrmse` on the first variable → `yaxis4.visible === true`, its title text `NRMSE [–]`; select `signed` → `yaxis3` visible, `yaxis4` hidden; `getComputedStyle(document.body).fontFamily` ends with `sans-serif`. Screenshot the first variable section (viewport-sized), thumbnail it with `render_check.py thumb`, READ it: two axis titles at the right, the combinator rows grey not purple, nothing overlapping.

- [ ] **Step 4: The tool over TRANSFORM (the orchestrator).** `XDG_CACHE_HOME=<scratch> uv run --no-sync dstf --config <TRANSFORM testing.json> author --work-dir testing_output/TRANSFORM/Dymola/linux --port 8765 --no-browser` with `PYTEST_CURRENT_TEST` stripped from the env. Record: time to the first HTTP 200 on a COLD cache (the scan runs; expect ~52 s as before) and, after Stop server and a second launch, on a WARM cache (expect the scan's ~30 s gone: results read ~16 s + scoring ~5 s). With Playwright: wait for the page, click a PASS test's row, time click → `.js-plotly-plot` painted (`_fullLayout` present): under 1 s on the first click (the 3 s index is warmed), and a second click on the same test faster than the first. Click the legend entry "NRMSE (per-point)" → `yaxis4.visible`. Never click `Accept N` / `Rerun N`. Before/after: the spec's md5, the reference-dir listing and the work-dir listing unchanged; `<scratch>/dstf/scan/` holds one JSON. Screenshot the centre, thumbnail, READ.

- [ ] **Step 5: Record.** The gate's numbers (cold/warm open, first/second click, suite counts) go in the handoff and the sub-project's closing note in the roadmap's table row (a one-line "gated <date>: …" under sub-project 1). No commit unless a document changed.

---

## Self-review (run before handing the plan to review)

- **Brief coverage.** §3.1 "only layout-independent work precedes the design": Tasks 1–9 touch the figure layer, the templates' stylesheet, the Session, the backends' read path, discovery — no layout. §3.4: Tasks 1–3. §3.8 "page within a second", "list fills in as discovered", "badges as scored", "scan cached": the Session side is Tasks 8–9 (`loading` events, per-result scoring, the cache); the page itself is sub-project 2, which consumes them — stated in Conventions #6–7. §3.10 theme ruling: the token table is Task 4; the `dark` mapping and the `theme` setting are sub-project 7. Brief §2.2's three costs: the index (Task 6), the per-click reads (Task 7), decimation (Tasks 5 and 7); the list-rebuild cost is the shell's and is sub-project 2's.
- **Placeholders.** None: every step carries its code or its command. Two test helpers are named but written by the implementer from an existing fixture (`_modelica_project`, `_bump_stop_time` in Task 9, `served_page` / the row click in Task 3, `work_with_manifest` in Task 8) — each names the file whose fixture it copies.
- **Type consistency.** `axes_js_source` (Tasks 2, 3); `jinja_env` / `css_variables` / `quasar_palette` (Task 4); `index_args` / `sibling_backend_index` / `warm_sibling_backend_index` (Task 6); `_ModelReads` / `_reads` / `_reads_for` (Task 7); `run_verdicts` / `on_result` / `progress` / `loading` (Task 8); `load_scan` / `store_scan` / `rescan_done` / `_rescan(project, cached, done)` (Task 9). `Session.open`'s `started` (Task 8) is set before the scan, which Task 9 wraps; the `done` event is emitted before the rescan thread starts, so the order is scan → results… → done → rescan.
