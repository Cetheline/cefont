"""Native dependency and raster tests using owned buffers, never invalid pointers."""
import contextlib
import ctypes
import io
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from cefont import main
from cefont.core import db, freetype_backend as ft, installer, renderer, terminal, terminal_tester


class DependencyTests(unittest.TestCase):
    def test_missing_freetype_is_an_explicit_operation_error(self):
        with patch.object(ft, '_ft', None):
            for operation, args in ((ft.extract_font_metadata, ('/fixture.ttf',)),
                                    (ft.get_all_codepoints, ('/fixture.ttf',)),
                                    (ft.render_glyph_ascii, ('/fixture.ttf', 0, 'A'))):
                with self.subTest(operation=operation.__name__), self.assertRaisesRegex(RuntimeError, 'FreeType'):
                    operation(*args)

    def test_renderer_rejects_missing_dependency_without_attribute_error(self):
        with patch.object(renderer, '_ft', None):
            for operation, args in ((renderer.render_text_bitmap, ('/fixture.ttf', 'A')),
                                    (renderer.render_glyph_bitmap, ('/fixture.ttf', 65))):
                with self.subTest(operation=operation.__name__), self.assertRaisesRegex(RuntimeError, 'FreeType'):
                    operation(*args)

    def test_missing_parser_does_not_install_with_fallback(self):
        with tempfile.TemporaryDirectory(prefix='cefont-no-ft-') as tmp:
            fixture = Path(tmp) / 'fixture.ttf'
            fixture.write_bytes(b'disposable')
            with patch.object(ft, '_ft', None), patch.object(installer, 'ensure_directories'), \
                 patch.object(installer, 'trigger_fontconfig_cache'), \
                 patch.object(installer, 'USER_FONT_DIR', str(Path(tmp) / 'managed')), \
                 patch.object(installer.shutil, 'copy2') as copy:
                operation = installer.FontInstaller(db=Mock())
                with self.assertRaisesRegex(RuntimeError, 'FreeType'):
                    operation.install_font(str(fixture))
                copy.assert_not_called()
                self.assertFalse((Path(tmp) / 'managed').exists())

    def test_help_stays_available_but_info_and_preview_fail_cleanly(self):
        for arguments in (['--help'], ['info', '/fixture.ttf'], ['preview', 'waterfall', '/fixture.ttf']):
            output, error = io.StringIO(), io.StringIO()
            with patch.object(ft, '_ft', None), patch.object(main.sys, 'argv', ['cefont'] + arguments), \
                 patch.object(main, 'get_detailed_font_info', side_effect=lambda *args: ft.extract_font_metadata('/fixture.ttf')), \
                 contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                with self.assertRaises(SystemExit) as exit:
                    main.main()
            self.assertEqual(exit.exception.code, 0 if arguments == ['--help'] else 1)
            if arguments != ['--help']:
                self.assertIn('FreeType', error.getvalue())
                self.assertNotIn('Traceback', error.getvalue())

    def test_failed_initialization_does_not_open_font_faces(self):
        library = Mock()
        library.FT_Init_FreeType.return_value = 7
        with patch.object(ft, '_ft', library), patch.object(ft, '_GLOBAL_FT', None):
            with self.assertRaisesRegex(RuntimeError, 'initialization failed'):
                ft.extract_font_metadata(__file__)
        library.FT_New_Face.assert_not_called()

    def test_waterfall_and_comparison_do_not_hide_dependency_failure(self):
        with patch.object(terminal.os.path, 'isfile', return_value=True), \
             patch.object(renderer, 'render_font_preview_image', side_effect=ft.FreeTypeUnavailableError('FreeType unavailable')):
            with self.assertRaises(ft.FreeTypeUnavailableError):
                terminal.render_waterfall('Fixture', filepath='/fixture.ttf')
            with self.assertRaises(ft.FreeTypeUnavailableError):
                terminal.render_comparison_view([dict(filepath='/fixture.ttf', family='Fixture')])

    def test_missing_dependency_preserves_an_existing_index(self):
        with tempfile.TemporaryDirectory(prefix='cefont-no-ft-index-') as tmp:
            fixture = Path(tmp) / 'fixture.ttf'
            fixture.write_bytes(b'fixture')
            font = dict(filepath=str(fixture), findex=0)
            with patch.object(db, 'DATABASE_FILE', str(Path(tmp) / 'index.sqlite')), \
                 patch.object(db, 'ensure_directories'), patch.object(db, 'scan_available_fonts', return_value=[font]), \
                 patch.object(ft, '_ft', None):
                con = db.get_db_connection()
                con.execute("INSERT INTO Fonts(filepath,findex,family) VALUES ('/previous.ttf',0,'Previous')")
                con.commit(); con.close()
                with self.assertRaisesRegex(RuntimeError, 'FreeType'):
                    db.update_database()
                self.assertEqual(db.get_all_families(), ['Previous'])

    def test_wrapped_preview_and_terminal_validation_do_not_bypass_missing_parser(self):
        with patch.object(renderer, '_ft', None):
            with self.assertRaises(ft.FreeTypeUnavailableError):
                renderer.render_font_preview_image('/fixture.ttf', 'A', protocol='blocks', wrap=True)
        with patch.object(ft, '_ft', None):
            with self.assertRaises(ft.FreeTypeUnavailableError):
                terminal_tester.check_font_terminal_suitability(__file__)


class BitmapTests(unittest.TestCase):
    def fixture(self, mode=1, width=3, rows=1, pitch=2, content=b'\xa0\x00', grays=2):
        # Allocate more than the advertised stride, making the old incorrect
        # byte-per-pixel read deterministic without accessing unowned memory.
        buffer = ctypes.create_string_buffer(content, max(64, len(content)))
        slot = renderer.FT_GlyphSlotRec()
        slot.bitmap = renderer.FT_Bitmap(rows, width, pitch, ctypes.addressof(buffer), grays, mode, 0, None)
        slot.advance.x = width << 6
        slot.bitmap_top = rows
        face = ft.FT_FaceRec()
        face.glyph = ctypes.addressof(slot)
        library = Mock()
        def open_face(context, path, index, result):
            ctypes.cast(result, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.addressof(face)
            return 0
        library.FT_New_Face.side_effect = open_face
        library.FT_Load_Char.return_value = 0
        return buffer, slot, face, library

    def test_mono_glyph_decodes_bits_instead_of_reading_pixels_as_bytes(self):
        buffer, slot, face, library = self.fixture()
        with patch.object(renderer, '_ft', library), patch.object(renderer, 'get_ft_context', return_value=Mock()):
            width, height, rgb, _ = renderer.render_glyph_bitmap('/fixture.ttf', 65, padding=0)
        self.assertEqual((width, height), (3, 1))
        self.assertEqual(rgb, bytearray([255, 255, 255, 0, 0, 0, 255, 255, 255]))

    def test_grayscale_and_packed_modes_with_padding(self):
        for mode, content, levels in ((2, b'\x00\x01\x02\x03\xff', 4),
                                      (3, b'\x1b\xff', 4), (4, b'\x05\xaf\xff', 16)):
            buffer, slot, _, _ = self.fixture(mode=mode, width=4, pitch=len(content), content=content, grays=levels)
            self.assertEqual(ft.bitmap_to_grayscale(slot.bitmap), [[0, 85, 170, 255]])

    def test_signed_pitch_preserves_logical_row_order(self):
        buffer, slot, _, _ = self.fixture(mode=2, width=2, rows=2, pitch=-3,
                                           content=b'\x10\x20\x00\x30\x40\x00', grays=256)
        slot.bitmap.buffer = ctypes.addressof(buffer) + 3
        self.assertEqual(ft.bitmap_to_grayscale(slot.bitmap), [[48, 64], [16, 32]])

    def test_invalid_layout_and_unsupported_color_modes_are_rejected_before_read(self):
        for mode, pitch, address in ((1, 0, True), (2, 1, True), (2, 3, False),
                                      (5, 3, True), (6, 3, True), (7, 12, True)):
            buffer, slot, _, _ = self.fixture(mode=mode, pitch=pitch)
            if not address:
                slot.bitmap.buffer = None
            with self.subTest(mode=mode, pitch=pitch), patch.object(ft.ctypes, 'string_at') as read:
                with self.assertRaises(RuntimeError):
                    ft.bitmap_to_grayscale(slot.bitmap)
                read.assert_not_called()

    def test_empty_and_oversized_bitmaps(self):
        bitmap = ft.FT_Bitmap()
        self.assertEqual(ft.bitmap_to_grayscale(bitmap), [])
        buffer, slot, _, _ = self.fixture(width=10000, rows=10000, pitch=1250)
        with patch.object(ft.ctypes, 'string_at') as read:
            with self.assertRaisesRegex(RuntimeError, 'limit'):
                ft.bitmap_to_grayscale(slot.bitmap)
            read.assert_not_called()

    def test_space_bitmap_preserves_requested_background_color(self):
        buffer, slot, face, library = self.fixture(width=0, rows=0, pitch=0)
        slot.bitmap.buffer = None
        with patch.object(renderer, '_ft', library), patch.object(renderer, 'get_ft_context', return_value=Mock()):
            width, height, rgb, _ = renderer.render_glyph_bitmap('/fixture.ttf', 32, bg_color=(1, 2, 3))
        self.assertEqual(rgb, bytearray([1, 2, 3]) * (width * height))

    def test_text_and_ascii_use_the_same_mono_decoder(self):
        buffer, slot, face, library = self.fixture()
        with patch.object(renderer, '_ft', library), patch.object(renderer, 'get_ft_context', return_value=Mock()):
            _, _, rgb = renderer.render_text_bitmap('/fixture.ttf', 'A')
        self.assertEqual(set(rgb), {0, 255})
        with patch.object(ft, '_ft', library), patch.object(ft, 'get_ft_context', return_value=Mock()), \
             patch.object(ft.os.path, 'isfile', return_value=True):
            text = ft.render_glyph_ascii('/fixture.ttf', 0, 'A')
        self.assertTrue(text.endswith('@ @'), text)

    def test_layout_matches_installed_freetype_headers(self):
        compiler = shutil.which('cc')
        include = Path('/usr/include/freetype2')
        if not compiler or not (include / 'ft2build.h').exists():
            self.skipTest('Compiler or FreeType development headers unavailable')
        source = '''#include <stddef.h>
#include <stdio.h>
#include <ft2build.h>
#include FT_FREETYPE_H
int main(void) { printf("%zu %zu %zu %zu %d %d %d %d", offsetof(FT_FaceRec, glyph),
 offsetof(FT_GlyphSlotRec, bitmap), sizeof(FT_Bitmap), offsetof(FT_Bitmap, pixel_mode),
 FT_PIXEL_MODE_MONO, FT_PIXEL_MODE_GRAY, FT_PIXEL_MODE_GRAY2, FT_PIXEL_MODE_GRAY4); }
'''
        with tempfile.TemporaryDirectory(prefix='cefont-abi-') as tmp:
            path = Path(tmp) / 'layout.c'
            path.write_text(source)
            binary = Path(tmp) / 'layout'
            subprocess.run([compiler, '-I' + str(include), str(path), '-o', str(binary)], check=True, capture_output=True)
            actual = list(map(int, subprocess.check_output([str(binary)]).split()))
        self.assertEqual(actual, [ft.FT_FaceRec.glyph.offset, ft.FT_GlyphSlotRec.bitmap.offset,
                                  ctypes.sizeof(ft.FT_Bitmap), ft.FT_Bitmap.pixel_mode.offset, 1, 2, 3, 4])
