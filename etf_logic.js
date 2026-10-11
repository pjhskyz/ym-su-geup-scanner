(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.ETFLogic = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const highStates = {
    new_close_high_candidate: {label: '신고가 후보', tone: 'new', order: 1},
    high_held_candidate: {label: '고점 유지 후보', tone: 'held', order: 2},
    near_close_high_candidate: {label: '고점 근접 후보', tone: 'near', order: 3},
    weakened_after_high_candidate: {label: '신고가 후 약화 후보', tone: 'weak', order: 4},
    new_close_high: {label: '종가 신고가', tone: 'new', order: 1},
    high_held: {label: '고점 유지', tone: 'held', order: 2},
    near_close_high: {label: '종가 고점 근접', tone: 'near', order: 3},
    weakened_after_high: {label: '신고가 후 약화', tone: 'weak', order: 4},
    far_from_high: {label: '고점에서 이격', tone: 'muted', order: 5},
    insufficient_253_closes: {label: '기간 부족', tone: 'muted', order: 6},
    unavailable: {label: '확인 불가', tone: 'muted', order: 7}
  };
  const number = value => typeof value === 'number' && Number.isFinite(value);
  function isCurrent(row, data) {
    const expected = data.expected_dates && data.expected_dates[row.market];
    return row.status === 'fresh' && !!expected && row.date === expected && row.quarantined !== true;
  }
  function status(row, data) {
    if (!isCurrent(row, data)) return {label: row.status === 'quarantined' ? '검증 보류' : '현재 자료 없음', tone: 'muted', order: 9};
    return highStates[row.high && row.high.state] || highStates.unavailable;
  }
  function rank(row, ma) { return (row.ranks && row.ranks['sortino' + ma]) || {}; }
  function isRepresentative(row) { return row.representative !== false && row.representative !== 'supplemental'; }
  function matches(row, query) { return !query || [row.name, row.symbol, row.id, row.group].join(' ').toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()); }
  function sortRows(rows, key, direction, getter) {
    return rows.slice().sort((a, b) => {
      const x = getter ? getter(a, key) : a[key], y = getter ? getter(b, key) : b[key];
      const emptyX = x == null || (typeof x === 'number' && !Number.isFinite(x));
      const emptyY = y == null || (typeof y === 'number' && !Number.isFinite(y));
      if (emptyX || emptyY) return emptyX === emptyY ? String(a.name || '').localeCompare(String(b.name || ''), 'ko') : emptyX ? 1 : -1;
      const compared = typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y), 'ko');
      return compared * (direction === 'desc' ? -1 : 1) || String(a.name || '').localeCompare(String(b.name || ''), 'ko');
    });
  }
  function counts(data) {
    const rows = (data.rows || []).filter(r => isCurrent(r, data) && isRepresentative(r));
    return {
      KR: rows.filter(r => r.market === 'KR' && status(r, data).tone === 'new').length,
      US: rows.filter(r => r.market === 'US' && status(r, data).tone === 'new').length,
      near: rows.filter(r => status(r, data).tone === 'near').length,
      weak: rows.filter(r => status(r, data).tone === 'weak').length,
      ma20: rows.filter(r => r.market === 'KR' && r.above_ma20 === true && number(r.sortino_price_126)).length,
      KR_high_usable: rows.filter(r => r.market === 'KR' && r.high && number(r.high.gap) && !['insufficient_253_closes', 'unavailable'].includes(r.high.state)).length,
      US_high_usable: rows.filter(r => r.market === 'US' && r.high && number(r.high.gap) && !['insufficient_253_closes', 'unavailable'].includes(r.high.state)).length,
      weak_usable: rows.filter(r => r.high && r.high.weak_window_complete === true).length,
      usable: rows.length
    };
  }
  function deltaLabel(value) {
    if (value === 'entry') return '조건진입';
    if (value === 'exit') return '조건이탈';
    if (!number(value)) return '—';
    return value > 0 ? '↑' + value : value < 0 ? '↓' + Math.abs(value) : '0';
  }
  function rankComparison(ranking, period) {
    const key = 'delta_' + period, comparison = ranking.comparisons && (ranking.comparisons[String(period)] || ranking.comparisons[key] || ranking.comparisons['d' + period]);
    return {value: ranking[key], comparison: comparison || null};
  }
  const families = [
    {name: '반도체·AI 하드웨어', US: ['SOXX'], KR: ['091160', '475300', '475310', '471760'], scope: 'US', relation: '부분 관련 · 전공정·후공정 포함 비중 차이'},
    {name: '네트워크·통신장비', US: ['XTL'], KR: ['367760'], scope: 'US', relation: '부분 관련 · 통신서비스 포함 비중 차이'},
    {name: '바이오', US: ['XBI'], KR: ['364970'], scope: 'US', relation: '부분 관련 · 산업 구성 차이'},
    {name: '방산·항공우주', US: ['XAR'], KR: ['463250', '421320'], scope: 'US', relation: '부분 관련 · 항공우주 포함 비중 차이'},
    {name: '의료기기', US: ['XHE'], KR: ['307510'], scope: 'US', relation: '같은 업종 · 국가별 구성 차이'},
    {name: '은행', US: ['KBE', 'KRE'], KR: ['091170'], scope: 'US', relation: '부분 관련 · 지역은행 포함 비중 차이'},
    {name: '보험', US: ['KIE'], KR: ['140700'], scope: 'US', relation: '같은 업종 · 국가별 구성 차이'},
    {name: '에너지·화학', US: ['XLE', 'XOP', 'XES'], KR: ['117460'], scope: 'US', relation: '부분 관련 · 화학과 석유·가스 노출 차이'},
    {name: '전력망·전력설비', US: ['GRID'], KR: ['487240'], scope: 'GLOBAL', relation: '부분 관련 · 글로벌 전력망과 한국 설비'},
    {name: '신재생·태양광', US: ['TAN', 'ICLN'], KR: ['457990', '381570'], scope: 'GLOBAL', relation: '부분 관련 · ESS 포함 비중 차이'},
    {name: '로봇·자동화', US: ['ROBO'], KR: ['445290'], scope: 'GLOBAL', relation: '부분 관련 · 글로벌과 국내 종목 구성 차이'},
    {name: '원자력', US: ['NLR'], KR: ['433500'], scope: 'GLOBAL', relation: '부분 관련 · 우라늄·발전과 설비 구성 차이'},
    {name: '배터리·2차전지', US: ['LIT'], KR: ['305540'], scope: 'GLOBAL', relation: '부분 관련 · 리튬 광산 포함 비중 차이'}
  ];
  function signal(row, data) {
    if (!isCurrent(row, data)) return null;
    if (number(row.rs_price_126) && row.rs_price_126 > 0) return 'rs';
    const state = row.high && row.high.state;
    if (state === 'new_close_high' || state === 'high_held') return 'high';
    if (state === 'new_close_high_candidate' || state === 'high_held_candidate') return 'candidate';
    return null;
  }
  function crossCountry(data) {
    const reps = (data.rows || []).filter(isRepresentative);
    return families.map(f => {
      const related = reps.filter(r => f[r.market] && f[r.market].includes(r.symbol));
      const sides = {};
      ['KR', 'US'].forEach(market => {
        const registered = related.filter(r => r.market === market), fresh = registered.filter(r => isCurrent(r, data));
        const strong = fresh.filter(r => signal(r, data));
        const confirmed = strong.filter(r => signal(r, data) !== 'candidate');
        const complete = registered.length > 0 && fresh.length === registered.length && fresh.every(r => number(r.rs_price_126) || signal(r, data) === 'high');
        sides[market] = {registered, fresh, strong, confirmed, complete};
      });
      const abroad = f.scope === 'GLOBAL' ? '글로벌' : '미국', both = f.scope === 'GLOBAL' ? '글로벌·한국' : '양국';
      let label = both + ' 강세 미확인', tone = 'muted';
      if (sides.KR.confirmed.length && sides.US.confirmed.length) {label = both + ' 동시 확인'; tone = 'held';}
      else if (sides.KR.strong.length && sides.US.strong.length) {label = both + ' 강세 후보'; tone = 'near';}
      else if (sides.US.strong.length) {label = sides.KR.complete ? abroad + '에서만 관측' : abroad + ' 강세·한국 비교 제한'; tone = 'near';}
      else if (sides.KR.strong.length) {label = sides.US.complete ? '한국에서만 관측' : '한국 강세·' + abroad + ' 비교 제한'; tone = 'near';}
      else if (!sides.KR.fresh.length || !sides.US.fresh.length) label = !sides.KR.fresh.length && !sides.US.fresh.length ? '양쪽 자료 부족' : !sides.KR.fresh.length ? '한국 자료 부족' : abroad + ' 자료 부족';
      else if (!sides.KR.complete || !sides.US.complete) label = '시장 대비 강세 비교 제한';
      return {name: f.name, relation: f.relation, scope: f.scope, sides, label, tone};
    });
  }
  return {number, isCurrent, status, rank, matches, isRepresentative, sortRows, counts, deltaLabel, rankComparison, crossCountry, signal};
});
