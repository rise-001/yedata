import json
import os
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'balance_watch.settings')

from django.test import Client

from monitor import balance, state


SITE = {
    'id': 'newapi-1',
    'name': 'New API',
    'baseUrl': 'https://newapi.example.com/',
    'siteType': 'newapi',
    'authMode': 'token',
    'token': 'Bearer sk-secret',
    'newApiUserId': '7',
}


class TokenUsageTests(unittest.TestCase):
    def test_usage_request_uses_saved_token_and_user_id(self):
        with patch.object(balance, 'fetch_json', return_value={'data': {'used_quota': 12}}) as fetch:
            result = balance.get_token_usage(SITE)

        self.assertEqual(result['data']['used_quota'], 12)
        fetch.assert_called_once_with(
            'https://newapi.example.com/api/usage/token/',
            headers={'Authorization': 'sk-secret', 'new-api-user': '7'},
        )

    def test_usage_requires_newapi_site_and_token(self):
        with self.assertRaisesRegex(ValueError, 'New API'):
            balance.get_token_usage({**SITE, 'siteType': 'sub2'})
        with self.assertRaisesRegex(ValueError, '访问令牌'):
            balance.get_token_usage({**SITE, 'token': ''})

    def test_usage_defaults_new_api_user_to_one(self):
        with patch.object(balance, 'fetch_json', return_value={}) as fetch:
            balance.get_token_usage({**SITE, 'newApiUserId': ''})
        self.assertEqual(fetch.call_args.kwargs['headers']['new-api-user'], '1')

    @patch('monitor.views.get_token_usage', return_value={'data': {'used_quota': 12}})
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
        self.assertEqual(response.json()['data']['data']['used_quota'], 12)
        get_usage.assert_called_once_with(SITE)


if __name__ == '__main__':
    unittest.main()
