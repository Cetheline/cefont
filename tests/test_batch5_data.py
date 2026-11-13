"""Reference-data regressions using packaged resources and isolated fonts."""
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from cefont.core import data, db, orthography


class ReferenceDataTests(unittest.TestCase):
    def setUp(self):
        for name in ('_ORTHOGRAPHIES', '_VENDORS', '_NOTICES'):
            context = patch.object(data, name, None)
            context.start()
            self.addCleanup(context.stop)

    def test_basic_latin_has_numeric_key_and_full_support(self):
        definition = data.load_orthographies()['Basic Latin']
        self.assertEqual(definition['key'], 0x41)
        self.assertEqual(definition['id'], 'BasicLatin')
        with patch.object(orthography.os.path, 'isfile', return_value=True), \
             patch.object(orthography, 'get_all_codepoints', return_value=set(range(65, 91)) | set(range(97, 123))):
            result = orthography.analyze_font_orthographies('/fixture.ttf', filter_lang='Basic Latin')
        self.assertEqual(result['results'][0]['coverage'], 100)
        self.assertTrue(result['results'][0]['fully_supported'])

    def test_afrikaans_singletons_are_not_invented_ranges(self):
        expected = {0xc0, 0xe0, 0xc1, 0xe1, 0xc4, 0xe4, 0xc8, 0xe8,
                    0xc9, 0xe9, 0xca, 0xea, 0xcb, 0xeb, 0xcd, 0xed,
                    0xce, 0xee, 0xcf, 0xef, 0xd2, 0xf2, 0xd3, 0xf3,
                    0xd4, 0xf4, 0xd6, 0xf6, 0xda, 0xfa, 0xdb, 0xfb,
                    0xdc, 0xfc, 0xdd, 0xfd, 0x149}
        self.assertEqual(data.load_orthographies()['Afrikaans']['codepoints'], expected)

    def test_notices_are_separate_from_vendor_ids(self):
        vendors, notices = data.load_vendors_and_notices()
        self.assertIn(('Adobe', 'adobe'), notices)
        self.assertEqual(data.get_vendor('', 'Copyright Adobe'), 'Adobe')
        self.assertNotIn('Bigelow & Holmes', vendors)
        self.assertEqual(data.get_vendor('GOOG', 'Adobe'), 'Google')

    def test_mixed_ranges_singletons_and_escaped_strings(self):
        fixture = r'''{
            "Fixture", "Native", 0, "A\"{B",
            { "Pangram }", FONT_MANAGER_END_OF_DATA },
            {0x41, FONT_MANAGER_START_RANGE_PAIR, 0x61, 0x63,
             /* ignored 0x999 */ 0x7a, FONT_MANAGER_END_OF_DATA}
        },'''
        with patch.object(data, '_orthography_names', return_value=['Fixture']), \
             patch.object(data, '_read_resource', return_value=fixture):
            definitions = data.load_orthographies()
        self.assertEqual(definitions['Fixture']['codepoints'], {65, 97, 98, 99, 122})
        self.assertEqual(definitions['Fixture']['sample'], 'A"{B')
        self.assertEqual(definitions['Fixture']['pangrams'], ['Pangram }'])
        for key, supported in ((0, True), (None, True), (66, False), (65, True)):
            definitions['Fixture']['key'] = key
            with self.subTest(key=key), patch.object(orthography.os.path, 'isfile', return_value=True), \
                 patch.object(orthography, 'load_orthographies', return_value=definitions), \
                 patch.object(orthography, 'get_all_codepoints', return_value={65, 97, 98, 99, 122}):
                result = orthography.analyze_font_orthographies('/fixture.ttf')['results'][0]
                self.assertEqual(result['fully_supported'], supported)
                self.assertEqual(result['coverage'], 100)
