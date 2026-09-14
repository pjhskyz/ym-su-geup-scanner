const assert = require('node:assert/strict');
const test = require('node:test');
const L = require('./scanner_logic');
const row = (code, i1, f1, neti = 100) => ({ code, i1, f1, neti, netf: neti });
test('strong buying uses both actual ratios and excludes rounded boundary', () => {
  assert.equal(L.strongBoth(row('1', .3, .3)), true);
  assert.equal(L.strongBoth(row('2', .31, .29)), false);
  assert.equal(L.strongBoth({ ...row('3', .3, .3), _raw: { ratio_pct: { inst: .29999, frgn: .31 } } }), false);
  assert.equal(L.strongBoth(row('4', null, .5)), false);
});
test('top membership stays at 30 with tied values and stable code ordering', () => {
  const rows = Array.from({length: 35}, (_, i) => row(String(i + 1).padStart(6, '0'), .5, .5)).reverse();
  const selected = L.top(rows, 'inst', 'ratio');
  assert.equal(selected.length, 30);
  assert.equal(selected[0].row.code, '000001');
  assert.equal(selected[29].row.code, '000030');
});
test('comparison marks entry retention exit and waits without a valid prior day', () => {
  const before = Array.from({length:30}, (_, i) => row(String(i), i + 1, 31-i));
  const now = [...before.slice(1), row('new', 99, 99)];
  const result = L.compare(now, before, 'inst', 'ratio', true);
  assert.equal(result.current.filter(x => x.status === 'new').length, 1);
  assert.equal(result.current.filter(x => x.status === 'retained').length, 29);
  assert.equal(result.exited[0].row.code, '0');
  assert.equal(L.compare(now, before, 'inst', 'ratio', false).current.every(x => x.status === 'pending'), true);
  assert.deepEqual(L.compare(now, before, 'inst', 'ratio', false).exited, []);
});
test('separate modes and investors use raw values, not bucket ranks or current market caps', () => {
  const rows = [row('a', .8, .2, 10), row('b', .2, .9, 500)];
  assert.equal(L.top(rows, 'inst', 'ratio')[0].row.code, 'a');
  assert.equal(L.top(rows, 'frgn', 'ratio')[0].row.code, 'b');
  assert.equal(L.top(rows, 'inst', 'amount')[0].row.code, 'b');
  assert.equal(L.metric({...rows[0], _raw:{net_won:{inst:10001}}}, 'inst', 'amount'),10001);
});
test('million KRW converts to hundred-million KRW; missing and rounded zero remain clear', () => {
  assert.equal(L.amount(12345), 123.45);
  assert.equal(L.amount(null), null);
  assert.equal(L.number(-.0001), '0.00');
  assert.equal(L.number(null), '—');
});
test('legacy Intek Plus 0.30 display reconstructs below strong-buying threshold', () => {
  const intek = { code:'064290',name:'인텍플러스',neti:1892,netf:2233,mcap:6330,i1:.30,f1:.35 };
  assert.equal(L.number(L.metric(intek,'inst','ratio')), '0.30');
  assert.ok(Math.abs(L.metric(intek,'inst','ratio')-.29889415481832543)<1e-12);
  assert.equal(L.strongBoth(intek),false);
  assert.equal(L.strongBoth({...intek,_raw:{ratio_pct:{inst:.30001,frgn:.35}}}),true);
  const rival={...intek,code:'000001',neti:1891,i1:.31};
  assert.equal(L.top([rival,intek],'inst','ratio')[0].row.code,'064290');
});
test('explicit null exact rank never falls back to legacy bucket 250', () => {
  const source={it:250,id1:1,id2:2,ft:250,fd1:4,fd2:5,_raw:{inst:{today:null,previous:7,two_days_ago:8},frgn:{today:null,previous:null,two_days_ago:null}}};
  assert.deepEqual(L.rankSeries(source,'inst'),[8,7,null]);
  assert.deepEqual(L.rankSeries(source,'frgn'),[null,null,null]);
  assert.deepEqual(L.rankSeries({...source,_raw:{}},'inst'),[2,1,250]);
});
