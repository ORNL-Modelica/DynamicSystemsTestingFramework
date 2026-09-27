// The typed column filters: one module the dashboard and (S2) the tool's
// Suite table both load, so a filter means the same thing on each.
//
// DstfFilters.compile(kind, query, {regex, case}) returns null for an empty
// query (no filter), a predicate over a cell's value, or throws
// DstfFilters.BadFilter for a query it cannot read.
//   text   — regex on: the query is a regex, searched. Else a * or ? makes
//            an anchored glob with every other character literal (so
//            `x[1]` is an array index — the rule a spec's `variables` list
//            and the tool's Python `_glob` use); else a substring.
//            Case-insensitive unless `case`.
//   number — `>1e-4`, `>=`, `<`, `<=`, `=`, a bare number (equal), or an
//            inclusive range `1..10`. The predicate is given the raw value
//            (a row's data-sort-<key>), never its display text. A missing
//            value (null / '') never matches.
// A status column is not a typed filter: the count pills filter it.
//
// Same semantics as the Python `_glob`, not the same cost: JS globs and
// regexes backtrack (`_glob` exists because `*a*a*a*b` took >20 s there),
// so a runaway pattern can freeze the browser tab it is typed in — only
// that tab.
//
// dashboard.html inlines this file with a Jinja include, so it must never
// contain a Jinja delimiter: two open braces, or an open brace followed by
// a percent sign or a hash.
(function() {
  class BadFilter extends Error {}

  const NUM = '[+-]?(?:\\d+\\.?\\d*|\\.\\d+)(?:[eE][+-]?\\d+)?';
  const CMP = new RegExp('^(>=|<=|>|<|=)?\\s*(' + NUM + ')$');
  const RANGE = new RegExp('^(' + NUM + ')\\s*\\.\\.\\s*(' + NUM + ')$');

  function str(v) {
    return v === null || v === undefined ? '' : String(v);
  }

  function compileText(q, opts) {
    const flags = opts.case ? '' : 'i';
    if (opts.regex) {
      let re;
      try { re = new RegExp(q, flags); }
      catch (e) { throw new BadFilter(e.message); }
      return (v) => re.test(str(v));
    }
    if (q.includes('*') || q.includes('?')) {
      const body = Array.from(q).map(ch =>
        ch === '*' ? '.*' : ch === '?' ? '.' : ch.replace(/[.*+?^$()|[\]\\{}]/g, '\\$&')
      ).join('');
      const re = new RegExp('^' + body + '$', flags);
      return (v) => re.test(str(v));
    }
    const needle = opts.case ? q : q.toLowerCase();
    return (v) => (opts.case ? str(v) : str(v).toLowerCase()).indexOf(needle) !== -1;
  }

  function toNumber(v) {
    if (v === null || v === undefined || v === '') return null;
    const n = typeof v === 'number' ? v : parseFloat(v);
    return isNaN(n) ? null : n;
  }

  function compileNumber(q) {
    const t = q.trim();
    let m = RANGE.exec(t);
    if (m) {
      const lo = parseFloat(m[1]), hi = parseFloat(m[2]);
      return (v) => { const n = toNumber(v); return n !== null && n >= lo && n <= hi; };
    }
    m = CMP.exec(t);
    if (!m) throw new BadFilter('Not a comparison (>1e-4) or a range (1..10): ' + q);
    const x = parseFloat(m[2]);
    const test = {
      '>': (n) => n > x, '>=': (n) => n >= x, '<': (n) => n < x,
      '<=': (n) => n <= x, '=': (n) => n === x,
    }[m[1] || '='];
    return (v) => { const n = toNumber(v); return n !== null && test(n); };
  }

  function compile(kind, query, opts) {
    if (!query) return null;
    if (kind === 'number') return compileNumber(query);
    return compileText(query, opts || {});
  }

  window.DstfFilters = { compile, BadFilter };
})();
