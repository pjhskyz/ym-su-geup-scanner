import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from collection_window import KST, collection_decision, run_collection_window


def at(value):
    return datetime.fromisoformat(value).replace(tzinfo=KST)


class FakeClock:
    def __init__(self, now):
        self.now = now
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += timedelta(seconds=seconds)


class CollectionDecisionTests(unittest.TestCase):
    def test_1830_external_request_skips(self):
        result = collection_decision(at("2026-09-15T18:30:00"), "workflow_dispatch")
        self.assertFalse(result.allowed)
        self.assertEqual(result.wait_seconds, 0)

    def test_before_wait_window_skips(self):
        result = collection_decision(at("2026-09-15T19:59:59"), "workflow_dispatch")
        self.assertFalse(result.allowed)

    def test_wait_window_boundaries(self):
        for time, expected in [
            ("20:00:00", 1200),
            ("20:05:00", 900),
            ("20:19:59", 1),
            ("20:19:59.900000", 1),
        ]:
            with self.subTest(time=time):
                result = collection_decision(at(f"2026-09-15T{time}"), "workflow_dispatch")
                self.assertTrue(result.allowed)
                self.assertEqual(result.wait_seconds, expected)

    def test_collection_boundary_and_late_requests_allow_without_wait(self):
        for time in ["20:20:00", "20:50:00", "21:00:00", "23:59:59"]:
            with self.subTest(time=time):
                result = collection_decision(at(f"2026-09-15T{time}"), "workflow_dispatch")
                self.assertTrue(result.allowed)
                self.assertEqual(result.wait_seconds, 0)

    def test_weekend_dispatch_skips_even_in_wait_window_or_after_2020(self):
        for value in ["2026-09-19T20:05:00", "2026-09-20T21:00:00"]:
            with self.subTest(value=value):
                result = collection_decision(at(value), "workflow_dispatch")
                self.assertFalse(result.allowed)
                self.assertEqual(result.wait_seconds, 0)

    def test_force_dispatch_allows_early_or_weekend(self):
        for value in ["2026-09-15T18:30:00", "2026-09-19T20:05:00"]:
            with self.subTest(value=value):
                result = collection_decision(at(value), "workflow_dispatch", force=True)
                self.assertTrue(result.allowed)
                self.assertEqual(result.wait_seconds, 0)

    def test_delayed_schedule_survives_date_and_weekend_rollover(self):
        # Includes the observed 02:03 delay and a Friday cron starting Saturday.
        for value in [
            "2026-09-15T02:03:51",
            "2026-09-19T02:03:51",
            "2026-09-21T01:00:00",
            "2026-09-15T20:20:00",
        ]:
            with self.subTest(value=value):
                result = collection_decision(at(value), "schedule")
                self.assertTrue(result.allowed)
                self.assertEqual(result.wait_seconds, 0)

    def test_unknown_event_denied_even_with_force(self):
        for event in ["", "push", "pull_request"]:
            with self.subTest(event=event):
                self.assertFalse(collection_decision(at("2026-09-15T21:00:00"), event, True).allowed)

    def test_utc_input_uses_kst_date_and_time(self):
        utc = datetime(2026, 9, 15, 11, 5, tzinfo=timezone.utc)
        result = collection_decision(utc, "workflow_dispatch")
        self.assertTrue(result.allowed)
        self.assertEqual(result.wait_seconds, 900)
        # 15:00 Friday UTC is already Saturday midnight KST.
        weekend = datetime(2026, 9, 18, 15, tzinfo=timezone.utc)
        self.assertFalse(collection_decision(weekend, "workflow_dispatch").allowed)

    def test_naive_datetime_rejected(self):
        with self.assertRaisesRegex(ValueError, "timezone"):
            collection_decision(datetime(2026, 9, 15, 20, 5), "workflow_dispatch")


class CollectionRunnerTests(unittest.TestCase):
    def test_2005_waits_until_2020_before_emitting_allowed(self):
        clock = FakeClock(at("2026-09-15T20:05:00"))
        logs = []
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp, "output")
            summary = Path(temp, "summary")

            def sleep(seconds):
                self.assertFalse(output.exists(), "Downstream job must stay blocked during the wait")
                clock.sleep(seconds)

            result = run_collection_window(
                "workflow_dispatch", clock=clock, sleep=sleep,
                output_path=str(output), summary_path=str(summary), log=logs.append,
            )
            self.assertTrue(result.allowed)
            self.assertEqual(clock.now, at("2026-09-15T20:20:00"))
            self.assertEqual(sum(clock.sleeps), 900)
            self.assertTrue(all(0 < seconds <= 30 for seconds in clock.sleeps))
            self.assertIn("allowed=true\n", output.read_text())
            self.assertIn("대기 완료", output.read_text())
            self.assertIn("900초", summary.read_text())
            self.assertIn("2026-09-15T20:20:00+09:00", summary.read_text())
            self.assertTrue(any("대기" in line for line in logs))

    def test_last_second_waits_one_second(self):
        clock = FakeClock(at("2026-09-15T20:19:59"))
        result = run_collection_window("workflow_dispatch", clock=clock, sleep=clock.sleep, log=lambda _: None)
        self.assertTrue(result.allowed)
        self.assertEqual(clock.sleeps, [1])
        self.assertEqual(clock.now, at("2026-09-15T20:20:00"))

    def test_earliest_wait_is_bounded_at_twenty_minutes(self):
        clock = FakeClock(at("2026-09-15T20:00:00"))
        result = run_collection_window("workflow_dispatch", clock=clock, sleep=clock.sleep, log=lambda _: None)
        self.assertTrue(result.allowed)
        self.assertEqual(sum(clock.sleeps), 1200)
        self.assertEqual(max(clock.sleeps), 30)

    def test_delayed_schedule_immediately_emits_allowed(self):
        clock = FakeClock(at("2026-09-19T02:03:51"))
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp, "output")
            result = run_collection_window(
                "schedule", clock=clock, sleep=clock.sleep,
                output_path=str(output), log=lambda _: None,
            )
            self.assertTrue(result.allowed)
            self.assertEqual(clock.sleeps, [])
            self.assertIn("allowed=true\n", output.read_text())

    def test_skip_emits_false_and_appends_to_summary(self):
        clock = FakeClock(at("2026-09-15T18:30:00"))
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp, "output")
            summary = Path(temp, "summary")
            summary.write_text("Existing summary\n")
            result = run_collection_window(
                "workflow_dispatch", clock=clock, sleep=clock.sleep,
                output_path=str(output), summary_path=str(summary), log=lambda _: None,
            )
            self.assertFalse(result.allowed)
            self.assertEqual(clock.sleeps, [])
            self.assertIn("allowed=false\n", output.read_text())
            self.assertTrue(summary.read_text().startswith("Existing summary\n"))
            self.assertIn("건너뜀", summary.read_text())

    def test_clock_rollback_does_not_publish_early_allowance(self):
        clock = FakeClock(at("2026-09-15T20:19:59"))
        # Simulate a system clock moving backward while the bounded wait ends.
        result = run_collection_window("workflow_dispatch", clock=clock, sleep=lambda _: None, log=lambda _: None)
        self.assertFalse(result.allowed)
        self.assertIn("예정 시각 이전", result.reason)


if __name__ == "__main__":
    unittest.main()
