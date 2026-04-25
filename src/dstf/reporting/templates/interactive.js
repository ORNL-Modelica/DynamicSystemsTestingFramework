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
    // ``original_params`` mirrors the INITIAL state the user is editing
    // from (spec values merged with mode_values). buildPatchData diffs
    // params-vs-original to decide which ops to emit — if original only
    // held spec values and params had mode_values filled in, every
    // mode-defaulted field would show as a diff even when the user
    // hadn't touched anything. Pre-fix (D74/D75) this produced noisy
    // 5-op patches on untouched tube leaves; now the diff is quiet.
    const merged = Object.assign({}, leaf.params || {}, leaf.mode_values || {});
    leafState[leaf.path] = {
      params: Object.assign({}, merged),
      window: Object.assign({}, leaf.window || {}),
      visible: true,
      original_params: Object.assign({}, merged),
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
    const traj = (VARIABLES_BY_NAME[leaf.variable] || {}).trajectory || {};
    const { refTime, refValues, actTime, actValues } =
        _sliceLeafTrajectory(leaf, traj);
    if (refTime.length < 2) {
      // Not enough windowed points to compute a meaningful NRMSE —
      // fall through to the CLI value. Covers the case where the
      // user narrows the window below the sampling rate.
      return leaf.nrmse < tol;
    }
    // NRMSE = sqrt(mean((act - ref)^2)) / (max(ref) - min(ref)).
    // Interpolate act onto ref's time grid so we score on a shared
    // time axis (matches the CLI's signal-range normalization).
    let sq = 0;
    for (let i = 0; i < refTime.length; i++) {
      const aV = _interpLinear(actTime, actValues, refTime[i]);
      const d = aV - refValues[i];
      sq += d * d;
    }
    const rmse = Math.sqrt(sq / refTime.length);
    let refMin = refValues[0], refMax = refValues[0];
    for (const v of refValues) {
      if (v < refMin) refMin = v;
      if (v > refMax) refMax = v;
    }
    const range = refMax - refMin;
    // Zero-range (flat reference) → use RMSE directly, same convention
    // as the CLI's _compare_trajectories degenerate-signal handling.
    const nrmse = range > 0 ? rmse / range : rmse;
    return nrmse < tol;
  },
  'points': (leaf) => {
    const tol = _paramNumber(leaf, 'tolerance', leaf.tolerance_used);
    const traj = (VARIABLES_BY_NAME[leaf.variable] || {}).trajectory || {};
    const { refTime, refValues, actTime, actValues } =
        _sliceLeafTrajectory(leaf, traj);
    const params = (leafState[leaf.path] || {}).params || {};
    const points = Array.isArray(params.points) ? params.points : null;

    // Implicit final-only path: empty / null points → check act[-1] vs
    // ref[-1] with tolerance. Matches CLI's _compare_points fallback.
    if (!points || points.length === 0) {
      if (!refTime.length || !actTime.length) return !!leaf.passed;
      const refFinal = refValues[refValues.length - 1];
      const actFinal = actValues[actValues.length - 1];
      return Math.abs(actFinal - refFinal) < tol;
    }

    // Declared-points path. Same algorithm as _compare_points + box check.
    if (!actTime.length) return !!leaf.passed;
    const traceEnd = refTime.length ? refTime[refTime.length - 1]
                   : actTime[actTime.length - 1];
    let allMatched = true;
    for (const point of points) {
      let t = point.time;
      if (t == null) t = traceEnd;
      else t = Number(t);
      // Resolve target.
      let target;
      if (point.value != null) {
        target = Number(point.value);
      } else if (refTime.length) {
        target = _interpLinear(refTime, refValues, t);
      } else {
        // Ref-relative point with no ref data — skip.
        continue;
      }
      // Resolve y-tolerance + mode.
      const perTol = point.tolerance != null ? Number(point.tolerance) : tol;
      const mode = point.tolerance_mode || 'abs';
      const yLimit = mode === 'rel' ? perTol * Math.abs(target) : perTol;
      // Box check.
      const xTol = point.time_tolerance != null
        ? Number(point.time_tolerance) : 0;
      const tLo = Math.max(t - xTol, actTime[0]);
      const tHi = Math.min(t + xTol, actTime[actTime.length - 1]);
      if (tHi < tLo) continue;          // fully clipped
      const delta = _minDeltaInBox(actTime, actValues, tLo, tHi, target);
      if (delta > yLimit) {
        allMatched = false;
      }
    }
    return allMatched;
  },
  'range': (leaf) => {
    const p = (leafState[leaf.path] || {}).params || {};
    const traj = (VARIABLES_BY_NAME[leaf.variable] || {}).trajectory || {};
    const { actValues } = _sliceLeafTrajectory(leaf, traj);
    const mn = _nullOrNumber(p.min_value);
    const mx = _nullOrNumber(p.max_value);
    for (const v of actValues) {
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

    // Window-clip both ref and act before iterating. Act interp uses
    // the full-trajectory act arrays (so we interpolate onto windowed
    // refTime grid, but get correct values even if the nearest act
    // samples are just outside the window).
    const { refTime, refValues } = _sliceLeafTrajectory(leaf, traj);
    const actTimeFull = traj.act_time || [];
    const actValuesFull = traj.act_values || [];

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
      widthsUpper = refTime.map(t => Math.max(minW, interp(t, 'upper')));
      widthsLower = refTime.map(t => Math.max(minW, interp(t, 'lower')));
    } else {
      const w = refValues.map(v => {
        if (mode === 'rel') return Math.max(minW, rel * Math.abs(v));
        if (mode === 'band') return Math.max(minW, abs);
        return Math.max(minW, Math.max(abs, rel * Math.abs(v)));
      });
      widthsUpper = w;
      widthsLower = w;
    }

    for (let i = 0; i < refTime.length; i++) {
      const actV = _interpLinear(actTimeFull, actValuesFull, refTime[i]);
      if (actV > refValues[i] + widthsUpper[i]) return false;
      if (actV < refValues[i] - widthsLower[i]) return false;
    }
    return true;
  },
  // event-timing intentionally absent — CLI-authoritative (event pairing
  // algorithm stays Python-side).
  'dominant-frequency': (leaf) => {
    // Declared-peaks live scorer (D75+D76). Computes the actual signal's
    // spectrum LIVE via the ported FFT, scoped to the leaf's current
    // window, then checks each declared peak has a local-max in its
    // tolerance window. Live-recomputed on every state mutation — window
    // edits, tolerance edits, and peak additions all flip the pill
    // without waiting for a CLI rerun.
    const pks = ((leafState[leaf.path] || {}).params || {}).peaks || [];
    if (!pks.length) return false;
    const traj = (VARIABLES_BY_NAME[leaf.variable] || {}).trajectory || {};
    const at = traj.act_time || [];
    const av = traj.act_values || [];
    let freqs, mags;
    if (at.length >= 4) {
      const st = leafState[leaf.path] || {};
      const w = st.window || {};
      const sliced = _sliceToWindow(at, av, w.start, w.end);
      const spec = _computeFftSpectrum(sliced.time, sliced.values);
      freqs = spec.freqs;
      mags = spec.magnitudes;
    } else {
      // Fallback to CLI-embedded arrays (rare; e.g., spectrum-only tests
      // with no trajectory embed).
      const spec = leaf.spectrum || {};
      freqs = spec.act_freq || [];
      mags = spec.act_mag || [];
    }
    if (!freqs || freqs.length < 3) return !!leaf.passed;
    for (const pk of pks) {
      const f = Number(pk.freq);
      const tol = Number(pk.tolerance) || 0;
      if (!(f > 0)) return false;
      const [lo, hi] = pk.tolerance_mode === 'abs'
        ? [f - tol, f + tol]
        : [f * (1 - tol), f * (1 + tol)];
      if (!_findStrongestPeakInWindowJS(freqs, mags, lo, hi)) return false;
    }
    return true;
  },
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

function _minDeltaInBox(times, values, tLo, tHi, target) {
  // Mirrors comparator._min_delta_in_box: evaluates every sample inside
  // [tLo, tHi] plus the interpolated endpoints, AND scans adjacent pairs
  // for sign-flips of (v - target) — a piecewise-linear curve that
  // crosses target between two samples must register delta = 0 there
  // (matches Python comparator.py:307-322). Returns +Infinity when the
  // box is empty / outside the trajectory.
  if (!times.length || tHi < tLo) return Infinity;
  const n = times.length;
  const t0 = times[0];
  const tN = times[n - 1];
  // Build ordered (t, v) candidate list: interpolated endpoints (if
  // inside trajectory) + interior samples.
  const candidates = [];
  if (t0 <= tLo && tLo <= tN) {
    candidates.push([tLo, _interpLinear(times, values, tLo)]);
  }
  for (let i = 0; i < n; i++) {
    if (times[i] >= tLo && times[i] <= tHi) {
      candidates.push([times[i], values[i]]);
    }
  }
  if (t0 <= tHi && tHi <= tN) {
    candidates.push([tHi, _interpLinear(times, values, tHi)]);
  }
  if (!candidates.length) return Infinity;
  candidates.sort((a, b) => a[0] - b[0]);
  let best = Infinity;
  for (const [, v] of candidates) {
    const d = Math.abs(v - target);
    if (d < best) best = d;
  }
  // Zero-crossing scan on adjacent linear segments.
  for (let i = 0; i < candidates.length - 1; i++) {
    const d1 = candidates[i][1] - target;
    const d2 = candidates[i + 1][1] - target;
    if (d1 === 0 || d2 === 0) continue;  // endpoint already captured
    if ((d1 > 0) !== (d2 > 0)) {
      // Sign flip → curve passes through target → delta = 0 somewhere
      // inside the segment.
      return 0;
    }
  }
  return best;
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
  'points': (leaf, traj) => {
    const params = (leafState[leaf.path] || {}).params || {};
    const tolDefault = Number(params.tolerance) || 0;
    const points = Array.isArray(params.points) ? params.points : null;
    if (!points || points.length === 0) {
      // Implicit final case — no plot contribution. The implicit
      // check just compares the final value; nothing useful to draw.
      return { traces: [], shapes: [] };
    }
    const refTime = traj.ref_time || [];
    const refValues = traj.ref_values || [];
    const actTime = traj.act_time || [];
    const traceEnd = refTime.length ? refTime[refTime.length - 1]
                   : (actTime.length ? actTime[actTime.length - 1] : 0);

    const xs = [];
    const ys = [];
    const shapes = [];
    for (const point of points) {
      let t = point.time;
      if (t == null) t = traceEnd;
      else t = Number(t);
      // Resolve target.
      let target;
      if (point.value != null) {
        target = Number(point.value);
      } else if (refTime.length) {
        target = _interpLinear(refTime, refValues, t);
      } else {
        continue;     // ref-relative without ref data — skip
      }
      // y-limit (resolved absolute size of the band).
      const perTol = point.tolerance != null ? Number(point.tolerance) : tolDefault;
      const mode = point.tolerance_mode || 'abs';
      const yLimit = mode === 'rel' ? perTol * Math.abs(target) : perTol;
      const xTol = point.time_tolerance != null
        ? Number(point.time_tolerance) : 0;
      // Marker.
      xs.push(t);
      ys.push(target);
      // Translucent rectangle. Width = 2 * xTol (zero when xTol=0 →
      // visually a vertical line segment thanks to Plotly drawing
      // zero-width rects as a single line stroke).
      shapes.push({
        type: 'rect', xref: 'x', yref: 'y',
        x0: t - xTol, x1: t + xTol,
        y0: target - yLimit, y1: target + yLimit,
        fillcolor: 'rgba(76,175,80,0.10)',
        line: { color: 'rgba(76,175,80,0.6)', width: 1, dash: 'dot' },
        name: `points_box:${leaf.path}:${xs.length - 1}`,
      });
    }
    if (!xs.length) return { traces: [], shapes };
    const traces = [{
      x: xs, y: ys, mode: 'markers', type: 'scatter',
      name: `Points ${leaf.path}`,
      marker: {
        color: '#2e7d32', size: 12, symbol: 'diamond',
        line: { color: 'white', width: 1.5 },
      },
      hoverinfo: 'x+y', showlegend: true,
    }];
    return { traces, shapes };
  },
  'range': (leaf, traj) => {
    const p = leafState[leaf.path] ? leafState[leaf.path].params : {};
    const state = leafState[leaf.path] || {};
    const w = state.window || {};
    const hasWindow = (w.start != null && w.start !== ''
                     && w.end != null && w.end !== '');

    // Build one bound's shapes — returns an array of 1 or 3 line shapes.
    // When no window: a single full-width (paper-coord) red dashed line.
    // When windowed: three segments in data coords — gray pre-window,
    // red in-window, gray post-window. Named shapes let
    // MODE_PLOT_EDITORS['range'] match plotly_relayout drag events back
    // to the right params field by path — only the in-window RED segment
    // is draggable (so users interact with the authoritative segment).
    const buildBoundShapes = (yVal, nameSuffix) => {
      if (!Number.isFinite(yVal)) return [];
      if (!hasWindow) {
        return [{
          type: 'line', xref: 'paper', x0: 0, x1: 1, yref: 'y',
          y0: yVal, y1: yVal,
          line: { color: '#f44336', width: 1.5, dash: 'dash' },
          name: `range_${nameSuffix}:${leaf.path}`,
        }];
      }
      // Window-aware: three segments, all in data coords so they sit
      // under the right regions of the x axis.
      const refTime = traj.ref_time || traj.act_time || [];
      const tStart = refTime.length ? refTime[0] : 0;
      const tEnd = refTime.length ? refTime[refTime.length - 1] : 1;
      const wStart = Number(w.start);
      const wEnd = Number(w.end);
      return [
        // Pre-window gray segment.
        {
          type: 'line', xref: 'x', x0: tStart, x1: wStart, yref: 'y',
          y0: yVal, y1: yVal,
          line: { color: '#9e9e9e', width: 1.5, dash: 'dash' },
          name: `range_${nameSuffix}_pre:${leaf.path}`,
        },
        // In-window red segment — the authoritative one.
        {
          type: 'line', xref: 'x', x0: wStart, x1: wEnd, yref: 'y',
          y0: yVal, y1: yVal,
          line: { color: '#f44336', width: 1.5, dash: 'dash' },
          name: `range_${nameSuffix}:${leaf.path}`,
        },
        // Post-window gray segment.
        {
          type: 'line', xref: 'x', x0: wEnd, x1: tEnd, yref: 'y',
          y0: yVal, y1: yVal,
          line: { color: '#9e9e9e', width: 1.5, dash: 'dash' },
          name: `range_${nameSuffix}_post:${leaf.path}`,
        },
      ];
    };

    const shapes = [];
    const mn = _nullOrNumber(p.min_value);
    const mx = _nullOrNumber(p.max_value);
    if (mn !== null) shapes.push(...buildBoundShapes(mn, 'min'));
    if (mx !== null) shapes.push(...buildBoundShapes(mx, 'max'));
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
  'event-timing': (leaf, traj) => {
    // Overlay reference + actual event instants as vertical lines, with a
    // tolerance band around each reference event. Event detection JS-side
    // uses the Modelica duplicate-time-sample convention — two samples at
    // the same t flag a solver event. CLI remains authoritative for pass/
    // fail (event pairing is non-trivial and stays Python-side); this
    // contribution is purely visual.
    const refEvents = _detectEvents(traj.ref_time || []);
    const actEvents = _detectEvents(traj.act_time || []);
    const tol = Number(((leafState[leaf.path] || {}).params || {}).time_tolerance) || 0;
    const shapes = [];
    for (const t of refEvents) {
      if (tol > 0) {
        // Tolerance band first so the vertical line draws on top.
        shapes.push({
          type: 'rect', xref: 'x', yref: 'paper',
          x0: t - tol, x1: t + tol, y0: 0, y1: 1,
          line: { width: 0 },
          fillcolor: 'rgba(117,117,117,0.09)',
        });
      }
      shapes.push({
        type: 'line', xref: 'x', yref: 'paper',
        x0: t, x1: t, y0: 0, y1: 1,
        line: { color: '#757575', width: 1, dash: 'dash' },
        name: `ref_event:${leaf.path}`,
      });
    }
    for (const t of actEvents) {
      shapes.push({
        type: 'line', xref: 'x', yref: 'paper',
        x0: t, x1: t, y0: 0, y1: 1,
        line: { color: '#1976D2', width: 1.5 },
        name: `act_event:${leaf.path}`,
      });
    }
    return { traces: [], shapes };
  },
  'dominant-frequency': () => ({ traces: [], shapes: [] }),
};

// ---- JS-side FFT + peak detection (D76) ----
// Pure-JS port of _compute_fft_spectrum / _find_top_n_peaks /
// _find_strongest_peak_in_window from comparator.py. Lets the reporter:
//   * Recompute the spectrum subplot live when the leaf's window changes.
//   * Offer "Detect peaks" over either reference OR actual, scoped to the
//     current window, without a CLI round-trip.
// Algorithm is radix-2 Cooley-Tukey with zero-padding to the next power
// of 2. The Python side uses arbitrary-N numpy.fft.rfft — we zero-pad
// instead to keep the implementation minimal. Peak LOCATIONS match
// within one bin; magnitudes may differ in absolute scale (sinc
// interpolation from padding).

function _fftRadix2(real, imag) {
  // In-place iterative Cooley-Tukey. Requires length to be a power of 2.
  const n = real.length;
  // Bit-reversal permutation.
  let j = 0;
  for (let i = 1; i < n; i++) {
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      let tmp = real[i]; real[i] = real[j]; real[j] = tmp;
      tmp = imag[i]; imag[i] = imag[j]; imag[j] = tmp;
    }
  }
  // Butterfly.
  for (let size = 2; size <= n; size <<= 1) {
    const half = size >> 1;
    const angleStep = -2 * Math.PI / size;
    for (let start = 0; start < n; start += size) {
      for (let k = 0; k < half; k++) {
        const theta = k * angleStep;
        const wr = Math.cos(theta);
        const wi = Math.sin(theta);
        const i1 = start + k;
        const i2 = i1 + half;
        const tr = wr * real[i2] - wi * imag[i2];
        const ti = wr * imag[i2] + wi * real[i2];
        real[i2] = real[i1] - tr;
        imag[i2] = imag[i1] - ti;
        real[i1] = real[i1] + tr;
        imag[i1] = imag[i1] + ti;
      }
    }
  }
}

function _dedupeMonotonicTimes(time, values) {
  // Drop consecutive samples at identical t (Modelica events) — np.unique
  // equivalent. Input assumed already monotonic-non-decreasing.
  const uniqT = [], uniqV = [];
  let last = NaN;
  for (let i = 0; i < time.length; i++) {
    if (time[i] !== last) {
      uniqT.push(time[i]);
      uniqV.push(values[i]);
      last = time[i];
    }
  }
  return { uniqT, uniqV };
}

function _sliceToWindow(time, values, winStart, winEnd) {
  // Restrict the signal to [winStart, winEnd], both optional. Null / undef
  // endpoints mean unbounded on that side.
  if ((winStart == null || winStart === '') && (winEnd == null || winEnd === '')) {
    return { time, values };
  }
  const lo = winStart == null || winStart === '' ? -Infinity : Number(winStart);
  const hi = winEnd == null || winEnd === '' ? Infinity : Number(winEnd);
  const outT = [], outV = [];
  for (let i = 0; i < time.length; i++) {
    if (time[i] >= lo && time[i] <= hi) {
      outT.push(time[i]);
      outV.push(values[i]);
    }
  }
  return { time: outT, values: outV };
}

function _sliceLeafTrajectory(leaf, traj) {
  // Read the leaf's current window (from leafState) and clip every
  // trajectory array to it. Returns {refTime, refValues, actTime,
  // actValues} — same shape as the raw trajectory, just windowed.
  //
  // Used by every MODE_SCORERS / MODE_PLOT_CONTRIBUTIONS entry that
  // needs window-awareness. Centralizing here so bugs in window
  // handling get fixed in ONE place, not six.
  //
  // If no window is set (both endpoints null/unset), returns the
  // trajectory unchanged — zero-cost fast path.
  const state = leafState[leaf.path] || {};
  const w = state.window || {};
  const s = w.start, e = w.end;
  const refTime = traj.ref_time || [];
  const refValues = traj.ref_values || [];
  const actTime = traj.act_time || [];
  const actValues = traj.act_values || [];
  const refSliced = _sliceToWindow(refTime, refValues, s, e);
  const actSliced = _sliceToWindow(actTime, actValues, s, e);
  return {
    refTime: refSliced.time,
    refValues: refSliced.values,
    actTime: actSliced.time,
    actValues: actSliced.values,
  };
}

function _computeFftSpectrum(time, values) {
  // Mirrors Python _compute_fft_spectrum: dedupe, uniform resample to the
  // next power of 2 above max(n, 64) points, detrend, radix-2 FFT.
  // Resampling at nPad points (rather than sampling n then zero-padding
  // to nPad) is what guarantees bit-identical bin frequencies across
  // Python and JS — without that, Python computes paired_peaks on one
  // bin grid and the JS live scorer on another, and the two disagree
  // on self-regression when the declared tolerance is tight.
  if (!time || time.length < 4 || !values || values.length < 4) {
    return { freqs: [], magnitudes: [] };
  }
  const { uniqT, uniqV } = _dedupeMonotonicTimes(time, values);
  if (uniqT.length < 4) return { freqs: [], magnitudes: [] };

  const n0 = Math.max(time.length, 64);
  const nPad = 1 << Math.ceil(Math.log2(n0));
  const t0 = uniqT[0];
  const tN = uniqT[uniqT.length - 1];
  if (tN <= t0) return { freqs: [], magnitudes: [] };
  const dt = (tN - t0) / (nPad - 1);
  if (!(dt > 0)) return { freqs: [], magnitudes: [] };

  const real = new Float64Array(nPad);
  const imag = new Float64Array(nPad);

  // Linear-interp resample to uniform grid of nPad points over [t0, tN].
  let j = 1;
  let mean = 0;
  for (let i = 0; i < nPad; i++) {
    const t = t0 + i * dt;
    while (j < uniqT.length && uniqT[j] < t) j++;
    let v;
    if (j <= 0) v = uniqV[0];
    else if (j >= uniqT.length) v = uniqV[uniqV.length - 1];
    else {
      const t0s = uniqT[j - 1], t1s = uniqT[j];
      if (t <= t0s) v = uniqV[j - 1];
      else if (t >= t1s) v = uniqV[j];
      else {
        const f = (t - t0s) / (t1s - t0s);
        v = uniqV[j - 1] + f * (uniqV[j] - uniqV[j - 1]);
      }
    }
    real[i] = v;
    mean += v;
  }
  mean /= nPad;
  for (let i = 0; i < nPad; i++) real[i] -= mean;

  _fftRadix2(real, imag);

  const nHalf = (nPad >> 1) + 1;
  const freqs = new Array(nHalf);
  const magnitudes = new Array(nHalf);
  const binHz = 1 / (nPad * dt);
  for (let i = 0; i < nHalf; i++) {
    freqs[i] = i * binHz;
    magnitudes[i] = Math.hypot(real[i], imag[i]);
  }
  return { freqs, magnitudes };
}

function _findTopNPeaksJS(freqs, spectrum, nPeaks, minFrequency = 0) {
  // Mirrors Python _find_top_n_peaks. Returns [{freq, amplitude}, ...]
  // sorted by frequency ascending (not amplitude).
  if (!freqs || freqs.length < 3 || nPeaks < 1) return [];
  const floor = Math.max(minFrequency || 0, 0);
  const peaks = [];
  for (let i = 1; i < freqs.length - 1; i++) {
    if (freqs[i] <= floor) continue;
    if (spectrum[i] > spectrum[i - 1] && spectrum[i] > spectrum[i + 1]) {
      peaks.push({ idx: i, freq: freqs[i], amp: spectrum[i] });
    }
  }
  if (peaks.length === 0) return [];
  peaks.sort((a, b) => b.amp - a.amp);
  const top = peaks.slice(0, nPeaks);
  top.sort((a, b) => a.freq - b.freq);
  return top.map(p => ({ freq: p.freq, amplitude: p.amp }));
}

function _findStrongestPeakInWindowJS(freqs, spectrum, lo, hi) {
  // Mirrors Python _find_strongest_peak_in_window. Returns {freq, amplitude}
  // of the strongest local max in [lo, hi], or null if none.
  if (!freqs || freqs.length < 3) return null;
  let best = null;
  for (let i = 1; i < freqs.length - 1; i++) {
    if (freqs[i] < lo || freqs[i] > hi) continue;
    if (spectrum[i] > spectrum[i - 1] && spectrum[i] > spectrum[i + 1]) {
      if (best === null || spectrum[i] > best.amplitude) {
        best = { freq: freqs[i], amplitude: spectrum[i] };
      }
    }
  }
  return best;
}

function _detectEvents(time) {
  // Modelica events manifest as consecutive samples at the same ``t`` — one
  // pre-event, one post-event. Return a de-duplicated list of event times.
  const out = [];
  if (!time || time.length < 2) return out;
  for (let i = 1; i < time.length; i++) {
    if (time[i] === time[i - 1]) out.push(time[i]);
  }
  return out;
}

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
// ---- createPointPlotEditor — shared Shift-modifier interaction layer ----
// Extracted from tube editor; also used by dominant-frequency's declared-
// peaks spectrum editor. Any future mode that needs "user-draggable
// points on a Plotly plot" should reuse this rather than duplicate the
// mousedown/move/up/contextmenu wiring + px→data math + drag-reference
// tracking. The consumer only has to describe the point model via
// callbacks; the factory owns interaction plumbing.
//
// Design choices worth noting:
//   * Anchors (not points) — the consumer returns ``[{pt, x, y}]`` from
//     ``getAnchors``. A single point can contribute multiple anchors (tube
//     has upper + lower bounds per point). ``pt`` is the stable reference
//     the consumer mutates; ``x``/``y`` are the current resolved plot-
//     space coordinates used for hit testing.
//   * Drag by pt identity — the drag state tracks ``anchor.pt``, not an
//     index. Consumers can re-order their internal arrays mid-drag
//     (tube sorts by time on every frame) and the factory keeps working
//     because it asks for anchors fresh each frame.
//   * RAF throttling — onDragStep fires on every mousemove but the
//     consumer's ``commit()`` is usually expensive (re-render plot +
//     re-evaluate pass/fail). The factory batches into requestAnimation
//     Frame so fast-dragging doesn't drown the main thread.
function createPointPlotEditor(spec) {
  const {
    getAnchors,          // (leaf) => [{pt, x, y, bound?, label?}]
    onClickAdd,          // (leaf, plotEl, x, y, commit) => void
    onDragStep,          // (leaf, plotEl, anchor, x, y, commit) => void
    onDragEnd,           // (leaf, plotEl, anchor, commit) => void
    onRemove,            // (leaf, plotEl, anchor, commit) => void
    hitRadiusPx = 15,    // max click-distance-in-pixels to count as "on" an anchor
  } = spec;

  const wired = new WeakMap();

  function _pxToData(plotEl, evt) {
    const xa = plotEl._fullLayout?.xaxis;
    const ya = plotEl._fullLayout?.yaxis;
    if (!xa || !ya) return null;
    const area = plotEl.querySelector('.nsewdrag') || plotEl;
    const rect = area.getBoundingClientRect();
    return { x: xa.p2d(evt.clientX - rect.left), y: ya.p2d(evt.clientY - rect.top) };
  }

  function _findNearestAnchor(plotEl, leaf, dataX, dataY) {
    const xa = plotEl._fullLayout?.xaxis;
    const ya = plotEl._fullLayout?.yaxis;
    if (!xa || !ya) return null;
    const anchors = getAnchors(leaf) || [];
    let best = null;
    for (const a of anchors) {
      const dx = xa.d2p(a.x) - xa.d2p(dataX);
      const dy = ya.d2p(a.y) - ya.d2p(dataY);
      const dist = Math.hypot(dx, dy);
      if (!best || dist < best.dist) best = { anchor: a, dist };
    }
    return best;
  }

  return {
    attach(leaf, plotEl, commit) {
      const drag = { active: false, anchor: null, clickStart: null };
      let rafPending = null;

      function onMouseDown(evt) {
        if (!evt.shiftKey || evt.button !== 0) return;
        evt.stopPropagation();
        evt.stopImmediatePropagation();
        evt.preventDefault();
        const d = _pxToData(plotEl, evt);
        if (!d) return;
        const nearest = _findNearestAnchor(plotEl, leaf, d.x, d.y);
        if (nearest && nearest.dist < hitRadiusPx) {
          drag.active = true;
          drag.anchor = nearest.anchor;
        } else {
          drag.clickStart = d;
        }
      }

      function onMouseMove(evt) {
        if (drag.active) {
          evt.preventDefault();
          const d = _pxToData(plotEl, evt);
          if (!d || !drag.anchor) return;
          onDragStep(leaf, plotEl, drag.anchor, d.x, d.y, commit);
          // Coalesce rapid mousemoves into one RAF so commit() (which
          // re-renders Plotly) doesn't run per pixel.
          if (!rafPending) {
            rafPending = requestAnimationFrame(() => {
              commit();
              rafPending = null;
            });
          }
        } else if (drag.clickStart) {
          const d = _pxToData(plotEl, evt);
          if (!d) return;
          if (Math.abs(d.x - drag.clickStart.x) > 1e-10 ||
              Math.abs(d.y - drag.clickStart.y) > 1e-10) drag.clickStart = null;
        }
      }

      function onMouseUp() {
        if (drag.active) {
          const anchor = drag.anchor;
          drag.active = false; drag.anchor = null;
          if (onDragEnd) onDragEnd(leaf, plotEl, anchor, commit);
          commit();
          return;
        }
        if (drag.clickStart) {
          const c = drag.clickStart;
          drag.clickStart = null;
          if (onClickAdd) onClickAdd(leaf, plotEl, c.x, c.y, commit);
        }
      }

      function onContextMenu(evt) {
        if (!evt.shiftKey) return;
        const d = _pxToData(plotEl, evt);
        if (!d) return;
        const nearest = _findNearestAnchor(plotEl, leaf, d.x, d.y);
        if (nearest && nearest.dist < hitRadiusPx) {
          evt.preventDefault();
          if (onRemove) onRemove(leaf, plotEl, nearest.anchor, commit);
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
    },

    detach(plotEl) {
      const cleanup = wired.get(plotEl);
      if (cleanup) { cleanup(); wired.delete(plotEl); }
    },
  };
}

MODE_PLOT_EDITORS['tube'] = (function() {
  const editorState = {};   // leafPath → {synced, perPointModes}

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
  // ``resolvePoint`` returns the absolute (y-axis) bounds AT A SINGLE
  // CONTROL POINT'S time — useful for the draggable-marker visual. The
  // CONTOUR of the tube envelope across the full grid is NOT this
  // function's responsibility; see ``resolveAllBoundsOnGrid`` for that
  // (it applies the width-then-combine-with-ref(t) semantic that matches
  // Python's ``_compare_tube``).
  function resolvePoint(leaf, pt, _ptIdx) {
    const mode = getWidthMode(leaf);
    const rv = refValueAt(leaf, pt.time);
    const rvAbs = Math.abs(rv);
    const minW = getMinWidth(leaf);

    let upper, lower;
    if (mode === 'absolute') {
      upper = Number(pt.upper);
      lower = Number(pt.lower);
    } else if (mode === 'rel') {
      let uw = Number(pt.upper) * rvAbs;
      let lw = Number(pt.lower) * rvAbs;
      if (minW > 0) { uw = Math.max(uw, minW); lw = Math.max(lw, minW); }
      upper = rv + uw;
      lower = rv - lw;
    } else {
      let uw = Number(pt.upper);
      let lw = Number(pt.lower);
      if (minW > 0) { uw = Math.max(uw, minW); lw = Math.max(lw, minW); }
      upper = rv + uw;
      lower = rv - lw;
    }
    return { upper, lower, uMode: mode, lMode: mode };
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
  // Mirrors Python's ``_compare_tube`` + ``_interpolate_tube_widths``:
  // interpolate the RAW width values (what the user authored as
  // ``pt.upper`` / ``pt.lower``) across the grid, THEN at each grid point
  // combine with the ref(t) value there under the current width-mode.
  // Previously this function resolved each control point to its absolute
  // y-bound and then linearly interpolated those absolute bounds — which
  // gave a straight-line envelope between endpoints even when the signal
  // was curvy. CLI's tube scoring uses the width-first approach, so the
  // polygon visual + control-point markers now match what the CLI scores.
  function resolveAllBoundsOnGrid(leaf, grid) {
    const pts = [...getPoints(leaf)].sort((a, b) => a.time - b.time);
    if (pts.length === 0) {
      return { upper: [], lower: [], ctrlTimes: [], resUpper: [], resLower: [] };
    }
    const ctrlTimes = pts.map(p => p.time);
    const rawUpperCtrl = pts.map(p => Number(p.upper));
    const rawLowerCtrl = pts.map(p => Number(p.lower));
    const fn = getInterpolation(leaf) === 'constant' ? _interpStep : _interpLinear;
    // Interpolate RAW widths / bounds on the grid first.
    const rawUpperGrid = grid.map(t => fn(ctrlTimes, rawUpperCtrl, t));
    const rawLowerGrid = grid.map(t => fn(ctrlTimes, rawLowerCtrl, t));

    const mode = getWidthMode(leaf);
    const minW = getMinWidth(leaf);
    const traj = (VARIABLES_BY_NAME[leaf.variable] || {}).trajectory || {};
    const rt = traj.ref_time || traj.act_time || [];
    const rv = traj.ref_values || traj.act_values || [];

    // Per-grid-point ref value so rel mode tracks the curve.
    const refAt = grid.map(t => (rt.length ? _interpLinear(rt, rv, t) : 0));

    const upper = grid.map((t, i) => {
      if (mode === 'absolute') return rawUpperGrid[i];
      let w = mode === 'rel'
        ? rawUpperGrid[i] * Math.abs(refAt[i])
        : rawUpperGrid[i];  // 'band'
      if (minW > 0) w = Math.max(w, minW);
      return refAt[i] + w;
    });
    const lower = grid.map((t, i) => {
      if (mode === 'absolute') return rawLowerGrid[i];
      let w = mode === 'rel'
        ? rawLowerGrid[i] * Math.abs(refAt[i])
        : rawLowerGrid[i];
      if (minW > 0) w = Math.max(w, minW);
      return refAt[i] - w;
    });

    // Marker-placement bounds (same semantic, evaluated at each control
    // point's time rather than on the render grid).
    const resUpper = pts.map(p => resolvePoint(leaf, p, 0).upper);
    const resLower = pts.map(p => resolvePoint(leaf, p, 0).lower);

    return { upper, lower, ctrlTimes, resUpper, resLower };
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

  // ---- plot interactions (delegated to shared PointPlotEditor) -------
  // Tube-specific hooks translate the generic "anchor" model to
  // (pt, bound) pairs. A point contributes two anchors (upper + lower);
  // drag mutates pt.time (x) plus the side (y) via mode-aware projection.
  const _pointEditor = createPointPlotEditor({
    getAnchors(leaf) {
      return getPoints(leaf).flatMap((pt, i) => {
        const r = resolvePoint(leaf, pt, i);
        return [
          { pt, bound: 'upper', x: pt.time, y: r.upper },
          { pt, bound: 'lower', x: pt.time, y: r.lower },
        ];
      });
    },
    onClickAdd(leaf, _plotEl, x, y, commit) {
      addPointAt(leaf, x, y, commit);
    },
    onDragStep(leaf, _plotEl, anchor, x, y, _commit) {
      // Mutate the tracked pt in place — no setPoints clone, so
      // anchor.pt stays a live reference across the sort.
      const traj = (VARIABLES_BY_NAME[leaf.variable] || {}).trajectory || {};
      const rt = traj.ref_time || traj.act_time || [];
      const tMin = rt[0], tMax = rt[rt.length - 1];
      anchor.pt.time = roundSig(Math.max(tMin, Math.min(tMax, x)), 6);
      const ptIdx = getPoints(leaf).indexOf(anchor.pt);
      if (ptIdx >= 0) setBoundFromAbsoluteY(leaf, ptIdx, anchor.bound, y);
      // Sort during drag so a crossing point smoothly swaps; the pt
      // reference follows its new index.
      sortPointsAndModes(leaf);
      refreshTables(leaf, _commit);
    },
    onDragEnd(leaf, _plotEl, _anchor, commit) {
      sortPointsAndModes(leaf);
      refreshTables(leaf, commit);
    },
    onRemove(leaf, _plotEl, anchor, commit) {
      const idx = getPoints(leaf).indexOf(anchor.pt);
      if (idx >= 0) removePointAt(leaf, idx, commit);
    },
  });

  function attachPlotHandlers(leaf, plotEl, commit) {
    _pointEditor.attach(leaf, plotEl, commit);
  }
  function detachPlotHandlers(plotEl) {
    _pointEditor.detach(plotEl);
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
    deactivate(_leaf, plotEl) {
      detachPlotHandlers(plotEl);
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

// Event-timing editor — declared-events table in the leaf slot.
// Event-timing is CLI-authoritative for pass/fail (event pairing is
// non-trivial and lives in Python). The editor lets users AUTHOR the
// declared-events list: add rows, edit time + tolerance, delete rows,
// and (Task 4) auto-detect from the reference or actual signals.
// The table UI mirrors dominant-frequency's declared-peaks editor
// pattern; we don't share code with dom-frequency yet — if the overlap
// ends up >=70% after both ship, a shared helper can be extracted as
// a follow-up.
MODE_PLOT_EDITORS['event-timing'] = (function() {

  // --- state helpers -----------------------------------------------------
  function getEvents(leaf) {
    const st = leafState[leaf.path] || {};
    const p = st.params || (st.params = {});
    if (!Array.isArray(p.events)) p.events = [];
    return p.events;
  }

  function getGlobalTolerance(leaf) {
    const st = leafState[leaf.path] || {};
    const v = Number((st.params || {}).time_tolerance);
    return Number.isFinite(v) && v > 0 ? v : 1e-3;
  }

  function getDetectSource(leaf) {
    const st = leafState[leaf.path] || {};
    return st.event_detect_source === 'act' ? 'act' : 'ref';
  }

  function setDetectSource(leaf, src) {
    const st = leafState[leaf.path] || (leafState[leaf.path] = {});
    st.event_detect_source = src === 'act' ? 'act' : 'ref';
  }

  function getTrajectory(leaf) {
    return (VARIABLES_BY_NAME[leaf.variable] || {}).trajectory || {};
  }

  // Evaluate live match status for each declared event against the
  // ACTUAL signal's auto-detected events. Does NOT substitute for CLI
  // pass/fail — this is live feedback only. Same pairing rule as the
  // Python scorer: nearest unclaimed actual event within the declared
  // tolerance wins.
  function evaluateLiveMatches(leaf) {
    const traj = getTrajectory(leaf);
    const actEvents = _detectEvents(traj.act_time || []);
    const evs = getEvents(leaf);
    const globalTol = getGlobalTolerance(leaf);
    const claimed = new Array(actEvents.length).fill(false);
    return evs.map(ev => {
      const target = Number(ev.time);
      const tol = ev.tolerance != null ? Number(ev.tolerance) : globalTol;
      let bestIdx = -1;
      let bestD = Infinity;
      for (let j = 0; j < actEvents.length; j++) {
        if (claimed[j]) continue;
        const d = Math.abs(actEvents[j] - target);
        if (d <= tol && d < bestD) {
          bestD = d;
          bestIdx = j;
        }
      }
      if (bestIdx < 0) return { matched: false, delta: null, at: null };
      claimed[bestIdx] = true;
      return { matched: true, delta: bestD, at: actEvents[bestIdx] };
    });
  }

  function sortEvents(leaf) {
    const evs = getEvents(leaf);
    evs.sort((a, b) => Number(a.time) - Number(b.time));
  }

  // --- table rendering ---------------------------------------------------
  const mountedByLeaf = new WeakMap();

  function mount(container, leaf, commit) {
    const root = document.createElement('div');
    root.className = 'event-timing-editor';
    container.appendChild(root);
    mountedByLeaf.set(leaf, { root });
    refreshEditor(leaf, commit);
  }

  function unmount(container) {
    const el = container.querySelector('.event-timing-editor');
    if (el) el.remove();
  }

  function refreshEditor(leaf, commit) {
    const m = mountedByLeaf.get(leaf);
    if (!m) return;
    renderTable(m.root, leaf, commit);
  }

  function renderTable(root, leaf, commit) {
    const evs = getEvents(leaf);
    const globalTol = getGlobalTolerance(leaf);
    const matches = evaluateLiveMatches(leaf);

    root.innerHTML = '';

    // Table.
    const table = document.createElement('table');
    table.className = 'event-table';
    const thead = document.createElement('thead');
    thead.innerHTML = (
      '<tr>'
      + '<th>Time (s)</th>'
      + '<th>Tolerance (s)</th>'
      + '<th>Match (live)</th>'
      + '<th></th>'
      + '</tr>'
    );
    table.appendChild(thead);
    const tbody = document.createElement('tbody');
    evs.forEach((ev, i) => {
      const tr = document.createElement('tr');
      // Time input.
      const timeTd = document.createElement('td');
      const timeInput = document.createElement('input');
      timeInput.type = 'number';
      timeInput.step = 'any';
      timeInput.value = ev.time != null ? String(ev.time) : '';
      timeInput.addEventListener('change', () => {
        const n = Number(timeInput.value);
        if (Number.isFinite(n)) ev.time = n;
        sortEvents(leaf);
        refreshEditor(leaf, commit);
        commit();
      });
      timeTd.appendChild(timeInput);
      tr.appendChild(timeTd);
      // Tolerance input.
      const tolTd = document.createElement('td');
      const tolInput = document.createElement('input');
      tolInput.type = 'number';
      tolInput.step = 'any';
      tolInput.placeholder = String(globalTol);
      tolInput.value = ev.tolerance != null ? String(ev.tolerance) : '';
      tolInput.addEventListener('change', () => {
        const raw = tolInput.value.trim();
        if (raw === '') {
          delete ev.tolerance;
        } else {
          const n = Number(raw);
          if (Number.isFinite(n) && n > 0) ev.tolerance = n;
        }
        refreshEditor(leaf, commit);
        commit();
      });
      tolTd.appendChild(tolInput);
      tr.appendChild(tolTd);
      // Match column (live).
      const matchTd = document.createElement('td');
      matchTd.className = 'match-cell';
      const m = matches[i];
      if (m.matched) {
        matchTd.innerHTML = (
          '<span style="color:#2e7d32">✓ matched</span> '
          + `@ t=${m.at.toPrecision(4)} (Δ=${m.delta.toPrecision(2)})`
        );
      } else {
        matchTd.innerHTML = (
          '<span style="color:#c62828">✕ unmatched</span>'
        );
      }
      tr.appendChild(matchTd);
      // Delete button.
      const delTd = document.createElement('td');
      const delBtn = document.createElement('button');
      delBtn.className = 'row-delete';
      delBtn.textContent = '✕';
      delBtn.title = 'Remove this event';
      delBtn.addEventListener('click', () => {
        evs.splice(i, 1);
        refreshEditor(leaf, commit);
        commit();
      });
      delTd.appendChild(delBtn);
      tr.appendChild(delTd);
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    root.appendChild(table);

    // Button row: add + (new) detect with source dropdown.
    const btnRow = document.createElement('div');
    btnRow.className = 'event-editor-buttons';

    const addBtn = document.createElement('button');
    addBtn.className = 'node-btn node-btn-add';
    addBtn.textContent = '+ add event';
    addBtn.addEventListener('click', () => {
      const seedTime = evs.length ? Number(evs[evs.length - 1].time) + 0.5 : 0;
      evs.push({ time: seedTime });
      sortEvents(leaf);
      refreshEditor(leaf, commit);
      commit();
    });
    btnRow.appendChild(addBtn);

    // Source dropdown — which signal to scan for auto-detected events.
    const sourceLabel = document.createElement('label');
    sourceLabel.className = 'editor-hint';
    sourceLabel.style.marginLeft = '1em';
    sourceLabel.textContent = 'Detect from: ';
    sourceLabel.title = (
      'Which signal to scan for duplicate-time samples when you click '
      + 'Detect. Reference = pick up events from the saved baseline; '
      + 'Actual = pick up events from this run (useful on a fresh test '
      + 'with no baseline).'
    );
    const sourceSel = document.createElement('select');
    sourceSel.className = 'detect-source-select';
    for (const [val, txt] of [['ref', 'Reference'], ['act', 'Actual']]) {
      const opt = document.createElement('option');
      opt.value = val; opt.textContent = txt;
      if (getDetectSource(leaf) === val) opt.selected = true;
      sourceSel.appendChild(opt);
    }
    sourceSel.addEventListener('change', () => {
      setDetectSource(leaf, sourceSel.value);
    });
    sourceLabel.appendChild(sourceSel);
    btnRow.appendChild(sourceLabel);

    const detectBtn = document.createElement('button');
    detectBtn.className = 'node-btn detect-events-btn';
    detectBtn.textContent = '🔍 Detect events';
    detectBtn.title = (
      'Replace the declared-events list with duplicate-time samples '
      + "auto-detected on the selected source signal. Uses Modelica's "
      + 'convention: two consecutive samples at the same t flag a '
      + 'solver event.'
    );
    detectBtn.addEventListener('click', () => {
      const traj = getTrajectory(leaf);
      const source = getDetectSource(leaf);
      const times = source === 'act' ? (traj.act_time || []) : (traj.ref_time || []);
      const detected = _detectEvents(times);
      const globalTol = getGlobalTolerance(leaf);
      const seed = detected.map(t => ({ time: Number(t), tolerance: globalTol }));
      leafState[leaf.path].params.events = seed;
      refreshEditor(leaf, commit);
      commit();
    });
    btnRow.appendChild(detectBtn);

    root.appendChild(btnRow);
  }

  // --- editor lifecycle (MODE_PLOT_EDITORS contract) ---------------------
  return {
    activate(leaf, plotEl, commit) {
      // The leaf's editor slot is a .node-editor <div> the core
      // already cleared for us. Find it via the leaf's DOM anchor.
      const anchor = document.querySelector(
        `[data-path="${escapeSelector(leaf.path)}"] .node-editor`
      );
      if (!anchor) return;
      mount(anchor, leaf, commit);
    },
    deactivate(leaf, _plotEl) {
      const anchor = document.querySelector(
        `[data-path="${escapeSelector(leaf.path)}"] .node-editor`
      );
      if (anchor) unmount(anchor);
      mountedByLeaf.delete(leaf);
    },
  };
})();

MODE_PLOT_EDITORS['dominant-frequency'] = (function() {
  // Declared-peaks editor for dominant-frequency leaves (D75 + D76).
  // Each editor slot gets:
  //   * A spectrum subplot (reference + actual magnitude) computed LIVE
  //     by _computeFftSpectrum over the leaf's current window. Updating
  //     the window re-FFTs on the fly; no CLI round-trip needed for
  //     visualization.
  //   * Diamond markers at each declared peak's frequency, draggable via
  //     Shift+click/drag/right-click through the shared PointPlotEditor.
  //   * Acceptance-band shapes around each declared peak.
  //   * Table: freq, tolerance, mode, match-live, remove.
  //   * Detect dropdown: source = Reference | Actual. Uses the live
  //     windowed spectrum, so "brush a window → Detect" works in one
  //     interaction cycle. Populates derived_from_window metadata on
  //     each added peak so provenance survives the patch round-trip.
  const mountedByLeaf = new WeakMap();
  // Per-leaf selected detect source (in-memory; not persisted).
  const detectSourceByPath = new Map();

  function getDeclaredPeaks(leaf) {
    const p = (leafState[leaf.path] || {}).params || {};
    if (!Array.isArray(p.peaks)) p.peaks = [];
    return p.peaks;
  }
  function setDeclaredPeaks(leaf, peaks) {
    const p = (leafState[leaf.path] || {}).params || {};
    p.peaks = peaks.map(pk => {
      const out = {
        freq: Number(pk.freq) || 0,
        tolerance: Number(pk.tolerance) || 0,
        tolerance_mode: pk.tolerance_mode === 'abs' ? 'abs' : 'rel',
      };
      if (pk.derived_from_window) out.derived_from_window = pk.derived_from_window;
      return out;
    });
  }
  function sortDeclared(leaf) {
    const pks = getDeclaredPeaks(leaf);
    pks.sort((a, b) => Number(a.freq) - Number(b.freq));
  }

  function getLeafWindow(leaf) {
    const w = (leafState[leaf.path] || {}).window || {};
    const start = w.start != null && w.start !== '' ? Number(w.start) : null;
    const end = w.end != null && w.end !== '' ? Number(w.end) : null;
    return { start, end };
  }

  function _windowCopy(w) {
    const out = {};
    if (w.start != null) out.start = Number(w.start);
    if (w.end != null) out.end = Number(w.end);
    return Object.keys(out).length ? out : null;
  }

  // Compute the live spectrum for ``source`` ('ref' or 'act') scoped to
  // the leaf's current window. Falls back to the CLI-embedded arrays in
  // ``leaf.spectrum`` when the variable has no trajectory data (rare but
  // possible for unusual report shapes).
  function getLiveSpectrum(leaf, source) {
    const traj = (VARIABLES_BY_NAME[leaf.variable] || {}).trajectory || {};
    const time = (source === 'act' ? traj.act_time : traj.ref_time) || [];
    const values = (source === 'act' ? traj.act_values : traj.ref_values) || [];
    if (time.length < 4) {
      // Fallback — use whatever the CLI embedded.
      const spec = leaf.spectrum || {};
      return {
        freqs: (source === 'act' ? spec.act_freq : spec.ref_freq) || [],
        magnitudes: (source === 'act' ? spec.act_mag : spec.ref_mag) || [],
      };
    }
    const w = getLeafWindow(leaf);
    const sliced = _sliceToWindow(time, values, w.start, w.end);
    return _computeFftSpectrum(sliced.time, sliced.values);
  }

  function getDetectSource(leaf) {
    return detectSourceByPath.get(leaf.path) || 'ref';
  }
  function setDetectSource(leaf, source) {
    detectSourceByPath.set(leaf.path, source === 'act' ? 'act' : 'ref');
  }

  function magnitudeAt(hz, xs, ys) {
    if (!xs || !xs.length) return 0;
    let best = 0, bestD = Infinity;
    for (let i = 0; i < xs.length; i++) {
      const d = Math.abs(xs[i] - hz);
      if (d < bestD) { bestD = d; best = i; }
    }
    return ys[best];
  }

  // Live match evaluation: does each declared peak have a local max in
  // its tolerance window on the actual spectrum? Replaces CLI-stale
  // paired_peaks in the table's match column. Returns [{declared_hz,
  // matched_hz, delta, in_window_freqs_missing}].
  function evaluateMatches(leaf, actSpectrum) {
    const pks = getDeclaredPeaks(leaf);
    return pks.map(pk => {
      const f = Number(pk.freq);
      const tol = Number(pk.tolerance) || 0;
      const [lo, hi] = pk.tolerance_mode === 'abs'
        ? [f - tol, f + tol]
        : [f * (1 - tol), f * (1 + tol)];
      const match = _findStrongestPeakInWindowJS(
        actSpectrum.freqs, actSpectrum.magnitudes, lo, hi,
      );
      return {
        declared_hz: f,
        matched_hz: match ? match.freq : null,
        delta: match ? Math.abs(match.freq - f) : null,
      };
    });
  }

  const _peakEditor = createPointPlotEditor({
    getAnchors(leaf) {
      const refSpec = getLiveSpectrum(leaf, 'ref');
      const pks = getDeclaredPeaks(leaf);
      return pks.map(pk => ({
        pt: pk,
        x: Number(pk.freq),
        y: magnitudeAt(Number(pk.freq), refSpec.freqs, refSpec.magnitudes),
      }));
    },
    onClickAdd(leaf, _plotEl, x, _y, commit) {
      if (!(x > 0)) return;
      const pks = getDeclaredPeaks(leaf);
      const newPk = {
        freq: Number(x),
        tolerance: 0.01,
        tolerance_mode: 'rel',
      };
      const w = _windowCopy(getLeafWindow(leaf));
      if (w) newPk.derived_from_window = w;
      pks.push(newPk);
      sortDeclared(leaf);
      refreshEditor(leaf, commit);
    },
    onDragStep(leaf, _plotEl, anchor, x, _y, _commit) {
      if (!(x > 0)) return;
      anchor.pt.freq = Number(x);
      sortDeclared(leaf);
      refreshEditor(leaf, _commit);
    },
    onDragEnd(leaf, _plotEl, _anchor, commit) {
      sortDeclared(leaf);
      refreshEditor(leaf, commit);
    },
    onRemove(leaf, _plotEl, anchor, commit) {
      const pks = getDeclaredPeaks(leaf);
      const idx = pks.indexOf(anchor.pt);
      if (idx >= 0) {
        pks.splice(idx, 1);
        refreshEditor(leaf, commit);
      }
    },
  });

  function refreshEditor(leaf, commit) {
    const mounted = mountedByLeaf.get(leaf);
    if (!mounted) return;
    for (const { subplotDiv, tableDiv } of mounted) {
      _renderSpectrumAndPeaks(subplotDiv, leaf);
      _renderTable(tableDiv, leaf, commit);
    }
    commit();
  }

  // Lighter refresh — re-renders the Plotly subplot only, leaves the
  // table DOM untouched. Used from `input` event handlers on the table's
  // number inputs so mid-typing keystrokes don't destroy and recreate
  // the input element (which would drop focus and eat decimal points).
  // The full refresh (rebuilding the table with its sorted rows + live
  // match column) happens on `change` (blur / Enter).
  function refreshSubplotOnly(leaf, commit) {
    const mounted = mountedByLeaf.get(leaf);
    if (!mounted) return;
    for (const { subplotDiv } of mounted) {
      _renderSpectrumAndPeaks(subplotDiv, leaf);
    }
    commit();
  }

  function _renderSpectrumAndPeaks(div, leaf) {
    if (typeof Plotly === 'undefined') {
      div.innerHTML = '<div class="editor-hint">Plotly not loaded.</div>';
      return;
    }
    const refSpec = getLiveSpectrum(leaf, 'ref');
    const actSpec = getLiveSpectrum(leaf, 'act');
    if (refSpec.freqs.length === 0 && actSpec.freqs.length === 0) {
      div.innerHTML = '<div class="editor-hint">No trajectory data — '
                    + 'cannot compute spectrum.</div>';
      return;
    }
    const pks = getDeclaredPeaks(leaf);
    const liveMatches = evaluateMatches(leaf, actSpec);
    const matchByIdx = Object.fromEntries(
      liveMatches.map((m, i) => [i, m]),
    );

    const traces = [];
    if (refSpec.freqs.length) {
      traces.push({
        x: refSpec.freqs, y: refSpec.magnitudes, mode: 'lines', type: 'scatter',
        name: 'Reference',
        line: { color: '#1976D2', width: 1.5 },
      });
    }
    if (actSpec.freqs.length) {
      traces.push({
        x: actSpec.freqs, y: actSpec.magnitudes, mode: 'lines', type: 'scatter',
        name: 'Actual',
        line: { color: '#D32F2F', width: 1.5, dash: 'dot' },
      });
    }
    // Declared-peak markers at the reference-spectrum amplitude (so the
    // marker sits on the reference curve). Color reflects LIVE match
    // status — green for matched, red for no-match.
    const declaredXs = pks.map(p => Number(p.freq));
    const declaredYs = pks.map(p => magnitudeAt(Number(p.freq), refSpec.freqs, refSpec.magnitudes));
    const declaredColors = pks.map((_, i) => (
      matchByIdx[i]?.matched_hz != null ? '#2e7d32' : '#c62828'
    ));
    if (pks.length) {
      traces.push({
        x: declaredXs, y: declaredYs,
        mode: 'markers', type: 'scatter',
        name: 'Declared peaks',
        marker: {
          color: declaredColors, size: 13, symbol: 'diamond',
          line: { color: 'white', width: 1.5 },
        },
        hoverinfo: 'x+y',
      });
    }
    // Matched-actual markers from LIVE evaluation.
    const matchedXs = liveMatches.filter(m => m.matched_hz != null).map(m => m.matched_hz);
    if (matchedXs.length) {
      traces.push({
        x: matchedXs,
        y: matchedXs.map(hz => magnitudeAt(hz, actSpec.freqs, actSpec.magnitudes)),
        mode: 'markers', type: 'scatter',
        name: 'Matched (actual)',
        marker: { color: '#D32F2F', size: 10, symbol: 'x', line: { width: 2 } },
        hoverinfo: 'x+y',
      });
    }
    // Acceptance-band shapes around each declared peak.
    const shapes = [];
    for (const pk of pks) {
      const f = Number(pk.freq);
      const tol = Number(pk.tolerance) || 0;
      if (tol <= 0 || f <= 0) continue;
      const [lo, hi] = pk.tolerance_mode === 'abs'
        ? [f - tol, f + tol]
        : [f * (1 - tol), f * (1 + tol)];
      shapes.push({
        type: 'rect', xref: 'x', yref: 'paper',
        x0: lo, x1: hi, y0: 0, y1: 1,
        fillcolor: 'rgba(25,118,210,0.12)',
        line: { width: 0 },
      });
    }
    Plotly.newPlot(div, traces, {
      xaxis: { title: 'Frequency (Hz)', hoverformat: '.4~g' },
      yaxis: { title: 'Magnitude', tickformat: '.3~g' },
      shapes,
      margin: { t: 10, r: 10, b: 40, l: 55 },
      legend: { orientation: 'h', y: 1.12, x: 0.02 },
      uirevision: 'keep',
    }, { displayModeBar: false, responsive: true });
  }

  function _renderTable(container, leaf, commit) {
    container.innerHTML = '';
    const pks = getDeclaredPeaks(leaf);
    const actSpec = getLiveSpectrum(leaf, 'act');
    const liveMatches = evaluateMatches(leaf, actSpec);

    const hint = document.createElement('div');
    hint.className = 'editor-hint';
    hint.innerHTML = 'Shift+click on the spectrum to add a peak · '
                   + 'Shift+drag to move · Shift+right-click to remove · '
                   + 'Edit the window to rescope the spectrum';
    container.appendChild(hint);

    const table = document.createElement('table');
    table.className = 'tube-table';
    const header = table.insertRow();
    header.innerHTML = '<th>Freq (Hz)</th><th>Tolerance</th>'
                     + '<th>Mode</th><th>Match (live)</th>'
                     + '<th>Src window</th><th></th>';

    pks.forEach((pk, i) => {
      const row = table.insertRow();
      // Freq — input event for live plot preview (subplot only, keeps
      // focus alive), change event for full refresh + sort on blur.
      const freqTd = document.createElement('td');
      const freqInp = document.createElement('input');
      freqInp.type = 'number'; freqInp.step = 'any'; freqInp.min = '0';
      freqInp.value = String(pk.freq);
      freqInp.addEventListener('input', () => {
        const v = parseFloat(freqInp.value);
        if (Number.isFinite(v) && v > 0) {
          pk.freq = v;
          refreshSubplotOnly(leaf, commit);
        }
      });
      freqInp.addEventListener('change', () => {
        const v = parseFloat(freqInp.value);
        if (Number.isFinite(v) && v > 0) {
          pk.freq = v;
          sortDeclared(leaf);
          refreshEditor(leaf, commit);
        }
      });
      freqTd.appendChild(freqInp); row.appendChild(freqTd);
      // Tolerance — same split.
      const tolTd = document.createElement('td');
      const tolInp = document.createElement('input');
      tolInp.type = 'number'; tolInp.step = 'any'; tolInp.min = '0';
      tolInp.value = String(pk.tolerance);
      tolInp.addEventListener('input', () => {
        const v = parseFloat(tolInp.value);
        if (Number.isFinite(v) && v >= 0) {
          pk.tolerance = v;
          refreshSubplotOnly(leaf, commit);
        }
      });
      tolInp.addEventListener('change', () => {
        const v = parseFloat(tolInp.value);
        if (Number.isFinite(v) && v >= 0) {
          pk.tolerance = v;
          refreshEditor(leaf, commit);
        }
      });
      tolTd.appendChild(tolInp); row.appendChild(tolTd);
      // Tolerance mode
      const modeTd = document.createElement('td');
      const modeSel = document.createElement('select');
      for (const m of ['rel', 'abs']) {
        const opt = document.createElement('option');
        opt.value = m; opt.textContent = m;
        if (pk.tolerance_mode === m) opt.selected = true;
        modeSel.appendChild(opt);
      }
      modeSel.title = 'rel = fractional (e.g. 0.01 = 1% of declared freq); '
                    + 'abs = Hz';
      modeSel.addEventListener('change', () => {
        pk.tolerance_mode = modeSel.value === 'abs' ? 'abs' : 'rel';
        refreshEditor(leaf, commit);
      });
      modeTd.appendChild(modeSel); row.appendChild(modeTd);
      // Live match status (JS scorer).
      const matchTd = document.createElement('td');
      const m = liveMatches[i];
      if (!m || m.matched_hz == null) {
        matchTd.innerHTML = '<span class="fail">no match</span>';
      } else {
        const dhz = m.delta != null ? m.delta.toExponential(2) : '';
        matchTd.innerHTML = `<span class="pass">${m.matched_hz.toFixed(4)} Hz `
                          + `(Δ ${dhz})</span>`;
      }
      row.appendChild(matchTd);
      // Source window (provenance metadata)
      const winTd = document.createElement('td');
      const w = pk.derived_from_window;
      if (w && (w.start != null || w.end != null)) {
        const s = w.start != null ? Number(w.start).toPrecision(3) : '−∞';
        const e = w.end != null ? Number(w.end).toPrecision(3) : '+∞';
        const span = document.createElement('span');
        span.className = 'editor-hint';
        span.textContent = `[${s}, ${e}]`;
        span.title = `Detected in window [${s}, ${e}]. Scoring uses the `
                   + `leaf-level window; this metadata records where you found it.`;
        winTd.appendChild(span);
      } else {
        winTd.innerHTML = '<span class="editor-hint">—</span>';
      }
      row.appendChild(winTd);
      // Remove
      const rmTd = document.createElement('td');
      const rmBtn = document.createElement('button');
      rmBtn.className = 'node-btn node-btn-remove';
      rmBtn.textContent = '✕';
      rmBtn.title = 'Remove peak';
      rmBtn.addEventListener('click', () => {
        pks.splice(i, 1);
        refreshEditor(leaf, commit);
      });
      rmTd.appendChild(rmBtn); row.appendChild(rmTd);
    });
    container.appendChild(table);

    // Buttons row: add + detect (with source dropdown).
    const btnRow = document.createElement('div');
    btnRow.className = 'peak-editor-buttons';

    const addBtn = document.createElement('button');
    addBtn.className = 'node-btn node-btn-add';
    addBtn.textContent = '+ add peak';
    addBtn.addEventListener('click', () => {
      // Seed frequency from the first detected peak on the live ref
      // spectrum, or 1.0 Hz as a last resort.
      const live = getLiveSpectrum(leaf, 'ref');
      const detected = _findTopNPeaksJS(live.freqs, live.magnitudes, 10, 0);
      const freqs = detected.map(p => p.freq);
      const seedFreq = pks.length
        ? (pks[pks.length - 1].freq + (freqs[pks.length] || pks[pks.length - 1].freq * 2))
        : (freqs[0] || 1.0);
      const newPk = {
        freq: Number(seedFreq),
        tolerance: 0.01,
        tolerance_mode: 'rel',
      };
      const w = _windowCopy(getLeafWindow(leaf));
      if (w) newPk.derived_from_window = w;
      pks.push(newPk);
      sortDeclared(leaf);
      refreshEditor(leaf, commit);
    });
    btnRow.appendChild(addBtn);

    // Detect source dropdown — picks WHICH signal the Detect button
    // analyzes. Does not affect the subplot display (both reference
    // and actual curves are always shown). Default: Reference (the
    // regression target). Pick Actual for fresh tests where no
    // baseline exists yet.
    const sourceLabel = document.createElement('label');
    sourceLabel.className = 'editor-hint';
    sourceLabel.style.marginLeft = '1em';
    sourceLabel.textContent = 'Detect from: ';
    sourceLabel.title = 'Chooses which signal Detect analyzes for peaks. '
                      + 'Does NOT change the subplot display (both curves '
                      + 'are always shown). Reference = seed from the '
                      + 'regression target; Actual = seed from the current '
                      + 'run\'s output (useful on fresh tests without a '
                      + 'baseline).';
    const sourceSel = document.createElement('select');
    sourceSel.className = 'detect-source-select';
    for (const [val, txt] of [['ref', 'Reference'], ['act', 'Actual']]) {
      const opt = document.createElement('option');
      opt.value = val; opt.textContent = txt;
      if (getDetectSource(leaf) === val) opt.selected = true;
      sourceSel.appendChild(opt);
    }
    sourceSel.addEventListener('change', () => {
      setDetectSource(leaf, sourceSel.value);
    });
    sourceLabel.appendChild(sourceSel);
    btnRow.appendChild(sourceLabel);

    const detectBtn = document.createElement('button');
    detectBtn.className = 'node-btn';
    detectBtn.textContent = '🔍 Detect peaks';
    detectBtn.title = 'Replace the table with peaks detected on the '
                    + 'selected source spectrum, scoped to the leaf\'s '
                    + 'current window. Low-freq noise (< ~2 cycles per '
                    + 'window) is filtered out. Each new peak is stamped '
                    + 'with "derived_from_window" metadata for provenance.';
    detectBtn.addEventListener('click', () => {
      const source = getDetectSource(leaf);
      const live = getLiveSpectrum(leaf, source);
      if (live.freqs.length < 3) return;
      // Default min_frequency: require at least 2 full cycles within the
      // windowed signal. Anything below that is low-freq leakage / trend
      // residue, not a reliably-resolved peak. Matches the intuition
      // "don't flag a peak whose period doesn't fit twice in my window."
      const binSpacing = live.freqs[1] - live.freqs[0];
      const minFreq = 2 * binSpacing;
      const detected = _findTopNPeaksJS(live.freqs, live.magnitudes, 3, minFreq);
      if (detected.length === 0) return;
      const w = _windowCopy(getLeafWindow(leaf));
      const seed = detected.map(p => {
        const pk = {
          freq: Number(p.freq),
          tolerance: 0.01,
          tolerance_mode: 'rel',
        };
        if (w) pk.derived_from_window = w;
        return pk;
      });
      setDeclaredPeaks(leaf, seed);
      refreshEditor(leaf, commit);
    });
    btnRow.appendChild(detectBtn);

    // Resolution hint — tells the user what the current window's FFT
    // bin spacing is, so they can choose meaningful tolerances. If a
    // 1-Hz declared peak has a 0.05 Hz tolerance on a signal whose bin
    // spacing is 0.25 Hz, that's sub-bin and will fail for reasons
    // unrelated to drift.
    const resolutionHint = document.createElement('div');
    resolutionHint.className = 'editor-hint';
    resolutionHint.style.marginTop = '0.4em';
    const refSpec = getLiveSpectrum(leaf, 'ref');
    if (refSpec.freqs.length >= 2) {
      const binSpacing = refSpec.freqs[1] - refSpec.freqs[0];
      resolutionHint.textContent = (
        `FFT bin resolution: ~${binSpacing.toExponential(2)} Hz. `
        + `Tolerances tighter than this may fail regardless of actual drift; `
        + `set at least 2× bin spacing for reliable matching.`
      );
    }
    container.appendChild(btnRow);
    container.appendChild(resolutionHint);
  }

  return {
    activate(leaf, _plotEl, commit) {
      const mounted = [];
      getEditorSlots(leaf).forEach(slot => {
        const title = document.createElement('div');
        title.className = 'editor-title';
        title.textContent = 'Declared frequency peaks';
        slot.appendChild(title);

        const subplotDiv = document.createElement('div');
        subplotDiv.className = 'spectrum-subplot';
        subplotDiv.style.width = '100%';
        subplotDiv.style.height = '260px';
        slot.appendChild(subplotDiv);

        const tableDiv = document.createElement('div');
        tableDiv.className = 'peak-editor-ui';
        slot.appendChild(tableDiv);

        mounted.push({ slot, subplotDiv, tableDiv });
      });
      mountedByLeaf.set(leaf, mounted);
      // Initial render + attach shift-editor to each subplot.
      for (const { subplotDiv, tableDiv } of mounted) {
        _renderSpectrumAndPeaks(subplotDiv, leaf);
        _renderTable(tableDiv, leaf, commit);
        _peakEditor.attach(leaf, subplotDiv, commit);
      }
      commit();
    },
    deactivate(leaf, _plotEl) {
      const mounted = mountedByLeaf.get(leaf) || [];
      for (const { subplotDiv } of mounted) {
        _peakEditor.detach(subplotDiv);
        if (window.Plotly && subplotDiv.isConnected) Plotly.purge(subplotDiv);
      }
      mountedByLeaf.delete(leaf);
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

  // Kind dropdown — replaces the static label with a live editor. Inline
  // params (k for k-of-n; weights/threshold/direction for weighted) render
  // alongside so the entire combinator config is editable from the header.
  header.appendChild(kindSelect(node));
  header.appendChild(childCountLabel(node));
  if (node.combinator === 'k-of-n' || node.combinator === 'weighted') {
    header.appendChild(combinatorParamControls(node));
  }

  // Structural controls — + adds a leaf child, ⊕ wraps this node in a new
  // combinator, ⊖ unwraps this node (single-child combinators only),
  // − removes this node. Available on both full-tree and per-variable
  // mounts; per-variable mounts prefill + with the filtered variable so
  // edits stay scoped to the plot the user clicked from.
  header.appendChild(addLeafButton(node, opts.variableFilter));
  header.appendChild(wrapButton(node));
  if ((node.children || []).length === 1) header.appendChild(unwrapButton(node));
  if (opts.parent) header.appendChild(removeNodeButton(node, opts));

  wrapper.appendChild(header);
  wrapper.appendChild(childContainer);
  container.appendChild(wrapper);
  return true;
}

const COMBINATOR_HELP = {
  'and':      'All children must pass.',
  'or':       'At least one child must pass.',
  'warn':     'Single-child wrapper; never fails its parent (advisory-only scoring).',
  'k-of-n':   'At least k of N children must pass.',
  'weighted': 'Weighted sum of child scores passes a threshold.',
};

function kindSelect(node) {
  const sel = document.createElement('select');
  sel.className = 'node-kind-select';
  sel.title = 'Change combinator kind. ' + COMBINATOR_HELP[node.combinator || 'and'];
  const children = node.children || [];
  VALID_COMBINATORS.forEach(k => {
    const opt = document.createElement('option');
    opt.value = k;
    opt.textContent = k;
    opt.title = COMBINATOR_HELP[k] || '';
    if (k === node.combinator) opt.selected = true;
    // Disable warn when the node has != 1 child — use wrap-in-warn instead.
    if (k === 'warn' && k !== node.combinator && children.length !== 1) {
      opt.disabled = true;
      opt.textContent = 'warn (wrap first)';
      opt.title = 'warn requires exactly 1 child. Use the ⊕ wrap button to '
                + 'put this node inside a new warn parent instead.';
    }
    sel.appendChild(opt);
  });
  sel.addEventListener('change', (e) => {
    const ok = changeCombinatorKind(node.path, sel.value);
    if (!ok) sel.value = node.combinator;  // revert on refusal
    else sel.title = 'Change combinator kind. ' + COMBINATOR_HELP[sel.value];
  });
  return sel;
}

function childCountLabel(node) {
  const span = document.createElement('span');
  span.className = 'node-child-count';
  const n = (node.children || []).length;
  span.textContent = `[${n}]`;
  return span;
}

function combinatorParamControls(node) {
  // Inline header controls for k-of-n and weighted params. Scalar fields
  // render as number/select inputs; weights[] renders as a compact
  // per-child table. Same wholesale /metrics replace path on commit.
  const container = document.createElement('span');
  container.className = 'node-combinator-params';
  const children = node.children || [];

  if (node.combinator === 'k-of-n') {
    const label = document.createElement('label');
    label.className = 'mc-field mc-int';
    label.innerHTML = '<span>k</span>';
    const inp = document.createElement('input');
    inp.type = 'number';
    inp.step = '1';
    inp.min = '1';
    inp.max = String(Math.max(1, children.length));
    inp.value = String(node.k || 1);
    inp.className = 'node-k-input';
    inp.addEventListener('change', () => {
      const v = parseInt(inp.value, 10);
      if (Number.isFinite(v) && v >= 1) {
        node.k = v;
        markStructureDirty();
      } else {
        inp.value = String(node.k || 1);
      }
    });
    label.appendChild(inp);
    container.appendChild(label);
  }

  if (node.combinator === 'weighted') {
    const thrLabel = document.createElement('label');
    thrLabel.className = 'mc-field mc-float';
    thrLabel.innerHTML = '<span>threshold</span>';
    const thrInp = document.createElement('input');
    thrInp.type = 'number';
    thrInp.step = 'any';
    thrInp.value = String(node.threshold ?? 1.0);
    thrInp.className = 'node-threshold-input';
    thrInp.addEventListener('change', () => {
      const v = parseFloat(thrInp.value);
      if (Number.isFinite(v)) { node.threshold = v; markStructureDirty(); }
      else thrInp.value = String(node.threshold ?? 1.0);
    });
    thrLabel.appendChild(thrInp);
    container.appendChild(thrLabel);

    const dirLabel = document.createElement('label');
    dirLabel.className = 'mc-field mc-enum';
    dirLabel.innerHTML = '<span>direction</span>';
    const dirSel = document.createElement('select');
    dirSel.className = 'node-direction-select';
    ['less', 'greater'].forEach(d => {
      const opt = document.createElement('option');
      opt.value = d;
      opt.textContent = d;
      if (d === (node.direction || 'less')) opt.selected = true;
      dirSel.appendChild(opt);
    });
    dirSel.addEventListener('change', () => {
      node.direction = dirSel.value;
      markStructureDirty();
    });
    dirLabel.appendChild(dirSel);
    container.appendChild(dirLabel);

    // Weights row — one number input per child; auto-resized when the
    // child count changes (handled by the wholesale re-render in
    // markStructureDirty, which calls this function again).
    const weightsLabel = document.createElement('span');
    weightsLabel.className = 'node-weights';
    weightsLabel.innerHTML = '<span class="mc-field-label">weights</span>';
    const weights = Array.isArray(node.weights) ? node.weights : [];
    children.forEach((_, i) => {
      const w = weights[i] ?? 1.0;
      const wi = document.createElement('input');
      wi.type = 'number';
      wi.step = 'any';
      wi.value = String(w);
      wi.className = 'node-weight-input';
      wi.dataset.index = String(i);
      wi.addEventListener('change', () => {
        const v = parseFloat(wi.value);
        if (Number.isFinite(v)) {
          const nextWeights = children.map((__, j) => (
            j === i ? v : ((node.weights || [])[j] ?? 1.0)
          ));
          node.weights = nextWeights;
          markStructureDirty();
        } else {
          wi.value = String((node.weights || [])[i] ?? 1.0);
        }
      });
      weightsLabel.appendChild(wi);
    });
    container.appendChild(weightsLabel);
  }

  return container;
}

function resetButton(leaf) {
  const btn = document.createElement('button');
  btn.className = 'node-btn node-btn-reset';
  btn.textContent = '↻';
  btn.title = 'Reset this leaf to its CLI-evaluated values';
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    resetLeafToOriginal(leaf.path);
  });
  return btn;
}

function wrapButton(node) {
  const btn = document.createElement('button');
  btn.className = 'node-btn node-btn-wrap';
  btn.textContent = '⊕';
  btn.title = 'Wrap this node in a new combinator';
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    openWrapPopup(btn, node);
  });
  return btn;
}

function unwrapButton(node) {
  const btn = document.createElement('button');
  btn.className = 'node-btn node-btn-unwrap';
  btn.textContent = '⊖';
  btn.title = 'Unwrap — replace this node with its single child';
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    unwrapWorkingNode(node.path);
  });
  return btn;
}

let _activeWrapPopup = null;

function openWrapPopup(anchor, node) {
  closeWrapPopup();
  const pop = document.createElement('span');
  pop.className = 'wrap-popup';
  const dropdownOpts = VALID_COMBINATORS
    .map(k => `<option value="${k}">${k}</option>`)
    .join('');
  pop.innerHTML = `
    <span class="wrap-popup-label">Wrap in</span>
    <select class="wrap-popup-kind">${dropdownOpts}</select>
    <button class="node-btn wrap-popup-yes">Confirm</button>
    <button class="node-btn wrap-popup-no">Cancel</button>
  `;
  anchor.insertAdjacentElement('afterend', pop);
  _activeWrapPopup = pop;
  const onOutside = (ev) => {
    if (!pop.contains(ev.target) && ev.target !== anchor) closeWrapPopup();
  };
  const onEsc = (ev) => { if (ev.key === 'Escape') closeWrapPopup(); };
  pop._cleanup = () => {
    document.removeEventListener('mousedown', onOutside, true);
    document.removeEventListener('keydown', onEsc);
  };
  setTimeout(() => {
    document.addEventListener('mousedown', onOutside, true);
    document.addEventListener('keydown', onEsc);
  }, 0);
  pop.querySelector('.wrap-popup-yes').addEventListener('click', (e) => {
    e.stopPropagation();
    const kind = pop.querySelector('.wrap-popup-kind').value;
    closeWrapPopup();
    wrapWorkingNode(node.path, kind);
  });
  pop.querySelector('.wrap-popup-no').addEventListener('click', (e) => {
    e.stopPropagation();
    closeWrapPopup();
  });
}

function closeWrapPopup() {
  if (!_activeWrapPopup) return;
  if (_activeWrapPopup._cleanup) _activeWrapPopup._cleanup();
  _activeWrapPopup.remove();
  _activeWrapPopup = null;
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

  // Visibility toggle — hides this leaf's plot overlay (tube polygon, range
  // dashed lines, final-time marker, window band). Does NOT affect scoring —
  // the pass pill stays whatever the scorer computed. Leaf appears in every
  // mount (top-of-report tree + per-variable trees); all instances share the
  // same leafState.visible, so we sync sibling checkboxes on toggle.
  const visToggle = document.createElement('input');
  visToggle.type = 'checkbox';
  visToggle.className = 'node-visible';
  visToggle.checked = leafState[leaf.path] && leafState[leaf.path].visible !== false;
  visToggle.title = 'Show this leaf\'s plot overlay (does not affect scoring)';
  visToggle.addEventListener('change', () => {
    if (leafState[leaf.path]) leafState[leaf.path].visible = visToggle.checked;
    syncSiblingVisToggles(leaf.path, visToggle.checked, visToggle);
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
  // Structural controls — ⊕ wraps this leaf in a new combinator (always
  // allowed), − removes this leaf (only when a parent exists). ↻ resets
  // this leaf's params + window to the CLI-evaluated originals. Root leaf
  // (legal but unusual: single-leaf tree) has no remove.
  header.appendChild(resetButton(leaf));
  header.appendChild(wrapButton(leaf));
  if (opts.parent) header.appendChild(removeNodeButton(leaf, opts));
  wrapper.appendChild(header);

  // Controls — server-rendered HTML for mode fields + window inputs.
  // Tube leaves intentionally skip mode schema inputs: the rich
  // interactive editor (activated on click) owns that config surface
  // and would duplicate the same fields. Window inputs still show for
  // tube so the user can scope a tube to a time window.
  // Tube + dominant-frequency both own their entire UI via the activated
  // editor (rich table + shift-edit on plot). Suppressing the auto-derived
  // header panel avoids a redundant empty passthrough box.
  const skipModeControls = (leaf.metric === 'tube' || leaf.metric === 'dominant-frequency');
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
  'nrmse', 'tube', 'points', 'range', 'event-timing', 'dominant-frequency',
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

// ---- Structural editing: wrap / unwrap / change-kind (#52) ----
// All three mutate WORKING_TREE → markStructureDirty; the existing wholesale
// ``/metrics`` replace patch carries the result on save.

const VALID_COMBINATORS = ['and', 'or', 'warn', 'k-of-n', 'weighted'];

function _seedCombinatorParams(kind, children) {
  // Fills in the kind-specific fields when a combinator switches INTO
  // k-of-n or weighted. Other kinds stay bare (combinator + children).
  const n = (children || []).length || 1;
  if (kind === 'k-of-n') return { k: Math.max(1, n - 1) };
  if (kind === 'weighted') {
    return {
      weights: Array(n).fill(1.0),
      threshold: 1.0,
      direction: 'less',
    };
  }
  return {};
}

function _stripCombinatorParams(node) {
  // Drop kind-specific fields when a combinator switches OUT of them.
  // Idempotent — safe to call when the fields aren't present.
  delete node.k;
  delete node.weights;
  delete node.threshold;
  delete node.direction;
}

function _findPathContext(path) {
  // Returns {node, parent, indexInParent} for the target path, or null.
  if (!WORKING_TREE) return null;
  if (WORKING_TREE.path === path) {
    return { node: WORKING_TREE, parent: null, indexInParent: -1 };
  }
  let found = null;
  (function walk(n, parent, idx) {
    if (found) return;
    if (n.path === path) { found = { node: n, parent, indexInParent: idx }; return; }
    (n.children || []).forEach((c, i) => walk(c, n, i));
  })(WORKING_TREE, null, -1);
  return found;
}

function wrapWorkingNode(path, kind) {
  // Wrap the node at ``path`` in a new combinator of the given kind.
  // Always produces ``kind(target)`` — a single-child parent regardless
  // of target's shape. Works uniformly for leaf + combinator targets.
  if (!VALID_COMBINATORS.includes(kind)) return false;
  const ctx = _findPathContext(path);
  if (!ctx) return false;
  const wrapper = {
    kind: 'combinator',
    combinator: kind,
    children: [ctx.node],
  };
  Object.assign(wrapper, _seedCombinatorParams(kind, [ctx.node]));
  if (ctx.parent) {
    ctx.parent.children[ctx.indexInParent] = wrapper;
  } else {
    WORKING_TREE = wrapper;
  }
  markStructureDirty();
  return true;
}

function unwrapWorkingNode(path) {
  // Replace the combinator at ``path`` with its single child. Refuses
  // to unwrap leaves or combinators with != 1 child (user should edit
  // children first, or remove siblings, then unwrap).
  const ctx = _findPathContext(path);
  if (!ctx) return false;
  const target = ctx.node;
  if (target.kind !== 'combinator') return false;
  const children = target.children || [];
  if (children.length !== 1) return false;
  const lone = children[0];
  if (ctx.parent) {
    ctx.parent.children[ctx.indexInParent] = lone;
  } else {
    WORKING_TREE = lone;
  }
  markStructureDirty();
  return true;
}

function changeCombinatorKind(path, newKind) {
  // Flip node.combinator = newKind. Seeds or strips kind-specific
  // fields (k, weights, threshold, direction). Refuses change-to-warn
  // on multi-child targets — the grammar won't validate, and the user
  // should use wrap-in-warn (auto-inserts AND) for that intent.
  if (!VALID_COMBINATORS.includes(newKind)) return false;
  const ctx = _findPathContext(path);
  if (!ctx || ctx.node.kind !== 'combinator') return false;
  const node = ctx.node;
  const children = node.children || [];
  if (newKind === 'warn' && children.length !== 1) return false;
  if (newKind === node.combinator) return true;  // no-op
  node.combinator = newKind;
  _stripCombinatorParams(node);
  Object.assign(node, _seedCombinatorParams(newKind, children));
  markStructureDirty();
  return true;
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
    let minAttr = '';
    let maxAttr = '';
    if (f.ui_min != null) minAttr = ` min="${_escHtml(String(f.ui_min))}"`;
    if (f.ui_max != null) {
      // Soft cap — raise to current value if user already exceeded it.
      const cur = value == null ? -Infinity : Number(value);
      const eff = Math.max(Number(f.ui_max), Number.isFinite(cur) ? cur : -Infinity);
      maxAttr = f.type === 'int'
        ? ` max="${Math.floor(eff)}"`
        : ` max="${_escHtml(String(eff))}"`;
    }
    return `<label class="mc-field mc-${f.type}"${title}><span>${label}</span>`
         + `<input type="number" step="${step}" data-field="${name}"${minAttr}${maxAttr}${val}></label>`;
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
  // Snapshot leaf object→oldPath, rebuild, then migrate leafState entries
  // so live-edited values survive a path shift (remove / wrap / unwrap).
  // Without this, an edited tolerance vanishes when a sibling is removed.
  migrateLeafStatePaths();
  renderAllNodeTreesFromWorking();
  refreshPassStates();
  updateExport();
}

function migrateLeafStatePaths() {
  // Walk the tree, capture each leaf ref + its current (stale) path, then
  // rebuildPaths, then move leafState entries from old → new paths. Two
  // passes (read old + delete, then write new) avoid clobber when a chain
  // of paths shifts through a common index.
  if (!WORKING_TREE) {
    rebuildPaths(WORKING_TREE, '/metrics');
    return;
  }
  const snapshot = [];
  walkLeaves(WORKING_TREE, (leaf) => {
    snapshot.push({ leaf, oldPath: leaf.path });
  });
  rebuildPaths(WORKING_TREE, '/metrics');
  const carried = [];
  for (const { leaf, oldPath } of snapshot) {
    if (leaf.path === oldPath) continue;
    if (leafState[oldPath] !== undefined) {
      carried.push({ newPath: leaf.path, state: leafState[oldPath] });
      delete leafState[oldPath];
    }
  }
  for (const { newPath, state } of carried) {
    leafState[newPath] = state;
  }
}

function renderAllNodeTreesFromWorking() {
  // Stage-4 structural edits route through currentTree() — it returns
  // WORKING_TREE while structureDirty is set, so every reader picks up
  // the mutated tree transparently.
  renderAllNodeTrees();
  // The template-embedded ``mode_controls_html`` is a static server-
  // rendered string with the *original* values; after a re-render the
  // DOM inputs drift back to those authored values even though the live
  // edits live in leafState. Push leafState back onto the inputs so
  // edits survive every re-render trigger (structural and otherwise).
  refreshLeafInputsFromState();
  // Plots may gain or lose leaves — re-render affected variables.
  VARIABLE_ORDER.forEach((v, i) => renderVariablePlot(v, i));
}

function refreshLeafInputsFromState() {
  document.querySelectorAll('.node-leaf').forEach(el => {
    const path = el.dataset.path;
    const state = leafState[path];
    if (!state) return;
    el.querySelectorAll('.mode-controls [data-field]').forEach(inp => {
      const field = inp.dataset.field;
      _setInputValue(inp, (state.params || {})[field]);
    });
    el.querySelectorAll('.window-controls [data-field]').forEach(inp => {
      const field = inp.dataset.field;
      const key = field === 'window_start' ? 'start'
                : field === 'window_end'   ? 'end'
                : null;
      if (!key) return;
      _setInputValue(inp, (state.window || {})[key]);
    });
  });
}

function _setInputValue(inp, val) {
  if (inp.type === 'checkbox') { inp.checked = !!val; return; }
  if (inp.dataset.passthrough === 'true') {
    inp.value = val == null ? '' : JSON.stringify(val);
    return;
  }
  inp.value = val == null ? '' : String(val);
}

function resetLeafToOriginal(path) {
  // Revert leafState[path].params / .window to the original CLI-evaluated
  // snapshot (captured at init / append time). Non-structural — doesn't
  // flip structureDirty, just re-renders the current tree so DOM + plot
  // pick up the reverted values. The input-refresh pass handles the DOM.
  const state = leafState[path];
  if (!state) return;
  state.params = Object.assign({}, state.original_params || {});
  state.window = Object.assign({}, state.original_window || {});
  renderAllNodeTreesFromWorking();
  refreshPassStates();
  updateExport();
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

function syncSiblingVisToggles(leafPath, checked, sourceInput) {
  const selector = `[data-path="${escapeSelector(leafPath)}"] > .node-header > input.node-visible`;
  document.querySelectorAll(selector).forEach(inp => {
    if (inp === sourceInput) return;
    inp.checked = checked;
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
