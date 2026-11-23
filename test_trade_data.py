import datetime as dt
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import build_trade_data as T


def xml(items, code="00", message="NORMAL SERVICE."):
    body = "".join(
        f"<item><balPayments>0</balPayments><expDlr>{e}</expDlr><expWgt>{ew}</expWgt><hsCd>{hs}</hsCd>"
        f"<impDlr>{i}</impDlr><impWgt>{iw}</impWgt><statKor>품목</statKor><year>{year}</year></item>"
        for year, hs, e, ew, i, iw in items)
    return f"<response><header><resultCode>{code}</resultCode><resultMsg>{message}</resultMsg></header><body><items>{body}</items></body></response>"


class TradeDataTest(unittest.TestCase):
    def test_month_helpers(self):
        self.assertEqual(T.month_add("202601", -1), "202512")
        self.assertEqual(T.month_add("202512", 2), "202602")
        self.assertEqual(T.month_range("202511", "202602"), ["202511", "202512", "202601", "202602"])
        self.assertEqual(T.chunks("202501", "202602"), [("202501", "202512"), ("202601", "202602")])
        self.assertEqual(len(T.chunks("202101", "202601")), 6)
        self.assertTrue(all(len(T.month_range(a, b)) <= 12 for a, b in T.chunks("202101", "202601")))

    def test_latest_month_follows_mid_month_release(self):
        self.assertEqual(T.latest_month(dt.datetime(2026, 11, 14, tzinfo=T.KST)), "202609")
        self.assertEqual(T.latest_month(dt.datetime(2026, 11, 15, tzinfo=T.KST)), "202610")
        self.assertEqual(T.latest_month(dt.datetime(2026, 1, 20, tzinfo=T.KST)), "202512")

    def test_key_is_decoded_once(self):
        self.assertEqual(T.normalize_key(" abc%2Bdef%3D%3D "), "abc+def==")
        self.assertEqual(T.normalize_key("abc+def=="), "abc+def==")
        self.assertEqual(T.normalize_key(None), "")

    def test_parse_uses_exact_rows_and_drops_total(self):
        text = xml([("2026.09", "854232", "1,000", 10, 200, 2), ("2026.09", "8542321010", 700, 7, 150, 1),
                    ("2026.10", "854232", 1200, 12, 210, 2), ("총계", "854232", 2200, 22, 410, 4), ("2026.10", "999999", 5, 5, 5, 5)])
        self.assertEqual(T.parse_response(text, "854232"), {
            "202609": {"exp": 1000.0, "imp": 200.0, "exp_wgt": 10.0, "imp_wgt": 2.0},
            "202610": {"exp": 1200.0, "imp": 210.0, "exp_wgt": 12.0, "imp_wgt": 2.0}})

    def test_parse_sums_detail_rows_when_no_exact_row(self):
        text = xml([("2026.10", "8542321010", 700, 7, 150, 1), ("2026.10", "8542329000", 300, 3, "", 1)])
        self.assertEqual(T.parse_response(text, "854232")["202610"], {"exp": 1000.0, "imp": 150.0, "exp_wgt": 10.0, "imp_wgt": 2.0})

    def test_parse_raises_on_api_error(self):
        with self.assertRaisesRegex(ValueError, "30"):
            T.parse_response(xml([], code="30", message="SERVICE_KEY_IS_NOT_REGISTERED_ERROR"), "854232")
        gateway = "<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>SERVICE ERROR</errMsg><returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg><returnReasonCode>30</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>"
        with self.assertRaisesRegex(ValueError, "SERVICE_KEY_IS_NOT_REGISTERED_ERROR"):
            T.parse_response(gateway, "854232")

    def test_merge_keeps_stored_values_and_never_fills_zero(self):
        previous = {"months": ["202608", "202609"], "exp": [10, 20], "imp": [1, 2], "exp_wgt": [None, 5], "imp_wgt": [1]}
        merged = T.merge_series(previous, {"202609": {"exp": 25, "imp": None, "exp_wgt": 6, "imp_wgt": 3}}, ["202608", "202609", "202610"])
        self.assertEqual(merged["exp"], [10, 25, None])
        self.assertEqual(merged["imp"], [1, 2, None], "새 응답의 빈 값은 보관값을 지우지 않는다")
        self.assertEqual(merged["exp_wgt"], [None, 6, None])
        self.assertEqual(merged["imp_wgt"], [1, 3, None])

    def test_build_backfills_then_refreshes_and_isolates_failures(self):
        now = dt.datetime(2026, 11, 23, 23, 20, tzinfo=T.KST)
        items = [{"id": "a", "label": "A", "hs": "1111", "stocks": [{"code": "000001", "name": "가"}]}, {"id": "b", "label": "B", "hs": "2222"}]
        calls = []

        def fetcher(key, hs, start, end):
            calls.append((hs, start, end))
            if hs == "2222":
                raise ValueError("API 오류 22: LIMITED")
            return {month: {"exp": 100.0, "imp": 50.0, "exp_wgt": 10.0, "imp_wgt": 5.0} for month in T.month_range(start, end)}

        first = T.build(items, None, "k", now, fetcher, pause=0)
        self.assertEqual((first["target_month"], first["latest_month"]), ("202610", "202610"))
        a, b = first["items"]
        self.assertEqual(len(a["months"]), T.HISTORY_MONTHS)
        self.assertEqual(a["months"][0], "202110")
        self.assertTrue(all(v == 100.0 for v in a["exp"]))
        self.assertTrue(all(v is None for v in b["exp"]), "실패 품목은 값을 지어내지 않는다")
        self.assertEqual([e["id"] for e in first["errors"]], ["b"])
        self.assertEqual(sum(1 for c in calls if c[0] == "1111"), 6, "첫 수집은 12개월 단위로 5년치")
        self.assertEqual(sum(1 for c in calls if c[0] == "2222"), 1, "오류가 나면 그 품목은 바로 중단")
        calls.clear()
        second = T.build(items, first, "k", dt.datetime(2026, 12, 16, tzinfo=T.KST), fetcher, pause=0)
        self.assertEqual([c for c in calls if c[0] == "1111"], [("1111", "202609", "202611")], "이후에는 최근 3개월만")
        self.assertEqual(second["items"][0]["months"][-1], "202611")
        self.assertEqual(second["items"][0]["exp"][0], 100.0, "창이 한 달 밀려도 보관값 유지")
        changed = [dict(items[0], hs="3333"), items[1]]
        calls.clear()
        T.build(changed, second, "k", dt.datetime(2026, 12, 16, tzinfo=T.KST), fetcher, pause=0)
        self.assertEqual(sum(1 for c in calls if c[0] == "3333"), 6, "HS 코드를 바꾸면 처음부터 다시 받는다")

    def test_main_writes_status_and_never_leaks_key(self):
        key = "abc+def=="
        with tempfile.TemporaryDirectory() as folder:
            items, out, status = Path(folder, "items.json"), Path(folder, "trade.json"), Path(folder, "status.json")
            items.write_text(json.dumps({"items": [{"id": "a", "label": "A", "hs": "1111"}]}), encoding="utf-8")
            args = ["--items", str(items), "--output", str(out), "--status", str(status)]

            def failing(k, hs, start, end):
                raise ValueError(f"요청 실패: https://x/?serviceKey={k} 그리고 abc%2Bdef%3D%3D")

            with mock.patch.dict(os.environ, {"DATA_GO_KR_KEY": "abc%2Bdef%3D%3D"}), mock.patch.object(T, "fetch", failing):
                self.assertEqual(T.main(args), 1)
            self.assertFalse(out.exists(), "한 건도 못 받으면 데이터 파일을 만들지 않는다")
            text = status.read_text(encoding="utf-8")
            self.assertNotIn(key, text)
            self.assertNotIn("abc%2Bdef", text)
            doc = json.loads(text)
            self.assertEqual((doc["ok"], doc["failed"], doc["latest_month"]), (False, 1, None))
            self.assertIn("***", doc["errors"][0]["error"])

            def working(k, hs, start, end):
                return {m: {"exp": 1.0, "imp": 2.0, "exp_wgt": 3.0, "imp_wgt": 4.0} for m in T.month_range(start, end)}

            with mock.patch.dict(os.environ, {"DATA_GO_KR_KEY": key}), mock.patch.object(T, "fetch", working), mock.patch.object(T.time, "sleep", lambda s: None):
                self.assertEqual(T.main(args), 0)
            self.assertTrue(json.loads(status.read_text(encoding="utf-8"))["ok"])
            self.assertEqual(len(json.loads(out.read_text(encoding="utf-8"))["items"][0]["months"]), T.HISTORY_MONTHS)
            with mock.patch.dict(os.environ, {"DATA_GO_KR_KEY": ""}):
                status.unlink()
                self.assertEqual(T.main(args), 0)
            self.assertFalse(status.exists(), "키가 없으면 상태 파일도 만들지 않는다")


if __name__ == "__main__":
    unittest.main()
