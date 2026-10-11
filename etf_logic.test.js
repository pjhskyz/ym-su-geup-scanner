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
