const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const ETFLogic = require('./etf_logic.js');
const HoldingsLogic = require('./holdings_logic.js');

const interval = 5 * 60 * 1000;
const configurations = [
  {file: 'etf.js', api: 'ETFScanner', rootId: 'view-etf', prefix: 'etf', data: day => ({
    rows: [], expected_dates: {KR: day, US: day}, markets: {KR: {}, US: {}},
    generated_at: day + 'T00:00:00Z', last_collection_at: day + 'T00:00:00Z', collection_mode: 'live_collection',
    original: {date: '2026-10-08', observed_at_kst: '2026-10-09 08:40', rows: [
      {id: 'KR:091160', name: 'KODEX 반도체', representative: true, sortino20_rank: 1, rs_6m: 0.2},
    ]},
  })},
  {file: 'holdings.js', api: 'ETFHoldings', rootId: 'view-holdings', prefix: 'hold', data: day => ({
    schema_version: 'etf-holdings-v1', funds: [], expected_date: day, previous_date: '2026-10-12', weekly_date: '2026-10-02',
    generated_at: day + 'T00:00:00Z', coverage: {registered: 0, current: 0},
  })},
];

function harness(config) {
  let now = Date.parse('2026-10-14T00:00:00Z'), payload = config.data('2026-10-13'), failure = null;
  const rootEvents = {}, windowEvents = {}, documentEvents = {}, timers = [], requests = [];
  const node = {focus() {}, setSelectionRange() {}};
  const root = {innerHTML: '', style: {display: ''}, hidden: false,
    addEventListener: (type, callback) => {rootEvents[type] = callback;}, querySelector: () => node};
  const document = {visibilityState: 'visible', activeElement: null,
    getElementById: id => id === config.rootId ? root : null,
    addEventListener: (type, callback) => {documentEvents[type] = callback;}};
  const window = {ETFLogic, HoldingsLogic,
    addEventListener: (type, callback) => {windowEvents[type] = callback;},
    setInterval: (callback, duration) => {timers.push({callback, duration}); return timers.length;}};
  class Clock extends Date {constructor(...args) {super(...(args.length ? args : [now]));} static now() {return now;}}
  vm.runInNewContext(fs.readFileSync(__dirname + '/' + config.file, 'utf8'), {
    window, document, Date: Clock, URL,
    fetch: async (url, options) => {
      requests.push({url, options});
      if (failure) throw failure;
      return {ok: true, json: async () => structuredClone(payload)};
    },
  }, {filename: config.file});
  return {root, document, requests, timers, api: window[config.api],
    advance: duration => {now += duration;},
    respond: value => {payload = value; failure = null;}, fail: () => {failure = new Error('network unavailable');},
    focus: () => windowEvents.focus(), visibility: () => documentEvents.visibilitychange(), poll: () => timers[0].callback(),
    change: (id, values) => rootEvents.change({target: {id, ...values}}),
    input: value => rootEvents.input({target: {id: config.prefix + '-search', value}}),
    click: (attribute, dataset = {}) => rootEvents.click({target: {closest: selector => selector === '[' + attribute + ']' ? {dataset} : null}}),
  };
}

for (const config of configurations) {
  test(config.file + ': re-entry fetches new publication after five minutes without duplicate rapid requests', async () => {
    const h = harness(config);
    await h.api.mount();
    assert.equal(h.requests.length, 1);
    assert.equal(h.requests[0].options.cache, 'no-store');
    assert.match(h.root.innerHTML, /2026-10-13/);
    await h.api.mount(); await h.focus(); await h.visibility();
    assert.equal(h.requests.length, 1);
    assert.equal(h.timers.length, 1);
    assert.equal(h.timers[0].duration, interval);
    h.advance(interval); h.respond(config.data('2026-10-14'));
    await h.api.mount();
    assert.equal(h.requests.length, 2);
    assert.match(h.root.innerHTML, /2026-10-14/);
    await h.focus();
    assert.equal(h.requests.length, 2);
  });

  test(config.file + ': hidden document or unselected pane does not poll; returning refreshes once', async () => {
    const h = harness(config);
    await h.api.mount(); h.advance(interval);
    h.document.visibilityState = 'hidden';
    await h.poll(); await h.focus();
    assert.equal(h.requests.length, 1);
    h.document.visibilityState = 'visible'; h.root.style.display = 'none';
    await h.poll(); await h.visibility();
    assert.equal(h.requests.length, 1);
    h.root.style.display = ''; await h.visibility();
    assert.equal(h.requests.length, 2);
    h.advance(interval); await h.poll();
    assert.equal(h.requests.length, 3);
  });

  test(config.file + ': failed quiet refresh keeps last good data and filters, and manual refresh recovers', async () => {
    const h = harness(config);
    await h.api.mount();
    h.input('반도체');
    h.change(config.prefix + '-supplements', {checked: true});
    if (config.prefix === 'etf') h.change('etf-ma', {value: '10'});
    else h.change('hold-sort', {value: 'amount'});
    h.advance(interval); h.fail(); await h.focus();
    assert.match(h.root.innerHTML, /최신 자료 재확인 실패 · 마지막 표시 자료 유지/);
    assert.match(h.root.innerHTML, /2026-10-13/);
    assert.match(h.root.innerHTML, /value="반도체"/);
    assert.match(h.root.innerHTML, new RegExp('id="' + config.prefix + '-supplements" checked'));
    assert.match(h.root.innerHTML, config.prefix === 'etf' ? /value="10" selected/ : /value="amount" selected/);
    await h.focus(); await h.api.mount();
    assert.equal(h.requests.length, 2, 'failed background requests are also throttled');
    h.respond(config.data('2026-10-14'));
    await h.api.reload();
    assert.equal(h.requests.length, 3, 'manual retry bypasses elapsed-time throttle');
    assert.doesNotMatch(h.root.innerHTML, /최신 자료 재확인 실패/);
    assert.match(h.root.innerHTML, /value="반도체"/);
    assert.match(h.root.innerHTML, /2026-10-14/);
  });
}

test('original reference explicitly remains a dated saved observation after a page refresh', async () => {
  const h = harness(configurations[0]);
  await h.api.mount();
  h.click('data-etf-basis', {etfBasis: 'original'});
  assert.match(h.root.innerHTML, /원본 저장 참고 · 기준일 2026-10-08 · 자동 갱신 안 함/);
  assert.match(h.root.innerHTML, /저장 관측 시각 2026-10-09 08:40 KST/);
  assert.match(h.root.innerHTML, /원본 대시보드를 수집하지 않습니다/);
  h.advance(interval); await h.poll();
  assert.match(h.root.innerHTML, /data-etf-basis="original" aria-pressed="true"/);
  assert.match(h.root.innerHTML, /기준일 2026-10-08 · 자동 갱신 안 함/);
});
