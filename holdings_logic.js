(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.HoldingsLogic = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const number = value => typeof value === 'number' && Number.isFinite(value);
  const list = value => Array.isArray(value) ? value : [];
  const periodKey = period => period === 'weekly' ? 'weekly' : 'daily';
  const validDate = value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value + 'T00:00:00Z')) && new Date(value + 'T00:00:00Z').toISOString().slice(0, 10) === value;
  const pairKey = pair => pair && validDate(pair.from_date) && validDate(pair.to_date) ? pair.from_date + '/' + pair.to_date : '';
  const representative = fund => fund.representative !== false && fund.representative !== 'supplemental';
  const readyStatuses = ['current', 'fresh', 'ready', 'baseline_only'];
  const normalize = value => String(value || '').trim().toLocaleLowerCase();
  function netAssets(record) {
    return !!record && ['verified', 'stale'].includes(record.status) && record.metric === 'net_assets' && record.currency === 'KRW' && number(record.value_krw) && record.value_krw > 0 && validDate(record.asof);
  }
  function startAum(comparison) {
    const record = comparison && comparison.start_aum;
    return comparison && comparison.status === 'ready' && netAssets(record) && record.status === 'verified' && record.asof === comparison.from_date ? record : null;
  }
  function amountDelta(change, comparison) {
    const aum = startAum(comparison);
    const amount = aum && change && number(change.delta_weight_pp) ? aum.value_krw * (change.delta_weight_pp / 100) : null;
    return number(amount) ? amount : null;
  }
  function expectedPair(data, period) {
    return {from_date: data[periodKey(period) === 'weekly' ? 'weekly_date' : 'previous_date'], to_date: data.expected_date};
  }
  function selectedFunds(data, options = {}) {
    const seen = new Set();
    return list(data.funds).filter(f => {
      if (!f || !f.id || seen.has(f.id)) return false;
      seen.add(f.id);
      return (options.supplements || representative(f)) && (!options.group || options.group === 'all' || f.group === options.group) &&
        (!options.issuer || options.issuer === 'all' || f.issuer === options.issuer);
    });
  }
  function eligible(fund, comparison, data) {
    return !!comparison && comparison.status === 'ready' && !!pairKey(comparison) && comparison.from_date < comparison.to_date &&
      readyStatuses.includes(fund.status) &&
      fund.asof === comparison.to_date && (!data.expected_date || comparison.to_date <= data.expected_date);
  }
  function isCurrent(fund, data) { return readyStatuses.includes(fund.status) && fund.asof === data.expected_date && list(fund.latest_holdings).length > 0; }
  function cohorts(data, options = {}) {
    const period = periodKey(options.period), byPair = new Map(), expected = expectedPair(data, period);
    if (pairKey(expected)) byPair.set(pairKey(expected), {...expected, fund_ids: [], expected: true});
    for (const fund of selectedFunds(data, options)) {
      const comp = fund[period];
      if (!eligible(fund, comp, data)) continue;
      const key = pairKey(comp);
      if (!byPair.has(key)) byPair.set(key, {from_date: comp.from_date, to_date: comp.to_date, fund_ids: [], expected: key === pairKey(expected)});
      byPair.get(key).fund_ids.push(fund.id);
    }
    return [...byPair.values()].sort((a, b) => Number(b.expected) - Number(a.expected) || b.to_date.localeCompare(a.to_date) || b.from_date.localeCompare(a.from_date));
  }
  function aggregate(data, options = {}) {
    const selected = selectedFunds(data, options), period = periodKey(options.period), pairs = cohorts(data, options);
    const chosen = pairs.find(p => pairKey(p) === options.cohort) || pairs[0] || expectedPair(data, period), key = pairKey(chosen);
    const comparable = selected.filter(f => eligible(f, f[period], data) && pairKey(f[period]) === key);
    const byCode = new Map();
    for (const fund of comparable) {
      const seenCodes = new Set();
      for (const change of list(fund[period].changes)) {
        if (!change || !change.code || seenCodes.has(change.code) || (change.asset_type && change.asset_type !== 'equity') || !number(change.delta_weight_pp)) continue;
        // The backend admits only verified equity identifiers; do not infer equity from a numeric code here.
        seenCodes.add(change.code);
        if (!byCode.has(change.code)) byCode.set(change.code, {code: change.code, name: change.name || change.code, increased: 0, decreased: 0, new_count: 0, removed_count: 0, observed: 0, quantity_increased: 0, quantity_decreased: 0, quantity_unchanged: 0, quantity_unavailable: 0, details: [], issuers: new Set(), groups: new Set()});
        const row = byCode.get(change.code), increased = ['new', 'increased'].includes(change.event), decreased = ['removed', 'decreased'].includes(change.event);
        row.increased += Number(increased); row.decreased += Number(decreased); row.new_count += Number(change.event === 'new'); row.removed_count += Number(change.event === 'removed'); row.observed += 1;
        if (fund.issuer) row.issuers.add(fund.issuer);
        if (fund.group) row.groups.add(fund.group);
        const quantity = ['increased', 'decreased', 'unchanged'].includes(change.quantity_status) ? change.quantity_status : 'unavailable';
        row['quantity_' + quantity] += 1;
        row.details.push({...change, aum: fund.aum, start_aum: startAum(fund[period]), amount_delta_krw: amountDelta(change, fund[period]), etf_id: fund.id, etf_symbol: fund.symbol, etf_name: fund.name, issuer: fund.issuer, group: fund.group, kind: fund.kind, representative: representative(fund), source: fund.source, source_url: fund.source_url, observed_at: fund.observed_at, collection_mode: fund.collection_mode, from_date: fund[period].from_date, to_date: fund[period].to_date});
      }
    }
    const query = normalize(options.query), minimum = options.minimum === 1 || options.minimum === '1' ? 1 : 2;
    const all = [...byCode.values()].map(row => {
      const amounts = row.details.map(detail => detail.amount_delta_krw).filter(number);
      const sum = amounts.length ? amounts.reduce((total, value) => total + value, 0) : null;
      return {...row, net: row.increased - row.decreased, average_delta_pp: row.details.reduce((sum, r) => sum + r.delta_weight_pp, 0) / row.observed,
        amount_delta_krw: number(sum) ? sum : null, amount_covered_count: amounts.length, amount_complete: amounts.length === row.observed,
        issuer_count: row.issuers.size, group_count: row.groups.size, issuers: [...row.issuers], groups: [...row.groups],
        details: row.details.sort((a, b) => b.delta_weight_pp - a.delta_weight_pp || String(a.etf_id).localeCompare(String(b.etf_id)))};
    });
    const averageOrder = (a, b) => b.average_delta_pp - a.average_delta_pp || b.increased - a.increased || b.net - a.net || String(a.code).localeCompare(String(b.code));
    const amountOrder = (a, b) => Number(number(b.amount_delta_krw)) - Number(number(a.amount_delta_krw)) || (number(a.amount_delta_krw) && number(b.amount_delta_krw) ? b.amount_delta_krw - a.amount_delta_krw : 0) || averageOrder(a, b);
    const rows = all.filter(row => row.increased >= minimum && (!query || normalize([row.name, row.code, ...row.details.flatMap(d => [d.etf_name, d.etf_symbol, d.issuer, d.group])].join(' ')).includes(query)))
      .sort(options.sort === 'amount' ? amountOrder : averageOrder);
    return {rows, all, selected_count: selected.length, comparable_count: comparable.length, comparable_ids: comparable.map(f => f.id), pair: chosen, pairs, history_pending: comparable.length === 0, minimum};
  }
  function matchesFund(fund, query) {
    const q = normalize(query);
    return !q || normalize([fund.name, fund.symbol, fund.id, fund.issuer, fund.group, ...list(fund.latest_holdings).flatMap(h => [h.code, h.name])].join(' ')).includes(q);
  }
  function statusLabel(status) {
    return ({current: '기준일 자료 확보', fresh: '기준일 자료 확보', ready: '비교 가능', baseline_only: '첫 기준점 확보', waiting: '이력 축적 중', incomplete: '전체 구성 미확인', basis_mismatch: '비교 기준 다름', stale: '자료 미갱신', failed: '수집 실패', missing: '자료 미확보', unavailable: '자료 미확보', unsupported: '수집 경로 미확보', source_unavailable: '원천 자료 없음', cached_after_failure: '수집 실패 · 저장 자료 참고'})[status] || '상태 확인 필요';
  }
  const eventLabel = value => ({new: '신규 편입', removed: '편출', increased: '비중 확대', decreased: '비중 축소', unchanged: '기준 미만 변동'})[value] || '확인 불가';
  const quantityLabel = value => ({increased: '기준 수량 증가', decreased: '기준 수량 감소', unchanged: '기준 수량 동일', unavailable: '수량 확인 불가'})[value] || '수량 확인 불가';
  return {number, list, netAssets, startAum, amountDelta, periodKey, pairKey, representative, expectedPair, selectedFunds, isCurrent, eligible, cohorts, aggregate, matchesFund, statusLabel, eventLabel, quantityLabel};
});
