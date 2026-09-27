# The Row Model and One Vocabulary (step S1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One definition of a test's row — its fields, its columns, their types, their display text and their sort values — and one status vocabulary, used by the static dashboard now and by the tool's Suite table in S2; plus one typed-filter module (`filters.js`) both surfaces load, so a filter means the same thing on each.

**Architecture:** `reporting/status.py` owns every status word (the four verdicts plus the three live states). A new `reporting/rows.py` owns `TestRow` (a frozen dataclass), `COLUMNS` (key, label, kind, and the functions that give a row's display text and sort value) and the builders (`row_from_status`, `enrich_row`, `summary_fields`). The report suite's sidecar `summary` and the dashboard both go through `summary_fields`, so a number is computed once. `dashboard.html` loops over the columns instead of spelling fifteen of them out; `dashboard.js` filters through `filters.js`. The tool (S2) will build `TestRow`s from its Session and give the same `COLUMNS` to an AG Grid.

**Tech Stack:** Python 3.10+, Jinja2, plain JS (no build step), pytest, Playwright + Chromium, uv.

**Owner rulings this plan implements (2026-09-26, feedback board https://claude.ai/artifact/88WhEgGHtSjT3pkiJTJ4Sm):** "The dashboard and the Suite page share one row model: the same columns and the same words on both. The dashboard stays a static file, for CI and for archiving." Q1b A: typed column filters (status by the count pills, numbers by `>1e-4` / `1..10`, text by glob), with `.*` and `Aa` once in the toolbar. The pills on the board read `PASS 330 · FAIL 0 · SIM_FAIL 1 · NO_REF 0 · EDITED 2` — the verdict words, which this plan makes the dashboard's pill words too (it says "passed / sim failed / no baseline" today).

**Facts this plan rests on (read 2026-09-26, file:line at 9c417d9):**
- Dashboard columns: `templates/dashboard.html:179-193` (header), `:195-204` (filter row), `:209-254` (row `data-*` and cells). Rows are built in `reporting/dashboard_render.py:239-286` from `status.json` `tests[]`, then in final mode overwritten by `_enrich_row_from_comparison` (`:58-132`, two guards: model-id mismatch, `written_at < start_wall`) from the sidecar `summary` written at `reporting/report_suite.py:515-556`.
- A dashboard BUG: the Resolution column has a filter input (`dashboard.html:199`) but its `<tr>` carries no `data-resolution`, so any text typed there hides every row (`dashboard.js:~160` reads `row.dataset[col]`). Generating every column's attributes from one list fixes it by construction.
- Filters: `dashboard.js:124-133` `patternMatcher` — case-insensitive substring, or an anchored glob when the text holds `*` or `?`. Pills: `dashboard.js:108-117` (`PILL_TO_STATUSES`), `:278-313` (render), `:321-339` (counts from rows). No number filters; no regex or case toggle.
- Status words today, side by side: classifier `PASS/FAIL/SIM_FAIL/NO_REF` (`status.py:35-48`); live `queued/running/passed/failed/timed_out` (`backends/progress.py`), mapped to `QUEUED/RUNNING/TIMED OUT` by `dashboard_render._LIVE_STATUS_MAP` (`:136-147`); dashboard pills "queued / running / passed / failed / sim failed / no baseline / timed out / warnings" (`dashboard.js:280-303`); Suite chips `PASS n` etc. (`authoring/shell/suite.py:28`). Theme keys `pass, fail, sim-fail, no-ref, warn, timed-out, queued, running, pending, companion` (`reporting/theme.py:45ff`).
- In LIVE mode a status.json "passed" means the simulation ran, not a verdict (`dashboard_render.py:268-279`); the post-run columns are None then.

---

## Rules for every task (the project's build loop)

- Another agent may be alive in the repo: commit ONLY with `git commit -F <your own msg file> -- <explicit paths>`; never `--amend`, never `git stash`, never `git checkout -- <path>` (copy aside and restore bytes to revert). Never push. No `DSTF-NNN` record id in any tracked file.
- Every shell command in the FOREGROUND with `cd /mnt/d/Modelica/DynamicSystemsTestingFramework &&` in the same line; assert any path variable non-empty before `cp`/`rm`.
- Test-first: write the test, run it, SEE it fail for the stated reason, then implement.
- Gates before each commit: the task's tests, `uv run ruff check`, `uv run ruff format --check`; before the last commit also `uv run pytest -m "not playwright" -q`, `uv run pytest -m playwright -q` (two batches if it passes 10 minutes), bare `uv run mypy`. Add `src/dstf/reporting/rows.py` to `[tool.mypy] files` in `pyproject.toml` (the typed tier, D88).
- Commit messages end with the session's attribution lines (the controller gives them).

## File map

| File | Change |
|---|---|
| `src/dstf/reporting/status.py` | Task 1: the live states; `live_status(raw)`; `STATUS_ORDER`. |
| `src/dstf/reporting/rows.py` (new) | Task 2: `TestRow`, `Column`, `COLUMNS`, `summary_fields`, `row_from_status`, `enrich_row`. |
| `src/dstf/reporting/report_suite.py` | Task 2: the sidecar `summary` from `summary_fields`. |
| `src/dstf/reporting/dashboard_render.py` | Task 3: rows from `rows.py`; `_LIVE_STATUS_MAP` and `_enrich_row_from_comparison` move out. |
| `src/dstf/reporting/templates/dashboard.html`, `dashboard.js` | Task 3: columns and pills from the row model. Task 4: filters through `filters.js`; `.*` / `Aa` toggles. |
| `src/dstf/reporting/templates/filters.js` (new) | Task 4: the typed filters. |
| `tests/test_status_vocabulary.py`, `tests/test_rows.py` (new); `tests/test_dashboard_render.py`, `tests/test_dashboard_selection_playwright.py`, `tests/test_dashboard_filters_playwright.py` (new) | as below |

---

### Task 1: One status vocabulary

**Files:** Modify `src/dstf/reporting/status.py`; Test `tests/test_status_vocabulary.py` (new).

- [ ] **Step 1: Failing tests.**

```python
from dstf.reporting import status as st


def test_every_status_has_one_word_and_one_class():
    assert st.STATUS_CLASSES == {
        "PASS": "pass", "FAIL": "fail", "SIM_FAIL": "sim-fail", "NO_REF": "no-ref",
        "QUEUED": "queued", "RUNNING": "running", "TIMED_OUT": "timed-out",
    }


def test_the_word_a_person_reads():
    assert st.STATUS_LABELS["TIMED_OUT"] == "TIMED OUT"
    assert all(st.STATUS_LABELS[s] == s for s in ("PASS", "FAIL", "SIM_FAIL", "NO_REF",
                                                   "QUEUED", "RUNNING"))


def test_a_live_state_before_any_comparison():
    # status.json's words; "passed"/"failed" there mean the SIMULATION ran
    # or did not, not a verdict, so they are not PASS/FAIL
    assert st.live_status("queued") == "QUEUED"
    assert st.live_status("running") == "RUNNING"
    assert st.live_status("timed_out") == "TIMED_OUT"
    assert st.live_status("failed") == "SIM_FAIL"
    assert st.live_status("passed") is None  # simulated, not yet scored


def test_the_order_pills_and_sorting_use():
    assert st.STATUS_ORDER == ("QUEUED", "RUNNING", "PASS", "FAIL", "SIM_FAIL",
                               "NO_REF", "TIMED_OUT")
```

Run: `uv run pytest tests/test_status_vocabulary.py -q` — Expected: FAIL (`STATUS_LABELS` / `live_status` undefined; `STATUS_CLASSES` has four keys).

- [ ] **Step 2: Implement** in `status.py`, beside the four verdicts:

```python
#: The states a test is in before it has a verdict, from a live run's
#: status.json. A test that simulated and is not yet scored has no word of
#: its own: the dashboard shows it as its live row says until the compare
#: phase writes its verdict.
QUEUED = "QUEUED"
RUNNING = "RUNNING"
TIMED_OUT = "TIMED_OUT"

#: Every status, in the order the count pills and a sort by status use.
STATUS_ORDER = (QUEUED, RUNNING, PASS, FAIL, SIM_FAIL, NO_REF, TIMED_OUT)

#: Status → the CSS class both HTML surfaces style it with. One table, so
#: the dashboard, the report header and the tool cannot drift into two
#: names for one state.
STATUS_CLASSES = {
    PASS: "pass", FAIL: "fail", SIM_FAIL: "sim-fail", NO_REF: "no-ref",
    QUEUED: "queued", RUNNING: "running", TIMED_OUT: "timed-out",
}

#: Status → the word a person reads. Only TIMED_OUT differs from its key.
STATUS_LABELS = {s: s for s in STATUS_ORDER} | {TIMED_OUT: "TIMED OUT"}

_LIVE = {"queued": QUEUED, "running": RUNNING, "timed_out": TIMED_OUT,
         "failed": SIM_FAIL}


def live_status(raw: str) -> str | None:
    """A live status.json state as a status, or None for "passed": the
    simulation ran and the verdict is the compare phase's to give."""
    return _LIVE.get(raw)
```

Keep `STATUS_CLASSES` a single definition (replace the four-key one; `classify_with_class` still indexes it). Run Step 1: PASS; `uv run pytest tests/test_report_status.py -q`: PASS.

- [ ] **Step 3: Commit** "status: one vocabulary — the verdicts and the live states, their words and classes".

---

### Task 2: The row model

**Files:** Create `src/dstf/reporting/rows.py`; Modify `src/dstf/reporting/report_suite.py:515-556`; Test `tests/test_rows.py` (new).

- [ ] **Step 1: Failing tests** (`tests/test_rows.py`). Build comparisons with the helpers `tests/test_report_suite.py` uses for its summary tests (~L96, L431-433; grep them):

```python
from dstf.reporting.rows import COLUMNS, TestRow, enrich_row, row_from_status, summary_fields


def test_the_columns_both_surfaces_show():
    assert [c.key for c in COLUMNS] == [
        "test", "ref", "model", "status", "resolution", "worker", "elapsed",
        "nrmse", "vars", "warnings", "translate", "sim", "total", "message",
    ]
    kinds = {c.key: c.kind for c in COLUMNS}
    assert kinds["status"] == "status"
    assert {k for k, v in kinds.items() if v == "number"} == {
        "elapsed", "nrmse", "vars", "warnings", "translate", "sim", "total"}


def test_a_live_row_before_any_comparison():
    row = row_from_status({"test_key": "test_0001", "model_id": "Lib.A",
                           "status": "running", "worker_id": 2, "elapsed": 3.25,
                           "report_dir": "ref_0007", "field_sources": {"stop_time": "annotation"}})
    assert row.status == "RUNNING" and row.ref_id == "ref_0007"
    cells = {c.key: c.text(row) for c in COLUMNS}
    assert cells["worker"] == "W2" and cells["elapsed"] == "3.2"   # "%.1f"
    assert cells["nrmse"] == "—" and cells["resolution"] == "annotation"


def test_a_simulated_but_unscored_live_row_keeps_its_live_word():
    row = row_from_status({"test_key": "t", "model_id": "Lib.A", "status": "passed"})
    assert row.status is None and row.status_text == "SIMULATED"


def test_summary_fields_are_the_sidecars_numbers(sample_comparison):   # fixture: see Step 1 note
    s = summary_fields(sample_comparison, stats={"timing": {"sim_wall": 1.5}})
    assert s["status"] in ("PASS", "FAIL", "SIM_FAIL", "NO_REF")
    assert s["sim_wall"] == 1.5 and s["n_vars"] == len(sample_comparison.variables)


def test_a_stale_or_foreign_summary_does_not_enrich():
    row = row_from_status({"test_key": "t", "model_id": "Lib.A", "status": "passed"})
    assert enrich_row(row, {"model_id": "Lib.B", "status": "FAIL"}, start_wall=None) == row
    assert enrich_row(row, {"model_id": "Lib.A", "status": "FAIL", "written_at": 1.0},
                      start_wall=2.0) == row
    assert enrich_row(row, {"model_id": "Lib.A", "status": "FAIL", "written_at": 3.0},
                      start_wall=2.0).status == "FAIL"


def test_display_text_and_sort_value():
    row = TestRow(model_id="Lib.A", test_key="t", status="PASS", worst_score=1.23456e-5,
                  n_vars=4, n_vars_passed=3, n_unscored=0, n_warnings=0)
    col = {c.key: c for c in COLUMNS}
    assert col["nrmse"].text(row) == "1.2346e-05" and col["nrmse"].sort(row) == 1.23456e-5
    assert col["vars"].text(row) == "3/4" and col["vars"].sort(row) == 3
    assert col["status"].text(row) == "PASS" and col["status"].sort(row) == 2  # STATUS_ORDER index
    unscored = TestRow(model_id="Lib.B", test_key="u", status="FAIL", n_unscored=2)
    assert col["nrmse"].text(unscored) == "n/a (2 unscored)" and col["nrmse"].sort(unscored) is None
```

`sample_comparison`: a pytest fixture in `tests/test_rows.py` building a small `TestComparison` the way `tests/test_report_suite.py` does. `row.status is None` for a simulated-not-scored live row, and `status_text` then reads "SIMULATED" (a word shown only in a live dashboard, between "the simulation ran" and "the compare phase scored it"; it is not a status, so it is not in `STATUS_ORDER`). Run: FAIL (no module).

- [ ] **Step 2: Implement `rows.py`.** The contract (write it with docstrings in the house style):

```python
@dataclass(frozen=True)
class TestRow:
    model_id: str
    test_key: str | None = None
    status: str | None = None            # a status.py status, or None: simulated, not scored
    ref_id: str | None = None
    ref_file: str | None = None           # file:// URI of the reference JSON
    report_dir: str | None = None
    field_sources: Mapping[str, str] = field(default_factory=dict)
    worker_id: int | None = None
    elapsed: float | None = None
    started_wall: float | None = None
    worst_score: float | None = None
    n_unscored: int | None = None
    n_vars: int | None = None
    n_vars_passed: int | None = None
    n_warnings: int | None = None
    translation_wall: float | None = None
    sim_wall: float | None = None
    total_wall: float | None = None
    message: str | None = None
    edits: int = 0                        # the tool's staged edits on this test; 0 in a report

    @property
    def status_text(self) -> str: ...     # STATUS_LABELS[status], or "SIMULATED"
    @property
    def status_class(self) -> str: ...    # STATUS_CLASSES[status], or "simulated"
    @property
    def resolution(self) -> str | None: ...  # one source's name, "mixed", or None


@dataclass(frozen=True)
class Column:
    key: str
    label: str
    kind: Literal["text", "number", "status"]
    text: Callable[[TestRow], str]              # the cell as a person reads it; "—" when absent
    sort: Callable[[TestRow], float | str | None]  # None sorts last in both directions
    title: str | None = None                    # the header's tooltip


COLUMNS: tuple[Column, ...] = (...)   # the 14 of test_the_columns_both_surfaces_show, in that order
```

Formats, exactly as the dashboard renders them today (`dashboard.html:231-254`): elapsed `"%.1f"`, worst NRMSE `"%.4e"` else `"n/a (N unscored)"` when `n_unscored` else `"—"`, vars `"p/n"` sorting on `n_vars_passed`, warnings the count (the dashboard colours a non-zero one), translate / sim / total `"%.2f"`, worker `"W{n}"`, resolution the one source or `"mixed"`, message as is. A text column's `sort` is the lower-cased text; the status column's is its `STATUS_ORDER` index (None → last). Move the resolution rule's tooltip text (`dashboard.html:184`) into `Column.title`.

`summary_fields(comp, *, warnings=None, stats=None, ref_id=None, ref_file=None, field_sources=None) -> dict`: exactly the keys `report_suite.py:515-556` writes today, computed the same way (`worst_finite_score`, `classify_with_class`), with `status` added (the classifier's word) beside `status_text` / `status_class`. `report_suite.py` then writes `context["summary"] = {"model_id": ..., "written_at": ..., **summary_fields(...)}`: ONE computation for the sidecar and, in S2, the tool.

`row_from_status(entry: dict) -> TestRow`: `dashboard_render.py:239-286`'s per-entry logic (live `ref_id` from a `report_dir` starting `ref_`), with `status = live_status(entry["status"])`.

`enrich_row(row, summary, *, start_wall) -> TestRow`: `_enrich_row_from_comparison`'s two guards and its stderr warnings, returning `dataclasses.replace(row, ...)` with the summary's fields (the sidecar's `worst_nrmse` becomes `worst_score`; read `status` when present, else map the sidecar's `status_text` back through `STATUS_LABELS` — sidecars written before this plan carry only `status_text`, and code that reads files tolerates a missing field).

Run Step 1: PASS; `uv run pytest tests/test_report_suite.py -q`: PASS (the sidecar's keys are unchanged plus `status`).

- [ ] **Step 3: Commit** "reporting: one row model — a test's row, its columns, their text and sort values".

---

### Task 3: The dashboard renders the row model

**Files:** Modify `src/dstf/reporting/dashboard_render.py`, `templates/dashboard.html`, `templates/dashboard.js`; Test `tests/test_dashboard_render.py`, `tests/test_dashboard_selection_playwright.py`.

- [ ] **Step 1: Failing tests** in `tests/test_dashboard_render.py`:
  1. the rendered `<thead>` has one `<th>` per `COLUMNS` entry (plus the selection column) with `data-key` = the column key and the label, in `COLUMNS` order;
  2. every `<tr>` of the body carries `data-<key>` (filter text) and `data-sort-<key>` for EVERY column — in particular `data-resolution` (the bug);
  3. the pills are rendered from `STATUS_ORDER` with `STATUS_LABELS` words ("PASS", "TIMED OUT", …) plus a "warnings" cross-cut pill, and `data-pill` values are the status classes;
  4. a live snapshot row with status "passed" reads `SIMULATED`.
  Update the assertions the research listed that pin the old words and keys: `tests/test_dashboard_render.py:52-58, 540-541, 591-592, 640` (status words stay PASS/RUNNING/FAIL; classes unchanged), `:99-104` (`worst_nrmse` stays the SIDECAR's key; the context row now exposes the `TestRow`), `:365` (`PILL_TO_STATUSES` goes — pills now carry their class), `:383-385, 402-403` (header and filter attributes as generated), `:771-772, 841-869` (Resolution, "n/a (2 unscored)"). Run: FAIL.
- [ ] **Step 2: Implement.** `build_dashboard_context` returns `"rows": [TestRow, ...]` and `"columns": COLUMNS` (keep the other keys); `_LIVE_STATUS_MAP` and `_enrich_row_from_comparison` are deleted (their logic is `rows.py`'s). `dashboard.html` loops: header `{% for c in columns %}<th data-sort="{{ 'num' if c.kind == 'number' else 'text' }}" data-key="{{ c.key }}"{% if c.title %} title="{{ c.title }}"{% endif %}>{{ c.label }}…`; filter row one input per column with a placeholder by kind (`*Pipe*` for text, `>1e-4` for numbers, the status column's cell mirrors the pills); each `<tr>` gets `data-<key>="{{ c.text(row) }}"` and `data-sort-<key>="{{ sortval }}"` for every column (a Jinja filter or a context helper that renders None as the value the JS sort puts last). Cells: `c.text(row)`, keeping today's links (test key → its dir, ref → `ref_file`, model → `reports/<report_dir>/interactive.html`) and the warn / unscored styling — generate those three special cells by `c.key` in the template, everything else from `c.text`. `dashboard.js`: the pills render from a `STATUSES` array the template injects (`[["pass","PASS"],…]` from `STATUS_ORDER`), a pill filters rows whose `data-status` equals its class; "warnings" stays the cross-cut; counts from rows as today. Keep the live meta-refresh, the progress bar and the selection code unchanged.
  Run Step 1 and `uv run pytest tests/test_dashboard_render.py -q`: PASS; `uv run pytest -m playwright tests/test_dashboard_selection_playwright.py -q`: PASS (update its column-filter visibility test ~L207-230 only if the generated markup moved what it clicks).
- [ ] **Step 3: A browser check** (`tests/test_dashboard_selection_playwright.py` or a new `tests/test_dashboard_filters_playwright.py`): render a final-mode dashboard with two rows of different resolutions; typing `annotation` in the Resolution filter leaves the annotation row visible (it hid EVERY row before). Revert Task 3's template loop for the `data-*` attributes (copy aside, restore after) and see it fail.
- [ ] **Step 4: Commit** "dashboard: rendered from the row model — one column list, the verdict words on the pills; the Resolution filter works".

---

### Task 4: One typed-filter module, `filters.js`

**Files:** Create `src/dstf/reporting/templates/filters.js`; Modify `dashboard.html` (inline it before `dashboard.js`, as the dashboard inlines its scripts today — check how `dashboard.js` is included and do the same), `dashboard.js` (`patternMatcher` → `DstfFilters`); Test `tests/test_dashboard_filters_playwright.py` (new).

- [ ] **Step 1: Failing browser tests.** Load a page that includes `filters.js` alone (write it to `tmp_path` with a `<script>` of the file's text) and evaluate in Chromium:

```python
CASES = [
    # (kind, query, regex, case, value, expected)
    ("text", "Pipe", False, False, "Fluid.Pipes.GenericPipe", True),     # substring, any case
    ("text", "pipe", False, True,  "Fluid.Pipes.GenericPipe", False),    # Aa on
    ("text", "*Pipe", False, False, "GenericPipe", True),                # glob, anchored
    ("text", "*Pipe", False, False, "GenericPipe_Test", False),
    ("text", "x[1]", False, False, "unitTests.x[1]", True),              # brackets literal
    ("text", "^Gen.*Test$", True, False, "GenericPipe_Test", True),      # .* on
    ("text", "(", True, False, "anything", None),                        # bad regex → None (marked bad)
    ("number", ">1e-4", False, False, 2e-4, True),
    ("number", ">1e-4", False, False, 1e-4, False),
    ("number", ">=1e-4", False, False, 1e-4, True),
    ("number", "<5", False, False, 3, True),
    ("number", "1..10", False, False, 10, True),                         # inclusive
    ("number", "1..10", False, False, 10.5, False),
    ("number", "0", False, False, 0, True),                              # bare number: equal
    ("number", ">", False, False, 1, None),                              # bad → None
    ("number", ">1e-4", False, False, None, False),                      # a missing value never matches
]
```

`DstfFilters.compile(kind, query, {regex, case})` returns `null` for an empty query (no filter), a predicate, or throws `DstfFilters.BadFilter` for a query it cannot read (the test maps a throw to `None`). Run: FAIL (no file).
- [ ] **Step 2: Implement `filters.js`** as one IIFE defining `window.DstfFilters = {compile, BadFilter}`. Text: regex on → `new RegExp(q, case ? '' : 'i')` searched; else a `*`/`?` makes an anchored glob with every other character escaped (so `[ ]` is literal — the rule the spec's `variables` list and the tool's Python `_glob` use); else substring; case off lower-cases both. Number: `^(>=|<=|>|<|=)?\s*(num)$` or `^(num)\s*\.\.\s*(num)$`, `num` = JS float syntax incl. exponents; a missing (`null`/`''`) value never matches a number filter. Status: not a typed filter — the pills.
  Wire `dashboard.js`: the per-column inputs compile through `DstfFilters.compile(columnKind, q, toggles)`; an input whose query throws gets a `bad` class (red outline, `title` = why) and filters nothing; two toolbar toggles `.*` and `Aa` (buttons with `aria-pressed` and tooltips "Regex in every column filter" / "Match case in every column filter") re-compile every filter and persist with the other filter state. Number columns get filter inputs too (they had none).
  Run Step 1: PASS. Add one end-to-end dashboard test: `>1e-5` in Worst NRMSE hides the rows below it; `.*` on then `^Lib\.A$` in Model keeps one row; a bad regex marks the input and hides nothing.
- [ ] **Step 3: Commit** "dashboard: one typed-filter module — text by substring, glob or regex, numbers by comparison or range".

---

### Task 5: Record it

- [ ] `docs/decisions.md`: D136 — "One row model and one vocabulary for the dashboard and the Suite page (2026-09-26)": the owner's ruling, `rows.py` / `COLUMNS` / `STATUS_*`, `filters.js`, the Resolution bug, "SIMULATED" as the live word for a simulated-unscored row, and that the tool will consume the same model in S2. `CLAUDE.md`: in the reporting line of Project Structure add `rows.py` (the row model both HTML surfaces render) and `templates/filters.js`. Full gates; commit.
- [ ] The controller runs an executing review of this plan BEFORE Task 1 (building every task in an archive) and a mutation review after; the real-library check is part of S2's gate (the dashboard over a COPY of TRANSFORM's work dir: 331 rows, the same verdicts, every column's text identical to today's except the pills' words and the Resolution filter working).
