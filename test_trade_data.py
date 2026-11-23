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


def rows_xml(rows, total=None, code="00", message="NORMAL SERVICE."):
    body = "".join("<item>" + "".join(f"<{tag}>{value}</{tag}>" for tag, value in row.items()) + "</item>" for row in rows)
    count = "" if total is None else f"<totalCount>{total}</totalCount>"
    return f"<response><header><resultCode>{code}</resultCode><resultMsg>{message}</resultMsg></header><body><items>{body}</items>{count}</body></response>"


def prelim_row(month, days, base, split=False, width=11):
    """잠정치 한 행(천 달러). split이면 priodYear + priodMon(두 자리)로 나눠 준다."""
    period = {"priodYear": month[:4], "priodMon": month[4:]} if split else {"priodMon": month}
    return {**period, "priodDt": days, **{f"itemUsdAmt{i:02d}": f"{base + i:,}" for i in range(width)}}


def nation_row(month, code, name, exp, imp):
    return {"year": f"{month[:4]}.{month[4:]}", "statCd": code, "statCdCntnKor1": name, "expDlr": exp, "impDlr": imp, "balPayments": 0}


def fake_rows(calls, fail=()):
    """나머지 API를 흉내 낸다. 조회 구간의 달마다 행을 돌려주고, fail에 든 주소·HS는 오류를 낸다."""
    def rows(url, key, params):
        calls.append((url, dict(params)))
        if url in fail or params.get("hsSgn") in fail:
            raise ValueError(f"요청 실패: {url}?serviceKey={key}")
        months = T.month_range(params["strtYymm"], params["endYymm"])
        if url == T.NATION_URL:
            return [nation_row(m, code, name, exp, imp) for m in months for code, name, exp, imp in
                    (("CN", "중국", 300, 200), ("US", "미국", 200, 100), ("DE", "독일", 50, 40), ("VN", "베트남", 30, 20), ("ZZ", "기타국", 1, 1))]
        if url == T.ITEM_COUNTRY_URL:
            return [dict(nation_row(m, code, name, exp, imp), hsCd=params["hsSgn"] + "10") for m in months for code, name, exp, imp in
                    (("US", "미국", 60, 5), ("CN", "중국", 40, 20), ("JP", "일본", 0, 30))]
        return [prelim_row(m, days, 1000 * int(m[4:])) for m in months for days in ("01~10", "01~20", "01~31")]
    return rows


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

        extra = []
        first = T.build(items, None, "k", now, fetcher, pause=0, rows_fetcher=fake_rows(extra))
        self.assertEqual((first["target_month"], first["latest_month"]), ("202610", "202610"))
        self.assertEqual(first["section_errors"], [], "품목 실적이 실패해도 나머지 구역은 따로 받는다")
        a, b = first["items"]
        self.assertEqual(len(a["months"]), T.HISTORY_MONTHS)
        self.assertEqual(a["months"][0], "202110")
        self.assertTrue(all(v == 100.0 for v in a["exp"]))
        self.assertTrue(all(v is None for v in b["exp"]), "실패 품목은 값을 지어내지 않는다")
        self.assertEqual([e["id"] for e in first["errors"]], ["b"])
        self.assertEqual(sum(1 for c in calls if c[0] == "1111"), 6, "첫 수집은 12개월 단위로 5년치")
        self.assertEqual(sum(1 for c in calls if c[0] == "2222"), 1, "오류가 나면 그 품목은 바로 중단")
        calls.clear()
        second = T.build(items, first, "k", dt.datetime(2026, 12, 16, tzinfo=T.KST), fetcher, pause=0, rows_fetcher=fake_rows(extra))
        self.assertEqual([c for c in calls if c[0] == "1111"], [("1111", "202609", "202611")], "이후에는 최근 3개월만")
        self.assertEqual(second["items"][0]["months"][-1], "202611")
        self.assertEqual(second["items"][0]["exp"][0], 100.0, "창이 한 달 밀려도 보관값 유지")
        changed = [dict(items[0], hs="3333"), items[1]]
        calls.clear()
        T.build(changed, second, "k", dt.datetime(2026, 12, 16, tzinfo=T.KST), fetcher, pause=0, rows_fetcher=fake_rows(extra))
        self.assertEqual(sum(1 for c in calls if c[0] == "3333"), 6, "HS 코드를 바꾸면 처음부터 다시 받는다")

    def test_main_writes_status_and_never_leaks_key(self):
        key = "abc+def=="
        with tempfile.TemporaryDirectory() as folder:
            items, out, status = Path(folder, "items.json"), Path(folder, "trade.json"), Path(folder, "status.json")
            items.write_text(json.dumps({"items": [{"id": "a", "label": "A", "hs": "1111"}]}), encoding="utf-8")
            args = ["--items", str(items), "--output", str(out), "--status", str(status)]

            def failing(k, hs, start, end):
                raise ValueError(f"요청 실패: https://x/?serviceKey={k} 그리고 abc%2Bdef%3D%3D")

            def failing_rows(url, k, params):
                raise ValueError(f"요청 실패: {url}?serviceKey={k}")

            with mock.patch.dict(os.environ, {"DATA_GO_KR_KEY": "abc%2Bdef%3D%3D"}), mock.patch.object(T, "fetch", failing), mock.patch.object(T, "request_rows", failing_rows):
                self.assertEqual(T.main(args), 1)
            self.assertFalse(out.exists(), "한 건도 못 받으면 데이터 파일을 만들지 않는다")
            text = status.read_text(encoding="utf-8")
            self.assertNotIn(key, text)
            self.assertNotIn("abc%2Bdef", text)
            doc = json.loads(text)
            self.assertEqual((doc["ok"], doc["failed"], doc["latest_month"]), (False, 1, None))
            self.assertIn("***", doc["errors"][0]["error"])
            self.assertEqual(doc["section_failed"], 3, "국가별 1 + 잠정치 2. 월별 실적이 실패한 품목은 국가 분해를 시도하지 않는다")
            self.assertTrue(all("***" in e["error"] for e in doc["section_errors"]))

            def working(k, hs, start, end):
                return {m: {"exp": 1.0, "imp": 2.0, "exp_wgt": 3.0, "imp_wgt": 4.0} for m in T.month_range(start, end)}

            with mock.patch.dict(os.environ, {"DATA_GO_KR_KEY": key}), mock.patch.object(T, "fetch", working), mock.patch.object(T, "request_rows", fake_rows([])), mock.patch.object(T.time, "sleep", lambda s: None):
                self.assertEqual(T.main(args), 0)
            doc = json.loads(status.read_text(encoding="utf-8"))
            self.assertTrue(doc["ok"])
            self.assertEqual(doc["section_failed"], 0)
            self.assertEqual(doc["sections"]["item_countries"]["items"], 1)
            self.assertEqual(doc["fields"]["prelim_item"][:3], ["itemUsdAmt00", "itemUsdAmt01", "itemUsdAmt02"], "응답 항목명을 상태 파일에 남긴다")
            self.assertIn("statCd", doc["fields"]["nations"])
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(len(data["items"][0]["months"]), T.HISTORY_MONTHS)
            self.assertEqual(data["schema_version"], 2)
            self.assertNotIn(key, out.read_text(encoding="utf-8"))
            with mock.patch.dict(os.environ, {"DATA_GO_KR_KEY": ""}):
                status.unlink()
                self.assertEqual(T.main(args), 0)
            self.assertFalse(status.exists(), "키가 없으면 상태 파일도 만들지 않는다")

    # ---- 공용 응답 처리 ----
    def test_parse_rows_reads_items_total_and_nodata(self):
        rows, total = T.parse_rows(rows_xml([{"a": "1", "b": " x "}, {"a": "2"}], total=7))
        self.assertEqual((rows, total), ([{"a": "1", "b": "x"}, {"a": "2"}], 7))
        self.assertEqual(T.parse_rows(rows_xml([{"a": "1"}]))[1], None)
        self.assertEqual(T.parse_rows(rows_xml([], code="03", message="NODATA_ERROR")), ([], 0))
        self.assertEqual(T.parse_rows(rows_xml([], code="99", message="NO DATA")), ([], 0))
        with self.assertRaisesRegex(ValueError, "22"):
            T.parse_rows(rows_xml([], code="22", message="LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR"))

    def test_request_rows_follows_pages_without_double_counting(self):
        size = T.PAGE_SIZE
        data = [{"n": str(i)} for i in range(2 * size + 300)]

        def run(pager):
            calls = []

            def counted(url, key, params):
                calls.append(params["pageNo"])
                return pager(params["pageNo"])
            return T.request_rows("u", "k", {"x": 1}, pager=counted), calls

        paged = lambda page: (data[(page - 1) * size:page * size], len(data))
        self.assertEqual(run(paged), (data, [1, 2, 3]), "쪽 나눔을 하면 totalCount까지 이어 받는다")
        self.assertEqual(run(lambda page: (data, len(data))), (data, [1]), "한 번에 다 주면 한 번만 부른다")
        self.assertEqual(run(lambda page: (data, None)), (data, [1]), "totalCount가 없고 한 쪽보다 많이 오면 전부로 본다")
        self.assertEqual(run(lambda page: (data[:size], None)), (data[:size], [1, 2]), "딱 한 쪽이면 다음 쪽을 확인하고, 같은 내용이면 두 번 세지 않는다")
        self.assertEqual(run(lambda page: (data[(page - 1) * size:page * size], None)), (data, [1, 2, 3]), "totalCount 없이 쪽 나눔을 해도 끝까지")
        self.assertEqual(run(lambda page: ([], 0)), ([], [1]))
        with self.assertRaisesRegex(ValueError, "잘렸"):
            run(lambda page: (data[:size], len(data)))

    def test_stale_waits_for_new_month_then_refreshes_weekly(self):
        now = dt.datetime(2026, 11, 23, 23, 20, tzinfo=T.KST)
        fresh = {"latest_month": "202610", "fetched_at": "2026-11-20T23:20:00+09:00"}
        self.assertFalse(T.stale(fresh, "202610", now))
        self.assertTrue(T.stale(dict(fresh, fetched_at="2026-11-16T23:20:00+09:00"), "202610", now), "7일이 지나면 정정치를 다시 받는다")
        self.assertTrue(T.stale(fresh, "202611", now), "목표 월이 아직 안 들어왔으면 매번 다시 확인")
        self.assertTrue(T.stale(None, "202610", now))
        self.assertTrue(T.stale({"latest_month": "202610"}, "202610", now))
        self.assertTrue(T.stale({"latest_month": "202610", "fetched_at": "2026-11-20T23:20:00"}, "202610", now), "시간대 없는 값은 믿지 않는다")

    # ---- 10일 단위 잠정치 ----
    def test_prelim_period_reads_both_month_layouts(self):
        self.assertEqual(T.prelim_period({"priodMon": "202611", "priodDt": "01~10"}), ("202611", "d10"))
        self.assertEqual(T.prelim_period({"priodYear": "2026", "priodMon": "9", "priodDt": "1~20"}), ("202609", "d20"))
        self.assertEqual(T.prelim_period({"priodMon": "2026.02", "priodDt": "01~28"}), ("202602", "m"))
        self.assertEqual(T.prelim_period({"priodMon": "202610", "priodDt": "1일~말일"}), ("202610", "m"))
        self.assertEqual(T.prelim_period({"priodMon": "202610", "priodDt": "0131"}), ("202610", "m"))
        self.assertIsNone(T.prelim_period({"priodMon": "202610", "priodDt": "01~15"}))
        self.assertIsNone(T.prelim_period({"priodMon": "202613", "priodDt": "01~10"}))
        self.assertIsNone(T.prelim_period({"priodDt": "01~10"}))

    def test_parse_prelim_converts_thousand_usd_and_names_unknown_fields(self):
        parsed = T.parse_prelim([prelim_row("202611", "01~10", 33460308), prelim_row("202611", "01~20", 77400000, split=True),
                                 {"priodMon": "202611", "priodDt": "01~30", "itemUsdAmt00": "", "itemUsdAmt01": "-"}, {"priodMon": "총계"}])
        self.assertEqual(parsed["202611"]["d10"][:2], [33460308000, 33460309000])
        self.assertEqual(len(parsed["202611"]["d10"]), 11)
        self.assertEqual(parsed["202611"]["d20"][10], 77400010000)
        self.assertEqual(parsed["202611"]["m"], [None, None], "빈 값은 0으로 바꾸지 않는다")
        self.assertEqual(T.parse_prelim([]), {})
        with self.assertRaisesRegex(ValueError, "expAmt.*priodMon"):
            T.parse_prelim([{"priodMon": "202611", "priodDt": "01~10", "expAmt": "1"}])

    def test_merge_prelim_keeps_stored_values_and_finds_latest(self):
        months = ["202609", "202610", "202611"]
        before = [{"name": "전체", "d10": [1, 2], "d20": [3, 4], "m": [5, None]}, {"name": "반도체", "d10": [7, 8], "d20": [None, None], "m": [None, None]}]
        merged = T.merge_prelim(before, ["202609", "202610"], {"202610": {"m": [60, 61]}, "202611": {"d10": [70, None]}}, months, ["전체", "반도체"])
        self.assertEqual(merged[0], {"name": "전체", "d10": [1, 2, 70], "d20": [3, 4, None], "m": [5, 60, None]})
        self.assertEqual(merged[1], {"name": "반도체", "d10": [7, 8, None], "d20": [None, None, None], "m": [None, 61, None]})
        self.assertEqual(T.prelim_latest(merged, months), {"month": "202611", "span": "d10"})
        self.assertEqual(T.prelim_latest(T.merge_prelim(before, ["202609", "202610"], {}, months, ["전체", "반도체"]), months), {"month": "202610", "span": "d20"})
        self.assertIsNone(T.prelim_latest([], months))
        wide = T.merge_prelim(None, [], {"202611": {"d10": [1, 2, 3]}}, months, ["전체"])
        self.assertEqual([row["name"] for row in wide], ["전체", "항목 01", "항목 02"], "명세보다 칸이 많으면 버리지 않고 번호로 남긴다")

    def test_build_prelim_backfills_then_refreshes_recent_months(self):
        calls = []
        now = dt.datetime(2026, 11, 23, 23, 20, tzinfo=T.KST)
        rows_of = lambda section, url, params: fake_rows(calls)(url, "k", params)
        section, errors = T.build_prelim(None, "k", now, rows_of, pause=0)
        self.assertEqual(errors, [])
        self.assertEqual((section["months"][0], section["months"][-1], len(section["months"])), ("202111", "202611", T.HISTORY_MONTHS))
        self.assertEqual(section["latest"], {"month": "202611", "span": "m"})
        self.assertEqual([row["name"] for row in section["country"]][:3], ["전체", "중국", "미국"])
        self.assertEqual(section["item"][1]["d10"][-1], (11000 + 1) * 1000)
        self.assertEqual(len(calls), 12, "처음에는 두 API를 5년치(1년 단위)로 받는다")
        calls.clear()
        again, errors = T.build_prelim(section, "k", dt.datetime(2026, 12, 2, tzinfo=T.KST), rows_of, pause=0)
        self.assertEqual([c[1]["strtYymm"] for c in calls], ["202512", "202612", "202512", "202612"], "이후에는 최근 13개월만")
        self.assertEqual((again["months"][0], again["item"][0]["d10"][0]), ("202112", 12000 * 1000), "창이 밀려도 보관값을 유지한다")
        broken, errors = T.build_prelim(again, "k", dt.datetime(2026, 12, 2, tzinfo=T.KST),
                                        lambda section, url, params: fake_rows([], fail=(T.PRELIM_URLS["country"],))(url, "k", params), pause=0)
        self.assertEqual([e["section"] for e in errors], ["prelim_country"])
        self.assertEqual(broken["country"], again["country"], "실패한 쪽은 직전 값을 유지한다")

    # ---- 국가별 확정치 ----
    def test_country_table_drops_total_rows_and_picks_one_hs_level(self):
        rows = [nation_row("202610", "US", "미국", "1,000", 400), nation_row("202610", "CN", "중국", 500, ""), nation_row("202609", "US", "미국", 900, 300),
                {"year": "총계", "statCd": "-", "expDlr": 2400, "impDlr": 700}, nation_row("202610", "-", "총계", 1500, 400)]
        table, published = T.country_table(rows)
        self.assertEqual(published, {"202609", "202610"})
        self.assertEqual(table["US"], {"name": "미국", "exp": {"202610": 1000.0, "202609": 900.0}, "imp": {"202610": 400.0, "202609": 300.0}})
        self.assertEqual(table["CN"]["imp"], {})
        months = ["202608", "202609", "202610"]
        self.assertEqual(T.country_series(table, ["US", "CN", "XX"], "exp", months, published), [None, 900, 1500], "공표 전 달은 None, 공표된 달에 행이 없는 국가는 0")
        self.assertEqual(T.country_series(table, ["CN"], "imp", months, published), [None, 0, 0])
        hs_rows = [dict(nation_row("202610", "US", "미국", 700, 10), hsCd="8542"), dict(nation_row("202610", "US", "미국", 300, 4), hsCd="854231"),
                   dict(nation_row("202610", "US", "미국", 5, 5), hsCd="9999")]
        self.assertEqual(T.country_table(hs_rows, "8542")[0]["US"]["exp"], {"202610": 700.0}, "요청 코드와 같은 행이 있으면 그 행만")
        deep = [dict(nation_row("202610", "US", "미국", 600, 0), hsCd="854231"), dict(nation_row("202610", "US", "미국", 350, 0), hsCd="8542311000"),
                dict(nation_row("202610", "US", "미국", 250, 0), hsCd="8542319000"), dict(nation_row("202610", "CN", "중국", 90, 0), hsCd="8542321010")]
        table, _ = T.country_table(deep, "8542")
        self.assertEqual((table["US"]["exp"], table["CN"]["exp"]), ({"202610": 600.0}, {"202610": 90.0}), "하위 코드는 가장 세분된 단위만 더한다")

    def test_build_nations_ranks_adds_regions_and_skips_when_fresh(self):
        calls = []
        now = dt.datetime(2026, 11, 23, 23, 20, tzinfo=T.KST)
        rows_of = lambda section, url, params: fake_rows(calls)(url, "k", params)
        nations, errors = T.build_nations(None, "k", "202610", now, rows_of, pause=0)
        self.assertEqual((errors, len(calls), nations["latest_month"], nations["countries"]), ([], 6, "202610", 5))
        self.assertEqual([(r["code"], r["kind"]) for r in nations["rows"]], [("ALL", "total"), ("EU27", "region"), ("ASEAN10", "region"), ("CN", "country"), ("US", "country"), ("DE", "country"), ("VN", "country"), ("ZZ", "country")])
        by = {r["code"]: r for r in nations["rows"]}
        self.assertEqual((by["ALL"]["exp"][-1], by["ALL"]["imp"][-1], by["EU27"]["exp"][-1], by["ASEAN10"]["imp"][-1]), (581, 361, 50, 20))
        self.assertEqual(len(by["CN"]["exp"]), T.HISTORY_MONTHS)
        calls.clear()
        same, errors = T.build_nations(nations, "k", "202610", now + dt.timedelta(days=3), rows_of, pause=0)
        self.assertEqual((same is nations, calls), (True, []), "새 달이 들어온 뒤 7일 안에는 다시 받지 않는다")
        kept, errors = T.build_nations(nations, "k", "202611", now + dt.timedelta(days=30),
                                       lambda section, url, params: fake_rows([], fail=(T.NATION_URL,))(url, "k", params), pause=0)
        self.assertEqual((kept is nations, [e["section"] for e in errors]), (True, ["nations"]), "실패하면 직전 값을 유지한다")
        empty, errors = T.build_nations(None, "k", "202610", now, lambda section, url, params: [{"year": "총계", "weird": "1"}], pause=0)
        self.assertEqual(empty, None)
        self.assertIn("weird", errors[0]["error"], "해석하지 못한 응답은 항목명을 남긴다")

    def test_build_adds_country_breakdown_and_isolates_section_failures(self):
        now = dt.datetime(2026, 11, 23, 23, 20, tzinfo=T.KST)
        items = [{"id": f"i{n}", "label": f"품목{n}", "hs": f"{n}{n}{n}{n}"} for n in range(1, 6)]
        fetcher = lambda key, hs, start, end: {m: {"exp": 100.0, "imp": 50.0, "exp_wgt": 10.0, "imp_wgt": 5.0} for m in T.month_range(start, end)}
        calls, fields = [], {}
        first = T.build(items, None, "k", now, fetcher, pause=0, rows_fetcher=fake_rows(calls), fields=fields)
        self.assertEqual(first["section_errors"], [])
        c = first["items"][0]["countries"]
        self.assertEqual((c["hs"], c["latest_month"], len(c["months"]), c["months"][0], c["countries"]), ("1111", "202610", T.COUNTRY_MONTHS, "202411", 3))
        self.assertEqual([(r["code"], r["values"][-1]) for r in c["exp"]], [("US", 60), ("CN", 40)], "수출 실적이 0인 국가는 순위에 넣지 않는다")
        self.assertEqual([r["code"] for r in c["imp"]], ["JP", "CN", "US"])
        self.assertEqual((c["total"]["exp"][-1], c["total"]["imp"][0]), (100, 55))
        self.assertEqual(sum(1 for url, _ in calls if url == T.ITEM_COUNTRY_URL), 10, "품목마다 24개월을 1년 단위 두 번")
        self.assertEqual(sorted(fields), ["item_countries", "nations", "prelim_country", "prelim_item"])
        self.assertIn("hsCd", fields["item_countries"])
        calls.clear()
        second = T.build(items, first, "k", now + dt.timedelta(days=2), fetcher, pause=0, rows_fetcher=fake_rows(calls))
        self.assertEqual([url for url, _ in calls if url in (T.ITEM_COUNTRY_URL, T.NATION_URL)], [], "국가별 확정치는 매번 다시 받지 않는다")
        self.assertEqual(second["items"][0]["countries"], c)
        self.assertEqual(sum(1 for url, _ in calls if url in T.PRELIM_URLS.values()), 4, "잠정치는 매번 확인한다")
        calls.clear()
        third = T.build(items, second, "k!", dt.datetime(2026, 12, 16, tzinfo=T.KST), fetcher, pause=0, rows_fetcher=fake_rows(calls, fail=("2222", "3333", "4444", T.NATION_URL)))
        self.assertEqual([e.get("id") for e in third["section_errors"]], ["i2", "i3", "i4", None])
        self.assertTrue(all("k!" not in e["error"] and "***" in e["error"] for e in third["section_errors"]), "오류 문구에서 인증키를 가린다")
        self.assertEqual(third["items"][0]["countries"]["latest_month"], "202611")
        self.assertEqual(third["items"][1]["countries"], c | {"hs": "2222"}, "실패한 품목은 직전 국가 분해를 유지한다")
        self.assertEqual(third["items"][4]["countries"]["latest_month"], "202610", "연달아 세 번 실패하면 남은 품목은 이번에 시도하지 않는다")
        self.assertEqual([p["hsSgn"] for url, p in calls if url == T.ITEM_COUNTRY_URL], ["1111", "1111", "2222", "3333", "4444"])
        self.assertEqual(third["nations"], second["nations"])
        renamed = T.build([dict(items[0], hs="9999")], third, "k", dt.datetime(2026, 12, 16, tzinfo=T.KST), fetcher, pause=0, rows_fetcher=fake_rows([]))
        self.assertEqual(renamed["items"][0]["countries"]["hs"], "9999", "HS 코드를 바꾸면 국가 분해도 새로 받는다")

        def crashing(url, key, params):
            raise KeyError("뜻밖의 응답")

        crashed = T.build(items[:1], third, "k", dt.datetime(2027, 1, 16, tzinfo=T.KST), fetcher, pause=0, rows_fetcher=crashing)
        self.assertEqual([e["section"] for e in crashed["section_errors"]], ["item_countries", "nations", "prelim"])
        self.assertTrue(all(e["error"].startswith("KeyError") for e in crashed["section_errors"]))
        self.assertEqual((crashed["latest_month"], crashed["nations"], crashed["prelim"]), ("202612", third["nations"], third["prelim"]), "새 구역이 터져도 품목 월별 실적은 발행하고 직전 값을 유지한다")
        self.assertEqual(T.status_doc(T.build(items[:1], None, "k", now, fetcher, pause=0, rows_fetcher=crashing), now)["sections"]["prelim"], {"latest": None, "fetched_at": None})


if __name__ == "__main__":
    unittest.main()
