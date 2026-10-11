(function () {
  'use strict';
  const L = window.HoldingsLogic;
  const state = {tab: 'stocks', period: 'daily', group: 'all', supplements: false, minimum: 2, query: '', cohort: '', expanded: '', fund: ''};
  const tabs = [['stocks', '종목별 비중 확대'], ['funds', 'ETF별 구성변동'], ['coverage', '수집 현황']];
  let root = null, data = null, loading = false;
  const esc = value => String(value == null ? '' : value).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const safeUrl = value => { try { const u = new URL(value); return /^https?:$/.test(u.protocol) ? u.href : ''; } catch (_) { return ''; } };
  const pct = value => L.number(value) ? value.toFixed(2) + '%' : '—';
  const delta = value => L.number(value) ? (value > 0 ? '+' : '') + value.toFixed(2) + '%p' : '—';
  const sign = value => !L.number(value) || value === 0 ? '' : value > 0 ? 'hold-up' : 'hold-down';
  const kst = value => { const d = new Date(value); return !value || Number.isNaN(d.getTime()) ? '미제공' : d.toLocaleString('sv-SE', {timeZone: 'Asia/Seoul'}).slice(0, 16) + ' KST'; };
  const pill = (value, tone = '') => '<span class="hold-pill ' + tone + '">' + esc(value) + '</span>';
  const empty = (title, detail) => '<div class="hold-empty"><b>' + esc(title) + '</b><span>' + esc(detail) + '</span></div>';
  const num = value => L.number(value) ? value.toLocaleString('ko-KR') : '—';
  const source = item => { const u = safeUrl(item.source_url); return u ? '<a href="' + esc(u) + '" target="_blank" rel="noopener noreferrer">' + esc(item.source || '원자료') + ' ↗</a>' : esc(item.source || '미확인'); };
  const sourceMode = item => item.collection_mode === 'archived_official_response' ? ' · 저장 공시 참고 · 신규 조회 아님' : '';
  const compareDates = comparison => comparison && comparison.from_date && comparison.to_date ? comparison.from_date + ' → ' + comparison.to_date : '비교일 자료 미확보';
  function fundName(fund, button) {
    const name = esc(fund.etf_name || fund.name || fund.etf_symbol || fund.symbol || fund.id), id = fund.id || fund.etf_id;
    return (button ? '<button class="hold-name-button" type="button" data-hold-fund="' + esc(id) + '">' + name + ' ↗</button>' : '<b class="hold-name">' + name + '</b>') + '<span class="hold-sub"><code>' + esc(fund.symbol || fund.etf_symbol || '') + '</code>' + esc(fund.group || '') + ' · ' + (L.representative(fund) ? '대표' : '보조') + '</span>';
  }
  function header() {
    const c = data.coverage || {}, cards = [['등록 ETF', c.registered == null ? L.list(data.funds).length : c.registered, '한국 국내주식 ETF · 선정 목록'], ['기준일 구성 확보', c.current, '가격 수집과 별도 · 저장 참고 ' + num(c.archived_reference || 0) + '개'], ['1거래일 비교 가능', c.daily_comparable, '정확히 직전 거래일과 비교'], ['5거래일 비교 가능', c.weekly_comparable, '정확히 5거래일 전과 비교']];
    return '<header class="hold-head"><div class="hold-eyebrow">YM RESEARCH · ETF HOLDINGS</div><h1>ETF 구성종목 변동</h1><p>여러 ETF에서 비중이 함께 늘어난 종목을 찾습니다.<br>가격 변동만으로도 비중이 달라질 수 있어, <b>구성 수량 변화와 함께 확인</b>합니다.</p></header>' +
      '<div class="hold-freshness"><span><b>점검 대상 거래일</b> <code>' + esc(data.expected_date || '미확인') + '</code></span><span><b>자료 생성</b> ' + esc(kst(data.generated_at)) + '</span><span>' + (L.number(c.current) && L.number(c.registered) && c.current < c.registered ? '부분 점검 · ' : '') + '구성 기준일·조회일은 ETF별 표시</span><button type="button" class="hold-refresh" data-hold-retry>새로 확인 ↻</button></div>' +
      '<div class="hold-stats">' + cards.map(([label, value, detail]) => '<div class="hold-stat"><span>' + label + '</span><strong>' + num(value) + '<small>개</small></strong><p>' + detail + '</p></div>').join('') + '</div>';
  }
  function navigation() {
    return '<div class="hold-tabs" role="tablist" aria-label="ETF 구성종목 분석 보기">' + tabs.map(([key, title]) => '<button type="button" role="tab" id="hold-tab-' + key + '" aria-controls="hold-panel" aria-selected="' + (state.tab === key) + '" tabindex="' + (state.tab === key ? '0' : '-1') + '" data-hold-tab="' + key + '">' + title + '</button>').join('') + '</div>';
  }
  function toolbar(result) {
    const groups = [...new Set(L.list(data.funds).map(f => f.group).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'ko'));
    let html = '<div class="hold-toolbar"><div class="hold-tool-group">';
    if (state.tab !== 'coverage') html += '<div class="hold-seg" aria-label="비교 기간"><button type="button" data-hold-period="daily" aria-pressed="' + (state.period === 'daily') + '">1거래일</button><button type="button" data-hold-period="weekly" aria-pressed="' + (state.period === 'weekly') + '">5거래일</button></div>';
    html += '<select class="hold-select" id="hold-group" aria-label="ETF 업종 필터"><option value="all">업종·테마 전체</option>' + groups.map(g => '<option value="' + esc(g) + '" ' + (state.group === g ? 'selected' : '') + '>' + esc(g) + '</option>').join('') + '</select>';
    if (state.tab === 'stocks') html += '<select class="hold-select" id="hold-minimum" aria-label="비중 확대 ETF 최소 수"><option value="2" ' + (state.minimum === 2 ? 'selected' : '') + '>2개 이상 ETF에서 확대</option><option value="1" ' + (state.minimum === 1 ? 'selected' : '') + '>1개 이상 ETF에서 확대</option></select>';
    html += '</div><div class="hold-tool-group"><label class="hold-check"><input type="checkbox" id="hold-supplements" ' + (state.supplements ? 'checked' : '') + '>보조 ETF 포함</label><input class="hold-search" type="search" id="hold-search" placeholder="종목 · ETF · 코드 검색" aria-label="종목 ETF 코드 검색" value="' + esc(state.query) + '"></div></div>';
    if (state.tab === 'stocks' && result.pairs.length > 1) html += '<label class="hold-cohort-label">비교 날짜쌍 <select class="hold-select" id="hold-cohort" aria-label="동일 기준일 비교 집합">' + result.pairs.map(p => '<option value="' + esc(L.pairKey(p)) + '" ' + (L.pairKey(result.pair) === L.pairKey(p) ? 'selected' : '') + '>' + esc(compareDates(p)) + (p.expected ? ' · 점검 대상' : ' · 과거 자료') + ' · ' + p.fund_ids.length + '개 ETF</option>').join('') + '</select></label>';
    return html;
  }
  function quantity(item) {
    const extra = L.number(item.quantity_change_pct) && item.quantity_status !== 'unavailable' ? ' (' + (item.quantity_change_pct > 0 ? '+' : '') + item.quantity_change_pct.toFixed(2) + '%)' : '';
    return '<span>' + esc(L.quantityLabel(item.quantity_status)) + esc(extra) + '</span>' + (item.quantity_note ? '<small>' + esc(item.quantity_note) + '</small>' : '');
  }
  function stockDetails(row) {
    return '<div class="hold-detail"><div class="hold-section-title"><h3>' + esc(row.name) + ' · 관련 ETF</h3><span>비교 가능한 보유·편출 ETF ' + row.observed + '개</span></div><div class="hold-scroll"><table class="hold-table hold-detail-table"><thead><tr><th class="hold-name-cell" scope="col">ETF</th><th scope="col">이전 비중</th><th scope="col">현재 비중</th><th scope="col">변화</th><th scope="col">구성 변화</th><th scope="col">수량 확인</th><th scope="col">기준일 · 출처</th></tr></thead><tbody>' + row.details.map(d => '<tr><td class="hold-name-cell">' + fundName(d, true) + '</td><td>' + pct(d.previous_weight_pct) + '</td><td>' + pct(d.current_weight_pct) + '</td><td class="' + sign(d.delta_weight_pp) + '">' + delta(d.delta_weight_pp) + '</td><td>' + pill(L.eventLabel(d.event), ['increased', 'new'].includes(d.event) ? 'positive' : '') + '</td><td class="hold-small">' + quantity(d) + '</td><td class="hold-small">' + esc(compareDates(d)) + '<br>' + source(d) + esc(sourceMode(d)) + '<small>자료 조회 ' + esc(kst(d.observed_at)) + '</small></td></tr>').join('') + '</tbody></table></div><p class="hold-caption">수량은 설정단위(CU) 등을 정규화한 구성 기준입니다. 수량 증가도 펀드의 실제 매수액을 뜻하지 않으며, 주식분할·기업행위는 전수 검증 전입니다.</p></div>';
  }
  function stocks(result) {
    const dateNote = compareDates(result.pair), scope = state.supplements ? '대표·보조 ETF' : '대표 ETF';
    let html = '<div class="hold-section-title"><h2>여러 ETF에서 관측된 비중 확대</h2><span>' + esc(scope) + ' · 비교 가능 ' + result.comparable_count + ' / 선택 ' + result.selected_count + '개</span></div><p class="hold-caption"><b>' + esc(dateNote) + '</b> · 신규 편입 또는 비중 +' + num(data.threshold_pp == null ? 0.05 : data.threshold_pp) + '%p 이상을 확대 ETF로 셉니다. 같은 노출의 보조 ETF는 기본 집계에서 제외합니다.</p>';
    if (result.history_pending) return html + empty('비교 이력 축적 중', '선택한 날짜쌍의 구성 자료가 모두 확보된 ETF가 없습니다. 확보된 현재 구성종목은 ETF별 구성변동에서 볼 수 있습니다.');
    if (!result.rows.length) return html + empty('선택 조건에 맞는 종목이 없습니다', '비교 가능한 ETF ' + result.comparable_count + '개에서 확인했습니다. 최소 확대 ETF 수·업종·검색 조건을 조정할 수 있습니다.');
    html += '<div class="hold-scroll"><table class="hold-table hold-aggregate" aria-label="ETF 통합 종목별 비중 확대"><thead><tr><th scope="col">순위</th><th scope="col" class="hold-name-cell">종목 · 관련 ETF 열기</th><th scope="col">확대 ETF</th><th scope="col">축소 ETF</th><th scope="col">순확대 수</th><th scope="col">평균 비중 변화</th><th scope="col">관측 ETF</th><th scope="col">운용사 · 노출군</th><th scope="col">구성 수량 확인</th></tr></thead><tbody>' + result.rows.map((r, i) => '<tr><td class="hold-rank">' + (i + 1) + '</td><td class="hold-name-cell"><button type="button" class="hold-name-button" data-hold-stock="' + esc(r.code) + '" aria-expanded="' + (state.expanded === r.code) + '" aria-controls="hold-detail-' + esc(r.code) + '">' + esc(r.name) + ' <span aria-hidden="true">' + (state.expanded === r.code ? '−' : '+') + '</span></button><span class="hold-sub"><code>' + esc(r.code) + '</code>신규 편입 ' + r.new_count + ' · 편출 ' + r.removed_count + '</span></td><td class="hold-up">' + r.increased + '</td><td class="hold-down">' + r.decreased + '</td><td class="' + sign(r.net) + '">' + (r.net > 0 ? '+' : '') + r.net + '</td><td class="' + sign(r.average_delta_pp) + '">' + delta(r.average_delta_pp) + '</td><td>' + r.observed + '</td><td title="' + esc(r.issuers.join(', ') + ' / ' + r.groups.join(', ')) + '">' + r.issuer_count + ' / ' + r.group_count + '</td><td class="hold-small">증가 ' + r.quantity_increased + ' · 동일 ' + r.quantity_unchanged + '<small>감소 ' + r.quantity_decreased + ' · 미확인 ' + r.quantity_unavailable + '</small></td></tr>' + (state.expanded === r.code ? '<tr id="hold-detail-' + esc(r.code) + '" class="hold-expanded"><td colspan="9">' + stockDetails(r) + '</td></tr>' : '')).join('') + '</tbody></table></div>';
    return html + '<p class="hold-caption">확대 ETF 수 → 순확대 수 → 평균 비중 변화 순으로 정렬합니다. 평균은 해당 종목을 보유했거나 편출한 비교 ETF 전체의 단순평균이며, 자산 규모를 반영한 자금 유입액이 아닙니다. 운용사·노출군 수는 확대·축소·미변동을 포함한 관측 ETF 기준입니다.</p>';
  }
  function quantityBasis(fund) {
    if (fund.quantity_basis === 'per_cu') return L.number(fund.creation_unit_shares) ? 'CU당 구성 수량 · 1 CU = ' + num(fund.creation_unit_shares) + '좌' : 'CU당 구성 수량 · CU 좌수 미제공';
    if (fund.quantity_basis === 'per_etf_share') return 'ETF 1좌당 구성 수량';
    if (fund.quantity_basis === 'fund_total') return '펀드 전체 구성 수량';
    return '구성 수량 단위 미확인';
  }
  function fundComparison(fund) {
    const comp = fund[state.period] || {status: 'waiting'}, changes = L.list(comp.changes);
    if (!L.eligible(fund, comp, data)) return '<div class="hold-notice"><b>' + esc(L.statusLabel(comp.status === 'ready' ? fund.status : comp.status)) + '</b><span>' + esc(comp.reason || '정확한 비교일의 전체 구성 자료가 확보되면 변화를 계산합니다.') + '</span></div>';
    const sorted = changes.slice().sort((a, b) => (L.number(b.delta_weight_pp) ? b.delta_weight_pp : -Infinity) - (L.number(a.delta_weight_pp) ? a.delta_weight_pp : -Infinity));
    const changed = sorted.filter(c => c.event !== 'unchanged');
    let html = '<div class="hold-section-title"><h3>' + (state.period === 'daily' ? '1거래일' : '5거래일') + ' 구성 변동</h3><span>' + esc(compareDates(comp)) + '</span></div>';
    if (!changed.length) return html + empty('기준 이상 구성 변동 없음', '두 날짜의 전체 구성 자료에서 신규 편입·편출 또는 설정한 비중 변화 기준을 확인했습니다.');
    return html + '<div class="hold-scroll"><table class="hold-table" aria-label="선택 ETF 구성종목 변화"><thead><tr><th scope="col" class="hold-name-cell">종목</th><th scope="col">구성 변화</th><th scope="col">이전 비중</th><th scope="col">현재 비중</th><th scope="col">변화</th><th scope="col">구성 수량 확인</th></tr></thead><tbody>' + changed.map(c => '<tr><td class="hold-name-cell"><b class="hold-name">' + esc(c.name || c.code) + '</b><span class="hold-sub"><code>' + esc(c.code) + '</code></span></td><td>' + pill(L.eventLabel(c.event), ['new', 'increased'].includes(c.event) ? 'positive' : '') + '</td><td>' + pct(c.previous_weight_pct) + '</td><td>' + pct(c.current_weight_pct) + '</td><td class="' + sign(c.delta_weight_pp) + '">' + delta(c.delta_weight_pp) + '</td><td class="hold-small">' + quantity(c) + '</td></tr>').join('') + '</tbody></table></div>';
  }
  function funds() {
    const funds = L.selectedFunds(data, state).filter(f => L.matchesFund(f, state.query)).sort((a, b) => String(a.name).localeCompare(String(b.name), 'ko'));
    if (!funds.length) return empty('선택 조건의 ETF가 없습니다', '업종·보조 ETF·검색 조건을 조정해 주세요.');
    if (!funds.some(f => f.id === state.fund)) state.fund = (funds.find(f => L.list(f.latest_holdings).length) || funds[0]).id;
    const fund = funds.find(f => f.id === state.fund), holdingsTitle = fund.complete === true ? '확보된 전체 구성종목' : fund.complete === false ? '확보된 구성 일부' : '확보된 구성종목', holdings = L.list(fund.latest_holdings).slice().sort((a, b) => (L.number(b.weight_pct) ? b.weight_pct : -Infinity) - (L.number(a.weight_pct) ? a.weight_pct : -Infinity));
    let html = '<label class="hold-fund-select">구성종목을 볼 ETF<select class="hold-select" id="hold-fund-select">' + funds.map(f => '<option value="' + esc(f.id) + '" ' + (f.id === fund.id ? 'selected' : '') + '>' + esc(f.name + ' (' + f.symbol + ')') + '</option>').join('') + '</select></label>' +
      '<section class="hold-fund-card"><div class="hold-section-title"><h2>' + esc(fund.name) + '</h2>' + pill(L.isCurrent(fund, data) ? '점검 대상일 자료' : L.statusLabel(fund.status), L.isCurrent(fund, data) ? 'positive' : '') + '</div><p class="hold-caption"><code>' + esc(fund.symbol) + '</code> · ' + esc(fund.group || '분류 미제공') + ' · ' + (L.representative(fund) ? '대표 ETF' : '보조 ETF') + '</p><div class="hold-fund-meta"><span><b>구성 기준일</b> ' + esc(fund.asof || '미확인') + '</span><span><b>자료 조회일</b> ' + esc(kst(fund.observed_at)) + '</span><span>' + source(fund) + esc(sourceMode(fund)) + '</span><span>' + esc(quantityBasis(fund)) + '</span></div>' + fundComparison(fund) +
      '<div class="hold-section-title hold-holdings-title"><h3>' + holdingsTitle + '</h3><span>' + holdings.length + '개 · 구성 기준일 ' + esc(fund.asof || '미확인') + '</span></div>';
    if (!holdings.length) html += empty('구성 자료 미확보', fund.reason || '수집 현황에서 원천별 상태를 확인할 수 있습니다.');
    else html += '<div class="hold-scroll"><table class="hold-table hold-latest-table" aria-label="선택 ETF 확보된 전체 구성"><thead><tr><th scope="col">번호</th><th scope="col" class="hold-name-cell">구성 종목</th><th scope="col">유형</th><th scope="col">비중</th><th scope="col">공시 구성 수량</th></tr></thead><tbody>' + holdings.map((h, i) => '<tr><td class="hold-rank">' + (i + 1) + '</td><td class="hold-name-cell"><b class="hold-name">' + esc(h.name || h.code) + '</b><span class="hold-sub"><code>' + esc(h.code) + '</code></span></td><td>' + esc(({equity: '주식', fund: '펀드·ETF', cash: '현금', derivative: '파생상품', other: '기타'})[h.asset_type] || '미분류') + '</td><td>' + pct(h.weight_pct) + '</td><td>' + num(h.quantity) + '</td></tr>').join('') + '</tbody></table></div>';
    return html + '<p class="hold-caption">현금·ETF·파생상품 등은 전체 구성에 표시해도 종목별 비중 확대 집계에서 제외합니다. 공시 수량의 단위는 원천별로 다를 수 있으며, 서로 합산하지 않습니다.</p></section>';
  }
  function coverage() {
    const funds = L.selectedFunds(data, state).filter(f => L.matchesFund(f, state.query)), counts = data.coverage || {};
    const current = funds.filter(f => L.isCurrent(f, data)).length;
    let html = '<div class="hold-section-title"><h2>ETF별 구성 자료 수집 현황</h2><span>현재 필터 ' + funds.length + '개 · 기준일 구성 ' + current + '개</span></div><p class="hold-caption">전체 등록 기준 첫 기준점만 확보 ' + num(counts.baseline_only) + '개 · 수집 실패 ' + num(counts.failed) + '개 · 자료 미갱신 ' + num(counts.stale) + '개. 실패·미갱신을 편출이나 비중 변화 없음으로 처리하지 않습니다.</p>';
    if (!funds.length) return html + empty('표시할 ETF가 없습니다', '필터와 검색어를 확인해 주세요.');
    return html + '<div class="hold-scroll"><table class="hold-table hold-coverage-table" aria-label="등록 ETF의 구성종목 수집 상태"><thead><tr><th scope="col" class="hold-name-cell">ETF</th><th scope="col">구성 상태</th><th scope="col">구성 기준일</th><th scope="col">1거래일 비교</th><th scope="col">5거래일 비교</th><th scope="col">구성 수</th><th scope="col">출처 · 자료 조회일</th></tr></thead><tbody>' + funds.map(f => '<tr><td class="hold-name-cell">' + fundName(f, true) + '</td><td class="hold-small" title="' + esc(f.reason || '') + '">' + pill(L.isCurrent(f, data) ? '기준일 자료 확보' : L.statusLabel(f.status), L.isCurrent(f, data) ? 'positive' : '') + '</td><td>' + esc(f.asof || '—') + '</td><td class="hold-small" title="' + esc(f.daily && f.daily.reason || '') + '">' + esc(L.statusLabel(f.daily && f.daily.status || 'waiting')) + '<small>' + esc(compareDates(f.daily)) + '</small></td><td class="hold-small" title="' + esc(f.weekly && f.weekly.reason || '') + '">' + esc(L.statusLabel(f.weekly && f.weekly.status || 'waiting')) + '<small>' + esc(compareDates(f.weekly)) + '</small></td><td>' + num(f.holding_count) + '</td><td class="hold-small">' + source(f) + esc(sourceMode(f)) + '<small>' + esc(kst(f.observed_at)) + '</small></td></tr>').join('') + '</tbody></table></div>';
  }
  function methodology() {
    return '<details class="hold-method" id="hold-method"><summary>비중 변화 읽는 법 · 집계 기준</summary><div><p><b>비중 확대</b>는 두 날짜의 비중 차이(%p)입니다. 예를 들어 5.0% → 5.3%는 +0.3%p입니다. 주가 상승으로도 비중이 늘 수 있으며 실제 자금 유입·매수 확정을 뜻하지 않습니다.</p><p><b>통합 집계</b>는 같은 날짜쌍을 비교할 수 있는 ETF에서만 계산합니다. 기본은 대표 ETF이며 보조 ETF 포함 시 같은 노출의 상품이 중복될 수 있습니다. 운용사 수·노출군 수는 분산된 관측 여부를 판단하는 참고 항목입니다.</p><p><b>비교 자료</b>는 같은 ETF 안에서 출처·구성 기준·비중 기준이 일치해야 합니다. 운용사 설정·해지 바스켓과 제공업체 구성자료는 서로 대체해 변화를 만들지 않습니다. 두 시점 모두 전체 구성이 확인돼야 신규 편입·편출로 판정합니다.</p><p><b>구성 수량</b>은 가능한 경우 설정단위(CU) 등을 정규화해 비교합니다. 동일 수량에서 비중만 늘었다면 가격 변동 영향도 확인해야 합니다. 주식분할 등 기업행위는 전수 검증 전입니다.</p><p><b>범위</b>는 한국 국내주식 ETF 선정 목록입니다. 현금·펀드·파생상품을 종목 통합에서 제외하며, 전 ETF 시장의 자금 흐름을 대표하는 통계가 아닙니다. 1일은 직전 거래일, 5일은 정확히 5거래일 전과 비교하고 해당일 자료가 없으면 이력 축적 중으로 둡니다.</p><p>대상 버전 ' + esc(data.universe_version || '미제공') + ' · 구성 자료에 표시된 날짜와 실제 조회 시각을 구분합니다.</p></div></details><footer class="hold-footer"><span>YM Research · 구성 변화로 보는 조사 우선순위</span><a href="holdings_data.json" target="_blank" rel="noopener noreferrer">현재 데이터 ↗</a></footer>';
  }
  function render() {
    if (!root || !data) return;
    const active = document.activeElement, focus = active && active.id === 'hold-search' ? {start: active.selectionStart, end: active.selectionEnd} : null;
    const result = L.aggregate(data, state);
    root.innerHTML = header() + navigation() + '<div id="hold-panel" role="tabpanel" aria-labelledby="hold-tab-' + state.tab + '">' + toolbar(result) + (state.tab === 'stocks' ? stocks(result) : state.tab === 'funds' ? funds() : coverage()) + '</div>' + methodology();
    if (focus) { const input = root.querySelector('#hold-search'); if (input) { input.focus(); try { input.setSelectionRange(focus.start, focus.end); } catch (_) {} } }
  }
  function setTab(tab) { state.tab = tab; state.query = ''; state.expanded = ''; render(); }
  function bind() {
    root.addEventListener('click', event => {
      const tab = event.target.closest('[data-hold-tab]'), period = event.target.closest('[data-hold-period]'), stock = event.target.closest('[data-hold-stock]'), fund = event.target.closest('[data-hold-fund]');
      if (tab) { setTab(tab.dataset.holdTab); root.querySelector('#hold-tab-' + state.tab).focus(); }
      else if (period) { state.period = period.dataset.holdPeriod; state.cohort = ''; state.expanded = ''; render(); }
      else if (stock) { const code = stock.dataset.holdStock; state.expanded = state.expanded === code ? '' : code; render(); const button = root.querySelector('[data-hold-stock="' + code.replace(/[^a-zA-Z0-9_-]/g, '') + '"]'); if (button) button.focus(); }
      else if (fund) { state.fund = fund.dataset.holdFund; setTab('funds'); }
      else if (event.target.closest('[data-hold-retry]')) load();
    });
    root.addEventListener('input', event => { if (event.target.id === 'hold-search') { state.query = event.target.value; render(); } });
    root.addEventListener('change', event => {
      const el = event.target;
      if (el.id === 'hold-group') { state.group = el.value; state.expanded = ''; }
      else if (el.id === 'hold-supplements') { state.supplements = el.checked; state.expanded = ''; }
      else if (el.id === 'hold-minimum') state.minimum = Number(el.value);
      else if (el.id === 'hold-cohort') { state.cohort = el.value; state.expanded = ''; }
      else if (el.id === 'hold-fund-select') state.fund = el.value;
      else return;
      render();
    });
    root.addEventListener('keydown', event => {
      if (!event.target.closest('[data-hold-tab]') || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault(); const keys = tabs.map(t => t[0]), i = keys.indexOf(state.tab), next = event.key === 'Home' ? 0 : event.key === 'End' ? keys.length - 1 : (i + (event.key === 'ArrowRight' ? 1 : keys.length - 1)) % keys.length;
      setTab(keys[next]); root.querySelector('#hold-tab-' + state.tab).focus();
    });
  }
  async function load() {
    if (loading || !root) return;
    loading = true; root.innerHTML = '<div class="hold-loader" role="status">ETF 구성종목과 비교 이력을 불러오고 있습니다.</div>';
    try {
      const response = await fetch('holdings_data.json', {cache: 'no-store'});
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const next = await response.json();
      if (!next || next.schema_version !== 'etf-holdings-v1' || !Array.isArray(next.funds) || !next.expected_date) throw new Error('자료 형식 확인 필요');
      data = next; render();
    } catch (error) {
      data = null;
      root.innerHTML = '<header class="hold-head"><div class="hold-eyebrow">YM RESEARCH · ETF HOLDINGS</div><h1>ETF 구성종목 변동</h1></header>' + empty('구성종목 자료를 불러오지 못했습니다', '비교 자료를 확인한 뒤 결과를 표시합니다. 자료 부재는 비중 변화가 없다는 뜻이 아닙니다.') + '<p class="hold-caption">' + esc(error.message) + '</p><button class="hold-refresh" type="button" data-hold-retry>다시 불러오기</button>';
    } finally { loading = false; }
  }
  window.ETFHoldings = {mount: function () { if (!root) { root = document.getElementById('view-holdings'); if (!root) return; bind(); } if (!data && !loading) load(); }, reload: load};
})();
