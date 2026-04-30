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
      // Status filter
      if (activeStatus !== 'all') {
        if (activeStatus === 'warn') {
          visible = parseInt(row.dataset.sortWarnings || '0') > 0;
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
      // Cycle: firstClick → opposite → none
      if (lastSorted.dir === firstClick) {
        dir = (firstClick === 'asc') ? 'desc' : 'asc';
      } else {
        dir = null;  // back to natural order
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
      html += `<div class="counter"><div class="label">${k}</div>` +
              `<div class="value">${counts[k] || 0}</div></div>`;
    }
    html += `<div class="counter"><div class="label">Total</div>` +
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

  // Initial render from inline data (read from data-* on tbody rows)
  function initialCountsFromRows() {
    const counts = { queued: 0, running: 0, passed: 0, failed: 0, timed_out: 0 };
    Array.from(tbody.querySelectorAll('tr')).forEach(row => {
      const s = row.dataset.status;
      if (s in counts) counts[s]++;
      else if (s === 'sim-fail') counts.failed++;
    });
    return counts;
  }
  renderCounters(initialCountsFromRows(), DASHBOARD_TOTAL);

  // ---- Live-mode fetch loop ----
  // Polls fetch('status.json') every 2s and patches mutable row fields.
  if (DASHBOARD_MODE === 'live') {
    let intervalId = null;

    function updateRowFromSnapshot(t) {
      // Update an existing row's mutable fields from the fresh snapshot.
      // Append a new row if it didn't exist (test registered mid-run).
      let row = tbody.querySelector(`tr[data-test="${t.test_key}"]`);
      if (!row) {
        // Row doesn't exist in current DOM — page reload would catch it,
        // but for now just trigger a soft full-reload to pick up new schema
        location.reload();
        return;
      }
      const status = (t.status || 'queued').replace('_', '-');
      row.dataset.status = status;
      row.dataset.sortStatus = t.status || 'queued';
      row.dataset.sortElapsed = (t.elapsed != null) ? t.elapsed : -1;
      row.dataset.sortWorker = (t.worker_id != null) ? t.worker_id : -1;
      row.className = status;
      // Re-render the cells inline (cheaper than full row replacement)
      const cells = row.querySelectorAll('td');
      cells[2].innerHTML = `<span class="${status}">${t.status || 'queued'}</span>`;
      // cells[3] = Resolution column (rendered server-side; field_sources
      // doesn't change per status update so leave it intact)
      cells[4].textContent = (t.worker_id != null) ? `W${t.worker_id}` : '—';
      cells[5].textContent = (t.elapsed != null) ? t.elapsed.toFixed(1) : '—';
    }

    async function poll() {
      try {
        const r = await fetch('status.json', { cache: 'no-store' });
        if (!r.ok) return;
        const snap = await r.json();
        renderCounters(snap.counts || {}, snap.total || 0);
        for (const t of snap.tests || []) updateRowFromSnapshot(t);
        applyFilters();
        const stamp = document.getElementById('updated-stamp');
        if (stamp) stamp.textContent = 'Updated ' + new Date().toLocaleTimeString();
        const elap = document.getElementById('elapsed-stamp');
        if (elap) elap.textContent = (snap.elapsed || 0).toFixed(0) + 's';
      } catch (e) {
        // Silent: a transient fetch failure shouldn't break the page.
      }
    }

    window.refreshNow = function() { poll(); };
    intervalId = setInterval(poll, 2000);
    poll();  // immediate first fetch
  } else {
    // Final mode: refresh button still works (one-shot reload)
    window.refreshNow = function() { location.reload(); };
  }
})();
