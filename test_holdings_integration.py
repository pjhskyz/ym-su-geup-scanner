"""Regression tests for dated holdings selection and safe history reuse."""

import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import build_holdings_data as build


ASSET = {"id": "KR:091160", "symbol": "091160", "name": "SOL 테스트",
         "market": "KR", "scope": "KR", "group": "반도체", "kind": "industry",
         "representative": True}
CONFIG = {"version": "test-v1", "assets": [ASSET]}
SESSIONS = {"KR": ["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02",
                   "2026-10-06", "2026-10-07", "2026-10-08"]}
EXPECTED = "2026-10-08"


def disclosure(day, source="SOL", complete=True, weight=10.0):
    return {
        "etf_id": ASSET["id"], "date": day, "date_kind": "provider_date",
        "basis": "creation_basket" if source != "NAVER" else "provider_holdings",
        "weight_basis": "issuer_non_cash" if source != "NAVER" else "provider_percentage",
        "source": source, "source_url": "https://example.com/public-holdings",
        "observed_at": "2026-10-11T12:00:00+00:00", "collection_mode": "live",
        "complete": complete, "reported_count": 1,
        "quantity_basis": "unknown", "creation_unit_shares": None,
        "holdings": [{"code": "005930", "isin": "KR7005930003", "name": "삼성전자",
                      "asset_type": "equity", "weight_pct": weight, "quantity": None}],
    }


class HoldingsIntegrationTests(unittest.TestCase):
    def test_active_registry_extends_holdings_without_mutating_sector_universe(self):
        active = {**ASSET, 'id': 'KR:0163Y0', 'symbol': '0163Y0',
                  'name': 'KoAct 코스닥액티브', 'kind': 'market_active',
                  'group': '코스닥 액티브', 'classification_status': 'reviewed_market_active'}
        registry = {'version': 'active-v1', 'assets': [active]}
        merged = build.holdings_config(CONFIG, registry)
        self.assertEqual(len(merged['assets']), 2)
        self.assertEqual(len(CONFIG['assets']), 1)
        self.assertEqual(merged['version'], 'test-v1+active-v1')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            build.holdings_config(merged, registry)
        with self.assertRaisesRegex(ValueError, 'unverified'):
            build.holdings_config(CONFIG, {**registry, 'assets': [{**active, 'classification_status': 'pending'}]})

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.history = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def save(self, day, source="SOL", **kwargs):
        snapshot = disclosure(day, source, **kwargs)
        build.save_snapshot(snapshot, history=self.history)
        return snapshot

    def export(self, statuses=None):
        return build.export(CONFIG, EXPECTED, SESSIONS, statuses or [], history=self.history,
                            aum_history=self.history / 'aum')

    def test_aum_never_substitutes_end_date_or_nearby_date_for_start(self):
        def aum(day, **kwargs):
            return {'etf_id': ASSET['id'], 'asof': day, 'status': 'verified',
                    'value_krw': 1_000_000_000_000, 'metric': 'net_assets',
                    'currency': 'KRW', 'source': 'KODEX',
                    'observed_at': '2026-10-11T13:00:00+00:00', **kwargs}
        records = {ASSET['id']: {'KODEX': {
            '2026-10-08': aum('2026-10-08'),
            '2026-10-06': aum('2026-10-06'),
            '2026-10-12': aum('2026-10-12', value_krw=999),
        }}}
        current = build.select_aum(records, ASSET['id'], EXPECTED)
        self.assertEqual(current['asof'], EXPECTED)
        self.assertEqual(current['status'], 'verified')
        start = build.select_aum(records, ASSET['id'], '2026-10-07', exact=True)
        self.assertEqual(start['status'], 'unavailable')
        self.assertIsNone(start['value_krw'])
        reference = build.select_aum(records, ASSET['id'], '2026-10-07')
        self.assertEqual(reference['asof'], '2026-10-06')
        self.assertEqual(reference['status'], 'stale')
        for bad in [dict(metric='market_cap'), dict(currency='USD'), dict(status='unavailable'),
                    dict(value_krw=-1), dict(value_krw=True), dict(value_krw=float('nan')),
                    dict(etf_id='KR:999999'), dict(asof='2026-10-07')]:
            invalid = {ASSET['id']: {'KODEX': {EXPECTED: aum(EXPECTED, **bad)}}}
            self.assertIsNone(build.select_aum(invalid, ASSET['id'], EXPECTED, exact=True)['value_krw'])
        undated = {ASSET['id']: {'NAVER': {'observed:2026-10-11': aum(None, status='undated')}}}
        self.assertEqual(build.select_aum(undated, ASSET['id'], EXPECTED)['status'], 'undated')
        self.assertIsNone(build.select_aum(undated, ASSET['id'], '2026-10-07', exact=True)['value_krw'])

    def test_start_aum_coverage_requires_ready_weights_as_well_as_exact_size(self):
        self.save(EXPECTED, weight=10.3)
        self.save('2026-10-07', weight=10.2)
        aum = {'etf_id': ASSET['id'], 'asof': '2026-10-07', 'status': 'verified',
               'value_krw': 1_000_000_000_000, 'metric': 'net_assets', 'currency': 'KRW',
               'source': 'KODEX', 'observed_at': '2026-10-11T13:00:00+00:00'}
        records = {ASSET['id']: {'KODEX': {'2026-10-07': aum}}}
        with patch('holdings_pipeline.aum.load_aum_history', return_value=records):
            result = self.export()
        fund = result['funds'][0]
        self.assertEqual(fund['aum']['status'], 'stale')
        self.assertEqual(fund['daily']['start_aum']['value_krw'], aum['value_krw'])
        self.assertEqual(fund['weekly']['start_aum']['status'], 'unavailable')
        self.assertEqual(result['coverage']['daily_amount_comparable'], 1)
        self.assertEqual(result['coverage']['weekly_amount_comparable'], 0)

    def test_future_basket_is_retained_but_never_current(self):
        self.save(EXPECTED)
        self.save("2026-10-12", weight=90)
        result = self.export()
        fund = result["funds"][0]
        self.assertEqual(fund["asof"], EXPECTED)
        self.assertEqual(fund["latest_holdings"][0]["weight_pct"], 10)
        self.assertEqual(fund["next_published_date"], "2026-10-12")
        future_only = {"SOL": {"2026-10-12": disclosure("2026-10-12")}}
        self.assertIsNone(build.select_snapshot(future_only, EXPECTED))

    def test_failed_official_cache_falls_back_to_healthy_same_date_source(self):
        for source in ("SOL", "NAVER"):
            self.save("2026-10-07", source)
            self.save(EXPECTED, source, weight=10.2)
        failure = {"etf_id": ASSET["id"], "source": "SOL", "date": EXPECTED, "status": "failed"}
        result = self.export([failure])
        fund = result["funds"][0]
        self.assertEqual(fund["source"], "NAVER")
        self.assertEqual(fund["status"], "current")
        self.assertEqual(fund["daily"]["status"], "ready")
        self.assertEqual(fund["daily"]["source"], "NAVER")
        # With both sources failing, neither preserved cache becomes ready.
        second_failure = {**failure, "source": "NAVER", "status": "not_attempted"}
        failed = self.export([failure, second_failure])
        self.assertEqual(failed["funds"][0]["status"], "cached_after_failure")
        self.assertNotEqual(failed["funds"][0]["daily"]["status"], "ready")
        self.assertEqual(failed["coverage"]["daily_comparable"], 0)
        self.assertEqual(failed["cohorts"]["daily"], [])

    def test_partial_response_cannot_overwrite_complete_dated_snapshot(self):
        original = self.save(EXPECTED, weight=10.2)
        partial = copy.deepcopy(original)
        partial.update(complete=False, reported_count=2, observed_at="2026-10-11T13:00:00+00:00")
        partial["holdings"][0]["weight_pct"] = 99
        self.assertFalse(build.save_snapshot(partial, history=self.history))
        saved = build.load_history(self.history)[ASSET["id"]]["SOL"][EXPECTED]
        self.assertTrue(saved["complete"])
        self.assertEqual(saved["holdings"][0]["weight_pct"], 10.2)

    def test_incomplete_past_disclosure_is_retried_but_complete_one_is_reused(self):
        previous, weekly = SESSIONS["KR"][-2], SESSIONS["KR"][-6]
        history = {ASSET["id"]: {"SOL": {
            previous: disclosure(previous, complete=False),
            weekly: disclosure(weekly, complete=True),
        }}}
        with patch("holdings_pipeline.kodex.extract_archives", return_value=[]), \
             patch.object(build, "load_history", return_value=history), \
             patch.object(build, "save_snapshot", return_value=True), \
             patch.object(build.time, "sleep"), \
             patch("holdings_pipeline.issuers.supports", return_value=True), \
             patch("holdings_pipeline.issuers.fetch_snapshot", side_effect=lambda asset, day, codes, **kwargs: disclosure(day)) as fetch:
            statuses = build.collect(CONFIG, EXPECTED, SESSIONS, set(), naver_enabled=False)
        self.assertEqual([call.args[1] for call in fetch.call_args_list], [EXPECTED, previous])
        previous_status = next(x for x in statuses if x.get("date") == previous)
        weekly_status = next(x for x in statuses if x.get("date") == weekly)
        self.assertEqual(previous_status["status"], "success")
        self.assertEqual(weekly_status["status"], "stored_history")

    def test_daily_and_weekly_use_exact_session_dates_without_nearest_substitution(self):
        self.save(EXPECTED, weight=10.3)
        self.save(SESSIONS["KR"][-2], weight=10.2)
        self.save(SESSIONS["KR"][-6], weight=10.0)
        result = self.export()
        daily, weekly = result["funds"][0]["daily"], result["funds"][0]["weekly"]
        self.assertEqual((daily["from_date"], daily["to_date"]), ("2026-10-07", EXPECTED))
        self.assertEqual((weekly["from_date"], weekly["to_date"]), ("2026-09-30", EXPECTED))
        self.assertEqual(daily["changes"][0]["delta_weight_pp"], 0.1)
        self.assertEqual(weekly["changes"][0]["delta_weight_pp"], 0.3)
        (self.history / "SOL" / ASSET["symbol"] / "2026-09-30.json").unlink()
        self.save("2026-10-01", weight=9.0)
        lacking_exact_date = self.export()
        self.assertEqual(lacking_exact_date["funds"][0]["weekly"]["status"], "waiting")
        self.assertEqual(lacking_exact_date["cohorts"]["weekly"], [])
        self.assertEqual(lacking_exact_date["coverage"]["daily_comparable"], 1)


if __name__ == "__main__":
    unittest.main()
