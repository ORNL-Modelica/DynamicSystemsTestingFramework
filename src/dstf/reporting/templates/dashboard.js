// Unified dashboard JS: filter / sort / fetch loop / refresh button.
// Loaded inline via a Jinja include directive in dashboard.html.
// Reads two top-level Jinja-injected constants: DASHBOARD_MODE
// ('live' or 'final') and DASHBOARD_TOTAL (int).

(function() {
  const tbody = document.getElementById('results-tbody');
  const headers = document.querySelectorAll('#results-table thead tr:first-child th[data-sort]');
  const colFilters = document.querySelectorAll('input.col-filter');

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

  function sortBy(th) {
    const key = th.dataset.key;
    const kind = th.dataset.sort;  // 'num' or 'text'
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

    headers.forEach(h => h.classList.remove('sorted-asc', 'sorted-desc'));
    if (dir) th.classList.add('sorted-' + dir);
    lastSorted = { th, dir };

    const rows = Array.from(tbody.querySelectorAll('tr'));
    if (dir === null) {
      // Natural order: sort by data-sort-test (insertion order proxy)
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

  // Live mode is driven by an http-equiv refresh meta tag in the Jinja
  // template — the browser does a full reload every 2s, which works
  // correctly on file:// URLs. We tried JS-fetch first; it's silently
  // blocked on file:// in Chrome/Edge for security reasons (CORS), so the
  // fetch path was non-functional in practice. Final mode strips the
  // refresh tag at template-render time and the page becomes static.
})();
