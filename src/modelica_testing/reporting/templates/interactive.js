// ---- Embedded data ----
// interactive.html inlines the Jinja-rendered context into window.MT_REPORT
// (one <script> block), then loads this file via <script src>. Keeps the JS
// pure-static so it can be unit-tested under Playwright without re-rendering,
// and keeps the template responsible for data marshalling, not behavior.
const MT_REPORT = (typeof window !== 'undefined' && window.MT_REPORT) || {};
const MODEL_ID = MT_REPORT.MODEL_ID;
const TREE_VIEW = MT_REPORT.TREE_VIEW;
const VARIABLES_BY_NAME = MT_REPORT.VARIABLES_BY_NAME || {};
const MODE_SCHEMAS = MT_REPORT.MODE_SCHEMAS || {};
const DIAG_TRAJECTORIES = MT_REPORT.DIAG_TRAJECTORIES || [];
const NB_TRAJECTORIES = MT_REPORT.NB_TRAJECTORIES || [];
const SPEC_PATH = MT_REPORT.SPEC_PATH || "";

// Variable name → index (element IDs). Order matches template iteration.
const VARIABLE_ORDER = Object.keys(VARIABLES_BY_NAME || {});
const VARIABLE_INDEX = {};
VARIABLE_ORDER.forEach((name, i) => { VARIABLE_INDEX[name] = i; });

// ---- Runtime state — keyed by JSON-Pointer leaf path ----
// Single source of truth for edits: {params, window, visible, original_*}.
// "params" captures every per-mode config field (tolerance, tube_rel, ...);
// "window" is {start, end} (either may be absent). "visible" gates the leaf's
// plot contribution (tube polygon, range lines, window band, ...).
const leafState = {};

// Stage 4 — structural edit tracking. WORKING_TREE starts as a deep clone
// of the rendered TREE_VIEW and mutates in place when the user adds/removes
// nodes via +/−. On patch export, a dirty structure emits a single
// wholesale ``replace`` at ``/metrics``; granular scalar ops continue to
// flow for in-place leaf-config edits.
let WORKING_TREE = null;
let structureDirty = false;

// Tube-editor-era — the "active leaf" is whichever leaf the user most
// recently clicked in either tree mount. Its mode's MODE_PLOT_EDITORS
// entry (if any) gets wired to the variable's plot: Shift+click to add
// a control point, − to remove, etc. One leaf at a time; clicking a
// different leaf transfers editing to it.
let activeLeafPath = null;

function initWorkingTree() {
  WORKING_TREE = TREE_VIEW ? JSON.parse(JSON.stringify(TREE_VIEW)) : null;
  structureDirty = false;
}

(function initLeafState() {
  walkLeaves(TREE_VIEW, (leaf) => {
    leafState[leaf.path] = {
      params: Object.assign({}, leaf.params || {}, leaf.mode_values || {}),
      window: Object.assign({}, leaf.window || {}),
      visible: true,
      original_params: Object.assign({}, leaf.params || {}),
      original_window: Object.assign({}, leaf.window || {}),
    };
  });
  initWorkingTree();
})();

// ---- Helpers ----
function walkLeaves(node, fn) {
  if (!node) return;
  if (node.kind === 'leaf') { fn(node); return; }
  (node.children || []).forEach(c => walkLeaves(c, fn));
}

function leavesForVariable(varname) {
  const out = [];
  walkLeaves(TREE_VIEW, (leaf) => {
    if (leaf.variable === varname) out.push(leaf);
  });
  return out;
}

function jsonPointerEscape(s) {
  return String(s).replace(/~/g, '~0').replace(/\//g, '~1');
}

function floatsDiffer(a, b) {
  if (a === b) return false;
  if (a == null || b == null) return true;
  const scale = Math.max(Math.abs(a), Math.abs(b), 1e-12);
  return Math.abs(a - b) / scale > 1e-12;
}

function overlayTraceName(role, name) {
  return `Overlay: ${role}/${name}`;
}

// ---- Pass/fail scorers — live recompute in the browser ----
// Re-derives each leaf's pass/fail from its current leafState[path].params
// against the pre-computed error metrics stored on the leaf (nrmse,
// max_abs_error, trajectory shapes). The CLI remains authoritative for
// event-timing + dominant-frequency (FFT / event-detection not reimplemented
// JS-side); their pills stay on whatever the evaluator decided.
const MODE_SCORERS = {
  'nrmse': (leaf) => {
    const tol = _paramNumber(leaf, 'tolerance', leaf.tolerance_used);
    return leaf.nrmse < tol;
  },
  'final-only': (leaf) => {
    const tol = _paramNumber(leaf, 'tolerance', leaf.tolerance_used);
    return leaf.max_abs_error < tol;
  },
  'range': (leaf) => {
    const p = (leafState[leaf.path] || {}).params || {};
    const traj = (VARIABLES_BY_NAME[leaf.variable] || {}).trajectory || {};
    const values = traj.act_values || [];
    const mn = _nullOrNumber(p.min_value);
    const mx = _nullOrNumber(p.max_value);
    for (const v of values) {
      if (mn !== null && v < mn) return false;
      if (mx !== null && v > mx) return false;
    }
    return true;
  },
  'tube': (leaf) => {
    const traj = (VARIABLES_BY_NAME[leaf.variable] || {}).trajectory || {};
    if (!traj.ref_time || !traj.ref_time.length) return !!leaf.passed;
    const p = (leafState[leaf.path] || {}).params || {};
    const rel = Number(p.tube_rel || 0);
    const abs = Number(p.tube_abs || 0);
    const minW = Number(p.tube_min_width || 0);
    const mode = p.tube_width_mode;
    const points = Array.isArray(p.tube_points) ? p.tube_points : [];

    let widthsUpper, widthsLower;
    if (points.length > 0) {
      const normalized = points.map(pt => ({
        time: Number(pt.time ?? 0),
        upper: Number(pt.upper ?? pt.abs ?? pt.rel ?? 0),
        lower: Number(pt.lower ?? pt.abs ?? pt.rel ?? 0),
      })).sort((a, b) => a.time - b.time);
      const interp = (t, key) => {
        if (t <= normalized[0].time) return normalized[0][key];
        if (t >= normalized[normalized.length - 1].time) return normalized[normalized.length - 1][key];
        for (let i = 1; i < normalized.length; i++) {
          if (normalized[i].time >= t) {
            const f = (t - normalized[i - 1].time) / (normalized[i].time - normalized[i - 1].time);
            return normalized[i - 1][key] + f * (normalized[i][key] - normalized[i - 1][key]);
          }
        }
        return normalized[normalized.length - 1][key];
      };
      widthsUpper = traj.ref_time.map(t => Math.max(minW, interp(t, 'upper')));
      widthsLower = traj.ref_time.map(t => Math.max(minW, interp(t, 'lower')));
    } else {
      const w = traj.ref_values.map(v => {
        if (mode === 'rel') return Math.max(minW, rel * Math.abs(v));
        if (mode === 'band') return Math.max(minW, abs);
        return Math.max(minW, Math.max(abs, rel * Math.abs(v)));
      });
      widthsUpper = w;
      widthsLower = w;
    }

    const refTime = traj.ref_time;
    const refValues = traj.ref_values;
    for (let i = 0; i < refTime.length; i++) {
      const actV = _interpLinear(traj.act_time, traj.act_values, refTime[i]);
      if (actV > refValues[i] + widthsUpper[i]) return false;
      if (actV < refValues[i] - widthsLower[i]) return false;
    }
    return true;
  },
  // event-timing + dominant-frequency intentionally absent — CLI-authoritative.
};

function _paramNumber(leaf, field, fallback) {
  const p = (leafState[leaf.path] || {}).params || {};
  const v = p[field];
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

function _nullOrNumber(v) {
  if (v === null || v === undefined || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function _interpLinear(xs, ys, x) {
  if (!xs || !xs.length) return 0;
  if (x <= xs[0]) return ys[0];
  if (x >= xs[xs.length - 1]) return ys[ys.length - 1];
  for (let i = 1; i < xs.length; i++) {
    if (xs[i] >= x) {
      const f = (x - xs[i - 1]) / (xs[i] - xs[i - 1]);
      return ys[i - 1] + f * (ys[i] - ys[i - 1]);
    }
  }
  return ys[ys.length - 1];
}

// Walk the tree, recompute pass at every node, return {path: bool}.
// Combinator pass-semantics mirror the Python combinator classes
// (metric_tree.py); warn is always true (warn wrappers never fail their
// parent), weighted + k-of-n keep the CLI result since they need signal
// data we don't carry JS-side.
function recomputePassStates(root) {
  const out = {};
  function walk(node) {
    if (!node) return false;
    if (node.kind === 'leaf') {
      const scorer = MODE_SCORERS[node.metric];
      const p = scorer ? scorer(node) : !!node.passed;
      out[node.path] = p;
      return p;
    }
    const childResults = (node.children || []).map(walk);
    let passed;
    switch (node.combinator) {
      case 'and': passed = childResults.every(x => x); break;
      case 'or': passed = childResults.some(x => x); break;
      case 'warn': passed = true; break;
      case 'k-of-n':
        passed = childResults.filter(x => x).length >= (node.k || childResults.length);
        break;
      case 'weighted':
        passed = !!node.passed;  // needs score-weighted sum; defer to CLI
        break;
      default:
        passed = childResults.every(x => x);
    }
    out[node.path] = passed;
    return passed;
  }
  walk(root);
  return out;
}

function updatePassPills(passMap) {
  Object.entries(passMap).forEach(([path, passed]) => {
    document.querySelectorAll(
      `[data-path="${escapeSelector(path)}"] > .node-header > .node-status`,
    ).forEach(pill => {
      pill.className = `node-status ${passed ? 'pass' : 'fail'}`;
      pill.textContent = passed ? 'PASS' : 'FAIL';
    });
  });
}

// ---- Plot contribution registry ----
// Each entry: (leaf, trajectory) => {traces: [...], shapes: [...]}
// Called when rendering a variable's plot, once per leaf targeting that
// variable, if the leaf's visibility toggle is on.
const MODE_PLOT_CONTRIBUTIONS = {
  'nrmse': () => ({ traces: [], shapes: [] }),
  'final-only': (leaf, traj) => {
    const t = (traj.ref_time && traj.ref_time.length)
      ? traj.ref_time[traj.ref_time.length - 1]
      : ((traj.act_time && traj.act_time.length)
         ? traj.act_time[traj.act_time.length - 1] : null);
    if (t == null) return { traces: [], shapes: [] };
    return { traces: [], shapes: [{
      type: 'line', xref: 'x', yref: 'paper',
      x0: t, x1: t, y0: 0, y1: 1,
      line: { color: '#607D8B', width: 1, dash: 'dot' },
    }] };
  },
  'range': (leaf) => {
    const p = leafState[leaf.path] ? leafState[leaf.path].params : {};
    // Named shapes let MODE_PLOT_EDITORS['range'] match plotly_relayout
    // shape-drag events back to the right params field by path.
    const shapes = [];
    if (p.min_value !== null && p.min_value !== undefined && p.min_value !== '') {
      const mn = Number(p.min_value);
      if (Number.isFinite(mn)) shapes.push({
        type: 'line', xref: 'paper', x0: 0, x1: 1, yref: 'y',
        y0: mn, y1: mn,
        line: { color: '#f44336', width: 1.5, dash: 'dash' },
        name: `range_min:${leaf.path}`,
      });
    }
    if (p.max_value !== null && p.max_value !== undefined && p.max_value !== '') {
      const mx = Number(p.max_value);
      if (Number.isFinite(mx)) shapes.push({
        type: 'line', xref: 'paper', x0: 0, x1: 1, yref: 'y',
        y0: mx, y1: mx,
        line: { color: '#f44336', width: 1.5, dash: 'dash' },
        name: `range_max:${leaf.path}`,
      });
    }
    return { traces: [], shapes };
  },
  'tube': (leaf, traj) => {
    if (!traj.ref_time || !traj.ref_time.length) return { traces: [], shapes: [] };
    const p = leafState[leaf.path] ? leafState[leaf.path].params : {};
    const rel = Number(p.tube_rel || 0);
    const abs = Number(p.tube_abs || 0);
    const minW = Number(p.tube_min_width || 0);
    const mode = p.tube_width_mode;
    const points = Array.isArray(p.tube_points) ? p.tube_points : [];

    // Time-varying tube wins over the scalar widths when any control
    // points are authored (matches _interpolate_tube_widths in the
    // comparator; interp across the control grid, hold at the ends).
    let widths_upper, widths_lower;
    if (points.length > 0) {
      const normalized = points.map(pt => ({
        time: Number(pt.time ?? 0),
        upper: Number(pt.upper ?? pt.abs ?? pt.rel ?? 0),
        lower: Number(pt.lower ?? pt.abs ?? pt.rel ?? 0),
      })).sort((a, b) => a.time - b.time);
      const interp = (t, key) => {
        if (t <= normalized[0].time) return normalized[0][key];
        if (t >= normalized[normalized.length - 1].time) return normalized[normalized.length - 1][key];
        for (let i = 1; i < normalized.length; i++) {
          if (normalized[i].time >= t) {
            const f = (t - normalized[i - 1].time) / (normalized[i].time - normalized[i - 1].time);
            return normalized[i - 1][key] + f * (normalized[i][key] - normalized[i - 1][key]);
          }
        }
        return normalized[normalized.length - 1][key];
      };
      widths_upper = traj.ref_time.map(t => Math.max(minW, interp(t, 'upper')));
      widths_lower = traj.ref_time.map(t => Math.max(minW, interp(t, 'lower')));
    } else {
      const w = traj.ref_values.map(v => {
        if (mode === 'rel') return Math.max(minW, rel * Math.abs(v));
        if (mode === 'band') return Math.max(minW, abs);
        return Math.max(minW, Math.max(abs, rel * Math.abs(v)));
      });
      widths_upper = w; widths_lower = w;
    }

    const upper = traj.ref_values.map((v, i) => v + widths_upper[i]);
    const lower = traj.ref_values.map((v, i) => v - widths_lower[i]);
    const traces = [{
      x: traj.ref_time.concat([...traj.ref_time].reverse()),
      y: upper.concat([...lower].reverse()),
      fill: 'toself',
      fillcolor: 'rgba(76,175,80,0.15)',
      line: { color: 'transparent' },
      name: `Tube ${leaf.path}`,
      hoverinfo: 'skip',
      showlegend: true,
    }];

    // When this tube leaf is the active one, surface its control points
    // as draggable-looking markers. Adds visual feedback that the
    // interactive editor is wired to this plot.
    if (activeLeafPath === leaf.path && points.length > 0) {
      const pxt = points.map(pt => Number(pt.time ?? 0));
      const pxu = points.map(pt => interpOnRef(traj, Number(pt.time ?? 0)) + Number(pt.upper ?? 0));
      const pxl = points.map(pt => interpOnRef(traj, Number(pt.time ?? 0)) - Number(pt.lower ?? 0));
      traces.push({
        x: pxt, y: pxu, mode: 'markers', type: 'scatter',
        marker: { color: '#2e7d32', size: 9, symbol: 'triangle-up', line: {color: 'white', width: 1} },
        name: `Tube upper pts ${leaf.path}`, hoverinfo: 'x+y',
      });
      traces.push({
        x: pxt, y: pxl, mode: 'markers', type: 'scatter',
        marker: { color: '#c62828', size: 9, symbol: 'triangle-down', line: {color: 'white', width: 1} },
        name: `Tube lower pts ${leaf.path}`, hoverinfo: 'x+y',
      });
    }
    return { traces, shapes: [] };
  },
  'event-timing': () => ({ traces: [], shapes: [] }),
  'dominant-frequency': () => ({ traces: [], shapes: [] }),
};

// ---- Plot editor registry (MODE_PLOT_EDITORS) ----
// Parallel to MODE_PLOT_CONTRIBUTIONS. Each entry describes an
// interactive edit flow for a mode. Activated on leaf click, deactivated
// on click-away / ESC / switch-to-different-leaf.
//
// Contract:
//   activate(leaf, plotEl, commit) → void
//     - leaf:   the TREE_VIEW leaf object.
//     - plotEl: the Plotly DOM element for this leaf's variable.
//     - commit(): flush leafState to re-render plot + export. Call on any
//                 state mutation the editor makes.
//     - Implementation owns: DOM injection into the leaf's .node-editor
//       slot, plot event wiring (Plotly.on / addEventListener), and any
//       interim feedback.
//   deactivate(leaf, plotEl) → void
//     - Implementation owns: DOM removal + event unwiring.
//
// Modes without an entry have no interactive editor — clicking their
// leaf activates it (for visual consistency) but no plot events wire.
const MODE_PLOT_EDITORS = {};

// Tube editor — control-point table + Shift+click to add a point.
// Points live at ``leafState[path].params.tube_points`` as a list of
// ``{time, upper, lower}``. Polygon re-renders via plot_contribution.
MODE_PLOT_EDITORS['tube'] = (function() {
  const wired = new WeakMap();  // plotEl → cleanup fn

  function getPoints(leaf) {
    const p = leafState[leaf.path]?.params || {};
    const pts = Array.isArray(p.tube_points) ? p.tube_points : [];
    return pts.map(normalizePoint);
  }

  function normalizePoint(p) {
    // Accept legacy {time, abs, rel} as symmetric upper=lower=abs||rel.
    if (p.upper !== undefined || p.lower !== undefined) {
      return { time: Number(p.time || 0),
               upper: Number(p.upper ?? p.lower ?? 0),
               lower: Number(p.lower ?? p.upper ?? 0) };
    }
    const w = Number(p.abs ?? p.rel ?? 0);
    return { time: Number(p.time || 0), upper: w, lower: w };
  }

  function setPoints(leaf, pts) {
    const state = leafState[leaf.path];
    if (!state) return;
    state.params.tube_points = pts.map(p => ({
      time: Number(p.time), upper: Number(p.upper), lower: Number(p.lower),
    }));
  }

  function renderTable(leaf, container, commit) {
    container.innerHTML = '';
    const title = document.createElement('div');
    title.className = 'editor-title';
    title.textContent = 'Tube control points (time / upper / lower width)';
    container.appendChild(title);

    const hint = document.createElement('div');
    hint.className = 'editor-hint';
    hint.innerHTML = 'Shift+click the plot to add a point at that (time, value). Widths are offsets from the reference trajectory.';
    container.appendChild(hint);

    const table = document.createElement('table');
    table.className = 'tube-table';
    const header = document.createElement('tr');
    ['time', 'upper', 'lower', ''].forEach(h => {
      const th = document.createElement('th'); th.textContent = h;
      header.appendChild(th);
    });
    table.appendChild(header);

    const points = getPoints(leaf);
    points.forEach((pt, i) => {
      const row = document.createElement('tr');
      for (const key of ['time', 'upper', 'lower']) {
        const td = document.createElement('td');
        const inp = document.createElement('input');
        inp.type = 'number'; inp.step = 'any'; inp.value = pt[key];
        inp.addEventListener('input', () => {
          points[i][key] = parseFloat(inp.value);
          if (Number.isFinite(points[i][key])) {
            setPoints(leaf, points);
            commit();
          }
        });
        td.appendChild(inp);
        row.appendChild(td);
      }
      const rm = document.createElement('td');
      const btn = document.createElement('button');
      btn.className = 'node-btn node-btn-remove';
      btn.textContent = '−'; btn.title = 'Remove point';
      btn.addEventListener('click', () => {
        points.splice(i, 1);
        setPoints(leaf, points);
        commit();
        renderTable(leaf, container, commit);
      });
      rm.appendChild(btn);
      row.appendChild(rm);
      table.appendChild(row);
    });
    container.appendChild(table);

    const addBtn = document.createElement('button');
    addBtn.className = 'node-btn node-btn-add';
    addBtn.textContent = '+ add point';
    addBtn.addEventListener('click', () => {
      const traj = (VARIABLES_BY_NAME[leaf.variable] || {}).trajectory || {};
      const lastT = points.length ? points[points.length - 1].time
                 : (traj.ref_time?.[0] ?? 0);
      points.push({ time: lastT, upper: 0, lower: 0 });
      setPoints(leaf, points);
      commit();
      renderTable(leaf, container, commit);
    });
    container.appendChild(addBtn);
  }

  return {
    activate(leaf, plotEl, commit) {
      // Inject editor UI inside the active leaf's dedicated slot.
      document.querySelectorAll(`[data-path="${escapeSelector(leaf.path)}"] .node-editor`).forEach(slot => {
        renderTable(leaf, slot, commit);
      });

      // Shift+click on the plot adds a control point at (x, ref-offset).
      const handler = (e) => {
        if (!e || !e.event || !e.event.shiftKey) return;
        const pt = e.points?.[0];
        const x = pt ? pt.x : (e.xvals?.[0]);
        const y = pt ? pt.y : (e.yvals?.[0]);
        if (x == null || y == null) return;
        // Pick the reference value at x to compute width (y - ref).
        const traj = (VARIABLES_BY_NAME[leaf.variable] || {}).trajectory || {};
        const refY = interpOnRef(traj, x);
        const width = Math.abs(y - refY);
        const pts = getPoints(leaf);
        pts.push({ time: Number(x), upper: width, lower: width });
        pts.sort((a, b) => a.time - b.time);
        setPoints(leaf, pts);
        commit();
        // Re-render table to reflect the new point.
        document.querySelectorAll(`[data-path="${escapeSelector(leaf.path)}"] .node-editor`).forEach(slot => {
          renderTable(leaf, slot, commit);
        });
      };
      plotEl.on('plotly_click', handler);

      // Shift+right-click on the plot near an existing control point
      // removes it. Plotly swallows right-click by default, so we hit
      // DOM events directly, convert pixel→data via Plotly's p2d, and
      // match the nearest point within a threshold (10% of x-range).
      const removeHandler = (e) => {
        if (e.button !== 2 || !e.shiftKey) return;
        e.preventDefault();
        const fl = plotEl._fullLayout;
        if (!fl || !fl.xaxis) return;
        const rect = plotEl.getBoundingClientRect();
        // Plot area offset within the plot div.
        const mx = e.clientX - rect.left - (fl._size?.l || 0);
        if (mx < 0) return;
        const dataX = fl.xaxis.p2d(mx);
        const pts = getPoints(leaf);
        if (!pts.length) return;
        const xRange = fl.xaxis.range;
        const threshold = 0.1 * Math.abs(xRange[1] - xRange[0]);
        let bestIdx = -1, bestDist = Infinity;
        pts.forEach((pt, i) => {
          const d = Math.abs(pt.time - dataX);
          if (d < bestDist && d < threshold) { bestDist = d; bestIdx = i; }
        });
        if (bestIdx < 0) return;
        pts.splice(bestIdx, 1);
        setPoints(leaf, pts);
        commit();
        document.querySelectorAll(
          `[data-path="${escapeSelector(leaf.path)}"] .node-editor`,
        ).forEach(slot => renderTable(leaf, slot, commit));
      };
      // Suppress the browser context menu only when Shift is held —
      // plain right-click still opens it for unrelated use. Capture-
      // phase listener so we fire before Plotly's own drag/select
      // handlers stopPropagation (they register on inner svg layers).
      const ctxHandler = (e) => { if (e.shiftKey) e.preventDefault(); };
      plotEl.addEventListener('mousedown', removeHandler, true);
      plotEl.addEventListener('contextmenu', ctxHandler, true);

      wired.set(plotEl, () => {
        plotEl.removeAllListeners && plotEl.removeAllListeners('plotly_click');
        plotEl.removeEventListener('mousedown', removeHandler, true);
        plotEl.removeEventListener('contextmenu', ctxHandler, true);
      });
    },
    deactivate(leaf, plotEl) {
      document.querySelectorAll(`[data-path="${escapeSelector(leaf.path)}"] .node-editor`).forEach(slot => {
        slot.innerHTML = '';
      });
      const cleanup = wired.get(plotEl);
      if (cleanup) { cleanup(); wired.delete(plotEl); }
    },
  };
})();

// Range editor — drag the min/max dashed lines directly on the plot.
// Leans on Plotly's built-in ``edits.shapePosition`` config, which
// activateLeaf flips on at re-render time via the plotConfigOverride
// hook. plotly_relayout then emits shape.y0/y1 on every drop; we match
// the shape by its name (range_min:<path> / range_max:<path>) and write
// through to leafState.params so the number inputs stay in sync too.
MODE_PLOT_EDITORS['range'] = (function() {
  const wired = new WeakMap();
  return {
    plotConfigOverride() { return { edits: { shapePosition: true } }; },
    activate(leaf, plotEl, commit) {
      const handler = (evt) => {
        if (!evt) return;
        for (const [key, val] of Object.entries(evt)) {
          const m = key.match(/^shapes\[(\d+)\]\.y[01]$/);
          if (!m) continue;
          const shape = (plotEl.layout?.shapes || [])[parseInt(m[1])];
          if (!shape || !shape.name) continue;
          const match = shape.name.match(/^range_(min|max):(.+)$/);
          if (!match || match[2] !== leaf.path) continue;
          const field = match[1] + '_value';
          const state = leafState[leaf.path];
          if (!state) continue;
          state.params[field] = Number(val);
          syncSiblingInputs(leaf.path, field, val, null);
          commit();
        }
      };
      plotEl.on('plotly_relayout', handler);
      wired.set(plotEl, handler);
    },
    deactivate(leaf, plotEl) {
      const h = wired.get(plotEl);
      if (h && plotEl.removeListener) plotEl.removeListener('plotly_relayout', h);
      wired.delete(plotEl);
    },
  };
})();

function interpOnRef(traj, x) {
  const rt = traj.ref_time || [];
  const rv = traj.ref_values || [];
  if (!rt.length) return 0;
  if (x <= rt[0]) return rv[0];
  if (x >= rt[rt.length - 1]) return rv[rv.length - 1];
  // Linear interp — good enough for placing a new control point.
  for (let i = 1; i < rt.length; i++) {
    if (rt[i] >= x) {
      const f = (x - rt[i - 1]) / (rt[i] - rt[i - 1]);
      return rv[i - 1] + f * (rv[i] - rv[i - 1]);
    }
  }
  return rv[rv.length - 1];
}

// ---- Leaf activation (click a leaf to wire its editor) ----
function activateLeaf(leaf) {
  if (activeLeafPath === leaf.path) return;
  deactivateLeaf();
  activeLeafPath = leaf.path;

  // Highlight the active leaf across all mounts.
  document.querySelectorAll(`[data-path="${escapeSelector(leaf.path)}"]`).forEach(el => {
    el.classList.add('node-active');
  });

  const idx = VARIABLE_INDEX[leaf.variable];
  const plotEl = document.getElementById(`plot-${idx}`);
  const commit = () => {
    renderVariablePlot(leaf.variable, idx);
    refreshPassStates();
    updateExport();
  };

  // Window brush — universal across modes (any leaf can carry a window).
  // Plugs in alongside the mode-specific editor rather than through
  // MODE_PLOT_EDITORS (which is metric-keyed).
  if (plotEl) injectWindowBrushControl(leaf, plotEl, commit);

  const editor = MODE_PLOT_EDITORS[leaf.metric];
  if (editor && plotEl) editor.activate(leaf, plotEl, commit);

  // Re-render picks up the editor's plotConfigOverride (if any) and
  // active-leaf-aware plot contributions (tube control-point markers).
  renderVariablePlot(leaf.variable, idx);
}

// Inject "🔲 Set window from plot" button into every active-leaf mount.
// Clicking toggles Plotly into horizontal-select dragmode; the next
// selection writes to ``leafState.window`` and restores normal zoom.
function injectWindowBrushControl(leaf, plotEl, commit) {
  document.querySelectorAll(`[data-path="${escapeSelector(leaf.path)}"] .node-editor`).forEach(slot => {
    const wrap = document.createElement('div');
    wrap.className = 'window-brush-wrap';
    const btn = document.createElement('button');
    btn.className = 'node-btn';
    btn.textContent = '🔲 Set window from plot';
    btn.title = 'Drag a horizontal range on the plot to set this leaf\'s window';
    btn.addEventListener('click', () => enterBrushMode(leaf, plotEl, commit, btn));
    wrap.appendChild(btn);
    // "Clear window" — quick escape from an authored window back to full.
    const clr = document.createElement('button');
    clr.className = 'node-btn';
    clr.textContent = 'clear';
    clr.title = 'Remove window (leaf scores over full trajectory)';
    clr.addEventListener('click', () => {
      const state = leafState[leaf.path];
      if (!state) return;
      state.window = {};
      syncSiblingInputs(leaf.path, 'window_start', null, null);
      syncSiblingInputs(leaf.path, 'window_end', null, null);
      commit();
    });
    wrap.appendChild(clr);
    slot.appendChild(wrap);
  });
}

function enterBrushMode(leaf, plotEl, commit, btn) {
  if (!plotEl) return;
  const prev = plotEl._fullLayout?.dragmode || 'zoom';
  Plotly.relayout(plotEl, { dragmode: 'select', selectdirection: 'h' });
  if (btn) { btn.classList.add('brush-armed'); btn.textContent = '🔲 Drag on plot…'; }

  const cleanup = () => {
    plotEl.removeListener?.('plotly_selected', onSel);
    Plotly.relayout(plotEl, { dragmode: prev });
    if (btn) { btn.classList.remove('brush-armed'); btn.textContent = '🔲 Set window from plot'; }
  };
  const onSel = (evt) => {
    if (!evt || !evt.range || !evt.range.x) return;
    const [x0, x1] = evt.range.x;
    const state = leafState[leaf.path];
    if (state) {
      state.window.start = Math.min(x0, x1);
      state.window.end = Math.max(x0, x1);
      syncSiblingInputs(leaf.path, 'window_start', state.window.start, null);
      syncSiblingInputs(leaf.path, 'window_end', state.window.end, null);
    }
    cleanup();
    commit();
  };
  plotEl.on('plotly_selected', onSel);
}

function deactivateLeaf() {
  if (!activeLeafPath) return;
  const path = activeLeafPath;
  const leaf = findLeaf(TREE_VIEW, path) || findLeaf(WORKING_TREE, path);
  activeLeafPath = null;
  document.querySelectorAll('.node-active').forEach(el => el.classList.remove('node-active'));
  if (leaf) {
    const editor = MODE_PLOT_EDITORS[leaf.metric];
    if (editor && editor.deactivate) {
      const idx = VARIABLE_INDEX[leaf.variable];
      const plotEl = document.getElementById(`plot-${idx}`);
      if (plotEl) editor.deactivate(leaf, plotEl);
    }
    renderVariablePlot(leaf.variable, VARIABLE_INDEX[leaf.variable]);
  }
}

function findLeaf(tree, path) {
  if (!tree) return null;
  let found = null;
  walkLeaves(tree, l => { if (l.path === path) found = l; });
  return found;
}

function windowShape(leaf) {
  const state = leafState[leaf.path];
  if (!state) return null;
  const w = state.window || {};
  if (w.start == null || w.end == null) return null;
  return {
    type: 'rect', xref: 'x', yref: 'paper',
    x0: Number(w.start), x1: Number(w.end), y0: 0, y1: 1,
    fillcolor: 'rgba(33,150,243,0.08)',
    line: { color: 'rgba(33,150,243,0.4)', width: 1, dash: 'dot' },
  };
}

// ---- Init ----
document.addEventListener('DOMContentLoaded', () => {
  renderAllPlots();
  renderAllNodeTrees();
  wireOverlayPickers();
  refreshPassStates();
  updateExport();
  // ESC deactivates the active leaf (escape hatch from edit mode).
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && activeLeafPath) deactivateLeaf();
  });
});

// ---- Plotly rendering ----
const PLOT_CFG = { responsive: true, displaylogo: false, scrollZoom: true };

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
  // visibility via the per-plot overlay picker.
  for (const ov of (vardata.overlays || [])) {
    const isSoft = ov.role === 'soft_check';
    traces.push({
      x: ov.time, y: ov.values,
      name: overlayTraceName(ov.role, ov.name),
      type: 'scatter', mode: 'lines',
      line: {
        color: isSoft ? '#7B1FA2' : '#388E3C',
        width: 1.2,
        dash: isSoft ? 'dot' : 'dashdot',
      },
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

  // Active leaf's editor may want a tweaked plot config (e.g., range
  // editor turns on shape dragging). Merge cleanly so other editors
  // can layer config bits without stomping each other.
  let cfg = PLOT_CFG;
  if (activeLeafPath) {
    const active = findLeaf(TREE_VIEW, activeLeafPath) || findLeaf(WORKING_TREE, activeLeafPath);
    if (active && active.variable === varname) {
      const editor = MODE_PLOT_EDITORS[active.metric];
      if (editor && editor.plotConfigOverride) {
        cfg = Object.assign({}, PLOT_CFG, editor.plotConfigOverride(active));
      }
    }
  }

  Plotly.newPlot(el, traces, {
    xaxis: { title: 'Time', hoverformat: '.6~g' },
    yaxis: { title: { text: 'Value', standoff: 10 }, tickformat: '.4g', automargin: true, nticks: 6 },
    margin: { t: 25, b: 45, l: 60, r: 20 },
    legend: { x: 0, y: 1, bgcolor: 'rgba(255,255,255,0.8)' },
    hovermode: 'x unified',
    shapes,
  }, cfg);
}

// ---- Recursive SpecNodeView ----
// Same component renders the tree at every mount point. Per-variable
// views pass ``variableFilter: varname``; the full-tree mount (Stage 3)
// will omit the filter.

function renderAllNodeTrees() {
  // Full unfiltered mount — Stage 3. Same recursive component as the
  // per-variable filtered mounts below. Edits in either view propagate
  // via the shared path-keyed leafState.
  const fullContainer = document.getElementById('nodes-full');
  if (fullContainer) {
    fullContainer.innerHTML = '';
    renderNode(TREE_VIEW, fullContainer, {});
  }
  VARIABLE_ORDER.forEach((varname, idx) => {
    const container = document.getElementById(`nodes-${idx}`);
    if (!container) return;
    container.innerHTML = '';
    renderNode(TREE_VIEW, container, { variableFilter: varname });
  });
}

function renderNode(node, container, opts) {
  if (!node) return false;
  if (node.kind === 'leaf') return renderLeaf(node, container, opts);
  return renderCombinator(node, container, opts);
}

function renderCombinator(node, container, opts) {
  // Recurse children first — skip combinators whose descendants are all
  // filtered out (keeps per-variable view focused on that variable).
  const childContainer = document.createElement('div');
  childContainer.className = 'node-children';
  let any = false;
  (node.children || []).forEach((child, i) => {
    const childOpts = Object.assign({}, opts, { parent: node, indexInParent: i });
    any = renderNode(child, childContainer, childOpts) || any;
  });
  if (!any && opts.variableFilter) return false;

  const wrapper = document.createElement('div');
  wrapper.className = 'node node-combinator';
  wrapper.dataset.path = node.path;

  const header = document.createElement('div');
  header.className = 'node-header';
  const pill = statusPill(node.passed);
  if (pill) header.appendChild(pill);
  const label = document.createElement('span');
  label.className = 'node-label';
  label.textContent = node.label || combinatorLabel(node);
  header.appendChild(label);

  // Structural controls — + adds a leaf child, − removes this node.
  // Disabled on per-variable filtered views (structural edits only from
  // the full-tree mount) and on the root of the working tree (no parent).
  if (!opts.variableFilter) {
    header.appendChild(addLeafButton(node));
    if (opts.parent) header.appendChild(removeNodeButton(node, opts));
  }

  wrapper.appendChild(header);
  wrapper.appendChild(childContainer);
  container.appendChild(wrapper);
  return true;
}

function combinatorLabel(node) {
  const n = (node.children || []).length;
  switch (node.combinator) {
    case 'and': return `and[${n}]`;
    case 'or': return `or[${n}]`;
    case 'warn': return 'warn';
    case 'k-of-n': return `k-of-n[${node.k}/${n}]`;
    case 'weighted': return `weighted[${n}]`;
    default: return node.combinator || '?';
  }
}

function renderLeaf(leaf, container, opts) {
  if (opts.variableFilter && leaf.variable !== opts.variableFilter) return false;

  const wrapper = document.createElement('div');
  wrapper.className = 'node node-leaf';
  wrapper.dataset.path = leaf.path;

  const header = document.createElement('div');
  header.className = 'node-header';

  // Visibility toggle — gates the leaf's plot contribution.
  const visToggle = document.createElement('input');
  visToggle.type = 'checkbox';
  visToggle.className = 'node-visible';
  visToggle.checked = leafState[leaf.path] && leafState[leaf.path].visible !== false;
  visToggle.title = "Show this leaf's plot contribution";
  visToggle.addEventListener('change', () => {
    if (leafState[leaf.path]) leafState[leaf.path].visible = visToggle.checked;
    renderVariablePlot(leaf.variable, VARIABLE_INDEX[leaf.variable]);
  });
  header.appendChild(visToggle);

  // Status + label + score
  const pill = statusPill(leaf.passed);
  if (pill) header.appendChild(pill);
  const label = document.createElement('span');
  label.className = 'node-label';
  let lbl = `${leaf.metric} · ${leaf.variable}`;
  if (leaf.against && leaf.against !== 'primary') lbl += ` [against=${leaf.against}]`;
  label.textContent = lbl;
  header.appendChild(label);

  if (leaf.score_display) {
    const score = document.createElement('span');
    score.className = 'node-score';
    score.textContent = leaf.score_display;
    header.appendChild(score);
  }
  if (leaf.cli_authoritative) {
    const badge = document.createElement('span');
    badge.className = 'cli-authoritative';
    badge.title = 'Edits reflected after running modelica-testing run';
    badge.textContent = 'CLI-authoritative';
    header.appendChild(badge);
  }
  // Structural control — − removes this leaf. Per-variable mount is
  // read-only structurally; only the full-tree mount can add/remove.
  if (!opts.variableFilter && opts.parent) {
    header.appendChild(removeNodeButton(leaf, opts));
  }
  wrapper.appendChild(header);

  // Controls — server-rendered HTML for mode fields + window inputs.
  const innerHtml = (leaf.mode_controls_html || '') + (leaf.window_controls_html || '');
  if (innerHtml) {
    const controls = document.createElement('div');
    controls.className = 'node-controls';
    controls.innerHTML = innerHtml;
    wrapper.appendChild(controls);
    wireLeafInputs(controls, leaf);
  }

  // Editor slot — MODE_PLOT_EDITORS[metric].activate mounts its rich
  // UI (tube control-point table, etc.) here when this leaf is active.
  // Empty div ensures the mount point exists even for modes without an
  // editor; activate() is a no-op for them.
  const editorSlot = document.createElement('div');
  editorSlot.className = 'node-editor';
  wrapper.appendChild(editorSlot);

  // Clicking the leaf's header (not inputs / buttons / toggles) makes
  // it the active leaf. Per-variable and full-tree mounts share state.
  header.addEventListener('click', (e) => {
    const tag = e.target.tagName;
    if (tag === 'INPUT' || tag === 'SELECT' || tag === 'BUTTON'
        || tag === 'TEXTAREA' || tag === 'LABEL') return;
    activateLeaf(leaf);
  });

  if (activeLeafPath === leaf.path) wrapper.classList.add('node-active');

  container.appendChild(wrapper);
  return true;
}

function statusPill(passed) {
  if (passed === undefined || passed === null) return null;
  const span = document.createElement('span');
  span.className = `node-status ${passed ? 'pass' : 'fail'}`;
  span.textContent = passed ? 'PASS' : 'FAIL';
  return span;
}

// ---- Structural editing (+ / − buttons, Stage 4) ----
// Adds/removes only; move (drag/up-down) is a later polish pass.
// Mutations happen on WORKING_TREE; TREE_VIEW stays pristine so per-
// leaf render artifacts (mode_controls_html, score_display) survive the
// structural change. On export, a dirty WORKING_TREE is emitted whole.

function addLeafButton(combinatorNode) {
  const btn = document.createElement('button');
  btn.className = 'node-btn node-btn-add';
  btn.textContent = '+';
  btn.title = 'Add a leaf child';
  btn.addEventListener('click', () => promptAddLeaf(combinatorNode));
  return btn;
}

function removeNodeButton(node, opts) {
  const btn = document.createElement('button');
  btn.className = 'node-btn node-btn-remove';
  btn.textContent = '−';
  btn.title = 'Remove this node';
  btn.addEventListener('click', () => {
    if (!confirm(`Remove ${node.kind === 'leaf' ? `leaf ${node.metric}·${node.variable}` : combinatorLabel(node)}?`)) return;
    const parent = findWorkingNode(opts.parent.path);
    if (!parent || !parent.children) return;
    parent.children.splice(opts.indexInParent, 1);
    markStructureDirty();
  });
  return btn;
}

function promptAddLeaf(combinatorNode) {
  const metric = (prompt(
    'Metric (nrmse / tube / final-only / range / event-timing / dominant-frequency):',
    'nrmse',
  ) || '').trim();
  if (!metric) return;
  const validMetrics = ['nrmse', 'tube', 'final-only', 'range', 'event-timing', 'dominant-frequency'];
  if (!validMetrics.includes(metric)) {
    alert(`Unknown metric ${JSON.stringify(metric)}. Valid: ${validMetrics.join(', ')}`);
    return;
  }
  const varname = (prompt(
    'Variable name (e.g. h, v):',
    VARIABLE_ORDER[0] || '',
  ) || '').trim();
  if (!varname) return;

  const node = findWorkingNode(combinatorNode.path);
  if (!node || !Array.isArray(node.children)) return;
  node.children.push({
    kind: 'leaf',
    metric, variable: varname,
    params: {}, against: 'primary', window: {},
    children: [],
    // Render artifacts synthesized just-in-time — no server round-trip
    // needed. pass/fail remains undefined (no evaluation yet).
    name: varname, mode_effective: metric,
    mode_values: {}, mode_controls_html: '', window_controls_html: '',
    window_values: {}, cli_authoritative: ['event-timing', 'dominant-frequency'].includes(metric),
  });
  markStructureDirty();
}

function findWorkingNode(path) {
  if (!WORKING_TREE) return null;
  if (WORKING_TREE.path === path) return WORKING_TREE;
  let found = null;
  (function walk(n) {
    if (found) return;
    if (n.path === path) { found = n; return; }
    (n.children || []).forEach(walk);
  })(WORKING_TREE);
  return found;
}

function rebuildPaths(node, root) {
  node.path = root;
  if (node.children) {
    node.children.forEach((c, i) => rebuildPaths(c, `${root}/children/${i}`));
  }
}

function markStructureDirty() {
  structureDirty = true;
  rebuildPaths(WORKING_TREE, '/metrics');
  // Re-render every mount from WORKING_TREE. TREE_VIEW stays as the
  // originally-rendered snapshot (for render artifacts on surviving
  // leaves); new leaves render from their own synthesized fields.
  renderAllNodeTreesFromWorking();
  refreshPassStates();
  updateExport();
}

function renderAllNodeTreesFromWorking() {
  // Swap TREE_VIEW to WORKING_TREE for the render pass only; any
  // downstream code that walks TREE_VIEW (e.g., plot rendering) picks
  // up the structural change naturally.
  const original = TREE_VIEW;
  window.TREE_VIEW = WORKING_TREE;  // shadow so leavesForVariable() sees it
  try {
    renderAllNodeTrees();
    // Plots may gain or lose leaves — re-render affected variables.
    VARIABLE_ORDER.forEach((v, i) => renderVariablePlot(v, i));
  } finally {
    window.TREE_VIEW = original;
  }
}

// Spec-strip — convert a tree_view node into its pure spec form,
// discarding render artifacts (mode_controls_html, score_display, ...).
// Used on patch export when the structure has been edited.
function nodeToSpec(node) {
  if (!node) return null;
  if (node.kind === 'leaf') {
    // Merge authored params with live state (the user may have edited
    // both structure AND scalar fields in one session).
    const state = leafState[node.path] || {};
    const mergedParams = Object.assign({}, node.params || {}, state.params || {});
    // Drop any render-only stashes that leaked into params.
    delete mergedParams.mode;
    const out = {
      metric: node.metric,
      variable: node.variable,
    };
    for (const k of Object.keys(mergedParams)) {
      if (mergedParams[k] !== undefined && mergedParams[k] !== null) out[k] = mergedParams[k];
    }
    if (node.against && node.against !== 'primary') out.against = node.against;
    const win = state.window && (state.window.start != null || state.window.end != null)
      ? state.window
      : (node.window && (node.window.start != null || node.window.end != null) ? node.window : null);
    if (win) {
      const w = {};
      if (win.start != null) w.start = Number(win.start);
      if (win.end != null) w.end = Number(win.end);
      out.window = w;
    }
    return out;
  }
  const out = { combinator: node.combinator, children: (node.children || []).map(nodeToSpec) };
  if (node.combinator === 'k-of-n') out.k = node.k;
  if (node.combinator === 'weighted') {
    out.weights = node.weights;
    out.threshold = node.threshold;
    if (node.direction && node.direction !== 'less') out.direction = node.direction;
  }
  return out;
}

function wireLeafInputs(controlsEl, leaf) {
  controlsEl.querySelectorAll('[data-field]').forEach(input => {
    const handler = (e) => onLeafFieldChange(leaf, e.target.dataset.field, e.target);
    input.addEventListener('input', handler);
    input.addEventListener('change', handler);
  });
}

function onLeafFieldChange(leaf, field, input) {
  let val;
  if (input.type === 'checkbox') val = input.checked;
  else if (input.type === 'number') {
    val = input.value === '' ? null : parseFloat(input.value);
    if (input.value !== '' && !Number.isFinite(val)) return;
  } else if (input.dataset.passthrough === 'true') {
    try { val = input.value ? JSON.parse(input.value) : null; }
    catch (_) { val = input.value; }
  } else {
    val = input.value === '' ? null : input.value;
  }
  const state = leafState[leaf.path];
  if (!state) return;
  if (field === 'window_start' || field === 'window_end') {
    const key = field === 'window_start' ? 'start' : 'end';
    if (val == null) delete state.window[key];
    else state.window[key] = val;
  } else {
    state.params[field] = val;
  }
  // The same leaf may render in multiple mount points (full tree + the
  // per-variable subtree). Sync sibling inputs so both views stay coherent
  // without re-rendering (which would break focus mid-edit).
  syncSiblingInputs(leaf.path, field, val, input);
  renderVariablePlot(leaf.variable, VARIABLE_INDEX[leaf.variable]);
  refreshPassStates();
  updateExport();
}

// Recompute every node's pass/fail against current state + update the
// DOM (pills + variable-level status + summary). Cheap — single tree walk.
// Walks WORKING_TREE when the structure has been edited (new leaves /
// removals) so pills match rendered structure; otherwise walks TREE_VIEW
// so cached CLI pass/fail shows through on untouched structure.
function refreshPassStates() {
  const tree = structureDirty && WORKING_TREE ? WORKING_TREE : TREE_VIEW;
  const passMap = recomputePassStates(tree);
  updatePassPills(passMap);
  updateSummaryFromMap(passMap, tree);
}

function updateSummaryFromMap(passMap, tree) {
  // Collect leaves from the active tree (structure-dirty pulls from
  // WORKING_TREE; untouched pulls from TREE_VIEW).
  const leaves = [];
  walkLeaves(tree, l => leaves.push(l));
  const getPass = (l) => passMap ? !!passMap[l.path] : !!l.passed;
  const n = leaves.length;
  const nPassed = leaves.filter(getPass).length;
  const el = document.getElementById('summary-text');
  if (el) {
    const cls = nPassed === n ? 'pass' : 'fail';
    el.className = cls;
    el.innerHTML = `<strong>${nPassed}</strong> / <strong>${n}</strong> leaves passed`;
  }
  VARIABLE_ORDER.forEach((varname, idx) => {
    const varLeaves = leaves.filter(l => l.variable === varname);
    const varPassed = varLeaves.length > 0 && varLeaves.every(getPass);
    const sel = document.querySelector(`.var-status[data-vidx="${idx}"]`);
    if (sel) {
      sel.className = `var-status ${varPassed ? 'pass' : 'fail'}`;
      sel.textContent = varPassed ? 'PASS' : 'FAIL';
    }
  });
}

function syncSiblingInputs(leafPath, field, val, sourceInput) {
  const selector = `[data-path="${escapeSelector(leafPath)}"] [data-field="${escapeSelector(field)}"]`;
  document.querySelectorAll(selector).forEach(inp => {
    if (inp === sourceInput) return;
    if (inp.type === 'checkbox') inp.checked = !!val;
    else inp.value = (val == null ? '' : val);
  });
}

function escapeSelector(s) {
  // CSS.escape is supported everywhere Plotly runs (2015+). Fall back to
  // a best-effort replacement for defensive builds without it.
  if (typeof CSS !== 'undefined' && CSS.escape) return CSS.escape(s);
  return String(s).replace(/(["\\])/g, '\\$1');
}

// ---- Summary ----
// When ``passMap`` is supplied (post-edit refresh), use the live-recomputed
// pass states; otherwise fall back to the CLI-computed leaf.passed for
// initial render.
function updateSummary(passMap) {
  const leaves = [];
  walkLeaves(TREE_VIEW, l => leaves.push(l));
  const getPass = (l) => passMap ? !!passMap[l.path] : !!l.passed;
  const n = leaves.length;
  const nPassed = leaves.filter(getPass).length;
  const el = document.getElementById('summary-text');
  if (el) {
    const cls = nPassed === n ? 'pass' : 'fail';
    el.className = cls;
    el.innerHTML = `<strong>${nPassed}</strong> / <strong>${n}</strong> leaves passed`;
  }
  VARIABLE_ORDER.forEach((varname, idx) => {
    const varLeaves = leaves.filter(l => l.variable === varname);
    const varPassed = varLeaves.length > 0 && varLeaves.every(getPass);
    const sel = document.querySelector(`.var-status[data-vidx="${idx}"]`);
    if (sel) {
      sel.className = `var-status ${varPassed ? 'pass' : 'fail'}`;
      sel.textContent = varPassed ? 'PASS' : 'FAIL';
    }
  });
}

// ---- Overlay picker ----
function wireOverlayPickers() {
  document.querySelectorAll('.overlay-picker .overlay-toggle').forEach(cb => {
    cb.addEventListener('change', (e) => {
      const vidx = parseInt(e.target.dataset.vidx);
      if (isNaN(vidx)) return;
      const name = e.target.dataset.ovName;
      const role = e.target.dataset.ovRole;
      setOverlayVisible(vidx, role, name, e.target.checked);
    });
  });
}

function setOverlayVisible(vidx, role, name, visible) {
  const el = document.getElementById(`plot-${vidx}`);
  if (!el || !el.data) return;
  const traceName = overlayTraceName(role, name);
  for (let i = 0; i < el.data.length; i++) {
    if (el.data[i].name === traceName) {
      Plotly.restyle(el, { visible: visible ? true : 'legendonly' }, [i]);
      return;
    }
  }
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
    Plotly.newPlot(el, [{
      x: traj.time, y: traj.values, name: 'Actual',
      type: 'scatter', mode: 'lines', line: { color: '#2196F3', width: 1 },
    }], {
      xaxis: { title: 'Time' },
      yaxis: { title: 'Value' },
      margin: { t: 25, b: 35, l: 60, r: 20 },
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

    const orig = state.original_params || {};
    Object.keys(state.params).forEach(field => {
      const cur = state.params[field];
      const o = orig[field];
      if (cur === o) return;
      if (typeof cur === 'number' && typeof o === 'number' && !floatsDiffer(cur, o)) return;
      ops.push({
        op: 'add',
        path: `${leaf.path}/${jsonPointerEscape(field)}`,
        value: cur,
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

function buildExportData() { return buildPatchData(); }

function updateExport() {
  const el = document.getElementById('export-json');
  if (el) el.value = JSON.stringify(buildExportData(), null, 2);
}

function copyExport() {
  const el = document.getElementById('export-json');
  if (!el) return;
  navigator.clipboard.writeText(el.value).then(() => showSaveStatus('Copied!'));
}

function downloadExport() {
  const data = JSON.stringify(buildExportData(), null, 2);
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
