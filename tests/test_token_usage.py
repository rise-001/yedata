import json
import os
import time
import unittest
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock, patch

import requests

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'balance_watch.settings')

from django.test import Client

from monitor import balance, state


SITE = {
    'id': 'newapi-1',
    'name': 'New API',
    'baseUrl': 'https://newapi.example.com/',
    'siteType': 'newapi',
    'authMode': 'token',
    'token': 'dashboard-login-token',
    'usageToken': 'sk-usage-key',
    'newApiUserId': '7',
}


class TokenUsageTests(unittest.TestCase):
    def setUp(self):
        # Isolate existing cumulative/Sub2 fixtures from the separate daily API.
        today = patch.object(balance, '_get_newapi_today_usage', return_value={})
        today.start()
        self.addCleanup(today.stop)

    def test_usage_request_uses_saved_token_and_user_id(self):
        response = {'code': True, 'data': {
            'name': 'production', 'used_quota': 6000000, 'remain_quota': 44000000,
            'expired_time': -1, 'unlimited_quota': False,
            'model_limits_enabled': True, 'model_limits': ['gpt-4o'],
        }}
        with patch.object(balance, 'fetch_json', return_value=response) as fetch:
            result = balance.get_token_usage(SITE)

        self.assertEqual(result, {
            'name': 'production', 'usedQuota': 12.0, 'remainingQuota': 88.0,
            'totalQuota': 100.0, 'unlimitedQuota': False, 'expiresAt': -1,
            'modelLimitsEnabled': True, 'modelLimits': ['gpt-4o'],
        })
        fetch.assert_called_once_with(
            'https://newapi.example.com/api/usage/token',
            headers={'Authorization': 'Bearer sk-usage-key', 'New-Api-User': '7'},
        )

    def test_newapi_total_used_is_converted_to_cost_and_name_is_preserved(self):
        response = {'code': True, 'data': {
            'expires_at': 0, 'model_limits': {}, 'model_limits_enabled': False,
            'name': 'gemini 文本', 'object': 'token_usage',
            'total_available': -4868070, 'total_granted': 63362209,
            'total_used': 68230279, 'unlimited_quota': True,
        }, 'message': 'ok'}
        site = {**SITE, 'newApiUserId': '5', 'balanceDivisor': 10, 'balanceMultiplier': 20}
        with patch.object(balance, 'fetch_json', return_value=response) as fetch:
            result = balance.get_token_usage(site)
        self.assertEqual(result, {
            'name': 'gemini 文本', 'usedQuota': 136.460558,
            'remainingQuota': -9.73614, 'totalQuota': 126.724418,
            'unlimitedQuota': True, 'expiresAt': 0,
            'modelLimitsEnabled': False, 'modelLimits': [],
        })
        self.assertEqual(fetch.call_args.kwargs['headers'], {
            'Authorization': 'Bearer sk-usage-key', 'New-Api-User': '5',
        })

    def test_newapi_zero_and_string_quotas_are_converted_once(self):
        response = {'data': {'total_used': '0', 'total_available': '500000',
                             'total_granted': '500000', 'used_quota': 999999}}
        with patch.object(balance, 'fetch_json', return_value=response):
            result = balance.get_token_usage(SITE)
        self.assertEqual(result['usedQuota'], 0)
        self.assertEqual(result['remainingQuota'], 1)
        self.assertEqual(result['totalQuota'], 1)

    def test_newapi_model_limit_mapping_and_negative_remaining(self):
        response = {'data': {'total_used': 1000000, 'total_granted': 500000,
                             'model_limits_enabled': True,
                             'model_limits': {'gemini': True, 'disabled-model': False}}}
        with patch.object(balance, 'fetch_json', return_value=response):
            result = balance.get_token_usage(SITE)
        self.assertEqual(result['usedQuota'], 2)
        self.assertEqual(result['remainingQuota'], -1)
        self.assertEqual(result['modelLimits'], ['gemini'])

    def test_newapi_user_id_is_optional_and_independent_of_cookie_balance_auth(self):
        with patch.object(balance, 'fetch_json', return_value={}) as fetch:
            balance.get_token_usage({**SITE, 'newApiUserId': ''})
            self.assertEqual(fetch.call_args.kwargs['headers'], {'Authorization': 'Bearer sk-usage-key'})
            balance.get_token_usage({**SITE, 'authMode': 'cookie', 'sessionCookie': 'session=balance'})
            self.assertEqual(fetch.call_args.kwargs['headers'], {
                'Authorization': 'Bearer sk-usage-key', 'New-Api-User': '7',
            })

    def test_usage_requires_supported_site_and_token(self):
        with self.assertRaisesRegex(ValueError, 'New API'):
            balance.get_token_usage({**SITE, 'siteType': 'custom'})
        with self.assertRaisesRegex(ValueError, '消耗查询 API Key'):
            balance.get_token_usage({**SITE, 'usageToken': ''})

    def test_auto_detected_newapi_site_can_query_usage(self):
        site = {**SITE, 'siteType': 'auto', 'mePath': '/api/user/self'}
        with patch.object(balance, 'fetch_json', return_value={}):
            self.assertEqual(balance.get_token_usage(site)['usedQuota'], None)

    def test_usage_accepts_key_with_bearer_prefix(self):
        with patch.object(balance, 'fetch_json', return_value={}) as fetch:
            balance.get_token_usage({**SITE, 'usageToken': 'Bearer sk-prefixed'})
        self.assertEqual(fetch.call_args.kwargs['headers'], {
            'Authorization': 'Bearer sk-prefixed', 'New-Api-User': '7',
        })

    def test_fetch_json_accepts_boolean_true_code(self):
        response = Mock(ok=True, status_code=200, text='json')
        response.json.return_value = {'code': True, 'data': {'used_quota': 1}}
        response.headers = {}
        with patch.object(balance.requests, 'request', return_value=response):
            self.assertEqual(balance.fetch_json('https://example.com')['code'], True)

    def test_usage_normalizes_model_limit_string_and_expired_at(self):
        response = {'data': {'model_limits': 'gpt-4o, claude-3-5', 'expired_at': 123}}
        with patch.object(balance, 'fetch_json', return_value=response):
            result = balance.get_token_usage(SITE)
        self.assertEqual(result['modelLimits'], ['gpt-4o', 'claude-3-5'])
        self.assertEqual(result['expiresAt'], 123)

    def test_sub2_uses_v1_usage_and_today_actual_cost_without_balance_scaling(self):
        site = {**SITE, 'siteType': 'sub2', 'balanceDivisor': 500000, 'balanceMultiplier': 10}
        for response, expected in (
            ({'mode': 'unrestricted', 'usage': {
                'today': {'actual_cost': 1.25, 'cost': 99},
                'total': {'actual_cost': 999},
            }}, 1.25),
            ({'mode': 'quota_limited', 'usage': {'today': {'actual_cost': 0}}}, 0),
            ({'data': {'usage': {'today': {'actual_cost': '2.345678'}}}}, 2.345678),
            ({'unit': 'USD', 'usage': {
                'today': {'actual_cost': 2.517114045, 'cost': 8.39038015},
                'total': {'actual_cost': 1001.7051126285},
            }}, 2.517114045),
            ({'today': {'actual_cost': 0}, 'total': {'actual_cost': 999}}, 0),
            ({'data': {'today': {'actual_cost': '1.234567'}}}, 1.234567),
        ):
            with self.subTest(response=response), patch.object(balance, 'fetch_json', return_value=response) as fetch:
                self.assertEqual(balance.get_token_usage(site), {'todayActualCost': expected})
                fetch.assert_called_once_with(
                    'https://newapi.example.com/v1/usage',
                    headers={'Authorization': 'Bearer sk-usage-key'}, timeout=20,
                )

    def test_auto_detected_sub2_uses_usage_endpoint(self):
        site = {**SITE, 'siteType': 'auto', 'mePath': '/api/v1/auth/me'}
        with patch.object(balance, 'fetch_json', return_value={'today': {'actual_cost': 2}}):
            self.assertEqual(balance.get_token_usage(site)['todayActualCost'], 2)

    def test_sub2_rejects_missing_or_invalid_today_cost_instead_of_reporting_zero(self):
        for response in ({}, [], {'usage': None}, {'usage': {}}, {'usage': {'today': None}},
                         {'usage': {'today': {'cost': 25}, 'total': {'actual_cost': 999}}},
                         {'usage': {'today': {'actual_cost': 'nan'}}},
                         {'today': None}, {'today': {'actual_cost': True}},
                         {'today': {'actual_cost': 'nan'}}, {'today': {'actual_cost': 'inf'}},
                         {'today': {'actual_cost': 'invalid'}}):
            with self.subTest(response=response), patch.object(balance, 'fetch_json', return_value=response):
                with self.assertRaisesRegex(ValueError, 'today.actual_cost'):
                    balance.get_token_usage({**SITE, 'siteType': 'sub2'})

    def test_sub2_queries_multiple_keys_and_returns_partial_total_without_secrets(self):
        site = {**SITE, 'siteType': 'sub2', 'usageKeys': [
            {'id': 'first', 'name': '生产', 'token': 'Bearer sk-first'},
            {'id': 'second', 'name': '测试', 'token': 'sk-second'},
            {'id': 'third', 'name': '开发', 'token': 'sk-third'},
        ]}

        def fetch(url, *, headers, timeout):
            token = headers['Authorization']
            if token == 'Bearer sk-second':
                raise ValueError('invalid sk-second')
            return {'usage': {'today': {'actual_cost': 0 if token == 'Bearer sk-first' else '2.5'}}}

        with patch.object(balance, 'fetch_json', side_effect=fetch) as fetch_mock:
            result = balance.get_token_usage(site)
        self.assertEqual(fetch_mock.call_count, 3)
        self.assertEqual([key['keyId'] for key in result['keys']], ['first', 'second', 'third'])
        self.assertEqual(result['successCount'], 2)
        self.assertEqual(result['failedCount'], 1)
        self.assertEqual(result['todayActualCost'], 2.5)
        self.assertEqual(result['keys'][0]['data']['todayActualCost'], 0)
        self.assertNotIn('sk-', json.dumps(result))
        self.assertNotIn('dashboard-login-token', json.dumps(fetch_mock.call_args_list, default=str))

    def test_all_failed_keys_have_unknown_total_and_timeout_message(self):
        site = {**SITE, 'siteType': 'sub2', 'usageKeys': [
            {'id': 'one', 'name': 'One', 'token': 'sk-secret'},
        ]}
        with patch.object(balance, 'fetch_json', side_effect=requests.Timeout('sk-secret')):
            result = balance.get_token_usage(site)
        self.assertIsNone(result['todayActualCost'])
        self.assertEqual(result['successCount'], 0)
        self.assertEqual(result['failedCount'], 1)
        self.assertIn('超时', result['keys'][0]['message'])
        self.assertNotIn('sk-secret', str(result))

    def test_newapi_also_supports_multiple_keys(self):
        site = {**SITE, 'usageKeys': [
            {'id': 'one', 'name': 'One', 'token': 'sk-one'},
            {'id': 'two', 'name': 'Two', 'token': 'sk-two'},
        ]}
        with patch.object(balance, 'fetch_json', return_value={'data': {'total_used': 6000000}}) as fetch:
            result = balance.get_token_usage(site)
        self.assertEqual(result['successCount'], 2)
        self.assertTrue(all(key['data']['usedQuota'] == 12 for key in result['keys']))
        self.assertEqual({call.kwargs['headers']['Authorization'] for call in fetch.call_args_list},
                         {'Bearer sk-one', 'Bearer sk-two'})
        self.assertTrue(all(call.kwargs['headers']['New-Api-User'] == '7' for call in fetch.call_args_list))

    def test_endpoint_returns_per_key_sub2_usage_without_saved_secrets(self):
        site = {**SITE, 'siteType': 'sub2', 'usageKeys': [
            {'id': 'one', 'name': 'One', 'token': 'sk-one'},
            {'id': 'two', 'name': 'Two', 'token': 'sk-two'},
        ]}
        client = Client()
        client.cookies['balance_session'] = 'admin-session'
        with patch.object(state, 'saved_sites', [site]), patch.dict(
            state.sessions, {'admin-session': {'expires_at': time.time() + 60}}, clear=True
        ), patch.object(balance, 'fetch_json', return_value={
            'mode': 'unrestricted', 'usage': {'today': {'actual_cost': 1.25}},
        }):
            response = client.post('/api/usage/token', data=json.dumps({'siteId': SITE['id']}),
                                   content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['data']['todayActualCost'], 2.5)
        self.assertEqual(response.json()['data']['successCount'], 2)
        self.assertNotIn('sk-', response.content.decode())

    @patch('monitor.views.get_token_usage', return_value={
        'todayActualCost': 3.75, 'todaySuccessCount': 2, 'todayFailedCount': 1,
    })
    def test_manual_usage_query_updates_dashboard_summary_without_changing_balance(self, get_usage):
        client = Client()
        client.cookies['balance_session'] = 'admin-session'
        previous = {'ok': True, 'balance': 99, 'checkedAt': '2026-11-05T00:00:00Z'}
        with patch.object(state, 'saved_sites', [dict(SITE)]), patch.dict(
            state.sessions, {'admin-session': {'expires_at': time.time() + 60}}, clear=True
        ), patch.dict(state.scheduled_results, {SITE['id']: previous}, clear=True):
            response = client.post('/api/usage/token', data=json.dumps({'siteId': SITE['id']}),
                                   content_type='application/json')
            self.assertEqual(response.status_code, 200)
            summary = response.json()['todayUsage']
            self.assertEqual(summary['source'], 'keys')
            self.assertEqual(summary['cost'], 3.75)
            self.assertEqual(summary['failedCount'], 1)
            cached = state.scheduled_results[SITE['id']]
            self.assertEqual(cached['todayUsage'], summary)
            self.assertEqual(cached['balance'], 99)
            self.assertEqual(cached['checkedAt'], previous['checkedAt'])

    @patch('monitor.views.get_token_usage', return_value={'usedQuota': 12})
    def test_endpoint_requires_login_and_uses_saved_site(self, get_usage):
        client = Client()
        with patch.object(state, 'saved_sites', [dict(SITE)]), patch.dict(
            state.sessions, {'admin-session': {'expires_at': time.time() + 60}}, clear=True
        ):
            self.assertEqual(
                client.post('/api/usage/token', data='{}', content_type='application/json').status_code,
                401,
            )
            client.cookies['balance_session'] = 'admin-session'
            response = client.post(
                '/api/usage/token',
                data=json.dumps({'siteId': 'newapi-1'}),
                content_type='application/json',
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['data']['usedQuota'], 12)
        get_usage.assert_called_once_with(SITE)


class NewApiTodayUsageTests(unittest.TestCase):
    def setUp(self):
        self.site = {**SITE, 'token': 'console-access-token', 'newApiUserId': '5',
                     'usageKeys': [{'id': 'first', 'name': '本地备注', 'token': 'sk-first'}]}
        self.now = datetime(2026, 11, 4, 16, 5, tzinfo=timezone.utc)
        clock = patch.object(balance, 'datetime')
        clock.start().now.return_value = self.now
        self.addCleanup(clock.stop)

    def fetch_usage(self, url, *, headers, timeout=None):
        path = urlsplit(url).path
        if path == '/api/usage/token':
            name = 'gemini 文本 & 1' if headers['Authorization'] == 'Bearer sk-first' else 'second'
            return {'code': True, 'data': {'name': name, 'total_used': 68230279}}
        if path == '/api/log/self/stat':
            quota = 1500000 if parse_qs(urlsplit(url).query)['token_name'] == ['gemini 文本 & 1'] else 0
            return {'success': True, 'data': {'quota': quota}}
        raise AssertionError('Unexpected API path')

    def test_daily_query_uses_upstream_name_console_credentials_and_utc8_day(self):
        with patch.object(balance, 'fetch_json', side_effect=self.fetch_usage) as fetch:
            result = balance.get_token_usage(self.site)
        data = result['keys'][0]['data']
        self.assertEqual(data['usedQuota'], 136.460558)
        self.assertEqual(data['todayQuota'], 1500000)
        self.assertEqual(data['todayActualCost'], 3)
        self.assertEqual(result['todayActualCost'], 3)
        self.assertEqual(result['todaySuccessCount'], 1)
        self.assertEqual(result['todayFailedCount'], 0)
        self.assertNotIn('todayError', data)
        stat_call = next(call for call in fetch.call_args_list if '/api/log/self/stat?' in call.args[0])
        self.assertEqual(stat_call.kwargs, {
            'headers': {'Authorization': 'Bearer console-access-token', 'New-Api-User': '5'},
            'timeout': 30,
        })
        self.assertEqual(parse_qs(urlsplit(stat_call.args[0]).query), {
            'token_name': ['gemini 文本 & 1'],
            'start_timestamp': [str(int(datetime(2026, 11, 4, 16, tzinfo=timezone.utc).timestamp()))],
            'end_timestamp': [str(int(self.now.timestamp()))],
        })
        self.assertEqual(data['todayRange']['timezone'], 'Asia/Shanghai')

    def test_multiple_keys_use_identical_window_and_zero_daily_usage_is_success(self):
        site = {**self.site, 'token': 'Bearer console-access-token',
                'usageKeys': [*self.site['usageKeys'], {'id': 'second', 'name': '备注 2', 'token': 'sk-second'}]}
        with patch.object(balance, 'fetch_json', side_effect=self.fetch_usage) as fetch:
            result = balance.get_token_usage(site)
        self.assertEqual(result['todaySuccessCount'], 2)
        self.assertEqual(result['todayFailedCount'], 0)
        self.assertEqual(result['todayActualCost'], 3)
        self.assertEqual(result['keys'][1]['data']['todayActualCost'], 0)
        calls = [call for call in fetch.call_args_list if '/api/log/self/stat?' in call.args[0]]
        self.assertEqual(len(calls), 2)
        self.assertEqual({parse_qs(urlsplit(call.args[0]).query)['end_timestamp'][0] for call in calls},
                         {str(int(self.now.timestamp()))})
        self.assertTrue(all(call.kwargs['headers']['Authorization'] == 'Bearer console-access-token'
                            for call in calls))

    def test_daily_query_obeys_configured_site_timezone(self):
        with patch.object(balance, 'fetch_json', side_effect=self.fetch_usage) as fetch:
            result = balance.get_token_usage({**self.site, 'timezone': 'UTC'})
        daily = result['keys'][0]['data']
        self.assertEqual(daily['todayRange']['startTimestamp'],
                         int(datetime(2026, 11, 4, tzinfo=timezone.utc).timestamp()))
        self.assertEqual(daily['todayRange']['timezone'], 'UTC')

    def test_duplicate_upstream_names_are_not_counted_as_separate_daily_usage(self):
        site = {**self.site, 'usageKeys': [*self.site['usageKeys'],
                                         {'id': 'second', 'name': '备注 2', 'token': 'sk-second'}]}
        def fetch(url, **kwargs):
            if '/api/log/self/stat?' in url:
                return {'success': True, 'data': {'quota': 1000000}}
            return {'data': {'name': 'same-name', 'total_used': 500000}}
        with patch.object(balance, 'fetch_json', side_effect=fetch):
            result = balance.get_token_usage(site)
        self.assertEqual(result['successCount'], 2)
        self.assertEqual(result['todayFailedCount'], 2)
        self.assertIsNone(result['todayActualCost'])
        self.assertTrue(all('相同名称' in item['data']['todayError'] for item in result['keys']))

    def test_daily_failure_keeps_cumulative_data_and_reports_unknown_total(self):
        def fetch(url, **kwargs):
            if '/api/log/self/stat?' in url:
                raise requests.Timeout()
            return self.fetch_usage(url, **kwargs)
        with patch.object(balance, 'fetch_json', side_effect=fetch):
            result = balance.get_token_usage(self.site)
        self.assertTrue(result['keys'][0]['ok'])
        self.assertEqual(result['keys'][0]['data']['usedQuota'], 136.460558)
        self.assertIn('超时', result['keys'][0]['data']['todayError'])
        self.assertIsNone(result['todayActualCost'])
        self.assertEqual(result['todayFailedCount'], 1)

    def test_missing_console_settings_do_not_send_daily_request(self):
        for missing in ('token', 'newApiUserId'):
            with self.subTest(missing=missing), patch.object(balance, 'fetch_json', side_effect=self.fetch_usage) as fetch:
                result = balance.get_token_usage({**self.site, missing: ''})
                fetch.assert_called_once()
                self.assertEqual(result['keys'][0]['data']['usedQuota'], 136.460558)
                self.assertIn('已保存', result['keys'][0]['data']['todayError'])

    def test_missing_upstream_name_does_not_query_all_account_logs(self):
        with patch.object(balance, 'fetch_json', return_value={'data': {'total_used': 500000}}) as fetch:
            result = balance.get_token_usage(self.site)
        fetch.assert_called_once()
        self.assertIn('name', result['keys'][0]['data']['todayError'])
        self.assertEqual(result['keys'][0]['data']['usedQuota'], 1)

    def test_daily_response_requires_success_and_valid_quota(self):
        for response in ({'success': False}, {'data': {'quota': 500000}},
                         {'success': True, 'data': {}},
                         {'success': True, 'data': {'quota': 'nan'}},
                         {'success': True, 'data': {'quota': True}}):
            def fetch(url, **kwargs):
                return response if '/api/log/self/stat?' in url else self.fetch_usage(url, **kwargs)
            with self.subTest(response=response), patch.object(balance, 'fetch_json', side_effect=fetch):
                result = balance.get_token_usage(self.site)
                self.assertTrue(result['keys'][0]['ok'])
                self.assertIsNone(result['keys'][0]['data']['todayActualCost'])
                self.assertEqual(result['todayFailedCount'], 1)

    def test_endpoint_uses_console_credentials_saved_on_server(self):
        client = Client()
        client.cookies['balance_session'] = 'admin-session'
        with patch.object(state, 'saved_sites', [self.site]), patch.dict(
            state.sessions, {'admin-session': {'expires_at': time.time() + 60}}, clear=True
        ), patch.object(balance, 'fetch_json', side_effect=self.fetch_usage):
            response = client.post('/api/usage/token', data=json.dumps({'siteId': SITE['id']}),
                                   content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['data']['todayActualCost'], 3)
        self.assertNotIn('console-access-token', response.content.decode())
        self.assertNotIn('sk-first', response.content.decode())


if __name__ == '__main__':
    unittest.main()
