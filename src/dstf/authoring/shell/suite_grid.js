// The Suite table's client side: group rows, the typed filters (through
// filters.js — DstfFilters), the count pills, the toolbar's view toggles, a
// sort within each group, the selection's label, and the view that persists
// per viewer in localStorage. Python sends rows (DstfSuite.apply, one
// applyTransaction) and receives the selection; it never updates the grid
// element, which would rebuild the grid (suite_grid.py).
(function () {
  // Per project (the page sets it before this script): two tabs on one
  // project share a view, two projects do not.
  const KEY = window.DstfSuiteKey || 'dstf-suite-v1';
  const PILLS = [['pass', 'PASS'], ['fail', 'FAIL'], ['sim-fail', 'SIM_FAIL'],
                 ['no-ref', 'NO_REF'], ['edited', 'EDITED'], ['running', 'RUNNING']];
  const S = {
    grouped: true, short: true, regex: false, case: false,
    pills: [], filters: {}, collapsed: [], columns: null, sort: null,
  };
  const D = { state: S, kinds: {}, api: null, matchers: [], bad: {}, gcount: {}, counts: {} };

  // A per-viewer convenience: storage may be absent or refuse (a private
  // window, blocked site data); the table works without it.
  function load() {
    try { Object.assign(S, JSON.parse(localStorage.getItem(KEY)) || {}); } catch (e) { /* none */ }
  }
  function save() {
    try {
      if (D.api) {
        const cols = D.api.getColumnState();
        S.columns = cols.map(c => ({ colId: c.colId, hide: c.hide }));
        S.sort = cols.filter(c => c.sort).map(c => ({ colId: c.colId, sort: c.sort }));
      }
      localStorage.setItem(KEY, JSON.stringify(S));
    } catch (e) { /* none */ }
  }
  load();

  // -- filtering --------------------------------------------------------------
  function value(r, key) {
    // A number filter reads the value (the __sort), never its text; a text
    // filter reads the cell's text only when the row HAS a value (S1's
    // Column.filter_text: an empty cell reads "—", which `.` would match).
    const sort = r[key + '__sort'];
    if (D.kinds[key] === 'number') return sort;
    return sort === null || sort === undefined ? '' : r[key];
  }
  function compile() {
    D.matchers = []; D.bad = {};
    for (const [key, q] of Object.entries(S.filters)) {
      let pred = null;
      try { pred = DstfFilters.compile(D.kinds[key] || 'text', q, { regex: S.regex, case: S.case }); }
      catch (e) { if (!(e instanceof DstfFilters.BadFilter)) throw e; D.bad[key] = e.message; }
      if (pred) D.matchers.push(r => pred(value(r, key)));
    }
    // The pills filter the status; EDITED is OR'd with them.
    const pills = new Set(S.pills);
    if (pills.size) {
      D.matchers.push(r => pills.has(r.status_class) || (pills.has('edited') && r.edits__sort > 0));
    }
  }
  function passes(r) { for (const m of D.matchers) if (!m(r)) return false; return true; }

  D.externalPass = (node) => {
    const r = node.data;
    if (r._g) return S.grouped && (D.gcount[r._pkg] || 0) > 0;
    return passes(r) && (!S.grouped || !S.collapsed.includes(r._pkg));
  };

  // -- sorting ----------------------------------------------------------------
  D.comparator = (key) => (a, b, na, nb, desc) => {
    const x = na.data[key + '__sort'], y = nb.data[key + '__sort'];
    const xn = x === null || x === undefined, yn = y === null || y === undefined;
    if (xn || yn) {
      if (xn && yn) return 0;
      // Empties last in both directions: AG Grid negates for descending.
      return (xn ? 1 : -1) * (desc ? -1 : 1);
    }
    return x < y ? -1 : x > y ? 1 : 0;
  };
  // Grouped: each package's header, then its rows in the order the sort
  // left them — AG Grid Community has no row grouping, so it is done here.
  D.postSort = (p) => {
    if (!S.grouped) return;
    const order = [], by = new Map();
    for (const n of p.nodes) {
      const k = n.data._pkg;
      if (!by.has(k)) { by.set(k, { g: null, rs: [] }); order.push(k); }
      const b = by.get(k);
      if (n.data._g) b.g = n; else b.rs.push(n);
    }
    order.sort();
    const out = [];
    for (const k of order) { const b = by.get(k); if (b.g) out.push(b.g); out.push(...b.rs); }
    p.nodes.length = 0; p.nodes.push(...out);
  };

  // -- recount and repaint ----------------------------------------------------
  function recount(api) {
    compile();
    const g = {}, c = {};
    for (const [k] of PILLS) c[k] = 0;
    api.forEachNode(n => {
      const r = n.data;
      if (r._g) return;
      // The pills count every row, whatever the filters (the dashboard's rule).
      if (r.status_class in c) c[r.status_class]++;
      if (r.edits__sort > 0) c.edited++;
      if (passes(r)) g[r._pkg] = (g[r._pkg] || 0) + 1;
    });
    D.gcount = g; D.counts = c;
  }
  D.refresh = (api) => {
    api = api || D.api;
    if (!api) return;
    recount(api);
    api.onFilterChanged();
    const groups = [];
    api.forEachNode(n => { if (n.data._g) groups.push(n); });
    api.redrawRows({ rowNodes: groups });
    renderToolbar();
    D.renderSelection();
    document.querySelectorAll('.dstf-ff').forEach(el => {
      const bad = D.bad[el.dataset.col];
      el.classList.toggle('bad', !!bad);
      el.title = bad || el.dataset.help;
    });
    document.querySelectorAll('.dstf-ff-status').forEach(el => {
      el.textContent = S.pills.length
        ? S.pills.map(p => PILLS.find(x => x[0] === p)[1]).join(' | ') : 'all';
    });
  };

  // -- rows from Python -------------------------------------------------------
  function groupRow(pkg) { return { id: 'g:' + pkg, _g: true, _pkg: pkg }; }
  // A group row for every package with rows, none for a package without.
  function regroup(api) {
    const rows = new Set(), groups = new Set();
    api.forEachNode(n => (n.data._g ? groups : rows).add(n.data._pkg));
    const add = [...rows].filter(k => !groups.has(k)).map(groupRow);
    const remove = [...groups].filter(k => !rows.has(k)).map(k => ({ id: 'g:' + k }));
    if (add.length || remove.length) api.applyTransaction({ add, remove });
  }
  // One transaction from Python: {add, update, remove, select}. `select`
  // names added rows the tab has selected (rows that arrive after the grid
  // was ready — drawn before the library scan finished).
  D.apply = (tx) => {
    const api = D.api;
    if (!api) { D.pending = (D.pending || []).concat([tx]); return; }
    api.applyTransaction({
      add: tx.add || [], update: tx.update || [],
      remove: (tx.remove || []).map(id => ({ id })),
    });
    regroup(api);
    const want = new Set(tx.select || []);
    if (want.size) {
      const nodes = [];
      api.forEachNode(n => { if (want.has(n.data.id)) nodes.push(n); });
      api.setNodesSelected({ nodes, newValue: true, source: 'api' });
    }
    D.refresh(api);
  };

  // -- the grid's components --------------------------------------------------
  D.GroupRow = class {
    init(p) {
      this.p = p;
      this.e = document.createElement('div');
      this.e.className = 'dstf-grp';
      this.e.setAttribute('role', 'button');
      this.e.addEventListener('click', () => D.toggleGroup(p.data._pkg));
      this.render();
    }
    render() {
      const k = this.p.data._pkg, closed = S.collapsed.includes(k), n = D.gcount[k] || 0;
      this.e.setAttribute('aria-expanded', closed ? 'false' : 'true');
      this.e.innerHTML = `<i class="q-icon material-icons">${closed ? 'chevron_right' : 'expand_more'}</i>`
        + `<span class="dstf-grp-name"></span>`
        + `<span class="dstf-grp-count">${n} ${n === 1 ? 'test' : 'tests'}</span>`;
      this.e.querySelector('.dstf-grp-name').textContent = k || '(top level)';
    }
    getGui() { return this.e; }
    refresh() { this.render(); return true; }
  };
  D.toggleGroup = (pkg) => {
    const i = S.collapsed.indexOf(pkg);
    if (i >= 0) S.collapsed.splice(i, 1); else S.collapsed.push(pkg);
    save(); D.refresh();
  };
  // The keyboard's clicks: Enter / Space on a focused group row collapses
  // it; Enter on a focused Test cell opens the test's page, as its link does.
  D.keyDown = (e) => {
    const k = e.event && e.event.key;
    const r = e.node && e.node.data;
    if (!r) return;
    if (r._g && (k === 'Enter' || k === ' ')) {
      e.event.preventDefault();
      D.toggleGroup(r._pkg);
    } else if (!r._g && k === 'Enter' && e.column && e.column.getColId() === 'model') {
      e.event.preventDefault();
      window.location.assign('/test/' + encodeURIComponent(r.id));
    }
  };
  // Every column "has" a filter so AG Grid draws the floating filter row;
  // the filtering itself is the external filter's.
  D.NoFilter = class {
    init() {} getGui() { return document.createElement('span'); }
    doesFilterPass() { return true; } isFilterActive() { return false; }
    getModel() { return null; } setModel() {}
  };
  D.FloatingInput = class {
    init(p) {
      const col = p.column.getColId();
      if (p.kind === 'status') {
        // The status is filtered by the pills: this cell mirrors them.
        this.e = document.createElement('span');
        this.e.className = 'dstf-ff-status';
        this.e.textContent = 'all';
        return;
      }
      this.e = document.createElement('input');
      this.e.className = 'dstf-ff';
      this.e.dataset.col = col;
      this.e.dataset.help = p.help;
      this.e.title = p.help;
      this.e.placeholder = p.placeholder;
      this.e.setAttribute('aria-label', 'Filter ' + p.column.getColDef().headerName);
      this.e.value = S.filters[col] || '';
      this.e.addEventListener('input', () => {
        if (this.e.value) S.filters[col] = this.e.value; else delete S.filters[col];
        save(); D.refresh(p.api);
      });
    }
    getGui() { return this.e; }
    onParentModelChanged() {}
  };
  function esc(s) {
    return String(s).replace(/[&<>"']/g, c => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
  D.cell = {
    status: (p) => `<span class="dstf-badge-cell" `
      + `style="background: var(--dstf-${p.data.status_class}-fill)">${esc(p.value)}</span>`,
    // No `title`: the column's tooltip names the test over the whole cell,
    // and a title on the link would show a second one over it.
    model: (p) => {
      const name = S.short ? p.data._name_short : p.data._name_full;
      return `<a class="dstf-name-link" href="/test/${encodeURIComponent(p.data.id)}">`
        + `${esc(name)}</a>`;
    },
    edits: (p) => (p.value ? `<span class="dstf-edits">${esc(p.value)}</span>` : ''),
  };
  D.rowClass = (p) => (p.data && !p.data._g ? 'dstf-tint-' + p.data.status_class : '');

  // -- the toolbar and the selection's label (their state is the browser's) --
  // `key` names the control across repaints (the focus follows it).
  function btn(key, icon, tip, pressed, onclick, extra) {
    const b = document.createElement('button');
    b.type = 'button';
    b.dataset.tb = key;
    b.className = 'dstf-tb' + (pressed ? ' on' : '') + (extra ? ' ' + extra : '');
    b.title = tip;
    b.setAttribute('aria-label', tip);
    if (pressed !== null) b.setAttribute('aria-pressed', pressed ? 'true' : 'false');
    b.innerHTML = icon.startsWith('txt:')
      ? `<span class="dstf-tb-txt">${esc(icon.slice(4))}</span>`
      : `<i class="q-icon material-icons">${icon}</i>`;
    b.addEventListener('click', onclick);
    return b;
  }
  // Drawn again on every refresh — every live transaction too — so the
  // focused control is focused again: Space on a pill or Enter on a view
  // icon dropped the keyboard to the page.
  function renderToolbar() {
    const host = document.getElementById('dstf-suite-tools');
    if (!host) return;
    const had = host.contains(document.activeElement) ? document.activeElement.dataset.tb : null;
    host.textContent = '';
    const pills = document.createElement('span');
    pills.className = 'dstf-pills';
    for (const [k, word] of PILLS) {
      const n = D.counts[k] || 0;
      const on = S.pills.includes(k);
      // Shown while a job runs — and while it filters, reading 0, so a
      // pill switched on can be switched off once the job has ended.
      if (k === 'running' && !n && !on) continue;
      const b = btn('pill:' + k, 'txt:' + word + ' ' + n, (on ? 'Stop showing only ' : 'Show only ') + word, on, () => {
        const i = S.pills.indexOf(k);
        if (i >= 0) S.pills.splice(i, 1); else S.pills.push(k);
        save(); D.refresh();
      }, 'dstf-pill-btn');
      b.dataset.pill = k;
      b.setAttribute('aria-label', word + ' ' + n);  // the text: what it counts
      if (!n && !on) b.disabled = true;
      pills.appendChild(b);
    }
    host.appendChild(pills);
    // A view button's label says what a click does; `.*` and `Aa` are
    // toggles with a fixed name and aria-pressed.
    const views = document.createElement('span');
    views.className = 'dstf-views';
    views.appendChild(btn('group', 'account_tree', S.grouped ? 'Show flat' : 'Group by package', null, () => {
      S.grouped = !S.grouped; save(); D.api.refreshClientSideRowModel('sort'); D.refresh();
    }));
    views.appendChild(btn('names', 'label', S.short ? 'Full names' : 'Short names', null, () => {
      S.short = !S.short; save(); D.api.refreshCells({ columns: ['model'], force: true }); D.refresh();
    }));
    const cols = btn('columns', 'view_column', 'Choose columns', null, (e) => { e.stopPropagation(); D.columnMenu(cols); });
    cols.setAttribute('aria-haspopup', 'menu');
    views.appendChild(cols);
    views.appendChild(btn('regex', 'txt:.*', 'Regex in every column filter', S.regex, () => {
      S.regex = !S.regex; save(); D.refresh();
    }));
    views.appendChild(btn('case', 'txt:Aa', 'Match case in every column filter', S.case, () => {
      S.case = !S.case; save(); D.refresh();
    }));
    host.appendChild(views);
    if (had) {
      const again = host.querySelector(`[data-tb="${had}"]`);
      if (again) again.focus();
    }
  }
  // "2 selected · ✕", with the selected rows a filter or a collapsed group
  // hides counted: the actions act on every selected row.
  D.renderSelection = () => {
    const host = document.getElementById('dstf-suite-selection');
    if (!host || !D.api) return;
    const ids = D.api.getSelectedRows().filter(r => !r._g).map(r => r.id);
    const shown = new Set();
    D.api.forEachNodeAfterFilter(n => { if (n.data && !n.data._g) shown.add(n.data.id); });
    const hidden = ids.filter(id => !shown.has(id)).length;
    host.textContent = '';
    if (!ids.length) return;
    const label = document.createElement('span');
    label.textContent = `${ids.length} selected` + (hidden ? ` (${hidden} hidden)` : '') + ' ·';
    host.appendChild(label);
    const clear = btn('clear', 'close', 'Clear selection', null, () => D.api.deselectAll());
    clear.classList.add('dstf-clear');
    host.appendChild(clear);
  };
  D.columnMenu = (anchor) => {
    let m = document.getElementById('dstf-colmenu');
    if (m) { m.remove(); return; }
    m = document.createElement('div');
    m.id = 'dstf-colmenu';
    m.setAttribute('role', 'menu');
    for (const c of D.api.getColumns()) {
      const def = c.getColDef();
      if (!def.field) continue;  // the selection column
      const lab = document.createElement('label');
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.checked = c.isVisible();
      box.addEventListener('change', () => { D.api.setColumnsVisible([c.getColId()], box.checked); save(); });
      lab.appendChild(box);
      lab.appendChild(document.createTextNode(' ' + def.headerName));
      m.appendChild(lab);
    }
    const r = anchor.getBoundingClientRect();
    m.style.top = (r.bottom + window.scrollY + 4) + 'px';
    m.style.left = Math.max(8, Math.min(r.left, window.innerWidth - 220)) + 'px';
    document.body.appendChild(m);
    const close = () => {
      m.remove();
      document.removeEventListener('click', off);
      document.removeEventListener('keydown', esc_);
    };
    const off = (e) => { if (!m.contains(e.target)) close(); };
    const esc_ = (e) => { if (e.key === 'Escape') { close(); anchor.focus(); } };
    setTimeout(() => {
      document.addEventListener('click', off);
      document.addEventListener('keydown', esc_);
    });
  };

  // -- ready ------------------------------------------------------------------
  D.ready = (p, kinds, selected) => {
    D.api = p.api;
    D.kinds = kinds;
    if (S.columns) p.api.applyColumnState({ state: S.columns });
    if (S.sort) p.api.applyColumnState({ state: S.sort, defaultState: { sort: null } });
    regroup(p.api);
    for (const tx of D.pending || []) D.apply(tx);
    D.pending = [];
    const want = new Set(selected || []);
    const nodes = [];
    p.api.forEachNode(n => { if (want.has(n.data.id)) nodes.push(n); });
    if (nodes.length) p.api.setNodesSelected({ nodes, newValue: true, source: 'api' });
    D.refresh(p.api);
  };
  D.saveState = save;
  window.DstfSuite = D;
})();
