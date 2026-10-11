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
  between signed and absolute error on the right axis "Error [signal units]"
  and per-point NRMSE on its own "NRMSE [–]".
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
- [ ] The error-overlay dropdown's three choices are visibly different
      curves; "none" removes it. Signed and absolute error draw against
      "Error [signal units]", NRMSE against "NRMSE [–]", and each axis
      shows only while a series on it is visible — by the dropdown or by a
      legend click.
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

## Authoring tool

`dstf author` is covered in Python (the shell's regions and the built-in
capabilities through NiceGUI's in-process `user_simulation`) and by two
Chromium smoke tests, neither of which saves: one edits a tolerance, the other
drags a tube handle and a range bound. What stays
manual is the feel of a live edit and the states a single page cannot reach.

Accept writes baselines and Queue simulates: run the steps below against a
throwaway copy of the example library, never a reference tree you would mind
re-accepting.

### Prep

```bash
uv sync --extra dev --extra author --locked
uv run dstf --config examples/fmu/testing.json run --report
uv run dstf --config examples/fmu/testing.json author
```

The second command leaves results and a dashboard for the tool to read; the
third opens the tab. Discard anything staged before quitting, or the spec is
left as you edited it.

- [ ] The tab opens on the project: the header names the library and the
      backend, and the test list carries one row per discovered test with its
      status badge.
- [ ] Click a test: the centre shows the variable picker, a painted figure and
      the metric tree, and the tree's verdicts agree with what the CLI printed.
- [ ] Click a leaf, open Edit criteria, and change the tolerance so the leaf
      fails: the verdict flips, the figure follows, and the header's pending
      count reads 2 (the tree materialized once, plus your edit) — not one per keystroke.
- [ ] Discard: the verdict comes back, the drawer is empty, the count is 0.
- [ ] Stage the edit again and Save: `test_spec.json` carries exactly that
      change and the count is 0. Edit the file in another editor, stage
      something and Save again: the tool refuses and offers to reload.
- [ ] Export writes `<work_dir>/spec_patch.json`, and
      `dstf --config ... spec-update <that file>` applies it.
- [ ] Select two tests: Edit tree is greyed with "select exactly one test",
      and Edit criteria is not listed at all — its scope is a leaf. Uncheck
      them all and the tab area says nothing applies to the selection; no v1
      capability is PROJECT-scope.
- [ ] Select two tests with results and open Accept: each is listed as
      `model · status · will store`, the button reads `Accept 2`. Click it —
      the footer gains one line naming what it stored and where, and both
      tests' verdicts flip to PASS (the tree is scored against the baseline
      just written).
- [ ] Select a test the last run never finished (interrupt a run with Ctrl-C
      to make one): its Accept row reads `re-run before accepting`, the tab is
      greyed with that same reason, and nothing offers to store it.
- [ ] On the Suite page tick one test and click Queue (the toolbar's icon
      whose tooltip is its caption, `Queue 1`): a dialog names the backend
      (`runs through <backend>`) and the test. Click `Queue 1` — the
      simulation queue beside the table (above it under 1200 px) gains row 1:
      its badge and the test's Suite row read QUEUED, then RUNNING with
      `running · N s · worker 1`, then the test's verdict with `finished ·
      N s`; the header's indicator reads `1 running`, then `idle`. The
      console that started `dstf author` printed nothing of the tool's own
      the whole time — a logged warning (Python's last-resort handler) or a
      simulator's own log lines can still reach it.
- [ ] The next three steps need tests that run for seconds: an example FMU
      is done before you can click. Tick three tests and Queue them (`Queue
      3`): the rows are numbered in run order, the first runs, the others read
      `waiting · 1 ahead` and `waiting · 2 ahead`. Remove on a waiting row
      takes it out — the row goes and its Suite row reads its verdict again.
      With the three still ticked, Queue's caption counts what the queue
      holds: `Queue 1 (2 already queued)`.
- [ ] Cancel on a running row stops its simulation: the row reads `cancelled`
      under a CANCELLED badge, the test's Suite row reads CANCELLED, and its
      Test page's strip offers `Queue again`. On a backend that cannot stop a
      test (FMPy — so the example FMUs) a running row has no Cancel: an info
      icon in its place says why.
- [ ] When nothing is queued or running the panel's header counts what ended
      (`2 finished · 1 cancelled`) and `<work_dir>/dashboard.html` is
      rewritten with the tool's verdict for every test — CANCELLED for the
      cancelled one. Clear finished empties the panel and leaves the results.
      ⋮ → Stop workers ends the simulator processes and says `workers
      stopped`; while a test runs it is refused with a sentence.
- [ ] Make Queue fail to start — use a backend whose batch class builds but
      whose persistent probe does not: point `testing.json` at `OpenModelica`
      on a machine with no OMPython (re-sync the venv without `--extra om` if
      it is installed), so the project opens and the queue's
      `select_backend(persistent=True)` is what raises. The dialog says the
      backend's sentence, nothing is queued and every verdict is exactly what
      it was; from then on the Queue button is greyed, with that sentence as
      its tooltip. (A backend that cannot be built at all — an unregistered
      name, a missing executable — never gets that far: `dstf author` refuses
      to open the project, printing one `Config error: ...` line.)
- [ ] With a test queued or running, the header's ⋮ → Stop server asks first
      (`2 tests are queued or running — stop anyway?`); Stop anyway ends the
      tool and leaves no simulator process behind (`ps` shows none).
- [ ] Select BouncingBall with `h` plotted: Edit on plot is enabled (the
      `range` leaf). Zoom into part of the plot, then drag the `range` upper
      bound line down: the pending count rises, the leaf's verdict follows,
      the line stays where you dropped it, and the zoom is unchanged.
- [ ] Select an `nrmse` leaf (BouncingBall's first), switch Edit criteria's
      metric to `tube` with width mode `band`; Edit on plot's
      `tube_points` table for that leaf appears. `Add row` stages a
      zero-width row at the middle of the trajectory; type an `upper` and
      a `lower` width and two green rings separate on the plot. Drag the
      upper ring up by its centre: the band widens there, one pending op
      for that leaf (not one per drag), zoom kept.
- [ ] Grab a ring slightly off its centre and drag: it is resized, then
      snaps back, and nothing is staged.
- [ ] Select Dahlquist and drag one of its checkpoint tolerance boxes: the
      box snaps back, and the Edit on plot tab shows the leaf path with
      `cannot be moved on the plot`. Drag a checkpoint ring that has no
      `value` in the table straight up, with no sideways movement: nothing is
      staged (a visible sideways jitter is a time move, and stages the new
      time).
- [ ] In a table, change a cell, then `Add row` and `Remove row`: each
      stages, and the drawer reads "leaf → new leaf". Clear a required
      cell (a tube row's `time`): the cell is marked `time is required`, and
      nothing is staged until it is filled.
- [ ] Save: `test_spec.json` carries the dragged and table edits along
      with the rest of what the drawer listed, and nothing it did not.
- [ ] The dashboard link opens the suite page in a new tab.
- [ ] Quit shuts the server down; the tab shows NiceGUI's disconnected banner.

## Schema export (one-off sanity)

- [ ] `uv run dstf export-schema` emits valid JSON-Schema
      2020-12 to stdout.
- [ ] `$defs` contains entries for every registered mode.
- [ ] Pass it to any JSON-Schema validator; parses without errors.
