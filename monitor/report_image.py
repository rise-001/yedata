"""Render the daily report as a compact spreadsheet-style PNG."""
from io import BytesIO
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def _font(size: int) -> ImageFont.FreeTypeFont:
    configured = os.environ.get('REPORT_FONT_PATH')
    candidates = [configured] if configured else [
        '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
        str(Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / 'simsun.ttc'),
        str(Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / 'msyh.ttc'),
        '/System/Library/Fonts/PingFang.ttc',
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    raise ValueError('晨报图片缺少中文字体，请安装 fonts-wqy-zenhei 或设置 REPORT_FONT_PATH')


def render_daily_report(*, date: str, balance_total: str, usage_total: str,
                        rows: list[tuple[str, str, str]], notes: list[str] | None = None,
                        title: str = '数据中心晨报') -> bytes:
    """Keep amounts intact and wrap long names/notes instead of clipping cells."""
    width = 720
    edges = (0, 300, 510, width - 1)
    padding = 14
    font, title_font, note_font = _font(26), _font(28), _font(21)
    measure = ImageDraw.Draw(Image.new('RGB', (1, 1)))

    def wrap(text, cell_width, face):
        text = ' '.join(str(text).split())
        lines, line = [], ''
        for char in text:
            if line and measure.textlength(line + char, font=face) > cell_width - 2 * padding:
                lines.append(line)
                line = ''
            line += char
        return lines + [line]

    # Each cell specifies its left/right edges and horizontal alignment.
    layout = []

    def add_row(cells, face=font):
        prepared = [(left, right, wrap(text, right - left, face), align)
                    for left, right, text, align in cells]
        line_height = face.size + 8
        height = max(len(lines) for _, _, lines, _ in prepared) * line_height + 14
        layout.append((prepared, face, line_height, height))

    add_row([(0, width - 1, title, 'center')], title_font)
    for label, value in [('日期', date), ('总余额', balance_total), ('总消耗', usage_total)]:
        add_row([(0, edges[1], label, 'left'), (edges[1], width - 1, value, 'center')])
    add_row([(edges[index], edges[index + 1], label, 'left' if index == 0 else 'center')
             for index, label in enumerate(('站点', '余额', '消耗'))])
    for row in rows:
        add_row([(edges[index], edges[index + 1], value, 'left' if index == 0 else 'center')
                 for index, value in enumerate(row)])

    note_lines = [line for note in notes or [] for line in wrap(note, width, note_font)]
    table_height = sum(item[3] for item in layout)
    note_height = len(note_lines) * (note_font.size + 9) + 20 if note_lines else 0
    image = Image.new('RGB', (width, table_height + note_height + 1), 'white')
    draw = ImageDraw.Draw(image)
    grid_color = '#d9d9d9'
    y = 0
    for cells, face, line_height, height in layout:
        for left, right, lines, align in cells:
            draw.rectangle((left, y, right, y + height), outline=grid_color)
            for index, line in enumerate(lines):
                text_width = draw.textlength(line, font=face)
                x = (left + right - text_width) / 2 if align == 'center' else (
                    right - padding - text_width if align == 'right' else left + padding)
                # Anchor by visible glyph bounds so Chinese and Latin align vertically.
                _, top, _, bottom = draw.textbbox((0, 0), line, font=face)
                text_y = y + 7 + index * line_height + (line_height - bottom + top) / 2 - top
                draw.text((x, text_y), line, font=face, fill='#111111')
        y += height
    for index, line in enumerate(note_lines):
        top = draw.textbbox((0, 0), line, font=note_font)[1]
        draw.text((padding, y + 10 + index * (note_font.size + 9) - top),
                  line, font=note_font, fill='#555555')
    output = BytesIO()
    # A small grayscale palette keeps long reports below WeCom's 2 MB limit
    # while preserving antialiased text at its original resolution.
    image.convert('L').quantize(colors=16).save(output, format='PNG', optimize=True, bits=4)
    return output.getvalue()
