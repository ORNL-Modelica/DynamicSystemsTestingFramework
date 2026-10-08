"""The bundled themes (D148): `default` and `dark`, one token table each
(`reporting.theme`) and one figure palette each (`figures.palette`). The
contrast floors are COMPUTED here from the tables, by the WCAG 2 formula:
a number copied from a measurement would pass whatever the table says."""

from __future__ import annotations

import ast
import json
import re
from functools import partial
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


def _figure_cases() -> dict:
    """A figure of every kind both tiers draw: each mode's golden view as
    the report draws it, each view with grips and the handle view as the
    tool draws them, a soft-check overlay beside a context series, and
    three families of four — every stop of every ramp."""
    from dstf.figures.plotly import plot_figure, variable_figure
    from test_figure_goldens import VIEWS
    from test_figures import GRIP_VIEWS, _handle_view
    from test_plot_figure import _labelled, _one_with_overlay, _view

    cases = {
        f"report:{mode}": partial(variable_figure, make(), 200)
        for mode, make in VIEWS.items()
    }
    for mode, make in GRIP_VIEWS.items():
        cases[f"tool:{mode}"] = partial(variable_figure, make(), editable=True)
    cases["tool:handles"] = partial(variable_figure, _handle_view(), editable=True)
    cases["overlay+context"] = partial(
        plot_figure, [_one_with_overlay()], context=[_labelled("z", "a.Q", 2.0)]
    )
    families = [
        _view(f"{name}[{i}]", i, ref=True) for name in "abc" for i in range(1, 5)
    ]
    cases["families"] = partial(plot_figure, families)
    return cases


def _differences(a, b, at="") -> dict[str, tuple]:
    """``{path: (a's value, b's)}`` wherever two figures differ."""
    if isinstance(a, dict) and isinstance(b, dict):
        out: dict[str, tuple] = {}
        for key in a.keys() | b.keys():
            if key not in a or key not in b:
                out[f"{at}/{key}"] = (a.get(key), b.get(key))
            else:
                out.update(_differences(a[key], b[key], f"{at}/{key}"))
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        out = {}
        for i, (x, y) in enumerate(zip(a, b)):
            out.update(_differences(x, y, f"{at}/{i}"))
        return out
    return {} if a == b else {at: (a, b)}


def _line_colours(figure: dict) -> set[str]:
    """Every colour the figure draws a LINE or a marker in: its series,
    its guide lines, its rings and arrows. Not a region's fill."""
    found: set[str] = set()
    for item in (*figure["data"], *figure["layout"].get("shapes", [])):
        for part in (item.get("line"), item.get("marker")):
            colour = (part or {}).get("color")
            found.update(colour if isinstance(colour, list) else [colour])
    return {c for c in found if isinstance(c, str)}


class TestFigures:
    @pytest.mark.parametrize("case", list(_figure_cases()))
    def test_the_default_theme_is_the_figure_as_it_was(self, case):
        """No template key at all, and the same dict as a figure built
        without naming a theme: the goldens hold for both."""
        build = _figure_cases()[case]
        figure = build(theme="default")
        assert figure == build()
        assert "template" not in figure["layout"]

    @pytest.mark.parametrize("case", list(_figure_cases()))
    def test_a_dark_figure_differs_by_its_template_and_its_palette_only(self, case):
        build = _figure_cases()[case]
        default, dark = palette_of("default"), palette_of("dark")
        swaps = {
            (default.reference_event, dark.reference_event),
            (default.ring, dark.ring),
            (default.ring_fill, dark.ring_fill),
            (default.context, dark.context),
            (default.soft_check, dark.soft_check),
            *zip(sum(default.hues, ()), sum(dark.hues, ())),
        }
        diffs = _differences(build(), build(theme="dark"))
        assert diffs.pop("/layout/template") == (None, {"layout": dark.template})
        assert set(diffs.values()) <= swaps, diffs

    def test_each_part_of_the_palette_reaches_a_figure(self):
        """Read off the figures, part by part: a palette entry nothing
        draws would pass the test above."""
        cases = _figure_cases()
        dark = palette_of("dark")

        def swapped(case):
            diffs = _differences(cases[case](), cases[case](theme="dark"))
            diffs.pop("/layout/template")
            return {now for _, now in diffs.values()}

        assert swapped("report:event-timing") == {dark.reference_event}
        assert swapped("tool:handles") == {dark.ring, dark.ring_fill}
        assert swapped("overlay+context") == {dark.soft_check, dark.context}
        assert swapped("families") == {
            dark.hues[0][3],
            dark.hues[1][3],
            dark.hues[2][2],
            dark.hues[2][3],
        }
        assert swapped("report:nrmse") == set()

    def test_the_template_in_a_figure_is_a_copy(self):
        build = _figure_cases()["report:nrmse"]
        figure = build(theme="dark")
        figure["layout"]["template"]["layout"]["xaxis"]["gridcolor"] = "#ff0000"
        figure["layout"]["template"]["layout"]["font"]["color"] = "#ff0000"
        again = build(theme="dark")["layout"]["template"]["layout"]
        assert again == palette_of("dark").template
        assert again["xaxis"]["gridcolor"] == theme.DARK_THEME["border-hairline"]
        assert again["xaxis"] is not again["yaxis"]

    def test_a_dark_figure_is_json(self):
        import json

        for case, build in _figure_cases().items():
            figure = build(theme="dark")
            assert json.loads(json.dumps(figure)) == figure, case

    def test_every_line_a_dark_figure_draws_reads_on_the_dark_plot(self):
        """3:1 for every series, guide line, ring and arrow, over every
        kind of figure — the literals the theme leaves alone too."""
        ground = theme.DARK_THEME["surface"]
        seen: set[str] = set()
        for build in _figure_cases().values():
            seen |= _line_colours(build(theme="dark"))
        opaque = {c for c in seen if HEX.match(c)}
        # What is left out, by name: a marker's white outline, the band's
        # invisible edge, and the translucent pass / fail / muted /
        # window lines, which mark a REGION's edge.
        assert seen - opaque <= {
            "white",
            "rgba(0,0,0,0)",
            "rgba(76,175,80,0.6)",
            "rgba(244,67,54,0.8)",
            "rgba(117,117,117,0.35)",
            "rgba(33,150,243,0.4)",
        }
        assert len(opaque) >= 20, sorted(opaque)
        low = {c: round(contrast(c, ground), 2) for c in opaque}
        assert {c: v for c, v in low.items() if v < 3} == {}

    def test_the_default_figures_dark_lines_would_not_all_read(self):
        """The control for the test above: the same scan over the DEFAULT
        figures finds the five colours the dark palette replaces."""
        ground = theme.DARK_THEME["surface"]
        seen: set[str] = set()
        for build in _figure_cases().values():
            seen |= _line_colours(build())
        sunk = {c for c in seen if HEX.match(c) and contrast(c, ground) < 3}
        assert sunk == {"#7B1FA2", "#0d47a1", "#1b5e20", "#7b1fa2", "#4a148c"}

    def test_an_unknown_theme_is_refused_by_the_builders(self):
        from dstf.figures.plotly import plot_figure, variable_figure
        from test_plot_figure import _view

        for build in (
            partial(variable_figure, _view("x")),
            partial(plot_figure, [_view("x")]),
            partial(plot_figure, [_view("x"), _view("y")]),
        ):
            with pytest.raises(ValueError, match="default, dark"):
                build(theme="solarized")


#: What renders a page or builds a figure, and so takes a theme — the
#: project's for a generated page, the tab's in the tool: a call in `src`
#: that leaves `theme=` out draws the default theme whatever testing.json
#: or the tab says, and nothing else would fail.
RENDERERS = frozenset(
    {
        "render_live",
        "render_final",
        "render_final_to",
        "generate_comparison_plots",
        "ProgressReporter",
        "plot_page_keys",
        "plot_figure",
        "variable_figure",
        # The Session's own two, reached as `session.figure(...)`.
        "figure",
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
        # review's page, and every figure built: the report's two, the
        # Session's and its view's, the plot card's. Fewer means the scan
        # stopped finding them.
        assert len(calls) >= 24, [(p, n) for p, n, _ in calls]
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


def themed_project(tmp_path: Path, name: str | None) -> tuple[Path, Path]:
    """`test_session._page_project` with ``"theme": name`` in its
    testing.json (no key at all for ``None``)."""
    from test_session import _page_project

    cfg, work = _page_project(tmp_path)
    if name is not None:
        data = json.loads(cfg.read_text(encoding="utf-8"))
        cfg.write_text(json.dumps({**data, "theme": name}), encoding="utf-8")
    return cfg, work


#: Every ground the light theme lays behind text: none belongs on a dark
#: page, in its stylesheet or in a figure.
LIGHT_GROUNDS = sorted(
    {
        *(theme.DEFAULT_THEME[t] for t in ("page", "surface", "surface-hover")),
        *(theme.DEFAULT_THEME[t] for t in ("surface-active", "selection")),
        *(parts[part] for parts in theme.STATUS.values() for part in ("tint", "pill")),
    }
)


def _root_block(html: str) -> str:
    (block,) = re.findall(r":root \{\n(.*?)\n\}", html, re.S)
    return block


def _page_figures(html: str) -> list[dict]:
    """The figures a report page embeds, plots then diagnostics."""
    (plots,) = re.findall(r"^\s*PLOT_FIGURES: (\[.*\]),$", html, re.M)
    (diag,) = re.findall(r"^\s*DIAG_FIGURES: (\[.*\]),$", html, re.M)
    return [f for f in (*json.loads(plots), *json.loads(diag)) if f is not None]


class TestGeneratedPages:
    """`dstf compare --report` over a project whose testing.json names a
    theme: the dashboard and every per-test page are generated in it —
    baked, with no switch in the page."""

    def _generate(self, tmp_path, monkeypatch, name) -> list[str]:
        """The dashboard, then every per-test page."""
        from dstf.cli import main

        monkeypatch.setattr("dstf.reporting.report_suite.open_in_browser", lambda p: 0)
        cfg, work = themed_project(tmp_path, name)
        main(["--config", str(cfg), "compare", "--work-dir", str(work), "--report"])
        pages = sorted(work.glob("reports/*/interactive.html"))
        assert len(pages) >= 2, pages
        return [
            p.read_text(encoding="utf-8") for p in (work / "dashboard.html", *pages)
        ]

    def test_dark_pages_carry_the_dark_variables(self, tmp_path, monkeypatch):
        for html in self._generate(tmp_path, monkeypatch, "dark"):
            root = _root_block(html)
            assert root == _root_block(theme.css_variables("dark"))
            for token, value in theme.DARK_THEME.items():
                assert f"--dstf-{token}: {value};" in root, token
            assert "color-scheme: dark;" in root

    def test_dark_pages_hold_no_light_ground_anywhere(self, tmp_path, monkeypatch):
        """In the whole file — stylesheet, markup, scripts, figures."""
        for html in self._generate(tmp_path, monkeypatch, "dark"):
            lowered = html.lower()
            found = [c for c in LIGHT_GROUNDS if c.lower() in lowered]
            assert found == []
            # The short spellings a hand-written rule would use.
            assert not re.search(r"#fff\b|#fafafa\b|background[^;}]*white", lowered)

    def test_the_control_a_default_page_does_hold_them(self, tmp_path, monkeypatch):
        """The scan above finds what it looks for on a default page."""
        for html in self._generate(tmp_path, monkeypatch, None):
            assert "#ffffff" in html.lower() and "#fafafa" in html.lower()
            assert "color-scheme" not in html

    def test_a_dark_report_pages_figures_are_drawn_dark(self, tmp_path, monkeypatch):
        _, *pages = self._generate(tmp_path, monkeypatch, "dark")
        figures = [f for page in pages for f in _page_figures(page)]
        assert len(figures) >= 7  # A's alone: x, y and the five watch plots
        template = palette_of("dark").template
        for figure in figures:
            assert figure["layout"]["template"] == {"layout": template}

    def test_a_default_report_pages_figures_carry_no_template(
        self, tmp_path, monkeypatch
    ):
        _, *pages = self._generate(tmp_path, monkeypatch, "default")
        figures = [f for page in pages for f in _page_figures(page)]
        assert len(figures) >= 7
        assert all("template" not in figure["layout"] for figure in figures)
        for page in pages:
            assert "--dstf-page: #fafafa;" in _root_block(page)

    def test_the_filled_buttons_text_is_the_token(self):
        """White on the dark accent is 2.2:1; the one rule that wrote
        `white` on an accent fill reads `on-accent` instead. A badge keeps
        `white`: it sits on a status fill, the same in every theme."""
        html = (SRC / "reporting" / "templates" / "dashboard.html").read_text(
            encoding="utf-8"
        )
        rule = re.search(r"^\.sel-footer button\.btn-primary \{[^}]*\}", html, re.M)
        assert rule and "color: var(--dstf-on-accent);" in rule.group(0)
        whites = re.findall(r"^([^{\n]+)\{[^}]*color: white;", html, re.M)
        assert [w.strip() for w in whites] == [".status-badge"]
