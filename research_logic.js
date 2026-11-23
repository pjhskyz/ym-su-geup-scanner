(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.ResearchLogic = api;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const isNumber = value => typeof value === 'number' && Number.isFinite(value);

  // ---- 표시 형식 ----
  function signed(value, digits = 2, suffix = '%') {
    if (!isNumber(value)) return '—';
    const fixed = value.toFixed(digits);
    return (value > 0 ? '+' : '') + fixed + suffix;
  }
  function direction(value) { return !isNumber(value) || value === 0 ? 'flat' : value > 0 ? 'up' : 'down'; }
  function formatDate(yyyymmdd) {
    const s = String(yyyymmdd || '').replace(/-/g, '');
    return /^\d{8}$/.test(s) ? `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}` : '—';
  }
  function shortDate(yyyymmdd) {
    const s = String(yyyymmdd || '').replace(/-/g, '');
    return /^\d{8}$/.test(s) ? `${s.slice(4, 6)}.${s.slice(6, 8)}` : '';
  }
  function capLabel(eok) {
    if (!isNumber(eok)) return '—';
    return eok >= 10000 ? (eok / 10000).toFixed(eok >= 100000 ? 0 : 1) + '조' : Math.round(eok).toLocaleString('ko-KR') + '억';
  }

  // ---- 시계열 ----
  function lastValid(values) {
    for (let i = (values || []).length - 1; i >= 0; i--) if (isNumber(values[i])) return {index: i, value: values[i]};
    return null;
  }
  function previousValid(values, before) {
    for (let i = before - 1; i >= 0; i--) if (isNumber(values[i])) return {index: i, value: values[i]};
    return null;
  }
  function movingAverage(values, window) {
    return (values || []).map((_, i) => {
      if (i - window + 1 < 0) return null;
      let sum = 0;
      for (let k = i - window + 1; k <= i; k++) { if (!isNumber(values[k])) return null; sum += values[k]; }
      return sum / window;
    });
  }
  function tail(values, count) { return count && values.length > count ? values.slice(values.length - count) : values.slice(); }
  // levels의 마지막 값과 count 거래일 전 값을 비교한 수익률(%). 이력이 모자라면 확보된 첫 값과 비교.
  function periodReturn(levels, count) {
    const last = lastValid(levels);
    if (!last) return null;
    const startIndex = Math.max(0, last.index - count);
    const start = levels[startIndex];
    return isNumber(start) && start > 0 ? {pct: (last.value / start - 1) * 100, from: startIndex, to: last.index} : null;
  }

  // ---- 수출입 ----
  // (현재 − 기준) ÷ |기준| × 100. 기준이 없거나 0이면 계산하지 않는다.
  function pctChange(now, base) { return isNumber(now) && isNumber(base) && base !== 0 ? (now - base) / Math.abs(base) * 100 : null; }
  function formatMonth(yyyymm) { const s = String(yyyymm || ''); return /^\d{6}$/.test(s) ? `${s.slice(0, 4)}-${s.slice(4)}` : '—'; }
  function usdLabel(usd) {
    if (!isNumber(usd)) return '—';
    const million = usd / 1e6;
    return Math.abs(million) >= 100 ? (million / 100).toFixed(2) + '억$' : million.toFixed(1) + '백만$';
  }
  // 품목 시계열(금액 USD·중량 kg) → 최신 관측월 지표. flow: 'exp' | 'imp'
  function tradeMetrics(item, flow) {
    const amount = (item && item[flow]) || [], weight = (item && item[flow + '_wgt']) || [], months = (item && item.months) || [];
    const last = lastValid(amount);
    if (!last) return null;
    const i = last.index, at = k => (k >= 0 ? amount[k] : null);
    const sum = (from, to) => { let total = 0; for (let k = from; k <= to; k++) { if (k < 0 || !isNumber(amount[k])) return null; total += amount[k]; } return total; };
    const unit = k => (k >= 0 && isNumber(amount[k]) && isNumber(weight[k]) && weight[k] > 0 ? amount[k] / weight[k] : null);
    const sum12 = sum(i - 11, i);
    return {index: i, month: months[i], amount: last.value, yoy: pctChange(last.value, at(i - 12)), mom: pctChange(last.value, at(i - 1)),
      sum12, sum12Yoy: pctChange(sum12, sum(i - 23, i - 12)), unit: unit(i), unitYoy: pctChange(unit(i), unit(i - 12))};
  }
  function yoySeries(values) { return (values || []).map((v, i) => pctChange(v, i >= 12 ? values[i - 12] : null)); }
  function unitSeries(amount, weight) { return (amount || []).map((v, i) => (isNumber(v) && isNumber((weight || [])[i]) && weight[i] > 0 ? v / weight[i] : null)); }
  // ---- 수출 잠정치(10일 단위 누계) ----
  const PRELIM_SPANS = ['d10', 'd20', 'm'];
  function prelimSpanLabel(span) { return {d10: '1~10일', d20: '1~20일', m: '월 전체'}[span] || '—'; }
  // 볼 (월, 구간)을 고른다. span이 없으면 수집기가 표시한 최신 구간, 있으면 그 구간 값이 있는 가장 최근 월.
  function prelimPick(prelim, span) {
    const months = (prelim && prelim.months) || [];
    const total = prelim && ((prelim.item && prelim.item[0]) || (prelim.country && prelim.country[0]));
    if (!total || !months.length) return null;
    if (!span) {
      const latest = prelim.latest, index = latest ? months.indexOf(latest.month) : -1;
      return index >= 0 && PRELIM_SPANS.includes(latest.span) && isNumber((total[latest.span] || [])[index]) ? {index, month: latest.month, span: latest.span} : null;
    }
    const last = PRELIM_SPANS.includes(span) ? lastValid(total[span] || []) : null;
    return last ? {index: last.index, month: months[last.index], span} : null;
  }
  // kind: 'item' | 'country'. 같은 구간끼리만 비교한다(전년 동기 = 12개월 전, 전월 동기 = 1개월 전). 비중은 첫 행(전체) 대비.
  function prelimRows(prelim, kind, pick) {
    const rows = (prelim && prelim[kind]) || [];
    if (!pick || !rows.length) return [];
    const at = (row, k) => (k >= 0 ? (row[pick.span] || [])[k] : null), total = at(rows[0], pick.index);
    return rows.map((row, index) => {
      const amount = at(row, pick.index);
      return {index, name: row.name, amount, yoy: pctChange(amount, at(row, pick.index - 12)), mom: pctChange(amount, at(row, pick.index - 1)),
        share: index > 0 && isNumber(amount) && isNumber(total) && total > 0 ? amount / total * 100 : null};
    });
  }
  // 전체에서 한 품목을 뺀 금액의 지표(예: 반도체 제외 수출). 둘 중 하나라도 없으면 계산하지 않는다.
  function prelimExcluding(prelim, pick, name) {
    const rows = (prelim && prelim.item) || [], part = rows.find(row => row.name === name);
    if (!pick || !part || !rows[0]) return null;
    const at = k => { const a = k >= 0 ? (rows[0][pick.span] || [])[k] : null, b = k >= 0 ? (part[pick.span] || [])[k] : null; return isNumber(a) && isNumber(b) ? a - b : null; };
    const amount = at(pick.index);
    return isNumber(amount) ? {amount, yoy: pctChange(amount, at(pick.index - 12)), mom: pctChange(amount, at(pick.index - 1))} : null;
  }
  function prelimSeries(prelim, kind, index, span) {
    const row = ((prelim && prelim[kind]) || [])[index], amount = (row && row[span]) || [];
    return {name: row ? row.name : '', months: (prelim && prelim.months) || [], amount, yoy: yoySeries(amount)};
  }

  // ---- 국가별 ----
  // 국가·지역 한 행(exp·imp 월별 금액) → 최신월 지표와 같은 달 무역수지.
  function nationMetrics(row, months, flow) {
    const m = tradeMetrics({months, exp: row && row.exp, imp: row && row.imp}, flow);
    if (!m) return null;
    const exp = (row.exp || [])[m.index], imp = (row.imp || [])[m.index];
    return {...m, balance: isNumber(exp) && isNumber(imp) ? exp - imp : null};
  }
  // 품목의 국가 분해 → 상위 국가 행 + 그 외 + 전체. 비중은 국가 합계의 최신월 대비.
  function countryBreakdown(countries, flow) {
    const months = (countries && countries.months) || [], total = (countries && countries.total && countries.total[flow]) || [];
    const all = tradeMetrics({months, v: total}, 'v');
    if (!all) return null;
    const share = m => (m && m.index === all.index && all.amount > 0 ? m.amount / all.amount * 100 : null);
    const rows = ((countries && countries[flow]) || []).map(row => { const m = tradeMetrics({months, v: row.values}, 'v'); return {code: row.code, name: row.name, values: row.values || [], m, share: share(m)}; });
    const rest = total.map((v, k) => (isNumber(v) && rows.every(r => isNumber(r.values[k])) ? v - rows.reduce((sum, r) => sum + r.values[k], 0) : null));
    const restM = tradeMetrics({months, v: rest}, 'v');
    return {months, month: all.month, total: {code: 'ALL', name: '전체', values: total, m: all, share: null}, rows, rest: {code: 'REST', name: '그 외 국가', values: rest, m: restM, share: share(restM)}};
  }

  // null은 방향과 무관하게 항상 뒤로 보내는 정렬
  function sortBy(rows, getter, dir) {
    return rows.slice().sort((a, b) => {
      const x = getter(a), y = getter(b), ex = x == null || (typeof x === 'number' && !Number.isFinite(x)), ey = y == null || (typeof y === 'number' && !Number.isFinite(y));
      if (ex || ey) return ex === ey ? 0 : ex ? 1 : -1;
      return (typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y), 'ko')) * dir;
    });
  }

  // ---- 히트맵 ----
  // 타일 면적: 시가총액의 제곱근(대형주 면적 집중 완화).
  function tileWeight(mcap) { return isNumber(mcap) && mcap > 0 ? Math.sqrt(mcap) : 0; }
  function worstRatio(min, max, sum, side) {
    const side2 = side * side, sum2 = sum * sum;
    return Math.max(side2 * max / sum2, sum2 / (side2 * min));
  }
  // Squarified treemap. items: [{weight, ...}] → [{item, x, y, w, h}] (weight 내림차순 배치)
  function treemap(items, width, height) {
    const valid = (items || []).filter(it => isNumber(it.weight) && it.weight > 0).sort((a, b) => b.weight - a.weight);
    const total = valid.reduce((sum, it) => sum + it.weight, 0);
    if (!valid.length || !(width > 0) || !(height > 0)) return [];
    const scale = width * height / total;
    const nodes = valid.map(it => ({item: it, area: it.weight * scale}));
    const out = [];
    let x = 0, y = 0, w = width, h = height, start = 0;
    while (start < nodes.length) {
      const side = Math.min(w, h);
      let end = start + 1, sum = nodes[start].area, min = sum, max = sum, worst = worstRatio(min, max, sum, side);
      while (end < nodes.length) {
        const area = nodes[end].area, nextSum = sum + area, nextMin = Math.min(min, area), nextMax = Math.max(max, area);
        const next = worstRatio(nextMin, nextMax, nextSum, side);
        if (next > worst) break;
        sum = nextSum; min = nextMin; max = nextMax; worst = next; end++;
      }
      if (w >= h) {
        const rowWidth = sum / h; let cy = y;
        for (let i = start; i < end; i++) { const rh = nodes[i].area / rowWidth; out.push({item: nodes[i].item, x, y: cy, w: rowWidth, h: rh}); cy += rh; }
        x += rowWidth; w -= rowWidth;
      } else {
        const rowHeight = sum / w; let cx = x;
        for (let i = start; i < end; i++) { const rw = nodes[i].area / rowHeight; out.push({item: nodes[i].item, x: cx, y, w: rw, h: rowHeight}); cx += rw; }
        y += rowHeight; h -= rowHeight;
      }
      start = end;
    }
    return out;
  }
  // 등락률 → 색. 한국식(상승 빨강·하락 파랑), ±limit%에서 포화.
  function heatColor(pct, limit = 3) {
    if (!isNumber(pct)) return 'rgb(42,50,62)';
    const t = Math.max(-1, Math.min(1, pct / limit)), k = Math.abs(t);
    const base = [34, 41, 52], target = t > 0 ? [214, 48, 49] : [41, 98, 204];
    return 'rgb(' + base.map((b, i) => Math.round(b + (target[i] - b) * (0.18 + 0.82 * k) * (t === 0 ? 0 : 1))).join(',') + ')';
  }
  function heatmapRows(heatmap) {
    const cols = (heatmap && heatmap.columns) || [];
    return ((heatmap && heatmap.rows) || []).map(row => { const o = {}; cols.forEach((c, i) => { o[c] = row[i]; }); return o; });
  }
  // ---- 히트맵 시장 전환 (코스피/코스닥) ----
  const MARKET_LABELS = {KOSPI: '코스피', KOSDAQ: '코스닥'};
  const marketLabel = market => MARKET_LABELS[market] || '전체';
  // 시장별 종목 수. 시장 칸이 하나도 채워지지 않은 자료(예전 수집본)면 available 이 false 다.
  function marketCounts(rows) {
    const counts = {KOSPI: 0, KOSDAQ: 0, unknown: 0};
    (rows || []).forEach(r => { if (r && MARKET_LABELS[r.market]) counts[r.market]++; else counts.unknown++; });
    return {...counts, available: counts.KOSPI + counts.KOSDAQ > 0};
  }
  // 'all' 이면 전부, 아니면 그 시장 종목만. 시장을 모르는 종목은 전체에서만 보인다.
  function marketRows(rows, market) {
    return MARKET_LABELS[market] ? (rows || []).filter(r => r && r.market === market) : (rows || []);
  }
  function moveCounts(rows, key) {
    const counts = {up: 0, down: 0, flat: 0, missing: 0};
    (rows || []).forEach(r => { const v = r[key]; if (!isNumber(v)) counts.missing++; else if (v > 0) counts.up++; else if (v < 0) counts.down++; else counts.flat++; });
    return counts;
  }

  // ---- 시장의 흐름: 규칙 기반 읽기 (YM 자체 기준) ----
  function marketReading(breadth) {
    const ratio = lastValid((breadth && breadth.up_sector_ratio) || []);
    if (!ratio) return {label: '판단 보류', tone: 'flat', notes: ['업종 수익률을 계산할 수 있는 자료가 없습니다.']};
    const osc = lastValid(breadth.oscillator || []), stock = lastValid(breadth.up_stock_ratio || []);
    const notes = [];
    let label, tone;
    if (ratio.value >= 70) { label = '시장 전반 강세'; tone = 'up'; notes.push('상승 업종이 70% 이상입니다.'); }
    else if (ratio.value <= 30) { label = '시장 전반 약세'; tone = 'down'; notes.push('상승 업종이 30% 이하입니다.'); }
    else { label = '업종별 차별화'; tone = 'flat'; notes.push('상승 업종과 하락 업종이 섞여 있습니다.'); }
    if (stock && Math.abs(stock.value - ratio.value) >= 25) notes.push(stock.value < ratio.value ? '업종 지수보다 개별 종목의 상승 폭이 좁습니다(대형주 주도).' : '업종 지수보다 개별 종목의 상승 폭이 넓습니다(중소형주 확산).');
    if (osc) notes.push(osc.value > 0 ? '상위 업종으로 쏠림이 커지는 중입니다.' : osc.value < 0 ? '상위 업종 쏠림이 완화되는 중입니다.' : '쏠림 변화가 없습니다.');
    return {label, tone, notes};
  }

  // ---- ETF 주도·반등 후보 (YM 자체 점수: 벤치마크 대비 1·3·6개월 가격 상대강도 평균) ----
  function leaderScore(row) {
    const parts = [row.rs_price_21, row.rs_price_63, row.rs_price_126].filter(isNumber);
    return parts.length === 3 ? parts.reduce((a, b) => a + b, 0) / 3 : null;
  }
  function etfCandidates(data, market) {
    const expected = data && data.expected_dates && data.expected_dates[market];
    return ((data && data.rows) || []).filter(r => r.market === market && r.rank_enabled !== false && r.representative !== false && r.representative !== 'supplemental'
      && !/style|factor/.test(r.kind || '') && r.status === 'fresh' && (!expected || r.date === expected));
  }
  // 같은 group(섹터·테마)에서는 점수가 가장 높은 ETF 하나만 남긴다.
  function bestPerGroup(rows, score) {
    const best = new Map();
    rows.forEach(r => { const s = score(r); if (!isNumber(s)) return; const cur = best.get(r.group); if (!cur || s > cur.score) best.set(r.group, {row: r, score: s}); });
    return [...best.values()].sort((a, b) => b.score - a.score);
  }
  function etfLeaders(data, market, count = 5) {
    return bestPerGroup(etfCandidates(data, market), leaderScore).slice(0, count);
  }
  // 반등 관찰: 6개월 상대강도는 음수(약세)인데 1개월 상대강도가 3개월보다 개선된 섹터. 점수 = 개선폭.
  function etfRebounds(data, market, count = 5) {
    const improving = r => isNumber(r.rs_price_126) && r.rs_price_126 < 0 && isNumber(r.rs_price_21) && isNumber(r.rs_price_63)
      ? r.rs_price_21 - r.rs_price_63 : null;
    return bestPerGroup(etfCandidates(data, market), improving).filter(x => x.score > 0).slice(0, count);
  }

  // ---- Yahoo 차트 응답(range=1mo, interval=1d) → 시세 요약 ----
  function quoteFromYahoo(result, options = {}) {
    if (!result || !result.meta) return null;
    const stamps = result.timestamp || [];
    const raw = (result.indicators && result.indicators.quote && result.indicators.quote[0] && result.indicators.quote[0].close) || [];
    const offset = result.meta.gmtoffset || 0;
    const dayOf = seconds => Math.floor((seconds + offset) / 86400);
    const bars = [];
    stamps.forEach((t, i) => { if (isNumber(raw[i])) bars.push({day: dayOf(t), close: raw[i]}); });
    let price = isNumber(result.meta.regularMarketPrice) ? result.meta.regularMarketPrice : (bars.length ? bars[bars.length - 1].close : null);
    if (!isNumber(price)) return null;
    const marketDay = isNumber(result.meta.regularMarketTime) ? dayOf(result.meta.regularMarketTime) : (bars.length ? bars[bars.length - 1].day : null);
    const prior = bars.filter(b => marketDay == null || b.day < marketDay);
    let previous = prior.length ? prior[prior.length - 1].close : null;
    let closes = bars.map(b => b.close);
    if (bars.length && marketDay != null && bars[bars.length - 1].day === marketDay) closes[closes.length - 1] = price; else closes.push(price);
    const divisor = options.divisor || 1;
    if (divisor !== 1) { price /= divisor; if (isNumber(previous)) previous /= divisor; closes = closes.map(v => v / divisor); }
    const multiplier = options.multiplier || 1;
    if (multiplier !== 1) { price *= multiplier; if (isNumber(previous)) previous *= multiplier; closes = closes.map(v => v * multiplier); }
    return {
      price, previous, closes: closes.slice(-30), time: result.meta.regularMarketTime || null,
      changePct: isNumber(previous) && previous !== 0 ? (price / previous - 1) * 100 : null,
      change: isNumber(previous) ? price - previous : null,
    };
  }

  return {
    isNumber, signed, direction, formatDate, shortDate, capLabel,
    lastValid, previousValid, movingAverage, tail, periodReturn,
    pctChange, formatMonth, usdLabel, tradeMetrics, yoySeries, unitSeries, sortBy,
    prelimSpanLabel, prelimPick, prelimRows, prelimExcluding, prelimSeries, nationMetrics, countryBreakdown,
    tileWeight, treemap, heatColor, heatmapRows, moveCounts, marketLabel, marketCounts, marketRows,
    marketReading, leaderScore, etfCandidates, etfLeaders, etfRebounds, quoteFromYahoo,
  };
});
