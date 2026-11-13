from io import BytesIO
import os
import unittest
from unittest.mock import patch

from PIL import Image

from monitor.report_image import render_daily_report


class ReportImageTests(unittest.TestCase):
    def render(self, rows, **kwargs):
        return render_daily_report(date='2026/11/13', balance_total='440.00',
                                   usage_total='42.68', rows=rows, **kwargs)

    def test_image_is_valid_png_with_chinese_text_and_all_rows(self):
        rows = [('apiling', '43.00', '4.35'), ('中文站点', '354.00', '34.00'),
                ('站点三', '43.00', '4.33')]
        png = self.render(rows)
        with Image.open(BytesIO(png)) as image:
            self.assertEqual(image.format, 'PNG')
            self.assertEqual(image.width, 720)
            self.assertGreater(image.height, 350)
            self.assertLess(image.height, 500)
            image.load()
            darkest, lightest = image.convert('L').getextrema()
            self.assertLess(darkest, 100)
            self.assertEqual(lightest, 255)
        self.assertLess(len(png), 2 * 1024 * 1024)

    def test_long_names_notes_and_maximum_site_count_fit_in_wecom_image(self):
        rows = [(f'{index}号中文站点' * 10, '123456789.12', '未获取') for index in range(100)]
        notes = [f'{index}号：' + '查询失败，请稍后再试。' * 8 for index in range(100)]
        png = self.render(rows, notes=notes)
        short_png = self.render([('短名称', '1.00', '0.00')] * 100)
        with Image.open(BytesIO(png)) as image, Image.open(BytesIO(short_png)) as short:
            self.assertEqual(image.width, short.width)
            self.assertGreater(image.height, short.height)
            image.verify()
        self.assertLess(len(png), 2 * 1024 * 1024)

    def test_missing_font_is_reported_instead_of_sending_unreadable_chinese(self):
        with patch.dict(os.environ, {'REPORT_FONT_PATH': '/missing/report-font.ttf'}):
            with self.assertRaisesRegex(ValueError, 'REPORT_FONT_PATH'):
                self.render([])
