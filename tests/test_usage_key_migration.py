import os
import unittest
from unittest.mock import patch

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'balance_watch.settings')

import django
with patch('monitor.scheduler.start'):
    django.setup()

from monitor.adapters import public_site


class UsageKeyMigrationTests(unittest.TestCase):
    def test_public_site_hides_misplaced_key_and_marks_it_saved(self):
        public = public_site({
            'id': 'alpha', 'siteType': 'newapi', 'usagePath': 'sk-exposed-example-key',
            'usageToken': '',
        })
        self.assertEqual(public['usagePath'], '')
        self.assertTrue(public['hasUsageToken'])
        self.assertNotIn('sk-exposed-example-key', str(public))


if __name__ == '__main__':
    unittest.main()
