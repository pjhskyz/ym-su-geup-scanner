const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const L = require('./research_logic.js');

test('표시 형식: 부호·방향·날짜·시총', () => {
  assert.equal(L.signed(1.234), '+1.23%');
  assert.equal(L.signed(-0.5, 1, '%p'), '-0.5%p');
  assert.equal(L.signed(null), '—');
  assert.equal(L.direction(0), 'flat');
  assert.equal(L.direction(-2), 'down');
  assert.equal(L.direction(undefined), 'flat');
  assert.equal(L.formatDate('20261120'), '2026-11-20');
  assert.equal(L.formatDate('2026-11-20'), '2026-11-20');
  assert.equal(L.formatDate(''), '—');
  assert.equal(L.shortDate('20261120'), '11.20');
  assert.equal(L.capLabel(57585), '5.8조');
  assert.equal(L.capLabel(10704500), '1070조');
  assert.equal(L.capLabel(3200), '3,200억');
});

test('시계열: 결측은 건너뛰고 0으로 채우지 않는다', () => {
  assert.deepEqual(L.lastValid([1, null, 3, null]), {index: 2, value: 3});
  assert.equal(L.lastValid([null, null]), null);
  assert.deepEqual(L.previousValid([1, null, 3], 2), {index: 0, value: 1});
  assert.deepEqual(L.movingAverage([1, 2, 3, null, 5], 2), [null, 1.5, 2.5, null, null]);
  assert.deepEqual(L.tail([1, 2, 3, 4], 2), [3, 4]);
  assert.deepEqual(L.tail([1, 2], 0), [1, 2]);
  const r = L.periodReturn([100, 110, 121], 2);
  assert.ok(Math.abs(r.pct - 21) < 1e-9);
  assert.equal(L.periodReturn([100, 110], 50).from, 0, '이력이 모자라면 확보된 첫 값과 비교');
  assert.equal(L.periodReturn([], 5), null);
});

test('트리맵: 면적 합과 비율을 보존하고 서로 겹치지 않는다', () => {
  const items = [6, 6, 4, 3, 2, 2, 1].map((weight, i) => ({id: i, weight}));
  const rects = L.treemap(items, 600, 400);
  assert.equal(rects.length, items.length);
  const area = rects.reduce((s, r) => s + r.w * r.h, 0);
  assert.ok(Math.abs(area - 600 * 400) < 1e-6);
  rects.forEach(r => {
    assert.ok(Math.abs(r.w * r.h - r.item.weight * 600 * 400 / 24) < 1e-6, '면적은 가중치에 비례');
    assert.ok(r.x >= -1e-9 && r.y >= -1e-9 && r.x + r.w <= 600 + 1e-6 && r.y + r.h <= 400 + 1e-6, '영역 안에 위치');
  });
  for (let i = 0; i < rects.length; i++) for (let j = i + 1; j < rects.length; j++) {
    const a = rects[i], b = rects[j];
    const overlapX = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x), overlapY = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
    assert.ok(!(overlapX > 1e-6 && overlapY > 1e-6), `타일 ${i}와 ${j}가 겹침`);
  }
  assert.deepEqual(L.treemap([{weight: 0}, {weight: null}], 100, 100), []);
  assert.deepEqual(L.treemap(items, 0, 100), []);
});

test('히트맵: 제곱근 가중치·색·집계', () => {
  assert.equal(L.tileWeight(10000), 100);
  assert.equal(L.tileWeight(-1), 0);
  assert.notEqual(L.heatColor(3), L.heatColor(-3));
  assert.equal(L.heatColor(9), L.heatColor(3), '±3%에서 포화');
  assert.equal(L.heatColor(null), 'rgb(42,50,62)');
  const [r] = L.heatColor(2).match(/\d+/g).map(Number), [, , b] = L.heatColor(-2).match(/\d+/g).map(Number);
  assert.ok(r > 120 && b > 120, '상승은 빨강, 하락은 파랑');
  const rows = L.heatmapRows({columns: ['code', 'r1'], rows: [['A', 1], ['B', -1], ['C', 0], ['D', null]]});
  assert.deepEqual(rows[0], {code: 'A', r1: 1});
  assert.deepEqual(L.moveCounts(rows, 'r1'), {up: 1, down: 1, flat: 1, missing: 1});
});

test('시장의 흐름 읽기: 30%·70% 경계와 자료 없음', () => {
  assert.equal(L.marketReading({up_sector_ratio: [80], up_stock_ratio: [75], oscillator: [0.4]}).label, '시장 전반 강세');
  const weak = L.marketReading({up_sector_ratio: [0], up_stock_ratio: [8], oscillator: [-2.8]});
  assert.equal(weak.label, '시장 전반 약세');
  assert.ok(weak.notes.some(n => n.includes('완화')));
  assert.equal(L.marketReading({up_sector_ratio: [50], up_stock_ratio: [50], oscillator: [null]}).label, '업종별 차별화');
  assert.equal(L.marketReading({up_sector_ratio: [null]}).label, '판단 보류');
  assert.ok(L.marketReading({up_sector_ratio: [90], up_stock_ratio: [40], oscillator: []}).notes.some(n => n.includes('대형주')));
});

test('ETF 주도·반등 후보: 그룹당 1개, 미갱신·보조·스타일 제외', () => {
  const base = {market: 'KR', status: 'fresh', date: '2026-11-20', kind: 'sector', representative: true, rank_enabled: true, above_ma20: true};
  const data = {expected_dates: {KR: '2026-11-20'}, rows: [
    {...base, group: '반도체', name: 'A', rs_price_21: 0.03, rs_price_63: 0.06, rs_price_126: 0.09},
    {...base, group: '반도체', name: 'A2', rs_price_21: 0.01, rs_price_63: 0.02, rs_price_126: 0.03},
    {...base, group: '조선', name: 'B', rs_price_21: 0.05, rs_price_63: -0.04, rs_price_126: -0.10},
    {...base, group: '건설', name: 'C', rs_price_21: -0.02, rs_price_63: -0.01, rs_price_126: -0.20},
    {...base, group: '은행', name: 'D', rs_price_21: 0.2, rs_price_63: 0.2, rs_price_126: 0.2, date: '2026-11-19'},
    {...base, group: '배당', name: 'E', kind: 'style', rs_price_21: 0.3, rs_price_63: 0.3, rs_price_126: 0.3},
    {...base, group: '로봇', name: 'F', representative: 'supplemental', rs_price_21: 0.3, rs_price_63: 0.3, rs_price_126: 0.3},
    {...base, group: '게임', name: 'G', rs_price_21: 0.1, rs_price_63: null, rs_price_126: 0.1},
  ]};
  const leaders = L.etfLeaders(data, 'KR');
  assert.deepEqual(leaders.map(x => x.row.name), ['A', 'B', 'C']);
  assert.ok(Math.abs(leaders[0].score - 0.06) < 1e-12);
  const rebounds = L.etfRebounds(data, 'KR');
  assert.deepEqual(rebounds.map(x => x.row.name), ['B'], '1개월 상대강도가 3개월보다 개선된 약세 섹터만');
  assert.ok(Math.abs(rebounds[0].score - 0.09) < 1e-12, '점수는 1개월 − 3개월 개선폭');
  const belowMa = {...data, rows: data.rows.map(r => ({...r, above_ma20: false}))};
  assert.deepEqual(L.etfRebounds(belowMa, 'KR').map(x => x.row.name), ['B'], '20일선 아래여도 후보에서 빼지 않고 화면에 상태만 표시');
  assert.deepEqual(L.etfLeaders(data, 'US'), []);
});

test('Yahoo 일봉 응답: 당일 봉 유무에 따라 전일 종가를 고른다', () => {
  const day = 86400, t0 = 20000 * day;
  const open = {meta: {regularMarketPrice: 105, regularMarketTime: t0 + 2 * day + 3600, gmtoffset: 0},
    timestamp: [t0, t0 + day, t0 + 2 * day], indicators: {quote: [{close: [100, 102, 104]}]}};
  const q = L.quoteFromYahoo(open);
  assert.equal(q.previous, 102, '당일 봉이 있으면 그 전 봉이 전일 종가');
  assert.deepEqual(q.closes, [100, 102, 105], '마지막 점은 현재 시세');
  assert.ok(Math.abs(q.changePct - (105 / 102 - 1) * 100) < 1e-9);
  const closed = {meta: {regularMarketPrice: 102, regularMarketTime: t0 + day + 3600, gmtoffset: 0},
    timestamp: [t0, t0 + day], indicators: {quote: [{close: [100, null]}]}};
  assert.equal(L.quoteFromYahoo(closed).previous, 100);
  const tnx = L.quoteFromYahoo({meta: {regularMarketPrice: 45, regularMarketTime: t0 + day, gmtoffset: 0}, timestamp: [t0], indicators: {quote: [{close: [44]}]}}, {divisor: 10});
  assert.equal(tnx.price, 4.5);
  assert.equal(tnx.previous, 4.4);
  assert.equal(L.quoteFromYahoo({meta: {}}), null);
  assert.equal(L.quoteFromYahoo(null), null);
});

test('발행된 research_data.json과 함께 동작한다', () => {
  const data = JSON.parse(fs.readFileSync('research_data.json', 'utf8'));
  const rows = L.heatmapRows(data.heatmap);
  assert.ok(rows.length > 500);
  const big = rows.filter(r => r.mcap >= 3000);
  const rects = L.treemap(big.map(r => ({...r, weight: L.tileWeight(r.mcap)})), 1200, 700);
  assert.equal(rects.length, big.length);
  assert.ok(rects.every(r => r.w > 0 && r.h > 0 && Number.isFinite(r.x + r.y)));
  assert.equal(data.breadth.dates.length, data.breadth.up_sector_ratio.length);
  assert.ok(['시장 전반 강세', '시장 전반 약세', '업종별 차별화'].includes(L.marketReading(data.breadth).label));
  data.size.buckets.forEach(b => assert.equal(b.levels.length, data.size.dates.length));
});
