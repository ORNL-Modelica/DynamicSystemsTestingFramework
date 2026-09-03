// ---- interactive.js: part 2 of 5 (editors.js) ----
// interactive.js is authored as 5 fragments and concatenated, in the
// order named by ``INTERACTIVE_JS_PARTS`` in reporting/report_suite.py, into
// the single file the report page loads. The fragments are contiguous slices
// of one script: never reorder them and never move a declaration across a
// seam — top-level ``const`` TDZ, function hoisting and the load-time IIFEs
// all depend on this exact sequence.
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
//     - synced + abs:       Time | Upper | Lower | ✕
//     - unsynced:           Time | Upper | Mode | Lower | Mode | ✕
//   * Width mode selector (rel / band / abs) — global. The per-side
//     selectors shown when unsynced are display/input-interpretation
//     aids only; stored values ALWAYS live in the global mode (review
//     2026-07-06 finding 46).
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

  // Max screen-pixel travel between mousedown and mouseup that still counts
  // as a click (rather than a drag) for the Shift+click add gesture.
  const CLICK_SLOP_PX = 5;

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
      const drag = { active: false, anchor: null, clickStart: null, clickStartPx: null };
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
          drag.clickStartPx = { x: evt.clientX, y: evt.clientY };
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
          // Cancel the pending add only on a real drag. The threshold is in
          // SCREEN pixels: a data-unit epsilon (the previous 1e-10) is many
          // orders of magnitude below one pixel of trackpad jitter on any
          // real axis range, so Shift+click frequently added nothing.
          const s = drag.clickStartPx;
          if (!s) return;
          if (Math.hypot(evt.clientX - s.x, evt.clientY - s.y) > CLICK_SLOP_PX) {
            drag.clickStart = null;
            drag.clickStartPx = null;
          }
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
          drag.clickStartPx = null;
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

// ---- createDeclaredItemsTable — shared scaffold for declared-list editors ----
//
// The three "declared-list" modes (event-timing, points, dominant-
// frequency) all render a table of user-authored items with per-row
// number/select inputs, an optional live Match column, a delete button,
// and a button row with +add plus mode-specific extras (Detect,
// Snapshot, source dropdown). This helper handles slot mounting, the
// table shell, and the refresh lifecycle so each editor only has to
// declare its column accessors and match-evaluation logic.
//
// Each mode supplies:
//   {
//     rootClassName,             // class for the per-slot root <div>
//     tableClassName,            // class for the <table>
//     getItems(leaf),            // -> array of items (mutated in place)
//     columns: [
//       { label, cell(item, leaf, ctx) -> HTMLElement },
//       ...
//     ],
//     evaluateMatches?: (leaf) => Array,        // optional live match data
//     renderMatch?: (match, item, leaf) => HTMLElement,  // required iff evaluateMatches
//     addItem(leaf),                            // called by +add
//     onRemove?: (leaf, idx, item),             // optional post-delete hook
//     emptyMessage?: string | HTMLElement,      // shown when items=[]
//     addLabel?: string,                        // +add button label (default '+ add')
//     hint?: string,                            // HTML, rendered above the table
//     emptyRowClassName?: string,               // class for the empty <tr>
//     extraButtons?: Array<(leaf, ctx) => HTMLElement | null>,  // appended after +add
//     footer?: (leaf, ctx) => HTMLElement | null,  // appended below the button row
//   }
//
// `emptyMessage` may be a string (HTML), an HTMLElement, or a factory
// `(leaf) => string|HTMLElement` invoked on every refresh.
//
// `ctx` passed to cell + extraButton closures:
//   { commit, refresh: () => void }
//
// `refresh()` re-renders the table in every mounted slot for the leaf.
// Helper-built handlers (delete, +add) call refresh + commit; cells
// call them as needed after their own mutations.
//
// Returns { activate(leaf, commit), refresh(leaf, commit), deactivate(leaf) }.

function createDeclaredItemsTable(spec) {
  const containersByPath = new Map();   // leaf.path -> [HTMLElement, ...]

  function _ensureContainers(leaf) {
    const slots = Array.from(document.querySelectorAll(
      `[data-path="${escapeSelector(leaf.path)}"] .node-editor`,
    ));
    const containers = [];
    for (const slot of slots) {
      let root = slot.querySelector(`:scope > .${spec.rootClassName}`);
      if (!root) {
        root = document.createElement('div');
        root.className = spec.rootClassName;
        slot.appendChild(root);
      }
      containers.push(root);
    }
    containersByPath.set(leaf.path, containers);
    return containers;
  }

  function refresh(leaf, commit) {
    const containers = containersByPath.get(leaf.path) || _ensureContainers(leaf);
    const ctx = { commit, refresh: () => refresh(leaf, commit) };
    const items = spec.getItems(leaf);
    const matches = spec.evaluateMatches ? spec.evaluateMatches(leaf) : null;
    for (const root of containers) _renderInto(root, leaf, items, matches, ctx);
  }

  function _renderInto(root, leaf, items, matches, ctx) {
    root.innerHTML = '';

    if (spec.hint) {
      const h = document.createElement('div');
      h.className = 'editor-hint';
      h.innerHTML = spec.hint;
      root.appendChild(h);
    }

    const table = document.createElement('table');
    table.className = spec.tableClassName;
    const thead = document.createElement('thead');
    const headRow = document.createElement('tr');
    for (const col of spec.columns) {
      const th = document.createElement('th');
      th.textContent = col.label;
      headRow.appendChild(th);
    }
    if (spec.renderMatch) {
      const th = document.createElement('th');
      th.textContent = 'Match (live)';
      headRow.appendChild(th);
    }
    headRow.appendChild(document.createElement('th'));  // delete col
    thead.appendChild(headRow);
    table.appendChild(thead);

    const tbody = document.createElement('tbody');
    if (items.length === 0 && spec.emptyMessage != null) {
      const tr = document.createElement('tr');
      if (spec.emptyRowClassName) tr.className = spec.emptyRowClassName;
      const td = document.createElement('td');
      td.colSpan = spec.columns.length + (spec.renderMatch ? 1 : 0) + 1;
      const msg = typeof spec.emptyMessage === 'function'
        ? spec.emptyMessage(leaf) : spec.emptyMessage;
      if (typeof msg === 'string') td.innerHTML = msg;
      else td.appendChild(msg);
      tr.appendChild(td);
      tbody.appendChild(tr);
    } else {
      items.forEach((item, i) => {
        const tr = document.createElement('tr');
        for (const col of spec.columns) {
          const td = document.createElement('td');
          td.appendChild(col.cell(item, leaf, ctx));
          tr.appendChild(td);
        }
        if (spec.renderMatch) {
          const td = document.createElement('td');
          td.className = 'match-cell';
          td.appendChild(spec.renderMatch(matches ? matches[i] : null, item, leaf));
          tr.appendChild(td);
        }
        const delTd = document.createElement('td');
        const delBtn = document.createElement('button');
        delBtn.className = 'row-delete';
        delBtn.textContent = '✕';
        delBtn.title = 'Remove this row';
        delBtn.addEventListener('click', () => {
          items.splice(i, 1);
          if (spec.onRemove) spec.onRemove(leaf, i, item);
          ctx.refresh();
          ctx.commit();
        });
        delTd.appendChild(delBtn);
        tr.appendChild(delTd);
        tbody.appendChild(tr);
      });
    }
    table.appendChild(tbody);
    root.appendChild(table);

    const btnRow = document.createElement('div');
    btnRow.className = 'editor-button-row';

    const addBtn = document.createElement('button');
    addBtn.className = 'node-btn node-btn-add';
    addBtn.textContent = spec.addLabel || '+ add';
    addBtn.addEventListener('click', () => {
      spec.addItem(leaf);
      ctx.refresh();
      ctx.commit();
    });
    btnRow.appendChild(addBtn);

    if (spec.extraButtons) {
      for (const factory of spec.extraButtons) {
        const el = factory(leaf, ctx);
        if (el) btnRow.appendChild(el);
      }
    }
    root.appendChild(btnRow);

    if (spec.footer) {
      const f = spec.footer(leaf, ctx);
      if (f) root.appendChild(f);
    }
  }

  return {
    activate(leaf, commit) {
      _ensureContainers(leaf);
      refresh(leaf, commit);
    },
    refresh,
    deactivate(leaf) {
      const containers = containersByPath.get(leaf.path) || [];
      for (const root of containers) if (root.parentNode) root.remove();
      containersByPath.delete(leaf.path);
    },
  };
}

MODE_PLOT_EDITORS['tube'] = (function() {
  const editorState = {};   // leafPath → {synced, perPointModes}

  // ---- state accessors ------------------------------------------------
  function getParams(leaf) { return (leafState[leaf.path] || {}).params || {}; }
  function getWidthMode(leaf) {
    // review 2026-07-06 finding 39: canonical token is 'abs' (Python
    // modes.py Literal); 'absolute' accepted as a legacy read-alias.
    const m = _normTubeMode(getParams(leaf).tube_width_mode);
    return (m === 'band' || m === 'rel' || m === 'abs')
      ? m : DEFAULT_TUBE_WIDTH_MODE;
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
    // Baseline-aware: an ``against`` leaf's tube is centred on the named
    // soft_check's curve, the one it is scored against, not the primary.
    const { time: rt, values: rv } = _leafBaselineOrActual(leaf);
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
    if (mode === 'abs') {
      const band = rvAbs > 1e-15 ? roundSig(rvAbs * 0.05, 3) : fallbackBand;
      return { upper: roundSig(rv + band, 4), lower: roundSig(rv - band, 4) };
    }
    const band = rvAbs > 1e-15 ? roundSig(rvAbs * 0.05, 3) : fallbackBand;
    return { upper: band, lower: band };
  }

  // ---- width-mode conversion helpers (review 2026-07-06 finding 46) ----
  // Stored control-point values ALWAYS live in the single GLOBAL width
  // mode. These two convert a value between a mode's units and the
  // absolute y-axis position at the point's time, so per-side mode
  // selectors can act as input-interpretation aids without ever leaking
  // a differently-scaled value into storage / scoring / export.
  function widthToAbsY(leaf, pt, side, mode, value) {
    const rv = refValueAt(leaf, pt.time);
    if (mode === 'abs') return Number(value);
    const offset = mode === 'rel'
      ? Number(value) * Math.abs(rv)
      : Number(value);
    return side === 'upper' ? rv + offset : rv - offset;
  }
  function absYToWidth(leaf, pt, side, mode, yAbs) {
    const rv = refValueAt(leaf, pt.time);
    const rvAbs = Math.abs(rv);
    if (mode === 'abs') return roundSig(yAbs, 4);
    const offset = side === 'upper' ? yAbs - rv : rv - yAbs;
    if (mode === 'rel') {
      return rvAbs > 1e-15 ? roundSig(Math.max(offset / rvAbs, 0), 3) : 0.05;
    }
    return roundSig(Math.max(offset, 0), 3);
  }
  // Value shown in an unsynced row's input: the stored global-mode value
  // re-expressed in the row's per-side display mode.
  function displaySideValue(leaf, pt, side, sideMode) {
    const globalMode = getWidthMode(leaf);
    if (sideMode === globalMode) return pt[side];
    return absYToWidth(leaf, pt, side, sideMode,
      widthToAbsY(leaf, pt, side, globalMode, pt[side]));
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
    if (mode === 'abs') {
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
    const es = ensureEditorState(leaf);
    const isUpper = bound === 'upper';
    // review 2026-07-06 finding 46: a drag supplies an absolute y — store
    // it in the GLOBAL width mode always. Per-side modes are a display /
    // input-interpretation aid, never a storage format.
    pt[bound] = absYToWidth(leaf, pt, bound, getWidthMode(leaf), yAbs);
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
    const { time: rt, values: rv } = _leafBaselineOrActual(leaf);

    // Per-grid-point ref value so rel mode tracks the curve.
    const refAt = grid.map(t => (rt.length ? _interpLinear(rt, rv, t) : 0));

    const upper = grid.map((t, i) => {
      if (mode === 'abs') return rawUpperGrid[i];
      let w = mode === 'rel'
        ? rawUpperGrid[i] * Math.abs(refAt[i])
        : rawUpperGrid[i];  // 'band'
      if (minW > 0) w = Math.max(w, minW);
      return refAt[i] + w;
    });
    const lower = grid.map((t, i) => {
      if (mode === 'abs') return rawLowerGrid[i];
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
    return mode === 'rel' ? 'fraction' : mode === 'abs' ? 'y-value' : 'signal units';
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
          <option value="abs">abs (literal y-bounds)</option>
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
    if (es.synced && widthMode !== 'abs') {
      header.innerHTML = `<th>Time</th><th>Width (${unitLabel(widthMode)})</th><th></th>`;
    } else if (es.synced && widthMode === 'abs') {
      header.innerHTML = `<th>Time</th><th>Upper (y)</th><th>Lower (y)</th><th></th>`;
    } else {
      header.innerHTML = `<th>Time</th><th>Upper</th><th>Mode</th><th>Lower</th><th>Mode</th><th></th>`;
    }

    pts.forEach((pt, i) => {
      const row = table.insertRow();
      if (es.synced && widthMode !== 'abs') {
        row.appendChild(numberCell(pt.time, v => onPointField(leaf, i, 'time', v, commit), 'any'));
        row.appendChild(numberCell(pt.upper, v => onPointField(leaf, i, 'width', v, commit),
                                   widthMode === 'rel' ? '0.001' : 'any', 0));
        row.appendChild(removeCell(leaf, i, commit));
      } else if (es.synced && widthMode === 'abs') {
        row.appendChild(numberCell(pt.time, v => onPointField(leaf, i, 'time', v, commit), 'any'));
        row.appendChild(numberCell(pt.upper, v => onPointField(leaf, i, 'upper', v, commit), 'any'));
        row.appendChild(numberCell(pt.lower, v => onPointField(leaf, i, 'lower', v, commit), 'any'));
        row.appendChild(removeCell(leaf, i, commit));
      } else {
        const pm = es.perPointModes[i] || { upperMode: null, lowerMode: null };
        const uMode = pm.upperMode || widthMode;
        const lMode = pm.lowerMode || widthMode;
        // review 2026-07-06 finding 46: inputs DISPLAY in the per-side
        // mode; storage stays in the global mode (displaySideValue /
        // onPointField convert on the way in and out).
        row.appendChild(numberCell(pt.time, v => onPointField(leaf, i, 'time', v, commit), 'any'));
        row.appendChild(numberCell(displaySideValue(leaf, pt, 'upper', uMode),
                                   v => onPointField(leaf, i, 'upper', v, commit),
                                   uMode === 'rel' ? '0.001' : 'any'));
        row.appendChild(modeCell(uMode, m => onSideMode(leaf, i, 'upper', m, commit)));
        row.appendChild(numberCell(displaySideValue(leaf, pt, 'lower', lMode),
                                   v => onPointField(leaf, i, 'lower', v, commit),
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
    // review 2026-07-06 finding 39: canonical 'abs' token in the per-side
    // selector too (was 'absolute', which nothing else recognized).
    for (const opt of ['band', 'rel', 'abs']) {
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
    else if (field === 'upper' || field === 'lower') {
      // review 2026-07-06 finding 46: typed values are interpreted in the
      // row's per-side display mode, but STORED in the global mode.
      const es = ensureEditorState(leaf);
      const pm = es.perPointModes[i] || { upperMode: null, lowerMode: null };
      const globalMode = getWidthMode(leaf);
      const sideMode = (es.synced ? globalMode
        : (pm[field + 'Mode'] || globalMode));
      pt[field] = sideMode === globalMode
        ? value
        : absYToWidth(leaf, pt, field, globalMode,
            widthToAbsY(leaf, pt, field, sideMode, value));
    }
    if (field === 'time') sortPointsAndModes(leaf);
    refreshTables(leaf, commit);
    commit();
  }

  function onSideMode(leaf, i, side, newMode, commit) {
    // review 2026-07-06 finding 46: switching a per-side mode changes the
    // input's DISPLAY units only — stored values stay in the global width
    // mode, so scoring / polygon / export are unaffected by the toggle.
    const es = ensureEditorState(leaf);
    const pm = es.perPointModes[i];
    if (!pm) return;
    const oldMode = pm[side + 'Mode'] || getWidthMode(leaf);
    if (oldMode === newMode) return;
    pm[side + 'Mode'] = newMode;
    refreshTables(leaf, commit);
    commit();
  }

  function reprojectAllPoints(leaf, oldMode, newMode) {
    // In-place reprojection when the GLOBAL width mode changes —
    // iterates the live array and mutates each pt. Caller holds the pt
    // references; we keep them valid. review 2026-07-06 finding 39:
    // token-safe via the shared conversion helpers ('abs', not
    // 'absolute').
    getPoints(leaf).forEach(pt => {
      for (const side of ['upper', 'lower']) {
        pt[side] = absYToWidth(leaf, pt, side, newMode,
          widthToAbsY(leaf, pt, side, oldMode, pt[side]));
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

  // Finite values pass through unclamped — see _paramNumber. A 0
  // time_tolerance means "exact event instant", which Python accepts
  // (algorithms.py: max_delta <= time_tolerance).
  function getGlobalTolerance(leaf) {
    const st = leafState[leaf.path] || {};
    const raw = (st.params || {}).time_tolerance;
    if (raw == null || (typeof raw === 'string' && raw.trim() === '')) return 1e-3;
    const v = Number(raw);
    return Number.isFinite(v) ? v : 1e-3;
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

  function sortEvents(leaf) {
    getEvents(leaf).sort((a, b) => Number(a.time) - Number(b.time));
  }

  // Evaluate live match status for each declared event against the
  // ACTUAL signal's auto-detected events. Does NOT substitute for CLI
  // pass/fail — this is live feedback only, but the rows must agree with
  // the pill. Same pairing rule as the Python scorer (algorithms.py
  // _compare_event_timing, declared-events path): optimal 1-D interval
  // matching — sort the declared windows by RIGHT endpoint, give each the
  // SMALLEST unclaimed actual event inside it. Greedy nearest-unclaimed
  // (the rule this replaced) let a wide early window steal the only event
  // a later narrow window could match, showing a row red that the CLI
  // scores green.
  function evaluateLiveMatches(leaf) {
    const traj = getTrajectory(leaf);
    const actEvents = _detectEvents(traj.act_time || []);
    const evs = getEvents(leaf);
    const globalTol = getGlobalTolerance(leaf);
    const claimed = new Array(actEvents.length).fill(false);
    const out = evs.map(() => ({ matched: false, delta: null, at: null }));
    const windows = evs.map((ev, i) => {
      const target = Number(ev.time);
      const tol = ev.tolerance != null ? Number(ev.tolerance) : globalTol;
      return { i, lo: target - tol, hi: target + tol, target };
    });
    windows.sort((a, b) => a.hi - b.hi);
    for (const w of windows) {
      for (let j = 0; j < actEvents.length; j++) {
        // actEvents is time-sorted → the first unclaimed hit is the smallest.
        if (claimed[j] || actEvents[j] < w.lo || actEvents[j] > w.hi) continue;
        claimed[j] = true;
        out[w.i] = {
          matched: true,
          delta: Math.abs(actEvents[j] - w.target),
          at: actEvents[j],
        };
        break;
      }
    }
    return out;
  }

  const editor = createDeclaredItemsTable({
    rootClassName: 'event-timing-editor',
    tableClassName: 'event-table',
    addLabel: '+ add event',
    getItems: getEvents,
    columns: [
      {
        label: 'Time (s)',
        cell: (ev, leaf, ctx) => {
          const inp = document.createElement('input');
          inp.type = 'number'; inp.step = 'any';
          inp.value = ev.time != null ? String(ev.time) : '';
          inp.addEventListener('change', () => {
            const n = Number(inp.value);
            if (Number.isFinite(n)) ev.time = n;
            sortEvents(leaf);
            ctx.refresh();
            ctx.commit();
          });
          return inp;
        },
      },
      {
        label: 'Tolerance (s)',
        cell: (ev, leaf, ctx) => {
          const inp = document.createElement('input');
          inp.type = 'number'; inp.step = 'any';
          inp.placeholder = String(getGlobalTolerance(leaf));
          inp.value = ev.tolerance != null ? String(ev.tolerance) : '';
          inp.addEventListener('change', () => {
            const raw = inp.value.trim();
            if (raw === '') delete ev.tolerance;
            else {
              const n = Number(raw);
              // 0 is legal (exact instant, algorithms.py honors it);
              // negatives are authoring nonsense — reject visibly here.
              if (Number.isFinite(n) && n >= 0) ev.tolerance = n;
            }
            ctx.refresh();
            ctx.commit();
          });
          return inp;
        },
      },
    ],
    evaluateMatches: evaluateLiveMatches,
    renderMatch: (m) => {
      const span = document.createElement('span');
      if (m && m.matched) {
        span.innerHTML =
          '<span style="color:#2e7d32">✓ matched</span> '
          + `@ t=${m.at.toPrecision(4)} (Δ=${m.delta.toPrecision(2)})`;
      } else {
        span.innerHTML = '<span style="color:#c62828">✕ unmatched</span>';
      }
      return span;
    },
    addItem: (leaf) => {
      const evs = getEvents(leaf);
      const seedTime = evs.length ? Number(evs[evs.length - 1].time) + 0.5 : 0;
      evs.push({ time: seedTime });
      sortEvents(leaf);
    },
    extraButtons: [
      // Source dropdown — which signal Detect should scan.
      (leaf) => {
        const label = document.createElement('label');
        label.className = 'editor-hint';
        label.style.marginLeft = '1em';
        label.textContent = 'Detect from: ';
        label.title = (
          'Which signal to scan for duplicate-time samples when you click '
          + 'Detect. Reference = pick up events from the saved baseline; '
          + 'Actual = pick up events from this run (useful on a fresh test '
          + 'with no baseline).'
        );
        const sel = document.createElement('select');
        sel.className = 'detect-source-select';
        for (const [val, txt] of [['ref', 'Reference'], ['act', 'Actual']]) {
          const opt = document.createElement('option');
          opt.value = val; opt.textContent = txt;
          if (getDetectSource(leaf) === val) opt.selected = true;
          sel.appendChild(opt);
        }
        sel.addEventListener('change', () => setDetectSource(leaf, sel.value));
        label.appendChild(sel);
        return label;
      },
      // Detect button — replaces declared events with auto-detected.
      (leaf, ctx) => {
        const btn = document.createElement('button');
        btn.className = 'node-btn detect-events-btn';
        btn.textContent = '🔍 Detect events';
        btn.title = (
          'Replace the declared-events list with duplicate-time samples '
          + "auto-detected on the selected source signal. Uses Modelica's "
          + 'convention: two consecutive samples at the same t flag a '
          + 'solver event.'
        );
        btn.addEventListener('click', () => {
          const traj = getTrajectory(leaf);
          const source = getDetectSource(leaf);
          const times = source === 'act' ? (traj.act_time || []) : (traj.ref_time || []);
          const detected = _detectEvents(times);
          const globalTol = getGlobalTolerance(leaf);
          leafState[leaf.path].params.events = detected.map(
            t => ({ time: Number(t), tolerance: globalTol })
          );
          ctx.refresh();
          ctx.commit();
        });
        return btn;
      },
    ],
  });

  return {
    activate(leaf, _plotEl, commit) { editor.activate(leaf, commit); },
    deactivate(leaf, _plotEl) { editor.deactivate(leaf); },
    // Exposed for the Python<->JS pairing parity test (test_scorer_parity.py).
    _evaluateLiveMatches: evaluateLiveMatches,
  };
})();

// Points editor — declared-points table in the leaf slot.
// Each row authors one (time, value?, tolerance?, tolerance_mode,
// time_tolerance?) checkpoint. Pass/fail is computed live by
// MODE_SCORERS['points']; this editor just mutates leafState.
MODE_PLOT_EDITORS['points'] = (function() {

  function getPoints(leaf) {
    const st = leafState[leaf.path] || {};
    const p = st.params || (st.params = {});
    if (!Array.isArray(p.points)) p.points = [];
    return p.points;
  }

  // Finite values pass through unclamped — see _paramNumber.
  function getDefaultTolerance(leaf) {
    const st = leafState[leaf.path] || {};
    const raw = (st.params || {}).tolerance;
    if (raw == null || (typeof raw === 'string' && raw.trim() === '')) return 1e-4;
    const v = Number(raw);
    return Number.isFinite(v) ? v : 1e-4;
  }

  function getTrajectory(leaf) {
    return (VARIABLES_BY_NAME[leaf.variable] || {}).trajectory || {};
  }

  // Live match info per point: nearest delta inside the box, plus
  // the t at which it occurred. Used by the Match column.
  function evaluateLiveMatches(leaf) {
    const traj = getTrajectory(leaf);
    const sliced = _sliceLeafTrajectory(leaf, traj);
    const refTime = sliced.refTime;
    const refValues = sliced.refValues;
    const actTime = sliced.actTime;
    const actValues = sliced.actValues;
    const tol = getDefaultTolerance(leaf);
    const traceEnd = refTime.length ? refTime[refTime.length - 1]
                    : (actTime.length ? actTime[actTime.length - 1] : 0);
    return getPoints(leaf).map(pt => {
      let t = pt.time != null ? Number(pt.time) : traceEnd;
      let target;
      if (pt.value != null) target = Number(pt.value);
      else if (refTime.length) target = _interpLinear(refTime, refValues, t);
      else return { ok: null, delta: null, at: null };
      const perTol = pt.tolerance != null ? Number(pt.tolerance) : tol;
      const mode = pt.tolerance_mode || 'abs';
      const yLimit = mode === 'rel' ? perTol * Math.abs(target) : perTol;
      const xTol = pt.time_tolerance != null ? Number(pt.time_tolerance) : 0;
      if (!actTime.length) return { ok: null, delta: null, at: null };
      const tLo = Math.max(t - xTol, actTime[0]);
      const tHi = Math.min(t + xTol, actTime[actTime.length - 1]);
      if (tHi < tLo) return { ok: null, delta: null, at: null };
      const delta = _minDeltaInBox(actTime, actValues, tLo, tHi, target);
      // Best-t — use the canonical center for now (full search would
      // require returning t along with the min from _minDeltaInBox).
      // CLI-side reports the actual best_t; JS keeps it simple — Match
      // column shows delta only for points with x_tol = 0, plus the
      // box label "in box" for x_tol > 0.
      return { ok: delta <= yLimit, delta, at: t };
    });
  }

  // Resolved (target_x, target_y) for a point. Mirrors the box-drawing
  // and live-scoring math: ref-relative if ``value`` is null, else the
  // explicit value.
  function _resolvedXY(leaf, pt) {
    const traj = getTrajectory(leaf);
    const refTime = traj.ref_time || [];
    const refValues = traj.ref_values || [];
    const traceEnd = refTime.length ? refTime[refTime.length - 1] : 0;
    const t = pt.time != null ? Number(pt.time) : traceEnd;
    let y;
    if (pt.value != null) y = Number(pt.value);
    else if (refTime.length) y = _interpLinear(refTime, refValues, t);
    else y = 0;
    return { x: t, y };
  }

  function _resolvedYLimit(leaf, pt) {
    const tolDefault = getDefaultTolerance(leaf);
    const perTol = pt.tolerance != null ? Number(pt.tolerance) : tolDefault;
    const mode = pt.tolerance_mode || 'abs';
    const { y } = _resolvedXY(leaf, pt);
    return mode === 'rel' ? perTol * Math.abs(y) : perTol;
  }

  function _sortByTime(leaf) {
    getPoints(leaf).sort((a, b) => {
      const ta = a.time != null ? Number(a.time) : Infinity;
      const tb = b.time != null ? Number(b.time) : Infinity;
      return ta - tb;
    });
  }

  // Shared shift-modifier interaction layer for the diamond markers.
  // The box itself is editable via Plotly's native shape-drag (handled
  // by the plotly_relayout listener below), so anchors here are just
  // the point centers.
  //   * shift+click on empty plot     → add point (explicit value at click)
  //   * shift+drag on diamond         → move time + value (becomes
  //                                     explicit value if ref-relative)
  //   * shift+right-click on diamond  → delete point
  const _pointEditor = createPointPlotEditor({
    getAnchors(leaf) {
      return getPoints(leaf).map(pt => {
        const { x, y } = _resolvedXY(leaf, pt);
        return { pt, x, y };
      });
    },
    onClickAdd(leaf, _plotEl, x, y, commit) {
      const tDefault = getDefaultTolerance(leaf);
      getPoints(leaf).push({
        time: Number(x),
        value: Number(y),
        tolerance: tDefault,
        tolerance_mode: 'abs',
      });
      _sortByTime(leaf);
      tableEditor.refresh(leaf, commit);
      commit();
    },
    onDragStep(leaf, _plotEl, anchor, x, y, commit) {
      // Direct manipulation of the diamond writes both time + value, so
      // a previously-ref-relative point becomes anchored to the dragged
      // y. Mirrors how the tube editor treats drag — clear Value cell
      // in the table to un-anchor back to ref-relative.
      anchor.pt.time = Number(x);
      anchor.pt.value = Number(y);
      tableEditor.refresh(leaf, commit);
    },
    onDragEnd(leaf, _plotEl, _anchor, commit) {
      _sortByTime(leaf);
      tableEditor.refresh(leaf, commit);
    },
    onRemove(leaf, _plotEl, anchor, commit) {
      const points = getPoints(leaf);
      const idx = points.indexOf(anchor.pt);
      if (idx >= 0) {
        points.splice(idx, 1);
        tableEditor.refresh(leaf, commit);
        commit();
      }
    },
  });

  // Box-resize via Plotly's native shape-drag. PLOT_CFG sets
  // ``edits.shapePosition`` globally, so the user can drag any box
  // edge / corner / center. We listen for both ``plotly_relayouting``
  // (live, fires every drag step) and ``plotly_relayout`` (final,
  // fires on release) — relying only on the final event was unreliable
  // (Plotly skips it on rapid clicks / drag-cancel) and showed the box
  // lopsided during the drag.
  //
  // Edge-vs-translation detection looks at DELTAS from the canonical
  // previous bounds (derived from ``pt.time + pt.time_tolerance``),
  // NOT at which keys are present in evt. Plotly emits all four bounds
  // on every shape event regardless of which edge actually moved — so
  // checking key presence falsely flagged every edge drag as a
  // translation, which preserved the half-extent and snapped the box
  // back to its original position.
  //
  // Per-axis:
  //   * both deltas non-zero with same magnitude → translation → preserve
  //     half-extent (snap-back: dragging the box body doesn't grow it)
  //   * only one delta non-zero                  → that edge was dragged →
  //     new half-extent = |dragged_edge - point_center|
  //   * both zero                                → no change
  // Mirror the opposite edge via Plotly.relayout (recursion-guarded) so
  // the user sees the box grow/shrink symmetrically as they drag — the
  // edge they're dragging stays where their cursor is, the opposite
  // edge moves in lock step.
  let _updatingBox = false;
  function applyBoxRelayout(leaf, plotEl, evt, commit, isFinal) {
    if (_updatingBox) return false;
    if (!evt || !plotEl.layout) return false;
    const updatedShapes = new Set();
    for (const key of Object.keys(evt)) {
      const m = key.match(/^shapes\[(\d+)\]\.[xy][01]$/);
      if (m) updatedShapes.add(parseInt(m[1]));
    }
    if (updatedShapes.size === 0) return false;
    const points = getPoints(leaf);
    let mutated = false;
    const layoutUpdates = {};
    const eps = 1e-9;
    for (const shapeIdx of updatedShapes) {
      const shape = plotEl.layout.shapes?.[shapeIdx];
      if (!shape || !shape.name) continue;
      const m = shape.name.match(/^points_box:(.+):(\d+)$/);
      if (!m || m[1] !== leaf.path) continue;
      const ptIdx = parseInt(m[2]);
      const pt = points[ptIdx];
      if (!pt) continue;
      const { x: cx, y: cy } = _resolvedXY(leaf, pt);
      const prevHalfX = pt.time_tolerance != null
        ? Number(pt.time_tolerance) : 0;
      const prevHalfY = _resolvedYLimit(leaf, pt);
      const prevSizeX = 2 * prevHalfX;
      const prevSizeY = 2 * prevHalfY;
      const newX0 = Number(shape.x0);
      const newX1 = Number(shape.x1);
      const newY0 = Number(shape.y0);
      const newY1 = Number(shape.y1);
      const newSizeX = newX1 - newX0;
      const newSizeY = newY1 - newY0;
      // Per-axis: did the SIZE change? If size is unchanged but the
      // box has translated (centers don't match), it's a pure
      // translation → preserve half-extent. If size DID change, the
      // user dragged an edge — half-extent comes from whichever edge
      // moved further from canonical. The size-based check is robust
      // to floating-point noise from Plotly's pixel-to-data
      // conversion (per-edge delta comparison wasn't — Plotly's
      // translation can produce dx0 ≠ dx1 by 1e-7 or so, which
      // fooled the previous edge-delta comparison into thinking the
      // user dragged an edge).
      //
      // Threshold: 0.1% of the box size (or 1e-3 absolute floor).
      // A typical user-intent drag is several pixels = ≫ 1% of size,
      // so this comfortably ignores FP noise without rejecting any
      // visible drag. The prior 1e-6 threshold was tight enough that
      // noise on every translation drag added ~5e-6 to halfX, and
      // the box visibly grew over many drags ("snaps to middle but
      // the box keeps on getting bigger and bigger" — user report).
      const sizeXEps = Math.max(prevSizeX * 0.001, 1e-3);
      const sizeYEps = Math.max(prevSizeY * 0.001, 1e-3);
      const sizeXChanged = Math.abs(newSizeX - prevSizeX) > sizeXEps;
      const sizeYChanged = Math.abs(newSizeY - prevSizeY) > sizeYEps;
      let halfX;
      if (!sizeXChanged) {
        halfX = prevHalfX;       // translation in x (or no x change)
      } else {
        // Resize in x. Use whichever edge moved further from canonical
        // as the user's "intent" — that's the dragged edge.
        const dx0 = Math.abs(newX0 - (cx - prevHalfX));
        const dx1 = Math.abs(newX1 - (cx + prevHalfX));
        halfX = dx0 > dx1
          ? Math.abs(newX0 - cx)
          : Math.abs(newX1 - cx);
      }
      let halfY;
      if (!sizeYChanged) {
        halfY = prevHalfY;
      } else {
        const dy0 = Math.abs(newY0 - (cy - prevHalfY));
        const dy1 = Math.abs(newY1 - (cy + prevHalfY));
        halfY = dy0 > dy1
          ? Math.abs(newY0 - cy)
          : Math.abs(newY1 - cy);
      }
      pt.time_tolerance = halfX;
      const mode = pt.tolerance_mode || 'abs';
      if (mode === 'rel') {
        pt.tolerance = Math.abs(cy) > 0 ? halfY / Math.abs(cy) : 0;
      } else {
        pt.tolerance = halfY;
      }
      mutated = true;
      // Mirror — stage the symmetric bounds for a single Plotly.relayout.
      const mirrorX0 = cx - halfX;
      const mirrorX1 = cx + halfX;
      const mirrorY0 = cy - halfY;
      const mirrorY1 = cy + halfY;
      if (Math.abs(newX0 - mirrorX0) > eps)
        layoutUpdates[`shapes[${shapeIdx}].x0`] = mirrorX0;
      if (Math.abs(newX1 - mirrorX1) > eps)
        layoutUpdates[`shapes[${shapeIdx}].x1`] = mirrorX1;
      if (Math.abs(newY0 - mirrorY0) > eps)
        layoutUpdates[`shapes[${shapeIdx}].y0`] = mirrorY0;
      if (Math.abs(newY1 - mirrorY1) > eps)
        layoutUpdates[`shapes[${shapeIdx}].y1`] = mirrorY1;
    }
    if (!mutated) return false;
    tableEditor.refresh(leaf, commit);
    if (Object.keys(layoutUpdates).length > 0
        && typeof Plotly !== 'undefined') {
      _updatingBox = true;
      try {
        const r = Plotly.relayout(plotEl, layoutUpdates);
        if (r && typeof r.finally === 'function') {
          r.finally(() => { _updatingBox = false; });
        } else {
          _updatingBox = false;
        }
      } catch (_e) {
        _updatingBox = false;
      }
    }
    if (isFinal) {
      // Full re-render on release — refreshes diamond marker position,
      // pass states, export. Live events skip this to avoid fighting
      // Plotly's mid-drag state.
      commit();
    }
    return true;
  }

  // Per-plotEl listener bookkeeping so deactivate can clean up. Two
  // listeners are registered per activate: one each for relayouting +
  // relayout. The map stores both so detach can iterate.
  const boxListenerByPlot = new WeakMap();

  // ---- Cell factories — column-spec building blocks ----

  function _numberCell(field, opts = {}) {
    return (pt, leaf, ctx) => {
      const inp = document.createElement('input');
      inp.type = 'number'; inp.step = 'any';
      if (opts.placeholder) inp.placeholder = opts.placeholder(leaf, pt);
      inp.value = pt[field] != null ? String(pt[field]) : '';
      inp.addEventListener('change', () => {
        const raw = inp.value.trim();
        if (raw === '') {
          if (opts.allowNull) pt[field] = null;
          else delete pt[field];
        } else {
          const n = Number(raw);
          if (Number.isFinite(n) && (!opts.validate || opts.validate(n))) {
            pt[field] = n;
          }
        }
        ctx.refresh();
        ctx.commit();
      });
      return inp;
    };
  }

  function _modeCell(pt, leaf, ctx) {
    const sel = document.createElement('select');
    for (const m of ['abs', 'rel']) {
      const opt = document.createElement('option');
      opt.value = m; opt.textContent = m;
      if ((pt.tolerance_mode || 'abs') === m) opt.selected = true;
      sel.appendChild(opt);
    }
    // Convert the stored tolerance value when switching modes so the
    // VISIBLE box size stays the same. abs stores the half-height
    // directly in y-units; rel stores it as a fraction of |target|.
    //
    // Protection for "essentially zero" target on abs→rel: a small but
    // non-zero target (e.g., solver-near-zero ~1e-7) produces a huge
    // rel fraction when oldTol/targetAbs is computed. The previous
    // threshold (1e-12) was so tight that it only caught EXACTLY-zero
    // targets, and small numerical values like sin(t≈0) slipped
    // through. Now we judge by whether the resulting rel fraction
    // would be unreasonable (>10 = 1000%) — that catches both true
    // zeros and the near-zero cases the user actually hit.
    sel.addEventListener('change', () => {
      const oldMode = pt.tolerance_mode || 'abs';
      const newMode = sel.value;
      if (oldMode !== newMode) {
        const tolDef = getDefaultTolerance(leaf);
        const oldTol = pt.tolerance != null ? Number(pt.tolerance) : tolDef;
        const { y } = _resolvedXY(leaf, pt);
        const targetAbs = Math.abs(y);
        if (oldMode === 'abs' && newMode === 'rel') {
          const proposedRel = targetAbs > 0 ? oldTol / targetAbs : Infinity;
          if (Number.isFinite(proposedRel) && proposedRel <= 10) {
            pt.tolerance = proposedRel;
          } else {
            // Conversion would produce an enormous (or undefined) rel
            // fraction. Revert.
            sel.value = 'abs';
            ctx.refresh();
            return;
          }
        } else if (oldMode === 'rel' && newMode === 'abs') {
          pt.tolerance = oldTol * targetAbs;
        }
      }
      pt.tolerance_mode = newMode;
      ctx.refresh();
      ctx.commit();
    });
    return sel;
  }

  const tableEditor = createDeclaredItemsTable({
    rootClassName: 'points-editor',
    tableClassName: 'points-table',
    addLabel: '+ add point',
    getItems: getPoints,
    columns: [
      { label: 'Time', cell: _numberCell('time', { placeholder: () => 'final', allowNull: true }) },
      { label: 'X-Tolerance', cell: _numberCell('time_tolerance', { placeholder: () => '0', validate: n => n >= 0 }) },
      { label: 'Value', cell: _numberCell('value', { placeholder: () => 'ref(t)' }) },
      { label: 'Mode', cell: _modeCell },
      { label: 'Tolerance', cell: _numberCell('tolerance', {
          placeholder: (leaf) => String(getDefaultTolerance(leaf)),
          // 0 is legal (strictest; Python honors it, algorithms.py
          // fail rule delta > y_limit); negatives rejected at authoring.
          validate: n => n >= 0,
        }) },
    ],
    evaluateMatches: evaluateLiveMatches,
    renderMatch: (m) => {
      const span = document.createElement('span');
      const mm = m || {};
      if (mm.ok === null || mm.ok === undefined) {
        span.innerHTML = '<span style="color:#9e9e9e">·</span>';
      } else if (mm.ok) {
        span.innerHTML = `<span style="color:#2e7d32">✓ matched</span> `
                       + `(Δ=${mm.delta.toExponential(2)})`;
      } else {
        span.innerHTML = `<span style="color:#c62828">✕ unmatched</span> `
                       + `(Δ=${mm.delta.toExponential(2)})`;
      }
      return span;
    },
    addItem: (leaf) => {
      const points = getPoints(leaf);
      let seedTime = null;
      if (points.length) {
        const prev = points[points.length - 1];
        const prevT = prev.time != null ? Number(prev.time) : null;
        seedTime = prevT != null ? prevT + 0.5 : null;
      }
      points.push({ time: seedTime });
    },
    // Zero-point fast path: italic placeholder row reinforcing the
    // implicit final-only behavior. Re-rendered per edit so the
    // default-tolerance number is always current.
    emptyRowClassName: 'points-implicit-row',
    emptyMessage: (leaf) => {
      const span = document.createElement('span');
      span.style.fontStyle = 'italic';
      span.style.color = '#666';
      span.textContent = `final · uses ref[-1] · tolerance ${getDefaultTolerance(leaf)} · `
                       + `+ add point overrides`;
      return span;
    },
    extraButtons: [
      // 📸 Snapshot from ref — fills empty Value cells with current ref(time).
      (leaf, ctx) => {
        const btn = document.createElement('button');
        btn.className = 'node-btn snapshot-btn';
        btn.textContent = '📸 Snapshot from ref';
        btn.title = (
          'For every row whose Value is empty (ref-relative), fill in the '
          + 'current ref(time) as an explicit value. Converts ref-based '
          + 'points into baseline-free absolute points. Idempotent — rows '
          + 'with an explicit value are untouched.'
        );
        btn.addEventListener('click', () => {
          const traj = getTrajectory(leaf);
          const refTime = traj.ref_time || [];
          const refValues = traj.ref_values || [];
          if (!refTime.length) return;
          const traceEnd = refTime[refTime.length - 1];
          getPoints(leaf).forEach(pt => {
            if (pt.value != null) return;
            const t = pt.time != null ? Number(pt.time) : traceEnd;
            pt.value = _interpLinear(refTime, refValues, t);
          });
          ctx.refresh();
          ctx.commit();
        });
        return btn;
      },
    ],
  });

  return {
    activate(leaf, plotEl, commit) {
      tableEditor.activate(leaf, commit);
      if (plotEl) {
        _pointEditor.attach(leaf, plotEl, commit);
        const liveHandler = (evt) =>
          applyBoxRelayout(leaf, plotEl, evt, commit, false);
        const finalHandler = (evt) =>
          applyBoxRelayout(leaf, plotEl, evt, commit, true);
        plotEl.on('plotly_relayouting', liveHandler);
        plotEl.on('plotly_relayout', finalHandler);
        boxListenerByPlot.set(plotEl, [
          ['plotly_relayouting', liveHandler],
          ['plotly_relayout', finalHandler],
        ]);
      }
    },
    deactivate(leaf, plotEl) {
      if (plotEl) {
        _pointEditor.detach(plotEl);
        const handlers = boxListenerByPlot.get(plotEl);
        if (handlers && plotEl.removeListener) {
          for (const [evtName, handler] of handlers) {
            plotEl.removeListener(evtName, handler);
          }
          boxListenerByPlot.delete(plotEl);
        }
      }
      tableEditor.deactivate(leaf);
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
  //   * Table (via createDeclaredItemsTable): freq, tolerance, mode,
  //     match-live, src-window, remove.
  //   * Detect dropdown: source = Reference | Actual. Uses the live
  //     windowed spectrum, so "brush a window → Detect" works in one
  //     interaction cycle. Populates derived_from_window metadata on
  //     each added peak so provenance survives the patch round-trip.
  const slotsByLeaf = new WeakMap();   // leaf -> [{slot, subplotDiv}, ...]
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
  // matched_hz, delta}].
  // review 2026-07-06 finding 44: mirrors the scorer's claiming +
  // amplitude-floor semantics so the Match column agrees with the pill.
  function evaluateMatches(leaf) {
    const actSpectrum = getLiveSpectrum(leaf, 'act');
    const pks = getDeclaredPeaks(leaf);
    let maxMag = 0;
    for (const m of actSpectrum.magnitudes) if (m > maxMag) maxMag = m;
    const ampFloor = _PEAK_MIN_REL_AMPLITUDE_JS * maxMag;
    const claimed = new Set();
    return pks.map(pk => {
      const f = Number(pk.freq);
      // review 2026-07-06 finding 76b: default 0.01 (Python parity).
      const tol = pk.tolerance == null ? 0.01 : Number(pk.tolerance);
      const [lo, hi] = pk.tolerance_mode === 'abs'
        ? [f - tol, f + tol]
        : [f * (1 - tol), f * (1 + tol)];
      const match = _findStrongestPeakInWindowJS(
        actSpectrum.freqs, actSpectrum.magnitudes, lo, hi,
        { exclude: claimed, minAmplitude: ampFloor },
      );
      if (match) claimed.add(match.freq);
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
      const newPk = { freq: Number(x), tolerance: 0.01, tolerance_mode: 'rel' };
      const w = _windowCopy(getLeafWindow(leaf));
      if (w) newPk.derived_from_window = w;
      pks.push(newPk);
      sortDeclared(leaf);
      refreshAll(leaf, commit);
    },
    onDragStep(leaf, _plotEl, anchor, x, _y, commit) {
      if (!(x > 0)) return;
      anchor.pt.freq = Number(x);
      sortDeclared(leaf);
      refreshAll(leaf, commit);
    },
    onDragEnd(leaf, _plotEl, _anchor, commit) {
      sortDeclared(leaf);
      refreshAll(leaf, commit);
    },
    onRemove(leaf, _plotEl, anchor, commit) {
      const pks = getDeclaredPeaks(leaf);
      const idx = pks.indexOf(anchor.pt);
      if (idx >= 0) {
        pks.splice(idx, 1);
        refreshAll(leaf, commit);
      }
    },
  });

  // Full refresh — subplot + table + commit. Table refresh is delegated
  // to the helper.
  function refreshAll(leaf, commit) {
    refreshSubplotOnly(leaf);
    tableEditor.refresh(leaf, commit);
    commit();
  }

  // Lighter refresh — re-renders the Plotly subplot only, leaves the
  // table DOM untouched. Used from `input` event handlers on the table's
  // number inputs so mid-typing keystrokes don't destroy and recreate
  // the input element (which would drop focus and eat decimal points).
  // The full refresh (rebuilding the table with its sorted rows + live
  // match column) happens on `change` (blur / Enter).
  function refreshSubplotOnly(leaf) {
    const mounted = slotsByLeaf.get(leaf);
    if (!mounted) return;
    for (const { subplotDiv } of mounted) {
      _renderSpectrumAndPeaks(subplotDiv, leaf);
    }
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
    const liveMatches = evaluateMatches(leaf);
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
      // review 2026-07-06 finding 76b: default 0.01 (Python parity).
      const tol = pk.tolerance == null ? 0.01 : Number(pk.tolerance);
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

  // Number-input cell with dual `input` (live subplot preview, keeps
  // focus alive) and `change` (sort + full refresh on blur/Enter)
  // semantics. Mid-typing keystrokes mustn't destroy the input element
  // or it drops focus and eats decimal points.
  function _peakNumberCell(field, mutateOnInput) {
    return (pk, leaf, ctx) => {
      const inp = document.createElement('input');
      inp.type = 'number'; inp.step = 'any'; inp.min = '0';
      inp.value = String(pk[field]);
      inp.addEventListener('input', () => {
        const v = parseFloat(inp.value);
        if (Number.isFinite(v) && mutateOnInput(v)) {
          pk[field] = v;
          refreshSubplotOnly(leaf);
        }
      });
      inp.addEventListener('change', () => {
        const v = parseFloat(inp.value);
        if (Number.isFinite(v) && mutateOnInput(v)) {
          pk[field] = v;
          if (field === 'freq') sortDeclared(leaf);
          refreshAll(leaf, ctx.commit);
        }
      });
      return inp;
    };
  }

  const tableEditor = createDeclaredItemsTable({
    rootClassName: 'peak-editor-ui',
    tableClassName: 'tube-table',
    addLabel: '+ add peak',
    hint: 'Shift+click on the spectrum to add a peak · '
        + 'Shift+drag to move · Shift+right-click to remove · '
        + 'Edit the window to rescope the spectrum',
    getItems: getDeclaredPeaks,
    columns: [
      { label: 'Freq (Hz)', cell: _peakNumberCell('freq', v => v > 0) },
      { label: 'Tolerance', cell: _peakNumberCell('tolerance', v => v >= 0) },
      {
        label: 'Mode',
        cell: (pk, leaf, ctx) => {
          const sel = document.createElement('select');
          for (const m of ['rel', 'abs']) {
            const opt = document.createElement('option');
            opt.value = m; opt.textContent = m;
            if (pk.tolerance_mode === m) opt.selected = true;
            sel.appendChild(opt);
          }
          sel.title = 'rel = fractional (e.g. 0.01 = 1% of declared freq); '
                    + 'abs = Hz';
          sel.addEventListener('change', () => {
            pk.tolerance_mode = sel.value === 'abs' ? 'abs' : 'rel';
            refreshAll(leaf, ctx.commit);
          });
          return sel;
        },
      },
    ],
    evaluateMatches,
    renderMatch: (m) => {
      const span = document.createElement('span');
      if (!m || m.matched_hz == null) {
        span.innerHTML = '<span class="fail">no match</span>';
      } else {
        const dhz = m.delta != null ? m.delta.toExponential(2) : '';
        span.innerHTML = `<span class="pass">${m.matched_hz.toFixed(4)} Hz `
                       + `(Δ ${dhz})</span>`;
      }
      return span;
    },
    addItem: (leaf) => {
      const pks = getDeclaredPeaks(leaf);
      // Seed frequency from the first detected peak on the live ref
      // spectrum, or 1.0 Hz as a last resort.
      const live = getLiveSpectrum(leaf, 'ref');
      const detected = _findTopNPeaksJS(live.freqs, live.magnitudes, 10, 0);
      const freqs = detected.map(p => p.freq);
      const seedFreq = pks.length
        ? (pks[pks.length - 1].freq + (freqs[pks.length] || pks[pks.length - 1].freq * 2))
        : (freqs[0] || 1.0);
      const newPk = { freq: Number(seedFreq), tolerance: 0.01, tolerance_mode: 'rel' };
      const w = _windowCopy(getLeafWindow(leaf));
      if (w) newPk.derived_from_window = w;
      pks.push(newPk);
      sortDeclared(leaf);
    },
    extraButtons: [
      // Detect source dropdown — picks WHICH signal Detect analyzes.
      // Does not affect the subplot display (both curves always shown).
      // Default: Reference (the regression target). Pick Actual for
      // fresh tests where no baseline exists yet.
      (leaf) => {
        const label = document.createElement('label');
        label.className = 'editor-hint';
        label.style.marginLeft = '1em';
        label.textContent = 'Detect from: ';
        label.title = 'Chooses which signal Detect analyzes for peaks. '
                    + 'Does NOT change the subplot display (both curves '
                    + 'are always shown). Reference = seed from the '
                    + 'regression target; Actual = seed from the current '
                    + 'run\'s output (useful on fresh tests without a '
                    + 'baseline).';
        const sel = document.createElement('select');
        sel.className = 'detect-source-select';
        for (const [val, txt] of [['ref', 'Reference'], ['act', 'Actual']]) {
          const opt = document.createElement('option');
          opt.value = val; opt.textContent = txt;
          if (getDetectSource(leaf) === val) opt.selected = true;
          sel.appendChild(opt);
        }
        sel.addEventListener('change', () => setDetectSource(leaf, sel.value));
        label.appendChild(sel);
        return label;
      },
      // Detect button — replaces the table with peaks detected on the
      // selected source spectrum, scoped to the leaf's current window.
      (leaf, ctx) => {
        const btn = document.createElement('button');
        btn.className = 'node-btn';
        btn.textContent = '🔍 Detect peaks';
        btn.title = 'Replace the table with peaks detected on the '
                  + 'selected source spectrum, scoped to the leaf\'s '
                  + 'current window. Low-freq noise (< ~2 cycles per '
                  + 'window) is filtered out. Each new peak is stamped '
                  + 'with "derived_from_window" metadata for provenance.';
        btn.addEventListener('click', () => {
          const source = getDetectSource(leaf);
          const live = getLiveSpectrum(leaf, source);
          if (live.freqs.length < 3) return;
          // Default min_frequency: require at least 2 full cycles within
          // the windowed signal. Anything below that is low-freq leakage
          // / trend residue, not a reliably-resolved peak. Matches "don't
          // flag a peak whose period doesn't fit twice in my window".
          const binSpacing = live.freqs[1] - live.freqs[0];
          const detected = _findTopNPeaksJS(live.freqs, live.magnitudes, 3, 2 * binSpacing);
          if (detected.length === 0) return;
          const w = _windowCopy(getLeafWindow(leaf));
          setDeclaredPeaks(leaf, detected.map(p => {
            const pk = { freq: Number(p.freq), tolerance: 0.01, tolerance_mode: 'rel' };
            if (w) pk.derived_from_window = w;
            return pk;
          }));
          refreshAll(leaf, ctx.commit);
        });
        return btn;
      },
    ],
    // Resolution hint — tells the user what the current window's FFT
    // bin spacing is, so they can choose meaningful tolerances. If a
    // 1-Hz declared peak has a 0.05 Hz tolerance on a signal whose bin
    // spacing is 0.25 Hz, that's sub-bin and will fail for reasons
    // unrelated to drift.
    footer: (leaf) => {
      const refSpec = getLiveSpectrum(leaf, 'ref');
      if (refSpec.freqs.length < 2) return null;
      const binSpacing = refSpec.freqs[1] - refSpec.freqs[0];
      const div = document.createElement('div');
      div.className = 'editor-hint';
      div.style.marginTop = '0.4em';
      div.textContent =
        `FFT bin resolution: ~${binSpacing.toExponential(2)} Hz. `
        + `Tolerances tighter than this may fail regardless of actual drift; `
        + `set at least 2× bin spacing for reliable matching.`;
      return div;
    },
  });

  return {
    activate(leaf, _plotEl, commit) {
      const slots = [];
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

        slots.push({ slot, subplotDiv });
      });
      slotsByLeaf.set(leaf, slots);
      // Helper appends its table root to each slot (after title +
      // subplot), then we render the spectrum and attach the shift-
      // editor to each subplot.
      tableEditor.activate(leaf, commit);
      for (const { subplotDiv } of slots) {
        _renderSpectrumAndPeaks(subplotDiv, leaf);
        _peakEditor.attach(leaf, subplotDiv, commit);
      }
      commit();
    },
    deactivate(leaf, _plotEl) {
      const slots = slotsByLeaf.get(leaf) || [];
      for (const { subplotDiv } of slots) {
        _peakEditor.detach(subplotDiv);
        if (window.Plotly && subplotDiv.isConnected) Plotly.purge(subplotDiv);
      }
      slotsByLeaf.delete(leaf);
      tableEditor.deactivate(leaf);
    },
  };
})();

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

// review 2026-07-06 finding 76c: single armed brush at a time, with a
// well-defined cancel path — ESC and a second click on the brush button
// both disarm cleanly (listener removed, dragmode restored, no stacked
// handlers). The global ESC handler consults this before leaf-deactivate.
let _activeBrush = null;   // {btn, cleanup} | null

function enterBrushMode(leaf, plotEl, commit, btn) {
  if (!plotEl) return;
  if (_activeBrush) {
    const sameBtn = _activeBrush.btn === btn;
    _activeBrush.cleanup();
    if (sameBtn) return;   // second click on the armed button = cancel
  }
  const prev = plotEl._fullLayout?.dragmode || 'zoom';
  Plotly.relayout(plotEl, { dragmode: 'select', selectdirection: 'h' });
  if (btn) { btn.classList.add('brush-armed'); btn.textContent = '🔲 Drag on plot…'; }

  const cleanup = () => {
    plotEl.removeListener?.('plotly_selected', onSel);
    Plotly.relayout(plotEl, { dragmode: prev });
    if (btn) { btn.classList.remove('brush-armed'); btn.textContent = '🔲 Set window from plot'; }
    _activeBrush = null;
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
  _activeBrush = { btn, cleanup };
}

function deactivateLeaf() {
  if (!activeLeafPath) return;
  // An armed brush belongs to the active leaf's editor slot — disarm it
  // before the slot DOM is cleared (review 2026-07-06 finding 76c).
  if (_activeBrush) _activeBrush.cleanup();
  const path = activeLeafPath;
  // review 2026-07-06 finding 76d: when the structure has been edited the
  // rendered (and activated) leaf objects come from WORKING_TREE — resolve
  // there FIRST so editors' WeakMap identity lookups (e.g. the peak
  // editor's slotsByLeaf) hit, and their document-level listeners detach.
  const leaf = structureDirty
    ? (findLeaf(WORKING_TREE, path) || findLeaf(TREE_VIEW, path))
    : (findLeaf(TREE_VIEW, path) || findLeaf(WORKING_TREE, path));
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

