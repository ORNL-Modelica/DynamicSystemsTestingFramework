"""The shell's stylesheet on top of `reporting.theme.css_variables()`
(DESIGN.md "Typography", "Header", "Spacing, shape, elevation", "Status
badges"): the font token on the body and on Quasar's controls — the
roadmap left the tool on Quasar's stack until this shell applied it — the
off-white header with its hairline, the 32 px rows with their status tint,
the filled badge, the sentence beside a greyed button. Every colour is a
token; nothing here names a hex."""

from __future__ import annotations

from ...reporting.theme import STATUS

_PILLS = "".join(
    f".dstf-pill-{status} {{ background: var(--dstf-{status}-pill); "
    f"color: var(--dstf-{status}-dark); border-color: var(--dstf-{status}-border); }}\n"
    for status in ("pass", "fail", "sim-fail", "no-ref", "not-run", "warn", "queued")
)

#: A criterion's swatch (legend b) in the colour its pill's border uses:
#: filled while its drawing shows, and `.dstf-swatch-off` empties it.
_SWATCHES = "".join(
    f".dstf-swatch-{status} {{ border-color: var(--dstf-{status}-border); "
    f"background: var(--dstf-{status}-border); }}\n"
    for status in ("pass", "fail", "warn", "queued")
)

_TINTS = "".join(
    f".dstf-tint-{status} {{ background: var(--dstf-{status}-tint); }}\n"
    for status in STATUS
)

STYLE = (
    """
body, .q-header, .q-drawer, .q-btn, .q-field, .q-badge, .q-dialog, .q-tooltip, .q-item {
  font-family: var(--dstf-font);
}
body { background: var(--dstf-page); color: var(--dstf-text); }
.dstf-header.q-header {
  background: var(--dstf-page) !important; color: var(--dstf-text) !important;
  border-bottom: 1px solid var(--dstf-border); box-shadow: none;
  /* No padding of its own — NiceGUI's `.nicegui-header` brings 16 px on
     every side — because each ROW carries the side padding instead: on the
     header it inset the sub-header's border-top, which then stopped short
     of both edges while the header's own border-bottom ran to them. */
  padding: 0;
  /* NiceGUI's own `.nicegui-header { flex-direction: row }` outranks
     Quasar's `.column`: without this the Test page's second row renders to
     the RIGHT of the first with Rerun / Accept clipped off-screen, and
     every simulation test still passes. */
  flex-direction: column; flex-wrap: nowrap; align-items: stretch;
}
.dstf-header-row { padding: 4px 24px; min-height: 40px; }
.dstf-subheader {
  border-top: 1px solid var(--dstf-border); padding: 4px 24px; min-height: 40px;
}
/* The Test page's subheader WRAPS: kept to one line beside a real
   library's model id, it put Accept and Rerun past a 900 px window (wholly
   off it on a TRANSFORM-length id) and squeezed Rerun to seven lines. The
   id breaks at any character when a line cannot hold it. The TEST actions
   are the status strip's now, below it. */
.dstf-subheader .dstf-model { overflow-wrap: anywhere; }
/* The Test page's status strip: a third row of the header, its tone a
   3 px left border and a tint. The sentence takes what is left and wraps;
   its buttons keep to the right as one group, below it when they do not
   fit. */
.dstf-strip {
  border-top: 1px solid var(--dstf-border); border-left: 3px solid;
  padding: 4px 24px 4px 21px; min-height: 36px; row-gap: 4px;
}
.dstf-strip-grey { background: var(--dstf-queued-tint); border-left-color: var(--dstf-queued-border); }
.dstf-strip-red { background: var(--dstf-fail-tint); border-left-color: var(--dstf-fail-border); }
.dstf-strip-amber { background: var(--dstf-warn-tint); border-left-color: var(--dstf-warn-border); }
.dstf-strip-text { flex: 1 1 20em; }
.dstf-strip > .dstf-strip-actions { margin-left: auto; }
/* A pill is at least as wide as WARN, its text centred: PASS and FAIL are
   one width, so the names after them on a plot's criteria rows start in
   one column (D140; the report's `.node-status` keeps the same). */
.dstf-pill {
  font-size: 10.5px; font-weight: 700; text-transform: uppercase;
  letter-spacing: .03em; padding: 1px 6px; border-radius: 3px; border: 1px solid;
  min-width: 4.8em; text-align: center;
}
/* A pill that carries its variable's name ("x FAIL", on a card drawing
   more than one series): the name keeps the case the test wrote it in. */
.dstf-pill-named { text-transform: none; }
.dstf-tree-line, .dstf-stats-line { min-height: 32px; }
.dstf-tree-row {
  min-height: 32px; border-top: 1px solid var(--dstf-border-hairline);
}
.dstf-tree-row:hover { background: var(--dstf-surface-hover); }
/* Move to… (U4): a place per line, its node's text cut in the middle and
   where in it kept whole; the menu no wider than the page allows. */
.dstf-move-menu { max-width: min(560px, 92vw); }
.dstf-move-label {
  display: flex; gap: .4em; min-width: 0; white-space: nowrap;
}
.dstf-move-where { flex: none; }
/* A combinator's kind dropdown, in its row's lead place: a dense Quasar
   field is 40 px tall, which stood a combinator's row well above a
   leaf's; at 28 px the rows read as one list. */
.dstf-kind .q-field__control, .dstf-kind .q-field__native,
.dstf-kind .q-field__marginal { min-height: 28px; height: 28px; }
/* A plot card: its title row, its plot, its criteria header and rows at
   the card's own spacing (the brief's "col gap 10px"). */
.dstf-plot { gap: 10px; }
/* A card's header row wraps (3c's gate over a real library: at 900 px
   beside a drawer, a long title, its pills and the toolbar in one row
   that neither wrapped nor shrank scrolled the page sideways). The title
   and its pills take what is left and wrap inside it, a long name cut in
   its middle (`.dstf-mid`); the toolbar wraps as one unit, pushed to the
   right. No
   `no-wrap` on the row: kept to one line, the title shrank instead (to
   three and four lines at 900 px beside a drawer). */
.dstf-card-head { row-gap: 4px; }
/* `min-width: 0`: a title cut in its middle is one unbreakable line, and
   the row's automatic minimum — that whole line — pushed the page sideways
   (1477 px on a 150-character title). */
.dstf-card-title { flex: 1 1 auto; min-width: 0; }
.dstf-card-tools { flex: none; margin-left: auto; }
.dstf-plot-slot { min-height: 320px; --toolbar-gutter: 34px; }
/* The plot's vertical toolbar stands in a gutter of its own, right of the
   figure, instead of over the error axis's tick labels and title: a margin
   reserved inside the figure could not know how wide those labels grow
   (an error around 1e-5 outgrew every reserve measured). The plot is the
   gutter's width narrower than its slot: `width: auto` undoes the plot's
   `w-full`, so the margin narrows the plot instead of hanging the toolbar
   15 px past the card. Plotly pins the toolbar's container with an inline
   `right: 0px`, hence the `!important`. The width is not a `--dstf-`
   token: those are the theme's table (theme.py). */
.dstf-plot-slot > .js-plotly-plot {
  width: auto; margin-right: var(--toolbar-gutter);
}
.dstf-plot-slot .js-plotly-plot .modebar-container {
  right: calc(-1 * var(--toolbar-gutter)) !important;
}
.dstf-crit {
  min-height: 36px; padding: 4px 8px; cursor: pointer;
  border-top: 1px solid var(--dstf-border-hairline);
}
.dstf-crit:hover { background: var(--dstf-surface-hover); }
/* A row's name (metric · variable) is one line: beside an open drawer a
   card is ~540 px at 900 px, and it broke inside itself ("tube" over
   "· x"). The readout beside it may wrap. */
.dstf-crit-name { white-space: nowrap; }
/* A variable label cut in the MIDDLE (D140, Q20; `widgets.MiddleCut`): the
   head ellipsized, the tail (`[3]`, the element) whole. Two inline-blocks
   in an inline-block — never a flex pair — so innerText and a copy read the
   whole label; the head is capped at the element's width less the tail's
   (`--tail`, its length in ch), and the tail is at least that wide: in a
   proportional font `[3]` is narrower than 3ch, and a head capped by more
   than the tail took was cut with room to spare. In a flex row the element
   shrinks: `overflow: hidden` makes its minimum width 0. */
.dstf-mid {
  display: inline-block; max-width: 100%; overflow: hidden;
  white-space: nowrap; vertical-align: bottom;
}
.dstf-mid-head {
  display: inline-block; max-width: calc(100% - var(--tail, 0ch));
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  vertical-align: bottom;
}
.dstf-mid-tail {
  display: inline-block; min-width: var(--tail, 0ch); white-space: nowrap;
  vertical-align: bottom;
}
/* A row's breadcrumb — its ancestors' kinds, `and › k-of-n ›` — leads it,
   muted and on one line: a plot's rows are its variable's leaves from
   anywhere in the tree, and the crumb says where each sits. Its minimum
   width is its card's longest crumb, in ch (`PlotCard`), so a plot's
   `and ›` and `and › k-of-n ›` rows start their names in one column (the
   report's `.node-crumbs` does the same). */
.dstf-crumb { white-space: nowrap; }
/* A plot's context series (grey on the plot, not scored), listed above it
   each with the × that takes it off: Plotly's legend cannot hold a
   button. */
.dstf-context { padding: 0 8px; }
.dstf-context-chip {
  border: 1px solid var(--dstf-border-hairline); border-radius: 4px;
  padding: 0 0 0 6px;
}
/* A long criterion name keeps its one line, so at narrow widths the row
   wraps (it carries no `no-wrap`): the readout and the node's controls move
   to a second line, the controls at its right end, instead of pushing the
   page sideways (3c's gate over a real library, 900 px beside a drawer). */
.dstf-crit > .dstf-node-host { margin-left: auto; }
/* A plot's criteria header: a muted "Criteria" and, at its right end, what
   attaches at VARIABLE (the plot's +), level with the criteria rows'
   controls below it. */
.dstf-criteria-head { min-height: 32px; padding: 0 8px; }
.dstf-criteria-head > .dstf-node-host { margin-left: auto; }
/* It wraps (no `no-wrap`): under a lock the + gives way to "… is running
   on <model>", longer than a card beside a drawer at 900 px. Its form
   row's slot, below it, holds the host's hidden error labels even when no
   form is open, so it is never `:empty` — it sits flush under the header
   instead, and the card's gap is not paid twice. */
.dstf-criteria-head + .dstf-criteria-slot { margin-top: -10px; }
/* The open criterion's row, and a row with a form open under it, stay
   lit. */
.dstf-crit-open, .dstf-row-open { background: var(--dstf-selection); }
/* A criterion's swatch: its status colour, filled while its drawing shows,
   an outline of it while hidden; the open criterion's is locked on. */
.dstf-swatch {
  width: 12px; height: 12px; border-radius: 2px; flex: none;
  border: 2px solid; cursor: pointer; box-sizing: border-box;
}
.dstf-swatch-locked { cursor: default; opacity: .6; }
/* The editor belongs to the row above it: indented under it, with the
   accent rule down its left saying how far it reaches. A node's form row
   belongs to its row the same way (the 2026-09-21 board, 1A). */
.dstf-editor, .dstf-form-row {
  margin: 0 0 8px 16px; padding: 12px;
  border-left: 3px solid var(--dstf-accent);
  border-top: 1px solid var(--dstf-border-hairline);
  border-bottom: 1px solid var(--dstf-border-hairline);
}
/* The criterion editor's fields in ONE wrapping row (the board: the plot
   and its editor fit a screen together). `.dstf-form` is the mode form's
   slot, which hands its controls straight to that row rather than stacking
   them in a column of its own; `.dstf-field` is one control, wide enough
   for its label ("Width mode" clipped to "Wid…" in the Task 6 screenshot);
   `.dstf-field-wide` is a multi-line one (a JSON list), on its own line. */
.dstf-fields { row-gap: 8px; }
.dstf-form { display: contents; }
.dstf-field { min-width: 140px; }
/* `order`, not source order: a tube's "Control points" is declared in the
   MIDDLE of its mode form, and a full-width box there broke the row into
   three lines with two single-line fields stranded below it. */
.dstf-fields .dstf-field-wide { order: 1; }
.dstf-field-wide { width: 100%; }
/* Focus (board 5A): "Showing 1 of N plots", the switcher naming it, and
   the way back to all of them, on the accent tint so the page says at a
   glance that what is below it is one plot of several. */
.dstf-focus-bar {
  background: var(--dstf-selection); border: 1px solid var(--dstf-accent);
  border-radius: 4px; padding: 4px 12px;
}
/* Eight rows of 42 px, and then the rows scroll (board 2A) — the
   container this sits on holds the rows alone, never `Add row`. */
.dstf-table-scroll { max-height: 336px; overflow-y: auto; }
.dstf-wordmark { font-size: 20px; font-weight: 700; }
/* The pending drawer's preview (Q6 A): the entry as test_spec.json will
   hold it, one line per row, never wrapped — a long line scrolls INSIDE
   the diff, not the drawer. A flex item's min-width is its content's:
   without the min-width 0 here and on the section and body columns a long
   line widened the diff past the drawer, which clipped it, and nothing
   scrolled. The lines sit in a box as wide as the longest of them, so an
   added line's tint runs the whole scrolled width. */
.dstf-diff {
  font-family: var(--dstf-mono); font-size: 12px; overflow-x: auto;
  width: 100%; min-width: 0; max-width: 100%;
  border: 1px solid var(--dstf-border-hairline); border-radius: 3px;
}
.dstf-diff-lines { width: max-content; min-width: 100%; }
.dstf-diff-line { display: flex; white-space: pre; line-height: 18px; padding: 0 6px; }
.dstf-diff-line > * { flex: none; }
.dstf-diff-add { background: var(--dstf-pass-tint); color: var(--dstf-pass-dark); }
.dstf-diff-del { background: var(--dstf-fail-tint); color: var(--dstf-fail-dark); }
.dstf-diff-gap, .dstf-diff-muted { color: var(--dstf-text-muted); }
.dstf-mono { font-family: var(--dstf-mono); }
.dstf-why { color: var(--dstf-text-muted); font-size: 12px; }
/* A card's title reads the expression its model wrote; the result-file
   name — what a criterion, an override or a --filter names — rides beside
   it, muted. Drawn only when the two differ. */
.dstf-varname { color: var(--dstf-text-muted); font-size: 12px; font-family: var(--dstf-mono); }
.dstf-progress { font-size: 12px; color: var(--dstf-text-secondary); }
.dstf-group {
  font-size: 10px; text-transform: uppercase; letter-spacing: .03em;
  color: var(--dstf-text-muted); padding: 12px 0 2px; overflow-wrap: anywhere;
}
/* A short, fixed title — the picker's sections, the Statistics card's —
   never breaks (at 10 px "On this plot" measured 70 px in a 69.6 px box)
   and spaces its words: uppercase at 10 px a space is 2.3 px, and "S P"
   read "ON THISPLOT" in Chromium on one line. A drawer's package title
   keeps `anywhere`. */
.dstf-pick-list .dstf-group, .dstf-stats .dstf-group {
  white-space: nowrap; word-spacing: .2em;
}
/* The Statistics card (U3): a row per statistic, the numbers in fixed
   right-aligned columns so Run and Baseline read down, the `+` or the
   criterion's pill at the end; a changed row wears the report's warn
   tint. */
.dstf-stats .dstf-group { padding-left: 8px; }
.dstf-stat-row {
  width: 100%; min-height: 28px; padding: 0 8px;
  border-top: 1px solid var(--dstf-border-hairline);
}
.dstf-stat-head { color: var(--dstf-text-muted); font-size: 12px; border-top: none; }
.dstf-stat-label {
  flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap;
}
/* In px, not ch: the headings are not in the numbers' mono font, and a
   `ch` column is as wide as its own font says. */
.dstf-stat-num { flex: none; width: 84px; text-align: right; }
.dstf-stat-delta { flex: none; width: 56px; text-align: right; }
.dstf-stat-end {
  flex: none; width: 150px; display: flex; align-items: center;
  justify-content: flex-end; gap: 6px;
}
.dstf-stat-changed { background: var(--dstf-warn-tint); }
/* Open, a changed row wears the selection as every open criteria row does:
   the tint, declared later, hid which row the editor was under. */
.dstf-stats .dstf-crit-open { background: var(--dstf-selection); }
/* The editor under a Statistics row keeps inside the card: `w-full` plus
   the editor's 16 px indent ran 16 px past its right edge (B12). */
.dstf-stats .dstf-editor { width: calc(100% - 16px); }
/* The Tests drawer's rows at a real library's lengths: the name takes what
   the badge and the edits pill leave it and is cut with an ellipsis (the
   whole id is its title), and they never shrink. A TRANSFORM id in Full,
   or a 40-character short name, pushed the badge out of the drawer at
   900 px, and a package label ran past it. */
.dstf-tests .dstf-name { flex: 1 1 auto; min-width: 0; }
.dstf-tests .dstf-name .q-btn__content > .block {
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
/* A Full name is cut at its start, where ids sharing a package prefix
   differ least (`test_list._full_name`: the id is a <bdi> inside). */
.dstf-tests .dstf-name .dstf-cut-start { direction: rtl; }
.dstf-tests .dstf-badge, .dstf-tests .dstf-pill-named { flex: none; }
.dstf-row {
  min-height: 32px; border-bottom: 1px solid var(--dstf-border-hairline);
  background: var(--dstf-surface); transition: background .1s;
}
.dstf-row:hover { background: var(--dstf-surface-hover); }
.dstf-badge.q-badge {
  color: #ffffff; font-size: 10.5px; font-weight: 700; text-transform: uppercase;
  letter-spacing: .03em; border-radius: 3px;
}
/* The Test page's rail (3c): labelled buttons in 40 px. NiceGUI pads a
   drawer by 1rem, which put the rail's buttons at 16-56 px of a 40 px
   drawer and clipped "Tests" to "Tes" (the review's screenshot, and again
   with 10 px labels): the drawer carries no padding. The rail and the
   panel may give up the 1 px of the drawer's border rather than overflow
   it. The open drawer's button sits on the selection tint. */
.nicegui-drawer.dstf-rail-drawer { padding: 0; gap: 0; }
.dstf-rail { width: 40px; min-width: 0; padding-top: 4px; }
.dstf-rail .q-btn { width: 100%; padding: 4px 0; min-width: 0; }
.dstf-rail-label { font-size: 10px; letter-spacing: 0; line-height: 1.2; }
.dstf-rail-on { background: var(--dstf-selection); color: var(--dstf-accent); }
/* The panel's width is the tab's (`TabState.drawer_width`, set inline by
   `Rail`); its right edge is the resize grip (K5), 6 px of the drawer over
   the panel's border. */
.dstf-drawer-panel { min-width: 0; flex: none; }
.dstf-rail-row { position: relative; }
.dstf-drawer-handle {
  position: absolute; top: 0; right: 0; bottom: 0; width: 6px;
  cursor: col-resize; touch-action: none; z-index: 1;
}
.dstf-drawer-handle:hover { background: var(--dstf-selection); }
/* Focused from the keyboard, the grip is the accent: ←/→ move it (K5). */
.dstf-drawer-handle:focus-visible { outline: none; background: var(--dstf-accent); }
/* The Variables drawer's rows (3c): "+ new plot" is there on the hovered
   row only (the owner's ruling, Q6 A) — `visibility`, so the row does not
   reflow as the mouse moves down the list — and on a row with focus in
   it: a hidden button takes no focus, and Tab from a check skipped its
   own. A family's members hang under it, indented; a family's swatch marks
   an array, in the accent ramp (a plot picks a family's hue by its order
   on that plot, not this). The target line is a tag on the accent tint
   with the selected plot's ring inside it, as the owner's board drew it: a
   bare ring beside the text read as an empty checkbox. */
.dstf-var-row { min-height: 28px; }
/* Two lines (2026-09-29): the label with the row's width on the first; the
   muted name, "plot N" and "+ new plot" on the second, under the label
   (past the check). A row with nothing but "+ new plot" is one line (K6). */
.dstf-var-line1 { min-height: 26px; }
.dstf-var-line2 { padding-left: 28px; min-height: 18px; font-size: 11px; }
.dstf-var-line2 .q-btn { min-height: 18px; }
.dstf-var-line2 .dstf-varname {
  min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.dstf-var-row:hover { background: var(--dstf-surface-hover); }
.dstf-var-row .dstf-var-new { visibility: hidden; }
.dstf-var-row:hover .dstf-var-new,
.dstf-var-row:focus-within .dstf-var-new { visibility: visible; }
.dstf-var-member { padding-left: 24px; }
.dstf-var-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dstf-var-where, .dstf-var-row .dstf-var-new { flex: none; white-space: nowrap; }
.dstf-var-ramp { display: inline-flex; flex: none; }
.dstf-var-ramp > span { width: 5px; height: 9px; }
.dstf-var-target {
  cursor: pointer; font-size: 12px; padding: 4px 8px; border-radius: 3px;
  background: var(--dstf-selection); color: var(--dstf-accent);
}
.dstf-var-ring {
  width: 12px; height: 9px; border-radius: 2px; flex: none;
  border: 2px solid var(--dstf-accent); box-sizing: content-box;
}
/* The add form's picker (U2): a list that scrolls inside the form row,
   never a 200-row form; the chosen row on the selection tint, the
   keyboard's cursor ringed; the Chosen line under it (K7). */
.dstf-pick-list { max-height: 240px; overflow-y: auto; }
.dstf-pick-row { cursor: pointer; min-height: 24px; padding: 0 4px; }
.dstf-pick-row:hover { background: var(--dstf-surface-hover); }
.dstf-pick-on { background: var(--dstf-selection); }
.dstf-pick-cursor { box-shadow: inset 0 0 0 2px var(--dstf-accent); }
.dstf-pick-chosen { color: var(--dstf-text-muted); padding: 2px 4px 0; }
/* The drawers' one control set (Q7): a menu's current choice carries its
   check; a count pill is a status pill that is a button, ringed while it
   filters. */
.dstf-menu-check { visibility: hidden; }
.dstf-menu-checked .dstf-menu-check { visibility: visible; }
.dstf-count-pill.q-btn { min-height: 20px; padding: 0 6px; font-size: 10.5px; }
.dstf-count-pill-on.q-btn { box-shadow: 0 0 0 2px var(--dstf-accent); }
.dstf-card {
  border: 1px solid var(--dstf-border); border-radius: 4px; box-shadow: none;
  background: var(--dstf-surface); padding: 16px;
}
/* The selected plot, the one the Variables drawer's checkboxes act on: two
   classes and after `.dstf-card`, whose `box-shadow: none` otherwise wins
   and the ring is classed but never painted (3c's LOOK, in Chromium). */
.dstf-card.dstf-plot-selected { box-shadow: inset 0 0 0 2px var(--dstf-accent); }
"""
    + _TINTS
    + _PILLS
    + _SWATCHES
    # After the fills, so an outline wins over the colour it is drawn in.
    + ".dstf-swatch-off { background: transparent; }\n"
    # Two classes: that alone beats the one-class status tints, whatever the
    # order (as one class it tied with them and read the fail tint,
    # rgb(255,235,238) — the 3c review). The order that matters is after
    # `.dstf-row:hover`, which has the same specificity: last, the current
    # row stays lit under the mouse.
    + ".dstf-row.dstf-row-current { background: var(--dstf-selection); }\n"
)
