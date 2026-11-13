"""Read-only native metadata checks against an independent SFNT decoder."""
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from cefont.core import freetype_backend as ft
from cefont.core import renderer
from cefont.core import db


def tables(raw):
    if raw[:4] not in (b'\x00\x01\x00\x00', b'OTTO', b'true'):
        return {}
    count = int.from_bytes(raw[4:6], 'big')
    result = {}
    for index in range(count):
        entry = 12 + index * 16
        tag, _, offset, size = struct.unpack_from('>4sIII', raw, entry)
        result[tag] = (entry, offset, size)
    return result


class MetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not ft._ft:
            raise unittest.SkipTest('FreeType not available')
        cls.samples = []
        for root in ('/usr/share/fonts', '/usr/local/share/fonts'):
            for path in sorted(Path(root).rglob('*.ttf')):
                try:
                    raw = path.read_bytes()
                except OSError:
                    continue
                record = tables(raw).get(b'OS/2')
                if record and record[2] >= 78:
                    cls.samples.append(path)
                    if len(cls.samples) == 3:
                        return
        if not cls.samples:
            raise unittest.SkipTest('No readable SFNT fixture available')

    def test_metadata_matches_independently_decoded_os2(self):
        for path in self.samples:
            raw = path.read_bytes()
            _, offset, size = tables(raw)[b'OS/2']
            os2 = raw[offset:offset + size]
            expected_flags = int.from_bytes(os2[8:10], 'big')
            expected_panose = list(os2[32:42])
            expected_vendor = os2[58:62].decode('latin1').strip()
            with self.subTest(font=str(path)), patch.object(ft, 'get_vendor', wraps=ft.get_vendor) as vendor:
                info = ft.extract_font_metadata(str(path))
                self.assertIsNotNone(info)
                self.assertEqual(info['fsType'], expected_flags)
                self.assertEqual(info['panose'], expected_panose)
                self.assertEqual(vendor.call_args.args[0], expected_vendor)

    def test_embedding_flags_and_vendor_are_not_native_layout_offsets(self):
        raw = bytearray(self.samples[0].read_bytes())
        _, offset, _ = tables(raw)[b'OS/2']
        raw[offset + 8:offset + 10] = b'\x01\x04'
        raw[offset + 32:offset + 42] = bytes([2, 3, 4, 5, 6, 7, 8, 9, 10, 11])
        raw[offset + 58:offset + 62] = b'GOOG'
        # Remove manufacturer name records, so public vendor output exercises
        # the OS/2 ID fallback instead of an unrelated SFNT name string.
        _, name_offset, _ = tables(raw)[b'name']
        count = int.from_bytes(raw[name_offset + 2:name_offset + 4], 'big')
        for index in range(count):
            name_id = name_offset + 6 + index * 12 + 6
            if raw[name_id:name_id + 2] == b'\x00\x08':
                raw[name_id:name_id + 2] = b'\xff\xff'
        with tempfile.TemporaryDirectory(prefix='cefont-meta-') as tmp:
            fixture = Path(tmp) / 'fixture.ttf'
            fixture.write_bytes(raw)
            info = ft.extract_font_metadata(str(fixture))
            self.assertIsNotNone(info)
            self.assertEqual(info['fsType'], 0x0104)
            self.assertEqual(info['panose'], [2, 3, 4, 5, 6, 7, 8, 9, 10, 11])
            self.assertEqual(info['vendor'], 'Google')

    def test_short_or_unknown_version_os2_is_not_read_as_embedding_rights(self):
        for mode in ('short', 'unsupported'):
            raw = bytearray(self.samples[0].read_bytes())
            entry, offset, _ = tables(raw)[b'OS/2']
            if mode == 'short':
                struct.pack_into('>I', raw, entry + 12, 61)
            else:
                raw[offset:offset + 2] = b'\xff\xfe'
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(prefix='cefont-meta-') as tmp:
                fixture = Path(tmp) / 'fixture.ttf'
                fixture.write_bytes(raw)
                info = ft.extract_font_metadata(str(fixture))
                self.assertIsNotNone(info)
                self.assertIsNone(info['fsType'])
                self.assertEqual(info['panose'], [0] * 10)

    def test_absent_os2_has_unknown_embedding_rights(self):
        raw = bytearray(self.samples[0].read_bytes())
        entry, _, _ = tables(raw)[b'OS/2']
        raw[entry:entry + 4] = b'NONE'
        with tempfile.TemporaryDirectory(prefix='cefont-meta-') as tmp:
            fixture = Path(tmp) / 'absent.ttf'
            fixture.write_bytes(raw)
            info = ft.extract_font_metadata(str(fixture))
        self.assertIsNotNone(info)
        self.assertIsNone(info['fsType'])
        self.assertEqual(info['panose'], [0] * 10)

    def test_rendering_is_unchanged_by_metadata_inspection(self):
        path = str(self.samples[0])
        before = renderer.render_text_bitmap(path, 'Ab 012', pixel_size=16)
        ft.extract_font_metadata(path)
        after = renderer.render_text_bitmap(path, 'Ab 012', pixel_size=16)
        self.assertEqual(before, after)
        width, height, rgb = after
        self.assertEqual(len(rgb), width * height * 3)
        self.assertTrue(any(rgb))
        self.assertTrue(renderer.encode_png(width, height, rgb).startswith(b'\x89PNG\r\n\x1a\n'))

    def test_indexed_metadata_round_trips_without_touching_other_caches(self):
        raw = self.samples[0].read_bytes()
        _, offset, _ = tables(raw)[b'OS/2']
        with tempfile.TemporaryDirectory(prefix='cefont-index-') as tmp:
            fixtures = Path(tmp) / 'fixtures'
            fixtures.mkdir()
            fixture = fixtures / 'fixture.ttf'
            fixture.write_bytes(raw)
            sentinel = Path(tmp) / 'other-cache.sqlite'
            sentinel.write_bytes(b'Existing cache must remain untouched')
            with patch.object(db, 'DATABASE_FILE', str(Path(tmp) / 'isolated.sqlite')), \
                 patch.object(db, 'ensure_directories'):
                self.assertEqual(db.scan_and_index_directory(str(fixtures)), 1)
                info = db.get_detailed_font_info(str(fixture))
            self.assertEqual(info['fsType'], int.from_bytes(raw[offset + 8:offset + 10], 'big'))
            self.assertEqual([info['P%d' % index] for index in range(10)], list(raw[offset + 32:offset + 42]))
            self.assertEqual(sentinel.read_bytes(), b'Existing cache must remain untouched')
