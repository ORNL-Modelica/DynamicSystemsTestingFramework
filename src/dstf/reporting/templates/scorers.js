// ---- interactive.js: part 1 of 5 (scorers.js) ----
// interactive.js is authored as 5 fragments and concatenated, in the
// order named by ``INTERACTIVE_JS_PARTS`` in reporting/report_suite.py, into
// the single file the report page loads. The fragments are contiguous slices
// of one script: never reorder them and never move a declaration across a
// seam — top-level ``const`` TDZ, function hoisting and the load-time IIFEs
// all depend on this exact sequence.
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
// Test-level baseline roster (overlay_loader.overlay_summary). Only the
// soft_check rows are offerable as an add-leaf "against" target.
const OVERLAY_ROWS = MT_REPORT.OVERLAY_ROWS || [];

// Variable name → index (element IDs). Order matches template iteration.
// review 2026-07-06 finding 76h: the template emits the ordered key list
// explicitly — JS Object.keys() reorders integer-like keys numerically,
// which desyncs plot element IDs from the Jinja loop.index0 numbering.
const VARIABLE_ORDER = MT_REPORT.VARIABLE_ORDER
  || Object.keys(VARIABLES_BY_NAME || {});
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

// review 2026-07-06 finding 38: JSON round-trip deep copy. leafState params
// must not share nested arrays (tube_points / points / events / peaks) with
// the tree or with original_params — shared references made in-place edits
// invisible to the export diff and unrecoverable by the reset button.
function _deepCopy(obj) {
  return obj == null ? obj : JSON.parse(JSON.stringify(obj));
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
    // review 2026-07-06 finding 39: 'absolute' is a legacy read-alias for
    // the canonical 'abs' token (matches Python modes.py Literal) —
    // normalize on load, always store/export 'abs'.
    if (merged.tube_width_mode === 'absolute') merged.tube_width_mode = 'abs';
    leafState[leaf.path] = {
      params: _deepCopy(merged),
      window: Object.assign({}, leaf.window || {}),
      visible: true,
      original_params: _deepCopy(merged),
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

// The baseline curve a leaf scores against: the primary reference, or —
// for a leaf with ``against: "<name>"`` — that soft_check's trajectory,
// which the page already embeds as the plot overlay. Every scorer and
// plot contribution resolves the reference side through here, so an
// against-leaf re-scores and draws against the curve it actually names
// instead of silently using the primary (D105's display limit).
//
// ``embedded: false`` means the named baseline carried no data for this
// variable — nothing to score or draw against. Those leaves are stamped
// CLI-authoritative (context_builder / _leafIsCliAuthoritative), so the
// scorer never runs for them; returning empty arrays keeps the plot
// honest rather than falling back to the wrong curve.
function _leafBaselineCurve(leaf, traj) {
  const vardata = VARIABLES_BY_NAME[(leaf && leaf.variable) || ''] || {};
  traj = traj || vardata.trajectory || {};
  const against = (leaf && leaf.against) || 'primary';
  if (against === 'primary') {
    return {
      name: 'primary',
      embedded: true,
      time: traj.ref_time || [],
      values: traj.ref_values || [],
    };
  }
  const overlays = vardata.overlays || traj.overlays || [];
  const ov = overlays.find(
    o => o && o.role === 'soft_check' && o.name === against);
  return {
    name: against,
    embedded: !!ov,
    time: ov ? (ov.time || []) : [],
    values: ov ? (ov.values || []) : [],
  };
}

// Same resolution, with the long-standing "no baseline at all → draw
// against the actual trace" fallback the tube editor relies on so a
// brand-new test (no stored reference yet) still gets a usable envelope
// to author against. Only the primary path falls back: a named baseline
// that isn't embedded has nothing legitimate to stand in for it.
function _leafBaselineOrActual(leaf, traj) {
  const vardata = VARIABLES_BY_NAME[(leaf && leaf.variable) || ''] || {};
  traj = traj || vardata.trajectory || {};
  const base = _leafBaselineCurve(leaf, traj);
  if (base.name === 'primary' && !base.time.length) {
    return { time: traj.act_time || [], values: traj.act_values || [] };
  }
  return { time: base.time, values: base.values };
}

// Legend label for a leaf's plot contribution. The legend used to carry
// the leaf's JSON pointer ("Tube /metrics/children/3") — an internal
// address, meaningless to a reader looking at a plot. Name it the way the
// tree node does: what it is, on which variable, against which baseline.
// Shapes keep the pointer in their ``name`` (the drag editors route by it);
// this is only for things the legend shows.
function leafTraceName(leaf, what) {
  const varname = leaf.variable || leaf.name || '';
  let label = varname ? `${what} · ${varname}` : what;
  if (leaf.against && leaf.against !== 'primary') label += ` [${leaf.against}]`;
  return label;
}

// ---- Pass/fail scorers — live recompute in the browser ----
// Re-derives each leaf's pass/fail from its current leafState[path].params
// against the pre-computed error metrics stored on the leaf (nrmse,
// max_abs_error, trajectory shapes). The CLI remains authoritative for
// event-timing (pairing not reimplemented JS-side) and for
// dominant-frequency leaves whose trajectory was decimated; those pills stay
// on whatever the evaluator decided — recomputePassStates skips the scorer
// for any leaf flagged ``cli_authoritative``.
// parity-test: each entry below mirrors a Python ``_compare_*`` function
// from src/dstf/comparison/comparator.py. Drift is caught by
// tests/test_scorer_parity.py — when changing math here, update the
// Python counterpart and the parity-test expectations.

// Width mode assumed when a tube leaf leaves ``tube_width_mode`` unset.
// Single source of truth: the scorer, the tube editor (getWidthMode) and the
// constant-tube plot contribution all default through this, so the drawn band
// is always the scored band. (Python raises on a missing mode, so the case
// only arises for specs the reporter itself is asked to render.)
const DEFAULT_TUBE_WIDTH_MODE = 'rel';

const MODE_SCORERS = {
  // Mirror of _compare_trajectories (algorithms.py). review 2026-07-06
  // finding 42: piecewise event segmentation (duplicate-time boundaries,
  // pre/post-event dedup interpolation) + _EPS-guarded constant-signal
  // normalization now mirror Python exactly; empty data FAILS (finding 1
  // parity — _no_data_failure).
  'nrmse': (leaf) => {
    const tol = _paramNumber(leaf, 'tolerance', leaf.tolerance_used);
    const traj = (VARIABLES_BY_NAME[leaf.variable] || {}).trajectory || {};
    const { refTime, refValues, actTime, actValues } =
        _sliceLeafTrajectory(leaf, traj);
    if (!refTime.length || !actTime.length) return false;  // _no_data_failure

    const boundaries = _findEventBoundariesJS(refTime);
    const segments = _splitSegmentsJS(refTime, refValues, boundaries);
    const [actTPre, actVPre] = _dedupTimeSeriesJS(actTime, actValues, 'first');
    const [actTPost, actVPost] = _dedupTimeSeriesJS(actTime, actValues, 'last');

    let sq = 0;
    let n = 0;
    const nSeg = segments.length;
    for (let s = 0; s < nSeg; s++) {
      const [segT, segV] = segments[s];
      if (!segT.length) continue;
      const isLast = s === nSeg - 1;
      for (let i = 0; i < segT.length; i++) {
        let aV;
        if (isLast || nSeg === 1) {
          // Last (or only) segment: post-event values.
          aV = _interpLinear(actTPost, actVPost, segT[i]);
        } else if (s === 0) {
          // First segment ending at an event: pre-event values.
          aV = _interpLinear(actTPre, actVPre, segT[i]);
        } else if (i === 0) {
          // Interior segment start: post-event value at the boundary.
          aV = _interpLinear(actTPost, actVPost, segT[0]);
        } else {
          aV = _interpLinear(actTPre, actVPre, segT[i]);
        }
        const d = aV - segV[i];
        sq += d * d;
        n++;
      }
    }
    if (n === 0) return false;  // no comparable samples after segmentation

    const rmse = Math.sqrt(sq / n);
    let refMin = refValues[0], refMax = refValues[0], refMagMax = 0;
    for (const v of refValues) {
      if (v < refMin) refMin = v;
      if (v > refMax) refMax = v;
      const a = Math.abs(v);
      if (a > refMagMax) refMagMax = a;
    }
    const range = refMax - refMin;
    // Constant-signal normalization: same _EPS (100 × float64 eps) and
    // magnitude fallback as Python (algorithms.py / types._EPS).
    let nrmse;
    if (range < _EPS_JS) {
      nrmse = refMagMax > _EPS_JS ? rmse / refMagMax : rmse;
    } else {
      nrmse = rmse / range;
    }
    return nrmse < tol;
  },
  // Mirror of _compare_points (algorithms.py). review 2026-07-06 finding
  // 43: fully-clipped checkpoints FAIL, ref-relative targets outside the
  // reference range FAIL, empty actual FAILS, and passing requires
  // scored > 0 — matches the post-review Python semantics exactly.
  'points': (leaf) => {
    const tol = _paramNumber(leaf, 'tolerance', leaf.tolerance_used);
    const traj = (VARIABLES_BY_NAME[leaf.variable] || {}).trajectory || {};
    const { refTime, refValues, actTime, actValues } =
        _sliceLeafTrajectory(leaf, traj);
    const params = (leafState[leaf.path] || {}).params || {};
    const points = Array.isArray(params.points) ? params.points : null;

    // Implicit final-only path: empty / null points → check act[-1] vs
    // ref[-1] with tolerance. Empty trajectory FAILS (Python parity).
    if (!points || points.length === 0) {
      if (!refValues.length || !actValues.length) return false;
      const refFinal = refValues[refValues.length - 1];
      const actFinal = actValues[actValues.length - 1];
      return Math.abs(actFinal - refFinal) < tol;
    }

    // Declared-points path.
    const traceEnd = refTime.length ? refTime[refTime.length - 1]
                   : actTime.length ? actTime[actTime.length - 1] : 0;
    let scored = 0;
    let failed = 0;
    for (const point of points) {
      const t = point.time == null ? traceEnd : Number(point.time);
      // Resolve target.
      let target;
      if (point.value != null) {
        target = Number(point.value);
      } else {
        if (!refTime.length) { failed++; continue; }  // ref-relative, no ref
        if (t < refTime[0] || t > refTime[refTime.length - 1]) {
          failed++;  // checkpoint time outside reference range
          continue;
        }
        target = _interpLinear(refTime, refValues, t);
      }
      // Resolve y-tolerance + mode.
      const perTol = point.tolerance != null ? Number(point.tolerance) : tol;
      const mode = point.tolerance_mode || 'abs';
      const yLimit = mode === 'rel' ? perTol * Math.abs(target) : perTol;
      // Box check.
      const xTol = point.time_tolerance != null
        ? Number(point.time_tolerance) : 0;
      if (!actTime.length) { failed++; continue; }  // empty trajectory
      const tLo = Math.max(t - xTol, actTime[0]);
      const tHi = Math.min(t + xTol, actTime[actTime.length - 1]);
      if (tHi < tLo) { failed++; continue; }  // fully clipped → FAIL
      const delta = _minDeltaInBox(actTime, actValues, tLo, tHi, target);
      scored++;
      if (delta > yLimit) failed++;
    }
    return scored > 0 && failed === 0;
  },
  // Mirror of _compare_range (algorithms.py).
  'range': (leaf) => {
    const p = (leafState[leaf.path] || {}).params || {};
    const traj = (VARIABLES_BY_NAME[leaf.variable] || {}).trajectory || {};
    const { actValues } = _sliceLeafTrajectory(leaf, traj);
    // review 2026-07-06 finding 6 parity: zero samples (empty trajectory
    // or window slicing away everything) FAILS — no vacuous pass.
    if (!actValues.length) return false;
    const mn = _nullOrNumber(p.min_value);
    const mx = _nullOrNumber(p.max_value);
    // No bounds at all is not "unbounded, everything passes" — Python
    // raises from RangeMode.__init__, so the leaf never scored. Passing
    // here would flip a CLI FAIL sentinel green on the first refresh.
    if (mn === null && mx === null) return false;
    for (const v of actValues) {
      if (mn !== null && v < mn) return false;
      if (mx !== null && v > mx) return false;
    }
    return true;
  },
  // Mirror of _compare_tube + _interpolate_tube_widths (algorithms.py).
  // review 2026-07-06 finding 40: the previous scorer scored every
  // tube_points value as a band offset regardless of tube_width_mode /
  // tube_interpolation. Now: interpolate RAW widths on the ref grid
  // (linear, or stepwise for tube_interpolation='constant'), then
  // dispatch on mode — 'rel' scales by |ref|, 'band' adds as offsets,
  // 'abs' treats interpolated upper/lower as literal y-bounds. The
  // tube_min_width floor applies to rel/band only. Empty data FAILS.
  'tube': (leaf) => {
    const traj = (VARIABLES_BY_NAME[leaf.variable] || {}).trajectory || {};
    const p = (leafState[leaf.path] || {}).params || {};
    // Window-clip both sides — Python receives pre-sliced arrays from
    // tree_eval and interpolates act on the windowed grid the same way.
    const { refTime, refValues, actTime, actValues } =
        _sliceLeafTrajectory(leaf, traj);
    if (!refTime.length || !actTime.length) return false;  // _no_data_failure

    // Missing/null mode defaults to DEFAULT_TUBE_WIDTH_MODE — the SAME
    // constant the tube editor (getWidthMode) and the constant-tube plot
    // contribution use, so what is drawn is what is scored. Python raises
    // ValueError for a missing mode, so no parity case is expressible;
    // unknown non-empty tokens still fail hard.
    const mode = _normTubeMode(p.tube_width_mode) || DEFAULT_TUBE_WIDTH_MODE;
    if (mode !== 'band' && mode !== 'rel' && mode !== 'abs') {
      return false;  // Python raises ValueError on unknown width mode
    }
    const minW = Number(p.tube_min_width || 0);
    const points = Array.isArray(p.tube_points) ? p.tube_points : [];

    let rawUpper, rawLower;
    if (points.length > 0) {
      const pts = [...points].sort((a, b) => Number(a.time) - Number(b.time));
      // Points carry upper/lower widths; Python raises when no point has
      // either, so that case is not expressible as a parity fixture.
      if (!pts.some(q => q.upper !== undefined || q.lower !== undefined)) return false;
      const ctrlT = pts.map(q => Number(q.time));
      const ctrlU = pts.map(q => Number(q.upper ?? 0));
      const ctrlL = pts.map(q => Number(q.lower ?? 0));
      const interp = (p.tube_interpolation === 'constant' || pts.length === 1)
        ? _interpStep : _interpLinear;
      rawUpper = refTime.map(t => interp(ctrlT, ctrlU, t));
      rawLower = refTime.map(t => interp(ctrlT, ctrlL, t));
    } else {
      // Constant-tube shorthand — only 'band' and 'rel'; Python raises
      // for 'abs' without tube_points.
      if (mode === 'abs') return false;
      const c = mode === 'band' ? Number(p.tube_abs || 0)
                                : Number(p.tube_rel || 0);
      rawUpper = refTime.map(() => c);
      rawLower = rawUpper;
    }

    for (let i = 0; i < refTime.length; i++) {
      const actV = _interpLinear(actTime, actValues, refTime[i]);
      let upper, lower;
      if (mode === 'abs') {
        upper = rawUpper[i];   // literal y-axis bounds — no min-width floor
        lower = rawLower[i];
      } else {
        let uw = mode === 'rel' ? rawUpper[i] * Math.abs(refValues[i]) : rawUpper[i];
        let lw = mode === 'rel' ? rawLower[i] * Math.abs(refValues[i]) : rawLower[i];
        if (minW > 0) { uw = Math.max(uw, minW); lw = Math.max(lw, minW); }
        upper = refValues[i] + uw;
        lower = refValues[i] - lw;
      }
      if (actV > upper || actV < lower) return false;
    }
    return true;
  },
  // event-timing intentionally absent — CLI-authoritative (event pairing
  // algorithm stays Python-side).
  // Mirror of _compare_dominant_frequency (comparator.py:906). Power-of-2
  // FFT resampling makes bin frequencies bit-identical with Python — but
  // only on the SAME samples. When the embedded trajectory was LTTB-
  // decimated the two sides resample onto different grids, so those leaves
  // arrive with ``cli_authoritative`` set and recomputePassStates never
  // calls this scorer (see _leafIsCliAuthoritative).
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
    // review 2026-07-06: no usable spectrum FAILS (Python parity — the
    // CLI fails a too-short signal rather than passing vacuously).
    if (!freqs || freqs.length < 3) return false;
    // review 2026-07-06 finding 44: mirror Python's claiming + amplitude
    // floor — each declared peak must claim a DISTINCT actual peak, and
    // matches below _PEAK_MIN_REL_AMPLITUDE of the spectrum max are
    // ignored as noise.
    let maxMag = 0;
    for (const m of mags) if (m > maxMag) maxMag = m;
    const ampFloor = _PEAK_MIN_REL_AMPLITUDE_JS * maxMag;
    const claimed = new Set();
    for (const pk of pks) {
      const f = Number(pk.freq);
      // review 2026-07-06 finding 76b: default tolerance 0.01 (Python's
      // declared.get("tolerance", 0.01)), not 0.
      const tol = pk.tolerance == null ? 0.01 : Number(pk.tolerance);
      if (!(f > 0)) return false;
      const [lo, hi] = pk.tolerance_mode === 'abs'
        ? [f - tol, f + tol]
        : [f * (1 - tol), f * (1 + tol)];
      const match = _findStrongestPeakInWindowJS(freqs, mags, lo, hi, {
        exclude: claimed,
        minAmplitude: ampFloor,
      });
      if (!match) return false;
      claimed.add(match.freq);
    }
    return true;
  },
};

// review 2026-07-06 finding 42: mirror of Python types._EPS
// (= 100 * np.finfo(float64).eps; Number.EPSILON === float64 eps).
const _EPS_JS = 100 * Number.EPSILON;

// review 2026-07-06 finding 44: mirror of algorithms._PEAK_MIN_REL_AMPLITUDE.
const _PEAK_MIN_REL_AMPLITUDE_JS = 0.01;

// review 2026-07-06 finding 39: 'absolute' accepted as a legacy read-alias
// only; canonical token everywhere is 'abs' (Python modes.py Literal).
function _normTubeMode(m) {
  return m === 'absolute' ? 'abs' : m;
}

// Any FINITE value passes through — the CLI scores whatever the patch
// carries (comparator.py is-not-None resolve chain: 0 is the strictest
// setting, a negative fails everything), and buildPatchData exports the
// raw param, so any live-side clamp diverges the pill from the exported
// patch. Only unset counts as unset: cleared fields arrive as undefined /
// null / '' (onLeafFieldChange writes null for a cleared input), which
// Number() would otherwise map to 0.
function _paramNumber(leaf, field, fallback) {
  const p = (leafState[leaf.path] || {}).params || {};
  const v = p[field];
  if (v == null || (typeof v === 'string' && v.trim() === '')) return fallback;
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
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

// Stepwise ("constant") tube-width interpolation, the companion to
// _interpLinear. Lived beside the tube editor until the interactive.js
// split — MODE_SCORERS['tube'] calls it, so it belongs on the scorer side of
// the seam that keeps scorers.js loadable (and parity-testable) on its own.
// The tube editor still calls it; a hoisted function declaration is visible
// to every fragment of the concatenated script.
function _interpStep(xArr, yArr, x) {
  if (!xArr.length) return 0;
  if (x <= xArr[0]) return yArr[0];
  let val = yArr[0];
  for (let i = 0; i < xArr.length; i++) {
    if (xArr[i] <= x) val = yArr[i]; else break;
  }
  return val;
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
      // A CLI-authoritative leaf keeps the CLI verdict even when a scorer
      // exists — a decimated dominant-frequency leaf would otherwise be
      // re-scored on a different FFT bin grid than the pill it replaces.
      const scorer = node.cli_authoritative ? null : MODE_SCORERS[node.metric];
      const p = scorer ? scorer(node) : !!node.passed;
      out[node.path] = p;
      return p;
    }
    const childResults = (node.children || []).map(x => !!walk(x));
    let passed;
    switch (node.combinator) {
      case 'and': passed = childResults.every(x => x); break;
      case 'or': passed = childResults.some(x => x); break;
      case 'warn': passed = true; break;
      case 'k-of-n':
        passed = childResults.filter(x => x).length >= (node.k || childResults.length);
        break;
      case 'weighted':
        // Needs the score-weighted sum, which wants per-leaf scores the
        // page doesn't carry — the CLI verdict stands (the header's
        // CLI-authoritative badge says so). A weighted node the user just
        // created in the browser has NO CLI verdict; publish it as
        // pending (null → "—" pill) instead of coercing undefined to a
        // bare unexplained FAIL. Ancestors still read it as not-passing:
        // an unknown verdict is not a pass.
        if (node.passed === undefined || node.passed === null) {
          out[node.path] = null;
          return false;
        }
        passed = !!node.passed;
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
      // null = "no verdict available here" (a CLI-deferred combinator the
      // user created in the browser). statusPill renders the same neutral
      // placeholder on first paint; keep it rather than inventing a FAIL.
      if (passed === null || passed === undefined) {
        pill.className = 'node-status pending';
        pill.textContent = '—';
        return;
      }
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
    // Checkpoint targets resolve against the leaf's own baseline.
    const { time: refTime, values: refValues } = _leafBaselineCurve(leaf, traj);
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
        // Name embeds the leaf path + point index so the editor's
        // plotly_relayout listener can route box edits to the right
        // pt.time_tolerance / pt.tolerance fields. Plotly's global
        // edits.shapePosition=true makes this draggable; the listener
        // re-derives tolerances from the new bounds and re-renders so
        // the box always snaps back centered on the point.
        name: `points_box:${leaf.path}:${xs.length - 1}`,
      });
    }
    if (!xs.length) return { traces: [], shapes };
    const traces = [{
      x: xs, y: ys, mode: 'markers', type: 'scatter',
      name: leafTraceName(leaf, 'Points'),
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

    const shapes = [];
    const traces = [];

    // One bound renders as: the in-window RED segment as a SHAPE (that is
    // the authoritative, draggable one — MODE_PLOT_EDITORS['range'] routes
    // plotly_relayout back to min_value/max_value by the shape's name), and
    // the out-of-window GRAY segments as TRACES.
    //
    // The gray segments used to be shapes too. Plotly's global
    // ``edits.shapePosition`` makes EVERY shape draggable — per-shape
    // ``editable: false`` does not override it (verified against the
    // vendored plotly 2.35.0) — so the gray segments accepted a drag that
    // the relayout handler deliberately ignores, and they snapped back on
    // the next render. A trace is never draggable, so as a trace the
    // segment is pinned by construction rather than by a handler that
    // declines to listen.
    const buildBound = (yVal, nameSuffix) => {
      if (!Number.isFinite(yVal)) return;
      if (!hasWindow) {
        shapes.push({
          type: 'line', xref: 'paper', x0: 0, x1: 1, yref: 'y',
          y0: yVal, y1: yVal,
          line: { color: '#f44336', width: 1.5, dash: 'dash' },
          name: `range_${nameSuffix}:${leaf.path}`,
        });
        return;
      }
      // Window-aware: three segments, all in data coords so they sit
      // under the right regions of the x axis.
      const refTime = traj.ref_time || traj.act_time || [];
      const tStart = refTime.length ? refTime[0] : 0;
      const tEnd = refTime.length ? refTime[refTime.length - 1] : 1;
      const wStart = Number(w.start);
      const wEnd = Number(w.end);
      const grayLine = (x0, x1, part) => ({
        x: [x0, x1], y: [yVal, yVal],
        type: 'scatter', mode: 'lines',
        line: { color: '#9e9e9e', width: 1.5, dash: 'dash' },
        name: `range_${nameSuffix}_${part}:${leaf.path}`,
        showlegend: false, hoverinfo: 'skip',
      });
      traces.push(grayLine(tStart, wStart, 'pre'));
      shapes.push({
        type: 'line', xref: 'x', x0: wStart, x1: wEnd, yref: 'y',
        y0: yVal, y1: yVal,
        line: { color: '#f44336', width: 1.5, dash: 'dash' },
        name: `range_${nameSuffix}:${leaf.path}`,
      });
      traces.push(grayLine(wEnd, tEnd, 'post'));
    };

    const mn = _nullOrNumber(p.min_value);
    const mx = _nullOrNumber(p.max_value);
    if (mn !== null) buildBound(mn, 'min');
    if (mx !== null) buildBound(mx, 'max');
    // Plotly autorange (and the double-click reset) ignores shapes — it
    // only sees trace data. Without an anchor trace the y-axis snaps to
    // the trajectory envelope and a far-out-of-window bound disappears.
    // Emit one invisible scatter point per declared bound so autorange
    // includes them in the reset range.
    const refTime = traj.ref_time || traj.act_time || [];
    const xAnchor = refTime.length ? refTime[0] : 0;
    const pushAnchor = (yVal) => {
      traces.push({
        x: [xAnchor], y: [yVal],
        mode: 'markers',
        marker: { color: 'rgba(0,0,0,0)', size: 0.1 },
        showlegend: false, hoverinfo: 'skip',
      });
    };
    if (mn !== null) pushAnchor(mn);
    if (mx !== null) pushAnchor(mx);
    return { traces, shapes };
  },
  'tube': (leaf, traj) => {
    // Envelope is drawn around the curve this leaf SCORES against — an
    // against-leaf's tube used to be painted around the primary while
    // being scored (CLI-side) against the soft_check.
    const base = _leafBaselineCurve(leaf, traj);
    const refTime = base.time;
    const refValues = base.values;
    if (!refTime.length) return { traces: [], shapes: [] };
    const p = (leafState[leaf.path] || {}).params || {};
    const points = Array.isArray(p.tube_points) ? p.tube_points : [];
    const editor = MODE_PLOT_EDITORS['tube'];

    let upper, lower;
    if (points.length > 0 && editor && editor._resolveAllBoundsOnGrid) {
      // Delegate to the editor's resolver so per-point / per-side modes
      // flow through to the polygon. Same code path the table + pass/fail
      // use — one source of truth for "what bounds does this config imply?"
      const r = editor._resolveAllBoundsOnGrid(leaf, refTime);
      upper = r.upper;
      lower = r.lower;
    } else {
      // Scalar constant tube (no authored points) — fall back to width-mode
      // arithmetic against ref values.
      const rel = Number(p.tube_rel || 0);
      const abs = Number(p.tube_abs || 0);
      const minW = Number(p.tube_min_width || 0);
      // review 2026-07-06 finding 39: 'absolute' → 'abs'. An unset mode
      // takes DEFAULT_TUBE_WIDTH_MODE, the same default the scorer applies —
      // drawing max(abs, rel*|v|) there painted a band the score ignored.
      const mode = _normTubeMode(p.tube_width_mode) || DEFAULT_TUBE_WIDTH_MODE;
      const widths = refValues.map(v => {
        if (mode === 'rel') return Math.max(minW, rel * Math.abs(v));
        if (mode === 'band') return Math.max(minW, abs);
        return Math.max(minW, Math.max(abs, rel * Math.abs(v)));
      });
      upper = refValues.map((v, i) => v + widths[i]);
      lower = refValues.map((v, i) => v - widths[i]);
    }

    const traces = [{
      x: refTime.concat([...refTime].reverse()),
      y: upper.concat([...lower].reverse()),
      fill: 'toself',
      fillcolor: 'rgba(76,175,80,0.15)',
      line: { color: 'transparent' },
      name: leafTraceName(leaf, 'Tube'),
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
        name: leafTraceName(leaf, 'Tube upper pts'), hoverinfo: 'x+y',
        showlegend: false,
      });
      traces.push({
        x: xs, y: los, mode: 'markers', type: 'scatter',
        marker: { color: '#c62828', size: 10, symbol: 'triangle-down',
                  line: { color: 'white', width: 1 } },
        name: leafTraceName(leaf, 'Tube lower pts'), hoverinfo: 'x+y',
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
    const refEvents = _detectEvents(_leafBaselineCurve(leaf, traj).time);
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
  // "ref" here is whatever baseline THIS leaf scores against — the
  // primary reference, or the named soft_check's embedded overlay curve
  // for an ``against`` leaf.
  const baseline = _leafBaselineCurve(leaf, traj);
  const refTime = baseline.time;
  const refValues = baseline.values;
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

function _findStrongestPeakInWindowJS(freqs, spectrum, lo, hi, opts) {
  // Mirrors Python _find_strongest_peak_in_window. Returns {freq, amplitude}
  // of the strongest local max in [lo, hi], or null if none.
  // review 2026-07-06 finding 44: optional ``opts.exclude`` (Set of already
  // claimed bin frequencies) and ``opts.minAmplitude`` (noise floor) mirror
  // the Python signature's exclude / min_amplitude parameters.
  if (!freqs || freqs.length < 3) return null;
  const exclude = opts && opts.exclude;
  const minAmplitude = (opts && opts.minAmplitude) || 0;
  let best = null;
  for (let i = 1; i < freqs.length - 1; i++) {
    if (freqs[i] < lo || freqs[i] > hi) continue;
    if (spectrum[i] > spectrum[i - 1] && spectrum[i] > spectrum[i + 1]) {
      if (minAmplitude > 0 && spectrum[i] < minAmplitude) continue;
      if (exclude && exclude.has(freqs[i])) continue;
      if (best === null || spectrum[i] > best.amplitude) {
        best = { freq: freqs[i], amplitude: spectrum[i] };
      }
    }
  }
  return best;
}

function _findEventBoundariesJS(time) {
  // Mirror of Python _find_event_boundaries (algorithms.py): groups runs
  // of CONSECUTIVE duplicate time values, returning [firstDup, lastDup]
  // index pairs. Dymola may emit 2 OR 3 duplicates per event.
  const boundaries = [];
  if (!time || time.length < 2) return boundaries;
  let i = 1;
  while (i < time.length) {
    if (time[i] === time[i - 1]) {
      const firstDup = i;
      while (i < time.length && time[i] === time[firstDup - 1]) i++;
      boundaries.push([firstDup, i - 1]);
    } else {
      i++;
    }
  }
  return boundaries;
}

function _splitSegmentsJS(time, values, boundaries) {
  // Mirror of Python _split_segments: pre-event value ends the previous
  // segment, post-event value starts the next; intermediate dups skipped.
  if (!boundaries.length) return [[time, values]];
  const segments = [];
  let prev = 0;
  for (const [firstDup, lastDup] of boundaries) {
    segments.push([time.slice(prev, firstDup), values.slice(prev, firstDup)]);
    prev = lastDup;
  }
  if (prev < time.length) {
    segments.push([time.slice(prev), values.slice(prev)]);
  }
  return segments;
}

function _dedupTimeSeriesJS(time, values, keep) {
  // Mirror of Python _dedup_time_series: drop duplicate time entries,
  // keeping the 'first' or 'last' value at each duplicated t.
  const outT = [], outV = [];
  if (keep === 'first') {
    for (let i = 0; i < time.length; i++) {
      if (i === 0 || time[i] !== time[i - 1]) {
        outT.push(time[i]); outV.push(values[i]);
      }
    }
  } else {
    for (let i = 0; i < time.length; i++) {
      if (i === time.length - 1 || time[i] !== time[i + 1]) {
        outT.push(time[i]); outV.push(values[i]);
      }
    }
  }
  return [outT, outV];
}

function _detectEvents(time) {
  // Modelica events manifest as CONSECUTIVE samples at the same ``t``.
  // review 2026-07-06 finding 47: group a run of duplicates into ONE
  // event (mirror Python _find_event_boundaries) — Dymola's 3-duplicate
  // events used to seed two identical declared rows.
  return _findEventBoundariesJS(time || []).map(b => time[b[0]]);
}

