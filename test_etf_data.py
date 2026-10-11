"""Data correctness gates for the independent ETF screen."""
from datetime import date, datetime, timedelta, timezone
import json
import math
from pathlib import Path
import tempfile
import unittest

import build_etf_data as etf
from etf_pipeline import monitor


def sample(prices, asset_id='KR:A'):
    dates = [(date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(len(prices))]
    rows = [dict(date=d, open=p, high=p, low=p, close=p, volume=1) for d, p in zip(dates, prices)]
    return {'id': asset_id, 'source': 'NAVER', 'basis': 'provider_price_adjustment_unverified',
            'asof': dates[-1], 'rows': rows}, dates


def oscillating(n=300):
    values = [100.]
    for i in range(n - 1):
        values.append(values[-1] * (1.012 if i % 2 == 0 else .995))
    return values


class MetricTests(unittest.TestCase):
    def test_sortino_includes_up_days_as_zero_downside(self):
        prices = [100., 110., 104.5]
        expected = math.sqrt(252) * .025 / math.sqrt(.05 ** 2 / 2)
        self.assertAlmostEqual(monitor.sortino(prices, 2), expected)

    def test_zero_downside_is_na(self):
        self.assertIsNone(monitor.sortino([1., 2., 3.], 2))

    def test_benchmark_failure_does_not_remove_price_metrics(self):
        s, ds = sample(oscillating())
        result = etf.metrics(s, None, ds)
        self.assertIsNotNone(result['sortino_price_126'])
        self.assertIsNotNone(result['high']['gap'])
        self.assertIsNone(result['rs_price_126'])

    def test_rs_requires_identical_basis(self):
        s, ds = sample(oscillating())
        b, _ = sample(oscillating(), 'KR:KOSPI')
        b['basis'] = 'total_return'
        result = etf.metrics(s, b, ds)
        self.assertIsNone(result['rs_price_126'])
        self.assertIsNotNone(result['sortino_price_126'])

    def test_rs_is_return_difference_not_relative_ratio(self):
        s, ds = sample([100.] * 126 + [120.])
        b, _ = sample([100.] * 126 + [110.], 'KR:KOSPI')
        self.assertAlmostEqual(etf.metrics(s, b, ds)['rs_price_126'], .10)

    def test_missing_calendar_session_never_compresses_window(self):
        s, ds = sample(oscillating())
        s['rows'].pop(-30)
        result = etf.metrics(s, None, ds)
        self.assertIsNone(result['sortino_price_126'])
        self.assertEqual(result['high']['state'], 'insufficient_253_closes')
        self.assertIsNotNone(result['above_ma20'])

    def test_missing_benchmark_day_only_suppresses_rs(self):
        s, ds = sample(oscillating())
        b, _ = sample(oscillating(), 'KR:KOSPI')
        b['rows'].pop(-10)
        result = etf.metrics(s, b, ds)
        self.assertIsNone(result['rs_price_21'])
        self.assertIsNotNone(result['sortino_price_126'])

    def test_discontinuity_blocks_affected_not_shorter_window(self):
        prices = oscillating()
        prices[:200] = [x * 2 for x in prices[:200]]
        s, ds = sample(prices)
        result = etf.metrics(s, None, ds)
        self.assertIsNone(result['sortino_price_126'])
        self.assertIsNotNone(result['above_ma50'])

    def test_high_distance_reference_and_peak_date_are_consistent(self):
        s, ds = sample([100.] * 252 + [101.])
        result = etf.high_state(s, ds)
        self.assertEqual(result['state'], 'new_close_high_candidate')
        self.assertEqual(result['reference_high'], 101.)
        self.assertEqual(result['gap'], 0)
        self.assertEqual(result['peak_date'], ds[-1])
        self.assertAlmostEqual(result['breakout_pct'], .01)
        self.assertFalse(result['weak_window_complete'])

    def test_recent_breakout_then_weakening_has_event_reference(self):
        s, ds = sample([100.] * 253 + [102., 100., 98., 97.])
        result = etf.high_state(s, ds)
        self.assertEqual(result['state'], 'weakened_after_high_candidate')
        self.assertEqual(result['peak_date'], ds[-4])
        self.assertAlmostEqual(result['gap'], 97 / 102 - 1)
        self.assertTrue(result['weak_window_complete'])

    def test_no_weakening_label_without_verified_recent_event(self):
        s, ds = sample([110.] + [100.] * 256)
        self.assertNotEqual(etf.high_state(s, ds)['state'], 'weakened_after_high_candidate')

    def test_history_close_is_not_today_close(self):
        s, ds = sample(oscillating())
        self.assertEqual(etf.metrics(s, None, ds[:-20])['close'], s['rows'][-21]['close'])

    def test_verified_complete_listing_history_is_separate_from_52_week(self):
        s, ds = sample(oscillating(100))
        asset = {'listing_date': ds[0], 'listing_verified': True,
                 'listing_source_url': 'https://issuer.example/first-trading-date'}
        result = etf.high_state(s, ds, asset)
        self.assertEqual(result['basis'], 'since_listing')
        self.assertEqual(result['state'], 'new_listing_high_candidate')
        self.assertEqual(result['observations'], 100)
        self.assertEqual(result['reference_high'], s['rows'][-1]['close'])
        self.assertFalse(result['weak_window_complete'])

    def test_partial_provider_history_never_implies_listing_date(self):
        s, ds = sample(oscillating(100))
        for asset in ({}, {'listing_date': ds[0]},
                      {'listing_date': ds[0], 'listing_verified': True},
                      {'inception_date': ds[0], 'inception_verified': True,
                       'inception_source_url': 'https://issuer.example/fund-inception'}):
            with self.subTest(asset=asset):
                self.assertEqual(etf.high_state(s, ds, asset)['state'], 'insufficient_253_closes')

    def test_missing_day_blocks_even_verified_listing_high(self):
        s, ds = sample(oscillating(100))
        asset = {'listing_date': ds[0], 'listing_verified': True,
                 'listing_source_url': 'https://issuer.example/first-trading-date'}
        s['rows'].pop(50)
        self.assertEqual(etf.high_state(s, ds, asset)['state'], 'insufficient_253_closes')

    def test_old_listing_with_truncated_history_is_not_since_listing(self):
        s, ds = sample(oscillating())
        asset = {'listing_date': ds[0], 'listing_verified': True,
                 'listing_source_url': 'https://issuer.example/first-trading-date'}
        s['rows'] = s['rows'][-100:]
        self.assertEqual(etf.high_state(s, ds, asset)['state'], 'insufficient_253_closes')


class StatusTests(unittest.TestCase):
    def test_original_preserves_rank_only_observation(self):
        config = etf.read_json(etf.DATA / 'universe.json')
        result = etf.original_snapshot(config, etf.DATA)
        medical = next(r for r in result['rows'] if r['id'] == 'KR:307510')
        self.assertEqual(medical['sortino20_rank'], 11)
        self.assertIsNone(medical['rs_6m'])
        self.assertTrue(medical['representative'])

    def test_failed_status_does_not_promote_current_dated_cache(self):
        s, ds = sample(oscillating())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            monitor.dump(root / 'data/series/KR_A.json', s)
            result = etf.current_series({'id': 'KR:A'}, ds[-1], {'KR:A': {'status': 'failed'}}, {}, {}, root)
            self.assertIsNone(result[0])
            self.assertEqual(result[1], 'failed')
            self.assertEqual(result[3], ds[-1])

    def test_stale_and_quarantine(self):
        s, ds = sample(oscillating())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            monitor.dump(root / 'data/series/KR_A.json', s)
            status = {'KR:A': {'status': 'fresh', 'asof': ds[-1]}}
            self.assertEqual(etf.current_series({'id': 'KR:A'}, '2027-01-01', status, {}, {}, root)[1], 'stale')
            quotes = {'KR:A': {'comparison': {'status': 'mismatch'}}}
            self.assertEqual(etf.current_series({'id': 'KR:A'}, ds[-1], status, quotes, {}, root)[1], 'quarantined')

    def test_known_split_applied_once(self):
        s, ds = sample([100.] * 252 + [50.], 'US:XLK')
        events = [{'id': 'US:XLK', 'effective_date': ds[-1], 'new_shares_per_old_share': 2}]
        a = monitor.apply_verified_splits(s, events)
        b = monitor.apply_verified_splits(a, events)
        self.assertEqual(a['rows'][0]['close'], 50.)
        self.assertEqual(a['rows'], b['rows'])

    def test_announced_future_split_does_not_adjust_current_prices(self):
        s, ds = sample(oscillating(), 'US:SOXX')
        future_date = (date.fromisoformat(ds[-1]) + timedelta(days=20)).isoformat()
        events = [{'id': 'US:SOXX', 'effective_date': future_date, 'new_shares_per_old_share': 3}]
        result = monitor.apply_verified_splits(s, events)
        self.assertEqual(result['rows'], s['rows'])
        self.assertEqual(result['known_splits_applied'], [])


class RankTests(unittest.TestCase):
    def test_current_not_removed_by_missing_twenty_day_history(self):
        rows = [dict(id='KR:A', market='KR', scope='KR', kind='industry', sortino_price_126=3,
                     above_ma10=True, above_ma20=True, above_ma50=True, rs_price_126=.10),
                dict(id='KR:B', market='KR', scope='KR', kind='industry', sortino_price_126=2,
                     above_ma10=True, above_ma20=True, above_ma50=True, rs_price_126=.05)]
        prior = {'KR:A': rows[0] | {'sortino_price_126': 1}, 'KR:B': rows[1]}
        history = {'KR': {1: prior, 5: prior, 20: {'KR:A': prior['KR:A']}}}
        ds = sample(oscillating())[1]
        etf.assign_ranks(rows, history, {'KR': ds})
        rank = rows[0]['ranks']['sortino20']
        self.assertEqual(rank['rank'], 1)
        self.assertEqual(rank['delta_1'], 1)
        self.assertIsNone(rank['delta_20'])
        self.assertEqual(rank['comparisons']['20']['reason'], 'historical_valid_cohort_differs')

    def test_entry_means_ma_condition_entry_not_top_ten_entry(self):
        row = dict(id='KR:A', market='KR', scope='KR', kind='industry', sortino_price_126=3,
                   above_ma10=True, above_ma20=True, above_ma50=True, rs_price_126=.10)
        prior = row | {'above_ma20': False}
        etf.assign_ranks([row], {'KR': {1: {'KR:A': prior}}}, {'KR': sample(oscillating())[1]})
        self.assertEqual(row['ranks']['sortino20']['delta_1'], 'entry')


class ExpandedUniverseTests(unittest.TestCase):
    def asset(self, symbol, *, ranking=True, representative=True, classification='issuer_reviewed'):
        return dict(id='KR:' + symbol, symbol=symbol, name=symbol, market='KR', scope='KR',
                    kind='industry', group=symbol, representative=representative,
                    rank_enabled=ranking, classification_status=classification)

    def test_classification_counts_do_not_confuse_exclusion_with_unreviewed(self):
        assets = [self.asset('A'), self.asset('B', representative=False),
                  self.asset('C', ranking=False, representative=False),
                  self.asset('D', ranking=False, representative=False, classification='pending_issuer_review')]
        result = etf.universe_counts(assets)
        self.assertEqual(result['registered'], 4)
        self.assertEqual(result['classified'], 3)
        self.assertEqual(result['rank_enabled'], 2)
        self.assertEqual(result['representatives'], 1)
        self.assertEqual(result['supplemental'], 1)
        self.assertEqual(result['excluded'], 1)
        self.assertEqual(result['unclassified'], 1)

    def test_all_registered_visible_but_only_eligible_ranked(self):
        eligible = self.asset('A')
        excluded = self.asset('B', ranking=False, representative=False)
        excluded['ranking_exclusion_reason'] = 'country_exposure'
        pending = self.asset('C', ranking=False, representative=False, classification='pending_issuer_review')
        config = {'version': '2026-10-11-v3', 'changed_at': '2026-10-11',
                  'change_reason': 'expanded_coverage', 'assets': [eligible, excluded, pending],
                  'benchmarks': {'KR': {'id': 'KR:KOSPI', 'symbol': 'KOSPI'},
                                 'US': {'id': 'US:SPY', 'symbol': 'SPY'}}}
        s, ds = sample(oscillating())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            monitor.dump(root / 'data/series/KR_A.json', s)
            monitor.dump(root / 'data/series/KR_B.json', s | {'id': 'KR:B'})
            monitor.dump(root / 'output/status.json', {'status': [
                {'id': 'KR:A', 'status': 'fresh', 'asof': ds[-1]},
                {'id': 'KR:B', 'status': 'fresh', 'asof': ds[-1]},
                {'id': 'KR:C', 'status': 'failed', 'error': 'provider_unavailable'}]})
            result = etf.export(config, {'KR': ds[-1], 'US': ds[-1]},
                                {'KR': ds, 'US': ds}, {}, saved=True, data_dir=root)
        self.assertEqual(result['schema_version'], 2)
        self.assertEqual(len(result['all_rows']), 3)
        self.assertEqual(len(result['rows']), 1)
        self.assertEqual(len(result['watchlist_rows']), 2)
        self.assertEqual(result['rows'][0]['ranks']['sortino20']['rank'], 1)
        self.assertEqual(result['all_rows'][1]['ranks'], {})
        self.assertEqual(result['all_rows'][1]['exclusion_reason'], 'country_exposure')
        self.assertIsNotNone(result['all_rows'][1]['sortino_price_126'])
        self.assertEqual(result['all_rows'][2]['status'], 'failed')
        self.assertIsNone(result['all_rows'][2]['sortino_price_126'])
        self.assertEqual(result['markets']['KR']['registered'], 3)
        self.assertEqual(result['markets']['KR']['fresh'], 2)
        self.assertEqual(result['markets']['KR']['usable'], 1)
        self.assertEqual(result['markets']['KR']['excluded'], 1)
        self.assertEqual(result['markets']['KR']['unclassified'], 1)
        self.assertEqual(result['universe']['version'], '2026-10-11-v3')
        self.assertEqual(result['universe']['changed_at'], '2026-10-11')

    def test_discovery_catalog_is_separate_from_registered_price_universe(self):
        config = {'assets': [self.asset('A')]}
        catalog = {'listed_count': 3, 'candidate_count': 2,
                   'observed_at': '2026-10-11T11:21:12+00:00', 'source_asof': None,
                   'collection_mode': 'live', 'source_url': 'https://provider.example/catalog',
                   'review_counts': {'registered_reviewed': 1, 'candidate_pending_review': 2},
                   'rows': [{'symbol': 'A'}, {'symbol': 'B'}, {'symbol': 'C'}]}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            monitor.dump(root / 'kr_catalog.json', catalog)
            result = etf.catalog_summary(config, root)['KR']
        self.assertEqual(result['status'], 'dated_observation')
        self.assertEqual(result['listed_count'], 3)
        self.assertEqual(result['candidate_count'], 2)
        self.assertEqual(result['registered_in_catalog'], 1)
        self.assertIsNone(result['source_asof'])
        self.assertNotIn('rows', result)

    def test_invalid_catalog_does_not_claim_complete_coverage(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            monitor.dump(root / 'kr_catalog.json', {'listed_count': 100, 'rows': [{'symbol': 'A'}]})
            result = etf.catalog_summary({'assets': []}, root)['KR']
        self.assertEqual(result['status'], 'invalid')
        self.assertNotIn('listed_count', result)


class CalendarTests(unittest.TestCase):
    def test_complete_sessions_and_verified_2026_holidays(self):
        ex, ds, _ = etf.calendar_sessions(datetime(2026, 10, 11, 10, tzinfo=timezone.utc))
        self.assertEqual(ex, {'KR': '2026-10-08', 'US': '2026-10-09'})
        self.assertNotIn('2026-06-03', ds['KR'])
        self.assertNotIn('2026-07-17', ds['KR'])

    def test_live_intraday_is_not_complete(self):
        ex, _, _ = etf.calendar_sessions(datetime(2026, 10, 8, 5, tzinfo=timezone.utc))
        self.assertEqual(ex['KR'], '2026-10-07')
        self.assertEqual(ex['US'], '2026-10-07')

    def test_holiday_override_rejected(self):
        with self.assertRaises(ValueError):
            etf.calendar_sessions(datetime(2026, 10, 11, 10, tzinfo=timezone.utc), {'KR': '2026-10-09'})


if __name__ == '__main__':
    unittest.main()
