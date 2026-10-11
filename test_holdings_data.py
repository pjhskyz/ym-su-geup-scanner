import copy
import unittest

from holdings_pipeline.logic import aggregate_comparisons, compare_snapshots


def holding(code="005930", weight=16.9, quantity=100, **overrides):
    return {"code": code, "isin": None, "name": "삼성전자", "asset_type": "equity",
            "weight_pct": weight, "quantity": quantity, **overrides}


def snapshot(day="2026-10-07", **overrides):
    return {"etf_id": "KR:091160", "date": day, "date_kind": "provider_date",
            "basis": "creation_basket", "source": "KODEX",
            "source_url": "https://www.samsungfund.com/", "observed_at": None,
            "collection_mode": "live", "weight_basis": "excluding_cash",
            "complete": True, "reported_count": 1, "creation_unit_shares": 50000,
            "quantity_basis": "per_cu", "holdings": [holding()], **overrides}


class SnapshotComparisonTests(unittest.TestCase):
    def setUp(self):
        self.previous = snapshot()
        self.current = snapshot("2026-10-08", holdings=[holding(weight=17.0)])

    def test_percentage_points_and_quantity_unchanged(self):
        result = compare_snapshots(self.previous, self.current)
        row = result["changes"][0]
        self.assertEqual(result["status"], "ready")
        self.assertEqual(row["delta_weight_pp"], 0.1)
        self.assertEqual(row["event"], "increased")
        self.assertEqual(row["quantity_status"], "unchanged")
        self.assertEqual(row["quantity_change_pct"], 0)
        self.assertIn("실제 매매", row["quantity_note"])

    def test_same_or_older_date_is_stale_not_zero_change(self):
        self.assertEqual(compare_snapshots(self.previous, self.previous)["status"], "stale")
        self.assertEqual(compare_snapshots(self.current, self.previous)["changes"], [])

    def test_missing_snapshot_waits(self):
        self.assertEqual(compare_snapshots(None, self.current)["status"], "waiting")
        self.assertEqual(compare_snapshots(self.previous, None)["changes"], [])

    def test_partial_holdings_cannot_be_exit(self):
        self.current.update(complete=False, holdings=[])
        result = compare_snapshots(self.previous, self.current)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["changes"], [])

    def test_complete_missing_equity_is_removed(self):
        self.current["holdings"] = [holding(code="000660", name="SK하이닉스", weight=20)]
        result = compare_snapshots(self.previous, self.current)
        rows = {x["code"]: x for x in result["changes"]}
        self.assertEqual(rows["005930"]["event"], "removed")
        self.assertEqual(rows["005930"]["current_weight_pct"], 0)
        self.assertEqual(rows["000660"]["event"], "new")
        self.assertIsNone(rows["000660"]["quantity_change_pct"])

    def test_rename_same_code_not_new_or_removed(self):
        self.current["holdings"][0]["name"] = "변경된 이름"
        result = compare_snapshots(self.previous, self.current)
        self.assertEqual(len(result["changes"]), 1)
        self.assertEqual(result["changes"][0]["name"], "변경된 이름")
        self.assertEqual(result["changes"][0]["event"], "increased")

    def test_source_basis_weight_basis_and_date_kind_must_match(self):
        for key, value in [("source", "NAVER"), ("basis", "fund_holdings"),
                           ("weight_basis", "nav"), ("date_kind", "observation_date"),
                           ("etf_id", "KR:091230")]:
            with self.subTest(key=key):
                different = copy.deepcopy(self.current)
                different[key] = value
                self.assertEqual(compare_snapshots(self.previous, different)["status"], "basis_mismatch")

    def test_observation_date_pair_remains_labelled(self):
        self.previous["date_kind"] = self.current["date_kind"] = "observation_date"
        result = compare_snapshots(self.previous, self.current)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["date_kind"], "observation_date")
        self.assertIn("조회일", result["reason"])

    def test_cu_doubling_is_not_quantity_growth(self):
        self.current["creation_unit_shares"] = 100000
        self.current["holdings"][0]["quantity"] = 200
        row = compare_snapshots(self.previous, self.current)["changes"][0]
        self.assertEqual(row["quantity_status"], "unchanged")
        self.assertEqual(row["quantity_change_pct"], 0)

    def test_quantity_per_etf_share_can_match_normalized_cu(self):
        self.current["quantity_basis"] = "per_etf_share"
        self.current["holdings"][0]["quantity"] = 100 / 50000
        self.assertEqual(compare_snapshots(self.previous, self.current)["changes"][0]["quantity_status"], "unchanged")

    def test_unknown_cu_fund_total_and_unknown_quantity_are_unavailable(self):
        for overrides in ({"creation_unit_shares": None}, {"creation_unit_shares": 0},
                          {"quantity_basis": "fund_total"}, {"quantity_basis": "unknown"}):
            with self.subTest(overrides=overrides):
                self.current.update(overrides)
                row = compare_snapshots(self.previous, self.current)["changes"][0]
                self.assertIsNone(row["quantity_change_pct"])
                self.assertEqual(row["quantity_status"], "unavailable")

    def test_quantity_increase_is_not_confirmed_purchase(self):
        self.current["holdings"][0]["quantity"] = 200
        row = compare_snapshots(self.previous, self.current)["changes"][0]
        self.assertEqual(row["quantity_change_pct"], 100)
        self.assertEqual(row["quantity_status"], "increased")
        self.assertIn("분할·합병 미검증", row["quantity_note"])

    def test_funds_cash_and_derivatives_not_aggregated_as_stocks(self):
        self.current["holdings"] += [holding(code="069500", asset_type="fund"),
                                     holding(code="CASH", asset_type="cash", weight=None),
                                     holding(code="FUTURE", asset_type="derivative")]
        self.assertEqual(len(compare_snapshots(self.previous, self.current)["changes"]), 1)

    def test_six_character_alphanumeric_equity_and_leading_zero(self):
        self.previous["holdings"] = [holding(code="0009K0")]
        self.current["holdings"] = [holding(code="0009K0", weight=17)]
        self.assertEqual(compare_snapshots(self.previous, self.current)["changes"][0]["code"], "0009K0")

    def test_duplicate_invalid_codes_and_invalid_weights_are_incomplete(self):
        for rows in ([holding(), holding()], [holding(code="cash")],
                     [holding(weight=None)], [holding(weight=float("nan"))]):
            with self.subTest(rows=rows):
                self.current["holdings"] = rows
                self.assertEqual(compare_snapshots(self.previous, self.current)["status"], "incomplete")

    def test_isin_change_requires_review(self):
        self.previous["holdings"][0]["isin"] = "KR7005930003"
        self.current["holdings"][0]["isin"] = "KR7005930004"
        self.assertEqual(compare_snapshots(self.previous, self.current)["status"], "incomplete")

    def test_threshold_is_not_rounding_of_raw_delta(self):
        self.current["holdings"][0]["weight_pct"] = 16.94
        row = compare_snapshots(self.previous, self.current)["changes"][0]
        self.assertEqual(row["event"], "unchanged")
        self.assertEqual(row["delta_weight_pp"], 0.04)
        self.current["holdings"][0]["weight_pct"] = 16.95
        self.assertEqual(compare_snapshots(self.previous, self.current)["changes"][0]["event"], "increased")
        self.current["holdings"][0]["weight_pct"] = 16.85
        self.assertEqual(compare_snapshots(self.previous, self.current)["changes"][0]["event"], "decreased")

    def test_missing_or_invalid_dates_are_not_observation_inferred(self):
        for value in (None, "", "2026-13-10"):
            self.current["date"] = value
            self.assertEqual(compare_snapshots(self.previous, self.current)["status"], "incomplete")

    def test_invalid_threshold_rejected(self):
        for value in (0, -1, float("nan"), True):
            with self.assertRaises(ValueError):
                compare_snapshots(self.previous, self.current, value)


class AggregationTests(unittest.TestCase):
    def fund(self, fund_id, delta=0.1, representative=True, issuer="삼성", **snapshot_overrides):
        first = snapshot(etf_id=fund_id, **snapshot_overrides)
        last = snapshot("2026-10-08", etf_id=fund_id,
                        holdings=[holding(weight=16.9 + delta)], **snapshot_overrides)
        comparison = compare_snapshots(first, last)
        return {"id": fund_id, "name": fund_id, "issuer": issuer, "group": "반도체",
                "representative": representative, "daily": comparison, "weekly": comparison}

    def test_positive_negative_and_unchanged_all_in_average(self):
        funds = [self.fund("a", 0.3), self.fund("b", 0.2, issuer="미래"),
                 self.fund("c", -0.1), self.fund("d", 0)]
        result = aggregate_comparisons(funds)
        row = result["cohorts"][0]["rows"][0]
        self.assertEqual(row["increased_count"], 2)
        self.assertEqual(row["decreased_count"], 1)
        self.assertEqual(row["observed_count"], 4)
        self.assertEqual(row["average_delta_weight_pp"], 0.1)
        self.assertEqual(row["issuer_count"], 2)

    def test_duplicate_fund_and_supplemental_not_double_counted(self):
        fund = self.fund("a")
        result = aggregate_comparisons([fund, fund, self.fund("b", representative=False)], min_increases=1)
        self.assertEqual(result["selected_count"], 1)
        self.assertEqual(result["cohorts"][0]["rows"][0]["increased_count"], 1)
        all_result = aggregate_comparisons([fund, self.fund("b", representative=False)], representatives_only=False)
        self.assertEqual(all_result["cohorts"][0]["rows"][0]["increased_count"], 2)

    def test_date_pairs_and_date_kind_cannot_mix(self):
        first, second, observed = self.fund("a"), self.fund("b"), self.fund("c", date_kind="observation_date")
        second["daily"]["from_date"] = "2026-10-06"
        result = aggregate_comparisons([first, second, observed], min_increases=1)
        self.assertEqual(len(result["cohorts"]), 3)
        self.assertTrue(all(c["comparable_count"] == 1 for c in result["cohorts"]))

    def test_failure_keeps_selected_denominator_not_false_zero_signals(self):
        failed = self.fund("a")
        failed["daily"] = {"status": "waiting", "changes": []}
        result = aggregate_comparisons([failed])
        self.assertEqual(result["selected_count"], 1)
        self.assertEqual(result["comparable_count"], 0)
        self.assertEqual(result["status"], "waiting")
        self.assertEqual(result["cohorts"], [])

    def test_comparable_without_filter_match_is_ready_empty_rows(self):
        result = aggregate_comparisons([self.fund("a", -0.1)])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["cohorts"][0]["rows"], [])

    def test_inputs_not_mutated(self):
        funds = [self.fund("a")]
        original = copy.deepcopy(funds)
        aggregate_comparisons(funds, min_increases=1)
        self.assertEqual(funds, original)


if __name__ == "__main__":
    unittest.main()
