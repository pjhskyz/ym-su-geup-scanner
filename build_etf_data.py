#!/usr/bin/env python3
"""Collect public ETF prices and export a conservative, browser-ready snapshot.

This is an independent PRICE screen, not the reference dashboard's formula and
not a fund-flow measure. Network failures cannot promote a cache to current data.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics

from etf_pipeline import monitor
from etf_pipeline.issuer_sources import collect_quotes

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'etf_pipeline'
VERSION = 'etf-scanner-price-v1'
OFFSETS = (1, 5, 20)


def read_json(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def calendar_sessions(now=None, overrides=None):
    """Use exchange schedules, including holidays/early closes. Never weekday-guess."""
    import exchange_calendars as xc
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('now_requires_timezone')
    overrides = overrides or {}
    result, expected = {}, {}
    adjustments = read_json(DATA / 'calendar_overrides.json', {'closures': []})
    for market, name in [('KR', 'XKRX'), ('US', 'XNYS')]:
        start = (now - timedelta(days=1500)).date().isoformat()
        end = (now + timedelta(days=14)).date().isoformat()
        calendar = xc.get_calendar(name, start=start, end=end)
        complete = calendar.schedule[calendar.schedule['close'] <= now]
        excluded = {x['date'] for x in adjustments['closures'] if x['market'] == market}
        valid = [d.date().isoformat() for d in complete.index if d.date().isoformat() not in excluded]
        latest = valid[-1]
        target = overrides.get(market) or latest
        if target not in valid:
            raise ValueError(f'{market}:asof_is_not_completed_session:{target}')
        result[market] = [d for d in valid if d <= target]
        expected[market] = target
    return expected, result, {'source': 'exchange_calendars', 'version': xc.__version__,
                              'calendars': {'KR': 'XKRX', 'US': 'XNYS'},
                              'evaluated_at': now.isoformat(), 'explicit_asof': bool(overrides),
                              'verified_closure_overrides': adjustments['closures']}


def window_values(series, dates, n):
    """Require every scheduled session. Missing days are never compressed/interpolated."""
    if len(dates) < n:
        return None
    index = {r['date']: r['close'] for r in series['rows']}
    wanted = dates[-n:]
    if not all(d in index for d in wanted):
        return None
    values = [index[d] for d in wanted]
    if any(not math.isfinite(v) or v <= 0 for v in values):
        return None
    if any(abs(y / x - 1) > .35 for x, y in zip(values, values[1:])):
        return None
    return values


def high_state(series, dates):
    p = window_values(series, dates, 253)
    if p is None:
        return {'state': 'insufficient_253_closes', 'gap': None, 'reference_high': None,
                'peak_date': None, 'breakout_pct': None,
                'reason': '253_complete_daily_closes_or_action_review_required'}
    previous = max(p[:-1])
    peak = max(p)
    # gap and reference_high always refer to the SAME peak; prior high is separate.
    gap = p[-1] / peak - 1
    peak_date = dates[-253:][max(i for i, v in enumerate(p) if v == peak)]
    if p[-1] > previous and not math.isclose(p[-1], previous, rel_tol=1e-10):
        state = 'new_close_high_candidate'
    elif math.isclose(p[-1], previous, rel_tol=1e-10):
        state = 'high_held_candidate'
    elif gap >= -.03 - 1e-12:
        state = 'near_close_high_candidate'
    else:
        state = 'far_from_high'
    events = []
    for offset in range(5):
        ds = dates if offset == 0 else dates[:-offset]
        event = window_values(series, ds, 253)
        if event and event[-1] > max(event[:-1]):
            events.append({'date': ds[-1], 'close': event[-1]})
    if events:
        event = max(events, key=lambda r: (r['close'], r['date']))
        if p[-1] / event['close'] - 1 <= -.03 + 1e-12:
            state = 'weakened_after_high_candidate'
            # For weakened state, the reported distance is from the recent event.
            peak, peak_date = event['close'], event['date']
            gap = p[-1] / peak - 1
    return {'state': state, 'gap': gap, 'reference_high': peak, 'peak_date': peak_date,
            'prior_252_high': previous, 'breakout_pct': p[-1] / previous - 1,
            'weak_window_complete': window_values(series, dates, 257) is not None,
            'recent_high_events': events, 'reason': 'price_adjustment_not_fully_verified'}


def metrics(series, benchmark, dates):
    """Sortino/MA/high remain useful when a fresh benchmark is unavailable."""
    result = {}
    reasons = {}
    for n in (21, 63, 126):
        p = window_values(series, dates, n + 1)
        q = window_values(benchmark, dates, n + 1) if benchmark else None
        compatible = benchmark and series['basis'] == benchmark['basis'] and series['source'] == benchmark['source']
        result[f'rs_price_{n}'] = p[-1] / p[0] - q[-1] / q[0] if p and q and compatible else None
        if result[f'rs_price_{n}'] is None:
            reasons[f'rs_price_{n}'] = ('benchmark_missing_or_not_current' if not benchmark else
                                      'basis_mismatch' if not compatible else 'incomplete_session_window')
    for n in (126, 252):
        p = window_values(series, dates, n + 1)
        result[f'sortino_price_{n}'] = monitor.sortino(p, n) if p else None
        if result[f'sortino_price_{n}'] is None:
            reasons[f'sortino_price_{n}'] = 'zero_downside_deviation' if p else 'incomplete_session_window_or_action_review'
    for n in (10, 20, 50):
        p = window_values(series, dates, n)
        result[f'above_ma{n}'] = p[-1] >= statistics.fmean(p) if p else None
    result['close'] = next((r['close'] for r in reversed(series['rows']) if r['date'] == dates[-1]), None)
    result['high'] = high_state(series, dates)
    result['metric_reasons'] = reasons
    return result


def empty_metrics(reason):
    result = {f'rs_price_{n}': None for n in (21, 63, 126)}
    result.update({f'sortino_price_{n}': None for n in (126, 252)})
    result.update({f'above_ma{n}': None for n in (10, 20, 50)})
    result.update(close=None, high={'state': 'unavailable', 'gap': None, 'reference_high': None,
                                  'peak_date': None, 'breakout_pct': None, 'reason': reason},
                  metric_reasons={key: reason for key in result})
    return result


def current_series(asset, expected, statuses, quotes, config, data_dir):
    status = statuses.get(asset['id'], {})
    source = read_json(data_dir / 'data/series' / (asset['id'].replace(':', '_') + '.json'))
    cached_date = source.get('asof') if source else None
    if quotes.get(asset['id'], {}).get('comparison', {}).get('status') == 'mismatch':
        return None, 'quarantined', 'issuer_close_mismatch', cached_date
    if status.get('status') != 'fresh':
        return None, status.get('status', 'missing'), status.get('error', 'collection_not_current'), cached_date
    if status.get('asof') != expected or not source or source.get('asof') != expected:
        return None, 'stale', 'price_date_not_expected_session', cached_date
    try:
        source = monitor.apply_verified_splits(source, config.get('verified_split_events', []))
        valid = monitor.validate(source['rows'], expected, 1.01 if asset['id'].startswith('KR:') else .02)
        source['rows'] = valid
    except (ValueError, KeyError) as exc:
        return None, 'invalid', str(exc), cached_date
    return source, 'fresh', None, cached_date


def rank_scope(row):
    return 'KR:ETF' if row['market'] == 'KR' else row['scope'] + ':' + row['kind']


def assign_ranks(rows, history, dates):
    specs = {'sortino20': ('sortino_price_126', 'above_ma20'),
             'sortino10': ('sortino_price_126', 'above_ma10'),
             'sortino50': ('sortino_price_126', 'above_ma50'), 'rs126': ('rs_price_126', None)}
    for row in rows:
        row['ranks'] = {}
    for scope in sorted({rank_scope(r) for r in rows}):
        group = [r for r in rows if rank_scope(r) == scope]
        market = group[0]['market']
        for name, (field, condition) in specs.items():
            valid = {r['id'] for r in group if r.get(field) is not None and
                     (condition is None or r.get(condition) is not None)}
            eligible = [r for r in group if r['id'] in valid and (condition is None or r[condition])]
            ranks = monitor.rank(eligible, field)
            for row in group:
                row['ranks'][name] = {'rank': ranks.get(row['id']), 'cohort_count': len(eligible),
                                     'valid_count': len(valid), 'scope': scope, 'dates': {'0': dates[market][-1]},
                                     'comparisons': {}, 'delta_1': None, 'delta_5': None, 'delta_20': None}
            for offset in OFFSETS:
                previous = history.get(market, {}).get(offset, {})
                pair = {aid for aid in valid if previous.get(aid, {}).get(field) is not None and
                        (condition is None or previous[aid].get(condition) is not None)}
                old = monitor.rank([previous[i] for i in pair if condition is None or previous[i][condition]], field)
                new = monitor.rank([r for r in group if r['id'] in pair and (condition is None or r[condition])], field)
                date = dates[market][-offset - 1] if len(dates[market]) > offset else None
                comparable_to_current = pair == valid
                for row in group:
                    rank = row['ranks'][name]
                    rank['dates'][str(offset)] = date
                    aid = row['id']
                    delta = None
                    if aid in pair:
                        delta = old[aid] - new[aid] if aid in old and aid in new else ('entry' if aid in new else 'exit' if aid in old else None)
                    rank['comparisons'][str(offset)] = {
                        'date': date, 'current_rank': new.get(aid), 'previous_rank': old.get(aid),
                        'cohort_count': len(pair), 'current_eligible': len(new), 'previous_eligible': len(old),
                        'matches_current_cohort': comparable_to_current,
                        'reason': None if comparable_to_current else 'historical_valid_cohort_differs',
                        'delta': delta, 'cohort_hash': hashlib.sha256('|'.join(sorted(pair)).encode()).hexdigest()[:12]}
                    if comparable_to_current:
                        rank['delta_' + str(offset)] = delta


def original_snapshot(config, data_dir):
    observed = read_json(data_dir / 'original_snapshot.json')
    if not observed:
        return {'status': 'unavailable', 'rows': []}
    identities = {a['name']: a for a in config['assets'] if a['market'] == 'KR'}
    ranks = {r['name']: r['rank'] for r in observed.get('sortino_20ma_etf_observed', [])}
    rows = []
    rs_values = {value['name']: value for value in observed.get('rs_6m_vs_kospi', [])}
    for name in dict.fromkeys(list(rs_values) + list(ranks)):
        value = rs_values.get(name, {})
        a = identities.get(name)
        if a:
            rows.append({k: a[k] for k in ('id', 'symbol', 'name', 'kind', 'group', 'representative')} | {
                'rs_6m': value['rs_pct_point'] / 100 if 'rs_pct_point' in value else None,
                'return_6m': value['return_6m_pct'] / 100 if 'return_6m_pct' in value else None,
                'sortino20_rank': ranks.get(a['name']), 'sortino10_rank': None,
                'sortino50_rank': None, 'sortino_value': None})
    return {'status': 'saved_observation', 'date': observed['data_date'],
            'source_url': observed['source'], 'observed_at_kst': observed['observed_at_kst'],
            'rank_basis': '관측 ETF 내 순위 (개별주 제외)',
            'method': '원본 산식 · 배당조정 및 목표수익률 확인 불가',
            'note': '저장된 부분 관측이며 자동 갱신하지 않습니다. 자체 순위와 연결하지 않습니다.', 'rows': rows}


def export(config, expected, sessions, calendar, *, saved=False, data_dir=DATA):
    run = read_json(data_dir / 'output/status.json', {})
    statuses = {r['id']: r for r in run.get('status', [])}
    issuer = read_json(data_dir / 'output/issuer_status.json', {})
    quotes = {r['id']: r for r in issuer.get('quotes', [])}
    # Preserve unresolved mismatches in retained issuer observations as well.
    for path in (data_dir / 'data/issuer_quotes').glob('*.json'):
        quote = read_json(path)
        current = quotes.get(quote['id'], {})
        resolved = (current.get('comparison', {}).get('status') == 'match' and
                    current.get('date', '') >= quote.get('date', ''))
        if quote.get('comparison', {}).get('status') == 'mismatch' and not resolved:
            quotes[quote['id']] = quote
    rows, markets, history = [], {}, {}
    for market in ('KR', 'US'):
        assets = [a for a in config['assets'] if a['market'] == market]
        benchmark, bs, br, bd = current_series(config['benchmarks'][market], expected[market], statuses, quotes, config, data_dir)
        history[market] = {off: {} for off in OFFSETS}
        usable = high_usable = 0
        for asset in [a for a in assets if a['rank_enabled']]:
            series, state, reason, cached = current_series(asset, expected[market], statuses, quotes, config, data_dir)
            row = {key: asset[key] for key in ('id', 'symbol', 'name', 'market', 'scope', 'kind', 'group', 'representative', 'rank_enabled')}
            row.update(date=series['asof'] if series else None, status=state, reason=reason,
                       cached_date=cached, source=series.get('source') if series else 'NAVER',
                       basis=series.get('basis') if series else 'provider_price_adjustment_unverified',
                       source_url=asset.get('identity_url') or ('https://finance.naver.com/item/main.naver?code=' + asset['symbol']),
                       source_retrieved_at=series.get('source_retrieved_at') if series else None,
                       adjustment_verified=False, total_return_verified=False,
                       issuer_status=quotes.get(asset['id'], {}).get('status', 'not_available'))
            row.update(metrics(series, benchmark, sessions[market]) if series else empty_metrics(reason))
            if row['sortino_price_126'] is not None:
                usable += 1
            if row['high']['state'] not in ('unavailable', 'insufficient_253_closes'):
                high_usable += 1
            if series:
                for offset in OFFSETS:
                    if len(sessions[market]) > offset:
                        ds = sessions[market][:-offset]
                        value = metrics(series, benchmark, ds)
                        history[market][offset][asset['id']] = value | {'id': asset['id']}
            rows.append(row)
        fresh = sum(statuses.get(a['id'], {}).get('status') == 'fresh' and
                    statuses.get(a['id'], {}).get('asof') == expected[market] for a in assets)
        markets[market] = {'registered': len(assets), 'fresh': fresh,
                           'rank_enabled': sum(a['rank_enabled'] for a in assets),
                           'usable': usable, 'high_usable': high_usable,
                           'asof': expected[market], 'benchmark_status': bs,
                           'benchmark_reason': br, 'benchmark_cached_date': bd,
                           'benchmark': config['benchmarks'][market]['symbol'],
                           'unclassified_watchlist': sum(not a['rank_enabled'] for a in assets)}
    assign_ranks(rows, history, sessions)
    return {'schema_version': 1, 'generated_at': datetime.now(timezone.utc).isoformat(),
            'collection_mode': 'saved_data_analysis' if saved else 'live_collection',
            'last_collection_at': run.get('retrieved_at'),
            'method': {'version': VERSION, 'label': '자체 계산 · 공급자 가격 기반 · 원본 산식과 다름',
                       'sortino': 'sqrt(252)*mean(r)/sqrt(mean(min(r,0)^2)); target=0; n=126/252',
                       'rs': 'ETF minus KOSPI/SPY 21/63/126-session price return',
                       'rank_history': '현재 고정 유니버스 소급 계산; 같은 유효 비교집합에서만 변화 표시',
                       'high': '253개 연속 거래일 종가; 조정 미검증 후보; 근접3%/최근5일약화3%',
                       'notes': ['배당 포함 총수익률 아님', '가격 강세는 실제 자금 유입의 직접 증거가 아님',
                                 '한국 순위는 관측 ETF 순위이며 업종 순위가 아님']},
            'universe': {'version': config['version'],
                         'registered': {m: sum(a['market'] == m for a in config['assets']) for m in ('KR', 'US')},
                         'rank_enabled': {m: sum(a['market'] == m and a['rank_enabled'] for a in config['assets']) for m in ('KR', 'US')}},
            'expected_dates': expected, 'calendar': calendar, 'markets': markets, 'rows': rows,
            'original': original_snapshot(config, data_dir),
            'issuer_checks': {'attempted': len(quotes),
                              'fresh': sum(q.get('status') == 'fresh' and q.get('date') == expected.get(q['id'][:2]) for q in quotes.values()),
                              'quarantined_ids': [i for i, q in quotes.items() if q.get('comparison', {}).get('status') == 'mismatch']}}


def collect(config, expected):
    monitor.ROOT = DATA
    fetcher = monitor.Fetcher(DATA / 'data/raw')
    # Benchmarks first, then ranking assets, then unreviewed watchlist.
    assets = list(config['benchmarks'].values()) + sorted(config['assets'], key=lambda a: not a['rank_enabled'])
    statuses = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(monitor.collect_one, a, expected[a['id'][:2]], fetcher) for a in assets]
        for future in as_completed(futures):
            status = future.result()
            statuses.append(status)
            print(json.dumps(status, ensure_ascii=False), flush=True)
    run = {'retrieved_at': datetime.now(timezone.utc).isoformat(), 'expected_dates': expected,
           'replay': False, 'status': statuses}
    monitor.dump(DATA / 'output/status.json', run)
    collect_quotes(config, fetcher, DATA, expected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--asof-kr')
    parser.add_argument('--asof-us')
    parser.add_argument('--saved', action='store_true', help='Analyse recorded status and saved prices, not a fresh collection')
    parser.add_argument('--output', default=str(ROOT / 'etf_data.json'))
    args = parser.parse_args()
    overrides = {k: v for k, v in {'KR': args.asof_kr, 'US': args.asof_us}.items() if v}
    expected, sessions, calendar = calendar_sessions(overrides=overrides)
    config = read_json(DATA / 'universe.json')
    if not args.saved:
        collect(config, expected)
    result = export(config, expected, sessions, calendar, saved=args.saved)
    monitor.dump(Path(args.output), result)
    print(json.dumps({'output': args.output, 'expected_dates': expected, 'markets': result['markets']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
