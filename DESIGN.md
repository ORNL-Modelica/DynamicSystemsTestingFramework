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
tool's Quasar palette. A theme is a NAME of a bundled table that sets the
fifteen tokens below (`reporting/theme.py`); there are two, `default` — this
document's values — and `dark` (D148). No theme file, no inline colours.

What a theme may change is what sits BEHIND meaning. The status fills, the
filled badge (white on the fill), the baseline-role chips and the plot series
keep their values in every theme, with two exceptions on `dark`, both because
the light value does not read there:

- A status's tint, pill, pill border and the text on them are DIMMED: the
  fill laid at 14 % (tint), 24 % (pill) and 60 % (border) over the dark
  surface, and the status's pastel — the light table's pill border; for
  warnings the lighter amber `#ffd54f` — as the text. The light tints would
  be light islands on a dark page.
- Five plot colours under 2.2:1 on the dark plot are lightened ("Plot series"
  below). No other series colour moves.

Each theme has one plot layout template in the figure layer
(`figures/palette.py`): the plot's ground, grid, zero line, axis lines, ticks
and titles, legend, hover label and toolbar. `default` has none — its plot is
plotly.js's own. The figure's chrome follows the theme too: a reference
event's guide line, an open criterion's ring and handle markers, the grey of a
context series.

The theme is chosen in `testing.json` (`"theme"`): the dashboard and the
report pages are generated in it, with no switch in those pages, and the tool
opens in it. In the tool it can be switched per browser tab, for the session,
from the header's ⋮.

## Colors

Tokens (neutrals, accent, selection)

| Token | default | dark | Use |
|---|---|---|---|
| page | `#fafafa` | `#121212` | page background, sticky chrome |
| surface | `#ffffff` | `#1e1e1e` | cards, tables, plots, panels |
| surface-hover | `#f5f5f5` | `#2a2a2a` | hover on rows and counters; table headers |
| surface-active | `#ececec` | `#353535` | hover on a control that rests on surface-hover |
| border | `#dddddd` | `#484848` | cards, tables, chrome dividers |
| border-hairline | `#eeeeee` | `#303030` | inside cards; plot frames; the dark plot's grid |
| text | `#333333` | `#e4e4e4` | body |
| text-secondary | `#666666` | `#b8b8b8` | status bar, detail cells, hints |
| text-muted | `#888888` | `#9e9e9e` | labels, unscored, queued; a context series |
| text-disabled | `#999999` | `#7d7d7d` | reference values |
| accent | `#1976D2` | `#64b5f6` | links, sort markers, selection ring, the primary button |
| accent-hover | `#1565C0` | `#90caf9` | the accent, hovered |
| selection | `#e3f2fd` | `#1d3a57` | the selection tint |
| shadow | `rgba(0,0,0,0.1)` | `rgba(0,0,0,0.5)` | elevation |
| on-accent | `#ffffff` | `#0b1a2b` | text on a FILLED accent (the primary button) |

One accent only; never a second. The dark neutrals are neutral grey, not
blue-tinted, and the dark page is Quasar's own dark page. `on-accent` is a
token of its own because on a dark surface no one blue both reads as link
text and carries white text: white on the dark accent is 2.2:1.

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
ships only pass and fail, the rest are derived the same way. These are the
`default` values; on `dark` the fill column is the same and the other three
are dimmed as "Themes" says (pass: tint `#243225`, pill `#29412a`, text
`#a5d6a7`).

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

On `dark` (`figures/palette.py`; contrast on the dark plot `#1e1e1e`):

| What | default | dark |
|---|---|---|
| soft-check overlay (the dotted line above) | `#7B1FA2` (2.0) | `#ba68c8` (4.7) |
| blue ramp, darkest stop | `#0d47a1` (1.9) | `#1264e2` (3.1) |
| green ramp, darkest stop | `#1b5e20` (2.1) | `#237b2a` (3.1) |
| purple ramp, third stop | `#7b1fa2` (2.0) | `#a631d8` (3.2) |
| purple ramp, darkest stop | `#4a148c` (1.4) | `#8a40e4` (3.1) |
| drag handle (chrome) | ring `#2e7d32` | ring `#66bb6a`, fill `rgba(102,187,106,0.25)` |
| reference event line (chrome) | `#757575` | `#a8a8a8` |
| context series (chrome) | `#888888` | `#9e9e9e` — the theme's text-muted |

A ramp stop is lightened at its own hue and saturation until it clears 3:1 and
still stands darker than the stop before it; the eight other stops are the
same in both themes.

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
housekeeping (Discard; the header's ⋮ menu holds Export, Change backend…,
the dashboard and Stop server as menu items). Destructive actions are outlined in `fail` and
confirm.

A button's label names its object, its effect and a count: "Save 3 spec
edits (2 tests) to test_spec.json" — "(2 tests, 1 new entry)" when a save
creates an entry — "Rerun 2 selected tests (Dymola)",
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
inset ring. Indexed names fold into one row, `pipe.T[1..20]`. The indices
field in Modelica slice syntax (`1:5:20`) or a list, first drawn on this row,
is the add form's and the folded criteria row's (the owner's ruling of
2026-10-08, D152).

*As built:* the rail is 40px of labelled buttons, "Tests" and "Vars" (its
tooltip "Variables"); the open drawer's button sits on the selection tint.
The drawer pushes the plots narrower at every width, never overlays them, and
the page never scrolls sideways. Which drawer is open, and the Tests drawer's
filter, are remembered per browser tab, so a switch of test keeps the drawer
open. The Tests drawer is the Suite page's list without checkboxes: grouped
by package, short names, status badges, the open test's row on the selection
tint, and a filter of its own (the Suite page's chips and toggles stay on the
Suite page). A click opens that test in the same tab; pending edits survive
the switch.

The Variables drawer adds and takes off series and makes new plots, acting on
the *selected plot*: the first, until a click on a card outside its plot area
selects another — its toolbar, its title, its margin, or a criteria row, whose
click also opens its editor. A criterion's swatch and its `+` / wrap / `−` do
not select, and the plot area keeps its zooms and handle drags. Add series
selects its plot and opens the drawer. The target line, "Checkboxes act on
plot 2 of 3", is a tag on the selection tint with the selected card's ring
inside it; a click on it scrolls that card into view. With no plot it reads
"No plot yet: + new plot adds one" and the checks are grey. Groups: Tracked,
then each dot prefix (the component before any bracket: `port[2, 1].C[1]` is
in "port"), then "(top level)", then Diagnostics. A family is one row reading
`pipe.T[1:3]` with a swatch of four blocks, one per stop of the accent ramp,
light to dark and never a gradient — whatever hue a plot draws it in — and a
chevron that opens it to its members, each a row with its own check, at most
50, then "… N more". A row's check is a button with `role=checkbox`,
`aria-checked` `true` / `mixed` / `false` for all, some or none of its members
on the selected plot, and the row's text as its `aria-label`, with the muted
name after it in parentheses when the row shows one (`a.Q (x)`); it is not a
checkbox, whose repaint would stage an edit. A click adds whatever the plot
lacks (a mixed row adds), or takes the row off when the plot holds all of it;
taking off a plot's last series removes the plot. "+ new plot" shows on the
row's hover and while focus is in the row; it puts the row on a new plot below
the selected one, selects that plot, and leaves the variable where it was.
Other plots holding the row read "plot 1, 3" at its right. A variable a
criterion scores cannot leave its last plot: the check greys, and its tooltip
is the one sentence Remove plot uses too — "x is scored
by 1 criterion and this is the only plot that shows it. Add it to another plot
first, or remove its criteria." (in the plural for several). The filter keeps
a variable whose name or label matches: a substring; with `*` or `?`, a glob
matched against the whole text with brackets literal, as a spec's `variables`
list reads it, and matched without backtracking; with `.*` on, a Python
regular expression that may match anywhere, and a bad one is said under the
box. At most 200 rows are drawn, an opened family's members aside, then
"Showing 200 of 1,843 rows — narrow the filter". The drawer's row has no
indices field: it is in the add form and in the folded criteria row's editor
(D152).

**Array families on a plot.** Series from one array share a hue and ramp in
lightness by index, low to high (`#90caf9` `#42a5f5` `#1976D2` `#0d47a1` for
the accent hue); the legend folds to one entry; hover names the index. A
second array on the same plot takes another hue, and so does a series that
belongs to no array: green `#a5d6a7` `#66bb6a` `#388e3c` `#1b5e20`, then
purple `#ce93d8` `#ab47bc` `#7b1fa2` `#4a148c` (Material 200 / 400 / 700 /
900, as the accent ramp is), then the list cycles. A lone series takes its
hue's third stop. A family is the names that differ in their last integer
index (`pipe.T[3]`, `pipe.medium[3].T`), whatever comes before that index:
`massPorts[4, 1].C[3]` folds on its 3. A two-dimensional index after it
(`a[1].b[2,3]`), or as the last index (`x[2,3]`), makes no family. The card's
title, its muted names line, its plot-only line and the focus bar fold a
family as the report's plot titles do (`pipe.T[1:3]`). On a plot of several
series a reference is dashed in its series' colour and stays out of the
legend.

**Plots are modular.** A test page is a list of plots; a plot is a list of
series; a series is a variable name from the result plus an optional
expression (later: `T - 273.15`, `p / 1e5`, a unit label). The default layout
is one plot per tracked variable. Users add and remove plots, add series to a
plot, or separate them. A plot has a `kind`; only the time plot exists now,
and profile, x-y, heat-map and 3-D kinds are reserved on the same model. A tracked variable's criteria rows follow it onto
whichever plot shows it; a plot-only series has no criteria until it is
tracked. The legend sits above the plot area and holds the signals: actual,
reference and any overlays. Secondary axes appear only while a series on them
is visible; a second unit family on one plot gets one. A plot of several
series shows no overlays and no error traces — both belong to a plot of one
variable — and says so where it greys them.

*As built:* a plot's toolbar is the **Error** select, where the plot draws one
series, and **Focus** — `Showing 1 of N plots: <name> — Show all N plots` on
the accent tint above it, the focused plot's own button reading "Focused". The
error series is chosen by the select, one at a time, with its axis; a
criterion's drawing is shown or hidden by the swatch on its criteria row; the
overlays are in the figure as hidden traces, and the legend toggles them. The
wheel scrolls the page, never the plot: plots are stacked full-width, and a
plot that ate the wheel stopped the page dead (the report, one figure to a
page, keeps wheel zoom). After Focus come **Add series**, which selects the
plot and opens the Variables drawer on it, and **Remove plot**, grey with the
drawer's sentence when it would leave a scored variable on no plot; its click
does not select the card, and the selection stays on the same plot (the one
now in the removed plot's place, when the removed one was selected). There
are no series chips (the owner's ruling): the Variables drawer does every
other layout edit. **Reset layout** ends the tree card's closed line while
the test's effective entry has a `plots` key, and stages removing it. Every
layout edit stages ONE op on the entry's `plots` key, the first on a test
without the key writing out the default layout with its change, and a run of
them reads as one pending edit (D134 names the one pair that stays two); a
refused edit is a notification with the reason, and nothing moves. "Set
window" is scheduled with the rest of the gesture work.

**Icons.** Material Icons in the tool (Quasar ships them). In mockups, inline
stroke SVG on a 16px grid. Never emoji.

**Header.** Off-white, 1px `border` below, sticky. Left: "DSTF" wordmark,
library name, and the backend as plain text with its OS partition ("Python ·
linux"). Centre: the queue indicator. Right: pending-edit count with Save and
Discard, then a ⋮ menu holding Export (grey, with "no pending edits" under it,
while nothing is pending), Change backend… (a dialog holding the backend
select; grey, with the sentence that says why under it, while an edit is
pending or a run is in flight), Open in another backend…, dashboard (a link
into a new tab) and Stop server, each with its full label — one layout at
every width. The backend was a select in the header's left side; the owner
moved it into the menu (Q13) when a rerun running, an edit pending and a
caption naming a new entry put Save on four lines at 900px and two at 1280px. Save's caption counts the entries it creates: a
pending edit to a test with no entry in the file as loaded writes one, "Save 2
spec edits (2 tests, 1 new entry) to test_spec.json". In every state,
including the busiest (a rerun running, an edit pending and a caption naming a
new entry), a header button takes at most two lines at 900px and one at
1280px, the count keeps one, and nothing is cut off or scrolls sideways. One
line at 900px is not the rule: it needs 960px. The header is never a solid
accent bar.

## Do / don't

- Do keep the report and the tool byte-identical in colour and type; one
  stylesheet feeds both where they share markup.
- Do put numbers in mono and right-align them.
- Don't add a colour that is not in the tables above; derive tints from the
  status fill.
- Don't grey a control without its sentence.
- Don't use emoji, gradients, or drop shadows on cards.
