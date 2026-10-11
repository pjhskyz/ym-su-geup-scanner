#!/usr/bin/env python3
"""Public Naver ETF discovery only. Never adds candidates to the ranking universe."""
import argparse
import collections
import datetime as dt
import json
import pathlib
import re
import urllib.request

SOURCE = 'https://finance.naver.com/api/sise/etfItemList.nhn'
EXCLUDED_STRUCTURE = re.compile(r'레버리지|인버스|커버드콜|단일종목|채권|국채|국고채|통안채|회사채|은행채|금융채|혼합|머니마켓|금리|TDF|TRF|롱.*숏', re.I)
OVERSEAS = re.compile(r'미국|글로벌|차이나|중국|일본|유럽|인도|베트남|대만|아시아|싱가포르|한중|해외|나스닥|S&P|토탈월드|팔란티어|테슬라|엔비디아', re.I)
DOMESTIC_NAME_EXCEPTIONS = {'150460', '215620'}

def parse_payload(raw):
    for encoding in ('utf-8-sig', 'cp949'):
        try:
            data = json.loads(raw.decode(encoding))
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError('unsupported_encoding')
    if data.get('resultCode') != 'success':
        raise ValueError('provider_result_not_success')
    records = data.get('result', {}).get('etfItemList')
    if not isinstance(records, list) or len(records) < 100:
        raise ValueError('incomplete_catalog')
    if len({x['itemcode'] for x in records}) != len(records):
        raise ValueError('duplicate_symbols')
    return records

def triage(record):
    code, name = record['itemcode'], record['itemname']
    if EXCLUDED_STRUCTURE.search(name):
        return 'excluded_structure', '레버리지·인버스·옵션·단일종목·채권·혼합·현금성 구조'
    if code not in DOMESTIC_NAME_EXCEPTIONS and OVERSEAS.search(name):
        return 'excluded_overseas_name', '상품명상 해외 노출; 한국 국내업종 아님'
    tab = int(record['etfTabCode'])
    if tab == 2 or (tab == 7 and re.search(r'리츠|부동산', name)):
        return 'candidate_pending_review', '운용사 국내주식/리츠·업종/테마 및 중복노출 검증 전 후보'
    if tab == 1:
        return 'excluded_broad_market', '네이버 국내시장지수 분류; 업종/테마 후보에 자동 포함 안 함'
    return 'outside_domestic_sector_candidate', '원자재·해외·채권·기타 분류; 국내업종 편입에 별도 검토 필요'

def build_catalog(records, registered_ids=()):
    registered = {x.split(':')[-1] for x in registered_ids}
    rows = []
    for x in records:
        status, reason = triage(x)
        if x['itemcode'] in registered:
            status, reason = 'registered_reviewed', '고정 유니버스에 등록; 자동 편입 아님'
        rows.append({'symbol': x['itemcode'], 'name': x['itemname'],
                     'provider_category_code': int(x['etfTabCode']),
                     'review_status': status, 'reason': reason})
    return {'source_url': SOURCE, 'observed_at': dt.datetime.now(dt.timezone.utc).isoformat(),
            'source_asof': None, 'source_asof_note': '응답에 목록 기준일 없음; 조회 시각과 시세 기준일을 동일시하지 않음',
            'listed_count': len(rows),
            'provider_category_counts': dict(collections.Counter(str(x['provider_category_code']) for x in rows)),
            'review_counts': dict(collections.Counter(x['review_status'] for x in rows)),
            'candidate_count': sum(x['review_status'] == 'candidate_pending_review' for x in rows),
            'rules_version': 'kr-discovery-v1',
            'notes': ['전체 공개 목록은 확보했으나 국내 업종/테마 전수 검증 완료를 뜻하지 않음',
                      '상품명·공급자 분류는 후보 선별용이며 운용사 검증 없이 순위에 편입하지 않음',
                      '리츠부동산은 국내 상장 리츠·인프라 지분만 별도 검토; 채권혼합 제외',
                      '신규 후보 발견 시 자동 순위 편입 없이 검토 목록만 갱신'],
            'rows': rows}

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--output', required=True)
    p.add_argument('--raw-file')
    p.add_argument('--universe')
    a = p.parse_args()
    if a.raw_file:
        raw = pathlib.Path(a.raw_file).read_bytes()
        mode = 'saved_response'
    else:
        with urllib.request.urlopen(SOURCE, timeout=25) as response:
            raw = response.read()
        mode = 'live'
    ids = []
    if a.universe:
        universe = json.loads(pathlib.Path(a.universe).read_text())
        ids = [x['id'] for x in universe['assets'] if x['market'] == 'KR']
    result = build_catalog(parse_payload(raw), ids)
    result['collection_mode'] = mode
    path = pathlib.Path(a.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: result[k] for k in ('listed_count','candidate_count','review_counts')}, ensure_ascii=False))

if __name__ == '__main__':
    main()
