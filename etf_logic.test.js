const assert = require('node:assert/strict');
const test = require('node:test');
const L = require('./etf_logic.js');
const expected = {KR: '2026-10-08', US: '2026-10-09'};
function row(market, symbol, overrides = {}) {
  return {id: market + ':' + symbol, market, symbol, scope: market, name: symbol, status: 'fresh', date: expected[market], representative: true, high: {state: 'far_from_high', gap: -0.1, weak_window_complete: true}, rs_price_126: 0.1, ...overrides};
}
const dataset = rows => ({expected_dates: expected, rows});
test('failed, stale, quarantined observations never count as current signals', () => {
  for (const bad of [{status: 'failed'}, {status: 'stale'}, {status: 'quarantined'}, {date: '2026-10-07'}, {quarantined: true}]) {
    const r = row('KR', '091160', {...bad, high: {state: 'new_close_high_candidate', gap: 0}}), d = dataset([r]);
    assert.equal(L.isCurrent(r, d), false); assert.equal(L.counts(d).KR, 0); assert.equal(L.status(r, d).tone, 'muted');
  }
});
test('supplemental ETFs do not increase representative signal counts', () => {
  const d = dataset([row('US', 'SOXX', {high: {state: 'new_close_high_candidate', gap: 0}}), row('US', 'XSD', {representative: false, high: {state: 'new_close_high_candidate', gap: 0}})]);
  assert.equal(L.counts(d).US, 1); assert.equal(L.counts(d).US_high_usable, 1);
});
test('lack of 253 closes does not supply a usable high or five-day weakness denominator', () => {
  const d = dataset([row('KR', 'new', {high: {state: 'insufficient_253_closes', gap: null, weak_window_complete: false}})]);
  assert.equal(L.counts(d).KR_high_usable, 0); assert.equal(L.counts(d).weak_usable, 0);
});
test('null metrics sort last in either direction; zero remains numeric', () => {
  const rows = [{name: 'a', v: null}, {name: 'b', v: 0}, {name: 'c', v: 3}];
  assert.deepEqual(L.sortRows(rows, 'v', 'asc').map(r => r.v), [0, 3, null]);
  assert.deepEqual(L.sortRows(rows, 'v', 'desc').map(r => r.v), [3, 0, null]);
});
test('MA entries and exits are condition changes, never invented Top10 membership', () => {
  assert.equal(L.deltaLabel('entry'), '조건진입'); assert.equal(L.deltaLabel('exit'), '조건이탈');
  assert.equal(L.deltaLabel(null), '—'); assert.equal(L.deltaLabel(0), '0');
});
test('missing other-country benchmark cannot be called only one-country strength', () => {
  const d = dataset([row('US', 'SOXX'), row('KR', '091160', {rs_price_126: null})]);
  const pair = L.crossCountry(d).find(p => p.name === '반도체·AI 하드웨어');
  assert.equal(pair.label, '미국 강세·한국 비교 제한');
});
test('verified comparison supports US-only only when the opposite observation is complete', () => {
  const d = dataset([row('US', 'SOXX'), row('KR', '091160', {rs_price_126: -0.1})]);
  assert.equal(L.crossCountry(d).find(p => p.name === '반도체·AI 하드웨어').label, '미국에서만 관측');
});
test('global robotics is never a US domestic bilateral confirmation', () => {
  const d = dataset([row('US', 'ROBO', {scope: 'GLOBAL'}), row('KR', '445290')]);
  const pair = L.crossCountry(d).find(p => p.name === '로봇·자동화');
  assert.equal(pair.scope, 'GLOBAL'); assert.equal(pair.label, '글로벌·한국 동시 확인');
});
test('clean energy does not contaminate petroleum energy comparison', () => {
  const d = dataset([row('US', 'ICLN', {name: 'Global Clean Energy ETF', scope: 'GLOBAL'}), row('KR', '381570', {name: 'HANARO Fn친환경에너지'})]);
  const pair = L.crossCountry(d).find(p => p.name === '에너지·화학');
  assert.equal(pair.sides.US.registered.length, 0); assert.equal(pair.sides.KR.registered.length, 0);
});
test('two unverified high candidates do not become confirmed high signals', () => {
  const common = {rs_price_126: null, high: {state: 'new_close_high_candidate', gap: 0}};
  const d = dataset([row('US', 'SOXX', common), row('KR', '091160', common)]);
  assert.equal(L.crossCountry(d).find(p => p.name === '반도체·AI 하드웨어').label, '양국 강세 후보');
});
test('expanded catalogue retains excluded and failed observations without inflating sector signals', () => {
  const rep = row('US', 'SOXX', {rank_enabled: true, selection_status: 'ranking', kind: 'industry', high: {state: 'new_close_high_candidate', gap: 0}});
  const excluded = row('US', 'EFA', {rank_enabled: false, selection_status: 'excluded', exclusion_reason: '국가·지역형 ETF', high: {state: 'new_close_high_candidate', gap: 0}});
  const pending = row('US', 'OTHER', {rank_enabled: false, selection_status: 'unclassified', status: 'failed', classification_status: 'pending'});
  const d = {...dataset([rep, excluded, pending]), all_rows: [rep, excluded, pending]};
  assert.equal(L.counts(d).US, 1);
  assert.deepEqual(L.filterCatalogue(d, {selection: 'unclassified', freshness: 'unavailable'}).map(r => r.symbol), ['OTHER']);
  assert.deepEqual(L.filterCatalogue(d, {query: '국가·지역'}).map(r => r.symbol), ['EFA']);
  assert.equal(L.catalogue(d).length, 3);
});
test('listing highs and style funds stay outside 52-week sector signal counts', () => {
  const d = dataset([
    row('KR', 'LISTING', {kind: 'theme', high: {state: 'new_listing_high_candidate', basis: 'since_listing', gap: 0, weak_window_complete: false}}),
    row('KR', 'STYLE', {kind: 'style', high: {state: 'new_close_high_candidate', gap: 0, weak_window_complete: true}})
  ]);
  assert.equal(L.counts(d).KR, 0); assert.equal(L.counts(d).KR_high_usable, 0);
  assert.equal(L.counts(d).weak_usable, 0); assert.equal(L.counts(d).listing, 1);
  assert.equal(L.status(d.rows[0], d).label, '상장 이후 신고가 후보');
});
test('country and classification filters distinguish listing venue from global exposure', () => {
  const d = {expected_dates: expected, all_rows: [row('US', 'GRID', {kind: 'theme', scope: 'GLOBAL'}), row('US', 'XLK', {kind: 'sector'}), row('KR', '091160', {kind: 'industry'})]};
  assert.deepEqual(L.filterCatalogue(d, {market: 'US', scope: '미국 상장 글로벌 업종·테마'}).map(r => r.symbol), ['GRID']);
  assert.deepEqual(L.filterCatalogue(d, {market: 'KR'}).map(r => r.symbol), ['091160']);
  assert.equal(L.scopeLabel(row('US', 'IXG', {scope: 'GLOBAL', kind: 'sector'})), '미국 상장 글로벌 업종·테마');
  assert.equal(L.scopeLabel(row('US', 'ECON', {scope: 'COUNTRY', kind: 'country', rank_enabled: false, selection_status: 'excluded'})), '미국 상장 국가·지역');
});
test('expanded cross-country pairs preserve global classification and exact sector mapping', () => {
  const d = dataset([row('US', 'IGV', {scope: 'GLOBAL'}), row('KR', '157490'), row('US', 'SLX', {scope: 'GLOBAL'}), row('KR', '117680'), row('US', 'FDN'), row('KR', '365000')]);
  const pairs = L.crossCountry(d);
  assert.equal(pairs.find(p => p.name === '소프트웨어').label, '글로벌·한국 동시 확인');
  assert.equal(pairs.find(p => p.name === '철강').scope, 'GLOBAL');
  assert.equal(pairs.find(p => p.name === '인터넷').label, '양국 동시 확인');
  assert.equal(pairs.find(p => p.name === '에너지·화학').sides.KR.registered.length, 0);
});
test('every bilateral mapping resolves to a current configured representative and exposure scope', () => {
  const u = require('./etf_pipeline/universe.json');
  const pairs = L.crossCountry({expected_dates: expected, rows: u.assets});
  for (const p of pairs) {
    assert.ok(p.sides.KR.registered.length > 0, p.name + ': Korean representative missing');
    assert.ok(p.sides.US.registered.length > 0, p.name + ': US-listed representative missing');
    assert.ok(p.sides.US.registered.every(r => r.scope === p.scope), p.name + ': country exposure mismatch');
  }
});
test('expanded UI navigates all five tabs and exposes unavailable catalogue rows safely', async () => {
  const fs = require('node:fs'), vm = require('node:vm');
  const listeners = {};
  const root = {innerHTML: '', style: {display: ''}, addEventListener: (type, fn) => {listeners[type] = fn;}, querySelector: () => ({focus: () => {}})};
  const rep = row('KR', '091160', {name: '한국 대표 테스트', kind: 'industry', rank_enabled: true, classification_sources: [{url: 'https://example.com/issuer-proof', evidence: '공식 업종 분류 확인'}], above_ma20: true, sortino_price_126: 3, ranks: {sortino20: {rank: 2, delta_1: 0}}});
  const supplemental = row('KR', 'DUP', {name: '보조 테스트', kind: 'industry', rank_enabled: true, representative: false, above_ma20: true, sortino_price_126: 4, ranks: {sortino20: {rank: 1}}});
  const unknown = row('US', 'PENDING', {name: '<img src=x onerror=alert(1)>', rank_enabled: false, selection_status: 'unclassified', status: 'failed', reason: 'network failure', source_url: 'javascript:alert(1)', classification_status: 'pending'});
  const d = {...dataset([rep, supplemental]), all_rows: [rep, supplemental, unknown], catalog_summary: {KR: {listed_count: 1172, candidate_count: 236, observed_at: '2026-10-11T11:21:00Z', source_asof: null, status: 'dated_observation'}}, markets: {KR: {registered: 2, fresh: 2, rank_enabled: 2, classified: 2, representatives: 1, usable: 2}, US: {registered: 1, fresh: 0, rank_enabled: 0, classified: 0, representatives: 0, usable: 0}}, universe: {version: '2026-10-11-v3', changed_at: '2026-10-11'}};
  const window = {ETFLogic: L, addEventListener() {}, setInterval() {}}, context = {window, document: {visibilityState: 'visible', addEventListener() {}, getElementById: () => root, activeElement: null}, fetch: async () => ({ok: true, json: async () => d}), URL};
  vm.runInNewContext(fs.readFileSync(require.resolve('./etf.js'), 'utf8'), context); window.ETFScanner.mount();
  await new Promise(resolve => setImmediate(resolve));
  assert.match(root.innerHTML, /대표 ETF 중심 표시/); assert.match(root.innerHTML, /2026-10-11-v3/);
  assert.match(root.innerHTML, /class="etf-rank">2<\/td>/); assert.doesNotMatch(root.innerHTML, /보조 테스트/);
  const clickTab = name => listeners.click({target: {closest: selector => selector === '[data-etf-tab]' ? {dataset: {etfTab: name}} : null}});
  for (const tab of ['us-high', 'kr-high', 'cross', 'catalogue']) {clickTab(tab); assert.match(root.innerHTML, new RegExp('aria-labelledby="etf-tab-' + tab + '"'));}
  assert.match(root.innerHTML, /등록 3개/); assert.match(root.innerHTML, /수집 실패/); assert.match(root.innerHTML, /보조 테스트/);
  assert.match(root.innerHTML, /href="https:\/\/example.com\/issuer-proof"/); assert.match(root.innerHTML, /공식 업종 분류 확인/);
  assert.match(root.innerHTML, /전체 상장 목록 <strong>1,172<\/strong>/); assert.match(root.innerHTML, /추가 검토 후보 <strong>236<\/strong>/); assert.match(root.innerHTML, /가격 수집 등록·분석 완료 숫자에 포함하지 않습니다/);
  assert.match(root.innerHTML, /&lt;img src=x onerror=alert\(1\)&gt;/); assert.doesNotMatch(root.innerHTML, /href="javascript:|<img src=x/);
  listeners.change({target: {id: 'etf-catalogue-market', value: 'US'}});
  assert.match(root.innerHTML, /1 \/ 등록 3개/); assert.doesNotMatch(root.innerHTML, /한국 대표 테스트/);
  listeners.change({target: {id: 'etf-catalogue-freshness', value: 'fresh'}});
  assert.match(root.innerHTML, /해당 조건의 ETF가 없습니다/);
});
