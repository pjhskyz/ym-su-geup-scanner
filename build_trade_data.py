#!/usr/bin/env python3
"""수출입 분석 화면용 관세청 수출입 통계 수집 → trade_data.json.

원천(공공데이터포털, 무료·인증키 하나를 함께 씀):
- 품목별 수출입실적: 추적 품목의 월별 수출·수입(금액·중량). 매월 15일경 전월 실적이 올라온다.
- 품목별 국가별 수출입실적: 추적 품목이 어느 나라로 나가고 어디서 들어오는지.
- 국가별 수출입실적: 국가·지역별 전체 수출입.
- 수출 주요품목별·주요국가별 10일 단위 잠정치: 1~10일, 1~20일, 월 전체 수출(11일·21일·익월 1일 공표).
인증키는 환경변수 DATA_GO_KR_KEY로 받는다. 키가 없으면 아무 파일도 만들지 않고 정상 종료한다.
품목·구역 하나가 실패해도 나머지는 계속 받고, 실패한 부분은 직전 보관값을 유지한다(값을 지어내지 않음).
"""
from __future__ import annotations

import argparse
import datetime as dt
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

SCHEMA_VERSION = 2
KST = dt.timezone(dt.timedelta(hours=9))
API_URL = "https://apis.data.go.kr/1220000/Itemtrade/getItemtradeList"
ITEM_COUNTRY_URL = "https://apis.data.go.kr/1220000/nitemtrade/getNitemtradeList"
NATION_URL = "https://apis.data.go.kr/1220000/nationtrade/getNationtradeList"
PRELIM_URLS = {
    "item": "https://apis.data.go.kr/1220000/prlstMmUtPrviExpAcrs/getPrlstMmUtPrviExpAcrs",
    "country": "https://apis.data.go.kr/1220000/cntyMmUtPrviExpAcrs/getCntyMmUtPrviExpAcrs",
}
# 잠정치 응답의 금액 칸(itemUsdAmt00, 01, …) 순서. 관세청 명세의 제공 범위 순서와 같다.
PRELIM_NAMES = {
    "item": ["전체", "반도체", "철강제품", "승용차", "석유제품", "무선통신기기", "선박", "자동차부품", "컴퓨터 주변기기", "정밀기기", "가전제품"],
    "country": ["전체", "중국", "미국", "유럽연합", "베트남", "홍콩", "일본", "대만", "인도", "싱가포르", "말레이시아"],
}
PRELIM_SPANS = ("d10", "d20", "m")   # 1~10일 · 1~20일 · 1일~말일 누계
PRELIM_VALUE = re.compile(r"^itemUsdAmt(\d{1,2})$")
PRELIM_REFRESH_MONTHS = 13   # 전년 동기 비교 기준까지 다시 받아 정정치를 반영
HISTORY_MONTHS = 61      # 5년 + 전년 동월 비교용 1개월
REFRESH_MONTHS = 3       # 이미 받은 품목은 최근 3개월만 다시 받아 정정치를 반영
CHUNK_MONTHS = 12        # API 조회기간 제한(1년 이내)
COUNTRY_MONTHS = 24      # 품목별 국가 분해: 최근월 YoY와 12개월 누적 YoY에 필요한 기간
COUNTRY_TOP = 6          # 품목마다 수출·수입 각각 보관할 상위 국가 수(최근 12개월 누적 기준)
NATION_TOP = 40          # 국가·지역 화면에 보관할 상위 국가 수(최근 12개월 수출+수입 기준)
STALE_DAYS = 7           # 국가별 확정치는 새 달이 들어온 뒤에는 이 간격으로만 다시 받는다
COUNTRY_GIVE_UP = 3      # 품목별 국가 분해가 이만큼 연달아 실패하면 그 회차에서는 중단
PAGE_SIZE = 1000
MAX_PAGES = 60
OK_CODES = ("00", "0", "000")
FIELDS = ("exp", "imp", "exp_wgt", "imp_wgt")
FLOWS = ("exp", "imp")
XML_FIELDS = {"exp": "expDlr", "imp": "impDlr", "exp_wgt": "expWgt", "imp_wgt": "impWgt"}
# 국가별 실적에는 지역 합계가 없어 회원국을 직접 더한다(ISO 2자리 코드).
REGIONS = [
    {"code": "EU27", "name": "유럽연합(27개국 합산)", "members": ["AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE"]},
    {"code": "ASEAN10", "name": "아세안(10개국 합산)", "members": ["BN", "KH", "ID", "LA", "MY", "MM", "PH", "SG", "TH", "VN"]},
]


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


def result_of(root) -> tuple:
    """응답의 (결과 코드, 문구). 정상 응답과 게이트웨이 오류 응답의 항목명이 달라 둘 다 본다."""
    code = (root.findtext(".//resultCode") or root.findtext(".//returnReasonCode") or "").strip()
    message = (root.findtext(".//resultMsg") or root.findtext(".//returnAuthMsg") or root.findtext(".//errMsg") or "").strip()
    return code, message


def parse_response(xml_text: str, hs: str) -> dict:
    """응답 XML → {YYYYMM: {exp, imp, exp_wgt, imp_wgt}}.

    요청 코드와 hsCd가 정확히 같은 행이 있으면 그 행만 쓰고, 없으면 하위 코드 행을 월별로 합산한다
    (집계 행과 세부 행이 함께 올 때 이중 합산을 피한다). '총계' 행은 버린다.
    """
    root = ET.fromstring(xml_text)
    code, message = result_of(root)
    if code not in ("00", "0"):
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


def parse_rows(xml_text: str) -> tuple:
    """응답 XML → (item 행 목록[{항목명: 값}], totalCount 또는 None). 자료 없음(03)은 빈 목록."""
    root = ET.fromstring(xml_text)
    code, message = result_of(root)
    if code == "03" or "NODATA" in message.upper().replace("_", "").replace(" ", ""):
        return [], 0
    if code not in OK_CODES:
        raise ValueError(f"API 오류 {code or '?'}: {message or '응답에 결과 코드가 없습니다'}")
    rows = [{child.tag: (child.text or "").strip() for child in item} for item in root.iter("item")]
    total = (root.findtext(".//totalCount") or "").strip()
    return rows, int(total) if total.isdigit() else None


def request_page(url: str, key: str, params: dict, timeout: int = 60, retries: int = 2) -> tuple:
    query = urllib.parse.urlencode({"serviceKey": key, **params})
    last_error = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(f"{url}?{query}", timeout=timeout) as response:
                return parse_rows(response.read().decode("utf-8", "replace"))
        except (OSError, http.client.HTTPException, ET.ParseError) as error:   # 연결 끊김·시간 초과·깨진 응답
            last_error = error
            time.sleep(2 * (attempt + 1))
    raise ValueError(f"요청 실패: {last_error}")


def request_rows(url: str, key: str, params: dict, pager=None) -> list:
    """한 조회의 행 전체. 쪽 나눔을 하는 API면 끝까지 이어 받고, 한 번에 다 주는 API면 첫 응답으로 끝난다.

    totalCount가 없을 때는 정확히 한 쪽 분량이 온 경우에만 다음 쪽을 확인한다. 같은 내용이 다시 오면
    쪽 나눔을 무시하는 API로 보고 멈춘다(같은 행을 두 번 세지 않는다).
    """
    pager = pager or request_page
    rows, page, previous = [], 1, None
    while True:
        got, total = pager(url, key, {**params, "numOfRows": PAGE_SIZE, "pageNo": page})
        if page > 1 and got == previous:
            break
        rows.extend(got)
        done = len(rows) >= total if total is not None else len(got) != PAGE_SIZE
        if not got or done:
            break
        if page >= MAX_PAGES:
            raise ValueError(f"응답이 너무 깁니다: {len(rows)}행에서 중단")
        previous, page = got, page + 1
    if total is not None and len(rows) < total:
        raise ValueError(f"응답이 잘렸습니다: 전체 {total}행 중 {len(rows)}행")
    return rows


def to_amount(text):
    """금액 → 수. 정수로 떨어지면 int로 둔다(파일 크기)."""
    value = to_number(text)
    return int(value) if value is not None and value == int(value) else value


def period_month(text) -> str | None:
    """'2026.10' · '202610' → '202610'. '총계' 같은 합계 행은 None."""
    digits = re.sub(r"\D", "", str(text or ""))
    return digits if len(digits) == 6 else None


def stale(section: dict | None, end: str, now: dt.datetime) -> bool:
    """국가별 확정치를 다시 받을 때인지. 목표 월이 아직 안 들어왔으면 매번, 들어온 뒤에는 STALE_DAYS마다."""
    if not section or section.get("latest_month") != end:
        return True
    try:
        return (now - dt.datetime.fromisoformat(section.get("fetched_at") or "")).days >= STALE_DAYS
    except (TypeError, ValueError):
        return True


# ---- 10일 단위 잠정치 ----
def prelim_period(row: dict) -> tuple | None:
    """잠정치 한 행의 (YYYYMM, 구간). priodMon이 6자리이거나 priodYear+priodMon으로 나뉘어 와도 읽는다."""
    month = re.sub(r"\D", "", row.get("priodMon") or "")
    year = re.sub(r"\D", "", row.get("priodYear") or "")
    if len(month) != 6:
        month = year + month.zfill(2) if len(year) == 4 and 1 <= len(month) <= 2 else ""
    if len(month) != 6 or not 1 <= int(month[4:]) <= 12:
        return None
    text = row.get("priodDt") or ""
    days = re.findall(r"\d+", text)
    last = 31 if "말" in text else int(days[-1]) % 100 if days else 0
    span = "d10" if last == 10 else "d20" if last == 20 else "m" if last >= 28 else None
    return (month, span) if span else None


def parse_prelim(rows: list) -> dict:
    """잠정치 행 → {YYYYMM: {구간: [칸 순서대로 금액(USD)]}}. 원자료 단위 천 달러를 달러로 바꾼다."""
    out, tags = {}, set()
    for row in rows:
        tags.update(row)
        period = prelim_period(row)
        values = {int(match.group(1)): to_number(text) for tag, text in row.items() if (match := PRELIM_VALUE.match(tag))}
        if not period or not values:
            continue
        out.setdefault(period[0], {})[period[1]] = [None if values.get(i) is None else to_amount(values[i] * 1000) for i in range(max(values) + 1)]
    if rows and not out:
        raise ValueError("잠정치 응답 항목을 해석하지 못했습니다: " + ", ".join(sorted(tags))[:150])
    return out


def merge_prelim(before: list | None, before_months: list, fresh: dict, months: list, names: list) -> list:
    """보관 잠정치에 새 값을 덮어쓴다. 새 응답에 없는 값은 보관값을 유지하고, 둘 다 없으면 None."""
    width = max([len(names), len(before or [])] + [len(values) for spans in fresh.values() for values in spans.values()])
    out = []
    for index in range(width):
        old = (before or [])[index] if index < len(before or []) else {}
        row = {"name": names[index] if index < len(names) else f"항목 {index:02d}"}
        for span in PRELIM_SPANS:
            stored = dict(zip(before_months, old.get(span) or []))
            for month, spans in fresh.items():
                values = spans.get(span) or []
                if index < len(values) and values[index] is not None:
                    stored[month] = values[index]
            row[span] = [stored.get(month) for month in months]
        out.append(row)
    return out


def prelim_latest(rows: list, months: list) -> dict | None:
    """'전체' 칸에 값이 있는 가장 최근 (월, 구간)."""
    total = rows[0] if rows else {}
    for index in range(len(months) - 1, -1, -1):
        for span in reversed(PRELIM_SPANS):
            if index < len(total.get(span) or []) and total[span][index] is not None:
                return {"month": months[index], "span": span}
    return None


def build_prelim(previous: dict | None, key: str, now: dt.datetime, rows_of, pause: float = 0.2) -> tuple:
    """주요 품목·주요 국가 수출 잠정치 구역과 오류 목록. 달력상 이번 달까지 본다."""
    end = f"{now.year:04d}{now.month:02d}"
    months = month_range(month_add(end, -(HISTORY_MONTHS - 1)), end)
    before_months = (previous or {}).get("months") or []
    section, errors = {"months": months, "fetched_at": now.astimezone(KST).isoformat(timespec="seconds")}, []
    for kind, url in PRELIM_URLS.items():
        before = (previous or {}).get(kind)
        has_history = any(v is not None for row in before or [] for span in PRELIM_SPANS for v in row.get(span) or [])
        fresh = {}
        try:
            for chunk_start, chunk_end in chunks(month_add(end, -(PRELIM_REFRESH_MONTHS - 1)) if has_history else months[0], end):
                fresh.update(parse_prelim(rows_of("prelim_" + kind, url, {"strtYymm": chunk_start, "endYymm": chunk_end})))
                if pause:
                    time.sleep(pause)
        except ValueError as error:
            errors.append({"section": "prelim_" + kind, "error": str(error)})
        section[kind] = merge_prelim(before, before_months, fresh, months, PRELIM_NAMES[kind])
    section["latest"] = prelim_latest(section["item"], months) or prelim_latest(section["country"], months)
    return section, errors


# ---- 국가별 확정치 ----
def country_table(rows: list, hs: str | None = None) -> tuple:
    """국가별 행 → ({국가코드: {name, exp: {월: 금액}, imp: {월: 금액}}}, 응답에 실적이 있는 월 집합).

    hs를 주면 품목별 국가별 응답으로 보고, 요청 코드와 hsCd가 같은 행이 있으면 그 행만,
    없으면 가장 세분된 단위의 하위 코드 행만 더한다(집계 행과 세부 행을 함께 세지 않는다).
    국가코드가 없는 합계 행은 버리고 전체는 국가 행을 더해 구한다.
    """
    picked = []
    for row in rows:
        month, country = period_month(row.get("year")), (row.get("statCd") or "").strip().upper()
        code = re.sub(r"\D", "", row.get("hsCd") or "")
        if not month or not country or country == "-" or (hs and code and not code.startswith(hs)):
            continue
        picked.append((month, country, code, row))
    if hs:
        exact = [entry for entry in picked if entry[2] == hs or not entry[2]]
        depth = max((len(entry[2]) for entry in picked), default=0)
        picked = exact or [entry for entry in picked if len(entry[2]) == depth]
    table, published = {}, set()
    for month, country, _, row in picked:
        slot = table.setdefault(country, {"name": (row.get("statCdCntnKor1") or "").strip() or country, "exp": {}, "imp": {}})
        for flow in FLOWS:
            value = to_number(row.get(XML_FIELDS[flow]))
            if value is not None:
                slot[flow][month] = slot[flow].get(month, 0) + value
                published.add(month)
    return table, published


def country_series(table: dict, codes, flow: str, months: list, published: set) -> list:
    """codes에 든 국가의 월별 합. 실적이 공표된 달에 행이 없는 국가는 0, 공표 전인 달은 None."""
    return [to_amount(sum(table[code][flow].get(month, 0) for code in codes if code in table)) if month in published else None for month in months]


def top_countries(table: dict, flow_keys: tuple, published: set, count: int) -> list:
    """최근 12개 공표월 합계가 큰 순서의 국가코드(합계가 0보다 큰 것만)."""
    recent = sorted(published)[-12:]
    score = {code: sum(slot[flow].get(month, 0) for flow in flow_keys for month in recent) for code, slot in table.items()}
    return [code for code in sorted(score, key=lambda c: (-score[c], c)) if score[code] > 0][:count]


def build_nations(previous: dict | None, key: str, end: str, now: dt.datetime, rows_of, pause: float = 0.2) -> tuple:
    """국가·지역별 전체 수출입 구역과 오류 목록. 새로 받을 때가 아니면 보관본을 그대로 돌려준다."""
    if not stale(previous, end, now):
        return previous, []
    months = month_range(month_add(end, -(HISTORY_MONTHS - 1)), end)
    rows = []
    try:
        for chunk_start, chunk_end in chunks(months[0], end):
            rows.extend(rows_of("nations", NATION_URL, {"strtYymm": chunk_start, "endYymm": chunk_end}))
            if pause:
                time.sleep(pause)
        table, published = country_table(rows)
        if not published:
            raise ValueError("국가별 실적 행이 없습니다" + (": " + ", ".join(sorted(rows[0]))[:150] if rows else ""))
    except ValueError as error:
        return previous, [{"section": "nations", "error": str(error)}]
    entry = lambda code, name, kind, codes: {"code": code, "name": name, "kind": kind, **{flow: country_series(table, codes, flow, months, published) for flow in FLOWS}}
    out = [entry("ALL", "전체", "total", list(table))]
    out += [entry(region["code"], region["name"], "region", region["members"]) for region in REGIONS]
    out += [entry(code, table[code]["name"], "country", [code]) for code in top_countries(table, FLOWS, published, NATION_TOP)]
    return {"months": months, "latest_month": max(published), "fetched_at": now.astimezone(KST).isoformat(timespec="seconds"),
            "countries": len(table), "rows": out}, []


def build_item_countries(item: dict, key: str, end: str, now: dt.datetime, rows_of, pause: float = 0.2) -> dict:
    """한 품목의 국가별 분해: 전체 합과 수출·수입 각각 상위 국가의 월별 금액. 실패하면 ValueError."""
    months = month_range(month_add(end, -(COUNTRY_MONTHS - 1)), end)
    rows = []
    for chunk_start, chunk_end in chunks(months[0], end):
        rows.extend(rows_of("item_countries", ITEM_COUNTRY_URL, {"strtYymm": chunk_start, "endYymm": chunk_end, "hsSgn": item["hs"]}))
        if pause:
            time.sleep(pause)
    table, published = country_table(rows, item["hs"])
    if not published:
        raise ValueError("품목별 국가별 실적 행이 없습니다" + (": " + ", ".join(sorted(rows[0]))[:150] if rows else ""))
    out = {"hs": item["hs"], "months": months, "latest_month": max(published), "fetched_at": now.astimezone(KST).isoformat(timespec="seconds"),
           "countries": len(table), "total": {flow: country_series(table, list(table), flow, months, published) for flow in FLOWS}}
    for flow in FLOWS:
        out[flow] = [{"code": code, "name": table[code]["name"], "values": country_series(table, [code], flow, months, published)}
                     for code in top_countries(table, (flow,), published, COUNTRY_TOP)]
    return out


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


def describe(error: Exception) -> str:
    """오류 문구. 수집기가 직접 낸 ValueError는 문구 그대로, 그 밖의 예외는 종류를 앞에 붙인다."""
    return str(error) if type(error) is ValueError else f"{type(error).__name__}: {error}"


def status_doc(result: dict, now: dt.datetime, fields: dict | None = None) -> dict:
    """최근 수집 시도 요약(성공 여부·최신월·실패 품목·구역별 상태). 실패해도 남겨 원인을 확인할 수 있게 한다.

    fields는 이번에 받은 응답의 항목명(값 없음)으로, 명세와 실제 응답이 다를 때 확인하는 데 쓴다.
    """
    prelim, nations = result.get("prelim") or {}, result.get("nations") or {}
    with_countries = [item for item in result["items"] if item.get("countries")]
    return {
        "attempted_at": now.astimezone(KST).isoformat(timespec="seconds"),
        "ok": bool(result["latest_month"]),
        "latest_month": result["latest_month"],
        "items": len(result["items"]),
        "failed": len(result["errors"]),
        "errors": result["errors"][:5],
        "sections": {
            "prelim": {"latest": prelim.get("latest"), "fetched_at": prelim.get("fetched_at")},
            "nations": {"latest_month": nations.get("latest_month"), "countries": nations.get("countries"), "fetched_at": nations.get("fetched_at")},
            "item_countries": {"items": len(with_countries), "latest_month": max((item["countries"]["latest_month"] for item in with_countries), default=None)},
        },
        "section_failed": len(result.get("section_errors") or []),
        "section_errors": (result.get("section_errors") or [])[:8],
        "fields": fields or {},
    }


def build(items: list, previous: dict | None, key: str, now: dt.datetime, fetcher=None, pause: float = 0.2, rows_fetcher=None, fields: dict | None = None) -> dict:
    """fetcher는 품목별 월별 실적, rows_fetcher는 나머지 API의 행을 받아 오는 함수(시험에서 바꿔 끼운다)."""
    fetcher, rows_fetcher = fetcher or fetch, rows_fetcher or request_rows
    seen = fields if fields is not None else {}

    def rows_of(section: str, url: str, params: dict) -> list:
        rows = rows_fetcher(url, key, params)
        for row in rows[:20]:
            seen[section] = sorted(set(seen.get(section, [])) | set(row))
        return rows

    section_errors, country_failures, sections = [], 0, {}
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
        entry = {"id": item["id"], "label": item["label"], "hs": item["hs"], "stocks": item.get("stocks") or [], **series}
        countries = (before or {}).get("countries")
        # 월별 실적이 실패한 품목이나, 국가 분해가 연달아 실패한 뒤에는 이번 수집에서 더 시도하지 않는다
        # (재시도 대기가 쌓여 작업 제한 시간을 넘기지 않게). 그 품목은 직전 국가 분해를 유지한다.
        if stale(countries, end, now) and not failed and country_failures < COUNTRY_GIVE_UP:
            try:
                countries, country_failures = build_item_countries(item, key, end, now, rows_of, pause), 0
            except Exception as error:   # 새 구역의 예기치 않은 오류가 품목 월별 실적까지 막지 않게 한다
                country_failures += 1
                section_errors.append({"section": "item_countries", "id": item["id"], "hs": item["hs"], "error": describe(error)})
        if countries:
            entry["countries"] = countries
        out_items.append(entry)
    for name, builder in (("nations", lambda before: build_nations(before, key, end, now, rows_of, pause)),
                          ("prelim", lambda before: build_prelim(before, key, now, rows_of, pause))):
        before = (previous or {}).get(name)
        try:
            sections[name], failed_sections = builder(before)
        except Exception as error:   # 위와 같은 이유. 직전 값을 유지한다.
            sections[name], failed_sections = before, [{"section": name, "error": describe(error)}]
        section_errors.extend(failed_sections)
    section_errors = [dict(error, error=scrub(error["error"], key)) for error in section_errors]
    observed = [m for it in out_items for m, v in zip(it["months"], it["exp"]) if v is not None]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.astimezone(KST).isoformat(timespec="seconds"),
        "target_month": end,
        "latest_month": max(observed) if observed else None,
        "source": {"provider": "관세청 품목별 수출입실적(공공데이터포털)", "basis": "통관 기준 · 금액 USD · 중량 kg · 매월 15일경 전월 실적 공표, 이후 정정 가능",
                   "url": "https://www.data.go.kr/"},
        "errors": errors,
        "section_errors": section_errors,
        "items": out_items,
        "nations": sections["nations"],
        "prelim": sections["prelim"],
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
    fields = {}
    result = build(items, previous, key, now, fields=fields)
    Path(args.status).write_text(json.dumps(status_doc(result, now, fields), ensure_ascii=False, indent=1), encoding="utf-8")
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
    latest = (result["prelim"] or {}).get("latest") or {}   # 잠정치를 한 번도 받지 못했으면 prelim이 없다
    print(f"  잠정치 최신 {latest.get('month')} {latest.get('span')} · 국가별 최신월 {(result['nations'] or {}).get('latest_month')} · "
          f"국가 분해 품목 {sum(1 for item in result['items'] if item.get('countries'))} · 구역 실패 {len(result['section_errors'])}", file=sys.stderr)
    for error in result["section_errors"]:
        print(f"  구역 실패 {error['section']}{'(' + error['id'] + ')' if error.get('id') else ''}: {error['error']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
