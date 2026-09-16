"""The shell's stylesheet on top of `reporting.theme.css_variables()`
(DESIGN.md "Typography", "Header", "Spacing, shape, elevation", "Status
badges"): the font token on the body and on Quasar's controls — the
roadmap left the tool on Quasar's stack until this shell applied it — the
off-white header with its hairline, the 32 px rows with their status tint,
the filled badge, the sentence beside a greyed button. Every colour is a
token; nothing here names a hex."""

from __future__ import annotations

from ...reporting.theme import STATUS

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
  padding: 4px 24px; min-height: 40px;
}
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
)
