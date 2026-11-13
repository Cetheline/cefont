"""Render README illustrations from real CeFont output; never install fonts.

Run from the repository root: python3 -B docs/assets/render_specimens.py
Requires readable Noto Sans, Noto Serif and Noto Sans Mono regular font files.
The renderer and standard library are the only dependencies.
"""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from cefont.core.freetype_backend import extract_font_metadata
from cefont.core.orthography import analyze_font_orthographies
from cefont.core.renderer import encode_png, render_glyph_bitmap, render_text_bitmap

WIDTH = 1120
PALETTES = {
    'dark': dict(bg=(17, 28, 24), fg=(229, 237, 232), accent=(116, 184, 150), muted=(163, 183, 172), line=(58, 79, 67)),
    'light': dict(bg=(246, 249, 247), fg=(28, 44, 36), accent=(44, 111, 76), muted=(80, 101, 90), line=(199, 216, 206)),
}


class Panel:
    def __init__(self, height, palette, fonts):
        self.height, self.palette, self.fonts = height, palette, fonts
        self.pixels = bytearray(palette['bg']) * (WIDTH * height)

    def paste(self, raster, left, top):
        width, height, pixels = raster[:3]
        assert left >= 0 and top >= 0
        assert left + width <= WIDTH and top + height <= self.height, (left, top, width, height)
        for row in range(height):
            destination = ((top + row) * WIDTH + left) * 3
            self.pixels[destination:destination + width * 3] = pixels[row * width * 3:(row + 1) * width * 3]

    def text(self, value, x, y, size=20, color='fg', font='mono'):
        self.paste(render_text_bitmap(str(self.fonts[font]), value, pixel_size=size,
                                     fg_color=self.palette[color], bg_color=self.palette['bg'], padding=0), x, y)

    def rule(self, y, left=48, right=1072):
        start = (y * WIDTH + left) * 3
        self.pixels[start:start + (right - left) * 3] = bytearray(self.palette['line']) * (right - left)

    def title(self, title, subtitle):
        self.text(title, 48, 22, 34)
        self.text(subtitle, 48, 78, 17, 'muted')

    def save(self, directory, name, theme):
        path = directory / ('cefont-%s-%s.png' % (name, theme))
        path.write_bytes(encode_png(WIDTH, self.height, self.pixels))
        print(path.name, path.stat().st_size, 'bytes')


def render(output, fonts):
    metadata = extract_font_metadata(str(fonts['mono']))
    coverage = analyze_font_orthographies(str(fonts['mono']))['results']
    selected = []
    for name in ('Basic Latin', 'Basic Greek', 'Basic Cyrillic', 'Afrikaans'):
        found = next((row for row in coverage if row['name'] == name), None)
        if found:
            selected.append(found)
    assert selected, 'No matching reference definitions found'
    for theme, palette in PALETTES.items():
        panel = Panel(650, palette, fonts)
        panel.title('One font. Every scale.', 'Noto Sans Mono / actual FreeType raster output')
        y = 140
        for size in (16, 24, 36, 54, 80):
            panel.text('%02d px' % size, 48, y + 6, 16, 'muted')
            panel.text('Aa 0O Il1 {} =>', 176, y, size, 'accent' if size == 80 else 'fg')
            y += int(size * 1.35) + 21
        panel.text('Look for spacing, punctuation and ambiguous characters.', 48, 586, 17, 'muted')
        panel.save(output, 'waterfall', theme)

        panel = Panel(570, palette, fonts)
        panel.title('Same text. Different character.', 'Three real typefaces, rendered through the same pipeline')
        for index, (name, font) in enumerate((('Noto Sans Mono', 'mono'), ('Noto Sans', 'sans'), ('Noto Serif', 'serif'))):
            y = 134 + index * 132
            panel.text(name, 48, y, 17, 'muted')
            panel.text('0O Il1  {} []  => !=', 48, y + 36, 44, 'accent' if font == 'mono' else 'fg', font)
            if index < 2:
                panel.rule(y + 111)
        panel.save(output, 'comparison', theme)

        panel = Panel(690, palette, fonts)
        panel.title('A closer look at the alphabet.', 'Noto Sans Mono / U+0020 through U+007E')
        for number, codepoint in enumerate(range(0x20, 0x7f)):
            column, row = number % 16, number // 16
            x, y = 48 + column * 64, 150 + row * 80
            panel.text(chr(codepoint), x + 14, y, 30, 'accent' if chr(codepoint) in '{}[]0O1Il' else 'fg')
            panel.text('%04X' % codepoint, x, y + 43, 12, 'muted')
        panel.save(output, 'charmap', theme)

        panel = Panel(530, palette, fonts)
        panel.title('Go beyond the family name.', 'Live metadata read from the Noto Sans Mono specimen')
        panel.paste(render_glyph_bitmap(str(fonts['mono']), ord('g'), pixel_size=220,
                                        fg_color=palette['accent'], bg_color=palette['bg'], padding=8), 80, 175)
        entries = [('FAMILY', metadata['family']), ('STYLE / FORMAT', metadata['style'] + ' / ' + metadata['filetype']),
                   ('VENDOR', metadata['vendor']), ('GLYPHS', str(metadata['n-glyphs'])),
                   ('PANOSE', ' '.join(str(value) for value in metadata['panose']))]
        for index, (label, value) in enumerate(entries):
            y = 140 + index * 69
            panel.text(label, 388, y, 13, 'muted')
            panel.text(value, 388, y + 24, 22)
        panel.save(output, 'metadata', theme)

        panel = Panel(500, palette, fonts)
        panel.title('Does it speak your language?', 'Measured coverage of the Noto Sans Mono specimen')
        panel.text('Definition', 48, 139, 17, 'muted')
        panel.text('Present / required', 570, 139, 17, 'muted')
        panel.text('Coverage', 892, 139, 17, 'muted')
        for index, result in enumerate(selected):
            y = 195 + index * 61
            panel.text(result['name'], 48, y, 23)
            panel.text('%d / %d' % (result['present_count'], result['required_count']), 570, y, 23)
            panel.text('%.1f%%' % result['coverage'], 892, y, 23, 'accent')
        panel.text('Reference definitions measure codepoints, not shaping quality.', 48, 444, 16, 'muted')
        panel.save(output, 'coverage', theme)

        panel = Panel(374, palette, fonts)
        panel.title('From file to terminal.', 'Family lookup uses the index; file paths can render directly.')
        for x, title, line1, line2 in ((48, 'Font files', 'TTF / OTF / TTC', 'Read face metadata'),
                                        (418, 'SQLite index', 'Path + face index', 'Content-aware cache'),
                                        (794, 'Preview', 'FreeType raster', 'Terminal encoders')):
            panel.text(title, x, 175, 29, 'accent')
            panel.text(line1, x, 236, 15)
            panel.text(line2, x, 268, 15, 'muted')
        panel.text('>', 342, 178, 30, 'muted')
        panel.text('>', 718, 178, 30, 'muted')
        panel.save(output, 'pipeline', theme)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--font-root', type=Path, default=Path('/usr/share/fonts'))
    parser.add_argument('--output', type=Path, default=Path(__file__).parent)
    arguments = parser.parse_args()
    fonts = {}
    for key, filename in (('mono', 'NotoSansMono-Regular.ttf'), ('sans', 'NotoSans-Regular.ttf'), ('serif', 'NotoSerif-Regular.ttf')):
        match = next((path for path in arguments.font_root.rglob(filename) if path.is_file()), None)
        if match is None:
            parser.error('Required readable specimen font not found: ' + filename)
        fonts[key] = match
    arguments.output.mkdir(parents=True, exist_ok=True)
    render(arguments.output, fonts)


if __name__ == '__main__':
    main()
