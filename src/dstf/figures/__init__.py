"""One figure layer, two consumers.

A :class:`~.view.VariableView` is everything a plot of one variable needs —
trajectories, overlays, the leaves that target it with their geometry and
summaries, the error series — assembled the same way by the HTML report
and by the authoring tool. :func:`~.plotly.variable_figure` renders it to a
plain Plotly figure dict (``data`` / ``layout`` / ``config``), with no
``plotly.py`` dependency; the report embeds the dict, the tool hands it to
its plotly element. :func:`~.plotly.plot_figure` renders a plot of SEVERAL
views the same way — array families in one hue ramp (:mod:`.families`) —
and is ``variable_figure`` itself when a plot holds one. Nothing here knows
a mode name: drawing is keyed on primitive type
(:mod:`dstf.comparison.geometry`).
"""
