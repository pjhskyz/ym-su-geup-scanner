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
    digits = re.sub(r"\D", "", str(text or ""))
    return digits[:6] if len(digits) >= 6 else ""


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
    for path in sorted(inbox.glob("*.csv")):
        date = date_from_name(path.name)
        if not date:
            print(f"건너뜀(파일명에 날짜 없음): {path.name}", file=sys.stderr)
            continue
        try:
            rows = parse_csv(path.read_bytes())
        except ValueError as error:
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
        "metric": "분기 영업이익 예상치",
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
