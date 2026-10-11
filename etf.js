(function () {
  'use strict';
  const L = window.ETFLogic;
  const sourceUrl = 'https://taerinsdad-market.streamlit.app/';
  const state = {tab: 'strength', basis: 'independent', ma: '20', query: '', supplements: false, highFilter: 'signals', allRows: false, sortKey: 'rank', sortDir: 'asc', catalogueMarket: 'all', catalogueScope: 'all', catalogueSelection: 'all', catalogueFreshness: 'all'};
  const tabItems = [['strength', '한국 강도'], ['us-high', '미국 신고가'], ['kr-high', '한국 신고가'], ['cross', '양국 비교'], ['catalogue', '전체 ETF 목록']];
  let data = null, loading = false, root = null;
  const esc = value => String(value == null ? '' : value).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const safeUrl = value => { try { const url = new URL(value); return /^https?:$/.test(url.protocol) ? url.href : ''; } catch (_) { return ''; } };
  const pct = (v, unit = '%p') => L.number(v) ? (v > 0 ? '+' : '') + (v * 100).toFixed(2) + unit : '—';
  const decimal = v => L.number(v) ? v.toFixed(2) : '—';
  const sign = v => !L.number(v) || v === 0 ? '' : v > 0 ? 'etf-up' : 'etf-down';
  const pill = (label, tone = 'muted') => '<span class="etf-pill ' + tone + '">' + esc(label) + '</span>';
  function date(value) { return value || '미확인'; }
  function kst(value) { const d = new Date(value); return !value || Number.isNaN(d.getTime()) ? '미제공' : d.toLocaleString('sv-SE', {timeZone: 'Asia/Seoul'}).slice(0, 16) + ' KST'; }
  const scopeLabel = L.scopeLabel;
  const selectionLabels = {representative: '대표 ETF', supplemental: '보조 ETF', unclassified: '분류 검토 전', excluded: '순위 제외'};
  function nameCell(row) {
    const href = safeUrl(row.source_url), title = esc(row.name || row.symbol || row.id);
    return '<td class="etf-name-cell">' + (href ? '<a class="etf-fund-name" href="' + esc(href) + '" target="_blank" rel="noopener noreferrer">' + title + ' ↗</a>' : '<span class="etf-fund-name">' + title + '</span>') +
      '<span class="etf-fund-sub"><code>' + esc(row.symbol || row.id) + '</code>' + esc(row.group || '') + ' · ' + esc(scopeLabel(row)) + ' · ' + esc(selectionLabels[L.selection(row)]) + '</span></td>';
  }
  function sortHeader(label, key, extraClass = '') {
    const on = state.sortKey === key;
    return '<th class="' + extraClass + '" scope="col"><button type="button" data-etf-sort="' + key + '" aria-pressed="' + on + '">' + label + (on ? (state.sortDir === 'asc' ? ' ↑' : ' ↓') : ' ↕') + '</button></th>';
  }
  function displayRank(row) { const r = L.rank(row, state.ma); return L.isCurrent(row, data) && L.number(r.rank) ? r.rank : null; }
  function comparisonCell(row, period) {
    if (!L.isCurrent(row, data)) return '<td class="etf-dim">—</td>';
    const ranking = L.rank(row, state.ma), result = L.rankComparison(ranking, period), comp = result.comparison;
    let title = period + '거래일 전과 같은 관측 집합으로 소급 비교';
    if (comp) title += ' · ' + (comp.date || '비교일 미제공') + ' · ' + (comp.previous_rank == null ? '조건 밖' : comp.previous_rank + '위') + ' → ' + (comp.current_rank == null ? '조건 밖' : comp.current_rank + '위') + ' · ' + comp.cohort_count + '개 기준';
    else if (ranking.dates) title += ' · ' + (ranking.dates[String(period)] || ranking.dates['d' + period] || '');
    if (result.value == null) title = '비교 가능한 동일 기준 자료 없음';
    if (result.value === 'entry' || result.value === 'exit') title += ' · 이동평균선 조건의 진입·이탈, Top10 편입·탈락 아님';
    return '<td title="' + esc(title) + '" class="etf-delta ' + sign(typeof result.value === 'number' ? result.value : null) + '">' + esc(L.deltaLabel(result.value)) + '</td>';
  }
  function rowNote(row) {
    if (!L.isCurrent(row, data)) return ({failed: '가격 수집 실패', stale: '자료 미갱신', quarantined: '원천 간 종가 불일치 · 계산 보류', invalid: '가격 검증 보류', missing: '가격 자료 미확보'})[row.status] || '완료 거래일 자료를 확보하지 못했습니다.';
    const notes = [];
    if (!row.adjustment_verified) notes.push('가격 조정 검증 전');
    if (row.rs_price_126 == null) notes.push('시장 비교 자료 부족');
    if (row.high && row.high.state === 'insufficient_253_closes') notes.push('253개 연속 종가 부족');
    if (L.isListingHigh(row)) notes.push('상장 이후 고점 · 52주 고점과 별도');
    if (row.high && row.high.weak_window_complete === false) notes.push('최근 고점 이력 일부 미확인');
    return notes.join(' · ') || '공급자 가격 기준';
  }
  function summary() {
    const counts = L.counts(data), current = (data.rows || []).filter(r => L.isCurrent(r, data) && L.isRepresentative(r));
    const kr = current.filter(r => r.market === 'KR').length, krHigh = counts.KR_high_usable, usHigh = counts.US_high_usable;
    const cards = [
      ['미국 상장 52주 고점 후보', usHigh ? counts.US : '—', usHigh ? '업종·테마 대표 ETF ' + usHigh + '개 점검' : '고점 계산 자료 부족 · 0건 아님'],
      ['한국 52주 고점 후보', krHigh ? counts.KR : '—', krHigh ? '업종·테마 대표 ETF ' + krHigh + '개 점검' : '고점 계산 자료 부족 · 0건 아님'],
      ['한국 20일선 위', kr ? counts.ma20 : '—', '126일 Sortino 계산 가능한 대표 ETF'],
      ['최근 고점 후 약화 후보', counts.weak_usable ? counts.weak : '—', '5일 고점 이력 확인 가능 ' + counts.weak_usable + '개 중']
    ];
    return '<div class="etf-stats">' + cards.map(c => '<div class="etf-stat"><div class="etf-stat-k">' + c[0] + '</div><div class="etf-stat-v">' + c[1] + '<small>ETF</small></div><div class="etf-stat-note">' + c[2] + '</div></div>').join('') + '</div>';
  }
  function country(market) {
    const m = data.markets && data.markets[market] || {}, isKR = market === 'KR';
    const show = v => esc(v == null ? '—' : v);
    const metrics = [['등록', m.registered], ['가격 확보', m.fresh], ['분류 완료', m.classified == null ? m.rank_enabled : m.classified], ['순위 대상', m.rank_enabled], ['대표 대상', m.representatives == null ? m.representative_count : m.representatives]];
    return '<div class="etf-country"><span class="etf-country-code">' + market + '</span><div class="etf-country-main"><b>' + (isKR ? '한국 국내주식 ETF' : '미국 상장 ETF') + '</b><span>기준일 ' + esc(date(m.asof)) + '</span><div class="etf-coverage-metrics">' + metrics.map(([label, value]) => '<span>' + label + '<strong>' + show(value) + '</strong></span>').join('') + '</div><small>Sortino 계산 가능 ' + show(m.usable) + '개 · 분류 검토 전 ' + show(m.unclassified == null ? m.unclassified_watchlist : m.unclassified) + '개 · 순위 제외 ' + show(m.excluded) + '개</small></div></div>';
  }
  function header() {
    const mode = data.collection_mode === 'live_collection' ? '수집 결과' : '저장 원자료 분석 · 신규 조회 아님';
    const partial = ['KR', 'US'].some(m => { const x = data.markets && data.markets[m]; return !x || x.fresh < x.registered; });
    return '<header class="etf-head"><div><div class="etf-eyebrow">YM RESEARCH · ETF LEADERSHIP</div><h1>ETF 주도섹터</h1><p class="etf-intro">시장을 앞서는 업종과 고점의 변화를 함께 봅니다.<br><b>가격으로 확인하는 주도력</b>이며, 실제 ETF 자금 유입·유출을 뜻하지 않습니다.</p></div><div class="etf-head-links"><a class="etf-link" href="' + sourceUrl + '" target="_blank" rel="noopener noreferrer">원본 대시보드 ↗</a><a class="etf-link" href="#etf-method">지표 읽는 법 ↓</a></div></header>' +
      '<div class="etf-freshness"><span><b>점검 대상 거래일</b> 한국 <span class="etf-date">' + esc(date(data.expected_dates && data.expected_dates.KR)) + '</span> · 미국 <span class="etf-date">' + esc(date(data.expected_dates && data.expected_dates.US)) + '</span></span><span><b>원자료 조회</b> ' + esc(kst(data.last_collection_at)) + '</span><span><b>자료 생성</b> ' + esc(kst(data.generated_at)) + '</span><span>' + mode + (partial ? ' · 부분 점검' : '') + '</span></div>' +
      '<div class="etf-coverage">' + country('KR') + country('US') + '</div>' +
      '<div class="etf-warning"><strong>종가 고점은 검증 전 후보</strong>분할·과거 가격 조정의 전수 검증 전입니다. 원본 지표와 자체 가격 지표는 별도로 표시하며, 미수집·미갱신 자료는 현재 순위에서 제외합니다.</div>' + summary();
  }
  function tabs() {
    return '<div class="etf-tabs" role="tablist" aria-label="ETF 분석 보기">' + tabItems.map(t => '<button type="button" class="etf-tab" role="tab" id="etf-tab-' + t[0] + '" aria-controls="etf-panel" aria-selected="' + (state.tab === t[0]) + '" tabindex="' + (state.tab === t[0] ? '0' : '-1') + '" data-etf-tab="' + t[0] + '">' + t[1] + '</button>').join('') + '</div>';
  }
  function toolbar() {
    if (state.tab === 'catalogue') return catalogueToolbar();
    const strength = state.tab === 'strength', original = strength && state.basis === 'original';
    let left = '';
    if (strength) left = '<div class="etf-seg" aria-label="지표 출처"><button type="button" data-etf-basis="independent" aria-pressed="' + !original + '">자체 계산</button><button type="button" data-etf-basis="original" aria-pressed="' + original + '">원본 참고</button></div>' +
      '<select class="etf-select" id="etf-ma" aria-label="소르티노 이동평균선 조건">' + ['10', '20', '50'].map(n => '<option value="' + n + '" ' + (n === state.ma ? 'selected' : '') + '>' + n + '일선 이상' + (n === '20' ? ' · 기본' : '') + '</option>').join('') + '</select>';
    else if (state.tab !== 'cross') left = '<select class="etf-select" id="etf-high-filter" aria-label="고점 상태 필터">' + [['signals', '신고가·근접·약화'], ['new', '52주 신고가 후보'], ['held', '52주 고점 유지 후보'], ['near', '52주 고점 근접 후보'], ['weak', '약화 후보'], ['listing', '상장 이후 고점 · 1년 미만'], ['all', '분석 대상 전체 · 미수집 포함']].map(p => '<option value="' + p[0] + '" ' + (state.highFilter === p[0] ? 'selected' : '') + '>' + p[1] + '</option>').join('') + '</select>';
    if (state.tab === 'cross') return '<div class="etf-toolbar"><span class="etf-caption">대분류와 세부업종 연결을 함께 표시합니다. 카드 수는 고유 업종 수가 아닙니다.</span><input type="search" class="etf-search" id="etf-search" placeholder="비교 업종 · ETF 검색" aria-label="비교 업종 ETF 검색" value="' + esc(state.query) + '"></div>';
    return '<div class="etf-toolbar"><div class="etf-tool-group">' + left + '</div><div class="etf-tool-group"><label class="etf-check"><input type="checkbox" id="etf-supplements" ' + (state.supplements ? 'checked' : '') + '>보조 ETF 포함</label><input type="search" class="etf-search" id="etf-search" placeholder="업종 · ETF · 코드 검색" aria-label="업종 ETF 코드 검색" value="' + esc(state.query) + '"></div></div>';
  }
  function catalogueToolbar() {
    const select = (id, label, values, selected) => '<label class="etf-filter-label">' + label + '<select class="etf-select" id="' + id + '" aria-label="전체 목록 ' + label + '">' + values.map(([value, name]) => '<option value="' + esc(value) + '" ' + (value === selected ? 'selected' : '') + '>' + esc(name) + '</option>').join('') + '</select></label>';
    const scopes = [...new Set(L.catalogue(data).filter(r => state.catalogueMarket === 'all' || r.market === state.catalogueMarket).map(scopeLabel))].sort((a, b) => a.localeCompare(b, 'ko'));
    if (!scopes.includes(state.catalogueScope)) state.catalogueScope = 'all';
    return '<div class="etf-toolbar etf-catalogue-toolbar"><div class="etf-tool-group">' +
      select('etf-catalogue-market', '시장', [['all', '양국 전체'], ['KR', '한국'], ['US', '미국 상장']], state.catalogueMarket) +
      select('etf-catalogue-scope', '분류', [['all', '분류 전체'], ...scopes.map(s => [s, s])], state.catalogueScope) +
      select('etf-catalogue-selection', '분석 역할', [['all', '역할 전체'], ...Object.entries(selectionLabels)], state.catalogueSelection) +
      select('etf-catalogue-freshness', '가격 상태', [['all', '전체'], ['fresh', '기준일 자료 확보'], ['unavailable', '실패 · 미갱신 · 보류']], state.catalogueFreshness) +
      '</div><input type="search" class="etf-search" id="etf-search" placeholder="업종 · ETF · 코드 검색" aria-label="업종 ETF 코드 검색" value="' + esc(state.query) + '"></div>';
  }
  function catalogueTable() {
    const all = L.catalogue(data), rows = L.filterCatalogue(data, {market: state.catalogueMarket, scope: state.catalogueScope, selection: state.catalogueSelection, freshness: state.catalogueFreshness, query: state.query});
    const sorted = L.sortRows(rows, state.sortKey === 'rank' ? 'name' : state.sortKey, state.sortDir, (r, key) => key === 'selection' ? L.selection(r) : key === 'scope' ? scopeLabel(r) : r[key]);
    const statusLabels = {fresh: '기준일 자료 확보', failed: '수집 실패', stale: '자료 미갱신', quarantined: '검증 보류', invalid: '검증 보류', missing: '자료 미확보'};
    const body = sorted.map(r => {
      const proof = (Array.isArray(r.classification_sources) ? r.classification_sources : []).find(p => p && safeUrl(p.url)) || {};
      const current = L.isCurrent(r, data), role = L.selection(r), reason = r.exclusion_reason || r.classification_reason || proof.evidence || (role === 'unclassified' ? '업종·노출 분류 검토 전' : role === 'supplemental' ? '같은 노출의 대표 ETF와 함께 관찰' : role === 'excluded' ? '업종·테마 순위 대상 밖' : '업종·테마 분석 대상');
      const classificationSource = [r.classification_source_url, r.issuer_url, r.classification_source, proof.url].map(safeUrl).find(Boolean) || '', priceSource = safeUrl(r.source_url);
      const sources = (priceSource ? '<a href="' + esc(priceSource) + '" target="_blank" rel="noopener noreferrer">' + esc(r.source || '시세') + ' ↗</a>' : esc(r.source || '미확인')) + (classificationSource ? '<br><a href="' + esc(classificationSource) + '" target="_blank" rel="noopener noreferrer">상품 분류 근거 ↗</a>' : '');
      const hs = L.status(r, data), statusLabel = current ? statusLabels.fresh : r.status === 'fresh' ? '기준일 불일치' : statusLabels[r.status] || '현재 자료 없음';
      return '<tr>' + nameCell(r) + '<td>' + esc(r.market) + '</td><td>' + pill(selectionLabels[role], role === 'representative' ? 'held' : 'muted') + '</td><td class="etf-small etf-catalogue-reason">' + esc(reason) + '</td><td title="' + esc(r.reason || '') + '">' + pill(statusLabel, current ? 'held' : 'muted') + '</td><td>' + esc(r.date || r.cached_date || '—') + '</td><td>' + pill(hs.label, hs.tone) + '</td><td class="etf-small etf-sources">' + sources + '</td></tr>';
    }).join('');
    const table = '<div class="etf-table-scroll"><table class="etf-table etf-catalogue-table" aria-label="전체 등록 ETF와 분류 및 자료 상태"><thead><tr>' + sortHeader('업종 · ETF', 'name', 'etf-name-cell') + sortHeader('시장', 'market') + sortHeader('분석 역할', 'selection') + '<th scope="col">분류 · 선정 사유</th><th scope="col">가격 상태</th>' + sortHeader('가격 기준일', 'date') + '<th scope="col">고점 관찰</th><th scope="col">자료 출처</th></tr></thead><tbody>' + body + '</tbody></table></div>';
    return discoverySummary() + '<p class="etf-caption"><b>수집 등록 전체를 보여 줍니다.</b> 대표·보조 ETF뿐 아니라 분류 검토 전 또는 순위 제외 상품도 포함합니다. 대표는 업종의 첫 관찰용이고, 보조는 같은 노출을 추가 확인하는 용도입니다. 분류 검토 전·순위 제외 상품의 가격 신호는 업종 순위와 대표 집계에 넣지 않습니다.</p>' +
      section('전체 ETF 목록', sorted.length + ' / 등록 ' + all.length + '개', sorted.length ? table : empty('해당 조건의 ETF가 없습니다', '시장·분류·분석 역할·가격 상태 또는 검색어를 조정해 주세요.')) +
      '<p class="etf-caption">등록은 이 스캐너에 수집 대상으로 선정한 목록입니다. 전체 상장 ETF 전수 수집을 뜻하지 않습니다. 가격 상태가 실패·미갱신이면 표시된 날짜는 보존된 마지막 자료의 기준일일 수 있습니다.</p>';
  }
  function discoverySummary() {
    const c = data.catalog_summary && data.catalog_summary.KR;
    if (!c) return '';
    if (!L.number(c.listed_count)) return '<div class="etf-warning"><strong>한국 전체 상장 목록 미확인</strong>가격 수집 등록 목록과 별도로 후보 목록을 확인하지 못했습니다. 등록 ETF 점검은 아래에서 확인할 수 있습니다.</div>';
    const href = safeUrl(c.source_url), observed = kst(c.observed_at), count = c.candidate_count == null ? '—' : c.candidate_count;
    return '<div class="etf-discovery"><div><b>한국 ETF 후보군 탐색</b><span>전체 상장 목록 <strong>' + esc(c.listed_count.toLocaleString('ko-KR')) + '</strong>개 · 추가 검토 후보 <strong>' + esc(count) + '</strong>개</span></div><p>상장 목록은 후보를 찾는 용도입니다. 추가 검토 후보는 운용사 분류 확인 전이며, 아래 가격 수집 등록·분석 완료 숫자에 포함하지 않습니다.</p><small>목록 조회 ' + esc(observed) + ' · 목록 기준일 ' + esc(c.source_asof || '원천 미제공') + (c.collection_mode && !/^live/.test(c.collection_mode) ? ' · 저장 목록 참고' : '') + (c.status && /fail|stale|error/.test(c.status) ? ' · 최신 목록 갱신 미확인' : '') + (href ? ' · <a href="' + esc(href) + '" target="_blank" rel="noopener noreferrer">목록 출처 ↗</a>' : '') + '</small></div>';
  }
  function section(title, count, content) { return '<section class="etf-section"><div class="etf-section-title"><h2>' + title + '</h2><span>' + esc(count) + '</span></div>' + content + '</section>'; }
  function empty(title, text) { return '<div class="etf-empty"><b>' + title + '</b>' + text + '</div>'; }
  function rankTable(rows, conditionPending = false) {
    const head = '<thead><tr>' + sortHeader('순위', 'rank', 'etf-rank') + sortHeader('업종 · ETF', 'name', 'etf-name-cell') + sortHeader('RS 126일<br>KOSPI 대비', 'rs_price_126') + sortHeader('Sortino<br>126일', 'sortino_price_126') + sortHeader('Sortino<br>252일', 'sortino_price_252') + sortHeader('1일 변화', 'delta_1') + sortHeader('5일 변화', 'delta_5') + sortHeader('20일 변화', 'delta_20') + '<th scope="col">' + state.ma + '일선</th><th scope="col">고점 상태</th><th scope="col">기준일</th></tr></thead>';
    const body = rows.map(row => {
      const current = L.isCurrent(row, data), r = displayRank(row), ma = current ? row['above_ma' + state.ma] : null, hs = L.status(row, data);
      return '<tr><td class="etf-rank">' + (r == null ? '—' : r) + '</td>' + nameCell(row) +
        '<td class="' + sign(row.rs_price_126) + '">' + pct(current ? row.rs_price_126 : null) + '</td><td>' + decimal(current ? row.sortino_price_126 : null) + '</td><td>' + decimal(current ? row.sortino_price_252 : null) + '</td>' +
        comparisonCell(row, 1) + comparisonCell(row, 5) + comparisonCell(row, 20) + '<td>' + (ma === true ? '<span class="etf-accent">이상</span>' : ma === false ? '<span class="etf-dim">미만</span>' : '—') + '</td><td title="' + esc(rowNote(row)) + '">' + pill(hs.label, hs.tone) + '</td><td>' + esc(row.date || '—') + '</td></tr>';
    }).join('');
    return rows.length ? '<div class="etf-table-scroll"><table class="etf-table" aria-label="한국 ETF 자체 강도 ' + (conditionPending ? '조건 미충족 및 자료 상태' : '순위') + '">' + head + '<tbody>' + body + '</tbody></table></div>' : empty('표시할 ETF가 없습니다', '검색어와 조건을 확인해 주세요.');
  }
  function strength() {
    if (state.basis === 'original') return originalStrength();
    const rows = (data.rows || []).filter(r => r.market === 'KR' && L.isRankEligible(r) && (state.supplements || L.isRepresentative(r)) && L.matches(r, state.query));
    const sorted = L.sortRows(rows, state.sortKey, state.sortDir, (r, k) => k === 'rank' ? displayRank(r) : k.startsWith('delta_') ? L.rank(r, state.ma)[k] : r[k]);
    const ready = sorted.filter(r => L.isCurrent(r, data) && r['above_ma' + state.ma] === true && L.number(r.sortino_price_126));
    const pending = sorted.filter(r => !ready.includes(r));
    const visible = state.allRows ? ready : ready.slice(0, 10);
    let html = '<p class="etf-caption"><b>' + (state.supplements ? '대표·보조 ETF 표시' : '대표 ETF 중심 표시') + ' · 자체 계산 · 원본 산식과 다름.</b> Sortino 126일, ' + state.ma + '일선 이상 조건. RS는 KOSPI와의 가격수익률 차이(%p)입니다. 순위는 대표·보조를 포함한 전체 순위 대상 ETF에서 계산하며, 대표만 표시하면 순위가 건너뛸 수 있습니다. 표시·검색 조건은 순위를 바꾸지 않습니다.</p>' + universeNote();
    if (data.markets.KR && data.markets.KR.benchmark_status !== 'fresh') html += '<div class="etf-warning"><strong>자체 RS 계산 보류</strong>현재 비교 가능한 KOSPI 가격 자료가 없어 RS는 —로 표시합니다. 원본 참고에서 별도로 저장된 6개월 RS를 확인할 수 있습니다.</div>';
    if (!ready.length) html += empty('현재 조건으로 표시할 강도 순위가 없습니다', '가격 이력·벤치마크·이동평균선 조건을 확인합니다. 자료 부족을 순위 유지로 해석하지 않습니다.');
    else html += section(state.allRows ? '조건 통과 ETF' : '조건 통과 ETF · 현재 정렬 상위 10개', visible.length + ' / ' + ready.length + '개 표시', rankTable(visible)) + '<label class="etf-check"><input id="etf-all-rows" type="checkbox" ' + (state.allRows ? 'checked' : '') + '>조건 통과 ETF 전체 보기</label>';
    html += '<p class="etf-caption">↑ 순위 개선 · ↓ 순위 하락 · 0 비교 순위 동일 · <b>조건진입/조건이탈</b>은 이동평균선 조건 변화입니다. Top10 신규 진입·이탈을 뜻하지 않습니다. 각 변화에 마우스를 올리면 비교일·관측 집합을 볼 수 있습니다.</p>';
    if (pending.length) html += '<details class="etf-disclosure"><summary>조건 미충족 · 계산 대기 ' + pending.length + '개</summary><div class="etf-disclosure-body">' + rankTable(pending, true) + '</div></details>';
    return html;
  }
  function originalStrength() {
    const original = data.original || {}, rows = original.rows || [], key = 'sortino' + state.ma + '_rank';
    if (!rows.length) return empty('원본 표를 확인하지 못했습니다', '자체 계산 화면은 독립 수집 결과로 계속 이용할 수 있습니다. 원본 순위는 추정해 채우지 않습니다.');
    const matching = rows.filter(r => (state.supplements || L.isRepresentative(r)) && L.matches(r, state.query));
    const sortKey = state.sortKey === 'rank' ? key : state.sortKey === 'rs_price_126' ? 'rs_6m' : state.sortKey === 'name' ? 'name' : key;
    const sorted = L.sortRows(matching, sortKey, state.sortDir);
    const hasRanks = sorted.some(r => L.number(r[key]));
    const body = sorted.map(r => '<tr><td class="etf-rank">' + (L.number(r[key]) ? r[key] : '—') + '</td>' + nameCell(r) + '<td class="' + sign(r.rs_6m) + '">' + pct(r.rs_6m) + '</td><td class="etf-dim">—<span class="etf-fund-sub">수치 미제공</span></td><td>' + (L.number(r[key]) ? '이상 · 원본 조건' : '—') + '</td><td>' + esc(date(r.date || original.date || original.asof)) + '</td></tr>').join('');
    return '<div class="etf-warning"><strong>원본 저장 관측값 · ' + esc(date(original.date || original.asof)) + '</strong>' + esc(original.rank_basis || '관측 ETF 내 순위') + '. 원본의 6개월 지표이며 자체 126거래일 지표와 이어 붙이지 않습니다. 전일·5일 변화는 비교 자료가 없어 미표시합니다.</div>' +
      '<p class="etf-caption">표 기준일과 오늘의 완료 거래일이 다르면 <b>과거 참고값</b>입니다. 소르티노 숫자는 원본에서 제공하지 않아 —로 표시합니다. 원본의 배당조정·목표수익률·연율화 세부 방식은 미확인입니다.</p>' +
      (!hasRanks ? '<div class="etf-warning"><strong>' + state.ma + '일선 조건 순위 미확보</strong>확인된 6개월 RS만 표시합니다. 20일선 조건에서 저장된 관측 순위를 볼 수 있습니다.</div>' : '') +
      section('원본 참고 · 관측 ETF', sorted.length + '개', sorted.length ? '<div class="etf-table-scroll"><table class="etf-table" aria-label="원본 한국 ETF 관측표"><thead><tr>' + sortHeader('관측 순위', 'rank', 'etf-rank') + sortHeader('업종 · ETF', 'name', 'etf-name-cell') + sortHeader('6개월 RS<br>KOSPI 대비', 'rs_price_126') + '<th scope="col">6개월 Sortino<br>숫자</th><th scope="col">' + state.ma + '일선</th><th scope="col">표 기준일</th></tr></thead><tbody>' + body + '</tbody></table></div>' : empty('검색 결과가 없습니다', '다른 업종명이나 ETF 코드를 검색해 주세요.'));
  }
  function highTable(rows) {
    const sorted = L.sortRows(rows, state.sortKey, state.sortDir, (r, k) => k === 'rank' ? L.status(r, data).order : k === 'gap' ? r.high && r.high.gap : r[k]);
    const head = '<thead><tr>' + sortHeader('업종 · ETF', 'name', 'etf-name-cell') + sortHeader('고점 상태', 'rank') + sortHeader('고점 대비', 'gap') + '<th scope="col">고점일</th><th scope="col">가격 기준일</th>' + sortHeader('RS 126일', 'rs_price_126') + '<th scope="col">해석 · 자료 상태</th></tr></thead>';
    const body = sorted.map(r => {
      const current = L.isCurrent(r, data), hs = L.status(r, data), high = r.high || {};
      return '<tr>' + nameCell(r) + '<td>' + pill(hs.label, hs.tone) + '</td><td>' + pct(current ? high.gap : null, '%') + '</td><td>' + esc(current && high.peak_date || '—') + '</td><td>' + esc(r.date || '—') + '</td><td class="' + sign(r.rs_price_126) + '">' + pct(current ? r.rs_price_126 : null) + '</td><td class="etf-small">' + esc(rowNote(r)) + '</td></tr>';
    }).join('');
    return '<div class="etf-table-scroll"><table class="etf-table" aria-label="ETF 종가 고점 점검">' + head + '<tbody>' + body + '</tbody></table></div>';
  }
  function highs(market) {
    const base = (data.rows || []).filter(r => r.market === market && L.isRankEligible(r) && (state.supplements || L.isRepresentative(r)) && L.matches(r, state.query));
    const rows = base.filter(r => state.highFilter === 'all' || (L.isCurrent(r, data) && (state.highFilter === 'listing' ? L.isListingHigh(r) : state.highFilter === 'signals' ? ['new', 'held', 'near', 'weak', 'listing-new', 'listing-held', 'listing-near'].includes(L.status(r, data).tone) : !L.isListingHigh(r) && L.status(r, data).tone === state.highFilter)));
    const currentCount = base.filter(r => L.isCurrent(r, data)).length;
    const groups = market === 'US' ? [['미국 대분류', '미국 대분류 업종'], ['미국 세부업종', '미국 세부업종·테마'], ['미국 상장 글로벌 업종·테마', '글로벌 업종·테마 · 미국 상장']] : [['국내 업종', '한국 업종'], ['국내 테마', '한국 테마'], ['국내 스타일', '한국 스타일 · 업종과 별도']];
    let html = '<p class="etf-caption"><b>분할조정 검증 전 종가 고점 후보</b> · 최소 253개 종가로 직전 252거래일 최고 종가와 비교합니다. 고점 대비 −3% 이내는 근접, 최근 5거래일에 고점을 기록한 뒤 −3% 이상 하락하면 약화 후보입니다. 장중 52주 고가와는 다른 기준입니다.</p>';
    if (!rows.length) html += empty(currentCount ? '이 조건에서 확인된 후보가 없습니다' : '현재 고점 여부를 확인할 수 없습니다', currentCount ? '확인한 ' + currentCount + '개 ETF의 결과입니다. 미확인 ETF는 포함되지 않았습니다. 전체 보기에서 자료 상태를 확인할 수 있습니다.' : '공급자 실패·자료 미갱신은 신고가 0건을 의미하지 않습니다. 전체 보기에서 종목별 상태를 확인할 수 있습니다.');
    groups.forEach(([key, label]) => { const subset = rows.filter(r => scopeLabel(r) === key && !L.isListingHigh(r)); if (subset.length) html += section(label, subset.length + '개 ETF', highTable(subset)); });
    const listings = rows.filter(L.isListingHigh);
    if (listings.length) html += section('상장 이후 고점 · 52주 고점과 별도', listings.length + '개 ETF', '<p class="etf-caption">상장 1년 미만이며 검증된 최초 거래일부터의 일별 종가를 비교합니다. 52주 신고가 건수와 합산하지 않습니다.</p>' + highTable(listings));
    html += '<p class="etf-caption">상단 신호 건수는 업종·테마 대표 ETF 기준입니다. 보조·스타일·상장 이후 고점은 대표 신호 건수에 합산하지 않습니다. 미국 상장 글로벌 업종·테마를 미국 국내업종으로 해석하지 않습니다. 분류 검토 전·순위 제외 상품은 전체 ETF 목록에서 볼 수 있습니다.</p>';
    return html;
  }
  function cross() {
    const pairs = L.crossCountry(data).filter(p => !state.query || [p.name, p.relation, ...p.sides.US.registered.map(r => r.name + ' ' + r.symbol), ...p.sides.KR.registered.map(r => r.name + ' ' + r.symbol)].join(' ').toLocaleLowerCase().includes(state.query.trim().toLocaleLowerCase()));
    function side(pair, market) {
      const s = pair.sides[market], rows = s.strong.length ? s.strong : s.fresh;
      return '<div class="etf-cross-side"><span>' + market + '</span><div class="etf-cross-items">' + (!rows.length ? '현재 비교 자료 없음' : rows.slice(0, 2).map(r => esc(r.name || r.symbol) + '<small>' + (scopeLabel(r) === '미국 상장 글로벌 업종·테마' ? '미국 상장 글로벌 · ' : '') + (L.number(r.rs_price_126) ? 'RS 126일 ' + pct(r.rs_price_126) : 'RS 확인 불가') + ' · ' + esc(L.status(r, data).label) + '</small>').join('')) + (rows.length > 2 ? '<small>외 ' + (rows.length - 2) + '개 ETF</small>' : '') + '</div></div>';
    }
    const cards = scope => { const list = pairs.filter(p => p.scope === scope); return list.length ? '<div class="etf-cross-grid">' + list.map(p => '<article class="etf-cross"><div class="etf-cross-head"><h3>' + esc(p.name) + '</h3>' + pill(p.label, p.tone) + '</div><p class="etf-cross-rel">' + esc(p.relation) + '</p>' + side(p, 'US') + side(p, 'KR') + '</article>').join('') + '</div>' : empty('해당 비교 업종이 없습니다', '다른 업종명이나 ETF 코드를 검색해 주세요.'); };
    return '<p class="etf-caption">시장 대비 RS가 양수이거나 고점 신호가 있는 대표 ETF를 연결합니다. <b>양국 동시 확인</b>은 미국 국내업종과 한국 양쪽 근거가 있을 때만 표시합니다. 신고가 검증 전 후보는 별도 표시하고, 한쪽 자료가 없으면 비교를 보류합니다. 구성 종목이 달라 한국의 후행 상승을 뜻하지 않습니다.</p>' + section('미국 국내업종 ↔ 한국', '대표 ETF 비교', cards('US')) + section('글로벌 업종·테마 ↔ 한국', '미국 상장 · 미국 국내업종과 별도', cards('GLOBAL'));
  }
  function universeNote() {
    const u = data.universe || {}, changed = u.changed_at || u.expanded_at, reason = u.change_reason;
    return '<p class="etf-caption etf-universe-note"><b>대상 목록 ' + esc(u.version || '버전 미제공') + '</b>' + (changed ? ' · 변경일 ' + esc(changed) : '') + (reason ? ' · ' + esc(reason) : '') + '<br>전일·5일·20일 변화는 현재 대상 목록으로 과거까지 다시 계산한 값입니다. 이전 목록의 순위와 직접 연결하지 않으며, 같은 비교 집합을 확보하지 못하면 —로 표시합니다.</p>';
  }
  function methodology() {
    const method = data.method || {}, universe = data.universe || {};
    const coverage = ['KR', 'US'].map(market => { const m = data.markets[market] || {}; return (market === 'KR' ? '한국' : '미국 상장') + ' 등록 ' + (m.registered == null ? '—' : m.registered) + '개 · 순위 대상 ' + (m.rank_enabled == null ? '—' : m.rank_enabled) + '개'; }).join(', ');
    return '<details class="etf-disclosure" id="etf-method"><summary>지표 읽는 법 · 비교 기준과 자료 범위</summary><div class="etf-disclosure-body"><p><b>상대강도(RS_PRICE)</b>는 21·63·126거래일 ETF 가격수익률에서 한국 KOSPI 또는 미국 SPY의 같은 기간 가격수익률을 뺀 값입니다. 양수면 시장보다 강했다는 뜻입니다. 표는 126일 값을 기본으로 보여 줍니다. 원본의 6개월 RS, Mansfield RS, Normalized RS와 다른 지표입니다.</p><p><b>소르티노(Sortino_PRICE)</b>는 일별 목표수익률 0%를 기준으로 연율화합니다. √252 × 일수익률 평균 ÷ √평균(min(일수익률, 0)²). 상승일의 0도 분모에 포함하고, 126일이 기본·252일은 안정성 참고입니다. 분모 0 또는 기간 부족은 —입니다. 총수익률 지표가 아니며, 분배금 재투자는 반영하지 않습니다.</p><p><b>종가 신고가 후보</b>는 당일 종가가 직전 252거래일 최고 종가를 초과한 경우입니다. 같으면 고점 유지 후보입니다. 고점 거리는 최근 253개 종가의 최고값 대비 계산합니다. 최소 253개 종가가 필요하며 기업행위·과거 조정 미검증 값은 확정 신고가로 부르지 않습니다. −3%와 5거래일은 초기 관찰 기준입니다.</p><p><b>순위와 변화</b>는 같은 산식·조건·비교 집합에서 계산합니다. 현재 고정 대상의 양일 자료로 소급 계산한 변화입니다. 비교일의 유효 집합이 현재 집합과 다르면 변화는 —로 보류합니다. 과거 원본 순위 또는 생존편향 없는 백테스트가 아닙니다. 조건진입·조건이탈은 이동평균선 조건 변화입니다. 검색·정렬·대표/보조 표시는 계산 순위를 바꾸지 않습니다.</p><p><b>범위</b>는 ' + esc(coverage) + '입니다. 분류를 마친 적격 ETF를 순위 대상으로 삼고, 같은 노출의 대표 ETF를 기본으로 보여 줍니다. 전체 ETF 목록에는 대표·보조·분류 검토 전·순위 제외 상품을 함께 표시합니다. 전체 ETF 시장의 전수 순위가 아닙니다. 레버리지·인버스·커버드콜·단일종목 ETF와 개별주를 제외하고, 한국 상장 해외주식 ETF를 국내업종으로 분류하지 않습니다.</p><p><b>자료 상태</b>는 완료 거래일과 실제 가격 기준일을 비교합니다. 실패·미갱신·원천 간 가격 불일치 자료는 현재 계산에서 제외합니다. 원본 저장 관측값은 독립 지표와 별도입니다. 원본의 배당조정·목표수익률은 미확인입니다. XSD는 SOXX, URA는 NLR의 보조로 관찰합니다.</p><p>산식 버전 ' + esc(method.version || '미제공') + ' · 대상 버전 ' + esc(universe.version || '미제공') + ' · <a href="' + sourceUrl + '" target="_blank" rel="noopener noreferrer">원본 대시보드</a></p>' + universeNote() + (universe.coverage_note || universe.catalog_note ? '<p>' + esc(universe.coverage_note || universe.catalog_note) + '</p>' : '') + '</div></details>';
  }
  function render() {
    if (!root || !data) return;
    const focus = document.activeElement && document.activeElement.id === 'etf-search' ? {start: document.activeElement.selectionStart, end: document.activeElement.selectionEnd} : null;
    root.innerHTML = header() + tabs() + '<div id="etf-panel" role="tabpanel" aria-labelledby="etf-tab-' + state.tab + '">' + toolbar() + (state.tab === 'strength' ? strength() : state.tab === 'us-high' ? highs('US') : state.tab === 'kr-high' ? highs('KR') : state.tab === 'catalogue' ? catalogueTable() : cross()) + '</div>' + methodology() +
      '<footer class="etf-foot"><span>YM Research · 가격 기반 업종 조사 우선순위</span><span>원자료·계산 기준을 분리해 표시합니다. <a href="etf_data.json" target="_blank" rel="noopener">현재 데이터 ↗</a></span></footer>';
    if (focus) { const input = root.querySelector('#etf-search'); if (input) { input.focus(); try { input.setSelectionRange(focus.start, focus.end); } catch (_) {} } }
  }
  function selectTab(tab) { state.tab = tab; state.query = ''; state.sortKey = tab === 'catalogue' ? 'name' : 'rank'; state.sortDir = 'asc'; render(); }
  function events() {
    root.addEventListener('click', event => {
      const tab = event.target.closest('[data-etf-tab]'), basis = event.target.closest('[data-etf-basis]'), sort = event.target.closest('[data-etf-sort]');
      if (tab) { selectTab(tab.dataset.etfTab); root.querySelector('#etf-tab-' + state.tab).focus(); }
      else if (basis) { state.basis = basis.dataset.etfBasis; render(); }
      else if (sort) { const key = sort.dataset.etfSort; state.sortDir = state.sortKey === key ? state.sortDir === 'asc' ? 'desc' : 'asc' : key === 'name' || key === 'rank' ? 'asc' : 'desc'; state.sortKey = key; render(); }
      else if (event.target.closest('[data-etf-retry]')) load();
      else if (event.target.closest('a[href="#etf-method"]')) { event.preventDefault(); const details = root.querySelector('#etf-method'); details.open = true; details.scrollIntoView({block: 'start', behavior: 'smooth'}); }
    });
    root.addEventListener('keydown', event => {
      const t = event.target.closest('[data-etf-tab]');
      if (!t || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault(); const items = tabItems.map(t => t[0]), i = items.indexOf(state.tab);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : (i + (event.key === 'ArrowRight' ? 1 : items.length - 1)) % items.length;
      selectTab(items[next]); root.querySelector('#etf-tab-' + state.tab).focus();
    });
    root.addEventListener('input', event => { if (event.target.id === 'etf-search') { state.query = event.target.value; render(); } });
    root.addEventListener('change', event => {
      const el = event.target;
      if (el.id === 'etf-ma') state.ma = el.value;
      else if (el.id === 'etf-supplements') state.supplements = el.checked;
      else if (el.id === 'etf-high-filter') state.highFilter = el.value;
      else if (el.id === 'etf-all-rows') state.allRows = el.checked;
      else if (el.id === 'etf-catalogue-market') { state.catalogueMarket = el.value; state.catalogueScope = 'all'; }
      else if (el.id === 'etf-catalogue-scope') state.catalogueScope = el.value;
      else if (el.id === 'etf-catalogue-selection') state.catalogueSelection = el.value;
      else if (el.id === 'etf-catalogue-freshness') state.catalogueFreshness = el.value;
      else return;
      render();
    });
  }
  async function load() {
    if (loading) return; loading = true;
    root.innerHTML = '<div class="etf-loader" role="status">ETF 가격과 업종 신호를 불러오고 있습니다.</div>';
    try {
      const response = await fetch('etf_data.json', {cache: 'no-cache'});
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const loaded = await response.json();
      if (!loaded || !Array.isArray(loaded.rows) || !loaded.expected_dates || !loaded.markets) throw new Error('자료 형식 확인 필요');
      data = loaded; render();
    } catch (error) {
      root.innerHTML = '<div class="etf-head"><div><div class="etf-eyebrow">YM RESEARCH · ETF LEADERSHIP</div><h1>ETF 주도섹터</h1></div></div>' + empty('ETF 자료를 불러오지 못했습니다', '연결 상태를 확인한 뒤 다시 불러올 수 있습니다. 과거 자료를 현재 신호로 표시하지 않습니다.') + '<p class="etf-caption">' + esc(error.message) + '</p><button class="btn" type="button" data-etf-retry>다시 불러오기</button>';
    } finally { loading = false; }
  }
  window.ETFScanner = {mount: function () { if (!root) { root = document.getElementById('view-etf'); events(); } if (!data && !loading) load(); }, reload: load};
})();
