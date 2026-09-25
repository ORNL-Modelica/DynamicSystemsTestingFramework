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
// it reads `visible` flags and writes `visible` flags — on a restyle
// (the select, a legend click) and after a react.
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
      if (Boolean(layout[key].visible) !== want) update[key + '.visible'] = want;
    });
    if (Object.keys(update).length) Plotly.relayout(gd, update);
  }

  function isWired(gd) {
    // Plotly.newPlot on an already-plotted node purges it, which removes
    // every listener (plotly.min.js 2.35.0: Plots.purge ends in
    // removeAllListeners) while the node object — and any flag on it —
    // survives; so ask the emitter whether OUR handler is registered. Not
    // "any listener": NiceGUI's plotly element registers its own
    // plotly_restyle forwarder after every newPlot, before this script's
    // observer runs (the authoring tool's page, when it loaded this
    // script).
    var follow = gd._dstfFollow;
    return !!(follow && gd._ev && typeof gd._ev.listeners === 'function' &&
              gd._ev.listeners('plotly_restyle').indexOf(follow) !== -1);
  }

  function dstfWireSecondaryAxes(gd) {
    if (!gd || typeof gd.on !== 'function' || isWired(gd)) return;
    var follow = function () { dstfFollowSecondaryAxes(gd); };
    gd._dstfFollow = follow;
    gd.on('plotly_restyle', follow);
    // Plotly.react (NiceGUI's update_figure, which the authoring tool
    // called when it loaded this script) replaces the layout with the
    // server's — every secondary axis hidden — while uirevision keeps a
    // legend-shown trace visible, and fires no restyle. The report never
    // reacts.
    gd.on('plotly_react', follow);
    dstfFollowSecondaryAxes(gd);
  }

  // A plot created after the page is built, or re-plotted on the same
  // node (NiceGUI's element calls newPlot when the figure's config
  // changes, react otherwise — the authoring tool's page, when it loaded
  // this script), is wired when Plotly adds its class; a node that is not
  // yet a plot has no `on` and is skipped until then. report.js wires its
  // own plots as it draws them.
  var observer = new MutationObserver(function () {
    document.querySelectorAll('.js-plotly-plot').forEach(dstfWireSecondaryAxes);
  });
  observer.observe(document.documentElement, {
    childList: true, subtree: true, attributes: true, attributeFilter: ['class'],
  });

  window.dstfFollowSecondaryAxes = dstfFollowSecondaryAxes;
  window.dstfWireSecondaryAxes = dstfWireSecondaryAxes;
})();
