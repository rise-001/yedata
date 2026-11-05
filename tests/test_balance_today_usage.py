import unittest
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from monitor import balance


class BalanceTodayUsageTests(unittest.TestCase):
    def setUp(self):
        self.site = {
            'id': 'alpha', 'baseUrl': 'https://example.com', 'siteType': 'sub2',
            'authMode': 'token', 'token': 'console-token', 'timezone': 'Asia/Shanghai',
        }
        self.now = datetime(2026, 11, 4, 16, 5, tzinfo=timezone.utc)
        clock = patch.object(balance, 'datetime')
        self.clock = clock.start()
        self.clock.now.return_value = self.now
        self.addCleanup(clock.stop)

    def test_sub2_fetches_today_account_usage_alongside_balance(self):
        with patch.object(balance, 'fetch_json', side_effect=[
            {'data': {'balance': 100}},
            {'data': {'trend': [
                {'date': '2026-11-04', 'actual_cost': 99},
                {'date': '2026-11-05', 'actual_cost': '1.25'},
                {'date': '2026-11-05T01:00:00', 'actual_cost': 2},
            ]}},
        ]) as fetch:
            result = balance.get_balance(self.site)
        self.assertEqual(result['balance'], 100)
        self.assertEqual(result['todayUsage'], {'date': '2026-11-05', 'cost': 3.25, 'currency': 'CNY'})
        query = parse_qs(urlsplit(fetch.call_args.args[0]).query)
        self.assertEqual(query['start_date'], ['2026-11-05'])
        self.assertEqual(query['end_date'], ['2026-11-05'])

    def test_newapi_uses_account_stat_without_key_filter_and_converts_quota(self):
        site = {**self.site, 'siteType': 'newapi', 'newApiUserId': '7', 'balanceMultiplier': 2}
        with patch.object(balance, 'fetch_json', side_effect=[
            {'data': {'quota': 5000000}}, {'success': True, 'data': {'quota': 1250000}},
        ]) as fetch:
            result = balance.get_balance(site)
        self.assertEqual(result['balance'], 20)
        self.assertEqual(result['todayUsage']['cost'], 5)
        request = fetch.call_args
        self.assertEqual(urlsplit(request.args[0]).path, '/api/log/self/stat')
        query = parse_qs(urlsplit(request.args[0]).query)
        self.assertNotIn('token_name', query)
        self.assertEqual(query['start_timestamp'], [str(int(self.now.timestamp()) - 300)])
        self.assertEqual(query['end_timestamp'], [str(int(self.now.timestamp()))])
        self.assertEqual(request.kwargs['headers'], {'Authorization': 'Bearer console-token', 'New-Api-User': '7'})

    def test_zero_usage_is_known_for_both_adapters(self):
        for site_type, usage in (
            ('sub2', {'data': {'trend': []}}),
            ('newapi', {'success': True, 'data': {'quota': 0}}),
        ):
            with self.subTest(site_type=site_type), patch.object(balance, 'fetch_json', side_effect=[
                {'data': {'balance': 100}}, usage,
            ]):
                self.assertEqual(balance.get_balance({**self.site, 'siteType': site_type})['todayUsage']['cost'], 0)

    def test_failed_or_malformed_usage_preserves_successful_balance(self):
        for site_type, usage in (
            ('sub2', ValueError('unavailable')), ('sub2', {'data': {}}),
            ('sub2', {'data': {'trend': [{'date': '2026-11-05', 'actual_cost': 'NaN'}]}}),
            ('newapi', {'success': True, 'data': {}}),
            ('newapi', {'success': True, 'data': {'quota': True}}),
            ('newapi', {'success': False, 'data': {'quota': 100}}),
        ):
            with self.subTest(site_type=site_type, usage=usage), patch.object(balance, 'fetch_json', side_effect=[
                {'data': {'balance': 100, 'quota': 50000000}}, usage,
            ]):
                result = balance.get_balance({**self.site, 'siteType': site_type})
                self.assertEqual(result['balance'], 100)
                self.assertIsNone(result['todayUsage'])
                self.assertTrue(result['todayUsageError'])

    def test_report_requested_day_is_not_shown_as_today(self):
        with patch.object(balance, 'fetch_json', side_effect=[
            {'data': {'balance': 100}},
            {'data': {'trend': [{'date': '2026-11-04', 'actual_cost': 12}]}},
        ]):
            result = balance.get_balance(self.site, usage_day='2026-11-04')
        self.assertEqual(result['usage']['cost'], 12)
        self.assertIsNone(result['todayUsage'])

    def test_uses_configured_timezone_and_cookie_auth(self):
        site = {**self.site, 'siteType': 'newapi', 'timezone': 'UTC',
                'authMode': 'cookie', 'token': '', 'sessionCookie': 'session=example'}
        with patch.object(balance, 'fetch_json', side_effect=[
            {'data': {'quota': 500000}}, {'success': True, 'data': {'quota': 100}},
        ]) as fetch:
            result = balance.get_balance(site)
        self.assertEqual(result['todayUsage']['date'], '2026-11-04')
        self.assertEqual(fetch.call_args.kwargs['headers'], {'Cookie': 'session=example'})
        query = parse_qs(urlsplit(fetch.call_args.args[0]).query)
        self.assertEqual(query['start_timestamp'], [str(int(self.now.replace(hour=0, minute=0).timestamp()))])
