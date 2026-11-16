import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from collection_window import KST, collection_decision, has_current_snapshot, run_collection_window


def at(value):
    return datetime.fromisoformat(value).replace(tzinfo=KST)


def snapshot():
    return {
        "asof": "20261117",
        "rows": [["sample"]],
        "metadata": {
            "status": "collected",
            "report_session": "krx_close_1600",
            "asof_fallback": False,
            "collection_started_at": "2026-11-17T16:00:10+09:00",
            "updated_at": "2026-11-17T16:02:00+09:00",
        },
    }


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
    def test_known_late_close_day_skips_early_collection(self):
        for event in ["schedule", "workflow_dispatch"]:
            for time in ["16:00:00", "16:20:00"]:
                result = collection_decision(at(f"2026-11-19T{time}"), event)
                self.assertFalse(result.allowed)
                self.assertIn("정규장 종료 16:30", result.reason)
        self.assertTrue(collection_decision(at("2026-11-19T16:05:00"),
                                            "workflow_dispatch", force=True).allowed)

    def test_before_wait_window_skips_both_events(self):
        for event in ["schedule", "workflow_dispatch"]:
            self.assertFalse(collection_decision(at("2026-11-17T15:39:59"), event).allowed)

    def test_wait_window_boundaries(self):
        for event in ["schedule", "workflow_dispatch"]:
            for time, expected in [("15:40:00", 1200), ("15:45:00", 900), ("15:59:59", 1), ("15:59:59.900000", 1)]:
                with self.subTest(event=event, time=time):
                    result = collection_decision(at(f"2026-11-17T{time}"), event)
                    self.assertTrue(result.allowed)
                    self.assertEqual(result.wait_seconds, expected)

    def test_collection_and_backup_window_allow_without_wait(self):
        for event in ["schedule", "workflow_dispatch"]:
            for time in ["16:00:00", "16:20:00", "16:29:59.999999"]:
                with self.subTest(event=event, time=time):
                    result = collection_decision(at(f"2026-11-17T{time}"), event)
                    self.assertTrue(result.allowed)
                    self.assertEqual(result.wait_seconds, 0)

    def test_cutoff_and_legacy_evening_requests_skip(self):
        for event in ["schedule", "workflow_dispatch"]:
            for time in ["16:30:00", "18:30:00", "20:05:00", "20:20:00", "20:50:00", "23:59:59"]:
                with self.subTest(event=event, time=time):
                    result = collection_decision(at(f"2026-11-17T{time}"), event)
                    self.assertFalse(result.allowed)
                    self.assertEqual(result.wait_seconds, 0)

    def test_weekends_and_overnight_delayed_schedules_skip(self):
        for event in ["schedule", "workflow_dispatch"]:
            for value in ["2026-11-21T16:00:00", "2026-11-22T16:20:00", "2026-11-17T02:03:51"]:
                with self.subTest(event=event, value=value):
                    self.assertFalse(collection_decision(at(value), event).allowed)

    def test_force_bypasses_guards_only_for_explicit_dispatch(self):
        for value in ["2026-11-17T14:00:00", "2026-11-21T16:00:00", "2026-11-17T20:05:00"]:
            with self.subTest(value=value):
                result = collection_decision(at(value), "workflow_dispatch", force=True)
                self.assertTrue(result.allowed)
                self.assertEqual(result.wait_seconds, 0)
                self.assertFalse(collection_decision(at(value), "schedule", force=True).allowed)
        for event in ["", "push", "pull_request"]:
            self.assertFalse(collection_decision(at("2026-11-17T16:00:00"), event, True).allowed)

    def test_utc_input_uses_kst_date_and_time(self):
        utc = datetime(2026, 11, 17, 6, 45, tzinfo=timezone.utc)
        for event in ["schedule", "workflow_dispatch"]:
            result = collection_decision(utc, event)
            self.assertTrue(result.allowed)
            self.assertEqual(result.wait_seconds, 900)
            # Friday 15:00 UTC is already Saturday midnight KST.
            weekend = datetime(2026, 11, 20, 15, tzinfo=timezone.utc)
            self.assertFalse(collection_decision(weekend, event).allowed)

    def test_naive_datetime_rejected(self):
        with self.assertRaisesRegex(ValueError, "timezone"):
            collection_decision(datetime(2026, 11, 17, 16, 0), "workflow_dispatch")

    def test_completed_snapshot_skips_both_events_but_not_manual_force(self):
        now = at("2026-11-17T16:20:00")
        self.assertTrue(has_current_snapshot(now, snapshot()))
        for event in ["schedule", "workflow_dispatch"]:
            result = collection_decision(now, event, snapshot=snapshot())
            self.assertFalse(result.allowed)
            self.assertIn("정상 스냅샷", result.reason)
        self.assertTrue(collection_decision(now, "workflow_dispatch", True, snapshot=snapshot()).allowed)

    def test_status_only_and_invalid_snapshots_do_not_block_backup(self):
        invalid = [None, [], {"status": "success"}, {"metadata": []}]
        for key, value in [("asof", "20261116"), ("rows", []), ("rows", "not rows")]:
            entry = snapshot()
            entry[key] = value
            invalid.append(entry)
        for key, value in [
            ("status", "failed"), ("report_session", "other"), ("asof_fallback", True),
            ("collection_started_at", "2026-11-16T16:00:10+09:00"),
            ("collection_started_at", "2026-11-17T20:00:10+09:00"),
            ("collection_started_at", "2026-11-17T16:30:00+09:00"),
            ("collection_started_at", "2026-11-17T15:59:59+09:00"),
            ("collection_started_at", "invalid"), ("updated_at", None),
            ("updated_at", "2026-11-17T16:02:00"),
            ("updated_at", "2026-11-17T16:00:00+09:00"),
            ("updated_at", "2026-11-17T16:21:00+09:00"),
            ("updated_at", "2026-11-17T16:30:00+09:00"),
        ]:
            entry = snapshot()
            entry["metadata"][key] = value
            invalid.append(entry)
        for entry in invalid:
            with self.subTest(snapshot=entry):
                self.assertTrue(collection_decision(at("2026-11-17T16:20:00"), "schedule", snapshot=entry).allowed)

    def test_snapshot_timestamps_accept_utc_and_top_level_updated_fallback(self):
        entry = snapshot()
        entry["metadata"]["collection_started_at"] = "2026-11-17T07:00:10+00:00"
        del entry["metadata"]["updated_at"]
        entry["updated"] = "2026-11-17T07:02:00+00:00"
        self.assertTrue(has_current_snapshot(at("2026-11-17T16:20:00"), entry))

    def test_snapshot_completed_after_cutoff_is_not_valid_session(self):
        entry = snapshot()
        entry["metadata"]["updated_at"] = "2026-11-17T16:30:00+09:00"
        self.assertFalse(has_current_snapshot(at("2026-11-17T17:00:00"), entry))


class CollectionRunnerTests(unittest.TestCase):
    def test_waits_until_1600_before_emitting_allowed(self):
        clock = FakeClock(at("2026-11-17T15:45:00"))
        logs = []
        with tempfile.TemporaryDirectory() as temp:
            output, summary = Path(temp, "output"), Path(temp, "summary")

            def sleep(seconds):
                self.assertFalse(output.exists(), "Downstream job must stay blocked during the wait")
                clock.sleep(seconds)

            result = run_collection_window(
                "workflow_dispatch", clock=clock, sleep=sleep, snapshot_path=None,
                output_path=str(output), summary_path=str(summary), log=logs.append,
            )
            self.assertTrue(result.allowed)
            self.assertEqual(clock.now, at("2026-11-17T16:00:00"))
            self.assertEqual(sum(clock.sleeps), 900)
            self.assertTrue(all(0 < seconds <= 30 for seconds in clock.sleeps))
            self.assertIn("allowed=true\n", output.read_text())
            self.assertIn("대기 완료", output.read_text())
            self.assertIn("900초", summary.read_text())
            self.assertIn("2026-11-17T16:00:00+09:00", summary.read_text())
            self.assertTrue(any("대기" in line for line in logs))

    def test_last_second_and_earliest_wait_are_bounded(self):
        for time, seconds in [("15:59:59", 1), ("15:40:00", 1200)]:
            clock = FakeClock(at(f"2026-11-17T{time}"))
            result = run_collection_window("schedule", clock=clock, sleep=clock.sleep, snapshot_path=None, log=lambda _: None)
            self.assertTrue(result.allowed)
            self.assertEqual(sum(clock.sleeps), seconds)
            self.assertLessEqual(max(clock.sleeps), 30)
            self.assertEqual(clock.now, at("2026-11-17T16:00:00"))

    def test_legacy_skip_preserves_snapshot_and_status_and_appends_summary(self):
        clock = FakeClock(at("2026-11-17T20:05:00"))
        with tempfile.TemporaryDirectory() as temp:
            output, summary = Path(temp, "output"), Path(temp, "summary")
            data, status = Path(temp, "scanner_data.json"), Path(temp, "scanner_status.json")
            original_data, original_status = json.dumps(snapshot()), '{"status":"success"}'
            data.write_text(original_data)
            status.write_text(original_status)
            summary.write_text("Existing summary\n")
            result = run_collection_window(
                "workflow_dispatch", clock=clock, sleep=clock.sleep, snapshot_path=str(data),
                output_path=str(output), summary_path=str(summary), log=lambda _: None,
            )
            self.assertFalse(result.allowed)
            self.assertEqual(clock.sleeps, [])
            self.assertIn("allowed=false\n", output.read_text())
            self.assertTrue(summary.read_text().startswith("Existing summary\n"))
            self.assertEqual(data.read_text(), original_data)
            self.assertEqual(status.read_text(), original_status)

    def test_missing_corrupt_and_null_files_allow_backup(self):
        clock = FakeClock(at("2026-11-17T16:20:00"))
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp, "scanner_data.json")
            for contents in [None, "{broken", "null"]:
                with self.subTest(contents=contents):
                    if contents is not None:
                        data.write_text(contents)
                    result = run_collection_window("schedule", clock=clock, sleep=clock.sleep, snapshot_path=str(data), log=lambda _: None)
                    self.assertTrue(result.allowed)

    def test_completed_file_skips_backup_without_mutation(self):
        clock = FakeClock(at("2026-11-17T16:20:00"))
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp, "scanner_data.json")
            original = json.dumps(snapshot())
            data.write_text(original)
            result = run_collection_window("schedule", clock=clock, sleep=clock.sleep, snapshot_path=str(data), log=lambda _: None)
            self.assertFalse(result.allowed)
            self.assertEqual(data.read_text(), original)

    def test_clock_rollback_does_not_publish_early_allowance(self):
        clock = FakeClock(at("2026-11-17T15:59:59"))
        result = run_collection_window("workflow_dispatch", clock=clock, sleep=lambda _: None, snapshot_path=None, log=lambda _: None)
        self.assertFalse(result.allowed)
        self.assertIn("예정 시각 이전", result.reason)

    def test_wait_overshoot_rechecks_cutoff(self):
        clock = FakeClock(at("2026-11-17T15:59:59"))

        def oversleep(_):
            clock.now = at("2026-11-17T16:30:00")

        result = run_collection_window("schedule", clock=clock, sleep=oversleep, snapshot_path=None, log=lambda _: None)
        self.assertFalse(result.allowed)
        self.assertIn("16:30", result.reason)

    def test_cutoff_is_rechecked_even_without_wait(self):
        times = iter([at("2026-11-17T16:29:59"), at("2026-11-17T16:30:00")])
        result = run_collection_window("schedule", clock=lambda: next(times), snapshot_path=None, log=lambda _: None)
        self.assertFalse(result.allowed)

    def test_snapshot_published_during_wait_blocks_duplicate(self):
        clock = FakeClock(at("2026-11-17T15:59:59"))
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp, "scanner_data.json")

            def sleep(_):
                data.write_text(json.dumps(snapshot()))
                clock.now = at("2026-11-17T16:02:00")

            result = run_collection_window("schedule", clock=clock, sleep=sleep, snapshot_path=str(data), log=lambda _: None)
            self.assertFalse(result.allowed)
            self.assertIn("정상 스냅샷", result.reason)


if __name__ == "__main__":
    unittest.main()
