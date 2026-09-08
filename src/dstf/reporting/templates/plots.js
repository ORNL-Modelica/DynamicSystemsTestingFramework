// ---- interactive.js: part 3 of 5 (plots.js) ----
// interactive.js is authored as 5 fragments and concatenated, in the
// order named by ``INTERACTIVE_JS_PARTS`` in reporting/report_suite.py, into
// the single file the report page loads. The fragments are contiguous slices
// of one script: never reorder them and never move a declaration across a
// seam — top-level ``const`` TDZ, function hoisting and the load-time IIFEs
// all depend on this exact sequence.
// ---- Init ----
document.addEventListener('DOMContentLoaded', () => {
  renderAllPlots();
  renderAllNodeTrees();
  wireOverlayPickers();
  wireErrorOverlays();
  // review 2026-07-06 finding 37: the CLI verdict is authoritative for the
  // INITIAL render. Pills / summary are stamped from the CLI-evaluated
  // tree, NOT recomputed from the LTTB-decimated embedded arrays —
  // borderline tests used to flip verdicts in the browser before the user
  // touched anything. Live re-scoring (refreshPassStates) kicks in only
  // once the user edits (param change, window change, structural edit).
  applyCliPassStates();
  updateExport();
  renderDecimationBanner();
  // ESC: cancel an armed window brush first (review 2026-07-06 finding
  // 76c), else deactivate the active leaf (escape hatch from edit mode).
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (_activeBrush) { _activeBrush.cleanup(); return; }
    if (activeLeafPath) deactivateLeaf();
  });
});

// Stamp pills + summary from the CLI-computed ``passed`` flags without
// invoking MODE_SCORERS (review 2026-07-06 finding 37).
function applyCliPassStates() {
  const tree = currentTree();
  const passMap = {};
  (function walk(n) {
    if (!n) return;
    if (n.passed !== undefined && n.passed !== null) passMap[n.path] = !!n.passed;
    (n.children || []).forEach(walk);
  })(tree);
  updatePassPills(passMap);
  updateSummaryFromMap(passMap, tree);
}

// review 2026-07-06 finding 37: persistent, dismissible disclosure that the
// browser's live scorers run on LTTB-decimated arrays.
// report_suite._decimate_context_for_html stamps per-variable
// ``full_samples`` (pre-decimation counts) into the embedded trajectory
// dicts; when any variable was decimated, state N of M.
function renderDecimationBanner() {
  let embedded = 0;
  let full = 0;
  let anyDecimated = false;
  // Named soft_check baselines that were themselves decimated. An
  // against-leaf live-scores on the overlay curve (D105 narrowing), so an
  // overlay over the cap is exactly the disclosure the primary's arrays
  // already got — the trajectory-level ``full_samples`` stamp only counts
  // act/ref and would leave that silent.
  const decimatedBaselines = new Set();
  for (const name of VARIABLE_ORDER) {
    const vardata = VARIABLES_BY_NAME[name] || {};
    const traj = vardata.trajectory || {};
    const fs = traj.full_samples;
    if (fs) {
      const e = (traj.act_time || []).length + (traj.ref_time || []).length;
      const f = (fs.act || 0) + (fs.ref || 0);
      embedded += e;
      full += f;
      if (f > e) anyDecimated = true;
    }
    for (const ov of (vardata.overlays || [])) {
      if (ov.full_samples == null) continue;
      const e = (ov.time || []).length;
      embedded += e;
      full += ov.full_samples;
      if (ov.full_samples > e) {
        anyDecimated = true;
        if (ov.role === 'soft_check' && ov.name) decimatedBaselines.add(ov.name);
      }
    }
  }
  if (!anyDecimated || !document.body) return;
  const banner = document.createElement('div');
  banner.id = 'decimation-banner';
  banner.style.cssText =
    'position:sticky;top:0;z-index:1000;background:#fff3cd;'
    + 'border:1px solid #ffe08a;border-radius:4px;color:#664d03;'
    + 'padding:8px 12px;margin:8px 0;display:flex;align-items:center;'
    + 'gap:8px;font-size:0.9em;';
  const msg = document.createElement('span');
  const baselineNote = decimatedBaselines.size
    ? ' Decimated baselines scored against: '
      + Array.from(decimatedBaselines).sort().join(', ') + '.'
    : '';
  msg.textContent =
    `Live scores are computed on decimated data (${embedded} of ${full} `
    + 'samples) — CLI verdicts are authoritative until re-run.'
    + baselineNote;
  banner.appendChild(msg);
  const close = document.createElement('button');
  close.textContent = '✕';
  close.title = 'Dismiss';
  close.style.cssText =
    'margin-left:auto;background:none;border:none;cursor:pointer;'
    + 'color:inherit;font-size:1em;';
  close.addEventListener('click', () => banner.remove());
  banner.appendChild(close);
  document.body.insertBefore(banner, document.body.firstChild);
}

// ---- Plotly rendering ----
// Always enable shape-position drag — the range editor hooks
// plotly_relayout with a shape-name filter so it only fires on shapes
// it cares about; non-range plots have no draggable shapes anyway.
// Keeping config static across renders means we can use Plotly.react
// (which preserves zoom / pan) instead of newPlot (which resets them).
const PLOT_CFG = {
  responsive: true,
  displaylogo: false,
  scrollZoom: true,
  edits: { shapePosition: true },
};

function renderAllPlots() {
  VARIABLE_ORDER.forEach((varname, idx) => renderVariablePlot(varname, idx));
  renderDiagPlots();
  renderNBPlots();
}

function renderVariablePlot(varname, idx) {
  const vardata = VARIABLES_BY_NAME[varname];
  if (!vardata) return;
  const traj = vardata.trajectory || {};
  const el = document.getElementById(`plot-${idx}`);
  if (!el) return;

  // First paint: the Python figure layer's dict, verbatim. Every later
  // call comes from an edit and re-renders through the JS contributions
  // below (Plotly.react), which is unchanged behaviour until the two-tier
  // split deletes that path.
  const fig = FIGURES[varname];
  if (fig && !el._mt_plotted) {
    Plotly.newPlot(el, fig.data, fig.layout, PLOT_CFG);
    el._mt_plotted = true;
    el._mt_python = true;
    return;
  }
  el._mt_python = false;

  const actTime = traj.act_time || [];
  const refTime = traj.ref_time || [];
  const actValues = traj.act_values || [];
  const refValues = traj.ref_values || [];
  const hasRef = refTime.length > 0;

  const traces = [{
    x: actTime, y: actValues, name: 'Actual',
    type: 'scatter', mode: 'lines',
    line: { color: '#2196F3', width: 1.5 },
  }];
  if (hasRef) {
    traces.push({
      x: refTime, y: refValues, name: 'Reference',
      type: 'scatter', mode: 'lines',
      line: { color: '#FF9800', width: 1.5, dash: 'dash' },
    });
  }

  // Overlay traces (companions / soft_checks) — default hidden; toggle
  // visibility via the per-plot overlay picker. Styles by role + kind:
  //   soft_check             → purple dotted
  //   companion (generic)    → green dashdot
  //   companion sibling-backend → blue dashed (pre-accept cross-check)
  for (const ov of (vardata.overlays || [])) {
    let color, dash;
    if (ov.role === 'soft_check') { color = '#7B1FA2'; dash = 'dot'; }
    else if (ov.kind === 'sibling-backend') { color = '#1976D2'; dash = 'dash'; }
    else { color = '#388E3C'; dash = 'dashdot'; }
    traces.push({
      x: ov.time, y: ov.values,
      name: overlayTraceName(ov.role, ov.name),
      type: 'scatter', mode: 'lines',
      line: { color, width: 1.2, dash },
      opacity: 0.85,
      visible: 'legendonly',
    });
  }

  // Per-leaf plot contributions — each leaf's visual scored artifact
  // (tube polygon, range lines, window band, final-time marker).
  const shapes = [];
  for (const leaf of leavesForVariable(varname)) {
    const state = leafState[leaf.path];
    if (!state || !state.visible) continue;
    const fn = MODE_PLOT_CONTRIBUTIONS[leaf.metric] || (() => ({ traces: [], shapes: [] }));
    const contrib = fn(leaf, traj);
    for (const t of (contrib.traces || [])) traces.push(t);
    for (const s of (contrib.shapes || [])) shapes.push(s);
    const w = windowShape(leaf);
    if (w) shapes.push(w);
  }

  const layout = {
    xaxis: { title: 'Time', hoverformat: '.6~g', uirevision: 'keep' },
    yaxis: {
      title: { text: 'Value', standoff: 10 }, tickformat: '.4g',
      automargin: true, nticks: 6, uirevision: 'keep',
    },
    margin: { t: 25, b: 45, l: 60, r: 20 },
    legend: { x: 0, y: 1, bgcolor: 'rgba(255,255,255,0.8)' },
    hovermode: 'x unified',
    shapes,
    // Stable uirevision across re-renders preserves user zoom / pan —
    // without it, Plotly.react reapplies default autorange every time
    // traces change (e.g., on a tube-drag commit). Per-axis uirevision
    // is what actually preserves the zoom state; the top-level one
    // covers other user bits like legend clicks.
    uirevision: 'keep',
  };
  // First paint uses newPlot (must establish the plot); subsequent
  // re-renders use react so user-driven zoom/pan survive tube-edit
  // commits. We track "already plotted" via the element's ._plotted
  // marker — Plotly doesn't expose this natively.
  if (el._mt_plotted) {
    Plotly.react(el, traces, layout, PLOT_CFG);
  } else {
    Plotly.newPlot(el, traces, layout, PLOT_CFG);
    el._mt_plotted = true;
  }

  // Plotly.newPlot wipes every trace — re-apply the error overlay if
  // the dropdown has one selected. Source of truth is the DOM select;
  // no extra persistent state in JS.
  const errSel = document.querySelector(`.error-overlay-select[data-vidx="${idx}"]`);
  if (errSel && errSel.value && errSel.value !== 'none') {
    setErrorOverlay(idx, errSel.value);
  }
}

