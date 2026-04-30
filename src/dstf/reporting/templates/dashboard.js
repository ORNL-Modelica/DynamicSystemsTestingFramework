// Unified dashboard JS: filter / sort / fetch loop / refresh button.
// Loaded inline via a Jinja include directive in dashboard.html.
// Reads two top-level Jinja-injected constants: DASHBOARD_MODE
// ('live' or 'final') and DASHBOARD_TOTAL (int).

(function() {
  const tbody = document.getElementById('results-tbody');
  const headers = document.querySelectorAll('#results-table thead tr:first-child th[data-sort]');
  const colFilters = document.querySelectorAll('input.col-filter');

  // ---- Filter (status buttons + per-column text) ----
  // Status pills are multi-toggle: "All" clears the selection; any other
  // button toggles its status in/out of an active Set. Empty set means
  // show everything (functionally equivalent to "All" being on).
  const activeStatuses = new Set();
  const colFilterValues = {};

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
    for (const s of activeStatuses) {
      if (s === 'warn' && sortWarnings > 0) return true;
      // "fail" lumps in timed-out so a single click of Failed surfaces
      // both genuine compare-fails and timeouts.
      if (s === 'fail' && (rowStatus === 'fail' || rowStatus === 'timed-out')) return true;
      if (s === rowStatus) return true;
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

  function syncFilterButtonStates() {
    const allBtn = document.querySelector('.filter-bar button[data-status="all"]');
    document.querySelectorAll('.filter-bar button[data-status]').forEach(b => {
      const s = b.dataset.status;
      if (s === 'all') {
        b.classList.toggle('active', activeStatuses.size === 0);
      } else {
        b.classList.toggle('active', activeStatuses.has(s));
      }
    });
  }

  window.filterRows = function(status, btn) {
    if (status === 'all') {
      activeStatuses.clear();
    } else if (activeStatuses.has(status)) {
      activeStatuses.delete(status);
    } else {
      activeStatuses.add(status);
    }
    syncFilterButtonStates();
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
  // The counters reflect the current row population. During a live run
  // status_class only takes the values queued/running/pass/fail/timed-out
  // (sim_failed and no_ref need a comparison phase to produce). After
  // comparison sidecars overlay row status_class with sim-fail/no-ref,
  // those pills come alive. Pills with zero count still render so the
  // user sees the full set and can spot the moment an outcome appears.
  function renderCounters(counts, total) {
    const cn = document.getElementById('counters');
    // Display order chosen so transient (queued/running) come first,
    // then outcomes (passed → failures), then warnings + total at end.
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
      html += `<div class="counter ${k}"><div class="label">${label}</div>` +
              `<div class="value">${counts[k] || 0}</div></div>`;
    }
    html += `<div class="counter total"><div class="label">Total</div>` +
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
