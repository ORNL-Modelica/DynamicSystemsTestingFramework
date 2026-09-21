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
.dstf-plot-slot { min-height: 320px; }
.dstf-crit {
  min-height: 36px; padding: 4px 8px; cursor: pointer;
  border-top: 1px solid var(--dstf-border-hairline);
}
.dstf-crit:hover { background: var(--dstf-surface-hover); }
.dstf-crit-open { background: var(--dstf-selection); }
/* The editor belongs to the row above it: indented under it, with the
   accent rule down its left saying how far it reaches. */
.dstf-editor {
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
.dstf-chip-active { outline: 2px solid var(--dstf-accent); }
.dstf-card {
  border: 1px solid var(--dstf-border); border-radius: 4px; box-shadow: none;
  background: var(--dstf-surface); padding: 16px;
}
"""
    + _TINTS
    + _PILLS
)
