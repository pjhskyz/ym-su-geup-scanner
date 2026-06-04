#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
기관/외국인 수급 스캐너 — 데이터 빌더 (전종목 + 일별 시계열)
=========================================================
- 메인 순위/순매수/백분위 = [기준일-LOOKBACK, 기준일] '누적' (1년 맥락)
- 시계열 순위(오늘/D-1/D-2)  = 각 일자의 '일별' 순매수 순위 → 순위 점프 포착
- 수익률(1/5/20영업일)        = 전종목 일괄(price_change)로 효율 조회
- 연속 순매수일(기관/외국인)  = 상위 STREAK_TOP 종목만 일별 부호로 계산
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
LOOKBACK_DAYS = 365        # 누적 분석기간 1년 (2년=730, 3년=1095)
BUCKET_SIZE   = 0.4        # 백분위 버킷 크기(%)
MAX_BUCKET    = int(100 / BUCKET_SIZE)        # = 250
STREAK_TOP    = 300        # 연속 순매수일을 계산할 (주체별) 상위 종목 수
STREAK_WINDOW = 30
SLEEP         = 0.4
MARKETS       = ["KOSPI", "KOSDAQ"]
INVESTORS     = {"inst": "기관합계", "frgn": "외국인"}
TV_COLS       = {"inst": "기관합계", "frgn": "외국인합계"}
OUT_PATH      = "scanner_data.json"


def log(*a): print(*a, file=sys.stderr, flush=True)
def ymd(d): return d.strftime("%Y%m%d")
def shift(asof, days): return ymd(dt.datetime.strptime(asof, "%Y%m%d") - dt.timedelta(days=days))


# --------------------------- 영업일 ---------------------------
def business_days(asof: str, n: int) -> list[str]:
    """asof 포함 직전 n 영업일을 최신순으로."""
    df = stock.get_market_ohlcv(shift(asof, n * 3 + 15), asof, "005930")
    days = [d.strftime("%Y%m%d") for d in df.index]
    if len(days) < n:
        raise RuntimeError("영업일을 충분히 확보하지 못했습니다.")
    return days[-n:][::-1]


# ----------------------- 순매수 랭킹 -----------------------
def ranking(asof: str, investor: str, lookback: int) -> dict:
    """lookback>0: [asof-lookback, asof] 누적 / lookback==0: asof 하루(일별).
    반환: { ticker: {"net":원, "rank":1.., "bucket":1.., "name":str} }"""
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


# ----------------------------- 업종 -----------------------------
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
    bdays = business_days(asof, 21)          # [D, D-1, ... D-20]
    D, D1, D2 = bdays[0], bdays[1], bdays[2]
    log("기준일:", [D, D1, D2])

    # 누적(메인) + 일별(시계열) 랭킹
    cum, daily = {}, {}
    for k, inv in INVESTORS.items():
        log(f"· {inv} 누적 랭킹…")
        cum[k] = ranking(D, inv, LOOKBACK_DAYS)
        log(f"· {inv} 일별 랭킹 (오늘/D-1/D-2)…")
        daily[k] = {d: ranking(d, inv, 0) for d in (D, D1, D2)}
    if not cum["inst"]:
        log("[!] 데이터가 비었습니다. 장 마감 후(18시 이후) 다시 실행하세요.")
        return

    # 수익률 전종목 일괄 (1일/1주/1개월)
    log("· 수익률 일괄 조회…")
    r1m = price_change_map(bdays[1], D)
    r5m = price_change_map(bdays[5], D)
    r20m = price_change_map(bdays[20], D)

    # 연속 순매수일: 주체별 상위 STREAK_TOP 합집합만
    pool = set()
    for k in INVESTORS:
        pool |= {t for t, _ in sorted(cum[k].items(), key=lambda kv: kv[1]["rank"])[:STREAK_TOP]}
    log(f"· 연속 순매수일 계산 ({len(pool)}종목)…")
    streaks = {}
    for tkr in pool:
        tv = daily_net(tkr, D)
        streaks[tkr] = (continuity(tv, TV_COLS["inst"]), continuity(tv, TV_COLS["frgn"]))

    secmap = sector_map()

    def build_rows(key: str) -> list:
        rall = cum[key]
        n = len(rall)
        rows = []
        for rank, (tkr, info) in enumerate(
                sorted(rall.items(), key=lambda kv: kv[1]["rank"]), start=1):
            if tkr in streaks:
                (istk, i30), (fstk, f30) = streaks[tkr]
            else:
                istk = i30 = fstk = f30 = None
            rows.append([
                rank, info["name"], secmap.get(tkr, ""),
                round(info["net"] / 1_000_000),
                daily[key][D].get(tkr, {}).get("bucket", MAX_BUCKET),
                daily[key][D1].get(tkr, {}).get("bucket", MAX_BUCKET),
                daily[key][D2].get(tkr, {}).get("bucket", MAX_BUCKET),
                round(rank / n * 100, 1),
                r1m.get(tkr, 0.0), r5m.get(tkr, 0.0), r20m.get(tkr, 0.0),
                istk, i30, fstk, f30,
            ])
        return rows

    out = {"asof": D, "inst": build_rows("inst"), "frgn": build_rows("frgn")}
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    log(f"\n✓ 기관 {len(out['inst'])} · 외국인 {len(out['frgn'])}종목(전종목) → {OUT_PATH}")


if __name__ == "__main__":
    main()
