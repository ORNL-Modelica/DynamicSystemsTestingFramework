# DESIGN.md — the DSTF visual system

One system for both tiers: the read-only report (`dstf run --report`, the
dashboard) and the authoring tool (`dstf author`). Every value here is lifted
from what the report already ships in
`src/dstf/reporting/templates/dashboard.html` / `interactive.html` and from
`src/dstf/figures/plotly.py`, except where a line says **corrected**. The
report defines the language; the tool renders it through NiceGUI, which is
Quasar (Material Design components) plus Tailwind utilities. So the tool
chooses among Quasar variants and states. It never invents a control.

Status: the owner chose this Material chrome over a neutral alternate on the
design canvas on 2026-09-15 (brief §3.9). It is the default theme.

## Themes

Every neutral, the accent and the status tints are CSS variables named as in
the tables below, on `:root` in the shared stylesheet and mirrored in the
tool's Quasar palette. A theme is one file that sets those variables; the
default theme is this document. Dark mode is a theme. The status fills and
darks, the baseline-role chips and the plot series keep their values in every
theme, because they carry meaning; a theme may only change what sits behind
them, and each plot layout template (background, grid, text) follows the
theme. The theme is chosen in `testing.json` (`"theme"`) and can be switched
in the tool for the session.

## Colors

Neutrals (page and text)

| Token | Value | Use |
|---|---|---|
| page | `#fafafa` | page background, sticky chrome |
| surface | `#ffffff` | cards, tables, plots, panels |
| surface-hover | `#f5f5f5` | hover on rows and counters; table headers |
| border | `#dddddd` | cards, tables, chrome dividers |
| border-hairline | `#eeeeee` | inside cards; plot frames |
| text | `#333333` | body |
| text-secondary | `#666666` | status bar, detail cells, hints |
| text-muted | `#888888` | labels, unscored, queued |
| text-disabled | `#999999` | reference values |

Accent, one only: `#1976D2` (links, sort markers, selection ring, the primary
button). Hover `#1565C0`. Selection tint `#e3f2fd`. Never a second accent.

Status. The words are the classifier's (`reporting/status.py`), the colours the
dashboard's. Each status has a **fill** (filled badge, bar segment), a **dark**
text colour, and a **tint** row background.

| Status | fill | dark | tint | border (tinted pill) |
|---|---|---|---|---|
| pass | `#4CAF50` | `#2E7D32` | `#f1f8e9` (rows) / `#e8f5e9` (pill) | `#a5d6a7` |
| fail | `#f44336` | `#C62828` | `#ffebee` | `#ef9a9a` |
| sim-fail | `#d32f2f` | `#B71C1C` | `#ffebee` | `#ef9a9a` |
| no-ref | `#FF9800` | `#E65100` | `#fff8e1` | `#ffe0b2` |
| warnings / advisory | `#ffc107` | `#8a6d00` | `#fff3cd` | `#ffc107` |
| timed-out | `#9C27B0` | `#6A1B9A` | `#f3e5f5` | `#e1bee7` |
| queued | `#888888` | `#555555` | `#f5f5f5` | `#dddddd` |
| running | `#2196F3` | `#1976D2` | `#e3f2fd` | `#90caf9` |
| pending / info | `#607D8B` | `#607D8B` | `#eceff1` | `#b0bec5` |

The tinted pill's border is the fill's Material 200 shade; the dashboard
ships only pass and fail, the rest are derived the same way.

Baseline roles (chips in the overlay picker): soft_check `#e8d8f3` on
`#5e3a80`, border `#c4a8df`; companion `#d8f3e4` on `#2b6b48`, border
`#a8dfc0`.

Plot series (the figure layer; literal hex in figures, never CSS variables)

| Series | Value |
|---|---|
| actual | `#2196F3`, solid |
| reference | `#FF9800`, dashed |
| pass region (tube, box) | fill `rgba(76,175,80,0.15)`, line `rgba(76,175,80,0.6)` |
| fail region | fill `rgba(244,67,54,0.15)`, line `rgba(244,67,54,0.8)` |
| window | fill `rgba(33,150,243,0.08)`, line `rgba(33,150,243,0.4)` |
| error axis and traces | `#f44336` |
| muted / out-of-window | `rgba(117,117,117,0.35)` |
| drag handle | ring `#2e7d32`, fill `rgba(46,125,50,0.25)` |
| scalar lines | `#7B1FA2` dot · `#1976D2` dash · `#388E3C` dashdot |
| live (running) trace | actual colour; the unfinished end carries a marker in `#2196F3` |

## Typography

Stack, **corrected**: `"Segoe UI", Roboto, system-ui, -apple-system,
sans-serif`. The shipped stack ends in `monospace` with no generic
sans-serif, so a Linux browser without Segoe UI or Roboto renders the report
in monospace. Plotly text stays Plotly's default (Open Sans, 12px).

Mono, for values, paths and model IDs where alignment matters:
`ui-monospace, Consolas, "Cascadia Mono", monospace`.

| Role | Size / weight | Notes |
|---|---|---|
| page title (h1) | 20px / 600 | "DSTF" wordmark is this size, weight 700 |
| section (h2) | 16px / 600 | `#555`, hairline below |
| body, controls | 14px / 400 | Quasar's base |
| table cells, detail | 12px / 400 | the dashboard's `0.85em` |
| labels, captions | 10px / 400 | uppercase, `#888`, letter-spacing 0.03em |
| status badge | 10.5px / 700 | uppercase, letter-spacing 0.03em |
| stat value | 18px / 600 | counters and key stats |
| numbers in tables | mono 12px | right-aligned |

Sentence case everywhere: "Save 3 spec edits", never "SAVE". Every Quasar
button carries `no-caps`.

## Spacing, shape, elevation

- Page padding 24px. Card padding 16px. Table cell padding 4px 8px (5px 8px
  in the per-test report).
- Gaps: 8px inside a control group, 12px between groups, 16px between cards,
  24px between page sections.
- Radius: 4px cards, counters, panels; 3px badges and buttons; 2px inputs.
- Borders, not shadows: 1px `border` on every surface that sits in the page.
  Surfaces that float over it (menus, popovers, dialogs, the fixed action
  footer) carry one shadow, `0 2px 8px rgba(0,0,0,0.1)`, which is also what
  Quasar's menu and dialog ship with.
- Counters keep their 4px left border in the status fill. That is the
  dashboard's existing signature and stays limited to counters.
- Control heights: dense everywhere. Buttons 28px, inputs and selects 32px,
  table rows 28px, test-list rows 32px. Plots 320px tall, full width.
- Motion: 0.1s background transitions on hover. Nothing else animates
  except the running progress bar.

## Components

**Buttons.** Three tiers, each a Quasar variant. Filled primary
(`unelevated color=primary`): the one action the page exists for, at most
one per header, usually Save. Outlined secondary (`outline`): actions on a
selection (Rerun, Accept). Flat tertiary (`flat`): navigation and
housekeeping (Export, Discard, the dashboard link). Destructive actions are
outlined in `fail` and confirm.

A button's label names its object, its effect and a count: "Save 3 spec
edits (2 tests) to test_spec.json", "Rerun 2 selected tests (Dymola)",
"Accept 2 results as baselines". A tooltip says when to use it. A count of
zero disables the button and the label still says what it would do.

**Disabled states explain themselves.** A greyed control keeps its label and
gets one line beside or below it: why it is unavailable and what enables it
("Rerun needs a selection", "locked while this test is running"). Never a
bare grey button.

**Status badges.** Two forms with two meanings. The filled badge (white text
on the status fill) is a *test's* verdict: test list, header, queue. The
tinted pill (dark text on the tint, 1px border) is a *variable's* or a
*criterion's* verdict, inside a test. An advisory (warn-wrapped) failure is
the amber chip, never red.

A pill says which variable it is about only when the card cannot: a plot of
one series has that variable in its title, so its pill is the bare status,
while a plot of several carries one pill per tracked series reading "x FAIL"
— the name in the case the test wrote it in, not the pill's uppercase.

The criteria tree's closed line says the same thing once each: the root's
pill, what the root is (`and · 4 criteria`), the tally (`3 pass · 1 fail`,
plus an advisory and an unscored count when either is non-zero), then each
failure by name as the button that jumps to its criterion — at most two, with
"+k more" for the rest. The tally does not repeat the names, and a name
appears once.

**Tables** for homogeneous rows the user sorts and filters: the test list,
the queue, criteria points. Sticky header, sortable columns, a filter row,
status tint on the row. **Key-stat cards** for a handful of named numbers.
**Cards** with a 1px border for every other grouping.

**Tabs: none.** Pages and collapsible sections replace them. The brief's
placement model attaches capabilities at suite / test / variable / criterion
instead of a tab strip. A control that wants a tab needs a stated reason.

**Criteria rows** under each plot follow the old per-test page: a tinted
pill, `metric · variable` in 14px 600, the readout in mono 12px, then `+`,
wrap and `−` as flat icon buttons on the right. The readout is the CLI's own
criterion line, which already carries the score ("NRMSE 1.531e-01 vs
tolerance 1.000e-02 → FAIL"), so no number is printed twice. Clicking the row
opens its editor inline, indented 16px, with a 3px `accent` left rule, and the
plot above becomes its editing surface. One criterion open at a time.

The editor opens with one line saying how to use it — "Drag a ring on the
plot, or edit the fields. Edits are staged as you make them; Save writes
them." — and "Done" at the right of that line, the way out. Its fields sit in
ONE wrapping row with the mode select first, so the plot and its editor fit a
screen together; a multi-line field (a JSON list) takes a line of its own at
the end of the row rather than where it was declared. A list table (control
points, checkpoints, events, peaks) scrolls inside the editor after eight
rows; only the rows scroll, so "Add row" below them is never scrolled out of
reach.

**The Variables drawer.** The Test page's left rail holds two drawers, Tests
and Variables, one open at a time; open, a drawer is 320px and the plots
narrow, since they are fluid. The Variables drawer lists every variable in
the last result, grouped by dot prefix, tracked first, diagnostics last, with
a filter box: substring match with no wildcard, glob with `*` and `?`, square
brackets literal because `T[1]` is a name; regex (`.*`) and match-case (`Aa`)
toggles at the right of the box, off by default. A checkbox per row means
"on the selected plot"; hovering a row offers "new plot"; small text names
the other plots a variable is on. The selected plot carries the 2px accent
inset ring. Indexed names fold into one row, `pipe.T[1..20]`, with an
indices field in Modelica slice syntax (`1:5:20`) or a list.

**Array families on a plot.** Series from one array share a hue and ramp in
lightness by index, low to high (`#90caf9` `#42a5f5` `#1976D2` `#0d47a1` for
the accent hue); the legend folds to one entry; hover names the index. A
second array on the same plot takes another hue, and so does a series that
belongs to no array: green `#a5d6a7` `#66bb6a` `#388e3c` `#1b5e20`, then
purple `#ce93d8` `#ab47bc` `#7b1fa2` `#4a148c` (Material 200 / 400 / 700 /
900, as the accent ramp is), then the list cycles. A lone series takes its
hue's third stop. A family is the names that differ in their last integer
index (`pipe.T[3]`, `pipe.medium[3].T`). On a plot of several series a
reference is dashed in its series' colour and stays out of the legend.

**Plots are modular.** A test page is a list of plots; a plot is a list of
series; a series is a variable name from the result plus an optional
expression (later: `T - 273.15`, `p / 1e5`, a unit label). The default layout
is one plot per tracked variable. Users add and remove plots, add series to a
plot, or separate them. A plot has a `kind`; only the time plot exists now,
and profile, x-y, heat-map and 3-D kinds are reserved on the same model. A tracked variable's criteria rows follow it onto
whichever plot shows it; a plot-only series has no criteria until it is
tracked. The legend sits above the plot area and holds the signals: actual,
reference and any overlays. Secondary axes appear only while a series on them
is visible; a second unit family on one plot gets one. A plot of several series shows no overlays and no error traces — both
belong to a plot of one variable — and says so where it greys them.

*As built:* a plot's toolbar is the **Error** select, where the plot draws one
series, and **Focus** — `Showing 1 of N plots: <name> — Show all N plots` on
the accent tint above it, the focused plot's own button reading "Focused". The
error series is chosen by the select, one at a time, with its axis; a
criterion's drawing is shown or hidden by the swatch on its criteria row; the
overlays are in the figure as hidden traces, and the legend toggles them. The
wheel scrolls the page, never the plot: plots are stacked full-width, and a
plot that ate the wheel stopped the page dead (the report, one figure to a
page, keeps wheel zoom). Series chips, "Set window" and Remove plot are
scheduled — "Set window" with the rest of the gesture work, the plot-layout
controls with the Variables drawer.

**Icons.** Material Icons in the tool (Quasar ships them). In mockups, inline
stroke SVG on a 16px grid. Never emoji.

**Header.** Off-white, 1px `border` below, sticky. Left: "DSTF" wordmark,
library name, backend. Centre: the queue indicator. Right: pending-edit count
with Save / Discard / Export, then Stop server. The header is never a solid
accent bar.

## Do / don't

- Do keep the report and the tool byte-identical in colour and type; one
  stylesheet feeds both where they share markup.
- Do put numbers in mono and right-align them.
- Don't add a colour that is not in the tables above; derive tints from the
  status fill.
- Don't grey a control without its sentence.
- Don't use emoji, gradients, or drop shadows on cards.
