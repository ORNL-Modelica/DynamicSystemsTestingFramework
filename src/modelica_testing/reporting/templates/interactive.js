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
// currentTree() returns whichever tree the UI should reflect: the
// original CLI-evaluated TREE_VIEW normally, or WORKING_TREE when the
// user has made structural edits. Readers (plot contributions, tree
// mounts, pass-state recompute, summary) go through this so structural
// edits show up without duplicate code paths.
function currentTree() {
  return structureDirty && WORKING_TREE ? WORKING_TREE : TREE_VIEW;
}

function walkLeaves(node, fn) {
  if (!node) return;
  if (node.kind === 'leaf') { fn(node); return; }
  (node.children || []).forEach(c => walkLeaves(c, fn));
}

function leavesForVariable(varname) {
  const out = [];
  walkLeaves(currentTree(), (leaf) => {
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
    const p = (leafState[leaf.path] || {}).params || {};
    const points = Array.isArray(p.tube_points) ? p.tube_points : [];
    const editor = MODE_PLOT_EDITORS['tube'];

    let upper, lower;
    if (points.length > 0 && editor && editor._resolveAllBoundsOnGrid) {
      // Delegate to the editor's resolver so per-point / per-side modes
      // flow through to the polygon. Same code path the table + pass/fail
      // use — one source of truth for "what bounds does this config imply?"
      const r = editor._resolveAllBoundsOnGrid(leaf, traj.ref_time);
      upper = r.upper;
      lower = r.lower;
    } else {
      // Scalar constant tube (no authored points) — fall back to width-mode
      // arithmetic against ref values.
      const rel = Number(p.tube_rel || 0);
      const abs = Number(p.tube_abs || 0);
      const minW = Number(p.tube_min_width || 0);
      const mode = p.tube_width_mode;
      const widths = traj.ref_values.map(v => {
        if (mode === 'rel') return Math.max(minW, rel * Math.abs(v));
        if (mode === 'band') return Math.max(minW, abs);
        return Math.max(minW, Math.max(abs, rel * Math.abs(v)));
      });
      upper = traj.ref_values.map((v, i) => v + widths[i]);
      lower = traj.ref_values.map((v, i) => v - widths[i]);
    }

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
    // as draggable-looking markers at their RESOLVED absolute y-values
    // (so the markers land on the tube boundary whatever mode's in play).
    // Legend excludes these — the polygon entry is already enough, and
    // a per-variable plot with 2+ tube leaves would otherwise flood the
    // legend with marker rows.
    if (activeLeafPath === leaf.path && points.length > 0 && editor) {
      const xs = points.map(pt => Number(pt.time));
      const ups = points.map((pt, i) => editor._resolvePoint(leaf, pt, i).upper);
      const los = points.map((pt, i) => editor._resolvePoint(leaf, pt, i).lower);
      traces.push({
        x: xs, y: ups, mode: 'markers', type: 'scatter',
        marker: { color: '#2e7d32', size: 10, symbol: 'triangle-up',
                  line: { color: 'white', width: 1 } },
        name: `Tube upper pts ${leaf.path}`, hoverinfo: 'x+y',
        showlegend: false,
      });
      traces.push({
        x: xs, y: los, mode: 'markers', type: 'scatter',
        marker: { color: '#c62828', size: 10, symbol: 'triangle-down',
                  line: { color: 'white', width: 1 } },
        name: `Tube lower pts ${leaf.path}`, hoverinfo: 'x+y',
        showlegend: false,
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

// Tube editor — full reimplementation of the pre-Stage-2 prototype,
// reshaped around path-keyed leafState. Features:
//
//   * Auto-seed two control points at trajectory start/end on first
//     activation (when tube_points is empty), widthMode='rel', width=0.05,
//     synced=true.
//   * Control-point table — columns adapt to synced + widthMode:
//     - synced + rel/band:  Time | Width     | ✕
//     - synced + absolute:  Time | Upper | Lower | ✕
//     - unsynced:           Time | Upper | Mode | Lower | Mode | ✕
//   * Width mode selector (rel / band / absolute) — global when synced;
//     per-point-per-side when unsynced.
//   * Sync/unsync toggle, interpolation (linear / constant), min-width floor.
//   * Shift+click on plot — adds a point; assigned to whichever bound
//     (upper/lower) the click y is closest to.
//   * Shift+drag on a control-point marker — moves it live.
//   * Shift+right-click on a marker — removes (≥1 point must remain).
//   * Live pass/fail + "% inside" + worst-violation readout.
MODE_PLOT_EDITORS['tube'] = (function() {
  const editorState = {};   // leafPath → {synced, perPointModes}
  const wired = new WeakMap();

  // ---- state accessors ------------------------------------------------
  function getParams(leaf) { return (leafState[leaf.path] || {}).params || {}; }
  function getWidthMode(leaf) {
    const m = getParams(leaf).tube_width_mode;
    return (m === 'band' || m === 'rel' || m === 'absolute') ? m : 'rel';
  }
  function setWidthMode(leaf, mode) { getParams(leaf).tube_width_mode = mode; }
  function getInterpolation(leaf) {
    return getParams(leaf).tube_interpolation === 'constant' ? 'constant' : 'linear';
  }
  function setInterpolation(leaf, v) { getParams(leaf).tube_interpolation = v; }
  function getMinWidth(leaf) { return Number(getParams(leaf).tube_min_width || 0); }
  function setMinWidth(leaf, v) { getParams(leaf).tube_min_width = Number(v) || 0; }

  function getPoints(leaf) {
    const p = getParams(leaf);
    if (!Array.isArray(p.tube_points)) p.tube_points = [];
    return p.tube_points;
  }
  // setPoints REPLACES the points array with fresh pt objects. Only use
  // for structural changes (seed / add / remove / import) where object
  // identity doesn't need to survive. For in-place edits (drag, input
  // change), mutate pt.time / pt.upper / pt.lower directly on the
  // reference returned by getPoints() — keeps drag.pt references valid
  // through sort.
  function setPoints(leaf, pts) {
    getParams(leaf).tube_points = pts.map(p => ({
      time: Number(p.time),
      upper: Number(p.upper),
      lower: Number(p.lower),
    }));
  }
  // sortPointsAndModes re-orders in place so a drag in progress holding
  // a pt reference still sees its pt move through the array. No clone
  // — callers holding drag.pt keep a valid reference.
  function sortPointsAndModes(leaf) {
    const pts = getPoints(leaf);
    if (pts.length <= 1) return;
    const es = ensureEditorState(leaf);
    const paired = pts.map((pt, i) => ({ pt, mode: es.perPointModes[i] || { upperMode: null, lowerMode: null } }));
    paired.sort((a, b) => a.pt.time - b.pt.time);
    for (let i = 0; i < paired.length; i++) {
      pts[i] = paired[i].pt;
      es.perPointModes[i] = paired[i].mode;
    }
  }

  function ensureEditorState(leaf) {
    if (editorState[leaf.path]) return editorState[leaf.path];
    // Infer synced from point values — saved asymmetric config re-opens unsynced.
    const pts = getPoints(leaf);
    const inferredSynced = pts.length === 0 ||
      pts.every(p => Math.abs(Number(p.upper) - Number(p.lower)) < 1e-12);
    editorState[leaf.path] = {
      synced: inferredSynced,
      perPointModes: pts.map(() => ({ upperMode: null, lowerMode: null })),
    };
    return editorState[leaf.path];
  }

  function refValueAt(leaf, t) {
    const traj = (VARIABLES_BY_NAME[leaf.variable] || {}).trajectory || {};
    const rt = traj.ref_time || traj.act_time || [];
    const rv = traj.ref_values || traj.act_values || [];
    return rt.length ? _interpLinear(rt, rv, t) : 0;
  }
  function signalRange(leaf) {
    const traj = (VARIABLES_BY_NAME[leaf.variable] || {}).trajectory || {};
    const rv = traj.ref_values || traj.act_values || [];
    if (!rv.length) return 1.0;
    return Math.max(...rv) - Math.min(...rv) || 1.0;
  }
  function roundSig(val, digits) {
    if (val === 0) return 0;
    const d = Math.ceil(Math.log10(Math.abs(val)));
    const pow = Math.pow(10, digits - d);
    return Math.round(val * pow) / pow;
  }

  function defaultBoundValue(leaf, t, mode) {
    const rv = refValueAt(leaf, t);
    const rvAbs = Math.abs(rv);
    const fallbackBand = roundSig(signalRange(leaf) * 0.05, 3);
    if (mode === 'rel') return { upper: 0.05, lower: 0.05 };
    if (mode === 'absolute') {
      const band = rvAbs > 1e-15 ? roundSig(rvAbs * 0.05, 3) : fallbackBand;
      return { upper: roundSig(rv + band, 4), lower: roundSig(rv - band, 4) };
    }
    const band = rvAbs > 1e-15 ? roundSig(rvAbs * 0.05, 3) : fallbackBand;
    return { upper: band, lower: band };
  }

  function seedIfEmpty(leaf) {
    if (getPoints(leaf).length > 0) return;
    const traj = (VARIABLES_BY_NAME[leaf.variable] || {}).trajectory || {};
    const rt = traj.ref_time || traj.act_time || [];
    if (!rt.length) return;
    if (!getParams(leaf).tube_width_mode) setWidthMode(leaf, 'rel');
    if (!getParams(leaf).tube_interpolation) setInterpolation(leaf, 'linear');
    const mode = getWidthMode(leaf);
    setPoints(leaf, [
      { time: rt[0], ...defaultBoundValue(leaf, rt[0], mode) },
      { time: rt[rt.length - 1], ...defaultBoundValue(leaf, rt[rt.length - 1], mode) },
    ]);
    const es = ensureEditorState(leaf);
    es.perPointModes = [{ upperMode: null, lowerMode: null }, { upperMode: null, lowerMode: null }];
  }

  // ---- resolve point to absolute y-bounds ----------------------------
  function resolvePoint(leaf, pt, ptIdx) {
    const globalMode = getWidthMode(leaf);
    const es = ensureEditorState(leaf);
    const pm = es.perPointModes[ptIdx] || { upperMode: null, lowerMode: null };
    const uMode = es.synced ? globalMode : (pm.upperMode || globalMode);
    const lMode = es.synced ? globalMode : (pm.lowerMode || globalMode);
    const rv = refValueAt(leaf, pt.time);
    const rvAbs = Math.abs(rv);
    const minW = getMinWidth(leaf);

    let upper, lower;
    if (uMode === 'absolute') upper = Number(pt.upper);
    else if (uMode === 'rel') {
      let w = Number(pt.upper) * rvAbs;
      if (minW > 0) w = Math.max(w, minW);
      upper = rv + w;
    } else {
      let w = Number(pt.upper);
      if (minW > 0) w = Math.max(w, minW);
      upper = rv + w;
    }
    if (lMode === 'absolute') lower = Number(pt.lower);
    else if (lMode === 'rel') {
      let w = Number(pt.lower) * rvAbs;
      if (minW > 0) w = Math.max(w, minW);
      lower = rv - w;
    } else {
      let w = Number(pt.lower);
      if (minW > 0) w = Math.max(w, minW);
      lower = rv - w;
    }
    return { upper, lower, uMode, lMode };
  }

  function setBoundFromAbsoluteY(leaf, ptIdx, bound, yAbs) {
    const pts = getPoints(leaf);
    const pt = pts[ptIdx];
    if (!pt) return;
    const globalMode = getWidthMode(leaf);
    const es = ensureEditorState(leaf);
    const pm = es.perPointModes[ptIdx] || { upperMode: null, lowerMode: null };
    const isUpper = bound === 'upper';
    const mode = es.synced ? globalMode : (pm[isUpper ? 'upperMode' : 'lowerMode'] || globalMode);
    const rv = refValueAt(leaf, pt.time);
    const rvAbs = Math.abs(rv);

    if (mode === 'absolute') pt[bound] = roundSig(yAbs, 4);
    else if (mode === 'rel') {
      const offset = isUpper ? yAbs - rv : rv - yAbs;
      pt[bound] = rvAbs > 1e-15 ? roundSig(Math.max(offset / rvAbs, 0), 3) : 0.05;
    } else {
      const offset = isUpper ? yAbs - rv : rv - yAbs;
      pt[bound] = roundSig(Math.max(offset, 0), 3);
    }
    if (es.synced) pt[isUpper ? 'lower' : 'upper'] = pt[bound];
    // In-place mutation only — don't clone via setPoints here. During a
    // drag, callers hold a pt reference that must survive to the next
    // frame; cloning would break that invariant.
  }

  // ---- pass/fail + rendering helpers ---------------------------------
  function resolveAllBoundsOnGrid(leaf, grid) {
    const pts = [...getPoints(leaf)].sort((a, b) => a.time - b.time);
    const ctrlTimes = pts.map(p => p.time);
    const resUpper = pts.map((p, i) => resolvePoint(leaf, p, i).upper);
    const resLower = pts.map((p, i) => resolvePoint(leaf, p, i).lower);
    const fn = getInterpolation(leaf) === 'constant' ? _interpStep : _interpLinear;
    return {
      upper: grid.map(t => fn(ctrlTimes, resUpper, t)),
      lower: grid.map(t => fn(ctrlTimes, resLower, t)),
      ctrlTimes, resUpper, resLower,
    };
  }

  function evaluatePass(leaf) {
    const traj = (VARIABLES_BY_NAME[leaf.variable] || {}).trajectory || {};
    const rt = traj.ref_time || traj.act_time || [];
    if (!rt.length || !getPoints(leaf).length) return { passed: true, pctInside: 100, worst: 0, worstT: 0 };
    const { upper, lower } = resolveAllBoundsOnGrid(leaf, rt);
    let nInside = 0, worst = 0, worstT = 0;
    for (let i = 0; i < rt.length; i++) {
      const actV = _interpLinear(traj.act_time || [], traj.act_values || [], rt[i]);
      if (actV >= lower[i] && actV <= upper[i]) nInside++;
      else {
        const v = Math.max(actV - upper[i], lower[i] - actV);
        if (v > worst) { worst = v; worstT = rt[i]; }
      }
    }
    return {
      passed: nInside === rt.length,
      pctInside: (nInside / rt.length) * 100,
      worst, worstT,
    };
  }

  // ---- table UI -------------------------------------------------------
  function unitLabel(mode) {
    return mode === 'rel' ? 'fraction' : mode === 'absolute' ? 'y-value' : 'signal units';
  }

  // Re-render tube UI into every active slot for this leaf. Called on
  // every state mutation — keeps the full-tree mount and per-variable
  // mount in lock step. No per-slot parameter threads through the
  // event handlers; if you mutate state, you call refresh(), period.
  function refreshTables(leaf, commit) {
    const slots = document.querySelectorAll(
      `[data-path="${escapeSelector(leaf.path)}"] .node-editor`,
    );
    slots.forEach(slot => renderInto(leaf, slot, commit));
  }

  // Render the tube UI into its dedicated sub-container inside the
  // slot. Leaves the slot's other children (window-brush control)
  // alone — core lifecycle owns slot-level cleanup.
  function renderInto(leaf, slot, commit) {
    let container = slot.querySelector(':scope > .tube-editor-ui');
    if (!container) {
      container = document.createElement('div');
      container.className = 'tube-editor-ui';
      slot.appendChild(container);
    }
    container.innerHTML = '';

    const es = ensureEditorState(leaf);
    const widthMode = getWidthMode(leaf);

    const controls = document.createElement('div');
    controls.className = 'tube-editor-controls';
    controls.innerHTML = `
      <label>Width mode
        <select data-tube-field="widthMode" ${!es.synced ? 'disabled' : ''}>
          <option value="rel">rel (fraction of |ref|)</option>
          <option value="band">band (± offset)</option>
          <option value="absolute">absolute (y-value)</option>
        </select>
      </label>
      <label>Symmetry
        <select data-tube-field="synced">
          <option value="true">synced (upper = lower)</option>
          <option value="false">unsynced (per-side)</option>
        </select>
      </label>
      <label>Interpolation
        <select data-tube-field="interpolation">
          <option value="linear">linear</option>
          <option value="constant">step</option>
        </select>
      </label>
      <label>Min width
        <input type="number" step="any" min="0" data-tube-field="minWidth" style="width:6em">
      </label>
      <span class="tube-status"></span>
    `;
    controls.querySelector('[data-tube-field="widthMode"]').value = widthMode;
    controls.querySelector('[data-tube-field="synced"]').value = String(es.synced);
    controls.querySelector('[data-tube-field="interpolation"]').value = getInterpolation(leaf);
    controls.querySelector('[data-tube-field="minWidth"]').value = getMinWidth(leaf);

    controls.querySelector('[data-tube-field="widthMode"]').addEventListener('change', (e) => {
      const old = widthMode, next = e.target.value;
      if (old !== next) reprojectAllPoints(leaf, old, next);
      setWidthMode(leaf, next);
      refreshTables(leaf, commit); commit();
    });
    controls.querySelector('[data-tube-field="synced"]').addEventListener('change', (e) => {
      const syn = e.target.value === 'true';
      es.synced = syn;
      if (syn) {
        // In-place sync — mutate lower to match upper on each existing pt
        // object; no setPoints clone, so downstream references stay valid.
        getPoints(leaf).forEach(p => { p.lower = p.upper; });
        es.perPointModes.forEach(pm => { pm.upperMode = null; pm.lowerMode = null; });
      }
      refreshTables(leaf, commit); commit();
    });
    controls.querySelector('[data-tube-field="interpolation"]').addEventListener('change', (e) => {
      setInterpolation(leaf, e.target.value); commit();
    });
    controls.querySelector('[data-tube-field="minWidth"]').addEventListener('input', (e) => {
      setMinWidth(leaf, e.target.value); commit();
    });
    container.appendChild(controls);

    const hint = document.createElement('div');
    hint.className = 'editor-hint';
    hint.innerHTML = 'Shift+click on the plot to add a point · Shift+drag to move · Shift+right-click to delete';
    container.appendChild(hint);

    const table = document.createElement('table');
    table.className = 'tube-table';
    const header = table.insertRow();
    const pts = getPoints(leaf);
    if (es.synced && widthMode !== 'absolute') {
      header.innerHTML = `<th>Time</th><th>Width (${unitLabel(widthMode)})</th><th></th>`;
    } else if (es.synced && widthMode === 'absolute') {
      header.innerHTML = `<th>Time</th><th>Upper (y)</th><th>Lower (y)</th><th></th>`;
    } else {
      header.innerHTML = `<th>Time</th><th>Upper</th><th>Mode</th><th>Lower</th><th>Mode</th><th></th>`;
    }

    pts.forEach((pt, i) => {
      const row = table.insertRow();
      if (es.synced && widthMode !== 'absolute') {
        row.appendChild(numberCell(pt.time, v => onPointField(leaf, i, 'time', v, commit), 'any'));
        row.appendChild(numberCell(pt.upper, v => onPointField(leaf, i, 'width', v, commit),
                                   widthMode === 'rel' ? '0.001' : 'any', 0));
        row.appendChild(removeCell(leaf, i, commit));
      } else if (es.synced && widthMode === 'absolute') {
        row.appendChild(numberCell(pt.time, v => onPointField(leaf, i, 'time', v, commit), 'any'));
        row.appendChild(numberCell(pt.upper, v => onPointField(leaf, i, 'upper', v, commit), 'any'));
        row.appendChild(numberCell(pt.lower, v => onPointField(leaf, i, 'lower', v, commit), 'any'));
        row.appendChild(removeCell(leaf, i, commit));
      } else {
        const pm = es.perPointModes[i] || { upperMode: null, lowerMode: null };
        const uMode = pm.upperMode || widthMode;
        const lMode = pm.lowerMode || widthMode;
        row.appendChild(numberCell(pt.time, v => onPointField(leaf, i, 'time', v, commit), 'any'));
        row.appendChild(numberCell(pt.upper, v => onPointField(leaf, i, 'upper', v, commit),
                                   uMode === 'rel' ? '0.001' : 'any'));
        row.appendChild(modeCell(uMode, m => onSideMode(leaf, i, 'upper', m, commit)));
        row.appendChild(numberCell(pt.lower, v => onPointField(leaf, i, 'lower', v, commit),
                                   lMode === 'rel' ? '0.001' : 'any'));
        row.appendChild(modeCell(lMode, m => onSideMode(leaf, i, 'lower', m, commit)));
        row.appendChild(removeCell(leaf, i, commit));
      }
    });
    container.appendChild(table);

    const addBtn = document.createElement('button');
    addBtn.className = 'node-btn node-btn-add';
    addBtn.textContent = '+ add point';
    addBtn.addEventListener('click', () => addPointAt(leaf, null, null, commit));
    container.appendChild(addBtn);

    updateStatusInContainer(leaf, container);
  }

  function numberCell(value, onChange, step, minVal) {
    const td = document.createElement('td');
    const inp = document.createElement('input');
    inp.type = 'number'; inp.step = step || 'any';
    if (minVal !== undefined) inp.min = String(minVal);
    inp.value = value;
    inp.addEventListener('input', () => {
      const v = parseFloat(inp.value);
      if (Number.isFinite(v)) onChange(v);
    });
    td.appendChild(inp);
    return td;
  }

  function modeCell(mode, onChange) {
    const td = document.createElement('td');
    const sel = document.createElement('select');
    for (const opt of ['band', 'rel', 'absolute']) {
      const o = document.createElement('option');
      o.value = opt; o.textContent = opt;
      if (mode === opt) o.selected = true;
      sel.appendChild(o);
    }
    sel.addEventListener('change', () => onChange(sel.value));
    td.appendChild(sel);
    return td;
  }

  function removeCell(leaf, i, commit) {
    const td = document.createElement('td');
    const btn = document.createElement('button');
    btn.className = 'node-btn node-btn-remove';
    btn.textContent = '✕';
    btn.title = 'Remove point';
    btn.addEventListener('click', () => removePointAt(leaf, i, commit));
    td.appendChild(btn);
    return td;
  }

  function onPointField(leaf, i, field, value, commit) {
    const pts = getPoints(leaf);
    const pt = pts[i];
    if (!pt) return;
    // In-place mutation only — preserves pt identity for any drag in flight.
    if (field === 'time') pt.time = value;
    else if (field === 'width') { pt.upper = value; pt.lower = value; }
    else if (field === 'upper') pt.upper = value;
    else if (field === 'lower') pt.lower = value;
    if (field === 'time') sortPointsAndModes(leaf);
    refreshTables(leaf, commit);
    commit();
  }

  function onSideMode(leaf, i, side, newMode, commit) {
    const es = ensureEditorState(leaf);
    const pm = es.perPointModes[i];
    const globalMode = getWidthMode(leaf);
    const oldMode = pm[side + 'Mode'] || globalMode;
    if (oldMode === newMode) return;

    const pts = getPoints(leaf);
    const pt = pts[i];
    const rv = refValueAt(leaf, pt.time);
    const rvAbs = Math.abs(rv);
    const isUpper = side === 'upper';

    let yAbs;
    if (oldMode === 'absolute') yAbs = pt[side];
    else if (oldMode === 'rel') {
      const offset = pt[side] * rvAbs;
      yAbs = isUpper ? rv + offset : rv - offset;
    } else yAbs = isUpper ? rv + pt[side] : rv - pt[side];

    if (newMode === 'absolute') pt[side] = roundSig(yAbs, 4);
    else if (newMode === 'rel') {
      const offset = isUpper ? yAbs - rv : rv - yAbs;
      pt[side] = rvAbs > 1e-15 ? roundSig(Math.max(offset / rvAbs, 0), 3) : 0.05;
    } else {
      const offset = isUpper ? yAbs - rv : rv - yAbs;
      pt[side] = roundSig(Math.max(offset, 0), 3);
    }
    pm[side + 'Mode'] = newMode;
    // In-place mutation of pt; no clone.
    refreshTables(leaf, commit);
    commit();
  }

  function reprojectAllPoints(leaf, oldMode, newMode) {
    // In-place reprojection — iterates the live array and mutates each
    // pt. Caller holds the pt references; we keep them valid.
    getPoints(leaf).forEach(pt => {
      const rv = refValueAt(leaf, pt.time);
      const rvAbs = Math.abs(rv);
      for (const side of ['upper', 'lower']) {
        const isUpper = side === 'upper';
        let yAbs;
        if (oldMode === 'absolute') yAbs = pt[side];
        else if (oldMode === 'rel') {
          const offset = pt[side] * rvAbs;
          yAbs = isUpper ? rv + offset : rv - offset;
        } else yAbs = isUpper ? rv + pt[side] : rv - pt[side];

        if (newMode === 'absolute') pt[side] = roundSig(yAbs, 4);
        else if (newMode === 'rel') {
          const offset = isUpper ? yAbs - rv : rv - yAbs;
          pt[side] = rvAbs > 1e-15 ? roundSig(Math.max(offset / rvAbs, 0), 3) : 0.05;
        } else {
          const offset = isUpper ? yAbs - rv : rv - yAbs;
          pt[side] = roundSig(Math.max(offset, 0), 3);
        }
      }
    });
  }

  function addPointAt(leaf, atT, atY, commit) {
    const pts = getPoints(leaf);
    const traj = (VARIABLES_BY_NAME[leaf.variable] || {}).trajectory || {};
    const rt = traj.ref_time || traj.act_time || [];
    let t;
    if (atT != null) t = atT;
    else if (pts.length >= 2) t = (pts[pts.length - 2].time + pts[pts.length - 1].time) / 2;
    else t = rt.length ? rt[Math.floor(rt.length / 2)] : 0;

    const mode = getWidthMode(leaf);
    const d = defaultBoundValue(leaf, t, mode);
    const newPt = { time: t, ...d };
    const newIdx = pts.length;
    pts.push(newPt);
    ensureEditorState(leaf).perPointModes.push({ upperMode: null, lowerMode: null });
    setPoints(leaf, pts);

    // If the click came from the plot, bias the placement toward the clicked bound.
    if (atY != null) {
      const r = resolvePoint(leaf, newPt, newIdx);
      const bound = Math.abs(atY - r.upper) <= Math.abs(atY - r.lower) ? 'upper' : 'lower';
      setBoundFromAbsoluteY(leaf, newIdx, bound, atY);
    }
    sortPointsAndModes(leaf);
    refreshTables(leaf, commit);
    commit();
  }

  function removePointAt(leaf, i, commit) {
    const pts = getPoints(leaf);
    if (pts.length <= 1) return;
    pts.splice(i, 1);
    ensureEditorState(leaf).perPointModes.splice(i, 1);
    setPoints(leaf, pts);
    refreshTables(leaf, commit);
    commit();
  }

  function updateStatusInContainer(leaf, container) {
    const el = container.querySelector('.tube-status');
    if (!el) return;
    const r = evaluatePass(leaf);
    el.innerHTML = r.passed
      ? `<span class="pass">PASS</span> (${r.pctInside.toFixed(1)}% inside)`
      : `<span class="fail">FAIL</span> (${r.pctInside.toFixed(1)}% inside · worst ${r.worst.toExponential(2)} at t=${r.worstT.toPrecision(4)})`;
  }

  // ---- plot interactions ---------------------------------------------
  function attachPlotHandlers(leaf, plotEl, commit) {
    // Drag tracks the pt reference, not an index. Sorting during drag
    // reorders the array in place (sortPointsAndModes keeps the same pt
    // objects); we re-derive the current index via indexOf each frame.
    // This is what gives the "drag past another point" smooth-swap: the
    // polygon re-draws on every frame with all points in time order,
    // and the table row follows its pt object through the sort.
    const drag = { active: false, pt: null, bound: null, clickStart: null };
    let rafPending = null;

    function pxToData(evt) {
      const xa = plotEl._fullLayout?.xaxis;
      const ya = plotEl._fullLayout?.yaxis;
      if (!xa || !ya) return null;
      const area = plotEl.querySelector('.nsewdrag') || plotEl;
      const rect = area.getBoundingClientRect();
      return { x: xa.p2d(evt.clientX - rect.left), y: ya.p2d(evt.clientY - rect.top) };
    }

    function findNearestCP(dataX, dataY) {
      const xa = plotEl._fullLayout?.xaxis;
      const ya = plotEl._fullLayout?.yaxis;
      if (!xa || !ya) return null;
      const pts = getPoints(leaf);
      let best = null;
      pts.forEach((pt, i) => {
        const r = resolvePoint(leaf, pt, i);
        for (const bound of ['upper', 'lower']) {
          const dx = xa.d2p(pt.time) - xa.d2p(dataX);
          const dy = ya.d2p(r[bound]) - ya.d2p(dataY);
          const dist = Math.hypot(dx, dy);
          if (!best || dist < best.dist) best = { pt, ptIdx: i, bound, dist };
        }
      });
      return best;
    }

    function onMouseDown(evt) {
      if (!evt.shiftKey || evt.button !== 0) return;
      evt.stopPropagation();
      evt.stopImmediatePropagation();
      evt.preventDefault();
      const d = pxToData(evt);
      if (!d) return;
      const nearest = findNearestCP(d.x, d.y);
      if (nearest && nearest.dist < 15) {
        drag.active = true;
        drag.pt = nearest.pt;
        drag.bound = nearest.bound;
      } else {
        drag.clickStart = d;
      }
    }

    function onMouseMove(evt) {
      if (drag.active) {
        evt.preventDefault();
        const d = pxToData(evt);
        if (!d || !drag.pt) return;
        // Mutate the tracked pt in place — no setPoints clone, so
        // references stay alive across sort.
        const traj = (VARIABLES_BY_NAME[leaf.variable] || {}).trajectory || {};
        const rt = traj.ref_time || traj.act_time || [];
        const tMin = rt[0], tMax = rt[rt.length - 1];
        drag.pt.time = roundSig(Math.max(tMin, Math.min(tMax, d.x)), 6);
        const ptIdx = getPoints(leaf).indexOf(drag.pt);
        if (ptIdx >= 0) setBoundFromAbsoluteY(leaf, ptIdx, drag.bound, d.y);
        // Sort during the drag so a point crossing another smoothly
        // swaps array positions — the pt reference follows it.
        sortPointsAndModes(leaf);
        if (!rafPending) {
          rafPending = requestAnimationFrame(() => {
            refreshTables(leaf, commit);
            commit();
            rafPending = null;
          });
        }
      } else if (drag.clickStart) {
        const d = pxToData(evt);
        if (!d) return;
        if (Math.abs(d.x - drag.clickStart.x) > 1e-10 ||
            Math.abs(d.y - drag.clickStart.y) > 1e-10) drag.clickStart = null;
      }
    }

    function onMouseUp() {
      if (drag.active) {
        drag.active = false; drag.pt = null;
        // Final sort + render on drop (rafPending may have a pending
        // mid-frame refresh — this settles the final state either way).
        sortPointsAndModes(leaf);
        refreshTables(leaf, commit);
        commit();
        return;
      }
      if (drag.clickStart) {
        const c = drag.clickStart;
        drag.clickStart = null;
        addPointAt(leaf, c.x, c.y, commit);
      }
    }

    function onContextMenu(evt) {
      if (!evt.shiftKey) return;
      const d = pxToData(evt);
      if (!d) return;
      const nearest = findNearestCP(d.x, d.y);
      if (nearest && nearest.dist < 15) {
        evt.preventDefault();
        removePointAt(leaf, nearest.ptIdx, commit);
      }
    }

    plotEl.addEventListener('mousedown', onMouseDown, true);
    plotEl.addEventListener('contextmenu', onContextMenu, true);
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);

    wired.set(plotEl, () => {
      plotEl.removeEventListener('mousedown', onMouseDown, true);
      plotEl.removeEventListener('contextmenu', onContextMenu, true);
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);
    });
  }

  return {
    // Editor appends its sub-container into each slot (core already
    // cleared the slot + injected the window-brush button).
    activate(leaf, plotEl, commit) {
      ensureEditorState(leaf);
      seedIfEmpty(leaf);
      refreshTables(leaf, commit);
      attachPlotHandlers(leaf, plotEl, commit);
      commit();
    },
    // Editor only owns event-handler cleanup. Slot DOM is core's.
    deactivate(leaf, plotEl) {
      const cleanup = wired.get(plotEl);
      if (cleanup) { cleanup(); wired.delete(plotEl); }
    },
    // Exposed for the plot contribution so the polygon matches editor state.
    _resolveAllBoundsOnGrid: resolveAllBoundsOnGrid,
    _getPoints: getPoints,
    _resolvePoint: resolvePoint,
  };
})();

function _interpStep(xArr, yArr, x) {
  if (!xArr.length) return 0;
  if (x <= xArr[0]) return yArr[0];
  let val = yArr[0];
  for (let i = 0; i < xArr.length; i++) {
    if (xArr[i] <= x) val = yArr[i]; else break;
  }
  return val;
}

// Range editor — drag the min/max dashed lines directly on the plot.
// Leans on Plotly's built-in ``edits.shapePosition`` config, which
// activateLeaf flips on at re-render time via the plotConfigOverride
// hook. plotly_relayout then emits shape.y0/y1 on every drop; we match
// the shape by its name (range_min:<path> / range_max:<path>) and write
// through to leafState.params so the number inputs stay in sync too.
MODE_PLOT_EDITORS['range'] = (function() {
  const wired = new WeakMap();
  return {
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
// Unified activate/deactivate lifecycle. ``.node-editor`` DOM cleanup
// and window-brush injection are centralized here so no editor has to
// manage DOM on deactivate (it only has to unwire its own event
// handlers). Every state mutation going through this cycle is
// idempotent — clicking the same leaf twice or switching leaves never
// accumulates UI, avoiding the "extra brush button" / "duplicate
// handler" class of bugs.
function activateLeaf(leaf) {
  if (activeLeafPath === leaf.path) return;
  deactivateLeaf();
  activeLeafPath = leaf.path;

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

  // Clear every .node-editor slot for this leaf (full-tree + per-variable
  // mounts may both have one). Then inject the universal window-brush
  // button into each. Then let the mode's editor (if any) append its
  // own UI. Slot ownership sequence: core clears → core injects brush →
  // editor appends. Editors only clean up their event handlers; DOM is
  // core's responsibility.
  getEditorSlots(leaf).forEach(slot => { slot.innerHTML = ''; });
  if (plotEl) {
    getEditorSlots(leaf).forEach(slot => {
      slot.appendChild(buildWindowBrushControl(leaf, plotEl, commit));
    });
  }

  const editor = MODE_PLOT_EDITORS[leaf.metric];
  if (editor && plotEl) editor.activate(leaf, plotEl, commit);

  renderVariablePlot(leaf.variable, idx);
}

function getEditorSlots(leaf) {
  return Array.from(document.querySelectorAll(
    `[data-path="${escapeSelector(leaf.path)}"] .node-editor`,
  ));
}

// Build — not inject — the window-brush control as a single DOM node.
// Caller owns where it goes so lifecycle is deterministic.
function buildWindowBrushControl(leaf, plotEl, commit) {
  const wrap = document.createElement('div');
  wrap.className = 'window-brush-wrap';
  const btn = document.createElement('button');
  btn.className = 'node-btn';
  btn.textContent = '🔲 Set window from plot';
  btn.title = 'Drag a horizontal range on the plot to set this leaf\'s window';
  btn.addEventListener('click', () => enterBrushMode(leaf, plotEl, commit, btn));
  wrap.appendChild(btn);
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
  return wrap;
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
    // Editor cleans up its event handlers; DOM clearing is core's job.
    const editor = MODE_PLOT_EDITORS[leaf.metric];
    if (editor && editor.deactivate) {
      const idx = VARIABLE_INDEX[leaf.variable];
      const plotEl = document.getElementById(`plot-${idx}`);
      if (plotEl) editor.deactivate(leaf, plotEl);
    }
    getEditorSlots(leaf).forEach(slot => { slot.innerHTML = ''; });
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
  wireErrorOverlays();
  refreshPassStates();
  updateExport();
  // ESC deactivates the active leaf (escape hatch from edit mode).
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && activeLeafPath) deactivateLeaf();
  });
});

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

// ---- Recursive SpecNodeView ----
// Same component renders the tree at every mount point. Per-variable
// views pass ``variableFilter: varname``; the full-tree mount (Stage 3)
// will omit the filter.

function renderAllNodeTrees() {
  // Full unfiltered mount — Stage 3. Same recursive component as the
  // per-variable filtered mounts below. Edits in either view propagate
  // via the shared path-keyed leafState. When the user has made
  // structural edits (+/−), ``currentTree()`` returns WORKING_TREE so
  // the mutated tree shows up; otherwise the original TREE_VIEW.
  const tree = currentTree();
  const fullContainer = document.getElementById('nodes-full');
  if (fullContainer) {
    fullContainer.innerHTML = '';
    renderNode(tree, fullContainer, {});
  }
  VARIABLE_ORDER.forEach((varname, idx) => {
    const container = document.getElementById(`nodes-${idx}`);
    if (!container) return;
    container.innerHTML = '';
    renderNode(tree, container, { variableFilter: varname });
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
  // Available on both full-tree AND per-variable mounts; per-variable
  // mounts prefill the + modal with the filtered variable so edits
  // stay scoped to what the user is looking at.
  header.appendChild(addLeafButton(node, opts.variableFilter));
  if (opts.parent) header.appendChild(removeNodeButton(node, opts));

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
  // Structural control — − removes this leaf. Available in every mount
  // (full-tree + per-variable) so users can trim from wherever they're
  // looking. The root of a working tree has no parent → no button.
  if (opts.parent) header.appendChild(removeNodeButton(leaf, opts));
  wrapper.appendChild(header);

  // Controls — server-rendered HTML for mode fields + window inputs.
  // Tube leaves intentionally skip mode schema inputs: the rich
  // interactive editor (activated on click) owns that config surface
  // and would duplicate the same fields. Window inputs still show for
  // tube so the user can scope a tube to a time window.
  const skipModeControls = (leaf.metric === 'tube');
  const innerHtml =
    (skipModeControls ? '' : (leaf.mode_controls_html || ''))
    + (leaf.window_controls_html || '');
  if (innerHtml) {
    const controls = document.createElement('div');
    controls.className = 'node-controls';
    controls.innerHTML = innerHtml;
    wrapper.appendChild(controls);
    wireLeafInputs(controls, leaf);
  }

  // Tube-leaf summary when inactive — "N pts, rel" quick readout so
  // the user sees config state without activating. The editor itself
  // (with full table) mounts into .node-editor on activate.
  if (leaf.metric === 'tube' && activeLeafPath !== leaf.path) {
    const state = leafState[leaf.path];
    const pts = (state?.params?.tube_points || []);
    const wm = state?.params?.tube_width_mode || 'rel';
    if (pts.length > 0) {
      const summary = document.createElement('div');
      summary.className = 'tube-summary';
      summary.textContent = `${pts.length} control point${pts.length === 1 ? '' : 's'} · ${wm}`;
      wrapper.appendChild(summary);
    }
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

function addLeafButton(combinatorNode, presetVariable) {
  const btn = document.createElement('button');
  btn.className = 'node-btn node-btn-add';
  btn.textContent = '+';
  btn.title = presetVariable
    ? `Add a leaf for ${presetVariable}`
    : 'Add a leaf child';
  btn.addEventListener('click', () => promptAddLeaf(combinatorNode, presetVariable));
  return btn;
}

function removeNodeButton(node, opts) {
  const btn = document.createElement('button');
  btn.className = 'node-btn node-btn-remove';
  btn.textContent = '−';
  btn.title = 'Remove this node';
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    openRemoveConfirm(btn, node, opts);
  });
  return btn;
}

// Inline remove-confirm popup. Pops up next to the − button with the
// node's label + ✓ / ✗ buttons. Replaces window.confirm (which hijacks
// the page and can't be styled). One popup at a time — clicking another
// − closes the previous.
let _activeRemoveConfirm = null;

function openRemoveConfirm(anchor, node, opts) {
  closeRemoveConfirm();
  const label = node.kind === 'leaf'
    ? `leaf ${node.metric}·${node.variable}`
    : combinatorLabel(node);
  const pop = document.createElement('span');
  pop.className = 'remove-confirm';
  pop.innerHTML = `
    <span class="remove-confirm-label">Remove ${_escHtml(label)}?</span>
    <button class="node-btn remove-confirm-yes">Confirm</button>
    <button class="node-btn remove-confirm-no">Cancel</button>
  `;
  anchor.insertAdjacentElement('afterend', pop);
  _activeRemoveConfirm = pop;

  const onOutside = (ev) => {
    if (!pop.contains(ev.target) && ev.target !== anchor) closeRemoveConfirm();
  };
  const onEsc = (ev) => { if (ev.key === 'Escape') closeRemoveConfirm(); };
  pop._cleanup = () => {
    document.removeEventListener('mousedown', onOutside, true);
    document.removeEventListener('keydown', onEsc);
  };
  setTimeout(() => {
    document.addEventListener('mousedown', onOutside, true);
    document.addEventListener('keydown', onEsc);
  }, 0);

  pop.querySelector('.remove-confirm-yes').addEventListener('click', (e) => {
    e.stopPropagation();
    closeRemoveConfirm();
    const parent = findWorkingNode(opts.parent.path);
    if (!parent || !parent.children) return;
    parent.children.splice(opts.indexInParent, 1);
    markStructureDirty();
  });
  pop.querySelector('.remove-confirm-no').addEventListener('click', (e) => {
    e.stopPropagation();
    closeRemoveConfirm();
  });
}

function closeRemoveConfirm() {
  if (!_activeRemoveConfirm) return;
  if (_activeRemoveConfirm._cleanup) _activeRemoveConfirm._cleanup();
  _activeRemoveConfirm.remove();
  _activeRemoveConfirm = null;
}

const VALID_METRICS = [
  'nrmse', 'tube', 'final-only', 'range', 'event-timing', 'dominant-frequency',
];

function promptAddLeaf(combinatorNode, presetVariable) {
  // Inline modal — two dropdowns (metric, variable) + OK / Cancel. The
  // variable select is pre-populated with the tracked variables on
  // this test; user can type a new name (via the "other..." option +
  // text input) to add a leaf referencing a not-yet-tracked variable.
  // ``presetVariable`` (from per-variable + button) pre-selects and
  // locks the variable dropdown so the new leaf stays scoped to the
  // plot the user clicked from.
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  const dataListId = 'add-leaf-var-options';
  overlay.innerHTML = `
    <div class="modal-dialog" role="dialog" aria-label="Add leaf">
      <h3>Add leaf</h3>
      <label class="mc-field"><span>Metric</span>
        <select id="add-leaf-metric">
          ${VALID_METRICS.map(m => `<option value="${m}">${m}</option>`).join('')}
        </select>
      </label>
      <label class="mc-field">
        <span>Variable (type to filter; * ? globs accepted)</span>
        <input id="add-leaf-variable" type="text" list="${dataListId}"
               placeholder="start typing a variable name" autocomplete="off">
        <datalist id="${dataListId}">
          ${VARIABLE_ORDER.map(v => `<option value="${_escHtml(v)}"></option>`).join('')}
        </datalist>
      </label>
      <div id="add-leaf-var-warning" class="var-warning"></div>
      <div class="modal-actions">
        <button class="btn" id="add-leaf-cancel">Cancel</button>
        <button class="btn btn-primary" id="add-leaf-ok">Add</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);

  const metricSel = overlay.querySelector('#add-leaf-metric');
  const varInp = overlay.querySelector('#add-leaf-variable');
  const warnEl = overlay.querySelector('#add-leaf-var-warning');
  const cancelBtn = overlay.querySelector('#add-leaf-cancel');
  const okBtn = overlay.querySelector('#add-leaf-ok');

  function isGlob(s) { return /[*?]/.test(s); }
  function checkVar() {
    const v = varInp.value.trim();
    if (!v || VARIABLE_ORDER.includes(v) || isGlob(v)) { warnEl.textContent = ''; return; }
    // Allow but warn — user may be adding a not-yet-tracked variable.
    warnEl.textContent = `Warning: '${v}' isn't tracked. Will be added anyway.`;
  }
  varInp.addEventListener('input', checkVar);

  if (presetVariable) {
    varInp.value = presetVariable;
    varInp.readOnly = true;
  }

  const close = () => overlay.remove();
  cancelBtn.addEventListener('click', close);
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) close();
  });
  document.addEventListener('keydown', function escListener(e) {
    if (e.key === 'Escape') { close(); document.removeEventListener('keydown', escListener); }
  });
  varInp.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); okBtn.click(); }
  });
  // Autofocus — user wants to type immediately.
  setTimeout(() => (presetVariable ? metricSel : varInp).focus(), 0);

  okBtn.addEventListener('click', () => {
    const metric = metricSel.value;
    const varname = varInp.value.trim();
    if (!varname) { varInp.focus(); return; }
    close();
    appendLeafToWorking(combinatorNode.path, metric, varname);
  });
}

function appendLeafToWorking(parentPath, metric, varname) {
  const node = findWorkingNode(parentPath);
  if (!node || !Array.isArray(node.children)) return;
  const schema = MODE_SCHEMAS[metric] || { fields: [] };
  // Seed params from the schema defaults so scorers + plot contributions
  // have real values to work with. Optional fields stay ``null`` (unset)
  // per schema semantics; scalars with a non-null default take it.
  const defaults = {};
  for (const f of (schema.fields || [])) {
    if (f.default !== null && f.default !== undefined) defaults[f.name] = f.default;
  }
  const bounds = _timeBoundsFor(varname);
  const leaf = {
    kind: 'leaf',
    metric, variable: varname,
    params: Object.assign({}, defaults),
    against: 'primary',
    window: {},
    children: [],
    name: varname,
    mode_effective: metric,
    mode_values: Object.assign({}, defaults),
    mode_controls_html: renderModeControlsHtmlJs(metric, varname, defaults),
    window_controls_html: renderWindowControlsHtmlJs(varname, {}, bounds),
    window_values: {},
    cli_authoritative: ['event-timing', 'dominant-frequency'].includes(metric),
  };
  node.children.push(leaf);
  // Rebuild paths first so the new leaf has its final path, then seed
  // leafState under that path — scorers + plot contributions lookup
  // ``leafState[path]`` and silently short-circuit when missing, which
  // was the cause of "added tube but plot doesn't update".
  rebuildPaths(WORKING_TREE, '/metrics');
  leafState[leaf.path] = {
    params: Object.assign({}, defaults),
    window: {},
    visible: true,
    original_params: Object.assign({}, defaults),
    original_window: {},
  };
  markStructureDirty();
}

function _timeBoundsFor(varname) {
  const traj = (VARIABLES_BY_NAME[varname] || {}).trajectory || {};
  const rt = traj.ref_time || traj.act_time || [];
  if (!rt.length) return {};
  return { start: rt[0], end: rt[rt.length - 1] };
}

// ---- JS-side schema → HTML renderers ----
// Mirror of ``render_schema_html`` + ``render_window_controls_html`` in
// mode_controls.py. Needed so newly-added leaves can build their control
// inputs without a server round-trip. Source of truth is still the Python
// schema (emitted into MODE_SCHEMAS at render time); this JS is a dumb
// walker.
function renderModeControlsHtmlJs(mode, variable, values) {
  const schema = MODE_SCHEMAS[mode];
  if (!schema) return '';
  const rows = (schema.fields || []).map(
    f => renderSchemaFieldJs(f, (values || {})[f.name] ?? f.default),
  );
  return `<div class="mode-controls" data-mode="${_escHtml(mode)}" `
       + `data-variable="${_escHtml(variable)}">${rows.join('')}</div>`;
}

function renderSchemaFieldJs(f, value) {
  const label = _escHtml(f.label || f.name);
  const name = _escHtml(f.name);
  const title = f.help ? ` title="${_escHtml(f.help)}"` : '';

  if (f.type === 'enum') {
    const options = [];
    if (f.optional) {
      const sel = value == null ? ' selected' : '';
      options.push(`<option value=""${sel}>(unset)</option>`);
    }
    for (const choice of (f.choices || [])) {
      const sel = String(value) === choice ? ' selected' : '';
      options.push(`<option value="${_escHtml(choice)}"${sel}>${_escHtml(choice)}</option>`);
    }
    return `<label class="mc-field mc-enum"${title}><span>${label}</span>`
         + `<select data-field="${name}">${options.join('')}</select></label>`;
  }
  if (f.type === 'bool') {
    const checked = value ? ' checked' : '';
    return `<label class="mc-field mc-bool"${title}>`
         + `<input type="checkbox" data-field="${name}"${checked}>`
         + `<span>${label}</span></label>`;
  }
  if (f.type === 'float' || f.type === 'int') {
    const step = f.type === 'float' ? 'any' : '1';
    const val = value == null ? '' : ` value="${_escHtml(String(value))}"`;
    return `<label class="mc-field mc-${f.type}"${title}><span>${label}</span>`
         + `<input type="number" step="${step}" data-field="${name}"${val}></label>`;
  }
  if (f.type === 'str') {
    const val = value == null ? '' : ` value="${_escHtml(String(value))}"`;
    return `<label class="mc-field mc-str"${title}><span>${label}</span>`
         + `<input type="text" data-field="${name}"${val}></label>`;
  }
  // passthrough — raw JSON textarea
  let raw = '';
  if (value != null) {
    try { raw = _escHtml(JSON.stringify(value)); } catch (_) { raw = ''; }
  }
  return `<label class="mc-field mc-passthrough"${title}><span>${label}</span>`
       + `<textarea data-field="${name}" data-passthrough="true" rows="2">${raw}</textarea></label>`;
}

function renderWindowControlsHtmlJs(variable, values, bounds) {
  values = values || {};
  bounds = bounds || {};
  const start = values.start;
  const end = values.end;
  const startAttr = start == null ? '' : ` value="${_escHtml(String(start))}"`;
  const endAttr = end == null ? '' : ` value="${_escHtml(String(end))}"`;
  const startPh = bounds.start != null ? ` placeholder="${_escHtml(String(bounds.start))}"` : '';
  const endPh = bounds.end != null ? ` placeholder="${_escHtml(String(bounds.end))}"` : '';
  return `<div class="window-controls" data-variable="${_escHtml(variable)}" `
       + `title="Restrict this leaf to a time window [start, end] before scoring">`
       + `<span class="wc-label">Window:</span>`
       + `<label class="wc-field"><span>start</span>`
       + `<input type="number" step="any" data-field="window_start"${startAttr}${startPh}></label>`
       + `<label class="wc-field"><span>end</span>`
       + `<input type="number" step="any" data-field="window_end"${endAttr}${endPh}></label>`
       + `</div>`;
}

function _escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
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
  // Stage-4 structural edits route through currentTree() — it returns
  // WORKING_TREE while structureDirty is set, so every reader picks up
  // the mutated tree transparently.
  renderAllNodeTrees();
  // Plots may gain or lose leaves — re-render affected variables.
  VARIABLE_ORDER.forEach((v, i) => renderVariablePlot(v, i));
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
