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
test('UI loads once, navigates all tabs, exposes related ETF details, and escapes untrusted source values', async () => {
  const fs = require('node:fs'), vm = require('node:vm');
  const malicious = '<img src=x onerror=alert(1)>', a = fund('KR:1', {name: malicious, source_url: 'javascript:alert(1)', collection_mode: 'archived_official_response'}), b = fund('KR:2');
  const d = dataset([a, b]), handlers = {}, focused = [];
  const root = {innerHTML: '', addEventListener: (type, handler) => { handlers[type] = handler; }, querySelector: selector => ({focus: () => focused.push(selector), setSelectionRange: () => {}})};
  const document = {activeElement: null, getElementById: id => id === 'view-holdings' ? root : null};
  let fetchCount = 0, fetchOptions;
  const context = {window: {HoldingsLogic: L}, document, URL, fetch: async (_, options) => { fetchCount++; fetchOptions = options; return {ok: true, json: async () => d}; }};
  vm.runInNewContext(fs.readFileSync('./holdings.js', 'utf8'), context);
  context.window.ETFHoldings.mount(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(fetchOptions.cache, 'no-store'); assert.match(root.innerHTML, /종목별 비중 확대/); assert.match(root.innerHTML, /삼성전자/);
  const click = (selector, dataset) => handlers.click({target: {closest: s => s === selector ? {dataset} : null}});
  click('[data-hold-stock]', {holdStock: '005930'});
  assert.match(root.innerHTML, /삼성전자 · 관련 ETF/); assert.match(root.innerHTML, /&lt;img src=x onerror=alert\(1\)&gt;/); assert.doesNotMatch(root.innerHTML, /href="javascript:/); assert.doesNotMatch(root.innerHTML, /<img/); assert.match(root.innerHTML, /신규 조회 아님/);
  click('[data-hold-tab]', {holdTab: 'funds'});
  assert.match(root.innerHTML, /확보된 전체 구성종목/); assert.match(root.innerHTML, /구성 기준일/); assert.match(root.innerHTML, /자료 조회일/); assert.match(root.innerHTML, /10\.30%/); assert.doesNotMatch(root.innerHTML, /1030\.00%/);
  click('[data-hold-tab]', {holdTab: 'coverage'}); assert.match(root.innerHTML, /ETF별 구성 자료 수집 현황/);
  handlers.keydown({target: {closest: () => ({})}, key: 'Home', preventDefault: () => {}}); assert.match(root.innerHTML, /여러 ETF에서 관측된 비중 확대/);
  context.window.ETFHoldings.mount(); assert.equal(fetchCount, 1);
});
test('UI displays history accumulation and collection failure without invented signal zeros', async () => {
  const fs = require('node:fs'), vm = require('node:vm');
  const d = dataset([fund('KR:1', {status: 'cached_after_failure', daily: {status: 'waiting', reason: '조회 실패'}})]), handlers = {}, root = {innerHTML: '', addEventListener: (type, handler) => { handlers[type] = handler; }, querySelector: () => ({focus: () => {}})};
  const context = {window: {HoldingsLogic: L}, document: {activeElement: null, getElementById: () => root}, URL, fetch: async () => ({ok: true, json: async () => d})};
  vm.runInNewContext(fs.readFileSync('./holdings.js', 'utf8'), context); context.window.ETFHoldings.mount(); await new Promise(resolve => setImmediate(resolve));
  assert.match(root.innerHTML, /비교 이력 축적 중/); assert.doesNotMatch(root.innerHTML, /선택 조건에 맞는 종목이 없습니다/);
  handlers.click({target: {closest: s => s === '[data-hold-tab]' ? {dataset: {holdTab: 'coverage'}} : null}});
  assert.match(root.innerHTML, /수집 실패 · 저장 자료 참고/);
});
