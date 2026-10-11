#!/usr/bin/env python3
"""Independent ETF observation pipeline. Python 3.10+, standard library only.
No dashboard dependency. Never promote unverified prices to total returns.
"""
from __future__ import annotations
import argparse, ast, csv, hashlib, io, json, math, os, statistics, time, threading
import urllib.request, urllib.error, urllib.parse, xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parent
VERSION='independent-provider-price-v1.2'

def dump(path,obj):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    os.replace(temp,path)

def sha(obj):
    return hashlib.sha256(json.dumps(obj,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def number(x):
    return float(str(x).replace(',',''))

class Fetcher:
    def __init__(self,folder,offline=False):
        self.folder=folder;self.blocked=set();self.offline=offline;self.times={};self.locks={};self.lock=threading.Lock()
        self.raw_index={}
        if offline:
            for path in sorted(folder.glob('*.meta.json')):
                meta=json.loads(path.read_text());self.raw_index[meta['url']]=(path,meta)
    def get(self,url,binary=False):
        host=urllib.parse.urlparse(url).netloc
        with self.lock:guard=self.locks.setdefault(host,threading.BoundedSemaphore(3))
        # At most three in-flight reads; denied hosts stop all later queued reads.
        with guard:return self._get(url,binary)
    def _get(self,url,binary=False):
        if self.offline:
            path,meta=self.raw_index[url]
            body=path.with_name(path.name.replace('.meta.json','.body')).read_bytes()
            if hashlib.sha256(body).hexdigest()!=meta['sha256']:raise ValueError('raw_hash_mismatch')
            self.times[url]=meta['retrieved_at']
            if binary:return body
            try:return body.decode('utf-8')
            except UnicodeDecodeError:return body.decode('euc-kr')
        host=urllib.parse.urlparse(url).netloc
        if host in self.blocked: raise RuntimeError('circuit_open:'+host)
        key=hashlib.sha256(url.encode()).hexdigest()
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
        target=self.folder/(stamp+'_'+key)
        # Normal client; no cookies, fingerprint evasion or credential handling.
        try:
            with urllib.request.urlopen(url,timeout=20) as r:
                body=r.read(); charset=r.headers.get_content_charset() or 'utf-8'
                target.parent.mkdir(parents=True,exist_ok=True)
                target.with_suffix('.body').write_bytes(body)
                dump(target.with_suffix('.meta.json'),{'url':url,'retrieved_at':stamp,'retrieved_timezone':'UTC','sha256':hashlib.sha256(body).hexdigest(),'status':r.status})
                self.times[url]=stamp
                challenge=body[:200000].lower()
                if any(s in challenge for s in (b'verify you are human',b'captcha',b'access denied')):
                    self.blocked.add(host);raise RuntimeError('access_challenge:'+host)
                if binary:return body
                try: text=body.decode(charset)
                except UnicodeDecodeError: text=body.decode('euc-kr')
                if any(s in text.lower() for s in ['verify you are human','captcha','access denied']):
                    self.blocked.add(host);raise RuntimeError('access_challenge:'+host)
                return text
        except urllib.error.HTTPError as e:
            if e.code in (401,403,429): self.blocked.add(host)
            raise RuntimeError('HTTP '+str(e.code)+' '+host) from e
        except urllib.error.URLError as e:
            # Proxy CONNECT denials arrive as URLError, not HTTPError.
            reason=str(e.reason).lower()
            if any(code in reason for code in ('401','403','429','access denied','forbidden')):
                self.blocked.add(host)
                raise RuntimeError('transport_access_denied:'+host) from e
            raise

def validate(rows,asof,rounding_tolerance=0.02):
    if not rows: raise ValueError('empty_response')
    dates=[r['date'] for r in rows]
    if len(dates)!=len(set(dates)): raise ValueError('duplicate_dates')
    rows=sorted([r for r in rows if r['date']<=asof],key=lambda r:r['date'])
    if not rows:raise ValueError('no_closed_session')
    for r in rows:
        datetime.strptime(r['date'],'%Y-%m-%d')
        if not all(math.isfinite(r[k]) and r[k]>0 for k in ['open','high','low','close']):raise ValueError('invalid_ohlc')
        if r['low']>min(r['open'],r['close'])+rounding_tolerance or r['high']<max(r['open'],r['close'])-rounding_tolerance:raise ValueError('ohlc_order:'+r['date'])
    return rows

def kr_prices(fetch,code,asof):
    url='https://fchart.stock.naver.com/sise.nhn?'+urllib.parse.urlencode(dict(timeframe='day',count=800,requestType=0,symbol=code))
    text=fetch.get(url);root=ET.fromstring(text)
    chart=root.find('.//chartdata')
    if chart is None or chart.attrib.get('symbol')!=code:raise ValueError('identity_mismatch')
    rows=[]
    for item in root.findall('.//item'):
        d,o,h,l,c,v=item.attrib['data'].split('|')
        rows.append(dict(date=d[:4]+'-'+d[4:6]+'-'+d[6:8],open=number(o),high=number(h),low=number(l),close=number(c),volume=number(v)))
    # Naver integer-KRW back-adjustments can differ by one won after rounding.
    return validate(rows,asof,1.01),[url],chart.attrib.get('name')

def us_prices(fetch,code,asof):
    rows=[];urls=[]
    for page in range(1,6):
        url='https://api.stock.naver.com/stock/'+urllib.parse.quote(code,safe='')+'/price?'+urllib.parse.urlencode(dict(page=page,pageSize=60))
        data=json.loads(fetch.get(url));urls.append(url)
        if not isinstance(data,list):raise ValueError('unexpected_us_schema')
        if not data:break
        for x in data:
            rows.append(dict(date=x['localTradedAt'][:10],open=number(x['openPrice']),high=number(x['highPrice']),low=number(x['lowPrice']),close=number(x['closePrice']),volume=None))
        if len(data)<60:break
        time.sleep(.2)
    return validate(rows,asof),urls,None

def sync_us(config,fetch,max_pages):
    wanted={a['symbol']:a for a in config['assets'] if a['market']=='US' and not a.get('provider_code')}
    found=0
    for page in range(1,max_pages+1):
        if not wanted:break
        url=f'https://api.stock.naver.com/etf/priceTop?page={page}&pageSize=60'
        data=json.loads(fetch.get(url))
        for x in data['etfs']:
            a=wanted.pop(x['symbolCode'],None)
            if a:
                if x.get('stockEndType')!='etf' or x.get('nationType')!='USA':raise ValueError('not_US_listed_ETF')
                a.update(provider_code=x['reutersCode'],name=x['stockNameEng'],identity_url=x['endUrl']);found+=1
        if page*60>=data['totalCount']:break
        time.sleep(.25)
    return {'resolved':found,'unresolved':list(wanted)}

def paths(asset):
    return ROOT/'data'/'series'/(asset['id'].replace(':','_')+'.json')

def collect_one(a,asof,fetch):
    path=paths(a)
    try:
        if not a.get('provider_code'):raise ValueError('unresolved_identity')
        rows,urls,name=(kr_prices if a['id'].startswith('KR:') else us_prices)(fetch,a['provider_code'],asof)
        old=json.loads(path.read_text()) if path.exists() else None
        # Do not splice providers or retain old values outside refreshed adjustment window.
        alerts=[]
        if old:
            previous={r['date']:r['close'] for r in old['rows']}
            revised=[r['date'] for r in rows if r['date'] in previous and abs(r['close']/previous[r['date']]-1)>.005]
            if revised:alerts.append({'historical_revision_dates':revised})
        for p,r in zip(rows,rows[1:]):
            if abs(r['close']/p['close']-1)>.35:alerts.append({'large_move_or_corporate_action':r['date']})
        obj=dict(id=a['id'],source='NAVER',basis='provider_price_adjustment_unverified',total_return_verified=False,split_adjustment_verified=False,asof=rows[-1]['date'],expected_asof=asof,validated_at=datetime.now(timezone.utc).isoformat(),source_retrieved_at=[fetch.times[u] for u in urls],replayed_from_raw=fetch.offline,urls=urls,source_name=name,rows=rows,alerts=alerts)
        # Preserve last complete latest series if response regresses.
        if old and obj['asof']<old['asof']:raise ValueError('regressed_response_preserved_cache')
        dump(path,obj)
        return dict(id=a['id'],status='fresh' if obj['asof']==asof else 'stale',rows=len(rows),asof=obj['asof'],alerts=alerts)
    except Exception as e:
        return dict(id=a['id'],status='failed',error=str(e),cached=path.exists())

def aligned_values(series,dates):
    index={r['date']:r['close'] for r in series['rows']}
    if not all(d in index for d in dates):return None
    return [index[d] for d in dates]

def apply_verified_splits(series,events):
    rows=[dict(r) for r in series['rows']];applied=[]
    for event in events:
        if event['id']!=series['id']:continue
        when=event['effective_date'];factor=event['new_shares_per_old_share']
        before=[r for r in rows if r['date']<when];after=[r for r in rows if r['date']>=when]
        if not before or not after:continue
        if after[0]['date']!=when:raise ValueError('split_date_missing')
        ratio=after[0]['close']/before[-1]['close']
        if .8<=ratio*factor<=1.2:
            for r in before:
                for key in ('open','high','low','close'):r[key]/=factor
            applied.append(event)
        elif .8<=ratio<=1.2:
            pass # Provider already adjusted; do not double-adjust.
        else:raise ValueError('split_adjustment_ambiguous')
    return dict(series,rows=rows,known_splits_applied=applied)

def sortino(prices,n):
    if len(prices)<n+1:return None
    prices=prices[-n-1:];r=[b/a-1 for a,b in zip(prices,prices[1:])]
    denom=math.sqrt(sum(min(x,0.)**2 for x in r)/n)
    return math.sqrt(252)*statistics.fmean(r)/denom if denom else None

def rank(rows,field):
    usable=[r for r in rows if r.get(field) is not None]
    usable.sort(key=lambda r:(-r[field],r['id']))
    result={};last=None;rank_num=0
    for pos,row in enumerate(usable,1):
        if last is None or row[field]!=last:rank_num=pos
        result[row['id']]=rank_num;last=row[field]
    return result
