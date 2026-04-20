# Reporter QA checklist

Manual click-through scenarios for the interactive HTML reporter (D66 Q8
testing strategy). Run before releases; the auto tests cover the data
contracts exhaustively but only a human can judge whether the UX feels
right on a real report in a real browser.

## Prep

```bash
uv run modelica-testing --config examples/fmu/testing.json run --report
open testing_output/fmu/FMPy/linux/reports/index.html
```

The FMU example is the reference fixture — it exercises three of the six
modes (nrmse, range, warn-wrapped soft_check nrmse) plus the tube editor
in the Dymola variant.

## Index page

- [ ] Test counts match the console output (`3 / 3 passed` on FMU
  example, no "stale" indicators unless you've done a filtered rerun).
- [ ] Last-run timestamps show in the right column.
- [ ] Clicking a test row opens the per-test interactive report.

## Per-test interactive report — all variables

- [ ] Page renders without JS console errors (`DevTools → Console`).
- [ ] All trajectory plots appear; axes and legends readable.
- [ ] Variable table shows one row per leaf/variable with a status pill,
  mode name, score, and the tolerance / control cell.
- [ ] Summary card at the top reports the correct N-passed count.

## Mode — NRMSE (BouncingBall `h` and `v` — first two rows)

- [ ] Tolerance cell contains a numeric input pre-filled with the
  stored tolerance (e.g., `0.001`).
- [ ] Changing the input to a smaller value (e.g., `1e-12`) flips the
  status pill + summary count + NRMSE plot's fail-zone band in
  real time.
- [ ] The `.modified` class on the input renders visibly different.

## Mode — Range (BouncingBall `h` — third row)

- [ ] Cell shows two number inputs labeled Min value / Max value,
  pre-filled with `-0.01` and `1.1`.
- [ ] Trajectory plot for this variable shows two dashed-red horizontal
  reference lines at `-0.01` and `1.1`.
- [ ] Dragging the Min input down to `-5` visibly lowers the reference
  line in real time. Same for Max.
- [ ] Changing bounds so the trajectory violates them flips the status
  pill to FAIL with an updated `max_viol` score.

## Mode — Soft_check nrmse against `experiment` (BouncingBall `h` — fourth row)

- [ ] Row shows `against=experiment` diagnostic somewhere in the score
  display or alongside it.
- [ ] Status pill is the test result (PASS by default with the current
  reference data).
- [ ] Verify by tightening the tolerance: the warn-wrapped failure should
  NOT cascade into the test's overall PASS/FAIL — soft_checks are
  advisory. Use the warn-combinator rendering in the metric tree view
  (if present).

## Mode — Tube (ModelicaTestingLib — Dymola-side)

*(Requires a Dymola run; skip on FMU-only QA passes.)*

- [ ] Cell shows `→ See tube editor below plot` (not duplicate inputs).
- [ ] Rich tube editor below the plot works: add/remove points,
  synced/unsynced toggle, rel/band/absolute width modes.
- [ ] Shift+click on the plot adds a point. Shift+drag moves it.
  Shift+right-click removes it.

## Mode — Final-only

- [ ] Tolerance cell behaves like NRMSE (numeric input + live recompute).
- [ ] Score cell shows `|err| <value>`.

## Mode — Event-timing / Dominant-frequency

*(Not exercised by the default examples; construct a fixture if needed.)*

- [ ] Cell shows the auto-derived panel inputs + `CLI-authoritative`
  badge.
- [ ] Edits to the inputs do NOT immediately recompute — status pill
  stays on the CLI-computed value.

## Export / round-trip (6.4)

- [ ] "Export Tolerance Config" section shows a JSON-Patch envelope:
  `{"model": "...", "patch": [...]}`.
- [ ] Empty patch (no user edits) produces `{"model": "...", "patch": []}`.
- [ ] Editing any input populates the `patch` array with matching
  `{"op": "replace", "path": "/comparison/...", "value": ...}` entries.
- [ ] "Copy to Clipboard" and "Download JSON" both produce the same
  text (filename: `spec_patch.json`).
- [ ] Apply the downloaded patch:
  ```bash
  uv run modelica-testing --config examples/fmu/testing.json spec-update spec_patch.json
  ```
  Verify:
    - CLI prints the ops applied with paths.
    - `test_spec.json` gets the scalar change.
    - Any hand-authored `description` / `metadata` / `info` keys on the
      entry or the `comparison` block survive unchanged.

## Schema export (6.4.5)

- [ ] `uv run modelica-testing export-schema` prints valid JSON-Schema
  (draft 2020-12) to stdout.
- [ ] The `$defs` section contains entries for the six modes, plus
  `leaf`, `combinator`, `tree_node`, `test_entry`.
- [ ] Feed the output to any JSON-Schema validator of choice; it
  should parse without errors.

## Baseline-role management CLIs (D66 — orthogonal to the HTML reporter)

- [ ] `uv run modelica-testing --config examples/fmu/testing.json soft-check list`
  lists soft_checks registered on BouncingBall (`experiment` should
  appear post-migration).
- [ ] `uv run modelica-testing --config examples/fmu/testing.json migrate-baselines`
  on a repo with no legacy flat-baselines is a no-op.
- [ ] `companion add <model> <name> <csv-path>` succeeds without
  reading the file; `companion list` shows it as `kind=external`;
  `companion freeze <model> <name>` copies the data sibling;
  `companion list` now shows `kind=frozen`.

## Failure modes

- [ ] Patch with an out-of-whitelist path exits nonzero and does NOT
  mutate the spec. Try:
  ```json
  {"model": "BouncingBall", "patch": [
    {"op": "replace", "path": "/simulation/stop_time", "value": 100}
  ]}
  ```
- [ ] Patch that would produce an invalid metric tree (e.g., a
  `against: soft_check` leaf outside `warn`) is rejected by the
  validator hook inside `cmd_spec_update` and the spec is NOT written.

## Known gaps / deferred

- Drag-to-edit the range reference lines (v2; today the inputs drive
  the lines one-way).
- Time-window UI controls on every mode panel (auto-derive doesn't
  yet surface `LeafSpec.window_start/end` — they parse from JSON but
  there's no in-browser field for them). See idea #46 follow-up.
- JS unit framework / Playwright E2E (D66 Q8 — deferred indefinitely
  unless the reporter becomes a regression source).
