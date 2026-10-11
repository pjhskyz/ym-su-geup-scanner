"""Public, date-checked holdings from SOL, KoAct, PLUS and HANARO.

Discovery follows each issuer's public product page and its own date-picker
request. Historical live-price fields are deliberately ignored. No provider
quantity is exposed until its unit (per CU / whole fund) is independently
verified. These adapters have no authentication or access-control fallback.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import math
import re
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path

SOL = {"475300": "211053", "475310": "211054", "0008T0": "211080",
       "466920": "211028", "464600": "211016", "0141S0": "211101"}
KOACT = {"0150K0": "2ETFT9", "0219B0": "2ETFV4"}
PLUS = {"449450": "006192", "421320": "006344", "457990": "006279"}
HANARO = {"381570": "50FF16209D3645F5", "438900": "52EEBC0DE7D04AC9",
          "322400": "5CD1DD359516432B", "314700": "07EE61EDFBEC4ECB",
          "454320": "045134E6E3534476"}
SUPPORTED_SYMBOLS = frozenset(SOL) | frozenset(KOACT) | frozenset(PLUS) | frozenset(HANARO)
_BLOCKED_HOSTS: set[str] = set()


def supports(asset):
    return asset.get("market", "KR") == "KR" and asset.get("symbol") in SUPPORTED_SYMBOLS


def _number(value):
    if value is None or str(value).strip() in ("", "-", "--"):
        return None
    n = float(str(value).replace(",", "").replace("%", "").strip())
    if not math.isfinite(n):
        raise ValueError("Non-finite issuer value")
    return n


def _iso(value):
    cleaned = re.sub(r"[^0-9]", "", str(value))
    if len(cleaned) != 8:
        raise ValueError(f"Invalid provider date: {value!r}")
    return dt.date.fromisoformat(f"{cleaned[:4]}-{cleaned[4:6]}-{cleaned[6:]}").isoformat()


def _symbol_and_type(code, name, etf_codes):
    raw = str(code or "").strip()
    isin = raw if re.fullmatch(r"KR[0-9A-Z]{10}", raw) else None
    symbol = raw[3:9] if re.fullmatch(r"KR7[0-9A-Z]{9}", raw) else raw
    if raw.startswith("KRD") or any(x in name for x in ("현금", "예금", "설정현금")):
        return raw, isin, "cash"
    if any(x in name.upper() for x in ("선물", "옵션", "SWAP", "FUTURE")):
        return raw, isin, "derivative"
    if symbol in etf_codes or symbol in {str(x).removeprefix("KR:") for x in etf_codes}:
        return symbol, isin, "fund"
    # These adapters are restricted to issuer-verified domestic equity ETFs.
    # ETF instruments are removed by the whole listed-ETF catalog supplied by caller.
    if re.fullmatch(r"[0-9A-Z]{6}", symbol) and not symbol.startswith("KR"):
        return symbol, isin, "equity"
    return raw, isin, "other"


def _holding(code, name, weight, etf_codes, isin=None):
    code, detected_isin, kind = _symbol_and_type(code, str(name), etf_codes)
    return {"code": code, "isin": isin or detected_isin, "name": str(name).strip(),
            "asset_type": kind, "weight_pct": _number(weight), "quantity": None}


def _snapshot(asset, date, source, url, holdings, count, complete, observed_at=None):
    codes = [x["code"] for x in holdings]
    if len(codes) != len(set(codes)):
        raise ValueError("Duplicate security identifiers in issuer response")
    return {"etf_id": asset["id"], "date": date, "date_kind": "provider_date",
            "basis": "creation_basket", "weight_basis": f"{source.lower()}_published_pdf_weight_pct",
            "source": source, "source_url": url,
            "observed_at": observed_at or dt.datetime.now(dt.timezone.utc).isoformat(),
            "collection_mode": "live", "complete": bool(complete), "reported_count": int(count),
            "creation_unit_shares": None, "quantity_basis": "unknown", "holdings": holdings}


def normalize_sol(asset, date, payload, etf_codes, source_url="", observed_at=None):
    expected = _iso(date)
    if not isinstance(payload, list) or not payload:
        raise ValueError("SOL empty or malformed holdings response")
    if any(_iso(x.get("WORK_DT")) != expected for x in payload):
        raise ValueError("SOL provider date does not match requested session")
    if any(x.get("ETF_CD6") != asset["symbol"] or x.get("FUND_CD") != SOL[asset["symbol"]] for x in payload):
        raise ValueError("SOL ETF identity mismatch")
    h = [_holding(x.get("STOCK_CODE"), x["SEC_NM"], x.get("WT_DISP"), etf_codes) for x in payload]
    # The issuer's date-picker returns its full list in one unpaginated array.
    return _snapshot(asset, expected, "SOL", source_url, h, len(payload), True, observed_at)


def normalize_koact(asset, date, payload, etf_codes, source_url="", observed_at=None):
    expected = _iso(date)
    pdf = payload.get("pdf", {})
    if _iso(pdf.get("gijunYMD")) != expected:
        raise ValueError("KoAct provider date does not match requested session")
    rows = pdf.get("list", [])
    if not rows:
        raise ValueError("KoAct empty holdings response")
    count = int(pdf["totalCnt"])
    h = [_holding(x.get("itmNo"), x["secNm"], x.get("ratio"), etf_codes) for x in rows]
    # Historical curp/risep contain current quotes: never use them for return attribution.
    return _snapshot(asset, expected, "KOACT", source_url, h, count, len(rows) == count, observed_at)


def normalize_plus(asset, date, payload, etf_codes, source_url="", observed_at=None):
    expected = _iso(date)
    rows = payload.get("content", [])
    if not rows:
        raise ValueError("PLUS empty holdings response")
    if any(_iso(x.get("wkdate")) != expected for x in rows):
        raise ValueError("PLUS returned an earlier session instead of the requested date")
    count = int(payload["totalElements"])
    h = [_holding(x.get("jmCd") or x.get("krJmCd"), x["jmNm"], x.get("ratio"), etf_codes,
                  isin=x.get("krJmCd")) for x in rows]
    complete = len(rows) == count and payload.get("last") is True and int(payload.get("number", 0)) == 0
    return _snapshot(asset, expected, "PLUS", source_url, h, count, complete, observed_at)


def normalize_hanaro(asset, date, document, etf_codes, source_url="", observed_at=None):
    # The historical fragment /get-fund-holdings-list does NOT echo its date.
    # Only the full page with the published pdfDate is accepted in v1.
    match = re.search(r'<input\b[^>]*id="pdfDate"[^>]*value="([^\"]+)"', document, re.S)
    if not match:
        raise ValueError("HANARO published holdings date not found")
    provider_date = _iso(match.group(1))
    if provider_date != _iso(date):
        raise ValueError(f"HANARO only date-verified current disclosure is {provider_date}; requested {_iso(date)}")
    table = re.search(r'<tbody\b[^>]*data-target="fundHoldingsListTableBody"[^>]*>(.*?)</tbody>', document, re.S)
    total = re.search(r'data-target="pdfTotalCount"[^>]*>\s*(\d+)', document)
    if not table or not total:
        raise ValueError("HANARO complete table or count missing")
    h = []
    for tr in re.findall(r'<tr\b[^>]*>(.*?)</tr>', table.group(1), re.S):
        cells = [html.unescape(re.sub(r'<[^>]+>', '', x)).strip() for x in re.findall(r'<t[dh]\b[^>]*>(.*?)</t[dh]>', tr, re.S)]
        if len(cells) != 6:
            raise ValueError("HANARO unexpected holdings row")
        h.append(_holding(cells[1], cells[2], cells[5], etf_codes))
    if not h:
        raise ValueError("HANARO empty holdings")
    count = int(total.group(1))
    return _snapshot(asset, provider_date, "HANARO", source_url, h, count, len(h) == count, observed_at)


def _download(url, raw_path=None):
    from urllib.parse import urlsplit
    host = urlsplit(url).netloc
    if host in _BLOCKED_HOSTS:
        raise RuntimeError(f"Provider circuit open after access restriction: {host}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Roy ETF Research Monitor/1.0", "Accept": "application/json,text/html;q=0.9,*/*;q=0.8"})
        with urllib.request.urlopen(req, timeout=30) as response:
            body = response.read(10_000_001)
            if len(body) > 10_000_000:
                raise ValueError("Issuer response too large")
            beginning = body[:10000].decode("utf-8", "ignore").lower()
            if any(x in beginning for x in ("verify you are human", "access denied", "cf-chl-", "captcha challenge")):
                _BLOCKED_HOSTS.add(host)
                raise RuntimeError(f"Provider returned access challenge: {host}")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 429):
            _BLOCKED_HOSTS.add(host)
        raise
    if raw_path is not None:
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(body)
    return body


@lru_cache(maxsize=8)
def _hanaro_current(url):
    return _download(url)


def fetch_snapshot(asset, date, etf_codes, raw_dir=None):
    """Return a normalized snapshot; fail closed on date, count or schema errors.

    HANARO supports only the published current page. Prior snapshots must be
    accumulated by the caller; request-only dates are not asserted as fact.
    """
    symbol = asset.get("symbol")
    day = _iso(date)
    compact = day.replace("-", "")
    if not supports(asset):
        raise ValueError(f"Unsupported issuer ETF {symbol}")
    if symbol in SOL:
        source = "SOL"
        url = f"https://www.soletf.com/api/fund/pdfList?fund_cd={SOL[symbol]}&work_dt={compact}"
        normalizer = normalize_sol
    elif symbol in KOACT:
        source = "KOACT"
        url = f"https://www.samsungactive.co.kr/api/v1/product/etf-pdf/{KOACT[symbol]}.do?gijunYMD={compact}"
        normalizer = normalize_koact
    elif symbol in PLUS:
        source = "PLUS"
        url = f"https://www.plusetf.co.kr/api/v1/product/pdf/list?n={PLUS[symbol]}&page=0&d={compact}&pageSize=100"
        normalizer = normalize_plus
    else:
        source = "HANARO"
        url = f"https://www.hanaroetf.com/fund/{HANARO[symbol]}"
        normalizer = normalize_hanaro
    path = Path(raw_dir) / f"{source.lower()}-{symbol}-{day}.raw" if raw_dir is not None else None
    if source == "HANARO":
        body = _hanaro_current(url)
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        payload = body.decode("utf-8")
    else:
        payload = json.loads(_download(url, path))
    return normalizer(asset, day, payload, etf_codes, url)
