# Reporter QA checklist

Manual QA pass for the per-test report page. The page is read-only: it
mutates nothing and computes nothing, so correctness of what it draws is
covered in Python (figure goldens per mode) and in Playwright
(`tests/test_interactive_report.py`, which asserts the kept behaviours on
pages generated through the real render path). This checklist covers what
those cannot judge — visual quality and feel on a real report.

**What's automated** (skip these in manual QA):

- Page renders without JS console errors (Playwright `pageerror` handler).
- Every variable plot paints from its own figure dict, with the trace count
  the dict declares and the criterion shapes the metric emitted.
- Zoom, hover and legend toggles are live; the overlay picker toggles a
  soft_check and a companion trace; the error-overlay dropdown switches
  between signed, absolute and per-point NRMSE on the right axis.
- The metric tree renders read-only, with the CLI verdict, score text and
  detail pairs per leaf; no input element anywhere in it.
- The decimation banner names the decimated traces with their sample counts.
- Overlay loading (missing file -> `status="missing"`, bad parse -> `"invalid"`).

**What stays manual** - everything below.

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
- [ ] Pass/fail pills visually distinct and legible (green vs red).
- [ ] Node tree indentation clear enough to see combinator nesting
      (warn wrapper should visibly contain its child leaf).

## Read-only page (viewing checks)

- [ ] Every variable has a plot and it is painted (no empty chart div, no
      console error). Nothing on the page offers to change a criterion:
      no inputs, no `+` / `-` buttons, no download.
- [ ] Hover a trace: the tooltip names the trace and reads the value at
      that time. Zoom into a region and double-click to reset; the axes
      come back including any declared bound that sits outside the data.
- [ ] The overlay picker only appears above plots whose variable an
      overlay carries data for, and each checkbox shows/hides that trace.
- [ ] The error-overlay dropdown draws on the right-hand axis and its
      three choices are visibly different curves; "none" removes it.
- [ ] The metric tree reads top-down: combinator nesting is clear (a
      `warn` wrapper visibly contains its child), each leaf shows its
      verdict, its one-line criterion and its detail pairs, and the
      summary line above agrees with the leaves below it.
- [ ] Where a trace was decimated, the banner is present, names the trace
      and its full -> embedded counts, and points at `comparison_data.json`.
      Where nothing was decimated, there is no banner.

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

## Schema export (one-off sanity)

- [ ] `uv run dstf export-schema` emits valid JSON-Schema
      2020-12 to stdout.
- [ ] `$defs` contains entries for every registered mode.
- [ ] Pass it to any JSON-Schema validator; parses without errors.
