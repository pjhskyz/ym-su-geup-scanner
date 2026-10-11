import unittest
from holdings_pipeline.issuers import normalize_sol, normalize_koact, normalize_plus, normalize_hanaro


class IssuerHoldingsTest(unittest.TestCase):
    def setUp(self):
        self.asset = {"id": "KR:475300", "symbol": "475300", "market": "KR"}

    def test_sol_rejects_other_date_and_other_etf(self):
        row = {"ETF_CD6": "475300", "FUND_CD": "211053", "WORK_DT": "20261008",
               "STOCK_CODE": "005930", "SEC_NM": "삼성전자", "WT_DISP": "16.5%", "QTY": 100}
        result = normalize_sol(self.asset, "2026-10-08", [row], set())
        self.assertEqual(result["holdings"][0]["weight_pct"], 16.5)
        self.assertIsNone(result["holdings"][0]["quantity"])
        self.assertEqual(result["quantity_basis"], "unknown")
        with self.assertRaises(ValueError):
            normalize_sol(self.asset, "2026-10-07", [row], set())
        with self.assertRaises(ValueError):
            normalize_sol(self.asset, "2026-10-08", [{**row, "ETF_CD6": "475310"}], set())

    def test_etf_cash_derivative_not_equities_and_ignore_live_price(self):
        asset = {"id": "KR:0150K0", "symbol": "0150K0"}
        rows = [{"itmNo": "069500", "secNm": "KODEX 200", "ratio": "5", "applyQ": "20", "curp": "999999"},
                {"itmNo": "KRD010010001", "secNm": "원화예금", "ratio": ""},
                {"itmNo": "165V00", "secNm": "코스피200선물", "ratio": "1"},
                {"itmNo": "KR7005930003", "secNm": "삼성전자", "ratio": "94"}]
        r = normalize_koact(asset, "2026-10-08", {"pdf": {"gijunYMD": "20261008", "list": rows, "totalCnt": 4}}, {"069500"})
        self.assertEqual([x["asset_type"] for x in r["holdings"]], ["fund", "cash", "derivative", "equity"])
        self.assertEqual(r["holdings"][-1]["code"], "005930")
        self.assertNotIn("curp", r["holdings"][0])
        self.assertIsNone(r["holdings"][0]["quantity"])

    def test_koact_count_mismatch_not_complete(self):
        r = normalize_koact({"id": "KR:0150K0"}, "2026-10-08", {"pdf": {"gijunYMD": "20261008", "totalCnt": 2, "list": [{"itmNo": "005930", "secNm": "삼성전자", "ratio": "100"}]}}, set())
        self.assertFalse(r["complete"])

    def test_plus_date_fallback_and_pagination_fail_closed(self):
        a = {"id": "KR:449450"}
        p = {"content": [{"wkdate": "20261008", "jmCd": "012450", "krJmCd": "KR7012450003", "jmNm": "한화에어로스페이스", "ratio": 20.1234}], "totalElements": 11, "last": False, "number": 0}
        r = normalize_plus(a, "2026-10-08", p, set())
        self.assertFalse(r["complete"])
        self.assertEqual(r["holdings"][0]["weight_pct"], 20.1234)
        with self.assertRaises(ValueError):
            normalize_plus(a, "2026-10-09", p, set())

    def test_hanaro_requires_published_date_and_all_hidden_rows(self):
        a = {"id": "KR:322400"}
        doc = '''<input type="text" id="pdfDate" value="2026.10.08"><tbody data-target="fundHoldingsListTableBody"><tr style="display:none"><td>1</td><td>KR7035720002</td><th>카카오</th><td>10</td><td>10000</td><td>100.0</td></tr></tbody><i data-target="pdfTotalCount">1</i>'''
        r = normalize_hanaro(a, "2026-10-08", doc, set())
        self.assertTrue(r["complete"])
        self.assertEqual(r["holdings"][0]["code"], "035720")
        with self.assertRaises(ValueError):
            normalize_hanaro(a, "2026-10-07", doc, set())


if __name__ == "__main__":
    unittest.main()
