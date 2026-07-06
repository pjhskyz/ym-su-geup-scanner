#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
시장지표 빌더 — market_data.json 생성 (ADR + 증시자금추이)
=========================================================
[ADR] 코스피·코스닥 20거래일 상승/하락 종목수 비율 (≥120% 과열 / ≤80% 바닥권)
  - KRX에는 일별 상승/하락 '종목수' 시계열 API가 없어, 일별 전종목 스냅샷으로 집계한다.
  - 집계 결과는 이 파일(market_data.json)의 adr_raw 에 하루씩 누적된다.
    최초 실행 시 BACKFILL 영업일을 백필(약 7~8분, 1회성)하고, 이후엔 빠진 날만 추가.
    → 3Y/5Y 차트는 데이터가 축적되는 만큼 점차 채워진다.

[증시자금]
  - 거래대금·상장시가총액 : KRX 지수시세(코스피 1001 / 코스닥 2001, 3년)
  - 투자자예탁금·신용융자잔고 : 금융투자협회 freesis (3년 · 2거래일 지연 발표)
  - 신용/시총 비율 : 신용융자잔고 ÷ 상장시가총액 × 100

설계 원칙: 어느 항목이 실패해도 나머지는 저장한다(부분 성공 허용).
KRX 로그인 자체가 실패하면 기존 파일을 유지하고 조용히 종료한다(exit 0).
필요 환경변수: KRX_ID, KRX_PW
"""
from __future__ import annotations
import json
import os
import re
import sys
import time
import threading
import datetime as dt

import pandas as pd
import requests

OUT_PATH   = "market_data.json"
SLEEP      = 0.4
BACKFILL   = 250          # ADR 최초 백필 영업일 수 (~1년)
ADR_KEEP   = 1300         # ADR 원자료 보존 한도 (~5년)
ADR_WIN    = 20           # ADR 산정 창(거래일)
FUND_YEARS = 3            # 증시자금 차트 기간(년)
CAL_YEARS  = 5            # 지수 달력·ADR 백필 범위(년) — 5Y 차트용

KOFIA_BASE = "https://freesis.kofia.or.kr"
KOFIA_URL  = KOFIA_BASE + "/meta/getMetaDataList.do"
KOFIA_HDR  = {
    "Content-Type": "application/json; charset=UTF-8",
    "Accept": "application/json, text/plain, */*",
    "Referer": KOFIA_BASE + "/",
    "Origin": KOFIA_BASE,
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/127.0 Safari/537.36",
}
SID_FUND   = "STATSCU0100000060"   # 증시자금추이 (투자자예탁금 = 2번째 컬럼)
SID_CREDIT = "STATSCU0100000070"   # 신용공여 잔고 (융자 전체/유가/코스닥 = 2~4번째 컬럼)


def log(*a): print(*a, file=sys.stderr, flush=True)
def ymd(d): return d.strftime("%Y%m%d")
def shift(asof, days): return ymd(dt.datetime.strptime(asof, "%Y%m%d") - dt.timedelta(days=days))


def _import_stock(retries: int = 3, wait_s: int = 40):
    """pykrx는 import 시점에 KRX 로그인을 시도한다 — 재시도로 감싼다."""
    for attempt in range(1, retries + 1):
        try:
            from pykrx import stock as _stock
            return _stock
        except Exception as e:
            log(f"[!] KRX 초기화 실패 (시도 {attempt}/{retries}): {e}")
            if attempt < retries:
                time.sleep(wait_s)
    return None


stock = _import_stock()
if stock is None:
    log("[!] KRX 로그인 반복 실패 → 기존 파일 유지, 정상 종료")
    raise SystemExit(0)


def _guard(fn, timeout_s: int = 60):
    """pykrx HTTP 호출엔 timeout이 없어 무한 대기 가능 → 스레드 하드 타임아웃."""
    box = {}
    def run():
        try:
            box["v"] = fn()
        except Exception as e:
            box["err"] = e
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        raise TimeoutError(f"{timeout_s}s 초과 (KRX 무응답)")
    if "err" in box:
        raise box["err"]
    return box.get("v")


# ----------------------- KRX 지수 (달력·거래대금·시총) -----------------------
def fetch_index(code: str, frm: str, to: str) -> pd.DataFrame | None:
    """연 단위 분할 + 콜당 60초 제한으로 지수 시세를 받는다."""
    parts, f = [], frm
    while f <= to:
        t = min(shift(f, -364), to)
        parts.append(_guard(lambda a=f, b=t: stock.get_index_ohlcv_by_date(a, b, code)))
        time.sleep(SLEEP)
        f = shift(t, -1)
    parts = [p for p in parts if p is not None and not p.empty]
    if not parts:
        return None
    df = pd.concat(parts)
    return df[~df.index.duplicated(keep="last")].sort_index()


def index_series(D: str):
    """5년치 지수시세 → 영업일 달력 + 거래대금(조) + 상장시가총액(조)."""
    frm = shift(D, CAL_YEARS * 365 + 20)
    out = {}
    for key, code in (("k", "1001"), ("q", "2001")):
        try:
            df = fetch_index(code, frm, D)
        except Exception as e:
            log(f"[!] 지수({key}) 조회 실패: {e}")
            return None
        if df is None or "거래대금" not in df.columns or "상장시가총액" not in df.columns:
            log(f"[!] 지수({key}) 컬럼 부족")
            return None
        out[key] = {
            d.strftime("%Y%m%d"): (
                round(float(r["거래대금"]) / 1e12, 2),        # 조 단위
                float(r["상장시가총액"]),                      # 원 (비율 계산용 원시값)
            )
            for d, r in df.iterrows()
        }
    dates = sorted(out["k"])
    return {
        "dates": dates,
        "vk": [out["k"][d][0] for d in dates],
        "vq": [out["q"].get(d, (None, None))[0] for d in dates],
        "mck": {d: out["k"][d][1] for d in dates},
        "mcq": {d: out["q"][d][1] for d in dates if d in out["q"]},
    }


# ----------------------- ADR (일별 상승/하락 종목수 누적) -----------------------
def adr_update(raw: dict, calendar: list) -> dict:
    """빠진 영업일의 상승/하락 종목수를 스냅샷으로 집계해 누적한다."""
    need = [d for d in calendar if d not in raw]
    # 회당 최대 BACKFILL일만 처리 (최근분 우선) — 실행 시간 폭주 방지.
    # 남은 과거분은 다음 실행이 이어받아 며칠에 걸쳐 5년치가 완성된다.
    need = need[-BACKFILL:]
    if not need:
        log("· ADR: 추가 집계할 날짜 없음")
        return raw
    log(f"· ADR 집계 {len(need)}일 (최초 백필이면 수 분 소요)…")
    consecutive_fail = 0
    for i, d in enumerate(need, 1):
        row = {}
        try:
            for mkt, (uk, dk) in (("KOSPI", ("ku", "kd")), ("KOSDAQ", ("qu", "qd"))):
                df = _guard(lambda m=mkt, dd=d: stock.get_market_ohlcv_by_ticker(dd, market=m), 45)
                time.sleep(SLEEP)
                if df is None or df.empty or "등락률" not in df.columns:
                    raise RuntimeError(f"{mkt} 스냅샷 없음")
                chg = pd.to_numeric(df["등락률"], errors="coerce")
                row[uk] = int((chg > 0).sum())
                row[dk] = int((chg < 0).sum())
            raw[d] = row
            consecutive_fail = 0
            if i % 50 == 0:
                log(f"  … {i}/{len(need)}")
        except Exception as e:
            consecutive_fail += 1
            log(f"[!] ADR {d} 실패: {e}")
            if consecutive_fail >= 3:
                log("[!] 연속 3회 실패 → ADR 수집 중단 (누적분은 유지)")
                break
    keep = sorted(raw)[-ADR_KEEP:]
    return {d: raw[d] for d in keep}


def adr_series(raw: dict):
    """누적 원자료 → 20거래일 ADR% 시계열."""
    ds = sorted(raw)
    if len(ds) < ADR_WIN + 1:
        return None
    fr = pd.DataFrame([raw[d] for d in ds], index=ds).astype(float)
    res = {"dates": [], "kospi": [], "kosdaq": []}
    for pre, (u, dn) in (("kospi", ("ku", "kd")), ("kosdaq", ("qu", "qd"))):
        up = fr[u].rolling(ADR_WIN).sum()
        down = fr[dn].rolling(ADR_WIN).sum().replace(0, pd.NA)
        adr = (up / down * 100).round(1)
        res[pre] = [None if pd.isna(v) else float(v) for v in adr]
    # 워밍업(첫 ADR_WIN-1일) 제거
    start = ADR_WIN - 1
    res["dates"] = ds[start:]
    res["kospi"] = res["kospi"][start:]
    res["kosdaq"] = res["kosdaq"][start:]
    return res


# ----------------------- 금융투자협회 (예탁금·신용잔고) -----------------------
def fetch_kofia(sid: str, frm: str, to: str) -> list:
    """freesis 통계 시계열. 반환: 화면 컬럼 순서의 값 리스트들."""
    payload = {"dmSearch": {
        "tmpV40": "1000000", "tmpV41": "1", "tmpV1": "D",
        "tmpV45": frm, "tmpV46": to, "OBJ_NM": sid + "BO",
    }}
    s = requests.Session()
    s.headers.update(KOFIA_HDR)
    s.get(KOFIA_BASE + "/", timeout=20)                 # 세션 쿠키 워밍업
    r = s.post(KOFIA_URL, data=json.dumps(payload), timeout=30)
    r.raise_for_status()
    rows = r.json().get("ds1", [])
    out = []
    for row in rows:
        keys = sorted((k for k in row if re.fullmatch(r"TMPV\d+", k)),
                      key=lambda x: int(x[4:]))
        out.append([row[k] for k in keys])
    return out


def _num(v):
    """'12,345' → 12345.0 (조 단위 환산은 호출부에서)."""
    try:
        return float(str(v).replace(",", ""))
    except Exception:
        return None


def kofia_series(D: str):
    """투자자예탁금(조) + 신용융자잔고 전체/유가/코스닥(조). 실패 시 None."""
    frm = shift(D, FUND_YEARS * 365 + 20)
    dep, mar = {}, {}
    try:
        for vals in fetch_kofia(SID_FUND, frm, D):
            if not vals or not re.fullmatch(r"\d{8}", str(vals[0]) or ""):
                continue                                  # 합계/평균 요약행 제외
            v = _num(vals[1]) if len(vals) > 1 else None  # 투자자예탁금(백만원)
            if v is not None:
                dep[str(vals[0])] = round(v / 1e6, 2)     # → 조
        time.sleep(SLEEP)
        for vals in fetch_kofia(SID_CREDIT, frm, D):
            if not vals or not re.fullmatch(r"\d{8}", str(vals[0]) or ""):
                continue
            t, k, q = (_num(vals[i]) if len(vals) > i else None for i in (1, 2, 3))
            if t is not None:
                mar[str(vals[0])] = (
                    round(t / 1e6, 2),
                    round(k / 1e6, 2) if k is not None else None,
                    round(q / 1e6, 2) if q is not None else None,
                )
    except Exception as e:
        log(f"[!] 금투협(freesis) 조회 실패: {e} → 예탁금·신용 항목 생략")
        return None
    if not dep and not mar:
        log("[!] 금투협 응답이 비었습니다 → 예탁금·신용 항목 생략")
        return None
    log(f"· 금투협: 예탁금 {len(dep)}일 · 신용잔고 {len(mar)}일")
    return {"dep": dep, "mar": mar}


# ----------------------- 조립 -----------------------
def main():
    try:
        D = stock.get_nearest_business_day_in_a_week()
    except Exception as e:
        log("[!] 기준일 조회 실패:", e, "→ 기존 파일 유지")
        return
    log("기준일:", D)

    prev = {}
    if os.path.exists(OUT_PATH):
        try:
            with open(OUT_PATH, encoding="utf-8") as f:
                prev = json.load(f)
        except Exception:
            prev = {}

    log("· 지수시세(달력·거래대금·시총)…")
    idx = index_series(D)
    if idx is None:
        log("[!] 지수시세 실패 → 기존 파일 유지, 종료")
        return

    adr_raw = adr_update(dict(prev.get("adr_raw", {})), idx["dates"])   # 5년 달력 기준
    adr = adr_series(adr_raw)

    log("· 금투협(예탁금·신용잔고)…")
    kofia = kofia_series(D)

    fn = FUND_YEARS * 252                       # 자금 차트는 최근 3년만
    dates = idx["dates"][-fn:]
    fund = {"dates": dates, "vk": idx["vk"][-fn:], "vq": idx["vq"][-fn:]}
    if kofia:
        dep, mar = kofia["dep"], kofia["mar"]
        fund["dep"] = [dep.get(d) for d in dates]
        fund["mt"] = [mar.get(d, (None,) * 3)[0] for d in dates]
        fund["mk"] = [mar.get(d, (None,) * 3)[1] for d in dates]
        fund["mq"] = [mar.get(d, (None,) * 3)[2] for d in dates]
        def ratio(m_j, mc_won):
            if m_j is None or not mc_won:
                return None
            return round(m_j * 1e12 / mc_won * 100, 2)    # 조→원 후 %
        fund["rt"] = [ratio(fund["mt"][i], (idx["mck"].get(d, 0) + idx["mcq"].get(d, 0)) or None)
                      for i, d in enumerate(dates)]
        fund["rk"] = [ratio(fund["mk"][i], idx["mck"].get(d)) for i, d in enumerate(dates)]
        fund["rq"] = [ratio(fund["mq"][i], idx["mcq"].get(d)) for i, d in enumerate(dates)]

    kst = dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
    out = {
        "updated": kst.strftime("%Y-%m-%d %H:%M"),
        "adr_raw": adr_raw,
        "fund": fund,
    }
    if adr:
        out["adr"] = adr

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    log(f"\n✓ ADR {len(adr['dates']) if adr else 0}일 · 자금 {len(dates)}일"
        f"{' · 금투협 포함' if kofia else ' · 금투협 제외'} → {OUT_PATH}")


if __name__ == "__main__":
    main()
