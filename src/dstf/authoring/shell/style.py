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
    for status in ("pass", "fail", "warn", "queued")
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
.dstf-pill {
  font-size: 10.5px; font-weight: 700; text-transform: uppercase;
  letter-spacing: .03em; padding: 1px 6px; border-radius: 3px; border: 1px solid;
}
/* A pill that carries its variable's name ("x FAIL", on a card drawing
   more than one series): the name keeps the case the test wrote it in. */
.dstf-pill-named { text-transform: none; }
.dstf-tree-line { min-height: 32px; }
.dstf-tree-row {
  min-height: 32px; border-top: 1px solid var(--dstf-border-hairline);
}
.dstf-tree-row:hover { background: var(--dstf-surface-hover); }
/* A plot card: its title row, its plot, its criteria rows and its footers
   at the card's own spacing (the brief's "col gap 10px"). */
.dstf-plot { gap: 10px; }
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
/* Focus (board 5A): "Showing 1 of N plots: <name>" and the way back to all
   of them, on the accent tint so the page says at a glance that what is
   below it is one plot of several. */
.dstf-focus-bar {
  background: var(--dstf-selection); border: 1px solid var(--dstf-accent);
  border-radius: 4px; padding: 4px 12px;
}
/* Eight rows of 42 px, and then the rows scroll (board 2A) — the
   container this sits on holds the rows alone, never `Add row`. */
.dstf-table-scroll { max-height: 336px; overflow-y: auto; }
.dstf-wordmark { font-size: 20px; font-weight: 700; }
.dstf-mono { font-family: var(--dstf-mono); }
.dstf-why { color: var(--dstf-text-muted); font-size: 12px; }
/* A card's title reads the expression its model wrote; the result-file
   name — what a criterion, an override or a --filter names — rides beside
   it, muted. Drawn only when the two differ. */
.dstf-varname { color: var(--dstf-text-muted); font-size: 12px; font-family: var(--dstf-mono); }
.dstf-progress { font-size: 12px; color: var(--dstf-text-secondary); }
.dstf-group {
  font-size: 10px; text-transform: uppercase; letter-spacing: .03em;
  color: var(--dstf-text-muted); padding: 12px 0 2px;
}
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
.dstf-drawer-panel { width: 320px; min-width: 0; }
/* The Variables drawer's rows (3c): "+ new plot" is there on the hovered
   row only (the owner's ruling, Q6 A) — `visibility`, so the row does not
   reflow as the mouse moves down the list. A family's members hang under
   it, indented; a family carries the ramp its members are drawn in. The
   target line is a tag on the accent tint with the selected plot's ring
   inside it, as the owner's board drew it: a bare ring beside the text
   read as an empty checkbox. */
.dstf-var-row { min-height: 28px; }
.dstf-var-row:hover { background: var(--dstf-surface-hover); }
.dstf-var-row .dstf-var-new { visibility: hidden; }
.dstf-var-row:hover .dstf-var-new { visibility: visible; }
.dstf-var-member { padding-left: 24px; }
.dstf-var-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dstf-var-ramp { width: 16px; height: 8px; border-radius: 2px; flex: none; }
.dstf-var-target {
  cursor: pointer; font-size: 12px; padding: 4px 8px; border-radius: 3px;
  background: var(--dstf-selection); color: var(--dstf-accent);
}
.dstf-var-ring {
  width: 12px; height: 9px; border-radius: 2px; flex: none;
  border: 2px solid var(--dstf-accent); box-sizing: content-box;
}
.dstf-chip-active { outline: 2px solid var(--dstf-accent); }
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
