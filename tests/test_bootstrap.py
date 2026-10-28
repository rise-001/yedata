import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from balance_watch.bootstrap import CREDENTIAL_FIELDS, load_credentials


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data_dir = Path(self.temporary.name) / 'data'
        self.env = patch.dict(os.environ, {field: '' for field in CREDENTIAL_FIELDS})
        self.env.start()
        self.addCleanup(self.env.stop)

    def load(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            credentials = load_credentials(self.data_dir)
        return credentials, output.getvalue()

    def test_first_start_generates_and_persists_credentials(self):
        credentials, output = self.load()
        self.assertEqual(credentials['APP_USERNAME'], 'admin')
        self.assertGreaterEqual(len(credentials['APP_PASSWORD']), 32)
        self.assertGreaterEqual(len(credentials['DJANGO_SECRET_KEY']), 64)
        saved = json.loads((self.data_dir / 'bootstrap.json').read_text())
        self.assertEqual(credentials, saved)
        self.assertIn(credentials['APP_PASSWORD'], output)
        self.assertNotIn(credentials['DJANGO_SECRET_KEY'], output)
        if os.name == 'posix':
            self.assertEqual((self.data_dir / 'bootstrap.json').stat().st_mode & 0o777, 0o600)

    def test_restart_reuses_credentials_without_printing_password(self):
        original, _ = self.load()
        current, output = self.load()
        self.assertEqual(original, current)
        self.assertEqual(output, '')

    def test_environment_overrides_are_persisted(self):
        original, _ = self.load()
        with patch.dict(os.environ, {'APP_USERNAME': 'owner', 'APP_PASSWORD': 'custom-password'}):
            current, output = self.load()
        self.assertEqual(current['APP_USERNAME'], 'owner')
        self.assertEqual(current['APP_PASSWORD'], 'custom-password')
        self.assertEqual(current['DJANGO_SECRET_KEY'], original['DJANGO_SECRET_KEY'])
        self.assertEqual(output, '')
        restored, _ = self.load()
        self.assertEqual(current, restored)

    def test_explicit_credentials_are_used_on_first_start(self):
        configured = dict(zip(CREDENTIAL_FIELDS, ('owner', 'custom-password', 'custom-secret')))
        with patch.dict(os.environ, configured):
            current, output = self.load()
        self.assertEqual(current, configured)
        self.assertEqual(output, '')

    def test_independent_installations_have_different_secrets(self):
        first, _ = self.load()
        self.data_dir = Path(self.temporary.name) / 'another-installation'
        second, _ = self.load()
        self.assertNotEqual(first['APP_PASSWORD'], second['APP_PASSWORD'])
        self.assertNotEqual(first['DJANGO_SECRET_KEY'], second['DJANGO_SECRET_KEY'])

    def test_corrupt_credentials_are_not_silently_replaced(self):
        self.data_dir.mkdir()
        path = self.data_dir / 'bootstrap.json'
        for contents in ('{', '{}', '[]', '{"APP_PASSWORD": "old"}'):
            with self.subTest(contents=contents):
                path.write_text(contents)
                with self.assertRaises(RuntimeError):
                    self.load()
                self.assertEqual(path.read_text(), contents)

    def test_failed_write_preserves_previous_credentials(self):
        original, _ = self.load()
        with patch.dict(os.environ, {'APP_PASSWORD': 'replacement'}):
            with patch('balance_watch.bootstrap.os.replace', side_effect=OSError('disk error')):
                with self.assertRaises(OSError):
                    self.load()
        current, _ = self.load()
        self.assertEqual(current, original)
        self.assertEqual(list(self.data_dir.glob('.bootstrap-*')), [])

    def test_django_first_start_and_login_after_process_restart(self):
        script = '''
import hashlib
import json
import re
import django
django.setup()
from django.conf import settings
from django.core.management import call_command
from django.test import Client
call_command('check', verbosity=0)
client = Client()
assert client.get('/').status_code == 302
login_page = client.get('/login.html')
assert login_page.status_code == 200
assert b'id="root"' in login_page.content
assert client.get('/api/sites').status_code == 401
assert client.post('/api/login', data=json.dumps({
    'username': settings.APP_USERNAME, 'password': 'wrong-password'
}), content_type='application/json').json()['ok'] is False
response = client.post('/api/login', data=json.dumps({
    'username': settings.APP_USERNAME, 'password': settings.APP_PASSWORD
}), content_type='application/json')
assert response.json()['ok'] is True
# The application writes a raw Set-Cookie header; emulate browser cookie storage.
client.cookies.load(response['Set-Cookie'])
assert client.get('/api/sites').status_code == 200
index_page = client.get('/')
assert index_page.status_code == 200
assert 'no-store' in index_page['Cache-Control']
assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', index_page.content.decode())
assert assets
content_types = {client.get(path)['Content-Type'].split(';')[0] for path in assets}
assert 'text/javascript' in content_types
assert 'text/css' in content_types
assert client.get('/favicon.svg')['Content-Type'] == 'image/svg+xml'
assert client.get('/bootstrap.json').status_code == 404
assert client.get('/data/bootstrap.json').status_code == 404
print('FINGERPRINT:' + hashlib.sha256(
    (settings.APP_PASSWORD + settings.SECRET_KEY).encode()
).hexdigest())
'''
        env = dict(os.environ, DATA_DIR=str(self.data_dir),
                   DJANGO_SETTINGS_MODULE='balance_watch.settings')
        runs = [subprocess.run(
            [sys.executable, '-c', script], env=env, capture_output=True,
            text=True, encoding='utf-8', cwd=Path(__file__).resolve().parents[1],
            timeout=30,
        ) for _ in range(2)]
        for result in runs:
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Initial administrator credentials', runs[0].stdout)
        self.assertNotIn('Initial administrator credentials', runs[1].stdout)
        self.assertEqual(runs[0].stdout.split('FINGERPRINT:')[1],
                         runs[1].stdout.split('FINGERPRINT:')[1])
        self.assertTrue((self.data_dir / 'db.sqlite3').exists())


if __name__ == '__main__':
    unittest.main()
