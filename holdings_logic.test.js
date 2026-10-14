const assert = require('node:assert/strict');
const test = require('node:test');
const L = require('./holdings_logic.js');
const dates = {expected_date: '2026-10-08', previous_date: '2026-10-07', weekly_date: '2026-09-30'};
const change = (overrides = {}) => ({code: '005930', name: '삼성전자', previous_weight_pct: 10, current_weight_pct: 10.3, delta_weight_pp: 0.3, event: 'increased', quantity_status: 'unchanged', quantity_change_pct: 0, quantity_note: 'CU 기준 정규화 수량', ...overrides});
function fund(id, overrides = {}) {
  return {id, symbol: id.replace('KR:', ''), name: id + ' 반도체', issuer: '운용사 A', group: '반도체', representative: true, status: 'current', asof: dates.expected_date, source: 'KODEX', source_url: 'https://example.com/fund', observed_at: '2026-10-11T11:30:00Z', latest_holdings: [{code: '005930', name: '삼성전자', asset_type: 'equity', weight_pct: 10.3, quantity: 100}], holding_count: 1, complete: true, quantity_basis: 'per_cu', creation_unit_shares: 10000, daily: {status: 'ready', from_date: dates.previous_date, to_date: dates.expected_date, changes: [change()]}, weekly: {status: 'waiting', from_date: dates.weekly_date, to_date: dates.expected_date, changes: []}, ...overrides};
}
const dataset = funds => ({schema_version: 'etf-holdings-v1', ...dates, generated_at: '2026-10-11T11:30:00Z', universe_version: 'test', threshold_pp: 0.05, funds, coverage: {registered: funds.length, current: funds.length, daily_comparable: funds.length, weekly_comparable: 0, failed: 0, stale: 0, baseline_only: 0}});

test('missing comparison is history accumulation, not a confirmed zero', () => {
  const data = dataset([fund('KR:1', {daily: {status: 'waiting', reason: '첫 기준점'}})]);
  const result = L.aggregate(data);
  assert.equal(result.history_pending, true); assert.equal(result.comparable_count, 0); assert.equal(result.rows.length, 0);
});
test('exact date pair prevents mixing previous-disclosure, weekly and daily comparisons', () => {
  const a = fund('KR:1'), b = fund('KR:2', {daily: {status: 'ready', from_date: '2026-10-06', to_date: dates.expected_date, changes: [change()]}});
  const d = dataset([a, b]);
  assert.equal(L.aggregate(d, {minimum: 1}).comparable_count, 1);
  assert.equal(L.aggregate(d, {minimum: 1}).rows[0].increased, 1);
  assert.equal(L.aggregate(d).rows.length, 0);
  assert.equal(L.aggregate(d, {period: 'weekly'}).history_pending, true);
  assert.equal(L.cohorts(d).length, 2);
  assert.equal(L.aggregate(d, {cohort: '2026-10-06/2026-10-08', minimum: 1}).comparable_count, 1);
});
test('stale, failed and cached-after-failure data cannot inflate current aggregation', () => {
  for (const overrides of [{status: 'failed'}, {status: 'stale'}, {status: 'cached_after_failure'}, {status: 'incomplete'}, {asof: '2026-10-07'}]) {
    const f = fund('KR:1', overrides), d = dataset([f]);
    assert.equal(L.aggregate(d, {minimum: 1}).history_pending, true);
    assert.equal(L.isCurrent(f, d), false);
  }
});
test('future published baskets do not count as latest completed trading-session observations', () => {
  const f = fund('KR:1', {asof: '2026-10-12', daily: {status: 'ready', from_date: '2026-10-08', to_date: '2026-10-12', changes: [change()]}});
  assert.equal(L.aggregate(dataset([f]), {minimum: 1}).comparable_count, 0);
});
test('representatives are the default, supplemental ETFs are explicit and repeated IDs count once', () => {
  const a = fund('KR:1'), b = fund('KR:2', {representative: false});
  assert.equal(L.aggregate(dataset([a, b]), {minimum: 1}).rows[0].increased, 1);
  assert.equal(L.aggregate(dataset([a, b]), {supplements: true}).rows[0].increased, 2);
  assert.equal(L.aggregate(dataset([a, a]), {minimum: 1}).rows[0].increased, 1);
});
test('averages include all exposed comparable ETFs, including decreases and unchanged weights', () => {
  const a = fund('KR:1'), b = fund('KR:2', {issuer: '운용사 B', group: '전기전자', daily: {status: 'ready', from_date: dates.previous_date, to_date: dates.expected_date, changes: [change({delta_weight_pp: -0.15, event: 'decreased', quantity_status: 'decreased'})]}}), c = fund('KR:3', {daily: {status: 'ready', from_date: dates.previous_date, to_date: dates.expected_date, changes: [change({delta_weight_pp: 0, event: 'unchanged', quantity_status: 'unavailable'})]}});
  const r = L.aggregate(dataset([a, b, c]), {minimum: 1}).rows[0];
  assert.equal(r.observed, 3); assert.equal(r.increased, 1); assert.equal(r.decreased, 1); assert.equal(r.net, 0);
  assert.ok(Math.abs(r.average_delta_pp - 0.05) < 1e-10);
  assert.equal(r.issuer_count, 2); assert.equal(r.group_count, 2);
  assert.equal(r.quantity_unchanged, 1); assert.equal(r.quantity_unavailable, 1); assert.equal(r.quantity_decreased, 1); assert.equal(r.quantity_increased, 0);
});
test('stock ranking uses raw average descending before breadth, including negative averages and rounded ties', () => {
  const series = [
    ['000001', [0.2, 0.2, 0.2]],
    ['000002', [0.8, 0.8]],
    ['000003', [0.1, 0.1, -1.1]],
    ['000004', [0.3011, 0.3011]],
    ['000005', [0.3012, 0.3012]],
    ['000006', [0.5, 0.5]],
    ['000007', [0.5, 0.5, 0.5]],
    ['000008', [0.5, 0.5]],
    ['000009', [0.1, 0.1, -0.5]],
  ];
  const funds = [0, 1, 2].map(index => fund('KR:' + index, {daily: {
    status: 'ready', from_date: dates.previous_date, to_date: dates.expected_date,
    changes: series.filter(([, deltas]) => index < deltas.length).map(([code, deltas]) => change({
      code, name: code, delta_weight_pp: deltas[index], event: deltas[index] > 0 ? 'increased' : 'decreased',
    })),
  }}));
  const rows = L.aggregate(dataset(funds)).rows;
  assert.deepEqual(rows.map(row => row.code), ['000002', '000007', '000006', '000008', '000005', '000004', '000001', '000009', '000003']);
  assert.equal(rows[0].increased, 2);
  assert.equal(rows.find(row => row.code === '000001').increased, 3);
  assert.equal(rows.find(row => row.code === '000004').average_delta_pp.toFixed(2), rows.find(row => row.code === '000005').average_delta_pp.toFixed(2));
});
test('stock or ETF search filters display without changing observed ETF denominators', () => {
  const a = fund('KR:1'), b = fund('KR:2', {name: '다른 ETF'}), d = dataset([a, b]);
  const r = L.aggregate(d, {query: 'KR:1'}).rows[0];
  assert.equal(r.increased, 2); assert.equal(r.observed, 2);
  assert.equal(L.aggregate(d, {query: '없는종목'}).comparable_count, 2);
  assert.equal(L.aggregate(d, {query: '없는종목'}).history_pending, false);
  assert.equal(L.matchesFund(a, '삼성전자'), true);
});
test('ETF group filter changes the observation universe explicitly', () => {
  const d = dataset([fund('KR:1'), fund('KR:2', {group: '전력'})]);
  assert.equal(L.aggregate(d, {group: '반도체', minimum: 1}).selected_count, 1);
  assert.equal(L.aggregate(d, {group: '전력', minimum: 1}).rows[0].increased, 1);
});
test('issuer filter restricts both aggregation and its denominator while market active ETFs remain eligible', () => {
  const active = fund('KR:1', {issuer: 'KoAct', kind: 'market_active', group: '코스피 액티브'});
  const sameIssuer = fund('KR:2', {issuer: 'KoAct', kind: 'market_active', group: '코스닥 액티브'});
  const otherIssuer = fund('KR:3', {issuer: 'TIME', kind: 'market_active', group: '코스피 액티브', daily: {
    status: 'ready', from_date: dates.previous_date, to_date: dates.expected_date,
    changes: [change({delta_weight_pp: -0.9, event: 'decreased'})],
  }});
  const data = dataset([active, sameIssuer, otherIssuer]);
  assert.equal(L.aggregate(data).rows[0].observed, 3);
  const result = L.aggregate(data, {issuer: 'KoAct'});
  assert.equal(result.selected_count, 2); assert.equal(result.comparable_count, 2);
  assert.equal(result.rows[0].observed, 2); assert.equal(result.rows[0].increased, 2);
  assert.equal(result.rows[0].average_delta_pp, 0.3); assert.deepEqual(result.rows[0].issuers, ['KoAct']);
  assert.equal(result.rows[0].details[0].kind, 'market_active');
  assert.deepEqual(L.selectedFunds(data, {issuer: 'KoAct', group: '코스피 액티브'}).map(f => f.id), ['KR:1']);
});
test('non-equity, null, nonfinite and repeated stock records cannot create signals', () => {
  const changes = [change(), change(), change({code: 'CASH', asset_type: 'cash'}), change({code: 'ETF', asset_type: 'fund'}), change({code: 'UNKNOWN', delta_weight_pp: null}), change({code: 'INFINITE', delta_weight_pp: Infinity})];
  const f = fund('KR:1', {daily: {status: 'ready', from_date: dates.previous_date, to_date: dates.expected_date, changes}});
  const r = L.aggregate(dataset([f]), {minimum: 1});
  assert.equal(r.rows.length, 1); assert.equal(r.rows[0].increased, 1); assert.equal(r.rows[0].observed, 1);
});
test('new and removed events retain separate breadth counts', () => {
  const a = fund('KR:1', {daily: {status: 'ready', from_date: dates.previous_date, to_date: dates.expected_date, changes: [change({event: 'new', previous_weight_pct: 0, delta_weight_pp: 10.3})]}}), b = fund('KR:2', {daily: {status: 'ready', from_date: dates.previous_date, to_date: dates.expected_date, changes: [change({event: 'removed', current_weight_pct: 0, delta_weight_pp: -10})]}});
  const r = L.aggregate(dataset([a, b]), {minimum: 1}).rows[0];
  assert.equal(r.new_count, 1); assert.equal(r.removed_count, 1); assert.equal(r.net, 0);
  assert.equal(L.eventLabel('new'), '신규 편입'); assert.equal(L.eventLabel('removed'), '편출');
});
const aum = (value, asof = dates.previous_date, overrides = {}) => ({status: 'verified', metric: 'net_assets', currency: 'KRW', value_krw: value, asof, source: '공식 순자산', source_url: 'https://example.com/aum', ...overrides});
function withAum(id, value, delta = 0.5, extra = {}) {
  return fund(id, {aum: aum(value, dates.expected_date), daily: {status: 'ready', from_date: dates.previous_date, to_date: dates.expected_date, start_aum: aum(value), changes: [change({delta_weight_pp: delta, event: delta > 0 ? 'increased' : delta < 0 ? 'decreased' : 'unchanged'})]}, ...extra});
}
test('reference amounts scale with beginning AUM: 1조 and 300억 at +0.5pp', () => {
  const result = L.aggregate(dataset([withAum('KR:1', 1e12), withAum('KR:2', 3e10)]));
  assert.equal(result.rows[0].details[0].amount_delta_krw, 5e9);
  assert.equal(result.rows[0].details[1].amount_delta_krw, 1.5e8);
  assert.equal(result.rows[0].amount_delta_krw, 5.15e9);
  assert.equal(result.rows[0].amount_covered_count, 2);
  assert.equal(result.rows[0].amount_complete, true);
});
test('reference sums include signed decreases and unchanged exposure, use start AUM and respect issuer filter', () => {
  const a = withAum('KR:1', 1e12, 0.5, {issuer: 'A', aum: aum(8e12, dates.expected_date)});
  const b = withAum('KR:2', 1e12, -0.8, {issuer: 'B'});
  const c = withAum('KR:3', 2e12, 0, {issuer: 'A'});
  const d = dataset([a, b, c]);
  const result = L.aggregate(d, {minimum: 1}).rows[0];
  assert.equal(result.amount_delta_krw, -3e9); assert.equal(result.amount_covered_count, 3); assert.equal(result.observed, 3);
  const filtered = L.aggregate(d, {minimum: 1, issuer: 'A'}).rows[0];
  assert.equal(filtered.amount_delta_krw, 5e9); assert.equal(filtered.amount_covered_count, 2); assert.equal(filtered.observed, 2);
});
test('missing and partial beginning AUM remain explicit and current AUM is never substituted', () => {
  const missing = fund('KR:1', {aum: aum(1e12, dates.expected_date)}), available = withAum('KR:2', 1e12, 0.5, {aum: null});
  let row = L.aggregate(dataset([missing, available])).rows[0];
  assert.equal(row.amount_delta_krw, 5e9); assert.equal(row.amount_covered_count, 1); assert.equal(row.observed, 2); assert.equal(row.amount_complete, false);
  row = L.aggregate(dataset([missing]), {minimum: 1}).rows[0];
  assert.equal(row.amount_delta_krw, null); assert.equal(row.details[0].amount_delta_krw, null); assert.equal(row.amount_covered_count, 0);
});
test('wrong dates, stale/invalid AUM, market capitalization, non-KRW and non-ready comparisons cannot create amounts', () => {
  for (const overrides of [{asof: dates.expected_date}, {asof: '2026-02-30'}, {status: 'stale'}, {status: 'unavailable'}, {status: 'undated', asof: null}, {metric: 'market_cap'}, {currency: 'USD'}, {value_krw: 0}, {value_krw: -1}, {value_krw: Infinity}, {value_krw: '1000000000000'}]) {
    const f = withAum('KR:1', 1e12); f.daily.start_aum = aum(1e12, dates.previous_date, overrides);
    assert.equal(L.amountDelta(change(), f.daily), null, JSON.stringify(overrides));
    assert.equal(L.aggregate(dataset([f]), {minimum: 1}).rows[0].amount_delta_krw, null);
  }
  const f = withAum('KR:1', 1e12); f.daily.status = 'waiting';
  assert.equal(L.amountDelta(change(), f.daily), null);
  assert.equal(L.amountDelta(change({delta_weight_pp: Infinity}), withAum('KR:1', 1e12).daily), null);
});
test('amount sorting uses signed sums descending with missing last and keeps average sort as default', () => {
  const build = (id, code, amount, delta) => {
    const f = withAum(id, amount, delta); f.daily.changes[0].code = code; return f;
  };
  const small = build('KR:1', '000001', 3e10, 0.9), large = build('KR:2', '000002', 1e12, 0.5), missing = fund('KR:3', {daily: {status: 'ready', from_date: dates.previous_date, to_date: dates.expected_date, changes: [change({code: '000003', delta_weight_pp: 1})]}});
  const negative = build('KR:4', '000004', 3e10, 0.5), offset = build('KR:5', '000004', 1e12, -0.2);
  const d = dataset([small, large, missing, negative, offset]);
  assert.deepEqual(L.aggregate(d, {minimum: 1}).rows.map(r => r.code), ['000003', '000001', '000002', '000004']);
  assert.deepEqual(L.aggregate(d, {minimum: 1, sort: 'amount'}).rows.map(r => r.code), ['000002', '000001', '000004', '000003']);
});
test('UI loads once, navigates all tabs, exposes related ETF details, and escapes untrusted source values', async () => {
  const fs = require('node:fs'), vm = require('node:vm');
  const malicious = '<img src=x onerror=alert(1)>', a = withAum('KR:1', 1e12, 0.5, {name: malicious, source_url: 'javascript:alert(1)', collection_mode: 'archived_official_response', kind: 'market_active', aum: aum(1e12, dates.expected_date, {source: malicious, source_url: 'javascript:alert(1)'})}), b = fund('KR:2');
  a.daily.start_aum.resolution_krw = 1e8; a.daily.start_aum.note = '운용사 공시 순자산총액·억원 단위 반올림값';
  const d = dataset([a, b]), handlers = {}, focused = [];
  d.coverage.aum_current = 1; d.coverage.aum_undated = 1;
  const root = {innerHTML: '', style: {display: ''}, addEventListener: (type, handler) => { handlers[type] = handler; }, querySelector: selector => ({focus: () => focused.push(selector), setSelectionRange: () => {}})};
  const document = {activeElement: null, visibilityState: 'visible', addEventListener() {}, getElementById: id => id === 'view-holdings' ? root : null};
  let fetchCount = 0, fetchOptions;
  const context = {window: {HoldingsLogic: L, addEventListener() {}, setInterval() {}}, document, URL, fetch: async (_, options) => { fetchCount++; fetchOptions = options; return {ok: true, json: async () => d}; }};
  vm.runInNewContext(fs.readFileSync('./holdings.js', 'utf8'), context);
  context.window.ETFHoldings.mount(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(fetchOptions.cache, 'no-store'); assert.match(root.innerHTML, /종목별 비중 확대/); assert.match(root.innerHTML, /삼성전자/);
  assert.match(root.innerHTML, /aria-sort="descending">평균 비중 변화/); assert.match(root.innerHTML, /평균 비중 변화가 높은 순/);
  assert.match(root.innerHTML, /순자산 규모 확인/); assert.match(root.innerHTML, /점검 대상일 AUM 1개 · 기준일 미제공 1개/);
  assert.match(root.innerHTML, /id="hold-sort"/); assert.match(root.innerHTML, /환산 1\/2 ETF · 부분/); assert.match(root.innerHTML, /\+50억원/);
  handlers.change({target: {id: 'hold-sort', value: 'amount'}}); assert.match(root.innerHTML, /aria-sort="descending"[^>]*>비중 변화 환산액/); assert.match(root.innerHTML, /환산액이 높은 순/);
  assert.match(root.innerHTML, /id="hold-issuer"/); assert.match(root.innerHTML, /운용사 전체/);
  const click = (selector, dataset) => handlers.click({target: {closest: s => s === selector ? {dataset} : null}});
  click('[data-hold-stock]', {holdStock: '005930'});
  assert.match(root.innerHTML, /삼성전자 · 관련 ETF/); assert.match(root.innerHTML, /&lt;img src=x onerror=alert\(1\)&gt;/); assert.doesNotMatch(root.innerHTML, /href="javascript:/); assert.doesNotMatch(root.innerHTML, /<img/); assert.match(root.innerHTML, /신규 조회 아님/);
  assert.match(root.innerHTML, /시장 액티브<\/span>/); assert.match(root.innerHTML, /순자산총액 · 기준일/); assert.match(root.innerHTML, /1조원/); assert.match(root.innerHTML, /시작 AUM 1조원 · 2026-10-07/); assert.match(root.innerHTML, /title="운용사 공시 순자산총액·억원 단위 반올림값 · 원천 금액 단위: 100,000,000원"/);
  click('[data-hold-tab]', {holdTab: 'funds'});
  assert.match(root.innerHTML, /확보된 전체 구성종목/); assert.match(root.innerHTML, /구성 기준일/); assert.match(root.innerHTML, /자료 조회일/); assert.match(root.innerHTML, /10\.30%/); assert.doesNotMatch(root.innerHTML, /1030\.00%/);
  click('[data-hold-tab]', {holdTab: 'coverage'}); assert.match(root.innerHTML, /ETF별 구성 자료 수집 현황/); assert.match(root.innerHTML, /순자산총액 · 기준일/);
  handlers.keydown({target: {closest: () => ({})}, key: 'Home', preventDefault: () => {}}); assert.match(root.innerHTML, /여러 ETF에서 관측된 비중 확대/);
  context.window.ETFHoldings.mount(); assert.equal(fetchCount, 1);
});
test('UI displays history accumulation and collection failure without invented signal zeros', async () => {
  const fs = require('node:fs'), vm = require('node:vm');
  const d = dataset([fund('KR:1', {status: 'cached_after_failure', daily: {status: 'waiting', reason: '조회 실패'}})]), handlers = {}, root = {innerHTML: '', style: {display: ''}, addEventListener: (type, handler) => { handlers[type] = handler; }, querySelector: () => ({focus: () => {}})};
  const context = {window: {HoldingsLogic: L, addEventListener() {}, setInterval() {}}, document: {activeElement: null, visibilityState: 'visible', addEventListener() {}, getElementById: () => root}, URL, fetch: async () => ({ok: true, json: async () => d})};
  vm.runInNewContext(fs.readFileSync('./holdings.js', 'utf8'), context); context.window.ETFHoldings.mount(); await new Promise(resolve => setImmediate(resolve));
  assert.match(root.innerHTML, /비교 이력 축적 중/); assert.doesNotMatch(root.innerHTML, /선택 조건에 맞는 종목이 없습니다/);
  handlers.click({target: {closest: s => s === '[data-hold-tab]' ? {dataset: {holdTab: 'coverage'}} : null}});
  assert.match(root.innerHTML, /수집 실패 · 저장 자료 참고/);
});

test('baseline ETFs display undated AUM only as a qualified size reference, without reference amounts', async () => {
  const fs = require('node:fs'), vm = require('node:vm');
  const f = fund('KR:1', {status: 'baseline_only', aum: aum(3e10, null, {status: 'undated', observed_at: '2026-10-11T13:00:00Z'}), daily: {status: 'waiting', reason: '첫 기준점'}});
  const handlers = {}, root = {innerHTML: '', style: {display: ''}, addEventListener: (type, handler) => { handlers[type] = handler; }, querySelector: () => ({focus: () => {}})};
  const context = {window: {HoldingsLogic: L, addEventListener() {}, setInterval() {}}, document: {activeElement: null, visibilityState: 'visible', addEventListener() {}, getElementById: () => root}, URL, fetch: async () => ({ok: true, json: async () => dataset([f])})};
  vm.runInNewContext(fs.readFileSync('./holdings.js', 'utf8'), context); context.window.ETFHoldings.mount(); await new Promise(resolve => setImmediate(resolve));
  handlers.click({target: {closest: selector => selector === '[data-hold-tab]' ? {dataset: {holdTab: 'funds'}} : null}});
  assert.match(root.innerHTML, /300억원/); assert.match(root.innerHTML, /기준일 미제공 · 조회 2026-10-11 22:00 KST/); assert.match(root.innerHTML, /참고 규모/);
  assert.match(root.innerHTML, /첫 기준점/); assert.doesNotMatch(root.innerHTML, /시작 AUM 300억원/);
});
