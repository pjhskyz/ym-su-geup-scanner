#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
기관/외국인 수급 스캐너 — 데이터 빌더 (전종목)
=========================================================
컬럼 구성
  - 실제 금액 순위      : 각 일자의 KOSPI+KOSDAQ 순매수금액 순번 → rank_details
  - 기존 rows 순위 칸  : 호환을 위해 0.4% 백분위 구간을 유지 (실제 등수 아님)
  - 순매수(메인 금액)   : '당일(D)' 순매수 거래대금 (주체별, 백만원)
  - I1/I5/I20          : 기관 1/5/20영업일 누적 순매수 ÷ 시가총액 × 100 (%)
  - F1/F5/F20          : 외국인 1/5/20영업일 누적 순매수 ÷ 시가총액 × 100 (%)
  - 연속 순매수일       : 전종목 — stocks_data.json 축적 이력 기반, API 콜 0회 (이력 없으면 상위 STREAK_TOP 폴백)
  - 수익률             : 1/5/20영업일 (전종목 일괄)
  - 출력: 순매수≠0 전종목을 inst/frgn 두 테이블로 scanner_data.json

설치:  pip install pykrx finance-datareader pandas

KRX 인증 (중요)
  2025-12-27 KRX 회원제 전환으로 로그인 필수. 아래 환경변수 설정:
    export KRX_ID="krx_아이디"          # macOS / Linux
    export KRX_PW="krx_비밀번호"
    # Windows PowerShell:  $env:KRX_ID="..."  ;  $env:KRX_PW="..."

실행:  python build_scanner_data.py
"""

from __future__ import annotations
import json
import math
import os
import sys
import time
import datetime as dt
from importlib.metadata import PackageNotFoundError, version

import pandas as pd


def _import_stock(retries: int = 3, wait_s: int = 40):
    """pykrx는 import 시점에 KRX 로그인을 시도한다. 서버가 빈 응답을 주는 일시 장애는
    재시도로 넘기되, 끝내 실패하면 시끄럽게 죽는다 — 비밀번호 만료 등은 알아채야 하므로."""
    last = None
    for attempt in range(1, retries + 1):
        try:
            from pykrx import stock as _stock
            return _stock
        except Exception as e:
            last = e
            print(f"[!] KRX 초기화 실패 (시도 {attempt}/{retries}): {e}", file=sys.stderr, flush=True)
            if attempt < retries:
                time.sleep(wait_s)
    raise SystemExit(f"KRX 로그인 {retries}회 연속 실패 — KRX 점검 중이거나 비밀번호 만료(≈90일 주기)일 수 있습니다: {last}")


# 로그인은 실제 빌드 시에만 수행한다. 순위/스냅샷 검증은 네트워크 없이 가능하다.
stock = None

try:
    import FinanceDataReader as fdr
    _HAS_FDR = True
except Exception:
    _HAS_FDR = False


# ----------------------------- 설정 -----------------------------
LOOKBACK_DAYS = 365
BUCKET_SIZE   = 0.4
MAX_BUCKET    = int(100 / BUCKET_SIZE)
MIN_CAP       = 50_000_000_000      # 시가총액 500억 미만 종목 제외 (스팩·초소형주 정리)
STREAK_TOP    = 300
STREAK_WINDOW = 30
SLEEP         = 0.4
MARKETS       = ["KOSPI", "KOSDAQ"]
INVESTORS     = {"inst": "기관합계", "frgn": "외국인"}
TV_COLS       = {"inst": "기관합계", "frgn": "외국인합계"}
OUT_PATH      = "scanner_data.json"
STATUS_PATH   = "scanner_status.json"
KST           = dt.timezone(dt.timedelta(hours=9))
SOURCE_RANK_DEFINITION = (
    "KOSPI·KOSDAQ의 해당 투자자 순매수금액이 0이 아닌 전종목을 금액 내림차순으로 정렬한 순번. "
    "시가총액 500억원 및 화면 필터 적용 전이며, 금액 동률은 종목코드 오름차순. "
    "종목 자체의 과거 수급 강도를 뜻하지 않음."
)


def log(*a): print(*a, file=sys.stderr, flush=True)
def ymd(d): return d.strftime("%Y%m%d")
def shift(asof, days): return ymd(dt.datetime.strptime(asof, "%Y%m%d") - dt.timedelta(days=days))


def business_days(asof: str, n: int) -> list[str]:
    df = stock.get_market_ohlcv(shift(asof, n * 3 + 15), asof, "005930")
    days = [d.strftime("%Y%m%d") for d in df.index]
    if len(days) < n:
        raise RuntimeError("영업일을 충분히 확보하지 못했습니다.")
    return days[-n:][::-1]


# ----------------------- 순매수 랭킹/맵 -----------------------
def ranking(asof: str, investor: str, lookback: int) -> dict:
    """lookback>0: 누적 / 0: 일별. {ticker:{net,rank,bucket,name}}"""
    start = shift(asof, lookback) if lookback > 0 else asof
    frames = []
    for mkt in MARKETS:
        df = stock.get_market_net_purchases_of_equities(start, asof, mkt, investor)
        time.sleep(SLEEP)
        if df is not None and not df.empty and "순매수거래대금" in df.columns:
            frames.append(df[["순매수거래대금", "종목명"]])
    if not frames:
        return {}
    if len(frames) != len(MARKETS):
        raise RuntimeError(f"{asof} {investor}: 일부 시장 순매수 응답 누락 — 불완전한 순위 발행 중단")
    alldf = pd.concat(frames)
    if alldf.index.has_duplicates:
        raise RuntimeError(f"{asof} {investor}: 중복 종목코드 — 순위 발행 중단")
    # 동률을 종목코드로 결정해 재실행 때 순위/Top30 경계가 바뀌지 않게 한다.
    alldf = alldf[alldf["순매수거래대금"] != 0].sort_index().sort_values(
        "순매수거래대금", ascending=False, kind="stable")
    n = len(alldf)
    out = {}
    for i, (tkr, row) in enumerate(alldf.iterrows(), start=1):
        bucket = min(MAX_BUCKET, max(1, math.ceil(i / n * 100 / BUCKET_SIZE)))
        out[tkr] = {"net": int(row["순매수거래대금"]), "rank": i,
                    "bucket": bucket, "name": str(row["종목명"])}
    return out


def net_window(frm: str, to: str, investor: str) -> dict:
    """[frm, to] 영업일 구간 누적 순매수 {ticker: net(원)}"""
    out = {}
    for mkt in MARKETS:
        df = stock.get_market_net_purchases_of_equities(frm, to, mkt, investor)
        time.sleep(SLEEP)
        if df is not None and not df.empty and "순매수거래대금" in df.columns:
            for tkr, row in df.iterrows():
                out[tkr] = int(row["순매수거래대금"])
        else:
            raise RuntimeError(f"{frm}~{to} {investor} {mkt}: 기간 순매수 응답 누락")
    return out


# ----------------------- 시가총액 -----------------------
def cap_map(asof: str) -> dict:
    df = stock.get_market_cap(asof, market="ALL")
    time.sleep(SLEEP)
    if df is None or df.empty or "시가총액" not in df.columns:
        return {}
    return {tkr: int(row["시가총액"]) for tkr, row in df.iterrows()}


def raw_ratio(net: int, cap: int | None) -> float | None:
    """필터와 정렬에는 표시용 반올림 전 값을 사용한다."""
    return net / cap * 100 if cap and cap > 0 else None


def make_rank_details(daily: dict, dates: list[str], caps: dict) -> dict:
    """row 인덱스를 변경하지 않고 정확한 금액 순위/금액/비율을 제공한다."""
    tickers = set().union(*(daily[k].get(dates[0], {}) for k in INVESTORS))
    details = {}
    for tkr in sorted(tickers):
        if caps.get(tkr, 0) < MIN_CAP:
            continue
        item = {}
        for k in INVESTORS:
            item[k] = {
                key: daily[k].get(date, {}).get(tkr, {}).get("rank") if date else None
                for key, date in zip(("today", "previous", "two_days_ago"),
                                     dates + [None] * (3 - len(dates)))
            }
        item["net_won"] = {k: daily[k][dates[0]].get(tkr, {}).get("net", 0) for k in INVESTORS}
        item["ratio_pct"] = {k: raw_ratio(item["net_won"][k], caps.get(tkr)) for k in INVESTORS}
        item["cap_won"] = caps[tkr]
        details[tkr] = item
    return details


def comparison_snapshot(daily: dict, dates: list[str], caps: dict,
                        sectors: dict | None = None) -> dict:
    """직전 거래일 자체의 원 순매수/시총으로 비교 자료를 만든다. 현재 시총 역산 금지."""
    D = dates[0]
    result = {"asof": D, "available": False, "reason": None, "rows": [], "rank_details": {}}
    if not caps or any(not daily[k].get(D) for k in INVESTORS):
        result["reason"] = "직전 거래일 순매수 또는 시가총액 자료가 없어 신규 진입 비교를 보류합니다."
        return result
    # 과거 일자 시총이 일부만 내려오면 완전한 Top30 비교로 취급하지 않는다.
    tickers = set(daily["inst"][D]) | set(daily["frgn"][D])
    if tickers - set(caps):
        result["reason"] = "직전 거래일 일부 종목의 시가총액이 누락되어 비교를 보류합니다."
        return result
    sectors = sectors or {}
    details = make_rank_details(daily, dates, caps)
    rows = []
    for tkr, item in details.items():
        di, df = daily["inst"][D].get(tkr), daily["frgn"][D].get(tkr)
        row = [None] * 28
        row[0], row[1], row[23] = (di or df)["name"], sectors.get(tkr, ""), tkr
        for k, net_idx, rank_idx, ratio_idx in (("inst", 2, 3, 14), ("frgn", 6, 7, 17)):
            row[net_idx] = round(item["net_won"][k] / 1_000_000)
            row[ratio_idx] = round(item["ratio_pct"][k], 2)
            for offset, day in enumerate(dates[:3]):
                row[rank_idx + offset] = daily[k].get(day, {}).get(tkr, {}).get("bucket", MAX_BUCKET)
        row[27] = round(caps[tkr] / 1e8)
        rows.append(row)
    rows.sort(key=lambda row: (-details[row[23]]["net_won"]["inst"], row[23]))
    result.update(available=True, rows=rows, rank_details=details,
                  method="previous_trading_day_net_and_market_cap",
                  precision="unrounded_source_values",
                  available_fields=["name", "sector", "neti", "netf", "i1", "f1", "code", "mcap"])
    return result


# ----------------------- 수익률(종가 기준, 전종목 일괄) -----------------------
def close_at(date: str) -> dict:
    """특정일 전종목 종가 {종목코드: 종가}."""
    out = {}
    for mkt in MARKETS:
        try:
            df = stock.get_market_ohlcv_by_ticker(date, market=mkt)
        except Exception:
            df = None
        time.sleep(SLEEP)
        if df is not None and not df.empty and "종가" in df.columns:
            for tkr, row in df.iterrows():
                try:
                    out[tkr] = float(row["종가"])
                except Exception:
                    pass
    return out


# ----------------------- 연속 순매수일 -----------------------
def continuity(tv, col) -> tuple[int, int]:
    if tv is None or tv.empty or col not in tv.columns:
        return (0, 0)
    v = tv[col].values
    streak = 0
    for x in reversed(v):
        if x > 0:
            streak += 1
        else:
            break
    return (streak, int((v[-STREAK_WINDOW:] > 0).sum()))


def daily_net(ticker, asof):
    df = stock.get_market_trading_value_by_date(shift(asof, 60), asof, ticker)
    time.sleep(SLEEP)
    return df


STK_HIST_PATH = "stocks_data.json"   # build_market.py 가 축적하는 전종목 일별 수급

def load_stock_history():
    """축적 이력 로드 — {dates, s:{tkr:[기관배열, 외인배열]}}. 부족하면 None(폴백)."""
    if not os.path.exists(STK_HIST_PATH):
        return None
    try:
        with open(STK_HIST_PATH, encoding="utf-8") as f:
            p = json.load(f)
        dates, s = p.get("dates", []), p.get("s", {})
        if len(dates) < STREAK_WINDOW + 5 or not s:
            return None
        return {"dates": dates, "s": s}
    except Exception:
        return None


def _cont_arr(v):
    """일별 순매수 배열 → (현재 연속 순매수일, 최근 STREAK_WINDOW일 중 순매수일수)."""
    streak = 0
    for x in reversed(v):
        if x > 0:
            streak += 1
        else:
            break
    w = v[-STREAK_WINDOW:]
    return (streak, int(sum(1 for x in w if x > 0)))


def streaks_from_history(hist, net1, D):
    """이력 + 당일 순매수(net1)로 전 종목 연속/순매수일수 계산 — 부호만 사용(단위 무관)."""
    dates, s = hist["dates"], hist["s"]
    has_today = bool(dates) and dates[-1] == D
    n = len(dates)
    out = {}
    tickers = set(s) | set(net1["inst"]) | set(net1["frgn"])
    for tkr in tickers:
        arr = s.get(tkr)
        ia = list(arr[0]) if arr else [0] * n
        fa = list(arr[1]) if arr else [0] * n
        ti = net1["inst"].get(tkr, 0)
        tf = net1["frgn"].get(tkr, 0)
        if has_today:                      # 이력에 당일이 이미 있으면 빌더 당일 집계로 교체
            ia[-1], fa[-1] = ti, tf
        else:                              # 보통 케이스: 이력은 전일까지 → 당일을 덧붙임
            ia.append(ti)
            fa.append(tf)
        out[tkr] = (_cont_arr(ia), _cont_arr(fa))
    return out


# ----------------------- 지수 이격도 (50일) -----------------------
DISP_MA    = 50     # 이동평균 일수
DISP_YEARS = 2      # 차트 보존 기간(년)

def _fetch_index(code: str, frm: str, to: str, timeout_s: int = 60):
    """지수 OHLCV를 스레드로 감싸 하드 타임아웃 적용.
    pykrx의 HTTP 호출엔 timeout이 없어 KRX가 응답을 물고 있으면 무한 대기 →
    잡 전체가 죽는 사고를 여기서 차단한다."""
    import threading
    box = {}
    def run():
        try:
            box["df"] = stock.get_index_ohlcv_by_date(frm, to, code)
        except Exception as e:
            box["err"] = e
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        raise TimeoutError(f"{timeout_s}s 초과 (KRX 무응답)")
    if "err" in box:
        raise box["err"]
    return box.get("df")

def index_disparity(D: str):
    """코스피·코스닥 50일 이격도 시계열 (종가 ÷ 50일 이평 × 100).
    연 단위 분할 요청 + 콜당 60초 제한. 어떤 실패든 None을 돌려주고
    수급 데이터 생성에는 영향을 주지 않는다."""
    frm_all = shift(D, int(DISP_YEARS * 365 + DISP_MA * 2.5))
    series = {}
    for key, code in (("kospi", "1001"), ("kosdaq", "2001")):
        parts, f = [], frm_all
        try:
            while f <= D:
                t = min(shift(f, -364), D)          # f + 364일 (연 단위 구간)
                parts.append(_fetch_index(code, f, t))
                time.sleep(SLEEP)
                f = shift(t, -1)                    # 다음 구간 시작 = t + 1일
        except Exception as e:
            log(f"[!] 지수({key}) 조회 실패: {e} → 이격도 생략")
            return None
        parts = [p for p in parts if p is not None and not p.empty]
        if not parts:
            log(f"[!] 지수({key}) 데이터 없음 → 이격도 생략")
            return None
        df = pd.concat(parts)
        df = df[~df.index.duplicated(keep="last")].sort_index()
        if "종가" not in df.columns or len(df) < DISP_MA + 5:
            log(f"[!] 지수({key}) 데이터 부족 → 이격도 생략")
            return None
        close = df["종가"].astype(float)
        disp = (close / close.rolling(DISP_MA).mean() * 100).round(2).dropna()
        series[key] = {d.strftime("%Y%m%d"): float(v) for d, v in disp.items()}
    dates = sorted(series["kospi"])[-int(DISP_YEARS * 252):]
    return {
        "dates": dates,
        "kospi": [series["kospi"].get(d) for d in dates],
        "kosdaq": [series["kosdaq"].get(d) for d in dates],
    }


# ----------------------- 밸류에이션 (스냅샷 2콜) -----------------------
def fundamentals(D: str) -> dict:
    """{ticker: (PER, PBR, 배당수익률%)} — 0/음수는 None(적자·무배당)."""
    out = {}
    for mkt in MARKETS:
        try:
            df = stock.get_market_fundamental_by_ticker(D, market=mkt)
        except Exception as e:
            log(f"[!] 밸류에이션({mkt}) 실패: {e}")
            continue
        time.sleep(SLEEP)
        if df is None or df.empty:
            continue
        for tkr, r in df.iterrows():
            def g(c):
                try:
                    v = float(r[c])
                    return round(v, 2) if v > 0 else None
                except Exception:
                    return None
            out[str(tkr)] = (g("PER"), g("PBR"), g("DIV"))
    return out


# ----------------------- 업종 (WICS 중분류) -----------------------
# FnGuide WICS 중분류 — 네이버·다음 증권이 쓰는 그 분류 (반도체/IT하드웨어/자동차/은행 …)
WICS_CODES = [
    "G1010", "G1510", "G2010", "G2020", "G2030", "G2510", "G2520", "G2530",
    "G2550", "G2560", "G3010", "G3020", "G3030", "G3510", "G3520", "G4010",
    "G4020", "G4030", "G4040", "G4050", "G4510", "G4520", "G4530", "G4535",
    "G5010", "G5020", "G5510",
]

def sector_map(asof: str) -> dict:
    import requests
    hdr = {"User-Agent": "Mozilla/5.0"}
    out = {}
    for cd in WICS_CODES:
        url = (f"https://www.wiseindex.com/Index/GetIndexComponets"
               f"?ceil_yn=0&dt={asof}&sec_cd={cd}")
        try:
            j = requests.get(url, headers=hdr, timeout=20).json()
            for it in j.get("list", []):
                code = str(it.get("CMP_CD", "")).zfill(6)
                nm = str(it.get("SEC_NM_KOR", "")).strip()
                if code and nm:
                    out[code] = nm
        except Exception as e:
            log(f"[!] WICS {cd} 실패: {e}")
        time.sleep(0.3)
    if out:
        log(f"· WICS 업종 매핑 {len(out)}종목 / {len(set(out.values()))}개 업종")
    else:
        log("[i] WICS 업종 매핑 실패 → 마지막 저장본의 종목코드별 분류 확인")
    return out


def load_sector_history(path: str | None = None) -> dict:
    """직전 발행본의 코드별 분류만 읽는다. 분류가 이월됐으면 원 조회일도 유지한다."""
    result = {"snapshot_asof": None, "sectors": {}, "source_asof_by_code": {}}
    try:
        with open(path or OUT_PATH, encoding="utf-8") as f:
            previous = json.load(f)
    except (OSError, ValueError):
        return result
    if not isinstance(previous, dict):
        return result
    result["snapshot_asof"] = previous.get("asof")
    metadata = previous.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    classification = metadata.get("sector_classification")
    classification = classification if isinstance(classification, dict) else {}
    source_dates = classification.get("source_asof_by_code")
    for row in previous.get("rows") or []:
        if not isinstance(row, list) or len(row) < 24:
            continue
        code, sector = row[23], row[1]
        if not isinstance(code, str) or not isinstance(sector, str) or not sector.strip():
            continue
        result["sectors"][code] = sector.strip()
        # 메타가 없는 기존 발행본은 해당 발행본의 조회 기준일을 사용한다.
        # 이미 분류 메타가 있다면 미상의 원 조회일을 발행일로 바꾸지 않는다.
        result["source_asof_by_code"][code] = (
            source_dates.get(code) if isinstance(source_dates, dict)
            else (None if classification else previous.get("asof"))
        )
    return result


def resolve_sector_classification(asof: str, fresh: dict, previous: dict) -> tuple[dict, dict]:
    """이번 응답에서 빠진 코드에만 과거 분류를 적용한다. 이름/유사업종 추정은 하지 않는다."""
    fresh = {code: sector.strip() for code, sector in fresh.items()
             if isinstance(sector, str) and sector.strip()}
    carried = {code: sector for code, sector in previous["sectors"].items() if code not in fresh}
    sectors = {**carried, **fresh}
    source_dates = {code: previous["source_asof_by_code"].get(code) for code in carried}
    source_dates.update({code: asof for code in fresh})
    metadata = {
        "provider": "FnGuide WICS",
        "requested_asof": asof,
        "fallback_snapshot_asof": previous["snapshot_asof"],
        "source_asof_by_code": source_dates,
        "note": "이번 WICS 조회에서 누락된 종목에만 마지막 저장본의 같은 종목코드 분류를 이월합니다. "
                "원 조회일을 유지하며, 해당 분류가 현재도 유효한지는 미확인입니다. 신규 미분류 종목은 공란입니다. "
                "직전 거래일 비교표에도 같은 분류를 적용하므로 그 거래일의 역사적 업종을 뜻하지 않습니다.",
    }
    return sectors, {"metadata": metadata, "fresh_codes": set(fresh), "carried_codes": set(carried)}


def sector_classification_metadata(state: dict, rows: list, comparison_rows: list) -> dict:
    """실제 수록된 종목만 집계하고, 분류 원 조회일을 다음 실행에 전달한다."""
    def counts(codes):
        return {
            "fresh": len(codes & state["fresh_codes"]),
            "carried": len(codes & state["carried_codes"]),
            "missing": len(codes - state["fresh_codes"] - state["carried_codes"]),
        }
    current_codes = {row[23] for row in rows}
    comparison_codes = {row[23] for row in comparison_rows}
    included_codes = current_codes | comparison_codes
    metadata = dict(state["metadata"])
    metadata["source_asof_by_code"] = {
        code: date for code, date in metadata["source_asof_by_code"].items() if code in included_codes
    }
    carried_codes = included_codes & state["carried_codes"]
    metadata["fallback_source_asofs"] = sorted({
        metadata["source_asof_by_code"][code] for code in carried_codes
        if metadata["source_asof_by_code"].get(code)
    })
    metadata["fallback_unknown_source_count"] = sum(
        not metadata["source_asof_by_code"].get(code) for code in carried_codes
    )
    metadata["counts"] = counts(current_codes)
    metadata["comparison_counts"] = counts(comparison_codes)
    return metadata


# ----------------------------- main -----------------------------
def main():
    global stock
    collection_started_at = dt.datetime.now(KST).isoformat(timespec="seconds")
    previous_sectors = load_sector_history()
    if not (os.getenv("KRX_ID") and os.getenv("KRX_PW")):
        log("[!] KRX_ID / KRX_PW 환경변수가 없습니다. (2025-12-27 KRX 회원제 전환)")
        log("    상단 docstring 안내대로 설정 후 다시 실행하세요.")
        raise SystemExit(1)

    stock = _import_stock()

    try:
        asof = stock.get_nearest_business_day_in_a_week()
    except Exception as e:
        raise RuntimeError("KRX 최근 거래일 조회 실패 — 실행 로그의 로그인/비밀번호 만료 또는 원천 응답 오류를 확인하세요. 기존 데이터는 유지합니다.") from e
    bdays = business_days(asof, 22)          # [D, D-1, ... D-21] (폴백 여유분 포함)
    # 장중엔 당일 투자자별 데이터가 아직 없음 → 직전 영업일로 자동 폴백
    if not ranking(bdays[0], INVESTORS["inst"], 0):
        log(f"· {bdays[0]} 투자자 데이터 미확정(장중 추정) → 직전 영업일로 폴백")
        bdays = bdays[1:]
    D, D1, D2 = bdays[0], bdays[1], bdays[2]
    log("기준일:", [D, D1, D2])

    # 일별 랭킹(시계열·당일금액)
    daily = {}
    for k, inv in INVESTORS.items():
        log(f"· {inv} 일별 랭킹…")
        daily[k] = {d: ranking(d, inv, 0) for d in (D, D1, D2)}
    if not daily["inst"][D]:
        raise RuntimeError("당일 기관 수급 데이터가 비었습니다. 이전 발행본을 유지합니다.")
    if any(not daily[k][d] for k in INVESTORS for d in (D, D1, D2)):
        raise RuntimeError("투자자/거래일별 수급 자료가 누락됐습니다. 불완전한 순위를 발행하지 않습니다.")

    # 시총 + 기간별 순매수(시총대비 비율용)
    log("· 시가총액 + 기간 순매수(I/F 비율용)…")
    caps = cap_map(D)
    if not caps:
        raise RuntimeError("기준일 시가총액을 조회하지 못했습니다. 이전 발행본을 유지합니다.")
    missing_caps = (set(daily["inst"][D]) | set(daily["frgn"][D])) - set(caps)
    if missing_caps:
        raise RuntimeError(
            f"기준일 {len(missing_caps)}개 종목의 시가총액이 누락됐습니다. "
            "시총 기준 미달로 간주하지 않고 발행을 중단하며 이전 발행본을 유지합니다.")
    try:
        previous_caps = cap_map(D1)
    except Exception as e:
        log(f"[!] 직전 거래일 시가총액 조회 실패: {e} → 신규 진입 비교 보류")
        previous_caps = {}
    win = {
        "i5": net_window(bdays[4], D, INVESTORS["inst"]),
        "i20": net_window(bdays[19], D, INVESTORS["inst"]),
        "f5": net_window(bdays[4], D, INVESTORS["frgn"]),
        "f20": net_window(bdays[19], D, INVESTORS["frgn"]),
    }
    net1 = {k: {t: v["net"] for t, v in daily[k][D].items()} for k in INVESTORS}  # 당일

    def ratio(net, tkr):
        c = caps.get(tkr)
        return round(net / c * 100, 2) if c else 0.0

    # 수익률(전종목 일괄)
    log("· 종가 수집(수익률 = 종가 대비 종가)…")
    cD  = close_at(D)
    c1  = close_at(bdays[1])
    c5  = close_at(bdays[5])
    c20 = close_at(bdays[20])
    def chg(base: dict) -> dict:
        out = {}
        for t, p in cD.items():
            b = base.get(t)
            if b:
                out[t] = round((p / b - 1) * 100, 1)   # (당일종가/기준종가 - 1)
        return out
    r1m, r5m, r20m = chg(c1), chg(c5), chg(c20)

    # 연속 순매수일: 축적 이력(stocks_data.json) 기반 전종목 계산 — API 콜 0회
    hist = load_stock_history()
    if hist:
        log(f"· 연속 순매수일 (이력 {len(hist['dates'])}일 · 전종목)…")
        streaks = streaks_from_history(hist, net1, D)
    else:
        # 폴백: 이력 파일이 없거나 짧으면 예전 방식 — 당일 상위 STREAK_TOP 합집합만
        pool = set()
        for k in INVESTORS:
            pool |= {t for t, _ in sorted(net1[k].items(), key=lambda kv: abs(kv[1]), reverse=True)[:STREAK_TOP]}
        log(f"· 연속 순매수일 (이력 없음 → 상위 {len(pool)}종목 폴백)…")
        streaks = {}
        for tkr in pool:
            tv = daily_net(tkr, D)
            streaks[tkr] = (continuity(tv, TV_COLS["inst"]), continuity(tv, TV_COLS["frgn"]))

    secmap, sector_state = resolve_sector_classification(D, sector_map(D), previous_sectors)
    log("· 밸류에이션(PER/PBR/배당)…")
    fnd = fundamentals(D)

    def merged_rows() -> list:
        tickers = set(daily["inst"][D]) | set(daily["frgn"][D])
        rows = []
        for tkr in tickers:
            if caps.get(tkr, 0) < MIN_CAP:      # 시총 500억 미만 제외
                continue
            di, df = daily["inst"][D].get(tkr), daily["frgn"][D].get(tkr)
            name = (di or df)["name"]
            if tkr in streaks:
                (istk, i30), (fstk, f30) = streaks[tkr]
            else:
                istk = i30 = fstk = f30 = None
            rows.append([
                name, secmap.get(tkr, ""),
                round(net1["inst"].get(tkr, 0) / 1_000_000),          # 기관 당일순매수
                daily["inst"][D].get(tkr, {}).get("bucket", MAX_BUCKET),
                daily["inst"][D1].get(tkr, {}).get("bucket", MAX_BUCKET),
                daily["inst"][D2].get(tkr, {}).get("bucket", MAX_BUCKET),
                round(net1["frgn"].get(tkr, 0) / 1_000_000),          # 외국인 당일순매수
                daily["frgn"][D].get(tkr, {}).get("bucket", MAX_BUCKET),
                daily["frgn"][D1].get(tkr, {}).get("bucket", MAX_BUCKET),
                daily["frgn"][D2].get(tkr, {}).get("bucket", MAX_BUCKET),
                istk, i30, fstk, f30,
                ratio(net1["inst"].get(tkr, 0), tkr),                 # I1
                ratio(win["i5"].get(tkr, 0), tkr),                    # I5
                ratio(win["i20"].get(tkr, 0), tkr),                   # I20
                ratio(net1["frgn"].get(tkr, 0), tkr),                 # F1
                ratio(win["f5"].get(tkr, 0), tkr),                    # F5
                ratio(win["f20"].get(tkr, 0), tkr),                   # F20
                r1m.get(tkr, 0.0), r5m.get(tkr, 0.0), r20m.get(tkr, 0.0),
                tkr,                                          # 종목코드
                fnd.get(tkr, (None,) * 3)[0],                 # PER
                fnd.get(tkr, (None,) * 3)[1],                 # PBR
                fnd.get(tkr, (None,) * 3)[2],                 # 배당수익률(%)
                round(caps.get(tkr, 0) / 1e8),                # 시가총액(억)
            ])
        rows.sort(key=lambda r: r[2], reverse=True)   # 기관 당일순매수 desc
        return rows

    rows = merged_rows()
    if not rows:
        raise RuntimeError("발행할 종목이 없습니다. 이전 발행본을 유지합니다.")
    rank_details = make_rank_details(daily, [D, D1, D2], caps)
    # 정확한 원 단위 금액으로 정렬: 백만원 반올림 동률에 의한 비결정성 제거.
    rows.sort(key=lambda row: (-rank_details[row[23]]["net_won"]["inst"], row[23]))
    comparison = comparison_snapshot(daily, [D1, D2], previous_caps, secmap)
    sector_classification = sector_classification_metadata(sector_state, rows, comparison["rows"])
    sector_counts = sector_classification["counts"]
    log(f"· 업종: 이번 조회 {sector_counts['fresh']} / 과거 분류 이월 {sector_counts['carried']} "
        f"/ 미분류 {sector_counts['missing']}종목")
    try:
        pykrx_version = version("pykrx")
    except PackageNotFoundError:
        pykrx_version = "unknown"
    out = {
        "asof": D,
        "schema_version": 2,
        "rows": rows,
        "rank_details": rank_details,
        "metadata": {
            "collection_started_at": collection_started_at,
            "requested_asof": asof,
            "previous_trading_day": D1,
            "asof_fallback": D != asof,
            "status": "collected",
            "source_finalized": None,
            "sector_classification": sector_classification,
            "source_rank": SOURCE_RANK_DEFINITION,
            "legacy_bucket": {
                "width_pct": BUCKET_SIZE,
                "row_indices": {"inst": [3, 4, 5], "frgn": [7, 8, 9]},
                "definition": "ceil(당일 실제 순번 / 당일 순매수≠0 종목수 × 100 / 0.4). 실제 등수가 아닌 백분위 구간.",
            },
            "universe": {
                "markets": MARKETS,
                "minimum_cap_won": MIN_CAP,
                "rank_before_cap_filter": True,
                "source_nonzero_counts": {k: {d: len(daily[k][d]) for d in (D, D1, D2)} for k in INVESTORS},
                "display_count": len(rows),
                "top30_definition": "스캐너 수록 종목(해당일 시총 500억원 이상)의 양수 순매수만 비율 또는 금액으로 정렬한 상위 30개. 원천 전체 금액 순위와 모수가 다름.",
            },
            "source": {
                "provider": "KRX via pykrx",
                "pykrx_version": pykrx_version,
                "api": "get_market_net_purchases_of_equities",
                "endpoint": "dbms/MDC/STAT/standard/MDCSTAT02401",
                "investors": {"inst": "기관합계(7050)", "frgn": "외국인(9000, 기타외국인 제외)"},
            },
            "coverage": {
                "after_market": "unverified",
                "nxt": "unverified",
                "label": "KRX 일별 집계 · 시간외/NXT 반영 범위 미확인",
                "reason": "현재 API 호출은 조회일·시장·투자자만 지정합니다. 20시까지의 거래 및 NXT 합산 여부·원천 확정 시각을 검증할 응답 필드가 없습니다.",
                "audit_method": "builder and pykrx source inspection",
            },
            "schedule": {
                "timezone": "Asia/Seoul",
                "weekdays": [1, 2, 3, 4, 5],
                "primary": "20:20",
                "retry": "20:50",
                "kind": "scheduled_start",
                "note": "평일 20:05 외부 예약 요청은 20:20까지 대기한 뒤 수집합니다. GitHub 예약은 20:20·20:50 보완 실행이며, 지연된 예약도 처리합니다. 수집·배포에 따라 화면 반영은 늦어질 수 있습니다.",
            },
            "comparison": comparison,
        },
    }

    log("· 지수 이격도(50일)…")
    disp = index_disparity(D)
    if disp:
        kst = dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
        disp["updated"] = kst.strftime("%Y-%m-%d %H:%M")
        out["disparity"] = disp
    out["metadata"]["updated_at"] = dt.datetime.now(KST).isoformat(timespec="seconds")
    out["updated"] = out["metadata"]["updated_at"]
    tmp_path = OUT_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    os.replace(tmp_path, OUT_PATH)
    log(f"\n✓ {len(out['rows'])}종목(전종목) → {OUT_PATH}")


def write_build_status(attempted_at: str, status: str) -> None:
    """수집 실패 시 마지막 정상 수급은 유지하고 별도 상태만 발행한다."""
    last_asof = None
    try:
        with open(OUT_PATH, encoding="utf-8") as f:
            last_asof = json.load(f).get("asof")
    except (OSError, ValueError):
        pass
    run_id, repo = os.getenv("GITHUB_RUN_ID"), os.getenv("GITHUB_REPOSITORY")
    payload = {
        "attempted_at": attempted_at,
        "finished_at": dt.datetime.now(KST).isoformat(timespec="seconds"),
        "status": status,
        "code": None if status == "success" else "source_fetch_failed",
        "asof": last_asof,
        "error": None if status == "success" else "수급 수집 실패 · KRX 인증 또는 원천 응답을 확인해야 합니다. 마지막 정상 데이터를 유지합니다.",
        "run_url": f"https://github.com/{repo}/actions/runs/{run_id}" if repo and run_id else None,
    }
    tmp_path = STATUS_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    os.replace(tmp_path, STATUS_PATH)


if __name__ == "__main__":
    attempted_at = dt.datetime.now(KST).isoformat(timespec="seconds")
    try:
        main()
    except (Exception, SystemExit):
        write_build_status(attempted_at, "failed")
        raise
    else:
        write_build_status(attempted_at, "success")
