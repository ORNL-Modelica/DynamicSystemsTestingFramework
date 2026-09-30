# The Statistics Card (step U3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Test page shows every statistic the run and its baseline recorded, side by side, in a card above the criteria tree — collapsed to one line naming the rows that changed; one click on a row's `+` adds a `scalar` criterion on that statistic; the card is where a statistics criterion is edited; a count the parser never wrote because its list was empty reads as 0.

**Architecture:** One pure row model, `reporting/stats_rows.py::statistics_rows(...)`, built from a run's and a baseline's `statistics` dicts, is what BOTH tiers render: the report's Statistics table and the tool's new `StatsCard`. A row's "changed" is one rule — the criterion its `+` would add would fail — computed by the scalar mode itself over the seed `comparison/seeds.py::seed_quantity` writes. The zero-count rule lives in one small module (`backends/common/statistics.py`) that the Dymola parser writes through and the scalar resolver and the row model read through. The card is a VIEW of the one tree (Q1 b): a row whose quantity a leaf reads shows that leaf, and its editor opens under the row.

**Tech Stack:** Python 3.10+, NiceGUI 3.16 (`[author]`), Jinja report templates, pytest + pytest-asyncio, Playwright + Chromium, uv.

**Owner rulings this plan implements:**
- Grill 2026-09-28 (Q1 b, Q6) and the plot-model board (Q2 A): a Statistics card, run vs baseline, collapsed by default to one line naming the changed rows; its `+` adds a requirement — counts must match exactly, times get a relative tolerance inside `warn` — landing at the END of the root; the card is a view of the one tree. The board drew it above the Criteria card: columns Statistic / Run / Baseline / (a `+`, or the criterion's pill for a scored row).
- 2026-09-29 — **a row is "changed" when the criterion its `+` would add would fail:** a count on any difference, a time beyond the same relative tolerance the `+` seeds (10%). Not the report's "any difference".
- 2026-09-29 — **expanded, the card lists EVERY numeric statistic** the run or the baseline recorded, under Translation / Simulation / Timing (then any other section) sub-headings, as the report's table does.
- D142 (built): CPU time is wrapped in `warn` only when it lands in an `and` — a `warn` child under an `or` / `k-of-n` would pass its parent with nothing else passing. A diagnostic's criterion has no plot; U3's card hosts its editor (D142 "Known gaps", the add-form plan's B6).
- The grill's controller call (recorded then): a missing zero count is read as 0 where the list it summarizes exists; new runs always write the key ("code that writes never leaves a field out").

**Controller's calls (said to the owner in the report; reversible):**
- (S1) The card omits list- and text-valued statistics (`translation.nonlinear`, `translation.state_names`): nothing scores them, and their numeric summaries (`nonlinear_count` / `_total` / `_max`, `continuous_time_states`) are rows beside them. The REPORT keeps showing them (its rows of kind `text`, changed by the report's old string rule — the owner's rule speaks of rows a click can score).
- (S2) A top-level statistic (`CPUtime`, `EventCounter`, folded in from the diagnostics) is a Simulation row whose quantity path is its bare name (`"CPUtime"` — it resolves from the statistics dict on both sides, including a baseline stored without diagnostics). A leaf reading `"CPUtime.final"` (what U2's add form writes) is the SAME row: the row matches both spellings.
- (S3) A row with no value on one side (a statistic the baseline never recorded) is never "changed"; its `+` still adds (an accept then records it — the same "rerun/accept" path as U2's new variables).
- (S4) A leaf reading a statistic no row holds (a typo, a statistic neither side recorded) gets its own row at the end, in a section "Other scored quantities", its values `—`: every statistics criterion has a home on the card.
- (S5) Time vs count by path (Task 2's table), never by the value's type: `translation.*` / `simulation.*` counts are ints, but a Dymola `result_points` can be written as a float.
- (S6) The collapsed line names at most three changed rows, then `+N more`; a count reads `0→3`, a time `+38%` (`+0.12 s` when the baseline is 0).
- (S7) The report's Statistics table and its Key stats adopt the same "changed" rule (one row model): otherwise the report tints a time the card calls unchanged.

**Not in U3:** U4 (move a subtree). The structural-warning check (`comparator._check_structural_changes`, which skips a statistic missing on either side) stays as it is — log it in `docs/ideas.md`.

**Evidence (read at 89c8402, from a read-only code map):**
- `backends/dymola/log_parser.py:236-240, 250-254, 272-276, 285-289` write `<key>_count/_total/_max` only `if values:`; an empty `{ }` stores `nonlinear: []` and nothing else (`tests/test_backends.py::test_translation_log` asserts it).
- `backends/common/result_variables.py:221-238 fold_diagnostics_into_statistics` puts top-level `CPUtime` / `EventCounter` (finals) into the statistics; OpenModelica records `timing.{frontend,backend,simcode,templates,compile,simulation,total}` and folds diagnostics; FMPy / Julia / Python record `simulation.wall_time`; every backend gets `timing.{translation_wall,sim_wall,other_wall,total_wall}` (`backends/base.py:196-209`).
- Stored: `storage/reference_store.py:276-293` writes the whole `statistics` dict and `diagnostics` summaries; `Baseline.statistics` / `.diagnostics` (`storage/baseline.py:31,39`).
- `comparison/tree_eval.py:514-557 _resolve_scalar_path` (dotted walk into statistics, then `<diag>.final|min|max`; statistics win); `:572-579 _dotted_lookup`; `:729-755 _missing_scalar_path_comparison` — a missing path HARD-FAILS on purpose ("a stats path that silently scored as 0 would be a green regression test measuring nothing"): the zero rule must stay narrow.
- `comparison/algorithms.py:989 _compare_scalar`: `rel` passes when `|act-ref| <= tol*|ref|`, falling back to absolute when `ref == 0`.
- `comparison/seeds.py:196-230 seed_diagnostic` (COUNTS / TIMES by name, warn only `into == "and"`); `authoring/builtin/edit_tree.py:228-272` `act("add")` (root combinator → `/metrics/children/-`; root leaf → `and` of both; the `into` kind).
- `reporting/context_builder.py:56-88 _build_stats_section` (changed = formatted strings differ, both non-empty; rows keep no path), `:295-348 _build_statistics_sections` — MUTATES its input (`ref_cat` IS `ref_stats["simulation"]`; the `setdefault` loop writes CPUtime into it); `:542-641 _build_key_stats`; `interactive.html:211-220, 298-311` (changed rows `background: var(--dstf-warn-tint)`).
- `authoring/shell/test_page.py:122-164 TestPage.build` (`TreeCard` at :136), `:415-433 _reveal_in_tree` (B6: the only home a statistics leaf has today), `:443-467 open_criterion` (`self.open = (index, path)` — plot cards only), `:868-909 sync`, `:911-956 _gone`; `tree_card.py` (the card pattern: `refresh(view)`, `toggle`, `forget`, `rows_by_path`, the closed `_line`); `plot_card.py:843-895 open_editor` / `close_editor` (the `InlineHost(Placement.CRITERION, partial(Selection, ...), figure=..., on_applied=...)` pattern); `builtin/edit_criteria.py:62-182 EditCriteria.panel` (works with `figure=None`; its `set_metric` reseeds `{"reduce": "final"}` — wrong for a stats path; it shows the window fields, which a stats path ignores); `authoring/forms.py:108` renders `quantity` as raw JSON.
- `authoring/session.py`: `project.results[model_id]` (`.statistics`, `.diagnostics`), `_reads_for(model_id).ref` (:632-664, cached — must not be mutated), `tree_view` (:970), `stage` (:1011). No statistics accessor exists.
- FakeLib (the Python backend) records only `simulation.wall_time` + `timing.*`; a test needing Dymola-shaped statistics sets `result.statistics` and the reference's `statistics` itself (`tests/test_test_page.py::_drawer_project` injects diagnostics the same way).

---

## Rules for every task (the project's build loop)

- Another agent may be alive in the repo: commit ONLY with `git commit -F <your own msg file> -- <explicit paths>`; never `--amend`, never `git stash` (not even in a one-liner), never `git checkout -- <path>`. Never push. No `.soul/` record id in a tracked file.
- FOREGROUND commands with `cd /mnt/d/Modelica/DynamicSystemsTestingFramework &&`; each under 600 s (the not-playwright suite in three chunks: `tests/test_test_page.py` alone and two lists of the rest); `uv run --no-sync`, never `uv sync`; kill only PIDs you started, never `pkill -f`; never port 8765; screenshots under the scratchpad; never `dstf run` / `compare` against a real work dir.
- Test-first; named mutants applied and restored (cmp); commit before running mutants, and the mutant script refuses a dirty tree.
- A UI change: a real browser, thumbnails read (`~/.claude/skills/ue-windows-wsl/scripts/render_check.py thumb`). A design choice not written here: stop and report.
- Gates before each commit: the task's tests, `ruff check`, `ruff format --check`; before a task's last commit also bare `mypy`, the whole not-playwright suite, the Playwright files it touched.

## File map

| File | Change |
|---|---|
| `src/dstf/backends/common/statistics.py` (new) | T1: `summaries(key, values)`, `read_summary(category, key)` — the zero-count rule, written and read in one place. |
| `src/dstf/backends/dymola/log_parser.py` | T1: writes through `summaries` (zeros for an empty list). |
| `src/dstf/comparison/tree_eval.py` | T1: `_dotted_lookup`'s last hop reads through `read_summary`; `_resolve_scalar_path` → public `resolve_scalar_path` (a rename, its own commit). |
| `src/dstf/comparison/seeds.py` | T2: `quantity_kind`, `seed_quantity`; `seed_diagnostic` delegates. |
| `src/dstf/reporting/stats_rows.py` (new) | T3: `StatRow`, `statistics_rows`, `changed`. |
| `src/dstf/reporting/context_builder.py`, `templates/interactive.html` | T3: the report's table and key stats render the rows; the input is no longer mutated. |
| `src/dstf/authoring/session.py`, `authoring/builtin/edit_tree.py` | T4: `Session.statistics`; `act("add", quantity=...)`. |
| `src/dstf/authoring/shell/stats_card.py` (new), `shell/test_page.py`, `shell/style.py` | T5-T6: the card; its editor; the page's one-editor rule and reveal. |
| `src/dstf/authoring/builtin/edit_criteria.py` | T6: a statistics leaf's editor: scalar fixed, no window, no quantity field. |
| docs | T7. |

---

### Task 1: A missing zero count reads as 0 — and new runs write it

**Files:** `backends/common/statistics.py` (new), `backends/dymola/log_parser.py`, `comparison/tree_eval.py`; tests `tests/test_backends.py`, `tests/test_scalar_metric.py`, `tests/test_statistics.py` (new).

```python
# backends/common/statistics.py
SUMMARIES = ("count", "total", "max")

def summaries(key: str, values: list[int]) -> dict[str, int]:
    """`<key>_count/_total/_max` of a system-size list — 0, 0, 0 for an empty
    one. The parser writes these ALWAYS: code that writes never leaves a
    field out (a reader would have to guess what its absence means)."""
    return {
        f"{key}_count": len(values),
        f"{key}_total": sum(values),
        f"{key}_max": max(values, default=0),
    }

def read_summary(category: dict, key: str) -> int | None:
    """A summary a baseline written before `summaries` lacks: 0 where
    `key` is `<list>_count|_total|_max` and `<list>` is present AND EMPTY in
    the same category. Anything else stays missing — a stats path absent
    from a side hard-fails its leaf (tree_eval: a path that silently scored
    0 would be a green test measuring nothing), and only an empty list
    says what its summary is."""
    base, _, field = key.rpartition("_")
    if field not in SUMMARIES or key in category:
        return None
    return 0 if category.get(base) == [] else None
```

The parser's four `if values:` blocks become `stats[category].update(summaries(key, values))`. `_dotted_lookup`: when the walk misses on the LAST part and the node is a dict, return `_as_float(read_summary(node, part))`. Nothing else in the resolver changes.

- [ ] **Step 1: Failing tests.** `test_statistics.py`: `summaries("nonlinear", [])` → three zeros; `[3, 5]` → 2, 8, 5; `read_summary({"nonlinear": []}, "nonlinear_count") == 0`; `read_summary({}, "nonlinear_count") is None` (no list); `read_summary({"nonlinear": [2]}, "nonlinear_count") is None` (a non-empty list without its count is not guessed); `read_summary({"nonlinear": []}, "nonlinear")` is None (not a summary key); a key ending `_after_manipulation_count` resolves against `nonlinear_after_manipulation`. `test_backends.py::test_translation_log`: an empty `{ }` now ALSO stores `nonlinear_count == 0` etc. `test_scalar_metric.py`: a `translation.nonlinear_count` leaf (tolerance 0 abs) over a baseline holding only `"nonlinear": []` and a run holding `nonlinear_count: 0` PASSES; a run with `nonlinear_count: 3` FAILS on a value (not "not found"); a baseline with NO `nonlinear` key still hard-fails `"not found in baseline"`. RED.
- [ ] **Step 2: Implement; green.** Mutants: `read_summary` fills when the list is absent (the "no list" and hard-fail tests fail); fills for a non-empty list; the parser keeps `if values:` (the parser test fails). Commit "statistics: a missing zero count reads as 0 where its list is empty; the parser writes it".
- [ ] **Step 3 (own commit, a rename):** `_resolve_scalar_path` → `resolve_scalar_path` (T3 and T4 import it); update callers and tests. Commit "refactor: resolve_scalar_path is public (the Statistics rows read it)".

### Task 2: What kind a quantity is, and the one seed

**Files:** `comparison/seeds.py`; tests `tests/test_seeds.py`.

```python
#: A statistic is a time by its path (S5): the `timing` section, CPUtime,
#: and the `*_time` / `*_wall` / `cpu_time*` keys; every other number a
#: backend records is a count.
def quantity_kind(path: str, values: np.ndarray | None = None) -> Literal["count", "time"]:
    name, _, field = path.rpartition(".")
    if name and "." not in name and field in ("final", "min", "max"):
        # A diagnostic's summary (`CPUtime.final`): K1's rule — by name
        # when known, else whole samples mean a count.
        if name in COUNTS:
            return "count"
        if name in TIMES:
            return "time"
        finite = values[np.isfinite(values)] if values is not None else np.array([])
        whole = finite.size > 0 and bool(np.all(finite == np.round(finite)))
        return "count" if whole else "time"
    head = path.split(".", 1)[0]
    last = path.rsplit(".", 1)[-1]
    if (
        head == "timing"
        or last in TIMES
        or last.endswith(("_time", "_wall"))
        or last.startswith("cpu_time")
    ):
        return "time"
    return "count"
```

The tests ARE this table:

| path | kind |
|---|---|
| `translation.nonlinear_count`, `translation.continuous_time_states`, `simulation.state_events`, `simulation.result_points`, `simulation.accepted_steps` | count |
| `EventCounter`, `EventCounter.final` | count |
| `CPUtime`, `CPUtime.final` | time |
| `simulation.cpu_time_integration`, `simulation.wall_time` | time |
| `timing.total_wall`, `timing.frontend` (OpenModelica), any `timing.*` | time |
| `Foo.final` with whole samples / fractional samples (a diagnostic of neither known kind) | count / time |

`seed_quantity(path, kind, *, into, variable=None) -> dict`: `{"metric": "scalar", "quantity": path, "tolerance": 0 | 0.1, "tolerance_mode": "abs" | "rel"}` plus `"variable": variable` when given; a `time` landing `into == "and"` is returned inside `{"combinator": "warn", "children": [leaf]}`, bare otherwise. `seed_diagnostic(name, values, *, into)` becomes `seed_quantity(f"{name}.final", quantity_kind(f"{name}.final", values), into=into, variable=name)` — its docstring's reasoning moves onto `seed_quantity`, and it now wraps ANY time in `and` (was: `name in TIMES` only — a diagnostic time of unknown name is a time too; say this change in the commit message).

- [ ] Tests: the table; seed shapes for a count / a time under `and` (warn) / a time under `or`, `k-of-n`, `warn` (bare); `seed_diagnostic` unchanged for CPUtime / EventCounter (the existing `test_seeds.py:330-420` stay green). Mutants: `timing.*` read as count; warn under `or`. Commit "seeds: a quantity's kind by its path; one seed for statistics and diagnostics".

### Task 3: The row model both tiers render

**Files:** `reporting/stats_rows.py` (new), `reporting/context_builder.py`, `reporting/templates/interactive.html` (only if a field name changes); tests `tests/test_stats_rows.py` (new), `tests/test_report_suite.py`, `tests/test_interactive_report.py` (its Playwright statistics test stays green).

```python
@dataclass(frozen=True)
class StatRow:
    section: str            # "translation" | "simulation" | "timing" | other | "other scored"
    key: str                # the key inside its section ("nonlinear_count", "CPUtime")
    path: str               # the quantity path a leaf reads ("translation.nonlinear_count", "CPUtime")
    label: str              # "Nonlinear count" — key with "_" → " ", first letter upper; a top-level name as is
    kind: Literal["count", "time", "text"]
    run: float | str | None
    baseline: float | str | None
    changed: bool
    delta: str              # "0→3" | "+38%" | "+0.12 s" | "" (S6)

SECTION_TITLES = {"translation": "Translation", "simulation": "Simulation", "timing": "Timing"}

def statistics_rows(run: Mapping | None, baseline: Mapping | None, *,
                    run_diagnostics: Sequence = (), baseline_diagnostics: Sequence = (),
                    extra_paths: Iterable[str] = ()) -> list[StatRow]: ...
def changed(path: str, kind: Literal["count", "time"],
            run: float | None, baseline: float | None) -> bool: ...
```

- Sections in the order translation, simulation, timing, then others (as today); top-level scalars (S2) are Simulation rows with `path = key`; inside a section keys in the order they first appear (run then baseline), `timing` in the report's `["translation_wall","sim_wall","other_wall","total_wall"]` order first. Summary keys a side lacks are filled through `read_summary` (T1), so a baseline's empty `nonlinear` gives `nonlinear_count` `0`.
- Numbers are `count` / `time` by `quantity_kind(path)`; lists and strings are `text` rows (S1: the report shows them, the card skips them).
- `changed(path, kind, run, baseline)` (numbers only): `False` when either side is None (S3); otherwise NOT the scalar mode's verdict over the seed — `ScalarMode(ScalarConfig(**params)).compare(np.array([0.]), np.array([baseline]), np.array([0.]), np.array([run])).passed` with `params` = the scalar leaf of `seed_quantity(path, kind, into="and")` (unwrapped from its `warn`) minus `metric` / `variable`. No second copy of the tolerance. A `text` row never calls it: `statistics_rows` sets its `changed` by the report's old rule (formatted strings differ, both non-empty) — the report tints it, the card never shows it.
- `extra_paths`: every path a leaf reads that no row holds becomes an `other scored` row (S4), its values through `resolve_scalar_path(path, statistics, diagnostics)` on each side.
- NO MUTATION: the function never writes into its inputs (a test deep-copies both and compares after).
- `context_builder._build_statistics_sections` becomes a render of `statistics_rows` into the template's existing `{title, rows: [{label, current, reference, changed}]}` shape (`title` = `SECTION_TITLES[...] + " Statistics"` / "Timing" as today; `current`/`reference` formatted as `_format_value` does); `_build_key_stats`'s `changed` for a statistics pick reads the row's `changed`. Delete `_build_stats_section` if nothing else calls it.

- [ ] Tests: a Dymola-shaped pair (a count 0→3 changed; a count equal not; CPUtime 0.88→1.21 changed, 3.91→4.02 wall not; a baseline `nonlinear: []` vs run `nonlinear_count: 3` → changed with `0→3`); a value only on one side → not changed, `delta ""`; `text` rows present; OM `timing.*` and FakeLib's `simulation.wall_time` shapes; the input deep-equality; the report: a time 3% off no longer tinted, a count change tinted, `test_report_suite` / `test_interactive_report` green. Mutants: `changed` true when one side is None; the time tolerance hard-coded separately from the seed (change the seed's 0.1 to 0.2 in a mutant — a row test must fail); the mutation restored (`setdefault` into the input). Commit "statistics rows: one model for the report and the tool; changed = the criterion a click would add would fail".

### Task 4: The Session reads them; EditTree adds one

**Files:** `authoring/session.py`, `authoring/builtin/edit_tree.py`; tests `tests/test_session.py`, `tests/test_builtin_capabilities.py`.

- `Session.statistics(model_id) -> list[StatRow]`: `statistics_rows(result.statistics, reference["statistics"], run_diagnostics=result.diagnostics, baseline_diagnostics=reference["diagnostics"], extra_paths=<every staged-tree leaf's quantity path that reads_a_statistic>)` from `project.results` and the CACHED reference `_reads_for(model_id).ref` — never copied by hand, never mutated (T3 guarantees it; a test asserts the cached dict is unchanged after two calls). No result → the baseline's rows with `run=None`; neither → `[]`.
- `EditTree.act(action="add", path=ROOT, quantity=<path>)`: when `quantity` is given the leaf is `seed_quantity(quantity, quantity_kind(quantity), into=into)` (no variable, no metric input needed); landing is today's `add` at ROOT — a root combinator → `/metrics/children/-` (the END, Q6 b), a root leaf → `and` of both. A `quantity` whose path is a diagnostic's `X.final` goes through `seed_diagnostic` (one seed per path — T2 made them agree). `quantity` together with `variable` raises.

- [ ] Tests: `Session.statistics` over a project whose result/reference carry Dymola-shaped statistics (inject as `_drawer_project` does), ordering, extra paths after a staged typo leaf; the cache unchanged. `act(add, quantity="translation.nonlinear_count")` on an `and` root → one op `add /metrics/children/-` with the count leaf; `quantity="CPUtime"` on an `and` root → the warn-wrapped leaf; on an `or` root → bare; on a root leaf → `add /metrics` with `and` of both. Mutants: lands at `/children/0`; a time bare under `and`. Commit "author: the Session reads a test's statistics; a quantity is added at the root's end".

### Task 5: The card

**Files:** `authoring/shell/stats_card.py` (new), `authoring/shell/test_page.py`, `authoring/shell/style.py`; tests `tests/test_test_page.py`, `tests/test_author_test_page_playwright.py`.

`StatsCard(page)` — the `TreeCard` pattern (`ui.card().classes("dstf-card dstf-stats w-full gap-1").mark("stats")`, a `line` row and a `rows` column; `refresh(view)`, `toggle`, `forget`, `rows_by_path`, `is_open`). Built in `TestPage.build` JUST BEFORE `self.tree = TreeCard(self)`; refreshed in `sync()` beside `tree.refresh(view)`; `forget()` in `_gone()`.

- **Closed line** (default on every page open): toggle chevron · **Statistics** · `run vs baseline ·` then one of: `N changed: <label> <delta>, …` (S6), `all match`, `no run result` (baseline rows only), `nothing recorded` (no rows — the card still shows, one line). Marked `stats:line`.
- **Open:** per section a sub-heading row (`TRANSLATION`, uppercase caption style as the board) then one row per `count`/`time` StatRow (S1 skips `text`): label (tooltip = the path), Run and Baseline right-aligned in the mono font (`1.21 s` for a time — 3 significant figures; a count as an int), the delta muted, and at the end EITHER a `+` icon button (`aria-label` and tooltip: `Add a requirement: exact match` for a count; `Add a requirement: within 10%, as a warning` for a time landing in an `and` root, `…within 10%` otherwise) OR, for a row a leaf reads (the view's leaves where `reads_a_statistic`, matched by path — `X` and `X.final` both match row `X`, S2), that leaf's pill (its verdict: pass / fail / pending, as the tree card's rows draw it) followed by its one-line summary (`exact` / `±10%` / `in [a, b]`). A changed row's background is `var(--dstf-warn-tint)` (the report's token; `theme.py` has it). Rows marked `stats:row:<path>`.
- **`+`:** runs `EditTree` `act(action="add", path=ROOT, quantity=row.path)` through the same outcome path the tree card's forms use (find it in `actions.py` — `InlineHost`'s act → `Session.stage` → the page's `on_applied`/sync; if no entry runs a capability without a form, add the smallest one to `InlineHost` and say which). After it the card stays open and the new criterion's editor opens under its row (Task 6's reveal).
- Refresh cost: the rows are rebuilt only when `(model_id, the rows' paths, which are scored)` changes (the `TreeCard.shape_of` idea); values and pills repaint in place.

- [ ] Tests (`test_test_page.py`, over a project with injected Dymola-shaped statistics): the card sits before the tree card in DOM order; closed by default; the line names `Nonlinear count 0→3` and `CPUtime +38%` and not `Total wall`; open lists every numeric statistic by section, no `text` row; a changed row carries the tint class; `+` on `Nonlinear count` stages the leaf at the root's end and the row then shows the pending pill; `+` absent on a scored row; a staged `CPUtime.final` leaf (U2's add form) shows on the `CPUtime` row; FakeLib's real `simulation.wall_time` + `timing.*` render (nothing injected). Playwright (`_served(..., project=<the injected project>)`): open the card, click `+` on a count row, the tree card's count reads one more criterion; LOOK at 1280 and 900 (thumbnails): the line, the open table, a tinted row, a pill row. Also LOOK at the add form's picker section title (U2's watch item: "ON THISPLOT" at 10 px) and report it. Mutants: the card after the tree card; `text` rows shown; the `+` shown on a scored row. Commit "Test page: the Statistics card".

### Task 6: The card hosts a statistics criterion's editor

**Files:** `authoring/shell/stats_card.py`, `authoring/shell/test_page.py`, `authoring/builtin/edit_criteria.py`; tests `tests/test_test_page.py`, `tests/test_builtin_capabilities.py`, Playwright.

- Clicking a scored row (or its pill) opens that leaf's editor in a slot under the row: `InlineHost(Placement.CRITERION, partial(Selection, tests=[model_id], variable=None, leaf_path=path), figure=None, on_applied=...)` — the `PlotCard.open_editor` pattern, a Done button closing it. One editor page-wide: `TestPage.open` grows to name the card as a host (pick the smallest change to `self.open`'s shape — e.g. `index = STATS` sentinel or `(host, path)` — and say which); opening it closes any plot editor and form row, and opening a plot editor closes it.
- `TestPage._reveal(path)`: a leaf that `reads_a_statistic` is revealed in the card (open it, open its editor, scroll to it) — `_reveal_in_tree` is deleted (its only caller was this case); the tree card's row jump for such a leaf goes to the card too. `test_a_criterion_on_a_diagnostic_is_revealed_in_the_tree` becomes "…is revealed in the Statistics card".
- `EditCriteria.panel` for a leaf that `reads_a_statistic`: the metric is fixed (no mode select — the row names `scalar`; only `accepts_quantity` metrics could score it and only `scalar` does), no window fields, no `quantity` field (the card row names the quantity); tolerance / tolerance_mode / min_value / max_value as today, with U2's values-in-effect hints. `set_metric`'s reseed can no longer run on one.

- [ ] Tests: click a scored row → the editor under it; change the tolerance → one staged `replace` on that leaf; Done closes; opening a plot's editor closes the card's; U2's add form adding `CPUtime` → the card opens with the editor under the `CPUtime` row; the panel for a stats leaf has no mode select / window / quantity field, a `{"reduce": ...}` scalar on a variable keeps them. Playwright: the add form's diagnostic add lands in the card's editor; LOOK. Mutants: the window shown for a stats leaf; two editors open at once. Commit "Statistics card: a statistics criterion is edited under its row".

### Task 7: Record it; the gate

- [ ] `docs/decisions.md` D143 (the rulings; S1-S7; the one "changed" rule and where it lives; the zero-count rule and why it is narrow; what it closes — D142's known gap B6; what it reverses — the report's any-difference tint, `_reveal_in_tree`), `docs/usage.md` (the card), CLAUDE.md (one clause in the `dstf author` block: "a Statistics card above the tree: every recorded statistic, run vs baseline, collapsed to the changed ones; `+` adds a scalar criterion, and a statistics criterion is edited there"), the roadmap (U3 BUILT), `docs/ideas.md` (the structural-warning check skips a statistic missing on one side).
- [ ] The controller: two executing reviews before Task 1 (archives), a mutation review per part and per fix batch, and the TRANSFORM gate over a COPY (`/tmp/claude-1000/gate-U2` or a fresh one — delete old copies first; disk): (1) `statistics_rows` over all 331 tests' last results and baselines — no exception, rows per test, how many tests have ≥ 1 changed row and which paths, the number of stored baselines holding an empty list with no `_count` (the zero rule's reach); (2) the report over the copy — tinted statistics rows before vs after (expected fewer: times), nothing else in the HTML changed but the tint (diff); (3) the tool over the copy: a Test page's card on BranchingDynamicPipes (ref_0038) closed and open, a `+` on a count, the staged leaf scores (compare on the COPY), its editor; LOOK at thumbnails; (4) the owner's files hashed before and after.

---
