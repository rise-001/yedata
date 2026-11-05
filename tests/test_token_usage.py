import json
import os
import time
import unittest
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
    def test_usage_request_uses_saved_token_and_user_id(self):
        response = {'code': True, 'data': {
            'name': 'production', 'used_quota': 12, 'remain_quota': 88,
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
            'https://newapi.example.com/api/usage/token/',
            headers={'Authorization': 'Bearer sk-usage-key'},
        )

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
        self.assertEqual(fetch.call_args.kwargs['headers'], {'Authorization': 'Bearer sk-prefixed'})

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
        for response in ({}, [], {'today': None}, {'today': {'actual_cost': True}},
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
            return {'today': {'actual_cost': 0 if token == 'Bearer sk-first' else '2.5'}}

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
        with patch.object(balance, 'fetch_json', return_value={'data': {'used_quota': 12}}) as fetch:
            result = balance.get_token_usage(site)
        self.assertEqual(result['successCount'], 2)
        self.assertTrue(all(key['data']['usedQuota'] == 12 for key in result['keys']))
        self.assertEqual({call.kwargs['headers']['Authorization'] for call in fetch.call_args_list},
                         {'Bearer sk-one', 'Bearer sk-two'})

    def test_endpoint_returns_per_key_sub2_usage_without_saved_secrets(self):
        site = {**SITE, 'siteType': 'sub2', 'usageKeys': [
            {'id': 'one', 'name': 'One', 'token': 'sk-one'},
            {'id': 'two', 'name': 'Two', 'token': 'sk-two'},
        ]}
        client = Client()
        client.cookies['balance_session'] = 'admin-session'
        with patch.object(state, 'saved_sites', [site]), patch.dict(
            state.sessions, {'admin-session': {'expires_at': time.time() + 60}}, clear=True
        ), patch.object(balance, 'fetch_json', return_value={'today': {'actual_cost': 1.25}}):
            response = client.post('/api/usage/token', data=json.dumps({'siteId': SITE['id']}),
                                   content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['data']['todayActualCost'], 2.5)
        self.assertEqual(response.json()['data']['successCount'], 2)
        self.assertNotIn('sk-', response.content.decode())

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


if __name__ == '__main__':
    unittest.main()
