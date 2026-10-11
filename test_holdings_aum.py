import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from holdings_pipeline.aum import (
    AumSourceBlockedError, collect_aum, import_archives, load_aum_history,
    normalize_naver, normalize_samsung, save_record,
)


ASSET = {"id": "KR:091160", "symbol": "091160", "market": "KR",
         "issuer_quote_url": "https://www.samsungfund.com/api/v1/kodex/product/2ETF07.do"}
OFFICIAL_URL = ASSET["issuer_quote_url"]
NAVER_URL = "https://stock.naver.com/api/domestic/detail/091160/ETFBase"
OBSERVED = "2026-10-11T14:00:00+00:00"


def official(day="20261007", nav="61567"):
    return {"info": {"product": {"stkTicker": "091160", "fId": "2ETF07",
                                "gijunYMD": day, "nav": nav, "fP": "145993.23"}},
            "pdf": {"gijunYMD": "20261012"},
            "suik": {"standardList": [{"EVAL_D": "20261008", "F_P": "145993.23"}]}}


def naver():
    return {"itemCode": "091160", "totalNetAssets": "6156748977753",
            "marketSum": "6216729", "nav": "145993.23", "issueAmount": "42525000",
            "tradeTime": "20261008203434", "referenceDate": "2026-10-08"}


class AumSourceTests(unittest.TestCase):
    def test_official_uses_net_assets_field_and_native_billion_won_unit(self):
        record = normalize_samsung(ASSET, official(), source_url=OFFICIAL_URL,
                                   observed_at=OBSERVED, expected_date="2026-10-07")
        self.assertEqual(record["value_krw"], 6_156_700_000_000)
        self.assertEqual(record["resolution_krw"], 100_000_000)
        self.assertEqual(record["asof"], "2026-10-07")
        self.assertEqual(record["metric"], "net_assets")
        self.assertEqual(record["value_field"], "info.product.nav")
        # Latest price date and the next-session basket are not the AUM date.
        self.assertNotEqual(record["asof"], "2026-10-08")

    def test_official_requested_date_does_not_relabel_current_aum(self):
        with self.assertRaisesRegex(ValueError, "aum_date_mismatch"):
            normalize_samsung(ASSET, official(), source_url=OFFICIAL_URL,
                              expected_date="2026-09-30")

    def test_official_identity_checks_both_ticker_and_fund_id(self):
        for key, value in (("stkTicker", "005930"), ("fId", "2ETF08")):
            payload = official()
            payload["info"]["product"][key] = value
            with self.assertRaisesRegex(ValueError, "identity_mismatch"):
                normalize_samsung(ASSET, payload, source_url=OFFICIAL_URL)

    def test_bad_financial_numbers_never_default_to_zero(self):
        for value in (None, "", "NaN", "Infinity", "0", "-1", True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_samsung(ASSET, official(nav=value), source_url=OFFICIAL_URL)

    def test_naver_aum_krw_never_market_cap_or_nav_times_current_shares(self):
        record = normalize_naver(ASSET, naver(), source_url=NAVER_URL, observed_at=OBSERVED)
        self.assertEqual(record["value_krw"], 6_156_748_977_753)
        self.assertNotEqual(record["value_krw"], int(naver()["marketSum"]) * 1_000_000)
        self.assertNotEqual(record["value_krw"], round(float(naver()["nav"]) * int(naver()["issueAmount"])))
        self.assertEqual(record["status"], "undated")
        self.assertIsNone(record["asof"])
        self.assertIsNone(record["date_field"])

    def test_naver_missing_net_assets_does_not_use_market_cap_fallback(self):
        payload = naver()
        del payload["totalNetAssets"]
        with self.assertRaisesRegex(ValueError, "aum_invalid_number"):
            normalize_naver(ASSET, payload, source_url=NAVER_URL)

    def test_naver_identity_and_currency_unit_validation(self):
        for field, value in (("itemCode", "005930"), ("totalNetAssets", "10.5")):
            payload = naver()
            payload[field] = value
            with self.assertRaises(ValueError):
                normalize_naver(ASSET, payload, source_url=NAVER_URL)

    def test_dated_history_and_undated_observations_remain_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = normalize_samsung(ASSET, official(), source_url=OFFICIAL_URL, observed_at=OBSERVED)
            b = normalize_naver(ASSET, naver(), source_url=NAVER_URL, observed_at=OBSERVED)
            save_record(tmp, a)
            save_record(tmp, b)
            rows = load_aum_history(tmp)[ASSET["id"]]
            self.assertEqual(rows["KODEX"]["2026-10-07"]["value_krw"], a["value_krw"])
            self.assertEqual(list(rows["NAVER"]), ["observed:" + OBSERVED])
            self.assertFalse((Path(tmp) / "NAVER" / "091160" / "2026-10-08.json").exists())

    def test_older_archive_cannot_overwrite_newer_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = normalize_samsung(ASSET, official(), source_url=OFFICIAL_URL, observed_at=OBSERVED)
            save_record(tmp, first)
            older = copy.deepcopy(first)
            older.update(observed_at="2026-10-10T14:00:00+00:00", value_krw=1)
            self.assertFalse(save_record(tmp, older))
            self.assertEqual(load_aum_history(tmp)[ASSET["id"]]["KODEX"][first["asof"]], first)

    def test_archive_hash_and_original_observation_time_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw"
            raw.mkdir()
            body = json.dumps(official()).encode()
            (raw / "sample.body").write_bytes(body)
            meta = {"url": OFFICIAL_URL, "status": 200, "retrieved_at": "20261009T221908563699",
                    "sha256": hashlib.sha256(body).hexdigest()}
            (raw / "sample.meta.json").write_text(json.dumps(meta))
            history = Path(tmp) / "history"
            statuses = import_archives([ASSET], [raw], history)
            self.assertEqual(statuses[0]["status"], "archived")
            record = load_aum_history(history)[ASSET["id"]]["KODEX"]["2026-10-07"]
            self.assertEqual(record["collection_mode"], "archived_official_response")
            self.assertEqual(record["observed_at"], "2026-10-09T22:19:08.563699+00:00")
            (raw / "sample.body").write_bytes(b"{}")
            status = import_archives([ASSET], [raw], Path(tmp) / "bad-history")
            self.assertEqual(status[0]["error"], "aum_raw_hash_mismatch")

    def test_access_restriction_opens_host_circuit_and_preserves_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            history, raw = Path(tmp) / "history", Path(tmp) / "raw"
            prior = normalize_samsung(ASSET, official(), source_url=OFFICIAL_URL, observed_at=OBSERVED)
            save_record(history, prior)
            calls = []
            assets = [{"id": "KR:091160", "symbol": "091160"},
                      {"id": "KR:300950", "symbol": "300950"}]
            def blocked(url):
                calls.append(url)
                raise AumSourceBlockedError("stock.naver.com", 429)
            statuses = collect_aum(assets, ["2026-10-08"], history, raw,
                                   fetch_json=blocked, pause=0)
            self.assertEqual(len(calls), 1)
            self.assertEqual(statuses[-1]["status"], "not_attempted")
            self.assertEqual(load_aum_history(history)[ASSET["id"]]["KODEX"]["2026-10-07"], prior)

    def test_future_official_record_not_saved_as_completed_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            statuses = collect_aum([ASSET], ["2026-10-08"], Path(tmp) / "history", Path(tmp) / "raw",
                                   fetch_json=lambda url: official("20261012") if "samsungfund" in url else naver(), pause=0)
            self.assertEqual(statuses[0]["status"], "future_date")
            records = load_aum_history(Path(tmp) / "history")[ASSET["id"]]
            self.assertNotIn("KODEX", records)
            self.assertEqual(next(iter(records["NAVER"].values()))["status"], "undated")

    def test_official_current_archive_avoids_duplicate_live_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            history = Path(tmp) / "history"
            save_record(history, normalize_samsung(ASSET, official("20261008"),
                        source_url=OFFICIAL_URL, observed_at=OBSERVED))
            calls = []
            statuses = collect_aum([ASSET], ["2026-10-08"], history, Path(tmp) / "raw",
                                   fetch_json=lambda url: calls.append(url), pause=0)
            self.assertFalse(calls)
            self.assertEqual(statuses[0]["status"], "stored_history")
            self.assertEqual(statuses[0]["mode"], "stored_history")

    def test_kodex_archive_only_does_not_fake_access_restriction(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            def fetch(url):
                calls.append(url)
                return naver()
            statuses = collect_aum([ASSET], ["2026-10-08"], Path(tmp) / "history", Path(tmp) / "raw",
                                   fetch_json=fetch, pause=0, kodex_archive_only=True)
            self.assertEqual(calls, [NAVER_URL])
            self.assertEqual(statuses[0]["reason"], "official_price_archive_only")

    def test_corrupt_history_missing_provenance_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = normalize_samsung(ASSET, official(), source_url=OFFICIAL_URL, observed_at=OBSERVED)
            bads = [[], None, dict(good, observed_at=None), dict(good, source="OTHER"),
                    dict(good, source_url="https://example.com/not-verified"),
                    dict(good, fund_id="wrong")]
            for i, bad in enumerate(bads):
                (root / f"bad-{i}.json").write_text(json.dumps(bad))
            self.assertEqual(load_aum_history(root), {})


if __name__ == "__main__":
    unittest.main()
