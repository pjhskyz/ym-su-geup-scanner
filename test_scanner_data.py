"""수급의 순위 정의·비교 경계·실패 시 데이터 보존을 검증한다. 네트워크/인증 불필요."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import pandas as pd

import build_scanner_data as scanner


def ranked(net, rank=1, name="테스트"):
    return {"net": net, "rank": rank, "bucket": 1, "name": name}


class ScannerDataTests(unittest.TestCase):
    def test_rank_is_unique_while_bucket_can_repeat_and_ties_are_stable(self):
        frames = []
        for first in (1, 301):
            frames.append(pd.DataFrame({
                "순매수거래대금": [1_000_000] * 300,
                "종목명": [f"종목{i}" for i in range(first, first + 300)],
            }, index=[f"{i:06d}" for i in reversed(range(first, first + 300))]))
        fake = Mock()
        fake.get_market_net_purchases_of_equities.side_effect = frames
        with patch.object(scanner, "stock", fake), patch.object(scanner, "SLEEP", 0):
            values = scanner.ranking("20260911", "기관합계", 0)
        self.assertEqual(values["000001"]["rank"], 1)
        self.assertEqual(values["000002"]["rank"], 2)
        self.assertEqual(values["000001"]["bucket"], values["000002"]["bucket"])
        self.assertEqual(len({v["rank"] for v in values.values()}), 600)

    def test_one_missing_market_does_not_publish_a_partial_rank(self):
        fake = Mock()
        fake.get_market_net_purchases_of_equities.side_effect = [
            pd.DataFrame({"순매수거래대금": [100], "종목명": ["종목"]}, index=["005930"]),
            pd.DataFrame(),
        ]
        with patch.object(scanner, "stock", fake), patch.object(scanner, "SLEEP", 0):
            with self.assertRaisesRegex(RuntimeError, "일부 시장"):
                scanner.ranking("20260911", "기관합계", 0)

    def test_previous_snapshot_uses_that_days_actual_cap_and_unrounded_ratio(self):
        current, previous, earlier = "20260911", "20260910", "20260909"
        daily = {
            "inst": {
                current: {"000001": ranked(300_000_000)},
                previous: {"000001": ranked(299_999_999)},
                earlier: {"000001": ranked(100_000_000)},
            },
            "frgn": {
                current: {"000001": ranked(300_000_000)},
                previous: {"000001": ranked(300_000_000)},
                earlier: {"000001": ranked(100_000_000)},
            },
        }
        today_detail = scanner.make_rank_details(daily, [current, previous, earlier], {"000001": 50_000_000_000})
        snapshot = scanner.comparison_snapshot(daily, [previous, earlier], {"000001": 100_000_000_000})
        self.assertTrue(snapshot["available"])
        self.assertEqual(snapshot["asof"], previous)
        self.assertEqual(len(snapshot["rows"][0]), 28)
        self.assertEqual(snapshot["rows"][0][14], 0.30)
        self.assertLess(snapshot["rank_details"]["000001"]["ratio_pct"]["inst"], 0.3)
        self.assertEqual(snapshot["rank_details"]["000001"]["ratio_pct"]["frgn"], 0.3)
        self.assertEqual(today_detail["000001"]["ratio_pct"]["inst"], 0.6)
        # Same-day repeated builds always reconstruct the previous trading day, not an intraday snapshot.
        self.assertEqual(snapshot, scanner.comparison_snapshot(daily, [previous, earlier], {"000001": 100_000_000_000}))

    def test_missing_previous_cap_is_comparison_pending_not_all_new(self):
        daily = {k: {"20260910": {"000001": ranked(100)}} for k in scanner.INVESTORS}
        snapshot = scanner.comparison_snapshot(daily, ["20260910"], {})
        self.assertFalse(snapshot["available"])
        self.assertEqual(snapshot["rows"], [])
        self.assertTrue(snapshot["reason"])

    def test_current_partial_caps_abort_without_replacing_last_good_data(self):
        fake = Mock()
        fake.get_nearest_business_day_in_a_week.return_value = "20260911"
        days = [day.strftime("%Y%m%d") for day in pd.bdate_range(end="2026-09-11", periods=22)][::-1]
        all_ranked = {"000001": ranked(100), "000002": ranked(90, 2)}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "scanner_data.json"
            original = '{"asof":"20260910","rows":[["last good data"]]}'
            path.write_text(original, encoding="utf-8")
            with patch.dict(os.environ, {"KRX_ID": "unit-test", "KRX_PW": "unit-test"}), \
                 patch.object(scanner, "_import_stock", return_value=fake), \
                 patch.object(scanner, "business_days", return_value=days), \
                 patch.object(scanner, "ranking", return_value=all_ranked), \
                 patch.object(scanner, "cap_map", return_value={"000001": 100_000_000_000}), \
                 patch.object(scanner, "OUT_PATH", str(path)):
                with self.assertRaisesRegex(RuntimeError, "시가총액이 누락"):
                    scanner.main()
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_market_rank_is_not_rebased_after_cap_filter_and_missing_rank_is_null(self):
        daily = {
            "inst": {"20260911": {"000001": ranked(100, 1), "000002": ranked(90, 2)}},
            "frgn": {"20260911": {"000001": ranked(100, 1)}},
        }
        detail = scanner.make_rank_details(daily, ["20260911"], {"000001": 10_000_000_000, "000002": 100_000_000_000})
        self.assertNotIn("000001", detail)
        self.assertEqual(detail["000002"]["inst"]["today"], 2)
        self.assertIsNone(detail["000002"]["frgn"]["today"])
        self.assertEqual(detail["000002"]["net_won"]["frgn"], 0)

    def test_failed_build_preserves_last_good_data_and_records_failure(self):
        script = Path(scanner.__file__).resolve()
        env = os.environ.copy()
        env.pop("KRX_ID", None)
        env.pop("KRX_PW", None)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "scanner_data.json"
            original = '{"asof":"20260911","rows":[["unchanged"]]}'
            path.write_text(original, encoding="utf-8")
            run = subprocess.run([sys.executable, str(script)], cwd=folder, env=env,
                                 capture_output=True, text=True, timeout=20)
            self.assertNotEqual(run.returncode, 0)
            self.assertEqual(path.read_text(encoding="utf-8"), original)
            status = json.loads((Path(folder) / "scanner_status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["status"], "failed")
            self.assertEqual(status["asof"], "20260911")
            self.assertIn("+09:00", status["finished_at"])


if __name__ == "__main__":
    unittest.main()
