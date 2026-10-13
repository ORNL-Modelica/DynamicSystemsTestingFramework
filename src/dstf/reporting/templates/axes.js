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
// an axis whose shown traces change — on a restyle (the select, a legend
// click).
(function () {
  // The indices of the visible traces on the axis, as one string: the
  // report never adds, removes or reorders a trace, so an index names one.
  function visibleOn(gd, axisId) {
    var ids = [];
    (gd.data || []).forEach(function (t, i) {
      if ((t.yaxis || 'y') !== axisId) return;
      if (t.visible === 'legendonly' || t.visible === false) return;
      ids.push(i);
    });
    return ids.join(',');
  }

  function dstfFollowSecondaryAxes(gd) {
    var layout = gd.layout || {};
    var seen = gd._dstfShownOn || (gd._dstfShownOn = {});
    var update = {};
    Object.keys(layout).forEach(function (key) {
      var m = /^yaxis(\d+)$/.exec(key);
      if (!m || !layout[key] || !layout[key].overlaying) return;
      var ids = visibleOn(gd, 'y' + m[1]);
      var was = seen[key];
      seen[key] = ids;
      var want = ids !== '';
      if (Boolean(layout[key].visible) !== want) update[key + '.visible'] = want;
      // A box zoom scales the axis too, shown or hidden (it overlays `y`):
      // kept when another series took the axis, it cut that series off or
      // drew it as a flat line. A new set of series scales it to their
      // data; a restyle that leaves the set alone (an overlay on `y`)
      // keeps a zoom.
      if (want && ids !== was) update[key + '.autorange'] = true;
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
