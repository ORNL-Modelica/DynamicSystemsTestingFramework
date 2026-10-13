"""The figure hook's dispatch contract (§4.6, amended in sub-project 6): pure
tests, no page. Moved from tests/test_shell.py with the hook."""

import pytest

pytest.importorskip("nicegui")

from dstf.authoring.shell.figure_hook import FigureHook  # noqa: E402


def test_the_figure_hook_names_the_primitive_from_meta_id():
    hook = FigureHook()
    seen = []
    hook.on("plotly_click", lambda primitive, payload: seen.append(primitive))
    hook._dispatch(
        "plotly_click",
        {
            "points": [
                {
                    "curveNumber": 0,
                    "data": {"meta": {"id": "/metrics/children/0/tube/band"}},
                }
            ]
        },
    )
    hook._dispatch(
        "plotly_click",
        {"points": [{"curveNumber": 0, "customdata": "/metrics/children/1/points/2"}]},
    )
    hook._dispatch("plotly_click", {"points": [{"curveNumber": 0}]})
    hook._dispatch("plotly_click", None)
    # A point carrying both: customdata wins over meta.id.
    hook._dispatch(
        "plotly_click",
        {
            "points": [
                {
                    "curveNumber": 0,
                    "customdata": "/metrics/children/1/points/3",
                    "data": {"meta": {"id": "/metrics/children/1/points"}},
                }
            ]
        },
    )
    assert seen == [
        "/metrics/children/0/tube/band",
        "/metrics/children/1/points/2",
        "0",
        "",
        "/metrics/children/1/points/3",
    ]


class _StandInPlot:
    """What `FigureHook.attach` needs of a `ui.plotly`: a `figure` dict and
    an `on` that records registrations. No page."""

    def __init__(self, figure=None):
        self.figure = figure if figure is not None else {}
        self.registered = []
        self.in_page = {}  # event -> the `js_handler` it was registered with

    def on(self, event, handler, **in_page):
        self.registered.append(event)
        self.in_page[event] = in_page


def _recording_hook(plot=None):
    moved = []
    hook = FigureHook(on_shape_moved=lambda: moved.append(True))
    seen = []
    for kind in ("plotly_click", "plotly_relayout", "plotly_selected"):
        hook.on(
            kind,
            lambda primitive, payload, k=kind: seen.append((k, primitive, payload)),
        )
    if plot is not None:
        hook.attach(plot)
    return hook, seen, moved


def test_an_x_unified_click_names_the_handle_not_the_first_trace():
    """The spike: under the figure's `hovermode: "x unified"` a click on a
    handle lists the Actual trace first. A selection prefers the handle the
    same way."""
    hook, seen, moved = _recording_hook()
    payload = {
        "event": {},
        "points": [
            {
                "curveNumber": 0,
                "pointNumber": 50,
                "x": 5,
                "y": 5,
                "data": {"meta": {"id": "actual", "kind": "act"}},
            },
            {
                "curveNumber": 1,
                "pointNumber": 1,
                "x": 5,
                "y": 7,
                "customdata": "/metrics/children/0/tube/upper/1",
                "data": {"meta": {"family": "tube/upper", "kind": "handle"}},
            },
        ],
    }
    hook._dispatch("plotly_click", payload)
    hook._dispatch("plotly_selected", payload)
    assert [(kind, primitive) for kind, primitive, _ in seen] == [
        ("plotly_click", "/metrics/children/0/tube/upper/1"),
        ("plotly_selected", "/metrics/children/0/tube/upper/1"),
    ]
    assert moved == []


_SHAPES = {
    "layout": {
        "shapes": [
            {"name": "/metrics/children/1/range/max"},
            {"name": "/metrics/children/2/points/box/0"},
            {"name": "/metrics/children/0/tube/lower/0"},
        ]
    }
}


def test_a_shape_relayout_is_dispatched_per_shape_with_the_rendered_name():
    """The spike's drag payloads carry the shape's index, never its name."""
    plot = _StandInPlot(_SHAPES)
    hook, seen, moved = _recording_hook(plot)
    assert plot.registered == ["plotly_click", "plotly_relayout", "plotly_selected"]
    cases = [
        (
            {
                "shapes[0].x0": 0,
                "shapes[0].x1": 10,
                "shapes[0].y0": 6.0,
                "shapes[0].y1": 6.0,
            },
            [
                (
                    "/metrics/children/1/range/max",
                    {"x0": 0, "x1": 10, "y0": 6.0, "y1": 6.0},
                )
            ],
        ),
        (
            {"shapes[2].xanchor": 4.0, "shapes[2].yanchor": 2.99},
            [("/metrics/children/0/tube/lower/0", {"xanchor": 4.0, "yanchor": 2.99})],
        ),
        (
            {"shapes[0].x0": 0, "shapes[0].y0": 2.0},
            [("/metrics/children/1/range/max", {"x0": 0, "y0": 2.0})],
        ),
        (
            {"shapes[2].xanchor": 4.0, "shapes[0].y0": 2.0, "shapes[2].yanchor": 2.99},
            [
                ("/metrics/children/1/range/max", {"y0": 2.0}),
                ("/metrics/children/0/tube/lower/0", {"xanchor": 4.0, "yanchor": 2.99}),
            ],
        ),
    ]
    for payload, expected in cases:
        seen.clear()
        moved.clear()
        hook._dispatch("plotly_relayout", payload)
        assert [(p, d) for _, p, d in seen] == expected
        assert moved == [True]  # once per relayout, however many shapes


def test_a_zoom_relayout_is_dispatched_once_raw_and_moves_no_shape():
    hook, seen, moved = _recording_hook(_StandInPlot(_SHAPES))
    zoom = {"xaxis.range[0]": 0.49, "xaxis.range[1]": 1.49}
    hook._dispatch("plotly_relayout", zoom)
    assert [(p, d) for _, p, d in seen] == [("", zoom)]
    assert moved == []


def test_a_relayout_mixing_zoom_and_shape_keys_is_a_shape_relayout():
    hook, seen, moved = _recording_hook(_StandInPlot(_SHAPES))
    hook._dispatch("plotly_relayout", {"xaxis.range[0]": 0.49, "shapes[0].y0": 2.0})
    assert [(p, d) for _, p, d in seen] == [
        ("/metrics/children/1/range/max", {"y0": 2.0})
    ]
    assert moved == [True]


def test_the_shape_moved_callback_runs_when_a_handler_raises():
    moved = []
    hook = FigureHook(on_shape_moved=lambda: moved.append(True))
    hook.attach(_StandInPlot(_SHAPES))

    def boom(primitive, payload):
        raise RuntimeError("boom")

    hook.on("plotly_relayout", boom)
    with pytest.raises(RuntimeError):
        hook._dispatch("plotly_relayout", {"shapes[0].y0": 2.0})
    assert moved == [True]


def test_nothing_malformed_makes_the_hook_dispatch_raise():
    """The docstring's "no malformed payload makes dispatch raise": every
    input it rules out, tried."""
    # A shape key whose index cannot be resolved: id "" for that shape, and
    # the shape-moved callback still runs.
    unresolvable = [
        (_StandInPlot(_SHAPES), {"shapes[9].y0": 1.0}, {"y0": 1.0}),
        (None, {"shapes[0].y0": 1.0}, {"y0": 1.0}),
        (
            _StandInPlot({"layout": {"shapes": [{"type": "line"}]}}),
            {"shapes[0].y0": 1.0},
            {"y0": 1.0},
        ),
        (_StandInPlot({"layout": {}}), {"shapes[0].y0": 1.0}, {"y0": 1.0}),
        (_StandInPlot("not a dict"), {"shapes[0].y0": 1.0}, {"y0": 1.0}),
    ]
    for plot, payload, stripped in unresolvable:
        hook, seen, moved = _recording_hook(plot)
        hook._dispatch("plotly_relayout", payload)
        assert [(p, d) for _, p, d in seen] == [("", stripped)]
        assert moved == [True]
    # A key NOT matching the shape pattern is any other relayout.
    # A 5000-digit index too: past `int()`'s digit limit, it must not raise.
    for payload in (
        {"shapes[x].y0": 1.0},
        {"shapes[0]": 1.0},
        {"shapes[" + "9" * 5000 + "].y0": 1.0},
    ):
        hook, seen, moved = _recording_hook(_StandInPlot(_SHAPES))
        hook._dispatch("plotly_relayout", payload)
        assert [(p, d) for _, p, d in seen] == [("", payload)]
        assert moved == []
    for kind in ("plotly_click", "plotly_relayout", "plotly_selected"):
        for payload in (
            None,
            [],
            {"points": [5, "x", None]},
            {"points": "not a list"},
            {"points": [{"curveNumber": 2, "data": {"meta": "not a dict"}}]},
            {"points": [{"data": "not a dict"}]},
        ):
            hook = FigureHook()
            got: list[tuple[str, dict]] = []
            hook.on(
                kind, lambda primitive, data, got=got: got.append((primitive, data))
            )
            hook._dispatch(kind, payload)
            assert len(got) == 1 and isinstance(got[0][0], str)
            if not isinstance(payload, dict):
                assert got[0][1] == {}  # a non-dict payload becomes {}


def test_the_figure_hook_clears_handlers_by_owner():
    hook = FigureHook()
    got = []

    def h1(primitive, payload):
        got.append("h1")

    def h2(primitive, payload):
        got.append("h2")

    hook.owned("a").on("plotly_click", h1)
    hook.owned("b").on("plotly_click", h2)
    hook._dispatch("plotly_click", {})
    assert got == ["h1", "h2"]
    hook.clear("a")
    got.clear()
    hook._dispatch("plotly_click", {})
    assert got == ["h2"]
    hook.clear()
    got.clear()
    hook._dispatch("plotly_click", {})
    assert got == []


def test_the_figure_hook_dispatches_from_a_snapshot():
    """A handler registered during dispatch — a panel rebuilt by the act it
    triggered — is first called on the next event."""
    hook = FigureHook()
    calls = {"old": 0, "new": 0, "extra": 0}

    def bounded(name):
        calls[name] += 1
        if sum(calls.values()) > 5:
            raise AssertionError(f"dispatch did not stop: {calls}")

    def new(primitive, payload):
        bounded("new")

    def old(primitive, payload):
        bounded("old")
        hook.clear("a")  # what a slot rebuild does
        hook.owned("a").on("plotly_click", new)

    hook.owned("a").on("plotly_click", old)
    hook._dispatch("plotly_click", {})
    assert (calls["old"], calls["new"]) == (1, 0)
    hook._dispatch("plotly_click", {})
    assert (calls["old"], calls["new"]) == (1, 1)

    # A handler that registers another without clearing: the added one does
    # not run in the dispatch that added it.
    hook = FigureHook()
    calls.update(old=0, new=0, extra=0)

    def extra(primitive, payload):
        bounded("extra")

    def adder(primitive, payload):
        bounded("old")
        hook.owned("b").on("plotly_click", extra)

    hook.owned("a").on("plotly_click", adder)
    hook._dispatch("plotly_click", {})
    assert (calls["old"], calls["extra"]) == (1, 0)


def test_a_handler_removed_during_dispatch_is_skipped():
    """A handler removed since the snapshot does not run, so a rebuilt
    panel's destroyed predecessor never sees the event."""
    hook = FigureHook()
    calls = {"a": 0, "b_old": 0, "b_new": 0}

    def b_new(primitive, payload):
        calls["b_new"] += 1

    def b_old(primitive, payload):
        calls["b_old"] += 1

    def a(primitive, payload):
        calls["a"] += 1
        if calls["a"] == 1:
            hook.clear("b")
            hook.owned("b").on("plotly_relayout", b_new)

    hook.attach(_StandInPlot(_SHAPES))
    hook.owned("a").on("plotly_relayout", a)
    hook.owned("b").on("plotly_relayout", b_old)
    hook._dispatch("plotly_relayout", {"shapes[0].y0": 1.0})
    assert calls == {"a": 1, "b_old": 0, "b_new": 0}
    hook._dispatch("plotly_relayout", {"shapes[0].y0": 1.0})
    assert calls == {"a": 2, "b_old": 0, "b_new": 1}


def test_each_shape_of_a_relayout_is_dispatched_from_its_own_snapshot():
    hook = FigureHook()
    hook.attach(_StandInPlot(_SHAPES))
    seen = []

    def a_new(primitive, payload):
        seen.append(("a_new", primitive))

    def a_old(primitive, payload):
        seen.append(("a_old", primitive))
        hook.clear("a")
        hook.owned("a").on("plotly_relayout", a_new)

    hook.owned("a").on("plotly_relayout", a_old)
    hook._dispatch("plotly_relayout", {"shapes[0].y0": 1, "shapes[1].y0": 2})
    assert seen == [
        ("a_old", "/metrics/children/1/range/max"),
        ("a_new", "/metrics/children/2/points/box/0"),
    ]


def test_the_figure_hook_is_dispatching_only_while_handlers_run():
    hook = FigureHook()
    during = []
    hook.on("plotly_click", lambda primitive, payload: during.append(hook.dispatching))
    assert hook.dispatching is False
    hook._dispatch("plotly_click", {})
    assert during == [True]
    assert hook.dispatching is False

    def boom(primitive, payload):
        raise RuntimeError("boom")

    hook.on("plotly_click", boom)
    with pytest.raises(RuntimeError):
        hook._dispatch("plotly_click", {})
    assert hook.dispatching is False


def test_clearing_another_owner_mid_dispatch_does_not_skip_a_live_handler():
    """`clear` keeps the SAME entry objects for the handlers it leaves: the
    removed-since-snapshot check is by identity, so a rebuilt entry would
    look removed and a live handler would be skipped."""
    hook = FigureHook()
    seen = []

    def x(primitive, payload):
        seen.append("x")
        hook.clear("z")

    def y(primitive, payload):
        seen.append("y")

    hook.owned("x").on("plotly_click", x)
    hook.owned("y").on("plotly_click", y)
    hook._dispatch("plotly_click", {})
    assert seen == ["x", "y"]


def _reregistering_hook(handler):
    """A hook where owner "x" clears owner "cap" and re-registers ``handler``
    under it on the FIRST dispatch only; "cap" starts with ``handler``."""
    hook = FigureHook()
    done = []

    def x(primitive, payload):
        if not done:
            done.append(True)
            hook.clear("cap")
            hook.owned("cap").on("plotly_click", handler)

    hook.owned("x").on("plotly_click", x)
    hook.owned("cap").on("plotly_click", handler)
    return hook


def test_a_handler_cleared_and_re_registered_mid_dispatch_is_skipped_by_identity():
    """The same function object re-registered makes an EQUAL entry, not the
    same one: it is not called in that dispatch, and called once in the
    next."""
    calls = []

    def cap(primitive, payload):
        calls.append(primitive)

    hook = _reregistering_hook(cap)
    hook._dispatch("plotly_click", {})
    assert calls == []
    hook._dispatch("plotly_click", {})
    assert len(calls) == 1


def test_a_bound_method_cleared_and_re_registered_mid_dispatch_is_skipped():
    """A bound method compares equal but is a new object on each access."""

    class Panel:
        def __init__(self):
            self.calls = 0

        def handle(self, primitive, payload):
            self.calls += 1

    panel = Panel()
    assert panel.handle == panel.handle and panel.handle is not panel.handle
    hook = _reregistering_hook(panel.handle)
    hook._dispatch("plotly_click", {})
    assert panel.calls == 0
    hook._dispatch("plotly_click", {})
    assert panel.calls == 1


def test_a_raising_handler_ends_the_relayout():
    """Later handlers and later shapes are not dispatched, the exception
    propagates, and the shape-moved callback still runs once."""
    moved = []
    hook = FigureHook(on_shape_moved=lambda: moved.append(True))
    hook.attach(_StandInPlot(_SHAPES))
    seen = []

    def first(primitive, payload):
        seen.append(("first", primitive))
        raise RuntimeError("boom")

    def second(primitive, payload):
        seen.append(("second", primitive))

    hook.on("plotly_relayout", first)
    hook.on("plotly_relayout", second)
    with pytest.raises(RuntimeError, match="boom"):
        hook._dispatch("plotly_relayout", {"shapes[0].y0": 1, "shapes[1].y0": 2})
    assert seen == [("first", "/metrics/children/1/range/max")]
    assert moved == [True]
    assert hook.dispatching is False


def test_a_nested_dispatch_restores_the_outer_dispatching_flag():
    hook = FigureHook()
    during = []

    def inner(primitive, payload):
        during.append(("inner", hook.dispatching))

    def outer(primitive, payload):
        hook._dispatch("plotly_click", {})
        during.append(("outer after inner", hook.dispatching))

    hook.on("plotly_click", inner)
    hook.on("plotly_relayout", outer)
    hook._dispatch("plotly_relayout", {"xaxis.range[0]": 0.5})
    assert during == [("inner", True), ("outer after inner", True)]
    assert hook.dispatching is False


def test_a_shape_key_with_a_trailing_newline_or_a_non_ascii_digit_is_not_a_shape():
    """`$` matches before a trailing newline and `\\d` matches any Unicode
    digit (which `int()` accepts): both would make these shape relayouts."""
    for payload in ({"shapes[0].y0\n": 1.0}, {"shapes[\u0661].y0": 1.0}):
        hook, seen, moved = _recording_hook(_StandInPlot(_SHAPES))
        hook._dispatch("plotly_relayout", payload)
        assert [(p, d) for _, p, d in seen] == [("", payload)]
        assert moved == []


def test_a_relayout_can_pass_through_the_page_on_its_way():
    """`attach(plot, relayout_js=...)`: the relayout's payload is sent as
    the page's expression over it gives it — the card holds a released grip
    to the visible range there. Every other event, and a relayout with no
    expression, is registered as it was."""
    plot = _StandInPlot()
    FigureHook().attach(plot, relayout_js="window.X ? X.released(7, e) : e")
    assert plot.in_page == {
        "plotly_click": {},
        "plotly_relayout": {
            "js_handler": "(e) => emit(window.X ? X.released(7, e) : e)"
        },
        "plotly_selected": {},
    }
    plain = _StandInPlot()
    FigureHook().attach(plain)
    assert len(plain.in_page) == 3 and all(kw == {} for kw in plain.in_page.values())
