#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
기관/외국인 수급 스캐너 — 데이터 빌더 (전종목)
=========================================================
컬럼 구성
  - 순위 시계열         : 각 일자의 '일별' 순매수 금액 순위(오늘/D-1/D-2) → 점프 포착
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


stock = _import_stock()

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
    alldf = pd.concat(frames)
    alldf = alldf[alldf["순매수거래대금"] != 0].sort_values("순매수거래대금", ascending=False)
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
    return out


# ----------------------- 시가총액 -----------------------
def cap_map(asof: str) -> dict:
    df = stock.get_market_cap(asof)        # 전종목 시가총액
    time.sleep(SLEEP)
    if df is None or df.empty or "시가총액" not in df.columns:
        return {}
    return {tkr: int(row["시가총액"]) for tkr, row in df.iterrows()}


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
        log("[i] WICS 업종 매핑 실패 → 업종 공란")
    return out


# ----------------------------- main -----------------------------
def main():
    if not (os.getenv("KRX_ID") and os.getenv("KRX_PW")):
        log("[!] KRX_ID / KRX_PW 환경변수가 없습니다. (2025-12-27 KRX 회원제 전환)")
        log("    상단 docstring 안내대로 설정 후 다시 실행하세요.")
        return

    asof = stock.get_nearest_business_day_in_a_week()
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
        log("[!] 데이터가 비었습니다. 장 마감 후(18시 이후) 다시 실행하세요.")
        return

    # 시총 + 기간별 순매수(시총대비 비율용)
    log("· 시가총액 + 기간 순매수(I/F 비율용)…")
    caps = cap_map(D)
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

    secmap = sector_map(D)
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

    out = {"asof": D, "rows": merged_rows()}

    log("· 지수 이격도(50일)…")
    disp = index_disparity(D)
    if disp:
        kst = dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
        disp["updated"] = kst.strftime("%Y-%m-%d %H:%M")
        out["disparity"] = disp
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    log(f"\n✓ {len(out['rows'])}종목(전종목) → {OUT_PATH}")


if __name__ == "__main__":
    main()
