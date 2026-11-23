import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

import build_research_data as R


def scanner_row(name, sector, r1, r5, r20, code, mcap):
    row = [None] * 28
    row[R.COL_NAME], row[R.COL_SECTOR], row[R.COL_R1], row[R.COL_R5], row[R.COL_R20] = name, sector, r1, r5, r20
    row[R.COL_CODE], row[R.COL_MCAP] = code, mcap
    return row


def history(closes):
    return [closes, closes, closes, closes, [1] * len(closes)]


class ResearchDataTest(unittest.TestCase):
    def test_daily_returns_skip_missing_and_price_limit_breaks(self):
        returns = R.daily_returns([100, 110, None, 120, 60, 0, 50])
        self.assertIsNone(returns[0])
        self.assertAlmostEqual(returns[1], 0.10)
        self.assertIsNone(returns[2])
        self.assertIsNone(returns[3], "결측 다음 날은 계산하지 않는다")
        self.assertIsNone(returns[4], "-50%는 가격제한폭 밖(분할 등) → 제외")
        self.assertIsNone(returns[5])
        self.assertIsNone(returns[6])
        self.assertAlmostEqual(R.daily_returns([100, 130])[1], 0.30)

    def test_weighted_mean_ignores_invalid_pairs(self):
        self.assertAlmostEqual(R.weighted_mean([(1.0, 3), (5.0, 1), (None, 10), (9.0, 0)]), 2.0)
        self.assertIsNone(R.weighted_mean([(None, 1)]))

    def test_windows_require_complete_data(self):
        self.assertAlmostEqual(R.trailing_mean([1, 2, 3, 4], 3, 2), 3.5)
        self.assertIsNone(R.trailing_mean([1, None, 3], 2, 2))
        self.assertIsNone(R.trailing_mean([1, 2], 1, 5))
        self.assertAlmostEqual(R.cumulative_return([None, 0.1, 0.1], 2, 2), 0.21)
        self.assertIsNone(R.cumulative_return([None, 0.1], 1, 2))

    def test_universe_filters_bad_rows_and_sorts_by_cap(self):
        scanner = {"rows": [
            scanner_row("소형", "IT", 1.0, 2.0, 3.0, "000002", 600),
            scanner_row("대형", "", -1.0, None, 3.0, "000001", 9000),
            scanner_row("시총없음", "IT", 1.0, 1.0, 1.0, "000003", 0),
            ["짧은행"],
        ]}
        universe = R.universe_from_scanner(scanner)
        self.assertEqual([u["code"] for u in universe], ["000001", "000002"])
        self.assertEqual(universe[0]["sector"], R.UNCLASSIFIED)
        self.assertIsNone(universe[0]["r5"])

    def test_sector_summary_is_cap_weighted_and_counts_moves(self):
        universe = R.universe_from_scanner({"rows": [
            scanner_row("A", "IT", 2.0, 2.0, 2.0, "A", 3000),
            scanner_row("B", "IT", -2.0, None, 0.0, "B", 1000),
            scanner_row("C", "금융", 0.0, 1.0, 1.0, "C", 500),
            scanner_row("D", "", 5.0, 5.0, 5.0, "D", 99999),
        ]})
        sectors = R.sector_summary(universe)
        self.assertEqual(sectors[-1]["name"], R.UNCLASSIFIED, "미분류는 시총과 무관하게 맨 뒤")
        it = next(s for s in sectors if s["name"] == "IT")
        self.assertEqual((it["count"], it["cap"], it["up"], it["down"], it["flat"]), (2, 4000, 1, 1, 0))
        self.assertAlmostEqual(it["r1"], 1.0)
        self.assertAlmostEqual(it["r5"], 2.0, msg="결측 종목은 가중 평균에서 제외")

    def test_breadth_counts_rising_sectors_and_excludes_unclassified(self):
        universe = R.universe_from_scanner({"rows": [
            scanner_row("A", "IT", 0, 0, 0, "A", 1000), scanner_row("B", "금융", 0, 0, 0, "B", 1000),
            scanner_row("C", "", 0, 0, 0, "C", 1000), scanner_row("D", "IT", 0, 0, 0, "D", 1000),
        ]})
        stocks = {"dates": ["20260101", "20260102", "20260105"], "c": {
            "A": history([100, 110, 99]), "B": history([100, 90, 99]), "C": history([100, 120, 130]),
            "D": history([100, 100]),  # 길이가 다른 이력은 제외
        }}
        b = R.breadth_series(universe, stocks)
        self.assertEqual(b["n_sectors"], 2)
        self.assertEqual(b["covered_stocks"], 3)
        self.assertEqual(b["up_sectors"], [None, 1, 1])
        self.assertEqual(b["up_sector_ratio"], [None, 50.0, 50.0])
        self.assertEqual(b["up_stock_ratio"], [None, 66.7, 66.7])
        self.assertEqual(b["gap"], [None, None, None], "업종 수·이력이 모자라면 격차를 지어내지 않는다")
        self.assertEqual(b["oscillator"], [None, None, None])

    def test_gap_and_size_series_on_longer_history(self):
        n = R.GAP_WINDOW + 25
        rows, closes = [], {}
        for i in range(6):
            code = f"S{i}"
            rows.append(scanner_row(code, f"업종{i}", 0, 0, 0, code, 1000 * (6 - i)))
            closes[code] = history([100 * (1 + 0.001 * (i + 1)) ** t for t in range(n)])
        universe = R.universe_from_scanner({"rows": rows})
        stocks = {"dates": [f"d{t:03d}" for t in range(n)], "c": closes}
        b = R.breadth_series(universe, stocks)
        self.assertIsNone(b["gap"][R.GAP_WINDOW - 1])
        self.assertGreater(b["gap"][R.GAP_WINDOW], 0, "상위 3개 업종이 나머지보다 높다")
        self.assertIsNone(b["oscillator"][R.GAP_WINDOW + 18])
        self.assertIsNotNone(b["oscillator"][R.GAP_WINDOW + 19])
        self.assertEqual(b["up_sector_ratio"][-1], 100.0)
        size = R.size_series(universe, stocks)
        large = size["buckets"][0]
        self.assertEqual((large["count"], large["rank_from"], large["rank_to"]), (6, 1, 100))
        self.assertEqual(large["levels"][0], 100.0)
        self.assertGreater(large["levels"][-1], 100.0)
        self.assertEqual(len(large["levels"]), n)
        self.assertEqual(size["buckets"][2]["count"], 0)
        self.assertIsNone(size["buckets"][2]["r1"], "종목이 없는 구간은 수익률을 비운다")

    def test_build_and_cli_keep_previous_file_on_failure(self):
        rows = [scanner_row("A", "IT", 1.5, 2.5, 3.5, "A", 2000), scanner_row("B", "금융", -1.0, 0.0, 1.0, "B", 1000)]
        scanner = {"asof": "20260105", "updated": "2026-01-05T16:03:00+09:00", "rows": rows}
        stocks = {"dates": ["20260102", "20260105"], "updated": "2026-01-05 16:04", "c": {"A": history([100, 101]), "B": history([100, 99])}}
        now = dt.datetime(2026, 1, 5, 16, 5, tzinfo=R.KST)
        result = R.build(scanner, stocks, now)
        self.assertEqual(result["generated_at"], "2026-01-05T16:05:00+09:00")
        self.assertTrue(result["dates_match"])
        self.assertEqual(result["heatmap"]["rows"][0], ["A", "A", "IT", 2000, 1.5, 2.5, 3.5])
        self.assertFalse(R.build({**scanner, "asof": "20260106"}, stocks, now)["dates_match"])
        with self.assertRaises(ValueError):
            R.build({"rows": []}, stocks, now)
        with self.assertRaises(ValueError):
            R.build(scanner, {"dates": ["20260105"], "c": {}}, now)
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base / "scanner.json").write_text(json.dumps(scanner), encoding="utf-8")
            (base / "stocks.json").write_text(json.dumps(stocks), encoding="utf-8")
            out = base / "research.json"
            args = ["--scanner", str(base / "scanner.json"), "--stocks", str(base / "stocks.json"), "--output", str(out)]
            self.assertEqual(R.main(args), 0)
            first = out.read_text(encoding="utf-8")
            self.assertEqual(json.loads(first)["asof"], "20260105")
            (base / "scanner.json").write_text("{not json", encoding="utf-8")
            self.assertEqual(R.main(args), 1)
            self.assertEqual(out.read_text(encoding="utf-8"), first, "실패 시 기존 파일을 덮어쓰지 않는다")


if __name__ == "__main__":
    unittest.main()
