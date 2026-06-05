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
  - 연속 순매수일       : 상위 STREAK_TOP 종목만 (기관/외국인)
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
from pykrx import stock

try:
    import FinanceDataReader as fdr
    _HAS_FDR = True
except Exception:
    _HAS_FDR = False


# ----------------------------- 설정 -----------------------------
LOOKBACK_DAYS = 365
BUCKET_SIZE   = 0.4
MAX_BUCKET    = int(100 / BUCKET_SIZE)
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


# ----------------------- 수익률(전종목 일괄) -----------------------
def price_change_map(frm: str, to: str) -> dict:
    out = {}
    for mkt in MARKETS:
        try:
            df = stock.get_market_price_change(frm, to, mkt)
        except Exception:
            df = None
        time.sleep(SLEEP)
        if df is not None and not df.empty and "등락률" in df.columns:
            for tkr, row in df.iterrows():
                try:
                    out[tkr] = round(float(row["등락률"]), 1)
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


# ----------------------- 업종 -----------------------
def sector_map() -> dict:
    if not _HAS_FDR:
        log("[i] finance-datareader 미설치 → 업종 공란"); return {}
    try:
        df = fdr.StockListing("KRX")
        code_col = "Code" if "Code" in df.columns else "Symbol"
        sec_col = next((c for c in ("Sector", "Industry") if c in df.columns), None)
        if not sec_col:
            return {}
        return {str(r[code_col]).zfill(6): (str(r[sec_col]) if pd.notna(r[sec_col]) else "")
                for _, r in df.iterrows()}
    except Exception as e:
        log("[!] 업종 보강 실패:", e); return {}


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
    log("· 수익률 일괄…")
    r1m = price_change_map(bdays[1], D)
    r5m = price_change_map(bdays[5], D)
    r20m = price_change_map(bdays[20], D)

    # 연속 순매수일: 주체별 상위 STREAK_TOP 합집합만
    pool = set()
    for k in INVESTORS:
        pool |= {t for t, _ in sorted(net1[k].items(), key=lambda kv: abs(kv[1]), reverse=True)[:STREAK_TOP]}
    log(f"· 연속 순매수일 ({len(pool)}종목)…")
    streaks = {}
    for tkr in pool:
        tv = daily_net(tkr, D)
        streaks[tkr] = (continuity(tv, TV_COLS["inst"]), continuity(tv, TV_COLS["frgn"]))

    secmap = sector_map()

    def merged_rows() -> list:
        tickers = set(daily["inst"][D]) | set(daily["frgn"][D])
        rows = []
        for tkr in tickers:
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
            ])
        rows.sort(key=lambda r: r[2], reverse=True)   # 기관 당일순매수 desc
        return rows

    out = {"asof": D, "rows": merged_rows()}
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    log(f"\n✓ {len(out['rows'])}종목(전종목) → {OUT_PATH}")


if __name__ == "__main__":
    main()
