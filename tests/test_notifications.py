import base64
import hashlib
from io import BytesIO
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
from PIL import Image

from monitor import balance, scheduler, state


SITE = {
    'id': 'alpha', 'name': 'Alpha', 'baseUrl': 'https://alpha.example.com',
    'email': 'shared@example.com', 'threshold': 100, 'timezone': 'Asia/Shanghai',
}
RESULT = {
    'balance': 10, 'currency': 'CNY', 'displayName': 'Account',
    'email': 'shared@example.com', 'usage': {
        'date': '2026-11-04', 'source': 'keys', 'cost': 2, 'successCount': 1, 'failedCount': 0,
    },
}


class NotificationTests(unittest.TestCase):
    def setUp(self):
        for name in ('alerts', 'daily_report_sent', 'daily_report_last_attempt'):
            patcher = patch.dict(getattr(state, name), {}, clear=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        for target, value in (
            ('monitor.state.global_webhook', 'https://example.com/webhook?key=secret'),
            ('monitor.state.daily_report_webhook', 'https://example.com/report?key=report-secret'),
            ('monitor.state.saved_sites', [dict(SITE)]),
            ('monitor.scheduler.settings', SimpleNamespace(DAILY_REPORT_HOUR=8)),
        ):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.claim_report = patch('monitor.storage.claim_daily_report', return_value=True)
        self.release_report = patch('monitor.storage.release_daily_report_claim')
        self.claim_report.start()
        self.release_report.start()
        self.addCleanup(self.claim_report.stop)
        self.addCleanup(self.release_report.stop)

    def response(self, data=None, *, status=200):
        response = Mock(ok=status < 400, status_code=status)
        response.json.return_value = data
        return response

    def test_wecom_requires_explicit_success(self):
        for data in ({}, {'errmsg': 'ok'}, [], None, {'errcode': False}, {'errcode': '0'}):
            with self.subTest(data=data):
                with patch.object(balance.requests, 'post', return_value=self.response(data)):
                    with self.assertRaisesRegex(ValueError, 'errcode'):
                        balance.send_wecom(state.global_webhook, 'test')
        with patch.object(balance.requests, 'post', return_value=self.response({'errcode': 0})) as post:
            balance.send_wecom(state.global_webhook, 'test')
            self.assertFalse(post.call_args.kwargs['allow_redirects'])

    def test_invalid_json_and_http_errors_are_failures(self):
        response = self.response()
        response.json.side_effect = ValueError('HTML instead of JSON')
        with patch.object(balance.requests, 'post', return_value=response):
            with self.assertRaisesRegex(ValueError, 'JSON'):
                balance.send_wecom(state.global_webhook, 'test')
        for status in (302, 500):
            with self.subTest(status=status):
                with patch.object(balance.requests, 'post', return_value=self.response({'errcode': 0}, status=status)):
                    with self.assertRaisesRegex(ValueError, str(status)):
                        balance.send_wecom(state.global_webhook, 'test')

    def test_wecom_error_code_is_visible(self):
        with patch.object(balance.requests, 'post', return_value=self.response({'errcode': 93000, 'errmsg': 'invalid webhook url'})):
            with self.assertRaisesRegex(ValueError, '93000.*invalid webhook url'):
                balance.send_wecom(state.global_webhook, 'test')

    def test_network_errors_do_not_expose_webhook_key(self):
        for error in (requests.Timeout(state.global_webhook), requests.ConnectionError(state.global_webhook)):
            with self.subTest(error=type(error).__name__):
                with patch.object(balance.requests, 'post', side_effect=error):
                    with self.assertRaises(ValueError) as caught:
                        balance.send_wecom(state.global_webhook, 'test')
                self.assertNotIn('key=secret', str(caught.exception))

    def test_image_payload_uses_png_bytes_and_matching_md5(self):
        png = balance.render_daily_report(date='2026/11/13', balance_total='43.00',
                                         usage_total='4.35', rows=[('apiling', '43.00', '4.35')])
        with patch.object(balance.requests, 'post', return_value=self.response({'errcode': 0})) as post:
            balance.send_wecom_image(state.daily_report_webhook, png)
        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['msgtype'], 'image')
        self.assertEqual(base64.b64decode(payload['image']['base64']), png)
        self.assertEqual(payload['image']['md5'], hashlib.md5(png, usedforsecurity=False).hexdigest())
        self.assertEqual(post.call_args.args[0], state.daily_report_webhook)
        self.assertFalse(post.call_args.kwargs['allow_redirects'])
        self.assertEqual(post.call_args.kwargs['timeout'], 10)

    def test_image_delivery_requires_success_and_rejects_oversized_payloads(self):
        for data in ({}, {'errcode': 40009, 'errmsg': 'invalid image size'}, {'errcode': False}):
            with self.subTest(data=data), patch.object(balance.requests, 'post', return_value=self.response(data)):
                with self.assertRaises(ValueError):
                    balance.send_wecom_image(state.daily_report_webhook, b'png')
        with patch.object(balance.requests, 'post') as post:
            for image in (b'', b'x' * (2 * 1024 * 1024 + 1)):
                with self.assertRaisesRegex(ValueError, '2 MB'):
                    balance.send_wecom_image(state.daily_report_webhook, image)
            post.assert_not_called()

    @patch.object(balance, 'get_balance', return_value=RESULT)
    @patch.object(balance, 'send_wecom')
    def test_same_email_on_different_sites_gets_independent_alerts(self, send, get):
        self.assertTrue(balance.check_and_notify(SITE)['notified'])
        self.assertTrue(balance.check_and_notify({**SITE, 'id': 'beta'})['notified'])
        again = balance.check_and_notify(SITE)
        self.assertFalse(again['notified'])
        self.assertIn('6 小时', again['notificationMessage'])
        self.assertEqual(send.call_count, 2)
        self.assertTrue(all(call.args[0] == state.global_webhook for call in send.call_args_list))

    @patch.object(balance, 'get_balance', return_value=RESULT)
    @patch.object(balance, 'send_wecom', side_effect=[ValueError('send failed'), None])
    def test_failed_alert_is_not_deduplicated(self, send, get):
        with self.assertLogs('monitor.balance', level='ERROR'):
            first = balance.check_and_notify(SITE)
        self.assertFalse(first['notified'])
        self.assertEqual(first['notificationError'], 'send failed')
        self.assertEqual(state.alerts, {})
        self.assertTrue(balance.check_and_notify(SITE)['notified'])

    @patch.object(balance, 'get_balance', return_value=RESULT)
    @patch.object(balance, 'send_wecom')
    def test_skipped_alert_explains_why(self, send, get):
        for overrides, reason in (
            ({'notifyEnabled': False}, '未启用'), ({'threshold': None}, '阈值'),
            ({'threshold': 'nan'}, '阈值'), ({'threshold': 'inf'}, '阈值'),
            ({'threshold': 1}, '高于'),
        ):
            with self.subTest(overrides=overrides):
                result = balance.check_and_notify({**SITE, **overrides})
                self.assertIn(reason, result['notificationMessage'])
                self.assertFalse(result['notified'])
        with patch.object(state, 'global_webhook', ''):
            self.assertIn('Webhook', balance.check_and_notify(SITE)['notificationMessage'])
        send.assert_not_called()

    @patch.object(balance, 'get_balance', return_value=RESULT)
    @patch.object(balance, 'record_daily_usage')
    @patch.object(balance, 'send_wecom_image')
    @patch.object(balance, 'day_key_of', return_value='2026-11-05')
    def test_daily_report_returns_delivery_result_and_filters_timezone(self, day, send, record, get):
        state.saved_sites.append({**SITE, 'id': 'beta', 'timezone': 'UTC'})
        self.assertTrue(balance.run_daily_report(timezone='Asia/Shanghai'))
        get.assert_called_once()
        self.assertEqual(get.call_args.args[0]['id'], 'alpha')
        self.assertEqual(send.call_args.args[0], state.daily_report_webhook)
        with Image.open(BytesIO(send.call_args.args[1])) as image:
            self.assertEqual(image.format, 'PNG')
            self.assertEqual(image.width, 720)
        self.assertEqual(get.call_args.kwargs, {'usage_day': '2026-11-04'})
        send.side_effect = ValueError('send failed')
        with self.assertLogs('monitor.balance', level='ERROR'):
            self.assertFalse(balance.run_daily_report(timezone='Asia/Shanghai'))
        send.reset_mock()
        self.assertFalse(balance.run_daily_report(timezone='Asia/Tokyo'))
        send.assert_not_called()

    @patch.object(balance, 'get_balance')
    @patch.object(balance, 'send_wecom_image')
    def test_daily_report_does_not_fall_back_to_alert_webhook(self, send, get):
        with patch.object(state, 'daily_report_webhook', ''):
            self.assertFalse(balance.run_daily_report())
        get.assert_not_called()
        send.assert_not_called()

    @patch.object(balance, 'get_balance', return_value=RESULT)
    @patch.object(balance, 'record_daily_usage')
    @patch.object(balance, 'send_wecom_image')
    @patch.object(balance, 'render_daily_report', side_effect=ValueError('missing font'))
    def test_image_render_failure_remains_retryable(self, render, send, record, get):
        with self.assertLogs('monitor.balance', level='ERROR'):
            self.assertFalse(balance.run_daily_report())
        send.assert_not_called()

    @patch('monitor.time_utils.local_parts', return_value={'year': '2026', 'month': '10', 'day': '27', 'hour': '09'})
    @patch.object(balance, 'run_daily_report', side_effect=[False, True])
    def test_daily_report_catches_up_and_retries_only_until_success(self, report, parts):
        scheduler._run_daily_reports(1000)
        self.assertEqual(state.daily_report_sent, {})
        scheduler._run_daily_reports(1030)
        self.assertEqual(report.call_count, 1)
        scheduler._run_daily_reports(1060)
        self.assertEqual(state.daily_report_sent, {'Asia/Shanghai': '2026-10-27'})
        scheduler._run_daily_reports(1200)
        self.assertEqual(report.call_count, 2)
        report.assert_called_with(timezone='Asia/Shanghai')

    @patch('monitor.time_utils.local_parts', return_value={'year': '2026', 'month': '10', 'day': '27', 'hour': '09'})
    @patch.object(balance, 'run_daily_report', side_effect=[ValueError('failed'), True])
    def test_unexpected_report_failure_is_retryable(self, report, parts):
        with self.assertLogs('monitor.scheduler', level='ERROR'):
            scheduler._run_daily_reports(1000)
        self.assertEqual(state.daily_report_sent, {})
        scheduler._run_daily_reports(1060)
        self.assertEqual(state.daily_report_sent, {'Asia/Shanghai': '2026-10-27'})

    @patch('monitor.time_utils.local_parts', return_value={'year': '2026', 'month': '10', 'day': '27', 'hour': '07'})
    @patch.object(balance, 'run_daily_report', return_value=True)
    def test_report_waits_for_configured_hour_and_webhook(self, report, parts):
        scheduler._run_daily_reports(1000)
        report.assert_not_called()
        parts.return_value['hour'] = '08'
        with patch.object(state, 'daily_report_webhook', ''):
            scheduler._run_daily_reports(1000)
        self.assertEqual(state.daily_report_last_attempt, {})
        with patch.object(state, 'global_webhook', ''):
            scheduler._run_daily_reports(1000)
        report.assert_called_once()

    @patch('monitor.time_utils.local_parts', return_value={'year': '2026', 'month': '10', 'day': '27', 'hour': '09'})
    @patch.object(balance, 'run_daily_report', return_value=True)
    def test_persistent_claim_suppresses_reports_sent_by_another_process(self, report, parts):
        self.claim_report.stop()
        claim = patch('monitor.storage.claim_daily_report', return_value=False)
        claim.start()
        self.addCleanup(claim.stop)
        scheduler._run_daily_reports(1000)
        report.assert_not_called()


if __name__ == '__main__':
    unittest.main()
