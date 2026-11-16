"""Guard the 16:00 KST snapshot against late or duplicate collections."""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo


KST = ZoneInfo("Asia/Seoul")
WAIT_FROM = (15, 40)
COLLECT_FROM = (16, 0)
COLLECT_UNTIL = (16, 30)
REPORT_SESSION = "krx_close_1600"
MAX_SLEEP_SECONDS = 30
# 확인된 임시 거래시간만 기록한다. 보고 단계에서도 매번 당일 휴장/시간변경을 확인한다.
LATE_CLOSE_SESSIONS = {
    "20261119": {
        "close_kst": "16:30",
        "reason": "수능일 정규장 종료 16:30: 16시 조기 수집 건너뜀",
        "source": "https://02.myasset.com/myasset/customer/notice/CU_0201000_P2.cmd?SEQ=202611131405580000000004&gubun=norNotice",
        "published_at": "2026-11-13",
    },
}


@dataclass(frozen=True)
class CollectionDecision:
    allowed: bool
    wait_seconds: int
    reason: str


def has_current_snapshot(now: datetime, snapshot: object) -> bool:
    """Accept only a completed, dated 16:00-session snapshot, not status alone."""
    if not isinstance(snapshot, dict):
        return False
    metadata = snapshot.get("metadata")
    if not isinstance(metadata, dict):
        return False
    now = now.astimezone(KST)
    rows = snapshot.get("rows")
    if (
        snapshot.get("asof") != now.strftime("%Y%m%d")
        or now.strftime("%Y%m%d") in LATE_CLOSE_SESSIONS
        or metadata.get("report_session") != REPORT_SESSION
        or metadata.get("status") != "collected"
        or metadata.get("asof_fallback") is True
        or not isinstance(rows, list)
        or not rows
    ):
        return False
    try:
        started = datetime.fromisoformat(metadata["collection_started_at"])
        finished = datetime.fromisoformat(metadata.get("updated_at") or snapshot["updated"])
        if any(value.tzinfo is None or value.utcoffset() is None for value in (started, finished)):
            return False
        started, finished = started.astimezone(KST), finished.astimezone(KST)
    except (KeyError, TypeError, ValueError):
        return False
    return (
        started.weekday() < 5
        and started.date() == finished.date() == now.date()
        and COLLECT_FROM <= (started.hour, started.minute) < COLLECT_UNTIL
        and COLLECT_FROM <= (finished.hour, finished.minute) < COLLECT_UNTIL
        and started <= finished <= now
    )


def load_snapshot(path: str | None) -> object:
    if path is None:
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def collection_decision(
    now: datetime, event_name: str, force: bool = False, *, snapshot: object = None
) -> CollectionDecision:
    """Return a KST decision; delayed schedules obey the same window as dispatch.

    ``allowed`` takes effect after any required wait. Only explicit manual force
    may bypass the weekday, session-window and duplicate-snapshot guards.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Collection time must include a timezone")
    now = now.astimezone(KST)

    if event_name not in ("schedule", "workflow_dispatch"):
        return CollectionDecision(False, 0, "지원하지 않는 실행 이벤트: 수집 건너뜀")
    if force and event_name == "workflow_dispatch":
        return CollectionDecision(True, 0, "명시적인 수동 강제 수집")
    special = LATE_CLOSE_SESSIONS.get(now.strftime("%Y%m%d"))
    if special:
        return CollectionDecision(False, 0, special["reason"])
    if now.weekday() >= 5:
        return CollectionDecision(False, 0, "주말의 일반 요청: 수집 건너뜀")
    current_time = (now.hour, now.minute)
    if current_time >= COLLECT_UNTIL:
        return CollectionDecision(False, 0, "16:30 KST 이후 요청: 조기 보고용 자료 보호를 위해 수집 건너뜀")
    if has_current_snapshot(now, snapshot):
        return CollectionDecision(False, 0, "당일 16시 정상 스냅샷 있음: 중복·예비 수집 건너뜀")
    if current_time >= COLLECT_FROM:
        return CollectionDecision(True, 0, "평일 16:00~16:29 KST 요청: 수집 허용")
    if current_time >= WAIT_FROM:
        target = now.replace(hour=16, minute=0, second=0, microsecond=0)
        wait_seconds = math.ceil((target - now).total_seconds())
        return CollectionDecision(True, wait_seconds, "평일 16:00 KST까지 대기 후 수집")
    return CollectionDecision(False, 0, "평일 15:40 KST 이전의 일반 요청: 수집 건너뜀")


def kst_now() -> datetime:
    return datetime.now(KST)


def run_collection_window(
    event_name: str,
    force: bool = False,
    *,
    clock: Callable[[], datetime] = kst_now,
    sleep: Callable[[float], None] = time.sleep,
    snapshot_path: str | None = "scanner_data.json",
    output_path: str | None = None,
    summary_path: str | None = None,
    log: Callable[[str], None] = print,
) -> CollectionDecision:
    requested_at = clock()
    decision = collection_decision(requested_at, event_name, force, snapshot=load_snapshot(snapshot_path))
    requested_at = requested_at.astimezone(KST)
    wait_seconds = decision.wait_seconds
    log(f"{requested_at.isoformat(timespec='seconds')} / {decision.reason}")

    if wait_seconds:
        target = requested_at.replace(hour=16, minute=0, second=0, microsecond=0)
        log(f"수집 시작 대기: {target.isoformat(timespec='seconds')} / {wait_seconds}초")
        # Keep the wait bounded at 20 minutes. Short sleeps also make stopping
        # the job responsive. Revalidate the wall clock and snapshot afterward.
        remaining = wait_seconds
        while remaining > 0:
            interval = min(MAX_SLEEP_SECONDS, remaining)
            sleep(interval)
            remaining -= interval
    finished_at = clock().astimezone(KST)
    if decision.allowed:
        # The runner or sleep may be delayed past the report cutoff, and another
        # run may have published the snapshot while this request was waiting.
        decision = collection_decision(finished_at, event_name, force, snapshot=load_snapshot(snapshot_path))
        if decision.wait_seconds:
            decision = CollectionDecision(False, 0, "대기 후 시계가 수집 예정 시각 이전: 수집 건너뜀")
        elif decision.allowed and wait_seconds:
            decision = CollectionDecision(True, 0, "16:00 KST까지 대기 완료: 수집 허용")

    allowed = str(decision.allowed).lower()
    log(f"{finished_at.isoformat(timespec='seconds')} / allowed={allowed} / {decision.reason}")
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as output:
            output.write(f"allowed={allowed}\n")
            output.write(f"reason={decision.reason}\n")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as summary:
            summary.write("## 수급 수집 시작 판단\n\n")
            summary.write(f"- 결과: {'수집 허용' if decision.allowed else '수집 건너뜀'}\n")
            summary.write(f"- 사유: {decision.reason}\n")
            summary.write(f"- 요청 확인: {requested_at.isoformat(timespec='seconds')}\n")
            summary.write(f"- 판단 완료: {finished_at.isoformat(timespec='seconds')}\n")
            summary.write(f"- 16:00까지 대기: {wait_seconds}초\n")
            summary.write("- 화면 반영은 실제 수집과 배포가 완료된 뒤 이루어집니다.\n")
    return decision


if __name__ == "__main__":
    run_collection_window(
        os.environ.get("GITHUB_EVENT_NAME", ""),
        os.environ.get("FORCE_COLLECTION", "false").lower() == "true",
        output_path=os.environ.get("GITHUB_OUTPUT"),
        summary_path=os.environ.get("GITHUB_STEP_SUMMARY"),
    )

