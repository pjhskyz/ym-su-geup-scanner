#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
이격도 전용 경량 빌더 — disparity_data.json 생성
=========================================================
코스피(1001)·코스닥(2001) 50일 이격도(현재가 ÷ 50일 이평 × 100) 시계열만 갱신한다.
매 거래일 12:00 / 15:40 KST 실행용 — 장중엔 당일 '현재가' 기준 이격도가 된다.

- 연 단위 분할 요청 + 콜당 60초 하드 타임아웃 (pykrx는 HTTP 타임아웃이 없음)
- 어떤 실패든 파일을 건드리지 않고 종료(exit 0) → 직전 데이터 유지
  (저녁 메인 빌드의 scanner_data.json 내 disparity 가 백업 역할)
- 실행 시간: 1분 미만 (지수 호출 6회)

필요 환경변수: KRX_ID, KRX_PW
"""
from __future__ import annotations
import json
import sys
import time
import threading
import datetime as dt

import pandas as pd

OUT_PATH   = "disparity_data.json"
SLEEP      = 0.4
DISP_MA    = 50     # 이동평균 일수
DISP_YEARS = 2      # 차트 보존 기간(년)


def log(*a): print(*a, file=sys.stderr, flush=True)
def ymd(d): return d.strftime("%Y%m%d")
def shift(asof, days): return ymd(dt.datetime.strptime(asof, "%Y%m%d") - dt.timedelta(days=days))


def _import_stock(retries: int = 3, wait_s: int = 40):
    """pykrx는 import 시점에 KRX 로그인을 시도한다. 주말 점검·일시 장애로
    로그인 응답이 깨지면 import 자체가 예외를 던지므로 재시도로 감싼다."""
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
    log("[!] KRX 로그인 반복 실패(점검·일시 장애 추정) → 기존 파일 유지, 정상 종료")
    raise SystemExit(0)


def _fetch_index(code: str, frm: str, to: str, timeout_s: int = 60):
    """지수 OHLCV를 스레드로 감싸 하드 타임아웃 적용."""
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
    """코스피·코스닥 50일 이격도 시계열. 실패 시 None."""
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
            log(f"[!] 지수({key}) 조회 실패: {e}")
            return None
        parts = [p for p in parts if p is not None and not p.empty]
        if not parts:
            log(f"[!] 지수({key}) 데이터 없음")
            return None
        df = pd.concat(parts)
        df = df[~df.index.duplicated(keep="last")].sort_index()
        if "종가" not in df.columns or len(df) < DISP_MA + 5:
            log(f"[!] 지수({key}) 데이터 부족")
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


def main():
    try:
        D = stock.get_nearest_business_day_in_a_week()
    except Exception as e:
        log("[!] 기준일 조회 실패:", e, "→ 기존 파일 유지")
        return
    log("기준일:", D)

    disp = index_disparity(D)
    if not disp:
        log("[!] 이격도 생성 실패 → 기존 파일 유지 (저녁 메인 빌드가 백업)")
        return

    kst = dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
    disp["updated"] = kst.strftime("%Y-%m-%d %H:%M")

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(disp, f, ensure_ascii=False)
    log(f"\n✓ 이격도 {len(disp['dates'])}일 → {OUT_PATH} (갱신 {disp['updated']} KST)")


if __name__ == "__main__":
    main()
