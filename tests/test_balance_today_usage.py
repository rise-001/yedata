import unittest
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import requests

from monitor import balance


class BalanceTodayUsageTests(unittest.TestCase):
    def setUp(self):
        self.site = {
            'id': 'alpha', 'baseUrl': 'https://example.com', 'siteType': 'sub2',
            'authMode': 'token', 'token': 'console-token', 'timezone': 'Asia/Shanghai',
            'balanceMultiplier': 20, 'balanceDivisor': 10,
            'usageKeys': [{'id': 'one', 'name': 'One', 'token': 'sk-one'},
                          {'id': 'two', 'name': 'Two', 'token': 'sk-two'}],
        }
        self.now = datetime(2026, 11, 4, 16, 5, tzinfo=timezone.utc)
        clock = patch.object(balance, 'datetime')
        clock.start().now.return_value = self.now
        self.addCleanup(clock.stop)

    def fetch(self, url, *, headers, timeout=None):
        path = urlsplit(url).path
        if path in ('/api/v1/auth/me', '/api/user/self'):
            return {'data': {'balance': 100, 'quota': 100}}
        if path == '/v1/usage':
            cost = 1.25 if headers['Authorization'] == 'Bearer sk-one' else 2.5
            return {'usage': {'today': {'actual_cost': cost}}}
        if path == '/api/usage/token':
            return {'data': {'name': headers['Authorization'][7:], 'total_used': 50000000}}
        if path == '/api/log/self/stat':
            query = parse_qs(urlsplit(url).query)
            self.assertIn('token_name', query, 'Must never query account-wide consumption')
            quota = 625000 if query['token_name'] == ['sk-one'] else 1250000
            return {'success': True, 'data': {'quota': quota}}
        self.fail(f'Unexpected consumption endpoint: {path}')

    def test_both_adapters_sum_only_configured_keys_without_balance_scaling(self):
        for site_type in ('sub2', 'newapi'):
            with self.subTest(site_type=site_type), patch.object(balance, 'fetch_json', side_effect=self.fetch) as fetch:
                result = balance.get_balance({**self.site, 'siteType': site_type, 'newApiUserId': '7'})
            self.assertEqual(result['todayUsage'], {
                'date': '2026-11-05', 'source': 'keys', 'cost': 3.75,
                'successCount': 2, 'failedCount': 0,
            })
            self.assertIsNone(result['usage'])
            self.assertEqual(result['balance'], 200)
            self.assertNotIn('/api/v1/usage/dashboard/trend', [urlsplit(call.args[0]).path for call in fetch.call_args_list])

    def test_partial_failure_keeps_successful_key_subtotal(self):
        def fetch(url, **kwargs):
            if kwargs['headers'].get('Authorization') == 'Bearer sk-two':
                raise requests.Timeout()
            return self.fetch(url, **kwargs)
        with patch.object(balance, 'fetch_json', side_effect=fetch):
            result = balance.get_balance(self.site)
        self.assertEqual(result['todayUsage']['cost'], 1.25)
        self.assertEqual(result['todayUsage']['successCount'], 1)
        self.assertEqual(result['todayUsage']['failedCount'], 1)

    def test_all_key_failures_do_not_turn_into_zero_or_fail_balance(self):
        def fetch(url, **kwargs):
            if urlsplit(url).path == '/v1/usage':
                raise requests.Timeout()
            return self.fetch(url, **kwargs)
        with patch.object(balance, 'fetch_json', side_effect=fetch):
            result = balance.get_balance(self.site)
        self.assertEqual(result['balance'], 200)
        self.assertIsNone(result['todayUsage']['cost'])
        self.assertEqual(result['todayUsage']['failedCount'], 2)
        self.assertTrue(result['todayUsageError'])

    def test_unconfigured_keys_never_fall_back_to_account_consumption(self):
        with patch.object(balance, 'fetch_json', side_effect=self.fetch) as fetch:
            result = balance.get_balance({**self.site, 'usageKeys': []})
        fetch.assert_called_once()
        self.assertIsNone(result['todayUsage'])
        self.assertIn('API Key', result['todayUsageError'])

    def test_legacy_single_key_zero_usage_and_site_timezone(self):
        site = {**self.site, 'timezone': 'UTC', 'usageToken': 'sk-one'}
        del site['usageKeys']
        with patch.object(balance, 'fetch_json', side_effect=[
            {'data': {'balance': 100}}, {'usage': {'today': {'actual_cost': 0}}},
        ]):
            result = balance.get_balance(site)
        self.assertEqual(result['todayUsage'], {
            'date': '2026-11-04', 'source': 'keys', 'cost': 0,
            'successCount': 1, 'failedCount': 0,
        })

    def test_report_historical_usage_stays_separate_from_today_keys(self):
        with patch.object(balance, 'fetch_json', side_effect=[
            {'data': {'balance': 100}},
            {'data': {'trend': [{'date': '2026-11-04', 'actual_cost': 12}]}},
        ]), patch.object(balance, 'get_token_usage') as keys:
            result = balance.get_balance(self.site, usage_day='2026-11-04')
        keys.assert_not_called()
        self.assertEqual(result['usage']['cost'], 12)
        self.assertIsNone(result['todayUsage'])
