// Unified dashboard JS: filter / sort / fetch loop / refresh button.
// Loaded inline via a Jinja include directive in dashboard.html.
// Reads two top-level Jinja-injected constants: DASHBOARD_MODE
// ('live' or 'final') and DASHBOARD_TOTAL (int).

(function() {
  const tbody = document.getElementById('results-tbody');
  const headers = document.querySelectorAll('#results-table thead tr:first-child th[data-sort]');
  const colFilters = document.querySelectorAll('input.col-filter');

  // ---- Column resize (drag handles on header th's) ----
  // Each header th has a `<span class="col-resize-handle">` strip on its
  // right edge; mousedown there starts a drag that updates th.style.width
  // (the table inherits from the header in auto-layout so tds follow).
  // For the Model + Detail columns, dragging also updates the
  // `--col-w-<key>` CSS variable so .model-cell / .detail-cell's
  // max-width tracks the new column width — without this, the td's
  // ellipsis-truncation cap would override the resize.
  // Widths persist to localStorage so they survive the 5s meta-refresh
  // ticks; restored on page load alongside filter / selection / sort.
  const colWidths = {};  // {key: '500px'} — populated by drag + restore

  function applyColWidth(th, width) {
    th.style.width = width;
    th.style.minWidth = width;
    const key = th.dataset.key;
    if (key) {
      colWidths[key] = width;
      // Mirror to a CSS variable so cells with max-width truncation
      // (Model, Detail) widen along with the th. Cells without
      // truncation just respect the th's width via auto-layout.
      document.documentElement.style.setProperty('--col-w-' + key, width);
    }
  }

  function startColResize(e, th) {
    e.preventDefault();
    e.stopPropagation();  // Don't trigger the th's click-to-sort handler
    const startX = e.clientX;
    const startWidth = th.offsetWidth;
    th.classList.add('resizing');
    document.body.style.cursor = 'col-resize';

    function onMove(ev) {
      const delta = ev.clientX - startX;
      const newWidth = Math.max(40, startWidth + delta);
      applyColWidth(th, newWidth + 'px');
    }
    function onUp() {
      th.classList.remove('resizing');
      document.body.style.cursor = '';
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      persistState();
    }
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  }

  document.querySelectorAll('.col-resize-handle').forEach(handle => {
    handle.addEventListener('mousedown', e => {
      const th = handle.closest('th');
      if (th) startColResize(e, th);
    });
    // Eat clicks so they don't bubble up to the th's sort handler
    handle.addEventListener('click', e => e.stopPropagation());
  });

  // ---- Sticky-chrome height tracking ----
  // The chrome (h1 + status bar + progress bar + counter pills) is sticky
  // at top:0; the two thead rows stack below it via CSS variables. The
  // chrome's height varies (counter pills wrap on narrow viewports, header
  // copy length differs between live and final modes), so we measure it
  // and feed --chrome-height + --header-row-height to CSS. Fallback values
  // in the CSS rules cover the brief moment before the first measurement.
  function updateChromeHeight() {
    const chrome = document.getElementById('sticky-chrome');
    const headerRow = document.querySelector('#results-table thead tr:first-child');
    if (chrome) {
      document.documentElement.style.setProperty(
        '--chrome-height', chrome.offsetHeight + 'px',
      );
    }
    if (headerRow) {
      document.documentElement.style.setProperty(
        '--header-row-height', headerRow.offsetHeight + 'px',
      );
    }
  }
  updateChromeHeight();
  if (typeof ResizeObserver !== 'undefined') {
    const chrome = document.getElementById('sticky-chrome');
    if (chrome) new ResizeObserver(updateChromeHeight).observe(chrome);
  }
  window.addEventListener('resize', updateChromeHeight);

  // ---- Filter (status pills + per-column text) ----
  // Counter pills are dual-purpose: they show the count AND act as filter
  // toggles. Click a pill to add its status to the filter set; click again
  // to remove. Click Total to clear all filters. Empty set = show all.
  // Pills with zero count are disabled (no-op on click).
  const activeStatuses = new Set();
  const colFilterValues = {};

  // Maps the pill key (counter category) to the row data-status value(s)
  // it should match. Most are direct; warnings is a cross-cut that
  // checks any row with sortWarnings>0 regardless of pass/fail outcome.
  const PILL_TO_STATUSES = {
    queued:     ['queued'],
    running:    ['running'],
    passed:     ['pass'],
    failed:     ['fail'],
    sim_failed: ['sim-fail'],
    no_ref:     ['no-ref'],
    timed_out:  ['timed-out'],
    warnings:   ['__warnings__'],   // sentinel handled below
  };

  // Convert a per-column filter string to a matcher. If the input contains
  // glob metacharacters (* or ?), build a case-insensitive regex from the
  // glob; otherwise fall back to plain case-insensitive substring match.
  // This way "Freq" still works for naive users while *.Freq* / Lib.* etc.
  // power-users get glob semantics matching the CLI's --filter behavior.
  function patternMatcher(q) {
    if (!q) return null;
    if (!q.includes('*') && !q.includes('?')) {
      const needle = q.toLowerCase();
      return (val) => (val || '').toLowerCase().indexOf(needle) !== -1;
    }
    const escaped = q.replace(/[.+^${}()|[\]\\]/g, '\\$&');
    const regex = new RegExp('^' + escaped.replace(/\*/g, '.*').replace(/\?/g, '.') + '$', 'i');
    return (val) => regex.test(val || '');
  }

  function statusMatches(rowStatus, sortWarnings) {
    if (activeStatuses.size === 0) return true;
    for (const pill of activeStatuses) {
      const targets = PILL_TO_STATUSES[pill] || [pill];
      for (const t of targets) {
        if (t === '__warnings__' && sortWarnings > 0) return true;
        if (t === rowStatus) return true;
      }
    }
    return false;
  }

  function applyFilters() {
    // Compile per-column matchers once per filter event (cheap; rebuilt
    // when any input changes via the input event handler below).
    const matchers = {};
    for (const [col, q] of Object.entries(colFilterValues)) {
      const m = patternMatcher(q);
      if (m) matchers[col] = m;
    }
    Array.from(tbody.querySelectorAll('tr')).forEach(row => {
      const sortWarnings = parseInt(row.dataset.sortWarnings || '0');
      let visible = statusMatches(row.dataset.status, sortWarnings);
      if (visible) {
        for (const [col, match] of Object.entries(matchers)) {
          if (!match(row.dataset[col])) {
            visible = false;
            break;
          }
        }
      }
      row.style.display = visible ? '' : 'none';
    });
    // Filter changes shift which rows are visible, which affects the
    // tristate header checkbox + the "(M hidden by filter)" hint in the
    // footer. The selection itself doesn't change here — filters never
    // touch ticks. syncSelectionUI is hoisted (function declaration) so
    // forward-reference is fine even though it's defined further down.
    if (typeof syncSelectionUI === 'function') syncSelectionUI();
  }

  function syncPillStates() {
    document.querySelectorAll('.counter[data-pill]').forEach(p => {
      const key = p.dataset.pill;
      p.classList.toggle('active', activeStatuses.has(key));
    });
  }

  // Pill click handler. Total clears all filters; other pills toggle
  // their key in/out of the active set. Disabled pills (zero count)
  // are inert.
  window.togglePill = function(key, el) {
    if (el && el.classList.contains('disabled')) return;
    if (key === 'total') {
      activeStatuses.clear();
    } else if (activeStatuses.has(key)) {
      activeStatuses.delete(key);
    } else {
      activeStatuses.add(key);
    }
    syncPillStates();
    applyFilters();
  };

  colFilters.forEach(inp => {
    inp.addEventListener('input', () => {
      colFilterValues[inp.dataset.colFilter] = inp.value;
      applyFilters();
    });
  });

  // ---- 3-state sort cycle: none → sorted → reverse → none ----
  // Numeric columns descend first (largest NRMSE first when triaging);
  // text columns ascend first (alphabetical).
  let lastSorted = { th: null, dir: null };

  headers.forEach(th => {
    th.addEventListener('click', () => sortBy(th));
  });

  // Apply a specific direction to a column. Used by both the interactive
  // sortBy cycle and the localStorage-restored sort on page reload.
  function applySort(th, dir) {
    const key = th.dataset.key;
    const kind = th.dataset.sort;  // 'num' or 'text'

    headers.forEach(h => h.classList.remove('sorted-asc', 'sorted-desc'));
    if (dir) th.classList.add('sorted-' + dir);
    lastSorted = { th, dir };

    const rows = Array.from(tbody.querySelectorAll('tr'));
    if (dir === null) {
      rows.sort((a, b) => {
        const at = a.dataset.sortTest || a.dataset.sortModel || '';
        const bt = b.dataset.sortTest || b.dataset.sortModel || '';
        return at < bt ? -1 : at > bt ? 1 : 0;
      });
    } else {
      rows.sort((a, b) => {
        const ak = 'sort' + key[0].toUpperCase() + key.slice(1);
        let av = a.dataset[ak], bv = b.dataset[ak];
        if (kind === 'num') {
          av = parseFloat(av); bv = parseFloat(bv);
          if (isNaN(av)) av = -Infinity;
          if (isNaN(bv)) bv = -Infinity;
        } else {
          av = (av || '').toLowerCase();
          bv = (bv || '').toLowerCase();
        }
        if (av < bv) return dir === 'asc' ? -1 : 1;
        if (av > bv) return dir === 'asc' ? 1 : -1;
        return 0;
      });
    }
    rows.forEach(r => tbody.appendChild(r));
  }

  function sortBy(th) {
    const kind = th.dataset.sort;
    const firstClick = (kind === 'num') ? 'desc' : 'asc';
    let dir;
    if (lastSorted.th === th) {
      // Cycle: firstClick → opposite → none → firstClick → ...
      // The null-state has to restart the cycle, otherwise the third click
      // gets stuck at null and the column never sorts again.
      if (lastSorted.dir === null) {
        dir = firstClick;
      } else if (lastSorted.dir === firstClick) {
        dir = (firstClick === 'asc') ? 'desc' : 'asc';
      } else {
        dir = null;
      }
    } else {
      dir = firstClick;
    }
    applySort(th, dir);
    persistState();
  }

  // ---- Counters + progress bar (rendered from status snapshot) ----
  // Each pill is a clickable filter toggle. Disabled pills (zero count)
  // stay rendered so the user sees the full set and can spot the moment
  // an outcome appears. Total acts as the clear-all button.
  function renderCounters(counts, total) {
    const cn = document.getElementById('counters');
    const order = [
      ['queued',     'queued'],
      ['running',    'running'],
      ['passed',     'passed'],
      ['failed',     'failed'],
      ['sim_failed', 'sim failed'],
      ['no_ref',     'no baseline'],
      ['timed_out',  'timed out'],
      ['warnings',   'warnings'],
    ];
    let html = '';
    for (const [k, label] of order) {
      const n = counts[k] || 0;
      const disabled = n === 0 ? ' disabled' : '';
      const active = activeStatuses.has(k) ? ' active' : '';
      html += `<div class="counter ${k}${disabled}${active}" data-pill="${k}" ` +
              `onclick="togglePill('${k}', this)">` +
              `<div class="label">${label}</div>` +
              `<div class="value">${n}</div></div>`;
    }
    html += `<div class="counter total" data-pill="total" ` +
            `onclick="togglePill('total', this)" title="Clear all filters">` +
            `<div class="label">Total</div>` +
            `<div class="value">${total}</div></div>`;
    cn.innerHTML = html;

    const bar = document.getElementById('progress-bar');
    const pct = (n) => total ? (n / total * 100).toFixed(2) : 0;
    bar.innerHTML =
      `<span class="b-passed" style="width:${pct(counts.passed || 0)}%"></span>` +
      `<span class="b-failed" style="width:${pct(counts.failed || 0)}%"></span>` +
      `<span class="b-timed_out" style="width:${pct(counts.timed_out || 0)}%"></span>` +
      `<span class="b-running" style="width:${pct(counts.running || 0)}%"></span>`;
  }

  // Initial render from inline data (read from data-* on tbody rows).
  // Rows use the filter-vocab status_class (pass/fail/sim-fail/no-ref/
  // timed-out/queued/running). The counter keys use underscored variants
  // so the JS class names + CSS rules stay sane. Warnings cross-cuts
  // status — any row with sortWarnings>0 contributes regardless of its
  // pass/fail outcome.
  function initialCountsFromRows() {
    const counts = {
      queued: 0, running: 0, passed: 0, failed: 0,
      sim_failed: 0, no_ref: 0, timed_out: 0, warnings: 0,
    };
    Array.from(tbody.querySelectorAll('tr')).forEach(row => {
      const s = row.dataset.status;
      if      (s === 'pass')      counts.passed++;
      else if (s === 'fail')      counts.failed++;
      else if (s === 'sim-fail')  counts.sim_failed++;
      else if (s === 'no-ref')    counts.no_ref++;
      else if (s === 'timed-out') counts.timed_out++;
      else if (s === 'queued')    counts.queued++;
      else if (s === 'running')   counts.running++;
      if (parseInt(row.dataset.sortWarnings || '0') > 0) counts.warnings++;
    });
    return counts;
  }
  renderCounters(initialCountsFromRows(), DASHBOARD_TOTAL);

  // ---- Selection (per-row checkbox + header tristate + sticky footer) ----
  // Selection persists across filter changes ("curated basket" model). The
  // header checkbox is tristate and toggles only currently-visible rows.
  // Hidden-but-selected rows stay selected; the footer surfaces the count.
  const footer = document.getElementById('sel-footer');
  const selAllBox = document.getElementById('sel-all');
  const selCount = document.getElementById('sel-count');
  const hiddenHint = document.getElementById('sel-hidden-hint');
  const cmdOut = document.getElementById('cmd-out');

  function visibleRows() {
    return Array.from(tbody.querySelectorAll('tr')).filter(r => r.style.display !== 'none');
  }
  function selectedRows() {
    return Array.from(tbody.querySelectorAll('tr.row-selected'));
  }
  function setRowSelected(row, selected) {
    row.classList.toggle('row-selected', selected);
    const cb = row.querySelector('input.row-sel');
    if (cb) cb.checked = selected;
  }

  window.toggleAllVisible = function(checked) {
    visibleRows().forEach(r => setRowSelected(r, checked));
    syncSelectionUI();
  };
  window.onRowToggle = function(cb) {
    cb.closest('tr').classList.toggle('row-selected', cb.checked);
    syncSelectionUI();
  };
  window.clearSelection = function() {
    selectedRows().forEach(r => setRowSelected(r, false));
    syncSelectionUI();
  };

  function buildRerunCommand(modelIds) {
    if (!modelIds.length) return '';
    if (modelIds.length <= 3) {
      return `${RERUN_PREFIX} --filter "${modelIds.join(',')}" --merge --report`;
    }
    return `${RERUN_PREFIX} --filter @selected.txt --merge --report`;
  }

  function syncSelectionUI() {
    const sel = selectedRows();
    const vis = visibleRows();
    const visSel = vis.filter(r => r.classList.contains('row-selected'));

    selCount.textContent = sel.length;
    const hiddenSel = sel.length - visSel.length;
    hiddenHint.textContent = hiddenSel > 0 ? ` (${hiddenSel} hidden by filter)` : '';

    // Tristate header checkbox: checked if all visible are selected,
    // indeterminate if some, unchecked if none.
    selAllBox.checked = vis.length > 0 && visSel.length === vis.length;
    selAllBox.indeterminate = visSel.length > 0 && visSel.length < vis.length;

    footer.classList.toggle('shown', sel.length > 0);
    document.body.classList.toggle('has-selection', sel.length > 0);

    const ids = sel.map(r => r.dataset.model);
    cmdOut.value = buildRerunCommand(ids);
  }

  function flashSaveStatus() {
    const el = document.getElementById('save-status');
    el.classList.add('shown');
    setTimeout(() => el.classList.remove('shown'), 1200);
  }
  function copyToClipboard(text) {
    if (navigator.clipboard) {
      navigator.clipboard.writeText(text).then(flashSaveStatus, () => fallbackCopy(text));
    } else {
      fallbackCopy(text);
    }
  }
  function fallbackCopy(text) {
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); flashSaveStatus(); } catch (e) {}
    document.body.removeChild(ta);
  }
  window.copyCommand = function() {
    const ids = selectedRows().map(r => r.dataset.model);
    copyToClipboard(buildRerunCommand(ids));
  };
  window.downloadFilter = function() {
    const ids = selectedRows().map(r => r.dataset.model);
    if (!ids.length) return;
    const blob = new Blob([ids.join('\n') + '\n'], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'selected.txt';
    document.body.appendChild(a); a.click();
    document.body.removeChild(a); URL.revokeObjectURL(url);
    flashSaveStatus();
  };

  // ---- Persistence: survive meta-refresh page reloads ----
  // Live mode reloads the entire page every 5s via the meta-refresh tag,
  // which would otherwise wipe the user's filter/selection/sort state.
  // localStorage is keyed by origin + path so each dashboard.html gets
  // its own scope. Failures (private mode, quota, blocked) fall through
  // silently — the dashboard still works without persistence, just resets
  // each tick.
  const STATE_KEY = 'dstf-dashboard-v1:' + location.pathname;

  function loadState() {
    try { return JSON.parse(localStorage.getItem(STATE_KEY)) || {}; }
    catch (e) { return {}; }
  }
  function persistState() {
    try {
      localStorage.setItem(STATE_KEY, JSON.stringify({
        activeStatuses: Array.from(activeStatuses),
        colFilterValues: { ...colFilterValues },
        selection: selectedRows().map(r => r.dataset.model),
        sort: lastSorted.th
          ? { key: lastSorted.th.dataset.key, dir: lastSorted.dir }
          : null,
        colWidths: { ...colWidths },
      }));
    } catch (e) { /* quota / blocked / private mode — non-fatal */ }
  }

  // Hook persistState into the state-change handlers. We extend the
  // existing window functions (defined above) so callers don't have to
  // know about persistence — every UI action already routes through
  // these handlers.
  const _togglePill = window.togglePill;
  window.togglePill = function(k, el) { _togglePill(k, el); persistState(); };
  const _toggleAllVisible = window.toggleAllVisible;
  window.toggleAllVisible = function(c) { _toggleAllVisible(c); persistState(); };
  const _onRowToggle = window.onRowToggle;
  window.onRowToggle = function(cb) { _onRowToggle(cb); persistState(); };
  const _clearSelection = window.clearSelection;
  window.clearSelection = function() { _clearSelection(); persistState(); };
  colFilters.forEach(inp => inp.addEventListener('input', persistState));

  // Restore saved state on page load. Order: filters first (so the
  // selection step's tristate header reflects the right visible-row
  // population), then selection, then sort.
  (function restoreState() {
    const s = loadState();
    if (s.activeStatuses && Array.isArray(s.activeStatuses)) {
      for (const k of s.activeStatuses) activeStatuses.add(k);
      syncPillStates();
    }
    if (s.colFilterValues && typeof s.colFilterValues === 'object') {
      Object.assign(colFilterValues, s.colFilterValues);
      colFilters.forEach(inp => {
        const k = inp.dataset.colFilter;
        if (s.colFilterValues[k]) inp.value = s.colFilterValues[k];
      });
    }
    // Re-render counters so the active class on pills reflects the
    // restored set (initial render happened before restore).
    renderCounters(initialCountsFromRows(), DASHBOARD_TOTAL);
    applyFilters();

    if (s.selection && Array.isArray(s.selection)) {
      const wantSet = new Set(s.selection);
      Array.from(tbody.querySelectorAll('tr')).forEach(row => {
        if (wantSet.has(row.dataset.model)) setRowSelected(row, true);
      });
    }
    if (s.sort && s.sort.key) {
      const th = document.querySelector(`th[data-key="${s.sort.key}"]`);
      if (th) applySort(th, s.sort.dir);
    }
    if (s.colWidths && typeof s.colWidths === 'object') {
      for (const [key, width] of Object.entries(s.colWidths)) {
        const th = document.querySelector(`#results-table thead tr:first-child th[data-key="${key}"]`);
        if (th) applyColWidth(th, width);
      }
    }
    syncSelectionUI();
  })();

  // Live mode is driven by an http-equiv refresh meta tag in the Jinja
  // template — the browser does a full reload every 5s, which works
  // correctly on file:// URLs. We tried JS-fetch first; it's silently
  // blocked on file:// in Chrome/Edge for security reasons (CORS), so the
  // fetch path was non-functional in practice. Final mode strips the
  // refresh tag at template-render time and the page becomes static.

  // ---- Live elapsed tick (header + per-row "running for Ns") ----
  // The server only writes a fresh snapshot on test state changes. Between
  // those, meta-refresh would just show the same stale "elapsed Xs". We
  // tick a JS-side counter every 1s so the header elapsed + running-test
  // cells advance smoothly, regardless of when the snapshot was last
  // touched. Final mode does a single render of the wall time and stops
  // (no ticking — the report is static).
  function fmtClock(epochSeconds) {
    return new Date(epochSeconds * 1000).toLocaleTimeString();
  }
  function fmtSeconds(s) {
    return s.toFixed(1) + 's';
  }
  function tickLiveElapsed() {
    const nowEpoch = Date.now() / 1000;
    const liveRunElapsed = SNAPSHOT_ELAPSED + (nowEpoch - SNAPSHOT_WALL);
    const headerEl = document.getElementById('header-elapsed');
    if (headerEl) headerEl.textContent = liveRunElapsed.toFixed(0) + 's';
    const wallEl = document.getElementById('header-wall');
    if (wallEl) wallEl.textContent = fmtClock(nowEpoch);
    // Per-row: any row currently in 'running' status with a started_wall
    // gets its elapsed cell live-updated to (now - started_wall).
    Array.from(tbody.querySelectorAll('tr[data-status="running"]')).forEach(row => {
      const startedWall = parseFloat(row.dataset.startedWall);
      if (!isNaN(startedWall)) {
        const cell = row.querySelector('td.elapsed-cell');
        if (cell) cell.textContent = fmtSeconds(nowEpoch - startedWall);
      }
    });
  }
  if (DASHBOARD_MODE === 'live') {
    tickLiveElapsed();
    setInterval(tickLiveElapsed, 1000);
  } else {
    // Final mode: render the finish wall-clock once. The header copy
    // shows "ran for Xs · finished HH:MM:SS"; we just need to fill in
    // the time-of-finish from the snapshot's updated_at.
    const finalEl = document.getElementById('header-wall-final');
    if (finalEl) finalEl.textContent = fmtClock(SNAPSHOT_WALL);
  }
})();
