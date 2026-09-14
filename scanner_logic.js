/* Raw source values stay intact; display conversion and membership are separate. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.ScannerLogic = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const isNumber = value => typeof value === 'number' && Number.isFinite(value);
  const clean = (value, digits = 2) => {
    if (!isNumber(value)) return null;
    const rounded = Number(value.toFixed(digits));
    return Object.is(rounded, -0) ? 0 : rounded;
  };
  function number(value, digits = 2, signed = false) {
    const n = clean(value, digits);
    return n === null ? '—' : (signed && n > 0 ? '+' : '') + n.toLocaleString('ko-KR', { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }
  const amount = value => isNumber(value) ? value / 100 : null;
  function metric(row, investor, mode) {
    const field = investor === 'inst' ? 'i' : 'f';
    const raw = mode === 'ratio' ? row._raw?.ratio_pct?.[investor] : row._raw?.net_won?.[investor];
    if (isNumber(raw)) return raw;
    const net = row['net' + field];
    // Legacy snapshots store net in million KRW and cap in hundred-million
    // KRW. net/cap is already a percent number, using that snapshot's cap.
    if (mode === 'ratio') return isNumber(net) && isNumber(row.mcap) && row.mcap > 0 ? net / row.mcap : row[field + '1'];
    return isNumber(net) ? net * 1e6 : null;
  }
  function rankSeries(row, investor) {
    const raw = row._raw?.[investor];
    // An explicit null exact rank means unranked; it is never legacy bucket 250.
    if (raw && typeof raw === 'object') return [raw.two_days_ago ?? null, raw.previous ?? null, raw.today ?? null];
    return investor === 'inst' ? [row.id2, row.id1, row.it] : [row.fd2, row.fd1, row.ft];
  }
  function strongBoth(row) {
    return metric(row, 'inst', 'ratio') >= 0.3 && metric(row, 'frgn', 'ratio') >= 0.3;
  }
  function top(rows, investor, mode, limit = 30) {
    return rows.filter(row => row.code && isNumber(metric(row, investor, mode)) && metric(row, investor, mode) > 0)
      .slice().sort((a, b) => metric(b, investor, mode) - metric(a, investor, mode) || String(a.code).localeCompare(String(b.code)))
      .slice(0, limit).map((row, index) => ({ row, rank: index + 1 }));
  }
  function compare(current, previous, investor, mode, available) {
    const now = top(current, investor, mode);
    const before = available ? top(previous, investor, mode) : [];
    const nowRanks = new Map(now.map(item => [item.row.code, item.rank]));
    const beforeRanks = new Map(before.map(item => [item.row.code, item.rank]));
    return {
      current: now.map(item => ({ ...item, previousRank: beforeRanks.get(item.row.code) ?? null, status: !available ? 'pending' : beforeRanks.has(item.row.code) ? 'retained' : 'new' })),
      exited: before.filter(item => !nowRanks.has(item.row.code)).map(item => ({ ...item, previousRank: item.rank, rank: null, status: 'exited' }))
    };
  }
  return { isNumber, clean, number, amount, metric, rankSeries, strongBoth, top, compare };
});
