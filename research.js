/* 투자 리서치 화면: 시장 종합 현황 · 시장 히트맵 · 시장 수급 변화 · 컨센서스 · 수출입
   데이터: research_data.json(파생) · market_data.json · disparity_data.json · etf_data.json · /yf 시세 프록시 */
(function () {
  'use strict';
  const L = window.ResearchLogic;
  const TITLES = {overview: '시장 종합 현황', heatmap: '시장 히트맵', breadth: '시장 수급 변화', consensus: '컨센서스 누적 분석', trade: '수출입 분석'};
  const FILES = {research: 'research_data.json', market: 'market_data.json', disparity: 'disparity_data.json', etf: 'etf_data.json'};
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
    const [research, market, disp] = await Promise.all([load('research', force), load('market', force), load('disparity', force)]);
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
        ${pendingCard('수출입 모니터', '품목별 수출입 금액과 전년 대비 증감은 관세청 통계 수집을 연결한 뒤 표시됩니다.', 'trade')}
        ${pendingCard('컨센서스 요약', '분기 영업이익 예상치의 상향·하향 집계는 컨센서스 원자료를 연결한 뒤 표시됩니다.', 'consensus')}
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

  // ---- 수집 연결 전 화면 ----
  const PENDING = {
    consensus: {
      lead: '증권사 실적 예상치(컨센서스)가 날마다 어떻게 바뀌는지 누적해서 보는 화면입니다. 예상치 원자료를 매 거래일 저장해야 하므로, 자료 출처를 정한 뒤 수집을 시작한 날부터 추이가 쌓입니다.',
      plan: ['요약 카드: 직전 관측 대비 상향·하향·유지·비교 불가 종목 수', '종목별 분기 영업이익 예상치 변화 표: 누적 주간 · 누적 월간 · 직전 관측 대비', '종목을 고르면 현재·1주 전·1개월 전 예상치와 전망치 추이, 직전 관측 대비 변화율 차트', '대상 분기·회계기준(연결/별도)별 필터와 상향순·하향순 정렬'],
      need: ['컨센서스 원자료 출처 결정: 이용 계약이 있는 데이터 제공처의 API 또는 내려받은 파일', '출처별 이용 조건 확인(재배포·화면 게시 가능 범위)', '수집 시각과 보관 기간 결정(예: 거래일 밤 1회, 최근 1년)'],
    },
    trade: {
      lead: '품목별 수출입 금액·물량·단가를 월별·순별 잠정치와 함께 보는 화면입니다. 관세청 통계를 직접 수집해야 하므로 인증키와 추적 품목 목록이 필요합니다.',
      plan: ['최근 잠정치 요약: 수출·수입 총액과 주요 품목·국가별 금액, 전년 동기 대비 · 전월 대비', '품목별 월별 금액 추이와 YoY·MoM, 일평균 기준 증감', '품목과 관련 종목 연결, 종목을 누르면 개별종목 상세로 이동', '수출/수입 전환, 증감률·금액순 정렬, 품목·종목 검색'],
      need: ['관세청 수출입무역통계 API 인증키(공공데이터포털에서 발급) → GitHub Secrets 등록', '추적할 품목(HS코드) 목록과 품목별 관련 종목 지정', '월 1회 확정치와 순별(1~10일, 1~20일) 잠정치 중 어디까지 볼지 결정'],
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
    return renderPending(view);
  }
  function onClick(event) {
    const t = event.target.closest('button,th');
    if (!t || !root.contains(t)) return;
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
    if (name === 'heatCap') state.heat.minCap = value; else if (name === 'heatKey') state.heat.key = value; else if (name === 'heatGroup') state.heat.grouped = value === 1; else state[name] = value;
    if (name.startsWith('heat')) { group.querySelectorAll('button').forEach(b => b.classList.toggle('on', b === t)); return paintHeat(); }
    render(false);
  }
  function onInput(event) { if (event.target.id === 'rsHeatSearch') { state.heat.query = event.target.value; paintHeat(); } }
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
