import os
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import requests

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'balance_watch.settings')

from django.test import Client
from monitor import balance, state


class WeeklyUsageTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 5, 0, tzinfo=timezone.utc)
        self.site = {'id': 'alpha', 'siteType': 'sub2', 'baseUrl': 'https://example.com',
                     'timezone': 'Asia/Shanghai', 'currency': 'CNY',
                     'token': 'console-secret', 'newApiUserId': '7',
                     'usageKeys': [{'id': 'one', 'name': 'One', 'token': 'sk-one'},
                                   {'id': 'two', 'name': 'Two', 'token': 'sk-two'}]}

    def test_sub2_fetches_once_per_key_and_keeps_today_separate(self):
        def fetch(url, **kwargs):
            self.assertEqual(parse_qs(urlsplit(url).query),
                             {'days': ['8'], 'timezone': ['Asia/Shanghai']})
            return {'data': {'usage': {'today': {'actual_cost': 2}}, 'daily_usage': [
                {'date': '2026-10-30', 'actual_cost': 1.5},
                {'date': '2026-11-04', 'actual_cost': 0},
                {'date': '2026-11-05', 'actual_cost': 999},
                {'date': '2026-10-29', 'actual_cost': 2.5},
                {'date': '2026-10-28', 'actual_cost': 999},
            ]}}
        with patch.object(balance, 'fetch_json', side_effect=fetch) as upstream:
            days = balance.get_weekly_key_usage(self.site, self.now)
        self.assertEqual(upstream.call_count, 2)
        self.assertEqual([day['date'] for day in days],
                         ['2026-10-29', '2026-10-30', '2026-10-31', '2026-11-01', '2026-11-02',
                          '2026-11-03', '2026-11-04', '2026-11-05'])
        self.assertEqual([day['cost'] for day in days], [5, 3, 0, 0, 0, 0, 0, 4])
        self.assertTrue(all(day['successCount'] == 2 for day in days))

    def test_missing_history_keeps_today_and_timeout_never_becomes_zero(self):
        def fetch(url, **kwargs):
            if kwargs['headers']['Authorization'] == 'Bearer sk-two':
                raise requests.Timeout()
            return {'usage': {'today': {'actual_cost': 0}}}
        with patch.object(balance, 'fetch_json', side_effect=fetch):
            days = balance.get_weekly_key_usage(self.site, self.now)
        self.assertTrue(all(day['cost'] is None and day['failedCount'] == 2 for day in days[:-1]))
        self.assertEqual(days[-1]['cost'], 0)
        self.assertEqual(days[-1]['failedCount'], 1)

    def test_newapi_queries_eight_local_ranges_and_resolves_each_name_once(self):
        ranges = []
        def fetch(url, **kwargs):
            if urlsplit(url).path == '/api/usage/token':
                return {'data': {'name': kwargs['headers']['Authorization'][7:]}}
            query = parse_qs(urlsplit(url).query)
            self.assertEqual(kwargs['headers'],
                             {'Authorization': 'Bearer console-secret', 'New-Api-User': '7'})
            ranges.append((int(query['start_timestamp'][0]), int(query['end_timestamp'][0])))
            return {'success': True, 'data': {'quota': 625000}}
        with patch.object(balance, 'fetch_json', side_effect=fetch) as upstream:
            days = balance.get_weekly_key_usage({**self.site, 'siteType': 'newapi'}, self.now)
        self.assertEqual(upstream.call_count, 18)
        self.assertEqual(len(set(ranges)), 8)
        self.assertEqual(min(start for start, _ in ranges),
                         int(datetime(2026, 10, 28, 16, tzinfo=timezone.utc).timestamp()))
        self.assertEqual(max(end for _, end in ranges), int(self.now.timestamp()))
        self.assertTrue(all(day['cost'] == 2.5 for day in days))
        self.assertTrue(all(end - start == 86399 for start, end in ranges if end != int(self.now.timestamp())))

    def test_duplicate_names_and_unconfigured_sites_make_no_daily_queries(self):
        for site in ({**self.site, 'siteType': 'newapi'}, {**self.site, 'usageKeys': []}):
            with self.subTest(site=site), patch.object(balance, 'fetch_json', return_value={'name': 'same'}) as upstream:
                days = balance.get_weekly_key_usage(site, self.now)
            self.assertTrue(all(day['cost'] is None for day in days))
            self.assertEqual(upstream.call_count, len(site['usageKeys']))

    def test_refresh_queries_only_requested_day(self):
        def fetch(url, **kwargs):
            if urlsplit(url).path == '/api/usage/token':
                return {'data': {'name': kwargs['headers']['Authorization'][7:]}}
            query = parse_qs(urlsplit(url).query)
            self.assertEqual(int(query['end_timestamp'][0]), int(self.now.timestamp()))
            return {'success': True, 'data': {'quota': 500000}}
        with patch.object(balance, 'fetch_json', side_effect=fetch) as upstream:
            days = balance.get_weekly_key_usage({**self.site, 'siteType': 'newapi'}, self.now,
                                              requested_days=['2026-11-05'])
        self.assertEqual(upstream.call_count, 4)
        self.assertEqual(days, [{'date': '2026-11-05', 'cost': 2,
                                 'successCount': 2, 'failedCount': 0}])

    def test_endpoint_reads_database_and_only_post_refreshes_enabled_sites(self):
        sites = [self.site, {**self.site, 'id': 'beta'},
                 {**self.site, 'id': 'usd', 'currency': 'USD'},
                 {**self.site, 'id': 'utc', 'timezone': 'UTC'},
                 {**self.site, 'id': 'disabled', 'enabled': False}]
        client = Client()
        client.cookies['balance_session'] = 'weekly-test-session'
        with patch.object(state, 'saved_sites', sites), patch.dict(
            state.sessions, {'weekly-test-session': {'expires_at': time.time() + 60}}, clear=True
        ), patch('monitor.views.read_usage_history', return_value={'series': [], 'source': 'database'}) as read, \
                patch('monitor.views.refresh_usage_history') as refresh:
            response = client.get('/api/usage/history')
            refresh.assert_not_called()
            self.assertEqual(client.post('/api/usage/history').status_code, 200)
            self.assertEqual(client.put('/api/usage/history').status_code, 405)
            refresh.assert_called_once_with(sites[:-1], force=True)
            self.assertEqual(read.call_count, 2)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['data']['source'], 'database')
        self.assertNotIn('secret', response.content.decode())
        self.assertNotIn('sk-', response.content.decode())
        self.assertEqual(Client().get('/api/usage/history').status_code, 401)

