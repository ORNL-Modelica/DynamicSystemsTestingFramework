"""The bundled themes (D148): `default` and `dark`, one token table each
(`reporting.theme`) and one figure palette each (`figures.palette`). The
contrast floors are COMPUTED here from the tables, by the WCAG 2 formula:
a number copied from a measurement would pass whatever the table says."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import dstf
from dstf.figures.families import HUES
from dstf.figures.palette import PALETTES, palette_of
from dstf.reporting import theme

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
SRC = Path(dstf.__file__).parent


def _linear(channel: int) -> float:
    c = channel / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(colour: str) -> float:
    assert HEX.match(colour), colour
    r, g, b = (int(colour[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * _linear(r) + 0.7152 * _linear(g) + 0.0722 * _linear(b)


def contrast(a: str, b: str) -> float:
    """The WCAG 2 contrast ratio of two opaque colours."""
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_the_contrast_formula_is_wcags():
    assert contrast("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast("#777777", "#ffffff") == pytest.approx(4.48, abs=0.01)
    assert contrast("#ffffff", "#777777") == contrast("#777777", "#ffffff")


class TestTokens:
    def test_the_bundled_themes_are_default_and_dark(self):
        assert list(theme.THEMES) == ["default", "dark"]
        assert theme.THEMES["default"] is theme.DEFAULT_THEME
        assert not theme.is_dark("default") and theme.is_dark("dark")

    def test_every_theme_sets_the_same_fifteen_tokens(self):
        """A token one theme lacks is a `var(--dstf-...)` that silently
        falls back to the browser's default under it."""
        default = set(theme.DEFAULT_THEME)
        assert len(default) == 15
        for name, table in theme.THEMES.items():
            assert set(table) == default, name

    def test_the_dark_tokens_are_the_ones_chosen(self):
        assert dict(theme.DARK_THEME) == {
            "page": "#121212",
            "surface": "#1e1e1e",
            "surface-hover": "#2a2a2a",
            "surface-active": "#353535",
            "border": "#484848",
            "border-hairline": "#303030",
            "text": "#e4e4e4",
            "text-secondary": "#b8b8b8",
            "text-muted": "#9e9e9e",
            "text-disabled": "#7d7d7d",
            "accent": "#64b5f6",
            "accent-hover": "#90caf9",
            "selection": "#1d3a57",
            "shadow": "rgba(0,0,0,0.5)",
            "on-accent": "#0b1a2b",
        }
        assert theme.DEFAULT_THEME["on-accent"] == "#ffffff"

    def test_the_dark_neutrals_are_grey_not_tinted(self):
        """Neutral grey (the owner's choice over the blue-tinted board):
        every neutral has three equal channels."""
        for token in (
            "page",
            "surface",
            "surface-hover",
            "surface-active",
            "border",
            "border-hairline",
            "text",
            "text-secondary",
            "text-muted",
            "text-disabled",
        ):
            value = theme.DARK_THEME[token]
            assert value[1:3] == value[3:5] == value[5:7], token

    def test_an_unknown_theme_is_refused_by_name(self):
        for ask in (theme.theme_named, theme.is_dark, theme.css_variables):
            with pytest.raises(ValueError, match="default, dark"):
                ask("solarized")
        with pytest.raises(ValueError, match="'solarized'"):
            theme.quasar_palette("solarized")

    def test_dark_text_clears_its_floors_on_the_surface(self):
        t = theme.DARK_THEME
        assert contrast(t["text"], t["surface"]) >= 7
        assert contrast(t["text-secondary"], t["surface"]) >= 4.5
        assert contrast(t["text-muted"], t["surface"]) >= 4.5
        # The page, a hovered row and a selected one are grounds too.
        for ground in ("page", "surface-hover", "selection"):
            assert contrast(t["text"], t[ground]) >= 7, ground
            assert contrast(t["text-secondary"], t[ground]) >= 4.5, ground

    @pytest.mark.parametrize("name", list(theme.THEMES))
    def test_the_accent_reads_as_a_link_and_carries_its_buttons_text(self, name):
        t = theme.THEMES[name]
        assert contrast(t["accent"], t["surface"]) >= 4.5
        assert contrast(t["on-accent"], t["accent"]) >= 4.5

    def test_white_on_the_dark_accent_is_why_on_accent_exists(self):
        assert contrast("#ffffff", theme.DARK_THEME["accent"]) < 3


class TestStatus:
    def test_the_default_themes_status_is_the_table(self):
        assert theme.status_colours() is theme.STATUS
        assert theme.status_colours("default") is theme.STATUS

    def test_a_dark_theme_keeps_every_fill(self):
        """The badge is white on the fill in every theme."""
        dark = theme.status_colours("dark")
        assert set(dark) == set(theme.STATUS)
        for status, parts in theme.STATUS.items():
            assert dark[status]["fill"] == parts["fill"], status
            assert set(dark[status]) == set(parts), status

    def test_a_dark_tint_and_pill_are_the_fill_laid_thin_over_the_surface(self):
        dark = theme.status_colours("dark")
        # #4CAF50 at 14 % over #1e1e1e, worked by hand: 0x4c*.14 + 0x1e*.86.
        assert dark["pass"]["tint"] == "#243225"
        assert dark["pass"]["pill"] == "#29412a"
        assert dark["fail"]["tint"] == "#3c2321"
        surface = theme.DARK_THEME["surface"]
        for status, parts in dark.items():
            # Thinner than the pill, and both between the surface and the fill.
            steps = [surface, parts["tint"], parts["pill"], parts["fill"]]
            lums = [luminance(c) for c in steps]
            assert lums == sorted(lums) and len(set(lums)) == 4, status

    def test_dimmed_status_text_reads_on_its_tint_its_pill_and_the_surface(self):
        surface = theme.DARK_THEME["surface"]
        for status, parts in theme.status_colours("dark").items():
            for ground in (parts["tint"], parts["pill"], surface):
                assert contrast(parts["dark"], ground) >= 4.5, (status, ground)

    def test_the_themes_own_text_reads_on_a_dark_tint(self):
        """A row's name and numbers are the theme's text, on the tint."""
        t = theme.DARK_THEME
        for status, parts in theme.status_colours("dark").items():
            assert contrast(t["text"], parts["tint"]) >= 7, status
            assert contrast(t["text-muted"], parts["tint"]) >= 4.5, status

    def test_the_light_tints_would_be_islands_on_dark(self):
        """Why dark dims: the light table's tints behind the dark theme's
        text are unreadable."""
        for status, parts in theme.STATUS.items():
            assert contrast(theme.DARK_THEME["text"], parts["tint"]) < 1.5, status


class TestStylesheet:
    def test_default_css_gains_one_line_and_no_colour_scheme(self):
        css = theme.css_variables()
        assert css == theme.css_variables("default")
        assert "  --dstf-on-accent: #ffffff;\n" in css
        assert "color-scheme" not in css

    def test_dark_css_carries_the_dark_tokens_and_the_dimmed_status(self):
        css = theme.css_variables("dark")
        for token, value in theme.DARK_THEME.items():
            assert css.count(f"  --dstf-{token}: {value};\n") == 1, token
        assert "  color-scheme: dark;\n" in css
        assert "  --dstf-pass-fill: #4CAF50;\n" in css
        assert "  --dstf-pass-tint: #243225;\n" in css
        assert "  --dstf-pass-dark: #a5d6a7;\n" in css
        assert "  --dstf-warn-dark: #ffd54f;\n" in css
        # No light surface, no light tint.
        for light in ("#ffffff;", "#fafafa", "#f1f8e9", "#ffebee"):
            assert light not in css.replace("--dstf-on-accent: #ffffff;", ""), light

    def test_both_themes_define_the_same_variables(self):
        def names(css):
            return set(re.findall(r"(--dstf-[\w-]+):", css))

        assert names(theme.css_variables("dark")) == names(theme.css_variables())

    def test_roles_are_the_same_in_every_theme(self):
        for role, parts in theme.ROLES.items():
            for part, value in parts.items():
                line = f"  --dstf-role-{role}-{part}: {value};\n"
                assert line in theme.css_variables("dark")
                assert line in theme.css_variables("default")

    def test_the_dark_quasar_palette_names_quasars_dark_grounds(self):
        palette = theme.quasar_palette("dark")
        assert palette["primary"] == palette["accent"] == "#64b5f6"
        assert palette["dark"] == theme.DARK_THEME["surface"]
        assert palette["dark_page"] == theme.DARK_THEME["page"]
        assert palette["positive"] == theme.STATUS["pass"]["fill"]
        assert "dark" not in theme.quasar_palette("default")


def _colours(value) -> list[str]:
    """Every colour string anywhere in a nested template."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [c for v in value.values() for c in _colours(v)]
    return []


class TestFigurePalette:
    def test_one_palette_per_bundled_theme(self):
        assert list(PALETTES) == list(theme.THEMES)
        with pytest.raises(ValueError, match="default, dark"):
            palette_of("solarized")

    def test_the_default_palette_is_what_the_figures_drew(self):
        """The literals `figures/plotly.py` and `families.py` held before
        there was a palette: the default theme's figure is unchanged."""
        default = palette_of("default")
        assert default.reference_event == "#757575"
        assert default.ring == "#2e7d32"
        assert default.ring_fill == "rgba(46,125,50,0.25)"
        assert default.context == "#888888"
        assert default.soft_check == "#7B1FA2"
        assert default.hues is HUES
        assert default.template is None

    def test_a_context_series_is_its_themes_muted_text(self):
        for name, table in theme.THEMES.items():
            assert palette_of(name).context == table["text-muted"], name

    def test_the_dark_template_is_made_of_the_dark_tokens(self):
        t = theme.DARK_THEME
        template = palette_of("dark").template
        assert template is not None
        assert template["paper_bgcolor"] == template["plot_bgcolor"] == t["surface"]
        assert template["font"] == {"color": t["text"]}
        for axis in ("xaxis", "yaxis"):
            assert template[axis] == {
                "gridcolor": t["border-hairline"],
                "zerolinecolor": t["border"],
                "linecolor": t["border"],
                "tickcolor": t["border"],
                "tickfont": {"color": t["text-secondary"]},
                "title": {"font": {"color": t["text-secondary"]}},
            }
        assert template["legend"] == {"font": {"color": t["text"]}}
        assert template["hoverlabel"] == {
            "bgcolor": t["surface-hover"],
            "bordercolor": t["border"],
            "font": {"color": t["text"]},
        }
        assert template["modebar"] == {
            "bgcolor": "rgba(0,0,0,0)",
            "color": t["text-muted"],
            "activecolor": t["text"],
        }
        # Nothing in it is a colour of some other table.
        assert set(_colours(template)) <= {*t.values(), "rgba(0,0,0,0)"}

    def test_the_dark_ramps_change_only_the_four_shades_that_sank(self):
        dark = palette_of("dark").hues
        changed = {
            (h, i): (HUES[h][i], dark[h][i])
            for h in range(len(HUES))
            for i in range(4)
            if HUES[h][i] != dark[h][i]
        }
        assert changed == {
            (0, 3): ("#0d47a1", "#1264e2"),
            (1, 3): ("#1b5e20", "#237b2a"),
            (2, 2): ("#7b1fa2", "#a631d8"),
            (2, 3): ("#4a148c", "#8a40e4"),
        }
        ground = theme.DARK_THEME["surface"]
        for was, now in changed.values():
            assert contrast(was, ground) < 2.2, was
            assert contrast(now, ground) >= 3, now

    def test_a_dark_ramp_still_runs_light_to_dark(self):
        for hue in palette_of("dark").hues:
            lums = [luminance(stop) for stop in hue]
            assert lums == sorted(lums, reverse=True), hue

    def test_the_dark_chrome_reads_on_the_dark_plot(self):
        dark = palette_of("dark")
        ground = theme.DARK_THEME["surface"]
        assert dark.reference_event == "#a8a8a8"
        assert dark.ring == "#66bb6a"
        assert dark.ring_fill == "rgba(102,187,106,0.25)"
        assert dark.soft_check == "#ba68c8"
        for colour in (dark.reference_event, dark.ring, dark.soft_check, dark.context):
            assert contrast(colour, ground) >= 3, colour
        # The default theme's: the overlay sank, the other two sat at the
        # floor (a ring is a 2 px outline, an event line a 1 px dash).
        default = palette_of("default")
        assert contrast(default.soft_check, ground) < 2.2
        assert contrast(default.ring, ground) < 3.3
        assert contrast(default.reference_event, ground) < 3.7

    def test_the_ring_fill_is_the_rings_colour_thinned(self):
        for name in PALETTES:
            p = palette_of(name)
            r, g, b = (int(p.ring[i : i + 2], 16) for i in (1, 3, 5))
            assert p.ring_fill == f"rgba({r},{g},{b},0.25)", name


#: What renders a page, and so takes the project's theme: a call in `src`
#: that leaves `theme=` out renders the default theme whatever testing.json
#: says, and nothing else would fail.
RENDERERS = frozenset(
    {
        "render_live",
        "render_final",
        "render_final_to",
        "generate_comparison_plots",
        "ProgressReporter",
    }
)


def _render_calls() -> list[tuple[str, int, ast.Call]]:
    found = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name in RENDERERS:
                found.append((str(path.relative_to(SRC)), node.lineno, node))
    return found


class TestTheThemeIsHandedDown:
    """The theme is the project's (`Config.theme`) and reaches a renderer as
    an argument — no process-wide setting a `Config` writes."""

    def test_every_render_call_in_src_says_the_theme(self):
        calls = _render_calls()
        # The eight of `cli/run_cmds.py`, the tool's two, the pipeline's,
        # the reporter's own and its construction, the suite's and the
        # review's page: fewer means the scan stopped finding them.
        assert len(calls) >= 15, [(p, n) for p, n, _ in calls]
        silent = [
            (path, line)
            for path, line, call in calls
            if not any(kw.arg == "theme" for kw in call.keywords)
        ]
        assert silent == []

    def test_the_dashboards_environment_holds_no_stylesheet(self):
        """It was set at import, from the default theme, before any
        `Config` was read."""
        from dstf.reporting import dashboard_render

        assert "dstf_css" not in dashboard_render._env.globals

    def test_the_dashboard_is_rendered_in_the_theme_it_is_handed(self, tmp_path):
        from dstf.reporting.dashboard_render import render_final, render_live
        from test_dashboard_render import _write_status_json

        _write_status_json(tmp_path, {"total": 0, "counts": {}, "tests": []})
        page = tmp_path / "dashboard.html"
        for render in (render_live, render_final):
            render(tmp_path)
            assert "--dstf-page: #fafafa;" in page.read_text(encoding="utf-8")
            render(tmp_path, theme="dark")
            assert "--dstf-page: #121212;" in page.read_text(encoding="utf-8")

    def test_the_live_page_takes_the_reporters_theme(self, tmp_path):
        from dstf.backends.progress import ProgressReporter

        reporter = ProgressReporter(tmp_path, 1, theme="dark")
        reporter.register("test_0001", "Lib.A")
        html = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
        assert "--dstf-page: #121212;" in html
