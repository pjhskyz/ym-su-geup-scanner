/* 투자 리서치 화면: 시장 종합 현황 · 시장 히트맵 · 시장 수급 변화 · 컨센서스 · 수출입
   데이터: research_data.json(파생) · market_data.json · disparity_data.json · etf_data.json · /yf 시세 프록시 */
(function () {
  'use strict';
  const L = window.ResearchLogic;
  const TITLES = {overview: '시장 종합 현황', heatmap: '시장 히트맵', breadth: '시장 수급 변화', consensus: '컨센서스 누적 분석', trade: '수출입 분석'};
  const FILES = {research: 'research_data.json', market: 'market_data.json', disparity: 'disparity_data.json', etf: 'etf_data.json', trade: 'trade_data.json', consensus: 'consensus_data.json'};
  const STALE_MS = 5 * 60 * 1000, QUOTE_MS = 15 * 60 * 1000;
  const COLORS = {kospi: '#ffb020', kosdaq: '#1fd1a3', up: '#ff4d4d', down: '#3d8bff', dim: '#8c9bad', violet: '#b08cff'};
  const QUOTES = [
    {id: 'ks11', sym: '^KS11', grp: 'kr', name: '코스피', d: 2},
    {id: 'kq11', sym: '^KQ11', grp: 'kr', name: '코스닥', d: 2},
    {id: 'gspc', sym: '^GSPC', grp: '해외 지수', name: 'S&P 500', d: 2, unit: 'pt'},
    {id: 'ixic', sym: '^IXIC', grp: '해외 지수', name: '나스닥 종합', d: 2, unit: 'pt'},
    {id: 'n225', sym: '^N225', grp: '해외 지수', name: '닛케이 225', d: 2, unit: 'pt'},
    {id: 'hsi', sym: '^HSI', grp: '해외 지수', name: '항셍', d: 2, unit: 'pt'},
    {id: 'ssec', sym: '000001.SS', grp: '해외 지수', name: '상해종합', d: 2, unit: 'pt'},
    {id: 'tnx', sym: '^TNX', grp: '국채 금리', name: '미국채 10년', d: 3, unit: '%', bp: true, tnx: true},
    {id: 'wti', sym: 'CL=F', grp: '에너지', name: 'WTI유 선물', d: 2, unit: 'USD/배럴'},
    {id: 'brent', sym: 'BZ=F', grp: '에너지', name: '브렌트유 선물', d: 2, unit: 'USD/배럴'},
    {id: 'ng', sym: 'NG=F', grp: '에너지', name: '천연가스 선물', d: 3, unit: 'USD/MMBtu'},
    {id: 'usdkrw', sym: 'KRW=X', grp: '환율', name: '달러/원', d: 2, unit: '원 / 1달러'},
    {id: 'jpykrw', sym: 'JPYKRW=X', grp: '환율', name: '엔/원', d: 2, unit: '원 / 100엔', multiplier: 100},
    {id: 'eurkrw', sym: 'EURKRW=X', grp: '환율', name: '유로/원', d: 2, unit: '원 / 1유로'},
    {id: 'btc', sym: 'BTC-USD', grp: '디지털 자산', name: '비트코인/달러', d: 2, unit: 'USD'},
  ];
  const state = {
    sizeDays: 63, flowDays: 60, tileKey: 'r1', dispDays: 120, fundDays: 63,
    heat: {minCap: 3000, key: 'r1', grouped: false, query: ''},
    sectorKey: 'r1', trendDays: 60, etfSort: {key: 'rs_price_21', dir: -1},
    trade: {flow: 'exp', sort: 'yoy', dir: -1, query: '', selected: null, view: 'item',
      nation: {sort: 'amount', dir: -1, query: '', selected: null}, prelim: {span: 'auto', kind: 'item', index: 0}},
    cons: {filter: 'all', sort: 'week_pct', dir: -1, query: '', selected: null},
  };
  let root = null, view = 'overview', charts = [], quoteTimer = null, resizeTimer = null, renderToken = 0;
  const cache = {}, quotes = {};

  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const num = (v, d = 2) => L.isNumber(v) ? v.toLocaleString('ko-KR', {minimumFractionDigits: d, maximumFractionDigits: d}) : '—';
  const pct = (v, d = 2, suffix = '%') => `<span class="rs-${L.direction(v)}">${L.signed(v, d, suffix)}</span>`;
  const seg = (name, value, options) => `<div class="seg" data-rs-seg="${name}">${options.map(([v, label]) => `<button data-v="${v}" class="${String(v) === String(value) ? 'on' : ''}">${label}</button>`).join('')}</div>`;
  const panel = (title, sub, body, tools = '', cls = '') => `<section class="rs-panel ${cls}"><div class="rs-panel-head"><div><h2>${title}</h2>${sub ? `<p>${sub}</p>` : ''}</div>${tools ? `<div class="rs-tools">${tools}</div>` : ''}</div>${body}</section>`;
  const missing = text => `<div class="rs-empty">${esc(text)}</div>`;

  // ---- 데이터 ----
  async function load(key, force) {
    const hit = cache[key];
    if (hit && !force && Date.now() - hit.at < STALE_MS) return hit.data;
    try {
      const response = await fetch(FILES[key], {cache: 'no-store'});
      if (!response.ok) throw new Error('HTTP ' + response.status);
      cache[key] = {data: await response.json(), at: Date.now(), error: null};
    } catch (error) {
      cache[key] = {data: hit ? hit.data : null, at: Date.now(), error: error.message};
    }
    return cache[key].data;
  }
  async function fetchQuote(q) {
    try {
      const response = await fetch(`/yf/v8/finance/chart/${encodeURIComponent(q.sym)}?range=1mo&interval=1d&includePrePost=false`, {cache: 'no-store'});
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const json = await response.json();
      const result = json && json.chart && json.chart.result && json.chart.result[0];
      // ^TNX가 수익률×10으로 오는 경우 정규화(10년물 금리가 20%를 넘는 경우는 다루지 않음)
      const divisor = q.tnx && result && result.meta && result.meta.regularMarketPrice > 20 ? 10 : 1;
      const quote = L.quoteFromYahoo(result, {divisor, multiplier: q.multiplier || 1});
      quotes[q.id] = quote ? {...quote, at: Date.now()} : {error: '응답 형식'};
    } catch (error) { quotes[q.id] = {error: error.message}; }
    paintQuote(q);
  }
  function loadQuotes(force) {
    QUOTES.forEach(q => { const hit = quotes[q.id]; if (force || !hit || hit.error || Date.now() - hit.at > QUOTE_MS) fetchQuote(q); else paintQuote(q); });
  }

  // ---- 그리기 도우미 ----
  function spark(values, dir) {
    const v = (values || []).filter(L.isNumber);
    if (v.length < 2) return '';
    const min = Math.min(...v), max = Math.max(...v), span = max - min || 1;
    const points = v.map((y, i) => `${(i / (v.length - 1) * 100).toFixed(2)},${(26 - (y - min) / span * 24).toFixed(2)}`).join(' ');
    return `<svg class="rs-spark rs-${dir}" viewBox="0 0 100 28" preserveAspectRatio="none" aria-hidden="true"><polyline points="${points}"/></svg>`;
  }
  function destroyCharts() { charts.forEach(c => { try { c.destroy(); } catch (e) { /* 이미 해제됨 */ } }); charts = []; }
  function lineChart(id, labels, datasets, o = {}) {
    const el = document.getElementById(id);
    if (!el || !window.Chart) return;
    const axis = {ticks: {color: COLORS.dim, font: {size: 10}, maxTicksLimit: 7, maxRotation: 0}, grid: {color: 'rgba(255,255,255,.05)'}, border: {display: false}};
    const sets = datasets.map(d => ({borderWidth: 1.6, pointRadius: 0, pointHoverRadius: 3, tension: .15, ...d}));
    if (L.isNumber(o.baseline)) sets.push({label: o.baselineLabel || '기준선', data: labels.map(() => o.baseline), borderColor: 'rgba(255,255,255,.28)', borderWidth: 1, borderDash: [4, 4], pointRadius: 0, pointHoverRadius: 0});
    charts.push(new Chart(el, {type: 'line', data: {labels, datasets: sets}, options: {
      responsive: true, maintainAspectRatio: false, animation: false, interaction: {mode: 'index', intersect: false},
      plugins: {legend: {display: datasets.length > 1, labels: {color: '#aebaca', boxWidth: 10, font: {size: 10}, filter: item => item.text !== (o.baselineLabel || '기준선')}},
        tooltip: {callbacks: {label: c => ` ${c.dataset.label}: ${c.parsed.y == null ? '—' : c.parsed.y.toFixed(o.decimals ?? 1) + (o.suffix || '')}`}}},
      scales: {x: axis, y: {...axis, ticks: {...axis.ticks, callback: v => v + (o.tick || '')}}},
    }}));
  }
  const labelsOf = dates => dates.map(L.shortDate);
  function statusLine(parts) { return `<div class="rs-basis">${parts.filter(Boolean).map(p => `<span>${p}</span>`).join('')}</div>`; }
  function fileNote(key, label) { const hit = cache[key]; return hit && hit.error ? `<span class="rs-warn">${label} 조회 실패(${esc(hit.error)})${hit.data ? ' · 직전 조회본 표시' : ''}</span>` : ''; }
  function header(extra = '') {
    return `<div class="controls"><div class="title-block"><h1>${TITLES[view]}</h1></div>${extra}<span class="spacer"></span><button class="btn" data-rs="reload">새로 확인</button></div>`;
  }

  // ---- 시세 카드 ----
  function quoteCard(q, cls = '') {
    return `<div class="rs-card rs-quote ${cls}" id="rsq-${q.id}"><div class="rs-k">${esc(q.name)}${q.unit ? `<small>${esc(q.unit)}</small>` : ''}</div><div class="rs-v">—</div><div class="rs-c rs-flat">불러오는 중…</div><div class="rs-sparkbox"></div><div class="rs-t"></div></div>`;
  }
  function paintQuote(q) {
    const el = document.getElementById('rsq-' + q.id), data = quotes[q.id];
    if (!el || !data) return;
    const value = el.querySelector('.rs-v'), change = el.querySelector('.rs-c'), box = el.querySelector('.rs-sparkbox'), time = el.querySelector('.rs-t');
    if (data.error) { value.textContent = '—'; change.className = 'rs-c rs-flat'; change.textContent = '시세 연결 불가'; box.innerHTML = ''; time.textContent = ''; return; }
    const dir = L.direction(q.bp ? data.change : data.changePct);
    value.textContent = num(data.price, q.d) + (q.bp ? '%' : '');
    change.className = 'rs-c rs-' + dir;
    change.textContent = (q.bp ? L.signed(L.isNumber(data.change) ? data.change * 100 : null, 1, 'bp') : L.signed(data.changePct)) + ' 전일 대비';
    box.innerHTML = spark(data.closes, dir);
    time.textContent = data.time ? new Date(data.time * 1000).toLocaleString('ko-KR', {timeZone: 'Asia/Seoul', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false}) + ' KST · 제공처 지연 시세' : '';
  }

  // ---- 시장 종합 현황 ----
  function breadthKpi(research) {
    const b = research && research.breadth;
    const card = (title, sub, series, digits, unit, changeUnit) => {
      const last = b && L.lastValid(series || []);
      if (!last) return `<div class="rs-card"><div class="rs-k">${title}</div><div class="rs-v">—</div><div class="rs-c rs-flat">자료 없음</div></div>`;
      const prev = L.previousValid(series, last.index), delta = prev ? last.value - prev.value : null;
      return `<div class="rs-card"><div class="rs-k">${title}<small>${sub}</small></div><div class="rs-v">${num(last.value, digits)}<em>${unit}</em></div><div class="rs-c">${pct(delta, digits, changeUnit)} 전 거래일 대비</div><div class="rs-sparkbox">${spark(L.tail(series, 60), L.direction(delta))}</div><div class="rs-t">${L.formatDate(b.dates[last.index])} 종가 기준</div></div>`;
    };
    return card('상승 업종 비율', `WICS ${b ? b.n_sectors : '—'}개 업종`, b && b.up_sector_ratio, 1, '%', '%p')
      + card('상위권과 나머지 격차', '20거래일 수익률 상위 3개 − 나머지', b && b.gap, 2, '%p', '%p');
  }
  function sizeCards(research) {
    const size = research && research.size;
    if (!size || !size.buckets) return missing('시총 구간 지수를 계산할 자료가 없습니다.');
    return `<div class="rs-grid rs-g3">${size.buckets.map(b => {
      const period = L.periodReturn(b.levels, state.sizeDays), last = L.lastValid(b.levels);
      return `<div class="rs-card"><div class="rs-k">${esc(b.label)}<small>시총 ${b.rank_from}~${b.rank_to}위 · ${b.count}종목</small></div><div class="rs-v">${num(last && last.value, 2)}<em>pt</em></div>
        <div class="rs-c">당일 ${pct(b.r1)} · 기간 ${pct(period && period.pct)}</div><div class="rs-sparkbox tall">${spark(L.tail(b.levels, state.sizeDays + 1), L.direction(period && period.pct))}</div>
        <div class="rs-t">${period ? `${L.formatDate(size.dates[period.from])} → ${L.formatDate(size.dates[period.to])}` : ''}</div></div>`;
    }).join('')}</div><p class="rs-note">${esc(size.definition)}</p>`;
  }
  function flowPanel(research) {
    const b = research && research.breadth;
    if (!b) return missing('업종 흐름 자료가 없습니다.');
    const reading = L.marketReading(b), ups = L.lastValid(b.up_sectors), osc = L.lastValid(b.oscillator), gap = L.lastValid(b.gap);
    return `<div class="rs-reading rs-tone-${reading.tone}"><b>${esc(reading.label)}</b><ul>${reading.notes.map(n => `<li>${esc(n)}</li>`).join('')}</ul></div>
      <div class="rs-stats"><div><span>상승 업종</span><b>${ups ? ups.value : '—'} / ${b.n_sectors}개</b></div><div><span>쏠림 변화</span><b>${pct(osc && osc.value, 3, '')}</b></div><div><span>상위권·나머지 격차</span><b>${num(gap && gap.value, 2)}%p</b></div></div>
      <div class="rs-chart"><canvas id="rsFlowChart"></canvas></div><p class="rs-note">규칙 기반 읽기이며 매수·매도 판단이 아닙니다. 쏠림 변화 = 격차의 5일 평균 − 20일 평균.</p>`;
  }
  function drawFlow(research) {
    const b = research && research.breadth;
    if (!b) return;
    const n = state.flowDays, avg = L.movingAverage(b.up_sectors, 5);
    lineChart('rsFlowChart', labelsOf(L.tail(b.dates, n)), [
      {label: '상승 업종 수', data: L.tail(b.up_sectors, n), borderColor: 'rgba(174,186,202,.55)', stepped: true},
      {label: '5일 평균', data: L.tail(avg, n), borderColor: COLORS.kosdaq, borderWidth: 2},
    ], {decimals: 1, suffix: '개'});
  }
  function sectorTiles(research) {
    const sectors = ((research && research.sectors) || []).filter(s => L.isNumber(s[state.tileKey])).sort((a, b) => b[state.tileKey] - a[state.tileKey]);
    if (!sectors.length) return missing('업종 등락률 자료가 없습니다.');
    const limit = {r1: 3, r5: 6, r20: 12}[state.tileKey];
    return `<div class="rs-tiles">${sectors.map(s => `<div class="rs-tile" style="background:${L.heatColor(s[state.tileKey], limit)}"><span>${esc(s.name)}</span><b>${L.signed(s[state.tileKey])}</b><small>${s.count}종목 · 상승 ${s.up}</small></div>`).join('')}</div>
      <p class="rs-note">업종 내 종목 등락률의 시총가중 평균 · 색은 ±${limit}%에서 가장 진합니다. <button class="rs-link" data-rs-go="breadth">시장 수급 변화에서 자세히 →</button></p>`;
  }
  function leaderList(items, kind) {
    if (!items.length) return missing(kind === 'lead' ? '기준일 자료가 확인된 ETF가 없습니다.' : '조건에 맞는 섹터가 없습니다.');
    return `<ol class="rs-rank">${items.map((x, i) => `<li><i>${i + 1}</i><div><b>${esc(x.row.group)}</b><small>${esc(x.row.name)} · 1개월 ${L.signed(x.row.rs_price_21 * 100, 1, '%p')} · 3개월 ${L.signed(x.row.rs_price_63 * 100, 1, '%p')}${kind === 'rebound' ? ` · 20일선 ${x.row.above_ma20 === true ? '위' : x.row.above_ma20 === false ? '아래' : '미확인'}` : ''}</small></div><em class="rs-${L.direction(x.score)}">${L.signed(x.score * 100, 1, '%p')}</em></li>`).join('')}</ol>`;
  }
  function etfPanels(etf) {
    if (!etf) return panel('주도섹터 · 반등 후보', 'ETF 상대강도', missing(cache.etf && cache.etf.error ? 'ETF 자료를 불러오지 못했습니다.' : 'ETF 자료를 불러오는 중입니다.'), '', 'rs-span2');
    return [['KR', '국내', 'KOSPI'], ['US', '미국', 'SPY']].map(([market, label, bench]) => {
      const asof = etf.markets && etf.markets[market] && etf.markets[market].asof;
      return panel(`주도섹터 TOP 5(${label})`, `${bench} 대비 1·3·6개월 가격 상대강도 평균 · ${esc(asof || '기준일 미확인')}`, leaderList(L.etfLeaders(etf, market), 'lead'), '<button class="rs-link" data-rs-tab="etf">ETF 주도섹터 →</button>')
        + panel(`반등 가능성 섹터(${label})`, '6개월 상대강도 약세 · 1개월 상대강도가 3개월보다 개선된 폭 순', leaderList(L.etfRebounds(etf, market), 'rebound'));
    }).join('');
  }
  function dispPanel(disp) {
    if (!disp || !disp.dates || !disp.dates.length) return missing('이격도 자료가 없습니다.');
    const card = (name, series) => { const last = L.lastValid(series); return `<div><span>${name}</span><b>${num(last && last.value, 2)}%</b><small>50일선 대비 ${pct(last ? last.value - 100 : null)}</small></div>`; };
    return `<div class="rs-stats">${card('코스피', disp.kospi)}${card('코스닥', disp.kosdaq)}</div><div class="rs-chart"><canvas id="rsDispChart"></canvas></div>
      <p class="rs-note">종가 ÷ 50일 이동평균 × 100 · 기준일 ${L.formatDate(disp.dates[disp.dates.length - 1])}</p>`;
  }
  function drawDisp(disp, id = 'rsDispChart') {
    if (!disp || !disp.dates) return;
    const n = state.dispDays;
    lineChart(id, labelsOf(L.tail(disp.dates, n)), [
      {label: '코스피', data: L.tail(disp.kospi, n), borderColor: COLORS.kospi}, {label: '코스닥', data: L.tail(disp.kosdaq, n), borderColor: COLORS.kosdaq},
    ], {decimals: 2, suffix: '%', baseline: 100, baselineLabel: '50일선'});
  }
  function fundPanels(market) {
    const f = market && market.fund;
    if (!f || !f.dates) return panel('증시자금', '', missing('증시자금 자료가 없습니다.'), '', 'rs-span2');
    const latest = (series, label) => { const last = L.lastValid(series || []); return `<div><span>${label}</span><b>${num(last && last.value, 2)} 조원</b><small>${last ? L.formatDate(f.dates[last.index]) + ' 기준' : '자료 없음'}</small></div>`; };
    const tools = seg('fundDays', state.fundDays, [[21, '1개월'], [63, '3개월'], [126, '6개월'], [252, '1년']]);
    return panel('투자자예탁금 · 전체 시장', '코스피·코스닥을 나누지 않은 잔액 · 금융투자협회 공표(2거래일 지연)', `<div class="rs-stats">${latest(f.dep, '투자자예탁금')}</div><div class="rs-chart"><canvas id="rsDepChart"></canvas></div>`, tools, 'rs-span2')
      + panel('코스피 · 거래대금 · 신용융자 잔고', '거래대금은 하루 거래 금액, 신용융자는 기준일 잔액', `<div class="rs-stats">${latest(f.vk, '일별 거래대금')}${latest(f.mk, '신용융자 잔고')}</div><div class="rs-chart"><canvas id="rsKospiFund"></canvas></div>`)
      + panel('코스닥 · 거래대금 · 신용융자 잔고', '공표 시차로 빈 날짜는 채우지 않습니다', `<div class="rs-stats">${latest(f.vq, '일별 거래대금')}${latest(f.mq, '신용융자 잔고')}</div><div class="rs-chart"><canvas id="rsKosdaqFund"></canvas></div>`);
  }
  function drawFund(market) {
    const f = market && market.fund;
    if (!f || !f.dates) return;
    const n = state.fundDays, labels = labelsOf(L.tail(f.dates, n)), o = {decimals: 2, suffix: '조원'};
    lineChart('rsDepChart', labels, [{label: '투자자예탁금', data: L.tail(f.dep || [], n), borderColor: COLORS.violet}], o);
    lineChart('rsKospiFund', labels, [{label: '거래대금', data: L.tail(f.vk || [], n), borderColor: COLORS.kospi}, {label: '신용융자 잔고', data: L.tail(f.mk || [], n), borderColor: COLORS.up}], o);
    lineChart('rsKosdaqFund', labels, [{label: '거래대금', data: L.tail(f.vq || [], n), borderColor: COLORS.kosdaq}, {label: '신용융자 잔고', data: L.tail(f.mq || [], n), borderColor: COLORS.up}], o);
  }
  const pendingCard = (title, text, target) => panel(title, '데이터 수집 연결 전', `<div class="rs-empty">${esc(text)}<br><button class="rs-link" data-rs-go="${target}">필요한 준비 보기 →</button></div>`);

  async function renderOverview(force) {
    const token = ++renderToken;
    const [research, market, disp, trade, cons] = await Promise.all([load('research', force), load('market', force), load('disparity', force), load('trade', force), load('consensus', force)]);
    if (token !== renderToken) return;
    const etf = cache.etf && cache.etf.data;
    const groups = [...new Set(QUOTES.filter(q => q.grp !== 'kr').map(q => q.grp))];
    root.innerHTML = header() + `<div class="rs-wrap">
      ${statusLine([research ? `종가·업종 ${L.formatDate(research.asof)}` : '', disp && disp.dates ? `이격도 ${L.formatDate(disp.dates[disp.dates.length - 1])}` : '', market && market.updated ? `증시자금 갱신 ${esc(market.updated)}` : '', etf && etf.markets ? `ETF 국내 ${esc(etf.markets.KR && etf.markets.KR.asof)} · 미국 ${esc(etf.markets.US && etf.markets.US.asof)}` : '', fileNote('research', '파생 지표'), fileNote('market', '증시자금'), fileNote('disparity', '이격도')])}
      <div class="rs-grid rs-g4">${quoteCard(QUOTES[0], 'big')}${quoteCard(QUOTES[1], 'big')}${breadthKpi(research)}</div>
      ${panel('시총 구간별 지수', research ? `${L.formatDate(research.asof)} KRX 정규장 종가 · 스캐너 수록 종목` : '', sizeCards(research), seg('sizeDays', state.sizeDays, [[21, '1개월'], [63, '3개월'], [129, '6개월']]))}
      ${panel('글로벌 시장 지표', '해외 지수 · 국채 금리 · 에너지 · 환율 · 디지털 자산 · 열린 화면은 15분마다 다시 확인', groups.map(g => `<h3 class="rs-sub">${g}</h3><div class="rs-grid rs-g5">${QUOTES.filter(q => q.grp === g).map(q => quoteCard(q)).join('')}</div>`).join('') + '<p class="rs-note">시세는 Yahoo Finance 제공값이며 시장·종목마다 지연과 휴장이 다릅니다. 작은 그래프는 최근 약 1개월 일별 종가이고 마지막 점이 표시 시세입니다. 금리 1bp = 0.01%p.</p>')}
      <h2 class="rs-section">리서치 데스크</h2>
      <div class="rs-grid rs-g2">
        ${panel('시장의 흐름', research && research.breadth ? `WICS ${research.breadth.n_sectors}개 업종의 상승 비율 · ${L.formatDate(research.asof)}` : '', flowPanel(research), seg('flowDays', state.flowDays, [[20, '20일'], [60, '60일'], [0, '전체']]))}
        ${panel('업종 흐름', '등락률 순 · 색이 진할수록 등락 폭이 큽니다', sectorTiles(research), seg('tileKey', state.tileKey, [['r1', '하루'], ['r5', '한 주'], ['r20', '한 달']]))}
        <div class="rs-slot rs-span2 rs-grid rs-g2" id="rsEtfSlot">${etfPanels(etf)}</div>
        ${tradeDigest(trade)}
        ${consDigest(cons)}
        ${panel('코스피, 코스닥 이격도 추이', '50일 이동평균선에서 얼마나 떨어져 있는지 확인합니다', dispPanel(disp), seg('dispDays', state.dispDays, [[60, '3개월'], [120, '6개월'], [250, '1년']]), 'rs-span2')}
        ${fundPanels(market)}
      </div>
      <p class="rs-foot">통계적 관찰 자료이며 특정 종목의 매수·매도 권유가 아닙니다. 각 자료의 기준일은 항목마다 다르므로 표시된 날짜를 확인하세요.</p></div>`;
    drawFlow(research); drawDisp(disp); drawFund(market); loadQuotes(force);
    if (!etf || force) load('etf', force).then(() => { const slot = document.getElementById('rsEtfSlot'); if (slot && view === 'overview' && token === renderToken) slot.innerHTML = etfPanels(cache.etf.data); });
  }

  // ---- 시장 히트맵 ----
  function heatTile(rect, key, limit, query) {
    const r = rect.item, value = r[key], w = rect.w, h = rect.h;
    const size = Math.max(9, Math.min(20, Math.sqrt(w * h) / 5.2));
    const showName = w > 42 && h > 22, showPct = w > 42 && h > 36;
    const dim = query && !(r.name.toLowerCase().includes(query) || r.code.includes(query));
    return `<button class="rs-heat${dim ? ' dim' : ''}" data-code="${esc(r.code)}" title="${esc(r.name)} (${esc(r.code)}) · ${esc(r.sector)} · ${L.signed(value, 1)} · 시총 ${L.capLabel(r.mcap)}원"
      style="left:${rect.x.toFixed(1)}px;top:${rect.y.toFixed(1)}px;width:${Math.max(0, w - 1).toFixed(1)}px;height:${Math.max(0, h - 1).toFixed(1)}px;background:${L.heatColor(value, limit)};font-size:${size.toFixed(1)}px">${showName ? `<b>${esc(r.name)}</b>` : ''}${showPct ? `<span>${L.signed(value, 1)}</span>` : ''}</button>`;
  }
  function paintHeat() {
    const box = document.getElementById('rsHeatBox'), research = cache.research && cache.research.data;
    if (!box || !research) return;
    const h = state.heat, limit = {r1: 3, r5: 6, r20: 12}[h.key], query = h.query.trim().toLowerCase();
    const rows = L.heatmapRows(research.heatmap).filter(r => r.mcap >= h.minCap).map(r => ({...r, weight: L.tileWeight(r.mcap)}));
    const width = box.clientWidth || 1100, height = Math.max(520, Math.min(860, Math.round(width * 0.58)));
    box.style.height = height + 'px';
    let html = '';
    if (h.grouped) {
      const groups = new Map();
      rows.forEach(r => { if (!groups.has(r.sector)) groups.set(r.sector, []); groups.get(r.sector).push(r); });
      const outer = L.treemap([...groups].map(([name, members]) => ({name, members, weight: members.reduce((s, m) => s + m.weight, 0)})), width, height);
      outer.forEach(g => {
        const head = g.h > 40 ? 17 : 0;
        html += `<div class="rs-heat-group" style="left:${g.x.toFixed(1)}px;top:${g.y.toFixed(1)}px;width:${(g.w - 2).toFixed(1)}px;height:${(g.h - 2).toFixed(1)}px">${head ? `<div class="rs-heat-label">${esc(g.item.name)} · ${g.item.members.length}</div>` : ''}<div class="rs-heat-inner" style="top:${head}px">${L.treemap(g.item.members, Math.max(1, g.w - 2), Math.max(1, g.h - 2 - head)).map(t => heatTile(t, h.key, limit, query)).join('')}</div></div>`;
      });
    } else html = L.treemap(rows, width, height).map(t => heatTile(t, h.key, limit, query)).join('');
    box.innerHTML = html || missing('표시할 종목이 없습니다.');
    const all = L.heatmapRows(research.heatmap), counts = L.moveCounts(all, h.key), total = counts.up + counts.down + counts.flat || 1;
    const capShown = rows.reduce((s, r) => s + r.mcap, 0), capAll = all.reduce((s, r) => s + r.mcap, 0);
    document.getElementById('rsHeatSummary').innerHTML = `<div><b>${all.length.toLocaleString('ko-KR')}</b> 종목 <span class="rs-up">상승 ${counts.up}</span> <span class="rs-down">하락 ${counts.down}</span> <span class="rs-flat">보합 ${counts.flat}</span></div>
      <div class="rs-bar"><i class="u" style="width:${counts.up / total * 100}%"></i><i class="f" style="width:${counts.flat / total * 100}%"></i><i class="d" style="width:${counts.down / total * 100}%"></i></div>
      <div class="rs-dim">${rows.length.toLocaleString('ko-KR')}종목 표시 · 수록 시가총액의 ${num(capAll ? capShown / capAll * 100 : null, 1)}%</div>`;
    document.getElementById('rsHeatLegend').innerHTML = [-1, -2 / 3, -1 / 3, 0, 1 / 3, 2 / 3, 1].map(t => `<i style="background:${L.heatColor(t * limit, limit)}">${t === -1 ? '≤' : t === 1 ? '≥' : ''}${L.signed(t * limit, 0)}</i>`).join('');
  }
  async function renderHeatmap(force) {
    const token = ++renderToken, research = await load('research', force);
    if (token !== renderToken) return;
    const h = state.heat;
    root.innerHTML = header(research ? `<span class="basis">${L.formatDate(research.asof)} · KRX 정규장 종가 기준</span>` : '') + `<div class="rs-wrap">
      ${statusLine([fileNote('research', '히트맵 자료')])}
      ${research ? `<div class="rs-panel"><div class="rs-heat-top"><div id="rsHeatSummary" class="rs-heat-summary"></div>
        <div class="rs-tools">${seg('heatCap', h.minCap, [[3000, '시총 3,000억↑'], [10000, '1조↑'], [0, '전체']])}${seg('heatKey', h.key, [['r1', '하루'], ['r5', '1주'], ['r20', '1개월']])}${seg('heatGroup', h.grouped ? 1 : 0, [[0, '전체'], [1, '업종별']])}<input class="search" id="rsHeatSearch" placeholder="종목명·코드 찾기" value="${esc(h.query)}"></div></div>
        <div id="rsHeatLegend" class="rs-legend"></div><div id="rsHeatBox" class="rs-heatbox"></div>
        <p class="rs-note">타일 면적은 시가총액의 제곱근으로 대형주 쏠림을 줄였습니다(실제 시총 비중과 다름). 스캐너 수록 범위는 KOSPI·KOSDAQ 시가총액 500억원 이상입니다. 타일을 누르면 개별종목 수급 상세로 이동합니다.</p></div>` : missing('히트맵 자료(research_data.json)를 불러오지 못했습니다.')}</div>`;
    paintHeat();
  }

  // ---- 시장 수급 변화 ----
  function sectorBars(research) {
    const key = state.sectorKey, sectors = ((research && research.sectors) || []).filter(s => L.isNumber(s[key])).sort((a, b) => b[key] - a[key]);
    if (!sectors.length) return missing('업종 등락률 자료가 없습니다.');
    const max = Math.max(...sectors.map(s => Math.abs(s[key])), 0.01);
    return `<div class="rs-bars">${sectors.map(s => { const v = s[key], width = Math.abs(v) / max * 50; return `<div class="rs-barrow"><span>${esc(s.name)}</span><div class="rs-track"><i class="${v >= 0 ? 'u' : 'd'}" style="width:${width}%;${v >= 0 ? 'left:50%' : `right:50%`}"></i></div><b class="rs-${L.direction(v)}">${L.signed(v)}</b><small>상승 ${s.up} · 하락 ${s.down}</small></div>`; }).join('')}</div>
      <p class="rs-note">업종 내 종목 등락률의 기준일 시총가중 평균(배당·거래비용 미포함). 업종은 스캐너에 수록된 FnGuide WICS 대분류입니다.</p>`;
  }
  function etfTable(etf) {
    if (!etf) return missing(cache.etf && cache.etf.error ? 'ETF 자료를 불러오지 못했습니다.' : 'ETF 자료를 불러오는 중입니다.');
    const s = state.etfSort, rows = L.etfCandidates(etf, 'KR').sort((a, b) => {
      const x = a[s.key], y = b[s.key];
      if (typeof x === 'string' || typeof y === 'string') return String(x || '').localeCompare(String(y || ''), 'ko') * s.dir;
      return ((L.isNumber(x) ? x : -Infinity) - (L.isNumber(y) ? y : -Infinity)) * s.dir;
    });
    if (!rows.length) return missing('기준일 자료가 확인된 국내 ETF가 없습니다.');
    const th = (key, label, numeric) => `<th data-rs-sort="${key}" class="${numeric ? 'n ' : ''}${s.key === key ? 'on' : ''}">${label}${s.key === key ? (s.dir < 0 ? ' ▼' : ' ▲') : ''}</th>`;
    const ma = v => v === true ? '<span class="rs-up">위</span>' : v === false ? '<span class="rs-down">아래</span>' : '—';
    return `<div class="rs-tablebox"><table class="rs-table"><thead><tr>${th('name', 'ETF')}${th('group', '섹터·테마')}${th('close', '종가', true)}${th('rs_price_21', '상대강도 1개월', true)}${th('rs_price_63', '3개월', true)}${th('rs_price_126', '6개월', true)}<th>20일선</th><th>50일선</th></tr></thead>
      <tbody>${rows.map(r => `<tr><td>${esc(r.name)}</td><td class="rs-dim">${esc(r.group)}</td><td class="n">${num(r.close, 0)}</td><td class="n">${pct(L.isNumber(r.rs_price_21) ? r.rs_price_21 * 100 : null, 1, '%p')}</td><td class="n">${pct(L.isNumber(r.rs_price_63) ? r.rs_price_63 * 100 : null, 1, '%p')}</td><td class="n">${pct(L.isNumber(r.rs_price_126) ? r.rs_price_126 * 100 : null, 1, '%p')}</td><td>${ma(r.above_ma20)}</td><td>${ma(r.above_ma50)}</td></tr>`).join('')}</tbody></table></div>
      <p class="rs-note">상대강도 = ETF 가격수익률 − KOSPI 수익률(21·63·126거래일). 대표 ETF ${rows.length}개 · 기준일 ${esc(etf.markets && etf.markets.KR && etf.markets.KR.asof)}. 배당 포함 총수익률이 아닙니다.</p>`;
  }
  async function renderBreadth(force) {
    const token = ++renderToken;
    const [research, disp] = await Promise.all([load('research', force), load('disparity', force)]);
    if (token !== renderToken) return;
    const b = research && research.breadth, etf = cache.etf && cache.etf.data;
    const trend = (id, title, note) => `<div class="rs-card"><div class="rs-k">${title}<small>${note}</small></div><div class="rs-chart"><canvas id="${id}"></canvas></div></div>`;
    root.innerHTML = header(research ? `<span class="basis">${L.formatDate(research.asof)} 종가 기준</span>` : '') + `<div class="rs-wrap">
      ${statusLine([fileNote('research', '파생 지표'), fileNote('disparity', '이격도')])}
      ${panel('업종 등락률', research ? `스캐너 수록 ${research.heatmap.rows.length.toLocaleString('ko-KR')}종목 · WICS 대분류` : '', sectorBars(research), seg('sectorKey', state.sectorKey, [['r1', '일간'], ['r5', '주간'], ['r20', '월간']]))}
      ${panel('시장 흐름 추이', b ? `${L.formatDate(b.dates[0])} ~ ${L.formatDate(b.dates[b.dates.length - 1])} 중 선택 구간 · 이력이 짧은 지표는 확보된 구간만 표시` : '', b ? `<div class="rs-grid rs-g2">
        ${trend('rsUpSector', '상승 업종 비율', '당일 오른 업종 ÷ 전체 업종 · %')}${trend('rsOsc', '업종 쏠림 변화', '격차의 5일 평균 − 20일 평균 · 오실레이터')}
        ${trend('rsUpStock', '상승 종목 비율', '당일 오른 종목 ÷ 수록 종목 · %')}${trend('rsGap', '상위권 업종과 나머지의 수익률 격차', '20거래일 수익률 상위 3개 − 나머지 · %p')}</div>` : missing('시장 흐름 자료가 없습니다.'), seg('trendDays', state.trendDays, [[20, '20거래일'], [60, '60거래일'], [0, '전체']]))}
      ${panel('코스피 · 코스닥 이격도 추이', '50일 이동평균선 = 100%', disp && disp.dates ? '<div class="rs-chart tall"><canvas id="rsDispChart2"></canvas></div>' : missing('이격도 자료가 없습니다.'), seg('dispDays', state.dispDays, [[60, '3개월'], [120, '6개월'], [250, '1년']]))}
      ${panel('섹터·테마 ETF', '국내 대표 ETF의 벤치마크 대비 가격 상대강도 · 헤더를 누르면 정렬', `<div id="rsEtfTable">${etfTable(etf)}</div>`, '<button class="rs-link" data-rs-tab="etf">ETF 주도섹터에서 전체 보기 →</button>')}
      <p class="rs-foot">통계적 관찰 자료이며 특정 종목의 매수·매도 권유가 아닙니다.</p></div>`;
    if (b) {
      const n = state.trendDays, labels = labelsOf(L.tail(b.dates, n));
      lineChart('rsUpSector', labels, [{label: '상승 업종 비율', data: L.tail(b.up_sector_ratio, n), borderColor: COLORS.kospi}], {suffix: '%', baseline: 50, baselineLabel: '50%'});
      lineChart('rsOsc', labels, [{label: '쏠림 변화', data: L.tail(b.oscillator, n), borderColor: COLORS.violet}], {decimals: 3, baseline: 0, baselineLabel: '0'});
      lineChart('rsUpStock', labels, [{label: '상승 종목 비율', data: L.tail(b.up_stock_ratio, n), borderColor: COLORS.kosdaq}], {suffix: '%', baseline: 50, baselineLabel: '50%'});
      lineChart('rsGap', labels, [{label: '격차', data: L.tail(b.gap, n), borderColor: COLORS.up}], {decimals: 2, suffix: '%p'});
    }
    drawDisp(disp, 'rsDispChart2');
    if (!etf || force) load('etf', force).then(() => { const slot = document.getElementById('rsEtfTable'); if (slot && view === 'breadth' && token === renderToken) slot.innerHTML = etfTable(cache.etf.data); });
  }

  // ---- 수출입 분석 ----
  const TRADE_COLS = [['label', '품목', 0], ['amount', '금액', 1], ['yoy', 'YoY', 1], ['mom', 'MoM', 1], ['sum12', '12개월 누적', 1], ['sum12Yoy', '누적 YoY', 1], ['unitYoy', '단가 YoY', 1]];
  const NATION_COLS = [['name', '국가·지역', 0], ['amount', '금액', 1], ['yoy', 'YoY', 1], ['mom', 'MoM', 1], ['sum12', '12개월 누적', 1], ['sum12Yoy', '누적 YoY', 1], ['balance', '무역수지', 1]];
  const SERIES_COLORS = [COLORS.kospi, COLORS.kosdaq, COLORS.down, COLORS.violet, COLORS.up, COLORS.dim];
  const flowName = () => (state.trade.flow === 'exp' ? '수출' : '수입');
  const toMillion = values => (values || []).map(v => (L.isNumber(v) ? v / 1e6 : null));
  const chartCard = (id, title, unit) => `<div class="rs-card"><div class="rs-k">${title}<small>${unit}</small></div><div class="rs-chart"><canvas id="${id}"></canvas></div></div>`;
  const sortTh = (attr, s) => ([key, label, numeric]) => `<th data-rs-${attr}="${key}" class="${numeric ? 'n ' : ''}${s.sort === key ? 'on' : ''}">${label}${s.sort === key ? (s.dir < 0 ? ' ▼' : ' ▲') : ''}</th>`;

  // 수출 잠정치 요약: 주요 품목·주요 국가의 같은 구간 누계와 전년·전월 동기 대비
  function prelimTable(title, kind, rows) {
    const s = state.trade.prelim;
    if (!rows.length) return `<div><h3 class="rs-sub">${title}</h3>${missing('이 구간의 잠정치를 받지 못했습니다.')}</div>`;
    return `<div><h3 class="rs-sub">${title}</h3><div class="rs-tablebox"><table class="rs-table"><thead><tr><th>${kind === 'item' ? '품목' : '국가'}</th><th class="n">금액</th><th class="n">비중</th><th class="n">전년 동기 대비</th><th class="n">전월 동기 대비</th></tr></thead>
      <tbody>${rows.map(r => `<tr data-row="prelim" data-kind="${kind}" data-id="${r.index}" class="${s.kind === kind && s.index === r.index ? 'sel' : ''}"><td>${esc(r.name)}</td><td class="n">${L.usdLabel(r.amount)}</td><td class="n">${L.isNumber(r.share) ? num(r.share, 1) + '%' : '—'}</td><td class="n">${pct(r.yoy, 1)}</td><td class="n">${pct(r.mom, 1)}</td></tr>`).join('')}</tbody></table></div></div>`;
  }
  function paintPrelim(trade) {
    const box = document.getElementById('rsPrelim'), p = trade.prelim, s = state.trade.prelim;
    if (!box) return;
    const spans = seg('prelimSpan', s.span, [['auto', '최신'], ['d10', '1~10일'], ['d20', '1~20일'], ['m', '월 전체']]);
    const head = (sub, tools = '') => `<div class="rs-panel-head"><div><h2>수출 잠정치</h2><p>${sub}</p></div><div class="rs-tools">${tools}</div></div>`;
    const pick = p && L.prelimPick(p, s.span === 'auto' ? null : s.span);
    if (!pick) { box.innerHTML = head('관세청 10일 단위 잠정치', p ? spans : '') + missing(p ? '이 구간의 잠정치를 아직 받지 못했습니다.' : '10일 단위 잠정치를 아직 받지 못했습니다. 다음 수집 뒤에 표시됩니다.'); return; }
    const items = L.prelimRows(p, 'item', pick), countries = L.prelimRows(p, 'country', pick), total = items[0] || countries[0];
    if (!((p[s.kind] || [])[s.index])) { s.kind = items.length ? 'item' : 'country'; s.index = 0; }
    const rest = L.prelimExcluding(p, pick, '반도체'), label = `${L.formatMonth(pick.month)} · ${L.prelimSpanLabel(pick.span)}${pick.span === 'm' ? '' : ' 누계'}`;
    const series = L.prelimSeries(p, s.kind, s.index, pick.span), shown = `${esc(series.name)} · ${L.prelimSpanLabel(pick.span)}`;
    box.innerHTML = head(`${label} · 수출신고 수리일 기준 · 조업일수 보정 전`, spans)
      + `<div class="rs-stats"><div><span>전체 수출</span><b>${L.usdLabel(total.amount)}</b><small>${label}</small></div><div><span>전년 동기 대비</span><b class="rs-${L.direction(total.yoy)}">${L.signed(total.yoy, 1)}</b><small>전년 같은 구간 누계와 비교</small></div><div><span>전월 동기 대비</span><b class="rs-${L.direction(total.mom)}">${L.signed(total.mom, 1)}</b><small>전월 같은 구간 누계와 비교</small></div><div><span>반도체 제외 수출</span><b>${L.usdLabel(rest && rest.amount)}</b><small>전년 동기 대비 ${L.signed(rest && rest.yoy, 1)}</small></div></div>
      <div class="rs-grid rs-g2">${prelimTable('주요 품목', 'item', items)}${prelimTable('주요 국가', 'country', countries)}</div>
      <div class="rs-grid rs-g2 rs-gap">${chartCard('rsPrelimAmount', `${shown} 금액`, '억 달러')}${chartCard('rsPrelimYoy', `${shown} 전년 동기 대비`, '%')}</div>
      <p class="rs-note">표의 행을 누르면 그 품목·국가의 추이를 아래에 표시합니다. 매월 11일·21일·익월 1일경 공표되는 잠정치이며 이후 정정될 수 있습니다. 관세청이 정한 주요 품목 10개·주요 국가 10개만 제공되고(추적 품목과 분류가 다름) 수입은 포함하지 않습니다. 원자료 단위는 천 달러입니다.</p>`;
    const labels = series.months.map(L.formatMonth);
    lineChart('rsPrelimAmount', labels, [{label: shown, data: series.amount.map(v => (L.isNumber(v) ? v / 1e8 : null)), borderColor: COLORS.kospi}], {decimals: 1, suffix: '억$'});
    lineChart('rsPrelimYoy', labels, [{label: '전년 동기 대비', data: series.yoy, borderColor: COLORS.up}], {decimals: 1, suffix: '%', baseline: 0, baselineLabel: '0'});
  }

  // 품목별
  function tradeRows(trade) {
    const t = state.trade, query = t.query.trim().toLowerCase();
    const rows = (trade.items || []).map(item => ({item, m: L.tradeMetrics(item, t.flow)})).filter(r => !query || r.item.label.toLowerCase().includes(query) || r.item.hs.includes(query) || (r.item.stocks || []).some(s => s.name.toLowerCase().includes(query) || s.code.includes(query)));
    return L.sortBy(rows, r => (t.sort === 'label' ? r.item.label : r.m && r.m[t.sort]), t.dir);
  }
  function paintItems(trade) {
    const table = document.getElementById('rsTradeTable');
    if (!table) return;
    const t = state.trade, rows = tradeRows(trade);
    if (!rows.some(r => r.item.id === t.selected)) t.selected = rows.length ? rows[0].item.id : null;
    const th = sortTh('tsort', t);
    table.innerHTML = rows.length ? `<div class="rs-tablebox"><table class="rs-table"><thead><tr>${th(TRADE_COLS[0])}<th>관련 종목</th><th>기준월</th>${TRADE_COLS.slice(1).map(th).join('')}</tr></thead><tbody>${rows.map(({item, m}) => `<tr data-row="trade" data-id="${esc(item.id)}" class="${item.id === t.selected ? 'sel' : ''}"><td>${esc(item.label)} <small class="rs-dim">HS ${esc(item.hs)}</small></td><td class="rs-dim">${(item.stocks || []).map(s => esc(s.name)).join(', ') || '—'}</td>
      <td class="n">${m ? L.formatMonth(m.month) : '—'}</td><td class="n">${L.usdLabel(m && m.amount)}</td><td class="n">${pct(m && m.yoy, 1)}</td><td class="n">${pct(m && m.mom, 1)}</td><td class="n">${L.usdLabel(m && m.sum12)}</td><td class="n">${pct(m && m.sum12Yoy, 1)}</td><td class="n">${pct(m && m.unitYoy, 1)}</td></tr>`).join('')}</tbody></table></div>` : missing('조건에 맞는 품목이 없습니다.');
    paintTradeDetail(trade);
  }
  function countryBlock(item) {
    const b = L.countryBreakdown(item.countries, state.trade.flow), name = flowName();
    if (!b) return `<h3 class="rs-sub">국가별 ${name}</h3>${missing(item.countries ? `이 품목의 국가별 ${name} 실적이 없습니다.` : '국가별 실적을 아직 받지 못했습니다. 다음 수집 뒤에 표시됩니다.')}`;
    const line = (r, cls = '') => `<tr class="${cls}"><td>${esc(r.name)}${r.code.length === 2 ? ` <small class="rs-dim">${esc(r.code)}</small>` : ''}</td><td class="n">${L.usdLabel(r.m && r.m.amount)}</td><td class="n">${L.isNumber(r.share) ? num(r.share, 1) + '%' : '—'}</td><td class="n">${pct(r.m && r.m.yoy, 1)}</td><td class="n">${L.usdLabel(r.m && r.m.sum12)}</td><td class="n">${pct(r.m && r.m.sum12Yoy, 1)}</td></tr>`;
    return `<h3 class="rs-sub">국가별 ${name} · ${L.formatMonth(b.month)} 기준</h3>
      <div class="rs-grid rs-g2"><div class="rs-tablebox"><table class="rs-table"><thead><tr><th>${state.trade.flow === 'exp' ? '목적국' : '원산국'}</th><th class="n">금액</th><th class="n">비중</th><th class="n">YoY</th><th class="n">12개월 누적</th><th class="n">누적 YoY</th></tr></thead>
        <tbody>${b.rows.map(r => line(r)).join('')}${line(b.rest, 'rs-sum')}${line(b.total, 'rs-sum')}</tbody></table></div>
        ${chartCard('rsTradeCountry', `상위 국가 월별 ${name}`, '백만 달러')}</div>
      <p class="rs-note">최근 12개월 ${name} 누계가 큰 순서로 ${b.rows.length}개국을 보여 줍니다(${(item.countries.countries || 0).toLocaleString('ko-KR')}개국 중). 수출은 최종 목적국, 수입은 원산국 기준이며 비중은 국가별 합계 대비입니다. 실적이 공표된 달에 기록이 없는 국가는 0으로 봅니다.</p>`;
  }
  function paintTradeDetail(trade) {
    const box = document.getElementById('rsTradeDetail'), t = state.trade, item = (trade.items || []).find(i => i.id === t.selected);
    if (!box) return;
    if (!item) { box.innerHTML = missing('품목을 선택하세요.'); return; }
    const m = L.tradeMetrics(item, t.flow), name = flowName();
    box.innerHTML = `<div class="rs-panel-head"><div><h2>${esc(item.label)} · ${name}</h2><p>HS ${esc(item.hs)} · ${m ? L.formatMonth(m.month) + ' 기준' : '관측값 없음'}</p></div><div class="rs-tools">${(item.stocks || []).map(s => `<button class="btn" data-code="${esc(s.code)}">${esc(s.name)} 상세 →</button>`).join('')}</div></div>
      <div class="rs-stats"><div><span>최근월 금액</span><b>${L.usdLabel(m && m.amount)}</b><small>YoY ${L.signed(m && m.yoy, 1)} · MoM ${L.signed(m && m.mom, 1)}</small></div><div><span>12개월 누적</span><b>${L.usdLabel(m && m.sum12)}</b><small>전년 같은 기간 대비 ${L.signed(m && m.sum12Yoy, 1)}</small></div><div><span>단가(금액 ÷ 중량)</span><b>${m && L.isNumber(m.unit) ? num(m.unit, 2) + ' $/kg' : '—'}</b><small>YoY ${L.signed(m && m.unitYoy, 1)}</small></div></div>
      <div class="rs-grid rs-g3">${chartCard('rsTradeAmount', '월별 금액', '백만 달러')}${chartCard('rsTradeYoy', '전년 동월 대비', '%')}${chartCard('rsTradeUnit', '단가', '달러/kg')}</div>
      ${countryBlock(item)}`;
    const labels = item.months.map(L.formatMonth), amount = item[t.flow] || [];
    lineChart('rsTradeAmount', labels, [{label: name + ' 금액', data: toMillion(amount), borderColor: COLORS.kospi}], {decimals: 1, suffix: '백만$'});
    lineChart('rsTradeYoy', labels, [{label: 'YoY', data: L.yoySeries(amount), borderColor: COLORS.up}], {decimals: 1, suffix: '%', baseline: 0, baselineLabel: '0'});
    lineChart('rsTradeUnit', labels, [{label: '단가', data: L.unitSeries(amount, item[t.flow + '_wgt']), borderColor: COLORS.kosdaq}], {decimals: 2, suffix: '$/kg'});
    const b = L.countryBreakdown(item.countries, t.flow);
    if (b && b.rows.length) lineChart('rsTradeCountry', b.months.map(L.formatMonth), b.rows.slice(0, SERIES_COLORS.length).map((r, i) => ({label: r.name, data: toMillion(r.values), borderColor: SERIES_COLORS[i]})), {decimals: 1, suffix: '백만$'});
  }

  // 국가·지역
  function nationRows(nations) {
    const t = state.trade, n = t.nation, query = n.query.trim().toLowerCase();
    const rows = (nations.rows || []).map(row => ({row, m: L.nationMetrics(row, nations.months, t.flow)})).filter(r => !query || r.row.name.toLowerCase().includes(query) || r.row.code.toLowerCase().includes(query));
    return L.sortBy(rows, r => (n.sort === 'name' ? r.row.name : r.m && r.m[n.sort]), n.dir);
  }
  function paintNations(trade) {
    const table = document.getElementById('rsNationTable'), box = document.getElementById('rsNationDetail'), nations = trade.nations;
    if (!table || !box) return;
    if (!nations || !(nations.rows || []).length) { table.innerHTML = missing('국가별 실적을 아직 받지 못했습니다. 다음 수집 뒤에 표시됩니다.'); box.innerHTML = ''; return; }
    const t = state.trade, n = t.nation, rows = nationRows(nations), name = flowName();
    if (!rows.some(r => r.row.code === n.selected)) n.selected = rows.length ? rows[0].row.code : null;
    const th = sortTh('nsort', n), tag = row => (row.kind === 'country' ? `<small class="rs-dim">${esc(row.code)}</small>` : row.kind === 'region' ? '<small class="rs-dim">지역</small>' : '');
    table.innerHTML = rows.length ? `<div class="rs-tablebox"><table class="rs-table"><thead><tr>${th(NATION_COLS[0])}<th>기준월</th>${NATION_COLS.slice(1).map(th).join('')}</tr></thead><tbody>${rows.map(({row, m}) => `<tr data-row="nation" data-id="${esc(row.code)}" class="${row.code === n.selected ? 'sel' : ''}"><td>${esc(row.name)} ${tag(row)}</td>
      <td class="n">${m ? L.formatMonth(m.month) : '—'}</td><td class="n">${L.usdLabel(m && m.amount)}</td><td class="n">${pct(m && m.yoy, 1)}</td><td class="n">${pct(m && m.mom, 1)}</td><td class="n">${L.usdLabel(m && m.sum12)}</td><td class="n">${pct(m && m.sum12Yoy, 1)}</td><td class="n rs-${L.direction(m && m.balance)}">${L.usdLabel(m && m.balance)}</td></tr>`).join('')}</tbody></table></div>` : missing('조건에 맞는 국가가 없습니다.');
    const row = (nations.rows || []).find(r => r.code === n.selected);
    if (!row) { box.innerHTML = missing('국가를 선택하세요.'); return; }
    const m = L.nationMetrics(row, nations.months, t.flow), other = L.nationMetrics(row, nations.months, t.flow === 'exp' ? 'imp' : 'exp');
    box.innerHTML = `<div class="rs-panel-head"><div><h2>${esc(row.name)} · ${name}</h2><p>${m ? L.formatMonth(m.month) + ' 기준' : '관측값 없음'} · 수출은 최종 목적국, 수입은 원산국 기준</p></div></div>
      <div class="rs-stats"><div><span>최근월 ${name}</span><b>${L.usdLabel(m && m.amount)}</b><small>YoY ${L.signed(m && m.yoy, 1)} · MoM ${L.signed(m && m.mom, 1)}</small></div><div><span>12개월 누적 ${name}</span><b>${L.usdLabel(m && m.sum12)}</b><small>전년 같은 기간 대비 ${L.signed(m && m.sum12Yoy, 1)}</small></div><div><span>최근월 ${t.flow === 'exp' ? '수입' : '수출'}</span><b>${L.usdLabel(other && other.amount)}</b><small>YoY ${L.signed(other && other.yoy, 1)}</small></div><div><span>최근월 무역수지</span><b class="rs-${L.direction(m && m.balance)}">${L.usdLabel(m && m.balance)}</b><small>수출 − 수입</small></div></div>
      <div class="rs-grid rs-g2">${chartCard('rsNationAmount', '월별 수출·수입', '백만 달러')}${chartCard('rsNationYoy', `${name} 전년 동월 대비`, '%')}</div>`;
    const labels = nations.months.map(L.formatMonth);
    lineChart('rsNationAmount', labels, [{label: '수출', data: toMillion(row.exp), borderColor: COLORS.kospi}, {label: '수입', data: toMillion(row.imp), borderColor: COLORS.kosdaq}], {decimals: 1, suffix: '백만$'});
    lineChart('rsNationYoy', labels, [{label: 'YoY', data: L.yoySeries(row[t.flow] || []), borderColor: COLORS.up}], {decimals: 1, suffix: '%', baseline: 0, baselineLabel: '0'});
  }
  function paintTrade() {
    const trade = cache.trade && cache.trade.data;
    if (!trade) return;
    destroyCharts();
    paintPrelim(trade);
    if (state.trade.view === 'nation') paintNations(trade); else paintItems(trade);
  }
  async function renderTrade(force) {
    const token = ++renderToken, trade = await load('trade', force);
    if (token !== renderToken) return;
    if (!trade || !(trade.items || []).length) return renderPending('trade');
    const t = state.trade, failed = (trade.errors || []).length, sectionFailed = (trade.section_errors || []).length, nations = trade.nations;
    const flow = seg('tradeFlow', t.flow, [['exp', '수출'], ['imp', '수입']]);
    const body = t.view === 'nation'
      ? panel('국가·지역별 수출입', nations ? `전체와 지역 합산, 최근 12개월 수출입 합계 상위 ${(nations.rows || []).filter(r => r.kind === 'country').length}개국 · 헤더를 누르면 정렬 · 행을 누르면 아래에 추이 표시` : '', '<div id="rsNationTable"></div>', flow + `<input class="search" id="rsNationSearch" placeholder="국가명·코드 검색" value="${esc(t.nation.query)}">`) + '<section class="rs-panel" id="rsNationDetail"></section>'
      : panel('품목별 수출입 추이', '헤더를 누르면 정렬 · 행을 누르면 아래에 추이와 국가별 실적 표시', '<div id="rsTradeTable"></div>', flow + `<input class="search" id="rsTradeSearch" placeholder="품목·HS·관련 종목 검색" value="${esc(t.query)}">`) + '<section class="rs-panel" id="rsTradeDetail"></section>';
    root.innerHTML = header(`<span class="basis">확정 ${L.formatMonth(trade.latest_month)} · 통관 기준</span>` + seg('tradeView', t.view, [['item', '품목별'], ['nation', '국가·지역']])) + `<div class="rs-wrap">
      ${statusLine([`수집 ${esc(String(trade.generated_at || '').slice(0, 16).replace('T', ' '))}`, `품목 ${trade.items.length}개`, nations ? `국가별 확정 ${L.formatMonth(nations.latest_month)}` : '', failed ? `<span class="rs-warn">이번 수집 실패 ${failed}개 품목 · 직전 값 유지</span>` : '', sectionFailed ? `<span class="rs-warn">국가별·잠정치 수집 실패 ${sectionFailed}건 · 직전 값 유지</span>` : ''])}
      <section class="rs-panel" id="rsPrelim"></section>
      ${body}
      <p class="rs-foot">${esc(trade.source && trade.source.provider)} · ${esc(trade.source && trade.source.basis)}. 국가별 실적은 관세청 국가별·품목별 국가별 수출입실적이며, 유럽연합·아세안은 회원국 실적을 더한 값입니다. YoY는 전년 동월, MoM은 전월 대비이며 조업일수를 보정하지 않았습니다. 관련 종목은 YM이 지정한 목록으로 해당 품목의 매출 비중을 뜻하지 않습니다. 통계적 관찰 자료이며 매수·매도 권유가 아닙니다.</p></div>`;
    paintTrade();
  }
  function tradeDigest(trade) {
    if (!trade || !(trade.items || []).length) return pendingCard('수출입 모니터', '품목별 수출입 금액과 전년 대비 증감은 관세청 통계 인증키를 등록한 뒤 표시됩니다.', 'trade');
    const top = L.sortBy(trade.items.map(item => ({item, m: L.tradeMetrics(item, 'exp')})).filter(r => r.m && L.isNumber(r.m.yoy)), r => r.m.yoy, -1).slice(0, 5);
    const pick = L.prelimPick(trade.prelim, null), total = pick && L.prelimRows(trade.prelim, 'item', pick)[0];
    const headline = total && L.isNumber(total.amount) ? `<div class="rs-stats"><div><span>수출 잠정치 · ${L.formatMonth(pick.month)} ${L.prelimSpanLabel(pick.span)}</span><b>${L.usdLabel(total.amount)}</b><small>전년 동기 대비 ${L.signed(total.yoy, 1)} · 전월 동기 대비 ${L.signed(total.mom, 1)}</small></div></div>` : '';
    return panel('수출입 모니터', `수출 전년 동월 대비 상위 · 확정 ${L.formatMonth(trade.latest_month)}`, headline + (top.length ? `<ol class="rs-rank">${top.map((r, i) => `<li><i>${i + 1}</i><div><b>${esc(r.item.label)}</b><small>${L.formatMonth(r.m.month)} · ${L.usdLabel(r.m.amount)} · ${(r.item.stocks || []).map(s => esc(s.name)).join(', ')}</small></div><em class="rs-${L.direction(r.m.yoy)}">${L.signed(r.m.yoy, 1)}</em></li>`).join('')}</ol>` : missing('전년 동월과 비교할 수 있는 품목이 없습니다.')), '<button class="rs-link" data-rs-go="trade">수출입 분석 →</button>');
  }

  // ---- 컨센서스 누적 분석 ----
  const CONS_COLS = [['name', '종목', 0], ['current', '현재 예상치', 1], ['prev_pct', '직전 관측 대비', 1], ['week_pct', '누적 주간', 1], ['month_pct', '누적 월간', 1], ['class', '분류', 0]];
  const CONS_LIMIT = 200;
  const periodLabel = p => (/^\d{6}$/.test(String(p || '')) ? L.formatMonth(p) : esc(p || '미지정'));
  function consRows(cons) {
    const c = state.cons, query = c.query.trim().toLowerCase();
    const rows = (cons.rows || []).filter(r => (c.filter === 'all' || r.class === c.filter) && (!query || String(r.name).toLowerCase().includes(query) || r.code.includes(query)));
    return L.sortBy(rows, r => r[c.sort], c.dir);
  }
  function paintCons() {
    const cons = cache.consensus && cache.consensus.data, table = document.getElementById('rsConsTable');
    if (!cons || !table) return;
    const c = state.cons, rows = consRows(cons), shown = rows.slice(0, CONS_LIMIT);
    if (!rows.some(r => r.code === c.selected)) c.selected = rows.length ? rows[0].code : null;
    const th = ([key, label, numeric]) => `<th data-rs-csort="${key}" class="${numeric ? 'n ' : ''}${c.sort === key ? 'on' : ''}">${label}${c.sort === key ? (c.dir < 0 ? ' ▼' : ' ▲') : ''}</th>`;
    table.innerHTML = rows.length ? `<div class="rs-tablebox"><table class="rs-table"><thead><tr>${th(CONS_COLS[0])}<th>대상 기간</th>${CONS_COLS.slice(1).map(th).join('')}</tr></thead><tbody>${shown.map(r => `<tr data-row="cons" data-id="${esc(r.code)}" class="${r.code === c.selected ? 'sel' : ''}"><td>${esc(r.name || r.code)} <small class="rs-dim">${esc(r.code)}</small></td><td class="rs-dim">${periodLabel(r.period)}</td><td class="n">${num(r.current, 0)}</td><td class="n">${pct(r.prev_pct)}</td><td class="n">${pct(r.week_pct)}</td><td class="n">${pct(r.month_pct)}</td><td>${esc(r.class)}</td></tr>`).join('')}</tbody></table></div>
      <p class="rs-note">${rows.length.toLocaleString('ko-KR')}종목 중 ${shown.length}개 표시${rows.length > shown.length ? ' · 검색이나 분류로 좁혀 보세요' : ''}. 비교할 수 없는 값은 —로 두고 목록 뒤에 둡니다.</p>` : missing('조건에 맞는 종목이 없습니다.');
    const box = document.getElementById('rsConsDetail'), row = (cons.rows || []).find(r => r.code === c.selected);
    if (!box) return;
    destroyCharts();
    if (!row) { box.innerHTML = missing('종목을 선택하세요.'); return; }
    box.innerHTML = `<div class="rs-panel-head"><div><h2>${esc(row.name || row.code)}</h2><p>${esc(row.code)} · ${esc(cons.metric)} · 대상 ${periodLabel(row.period)} · ${esc(row.class)}</p></div><div class="rs-tools"><button class="btn" data-code="${esc(row.code)}">개별종목 상세 →</button></div></div>
      <div class="rs-stats"><div><span>현재 예상치</span><b>${num(row.current, 0)} ${esc(cons.unit)}</b><small>관측 ${esc(cons.asof)}</small></div><div><span>직전 관측</span><b>${num(row.prev, 0)} ${esc(cons.unit)}</b><small>${esc(cons.previous || '비교 대상 없음')} · ${L.signed(row.prev_pct)}</small></div><div><span>누적 주간 / 월간</span><b>${L.signed(row.week_pct)} / ${L.signed(row.month_pct)}</b><small>${esc(cons.week_base || '—')} · ${esc(cons.month_base || '—')} 대비</small></div></div>
      <div class="rs-chart"><canvas id="rsConsChart"></canvas></div><p class="rs-note">변화율 = (이번 예상치 − 기준 예상치) ÷ |기준 예상치|. 같은 대상 분기의 값만 비교하고, 관측일에 값이 없으면 선을 끊습니다.</p>`;
    lineChart('rsConsChart', (cons.series_dates || []).map(d => d.slice(5).replace('-', '.')), [{label: '예상치', data: row.series || [], borderColor: COLORS.kospi, pointRadius: 2, spanGaps: false}], {decimals: 0, suffix: cons.unit});
  }
  async function renderConsensus(force) {
    const token = ++renderToken, cons = await load('consensus', force);
    if (token !== renderToken) return;
    if (!cons || !(cons.rows || []).length) return renderPending('consensus');
    const c = state.cons, s = cons.summary || {}, classes = [...new Set(cons.rows.map(r => r.class))];
    const kpi = (title, value, sub, cls = '') => `<div class="rs-card"><div class="rs-k">${title}</div><div class="rs-v ${cls}">${value}</div><div class="rs-t">${sub}</div></div>`;
    root.innerHTML = header(`<span class="basis">기준일 ${esc(cons.asof)}</span>`) + `<div class="rs-wrap">
      ${statusLine([`관측일 ${cons.observations}개 · ${esc(cons.first_observation)}부터`, cons.previous ? `직전 관측 ${esc(cons.previous)}` : '<span class="rs-warn">관측일이 하나뿐이라 아직 비교할 수 없습니다</span>'])}
      <div class="rs-grid rs-g4">${kpi('수록 종목', cons.rows.length.toLocaleString('ko-KR') + '<em>종목</em>', esc(cons.metric))}${kpi('직전 관측 대비 상향', (s.up ?? 0) + '<em>종목</em>', cons.previous ? `${esc(cons.previous)} → ${esc(cons.asof)}` : '', 'rs-up')}${kpi('직전 관측 대비 하향', (s.down ?? 0) + '<em>종목</em>', '', 'rs-down')}${kpi('유지 / 비교 불가', `${s.flat ?? 0} / ${s.na ?? 0}`, '분류는 중복 집계하지 않습니다')}</div>
      ${panel(`종목별 ${esc(cons.metric)} 변화`, '헤더를 누르면 정렬 · 행을 누르면 아래에 추이 표시', '<div id="rsConsTable"></div>', seg('consFilter', c.filter, [['all', '전체'], ...classes.map(k => [k, k])]) + `<input class="search" id="rsConsSearch" placeholder="종목명·코드 검색" value="${esc(c.query)}">`)}
      <section class="rs-panel" id="rsConsDetail"></section>
      <p class="rs-foot">주가 수익률이 아니라 예상치의 변화입니다. 실제 확정 실적과 다르며, 통계적 관찰 자료로 매수·매도 권유가 아닙니다.</p></div>`;
    paintCons();
  }
  function consDigest(cons) {
    if (!cons || !(cons.rows || []).length) return pendingCard('컨센서스 요약', '분기 영업이익 예상치의 상향·하향 집계는 컨센서스 원자료를 넣은 뒤 표시됩니다.', 'consensus');
    const s = cons.summary || {};
    return panel('컨센서스 요약', `${esc(cons.metric)} · 기준일 ${esc(cons.asof)}`, `<div class="rs-stats"><div><span>직전 관측 대비 상향</span><b class="rs-up">${s.up ?? 0}종목</b></div><div><span>하향</span><b class="rs-down">${s.down ?? 0}종목</b></div><div><span>유지 / 비교 불가</span><b>${s.flat ?? 0} / ${s.na ?? 0}</b></div></div>`, '<button class="rs-link" data-rs-go="consensus">컨센서스 누적 분석 →</button>');
  }

  // ---- 수집 연결 전 화면 ----
  const PENDING = {
    consensus: {
      lead: '증권사 실적 예상치(컨센서스)가 날마다 어떻게 바뀌는지 누적해서 보는 화면입니다. 예상치 원자료를 매 거래일 저장해야 하므로, 자료 출처를 정한 뒤 수집을 시작한 날부터 추이가 쌓입니다.',
      plan: ['요약 카드: 직전 관측 대비 상향·하향·유지·비교 불가 종목 수', '종목별 분기 영업이익 예상치 변화 표: 누적 주간 · 누적 월간 · 직전 관측 대비', '종목을 고르면 현재·1주 전·1개월 전 예상치와 전망치 추이, 직전 관측 대비 변화율 차트', '대상 분기·회계기준(연결/별도)별 필터와 상향순·하향순 정렬'],
      need: ['consensus_pipeline/inbox/ 폴더에 날짜별 CSV(종목코드 · 대상분기 · 영업이익)를 넣으면 다음 수집 때 반영됩니다', '첫 파일을 넣은 날부터 추이가 쌓이고, 관측일이 2개 이상이면 상향·하향 비교가 시작됩니다', '이용 계약이 있는 제공처의 자료만 넣으세요(재배포·화면 게시 가능 범위 확인)', '자동 수집은 제공처 API를 정한 뒤 붙일 수 있습니다'],
    },
    trade: {
      lead: '품목별 수출입 금액·물량·단가를 월별·순별 잠정치와 함께 보는 화면입니다. 관세청 통계를 직접 수집해야 하므로 인증키와 추적 품목 목록이 필요합니다.',
      plan: ['최근 잠정치 요약: 수출·수입 총액과 주요 품목·국가별 금액, 전년 동기 대비 · 전월 대비', '품목별 월별 금액 추이와 YoY·MoM, 일평균 기준 증감', '품목과 관련 종목 연결, 종목을 누르면 개별종목 상세로 이동', '수출/수입 전환, 증감률·금액순 정렬, 품목·종목 검색'],
      need: ['공공데이터포털에서 관세청 API 다섯 개를 같은 계정으로 활용신청하고 인증키를 받습니다(무료): 품목별 수출입실적 · 품목별 국가별 수출입실적 · 국가별 수출입실적 · 수출 주요품목별 10일 단위 잠정치 · 수출 주요국가별 10일 단위 잠정치', '인증키를 GitHub 저장소 Secrets에 DATA_GO_KR_KEY로 등록합니다', 'Actions에서 build-research-sources를 한 번 실행하면 5년치 월별 실적과 잠정치를 받아 옵니다', '추적 품목과 관련 종목은 trade_pipeline/items.json에서 고칩니다', '잠정치는 수출만 제공되며 관세청이 정한 주요 품목 10개·주요 국가 10개 기준입니다'],
    },
  };
  function renderPending(kind) {
    ++renderToken;
    const p = PENDING[kind], list = items => `<ul class="rs-list">${items.map(i => `<li>${esc(i)}</li>`).join('')}</ul>`;
    root.innerHTML = header('<span class="basis">데이터 수집 연결 전</span>') + `<div class="rs-wrap"><div class="rs-panel"><p class="rs-lead">${esc(p.lead)}</p></div>
      <div class="rs-grid rs-g2">${panel('화면 구성 예정', '', list(p.plan))}${panel('연결에 필요한 것', '', list(p.need))}</div>
      <p class="rs-foot">자료가 연결되기 전에는 예시 수치나 추정값을 표시하지 않습니다.</p></div>`;
  }

  // ---- 이벤트 ----
  function render(force) {
    if (!root) return;
    destroyCharts();
    if (!L) { root.innerHTML = missing('research_logic.js를 불러오지 못했습니다.'); return; }
    if (view === 'overview') return renderOverview(force);
    if (view === 'heatmap') return renderHeatmap(force);
    if (view === 'breadth') return renderBreadth(force);
    if (view === 'trade') return renderTrade(force);
    if (view === 'consensus') return renderConsensus(force);
    return renderPending(view);
  }
  function onClick(event) {
    const t = event.target.closest('button,th,tr[data-row]');
    if (!t || !root.contains(t)) return;
    if (t.dataset.row === 'trade') { state.trade.selected = t.dataset.id; return paintTrade(); }
    if (t.dataset.row === 'nation') { state.trade.nation.selected = t.dataset.id; return paintTrade(); }
    if (t.dataset.row === 'prelim') { state.trade.prelim.kind = t.dataset.kind; state.trade.prelim.index = Number(t.dataset.id); return paintTrade(); }
    if (t.dataset.rsNsort) {
      const s = state.trade.nation, key = t.dataset.rsNsort;
      s.dir = s.sort === key ? -s.dir : (key === 'name' ? 1 : -1); s.sort = key;
      return paintTrade();
    }
    if (t.dataset.row === 'cons') { state.cons.selected = t.dataset.id; return paintCons(); }
    if (t.dataset.rsTsort || t.dataset.rsCsort) {
      const s = t.dataset.rsTsort ? state.trade : state.cons, key = t.dataset.rsTsort || t.dataset.rsCsort, text = ['label', 'name', 'class'].includes(key);
      s.dir = s.sort === key ? -s.dir : (text ? 1 : -1); s.sort = key;
      return t.dataset.rsTsort ? paintTrade() : paintCons();
    }
    if (t.dataset.rs === 'reload') return render(true);
    if (t.dataset.rsGo) { const nav = document.querySelector(`#nav button[data-tab="r-${t.dataset.rsGo}"]`); if (nav) nav.click(); return; }
    if (t.dataset.rsTab) { const nav = document.querySelector(`#nav button[data-tab="${t.dataset.rsTab}"]`); if (nav) nav.click(); return; }
    if (t.dataset.code && typeof window.openStockByCode === 'function') return window.openStockByCode(t.dataset.code);
    if (t.dataset.rsSort) {
      const key = t.dataset.rsSort, s = state.etfSort;
      state.etfSort = {key, dir: s.key === key ? -s.dir : (key === 'name' || key === 'group' ? 1 : -1)};
      const slot = document.getElementById('rsEtfTable'); if (slot) slot.innerHTML = etfTable(cache.etf && cache.etf.data);
      return;
    }
    const group = t.closest('[data-rs-seg]');
    if (!group) return;
    const name = group.dataset.rsSeg, raw = t.dataset.v, value = /^-?\d+$/.test(raw) ? Number(raw) : raw;
    if (name === 'heatCap') state.heat.minCap = value; else if (name === 'heatKey') state.heat.key = value; else if (name === 'heatGroup') state.heat.grouped = value === 1;
    else if (name === 'tradeFlow') state.trade.flow = value; else if (name === 'tradeView') state.trade.view = value; else if (name === 'prelimSpan') state.trade.prelim.span = value;
    else if (name === 'consFilter') state.cons.filter = raw; else state[name] = value;
    if (name.startsWith('heat')) { group.querySelectorAll('button').forEach(b => b.classList.toggle('on', b === t)); return paintHeat(); }
    render(false);
  }
  function onInput(event) {
    const id = event.target.id, value = event.target.value;
    if (id === 'rsHeatSearch') { state.heat.query = value; paintHeat(); }
    else if (id === 'rsTradeSearch') { state.trade.query = value; paintTrade(); }
    else if (id === 'rsNationSearch') { state.trade.nation.query = value; paintTrade(); }
    else if (id === 'rsConsSearch') { state.cons.query = value; paintCons(); }
  }
  function onResize() { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { if (root && view === 'heatmap' && root.offsetParent) paintHeat(); }, 150); }

  window.ResearchDesk = {
    mount(next) {
      if (!root) {
        root = document.getElementById('view-research');
        if (!root) return;
        root.addEventListener('click', onClick); root.addEventListener('input', onInput); window.addEventListener('resize', onResize);
      }
      view = TITLES[next] ? next : 'overview';
      clearInterval(quoteTimer);
      if (view === 'overview') quoteTimer = setInterval(() => { if (!document.hidden && root.offsetParent) loadQuotes(false); }, QUOTE_MS);
      return render(false);
    },
    unmount() { clearInterval(quoteTimer); quoteTimer = null; destroyCharts(); ++renderToken; },
    views: Object.keys(TITLES),
  };
})();
