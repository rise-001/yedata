import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import requests

from monitor import balance, state


class DailyKeyUsageTests(unittest.TestCase):
    def setUp(self):
        self.site = {
            'id': 'alpha', 'name': 'Alpha', 'baseUrl': 'https://example.com',
            'siteType': 'sub2', 'authMode': 'token', 'token': 'console-secret',
            'timezone': 'Asia/Shanghai', 'newApiUserId': '7',
            'balanceDivisor': 10, 'balanceMultiplier': 20, 'usagePath': '',
            'usageKeys': [{'id': 'one', 'name': 'One', 'token': 'sk-one'},
                          {'id': 'two', 'name': 'Two', 'token': 'sk-two'}],
        }
        self.now = datetime(2026, 11, 5, 0, tzinfo=timezone.utc)
        clock = patch.object(balance, 'datetime')
        clock.start().now.return_value = self.now
        self.addCleanup(clock.stop)

    def fetch(self, url, *, headers, timeout=None):
        path = urlsplit(url).path
        query = parse_qs(urlsplit(url).query)
        if path in ('/api/v1/auth/me', '/api/user/self'):
            return {'data': {'balance': 100, 'quota': 100, 'used_quota': 99999999}}
        if path == '/v1/usage':
            self.assertEqual(query, {'days': ['2'], 'timezone': ['Asia/Shanghai']})
            cost = 1.25 if headers['Authorization'] == 'Bearer sk-one' else 2.5
            return {'usage': {'today': {'actual_cost': 999}, 'total': {'actual_cost': 99999}},
                    'daily_usage': [{'date': '2026-11-04', 'actual_cost': cost},
                                    {'date': '2026-11-05', 'actual_cost': 999}]}
        if path == '/api/usage/token':
            return {'data': {'name': headers['Authorization'][7:], 'total_used': 99999999}}
        if path == '/api/log/self/stat':
            self.assertEqual(headers, {'Authorization': 'Bearer console-secret', 'New-Api-User': '7'})
            self.assertEqual(query['start_timestamp'], [str(int(datetime(2026, 11, 3, 16, tzinfo=timezone.utc).timestamp()))])
            self.assertEqual(query['end_timestamp'], [str(int(datetime(2026, 11, 4, 15, 59, 59, tzinfo=timezone.utc).timestamp()))])
            quota = 625000 if query['token_name'] == ['sk-one'] else 1250000
            return {'success': True, 'data': {'quota': quota}}
        self.fail(f'Unexpected endpoint: {path}')

    def test_both_adapters_fetch_yesterday_keys_without_scaling_or_account_queries(self):
        for site_type in ('sub2', 'newapi'):
            with self.subTest(site_type=site_type), patch.object(balance, 'fetch_json', side_effect=self.fetch):
                result = balance.get_balance({**self.site, 'siteType': site_type}, usage_day='2026-11-04')
            self.assertEqual(result['usage'], {
                'date': '2026-11-04', 'source': 'keys', 'cost': 3.75,
                'successCount': 2, 'failedCount': 0, 'error': '',
            })
            self.assertEqual(result['balance'], 200)
            self.assertIsNone(result['todayUsage'])

    def test_partial_and_all_failures_keep_balance_and_never_report_unknown_as_zero(self):
        for fail_all in (False, True):
            def fetch(url, **kwargs):
                if '/v1/usage' in url and (fail_all or kwargs['headers']['Authorization'] == 'Bearer sk-two'):
                    raise requests.Timeout()
                return self.fetch(url, **kwargs)
            with self.subTest(fail_all=fail_all), patch.object(balance, 'fetch_json', side_effect=fetch):
                result = balance.get_balance(self.site, usage_day='2026-11-04')
            self.assertEqual(result['balance'], 200)
            self.assertEqual(result['usage']['cost'], None if fail_all else 1.25)
            self.assertEqual(result['usage']['failedCount'], 2 if fail_all else 1)
            self.assertIn('超时', result['usageError'])

    def test_no_keys_makes_no_account_consumption_request(self):
        with patch.object(balance, 'fetch_json', side_effect=self.fetch) as fetch:
            result = balance.get_balance({**self.site, 'usageKeys': []}, usage_day='2026-11-04')
        fetch.assert_called_once()
        self.assertIsNone(result['usage'])
        self.assertIn('API Key', result['usageError'])

    def test_sub2_requires_daily_usage_and_valid_historical_cost(self):
        responses = [
            {'usage': {'today': {'actual_cost': 999}, 'total': {'actual_cost': 99999}}},
            {'daily_usage': None}, {'daily_usage': {}},
            {'daily_usage': [{'date': 'invalid', 'actual_cost': 9}]},
        ]
        responses += [
            {'daily_usage': [{'date': '2026-11-04', 'actual_cost': value}]}
            for value in (None, True, 'nan', 'inf', 'invalid')
        ]
        for response in responses:
            with self.subTest(response=response), patch.object(balance, 'fetch_json', return_value=response):
                usage = balance.get_key_usage_for_day(self.site, '2026-11-04')
            self.assertIsNone(usage['cost'])
            self.assertEqual(usage['failedCount'], 2)

    def test_sub2_empty_days_and_explicit_zero_are_successful(self):
        for points in ([], [{'date': '2026-11-04', 'actual_cost': 0}],
                       [{'date': '2026-11-05', 'actual_cost': 999}]):
            with self.subTest(points=points), patch.object(balance, 'fetch_json', return_value={'data': {'daily_usage': points}}):
                usage = balance.get_key_usage_for_day(self.site, '2026-11-04')
            self.assertEqual(usage['cost'], 0)
            self.assertEqual(usage['successCount'], 2)
            self.assertEqual(usage['failedCount'], 0)

    def test_newapi_duplicate_names_never_double_count(self):
        def fetch(url, **kwargs):
            if '/api/usage/token' in url:
                return {'data': {'name': 'same-name', 'total_used': 99999}}
            return {'success': True, 'data': {'quota': 500000}}
        with patch.object(balance, 'fetch_json', side_effect=fetch):
            usage = balance.get_key_usage_for_day({**self.site, 'siteType': 'newapi'}, '2026-11-04')
        self.assertIsNone(usage['cost'])
        self.assertEqual(usage['failedCount'], 2)
        self.assertIn('相同名称', usage['error'])

    def test_newapi_uses_local_day_boundaries_including_daylight_saving(self):
        def fetch(url, **kwargs):
            if '/api/usage/token' in url:
                return {'data': {'name': kwargs['headers']['Authorization'][7:]}}
            query = parse_qs(urlsplit(url).query)
            self.assertEqual(query['start_timestamp'], [str(int(datetime(2026, 11, 1, 4, tzinfo=timezone.utc).timestamp()))])
            self.assertEqual(query['end_timestamp'], [str(int(datetime(2026, 11, 2, 4, 59, 59, tzinfo=timezone.utc).timestamp()))])
            return {'success': True, 'data': {'quota': 0}}
        with patch.object(balance, 'fetch_json', side_effect=fetch):
            usage = balance.get_key_usage_for_day({**self.site, 'siteType': 'newapi', 'timezone': 'America/New_York'}, '2026-11-01')
        self.assertEqual(usage['cost'], 0)
        self.assertEqual(usage['successCount'], 2)

    def test_legacy_key_and_errors_do_not_expose_secrets(self):
        site = {**self.site, 'usageToken': 'sk-one'}
        del site['usageKeys']
        with patch.object(balance, 'fetch_json', side_effect=ValueError('invalid sk-one console-secret')):
            usage = balance.get_key_usage_for_day(site, '2026-11-04')
        self.assertEqual(usage['failedCount'], 1)
        self.assertNotIn('sk-one', str(usage))
        self.assertNotIn('console-secret', str(usage))

    def test_report_marks_partial_keys_and_ignores_old_account_snapshots(self):
        results = [
            {'balance': 100, 'currency': 'CNY', 'usage': {'date': '2026-11-04', 'source': 'keys', 'cost': 1.25, 'failedCount': 1}},
            {'balance': 200, 'currency': 'CNY', 'usage': {'date': '2026-11-04', 'cost': 999}},
        ]
        with patch.object(state, 'saved_sites', [self.site, {**self.site, 'id': 'beta', 'name': 'Beta'}]), \
                patch.object(state, 'global_webhook', 'https://example.com/webhook'), \
                patch.object(balance, 'get_balance', side_effect=results), \
                patch.object(balance, 'record_daily_usage'), \
                patch.object(balance, 'day_key_of', return_value='2026-11-05'), \
                patch.object(balance, 'usage_for_day', return_value=999) as snapshots, \
                patch.object(balance, 'send_wecom') as send:
            self.assertTrue(balance.run_daily_report(timezone='Asia/Shanghai'))
        snapshots.assert_not_called()
        report = send.call_args.args[1]
        self.assertIn('Alpha**：100.00，消耗 1.25（成功项小计，1 个 Key 未获取）', report)
        self.assertIn('Beta**：200.00，消耗未获取', report)
        self.assertNotIn('Key 总消耗', report)
        self.assertNotIn('仅已获取 Key 的小计', report)
        self.assertNotIn('999', report)

    def test_report_labels_missing_usage_keys_as_unconfigured(self):
        result = {'balance': 100, 'currency': 'CNY', 'usage': None,
                  'usageError': '请先配置消耗查询 API Key'}
        with patch.object(state, 'saved_sites', [self.site]), \
                patch.object(state, 'global_webhook', 'https://example.com/webhook'), \
                patch.object(balance, 'get_balance', return_value=result), \
                patch.object(balance, 'record_daily_usage'), \
                patch.object(balance, 'send_wecom') as send:
            self.assertTrue(balance.run_daily_report(timezone='Asia/Shanghai'))
        report = send.call_args.args[1]
        self.assertIn('Alpha**：100.00，消耗未配置', report)
        self.assertNotIn('请先配置消耗查询 API Key', report)


if __name__ == '__main__':
    unittest.main()
