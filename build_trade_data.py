#!/usr/bin/env python3
"""수출입 분석 화면용 품목별 월별 수출입 실적 수집 → trade_data.json.

원천: 관세청 품목별 수출입실적(공공데이터포털, 무료·인증키 필요). 매월 15일경 전월 실적이 올라온다.
인증키는 환경변수 DATA_GO_KR_KEY로 받는다. 키가 없으면 아무 파일도 만들지 않고 정상 종료한다.
품목 하나가 실패해도 나머지는 계속 받고, 실패한 품목은 직전 보관값을 유지한다(0으로 채우지 않음).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

SCHEMA_VERSION = 1
KST = dt.timezone(dt.timedelta(hours=9))
API_URL = "https://apis.data.go.kr/1220000/Itemtrade/getItemtradeList"
HISTORY_MONTHS = 61      # 5년 + 전년 동월 비교용 1개월
REFRESH_MONTHS = 3       # 이미 받은 품목은 최근 3개월만 다시 받아 정정치를 반영
CHUNK_MONTHS = 12        # API 조회기간 제한(1년 이내)
FIELDS = ("exp", "imp", "exp_wgt", "imp_wgt")
XML_FIELDS = {"exp": "expDlr", "imp": "impDlr", "exp_wgt": "expWgt", "imp_wgt": "impWgt"}


def month_add(yyyymm: str, delta: int) -> str:
    index = int(yyyymm[:4]) * 12 + int(yyyymm[4:]) - 1 + delta
    return f"{index // 12:04d}{index % 12 + 1:02d}"


def month_range(start: str, end: str) -> list:
    out, cur = [], start
    while cur <= end:
        out.append(cur)
        cur = month_add(cur, 1)
    return out


def chunks(start: str, end: str, size: int = CHUNK_MONTHS) -> list:
    """[start, end]를 size개월 이하 구간으로 나눈다."""
    months = month_range(start, end)
    return [(months[i], months[min(i + size, len(months)) - 1]) for i in range(0, len(months), size)]


def normalize_key(key: str) -> str:
    """포털의 Encoding 키(%2B 등 포함)가 들어와도 한 번만 인코딩되도록 원문으로 되돌린다."""
    key = (key or "").strip()
    return urllib.parse.unquote(key) if "%" in key else key


def to_number(text) -> float | None:
    try:
        value = float(str(text).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return value if value == value else None


def parse_response(xml_text: str, hs: str) -> dict:
    """응답 XML → {YYYYMM: {exp, imp, exp_wgt, imp_wgt}}.

    요청 코드와 hsCd가 정확히 같은 행이 있으면 그 행만 쓰고, 없으면 하위 코드 행을 월별로 합산한다
    (집계 행과 세부 행이 함께 올 때 이중 합산을 피한다). '총계' 행은 버린다.
    """
    root = ET.fromstring(xml_text)
    code = (root.findtext(".//resultCode") or root.findtext(".//returnReasonCode") or "").strip()
    if code not in ("00", "0"):
        message = (root.findtext(".//resultMsg") or root.findtext(".//returnAuthMsg") or root.findtext(".//errMsg") or "").strip()
        raise ValueError(f"API 오류 {code or '?'}: {message or '응답에 결과 코드가 없습니다'}")
    exact, detail = {}, {}
    for item in root.iter("item"):
        period = (item.findtext("year") or "").strip().replace(".", "")
        if len(period) != 6 or not period.isdigit():
            continue
        row_hs = (item.findtext("hsCd") or "").strip()
        if row_hs and not row_hs.startswith(hs):
            continue
        values = {field: to_number(item.findtext(tag)) for field, tag in XML_FIELDS.items()}
        target = exact if row_hs == hs or not row_hs else detail
        slot = target.setdefault(period, dict.fromkeys(FIELDS))
        for field, value in values.items():
            if value is not None:
                slot[field] = (slot[field] or 0) + value
    return exact or detail


def fetch(key: str, hs: str, start: str, end: str, timeout: int = 30, retries: int = 2) -> dict:
    query = urllib.parse.urlencode({"serviceKey": key, "strtYymm": start, "endYymm": end, "hsSgn": hs})
    last_error = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(f"{API_URL}?{query}", timeout=timeout) as response:
                return parse_response(response.read().decode("utf-8", "replace"), hs)
        except (urllib.error.URLError, TimeoutError, ET.ParseError) as error:
            last_error = error
            time.sleep(2 * (attempt + 1))
    raise ValueError(f"요청 실패: {last_error}")


def merge_series(previous: dict | None, fresh: dict, months: list) -> dict:
    """보관 시계열에 새 값을 덮어쓴다. 새 응답에 없는 달은 보관값을 유지하고, 둘 다 없으면 None."""
    stored = {}
    if previous:
        for i, month in enumerate(previous.get("months") or []):
            stored[month] = {field: (previous.get(field) or [])[i] if i < len(previous.get(field) or []) else None for field in FIELDS}
    for month, values in fresh.items():
        slot = stored.setdefault(month, dict.fromkeys(FIELDS))
        for field in FIELDS:
            if values.get(field) is not None:
                slot[field] = values[field]
    out = {"months": months}
    for field in FIELDS:
        out[field] = [stored.get(month, {}).get(field) for month in months]
    return out


def latest_month(now: dt.datetime) -> str:
    """수집 대상 마지막 월: 15일 이후면 전월, 그 전이면 전전월(전월 실적은 15일경 공표)."""
    current = f"{now.year:04d}{now.month:02d}"
    return month_add(current, -1 if now.day >= 15 else -2)


def scrub(text: str, key: str) -> str:
    """오류 문구에 인증키가 섞여 들어가도 파일·로그에 남지 않게 가린다."""
    for secret in {key, urllib.parse.quote(key, safe="")}:
        if secret:
            text = text.replace(secret, "***")
    return text[:200]


def status_doc(result: dict, now: dt.datetime) -> dict:
    """최근 수집 시도 요약(성공 여부·최신월·실패 품목). 실패해도 남겨 원인을 확인할 수 있게 한다."""
    return {
        "attempted_at": now.astimezone(KST).isoformat(timespec="seconds"),
        "ok": bool(result["latest_month"]),
        "latest_month": result["latest_month"],
        "items": len(result["items"]),
        "failed": len(result["errors"]),
        "errors": result["errors"][:5],
    }


def build(items: list, previous: dict | None, key: str, now: dt.datetime, fetcher=None, pause: float = 0.2) -> dict:
    fetcher = fetcher or fetch
    end = latest_month(now)
    months = month_range(month_add(end, -(HISTORY_MONTHS - 1)), end)
    old = {item["id"]: item for item in (previous or {}).get("items") or []}
    out_items, errors = [], []
    for item in items:
        before = old.get(item["id"]) if old.get(item["id"], {}).get("hs") == item["hs"] else None
        has_history = bool(before and any(v is not None for v in before.get("exp") or []))
        start = month_add(end, -(REFRESH_MONTHS - 1)) if has_history else months[0]
        fresh, failed = {}, None
        for chunk_start, chunk_end in chunks(start, end):
            try:
                fresh.update(fetcher(key, item["hs"], chunk_start, chunk_end))
            except ValueError as error:
                failed = str(error)
                break
            if pause:
                time.sleep(pause)
        if failed:
            errors.append({"id": item["id"], "hs": item["hs"], "error": scrub(failed, key)})
        series = merge_series(before, fresh, months)
        out_items.append({"id": item["id"], "label": item["label"], "hs": item["hs"], "stocks": item.get("stocks") or [], **series})
    observed = [m for it in out_items for m, v in zip(it["months"], it["exp"]) if v is not None]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.astimezone(KST).isoformat(timespec="seconds"),
        "target_month": end,
        "latest_month": max(observed) if observed else None,
        "source": {"provider": "관세청 품목별 수출입실적(공공데이터포털)", "basis": "통관 기준 · 금액 USD · 중량 kg · 매월 15일경 전월 실적 공표, 이후 정정 가능",
                   "url": "https://www.data.go.kr/"},
        "errors": errors,
        "items": out_items,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--items", default="trade_pipeline/items.json")
    parser.add_argument("--output", default="trade_data.json")
    parser.add_argument("--status", default="trade_status.json")
    args = parser.parse_args(argv)
    key = normalize_key(os.environ.get("DATA_GO_KR_KEY", ""))
    if not key:
        print("DATA_GO_KR_KEY가 없어 수출입 수집을 건너뜁니다(파일을 만들지 않음).", file=sys.stderr)
        return 0
    items = json.loads(Path(args.items).read_text(encoding="utf-8")).get("items") or []
    output = Path(args.output)
    previous = json.loads(output.read_text(encoding="utf-8")) if output.exists() else None
    now = dt.datetime.now(KST)
    result = build(items, previous, key, now)
    Path(args.status).write_text(json.dumps(status_doc(result, now), ensure_ascii=False, indent=1), encoding="utf-8")
    if not result["latest_month"]:
        # 한 건도 받지 못했으면 기존 파일을 지키고 실패로 끝낸다.
        for error in result["errors"][:5]:
            print(f"  {error['id']}({error['hs']}): {error['error']}", file=sys.stderr)
        print("수출입 실적을 한 건도 받지 못했습니다. 인증키와 활용신청 상태를 확인하세요.", file=sys.stderr)
        return 1
    output.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"trade_data.json 생성: 최신월 {result['latest_month']} · 품목 {len(result['items'])} · 실패 {len(result['errors'])}", file=sys.stderr)
    for error in result["errors"]:
        print(f"  실패 {error['id']}({error['hs']}): {error['error']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
