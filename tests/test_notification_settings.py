import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class NotificationSettingsTests(unittest.TestCase):
    def test_independent_webhooks_migration_persistence_and_api_routing(self):
        script = '''
import sys
sys.argv = ['manage.py', 'test']
import django
django.setup()
import time
from unittest.mock import patch
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client
from monitor import state
from monitor.storage import load_daily_report_webhook, load_sites

# Existing alert destinations survive the migration; reports require their own setting.
executor = MigrationExecutor(connection)
executor.migrate([('monitor', '0009_key_usage_day_finalized')])
old_apps = executor.loader.project_state([('monitor', '0009_key_usage_day_finalized')]).apps
old_apps.get_model('monitor', 'GlobalSettings').objects.update_or_create(
    pk=1, defaults={'webhook': 'https://example.com/alert-secret'})
executor = MigrationExecutor(connection)
executor.migrate(executor.loader.graph.leaf_nodes())
assert load_sites()[1] == 'https://example.com/alert-secret'
assert load_daily_report_webhook() == ''
state.global_webhook = load_sites()[1]
state.daily_report_webhook = ''

client = Client()
client.cookies['balance_session'] = 'notification-session'
state.sessions['notification-session'] = {'expires_at': time.time() + 60}
with patch('monitor.balance._post_wecom') as send:
    # Report tests never borrow the alert URL.
    response = client.post('/api/notify-test', {'target': 'dailyReport'}, content_type='application/json')
    assert not response.json()['ok']
    send.assert_not_called()
    response = client.post('/api/notify-test', {'target': 'invalid'}, content_type='application/json')
    assert not response.json()['ok']
    send.assert_not_called()

    response = client.put('/api/sites', {'sites': [], 'settings': {
        'webhook': '', 'dailyReportWebhook': '  https://example.com/report-secret  ',
    }}, content_type='application/json')
    assert response.json()['ok'], response.content
    assert response.json()['data']['settings'] == {'hasWebhook': True, 'hasDailyReportWebhook': True}
    assert b'alert-secret' not in response.content and b'report-secret' not in response.content
    assert state.global_webhook == 'https://example.com/alert-secret'
    assert state.daily_report_webhook == 'https://example.com/report-secret'

    # Blanks and older clients omitting the report field both preserve the secret.
    for settings in ({'webhook': '', 'dailyReportWebhook': '  '}, {'webhook': ''}):
        response = client.put('/api/sites', {'sites': [], 'settings': settings}, content_type='application/json')
        assert response.json()['ok'], response.content
        assert load_daily_report_webhook() == 'https://example.com/report-secret'

    response = client.get('/api/sites')
    assert response.json()['data']['settings'] == {'hasWebhook': True, 'hasDailyReportWebhook': True}
    assert b'alert-secret' not in response.content and b'report-secret' not in response.content

    for body, destination in (
        ({'target': 'dailyReport', 'webhook': ''}, 'https://example.com/report-secret'),
        ({'webhook': ''}, 'https://example.com/alert-secret'),
    ):
        response = client.post('/api/notify-test', body, content_type='application/json')
        assert response.json()['ok'], response.content
        assert send.call_args.args[0] == destination
        payload = send.call_args.args[1]
        assert payload['msgtype'] == ('image' if body.get('target') == 'dailyReport' else 'markdown')
        if payload['msgtype'] == 'image':
            import base64
            assert base64.b64decode(payload['image']['base64']).startswith(bytes.fromhex('89504e470d0a1a0a'))

    # A successful test persists just that channel, even without saving any sites.
    for body in (
        {'target': 'dailyReport', 'webhook': 'https://example.com/new-report'},
        {'webhook': 'https://example.com/new-alert'},
    ):
        response = client.post('/api/notify-test', body, content_type='application/json')
        assert response.json()['ok'], response.content
    assert load_sites()[1] == state.global_webhook == 'https://example.com/new-alert'
    assert load_daily_report_webhook() == state.daily_report_webhook == 'https://example.com/new-report'

    send.side_effect = ValueError('delivery failed')
    response = client.post('/api/notify-test', {
        'target': 'dailyReport', 'webhook': 'https://example.com/failed-report',
    }, content_type='application/json')
    assert not response.json()['ok']
    assert load_daily_report_webhook() == state.daily_report_webhook == 'https://example.com/new-report'

    send.reset_mock()
    client.cookies.clear()
    assert client.post('/api/notify-test', {}, content_type='application/json').status_code == 401
    send.assert_not_called()
'''
        restart = '''
import sys
sys.argv = ['manage.py', 'test']
import django
django.setup()
from monitor import state
assert state.global_webhook == 'https://example.com/new-alert'
assert state.daily_report_webhook == 'https://example.com/new-report'
'''
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, 'DJANGO_SETTINGS_MODULE': 'balance_watch.settings',
                   'DATA_DIR': directory, 'APP_PASSWORD': 'isolated-test-password',
                   'DJANGO_SECRET_KEY': 'isolated-test-secret'}
            for code in (script, restart):
                result = subprocess.run([sys.executable, '-c', code], env=env,
                                        cwd=Path(__file__).resolve().parents[1],
                                        capture_output=True, text=True, encoding='utf-8', timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
