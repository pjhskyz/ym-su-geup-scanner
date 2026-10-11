import copy
import unittest
import urllib.error
from unittest.mock import patch

from holdings_pipeline.naver import (
    SourceBlockedError, _get_json, fetch_snapshot, normalize_rows,
)


def row(code="005930", isin="KR7005930003", **values):
    result = {
        "itemCode": "091160", "componentItemCode": code,
        "componentIsinCode": isin, "componentReutersCode": None,
        "componentMarketType": "0", "componentCount": "1",
        "componentName": "삼성전자", "referenceDate": "2026-10-08",
        "weight": "17.40", "cuUnitQuantity": "2346.0000",
    }
    result.update(values)
    return result


ASSET = {"id": "KR:091160", "symbol": "091160"}


class NaverHoldingsTest(unittest.TestCase):
    def normalize(self, rows):
        return normalize_rows(ASSET, rows, {"091160", "0008T0"},
                              source_url="https://stock.naver.com/", observed_at="2026-10-11T11:00:00Z")

    def test_actual_date_and_unknown_cu_size_preserved(self):
        result = self.normalize([row()])
        self.assertTrue(result["complete"])
        self.assertEqual(result["date"], "2026-10-08")
        self.assertEqual(result["holdings"][0]["weight_pct"], 17.4)
        self.assertEqual(result["holdings"][0]["quantity"], 2346)
        self.assertIsNone(result["creation_unit_shares"])
        self.assertEqual(result["quantity_basis"], "per_cu")

    def test_cash_fund_and_unverified_security_are_not_equity(self):
        for data, kind in [
            (row(None, "KRD010010001", componentName="원화현금"), "cash"),
            (row("0008T0", "KR70008T0001"), "fund"),
            (row("005930", None), "other"),
            (row("005930", "US0000000000"), "other"),
            (row(componentMarketType="4"), "derivative"),
        ]:
            self.assertEqual(self.normalize([data])["holdings"][0]["asset_type"], kind)

    def test_duplicate_date_count_and_missing_weight_fail_closed(self):
        examples = [
            [row(componentCount="2"), row(componentCount="2")],
            [row(referenceDate="2026-02-30")],
            [row(componentCount="2")],
            [row(weight="NaN")],
            [row(itemCode="367760")],
        ]
        for rows in examples:
            self.assertFalse(self.normalize(rows)["complete"])

    def test_pagination_uses_page_number_not_offset(self):
        calls = []
        pages = [[row(componentCount="3"), row("000660", "KR7000660001", componentCount="3")],
                 [row("042700", "KR7042700005", componentCount="3")]]
        def get(url):
            calls.append(url)
            return pages[len(calls) - 1]
        result = fetch_snapshot(ASSET, set(), fetch_json=get, page_size=2)
        self.assertTrue(result["complete"])
        self.assertIn("startIdx=1&pageSize=2", calls[1])
        self.assertEqual(result["reported_count"], 3)

    def test_count_claim_does_not_hide_missing_or_duplicated_pages(self):
        batches = [[row(componentCount="2")], []]
        result = fetch_snapshot(ASSET, set(), page_size=1, fetch_json=lambda _: batches.pop(0))
        self.assertFalse(result["complete"])
        batches = [[row(componentCount="2")], [row(componentCount="2")]]
        result = fetch_snapshot(ASSET, set(), page_size=1, fetch_json=lambda _: batches.pop(0))
        self.assertFalse(result["complete"])

    def test_http_access_limit_is_exposed_for_source_circuit_breaker(self):
        for code in (401, 403, 429):
            error = urllib.error.HTTPError("https://stock.naver.com/", code, "blocked", {}, None)
            with patch("urllib.request.urlopen", side_effect=error):
                with self.assertRaises(SourceBlockedError) as caught:
                    _get_json("https://stock.naver.com/")
                self.assertEqual(caught.exception.status_code, code)


if __name__ == "__main__":
    unittest.main()
