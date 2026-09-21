"""A plot of several series (DESIGN.md "Plots are modular", "Array families
on a plot"): families by the last integer index, one hue per family ramped
by index, a folded legend — and one series is `variable_figure`, unchanged."""

from __future__ import annotations

import numpy as np
import pytest

from dstf.figures.families import HUES, family_of, group_families, ramp, slice_text
from dstf.figures.plotly import PLOT_CONFIG, plot_figure, variable_figure
from dstf.figures.view import build_view

T = np.linspace(0.0, 1.0, 5)


def _view(name, scale=1.0, *, ref=False):
    return build_view(
        name,
        act_time=T,
        act_values=T * scale,
        ref_time=T if ref else None,
        ref_values=T if ref else None,
    )


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("pipe.T[3]", ("pipe.T[]", 3)),
        ("pipe.medium[12].T", ("pipe.medium[].T", 12)),
        ("a[2].b[7]", ("a[2].b[]", 7)),
        ("x", None),
        ("x[2,3]", None),
        ("der(pipe.T[3])", ("der(pipe.T[])", 3)),
        ("x[i]", None),
    ],
)
def test_family_of_is_the_last_integer_index(name, expected):
    assert family_of(name) == expected


def test_group_families_keeps_first_appearance_order_and_sorts_members():
    names = ["pipe.T[11]", "m", "pipe.T[1]", "pipe.p[2]", "pipe.T[6]"]
    assert group_families(names) == [
        ("pipe.T[]", ["pipe.T[1]", "pipe.T[6]", "pipe.T[11]"]),
        ("m", ["m"]),
        ("pipe.p[]", ["pipe.p[2]"]),
    ]


@pytest.mark.parametrize(
    ("indices", "text"),
    [
        ([1, 6, 11, 16], "1:5:16"),
        ([1, 2, 3, 4], "1:4"),
        ([3], "3"),
        ([1, 2, 4], "1,2,4"),
        ([2, 1], "1:2"),
    ],
)
def test_slice_text_is_modelica_slice_syntax_or_a_list(indices, text):
    assert slice_text(indices) == text


def test_ramp_runs_low_to_high_through_the_hues_four_stops():
    blue = HUES[0]
    assert ramp(blue, 4) == list(blue)
    assert ramp(blue, 1) == [blue[2]]
    two = ramp(blue, 2)
    assert two == [blue[0], blue[3]]
    seven = ramp(blue, 7)
    assert len(seven) == 7 and seven[0] == blue[0] and seven[-1] == blue[3]
    assert seven[3] == _mid(blue[1], blue[2])


def _mid(a: str, b: str) -> str:
    pa = [int(a[i : i + 2], 16) for i in (1, 3, 5)]
    pb = [int(b[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round((x + y) / 2):02x}" for x, y in zip(pa, pb))


def test_the_accent_hue_is_designs_ramp():
    assert HUES[0] == ("#90caf9", "#42a5f5", "#1976d2", "#0d47a1")


def test_one_view_is_variable_figure_unchanged():
    view = _view("x", ref=True)
    assert plot_figure([view], 100, editable=True) == variable_figure(
        view, 100, editable=True
    )


def test_no_views_is_an_empty_titled_figure():
    fig = plot_figure([])
    assert fig["data"] == []
    assert fig["layout"]["xaxis"]["title"]["text"] == "Time"


def _acts(fig):
    return [tr for tr in fig["data"] if tr["meta"]["kind"] == "act"]


def test_a_family_shares_a_hue_ramped_by_index_with_one_legend_entry():
    views = [_view(f"pipe.T[{i}]", i) for i in (11, 1, 6, 16)]
    fig = plot_figure(views)
    acts = _acts(fig)
    assert [tr["meta"]["id"] for tr in acts] == [
        "actual/pipe.T[1]",
        "actual/pipe.T[6]",
        "actual/pipe.T[11]",
        "actual/pipe.T[16]",
    ]
    assert [tr["line"]["color"] for tr in acts] == list(HUES[0])
    assert {tr["legendgroup"] for tr in acts} == {"pipe.T[]"}
    assert [tr["showlegend"] for tr in acts] == [True, False, False, False]
    assert acts[0]["name"] == "pipe.T[1:5:16]"
    assert acts[2]["hovertemplate"] == "pipe.T[11]: %{y:.4g}<extra></extra>"


def test_a_second_family_and_a_scalar_take_the_next_hues():
    views = [_view("pipe.T[1]"), _view("pipe.T[2]"), _view("m"), _view("pipe.p[1]")]
    colors = {tr["meta"]["id"]: tr["line"]["color"] for tr in _acts(plot_figure(views))}
    assert colors["actual/pipe.T[1]"] == HUES[0][0]
    assert colors["actual/pipe.T[2]"] == HUES[0][3]
    assert colors["actual/m"] == HUES[1][2]
    assert colors["actual/pipe.p[1]"] == HUES[2][2]


def test_hues_cycle_past_the_palette():
    views = [_view(n) for n in ("a", "b", "c", "d")]
    colors = [tr["line"]["color"] for tr in _acts(plot_figure(views))]
    assert colors[3] == colors[0] == HUES[0][2]


def test_a_reference_is_the_series_colour_dashed_and_out_of_the_legend():
    fig = plot_figure([_view("x", ref=True), _view("y", 2.0, ref=True)])
    refs = [tr for tr in fig["data"] if tr["meta"]["kind"] == "ref"]
    assert [tr["meta"]["id"] for tr in refs] == ["reference/x", "reference/y"]
    assert [tr["line"]["color"] for tr in refs] == [HUES[0][2], HUES[1][2]]
    assert all(tr["line"]["dash"] == "dash" and not tr["showlegend"] for tr in refs)
    assert refs[0]["legendgroup"] == "x"


def test_several_series_draw_no_overlays_and_no_error_traces():
    one = build_view(
        "x",
        act_time=T,
        act_values=T,
        ref_time=T,
        ref_values=T,
        overlays=[{"name": "om", "role": "soft_check", "time": T, "values": T}],
    )
    fig = plot_figure([one, _view("y")])
    kinds = {tr["meta"]["kind"] for tr in fig["data"]}
    assert kinds == {"act", "ref"}
    assert "yaxis3" not in fig["layout"]


def test_the_budget_decimates_every_series():
    t = np.linspace(0.0, 1.0, 500)
    views = [build_view(n, act_time=t, act_values=t) for n in ("x", "y")]
    fig = plot_figure(views, 50)
    for tr in _acts(fig):
        assert len(tr["x"]) <= 50
        assert tr["meta"]["full_samples"] == 500


def test_a_label_overrides_a_familys_legend_text():
    views = [_view("pipe.T[1]"), _view("pipe.T[2]")]
    fig = plot_figure(views, labels={"pipe.T[]": "pipe.T[1:2] · 2 of 20"})
    assert _acts(fig)[0]["name"] == "pipe.T[1:2] · 2 of 20"


def test_every_leaf_of_every_series_keeps_its_geometry_and_editable_rings():
    from test_figures import _tube_view

    x = _tube_view(
        tube_points=[
            {"time": 0.0, "upper": 0.05, "lower": 0.05},
            {"time": 1.0, "upper": 0.1, "lower": 0.1},
        ]
    )
    assert x.name != "y"
    single = variable_figure(x, editable=True)
    multi = plot_figure([x, _view("y")], editable=True)
    # Marker traces carry meta {"family", "kind": "handle"} and no "id".
    leaf_ids = lambda fig: sorted(  # noqa: E731
        tr["meta"].get("id") or tr["meta"]["family"]
        for tr in fig["data"]
        if tr["meta"]["kind"] not in ("act", "ref", "error", "overlay")
    )
    assert leaf_ids(multi) == leaf_ids(single) and leaf_ids(single)
    assert x.errors, "the fixture must carry error series, or the next line is vacuous"
    assert not any(tr["meta"]["kind"] == "error" for tr in multi["data"])
    names = [s.get("name") for s in single["layout"]["shapes"]]
    assert [s.get("name") for s in multi["layout"]["shapes"]] == names
    assert any(n and n.startswith("/metrics/children/0/") for n in names), (
        "the fixture must carry at least one handle ring, or this test is vacuous"
    )
    assert multi["config"]["edits"] == {"shapePosition": True}
    plain = plot_figure([x, _view("y")])
    assert "edits" not in plain["config"]
    # The tool's plots are stacked down the Test page: the wheel scrolls the
    # page, and only the report's one figure per page zooms with it.
    assert multi["config"]["scrollZoom"] is False
    assert plain["config"] == PLOT_CONFIG and PLOT_CONFIG["scrollZoom"] is True
    assert not any(
        (s.get("name") or "").startswith("/metrics/") and s.get("xsizemode") == "pixel"
        for s in plain["layout"]["shapes"]
    )


def test_rings_for_reaches_every_view_of_a_multi_series_plot():
    from test_figures import _ring_names, _two_ring_view

    views = [
        _two_ring_view("x"),
        _two_ring_view("y", ("/metrics/children/2", "/metrics/children/3")),
    ]
    every = _ring_names(plot_figure(views, None, editable=True))
    assert len(every) == 4
    for path in ("/metrics/children/0", "/metrics/children/3"):
        only = _ring_names(plot_figure(views, None, editable=True, rings_for=(path,)))
        assert only and only < every
        assert all(n.startswith(path + "/") for n in only)
    full = plot_figure(views, None, editable=True)
    none = plot_figure(views, None, editable=True, rings_for=())
    assert _ring_names(none) == set()
    assert none["data"] == full["data"] and none["config"] == full["config"]


def test_rings_for_reaches_the_single_view_branch():
    from test_figures import _ring_names, _two_ring_view

    view = _two_ring_view()
    assert plot_figure([view], None, editable=True, rings_for=()) == variable_figure(
        view, None, editable=True, rings_for=()
    )
    assert _ring_names(plot_figure([view], None, editable=True, rings_for=())) == set()


def test_a_family_keeps_its_legend_entry_when_its_first_member_has_no_result():
    ghost = build_view("p.T[1]")  # no trajectory: the index is tracked, the run lost it
    fig = plot_figure([ghost, _view("p.T[2]"), _view("p.T[3]")])
    acts = _acts(fig)
    assert [tr["meta"]["id"] for tr in acts] == ["actual/p.T[2]", "actual/p.T[3]"]
    assert [tr["showlegend"] for tr in acts] == [True, False]
    assert acts[0]["name"] == "p.T[1:3]"


def _spectrum_view(name="s"):
    from test_figures import _compare_dominant_frequency, _stamped

    t = np.linspace(0.0, 10.0, 1001)
    sig = np.sin(2 * np.pi * 2.0 * t)
    vc = _compare_dominant_frequency(
        t, sig, t, sig, peaks=[{"freq": 2.0, "tolerance": 0.05}]
    )
    vc.name = name
    return build_view(
        name,
        act_time=t,
        act_values=sig,
        ref_time=t,
        ref_values=sig,
        leaves=[_stamped(vc, "/metrics/children/1")],
    )


def test_a_spectrum_leaf_on_any_series_opens_the_second_panel_for_all(monkeypatch):
    """The plot is one figure: every leaf is laid out against the UNION of the
    series' time spans and against the panels ANY series opens — a time-panel
    shape of a plain series must not span the spectrum panel's half."""
    from dstf.figures import plotly
    from test_figures import _tube_view

    seen = []
    real = plotly._leaf_traces

    def spy(view, ctx, **kwargs):
        seen.append((view.name, ctx))
        return real(view, ctx, **kwargs)

    monkeypatch.setattr(plotly, "_leaf_traces", spy)
    plain = _tube_view()  # named "x", 0..1 s, no spectrum
    fig = plot_figure([plain, _spectrum_view()])
    assert fig["layout"]["xaxis2"]["title"]["text"] == "Frequency [Hz]"
    assert fig["layout"]["yaxis"]["domain"] != [0, 1]
    spectrum = next(
        tr
        for tr in fig["data"]
        if tr["meta"].get("id") == "/metrics/children/1/spectrum/act"
    )
    assert spectrum["xaxis"] == "x2" and spectrum["yaxis"] == "y2"
    assert [name for name, _ in seen] == ["x", "s"]
    for _, ctx in seen:
        assert ctx.has_spectrum is True
        assert (ctx.x_min, ctx.x_max) == (0.0, 10.0)


# ---------------------------------------------------------------------------
# Labels: a variable reads as its expression and is keyed by its name
# ---------------------------------------------------------------------------

N1, N2, N3 = "unitTests.x[1]", "unitTests.x[2]", "unitTests.x[3]"


def _labelled(name, label, scale=1.0, *, ref=False):
    return build_view(
        name,
        act_time=T,
        act_values=T * scale,
        ref_time=T if ref else None,
        ref_values=T if ref else None,
        label=label,
    )


def test_the_label_rule_is_the_expression_or_the_name():
    from dstf.backends.types import display_label, label_with_name

    assert display_label(N1, "pipe.T[1]") == "pipe.T[1]"
    assert display_label("y", "") == "y"
    # The text slice: label (name) only when they differ.
    assert label_with_name(N1, "pipe.T[1]") == "pipe.T[1] (unitTests.x[1])"
    assert label_with_name("y", "") == "y"
    assert label_with_name("y", "y") == "y"


def test_a_view_without_a_label_reads_as_its_name():
    assert _view("y").label == "y"
    assert _labelled(N1, "pipe.T[1]").label == "pipe.T[1]"


def test_a_multi_series_plot_reads_the_labels_and_is_keyed_by_the_names():
    fig = plot_figure([_labelled(N1, "pipe.T[1]", ref=True), _labelled(N2, "a.Q")])
    by_id = {tr["meta"]["id"]: tr for tr in fig["data"] if tr.get("meta")}
    assert f"actual/{N1}" in by_id and f"reference/{N1}" in by_id
    assert not any("pipe.T" in i for i in by_id)
    assert by_id[f"reference/{N1}"]["name"] == "pipe.T[1] (reference)"
    assert by_id[f"actual/{N2}"]["hovertemplate"].startswith("a.Q:")
    # The legend entry of a family of one is its label.
    assert by_id[f"actual/{N2}"]["name"] == "a.Q"


def test_a_single_variables_hover_carries_the_label():
    fig = variable_figure(_labelled(N1, "pipe.T[1]", ref=True))
    by_id = {tr["meta"]["id"]: tr for tr in fig["data"] if tr.get("meta")}
    # The trace NAMES stay "Actual" / "Reference" (report.js and the overlay
    # picker find them by those); the hover says which variable it is.
    assert by_id["actual"]["name"] == "Actual"
    assert by_id["actual"]["hovertemplate"].startswith("pipe.T[1]:")
    assert by_id["reference"]["hovertemplate"].startswith("pipe.T[1] (reference):")


def test_a_variable_with_no_expression_draws_exactly_what_it_did():
    """The byte-for-byte predicate for every spec-tracked variable: a view
    whose label IS its name gets no hover text it did not have."""
    fig = variable_figure(_view("y", ref=True))
    base = [tr for tr in fig["data"] if tr["meta"]["kind"] in ("act", "ref")]
    assert len(base) == 2
    for trace in base:
        assert "hovertemplate" not in trace


def test_families_fold_over_labels_not_names():
    """`unitTests.x[1..3]` is not an array: the three carry unrelated
    expressions, and folding them into one family gives three quantities one
    legend entry and one colour ramp."""
    labels = {N1: "a.Q", N2: "b.Q", N3: "sum(core.Qs[6, :])"}
    assert group_families([N1, N2, N3], labels) == [(N1, [N1]), (N2, [N2]), (N3, [N3])]
    # Labels that ARE an array fold, under the label's own key.
    indexed = {N1: "pipe.T[1]", N2: "pipe.T[2]"}
    assert group_families([N1, N2], indexed) == [("pipe.T[]", [N1, N2])]


def test_a_family_label_reads_the_labels():
    from dstf.figures.families import family_label

    indexed = {N1: "pipe.T[1]", N2: "pipe.T[2]"}
    (key, members) = group_families([N1, N2], indexed)[0]
    assert family_label(key, members, indexed) == "pipe.T[1:2]"
    assert family_label(*group_families([N1], {N1: "a.Q"})[0], {N1: "a.Q"}) == "a.Q"


@pytest.mark.parametrize("label", ["cat(1, a, b)[2]", "sum(core.Qs[6, :])", "a.Q"])
def test_an_expression_that_is_not_a_plain_indexed_name_has_no_family(label):
    assert family_of(label) is None


def test_a_folded_family_of_labels_takes_one_legend_entry():
    fig = plot_figure(
        [_labelled(N1, "pipe.T[1]"), _labelled(N2, "pipe.T[2]", scale=2.0)]
    )
    legend = [tr["name"] for tr in fig["data"] if tr.get("showlegend")]
    assert legend == ["pipe.T[1:2]"]
