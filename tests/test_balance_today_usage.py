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
        if path == '/api/user/self':
            return {'data': {'balance': 100, 'quota': 100}}
        if path == '/v1/usage':
            cost = 1.25 if headers['Authorization'] == 'Bearer sk-one' else 2.5
            return {'balance': 100, 'usage': {'today': {'actual_cost': cost}}}
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
            if site_type == 'sub2':
                self.assertEqual(fetch.call_count, 2)
                fetch.assert_any_call('https://example.com/v1/usage',
                                      headers={'Authorization': 'Bearer sk-one'}, timeout=10)

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

    def test_missing_or_failed_usage_does_not_turn_into_zero_or_fail_balance(self):
        def fetch(url, **kwargs):
            if kwargs['headers']['Authorization'] == 'Bearer sk-one':
                return {'balance': 100}
            raise requests.Timeout()
        with patch.object(balance, 'fetch_json', side_effect=fetch):
            result = balance.get_balance(self.site)
        self.assertEqual(result['balance'], 200)
        self.assertIsNone(result['todayUsage']['cost'])
        self.assertEqual(result['todayUsage']['failedCount'], 2)
        self.assertTrue(result['todayUsageError'])

    def test_unconfigured_keys_never_fall_back_to_account_login(self):
        with patch.object(balance, 'fetch_json') as fetch:
            with self.assertRaisesRegex(ValueError, 'API Key'):
                balance.get_balance({**self.site, 'usageKeys': []})
        fetch.assert_not_called()

    def test_legacy_single_key_zero_usage_and_site_timezone(self):
        site = {**self.site, 'timezone': 'UTC', 'usageToken': 'sk-one'}
        del site['usageKeys']
        with patch.object(balance, 'fetch_json', return_value={
            'data': {'balance': 0, 'usage': {'today': {'actual_cost': 0}}},
        }):
            result = balance.get_balance(site)
        self.assertEqual(result['balance'], 0)
        self.assertEqual(result['currency'], 'USD')
        self.assertEqual(result['todayUsage'], {
            'date': '2026-11-04', 'source': 'keys', 'cost': 0,
            'successCount': 1, 'failedCount': 0,
        })

    def test_report_historical_keys_stay_separate_from_today_keys(self):
        def fetch(url, **kwargs):
            if urlsplit(url).query:
                return {'usage': {'today': {'actual_cost': 999}}, 'daily_usage': [
                    {'date': '2026-11-04', 'actual_cost': 6},
                    {'date': '2026-11-05', 'actual_cost': 999},
                ]}
            return self.fetch(url, **kwargs)
        with patch.object(balance, 'fetch_json', side_effect=fetch), patch.object(balance, 'get_token_usage') as keys:
            result = balance.get_balance(self.site, usage_day='2026-11-04')
        keys.assert_not_called()
        self.assertEqual(result['usage']['cost'], 12)
        self.assertEqual(result['usage']['source'], 'keys')
        self.assertIsNone(result['todayUsage'])

    def test_legacy_auto_site_uses_key_and_reports_missing_wallet_balance(self):
        site = {**self.site, 'siteType': 'auto', 'mePath': '/api/v1/auth/me',
                'authMode': 'password', 'balanceField': 'quota'}
        with patch.object(balance, 'fetch_json', return_value={'quota': 100}) as fetch:
            with self.assertRaisesRegex(ValueError, '账户余额 balance'):
                balance.get_balance(site)
        fetch.assert_called_once_with('https://example.com/v1/usage',
                                      headers={'Authorization': 'Bearer sk-one'}, timeout=10)
