// ---- interactive.js: part 4 of 5 (tree-ui.js) ----
// interactive.js is authored as 5 fragments and concatenated, in the
// order named by ``INTERACTIVE_JS_PARTS`` in reporting/report_suite.py, into
// the single file the report page loads. The fragments are contiguous slices
// of one script: never reorder them and never move a declaration across a
// seam — top-level ``const`` TDZ, function hoisting and the load-time IIFEs
// all depend on this exact sequence.
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
  // weighted defers to the CLI (recomputePassStates: the score-weighted
  // sum needs per-leaf scores the page doesn't carry). Same badge the
  // leaves use, for the same reason — without it, a weighted node the
  // user just created in the browser showed a bare FAIL with nothing
  // saying where the verdict came from or how to get a real one.
  if (node.combinator === 'weighted') {
    header.appendChild(cliAuthoritativeBadge(
      'Weighted scoring needs per-leaf scores the report does not carry — '
      + 'this verdict comes from the CLI. Run dstf run to score your edit.',
    ));
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

  // review 2026-07-06 finding 45: param edits must mutate the WORKING_TREE
  // node (resolved by path, same pattern as removeNodeButton) — mutating
  // the rendered ``node`` hit the pristine TREE_VIEW when the edit was the
  // first structural change, so the input visibly reverted and the export
  // carried the stale value.
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
      const target = findWorkingNode(node.path);
      if (Number.isFinite(v) && v >= 1 && target) {
        target.k = v;
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
      const target = findWorkingNode(node.path);
      if (Number.isFinite(v) && target) {
        target.threshold = v;
        markStructureDirty();
      } else {
        thrInp.value = String(node.threshold ?? 1.0);
      }
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
      const target = findWorkingNode(node.path);
      if (!target) return;
      target.direction = dirSel.value;
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
        const target = findWorkingNode(node.path);
        if (Number.isFinite(v) && target) {
          const tChildren = target.children || [];
          target.weights = tChildren.map((__, j) => (
            j === i ? v : ((target.weights || [])[j] ?? 1.0)
          ));
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

function cliAuthoritativeBadge(title) {
  // One badge element, two callers (CLI-pinned leaves + weighted
  // combinators) — the class is what the browser tests and the CSS match
  // on, so it must not diverge between them.
  const badge = document.createElement('span');
  badge.className = 'cli-authoritative';
  badge.title = title;
  badge.textContent = 'CLI-authoritative';
  return badge;
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
    // A sentinel leaf ("not scored") has no number to explain itself —
    // the reason is the whole finding, so it rides as the pill tooltip
    // and as the muted line appended under the header below.
    if (leaf.error) score.title = leaf.error;
    header.appendChild(score);
  }
  if (leaf.cli_authoritative) {
    header.appendChild(cliAuthoritativeBadge(
      'Edits reflected after running dstf run',
    ));
  }
  // Structural controls — ⊕ wraps this leaf in a new combinator (always
  // allowed), − removes this leaf (only when a parent exists). ↻ resets
  // this leaf's params + window to the CLI-evaluated originals. Root leaf
  // (legal but unusual: single-leaf tree) has no remove.
  header.appendChild(resetButton(leaf));
  header.appendChild(wrapButton(leaf));
  if (opts.parent) header.appendChild(removeNodeButton(leaf, opts));
  wrapper.appendChild(header);

  // Why this leaf could not be scored — a misconfigured metric, or a
  // window/trajectory with no samples in it. Without this the panel shows
  // only "not scored" and the reason lives nowhere in the report.
  if (leaf.error) {
    const err = document.createElement('div');
    err.className = 'node-error';
    err.textContent = leaf.error;
    wrapper.appendChild(err);
  }

  // Controls — server-rendered HTML for mode fields + window inputs.
  // Tube leaves intentionally skip mode schema inputs: the rich
  // interactive editor (activated on click) owns that config surface
  // and would duplicate the same fields. Window inputs still show for
  // tube so the user can scope a tube to a time window.
  // Tube, dominant-frequency, and points all own their entire UI via
  // the activated editor (rich table + shift-edit on plot). Suppressing
  // the auto-derived header panel avoids a redundant duplicate of the
  // declared-list config.
  const skipModeControls = (
    leaf.metric === 'tube'
    || leaf.metric === 'dominant-frequency'
    || leaf.metric === 'points'
  );
  const innerHtml =
    (skipModeControls ? '' : (leaf.mode_controls_html || ''))
    + (leaf.window_controls_html || '');
  if (innerHtml) {
    const controls = document.createElement('div');
    controls.className = 'node-controls';
    controls.innerHTML = innerHtml;
    // A mode with a rich editor owns its list-shaped config (declared
    // events, points, peaks). The schema panel's raw-JSON textarea for
    // the same field is a second, strictly worse surface for it, and
    // event-timing was showing BOTH — the textarea here and the table
    // below. Drop the textarea; the mode's scalar fields (time_tolerance,
    // count_must_match) stay, since the editor has no control for them.
    // No-op for tube / points / dominant-frequency: their whole panel is
    // suppressed above.
    if (MODE_PLOT_EDITORS[leaf.metric]) {
      controls.querySelectorAll('label.mc-passthrough').forEach(el => el.remove());
    }
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
  // review 2026-07-06 finding 76e: always return a pill element — leaves
  // added via the + editor have no CLI verdict yet, and without a
  // .node-status element updatePassPills had nothing to stamp once the
  // live scorer produced one. Undefined renders as a neutral placeholder.
  const span = document.createElement('span');
  if (passed === undefined || passed === null) {
    span.className = 'node-status pending';
    span.textContent = '—';
  } else {
    span.className = `node-status ${passed ? 'pass' : 'fail'}`;
    span.textContent = passed ? 'PASS' : 'FAIL';
  }
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
  // warn pins arity at exactly 1 (comparison/tree_spec.py). A warn that
  // already has its child cannot take another, so offering the button
  // only to have the grammar reject the result later is a trap: disable
  // it and say why. A warn with no child yet still accepts one.
  if (combinatorNode.combinator === 'warn'
      && (combinatorNode.children || []).length >= 1) {
    btn.disabled = true;
    btn.classList.add('node-btn-disabled');
    btn.title = 'warn takes exactly 1 child and already has it. Wrap the '
              + 'child in an and/or first (⊕), then add leaves there.';
    return btn;
  }
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

// Derived from the registry-fed schema map (mode_controls.emit_mode_schemas),
// so a newly registered metric reaches the add-leaf dropdown without a second
// name list here. Order is alphabetical, not registration order: the template
// serializes the map with Jinja's tojson, which sorts keys.
const VALID_METRICS = Object.keys(MODE_SCHEMAS);

function _softCheckBaselineNames() {
  // Companions are plot-only and can never be scored against
  // (comparison/validator.py), so they never enter the "against" list.
  const names = OVERLAY_ROWS
    .filter(r => r && r.role === 'soft_check' && r.name)
    .map(r => r.name);
  return Array.from(new Set(names)).sort();
}

function _hasWarnAncestor(path) {
  // The node at ``path`` counts as its own ancestor — a leaf appended
  // directly to a warn combinator already sits under one. Paths are
  // rebuilt by rebuildPaths as ``/metrics(/children/<i>)*``, so the
  // ancestor chain is the sequence of ``/children/`` prefixes.
  const segs = String(path || '').split('/children/');
  let prefix = '';
  for (let i = 0; i < segs.length; i++) {
    prefix = i === 0 ? segs[0] : `${prefix}/children/${segs[i]}`;
    const n = findWorkingNode(prefix);
    if (n && n.combinator === 'warn') return true;
  }
  return false;
}

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
  // The "against" row only exists when this test carries soft_check
  // baselines — with none, ``primary`` is the only legal target and a
  // one-option select would be noise.
  const softChecks = _softCheckBaselineNames();
  const againstField = softChecks.length ? `
      <label class="mc-field"><span>Against (baseline)</span>
        <select id="add-leaf-against">
          <option value="primary" selected>primary (default)</option>
          ${softChecks.map(n => `<option value="${_escHtml(n)}">${_escHtml(n)} (soft_check)</option>`).join('')}
        </select>
      </label>
      <div id="add-leaf-against-warning" class="var-warning"></div>
  ` : '';
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
      ${againstField}
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
  const againstSel = overlay.querySelector('#add-leaf-against');
  const againstWarnEl = overlay.querySelector('#add-leaf-against-warning');
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

  function checkAgainst() {
    // Non-blocking: the user may wrap the new leaf in warn afterwards.
    // spec-update's validator is the one that rejects an unwrapped
    // soft_check leaf, so warn here rather than disabling Add.
    if (!againstSel) return;
    const a = againstSel.value;
    if (a === 'primary' || _hasWarnAncestor(combinatorNode.path)) {
      againstWarnEl.textContent = '';
      return;
    }
    againstWarnEl.textContent = `Warning: a leaf scored against '${a}' must sit under a `
      + `warn combinator — spec-update rejects the patch otherwise. Wrap it after adding.`;
  }
  if (againstSel) againstSel.addEventListener('change', checkAgainst);

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
    appendLeafToWorking(
      combinatorNode.path, metric, varname, againstSel ? againstSel.value : 'primary');
  });
}

// Same rule the Python side stamps on CLI-provided leaves
// (context_builder._JS_SCORED_METRICS +
// report_suite._mark_decimated_cli_authoritative): any metric with
// no MODE_SCORERS entry (event-timing's pairing, scalar's stored quantities,
// third-party metrics) has no live verdict to show; dominant-frequency only
// when the variable's embedded trajectory was LTTB-decimated — the live FFT
// would then run on a different bin grid than the CLI's; and a leaf scored
// against a named baseline only when that baseline's curve for this variable
// is NOT embedded on the page. When it is, the scorers resolve it through
// _leafBaselineCurve and live-score against the right data.
function _leafIsCliAuthoritative(metric, varname, against) {
  if (against && against !== 'primary'
      && !_baselineEmbeddedFor(varname, against)) {
    return true;
  }
  if (!MODE_SCORERS[metric]) return true;
  // D98: decimation pins dominant-frequency regardless of which baseline
  // the leaf names — the FFT grid, not the reference curve, is what
  // diverges.
  if (metric !== 'dominant-frequency') return false;
  return _trajectoryDecimated(varname);
}

function _baselineEmbeddedFor(varname, name) {
  const overlays = (VARIABLES_BY_NAME[varname] || {}).overlays || [];
  return overlays.some(o => o && o.role === 'soft_check' && o.name === name);
}

// Reads the same ``full_samples`` stamp renderDecimationBanner uses
// (report_suite._decimate_context_for_html records pre-decimation counts).
function _trajectoryDecimated(varname) {
  const traj = (VARIABLES_BY_NAME[varname] || {}).trajectory || {};
  const fs = traj.full_samples;
  if (!fs) return false;
  return (traj.act_time || []).length < (fs.act || 0);
}

function appendLeafToWorking(parentPath, metric, varname, against = 'primary') {
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
    against: against || 'primary',
    window: {},
    children: [],
    name: varname,
    mode_effective: metric,
    mode_values: Object.assign({}, defaults),
    mode_controls_html: renderModeControlsHtmlJs(metric, varname, defaults),
    window_controls_html: renderWindowControlsHtmlJs(varname, {}, bounds),
    window_values: {},
    cli_authoritative: _leafIsCliAuthoritative(metric, varname, against),
  };
  node.children.push(leaf);
  // Rebuild paths first so the new leaf has its final path, then seed
  // leafState under that path — scorers + plot contributions lookup
  // ``leafState[path]`` and silently short-circuit when missing, which
  // was the cause of "added tube but plot doesn't update".
  rebuildPaths(WORKING_TREE, '/metrics');
  // review 2026-07-06 finding 38: deep copies — schema defaults can carry
  // arrays (points/peaks) that must not be shared between params and
  // original_params.
  leafState[leaf.path] = {
    params: _deepCopy(defaults),
    window: {},
    visible: true,
    original_params: _deepCopy(defaults),
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
       + `<textarea data-field="${name}" data-passthrough="true" rows="1">${raw}</textarea></label>`;
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
  // review 2026-07-06 finding 38: deep copy so reset restores pristine
  // nested arrays (tube_points / points / events / peaks), not references
  // the next edit would corrupt in both params AND original_params.
  state.params = _deepCopy(state.original_params || {});
  state.window = Object.assign({}, state.original_window || {});
  renderAllNodeTreesFromWorking();
  refreshPassStates();
  updateExport();
}

// True when ``leafState[path].params[field]`` differs from the original
// CLI-evaluated snapshot. Numbers compare through floatsDiffer; nested
// arrays/objects compare structurally, since params and original_params are
// deep copies and never share identity (review 2026-07-06 finding 38).
// Shared by the granular patch diff and the structural spec-strip so both
// agree on "the user edited this".
function _paramEdited(state, field) {
  const cur = (state.params || {})[field];
  const o = (state.original_params || {})[field];
  if (cur === o) return false;
  if (typeof cur === 'number' && typeof o === 'number') return floatsDiffer(cur, o);
  if (cur != null && o != null && typeof cur === 'object' && typeof o === 'object') {
    return JSON.stringify(cur) !== JSON.stringify(o);
  }
  return true;
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
    const specParams = node.params || {};
    const mergedParams = Object.assign({}, specParams, state.params || {});
    // Drop any render-only stashes that leaked into params.
    delete mergedParams.mode;
    const out = {
      metric: node.metric,
      variable: node.variable,
    };
    for (const k of Object.keys(mergedParams)) {
      if (mergedParams[k] === undefined || mergedParams[k] === null) continue;
      // leafState seeds params from spec params MERGED WITH mode_values, so
      // it also carries values the user never authored — notably the
      // tolerance the CLI inherited from config.tolerance. Freezing those
      // into a wholesale /metrics add would sever the leaf from the config
      // default forever. Export a param only if the spec declared it or the
      // user edited it this session; an omitted tolerance falls back to
      // base_tolerance at eval time (tree_eval._evaluate_leaf).
      if (!(k in specParams) && !_paramEdited(state, k)) continue;
      out[k] = mergedParams[k];
    }
    if (node.against && node.against !== 'primary') out.against = node.against;
    // review 2026-07-06 finding 76a: when the leaf has live state, its
    // window is authoritative — an explicitly CLEARED window (state.window
    // = {}) must export as "no window", not fall back to the stale
    // node.window and resurrect it.
    let win = null;
    if (state.window !== undefined) {
      win = (state.window && (state.window.start != null || state.window.end != null))
        ? state.window : null;
    } else if (node.window && (node.window.start != null || node.window.end != null)) {
      win = node.window;
    }
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

// Visual state for a raw-JSON textarea: red border + explanatory tooltip
// while the text doesn't parse, cleared once it does.
function _markPassthroughValidity(input, valid) {
  if (!input) return;
  if (input.style) input.style.borderColor = valid ? '' : '#d32f2f';
  input.title = valid ? '' : 'Invalid JSON — last valid value kept';
  if (input.classList) input.classList.toggle('mc-invalid', !valid);
}

function onLeafFieldChange(leaf, field, input) {
  let val;
  if (input.type === 'checkbox') val = input.checked;
  else if (input.type === 'number') {
    val = input.value === '' ? null : parseFloat(input.value);
    if (input.value !== '' && !Number.isFinite(val)) return;
  } else if (input.dataset.passthrough === 'true') {
    // Malformed JSON must NOT reach params: scorers skip it via their
    // Array.isArray guards, but buildPatchData would export the raw string
    // into the spec with nothing visible to say so. Flag the field and keep
    // the last-good value. A cleared field is still a real edit → null.
    if (input.value.trim() === '') {
      val = null;
    } else {
      try { val = JSON.parse(input.value); }
      catch (_) { _markPassthroughValidity(input, false); return; }
    }
    _markPassthroughValidity(input, true);
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

// Leaves sitting under a ``warn`` wrapper are ADVISORY: warn never fails
// its parent (comparison/metric_tree.py), so such a leaf's failure cannot
// change the test's verdict. That is the baseline-role semantics — a
// soft_check regression informs, it does not gate. Returns the set of
// advisory leaf paths in ``tree``.
function advisoryLeafPaths(tree) {
  const out = new Set();
  (function walk(node, underWarn) {
    if (!node) return;
    if (node.kind === 'leaf') {
      if (underWarn) out.add(node.path);
      return;
    }
    const warn = underWarn || node.combinator === 'warn';
    (node.children || []).forEach(c => walk(c, warn));
  })(tree, false);
  return out;
}

function updateSummaryFromMap(passMap, tree) {
  // Collect leaves from the active tree (structure-dirty pulls from
  // WORKING_TREE; untouched pulls from TREE_VIEW).
  const leaves = [];
  walkLeaves(tree, l => leaves.push(l));
  const getPass = (l) => passMap ? !!passMap[l.path] : !!l.passed;
  const advisory = advisoryLeafPaths(tree);
  const isAdvisory = (l) => advisory.has(l.path);
  const n = leaves.length;
  const nPassed = leaves.filter(getPass).length;
  // The headline colour follows the ROOT verdict, not the leaf tally: a
  // warn-wrapped failure used to paint the summary and the variable pill
  // red while the root passed, which reads as "this test failed" for a
  // finding that is advisory by construction. Advisory failures get the
  // amber chip instead.
  //
  // Tri-state, like the root's own pill: ``null`` means there is no verdict
  // to show (the user wrapped the ROOT in `weighted`, which defers to the
  // CLI). Coercing that to false painted the summary red under a pill
  // reading "—" — two surfaces, two answers, and the red one was invented.
  const rootRaw = (passMap && tree && tree.path in passMap)
    ? passMap[tree.path]
    : leaves.every(getPass);
  const rootPending = rootRaw === null || rootRaw === undefined;
  const nAdvisoryFailed = leaves.filter(l => isAdvisory(l) && !getPass(l)).length;
  const el = document.getElementById('summary-text');
  if (el) {
    el.className = rootPending ? 'pending' : (rootRaw ? 'pass' : 'fail');
    if (rootPending) {
      el.innerHTML =
        `<strong>${nPassed}</strong> / <strong>${n}</strong> leaves passed · `
        + 'root verdict pending — run dstf run to evaluate';
    } else {
      el.innerHTML =
        `<strong>${nPassed}</strong> / <strong>${n}</strong> leaves passed`;
    }
    if (nAdvisoryFailed) el.appendChild(advisoryChip(nAdvisoryFailed));
  }
  VARIABLE_ORDER.forEach((varname, idx) => {
    const varLeaves = leaves.filter(l => l.variable === varname);
    // Only the leaves that can actually gate this variable decide its
    // colour; advisory ones are reported beside it.
    const gating = varLeaves.filter(l => !isAdvisory(l));
    const varPassed = varLeaves.length > 0 && gating.every(getPass);
    const varAdvisoryFailed = varLeaves.filter(
      l => isAdvisory(l) && !getPass(l)).length;
    const sel = document.querySelector(`.var-status[data-vidx="${idx}"]`);
    if (sel) {
      sel.className = `var-status ${varPassed ? 'pass' : 'fail'}`;
      sel.textContent = varPassed ? 'PASS' : 'FAIL';
      // One chip per variable pill, replaced in place on every refresh.
      const existing = sel.parentNode
        && sel.parentNode.querySelector(`.advisory-chip[data-vidx="${idx}"]`);
      if (existing) existing.remove();
      if (varAdvisoryFailed) {
        const chip = advisoryChip(varAdvisoryFailed);
        chip.dataset.vidx = String(idx);
        sel.insertAdjacentElement('afterend', chip);
      }
    }
  });
}

function advisoryChip(count) {
  const chip = document.createElement('span');
  chip.className = 'advisory-chip';
  chip.textContent = count === 1 ? '1 advisory' : `${count} advisory`;
  chip.title = count === 1
    ? 'One failing leaf sits under a warn wrapper — advisory only; it does '
      + 'not fail the test.'
    : `${count} failing leaves sit under a warn wrapper — advisory only; `
      + 'they do not fail the test.';
  return chip;
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
    // review 2026-07-06 finding 76f: passthrough textareas hold raw JSON —
    // assigning an array/object directly stringifies to "[object Object]"
    // and corrupts the sibling mount's value.
    else if (inp.dataset && inp.dataset.passthrough === 'true') {
      inp.value = val == null ? ''
        : (typeof val === 'string' ? val : JSON.stringify(val));
    } else inp.value = (val == null ? '' : val);
  });
}

function escapeSelector(s) {
  // CSS.escape is supported everywhere Plotly runs (2015+). Fall back to
  // a best-effort replacement for defensive builds without it.
  if (typeof CSS !== 'undefined' && CSS.escape) return CSS.escape(s);
  return String(s).replace(/(["\\])/g, '\\$1');
}

