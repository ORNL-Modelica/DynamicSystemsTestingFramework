// ---- axes.js ----
// A secondary y-axis (one with `overlaying`) is visible exactly while a
// trace on it is visible. The figure layer lays every such axis out
// hidden (dstf.figures.plotly: a laid-out axis with only legend-only
// traces draws a stray zero line); this script shows and hides it as the
// traces toggle — by the report's select (Plotly.restyle) or by a legend
// click, which is a client-side restyle the server never hears.
//
// The report's script: it loads as the first part of interactive.js. The
// authoring tool does not load it — its error traces are out of the
// legend, and the figure it sends names the one error axis shown
// (dstf.figures.plotly.Visibility), so nothing in its page toggles a
// trace behind the server's back. It computes nothing about the data —
// it reads `visible` flags and writes `visible` flags, and `autorange` on
// an axis it shows — on a restyle (the select, a legend click).
(function () {
  function anyVisibleOn(gd, axisId) {
    return (gd.data || []).some(function (t) {
      return (t.yaxis || 'y') === axisId && t.visible !== 'legendonly' && t.visible !== false;
    });
  }

  function dstfFollowSecondaryAxes(gd) {
    var layout = gd.layout || {};
    var update = {};
    Object.keys(layout).forEach(function (key) {
      var m = /^yaxis(\d+)$/.exec(key);
      if (!m || !layout[key] || !layout[key].overlaying) return;
      var want = anyVisibleOn(gd, 'y' + m[1]);
      if (Boolean(layout[key].visible) === want) return;
      update[key + '.visible'] = want;
      // A box zoom made while the axis hides scales it too (it overlays
      // `y`): shown on that range, an error drew as a flat line. It
      // scales to its data when it shows.
      if (want) update[key + '.autorange'] = true;
    });
    if (Object.keys(update).length) Plotly.relayout(gd, update);
  }

  // report.js calls this once per plot, right after its one newPlot of
  // that node; the report never reacts or re-plots.
  function dstfWireSecondaryAxes(gd) {
    if (!gd || typeof gd.on !== 'function') return;
    gd.on('plotly_restyle', function () { dstfFollowSecondaryAxes(gd); });
    dstfFollowSecondaryAxes(gd);
  }

  window.dstfWireSecondaryAxes = dstfWireSecondaryAxes;
})();
