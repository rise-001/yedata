import json
import os
import time
import unittest
from unittest.mock import Mock, patch

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

    def test_usage_requires_newapi_site_and_token(self):
        with self.assertRaisesRegex(ValueError, 'New API'):
            balance.get_token_usage({**SITE, 'siteType': 'sub2'})
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
