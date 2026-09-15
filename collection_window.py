"""Choose when a scanner request may start, without discarding delayed schedules."""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo


KST = ZoneInfo("Asia/Seoul")
WAIT_FROM = (20, 0)
COLLECT_FROM = (20, 20)
MAX_SLEEP_SECONDS = 30


@dataclass(frozen=True)
class CollectionDecision:
    allowed: bool
    wait_seconds: int
    reason: str


def collection_decision(
    now: datetime, event_name: str, force: bool = False
) -> CollectionDecision:
    """Return a decision in KST. ``allowed`` takes effect after any required wait.

    GitHub already limits schedule events to the configured weekday cron. The
    runner can start hours later, so applying a second wall-clock/day gate to
    those events would silently throw away valid scheduled collections.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Collection time must include a timezone")
    now = now.astimezone(KST)

    if event_name == "schedule":
        return CollectionDecision(True, 0, "GitHub 예약 요청: 지연된 실행도 수집 허용")
    if event_name != "workflow_dispatch":
        return CollectionDecision(False, 0, "지원하지 않는 실행 이벤트: 수집 건너뜀")
    if force:
        return CollectionDecision(True, 0, "명시적인 수동 강제 수집")
    if now.weekday() >= 5:
        return CollectionDecision(False, 0, "주말의 일반 요청: 수집 건너뜀")
    current_time = (now.hour, now.minute)
    if current_time >= COLLECT_FROM:
        return CollectionDecision(True, 0, "평일 20:20 KST 이후 요청: 수집 허용")
    if current_time >= WAIT_FROM:
        target = now.replace(hour=20, minute=20, second=0, microsecond=0)
        wait_seconds = math.ceil((target - now).total_seconds())
        return CollectionDecision(True, wait_seconds, "평일 20:20 KST까지 대기 후 수집")
    return CollectionDecision(False, 0, "평일 20:00 KST 이전의 일반 요청: 수집 건너뜀")


def kst_now() -> datetime:
    return datetime.now(KST)


def run_collection_window(
    event_name: str,
    force: bool = False,
    *,
    clock: Callable[[], datetime] = kst_now,
    sleep: Callable[[float], None] = time.sleep,
    output_path: str | None = None,
    summary_path: str | None = None,
    log: Callable[[str], None] = print,
) -> CollectionDecision:
    requested_at = clock()
    decision = collection_decision(requested_at, event_name, force)
    requested_at = requested_at.astimezone(KST)
    wait_seconds = decision.wait_seconds
    log(f"{requested_at.isoformat(timespec='seconds')} / {decision.reason}")

    if wait_seconds:
        target = requested_at.replace(hour=20, minute=20, second=0, microsecond=0)
        log(f"수집 시작 대기: {target.isoformat(timespec='seconds')} / {wait_seconds}초")
        # Keep the wait bounded at 20 minutes. Short sleeps also make stopping
        # the job responsive; the job itself has a 25-minute timeout.
        remaining = wait_seconds
        while remaining > 0:
            interval = min(MAX_SLEEP_SECONDS, remaining)
            sleep(interval)
            remaining -= interval
        finished_wait_at = clock().astimezone(KST)
        if finished_wait_at < target:
            decision = CollectionDecision(False, 0, "대기 후 시계가 수집 예정 시각 이전: 수집 건너뜀")
        else:
            decision = CollectionDecision(True, 0, "20:20 KST까지 대기 완료: 수집 허용")

    finished_at = clock().astimezone(KST)
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
            summary.write(f"- 20:20까지 대기: {wait_seconds}초\n")
            summary.write("- 화면 반영은 실제 수집과 배포가 완료된 뒤 이루어집니다.\n")
    return decision


if __name__ == "__main__":
    run_collection_window(
        os.environ.get("GITHUB_EVENT_NAME", ""),
        os.environ.get("FORCE_COLLECTION", "false").lower() == "true",
        output_path=os.environ.get("GITHUB_OUTPUT"),
        summary_path=os.environ.get("GITHUB_STEP_SUMMARY"),
    )
