#!/usr/bin/env python3
"""Collect dated domestic ETF constituents, preserve history, and publish changes.

This is portfolio/basket weight observation, never an estimate of fund purchases.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import time
from urllib.error import HTTPError

from build_etf_data import calendar_sessions
from holdings_pipeline.logic import compare_snapshots

ROOT = Path(__file__).resolve().parent
PIPE = ROOT / 'holdings_pipeline'
HISTORY = PIPE / 'history'
SNAPSHOT_KEYS = ('etf_id', 'date', 'date_kind', 'basis', 'weight_basis', 'source',
                 'source_url', 'observed_at', 'collection_mode', 'complete',
                 'reported_count', 'creation_unit_shares', 'quantity_basis',
                 'holdings', 'source_sha256', 'date_note', 'quantity_note')
HOLDING_KEYS = ('code', 'isin', 'name', 'asset_type', 'weight_pct', 'quantity')


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n')
    temp.replace(path)


def canonical(snapshot):
    """Keep only public source evidence; never export archive paths or HTTP metadata."""
    s = {k: snapshot.get(k) for k in SNAPSHOT_KEYS if k in snapshot}
    if not re.fullmatch(r'KR:[0-9A-Z]{6}', s.get('etf_id', '')):
        raise ValueError('invalid_etf_identity')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', s.get('date', '')):
        raise ValueError('missing_provider_date')
    datetime.fromisoformat(s['date'])
    if not re.fullmatch(r'[A-Z0-9_]+', s.get('source', '')):
        raise ValueError('invalid_source')
    if not isinstance(s.get('holdings'), list) or not s['holdings']:
        raise ValueError('empty_holdings')
    s['holdings'] = [{k: h.get(k) for k in HOLDING_KEYS} for h in s['holdings']]
    identities = [(h.get('isin') or h.get('code') or h.get('name')) for h in s['holdings']]
    if len(set(identities)) != len(identities):
        raise ValueError('duplicate_constituents')
    if s.get('reported_count') != len(s['holdings']):
        s['complete'] = False
    for h in s['holdings']:
        if h['asset_type'] == 'equity' and not re.fullmatch(r'[0-9A-Z]{6}', h.get('code') or ''):
            h['asset_type'] = 'other'
        w = h.get('weight_pct')
        low = -100 if h['asset_type'] in ('cash', 'derivative') else 0
        if w is not None and (not isinstance(w, (int, float)) or not math.isfinite(w) or not low <= w <= 100):
            raise ValueError('invalid_weight_percent')
    return s


def save_snapshot(snapshot, history=HISTORY):
    s = canonical(snapshot)
    p = history / s['source'] / s['etf_id'].split(':')[1] / (s['date'] + '.json')
    previous = read(p)
    if previous and previous.get('complete') and not s.get('complete'):
        return False  # A truncated response may not replace a valid dated observation.
    if previous and previous.get('observed_at') and s.get('observed_at'):
        if s['observed_at'] < previous['observed_at']:
            return False
    if previous == s:
        return False
    write(p, s)
    return True


def load_history(history=HISTORY):
    result = {}
    for p in sorted(history.glob('*/*/*.json')):
        s = canonical(read(p))
        result.setdefault(s['etf_id'], {}).setdefault(s['source'], {})[s['date']] = s
    return result


def issuer_name(asset):
    return asset.get('issuer') or asset['name'].split()[0]


def holdings_config(sector_config, active_registry):
    """Extend constituents coverage without changing sector-price rankings."""
    assets = list(sector_config['assets'])
    ids = {a['id'] for a in assets}
    for asset in active_registry.get('assets', []):
        if asset['id'] in ids:
            raise ValueError('duplicate_holdings_universe_identity')
        if (asset.get('market') != 'KR' or asset.get('kind') != 'market_active' or
                asset.get('classification_status') != 'reviewed_market_active'):
            raise ValueError('unverified_market_active_asset')
        ids.add(asset['id'])
        assets.append(asset)
    return {**sector_config, 'assets': assets,
            'version': sector_config['version'] + '+' + active_registry['version'],
            'holdings_active_registry': {k: v for k, v in active_registry.items() if k != 'assets'}}


def no_comparison(reason, old_date, date):
    return {'status': 'waiting', 'reason': reason, 'from_date': old_date,
            'to_date': date, 'changes': []}


def select_snapshot(sources, expected, failed_sources=()):
    # A single consistent provider is used for a fund and both comparison dates.
    # Official, complete, exact-date disclosures take precedence over vendor data.
    options = []
    for source, days in sources.items():
        for date, s in days.items():
            if date <= expected:
                options.append(((date == expected, s.get('complete', False),
                                 date, source not in failed_sources, source != 'NAVER'), s))
    return max(options, key=lambda x: x[0])[1] if options else None


def export(config, expected, sessions, source_status, *, history=HISTORY, saved=False):
    assets = [a for a in config['assets'] if a['market'] == 'KR']
    dates = sessions['KR']
    previous, weekly = dates[-2], dates[-6]
    all_history = load_history(history)
    failed_current = {(r.get('etf_id'), r.get('source')) for r in source_status
                      if r.get('status') in ('failed', 'not_attempted') and
                      r.get('date', expected) == expected}
    funds = []
    for asset in assets:
        aid = asset['id']
        sources = all_history.get(aid, {})
        s = select_snapshot(sources, expected, {source for fid, source in failed_current if fid == aid})
        fund = {'id': aid, 'symbol': asset['symbol'], 'name': asset['name'],
                'issuer': issuer_name(asset), 'group': asset['group'],
                'kind': asset['kind'], 'representative': asset['representative'],
                'issuer_full': asset.get('issuer_full'),
                'classification_sources': asset.get('classification_sources', []),
                'benchmark_name': asset.get('benchmark_name'),
                'status': 'unavailable', 'asof': None, 'source': None,
                'source_url': None, 'observed_at': None, 'collection_mode': None,
                'holding_count': 0, 'latest_holdings': [],
                'available_sources': sorted(sources),
                'daily': no_comparison('두 날짜의 전체 구성 이력이 필요합니다.', previous, expected),
                'weekly': no_comparison('5거래일 전의 전체 구성 이력이 필요합니다.', weekly, expected)}
        if s:
            fund.update(asof=s['date'], source=s['source'], source_url=s['source_url'],
                        observed_at=s.get('observed_at'), collection_mode=s.get('collection_mode'),
                        basis=s['basis'], weight_basis=s['weight_basis'],
                        quantity_basis=s.get('quantity_basis'),
                        creation_unit_shares=s.get('creation_unit_shares'), complete=s['complete'],
                        holding_count=len(s['holdings']), latest_holdings=s['holdings'])
            fund['status'] = ('current' if s['complete'] else 'incomplete') if s['date'] == expected else 'stale'
            if (aid, s['source']) in failed_current:
                fund['status'] = 'cached_after_failure'
                fund['reason'] = '이번 조회 실패. 보존 자료를 현재 변동 계산에 사용하지 않습니다.'
            selected_days = sources[s['source']]
            if fund['status'] == 'current' and s.get('date_kind') == 'provider_date':
                for key, prior in [('daily', previous), ('weekly', weekly)]:
                    old = selected_days.get(prior)
                    if old:
                        comp = compare_snapshots(old, s, threshold_pp=0.05)
                        comp.update(from_date=prior, to_date=expected)
                        fund[key] = comp
            future = sorted(d for days in sources.values() for d in days if d > expected)
            if future:
                fund['next_published_date'] = future[0]
        funds.append(fund)
    cohorts = {}
    for period in ('daily', 'weekly'):
        pairs = {}
        for f in funds:
            c = f[period]
            if c['status'] == 'ready':
                pair = (c['from_date'], c['to_date'])
                pairs.setdefault(pair, []).append(f['id'])
        cohorts[period] = [{'from_date': a, 'to_date': b, 'fund_ids': ids}
                           for (a, b), ids in sorted(pairs.items())]
    coverage = {'registered': len(funds), 'current': sum(f['status'] == 'current' for f in funds),
                'baseline_only': sum(f['status'] == 'current' and f['daily']['status'] != 'ready' for f in funds),
                'daily_comparable': sum(f['daily']['status'] == 'ready' for f in funds),
                'weekly_comparable': sum(f['weekly']['status'] == 'ready' for f in funds),
                'failed': sum(f['status'] in ('unavailable', 'incomplete', 'cached_after_failure') for f in funds),
                'stale': sum(f['status'] == 'stale' for f in funds),
                'archived_reference': sum(f['collection_mode'] == 'archived_official_response' for f in funds)}
    coverage['market_active_registered'] = sum(f['kind'] == 'market_active' for f in funds)
    coverage['market_active_current'] = sum(f['kind'] == 'market_active' and f['status'] == 'current' for f in funds)
    return {'schema_version': 'etf-holdings-v1', 'generated_at': datetime.now(timezone.utc).isoformat(),
            'expected_date': expected, 'previous_date': previous, 'weekly_date': weekly,
            'universe_version': config['version'], 'threshold_pp': 0.05,
            'collection_mode': 'saved_history' if saved else 'live_and_dated_disclosures',
            'coverage': coverage, 'funds': funds, 'cohorts': cohorts, 'source_status': source_status,
            'active_registry': config.get('holdings_active_registry'),
            'method': {'version': 'holdings-weight-v1',
                       'scope': '한국 국내주식 업종·테마 ETF와 운용사별 코스피·코스닥 시장 액티브 ETF',
                       'comparison': '동일 ETF·원천·구성 기준·비중 분모의 완전한 두 날짜만 비교',
                       'daily': '직전 한국 거래일', 'weekly': '정확히 5거래일 전',
                       'selection': '완료 거래일의 전체 공식 공시 우선, 없으면 동일 날짜 NAVER 전체 구성 사용. 두 원천의 이력을 연결하지 않음.',
                       'weight': '비중 변화는 퍼센트포인트(%p); 가격 변화에 의해서도 발생하며 자금유입·매매를 뜻하지 않음',
                       'quantity': '확인된 CU 크기를 사용한 ETF 1좌당 구성수량만 비교. 전체 펀드 보유수량·매수금액 아님. 분할·합병 전수 검증 전.',
                       'aggregation': '같은 날짜쌍·선택 ETF 집합의 증가/감소 개수와 평균 변화. 비중·수량을 금액처럼 합산하지 않음.',
                       'future_disclosure': '다음 거래일 공시는 보존하되 완료 거래일의 현재 비중으로 사용하지 않음.',
                       'threshold': '강조 기준 0.05%p, 최적화된 매매 기준 아님. 전체 원래 변화는 보존.',
                       'price_effect_adjustment': '미제공: 일관된 구성종목별 조정가격·권리자료 미확보',
                       'reference_url': 'https://jusikbot.com/etf-constituents'}}


def blocked(exc):
    return ((isinstance(exc, HTTPError) and exc.code in (401, 403, 429)) or
            getattr(exc, 'status_code', None) in (401, 403, 429) or
            type(exc).__name__ == 'SourceBlockedError')


def collect(config, expected, sessions, etf_codes, *, naver_enabled=True, issuers_enabled=True):
    from holdings_pipeline import naver, issuers, kodex
    assets = [a for a in config['assets'] if a['market'] == 'KR']
    statuses = []
    # Reuse the price collector's publicly retrieved issuer responses: no second KODEX request.
    try:
        observations = kodex.extract_archives(ROOT / 'etf_pipeline/data/raw', assets, etf_codes)
        added = sum(save_snapshot(s) for s in observations)
        statuses.append({'source': 'KODEX', 'mode': 'reuse_official_responses',
                         'observations': len(observations), 'saved': added,
                         'note': '공식 가격 수집 응답의 전체 구성 공시 재사용. 추가 구성 요청 없음.'})
    except Exception as e:
        statuses.append({'source': 'KODEX', 'status': 'unavailable', 'reason': str(e)[:200]})
    if naver_enabled:
        stop = False
        for asset in assets:
            item = {'etf_id': asset['id'], 'source': 'NAVER', 'mode': 'live'}
            if stop:
                item.update(status='not_attempted', reason='source_access_limit_circuit_open')
            else:
                try:
                    s = naver.fetch_snapshot(asset, etf_codes)
                    save_snapshot(s)
                    item.update(status='success', date=s['date'], complete=s['complete'])
                except Exception as e:
                    item.update(status='failed', reason=str(e)[:200])
                    stop = blocked(e)
                time.sleep(0.35)
            statuses.append(item)
    if issuers_enabled:
        stopped = set()
        history = load_history()
        targets = [expected, sessions['KR'][-2], sessions['KR'][-6]]
        for asset in assets:
            if not issuers.supports(asset):
                continue
            issuer = issuer_name(asset).upper()
            for date in targets:
                item = {'etf_id': asset['id'], 'source': issuer, 'date': date, 'mode': 'live'}
                if issuer in stopped:
                    item.update(status='not_attempted', reason='source_access_limit_circuit_open')
                elif date != expected and history.get(asset['id'], {}).get(issuer, {}).get(date, {}).get('complete') is True:
                    item.update(status='stored_history', reason='이미 확보한 날짜별 전체 공시 보존')
                elif issuer == 'HANARO' and date != expected:
                    item.update(status='history_accumulating', reason='공식 현재 공시를 날짜별로 축적 중. 과거 날짜를 임의 지정하지 않음.')
                else:
                    try:
                        s = issuers.fetch_snapshot(asset, date, etf_codes, raw_dir=PIPE / 'raw')
                        if s['date'] != date:
                            raise ValueError('issuer_requested_date_mismatch')
                        save_snapshot(s)
                        item.update(status='success', complete=s['complete'])
                    except Exception as e:
                        item.update(status='failed', reason=str(e)[:200])
                        if blocked(e):
                            stopped.add(issuer)
                    time.sleep(0.35)
                statuses.append(item)
    return statuses


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--saved', action='store_true', help='Export saved disclosures; do not call source APIs')
    p.add_argument('--import-snapshots', action='append', default=[])
    p.add_argument('--no-naver', action='store_true')
    p.add_argument('--no-issuers', action='store_true')
    p.add_argument('--output', default=str(ROOT / 'holdings_data.json'))
    args = p.parse_args()
    config = holdings_config(read(ROOT / 'etf_pipeline/universe.json'), read(PIPE / 'active_universe.json'))
    etf_codes = {r['symbol'] for r in read(ROOT / 'etf_pipeline/kr_catalog.json', {'rows': []})['rows']}
    expected, sessions, _ = calendar_sessions()
    for source in args.import_snapshots:
        payload = read(Path(source))
        for s in payload if isinstance(payload, list) else payload['snapshots']:
            save_snapshot(s)
    statuses = [] if args.saved else collect(config, expected['KR'], sessions, etf_codes,
                                             naver_enabled=not args.no_naver,
                                             issuers_enabled=not args.no_issuers)
    result = export(config, expected['KR'], sessions, statuses, saved=args.saved)
    write(Path(args.output), result)
    print(json.dumps({'output': args.output, 'expected_date': result['expected_date'],
                      'coverage': result['coverage']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
