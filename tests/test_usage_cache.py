import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class UsageCacheTests(unittest.TestCase):
    def test_database_cache_refresh_failure_restart_and_configuration_changes(self):
        script = r'''
import sys
sys.argv = ['manage.py', 'test']
import django
django.setup()
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from django.db import connection, connections
from django.db.migrations.executor import MigrationExecutor
from django.core.management import call_command
from django.test.utils import CaptureQueriesContext
from monitor import balance, state
from monitor.models import KeyUsageDay
from monitor.usage_cache import (read_usage_history, refresh_site_usage, save_usage_points,
                                 save_today_usage, usage_fingerprint)
assert not MigrationExecutor(connection).migration_plan(
    MigrationExecutor(connection).loader.graph.leaf_nodes())
call_command('makemigrations', 'monitor', check=True, dry_run=True, verbosity=0)
site = {'id': 'alpha', 'baseUrl': 'https://example.com', 'siteType': 'sub2',
        'timezone': 'Asia/Shanghai', 'currency': 'CNY', 'intervalSeconds': 300,
        'usageKeys': [{'id': 'one', 'token': 'sk-one'}, {'id': 'two', 'token': 'sk-two'}]}
now = datetime(2026, 11, 5, 0, tzinfo=timezone.utc)

def query(site, queried_at, requested_days=None):
    from monitor.time_utils import day_key_of, shift_day_key
    today = day_key_of(site['timezone'], queried_at)
    return [{'date': shift_day_key(today, offset), 'cost': 1.234567,
             'successCount': 2, 'failedCount': 0} for offset in range(-6, 1)
            if requested_days is None or shift_day_key(today, offset) in requested_days]

with patch.object(balance, 'get_weekly_key_usage', side_effect=query) as upstream:
    # An empty cache still returns all seven dates without an upstream request.
    empty = read_usage_history([site], now)
    assert len(empty['series'][0]['days']) == 7
    assert all(day['cost'] is None for day in empty['series'][0]['days'])
    upstream.assert_not_called()
    refresh_site_usage(site, now=now)
    assert len(upstream.call_args.kwargs['requested_days']) == 7
    assert KeyUsageDay.objects.count() == 7
    with CaptureQueriesContext(connection) as queries:
        cached = read_usage_history([site], now)
    assert len(queries) == 1
    assert all(day['cost'] == 1.234567 for day in cached['series'][0]['days'])
    assert cached['source'] == 'database'
    # A restart/cleared process state does not lose amounts.
    connections.close_all()
    state.daily_usage.clear()
    assert read_usage_history([site], now) == cached
    refresh_site_usage(site, now=now + timedelta(seconds=30))
    assert upstream.call_count == 1
    refresh_site_usage(site, now=now + timedelta(seconds=301))
    assert upstream.call_args.kwargs['requested_days'] == ['2026-11-05']
    refresh_site_usage(site, now=now + timedelta(hours=1, seconds=1))
    assert upstream.call_args.kwargs['requested_days'] == ['2026-11-04', '2026-11-05']
    refresh_site_usage(site, force=True, now=now + timedelta(hours=1, seconds=2))
    assert len(upstream.call_args.kwargs['requested_days']) == 7
    # Missing data after midnight needs a fresh date; six cached days survive.
    refresh_site_usage(site, now=now + timedelta(days=1))
    assert upstream.call_args.kwargs['requested_days'] == ['2026-11-05', '2026-11-06']
    # Even after a multi-day outage, a saved partial calendar day is finalized.
    outage_site = {**site, 'id': 'outage'}
    refresh_site_usage(outage_site, now=now)
    refresh_site_usage(outage_site, now=now + timedelta(days=3))
    assert '2026-11-05' in upstream.call_args.kwargs['requested_days']

failed_at = now + timedelta(days=1, minutes=10)
with patch.object(balance, 'get_weekly_key_usage', side_effect=ValueError('upstream failed')):
    refresh_site_usage(site, force=True, now=failed_at)
retained = read_usage_history([site], failed_at)['series'][0]['days']
assert all(day['cost'] == 1.234567 and day['staleCount'] == 1 for day in retained)
assert all(day['updatedAt'] != failed_at.isoformat() for day in retained)
row = KeyUsageDay.objects.get(site_id='alpha', day_key='2026-11-06')
assert row.attempted_at == failed_at and row.refresh_failed
# Worse partial results cannot replace complete amounts; zero is a valid success.
save_usage_points(site, [{'date': '2026-11-06', 'cost': .5,
                         'successCount': 1, 'failedCount': 1}], failed_at + timedelta(seconds=1))
row.refresh_from_db()
assert row.cost == 1.234567 and row.refresh_failed
save_today_usage(site, {'date': '2026-11-06', 'source': 'keys', 'cost': 0,
                       'successCount': 2, 'failedCount': 0}, failed_at + timedelta(seconds=2))
row.refresh_from_db()
assert row.cost == 0 and not row.refresh_failed
# A slow older query may not overwrite a newer persisted value.
save_usage_points(site, [{'date': '2026-11-06', 'cost': 999,
                         'successCount': 2, 'failedCount': 0}], now)
row.refresh_from_db()
assert row.cost == 0
# Relabelling keeps the same cache; a different key/zone/currency/address does not.
assert usage_fingerprint({**site, 'name': 'Renamed'}) == usage_fingerprint(site)
for change in ({'timezone': 'UTC'}, {'currency': 'USD'}, {'baseUrl': 'https://other.com'},
               {'usageKeys': [{'id': 'different', 'token': 'sk-new'}]}):
    assert all(day['cost'] is None for day in read_usage_history([{**site, **change}], failed_at)['series'][0]['days'])
# Groups aggregate only matching currencies and calendar boundaries.
beta = {**site, 'id': 'beta'}
usd = {**site, 'id': 'usd', 'currency': 'USD'}
utc = {**site, 'id': 'utc', 'timezone': 'UTC'}
for other in (beta, usd, utc):
    with patch.object(balance, 'get_weekly_key_usage', side_effect=query):
        refresh_site_usage(other, now=failed_at)
groups = read_usage_history([site, beta, usd, utc], failed_at)['series']
assert len(groups) == 3 and groups[0]['siteCount'] == 2
assert groups[0]['days'][-1]['cost'] == 1.234567
assert 'sk-' not in str(groups)
# Exercise threaded POST refresh followed by database GET, with real SQLite.
import time
from django.test import Client
client = Client()
client.cookies['balance_session'] = 'cache-test-session'
with patch.object(state, 'saved_sites', [site, beta]), patch.dict(
    state.sessions, {'cache-test-session': {'expires_at': time.time() + 60}}, clear=True
), patch.object(balance, 'get_weekly_key_usage', side_effect=query) as upstream:
    assert client.get('/api/usage/history').status_code == 200
    upstream.assert_not_called()
    refreshed = client.post('/api/usage/history')
    assert refreshed.status_code == 200, refreshed.content
    assert upstream.call_count == 2
    assert refreshed.json()['data']['source'] == 'database'
    assert len(refreshed.json()['data']['series'][0]['days']) == 7
    upstream.reset_mock()
    assert client.get('/api/usage/history').json()['data'] == refreshed.json()['data']
    upstream.assert_not_called()
# The scheduled cache job runs independently of balance/profile availability.
from monitor import scheduler
jobs = {}
class FakeScheduler:
    def __init__(self, **kwargs): pass
    def scheduled_job(self, trigger, **kwargs):
        def register(func):
            jobs[kwargs['id']] = func
            return func
        return register
    def start(self): pass
    def shutdown(self, **kwargs): pass
with patch('apscheduler.schedulers.background.BackgroundScheduler', FakeScheduler), \
        patch('monitor.storage.load_sites', return_value=([site, {**beta, 'enabled': False}], '')), \
        patch('monitor.storage.load_upstream_sessions', return_value={}), \
        patch('monitor.storage.load_daily_usage', return_value={}), \
        patch('monitor.usage_cache.refresh_usage_history') as refresh:
    scheduler.start()
    jobs['usage_history_tick']()
    refresh.assert_called_once_with([site])
    scheduler.stop()
'''
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, 'DJANGO_SETTINGS_MODULE': 'balance_watch.settings',
                   'DATA_DIR': directory, 'APP_PASSWORD': 'isolated-test-password',
                   'DJANGO_SECRET_KEY': 'isolated-test-secret'}
            result = subprocess.run([sys.executable, '-c', script], env=env,
                                    cwd=Path(__file__).resolve().parents[1],
                                    capture_output=True, text=True, encoding='utf-8', timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
