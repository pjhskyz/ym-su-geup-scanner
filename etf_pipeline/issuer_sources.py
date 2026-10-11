"""Independent issuer snapshots. Quotes never silently replace price histories."""
from __future__ import annotations
import json, math, re, io, zipfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin
import xml.etree.ElementTree as ET

class PublicPage(HTMLParser):
    def __init__(self):
        super().__init__();self.tokens=[];self.hidden={};self.links=[];self.skip=0
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag in ('script','style'):self.skip+=1
        if tag=='input' and a.get('id') and 'value' in a:self.hidden[a['id']]=a['value']
        if tag=='a' and a.get('href'):self.links.append(a['href'])
    def handle_endtag(self,tag):
        if tag in ('script','style') and self.skip:self.skip-=1
    def handle_data(self,data):
        if not self.skip and data.strip():self.tokens.append(data.strip().replace('\ufeff',''))

def parse_ssga(text,symbol,expected):
    page=PublicPage();page.feed(text)
    quick=json.loads(page.hidden['fund-quick-info'])['attrs']
    if quick['fund-ticker']['value']!=symbol:raise ValueError('issuer_identity_mismatch')
    tokens=page.tokens
    start=tokens.index('Fund Market Price');section=tokens[start:start+45]
    stamp=next(t for t in section[:4] if t.startswith('as of '))
    day=datetime.strptime(stamp.removeprefix('as of '),'%b %d %Y').strftime('%Y-%m-%d')
    if day>expected:raise ValueError('issuer_unclosed_session')
    i=section.index('Closing Price')
    # The issuer explicitly calls this the Primary Listing Exchange official close.
    if not any('official closing price' in t.lower() for t in section[i:i+5]):raise ValueError('issuer_close_definition_missing')
    value=next(t for t in section[i+1:i+6] if re.fullmatch(r'\$[\d,]+\.\d+',t))
    close=float(value.replace('$','').replace(',',''))
    if not math.isfinite(close) or close<=0:raise ValueError('issuer_invalid_close')
    return {'id':'US:'+symbol,'symbol':symbol,'source':'SSGA','date':day,'close':close,
            'geography':quick.get('investment-geography',{}).get('value'),
            'name':quick['fund-name']['value'],'quote_kind':'official_exchange_close',
            'status':'fresh' if day==expected else 'stale',
            'history_available':False,'total_return_verified':False}

def compare_quote(quote,series):
    # Match the issuer's exact quote date, not the latest row or page header date.
    prices={r['date']:r['close'] for r in series['rows']}
    if quote['date'] not in prices:return {'status':'no_matching_date'}
    primary=prices[quote['date']];other=quote['close']
    tolerance=max(1.01 if quote.get('id','').startswith('KR:') else .02,other*.0005)
    return {'status':'match' if abs(primary-other)<=tolerance else 'mismatch',
            'date':quote['date'],'primary_close':primary,'issuer_close':other,
            'difference':primary-other,'tolerance':tolerance}

def collect_quotes(config,fetch,root,expected,market='ALL'):
    from .monitor import dump
    from concurrent.futures import ThreadPoolExecutor
    assets=[a for a in config['assets'] if a.get('issuer_quote_url') and (market=='ALL' or a['market']==market)]
    def one(asset):
        url=asset['issuer_quote_url'];day=expected[asset['market']]
        result={'id':asset['id'],'url':url,'expected_date':day}
        try:
            text=fetch.get(url)
            if asset['market']=='US':result.update(parse_ssga(text,asset['symbol'],day))
            else:result.update(parse_kodex(json.loads(text),asset['symbol'],day))
            result['retrieved_at']=fetch.times[url]
            path=root/'data'/'series'/(asset['id'].replace(':','_')+'.json')
            if path.exists():result['comparison']=compare_quote(result,json.loads(path.read_text()))
            else:result['comparison']={'status':'primary_missing'}
            save=root/'data'/'issuer_quotes'/(asset['id'].replace(':','_')+'.json')
            if save.exists() and json.loads(save.read_text())['date']>result['date']:
                raise ValueError('issuer_regressed_response_preserved_cache')
            dump(save,result)
        except Exception as exc:result.update(status='failed',error=str(exc))
        return result
    with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(one,assets))
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
    payload={'retrieved_at':stamp,'expected_dates':expected,'quotes':results,
             'note':'Independent official quotes only; no substitution for 253-day history or Sortino.'}
    dump(root/'output'/'issuer_status.json',payload)
    dump(root/'data'/'issuer_runs'/(stamp+'.json'),payload)
    return results

def parse_kodex(data,symbol,expected):
    product=data['info']['product']
    if product['stkTicker']!=symbol:raise ValueError('issuer_identity_mismatch')
    # realIdx/curp are intraday: use dated historical SEC_CLOSE_P only.
    records=[]
    for r in data['suik']['standardList']:
        day=datetime.strptime(r['EVAL_D'],'%Y%m%d').strftime('%Y-%m-%d')
        if day<=expected:records.append({'date':day,'close':float(r['SEC_CLOSE_P'])})
    if not records:raise ValueError('issuer_no_closed_session')
    records.sort(key=lambda r:r['date']);quote=records[-1]
    if any(not math.isfinite(r['close']) or r['close']<=0 for r in records):raise ValueError('issuer_invalid_close')
    if len({r['date'] for r in records})!=len(records):raise ValueError('issuer_duplicate_dates')
    return dict(id='KR:'+symbol,symbol=symbol,source='SAMSUNG_ASSET_MANAGEMENT',
        date=quote['date'],close=quote['close'],name=product['fNm'],geography=product['typeLnm'],
        status='fresh' if quote['date']==expected else 'stale',quote_kind='dated_exchange_close',
        history_available=True,history_rows=len(records),recent_closes=records,
        total_return_verified=False,distributions=data['info'].get('divideList',[]),
        distribution_date_semantics='BASIC_D is record date; ex-date not validated')
