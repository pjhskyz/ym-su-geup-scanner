#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
기관/외국인 수급 스캐너 — 데이터 빌더 (starter)
=========================================================
todaytoppick 스타일 "기관/외국인 매수 시계열 순위" + "연속 순매수일"을
pykrx 공개데이터로 재현한다.

흐름
  1) 최근 영업일 D, 그리고 D-1·D-2 영업일을 구한다.
  2) 기관·외국인 각각, 각 기준일마다 [기준일-LOOKBACK, 기준일] 윈도우의
     누적 순매수를 KOSPI+KOSDAQ 전종목 집계 → 순위 → 백분위 버킷.
  3) 기관 상위 TOP_N + 외국인 상위 TOP_N 의 합집합 종목에 대해
     수익률(1일/1주/1개월), 연속 순매수일(기관/외국인), 업종을 보강.
  4) inst/frgn 두 테이블을 scanner_data.json 으로 출력 → HTML이 탭으로 소비.

핵심 아이디어
  - 단순 누적순매수 순위가 아니라 '순위가 최근 급상승한 종목'(D-2 하위→오늘 상위)을
    잡아내는 게 알파. jump = bucket(D-2) - bucket(오늘).
  - 연속 순매수일 = 최근일부터 거꾸로 순매수(>0)가 끊기지 않은 영업일 수.

주의
  - 백분위 버킷의 정확한 모수는 todaytoppick 내부 로직이라, 여기서는
    '거래대금≠0 전종목'을 모수로 한 근사다.
  - pykrx 당일 확정치는 장 마감 후(약 18시) 채워지므로 배치는 저녁에 돌린다.

설치:  pip install pykrx finance-datareader pandas

KRX 인증 (중요)
  2025-12-27 부터 KRX 정보데이터시스템이 회원제(KRX Data Marketplace)로 전환되어
  로그인이 필수다(데이터 조회 자체는 무료). pykrx 1.2.8+ 는 아래 환경변수를 요구한다.
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
TOP_N         = 100        # 주체별 출력 상위 종목 수
LOOKBACK_DAYS = 365        # 분석기간 1년. (2년=730, 3년=1095)
BUCKET_SIZE   = 0.4        # 백분위 버킷 크기(%)
MAX_BUCKET    = int(100 / BUCKET_SIZE)        # = 250
STREAK_WINDOW = 30         # 연속/최근 순매수일 집계 영업일 수
SLEEP         = 0.4        # KRX 호출 간 딜레이(초)
MARKETS       = ["KOSPI", "KOSDAQ"]
INVESTORS     = {"inst": "기관합계", "frgn": "외국인"}   # 순매수 순위용
TV_COLS       = {"inst": "기관합계", "frgn": "외국인합계"}  # 일별 순매수 컬럼명
OUT_PATH      = "scanner_data.json"


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def ymd(d: dt.datetime) -> str:
    return d.strftime("%Y%m%d")


def shift(asof: str, days: int) -> str:
    return ymd(dt.datetime.strptime(asof, "%Y%m%d") - dt.timedelta(days=days))


# --------------------------- 영업일 ---------------------------
def business_days(asof: str, n: int) -> list[str]:
    """asof 포함 직전 n 영업일을 최신순으로. 삼성전자 거래일로 달력 추출."""
    start = shift(asof, n * 3 + 15)
    df = stock.get_market_ohlcv(start, asof, "005930")
    days = [d.strftime("%Y%m%d") for d in df.index]
    if len(days) < n:
        raise RuntimeError("영업일을 충분히 확보하지 못했습니다.")
    return days[-n:][::-1]


# ----------------------- 윈도우 누적 랭킹 -----------------------
def window_ranking(asof: str, investor: str) -> dict:
    """[asof-LOOKBACK, asof] 누적 순매수로 전종목 랭킹.
    반환: { ticker: {"net": 원, "rank": 1.., "bucket": 1..MAX_BUCKET} }"""
    start = shift(asof, LOOKBACK_DAYS)
    frames = []
    for mkt in MARKETS:
        df = stock.get_market_net_purchases_of_equities(start, asof, mkt, investor)
        time.sleep(SLEEP)
        if df is not None and not df.empty and "순매수거래대금" in df.columns:
            frames.append(df[["순매수거래대금"]])
    if not frames:
        return {}

    alldf = pd.concat(frames)
    alldf = alldf[alldf["순매수거래대금"] != 0]
    alldf = alldf.sort_values("순매수거래대금", ascending=False)
    n = len(alldf)

    out = {}
    for i, (tkr, row) in enumerate(alldf.iterrows(), start=1):
        pct = i / n * 100.0
        bucket = min(MAX_BUCKET, max(1, math.ceil(pct / BUCKET_SIZE)))
        out[tkr] = {"net": int(row["순매수거래대금"]), "rank": i, "bucket": bucket}
    return out


# -------------------- 수익률 & 일별 순매수 --------------------
def ohlcv(ticker: str, asof: str) -> pd.DataFrame | None:
    df = stock.get_market_ohlcv(shift(asof, 45), asof, ticker)
    time.sleep(SLEEP)
    return df


def returns_from(df: pd.DataFrame | None) -> tuple[float, float, float]:
    if df is None or df.empty:
        return (0.0, 0.0, 0.0)
    c = df["종가"].values

    def r(k: int) -> float:
        if len(c) <= k or c[-1 - k] == 0:
            return 0.0
        return round((c[-1] / c[-1 - k] - 1) * 100, 1)

    return (r(1), r(5), r(20))


def daily_net(ticker: str, asof: str) -> pd.DataFrame | None:
    """최근 영업일 일별 투자자 순매수(거래대금)."""
    df = stock.get_market_trading_value_by_date(shift(asof, 60), asof, ticker)
    time.sleep(SLEEP)
    return df


def continuity(tv: pd.DataFrame | None, col: str) -> tuple[int, int]:
    """(연속 순매수일, 최근 STREAK_WINDOW영업일 중 순매수일 수)."""
    if tv is None or tv.empty or col not in tv.columns:
        return (0, 0)
    v = tv[col].values  # 날짜 오름차순
    streak = 0
    for x in reversed(v):
        if x > 0:
            streak += 1
        else:
            break
    last = v[-STREAK_WINDOW:]
    cnt = int((last > 0).sum())
    return (streak, cnt)


# ----------------------------- 업종 -----------------------------
def sector_map() -> dict:
    if not _HAS_FDR:
        log("[i] finance-datareader 미설치 → 업종 공란")
        return {}
    try:
        df = fdr.StockListing("KRX")
        code_col = "Code" if "Code" in df.columns else "Symbol"
        sec_col = next((c for c in ("Sector", "Industry") if c in df.columns), None)
        if sec_col is None:
            return {}
        return {
            str(row[code_col]).zfill(6): (str(row[sec_col]) if pd.notna(row[sec_col]) else "")
            for _, row in df.iterrows()
        }
    except Exception as e:
        log("[!] 업종 보강 실패:", e)
        return {}


# ----------------------------- main -----------------------------
def main():
    if not (os.getenv("KRX_ID") and os.getenv("KRX_PW")):
        log("[!] KRX_ID / KRX_PW 환경변수가 없습니다.")
        log("    2025-12-27 KRX 회원제 전환 이후 로그인이 필수입니다.")
        log("    상단 docstring의 'KRX 인증' 안내대로 설정한 뒤 다시 실행하세요.")
        return

    asof = stock.get_nearest_business_day_in_a_week()
    days = business_days(asof, 3)            # [D, D-1, D-2]
    D, D1, D2 = days
    log("기준일:", days)

    # 주체별 3개 기준일 랭킹
    ranks = {}
    for key, inv in INVESTORS.items():
        log(f"· {inv} 윈도우 랭킹 (3개 기준일)…")
        ranks[key] = {d: window_ranking(d, inv) for d in days}
    if not ranks["inst"][D]:
        log("[!] 데이터가 비었습니다. 장 마감 후(18시 이후)에 다시 실행하세요.")
        return

    tops = {k: sorted(ranks[k][D].items(), key=lambda kv: kv[1]["rank"])[:TOP_N]
            for k in INVESTORS}
    union = {t for k in INVESTORS for t, _ in tops[k]}
    secmap = sector_map()

    # 합집합 종목 공통 데이터(수익률·연속·업종·종목명)
    log(f"· 합집합 {len(union)}종목 보강(수익률·연속 순매수일)…")
    common = {}
    for tkr in union:
        df_p = ohlcv(tkr, D)
        r1, r5, r20 = returns_from(df_p)
        tv = daily_net(tkr, D)
        istk, i30 = continuity(tv, TV_COLS["inst"])
        fstk, f30 = continuity(tv, TV_COLS["frgn"])
        common[tkr] = {
            "name": stock.get_market_ticker_name(tkr),
            "sec": secmap.get(tkr, ""),
            "r": (r1, r5, r20),
            "istk": istk, "i30": i30, "fstk": fstk, "f30": f30,
        }

    def build_rows(key: str) -> list:
        rall = ranks[key]
        n = len(rall[D])
        rows = []
        for rank, (tkr, info) in enumerate(tops[key], start=1):
            c = common[tkr]
            r1, r5, r20 = c["r"]
            rows.append([
                rank, c["name"], c["sec"],
                round(info["net"] / 1_000_000),               # 백만원
                info["bucket"],
                rall[D1].get(tkr, {}).get("bucket", MAX_BUCKET),
                rall[D2].get(tkr, {}).get("bucket", MAX_BUCKET),
                round(rank / n * 100, 1),
                r1, r5, r20,
                c["istk"], c["i30"], c["fstk"], c["f30"],
            ])
        return rows

    out = {"asof": D, "inst": build_rows("inst"), "frgn": build_rows("frgn")}
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    log(f"\n✓ 기관 {len(out['inst'])} · 외국인 {len(out['frgn'])}종목 → {OUT_PATH}")
    log("  → su-geup-scanner.html 이 inst/frgn 탭으로 자동 소비합니다.")


if __name__ == "__main__":
    main()
