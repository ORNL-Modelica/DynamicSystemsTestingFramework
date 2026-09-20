# The `plots` Key and the Plot Figure Implementation Plan (sub-project 3a)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give both tiers the data and the figure the redesigned Test page stands on (roadmap §3 "Settles"; brief §3.9 "Plots are modular", §3.10 first ruling; DESIGN.md "Plots are modular", "Array families on a plot"): an optional `plots` key per test in `test_spec.json`, parsed, validated, patchable and exported in the schema; any variable of a test's result file listed and read, tracked or not; one figure builder for a plot of several series with array families drawn as a hue ramp; the Session and the per-test report both drawing a test's plots from that key.

**Architecture:** Four moves, each where the state lives. (1) **The layout is spec data**: `dstf.discovery.plots` parses `plots` into frozen `PlotSpec` / `SeriesSpec`, `TestModel.plots` carries it (`None` = absent = one plot per tracked variable), and `/plots` joins the patch whitelist so a layout edit is a `PatchOp` like any other. (2) **A result file is read by its backend CLASS**: `Backend.result_names(test_dir)` and `Backend.result_series(test_dir, names)` are classmethods, because the report never instantiates a backend (it reads `artifact_files` off the class) and the Session's instance has the same class. (3) **`plot_figure(views)`** in `dstf.figures.plotly`: one view is `variable_figure` unchanged (so every golden holds); several views draw each series in its family's hue, geometry for every leaf of every series, and no overlays or error traces. (4) **Both tiers ask the same question** — `resolved_plots(test, tracked)` — and draw what it answers; the report's template loops over plot sections instead of variables.

**Tech Stack:** Python 3.10+, numpy, Jinja2, Plotly figure dicts (no plotly.py), pytest, Playwright + Chromium for the report page, uv.

**Why this is "3a".** Roadmap §3 is one block; its plan is written as three, built in order — 3a (this), 3b (the Test page: header, tree, stacked plots, criteria rows, inline editor, focus), 3c (the rail, the Tests and Variables drawers, array folding and save-time expansion). Three facts found while reading the tree (2026-09-20) reshape the block and each changes what the next part can assume: a loaded `TestResult` holds only the tracked variables; spec patches are whitelisted to `/comparison` and `/metrics`; the report page is laid out one section per variable. The roadmap's own reason for plans per sub-project — facts found while building change the next recommendation — applies inside the block. Task 7 records the split in the roadmap.

**Owner rulings this plan builds on (2026-09-20, on a rendered board; not reopened):** the criteria tree loads closed; an editor opens below its row; a 40 px left rail; an array family's criteria are ONE folded row; Focus is a button per plot. Only the fourth touches 3a: the family grouping and its slice text live in `dstf.figures.families` so the figure's legend and 3c's folded row read one function.

**Decisions this plan takes where the brief left the choice open** (each marked in its task):
1. `plots` is strict inside (Task 1): an unknown key in a plot or a series, an unknown `kind`, an empty `series`, a glob in `var`, a duplicate `var` in one plot are each refused by name. The entry around it keeps tolerating unknown keys. A typo in a new typed key should be loud; `kind` and the series shape are reserved for later kinds and expressions, which will widen the parser when they exist.
2. `"plots": []` is legal and means no plots (Task 1): a user who removed every plot has said so; absent is the only spelling of "the default".
3. A series whose variable is not in the result draws nothing and is named in the plot's `missing` list (Tasks 4, 5): a layout must survive a rerun that dropped a variable, and a silent blank would read as a flat signal.
4. A multi-series plot draws no overlays and no error traces (Task 3): both are per-variable pickers keyed by trace name on both tiers; on a plot of several series they would need a series selector nobody has asked for. The toolbar says so where it greys them (3b).
5. The family rule is "the LAST `[integer]` in the name" (Task 3): `pipe.T[3]` and `pipe.medium[3].T` both fold; `x[2,3]` does not (no two-index arrays have an asker). One function, `family_of`.
6. Result files are read through classmethods with a base default of "none" (Task 2): a plug-in backend that keeps no readable file stays valid and its tests draw tracked variables only.
7. The report page always renders plot sections (Task 5): with no `plots` key the sections are one per tracked variable, in the order and with the element ids (`plot-N`) the page has today, so nothing downstream of the page moves.

**Review provenance.** The plan text had a fresh-context EXECUTING review on 2026-09-20 (ACCEPT-WITH-FIXES: 7 HIGH, 3 MED, 4 LOW, 5 surviving mutations): Tasks 1–4 were applied in a `git archive` copy, every red state run, every named mutation applied. All findings are folded below; with them the archive's non-playwright suite was green and ruff / bare mypy clean. Task 5 was reviewed by reading and by a red run of its tests; its missed consumers and its one undefined path are folded.

**Not built here, named so nobody looks for it:** anything a browser user clicks (3b, 3c); staging a `plots` edit from the page (3c — this plan lands the whitelist, the validation and the Session's read side, and tests a staged `/plots` op directly); widening the OpenModelica variable filter or FMPy's requested outputs so untracked variables exist in those backends' files (the Variables drawer will say "this backend stores only tracked variables"; a rerun-with-more-variables feature has no asker).

---

## Conventions for every task

- Run from the repo root with `uv run --no-sync ...` (agents share the venv). Gates before every commit, each redirected to a log whose summary line is READ, never piped through `tail`: `uv run --no-sync ruff check`, `uv run --no-sync ruff format --check` (fix with `ruff format <files>`), `uv run --no-sync mypy` **bare, no path**, `uv run --no-sync pytest -q -m "not playwright" > <scratchpad>/gate.log 2>&1; echo "exit $?"; grep -E "passed|failed|error" <scratchpad>/gate.log | tail -1`, and `uv run --no-sync pytest -q -m playwright` the same way — the browser tests RUN, they do not skip.
- **Red before green, shown.** Every test step runs the new test on the unchanged code first and the step says what fails. A test that does not fail on the unchanged code — or on the named mutation, for tests that guard rather than reproduce — is not landed until it does.
- **Named mutations.** Each task lists the mutations its tests must catch. The task's fresh-context review applies each one in a `git archive` copy (memory: plan-build-loop — `export PYTHONPATH=<archive>/src`; `uv run --no-sync --project <repo> pytest -c <archive>/pyproject.toml -o pythonpath=<archive>/tests --rootdir=<archive> -p no:cacheprovider <files>`; the reviewer prints the module's `__file__` first) and reports which survived. A surviving mutation is a plan defect: fix the test.
- **Commit with explicit paths — `git add <new files>` then `git commit -F <msgfile> -- <paths>` — never a bare `git commit`, never `--amend`, never `git stash`.** Messages end with the attribution trailer the session's instructions specify. Nothing is pushed.
- **Never run `dstf run` / `dstf compare` / `dstf author` against `testing_output/` or any real work directory** from a task or a review. Task 8's gate is the orchestrator's and works over a COPY.
- **Done-checks are predicates over the end state**, never counts and never "this grep returns nothing".
- Locate code by CONTENT, not by the line numbers quoted here; they are from 7697c36.
- `docs/ideas.md` and `.soul/` are not touched. `docs/decisions.md` gains exactly one entry, D130, in Task 7.
- No `DSTF-NNN` id in any tracked file.

## File structure

| Path | Responsibility after this plan |
|---|---|
| `src/dstf/discovery/plots.py` | NEW. `SeriesSpec`, `PlotSpec`, `PlotsSpecError`, `parse_plots`, `plots_to_raw`, `default_plots`, `ResolvedPlot`, `resolved_plots`. No I/O. |
| `src/dstf/discovery/test_registry.py` | `TestModel.plots: tuple[PlotSpec, ...] | None`; `merge_tests` carries it. |
| `src/dstf/discovery/spec_parser.py` | Reads `plots`; a bad one is a `ConfigError`, like a bad `metrics`. |
| `src/dstf/discovery/patch_apply.py` | `/plots` joins the default whitelist. |
| `src/dstf/discovery/spec_update.py` | `validate_entry_plots`; `apply_validated_patches` calls it. |
| `src/dstf/reporting/schema_export.py` | `plots` in the entry schema. |
| `src/dstf/backends/base.py` | `Backend.result_names` / `result_series` classmethods (default: none); `last_test_keys(work_dir)`. |
| `src/dstf/backends/common/result_files.py` | NEW. `mat_names` / `mat_series`, `json_names` / `json_series`, `npz_names` / `npz_series`: the three file formats, each a pair of pure functions. |
| `src/dstf/backends/{dymola,openmodelica,fmpy}/backend.py`, `src/dstf/backends/common/json_driver.py` | Each overrides the two classmethods with its file name and its format's pair. |
| `src/dstf/figures/families.py` | NEW. `family_of`, `group_families`, `slice_text`, `ramp`, `HUES`. |
| `src/dstf/figures/plotly.py` | `plot_figure(views, max_samples, *, editable, labels)`. |
| `src/dstf/authoring/session.py` | `Session.variable_names`, `Session.plots`, `Session.plot_figure`; `view` falls back to the result file for an untracked variable; `SessionView` mirrors the three reads. `stage` is unchanged: the `_merge` it already runs parses `plots` and a bad layout raises `ConfigError`. |
| `src/dstf/pipeline.py` | Its hand-built model → key map becomes `last_test_keys`. |
| `src/dstf/reporting/report_suite.py` | `plot_inputs` (layout + plot-only series for one test) and `plot_page_keys` (the page's plot keys from a context), both public; `_build_per_test_args(..., backend_cls=None)` and `cli/review.py` call the first, `generate_comparison_plots` the second. |
| `tests/report_fixtures.py`, `tests/test_interactive_figures.py`, `tests/test_report_size_budget.py` | Build the page's plot keys through `plot_page_keys`; read `PLOT_FIGURES`. |
| `src/dstf/reporting/templates/interactive.html`, `report.js` | The variable loop becomes a plot-section loop; `PLOT_FIGURES` replaces `FIGURES` + `VARIABLE_ORDER`. |
| `tests/test_plots_spec.py` | NEW. Parsing, refusals, round-trip, defaults, resolution. |
| `tests/test_result_files.py` | NEW. The three formats and the four backend classes. |
| `tests/test_plot_figure.py` | NEW. Families, ramp, the multi-series figure, the single-view identity. |
| `tests/test_session.py` | `TestPlots` class. |
| `tests/test_spec_update.py`, `tests/test_patch_apply.py`, `tests/test_export_schema.py` | One test each for `/plots`. |
| `tests/test_report_suite.py`, `tests/test_interactive_report.py` | Plot sections; a two-series plot in Chromium. |
| `docs/usage.md`, `docs/architecture.md`, `DESIGN.md`, `CLAUDE.md`, `docs/decisions.md`, the roadmap | Task 7. |

---

### Task 0: The venv and the baseline gate

**Files:** none changed.

- [ ] **Step 1:** `uv run --no-sync python -c "import nicegui, playwright.sync_api, fmpy; print(nicegui.__version__)"` prints `3.16.0`. If an import fails: `uv sync --extra dev --extra fmpy --extra om --extra author --locked --inexact`, then `uv run --no-sync playwright install chromium`.
- [ ] **Step 2:** Run the four gates of the Conventions and record the summary lines. Expected at 7697c36: ruff, format and bare mypy clean; `2075 passed, 3 skipped` (not playwright); `28 passed` (playwright). A different baseline is recorded and is not a stop; a failing baseline IS a stop.

**Done when:** both commands produced their output in this session's log.

---

### Task 1: The `plots` key — parsed, carried, patchable, in the schema

**Files:**
- Create: `src/dstf/discovery/plots.py`, `tests/test_plots_spec.py`
- Modify: `src/dstf/discovery/test_registry.py` (the `TestModel` dataclass; `merge_tests`), `src/dstf/discovery/spec_parser.py` (`spec_models_from_document`, after the `metrics` block), `src/dstf/discovery/patch_apply.py` (both `whitelist=` defaults), `src/dstf/discovery/spec_update.py`, `src/dstf/reporting/schema_export.py` (the entry's `properties`)
- Test: `tests/test_plots_spec.py`, `tests/test_patch_apply.py`, `tests/test_spec_update.py`, `tests/test_export_schema.py`

- [ ] **Step 1: Write the failing tests.** Create `tests/test_plots_spec.py`:

```python
"""The optional `plots` key of a test_spec.json entry (brief §3.10)."""

from __future__ import annotations

import json

import pytest

from conftest import make_test_model

from dstf.config import ConfigError
from dstf.discovery.plots import (
    PlotSpec,
    PlotsSpecError,
    ResolvedPlot,
    SeriesSpec,
    default_plots,
    parse_plots,
    plots_to_raw,
    resolved_plots,
)
from dstf.discovery.spec_parser import spec_models_from_document
from dstf.discovery.test_registry import merge_tests, spec_tests_by_model

TWO = [
    {"kind": "time", "series": [{"var": "x"}, {"var": "der(x)"}]},
    {"series": [{"var": "pipe.T[1]"}]},
]


def test_parse_reads_plots_and_defaults_kind_to_time():
    plots = parse_plots(TWO)
    assert plots == (
        PlotSpec(series=(SeriesSpec("x"), SeriesSpec("der(x)")), kind="time"),
        PlotSpec(series=(SeriesSpec("pipe.T[1]"),), kind="time"),
    )


def test_round_trip_always_writes_kind():
    assert plots_to_raw(parse_plots(TWO)) == [
        {"kind": "time", "series": [{"var": "x"}, {"var": "der(x)"}]},
        {"kind": "time", "series": [{"var": "pipe.T[1]"}]},
    ]


def test_an_empty_list_is_no_plots_not_the_default():
    assert parse_plots([]) == ()


@pytest.mark.parametrize(
    ("raw", "needle"),
    [
        ({"series": []}, "plots: must be a list"),
        ([["x"]], "plots[0]: must be an object"),
        ([{"series": [{"var": "x"}], "title": "T"}], "plots[0]: unknown key 'title'"),
        ([{"kind": "xy", "series": [{"var": "x"}]}], "plots[0].kind: 'xy'"),
        ([{"kind": "time"}], "plots[0].series: must be a non-empty list"),
        ([{"series": []}], "plots[0].series: must be a non-empty list"),
        ([{"series": ["x"]}], "plots[0].series[0]: must be an object"),
        ([{"series": [{"var": ""}]}], "plots[0].series[0].var: must be a non-empty string"),
        ([{"series": [{"var": 3}]}], "plots[0].series[0].var: must be a non-empty string"),
        ([{"series": [{"var": "x", "expr": "x-1"}]}], "plots[0].series[0]: unknown key 'expr'"),
        ([{"series": [{"var": "pipe.T[*]"}]}], "plots[0].series[0].var: 'pipe.T[*]' is a pattern"),
        ([{"series": [{"var": "x"}, {"var": "x"}]}], "plots[0].series[1].var: 'x' is already on this plot"),
    ],
)
def test_each_refusal_names_where_and_what(raw, needle):
    with pytest.raises(PlotsSpecError) as e:
        parse_plots(raw)
    assert needle in str(e.value)


def test_a_variable_may_be_on_two_plots():
    raw = [{"series": [{"var": "x"}]}, {"series": [{"var": "x"}, {"var": "y"}]}]
    assert len(parse_plots(raw)) == 2


def test_default_is_one_plot_per_tracked_variable_in_order():
    assert default_plots(["b", "a"]) == (
        PlotSpec(series=(SeriesSpec("b"),)),
        PlotSpec(series=(SeriesSpec("a"),)),
    )


def test_resolved_plots_absent_key_follows_the_tracked_list():
    test = make_test_model("Lib.T")
    assert test.plots is None
    assert resolved_plots(test, ["x", "y"], available=["x", "y", "z"]) == (
        ResolvedPlot(index=0, kind="time", series=("x",), missing=()),
        ResolvedPlot(index=1, kind="time", series=("y",), missing=()),
    )


def test_resolved_plots_names_a_series_the_result_lacks():
    test = make_test_model("Lib.T")
    test.plots = parse_plots([{"series": [{"var": "x"}, {"var": "gone"}]}])
    assert resolved_plots(test, ["x"], available=["x", "z"]) == (
        ResolvedPlot(index=0, kind="time", series=("x",), missing=("gone",)),
    )


def test_resolved_plots_with_no_name_list_trusts_the_layout():
    """`available=None`: the backend could not list the file (decision 6)."""
    test = make_test_model("Lib.T")
    test.plots = parse_plots([{"series": [{"var": "x"}, {"var": "z"}]}])
    assert resolved_plots(test, ["x"], available=None)[0].series == ("x", "z")


def _doc(plots):
    return {"tests": [{"model": "Lib.T", "variables": ["x"], "plots": plots}]}


def test_the_spec_parser_carries_plots(tmp_path):
    (test,) = spec_models_from_document(_doc(TWO), base_dir=tmp_path, label="mem")
    assert test.plots == parse_plots(TWO)


def test_the_spec_parser_leaves_plots_none_when_absent(tmp_path):
    doc = {"tests": [{"model": "Lib.T", "variables": ["x"]}]}
    (test,) = spec_models_from_document(doc, base_dir=tmp_path, label="mem")
    assert test.plots is None


def test_a_bad_plots_key_is_a_config_error_naming_the_document(tmp_path):
    with pytest.raises(ConfigError) as e:
        spec_models_from_document(
            _doc([{"series": []}]), base_dir=tmp_path, label="spec.json"
        )
    assert "spec.json" in str(e.value)
    assert "tests[Lib.T].plots[0].series" in str(e.value)


def test_merge_carries_the_specs_plots_onto_a_scanned_test(tmp_path):
    scanned = {"Lib.T": make_test_model("Lib.T")}
    spec = spec_models_from_document(_doc(TWO), base_dir=tmp_path, label="mem")
    (merged,) = merge_tests(scanned, spec_tests_by_model(spec))
    assert merged.plots == parse_plots(TWO)


def test_the_example_specs_parse(tmp_path):
    """No bundled example regressed: every entry's plots (none today) parse."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "examples"
    specs = sorted(root.rglob("test_spec.json"))
    assert specs, "the examples moved; point this test at them"
    for spec in specs:
        data = json.loads(spec.read_text(encoding="utf-8"))
        spec_models_from_document(data, base_dir=spec.parent, label=str(spec))
```

Append to `tests/test_patch_apply.py`:

```python
def test_plots_is_a_whitelisted_subtree():
    from dstf.discovery.patch_apply import apply_patch_to_document

    data = {"tests": [{"model": "M", "variables": ["x"]}]}
    layout = [{"kind": "time", "series": [{"var": "x"}]}]
    apply_patch_to_document(data, "M", [{"op": "add", "path": "/plots", "value": layout}])
    apply_patch_to_document(
        data, "M", [{"op": "add", "path": "/plots/0/series/-", "value": {"var": "y"}}]
    )
    assert data["tests"][0]["plots"] == [
        {"kind": "time", "series": [{"var": "x"}, {"var": "y"}]}
    ]


def test_variables_is_still_outside_the_whitelist(tmp_path):
    """A guard (passes before the change): through BOTH entry points."""
    from dstf.discovery.patch_apply import (
        DEFAULT_WHITELIST,
        PatchError,
        apply_patch,
        apply_patch_to_document,
    )

    assert DEFAULT_WHITELIST == ("/comparison", "/metrics", "/plots")
    op = [{"op": "add", "path": "/variables/-", "value": "y"}]
    data = {"tests": [{"model": "M", "variables": ["x"]}]}
    with pytest.raises(PatchError, match="whitelist"):
        apply_patch_to_document(data, "M", op)
    spec = tmp_path / "test_spec.json"
    spec.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(PatchError, match="whitelist"):
        apply_patch(spec, "M", op)
```

(`import json` at the top of the module if it is not there.)

(If `tests/test_patch_apply.py` does not already `import pytest`, add it at the top.)

Append to `tests/test_spec_update.py` (its `_config(tmp_path)` and `_spec(tmp_path, *entries)` helpers and its `PatchError` import are at the top of the module; import `apply_validated_patches` from `dstf.discovery.spec_update` if the module's import list lacks it):

```python
def test_a_patch_that_leaves_plots_invalid_writes_nothing(tmp_path):
    config = _config(tmp_path)
    spec_path = _spec(tmp_path, {"model": "M", "variables": ["x"]})
    before = spec_path.read_bytes()
    bad = [{"op": "add", "path": "/plots", "value": [{"series": []}]}]
    with pytest.raises(PatchError, match=r"plots\[0\]\.series"):
        apply_validated_patches(spec_path, [("M", bad)], config)
    assert spec_path.read_bytes() == before


def test_a_valid_plots_patch_is_written(tmp_path):
    config = _config(tmp_path)
    spec_path = _spec(tmp_path, {"model": "M", "variables": ["x"]})
    layout = [{"kind": "time", "series": [{"var": "x"}, {"var": "z"}]}]
    apply_validated_patches(
        spec_path, [("M", [{"op": "add", "path": "/plots", "value": layout}])], config
    )
    assert json.loads(spec_path.read_text())["tests"][0]["plots"] == layout
```

Append to `tests/test_export_schema.py`:

```python
def _entry_schema() -> dict:
    """The schema of one `tests[]` entry, inline or behind a `$ref`."""
    from dstf.reporting.schema_export import build_schema

    schema = build_schema()
    entry = schema["properties"]["tests"]["items"]
    if "$ref" in entry:
        entry = schema["$defs"][entry["$ref"].rsplit("/", 1)[-1]]
    return entry


def test_the_entry_schema_describes_plots():
    plots = _entry_schema()["properties"]["plots"]
    assert plots["type"] == "array"
    plot = plots["items"]
    assert plot["required"] == ["series"]
    assert plot["additionalProperties"] is False
    assert plot["properties"]["kind"] == {"enum": ["time"]}
    series = plot["properties"]["series"]
    assert series["minItems"] == 1
    assert series["items"]["required"] == ["var"]
    assert series["items"]["additionalProperties"] is False
```

- [ ] **Step 2: Run them red.** `uv run --no-sync pytest -q tests/test_plots_spec.py` → collection error, `ModuleNotFoundError: No module named 'dstf.discovery.plots'`. `uv run --no-sync pytest -q tests/test_patch_apply.py -k plots` → `PatchError: ... path not in whitelist ('/comparison', '/metrics')`. `tests/test_spec_update.py -k plots` → the same whitelist error, which does NOT match `plots\[0\]\.series` → FAIL. `tests/test_export_schema.py -k plots` → `KeyError: 'plots'`.

- [ ] **Step 3: Create `src/dstf/discovery/plots.py`.**

```python
"""The optional ``plots`` key of a ``test_spec.json`` entry (brief §3.10).

A test's plot layout: a list of plots, each a ``kind`` and a list of series,
each a variable of the result. Read by both tiers, ignored by scoring. Absent
means one plot per tracked variable; ``[]`` means no plots. ``kind`` is
reserved for later plot kinds and a series for an expression and a unit
label; until they exist the parser refuses what it does not know, so a typo
is loud. No I/O here: the spec parser, the patch validator, the Session and
the report all call in.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..backends.types import is_glob_pattern

if TYPE_CHECKING:
    from .test_registry import TestModel

PLOT_KINDS = ("time",)


class PlotsSpecError(ValueError):
    """A ``plots`` value the parser refuses; the message starts with where."""


@dataclass(frozen=True)
class SeriesSpec:
    var: str


@dataclass(frozen=True)
class PlotSpec:
    series: tuple[SeriesSpec, ...]
    kind: str = "time"


@dataclass(frozen=True)
class ResolvedPlot:
    """One plot as a tier draws it: the series the result has, in order, and
    the ones it lacks (decision 3)."""

    index: int
    kind: str
    series: tuple[str, ...]
    missing: tuple[str, ...]


def _unknown(obj: dict, known: set[str], at: str) -> None:
    for key in obj:
        if key not in known:
            raise PlotsSpecError(f"{at}: unknown key {key!r}")


def parse_plots(raw: object, *, path: str = "plots") -> tuple[PlotSpec, ...]:
    if not isinstance(raw, list):
        raise PlotsSpecError(f"{path}: must be a list of plots")
    plots: list[PlotSpec] = []
    for i, plot in enumerate(raw):
        at = f"{path}[{i}]"
        if not isinstance(plot, dict):
            raise PlotsSpecError(f"{at}: must be an object")
        _unknown(plot, {"kind", "series"}, at)
        kind = plot.get("kind", "time")
        if kind not in PLOT_KINDS:
            raise PlotsSpecError(
                f"{at}.kind: {kind!r} is not a plot kind (known: {', '.join(PLOT_KINDS)})"
            )
        series_raw = plot.get("series")
        if not isinstance(series_raw, list) or not series_raw:
            raise PlotsSpecError(f"{at}.series: must be a non-empty list")
        series: list[SeriesSpec] = []
        for j, one in enumerate(series_raw):
            s_at = f"{at}.series[{j}]"
            if not isinstance(one, dict):
                raise PlotsSpecError(f"{s_at}: must be an object")
            _unknown(one, {"var"}, s_at)
            var = one.get("var")
            if not isinstance(var, str) or not var:
                raise PlotsSpecError(f"{s_at}.var: must be a non-empty string")
            if is_glob_pattern(var):
                raise PlotsSpecError(
                    f"{s_at}.var: {var!r} is a pattern; a series names one variable"
                )
            if any(s.var == var for s in series):
                raise PlotsSpecError(f"{s_at}.var: {var!r} is already on this plot")
            series.append(SeriesSpec(var))
        plots.append(PlotSpec(series=tuple(series), kind=kind))
    return tuple(plots)


def plots_to_raw(plots: Sequence[PlotSpec]) -> list[dict]:
    """The JSON a writer stores: ``kind`` always written."""
    return [
        {"kind": p.kind, "series": [{"var": s.var} for s in p.series]} for p in plots
    ]


def default_plots(tracked: Sequence[str]) -> tuple[PlotSpec, ...]:
    return tuple(PlotSpec(series=(SeriesSpec(v),)) for v in tracked)


def resolved_plots(
    test: TestModel, tracked: Sequence[str], *, available: Sequence[str] | None
) -> tuple[ResolvedPlot, ...]:
    """The plots both tiers draw for ``test``: its ``plots`` key, else one per
    name in ``tracked`` (the variables the comparison scored or the result
    resolved, in the page's order). ``available`` is every name in the result
    file, or ``None`` when the backend cannot list it — then nothing is
    called missing."""
    plots = test.plots if test.plots is not None else default_plots(tracked)
    have = None if available is None else set(available) | set(tracked)
    out: list[ResolvedPlot] = []
    for i, plot in enumerate(plots):
        names = [s.var for s in plot.series]
        out.append(
            ResolvedPlot(
                index=i,
                kind=plot.kind,
                series=tuple(n for n in names if have is None or n in have),
                missing=tuple(n for n in names if have is not None and n not in have),
            )
        )
    return tuple(out)
```

- [ ] **Step 4: Carry it.** In `test_registry.py`, in `TestModel`, directly after the `metric_tree_spec` field:

```python
    # The entry's optional ``plots`` layout (brief §3.10): None when the key
    # is absent — both tiers then draw one plot per tracked variable.
    plots: tuple["PlotSpec", ...] | None = None
```

(the `X | None` spelling: ruff's UP045 refuses a new `Optional`) with `from .plots import PlotSpec` under the module's existing `if TYPE_CHECKING:` block (add the block if the module has none). In `merge_tests`, directly after the `metric_tree_spec` line of the `if model_id in merged:` branch:

```python
            if spec_test.plots is not None:
                existing.plots = spec_test.plots
```

In `spec_parser.py`, directly after the `metrics` block and before `tests.append(test)`:

```python
        plots_raw = entry.get("plots")
        if plots_raw is not None:
            from .plots import PlotsSpecError, parse_plots

            try:
                test.plots = parse_plots(plots_raw, path=f"tests[{model_id}].plots")
            except PlotsSpecError as e:
                raise ConfigError(f"{label}: {e}") from e
```

- [ ] **Step 5: Patchable and validated.** In `patch_apply.py`, add a module constant `DEFAULT_WHITELIST: tuple[str, ...] = ("/comparison", "/metrics", "/plots")` and make it the default of BOTH `whitelist=` parameters (`apply_patch_to_document` and `apply_patch`) — one constant, so the two cannot drift; the module docstring's "(so `/comparison/...` and `/metrics/...`" gains `/plots/...`. In `spec_update.py` add, after `validate_entry_metric_tree`:

```python
def validate_entry_plots(data: dict, model_id: str) -> None:
    """The patched entry's ``plots`` key, when it has one, parses."""
    from .plots import PlotsSpecError, parse_plots

    entry = spec_entry(data, model_id)
    if entry is None or "plots" not in entry:
        return
    try:
        parse_plots(entry["plots"], path=f"tests[{model_id}].plots")
    except PlotsSpecError as e:
        raise PatchError(f"patched plots are invalid: {e}") from e
```

and in `apply_validated_patches`, directly after the `validate_entry_metric_tree(...)` call: `validate_entry_plots(data, model_id)`.

- [ ] **Step 6: The schema.** In `schema_export.py`, in the entry's `properties`, directly after `"metrics": {...}`:

```python
            "plots": {
                "type": "array",
                "description": (
                    "The test's plot layout, read by the report and the authoring "
                    "tool and ignored by scoring. Absent: one plot per tracked variable."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"enum": ["time"]},
                        "series": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "properties": {"var": {"type": "string", "minLength": 1}},
                                "required": ["var"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["series"],
                    "additionalProperties": False,
                },
            },
```

- [ ] **Step 6b: One key lookup.** `src/dstf/pipeline.py` builds `model_to_key` from `batch_manifest.json` by hand (the block that loads `BatchManifest` and maps `entry["model_id"]` to its key). Task 2 adds `last_test_keys(work_dir)` for exactly this; when Task 2 lands, that block becomes `model_to_key = last_test_keys(work_dir)` — done in TASK 2's commit, named here so Task 1's reviewer does not look for it.

- [ ] **Step 7: Green, gates, commit.** Run `ruff format` on the new and touched files before the format gate (several of the test lines above are longer than the formatter keeps). The four test files pass; the Conventions' gates pass. Commit `plots: the optional per-test layout key — parsed, merged, patchable, in the schema`.

**Named mutations (each must turn a test red):** M1.1 drop the `is_glob_pattern` refusal. M1.2 drop the duplicate-var refusal. M1.3 `plots_to_raw` omits `kind`. M1.4 `merge_tests` does not copy `plots`. M1.5 the spec parser swallows `PlotsSpecError` (logs and continues). M1.6 `apply_validated_patches` does not call `validate_entry_plots`. M1.7 the whitelist becomes `("/comparison", "/metrics", "/plots", "/variables")`. M1.8 `resolved_plots` reports a TRACKED name as missing when `available` lacks it (drop `| set(tracked)`) — killed by this test, which is part of Step 1 (unconditional):

```python
def test_a_tracked_name_is_never_missing_even_if_the_file_list_lacks_it():
    """unitTests.x[i] style names are tracked by index and may be absent
    from a name list that was filtered."""
    test = make_test_model("Lib.T")
    assert resolved_plots(test, ["x"], available=[])[0].series == ("x",)
```

**Done when:** `parse_plots(plots_to_raw(p)) == p` for the plots of the tests above; a spec with `plots` loads through `spec_models_from_document` and `merge_tests` with `TestModel.plots` set; a `/plots` op applies and an invalid result of one writes nothing; `export-schema`'s entry describes `plots`; all gates green.

---

### Task 2: A result file, listed and read by its backend class

**Files:**
- Create: `src/dstf/backends/common/result_files.py`, `tests/test_result_files.py`
- Modify: `src/dstf/backends/base.py` (the `Backend` class, beside `read_result`; a module-level `last_test_keys`), `src/dstf/backends/dymola/backend.py`, `src/dstf/backends/openmodelica/backend.py`, `src/dstf/backends/fmpy/backend.py`, `src/dstf/backends/common/json_driver.py`

**What is true today (2026-09-20 survey):** every backend writes `work_dir/<test_key>/<file>`: Dymola `dsres.mat` (ALL model variables), OpenModelica `result_res.mat` (the class attribute `RESULT_MAT_FILENAME`; only the variable filter's names), FMPy `result.npz` (the class attribute `RESULT_FILENAME`; a structured array, one column per recorded output plus `time`), Julia and Python `result.json` (`JsonDriverBackend.result_filename`; `{"time": [...], "variables": {name: [...]}}`, everything the driver materialized). The persistent backends inherit their batch class. `mat_reader.list_result_mat_variables(path) -> list[str] | None` and `read_result_mat(path, variable_names) -> {name: (time, values)} | None` exist.

- [ ] **Step 1: Write the failing tests.** Create `tests/test_result_files.py`:

```python
"""Listing and reading ANY variable of a test's result file, through the
backend class (the report never instantiates a backend)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from dstf.backends.base import Backend, last_test_keys
from dstf.backends.common.json_driver import JsonDriverBackend
from dstf.backends.common.result_files import (
    json_names,
    json_series,
    npz_names,
    npz_series,
)
from dstf.backends.python.backend import PythonBackend

T = [0.0, 0.5, 1.0]


def _json_result(test_dir: Path, **variables) -> None:
    test_dir.mkdir(parents=True, exist_ok=True)
    (test_dir / "result.json").write_text(
        json.dumps({"success": True, "time": T, "variables": variables}),
        encoding="utf-8",
    )


def test_json_names_lists_every_variable_in_file_order(tmp_path):
    _json_result(tmp_path, x=[0, 1, 2], z=[5, 5, 5])
    assert json_names(tmp_path / "result.json") == ["x", "z"]


def test_json_series_reads_the_named_ones_on_the_shared_time(tmp_path):
    _json_result(tmp_path, x=[0, 1, 2], z=[5, 5, 5])
    got = json_series(tmp_path / "result.json", ["z", "absent"])
    assert list(got) == ["z"]
    time, values = got["z"]
    assert time.tolist() == T and values.tolist() == [5.0, 5.0, 5.0]


def test_a_missing_or_unreadable_json_is_none_and_empty(tmp_path):
    assert json_names(tmp_path / "result.json") is None
    assert json_series(tmp_path / "result.json", ["x"]) == {}
    (tmp_path / "result.json").write_text("{not json", encoding="utf-8")
    assert json_names(tmp_path / "result.json") is None
    assert json_series(tmp_path / "result.json", ["x"]) == {}


def test_a_series_whose_length_disagrees_with_time_is_left_out(tmp_path):
    _json_result(tmp_path, x=[0, 1, 2], short=[1, 2])
    assert list(json_series(tmp_path / "result.json", ["x", "short"])) == ["x"]


def _npz(path: Path) -> None:
    arr = np.zeros(3, dtype=[("time", "f8"), ("h", "f8"), ("v", "f8")])
    arr["time"], arr["h"], arr["v"] = T, [1, 2, 3], [9, 8, 7]
    np.savez_compressed(path, result=arr)


def test_npz_names_leaves_time_out(tmp_path):
    _npz(tmp_path / "result.npz")
    assert npz_names(tmp_path / "result.npz") == ["h", "v"]


def test_npz_series_reads_the_named_columns(tmp_path):
    _npz(tmp_path / "result.npz")
    got = npz_series(tmp_path / "result.npz", ["v", "absent"])
    assert list(got) == ["v"]
    assert got["v"][0].tolist() == T and got["v"][1].tolist() == [9.0, 8.0, 7.0]
    assert npz_series(tmp_path / "result.npz", ["time"]) == {}


def test_a_missing_npz_is_none_and_empty(tmp_path):
    assert npz_names(tmp_path / "result.npz") is None
    assert npz_series(tmp_path / "result.npz", ["h"]) == {}


def test_the_base_backend_keeps_no_readable_file(tmp_path):
    assert Backend.result_names(tmp_path) is None
    assert Backend.result_series(tmp_path, ["x"]) == {}


def test_the_json_driver_backends_read_their_result_json(tmp_path):
    _json_result(tmp_path, x=[0, 1, 2], z=[5, 5, 5])
    assert PythonBackend.result_names(tmp_path) == ["x", "z"]
    assert list(PythonBackend.result_series(tmp_path, ["z"])) == ["z"]
    assert JsonDriverBackend.result_names.__func__ is PythonBackend.result_names.__func__


def test_fmpy_reads_its_npz(tmp_path):
    pytest.importorskip("fmpy")
    from dstf.backends.fmpy.backend import FmpyBackend

    _npz(tmp_path / FmpyBackend.RESULT_FILENAME)
    assert FmpyBackend.result_names(tmp_path) == ["h", "v"]
    assert FmpyBackend.result_series(tmp_path, ["h"])["h"][1].tolist() == [1.0, 2.0, 3.0]


@pytest.mark.parametrize(
    ("module", "cls", "filename"),
    [
        ("dstf.backends.dymola.backend", "DymolaBackend", "dsres.mat"),
        ("dstf.backends.openmodelica.backend", "OpenModelicaBackend", "result_res.mat"),
    ],
)
def test_the_mat_backends_name_their_file_and_use_the_mat_pair(
    module, cls, filename, tmp_path, monkeypatch
):
    import importlib

    from dstf.backends.common import result_files

    backend = getattr(importlib.import_module(module), cls)
    seen: list[tuple[str, Path]] = []
    monkeypatch.setattr(
        result_files, "mat_names", lambda p: seen.append(("names", p)) or ["a"]
    )
    monkeypatch.setattr(
        result_files,
        "mat_series",
        lambda p, names: seen.append(("series", p)) or {},
    )
    assert backend.result_names(tmp_path) == ["a"]
    assert backend.result_series(tmp_path, ["a"]) == {}
    assert seen == [("names", tmp_path / filename), ("series", tmp_path / filename)]


def test_the_persistent_backends_inherit_the_readers():
    from dstf.backends.dymola.backend import DymolaBackend
    from dstf.backends.dymola.persistent_backend import PersistentDymolaBackend

    assert (
        PersistentDymolaBackend.result_names.__func__
        is DymolaBackend.result_names.__func__
    )


def test_mat_series_reads_through_the_mat_reader(tmp_path, monkeypatch):
    from dstf.backends.common import result_files

    calls = []

    def fake_read(path, variable_names=None):
        calls.append((path, variable_names))
        t = np.asarray(T)
        return {"a": (t, t * 2), "time": (t, t)}

    monkeypatch.setattr(result_files, "read_result_mat", fake_read)
    (tmp_path / "r.mat").write_bytes(b"")
    got = result_files.mat_series(tmp_path / "r.mat", ["a", "b"])
    assert calls == [(tmp_path / "r.mat", {"a", "b"})]
    assert list(got) == ["a"] and got["a"][1].tolist() == [0.0, 1.0, 2.0]


def test_mat_names_leaves_time_out_in_either_case(tmp_path, monkeypatch):
    from dstf.backends.common import result_files

    monkeypatch.setattr(
        result_files, "list_result_mat_variables", lambda p: ["Time", "a", "time"]
    )
    (tmp_path / "r.mat").write_bytes(b"")
    assert result_files.mat_names(tmp_path / "r.mat") == ["a"]


def test_mat_pair_on_a_missing_file(tmp_path):
    from dstf.backends.common import result_files

    assert result_files.mat_names(tmp_path / "r.mat") is None
    assert result_files.mat_series(tmp_path / "r.mat", ["a"]) == {}


def test_last_test_keys_maps_model_to_key(tmp_path):
    (tmp_path / "batch_manifest.json").write_text(
        json.dumps(
            {
                "test_0001": {"model_id": "L.A", "ref_id": None},
                "test_0007": {"model_id": "L.B", "ref_id": None},
            }
        ),
        encoding="utf-8",
    )
    assert last_test_keys(tmp_path) == {"L.A": "test_0001", "L.B": "test_0007"}
    assert last_test_keys(tmp_path / "nowhere") == {}
```

If `BatchManifest.load` needs more than the two keys per entry to load that manifest, give the fixture what `tests/test_session.py::_project` gives its manifest (`"last_run_status": "passed"`).

- [ ] **Step 2: Run red.** `uv run --no-sync pytest -q tests/test_result_files.py` → collection error: `ImportError: cannot import name 'last_test_keys' from 'dstf.backends.base'`.

- [ ] **Step 3: Create `src/dstf/backends/common/result_files.py`.**

```python
"""Any variable of a result file, by name — the three on-disk formats the
bundled backends write, each as a pair: the names in the file (``None`` when
there is no readable file) and the named series as ``{name: (time, values)}``
(a name the file lacks is left out). A backend class points its
``result_names`` / ``result_series`` at one pair. The tracked-variable read
(``read_result``) stays where it is: it resolves patterns, indexes and
diagnostics; this reads what a plot asks for."""

from __future__ import annotations

import json
from collections.abc import Collection
from pathlib import Path

import numpy as np

from .mat_reader import list_result_mat_variables, read_result_mat

Series = dict[str, tuple[np.ndarray, np.ndarray]]


def mat_names(path: Path) -> list[str] | None:
    if not path.exists():
        return None
    names = list_result_mat_variables(path)
    return None if names is None else [n for n in names if n.lower() != "time"]


def mat_series(path: Path, names: Collection[str]) -> Series:
    if not path.exists() or not names:
        return {}
    try:
        data = read_result_mat(path, variable_names=set(names))
    except Exception:  # a partial or foreign .mat: nothing to draw
        return {}
    return {n: data[n] for n in names if data and n in data}


def _json_payload(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("variables"), dict):
        return None
    return payload


def json_names(path: Path) -> list[str] | None:
    payload = _json_payload(path)
    return None if payload is None else list(payload["variables"])


def json_series(path: Path, names: Collection[str]) -> Series:
    payload = _json_payload(path)
    if payload is None:
        return {}
    time = np.asarray(payload.get("time") or [], dtype=float)
    out: Series = {}
    for n in names:
        values = payload["variables"].get(n)
        if isinstance(values, list) and len(values) == len(time):
            out[n] = (time, np.asarray(values, dtype=float))
    return out


def _npz_array(path: Path) -> np.ndarray | None:
    try:
        with np.load(path, allow_pickle=False) as npz:
            return npz["result"]
    except (OSError, KeyError, ValueError):
        return None


def npz_names(path: Path) -> list[str] | None:
    arr = _npz_array(path)
    if arr is None or arr.dtype.names is None:
        return None
    return [n for n in arr.dtype.names if n != "time"]


def npz_series(path: Path, names: Collection[str]) -> Series:
    arr = _npz_array(path)
    if arr is None or arr.dtype.names is None or "time" not in arr.dtype.names:
        return {}
    time = np.asarray(arr["time"], dtype=float)
    return {
        n: (time, np.asarray(arr[n], dtype=float))
        for n in names
        if n in arr.dtype.names and n != "time"
    }
```

The mat pair is called through the MODULE by the backends (`result_files.mat_names(...)`, not a `from` import) so the test's monkeypatch reaches it.

- [ ] **Step 4: The base class and `last_test_keys`.** In `base.py`, in `Backend`, directly after the abstract `read_result`:

```python
    @classmethod
    def result_names(cls, test_dir: Path) -> list[str] | None:
        """Every variable name in the result file under ``test_dir``
        (``work_dir/<test_key>``), tracked or not; ``None`` when this backend
        keeps no file it can list. A classmethod: the report reads a test's
        files without instantiating a backend."""
        return None

    @classmethod
    def result_series(
        cls, test_dir: Path, names: Collection[str]
    ) -> dict[str, tuple[np.ndarray, np.ndarray]]:
        """``{name: (time, values)}`` for the ``names`` the file has."""
        return {}
```

(`base.py` has NO `from __future__ import annotations` and no numpy import: add `import numpy as np` at runtime — `backends/types.py` already imports it, so this costs nothing — and `from collections.abc import Collection`.) At module level, after `assign_test_keys`:

```python
def last_test_keys(work_dir: Path) -> dict[str, str]:
    """``{model_id: test_key}`` from the work dir's manifest; ``{}`` when
    there is none."""
    manifest_path = work_dir / "batch_manifest.json"
    if not manifest_path.exists():
        return {}
    manifest = BatchManifest.load(manifest_path).manifest
    return {entry["model_id"]: key for key, entry in manifest.items()}
```

- [ ] **Step 5: The four backend classes.** Each gets the two classmethods beside its `read_result`. Dymola (`dymola/backend.py`):

```python
    @classmethod
    def result_names(cls, test_dir: Path) -> list[str] | None:
        return result_files.mat_names(test_dir / "dsres.mat")

    @classmethod
    def result_series(cls, test_dir, names):
        return result_files.mat_series(test_dir / "dsres.mat", names)
```

with `from ..common import result_files`. OpenModelica: the same over `test_dir / cls.RESULT_MAT_FILENAME` (a CLASS attribute; `openmodelica/backend.py` has no `Path` import — add `from pathlib import Path`). FMPy: `result_files.npz_names(test_dir / cls.RESULT_FILENAME)` / `npz_series` (also a class attribute). `JsonDriverBackend`: `result_files.json_names(test_dir / cls.result_filename)` / `json_series`. Annotate `result_series` exactly as the base does (`backends/` is outside `[tool.mypy] files`, so nothing checks the override but the reader). In the same commit, `src/dstf/pipeline.py`'s hand-built `model_to_key` block becomes `model_to_key = last_test_keys(work_dir)` (Task 1, Step 6b).

- [ ] **Step 6: Green, gates, commit** `backends: any variable of a result file, listed and read through the backend class`.

**Named mutations:** M2.1 `json_series` does not check the length against `time`. M2.2 `mat_names` keeps `time`. M2.3 `npz_series` returns the `time` column when asked for `"time"`. M2.4 the OpenModelica class reads `dsres.mat`. M2.5 `last_test_keys` returns `{key: model_id}`. M2.6 `mat_series` passes `variable_names=None` (loads the whole file).

**Done when:** for a test dir of each bundled format, the backend CLASS lists the file's names and reads a named untracked series; the base class answers `None` / `{}`; gates green.

---

### Task 3: Families, the ramp, and `plot_figure`

**Files:**
- Create: `src/dstf/figures/families.py`, `tests/test_plot_figure.py`
- Modify: `src/dstf/figures/plotly.py` (after `variable_figure`), `src/dstf/figures/__init__.py` (the docstring names `plot_figure`)

- [ ] **Step 1: Write the failing tests.** Create `tests/test_plot_figure.py`:

```python
"""A plot of several series (DESIGN.md "Plots are modular", "Array families
on a plot"): families by the last integer index, one hue per family ramped
by index, a folded legend — and one series is `variable_figure`, unchanged."""

from __future__ import annotations

import numpy as np
import pytest

from dstf.figures.families import HUES, family_of, group_families, ramp, slice_text
from dstf.figures.plotly import plot_figure, variable_figure
from dstf.figures.view import build_view

T = np.linspace(0.0, 1.0, 5)


def _view(name, scale=1.0, *, ref=False):
    return build_view(
        name,
        act_time=T,
        act_values=T * scale,
        ref_time=T if ref else None,
        ref_values=T if ref else None,
    )


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("pipe.T[3]", ("pipe.T[]", 3)),
        ("pipe.medium[12].T", ("pipe.medium[].T", 12)),
        ("a[2].b[7]", ("a[2].b[]", 7)),
        ("x", None),
        ("x[2,3]", None),
        ("der(pipe.T[3])", ("der(pipe.T[])", 3)),
        ("x[i]", None),
    ],
)
def test_family_of_is_the_last_integer_index(name, expected):
    assert family_of(name) == expected


def test_group_families_keeps_first_appearance_order_and_sorts_members():
    names = ["pipe.T[11]", "m", "pipe.T[1]", "pipe.p[2]", "pipe.T[6]"]
    assert group_families(names) == [
        ("pipe.T[]", ["pipe.T[1]", "pipe.T[6]", "pipe.T[11]"]),
        ("m", ["m"]),
        ("pipe.p[]", ["pipe.p[2]"]),
    ]


@pytest.mark.parametrize(
    ("indices", "text"),
    [
        ([1, 6, 11, 16], "1:5:16"),
        ([1, 2, 3, 4], "1:4"),
        ([3], "3"),
        ([1, 2, 4], "1,2,4"),
        ([2, 1], "1:2"),
    ],
)
def test_slice_text_is_modelica_slice_syntax_or_a_list(indices, text):
    assert slice_text(indices) == text


def test_ramp_runs_low_to_high_through_the_hues_four_stops():
    blue = HUES[0]
    assert ramp(blue, 4) == list(blue)
    assert ramp(blue, 1) == [blue[2]]
    two = ramp(blue, 2)
    assert two == [blue[0], blue[3]]
    seven = ramp(blue, 7)
    assert len(seven) == 7 and seven[0] == blue[0] and seven[-1] == blue[3]
    assert seven[3] == _mid(blue[1], blue[2])


def _mid(a: str, b: str) -> str:
    pa = [int(a[i : i + 2], 16) for i in (1, 3, 5)]
    pb = [int(b[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round((x + y) / 2):02x}" for x, y in zip(pa, pb))


def test_the_accent_hue_is_designs_ramp():
    assert HUES[0] == ("#90caf9", "#42a5f5", "#1976d2", "#0d47a1")


def test_one_view_is_variable_figure_unchanged():
    view = _view("x", ref=True)
    assert plot_figure([view], 100, editable=True) == variable_figure(
        view, 100, editable=True
    )


def test_no_views_is_an_empty_titled_figure():
    fig = plot_figure([])
    assert fig["data"] == []
    assert fig["layout"]["xaxis"]["title"]["text"] == "Time"


def _acts(fig):
    return [tr for tr in fig["data"] if tr["meta"]["kind"] == "act"]


def test_a_family_shares_a_hue_ramped_by_index_with_one_legend_entry():
    views = [_view(f"pipe.T[{i}]", i) for i in (11, 1, 6, 16)]
    fig = plot_figure(views)
    acts = _acts(fig)
    assert [tr["meta"]["id"] for tr in acts] == [
        "actual/pipe.T[1]",
        "actual/pipe.T[6]",
        "actual/pipe.T[11]",
        "actual/pipe.T[16]",
    ]
    assert [tr["line"]["color"] for tr in acts] == list(HUES[0])
    assert {tr["legendgroup"] for tr in acts} == {"pipe.T[]"}
    assert [tr["showlegend"] for tr in acts] == [True, False, False, False]
    assert acts[0]["name"] == "pipe.T[1:5:16]"
    assert acts[2]["hovertemplate"] == "pipe.T[11]: %{y:.4g}<extra></extra>"


def test_a_second_family_and_a_scalar_take_the_next_hues():
    views = [_view("pipe.T[1]"), _view("pipe.T[2]"), _view("m"), _view("pipe.p[1]")]
    colors = {tr["meta"]["id"]: tr["line"]["color"] for tr in _acts(plot_figure(views))}
    assert colors["actual/pipe.T[1]"] == HUES[0][0]
    assert colors["actual/pipe.T[2]"] == HUES[0][3]
    assert colors["actual/m"] == HUES[1][2]
    assert colors["actual/pipe.p[1]"] == HUES[2][2]


def test_hues_cycle_past_the_palette():
    views = [_view(n) for n in ("a", "b", "c", "d")]
    colors = [tr["line"]["color"] for tr in _acts(plot_figure(views))]
    assert colors[3] == colors[0] == HUES[0][2]


def test_a_reference_is_the_series_colour_dashed_and_out_of_the_legend():
    fig = plot_figure([_view("x", ref=True), _view("y", 2.0, ref=True)])
    refs = [tr for tr in fig["data"] if tr["meta"]["kind"] == "ref"]
    assert [tr["meta"]["id"] for tr in refs] == ["reference/x", "reference/y"]
    assert [tr["line"]["color"] for tr in refs] == [HUES[0][2], HUES[1][2]]
    assert all(tr["line"]["dash"] == "dash" and not tr["showlegend"] for tr in refs)
    assert refs[0]["legendgroup"] == "x"


def test_several_series_draw_no_overlays_and_no_error_traces():
    one = build_view(
        "x",
        act_time=T,
        act_values=T,
        ref_time=T,
        ref_values=T,
        overlays=[{"name": "om", "role": "soft_check", "time": T, "values": T}],
    )
    fig = plot_figure([one, _view("y")])
    kinds = {tr["meta"]["kind"] for tr in fig["data"]}
    assert kinds == {"act", "ref"}
    assert "yaxis3" not in fig["layout"]


def test_the_budget_decimates_every_series():
    t = np.linspace(0.0, 1.0, 500)
    views = [build_view(n, act_time=t, act_values=t) for n in ("x", "y")]
    fig = plot_figure(views, 50)
    for tr in _acts(fig):
        assert len(tr["x"]) <= 50
        assert tr["meta"]["full_samples"] == 500


def test_a_label_overrides_a_familys_legend_text():
    views = [_view("pipe.T[1]"), _view("pipe.T[2]")]
    fig = plot_figure(views, labels={"pipe.T[]": "pipe.T[1:2] · 2 of 20"})
    assert _acts(fig)[0]["name"] == "pipe.T[1:2] · 2 of 20"
```

Add one geometry test to the same file. `tests/test_figures.py::_tube_view(n=11, **cfg)` returns a `VariableView` named by that helper whose one leaf is a real tube comparison stamped at `/metrics/children/0`; with `tube_points` it carries draggable handle markers:

```python
def test_every_leaf_of_every_series_keeps_its_geometry_and_editable_rings():
    from test_figures import _tube_view

    x = _tube_view(
        tube_points=[
            {"time": 0.0, "upper": 0.05, "lower": 0.05},
            {"time": 1.0, "upper": 0.1, "lower": 0.1},
        ]
    )
    assert x.name != "y"
    single = variable_figure(x, editable=True)
    multi = plot_figure([x, _view("y")], editable=True)
    # Marker traces carry meta {"family", "kind": "handle"} and no "id".
    leaf_ids = lambda fig: sorted(  # noqa: E731
        tr["meta"].get("id") or tr["meta"]["family"]
        for tr in fig["data"]
        if tr["meta"]["kind"] not in ("act", "ref", "error", "overlay")
    )
    assert leaf_ids(multi) == leaf_ids(single) and leaf_ids(single)
    assert x.errors, "the fixture must carry error series, or the next line is vacuous"
    assert not any(tr["meta"]["kind"] == "error" for tr in multi["data"])
    names = [s.get("name") for s in single["layout"]["shapes"]]
    assert [s.get("name") for s in multi["layout"]["shapes"]] == names
    assert any(n and n.startswith("/metrics/children/0/") for n in names), (
        "the fixture must carry at least one handle ring, or this test is vacuous"
    )
    assert multi["config"]["edits"] == {"shapePosition": True}
    plain = plot_figure([x, _view("y")])
    assert "edits" not in plain["config"]
    assert not any(
        (s.get("name") or "").startswith("/metrics/") and s.get("xsizemode") == "pixel"
        for s in plain["layout"]["shapes"]
    )
```

- [ ] **Step 2: Run red.** → collection error: `ModuleNotFoundError: No module named 'dstf.figures.families'`.

- [ ] **Step 3: Create `src/dstf/figures/families.py`.**

```python
"""Array families (DESIGN.md "Array families on a plot"; brief §3.9).

Indexed names fold by their LAST integer index: ``pipe.T[3]`` and
``pipe.medium[3].T`` each belong to a family; ``x[2,3]`` does not. One
function, so the figure's legend, the Variables drawer's folded row and the
folded criteria row agree on what a family is."""

from __future__ import annotations

import re
from collections.abc import Sequence

_LAST_INDEX = re.compile(r"\[(\d+)\](?!.*\[\d+\])")

#: Four stops per hue, light to dark (Material 200 / 400 / 700 / 900). The
#: first is DESIGN.md's accent ramp; a second family on a plot takes the
#: next hue, and the list cycles.
HUES: tuple[tuple[str, str, str, str], ...] = (
    ("#90caf9", "#42a5f5", "#1976d2", "#0d47a1"),
    ("#a5d6a7", "#66bb6a", "#388e3c", "#1b5e20"),
    ("#ce93d8", "#ab47bc", "#7b1fa2", "#4a148c"),
)


def family_of(name: str) -> tuple[str, int] | None:
    """``(family key, index)`` — the name with its last ``[integer]`` emptied
    — or ``None`` for a name with no single-integer index."""
    if re.search(r"\[[^\]]*,[^\]]*\]", name):
        return None
    m = _LAST_INDEX.search(name)
    if m is None:
        return None
    return name[: m.start()] + "[]" + name[m.end() :], int(m.group(1))


def group_families(names: Sequence[str]) -> list[tuple[str, list[str]]]:
    """``(key, members)`` in order of first appearance; members by index. A
    name with no family is its own group under its own name."""
    groups: dict[str, list[tuple[int, str]]] = {}
    for name in names:
        fam = family_of(name)
        key, index = fam if fam is not None else (name, 0)
        groups.setdefault(key, []).append((index, name))
    return [(key, [n for _, n in sorted(members)]) for key, members in groups.items()]


def slice_text(indices: Sequence[int]) -> str:
    """Modelica slice syntax for an arithmetic run (``1:5:16``, ``1:4``), a
    comma list otherwise."""
    idx = sorted(indices)
    if len(idx) == 1:
        return str(idx[0])
    step = idx[1] - idx[0]
    if step > 0 and all(b - a == step for a, b in zip(idx, idx[1:])):
        return f"{idx[0]}:{idx[-1]}" if step == 1 else f"{idx[0]}:{step}:{idx[-1]}"
    return ",".join(str(i) for i in idx)


def family_label(key: str, members: Sequence[str]) -> str:
    """``pipe.T[1:5:16]`` for a family of several; the name itself for one."""
    if len(members) == 1:
        return members[0]
    indices = [fam[1] for m in members if (fam := family_of(m)) is not None]
    return key.replace("[]", f"[{slice_text(indices)}]", 1) if "[]" in key else key


def _rgb(hex_color: str) -> tuple[int, int, int]:
    return int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)


def ramp(hue: Sequence[str], n: int) -> list[str]:
    """``n`` colours along the hue's four stops, light to dark; one colour is
    the third stop (the hue's name shade)."""
    if n == 1:
        return [hue[2]]
    out = []
    for i in range(n):
        pos = i * (len(hue) - 1) / (n - 1)
        lo = min(int(pos), len(hue) - 2)
        frac = pos - lo
        a, b = _rgb(hue[lo]), _rgb(hue[lo + 1])
        out.append("#" + "".join(f"{round(x + (y - x) * frac):02x}" for x, y in zip(a, b)))
    return out
```

Note for `family_label`: a key like `a[2].b[]` contains one `[]`; `replace(..., 1)` fills the first `[]`, which is the emptied one because `[2]` is not `[]`.

- [ ] **Step 4: `plot_figure` in `plotly.py`, after `variable_figure`.**

```python
def plot_figure(
    views: Sequence[VariableView],
    max_samples: int | None = None,
    *,
    editable: bool = False,
    labels: Mapping[str, str] | None = None,
) -> dict:
    """The figure for one plot of a test (DESIGN.md "Plots are modular").

    One view is :func:`variable_figure`, unchanged. Several draw every
    series in its family's hue (:mod:`.families`) — actual solid, reference
    dashed in the same colour and out of the legend, a family folded to one
    legend entry — with the geometry of every leaf of every series, and NO
    overlays or error traces: both are per-variable pickers keyed by trace
    name. ``labels`` overrides a family's legend text by family key."""
    if len(views) == 1:
        return variable_figure(views[0], max_samples, editable=editable)
    if max_samples:
        views = [decimate_view(v, max_samples) for v in views]
    by_name = {v.name: v for v in views}
    spans = [_time_span(v) for v in views if v.act_time is not None or v.ref_time is not None]
    x_min = min((s[0] for s in spans), default=0.0)
    x_max = max((s[1] for s in spans), default=1.0)
    data: list[dict] = []
    shapes: list[dict] = []
    rings: list[dict] = []
    has_spectrum = any(v.has_spectrum for v in views)
    for h, (key, members) in enumerate(group_families([v.name for v in views])):
        colors = ramp(HUES[h % len(HUES)], len(members))
        label = (labels or {}).get(key) or family_label(key, members)
        for i, (name, color) in enumerate(zip(members, colors)):
            data.extend(_series_traces(by_name[name], color, key, label, first=i == 0))
    for view in views:
        ctx = _Ctx(
            x_min=x_min,
            x_max=x_max,
            has_spectrum=has_spectrum,
            full_samples=view.full_samples,
        )
        for leaf in view.leaves:
            window = next((p for p in leaf.geometry if isinstance(p, Window)), None)
            markers: list[Marker] = []
            for prim in leaf.geometry:
                if isinstance(prim, Marker):
                    markers.append(prim)
                    continue
                builder = TRACE_BUILDERS.get(type(prim))
                if builder is None:
                    raise TypeError(
                        f"no figure builder for {type(prim).__name__} "
                        f"(id {prim.id!r}); add one to TRACE_BUILDERS"
                    )
                traces, new_shapes = builder(prim, leaf, window, ctx)
                data.extend(traces)
                shapes.extend(new_shapes)
            data.extend(_marker_traces(markers, leaf))
            if editable:
                rings.extend(_handle_rings(markers))
    config: dict = dict(PLOT_CONFIG)
    if editable:
        config["edits"] = {"shapePosition": True}
    layout_view = replace(views[0], leaves=tuple(leaf for v in views for leaf in v.leaves)) if views else VariableView(name="")
    return {
        "data": data,
        "layout": _layout(layout_view, shapes + rings, False),
        "config": config,
    }


def _series_traces(
    view: VariableView, color: str, group: str, label: str, *, first: bool
) -> list[dict]:
    traces = []
    hover = f"{view.name}: %{{y:.4g}}<extra></extra>"
    if view.act_time is not None and view.act_values is not None:
        traces.append(
            {
                "type": "scatter",
                "mode": "lines",
                "name": label if first else view.name,
                "legendgroup": group,
                "showlegend": first,
                "hovertemplate": hover,
                "x": view.act_time.tolist(),
                "y": view.act_values.tolist(),
                "line": {"color": color, "width": 1.5},
                "meta": {
                    "id": f"actual/{view.name}",
                    "kind": "act",
                    "samples": len(view.act_time),
                    "full_samples": view.full_samples.get("act", len(view.act_time)),
                },
            }
        )
    if view.ref_time is not None and view.ref_values is not None:
        traces.append(
            {
                "type": "scatter",
                "mode": "lines",
                "name": f"{view.name} (reference)",
                "legendgroup": group,
                "showlegend": False,
                "hovertemplate": f"{view.name} (reference): %{{y:.4g}}<extra></extra>",
                "x": view.ref_time.tolist(),
                "y": view.ref_values.tolist(),
                "line": {"color": color, "width": 1.2, "dash": "dash"},
                "opacity": 0.7,
                "meta": {
                    "id": f"reference/{view.name}",
                    "kind": "ref",
                    "samples": len(view.ref_time),
                    "full_samples": view.full_samples.get("ref", len(view.ref_time)),
                },
            }
        )
    return traces
```

Imports to add at the top of `plotly.py`: `from collections.abc import Mapping, Sequence`, `from dataclasses import replace` (if absent), and `from .families import HUES, family_label, group_families, ramp`. The leaf loop is `variable_figure`'s, repeated per view on purpose: extracting it into a helper both call is a refactor of `variable_figure`, and the single-view identity test plus the figure goldens are what would tell a reviewer it stayed byte-identical — so do it as its OWN commit after this task is green (`figures: one leaf loop for both figure builders`), with `tests/test_figure_goldens.py` unchanged and green as its done-check. The order of `data` in the first `for`: `group_families` order, which the test pins (a family's members contiguous, sorted by index).

For `plot_figure([])`: `views` empty → the two loops do nothing; `layout_view` is the empty `VariableView`; the test pins the titled empty figure.

- [ ] **Step 5: Green, gates, commit** `figures: plot_figure — several series, families in a hue ramp, one view unchanged`; then the refactor commit described in Step 4.

**Named mutations:** M3.1 `family_of` uses the FIRST index. M3.2 `group_families` sorts members as strings (`pipe.T[11]` before `pipe.T[6]`). M3.3 `ramp(hue, 1)` returns the first stop. M3.4 the single-view branch is removed (one view goes through the multi path). M3.5 overlays are drawn on a multi-series plot (call `_base_traces`). M3.6 references keep `showlegend` True. M3.7 `slice_text` writes a step of 1 (`1:1:4`). M3.8 rings are added when `editable` is False. M3.9 error traces are drawn on a multi-series plot.

**Done when:** `plot_figure([v]) == variable_figure(v)` for the test's view; a four-member family draws four traces in `HUES[0]`'s four stops with one legend entry reading `pipe.T[1:5:16]`; the figure goldens are unchanged; gates green.

---

### Task 4: The Session reads a test's plots

**Files:**
- Modify: `src/dstf/authoring/session.py` (`_ModelReads`; `view`; after `figure`; `stage`; `SessionView`)
- Test: `tests/test_session.py` (a `TestPlots` class at the end of the file)

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_session.py`:

```python
def _plots_project(tmp_path: Path) -> tuple[Path, Path]:
    """`_two_var_project` with an UNTRACKED variable `z` in A's result file."""
    cfg, work = _two_var_project(tmp_path)
    path = work / "test_0001" / "result.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    flat = [7.0 for _ in payload["time"]]
    # `a0` sits BEFORE x in the file: file order is not tracked-first order.
    payload["variables"] = {"a0": flat, **payload["variables"], "z": flat}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return cfg, work


LAYOUT = [{"kind": "time", "series": [{"var": "x"}, {"var": "z"}]}]


class TestPlots:
    def _open(self, tmp_path) -> Session:
        cfg, work = _plots_project(tmp_path)
        s = Session()
        s.open(cfg, work_dir=work)
        return s

    def test_variable_names_lists_the_file_tracked_first(self, tmp_path):
        s = self._open(tmp_path)
        assert s.variable_names(A) == ["x", "y", "a0", "z"]

    def test_variable_names_of_a_test_with_no_result_is_empty(self, tmp_path):
        s = self._open(tmp_path)
        assert s.variable_names(C) == []

    def test_variable_names_of_an_unknown_model_is_a_key_error(self, tmp_path):
        s = self._open(tmp_path)
        with pytest.raises(KeyError):
            s.variable_names("FakeLib.Nope")

    def test_the_default_layout_is_one_plot_per_scored_variable(self, tmp_path):
        s = self._open(tmp_path)
        assert [(p.index, p.series, p.missing) for p in s.plots(A)] == [
            (0, ("x",), ()),
            (1, ("y",), ()),
        ]

    def test_view_of_an_untracked_variable_reads_the_result_file(self, tmp_path):
        s = self._open(tmp_path)
        view = s.view(A, "z")
        assert view.act_values.tolist() == [7.0] * 5
        assert view.ref_time is None and view.leaves == ()

    def test_view_of_a_name_the_file_lacks_is_the_empty_state(self, tmp_path):
        s = self._open(tmp_path)
        view = s.view(A, "nowhere")
        assert view.act_time is None and view.leaves == ()

    def test_a_staged_layout_is_what_plots_answers(self, tmp_path):
        s = self._open(tmp_path)
        s.stage([PatchOp(A, "add", "/plots", LAYOUT)])
        assert [(p.series, p.missing) for p in s.plots(A)] == [(("x", "z"), ())]
        s.discard()
        assert len(s.plots(A)) == 2

    def test_a_series_the_result_lacks_is_named_missing(self, tmp_path):
        s = self._open(tmp_path)
        s.stage(
            [PatchOp(A, "add", "/plots", [{"series": [{"var": "x"}, {"var": "gone"}]}])]
        )
        (plot,) = s.plots(A)
        assert plot.series == ("x",) and plot.missing == ("gone",)

    def test_staging_an_invalid_layout_changes_nothing(self, tmp_path):
        from dstf.config import ConfigError
        from dstf.discovery.patch_apply import PatchError

        s = self._open(tmp_path)
        with pytest.raises((PatchError, ConfigError), match=r"plots\[0\]\.series"):
            s.stage([PatchOp(A, "add", "/plots", [{"series": []}])])
        assert s.spec.pending == [] or tuple(s.spec.pending) == ()
        assert len(s.plots(A)) == 2

    def test_a_layout_edit_does_not_materialize_a_metrics_tree(self, tmp_path):
        s = self._open(tmp_path)
        s.stage([PatchOp(A, "add", "/plots", LAYOUT)])
        assert [op.path for op in s.spec.pending] == ["/plots"]

    def test_saving_writes_the_key_and_a_reopen_reads_it(self, tmp_path):
        cfg, work = _plots_project(tmp_path)
        s = Session()
        s.open(cfg, work_dir=work)
        s.stage([PatchOp(A, "add", "/plots", LAYOUT)])
        s.save()
        entry = next(
            e
            for e in json.loads((cfg.parent / "test_spec.json").read_text())["tests"]
            if e["model"] == A
        )
        assert entry["plots"] == LAYOUT
        again = Session()
        again.open(cfg, work_dir=work)
        assert [p.series for p in again.plots(A)] == [("x", "z")]

    def test_plot_figure_of_one_series_is_the_variables_figure(self, tmp_path):
        s = self._open(tmp_path)
        assert s.plot_figure(A, 0) == s.figure(A, "x")

    def test_plot_figure_of_two_series_draws_both(self, tmp_path):
        s = self._open(tmp_path)
        s.stage([PatchOp(A, "add", "/plots", LAYOUT)])
        fig = s.plot_figure(A, 0)
        ids = [tr["meta"]["id"] for tr in fig["data"] if tr["meta"]["kind"] == "act"]
        assert ids == ["actual/x", "actual/z"]
        assert fig["config"]["edits"] == {"shapePosition": True}

    def test_plot_figure_keeps_the_leaves_of_a_tracked_series(self, tmp_path):
        s = self._open(tmp_path)
        s.stage([PatchOp(A, "add", "/plots", LAYOUT)])
        single = s.figure(A, "x")
        multi = s.plot_figure(A, 0)
        leafy = lambda fig: sorted(  # noqa: E731
            tr["meta"].get("id") or tr["meta"]["family"]
            for tr in fig["data"]
            if tr["meta"]["kind"] not in ("act", "ref", "error", "overlay")
        )
        assert leafy(multi) == leafy(single)

    def test_plot_figure_out_of_range_is_an_index_error(self, tmp_path):
        s = self._open(tmp_path)
        with pytest.raises(IndexError):
            s.plot_figure(A, 5)

    def test_the_file_read_is_cached_and_dropped_with_the_results(
        self, tmp_path, monkeypatch
    ):
        from dstf.backends.python.backend import PythonBackend

        s = self._open(tmp_path)
        calls = []
        real = PythonBackend.result_series.__func__

        def counting(cls, test_dir, names):
            calls.append(tuple(names))
            return real(cls, test_dir, names)

        monkeypatch.setattr(PythonBackend, "result_series", classmethod(counting))
        s.view(A, "z")
        s.view(A, "z")
        assert calls == [("z",)]
        s._reload_results()  # what TestReadCache's reload test calls
        s.view(A, "z")
        assert calls == [("z",), ("z",)]

    def test_the_session_view_mirrors_the_three_reads(self, tmp_path):
        from dstf.authoring.session import Selection, SessionView

        s = self._open(tmp_path)
        v = SessionView(s, Selection())
        assert v.variable_names(A) == s.variable_names(A)
        assert v.plots(A) == s.plots(A)
        assert v.plot_figure(A, 0) == s.plot_figure(A, 0)
```

`Session.reload()` does NOT drop `_reads`; `_reload_results()` does, and `TestReadCache::test_a_results_reload_drops_the_cache` already drives it that way. Also in Step 1: the existing `TestSessionView::test_exposes_readers_and_no_writer` pins `SessionView`'s public names — add `"plot_figure"`, `"plots"` and `"variable_names"` to its pinned set, or it fails on "Extra items in the left set".

- [ ] **Step 2: Run red.** `uv run --no-sync pytest -q tests/test_session.py -k TestPlots` → 15 of 17 fail: `AttributeError: 'Session' object has no attribute 'variable_names'` (or `plots`, `plot_figure`); `test_view_of_an_untracked_variable...` on `view.act_values` being `None`; `test_staging_an_invalid_layout...` on its closing `s.plots`. Two pass red and are GUARDS: `test_a_layout_edit_does_not_materialize...` (its mutation is M4.8) and `test_view_of_a_name_the_file_lacks...` (its mutation is M4.9).

- [ ] **Step 3: Implement.** In `_ModelReads` add two fields:

```python
    #: Every name in the result file (`Backend.result_names`); `None` until
    #: read, `()` when the backend cannot list it.
    names: tuple[str, ...] | None = None
    #: Untracked series read from the result file, by name.
    series: dict[str, tuple[np.ndarray, np.ndarray]] = field(default_factory=dict)
```

(`numpy as np` under `TYPE_CHECKING` if the module does not import it.) Add to `Session`, after `_reads_for`:

```python
    def _test_dir(self, model_id: str) -> Path | None:
        from ..backends.base import last_test_keys

        project = self._require_project()
        work_dir = project.config.work_dir
        key = last_test_keys(work_dir).get(model_id) if work_dir is not None else None
        return work_dir / key if key is not None and work_dir is not None else None

    def _tracked(self, model_id: str) -> list[str]:
        """The names the page plots by default, in its order: the scored
        variables, else — nothing scored (no baseline) — the result's."""
        comp = self.comparisons.get(model_id)
        names = [vc.name for vc in comp.variables if vc.name] if comp is not None else []
        if not names:
            result = self._require_project().results.get(model_id)
            names = [v.name for v in (result.variables if result else []) if v.name]
        return list(dict.fromkeys(names))

    def variable_names(self, model_id: str) -> list[str]:
        """Every variable the last result holds: the tracked ones first, in
        the page's order, then the rest of the result file in its order."""
        project = self._require_project()
        if project.test(model_id) is None:
            raise KeyError(model_id)
        reads = self._reads_for(model_id)
        if reads.names is None:
            test_dir = self._test_dir(model_id)
            listed = (
                type(project.backend).result_names(test_dir)
                if test_dir is not None
                else None
            )
            reads.names = tuple(listed or ())
        tracked = self._tracked(model_id)
        return tracked + [n for n in reads.names if n not in set(tracked)]

    def _file_series(self, model_id: str, variable: str):
        reads = self._reads_for(model_id)
        if variable not in reads.series:
            test_dir = self._test_dir(model_id)
            got = (
                type(self._require_project().backend).result_series(test_dir, [variable])
                if test_dir is not None
                else {}
            )
            if variable not in got:
                return None
            reads.series[variable] = got[variable]
        return reads.series[variable]

    def plots(self, model_id: str) -> tuple[ResolvedPlot, ...]:
        """The test's plots as the page draws them: the entry's ``plots``
        with pending ops applied, else one per tracked variable."""
        project = self._require_project()
        test = project.test(model_id)
        if test is None:
            raise KeyError(model_id)
        names = self.variable_names(model_id)
        reads = self._reads_for(model_id)
        return resolved_plots(
            test, self._tracked(model_id), available=names if reads.names else None
        )

    def plot_figure(self, model_id: str, index: int) -> dict:
        """The editable figure of plot ``index`` (IndexError past the end).
        One series is :meth:`figure` — cached base and all."""
        from ..figures.plotly import plot_figure

        plot = self.plots(model_id)[index]
        if len(plot.series) == 1:
            return self.figure(model_id, plot.series[0])
        budget = self._require_project().config.max_embedded_samples
        return plot_figure(
            [self.view(model_id, name) for name in plot.series], budget, editable=True
        )
```

with `from ..discovery.plots import ResolvedPlot, resolved_plots` at the top (no UI import — the UI-import predicate test covers this file). In `view`, replace the final `traj = (... if nb is not None else {})` fallback so an untracked name reads the file:

```python
        if traj is None:
            nb = next((t for t in nobaseline if t["name"] == variable), None)
            if nb is not None:
                traj = {
                    "act_time": nb["time"],
                    "act_values": nb["values"],
                    "overlays": nb["overlays"],
                }
            else:
                series = self._file_series(model_id, variable)
                traj = (
                    {"act_time": series[0], "act_values": series[1]}
                    if series is not None
                    else {}
                )
```

In `SessionView`, beside `figure`: `variable_names`, `plots`, `plot_figure`, each a one-line delegate like its neighbours. `stage` needs no change for validity (the `_merge` it already runs parses `plots` and raises `ConfigError`); confirm `needs_materialization` is false for a `/plots` op (it only looks at `/metrics/`) — the test pins it.

- [ ] **Step 4: Green, gates, commit** `session: a test's plots, every variable of its result, the figure of one plot`.

**Named mutations:** M4.1 `variable_names` does not put tracked first. M4.2 `view` does not fall back to the file (keeps `{}`). M4.3 `plots` ignores pending ops (reads `self.spec.loaded`'s entry) — caught by `test_a_staged_layout...`. M4.4 the spec parser's `ConfigError` on bad `plots` is caught inside `stage` and ignored. M4.5 `plot_figure` passes `editable=False`. M4.6 `_file_series` is not cached. M4.8 `needs_materialization` treats a `/plots` op as an edit under `/metrics/`. M4.9 `view` of a name the file lacks raises instead of answering the empty state. M4.7 `plots` passes `available=[]` when the backend could not list (every untracked series missing) — killed by this test, part of Step 1 (unconditional):

```python
    def test_a_backend_that_cannot_list_calls_nothing_missing(self, tmp_path, monkeypatch):
        from dstf.backends.python.backend import PythonBackend

        s = self._open(tmp_path)
        monkeypatch.setattr(
            PythonBackend, "result_names", classmethod(lambda cls, d: None)
        )
        s._reload_results()
        s.stage([PatchOp(A, "add", "/plots", LAYOUT)])
        assert s.plots(A)[0].missing == ()
```

**Done when:** over the fixture, `Session.plots(A)` answers the default, a staged layout and a saved-and-reopened layout; `view(A, "z")` holds the untracked trajectory; `plot_figure` draws one or several series editable; gates green.

---

### Task 5: The report draws the test's plots

**Files:**
- Modify: `src/dstf/reporting/report_suite.py` (`_build_per_test_args`, `_render_one_test`, `generate_comparison_plots`, `_build_figures`, `_page_context`, `_decimated_traces`' loop over figures), `src/dstf/reporting/templates/interactive.html` (the variable-sections block and `window.MT_REPORT`), `src/dstf/reporting/templates/report.js` (`renderAllPlots`, the error-overlay and overlay-picker wiring), `src/dstf/cli/review.py` (its `generate_comparison_plots` call)
- Test: `tests/test_report_suite.py`, `tests/test_interactive_report.py`

**The shape.** Two public helpers in `report_suite.py`, so no caller and no test fixture hand-rolls the layout or the page's keys:

```python
def plot_inputs(test, tracked, backend_cls, test_dir):
    """``(plots, extra_series)`` for one test's page. A test with no
    ``plots`` key — or no backend class or test dir to read a result file
    through — gets ``(None, {})``: the default layout, and the result file
    is not opened (331 TRANSFORM reports pay nothing). Otherwise the
    resolved plots and every plot-only series the file has."""
    from ..discovery.plots import resolved_plots

    if test is None or test.plots is None or backend_cls is None or test_dir is None:
        return None, {}
    available = backend_cls.result_names(test_dir)
    plots = resolved_plots(test, tracked, available=available)
    wanted = sorted({n for p in plots for n in p.series} - set(tracked))
    return plots, backend_cls.result_series(test_dir, wanted)


def plot_page_keys(context, plots, extra_series, metric_tree, max_samples) -> dict:
    """``plots`` (the sidecar's record), ``plot_figures``, ``plot_series``
    and ``plot_sections`` from a render context. ``plots=None`` is one plot
    per key of ``variables_by_name``, in its order. A series found neither
    there nor in ``extra_series`` draws nothing and joins ``missing``."""
```

`plot_page_keys`' body: `by_name = context.get("variables_by_name") or {}`; `plots` defaults to `tuple(ResolvedPlot(i, "time", (n,), ()) for i, n in enumerate(by_name))`; `vidx = {n: i for i, n in enumerate(by_name)}`; `leaves_by_var = leaves_by_variable(metric_tree)`; `budget = max_samples if max_samples and max_samples > 0 else None`. Per plot: `drawn = [n for n in plot.series if n in by_name or n in extra_series]`, `missing = [*plot.missing, *(n for n in plot.series if n not in drawn)]`; the views are `view_from_trajectory(n, by_name[n].get("trajectory") or {}, leaves_by_var.get(n, []))` for a tracked name and `build_view(n, act_time=extra_series[n][0], act_values=extra_series[n][1])` for a plot-only one; the figure is `plot_figure(views, budget)` (never editable). The section is `{"index": plot.index, "title": ", ".join(family_label(k, m) for k, m in group_families(drawn)), "series": drawn, "missing": missing, "single": len(drawn) == 1, "variables": [{"name": n, "vidx": vidx[n], **{k: v for k, v in by_name[n].items() if k != "trajectory"}, "overlays": [_overlay_row(ov) for ov in by_name[n].get("overlays") or []]} for n in drawn if n in by_name], "plot_only": [n for n in drawn if n not in by_name]}`. It returns `{"plots": [{"kind": p.kind, "series": drawn_p, "missing": missing_p} ...], "plot_figures": [...], "plot_series": [drawn_p ...], "plot_sections": [...]}`.

`generate_comparison_plots` gains `plots: tuple[ResolvedPlot, ...] | None = None` and `extra_series: Mapping[str, tuple] | None = None`. It calls `plot_page_keys` ONCE, after the context is built: the `"plots"` key goes into the context BEFORE the sidecar write (so `comparison_data.json` records the layout — built from names only, so computing the figures early costs the sidecar nothing: pop `plot_figures`, `plot_series` and `plot_sections` out before the dump and `context.update` them after it, where `_build_figures` is called today). `_build_figures` keeps `diag_figures` and `nb_figures` and no longer builds `figures`; `_page_context` no longer needs a per-variable copy for the markup (the sections carry it) but keeps stripping `trajectory` from `variables_by_name`; `_decimated_traces` scans `plot_figures` (naming a trace by its plot's title) instead of `figures`. Both callers resolve their inputs through `plot_inputs`: `_build_per_test_args` gains a keyword `backend_cls=None`, resolved ONCE per suite beside `artifact_files` inside the same `try/except ValueError` (`backend_cls = get_backend_class(config)`, `None` on `ValueError`), and calls `plot_inputs(test, tracked, backend_cls, test_dir)` after its existing `test_dir = ...` line, with

```python
    tracked = list(dict.fromkeys(v.name for v in (comp.variables or []) if v.name))
    if not tracked and result is not None:
        tracked = [v.name for v in result.variables if v.name]
```

and puts `"plots"` and `"extra_series"` in the args dict for `_render_one_test` to pass on; `cli/review.py` does the same before its `generate_comparison_plots` call (it has `config`, the test and `test_key`; `get_backend_class(config)` in the same `try/except ValueError`).

**The template.** The `{% for varname, vardata in variables_by_name.items() %}` block becomes `{% for plot in plot_sections %}`: a `<div class="variable-section plot-section-block" data-plot="{{ plot.index }}" id="variable-section-{{ plot.index }}">`; an `<h2>` holding, per tracked variable, its `var-status` span and advisory chip exactly as today, then `{{ plot.title }}`; the error-overlay `<select class="error-overlay-select" data-plot="{{ plot.index }}">` (renamed from `data-vidx`: on this page `data-vidx` now means a VARIABLE's index — on `var-status` and `nodes-N` — and the select belongs to a plot; `tests/test_interactive_report.py` selects it by that attribute, so its selector moves too) and the overlay picker (`data-plot-id="plot-{{ plot.index }}"`) ONLY `{% if plot.single and plot.variables %}`, otherwise — for a plot of several series — one line `<div class="plot-note">Overlays and error traces show on a plot with one series.</div>`; `{% if plot.missing %}<div class="plot-note">Not in this result: {{ plot.missing | join(", ") }}</div>{% endif %}`; the chart `<div class="plotly-chart plot-main" id="plot-{{ plot.index }}" style="height:300px">`; then per tracked variable its `node-tree` (`id="nodes-{{ v.vidx }}"`, `data-variable`), and per plot-only name `<div class="plot-note">{{ name }} is plot only: it has no reference and no criteria.</div>`. `.plot-note` is styled in the page's stylesheet as `font-size:0.85em;color:var(--dstf-text-secondary)` — use the token name the stylesheet already uses for secondary text (read `theme.py`; do not invent one). `window.MT_REPORT` carries `PLOT_FIGURES: {{ (plot_figures or []) | tojson }},` and `PLOT_SERIES: {{ plot_series | tojson }},` (each plot's series names, in order — one key per line, as the block is written today) in place of `VARIABLE_ORDER` and `FIGURES`.

**The script.** `renderAllPlots` paints `PLOT_FIGURES[idx]` into `plot-${idx}`; `const PLOT_SERIES = MT_REPORT.PLOT_SERIES || [];` is declared beside it (the browser tests read it); `VARIABLE_ORDER` and `FIGURES` are deleted; `wireErrorOverlays` reads `dataset.plot` and `setErrorOverlay(plotIdx, mode)` keeps its body (it looks up `plot-${plotIdx}`). Rebuild the copied-out `interactive.js` the way the module does (`INTERACTIVE_JS_PARTS`).

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_report_suite.py`:

```python
def _page_data(page: str, key: str):
    """One key of `window.MT_REPORT`, parsed. Keys are written one per line
    as `KEY: <json>,`."""
    import json
    import re

    m = re.search(rf"^\s*{key}:\s*(.*),\s*$", page, re.M)
    assert m, f"window.MT_REPORT must carry {key}"
    return json.loads(m.group(1))


class TestPlotSections:
    def _render(self, tmp_path, **kw):
        """Two tracked variables, x (passes) and y (passes), nrmse leaves."""
        import json
        from pathlib import Path

        import numpy as np

        from dstf.backends.types import TestResult, VariableResult
        from dstf.comparison.comparator import compare_test
        from dstf.discovery.test_registry import TestModel
        from dstf.reporting.report_suite import generate_comparison_plots

        t = np.linspace(0.0, 1.0, 51)
        ref = {
            "test_id": "ref_0001",
            "time": t.tolist(),
            "variables": [
                {"index": 1, "name": "x", "values": np.sin(t).tolist()},
                {"index": 2, "name": "y", "values": np.cos(t).tolist()},
            ],
        }
        result = TestResult(
            model_id="M",
            success=True,
            variables=[
                VariableResult(index=1, name="x", time=t, values=np.sin(t)),
                VariableResult(index=2, name="y", time=t, values=np.cos(t)),
            ],
        )
        test = TestModel(
            model_id="M",
            source_file=Path("fixture.mo"),
            source_package="Fixture",
            short_name="M",
            n_vars=2,
        )
        comp = compare_test(test, result, ref)
        plot_dir = tmp_path / "plots"
        html = generate_comparison_plots(
            "M",
            ref,
            result,
            comp.variables,
            plot_dir,
            test_model=test,
            metric_tree=comp.metric_tree,
            **kw,
        )
        page = html.read_text(encoding="utf-8")
        sidecar = json.loads(
            (plot_dir / "comparison_data.json").read_text(encoding="utf-8")
        )
        return page, sidecar

    def test_with_no_layout_the_page_has_one_section_per_tracked_variable(self, tmp_path):
        page, sidecar = self._render(tmp_path)
        assert _page_data(page, "PLOT_SERIES") == [["x"], ["y"]]
        assert len(_page_data(page, "PLOT_FIGURES")) == 2
        assert "VARIABLE_ORDER" not in page and "  FIGURES:" not in page
        assert 'id="plot-0"' in page and 'id="plot-1"' in page
        assert page.count('class="error-overlay-select"') == 2
        assert sidecar["plots"] == [
            {"kind": "time", "series": ["x"], "missing": []},
            {"kind": "time", "series": ["y"], "missing": []},
        ]

    def test_a_two_series_plot_draws_both_and_says_why_it_has_no_overlays(self, tmp_path):
        from dstf.discovery.plots import ResolvedPlot

        plots = (ResolvedPlot(index=0, kind="time", series=("x", "y"), missing=()),)
        page, sidecar = self._render(tmp_path, plots=plots)
        (fig,) = _page_data(page, "PLOT_FIGURES")
        acts = [t["meta"]["id"] for t in fig["data"] if t["meta"]["kind"] == "act"]
        assert acts == ["actual/x", "actual/y"]
        assert "Overlays and error traces show on a plot with one series." in page
        assert 'class="error-overlay-select"' not in page
        assert 'id="nodes-0"' in page and 'id="nodes-1"' in page
        assert sidecar["plots"] == [
            {"kind": "time", "series": ["x", "y"], "missing": []}
        ]

    def test_a_plot_only_series_comes_from_extra_series_and_is_named(self, tmp_path):
        import numpy as np

        from dstf.discovery.plots import ResolvedPlot

        t = np.linspace(0, 1, 5)
        plots = (
            ResolvedPlot(index=0, kind="time", series=("x", "z"), missing=("gone",)),
        )
        page, _ = self._render(
            tmp_path, plots=plots, extra_series={"z": (t, t * 0 + 7)}
        )
        (fig,) = _page_data(page, "PLOT_FIGURES")
        z = next(tr for tr in fig["data"] if tr["meta"]["id"] == "actual/z")
        assert z["y"] == [7.0] * 5
        assert "z is plot only: it has no reference and no criteria." in page
        assert "Not in this result: gone" in page

    def test_a_tracked_variable_on_no_plot_still_has_its_tree_at_the_top(self, tmp_path):
        from dstf.discovery.plots import ResolvedPlot

        plots = (ResolvedPlot(index=0, kind="time", series=("x",), missing=()),)
        page, _ = self._render(tmp_path, plots=plots)
        assert _page_data(page, "PLOT_SERIES") == [["x"]]
        assert 'id="nodes-full"' in page  # the whole tree, y's leaf included
        assert 'id="nodes-1"' not in page

    def test_the_figures_stay_uneditable(self, tmp_path):
        from dstf.discovery.plots import ResolvedPlot

        plots = (ResolvedPlot(index=0, kind="time", series=("x", "y"), missing=()),)
        page, _ = self._render(tmp_path, plots=plots)
        (fig,) = _page_data(page, "PLOT_FIGURES")
        assert "edits" not in fig["config"]


class TestPerTestArgsReadTheLayout:
    """`_build_per_test_args` over a real work dir (the Session fixture's)."""

    def _args(self, tmp_path, layout):
        import json

        from test_session import A, _two_var_project

        from dstf.authoring.session import Session
        from dstf.reporting.report_suite import _build_per_test_args

        cfg, work = _two_var_project(tmp_path)
        path = work / "test_0001" / "result.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["variables"]["z"] = [7.0 for _ in payload["time"]]
        path.write_text(json.dumps(payload), encoding="utf-8")
        if layout is not None:
            spec_path = cfg.parent / "test_spec.json"
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
            next(e for e in spec["tests"] if e["model"] == A)["plots"] = layout
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
        s = Session()
        s.open(cfg, work_dir=work)
        project = s.project
        return _build_per_test_args(
            s.comparisons[A],
            project.results,
            {t.model_id: t for t in project.tests},
            project.store,
            project.config,
            {A: {"test_key": "test_0001", "last_run_at": None}},
            backend_cls=type(project.backend),
        )

    def test_a_layout_naming_an_untracked_variable_reads_it_from_the_file(self, tmp_path):
        args = self._args(
            tmp_path, [{"series": [{"var": "x"}, {"var": "z"}, {"var": "gone"}]}]
        )
        (plot,) = args["plots"]
        assert plot.series == ("x", "z") and plot.missing == ("gone",)
        assert list(args["extra_series"]) == ["z"]
        assert args["extra_series"]["z"][1].tolist() == [7.0] * 5

    def test_without_a_layout_the_result_file_is_not_opened(self, tmp_path, monkeypatch):
        from dstf.backends.python.backend import PythonBackend

        def boom(cls, *a):
            raise AssertionError("the default layout must not touch the result file")

        monkeypatch.setattr(PythonBackend, "result_names", classmethod(boom))
        monkeypatch.setattr(PythonBackend, "result_series", classmethod(boom))
        args = self._args(tmp_path, None)
        assert args["plots"] is None and args["extra_series"] == {}
```

If `Session.project` is not the attribute name the Session exposes its `Project` under, use the one `tests/test_session.py` uses. Existing tests that read the page's old keys move with the page, and none loses an assertion: `tests/test_interactive_report.py::_embedded_figures` returns `{series[0]: fig}` built from `PLOT_SERIES` and `PLOT_FIGURES` (every fixture there is one series per plot); its `VARIABLE_ORDER.indexOf('x')` evaluations become `PLOT_SERIES.findIndex(s => s.includes('x'))` and the `VARIABLE_ORDER.forEach((name, i) => …)` loop becomes `PLOT_SERIES.forEach((series, i) => …)` with `series[0]` as the name; `tests/test_report_size_budget.py` and the two `FIGURES:` parses in `tests/test_report_suite.py` read `PLOT_FIGURES` through `_page_data`; the finding-76h test (`VARIABLE_ORDER` emitted explicitly because `Object.keys` reorders integer-like keys) becomes: with variables named `"10"` and `"2"`-style integer-like names as that test builds them, `PLOT_SERIES` lists them in `variables_by_name` order — the list form makes the ordering structural, and the test still pins it.

In `tests/test_interactive_report.py` add a Chromium test beside `test_every_variable_plot_paints_from_its_figure_dict`, with a module-scoped fixture `two_series` built like `compared` but passing `plots=(ResolvedPlot(0, "time", ("<first var>", "<second var>"), ()),)` for the first two variables `_generate_compared` tracks:

```python
@pytest.mark.playwright
def test_a_two_series_plot_paints_both_traces(two_series, playwright_browser):
    page = _open(playwright_browser, two_series)
    try:
        ids = page.evaluate(
            "() => document.getElementById('plot-0').data"
            ".filter(t => t.meta && t.meta.kind === 'act').map(t => t.meta.id)"
        )
        assert len(ids) == 2 and all(i.startswith("actual/") for i in ids)
        assert page.locator("#plot-0 .legend .traces").count() >= 2
        assert page.locator(".error-overlay-select").count() == 0
    finally:
        page.close()
```

(Follow the module's own open/close idiom if `_open` returns something other than a page.) Every existing test in both files that reads `FIGURES[...]` or `VARIABLE_ORDER` moves to `PLOT_FIGURES[idx]` — `_embedded_figures` becomes a dict keyed by variable name built from `PLOT_FIGURES` and the `plot_sections` order, so those tests keep asserting on names; do not delete an assertion to make a test pass.

- [ ] **Step 2: Run red.** `uv run --no-sync pytest -q tests/test_report_suite.py -k "TestPlotSections or TestPerTestArgs"` → the first fails on `window.MT_REPORT must carry PLOT_SERIES`; the ones passing `plots=` fail with `TypeError: generate_comparison_plots() got an unexpected keyword argument 'plots'`; the two args tests fail with `KeyError: 'plots'`.

- [ ] **Step 3: Implement** the two helpers, the callers, the template and the script as specified above. Then move every hand-built consumer onto `plot_page_keys` — none keeps a private copy of the new keys:
  - `tests/report_fixtures.py`: `_fixture_context()` drops `"figures": {}` and, as its last step, does `ctx.update(plot_page_keys(ctx, None, {}, None, None))`; `_context_with_figure()` replaces `ctx["figures"] = {var: fig}` with `ctx["plot_figures"] = [fig]` (the sections and series from `_fixture_context` already name that one variable). `tests/test_theme.py` renders through these and needs no edit of its own — confirm by running it.
  - `tests/test_interactive_figures.py` (playwright): where it assigns `ctx["figures"][var] = variable_figure(...)` it assigns `ctx["plot_figures"][0]`, and where it adds an overlay to `variables_by_name[var]` it re-runs `ctx.update(plot_page_keys(ctx, None, {}, None, None))` BEFORE replacing the figure, so the section's overlay rows exist for the picker.
  - `tests/test_report_suite.py`: the three `FIGURES:` parses (the decimated-figure test, the two further render tests) read `_page_data(page, "PLOT_FIGURES")[0]`; the spy on `_build_figures` that reads `built[0]["figures"]` spies on `plot_page_keys` and reads `["plot_figures"]`; the `_decimated_traces` test's context passes `"plot_figures": [...]` with `"plot_sections"` titles; `assert "FIGURES:" in page` becomes `assert "PLOT_FIGURES:" in page` (the old needle is a substring of the new key and would pass vacuously).
  - `tests/test_report_size_budget.py`: its `FIGURES:` parse reads `PLOT_FIGURES` the same way.
  - the `two_series` fixture in `tests/test_interactive_report.py` names the first two variables `_generate_compared` tracks — read them off its `TestModel` / result construction and write them literally.

  Add to `TestPlotSections`:

```python
    def test_a_series_in_neither_source_is_named_missing_not_drawn(self, tmp_path):
        """A caller that resolved the layout without a name list (or a
        result file that lost the variable between the list and the read)."""
        from dstf.discovery.plots import ResolvedPlot

        plots = (ResolvedPlot(index=0, kind="time", series=("x", "ghost"), missing=()),)
        page, sidecar = self._render(tmp_path, plots=plots)
        assert _page_data(page, "PLOT_SERIES") == [["x"]]
        assert "Not in this result: ghost" in page
        assert sidecar["plots"][0]["missing"] == ["ghost"]
```

- [ ] **Step 4: Green, gates (the Playwright gate matters here), commit** `report: the page draws the test's plots — sections per plot, plot-only series from the result file`.

**Named mutations:** M5.1 the template shows the error-overlay select on a multi-series plot. M5.2 `plot_only` names are not listed. M5.3 `missing` is not rendered. M5.4 `_build_per_test_args` never calls `result_series` (`extra_series = {}`). M5.5 the default layout reads the result file (assert with a monkeypatched `result_names` that raises when `test.plots is None` — add this test). M5.6 `report.js` still reads `FIGURES` (the Chromium test goes blank). M5.7 the sidecar omits `plots`. M5.8 `plot_page_keys` draws a series found in neither source as an empty trace instead of naming it missing. M5.9 `_build_per_test_args` resolves `get_backend_class` per test, unwrapped (an unregistered backend raises) — killed by a test that passes `backend_cls=None` with a `plots` key set and expects `(None, {})`; add it to `TestPerTestArgsReadTheLayout`.

**Done when:** a spec with `plots` naming two series, one untracked, renders through the suite entry point to a page whose plot 0 paints both in Chromium; a spec without the key renders the sections, ids and controls it renders today; the sidecar names the layout; all gates green including Playwright.

---

### Task 6: `compare --report` over the examples, by hand (the orchestrator's)

**Files:** none changed in the tree.

- [ ] **Step 1:** Copy `examples/python/PythonTestingLib` and its work dir recipe into the scratchpad the way `tests/conftest.py::python_example_copy` does; `uv run --no-sync dstf --config <copy>/testing.json run --report <scratch>/rep`; add to ONE entry of the COPY's `test_spec.json` a `plots` key naming its tracked variable plus one untracked name from that test's `result.json`; `uv run --no-sync dstf --config <copy>/testing.json compare --report <scratch>/rep2`.
- [ ] **Step 2:** Screenshot that test's `interactive.html` at 1400×900 in Chromium (Playwright, from the scratchpad); READ the thumbnail. Expect: one plot, two traces in blue and green, the legend naming both, the "plot only" line, the criteria tree of the tracked variable under the plot, and no error-overlay select.

**Done when:** the screenshot was read and matches; a mismatch is a defect fixed as its own commit with its own red state and review.

---

### Task 7: Docs — the key, the hues, D130, the split

**Files:** `docs/usage.md` (the test_spec.json section), `CLAUDE.md` (the "test_spec.json format" section: one paragraph + the key in the example; the Project Structure lines for `discovery/` and `figures/`), `docs/architecture.md` (the layer ↔ code map: `discovery/plots.py`, `backends/common/result_files.py`, `figures/families.py`), `docs/extensibility.md` (§ backends: the two optional classmethods, their contract and default), `DESIGN.md` ("Array families on a plot": the two further hues, their four stops, "the list cycles"; "Plots are modular": "a plot of several series shows no overlays and no error traces"), `docs/decisions.md` (D130), the roadmap (§3: a Status paragraph — "planned as 3a / 3b / 3c; 3a built <date>" with the three facts that forced the split; "The seams" paragraph: `/plots` is patchable through `spec-update`).

D130's text:

```markdown
## D130 — A test's plot layout is spec data; a result file is read by its backend class (2026-09-20)

**Context.** The redesigned Test page draws any number of plots per test, any
series per plot, tracked or not, and the report must draw the same plots
(brief §3.9, §3.10). Three things stood in the way: a loaded `TestResult`
holds only the tracked variables; spec patches were whitelisted to
`/comparison` and `/metrics`; the report page was one section per variable.

**Decision.** `plots` is an optional key per `test_spec.json` entry —
`[{"kind": "time", "series": [{"var": ...}]}]` — strict inside (unknown keys,
kinds, globs, duplicates and empty series are refused by name), ignored by
scoring, carried on `TestModel.plots` (`None` = absent = one plot per tracked
variable; `[]` = no plots), patchable under `/plots`, described in the
exported schema. Any variable of a result file is listed and read through two
classmethods, `Backend.result_names(test_dir)` and
`Backend.result_series(test_dir, names)`, default "none": a classmethod
because the report reads a test's files without instantiating a backend.
`plot_figure(views)` draws a plot: one view is `variable_figure` unchanged;
several draw families (the last integer index) in a hue ramp with a folded
legend, every leaf's geometry, and no overlays or error traces.

**Consequences.** Dymola, Julia and Python result files hold every variable,
so any of them can be plotted; OpenModelica's variable filter and FMPy's
requested outputs keep only the tracked ones, and the tool says so rather
than widening the run. A layout that names a variable a rerun dropped keeps
its plot and names the series as missing. `/variables` stays outside the
patch whitelist until tracking from the drawer needs it (3c).
```

- [ ] Write each; `uv run --no-sync pytest -q tests/test_docs*.py` if the repo has doc tests (run whatever `ls tests | grep -i doc` names); gates; commit `docs: the plots key, result-file reads, D130; the roadmap records 3a/3b/3c`.

**Done when:** each file above says what the code now does, in the present tense, with no plan vocabulary ("Task", "3a") outside the roadmap.

---

### Task 8: The real-library gate over a COPY (the orchestrator's)

Per memory (transform-verification-setup, plan-tests-from-scratchpad): `cp -r testing_output/TRANSFORM/Dymola/linux <scratch>/gate/work`; COPY the TRANSFORM `test_spec.json` and point a scratch `testing.json` at the copy (never write the owner's spec). One gate driver per command.

- [ ] **Step 1 — what stayed the same:** `compare --work-dir <copy> --report <scratch>/rep-before` at the pre-plan commit (from a `git archive` of 7697c36 with `PYTHONPATH` exported) and at HEAD over the same copy: the verdict counts are equal (expected 330 passed / 1 simulation error), and for three tests picked by position (first, middle, last) the number of plot elements and their ids on `interactive.html` are equal.
- [ ] **Step 2 — what the feature DID:** pick a test with ≥ 4 tracked variables (the full model id). `Backend.result_names` over its `dsres.mat` lists more names than are tracked — record both counts. Write to the COPY's spec a `plots` key for that test: plot 0 = two tracked variables; plot 1 = one tracked + one UNTRACKED array family of four members (`…[1]`, `[2]`, `[3]`, `[4]` of a real array in the file). `compare --work-dir <copy> --report <scratch>/rep-after --filter "<full model id>"`. Predicates: the page has exactly two plot sections; plot 1's figure has four traces whose colours are `HUES[1]`'s ramp… — state the colours read from the figure JSON and compare to `ramp(HUES[h], 4)` for the family's group position; the legend entry reads `<array>[1:4]`; the "plot only" line names the four; the owner's real spec md5 and the reference dir listing are unchanged.
- [ ] **Step 3:** screenshot the page at 1600×900, READ the thumbnail.
- [ ] **Step 4:** the Session over the copy: `Session.open(scratch testing.json, work_dir=<copy>)`; time `variable_names(<model>)` (first call, cached call) and `view(<model>, <untracked name>)`; record the three numbers in the roadmap's Status paragraph. No bound is asserted: they inform 3c's drawer (a 4,000-name list).

**Done when:** every predicate above holds, the numbers are in the roadmap, the screenshot was read.

---

## Self-review (run by the writer before the plan's review)

- Spec coverage: roadmap §3 "Settles" — the `plots` schema and its validation (Task 1), the series model (Task 1, `SeriesSpec`), how the report renders a layout (Task 5). "The in-place redraw per plot" is 3b. Acceptance predicate "a `plots` entry with two series on one plot draws both on both tiers" — Tasks 4 and 5. Gate item "a `plots` key written to a COPY of the spec and read back by `compare --report`" — Task 8.
- Types: `ResolvedPlot(index, kind, series, missing)` is used with the same field names in Tasks 1, 4, 5, 8. `plot_figure(views, max_samples=None, *, editable=False, labels=None)` in Tasks 3, 4, 5. `result_names(test_dir) -> list[str] | None`, `result_series(test_dir, names) -> dict` in Tasks 2, 4, 5.
- Held for 3b/3c, named above: staging layout edits from the page, the toolbar's greyed overlay controls, `/variables` in the whitelist.
