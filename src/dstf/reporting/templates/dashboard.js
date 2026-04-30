// Unified dashboard JS: filter / sort / fetch loop / refresh button.
// Loaded inline via a Jinja include directive in dashboard.html.
// Reads two top-level Jinja-injected constants: DASHBOARD_MODE
// ('live' or 'final') and DASHBOARD_TOTAL (int).

(function() {
  const tbody = document.getElementById('results-tbody');
  const headers = document.querySelectorAll('#results-table thead tr:first-child th[data-sort]');
  const colFilters = document.querySelectorAll('input.col-filter');

  // ---- Filter (status buttons + per-column text) ----
  let activeStatus = 'all';
  const colFilterValues = {};

  function applyFilters() {
    Array.from(tbody.querySelectorAll('tr')).forEach(row => {
      let visible = true;
      // Status filter. "fail" lumps in timed-out so a single click of the
      // Failed button surfaces both genuine compare-fails and timeouts.
      if (activeStatus !== 'all') {
        if (activeStatus === 'warn') {
          visible = parseInt(row.dataset.sortWarnings || '0') > 0;
        } else if (activeStatus === 'fail') {
          visible = (row.dataset.status === 'fail'
                     || row.dataset.status === 'timed-out');
        } else {
          visible = row.dataset.status === activeStatus;
        }
      }
      // Per-column text filter
      if (visible) {
        for (const [col, q] of Object.entries(colFilterValues)) {
          if (!q) continue;
          const val = (row.dataset[col] || '').toLowerCase();
          if (val.indexOf(q.toLowerCase()) === -1) {
            visible = false;
            break;
          }
        }
      }
      row.style.display = visible ? '' : 'none';
    });
  }

  window.filterRows = function(status, btn) {
    document.querySelectorAll('.filter-bar button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    activeStatus = status;
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
  function renderCounters(counts, total) {
    const cn = document.getElementById('counters');
    const order = ['queued', 'running', 'passed', 'failed', 'timed_out'];
    let html = '';
    for (const k of order) {
      html += `<div class="counter ${k}"><div class="label">${k}</div>` +
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
  // timed-out/queued/running) but the counters use the live-snapshot key
  // names (queued/running/passed/failed/timed_out) so the JS-rendered
  // pills match what status.json carries during a run. Map between them.
  function initialCountsFromRows() {
    const counts = { queued: 0, running: 0, passed: 0, failed: 0, timed_out: 0 };
    Array.from(tbody.querySelectorAll('tr')).forEach(row => {
      const s = row.dataset.status;
      if      (s === 'pass')      counts.passed++;
      else if (s === 'fail')      counts.failed++;
      else if (s === 'sim-fail')  counts.failed++;
      else if (s === 'timed-out') counts.timed_out++;
      else if (s === 'queued')    counts.queued++;
      else if (s === 'running')   counts.running++;
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
