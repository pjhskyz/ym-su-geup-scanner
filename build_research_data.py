#!/usr/bin/env python3
"""투자 리서치 화면(시장 종합 현황·시장 히트맵·시장 수급 변화)용 파생 데이터 생성.

네트워크를 쓰지 않는다. 이미 발행된 scanner_data.json(기준일 종가·시총·WICS 업종)과
stocks_data.json(종목별 일별 OHLCV)만 읽어 research_data.json을 만든다.
따라서 원천 수집이 실패한 날에는 직전 정상본에서 다시 계산될 뿐 새 값을 지어내지 않는다.

산식은 YM 자체 정의이며 화면과 README에 그대로 적는다.
- 업종 수익률: 업종 내 종목 일간 수익률을 기준일 시가총액으로 가중 평균(과거 시총 미보유 → 근사).
- 상승 업종 비율: 당일 수익률이 0보다 큰 업종 수 ÷ 계산 가능한 업종 수.
- 상위권·나머지 격차: 업종별 20거래일 누적 수익률 상위 3개 평균 − 나머지 평균(%p).
- 쏠림 변화: 격차의 5일 평균 − 20일 평균. 양수면 상위 업종으로 쏠림이 커지는 중.
- 시총 구간 지수: 기준일 시총 1~100위 / 101~300위 / 301위 이하의 시총가중 수익률을 100에서 누적.
- 일간 수익률은 원시 종가 비율이며, 가격제한폭(±30%)을 넘는 변화는 분할·병합 등으로 보고 제외.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from pathlib import Path

SCHEMA_VERSION = 1
KST = dt.timezone(dt.timedelta(hours=9))
UNCLASSIFIED = "미분류"
GAP_WINDOW = 20
GAP_TOP_N = 3
# KRX 일일 가격제한폭은 ±30%. 이를 넘는 종가 변화는 액면분할·병합·신규상장 첫날 등
# 수정주가가 아닌 원시 종가에서 생기는 불연속으로 보고 업종·구간 수익률 계산에서 뺀다.
MAX_DAILY_MOVE = 0.305
SIZE_BUCKETS = (("large", "대형주", 1, 100), ("mid", "중형주", 101, 300), ("small", "소형주", 301, None))

# scanner_data.json rows 열 위치 (index.html KEYS와 동일)
COL_NAME, COL_SECTOR, COL_R1, COL_R5, COL_R20, COL_CODE, COL_MCAP = 0, 1, 20, 21, 22, 23, 27
CLOSE_FIELD = 3  # stocks_data.json c[code] = [시가, 고가, 저가, 종가, 거래량]


def is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def rounded(value, digits=4):
    return round(value, digits) if is_number(value) else None


def daily_returns(closes: list) -> list:
    """종가 배열 → 일간 수익률 배열(첫날·결측·0 이하 가격은 None)."""
    out = [None] * len(closes)
    for i in range(1, len(closes)):
        prev, cur = closes[i - 1], closes[i]
        if is_number(prev) and is_number(cur) and prev > 0 and cur > 0:
            change = cur / prev - 1
            out[i] = change if abs(change) <= MAX_DAILY_MOVE else None
    return out


def weighted_mean(pairs) -> float | None:
    """[(값, 가중치)] → 가중 평균. 유효한 쌍이 없으면 None."""
    total = weight_sum = 0.0
    for value, weight in pairs:
        if is_number(value) and is_number(weight) and weight > 0:
            total += value * weight
            weight_sum += weight
    return total / weight_sum if weight_sum > 0 else None


def trailing_mean(values: list, end: int, window: int) -> float | None:
    """values[end-window+1 .. end] 평균. 구간에 결측이 하나라도 있으면 None."""
    if end - window + 1 < 0:
        return None
    chunk = values[end - window + 1:end + 1]
    return sum(chunk) / window if all(is_number(v) for v in chunk) else None


def cumulative_return(returns: list, end: int, window: int) -> float | None:
    """returns[end-window+1 .. end] 누적 수익률. 결측이 있으면 None."""
    if end - window + 1 < 0:
        return None
    level = 1.0
    for value in returns[end - window + 1:end + 1]:
        if not is_number(value):
            return None
        level *= 1 + value
    return level - 1


def universe_from_scanner(scanner: dict) -> list:
    """스캐너 수록 종목 → [{code,name,sector,mcap,r1,r5,r20}] (시총 내림차순)."""
    items = []
    for row in scanner.get("rows") or []:
        if not isinstance(row, list) or len(row) <= COL_MCAP:
            continue
        code, mcap = row[COL_CODE], row[COL_MCAP]
        if not isinstance(code, str) or not is_number(mcap) or mcap <= 0:
            continue
        items.append({
            "code": code, "name": row[COL_NAME], "sector": row[COL_SECTOR] or UNCLASSIFIED, "mcap": mcap,
            "r1": row[COL_R1] if is_number(row[COL_R1]) else None,
            "r5": row[COL_R5] if is_number(row[COL_R5]) else None,
            "r20": row[COL_R20] if is_number(row[COL_R20]) else None,
        })
    items.sort(key=lambda item: (-item["mcap"], item["code"]))
    return items


def sector_summary(universe: list) -> list:
    """업종별 시총가중 1일·1주·1개월 등락률과 상승/하락 종목 수."""
    groups: dict[str, list] = {}
    for item in universe:
        groups.setdefault(item["sector"], []).append(item)
    out = []
    for name, members in groups.items():
        r1s = [m["r1"] for m in members if is_number(m["r1"])]
        out.append({
            "name": name, "count": len(members), "cap": round(sum(m["mcap"] for m in members)),
            "r1": rounded(weighted_mean((m["r1"], m["mcap"]) for m in members), 2),
            "r5": rounded(weighted_mean((m["r5"], m["mcap"]) for m in members), 2),
            "r20": rounded(weighted_mean((m["r20"], m["mcap"]) for m in members), 2),
            "up": sum(1 for v in r1s if v > 0), "down": sum(1 for v in r1s if v < 0),
            "flat": sum(1 for v in r1s if v == 0),
        })
    out.sort(key=lambda s: (s["name"] == UNCLASSIFIED, -s["cap"]))
    return out


def stock_return_table(universe: list, stocks: dict) -> dict:
    """code → 일간 수익률 배열. 이력이 없거나 길이가 다른 종목은 제외."""
    n = len(stocks.get("dates") or [])
    table = {}
    history = stocks.get("c") or {}
    for item in universe:
        fields = history.get(item["code"])
        if not isinstance(fields, list) or len(fields) <= CLOSE_FIELD:
            continue
        closes = fields[CLOSE_FIELD]
        if isinstance(closes, list) and len(closes) == n:
            table[item["code"]] = daily_returns(closes)
    return table


def group_return_series(members: list, table: dict, n: int) -> list:
    """구성 종목의 시총가중 일간 수익률 시계열."""
    return [weighted_mean((table[m["code"]][t], m["mcap"]) for m in members if m["code"] in table) for t in range(n)]


def breadth_series(universe: list, stocks: dict) -> dict:
    dates = list(stocks.get("dates") or [])
    n = len(dates)
    table = stock_return_table(universe, stocks)
    groups: dict[str, list] = {}
    for item in universe:
        if item["sector"] != UNCLASSIFIED:
            groups.setdefault(item["sector"], []).append(item)
    sector_returns = {name: group_return_series(members, table, n) for name, members in groups.items()}

    up_sectors, up_sector_ratio, up_stock_ratio, gap = [], [], [], []
    for t in range(n):
        valid = [series[t] for series in sector_returns.values() if is_number(series[t])]
        ups = sum(1 for v in valid if v > 0)
        up_sectors.append(ups if valid else None)
        up_sector_ratio.append(rounded(ups / len(valid) * 100, 1) if valid else None)
        moves = [returns[t] for returns in table.values() if is_number(returns[t])]
        up_stock_ratio.append(rounded(sum(1 for v in moves if v > 0) / len(moves) * 100, 1) if moves else None)
        window = [cumulative_return(series, t, GAP_WINDOW) for series in sector_returns.values()]
        window = sorted((v for v in window if is_number(v)), reverse=True)
        if len(window) > GAP_TOP_N:
            top, rest = window[:GAP_TOP_N], window[GAP_TOP_N:]
            gap.append(rounded((sum(top) / len(top) - sum(rest) / len(rest)) * 100, 2))
        else:
            gap.append(None)
    oscillator = []
    for t in range(n):
        short, long_ = trailing_mean(gap, t, 5), trailing_mean(gap, t, 20)
        oscillator.append(rounded(short - long_, 3) if is_number(short) and is_number(long_) else None)
    return {
        "dates": dates, "n_sectors": len(groups), "covered_stocks": len(table),
        "up_sectors": up_sectors, "up_sector_ratio": up_sector_ratio, "up_stock_ratio": up_stock_ratio,
        "gap": gap, "oscillator": oscillator,
        "definition": {
            "sector_return": "업종 내 종목 일간 수익률의 기준일 시총가중 평균(과거 시총 미보유 근사)",
            "gap": f"업종별 {GAP_WINDOW}거래일 누적 수익률 상위 {GAP_TOP_N}개 평균 − 나머지 평균(%p)",
            "oscillator": "격차의 5일 평균 − 20일 평균",
        },
    }


def size_series(universe: list, stocks: dict) -> dict:
    dates = list(stocks.get("dates") or [])
    n = len(dates)
    table = stock_return_table(universe, stocks)
    buckets = []
    for key, label, start, end in SIZE_BUCKETS:
        members = universe[start - 1:end]
        returns = group_return_series(members, table, n)
        level, levels = 100.0, []
        for t, value in enumerate(returns):
            if t and is_number(value):
                level *= 1 + value
            levels.append(round(level, 2))
        last = next((v for v in reversed(returns) if is_number(v)), None)
        buckets.append({
            "key": key, "label": label, "count": len(members),
            "rank_from": start, "rank_to": end if end else len(universe),
            "r1": rounded(last * 100, 2) if is_number(last) else None, "levels": levels,
        })
    return {"dates": dates, "base": 100, "buckets": buckets,
            "definition": "스캐너 수록 종목을 기준일 시총 순위로 나눈 시총가중 누적 지수(첫 관측일=100). KRX 규모별 지수와 구성·산식이 다릅니다."}


def build(scanner: dict, stocks: dict, now: dt.datetime | None = None) -> dict:
    universe = universe_from_scanner(scanner)
    if not universe:
        raise ValueError("scanner_data.json에 사용할 수 있는 종목이 없습니다.")
    dates = stocks.get("dates") or []
    if len(dates) < 2:
        raise ValueError("stocks_data.json의 일별 이력이 부족합니다.")
    asof, history_asof = str(scanner.get("asof") or ""), str(dates[-1])
    now = now or dt.datetime.now(KST)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.astimezone(KST).isoformat(timespec="seconds"),
        "asof": asof,
        "history_asof": history_asof,
        "dates_match": asof == history_asof,
        "source": {
            "scanner_updated": scanner.get("updated"), "stocks_updated": stocks.get("updated"),
            "price_basis": "KRX 정규장 일별 종가(pykrx)", "sector_basis": "FnGuide WICS 대분류(스캐너 수록 분류)",
            "universe": "KOSPI·KOSDAQ 시가총액 500억원 이상 스캐너 수록 종목",
        },
        "heatmap": {
            "columns": ["code", "name", "sector", "mcap", "r1", "r5", "r20"],
            "rows": [[u["code"], u["name"], u["sector"], u["mcap"], u["r1"], u["r5"], u["r20"]] for u in universe],
        },
        "sectors": sector_summary(universe),
        "size": size_series(universe, stocks),
        "breadth": breadth_series(universe, stocks),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scanner", default="scanner_data.json")
    parser.add_argument("--stocks", default="stocks_data.json")
    parser.add_argument("--output", default="research_data.json")
    args = parser.parse_args(argv)
    try:
        scanner = json.loads(Path(args.scanner).read_text(encoding="utf-8"))
        stocks = json.loads(Path(args.stocks).read_text(encoding="utf-8"))
        result = build(scanner, stocks)
    except (OSError, ValueError) as error:
        # 실패 시 기존 research_data.json을 그대로 둔다(빈 파일·부분 파일을 쓰지 않음).
        print(f"research_data 생성 실패: {error}", file=sys.stderr)
        return 1
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    breadth = result["breadth"]
    print(f"research_data.json 생성: 기준일 {result['asof']} · 종목 {len(result['heatmap']['rows'])} · "
          f"업종 {breadth['n_sectors']} · 이력 {len(breadth['dates'])}일", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
