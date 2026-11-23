#!/usr/bin/env python3
"""컨센서스 누적 분석 화면용 데이터 생성 → consensus_data.json.

네트워크를 쓰지 않는다. consensus_pipeline/inbox/ 에 넣은 날짜별 CSV(종목별 분기 영업이익 예상치)를
consensus_pipeline/history/YYYY-MM-DD.json 으로 보관하고, 보관된 관측일끼리 비교한다.
같은 종목·같은 대상분기 값만 비교하며, 비교할 값이 없으면 비워 둔다(0이나 직전 값으로 채우지 않음).
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

SCHEMA_VERSION = 1
KST = dt.timezone(dt.timedelta(hours=9))
KEEP_DAYS = 400
SERIES_POINTS = 120
COLUMNS = {
    "code": ("종목코드", "코드", "code", "ticker", "symbol"),
    "name": ("종목명", "종목", "name"),
    "period": ("대상분기", "대상기간", "분기", "period", "quarter"),
    "op": ("영업이익", "영업이익(억원)", "op", "operating_profit", "estimate"),
}
SHEET_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
# FnGuide 스크리너 엑셀의 추정치 열: '영업이익(FY1)(억원)', '영업이익(FY2)(억원)' …
# 'Fwd.12M' 열은 머리글 단위(억원)와 실제 값의 단위가 달라 읽지 않는다.
OP_COLUMN = re.compile(r"^영업이익\((F[YQ][1-9])\)")  # FY0(확정 실적)은 추정치가 아니므로 제외
DATE_IN_NAME = re.compile(r"(20\d{2})[-_.]?(\d{2})[-_.]?(\d{2})")


def date_from_name(name: str) -> str | None:
    match = DATE_IN_NAME.search(name)
    if not match:
        return None
    try:
        return dt.date(int(match[1]), int(match[2]), int(match[3])).isoformat()
    except ValueError:
        return None


def to_number(text) -> float | None:
    cleaned = str(text if text is not None else "").replace(",", "").strip()
    if cleaned in ("", "-", "—", "N/A", "n/a", "NA"):
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return value if value == value else None


def normalize_code(text) -> str | None:
    code = re.sub(r"^A(?=\d{6}$)", "", str(text or "").strip().upper())
    if code.isdigit() and len(code) < 6:
        code = code.zfill(6)
    return code if re.fullmatch(r"[0-9A-Z]{6}", code) else None


def normalize_period(text) -> str:
    raw = str(text or "").strip().upper()
    if re.fullmatch(r"F[YQ]\d", raw):
        return raw
    digits = re.sub(r"\D", "", raw)
    return digits[:6] if len(digits) >= 6 else ""

def read_xlsx(data: bytes) -> list:
    """엑셀(.xlsx) 첫 시트를 문자열 2차원 배열로 읽는다. 표준 라이브러리만 쓴다."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            for item in ET.fromstring(archive.read("xl/sharedStrings.xml")).findall(f"{SHEET_NS}si"):
                shared.append("".join(t.text or "" for t in item.iter(f"{SHEET_NS}t")))
        sheets = sorted(n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
        if not sheets:
            raise ValueError("엑셀에서 시트를 찾지 못했습니다.")
        root = ET.fromstring(archive.read(sheets[0]))
    rows = []
    for row in root.iter(f"{SHEET_NS}row"):
        cells = {}
        for cell in row.findall(f"{SHEET_NS}c"):
            letters = re.match(r"[A-Z]+", cell.get("r") or "")
            if not letters:
                continue
            index = 0
            for ch in letters.group():
                index = index * 26 + ord(ch) - 64
            kind, value = cell.get("t"), cell.find(f"{SHEET_NS}v")
            if kind == "s" and value is not None:
                text = shared[int(value.text)]
            elif kind == "inlineStr":
                text = "".join(t.text or "" for t in cell.iter(f"{SHEET_NS}t"))
            else:
                text = value.text if value is not None and value.text is not None else ""
            cells[index - 1] = text
        rows.append([cells.get(i, "") for i in range(max(cells) + 1)] if cells else [])
    return rows


def parse_screener(rows: list) -> list:
    """FnGuide 스크리너 엑셀 → [[code, name, period, op]]. 추정치 열마다 기간(FY1, FY2 …)을 나눠 읽는다."""
    header_index = next((i for i, r in enumerate(rows) if "종목코드" in [str(c).strip() for c in r]), None)
    if header_index is None:
        raise ValueError("'종목코드' 머리글 행을 찾지 못했습니다.")
    header = [str(c).strip() for c in rows[header_index]]
    code_col = header.index("종목코드")
    name_col = header.index("종목명") if "종목명" in header else None
    op_cols = [(i, match.group(1)) for i, h in enumerate(header) if (match := OP_COLUMN.match(h))]
    if not op_cols:
        raise ValueError("'영업이익(FY1)' 형태의 추정치 열을 찾지 못했습니다.")
    out, seen = [], set()
    for row in rows[header_index + 1:]:
        code = normalize_code(row[code_col] if code_col < len(row) else None)
        if not code:
            continue
        name = str(row[name_col]).strip() if name_col is not None and name_col < len(row) else ""
        for col, period in op_cols:
            op = to_number(row[col] if col < len(row) else None)
            if op is None or (code, period) in seen:
                continue
            seen.add((code, period))
            out.append([code, name, period, op])
    return out



def parse_csv(data: bytes) -> list:
    """CSV 바이트 → [[code, name, period, op]]. 영업이익이 비어 있는 행은 버린다."""
    for encoding in ("utf-8-sig", "cp949"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("인코딩을 알 수 없습니다(UTF-8 또는 CP949만 지원).")
    reader = csv.DictReader(io.StringIO(text))
    headers = {(h or "").strip().lower(): h for h in reader.fieldnames or []}
    pick = {field: next((headers[a.lower()] for a in aliases if a.lower() in headers), None) for field, aliases in COLUMNS.items()}
    if not pick["code"] or not pick["op"]:
        raise ValueError("필수 열(종목코드, 영업이익)을 찾지 못했습니다.")
    rows, seen = [], set()
    for record in reader:
        code, op = normalize_code(record.get(pick["code"])), to_number(record.get(pick["op"]))
        if not code or op is None:
            continue
        period = normalize_period(record.get(pick["period"])) if pick["period"] else ""
        if (code, period) in seen:
            continue
        seen.add((code, period))
        rows.append([code, (record.get(pick["name"]) or "").strip() if pick["name"] else "", period, op])
    return rows


def ingest(inbox: Path, history: Path) -> list:
    """inbox의 CSV를 history의 날짜별 JSON으로 옮겨 적는다. 반환: 새로 반영한 날짜."""
    added = []
    for path in sorted(p for p in inbox.iterdir() if p.suffix.lower() in (".csv", ".xlsx")) if inbox.is_dir() else []:
        date = date_from_name(path.name)
        if not date:
            print(f"건너뜀(파일명에 날짜 없음): {path.name}", file=sys.stderr)
            continue
        try:
            rows = parse_screener(read_xlsx(path.read_bytes())) if path.suffix.lower() == ".xlsx" else parse_csv(path.read_bytes())
        except (ValueError, KeyError, IndexError, zipfile.BadZipFile, ET.ParseError) as error:
            print(f"건너뜀({path.name}): {error}", file=sys.stderr)
            continue
        if not rows:
            print(f"건너뜀(유효한 행 없음): {path.name}", file=sys.stderr)
            continue
        history.mkdir(parents=True, exist_ok=True)
        (history / f"{date}.json").write_text(json.dumps({"date": date, "source_file": path.name, "rows": rows}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        added.append(date)
    return added


def load_history(history: Path, today: dt.date) -> list:
    """보관 스냅샷을 날짜순으로 읽는다. KEEP_DAYS보다 오래된 파일은 지운다."""
    snapshots = []
    for path in sorted(history.glob("*.json")):
        date = date_from_name(path.name)
        if not date:
            continue
        if (today - dt.date.fromisoformat(date)).days > KEEP_DAYS:
            path.unlink()
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        values = {(r[0], r[2]): r[3] for r in doc.get("rows") or [] if len(r) >= 4}
        names = {r[0]: r[1] for r in doc.get("rows") or [] if len(r) >= 4 and r[1]}
        if values:
            snapshots.append({"date": date, "values": values, "names": names})
    return snapshots


def change_pct(current, base) -> float | None:
    """(현재 − 기준) ÷ |기준| × 100. 기준이 없거나 0이면 계산하지 않는다."""
    if current is None or base is None or base == 0:
        return None
    return round((current - base) / abs(base) * 100, 2)


def snapshot_on_or_before(snapshots: list, limit: str, exclude_last: bool = True):
    pool = snapshots[:-1] if exclude_last else snapshots
    for snapshot in reversed(pool):
        if snapshot["date"] <= limit:
            return snapshot
    return None


def classify(prev_pct, week_pct, month_pct, current, prev_value) -> str:
    if prev_pct is None and week_pct is None and month_pct is None:
        return "비교 불가"
    if current is not None and prev_value is not None and prev_value < 0 and current > prev_value:
        return "적자 축소"
    ups = [p for p in (prev_pct, week_pct, month_pct) if p is not None and p > 0]
    downs = [p for p in (prev_pct, week_pct, month_pct) if p is not None and p < 0]
    if ups and not downs:
        return "상향"
    if downs and not ups:
        return "하향"
    if ups and downs:
        return "엇갈림"
    return "유지"


def build(snapshots: list, now: dt.datetime) -> dict:
    if not snapshots:
        raise ValueError("보관된 컨센서스 관측일이 없습니다.")
    latest = snapshots[-1]
    latest_date = dt.date.fromisoformat(latest["date"])
    previous = snapshots[-2] if len(snapshots) > 1 else None
    week = snapshot_on_or_before(snapshots, (latest_date - dt.timedelta(days=7)).isoformat())
    month = snapshot_on_or_before(snapshots, (latest_date - dt.timedelta(days=30)).isoformat())
    # 종목마다 가장 가까운 대상분기 하나를 화면 기본값으로 쓴다.
    chosen = {}
    for (code, period) in sorted(latest["values"]):
        chosen.setdefault(code, period)
    names = {}
    for snapshot in snapshots:
        names.update(snapshot["names"])
    recent = snapshots[-SERIES_POINTS:]
    rows, summary = [], {"up": 0, "down": 0, "flat": 0, "na": 0}
    for code, period in chosen.items():
        key, current = (code, period), latest["values"][(code, period)]
        prev_value = previous["values"].get(key) if previous else None
        prev_pct = change_pct(current, prev_value)
        week_pct = change_pct(current, week["values"].get(key)) if week else None
        month_pct = change_pct(current, month["values"].get(key)) if month else None
        if prev_pct is None:
            summary["na"] += 1
        elif prev_pct > 0:
            summary["up"] += 1
        elif prev_pct < 0:
            summary["down"] += 1
        else:
            summary["flat"] += 1
        rows.append({
            "code": code, "name": names.get(code, ""), "period": period, "current": current,
            "prev": prev_value, "prev_pct": prev_pct, "week_pct": week_pct, "month_pct": month_pct,
            "class": classify(prev_pct, week_pct, month_pct, current, prev_value),
            "series": [snapshot["values"].get(key) for snapshot in recent],
        })
    rows.sort(key=lambda r: (r["week_pct"] is None, -(r["week_pct"] or 0), r["code"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.astimezone(KST).isoformat(timespec="seconds"),
        "asof": latest["date"],
        "previous": previous["date"] if previous else None,
        "week_base": week["date"] if week else None,
        "month_base": month["date"] if month else None,
        "observations": len(snapshots),
        "first_observation": snapshots[0]["date"],
        "series_dates": [snapshot["date"] for snapshot in recent],
        "unit": "억원",
        "metric": "연간 영업이익 예상치" if any(p.startswith("F") for p in chosen.values()) else "분기 영업이익 예상치",
        "summary": summary,
        "rows": rows,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--inbox", default="consensus_pipeline/inbox")
    parser.add_argument("--history", default="consensus_pipeline/history")
    parser.add_argument("--output", default="consensus_data.json")
    args = parser.parse_args(argv)
    now = dt.datetime.now(KST)
    added = ingest(Path(args.inbox), Path(args.history))
    snapshots = load_history(Path(args.history), now.date())
    if not snapshots:
        print("보관된 컨센서스 관측일이 없어 건너뜁니다(파일을 만들지 않음).", file=sys.stderr)
        return 0
    result = build(snapshots, now)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"consensus_data.json 생성: 기준일 {result['asof']} · 종목 {len(result['rows'])} · 관측일 {result['observations']} · 새로 반영 {len(added)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
