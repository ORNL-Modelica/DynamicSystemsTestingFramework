# Reporter QA checklist

Manual QA pass for the interactive reporter. Most data-contract, render,
and state-mutation scenarios are now automated in Python + Playwright
(`tests/test_interactive_playwright.py`); this checklist covers the
remaining ~20% that requires human judgment — visual quality, drag-based
plot interactions, and UX feel on real reports.

**What's automated** (skip these in manual QA):

- Page renders without JS console errors (Playwright `pageerror` handler).
- Variable section dedup, full-tree + per-variable mounts, leaf activation,
  ESC deactivation, click-switch between leaves.
- Scalar input edits → `leafState`, cross-mount input sync, window inputs
  round-trip, `buildPatchData` emits correct ops.
- `+` adds a leaf (via prompt), `−` marks `structureDirty` + emits
  wholesale `/metrics` replace.
- Tube editor activates with control-point table; add-point button commits.
- Window brush button injected on leaf activation.
- Overlay loading (missing file → `status="missing"`, bad parse → `"invalid"`).

**What stays manual** — everything below.

## Prep

```bash
uv run dstf --config examples/fmu/testing.json run --report
# Open the report in your browser:
# testing_output/fmu/FMPy/linux/reports/ref_0001/interactive.html
```

The FMU example (BouncingBall) exercises: NRMSE leaf, range leaf, nested
`warn` wrapper, soft_check overlay (`experiment`). Two variables (`h`,
`v`) produce two plots.

For the full UX picture, also run once with a dev-registered companion:

```bash
cat > /tmp/analytical.csv <<'EOF'
time,h
0.0,1.0
1.0,0.6
2.0,0.2
3.0,0.0
EOF
uv run dstf --config examples/fmu/testing.json companion add \
  BouncingBall analytical /tmp/analytical.csv
# ...then `run --report` again to exercise the overlay picker.
# Cleanup when done: companion remove BouncingBall analytical
```

## Visual quality (human-only)

- [ ] Layout isn't cramped or overflowing at typical widths (~1400 px).
      Variable sections are clearly separated; the full-tree panel at top
      doesn't feel cluttered when it has 4+ leaves.
- [ ] Plot axis labels + tick marks readable; legend placement not
      overlapping traces.
- [ ] Colors hold their meaning across the report:
      - Actual: blue (`#2196F3`)
      - Reference: orange dashed (`#FF9800`)
      - Tube polygon: green-tinted fill
      - Range min/max lines: red dashed
      - Window x-band highlight: blue-tinted
      - Soft_check overlay: purple dotted
      - Companion overlay: green dashdot
      - Active leaf highlight: blue left border, light blue background
- [ ] Pass/fail pills visually distinct and legible (green vs red).
- [ ] Node tree indentation clear enough to see combinator nesting
      (warn wrapper should visibly contain its child leaf).

## Range autoscale edge case (known issue)

- [ ] Range leaf with a min below the data's own range — e.g., set `min`
      to `-5` on BouncingBall's `h` leaf. The red dashed line should still
      be visible. **Known**: Plotly's y-axis autoscale doesn't include
      layout shapes, so the line may clip off the bottom of the plot. If
      you have to double-click the plot to zoom out, file that as a visual
      polish item — not a blocker.

## Drag-based plot interactions (hard to test synthetically)

- [ ] **Tube Shift+click adds a point.** Activate a tube leaf (click its
      header), hold Shift, click somewhere on the plot above or below the
      reference trajectory. A new row should appear in the control-point
      table with the clicked `(time, |Δ from ref|)` values. The tube
      polygon should redraw live to include the new point.
- [ ] **Range drag.** Activate a range leaf. Click-and-drag one of the
      dashed red min/max lines vertically. On drop, the line snaps to
      the new y-value; the leaf's `min_value` / `max_value` text input
      updates to match. Verify the export-patch JSON now has the new value.
- [ ] **Window brush.** Activate any leaf. Click the `🔲 Set window from
      plot` button in its editor slot; the button turns orange ("Drag on
      plot…"). Drag a horizontal range on the plot. On release, the
      window_start + window_end inputs populate; the blue x-band
      highlight appears across the selected range. Verify the
      export-patch JSON shows the window `add` op.

## Soft_check / companion overlay visuals

Requires a registered companion (see Prep). On BouncingBall's `h` plot:

- [ ] `Overlays (N)` summary at the top of the report lists every
      soft_check + companion, loaded or not.
- [ ] Missing / moved companion files show with a yellow-highlighted row
      in the summary + a `note` column explaining why.
- [ ] Each plot with at least one matching overlay shows an `Overlays:`
      picker row above it with role-colored badges (soft_check purple,
      companion green).
- [ ] Toggling an overlay checkbox shows/hides its trace on the plot
      (default off → `visible: 'legendonly'` → `true` on check).
- [ ] Overlays only appear on plots whose variable they carry data for —
      a companion with only `h` data doesn't show a picker on the `v` plot.

## Structural editing (UX feel, not correctness)

Playwright covers correctness. These check the UX:

- [ ] `+` button prompts for metric + variable in sequence. The prompt
      text names valid metrics. If the user types an unknown metric, the
      error message is clear.
- [ ] `−` button asks for confirmation before removing. Confirmation
      text includes the node's label ("Remove leaf nrmse·h?").
- [ ] After a structural edit, the full tree re-renders AND the plots
      update. Per-variable mounts reflect the new structure.
- [ ] Export JSON (click "Download JSON") shows a single `/metrics`
      replace op when structure has changed, not dozens of scalar ops.
- [ ] Applying the downloaded patch:
  ```bash
  uv run dstf --config examples/fmu/testing.json \
    spec-update ~/Downloads/spec_patch.json
  ```
  - CLI exits 0 with the op applied.
  - `examples/fmu/test_spec.json` reflects the structural change.
  - Hand-authored sibling keys (description, metadata) on the test entry
    survive unchanged.

## Schema export (one-off sanity)

- [ ] `uv run dstf export-schema` emits valid JSON-Schema
      2020-12 to stdout.
- [ ] `$defs` contains entries for all six modes.
- [ ] Pass it to any JSON-Schema validator; parses without errors.

## Known limitations / deferred (don't file as bugs)

- Pass/fail pills are CLI-authoritative across ALL modes — live recompute
  was retired in Stage 2 along with `MODE_SCORERS`. Edits in the browser
  don't flip pills until `dstf run` is re-invoked. The
  `cli_authoritative` badge only appears on `event-timing` and
  `dominant-frequency`, but the semantics apply everywhere now. Expected.
- Shift+drag / Shift+right-click on existing tube control points (move /
  delete via plot interaction) not yet wired — only Shift+click-to-add
  works on the plot today. Table inputs + row − buttons are the other
  edit paths.
- Window brush is one-shot per activation: drag once, set, brush mode
  exits. No "keep selecting" lock.
- `+` button only adds leaves. Wrapping a subtree in a new combinator
  (e.g., `warn`) requires hand-editing the JSON spec.
- Drag-to-edit range handles are input-driven ↔ drag two-way synced, but
  Plotly's editable-shape drag can feel laggy on very zoomed-in plots.
