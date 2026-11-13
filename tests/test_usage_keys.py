import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from monitor.adapters import get_usage_keys, public_site, validate_site_list, with_saved_secrets


SAVED = {'id': 'alpha', 'siteType': 'sub2', 'usageKeys': [
    {'id': 'one', 'name': '生产', 'token': 'sk-first'},
    {'id': 'two', 'name': '测试', 'token': 'sk-second'},
]}


class UsageKeysTests(unittest.TestCase):
    def test_public_configuration_only_returns_metadata(self):
        public = public_site(SAVED)
        self.assertEqual(public['usageKeys'], [
            {'id': 'one', 'name': '生产', 'hasToken': True},
            {'id': 'two', 'name': '测试', 'hasToken': True},
        ])
        self.assertTrue(public['hasUsageToken'])
        self.assertNotIn('sk-', str(public))

    def test_saved_secrets_follow_key_id_through_reorder_edit_and_removal(self):
        incoming = {'id': 'alpha', 'usageKeys': [
            {'id': 'two', 'name': '重命名', 'token': ''},
            {'id': 'new', 'name': '新 Key', 'token': 'Bearer sk-new'},
        ]}
        merged = with_saved_secrets(incoming, [SAVED])
        self.assertEqual(merged['usageKeys'], [
            {'id': 'two', 'name': '重命名', 'token': 'sk-second'},
            {'id': 'new', 'name': '新 Key', 'token': 'sk-new'},
        ])
        self.assertEqual(with_saved_secrets({'id': 'alpha', 'usageKeys': []}, [SAVED])['usageKeys'], [])
        self.assertEqual(with_saved_secrets({'id': 'alpha'}, [SAVED])['usageKeys'], SAVED['usageKeys'])

    def test_rejects_invalid_key_lists_and_duplicate_tokens(self):
        for keys in ({}, [None], [{'id': ''}], [{'id': 'same'}, {'id': 'same'}],
                     [{'id': str(i)} for i in range(21)],
                     [{'id': 'a', 'token': 'sk-same'}, {'id': 'b', 'token': 'Bearer sk-same'}]):
            with self.subTest(keys=keys), self.assertRaises(ValueError):
                validate_site_list([{'id': 'alpha', 'usageKeys': keys}])
        with self.assertRaises(ValueError):
            get_usage_keys({'usageKeys': [{'id': 'a', 'token': ''}]}, require_tokens=True)

    def test_migration_and_database_save_reload_and_delete_in_isolated_database(self):
        script = '''
import sys
sys.argv = ['manage.py', 'test']
import django
django.setup()
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from monitor.adapters import public_site, with_saved_secrets
from monitor.storage import load_sites, save_sites

executor = MigrationExecutor(connection)
executor.migrate([('monitor', '0006_daily_report')])
old_apps = executor.loader.project_state([('monitor', '0006_daily_report')]).apps
old_apps.get_model('monitor', 'Site').objects.create(
    site_id='legacy', site_type='newapi', usage_token='sk-legacy', base_url='https://example.com')
MigrationExecutor(connection).migrate([('monitor', '0007_site_usage_keys')])
# Runtime models require the current schema after validating the legacy migration path.
executor = MigrationExecutor(connection)
executor.migrate(executor.loader.graph.leaf_nodes())
saved, _ = load_sites()
assert saved[0]['usageKeys'] == [{'id': 'default', 'name': '\u9ed8\u8ba4 Key', 'token': 'sk-legacy'}]
public = public_site(saved[0])
assert 'sk-legacy' not in str(public)
public['usageKeys'].append({'id': 'new', 'name': 'New', 'token': 'sk-new'})
saved, _ = save_sites([with_saved_secrets(public, saved)])
assert [key['token'] for key in load_sites()[0][0]['usageKeys']] == ['sk-legacy', 'sk-new']
public = public_site(saved[0])
public['usageKeys'] = [public['usageKeys'][1]]
save_sites([with_saved_secrets(public, saved)])
saved, _ = load_sites()
assert saved[0]['usageKeys'] == [{'id': 'new', 'name': 'New', 'token': 'sk-new'}]
assert saved[0]['usageToken'] == ''
public = public_site(saved[0])
public['usageKeys'] = []
save_sites([with_saved_secrets(public, saved)])
assert load_sites()[0][0]['usageKeys'] == []
assert not public_site(load_sites()[0][0])['hasUsageToken']
try:
    save_sites([{'id': 'legacy', 'name': 'should roll back', 'usageKeys': [{'id': 'empty'}]}])
except ValueError:
    pass
else:
    raise AssertionError('Missing key was accepted')
assert load_sites()[0][0]['name'] != 'should roll back'
'''
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, 'DJANGO_SETTINGS_MODULE': 'balance_watch.settings',
                   'DATA_DIR': directory, 'APP_PASSWORD': 'isolated-test-password',
                   'DJANGO_SECRET_KEY': 'isolated-test-secret'}
            result = subprocess.run([sys.executable, '-c', script], env=env,
                                    cwd=Path(__file__).resolve().parents[1],
                                    capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
