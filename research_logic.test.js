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

test('수출입 지표: 전년 동월·전월·12개월 누적·단가, 결측은 계산하지 않는다', () => {
  assert.equal(L.pctChange(110, 100), 10);
  assert.equal(L.pctChange(-50, -100), 50);
  assert.equal(L.pctChange(1, 0), null);
  assert.equal(L.pctChange(null, 5), null);
  assert.equal(L.formatMonth('202610'), '2026-10');
  assert.equal(L.formatMonth('2026'), '—');
  assert.equal(L.usdLabel(12_300_000), '12.3백만$');
  assert.equal(L.usdLabel(1_234_000_000), '12.34억$');
  assert.equal(L.usdLabel(null), '—');
  const months = Array.from({length: 25}, (_, i) => String(202410 + (i < 3 ? i : 0)).slice(0, 4) + String(i).padStart(2, '0'));
  const exp = Array.from({length: 25}, (_, i) => 100 + i * 10), wgt = Array.from({length: 25}, () => 10);
  const item = {months, exp, exp_wgt: wgt, imp: Array(25).fill(null), imp_wgt: Array(25).fill(null)};
  const m = L.tradeMetrics(item, 'exp');
  assert.equal(m.index, 24);
  assert.equal(m.amount, 340);
  assert.ok(Math.abs(m.yoy - (340 / 220 - 1) * 100) < 1e-9);
  assert.ok(Math.abs(m.mom - (340 / 330 - 1) * 100) < 1e-9);
  assert.equal(m.sum12, exp.slice(13).reduce((a, b) => a + b, 0));
  assert.equal(m.sum12Yoy, L.pctChange(m.sum12, exp.slice(1, 13).reduce((a, b) => a + b, 0)));
  assert.equal(m.unit, 34);
  assert.equal(L.tradeMetrics(item, 'imp'), null, '값이 하나도 없으면 지표를 만들지 않는다');
  const gap = {...item, exp: exp.map((v, i) => (i === 20 ? null : v)), exp_wgt: wgt.map((v, i) => (i === 24 ? 0 : v))};
  const g = L.tradeMetrics(gap, 'exp');
  assert.equal(g.sum12, null, '12개월 중 빈 달이 있으면 누적을 비운다');
  assert.equal(g.unit, null, '중량이 0이면 단가를 비운다');
  assert.equal(L.tradeMetrics({months: ['202610'], exp: [5]}, 'exp').yoy, null);
  assert.deepEqual(L.yoySeries([1, ...Array(11).fill(1), 2]).slice(-2), [null, 100]);
  assert.deepEqual(L.unitSeries([10, 20, null], [2, 0, 3]), [5, null, null]);
  assert.deepEqual(L.sortBy([{v: 1}, {v: null}, {v: 3}], r => r.v, -1).map(r => r.v), [3, 1, null]);
  assert.deepEqual(L.sortBy([{v: 1}, {v: null}, {v: 3}], r => r.v, 1).map(r => r.v), [1, 3, null]);
  assert.deepEqual(L.sortBy([{v: '나'}, {v: '가'}], r => r.v, 1).map(r => r.v), ['가', '나']);
});

test('수출 잠정치: 같은 구간끼리 전년·전월 동기 비교, 없는 구간은 고르지 않는다', () => {
  const months = Array.from({length: 14}, (_, i) => (i < 2 ? `2025${String(11 + i).padStart(2, '0')}` : `2026${String(i - 1).padStart(2, '0')}`));
  assert.deepEqual([months[0], months[12], months[13]], ['202511', '202611', '202612']);
  const fill = (base, last) => months.map((_, i) => (i === 13 ? last : base + i));
  const prelim = {months, latest: {month: '202612', span: 'd10'},
    item: [{name: '전체', d10: fill(100, 150), d20: fill(200, null), m: fill(300, null)}, {name: '반도체', d10: fill(40, 90), d20: fill(80, null), m: fill(120, null)}, {name: '선박', d10: months.map(() => null), d20: months.map(() => null), m: months.map(() => null)}],
    country: [{name: '전체', d10: fill(100, 150), d20: fill(200, null), m: fill(300, null)}, {name: '중국', d10: fill(20, 30), d20: fill(50, null), m: fill(70, null)}]};
  assert.deepEqual(L.prelimPick(prelim, null), {index: 13, month: '202612', span: 'd10'});
  assert.deepEqual(L.prelimPick(prelim, 'd20'), {index: 12, month: '202611', span: 'd20'}, '1~20일 값이 아직 없는 달은 건너뛴다');
  assert.equal(L.prelimPick(prelim, 'x'), null);
  assert.equal(L.prelimPick({...prelim, latest: {month: '202612', span: 'm'}}, null), null, '표시된 최신 구간에 값이 없으면 고르지 않는다');
  assert.equal(L.prelimPick(null, null), null);
  assert.equal(L.prelimPick({months: [], item: []}, 'd10'), null);
  const rows = L.prelimRows(prelim, 'item', L.prelimPick(prelim, null));
  assert.deepEqual(rows.map(r => r.name), ['전체', '반도체', '선박']);
  assert.equal(rows[0].amount, 150);
  assert.equal(rows[0].yoy, L.pctChange(150, 101));
  assert.equal(rows[0].mom, L.pctChange(150, 112));
  assert.equal(rows[0].share, null, '전체 행은 비중을 표시하지 않는다');
  assert.equal(rows[1].share, 60);
  assert.deepEqual([rows[2].amount, rows[2].yoy, rows[2].share], [null, null, null], '값이 없는 품목은 0으로 보지 않는다');
  const d20 = L.prelimRows(prelim, 'country', L.prelimPick(prelim, 'd20'));
  assert.equal(d20[1].amount, 62);
  assert.equal(d20[1].yoy, L.pctChange(62, 50));
  assert.ok(Math.abs(d20[1].share - 62 / 212 * 100) < 1e-9);
  assert.deepEqual(L.prelimRows(prelim, 'item', null), []);
  const ex = L.prelimExcluding(prelim, L.prelimPick(prelim, null), '반도체');
  assert.deepEqual([ex.amount, ex.yoy, ex.mom], [60, L.pctChange(60, 60), L.pctChange(60, 60)]);
  assert.equal(L.prelimExcluding(prelim, L.prelimPick(prelim, null), '없는 품목'), null);
  assert.equal(L.prelimExcluding(prelim, L.prelimPick(prelim, null), '선박'), null);
  const series = L.prelimSeries(prelim, 'item', 1, 'd10');
  assert.equal(series.name, '반도체');
  assert.equal(series.yoy[12], L.pctChange(52, 40));
  assert.equal(series.yoy[11], null);
  assert.deepEqual(L.prelimSeries(prelim, 'item', 9, 'd10').amount, []);
  assert.deepEqual(['d10', 'd20', 'm', 'q'].map(L.prelimSpanLabel), ['1~10일', '1~20일', '월 전체', '—']);
});

test('국가별: 무역수지·국가 분해 비중, 그 외 국가는 전체에서 뺀다', () => {
  const months = Array.from({length: 24}, (_, i) => `${2024 + Math.floor((10 + i) / 12)}${String((10 + i) % 12 + 1).padStart(2, '0')}`);
  assert.deepEqual([months[0], months[23]], ['202411', '202610']);
  const flat = v => months.map(() => v);
  const n = L.nationMetrics({exp: months.map((_, i) => 100 + i), imp: flat(80)}, months, 'exp');
  assert.deepEqual([n.month, n.amount, n.balance], ['202610', 123, 43]);
  assert.equal(n.yoy, L.pctChange(123, 111));
  assert.equal(L.nationMetrics({exp: flat(null), imp: flat(1)}, months, 'exp'), null);
  assert.equal(L.nationMetrics({exp: flat(5), imp: flat(null)}, months, 'exp').balance, null, '한쪽이 없으면 수지를 계산하지 않는다');
  const countries = {months, total: {exp: flat(100), imp: flat(10)}, exp: [{code: 'US', name: '미국', values: months.map((_, i) => (i < 12 ? 30 : 60))}, {code: 'CN', name: '중국', values: flat(25)}], imp: []};
  const b = L.countryBreakdown(countries, 'exp');
  assert.equal(b.month, '202610');
  assert.deepEqual(b.rows.map(r => [r.code, r.m.amount, r.share]), [['US', 60, 60], ['CN', 25, 25]]);
  assert.equal(b.rows[0].m.yoy, 100);
  assert.equal(b.rows[0].m.sum12Yoy, 100);
  assert.deepEqual([b.rest.values[0], b.rest.values[23], b.rest.share], [45, 15, 15]);
  assert.equal(b.total.m.sum12, 1200);
  const imp = L.countryBreakdown(countries, 'imp');
  assert.deepEqual([imp.rows.length, imp.rest.m.amount, imp.rest.share], [0, 10, 100], '상위 국가가 없으면 전부 그 외로 본다');
  assert.equal(L.countryBreakdown({months, total: {exp: flat(null)}, exp: []}, 'exp'), null);
  assert.equal(L.countryBreakdown(null, 'exp'), null);
  const lagging = L.countryBreakdown({months, total: {exp: flat(100)}, exp: [{code: 'US', name: '미국', values: months.map((_, i) => (i === 23 ? null : 50))}]}, 'exp');
  assert.deepEqual([lagging.rows[0].share, lagging.rest.values[23]], [null, null], '기준월이 다르면 비중을 계산하지 않는다');
});

test('히트맵 시장 전환: 시장별 종목만 고르고, 시장을 모르는 종목은 전체에만 남긴다', () => {
  const rows = L.heatmapRows({columns: ['code', 'name', 'market'], rows: [['1', '가', 'KOSPI'], ['2', '나', 'KOSDAQ'], ['3', '다', 'KOSPI'], ['4', '라', null], ['5', '마', 'KONEX']]});
  assert.deepEqual(L.marketCounts(rows), {KOSPI: 2, KOSDAQ: 1, unknown: 2, available: true});
  assert.deepEqual(L.marketRows(rows, 'KOSPI').map(r => r.code), ['1', '3']);
  assert.deepEqual(L.marketRows(rows, 'KOSDAQ').map(r => r.code), ['2']);
  assert.equal(L.marketRows(rows, 'all').length, 5);
  assert.equal(L.marketRows(rows, undefined).length, 5);
  assert.equal(L.marketRows(rows, 'KONEX').length, 5, '지원하지 않는 값은 전체로 본다');
  assert.deepEqual(L.marketRows(null, 'KOSPI'), []);
  assert.equal(L.marketLabel('KOSPI'), '코스피');
  assert.equal(L.marketLabel('KOSDAQ'), '코스닥');
  assert.equal(L.marketLabel('all'), '전체');
});

test('히트맵 시장 전환: 시장 칸이 없는 예전 자료는 전환할 수 없다고 알린다', () => {
  const old = L.heatmapRows({columns: ['code', 'name', 'sector', 'mcap', 'r1', 'r5', 'r20'], rows: [['1', '가', 'IT', 100, 1, 2, 3]]});
  assert.deepEqual(L.marketCounts(old), {KOSPI: 0, KOSDAQ: 0, unknown: 1, available: false});
  assert.deepEqual(L.marketCounts([]), {KOSPI: 0, KOSDAQ: 0, unknown: 0, available: false});
  assert.deepEqual(L.marketCounts(undefined), {KOSPI: 0, KOSDAQ: 0, unknown: 0, available: false});
});
