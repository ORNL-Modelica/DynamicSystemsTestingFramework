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

- [x] Test counts match the console output (`3 / 3 passed` on FMU
  example, no "stale" indicators unless you've done a filtered rerun).
- [x] Last-run timestamps show in the right column.
- [x] Clicking a test row opens the per-test interactive report.

## Per-test interactive report — all variables

- [x] Page renders without JS console errors (`DevTools → Console`).
- [x] All trajectory plots appear; axes and legends readable.
- [x] Variable table shows one row per leaf/variable with a status pill,
  mode name, score, and the tolerance / control cell.
  - i see one who's "mode" = range. also, if changed to tube the tolerance option is still there though it is greyed out. 
- [x] Summary card at the top reports the correct N-passed count.

## Mode — NRMSE (BouncingBall `h` and `v` — first two rows)

- [x] Tolerance cell contains a numeric input pre-filled with the
  stored tolerance (e.g., `0.001`).
- [x] Changing the input to a smaller value (e.g., `1e-12`) flips the
  status pill + summary count + NRMSE plot's fail-zone band in
  real time.
- [ ] The `.modified` class on the input renders visibly different.
  - i don't know what you mean

## Mode — Range (BouncingBall `h` — third row)

- [x] Cell shows two number inputs labeled Min value / Max value,
  pre-filled with `-0.01` and `1.1`.
- [x] Trajectory plot for this variable shows two dashed-red horizontal
  reference lines at `-0.01` and `1.1`.
    - though y scale was hiding the top. i had to zoom out slightly
- [x] Dragging the Min input down to `-5` visibly lowers the reference
  line in real time. Same for Max.
    - yes but couldn't zoom out when i sent negative value something large like -5
- [x] Changing bounds so the trajectory violates them flips the status
  pill to FAIL with an updated `max_viol` score.

## Mode — Soft_check nrmse against `experiment` (BouncingBall `h` — fourth row)

- [x] Row shows `against=experiment` diagnostic somewhere in the score
  display or alongside it.
    - I see it in an "Overlay" section... don't understand it.
    - i don't see it. also soft_check boolean only impacts first "h" plots (multiple toggle buttons but only impact first instance of h plots)
- [x] Status pill is the test result (PASS by default with the current
  reference data).
- [ ] Verify by tightening the tolerance: the warn-wrapped failure should
  NOT cascade into the test's overall PASS/FAIL — soft_checks are
  advisory. Use the warn-combinator rendering in the metric tree view
  (if present).
    - don't see changes.

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

  ** Only see NRMSE and Tube
  
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

## Window UI round-trip (A1 / idea #46 UI surfacing)

Tree-backed leaves only — BouncingBall is the canonical exerciser
(`metrics` block with 4 leaves, paths `/metrics/children/{0,1,2,3/children/0}`).

### Prep

```bash
# Save the spec so we can restore it after the manual test
cp examples/fmu/test_spec.json /tmp/test_spec.baseline.json
uv run modelica-testing --config examples/fmu/testing.json run --report
# Open: testing_output/fmu/FMPy/linux/reports/ref_0001/interactive.html
```

### Render check

- [ ] Every BouncingBall row (4 total: `h` nrmse, `v` nrmse, `h` range,
  `h` warn) has a `Window:` row with two number inputs (`start`, `end`)
  below the tolerance or mode cell.
- [ ] Window inputs are blank initially (no window authored yet).
- [ ] Non-tree tests (Dahlquist `ref_0002`, VanDerPol `ref_0003`) have
  NO window inputs — they're flat-override and window only applies to
  `LeafSpec`.

### Edit + download

- [ ] On the first row (`h` nrmse), enter `start=0.5`, `end=2.0`. The
  Export Tolerance Config JSON updates live and includes:
  ```json
  {"op": "add", "path": "/metrics/children/0/window",
   "value": {"start": 0.5, "end": 2.0}}
  ```
- [ ] "Download JSON" → `spec_patch.json` on disk.

### Apply + verify

```bash
uv run modelica-testing --config examples/fmu/testing.json spec-update ~/Downloads/spec_patch.json
```

- [ ] CLI reports one op applied.
- [ ] `examples/fmu/test_spec.json` now has `"window": {"start": 0.5,
  "end": 2.0}` on the first child of `metrics.children`. Hand-authored
  keys elsewhere in the file (if any) are byte-unchanged.
- [ ] Re-run `run --report`; reopen `ref_0001/interactive.html`. The
  first row's window inputs are pre-filled with `0.5` / `2.0`.
- [ ] Score for that leaf reflects windowed NRMSE (typically very
  small since BouncingBall passes trivially — window narrows the slice
  but doesn't introduce error).

### Remove path

- [ ] Clear both window inputs on the first row. Export JSON updates to
  include `{"op": "remove", "path": "/metrics/children/0/window"}`.
- [ ] Download + apply; verify `window` key is gone from the spec.

### Restore

```bash
cp /tmp/test_spec.baseline.json examples/fmu/test_spec.json
```

## Companion + soft_check overlay rendering (A2 / idea #50, 6.3 first slice)

BouncingBall has an existing soft_check (`experiment`) and accepts
ad-hoc companion registration for full exercise.

### Prep — synthetic external companion

```bash
cat > /tmp/analytical.csv <<'EOF'
time,h
0.0,1.0
1.0,0.6
2.0,0.2
3.0,0.0
EOF
uv run modelica-testing --config examples/fmu/testing.json companion add \
    BouncingBall analytical /tmp/analytical.csv
uv run modelica-testing --config examples/fmu/testing.json companion list
uv run modelica-testing --config examples/fmu/testing.json run --report
# Open: testing_output/fmu/FMPy/linux/reports/ref_0001/interactive.html
```

### Render check

- [ ] Top of page (just above the Statistics details) shows an
  `Overlays (2) — companion + soft_check` collapsible. Inside:
    - `experiment` — role=soft_check, status=loaded, variables=`h`.
    - `analytical` — role=companion (external), status=loaded,
      variables=`h`.
- [ ] Every `h` trajectory plot (rows 0, 2, 3 in BouncingBall) has an
  `Overlays:` picker above it with two checkboxes:
  `[soft_check] experiment` and `[companion] analytical`.
- [ ] The `v` plot (row 1) has an `Overlays:` picker with ONLY
  `[soft_check] experiment` (analytical's CSV has no `v` column, so
  that entry is correctly suppressed).
- [ ] Both checkboxes start unchecked — overlays are opt-in.

### Toggle behavior

- [ ] Check `experiment`: a purple dotted trace appears on the plot
  labeled `Overlay: soft_check/experiment`. Uncheck: it goes back to
  `legendonly` (the legend entry stays, no data is drawn).
- [ ] Check `analytical`: a green dashdot trace appears labeled
  `Overlay: companion/analytical`. Uncheck: returns to `legendonly`.
- [ ] Both overlays can be on simultaneously without overlapping the
  Actual / Reference traces' visual weight.
- [ ] Non-`h` plots (the `v` row) show no `analytical` option — same
  invariant as the render check, restated since toggle bugs sometimes
  cross rows.

### Graceful degradation — missing companion file

```bash
mv /tmp/analytical.csv /tmp/analytical.csv.moved
uv run modelica-testing --config examples/fmu/testing.json run --report
```

- [ ] Console shows a warning line like `Failed to load companion
  'analytical' ... no such file`. The run still exits 0 (overlays never
  fail a test).
- [ ] Reopen the report. `Overlays` summary now shows `analytical` with
  `status=missing` and the row has a yellow background. The `note`
  column says `file not found: /tmp/analytical.csv`.
- [ ] Picker checkboxes above `h` plots no longer include `analytical`
  (nothing to render — by design).

### Cleanup

```bash
mv /tmp/analytical.csv.moved /tmp/analytical.csv  # restore so next run works
uv run modelica-testing --config examples/fmu/testing.json companion remove \
    BouncingBall analytical
rm /tmp/analytical.csv
```

- [ ] After `companion remove` + `run --report`, the `Overlays`
  summary shows only `experiment`. `analytical` is gone.

## Known gaps / deferred

- Drag-to-edit the range reference lines (v2; today the inputs drive
  the lines one-way).
- Range-brush on the trajectory plot as a visual window editor (stretch
  noted in the A1 handoff; scalar inputs are v1).
- Bulk toggle (single switch that flips every overlay across every
  plot). Today each plot toggles independently.
- Overlay-vs-primary error panel (an analogue of the existing
  reference-error panels, but for overlays). Not scoped.
- JS unit framework / Playwright E2E (D66 Q8 — deferred indefinitely
  unless the reporter becomes a regression source).
