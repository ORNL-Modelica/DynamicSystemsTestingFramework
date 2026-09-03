// ---- interactive.js: part 5 of 5 (export.js) ----
// interactive.js is authored as 5 fragments and concatenated, in the
// order named by ``INTERACTIVE_JS_PARTS`` in reporting/report_suite.py, into
// the single file the report page loads. The fragments are contiguous slices
// of one script: never reorder them and never move a declaration across a
// seam — top-level ``const`` TDZ, function hoisting and the load-time IIFEs
// all depend on this exact sequence.
// ---- Summary ----
// When ``passMap`` is supplied (post-edit refresh), use the live-recomputed
// pass states; otherwise fall back to the CLI-computed leaf.passed for
// initial render.

// ---- Overlay picker ----
// Each .overlay-picker div declares which Plotly chart it drives via its
// data-plot-id attribute, so the single handler works for both the
// comparison plots (plot-N) and the no-baseline plots (nb-plot-N).
function wireOverlayPickers() {
  document.querySelectorAll('.overlay-picker .overlay-toggle').forEach(cb => {
    cb.addEventListener('change', (e) => {
      const picker = e.target.closest('.overlay-picker');
      if (!picker) return;
      const plotId = picker.dataset.plotId;
      if (!plotId) return;
      const name = e.target.dataset.ovName;
      const role = e.target.dataset.ovRole;
      setOverlayVisible(plotId, role, name, e.target.checked);
    });
  });
}

function setOverlayVisible(plotId, role, name, visible) {
  const el = document.getElementById(plotId);
  if (!el || !el.data) return;
  const traceName = overlayTraceName(role, name);
  for (let i = 0; i < el.data.length; i++) {
    if (el.data[i].name === traceName) {
      Plotly.restyle(el, { visible: visible ? true : 'legendonly' }, [i]);
      return;
    }
  }
}

// ---- Error overlays (signed / abs / NRMSE, right y-axis) ------------
// Per-variable dropdown adds or removes a single error trace on the
// right axis. State is path-tracked by vidx so switching between values
// replaces cleanly (no trace accumulation).
const ERROR_OVERLAY_TRACE_NAME = 'Error overlay';

function wireErrorOverlays() {
  document.querySelectorAll('.error-overlay-select').forEach(sel => {
    sel.addEventListener('change', (e) => {
      const vidx = parseInt(e.target.dataset.vidx);
      if (isNaN(vidx)) return;
      setErrorOverlay(vidx, e.target.value);
    });
  });
}

function setErrorOverlay(vidx, mode) {
  const el = document.getElementById(`plot-${vidx}`);
  if (!el || !el.data) return;
  // Remove any existing error-overlay trace + right-axis.
  for (let i = el.data.length - 1; i >= 0; i--) {
    if (el.data[i].name === ERROR_OVERLAY_TRACE_NAME) {
      Plotly.deleteTraces(el, i);
    }
  }
  if (mode === 'none') {
    Plotly.relayout(el, { yaxis2: null });
    return;
  }
  const varname = VARIABLE_ORDER[vidx];
  const traj = (VARIABLES_BY_NAME[varname] || {}).trajectory || {};
  if (!traj.ref_time || !traj.ref_time.length) return;

  // Interp act onto ref grid, compute chosen error.
  const refT = traj.ref_time;
  const refV = traj.ref_values || [];
  const actOnRef = refT.map(t => _interpLinear(traj.act_time || [], traj.act_values || [], t));
  const signed = refT.map((_, i) => actOnRef[i] - refV[i]);
  const absErr = signed.map(Math.abs);

  let yData, label;
  if (mode === 'signed') { yData = signed; label = 'Signed error'; }
  else if (mode === 'abs') { yData = absErr; label = 'Abs error'; }
  else if (mode === 'nrmse') {
    const rmin = Math.min(...refV);
    const rmax = Math.max(...refV);
    const range = rmax - rmin;
    yData = range > 1e-15 ? absErr.map(e => e / range) : absErr.slice();
    label = 'NRMSE (per-point)';
  } else return;

  Plotly.addTraces(el, {
    x: refT, y: yData,
    name: ERROR_OVERLAY_TRACE_NAME, type: 'scatter', mode: 'lines',
    line: { color: '#f44336', width: 1 }, opacity: 0.6,
    yaxis: 'y2', hovertemplate: `${label}: %{y:.4g}<extra></extra>`,
  });
  Plotly.relayout(el, {
    yaxis2: {
      title: { text: label, font: { color: '#f44336', size: 10 } },
      tickformat: '.4g', nticks: 4, automargin: true,
      overlaying: 'y', side: 'right', showgrid: false,
      tickfont: { color: '#f44336', size: 10 },
    },
  });
}

// ---- Diagnostic / no-baseline plots (unchanged from previous template) ----
function renderDiagPlots() {
  DIAG_TRAJECTORIES.forEach((traj, idx) => {
    const el = document.getElementById(`diag-plot-${idx}`);
    if (!el) return;
    const traces = [{
      x: traj.act_time, y: traj.act_values, name: 'Actual',
      type: 'scatter', mode: 'lines', line: { color: '#2196F3', width: 1 },
    }];
    if (traj.ref_values && traj.ref_values.length) {
      traces.push({
        x: traj.ref_time, y: traj.ref_values, name: 'Reference',
        type: 'scatter', mode: 'lines',
        line: { color: '#FF9800', width: 1, dash: 'dash' },
      });
    }
    Plotly.newPlot(el, traces, {
      xaxis: { title: 'Time' },
      yaxis: { title: 'Value' },
      margin: { t: 10, b: 35, l: 60, r: 20 },
    }, PLOT_CFG);
  });
}

function renderNBPlots() {
  NB_TRAJECTORIES.forEach((traj, idx) => {
    const el = document.getElementById(`nb-plot-${idx}`);
    if (!el) return;
    const traces = [{
      x: traj.time, y: traj.values, name: 'Actual',
      type: 'scatter', mode: 'lines', line: { color: '#2196F3', width: 1 },
    }];
    // Overlay traces — mirror the main-path styling so NO_REF plots can
    // still surface sibling-backend references for pre-accept cross-check.
    //   soft_check              → purple dotted
    //   companion (generic)     → green dashdot
    //   companion sibling-backend → blue dashed
    for (const ov of (traj.overlays || [])) {
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
    Plotly.newPlot(el, traces, {
      xaxis: { title: 'Time' },
      yaxis: { title: 'Value' },
      margin: { t: 25, b: 35, l: 60, r: 20 },
      legend: { x: 0, y: 1, bgcolor: 'rgba(255,255,255,0.8)' },
    }, PLOT_CFG);
  });
}

// ---- Export / Save (path-keyed patch emission) ----
function buildPatchData() {
  const ops = [];

  // Stage 4 — structural edits (+ / − on nodes) emit a single wholesale
  // replace at /metrics. The whitelist covers /metrics; unknown
  // sibling keys on the test entry (description / metadata) survive
  // intact. Structural changes skip per-leaf scalar diffing: the full
  // tree is the new source of truth.
  if (structureDirty && WORKING_TREE) {
    const newSpec = nodeToSpec(WORKING_TREE);
    // Use 'add' — patch_apply's add upserts on existing keys and creates
    // when absent, so it works whether the test previously had /metrics
    // or was flat-override.
    ops.push({ op: 'add', path: '/metrics', value: newSpec });
    return { model: MODEL_ID, patch: ops };
  }

  walkLeaves(TREE_VIEW, (leaf) => {
    const state = leafState[leaf.path];
    if (!state) return;

    Object.keys(state.params).forEach(field => {
      // _paramEdited carries the float + deep-copy comparison rules
      // (review 2026-07-06 finding 38) — ops only for real value changes.
      if (!_paramEdited(state, field)) return;
      ops.push({
        op: 'add',
        path: `${leaf.path}/${jsonPointerEscape(field)}`,
        value: state.params[field],
      });
    });

    const origW = state.original_window || {};
    const curW = state.window;
    const origHas = origW.start != null || origW.end != null;
    const curHas = curW.start != null || curW.end != null;
    if (!curHas && origHas) {
      ops.push({ op: 'remove', path: `${leaf.path}/window` });
    } else if (curHas) {
      const startDiff =
        (curW.start == null) !== (origW.start == null) ||
        (curW.start != null && origW.start != null && floatsDiffer(curW.start, origW.start));
      const endDiff =
        (curW.end == null) !== (origW.end == null) ||
        (curW.end != null && origW.end != null && floatsDiffer(curW.end, origW.end));
      if (startDiff || endDiff || !origHas) {
        const body = {};
        if (curW.start != null) body.start = Number(curW.start);
        if (curW.end != null) body.end = Number(curW.end);
        ops.push({ op: 'add', path: `${leaf.path}/window`, value: body });
      }
    }
  });
  return { model: MODEL_ID, patch: ops };
}

function updateExport() {
  const el = document.getElementById('export-json');
  if (el) el.value = JSON.stringify(buildPatchData(), null, 2);
}

function copyExport() {
  const el = document.getElementById('export-json');
  if (!el) return;
  // review 2026-07-06 finding 76g: navigator.clipboard is unavailable on
  // file:// (non-secure context) — fall back to the textarea+execCommand
  // pattern (same as dashboard.js) and surface failure instead of
  // silently doing nothing.
  const text = el.value;
  const fallbackCopy = () => {
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (_e) { ok = false; }
    document.body.removeChild(ta);
    showSaveStatus(ok ? 'Copied!' : 'Copy failed — select the JSON and copy manually');
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(() => showSaveStatus('Copied!'), fallbackCopy);
  } else {
    fallbackCopy();
  }
}

function downloadExport() {
  const data = JSON.stringify(buildPatchData(), null, 2);
  const blob = new Blob([data], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'spec_patch.json';
  a.click();
  URL.revokeObjectURL(url);
  showSaveStatus('Downloaded!');
}

function showSaveStatus(msg) {
  const el = document.getElementById('save-status');
  if (el) {
    el.textContent = msg;
    el.style.display = 'inline';
    setTimeout(() => el.style.display = 'none', 2000);
  }
}
