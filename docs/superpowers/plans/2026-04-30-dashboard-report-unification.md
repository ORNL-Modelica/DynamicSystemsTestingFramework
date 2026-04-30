# Dashboard + Report Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace DSTF's two HTML surfaces (live `dashboard.html` from f-string template + post-run `reports/index.html` from Jinja) with one progressively-enriching page at `dashboard.html` that gradually fills in NRMSE/warnings/translate/sim columns + per-test report links as data lands. `--report` flag narrows to controlling only per-test `interactive.html` deep dives — the unified dashboard always exists.

**Architecture:** ProgressReporter writes only `status.json`; a new `dashboard_render.py` module reads `status.json` (plus optional `comparison_data.json` and `tests/<test>/comparison_data.json` per-test sidecars) and renders one Jinja `dashboard.html`. JS uses `setInterval(fetch('status.json'))` + DOM diff during the run (preserves scroll); a final-pass render after comparison strips refresh-cadence + adds report links + populates post-comparison columns. All status filter / sort / selection / rerun-cmd-builder JS already in `index.html` ports over.

**Tech Stack:** Python 3.11+, Jinja2, vanilla JS (no build step), pytest, Playwright (smoke only). LOC target: net-flat or slightly negative — deletes the 80-line f-string in `progress.py` plus standalone `index.html`, replaces with shared template + render module.

---

## File Structure

**New files:**

- `src/dstf/reporting/templates/dashboard.html` — the unified Jinja template. Replaces both the `_DASHBOARD_TEMPLATE` f-string in `progress.py` and `templates/index.html`. Renders a `<table>` of tests with all columns; each cell uses `{% if data is not none %}` so live-mode (no comparison data yet) shows `—` and post-run mode shows real values.
- `src/dstf/reporting/templates/dashboard.js` — vanilla JS module: status-button + per-column text filter, 3-state sort, row selection + rerun-cmd builder (ported from `index.html`), `setInterval(fetch('status.json'))` + DOM diff, "Refresh now" button, scroll preservation.
- `src/dstf/reporting/dashboard_render.py` — single source of truth for rendering `dashboard.html`. Reads `status.json` for live state, optionally augments rows from `comparison_data.json` per-test sidecars when present. Atomic-write semantics ported from `progress.py`.
- `tests/test_dashboard_render.py` — pytest unit tests for the render module: live-only snapshot, post-run snapshot, mixed snapshot.
- `tests/test_dashboard_html.py` — Playwright smoke test for the unified page (gated behind `importorskip("playwright")`): live mode → status updates land in DOM via fetch loop; post-run mode → report links present, refresh stripped.

**Modified files:**

- `src/dstf/simulators/progress.py` — strip the inline `_DASHBOARD_TEMPLATE` f-string + `_write_html`. ProgressReporter becomes a `status.json`-only writer; calls `dashboard_render.render_live(work_dir)` after each `_write_json`. `finalize()` calls `dashboard_render.render_final(work_dir)` instead of stripping the meta-refresh inline.
- `src/dstf/reporting/plot_comparison.py` — `generate_report_suite` no longer renders a separate `index.html`; it just writes per-test `interactive.html` deep dives, plus a flag-file or single call into `dashboard_render.render_final` to refresh the unified page with post-comparison data. Remove `_render_template("index.html", ...)` call.
- `src/dstf/reporting/templates/index.html` — **deleted** (its content folds into `dashboard.html`).
- `src/dstf/discovery/test_registry.py` — TestModel grows a `field_sources: dict[str, str]` field tracking the origin of `stop_time` / `tolerance` / `method` / `number_of_intervals` / `output_interval` (values: `"annotation"`, `"test_spec"`, `"default"`). Mutated during the merge in `_build_test_model_from_recognizer_results` and `discover_tests`.
- `src/dstf/cli.py` — narrow `_generate_report_suite` semantics; `cmd_run` and `cmd_compare` always trigger a final dashboard render via `dashboard_render.render_final(work_dir)`. The `--report` flag still gates per-test deep-dive generation but no longer gates the dashboard's existence.

**Deleted files:**

- `src/dstf/reporting/templates/index.html`

**Untouched files (verified):**

- `src/dstf/reporting/templates/interactive.html` + `interactive.js` (per-test deep-dive) — completely separate surface.
- `tests/golden/interactive_*.hash` — apply to interactive.html only.
- All five backend runners — feed ProgressReporter via the same event contract; no changes needed.

---

## Phase 1 — Provenance plumbing in TestModel (foundation, no UI)

Lays the data groundwork for the resolution-explainer column. No user-visible change.

### Task 1: Add `field_sources` to TestModel

**Files:**

- Modify: `src/dstf/discovery/test_registry.py:20-67` (TestModel dataclass)
- Test: `tests/test_discovery.py` (extend an existing test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_discovery.py` (after the existing spec-merge tests around line 200):

```python
def test_field_sources_records_annotation_default():
    """A test discovered only via Modelica annotation has field_sources='annotation'."""
    from dstf.discovery.recognizer import RecognizerResult
    from dstf.discovery.test_registry import (
        _build_test_model_from_recognizer_results,
    )

    r = RecognizerResult(
        model_id="MyLib.Foo",
        stop_time=10.0,
        tolerance=1e-5,
        method="Dassl",
    )
    model = _build_test_model_from_recognizer_results("MyLib.Foo", [r])
    assert model.field_sources["stop_time"] == "annotation"
    assert model.field_sources["tolerance"] == "annotation"
    assert model.field_sources["method"] == "annotation"


def test_field_sources_records_spec_override():
    """When spec overrides annotation, field_sources flips to 'test_spec'."""
    import json
    import tempfile
    from pathlib import Path
    from dstf.config import Config
    from dstf.discovery.test_registry import discover_tests

    spec = {
        "tests": [{
            "model": "MyLib.Foo",
            "variables": ["x"],
            "simulation": {"stop_time": 999.0},
        }],
    }
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        spec_path = td / "test_spec.json"
        spec_path.write_text(json.dumps(spec))
        config = Config(source_path=td, test_spec_file=spec_path)
        tests = discover_tests(config)
    assert len(tests) == 1
    assert tests[0].field_sources["stop_time"] == "test_spec"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_discovery.py::test_field_sources_records_annotation_default tests/test_discovery.py::test_field_sources_records_spec_override -v
```

Expected: FAIL with `AttributeError: 'TestModel' object has no attribute 'field_sources'`.

- [ ] **Step 3: Add `field_sources` field to TestModel**

In `src/dstf/discovery/test_registry.py`, after the `requested_baselines` field (around line 64), add:

```python
    # Per-field provenance: where the value came from.
    # Keys: "stop_time", "tolerance", "method", "number_of_intervals",
    # "output_interval". Values: "annotation", "test_spec", "default".
    # Populated during recognizer + spec merge so the dashboard's
    # resolution-explainer column can show "stop_time: 10 (annotation)".
    field_sources: dict[str, str] = field(default_factory=dict)
```

- [ ] **Step 4: Update `_build_test_model_from_recognizer_results` to record sources**

In the loop at `src/dstf/discovery/test_registry.py:93-121`, replace the existing assignment block with:

```python
    for r in results:
        if r.source_file is not None:
            model.source_file = r.source_file
        if r.n_vars is not None:
            model.n_vars = r.n_vars
        if r.x_expressions:
            model.x_expressions = list(r.x_expressions)
        if r.x_raw:
            model.x_raw = r.x_raw
        if r.x_reference is not None:
            model.x_reference = list(r.x_reference)
        if r.error_expected is not None:
            model.error_expected = r.error_expected
        if r.stop_time is not None:
            model.stop_time = r.stop_time
            model.field_sources["stop_time"] = "annotation"
        if r.tolerance is not None:
            model.tolerance = r.tolerance
            model.field_sources["tolerance"] = "annotation"
        if r.method is not None:
            model.method = r.method
            model.field_sources["method"] = "annotation"
        if r.number_of_intervals is not None:
            model.number_of_intervals = r.number_of_intervals
            model.field_sources["number_of_intervals"] = "annotation"
        if r.output_interval is not None:
            model.output_interval = r.output_interval
            model.field_sources["output_interval"] = "annotation"
        if r.simulate_only is not None:
            model.simulate_only = r.simulate_only
        if r.requested_fmu_export is not None:
            model.requested_fmu_export = r.requested_fmu_export
        if r.requested_baselines is not None:
            model.requested_baselines = list(r.requested_baselines)
```

- [ ] **Step 5: Update `discover_tests` spec-merge to record overrides**

In `src/dstf/discovery/test_registry.py:186-213` (the spec-merge block), replace each conditional override with one that also records the source:

```python
    for model_id, spec_test in spec_tests.items():
        if model_id in merged:
            existing = merged[model_id]
            existing.variable_patterns = spec_test.variable_patterns
            existing.source = "both"
            if spec_test.stop_time != DEFAULT_STOP_TIME:
                existing.stop_time = spec_test.stop_time
                existing.field_sources["stop_time"] = "test_spec"
            if spec_test.tolerance != DEFAULT_TOLERANCE:
                existing.tolerance = spec_test.tolerance
                existing.field_sources["tolerance"] = "test_spec"
            if spec_test.method != DEFAULT_METHOD:
                existing.method = spec_test.method
                existing.field_sources["method"] = "test_spec"
            if spec_test.number_of_intervals is not None:
                existing.number_of_intervals = spec_test.number_of_intervals
                existing.field_sources["number_of_intervals"] = "test_spec"
            if spec_test.output_interval is not None:
                existing.output_interval = spec_test.output_interval
                existing.field_sources["output_interval"] = "test_spec"
            if spec_test.comparison_tolerance is not None:
                existing.comparison_tolerance = spec_test.comparison_tolerance
            if spec_test.variable_overrides:
                existing.variable_overrides.update(spec_test.variable_overrides)
            if spec_test.timeout is not None:
                existing.timeout = spec_test.timeout
            if spec_test.metric_tree_spec is not None:
                existing.metric_tree_spec = spec_test.metric_tree_spec
        else:
            merged[model_id] = spec_test
```

For the spec-only path, also tag `field_sources` so a spec-only test has provenance. Add this block right above `merged[model_id] = spec_test` (final else branch):

```python
        else:
            for fname in ("stop_time", "tolerance", "method",
                          "number_of_intervals", "output_interval"):
                spec_test.field_sources.setdefault(fname, "test_spec")
            merged[model_id] = spec_test
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
uv run pytest tests/test_discovery.py::test_field_sources_records_annotation_default tests/test_discovery.py::test_field_sources_records_spec_override -v
```

Expected: PASS.

- [ ] **Step 7: Run full discovery test suite for regression check**

```bash
uv run pytest tests/test_discovery.py tests/test_recognizer.py tests/test_json_recognizer.py -q
```

Expected: all pass (74 tests baseline).

- [ ] **Step 8: Commit**

```bash
git add src/dstf/discovery/test_registry.py tests/test_discovery.py
git commit -m "$(cat <<'EOF'
feat(discovery): record per-field provenance on TestModel

Adds TestModel.field_sources tracking where stop_time / tolerance /
method / number_of_intervals / output_interval came from
(annotation, test_spec, or default). Fed by the recognizer-merge
loop in _build_test_model_from_recognizer_results and the
spec-override block in discover_tests.

Foundation for the resolution-explainer column landing in the
unified dashboard. No user-visible change yet.

EOF
)"
```

---

## Phase 2 — dashboard_render module + Jinja template skeleton

Builds the new render path side-by-side with the existing one. After this phase both old `dashboard.html` (from progress.py) and new `dashboard.html` exist at parallel paths so we can compare output before switching.

### Task 2: Stub the dashboard_render module

**Files:**

- Create: `src/dstf/reporting/dashboard_render.py`
- Test: `tests/test_dashboard_render.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_dashboard_render.py`:

```python
"""Tests for the unified dashboard renderer."""

import json
from pathlib import Path

import pytest

from dstf.reporting.dashboard_render import (
    render_live,
    render_final,
    build_dashboard_context,
)


def _write_status_json(work_dir: Path, snapshot: dict) -> None:
    (work_dir / "status.json").write_text(json.dumps(snapshot), encoding="utf-8")


def test_build_context_live_only(tmp_path):
    """Live snapshot (no comparison data yet): rows have status/elapsed
    populated; NRMSE/warnings columns are None; auto_refresh=True."""
    snapshot = {
        "total": 2,
        "elapsed": 5.0,
        "eta_seconds": None,
        "counts": {"queued": 0, "running": 1, "passed": 1,
                   "failed": 0, "timed_out": 0},
        "tests": [
            {"test_key": "test_0001", "model_id": "Lib.A",
             "status": "passed", "elapsed": 2.0, "worker_id": 0,
             "report_dir": "test_0001"},
            {"test_key": "test_0002", "model_id": "Lib.B",
             "status": "running", "elapsed": None, "worker_id": 1,
             "report_dir": "test_0002"},
        ],
        "updated_at": 0.0,
    }
    _write_status_json(tmp_path, snapshot)
    ctx = build_dashboard_context(tmp_path, mode="live")
    assert ctx["mode"] == "live"
    assert ctx["auto_refresh"] is True
    assert len(ctx["tests"]) == 2
    row_a = next(r for r in ctx["tests"] if r["model_id"] == "Lib.A")
    assert row_a["status_text"] == "passed"
    assert row_a["worst_nrmse"] is None
    assert row_a["n_vars"] is None


def test_build_context_final_with_comparisons(tmp_path):
    """Final snapshot: status.json + comparison_data.json sidecars yield
    a row with both live + post-run fields populated."""
    snapshot = {
        "total": 1, "elapsed": 5.0, "eta_seconds": None,
        "counts": {"queued": 0, "running": 0, "passed": 1,
                   "failed": 0, "timed_out": 0},
        "tests": [{"test_key": "test_0001", "model_id": "Lib.A",
                   "status": "passed", "elapsed": 2.0,
                   "worker_id": 0, "report_dir": "test_0001"}],
        "updated_at": 0.0,
    }
    _write_status_json(tmp_path, snapshot)
    test_dir = tmp_path / "reports" / "test_0001"
    test_dir.mkdir(parents=True)
    (test_dir / "comparison_data.json").write_text(json.dumps({
        "model_id": "Lib.A",
        "worst_nrmse": 1.2e-5,
        "n_vars": 3,
        "n_vars_passed": 3,
        "n_warnings": 0,
        "translation_wall": 0.5,
        "sim_wall": 1.5,
        "total_wall": 2.0,
    }))
    ctx = build_dashboard_context(tmp_path, mode="final")
    assert ctx["mode"] == "final"
    assert ctx["auto_refresh"] is False
    row = ctx["tests"][0]
    assert row["worst_nrmse"] == 1.2e-5
    assert row["n_vars"] == 3
    assert row["translation_wall"] == 0.5


def test_render_live_writes_dashboard_html(tmp_path):
    """render_live writes dashboard.html and it contains the auto-refresh
    JS-fetch hook, not <meta http-equiv='refresh'>."""
    snapshot = {
        "total": 1, "elapsed": 0.0, "eta_seconds": None,
        "counts": {"queued": 1, "running": 0, "passed": 0,
                   "failed": 0, "timed_out": 0},
        "tests": [{"test_key": "test_0001", "model_id": "Lib.A",
                   "status": "queued", "elapsed": None,
                   "worker_id": None, "report_dir": "test_0001"}],
        "updated_at": 0.0,
    }
    _write_status_json(tmp_path, snapshot)
    render_live(tmp_path)
    out = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert "<title>" in out
    assert "Lib.A" in out
    assert 'http-equiv="refresh"' not in out
    assert "DASHBOARD_MODE" in out  # JS fetch hook bootstrap


def test_render_final_strips_refresh(tmp_path):
    snapshot = {
        "total": 1, "elapsed": 0.0, "eta_seconds": None,
        "counts": {"queued": 0, "running": 0, "passed": 1,
                   "failed": 0, "timed_out": 0},
        "tests": [{"test_key": "test_0001", "model_id": "Lib.A",
                   "status": "passed", "elapsed": 1.0,
                   "worker_id": 0, "report_dir": "test_0001"}],
        "updated_at": 0.0,
    }
    _write_status_json(tmp_path, snapshot)
    render_final(tmp_path)
    out = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert "DASHBOARD_MODE = 'final'" in out
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_dashboard_render.py -v
```

Expected: ImportError from missing `dashboard_render` module.

- [ ] **Step 3: Create the dashboard_render module**

Write `src/dstf/reporting/dashboard_render.py`:

```python
"""Unified dashboard renderer.

One Jinja template (`dashboard.html`) feeds both live progress
during a run and the post-comparison report. The template renders
gracefully whether comparison data is present (post-run) or absent
(during run); JS-fetch on the client side keeps the page fresh
without full-page reloads.

Live mode is triggered every state change by ProgressReporter.
Final mode is triggered after comparison from cli.cmd_run /
cmd_compare; it strips the JS-fetch poll and adds per-test report
links + post-run columns (worst_nrmse, warnings, translate/sim/total
wall times).
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape


_TEMPLATE_DIR = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape(["html"]),
)


def _read_status(work_dir: Path) -> Optional[dict]:
    p = work_dir / "status.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _read_comparison_sidecar(work_dir: Path, report_dir: str) -> Optional[dict]:
    """Read per-test comparison_data.json if present.

    Per-test reports are at <work_dir>/reports/<report_dir>/comparison_data.json
    (matches generate_comparison_plots layout).
    """
    p = work_dir / "reports" / report_dir / "comparison_data.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _enrich_row_from_comparison(row: dict, comp: dict) -> None:
    """Copy post-run fields from a per-test comparison_data.json into a row."""
    for key in (
        "worst_nrmse", "n_vars", "n_vars_passed", "n_warnings",
        "translation_wall", "sim_wall", "total_wall",
        "ref_id", "field_sources",
    ):
        if key in comp:
            row[key] = comp[key]


def build_dashboard_context(work_dir: Path, mode: str) -> dict:
    """Build the Jinja context for dashboard.html.

    mode='live' — auto_refresh=True, post-run fields stay None
    mode='final' — auto_refresh=False, fields enriched from sidecars

    The same template renders both; JS reads `DASHBOARD_MODE` to
    decide whether to start the fetch loop.
    """
    snapshot = _read_status(work_dir) or {
        "total": 0, "elapsed": 0.0, "eta_seconds": None,
        "counts": {}, "tests": [], "updated_at": 0.0,
    }

    rows = []
    for t in snapshot.get("tests", []):
        row = {
            "test_key": t.get("test_key"),
            "model_id": t.get("model_id"),
            "status_text": t.get("status", "queued"),
            "status_class": t.get("status", "queued").replace("_", "-"),
            "elapsed": t.get("elapsed"),
            "worker_id": t.get("worker_id"),
            "report_dir": t.get("report_dir") or t.get("test_key"),
            "phase": t.get("phase"),
            # Post-run fields default to None; populated below in final mode
            "worst_nrmse": None,
            "n_vars": None,
            "n_vars_passed": None,
            "n_warnings": None,
            "translation_wall": None,
            "sim_wall": None,
            "total_wall": None,
            "ref_id": None,
            "field_sources": {},
        }
        if mode == "final" and row["report_dir"]:
            comp = _read_comparison_sidecar(work_dir, row["report_dir"])
            if comp:
                _enrich_row_from_comparison(row, comp)
        rows.append(row)

    return {
        "mode": mode,
        "auto_refresh": mode == "live",
        "title": "Test progress" if mode == "live" else "Test report",
        "total": snapshot.get("total", 0),
        "elapsed": snapshot.get("elapsed", 0.0),
        "eta_seconds": snapshot.get("eta_seconds"),
        "counts": snapshot.get("counts", {}),
        "tests": rows,
        "updated_at": snapshot.get("updated_at", time.time()),
    }


def _atomic_write(path: Path, text: str) -> None:
    """Atomic file write — Windows file-locking workaround.

    Same retry logic as ProgressReporter._atomic_write — uses unique
    tmp name so concurrent writers can't share the same tmp path.
    """
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(text, encoding="utf-8")
    last_err: Optional[OSError] = None
    for delay in (0, 0.05, 0.1, 0.2, 0.5):
        if delay:
            time.sleep(delay)
        try:
            tmp.replace(path)
            return
        except OSError as e:
            last_err = e
    try:
        tmp.unlink()
    except OSError:
        pass
    if not (path.parent / path.name).exists():
        raise last_err


def _render(work_dir: Path, mode: str) -> None:
    ctx = build_dashboard_context(work_dir, mode=mode)
    template = _env.get_template("dashboard.html")
    html = template.render(**ctx)
    _atomic_write(work_dir / "dashboard.html", html)


def render_live(work_dir: Path) -> None:
    """Render dashboard.html in live mode (JS-fetch loop active)."""
    _render(work_dir, mode="live")


def render_final(work_dir: Path) -> None:
    """Render dashboard.html in final mode (fetch loop stripped, sidecars merged)."""
    _render(work_dir, mode="final")
```

- [ ] **Step 4: Stub the dashboard.html template (minimal)**

Create `src/dstf/reporting/templates/dashboard.html` (minimal, expanded in Phase 3):

```html
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{{ title }}</title>
</head>
<body>
<h1>{{ title }}</h1>
<table id="results-table">
<thead><tr>
  <th>Test</th><th>Model</th><th>Status</th><th>Worker</th><th>Elapsed</th>
  <th>NRMSE</th><th>Vars</th><th>Warnings</th>
  <th>Translate (s)</th><th>Sim (s)</th><th>Total (s)</th>
</tr></thead>
<tbody>
{% for t in tests %}
<tr data-status="{{ t.status_class }}">
  <td>{{ t.test_key or "—" }}</td>
  <td>{{ t.model_id }}</td>
  <td>{{ t.status_text }}</td>
  <td>{% if t.worker_id is not none %}W{{ t.worker_id }}{% else %}—{% endif %}</td>
  <td>{% if t.elapsed is not none %}{{ "%.1f"|format(t.elapsed) }}s{% else %}—{% endif %}</td>
  <td>{% if t.worst_nrmse is not none %}{{ "%.4e"|format(t.worst_nrmse) }}{% else %}—{% endif %}</td>
  <td>{% if t.n_vars is not none %}{{ t.n_vars_passed }}/{{ t.n_vars }}{% else %}—{% endif %}</td>
  <td>{% if t.n_warnings is not none %}{{ t.n_warnings }}{% else %}—{% endif %}</td>
  <td>{% if t.translation_wall is not none %}{{ "%.2f"|format(t.translation_wall) }}{% else %}—{% endif %}</td>
  <td>{% if t.sim_wall is not none %}{{ "%.2f"|format(t.sim_wall) }}{% else %}—{% endif %}</td>
  <td>{% if t.total_wall is not none %}{{ "%.2f"|format(t.total_wall) }}{% else %}—{% endif %}</td>
</tr>
{% endfor %}
</tbody>
</table>
<script>
const DASHBOARD_MODE = '{{ mode }}';
// fetch-loop bootstrap added in Phase 3
</script>
</body>
</html>
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_dashboard_render.py -v
```

Expected: 4 PASS.

- [ ] **Step 6: Commit**

```bash
git add src/dstf/reporting/dashboard_render.py src/dstf/reporting/templates/dashboard.html tests/test_dashboard_render.py
git commit -m "$(cat <<'EOF'
feat(reporting): dashboard_render module + minimal Jinja template

New unified renderer reads status.json (live) and per-test
comparison_data.json sidecars (final) and produces one
dashboard.html. Atomic-write semantics ported from progress.py.

Template is intentionally minimal at this phase — full filter/sort
JS, auto-refresh fetch loop, status-button row land in Phase 3.

Module sits beside the existing progress.py f-string template; both
write dashboard.html today, but progress.py is still the live-mode
default. Cutover in Phase 6.

EOF
)"
```

---

## Phase 3 — Port the rich UI (filter / sort / select / refresh)

Brings `dashboard.html` to feature parity with today's `index.html` and adds the missing pieces (per-column text filter, 3-state sort, JS-fetch loop, refresh button).

### Task 3: Port CSS + sortable headers + filter buttons

**Files:**

- Modify: `src/dstf/reporting/templates/dashboard.html`
- Test: `tests/test_dashboard_render.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dashboard_render.py`:

```python
def test_dashboard_template_has_filter_buttons(tmp_path):
    """Status filter buttons must appear in the rendered HTML."""
    snapshot = {
        "total": 0, "elapsed": 0.0, "eta_seconds": None,
        "counts": {}, "tests": [], "updated_at": 0.0,
    }
    _write_status_json(tmp_path, snapshot)
    render_live(tmp_path)
    out = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert 'filterRows(\'all\'' in out
    assert 'filterRows(\'fail\'' in out
    assert 'filterRows(\'sim-fail\'' in out
    assert 'filterRows(\'no-ref\'' in out
    assert 'filterRows(\'pass\'' in out


def test_dashboard_template_has_sort_hooks(tmp_path):
    """Each sortable column header must have data-sort and data-key attrs."""
    snapshot = {
        "total": 0, "elapsed": 0.0, "eta_seconds": None,
        "counts": {}, "tests": [], "updated_at": 0.0,
    }
    _write_status_json(tmp_path, snapshot)
    render_live(tmp_path)
    out = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert 'data-sort="text" data-key="model"' in out
    assert 'data-sort="num" data-key="nrmse"' in out
    assert 'data-sort="num" data-key="elapsed"' in out


def test_dashboard_template_has_per_column_filter(tmp_path):
    """Per-column text filter inputs must be present below headers."""
    snapshot = {
        "total": 0, "elapsed": 0.0, "eta_seconds": None,
        "counts": {}, "tests": [], "updated_at": 0.0,
    }
    _write_status_json(tmp_path, snapshot)
    render_live(tmp_path)
    out = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert 'class="col-filter"' in out
    assert 'data-col-filter="model"' in out
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_dashboard_render.py::test_dashboard_template_has_filter_buttons tests/test_dashboard_render.py::test_dashboard_template_has_sort_hooks tests/test_dashboard_render.py::test_dashboard_template_has_per_column_filter -v
```

Expected: 3 FAIL.

- [ ] **Step 3: Replace dashboard.html with the rich version**

Overwrite `src/dstf/reporting/templates/dashboard.html` entirely with:

```html
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{{ title }} — {{ total }} tests</title>
<style>
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace; margin: 1.5em; background: #fafafa; color: #333; }
h1 { margin-bottom: 0.2em; font-size: 1.4em; }
.status-bar { font-size: 0.85em; color: #666; margin-bottom: 1em; }
.status-bar .updated { color: #888; }
.status-bar button.refresh-now { padding: 0.2em 0.6em; font-size: 0.8em; cursor: pointer; }
.counters { display: flex; gap: 0.6em; flex-wrap: wrap; margin-bottom: 1em; }
.counter { background: white; border: 1px solid #ddd; border-radius: 4px; padding: 0.4em 0.8em; min-width: 90px; }
.counter .label { font-size: 0.7em; color: #888; text-transform: uppercase; }
.counter .value { font-size: 1.3em; font-weight: 600; }
.bar { height: 14px; background: #eee; border-radius: 7px; overflow: hidden; margin-bottom: 1em; display: flex; }
.bar span { display: block; height: 100%; }
.bar .b-passed { background: #4CAF50; }
.bar .b-failed { background: #f44336; }
.bar .b-timed_out { background: #9C27B0; }
.bar .b-running { background: #2196F3; }
.filter-bar { margin-bottom: 0.5em; }
.filter-bar button { margin-right: 0.4em; padding: 0.3em 0.7em; border: 1px solid #ccc; border-radius: 3px; background: white; cursor: pointer; font-size: 0.82em; }
.filter-bar button.active { background: #1976D2; color: white; border-color: #1976D2; }
table { border-collapse: collapse; width: 100%; background: white; font-size: 0.85em; }
th, td { border: 1px solid #ddd; padding: 4px 8px; text-align: left; }
th { background: #f5f5f5; position: sticky; top: 0; cursor: pointer; user-select: none; }
th:hover { background: #e8e8e8; }
th.sorted-asc::after { content: " ▲"; color: #1976D2; font-size: 0.85em; }
th.sorted-desc::after { content: " ▼"; color: #1976D2; font-size: 0.85em; }
input.col-filter { width: 100%; padding: 2px 4px; font-size: 0.78em; border: 1px solid #ccc; border-radius: 2px; }
tr.queued td { color: #888; }
tr.running td { background: #e3f2fd; font-weight: 500; }
tr.passed td { background: #f1f8e9; }
tr.failed td { background: #ffebee; }
tr.timed_out td { background: #f3e5f5; }
.pass { color: #4CAF50; }
.fail { color: #f44336; }
.no-ref { color: #FF9800; }
.sim-fail { color: #f44336; }
.warn { color: #ff9800; }
a { color: #1976D2; text-decoration: none; }
a:hover { text-decoration: underline; }
</style>
</head>
<body>
<h1>{{ title }}</h1>
<div class="status-bar">
  {% if auto_refresh %}<span class="updated" id="updated-stamp">Updated …</span>
  · elapsed <span id="elapsed-stamp">{{ "%.0f"|format(elapsed) }}s</span>
  <button class="refresh-now" onclick="refreshNow()">Refresh now</button>
  {% else %}Final report · {{ total }} tests{% endif %}
</div>

<div class="bar" id="progress-bar"></div>
<div class="counters" id="counters"></div>

<div class="filter-bar">
  <button class="active" data-status="all" onclick="filterRows('all', this)">All</button>
  <button data-status="fail" onclick="filterRows('fail', this)">Failed</button>
  <button data-status="sim-fail" onclick="filterRows('sim-fail', this)">Sim Failed</button>
  <button data-status="no-ref" onclick="filterRows('no-ref', this)">No Baseline</button>
  <button data-status="warn" onclick="filterRows('warn', this)">Warnings</button>
  <button data-status="pass" onclick="filterRows('pass', this)">Passed</button>
</div>

<table id="results-table">
<thead>
<tr>
  <th data-sort="text" data-key="test">Test</th>
  <th data-sort="text" data-key="model">Model</th>
  <th data-sort="text" data-key="status">Status</th>
  <th data-sort="text" data-key="worker">Worker</th>
  <th data-sort="num" data-key="elapsed">Elapsed (s)</th>
  <th data-sort="num" data-key="nrmse">Worst NRMSE</th>
  <th data-sort="num" data-key="vars">Variables</th>
  <th data-sort="num" data-key="warnings">Warnings</th>
  <th data-sort="num" data-key="translate">Translate (s)</th>
  <th data-sort="num" data-key="sim">Sim (s)</th>
  <th data-sort="num" data-key="total">Total (s)</th>
</tr>
<tr class="filter-row">
  <th><input class="col-filter" data-col-filter="test" placeholder="filter…"></th>
  <th><input class="col-filter" data-col-filter="model" placeholder="filter…"></th>
  <th><input class="col-filter" data-col-filter="status" placeholder="filter…"></th>
  <th><input class="col-filter" data-col-filter="worker" placeholder="filter…"></th>
  <th></th><th></th><th></th><th></th><th></th><th></th><th></th>
</tr>
</thead>
<tbody id="results-tbody">
{% for t in tests %}
<tr class="{{ t.status_class }}"
    data-status="{{ t.status_class }}"
    data-test="{{ t.test_key or '' }}"
    data-model="{{ t.model_id }}"
    data-worker="{% if t.worker_id is not none %}W{{ t.worker_id }}{% endif %}"
    data-sort-test="{{ t.test_key or '' }}"
    data-sort-model="{{ t.model_id }}"
    data-sort-status="{{ t.status_text }}"
    data-sort-worker="{{ t.worker_id if t.worker_id is not none else -1 }}"
    data-sort-elapsed="{{ t.elapsed if t.elapsed is not none else -1 }}"
    data-sort-nrmse="{{ t.worst_nrmse if t.worst_nrmse is not none else -1 }}"
    data-sort-vars="{{ t.n_vars_passed if t.n_vars else -1 }}"
    data-sort-warnings="{{ t.n_warnings if t.n_warnings is not none else -1 }}"
    data-sort-translate="{{ t.translation_wall if t.translation_wall is not none else -1 }}"
    data-sort-sim="{{ t.sim_wall if t.sim_wall is not none else -1 }}"
    data-sort-total="{{ t.total_wall if t.total_wall is not none else -1 }}">
  <td>{% if t.test_key %}<a href="{{ t.test_key }}/">{{ t.test_key }}</a>{% else %}—{% endif %}</td>
  <td>{% if t.report_dir %}<a href="reports/{{ t.report_dir }}/interactive.html">{{ t.model_id }}</a>{% else %}{{ t.model_id }}{% endif %}</td>
  <td><span class="{{ t.status_class }}">{{ t.status_text }}</span></td>
  <td>{% if t.worker_id is not none %}W{{ t.worker_id }}{% else %}—{% endif %}</td>
  <td>{% if t.elapsed is not none %}{{ "%.1f"|format(t.elapsed) }}{% else %}—{% endif %}</td>
  <td>{% if t.worst_nrmse is not none %}{{ "%.4e"|format(t.worst_nrmse) }}{% else %}—{% endif %}</td>
  <td>{% if t.n_vars is not none %}{{ t.n_vars_passed }}/{{ t.n_vars }}{% else %}—{% endif %}</td>
  <td>{% if t.n_warnings is not none and t.n_warnings > 0 %}<span class="warn">{{ t.n_warnings }}</span>{% elif t.n_warnings is not none %}0{% else %}—{% endif %}</td>
  <td>{% if t.translation_wall is not none %}{{ "%.2f"|format(t.translation_wall) }}{% else %}—{% endif %}</td>
  <td>{% if t.sim_wall is not none %}{{ "%.2f"|format(t.sim_wall) }}{% else %}—{% endif %}</td>
  <td>{% if t.total_wall is not none %}{{ "%.2f"|format(t.total_wall) }}{% else %}—{% endif %}</td>
</tr>
{% endfor %}
</tbody>
</table>

<script>
const DASHBOARD_MODE = '{{ mode }}';
const DASHBOARD_TOTAL = {{ total }};
{% include 'dashboard.js' %}
</script>
</body>
</html>
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_dashboard_render.py -v
```

Expected: PASS for the 3 new tests + the 4 existing.

- [ ] **Step 5: Commit**

```bash
git add src/dstf/reporting/templates/dashboard.html tests/test_dashboard_render.py
git commit -m "$(cat <<'EOF'
feat(dashboard): rich template with filter buttons + sort hooks + per-column filter

Ports CSS + sortable column headers + status filter buttons from
the existing index.html. Adds new per-column text filter row below
the header. Sort hooks attach to every column with data-sort/data-key.

JS module (dashboard.js) referenced via Jinja include — populated
in Task 4. Template is now structurally complete for both live and
final modes.

EOF
)"
```

### Task 4: Add the JS module (filter / sort / fetch / refresh)

**Files:**

- Create: `src/dstf/reporting/templates/dashboard.js`
- Test: `tests/test_dashboard_render.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dashboard_render.py`:

```python
def test_live_mode_includes_fetch_loop(tmp_path):
    """In live mode, the fetch loop bootstrap must be present."""
    snapshot = {
        "total": 0, "elapsed": 0.0, "eta_seconds": None,
        "counts": {}, "tests": [], "updated_at": 0.0,
    }
    _write_status_json(tmp_path, snapshot)
    render_live(tmp_path)
    out = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert "setInterval" in out
    assert "fetch('status.json')" in out
    assert "function refreshNow()" in out


def test_final_mode_skips_fetch_loop(tmp_path):
    """In final mode, the fetch loop must NOT auto-start."""
    snapshot = {
        "total": 0, "elapsed": 0.0, "eta_seconds": None,
        "counts": {}, "tests": [], "updated_at": 0.0,
    }
    _write_status_json(tmp_path, snapshot)
    render_final(tmp_path)
    out = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    # The constant flips
    assert "DASHBOARD_MODE = 'final'" in out
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_dashboard_render.py::test_live_mode_includes_fetch_loop tests/test_dashboard_render.py::test_final_mode_skips_fetch_loop -v
```

Expected: 2 FAIL.

- [ ] **Step 3: Create dashboard.js**

Write `src/dstf/reporting/templates/dashboard.js`:

```javascript
// Unified dashboard JS: filter / sort / fetch loop / refresh button.
// Loaded inline via {% include 'dashboard.js' %} in dashboard.html.
// Reads two top-level Jinja-injected constants: DASHBOARD_MODE
// ('live' or 'final') and DASHBOARD_TOTAL (int).

(function() {
  const tbody = document.getElementById('results-tbody');
  const headers = document.querySelectorAll('#results-table thead tr:first-child th[data-sort]');
  const colFilters = document.querySelectorAll('input.col-filter');

  // ---- Filter (status buttons + per-column text) ----
  let activeStatus = 'all';
  const colFilterValues = {};

  function applyFilters() {
    Array.from(tbody.querySelectorAll('tr')).forEach(row => {
      let visible = true;
      // Status filter
      if (activeStatus !== 'all') {
        if (activeStatus === 'warn') {
          visible = parseInt(row.dataset.sortWarnings || '0') > 0;
        } else {
          visible = row.dataset.status === activeStatus;
        }
      }
      // Per-column text filter
      if (visible) {
        for (const [col, q] of Object.entries(colFilterValues)) {
          if (!q) continue;
          const val = (row.dataset[col] || '').toLowerCase();
          if (val.indexOf(q.toLowerCase()) === -1) {
            visible = false;
            break;
          }
        }
      }
      row.style.display = visible ? '' : 'none';
    });
  }

  window.filterRows = function(status, btn) {
    document.querySelectorAll('.filter-bar button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    activeStatus = status;
    applyFilters();
  };

  colFilters.forEach(inp => {
    inp.addEventListener('input', () => {
      colFilterValues[inp.dataset.colFilter] = inp.value;
      applyFilters();
    });
  });

  // ---- 3-state sort cycle: none → sorted → reverse → none ----
  // Numeric columns descend first (largest NRMSE first when triaging);
  // text columns ascend first (alphabetical).
  let lastSorted = { th: null, dir: null };

  headers.forEach(th => {
    th.addEventListener('click', () => sortBy(th));
  });

  function sortBy(th) {
    const key = th.dataset.key;
    const kind = th.dataset.sort;  // 'num' or 'text'
    const firstClick = (kind === 'num') ? 'desc' : 'asc';

    let dir;
    if (lastSorted.th === th) {
      // Cycle: firstClick → opposite → none
      if (lastSorted.dir === firstClick) {
        dir = (firstClick === 'asc') ? 'desc' : 'asc';
      } else {
        dir = null;  // back to natural order
      }
    } else {
      dir = firstClick;
    }

    headers.forEach(h => h.classList.remove('sorted-asc', 'sorted-desc'));
    if (dir) th.classList.add('sorted-' + dir);
    lastSorted = { th, dir };

    const rows = Array.from(tbody.querySelectorAll('tr'));
    if (dir === null) {
      // Natural order: sort by data-sort-test (insertion order proxy)
      rows.sort((a, b) => {
        const at = a.dataset.sortTest || a.dataset.sortModel || '';
        const bt = b.dataset.sortTest || b.dataset.sortModel || '';
        return at < bt ? -1 : at > bt ? 1 : 0;
      });
    } else {
      rows.sort((a, b) => {
        const ak = 'sort' + key[0].toUpperCase() + key.slice(1);
        let av = a.dataset[ak], bv = b.dataset[ak];
        if (kind === 'num') {
          av = parseFloat(av); bv = parseFloat(bv);
          if (isNaN(av)) av = -Infinity;
          if (isNaN(bv)) bv = -Infinity;
        } else {
          av = (av || '').toLowerCase();
          bv = (bv || '').toLowerCase();
        }
        if (av < bv) return dir === 'asc' ? -1 : 1;
        if (av > bv) return dir === 'asc' ? 1 : -1;
        return 0;
      });
    }
    rows.forEach(r => tbody.appendChild(r));
  }

  // ---- Counters + progress bar (rendered from status snapshot) ----
  function renderCounters(counts, total) {
    const cn = document.getElementById('counters');
    const order = ['queued', 'running', 'passed', 'failed', 'timed_out'];
    let html = '';
    for (const k of order) {
      html += `<div class="counter"><div class="label">${k}</div>` +
              `<div class="value">${counts[k] || 0}</div></div>`;
    }
    html += `<div class="counter"><div class="label">Total</div>` +
            `<div class="value">${total}</div></div>`;
    cn.innerHTML = html;

    const bar = document.getElementById('progress-bar');
    const pct = (n) => total ? (n / total * 100).toFixed(2) : 0;
    bar.innerHTML =
      `<span class="b-passed" style="width:${pct(counts.passed || 0)}%"></span>` +
      `<span class="b-failed" style="width:${pct(counts.failed || 0)}%"></span>` +
      `<span class="b-timed_out" style="width:${pct(counts.timed_out || 0)}%"></span>` +
      `<span class="b-running" style="width:${pct(counts.running || 0)}%"></span>`;
  }

  // Initial render from inline data (read from data-* on tbody rows)
  function initialCountsFromRows() {
    const counts = { queued: 0, running: 0, passed: 0, failed: 0, timed_out: 0 };
    Array.from(tbody.querySelectorAll('tr')).forEach(row => {
      const s = row.dataset.status;
      if (s in counts) counts[s]++;
      else if (s === 'sim-fail') counts.failed++;
    });
    return counts;
  }
  renderCounters(initialCountsFromRows(), DASHBOARD_TOTAL);

  // ---- Live-mode fetch loop ----
  if (DASHBOARD_MODE === 'live') {
    let intervalId = null;

    function updateRowFromSnapshot(t) {
      // Update an existing row's mutable fields from the fresh snapshot.
      // Append a new row if it didn't exist (test registered mid-run).
      let row = tbody.querySelector(`tr[data-test="${t.test_key}"]`);
      if (!row) {
        // Row doesn't exist in current DOM — page reload would catch it,
        // but for now just trigger a soft full-reload to pick up new schema
        location.reload();
        return;
      }
      const status = (t.status || 'queued').replace('_', '-');
      row.dataset.status = status;
      row.dataset.sortStatus = t.status || 'queued';
      row.dataset.sortElapsed = (t.elapsed != null) ? t.elapsed : -1;
      row.dataset.sortWorker = (t.worker_id != null) ? t.worker_id : -1;
      row.className = status;
      // Re-render the cells inline (cheaper than full row replacement)
      const cells = row.querySelectorAll('td');
      cells[2].innerHTML = `<span class="${status}">${t.status || 'queued'}</span>`;
      cells[3].textContent = (t.worker_id != null) ? `W${t.worker_id}` : '—';
      cells[4].textContent = (t.elapsed != null) ? t.elapsed.toFixed(1) : '—';
    }

    async function poll() {
      try {
        const r = await fetch('status.json', { cache: 'no-store' });
        if (!r.ok) return;
        const snap = await r.json();
        renderCounters(snap.counts || {}, snap.total || 0);
        for (const t of snap.tests || []) updateRowFromSnapshot(t);
        applyFilters();
        const stamp = document.getElementById('updated-stamp');
        if (stamp) stamp.textContent = 'Updated ' + new Date().toLocaleTimeString();
        const elap = document.getElementById('elapsed-stamp');
        if (elap) elap.textContent = (snap.elapsed || 0).toFixed(0) + 's';
      } catch (e) {
        // Silent: a transient fetch failure shouldn't break the page.
      }
    }

    window.refreshNow = function() { poll(); };
    intervalId = setInterval(poll, 2000);
    poll();  // immediate first fetch
  } else {
    // Final mode: refresh button still works (one-shot reload)
    window.refreshNow = function() { location.reload(); };
  }
})();
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_dashboard_render.py -v
```

Expected: all 9 PASS.

- [ ] **Step 5: Smoke-render against ModelicaTestingLib**

```bash
uv run dstf --config examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/testing.json discover
```

Then manually:

```bash
python -c "
from pathlib import Path
import json
work = Path('/tmp/dstf-smoke')
work.mkdir(exist_ok=True)
(work / 'status.json').write_text(json.dumps({
    'total': 2, 'elapsed': 5.0, 'eta_seconds': None,
    'counts': {'queued': 0, 'running': 1, 'passed': 1,
               'failed': 0, 'timed_out': 0},
    'tests': [
        {'test_key': 'test_0001', 'model_id': 'A.B', 'status': 'passed',
         'elapsed': 2.0, 'worker_id': 0, 'report_dir': 'test_0001'},
        {'test_key': 'test_0002', 'model_id': 'A.C', 'status': 'running',
         'elapsed': None, 'worker_id': 1, 'report_dir': 'test_0002'},
    ],
    'updated_at': 0.0,
}))
from dstf.reporting.dashboard_render import render_live
render_live(work)
print('Wrote', work / 'dashboard.html')
"
```

Open `/tmp/dstf-smoke/dashboard.html` in a browser to eyeball: filter buttons work, sort works, "Refresh now" button visible.

- [ ] **Step 6: Commit**

```bash
git add src/dstf/reporting/templates/dashboard.js tests/test_dashboard_render.py
git commit -m "$(cat <<'EOF'
feat(dashboard): JS module — filter, sort, fetch loop, refresh button

- Status-button filter (port from index.html) + per-column text filter (new)
- 3-state sort cycle: none → sorted → reverse → none; desc-first on
  numeric columns (NRMSE / wall times — largest first when triaging),
  asc-first on text columns (alphabetical)
- Live-mode setInterval(fetch('status.json')) every 2s; updates row
  mutable fields (status, worker, elapsed) inline, preserves scroll
  position. Falls back to location.reload() if a new test appears
  mid-run (rare; the alternative is full DOM diff which isn't worth it)
- "Refresh now" button: in live mode triggers immediate poll; in
  final mode reloads the page
- DOM-only updates; no full-page meta-refresh

EOF
)"
```

---

## Phase 4 — Wire ProgressReporter to render_live

Switches the live-mode write path from the f-string in `progress.py` to `dashboard_render.render_live`. The standalone `index.html` still exists at this point.

### Task 5: Replace progress.py inline template

**Files:**

- Modify: `src/dstf/simulators/progress.py:23-72` (delete `_DASHBOARD_TEMPLATE`)
- Modify: `src/dstf/simulators/progress.py:169-291` (replace `_write_html` and `finalize`)
- Test: `tests/test_progress_reporter.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_progress_reporter.py`:

```python
"""Smoke tests for ProgressReporter writing dashboard.html via dashboard_render."""

from pathlib import Path

from dstf.simulators.progress import ProgressReporter


def test_register_writes_status_and_dashboard(tmp_path):
    pr = ProgressReporter(tmp_path, total=2)
    pr.register("test_0001", "Lib.A")
    pr.register("test_0002", "Lib.B")
    assert (tmp_path / "status.json").exists()
    assert (tmp_path / "dashboard.html").exists()
    html = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert "Lib.A" in html
    assert "Lib.B" in html
    assert "DASHBOARD_MODE = 'live'" in html


def test_finalize_strips_live_mode(tmp_path):
    pr = ProgressReporter(tmp_path, total=1)
    pr.register("test_0001", "Lib.A")
    pr.on_start("test_0001", worker_id=0)
    pr.on_finish("test_0001", success=True, elapsed=1.0)
    pr.finalize()
    html = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert "DASHBOARD_MODE = 'final'" in html
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_progress_reporter.py -v
```

Expected: FAIL — `dashboard.html` has `DASHBOARD_MODE` but pre-cutover progress.py emits the old f-string template that has no such marker, and `finalize()` doesn't yet call render_final.

- [ ] **Step 3: Strip the f-string template + rewire `_write` and `finalize`**

In `src/dstf/simulators/progress.py`, delete lines 23-72 (the entire `_DASHBOARD_TEMPLATE = """..."""` block).

Replace the `_write` method (around line 169-176) with:

```python
    def _write(self) -> None:
        # Serialize all file writes — without this, two threads can race on
        # the same tmp filename and `replace` fails on Windows when the file
        # is still open by another thread.
        snapshot = self._snapshot()
        with self._write_lock:
            self._write_json(snapshot)
            self._render_dashboard(mode="live")
```

Replace `_write_html` (lines 210-278) with:

```python
    def _render_dashboard(self, mode: str) -> None:
        """Defer HTML rendering to dashboard_render.

        ProgressReporter's job is to keep status.json fresh + own atomic
        writes; the page rendering lives in reporting/dashboard_render.py
        so the live and final pages share one template.
        """
        from ..reporting.dashboard_render import render_live, render_final
        if mode == "live":
            render_live(self.work_dir)
        else:
            render_final(self.work_dir)
```

Replace `finalize` (lines 280-290) with:

```python
    def finalize(self) -> None:
        """Write a final status.json + render dashboard.html in final mode.

        Final mode strips the JS-fetch loop bootstrap so the page becomes
        a static report. Comparison-data sidecars (per-test
        comparison_data.json) are merged in by dashboard_render.render_final
        when present — typically populated later by --report.
        """
        with self._write_lock:
            snapshot = self._snapshot()
            self._write_json(snapshot)
            self._render_dashboard(mode="final")
```

Also delete the now-unused `_atomic_write` if it's only used by `_write_html`. Check first:

```bash
grep -n "_atomic_write" src/dstf/simulators/progress.py
```

If `_atomic_write` is still used by `_write_json` (it should be, line 207), keep it.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_progress_reporter.py -v
```

Expected: 2 PASS.

- [ ] **Step 5: Run full test suite**

```bash
uv run pytest -q --ignore=tests/test_interactive_html.py
```

Expected: all pass (one new test file adds 2 tests; total 844 passed + 3 skipped).

- [ ] **Step 6: Commit**

```bash
git add src/dstf/simulators/progress.py tests/test_progress_reporter.py
git commit -m "$(cat <<'EOF'
feat(progress): delegate dashboard.html rendering to dashboard_render

ProgressReporter no longer carries an inline _DASHBOARD_TEMPLATE
f-string. _write_html is replaced by _render_dashboard which calls
dashboard_render.render_live (for in-flight state changes) or
render_final (for finalize()).

The reporter's responsibility narrows to:
- maintain in-memory test state
- write status.json atomically
- trigger dashboard re-render via the shared module

That collapses two HTML codepaths to one. Live and final pages now
render through the same Jinja template; the page progressively
enriches as comparison sidecars become available.

EOF
)"
```

---

## Phase 5 — Final-render hookup from CLI + delete index.html template

Wires `cmd_run` and `cmd_compare` to call `render_final` after comparison so the unified page picks up post-run columns. Deletes the standalone `index.html` template — the dashboard takes over as the entry-point page.

### Task 6: Have generate_report_suite write per-test sidecars in dashboard-readable shape

**Files:**

- Modify: `src/dstf/reporting/plot_comparison.py:1284-1299` (`generate_report_suite`)
- Modify: `src/dstf/reporting/plot_comparison.py:917-1000` (`generate_comparison_plots` — augment `comparison_data.json`)
- Test: `tests/test_dashboard_render.py` (extend)

The dashboard_render module already reads per-test `reports/<test>/comparison_data.json`. Today this file exists and contains the per-test rendering context, but the row-summary fields (worst_nrmse, n_vars_passed, n_warnings, translation_wall, sim_wall, total_wall, ref_id) live in `_render_one_test`'s return dict, not in the sidecar. We need to write them into the sidecar so the dashboard's `_enrich_row_from_comparison` can find them.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dashboard_render.py`:

```python
def test_render_final_picks_up_real_sidecar_shape(tmp_path):
    """The sidecar emitted by generate_comparison_plots includes the
    summary fields (worst_nrmse, n_vars, etc.) at the top level so
    build_dashboard_context can read them without unwrapping."""
    snapshot = {
        "total": 1, "elapsed": 5.0, "eta_seconds": None,
        "counts": {"queued": 0, "running": 0, "passed": 1,
                   "failed": 0, "timed_out": 0},
        "tests": [{"test_key": "test_0001", "model_id": "Lib.A",
                   "status": "passed", "elapsed": 2.0,
                   "worker_id": 0, "report_dir": "test_0001"}],
        "updated_at": 0.0,
    }
    _write_status_json(tmp_path, snapshot)
    test_dir = tmp_path / "reports" / "test_0001"
    test_dir.mkdir(parents=True)
    # Sidecar shape after the patch — summary fields at the top level
    # alongside the existing rendering context fields
    (test_dir / "comparison_data.json").write_text(json.dumps({
        "model_id": "Lib.A",
        "summary": {
            "worst_nrmse": 1.2e-5,
            "n_vars": 3,
            "n_vars_passed": 3,
            "n_warnings": 1,
            "translation_wall": 0.5,
            "sim_wall": 1.5,
            "total_wall": 2.0,
            "ref_id": "ref_0042",
        },
        # other context fields can also exist (variables, etc.) — ignored here
    }))
    ctx = build_dashboard_context(tmp_path, mode="final")
    row = ctx["tests"][0]
    assert row["worst_nrmse"] == 1.2e-5
    assert row["n_vars_passed"] == 3
    assert row["n_warnings"] == 1
    assert row["ref_id"] == "ref_0042"
```

- [ ] **Step 2: Update `_enrich_row_from_comparison` to read from `summary` block**

In `src/dstf/reporting/dashboard_render.py`, modify `_enrich_row_from_comparison`:

```python
def _enrich_row_from_comparison(row: dict, comp: dict) -> None:
    """Copy post-run fields from a per-test comparison_data.json into a row.

    The sidecar puts row-summary fields under a `summary` block alongside
    the per-variable context. Fall back to top-level for backward compat.
    """
    summary = comp.get("summary", comp)
    for key in (
        "worst_nrmse", "n_vars", "n_vars_passed", "n_warnings",
        "translation_wall", "sim_wall", "total_wall",
        "ref_id", "field_sources",
    ):
        if key in summary:
            row[key] = summary[key]
```

- [ ] **Step 3: Run the new test**

```bash
uv run pytest tests/test_dashboard_render.py::test_render_final_picks_up_real_sidecar_shape -v
```

Expected: PASS (the previous test_build_context_final_with_comparisons that wrote top-level keys still works thanks to the fallback).

- [ ] **Step 4: Augment generate_comparison_plots to write the summary block**

In `src/dstf/reporting/plot_comparison.py`, locate the `data_path.write_text(json.dumps(context, indent=2, default=str), ...)` call near line 960. Just before that call, expand `context` with a `summary` block.

Add this block right before line 960:

```python
    # Summary block for the unified dashboard. The full context dict is
    # the per-variable rendering input; the dashboard only needs row-level
    # summary fields, exposed under "summary" so build_dashboard_context
    # can read them without parsing the heavy variable arrays.
    context["summary"] = {
        "model_id": model_id,
        "worst_nrmse": (max((v.nrmse for v in comparisons), default=None)
                        if comparisons else None),
        "n_vars": len(comparisons) if comparisons else 0,
        "n_vars_passed": (sum(1 for v in comparisons if v.passed)
                          if comparisons else 0),
        "n_warnings": len(warnings) if warnings else 0,
        "translation_wall": (cur_stats.get("timing", {}).get("translation_wall")
                             if isinstance(cur_stats, dict) else None),
        "sim_wall": (cur_stats.get("timing", {}).get("sim_wall")
                     if isinstance(cur_stats, dict) else None),
        "total_wall": (cur_stats.get("timing", {}).get("total_wall")
                       if isinstance(cur_stats, dict) else None),
        "field_sources": (test_model.field_sources
                          if test_model and hasattr(test_model, "field_sources")
                          else {}),
    }
```

- [ ] **Step 5: Run plot tests for regression**

```bash
uv run pytest tests/test_html_report.py tests/test_dashboard_render.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/dstf/reporting/dashboard_render.py src/dstf/reporting/plot_comparison.py tests/test_dashboard_render.py
git commit -m "$(cat <<'EOF'
feat(dashboard): per-test sidecar 'summary' block feeds final dashboard

generate_comparison_plots now writes a `summary` block into each
test's comparison_data.json containing the row-level fields the
unified dashboard needs (worst_nrmse, n_vars, n_warnings,
translate/sim/total wall times, ref_id, field_sources for the
provenance column).

dashboard_render._enrich_row_from_comparison reads from
comp["summary"] with a top-level fallback for older sidecars.

After Phase 5 ships, the unified dashboard.html in --report mode
will show NRMSE / wall-time / warning columns populated from these
sidecars; without --report the columns stay dashed (live mode never
generates per-test sidecars).

EOF
)"
```

### Task 7: Trigger render_final from cmd_run and cmd_compare

**Files:**

- Modify: `src/dstf/cli.py:_generate_report_suite` (around line 1199)
- Modify: `src/dstf/cli.py:cmd_run` and `cmd_compare` to always call `render_final` after comparison
- Test: `tests/test_dashboard_render.py` (extend with end-to-end CLI smoke)

- [ ] **Step 1: Update `_generate_report_suite` to also refresh the dashboard**

In `src/dstf/cli.py`, locate `_generate_report_suite` (around line 1199) and replace with:

```python
def _generate_report_suite(comparisons, results, tests, store, config) -> int:
    """Generate per-test reports + refresh the unified dashboard.

    generate_report_suite (in plot_comparison) writes per-test
    interactive.html + comparison_data.json sidecars under
    work_dir/reports/. dashboard_render.render_final then reads
    those sidecars and produces the top-level work_dir/dashboard.html.
    """
    from .reporting.plot_comparison import generate_report_suite, open_in_browser
    from .reporting.dashboard_render import render_final

    generate_report_suite(comparisons, results, tests, store, config)
    render_final(config.work_dir)
    dashboard_path = config.work_dir / "dashboard.html"
    print(f"Report: {dashboard_path}")
    open_in_browser(dashboard_path)
    return 0
```

- [ ] **Step 2: Have `cmd_compare` and `cmd_run` always call `render_final` even without `--report`**

In `cmd_compare` (around line 302), after `compare_all` returns and before the `--report` branch:

```python
    comparisons = compare_all(tests, results, store, config.tolerance, config.default_points)

    # Always refresh the unified dashboard, even without --report. Without
    # per-test sidecars (no --report run yet), the post-run columns stay
    # dashed and the page reads as a "live" snapshot frozen at the moment
    # comparison finished.
    from .reporting.dashboard_render import render_final
    render_final(config.work_dir)

    if getattr(args, "report", False):
        return _generate_report_suite(comparisons, results, tests, store, config)

    return _output_report(comparisons, args)
```

In `cmd_run`, similarly add a `render_final` call before the `--report` branch (around line 257):

```python
        comparisons = compare_all(scope_tests, results, store, config.tolerance, config.default_points)

        # Always refresh the unified dashboard before any --report work.
        from .reporting.dashboard_render import render_final
        render_final(config.work_dir)

        if args.report:
            return _generate_report_suite(comparisons, results, scope_tests, store, config)

        return _output_report(comparisons, args)
```

- [ ] **Step 3: Run the existing CLI suite**

```bash
uv run pytest tests/test_cli.py -v
```

Expected: PASS.

- [ ] **Step 4: Smoke run against ModelicaTestingLib**

```bash
uv run dstf --config examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/testing.json run --simulator OpenModelica --report
```

Open the printed dashboard URL. Verify:
- All 12 tests show in the table.
- NRMSE / Sim (s) / Translate (s) columns are populated.
- Per-test report links resolve to interactive.html.
- Status filter buttons work; per-column text filter works.
- Sort cycle works through 3 states.

- [ ] **Step 5: Commit**

```bash
git add src/dstf/cli.py
git commit -m "$(cat <<'EOF'
feat(cli): always refresh unified dashboard after comparison

cmd_run and cmd_compare now call dashboard_render.render_final
unconditionally after compare_all, regardless of --report. This way
the unified dashboard.html always reflects the latest compare
results — even without --report, the page is honest about pass/fail
counts (post-comparison columns stay dashed in that mode since no
per-test sidecars were written, but the row status updates).

_generate_report_suite stays as the per-test deep-dive generator
plus a final render trigger.

EOF
)"
```

### Task 8: Delete the standalone index.html template + rerun-prefix machinery

**Files:**

- Delete: `src/dstf/reporting/templates/index.html`
- Modify: `src/dstf/reporting/plot_comparison.py:generate_report_suite` (drop the `_render_template("index.html", ...)` call)
- Test: `tests/test_html_report.py` (update if any tests reference index.html directly)

- [ ] **Step 1: Verify nothing else references index.html**

```bash
grep -rn 'index\.html\|"index"' src/ tests/ 2>/dev/null
```

Expected: matches in plot_comparison.py (`generate_report_suite` writing index.html), in cli.py (`_generate_report_suite` printing path), in tests/test_html_report.py if any. Note all matches.

- [ ] **Step 2: Remove the index.html render in `generate_report_suite`**

In `src/dstf/reporting/plot_comparison.py`, locate the index-context build + render block at lines 1284-1299 and replace it with a comment + early return. The function now stops at "all per-test reports written" — top-level dashboard rendering is the caller's job (Task 7 already wired it).

Replace lines 1284-1299 (the `# Build index context` block through `_render_template("index.html", index_context, index_path)`) with:

```python
    # Per-test interactive.html + comparison_data.json sidecars are
    # written above. The unified work_dir/dashboard.html is rendered
    # by cli._generate_report_suite via dashboard_render.render_final;
    # the standalone index.html that used to live here is retired.
```

The phase-timing print block that follows (`# Phase timing — exposes whether parallelism is helping`) stays — it depends on `index_tests`, not on the index render. Verify it still works.

Update the function's return type and final return: change the trailing `return index_path` (or wherever the function returns the index path) to `return config.work_dir / "dashboard.html"` so any caller that did rely on the return value gets a sensible Path. The return is no longer used by `_generate_report_suite` (Task 7 step 1 reads from `config.work_dir` directly), but it's part of the public function signature.

- [ ] **Step 3: Delete the standalone index.html template**

```bash
git rm src/dstf/reporting/templates/index.html
```

- [ ] **Step 4: Verify no test references index.html**

```bash
grep -rln 'index\.html\|render_template.*index' tests/ 2>/dev/null
```

Audit at plan-write time: this command produces zero matches. If any appear by the time this task runs, update each to reference `dashboard.html` and the unified test surface. If none appear, no test changes are required for this step — proceed to Step 5.

- [ ] **Step 5: Run full suite**

```bash
uv run pytest -q --ignore=tests/test_interactive_html.py
```

Expected: all pass. Watch for any test that opens `index.html` — switch to `dashboard.html`.

- [ ] **Step 6: Commit**

```bash
git rm src/dstf/reporting/templates/index.html
git add src/dstf/reporting/plot_comparison.py tests/
git commit -m "$(cat <<'EOF'
refactor(reporting): retire standalone index.html template

The unified dashboard.html now serves both live progress and the
post-run report. index.html was an artifact of the pre-unification
two-page split; with render_final hooked into both cmd_run and
cmd_compare, dashboard.html replaces it cleanly.

generate_report_suite returns the dashboard path now. Per-test
reports/<test>/interactive.html deep dives are unchanged.

EOF
)"
```

---

## Phase 6 — Resolution-explainer column

Wires the `field_sources` data plumbed in Phase 1 through to a visible column in the dashboard. Without `--report` the column shows on every row (it's annotation/test_spec metadata, not comparison-derived). With `--report`, the column shows the same data plus visual highlighting on overrides.

### Task 9: Render the Resolution column in dashboard.html

**Files:**

- Modify: `src/dstf/reporting/templates/dashboard.html`
- Modify: `src/dstf/reporting/dashboard_render.py:build_dashboard_context` (read field_sources from snapshot OR sidecar)
- Modify: `src/dstf/simulators/progress.py:TestStatus` and `register()` (carry field_sources)
- Modify: `src/dstf/simulators/base.py` (or wherever `register` is called) — pass `test.field_sources` through
- Test: `tests/test_dashboard_render.py` (extend)

Search for ProgressReporter.register callsites:

```bash
grep -rn "progress\.register\|reporter\.register\|ProgressReporter" src/dstf/simulators/ 2>/dev/null
```

Each register call has access to the TestModel; we pass `test.field_sources` through.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dashboard_render.py`:

```python
def test_resolution_column_shows_provenance(tmp_path):
    """field_sources from status.json (or sidecar) flows into row cells."""
    snapshot = {
        "total": 1, "elapsed": 5.0, "eta_seconds": None,
        "counts": {"queued": 0, "running": 0, "passed": 1,
                   "failed": 0, "timed_out": 0},
        "tests": [{
            "test_key": "test_0001", "model_id": "Lib.A",
            "status": "passed", "elapsed": 2.0, "worker_id": 0,
            "report_dir": "test_0001",
            "field_sources": {
                "stop_time": "test_spec",
                "tolerance": "annotation",
            },
        }],
        "updated_at": 0.0,
    }
    _write_status_json(tmp_path, snapshot)
    render_live(tmp_path)
    out = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert "test_spec" in out
    assert "Resolution" in out  # column header visible
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/test_dashboard_render.py::test_resolution_column_shows_provenance -v
```

Expected: FAIL.

- [ ] **Step 3: Update `TestStatus` and `register()` to carry field_sources**

In `src/dstf/simulators/progress.py:75-86`, augment `TestStatus`:

```python
@dataclass
class TestStatus:
    test_key: str
    model_id: str
    status: str = "queued"
    started_at: Optional[float] = None
    elapsed: Optional[float] = None
    detail: Optional[str] = None
    worker_id: Optional[int] = None
    report_dir: Optional[str] = None
    phase: Optional[str] = None
    # PR-N: per-field provenance, plumbed from TestModel.field_sources
    field_sources: dict = field(default_factory=dict)
```

(Make sure `field` is imported from dataclasses at the top of the file.)

Update `register()` signature:

```python
    def register(
        self,
        test_key: str,
        model_id: str,
        report_dir: Optional[str] = None,
        field_sources: Optional[dict] = None,
    ) -> None:
        with self._lock:
            self._tests[test_key] = TestStatus(
                test_key=test_key,
                model_id=model_id,
                report_dir=report_dir,
                field_sources=field_sources or {},
            )
        self._write()
```

- [ ] **Step 4: Update register callsites to pass field_sources**

```bash
grep -rn "\.register(" src/dstf/simulators/ 2>/dev/null | grep -v "atexit\|__init__"
```

For each callsite that currently does `progress.register(test_key, model.model_id, ...)`, add `field_sources=test.field_sources` (or equivalent attribute name where `test` is a `TestModel`).

Most are in:
- `src/dstf/simulators/base.py` (PersistentRunnerBase)
- `src/dstf/simulators/dymola/runner.py` (batch path)
- `src/dstf/simulators/openmodelica/runner.py` (batch path)

Each looks like `progress.register(test_key=k, model_id=test.model_id, report_dir=...)`. Add `field_sources=test.field_sources` keyword.

- [ ] **Step 5: Update dashboard_render to surface field_sources**

In `src/dstf/reporting/dashboard_render.py:build_dashboard_context`, augment the row dict with field_sources from the snapshot test entry:

```python
        row = {
            "test_key": t.get("test_key"),
            "model_id": t.get("model_id"),
            ...
            "field_sources": t.get("field_sources") or {},
        }
```

Then in final mode, the sidecar's `summary.field_sources` overrides if richer (it includes resolved values, not just status-time placeholders). The existing `_enrich_row_from_comparison` already copies `field_sources` from `summary`.

- [ ] **Step 6: Add the Resolution column to the template**

In `src/dstf/reporting/templates/dashboard.html`, add a new column header (before "Worst NRMSE"):

```html
  <th data-sort="text" data-key="resolution" title="Provenance of stop_time / tolerance / method / number_of_intervals / output_interval — annotation, test_spec, or default">Resolution</th>
```

And add the matching `<td>` cell in the row template:

```html
  <td>
    {% if t.field_sources %}
      {% set src_set = t.field_sources.values()|list|unique|list %}
      {% if src_set|length == 1 %}
        <span title="all fields from {{ src_set[0] }}">{{ src_set[0] }}</span>
      {% else %}
        <span title="{% for k, v in t.field_sources.items() %}{{ k }}: {{ v }}{% if not loop.last %}, {% endif %}{% endfor %}">mixed</span>
      {% endif %}
    {% else %}—{% endif %}
  </td>
```

Update the matching filter row to add an empty cell, and update the data-sort-resolution attribute on each `<tr>`:

```html
data-sort-resolution="{% if t.field_sources %}{{ t.field_sources.values()|list|unique|join(',') }}{% endif %}"
```

- [ ] **Step 7: Run tests**

```bash
uv run pytest tests/test_dashboard_render.py tests/test_progress_reporter.py -v
```

Expected: all PASS including the new resolution test.

- [ ] **Step 8: Smoke render**

```bash
uv run dstf --config examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/testing.json run --simulator OpenModelica --report
```

Confirm the Resolution column shows `annotation` for tests with no test_spec.json overrides, `test_spec` (or `mixed`) for tests that do override.

- [ ] **Step 9: Commit**

```bash
git add src/dstf/simulators/progress.py src/dstf/simulators/ src/dstf/reporting/dashboard_render.py src/dstf/reporting/templates/dashboard.html tests/test_dashboard_render.py
git commit -m "$(cat <<'EOF'
feat(dashboard): resolution-explainer column shows per-field provenance

The Resolution column tells the user where stop_time / tolerance /
method / number_of_intervals / output_interval came from for each
test (annotation, test_spec, or default) — surfacing what was
previously buried in TestModel.field_sources.

Plumbing:
- TestStatus carries field_sources from register()
- ProgressReporter.register accepts a field_sources kwarg; backend
  callsites pass test.field_sources
- generate_comparison_plots writes field_sources into the per-test
  sidecar summary block
- build_dashboard_context surfaces it; the template renders one
  source label or "mixed" with per-field detail in the title attr

Closes the resolution-explainer sub-item from the annotation
contract grilling. The user can now answer "where did stop_time
come from?" by looking at the dashboard, no docs lookup needed.

EOF
)"
```

---

## Phase 7 — Cleanup, full validation, docs

### Task 10: Run the full pre-commit gauntlet

- [ ] **Step 1: Full test suite**

```bash
uv run pytest -q --ignore=tests/test_interactive_html.py
```

Expected: all pass. Baseline was 842 passed + 3 skipped. New tests added: ~10 in test_dashboard_render.py, ~2 in test_progress_reporter.py, ~2 in test_discovery.py. Expected new total ≈ 856 passed + 3 skipped.

- [ ] **Step 2: Smoke each backend**

```bash
uv run dstf --config examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/testing.json run --simulator Dymola --report
uv run dstf --config examples/modelica/ModelicaTestingLib/Resources/ReferenceResults/testing.json run --simulator OpenModelica --report
uv run dstf --config examples/julia/JuliaMtkTestingLib/Resources/ReferenceResults/testing.json run --report
uv run dstf --config examples/python/PythonTestingLib/Resources/ReferenceResults/testing.json run --report
```

For each, verify:
- `dashboard.html` exists at `config.work_dir/dashboard.html`
- Page opens, table renders all tests
- Filter buttons + per-column filter + sort cycle work
- "Refresh now" button triggers reload (or fetch in live mode)
- Resolution column shows expected provenance
- Per-test interactive.html links resolve

- [ ] **Step 3: Verify live mode by interrupting a long run**

Open a long backend run, then in another terminal `tail -f` the dashboard.html — confirm rewrite cadence. Open the browser to the dashboard URL during the run; observe the fetch loop updating rows without scrolling-reset.

- [ ] **Step 4: LOC accounting**

```bash
git diff --stat 4873b19..HEAD -- 'src/dstf/reporting/' 'src/dstf/simulators/progress.py'
```

Net should be ≤ ~50 LOC additions (template + JS + render module added; f-string template + index.html deleted).

### Task 11: Update docs to reflect the unified surface

**Files:**

- Modify: `CLAUDE.md`
- Modify: `docs/usage.md`
- Modify: `docs/SESSION_HANDOFF.md`

- [ ] **Step 1: Update CLAUDE.md "Running the Tool" section**

In `CLAUDE.md`, locate the parallel-run example near line 64:

```markdown
# Live progress: open work_dir/dashboard.html (auto-refreshes every 2s; URL printed on start)
```

Update it to:

```markdown
# Live progress + final report: work_dir/dashboard.html (one page; auto-fetches every 2s during the run, becomes the static report after compare)
```

In CLAUDE.md's `--report` row near line 70, update:

```markdown
# Generate per-test interactive deep-dive plots (the unified dashboard always exists; --report adds reports/test_NNNN/interactive.html plots per test)
uv run dstf --config testing.json run --report ./reports
```

- [ ] **Step 2: Update docs/usage.md**

Find the section describing dashboard / report and update to describe the unified page. Add a brief subsection explaining live vs final mode + the Resolution column. Concrete location: after the existing "Reports & dashboards" section if it exists, or as a new section under "Commands → run".

- [ ] **Step 3: Update SESSION_HANDOFF.md priority bullet**

Find the dashboard-unification bullet under "User-requested next-session priorities" and mark it complete with the commit range. Reduce it to one line in the historical-priority table at the top.

- [ ] **Step 4: Run pytest one more time to confirm doc edits didn't break anything**

```bash
uv run pytest -q --ignore=tests/test_interactive_html.py
```

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md docs/usage.md docs/SESSION_HANDOFF.md
git commit -m "$(cat <<'EOF'
docs: unified dashboard.html + Resolution column

- CLAUDE.md: progress + report are now one page
- usage.md: live vs final modes + Resolution column explained
- SESSION_HANDOFF.md: dashboard-unification priority closed

EOF
)"
```

---

## Acceptance criteria (for self-check at end of plan)

- [ ] `dashboard.html` is the only top-level HTML surface; `index.html` no longer exists in the templates dir.
- [ ] During a run, `dashboard.html` refreshes via JS-fetch (no `<meta http-equiv="refresh">`); browser scroll position is preserved across updates.
- [ ] After comparison (with or without `--report`), the dashboard shows post-run columns: NRMSE, Variables, Warnings, Translate, Sim, Total. With `--report`, per-test report links resolve to `interactive.html` deep dives.
- [ ] Status filter buttons + per-column text filter + 3-state sort (desc-first numeric, asc-first text) all work.
- [ ] "Refresh now" button is visible and functional.
- [ ] Resolution column shows `annotation` / `test_spec` / `default` / `mixed` per test.
- [ ] All 5 backends still produce a dashboard.html via ProgressReporter (no backend-specific fork).
- [ ] Full pytest suite passes (~856 passed + 3 skipped).
- [ ] Net LOC change is small and trends negative (template + module added; f-string template + standalone index.html deleted).
