"""DESIGN.md "Colors" / "Typography" / "Themes": one token table, rendered
as CSS custom properties into every page both tiers serve. The pages carry
no hex colour outside the token block, every token they use is defined, and
the type stack ends in a generic
sans-serif (the shipped stack ended in `monospace`, so a Linux browser
without Segoe UI or Roboto rendered the report in monospace)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from dstf.reporting import theme

HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
STYLE_BLOCK = re.compile(r"<style[^>]*>(.*?)</style>", re.S)
STYLE_ATTR = re.compile(r'style="([^"]*)"')


def _colours_outside_root(html: str) -> list[str]:
    """Every hex literal in a <style> block or a style= attribute that is
    not inside the `:root { ... }` token block."""
    found: list[str] = []
    for block in STYLE_BLOCK.findall(html):
        stripped = re.sub(r":root\s*\{[^}]*\}", "", block)
        found += HEX.findall(stripped)
    for attr in STYLE_ATTR.findall(html):
        found += HEX.findall(attr)
    return found


class TestTable:
    def test_default_tokens_are_the_design_systems(self):
        assert theme.DEFAULT_THEME["page"] == "#fafafa"
        assert theme.DEFAULT_THEME["accent"] == "#1976D2"
        assert theme.STATUS["pass"]["fill"] == "#4CAF50"
        assert theme.STATUS["warn"]["dark"] == "#8a6d00"
        assert theme.ROLES["soft_check"]["fg"] == "#5e3a80"

    def test_css_variables_render_every_token_once(self):
        css = theme.css_variables()
        assert css.startswith(":root {")
        for name in theme.DEFAULT_THEME:
            assert css.count(f"--dstf-{name}:") == 1
        assert "--dstf-pass-fill: #4CAF50;" in css
        assert "--dstf-role-companion-border: #a8dfc0;" in css
        assert f"--dstf-font: {theme.FONT_STACK};" in css

    def test_the_type_stack_ends_in_a_generic_sans_serif(self):
        assert theme.FONT_STACK.split(",")[-1].strip() == "sans-serif"
        assert "monospace" not in theme.FONT_STACK
        assert theme.MONO_STACK.split(",")[-1].strip() == "monospace"

    def test_status_and_roles_are_not_part_of_a_theme(self):
        """A theme's table is neutrals and accent; a status and a role chip
        are not tokens of it (DESIGN.md "Themes"; what a dark theme does to
        a status is `tests/test_themes.py`)."""
        assert not set(theme.DEFAULT_THEME) & {"pass", "fail", "soft_check"}
        with pytest.raises(TypeError):
            theme.DEFAULT_THEME["page"] = "#000000"  # type: ignore[index]

    def test_quasar_palette_mirrors_the_table(self):
        palette = theme.quasar_palette()
        assert palette == {
            "primary": "#1976D2",
            "secondary": "#666666",
            "accent": "#1976D2",
            "positive": "#4CAF50",
            "negative": "#f44336",
            "warning": "#ffc107",
            "info": "#607D8B",
        }


class TestPages:
    def test_the_per_test_report_carries_only_tokens(self, tmp_path):
        from report_fixtures import _context_with_figure, _render_with_context

        ctx, _ = _context_with_figure()
        html = _render_with_context(tmp_path, ctx).read_text(encoding="utf-8")
        assert ":root {" in html
        # Autoescape turns the stack's quotes into `&#34;`, whose `;` ends the
        # custom property early and leaves the page in the browser default.
        assert f"--dstf-font: {theme.FONT_STACK};" in html
        assert _colours_outside_root(html) == []
        assert re.search(r"body\s*\{[^}]*font-family:\s*var\(--dstf-font\)", html)

    def test_the_report_carries_no_level_tokens(self, tmp_path):
        """The tree card's level bars are the tool's alone (U5, H5): the
        per-test report and the dashboard declare none of their tokens."""
        from dstf.reporting.dashboard_render import render_live
        from report_fixtures import _context_with_figure, _render_with_context
        from test_dashboard_render import _write_status_json

        ctx, _ = _context_with_figure()
        report = _render_with_context(tmp_path, ctx).read_text(encoding="utf-8")
        _write_status_json(tmp_path, {"total": 0, "counts": {}, "tests": []})
        render_live(tmp_path)
        dashboard = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
        for html in (report, dashboard):
            assert ":root {" in html
            assert "--dstf-level" not in html
        assert "--dstf-level" not in theme.css_variables()
        tool = theme.level_variables()
        for name, value in theme.LEVELS.items():
            assert tool.count(f"--dstf-{name}: {value};") == 1

    def test_the_dashboard_carries_only_tokens(self, tmp_path):
        from dstf.reporting.dashboard_render import render_live
        from test_dashboard_render import _write_status_json

        _write_status_json(
            tmp_path,
            {
                "total": 1,
                "elapsed": 0.0,
                "eta_seconds": None,
                "counts": {
                    "queued": 1,
                    "running": 0,
                    "passed": 0,
                    "failed": 0,
                    "timed_out": 0,
                },
                "tests": [
                    {
                        "test_key": "test_0001",
                        "model_id": "Lib.A",
                        "status": "queued",
                        "elapsed": None,
                        "worker_id": None,
                        "report_dir": "test_0001",
                    }
                ],
                "updated_at": 0.0,
            },
        )
        render_live(tmp_path)
        html = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
        assert ":root {" in html
        # Autoescape turns the stack's quotes into `&#34;`, whose `;` ends the
        # custom property early and leaves the page in the browser default.
        assert f"--dstf-font: {theme.FONT_STACK};" in html
        assert _colours_outside_root(html) == []
        assert re.search(r"body\s*\{[^}]*font-family:\s*var\(--dstf-font\)", html)

    def test_every_token_a_template_uses_is_defined(self):
        """A misspelt ``var(--dstf-...)`` is not an error in CSS: the property
        silently falls back to the browser default."""
        defined = set(re.findall(r"(--dstf-[\w-]+):", theme.css_variables()))
        templates = Path(theme.__file__).parent / "templates"
        for name in ("interactive.html", "dashboard.html"):
            html = (templates / name).read_text(encoding="utf-8")
            used = set(re.findall(r"var\((--dstf-[\w-]+)\)", html))
            assert used, name
            assert used <= defined, (name, sorted(used - defined))

    def test_a_warning_row_reads_its_status_word_like_every_other(self):
        """`fill` is the filled badge and the bar segment; `dark` is text on a
        tint. The dashboard's six other `.counter .value` rules take `dark`;
        `.warn` took `fill`, and amber #ffc107 is 1.4-1.6:1 against the row
        tints — the word was there and unreadable. `dark` gives 4.1-4.9:1
        over the same grounds, which is legible everywhere but clears the
        4.5:1 AA floor only on the lighter tints; darkening the shared token
        (#7a6000 clears every ground) is an open call, because it restyles
        the report page's advisory chip and decimation banner too."""
        templates = Path(theme.__file__).parent / "templates"
        html = (templates / "dashboard.html").read_text(encoding="utf-8")
        rule = re.search(r"^\.warn \{[^}]*\}", html, re.M)
        assert rule, "the .warn rule moved"
        assert "var(--dstf-warn-dark)" in rule.group(0)
        assert "var(--dstf-warn-fill)" not in rule.group(0)

    def test_a_sortable_header_has_a_hover_state_distinct_from_its_rest_state(self):
        """A sort control is a `th[data-sort]` — dashboard.js binds the click
        on `#results-table thead tr:first-child th[data-sort]`, 14 of the
        page's `th`, not all of them; the filter row's are text-input cells
        and must not claim an affordance they do not have. `th` and
        `th:hover` both resolved to --dstf-surface-hover, so the only
        affordance a real sort control had was the cursor.

        Resolved through the token table, not compared as names: two
        different names holding the same hex is the same invisible hover,
        and an earlier version of this test passed with
        surface-active == #f5f5f5.
        """
        templates = Path(theme.__file__).parent / "templates"
        html = (templates / "dashboard.html").read_text(encoding="utf-8")

        def background(selector):
            rule = re.search(rf"^{re.escape(selector)} \{{[^}}]*\}}", html, re.M)
            assert rule, selector
            token = re.search(r"background:\s*var\(--dstf-([\w-]+)\)", rule.group(0))
            assert token, selector
            name = token.group(1)
            assert name in theme.DEFAULT_THEME, name
            return theme.DEFAULT_THEME[name]

        assert background("th[data-sort]:hover") != background("th")

    def test_the_templates_on_disk_carry_no_hex_but_the_root_block(self):
        templates = Path(theme.__file__).parent / "templates"
        for name in ("interactive.html", "dashboard.html"):
            html = (templates / name).read_text(encoding="utf-8")
            assert _colours_outside_root(html) == [], name
