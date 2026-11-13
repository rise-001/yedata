import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class ReconciliationTests(unittest.TestCase):
    def test_persistent_accounting_reviews_failures_cross_day_and_api(self):
        script = r'''
import sys
sys.argv = ['manage.py', 'test']
import django
django.setup()
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch
from django.core.management import call_command
from django.db import connections
from django.test import RequestFactory
from monitor import balance, state, views
from monitor.models import ReconciliationSnapshot, ReconciliationEvent, ReconciliationState
from monitor.reconciliation import (collect_check, fingerprint, observation, record_observation,
                                    read_history, reset_baseline, pending_site_ids)

call_command('makemigrations', 'monitor', check=True, dry_run=True, verbosity=0)
site = {'id': 'account', 'name': 'Alpha', 'siteType': 'newapi', 'baseUrl': 'https://example.com',
        'authMode': 'token', 'token': 'secret-console-token', 'timezone': 'Asia/Shanghai',
        'balanceDivisor': 500000, 'balanceMultiplier': 1, 'currency': 'CNY', 'usageKeys': []}
now = datetime(2026, 11, 13, 2, tzinfo=timezone.utc)
assert fingerprint(site) == fingerprint({**site, 'balanceDivisor': 500000.0, 'balanceMultiplier': 1.0})

def account_result(b, u):
    return {'balanceField': 'quota', 'balanceDivisor': 500000, 'balanceMultiplier': 1,
            'accounting': {'balance': str(Decimal(str(b)) * 500000),
                           'usedQuota': str(Decimal(str(u)) * 500000), 'frozen': '0'}, 'raw': {'id': 9}}

def account(b, u, seconds, cfg=site):
    t = now + timedelta(seconds=seconds)
    return record_observation(cfg, observation(cfg, account_result(b, u), t, t))

# Normal consumption, exact-zero amounts and sub-cent precision.
first = account(1000, 200, 0)
assert first.status == 'baseline'
assert account(970, 230, 60).status == 'normal'
pending = account(1460, 240, 120)
assert pending.status == 'pending' and pending.difference == 500
assert pending_site_ids() == {'account'}
assert account(1459, 241, 150).status == 'pending'
# Persisted reviews survive a process/database connection restart.
connections.close_all()
confirmed = account(1450, 250, 180)
assert confirmed.id == pending.id and confirmed.status == 'recharge' and confirmed.difference == 500
assert not pending_site_ids()
assert account(1440, 260, 240).status == 'normal'
negative = account(1400, 270, 300)
assert negative.status == 'pending' and negative.difference == -30
assert account(1390, 280, 360).status == 'anomaly'

# Delayed usage must recover the original event, never manufacture a recharge.
delayed = account(1370, 280, 420)
assert delayed.status == 'pending'
recovered = account(1360, 310, 480)
assert recovered.id == delayed.id and recovered.status == 'recovered'
assert ReconciliationEvent.objects.filter(status='recharge').count() == 1
assert account('1359.995', 310, 540).status == 'normal'

# A changing discrepancy requires a new full stable window, not just elapsed age.
p = account('1349.995', 310, 600)
assert account('1339.995', 310, 660).status == 'pending'
assert account('1338.995', 311, 720).status == 'anomaly'
# Invalid samples and frozen amounts never move the valid baseline.
cursor = ReconciliationState.objects.get(site_id='account')
baseline_id = cursor.baseline_id
invalid = observation(site, {}, now + timedelta(seconds=780), now + timedelta(seconds=780))
assert record_observation(site, invalid).status == 'unavailable'
assert ReconciliationState.objects.get(site_id='account').baseline_id == baseline_id
frozen = account_result(1338, 312)
frozen['accounting']['frozen'] = '5'
assert not observation(site, frozen, now, now).get('complete')
assert account(1337, 313, 840).status == 'normal'
# Out-of-order responses are ignored, even if complete.
before = ReconciliationSnapshot.objects.count()
assert account(99999, 900, 800) is None
assert ReconciliationSnapshot.objects.count() == before
assert account(1337, 0, 900).status == 'reset'
changed = {**site, 'balanceMultiplier': 2}
assert account(1337, 0, 960, changed).status == 'baseline'

# Key-only scope: normalize consumption to balance scale, reject partial totals.
key_site = {**site, 'id': 'keys', 'name': 'Keys', 'siteType': 'sub2', 'balanceDivisor': 1,
            'usageKeys': [{'id': 'one', 'token': 'sk-secret'}]}
def key_result(b, u, day='2026-11-13', failed=0):
    return {'balanceField': 'balance', 'balanceDivisor': 1, 'balanceMultiplier': 1,
            'accounting': {'balance': str(b), 'frozen': '0'},
            'todayUsage': {'source': 'keys', 'date': day, 'cost': u, 'successCount': 1, 'failedCount': failed}}
def key_sample(b, u, t, day='2026-11-13', failed=0):
    return record_observation(key_site, observation(key_site, key_result(b, u, day, failed), t, t))
assert key_sample(100, 10, now).status == 'baseline'
assert key_sample(90, 20, now + timedelta(minutes=1), failed=1).status == 'unavailable'
assert key_sample(80, 30, now + timedelta(minutes=2)).status == 'normal'
# Midnight: use full prior day minus baseline's already-accounted consumption.
next_day = datetime(2026, 11, 14, 0, tzinfo=timezone.utc)
with patch.object(balance, 'get_key_usage_for_day', return_value={
        'source': 'keys', 'date': '2026-11-13', 'cost': 40, 'successCount': 1, 'failedCount': 1}):
    assert key_sample(65, 5, next_day, '2026-11-14').status == 'unavailable'
with patch.object(balance, 'get_key_usage_for_day', return_value={
        'source': 'keys', 'date': '2026-11-13', 'cost': 40, 'successCount': 1, 'failedCount': 0}):
    cross_day = key_sample(65, 5, next_day + timedelta(minutes=1), '2026-11-14')
    assert cross_day.status == 'normal' and cross_day.interval_usage == 15
    assert Decimal(cross_day.evidence['dailyTotals']['2026-11-13']) == 40
assert key_sample(60, 10, next_day + timedelta(minutes=2), '2026-11-14').status == 'normal'
scaled = observation(key_site, {**key_result(100, 10), 'balanceDivisor': 10, 'balanceMultiplier': 20}, now, now)
assert scaled['balance'] == 200 and scaled['consumption'] == 20
assert not observation(key_site, key_result(100, 10), now - timedelta(seconds=121), now)['complete']
assert not observation(key_site, key_result(100, 10, '2026-11-12'), now, now)['complete']

# Configuration changes cancel unresolved events without counting money.
key_sample(50, 10, next_day + timedelta(minutes=3), '2026-11-14')
new_keys = {**key_site, 'usageKeys': [{'id': 'new', 'token': 'sk-new-secret'}]}
t = next_day + timedelta(minutes=4)
assert record_observation(new_keys, observation(new_keys, key_result(50, 10, '2026-11-14'), t, t)).status == 'baseline'
assert ReconciliationEvent.objects.filter(site_id='keys', status='cancelled').exists()
reset_baseline('keys')
assert ReconciliationState.objects.get(site_id='keys').baseline_id is None

# The public endpoint requires a session; reads do not query upstream and totals
# are independent of the status filter. Notes leave financial results unchanged.
state.saved_sites = [changed, new_keys, {**site, 'id': 'no-data'}]
factory = RequestFactory()
request = factory.get('/api/reconciliation')
request.app_session = None
assert views.api_reconciliation(request).status_code == 401
request.app_session = {'token': 'test'}
with patch.object(balance, 'get_key_usage_for_day') as upstream:
    data = read_history({'status': 'attention', 'start': '2026-11-13', 'end': '2026-11-13'})
    upstream.assert_not_called()
assert data['totals'][0]['recharge'] == 500 and data['totals'][0]['anomaly'] == 50
assert data['unavailableSiteCount'] >= 1
assert all(r['status'] in ('pending', 'anomaly', 'unavailable') for r in data['records'])
response = views.api_reconciliation(request)
assert response.status_code == 200
assert b'secret-console-token' not in response.content and b'sk-secret' not in response.content
request = factory.patch('/api/reconciliation/1', data={'note': 'confirmed by operator', 'resolution': 'adjustment'}, content_type='application/json')
request.app_session = {'token': 'test'}
assert views.api_reconciliation_detail(request, confirmed.id).status_code == 200
confirmed.refresh_from_db()
assert confirmed.note == 'confirmed by operator' and confirmed.difference == 500 and confirmed.status == 'recharge'
for params in ({'status': 'wrong'}, {'start': 'invalid'}, {'start': '2026-11-14', 'end': '2026-11-13'}, {'page': 'x'}):
    request = factory.get('/api/reconciliation', params)
    request.app_session = {'token': 'test'}
    assert views.api_reconciliation(request).status_code == 400

# The shared collection wrapper persists actual failures without leaking errors
# and excludes unsaved/changed previews. Balance parse failure is not zero.
state.saved_sites = [site]
with patch.object(balance, 'get_balance', return_value=account_result(100, 1)):
    collect_check(site, lambda cfg: account_result(100, 1))
before = ReconciliationSnapshot.objects.count()
try:
    collect_check(site, lambda cfg: (_ for _ in ()).throw(ValueError('secret-upstream-error')))
except ValueError:
    pass
assert ReconciliationSnapshot.objects.count() == before + 1
assert 'secret-upstream-error' not in ReconciliationSnapshot.objects.latest('id').reason
collect_check({**site, 'id': 'unsaved'}, lambda cfg: account_result(10, 1))
assert ReconciliationSnapshot.objects.count() == before + 1
for value in (None, True, 'NaN', 'Infinity'):
    with patch.object(balance, 'fetch_json', return_value={'data': {'quota': value}}):
        try:
            balance.get_balance(site)
            raise AssertionError('Invalid balance accepted')
        except ValueError:
            pass
with patch.object(balance, 'fetch_json', return_value={'data': {'quota': 0, 'used_quota': 0}}):
    result = balance.get_balance(site)
    assert result['balance'] == 0
    assert observation(site, result, now, now)['complete']

# All three manual collection paths use the same persisted ledger, including
# JSON integers paired with values loaded from ORM FloatFields.
manual = {**site, 'id': 'manual'}
state.saved_sites = [{**manual, 'balanceDivisor': 500000.0, 'balanceMultiplier': 1.0}]
for path, body, view in [('/api/check', manual, views.api_check),
                         ('/api/check-many', {'sites': [manual]}, views.api_check_many),
                         ('/api/reconciliation', {'action': 'check', 'siteId': 'manual'}, views.api_reconciliation)]:
    request = factory.post(path, data=body, content_type='application/json')
    request.app_session = {'token': 'test'}
    with patch.object(views, 'check_and_notify', return_value=account_result(100, 10)):
        response = view(request)
    assert response.status_code == 200, response.content
assert ReconciliationSnapshot.objects.filter(site_id='manual').count() == 3

# A saved config change while the historical request is in flight cannot commit
# a response under the old configuration.
state.saved_sites = [key_site]
def changed_during_query(*args):
    state.saved_sites = [{**key_site, 'balanceMultiplier': 2}]
    return {'source': 'keys', 'date': '2026-11-14', 'cost': 10, 'successCount': 1, 'failedCount': 0}
t = next_day + timedelta(minutes=6)
record_observation(key_site, observation(key_site, key_result(50, 10, '2026-11-14'), t, t))
count = ReconciliationSnapshot.objects.count()
with patch.object(balance, 'get_key_usage_for_day', side_effect=changed_during_query):
    t += timedelta(days=1)
    assert record_observation(key_site, observation(key_site, key_result(50, 0, '2026-11-15'), t, t), require_saved=True) is None
assert ReconciliationSnapshot.objects.count() == count

# Pagination and currency totals include all matching records, never only page 1.
usd = {**site, 'id': 'usd', 'currency': 'USD'}
account(100, 0, 0, usd)
account(125, 0, 60, usd)
account(125, 0, 120, usd)
for seconds in range(180, 180 + 51):
    account(125, 0, seconds, usd)
history = read_history({'siteId': 'usd', 'page': '2'})
assert history['page'] == 2 and history['count'] == 53 and len(history['records']) == 3
assert history['totals'] == [{'currency': 'USD', 'recharge': Decimal(25), 'anomaly': Decimal(0)}]

# Pending reviews shorten a 300-second schedule to 60 seconds and complete
# through the same collection path, with all notification/network calls mocked.
from monitor import scheduler
import time
scheduled = {**site, 'id': 'scheduled', 'intervalSeconds': 300}
t = datetime.now(timezone.utc) - timedelta(seconds=121)
record_observation(scheduled, observation(scheduled, account_result(100, 10), t, t))
t += timedelta(seconds=60)
record_observation(scheduled, observation(scheduled, account_result(150, 10), t, t))
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
     patch('monitor.storage.load_sites', return_value=([scheduled], '')), \
     patch('monitor.storage.load_upstream_sessions', return_value={}), \
     patch('monitor.storage.load_daily_usage', return_value={}), \
     patch('monitor.storage.load_daily_report_webhook', return_value=''), \
     patch.object(balance, 'check_and_notify', return_value=account_result(150, 10)) as check:
    scheduler.start()
    state.scheduled_last_run['scheduled'] = time.time() - 61
    jobs['balance_tick']()
    check.assert_called_once()
    scheduler.stop()
assert ReconciliationEvent.objects.filter(site_id='scheduled', status='recharge', difference=50).exists()
print('reconciliation scenarios passed')
'''
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, 'DJANGO_SETTINGS_MODULE': 'balance_watch.settings',
                   'DATA_DIR': directory, 'APP_PASSWORD': 'isolated-test-password',
                   'DJANGO_SECRET_KEY': 'isolated-test-secret', 'PYTHONIOENCODING': 'utf-8'}
            result = subprocess.run([sys.executable, '-c', script], env=env,
                                    cwd=Path(__file__).resolve().parents[1],
                                    capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
