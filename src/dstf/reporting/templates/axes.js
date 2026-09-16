// ---- axes.js ----
// A secondary y-axis (one with `overlaying`) is visible exactly while a
// trace on it is visible. The figure layer lays every such axis out
// hidden (dstf.figures.plotly: a laid-out axis with only legend-only
// traces draws a stray zero line); this script shows and hides it as the
// traces toggle — by the report's select (Plotly.restyle) or by a legend
// click, which is a client-side restyle the server never hears.
//
// One script for both tiers: the report loads it as the first part of
// interactive.js; the tool adds it to its page body. It computes nothing
// about the data — it reads `visible` flags and writes `visible` flags
// — on a restyle (the select, a legend click) and after a react.
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
    // "any listener": the tool's plotly element (NiceGUI) registers its
    // own plotly_restyle forwarder after every newPlot, before this
    // script's observer runs.
    var follow = gd._dstfFollow;
    return !!(follow && gd._ev && typeof gd._ev.listeners === 'function' &&
              gd._ev.listeners('plotly_restyle').indexOf(follow) !== -1);
  }

  function dstfWireSecondaryAxes(gd) {
    if (!gd || typeof gd.on !== 'function' || isWired(gd)) return;
    var follow = function () { dstfFollowSecondaryAxes(gd); };
    gd._dstfFollow = follow;
    gd.on('plotly_restyle', follow);
    // Plotly.react (the tool's update_figure, a drag's snap-back) replaces
    // the layout with the server's — every secondary axis hidden — while
    // uirevision keeps a legend-shown trace visible, and fires no restyle.
    gd.on('plotly_react', follow);
    dstfFollowSecondaryAxes(gd);
  }

  // The tool creates its plot after the page is built and re-plots the
  // same node when the figure's config changes (NiceGUI's element calls
  // newPlot then, react otherwise); a node that is not yet a plot has no
  // `on` and is skipped until Plotly adds the class.
  var observer = new MutationObserver(function () {
    document.querySelectorAll('.js-plotly-plot').forEach(dstfWireSecondaryAxes);
  });
  observer.observe(document.documentElement, {
    childList: true, subtree: true, attributes: true, attributeFilter: ['class'],
  });

  window.dstfFollowSecondaryAxes = dstfFollowSecondaryAxes;
  window.dstfWireSecondaryAxes = dstfWireSecondaryAxes;
})();
