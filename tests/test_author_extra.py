"""§4.8: the figure layer emits dicts and NiceGUI renders them without plotly.py.
If a future NiceGUI grows a hard plotly.py dependency, or plotly.py sneaks into
the lock, this says so before a page does."""

import importlib.util

import pytest

nicegui = pytest.importorskip("nicegui")


def test_the_author_extra_does_not_install_plotly_py():
    assert importlib.util.find_spec("plotly") is None, (
        "plotly.py is installed; the figure layer must not start depending on it"
    )


async def test_a_figure_dict_renders_without_plotly_py():
    from nicegui import ui
    from nicegui.testing.user_simulation import user_simulation

    fig = {
        "data": [{"type": "scatter", "x": [0, 1], "y": [0, 1], "name": "actual"}],
        "layout": {"title": {"text": "x"}},
        "config": {},
    }

    def root():
        ui.label("plot below")
        ui.plotly(fig)

    async with user_simulation(root=root) as user:
        await user.open("/")
        await user.should_see("plot below")
        (plot,) = user.find(ui.plotly).elements
        assert plot.figure["data"][0]["name"] == "actual"
