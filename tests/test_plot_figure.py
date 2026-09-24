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
    # The label is out of the TEMPLATE and in `meta`: plotly.js
    # substitutes every `%{…}` in a hovertemplate from the trace's data,
    # and a scalar `text` is not data it substitutes from. `.4~g`: 4
    # significant digits, trailing zeros trimmed (the owner's ruling).
    assert acts[2]["hovertemplate"] == "%{meta.label}: %{y:.4~g}<extra></extra>"
    assert acts[2]["meta"]["label"] == "pipe.T[11]"
    # The hover did not cost the trace the key everything addresses it by.
    assert acts[2]["meta"]["id"] == "actual/pipe.T[11]"


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
    assert by_id[f"actual/{N2}"]["meta"]["label"] == "a.Q"
    # The legend entry of a family of one is its label.
    assert by_id[f"actual/{N2}"]["name"] == "a.Q"


def test_a_single_variables_hover_carries_the_label():
    fig = variable_figure(_labelled(N1, "pipe.T[1]", ref=True))
    by_id = {tr["meta"]["id"]: tr for tr in fig["data"] if tr.get("meta")}
    # The trace NAMES stay "Actual" / "Reference" (report.js and the overlay
    # picker find them by those); the hover says which variable it is.
    assert by_id["actual"]["name"] == "Actual"
    assert by_id["actual"]["meta"]["label"] == "pipe.T[1]"
    assert by_id["reference"]["meta"]["label"] == "pipe.T[1] (reference)"


def test_a_variable_with_no_expression_draws_exactly_what_it_did():
    """The byte-for-byte predicate for every spec-tracked variable: a view
    whose label IS its name gets no hover text it did not have."""
    fig = variable_figure(_view("y", ref=True))
    base = [tr for tr in fig["data"] if tr["meta"]["kind"] in ("act", "ref")]
    assert len(base) == 2
    for trace in base:
        assert "hovertemplate" not in trace
        assert "text" not in trace


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
    assert family_of(label, expression=True) is None


def test_a_derivative_indexed_after_the_call_is_a_family_member():
    """`der(x)[1]` is a plain indexed name of a derivative — the derivative
    of an array, element by element — so `der(x)[1..3]` is one family, one
    legend entry and one colour ramp. `der(x)` alone is a family of one."""
    assert family_of("der(x)[1]", expression=True) == ("der(x)[]", 1)
    labels = {N1: "der(x)[1]", N2: "der(x)[2]"}
    assert group_families([N1, N2], labels) == [("der(x)[]", [N1, N2])]


def test_a_second_derivative_is_not_folded():
    """`der(x[1], 2)` is a call of TWO arguments: the index is the
    argument's, and nothing here can say the call returns an element of an
    array. It stays a family of one — reading `der(x[1:2], 2)` over two of
    them would name a third thing neither of them is."""
    assert family_of("der(x[1], 2)", expression=True) is None
    labels = {N1: "der(x[1], 2)", N2: "der(x[2], 2)"}
    assert group_families([N1, N2], labels) == [(N1, [N1]), (N2, [N2])]


def test_a_folded_family_of_labels_takes_one_legend_entry():
    fig = plot_figure(
        [_labelled(N1, "pipe.T[1]"), _labelled(N2, "pipe.T[2]", scale=2.0)]
    )
    legend = [tr["name"] for tr in fig["data"] if tr.get("showlegend")]
    assert legend == ["pipe.T[1:2]"]


def _family_of_at_2ac8c8c(name: str):
    """`figures/families.py::family_of` as it stood at 2ac8c8c, before a
    label could differ from a name. Copied, not imported: the point is that
    the current one still agrees with it over NAMES."""
    import re

    if re.search(r"\[[^\]]*,[^\]]*\]", name):
        return None
    m = re.compile(r"\[(\d+)\](?!.*\[\d+\])").search(name)
    if m is None:
        return None
    return name[: m.start()] + "[]" + name[m.end() :], int(m.group(1))


def _generated_result_file_names(n: int, seed: int = 20260921) -> list[str]:
    """Result-file variable NAMES of every shape a solver writes one: dotted
    components, quoted identifiers (spaces, dots, brackets, commas inside),
    letters outside ASCII (`θ`, `Ω`) and symbols no whitelist anticipates
    (Julia/MTK's `₊`, Dymola's `$DER.`, a hyphen), single and
    multi-dimensional indices, negated aliases, `der(...)` nested and with a
    second argument."""
    import random

    rng = random.Random(seed)
    idents = [
        "a",
        "pipe",
        "T",
        "x_1",
        "_q",
        "medium",
        "der",
        "sum",
        "θ",
        "Ωx",
        "pipe₊T",
        "T-wall",
        "$DER.x",
        "$cse1",
        "'b c'",
        "'a.b'",
        "'a[1]'",
        "'x(y)'",
        "'a,b'",
    ]

    def index() -> str:
        r = rng.random()
        if r < 0.5:
            return f"[{rng.randint(1, 12)}]"
        if r < 0.65:
            return f"[{rng.randint(1, 9)},{rng.randint(1, 9)}]"
        if r < 0.8:
            return f"[{rng.randint(1, 9)}, {rng.randint(1, 9)}]"
        if r < 0.85:
            return f"[{rng.randint(1, 3)},{rng.randint(1, 3)},{rng.randint(1, 3)}]"
        if r < 0.9:
            return "[:]"
        return ""

    out = []
    for _ in range(n):
        name = ".".join(
            rng.choice(idents) + (index() if rng.random() < 0.5 else "")
            for _ in range(rng.randint(1, 4))
        )
        r = rng.random()
        if r < 0.15:
            name = f"der({name})"
        elif r < 0.2:
            name = f"der(der({name}))"
        elif r < 0.25:
            name = f"der({name}, 2)"
        if rng.random() < 0.1:
            name = "-" + name
        out.append(name)
    return out


def test_family_of_answers_a_name_exactly_as_it_did_before_labels():
    """A differential over generated names, not a hand-list: the hand-list
    agreed while `θ[1]`, `pipe₊T[1]`, `T-wall[1]`, `$DER.x[1]`, `-der(x[2])`
    and `der(x[2], 2)` had all silently stopped being families (the b147918
    review fuzzed 300k names against this copy). A result file spells names
    the figure layer does not get to whitelist."""
    names = _generated_result_file_names(25000)
    assert len(set(names)) > 1000  # the generator is not producing one name
    differing = [n for n in names if family_of(n) != _family_of_at_2ac8c8c(n)]
    assert differing == []
    # and it is a real test: some of them DO have families.
    assert any(family_of(n) is not None for n in names)


@pytest.mark.parametrize(
    "name",
    ["θ[1]", "pipe₊T[1]", "T-wall[1]", "$DER.x[1]", "-der(x[2])", "der(x[2], 2)"],
)
def test_a_name_no_ascii_whitelist_anticipates_still_has_a_family(name):
    """The regression the fuzz found: each of these was a family at 2ac8c8c,
    and `θ[1..3]` drawn as three families is three hues for one array."""
    assert family_of(name) == _family_of_at_2ac8c8c(name)
    assert family_of(name) is not None


def test_non_ascii_names_fold_into_one_family():
    names = ["θ[1]", "θ[2]", "θ[3]"]
    assert group_families(names) == [("θ[]", names)]


@pytest.mark.parametrize(
    "label",
    ["sum(pipe.Qs[1])", "sum(pipe.Qs[2])", "abs(x[1])", "max(a.b[2])", "cat(1, y)[1]"],
)
def test_a_reduction_over_an_array_is_not_an_element_of_one(label):
    """`sum(pipe.Qs[1])` and `sum(pipe.Qs[2])` are two quantities. Folded,
    they took one legend entry, one colour ramp and a label —
    `sum(pipe.Qs[1:2])` — naming a third thing neither of them is."""
    assert family_of(label, expression=True) is None


@pytest.mark.parametrize("label", ["-der(x[2])", "der(-x[2])"])
def test_a_negated_derivative_is_an_element_either_way_round(label):
    """The two spellings of one quantity must agree: `der(-x[2])` was an
    element of an array and `-der(x[2])` was not."""
    fam = family_of(label, expression=True)
    assert fam is not None and fam[1] == 2


def test_two_reductions_of_one_array_stay_two_families():
    labels = {N1: "sum(pipe.Qs[1])", N2: "sum(pipe.Qs[2])"}
    assert group_families([N1, N2], labels) == [(N1, [N1]), (N2, [N2])]


def test_two_series_sharing_one_expression_read_label_and_name():
    """`x={a.Q, a.Q}` is legal: two result-file variables, one expression.
    Read as the label alone, one legend entry, one hover and one colour
    stood for both with no way to tell which curve was which.

    The disambiguation happens ONCE, in the TEST's label scope; the figure
    draws what the views were built with. Disambiguating here as well was
    a second scope, and a variable read `a.Q` in this legend while its
    criteria row read `a.Q (unitTests.x[1])`."""
    from dstf.backends.types import variable_texts

    texts = variable_texts({N1: "a.Q", N2: "a.Q"})
    fig = plot_figure(
        [
            _labelled(N1, texts[N1].text, ref=True),
            _labelled(N2, texts[N2].text, scale=2.0),
        ]
    )
    by_id = {tr["meta"]["id"]: tr for tr in fig["data"] if tr.get("meta")}
    assert by_id[f"actual/{N1}"]["name"] == f"a.Q ({N1})"
    assert by_id[f"actual/{N2}"]["name"] == f"a.Q ({N2})"
    assert by_id[f"actual/{N1}"]["meta"]["label"] == f"a.Q ({N1})"
    assert by_id[f"reference/{N1}"]["name"] == f"a.Q ({N1}) (reference)"
    # Two legend entries, two hues: `label (name)` belongs to no family.
    assert [tr["name"] for tr in fig["data"] if tr.get("showlegend")] == [
        f"a.Q ({N1})",
        f"a.Q ({N2})",
    ]
    colors = {by_id[f"actual/{n}"]["line"]["color"] for n in (N1, N2)}
    assert len(colors) == 2


def test_two_series_with_their_own_expressions_read_the_label_alone():
    """The guard on the rule above: the name rides along only where the
    label is ambiguous."""
    fig = plot_figure([_labelled(N1, "a.Q"), _labelled(N2, "b.Q")])
    by_id = {tr["meta"]["id"]: tr for tr in fig["data"] if tr.get("meta")}
    assert by_id[f"actual/{N1}"]["name"] == "a.Q"
    assert by_id[f"actual/{N2}"]["name"] == "b.Q"


@pytest.mark.parametrize("build", ["single", "multi"])
def test_a_label_holding_a_plotly_placeholder_is_printed_not_substituted(build):
    """`%{x}` in a hovertemplate is a data reference plotly.js substitutes,
    and it has no escape — so a label never enters the template. It rides
    in the trace's `meta`, which the template references as one whole
    value: `%{meta.label}` prints a label holding `%{x}` exactly as it is
    (measured in Chromium over the vendored plotly.js).

    The legend is NOT defended the same way: plotly.js templates a trace
    NAME from `meta` too, and a name holding `%{x}` rendered as `ab` in the
    legend of that same page. No Modelica expression spells one — this
    test is about the template, not about a real label — so nothing here
    tries to escape a placeholder into a legend entry."""
    views = [_labelled(N1, "a%{x}b", ref=True)]
    if build == "multi":
        views.append(_labelled(N2, "c.Q"))
    fig = plot_figure(views) if build == "multi" else variable_figure(views[0])
    drawn = [tr for tr in fig["data"] if tr.get("meta", {}).get("kind") == "act"]
    # One view names its actual trace "actual"; several name it "actual/<name>".
    mine = next(tr for tr in drawn if tr["meta"]["id"] in ("actual", f"actual/{N1}"))
    assert "a%{x}b" in mine["meta"]["label"]
    assert "a%{x}b" not in mine["hovertemplate"]
    # `.4~g` trims a hover's trailing zeros (the owner's 2026-09-24 ruling).
    assert mine["hovertemplate"] == "%{meta.label}: %{y:.4~g}<extra></extra>"


@pytest.mark.parametrize("build", ["single", "multi"])
def test_a_label_holding_markup_reaches_plotly_escaped(build):
    """Plotly renders a trace name and a hovertemplate through a small HTML
    subset, so `a<b>c` would lose its middle. The name is a KEY and is
    never escaped."""
    views = [_labelled(N1, "a<b>c", ref=True)]
    if build == "multi":
        views.append(_labelled(N2, "d&e"))
    fig = plot_figure(views) if build == "multi" else variable_figure(views[0])
    texts = [
        tr.get("name", "") + (tr.get("meta") or {}).get("label", "")
        for tr in fig["data"]
        if tr.get("meta", {}).get("kind") in ("act", "ref")
    ]
    assert any("a&lt;b&gt;c" in t for t in texts)
    assert not any("a<b>c" in t for t in texts)
    ids = [tr["meta"]["id"] for tr in fig["data"] if tr.get("meta")]
    assert not any("&lt;" in i for i in ids)


def test_a_plot_of_several_takes_visibility():
    from dstf.comparison.algorithms import _compare_range
    from dstf.figures.plotly import Visibility
    from test_figures import RANGE_P, _stamped, _tube_and_range_view

    vx = _tube_and_range_view()
    hidden = "/metrics/children/2"
    rng = _compare_range(vx.act_time, vx.act_values, min_value=-1.0, max_value=1.0)
    rng.name = "y"
    vy = build_view(
        "y",
        act_time=vx.act_time,
        act_values=vx.act_values,
        ref_time=vx.ref_time,
        ref_values=vx.ref_values,
        leaves=[_stamped(rng, hidden)],
    )
    plain = plot_figure([vx, vy])
    fig = plot_figure([vx, vy], visibility=Visibility(hidden=frozenset({hidden})))
    mine = [tr for tr in fig["data"] if (tr.get("meta") or {}).get("leaf") == hidden]
    assert mine and all(tr["visible"] is False for tr in mine)
    shapes = fig["layout"]["shapes"]
    assert [s["name"] for s in shapes] == [s["name"] for s in plain["layout"]["shapes"]]
    assert any(s["name"].startswith(hidden + "/") for s in shapes)
    assert all(
        s["visible"] is False for s in shapes if s["name"].startswith(hidden + "/")
    )
    assert all(
        "visible" not in s for s in shapes if not s["name"].startswith(hidden + "/")
    )

    def acts(f):
        return [tr["showlegend"] for tr in f["data"] if tr["meta"].get("kind") == "act"]

    assert acts(fig) == acts(plain) == [True, True]

    # One view is variable_figure's branch: the visibility must reach it too.
    one = plot_figure([vx], visibility=Visibility(hidden=frozenset({RANGE_P})))
    own = [tr for tr in one["data"] if (tr.get("meta") or {}).get("leaf") == RANGE_P]
    assert own and all(tr["visible"] is False for tr in own)
